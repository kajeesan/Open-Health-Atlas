"""§4b timing-aware adherence: done AND on time, generous no-guilt windows.

Drives the real CLI against a temp DB. Windows are complete days only (today
is in progress); bedtimes cross midnight via circular minute distance; only
scheduled weekdays expect a workout; dose = first MPH dose of the day.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import date, timedelta

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else {}
    assert r.returncode != 0
    return r.stderr


def d_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def sql(db, stmt, rows):
    con = sqlite3.connect(db)
    con.executemany(stmt, rows)
    con.commit()
    con.close()


# ── planned-time-set (config) ────────────────────────────────────────────────

def test_planned_time_set_validates(db):
    assert "metric must be" in run(db, "planned-time-set", "lunch", "12:00", expect_ok=False)
    assert "HH:MM" in run(db, "planned-time-set", "wake", "7am", expect_ok=False)
    assert "5-240" in run(db, "planned-time-set", "wake", "07:00", "--tolerance", "3", expect_ok=False)
    r = run(db, "planned-time-set", "wake", "07:00")
    assert r["tolerance_min"] == 30                    # metric default
    r = run(db, "planned-time-set", "workout", "17:30")
    assert r["tolerance_min"] == 60
    r = run(db, "planned-time-set", "wake", "06:45", "--tolerance", "20")
    assert r["planned"] == "06:45" and r["tolerance_min"] == 20   # upsert


# ── timing-adherence ─────────────────────────────────────────────────────────

def test_all_insufficient_until_plans_exist(db):
    r = run(db, "timing-adherence")
    for m in ("wake", "bed", "workout", "dose"):
        assert r["metrics"][m]["status"] == "insufficient_data"
        assert r["metrics"][m]["default_tolerance_min"] > 0
    assert "never a grade" in r["note"]


def test_wake_done_and_on_time_rates(db):
    run(db, "planned-time-set", "wake", "07:00")       # ±30
    sql(db, "INSERT INTO sleep_log(date, wake_time) VALUES(?,?)", [
        (d_ago(1), "07:20"),     # on time
        (d_ago(2), "08:15"),     # done, off window (75 min)
        (d_ago(3), "06:35"),     # on time
    ])
    m = run(db, "timing-adherence", "--days", "7")["metrics"]["wake"]
    assert m["done"] == {"days": 3, "rate": 0.429}     # 3 of 7 complete days
    assert m["on_time"]["days"] == 2 and m["on_time"]["of_timed_days"] == 3
    assert m["on_time"]["rate"] == 0.667
    assert m["streak_on_time"] == 1                    # yesterday on time; d-2 breaks


def test_bed_crosses_midnight_circularly(db):
    run(db, "planned-time-set", "bed", "23:30")        # ±30
    sql(db, "INSERT INTO sleep_log(date, bedtime) VALUES(?,?)", [
        (d_ago(1), "00:10"),     # 40 min past on the clock circle -> off
        (d_ago(2), "23:50"),     # 20 min -> on time
    ])
    m = run(db, "timing-adherence", "--days", "7")["metrics"]["bed"]
    assert m["on_time"]["days"] == 1
    assert m["streak_on_time"] == 0                    # yesterday was off-window


def test_dose_uses_first_configured_medication_dose_in_minutes(db):
    run(db, "planned-time-set", "dose", "08:00")       # ±30
    sql(db, "INSERT INTO meds_log(date, drug, dose_mg, time_taken) VALUES(?,?,?,?)", [
        (d_ago(1), "medication-alias", 10, "12:30"),
        (d_ago(1), "medication-alias", 5, "8:05"),
        (d_ago(2), "other-medication", 50, "07:55"),
    ])
    m = run(db, "timing-adherence", "--days", "7")["metrics"]["dose"]
    assert m["done"]["days"] == 1
    assert m["on_time"]["days"] == 1                   # 08:05 is within ±30
    assert m["median_abs_delta_min"] == 5


def test_workout_expected_only_on_scheduled_days(db):
    run(db, "planned-time-set", "workout", "17:00")    # ±60
    yesterday = date.today() - timedelta(days=1)
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    wd = weekdays[yesterday.weekday()]
    sql(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
        [(w, "Push A" if w == wd else "Rest") for w in weekdays])
    # trained yesterday at 17:30 CANON (15:30 UTC in July, CEST=UTC+2)
    sql(db, "INSERT INTO hevy_sets(date, exercise_title, start_time, set_type, weight_kg, reps, source)"
            " VALUES(?,?,?,?,?,?, 'hevy')",
        [(yesterday.isoformat(), "Front Squat", f"{yesterday.isoformat()}T15:30:00Z", "normal", 80, 5)])
    m = run(db, "timing-adherence", "--days", "7")["metrics"]["workout"]
    assert m["days_expected"] == 1                     # one scheduled day in the window
    assert m["done"] == {"days": 1, "rate": 1.0}
    assert m["on_time"]["days"] == 1                   # 17:30 vs 17:00 ±60
    assert m["streak_on_time"] == 1


def test_workout_unparseable_time_counts_done_not_timed(db):
    run(db, "planned-time-set", "workout", "17:00")
    yesterday = date.today() - timedelta(days=1)
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    sql(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
        [(w, "Push A") for w in weekdays])
    sql(db, "INSERT INTO hevy_sets(date, exercise_title, start_time, set_type, weight_kg, reps, source)"
            " VALUES(?,?,?,?,?,?, 'ui')",
        [(yesterday.isoformat(), "Front Squat", None, "normal", 80, 5)])
    m = run(db, "timing-adherence", "--days", "7")["metrics"]["workout"]
    assert m["done"]["days"] == 1
    assert m["on_time"]["of_timed_days"] == 0 and m["on_time"]["rate"] is None
    assert m["unparsed_times"] == 1


def test_window_excludes_today(db):
    run(db, "planned-time-set", "wake", "07:00")
    sql(db, "INSERT INTO sleep_log(date, wake_time) VALUES(?,?)",
        [(date.today().isoformat(), "07:00")])         # today only — in progress
    m = run(db, "timing-adherence", "--days", "7")["metrics"]["wake"]
    assert m["status"] == "insufficient_data"          # nothing in the complete-day window
