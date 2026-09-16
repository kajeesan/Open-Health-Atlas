"""§2a: muscle-volume --by group — the deterministic 7-axis radar rollup.

v2.8 (authored-first): an exercise present in the owner's cited
exercise_submuscles map contributes n_sets × Σ(authored weights per group) —
the SAME source of truth as the drill-down. Exercises NOT in the authored map
FALL BACK to Hevy's coarse exercise_muscles tags (surfaced in
`coarse_fallback`) so new lifts still auto-count until authored. Mobility
drills never count; exercises in neither map are surfaced, never guessed.
Unknown coarse muscle names are surfaced in `unmapped`. Left/right stays
insufficient_data until per-side fitness tests exist (PRD §3a/§3e). Same
conventions as the other suites (real CLI, temp DB).
"""
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

AXES = ["Chest", "Back", "Arms", "Shoulders", "Legs", "Core", "Glutes"]


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


def seed_training(db):
    """A small program mixing the panel's manual muscle names with Hevy-style
    tags, plus one deliberately unknown muscle."""
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)", [
        ("Bench Press (Barbell)", "Chest", 1.0),
        ("Bench Press (Barbell)", "Triceps", 0.5),
        ("Bench Press (Barbell)", "Shoulders", 0.5),
        ("Pull Up (Weighted)", "Back/Lats", 1.0),
        ("Pull Up (Weighted)", "Biceps", 0.5),
        ("Front Squat", "quadriceps", 1.0),          # hevy-style tag, lowercase
        ("Front Squat", "Glutes", 0.5),
        ("Front Squat", "Core", 0.5),
        ("Hip Abduction (Machine)", "Glute med/abductors", 1.0),
        ("Deadlift (Barbell)", "Hamstrings", 1.0),
        ("Deadlift (Barbell)", "Lower back/erectors", 1.0),
        ("Neck Curl", "neck", 1.0),                  # deliberately unmapped
    ])
    seed(db, "INSERT INTO routines(routine_name, exercise_title, ex_order, target_sets, target_reps, target_weight_kg)"
             " VALUES(?,?,?,?,?,?)", [
        ("day A", "Bench Press (Barbell)", 1, 3, 8, 60.0),
        ("day A", "Front Squat", 2, 2, 8, 60.0),
    ])
    seed(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
         [("Mon", "day A")])


def logged_sets(db, sets):
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source)"
             " VALUES(?,?,?,?,?,?, 'hevy')", sets)


def test_by_group_emits_all_seven_axes_zero_filled(db):
    seed_training(db)
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["axes"] == AXES
    assert sorted(outp["combined"]) == sorted(AXES)
    assert sorted(outp["planned"]) == sorted(AXES)
    # nothing logged yet → combined all zero
    assert all(v == 0 for v in outp["combined"].values())


def test_by_group_rolls_muscles_into_groups(db):
    seed_training(db)
    # 3 bench sets + 2 squat sets today; 1 warmup set and 1 stale set ignored.
    logged_sets(db, [
        (day(0), "Bench Press (Barbell)", 1, "normal", 60, 8),
        (day(0), "Bench Press (Barbell)", 2, "normal", 60, 8),
        (day(0), "Bench Press (Barbell)", 3, "normal", 60, 8),
        (day(1), "Front Squat", 1, "normal", 60, 8),
        (day(1), "Front Squat", 2, "normal", 60, 8),
        (day(1), "Front Squat", 3, "warmup", 20, 8),
        (day(30), "Pull Up (Weighted)", 1, "normal", 0, 8),
    ])
    outp = run(db, "muscle-volume", "--by", "group", "--days", "7")
    c = outp["combined"]
    assert c["Chest"] == 3.0          # 3 bench × 1.0
    assert c["Arms"] == 1.5           # 3 bench × 0.5 triceps
    assert c["Shoulders"] == 1.5      # 3 bench × 0.5
    assert c["Legs"] == 2.0           # 2 squats × 1.0 quadriceps (hevy tag)
    assert c["Glutes"] == 1.0         # 2 squats × 0.5
    assert c["Core"] == 1.0           # 2 squats × 0.5
    assert c["Back"] == 0             # pull-ups outside the 7-day window
    # planned: Mon day A = bench 3×(1.0 C + .5 T + .5 S) + squat 2×(1.0 Q + .5 G + .5 Core)
    p = outp["planned"]
    assert p["Chest"] == 3.0 and p["Legs"] == 2.0 and p["Glutes"] == 1.0
    assert outp["planned_distribution_pct"]["Chest"] == 100.0
    assert outp["planned_distribution_pct"]["Legs"] == 66.7
    assert outp["logged_distribution_pct"]["Chest"] == 100.0
    assert outp["logged_distribution_pct"]["Legs"] == 66.7


def test_by_group_surfaces_unmapped_instead_of_guessing(db):
    seed_training(db)
    logged_sets(db, [(day(0), "Neck Curl", 1, "normal", 10, 12)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert "neck" in outp["unmapped"]
    # the unknown muscle's volume landed in NO group
    assert all(v == 0 for v in outp["combined"].values())


def test_by_group_absorbs_owner_name_variants(db):
    """Owner sign-off 2026-07-10: the canonical map absorbs the manual-row
    name variants ("Core/abs" → Core, "Grip/forearms" → Arms) rather than
    renaming the rows, so future rows using them auto-map too."""
    seed_training(db)
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)", [
        ("Plank", "Core/abs", 1.0),
        ("Dead Hang", "Grip/forearms", 1.0),
    ])
    logged_sets(db, [(day(0), "Plank", 1, "normal", 0, 1),
                     (day(0), "Dead Hang", 1, "normal", 0, 1)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["combined"]["Core"] == 1.0
    assert outp["combined"]["Arms"] == 1.0
    assert outp["unmapped"] == []


def test_by_group_left_right_is_honestly_insufficient(db):
    seed_training(db)
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["left_right"]["status"] == "insufficient_data"


def test_by_group_current_strengths_needs_a_completed_quarter(db):
    seed_training(db)
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["current_strengths"]["status"] == "insufficient_data"
    assert outp["current_strengths"]["groups"] == {g: 0.0 for g in AXES}
    assert outp["current_strengths"]["distribution_pct"] == {g: 0.0 for g in AXES}


def test_by_group_current_strengths_shows_partial_first_baseline(db):
    """Before any complete baseline exists, completed bilateral pairs appear
    immediately and untested groups remain explicit zeroes."""
    seed(db, """INSERT INTO fitness_tests(
        date, movement, side, load_kg, reps, voided
        ) VALUES(?,?,?,?,?,0)""", [
        (day(0), "leg-extension", "left", 30.0, 6),
        (day(0), "leg-extension", "right", 30.0, 6),
        (day(0), "hip-extension", "left", 60.0, 6),
        (day(0), "hip-extension", "right", 60.0, 6),
    ])
    outp = run(db, "muscle-volume", "--by", "group")
    current = outp["current_strengths"]
    assert current["status"] == "partial"
    assert current["completed_movements"] == 2
    assert current["total_movements"] == 19
    assert current["groups"]["Legs"] == 36.0
    assert current["groups"]["Glutes"] == 72.0
    assert current["distribution_pct"]["Legs"] == 50.0
    assert current["distribution_pct"]["Glutes"] == 100.0
    assert current["tested_groups"] == ["Legs", "Glutes"]
    assert set(current["untested_groups"]) == set(AXES) - {"Legs", "Glutes"}


def test_by_group_current_strengths_uses_latest_completed_quarter(db):
    """A partial newer quarter must not displace the latest complete battery.
    Every priority isolation-strength movement is 30 kg × 6 reps in Q1, so all
    group means are a known 36 kg e1RM regardless of group test count."""
    unilateral = [
        "leg-extension", "leg-curl", "hip-extension", "hip-flexion", "hip-abduction",
        "calf-raise", "shoulder-er", "shoulder-ir", "lateral-raise",
        "rear-delt-fly", "chest-fly", "cable-row", "lat-pulldown",
        "face-pull", "lower-trap-raise", "biceps-curl", "triceps-extension",
    ]
    bilateral = ["cable-crunch", "back-extension"]
    rows = []
    for movement in unilateral:
        rows.extend([
            ("2026-03-20", movement, "left", 30.0, 6),
            ("2026-03-20", movement, "right", 30.0, 6),
        ])
    rows.extend(("2026-03-20", movement, "bilateral", 30.0, 6)
                for movement in bilateral)
    # Q2 starts, but only one movement is present: Q1 remains the current
    # completed test rather than blending old and new observations.
    rows.append(("2026-04-10", "leg-extension", "left", 100.0, 6))
    seed(db, """INSERT INTO fitness_tests(
        date, movement, side, load_kg, reps, voided
        ) VALUES(?,?,?,?,?,0)""", rows)
    outp = run(db, "muscle-volume", "--by", "group")
    current = outp["current_strengths"]
    assert current["status"] == "complete"
    assert current["quarter"] == "2026-Q1"
    assert current["completed_on"] == "2026-03-20"
    assert current["unit"] == "mean_e1rm_kg"
    assert current["groups"] == {g: 36.0 for g in AXES}
    assert current["distribution_pct"] == {g: 100.0 for g in AXES}


def test_flat_muscle_volume_unchanged(db):
    """The existing per-muscle output (no --by) keeps its shape."""
    seed_training(db)
    outp = run(db, "muscle-volume", "--source", "planned")
    assert "weekly_effective_sets" in outp


def test_null_set_type_rows_count_as_working_sets(db):
    """import-hevy inserts set_type verbatim (None when the CSV lacks it);
    those are working sets and must count — same COALESCE rule the scores
    engine uses. Plain `set_type!='warmup'` would silently drop them."""
    seed_training(db)
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source)"
             " VALUES(?,?,?,?,?,?, 'hevy')",
         [(day(0), "Bench Press (Barbell)", 1, None, 60, 8)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["combined"]["Chest"] == 1.0


def test_null_weight_and_null_muscle_do_not_crash(db):
    """A NULL em.weight or NULL em.muscle row (schema allows both) must not
    kill the whole radar — NULL weight contributes 0, NULL muscle is surfaced
    in unmapped."""
    seed_training(db)
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)", [
        ("Broken Row", "Back/Lats", None),
        ("Broken Row", None, 1.0),
    ])
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, set_type, weight_kg, reps, source)"
             " VALUES(?,?,?,?,?,?, 'hevy')",
         [(day(0), "Broken Row", 1, "normal", 40, 10)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["combined"]["Back"] == 0          # NULL weight → contributes 0
    assert "(missing muscle)" in outp["unmapped"]


def seed_submuscles(db, rows):
    """(title, group, sub_region, weight, iso, confidence, source) rows."""
    seed(db, "INSERT INTO exercise_submuscles(exercise_title, muscle_group,"
             " sub_region, weight, iso, confidence, source)"
             " VALUES(?,?,?,?,?,?,?)", rows)


def test_authored_rollup_overrides_coarse_tags(db):
    """An authored exercise's radar contribution = n_sets × Σ(tiered authored
    weights per group); its coarse Hevy tags are ignored (one cited source of
    truth). Unauthored exercises keep the coarse path and are surfaced in
    coarse_fallback. iso rows COUNT in volume for now (stored for a later
    dynamic/isometric split)."""
    seed_training(db)
    seed_submuscles(db, [
        ("Front Squat", "Legs", "quads", 1.0, 0, "B", "biomech"),
        ("Front Squat", "Glutes", "glute max", 0.66, 0, "B", "biomech"),
        ("Front Squat", "Legs", "adductor magnus", 0.4, 0, "B", "biomech"),
        ("Front Squat", "Core", "erector spinae", 0.4, 1, "B", "biomech"),
    ])
    logged_sets(db, [
        (day(0), "Bench Press (Barbell)", 1, "normal", 60, 8),
        (day(0), "Bench Press (Barbell)", 2, "normal", 60, 8),
        (day(0), "Bench Press (Barbell)", 3, "normal", 60, 8),
        (day(1), "Front Squat", 1, "normal", 60, 8),
        (day(1), "Front Squat", 2, "normal", 60, 8),
        (day(1), "Front Squat", 3, "warmup", 20, 8),   # excluded as before
    ])
    outp = run(db, "muscle-volume", "--by", "group", "--days", "7")
    c = outp["combined"]
    assert c["Legs"] == 2.8            # 2 sets × (1.0 quads + 0.4 adductor)
    assert c["Glutes"] == 1.3          # 2 × 0.66 (NOT the coarse 0.5 tag)
    assert c["Core"] == 0.8            # 2 × 0.4 — the iso row counts
    # bench is not authored → coarse tags still count, and it's surfaced
    assert c["Chest"] == 3.0 and c["Arms"] == 1.5 and c["Shoulders"] == 1.5
    assert "Bench Press (Barbell)" in outp["coarse_fallback"]
    assert "Front Squat" not in outp["coarse_fallback"]
    # planned (Mon day A: bench 3 target sets, squat 2) follows the same rule
    p = outp["planned"]
    assert p["Legs"] == 2.8 and p["Glutes"] == 1.3 and p["Chest"] == 3.0


def test_mobility_drills_never_count_in_radar(db):
    """The authored map excludes mobility drills from strength volume; the
    radar must too, even though Hevy gives them coarse muscle tags — they are
    surfaced in mobility_excluded, not silently dropped."""
    seed_training(db)
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)",
         [("sample mobility drill a", "quadriceps", 1.0)])
    logged_sets(db, [(day(0), "sample mobility drill a", 1, "normal", 0, 1)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["combined"]["Legs"] == 0
    assert outp["mobility_excluded"] == ["sample mobility drill a"]


def test_exercise_in_neither_map_is_surfaced(db):
    """A logged exercise with neither authored rows nor coarse tags used to be
    silently invisible (INNER JOIN dropped it) — it must be surfaced so the
    owner knows volume is uncounted."""
    seed_training(db)
    logged_sets(db, [(day(0), "Mystery Lift", 1, "normal", 50, 5)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert all(v == 0 for v in outp["combined"].values())
    assert outp["unmapped_exercises"] == ["Mystery Lift"]


def test_per_side_authored_rows_do_not_double_count(db):
    """The PK admits a left AND a right row per sub-region (future per-side
    curation). The COMBINED radar must count such a pair as ONE sub-region
    (max across sides), not sum left+right — volume can't tell sides apart,
    and doubling would inflate the axis the moment per-side rows land."""
    seed_training(db)
    seed(db, "INSERT INTO exercise_submuscles(exercise_title, muscle_group,"
             " sub_region, weight, laterality, iso, confidence, source)"
             " VALUES(?,?,?,?,?,?,?,?)", [
        ("Split Squat", "Glutes", "glute med", 0.4, "left", 1, "B", "biomech"),
        ("Split Squat", "Glutes", "glute med", 0.4, "right", 1, "B", "biomech"),
        ("Split Squat", "Glutes", "glute max", 0.66, "bilateral", 0, "B", "biomech"),
    ])
    logged_sets(db, [(day(0), "Split Squat", 1, "normal", 30, 10)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert outp["combined"]["Glutes"] == 1.1   # 0.66 + 0.4 once — not 1.5


def test_coarse_exercise_with_only_unmappable_tags_is_unmatched(db):
    """An exercise whose Hevy tags ALL miss MUSCLE_TO_GROUP contributes zero
    volume — it must land in unmapped_exercises (volume dropped), NOT in
    coarse_fallback (which reads as 'counted coarsely'). Its bad muscle
    names still surface in unmapped."""
    seed_training(db)
    logged_sets(db, [(day(0), "Neck Curl", 1, "normal", 10, 12)])
    outp = run(db, "muscle-volume", "--by", "group")
    assert "Neck Curl" in outp["unmapped_exercises"]
    assert "Neck Curl" not in outp["coarse_fallback"]
    assert "neck" in outp["unmapped"]


def test_by_group_rejects_explicit_source(db):
    """--source picks ONE flat view; --by group returns both. The combination
    is contradictory and must be refused, not silently ignored."""
    seed_training(db)
    r = subprocess.run([sys.executable, str(HEALTH),
                        "muscle-volume", "--source", "logged", "--by", "group"],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "--source" in (r.stderr + r.stdout)
