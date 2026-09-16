"""Insight-engine tests: known planted correlations must be recovered, missing
tables tolerated, the Fitbit/Apple merge rule honored, drug scoping exact, and
the canonical timezone midnight boundary respected. Same conventions as test_health.py
(real CLI against a temp DB), plus direct module import for the clock test.
"""
import importlib.util
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
TEST_TZ = ZoneInfo("Europe/Paris")


def _load_module():
    spec = importlib.util.spec_from_file_location("health_mod", HEALTH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
                       capture_output=True, text=True, timeout=60)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def jout(r):
    return json.loads(r.stdout)


def seed(db, sql, rows):
    con = sqlite3.connect(db)
    con.executemany(sql, rows)
    con.commit()
    con.close()


def local_today():
    return datetime.now(TEST_TZ).date()


def days_back(n):
    """ISO dates for the last n days ending today (canonical timezone), oldest first."""
    t = local_today()
    return [(t - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


# ---------------------------------------------------------------- timezone

def test_midnight_boundary_configured_timezone(monkeypatch):
    """00:30 local is TODAY in canonical timezone even though UTC still says yesterday.
    Regression guard for the accepted canonical-day rule."""
    health = _load_module()

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 7, 6, 22, 30, tzinfo=timezone.utc)  # 00:30 CEST Jul 7
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

    monkeypatch.setattr(health, "datetime", FakeDT)
    assert health.today() == "2026-07-07"


def test_midnight_boundary_winter(monkeypatch):
    """Same rule under CET (UTC+1): 23:30 UTC Jan 15 is 00:30 local Jan 16."""
    health = _load_module()

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc)
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

    monkeypatch.setattr(health, "datetime", FakeDT)
    assert health.today() == "2026-01-16"


def test_bedtime_orders_across_midnight():
    health = _load_module()
    before = health._bedtime_min("23:30")
    after = health._bedtime_min("00:30")
    assert before < after                     # 00:30 is LATER than 23:30
    assert health._bedtime_min("not a time") is None


# ---------------------------------------------------------------- frame merge

def test_dm_merge_primary_with_fallback(db):
    d = local_today().isoformat()
    seed(db, "INSERT INTO daily_metrics(date, source, resting_hr, steps, distance_km)"
             " VALUES(?,?,?,?,?)",
         [(d, "fitbit", 60, None, None),       # fitbit has RHR, lacks steps+distance
          (d, "apple", 65, 9000, 5.2)])
    frame = jout(run(db, "build-daily-frame", "--days", "7"))
    row = [r for r in frame["days"] if r["date"] == d][0]
    assert row["resting_hr"] == 60             # fitbit primary wins
    assert row["steps"] == 9000                # fitbit null -> apple fallback
    assert row["distance_km"] == 5.2           # apple primary for GPS distance
    counts = frame["meta"]["coverage"]["dm_source_counts"]
    assert counts["resting_hr"] == {"fitbit": 1}
    assert counts["steps"] == {"apple": 1}


def test_missing_table_skipped_not_crash(db):
    con = sqlite3.connect(db)
    con.execute("DROP TABLE subjective_daily")
    con.commit(); con.close()
    frame = jout(run(db, "build-daily-frame", "--days", "7"))
    missing = frame["meta"]["coverage"]["missing_tables"]
    assert "subjective_daily" in missing
    assert "commitments_log" not in missing    # migration-owned and present in final schema


def test_medication_scoping_aliases_and_exclusion(db):
    d = local_today().isoformat()
    seed(db, "INSERT INTO meds_log(date, drug, dose_mg, time_taken) VALUES(?,?,?,?)",
         [(d, "medication", 5, "08:00"),
          (d, "medication-alias", 10, "12:30"),
          (d, "another-medication", 50, "08:00")])
    frame = jout(run(db, "build-daily-frame", "--days", "7"))
    row = [r for r in frame["days"] if r["date"] == d][0]
    assert row["medication_dose_mg"] == 15
    assert row["medication_n_doses"] == 2
    assert row["medication_first_dose_min"] == 8 * 60
    assert frame["meta"]["coverage"]["other_medications"] == ["another-medication"]


def _legacy_daily_metrics_db(tmp_path):
    """A pre-2026-07-19 DB: daily_metrics rebuilt in its pre-rename shape —
    hrv_sdnn instead of hrv_ms (task-58's HRV rename; mirrors the same-named
    fixture in test_readiness.py / test_scores.py / test_google_health.py)."""
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


def test_build_daily_frame_tolerates_legacy_hrv_column_no_ddl(tmp_path):
    """T46 rule: READ paths never do DDL. `build-daily-frame` (and therefore
    every subcommand built on _daily_frame: features/correlate/day-signature/
    data-coverage) indexes daily_metrics rows via DM_METRICS, which now names
    the column "hrv_ms" — on a legacy DB (hrv_sdnn only) this must still
    surface the real HRV value, via a Python-side fallback (no SQL/DDL),
    WITHOUT adding the column."""
    p = _legacy_daily_metrics_db(tmp_path)
    d = local_today().isoformat()
    seed(p, "INSERT INTO daily_metrics(date, source, hrv_sdnn) VALUES(?,?,?)",
         [(d, "apple", 48.5)])
    cols_before = _daily_metrics_columns(p)

    frame = jout(run(p, "build-daily-frame", "--days", "7"))

    assert _daily_metrics_columns(p) == cols_before
    assert "hrv_ms" not in cols_before
    row = [r for r in frame["days"] if r["date"] == d][0]
    assert row["hrv_ms"] == 48.5
    assert frame["meta"]["coverage"]["dm_source_counts"]["hrv_ms"] == {"apple": 1}


# ---------------------------------------------------------------- correlate

def _seed_sleep_focus(db, n=30):
    """Plant a clean monotonic sleep->focus (and inverse anxiety) relationship."""
    dm, subj = [], []
    for i, d in enumerate(days_back(n)):
        sleep = 5.0 + (i % 4)                  # 5..8h cycle
        focus = 1 + (i % 4)                    # 1..4, perfectly monotonic w/ sleep
        anxiety = 5 - (i % 4)                  # high when sleep is low
        dm.append((d, "apple", sleep))
        subj.append((d, focus, anxiety))
    seed(db, "INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)", dm)
    seed(db, "INSERT INTO subjective_daily(date, focus, anxiety) VALUES(?,?,?)", subj)


def _pair(res, a, b):
    hits = [p for p in res["pairs"] if {p["a"], p["b"]} == {a, b}]
    return hits[0] if hits else None


def test_correlate_recovers_planted_correlation(db):
    _seed_sleep_focus(db)
    res = jout(run(db, "correlate", "--days", "60", "--min-n", "10", "--top", "500"))
    p = _pair(res, "sleep_hours", "focus")
    assert p and p["n"] == 30
    assert p["spearman"] > 0.9
    assert set(p["pillars"]) == {"sleep", "subjective"}


def test_correlate_flips_symptom_direction(db):
    """anxiety is higher=worse: raw correlation with sleep is negative, but the
    engine reports in wellbeing space so it must come out POSITIVE."""
    _seed_sleep_focus(db)
    res = jout(run(db, "correlate", "--days", "60", "--min-n", "10", "--top", "500"))
    p = _pair(res, "sleep_hours", "anxiety")
    assert p and p["spearman"] > 0.9
    assert "anxiety" in res["meta"]["flipped_in_correlations"]


def test_correlate_suppresses_below_min_n(db):
    dm = [(d, "apple", 7.0) for d in days_back(30)]
    seed(db, "INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)", dm)
    subj = [(d, 3) for d in days_back(5)]      # only 5 focus days: below min-n 10
    seed(db, "INSERT INTO subjective_daily(date, focus) VALUES(?,?)", subj)
    res = jout(run(db, "correlate", "--days", "60", "--min-n", "10", "--top", "500"))
    assert _pair(res, "sleep_hours", "focus") is None
    assert res["suppressed_below_min_n"] > 0


def test_correlate_lag_training_to_next_day_mood(db):
    """Train on day D -> better mood on D+1. Must appear as tr_volume_kg_lag1
    vs mood, tagged as a lag pair."""
    dates = days_back(30)
    hevy, subj = [], []
    for i, d in enumerate(dates):
        trained = i % 2 == 0
        if trained:
            hevy.append((d, "Squat", 1, 100.0, 5, "normal"))
        if i > 0:
            prev_trained = (i - 1) % 2 == 0
            subj.append((d, 5 if prev_trained else 2))
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,?)", hevy)
    seed(db, "INSERT INTO subjective_daily(date, mood) VALUES(?,?)", subj)
    res = jout(run(db, "correlate", "--days", "60", "--min-n", "10", "--top", "500"))
    p = _pair(res, "tr_volume_kg_lag1", "mood")
    assert p and p["spearman"] > 0.9 and p["lag"]


# ---------------------------------------------------------------- features

def test_features_rolling_delta_lag_regime(db):
    dates = days_back(10)
    seed(db, "INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)",
         [(d, "apple", 6.0 + i * 0.5) for i, d in enumerate(dates)])
    seed(db, "INSERT INTO meds_log(date, drug, dose_mg) VALUES(?,?,?)",
         [(d, "medication", 10 if i < 5 else 20) for i, d in enumerate(dates)])
    res = jout(run(db, "features", "--days", "10"))
    last = res["days"][-1]
    assert last["sleep_hours_d1"] == 0.5
    assert abs(last["sleep_hours_r3"] - (9.5 + 10.0 + 10.5) / 3) < 1e-9
    assert last["sleep_hours_lag1"] == 10.0
    assert last["medication_regime"] == "medication:20" and last["medication_regime_day"] == 5
    assert res["days"][4]["medication_regime"] == "medication:10" and res["days"][4]["medication_regime_day"] == 5


# ---------------------------------------------------------------- day signature

def test_day_signature_contrasts_green_vs_red(db):
    dates = days_back(12)
    dm, subj = [], []
    for i, d in enumerate(dates):
        green = i % 2 == 0
        dm.append((d, "apple", 8.0 if green else 5.0))
        subj.append((d, 3 if green else 1))
    seed(db, "INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)", dm)
    seed(db, "INSERT INTO subjective_daily(date, day_rating) VALUES(?,?)", subj)
    res = jout(run(db, "day-signature", "--days", "30"))
    assert res["green_days"] == 6 and res["red_days"] == 6
    sig = [s for s in res["signature"] if s["field"] == "sleep_hours"][0]
    assert sig["green"]["mean"] == 8.0 and sig["red"]["mean"] == 5.0
    assert sig["delta_mean"] == 3.0


def test_day_signature_honest_when_thin(db):
    seed(db, "INSERT INTO subjective_daily(date, day_rating) VALUES(?,?)",
         [(d, 3) for d in days_back(2)])       # 2 green, 0 red
    res = jout(run(db, "day-signature", "--days", "30"))
    assert res["insufficient_data"] is True
    assert res["red_days"] == 0


def test_correlate_rejects_tiny_min_n(db):
    r = run(db, "correlate", "--min-n", "2", expect_ok=False)
    assert r.returncode != 0


# ------------------------------------------------- review-finding regressions

def test_zero_fill_never_fabricates_on_row_bearing_days(db):
    """MAJOR review finding: a workout row with NULL km/kcal must stay null —
    only genuinely rowless days inside the era become 0 (rest days)."""
    dates = days_back(3)
    seed(db, "INSERT INTO workouts(date, type, minutes, km, kcal) VALUES(?,?,?,?,?)",
         [(dates[0], "run", 30, 5.0, 300),
          (dates[2], "run", 45, None, None)])   # 45-min run, unknown km/kcal
    frame = jout(run(db, "build-daily-frame", "--days", "7"))
    by_date = {r["date"]: r for r in frame["days"]}
    assert by_date[dates[2]]["cardio_min"] == 45
    assert "cardio_km" not in by_date[dates[2]]      # null, NOT fabricated 0
    assert by_date[dates[1]]["cardio_min"] == 0      # rowless day inside era


def test_warmup_only_day_still_counts_as_session(db):
    dates = days_back(3)
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,?)",
         [(dates[0], "Squat", 1, 100.0, 5, "normal"),
          (dates[2], "Squat", 1, 40.0, 10, "warmup")])   # showed up, warmed up
    frame = jout(run(db, "build-daily-frame", "--days", "7"))
    by_date = {r["date"]: r for r in frame["days"]}
    assert by_date[dates[2]]["tr_session"] == 1
    assert by_date[dates[2]]["tr_sets"] == 0             # no working sets
    assert by_date[dates[1]]["tr_session"] == 0          # true rest day


def test_log_validates_day_rating_range(db):
    r = run(db, "log", "subjective_daily", "day_rating=5", expect_ok=False)
    assert r.returncode != 0 and "1-3" in (r.stderr + r.stdout)
    run(db, "log", "subjective_daily", "day_rating=3")   # valid value passes


def test_day_signature_rejects_min_days_zero(db):
    r = run(db, "day-signature", "--min-days", "0", expect_ok=False)
    assert r.returncode != 0


def test_bp_brief_matches_configured_aliases(db):
    d = local_today().isoformat()
    seed(db, "INSERT INTO meds_log(date, drug, dose_mg) VALUES(?,?,?)",
         [(d, "medication-alias", 5), (d, "medication-alias-2", 10),
          (d, "other-medication", 50)])
    res = jout(run(db, "bp-brief", "--days", "7"))
    assert res["doses"] == [{"date": d, "dose_total_mg": 15.0}]
    r2 = jout(run(db, "bp-brief", "--days", "7", "--drug", "other-medication"))
    assert r2["doses"][0]["dose_total_mg"] == 50.0


def test_medication_aliases_are_neutral_and_configurable():
    health = _load_module()
    assert "medication" in health.MEDICATION_ALIASES
    assert all("medication" in value for value in health.MEDICATION_ALIASES)


# ---------------------------------------------------------------- coverage

def test_data_coverage_ranks_uncaptured_first(db):
    dates = days_back(30)
    seed(db, "INSERT INTO subjective_daily(date, day_rating) VALUES(?,?)",
         [(d, 3) for d in dates])                          # day_rating fully covered
    seed(db, "INSERT INTO body_metrics(date, weight_kg) VALUES(?,?)",
         [(d, 68.0) for d in dates[::7]])                  # weekly weigh-ins
    res = jout(run(db, "data-coverage", "--days", "30"))
    by = {r["field"]: r for r in res["ranked"]}
    assert by["day_rating"]["coverage_pct"] == 100.0
    assert by["day_rating"]["score"] == 0.0
    assert by["word_kept"]["coverage_pct"] == 0.0
    assert by["word_kept"]["score"] == 5.0                 # (1-0)*w5/e1
    # a weekly cadence hits ITS target: attained, not forever-missing (review)
    assert by["weight_kg"]["attainment_pct"] == 100.0
    assert by["weight_kg"]["score"] == 0.0
    assert res["ranked"][0]["score"] >= res["ranked"][-1]["score"]
