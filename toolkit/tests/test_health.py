"""Tests for toolkit/health.py — the canonical deterministic write layer.

Run against a temp DB via the real CLI (subprocess), exactly as an external
agent and the broker invoke it. Any change to health.py must keep this green.
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

HEVY_COLS = ("title,start_time,end_time,description,exercise_title,superset_id,"
             "exercise_notes,set_index,set_type,{w},reps,distance_km,duration_seconds,rpe")


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args, expect_ok=True, stdin=None):
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


def write_hevy_csv(path, unit="kg", exercise="Front Squat", weight=100.0):
    wcol = "weight_kg" if unit == "kg" else "weight_lbs"
    path.write_text(
        HEVY_COLS.format(w=wcol) + "\n"
        + f"fullbody Mandag,2026-07-01 10:00:00,2026-07-01 11:00:00,,{exercise},,,1,normal,{weight},8,,,\n"
        + f"fullbody Mandag,2026-07-01 10:00:00,2026-07-01 11:00:00,,{exercise},,,2,normal,{weight},7,,,\n"
    )


# --------------------------------------------------------------- import-hevy
def test_import_hevy_metric(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv, unit="kg", weight=60.0)
    assert jout(run(db, "import-hevy", str(csv)))["imported_sets"] == 2
    got = rows(db, "SELECT date, weight_kg, reps, source FROM hevy_sets ORDER BY set_index")
    assert got[0]["date"] == "2026-07-01"
    assert got[0]["weight_kg"] == 60.0
    assert all(r["source"] == "hevy" for r in got)


def test_import_hevy_lbs_converted(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv, unit="lbs", weight=100.0)
    run(db, "import-hevy", str(csv))
    got = rows(db, "SELECT weight_kg FROM hevy_sets LIMIT 1")
    assert got[0]["weight_kg"] == 45.36  # 100 lb -> kg, rounded to 2 dp


def test_import_hevy_miles_converted(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    csv.write_text("start_time,exercise_title,distance_miles\n2026-07-01,Running,2\n")

    run(db, "import-hevy", str(csv))

    assert rows(db, "SELECT distance_km FROM hevy_sets") == [{"distance_km": 3.219}]


def test_import_hevy_unknown_optional_values_stay_missing(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    csv.write_text("start_time,exercise_title,weight_kg,duration_seconds,rpe\n"
                   "unknown,Front Squat,unknown,,NaN\n")

    run(db, "import-hevy", str(csv))

    assert rows(db, "SELECT date,weight_kg,duration_seconds,rpe FROM hevy_sets") == [
        {"date": None, "weight_kg": None, "duration_seconds": None, "rpe": None}
    ]


def test_reimport_preserves_panel_logged_sets(db, tmp_path):
    """THE regression this repo exists to prevent: a Hevy reimport must never
    delete sets logged through the panel (source != 'hevy')."""
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv, weight=60.0)
    run(db, "import-hevy", str(csv))
    con = sqlite3.connect(db)
    con.execute("INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps, source)"
                " VALUES('2026-07-05', 'Deadlift (Barbell)', 1, 80, 5, 'ui')")
    con.commit()
    con.close()

    run(db, "import-hevy", str(csv))  # full reload of hevy rows

    ui = rows(db, "SELECT * FROM hevy_sets WHERE source='ui'")
    assert len(ui) == 1 and ui[0]["exercise_title"] == "Deadlift (Barbell)"
    assert len(rows(db, "SELECT * FROM hevy_sets WHERE source='hevy'")) == 2  # replaced, not doubled


def test_empty_hevy_export_clears_only_imported_history(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv)
    run(db, "import-hevy", str(csv))
    run(db, "log-set", "Front Squat", "--reps", "5", "--date", "2026-07-01")
    manual = rows(db, "SELECT * FROM hevy_sets WHERE source='ui'")
    csv.write_text("title,start_time,exercise_title\n")

    run(db, "import-hevy", str(csv))

    assert rows(db, "SELECT * FROM hevy_sets") == manual


def test_invalid_later_hevy_row_preserves_previous_history(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv)
    run(db, "import-hevy", str(csv))
    previous = rows(db, "SELECT * FROM hevy_sets ORDER BY id")
    csv.write_text("start_time,exercise_title,reps\n"
                   "2026-07-02,New Squat,5\n2026-07-02,New Squat,invalid\n")

    result = run(db, "import-hevy", str(csv), expect_ok=False)

    assert result.returncode == 1
    assert rows(db, "SELECT * FROM hevy_sets ORDER BY id") == previous


def test_missing_hevy_export_preserves_previous_history(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv)
    run(db, "import-hevy", str(csv))
    previous = rows(db, "SELECT * FROM hevy_sets ORDER BY id")

    result = run(db, "import-hevy", str(tmp_path / "missing.csv"), expect_ok=False)

    assert result.returncode == 1
    assert rows(db, "SELECT * FROM hevy_sets ORDER BY id") == previous


def test_failed_hevy_database_write_preserves_previous_history(db, tmp_path):
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv)
    run(db, "import-hevy", str(csv))
    previous = rows(db, "SELECT * FROM hevy_sets ORDER BY id")
    connection = sqlite3.connect(db)
    connection.execute("CREATE TRIGGER reject_fixture_set BEFORE INSERT ON hevy_sets "
                       "WHEN NEW.reps = 7 BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END")
    connection.commit()
    connection.close()

    result = run(db, "import-hevy", str(csv), expect_ok=False)

    assert result.returncode == 1
    assert rows(db, "SELECT * FROM hevy_sets ORDER BY id") == previous


def test_import_hevy_refuses_missing_source_without_ddl(tmp_path):
    """Migration 001 owns the guarded source column; this writer never adds it."""
    p = tmp_path / "legacy.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE hevy_sets (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " date TEXT, workout_title TEXT, start_time TEXT, end_time TEXT,"
                " description TEXT, exercise_title TEXT, superset_id TEXT,"
                " exercise_notes TEXT, set_index INTEGER, set_type TEXT,"
                " weight_kg REAL, reps INTEGER, distance_km REAL,"
                " duration_seconds REAL, rpe REAL)")
    con.commit()
    con.close()
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv)
    result = run(p, "import-hevy", str(csv), expect_ok=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "schema_migration_required"
    assert "source" not in {row[1] for row in sqlite3.connect(p).execute("PRAGMA table_info(hevy_sets)")}


# -------------------------------------------------------------------- log-set
def test_log_set_writes_ui_source_and_autoincrements(db):
    run(db, "log-set", "Front Squat", "--weight", "62.5", "--reps", "8")
    run(db, "log-set", "Front Squat", "--weight", "62.5", "--reps", "8")
    got = rows(db, "SELECT set_index, weight_kg, reps, source, set_type FROM hevy_sets"
                   " WHERE exercise_title='Front Squat' ORDER BY set_index")
    assert [r["set_index"] for r in got] == [1, 2]        # auto-incremented
    assert all(r["source"] == "ui" for r in got)          # protected from import-hevy
    assert got[0]["weight_kg"] == 62.5 and got[0]["reps"] == 8


def test_log_set_validation(db):
    assert run(db, "log-set", "Deadlift", "--reps", "0", expect_ok=False).returncode != 0     # reps<1
    assert run(db, "log-set", "Deadlift", "--reps", "8", "--weight", "9999", expect_ok=False).returncode != 0
    assert run(db, "log-set", "Deadlift", "--rpe", "12", "--reps", "8", expect_ok=False).returncode != 0
    assert run(db, "log-set", "Deadlift", expect_ok=False).returncode != 0                    # nothing given


def test_ui_set_survives_hevy_reimport(db, tmp_path):
    """The reason this whole project exists: a UI-logged set must NOT be wiped
    by a Hevy CSV reimport (which reloads only source='hevy' rows)."""
    csv = tmp_path / "hevy.csv"
    write_hevy_csv(csv, exercise="Front Squat", weight=60.0)
    run(db, "import-hevy", str(csv))                        # 2 hevy rows
    run(db, "log-set", "Bulgarian Split Squat", "--weight", "20", "--reps", "8")  # 1 ui row
    run(db, "import-hevy", str(csv))                        # reimport full Hevy history

    ui = rows(db, "SELECT exercise_title FROM hevy_sets WHERE source='ui'")
    hevy = rows(db, "SELECT id FROM hevy_sets WHERE source='hevy'")
    assert len(ui) == 1 and ui[0]["exercise_title"] == "Bulgarian Split Squat"  # survived
    assert len(hevy) == 2                                   # hevy replaced, not doubled


# ------------------------------------------------------------ program editing
def _routine(db, routine="R"):
    return rows(db, f"SELECT exercise_title, ex_order, target_sets, target_reps, target_weight_kg"
                    f" FROM routines WHERE routine_name='{routine}' ORDER BY ex_order")


def test_routine_set_add_edit_and_undo(db):
    # add new
    run(db, "routine-set", "R", "Front Squat", "--sets", "3", "--reps", "8", "--weight", "60")
    assert _routine(db) == [{"exercise_title": "Front Squat", "ex_order": 1,
                             "target_sets": 3, "target_reps": 8, "target_weight_kg": 60.0}]
    # edit weight only (reps/sets preserved)
    run(db, "routine-set", "R", "Front Squat", "--weight", "62.5")
    assert _routine(db)[0]["target_weight_kg"] == 62.5
    assert _routine(db)[0]["target_reps"] == 8
    # undo the edit -> back to 60
    run(db, "routine-undo")
    assert _routine(db)[0]["target_weight_kg"] == 60.0
    # undo the add -> row gone
    run(db, "routine-undo")
    assert _routine(db) == []


def test_routine_remove_and_undo_restores(db):
    run(db, "routine-set", "R", "Deadlift", "--sets", "3", "--reps", "5", "--weight", "80")
    run(db, "routine-remove", "R", "Deadlift")
    assert _routine(db) == []
    run(db, "routine-undo")   # restore the removed exercise
    got = _routine(db)
    assert len(got) == 1 and got[0]["exercise_title"] == "Deadlift" and got[0]["target_weight_kg"] == 80.0


def test_routine_undo_nothing_errors(db):
    assert run(db, "routine-undo", expect_ok=False).returncode != 0


def test_schedule_set_upserts(db):
    run(db, "schedule-set", "Mon", "R")
    run(db, "schedule-set", "Mon", "Rest")
    got = rows(db, "SELECT routine_name FROM training_schedule WHERE weekday='Mon'")
    assert got == [{"routine_name": "Rest"}]
    assert run(db, "schedule-set", "Funday", "R", expect_ok=False).returncode != 0


def test_schedule_change_is_undoable(db):
    run(db, "schedule-set", "Mon", "A")
    run(db, "schedule-set", "Mon", "B")   # change
    run(db, "routine-undo")               # must revert the schedule, not a routine edit
    assert rows(db, "SELECT routine_name FROM training_schedule WHERE weekday='Mon'") == [{"routine_name": "A"}]
    run(db, "routine-undo")               # undo the first set -> weekday row gone
    assert rows(db, "SELECT routine_name FROM training_schedule WHERE weekday='Mon'") == []


# ------------------------------------------------------------ vault notes
def test_write_note_whitelist_and_atomic(db, tmp_path):
    # DB lives in tmp_path; vault = its dir. personal/plan.md is whitelisted.
    r = subprocess.run([sys.executable, str(HEALTH), "write-note", "personal/plan.md"],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       input="# My Plan\nnew content\n", capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    written = (tmp_path / "personal" / "plan.md").read_text()
    assert written == "# My Plan\nnew content\n"


def test_write_note_rejects_non_whitelisted(db):
    for bad in ("CLAUDE.md", "../secret", "personal/daily/2026-07-07.md", "health.py"):
        r = subprocess.run([sys.executable, str(HEALTH), "write-note", bad],
                           env={**os.environ, "HEALTH_DB": str(db)},
                           input="x", capture_output=True, text=True)
        assert r.returncode != 0, f"should have rejected {bad}"


def test_write_note_uses_rebound_database_without_environment(db, monkeypatch, capsys):
    import io
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(ROOT))
    import health

    for key in ("HEALTH_DB", "HERMES_DATA_DIR", "HEALTH_VAULT", "VAULT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(health, "DB", str(db))
    monkeypatch.setattr(health.sys, "stdin", io.StringIO("# Fictional local plan\n"))
    health.write_note(SimpleNamespace(path="personal/plan.md"))
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert (db.parent / "personal" / "plan.md").read_text() == "# Fictional local plan\n"


# ---------------------------------------------------------------------- log
def test_log_whitelists_tables_and_columns(db):
    run(db, "log", "vitals", "date=2026-07-06", "systolic=124", "diastolic=80")
    assert rows(db, "SELECT systolic FROM vitals")[0]["systolic"] == 124

    assert run(db, "log", "sqlite_master", "x=1", expect_ok=False).returncode != 0
    assert run(db, "log", "vitals", "evil_column=1", expect_ok=False).returncode != 0


def test_generic_log_can_create_a_bounded_supplement_product(db):
    run(
        db,
        "log",
        "supplement_products",
        "name=Fictional Electrolyte",
        "brand=Example Brand",
        "dose=2",
        "unit=g",
        "form=powder",
        "schedule=daily",
        "active=1",
    )
    assert rows(
        db,
        """SELECT name,brand,dose,unit,form,schedule,active
             FROM supplement_products""",
    ) == [{
        "name": "Fictional Electrolyte",
        "brand": "Example Brand",
        "dose": 2.0,
        "unit": "g",
        "form": "powder",
        "schedule": "daily",
        "active": 1,
    }]
    assert run(
        db,
        "log",
        "supplement_products",
        "stock_count=4",
        expect_ok=False,
    ).returncode != 0


def test_generic_supplement_product_rejects_invalid_values_before_write(db):
    for fields in (
        ("brand=Only a brand",),
        ("name=   ",),
        ("name=Fictional", "active=2"),
        ("name=Fictional", "dose=unknown", "unit=mg"),
        ("name=Fictional", "dose=-1", "unit=mg"),
        ("name=Fictional", "dose=NaN", "unit=mg"),
        ("name=Fictional", "dose=Infinity", "unit=mg"),
        ("name=Fictional", "dose=2"),
        ("name=Fictional", "dose=2", "unit=   "),
    ):
        result = run(db, "log", "supplement_products", *fields, expect_ok=False)
        assert result.returncode != 0, fields
        assert rows(db, "SELECT * FROM supplement_products") == []
    # A product may be configured before its dose is known, or be inactive.
    run(db, "log", "supplement_products", "name=Fictional", "active=0")
    assert rows(db, "SELECT name,dose,active FROM supplement_products") == [
        {"name": "Fictional", "dose": None, "active": 0},
    ]


def test_log_validates_rating_range(db):
    assert run(db, "log", "subjective_daily", "focus=9", expect_ok=False).returncode != 0
    run(db, "log", "subjective_daily", "focus=4")
    assert rows(db, "SELECT focus FROM subjective_daily")[0]["focus"] == 4


def test_log_subjective_upserts_same_day(db):
    """Re-logging a date-PK table the same day updates, never raises UNIQUE."""
    run(db, "log", "subjective_daily", "focus=3", "energy=2")
    run(db, "log", "subjective_daily", "mood=5", "energy=4")  # same day again
    got = rows(db, "SELECT focus, energy, mood FROM subjective_daily")
    assert len(got) == 1
    assert got[0]["energy"] == 4 and got[0]["mood"] == 5   # updated
    assert got[0]["focus"] == 3                             # earlier value kept


def test_log_water_upserts_same_day(db):
    run(db, "log", "intake", "water_ml=500")
    run(db, "log", "intake", "water_ml=1200")
    got = rows(db, "SELECT water_ml FROM intake")
    assert got == [{"water_ml": 1200.0}]


def test_water_add_accumulates_taps(db):
    """§4c: preset taps ADD (unlike `log intake`, which replaces the day)."""
    run(db, "water-add", "250")
    run(db, "water-add", "500")
    r = jout(run(db, "water-add", "250"))
    assert r["total_ml"] == 1000.0 and r["added_ml"] == 250
    assert r["target_ml"] == 1800 and r["pct_of_target"] == 56
    assert rows(db, "SELECT water_ml FROM intake") == [{"water_ml": 1000.0}]


def test_water_add_validates_and_keeps_notes(db):
    assert "1-3000" in run(db, "water-add", "0", expect_ok=False).stderr
    assert "1-3000" in run(db, "water-add", "5000", expect_ok=False).stderr
    run(db, "log", "intake", "water_ml=500", "notes=with lunch")
    run(db, "water-add", "250")                       # a tap must not clobber notes
    got = rows(db, "SELECT water_ml, notes FROM intake")
    assert got == [{"water_ml": 750.0, "notes": "with lunch"}]


def test_day_rating_upserts(db):
    run(db, "day-rating", "green", "--date", "2026-07-06")
    run(db, "day-rating", "red", "--date", "2026-07-06")
    got = rows(db, "SELECT day_rating FROM subjective_daily WHERE date='2026-07-06'")
    assert got == [{"day_rating": 1}]  # one row, updated in place


# -------------------------------------------------------------------- query
def test_query_is_select_only(db):
    assert jout(run(db, "query", "SELECT 1 AS one"))["rows"] == [{"one": 1}]
    assert run(db, "query", "DELETE FROM vitals", expect_ok=False).returncode != 0
    assert run(db, "query", "SELECT 1; DROP TABLE vitals", expect_ok=False).returncode != 0


def _load_health_module(db):
    """Import health.py as a module (not the CLI) so cx_ro() can be exercised
    directly — the __main__ guard keeps import side-effect-free."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("health_mod", HEALTH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DB = str(db)                     # rebind the module-level DB to the temp DB
    return mod


def test_query_connection_is_read_only_not_just_regex(db):
    """Audit finding M3: query() must run on a read-only connection, so a write
    is refused at the CONNECTION level even if the SAFE regex is ever bypassed.
    Exercise cx_ro() directly (bypassing the regex) and prove writes are denied
    by mode=ro/query_only/authorizer — a plain SELECT can't be made to write, so
    the regex alone could never demonstrate this layer."""
    health = _load_health_module(db)
    c = health.cx_ro()
    try:
        c.execute("BEGIN")
        assert [dict(r) for r in c.execute("SELECT 1 AS one")] == [{"one": 1}]
        c.rollback()
        for write in ("INSERT INTO vitals(date, systolic) VALUES('2026-07-12', 120)",
                      "UPDATE vitals SET systolic = 1",
                      "DELETE FROM vitals",
                      "CREATE TABLE evil(x)"):
            with pytest.raises(sqlite3.DatabaseError):
                c.execute(write)
        assert [dict(r) for r in c.execute("SELECT 1 AS one")] == [{"one": 1}]  # reads still work
    finally:
        c.close()


def test_read_only_connection_allows_only_required_schema_metadata(db):
    """Migration validation can inspect schema shape without weakening cx_ro."""
    health = _load_health_module(db)
    c = health.cx_ro()
    try:
        assert list(c.execute("PRAGMA table_info('vitals')"))
        indexes = list(c.execute("PRAGMA index_list('vitals')"))
        if indexes:
            assert list(c.execute(f"PRAGMA index_info('{indexes[0]['name']}')"))
        list(c.execute("PRAGMA foreign_key_list('vitals')"))
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("PRAGMA user_version")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("PRAGMA user_version=9")
    finally:
        c.close()


# ----------------------------------------------------------------- bp-brief
def test_bp_brief_filters_to_one_medication(db):
    run(db, "log", "vitals", "systolic=126", "diastolic=82", "resting_hr=64")
    run(db, "log", "meds_log", "drug=medication", "dose_mg=5")
    run(db, "log", "meds_log", "drug=medication-alias", "dose_mg=10")
    run(db, "log", "meds_log", "drug=other-medication", "dose_mg=50")
    got = jout(run(db, "bp-brief", "--days", "7"))
    assert got["drug"] == "medication"
    assert got["doses"][0]["dose_total_mg"] == 15.0
    assert got["vitals"][0]["systolic"] == 126


def test_bp_brief_drug_match_is_case_insensitive(db):
    # Free-text labels are matched case-insensitively.
    run(db, "log", "meds_log", "drug=Medication", "dose_mg=5")
    run(db, "log", "meds_log", "drug=medication", "dose_mg=5")
    got = jout(run(db, "bp-brief", "--days", "7"))
    assert got["doses"][0]["dose_total_mg"] == 10.0


# ------------------------------------------------------- nutrition math
def test_prep_eat_deterministic_macros(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name) VALUES('sample-stew', 'Sample Stew')")
    # recipe_nutrients.per_gram holds RECIPE TOTALS until batch weight is set
    con.execute("INSERT INTO recipe_nutrients VALUES('sample-stew', 'Energy', 'kcal', 3000)")
    con.execute("INSERT INTO recipe_nutrients VALUES('sample-stew', 'Protein', 'g', 250)")
    con.commit()
    con.close()

    prep = jout(run(db, "prep", "sample-stew", "--portions", "5", "--batch-grams", "2000"))
    assert prep["grams_per_portion"] == 400.0

    ate = jout(run(db, "eat", "sample-stew"))
    assert ate["kcal"] == 600.0      # 3000/2000g * 400g
    assert ate["protein_g"] == 50.0  # 250/2000g * 400g
    assert ate["portions_left"] == 4
    assert ate["restock_alert"] is False

    run(db, "eat", "sample-stew"); run(db, "eat", "sample-stew")
    assert jout(run(db, "eat", "sample-stew"))["restock_alert"] is True  # 1 left


def test_eat_refuses_when_out_of_stock(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams) VALUES('soup', 'Soup', 1000)")
    con.commit()
    con.close()
    assert run(db, "eat", "soup", expect_ok=False).returncode != 0


def test_restock_state_snoozes_persists_and_resets_on_new_batch(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id,name) VALUES('stew','Fictional Stew')")
    con.commit(); con.close()
    run(db, "prep", "stew", "--portions", "4", "--batch-grams", "800")
    ate = jout(run(
        db, "eat", "stew", "--portions", "3", "--date", "2026-07-10"
    ))
    assert ate["portions_left"] == 1
    assert ate["restock_alert"] is True
    assert "restock-mark" in ate["restock_next"]

    due = jout(run(
        db, "restock-check", "--threshold", "2", "--date", "2026-07-10"
    ))
    assert due["count"] == 1
    assert due["alerts"][0] == {
        "recipe_id": "stew",
        "recipe": "Fictional Stew",
        "portions_left": 1.0,
        "threshold": 2.0,
        "prepped_on": due["alerts"][0]["prepped_on"],
    }

    marked = jout(run(
        db, "restock-mark", "stew", "--action", "later",
        "--threshold", "2", "--date", "2026-07-10",
    ))
    assert marked["snooze_until"] == "2026-07-13"
    assert jout(run(
        db, "restock-check", "--date", "2026-07-12"
    ))["count"] == 0
    assert jout(run(
        db, "restock-check", "--date", "2026-07-13"
    ))["count"] == 1

    run(
        db, "restock-mark", "stew", "--action", "notified",
        "--date", "2026-07-13",
    )
    assert jout(run(
        db, "restock-check", "--date", "2026-07-14"
    ))["count"] == 0
    assert rows(db, "SELECT action,threshold,portions_at_notice,snooze_until "
                    "FROM recipe_restock_state") == [{
        "action": "notified",
        "threshold": 2.0,
        "portions_at_notice": 1.0,
        "snooze_until": None,
    }]

    run(db, "prep", "stew", "--portions", "4", "--batch-grams", "800")
    assert rows(db, "SELECT * FROM recipe_restock_state") == []


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "1000001"])
def test_restock_threshold_rejects_invalid_numbers(db, value):
    result = run(
        db, "restock-check", "--threshold", value, expect_ok=False
    )
    assert result.returncode != 0
    assert "threshold" in (result.stderr + result.stdout)


def test_restock_rejects_bad_dates_actions_and_unknown_recipes(db):
    bad_date = run(
        db, "restock-check", "--date", "2026-02-30", expect_ok=False
    )
    assert bad_date.returncode != 0 and "YYYY-MM-DD" in bad_date.stderr
    unknown = run(
        db, "restock-mark", "missing", "--action", "later", expect_ok=False
    )
    assert unknown.returncode != 0 and "recipe not found" in unknown.stderr
    invalid_action = run(
        db, "restock-mark", "missing", "--action", "unknown", expect_ok=False
    )
    assert invalid_action.returncode != 0

    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id,name) VALUES('soup','Fictional Soup')")
    con.commit(); con.close()
    bad_snooze = run(
        db, "restock-mark", "soup", "--action", "later",
        "--snooze-until", "not-a-date", expect_ok=False,
    )
    assert bad_snooze.returncode != 0 and "snooze-until" in bad_snooze.stderr


def test_restock_commands_fail_closed_without_migration_owned_table(tmp_path):
    database = tmp_path / "legacy-restock.db"
    con = sqlite3.connect(database)
    con.execute("CREATE TABLE recipes(recipe_id TEXT PRIMARY KEY,name TEXT)")
    con.execute(
        "CREATE TABLE meal_inventory(id INTEGER PRIMARY KEY,recipe_id TEXT,"
        "portions_remaining REAL,grams_per_portion REAL,prepped_on TEXT)"
    )
    con.commit(); con.close()
    result = run(database, "restock-check", expect_ok=False)
    assert json.loads(result.stdout)["error"]["code"] == "schema_migration_required"
    con = sqlite3.connect(database)
    assert con.execute(
        "SELECT 1 FROM sqlite_master WHERE name='recipe_restock_state'"
    ).fetchone() is None
    con.close()


def test_import_recipes_end_to_end_totals_semantic(db, tmp_path):
    """T47 Part A pin: the ONE end-to-end semantic test for recipe macros,
    hand-computed. import-recipes stores the CSV's nutrient values VERBATIM
    as RECIPE TOTALS in recipe_nutrients.per_gram (misnamed column, documented
    in health.py); per-portion = total / batch_grams * grams_per_portion.
    A sample-vegetable-stew batch exported as TOTALS (3000 kcal / 250 g protein for
    the whole 2000 g batch), split into 5 x 400 g portions, must eat as
    600 kcal / 50 g protein per portion.

    If the owner's live DB shows nonsense per-portion numbers while this
    passes, the imported CSV did not contain whole-recipe totals (e.g. a
    per-serving Cronometer export — its Amount column is deliberately ignored
    by the importer) — a DATA defect, not a math defect. See the T47 report's
    root-cause chain + owner-run diagnostics."""
    csv_path = tmp_path / "recipes.csv"
    csv_path.write_text(
        "Food ID,Food Name,Comments,Amount,Energy (kcal),Protein (g),Carbs (g),Fat (g),Iron (mg)\n"
        "123,Sample Vegetable Stew,batch of 2026-07-01,1.0 recipe,3000,250,180,120,40\n"
    )
    imp = jout(run(db, "import-recipes", str(csv_path)))
    assert imp["recipes_loaded"] == ["Sample Vegetable Stew"]
    stored = {r["nutrient"]: r["per_gram"] for r in rows(
        db, "SELECT nutrient, per_gram FROM recipe_nutrients WHERE recipe_id='sample-vegetable-stew'")}
    # verbatim totals, unit split off the header, Amount column ignored
    assert stored == {"Energy": 3000.0, "Protein": 250.0, "Carbs": 180.0,
                      "Fat": 120.0, "Iron": 40.0}

    prep = jout(run(db, "prep", "Sample Vegetable Stew", "--portions", "5", "--batch-grams", "2000"))
    assert prep["grams_per_portion"] == 400.0
    ate = jout(run(db, "eat", "Sample Vegetable Stew"))
    assert ate["kcal"] == 600.0      # 3000 / 2000 g * 400 g — hand-computed truth
    assert ate["protein_g"] == 50.0  # 250 / 2000 g * 400 g


# ------------------------------------------------------- T56/T57: per-serving import + set-batch --portions
def _write_recipe_csv(path, servings_note="per-serving values"):
    """Cronometer RECIPE export shape, but PER-SERVING values (not totals) —
    e.g. one serving of a 10-serving sample vegetable stew batch."""
    path.write_text(
        "Food ID,Food Name,Comments,Amount,Energy (kcal),Protein (g),Carbs (g),Fat (g),Iron (mg)\n"
        f"123,Sample Vegetable Stew,{servings_note},1.0 serving,300,25,18,12,4\n"
    )
    return path


def test_import_recipes_per_serving_multiplies_by_servings(db, tmp_path):
    """T56: --per-serving --servings N must multiply every CSV nutrient value
    by N before storing, converting a per-serving Cronometer export to the
    batch-totals semantic recipe_nutrients.per_gram expects."""
    csv_path = _write_recipe_csv(tmp_path / "recipes.csv")
    imp = jout(run(db, "import-recipes", str(csv_path), "--per-serving", "--servings", "10"))
    assert imp["per_serving"] is True
    assert imp["servings"] == 10
    stored = {r["nutrient"]: r["per_gram"] for r in rows(
        db, "SELECT nutrient, per_gram FROM recipe_nutrients WHERE recipe_id='sample-vegetable-stew'")}
    assert stored == {"Energy": 3000.0, "Protein": 250.0, "Carbs": 180.0,
                       "Fat": 120.0, "Iron": 40.0}


def _write_per_gram_recipe_csv(path):
    """The exact Cronometer food-export shape that caused the live regression:
    Amount is `g`, so every nutrient value is for one gram."""
    path.write_text(
        "Food ID,Food Name,Comments,Amount,Energy (kcal),Protein (g),Carbs (g),Fat (g),Fiber (g)\n"
        "77717606,Sample Breakfast Bowl,,g,1.67,0.10,0.20,0.06,0.03\n"
    )
    return path


def test_import_recipes_refuses_per_gram_csv_without_explicit_mode(db, tmp_path):
    """A per-gram row must never silently become a whole-batch total again."""
    csv_path = _write_per_gram_recipe_csv(tmp_path / "food.csv")
    r = run(db, "import-recipes", str(csv_path), expect_ok=False)
    assert r.returncode != 0
    assert "per-gram nutrients" in (r.stderr + r.stdout)
    assert rows(db, "SELECT COUNT(*) AS n FROM recipes")[0]["n"] == 0


def test_import_recipes_per_gram_scales_and_sets_recipe_metadata(db, tmp_path):
    """Per-gram values × batch grams become batch totals; portion math then
    returns the source's per-gram values × one portion, not near-zero macros."""
    csv_path = _write_per_gram_recipe_csv(tmp_path / "food.csv")
    imp = jout(run(
        db, "import-recipes", str(csv_path),
        "--per-gram", "--batch-grams", "3157", "--portions", "7",
        "--meal-type", "breakfast",
    ))
    assert imp == {
        "ok": True,
        "recipes_loaded": ["Sample Breakfast Bowl"],
        "per_gram": True,
        "batch_grams": 3157.0,
        "portions": 7,
        "grams_per_portion": 451.0,
        "meal_type": "breakfast",
    }
    recipe = rows(
        db, "SELECT batch_grams,grams_per_portion,portions,meal_type "
            "FROM recipes WHERE recipe_id='sample-breakfast-bowl'")[0]
    assert recipe == {
        "batch_grams": 3157.0,
        "grams_per_portion": 451.0,
        "portions": 7,
        "meal_type": "breakfast",
    }
    stored = {r["nutrient"]: r["per_gram"] for r in rows(
        db, "SELECT nutrient,per_gram FROM recipe_nutrients "
            "WHERE recipe_id='sample-breakfast-bowl'")}
    assert stored == {
        "Energy": 5272.19,
        "Protein": 315.7,
        "Carbs": 631.4,
        "Fat": 189.42,
        "Fiber": 94.71,
    }
    logged = jout(run(
        db, "log-food", "Sample Breakfast Bowl", "--grams", "451"))
    assert logged["kcal"] == 753.17
    assert logged["protein_g"] == 45.1
    assert logged["carbs_g"] == 90.2
    assert logged["fat_g"] == 27.06
    assert rows(db, "SELECT fiber_g FROM nutrition_log")[0]["fiber_g"] == 13.53


def test_import_recipes_per_gram_requires_batch_weight(db, tmp_path):
    csv_path = _write_per_gram_recipe_csv(tmp_path / "food.csv")
    r = run(db, "import-recipes", str(csv_path), "--per-gram", expect_ok=False)
    assert r.returncode != 0
    assert "--per-gram requires --batch-grams" in (r.stderr + r.stdout)


def test_import_recipes_refuses_per_serving_csv_without_explicit_mode(db, tmp_path):
    csv_path = _write_recipe_csv(tmp_path / "recipes.csv")
    r = run(db, "import-recipes", str(csv_path), expect_ok=False)
    assert r.returncode != 0
    assert "per-serving nutrients" in (r.stderr + r.stdout)


def test_recipe_reimport_preserves_existing_batch_and_meal_metadata(db, tmp_path):
    """Refreshing nutrient totals must not erase previously configured recipe
    weight, portions, or meal filter tag."""
    csv_path = tmp_path / "recipes.csv"
    csv_path.write_text(
        "Food ID,Food Name,Comments,Amount,Energy (kcal),Protein (g)\n"
        "123,Sample Vegetable Stew,refreshed,1.0 recipe,3000,250\n"
    )
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO recipes(recipe_id,name,batch_grams,grams_per_portion,portions,meal_type) "
        "VALUES('sample-vegetable-stew','Sample Vegetable Stew',2000,400,5,'dinner')")
    con.commit(); con.close()

    run(db, "import-recipes", str(csv_path))
    recipe = rows(
        db, "SELECT batch_grams,grams_per_portion,portions,meal_type,notes "
            "FROM recipes WHERE recipe_id='sample-vegetable-stew'")[0]
    assert recipe == {
        "batch_grams": 2000.0,
        "grams_per_portion": 400.0,
        "portions": 5,
        "meal_type": "dinner",
        "notes": "refreshed",
    }


def _seed_recipe(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id,name) VALUES('breakfast','Breakfast')")
    con.commit(); con.close()


def test_recipe_ingredients_set_writes_validated_sidecar(db, monkeypatch):
    _seed_recipe(db)
    vault = db.parent / "configured-vault"
    monkeypatch.setenv("HEALTH_VAULT", str(vault))
    payload = json.dumps({"ingredients": [
        {"name": "Oats", "amount": 700, "unit": "g"},
        {"name": "Nut mix", "amount": 7, "unit": "servings", "weight_g": 210,
         "note": "30 g each"},
    ]})
    got = jout(run(
        db, "recipe-ingredients-set", "Breakfast", stdin=payload))
    assert got["ingredients_count"] == 2
    assert got["ingredients"][1] == {
        "name": "Nut mix", "amount": 7, "unit": "servings",
        "weight_g": 210, "note": "30 g each",
    }
    assert got["file"] == "personal/recipes/breakfast.ingredients.json"
    target = vault / "personal" / "recipes" / "breakfast.ingredients.json"
    saved = json.loads(target.read_text())
    assert saved["recipe_id"] == "breakfast"
    assert saved["recipe_name"] == "Breakfast"
    assert saved["ingredients"] == got["ingredients"]


def test_recipe_ingredients_invalid_replacement_preserves_prior_file(db):
    _seed_recipe(db)
    good = json.dumps([{"name": "Oats", "amount": 700, "unit": "g"}])
    run(db, "recipe-ingredients-set", "breakfast", stdin=good)
    target = db.parent / "personal" / "recipes" / "breakfast.ingredients.json"
    before = target.read_bytes()

    bad = json.dumps([{"name": "Oats", "amount": -1, "unit": "g"}])
    result = run(
        db, "recipe-ingredients-set", "breakfast",
        stdin=bad, expect_ok=False)
    assert result.returncode != 0
    assert "must be finite and > 0" in (result.stderr + result.stdout)
    assert target.read_bytes() == before


def test_recipe_ingredients_rejects_unknown_recipe_without_writing(db):
    result = run(
        db, "recipe-ingredients-set", "missing",
        stdin=json.dumps([{"name": "Oats"}]), expect_ok=False)
    assert result.returncode != 0
    assert "recipe not found" in (result.stderr + result.stdout)
    assert not (db.parent / "personal").exists()


def test_import_recipes_default_mode_unchanged(db, tmp_path):
    """Default (no flags) import must stay byte-identical to pre-T56 behavior:
    no per_serving/servings keys in the output, values stored verbatim."""
    csv_path = tmp_path / "recipes.csv"
    csv_path.write_text(
        "Food ID,Food Name,Comments,Amount,Energy (kcal),Protein (g),Carbs (g),Fat (g),Iron (mg)\n"
        "123,Sample Vegetable Stew,batch of 2026-07-01,1.0 recipe,3000,250,180,120,40\n"
    )
    imp = jout(run(db, "import-recipes", str(csv_path)))
    assert "per_serving" not in imp
    assert "servings" not in imp
    stored = {r["nutrient"]: r["per_gram"] for r in rows(
        db, "SELECT nutrient, per_gram FROM recipe_nutrients WHERE recipe_id='sample-vegetable-stew'")}
    assert stored == {"Energy": 3000.0, "Protein": 250.0, "Carbs": 180.0,
                       "Fat": 120.0, "Iron": 40.0}


def test_import_recipes_per_serving_requires_servings(db, tmp_path):
    csv_path = _write_recipe_csv(tmp_path / "recipes.csv")
    r = run(db, "import-recipes", str(csv_path), "--per-serving", expect_ok=False)
    assert r.returncode != 0


def test_import_recipes_servings_without_per_serving_errors(db, tmp_path):
    csv_path = _write_recipe_csv(tmp_path / "recipes.csv")
    r = run(db, "import-recipes", str(csv_path), "--servings", "10", expect_ok=False)
    assert r.returncode != 0


def test_import_recipes_servings_zero_errors(db, tmp_path):
    csv_path = _write_recipe_csv(tmp_path / "recipes.csv")
    r = run(db, "import-recipes", str(csv_path), "--per-serving", "--servings", "0", expect_ok=False)
    assert r.returncode != 0


def test_set_batch_with_portions_sets_grams_per_portion(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name) VALUES('sample-stew', 'Sample Stew')")
    con.commit()
    con.close()

    before = rows(db, "SELECT COUNT(*) AS n FROM meal_inventory")[0]["n"]
    res = jout(run(db, "set-batch", "sample-stew", "--grams", "2000", "--portions", "5"))
    assert res["portions"] == 5
    assert res["grams_per_portion"] == 400.0
    after = rows(db, "SELECT COUNT(*) AS n FROM meal_inventory")[0]["n"]
    assert after == before  # set-batch must never touch meal_inventory (that's prep's job)

    r = rows(db, "SELECT batch_grams, portions, grams_per_portion FROM recipes WHERE recipe_id='sample-stew'")[0]
    assert r["batch_grams"] == 2000.0
    assert r["portions"] == 5
    assert r["grams_per_portion"] == 400.0


def test_set_batch_without_portions_leaves_portions_untouched(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name, portions, grams_per_portion) "
                "VALUES('sample-stew', 'Sample Stew', 3, 111.0)")
    con.commit()
    con.close()

    res = jout(run(db, "set-batch", "sample-stew", "--grams", "2000"))
    assert "portions" not in res
    assert "grams_per_portion" not in res
    r = rows(db, "SELECT batch_grams, portions, grams_per_portion FROM recipes WHERE recipe_id='sample-stew'")[0]
    assert r["batch_grams"] == 2000.0
    assert r["portions"] == 3          # unchanged (regression)
    assert r["grams_per_portion"] == 111.0  # unchanged (regression)


def test_set_batch_portions_zero_errors(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name) VALUES('sample-stew', 'Sample Stew')")
    con.commit()
    con.close()
    r = run(db, "set-batch", "sample-stew", "--grams", "2000", "--portions", "0", expect_ok=False)
    assert r.returncode != 0


def test_import_recipes_per_serving_then_set_batch_portions_end_to_end(db, tmp_path):
    """Mirrors test_import_recipes_end_to_end_totals_semantic but via the new
    per-serving import + set-batch --portions path: a Cronometer export of ONE
    serving (300 kcal / 25 g protein) from a 10-serving batch, imported with
    --per-serving --servings 10, cooked to a 2000 g batch split into 5 x 400 g
    portions via set-batch --portions, must eat as 600 kcal / 50 g protein —
    the same hand-computed truth as the totals-import test."""
    csv_path = _write_recipe_csv(tmp_path / "recipes.csv")
    jout(run(db, "import-recipes", str(csv_path), "--per-serving", "--servings", "10"))

    sb = jout(run(db, "set-batch", "Sample Vegetable Stew", "--grams", "2000", "--portions", "5"))
    assert sb["grams_per_portion"] == 400.0

    # set-batch never touches meal_inventory (that's prep's job), so verify the
    # per-portion math via log-food (computes straight off recipes.batch_grams).
    ate = jout(run(db, "log-food", "Sample Vegetable Stew", "--grams", str(sb["grams_per_portion"])))
    assert ate["kcal"] == 600.0      # 3000 / 2000 g * 400 g
    assert ate["protein_g"] == 50.0  # 250 / 2000 g * 400 g


# ------------------------------------------------------- recipe-tag (T47)
def test_recipe_tag_sets_and_clears(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name) VALUES('sample-stew', 'Sample Vegetable Stew')")
    con.commit(); con.close()
    got = jout(run(db, "recipe-tag", "sample-stew", "dinner"))
    assert got == {"ok": True, "recipe": "Sample Vegetable Stew", "meal_type": "dinner"}
    assert rows(db, "SELECT meal_type FROM recipes")[0]["meal_type"] == "dinner"
    # by name, and 'clear' nulls it
    got = jout(run(db, "recipe-tag", "Sample Vegetable Stew", "clear"))
    assert got["meal_type"] is None
    assert rows(db, "SELECT meal_type FROM recipes")[0]["meal_type"] is None


def test_recipe_tag_rejects_unknown_recipe(db):
    r = run(db, "recipe-tag", "nope", "dinner", expect_ok=False)
    assert r.returncode != 0 and "recipe not found" in (r.stderr + r.stdout)


def test_recipe_tag_rejects_bad_meal_type(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name) VALUES('sample-stew', 'Sample Stew')")
    con.commit(); con.close()
    r = run(db, "recipe-tag", "sample-stew", "brunch", expect_ok=False)
    assert r.returncode != 0 and "meal_type must be one of" in (r.stderr + r.stdout)
    # the failed write must not have migrated-and-tagged anything
    assert rows(db, "SELECT meal_type FROM recipes")[0]["meal_type"] is None


def _legacy_recipes_db(tmp_path):
    """A pre-T47 DB whose recipes table has NO meal_type column."""
    p = tmp_path / "legacy.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE recipes (recipe_id TEXT PRIMARY KEY, name TEXT,"
                " batch_grams REAL, grams_per_portion REAL, portions INTEGER,"
                " notes TEXT, source TEXT DEFAULT 'cronometer',"
                " ingested_at TEXT DEFAULT (datetime('now')))")
    con.execute("CREATE TABLE meal_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " recipe_id TEXT, portions_remaining REAL, grams_per_portion REAL,"
                " prepped_on TEXT, notes TEXT, ingested_at TEXT DEFAULT (datetime('now')))")
    con.execute("CREATE TABLE recipe_nutrients (recipe_id TEXT, nutrient TEXT, unit TEXT,"
                " per_gram REAL, PRIMARY KEY (recipe_id, nutrient))")
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams, grams_per_portion)"
                " VALUES('sample-stew', 'Sample Stew', 2000, 400)")
    con.execute("INSERT INTO meal_inventory(recipe_id, portions_remaining, grams_per_portion,"
                " prepped_on) VALUES('sample-stew', 3, 400, '2026-07-01')")
    con.commit(); con.close()
    return p


def _recipes_columns(p):
    con = sqlite3.connect(p)
    try:
        return [r[1] for r in con.execute("PRAGMA table_info(recipes)")]
    finally:
        con.close()


def test_menu_read_never_migrates_meal_type(tmp_path):
    """Reads fail closed on an unmigrated guarded column and never add it."""
    p = _legacy_recipes_db(tmp_path)
    result = run(p, "menu", expect_ok=False)
    assert json.loads(result.stdout)["error"]["code"] == "schema_migration_required"
    assert "meal_type" not in _recipes_columns(p)


def test_recipe_tag_refuses_legacy_db_without_ddl(tmp_path):
    """Writer paths also require the explicit migration instead of altering shape."""
    p = _legacy_recipes_db(tmp_path)
    result = run(p, "recipe-tag", "sample-stew", "lunch", expect_ok=False)
    assert json.loads(result.stdout)["error"]["code"] == "schema_migration_required"
    assert "meal_type" not in _recipes_columns(p)


def test_menu_includes_meal_type_on_current_schema(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams, grams_per_portion)"
                " VALUES('sample-stew', 'Sample Stew', 2000, 400)")
    con.execute("INSERT INTO meal_inventory(recipe_id, portions_remaining, grams_per_portion,"
                " prepped_on) VALUES('sample-stew', 2, 400, '2026-07-01')")
    con.commit(); con.close()
    assert jout(run(db, "menu"))["menu"][0]["meal_type"] is None   # untagged -> honest null
    run(db, "recipe-tag", "sample-stew", "snack")
    assert jout(run(db, "menu"))["menu"][0]["meal_type"] == "snack"
