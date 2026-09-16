"""T44 — nutrition-targets engine: known-answer fixtures for every formula +
honest refusals. Same conventions as the other suites (real CLI, temp DB from
SCHEMA.sql). Dates are not frozen, so age-dependent expectations are computed
dynamically with the same formula."""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
TEST_TZ = ZoneInfo("Europe/Paris")


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return json.loads(r.stdout)


def seed(db, sql, rows):
    con = sqlite3.connect(db)
    con.executemany(sql, rows)
    con.commit()
    con.close()


def day(back=0):
    return (datetime.now(TEST_TZ).date() - timedelta(days=back)).isoformat()


DOB = "2001-01-15"
HEIGHT_CM = 165.0
WEIGHT_KG = 62.0


def age():
    """Floor full years DOB -> today (same rule as the engine).
    The fixture date is deliberately fictional."""
    d, t = date.fromisoformat(DOB), datetime.now(TEST_TZ).date()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def bmr(weight_kg=WEIGHT_KG):
    """Mifflin-St Jeor for the fictional female test profile."""
    return 10 * weight_kg + 6.25 * HEIGHT_CM - 5 * age() - 161


def set_profile(db):
    run(db, "profile-set", "height_cm", str(HEIGHT_CM))
    run(db, "profile-set", "sex", "female")
    run(db, "profile-set", "dob", DOB)


def seed_weight(db, kg=WEIGHT_KG, back=0):
    seed(db, "INSERT INTO body_metrics(date, weight_kg) VALUES(?,?)",
         [(day(back), kg)])


def targets(db):
    return run(db, "nutrition-targets")


# ── the maintain / fallback-activity known answer ───────────────────────────

def test_maintain_fallback_known_answer(db):
    set_profile(db)
    seed_weight(db)
    run(db, "phase-set", "maintain")
    t = targets(db)
    assert t["status"] == "ok"
    # profile echo
    assert t["profile"] == {"height_cm": HEIGHT_CM, "sex": "female", "age": age(),
                            "weight_kg": WEIGHT_KG, "weight_date": day(0)}
    assert t["phase"] == {"phase": "maintain", "started": day(0), "set": True}
    # no activity data at all -> fallback, default moderate (1.55)
    a = t["activity"]
    assert a["level"] == "moderate" and a["factor"] == 1.55
    assert a["basis"] == "fallback" and a["days_with_data"] == 0
    assert a["sessions_per_week"] == 0.0 and a["avg_steps"] is None
    # The exact maintenance value follows the fictional profile and activity factor.
    expect_maint = round(bmr() * 1.55)
    assert t["maintenance_kcal"] == expect_maint
    # maintain: configured offset 0; band is ±10%.
    k = t["targets"]["kcal"]
    assert k["target"] == expect_maint
    assert k["band_low"] == round(expect_maint * 0.9)
    assert k["band_high"] == round(expect_maint * 1.1)
    assert "override" not in k and k["source"]
    # protein uses the fictional configured 1.5 g/kg value.
    p = t["targets"]["protein_g"]
    protein = round(1.5 * WEIGHT_KG)
    assert p["target"] == protein and p["per_kg"] == 1.5 and "override" not in p
    f = t["targets"]["fat_g"]
    assert f["target"] == round(0.25 * expect_maint / 9) and f["pct_kcal"] == 25
    assert t["targets"]["carbs_g"]["target"] == round(
        (expect_maint - protein * 4 - f["target"] * 9) / 4)
    # every macro target carries a source string
    for key in ("kcal", "protein_g", "fat_g", "carbs_g", "water_ml"):
        assert t["targets"][key]["source" if key != "water_ml" else "basis"]


def test_phase_unset_treated_as_maintain(db):
    set_profile(db)
    seed_weight(db)
    t = targets(db)
    assert t["status"] == "ok"
    assert t["phase"] == {"phase": "maintain", "started": None, "set": False}
    assert t["targets"]["kcal"]["target"] == round(bmr() * 1.55)  # offset 0
    assert t["targets"]["protein_g"]["per_kg"] == 1.5


# ── phase offsets & protein per-kg switch ───────────────────────────────────

def test_cut_offset_and_protein(db):
    set_profile(db)
    seed_weight(db)
    run(db, "phase-set", "cut")
    t = targets(db)
    assert t["targets"]["kcal"]["target"] == round(bmr() * 1.55) - 300
    assert t["targets"]["protein_g"]["target"] == round(1.8 * WEIGHT_KG)
    assert t["targets"]["protein_g"]["per_kg"] == 1.8


def test_bulk_offset_and_protein(db):
    set_profile(db)
    seed_weight(db)
    run(db, "phase-set", "bulk")
    t = targets(db)
    assert t["targets"]["kcal"]["target"] == round(bmr() * 1.55) + 300
    assert t["targets"]["protein_g"]["target"] == round(1.6 * WEIGHT_KG)
    assert t["targets"]["protein_g"]["per_kg"] == 1.6


def test_latest_weight_wins(db):
    set_profile(db)
    seed_weight(db, 60.0, back=5)
    seed_weight(db, WEIGHT_KG, back=1)
    t = targets(db)
    assert t["profile"]["weight_kg"] == WEIGHT_KG
    assert t["profile"]["weight_date"] == day(1)


# ── activity derived from logged data ───────────────────────────────────────

def _seed_hevy_dates(db, n):
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg,"
             " reps, set_type) VALUES(?,?,?,?,?,'normal')",
         [(day(b), "Bench", 1, 60, 8) for b in range(1, n + 1)])


def test_activity_active_via_sessions_path(db):
    set_profile(db)
    seed_weight(db)
    # 16 distinct workout dates in 28d -> s = 16/4 = 4.0 >= 4 -> active (1.725)
    _seed_hevy_dates(db, 16)
    a = targets(db)["activity"]
    assert a["level"] == "active" and a["factor"] == 1.725
    assert a["basis"] == "logged"
    assert a["sessions_per_week"] == 4.0 and a["days_with_data"] == 16


def test_activity_active_via_steps_path(db):
    set_profile(db)
    seed_weight(db)
    # 12 workout dates -> s = 3.0 ; 14 days of 11000 steps -> st = 11000
    # very_active needs s>=6 or (s>=4 & st>=12000): no.
    # active: (s >= 3 and st >= 10000) -> yes
    _seed_hevy_dates(db, 12)
    seed(db, "INSERT INTO daily_metrics(date, source, steps) VALUES(?,?,?)",
         [(day(b), "apple", 11000) for b in range(1, 15)])
    a = targets(db)["activity"]
    assert a["level"] == "active" and a["factor"] == 1.725
    assert a["basis"] == "logged"
    assert a["sessions_per_week"] == 3.0 and a["avg_steps"] == 11000


def test_activity_moderate_via_steps_only(db):
    set_profile(db)
    seed_weight(db)
    # s = 0, st = 8500 >= 8000 -> moderate (1.55)
    seed(db, "INSERT INTO daily_metrics(date, source, steps) VALUES(?,?,?)",
         [(day(b), "apple", 8500) for b in range(1, 15)])
    a = targets(db)["activity"]
    assert a["level"] == "moderate" and a["factor"] == 1.55
    assert a["basis"] == "logged" and a["avg_steps"] == 8500


def test_activity_sparse_data_uses_fallback(db):
    set_profile(db)
    seed_weight(db)
    run(db, "profile-set", "activity_fallback", "light")
    # only 5 days with any activity data (< 14) -> fallback "light" (1.375)
    seed(db, "INSERT INTO daily_metrics(date, source, steps) VALUES(?,?,?)",
         [(day(b), "apple", 12000) for b in range(1, 6)])
    a = targets(db)["activity"]
    assert a["level"] == "light" and a["factor"] == 1.375
    assert a["basis"] == "fallback" and a["days_with_data"] == 5


def test_steps_per_day_max_across_sources(db):
    set_profile(db)
    seed_weight(db)
    # provenance rule: apple 8000 + fitbit 12000 on the SAME day -> per-day MAX
    # = 12000 (never a silent cross-source GROUP BY / sum / average)
    seed(db, "INSERT INTO daily_metrics(date, source, steps) VALUES(?,?,?)",
         [(day(b), "apple", 8000) for b in range(1, 15)] +
         [(day(b), "fitbit", 12000) for b in range(1, 15)])
    a = targets(db)["activity"]
    assert a["avg_steps"] == 12000
    # s = 0, st = 12000: not active (needs s>=3), moderate via st >= 8000
    assert a["level"] == "moderate"


# ── legacy nutrition_targets overrides ──────────────────────────────────────

def test_protein_override_wins(db):
    set_profile(db)
    seed_weight(db)
    run(db, "nutrition-target-set", "protein_g", "--target", "105")
    t = targets(db)
    p = t["targets"]["protein_g"]
    assert p["target"] == 105 and p["override"] is True
    assert p["per_kg"] == round(105 / WEIGHT_KG, 2)
    k, f = t["targets"]["kcal"]["target"], t["targets"]["fat_g"]["target"]
    assert t["targets"]["carbs_g"]["target"] == round((k - 105 * 4 - f * 9) / 4)
    assert "override" not in t["targets"]["kcal"]


def test_kcal_override_wins(db):
    set_profile(db)
    seed_weight(db)
    run(db, "nutrition-target-set", "kcal", "--target", "2200")
    t = targets(db)
    k = t["targets"]["kcal"]
    assert k["target"] == 2200 and k["override"] is True
    assert k["band_low"] == 1980 and k["band_high"] == 2420
    assert t["targets"]["fat_g"]["target"] == 61
    protein = round(1.5 * WEIGHT_KG)
    assert t["targets"]["carbs_g"]["target"] == round((2200 - protein * 4 - 61 * 9) / 4)
    assert "override" not in t["targets"]["protein_g"]


# ── honest refusals ─────────────────────────────────────────────────────────

def test_insufficient_missing_profile(db):
    seed_weight(db)   # weight alone is not enough
    t = targets(db)
    assert t["status"] == "insufficient_data"
    assert "profile" in t["reason"]
    # never partial fabrication: no macro targets, no maintenance number
    assert "maintenance_kcal" not in t
    assert set(t.get("targets", {})) <= {"water_ml"}
    # ... but the water heuristic only needs weight, so it IS computed
    assert t["targets"]["water_ml"]["target"] == 1850


def test_insufficient_missing_weight_water_falls_back(db):
    set_profile(db)
    t = targets(db)
    assert t["status"] == "insufficient_data"
    assert "weight" in t["reason"]
    w = t["targets"]["water_ml"]
    # existing behavior, clearly labeled: WATER_TARGET_ML constant
    assert w["target"] == 1800 and w["basis"] == "fallback"
    assert w["components"] == {"baseline": None, "exercise": None, "weather": None}


def test_read_paths_do_not_create_tables(db):
    """T46 Section 0: nutrition-targets and scores are READS — they must
    never CREATE TABLE. T45 hit this against the tracked demo DB: every
    /nutrition page view silently added an empty owner_profile table via
    the old _ensure_profile_table call sitting on this read path (and
    nutrient_daily/nutrition_targets via _ensure_nutrient_tables in scores'
    nutrition component). A DB that has never run a writer subcommand
    (profile-set / phase-set / nutrition-target-set / import-cronometer)
    must come out of a read with its table list byte-for-byte unchanged."""
    con = sqlite3.connect(db)
    before = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    run(db, "nutrition-targets")
    run(db, "scores")
    con = sqlite3.connect(db)
    after = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert after == before


# ── water target ────────────────────────────────────────────────────────────

def _ok_db(db, kg=WEIGHT_KG):
    set_profile(db)
    seed_weight(db, kg)


def test_water_baseline_only(db):
    _ok_db(db)
    w = targets(db)["targets"]["water_ml"]
    assert w["target"] == 1850
    assert w["components"] == {"baseline": 1860, "exercise": 0, "weather": 0}
    assert w["day_used"] == day(0)


def test_water_plus_exercise(db):
    _ok_db(db)
    seed(db, "INSERT INTO daily_metrics(date, source, exercise_min) VALUES(?,?,?)",
         [(day(0), "apple", 45)])
    w = targets(db)["targets"]["water_ml"]
    assert w["target"] == 2150
    assert w["components"]["exercise"] == 300 and w["day_used"] == day(0)


def test_water_plus_hot_day(db):
    _ok_db(db)
    seed(db, "INSERT INTO weather(date, temp_max_c) VALUES(?,?)", [(day(0), 28.0)])
    w = targets(db)["targets"]["water_ml"]
    assert w["target"] == 2100 and w["components"]["weather"] == 250


def test_water_cool_day_no_bonus(db):
    _ok_db(db)
    seed(db, "INSERT INTO weather(date, temp_max_c) VALUES(?,?)", [(day(0), 27.9)])
    w = targets(db)["targets"]["water_ml"]
    assert w["target"] == 1850 and w["components"]["weather"] == 0


def test_water_uses_yesterday_when_today_empty(db):
    _ok_db(db)
    seed(db, "INSERT INTO daily_metrics(date, source, exercise_min) VALUES(?,?,?)",
         [(day(1), "apple", 60)])
    w = targets(db)["targets"]["water_ml"]
    assert w["target"] == 2250 and w["day_used"] == day(1)


def test_water_rounds_to_50ml(db):
    _ok_db(db, kg=63.0)
    w = targets(db)["targets"]["water_ml"]
    assert w["target"] == 1900
    assert w["components"]["baseline"] == 1890   # components stay exact


# ── micros ──────────────────────────────────────────────────────────────────

def test_micros_cover_ui_six_with_cronometer_keys(db):
    _ok_db(db)
    micros = {m["nutrient"]: m for m in targets(db)["targets"]["micros"]}
    # the six the UI names, keyed by the names import-cronometer writes into
    # nutrient_daily (fibre has no Cronometer alias -> its own key)
    for key in ("fibre", "vitamin_d", "magnesium", "iron", "zinc", "omega3_epa_dha"):
        assert key in micros, key
    # vitamin D comes from the fictional configuration, not a demographic default.
    vd = micros["vitamin_d"]
    assert vd["target"] == 12.0 and vd["unit"] == "ug"
    assert vd["source"] == "user-configured target"
    # fibre is not in the Cronometer import -> never a fake target
    fib = micros["fibre"]
    assert fib["target"] is None
    assert fib["note"] == "not in Cronometer export"
    # every other micro carries a numeric target + a source string
    for key, m in micros.items():
        if key == "fibre":
            continue
        assert isinstance(m["target"], (int, float)) and m["target"] > 0, key
        assert m["source"], key


def test_micro_keys_match_cronometer_import(db):
    """Every targeted micro key (except the explicitly-null fibre) must be a
    key import-cronometer can write into nutrient_daily (MICRO_SEED keys)."""
    _ok_db(db)
    sys.path.insert(0, str(ROOT))
    import health
    seed_keys = {m["key"] for m in health.MICRO_SEED}
    for m in targets(db)["targets"]["micros"]:
        if m["target"] is None:
            continue
        assert m["nutrient"] in seed_keys, m["nutrient"]
