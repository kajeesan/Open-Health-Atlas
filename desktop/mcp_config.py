"""Local-client connection configuration for a selected desktop workspace.

Configuration contains local paths, but no credentials or record contents. It
is returned to the user locally; it must never be included in diagnostics.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConnectionConfigurationError(ValueError):
    """A user-actionable connection problem, without private paths."""


def bundled_executable(app_bundle: Path | str | None = None) -> Path:
    """Locate the stable installed entry point; never fall back to developer tools."""
    if app_bundle is None:
        app_bundle = next(
            (parent for parent in Path(__file__).resolve().parents
             if parent.suffix == ".app" and (parent / "Contents").is_dir()),
            None,
        )
    if app_bundle is None:
        raise ConnectionConfigurationError("Install the desktop app before connecting a local AI client.")
    bundle = Path(app_bundle)
    if not bundle.is_absolute():
        raise ConnectionConfigurationError("The installed app location must be an absolute path.")
    executable = bundle / "Contents" / "MacOS" / "openhealthatlas-mcp"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ConnectionConfigurationError("The local AI connection is missing. Reinstall Open Health Atlas.")
    return executable


def workspace_database(workspace: Path | str) -> Path:
    """Resolve a chosen workspace's current generation without migrating or writing."""
    directory = Path(workspace)
    try:
        if not directory.is_absolute():
            raise ValueError
        metadata = json.loads((directory / "workspace.json").read_text(encoding="utf-8"))
        generation = metadata.get("generation", "")
        if (metadata.get("version") != 1 or metadata.get("kind") not in {"demo", "personal", "import"}
                or not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation)):
            raise ValueError
        database = directory / "generations" / generation / "health.db"
        if not database.is_file() or not database.resolve().is_relative_to(directory.resolve()):
            raise ValueError
        return database.resolve()
    except (OSError, ValueError, AttributeError, TypeError):
        raise ConnectionConfigurationError("Open the selected workspace in the app, then reconnect your AI client.") from None


def client_configuration(executable: Path | str, database: Path | str, timezone: str,
                         *, workspace: Path | str | None = None) -> dict:
    """Build the common mcpServers JSON format for a local stdio-capable client.

    Callers supply the database selected by desktop setup, never a request's
    arbitrary path. Client compatibility and provider settings remain the
    client's responsibility. This function neither connects nor changes files.
    """
    command = Path(executable)
    selected = Path(database)
    if not command.is_absolute() or not command.is_file() or not os.access(command, os.X_OK):
        raise ConnectionConfigurationError("The installed local AI connection is unavailable.")
    if not selected.is_absolute() or not selected.is_file():
        raise ConnectionConfigurationError("Choose an existing Open Health Atlas workspace first.")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ConnectionConfigurationError("Choose a valid timezone in workspace settings.") from None
    selection = ["--database", str(selected.resolve())]
    if workspace is not None:
        if workspace_database(workspace) != selected.resolve():
            raise ConnectionConfigurationError("The workspace changed. Reopen its connection settings.")
        selection = ["--workspace", str(Path(workspace).resolve())]
    return {"mcpServers": {"openhealthatlas": {
        "command": str(command),
        "args": [*selection, "--timezone", timezone],
    }}}


def configuration_json(executable: Path | str, database: Path | str, timezone: str,
                       *, workspace: Path | str | None = None) -> str:
    return json.dumps(client_configuration(executable, database, timezone, workspace=workspace),
                      indent=2, ensure_ascii=False) + "\n"
