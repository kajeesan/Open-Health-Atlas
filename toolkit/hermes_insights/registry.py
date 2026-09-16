"""Code-owned ``feature-registry-v1`` inventory and strict serialization.

Only this module expands registry templates.  It reads runtime catalog
constants and observed/configured identity labels through SELECT statements;
it never performs schema creation, mutation, fuzzy identity matching, or
column discovery as feature generation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import inspect
import json
import re
import sqlite3
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    ADAPTER_IDS, AdapterContext, ContractError, FEATURE_KEY_RE, FeatureDefinition,
    PILLARS, REGISTRY_VERSION, canonical_json, validate_feature_definition,
)


class RegistryError(RuntimeError):
    """Controlled registry failure suitable for the canonical CLI surface."""

    def __init__(self, code: str, message: str, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.validation = validation


KNOWN_FORMULA_IDS = frozenset({
    "calendar_weekday_v1", "calendar_weekend_v1", "calendar_month_v1",
    "calendar_quarter_v1", "calendar_season_v1", "latest_sync_freshness_v1",
    "latest_sync_status_v1", "selected_daily_provider_v1",
    "selected_daily_metric_v1", "source_filtered_hrv_v1",
    "sleep_duration_fallback_v1", "direct_value_v1", "strict_time_minutes_v1",
    "sleep_efficiency_v1", "trimmed_text_presence_v1", "checkin_bucket_mean_v1",
    "episode_value_v1", "daily_arithmetic_mean_v1", "medication_daily_v1",
    "hevy_session_v1", "working_sets_v1", "loaded_volume_v1", "known_mean_v1",
    "identity_session_v1", "epley_daily_max_v1", "known_sum_v1",
    "effective_sets_v1", "effective_plan_v1", "logged_planned_ratio_v1",
    "workout_type_daily_v1", "run_exact_map_v1", "ratio_of_known_totals_v1",
    "running_stop_v1", "running_restart_v1", "running_consecutive_weeks_v1",
    "days_since_positive_v1", "running_weekly_complete_v1",
    "complete_daily_sum_v1", "unit_converted_value_v1", "target_fraction_v1",
    "target_met_v1", "food_identity_daily_v1", "meal_type_event_v1",
    "explicit_binary_v1", "event_daily_v1", "body_source_tier_episode_v1",
    "same_row_wcr_v1", "photo_presence_v1", "fitness_protocol_value_v1",
    "side_gap_v1", "tested_ratio_v1", "distance_to_band_v1",
    "athletic_axis_v1", "pain_daily_latest_v1", "reported_onset_marker_v1",
    "first_positive_marker_v1", "resolution_marker_v1", "self_test_result_v1",
    "rehab_response_v1", "lab_episode_v1", "lab_reference_status_v1",
    "episode_age_v1", "assessment_fraction_v1", "adherence_status_v1",
    "habit_explicit_v1", "timing_adherence_v1", "skincare_explicit_v1",
    "weather_metric_v1", "wind_direction_component_v1", "air_metric_v1",
    "recovery_dev_score_v1", "recovery_combined_score_v1",
})

REQUIRED_RUNTIME_CONSTANTS = frozenset({
    "CATALOG", "RATIO_SEED", "ATHLETIC_AXES", "MOBILITY_NORM",
    "PAIN_CAUSE_MAP", "SELF_TEST_CATALOG", "REHAB_CATALOG", "MICRO_SEED",
    "RUN_TYPE_KEYS", "DM_METRICS",
})

RUN_TYPE_BASE = frozenset({
    "running", "outdoor running", "indoor running", "treadmill running",
})
TRAINING_GROUPS = ("arms", "back", "chest", "core", "glutes", "legs", "shoulders")
EVENT_CATEGORIES = (
    "activity", "food", "illness", "location", "meal", "medication_change",
    "other", "social", "stress", "training_phase", "travel",
)
EVENT_SCOPE = {
    "activity": "activity", "food": "food_identity", "illness": "illness",
    "location": "location", "meal": "food_identity",
    "medication_change": "medication", "other": "other_event",
    "social": "social", "stress": "stress", "training_phase": "training_phase",
    "travel": "travel",
}
MEAL_TYPES = ("breakfast", "dinner", "lunch", "snack")
PAIN_SIDES = ("central", "left", "right")
CHECKIN_BUCKETS = ("am", "eve", "pm")
CHECKIN_KINDS = ("energy", "focus", "mood")

# When multiple raw spellings collapse to one normalized or aliased identity,
# Appendix B requires metadata to retain the latest original label.  These are
# existing ordering columns, not inferred timestamps or numeric feature IDs.
# Tables without an ordering column are current config snapshots, where lexical
# ordering remains the deterministic tie-breaker.
LATEST_LABEL_ORDER_COLUMNS = {
    "air_quality": "date",
    "assessments": "id",
    "commitments": "id",
    "daily_metrics": "date",
    "exercise_submuscles": "created_at",
    "habits_log": "id",
    "hevy_sets": "id",
    "labs": "id",
    "meds_log": "id",
    "nutrition_log": "id",
    "skincare_products": "product_id",
    "supplement_products": "supplement_id",
    "weather": "date",
    "workouts": "date",
}

WEATHER_UNITS = {
    "cloud_cover_mean_pct": "percent",
    "condition": "category",
    "daylight_hours": "h",
    "et0_mm": "mm",
    "feels_like_max_c": "deg_c",
    "feels_like_min_c": "deg_c",
    "humidity_mean_pct": "percent",
    "precipitation_hours": "h",
    "precipitation_mm": "mm",
    "pressure_mean_hpa": "hPa",
    "rain_mm": "mm",
    "snowfall_cm": "cm",
    "solar_radiation_mj": "MJ/m2",
    "sunrise_min": "minute_of_day",
    "sunset_min": "minute_of_day",
    "sunshine_hours": "h",
    "temp_max_c": "deg_c",
    "temp_min_c": "deg_c",
    "uv_index_clear_sky_max": "uv_index",
    "uv_index_max": "uv_index",
    "weather_code": "wmo_code",
    "wind_dir_cos": "dimensionless",
    "wind_dir_sin": "dimensionless",
    "wind_gusts_max_ms": "m/s",
    "wind_speed_max_ms": "m/s",
}

AIR_UNITS = {
    "alder_pollen": "grains/m3",
    "birch_pollen": "grains/m3",
    "co_ugm3": "ug/m3",
    "european_aqi_max": "aqi_index",
    "european_aqi_mean": "aqi_index",
    "grass_pollen": "grains/m3",
    "mugwort_pollen": "grains/m3",
    "no2_ugm3": "ug/m3",
    "olive_pollen": "grains/m3",
    "ozone_ugm3": "ug/m3",
    "pm10_ugm3": "ug/m3",
    "pm2_5_ugm3": "ug/m3",
    "ragweed_pollen": "grains/m3",
    "so2_ugm3": "ug/m3",
}

VE_DAY = (5, 1)
VE_SAFETY_QUICK = (5, 1)
VE_SAFETY_DEVICE = (5, 2)
VE_QUARTERLY_HIGH = (5, 3)
VE_WELLBEING = (4, 1)
VE_AUTO_TRAIN = (4, 1)
VE_EVENT = (4, 1)
VE_FOOD = (4, 2)
VE_BODY = (4, 2)
VE_RPE = (3, 1)
VE_WEIGHT = (3, 2)
VE_LAB = (3, 3)
VE_LOW = (2, 1)
VE_CONTEXT = (1, 1)
VE_NONE = (0, None)

ALL_GOALS = (
    "body_recomposition", "cardio_improvement", "energy_focus_mood",
    "follow_through", "green_days", "imbalance_correction", "pain_reduction",
    "strength_and_muscle",
)

# Appendix B.6 cross-domain eligibility, plus the narrower B.7 goal-specific
# context families.  These sets describe whether a feature belongs in a
# goal-filtered readiness view; they do not make the feature an allowed goal
# outcome and do not, by themselves, earn the readiness goal multiplier.
GOALS_SLEEP_WEARABLE = (
    "body_recomposition", "cardio_improvement", "energy_focus_mood",
    "follow_through", "green_days", "pain_reduction",
    "strength_and_muscle",
)
GOALS_NUTRITION = (
    "body_recomposition", "cardio_improvement", "energy_focus_mood",
    "follow_through", "green_days", "pain_reduction",
    "strength_and_muscle",
)
GOALS_BODY_FITNESS = (
    "body_recomposition", "cardio_improvement", "imbalance_correction",
    "pain_reduction", "strength_and_muscle",
)
GOALS_ADHERENCE = (
    "body_recomposition", "cardio_improvement", "energy_focus_mood",
    "follow_through", "green_days", "pain_reduction",
    "strength_and_muscle",
)
GOALS_MEDICATION_SUPPLEMENT = (
    "energy_focus_mood", "green_days", "pain_reduction",
)
GOALS_PAIN_TIMELINE = ("imbalance_correction", "pain_reduction")
GOALS_SKINCARE = ("energy_focus_mood", "green_days")


def _expected_goal_families(feature: FeatureDefinition) -> tuple[str, ...]:
    """Return the frozen Appendix B.6/B.7 readiness-membership union.

    This is deliberately separate from Appendix B.7 outcome validation and
    from the selected-outcome/prerequisite/leading-family multiplier gate.
    """

    key, pillar = feature.key, feature.pillar
    if pillar in {"calendar", "source", "weather", "air", "event"}:
        return ALL_GOALS
    if pillar in {"wearable", "sleep"}:
        return GOALS_SLEEP_WEARABLE
    if pillar == "training":
        if key.startswith(("training.plan.", "training.logged_vs_planned.")):
            return ("follow_through",)
        return ALL_GOALS
    if pillar in {"running", "cardio"}:
        if key.startswith("running.progress."):
            return ("cardio_improvement",)
        return ALL_GOALS
    if pillar in {"nutrition", "food", "meal"}:
        return GOALS_NUTRITION
    if pillar == "body":
        return (("body_recomposition",) if key == "body.photo_session"
                else GOALS_BODY_FITNESS)
    if pillar == "fitness":
        return GOALS_BODY_FITNESS
    if pillar in {"pain", "rehab"}:
        return GOALS_PAIN_TIMELINE
    if pillar == "recovery":
        return ("cardio_improvement", "pain_reduction", "strength_and_muscle")
    if pillar == "adherence":
        return GOALS_ADHERENCE
    if pillar == "medication":
        values = set(GOALS_MEDICATION_SUPPLEMENT)
        if key.endswith((".first_dose_min", ".last_dose_min")):
            values.add("follow_through")
        return tuple(sorted(values))
    if pillar == "supplement":
        return GOALS_MEDICATION_SUPPLEMENT
    if pillar == "skincare":
        return GOALS_SKINCARE
    if pillar == "substance":
        return ("energy_focus_mood", "green_days")
    if key == "subjective.brain_dump_logged":
        return ("energy_focus_mood", "follow_through", "green_days")
    if key == "subjective.day_rating":
        return ("green_days",)
    if key in {"subjective.energy", "subjective.focus", "subjective.mood"} \
            or key.startswith("subjective.checkin."):
        return ("energy_focus_mood",)
    return ()


def _ctx_constant(context: AdapterContext, name: str) -> Any:
    values = context.constants
    if isinstance(values, Mapping) and name in values:
        return values[name]
    if hasattr(values, name):
        return getattr(values, name)
    raise RegistryError("registry_constant_missing", f"required runtime constant {name} is absent")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """SELECT-only schema introspection compatible with the canonical authorizer."""

    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _query_dicts(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, tuple(params))
    names = [item[0] for item in cur.description or ()]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _normal(label: str) -> str:
    if not isinstance(label, str):
        raise RegistryError("invalid_identity", "identity label must be text", True)
    return " ".join(unicodedata.normalize("NFKC", label).strip().split()).casefold()


def _identity(namespace: str, label: str, *, canonical_key: str | None = None,
              alias_revision_id: int | None = None) -> dict[str, Any]:
    normalized = _normal(label)
    if not normalized:
        raise RegistryError("invalid_identity", "identity label must not be blank", True)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    identity_key = canonical_key or f"{namespace}:{digest}"
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}:[0-9a-f]{64}", identity_key):
        token_digest = identity_key.rsplit(":", 1)[1]
    elif identity_key.startswith("recipe:"):
        token_digest = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()
    else:
        raise RegistryError("invalid_identity", f"invalid canonical identity for {namespace}")
    return {
        "namespace": namespace, "identity_key": identity_key,
        "identity_token": f"i_{token_digest}", "original_label": label,
        "alias_revision_id": alias_revision_id,
    }


def _recipe_identity(recipe_id: str, label: str) -> dict[str, Any]:
    rid = unicodedata.normalize("NFKC", recipe_id).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,159}", rid):
        raise RegistryError("invalid_identity", f"invalid recipe id {recipe_id!r}")
    return _identity("recipe", label, canonical_key=f"recipe:{rid}")


def _source(table: str, columns: Sequence[str], merge: str, *,
            tables: Sequence[str] | None = None,
            identity: Mapping[str, Any] | None = None,
            parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table": table, "columns": tuple(columns), "merge": merge,
    }
    if tables is not None:
        result["tables"] = tuple(sorted(set(tables)))
    if identity is not None:
        result["identity"] = dict(identity)
    if parameters is not None:
        result["parameters"] = dict(parameters)
    return result


@dataclass
class _Builder:
    entries: dict[str, FeatureDefinition]

    def add(
        self, key: str, display_name: str, pillar: str, *, table: str,
        columns: Sequence[str], merge: str, unit: str, direction: str,
        temporal_type: str, cadence: str, target_pct: int | float | None,
        actionability: str, aggregation_id: str, confounder_role: str,
        value_kind: str, zero_semantics: str, stale_after_days: int | None,
        adapter: str, source_semantics: str, roles: Iterable[str],
        formula_id: str, completeness_profile: str, candidate_enabled: bool,
        lineage_roots: Iterable[str], readiness: tuple[int, int | None],
        valid_from: str | None = None, implemented: bool = True,
        identity_dimension: str | None = None, redundancy_group: str | None = None,
        preferred_rank: int = 100, goal_families: Iterable[str] = (),
        source_tables: Sequence[str] | None = None,
        source_identity: Mapping[str, Any] | None = None,
        source_parameters: Mapping[str, Any] | None = None,
        min_observations: int = 30,
    ) -> FeatureDefinition:
        if key in self.entries:
            raise RegistryError("duplicate_feature_key", f"duplicate feature key: {key}")
        if formula_id not in KNOWN_FORMULA_IDS:
            raise RegistryError("unknown_formula_id", f"unknown formula id: {formula_id}")
        profile = {
            "LP_STATE": ((0, 1, 2, 3, 7), ("point", 3, 7, 28)),
            "LP_SHORT": ((0, 1, 2), ("point", 3, 7, 28)),
            "LP_EVENT": ((0, 1, 2, 3, 7), (1, 3, 7, 28)),
            "LP_LOAD": ((0, 1, 2, 3, 7), (1, 3, 7, 28, 90, 365)),
            "LP_ENV": ((0, 1, 2, 3), ("point", 3, 7, 28)),
            "LP_TIMED": ((0,), ("point",)),
            "LP_SLOW": ((), ()), "LP_NONE": ((), ()),
        }[aggregation_id]
        feature = FeatureDefinition(
            key=key, display_name=display_name, pillar=pillar,
            source=_source(table, columns, merge, tables=source_tables,
                           identity=source_identity, parameters=source_parameters),
            unit=unit, direction=direction, temporal_type=temporal_type,
            valid_from=valid_from, coverage={"cadence": cadence, "target_pct": target_pct},
            actionability=actionability,
            lag_eligibility={"lags": profile[0], "windows": profile[1]},
            confounder_role=confounder_role, value_kind=value_kind,
            zero_semantics=zero_semantics, stale_after_days=stale_after_days,
            adapter=adapter, implemented=implemented, identity_dimension=identity_dimension,
            source_semantics=source_semantics, min_observations=min_observations,
            redundancy_group=redundancy_group, preferred_rank=preferred_rank,
            roles=tuple(sorted(set(roles))), formula_id=formula_id,
            aggregation_id=aggregation_id, completeness_profile=completeness_profile,
            candidate_enabled=candidate_enabled,
            lineage_roots=tuple(sorted(set(lineage_roots))),
            goal_families=tuple(sorted(set(goal_families))),
            readiness_value=readiness[0], readiness_effort=readiness[1],
        )
        self.entries[key] = feature
        return feature


def _add_calendar_and_source(builder: _Builder, dm_metrics: Sequence[str]) -> None:
    for suffix, formula, unit, kind in (
        ("weekday", "calendar_weekday_v1", "weekday_0_6", "ordinal"),
        ("weekend", "calendar_weekend_v1", "binary", "binary"),
        ("month", "calendar_month_v1", "month_1_12", "ordinal"),
        ("quarter", "calendar_quarter_v1", "quarter_1_4", "ordinal"),
        ("season", "calendar_season_v1", "category", "categorical"),
    ):
        builder.add(
            f"calendar.{suffix}", suffix.replace("_", " ").title(), "calendar",
            table="calendar", columns=("date",), merge="generated_per_date", unit=unit,
            direction="neutral", temporal_type="static_config", cadence="static",
            target_pct=None, actionability="context", aggregation_id="LP_NONE",
            confounder_role="context", value_kind=kind, zero_semantics="not_observation",
            stale_after_days=None, adapter="environment",
            source_semantics="generated_calendar", roles=("confounder", "display"),
            formula_id=formula, completeness_profile="STATIC_CONFIG",
            candidate_enabled=False, lineage_roots=(f"calendar.{suffix}",),
            goal_families=ALL_GOALS, readiness=VE_NONE, min_observations=0,
        )

    for source_name in ("air", "google-health", "hevy", "weather"):
        token = source_name
        for suffix, formula, unit, kind in (
            ("freshness_days", "latest_sync_freshness_v1", "days", "continuous"),
            ("status", "latest_sync_status_v1", "status", "categorical"),
        ):
            builder.add(
                f"source.collector.{token}.{suffix}",
                f"{source_name} collector {suffix.replace('_', ' ')}", "source",
                table="source_sync_runs",
                columns=("source", "completed_at", "status", "coverage_from", "coverage_to"),
                merge="latest_completed_run", unit=unit, direction="neutral",
                temporal_type="static_config", cadence="static", target_pct=None,
                actionability="non_actionable", aggregation_id="LP_NONE",
                confounder_role="source_era", value_kind=kind,
                zero_semantics="not_observation", stale_after_days=2,
                adapter="daily", source_semantics="operational_sync",
                roles=("confounder", "display", "readiness"), formula_id=formula,
                completeness_profile="STATIC_CONFIG", candidate_enabled=False,
                lineage_roots=(f"sync.{source_name}",),
                goal_families=ALL_GOALS, readiness=VE_NONE,
                source_parameters={"source": source_name}, min_observations=0,
            )
    for metric in sorted(set(dm_metrics)):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", metric):
            raise RegistryError("invalid_catalog", f"invalid DM_METRICS key: {metric!r}")
        builder.add(
            f"source.daily_metrics.{metric}.provider", f"{metric} selected provider", "source",
            table="daily_metrics", columns=("date", "source", metric),
            merge="daily_metric_source_selection", unit="provider", direction="neutral",
            temporal_type="static_config", cadence="daily", target_pct=None,
            actionability="non_actionable", aggregation_id="LP_NONE",
            confounder_role="source_era", value_kind="categorical",
            zero_semantics="not_observation", stale_after_days=2, adapter="daily",
            source_semantics="selected_automated_daily",
            roles=("confounder", "display", "readiness"),
            formula_id="selected_daily_provider_v1", completeness_profile="STATIC_CONFIG",
            candidate_enabled=False, lineage_roots=(f"daily_metrics.{metric}",),
            goal_families=ALL_GOALS, readiness=VE_NONE,
            source_parameters={"metric": metric}, min_observations=0,
        )


def _add_daily_features(builder: _Builder) -> None:
    wearable = (
        ("resting_hr_bpm", "resting_hr", "bpm", "daily_metrics.resting_hr", "lower_better"),
        ("hr_min_bpm", "hr_min", "bpm", "daily_metrics.hr_min", "neutral"),
        ("hr_avg_bpm", "hr_avg", "bpm", "daily_metrics.hr_avg", "neutral"),
        ("hr_max_bpm", "hr_max", "bpm", "daily_metrics.hr_max", "neutral"),
        ("steps", "steps", "count", "daily_metrics.steps", "neutral"),
        ("active_energy_kcal", "active_energy_kcal", "kcal", "daily_metrics.active_energy", "neutral"),
        ("basal_energy_kcal", "basal_energy_kcal", "kcal", "daily_metrics.basal_energy", "neutral"),
        ("exercise_min", "exercise_min", "min", "daily_metrics.exercise_min", "neutral"),
        ("distance_km", "distance_km", "km", "daily_metrics.distance", "neutral"),
        ("flights", "flights", "count", "daily_metrics.flights", "neutral"),
        ("respiratory_rate_brpm", "respiratory_rate", "breaths_per_min", "daily_metrics.respiratory_rate", "neutral"),
        ("spo2_pct", "spo2_pct", "percent", "daily_metrics.spo2", "neutral"),
        ("walking_hr_avg_bpm", "walking_hr_avg", "bpm", "daily_metrics.walking_hr", "neutral"),
    )
    for suffix, column, unit, lineage, direction in wearable:
        builder.add(
            f"wearable.{suffix}", suffix.replace("_", " ").title(), "wearable",
            table="daily_metrics", columns=("date", "source", column),
            merge="fitbit_primary_apple_distance_primary_lexical_fallback",
            unit=unit, direction=direction, temporal_type="daily_measurement",
            cadence="daily", target_pct=100, actionability="context",
            aggregation_id="LP_STATE", confounder_role="context",
            value_kind="count" if unit == "count" else "continuous",
            zero_semantics="invalid", stale_after_days=2, adapter="daily",
            source_semantics="selected_automated_daily",
            roles=("exposure", "readiness"), formula_id="selected_daily_metric_v1",
            completeness_profile="AUTO_VALUE", candidate_enabled=True,
            lineage_roots=(lineage,), goal_families=GOALS_SLEEP_WEARABLE,
            readiness=VE_CONTEXT,
            source_parameters={"metric": column},
        )

    for key, provider, algorithm in (
        ("wearable.hrv.fitbit_rmssd_ms", "fitbit", "rmssd"),
        ("wearable.hrv.apple_sdnn_ms", "apple", "sdnn"),
    ):
        builder.add(
            key, f"{provider.title()} HRV {algorithm.upper()}", "wearable",
            table="daily_metrics", columns=("date", "source", "hrv_ms", "hrv_sdnn"),
            merge="source_predicate_no_cross_source_merge", unit="ms",
            direction="neutral", temporal_type="daily_measurement", cadence="daily",
            target_pct=100, actionability="context", aggregation_id="LP_STATE",
            confounder_role="source_era", value_kind="continuous",
            zero_semantics="invalid", stale_after_days=2, adapter="daily",
            source_semantics="source_specific_daily",
            roles=("exposure", "readiness"), formula_id="source_filtered_hrv_v1",
            completeness_profile="AUTO_VALUE", candidate_enabled=True,
            lineage_roots=(f"hrv.{provider}.{algorithm}",),
            goal_families=GOALS_SLEEP_WEARABLE, readiness=VE_CONTEXT,
            source_parameters={"provider": provider, "algorithm": algorithm},
        )

    builder.add(
        "sleep.duration_hours", "Sleep duration", "sleep", table="sleep_log",
        columns=("date", "time_asleep_hours", "source"),
        merge="sleep_log_then_selected_daily_metrics", unit="hours",
        direction="target_range", temporal_type="outcome", cadence="daily",
        target_pct=90, actionability="outcome", aggregation_id="LP_STATE",
        confounder_role="outcome", value_kind="continuous", zero_semantics="invalid",
        stale_after_days=2, adapter="daily", source_semantics="selected_automated_daily",
        roles=("exposure", "outcome", "readiness"),
        formula_id="sleep_duration_fallback_v1", completeness_profile="AUTO_VALUE",
        candidate_enabled=True, lineage_roots=("sleep.duration",),
        goal_families=GOALS_SLEEP_WEARABLE, readiness=VE_WELLBEING,
        source_tables=("daily_metrics", "sleep_log"),
    )
    sleep_direct = (
        ("time_in_bed_hours", "time_in_bed_hours", "hours", "continuous", "neutral", "direct_value_v1"),
        ("deep_min", "deep_min", "min", "continuous", "neutral", "direct_value_v1"),
        ("rem_min", "rem_min", "min", "continuous", "neutral", "direct_value_v1"),
        ("light_min", "light_min", "min", "continuous", "neutral", "direct_value_v1"),
        ("awake_min", "awake_min", "min", "continuous", "lower_better", "direct_value_v1"),
        ("quality", "quality", "ordinal_1_5", "ordinal", "higher_better", "direct_value_v1"),
        ("awakenings", "awakenings", "count", "count", "lower_better", "direct_value_v1"),
        ("bedtime_min", "bedtime", "minute_of_day", "time_minutes", "neutral", "strict_time_minutes_v1"),
        ("wake_min", "wake_time", "minute_of_day", "time_minutes", "neutral", "strict_time_minutes_v1"),
        ("efficiency", "time_asleep_hours", "ratio", "ratio", "higher_better", "sleep_efficiency_v1"),
    )
    outcome_sleep = {"quality", "awakenings", "efficiency"}
    for suffix, column, unit, kind, direction, formula in sleep_direct:
        is_outcome = suffix in outcome_sleep
        cols = ("date", "time_asleep_hours", "time_in_bed_hours", "source") if suffix == "efficiency" else ("date", column, "source")
        builder.add(
            f"sleep.{suffix}", suffix.replace("_", " ").title(), "sleep",
            table="sleep_log", columns=cols, merge="one_row_per_date",
            unit=unit, direction=direction,
            temporal_type="outcome" if is_outcome else "daily_measurement",
            cadence="daily", target_pct=90,
            actionability="outcome" if is_outcome else "indirect",
            aggregation_id="LP_STATE", confounder_role="outcome" if is_outcome else "context",
            value_kind=kind, zero_semantics="invalid", stale_after_days=2,
            adapter="daily", source_semantics="manual_daily",
            roles=(("exposure", "outcome", "readiness") if is_outcome else ("exposure", "readiness")),
            formula_id=formula, completeness_profile="MANUAL_VALUE",
            candidate_enabled=True, lineage_roots=(f"sleep.{suffix}",),
            goal_families=GOALS_SLEEP_WEARABLE, readiness=VE_WELLBEING,
            redundancy_group="sleep.stage" if suffix in {"deep_min", "rem_min", "light_min", "awake_min"} else None,
            preferred_rank=5 if suffix in {"deep_min", "rem_min", "light_min", "awake_min"} else 100,
        )

    subjective = (
        ("day_rating", "ordinal_1_3", "ordinal", "higher_better", ("green_days",), VE_DAY, True),
        ("focus", "ordinal_1_5", "ordinal", "higher_better", ("energy_focus_mood",), VE_WELLBEING, True),
        ("energy", "ordinal_1_5", "ordinal", "higher_better", ("energy_focus_mood",), VE_WELLBEING, True),
        ("mood", "ordinal_1_5", "ordinal", "higher_better", ("energy_focus_mood",), VE_WELLBEING, True),
        ("emotional_regulation", "ordinal_1_5", "ordinal", "higher_better", (), VE_WELLBEING, True),
        ("anxiety", "ordinal_1_5", "ordinal", "lower_better", (), VE_WELLBEING, True),
        ("motivation", "ordinal_1_5", "ordinal", "higher_better", (), VE_WELLBEING, True),
        ("stress", "ordinal_1_5", "ordinal", "lower_better", (), VE_WELLBEING, True),
    )
    for suffix, unit, kind, direction, goals, readiness, candidate in subjective:
        builder.add(
            f"subjective.{suffix}", suffix.replace("_", " ").title(), "subjective",
            table="subjective_daily", columns=("date", suffix, "source"),
            merge="one_row_per_date", unit=unit, direction=direction,
            temporal_type="outcome", cadence="daily", target_pct=90,
            actionability="outcome", aggregation_id="LP_STATE",
            confounder_role="outcome", value_kind=kind, zero_semantics="invalid",
            stale_after_days=2, adapter="daily", source_semantics="manual_daily",
            roles=("outcome", "readiness"), formula_id="direct_value_v1",
            completeness_profile="MANUAL_VALUE", candidate_enabled=candidate,
            lineage_roots=(f"subjective.{suffix}",), goal_families=goals,
            readiness=readiness,
        )
    for suffix, unit in (("caffeine_mg", "mg"), ("alcohol_units", "units")):
        builder.add(
            f"substance.{suffix}", suffix.replace("_", " ").title(), "substance",
            table="subjective_daily", columns=("date", suffix, "source"),
            merge="one_row_per_date", unit=unit, direction="neutral",
            temporal_type="daily_measurement", cadence="daily", target_pct=90,
            actionability="direct", aggregation_id="LP_SHORT", confounder_role="context",
            value_kind="continuous", zero_semantics="valid", stale_after_days=2,
            adapter="daily", source_semantics="manual_daily", roles=("exposure", "readiness"),
            formula_id="direct_value_v1", completeness_profile="MANUAL_VALUE",
            candidate_enabled=True, lineage_roots=(f"substance.{suffix}",),
            goal_families=("energy_focus_mood", "green_days"), readiness=VE_RPE,
        )
    builder.add(
        "subjective.brain_dump_logged", "Brain dump logged", "subjective",
        table="subjective_daily", columns=("date", "brain_dump", "source"),
        merge="one_row_per_date", unit="binary", direction="neutral",
        temporal_type="event_occurrence", cadence="event", target_pct=None,
        actionability="direct", aggregation_id="LP_SHORT", confounder_role="none",
        value_kind="binary", zero_semantics="structural_zero_if_complete",
        stale_after_days=2, adapter="daily", source_semantics="manual_event",
        roles=("exposure", "readiness"), formula_id="trimmed_text_presence_v1",
        completeness_profile="MANUAL_EVENT:other_event", candidate_enabled=True,
        lineage_roots=("subjective.brain_dump_presence",),
        goal_families=("energy_focus_mood", "follow_through", "green_days"),
        readiness=VE_WELLBEING,
    )

    for kind in CHECKIN_KINDS:
        for bucket in CHECKIN_BUCKETS:
            builder.add(
                f"subjective.checkin.{kind}.{bucket}", f"{bucket.upper()} {kind}", "subjective",
                table="checkins", columns=("id", "date", "time", "kind", "value", "source"),
                merge="daily_kind_time_bucket_mean", unit="ordinal_1_5",
                direction="higher_better", temporal_type="outcome", cadence="daily",
                target_pct=90, actionability="outcome", aggregation_id="LP_STATE",
                confounder_role="outcome", value_kind="ordinal", zero_semantics="invalid",
                stale_after_days=2, adapter="daily", source_semantics="manual_daily",
                roles=("outcome", "readiness"), formula_id="checkin_bucket_mean_v1",
                completeness_profile="MANUAL_VALUE", candidate_enabled=True,
                lineage_roots=(f"checkin.{kind}.{bucket}",),
                goal_families=("energy_focus_mood",), readiness=VE_WELLBEING,
                source_parameters={"kind": kind, "bucket": bucket},
            )

    for metric, unit in (("systolic", "mmHg"), ("diastolic", "mmHg"), ("resting_hr", "bpm")):
        for suffix, formula, aggregation, temporal in (
            ("episode", "episode_value_v1", "LP_TIMED", "slow_measurement"),
            ("mean", "daily_arithmetic_mean_v1", "LP_STATE", "outcome"),
        ):
            builder.add(
                f"vitals.{metric}_{suffix}", f"{metric} {suffix}", "vitals",
                table="vitals", columns=("id", "date", "time", metric, "source"),
                merge="episode_or_daily_mean", unit=unit, direction="neutral",
                temporal_type=temporal, cadence="daily", target_pct=None,
                actionability="outcome" if suffix == "mean" else "context",
                aggregation_id=aggregation, confounder_role="outcome" if suffix == "mean" else "context",
                value_kind="episode" if suffix == "episode" else "continuous",
                zero_semantics="invalid", stale_after_days=7, adapter="manual",
                source_semantics="manual_daily", roles=("outcome", "readiness"),
                formula_id=formula, completeness_profile="MANUAL_VALUE",
                candidate_enabled=suffix == "mean", lineage_roots=(f"vitals.{metric}",),
                readiness=VE_SAFETY_DEVICE,
            )


def _add_training_and_running(builder: _Builder, run_type_keys: Sequence[str]) -> None:
    training = (
        ("session", "binary", "binary", "hevy_session_v1", "AUTO_EVENT", "structural_zero_if_complete", VE_AUTO_TRAIN),
        ("working_sets", "count", "count", "working_sets_v1", "AUTO_EVENT", "structural_zero_if_complete", VE_AUTO_TRAIN),
        ("loaded_volume_kg", "kg_reps", "continuous", "loaded_volume_v1", "AUTO_VALUE", "valid", VE_AUTO_TRAIN),
        ("mean_rpe", "rpe_0_10", "continuous", "known_mean_v1", "AUTO_VALUE", "invalid", VE_RPE),
    )
    for suffix, unit, kind, formula, completeness, zero, readiness in training:
        builder.add(
            f"training.{suffix}", suffix.replace("_", " ").title(), "training",
            table="hevy_sets",
            columns=("id", "date", "set_type", "weight_kg", "reps", "rpe", "source"),
            merge="daily_non_warmup_working_sets", unit=unit, direction="neutral",
            temporal_type="rolling_exposure", cadence="event", target_pct=None,
            actionability="direct", aggregation_id="LP_LOAD", confounder_role="context",
            value_kind=kind, zero_semantics=zero, stale_after_days=2,
            adapter="training", source_semantics="automated_event",
            roles=("exposure", "readiness"), formula_id=formula,
            completeness_profile=completeness, candidate_enabled=True,
            lineage_roots=(f"hevy.{suffix}",),
            goal_families=ALL_GOALS,
            readiness=readiness,
        )
    for group in TRAINING_GROUPS:
        builder.add(
            f"training.group.{group}.effective_sets", f"{group.title()} effective sets", "training",
            table="hevy_sets",
            columns=("id", "date", "exercise_title", "set_type", "source"),
            merge="authored_first_submuscle_then_coarse_once_per_working_set",
            unit="effective_sets", direction="neutral", temporal_type="rolling_exposure",
            cadence="event", target_pct=None, actionability="direct",
            aggregation_id="LP_LOAD", confounder_role="context", value_kind="continuous",
            zero_semantics="structural_zero_if_complete", stale_after_days=2,
            adapter="training", source_semantics="automated_event",
            roles=("exposure", "readiness"), formula_id="effective_sets_v1",
            completeness_profile="AUTO_EVENT", candidate_enabled=True,
            lineage_roots=(f"hevy.group.{group}",),
            goal_families=ALL_GOALS, readiness=VE_AUTO_TRAIN,
            source_tables=("exercise_muscles", "exercise_submuscles", "hevy_sets"),
            source_parameters={"group": group},
        )
        builder.add(
            f"training.plan.group.{group}.effective_sets", f"Planned {group} effective sets", "training",
            table="training_plan_revisions",
            columns=("id", "effective_from", "schedule_json", "routines_json"),
            merge="effective_revision_forward_only", unit="effective_sets",
            direction="neutral", temporal_type="static_config", cadence="static",
            target_pct=None, actionability="non_actionable", aggregation_id="LP_NONE",
            confounder_role="context", value_kind="config", zero_semantics="not_observation",
            stale_after_days=None, adapter="training", source_semantics="static_config",
            roles=("display", "readiness"), formula_id="effective_plan_v1",
            completeness_profile="STATIC_CONFIG", candidate_enabled=False,
            lineage_roots=(f"training_plan.group.{group}",),
            goal_families=("follow_through",), readiness=VE_NONE,
            source_parameters={"group": group}, min_observations=0,
        )
        builder.add(
            f"training.logged_vs_planned.{group}.ratio", f"{group.title()} logged versus planned", "training",
            table="training_plan_revisions",
            columns=("id", "effective_from", "schedule_json", "routines_json"),
            merge="positive_plan_and_complete_hevy_coverage", unit="ratio",
            direction="neutral", temporal_type="rolling_exposure", cadence="event",
            target_pct=None, actionability="context", aggregation_id="LP_LOAD",
            confounder_role="context", value_kind="ratio", zero_semantics="derived_strict",
            stale_after_days=2, adapter="training", source_semantics="derived",
            roles=("display", "readiness"), formula_id="logged_planned_ratio_v1",
            completeness_profile="DERIVED", candidate_enabled=False,
            lineage_roots=(f"hevy.group.{group}", f"training_plan.group.{group}"),
            goal_families=("follow_through",), readiness=VE_NONE,
            source_tables=("hevy_sets", "training_plan_revisions"),
            source_parameters={"group": group}, min_observations=0,
        )

    running = (
        ("session", "binary", "binary", "run_exact_map_v1", "AUTO_EVENT", "structural_zero_if_complete", "direct"),
        ("duration_min", "min", "continuous", "known_sum_v1", "AUTO_VALUE", "invalid", "indirect"),
        ("distance_km", "km", "continuous", "known_sum_v1", "AUTO_VALUE", "invalid", "indirect"),
        ("kcal", "kcal", "continuous", "known_sum_v1", "AUTO_VALUE", "invalid", "indirect"),
        ("pace_min_per_km", "min_per_km", "ratio", "ratio_of_known_totals_v1", "DERIVED", "derived_strict", "context"),
        ("intensity_kcal_per_min", "kcal_per_min", "ratio", "ratio_of_known_totals_v1", "DERIVED", "derived_strict", "context"),
    )
    for suffix, unit, kind, formula, completeness, zero, actionability in running:
        builder.add(
            f"running.{suffix}", suffix.replace("_", " ").title(), "running",
            table="workouts", columns=("date", "type", "minutes", "kcal", "km", "source"),
            merge="exact_run_type_daily_multiset", unit=unit, direction="lower_better" if suffix == "pace_min_per_km" else "neutral",
            temporal_type="rolling_exposure", cadence="event", target_pct=None,
            actionability=actionability, aggregation_id="LP_LOAD", confounder_role="context",
            value_kind=kind, zero_semantics=zero, stale_after_days=2,
            adapter="running", source_semantics="automated_event",
            roles=("exposure", "readiness"), formula_id=formula,
            completeness_profile=completeness, candidate_enabled=True,
            lineage_roots=(f"running.{suffix if suffix != 'session' else 'occurrence'}",),
            goal_families=ALL_GOALS, readiness=VE_AUTO_TRAIN,
            source_parameters={"run_type_keys": tuple(run_type_keys), "field": suffix},
        )
    for suffix, formula, aggregation, candidate in (
        ("stop", "running_stop_v1", "LP_EVENT", True),
        ("restart", "running_restart_v1", "LP_EVENT", True),
        ("consecutive_weeks", "running_consecutive_weeks_v1", "LP_NONE", False),
        ("days_since", "days_since_positive_v1", "LP_LOAD", True),
    ):
        builder.add(
            f"running.{suffix}", suffix.replace("_", " ").title(), "running",
            table="workouts", columns=("date", "type", "source"),
            merge="known_complete_exact_run_timeline", unit="count" if suffix == "consecutive_weeks" else ("days" if suffix == "days_since" else "binary"),
            direction="neutral", temporal_type="event_occurrence" if suffix in {"stop", "restart"} else "rolling_exposure",
            cadence="event", target_pct=None, actionability="context",
            aggregation_id=aggregation, confounder_role="context",
            value_kind="count" if suffix in {"consecutive_weeks", "days_since"} else "binary",
            zero_semantics="derived_strict", stale_after_days=2, adapter="running",
            source_semantics="derived", roles=("display", "exposure", "readiness"),
            formula_id=formula, completeness_profile="DERIVED", candidate_enabled=candidate,
            lineage_roots=("running.occurrence",), goal_families=ALL_GOALS,
            readiness=VE_AUTO_TRAIN, source_tables=("source_sync_runs", "workouts"),
            source_parameters={"restart_complete_no_run_days": 21,
                               "run_type_keys": tuple(run_type_keys)},
        )
    for suffix, unit, direction, lineage in (
        ("weekly_duration_min", "min_per_week", "higher_better", "running.duration_min"),
        ("weekly_distance_km", "km_per_week", "higher_better", "running.distance_km"),
        ("weekly_frequency", "runs_per_week", "higher_better", "running.occurrence"),
        ("weekly_pace_min_per_km", "min_per_km", "lower_better", "running.pace_min_per_km"),
    ):
        builder.add(
            f"running.progress.{suffix}", suffix.replace("_", " ").title(), "running",
            table="workouts", columns=("date", "type", "minutes", "km", "source"),
            merge="complete_configured_timezone_iso_week", unit=unit, direction=direction,
            temporal_type="outcome", cadence="weekly", target_pct=100,
            actionability="outcome", aggregation_id="LP_NONE", confounder_role="outcome",
            value_kind="continuous", zero_semantics="derived_strict", stale_after_days=2,
            adapter="running", source_semantics="derived", roles=("outcome", "readiness"),
            formula_id="running_weekly_complete_v1", completeness_profile="DERIVED",
            candidate_enabled=True, lineage_roots=(lineage,),
            goal_families=("cardio_improvement",), readiness=VE_AUTO_TRAIN,
            source_tables=("source_sync_runs", "workouts"),
            source_parameters={"run_type_keys": tuple(run_type_keys)},
        )


def _add_nutrition_and_events(builder: _Builder, micro_seed: Sequence[Mapping[str, Any]]) -> None:
    for suffix, unit in (
        ("kcal", "kcal"), ("protein_g", "g"), ("carbs_g", "g"),
        ("fat_g", "g"), ("fiber_g", "g"),
    ):
        builder.add(
            f"nutrition.logged.{suffix}", f"Logged {suffix.replace('_', ' ')}", "nutrition",
            table="nutrition_log", columns=("id", "date", suffix, "source"),
            merge="daily_sum_requires_complete_nutrition_total", unit=unit,
            direction="target_range", temporal_type="daily_measurement", cadence="daily",
            target_pct=90, actionability="direct", aggregation_id="LP_STATE",
            confounder_role="context", value_kind="continuous", zero_semantics="valid",
            stale_after_days=2, adapter="nutrition", source_semantics="manual_daily",
            roles=("exposure", "readiness"), formula_id="complete_daily_sum_v1",
            completeness_profile="MANUAL_VALUE", candidate_enabled=True,
            lineage_roots=(f"nutrition.logged.{suffix}",),
            goal_families=GOALS_NUTRITION, readiness=VE_FOOD,
            source_tables=("capture_completeness_revisions", "nutrition_log"),
            source_parameters={"required_scope": "nutrition_total"},
        )
    for suffix, nutrient, unit in (
        ("energy_kcal", "energy_kcal", "kcal"), ("protein_g", "protein_g", "g"),
        ("carbs_g", "carbs_g", "g"), ("fat_g", "fat_g", "g"),
        ("fiber_g", "fiber_g", "g"),
    ):
        builder.add(
            f"nutrition.cronometer.{suffix}", f"Cronometer {suffix.replace('_', ' ')}", "nutrition",
            table="nutrient_daily", columns=("date", "nutrient", "amount", "unit", "source"),
            merge="date_nutrient_source_exact_unit", unit=unit, direction="target_range",
            temporal_type="daily_measurement", cadence="daily", target_pct=100,
            actionability="direct", aggregation_id="LP_STATE", confounder_role="context",
            value_kind="continuous", zero_semantics="invalid", stale_after_days=2,
            adapter="nutrition", source_semantics="automated_daily",
            roles=("exposure", "readiness"), formula_id="unit_converted_value_v1",
            completeness_profile="AUTO_VALUE", candidate_enabled=True,
            lineage_roots=(f"nutrient_daily.{nutrient}",),
            goal_families=GOALS_NUTRITION, readiness=VE_FOOD,
            source_parameters={"nutrient": nutrient},
        )
    seen_micro: set[str] = set()
    for item in micro_seed:
        key = item.get("key") if isinstance(item, Mapping) else None
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) or key in seen_micro:
            raise RegistryError("invalid_catalog", "MICRO_SEED keys must be unique safe tokens")
        seen_micro.add(key)
        unit = item.get("unit")
        if not isinstance(unit, str) or not unit:
            raise RegistryError("invalid_catalog", f"MICRO_SEED {key} lacks a unit")
        base = f"nutrition.nutrient.{key}"
        builder.add(
            f"{base}.amount", f"{item.get('name', key)} amount", "nutrition",
            table="nutrient_daily", columns=("date", "nutrient", "amount", "unit", "source"),
            merge="date_nutrient_source_exact_unit", unit=unit, direction="target_range",
            temporal_type="daily_measurement", cadence="daily", target_pct=100,
            actionability="indirect", aggregation_id="LP_STATE", confounder_role="context",
            value_kind="continuous", zero_semantics="invalid", stale_after_days=2,
            adapter="nutrition", source_semantics="automated_daily",
            roles=("exposure", "readiness"), formula_id="unit_converted_value_v1",
            completeness_profile="AUTO_VALUE", candidate_enabled=True,
            lineage_roots=(f"nutrient_daily.{key}",),
            goal_families=GOALS_NUTRITION, readiness=VE_FOOD,
            source_parameters={"nutrient": key, "unit": unit},
        )
        for suffix, formula, kind, unit_name in (
            ("target_fraction", "target_fraction_v1", "ratio", "fraction"),
            ("target_met", "target_met_v1", "binary", "binary"),
        ):
            builder.add(
                f"{base}.{suffix}", f"{item.get('name', key)} {suffix.replace('_', ' ')}", "nutrition",
                table="nutrient_daily", columns=("date", "nutrient", "amount", "unit", "source"),
                merge="amount_with_effective_target", unit=unit_name,
                direction="higher_better", temporal_type="daily_measurement",
                cadence="daily", target_pct=100, actionability="indirect",
                aggregation_id="LP_STATE", confounder_role="context", value_kind=kind,
                zero_semantics="derived_strict", stale_after_days=2,
                adapter="nutrition", source_semantics="derived",
                roles=("exposure", "readiness"), formula_id=formula,
                completeness_profile="DERIVED", candidate_enabled=True,
                lineage_roots=(f"nutrient_daily.{key}", f"nutrition_target.{key}"),
                goal_families=GOALS_NUTRITION, readiness=VE_FOOD,
                source_tables=("nutrient_daily", "nutrition_targets"),
                source_parameters={"nutrient": key},
            )

    builder.add(
        "nutrition.water_ml", "Water", "nutrition", table="intake",
        columns=("date", "water_ml", "source"), merge="one_row_per_date",
        unit="ml", direction="target_range", temporal_type="daily_measurement",
        cadence="daily", target_pct=90, actionability="direct",
        aggregation_id="LP_STATE", confounder_role="context", value_kind="continuous",
        zero_semantics="invalid", stale_after_days=2, adapter="nutrition",
        source_semantics="manual_daily", roles=("exposure", "readiness"),
        formula_id="direct_value_v1", completeness_profile="MANUAL_VALUE",
        candidate_enabled=True, lineage_roots=("intake.water",),
        goal_families=GOALS_NUTRITION, readiness=VE_LOW,
    )
    for meal_type in MEAL_TYPES:
        builder.add(
            f"meal.type.{meal_type}.occurred", f"{meal_type.title()} occurred", "meal",
            table="nutrition_log", columns=("id", "date", "meal_type", "source"),
            merge="explicit_meal_type_daily", unit="binary", direction="neutral",
            temporal_type="event_occurrence", cadence="event", target_pct=None,
            actionability="direct", aggregation_id="LP_SHORT", confounder_role="none",
            value_kind="binary", zero_semantics="structural_zero_if_complete",
            stale_after_days=2, adapter="nutrition", source_semantics="manual_event",
            roles=("exposure", "readiness"), formula_id="meal_type_event_v1",
            completeness_profile="MANUAL_EVENT:food_identity", candidate_enabled=True,
            lineage_roots=(f"meal.type.{meal_type}",),
            goal_families=GOALS_NUTRITION, readiness=VE_FOOD,
            source_parameters={"meal_type": meal_type},
        )
    builder.add(
        "meal.time_min", "Meal time", "meal", table="nutrition_log",
        columns=("id", "date", "time", "source"), merge="strict_explicit_time_only",
        unit="minute_of_day", direction="neutral", temporal_type="event_occurrence",
        cadence="event", target_pct=None, actionability="direct", aggregation_id="LP_SHORT",
        confounder_role="none", value_kind="time_minutes", zero_semantics="invalid",
        stale_after_days=2, adapter="nutrition", source_semantics="manual_event",
        roles=("exposure", "readiness"), formula_id="strict_time_minutes_v1",
        completeness_profile="MANUAL_EVENT:food_identity", candidate_enabled=True,
        lineage_roots=("meal.explicit_time",), goal_families=GOALS_NUTRITION,
        readiness=VE_FOOD,
    )

    for category in EVENT_CATEGORIES:
        scope = EVENT_SCOPE[category]
        for suffix, unit, kind in (
            ("occurred", "binary", "binary"), ("count", "count", "count"),
            ("duration_min", "min", "continuous"),
            ("intensity_mean", "ordinal_1_5", "continuous"),
            ("valence_mean", "ordinal_-2_2", "continuous"),
        ):
            builder.add(
                f"event.{category}.{suffix}", f"{category} {suffix.replace('_', ' ')}", "event",
                table="event_exposures",
                columns=("id", "date", "time", "category", "duration_min", "intensity", "valence", "source", "voided"),
                merge="active_category_daily_all_known_values", unit=unit,
                direction="neutral", temporal_type="event_occurrence", cadence="event",
                target_pct=None, actionability="direct", aggregation_id="LP_EVENT",
                confounder_role="context" if category in {"illness", "stress", "training_phase", "travel"} else "none",
                value_kind=kind, zero_semantics="structural_zero_if_complete",
                stale_after_days=7, adapter="events", source_semantics="manual_event",
                roles=(("confounder", "exposure", "readiness") if category in {"illness", "stress", "training_phase", "travel"} else ("exposure", "readiness")),
                formula_id="event_daily_v1", completeness_profile=f"MANUAL_EVENT:{scope}",
                candidate_enabled=True, lineage_roots=(f"event.{category}",),
                goal_families=ALL_GOALS,
                readiness=VE_EVENT, source_parameters={"category": category, "scope": scope, "field": suffix},
            )


def _add_quarterly_and_pain(
    builder: _Builder, conn: sqlite3.Connection,
    catalog: Mapping[str, Mapping[str, Any]],
    ratio_seed: Sequence[Mapping[str, Any]], athletic_axes: Mapping[str, Any],
    mobility_norm: Mapping[str, Any], pain_map: Mapping[str, Any],
    self_tests: Mapping[str, Any], rehab_catalog: Mapping[str, Any],
) -> None:
    fitness_columns = (
        "id", "date", "movement", "side", "load_kg", "reps", "seconds",
        "rating", "cm", "degrees", "passed", "equipment_note", "source",
        "voided",
    )
    body = (
        ("weight_kg", "kg", "target_range", VE_WEIGHT),
        ("waist_cm", "cm", "lower_better", VE_BODY),
        ("chest_cm", "cm", "neutral", VE_BODY),
        ("arm_cm", "cm", "neutral", VE_BODY),
        ("thigh_cm", "cm", "neutral", VE_BODY),
        ("hip_cm", "cm", "neutral", VE_BODY),
        ("neck_cm", "cm", "neutral", VE_BODY),
        ("body_fat_pct", "percent", "lower_better", VE_WEIGHT),
    )
    for suffix, unit, direction, readiness in body:
        builder.add(
            f"body.{suffix}", suffix.replace("_", " ").title(), "body",
            table="body_metrics", columns=("id", "date", suffix, "source"),
            merge="same_date_source_tier_then_latest_id", unit=unit, direction=direction,
            temporal_type="slow_measurement", cadence="episodic", target_pct=None,
            actionability="outcome", aggregation_id="LP_SLOW", confounder_role="outcome",
            value_kind="episode", zero_semantics="invalid", stale_after_days=120,
            adapter="quarterly", source_semantics="slow_episode",
            roles=("exposure", "outcome", "readiness"),
            formula_id="body_source_tier_episode_v1", completeness_profile="SLOW_EPISODE",
            candidate_enabled=True, lineage_roots=(f"body.{suffix}",),
            goal_families=GOALS_BODY_FITNESS,
            readiness=readiness,
        )
    builder.add(
        "body.photo_session", "Body photo session", "body", table="body_metrics",
        columns=("id", "date", "photo_ref", "source"), merge="nonempty_photo_presence",
        unit="binary", direction="neutral", temporal_type="slow_measurement",
        cadence="episodic", target_pct=None, actionability="context",
        aggregation_id="LP_SLOW", confounder_role="context", value_kind="episode",
        zero_semantics="invalid", stale_after_days=120, adapter="quarterly",
        source_semantics="slow_episode", roles=("display", "readiness"),
        formula_id="photo_presence_v1", completeness_profile="SLOW_EPISODE",
        candidate_enabled=False, lineage_roots=("body.photo",),
        goal_families=("body_recomposition",), readiness=VE_BODY,
    )
    builder.add(
        "body.wcr", "Waist to chest ratio", "body", table="body_metrics",
        columns=("id", "date", "waist_cm", "chest_cm", "source"),
        merge="same_row_positive_waist_chest_only", unit="ratio",
        direction="lower_better", temporal_type="slow_measurement", cadence="episodic",
        target_pct=None, actionability="outcome", aggregation_id="LP_SLOW",
        confounder_role="outcome", value_kind="ratio", zero_semantics="derived_strict",
        stale_after_days=120, adapter="quarterly", source_semantics="derived",
        roles=("exposure", "outcome", "readiness"), formula_id="same_row_wcr_v1",
        completeness_profile="DERIVED", candidate_enabled=True,
        lineage_roots=("body.chest_cm", "body.waist_cm"),
        goal_families=GOALS_BODY_FITNESS, readiness=VE_BODY,
    )

    kind_unit = {
        "strength": "kg_e1rm", "hold": "seconds", "timed": "seconds",
        "control": "ordinal_1_3", "distance": "cm", "rom": "degrees",
        "binary": "binary",
    }
    kind_direction = {
        "strength": "higher_better", "hold": "higher_better",
        "timed": "lower_better", "control": "higher_better",
        "distance": "higher_better", "rom": "higher_better",
        "binary": "higher_better",
    }
    for movement in sorted(catalog):
        info = catalog[movement]
        if not FEATURE_KEY_RE.fullmatch(f"fitness.test.{movement}.value") or not isinstance(info, Mapping):
            raise RegistryError("invalid_catalog", f"invalid CATALOG movement: {movement!r}")
        kind = info.get("kind")
        if kind not in kind_unit:
            raise RegistryError("invalid_catalog", f"CATALOG {movement} has unknown kind")
        sides = ("left", "right") if bool(info.get("unilateral")) else ("bilateral",)
        stale = 180 if movement in mobility_norm else 120
        for side in sides:
            builder.add(
                f"fitness.test.{movement}.{side}.value", f"{info.get('name', movement)} {side}", "fitness",
                table="fitness_tests", columns=fitness_columns,
                merge="latest_protocol_comparable_nonvoid_episode", unit=kind_unit[kind],
                direction=kind_direction[kind], temporal_type="slow_measurement",
                cadence="episodic", target_pct=None, actionability="outcome",
                aggregation_id="LP_SLOW", confounder_role="outcome", value_kind="episode",
                zero_semantics="valid" if kind in {"timed", "distance", "rom", "binary"} else "invalid",
                stale_after_days=stale, adapter="quarterly", source_semantics="slow_episode",
                roles=("exposure", "outcome", "readiness"),
                formula_id="fitness_protocol_value_v1", completeness_profile="SLOW_EPISODE",
                candidate_enabled=True, lineage_roots=(f"fitness.{movement}.{side}.protocol",),
                goal_families=GOALS_BODY_FITNESS,
                readiness=VE_QUARTERLY_HIGH,
                source_parameters={"movement": movement, "side": side, "kind": kind},
            )
        if bool(info.get("unilateral")):
            builder.add(
                f"fitness.test.{movement}.side_gap", f"{info.get('name', movement)} side gap", "fitness",
                table="fitness_tests",
                columns=fitness_columns,
                merge="same_protocol_comparable_left_right_pair", unit="fraction",
                direction="lower_better", temporal_type="slow_measurement", cadence="episodic",
                target_pct=None, actionability="outcome", aggregation_id="LP_SLOW",
                confounder_role="outcome", value_kind="ratio", zero_semantics="derived_strict",
                stale_after_days=stale, adapter="quarterly", source_semantics="derived",
                roles=("exposure", "outcome", "readiness"), formula_id="side_gap_v1",
                completeness_profile="DERIVED", candidate_enabled=True,
                lineage_roots=(f"fitness.{movement}.left.protocol", f"fitness.{movement}.right.protocol"),
                goal_families=GOALS_BODY_FITNESS, readiness=VE_QUARTERLY_HIGH,
                source_parameters={"movement": movement},
            )

    seen_ratios: set[str] = set()
    for item in ratio_seed:
        key = item.get("key") if isinstance(item, Mapping) else None
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", key) or key in seen_ratios:
            raise RegistryError("invalid_catalog", "RATIO_SEED keys must be unique safe tokens")
        seen_ratios.add(key)
        sides = ("left", "right") if item.get("per_side") else ("bilateral",)
        numerator, denominator = item.get("num"), item.get("den")
        if numerator not in catalog or denominator not in catalog:
            raise RegistryError("invalid_catalog", f"ratio {key} references unknown tests")
        for side in sides:
            roots = (f"fitness.{denominator}.{side}.protocol", f"fitness.{numerator}.{side}.protocol")
            for suffix, formula, unit, direction in (
                ("value", "tested_ratio_v1", "ratio", "neutral"),
                ("distance_to_band", "distance_to_band_v1", "ratio", "lower_better"),
            ):
                builder.add(
                    f"fitness.ratio.{key}.{side}.{suffix}", f"{key} {side} {suffix.replace('_', ' ')}", "fitness",
                    table="fitness_tests", columns=fitness_columns,
                    merge="same_protocol_ratio_prerequisites", unit=unit, direction=direction,
                    temporal_type="slow_measurement", cadence="episodic", target_pct=None,
                    actionability="outcome", aggregation_id="LP_SLOW", confounder_role="outcome",
                    value_kind="ratio", zero_semantics="derived_strict", stale_after_days=120,
                    adapter="quarterly", source_semantics="derived",
                    roles=("exposure", "outcome", "readiness"), formula_id=formula,
                    completeness_profile="DERIVED", candidate_enabled=True,
                    lineage_roots=roots, goal_families=GOALS_BODY_FITNESS,
                    readiness=VE_QUARTERLY_HIGH,
                    source_parameters={"ratio": key, "side": side},
                )
        if item.get("per_side"):
            side_gap_roots = tuple(sorted({
                f"fitness.{movement}.{side}.protocol"
                for movement in (numerator, denominator)
                for side in ("left", "right")
            }))
            builder.add(
                f"fitness.ratio.{key}.side_gap", f"{key} ratio side gap", "fitness",
                table="fitness_tests", columns=fitness_columns,
                merge="comparable_left_right_ratio_pair", unit="fraction",
                direction="lower_better", temporal_type="slow_measurement", cadence="episodic",
                target_pct=None, actionability="outcome", aggregation_id="LP_SLOW",
                confounder_role="outcome", value_kind="ratio", zero_semantics="derived_strict",
                stale_after_days=120, adapter="quarterly", source_semantics="derived",
                roles=("exposure", "outcome", "readiness"), formula_id="side_gap_v1",
                completeness_profile="DERIVED", candidate_enabled=True,
                lineage_roots=side_gap_roots,
                goal_families=GOALS_BODY_FITNESS, readiness=VE_QUARTERLY_HIGH,
                source_parameters={"ratio": key},
            )

    strength_target_lifts: tuple[str, ...] = ()
    if _table_exists(conn, "athletic_targets"):
        strength_target_lifts = tuple(
            row["lift"] for row in _query_dicts(
                conn,
                "SELECT lift FROM athletic_targets "
                "WHERE axis='strength' AND lift IS NOT NULL AND lift!='' "
                "ORDER BY lift",
            )
            if isinstance(row["lift"], str) and _normal(row["lift"])
        )
    for axis in sorted(athletic_axes):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", axis):
            raise RegistryError("invalid_catalog", f"invalid ATHLETIC_AXES key: {axis!r}")
        config = athletic_axes[axis]
        movement = config.get("test")
        aggregation = config.get("agg")
        if aggregation == "e1rm_lift":
            axis_table = "hevy_sets"
            axis_columns = (
                "id", "date", "exercise_title", "set_type", "weight_kg",
                "reps", "source",
            )
            axis_merge = "owner_targeted_lift_scores_28_date_mean"
            axis_tables = ("athletic_targets", "hevy_sets")
            axis_roots = {
                "athletic_target.strength",
                *(
                    f"hevy.exercise.{_identity('exercise', lift)['identity_key']}.load_reps"
                    for lift in strength_target_lifts
                ),
            }
            axis_parameters = {
                "aggregation": aggregation, "axis": axis,
                "target_lifts": strength_target_lifts,
            }
        else:
            if movement not in catalog:
                raise RegistryError(
                    "invalid_catalog", f"ATHLETIC_AXES {axis} references unknown test",
                )
            axis_table = "fitness_tests"
            axis_columns = fitness_columns
            axis_merge = "owner_target_axis_weakest_side_exact"
            axis_tables = ("athletic_targets", "fitness_tests")
            sides = ("left", "right") if catalog[movement].get("unilateral") else ("bilateral",)
            axis_roots = {
                f"athletic_target.{axis}",
                *(f"fitness.{movement}.{side}.protocol" for side in sides),
            }
            axis_parameters = {
                "aggregation": aggregation, "axis": axis, "movement": movement,
            }
        builder.add(
            f"fitness.athletic_axis.{axis}.score", f"{axis.title()} athletic score", "fitness",
            table=axis_table, columns=axis_columns, merge=axis_merge,
            unit="score_0_100",
            direction="higher_better", temporal_type="slow_measurement", cadence="episodic",
            target_pct=None, actionability="outcome", aggregation_id="LP_SLOW",
            confounder_role="outcome", value_kind="continuous", zero_semantics="derived_strict",
            stale_after_days=120, adapter="quarterly", source_semantics="derived",
            roles=("display", "outcome", "readiness"), formula_id="athletic_axis_v1",
            completeness_profile="DERIVED", candidate_enabled=True,
            lineage_roots=axis_roots,
            goal_families=GOALS_BODY_FITNESS,
            readiness=VE_QUARTERLY_HIGH,
            source_tables=axis_tables, source_parameters=axis_parameters,
        )

    for provider in ("apple", "fitbit"):
        builder.add(
            f"recovery.rhr_score.{provider}", f"{provider.title()} RHR score", "recovery",
            table="daily_metrics", columns=("date", "source", "resting_hr"),
            merge="fourteen_prior_same_source_observations", unit="score_0_100",
            direction="higher_better", temporal_type="outcome", cadence="daily",
            target_pct=100, actionability="outcome", aggregation_id="LP_STATE",
            confounder_role="outcome", value_kind="continuous", zero_semantics="derived_strict",
            stale_after_days=2, adapter="daily", source_semantics="derived",
            roles=("outcome", "readiness"), formula_id="recovery_dev_score_v1",
            completeness_profile="DERIVED", candidate_enabled=True,
            lineage_roots=("daily_metrics.resting_hr", f"recovery.rhr.{provider}"),
            goal_families=("cardio_improvement", "pain_reduction", "strength_and_muscle"),
            readiness=VE_WELLBEING, source_parameters={"provider": provider, "prior_n": 14},
        )
    for provider, algorithm in (("apple", "apple_sdnn"), ("fitbit", "fitbit_rmssd")):
        builder.add(
            f"recovery.hrv_score.{algorithm}", f"{provider.title()} HRV score", "recovery",
            table="daily_metrics", columns=("date", "source", "hrv_ms", "hrv_sdnn"),
            merge="fourteen_prior_same_source_observations", unit="score_0_100",
            direction="higher_better", temporal_type="outcome", cadence="daily",
            target_pct=100, actionability="outcome", aggregation_id="LP_STATE",
            confounder_role="outcome", value_kind="continuous", zero_semantics="derived_strict",
            stale_after_days=2, adapter="daily", source_semantics="derived",
            roles=("outcome", "readiness"), formula_id="recovery_dev_score_v1",
            completeness_profile="DERIVED", candidate_enabled=True,
            lineage_roots=(f"hrv.{provider}.{'sdnn' if provider == 'apple' else 'rmssd'}",),
            goal_families=("cardio_improvement", "pain_reduction", "strength_and_muscle"),
            readiness=VE_WELLBEING,
            source_parameters={"provider": provider, "algorithm": algorithm, "prior_n": 14},
        )
        builder.add(
            f"recovery.score.{provider}", f"{provider.title()} recovery score", "recovery",
            table="daily_metrics", columns=("date", "source", "resting_hr", "hrv_ms", "hrv_sdnn"),
            merge="mean_available_same_source_components", unit="score_0_100",
            direction="higher_better", temporal_type="outcome", cadence="daily",
            target_pct=100, actionability="outcome", aggregation_id="LP_STATE",
            confounder_role="outcome", value_kind="continuous", zero_semantics="derived_strict",
            stale_after_days=2, adapter="daily", source_semantics="derived",
            roles=("outcome", "readiness"), formula_id="recovery_combined_score_v1",
            completeness_profile="DERIVED", candidate_enabled=True,
            lineage_roots=(
                "daily_metrics.resting_hr",
                f"hrv.{provider}.{'sdnn' if provider == 'apple' else 'rmssd'}",
                f"recovery.rhr.{provider}",
            ),
            goal_families=("cardio_improvement", "pain_reduction", "strength_and_muscle"),
            readiness=VE_WELLBEING, source_parameters={"provider": provider},
        )

    pain_regions = sorted(pain_map)
    for region in pain_regions + ["lateral-knee"]:
        implemented = region != "lateral-knee"
        for side in PAIN_SIDES:
            base = f"pain.nrs.{region}.{side}"
            builder.add(
                base, f"{region} {side} pain NRS", "pain", table="pain_log",
                columns=("id", "date", "region", "side", "intensity", "source", "voided"),
                merge="latest_id_per_date_region_side_nonvoid", unit="nrs_0_10",
                direction="lower_better", temporal_type="outcome" if implemented else "unavailable",
                cadence="episodic", target_pct=None, actionability="outcome",
                aggregation_id="LP_NONE", confounder_role="outcome", value_kind="ordinal",
                zero_semantics="valid", stale_after_days=120, adapter="pain",
                source_semantics="manual_daily", roles=("outcome", "readiness"),
                formula_id="pain_daily_latest_v1", completeness_profile="MANUAL_VALUE",
                candidate_enabled=implemented, lineage_roots=(f"pain.{region}.{side}",),
                goal_families=GOALS_PAIN_TIMELINE, readiness=VE_SAFETY_QUICK,
                implemented=implemented, source_parameters={"region": region, "side": side},
            )
            for suffix, formula in (
                ("reported_onset", "reported_onset_marker_v1"),
                ("first_observed_positive", "first_positive_marker_v1"),
                ("resolution_observed", "resolution_marker_v1"),
            ):
                builder.add(
                    f"{base}.{suffix}", f"{region} {side} {suffix.replace('_', ' ')}", "pain",
                    table="pain_log",
                    columns=("id", "date", "region", "side", "intensity", "reported_onset_date", "onset_precision", "voided"),
                    merge="ordered_explicit_pain_timeline", unit="binary",
                    direction="neutral", temporal_type="event_occurrence" if implemented else "unavailable",
                    cadence="episodic", target_pct=None, actionability="non_actionable",
                    aggregation_id="LP_NONE", confounder_role="context", value_kind="binary",
                    zero_semantics="explicit_binary", stale_after_days=120, adapter="pain",
                    source_semantics="derived", roles=("display", "readiness"),
                    formula_id=formula, completeness_profile="EXPLICIT_BINARY:pain",
                    candidate_enabled=False, lineage_roots=(f"pain.{region}.{side}",),
                    goal_families=GOALS_PAIN_TIMELINE, readiness=VE_SAFETY_QUICK,
                    implemented=implemented, source_parameters={"region": region, "side": side},
                )

    for test in sorted(self_tests):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", test):
            raise RegistryError("invalid_catalog", f"invalid SELF_TEST_CATALOG key: {test!r}")
        for side in PAIN_SIDES:
            builder.add(
                f"pain.self_test.{test}.{side}.result", f"{test} {side} result", "pain",
                table="self_test_log", columns=("id", "date", "test", "side", "result", "source", "voided"),
                merge="nonvoid_episode", unit="result", direction="neutral",
                temporal_type="slow_measurement", cadence="episodic", target_pct=None,
                actionability="non_actionable", aggregation_id="LP_SLOW",
                confounder_role="medical_constraint", value_kind="categorical",
                zero_semantics="invalid", stale_after_days=120, adapter="pain",
                source_semantics="slow_episode", roles=("display", "readiness"),
                formula_id="self_test_result_v1", completeness_profile="SLOW_EPISODE",
                candidate_enabled=False, lineage_roots=(f"pain.self_test.{test}.{side}",),
                goal_families=GOALS_PAIN_TIMELINE, readiness=VE_SAFETY_QUICK,
                source_parameters={"test": test, "side": side},
            )

    # Only catalog-declared drill/region pairs are pre-expanded.  A logged
    # target may add its exact identity later; no generic pathway is guessed.
    drill_targets: set[tuple[str, str]] = set()
    for region, region_info in pain_map.items():
        if not isinstance(region_info, Mapping):
            raise RegistryError("invalid_catalog", f"invalid PAIN_CAUSE_MAP region: {region}")
        for cause in region_info.get("causes", ()):
            if isinstance(cause, Mapping):
                for drill in cause.get("drills", ()):
                    if drill in rehab_catalog:
                        drill_targets.add((drill, region))
    for drill, target in sorted(drill_targets):
        target_identity = _identity("other", target)
        for suffix, unit, kind in (
            ("response", "response_-1_1", "ordinal"),
            ("pain_during_nrs", "nrs_0_10", "ordinal"),
        ):
            builder.add(
                f"rehab.trial.{drill}.{target_identity['identity_token']}.{suffix}",
                f"{drill} for {target} {suffix.replace('_', ' ')}", "rehab",
                table="exercise_trial_log",
                columns=("id", "date", "drill", "target", "response", "pain_during", "source", "voided"),
                merge="nonvoid_episode_exact_drill_target", unit=unit, direction="neutral",
                temporal_type="slow_measurement", cadence="episodic", target_pct=None,
                actionability="non_actionable", aggregation_id="LP_SLOW",
                confounder_role="medical_constraint", value_kind=kind,
                zero_semantics="valid", stale_after_days=120, adapter="pain",
                source_semantics="slow_episode", roles=("display", "readiness"),
                formula_id="rehab_response_v1", completeness_profile="SLOW_EPISODE",
                candidate_enabled=False, lineage_roots=(f"rehab.{drill}.{target_identity['identity_key']}",),
                goal_families=GOALS_PAIN_TIMELINE, readiness=VE_SAFETY_QUICK,
                identity_dimension="target", source_identity=target_identity,
                source_parameters={"drill": drill, "target": target, "field": suffix},
            )


def _alias_identity(conn: sqlite3.Connection, namespace: str, label: str) -> dict[str, Any]:
    raw = _identity(namespace, label)
    if namespace not in {"person", "food", "location", "activity", "supplement", "medication", "other"}:
        return raw
    if not _table_exists(conn, "entity_aliases"):
        return raw
    rows = _query_dicts(
        conn,
        "SELECT id,canonical_key,active FROM entity_aliases "
        "WHERE entity_type=? AND alias_key=? ORDER BY id DESC LIMIT 1",
        (namespace, raw["identity_key"]),
    )
    if not rows:
        return raw
    latest = rows[0]
    canonical = latest["canonical_key"] if latest["active"] == 1 else raw["identity_key"]
    return _identity(
        namespace, label, canonical_key=canonical,
        alias_revision_id=int(latest["id"]),
    )


def _simple_labels(conn: sqlite3.Connection, table: str, column: str, *,
                   where: str = "") -> list[str]:
    if not _table_exists(conn, table):
        return []
    order_column = LATEST_LABEL_ORDER_COLUMNS.get(table)
    if order_column is None:
        sql = f"SELECT DISTINCT {column} AS label FROM {table} WHERE {column} IS NOT NULL"
    else:
        sql = (
            f"SELECT {column} AS label,MAX({order_column}) AS registry_order "
            f"FROM {table} WHERE {column} IS NOT NULL"
        )
    if where:
        sql += f" AND ({where})"
    if order_column is None:
        sql += " ORDER BY label"
    else:
        sql += f" GROUP BY {column} ORDER BY registry_order,label"
    result: list[str] = []
    for row in _query_dicts(conn, sql):
        label = row["label"]
        if isinstance(label, str) and _normal(label):
            result.append(label)
    return result


def _identity_map(conn: sqlite3.Connection, table: str, column: str, namespace: str,
                  *, where: str = "", aliases: bool = False) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label in _simple_labels(conn, table, column, where=where):
        identity = _alias_identity(conn, namespace, label) if aliases else _identity(namespace, label)
        # Alias-equivalent labels intentionally collapse to one feature key.
        # Ascending source order means the latest original spelling wins; the
        # label itself is the deterministic tie-breaker for equal source order.
        result[identity["identity_token"]] = identity
    return result


def _training_plan_identities(
    conn: sqlite3.Connection,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return routine/exercise identities retained by append-only plan history."""

    routines: dict[str, dict[str, Any]] = {}
    exercises: dict[str, dict[str, Any]] = {}
    if not _table_exists(conn, "training_plan_revisions"):
        return routines, exercises
    for row in _query_dicts(
        conn, "SELECT id,routines_json FROM training_plan_revisions ORDER BY id",
    ):
        try:
            snapshot = json.loads(row["routines_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RegistryError(
                "invalid_registry", "training plan revision contains invalid routines_json",
            ) from exc
        records: list[tuple[str | None, str | None]] = []
        if isinstance(snapshot, list):
            for item in snapshot:
                if not isinstance(item, Mapping):
                    raise RegistryError("invalid_registry", "training plan routine entry is invalid")
                records.append((
                    item.get("routine_name") or item.get("name"),
                    item.get("exercise_title") or item.get("exercise") or item.get("title"),
                ))
        elif isinstance(snapshot, Mapping):
            for routine_label, raw_exercises in snapshot.items():
                values = raw_exercises.get("exercises", ()) if isinstance(raw_exercises, Mapping) else raw_exercises
                if not isinstance(values, (list, tuple)):
                    raise RegistryError("invalid_registry", "training plan exercise list is invalid")
                if not values:
                    records.append((routine_label, None))
                for item in values:
                    if not isinstance(item, Mapping):
                        raise RegistryError("invalid_registry", "training plan exercise entry is invalid")
                    records.append((
                        routine_label,
                        item.get("exercise_title") or item.get("exercise") or item.get("title"),
                    ))
        else:
            raise RegistryError("invalid_registry", "training plan routines_json must be an array or object")
        for routine_label, exercise_label in records:
            if isinstance(routine_label, str) and _normal(routine_label):
                identity = _identity("routine", routine_label)
                routines[identity["identity_token"]] = identity
            if isinstance(exercise_label, str) and _normal(exercise_label):
                identity = _identity("exercise", exercise_label)
                exercises[identity["identity_token"]] = identity
    return routines, exercises


def _add_dynamic_training(builder: _Builder, conn: sqlite3.Connection) -> None:
    workouts = _identity_map(conn, "hevy_sets", "workout_title", "workout")
    routines = _identity_map(conn, "routines", "routine_name", "routine")
    exercises = _identity_map(conn, "hevy_sets", "exercise_title", "exercise")
    for token, identity in _identity_map(conn, "routines", "exercise_title", "exercise").items():
        exercises.setdefault(token, identity)
    for token, identity in _identity_map(
        conn, "athletic_targets", "lift", "exercise",
        where="axis='strength' AND lift!=''",
    ).items():
        exercises.setdefault(token, identity)
    plan_routines, plan_exercises = _training_plan_identities(conn)
    routines.update(plan_routines)
    exercises.update(plan_exercises)

    for token, identity in workouts.items():
        builder.add(
            f"training.workout.{token}.session", f"{identity['original_label']} session", "training",
            table="hevy_sets", columns=("id", "date", "workout_title", "set_type", "source"),
            merge="exact_normalized_workout_daily", unit="binary", direction="neutral",
            temporal_type="event_occurrence", cadence="event", target_pct=None,
            actionability="direct", aggregation_id="LP_LOAD", confounder_role="context",
            value_kind="binary", zero_semantics="structural_zero_if_complete", stale_after_days=2,
            adapter="training", source_semantics="automated_event",
            roles=("exposure", "readiness"), formula_id="identity_session_v1",
            completeness_profile="AUTO_EVENT", candidate_enabled=True,
            lineage_roots=(f"hevy.workout.{identity['identity_key']}",),
            goal_families=ALL_GOALS,
            readiness=VE_AUTO_TRAIN, identity_dimension="workout", source_identity=identity,
        )
    for token, identity in routines.items():
        builder.add(
            f"training.routine.{token}.session", f"{identity['original_label']} routine session", "training",
            table="hevy_sets", columns=("id", "date", "workout_title", "set_type", "source"),
            merge="exact_effective_plan_name_match", unit="binary", direction="neutral",
            temporal_type="event_occurrence", cadence="event", target_pct=None,
            actionability="direct", aggregation_id="LP_LOAD", confounder_role="context",
            value_kind="binary", zero_semantics="structural_zero_if_complete", stale_after_days=2,
            adapter="training", source_semantics="automated_event",
            roles=("exposure", "readiness"), formula_id="identity_session_v1",
            completeness_profile="AUTO_EVENT", candidate_enabled=True,
            lineage_roots=(f"hevy.routine.{identity['identity_key']}",),
            goal_families=ALL_GOALS,
            readiness=VE_AUTO_TRAIN, identity_dimension="routine", source_identity=identity,
            source_tables=("hevy_sets", "training_plan_revisions"),
        )

    exercise_suffixes = (
        ("session", "binary", "binary", "identity_session_v1", "AUTO_EVENT", "structural_zero_if_complete", "neutral", "exposure"),
        ("working_sets", "count", "count", "working_sets_v1", "AUTO_EVENT", "structural_zero_if_complete", "neutral", "exposure"),
        ("loaded_volume_kg", "kg_reps", "continuous", "loaded_volume_v1", "AUTO_VALUE", "valid", "neutral", "exposure"),
        ("best_e1rm_kg", "kg", "continuous", "epley_daily_max_v1", "AUTO_VALUE", "invalid", "higher_better", "both"),
        ("mean_rpe", "rpe_0_10", "continuous", "known_mean_v1", "AUTO_VALUE", "invalid", "neutral", "exposure"),
        ("duration_sec", "seconds", "continuous", "known_sum_v1", "AUTO_VALUE", "invalid", "neutral", "exposure"),
        ("distance_km", "km", "continuous", "known_sum_v1", "AUTO_VALUE", "invalid", "neutral", "exposure"),
    )
    for token, identity in exercises.items():
        for suffix, unit, kind, formula, completeness, zero, direction, role_kind in exercise_suffixes:
            is_outcome = role_kind == "both"
            builder.add(
                f"training.exercise.{token}.{suffix}", f"{identity['original_label']} {suffix.replace('_', ' ')}", "training",
                table="hevy_sets",
                columns=("id", "date", "exercise_title", "set_type", "weight_kg", "reps", "rpe", "duration_seconds", "distance_km", "source"),
                merge="exact_normalized_exercise_nonwarmup_daily", unit=unit, direction=direction,
                temporal_type="outcome" if is_outcome else "rolling_exposure",
                cadence="event", target_pct=None,
                actionability="outcome" if is_outcome else "direct",
                aggregation_id="LP_LOAD", confounder_role="outcome" if is_outcome else "context",
                value_kind=kind, zero_semantics=zero, stale_after_days=2,
                adapter="training", source_semantics="automated_event",
                roles=("exposure", "outcome", "readiness") if is_outcome else ("exposure", "readiness"),
                formula_id=formula, completeness_profile=completeness,
                candidate_enabled=True,
                lineage_roots=(f"hevy.exercise.{identity['identity_key']}.load_reps",),
                goal_families=ALL_GOALS,
                readiness=VE_RPE if suffix == "mean_rpe" else VE_AUTO_TRAIN,
                identity_dimension="exercise", source_identity=identity,
                source_parameters={"field": suffix},
            )
        builder.add(
            f"training.plan.exercise.{token}.sets", f"Planned {identity['original_label']} sets", "training",
            table="training_plan_revisions",
            columns=("id", "effective_from", "schedule_json", "routines_json"),
            merge="effective_revision_forward_only", unit="sets", direction="neutral",
            temporal_type="static_config", cadence="static", target_pct=None,
            actionability="non_actionable", aggregation_id="LP_NONE",
            confounder_role="context", value_kind="config", zero_semantics="not_observation",
            stale_after_days=None, adapter="training", source_semantics="static_config",
            roles=("display", "readiness"), formula_id="effective_plan_v1",
            completeness_profile="STATIC_CONFIG", candidate_enabled=False,
            lineage_roots=(f"training_plan.exercise.{identity['identity_key']}",),
            goal_families=("follow_through",), readiness=VE_NONE,
            identity_dimension="exercise", source_identity=identity,
            min_observations=0,
        )

    for token, identity in _identity_map(conn, "exercise_submuscles", "sub_region", "subregion").items():
        builder.add(
            f"training.subregion.{token}.effective_sets", f"{identity['original_label']} effective sets", "training",
            table="hevy_sets", columns=("id", "date", "exercise_title", "set_type", "source"),
            merge="authored_first_submuscle_once_per_working_set", unit="effective_sets",
            direction="neutral", temporal_type="rolling_exposure", cadence="event",
            target_pct=None, actionability="direct", aggregation_id="LP_LOAD",
            confounder_role="context", value_kind="continuous",
            zero_semantics="structural_zero_if_complete", stale_after_days=2,
            adapter="training", source_semantics="automated_event",
            roles=("exposure", "readiness"), formula_id="effective_sets_v1",
            completeness_profile="AUTO_EVENT", candidate_enabled=True,
            lineage_roots=(f"hevy.subregion.{identity['identity_key']}",),
            goal_families=ALL_GOALS,
            readiness=VE_AUTO_TRAIN, identity_dimension="subregion", source_identity=identity,
            source_tables=("exercise_submuscles", "hevy_sets"),
        )


def _add_dynamic_cardio_and_nutrition(
    builder: _Builder, conn: sqlite3.Connection, run_type_keys: set[str],
    medication_aliases: Iterable[str],
) -> None:
    for token, identity in _identity_map(conn, "workouts", "type", "cardio_type").items():
        for suffix, unit, kind, completeness, zero in (
            ("session", "binary", "binary", "AUTO_EVENT", "structural_zero_if_complete"),
            ("duration_min", "min", "continuous", "AUTO_VALUE", "invalid"),
            ("distance_km", "km", "continuous", "AUTO_VALUE", "invalid"),
            ("kcal", "kcal", "continuous", "AUTO_VALUE", "invalid"),
        ):
            builder.add(
                f"cardio.type.{token}.{suffix}", f"{identity['original_label']} {suffix.replace('_', ' ')}", "cardio",
                table="workouts", columns=("date", "type", "minutes", "kcal", "km", "source"),
                merge="exact_normalized_type_daily_multiset", unit=unit, direction="neutral",
                temporal_type="rolling_exposure", cadence="event", target_pct=None,
                actionability="indirect", aggregation_id="LP_LOAD", confounder_role="context",
                value_kind=kind, zero_semantics=zero, stale_after_days=2,
                adapter="running", source_semantics="automated_event",
                roles=("exposure", "readiness"), formula_id="workout_type_daily_v1",
                completeness_profile=completeness, candidate_enabled=True,
                lineage_roots=(f"workouts.type.{identity['identity_key']}.{suffix}",),
                goal_families=ALL_GOALS,
                readiness=VE_AUTO_TRAIN, identity_dimension="cardio_type",
                source_identity=identity, source_parameters={"field": suffix},
            )

    recipes: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "recipes"):
        for row in _query_dicts(conn, "SELECT recipe_id,name FROM recipes WHERE recipe_id IS NOT NULL AND name IS NOT NULL ORDER BY recipe_id"):
            if isinstance(row["recipe_id"], str) and isinstance(row["name"], str) and _normal(row["name"]):
                identity = _recipe_identity(row["recipe_id"], row["name"])
                recipes[identity["identity_token"]] = identity
    for token, identity in recipes.items():
        for suffix, unit, kind in (
            ("occurred", "binary", "binary"), ("grams", "g", "continuous"),
            ("kcal", "kcal", "continuous"), ("protein_g", "g", "continuous"),
        ):
            builder.add(
                f"food.recipe.{token}.{suffix}", f"{identity['original_label']} {suffix.replace('_', ' ')}", "food",
                table="nutrition_log", columns=("id", "date", "recipe_id", "food_name", "grams", "kcal", "protein_g", "time", "meal_type", "source"),
                merge="recipe_id_wins_daily", unit=unit, direction="neutral",
                temporal_type="event_occurrence", cadence="event", target_pct=None,
                actionability="direct", aggregation_id="LP_SHORT", confounder_role="none",
                value_kind=kind, zero_semantics="structural_zero_if_complete",
                stale_after_days=2, adapter="nutrition", source_semantics="manual_event",
                roles=("exposure", "readiness"), formula_id="food_identity_daily_v1",
                completeness_profile="MANUAL_EVENT:food_identity", candidate_enabled=True,
                lineage_roots=(f"food.{identity['identity_key']}",),
                goal_families=GOALS_NUTRITION, readiness=VE_FOOD,
                identity_dimension="recipe", source_identity=identity,
                source_tables=("nutrition_log", "recipes"), source_parameters={"field": suffix},
            )
    for token, identity in _identity_map(
        conn, "nutrition_log", "food_name", "food",
        where="recipe_id IS NULL", aliases=True,
    ).items():
        for suffix, unit, kind in (
            ("occurred", "binary", "binary"), ("grams", "g", "continuous"),
            ("kcal", "kcal", "continuous"), ("protein_g", "g", "continuous"),
        ):
            builder.add(
                f"food.named.{token}.{suffix}", f"{identity['original_label']} {suffix.replace('_', ' ')}", "food",
                table="nutrition_log", columns=("id", "date", "recipe_id", "food_name", "grams", "kcal", "protein_g", "time", "meal_type", "source"),
                merge="named_only_when_recipe_absent_daily", unit=unit, direction="neutral",
                temporal_type="event_occurrence", cadence="event", target_pct=None,
                actionability="direct", aggregation_id="LP_SHORT", confounder_role="none",
                value_kind=kind, zero_semantics="structural_zero_if_complete",
                stale_after_days=2, adapter="nutrition", source_semantics="manual_event",
                roles=("exposure", "readiness"), formula_id="food_identity_daily_v1",
                completeness_profile="MANUAL_EVENT:food_identity", candidate_enabled=True,
                lineage_roots=(f"food.{identity['identity_key']}",),
                goal_families=GOALS_NUTRITION, readiness=VE_FOOD,
                identity_dimension="food", source_identity=identity, source_parameters={"field": suffix},
            )

    medication_norm = {_normal(value) for value in medication_aliases if isinstance(value, str) and _normal(value)}
    medications: dict[str, dict[str, Any]] = {}
    for label in _simple_labels(conn, "meds_log", "drug"):
        if _normal(label) in medication_norm:
            canonical = _identity("medication", "medication")["identity_key"]
            identity = _identity("medication", label, canonical_key=canonical)
        else:
            identity = _alias_identity(conn, "medication", label)
        medications[identity["identity_token"]] = identity
    for token, identity in medications.items():
        for suffix, unit, kind, aggregation, zero in (
            ("dose_mg", "mg", "continuous", "LP_EVENT", "structural_zero_if_complete"),
            ("dose_count", "count", "count", "LP_EVENT", "structural_zero_if_complete"),
            ("first_dose_min", "minute_of_day", "time_minutes", "LP_TIMED", "invalid"),
            ("last_dose_min", "minute_of_day", "time_minutes", "LP_TIMED", "invalid"),
            ("rebound", "binary", "binary", "LP_EVENT", "valid"),
            ("regime", "regime", "categorical", "LP_EVENT", "invalid"),
        ):
            builder.add(
                f"medication.{token}.{suffix}", f"{identity['original_label']} {suffix.replace('_', ' ')}", "medication",
                table="meds_log", columns=("id", "date", "drug", "dose_mg", "time_taken", "rebound", "source"),
                merge="exact_resolved_drug_daily_all_known", unit=unit, direction="neutral",
                temporal_type="event_occurrence", cadence="event", target_pct=None,
                actionability="direct", aggregation_id=aggregation,
                confounder_role="medical_constraint", value_kind=kind,
                zero_semantics=zero, stale_after_days=2,
                adapter="daily", source_semantics="manual_event",
                roles=("confounder", "exposure", "readiness"), formula_id="medication_daily_v1",
                completeness_profile="MANUAL_EVENT:medication", candidate_enabled=True,
                lineage_roots=(f"medication.{identity['identity_key']}",),
                goal_families=(
                    (*GOALS_MEDICATION_SUPPLEMENT, "follow_through")
                    if suffix in {"first_dose_min", "last_dose_min"}
                    else GOALS_MEDICATION_SUPPLEMENT
                ),
                readiness=VE_SAFETY_QUICK, identity_dimension="medication",
                source_identity=identity, source_parameters={"field": suffix},
            )

    supplements: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "supplement_products"):
        for row in _query_dicts(conn, "SELECT supplement_id,name FROM supplement_products WHERE name IS NOT NULL ORDER BY supplement_id"):
            if isinstance(row["name"], str) and _normal(row["name"]):
                identity = _alias_identity(conn, "supplement", row["name"])
                supplements[identity["identity_token"]] = identity
    for token, identity in supplements.items():
        for suffix, unit, kind, aggregation in (
            ("taken", "binary", "binary", "LP_EVENT"),
            ("dose", "product_unit", "continuous", "LP_EVENT"),
            ("time_min", "minute_of_day", "time_minutes", "LP_TIMED"),
        ):
            builder.add(
                f"supplement.{token}.{suffix}", f"{identity['original_label']} {suffix.replace('_', ' ')}", "supplement",
                table="supplements_log", columns=("id", "date", "supplement_id", "taken", "dose_taken", "time_taken", "source"),
                merge="product_join_explicit_values", unit=unit, direction="neutral",
                temporal_type="event_occurrence", cadence="event", target_pct=None,
                actionability="direct", aggregation_id=aggregation,
                confounder_role="medical_constraint", value_kind=kind,
                zero_semantics="explicit_binary" if suffix == "taken" else "invalid",
                stale_after_days=7, adapter="nutrition", source_semantics="manual_event",
                roles=("exposure", "readiness"), formula_id="explicit_binary_v1",
                completeness_profile="EXPLICIT_BINARY:supplement", candidate_enabled=True,
                lineage_roots=(f"supplement.{identity['identity_key']}",),
                goal_families=GOALS_MEDICATION_SUPPLEMENT, readiness=VE_RPE,
                identity_dimension="supplement", source_identity=identity,
                source_tables=("supplement_products", "supplements_log"),
                source_parameters={"field": suffix},
            )


def _add_dynamic_events(builder: _Builder, conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "event_exposures"):
        return
    rows = _query_dicts(
        conn,
        "SELECT id,category,entity_label,entity_key FROM event_exposures "
        "WHERE voided=0 AND entity_label IS NOT NULL AND entity_key IS NOT NULL ORDER BY id",
    )
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        category, label, canonical = row["category"], row["entity_label"], row["entity_key"]
        if category not in EVENT_SCOPE or not isinstance(label, str) or not _normal(label):
            continue
        namespace = {
            "social": "person", "location": "location", "activity": "activity",
            "food": "food", "meal": "food", "medication_change": "medication",
        }.get(category, "other")
        identity = _identity(namespace, label, canonical_key=canonical)
        identities[(category, identity["identity_token"])] = identity
    for (category, token), identity in sorted(identities.items()):
        scope = EVENT_SCOPE[category]
        for suffix, unit, kind in (
            ("occurred", "binary", "binary"), ("count", "count", "count"),
            ("duration_min", "min", "continuous"),
            ("intensity_mean", "ordinal_1_5", "continuous"),
            ("valence_mean", "ordinal_-2_2", "continuous"),
        ):
            builder.add(
                f"event.{category}.entity.{token}.{suffix}",
                f"{identity['original_label']} {suffix.replace('_', ' ')}", "event",
                table="event_exposures",
                columns=("id", "date", "time", "category", "entity_label", "entity_key", "duration_min", "intensity", "valence", "source", "voided"),
                merge="active_exact_resolved_entity_daily_all_known", unit=unit,
                direction="neutral", temporal_type="event_occurrence", cadence="event",
                target_pct=None, actionability="direct", aggregation_id="LP_EVENT",
                confounder_role="context" if category in {"illness", "stress", "training_phase", "travel"} else "none",
                value_kind=kind, zero_semantics="structural_zero_if_complete",
                stale_after_days=7, adapter="events", source_semantics="manual_event",
                roles=(("confounder", "exposure", "readiness") if category in {"illness", "stress", "training_phase", "travel"} else ("exposure", "readiness")),
                formula_id="event_daily_v1", completeness_profile=f"MANUAL_EVENT:{scope}",
                candidate_enabled=True, lineage_roots=(f"event.{category}.{identity['identity_key']}",),
                goal_families=ALL_GOALS,
                readiness=VE_EVENT, identity_dimension=identity["namespace"],
                source_identity=identity,
                source_parameters={"category": category, "scope": scope, "field": suffix},
            )


def _add_adherence_and_manual(builder: _Builder, conn: sqlite3.Connection) -> None:
    builder.add(
        "adherence.word_kept", "Whole-day word kept", "adherence",
        table="commitments_log", columns=("id", "date", "commitment_id", "status", "source"),
        merge="whole_day_commitment_zero_status", unit="fraction_0_1",
        direction="higher_better", temporal_type="outcome", cadence="daily",
        target_pct=90, actionability="outcome", aggregation_id="LP_STATE",
        confounder_role="outcome", value_kind="ordinal", zero_semantics="valid",
        stale_after_days=7, adapter="adherence", source_semantics="manual_daily",
        roles=("outcome", "readiness"), formula_id="adherence_status_v1",
        completeness_profile="MANUAL_VALUE", candidate_enabled=True,
        lineage_roots=("commitment.whole_day",), goal_families=GOALS_ADHERENCE,
        readiness=VE_DAY,
    )
    for token, identity in _identity_map(conn, "commitments", "name", "commitment").items():
        builder.add(
            f"adherence.commitment.{token}.kept", f"{identity['original_label']} kept", "adherence",
            table="commitments_log", columns=("id", "date", "commitment_id", "status", "source"),
            merge="configured_commitment_status", unit="fraction_0_1",
            direction="higher_better", temporal_type="outcome", cadence="daily",
            target_pct=90, actionability="outcome", aggregation_id="LP_STATE",
            confounder_role="outcome", value_kind="ordinal", zero_semantics="valid",
            stale_after_days=7, adapter="adherence", source_semantics="manual_daily",
            roles=("outcome", "readiness"), formula_id="adherence_status_v1",
            completeness_profile="MANUAL_VALUE", candidate_enabled=True,
            lineage_roots=(f"commitment.{identity['identity_key']}",),
            goal_families=GOALS_ADHERENCE, readiness=VE_DAY,
            identity_dimension="commitment", source_identity=identity,
            source_tables=("commitments", "commitments_log"),
        )
    for token, identity in _identity_map(conn, "habits_log", "habit", "habit").items():
        builder.add(
            f"adherence.habit.{token}.done", f"{identity['original_label']} done", "adherence",
            table="habits_log", columns=("id", "date", "habit", "done", "source"),
            merge="max_explicit_duplicate_date", unit="binary", direction="higher_better",
            temporal_type="outcome", cadence="daily", target_pct=90,
            actionability="direct", aggregation_id="LP_EVENT", confounder_role="outcome",
            value_kind="binary", zero_semantics="explicit_binary", stale_after_days=7,
            adapter="adherence", source_semantics="manual_event",
            roles=("exposure", "outcome", "readiness"), formula_id="habit_explicit_v1",
            completeness_profile="EXPLICIT_BINARY:other_event", candidate_enabled=True,
            lineage_roots=(f"habit.{identity['identity_key']}",),
            goal_families=GOALS_ADHERENCE, readiness=VE_LOW,
            identity_dimension="habit", source_identity=identity,
        )
    for metric in ("bed", "dose", "wake", "workout"):
        for suffix, unit, direction, kind in (
            ("on_time", "binary", "higher_better", "binary"),
            ("abs_delta_min", "min", "lower_better", "continuous"),
        ):
            builder.add(
                f"adherence.timing.{metric}.{suffix}", f"{metric} {suffix.replace('_', ' ')}", "adherence",
                table="planned_times", columns=("metric", "planned", "tolerance_min", "updated"),
                merge="effective_plan_circular_actual_difference", unit=unit,
                direction=direction, temporal_type="outcome", cadence="daily",
                target_pct=90, actionability="outcome", aggregation_id="LP_STATE",
                confounder_role="outcome", value_kind=kind, zero_semantics="derived_strict",
                stale_after_days=7, adapter="adherence", source_semantics="derived",
                roles=("outcome", "readiness"), formula_id="timing_adherence_v1",
                completeness_profile="DERIVED", candidate_enabled=True,
                lineage_roots=(f"planned_time.{metric}", f"timing_actual.{metric}"),
                goal_families=GOALS_ADHERENCE, readiness=VE_LOW,
                source_tables=("meds_log", "planned_times", "sleep_log", "workouts"),
                source_parameters={"metric": metric, "field": suffix},
            )

    labs: dict[str, tuple[dict[str, Any], str, str]] = {}
    catalog_lab_names: set[str] = set()
    if _table_exists(conn, "lab_catalog"):
        for row in _query_dicts(
            conn,
            "SELECT canonical,display,unit,aliases FROM lab_catalog ORDER BY canonical",
        ):
            canonical = row["canonical"]
            label = row["display"] or canonical
            catalog_unit = row["unit"]
            if (
                isinstance(canonical, str)
                and isinstance(label, str)
                and isinstance(catalog_unit, str)
                and catalog_unit
                and _normal(label)
            ):
                identity = _identity("lab", canonical)
                identity["original_label"] = label
                labs[identity["identity_token"]] = (
                    identity, canonical, catalog_unit,
                )
                catalog_lab_names.add(_normal(canonical))
                try:
                    aliases = json.loads(row["aliases"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    aliases = []
                if isinstance(aliases, list):
                    catalog_lab_names.update(
                        _normal(alias) for alias in aliases
                        if isinstance(alias, str) and alias
                    )
    for token, (identity, canonical, catalog_unit) in labs.items():
        for suffix, unit, kind, formula in (
            ("value", catalog_unit, "episode", "lab_episode_v1"),
            ("reference_status", "status", "categorical", "lab_reference_status_v1"),
            ("age_days", "days", "continuous", "episode_age_v1"),
        ):
            builder.add(
                f"lab.{token}.{suffix}", f"{identity['original_label']} {suffix.replace('_', ' ')}", "lab",
                table="labs", columns=("id", "date", "test_name", "value", "unit", "reference_low", "reference_high", "flag", "source"),
                merge="catalog_join_compatible_unit_episode", unit=unit,
                direction="neutral", temporal_type="slow_measurement", cadence="episodic",
                target_pct=None, actionability="context", aggregation_id="LP_SLOW",
                confounder_role="medical_constraint", value_kind=kind,
                zero_semantics="valid" if suffix == "value" else "derived_strict",
                stale_after_days=365, adapter="labs", source_semantics="slow_episode",
                roles=("display", "readiness"), formula_id=formula,
                completeness_profile="SLOW_EPISODE" if suffix == "value" else "DERIVED",
                candidate_enabled=False, lineage_roots=(f"lab.{identity['identity_key']}",),
                readiness=VE_LAB, identity_dimension="lab", source_identity=identity,
                source_tables=("lab_catalog", "labs"),
                source_parameters={"canonical": canonical, "field": suffix},
            )

    # Unknown lab names remain visible as disconnected status/age episodes.
    # They never acquire a value definition whose unit would relabel the raw
    # measurement before a user-confirmed catalog connection exists.
    unknown_labs: dict[str, tuple[dict[str, Any], str]] = {}
    for raw_name in _simple_labels(conn, "labs", "test_name"):
        if _normal(raw_name) in catalog_lab_names:
            continue
        identity = _identity("lab", raw_name)
        if identity["identity_token"] in labs:
            continue
        unknown_labs[identity["identity_token"]] = (identity, raw_name)
    for identity, raw_name in unknown_labs.values():
        for suffix, unit, kind, formula in (
            ("reference_status", "status", "categorical", "lab_reference_status_v1"),
            ("age_days", "days", "continuous", "episode_age_v1"),
        ):
            builder.add(
                f"lab.{identity['identity_token']}.{suffix}",
                f"{identity['original_label']} {suffix.replace('_', ' ')}",
                "lab", table="labs",
                columns=("id", "date", "test_name", "value", "unit", "reference_low", "reference_high", "flag", "source"),
                merge="catalog_missing_episode_status", unit=unit,
                direction="neutral", temporal_type="slow_measurement",
                cadence="episodic", target_pct=None, actionability="context",
                aggregation_id="LP_SLOW", confounder_role="medical_constraint",
                value_kind=kind, zero_semantics="derived_strict",
                stale_after_days=365, adapter="labs", source_semantics="derived",
                roles=("display", "readiness"), formula_id=formula,
                completeness_profile="DERIVED", candidate_enabled=False,
                lineage_roots=(f"lab.{identity['identity_key']}",),
                readiness=VE_LAB, identity_dimension="lab",
                source_identity=identity, source_tables=("lab_catalog", "labs"),
                source_parameters={
                    "canonical": None, "catalog_connected": False,
                    "field": suffix, "raw_test_name": raw_name,
                },
            )

    assessment_identities: dict[
        tuple[str, str], tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str]
    ] = {}
    if _table_exists(conn, "assessments"):
        rows = _query_dicts(
            conn,
            "SELECT id,scale,part FROM assessments "
            "WHERE scale IS NOT NULL ORDER BY id",
        )
        for row in rows:
            scale = row["scale"]
            if not isinstance(scale, str) or not _normal(scale):
                continue
            part = row["part"] if isinstance(row["part"], str) and _normal(row["part"]) else "total"
            scale_id, part_id = _identity("assessment", scale), _identity("assessment_part", part)
            combined = _identity("assessment", f"{scale} / {part}")
            assessment_identities[(
                scale_id["identity_token"], part_id["identity_token"],
            )] = (scale_id, part_id, combined, scale, part)
        for scale_id, part_id, combined, scale, part in assessment_identities.values():
            for suffix, unit, kind, formula in (
                ("score", "score", "episode", "episode_value_v1"),
                ("fraction", "fraction", "ratio", "assessment_fraction_v1"),
            ):
                builder.add(
                    f"assessment.{scale_id['identity_token']}.{part_id['identity_token']}.{suffix}",
                    f"{scale} {part} {suffix}", "assessment",
                    table="assessments", columns=("id", "date", "scale", "part", "score", "max_score", "source"),
                    merge="exact_scale_part_episode", unit=unit, direction="unknown",
                    temporal_type="slow_measurement", cadence="episodic", target_pct=None,
                    actionability="context", aggregation_id="LP_SLOW", confounder_role="context",
                    value_kind=kind, zero_semantics="valid" if suffix == "score" else "derived_strict",
                    stale_after_days=120, adapter="manual", source_semantics="slow_episode",
                    roles=("display", "readiness"), formula_id=formula,
                    completeness_profile="SLOW_EPISODE" if suffix == "score" else "DERIVED",
                    candidate_enabled=False, lineage_roots=(f"assessment.{combined['identity_key']}",),
                    readiness=VE_WEIGHT, identity_dimension="assessment", source_identity=combined,
                    source_parameters={"scale": scale, "part": part, "field": suffix},
                )

    skincare: dict[str, tuple[dict[str, Any], int]] = {}
    if _table_exists(conn, "skincare_products"):
        for row in _query_dicts(
            conn,
            "SELECT product_id,brand,product_name FROM skincare_products "
            "WHERE active=1 AND product_name IS NOT NULL ORDER BY product_id",
        ):
            brand = row["brand"] if isinstance(row["brand"], str) else ""
            name = row["product_name"]
            label = " ".join(part for part in (brand.strip(), name.strip()) if part)
            if label:
                identity = _identity("skincare", label)
                skincare[identity["identity_token"]] = (identity, int(row["product_id"]))
    for token, (identity, product_id) in skincare.items():
        builder.add(
            f"skincare.product.{token}.used", f"{identity['original_label']} used", "skincare",
            table="skincare_log", columns=("id", "date", "product_id", "used", "source"),
            merge="active_product_join_explicit_binary", unit="binary", direction="neutral",
            temporal_type="event_occurrence", cadence="event", target_pct=None,
            actionability="direct", aggregation_id="LP_EVENT", confounder_role="none",
            value_kind="binary", zero_semantics="explicit_binary", stale_after_days=7,
            adapter="manual", source_semantics="manual_event",
            roles=("exposure", "readiness"), formula_id="skincare_explicit_v1",
            completeness_profile="EXPLICIT_BINARY:other_event", candidate_enabled=True,
            lineage_roots=(f"skincare.{identity['identity_key']}",),
            goal_families=GOALS_SKINCARE, readiness=VE_LOW,
            identity_dimension="skincare", source_identity=identity,
            source_tables=("skincare_log", "skincare_products"),
            source_parameters={"product_id": product_id},
        )


def _add_environment(builder: _Builder, conn: sqlite3.Connection) -> None:
    weather_metrics = (
        "cloud_cover_mean_pct", "condition", "daylight_hours", "et0_mm",
        "feels_like_max_c", "feels_like_min_c", "humidity_mean_pct",
        "precipitation_hours", "precipitation_mm", "pressure_mean_hpa", "rain_mm",
        "snowfall_cm", "solar_radiation_mj", "sunrise_min", "sunset_min",
        "sunshine_hours", "temp_max_c", "temp_min_c", "uv_index_clear_sky_max",
        "uv_index_max", "weather_code", "wind_dir_cos", "wind_dir_sin",
        "wind_gusts_max_ms", "wind_speed_max_ms",
    )
    weather_locations = _identity_map(conn, "weather", "location", "location", aliases=True)
    for token, identity in weather_locations.items():
        for metric in weather_metrics:
            source_column = {
                "sunrise_min": "sunrise", "sunset_min": "sunset",
                "wind_dir_cos": "wind_dir_deg", "wind_dir_sin": "wind_dir_deg",
            }.get(metric, metric)
            categorical = metric in {"condition", "weather_code"}
            formula = "wind_direction_component_v1" if metric.startswith("wind_dir_") else "weather_metric_v1"
            unit = WEATHER_UNITS[metric]
            builder.add(
                f"weather.{token}.{metric}", f"{identity['original_label']} {metric.replace('_', ' ')}", "weather",
                table="weather", columns=("date", "location", source_column, "source"),
                merge="one_row_per_date_location", unit=unit, direction="neutral",
                temporal_type="daily_measurement", cadence="daily", target_pct=100,
                actionability="context", aggregation_id="LP_ENV", confounder_role="context",
                value_kind="categorical" if categorical else "continuous",
                zero_semantics="valid", stale_after_days=2, adapter="environment",
                source_semantics="automated_daily", roles=("confounder", "exposure", "readiness"),
                formula_id=formula, completeness_profile="AUTO_VALUE",
                candidate_enabled=not categorical, lineage_roots=(f"weather.{identity['identity_key']}.{metric}",),
                goal_families=ALL_GOALS, readiness=VE_CONTEXT,
                identity_dimension="location", source_identity=identity,
                source_parameters={"metric": metric, "source_column": source_column},
            )
    air_metrics = (
        "alder_pollen", "birch_pollen", "co_ugm3", "european_aqi_max",
        "european_aqi_mean", "grass_pollen", "mugwort_pollen", "no2_ugm3",
        "olive_pollen", "ozone_ugm3", "pm10_ugm3", "pm2_5_ugm3",
        "ragweed_pollen", "so2_ugm3",
    )
    for token, identity in _identity_map(conn, "air_quality", "location", "location", aliases=True).items():
        for metric in air_metrics:
            builder.add(
                f"air.{token}.{metric}", f"{identity['original_label']} {metric.replace('_', ' ')}", "air",
                table="air_quality", columns=("date", "location", metric, "source"),
                merge="one_row_per_date_location", unit=AIR_UNITS[metric], direction="neutral",
                temporal_type="daily_measurement", cadence="daily", target_pct=100,
                actionability="context", aggregation_id="LP_ENV", confounder_role="context",
                value_kind="continuous", zero_semantics="valid", stale_after_days=2,
                adapter="environment", source_semantics="automated_daily",
                roles=("confounder", "exposure", "readiness"), formula_id="air_metric_v1",
                completeness_profile="AUTO_VALUE", candidate_enabled=True,
                lineage_roots=(f"air.{identity['identity_key']}.{metric}",),
                goal_families=ALL_GOALS, readiness=VE_CONTEXT,
                identity_dimension="location", source_identity=identity,
                source_parameters={"metric": metric},
            )


def _add_unclassified_hrv(builder: _Builder, conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "daily_metrics"):
        return
    for source_label in _simple_labels(
        conn, "daily_metrics", "source",
        where="LOWER(source) NOT IN ('apple','fitbit') AND hrv_ms IS NOT NULL",
    ):
        # Keep the identity recipe identical to the daily adapter.  The
        # provider is deliberately classified as a generic ``source`` rather
        # than guessed into an Apple/Fitbit lineage.
        identity = _identity("source", source_label)
        builder.add(
            f"wearable.hrv.unclassified.{identity['identity_token']}",
            f"Unclassified {source_label} HRV", "wearable",
            table="daily_metrics", columns=("date", "source", "hrv_ms"),
            merge="source_specific_unclassified_disabled", unit="ms",
            direction="unknown", temporal_type="daily_measurement", cadence="daily",
            target_pct=100, actionability="non_actionable", aggregation_id="LP_STATE",
            confounder_role="source_era", value_kind="continuous", zero_semantics="invalid",
            stale_after_days=2, adapter="daily", source_semantics="source_specific_daily",
            roles=("confounder", "display", "readiness"),
            formula_id="source_filtered_hrv_v1", completeness_profile="AUTO_VALUE",
            candidate_enabled=False, lineage_roots=(f"hrv.unclassified.{identity['identity_key']}",),
            goal_families=GOALS_SLEEP_WEARABLE, readiness=VE_CONTEXT,
            identity_dimension="source", source_identity=identity,
            source_parameters={"provider": source_label, "algorithm": "unknown"},
        )


def _add_logged_rehab_targets(
    builder: _Builder, conn: sqlite3.Connection, rehab_catalog: Mapping[str, Any],
) -> None:
    if not _table_exists(conn, "exercise_trial_log"):
        return
    rows = _query_dicts(
        conn,
        "SELECT id,drill,target FROM exercise_trial_log "
        "WHERE voided=0 AND target IS NOT NULL ORDER BY id",
    )
    identities: dict[tuple[str, str], tuple[str, str, dict[str, Any]]] = {}
    for row in rows:
        drill, target = row["drill"], row["target"]
        if drill not in rehab_catalog or not isinstance(target, str) or not _normal(target):
            continue
        identity = _identity("other", target)
        identities[(drill, identity["identity_token"])] = (drill, target, identity)
    for drill, target, identity in identities.values():
        for suffix, unit, kind in (
            ("response", "response_-1_1", "ordinal"),
            ("pain_during_nrs", "nrs_0_10", "ordinal"),
        ):
            key = f"rehab.trial.{drill}.{identity['identity_token']}.{suffix}"
            if key in builder.entries:
                continue
            builder.add(
                key, f"{drill} for {target} {suffix.replace('_', ' ')}", "rehab",
                table="exercise_trial_log",
                columns=("id", "date", "drill", "target", "response", "pain_during", "source", "voided"),
                merge="nonvoid_episode_exact_drill_target", unit=unit, direction="neutral",
                temporal_type="slow_measurement", cadence="episodic", target_pct=None,
                actionability="non_actionable", aggregation_id="LP_SLOW",
                confounder_role="medical_constraint", value_kind=kind, zero_semantics="valid",
                stale_after_days=120, adapter="pain", source_semantics="slow_episode",
                roles=("display", "readiness"), formula_id="rehab_response_v1",
                completeness_profile="SLOW_EPISODE", candidate_enabled=False,
                lineage_roots=(f"rehab.{drill}.{identity['identity_key']}",),
                goal_families=GOALS_PAIN_TIMELINE, readiness=VE_SAFETY_QUICK,
                identity_dimension="target", source_identity=identity,
                source_parameters={"drill": drill, "target": target, "field": suffix},
            )


def _runtime_catalogs(context: AdapterContext) -> dict[str, Any]:
    catalogs = {name: _ctx_constant(context, name) for name in REQUIRED_RUNTIME_CONSTANTS}
    for name in ("CATALOG", "ATHLETIC_AXES", "MOBILITY_NORM", "PAIN_CAUSE_MAP",
                 "SELF_TEST_CATALOG", "REHAB_CATALOG"):
        if not isinstance(catalogs[name], Mapping):
            raise RegistryError("invalid_catalog", f"{name} must be a mapping")
    for name in ("RATIO_SEED", "MICRO_SEED"):
        if not isinstance(catalogs[name], (list, tuple)):
            raise RegistryError("invalid_catalog", f"{name} must be an ordered array")
    if not isinstance(catalogs["DM_METRICS"], (list, tuple)) or not catalogs["DM_METRICS"]:
        raise RegistryError("invalid_catalog", "DM_METRICS must be a non-empty ordered array")
    if (
        len(catalogs["DM_METRICS"]) != len(set(catalogs["DM_METRICS"]))
        or any(
            not isinstance(value, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value)
            for value in catalogs["DM_METRICS"]
        )
    ):
        raise RegistryError("invalid_catalog", "DM_METRICS must contain unique safe keys")

    for name in ("CATALOG", "ATHLETIC_AXES", "MOBILITY_NORM", "PAIN_CAUSE_MAP",
                 "SELF_TEST_CATALOG", "REHAB_CATALOG"):
        if any(
            not isinstance(key, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", key)
            for key in catalogs[name]
        ):
            raise RegistryError("invalid_catalog", f"{name} contains an invalid expansion key")

    micro_keys: list[str] = []
    for item in catalogs["MICRO_SEED"]:
        key = item.get("key") if isinstance(item, Mapping) else None
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key):
            raise RegistryError("invalid_catalog", "MICRO_SEED contains an invalid expansion")
        micro_keys.append(key)
    if len(micro_keys) != len(set(micro_keys)):
        raise RegistryError("invalid_catalog", "MICRO_SEED expansion keys must be unique")

    raw_runs = catalogs["RUN_TYPE_KEYS"]
    if isinstance(raw_runs, Mapping):
        runs = set(raw_runs)
    elif isinstance(raw_runs, (set, frozenset, list, tuple)):
        runs = set(raw_runs)
    else:
        raise RegistryError("invalid_catalog", "RUN_TYPE_KEYS must be a mapping or collection")
    if any(not isinstance(value, str) or _normal(value) != value for value in runs):
        raise RegistryError("invalid_catalog", "RUN_TYPE_KEYS must contain exact normalized text")
    if not RUN_TYPE_BASE <= runs:
        raise RegistryError("invalid_catalog", "RUN_TYPE_KEYS omits a required exact running value")
    extras = runs - RUN_TYPE_BASE
    fixtures = context.constants.get("RUN_TYPE_FIXTURES", {}) if isinstance(context.constants, Mapping) else {}
    if extras and (not isinstance(fixtures, Mapping) or not extras <= set(fixtures)):
        raise RegistryError(
            "invalid_catalog",
            "additional RUN_TYPE_KEYS require a provider fixture for each exact value",
        )
    catalogs["RUN_TYPE_KEYS"] = tuple(sorted(runs))

    if not set(catalogs["MOBILITY_NORM"]) <= set(catalogs["CATALOG"]):
        raise RegistryError("invalid_catalog", "MOBILITY_NORM must be a CATALOG subset")
    for axis, config in catalogs["ATHLETIC_AXES"].items():
        if not isinstance(config, Mapping):
            raise RegistryError("invalid_catalog", f"ATHLETIC_AXES {axis} must be an object")
        movement = config.get("test")
        if movement is not None and movement not in catalogs["CATALOG"]:
            raise RegistryError("invalid_catalog", f"ATHLETIC_AXES {axis} references unknown test")

    referenced_tests: set[str] = set()
    referenced_drills: set[str] = set()
    for region, region_info in catalogs["PAIN_CAUSE_MAP"].items():
        if not isinstance(region_info, Mapping) or not isinstance(region_info.get("causes", ()), (list, tuple)):
            raise RegistryError("invalid_catalog", f"PAIN_CAUSE_MAP {region} is invalid")
        for cause in region_info.get("causes", ()):
            if not isinstance(cause, Mapping):
                raise RegistryError("invalid_catalog", f"PAIN_CAUSE_MAP {region} cause is invalid")
            tests = cause.get("tests", ())
            drills = cause.get("drills", ())
            if not isinstance(tests, (list, tuple)) or not isinstance(drills, (list, tuple)):
                raise RegistryError("invalid_catalog", f"PAIN_CAUSE_MAP {region} references are invalid")
            for test in tests:
                key = test.get("key") if isinstance(test, Mapping) else None
                if not isinstance(key, str):
                    raise RegistryError("invalid_catalog", f"PAIN_CAUSE_MAP {region} test is invalid")
                referenced_tests.add(key)
            if any(not isinstance(drill, str) for drill in drills):
                raise RegistryError("invalid_catalog", f"PAIN_CAUSE_MAP {region} drill is invalid")
            referenced_drills.update(drills)
    unknown_tests = sorted(referenced_tests - set(catalogs["SELF_TEST_CATALOG"]))
    unknown_drills = sorted(referenced_drills - set(catalogs["REHAB_CATALOG"]))
    if unknown_tests or unknown_drills:
        raise RegistryError(
            "invalid_catalog",
            f"PAIN_CAUSE_MAP reference drift tests={unknown_tests} drills={unknown_drills}",
        )
    return catalogs


def _context_adapters(context: AdapterContext) -> Mapping[str, Any] | None:
    for container in (context.constants, context.functions):
        if not isinstance(container, Mapping):
            continue
        for key in ("ADAPTERS", "adapters"):
            value = container.get(key)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise RegistryError("adapter_handshake_drift", f"{key} must be an adapter mapping")
                return value
    return None


def validate_adapter_handshakes(
    definitions: Sequence[FeatureDefinition], adapters: Mapping[str, Any] | None = None,
) -> None:
    """Reject missing, renamed, disabled, or registry-version-drifted adapters."""

    if adapters is None:
        loaded: dict[str, Any] = {}
        for adapter_id in sorted(ADAPTER_IDS):
            try:
                loaded[adapter_id] = importlib.import_module(
                    f"{__package__}.adapters.{adapter_id}"
                )
            except (ImportError, AttributeError) as exc:
                raise RegistryError(
                    "adapter_handshake_drift", f"adapter {adapter_id} is not importable"
                ) from exc
        adapters = loaded
    missing = sorted(ADAPTER_IDS - set(adapters))
    unknown = sorted(set(adapters) - ADAPTER_IDS)
    if missing or unknown:
        raise RegistryError(
            "adapter_handshake_drift",
            f"adapter set mismatch missing={missing} unknown={unknown}",
        )
    for adapter_id in sorted(ADAPTER_IDS):
        adapter = adapters[adapter_id]
        if getattr(adapter, "ADAPTER_ID", None) != adapter_id:
            raise RegistryError("adapter_handshake_drift", f"adapter id drift: {adapter_id}")
        if getattr(adapter, "REGISTRY_VERSION", None) != REGISTRY_VERSION:
            raise RegistryError("adapter_handshake_drift", f"adapter version drift: {adapter_id}")
        load = getattr(adapter, "load", None)
        if not callable(load):
            raise RegistryError("adapter_handshake_drift", f"adapter load missing: {adapter_id}")
        try:
            parameters = tuple(inspect.signature(load).parameters.values())
        except (TypeError, ValueError) as exc:
            raise RegistryError(
                "adapter_handshake_drift", f"adapter load signature unreadable: {adapter_id}",
            ) from exc
        if (
            tuple(item.name for item in parameters)
            != ("conn", "definitions", "date_range", "context", "include_provenance")
            or any(
                item.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
                or item.default is not inspect.Parameter.empty
                for item in parameters[:4]
            )
            or len(parameters) != 5
            or parameters[4].kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameters[4].default is not False
        ):
            raise RegistryError(
                "adapter_handshake_drift", f"adapter load signature drift: {adapter_id}",
            )
    referenced = {definition.adapter for definition in definitions}
    if not referenced <= set(adapters):
        raise RegistryError("adapter_handshake_drift", "registry references an unknown adapter")


def validate_registry(definitions: Iterable[FeatureDefinition | Mapping[str, Any]]) -> tuple[FeatureDefinition, ...]:
    """Return canonical key order after complete entry and duplicate validation."""

    converted: list[FeatureDefinition] = []
    seen: set[str] = set()
    try:
        for raw in definitions:
            definition = raw if isinstance(raw, FeatureDefinition) else FeatureDefinition.from_dict(raw)
            validate_feature_definition(definition)
            if definition.formula_id not in KNOWN_FORMULA_IDS:
                raise RegistryError("unknown_formula_id", f"unknown formula id: {definition.formula_id}")
            expected_goals = _expected_goal_families(definition)
            if definition.goal_families != expected_goals:
                raise RegistryError(
                    "goal_family_mapping_drift",
                    f"{definition.key}: goal_families {definition.goal_families!r} "
                    f"!= {expected_goals!r}",
                )
            if definition.key in seen:
                raise RegistryError("duplicate_feature_key", f"duplicate feature key: {definition.key}")
            seen.add(definition.key)
            converted.append(definition)
    except ContractError as exc:
        raise RegistryError("invalid_registry_metadata", str(exc), True) from exc
    return tuple(sorted(converted, key=lambda item: item.key))


def build_registry(
    conn: sqlite3.Connection, context: AdapterContext, family: str | None = None,
) -> tuple[FeatureDefinition, ...]:
    """Build the exhaustive v1 registry from code templates and exact identities.

    Database access is SELECT-only.  Catalog keys come from the active runtime
    constants so adapter/catalog drift fails instead of being hidden by copied
    lists in this module.
    """

    if not isinstance(context, AdapterContext):
        raise RegistryError("validation_error", "context must be AdapterContext", True)
    if family is not None and (not isinstance(family, str) or family not in PILLARS):
        raise RegistryError("validation_error", "unknown feature family", True)
    catalogs = _runtime_catalogs(context)
    builder = _Builder({})
    try:
        _add_calendar_and_source(builder, catalogs["DM_METRICS"])
        _add_daily_features(builder)
        _add_training_and_running(builder, catalogs["RUN_TYPE_KEYS"])
        _add_nutrition_and_events(builder, catalogs["MICRO_SEED"])
        _add_quarterly_and_pain(
            builder, conn, catalogs["CATALOG"], catalogs["RATIO_SEED"],
            catalogs["ATHLETIC_AXES"], catalogs["MOBILITY_NORM"],
            catalogs["PAIN_CAUSE_MAP"], catalogs["SELF_TEST_CATALOG"],
            catalogs["REHAB_CATALOG"],
        )
        _add_dynamic_training(builder, conn)
        medication_aliases = context.constants.get("MEDICATION_ALIASES", ()) if isinstance(context.constants, Mapping) else ()
        _add_dynamic_cardio_and_nutrition(
            builder, conn, set(catalogs["RUN_TYPE_KEYS"]), medication_aliases,
        )
        _add_dynamic_events(builder, conn)
        _add_adherence_and_manual(builder, conn)
        _add_environment(builder, conn)
        _add_unclassified_hrv(builder, conn)
        _add_logged_rehab_targets(builder, conn, catalogs["REHAB_CATALOG"])
        definitions = validate_registry(builder.entries.values())
    except RegistryError:
        raise
    except (ContractError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise RegistryError("invalid_registry", str(exc)) from exc

    validate_adapter_handshakes(definitions, _context_adapters(context))
    if family is None:
        return definitions
    return tuple(
        item for item in definitions if item.pillar == family
    )


def _registry_payload(definitions: Iterable[FeatureDefinition | Mapping[str, Any]]) -> dict[str, Any]:
    canonical = validate_registry(definitions)
    return {
        "contract_version": REGISTRY_VERSION,
        "registry_version": REGISTRY_VERSION,
        "feature_count": len(canonical),
        "features": [item.to_dict() for item in canonical],
    }


def registry_content_checksum(
    definitions: Iterable[FeatureDefinition | Mapping[str, Any]],
) -> str:
    """SHA-256 of the exact canonical registry payload (without self-reference)."""

    return hashlib.sha256(canonical_json(_registry_payload(definitions)).encode("utf-8")).hexdigest()


registry_checksum = registry_content_checksum


def serialize_registry(
    definitions: Iterable[FeatureDefinition | Mapping[str, Any]],
) -> dict[str, Any]:
    """Return one JSON-serializable registry object with its content checksum."""

    payload = _registry_payload(definitions)
    payload["registry_sha256"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


__all__ = [
    "KNOWN_FORMULA_IDS", "REGISTRY_VERSION", "RegistryError", "build_registry",
    "registry_checksum", "registry_content_checksum", "serialize_registry",
    "validate_adapter_handshakes", "validate_registry",
]
