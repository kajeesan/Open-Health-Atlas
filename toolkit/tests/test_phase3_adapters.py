"""Boundary and missingness tests for the Phase 3 domain adapters."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import pathlib
import sqlite3
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_insights.adapters import (
    DefinitionIndex, identity_parts, make_observation, table_columns,
)
from hermes_insights.adapters import (
    adherence, daily, environment, events, labs, manual, nutrition, pain,
    quarterly, running, training,
)
from hermes_insights.contracts import AdapterContext, DateRange
from hermes_insights.frame import build_feature_frame
from hermes_insights.goals import collector_freshness
from hermes_insights.provenance import dependency_snapshot, input_fingerprint
from hermes_insights.readiness import build_readiness
from hermes_insights.registry import build_registry, registry_content_checksum

import health as toolkit_health


def db(schema: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    return conn


def defs(*keys):
    return {key: {"key": key, "unit": "value"} for key in keys}


def ctx(*, constants=None, functions=None, today=date(2026, 7, 23)):
    return AdapterContext(
        today=today, timezone="Europe/Paris",
        constants=constants or {}, functions=functions or {},
    )


def bounded(start="2026-07-01", end="2026-07-31"):
    return DateRange(date.fromisoformat(start), date.fromisoformat(end))


def test_table_introspection_stays_inside_select_authorizer():
    conn = db("CREATE TABLE sample(id INTEGER,date TEXT);")
    writes = {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_ALTER_TABLE,
    }

    def authorize(action, arg1, _arg2, _db, _trigger):
        if action in writes:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorize)
    assert table_columns(conn, "sample") == {"id", "date"}
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("PRAGMA user_version").fetchone()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT INTO sample VALUES(1,'2026-07-01')")


def test_explicit_adapter_unit_is_not_silently_relabelled_from_registry():
    definitions = DefinitionIndex({
        "training.loaded_volume_kg": {
            "key": "training.loaded_volume_kg", "unit": "kg_reps",
        },
    })
    observation = make_observation(
        definitions, "training.loaded_volume_kg", "2026-07-01", 100,
        unit="wrong-unit",
    )
    assert observation.unit == "wrong-unit"


def test_static_calendar_registry_entries_never_become_observations():
    definitions = defs("calendar.weekday", "calendar.weekend", "calendar.season")
    conn = db("CREATE TABLE dated(date TEXT);")
    conn.execute("INSERT INTO dated VALUES('2026-07-01')")
    assert environment.load(
        conn, definitions, bounded("2026-07-01", "2026-07-02"), ctx()) == []


def test_collector_freshness_uses_shared_fractional_30_hour_boundary():
    conn = db("""
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,source TEXT,started_at TEXT,completed_at TEXT,
          status TEXT,coverage_from TEXT,coverage_to TEXT,rows_seen INTEGER,
          rows_written INTEGER,error_code TEXT,details_json TEXT);
    """)
    conn.executemany(
        "INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            (1, "google-health", "2026-07-22T05:00:00+02:00",
             "2026-07-22T06:00:00+02:00", "success", "2026-07-21",
             "2026-07-21", 1, 1, None, "{}"),
            (2, "google-health", "2026-07-22T09:00:00+02:00",
             "2026-07-22T10:00:00+02:00", "failed", None, None,
             0, 0, "collection_failed", "{}"),
        ])
    definitions = defs(
        "source.collector.google-health.freshness_days",
        "source.collector.google-health.status",
    )
    now = datetime(2026, 7, 23, 12, 0,
                   tzinfo=timezone(timedelta(hours=2)))
    freshness = collector_freshness(conn, "google-health", now=now)
    observations = daily.load(
        conn, definitions, bounded("2026-07-23", "2026-07-23"),
        ctx(functions={"now": lambda: now}),
        include_provenance=True,
    )
    assert freshness["freshness_days"] == 1.25
    assert freshness["status"] == "fresh"
    assert freshness["run_status"] == "failed"
    assert observations == []  # STATIC_CONFIG readiness evidence, never N.


def test_recovery_baseline_is_immediately_prior_14_same_source_values():
    conn = db("""
        CREATE TABLE daily_metrics(
          date TEXT,source TEXT,resting_hr REAL,hrv_ms REAL,
          PRIMARY KEY(date,source));
    """)
    start = date(2026, 7, 1)
    conn.executemany(
        "INSERT INTO daily_metrics VALUES(?,?,?,?)",
        [((start + timedelta(days=i)).isoformat(), "fitbit", 60 + i, 40 + i)
         for i in range(16)],
    )
    seen = []

    def score(value, baseline, _coef, invert):
        seen.append((invert, list(baseline)))
        return len(baseline), 0

    observations = daily.load(
        conn, defs("recovery.rhr_score.fitbit"), bounded("2026-07-01", "2026-07-31"),
        ctx(functions={"dev_score": score}),
    )
    rhr_calls = [baseline for invert, baseline in seen if invert]
    assert [len(value) for value in rhr_calls] == list(range(15)) + [14]
    assert rhr_calls[-1] == list(range(61, 75))
    assert observations[-1].value == 14


def test_sleep_daily_fallback_accepts_a_single_non_fitbit_provider():
    conn = db("""
        CREATE TABLE daily_metrics(
          date TEXT,source TEXT,sleep_hours REAL,PRIMARY KEY(date,source));
    """)
    conn.execute(
        "INSERT INTO daily_metrics VALUES('2026-07-01','apple',7.5)"
    )
    observations = daily.load(
        conn, defs("sleep.duration_hours"),
        bounded("2026-07-01", "2026-07-01"), ctx(),
        include_provenance=True,
    )
    assert [(item.feature_key, item.value, item.source) for item in observations] == [
        ("sleep.duration_hours", 7.5, "apple"),
    ]
    assert observations[0].provenance["selected_provider"] == "apple"


def test_daily_units_survive_strict_full_registry_frame():
    conn = db((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        "INSERT INTO daily_metrics(date,source,respiratory_rate,spo2_pct) "
        "VALUES('2026-07-01','fitbit',14.5,98)"
    )
    conn.execute(
        "INSERT INTO sleep_log(date,time_asleep_hours,time_in_bed_hours,source) "
        "VALUES('2026-07-01',7.5,8,'manual')"
    )
    runtime = toolkit_health._phase3_context()
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone,
        runtime.constants, runtime.functions,
    )
    definitions = [
        item for item in build_registry(conn, context)
        if item.adapter == "daily"
    ]
    frame = build_feature_frame(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), context,
    )
    by_key = {item["feature_key"]: item for item in frame["observations"]}
    assert by_key["wearable.respiratory_rate_brpm"]["unit"] == "breaths_per_min"
    assert by_key["wearable.spo2_pct"]["unit"] == "percent"
    assert by_key["sleep.duration_hours"]["unit"] == "hours"
    assert by_key["sleep.efficiency"]["unit"] == "ratio"


def test_medication_alias_mapping_uses_only_exact_authorized_normalization():
    conn = db("""
        CREATE TABLE meds_log(
          id INTEGER PRIMARY KEY,date TEXT,drug TEXT,dose_mg REAL,
          time_taken TEXT,rebound INTEGER,source TEXT);
    """)
    conn.execute(
        "INSERT INTO meds_log VALUES(1,'2026-07-01',' ＭＥＤＩＣＡＴＩＯＮ-ＡＬＩＡＳ ',5,"
        "'08:00',0,'manual')"
    )
    _identity, token, _ = identity_parts("medication", "medication")
    key = f"medication.{token}.dose_mg"
    observations = daily.load(
        conn, defs(key), bounded("2026-07-01", "2026-07-01"),
        ctx(constants={"MEDICATION_ALIASES": {"medication-alias"}}),
        include_provenance=True,
    )
    assert [(item.feature_key, item.value) for item in observations] == [(key, 5)]
    assert observations[0].provenance["identity_rules"] == ["configured_medication_alias"]


def test_daily_multirow_aggregates_are_order_stable_and_label_mixed_sources():
    conn = db("""
        CREATE TABLE checkins(
          id INTEGER PRIMARY KEY,date TEXT,time TEXT,kind TEXT,value INTEGER,source TEXT);
        CREATE TABLE meds_log(
          id INTEGER PRIMARY KEY,date TEXT,drug TEXT,dose_mg REAL,
          time_taken TEXT,rebound INTEGER,source TEXT);
    """)
    conn.executemany("INSERT INTO checkins VALUES(?,?,?,?,?,?)", [
        (2, "2026-07-01", "09:00", "energy", 5, "panel"),
        (1, "2026-07-01", "08:00", "energy", 3, "chat"),
    ])
    conn.executemany("INSERT INTO meds_log VALUES(?,?,?,?,?,?,?)", [
        (2, "2026-07-01", "medication-alias", 10, "12:00", 0, "panel"),
        (1, "2026-07-01", "medication-alias", 5, "08:00", 0, "chat"),
    ])
    _identity, token, _ = identity_parts("medication", "medication")
    definitions = defs(
        "subjective.checkin.energy.am", f"medication.{token}.dose_mg",
    )
    parse = lambda value: int(value[:2]) * 60 + int(value[3:])
    context = ctx(
        constants={"MEDICATION_ALIASES": {"medication-alias"}},
        functions={"hhmm_min": parse},
    )
    first = daily.load(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), context,
        include_provenance=True,
    )
    conn.execute("PRAGMA reverse_unordered_selects=ON")
    second = daily.load(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), context,
        include_provenance=True,
    )
    assert first == second
    assert {item.source for item in first} == {"mixed"}
    medication = next(item for item in first if item.feature_key.startswith("medication."))
    assert medication.provenance["natural_keys"] == ["meds_log:1", "meds_log:2"]


def test_unclassified_hrv_and_cardio_dynamic_namespaces_match_registry():
    daily_conn = db("""
        CREATE TABLE daily_metrics(
          date TEXT,source TEXT,hrv_ms REAL,PRIMARY KEY(date,source));
    """)
    daily_conn.execute("INSERT INTO daily_metrics VALUES('2026-07-01','ring-x',42)")
    hrv_identity, hrv_token, _ = identity_parts("source", "ring-x")
    observed = daily.load(
        daily_conn, defs(f"wearable.hrv.unclassified.{hrv_token}"),
        bounded("2026-07-01", "2026-07-01"), ctx(),
        include_provenance=True)
    assert [(item.feature_key, item.value) for item in observed] == [
        (f"wearable.hrv.unclassified.{hrv_token}", 42),
    ]
    assert observed[0].provenance["identity_key"] == hrv_identity
    assert observed[0].provenance["original_provider"] == "ring-x"

    cardio_conn = db("""
        CREATE TABLE workouts(date TEXT,type TEXT,minutes REAL,kcal REAL,km REAL,source TEXT);
    """)
    cardio_conn.execute("INSERT INTO workouts VALUES('2026-07-01','Cycling',20,100,5,'manual')")
    _identity, cardio_token, _ = identity_parts("cardio_type", "cycling")
    observed = running.load(
        cardio_conn, defs(f"cardio.type.{cardio_token}.session"),
        bounded("2026-07-01", "2026-07-01"), ctx())
    assert [(item.feature_key, item.value) for item in observed] == [
        (f"cardio.type.{cardio_token}.session", 1),
    ]


def test_training_volume_distinguishes_warmup_zero_missing_and_partial_known():
    conn = db("""
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY,date TEXT,workout_title TEXT,start_time TEXT,
          exercise_title TEXT,set_type TEXT,weight_kg REAL,reps INTEGER,rpe REAL,
          duration_seconds REAL,distance_km REAL,source TEXT);
    """)
    conn.executemany(
        "INSERT INTO hevy_sets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
            (1, "2026-07-01", "A", None, "Press", "warmup", 10, 10, None, None, None, "hevy"),
            (2, "2026-07-02", "A", None, "Press", "normal", None, 5, None, None, None, "hevy"),
            (3, "2026-07-03", "A", None, "Press", "normal", 10, 2, None, None, None, "hevy"),
            (4, "2026-07-03", "A", None, "Press", "normal", None, 2, None, None, None, "hevy"),
        ])
    observations = training.load(
        conn, defs("training.loaded_volume_kg", "training.working_sets"),
        bounded("2026-07-01", "2026-07-03"), ctx(), include_provenance=True,
    )
    volumes = {item.observed_at: item.value for item in observations
               if item.feature_key == "training.loaded_volume_kg"}
    assert volumes == {"2026-07-01": 0, "2026-07-03": 20}
    assert "2026-07-02" not in volumes
    _identity, token, _ = identity_parts("exercise", "Press")
    exercise = training.load(
        conn, defs(f"training.exercise.{token}.session"),
        bounded("2026-07-01", "2026-07-01"), ctx())
    assert [(item.value, item.state) for item in exercise] == [(0, "observed")]


def test_warmup_only_day_is_zero_for_global_workout_and_matched_routine_sessions():
    conn = db("""
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY,date TEXT,workout_title TEXT,exercise_title TEXT,
          set_type TEXT,weight_kg REAL,reps INTEGER,source TEXT);
        CREATE TABLE training_plan_revisions(
          id INTEGER PRIMARY KEY,effective_from TEXT,schedule_json TEXT,
          routines_json TEXT);
    """)
    conn.execute(
        "INSERT INTO hevy_sets VALUES(1,'2026-07-01','Base','Press',"
        "'warmup',10,10,'hevy')"
    )
    conn.execute(
        "INSERT INTO training_plan_revisions VALUES(1,'2026-07-01',?,?)",
        (
            '[{"weekday":"Wed","routine_name":"Base"}]',
            '[{"routine_name":"Base","exercise_title":"Press","target_sets":3}]',
        ),
    )
    _identity, workout_token, _ = identity_parts("workout", "Base")
    _identity, routine_token, _ = identity_parts("routine", "Base")
    definitions = defs(
        "training.session",
        f"training.workout.{workout_token}.session",
        f"training.routine.{routine_token}.session",
    )
    observations = training.load(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), ctx(),
    )
    assert {
        (item.feature_key, item.value, item.state) for item in observations
    } == {
        ("training.session", 0, "observed"),
        (f"training.workout.{workout_token}.session", 0, "observed"),
        (f"training.routine.{routine_token}.session", 0, "observed"),
    }


def test_training_exact_normalized_titles_merge_and_preserve_original_labels():
    conn = db("""
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY,date TEXT,workout_title TEXT,exercise_title TEXT,
          set_type TEXT,weight_kg REAL,reps INTEGER,source TEXT);
    """)
    conn.executemany("INSERT INTO hevy_sets VALUES(?,?,?,?,?,?,?,?)", [
        (1, "2026-07-01", "Push", "Bench", "normal", 50, 5, "hevy"),
        (2, "2026-07-01", " push ", " bench ", "normal", 50, 5, "hevy"),
    ])
    _identity, exercise_token, _ = identity_parts("exercise", "bench")
    _identity, workout_token, _ = identity_parts("workout", "push")
    exercise_key = f"training.exercise.{exercise_token}.session"
    workout_key = f"training.workout.{workout_token}.session"
    observations = training.load(
        conn, defs(exercise_key, workout_key),
        bounded("2026-07-01", "2026-07-01"), ctx(),
        include_provenance=True,
    )
    assert [(item.feature_key, item.value) for item in observations] == [
        (exercise_key, 1), (workout_key, 1),
    ]
    assert next(
        item for item in observations if item.feature_key == exercise_key
    ).provenance["original_labels"] == [" bench ", "Bench"]


def test_training_loaded_volume_units_survive_strict_full_registry_frame():
    conn = db((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        "INSERT INTO hevy_sets(date,workout_title,exercise_title,set_type,"
        "weight_kg,reps,rpe,duration_seconds,source) VALUES(?,?,?,?,?,?,?,?,?)",
        ("2026-07-01", "Push", "Bench", "normal", 50, 5, 7, 60, "hevy"),
    )
    runtime = toolkit_health._phase3_context()
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone,
        runtime.constants, runtime.functions,
    )
    definitions = [
        item for item in build_registry(conn, context)
        if item.adapter == "training"
    ]
    frame = build_feature_frame(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), context,
    )
    _identity, token, _ = identity_parts("exercise", "Bench")
    by_key = {item["feature_key"]: item for item in frame["observations"]}
    assert by_key["training.loaded_volume_kg"]["unit"] == "kg_reps"
    assert by_key[f"training.exercise.{token}.loaded_volume_kg"]["unit"] == "kg_reps"
    assert by_key["training.mean_rpe"]["unit"] == "rpe_0_10"
    assert by_key[f"training.exercise.{token}.mean_rpe"]["unit"] == "rpe_0_10"
    assert by_key[f"training.exercise.{token}.duration_sec"]["unit"] == "seconds"


def test_unmapped_working_set_does_not_fabricate_zero_muscle_exposure():
    conn = db("""
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY,date TEXT,exercise_title TEXT,set_type TEXT,
          weight_kg REAL,reps INTEGER,source TEXT);
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,source TEXT,status TEXT,coverage_from TEXT,
          coverage_to TEXT,completed_at TEXT);
    """)
    conn.execute("INSERT INTO hevy_sets VALUES(1,'2026-07-01','Unknown','normal',10,5,'hevy')")
    conn.execute("INSERT INTO source_sync_runs VALUES(1,'hevy','success','2026-07-01','2026-07-01','2026-07-02T00:00:00Z')")

    def maps(_conn):
        return {}, {}

    def basis(_title, _authored, _coarse):
        return None, "unmapped"

    observations = training.load(
        conn, defs("training.group.chest.effective_sets"),
        bounded("2026-07-01", "2026-07-01"),
        ctx(functions={"group_weight_maps": maps, "basis_weights": basis}),
    )
    assert observations == []


def test_load_days_since_uses_latest_observed_positive_across_an_unproven_gap():
    anchor = date(2026, 7, 10)
    complete = {
        (anchor - timedelta(days=offset)).isoformat(): 0.0
        for offset in range(10)
    }
    complete["2026-07-05"] = 2
    assert training.window_summary(complete, anchor, 7)["days_since"] == 5
    del complete["2026-07-08"]
    assert training.window_summary(complete, anchor, 7)["days_since"] == 5


def test_running_exact_mapping_and_complete_gap_boundaries():
    conn = db("""
        CREATE TABLE workouts(date TEXT,type TEXT,minutes REAL,kcal REAL,km REAL,source TEXT);
    """)
    conn.executemany("INSERT INTO workouts VALUES(?,?,?,?,?,?)", [
        ("2026-07-01", "Trail Running", 40, 300, 8, "manual"),
        ("2026-07-02", "Outdoor Running", 30, 240, 5, "manual"),
    ])
    observations = running.load(
        conn, defs("running.session"), bounded("2026-07-01", "2026-07-02"),
        ctx(constants={"RUN_TYPE_KEYS": {
            "running": "running", "outdoor running": "running",
            "indoor running": "running", "treadmill running": "running",
        }}),
    )
    assert [(item.observed_at, item.value) for item in observations] == [("2026-07-02", 1)]

    complete = {(date(2026, 7, 1) + timedelta(days=i)).isoformat() for i in range(22)}
    runs = {"2026-06-30", "2026-07-22"}
    stops, restarts = running.stop_restart_events(runs, complete)
    assert stops[0]["confirmed_at"] == "2026-07-21"
    assert restarts[0]["date"] == "2026-07-22"
    incomplete = complete - {"2026-07-11"}
    assert running.stop_restart_events(runs, incomplete) == ([], [])


def test_running_confirmed_stop_survives_later_missing_coverage_and_sunday_counts():
    runs = {"2026-01-01", "2026-01-24"}
    complete = {
        (date(2026, 1, 2) + timedelta(days=offset)).isoformat()
        for offset in range(21)
    }
    stops, restarts = running.stop_restart_events(runs, complete)
    assert stops == [{
        "date": "2026-01-02",
        "confirmed_at": "2026-01-22",
        "prior_run": "2026-01-01",
    }]
    assert restarts == []

    sunday = date(2026, 1, 11)
    complete_week = {
        (date(2026, 1, 5) + timedelta(days=offset)).isoformat()
        for offset in range(7)
    }
    assert running.consecutive_complete_running_weeks(
        sunday, {"2026-01-07"}, complete_week,
    ) == 1


def test_running_completeness_zeros_session_only_and_restart_keeps_prior_outside_range():
    conn = db("""
        CREATE TABLE workouts(date TEXT,type TEXT,minutes REAL,kcal REAL,km REAL,source TEXT);
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,source TEXT,status TEXT,coverage_from TEXT,
          coverage_to TEXT,completed_at TEXT);
    """)
    conn.executemany("INSERT INTO workouts VALUES(?,?,?,?,?,?)", [
        ("2026-06-01", "Running", 60, 500, 10, "fixture"),
        ("2026-06-23", "Running", 30, 250, 5, "fixture"),
    ])
    conn.execute("INSERT INTO source_sync_runs VALUES(1,'fixture','success',"
                 "'2026-06-02','2026-06-24','2026-06-25T00:00:00Z')")
    observations = running.load(
        conn, defs("running.session", "running.duration_min", "running.distance_km",
                   "running.kcal", "running.restart"),
        bounded("2026-06-23", "2026-06-24"),
        ctx(constants={
            "RUN_TYPE_KEYS": {"running": "running"},
            "RUN_COMPLETENESS_SOURCES": {"fixture"},
        }), include_provenance=True,
    )
    no_run = [item for item in observations if item.observed_at == "2026-06-24"]
    assert [(item.feature_key, item.state, item.value) for item in no_run] == [
        ("running.session", "structural_zero", 0),
    ]
    restart = next(item for item in observations if item.feature_key == "running.restart")
    assert restart.provenance["prior_duration_min"] == 60
    assert restart.provenance["prior_distance_km"] == 10


def test_running_days_since_needs_a_prior_observed_run_not_completeness():
    conn = db("""
        CREATE TABLE workouts(date TEXT,type TEXT,minutes REAL,kcal REAL,km REAL,source TEXT);
    """)
    conn.execute(
        "INSERT INTO workouts VALUES('2026-07-01','Running',30,200,5,'manual')"
    )
    observations = running.load(
        conn, defs("running.days_since"),
        bounded("2026-07-02", "2026-07-02"),
        ctx(constants={"RUN_TYPE_KEYS": {"running": "running"}}),
        include_provenance=True,
    )
    assert [(item.observed_at, item.value) for item in observations] == [
        ("2026-07-02", 1),
    ]
    assert observations[0].provenance["does_not_assert_intervening_absence"] is True


def test_running_complete_zero_week_retains_sync_lineage():
    conn = db("""
        CREATE TABLE workouts(date TEXT,type TEXT,minutes REAL,kcal REAL,km REAL,source TEXT);
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,source TEXT,status TEXT,coverage_from TEXT,
          coverage_to TEXT,completed_at TEXT);
    """)
    conn.executemany("INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?)", [
        (1, "fixture", "success", "2026-07-06", "2026-07-09",
         "2026-07-10T00:00:00Z"),
        (2, "fixture", "success", "2026-07-10", "2026-07-12",
         "2026-07-13T00:00:00Z"),
    ])
    observations = running.load(
        conn, defs("running.progress.weekly_frequency"),
        bounded("2026-07-12", "2026-07-12"),
        ctx(constants={"RUN_COMPLETENESS_SOURCES": {"fixture"}}),
        include_provenance=True,
    )
    weekly = next(item for item in observations
                  if item.feature_key == "running.progress.weekly_frequency")
    assert weekly.value == 0
    assert weekly.provenance["source_sync_run_ids"] == [1, 2]


def test_running_units_survive_strict_full_registry_frame():
    conn = db((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        "INSERT INTO workouts(date,type,minutes,kcal,km,source) "
        "VALUES('2026-07-06','Running',30,300,5,'fixture')"
    )
    conn.execute(
        "INSERT INTO source_sync_runs("
        "source,started_at,completed_at,status,coverage_from,coverage_to"
        ") VALUES('fixture','2026-07-13T00:00:00Z','2026-07-13T00:01:00Z',"
        "'success','2026-07-06','2026-07-12')"
    )
    runtime = toolkit_health._phase3_context()
    constants = dict(runtime.constants)
    constants["RUN_COMPLETENESS_SOURCES"] = {"fixture"}
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone, constants, runtime.functions,
    )
    definitions = [
        item for item in build_registry(conn, context)
        if item.adapter == "running"
    ]
    frame = build_feature_frame(
        conn, definitions, bounded("2026-07-06", "2026-07-12"), context,
    )
    observed = {
        item["feature_key"]: item for item in frame["observations"]
        if item["state"] in {"observed", "structural_zero"}
    }
    assert observed["running.pace_min_per_km"]["unit"] == "min_per_km"
    assert observed["running.intensity_kcal_per_min"]["unit"] == "kcal_per_min"
    assert observed["running.consecutive_weeks"]["unit"] == "count"
    assert observed["running.progress.weekly_duration_min"]["unit"] == "min_per_week"
    assert observed["running.progress.weekly_distance_km"]["unit"] == "km_per_week"
    assert observed["running.progress.weekly_frequency"]["unit"] == "runs_per_week"
    assert observed["running.progress.weekly_pace_min_per_km"]["unit"] == "min_per_km"


def test_running_current_derivatives_remain_visible_to_all_range_readiness():
    conn = db((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        "INSERT INTO workouts(date,type,minutes,kcal,km,source) "
        "VALUES('2026-07-10','Running',30,300,5,'fixture')"
    )
    conn.execute(
        "INSERT INTO source_sync_runs("
        "source,started_at,completed_at,status,coverage_from,coverage_to"
        ") VALUES('fixture','2026-07-20T00:00:00Z','2026-07-20T00:01:00Z',"
        "'success','2026-07-06','2026-07-19')"
    )
    runtime = toolkit_health._phase3_context()
    constants = dict(runtime.constants)
    constants["RUN_COMPLETENESS_SOURCES"] = {"fixture"}
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone, constants, runtime.functions,
    )
    wanted = {"running.days_since", "running.consecutive_weeks"}
    definitions = [
        item for item in build_registry(conn, context) if item.key in wanted
    ]
    result = build_readiness(
        conn, definitions, bounded("2026-07-23", "2026-07-23"), context,
    )
    by_key = {item["feature_key"]: item for item in result["features"]}
    assert set(by_key) == wanted
    assert all(item["observations"] == 1 for item in by_key.values())
    assert all(item["state"] != "implemented_never_logged" for item in by_key.values())


def test_cardio_session_structural_zero_requires_proven_workout_coverage():
    conn = db("""
        CREATE TABLE workouts(date TEXT,type TEXT,minutes REAL,kcal REAL,km REAL,source TEXT);
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,source TEXT,status TEXT,coverage_from TEXT,
          coverage_to TEXT,completed_at TEXT);
    """)
    conn.execute(
        "INSERT INTO source_sync_runs VALUES(1,'fixture','success',"
        "'2026-07-01','2026-07-02','2026-07-03T00:00:00Z')"
    )
    _identity, token, _normalized = identity_parts("cardio_type", "cycling")
    cardio_key = f"cardio.type.{token}.session"
    observations = running.load(
        conn, defs(cardio_key), bounded("2026-07-01", "2026-07-02"),
        ctx(constants={"RUN_COMPLETENESS_SOURCES": {"fixture"}}),
    )
    assert [
        (item.observed_at, item.feature_key, item.state, item.value)
        for item in observations
    ] == [
        ("2026-07-01", cardio_key, "structural_zero", 0),
        ("2026-07-02", cardio_key, "structural_zero", 0),
    ]


def test_daily_provider_and_training_plan_static_config_never_emit_observations():
    daily_conn = db("""
        CREATE TABLE daily_metrics(date TEXT,source TEXT,steps REAL,PRIMARY KEY(date,source));
    """)
    daily_conn.execute("INSERT INTO daily_metrics VALUES('2026-07-01','fitbit',1000)")
    assert daily.load(
        daily_conn, defs("source.daily_metrics.steps.provider"),
        bounded("2026-07-01", "2026-07-01"), ctx()) == []

    training_conn = db("""
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY,date TEXT,exercise_title TEXT,set_type TEXT,
          weight_kg REAL,reps INTEGER,source TEXT);
        CREATE TABLE training_plan_revisions(
          id INTEGER PRIMARY KEY,effective_from TEXT,schedule_json TEXT,routines_json TEXT);
    """)
    training_conn.execute("INSERT INTO hevy_sets VALUES(1,'2026-07-01','Press','normal',10,5,'hevy')")
    training_conn.execute("INSERT INTO training_plan_revisions VALUES(1,'2026-07-01',"
                          "'{\"Wed\":\"A\"}',"
                          "'{\"A\":[{\"exercise_title\":\"Press\",\"sets\":3}]}')")
    _identity, token, _ = identity_parts("exercise", "Press")
    assert training.load(
        training_conn,
        defs(f"training.plan.exercise.{token}.sets",
             "training.plan.group.chest.effective_sets"),
        bounded("2026-07-01", "2026-07-01"), ctx()) == []


def test_routine_structural_zero_starts_at_owning_plan_effective_date():
    conn = db("""
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY,date TEXT,exercise_title TEXT,set_type TEXT,
          weight_kg REAL,reps INTEGER,source TEXT);
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,source TEXT,status TEXT,coverage_from TEXT,
          coverage_to TEXT,completed_at TEXT);
        CREATE TABLE training_plan_revisions(
          id INTEGER PRIMARY KEY,effective_from TEXT,schedule_json TEXT,
          routines_json TEXT);
    """)
    conn.execute(
        "INSERT INTO source_sync_runs VALUES(1,'hevy','success',"
        "'2026-07-01','2026-07-02','2026-07-03T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO training_plan_revisions VALUES(1,'2026-07-02',?,?)",
        (
            '[{"weekday":"Thu","routine_name":"Base"}]',
            '[{"routine_name":"Base","exercise_title":"Press","target_sets":3}]',
        ),
    )
    _identity, routine_token, _ = identity_parts("routine", "Base")
    key = f"training.routine.{routine_token}.session"
    observations = training.load(
        conn, defs(key), bounded("2026-07-01", "2026-07-02"), ctx(),
        include_provenance=True,
    )
    assert [(item.observed_at, item.state, item.value) for item in observations] == [
        ("2026-07-02", "structural_zero", 0),
    ]


def test_routine_structural_zeros_retain_each_superseding_plan_revision_in_frame():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        """INSERT INTO source_sync_runs(
             id,source,started_at,completed_at,status,coverage_from,coverage_to)
           VALUES(1,'hevy','2026-07-05T00:00:00','2026-07-05T00:01:00',
                  'success','2026-07-01','2026-07-04')"""
    )
    schedule = '{"Wed":"Base"}'
    routines = '{"Base":[{"exercise_title":"Press","sets":3}]}'
    conn.execute(
        """INSERT INTO training_plan_revisions(
             id,effective_from,schedule_json,routines_json,source,supersedes_id)
           VALUES(1,'2026-07-01',?,?, 'owner',NULL)""",
        (schedule, routines),
    )
    conn.execute(
        """INSERT INTO training_plan_revisions(
             id,effective_from,schedule_json,routines_json,source,supersedes_id)
           VALUES(2,'2026-07-03',?,?, 'owner',1)""",
        (schedule, routines),
    )
    runtime = toolkit_health._phase3_context()
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone,
        runtime.constants, runtime.functions,
    )
    definitions = build_registry(conn, context)
    _identity, routine_token, _ = identity_parts("routine", "Base")
    key = f"training.routine.{routine_token}.session"
    selected = [item for item in definitions if item.key == key]
    frame = build_feature_frame(
        conn,
        selected,
        bounded("2026-07-01", "2026-07-04"),
        context,
        include_provenance=True,
    )
    observations = [
        item for item in frame["observations"]
        if item["feature_key"] == key
    ]
    assert [
        (
            item["observed_at"],
            item["state"],
            item["provenance"]["training_plan_revision_id"],
        )
        for item in observations
    ] == [
        ("2026-07-01", "structural_zero", 1),
        ("2026-07-02", "structural_zero", 1),
        ("2026-07-03", "structural_zero", 2),
        ("2026-07-04", "structural_zero", 2),
    ]
    dependencies = dependency_snapshot(
        conn,
        observations=observations,
        date_from="2026-07-01",
        date_to="2026-07-04",
    )
    assert dependencies["source_sync_interval_ids"] == [1]
    assert dependencies["training_plan_revision_ids"] == [1, 2]
    fingerprint = input_fingerprint(
        observations,
        conn=conn,
        registry_version="feature-registry-v1",
        registry_sha256=registry_content_checksum(definitions),
        analysis_sha256="a" * 64,
        date_from="2026-07-01",
        date_to="2026-07-04",
    )
    without_plan_ids = [
        {
            **item,
            "provenance": {
                field: value
                for field, value in item["provenance"].items()
                if field != "training_plan_revision_id"
            },
        }
        for item in observations
    ]
    assert input_fingerprint(
        without_plan_ids,
        conn=conn,
        registry_version="feature-registry-v1",
        registry_sha256=registry_content_checksum(definitions),
        analysis_sha256="a" * 64,
        date_from="2026-07-01",
        date_to="2026-07-04",
    ) != fingerprint


def test_environment_strict_time_wind_components_and_exact_alias_provenance():
    conn = db("""
        CREATE TABLE weather(
          date TEXT,location TEXT,sunrise TEXT,wind_dir_deg REAL,condition TEXT,source TEXT);
        CREATE TABLE entity_aliases(
          id INTEGER PRIMARY KEY,entity_type TEXT,alias_key TEXT,canonical_key TEXT,active INTEGER);
    """)
    canonical_key, canonical_token, _ = identity_parts("location", "canonical timezone")
    alias_key, _alias_token, _ = identity_parts("location", "Example City")
    conn.execute("INSERT INTO entity_aliases VALUES(7,'location',?,?,1)",
                 (alias_key, canonical_key))
    conn.execute("INSERT INTO weather VALUES('2026-07-01','Example City','06:30',90,'clear','weather')")

    def hhmm(value):
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    definitions = defs(
        f"weather.{canonical_token}.sunrise_min",
        f"weather.{canonical_token}.wind_dir_sin",
        f"weather.{canonical_token}.wind_dir_cos",
    )
    observations = environment.load(
        conn, definitions, bounded("2026-07-01", "2026-07-01"),
        ctx(functions={"hhmm_min": hhmm}), include_provenance=True,
    )
    values = {item.feature_key.rsplit(".", 1)[-1]: item for item in observations}
    assert values["sunrise_min"].value == 390
    assert values["wind_dir_sin"].value == pytest.approx(1)
    assert values["wind_dir_cos"].value == pytest.approx(0, abs=1e-12)
    assert all(item.provenance["alias_revision_id"] == 7 for item in observations)


def test_lab_episodes_collapse_exact_reingest_and_disable_unit_mismatch_semantics():
    conn = db("""
        CREATE TABLE labs(
          id INTEGER PRIMARY KEY,date TEXT,test_name TEXT,value REAL,unit TEXT,
          reference_low REAL,reference_high REAL,source TEXT);
        CREATE TABLE lab_catalog(
          canonical TEXT,display TEXT,unit TEXT,ref_low REAL,ref_high REAL,aliases TEXT);
    """)
    conn.execute("INSERT INTO lab_catalog VALUES('Ferritin','Ferritin','ug/L',20,200,'[]')")
    conn.executemany("INSERT INTO labs VALUES(?,?,?,?,?,?,?,?)", [
        (1, "2026-07-01", "Ferritin", 50, "ug/L", None, None, "ocr"),
        (2, "2026-07-01", "Ferritin", 50, "ug/L", None, None, "corrected"),
        (3, "2026-07-02", "Ferritin", 5, "mg/L", None, None, "manual"),
    ])
    identity_key, token, _ = identity_parts("lab", "Ferritin")
    definitions = defs(*(f"lab.{token}.{suffix}" for suffix in
                         ("value", "reference_status", "age_days")))
    observations = labs.load(
        conn, definitions, bounded("2026-07-01", "2026-07-02"),
        ctx(functions={
            "norm_lab_unit": lambda value: str(value).casefold(),
            "lab_in_range": lambda value, low, high: low <= value <= high,
        }), include_provenance=True,
    )
    assert len(observations) == 5
    statuses = {item.observed_at: item.value for item in observations
                if item.feature_key.endswith(".reference_status")}
    assert statuses == {"2026-07-01": "within", "2026-07-02": "incompatible_unit"}
    first = next(item for item in observations
                 if item.observed_at == "2026-07-01" and item.feature_key.endswith(".value"))
    assert first.provenance["natural_key"] == "labs:2"
    assert not any(item.observed_at == "2026-07-02" and item.feature_key.endswith(".value")
                   for item in observations)


def test_lab_catalog_and_alias_join_use_exact_nfkc_whitespace_normalization():
    conn = db("""
        CREATE TABLE labs(
          id INTEGER PRIMARY KEY,date TEXT,test_name TEXT,value REAL,unit TEXT,
          reference_low REAL,reference_high REAL,source TEXT);
        CREATE TABLE lab_catalog(
          canonical TEXT,display TEXT,unit TEXT,ref_low REAL,ref_high REAL,aliases TEXT);
    """)
    conn.execute(
        "INSERT INTO lab_catalog VALUES('Ferritin','Ferritin','ug/L',20,200,"
        "'[\"Iron store\"]')"
    )
    conn.execute(
        "INSERT INTO labs VALUES(1,'2026-07-01','  IRON   STORE ',50,'ug/L',"
        "NULL,NULL,'manual')"
    )
    _identity, token, _ = identity_parts("lab", "Ferritin")
    observations = labs.load(
        conn, defs(f"lab.{token}.value", f"lab.{token}.reference_status"),
        bounded("2026-07-01", "2026-07-01"),
        ctx(functions={"norm_lab_unit": lambda value: str(value).casefold()}),
        include_provenance=True,
    )
    assert {item.feature_key: item.value for item in observations} == {
        f"lab.{token}.reference_status": "within",
        f"lab.{token}.value": 50,
    }
    assert all(item.provenance["canonical"] == "Ferritin" for item in observations)


def test_lab_registry_frame_units_and_unknown_visibility_are_exact():
    conn = db((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        "INSERT INTO lab_catalog(canonical,display,unit,plaus_low,plaus_high,source) "
        "VALUES('Ferritin','Ferritin','ug/L',0,1000,'fixture')"
    )
    conn.executemany(
        "INSERT INTO labs(date,test_name,value,unit,source) VALUES(?,?,?,?,?)", [
            ("2026-07-01", "Ferritin", 50, "ug/L", "manual"),
            ("2026-07-01", "Mystery analyte", 7, "mmol/L", "manual"),
        ],
    )
    runtime = toolkit_health._phase3_context()
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone,
        runtime.constants, runtime.functions,
    )
    definitions = [
        item for item in build_registry(conn, context)
        if item.adapter == "labs"
    ]
    frame = build_feature_frame(
        conn, definitions, bounded("2026-07-01", "2026-07-01"),
        context,
    )
    _identity_key, ferritin_token, _ = identity_parts("lab", "Ferritin")
    _unknown_key, unknown_token, _ = identity_parts("lab", "Mystery analyte")
    by_key = {item["feature_key"]: item for item in frame["observations"]}
    assert by_key[f"lab.{ferritin_token}.value"]["unit"] == "ug/L"
    assert by_key[f"lab.{ferritin_token}.reference_status"]["unit"] == "status"
    assert by_key[f"lab.{unknown_token}.reference_status"]["value"] == "catalog_missing"
    assert f"lab.{unknown_token}.age_days" in by_key
    assert f"lab.{unknown_token}.value" not in {item.key for item in definitions}


def test_adherence_uses_forward_only_plan_and_never_marks_absent_actual_missed():
    conn = db("""
        CREATE TABLE planned_times(metric TEXT,planned TEXT,tolerance_min INTEGER,updated TEXT);
        CREATE TABLE sleep_log(date TEXT,wake_time TEXT,bedtime TEXT,source TEXT);
    """)
    conn.execute("INSERT INTO planned_times VALUES('wake','07:00',30,'2026-07-02T12:00:00')")
    conn.executemany("INSERT INTO sleep_log VALUES(?,?,?,?)", [
        ("2026-07-01", "07:05", None, "manual"),
        ("2026-07-02", "07:20", None, "manual"),
    ])

    def hhmm(value):
        if not value:
            return None
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    observations = adherence.load(
        conn, defs("adherence.timing.wake.on_time", "adherence.timing.wake.abs_delta_min"),
        bounded("2026-07-01", "2026-07-03"),
        ctx(functions={
            "hhmm_min": hhmm,
            "circ_diff_min": lambda left, right: min(abs(left-right), 1440-abs(left-right)),
        }),
    )
    assert {(item.observed_at, item.feature_key, item.value) for item in observations} == {
        ("2026-07-02", "adherence.timing.wake.abs_delta_min", 20),
        ("2026-07-02", "adherence.timing.wake.on_time", 1),
    }


def test_habit_and_skincare_absence_require_effective_other_event_completeness():
    conn = db("""
        CREATE TABLE habits_log(
          id INTEGER PRIMARY KEY,date TEXT,habit TEXT,done INTEGER,source TEXT);
        CREATE TABLE skincare_products(
          product_id INTEGER PRIMARY KEY,brand TEXT,product_name TEXT,active INTEGER);
        CREATE TABLE skincare_log(
          id INTEGER PRIMARY KEY,date TEXT,product_id INTEGER,used INTEGER,source TEXT);
        CREATE TABLE capture_completeness_revisions(
          id INTEGER PRIMARY KEY,date TEXT,scope TEXT,entity_key TEXT,state TEXT,
          explicit_none INTEGER,source TEXT,capture_id TEXT,note TEXT,supersedes_id INTEGER);
    """)
    conn.execute("INSERT INTO skincare_products VALUES(1,'Brand','Cream',1)")
    conn.executemany("INSERT INTO capture_completeness_revisions VALUES(?,?,?,?,?,?,?,?,?,?)", [
        (1, "2026-07-01", "other_event", None, "complete", 1, "owner", None, None, None),
        (2, "2026-07-02", "other_event", None, "partial", 0, "owner", None, None, None),
    ])
    habit_identity, habit_token, _ = identity_parts("habit", "Read")
    skin_identity, skin_token, _ = identity_parts("skincare", "Brand Cream")
    definitions = {
        f"adherence.habit.{habit_token}.done": {
            "key": f"adherence.habit.{habit_token}.done", "unit": "binary",
            "source": {"identity": {"identity_key": habit_identity}},
        },
        f"skincare.product.{skin_token}.used": {
            "key": f"skincare.product.{skin_token}.used", "unit": "binary",
            "source": {"identity": {"identity_key": skin_identity}},
        },
    }
    habit = adherence.load(
        conn, definitions, bounded("2026-07-01", "2026-07-02"), ctx())
    skincare = manual.load(
        conn, definitions, bounded("2026-07-01", "2026-07-02"), ctx())
    assert [(item.observed_at, item.state, item.value) for item in habit] == [
        ("2026-07-01", "structural_zero", 0),
    ]
    assert [(item.observed_at, item.state, item.value) for item in skincare] == [
        ("2026-07-01", "structural_zero", 0),
    ]


def test_habit_duplicate_rule_applies_after_exact_normalization():
    conn = db("""
        CREATE TABLE habits_log(
          id INTEGER PRIMARY KEY,date TEXT,habit TEXT,done INTEGER,source TEXT);
    """)
    conn.executemany("INSERT INTO habits_log VALUES(?,?,?,?,?)", [
        (1, "2026-07-01", " Walk ", 0, "manual"),
        (2, "2026-07-01", "walk", 1, "manual"),
    ])
    identity_key, token, _ = identity_parts("habit", "walk")
    key = f"adherence.habit.{token}.done"
    definitions = {
        key: {
            "key": key, "unit": "binary",
            "source": {"identity": {"identity_key": identity_key}},
        },
    }
    observations = adherence.load(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), ctx(),
        include_provenance=True,
    )
    assert [(item.feature_key, item.value) for item in observations] == [(key, 1)]
    assert observations[0].provenance["original_labels"] == [" Walk ", "walk"]


def test_manual_episodes_preserve_unknown_time_and_explicit_product_values():
    conn = db("""
        CREATE TABLE vitals(
          id INTEGER PRIMARY KEY,date TEXT,time TEXT,systolic REAL,diastolic REAL,
          resting_hr REAL,source TEXT);
        CREATE TABLE assessments(
          id INTEGER PRIMARY KEY,date TEXT,scale TEXT,part TEXT,score REAL,max_score REAL,source TEXT);
        CREATE TABLE skincare_products(
          product_id INTEGER PRIMARY KEY,brand TEXT,product_name TEXT,active INTEGER);
        CREATE TABLE skincare_log(
          id INTEGER PRIMARY KEY,date TEXT,product_id INTEGER,used INTEGER,source TEXT);
    """)
    conn.executemany("INSERT INTO vitals VALUES(?,?,?,?,?,?,?)", [
        (1, "2026-07-01", "bad", 120, 80, 60, "manual"),
        (2, "2026-07-01", "08:30", 130, 82, 64, "manual"),
    ])
    conn.execute("INSERT INTO assessments VALUES(1,'2026-07-01','Scale','Part',8,10,'manual')")
    conn.execute("INSERT INTO skincare_products VALUES(1,'Brand','Cream',1)")
    conn.execute("INSERT INTO skincare_log VALUES(1,'2026-07-01',1,0,'manual')")
    _key, scale_token, _ = identity_parts("assessment", "Scale")
    _key, part_token, _ = identity_parts("assessment_part", "Part")
    _key, skin_token, _ = identity_parts("skincare", "Brand Cream")
    definitions = defs(
        "vitals.systolic_episode", "vitals.systolic_mean",
        f"assessment.{scale_token}.{part_token}.score",
        f"assessment.{scale_token}.{part_token}.fraction",
        f"skincare.product.{skin_token}.used",
    )

    def hhmm(value):
        if value == "08:30":
            return 510
        return None

    observations = manual.load(
        conn, definitions, bounded("2026-07-01", "2026-07-01"),
        ctx(functions={"hhmm_min": hhmm}), include_provenance=True,
    )
    episodes = [item for item in observations if item.feature_key == "vitals.systolic_episode"]
    assert [item.observed_at for item in episodes] == ["2026-07-01", "2026-07-01T08:30:00"]
    assert next(item.value for item in observations
                if item.feature_key == "vitals.systolic_mean") == 125
    assert next(item.value for item in observations
                if item.feature_key.endswith(".fraction")) == 0.8
    assert next(item.value for item in observations
                if item.feature_key.startswith("skincare.")) == 0


def test_assessment_prior_uses_normalized_identity_and_feature_specific_change():
    conn = db("""
        CREATE TABLE assessments(
          id INTEGER PRIMARY KEY,date TEXT,scale TEXT,part TEXT,
          score REAL,max_score REAL,source TEXT);
    """)
    conn.executemany("INSERT INTO assessments VALUES(?,?,?,?,?,?,?)", [
        (1, "2026-06-01", " Scale ", " Part ", 4, 5, "manual"),
        (2, "2026-07-01", "scale", "part", 9, 10, "manual"),
    ])
    _key, scale_token, _ = identity_parts("assessment", "scale")
    _key, part_token, _ = identity_parts("assessment_part", "part")
    prefix = f"assessment.{scale_token}.{part_token}"
    observations = manual.load(
        conn, defs(f"{prefix}.score", f"{prefix}.fraction"),
        bounded("2026-07-01", "2026-07-01"), ctx(),
        include_provenance=True,
    )
    by_key = {item.feature_key: item for item in observations}
    assert by_key[f"{prefix}.score"].provenance["prior_score"] == 4
    assert by_key[f"{prefix}.score"].provenance["change"] == 5
    assert by_key[f"{prefix}.fraction"].provenance["prior_fraction"] == 0.8
    assert math.isclose(
        by_key[f"{prefix}.fraction"].provenance["change"], 0.1,
    )


def test_skincare_duplicate_products_merge_by_normalized_brand_name_identity():
    conn = db("""
        CREATE TABLE skincare_products(
          product_id INTEGER PRIMARY KEY,brand TEXT,product_name TEXT,active INTEGER);
        CREATE TABLE skincare_log(
          id INTEGER PRIMARY KEY,date TEXT,product_id INTEGER,used INTEGER,source TEXT);
    """)
    conn.executemany("INSERT INTO skincare_products VALUES(?,?,?,1)", [
        (1, "Brand", "Cream"), (2, " brand ", " cream "),
    ])
    conn.executemany("INSERT INTO skincare_log VALUES(?,?,?,?,?)", [
        (1, "2026-07-01", 1, 0, "manual"),
        (2, "2026-07-01", 2, 1, "manual"),
    ])
    identity_key, token, _ = identity_parts("skincare", "Brand Cream")
    key = f"skincare.product.{token}.used"
    definitions = {
        key: {
            "key": key, "unit": "binary",
            "source": {"identity": {"identity_key": identity_key}},
        },
    }
    observations = manual.load(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), ctx(),
        include_provenance=True,
    )
    assert [(item.feature_key, item.value) for item in observations] == [(key, 1)]
    assert observations[0].provenance["product_natural_keys"] == [
        "skincare_products:1", "skincare_products:2",
    ]


def test_nutrition_and_events_require_all_matching_numeric_values():
    nutrition_conn = db("""
        CREATE TABLE nutrition_log(
          id INTEGER PRIMARY KEY,date TEXT,recipe_id TEXT,food_name TEXT,grams REAL,
          kcal REAL,protein_g REAL,carbs_g REAL,fat_g REAL,fiber_g REAL,
          time TEXT,meal_type TEXT,source TEXT);
    """)
    nutrition_conn.executemany(
        "INSERT INTO nutrition_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (1, "2026-07-01", None, "A", 10, 100, 5, 1, 1, 1, None, None, "manual"),
            (2, "2026-07-01", None, "B", 10, None, 5, 1, 1, 1, None, None, "manual"),
            (3, "2026-07-02", None, "A", 10, 100, 5, 1, 1, 1, None, None, "manual"),
            (4, "2026-07-02", None, "B", 10, 200, 5, 1, 1, 1, None, None, "manual"),
        ])
    nut = nutrition.load(
        nutrition_conn, defs("nutrition.logged.kcal"),
        bounded("2026-07-01", "2026-07-02"), ctx(),
    )
    assert [(item.observed_at, item.value) for item in nut] == [("2026-07-02", 300)]

    event_conn = db("""
        CREATE TABLE event_exposures(
          id INTEGER PRIMARY KEY,date TEXT,time TEXT,category TEXT,entity_label TEXT,
          entity_key TEXT,duration_min REAL,intensity REAL,valence REAL,source TEXT,
          voided INTEGER);
    """)
    event_conn.executemany("INSERT INTO event_exposures VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
        (1, "2026-07-01", None, "social", None, None, 20, 2, 1, "manual", 0),
        (2, "2026-07-01", None, "social", None, None, None, 4, 1, "manual", 0),
    ])
    observed = events.load(
        event_conn, defs("event.social.occurred", "event.social.count",
                         "event.social.duration_min", "event.social.intensity_mean"),
        bounded("2026-07-01", "2026-07-01"), ctx(),
    )
    values = {item.feature_key: item.value for item in observed}
    assert values == {
        "event.social.occurred": 1,
        "event.social.count": 2,
        "event.social.intensity_mean": 3,
    }


def test_nutrition_total_zero_requires_complete_scope_and_targets_use_shared_override():
    conn = db("""
        CREATE TABLE nutrition_log(
          id INTEGER PRIMARY KEY,date TEXT,kcal REAL,protein_g REAL,carbs_g REAL,
          fat_g REAL,fiber_g REAL,recipe_id TEXT,food_name TEXT,time TEXT,
          meal_type TEXT,source TEXT);
        CREATE TABLE capture_completeness_revisions(
          id INTEGER PRIMARY KEY,date TEXT,scope TEXT,entity_key TEXT,state TEXT,
          explicit_none INTEGER,source TEXT,capture_id TEXT,note TEXT,supersedes_id INTEGER);
        CREATE TABLE nutrient_daily(
          date TEXT,nutrient TEXT,amount REAL,unit TEXT,source TEXT);
    """)
    conn.executemany("INSERT INTO capture_completeness_revisions VALUES(?,?,?,?,?,?,?,?,?,?)", [
        (1, "2026-07-01", "nutrition_total", None, "complete", 1, "owner", None, None, None),
        (2, "2026-07-02", "nutrition_total", None, "partial", 0, "owner", None, None, None),
    ])
    conn.execute("INSERT INTO nutrient_daily VALUES('2026-07-01','vitamin_d',6,'µg','cronometer')")
    constants = {"MICRO_SEED": [{
        "key": "vitamin_d", "unit": "µg", "target": 6,
        "units": {}, "cite": "raw seed",
    }]}
    functions = {"nutrition_micro_targets": lambda: [{
        "nutrient": "vitamin_d", "unit": "µg", "target": 12,
        "source": "configured test override",
    }]}
    observations = nutrition.load(
        conn, defs("nutrition.logged.kcal", "nutrition.nutrient.vitamin_d.amount",
                   "nutrition.nutrient.vitamin_d.target_fraction",
                   "nutrition.nutrient.vitamin_d.target_met"),
        bounded("2026-07-01", "2026-07-02"),
        ctx(constants=constants, functions=functions), include_provenance=True,
    )
    values = {(item.feature_key, item.observed_at): item for item in observations}
    zero = values[("nutrition.logged.kcal", "2026-07-01")]
    assert (zero.state, zero.value) == ("structural_zero", 0)
    assert ("nutrition.logged.kcal", "2026-07-02") not in values
    fraction = values[("nutrition.nutrient.vitamin_d.target_fraction", "2026-07-01")]
    assert fraction.value == 0.5
    assert fraction.provenance["target"] == 12
    assert values[("nutrition.nutrient.vitamin_d.target_met", "2026-07-01")].value == 0


def test_meal_type_occurrence_is_one_daily_binary_with_all_item_lineage():
    conn = db("""
        CREATE TABLE nutrition_log(
          id INTEGER PRIMARY KEY,date TEXT,recipe_id TEXT,food_name TEXT,
          grams REAL,kcal REAL,protein_g REAL,carbs_g REAL,fat_g REAL,
          fiber_g REAL,time TEXT,meal_type TEXT,source TEXT);
    """)
    conn.executemany(
        "INSERT INTO nutrition_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (1, "2026-07-01", None, "Eggs", 100, 140, 12, 1, 10, 0,
             "08:00", "breakfast", "manual"),
            (2, "2026-07-01", None, "Toast", 50, 120, 4, 22, 2, 3,
             "08:00", "breakfast", "manual"),
        ],
    )
    observations = nutrition.load(
        conn, defs("meal.type.breakfast.occurred"),
        bounded("2026-07-01", "2026-07-01"), ctx(),
        include_provenance=True,
    )
    assert [(item.feature_key, item.value) for item in observations] == [
        ("meal.type.breakfast.occurred", 1),
    ]
    assert observations[0].provenance["natural_keys"] == [
        "nutrition_log:1", "nutrition_log:2",
    ]


def test_quarterly_zero_side_denominator_never_fabricates_gap():
    seed = {"num": "a", "den": "b"}
    valid = []
    row_id = 0
    for movement, side, value in (
        ("a", "left", 0), ("a", "right", 0),
        ("b", "left", 2), ("b", "right", 3),
    ):
        row_id += 1
        valid.append({
            "date": f"2026-07-0{row_id}", "value": value,
            "protocol": "same", "row": {"id": row_id, "movement": movement, "side": side},
        })
    assert quarterly._ratio_side_gap_episodes(valid, seed) == []


def test_quarterly_units_survive_strict_full_registry_frame():
    conn = db((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        "INSERT INTO body_metrics(date,waist_cm,chest_cm,body_fat_pct,source) "
        "VALUES('2026-07-01',80,100,18,'manual')"
    )
    rows = (
        ("leg-extension", "left", 40, 8, None, None, None, None),
        ("leg-extension", "right", 50, 8, None, None, None, None),
        ("balance-stand", "left", None, None, 45, None, None, None),
        ("balance-stand", "right", None, None, 60, None, None, None),
        ("single-leg-control", "left", None, None, None, 2, None, None),
        ("single-leg-control", "right", None, None, None, 3, None, None),
        ("ankle-df-wall", "left", None, None, None, None, 35, None),
        ("ankle-df-wall", "right", None, None, None, None, 40, None),
        ("run-3k", "bilateral", None, None, 900, None, None, None),
        ("sit-and-reach", "bilateral", None, None, None, None, None, 20),
    )
    conn.executemany(
        "INSERT INTO fitness_tests("
        "date,movement,side,load_kg,reps,seconds,rating,degrees,cm,"
        "equipment_note,source) VALUES('2026-07-01',?,?,?,?,?,?,?,?,?,'manual')",
        [(*row, "fixture-protocol") for row in rows],
    )
    conn.executemany(
        "INSERT INTO fitness_tests("
        "date,movement,side,passed,equipment_note,source) "
        "VALUES('2026-07-01','thomas',?,?,'fixture-protocol','manual')",
        [("left", 1), ("right", 0)],
    )
    conn.execute(
        "INSERT INTO athletic_targets(axis,lift,target) VALUES('balance','',60)"
    )
    runtime = toolkit_health._phase3_context()
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone,
        runtime.constants, runtime.functions,
    )
    definitions = [
        item for item in build_registry(conn, context)
        if item.adapter == "quarterly"
    ]
    frame = build_feature_frame(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), context,
    )
    by_key = {item["feature_key"]: item for item in frame["observations"]}
    expected = {
        "body.body_fat_pct": "percent",
        "body.wcr": "ratio",
        "fitness.test.leg-extension.left.value": "kg_e1rm",
        "fitness.test.balance-stand.left.value": "seconds",
        "fitness.test.single-leg-control.left.value": "ordinal_1_3",
        "fitness.test.ankle-df-wall.left.value": "degrees",
        "fitness.test.run-3k.bilateral.value": "seconds",
        "fitness.test.sit-and-reach.bilateral.value": "cm",
        "fitness.test.thomas.left.value": "binary",
        "fitness.test.leg-extension.side_gap": "fraction",
        "fitness.test.balance-stand.side_gap": "fraction",
        "fitness.athletic_axis.balance.score": "score_0_100",
    }
    assert {key: by_key[key]["unit"] for key in expected} == expected


@pytest.mark.parametrize(
    "left_date,left_protocol,right_date,right_protocol",
    [
        ("2026-07-01", "board-a", "2026-07-01", "board-b"),
        ("2026-01-01", "board-a", "2026-07-01", "board-a"),
    ],
)
def test_weakest_side_axis_rejects_protocol_mismatch_and_stale_parent_pair(
    left_date, left_protocol, right_date, right_protocol,
):
    conn = db("""
        CREATE TABLE fitness_tests(
          id INTEGER PRIMARY KEY,date TEXT,movement TEXT,side TEXT,
          seconds REAL,equipment_note TEXT,source TEXT,voided INTEGER);
        CREATE TABLE athletic_targets(axis TEXT,lift TEXT,target REAL);
    """)
    conn.executemany("INSERT INTO fitness_tests VALUES(?,?,?,?,?,?,?,0)", [
        (1, left_date, "balance-stand", "left", 45, left_protocol, "manual"),
        (2, right_date, "balance-stand", "right", 60, right_protocol, "manual"),
    ])
    conn.execute("INSERT INTO athletic_targets VALUES('balance','',60)")
    observations = quarterly.load(
        conn, defs("fitness.athletic_axis.balance.score"),
        bounded("2026-01-01", "2026-07-01"),
        ctx(constants={
            "CATALOG": {
                "balance-stand": {"kind": "hold", "unilateral": True},
            },
            "ATHLETIC_AXES": {
                "balance": {"test": "balance-stand", "agg": "min_side"},
            },
            "KIND_BETTER": {"hold": "higher"},
        }, functions={
            "axis_score": lambda result, target, _better: 100 * result / target,
        }),
    )
    assert observations == []


def test_weakest_side_axis_keeps_latest_older_comparable_pair_after_unmatched_update():
    conn = db("""
        CREATE TABLE fitness_tests(
          id INTEGER PRIMARY KEY,date TEXT,movement TEXT,side TEXT,
          seconds REAL,equipment_note TEXT,source TEXT,voided INTEGER);
        CREATE TABLE athletic_targets(axis TEXT,lift TEXT,target REAL);
    """)
    conn.executemany("INSERT INTO fitness_tests VALUES(?,?,?,?,?,?,?,0)", [
        (1, "2026-06-01", "balance-stand", "left", 45, "board-a", "manual"),
        (2, "2026-06-02", "balance-stand", "right", 60, "board-a", "manual"),
        (3, "2026-07-01", "balance-stand", "left", 40, "board-b", "manual"),
    ])
    conn.execute("INSERT INTO athletic_targets VALUES('balance','',60)")
    observations = quarterly.load(
        conn, defs("fitness.athletic_axis.balance.score"),
        bounded("2026-06-01", "2026-07-01"),
        ctx(constants={
            "CATALOG": {
                "balance-stand": {"kind": "hold", "unilateral": True},
            },
            "ATHLETIC_AXES": {
                "balance": {"test": "balance-stand", "agg": "min_side"},
            },
            "KIND_BETTER": {"hold": "higher"},
        }, functions={
            "axis_score": lambda result, target, _better: 100 * result / target,
        }),
        include_provenance=True,
    )
    assert [(item.observed_at, item.value) for item in observations] == [
        ("2026-06-02", 75),
    ]
    assert observations[0].provenance["component_protocol"] == "board-a"
    assert observations[0].provenance["parent_span_days"] == 1


def test_ratio_side_gap_requires_matching_bilateral_component_protocols():
    seed = {"num": "a", "den": "b"}
    valid = []
    for row_id, (movement, side, value, protocol) in enumerate((
        ("a", "left", 8, "machine-a"),
        ("a", "right", 10, "machine-b"),
        ("b", "left", 4, "machine-a"),
        ("b", "right", 5, "machine-a"),
    ), start=1):
        valid.append({
            "date": f"2026-07-0{row_id}", "value": value,
            "protocol": protocol,
            "row": {"id": row_id, "movement": movement, "side": side},
        })
    assert quarterly._ratio_side_gap_episodes(valid, seed) == []


def test_fitness_derived_pairs_reject_parents_over_120_days_old():
    seed = {"num": "a", "den": "b"}
    ratio_inputs = [
        {
            "date": "2026-01-01", "value": 4, "protocol": "machine",
            "row": {"id": 1, "movement": "b", "side": "left"},
        },
        {
            "date": "2026-07-01", "value": 8, "protocol": "machine",
            "row": {"id": 2, "movement": "a", "side": "left"},
        },
    ]
    assert quarterly._ratio_episodes(ratio_inputs, seed, "left") == []

    conn = db("""
        CREATE TABLE fitness_tests(
          id INTEGER PRIMARY KEY,date TEXT,movement TEXT,side TEXT,
          rating REAL,equipment_note TEXT,source TEXT,voided INTEGER);
    """)
    conn.executemany("INSERT INTO fitness_tests VALUES(?,?,?,?,?,?,?,?)", [
        (1, "2026-01-01", "balance", "left", 5, "board", "manual", 0),
        (2, "2026-07-01", "balance", "right", 8, "board", "manual", 0),
    ])
    observations = quarterly.load(
        conn, defs("fitness.test.balance.side_gap"),
        bounded("2026-07-01", "2026-07-01"),
        ctx(constants={
            "CATALOG": {
                "balance": {"kind": "control", "unilateral": True},
            },
        }),
    )
    assert observations == []


def test_weakest_side_axis_provenance_retains_both_required_parents():
    conn = db("""
        CREATE TABLE fitness_tests(
          id INTEGER PRIMARY KEY,date TEXT,movement TEXT,side TEXT,
          rating REAL,equipment_note TEXT,source TEXT,voided INTEGER);
        CREATE TABLE athletic_targets(axis TEXT,lift TEXT,target REAL);
    """)
    conn.executemany("INSERT INTO fitness_tests VALUES(?,?,?,?,?,?,?,?)", [
        (1, "2026-07-01", "balance", "left", 6, "board", "manual", 0),
        (2, "2026-07-01", "balance", "right", 8, "board", "manual", 0),
    ])
    conn.execute("INSERT INTO athletic_targets VALUES('balance','',10)")
    observations = quarterly.load(
        conn, defs("fitness.athletic_axis.balance.score"),
        bounded("2026-07-01", "2026-07-01"),
        ctx(constants={
            "CATALOG": {
                "balance": {"kind": "control", "unilateral": True},
            },
            "ATHLETIC_AXES": {
                "balance": {"test": "balance", "agg": "min_side"},
            },
            "KIND_BETTER": {"control": "higher"},
        }, functions={
            "axis_score": lambda result, target, _better: 100 * result / target,
        }),
        include_provenance=True,
    )
    assert len(observations) == 1
    assert observations[0].provenance["parent_natural_keys"] == [
        "fitness_tests:1", "fitness_tests:2",
    ]
    assert observations[0].provenance["weakest_side_name"] == "left"
    assert {item["side"] for item in observations[0].provenance["parents"]} == {
        "left", "right",
    }


def test_pain_supplemental_uses_pre_event_windows_and_keeps_same_day_separate():
    conn = db("""
        CREATE TABLE pain_log(
          id INTEGER PRIMARY KEY,date TEXT,region TEXT,side TEXT,intensity INTEGER,
          source TEXT,voided INTEGER,reported_onset_date TEXT,onset_precision TEXT);
        CREATE TABLE exercise_trial_log(
          id INTEGER PRIMARY KEY,date TEXT,drill TEXT,target TEXT,dose TEXT,
          response TEXT,pain_during INTEGER,source TEXT,voided INTEGER);
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY,date TEXT,workout_title TEXT,start_time TEXT,
          exercise_title TEXT,set_type TEXT,weight_kg REAL,reps INTEGER,rpe REAL,
          duration_seconds REAL,distance_km REAL,source TEXT);
        CREATE TABLE workouts(date TEXT,type TEXT,minutes REAL,kcal REAL,km REAL,source TEXT);
    """)
    conn.executemany("INSERT INTO pain_log VALUES(?,?,?,?,?,?,?,?,?)", [
        (1, "2026-07-10", "hip", "left", 4, "manual", 0, None, None),
        (2, "2026-07-11", "hip", "left", 6, "manual", 0, "2026-07-08", "exact"),
    ])
    conn.execute("INSERT INTO exercise_trial_log VALUES(1,'2026-07-11','drill-a',"
                 "'lateral-knee','2 sets','same',3,'manual',0)")
    conn.execute("INSERT INTO hevy_sets VALUES(1,'2026-07-09','A',NULL,'Press','normal',10,5,NULL,NULL,NULL,'hevy')")
    conn.execute("INSERT INTO workouts VALUES('2026-07-09','Outdoor Running',30,200,5,'manual')")
    payload = pain.supplemental(
        conn, bounded("2026-07-01", "2026-07-12"),
        ctx(constants={
            "PAIN_CAUSE_MAP": {"hip": {}},
            "SELF_TEST_CATALOG": {}, "REHAB_CATALOG": {"drill-a": {}},
            "RUN_TYPE_KEYS": {"outdoor running": "running"},
        }),
    )
    assert {item["view"] for item in payload["pre_observation_summaries"]} == {
        "observation_date", "reported_onset",
    }
    observation_view = next(item for item in payload["pre_observation_summaries"]
                            if item["view"] == "observation_date")
    working = next(item for item in observation_view["features"]
                   if item["feature_key"] == "training.working_sets"
                   and item["window_days"] == 3)
    assert working["value"] is None
    assert working["known_dates"] == 1
    assert working["to"] == "2026-07-09"
    assert working["same_day"] is None
    onset_view = next(item for item in payload["pre_observation_summaries"]
                      if item["view"] == "reported_onset")
    assert onset_view["onset_report_observation_date"] == "2026-07-11"
    kinds = {item["kind"] for item in payload["timeline"]}
    assert {"reported_onset", "training_event", "running_event", "pain_observation"} <= kinds
    assert payload["rehab_evidence_is_not_treatment_effect"] is True

    _target_key, target_token, _ = identity_parts("other", "lateral-knee")
    loaded = pain.load(
        conn, defs("pain.nrs.hip.left",
                   f"rehab.trial.drill-a.{target_token}.response"),
        bounded("2026-07-01", "2026-07-12"),
        ctx(constants={
            "PAIN_CAUSE_MAP": {"hip": {}}, "SELF_TEST_CATALOG": {},
            "REHAB_CATALOG": {"drill-a": {}},
        }), include_provenance=True,
    )
    nrs = [item for item in loaded if item.feature_key == "pain.nrs.hip.left"]
    assert nrs[-1].provenance["exact_change"] == 2
    assert nrs[-1].provenance["change_label"] == "higher observed pain"
    assert "flare" not in str(nrs[-1].provenance).casefold()
    trial = next(item for item in loaded if item.feature_key.startswith("rehab.trial."))
    assert trial.provenance["target_in_pain_catalog"] is False
    assert trial.provenance["no_clinical_pathway_inferred"] is True


def test_pain_completeness_zeros_markers_only_never_nrs():
    conn = db("""
        CREATE TABLE pain_log(
          id INTEGER PRIMARY KEY,date TEXT,region TEXT,side TEXT,intensity INTEGER,
          source TEXT,voided INTEGER,reported_onset_date TEXT,onset_precision TEXT);
        CREATE TABLE capture_completeness_revisions(
          id INTEGER PRIMARY KEY,date TEXT,scope TEXT,entity_key TEXT,state TEXT,
          explicit_none INTEGER,source TEXT,capture_id TEXT,note TEXT,
          supersedes_id INTEGER);
    """)
    entity_key, _token, _normalized = identity_parts(
        "other", "pain anterior-knee left",
    )
    conn.execute(
        "INSERT INTO capture_completeness_revisions VALUES(1,?,?,?,?,?,?,?,?,?)",
        ("2026-07-01", "pain", entity_key, "complete", 1, "owner",
         None, None, None),
    )
    prefix = "pain.nrs.anterior-knee.left"
    observations = pain.load(
        conn, defs(
            prefix,
            f"{prefix}.reported_onset",
            f"{prefix}.first_observed_positive",
            f"{prefix}.resolution_observed",
        ),
        bounded("2026-07-01", "2026-07-01"),
        ctx(constants={"PAIN_CAUSE_MAP": {"anterior-knee": {}}}),
        include_provenance=True,
    )
    assert [(item.feature_key, item.state, item.value) for item in observations] == [
        (f"{prefix}.first_observed_positive", "structural_zero", 0),
        (f"{prefix}.reported_onset", "structural_zero", 0),
        (f"{prefix}.resolution_observed", "structural_zero", 0),
    ]
    assert all(item.provenance["nrs_missing_is_not_pain_free"]
               for item in observations)


def test_pain_rejects_future_reported_onset_invalid_side_and_out_of_range_trial_nrs():
    conn = db("""
        CREATE TABLE pain_log(
          id INTEGER PRIMARY KEY,date TEXT,region TEXT,side TEXT,intensity INTEGER,
          source TEXT,voided INTEGER,reported_onset_date TEXT,onset_precision TEXT);
        CREATE TABLE self_test_log(
          id INTEGER PRIMARY KEY,date TEXT,test TEXT,side TEXT,result TEXT,
          source TEXT,voided INTEGER);
        CREATE TABLE exercise_trial_log(
          id INTEGER PRIMARY KEY,date TEXT,drill TEXT,target TEXT,dose TEXT,
          response TEXT,pain_during INTEGER,source TEXT,voided INTEGER);
    """)
    conn.execute(
        "INSERT INTO pain_log VALUES(1,'2026-07-01','anterior-knee','left',4,"
        "'manual',0,'2026-07-02','exact')"
    )
    conn.execute(
        "INSERT INTO self_test_log VALUES(1,'2026-07-01','decline-squat-pain',"
        "'bilateral','positive','manual',0)"
    )
    conn.execute(
        "INSERT INTO exercise_trial_log VALUES(1,'2026-07-01',"
        "'patellar-isometrics','anterior-knee','5x45s','better',11,'manual',0)"
    )
    _identity, target_token, _normalized = identity_parts(
        "other", "anterior-knee",
    )
    definitions = defs(
        "pain.nrs.anterior-knee.left.reported_onset",
        "pain.self_test.decline-squat-pain.bilateral.result",
        f"rehab.trial.patellar-isometrics.{target_token}.response",
        f"rehab.trial.patellar-isometrics.{target_token}.pain_during_nrs",
    )
    context = ctx(constants={
        "PAIN_CAUSE_MAP": {"anterior-knee": {}},
        "SELF_TEST_CATALOG": {"decline-squat-pain": {}},
        "REHAB_CATALOG": {"patellar-isometrics": {}},
    })
    observations = pain.load(
        conn, definitions, bounded("2026-07-01", "2026-07-02"), context,
    )
    assert [(item.feature_key, item.value) for item in observations] == [
        (f"rehab.trial.patellar-isometrics.{target_token}.response", 1),
    ]
    payload = pain.supplemental(
        conn, bounded("2026-07-01", "2026-07-02"), context,
    )
    assert not any(item["kind"] == "reported_onset" for item in payload["timeline"])
    assert all(item["view"] != "reported_onset"
               for item in payload["pre_observation_summaries"])


def test_pain_and_rehab_units_survive_strict_full_registry_frame():
    conn = db((ROOT / "SCHEMA.sql").read_text())
    conn.execute(
        "INSERT INTO self_test_log(date,test,side,result,source) "
        "VALUES('2026-07-01','decline-squat-pain','left','positive','manual')"
    )
    conn.execute(
        "INSERT INTO exercise_trial_log("
        "date,drill,target,dose,response,pain_during,source"
        ") VALUES('2026-07-01','patellar-isometrics','anterior-knee',"
        "'5x45s','better',3,'manual')"
    )
    runtime = toolkit_health._phase3_context()
    context = AdapterContext(
        date(2026, 7, 23), runtime.timezone,
        runtime.constants, runtime.functions,
    )
    definitions = [
        item for item in build_registry(conn, context)
        if item.adapter == "pain"
    ]
    frame = build_feature_frame(
        conn, definitions, bounded("2026-07-01", "2026-07-01"), context,
    )
    _identity, target_token, _normalized = identity_parts(
        "other", "anterior-knee",
    )
    by_key = {item["feature_key"]: item for item in frame["observations"]}
    assert by_key[
        "pain.self_test.decline-squat-pain.left.result"
    ]["unit"] == "result"
    assert by_key[
        f"rehab.trial.patellar-isometrics.{target_token}.response"
    ]["unit"] == "response_-1_1"
    assert by_key[
        f"rehab.trial.patellar-isometrics.{target_token}.pain_during_nrs"
    ]["unit"] == "nrs_0_10"
