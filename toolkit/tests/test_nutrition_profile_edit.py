"""Closed nutrition setup saves preserve settings and transaction integrity."""

from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from hermes_insights import nutrition, runtime
from hermes_insights.catalogs import MICRO_KEYS
from hermes_insights.command_context import CommandContext
from hermes_insights.commands.nutrition import nutrition_profile_set


@pytest.fixture()
def profile_context(tmp_path):
    database = tmp_path / "fictional-profile.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((Path(__file__).parents[1] / "SCHEMA.sql").read_text())
    with sqlite3.connect(database) as source, sqlite3.connect(tmp_path / "profile-before.db") as backup:
        source.backup(backup)
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    return CommandContext(
        database=str(database), timezone="Europe/Paris",
        clock=lambda: datetime(2026, 1, 15, 12, tzinfo=ZoneInfo("Europe/Paris")),
        vault=str(tmp_path / "vault"), cli_path="unused",
    )


@pytest.fixture()
def config():
    offsets = {"cut": -300, "maintain": 0, "bulk": 300}
    protein = {"cut": 1.8, "maintain": 1.5, "bulk": 1.6}
    return nutrition.NutritionConfig(
        target_seed=nutrition.target_seed(
            phase_offsets=offsets, protein_per_kg=protein,
            water_ml_per_kg=30, water_ml_per_exercise_hour=400,
            water_hot_day_bonus_ml=250, water_hot_day_temp_c=28,
        ),
        micro_targets={key: 10 for key in MICRO_KEYS},
        phase_offsets=offsets, protein_per_kg=protein,
        micro_seed=[], micro_target_extra=[], water_target_ml=1800,
    )


def test_profile_save_persists_the_complete_profile(profile_context):
    result = nutrition_profile_set(
        profile_context, SimpleNamespace(height_cm="165.5", sex="female", dob="2001-01-15"))

    assert result["ok"] is True
    with sqlite3.connect(profile_context.database) as connection:
        assert connection.execute(
            "SELECT key, value, updated_at FROM owner_profile ORDER BY key"
        ).fetchall() == [
            ("dob", "2001-01-15", "2026-01-15"),
            ("height_cm", "165.5", "2026-01-15"),
            ("sex", "female", "2026-01-15"),
        ]


def test_profile_save_preserves_other_target_settings(profile_context):
    with sqlite3.connect(profile_context.database) as connection:
        connection.executemany(
            "INSERT INTO owner_profile(key,value,updated_at) VALUES(?,?,'2026-01-01')",
            [("activity_fallback", "light"), ("phase", "bulk"), ("phase_started", "2026-01-01")],
        )
        connection.execute("INSERT INTO nutrition_targets(name,target) VALUES('kcal',2400)")
        connection.execute("INSERT INTO body_metrics(date,weight_kg) VALUES('2026-01-14',62)")

    nutrition_profile_set(
        profile_context, SimpleNamespace(height_cm="165", sex="female", dob="2001-01-15"))

    with sqlite3.connect(profile_context.database) as connection:
        assert connection.execute(
            "SELECT key,value,updated_at FROM owner_profile WHERE key NOT IN ('dob','height_cm','sex') ORDER BY key"
        ).fetchall() == [
            ("activity_fallback", "light", "2026-01-01"),
            ("phase", "bulk", "2026-01-01"),
            ("phase_started", "2026-01-01", "2026-01-01"),
        ]
        assert connection.execute("SELECT name,target FROM nutrition_targets").fetchall() == [("kcal", 2400)]
        assert connection.execute("SELECT date,weight_kg FROM body_metrics").fetchall() == [("2026-01-14", 62)]


def test_saved_profile_recomputes_estimates_from_the_canonical_owner(profile_context, config):
    with sqlite3.connect(profile_context.database) as connection:
        connection.execute("INSERT INTO body_metrics(date,weight_kg) VALUES('2026-01-14',62)")
        connection.execute("INSERT INTO body_metrics(date,weight_kg) VALUES('2026-01-16',90)")
    nutrition_profile_set(
        profile_context, SimpleNamespace(height_cm="165", sex="female", dob="2001-01-15"))

    with runtime.connect_read_only(profile_context.database) as connection:
        targets = nutrition.compute_targets(connection, clock=profile_context.clock, config=config)

    assert targets["status"] == "ok"
    assert targets["profile"]["weight_kg"] == 62
    assert targets["profile"]["age"] == 25
    assert targets["maintenance_kcal"] == 2116
    assert targets["targets"]["kcal"]["target"] == 2116
    assert targets["targets"]["protein_g"]["target"] == 93


def test_profile_save_leaves_missing_weight_unknown(profile_context, config):
    nutrition_profile_set(
        profile_context, SimpleNamespace(height_cm="165", sex="female", dob="2001-01-15"))

    with runtime.connect_read_only(profile_context.database) as connection:
        targets = nutrition.compute_targets(connection, clock=profile_context.clock, config=config)

    assert targets["status"] == "insufficient_data"
    assert "no weight_kg" in targets["reason"]
    assert targets["targets"]["water_ml"]["target"] == 1800


def test_profile_save_does_not_invent_missing_target_configuration(profile_context, config):
    with sqlite3.connect(profile_context.database) as connection:
        connection.execute("INSERT INTO body_metrics(date,weight_kg) VALUES('2026-01-14',62)")
    nutrition_profile_set(
        profile_context, SimpleNamespace(height_cm="165", sex="female", dob="2001-01-15"))

    with runtime.connect_read_only(profile_context.database) as connection:
        targets = nutrition.compute_targets(
            connection, clock=profile_context.clock, config=replace(config, micro_targets={}))

    assert targets["status"] == "insufficient_data"
    assert targets["reason"] == "nutrient targets are not configured"
    assert targets["targets"]["water_ml"]["target"] == 1850


def test_cli_profile_save_uses_the_explicit_database(profile_context):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "health.py"), "nutrition-profile-set",
         "--height-cm", "168", "--sex", "female", "--dob", "2001-01-15"],
        env={**os.environ, "HEALTH_DB": profile_context.database},
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(profile_context.database) as connection:
        assert connection.execute("SELECT key,value FROM owner_profile ORDER BY key").fetchall() == [
            ("dob", "2001-01-15"), ("height_cm", "168.0"), ("sex", "female"),
        ]


@pytest.mark.parametrize("args", [
    ["--height-cm", "165", "--sex", "female"],
    ["--height-cm", "165", "--sex", "female", "--dob", "2001-01-15", "--height-cm", "180"],
    ["--height-cm", "165", "--sex", "female", "--dob", "2001-01-15", "--activity-fallback", "active"],
    ["--height", "165", "--sex", "female", "--dob", "2001-01-15"],
])
def test_cli_profile_contract_rejects_ambiguous_or_incomplete_requests(profile_context, args):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "health.py"), "nutrition-profile-set", *args],
        env={**os.environ, "HEALTH_DB": profile_context.database},
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0
    with sqlite3.connect(profile_context.database) as connection:
        assert connection.execute("SELECT key FROM owner_profile").fetchall() == []


@pytest.mark.parametrize("height,dob", [(100, "1900-01-01"), (250, "2026-01-15")])
def test_profile_save_accepts_existing_profile_bounds(profile_context, height, dob):
    result = nutrition_profile_set(
        profile_context, SimpleNamespace(height_cm=height, sex="male", dob=dob))
    assert result["profile"] == {"height_cm": height, "sex": "male", "dob": dob}


@pytest.mark.parametrize("field,value", [
    ("height_cm", 99.9), ("height_cm", 250.1), ("height_cm", True),
    ("height_cm", "not-a-number"), ("height_cm", "NaN"), ("height_cm", float("inf")),
    ("height_cm", 10 ** 500), ("height_cm", []), ("height_cm", None),
    ("sex", "unspecified"), ("sex", True), ("sex", []),
    ("dob", True), ("dob", "20010115"), ("dob", "2001-W03-1"),
    ("dob", "2026-02-30"), ("dob", "1899-12-31"), ("dob", "2026-01-16"),
])
def test_invalid_profile_never_changes_saved_fields(profile_context, field, value):
    with sqlite3.connect(profile_context.database) as connection:
        connection.execute("INSERT INTO owner_profile(key,value,updated_at) VALUES('height_cm','170','2026-01-01')")
    payload = {"height_cm": "165", "sex": "female", "dob": "2001-01-15", field: value}

    with pytest.raises(SystemExit, match=field):
        nutrition_profile_set(profile_context, SimpleNamespace(**payload))

    with sqlite3.connect(profile_context.database) as connection:
        assert connection.execute("SELECT key,value FROM owner_profile").fetchall() == [("height_cm", "170")]


def test_partial_profile_failure_rolls_back_previous_fields(profile_context):
    with sqlite3.connect(profile_context.database) as connection:
        connection.executemany(
            "INSERT INTO owner_profile(key,value,updated_at) VALUES(?,?,'2026-01-01')",
            [("height_cm", "170"), ("sex", "male"), ("dob", "2000-05-02")],
        )
        connection.executescript(
            "CREATE TRIGGER reject_dob BEFORE INSERT ON owner_profile WHEN NEW.key='dob' "
            "BEGIN SELECT RAISE(ABORT,'fictional profile storage failure'); END;"
        )

    with pytest.raises(sqlite3.IntegrityError, match="fictional profile storage failure"):
        nutrition_profile_set(
            profile_context, SimpleNamespace(height_cm="165", sex="female", dob="2001-01-15"))

    with sqlite3.connect(profile_context.database) as connection:
        assert connection.execute(
            "SELECT key,value,updated_at FROM owner_profile ORDER BY key"
        ).fetchall() == [
            ("dob", "2000-05-02", "2026-01-01"),
            ("height_cm", "170", "2026-01-01"),
            ("sex", "male", "2026-01-01"),
        ]
