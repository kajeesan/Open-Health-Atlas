"""Boundary characterization for the daily-frame, Recovery, and lab CLI.

These cases pin distinct public contracts that are easy to lose while moving
the commands behind their new owners.  The records are fictional and every
assertion is made through the real CLI or its observable SQLite result.
"""

from datetime import date, datetime, timedelta
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
CATALOG = ROOT / "tests" / "fixtures" / "lab_catalog.synthetic.md"
TEST_TZ = ZoneInfo("Europe/Paris")
FIXED_NOW = datetime(2026, 7, 15, 12, tzinfo=TEST_TZ)
FIXED_CLOCK_LAUNCHER = """
from datetime import datetime
from zoneinfo import ZoneInfo
from hermes_insights import runtime

runtime.now = lambda **_kwargs: datetime(
    2026, 7, 15, 12, tzinfo=ZoneInfo("Europe/Paris")
)
import health

health.main()
"""


def make_db(tmp_path):
    path = tmp_path / "health.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()
    return path


@pytest.fixture()
def db(tmp_path):
    path = make_db(tmp_path)
    run(path, "import-lab-catalog", str(CATALOG), "--seed")
    return path


def run(db, *args, expect_ok=True, stdin=None):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args],
        env={
            **os.environ,
            "HEALTH_DB": str(db),
            "HERMES_TIMEZONE": "Europe/Paris",
            "TZ": "Europe/Paris",
            "HERMES_CODE_VERSION": "d" * 40,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if expect_ok:
        assert result.returncode == 0, f"{args}: {result.stderr or result.stdout}"
    return result


def run_fixed_clock(db, *args, expect_ok=True, stdin=None):
    result = subprocess.run(
        [sys.executable, "-c", FIXED_CLOCK_LAUNCHER, *args],
        env={
            **os.environ,
            "HEALTH_DB": str(db),
            "HERMES_TIMEZONE": "Europe/Paris",
            "TZ": "Europe/Paris",
            "HERMES_CODE_VERSION": "d" * 40,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if expect_ok:
        assert result.returncode == 0, f"{args}: {result.stderr or result.stdout}"
    return result


def output(result):
    return json.loads(result.stdout)


def rows(db, sql, params=()):
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def seed(db, sql, values):
    connection = sqlite3.connect(db)
    connection.executemany(sql, values)
    connection.commit()
    connection.close()


def database_dump(db):
    connection = sqlite3.connect(db)
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


def test_lab_commit_rolls_back_when_a_late_row_write_fails(db):
    connection = sqlite3.connect(db)
    connection.execute(
        """CREATE TRIGGER fixture_fail_late_lab_insert
           BEFORE INSERT ON labs
           WHEN NEW.test_name = 'Sodium'
           BEGIN SELECT RAISE(ABORT, 'fixture late write failure'); END"""
    )
    connection.commit()
    connection.close()
    before_failure = database_dump(db)

    report = (
        "Fixture Marker          1.5       unit       1 - 2             Normal\n"
        "Hemoglobin              14.2      g/dL      12.0 - 17.5       Normal\n"
        "Sodium                  140       mmol/L    135 - 145         Normal\n"
    )
    result = run(
        db, "lab-ingest", "--date", "2026-08-01", "--commit",
        "--confirm", "Fixture Marker", stdin=report, expect_ok=False,
    )

    assert result.returncode != 0
    assert "fixture late write failure" in (result.stderr + result.stdout)
    assert database_dump(db) == before_failure


def test_backdated_lab_uses_global_latest_prior_for_delta(db):
    run(
        db, "lab-ingest", "--date", "2026-08-01", "--commit",
        stdin="Creatinine              78        µmol/L    60 - 110          Normal\n",
    )
    result = output(run(
        db, "lab-ingest", "--date", "2026-07-01",
        stdin="Creatinine              320       µmol/L    60 - 110          Normal\n",
    ))

    row = result["rows"][0]
    assert row["tier"] == "blocked"
    assert row["checks"]["delta"] == "large_delta"
    assert row["last_value"] == 78.0
    assert len(rows(db, "SELECT test_name FROM labs")) == 1


def test_lab_rows_in_one_report_do_not_become_each_others_priors(db):
    report = (
        "Creatinine              78        µmol/L    60 - 110          Normal\n"
        "Creatinine              320       µmol/L    60 - 110          High\n"
    )
    result = output(run(
        db, "lab-ingest", "--date", "2026-08-01", "--commit", stdin=report,
    ))

    assert result["committed"] == 2
    assert [row["checks"]["delta"] for row in result["rows"]] == [
        "no_prior", "no_prior",
    ]
    assert len(rows(db, "SELECT test_name, value FROM labs")) == 2


def test_lab_catalog_edit_reflags_rows_without_report_interval(db):
    run(
        db, "lab-ingest", "--date", "2026-08-01", "--commit",
        stdin="Creatinine              78        µmol/L\n",
    )
    before = output(run(db, "labs"))
    creatinine_before = next(
        test for panel in before["panels"] for test in panel["tests"]
        if test["canonical"] == "Creatinine"
    )
    assert creatinine_before["reference_low"] == 60.0
    assert creatinine_before["reference_high"] == 110.0
    assert creatinine_before["in_range"] is True

    connection = sqlite3.connect(db)
    connection.execute(
        "UPDATE lab_catalog SET ref_low=80.0, ref_high=90.0 "
        "WHERE canonical='Creatinine'"
    )
    connection.commit()
    connection.close()

    after = output(run(db, "labs"))
    creatinine_after = next(
        test for panel in after["panels"] for test in panel["tests"]
        if test["canonical"] == "Creatinine"
    )
    assert creatinine_after["reference_low"] == 80.0
    assert creatinine_after["reference_high"] == 90.0
    assert creatinine_after["in_range"] is False
    assert rows(
        db, "SELECT reference_low, reference_high FROM labs"
    ) == [{"reference_low": None, "reference_high": None}]


def test_lab_trend_dedupes_exact_points_but_keeps_distinct_same_date_values(db):
    line = lambda value, flag: (
        f"Creatinine              {value}        µmol/L    {flag}\n"
    )
    run(db, "lab-ingest", "--date", "2026-08-01", "--commit",
        stdin=line(78, "Normal"))
    run(db, "lab-ingest", "--date", "2026-08-01", "--commit",
        stdin=line(78, "Retest"))
    run(db, "lab-ingest", "--date", "2026-08-01", "--commit",
        stdin=line(84, "Normal"))

    trend = output(run(db, "labs", "--test", "Creatinine"))
    assert trend["n"] == 2
    assert [point["value"] for point in trend["series"]] == [78.0, 84.0]
    assert trend["series"][0]["flag"] == "Retest"


def test_daily_frame_all_history_starts_from_primary_record_tables_only(db):
    old = date(2000, 1, 1).isoformat()
    seed(db, "INSERT INTO body_metrics(date, weight_kg) VALUES(?, ?)", [(old, 70.0)])
    seed(db, "INSERT INTO weather(date, temp_max_c) VALUES(?, ?)", [(old, 20.0)])
    seed(db, "INSERT INTO intake(date, water_ml) VALUES(?, ?)", [(old, 1800.0)])
    seed(db, "INSERT INTO habits_log(date, habit, done) VALUES(?, ?, ?)",
         [(old, "fictional-habit", 1)])

    frame = output(run_fixed_clock(db, "build-daily-frame", "--days", "0"))

    window = frame["meta"]["window"]
    assert window == {
        "from": FIXED_NOW.date().isoformat(),
        "to": FIXED_NOW.date().isoformat(),
        "days": 1,
    }
    assert [row["date"] for row in frame["days"]] == [FIXED_NOW.date().isoformat()]
    for table in ("body_metrics", "weather", "intake", "habits_log"):
        assert frame["meta"]["coverage"]["table_rows"].get(table) == 0


def test_future_workout_extends_zero_fill_without_appearing_as_a_frame_date(db):
    past = (FIXED_NOW.date() - timedelta(days=2)).isoformat()
    future = (FIXED_NOW.date() + timedelta(days=3)).isoformat()
    seed(db, "INSERT INTO workouts(date, type, minutes) VALUES(?, ?, ?)",
         [(past, "run", 30), (future, "run", 45)])

    frame = output(run_fixed_clock(db, "build-daily-frame", "--days", "7"))
    by_date = {row["date"]: row for row in frame["days"]}

    assert future not in by_date
    assert by_date[past]["cardio_min"] == 30
    assert by_date[FIXED_NOW.date().isoformat()]["cardio_min"] == 0
    assert frame["meta"]["coverage"]["table_rows"]["workouts"] == 2


def test_recovery_range_excludes_days_since_but_not_anchored_seven_day_sets(db):
    run(db, "migrate", "--to", "6", "--expected-from", "0")
    anchor = date(2026, 6, 30)
    session = anchor - timedelta(days=5)
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) "
         "VALUES(?, ?, ?)", [("Bench", "chest", 1.0)])
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, "
         "reps, set_type) VALUES(?, ?, ?, ?, ?, 'normal')",
         [(session.isoformat(), "Bench", index, 100.0, 5)
          for index in range(1, 4)])

    result = output(run(
        db, "readiness", "--from", "2026-06-29", "--anchor", anchor.isoformat(),
    ))
    chest = next(row for row in result["muscle_recovery"] if row["group"] == "Chest")

    assert chest["days_since"] is None
    assert chest["note"] == "never logged"
    assert chest["sets_7d"] == 3.0
