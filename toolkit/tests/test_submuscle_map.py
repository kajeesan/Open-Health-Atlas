"""Authored-map importer: seeds exercise_submuscles from a cited map. Offline,
collector-only (NOT in the bridge allowlists).

Determinism rules under test:
- DRY-RUN IS THE DEFAULT: without --seed nothing is ever written; the report
  shows what will seed AND which exercisedb:* rows will be retired.
- Every row's source is a known citation key (lit:*/biomech) — unknown keys,
  unknown groups, duplicate sub-regions and malformed rows are hard errors,
  never guessed around.
- TIERED rows (v2.8): '<weight> [E|B] [iso]' — any decimal 0<w<=1.0; the
  [E]/[B] confidence flag is REQUIRED per row ([E] = EMG-anchored, [B] =
  biomechanical estimate); `iso` marks isometric contribution. `approx` is
  DERIVED: [B] rows are mechanism-based emphasis, [E] rows are not — the
  legacy '· approx' suffix is refused, not silently accepted.
- Mobility drills never seed volume rows.
- --seed replaces each mapped exercise's rows wholesale, then retires ALL
  exercisedb:* rows — the end state has zero ExerciseDB-sourced rows.
"""
import json
import pathlib
import sqlite3
import subprocess
import sys
import os

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
SYNTHETIC_MAP = ROOT / "tests" / "fixtures" / "submuscle_map.synthetic.md"
PUBLIC_MAP = ROOT.parent / "docs" / "authored-submuscle-map.md"
SYNTHETIC_TITLES = [
    "Hip Abduction (Machine)", "Deadlift (Barbell)", "Plank", "sample mobility drill a",
]


def make_db(tmp_path, titles):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO exercise_muscles(exercise_title, muscle, weight, source)"
        " VALUES(?, 'chest', 1.0, 'hevy')", [(t,) for t in titles])
    con.commit()
    con.close()
    return p


@pytest.fixture()
def db(tmp_path):
    """Small DB: a few candidates + pre-existing exercisedb rows to retire."""
    p = make_db(tmp_path, ["Hip Abduction (Machine)", "Plank",
                           "Deadlift (Barbell)", "Bent Over Row (Barbell)",
                           "sample mobility drill a"])
    con = sqlite3.connect(p)
    con.executemany(
        "INSERT INTO exercise_submuscles(exercise_title, muscle_group,"
        " sub_region, weight, laterality, source) VALUES(?,?,?,?,'bilateral',?)",
        [("Plank", "Core", "rectus abdominis", 1.0, "exercisedb:exr_plank"),
         ("Plank", "Glutes", "tfl (abductor)", 0.5, "exercisedb:exr_plank"),
         ("Old Gone Exercise", "Arms", "biceps", 1.0, "exercisedb:exr_old")])
    con.commit()
    con.close()
    return p


def run(db, *args, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True, timeout=30)
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


FIXTURE_MD = """# fixture map

## Map

### Hip Abduction (Machine)  — [DIST09][BOR11]
- Glutes · glute med · 1.0 [E]
- Glutes · glute min · 0.4 [E]
- Glutes · TFL · 0.4 [B]

### Deadlift (Barbell) — conventional  — [BIOMECH]
- Core · erector spinae · 1.0 [B]
- Glutes · glute max · 1.0 [B]
- Legs · hamstrings · 0.66 [B]
- Arms · forearm flexors · 0.4 [B] iso  (grip)

### Plank (front plank)  — [BIOMECH]
- Core · rectus abdominis · 1.0 [B] iso
- Core · transverse abdominis · 1.0 [B] iso  (deep anti-extension)
- *Unlike the old row, this does NOT credit TFL.*

### Ghost Exercise  — [BIOMECH]
- Arms · biceps · 1.0 [B]
"""


def write_map(tmp_path, text=FIXTURE_MD, name="map.md"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_dry_run_is_default_shows_seed_and_retire_plans(db, tmp_path):
    o = jout(run(db, "import-submuscle-map", write_map(tmp_path)))
    assert o["dry_run"] is True
    by_ex = {p["exercise"]: p for p in o["proposed"]}
    # multi-citation source, joined in header order
    hip = by_ex["Hip Abduction (Machine)"]
    assert hip["source"] == "lit:distefano-2009+lit:boren-2011"
    subs = {r["sub_region"]: r for r in hip["rows"]}
    # tiered weights parse as written, not rounded to the old 1.0/0.5 pair
    assert subs["glute med"]["weight"] == 1.0
    assert subs["glute min"]["weight"] == 0.4
    # [E]/[B] confidence is carried per row; approx is DERIVED from it
    assert subs["glute med"]["confidence"] == "E" and subs["glute med"]["approx"] is False
    assert subs["tfl"]["confidence"] == "B" and subs["tfl"]["approx"] is True
    # '— conventional' suffix dropped; note-parenthetical dropped from weight
    dl = by_ex["Deadlift (Barbell)"]
    assert dl["source"] == "biomech"
    dsubs = {r["sub_region"]: r for r in dl["rows"]}
    assert dsubs["hamstrings"]["weight"] == 0.66
    # iso flag parsed (with or without a trailing note), stored per row
    assert dsubs["forearm flexors"]["iso"] is True
    assert dsubs["erector spinae"]["iso"] is False
    # parenthetical-stripped title resolution + commentary line skipped
    plank = by_ex["Plank"]
    assert {r["sub_region"] for r in plank["rows"]} == {"rectus abdominis",
                                                        "transverse abdominis"}
    assert all(r["iso"] for r in plank["rows"])
    assert all(r["laterality"] == "bilateral" for p in o["proposed"] for r in p["rows"])
    # per-exercise + total iso/confidence counts for the owner's review
    assert hip["counts"] == {"rows": 3, "iso": 0, "E": 2, "B": 1}
    assert dl["counts"] == {"rows": 4, "iso": 1, "E": 0, "B": 4}
    assert o["totals"] == {"exercises": 3, "rows": 9, "iso": 3, "E": 2, "B": 7}
    # wholesale-replace honesty: current rows per exercise are reported
    assert by_ex["Plank"]["replaces_rows"] == 2
    assert hip["replaces_rows"] == 0
    # authored-wins honesty: coarse Hevy groups the authored rows no longer
    # credit are listed per exercise (fixture tags everything 'chest')
    assert hip["coarse_groups_dropped"] == ["Chest"]
    # the retire plan lists every exercisedb:* row with full detail
    assert o["will_retire"]["count"] == 3
    retire_ex = {r["exercise_title"] for r in o["will_retire"]["rows"]}
    assert retire_ex == {"Plank", "Old Gone Exercise"}
    # map titles with no DB candidate are surfaced, never seeded
    assert o["map_titles_not_in_db"] == ["Ghost Exercise"]
    # candidates without authored rows are surfaced; mobility listed separately
    assert "Bent Over Row (Barbell)" in o["unmapped_candidates"]
    assert o["mobility_excluded"] == ["sample mobility drill a"]
    assert "sample mobility drill a" not in o["unmapped_candidates"]
    # and NOTHING was written
    got = rows(db, "SELECT * FROM exercise_submuscles")
    assert len(got) == 3 and all(g["source"].startswith("exercisedb:") for g in got)


def test_seed_replaces_and_retires_all_exercisedb_rows(db, tmp_path):
    f = write_map(tmp_path)
    o = jout(run(db, "import-submuscle-map", f, "--seed"))
    assert o["dry_run"] is False
    assert o["seeded_exercises"] == 3 and o["seeded_rows"] == 9
    assert o["retired_exercisedb_rows"] == 3
    # end state: ZERO exercisedb rows anywhere
    assert rows(db, "SELECT * FROM exercise_submuscles"
                    " WHERE source LIKE 'exercisedb:%'") == []
    plank = rows(db, "SELECT * FROM exercise_submuscles"
                     " WHERE exercise_title='Plank' ORDER BY sub_region")
    assert [p["sub_region"] for p in plank] == ["rectus abdominis",
                                                "transverse abdominis"]
    assert all(p["source"] == "biomech" for p in plank)
    assert all(p["iso"] == 1 and p["confidence"] == "B" and p["approx"] == 1
               for p in plank)
    # [E] rows land approx=0 (EMG-anchored), [B] rows approx=1 (mechanism)
    hip = {r["sub_region"]: r for r in rows(
        db, "SELECT * FROM exercise_submuscles"
            " WHERE exercise_title='Hip Abduction (Machine)'")}
    assert hip["glute med"]["confidence"] == "E" and hip["glute med"]["approx"] == 0
    assert hip["glute min"]["weight"] == 0.4 and hip["glute min"]["iso"] == 0
    assert hip["tfl"]["confidence"] == "B" and hip["tfl"]["approx"] == 1
    # re-run: idempotent (full replace per exercise, no duplicates)
    jout(run(db, "import-submuscle-map", f, "--seed"))
    assert len(rows(db, "SELECT * FROM exercise_submuscles"
                        " WHERE exercise_title='Plank'")) == 2


def test_unknown_citation_key_is_refused(db, tmp_path):
    bad = FIXTURE_MD.replace("[DIST09][BOR11]", "[WIKIPEDIA]")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "WIKIPEDIA" in (r.stderr + r.stdout)


def test_unknown_group_is_refused(db, tmp_path):
    bad = FIXTURE_MD.replace("- Glutes · glute med · 1.0 [E]",
                             "- Forearms · glute med · 1.0 [E]")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "Forearms" in (r.stderr + r.stdout)


def test_duplicate_subregion_is_refused(db, tmp_path):
    bad = FIXTURE_MD.replace("- Glutes · glute min · 0.4 [E]",
                             "- Glutes · glute med · 0.4 [E]")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0


def test_malformed_row_is_refused_not_skipped(db, tmp_path):
    bad = FIXTURE_MD.replace("- Glutes · glute min · 0.4 [E]",
                             "- Glutes glute min 0.4 [E]")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0


def test_missing_confidence_flag_is_refused(db, tmp_path):
    """Every row must declare [E] or [B] — an unflagged weight (the pre-v2.8
    format) is a hard error, not a silent default."""
    bad = FIXTURE_MD.replace("- Glutes · glute min · 0.4 [E]",
                             "- Glutes · glute min · 0.4")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "[E]" in (r.stderr + r.stdout)


def test_unknown_confidence_flag_is_refused(db, tmp_path):
    bad = FIXTURE_MD.replace("- Glutes · glute min · 0.4 [E]",
                             "- Glutes · glute min · 0.4 [X]")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0


def test_unknown_token_after_flags_is_refused(db, tmp_path):
    """A typo'd iso ('isoz') must not be silently dropped."""
    bad = FIXTURE_MD.replace("- Arms · forearm flexors · 0.4 [B] iso  (grip)",
                             "- Arms · forearm flexors · 0.4 [B] isoz  (grip)")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "isoz" in (r.stderr + r.stdout)


def test_legacy_approx_suffix_is_refused(db, tmp_path):
    """approx is now DERIVED from [B]; the old '· approx' suffix must be
    refused loudly so a stale-format file can't half-import."""
    bad = FIXTURE_MD.replace("- Glutes · TFL · 0.4 [B]",
                             "- Glutes · TFL · 0.4 [B] · approx")
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "approx" in (r.stderr + r.stdout)


def test_mobility_exercise_with_rows_is_refused(db, tmp_path):
    bad = FIXTURE_MD + "\n### sample mobility drill a  — [BIOMECH]\n- Legs · quads · 0.4 [B]\n"
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "mobility" in (r.stderr + r.stdout).lower()


def test_two_sections_resolving_to_same_title_refused(db, tmp_path):
    """'Plank (front plank)' and a hypothetical 'Plank (side)' both resolve
    to DB 'Plank' — silent last-wins would hide half the authored map."""
    bad = FIXTURE_MD + "\n### Plank (side)  — [BIOMECH]\n- Core · obliques · 1.0 [B]\n"
    r = run(db, "import-submuscle-map", write_map(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "Plank" in (r.stderr + r.stdout)


def test_pre_tiered_table_requires_explicit_migration(tmp_path):
    """The importer fails closed and never adds Migration 001 columns."""
    p = make_db(tmp_path, ["Hip Abduction (Machine)"])
    con = sqlite3.connect(p)
    con.execute("DROP TABLE exercise_submuscles")
    con.execute("""CREATE TABLE exercise_submuscles(
        exercise_title TEXT NOT NULL, muscle_group TEXT NOT NULL,
        sub_region TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
        laterality TEXT NOT NULL DEFAULT 'bilateral'
            CHECK(laterality IN ('left','right','bilateral')),
        source TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY(exercise_title, sub_region, laterality))""")
    con.commit(); con.close()
    result = run(p, "import-submuscle-map", write_map(tmp_path), "--seed", expect_ok=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "schema_migration_required"
    con = sqlite3.connect(p)
    cols = {row[1] for row in con.execute("PRAGMA table_info(exercise_submuscles)")}
    con.close()
    assert {"approx", "iso", "confidence"}.isdisjoint(cols)


def test_confidence_derived_approx_surfaces_in_muscle_detail(db, tmp_path):
    """End-to-end: [B] rows import as approx=1 and muscle-detail marks the
    region '≈'; [E] rows stay approx=0 — the EMG-vs-estimate distinction
    must survive the whole import→drill-down path."""
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO hevy_sets(date, exercise_title, set_type, weight_kg, reps)"
        " VALUES(date('now'),?,?,?,?)",
        [("Hip Abduction (Machine)", "normal", 30.0, 12)] * 2)
    con.execute("UPDATE exercise_muscles SET muscle='glutes'"
                " WHERE exercise_title='Hip Abduction (Machine)'")
    con.commit(); con.close()
    jout(run(db, "import-submuscle-map", write_map(tmp_path), "--seed"))
    o = jout(run(db, "muscle-detail", "Glutes"))
    regions = {r["sub_region"]: r for r in o["sub_regions"]["regions"]}
    assert regions["tfl"]["approx"] is True             # [B] → mechanism-based
    assert regions["glute med"]["approx"] is False      # [E] → EMG-anchored
    assert regions["glute med"]["sources"] == ["lit:distefano-2009+lit:boren-2011"]
    assert regions["glute med"]["eff_sets"] == 2.0


def test_bundled_synthetic_map_parses_and_covers_candidates(tmp_path):
    db = make_db(tmp_path, SYNTHETIC_TITLES)
    o = jout(run(db, "import-submuscle-map", str(SYNTHETIC_MAP)))
    assert len(o["proposed"]) == 3
    assert o["map_titles_not_in_db"] == ["Ghost Exercise"]
    assert o["unmapped_candidates"] == []
    assert o["mobility_excluded"] == ["sample mobility drill a"]
    for p in o["proposed"]:
        assert p["rows"], f"{p['exercise']} has no rows"
        for r in p["rows"]:
            assert r["muscle_group"] in ("Chest", "Back", "Arms", "Shoulders",
                                         "Legs", "Core", "Glutes")
            # the bundled fixture uses the supported discrete example tiers
            assert r["weight"] in (1.0, 0.66, 0.4, 0.2), \
                f"{p['exercise']}/{r['sub_region']}: {r['weight']}"
            assert r["confidence"] in ("E", "B")
    assert o["totals"]["exercises"] == 3
    assert o["totals"]["rows"] == sum(p["counts"]["rows"] for p in o["proposed"])
    assert o["totals"]["E"] + o["totals"]["B"] == o["totals"]["rows"]
    assert o["totals"]["iso"] > 0          # the map tags isometric work
    # spot-check the authored Plank replacement fixes the TFL/Glutes drift
    plank = next(p for p in o["proposed"] if p["exercise"] == "Plank")
    assert all(r["muscle_group"] != "Glutes" for r in plank["rows"])
    hip = next(p for p in o["proposed"] if p["exercise"] == "Hip Abduction (Machine)")
    assert any(r["confidence"] == "E" for r in hip["rows"])


def test_public_map_seeds_fresh_database_without_logged_exercises(tmp_path):
    """Fresh installs carry public configuration before user data exists."""
    db = make_db(tmp_path, [])
    o = jout(run(db, "import-submuscle-map", str(PUBLIC_MAP), "--seed", "--seed-all"))
    assert o["seed_all"] is True
    assert o["seeded_exercises"] == 9
    assert o["seeded_rows"] == 42
    assert len(o["seeded_without_candidate"]) == 9
    titles = {r["exercise_title"] for r in rows(
        db, "SELECT DISTINCT exercise_title FROM exercise_submuscles")}
    assert titles == {
        "Band Pull-Apart", "Dumbbell Floor Press", "Dumbbell Hinge",
        "Dumbbell Shoulder Press", "Goblet Squat", "Incline Push-Up",
        "Lat Pulldown", "Seated Cable Row", "Step-Up",
    }


def test_import_is_not_bridge_allowlisted():
    """Seeding stays collector-only: neither the broker nor the panel client
    may ever run it."""
    import types
    broker = types.ModuleType("broker")
    src = (ROOT.parent / "deploy" / "hermes-bridge").read_text()
    exec(compile(src, "hermes-bridge", "exec"), broker.__dict__)
    assert "import-submuscle-map" not in broker.ALLOWED
    assert "import-submuscle-map" not in (ROOT.parent / "app" / "bridge.py").read_text()
