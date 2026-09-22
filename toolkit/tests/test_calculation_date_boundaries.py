"""Current-day calculations exclude records dated after the controlled clock."""

from datetime import date, datetime, timedelta
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import health
from hermes_insights import nutrition
from hermes_insights.catalogs import MICRO_KEYS, MICRO_SEED, MICRO_TARGET_EXTRA


TODAY = date(2026, 1, 15)
TZ = ZoneInfo("Europe/Paris")
SCHEMA = Path(__file__).resolve().parents[1] / "SCHEMA.sql"


@pytest.fixture()
def calculation_db(tmp_path):
    path = tmp_path / "calculations.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA.read_text())
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def nutrition_config():
    phase_offsets = {"cut": -300, "maintain": 0, "bulk": 300}
    protein_per_kg = {"cut": 1.8, "maintain": 1.5, "bulk": 1.6}
    return nutrition.NutritionConfig(
        target_seed=nutrition.target_seed(
            phase_offsets=phase_offsets, protein_per_kg=protein_per_kg,
            water_ml_per_kg=30, water_ml_per_exercise_hour=400,
            water_hot_day_bonus_ml=250, water_hot_day_temp_c=28,
        ),
        micro_targets=dict.fromkeys(MICRO_KEYS, 10),
        phase_offsets=phase_offsets, protein_per_kg=protein_per_kg,
        micro_seed=[{**item, "target": 10} for item in MICRO_SEED],
        micro_target_extra=[dict(item) for item in MICRO_TARGET_EXTRA],
        water_target_ml=1800,
    )


@pytest.fixture()
def configured_profile(calculation_db):
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


def test_today_targets_do_not_use_a_future_weight(configured_profile, nutrition_config):
    configured_profile.execute(
        "INSERT INTO body_metrics(date,weight_kg) VALUES('2026-01-16',120)"
    )
    configured_profile.commit()

    result = nutrition.compute_targets(
        configured_profile, clock=lambda: datetime(2026, 1, 15, 12, tzinfo=TZ),
        config=nutrition_config,
    )

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
    configured_profile, nutrition_config, sql, values,
):
    configured_profile.executemany(
        sql,
        [((TODAY + timedelta(days=ahead)).isoformat(), *values)
         for ahead in range(1, 15)],
    )
    configured_profile.commit()

    result = nutrition.compute_targets(
        configured_profile, clock=lambda: datetime(2026, 1, 15, 12, tzinfo=TZ),
        config=nutrition_config,
    )

    assert result["activity"]["days_with_data"] == 0
    assert result["activity"]["basis"] == "fallback"
    assert result["activity"]["level"] == "sedentary"


def test_independent_target_configs_use_their_own_civil_clock(
    configured_profile, nutrition_config,
):
    other_protein = {"cut": 2, "maintain": 2, "bulk": 2}
    other_config = replace(
        nutrition_config, protein_per_kg=other_protein,
        target_seed={**nutrition_config.target_seed,
                     "protein_g_per_kg": {"values": other_protein, "source": "fixture"}},
    )
    before_birthday = lambda: datetime(2026, 1, 15, 23, 59, tzinfo=TZ)
    after_birthday = lambda: datetime(2026, 1, 16, 0, 1, tzinfo=TZ)

    first = nutrition.compute_targets(
        configured_profile, clock=before_birthday, config=nutrition_config)
    second = nutrition.compute_targets(
        configured_profile, clock=after_birthday, config=other_config)
    repeated = nutrition.compute_targets(
        configured_profile, clock=before_birthday, config=nutrition_config)

    assert (first["profile"]["age"], first["targets"]["protein_g"]["target"]) == (25, 120)
    assert (second["profile"]["age"], second["targets"]["protein_g"]["target"]) == (26, 160)
    assert repeated == first


def test_future_readings_cannot_change_current_recovery(calculation_db, capsys, monkeypatch):
    database = calculation_db.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.setattr(health, "DB", database)
    monkeypatch.setattr(health, "_now", lambda: datetime(2026, 1, 15, 12, tzinfo=TZ))
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
