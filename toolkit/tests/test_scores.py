"""Goal scores: known-answer fixtures for every formula + honest refusals.
Same conventions as the other suites (real CLI, temp DB from SCHEMA.sql)."""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
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


def scores(db):
    return run(db, "scores")["scores"]


def test_all_scores_refuse_honestly_on_empty_db(db):
    s = scores(db)
    for name in ("consistency", "sleep", "recovery", "muscle_balance", "water", "nutrition"):
        assert s[name].get("insufficient_data") is True, name
        assert "score" not in s[name], name              # never a fabricated number


def test_consistency_known_answer(db):
    rows = [(day(3), "kept"), (day(2), "partly"), (day(1), "broke"), (day(0), "kept")]
    seed(db, "INSERT INTO commitments_log(date, commitment_id, status) VALUES(?,0,?)", rows)
    s = scores(db)["consistency"]
    # rate = (1 + 0.5 + 0 + 1)/4 = 0.625 -> 62, band warn; streak = 1 (yesterday broke)
    assert s["score"] == 62 and s["band"] == "warn"
    assert s["inputs"]["streak"] == 1
    assert s["inputs"] == {"days_logged": 4, "kept": 2, "partly": 1, "broke": 1,
                           "streak": 1, "window_days": 30}


def test_sleep_hours_only_and_with_quality(db):
    seed(db, "INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)",
         [(day(0), "apple", 6.0)])
    s = scores(db)["sleep"]
    assert s["score"] == 75 and s["band"] == "good"      # 6/8 = 75%
    # sleep_log with quality takes priority: 0.7*(7/8*100) + 0.3*(4/5*100) = 85.25 -> 85
    seed(db, "INSERT INTO sleep_log(date, time_asleep_hours, quality) VALUES(?,?,?)",
         [(day(0), 7.0, 4)])
    s2 = scores(db)["sleep"]
    assert s2["score"] == 85 and s2["inputs"]["source"] == "sleep_log"


def test_recovery_baseline_relative(db):
    rows = [(day(b), "apple", 60, 50.0) for b in range(1, 21)]     # 20 baseline days
    rows.append((day(0), "apple", 57, 55.0))                       # today: better than base
    seed(db, "INSERT INTO daily_metrics(date, source, resting_hr, hrv_ms)"
             " VALUES(?,?,?,?)", rows)
    s = scores(db)["recovery"]
    # rhr: 50 + 500*(60-57)/60 = 75 ; hrv: 50 + 250*(55-50)/50 = 75 ; mean 75
    assert s["score"] == 75 and s["band"] == "good"
    assert s["inputs"]["rhr_baseline"] == 60


def _legacy_daily_metrics_db(tmp_path):
    """A pre-2026-07-19 DB: daily_metrics rebuilt in its pre-rename shape —
    hrv_sdnn instead of hrv_ms (task-58's HRV rename; mirrors test_readiness.py's
    fixture of the same name)."""
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


def test_scores_refuse_legacy_hrv_column_no_ddl(tmp_path):
    """Reads require the explicit Migration 001 HRV compatibility step."""
    p = _legacy_daily_metrics_db(tmp_path)
    rows = [(day(b), "apple", 60, 50.0) for b in range(1, 21)]
    rows.append((day(0), "apple", 57, 55.0))
    seed(p, "INSERT INTO daily_metrics(date, source, resting_hr, hrv_sdnn)"
            " VALUES(?,?,?,?)", rows)
    cols_before = _daily_metrics_columns(p)

    result = subprocess.run(
        [sys.executable, str(HEALTH), "scores"],
        env={**os.environ, "HEALTH_DB": str(p)}, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "schema_migration_required"
    assert _daily_metrics_columns(p) == cols_before
    assert "hrv_ms" not in cols_before


def test_water_known_answer_and_refusal(db):
    s = scores(db)["water"]
    assert s["insufficient_data"] is True
    seed(db, "INSERT INTO intake(date, water_ml) VALUES(?,?)", [(day(0), 900)])
    s2 = scores(db)["water"]
    # no owner_profile/weight logged -> the T44 engine's documented no-weight
    # fallback (WATER_TARGET_ML), same number as before T46
    assert s2["inputs"]["target_ml"] == 1800
    assert s2["score"] == 50 and s2["band"] == "warn"


def test_water_score_uses_computed_target(db):
    """T46: scores.water's target now comes from the T44 compute engine
    (configured per-kg baseline here — no exercise/weather rows logged) instead
    of the flat WATER_TARGET_ML constant, so the dashboard ring and the
    Nutrition page's Water card agree on one number (same helper)."""
    run(db, "profile-set", "height_cm", "165")
    run(db, "profile-set", "sex", "female")
    run(db, "profile-set", "dob", "2000-01-01")
    seed(db, "INSERT INTO body_metrics(date, weight_kg) VALUES(?,?)", [(day(0), 62.0)])
    seed(db, "INSERT INTO intake(date, water_ml) VALUES(?,?)", [(day(0), 1850)])
    s = scores(db)["water"]
    assert s["inputs"]["target_ml"] == 1850
    assert s["score"] == 100 and s["band"] == "good"


def test_muscle_balance_coverage_and_evenness(db):
    # Seed names "chest"/"back" map 1:1 onto the Chest/Back groups in
    # MUSCLE_TO_GROUP — if that map ever remaps them, fix the SEED vocabulary
    # here, not the score.
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)",
         [("Bench", "chest", 1.0), ("Row", "back", 1.0)])
    seed(db, "INSERT INTO routines(routine_name, exercise_title, ex_order, target_sets,"
             " target_reps) VALUES(?,?,?,?,?)",
         [("Push", "Bench", 1, 3, 8), ("Pull", "Row", 1, 3, 8)])
    seed(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
         [("Mon", "Push"), ("Tue", "Pull")])
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,'normal')",
         [(day(0), "Bench", i, 60, 8) for i in range(1, 4)] +
         [(day(1), "Row", i, 60, 8) for i in range(1, 4)])
    s = scores(db)["muscle_balance"]
    # both planned groups covered (3 eff sets each), perfectly even:
    # 60*1.0 + 40*1.0 = 100
    assert s["score"] == 100 and s["inputs"]["groups_covered"] == 2


def test_muscle_balance_uses_seven_group_rollup(db):
    """The score shares the radar's MUSCLE_TO_GROUP map (validated behavior
    2026-07-09): two raw muscle names in the same group count as ONE planned
    group, and an unmapped name counts as none — so the card's
    "X of Y groups" agrees with the 7-axis radar."""
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)",
         [("Hip Thrust", "Glutes", 1.0),
          ("Hip Abduction", "Glute med/abductors", 1.0),
          ("Neck Curl", "neck", 1.0)])
    seed(db, "INSERT INTO routines(routine_name, exercise_title, ex_order, target_sets,"
             " target_reps) VALUES(?,?,?,?,?)",
         [("R", "Hip Thrust", 1, 3, 8), ("R", "Hip Abduction", 2, 3, 8),
          ("R", "Neck Curl", 3, 3, 8)])
    seed(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
         [("Mon", "R")])
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,'normal')",
         [(day(0), "Hip Thrust", i, 80, 8) for i in range(1, 4)] +
         [(day(1), "Hip Abduction", i, 30, 10) for i in range(1, 4)])
    s = scores(db)["muscle_balance"]
    # Glutes is the ONLY planned group (both mapped muscles roll into it;
    # "neck" is unmapped) and it's covered → 60*1.0 + 40*1.0 = 100.
    assert s["inputs"]["groups_planned"] == 1
    assert s["inputs"]["groups_covered"] == 1
    assert s["score"] == 100


def test_muscle_balance_follows_the_authored_submuscle_map(db):
    """v2.8: the score rolls up through the SAME authored-first map as the
    radar (one cited source of truth — the 2026-07-09 no-drift sign-off).
    An authored exercise's coarse Hevy tag is ignored: Hip Thrust is tagged
    only 'Glutes' in exercise_muscles, but the authored map also credits
    Legs (hamstrings 0.4) — so Legs becomes a PLANNED group, covered only
    if its authored volume reaches the threshold (3 sets × 0.4 = 1.2 < 2)."""
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)",
         [("Hip Thrust", "Glutes", 1.0)])
    seed(db, "INSERT INTO exercise_submuscles(exercise_title, muscle_group,"
             " sub_region, weight, iso, confidence, source) VALUES(?,?,?,?,?,?,?)",
         [("Hip Thrust", "Glutes", "glute max", 1.0, 0, "E", "lit:contreras-2015"),
          ("Hip Thrust", "Legs", "hamstrings", 0.4, 0, "E", "lit:contreras-2015")])
    seed(db, "INSERT INTO routines(routine_name, exercise_title, ex_order, target_sets,"
             " target_reps) VALUES(?,?,?,?,?)", [("R", "Hip Thrust", 1, 3, 8)])
    seed(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
         [("Mon", "R")])
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,'normal')",
         [(day(0), "Hip Thrust", i, 80, 8) for i in range(1, 4)])
    s = scores(db)["muscle_balance"]
    assert s["inputs"]["groups_planned"] == 2       # Glutes AND authored Legs
    assert s["inputs"]["groups_covered"] == 1       # 3.0 Glutes ≥ 2; 1.2 Legs < 2


def test_muscle_balance_surfaces_uncounted_exercises(db):
    """A logged exercise in NEITHER map contributes nothing — the score's
    inputs must say so (inputs.unmapped_exercises), or 'needs logged sets
    this week' shows while sets exist and the drop is undiagnosable."""
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,'normal')",
         [(day(0), "Mystery Lift", i, 50, 5) for i in range(1, 4)])
    s = scores(db)["muscle_balance"]
    assert s["insufficient_data"] is True
    assert s["inputs"]["unmapped_exercises"] == ["Mystery Lift"]


def test_muscle_balance_planned_group_counts_without_target_sets(db):
    """routine-set without --sets leaves target_sets NULL; the exercise's
    group must still count as PLANNED (membership by presence, not volume) —
    otherwise an unconfigured exercise silently shrinks the denominator and
    inflates the score. Unmapped names are echoed in inputs.unmapped."""
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)",
         [("Bench", "chest", 1.0), ("Plank", "Core", 1.0), ("Neck Curl", "neck", 1.0)])
    seed(db, "INSERT INTO routines(routine_name, exercise_title, ex_order, target_sets,"
             " target_reps) VALUES(?,?,?,?,?)",
         [("R", "Bench", 1, 3, 8),
          ("R", "Plank", 2, None, None),      # added without --sets
          ("R", "Neck Curl", 3, 3, 12)])      # unmapped muscle
    seed(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
         [("Mon", "R")])
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,'normal')",
         [(day(0), "Bench", i, 60, 8) for i in range(1, 4)])
    s = scores(db)["muscle_balance"]
    # Chest covered; Core planned-but-uncovered (NULL sets ≠ not planned);
    # "neck" is unmapped → not a group, but surfaced for diagnosis.
    assert s["inputs"]["groups_planned"] == 2
    assert s["inputs"]["groups_covered"] == 1
    assert s["inputs"]["unmapped"] == ["neck"]


def test_nutrition_locked_until_sourced_targets(db):
    seed(db, "INSERT INTO nutrition_log(date, food_name, grams, kcal, protein_g)"
             " VALUES(?,?,?,?,?)", [(day(0), "meal", 400, 600, 45)])
    s = scores(db)["nutrition"]
    assert s["insufficient_data"] is True                # locked by design (§9e)


def test_habit_streaks(db):
    seed(db, "INSERT INTO habits_log(date, habit, done) VALUES(?,?,?)",
         [(day(2), "evening-checkin", 1), (day(1), "evening-checkin", 1),
          (day(0), "evening-checkin", 1), (day(0), "protein", 0)])
    res = run(db, "scores")
    by = {h["habit"]: h for h in res["habits"]}
    assert by["evening-checkin"]["streak"] == 3
    assert by["protein"]["streak"] == 0
    assert by["evening-checkin"]["done_7d"] == 3


def test_streaks_must_reach_the_present(db):
    """A run that ended a week ago is not a current streak (review finding)."""
    seed(db, "INSERT INTO commitments_log(date, commitment_id, status) VALUES(?,0,?)",
         [(day(9), "kept"), (day(8), "kept"), (day(7), "kept")])
    s = scores(db)["consistency"]
    assert s["inputs"]["streak"] == 0                     # ended long ago
    seed(db, "INSERT INTO habits_log(date, habit, done) VALUES(?,?,1)",
         [(day(9), "gym"), (day(8), "gym")])
    by = {h["habit"]: h for h in run(db, "scores")["habits"]}
    assert by["gym"]["streak"] == 0


# ── T49: mind + external-care pillar scores ─────────────────────────────────

def test_mind_and_care_insufficient_on_empty_db(db):
    s = scores(db)
    for name in ("mind", "care"):
        assert s[name].get("insufficient_data") is True, name
        assert "score" not in s[name], name


def test_mind_known_answer_all_five_components(db):
    # mood [5,3,4]->mean 4.0->80 ; focus [5,3,1]->mean 3.0->60 ;
    # emotional_regulation [5,3,1]->mean 3.0->60
    seed(db, "INSERT INTO subjective_daily(date, mood, focus, emotional_regulation,"
             " brain_dump) VALUES(?,?,?,?,?)",
         [(day(0), 5, 5, 5, "did stuff today"),
          (day(1), 3, 3, 3, "another entry"),
          (day(2), 4, 1, 1, None)])
    # brain_dump non-empty on day(1) and day(0) only -> 2-day streak (2/14*100=14.3->14)
    # habit_consistency: 3 done of 4 rows -> 75
    seed(db, "INSERT INTO habits_log(date, habit, done) VALUES(?,?,?)",
         [(day(0), "protein", 1), (day(0), "gym", 1),
          (day(1), "protein", 1), (day(1), "gym", 0)])
    s = scores(db)["mind"]
    # mean(80, 60, 60, 14, 75) = 289/5 = 57.8 -> 58, band warn
    assert s["score"] == 58 and s["band"] == "warn"
    assert s["inputs"]["weights"] == "equal"
    assert s["inputs"]["window_days"] == 30
    comp = {c["key"]: c["score"] for c in s["inputs"]["components"]}
    assert comp == {"mood": 80, "focus": 60, "emotional_regulation": 60,
                    "brain_dump_streak": 14, "habit_consistency": 75}


def test_mind_component_omission_only_two_logged(db):
    seed(db, "INSERT INTO subjective_daily(date, mood, focus) VALUES(?,?,?)",
         [(day(0), 4, 2)])
    s = scores(db)["mind"]
    # mood 4.0->80 ; focus 2.0->40 ; mean 60, band warn
    assert s["score"] == 60 and s["band"] == "warn"
    comp = {c["key"] for c in s["inputs"]["components"]}
    assert comp == {"mood", "focus"}


def test_mind_insufficient_with_one_component_names_whats_missing(db):
    seed(db, "INSERT INTO subjective_daily(date, mood) VALUES(?,?)", [(day(0), 4)])
    s = scores(db)["mind"]
    assert s["insufficient_data"] is True
    assert "mood" not in s["reason"].split("missing")[1]   # mood is not "missing"
    for missing in ("focus", "emotional_regulation", "brain_dump_streak", "habit_consistency"):
        assert missing in s["reason"]
    assert len(s["inputs"]["components"]) == 1


def test_mind_brain_dump_streak_cap(db):
    # 20 consecutive days of brain-dumps ending today, capped at 14 for scoring
    seed(db, "INSERT INTO subjective_daily(date, brain_dump) VALUES(?,?)",
         [(day(n), f"entry {n}") for n in range(20)])
    seed(db, "UPDATE subjective_daily SET mood=5 WHERE date=?", [(day(0),)])
    s = scores(db)["mind"]
    bd = next(c for c in s["inputs"]["components"] if c["key"] == "brain_dump_streak")
    assert bd["score"] == 100
    assert "20-day" in bd["basis"]                          # real streak, uncapped in basis
    assert s["score"] == 100 and s["band"] == "good"        # mood 100 too (5/5)


def test_mind_brain_dump_streak_stale_scores_zero(db):
    """T49 review finding: a brain-dump run that stopped 3+ days ago is a
    LAPSED streak, not missing data — the component still appears, scored 0
    (accepted design: honest 0, not insufficient_data)."""
    # 3 consecutive brain-dump days, but the most recent is day(3) -- older
    # than day(1), so the code's `bd_dates[-1] >= days_ago(1)` gate fails and
    # streak stays at its initialized 0 (the for-loop never runs).
    seed(db, "INSERT INTO subjective_daily(date, brain_dump) VALUES(?,?)",
         [(day(3), "entry -3"), (day(4), "entry -4"), (day(5), "entry -5")])
    # a second mind component so the score isn't insufficient_data
    seed(db, "INSERT INTO subjective_daily(date, mood) VALUES(?,?)", [(day(0), 4)])
    s = scores(db)["mind"]
    comp = {c["key"]: c for c in s["inputs"]["components"]}
    assert comp["brain_dump_streak"]["score"] == 0
    assert comp["brain_dump_streak"]["basis"] == \
        "0-day brain-dump streak (14-day cap for scoring)"          # not "3-day"
    assert comp["mood"]["score"] == 80                              # mood 4.0 -> 80
    # mean(mood 80, brain_dump_streak 0) = 40 -> band "warn" (40 is not <40)
    assert s["score"] == 40 and s["band"] == "warn"


def test_mind_brain_dump_streak_ends_yesterday_counts(db):
    """A streak whose most recent entry is YESTERDAY (none logged today yet)
    still counts as live per the code's `>= days_ago(1)` gate."""
    # 3 consecutive brain-dump days ending yesterday: day(3), day(2), day(1).
    seed(db, "INSERT INTO subjective_daily(date, brain_dump) VALUES(?,?)",
         [(day(1), "entry -1"), (day(2), "entry -2"), (day(3), "entry -3")])
    seed(db, "INSERT INTO subjective_daily(date, mood) VALUES(?,?)", [(day(0), 3)])
    s = scores(db)["mind"]
    comp = {c["key"]: c for c in s["inputs"]["components"]}
    # streak = 3 -> 3/14*100 = 21.428... -> round -> 21
    assert comp["brain_dump_streak"]["score"] == 21
    assert comp["brain_dump_streak"]["basis"] == \
        "3-day brain-dump streak (14-day cap for scoring)"
    assert comp["mood"]["score"] == 60                              # mood 3.0 -> 60
    # mean(mood 60, brain_dump_streak 21) = 40.5 -> round-half-to-even -> 40 -> "warn"
    assert s["score"] == 40 and s["band"] == "warn"


def test_care_known_answer_excludes_inactive_products(db):
    seed(db, "INSERT INTO skincare_products(product_id, slot, product_name, active)"
             " VALUES(?,?,?,?)", [(1, "daily", "Sample Care Item", 1), (2, "daily", "Retired Sample Item", 0)])
    # active product: 3 logged slots, 2 used -> expected=3, done=2
    seed(db, "INSERT INTO skincare_log(date, slot, product_id, used) VALUES(?,?,?,?)",
         [(day(0), "skin", 1, 1), (day(1), "skin", 1, 1), (day(2), "skin", 1, 0)])
    # inactive product's rows must NOT inflate either side (T34 lesson: never
    # filter one side of a fraction without the other)
    seed(db, "INSERT INTO skincare_log(date, slot, product_id, used) VALUES(?,?,?,?)",
         [(day(0), "skin", 2, 1), (day(3), "skin", 2, 1)])
    s = scores(db)["care"]
    assert s["inputs"] == {"done": 2, "expected": 3, "window_days": 30}
    assert s["score"] == 67 and s["band"] == "warn"          # 100*2/3 = 66.67 -> 67


def test_care_insufficient_when_only_inactive_products_logged(db):
    seed(db, "INSERT INTO skincare_products(product_id, slot, product_name, active)"
             " VALUES(?,?,?,?)", [(1, "daily", "Retired Sample Item", 0)])
    seed(db, "INSERT INTO skincare_log(date, slot, product_id, used) VALUES(?,?,?,?)",
         [(day(0), "skin", 1, 1)])
    s = scores(db)["care"]
    assert s["insufficient_data"] is True


def test_care_insufficient_data_when_empty(db):
    s = scores(db)["care"]
    assert s["insufficient_data"] is True
    assert "reason" in s


def test_mind_and_care_read_never_create_tables_when_missing(tmp_path):
    """T46 rule: reads never DDL. A DB missing subjective_daily/habits_log/
    skincare_log/skincare_products entirely (an even-older legacy shape than
    SCHEMA.sql ships today) must score both pillars honestly-insufficient
    without creating any of the missing tables."""
    p = tmp_path / "legacy.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    for t in ("subjective_daily", "habits_log", "skincare_log", "skincare_products"):
        con.execute(f"DROP TABLE {t}")
    con.commit()
    before = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    s = scores(p)
    assert s["mind"]["insufficient_data"] is True
    assert s["care"]["insufficient_data"] is True
    con = sqlite3.connect(p)
    after = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert after == before
