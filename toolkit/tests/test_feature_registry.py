"""Phase 3 strict feature-registry, expansion, identity and no-DDL contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
from types import SimpleNamespace

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SCHEMA = (ROOT / "SCHEMA.sql").read_text()

import health as toolkit_health
from hermes_insights.contracts import (
    AdapterContext, ContractError, DateRange, FEATURE_METADATA_FIELDS,
    FeatureDefinition, Observation, REGISTRY_VERSION, canonical_json,
)
from hermes_insights.normalize import identity_key
from hermes_insights.goals import GoalError, validate_goal_pair
from hermes_insights.registry import (
    RegistryError, build_registry, registry_content_checksum, serialize_registry,
    validate_adapter_handshakes, validate_registry,
)


ADAPTER_IDS = (
    "adherence", "daily", "environment", "events", "labs", "manual",
    "nutrition", "pain", "quarterly", "running", "training",
)


def fake_adapters(version=REGISTRY_VERSION):
    def load(conn, definitions, date_range, context, include_provenance=False):
        return []

    return {
        adapter_id: SimpleNamespace(
            ADAPTER_ID=adapter_id, REGISTRY_VERSION=version,
            load=load,
        )
        for adapter_id in ADAPTER_IDS
    }


def runtime_constants():
    result = {
        name: deepcopy(getattr(toolkit_health, name))
        for name in (
            "CATALOG", "RATIO_SEED", "ATHLETIC_AXES", "MOBILITY_NORM",
            "PAIN_CAUSE_MAP", "SELF_TEST_CATALOG", "REHAB_CATALOG",
            "MICRO_SEED", "DM_METRICS",
        )
    }
    result.update({
        "RUN_TYPE_KEYS": {
            "running": "running", "outdoor running": "running",
            "indoor running": "running", "treadmill running": "running",
        },
        "MEDICATION_ALIASES": deepcopy(getattr(toolkit_health, "MEDICATION_ALIASES", ())),
        "ADAPTERS": fake_adapters(),
    })
    return result


def context(constants=None):
    return AdapterContext(
        today=date(2026, 7, 23), timezone="Europe/Paris",
        constants=constants or runtime_constants(), functions={},
    )


@pytest.fixture()
def conn():
    value = sqlite3.connect(":memory:")
    value.executescript(SCHEMA)
    try:
        yield value
    finally:
        value.close()


@pytest.fixture()
def registry(conn):
    return build_registry(conn, context())


def schema_fingerprint(conn):
    rows = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def test_contract_public_dataclasses_and_exact_metadata_shape(registry):
    assert DateRange(date(2026, 1, 1), date(2026, 1, 2)).to_dict() == {
        "kind": "bounded", "from": date(2026, 1, 1), "to": date(2026, 1, 2),
    }
    assert DateRange(None, None, "all").to_dict()["kind"] == "all"
    with pytest.raises(ContractError):
        DateRange(None, date(2026, 1, 2))

    assert len(FEATURE_METADATA_FIELDS) == 31  # key plus all 30 metadata fields
    raw = registry[0].to_dict()
    assert tuple(raw) == FEATURE_METADATA_FIELDS
    assert FeatureDefinition.from_dict(raw).to_dict() == raw
    assert context().to_dict()["timezone"] == "Europe/Paris"

    missing = dict(raw); missing.pop("formula_id")
    unknown = dict(raw); unknown["invented"] = True
    with pytest.raises(ContractError, match="missing"):
        FeatureDefinition.from_dict(missing)
    with pytest.raises(ContractError, match="unknown"):
        FeatureDefinition.from_dict(unknown)

    with pytest.raises(ContractError, match="keys must be strings"):
        canonical_json({1: "lossy key coercion is forbidden"})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("key", "Bad Key", "feature key"),
        ("direction", "more_is_best", "direction"),
        ("actionability", "maybe", "actionability"),
        ("roles", ("readiness", "exposure"), "roles"),
        ("completeness_profile", "AUTO_MAYBE", "completeness"),
        ("lineage_roots", ("bad root with spaces",), "lineage"),
        ("adapter", "mystery", "adapter"),
    ],
)
def test_registry_rejects_invalid_enums_syntax_and_order(registry, field, value, message):
    raw = registry[0].to_dict()
    raw[field] = value
    with pytest.raises(ContractError, match=message):
        FeatureDefinition.from_dict(raw)


def test_registry_rejects_lag_profile_drift_unknown_formula_and_duplicate(registry):
    raw = registry[0].to_dict()
    raw["lag_eligibility"] = {"lags": [0], "windows": ["point"]}
    with pytest.raises(ContractError, match="lag metadata drifts"):
        FeatureDefinition.from_dict(raw)

    unknown_formula = registry[0].to_dict()
    unknown_formula["formula_id"] = "invented_formula_v1"
    definition = FeatureDefinition.from_dict(unknown_formula)
    with pytest.raises(RegistryError) as exc:
        validate_registry([definition])
    assert exc.value.code == "unknown_formula_id"

    with pytest.raises(RegistryError) as exc:
        validate_registry([registry[0], registry[0]])
    assert exc.value.code == "duplicate_feature_key"


def test_registry_rejects_malformed_key_segments_and_unavailable_drift(registry):
    for key in ("feature..value", "feature.value."):
        raw = registry[0].to_dict()
        raw["key"] = key
        with pytest.raises(ContractError, match="feature key"):
            FeatureDefinition.from_dict(raw)

    raw = registry[0].to_dict()
    raw["implemented"] = False
    with pytest.raises(ContractError, match="must be unavailable"):
        FeatureDefinition.from_dict(raw)

    lateral = next(
        item for item in registry if item.key == "pain.nrs.lateral-knee.left"
    ).to_dict()
    lateral["candidate_enabled"] = True
    with pytest.raises(ContractError, match="cannot be a candidate"):
        FeatureDefinition.from_dict(lateral)


def test_source_metadata_is_closed_and_identity_provenance_is_strict(registry):
    raw = registry[0].to_dict()
    raw["source"]["surprise"] = "column"
    with pytest.raises(ContractError, match="source metadata mismatch"):
        FeatureDefinition.from_dict(raw)

    identity_feature = next(
        (item for item in registry if "identity" in item.source), None
    )
    # A fresh schema may have no dynamic identities; the strict nested shape is
    # covered by the dynamic test below.
    assert identity_feature is None or set(identity_feature.source["identity"]) == {
        "namespace", "identity_key", "identity_token", "original_label",
        "alias_revision_id",
    }


def test_identity_namespace_key_and_token_cannot_drift(conn):
    conn.execute(
        "INSERT INTO nutrition_log(date,food_name,grams,source) "
        "VALUES('2026-07-23','Identity fixture',100,'manual')"
    )
    item = next(
        value for value in build_registry(conn, context())
        if value.key.startswith("food.named.") and value.key.endswith(".occurred")
    )
    raw = item.to_dict()
    raw["source"]["identity"]["identity_token"] = "i_" + "0" * 64
    with pytest.raises(ContractError, match="token/key drift"):
        FeatureDefinition.from_dict(raw)

    raw = item.to_dict()
    raw["source"]["identity"]["namespace"] = "location"
    with pytest.raises(ContractError, match="namespace/key drift"):
        FeatureDefinition.from_dict(raw)

    raw = item.to_dict()
    raw["source"]["identity"]["identity_key"] = "recipe:"
    with pytest.raises(ContractError, match="invalid identity key"):
        FeatureDefinition.from_dict(raw)


def test_appendix_b_fixed_inventory_and_b5_outcomes(registry):
    keys = {item.key for item in registry}
    required = {
        "calendar.weekday", "calendar.weekend", "wearable.resting_hr_bpm",
        "wearable.hrv.fitbit_rmssd_ms", "wearable.hrv.apple_sdnn_ms",
        "sleep.duration_hours", "subjective.day_rating",
        "subjective.checkin.energy.am", "vitals.systolic_episode",
        "training.session", "training.group.chest.effective_sets",
        "running.session", "running.stop", "running.restart",
        "nutrition.logged.kcal", "nutrition.cronometer.energy_kcal",
        "meal.type.breakfast.occurred", "nutrition.water_ml",
        "event.social.occurred", "body.wcr", "adherence.word_kept",
        "adherence.timing.dose.on_time",
        # B.5 outcomes not repeated in the B.4 table are still normative.
        "recovery.rhr_score.apple", "recovery.rhr_score.fitbit",
        "recovery.hrv_score.apple_sdnn", "recovery.hrv_score.fitbit_rmssd",
        "recovery.score.apple", "recovery.score.fitbit",
        "running.progress.weekly_duration_min",
        "running.progress.weekly_distance_km",
        "running.progress.weekly_frequency",
        "running.progress.weekly_pace_min_per_km",
    }
    assert required <= keys

    fitness_movements = {
        match.group(1)
        for key in keys
        if (match := re.fullmatch(r"fitness\.test\.([a-z0-9-]+)\.(?:left|right|bilateral)\.value", key))
    }
    assert fitness_movements == set(toolkit_health.CATALOG)
    assert {
        key.removeprefix("fitness.athletic_axis.").removesuffix(".score")
        for key in keys if key.startswith("fitness.athletic_axis.")
    } == set(toolkit_health.ATHLETIC_AXES)
    assert {
        match.group(1)
        for key in keys
        if (match := re.fullmatch(r"fitness\.ratio\.([a-z0-9-]+)\..+", key))
    } == {item["key"] for item in toolkit_health.RATIO_SEED}
    assert {
        match.group(1)
        for key in keys
        if (match := re.fullmatch(r"nutrition\.nutrient\.([a-z0-9_]+)\.amount", key))
    } == {item["key"] for item in toolkit_health.MICRO_SEED}

    assert "pain.nrs.lateral-knee.left" in keys
    lateral = next(item for item in registry if item.key == "pain.nrs.lateral-knee.left")
    assert lateral.implemented is False
    assert lateral.temporal_type == "unavailable"
    assert lateral.candidate_enabled is False


def test_load_profiles_windows_and_no_forbidden_raw_features(registry):
    by_key = {item.key: item for item in registry}
    for key in (
        "training.group.chest.effective_sets", "running.duration_min",
        "training.working_sets",
    ):
        item = by_key[key]
        assert item.aggregation_id == "LP_LOAD"
        assert item.lag_eligibility == {
            "lags": (0, 1, 2, 3, 7), "windows": (1, 3, 7, 28, 90, 365),
        }
    all_keys = "\n".join(by_key)
    for forbidden in ("notes", "brain_dump_text", "raw_text", "rowid", "ingested_at", "side_effects"):
        assert forbidden not in all_keys
    assert not by_key["source.collector.hevy.status"].candidate_enabled
    assert not by_key["training.logged_vs_planned.chest.ratio"].candidate_enabled


def test_appendix_b_manual_event_and_pain_marker_profiles_are_exact(registry):
    by_key = {item.key: item for item in registry}

    brain_dump = by_key["subjective.brain_dump_logged"]
    assert brain_dump.completeness_profile == "MANUAL_EVENT:other_event"
    assert brain_dump.aggregation_id == "LP_SHORT"

    meal_time = by_key["meal.time_min"]
    assert meal_time.completeness_profile == "MANUAL_EVENT:food_identity"
    assert meal_time.aggregation_id == "LP_SHORT"

    for suffix in (
        "reported_onset", "first_observed_positive", "resolution_observed",
    ):
        marker = by_key[f"pain.nrs.anterior-knee.left.{suffix}"]
        assert marker.completeness_profile == "EXPLICIT_BINARY:pain"
        assert marker.aggregation_id == "LP_NONE"
        assert marker.zero_semantics == "explicit_binary"
        assert not marker.candidate_enabled

    balance = by_key["fitness.athletic_axis.balance.score"]
    assert "imbalance_correction" in balance.goal_families

    assert by_key["wearable.resting_hr_bpm"].lineage_roots == (
        "daily_metrics.resting_hr",
    )
    for provider in ("apple", "fitbit"):
        assert "daily_metrics.resting_hr" in by_key[
            f"recovery.rhr_score.{provider}"
        ].lineage_roots
        assert "daily_metrics.resting_hr" in by_key[
            f"recovery.score.{provider}"
        ].lineage_roots


def test_derived_fitness_lineages_are_exact_parent_unions(registry):
    by_key = {item.key: item for item in registry}
    ratio = toolkit_health.RATIO_SEED[0]
    if ratio["per_side"]:
        expected = tuple(sorted({
            f"fitness.{movement}.{side}.protocol"
            for movement in (ratio["num"], ratio["den"])
            for side in ("left", "right")
        }))
        assert by_key[f"fitness.ratio.{ratio['key']}.side_gap"].lineage_roots == expected

    balance = by_key["fitness.athletic_axis.balance.score"]
    assert balance.lineage_roots == tuple(sorted((
        "athletic_target.balance",
        "fitness.balance-stand.left.protocol",
        "fitness.balance-stand.right.protocol",
    )))
    assert {"equipment_note", "seconds", "source"} <= set(
        balance.source["columns"]
    )
    strength = by_key["fitness.athletic_axis.strength.score"]
    assert strength.source["table"] == "hevy_sets"
    assert strength.source["tables"] == ("athletic_targets", "hevy_sets")
    assert strength.lineage_roots == ("athletic_target.strength",)


def test_strength_axis_lineage_expands_exact_owner_targeted_lifts(conn):
    conn.execute(
        "INSERT INTO athletic_targets(axis,lift,target) "
        "VALUES('strength','Bench Press',100)"
    )
    by_key = {item.key: item for item in build_registry(conn, context())}
    strength = by_key["fitness.athletic_axis.strength.score"]
    digest = hashlib.sha256("bench press".encode()).hexdigest()
    assert strength.lineage_roots == tuple(sorted((
        "athletic_target.strength",
        f"hevy.exercise.exercise:{digest}.load_reps",
    )))
    assert strength.source["parameters"]["target_lifts"] == ("Bench Press",)
    exercise_prefix = f"training.exercise.i_{digest}."
    assert {
        key.rsplit(".", 1)[-1] for key in by_key if key.startswith(exercise_prefix)
    } == {
        "best_e1rm_kg", "distance_km", "duration_sec", "loaded_volume_kg",
        "mean_rpe", "session", "working_sets",
    }


def test_imbalance_goal_accepts_exact_bilateral_minimum_control_outcome(registry):
    by_key = {item.key: item for item in registry}
    outcome = "fitness.athletic_axis.balance.score"
    validate_goal_pair(
        "imbalance_correction", outcome, "increase", registered_keys=by_key,
    )
    with pytest.raises(GoalError, match="not allowed"):
        validate_goal_pair(
            "imbalance_correction", outcome, "decrease", registered_keys=by_key,
        )


def test_appendix_b_goal_family_membership_matrix_is_exact(registry):
    by_key = {item.key: item for item in registry}
    all_goals = {
        "body_recomposition", "cardio_improvement", "energy_focus_mood",
        "follow_through", "green_days", "imbalance_correction",
        "pain_reduction", "strength_and_muscle",
    }
    sleep_wearable = all_goals - {"imbalance_correction"}
    body_fitness = {
        "body_recomposition", "cardio_improvement", "imbalance_correction",
        "pain_reduction", "strength_and_muscle",
    }
    nutrition_adherence = {
        "body_recomposition", "cardio_improvement", "energy_focus_mood",
        "follow_through", "green_days", "pain_reduction",
        "strength_and_muscle",
    }

    assert set(by_key["calendar.weekday"].goal_families) == all_goals
    assert set(by_key["source.collector.hevy.status"].goal_families) == all_goals
    assert set(by_key["wearable.steps"].goal_families) == sleep_wearable
    assert set(by_key["sleep.duration_hours"].goal_families) == sleep_wearable
    assert set(by_key["training.session"].goal_families) == all_goals
    assert set(by_key["running.session"].goal_families) == all_goals
    assert set(by_key["event.social.occurred"].goal_families) == all_goals
    assert set(by_key["nutrition.logged.kcal"].goal_families) == nutrition_adherence
    assert set(by_key["adherence.word_kept"].goal_families) == nutrition_adherence
    assert set(by_key["body.weight_kg"].goal_families) == body_fitness
    assert set(by_key["fitness.athletic_axis.speed.score"].goal_families) == body_fitness
    assert set(by_key["pain.nrs.anterior-knee.left"].goal_families) == {
        "imbalance_correction", "pain_reduction",
    }
    assert set(by_key["recovery.score.apple"].goal_families) == {
        "cardio_improvement", "pain_reduction", "strength_and_muscle",
    }
    assert "green_days" in by_key["substance.caffeine_mg"].goal_families
    assert "imbalance_correction" not in by_key["sleep.duration_hours"].goal_families


def test_dynamic_goal_family_membership_and_medication_timing_are_exact(conn):
    conn.execute(
        "INSERT INTO meds_log(date,drug,dose_mg,time_taken,source) "
        "VALUES('2026-07-23','Fixture medication',10,'08:00','manual')"
    )
    conn.execute(
        "INSERT INTO skincare_products(brand,product_name,active) "
        "VALUES('Brand','Cream',1)"
    )
    conn.execute(
        "INSERT INTO weather(date,location,source) "
        "VALUES('2026-07-23','canonical timezone','fixture')"
    )
    by_key = {item.key: item for item in build_registry(conn, context())}
    medication = {
        key.rsplit(".", 1)[-1]: item
        for key, item in by_key.items() if key.startswith("medication.")
    }
    assert "follow_through" not in medication["dose_mg"].goal_families
    assert "follow_through" not in medication["regime"].goal_families
    assert "follow_through" in medication["first_dose_min"].goal_families
    assert "follow_through" in medication["last_dose_min"].goal_families
    skincare = next(
        item for key, item in by_key.items() if key.startswith("skincare.product.")
    )
    assert set(skincare.goal_families) == {"energy_focus_mood", "green_days"}
    weather = next(item for key, item in by_key.items() if key.startswith("weather."))
    assert set(weather.goal_families) == {
        "body_recomposition", "cardio_improvement", "energy_focus_mood",
        "follow_through", "green_days", "imbalance_correction",
        "pain_reduction", "strength_and_muscle",
    }


def test_goal_family_drift_is_rejected_without_broadening_outcomes(registry):
    by_key = {item.key: item for item in registry}
    raw = by_key["wearable.steps"].to_dict()
    raw["goal_families"].remove("green_days")
    with pytest.raises(RegistryError) as exc:
        validate_registry([raw])
    assert exc.value.code == "goal_family_mapping_drift"

    # B.6 readiness membership is broader than B.7's exact selected outcomes.
    # A feature being goal-relevant must never make it an allowed goal outcome.
    for goal, outcome, direction in (
        ("strength_and_muscle", "fitness.athletic_axis.speed.score", "increase"),
        ("strength_and_muscle", "fitness.test.sprint-30m.bilateral.value", "increase"),
        ("imbalance_correction", "fitness.ratio.hq.left.value", "decrease"),
        ("body_recomposition", "fitness.test.balance-stand.left.value", "increase"),
    ):
        assert goal in by_key[outcome].goal_families
        with pytest.raises(GoalError):
            validate_goal_pair(goal, outcome, direction, registered_keys=by_key)


def test_calendar_is_config_only_and_never_an_observation(registry, conn):
    calendar = [item for item in registry if item.pillar == "calendar"]
    assert {item.key for item in calendar} == {
        "calendar.weekday", "calendar.weekend", "calendar.month",
        "calendar.quarter", "calendar.season",
    }
    assert all(item.temporal_type == "static_config" for item in calendar)
    assert all(item.completeness_profile == "STATIC_CONFIG" for item in calendar)
    assert all(item.aggregation_id == "LP_NONE" for item in calendar)
    assert all(item.zero_semantics == "not_observation" for item in calendar)
    assert all(not item.candidate_enabled for item in calendar)

    from hermes_insights.adapters import environment
    observations = environment.load(
        conn, calendar,
        DateRange(date(2026, 7, 20), date(2026, 7, 23)), context(),
    )
    assert not any(item.feature_key.startswith("calendar.") for item in observations)


def test_static_config_contract_cannot_drift_into_observation_or_analysis(registry):
    item = next(value for value in registry if value.key == "calendar.weekday")
    for field, replacement, message in (
        ("temporal_type", "daily_measurement", "STATIC_CONFIG requires"),
        ("aggregation_id", "LP_STATE", "STATIC_CONFIG requires LP_NONE"),
        ("zero_semantics", "valid", "never an observation"),
        ("roles", ("confounder", "exposure"), "analytical candidate"),
        ("min_observations", 1, "zero observation minimum"),
    ):
        raw = item.to_dict()
        raw[field] = replacement
        if field == "aggregation_id":
            raw["lag_eligibility"] = {
                "lags": [0, 1, 2, 3, 7], "windows": ["point", 3, 7, 28],
            }
        with pytest.raises(ContractError, match=message):
            FeatureDefinition.from_dict(raw)


def test_environment_units_are_exact_not_generic(conn):
    conn.execute(
        "INSERT INTO weather(date,location,source) VALUES('2026-07-23','canonical timezone','fixture')"
    )
    conn.execute(
        "INSERT INTO air_quality(date,location,source) VALUES('2026-07-23','canonical timezone','fixture')"
    )
    by_key = {item.key: item for item in build_registry(conn, context())}
    weather = {key: item.unit for key, item in by_key.items() if key.startswith("weather.")}
    air = {key: item.unit for key, item in by_key.items() if key.startswith("air.")}
    assert weather and air
    assert "metric_unit" not in set(weather.values()) | set(air.values())
    assert {unit for key, unit in weather.items() if key.endswith(("temp_max_c", "temp_min_c"))} == {"deg_c"}
    assert {unit for key, unit in weather.items() if key.endswith(("wind_speed_max_ms", "wind_gusts_max_ms"))} == {"m/s"}
    assert {unit for key, unit in air.items() if key.endswith("pm2_5_ugm3")} == {"ug/m3"}
    assert {unit for key, unit in air.items() if key.endswith("grass_pollen")} == {"grains/m3"}


def test_catalog_expansions_use_runtime_constants_instead_of_a_copy(conn):
    constants = runtime_constants()
    constants["CATALOG"]["fixture-hold"] = {
        "name": "Fixture hold", "kind": "hold", "unilateral": 0,
    }
    constants["MICRO_SEED"].append({
        "key": "fixture_micro", "name": "Fixture micro", "unit": "mg",
    })
    registry = build_registry(conn, context(constants))
    keys = {item.key for item in registry}
    assert "fitness.test.fixture-hold.bilateral.value" in keys
    assert "nutrition.nutrient.fixture_micro.amount" in keys


def test_all_normative_catalog_expansions_and_cross_references_are_exact(conn):
    values = build_registry(conn, context())
    keys = {item.key for item in values}
    assert {
        match.group(1) for key in keys
        if (match := re.fullmatch(r"pain\.nrs\.([a-z0-9-]+)\.(?:left|right|central)", key))
    } == set(toolkit_health.PAIN_CAUSE_MAP) | {"lateral-knee"}
    assert {
        match.group(1) for key in keys
        if (match := re.fullmatch(r"pain\.self_test\.([a-z0-9-]+)\.(?:left|right|central)\.result", key))
    } == set(toolkit_health.SELF_TEST_CATALOG)

    expected_drill_targets = {
        (drill, region)
        for region, info in toolkit_health.PAIN_CAUSE_MAP.items()
        for cause in info["causes"]
        for drill in cause.get("drills", ())
    }
    actual_drill_targets = {
        (item.source["parameters"]["drill"], item.source["parameters"]["target"])
        for item in values
        if item.key.startswith("rehab.trial.") and item.key.endswith(".response")
    }
    assert actual_drill_targets == expected_drill_targets

    bad = runtime_constants()
    bad["PAIN_CAUSE_MAP"]["anterior-knee"]["causes"][0]["tests"].append(
        {"key": "uncited-test", "expect": "positive"}
    )
    with pytest.raises(RegistryError, match="reference drift"):
        build_registry(conn, context(bad))

    bad = runtime_constants()
    bad["MICRO_SEED"].append(deepcopy(bad["MICRO_SEED"][0]))
    with pytest.raises(RegistryError, match="must be unique"):
        build_registry(conn, context(bad))


def test_running_map_is_exact_and_extra_requires_provider_fixture(conn):
    constants = runtime_constants()
    constants["RUN_TYPE_KEYS"]["fun run"] = "running"
    with pytest.raises(RegistryError) as exc:
        build_registry(conn, context(constants))
    assert exc.value.code == "invalid_catalog"

    constants["RUN_TYPE_FIXTURES"] = {"fun run": {"provider": "fixture"}}
    registry = build_registry(conn, context(constants))
    running = next(item for item in registry if item.key == "running.session")
    assert "fun run" in running.source["parameters"]["run_type_keys"]


def test_hrv_provider_classification_and_unclassified_identity_namespace_match(conn):
    conn.executemany(
        "INSERT INTO daily_metrics(date,source,hrv_ms) VALUES(?,?,?)",
        [
            ("2026-07-22", "Fitbit", 40),
            ("2026-07-23", "ring-x", 42),
        ],
    )
    values = build_registry(conn, context())
    unclassified = [
        item for item in values
        if item.key.startswith("wearable.hrv.unclassified.")
    ]
    assert len(unclassified) == 1
    assert unclassified[0].source["identity"]["namespace"] == "source"
    assert unclassified[0].source["identity"]["original_label"] == "ring-x"


def test_dynamic_identity_normalization_alias_and_original_label(conn):
    canonical = identity_key("food", "Canonical Bowl")
    alias = identity_key("food", "Café Bowl")
    conn.execute(
        "INSERT INTO entity_aliases(entity_type,alias_key,canonical_key,canonical_label,source,active) "
        "VALUES('food',?,?,?,'owner',1)",
        (alias, canonical, "Canonical Bowl"),
    )
    alias_revision = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.executemany(
        "INSERT INTO nutrition_log(date,food_name,grams,source) VALUES(?,?,100,'manual')",
        [
            ("2026-07-20", "  Café   Bowl  "),
            ("2026-07-21", "CAFÉ BOWL"),
            ("2026-07-22", "Café-Bowl"),  # punctuation remains distinct
        ],
    )
    registry = build_registry(conn, context())
    named = [item for item in registry if item.key.startswith("food.named.") and item.key.endswith(".occurred")]
    assert len(named) == 2
    aliased = next(item for item in named if item.source["identity"]["identity_key"] == canonical)
    assert aliased.source["identity"]["original_label"] == "CAFÉ BOWL"
    assert aliased.source["identity"]["alias_revision_id"] == alias_revision
    assert aliased.key.split(".")[2] == "i_" + canonical.rsplit(":", 1)[1]


def test_medication_absence_can_zero_only_dose_and_count(conn):
    conn.execute(
        "INSERT INTO meds_log(date,drug,dose_mg,source) "
        "VALUES('2026-07-23','Fixture medication',10,'manual')"
    )
    medication = {
        item.key.rsplit(".", 1)[-1]: item
        for item in build_registry(conn, context())
        if item.key.startswith("medication.")
    }
    assert medication["dose_mg"].zero_semantics == "structural_zero_if_complete"
    assert medication["dose_count"].zero_semantics == "structural_zero_if_complete"
    assert medication["first_dose_min"].zero_semantics == "invalid"
    assert medication["last_dose_min"].zero_semantics == "invalid"
    assert medication["regime"].zero_semantics == "invalid"
    assert medication["rebound"].zero_semantics == "valid"
    assert medication["rebound"].unit == "binary"


def test_append_only_plan_history_registers_unlogged_routines_and_exercises(conn):
    conn.execute(
        "INSERT INTO training_plan_revisions(effective_from,schedule_json,routines_json,source) "
        "VALUES(?,?,?,'fixture')",
        (
            "2026-07-01", '[{"weekday":"Mon","routine_name":"Recovery Day"}]',
            '[{"routine_name":"Recovery Day","exercise_title":"Nordic Curl","target_sets":3}]',
        ),
    )
    values = build_registry(conn, context())
    _routine_key, routine_token = identity_key("other", "Recovery Day").split(":", 1)
    _exercise_key, exercise_token = identity_key("other", "Nordic Curl").split(":", 1)
    routine = "training.routine.i_" + routine_token + ".session"
    exercise = "training.exercise.i_" + exercise_token + ".session"
    plan = "training.plan.exercise.i_" + exercise_token + ".sets"
    by_key = {item.key: item for item in values}
    assert {routine, exercise, plan} <= set(by_key)
    assert by_key[plan].completeness_profile == "STATIC_CONFIG"
    assert by_key[plan].candidate_enabled is False


def test_skincare_expansion_registers_only_active_products(conn):
    conn.executemany(
        "INSERT INTO skincare_products(brand,product_name,active) VALUES(?,?,?)",
        [
            ("Brand", "Active Cream", 1),
            ("Brand", "Retired Cream", 0),
        ],
    )
    values = build_registry(conn, context())
    skincare = [
        item for item in values if item.key.startswith("skincare.product.")
    ]
    assert len(skincare) == 1
    assert skincare[0].source["identity"]["original_label"] == "Brand Active Cream"
    assert skincare[0].source["parameters"]["product_id"] == 1


def test_lab_value_unit_is_the_exact_catalog_unit(conn):
    conn.execute(
        "INSERT INTO lab_catalog("
        "canonical,display,unit,plaus_low,plaus_high,source"
        ") VALUES('Ferritin','Ferritin','ug/L',0,1000,'fixture')"
    )
    values = build_registry(conn, context())
    lab_value = next(
        item for item in values
        if item.key.startswith("lab.") and item.key.endswith(".value")
    )
    assert lab_value.unit == "ug/L"


def test_unknown_lab_identity_is_visible_but_never_unit_relabelled(conn):
    conn.execute(
        "INSERT INTO labs(date,test_name,value,unit,source) "
        "VALUES('2026-07-23','Owner New Test',7.5,'fixture-unit','manual')"
    )
    values = build_registry(conn, context())
    digest = hashlib.sha256("owner new test".encode()).hexdigest()
    prefix = f"lab.i_{digest}."
    unknown = [item for item in values if item.key.startswith(prefix)]
    assert {item.key.removeprefix(prefix) for item in unknown} == {
        "age_days", "reference_status",
    }
    assert all(item.roles == ("display", "readiness") for item in unknown)
    assert all(not item.candidate_enabled for item in unknown)
    assert all(item.source["parameters"]["catalog_connected"] is False for item in unknown)
    assert not any(item.key == prefix + "value" for item in values)


def test_lab_catalog_alias_matching_uses_the_exact_identity_normalizer(conn):
    conn.execute(
        "INSERT INTO lab_catalog("
        "canonical,display,unit,plaus_low,plaus_high,source,aliases"
        ") VALUES('Ferritin','Ferritin','ug/L',0,1000,'fixture',?)",
        ('["  IRON   STORE "]',),
    )
    conn.execute(
        "INSERT INTO labs(date,test_name,value,unit,source) "
        "VALUES('2026-07-23',' iron store ',50,'ug/L','manual')"
    )
    values = build_registry(conn, context())
    lab_features = [item for item in values if item.key.startswith("lab.")]
    assert len(lab_features) == 3
    assert {item.key.rsplit(".", 1)[-1] for item in lab_features} == {
        "age_days", "reference_status", "value",
    }
    assert all(
        item.source["identity"]["original_label"] == "Ferritin"
        for item in lab_features
    )


def test_assessment_expansion_collapses_normalized_variants_to_latest_label(conn):
    conn.executemany(
        "INSERT INTO assessments(date,scale,part,score,max_score) VALUES(?,?,?,?,?)",
        [
            ("2026-07-22", "Wellbeing", "Total", 3, 5),
            ("2026-07-23", "  WELLBEING ", " total ", 4, 5),
        ],
    )
    values = build_registry(conn, context())
    assessments = [item for item in values if item.key.startswith("assessment.")]
    assert len(assessments) == 2
    assert {item.key.rsplit(".", 1)[-1] for item in assessments} == {
        "fraction", "score",
    }
    assert all(
        item.source["identity"]["original_label"] == "  WELLBEING  /  total "
        for item in assessments
    )


def test_logged_rehab_target_expansion_keeps_latest_original_label(conn):
    drill = next(iter(toolkit_health.REHAB_CATALOG))
    conn.executemany(
        "INSERT INTO exercise_trial_log("
        "date,drill,target,response,source"
        ") VALUES(?,?,?,?,?)",
        [
            ("2026-07-22", drill, "Owner Target", "same", "manual"),
            ("2026-07-23", drill, "  OWNER TARGET ", "better", "manual"),
        ],
    )
    values = build_registry(conn, context())
    digest = hashlib.sha256("owner target".encode()).hexdigest()
    prefix = f"rehab.trial.{drill}.i_{digest}."
    dynamic = [item for item in values if item.key.startswith(prefix)]
    assert {item.key.rsplit(".", 1)[-1] for item in dynamic} == {
        "pain_during_nrs", "response",
    }
    assert all(
        item.source["identity"]["original_label"] == "  OWNER TARGET "
        for item in dynamic
    )


def test_registry_is_stable_under_database_row_order(tmp_path):
    def make(path, labels):
        connection = sqlite3.connect(path)
        connection.executescript(SCHEMA)
        for index, label in enumerate(labels):
            connection.execute(
                "INSERT INTO hevy_sets(date,workout_title,exercise_title,set_type,source) "
                "VALUES(?,?,?,?,?)",
                (f"2026-07-{index + 1:02d}", label, label, "normal", "hevy"),
            )
        connection.commit()
        return connection

    first = make(tmp_path / "a.db", ["Upper Day", "Lower Day"])
    second = make(tmp_path / "b.db", ["Lower Day", "Upper Day"])
    try:
        assert registry_content_checksum(build_registry(first, context())) == \
               registry_content_checksum(build_registry(second, context()))
    finally:
        first.close(); second.close()


def test_serialization_and_checksum_are_canonical(registry):
    document = serialize_registry(registry)
    assert document["contract_version"] == REGISTRY_VERSION
    assert document["registry_version"] == REGISTRY_VERSION
    assert document["feature_count"] == len(registry)
    assert document["registry_sha256"] == registry_content_checksum(registry)
    json.dumps(document, ensure_ascii=False, allow_nan=False)
    assert registry_content_checksum(reversed(registry)) == document["registry_sha256"]

    changed = registry[0].to_dict(); changed["display_name"] += " changed"
    changed_registry = (FeatureDefinition.from_dict(changed), *registry[1:])
    assert registry_content_checksum(changed_registry) != document["registry_sha256"]


def test_adapter_handshake_rejects_missing_id_version_and_load(registry):
    adapters = fake_adapters()
    validate_adapter_handshakes(registry, adapters)

    missing = dict(adapters); missing.pop("pain")
    with pytest.raises(RegistryError, match="missing"):
        validate_adapter_handshakes(registry, missing)
    wrong_id = dict(adapters)
    wrong_id["pain"] = SimpleNamespace(ADAPTER_ID="knee", REGISTRY_VERSION=REGISTRY_VERSION, load=lambda: None)
    with pytest.raises(RegistryError, match="id drift"):
        validate_adapter_handshakes(registry, wrong_id)
    wrong_version = fake_adapters("feature-registry-v2")
    with pytest.raises(RegistryError, match="version drift"):
        validate_adapter_handshakes(registry, wrong_version)
    no_load = dict(adapters)
    no_load["pain"] = SimpleNamespace(ADAPTER_ID="pain", REGISTRY_VERSION=REGISTRY_VERSION)
    with pytest.raises(RegistryError, match="load missing"):
        validate_adapter_handshakes(registry, no_load)
    wrong_signature = dict(adapters)
    wrong_signature["pain"] = SimpleNamespace(
        ADAPTER_ID="pain", REGISTRY_VERSION=REGISTRY_VERSION,
        load=lambda conn, definitions: [],
    )
    with pytest.raises(RegistryError, match="signature drift"):
        validate_adapter_handshakes(registry, wrong_signature)


def test_family_filter_is_registry_family_not_table_name(conn):
    sleep = build_registry(conn, context(), family="sleep")
    assert sleep and all(item.pillar == "sleep" for item in sleep)
    for unknown in ("daily_metrics", "calendar.weekday", "bad family"):
        with pytest.raises(RegistryError) as exc:
            build_registry(conn, context(), family=unknown)
        assert exc.value.validation
        assert exc.value.code == "validation_error"


def test_build_registry_is_select_only_and_no_schema_drift(conn):
    before = schema_fingerprint(conn)
    writes = []
    conn.set_trace_callback(lambda statement: writes.append(statement))
    allowed = {
        getattr(sqlite3, "SQLITE_READ", 20), getattr(sqlite3, "SQLITE_SELECT", 21),
        getattr(sqlite3, "SQLITE_FUNCTION", 31), getattr(sqlite3, "SQLITE_RECURSIVE", 33),
    }
    conn.set_authorizer(
        lambda action, _a1, _a2, _db, _trigger:
        sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
    )
    try:
        registry = build_registry(conn, context())
        assert registry
    finally:
        conn.set_authorizer(None)
        conn.set_trace_callback(None)
    assert schema_fingerprint(conn) == before
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in writes)


def test_observation_state_never_conflates_missing_and_zero():
    missing = Observation("nutrition.water_ml", "2026-07-23", None, "missing", unit="ml")
    zero = Observation("running.session", "2026-07-23", 0, "structural_zero", unit="binary")
    assert missing.to_dict()["value"] is None
    assert zero.to_dict()["value"] == 0
    with pytest.raises(ContractError):
        Observation("running.session", "2026-07-23", None, "structural_zero")
    with pytest.raises(ContractError):
        Observation("nutrition.water_ml", "2026-07-23", 0, "missing")
    with pytest.raises(ContractError, match="must carry a value"):
        Observation("nutrition.water_ml", "2026-07-23", None, "observed")
    with pytest.raises(ContractError, match="not ISO"):
        Observation("nutrition.water_ml", "2026-7-23", 1, "observed")
