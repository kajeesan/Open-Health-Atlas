"""Current-day calculations exclude records dated after the controlled clock."""

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import health


TODAY = date(2026, 1, 15)
TZ = ZoneInfo("Europe/Paris")
SCHEMA = Path(__file__).resolve().parents[1] / "SCHEMA.sql"


@pytest.fixture()
def calculation_db(tmp_path, monkeypatch):
    path = tmp_path / "calculations.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA.read_text())
    monkeypatch.setattr(health, "DB", str(path))
    monkeypatch.setattr(
        health, "_now", lambda: datetime(2026, 1, 15, 12, tzinfo=TZ)
    )
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def configured_profile(calculation_db, monkeypatch):
    monkeypatch.setattr(health, "CONFIGURED_MICRO_TARGETS", dict.fromkeys(health.MICRO_KEYS, 10))
    monkeypatch.setattr(health, "CONFIGURED_PHASE_OFFSETS", {"cut": -300, "maintain": 0, "bulk": 300})
    monkeypatch.setattr(health, "CONFIGURED_PROTEIN_PER_KG", {"cut": 1.8, "maintain": 1.5, "bulk": 1.6})
    monkeypatch.setitem(health.TARGETS_SEED["phase_offset_kcal"], "values", health.CONFIGURED_PHASE_OFFSETS)
    monkeypatch.setitem(health.TARGETS_SEED["protein_g_per_kg"], "values", health.CONFIGURED_PROTEIN_PER_KG)
    calculation_db.executemany(
        "INSERT INTO owner_profile(key,value,updated_at) "
        "VALUES(?,?,'2026-01-15T12:00:00+01:00')",
        [("height_cm", "180"), ("sex", "male"), ("dob", "2000-01-16"),
         ("activity_fallback", "sedentary")],
    )
    calculation_db.execute(
        "INSERT INTO body_metrics(date,weight_kg) VALUES(?,?)",
        (TODAY.isoformat(), 80),
    )
    calculation_db.commit()
    return calculation_db


def test_today_targets_do_not_use_a_future_weight(configured_profile, capsys):
    configured_profile.execute(
        "INSERT INTO body_metrics(date,weight_kg) VALUES('2026-01-16',120)"
    )
    configured_profile.commit()

    health.nutrition_targets(SimpleNamespace())
    result = json.loads(capsys.readouterr().out)

    assert result["profile"]["weight_kg"] == 80
    assert result["maintenance_kcal"] == 2166


@pytest.mark.parametrize(
    ("sql", "values"),
    [("INSERT INTO daily_metrics(date,source,steps) VALUES(?,?,?)", ("apple", 20000)),
     ("INSERT INTO daily_metrics(date,source,exercise_min) VALUES(?,?,?)", ("apple", 120)),
     ("INSERT INTO hevy_sets(date,exercise_title,set_index,set_type,weight_kg,reps) "
      "VALUES(?,?,?,?,?,?)", ("Fixture lift", 1, "normal", 20, 10))],
    ids=["steps", "exercise-minutes", "workout-sessions"],
)
def test_future_activity_cannot_unlock_a_trailing_activity_estimate(
    configured_profile, capsys, sql, values,
):
    configured_profile.executemany(
        sql,
        [((TODAY + timedelta(days=ahead)).isoformat(), *values)
         for ahead in range(1, 15)],
    )
    configured_profile.commit()

    health.nutrition_targets(SimpleNamespace())
    result = json.loads(capsys.readouterr().out)

    assert result["activity"]["days_with_data"] == 0
    assert result["activity"]["basis"] == "fallback"
    assert result["activity"]["level"] == "sedentary"


def test_future_readings_cannot_change_current_recovery(calculation_db, capsys):
    calculation_db.executemany(
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) VALUES(?,?,?,?)",
        [((TODAY - timedelta(days=back)).isoformat(), "apple", 60, 50)
         for back in range(1, 15)],
    )
    calculation_db.execute(
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) "
        "VALUES(?,'apple',57,55)", (TODAY.isoformat(),),
    )
    calculation_db.executemany(
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) VALUES(?,?,?,?)",
        [((TODAY + timedelta(days=ahead)).isoformat(), "apple", 120, 10)
         for ahead in range(1, 16)],
    )
    calculation_db.commit()

    health.scores(SimpleNamespace(days=30))
    recovery = json.loads(capsys.readouterr().out)["scores"]["recovery"]

    assert recovery["score"] == 75
    assert recovery["inputs"]["baseline_days"] == 14
