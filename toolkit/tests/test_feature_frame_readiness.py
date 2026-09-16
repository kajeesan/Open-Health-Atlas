"""End-to-end Phase 3 frame, readiness, and read-command contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest


ROOT = Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
CODE_VERSION = "f" * 40
sys.path.insert(0, str(ROOT))

import health as toolkit_health
from hermes_insights.contracts import AdapterContext, DateRange
from hermes_insights.adapters import training
from hermes_insights.frame import FrameError, build_feature_frame, load_adapters
from hermes_insights.readiness import READINESS_STATES, build_readiness
from hermes_insights.registry import build_registry, registry_content_checksum


TODAY = date(2026, 7, 23)
TZ = ZoneInfo("Europe/Paris")
ADAPTER_IDS = {
    "adherence", "daily", "environment", "events", "labs", "manual",
    "nutrition", "pain", "quarterly", "running", "training",
}


def context(*, today=TODAY, now: datetime | None = None) -> AdapterContext:
    constants = {
        name: getattr(toolkit_health, name)
        for name in (
            "CATALOG", "RATIO_SEED", "ATHLETIC_AXES", "MOBILITY_NORM",
            "PAIN_CAUSE_MAP", "SELF_TEST_CATALOG", "REHAB_CATALOG",
            "MICRO_SEED", "RUN_TYPE_KEYS", "MUSCLE_GROUP_AXES",
            "DM_METRICS", "DM_PRIMARY", "MEDICATION_ALIASES", "KIND_BETTER",
        )
    }
    functions = {
        "now": lambda: now or datetime(2026, 7, 23, 12, tzinfo=TZ),
        "e1rm": toolkit_health.e1rm,
        "group_weight_maps": toolkit_health._group_weight_maps,
        "basis_weights": toolkit_health._basis_weights,
        "dev_score": toolkit_health._dev_score,
        "norm_lab_unit": toolkit_health._norm_lab_unit,
        "lab_in_range": toolkit_health._lab_in_range,
        "circ_diff_min": toolkit_health._circ_diff_min,
        "start_hhmm": toolkit_health._start_hhmm,
        "hhmm_min": toolkit_health._hhmm_min,
        "tested_ratios": toolkit_health._tested_ratios,
        "axis_score": toolkit_health._axis_score,
    }
    return AdapterContext(today, "Europe/Paris", constants, functions)


@pytest.fixture()
def conn():
    value = sqlite3.connect(":memory:")
    value.row_factory = sqlite3.Row
    value.executescript(SCHEMA)
    try:
        yield value
    finally:
        value.close()


def run_cli(db: Path, *args: str, expect: int = 0) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HEALTH), *args], text=True, capture_output=True,
        timeout=90,
        env={**os.environ, "HEALTH_DB": str(db), "HERMES_CODE_VERSION": CODE_VERSION},
    )
    assert completed.returncode == expect, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


@pytest.fixture()
def cli_db(tmp_path: Path) -> Path:
    path = tmp_path / "phase3-read.db"
    value = sqlite3.connect(path)
    value.executescript(SCHEMA)
    value.commit()
    value.close()
    run_cli(path, "migrate", "--to", "3", "--expected-from", "0")
    return path


def test_cli_exact_range_default_all_and_family_validation(cli_db):
    exact = run_cli(
        cli_db, "feature-frame", "--from", "2026-07-20", "--to", "2026-07-23",
        "--family", "calendar",
    )
    assert exact["meta"]["range"] == {
        "kind": "bounded", "from": "2026-07-20", "to": "2026-07-23",
    }

    two = run_cli(cli_db, "feature-frame", "--days", "2", "--family", "calendar")
    lo, hi = (date.fromisoformat(two["meta"]["range"][key]) for key in ("from", "to"))
    assert (hi - lo).days == 1

    default = run_cli(cli_db, "feature-frame", "--family", "calendar")
    lo, hi = (date.fromisoformat(default["meta"]["range"][key]) for key in ("from", "to"))
    assert (hi - lo).days == 364

    all_dates = run_cli(cli_db, "feature-frame", "--all", "--family", "calendar")
    assert all_dates["meta"]["range"] == {"kind": "all"}

    registry = run_cli(cli_db, "feature-registry", "--family", "calendar")
    assert registry["registry_sha256"] == all_dates["meta"]["registry_sha256"]

    invalid_calls = (
        ("feature-frame", "--from", "2026-07-20", "--family", "calendar"),
        ("feature-frame", "--from", "2026-07-23", "--to", "2026-07-20", "--family", "calendar"),
        ("feature-frame", "--days", "0", "--family", "calendar"),
        ("feature-frame", "--all", "--days", "2", "--family", "calendar"),
        ("feature-frame", "--all", "--all", "--family", "calendar"),
        ("feature-registry", "--family", "daily_metrics"),
    )
    for args in invalid_calls:
        failure = run_cli(cli_db, *args, expect=2)
        assert failure["error"]["code"] == "validation_error"


def test_provenance_is_strictly_opt_in(conn):
    conn.execute(
        "INSERT INTO daily_metrics(date,source,resting_hr,steps) VALUES(?,?,?,?)",
        ("2026-07-23", "fitbit", 58, 9000),
    )
    definitions = build_registry(conn, context(), family="wearable")
    requested = DateRange(TODAY, TODAY)
    plain = build_feature_frame(conn, definitions, requested, context())
    detailed = build_feature_frame(
        conn, definitions, requested, context(), include_provenance=True,
    )
    plain_row = next(row for row in plain["observations"]
                     if row["feature_key"] == "wearable.resting_hr_bpm")
    detailed_row = next(row for row in detailed["observations"]
                        if row["feature_key"] == "wearable.resting_hr_bpm")
    assert "provenance" not in plain_row
    assert detailed_row["provenance"]["table"] == "daily_metrics"
    assert detailed_row["provenance"]["natural_key"] == "daily_metrics:2026-07-23:fitbit"


def test_every_registered_adapter_is_discovered_and_loaded(conn):
    conn.execute(
        "INSERT INTO lab_catalog(canonical,display,unit,plaus_low,plaus_high,source) "
        "VALUES('fixture','Fixture','unit',0,100,'fixture')"
    )
    definitions = build_registry(conn, context())
    assert {item.adapter for item in definitions} == ADAPTER_IDS
    assert set(load_adapters()) == ADAPTER_IDS
    frame = build_feature_frame(
        conn, definitions, DateRange(TODAY, TODAY), context(),
    )
    assert frame["meta"]["adapter_count"] == len(ADAPTER_IDS)
    assert {item["adapter"] for item in frame["adapters"]} == ADAPTER_IDS
    assert all(item["connected"] for item in frame["adapters"])


def test_lab_catalog_units_and_unknown_identity_status_survive_full_frame(conn):
    conn.execute(
        "INSERT INTO lab_catalog("
        "canonical,display,unit,plaus_low,plaus_high,source,aliases"
        ") VALUES('Ferritin','Ferritin','ug/L',0,1000,'fixture','[]')"
    )
    conn.executemany(
        "INSERT INTO labs(date,test_name,value,unit,source) VALUES(?,?,?,?,?)",
        [
            ("2026-07-23", "Ferritin", 50, "ug/L", "manual"),
            ("2026-07-23", "Owner New Test", 7.5, "fixture-unit", "manual"),
        ],
    )
    definitions = build_registry(conn, context(), family="lab")
    known_value = next(
        item for item in definitions
        if item.source["identity"]["original_label"] == "Ferritin"
        and item.key.endswith(".value")
    )
    unknown = [
        item for item in definitions
        if item.source["identity"]["original_label"] == "Owner New Test"
    ]
    assert known_value.unit == "ug/L"
    assert {item.key.rsplit(".", 1)[-1] for item in unknown} == {
        "age_days", "reference_status",
    }

    frame = build_feature_frame(
        conn, definitions, DateRange(TODAY, TODAY), context(),
        include_provenance=True,
    )
    by_key = {row["feature_key"]: row for row in frame["observations"]}
    assert by_key[known_value.key]["value"] == 50
    assert by_key[known_value.key]["unit"] == "ug/L"
    unknown_status = next(
        item for item in unknown if item.key.endswith(".reference_status")
    )
    assert by_key[unknown_status.key]["value"] == "catalog_missing"
    assert by_key[unknown_status.key]["provenance"]["catalog_connected"] is False


def test_missing_is_distinct_from_completeness_proven_structural_zero(conn):
    conn.execute(
        "INSERT INTO capture_completeness_revisions"
        "(date,scope,state,explicit_none,source) VALUES(?,?,?,?,?)",
        ("2026-07-22", "other_event", "complete", 1, "manual"),
    )
    definitions = build_registry(conn, context(), family="subjective")
    frame = build_feature_frame(
        conn, definitions,
        DateRange(date(2026, 7, 22), date(2026, 7, 23)), context(),
    )
    rows = [row for row in frame["observations"]
            if row["feature_key"] == "subjective.brain_dump_logged"]
    assert [(row["observed_at"], row["state"], row["value"]) for row in rows] == [
        ("2026-07-22", "structural_zero", 0),
        ("2026-07-23", "missing", None),
    ]


def test_slow_episode_is_never_daily_filled(conn):
    conn.execute(
        "INSERT INTO body_metrics(date,weight_kg,source) VALUES('2026-07-21',80,'manual')"
    )
    definitions = build_registry(conn, context(), family="body")
    frame = build_feature_frame(
        conn, definitions,
        DateRange(date(2026, 7, 20), date(2026, 7, 23)), context(),
    )
    weight = [row for row in frame["observations"]
              if row["feature_key"] == "body.weight_kg"]
    assert [(row["observed_at"], row["state"], row["value"]) for row in weight] == [
        ("2026-07-21", "observed", 80),
    ]
    assert not any(row["state"] == "missing" for row in frame["observations"])


def test_no_adapter_can_emit_static_config_as_observation(conn):
    conn.execute(
        "INSERT INTO source_sync_runs(source,started_at,completed_at,status,coverage_from,coverage_to) "
        "VALUES('google-health','2026-07-23T08:00:00+02:00','2026-07-23T08:01:00+02:00',"
        "'success','2026-07-23','2026-07-23')"
    )
    conn.execute(
        "INSERT INTO daily_metrics(date,source,steps) VALUES('2026-07-23','fitbit',9000)"
    )
    conn.execute(
        "INSERT INTO hevy_sets(date,workout_title,exercise_title,set_type,source) "
        "VALUES('2026-07-23','Base','Row','normal','hevy')"
    )
    conn.execute(
        "INSERT INTO training_plan_revisions(effective_from,schedule_json,routines_json,source) "
        "VALUES('2026-07-01',?,?, 'fixture')",
        (
            '[{"weekday":"Thu","routine_name":"Base"}]',
            '[{"routine_name":"Base","exercise_title":"Row","target_sets":3}]',
        ),
    )
    all_definitions = build_registry(conn, context())
    static = tuple(item for item in all_definitions
                   if item.completeness_profile == "STATIC_CONFIG")
    assert static
    frame = build_feature_frame(conn, static, DateRange(TODAY, TODAY), context())
    assert frame["observations"] == []
    assert all(frame["feature_states"][item.key] == "missing" for item in static)


def test_exact_six_state_predicate_order_including_populated_unconnected(conn):
    conn.executemany(
        "INSERT INTO subjective_daily(date,energy,focus,mood,source) VALUES(?,?,?,?,?)",
        [
            ("2026-07-01", 3, None, None, "manual"),
            ("2026-07-23", None, 4, 4, "manual"),
        ],
    )
    conn.execute(
        "INSERT INTO nutrition_log(date,food_name,kcal,source) "
        "VALUES('2026-07-23','Fixture',500,'manual')"
    )
    conn.execute(
        "INSERT INTO pain_log(date,region,side,intensity,source) "
        "VALUES('2026-07-23','lateral-knee','left',4,'manual')"
    )
    registry = {item.key: item for item in build_registry(conn, context())}
    definitions = (
        registry["pain.nrs.lateral-knee.left"],
        registry["nutrition.logged.kcal"],
        registry["vitals.systolic_mean"],
        registry["subjective.energy"],
        registry["subjective.mood"],
        replace(registry["subjective.focus"], min_observations=1),
    )
    modules = load_adapters()
    modules.pop("nutrition")
    payload = build_readiness(
        conn, definitions,
        DateRange(date(2026, 7, 20), TODAY), context(), modules=modules,
    )
    state = {item["feature_key"]: item["state"] for item in payload["features"]}
    assert payload["meta"]["predicate_order"] == list(READINESS_STATES)
    assert state == {
        "pain.nrs.lateral-knee.left": "logic_not_implemented",
        "nutrition.logged.kcal": "present_not_connected",
        "vitals.systolic_mean": "implemented_never_logged",
        "subjective.energy": "stale",
        "subjective.mood": "too_sparse_for_analysis",
        "subjective.focus": "sufficient",
    }


def test_disconnected_derived_fitness_requires_feature_specific_source_rows(conn):
    conn.execute(
        "INSERT INTO fitness_tests("
        "date,movement,side,seconds,source"
        ") VALUES('2026-07-23','balance-stand','left',20,'manual')"
    )
    conn.execute(
        "INSERT INTO athletic_targets(axis,lift,target) "
        "VALUES('strength','Bench Press',100)"
    )
    conn.execute(
        "INSERT INTO hevy_sets("
        "date,exercise_title,set_type,weight_kg,reps,source"
        ") VALUES('2026-07-23','Squat','normal',100,5,'hevy')"
    )
    registry = {item.key: item for item in build_registry(conn, context())}
    ratio_key = next(
        key for key in registry
        if key.startswith("fitness.ratio.") and key.endswith(".left.value")
    )
    definitions = (
        registry[ratio_key], registry["fitness.athletic_axis.strength.score"],
    )
    modules = load_adapters()
    modules.pop("quarterly")
    payload = build_readiness(
        conn, definitions, DateRange(TODAY, TODAY), context(), modules=modules,
    )
    states = {item["feature_key"]: item["state"] for item in payload["features"]}
    assert states[ratio_key] != "present_not_connected"
    assert states["fitness.athletic_axis.strength.score"] != "present_not_connected"


def test_static_config_uses_config_evidence_without_fake_observations(conn):
    registry = {item.key: item for item in build_registry(conn, context())}
    keys = (
        "calendar.weekday", "source.collector.weather.status",
        "source.collector.air.status", "source.collector.google-health.status",
        "source.collector.hevy.status",
        "source.daily_metrics.resting_hr.provider",
        "source.daily_metrics.steps.provider",
        "training.plan.group.chest.effective_sets",
    )
    conn.execute(
        "INSERT INTO source_sync_runs(source,started_at,completed_at,status,coverage_from,coverage_to) "
        "VALUES('hevy','2026-07-23T08:00:00+02:00','2026-07-23T08:01:00+02:00',"
        "'success','2026-07-23','2026-07-23')"
    )
    conn.execute(
        "INSERT INTO daily_metrics(date,source,resting_hr,steps) "
        "VALUES('2026-07-23','fitbit',58,NULL)"
    )
    definitions = tuple(registry[key] for key in keys)
    before = build_readiness(
        conn, definitions, DateRange(TODAY, TODAY), context(),
    )
    before_by_key = {item["feature_key"]: item for item in before["features"]}
    assert before_by_key["calendar.weekday"]["state"] == "sufficient"
    assert before_by_key["calendar.weekday"]["observations"] == 0
    assert before_by_key["source.collector.hevy.status"]["state"] == "sufficient"
    assert before_by_key["source.daily_metrics.resting_hr.provider"]["state"] == "sufficient"
    for key in (
        "source.collector.weather.status", "source.collector.air.status",
        "source.collector.google-health.status", "source.daily_metrics.steps.provider",
    ):
        assert before_by_key[key]["state"] == "implemented_never_logged"
    assert before_by_key["training.plan.group.chest.effective_sets"]["state"] == "implemented_never_logged"

    conn.execute(
        "INSERT INTO source_sync_runs(source,started_at,completed_at,status,coverage_from,coverage_to) "
        "VALUES('weather','2026-07-23T08:00:00+02:00','2026-07-23T08:01:00+02:00',"
        "'success','2026-07-23','2026-07-23')"
    )
    conn.execute(
        "INSERT INTO training_plan_revisions(effective_from,schedule_json,routines_json,source) "
        "VALUES('2026-07-01','[]','[]','fixture')"
    )
    after = build_readiness(
        conn, definitions, DateRange(TODAY, TODAY), context(),
    )
    after_by_key = {item["feature_key"]: item for item in after["features"]}
    assert after_by_key["source.collector.weather.status"]["state"] == "sufficient"
    assert after_by_key["training.plan.group.chest.effective_sets"]["state"] == "implemented_never_logged"
    assert after_by_key["source.collector.air.status"]["state"] == "implemented_never_logged"
    assert after_by_key["source.collector.google-health.status"]["state"] == "implemented_never_logged"
    assert after_by_key["source.daily_metrics.steps.provider"]["state"] == "implemented_never_logged"
    assert all(after_by_key[key]["observations"] == 0 for key in keys)

    conn.execute(
        "INSERT INTO exercise_muscles(exercise_title,muscle,weight,source) "
        "VALUES('Press','chest',1,'manual')"
    )
    conn.execute(
        "INSERT INTO training_plan_revisions(effective_from,schedule_json,routines_json,source) "
        "VALUES('2026-07-02',?,?, 'fixture')",
        (
            '[{"weekday":"Thu","routine_name":"Base"}]',
            '[{"routine_name":"Base","exercise_title":"Press","target_sets":3}]',
        ),
    )
    configured = build_readiness(
        conn, definitions, DateRange(TODAY, TODAY), context(),
    )
    configured_by_key = {item["feature_key"]: item for item in configured["features"]}
    assert configured_by_key["training.plan.group.chest.effective_sets"]["state"] == "sufficient"
    assert configured_by_key["training.plan.group.chest.effective_sets"]["observations"] == 0


def test_collector_staleness_uses_latest_success_not_later_failure(conn):
    conn.execute(
        "INSERT INTO weather(date,location,temp_max_c,source) "
        "VALUES('2026-07-23','canonical timezone',24,'fixture')"
    )
    conn.executemany(
        "INSERT INTO source_sync_runs(source,started_at,completed_at,status,coverage_from,coverage_to,error_code) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            ("weather", "2026-07-21T11:00:00+02:00", "2026-07-21T12:00:00+02:00",
             "success", "2026-07-21", "2026-07-23", None),
            ("weather", "2026-07-23T09:00:00+02:00", "2026-07-23T10:00:00+02:00",
             "failed", None, None, "collection_failed"),
        ],
    )
    definition = next(
        item for item in build_registry(conn, context())
        if item.key.startswith("weather.") and item.key.endswith(".temp_max_c")
    )
    late = build_readiness(
        conn, (definition,), DateRange(TODAY, TODAY),
        context(now=datetime(2026, 7, 23, 12, tzinfo=TZ)),
    )["features"][0]
    assert late["state"] == "too_sparse_for_analysis"
    assert late["factors"]["collector_freshness"]["status"] == "late"
    assert late["factors"]["collector_freshness"]["successful_run_id"] == 1
    assert late["factors"]["collector_freshness"]["run_id"] == 2

    stale = build_readiness(
        conn, (definition,), DateRange(TODAY, TODAY),
        context(now=datetime(2026, 7, 23, 13, tzinfo=TZ)),
    )["features"][0]
    assert stale["state"] == "stale"
    assert stale["factors"]["collector_freshness"]["status"] == "stale"


def test_collector_status_can_exist_before_first_success_freshness(conn):
    conn.execute(
        "INSERT INTO source_sync_runs(source,started_at,completed_at,status,error_code) "
        "VALUES('air','2026-07-23T08:00:00+02:00','2026-07-23T08:01:00+02:00',"
        "'failed','collection_failed')"
    )
    registry = {item.key: item for item in build_registry(conn, context())}
    keys = ("source.collector.air.status", "source.collector.air.freshness_days")
    payload = build_readiness(
        conn, tuple(registry[key] for key in keys), DateRange(TODAY, TODAY), context(),
    )
    by_key = {item["feature_key"]: item for item in payload["features"]}
    assert by_key[keys[0]]["state"] == "sufficient"
    assert by_key[keys[1]]["state"] == "implemented_never_logged"


def test_zero_observation_readiness_names_exact_next_measurement(conn):
    registry = {item.key: item for item in build_registry(conn, context())}
    keys = (
        "pain.nrs.anterior-knee.left",
        "fitness.test.ankle-df-wall.left.value",
        "vitals.systolic_mean",
        "fitness.ratio.hq.left.value",
    )
    payload = build_readiness(
        conn, tuple(registry[key] for key in keys),
        DateRange(TODAY, TODAY), context(),
    )
    by_key = {item["feature_key"]: item for item in payload["features"]}
    assert "anterior-knee left NRS" in by_key[keys[0]]["needed"]
    assert "ankle-df-wall left rom result in degrees" in by_key[keys[1]]["needed"]
    assert "systolic mean" in by_key[keys[2]]["needed"]
    assert by_key[keys[3]]["needed"] == (
        "one protocol-consistent left-side pair; repeat next quarter for change"
    )


def test_goal_multiplier_and_ties_are_deterministic(conn):
    conn.execute(
        "INSERT INTO subjective_daily(date,energy,focus,source) "
        "VALUES('2026-07-23',4,4,'manual')"
    )
    conn.execute(
        "INSERT INTO sleep_log(date,time_asleep_hours,source) "
        "VALUES('2026-07-23',8,'manual')"
    )
    conn.execute(
        "INSERT INTO insight_goal_revisions"
        "(goal_key,enabled,priority,outcome_key,target_direction,source) "
        "VALUES('energy_focus_mood',1,5,'subjective.energy','increase','owner-test')"
    )
    registry = {item.key: item for item in build_registry(conn, context())}
    energy = replace(
        registry["subjective.energy"], min_observations=1,
        redundancy_group="readiness_tie", preferred_rank=2,
    )
    sleep = replace(
        registry["sleep.duration_hours"], min_observations=1,
        redundancy_group="readiness_tie", preferred_rank=1,
    )
    focus = replace(registry["subjective.focus"], min_observations=1)
    payload = build_readiness(
        conn, (focus, energy, sleep), DateRange(TODAY, TODAY), context(),
    )
    assert [item["feature_key"] for item in payload["features"]] == [
        "sleep.duration_hours", "subjective.energy", "subjective.focus",
    ]
    by_key = {item["feature_key"]: item for item in payload["features"]}
    for key in ("sleep.duration_hours", "subjective.energy"):
        item = by_key[key]
        assert item["state"] == "sufficient"
        assert item["factors"]["goal_multiplier"] == 1.4
        assert item["factors"]["highest_matching_goal_priority"] == 5
        assert item["priority"] == 5.6
    assert by_key["subjective.focus"]["state"] == "sufficient"
    assert by_key["subjective.focus"]["factors"]["goal_multiplier"] == 1.0
    assert by_key["subjective.focus"]["factors"]["highest_matching_goal_priority"] is None
    assert by_key["subjective.focus"]["priority"] == 4.0


def test_broad_goal_membership_does_not_bypass_b7_multiplier_gate(conn):
    conn.execute(
        "INSERT INTO hevy_sets("
        "date,workout_title,exercise_title,set_type,weight_kg,reps,source"
        ") VALUES('2026-07-23','Upper','Bench Press','normal',80,5,'hevy')"
    )
    registry = {item.key: item for item in build_registry(conn, context())}
    e1rm_key = next(
        key for key in registry
        if key.startswith("training.exercise.") and key.endswith(".best_e1rm_kg")
    )
    conn.execute(
        "INSERT INTO insight_goal_revisions("
        "goal_key,enabled,priority,outcome_key,target_direction,source"
        ") VALUES('strength_and_muscle',1,5,?,'increase','owner-test')",
        (e1rm_key,),
    )
    keys = (
        e1rm_key,
        "training.session",
        "nutrition.logged.kcal",
        "recovery.score.apple",
        "fitness.athletic_axis.speed.score",
        "fitness.test.sprint-30m.bilateral.value",
    )
    payload = build_readiness(
        conn, tuple(registry[key] for key in keys),
        DateRange(TODAY, TODAY), context(),
    )
    by_key = {item["feature_key"]: item for item in payload["features"]}

    # Selected outcome plus exact exercise/muscle, nutrition, and recovery
    # leading families receive the priority-five multiplier.
    for key in (
        e1rm_key, "training.session", "nutrition.logged.kcal",
        "recovery.score.apple",
    ):
        assert by_key[key]["factors"]["goal_multiplier"] == 1.4
        assert by_key[key]["factors"]["highest_matching_goal_priority"] == 5

    # B.6 membership alone does not turn unrelated fitness outputs into B.7
    # strength prerequisites or allowed leading-family measurements.
    for key in (
        "fitness.athletic_axis.speed.score",
        "fitness.test.sprint-30m.bilateral.value",
    ):
        assert "strength_and_muscle" in registry[key].goal_families
        assert by_key[key]["factors"]["goal_multiplier"] == 1.0
        assert by_key[key]["factors"]["highest_matching_goal_priority"] is None


def test_follow_through_multiplier_is_limited_to_medication_timing(conn):
    conn.execute(
        "INSERT INTO meds_log(date,drug,dose_mg,time_taken,source) "
        "VALUES('2026-07-23','Fixture medication',10,'08:00','manual')"
    )
    conn.execute(
        "INSERT INTO insight_goal_revisions("
        "goal_key,enabled,priority,outcome_key,target_direction,source"
        ") VALUES('follow_through',1,5,'adherence.word_kept','increase','owner-test')"
    )
    registry = {item.key: item for item in build_registry(conn, context())}
    medication = {
        key.rsplit(".", 1)[-1]: key
        for key in registry if key.startswith("medication.")
    }
    keys = (
        "adherence.word_kept", medication["dose_mg"],
        medication["first_dose_min"], medication["last_dose_min"],
    )
    payload = build_readiness(
        conn, tuple(registry[key] for key in keys),
        DateRange(TODAY, TODAY), context(),
    )
    by_key = {item["feature_key"]: item for item in payload["features"]}
    assert by_key[medication["dose_mg"]]["factors"]["goal_multiplier"] == 1.0
    for suffix in ("first_dose_min", "last_dose_min"):
        assert by_key[medication[suffix]]["factors"]["goal_multiplier"] == 1.4


def _fixture_definition(conn, **changes):
    value = next(
        item for item in build_registry(conn, context())
        if item.key == "subjective.energy"
    ).to_dict()
    value.update({
        "key": "fixture.value", "adapter": "daily", "unit": "count",
        "temporal_type": "slow_measurement", "aggregation_id": "LP_NONE",
        "lag_eligibility": {"lags": [], "windows": []},
        "goal_families": (),
    })
    value.update(changes)
    return value


def _fixture_module(rows):
    def load(conn, definitions, date_range, context, include_provenance=False):
        return rows(date_range) if callable(rows) else rows

    return SimpleNamespace(
        ADAPTER_ID="daily", REGISTRY_VERSION="feature-registry-v1", load=load,
    )


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"feature_key": "fixture.value", "observed_at": "2026-7-23",
          "value": 1, "state": "observed", "unit": "count"}, "not ISO"),
        ({"feature_key": "fixture.value", "observed_at": "2026-07-22",
          "value": 1, "state": "observed", "unit": "count"}, "out-of-range"),
        ({"feature_key": "fixture.value", "observed_at": "2026-07-23",
          "value": 1, "state": "missing", "unit": "count"}, "must not carry"),
        ({"feature_key": "fixture.value", "observed_at": "2026-07-23",
          "value": False, "state": "structural_zero", "unit": "count"}, "numeric zero"),
        (SimpleNamespace(feature_key="fixture.value", observed_at="2026-07-23",
                         value=1, state="observed", unit="count", source=7,
                         provenance=None),
         "source must"),
        ({"feature_key": "fixture.value", "observed_at": "2026-07-23",
          "value": 1, "state": "observed", "unit": "count", "provenance": []},
         "provenance must"),
        ({"feature_key": "fixture.value", "observed_at": "2026-07-23",
          "value": 1, "state": "observed", "unit": "kg"}, "unit drifts"),
    ],
)
def test_custom_adapter_observation_contract_is_fail_closed(conn, row, message):
    with pytest.raises(FrameError, match=message) as exc:
        build_feature_frame(
            conn, (_fixture_definition(conn),), DateRange(TODAY, TODAY), context(),
            modules={"daily": _fixture_module([row])},
        )
    assert exc.value.code == "adapter_contract_error"


def test_custom_adapter_static_config_observation_is_rejected(conn):
    definition = _fixture_definition(
        conn,
        completeness_profile="STATIC_CONFIG", temporal_type="static_config",
        zero_semantics="not_observation",
    )
    row = {
        "feature_key": "fixture.value", "observed_at": "2026-07-23",
        "value": 1, "state": "observed", "unit": "count",
    }
    with pytest.raises(FrameError, match="static config must not be emitted"):
        build_feature_frame(
            conn, (definition,), DateRange(TODAY, TODAY), context(),
            modules={"daily": _fixture_module([row])},
        )


def test_all_range_accepts_valid_old_iso_observation(conn):
    row = {
        "feature_key": "fixture.value", "observed_at": "1900-01-01",
        "value": 1, "state": "observed", "unit": "count",
    }
    payload = build_feature_frame(
        conn, (_fixture_definition(conn),), DateRange(None, None, "all"), context(),
        modules={"daily": _fixture_module([row])},
    )
    assert payload["observations"][0]["observed_at"] == "1900-01-01"


def test_load_summary_second_pass_cannot_bypass_observation_validation(conn):
    definition = next(
        item for item in build_registry(conn, context())
        if item.key == "training.session"
    )

    def load(conn, definitions, date_range, context, include_provenance=False):
        if date_range.kind == "bounded":
            return []
        return [{
            "feature_key": "training.session", "observed_at": "2026-01-01",
            "value": 1, "state": "observed", "unit": "kg",
        }]

    module = SimpleNamespace(
        ADAPTER_ID="training", REGISTRY_VERSION="feature-registry-v1",
        load=load, window_summary=training.window_summary,
    )
    with pytest.raises(FrameError, match="unit drifts"):
        build_feature_frame(
            conn, (definition,), DateRange(TODAY, TODAY), context(),
            modules={"training": module},
        )


def test_session_load_summary_uses_count_key_and_exact_preceding_window(conn):
    definition = next(
        item for item in build_registry(conn, context())
        if item.key == "training.session"
    )
    current_positive = {TODAY.isoformat(), (TODAY - timedelta(days=2)).isoformat()}
    history = []
    for offset in range(14):
        day = (TODAY - timedelta(days=offset)).isoformat()
        positive = day in current_positive
        history.append({
            "feature_key": "training.session", "observed_at": day,
            "value": 1 if positive else 0,
            "state": "observed" if positive else "structural_zero",
            "unit": "binary",
        })

    def load(_conn, _definitions, date_range, _context, include_provenance=False):
        return history if date_range.kind == "all" else [history[0]]

    module = SimpleNamespace(
        ADAPTER_ID="training", REGISTRY_VERSION="feature-registry-v1",
        load=load, window_summary=training.window_summary,
    )
    payload = build_feature_frame(
        conn, (definition,), DateRange(TODAY, TODAY), context(),
        modules={"training": module},
    )
    summary = next(item for item in payload["load_summaries"]
                   if item["window_days"] == 7)
    assert summary == {
        "feature_key": "training.session",
        "anchor_date": TODAY.isoformat(),
        "window_days": 7,
        "current": 2,
        "previous": 0,
        "absolute_change": 2,
        "change_per_day": 2 / 7,
        "percent_change": None,
        "new_exposure": True,
        "frequency_per_week": 2,
        "days_since": 0,
        "complete_current": True,
        "complete_previous": True,
        "candidate_keys": {
            "current": "training.session|lag=0|window=7|transform=count",
            "delta_per_day": "training.session|lag=0|window=7|transform=delta_per_day",
            "frequency_per_week": "training.session|lag=0|window=7|transform=frequency_per_week",
            "days_since": "training.session|lag=0|window=7|transform=days_since",
        },
    }


def _readiness_fixture_definition(conn, key, **changes):
    value = _fixture_definition(
        conn, key=key, implemented=True, candidate_enabled=True,
        roles=("exposure",), min_observations=30,
        value_kind="continuous", formula_id="direct_value_v1",
        temporal_type="daily_measurement",
        preferred_rank=100, readiness_value=1, readiness_effort=1,
        stale_after_days=None, goal_families=(), pillar="subjective",
        source={"table": "fixture", "columns": ["date", "value"], "merge": "fixture"},
    )
    value.update(changes)
    return value


def test_continuous_event_values_do_not_use_binary_sign_prevalence_gate(conn):
    start = TODAY - timedelta(days=29)
    rows = [
        {"feature_key": "fixture.value", "observed_at": (start + timedelta(days=i)).isoformat(),
         "value": i - 15, "state": "observed", "unit": "count"}
        for i in range(30)
    ]
    definition = _readiness_fixture_definition(
        conn, "fixture.value", temporal_type="event_occurrence", zero_semantics="valid",
    )
    item = build_readiness(
        conn, (definition,), DateRange(start, TODAY), context(),
        modules={"daily": _fixture_module(rows)},
    )["features"][0]
    assert item["state"] == "sufficient"
    assert item["factors"]["binary_prevalence_pass"] is True
    assert "binary_prevalence<10_each" not in item["factors"]["gate_failures"]


def test_duplicate_same_day_rows_never_inflate_aligned_n(conn):
    rows = [
        {"feature_key": "fixture.value", "observed_at": TODAY.isoformat(),
         "value": i, "state": "observed", "unit": "count"}
        for i in range(30)
    ]
    item = build_readiness(
        conn, (_readiness_fixture_definition(conn, "fixture.value"),),
        DateRange(TODAY, TODAY), context(),
        modules={"daily": _fixture_module(rows)},
    )["features"][0]
    assert item["observations"] == 30
    assert item["aligned_n"] == 1
    assert item["state"] == "too_sparse_for_analysis"
    assert "aligned_n<30" in item["factors"]["gate_failures"]


def test_selected_day_rating_modes_use_only_feature_aligned_dates(conn):
    start = TODAY - timedelta(days=44)
    days = [(start + timedelta(days=i)).isoformat() for i in range(45)]
    outcome_rows = [
        {"feature_key": "subjective.day_rating", "observed_at": day,
         "value": 1 + i // 15, "state": "observed", "unit": "ordinal_1_3"}
        for i, day in enumerate(days)
    ]
    # Exposure exists only on the 30 Yellow/Green days.  The 15 unaligned Red
    # outcomes must not satisfy its class gate.
    exposure_rows = [
        {"feature_key": "fixture.exposure", "observed_at": day,
         "value": i % 5, "state": "observed", "unit": "count"}
        for i, day in enumerate(days[15:])
    ]
    exposure = _readiness_fixture_definition(conn, "fixture.exposure")
    outcome = _readiness_fixture_definition(
        conn, "subjective.day_rating", unit="ordinal_1_3", roles=("outcome",),
        value_kind="ordinal", temporal_type="outcome",
        goal_families=("green_days",),
    )
    payload = build_readiness(
        conn, (exposure, outcome), DateRange(start, TODAY), context(),
        outcome="subjective.day_rating",
        modules={"daily": _fixture_module([*outcome_rows, *exposure_rows])},
    )
    item = next(row for row in payload["features"]
                if row["feature_key"] == "fixture.exposure")
    assert item["aligned_n"] == 30
    assert item["state"] == "too_sparse_for_analysis"
    assert item["factors"]["day_rating"]["red"] == 0
    assert "day_rating_modes" in item["factors"]["gate_failures"]


def test_constant_selected_continuous_outcome_fails_variation_gate(conn):
    start = TODAY - timedelta(days=29)
    days = [(start + timedelta(days=i)).isoformat() for i in range(30)]
    rows = [
        {"feature_key": key, "observed_at": day,
         "value": (3 if key == "fixture.outcome" else i % 5),
         "state": "observed", "unit": "count"}
        for i, day in enumerate(days)
        for key in ("fixture.outcome", "fixture.exposure")
    ]
    outcome = _readiness_fixture_definition(
        conn, "fixture.outcome", roles=("outcome",), temporal_type="outcome",
    )
    exposure = _readiness_fixture_definition(conn, "fixture.exposure")
    payload = build_readiness(
        conn, (outcome, exposure), DateRange(start, TODAY), context(),
        outcome="fixture.outcome", modules={"daily": _fixture_module(rows)},
    )
    for item in payload["features"]:
        assert item["state"] == "too_sparse_for_analysis"
        assert "outcome_variation" in item["factors"]["gate_failures"]


def _schema_hash(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    return hashlib.sha256(json.dumps([tuple(row) for row in rows]).encode()).hexdigest()


def test_frame_and_readiness_are_select_only_no_schema_or_data_mutation(conn):
    definitions = build_registry(conn, context())
    before_schema = _schema_hash(conn)
    before_changes = conn.total_changes
    statements: list[str] = []
    forbidden_actions = {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE,
    }
    conn.set_trace_callback(statements.append)
    conn.set_authorizer(
        lambda action, _a1, _a2, _db, _trigger:
        sqlite3.SQLITE_DENY if action in forbidden_actions else sqlite3.SQLITE_OK
    )
    try:
        frame = build_feature_frame(
            conn, definitions, DateRange(TODAY, TODAY), context(),
        )
        readiness = build_readiness(
            conn, definitions, DateRange(TODAY, TODAY), context(),
        )
    finally:
        conn.set_authorizer(None)
        conn.set_trace_callback(None)
    assert frame["ok"] and readiness["ok"]
    assert conn.total_changes == before_changes
    assert _schema_hash(conn) == before_schema
    assert statements
    normalized = [statement.lstrip().upper() for statement in statements]
    assert all(statement.startswith("SELECT") or statement.startswith("PRAGMA TABLE_INFO")
               or statement.startswith("-- PRAGMA TABLE_INFO")
               for statement in normalized)
    assert frame["meta"]["registry_sha256"] == registry_content_checksum(definitions)
    assert readiness["meta"]["registry_sha256"] == registry_content_checksum(definitions)
