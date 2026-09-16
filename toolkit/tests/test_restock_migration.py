"""Migration 005 contracts for the persistent freezer-restock state."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_VERSION = "5" * 40
sys.path.insert(0, str(ROOT))

import hermes_insights.migrations as migrations  # noqa: E402
from hermes_insights.migrations import (  # noqa: E402
    AUTONOMOUS_SCHEMA_VERSION,
    MIGRATION_005_SQL,
    MIGRATION_BY_VERSION,
    HYPOTHESIS_ANNOTATIONS_V6_DDL,
    PHASE5_TABLE_DDL,
    RESTOCK_STATE_DDL,
    SchemaError,
    migrate,
    schema_plan,
    schema_status,
    SYNTHESIS_RUNS_V6_DDL,
)


def _schema_form(ddl: str) -> str:
    return ddl.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1)


def _legacy_phase5_schema(schema: str) -> str:
    replacements = (
        (
            r"CREATE TABLE synthesis_runs \(.*?\n\);\n+CREATE TABLE synthesis_analysis_runs",
            _schema_form(PHASE5_TABLE_DDL["synthesis_runs"])
            + "\n\nCREATE TABLE synthesis_analysis_runs",
        ),
        (
            r"CREATE TABLE hypothesis_annotations \(.*?\n\);\n+CREATE INDEX hypothesis_annotations_latest_idx",
            _schema_form(PHASE5_TABLE_DDL["hypothesis_annotations"])
            + "\n\nCREATE INDEX hypothesis_annotations_latest_idx",
        ),
    )
    for pattern, replacement in replacements:
        schema, count = re.subn(pattern, replacement, schema, count=1, flags=re.DOTALL)
        assert count == 1
    return schema


CURRENT_SCHEMA = (ROOT / "SCHEMA.sql").read_text(encoding="utf-8")
SCHEMA = _legacy_phase5_schema(CURRENT_SCHEMA)


FROZEN_MIGRATION_005_CHECKSUM = (
    "51033f434a50a6169d619bf00c6702dee158accd21cf94310a4a034a68622545"
)


def _connection(path: Path) -> sqlite3.Connection:
    result = sqlite3.connect(path)
    result.row_factory = sqlite3.Row
    result.execute("PRAGMA foreign_keys=ON")
    return result


def _v4_without_restock(path: Path) -> Path:
    connection = _connection(path)
    connection.executescript(SCHEMA)
    connection.execute("DROP TABLE recipe_restock_state")
    connection.commit()
    connection.close()
    result = migrate(str(path), 4, 0, CODE_VERSION)
    assert result["to_version"] == 4
    return path


def test_migration_005_name_inventory_and_checksum_are_frozen():
    migration = MIGRATION_BY_VERSION[5]
    normalized = MIGRATION_005_SQL.replace("\r\n", "\n").replace("\r", "\n")
    assert AUTONOMOUS_SCHEMA_VERSION >= 5
    assert migration.name == "005_recipe_restock_state"
    assert migration.sql == RESTOCK_STATE_DDL + "\n"
    assert migration.checksum == FROZEN_MIGRATION_005_CHECKSUM
    assert hashlib.sha256(normalized.encode()).hexdigest() == migration.checksum


def test_v4_upgrade_adds_empty_restock_state_without_changing_records(tmp_path):
    database = _v4_without_restock(tmp_path / "upgrade.db")
    connection = _connection(database)
    connection.execute(
        "INSERT INTO recipes(recipe_id,name) VALUES('fictional-stew','Fictional Stew')"
    )
    connection.commit()
    connection.close()

    plan = schema_plan(str(database), 5)
    assert [step["version"] for step in plan["steps"]] == [5]
    result = migrate(str(database), 5, 4, CODE_VERSION)
    assert result["applied"] == [{
        "version": 5,
        "name": "005_recipe_restock_state",
        "checksum_sha256": FROZEN_MIGRATION_005_CHECKSUM,
    }]

    connection = _connection(database)
    assert connection.execute(
        "SELECT name FROM recipes WHERE recipe_id='fictional-stew'"
    ).fetchone()[0] == "Fictional Stew"
    assert connection.execute(
        "SELECT COUNT(*) FROM recipe_restock_state"
    ).fetchone()[0] == 0
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    assert schema_status(str(database))["status"] == "pending"


def test_preexisting_current_table_is_validated_and_recorded(tmp_path):
    database = tmp_path / "fresh.db"
    connection = _connection(database)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()
    result = migrate(str(database), 5, 0, CODE_VERSION)
    assert [item["version"] for item in result["applied"]] == [1, 2, 3, 4, 5]
    assert schema_status(str(database))["status"] == "pending"


def test_conflicting_restock_table_fails_before_migration_write(tmp_path):
    database = _v4_without_restock(tmp_path / "conflict.db")
    connection = _connection(database)
    connection.execute(
        "CREATE TABLE recipe_restock_state(recipe_id TEXT PRIMARY KEY, wrong TEXT)"
    )
    connection.commit()
    connection.close()
    before = database.read_bytes()

    with pytest.raises(SchemaError) as planned:
        schema_plan(str(database), 5)
    assert planned.value.code == "incompatible_schema"
    with pytest.raises(SchemaError) as applied:
        migrate(str(database), 5, 4, CODE_VERSION)
    assert applied.value.code == "incompatible_schema"
    assert database.read_bytes() == before
    connection = _connection(database)
    assert connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0] == 4
    connection.close()


def test_mid_migration_failure_rolls_back_table_and_ledger(tmp_path, monkeypatch):
    database = _v4_without_restock(tmp_path / "rollback.db")

    def fail_after_table(connection):
        connection.execute(RESTOCK_STATE_DDL)
        raise RuntimeError("injected restock migration failure")

    monkeypatch.setattr(migrations, "_apply_005", fail_after_table)
    with pytest.raises(RuntimeError, match="injected restock migration failure"):
        migrate(str(database), 5, 4, CODE_VERSION)

    connection = _connection(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='recipe_restock_state'"
    ).fetchone() is None
    assert connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0] == 4
    connection.close()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO recipe_restock_state(recipe_id,action) VALUES('x','unknown')",
        "INSERT INTO recipe_restock_state(recipe_id,action,threshold) "
        "VALUES('x','later',-1)",
        "INSERT INTO recipe_restock_state(recipe_id,action,threshold) "
        "VALUES('x','later',1000001)",
    ],
)
def test_restock_state_constraints_reject_invalid_rows(tmp_path, statement):
    database = tmp_path / "constraints.db"
    connection = _connection(database)
    connection.executescript(SCHEMA)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(statement)
    connection.close()
