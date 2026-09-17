"""Distinct desktop data boundaries, tested only with isolated fictional data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from desktop import workspaces
from desktop.workspaces import WorkspaceError, WorkspaceManager

ROOT = Path(__file__).resolve().parents[1]


def manager(path, version="a" * 40):
    return WorkspaceManager(path, ROOT, version)


def read_rows(path, table):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return connection.execute(f'SELECT * FROM "{table}"').fetchall()


def record_snapshot(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
                for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if name != "schema_migrations"}


def test_fictional_personal_switch_restart_and_timezone_are_separate(tmp_path):
    m = manager(tmp_path)
    personal = m.create("personal", "Pacific/Auckland")
    m._health(personal, "water-add", "250", "--date", "2026-06-30")
    original_rows = record_snapshot(personal.health_db)
    demo = m.create("demo", "UTC")
    assert demo.health_db != personal.health_db
    assert demo.panel_db != personal.panel_db
    assert len(read_rows(demo.health_db, "daily_metrics")) > 100
    assert not read_rows(personal.health_db, "daily_metrics")
    assert demo.public_status()["fictional"] is True
    selected = m.select(personal.id)
    restarted = manager(tmp_path).current()
    assert restarted == selected == personal
    assert restarted.timezone == "Pacific/Auckland"
    assert record_snapshot(personal.health_db) == original_rows
    assert (personal.directory.stat().st_mode & 0o077) == 0
    assert (personal.health_db.stat().st_mode & 0o077) == 0


def test_import_snapshots_committed_wal_without_touching_source(tmp_path):
    source_manager = manager(tmp_path / "source")
    source = source_manager.create("personal", "UTC")
    # WAL mode is a fictional test fixture condition. Record insertion still
    # uses the same validated toolkit boundary used by supported callers.
    keeper = sqlite3.connect(source.health_db)
    keeper.execute("PRAGMA journal_mode=WAL")
    keeper.execute("BEGIN")
    keeper.execute("SELECT COUNT(*) FROM intake").fetchone()
    source_manager._health(source, "water-add", "500", "--date", "2026-06-30")
    try:
        assert Path(str(source.health_db) + "-wal").stat().st_size > 0
        before = record_snapshot(source.health_db)
        imported = manager(tmp_path / "destination").create("import", "UTC", source.health_db)
        assert imported.health_db != source.health_db
        assert record_snapshot(imported.health_db) == before
        assert record_snapshot(source.health_db) == before
        assert not read_rows(imported.panel_db, "sessions")
        backups = list((imported.directory / "backups").glob("*/health.db"))
        assert len(backups) == 1
        assert record_snapshot(backups[0]) == before
    finally:
        keeper.close()


def test_unrelated_and_incomplete_databases_rejected_without_selection_or_source_change(tmp_path):
    m = manager(tmp_path / "desktop")
    original = m.create("personal", "UTC")
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    digest = hashlib.sha256(other.read_bytes()).hexdigest()
    with pytest.raises(WorkspaceError):
        m.create("import", "UTC", other)
    assert hashlib.sha256(other.read_bytes()).hexdigest() == digest
    assert m.current() == original
    incomplete = manager(tmp_path / "fixture").create("personal", "UTC")
    # Corruption fixture only; the implementation must never repair this.
    with sqlite3.connect(incomplete.health_db) as connection:
        connection.execute("DROP TABLE vitals")
    digest = hashlib.sha256(incomplete.health_db.read_bytes()).hexdigest()
    with pytest.raises(WorkspaceError, match="complete Open Health Atlas"):
        m.create("import", "UTC", incomplete.health_db)
    assert hashlib.sha256(incomplete.health_db.read_bytes()).hexdigest() == digest
    assert len(m.list_workspaces()) == 1


def test_supported_version_six_import_migrates_copy_and_preserves_all_records(tmp_path):
    m = manager(tmp_path / "desktop")
    source = tmp_path / "fictional-v6.db"
    # A complete older ledger fixture, using the canonical schema and migration
    # boundary. Only fixture construction executes schema text directly.
    with sqlite3.connect(source) as connection:
        connection.executescript((ROOT / "toolkit/SCHEMA.sql").read_text())
    env = {"HEALTH_DB": str(source), "HERMES_TIMEZONE": "UTC"}
    m.run_script("toolkit/health.py", ["migrate", "--to", "6", "--expected-from", "0"], env)
    m.run_script("toolkit/health.py", ["water-add", "250", "--date", "2026-06-30"], env)
    before = record_snapshot(source)
    imported = m.create("import", "UTC", source)
    assert record_snapshot(imported.health_db) == before
    assert record_snapshot(source) == before
    assert max(row[0] for row in read_rows(source, "schema_migrations")) == 6
    assert max(row[0] for row in read_rows(imported.health_db, "schema_migrations")) == 7


def test_interrupted_initialization_never_selects_partial_workspace(tmp_path, monkeypatch):
    m = manager(tmp_path)
    runner = m.run_script
    def interrupted(script, args, env):
        runner(script, args, env)
        raise KeyboardInterrupt
    monkeypatch.setattr(m, "run_script", interrupted)
    with pytest.raises(KeyboardInterrupt):
        m.create("personal", "UTC")
    assert m.current() is None
    assert m.list_workspaces() == []
    retried = manager(tmp_path).create("personal", "UTC")
    assert retried.health_db.is_file()
    assert len(m.list_workspaces()) == 1


def test_failed_upgrade_preserves_both_originals_and_recovers_on_retry(tmp_path, monkeypatch):
    m = manager(tmp_path)
    original = m.create("personal", "Europe/Paris")
    m._health(original, "water-add", "500", "--date", "2026-06-30")
    before = record_snapshot(original.health_db)
    panel_before = record_snapshot(original.panel_db)
    upgrade = manager(tmp_path, "b" * 40)
    def interrupted(candidate):
        upgrade._health(candidate, "water-add", "250", "--date", "2026-06-30")
        raise RuntimeError("injected failure after partial changes")
    monkeypatch.setattr(upgrade, "_migrate", interrupted)
    with pytest.raises(WorkspaceError, match="original workspace"):
        upgrade.prepare(original)
    assert upgrade.current() == original
    assert record_snapshot(original.health_db) == before
    assert record_snapshot(original.panel_db) == panel_before
    journal = original.directory / "upgrade.json"
    failed_generation = json.loads(journal.read_text())["to_generation"]
    for filename in ("health.db", "panel.db"):
        workspaces._integrity(original.directory / "backups" / failed_generation / filename)
    assert record_snapshot(original.directory / "backups" / failed_generation / "health.db") == before
    retried = manager(tmp_path, "b" * 40).prepare(original)
    assert retried.generation != original.generation
    assert record_snapshot(retried.health_db) == before
    assert record_snapshot(retried.panel_db) == panel_before
    assert original.health_db.is_file()
    assert not journal.exists()
    assert list(original.directory.glob("upgrade-recovered-*.json"))


def test_crash_after_upgrade_commit_preserves_new_generation(tmp_path, monkeypatch):
    m = manager(tmp_path)
    original = m.create("personal", "UTC")
    upgrade = manager(tmp_path, "b" * 40)
    unlink = Path.unlink
    def crash_before_journal_cleanup(path, *args, **kwargs):
        if path.name == "upgrade.json":
            raise KeyboardInterrupt
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", crash_before_journal_cleanup)
    with pytest.raises(KeyboardInterrupt):
        upgrade.prepare(original)
    committed = upgrade.current()
    assert committed.generation != original.generation
    upgrade._health(committed, "water-add", "250", "--date", "2026-06-30")
    monkeypatch.setattr(Path, "unlink", unlink)
    reopened = manager(tmp_path, "b" * 40).prepare(committed)
    assert reopened == committed
    assert read_rows(reopened.health_db, "intake")[0][1] == 250
    assert not read_rows(original.health_db, "intake")


def test_invalid_timezone_and_selection_fail_before_creating_data(tmp_path):
    m = manager(tmp_path)
    with pytest.raises(WorkspaceError, match="time zone"):
        m.create("personal", "../private")
    with pytest.raises(WorkspaceError, match="workspace"):
        m.select("../../private")
    assert list((tmp_path / "workspaces").iterdir()) == []


def test_same_version_reopen_checks_migration_ledger(tmp_path):
    m = manager(tmp_path)
    workspace = m.create("personal", "UTC")
    # Deliberately corrupt fictional metadata to ensure a matching app version
    # is never treated as proof that a database is compatible.
    with sqlite3.connect(workspace.health_db) as connection:
        connection.execute("UPDATE schema_migrations SET checksum_sha256='invalid' WHERE version=1")
    before = workspace.health_db.read_bytes()
    with pytest.raises(WorkspaceError):
        m.prepare(workspace)
    assert workspace.health_db.read_bytes() == before


def test_same_columns_with_wrong_primary_key_or_type_are_rejected(tmp_path):
    m = manager(tmp_path / "desktop")
    source = manager(tmp_path / "fictional-source").create("personal", "UTC")
    with sqlite3.connect(source.health_db) as connection:
        connection.execute("DROP TABLE intake")
        connection.execute("CREATE TABLE intake (date INTEGER, water_ml REAL, notes TEXT, source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')))")
    before = source.health_db.read_bytes()
    with pytest.raises(WorkspaceError, match="complete Open Health Atlas"):
        m.create("import", "UTC", source.health_db)
    assert source.health_db.read_bytes() == before
    assert m.current() is None


def test_damaged_unselected_workspace_does_not_hide_healthy_workspaces(tmp_path):
    m = manager(tmp_path)
    damaged = m.create("personal", "UTC")
    healthy = m.create("personal", "UTC")
    (damaged.directory / "workspace.json").write_text('{"kind":[],"generation":3}')
    listed = m.list_workspaces()
    assert next(item for item in listed if item["id"] == damaged.id)["unavailable"] is True
    assert next(item for item in listed if item["id"] == healthy.id)["kind"] == "personal"
    assert m.current() == healthy
    # The recovery surface can still list healthy choices if the selected
    # settings are corrupted; it never silently changes the selected data.
    settings = tmp_path / "settings.json"
    settings.write_text('{"selected":"invalid"}')
    assert m.public_status()["recovery_available"] is True
    assert m.public_status()["current"] is None
    assert settings.read_text() == '{"selected":"invalid"}'


def test_snapshot_timeout_leaves_original_unchanged(tmp_path, monkeypatch):
    m = manager(tmp_path / "desktop")
    workspace = m.create("personal", "UTC")
    before = workspace.health_db.read_bytes()
    clock = iter([0])
    monkeypatch.setattr(workspaces.time, "monotonic", lambda: next(clock, 121))
    with pytest.raises(WorkspaceError, match="took too long"):
        workspaces._snapshot(workspace.health_db, tmp_path / "incomplete-copy.db")
    assert workspace.health_db.read_bytes() == before
    assert m.current() == workspace
