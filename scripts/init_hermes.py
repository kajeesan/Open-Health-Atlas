#!/usr/bin/env python3
"""Initialize empty Hermes health and panel databases outside the checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "toolkit"))

from app.panel_db import init_db as init_panel_db
from hermes_insights.migrations import AUTONOMOUS_SCHEMA_VERSION, migrate


def seed_public_submuscle_map(health_db: Path) -> dict:
    """Install bundled public anatomy configuration into a fresh database."""
    command = [
        sys.executable,
        str(ROOT / "toolkit" / "health.py"),
        "import-submuscle-map",
        str(ROOT / "docs" / "authored-submuscle-map.md"),
        "--seed",
        "--seed-all",
    ]
    completed = subprocess.run(
        command,
        env={**os.environ, "HEALTH_DB": str(health_db)},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def parser() -> argparse.ArgumentParser:
    default_dir = Path(
        os.environ.get(
            "HERMES_DATA_DIR",
            Path.home() / ".local" / "share" / "hermes",
        )
    )
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--data-dir", type=Path, default=default_dir)
    return result


def main() -> int:
    args = parser().parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    health_db = data_dir / "health.db"
    panel_db = data_dir / "panel.db"
    vault_dir = data_dir / "vault"

    existing = [str(path) for path in (health_db, panel_db) if path.exists()]
    if existing:
        parser().error(
            "refusing to overwrite existing database(s): " + ", ".join(existing)
        )

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    vault_dir.mkdir(exist_ok=True, mode=0o700)
    os.chmod(data_dir, 0o700)
    os.chmod(vault_dir, 0o700)

    schema = (ROOT / "toolkit" / "SCHEMA.sql").read_text(encoding="utf-8")
    try:
        connection = sqlite3.connect(health_db)
        try:
            connection.executescript(schema)
            connection.commit()
        finally:
            connection.close()
        code_version = hashlib.sha1(b"hermes-open-source-init-v1").hexdigest()
        migrate(str(health_db), AUTONOMOUS_SCHEMA_VERSION, 0, code_version)
        map_result = seed_public_submuscle_map(health_db)
        init_panel_db(SimpleNamespace(config={"PANEL_DB": str(panel_db)}))
        os.chmod(health_db, 0o600)
        os.chmod(panel_db, 0o600)
    except Exception:
        # Only files created by this invocation are eligible for cleanup.
        health_db.unlink(missing_ok=True)
        panel_db.unlink(missing_ok=True)
        raise

    print(json.dumps({
        "ok": True,
        "data_dir": str(data_dir),
        "health_db": str(health_db),
        "panel_db": str(panel_db),
        "vault_dir": str(vault_dir),
        "schema_version": AUTONOMOUS_SCHEMA_VERSION,
        "empty": True,
        "public_submuscle_rows": map_result["seeded_rows"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
