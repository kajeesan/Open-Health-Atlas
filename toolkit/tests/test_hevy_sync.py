"""§5a/§5b: Hevy API sync — import-hevy-json / import-hevy-templates /
import-hevy-routines. Collector-only commands (NOT in the bridge allowlists);
JSON shapes match the Hevy OpenAPI spec (Workout/Exercise/Set,
ExerciseTemplate incl. the MuscleGroup enum, Routine). Same conventions as
the other suites (real CLI, temp DB from SCHEMA.sql).
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
                       env={
                           **os.environ,
                           "HEALTH_DB": str(db),
                           "HERMES_HEVY_QUARTERLY_CONFIG": str(
                               ROOT.parent / "config" / "hevy-quarterly.example.json"
                           ),
                       },
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


def seed(db, sql, data):
    con = sqlite3.connect(db)
    con.executemany(sql, data)
    con.commit()
    con.close()


def write_json(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(json.dumps(payload))
    return str(p)


WORKOUTS = {"workouts": [
    {"id": "w1", "title": "fullbody A", "routine_id": "r1", "description": None,
     "start_time": "2026-07-06T16:00:00Z", "end_time": "2026-07-06T17:00:00Z",
     "exercises": [
         {"index": 0, "title": "Front Squat", "notes": "felt strong",
          "exercise_template_id": "t1", "supersets_id": None,
          "sets": [
              {"index": 0, "type": "warmup", "weight_kg": 40, "reps": 8,
               "distance_meters": None, "duration_seconds": None, "rpe": None},
              {"index": 1, "type": "normal", "weight_kg": 62.5, "reps": 8,
               "distance_meters": None, "duration_seconds": None, "rpe": 8},
          ]},
     ]},
    {"id": "w2", "title": "run", "routine_id": None, "description": None,
     # 23:30 UTC = 01:30 CEST NEXT day — CANON_TZ decides the date
     "start_time": "2026-07-06T23:30:00Z", "end_time": "2026-07-07T00:00:00Z",
     "exercises": [
         {"index": 0, "title": "Running", "notes": None,
          "exercise_template_id": "t2", "supersets_id": None,
          "sets": [{"index": 0, "type": "normal", "weight_kg": None,
                    "reps": None, "distance_meters": 5000,
                    "duration_seconds": 1800, "rpe": None}]},
     ]},
]}


def test_import_hevy_json_maps_api_fields(db, tmp_path):
    f = write_json(tmp_path, "w.json", WORKOUTS)
    outp = jout(run(db, "import-hevy-json", f))
    assert outp["ok"] is True and outp["imported_sets"] == 3
    got = rows(db, "SELECT * FROM hevy_sets ORDER BY id")
    assert all(r["source"] == "hevy" for r in got)
    sq = [r for r in got if r["exercise_title"] == "Front Squat"]
    assert sq[0]["set_type"] == "warmup" and sq[1]["weight_kg"] == 62.5
    assert sq[1]["rpe"] == 8 and sq[0]["date"] == "2026-07-06"
    assert sq[0]["workout_title"] == "fullbody A"
    assert sq[0]["exercise_notes"] == "felt strong"
    runrow = [r for r in got if r["exercise_title"] == "Running"][0]
    assert runrow["distance_km"] == 5.0 and runrow["duration_seconds"] == 1800
    # UTC 23:30 on the 6th is the 7th in Europe/Paris (CANON_TZ)
    assert runrow["date"] == "2026-07-07"


def test_import_hevy_json_full_reload_spares_ui_rows(db, tmp_path):
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg,"
             " reps, source) VALUES(?,?,?,?,?,?)",
         [("2026-07-05", "Front Squat", 1, 60, 8, "ui")])
    f = write_json(tmp_path, "w.json", WORKOUTS)
    run(db, "import-hevy-json", f)
    run(db, "import-hevy-json", f)   # idempotent: reload replaces hevy rows only
    got = rows(db, "SELECT source, COUNT(*) n FROM hevy_sets GROUP BY source")
    assert {r["source"]: r["n"] for r in got} == {"hevy": 3, "ui": 1}


QUARTERLY_LOWER = {"workouts": [{
    "id": "quarterly-w1",
    "title": "Quarterly Test 1 — Lower Body",
    "routine_id": "example-routine-lower",
    "start_time": "2026-07-26T10:00:00Z",
    "end_time": "2026-07-26T11:00:00Z",
    "exercises": [
        {"index": 0, "title": "Single Leg Extensions",
         "exercise_template_id": "example-template-leg-extension", "sets": [
             {"index": 0, "type": "warmup", "weight_kg": 20, "reps": 8},
             {"index": 1, "type": "warmup", "weight_kg": 35, "reps": 5},
             {"index": 2, "type": "normal", "weight_kg": 50, "reps": 8},
             {"index": 3, "type": "normal", "weight_kg": 50, "reps": 8},
         ]},
        {"index": 1, "title": "Seated Leg Curl (Machine)",
         "exercise_template_id": "example-template-leg-curl", "sets": [
             {"index": 0, "type": "normal", "weight_kg": 25, "reps": 7},
             {"index": 1, "type": "normal", "weight_kg": 25, "reps": 9},
         ]},
        {"index": 2, "title": "Single Leg Standing Calf Raise",
         "exercise_template_id": "example-template-calf-raise", "sets": [
             {"index": 0, "type": "normal", "weight_kg": None, "reps": 15},
             {"index": 1, "type": "normal", "weight_kg": None, "reps": 14},
         ]},
        {"index": 3, "title": "Quarterly Test — Single-Leg Balance (Eyes Closed)",
         "exercise_template_id": "example-template-balance",
         "sets": [
             {"index": 0, "type": "normal", "duration_seconds": 26},
             {"index": 1, "type": "normal", "duration_seconds": 14},
         ]},
    ],
}]}


def test_quarterly_hevy_import_feeds_fitness_tests_honestly(db, tmp_path):
    result = jout(run(db, "import-hevy-json",
                      write_json(tmp_path, "quarterly.json", QUARTERLY_LOWER)))
    assert result["quarterly_tests_inserted"] == 6
    assert result["quarterly_tests_rejected"] == 2
    assert any(w["code"] == "outside_protocol_rep_range"
               and w["movement"] == "leg-curl" and w["side"] == "right"
               for w in result["quarterly_warnings"])
    got = rows(db, """SELECT movement,side,load_kg,reps,seconds,source,voided
                      FROM fitness_tests ORDER BY id""")
    assert [(r["movement"], r["side"]) for r in got] == [
        ("leg-extension", "left"), ("leg-extension", "right"),
        ("leg-curl", "left"), ("leg-curl", "right"),
        ("balance-stand", "left"), ("balance-stand", "right"),
    ]
    # Warmups never consume left/right positions.
    assert got[0]["load_kg"] == 50 and got[0]["reps"] == 8
    assert got[1]["load_kg"] == 50 and got[1]["reps"] == 8
    assert got[3]["reps"] == 9  # retained, but explicitly warned above
    assert got[4]["seconds"] == 26 and got[5]["seconds"] == 14
    # Missing calf load is incompatible with an e1RM strength test: no guess.
    assert not any(r["movement"] == "calf-raise" for r in got)
    assert all(r["source"].startswith("hevy-quarterly:quarterly-w1:")
               for r in got)


def test_quarterly_hevy_import_is_idempotent_and_revises_corrections(db, tmp_path):
    path = write_json(tmp_path, "quarterly.json", QUARTERLY_LOWER)
    first = jout(run(db, "import-hevy-json", path))
    second = jout(run(db, "import-hevy-json", path))
    assert first["quarterly_tests_inserted"] == 6
    assert second["quarterly_tests_inserted"] == 0
    assert second["quarterly_tests_unchanged"] == 6
    assert len(rows(db, "SELECT * FROM fitness_tests")) == 6

    corrected = json.loads(json.dumps(QUARTERLY_LOWER))
    corrected["workouts"][0]["exercises"][0]["sets"][2]["weight_kg"] = 52.5
    revised = jout(run(db, "import-hevy-json",
                       write_json(tmp_path, "corrected.json", corrected)))
    assert revised["quarterly_tests_inserted"] == 1
    assert revised["quarterly_tests_superseded"] == 1
    left = rows(db, """SELECT load_kg,source,voided,void_reason
                       FROM fitness_tests
                       WHERE movement='leg-extension' AND side='left'
                       ORDER BY id""")
    assert left[0]["voided"] == 1
    assert left[0]["void_reason"] == "superseded by corrected Hevy sync"
    assert left[1]["load_kg"] == 52.5 and left[1]["voided"] == 0
    assert left[1]["source"].endswith(":r2")


def test_quarterly_bodyweight_calf_uses_latest_measured_body_mass(db, tmp_path):
    seed(db, """INSERT INTO body_metrics(date,weight_kg,source)
                VALUES(?,?,?)""",
         [("2026-07-01", 84.0, "manual"),
          ("2026-07-20", 83.4, "manual"),
          ("2026-07-27", 82.9, "manual")])
    result = jout(run(db, "import-hevy-json",
                      write_json(tmp_path, "quarterly.json", QUARTERLY_LOWER)))
    assert result["quarterly_tests_inserted"] == 8
    assert result["quarterly_tests_rejected"] == 0
    calf = rows(db, """SELECT side,load_kg,reps,equipment_note
                       FROM fitness_tests WHERE movement='calf-raise'
                       ORDER BY side""")
    assert [(r["side"], r["load_kg"], r["reps"]) for r in calf] == [
        ("left", 83.4, 15), ("right", 83.4, 14)]
    # Never use a future measurement; retain the exact provenance used.
    assert all("bodyweight system load 83.4 kg (measured 2026-07-20)"
               in r["equipment_note"] for r in calf)
    assert any(w["code"] == "outside_protocol_rep_range"
               and w["movement"] == "calf-raise"
               for w in result["quarterly_warnings"])


def test_quarterly_translation_requires_known_routine_and_template_ids(db, tmp_path):
    ordinary = json.loads(json.dumps(QUARTERLY_LOWER))
    ordinary["workouts"][0]["routine_id"] = "ordinary-routine"
    ordinary["workouts"][0]["title"] = "Leg day"
    result = jout(run(db, "import-hevy-json",
                      write_json(tmp_path, "ordinary.json", ordinary)))
    assert result["quarterly_tests_inserted"] == 0
    assert rows(db, "SELECT * FROM fitness_tests") == []

    unknown_template = json.loads(json.dumps(QUARTERLY_LOWER))
    unknown_template["workouts"][0]["exercises"][0]["exercise_template_id"] = "unknown"
    result = jout(run(db, "import-hevy-json",
                      write_json(tmp_path, "unknown-template.json", unknown_template)))
    assert result["quarterly_tests_inserted"] == 4
    assert not rows(db, "SELECT * FROM fitness_tests WHERE movement='leg-extension'")


TEMPLATES = {"exercise_templates": [
    {"id": "t1", "title": "Front Squat", "type": "weight_reps",
     "primary_muscle_group": "quadriceps",
     "secondary_muscle_groups": ["glutes", "abdominals"], "is_custom": False},
    {"id": "t2", "title": "Running", "type": "distance_duration",
     "primary_muscle_group": "cardio", "secondary_muscle_groups": [],
     "is_custom": False},
    {"id": "t3", "title": "Never Done", "type": "weight_reps",
     "primary_muscle_group": "chest", "secondary_muscle_groups": [],
     "is_custom": False},
]}


def test_import_templates_builds_muscle_map_for_used_exercises(db, tmp_path):
    run(db, "import-hevy-json", write_json(tmp_path, "w.json", WORKOUTS))
    outp = jout(run(db, "import-hevy-templates",
                    write_json(tmp_path, "t.json", TEMPLATES)))
    got = rows(db, "SELECT * FROM exercise_muscles ORDER BY exercise_title, muscle")
    m = {(r["exercise_title"], r["muscle"]): (r["weight"], r["source"]) for r in got}
    assert m[("Front Squat", "quadriceps")] == (1.0, "hevy")
    assert m[("Front Squat", "glutes")] == (0.5, "hevy")
    assert m[("Front Squat", "abdominals")] == (0.5, "hevy")
    assert m[("Running", "cardio")] == (1.0, "hevy")
    # unused template ("Never Done") is not mapped
    assert not any(t == "Never Done" for t, _ in m)
    assert outp["mapped_exercises"] == 2 and outp["skipped_manual"] == 0


def test_import_templates_never_clobbers_manual_rows(db, tmp_path):
    run(db, "import-hevy-json", write_json(tmp_path, "w.json", WORKOUTS))
    # a manually curated mapping for Front Squat (pre-source-column era rows
    # get source='manual' from the idempotent migration)
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight)"
             " VALUES(?,?,?)", [("Front Squat", "Quads", 1.0)])
    outp = jout(run(db, "import-hevy-templates",
                    write_json(tmp_path, "t.json", TEMPLATES)))
    got = rows(db, "SELECT * FROM exercise_muscles WHERE exercise_title='Front Squat'")
    assert len(got) == 1 and got[0]["muscle"] == "Quads"   # manual row untouched
    assert outp["skipped_manual"] == 1
    # re-running the sync stays idempotent
    outp2 = jout(run(db, "import-hevy-templates",
                     write_json(tmp_path, "t.json", TEMPLATES)))
    assert outp2["mapped_exercises"] == 1                  # Running only


ROUTINES = {"routines": [
    {"id": "r1", "title": "fullbody A", "folder_id": None,
     "exercises": [
         {"index": 0, "title": "Front Squat", "rest_seconds": "120",
          "exercise_template_id": "t1",
          "sets": [{"index": 0, "type": "warmup", "weight_kg": 40, "reps": 8},
                   {"index": 1, "type": "normal", "weight_kg": 60, "reps": 8},
                   {"index": 2, "type": "normal", "weight_kg": 60, "reps": 8},
                   {"index": 3, "type": "normal", "weight_kg": 60, "reps": 8}]},
     ]},
]}


def test_import_routines_refreshes_config_with_snapshot(db, tmp_path):
    seed(db, "INSERT INTO routines(routine_name, exercise_title, ex_order,"
             " target_sets, target_reps, target_weight_kg) VALUES(?,?,?,?,?,?)",
         [("old plan", "Bench Press (Barbell)", 1, 3, 8, 60.0)])
    seed(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
         [("Mon", "old plan")])
    outp = jout(run(db, "import-hevy-routines",
                    write_json(tmp_path, "r.json", ROUTINES)))
    assert outp["routines"] == 1
    got = rows(db, "SELECT * FROM routines")
    assert len(got) == 1
    r = got[0]
    # 3 non-warmup sets; reps/weight from the first non-warmup set
    assert (r["routine_name"], r["exercise_title"]) == ("fullbody A", "Front Squat")
    assert (r["target_sets"], r["target_reps"], r["target_weight_kg"]) == (3, 8, 60.0)
    # prior config snapshotted (nothing is silently lost)…
    hist = rows(db, "SELECT * FROM routines_history WHERE op='hevy-sync'")
    assert len(hist) == 1 and hist[0]["exercise_title"] == "Bench Press (Barbell)"
    # …and the weekday mapping (owner config) is untouched
    sched = rows(db, "SELECT * FROM training_schedule")
    assert sched == [{"weekday": "Mon", "routine_name": "old plan"}]


def test_sync_commands_are_not_bridge_allowlisted():
    """Bulk imports stay collector-only: the panel's write broker must refuse
    them (defense in depth — BUILD-PLAN §3 keeps dangerous/bulk commands out).
    Executes the real broker source and inspects its actual ALLOWED dict."""
    import types
    broker = types.ModuleType("broker")
    src = (ROOT.parent / "deploy" / "hermes-bridge").read_text()
    exec(compile(src, "hermes-bridge", "exec"), broker.__dict__)
    for cmd in ("import-hevy-json", "import-hevy-templates", "import-hevy-routines",
                "import-hevy"):
        assert cmd not in broker.ALLOWED


def test_import_hevy_json_refuses_suspicious_shrink(db, tmp_path):
    """An empty or drastically shrunken payload must NOT wipe history: a 200
    with `{"workouts": []}` (API incident / renamed field upstream) would
    otherwise delete every hevy-sourced set. --force overrides deliberately."""
    f = write_json(tmp_path, "w.json", WORKOUTS)
    run(db, "import-hevy-json", f)
    empty = write_json(tmp_path, "empty.json", {"workouts": []})
    r = run(db, "import-hevy-json", empty, expect_ok=False)
    assert r.returncode != 0 and "refus" in (r.stderr + r.stdout).lower()
    # one workout = 1 set vs existing 3 → < half → refused without --force
    one = write_json(tmp_path, "one.json", {"workouts": [WORKOUTS["workouts"][1]]})
    r = run(db, "import-hevy-json", one, expect_ok=False)
    assert r.returncode != 0
    run(db, "import-hevy-json", one, "--force")
    assert len(rows(db, "SELECT * FROM hevy_sets WHERE source='hevy'")) == 1


def test_import_hevy_json_skips_invalid_sets_and_counts(db, tmp_path):
    bad = {"workouts": [{
        "id": "w9", "title": "junk", "start_time": "2026-07-06T10:00:00Z",
        "end_time": None,
        "exercises": [{"index": 0, "title": "Bench", "sets": [
            {"index": 0, "type": "normal", "weight_kg": -1e12, "reps": 8},
            {"index": 1, "type": "normal", "weight_kg": 60, "reps": 8},
        ]}]}]}
    outp = jout(run(db, "import-hevy-json", write_json(tmp_path, "b.json", bad)))
    assert outp["imported_sets"] == 1 and outp["skipped_sets"] == 1


def test_set_index_normalized_to_one_based(db, tmp_path):
    """API set index is 0-based; log-set allocates 1-based — normalize so the
    two sources interleave consistently."""
    run(db, "import-hevy-json", write_json(tmp_path, "w.json", WORKOUTS))
    got = rows(db, "SELECT set_index FROM hevy_sets WHERE exercise_title='Front Squat'"
                   " ORDER BY set_index")
    assert [r["set_index"] for r in got] == [1, 2]


def test_templates_primary_wins_over_duplicate_secondary(db, tmp_path):
    run(db, "import-hevy-json", write_json(tmp_path, "w.json", WORKOUTS))
    t = {"exercise_templates": [
        {"id": "t1", "title": "Front Squat", "type": "weight_reps",
         "primary_muscle_group": "glutes",
         "secondary_muscle_groups": ["glutes", "quadriceps"], "is_custom": True}]}
    run(db, "import-hevy-templates", write_json(tmp_path, "t.json", t))
    got = {r["muscle"]: r["weight"] for r in rows(
        db, "SELECT * FROM exercise_muscles WHERE exercise_title='Front Squat'")}
    assert got == {"glutes": 1.0, "quadriceps": 0.5}


def test_templates_cleanup_stale_hevy_rows_once_curated(db, tmp_path):
    """Manual curation wins AND retires the old hevy rows — otherwise the two
    row sets coexist and the radar double-counts the exercise forever."""
    run(db, "import-hevy-json", write_json(tmp_path, "w.json", WORKOUTS))
    run(db, "import-hevy-templates", write_json(tmp_path, "t.json", TEMPLATES))
    seed(db, "INSERT OR REPLACE INTO exercise_muscles(exercise_title, muscle, weight, source)"
             " VALUES(?,?,?,?)", [("Front Squat", "Quads", 1.0, "manual")])
    outp = jout(run(db, "import-hevy-templates",
                    write_json(tmp_path, "t.json", TEMPLATES)))
    assert outp["skipped_manual"] == 1
    got = rows(db, "SELECT * FROM exercise_muscles WHERE exercise_title='Front Squat'")
    assert len(got) == 1 and got[0]["muscle"] == "Quads"   # hevy rows retired


def test_routines_aggregate_duplicate_exercise_entries(db, tmp_path):
    """Hevy allows the same exercise twice in one routine (heavy block +
    back-off). Entries aggregate — sets sum, first block's reps/weight/order
    win — instead of the last block silently replacing the first."""
    dup = {"routines": [{"id": "r9", "title": "Legs A", "exercises": [
        {"index": 0, "title": "Front Squat",
         "sets": [{"index": 0, "type": "normal", "weight_kg": 100, "reps": 5}] * 3},
        {"index": 5, "title": "Front Squat",
         "sets": [{"index": 0, "type": "normal", "weight_kg": 60, "reps": 10}] * 2},
    ]}]}
    outp = jout(run(db, "import-hevy-routines", write_json(tmp_path, "r.json", dup)))
    got = rows(db, "SELECT * FROM routines")
    assert len(got) == 1
    r = got[0]
    assert (r["target_sets"], r["target_reps"], r["target_weight_kg"], r["ex_order"]) \
        == (5, 5, 100.0, 0)
    assert outp["duplicate_entries"] == 1


def test_routines_report_orphaned_schedule(db, tmp_path):
    seed(db, "INSERT INTO training_schedule(weekday, routine_name) VALUES(?,?)",
         [("Mon", "old plan"), ("Tue", "fullbody A")])
    outp = jout(run(db, "import-hevy-routines",
                    write_json(tmp_path, "r.json", ROUTINES)))
    # Mon's routine no longer exists after the sync — surfaced, never edited
    assert outp["orphaned_schedule"] == {"Mon": "old plan"}
    assert len(rows(db, "SELECT * FROM training_schedule")) == 2


def test_routine_undo_skips_hevy_sync_snapshots(db, tmp_path):
    """The bulk sync journal must not bury manual user edits: undo acts
    on the last owner edit, never 'undoing' a hevy-sync snapshot row."""
    seed(db, "INSERT INTO routines(routine_name, exercise_title, ex_order,"
             " target_sets, target_reps, target_weight_kg) VALUES(?,?,?,?,?,?)",
         [("old plan", "Bench Press (Barbell)", 1, 3, 8, 60.0)])
    run(db, "import-hevy-routines", write_json(tmp_path, "r.json", ROUTINES))
    # owner edit AFTER the sync, then undo → reverses the edit, not the sync rows
    run(db, "routine-set", "fullbody A", "Front Squat", "--weight", "70")
    run(db, "routine-undo")
    got = rows(db, "SELECT target_weight_kg w FROM routines WHERE exercise_title='Front Squat'")
    assert got[0]["w"] == 60.0
    # a second undo finds no owner edit left — hevy-sync rows are not candidates
    r = run(db, "routine-undo", expect_ok=False)
    assert r.returncode != 0
