"""Private desktop workspaces, using the product's initialization boundaries.

The launcher must stop its broker before prepare/select/create. Upgrades never
modify the selected generation: verified copies are migrated, then one metadata
file atomically selects them. Interrupted and failed copies remain recoverable.
No production health INSERT/UPDATE/DDL belongs in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class WorkspaceError(RuntimeError):
    """A safe, user-facing failure; never include record contents or child logs."""


def default_data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Open Health Atlas"
    # Portable path contract only; macOS is the first validated distribution.
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Open Health Atlas"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "open-health-atlas"


def validate_timezone(value: str) -> str:
    try:
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise WorkspaceError("Choose a valid time zone, such as Europe/Paris or America/New_York.") from None
    return value


def _private_directory(path: Path) -> None:
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for created in reversed(missing):
        created.chmod(0o700)
    path.chmod(0o700)


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError):
        raise WorkspaceError("Workspace settings could not be read. Your records have not been removed.") from None


def _integrity(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError
    except (sqlite3.Error, ValueError):
        raise WorkspaceError("This database did not pass its safety check. Choose a healthy Open Health Atlas backup.") from None


def _snapshot(source: Path, destination: Path, *, cancelled=None) -> None:
    """Include committed WAL records while leaving the source untouched."""
    if destination.exists():
        raise WorkspaceError("A recovery copy already exists. It has not been overwritten.")
    deadline = time.monotonic() + 120
    def progress(_status, _remaining, _total):
        if cancelled is not None and cancelled():
            raise WorkspaceError("Opening was cancelled. Your original data is safe.")
        if time.monotonic() > deadline:
            raise WorkspaceError("Copying this database took too long. Close the app using it, then try again.")
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=0.1)) as original:
            original.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(destination)) as copy:
                original.backup(copy, pages=256, progress=progress, sleep=0.05)
        destination.chmod(0o600)
        _integrity(destination)
    except sqlite3.Error:
        raise WorkspaceError("The selected database could not be copied. Choose an Open Health Atlas database you can read.") from None


@dataclass(frozen=True)
class Workspace:
    id: str
    kind: str
    directory: Path
    generation: str
    timezone: str
    anchor_date: str | None = None

    @property
    def health_db(self) -> Path:
        return self.directory / "generations" / self.generation / "health.db"

    @property
    def panel_db(self) -> Path:
        return self.directory / "generations" / self.generation / "panel.db"

    @property
    def vault_dir(self) -> Path:
        return self.directory / "vault"

    def environment(self) -> dict[str, str]:
        return {
            "HERMES_DATA_DIR": str(self.health_db.parent),
            "HEALTH_DB": str(self.health_db), "PANEL_DB": str(self.panel_db),
            "HEALTH_VAULT": str(self.vault_dir), "VAULT_DIR": str(self.vault_dir),
            "HERMES_TIMEZONE": self.timezone,
        }

    def public_status(self) -> dict:
        return {"id": self.id, "kind": self.kind, "timezone": self.timezone,
                "fictional": self.kind == "demo", "anchor_date": self.anchor_date,
                "label": {"demo": "Fictional example", "personal": "My health data",
                          "import": "Imported health data"}[self.kind]}


ScriptRunner = Callable[[str, list[str], dict[str, str]], dict]


class WorkspaceManager:
    def __init__(self, data_root: Path, source_root: Path, code_version: str,
                 run_script: ScriptRunner | None = None, cancelled=None):
        if not re.fullmatch(r"[0-9a-f]{40}", code_version):
            raise ValueError("code_version must identify the packaged source commit")
        self.data_root = Path(data_root).expanduser().resolve()
        self.source_root = Path(source_root).resolve()
        self.code_version = code_version
        self.run_script = run_script or self._run_script
        self.cancelled = cancelled
        self._lock = threading.RLock()
        _private_directory(self.data_root)
        _private_directory(self.data_root / "workspaces")

    def _snapshot(self, source: Path, destination: Path) -> None:
        _snapshot(source, destination, cancelled=self.cancelled)

    def _run_script(self, script: str, args: list[str], env: dict[str, str]) -> dict:
        # Do not inherit Hermes/provider configuration, PYTHONPATH or secrets.
        clean_env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()),
                     "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1",
                     "HERMES_CODE_VERSION": self.code_version, **env}
        try:
            result = subprocess.run([sys.executable, "-s", "-B", str(self.source_root / script), *args],
                                    cwd=self.source_root, env=clean_env, capture_output=True,
                                    text=True, timeout=240, check=True)
            value = json.loads(result.stdout.strip().splitlines()[-1])
            if not isinstance(value, dict) or value.get("ok") is False:
                raise ValueError
            return value
        except (subprocess.SubprocessError, OSError, ValueError, IndexError):
            raise WorkspaceError("Setup could not finish. Your existing data is safe. Try again or open Help.") from None

    def _health(self, workspace: Workspace, *args: str) -> dict:
        return self.run_script("toolkit/health.py", list(args),
                               {**workspace.environment(), "HERMES_CODE_VERSION": self.code_version})

    def _load(self, workspace_id: str) -> Workspace:
        if not isinstance(workspace_id, str) or not re.fullmatch(r"[a-f0-9]{32}", workspace_id):
            raise WorkspaceError("Choose a workspace from the list.")
        directory = self.data_root / "workspaces" / workspace_id
        metadata = _read_json(directory / "workspace.json")
        generation = metadata.get("generation", "")
        if (not isinstance(metadata.get("kind"), str)
                or metadata["kind"] not in {"demo", "personal", "import"}
                or not isinstance(generation, str)
                or not re.fullmatch(r"[a-f0-9]{32}", generation)):
            raise WorkspaceError("Workspace settings are not supported. Your records have not been removed.")
        return Workspace(workspace_id, metadata["kind"], directory, generation,
                         validate_timezone(metadata.get("timezone")), metadata.get("anchor_date"))

    def current(self) -> Workspace | None:
        settings = self.data_root / "settings.json"
        if not settings.exists():
            return None
        return self._load(_read_json(settings).get("selected"))

    def list_workspaces(self) -> list[dict]:
        result = []
        for path in sorted((self.data_root / "workspaces").iterdir()):
            if re.fullmatch(r"[a-f0-9]{32}", path.name) and (path / "workspace.json").is_file():
                try:
                    result.append(self._load(path.name).public_status())
                except WorkspaceError:
                    result.append({"id": path.name, "kind": "unavailable", "timezone": "",
                                   "fictional": False, "unavailable": True,
                                   "label": "Unavailable workspace — data kept"})
        return result

    def public_status(self) -> dict:
        recovery_available = False
        try:
            workspace = self.current()
        except WorkspaceError:
            workspace = None
            recovery_available = True
        return {"current": workspace.public_status() if workspace else None,
                "workspaces": self.list_workspaces(), "recovery_available": recovery_available}

    def _validate_complete_schema(self, workspace: Workspace) -> None:
        def shape(connection, name):
            columns = {row[1]: (row[2].upper(), row[3], row[4], row[5])
                       for row in connection.execute(f'PRAGMA table_info("{name}")')}
            primary = tuple(key for key, value in sorted(columns.items(), key=lambda item: item[1][3]) if value[3])
            unique = set()
            for index in connection.execute(f'PRAGMA index_list("{name}")'):
                if index[2]:
                    # Index names come from the selected DB; quote them safely.
                    quoted = index[1].replace('"', '""')
                    unique.add((tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{quoted}")')), bool(index[4])))
            return columns, primary, unique

        # In-memory reference only: no production schema is created or patched.
        with closing(sqlite3.connect(":memory:")) as reference:
            reference.executescript((self.source_root / "toolkit/SCHEMA.sql").read_text())
            required = {name: shape(reference, name)
                        for (name,) in reference.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        if not name.startswith("sqlite_")}
        try:
            with closing(sqlite3.connect(workspace.health_db.as_uri() + "?mode=ro", uri=True)) as connection:
                connection.execute("PRAGMA query_only=ON")
                for name, (columns, primary, unique) in required.items():
                    present, actual_primary, actual_unique = shape(connection, name)
                    if (any(present.get(key) != value for key, value in columns.items())
                            or primary != actual_primary or not unique <= actual_unique):
                        raise ValueError
        except (sqlite3.Error, ValueError):
            raise WorkspaceError("Choose a complete Open Health Atlas database. Other SQLite files, PDFs and spreadsheets cannot be opened here.") from None
        _integrity(workspace.health_db)
        _integrity(workspace.panel_db)

    def _migrate(self, workspace: Workspace) -> None:
        status = self._health(workspace, "schema-status")
        target = str(status["target_version"])
        plan = self._health(workspace, "schema-plan", "--to", target)
        if plan["from_version"] != plan["to_version"]:
            self._health(workspace, "migrate", "--to", target,
                         "--expected-from", str(plan["from_version"]))
        from app.panel_db import init_db
        init_db(SimpleNamespace(config={"PANEL_DB": str(workspace.panel_db)}))
        workspace.panel_db.chmod(0o600)
        self._validate_complete_schema(workspace)

    def create(self, kind: str, timezone: str, source_database: str | Path | None = None) -> Workspace:
        with self._lock:
            zone = validate_timezone(timezone)
            if kind not in {"demo", "personal", "import"}:
                raise WorkspaceError("Choose an example, an empty workspace, or existing Open Health Atlas data.")
            source = None
            if kind == "import":
                if not source_database or not Path(source_database).expanduser().is_file():
                    raise WorkspaceError("Choose an existing Open Health Atlas database first.")
                source = Path(source_database).expanduser().resolve()
            identifier, generation = uuid4().hex, uuid4().hex
            directory = self.data_root / "workspaces" / identifier
            target = directory / "generations" / generation
            _private_directory(target)
            _private_directory(directory / "vault")
            anchor = datetime.now(ZoneInfo(zone)).date().isoformat() if kind == "demo" else None
            workspace = Workspace(identifier, kind, directory, generation, zone, anchor)
            # Missing workspace.json marks incomplete initialization; it never
            # appears in selection and never displaces the user's current data.
            self.run_script("scripts/init_hermes.py", ["--data-dir", str(target)], workspace.environment())
            if kind == "demo":
                demo = target / "fictional.db"
                self.run_script("scripts/make_demo_db.py", ["--output", str(demo), "--anchor-date", anchor], workspace.environment())
                os.replace(demo, workspace.health_db)
            elif source is not None:
                imported = target / "imported.db"
                self._snapshot(source, imported)
                os.replace(imported, workspace.health_db)
                # Preserve a verified, untouched import before any migrations.
                backup = directory / "backups" / uuid4().hex
                _private_directory(backup)
                self._snapshot(workspace.health_db, backup / "health.db")
                self._snapshot(workspace.panel_db, backup / "panel.db")
            workspace.health_db.chmod(0o600)
            self._migrate(workspace)
            _write_json(directory / "workspace.json", {
                "version": 1, "kind": kind, "timezone": zone, "generation": generation,
                "anchor_date": anchor, "prepared_code_version": self.code_version,
            })
            _write_json(self.data_root / "settings.json", {"version": 1, "selected": identifier})
            return workspace

    def prepare(self, workspace: Workspace) -> Workspace:
        """Call before any app/bridge opens this workspace on a new launch."""
        with self._lock:
            workspace = self._load(workspace.id)
            metadata_path = workspace.directory / "workspace.json"
            metadata = _read_json(metadata_path)
            journal_path = workspace.directory / "upgrade.json"
            if journal_path.exists():
                # The selected metadata is the commit point. If interrupted
                # before it, the original is still selected; after it, the new
                # version is selected. Neither case overwrites any records.
                _integrity(workspace.health_db)
                _integrity(workspace.panel_db)
                recovered = workspace.directory / ("upgrade-recovered-" + uuid4().hex + ".json")
                os.replace(journal_path, recovered)
            if metadata.get("prepared_code_version") == self.code_version:
                status = self._health(workspace, "schema-status")
                plan = self._health(workspace, "schema-plan", "--to", str(status["target_version"]))
                if plan["from_version"] != plan["to_version"]:
                    raise WorkspaceError("This workspace needs an update. Its records have not been changed.")
                self._validate_complete_schema(workspace)
                return workspace
            generation = uuid4().hex
            backup = workspace.directory / "backups" / generation
            destination = workspace.directory / "generations" / generation
            _private_directory(backup)
            _private_directory(destination)
            for name in ("health.db", "panel.db"):
                self._snapshot(workspace.health_db.parent / name, backup / name)
                self._snapshot(backup / name, destination / name)
            candidate = Workspace(workspace.id, workspace.kind, workspace.directory,
                                  generation, workspace.timezone, workspace.anchor_date)
            _write_json(journal_path, {"version": 1, "from_generation": workspace.generation,
                                      "to_generation": generation, "code_version": self.code_version})
            try:
                self._migrate(candidate)
                _write_json(metadata_path, {**metadata, "generation": generation,
                                           "prepared_code_version": self.code_version})
            except Exception:
                raise WorkspaceError("The update could not finish. Your original workspace and verified backups are safe. Reopen the previous app version or try this update again.") from None
            journal_path.unlink(missing_ok=True)
            return candidate

    def select(self, workspace_id: str) -> Workspace:
        with self._lock:
            workspace = self.prepare(self._load(workspace_id))
            _write_json(self.data_root / "settings.json", {"version": 1, "selected": workspace.id})
            return workspace
