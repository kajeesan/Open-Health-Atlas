"""Fictional public-v7 upgrades and fail-closed training-context persistence."""

from pathlib import Path
import sqlite3

import pytest

from hermes_insights import migrations, training_context


ROOT = Path(__file__).resolve().parents[1]
CODE_VERSION = "fictional-training-migration"


def v7_database(tmp_path):
    database = tmp_path / "health.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((ROOT / "SCHEMA.sql").read_text())
        for table in ("fitness_source_revisions", "exercise_template_sources", "hevy_set_sources",
                      "training_context_revisions", "exercise_source_revisions"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("INSERT INTO hevy_sets(date,exercise_title,reps,source) VALUES('2026-06-01','Fictional row',6,'ui')")
    migrations.migrate(str(database), 7, 0, CODE_VERSION)
    return database


def test_current_public_v7_upgrade_preserves_records_and_ledger(tmp_path):
    database = v7_database(tmp_path)
    with sqlite3.connect(database) as connection:
        ledger_before = connection.execute("SELECT * FROM schema_migrations").fetchall()
        snapshot = sqlite3.connect(tmp_path / "before.db")
        connection.backup(snapshot)
        assert snapshot.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        snapshot.close()

    result = migrations.migrate(str(database), 8, 7, CODE_VERSION)

    assert [step["version"] for step in result["applied"]] == [8]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM schema_migrations WHERE version<=7").fetchall() == ledger_before
        assert connection.execute("SELECT exercise_title,reps,source FROM hevy_sets").fetchall() == [("Fictional row", 6, "ui")]
        assert connection.execute("SELECT COUNT(*) FROM training_context_revisions").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_training_migration_rolls_back_on_integrity_failure(tmp_path, monkeypatch):
    database = v7_database(tmp_path)
    with sqlite3.connect(database) as connection:
        before = tuple(connection.iterdump())

    def integrity_failure(connection):
        raise migrations.SchemaError("migration_integrity", "injected integrity failure")

    monkeypatch.setattr(migrations, "_integrity", integrity_failure)
    with pytest.raises(migrations.SchemaError, match="injected integrity failure"):
        migrations.migrate(str(database), 8, 7, CODE_VERSION)

    with sqlite3.connect(database) as connection:
        assert tuple(connection.iterdump()) == before


def test_historical_training_v6_ledger_is_never_relabelled(tmp_path):
    database = v7_database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=7")
        connection.execute("UPDATE schema_migrations SET name='006_training_context' WHERE version=6")
    before = database.read_bytes()

    with pytest.raises(migrations.SchemaError) as failure:
        migrations.migrate(str(database), 8, 6, CODE_VERSION)

    assert failure.value.code == "migration_checksum_mismatch"
    assert database.read_bytes() == before


@pytest.mark.parametrize("conflict", [
    "CREATE TABLE training_context_revisions(id INTEGER)",
    "CREATE VIEW training_context_revisions AS SELECT 1 AS id",
    migrations.TRAINING_CONTEXT_DDL["exercise_source_revisions"].replace(
        "source_key TEXT NOT NULL", "source_key TEXT COLLATE NOCASE NOT NULL"),
])
def test_conflicting_training_shape_is_rejected_before_writing(tmp_path, conflict):
    database = v7_database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(conflict)
    before = database.read_bytes()

    with pytest.raises(migrations.SchemaError):
        migrations.migrate(str(database), 8, 7, CODE_VERSION)

    assert database.read_bytes() == before


def test_unexpected_training_trigger_is_rejected_on_current_schema(tmp_path):
    database = v7_database(tmp_path)
    migrations.migrate(str(database), 8, 7, CODE_VERSION)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TRIGGER unexpected_training_insert AFTER INSERT ON training_context_revisions BEGIN SELECT 1; END")
    before = database.read_bytes()

    with pytest.raises(migrations.SchemaError, match="unexpected training context object"):
        migrations.schema_status(str(database))

    assert database.read_bytes() == before


@pytest.fixture
def context_db(tmp_path):
    database = v7_database(tmp_path)
    migrations.migrate(str(database), 8, 7, CODE_VERSION)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    yield connection
    connection.close()


@pytest.mark.parametrize("changed,code", [
    ({"subject_kind": []}, "invalid_context_key"),
    ({"context_key": "side"}, "invalid_context_key"),
    ({"capture_id": " spaced "}, "invalid_context_identity"),
    ({"effective_from": "2026-6-01"}, "invalid_context_date"),
    ({"effective_from": "2026-02-30"}, "invalid_context_date"),
    ({"effective_to": "2026-05-31"}, "invalid_context_interval"),
    ({"status": "guessed"}, "invalid_context_status"),
    ({"status": "skipped"}, "skipped_answer_must_be_unknown"),
    ({"value": " " * 1001 + "x"}, "invalid_context_value"),
    ({"subject_key": "someone_else"}, "invalid_history_subject"),
    ({"supersedes_id": True}, "invalid_correction_identity"),
])
def test_invalid_context_is_rejected_without_persistence(context_db, changed, code):
    fields = {"capture_id": "fictional-answer", "subject_kind": "history", "subject_key": "personal",
              "context_key": "history_note", "effective_from": "2026-06-01", "value": "Fictional history"}
    fields.update(changed)

    with pytest.raises(training_context.TrainingError, match=code):
        training_context.capture_context(context_db, **fields)

    assert context_db.execute("SELECT COUNT(*) FROM training_context_revisions").fetchone()[0] == 0


def test_correction_only_changes_its_explicit_effective_interval(context_db):
    base = {"subject_kind": "history", "subject_key": "personal", "context_key": "history_note"}
    old = training_context.capture_context(context_db, capture_id="original-history", effective_from="2026-06-01",
                                          value="Original report", **base)
    training_context.capture_context(context_db, capture_id="corrected-days", effective_from="2026-06-08",
                                     effective_to="2026-06-10", value="Corrected report", supersedes_id=old["id"], **base)
    rows = training_context.context_rows(context_db, "2026-06-30")

    values = [training_context.resolve_context(rows, "history", "personal", day)["history_note"]["value"]
              for day in ("2026-06-07", "2026-06-08", "2026-06-10", "2026-06-11")]

    assert values == ["Original report", "Corrected report", "Corrected report", "Original report"]


def test_skipped_correction_remains_unknown_instead_of_restoring_old_value(context_db):
    base = {"subject_kind": "history", "subject_key": "personal", "context_key": "history_note",
            "effective_from": "2026-06-01"}
    old = training_context.capture_context(context_db, capture_id="old-history", value="Uncertain memory", **base)
    training_context.capture_context(context_db, capture_id="skip-history", status="skipped",
                                     supersedes_id=old["id"], **base)

    resolved = training_context.resolve_context(training_context.context_rows(context_db, "2026-06-30"),
                                                "history", "personal", "2026-06-10")["history_note"]

    assert resolved["status"] == "skipped"
    assert resolved["value"] is None


def test_database_guard_rejects_answered_null(context_db):
    with pytest.raises(sqlite3.IntegrityError):
        context_db.execute("""INSERT INTO training_context_revisions
            (capture_id,subject_kind,subject_key,context_key,effective_from,status,value)
            VALUES('invalid-null','history','personal','history_note','2026-06-01','answered',NULL)""")


def test_correction_chain_retains_future_dated_ancestors_for_historical_resolution(context_db):
    base = {"subject_kind": "history", "subject_key": "personal", "context_key": "history_note"}
    first = training_context.capture_context(context_db, capture_id="original-date", effective_from="2026-06-05",
                                            value="Original report", **base)
    second = training_context.capture_context(context_db, capture_id="incorrect-future-date", effective_from="2026-07-01",
                                             value="Incorrect date", supersedes_id=first["id"], **base)
    training_context.capture_context(context_db, capture_id="corrected-earlier-date", effective_from="2026-06-01",
                                     value="Corrected report", supersedes_id=second["id"], **base)

    resolved = training_context.resolve_context(training_context.context_rows(context_db, "2026-06-30"),
                                                "history", "personal", "2026-06-10")

    assert resolved["history_note"]["value"] == "Corrected report"
