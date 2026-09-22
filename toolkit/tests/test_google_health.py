"""T53: import-google-health — the Google Health API v4 wearable-sync ingest.
Collector-only (NOT in the bridge allowlists). Payload is the collector's
pre-transformed shape (see the subcommand docstring), NOT raw API JSON.
Same conventions as the other suites (real CLI, temp DB from SCHEMA.sql).
"""
import json
import pathlib
import sqlite3
import subprocess
import sys
import os

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parent
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


def run(db, *args, stdin=None, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       input=stdin, capture_output=True, text=True, timeout=30)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def jout(r):
    return json.loads(r.stdout)


def rows(db, sql):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def seed(db, sql, data):
    con = sqlite3.connect(db)
    con.executemany(sql, data)
    con.commit()
    con.close()


def payload(**day_overrides):
    """One full valid day; overrides patch the day dict."""
    day = {
        "date": "2026-07-17",
        "metrics": {"resting_hr": 64, "hrv_ms": 38.5, "steps": 7350,
                    "active_energy_kcal": 420.0, "respiratory_rate": 15.0,
                    "spo2_pct": 98.0, "sleep_hours": 7.5},
        "sleep": {"time_asleep_hours": 7.5, "time_in_bed_hours": 8.0,
                  "deep_min": 75, "rem_min": 100, "light_min": 275,
                  "awake_min": 30, "awakenings": 2,
                  "bedtime": "22:30", "wake_time": "06:30"},
        "weight_kg": 67.2,
    }
    day.update(day_overrides)
    return {"days": [day]}


def imp(db, pl, expect_ok=True):
    return run(db, "import-google-health", "-",
               stdin=json.dumps(pl), expect_ok=expect_ok)


# ── happy path ──────────────────────────────────────────────────────────────

def test_valid_payload_upserts_all_three_tables(db):
    r = jout(imp(db, payload()))
    assert r["ok"] is True and r["days"] == 1

    dm = rows(db, "SELECT * FROM daily_metrics WHERE date='2026-07-17'")
    assert len(dm) == 1 and dm[0]["source"] == "fitbit"
    assert dm[0]["resting_hr"] == 64 and dm[0]["hrv_ms"] == 38.5
    assert dm[0]["steps"] == 7350 and dm[0]["active_energy_kcal"] == 420.0
    assert dm[0]["respiratory_rate"] == 15.0 and dm[0]["spo2_pct"] == 98.0
    assert dm[0]["sleep_hours"] == 7.5

    sl = rows(db, "SELECT * FROM sleep_log WHERE date='2026-07-17'")
    assert len(sl) == 1 and sl[0]["provenance"] == "fitbit"
    assert sl[0]["deep_min"] == 75 and sl[0]["rem_min"] == 100
    assert sl[0]["light_min"] == 275 and sl[0]["awake_min"] == 30
    assert sl[0]["time_asleep_hours"] == 7.5 and sl[0]["awakenings"] == 2
    assert sl[0]["bedtime"] == "22:30" and sl[0]["wake_time"] == "06:30"

    bm = rows(db, "SELECT * FROM body_metrics WHERE date='2026-07-17'")
    assert len(bm) == 1 and bm[0]["source"] == "fitbit"
    assert bm[0]["weight_kg"] == 67.2


def test_file_input_works_like_stdin(db, tmp_path):
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(payload()))
    r = jout(run(db, "import-google-health", str(p)))
    assert r["ok"] is True
    assert len(rows(db, "SELECT * FROM daily_metrics")) == 1


def test_sparse_day_only_touches_given_fields(db):
    pl = {"days": [{"date": "2026-07-17", "metrics": {"steps": 4000}}]}
    jout(imp(db, pl))
    dm = rows(db, "SELECT * FROM daily_metrics")[0]
    assert dm["steps"] == 4000 and dm["resting_hr"] is None
    assert rows(db, "SELECT * FROM sleep_log") == []
    assert rows(db, "SELECT * FROM body_metrics") == []


def test_rerun_is_idempotent(db):
    imp(db, payload())
    imp(db, payload())
    assert len(rows(db, "SELECT * FROM daily_metrics")) == 1
    assert len(rows(db, "SELECT * FROM sleep_log")) == 1
    assert len(rows(db, "SELECT * FROM body_metrics")) == 1


def test_rerun_updates_fitbit_values_in_place(db):
    imp(db, payload())
    imp(db, payload(metrics={"resting_hr": 54},
                    sleep={"deep_min": 88}, weight_kg=67.0))
    dm = rows(db, "SELECT * FROM daily_metrics")[0]
    assert dm["resting_hr"] == 54 and dm["steps"] == 7350  # untouched field kept
    sl = rows(db, "SELECT * FROM sleep_log")[0]
    assert sl["deep_min"] == 88 and sl["rem_min"] == 100
    bm = rows(db, "SELECT * FROM body_metrics")
    assert len(bm) == 1 and bm[0]["weight_kg"] == 67.0


# ── provenance: fitbit must never touch apple / manual rows ─────────────────

def test_apple_daily_metrics_row_untouched(db):
    seed(db, "INSERT INTO daily_metrics(date, source, resting_hr, steps)"
             " VALUES(?,?,?,?)", [("2026-07-17", "apple", 61, 8000)])
    jout(imp(db, payload()))
    apple = rows(db, "SELECT * FROM daily_metrics WHERE source='apple'")
    assert len(apple) == 1
    assert apple[0]["resting_hr"] == 61 and apple[0]["steps"] == 8000
    fitbit = rows(db, "SELECT * FROM daily_metrics WHERE source='fitbit'")
    assert len(fitbit) == 1 and fitbit[0]["resting_hr"] == 64


def test_non_fitbit_sleep_row_values_preserved_gaps_filled(db):
    # apple-provenance sleep row: existing values must survive, NULL stage
    # columns are gap-filled, provenance stays apple.
    seed(db, "INSERT INTO sleep_log(date, time_asleep_hours, deep_min, provenance)"
             " VALUES(?,?,?,?)", [("2026-07-17", 6.9, 70, "apple")])
    r = jout(imp(db, payload()))
    sl = rows(db, "SELECT * FROM sleep_log")[0]
    assert sl["provenance"] == "apple"
    assert sl["time_asleep_hours"] == 6.9 and sl["deep_min"] == 70  # kept
    assert sl["rem_min"] == 100 and sl["light_min"] == 275          # filled
    assert r["sleep_gap_filled"] == 1 and r["sleep_upserted"] == 0


def test_manual_weight_row_never_overwritten(db):
    seed(db, "INSERT INTO body_metrics(date, weight_kg, source)"
             " VALUES(?,?,?)", [("2026-07-17", 70.0, "manual")])
    imp(db, payload())
    bm = rows(db, "SELECT * FROM body_metrics ORDER BY source")
    assert len(bm) == 2
    assert [b["source"] for b in bm] == ["fitbit", "manual"]
    assert next(b for b in bm if b["source"] == "manual")["weight_kg"] == 70.0


# ── validation: reject unknown loudly, skip implausible with a count ────────

def test_unknown_top_level_key_rejected(db):
    r = imp(db, {"days": [], "extra": 1}, expect_ok=False)
    assert r.returncode != 0 and "extra" in (r.stderr + r.stdout)


def test_unknown_day_key_rejected(db):
    r = imp(db, payload(bogus=1), expect_ok=False)
    assert r.returncode != 0 and "bogus" in (r.stderr + r.stdout)


def test_unknown_metric_key_rejected(db):
    r = imp(db, payload(metrics={"vo2max": 50}), expect_ok=False)
    assert r.returncode != 0 and "vo2max" in (r.stderr + r.stdout)


def test_unknown_sleep_key_rejected(db):
    r = imp(db, payload(sleep={"efficiency": 92}), expect_ok=False)
    assert r.returncode != 0 and "efficiency" in (r.stderr + r.stdout)


def test_bad_date_rejected(db):
    r = imp(db, payload(date="07/17/2026"), expect_ok=False)
    assert r.returncode != 0


def test_non_dict_payload_rejected(db):
    r = run(db, "import-google-health", "-", stdin="[1,2]", expect_ok=False)
    assert r.returncode != 0


def test_out_of_range_values_skipped_and_counted(db):
    pl = payload(metrics={"resting_hr": 500, "steps": 7350},
                 sleep={"deep_min": 3000, "rem_min": 100},
                 weight_kg=1000)
    r = jout(imp(db, pl))
    assert r["skipped_values_out_of_range"] == 3
    dm = rows(db, "SELECT * FROM daily_metrics")[0]
    assert dm["resting_hr"] is None and dm["steps"] == 7350
    sl = rows(db, "SELECT * FROM sleep_log")[0]
    assert sl["deep_min"] is None and sl["rem_min"] == 100
    assert rows(db, "SELECT * FROM body_metrics") == []


def test_nothing_is_ever_deleted(db):
    imp(db, payload())
    before = rows(db, "SELECT COUNT(*) n FROM sleep_log")[0]["n"]
    # a later sparse re-sync must not blank out anything
    jout(imp(db, {"days": [{"date": "2026-07-17", "metrics": {"steps": 1}}]}))
    assert rows(db, "SELECT COUNT(*) n FROM sleep_log")[0]["n"] == before
    sl = rows(db, "SELECT * FROM sleep_log")[0]
    assert sl["deep_min"] == 75  # sleep untouched by a metrics-only day


# ── task-58: hrv_sdnn -> hrv_ms migration (Apple pipeline retired) ──────────

def _legacy_daily_metrics_db(tmp_path):
    """A pre-2026-07-19 DB: daily_metrics rebuilt in its pre-rename shape —
    hrv_sdnn instead of hrv_ms (mirrors the same-named fixture in
    test_readiness.py / test_scores.py)."""
    p = tmp_path / "legacy_hrv.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.execute("DROP TABLE daily_metrics")
    con.execute("""CREATE TABLE "daily_metrics" (
        date TEXT, source TEXT NOT NULL DEFAULT 'apple',
        "resting_hr" REAL, "hrv_sdnn" REAL, "hr_min" REAL, "hr_avg" REAL,
        "hr_max" REAL, "steps" REAL, "active_energy_kcal" REAL,
        "basal_energy_kcal" REAL, "exercise_min" REAL, "distance_km" REAL,
        "flights" REAL, "respiratory_rate" REAL, "spo2_pct" REAL,
        "walking_hr_avg" REAL, "sleep_hours" REAL, PRIMARY KEY (date, source))""")
    con.commit(); con.close()
    return p


def _daily_metrics_columns(p):
    con = sqlite3.connect(p)
    try:
        return [r[1] for r in con.execute("PRAGMA table_info(daily_metrics)")]
    finally:
        con.close()


def test_import_refuses_legacy_hrv_shape_without_ddl(tmp_path):
    """Migration 001, never the importer, owns hrv_ms and its compatibility copy."""
    p = _legacy_daily_metrics_db(tmp_path)
    seed(p, "INSERT INTO daily_metrics(date, source, hrv_sdnn) VALUES(?,?,?)",
         [("2026-07-01", "apple", 40.0), ("2026-07-02", "apple", 42.0)])

    result = imp(p, payload(), expect_ok=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "schema_migration_required"
    assert "hrv_ms" not in _daily_metrics_columns(p)
    assert rows(p, "SELECT date,hrv_sdnn FROM daily_metrics ORDER BY date") == [
        {"date": "2026-07-01", "hrv_sdnn": 40.0},
        {"date": "2026-07-02", "hrv_sdnn": 42.0},
    ]


def test_import_accepts_legacy_hrv_sdnn_payload_key(db):
    """Deploy-window tolerance: an old collector may still send the legacy
    "hrv_sdnn" metrics key before deploy/ghealth-sync is redeployed — it must
    land in hrv_ms (the canonical column), same as a native "hrv_ms" key."""
    r = jout(imp(db, payload(metrics={"hrv_sdnn": 48.2})))
    assert r["ok"] is True
    dm = rows(db, "SELECT * FROM daily_metrics WHERE date='2026-07-17'")[0]
    assert dm["hrv_ms"] == 48.2


# ── surface guards ──────────────────────────────────────────────────────────

def test_not_in_any_bridge_allowlist():
    """Collector-only: the subcommand must appear in NEITHER allowlist —
    the panel/broker drift test stays untouched and green."""
    bridge = (REPO / "app" / "bridge.py").read_text()
    broker = (REPO / "deploy" / "hermes-bridge").read_text()
    assert "import-google-health" not in bridge
    assert "import-google-health" not in broker


def test_no_ddl_needed_import_into_pristine_schema(db):
    """SCHEMA.sql already holds every column the import writes — the
    subcommand must not CREATE/ALTER anything on a pristine DB (T46 rule:
    lazily-migrated columns are allowed only via existing guarded writers)."""
    con = sqlite3.connect(db)
    schema_before = "\n".join(sorted(
        r[0] or "" for r in con.execute("SELECT sql FROM sqlite_master")))
    con.close()
    imp(db, payload())
    con = sqlite3.connect(db)
    schema_after = "\n".join(sorted(
        r[0] or "" for r in con.execute("SELECT sql FROM sqlite_master")))
    con.close()
    assert schema_before == schema_after


def test_invalid_later_day_rolls_back_all_observation_tables(db):
    seed(db, "INSERT INTO daily_metrics(date,source,steps) VALUES(?,?,?)",
         [("2026-07-17", "fitbit", 100)])
    seed(db, "INSERT INTO sleep_log(date,provenance,time_asleep_hours) VALUES(?,?,?)",
         [("2026-07-17", "manual", 6)])
    seed(db, "INSERT INTO body_metrics(date,source,weight_kg) VALUES(?,?,?)",
         [("2026-07-17", "fitbit", 70)])
    prior_metrics = rows(db, "SELECT * FROM daily_metrics")
    prior_sleep = rows(db, "SELECT * FROM sleep_log")
    prior_body = rows(db, "SELECT * FROM body_metrics")
    invalid = payload()
    invalid["days"].append({"date": "2026-07-18", "unexpected": 1})

    result = imp(db, invalid, expect_ok=False)

    assert result.returncode == 1
    assert "unknown day key(s)" in result.stderr
    assert rows(db, "SELECT * FROM daily_metrics") == prior_metrics
    assert rows(db, "SELECT * FROM sleep_log") == prior_sleep
    assert rows(db, "SELECT * FROM body_metrics") == prior_body
