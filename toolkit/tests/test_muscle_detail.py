"""§3b sub-muscle drill-down SCAFFOLD.

The exercises section is live data (same effective-set math as the radar);
the sub-region section must report `pending_source` while the curated map is
empty, and only compute once cited rows exist (never guessed).
"""
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


def seed(db, hevy=(), muscles=(), submuscles=()):
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO hevy_sets(date,exercise_title,set_index,set_type,weight_kg,reps,source)"
        " VALUES(?,?,?,?,?,?, 'hevy')", hevy)
    con.executemany(
        "INSERT INTO exercise_muscles(exercise_title,muscle,weight) VALUES(?,?,?)", muscles)
    if submuscles:
        # the scaffold table is created lazily — mirror it here for seeding
        con.execute("""CREATE TABLE IF NOT EXISTS exercise_submuscles(
            exercise_title TEXT NOT NULL, muscle_group TEXT NOT NULL,
            sub_region TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
            laterality TEXT NOT NULL DEFAULT 'bilateral'
                CHECK(laterality IN ('left','right','bilateral')),
            source TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(exercise_title, sub_region, laterality))""")
        con.executemany(
            "INSERT INTO exercise_submuscles(exercise_title,muscle_group,sub_region,weight,source)"
            " VALUES(?,?,?,?,?)", submuscles)
    con.commit()
    con.close()


def test_group_vocabulary_is_validated(db):
    err = run(db, "muscle-detail", "Biceps", expect_ok=False)
    assert "must be one of" in err and "Glutes" in err


def test_group_is_case_insensitive_and_pending_source(db):
    d = run(db, "muscle-detail", "glutes")
    assert d["group"] == "Glutes"
    assert d["sub_regions"]["status"] == "pending_source"
    assert "never guessed" in d["sub_regions"]["note"]
    assert d["exercises"] == []


def test_exercises_rollup_matches_radar_math(db):
    from datetime import date, timedelta
    today = date.today().isoformat()
    seed(db,
         hevy=[(today, "Hip Thrust (Barbell)", 1, "normal", 100, 8),
               (today, "Hip Thrust (Barbell)", 2, "normal", 100, 8),
               (today, "Hip Thrust (Barbell)", 3, "warmup", 40, 10),   # excluded
               (today, "Front Squat", 1, "normal", 80, 5)],
         muscles=[("Hip Thrust (Barbell)", "glutes", 1.0),
                  ("Front Squat", "glutes", 0.5),
                  ("Front Squat", "quadriceps", 1.0)])
    d = run(db, "muscle-detail", "Glutes")
    ex = {e["exercise"]: e for e in d["exercises"]}
    assert ex["Hip Thrust (Barbell)"]["eff_sets"] == 2.0     # 2 working sets × 1.0
    assert ex["Front Squat"]["eff_sets"] == 0.5              # 1 set × 0.5 secondary
    assert ex["Hip Thrust (Barbell)"]["sets"] == 2
    # quads volume must not leak into the Glutes page
    assert d["exercises"][0]["exercise"] == "Hip Thrust (Barbell)"
    # neither exercise is in the authored map → both are honest coarse
    # fallbacks, flagged per entry AND listed as pending an authored source
    assert all(e["basis"] == "coarse" for e in d["exercises"])
    assert d["pending_source"] == ["Front Squat", "Hip Thrust (Barbell)"]


def test_subregions_compute_only_from_cited_rows(db):
    from datetime import date
    today = date.today().isoformat()
    seed(db,
         hevy=[(today, "Hip Thrust (Barbell)", 1, "normal", 100, 8),
               (today, "Hip Thrust (Barbell)", 2, "normal", 100, 8)],
         muscles=[("Hip Thrust (Barbell)", "glutes", 1.0)],
         submuscles=[("Hip Thrust (Barbell)", "Glutes", "glute max", 1.0, "TEST-CITE 2026"),
                     ("Hip Thrust (Barbell)", "Glutes", "glute med", 0.5, "TEST-CITE 2026")])
    d = run(db, "muscle-detail", "Glutes")
    sub = d["sub_regions"]
    assert sub["status"] == "ok" and "approxim" in sub["note"]
    regions = {r["sub_region"]: r for r in sub["regions"]}
    assert regions["glute max"]["eff_sets"] == 2.0           # 2 sets × 1.0
    assert regions["glute med"]["eff_sets"] == 1.0           # 2 sets × 0.5
    assert regions["glute max"]["sources"] == ["TEST-CITE 2026"]
    # authored exercise → the exercises table uses the SAME authored weights
    # (2 sets × (1.0 + 0.5)) and is not pending a source
    ex = {e["exercise"]: e for e in d["exercises"]}
    assert ex["Hip Thrust (Barbell)"]["basis"] == "authored"
    assert ex["Hip Thrust (Barbell)"]["eff_sets"] == 3.0
    assert d["pending_source"] == []


def test_authored_exercise_counts_even_when_hevy_tags_disagree(db):
    """v2.8 replaces the old map_drift surfacing: an exercise the authored
    map credits to this group now CONTRIBUTES to the group's exercises table
    (authored wins over Hevy's coarse tag), instead of being flagged as
    drift while its volume was hidden."""
    from datetime import date
    today = date.today().isoformat()
    # authored into Glutes, but exercise_muscles maps it ONLY to quadriceps
    seed(db,
         hevy=[(today, "Leg Press", 1, "normal", 150, 10)],
         muscles=[("Leg Press", "quadriceps", 1.0)],
         submuscles=[("Leg Press", "Glutes", "glute max", 0.5, "TEST-CITE 2026")])
    d = run(db, "muscle-detail", "Glutes")
    ex = {e["exercise"]: e for e in d["exercises"]}
    assert ex["Leg Press"]["eff_sets"] == 0.5 and ex["Leg Press"]["basis"] == "authored"
    assert "map_drift" not in d["sub_regions"]               # concept retired


def test_unilateral_rows_stay_per_side(db):
    from datetime import date
    today = date.today().isoformat()
    seed(db,
         hevy=[(today, "Single Leg Hip Thrust", 1, "normal", 40, 10)],
         muscles=[("Single Leg Hip Thrust", "glutes", 1.0)])
    run(db, "muscle-detail", "Glutes")    # lazily creates the scaffold table
    con = sqlite3.connect(db)
    for side in ("left", "right"):        # PK admits one row per side
        con.execute("INSERT INTO exercise_submuscles(exercise_title,muscle_group,"
                    "sub_region,weight,laterality,source) VALUES(?,?,?,?,?,?)",
                    ("Single Leg Hip Thrust", "Glutes", "glute med", 1.0, side, "TEST-CITE"))
    con.commit(); con.close()
    sub = run(db, "muscle-detail", "Glutes")["sub_regions"]
    sides = sorted(r["laterality"] for r in sub["regions"])
    assert sides == ["left", "right"]                        # not collapsed


def test_null_weight_coarse_exercise_stays_visible(db):
    """A Hevy exercise_muscles row with NULL weight contributes 0 effective
    sets but the exercise must STAY visible on the group page (sets + last
    date are the only signal that logged volume has an unknown weight) —
    dropping it entirely would hide uncounted volume."""
    from datetime import date
    today = date.today().isoformat()
    seed(db,
         hevy=[(today, "Broken Row", 1, "normal", 40, 10)],
         muscles=[("Broken Row", "back", None)])
    d = run(db, "muscle-detail", "Back")
    ex = {e["exercise"]: e for e in d["exercises"]}
    assert ex["Broken Row"]["eff_sets"] == 0.0
    assert ex["Broken Row"]["sets"] == 1 and ex["Broken Row"]["basis"] == "coarse"
    assert d["pending_source"] == ["Broken Row"]


def test_stale_mobility_authored_rows_never_count(db):
    """Mobility drills are excluded from the exercises table and the radar;
    stale authored rows for a mobility title (seeded before the drill was
    reclassified) must not leak volume into the sub_regions section either —
    the two sections of one payload must agree."""
    from datetime import date
    today = date.today().isoformat()
    seed(db,
         hevy=[(today, "sample mobility drill a", 1, "normal", 0, 1)],
         muscles=[("sample mobility drill a", "quadriceps", 1.0)],
         submuscles=[("sample mobility drill a", "Legs", "rectus femoris", 1.0, "TEST-CITE")])
    d = run(db, "muscle-detail", "Legs")
    assert d["exercises"] == []
    rf = [r for r in d["sub_regions"]["regions"] if r["sub_region"] == "rectus femoris"]
    assert rf and rf[0]["eff_sets"] == 0.0 and rf[0]["exercises"] == []


def test_uncited_submuscle_row_is_impossible(db):
    run(db, "muscle-detail", "Glutes")           # creates the scaffold table
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):  # source is NOT NULL
        con.execute("INSERT INTO exercise_submuscles(exercise_title,muscle_group,sub_region)"
                    " VALUES('X','Glutes','y')")


def _seed_strength_history(db, dates):
    """Two differently-scaled lifts plus a repeated direct test."""
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO exercise_submuscles(exercise_title,muscle_group,sub_region,weight,source,confidence)"
        " VALUES(?,?,?,?,?,?)",
        [
            ("Heavy Hip Extension", "Glutes", "glute max", 1.0, "biomech", "B"),
            ("Hip Abduction (Machine)", "Glutes", "glute med", 1.0,
             "lit:distefano-2009", "E"),
            ("Hip Abduction (Cable)", "Glutes", "glute med", 1.0,
             "biomech", "B"),
        ],
    )
    for i, day in enumerate(dates):
        # A much heavier raw load stays flat. The lighter exercise improves;
        # cross-exercise kilograms must never make the heavy lift rank higher.
        con.execute(
            "INSERT INTO hevy_sets(date,exercise_title,set_index,set_type,weight_kg,reps,source)"
            " VALUES(?,?,1,'normal',?,8,'test')",
            (day, "Heavy Hip Extension", 200.0),
        )
        con.execute(
            "INSERT INTO hevy_sets(date,exercise_title,set_index,set_type,weight_kg,reps,source)"
            " VALUES(?,?,1,'normal',?,12,'test')",
            (day, "Hip Abduction (Machine)", 20.0 + i * 2.0),
        )
        con.execute(
            "INSERT INTO fitness_tests(date,movement,side,load_kg,reps,source)"
            " VALUES(?,'hip-abduction','bilateral',?,8,'test')",
            (day, 24.0 + i * 1.5),
        )
    con.commit()
    con.close()


def test_strength_theory_hidden_below_recurrence_gate(db):
    from datetime import date, timedelta
    dates = [(date.today() - timedelta(days=i * 7)).isoformat() for i in range(4)]
    _seed_strength_history(db, dates)
    theory = run(db, "muscle-detail", "Glutes")["strength_theories"]
    assert theory["status"] == "insufficient_evidence"
    assert theory["theories"] == []
    assert theory["minimum_observations"] == 5


def test_strength_theory_separates_exposure_performance_and_direct_tests(db):
    from datetime import date, timedelta
    dates = [(date.today() - timedelta(days=(5 - i) * 7)).isoformat() for i in range(6)]
    _seed_strength_history(db, dates)
    theory = run(db, "muscle-detail", "Glutes")["strength_theories"]
    assert theory["status"] == "theory"
    ranked = {row["sub_region"]: row for row in theory["theories"]}
    assert ranked["glute med"]["relative_strength_score"] > ranked["glute max"]["relative_strength_score"]
    assert "normalized_performance" in ranked["glute max"]["signals"]
    assert "direct_fitness_tests" in ranked["glute med"]["signals"]
    assert ranked["glute med"]["direct_test_evidence"]
    assert theory["imbalance"]["status"] == "possible_imbalance"
    assert "raw loads across exercises are never compared" in theory["method"]
