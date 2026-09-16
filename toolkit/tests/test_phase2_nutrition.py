"""Phase 2 nullable food, supplement and pain timing capture."""

import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "nutrition.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("""INSERT INTO recipes(
        recipe_id,name,batch_grams,grams_per_portion,portions)
        VALUES('sample-stew','Sample Stew',1000,250,4)""")
    con.execute("""INSERT INTO meal_inventory(
        recipe_id,portions_remaining,grams_per_portion,prepped_on)
        VALUES('sample-stew',4,250,'2026-07-01')""")
    con.execute("""INSERT INTO supplement_products(
        supplement_id,name,dose,unit,active) VALUES(1,'Creatine',5,'g',1)""")
    con.commit(); con.close()
    run(path, "migrate", "--to", "2", "--expected-from", "0")
    return path


def run(db, *args, stdin=None, expect=0):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args], input=stdin, text=True,
        capture_output=True, timeout=30,
        env={**os.environ, "HEALTH_DB": str(db), "HERMES_CODE_VERSION": "d" * 40},
    )
    assert result.returncode == expect, result.stderr or result.stdout
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else result


def row(db, sql, params=()):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        found = con.execute(sql, params).fetchone()
        return dict(found) if found else None
    finally:
        con.close()


def raw_capture(db, token, text="owner statement", event_time=None):
    return run(db, "capture-raw", "--stdin", stdin=json.dumps({
        "client_event_id": token, "event_date": "2026-07-22", "event_time": event_time,
        "surface": "ssh", "source": "manual", "raw_text": text,
    }))


def test_log_food_nullable_timing_source_and_legacy_response_shape(db):
    legacy = run(db, "log-food", "sample-stew", "--grams", "100", "--date", "2026-07-20")
    assert set(legacy) == {
        "ok", "logged", "grams", "date", "kcal", "protein_g", "carbs_g", "fat_g",
    }
    old = row(db, "SELECT time,meal_type,source FROM nutrition_log WHERE id=1")
    assert old == {"time": None, "meal_type": None, "source": "manual"}

    current = run(
        db, "log-food", "sample-stew", "--grams", "125", "--date", "2026-07-21",
        "--time", "12:35", "--meal-type", "lunch", "--source", "panel-ui",
    )
    assert current["time"] == "12:35" and current["meal_type"] == "lunch"
    assert current["source"] == "panel-ui"
    stored = row(db, "SELECT time,meal_type,source FROM nutrition_log WHERE id=2")
    assert stored == {"time": "12:35", "meal_type": "lunch", "source": "panel-ui"}


def test_eat_stores_exact_optional_values_and_never_infers_time(db):
    result = run(
        db, "eat", "sample-stew", "--portions", "2", "--date", "2026-07-21",
        "--meal-type", "dinner", "--source", "panel-ui",
    )
    assert result["time"] is None and result["meal_type"] == "dinner"
    stored = row(db, "SELECT time,meal_type,source FROM nutrition_log")
    assert stored == {"time": None, "meal_type": "dinner", "source": "panel-ui"}
    assert row(db, "SELECT portions_remaining FROM meal_inventory")["portions_remaining"] == 2


@pytest.mark.parametrize("args", [
    ("--time", "7:05"), ("--meal-type", "brunch"), ("--source", "scheduler"),
    ("--date", "20260722"),
])
def test_invalid_food_metadata_writes_nothing(args, db):
    result = subprocess.run(
        [sys.executable, str(HEALTH), "log-food", "sample-stew", "--grams", "100", *args],
        capture_output=True, text=True,
        env={**os.environ, "HEALTH_DB": str(db)},
    )
    assert result.returncode != 0
    assert row(db, "SELECT COUNT(*) AS n FROM nutrition_log")["n"] == 0


def test_supplement_time_is_nullable_and_link_is_atomic(db):
    plain = run(
        db, "supplement-log", "Creatine", "--taken", "1", "--dose", "5",
        "--date", "2026-07-22", "--source", "manual",
    )
    assert plain["time"] is None
    assert row(db, "SELECT time_taken,source FROM supplements_log WHERE id=1") == {
        "time_taken": None, "source": "manual",
    }

    cap = raw_capture(db, "supplement-2", "Took creatine at 08:10", "08:10")
    linked = run(
        db, "supplement-log", "1", "--taken", "1", "--dose", "5",
        "--time", "08:10", "--date", "2026-07-22", "--source", "manual",
        "--capture-id", cap["capture_id"],
    )
    assert linked["time"] == "08:10"
    resolution = row(
        db, "SELECT status,target_table,target_row_key FROM raw_capture_resolutions "
            "WHERE capture_id=? ORDER BY id DESC LIMIT 1", (cap["capture_id"],),
    )
    assert resolution == {
        "status": "linked", "target_table": "supplements_log",
        "target_row_key": str(linked["id"]),
    }


def test_pain_onset_validation_and_capture_link_are_atomic(db):
    cap = raw_capture(db, "pain-1", "Left knee pain started around Monday")
    logged = run(
        db, "pain-log", "anterior-knee", "--intensity", "4", "--side", "left",
        "--date", "2026-07-22", "--reported-onset-date", "2026-07-20",
        "--onset-precision", "approximate", "--source", "manual",
        "--capture-id", cap["capture_id"],
    )
    assert logged["logged"]["reported_onset_date"] == "2026-07-20"
    assert row(db, "SELECT reported_onset_date,onset_precision FROM pain_log") == {
        "reported_onset_date": "2026-07-20", "onset_precision": "approximate",
    }

    bad = subprocess.run(
        [sys.executable, str(HEALTH), "pain-log", "anterior-knee", "--intensity", "3",
         "--date", "2026-07-22", "--reported-onset-date", "2026-07-23",
         "--onset-precision", "exact", "--source", "manual"],
        capture_output=True, text=True, env={**os.environ, "HEALTH_DB": str(db)},
    )
    assert bad.returncode != 0
    assert row(db, "SELECT COUNT(*) AS n FROM pain_log")["n"] == 1

    bad_source = subprocess.run(
        [sys.executable, str(HEALTH), "pain-log", "anterior-knee", "--intensity", "3",
         "--date", "2026-07-22", "--source", "scheduler"],
        capture_output=True, text=True, env={**os.environ, "HEALTH_DB": str(db)},
    )
    assert bad_source.returncode != 0
    assert row(db, "SELECT COUNT(*) AS n FROM pain_log")["n"] == 1


@pytest.mark.parametrize("command", ["supplement", "pain"])
def test_conversational_structured_writers_require_raw_capture(db, command):
    args = (["supplement-log", "Creatine", "--date", "2026-07-22",
             "--source", "chat-panel"] if command == "supplement" else
            ["pain-log", "anterior-knee", "--intensity", "3",
             "--date", "2026-07-22", "--source", "chat-panel"])
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args], capture_output=True, text=True,
        env={**os.environ, "HEALTH_DB": str(db)},
    )
    assert result.returncode != 0


def test_capture_link_date_time_consistency_for_supplement_and_pain(db):
    cap = raw_capture(db, "mismatch", event_time="08:10")
    failed = run(
        db, "supplement-log", "Creatine", "--date", "2026-07-22",
        "--time", "08:11", "--source", "manual", "--capture-id", cap["capture_id"],
        expect=2,
    )
    assert failed["error"]["code"] == "validation_error"
    assert row(db, "SELECT COUNT(*) n FROM supplements_log")["n"] == 0


@pytest.mark.parametrize("command", ["supplement", "pain"])
def test_malformed_capture_ids_fail_without_structured_rows(db, command):
    args = (["supplement-log", "Creatine", "--date", "2026-07-22", "--source", "manual",
             "--capture-id", "bad"] if command == "supplement" else
            ["pain-log", "anterior-knee", "--intensity", "3", "--date", "2026-07-22",
             "--source", "manual", "--capture-id", "bad"])
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args], capture_output=True, text=True,
        env={**os.environ, "HEALTH_DB": str(db)},
    )
    assert result.returncode != 0
    table = "supplements_log" if command == "supplement" else "pain_log"
    assert row(db, f"SELECT COUNT(*) n FROM {table}")["n"] == 0


def test_structured_food_supplement_pain_and_medication_invalidate_none(db):
    sys.path.insert(0, str(ROOT))
    from hermes_insights.normalize import identity_key

    cases = [
        ("food_identity", "recipe:sample-stew"),
        ("nutrition_total", None),
        ("supplement", identity_key("supplement", "Creatine")),
        ("pain", identity_key("other", "pain anterior-knee central")),
        ("medication", identity_key("medication", "medication-alias")),
    ]
    for scope, key in cases:
        args = ["capture-completeness-set", "--date", "2026-07-22",
                "--scope", scope, "--state", "complete", "--explicit-none",
                "--source", "manual"]
        if key is not None:
            args += ["--entity-key", key]
        run(db, *args)
    run(db, "log-food", "sample-stew", "--grams", "100", "--date", "2026-07-22",
        "--source", "panel-ui")
    run(db, "supplement-log", "Creatine", "--date", "2026-07-22", "--source", "manual")
    run(db, "pain-log", "anterior-knee", "--intensity", "3", "--date", "2026-07-22")
    run(db, "log", "meds_log", "date=2026-07-22", "drug=medication-alias", "dose_mg=5")
    result = run(db, "capture-completeness", "--all")
    assert len(result["effective"]) == len(cases)
    assert all(item["state"] == "partial" for item in result["effective"])


def test_pain_rejects_noncanonical_observation_date_without_write(db):
    result = subprocess.run(
        [sys.executable, str(HEALTH), "pain-log", "anterior-knee", "--intensity", "3",
         "--date", "20260722"], capture_output=True, text=True,
        env={**os.environ, "HEALTH_DB": str(db)},
    )
    assert result.returncode != 0
    assert row(db, "SELECT COUNT(*) n FROM pain_log")["n"] == 0
