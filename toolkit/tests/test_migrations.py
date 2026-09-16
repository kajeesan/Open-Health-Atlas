"""Phase 1-3 schema ledger, migration and no-read-DDL contracts."""

import hashlib
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
PHASE2_SCHEMA = (
    ROOT / "tests" / "fixtures" / "legacy_phase2_schema.sql"
).read_text(encoding="utf-8")
CODE_VERSION = "a" * 40
sys.path.insert(0, str(ROOT))

from hermes_insights.migrations import (
    GUARDED_001, GUARDED_002, LAZY_TABLE_DDL, MIGRATION_BY_VERSION,
    PHASE3_INDEX_DDL, PHASE3_TABLE_DDL, migrate as migrate_direct,
    schema_plan as schema_plan_direct, SchemaError,
    canonical_training_plan_snapshot,
)
from hermes_insights.goals import canonical_plan


ALL_GUARDS = {**GUARDED_001, **GUARDED_002}
PRE_GUARD_DDL = {
    "fitness_tests": """CREATE TABLE fitness_tests(
      id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT NOT NULL,movement TEXT NOT NULL,
      side TEXT NOT NULL DEFAULT 'bilateral',load_kg REAL,reps INTEGER,seconds REAL,
      rating INTEGER,cm REAL,equipment_note TEXT,source TEXT NOT NULL DEFAULT 'chat',
      voided INTEGER NOT NULL DEFAULT 0,void_reason TEXT,
      created_at TEXT DEFAULT (datetime('now')));""",
    "exercise_submuscles": """CREATE TABLE exercise_submuscles(
      exercise_title TEXT NOT NULL,muscle_group TEXT NOT NULL,sub_region TEXT NOT NULL,
      weight REAL NOT NULL DEFAULT 1.0,laterality TEXT NOT NULL DEFAULT 'bilateral'
        CHECK(laterality IN ('left','right','bilateral')),source TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now')),
      PRIMARY KEY(exercise_title,sub_region,laterality));""",
}


def run(db, *args, expect=0, stdin=None):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args], input=stdin, text=True,
        capture_output=True, timeout=30,
        env={**os.environ, "HEALTH_DB": str(db), "HERMES_CODE_VERSION": CODE_VERSION},
    )
    assert result.returncode == expect, result.stderr or result.stdout
    return json.loads(result.stdout)


def fresh(tmp_path):
    path = tmp_path / "fresh.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit(); con.close()
    return path


def transitional(tmp_path):
    """Exact Phase 2 handoff schema, before any live migration has run."""
    path = tmp_path / "phase2-transitional.db"
    con = sqlite3.connect(path)
    con.executescript(PHASE2_SCHEMA)
    con.commit(); con.close()
    return path


def legacy(tmp_path, *, incompatible=False, live_shape=False):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    source_type = "INTEGER" if incompatible else "TEXT"
    con.executescript(f"""
      CREATE TABLE hevy_sets(id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT,source {source_type} DEFAULT 'hevy');
      CREATE TABLE exercise_muscles(exercise_title TEXT,muscle TEXT,weight REAL,
        PRIMARY KEY(exercise_title,muscle));
      CREATE TABLE body_metrics(id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT,weight_kg REAL);
      CREATE TABLE recipes(recipe_id TEXT PRIMARY KEY,name TEXT,batch_grams REAL,
        grams_per_portion REAL,portions INTEGER,notes TEXT,source TEXT DEFAULT 'cronometer',
        ingested_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE subjective_daily(date TEXT PRIMARY KEY,source TEXT DEFAULT 'manual');
      CREATE TABLE daily_metrics(date TEXT,source TEXT NOT NULL DEFAULT 'apple',hrv_sdnn REAL,
        PRIMARY KEY(date,source));
      CREATE TABLE nutrition_log(id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT,recipe_id TEXT,
        food_name TEXT,grams REAL,kcal REAL,protein_g REAL,carbs_g REAL,fat_g REAL,fiber_g REAL,
        notes TEXT,source TEXT DEFAULT 'manual',ingested_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE supplement_products(supplement_id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,
        brand TEXT,dose REAL,unit TEXT,form TEXT,schedule TEXT,active INTEGER DEFAULT 1,notes TEXT,
        ingested_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE supplements_log(id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT,
        supplement_id INTEGER,taken INTEGER,dose_taken REAL,notes TEXT,
        source TEXT DEFAULT 'manual',ingested_at TEXT DEFAULT (datetime('now')));
    """)
    if live_shape:
        con.executescript("""
          CREATE TABLE fitness_tests(id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT NOT NULL,
            movement TEXT NOT NULL,side TEXT NOT NULL DEFAULT 'bilateral',load_kg REAL,reps INTEGER,
            seconds REAL,rating INTEGER,cm REAL,equipment_note TEXT,source TEXT NOT NULL DEFAULT 'chat',
            voided INTEGER NOT NULL DEFAULT 0,void_reason TEXT,
            created_at TEXT DEFAULT (datetime('now')));
          ALTER TABLE fitness_tests ADD COLUMN degrees REAL;
          ALTER TABLE fitness_tests ADD COLUMN passed INTEGER;
          CREATE TABLE exercise_submuscles(exercise_title TEXT NOT NULL,muscle_group TEXT NOT NULL,
            sub_region TEXT NOT NULL,weight REAL NOT NULL DEFAULT 1.0,
            laterality TEXT NOT NULL DEFAULT 'bilateral'
              CHECK(laterality IN ('left','right','bilateral')),source TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(exercise_title,sub_region,laterality));
          ALTER TABLE exercise_submuscles ADD COLUMN approx INTEGER NOT NULL DEFAULT 0;
          ALTER TABLE exercise_submuscles ADD COLUMN iso INTEGER NOT NULL DEFAULT 0;
          ALTER TABLE exercise_submuscles ADD COLUMN confidence TEXT NOT NULL DEFAULT 'B'
            CHECK(confidence IN ('E','B'));
        """)
    con.execute("INSERT INTO daily_metrics VALUES('2026-01-01','apple',42)")
    con.execute("INSERT INTO hevy_sets(date,source) VALUES('2026-01-01',NULL)")
    con.commit(); con.close()
    return path


def schema_digest(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    con.close()
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def test_fresh_schema_plan_status_are_read_only(tmp_path):
    db = fresh(tmp_path)
    before = schema_digest(db)
    status = run(db, "schema-status")
    plan = run(db, "schema-plan", "--to", "2")
    assert status["current_version"] == 0 and status["ledger_present"] is True
    assert [s["version"] for s in plan["steps"]] == [1, 2]
    assert schema_digest(db) == before


def test_status_for_missing_database_is_one_json_error(tmp_path):
    result = run(tmp_path / "absent.db", "schema-status", expect=1)
    assert result["ok"] is False and result["error"]["code"] == "database_error"


def test_legacy_status_and_plan_never_create_ledger(tmp_path):
    db = legacy(tmp_path)
    before = schema_digest(db)
    assert run(db, "schema-status")["ledger_present"] is False
    assert run(db, "schema-plan", "--to", "2")["from_version"] == 0
    assert schema_digest(db) == before


def test_fresh_schema_records_both_checksums_and_repeat_is_noop(tmp_path):
    db = fresh(tmp_path)
    applied = run(db, "migrate", "--to", "2", "--expected-from", "0")
    assert applied["status"] == "migrated"
    assert [m["version"] for m in applied["applied"]] == [1, 2]
    status = run(db, "schema-status")
    assert status["current_version"] == 2 and status["status"] == "pending"
    assert all(len(row["checksum_sha256"]) == 64 for row in status["applied"])
    assert run(db, "migrate", "--to", "2", "--expected-from", "2")["status"] == "up_to_date"


def test_legacy_each_step_preserves_rows_and_only_hrv_copy(tmp_path):
    db = legacy(tmp_path)
    first = run(db, "migrate", "--to", "1", "--expected-from", "0")
    assert [x["version"] for x in first["applied"]] == [1]
    con = sqlite3.connect(db)
    assert con.execute("SELECT hrv_sdnn,hrv_ms FROM daily_metrics").fetchone() == (42.0, 42.0)
    assert con.execute("SELECT source FROM hevy_sets").fetchone()[0] == "hevy"
    assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    con.close()


@pytest.mark.parametrize("table", sorted(LAZY_TABLE_DDL))
def test_each_lazy_table_may_already_exist_at_its_exact_shape(tmp_path, table):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    con.executescript(LAZY_TABLE_DDL[table])
    con.commit(); con.close()
    result = run(db, "migrate", "--to", "1", "--expected-from", "0")
    assert result["applied"][0]["name"] == "001_reconcile_baseline"
    second = run(db, "migrate", "--to", "2", "--expected-from", "1")
    assert [x["version"] for x in second["applied"]] == [2]
    con = sqlite3.connect(db)
    assert con.execute("SELECT time,meal_type FROM nutrition_log").fetchall() == []
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()


@pytest.mark.parametrize("missing", sorted(LAZY_TABLE_DDL))
def test_each_lazy_table_may_be_independently_absent(tmp_path, missing):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    for table, ddl in LAZY_TABLE_DDL.items():
        if table != missing:
            con.executescript(ddl)
    con.commit(); con.close()
    assert run(db, "migrate", "--to", "2", "--expected-from", "0")["to_version"] == 2


@pytest.mark.parametrize("table", sorted(LAZY_TABLE_DDL))
def test_each_lazy_table_incompatible_extra_column_fails_preflight(tmp_path, table):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    con.executescript(LAZY_TABLE_DDL[table])
    con.execute(f'ALTER TABLE "{table}" ADD COLUMN phase2_wrong_extra BLOB')
    con.commit(); con.close()
    result = run(db, "schema-plan", "--to", "2", expect=1)
    assert result["error"]["code"] == "incompatible_schema"


@pytest.mark.parametrize("guard", sorted(ALL_GUARDS))
def test_each_guarded_column_may_be_independently_present(tmp_path, guard):
    table, column = guard
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    if table in PRE_GUARD_DDL and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        con.executescript(PRE_GUARD_DDL[table])
    elif table in LAZY_TABLE_DDL and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        con.executescript(LAZY_TABLE_DDL[table])
    columns = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
    if column not in columns:
        con.execute(ALL_GUARDS[guard])
    con.commit(); con.close()
    assert run(db, "migrate", "--to", "2", "--expected-from", "0")["to_version"] == 2


@pytest.mark.parametrize("guard", sorted(ALL_GUARDS))
def test_each_guarded_column_may_be_independently_absent(tmp_path, guard):
    table, column = guard
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    if guard == ("hevy_sets", "source"):
        con.executescript("""ALTER TABLE hevy_sets RENAME TO old_hevy_sets;
          CREATE TABLE hevy_sets(id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT);
          INSERT INTO hevy_sets(id,date) SELECT id,date FROM old_hevy_sets;
          DROP TABLE old_hevy_sets;""")
    elif table in PRE_GUARD_DDL and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        con.executescript(PRE_GUARD_DDL[table])
    elif table in LAZY_TABLE_DDL and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        con.executescript(LAZY_TABLE_DDL[table])
    assert column not in {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
    con.commit(); con.close()
    assert run(db, "migrate", "--to", "2", "--expected-from", "0")["to_version"] == 2


@pytest.mark.parametrize("guard", sorted(ALL_GUARDS))
def test_each_guarded_column_incompatible_declaration_fails(tmp_path, guard):
    table, column = guard
    db = legacy(tmp_path, incompatible=guard == ("hevy_sets", "source"))
    con = sqlite3.connect(db)
    if table in PRE_GUARD_DDL and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        con.executescript(PRE_GUARD_DDL[table])
    elif table in LAZY_TABLE_DDL and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        con.executescript(LAZY_TABLE_DDL[table])
    columns = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
    if column not in columns:
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" BLOB')
    con.commit(); con.close()
    assert run(db, "schema-plan", "--to", "2", expect=1)["error"]["code"] == "incompatible_schema"


def test_live_guard_column_order_is_compatible(tmp_path):
    db = legacy(tmp_path, live_shape=True)
    assert run(db, "migrate", "--to", "2", "--expected-from", "0")["to_version"] == 2


def test_expected_from_mismatch_writes_nothing(tmp_path):
    db = legacy(tmp_path)
    before = schema_digest(db)
    result = run(db, "migrate", "--to", "2", "--expected-from", "1", expect=2)
    assert result["error"]["code"] == "expected_from_mismatch"
    assert schema_digest(db) == before


def test_incompatible_guard_fails_plan_and_migrate_without_ledger(tmp_path):
    db = legacy(tmp_path, incompatible=True)
    assert run(db, "schema-plan", "--to", "2", expect=1)["error"]["code"] == "incompatible_schema"
    assert run(db, "migrate", "--to", "2", "--expected-from", "0", expect=1)["error"]["code"] == "incompatible_schema"
    con = sqlite3.connect(db)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() is None
    con.close()


def test_missing_lazy_unique_key_is_incompatible(tmp_path):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE commitments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,identity TEXT,
        trigger TEXT,floor TEXT,reward TEXT,active INTEGER DEFAULT 1,created TEXT)""")
    con.commit(); con.close()
    result = run(db, "schema-plan", "--to", "2", expect=1)
    assert result["error"]["code"] == "incompatible_schema"
    assert "unique key" in result["error"]["message"]


def test_extra_lazy_unique_key_is_incompatible(tmp_path):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    con.executescript(LAZY_TABLE_DDL["commitments"])
    con.execute("CREATE UNIQUE INDEX commitments_identity_extra_uq ON commitments(identity)")
    con.commit(); con.close()
    assert run(db, "schema-plan", "--to", "2", expect=1)["error"]["code"] == "incompatible_schema"


@pytest.mark.parametrize("kind", ["missing", "extra", "altered"])
def test_lazy_check_constraints_are_exact(kind, tmp_path):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    if kind == "missing":
        ddl = PRE_GUARD_DDL["exercise_submuscles"].replace(
            " CHECK(laterality IN ('left','right','bilateral'))", "")
    elif kind == "extra":
        ddl = LAZY_TABLE_DDL["routines_history"].replace(
            "\n);", ",\n  CHECK(undone IN (0,1))\n);")
    else:
        ddl = LAZY_TABLE_DDL["planned_times"].replace(
            "('wake','bed','workout','dose')", "('wake','bed','workout')")
    con.executescript(ddl)
    con.commit(); con.close()
    result = run(db, "schema-plan", "--to", "2", expect=1)
    assert result["error"]["code"] == "incompatible_schema"
    assert "CHECK" in result["error"]["message"]


def test_later_step_incompatibility_preflights_before_step_one_commit(tmp_path):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE event_exposures(id INTEGER PRIMARY KEY, wrong TEXT)")
    con.commit(); con.close()
    result = run(db, "migrate", "--to", "2", "--expected-from", "0", expect=1)
    assert result["error"]["code"] == "incompatible_schema"
    con = sqlite3.connect(db)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() is None
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='commitments'").fetchone() is None
    con.close()


def test_conflicting_named_phase2_index_preflights_before_step_one_commit(tmp_path):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE unrelated_index_owner(date TEXT, category TEXT)")
    con.execute("""CREATE INDEX event_exposures_date_category_idx
        ON unrelated_index_owner(date,category)""")
    con.commit(); con.close()
    result = run(db, "migrate", "--to", "2", "--expected-from", "0", expect=1)
    assert result["error"]["code"] == "incompatible_schema"
    con = sqlite3.connect(db)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() is None
    con.close()


def test_fresh_schema_and_migrated_legacy_owned_shapes_are_equivalent(tmp_path):
    fresh_db = fresh(tmp_path)
    legacy_db = legacy(tmp_path)
    run(fresh_db, "migrate", "--to", "2", "--expected-from", "0")
    run(legacy_db, "migrate", "--to", "2", "--expected-from", "0")
    tables = {
        "schema_migrations", "routines_history", "commitments", "commitments_log",
        "checkins", "planned_times", "nutrient_daily", "nutrition_targets",
        "owner_profile", "fitness_tests", "athletic_targets", "exercise_submuscles",
        "pain_log", "self_test_log", "exercise_trial_log", "lab_catalog",
        "event_exposures", "raw_capture_entries", "raw_capture_resolutions",
        "capture_completeness_revisions", "entity_aliases",
    }
    fresh_con, legacy_con = sqlite3.connect(fresh_db), sqlite3.connect(legacy_db)
    try:
        for table in tables:
            fresh_cols = [tuple(row[1:6]) for row in fresh_con.execute(f"PRAGMA table_info('{table}')")]
            legacy_cols = [tuple(row[1:6]) for row in legacy_con.execute(f"PRAGMA table_info('{table}')")]
            assert legacy_cols == fresh_cols, table
        for index in (
            "event_exposures_date_category_idx", "event_exposures_entity_idx",
            "raw_capture_resolution_latest_idx", "capture_completeness_latest_idx",
            "entity_alias_latest_idx",
        ):
            fresh_sql = fresh_con.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)
            ).fetchone()[0]
            legacy_sql = legacy_con.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)
            ).fetchone()[0]
            normalize = lambda value: "".join(value.lower().replace("if not exists", "").split()).rstrip(";")
            assert normalize(fresh_sql) == normalize(legacy_sql), index
    finally:
        fresh_con.close(); legacy_con.close()


def test_recorded_checksum_drift_fails_status(tmp_path):
    db = fresh(tmp_path)
    run(db, "migrate", "--to", "1", "--expected-from", "0")
    con = sqlite3.connect(db)
    con.execute("UPDATE schema_migrations SET checksum_sha256=? WHERE version=1", ("0" * 64,))
    con.commit(); con.close()
    assert run(db, "schema-status", expect=1)["error"]["code"] == "migration_checksum_mismatch"


def test_plan_validates_already_applied_shapes_and_indexes(tmp_path):
    db = fresh(tmp_path)
    run(db, "migrate", "--to", "2", "--expected-from", "0")
    con = sqlite3.connect(db)
    con.execute("DROP INDEX event_exposures_entity_idx")
    con.commit(); con.close()
    assert run(db, "schema-plan", "--to", "2", expect=1)["error"]["code"] == "incompatible_schema"


def test_migration_required_replaces_lazy_writer_ddl(tmp_path):
    db = legacy(tmp_path)
    result = run(db, "routine-set", "R", "Squat", "--sets", "3", expect=1)
    assert result["error"]["code"] == "schema_migration_required"
    con = sqlite3.connect(db)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='routines_history'").fetchone() is None
    con.close()


def test_old_phase1_health_can_read_migrated_additive_schema(tmp_path):
    db = fresh(tmp_path)
    run(db, "migrate", "--to", "3", "--expected-from", "0")
    old_reader = ROOT / "tests" / "fixtures" / "legacy_schema_reader.py"
    result = subprocess.run([sys.executable, str(old_reader), "schema", "nutrition_log"],
                            env={**os.environ, "HEALTH_DB": str(db)}, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "meal_type" in json.loads(result.stdout)["columns"][-1]["name"]


def test_migration_003_from_version_2_seeds_one_canonical_plan_snapshot(tmp_path):
    db = fresh(tmp_path)
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO training_schedule VALUES(?,?)", [
        ("Wed", "Run"), ("Mon", "Strength"),
    ])
    con.executemany("INSERT INTO routines VALUES(?,?,?,?,?,?)", [
        ("Strength", "Squat", 2, 3, 5, 80.0),
        ("Strength", "Bench", 1, 3, 5, 60.0),
    ])
    con.commit(); con.close()
    run(db, "migrate", "--to", "2", "--expected-from", "0")

    result = run(db, "migrate", "--to", "3", "--expected-from", "2")
    assert result["applied"] == [{
        "version": 3,
        "name": "003_feature_config_and_provenance",
        "checksum_sha256": MIGRATION_BY_VERSION[3].checksum,
    }]
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    revision = con.execute("SELECT * FROM training_plan_revisions").fetchone()
    assert revision["source"] == "migration-003" and revision["supersedes_id"] is None
    assert json.loads(revision["schedule_json"]) == [
        {"routine_name": "Strength", "weekday": "Mon"},
        {"routine_name": "Run", "weekday": "Wed"},
    ]
    assert [row["exercise_title"] for row in json.loads(revision["routines_json"])] == [
        "Bench", "Squat",
    ]
    assert len(revision["effective_from"]) == 10
    assert canonical_training_plan_snapshot(con) == canonical_plan(con)
    con.close()

    assert migrate_direct(str(db), 3, 3, CODE_VERSION)["status"] == "up_to_date"
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM training_plan_revisions").fetchone()[0] == 1
    con.close()


def test_exact_phase2_handoff_schema_migrates_0_to_1_to_2_to_3_additively(tmp_path):
    db = transitional(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO training_schedule VALUES('Mon','Strength')")
    con.execute("INSERT INTO routines VALUES('Strength','Bench',1,3,5,60.0)")
    before = {
        name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in ("training_schedule", "routines", "hevy_sets", "daily_metrics")
    }
    con.commit(); con.close()

    result = migrate_direct(str(db), 3, 0, CODE_VERSION)
    assert [step["version"] for step in result["applied"]] == [1, 2, 3]
    con = sqlite3.connect(db)
    assert {
        name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in before
    } == before
    assert con.execute("SELECT COUNT(*) FROM training_plan_revisions").fetchone()[0] == 1
    assert con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
    con.close()


def test_unserializable_plan_seed_preflights_before_any_migration_write(tmp_path):
    db = transitional(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO routines VALUES('Strength','Bench',1,3,5,?)", (float("inf"),))
    con.commit(); con.close()
    before = schema_digest(db)

    with pytest.raises(SchemaError) as failure:
        migrate_direct(str(db), 3, 0, CODE_VERSION)
    assert failure.value.code == "incompatible_schema"
    assert schema_digest(db) == before
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    con.close()


def test_phase3_fresh_schema_and_migrated_shapes_are_exact(tmp_path):
    fresh_dir, migrated_dir = tmp_path / "fresh-tree", tmp_path / "migrated-tree"
    fresh_dir.mkdir(); migrated_dir.mkdir()
    fresh_db = fresh(fresh_dir)
    migrated_db = fresh(migrated_dir)
    con = sqlite3.connect(migrated_db)
    for index in PHASE3_INDEX_DDL:
        con.execute(f'DROP INDEX "{index}"')
    for table in reversed(tuple(PHASE3_TABLE_DDL)):
        con.execute(f'DROP TABLE "{table}"')
    con.commit(); con.close()

    run(fresh_db, "migrate", "--to", "3", "--expected-from", "0")
    run(migrated_db, "migrate", "--to", "3", "--expected-from", "0")
    left, right = sqlite3.connect(fresh_db), sqlite3.connect(migrated_db)
    try:
        for table in PHASE3_TABLE_DDL:
            assert list(left.execute(f"PRAGMA table_info('{table}')")) == list(
                right.execute(f"PRAGMA table_info('{table}')")
            )
        normalize = lambda value: "".join(value.lower().replace("if not exists", "").split()).rstrip(";")
        for index in PHASE3_INDEX_DDL:
            lsql = left.execute("SELECT sql FROM sqlite_master WHERE name=?", (index,)).fetchone()[0]
            rsql = right.execute("SELECT sql FROM sqlite_master WHERE name=?", (index,)).fetchone()[0]
            assert normalize(lsql) == normalize(rsql)
    finally:
        left.close(); right.close()


def test_phase3_conflicting_index_fails_before_earlier_migration_write(tmp_path):
    db = legacy(tmp_path)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE routines(routine_name TEXT,exercise_title TEXT,ex_order INTEGER,"
                "target_sets INTEGER,target_reps INTEGER,target_weight_kg REAL,"
                "PRIMARY KEY(routine_name,exercise_title))")
    con.execute("CREATE TABLE training_schedule(weekday TEXT PRIMARY KEY,routine_name TEXT)")
    con.execute("CREATE TABLE wrong_goal_index(goal_key TEXT,id INTEGER)")
    con.execute("CREATE INDEX insight_goal_latest_idx ON wrong_goal_index(goal_key,id)")
    con.commit(); con.close()

    result = run(db, "migrate", "--to", "3", "--expected-from", "0", expect=1)
    assert result["error"]["code"] == "incompatible_schema"
    con = sqlite3.connect(db)
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() is None
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='commitments'").fetchone() is None
    con.close()


def test_phase3_constraint_drift_fails_read_only_plan(tmp_path):
    db = transitional(tmp_path)
    migrate_direct(str(db), 2, 0, CODE_VERSION)
    con = sqlite3.connect(db)
    con.execute(PHASE3_TABLE_DDL["insight_goal_revisions"].replace(
        " CHECK(enabled IN (0,1))", ""))
    con.commit(); con.close()
    before = schema_digest(db)
    with pytest.raises(SchemaError) as failure:
        schema_plan_direct(str(db), 3)
    assert failure.value.code == "incompatible_schema"
    assert schema_digest(db) == before


def test_applied_phase3_shape_and_index_drift_fail_status(tmp_path):
    db = fresh(tmp_path)
    run(db, "migrate", "--to", "3", "--expected-from", "0")
    con = sqlite3.connect(db)
    con.execute("DROP INDEX source_sync_runs_latest_idx")
    con.commit(); con.close()
    assert run(db, "schema-status", expect=1)["error"]["code"] == "incompatible_schema"


def test_migration_003_runtime_failure_rolls_back_seed_and_ledger(tmp_path):
    db = fresh(tmp_path)
    migrate_direct(str(db), 2, 0, CODE_VERSION)
    con = sqlite3.connect(db)
    for ddl in PHASE3_TABLE_DDL.values():
        con.execute(ddl)
    for ddl in PHASE3_INDEX_DDL.values():
        con.execute(ddl)
    con.execute("""CREATE TRIGGER reject_plan_seed
                   BEFORE INSERT ON training_plan_revisions
                   BEGIN SELECT RAISE(ABORT, 'fixture rejection'); END""")
    con.commit(); con.close()

    with pytest.raises(sqlite3.IntegrityError, match="fixture rejection"):
        migrate_direct(str(db), 3, 2, CODE_VERSION)
    con = sqlite3.connect(db)
    assert con.execute(
        "SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 2
    assert con.execute(
        "SELECT COUNT(*) FROM training_plan_revisions").fetchone()[0] == 0
    con.close()
