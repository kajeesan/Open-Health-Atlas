"""Retained training write semantics at the real CLI and SQLite boundary."""

from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from hermes_insights import migrations, orchestrator


ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "health.py"


@pytest.fixture()
def training_db(tmp_path):
    database = tmp_path / "training.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((ROOT / "SCHEMA.sql").read_text())
        connection.execute(
            "INSERT INTO routines(routine_name,exercise_title,ex_order,"
            "target_sets,target_reps,target_weight_kg) "
            "VALUES('Base','Front Squat',1,3,8,60)"
        )
        connection.execute(
            "INSERT INTO training_schedule(weekday,routine_name) VALUES('Mon','Base')"
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version,name,checksum_sha256,applied_at,code_version) "
            "VALUES(?,?,?,'2026-07-22T00:00:00+00:00','training-characterization')",
            [(migration.version, migration.name, migration.checksum)
             for migration in migrations.MIGRATIONS],
        )
    return database


def run(database, *args):
    return subprocess.run(
        [sys.executable, str(HEALTH), *args], text=True, capture_output=True,
        timeout=30, env={**os.environ, "HEALTH_DB": str(database)},
    )


def database_dump(database):
    with sqlite3.connect(database) as connection:
        return tuple(connection.iterdump())


def test_fitness_capture_keeps_valid_fields_beyond_its_required_kind(training_db):
    # A strength test requires load/reps but historically retains other valid
    # measurements. Extraction must not turn that minimum into an exclusivity rule.
    result = run(
        training_db, "fitness-test-log", "leg-extension", "--side", "left",
        "--load", "40", "--reps", "8", "--seconds", "30", "--rating", "2",
        "--cm", "15", "--degrees", "90", "--passed", "1", "--date", "2026-07-20",
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(training_db) as connection:
        assert connection.execute(
            "SELECT load_kg,reps,seconds,rating,cm,degrees,passed FROM fitness_tests"
        ).fetchall() == [(40, 8, 30, 2, 15, 90, 1)]


def test_repeated_physio_void_replaces_reason_without_trimming(training_db):
    with sqlite3.connect(training_db) as connection:
        connection.execute(
            "INSERT INTO pain_log(date,region,side,intensity,source,voided,void_reason) "
            "VALUES('2026-07-20','anterior-knee','left',3,'manual',1,'wrong side')"
        )

    result = run(training_db, "physio-void", "--kind", "pain", "1", "--reason", "   ")

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(training_db) as connection:
        assert connection.execute(
            "SELECT voided,void_reason FROM pain_log WHERE id=1"
        ).fetchone() == (1, "   ")


def test_fitness_void_turns_whitespace_reason_into_null(training_db):
    with sqlite3.connect(training_db) as connection:
        connection.execute(
            "INSERT INTO fitness_tests(date,movement,side,load_kg,reps) "
            "VALUES('2026-07-20','leg-extension','left',40,8)"
        )

    result = run(training_db, "fitness-test-void", "1", "--reason", "   ")

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(training_db) as connection:
        assert connection.execute(
            "SELECT voided,void_reason FROM fitness_tests WHERE id=1"
        ).fetchone() == (1, None)


def test_fitness_trigger_failure_preserves_the_complete_database(training_db):
    with sqlite3.connect(training_db) as connection:
        connection.row_factory = sqlite3.Row
        # The empty fixture's next fitness row is 1. Its existing trigger has
        # a different immutable start time, so the real enqueue must refuse it.
        orchestrator.enqueue_internal_trigger(
            connection, trigger_kind="quarterly_observation",
            source_table="fitness_tests", source_row_key="1", event_date="2026-07-20",
            not_before="2026-07-21T00:00:00+00:00", now="2026-07-20T12:00:00+00:00",
        )
    before = database_dump(training_db)

    result = run(
        training_db, "fitness-test-log", "balance-stand", "--side", "left",
        "--seconds", "30", "--date", "2026-07-20", "--source", "manual",
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "idempotency_conflict"
    assert database_dump(training_db) == before


@pytest.mark.parametrize(
    ("prior_command", "command"),
    [
        pytest.param(
            ("routine-set", "Base", "Front Squat", "--weight", "62"),
            ("routine-set", "Base", "Front Squat", "--weight", "65"), id="set",
        ),
        pytest.param(
            ("routine-set", "Base", "Front Squat", "--weight", "62"),
            ("routine-remove", "Base", "Front Squat"), id="remove",
        ),
        pytest.param(
            ("routine-set", "Base", "Front Squat", "--weight", "62"),
            ("routine-undo",), id="undo-routine",
        ),
        pytest.param(
            ("schedule-set", "Mon", "Rest"),
            ("routine-undo",), id="undo-schedule",
        ),
    ],
)
def test_plan_append_failure_preserves_routine_history_and_schedule(
    training_db, prior_command, command,
):
    setup = run(training_db, *prior_command)
    assert setup.returncode == 0, setup.stderr
    with sqlite3.connect(training_db) as connection:
        connection.execute(
            "CREATE TRIGGER reject_plan_append BEFORE INSERT ON training_plan_revisions "
            "BEGIN SELECT RAISE(ABORT,'training plan unavailable'); END"
        )
    before = database_dump(training_db)

    result = run(training_db, *command)

    assert result.returncode == 1
    assert "training plan unavailable" in result.stderr
    assert database_dump(training_db) == before


def test_history_append_failure_preserves_routine_edit(training_db):
    with sqlite3.connect(training_db) as connection:
        connection.execute(
            "CREATE TRIGGER reject_routine_history BEFORE INSERT ON routines_history "
            "BEGIN SELECT RAISE(ABORT,'routine history unavailable'); END"
        )
    before = database_dump(training_db)

    result = run(training_db, "routine-set", "Base", "Front Squat", "--weight", "65")

    assert result.returncode == 1
    assert "routine history unavailable" in result.stderr
    assert database_dump(training_db) == before
