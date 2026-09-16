"""Deterministic Phase 3 data-readiness and measurement prioritization.

This module is deliberately read-only.  It consumes the same registered
adapters as the feature frame, keeps static configuration out of the
observation stream, and exposes every predicate used to choose one of the six
normative readiness states.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import json
import math
import re
import sqlite3
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .contracts import AdapterContext, DateRange, FeatureDefinition
from .adapters import identity_parts
from .frame import adapter_status, build_feature_frame, load_adapters
from .goals import (
    GOAL_KEYS, GoalError, collector_freshness, goal_definitions, list_goals,
    validate_goal_pair,
)
from .normalize import normalized_label


READINESS_VERSION = "data-readiness-v1"
READINESS_STATES = (
    "logic_not_implemented",
    "present_not_connected",
    "implemented_never_logged",
    "stale",
    "too_sparse_for_analysis",
    "sufficient",
)

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OBSERVED_STATES = frozenset({"observed", "structural_zero"})


class ReadinessError(RuntimeError):
    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


def _validation(message: str) -> None:
    raise ReadinessError("validation_error", message, validation=True)


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _source(definition: Any) -> Mapping[str, Any]:
    value = _value(definition, "source", {})
    return value if isinstance(value, Mapping) else {}


def _parameters(definition: Any) -> Mapping[str, Any]:
    value = _source(definition).get("parameters", {})
    return value if isinstance(value, Mapping) else {}


def _source_tables(definition: Any) -> list[str]:
    source = _source(definition)
    tables: list[str] = []
    if isinstance(source.get("table"), str):
        tables.append(source["table"])
    if isinstance(source.get("tables"), (list, tuple)):
        tables.extend(item for item in source["tables"] if isinstance(item, str))
    return sorted(set(tables))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    if not _SAFE_IDENTIFIER.fullmatch(table):
        return False
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    cursor = conn.execute(f'SELECT * FROM "{table}" LIMIT 0')
    return {str(item[0]) for item in (cursor.description or ())}


def _storage_present(conn: sqlite3.Connection, tables: Iterable[str]) -> bool:
    return any(_table_exists(conn, table) for table in tables)


def _observed(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("state") in _OBSERVED_STATES]


def _date_of(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _days_in_range(date_range: DateRange) -> int | None:
    if date_range.start is None or date_range.end is None:
        return None
    return (date_range.end - date_range.start).days + 1


def _anchor(date_range: DateRange, context: AdapterContext) -> date:
    return date_range.end if date_range.kind == "bounded" else context.today


def _static_config_evidence(
    conn: sqlite3.Connection, definition: Any, anchor: date,
    context: AdapterContext,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Return exact configuration evidence without fabricating observations."""

    key = str(_value(definition, "key"))
    source = _source(definition)
    params = _parameters(definition)
    if key.startswith("calendar."):
        return True, anchor.isoformat(), {"kind": "code_generated_calendar"}

    if key.startswith("source.collector."):
        collector = params.get("source")
        if not isinstance(collector, str) or not _table_exists(conn, "source_sync_runs"):
            return False, None, {"kind": "collector_run", "source": collector}
        success_only = key.endswith(".freshness_days")
        row = conn.execute(
            "SELECT id,completed_at,status FROM source_sync_runs "
            "WHERE source=? AND substr(completed_at,1,10)<=? "
            + ("AND status='success' " if success_only else "")
            + "ORDER BY completed_at DESC,id DESC LIMIT 1",
            (collector, anchor.isoformat()),
        ).fetchone()
        return (
            row is not None,
            _date_of(row["completed_at"]) if row is not None else None,
            {"kind": "collector_success" if success_only else "collector_run",
             "source": collector,
             "run_id": row["id"] if row is not None else None,
             "status": row["status"] if row is not None else None},
        )

    if key.startswith("source.daily_metrics."):
        metric = params.get("metric")
        columns = _table_columns(conn, "daily_metrics")
        if not isinstance(metric, str) or metric not in columns:
            return False, None, {"kind": "selected_provider", "metric": metric}
        first, second = (("apple", "fitbit") if metric == "distance_km"
                         else ("fitbit", "apple"))
        row = conn.execute(
            f'SELECT date,source FROM "daily_metrics" '
            f'WHERE date<=? AND "{metric}" IS NOT NULL '
            "ORDER BY date DESC,CASE WHEN source=? THEN 0 "
            "WHEN source=? THEN 1 ELSE 2 END,source LIMIT 1",
            (anchor.isoformat(), first, second),
        ).fetchone()
        return (
            row is not None,
            row["date"] if row is not None else None,
            {"kind": "selected_provider", "metric": metric,
             "provider": row["source"] if row is not None else None},
        )

    if key.startswith("training.plan."):
        if not _table_exists(conn, "training_plan_revisions"):
            return False, None, {"kind": "effective_plan_revision"}
        row = conn.execute(
            "SELECT id,effective_from,schedule_json,routines_json "
            "FROM training_plan_revisions "
            "WHERE effective_from<=? ORDER BY effective_from DESC,id DESC LIMIT 1",
            (anchor.isoformat(),),
        ).fetchone()
        if row is None:
            return False, None, {"kind": "effective_plan_revision"}
        try:
            schedule = json.loads(row["schedule_json"])
            routines = json.loads(row["routines_json"])
        except (TypeError, json.JSONDecodeError):
            return False, None, {"kind": "invalid_effective_plan_revision",
                                 "revision_id": row["id"]}

        scheduled: set[str] = set()
        if isinstance(schedule, list):
            scheduled = {
                normalized_label(str(item.get("routine_name")))
                for item in schedule if isinstance(item, Mapping)
                and item.get("routine_name")
            }
        elif isinstance(schedule, Mapping):
            scheduled = {normalized_label(str(value)) for value in schedule.values()
                         if isinstance(value, str) and normalized_label(value)}

        records: list[tuple[str, str, str, float | None]] = []
        if isinstance(routines, list):
            for item in routines:
                if not isinstance(item, Mapping):
                    continue
                routine = item.get("routine_name") or item.get("name")
                exercise = item.get("exercise_title") or item.get("exercise") or item.get("title")
                target = item.get("target_sets", item.get("sets"))
                if isinstance(routine, str) and isinstance(exercise, str):
                    value = (float(target) if isinstance(target, (int, float))
                             and not isinstance(target, bool) and math.isfinite(float(target))
                             else None)
                    records.append((normalized_label(routine), normalized_label(exercise), exercise, value))
        elif isinstance(routines, Mapping):
            for routine, raw in routines.items():
                items = raw.get("exercises", ()) if isinstance(raw, Mapping) else raw
                if not isinstance(routine, str) or not isinstance(items, (list, tuple)):
                    continue
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    exercise = item.get("exercise_title") or item.get("exercise") or item.get("title")
                    target = item.get("target_sets", item.get("sets"))
                    if isinstance(exercise, str):
                        value = (float(target) if isinstance(target, (int, float))
                                 and not isinstance(target, bool) and math.isfinite(float(target))
                                 else None)
                        records.append((normalized_label(routine), normalized_label(exercise), exercise, value))
        active = [item for item in records if item[0] in scheduled]
        identity = source.get("identity")
        if isinstance(identity, Mapping):
            wanted = normalized_label(str(identity.get("original_label") or ""))
            present = any(exercise == wanted and target is not None
                          for _routine, exercise, _raw_exercise, target in active)
        else:
            group = params.get("group")
            present = False
            group_map_fn = context.functions.get("group_weight_maps")
            basis_fn = context.functions.get("basis_weights")
            if isinstance(group, str) and callable(group_map_fn) and callable(basis_fn):
                try:
                    authored, coarse = group_map_fn(conn)
                    for _routine, _exercise, raw_exercise, target in active:
                        if target is None or target <= 0:
                            continue
                        weights, _basis = basis_fn(raw_exercise, authored, coarse)
                        if weights and any(
                            str(name).casefold() == group.casefold()
                            and isinstance(weight, (int, float)) and weight > 0
                            for name, weight in weights.items()
                        ):
                            present = True
                            break
                except (sqlite3.Error, TypeError, ValueError):
                    present = False
        return (
            present,
            row["effective_from"] if present else None,
            {"kind": "effective_plan_revision",
             "revision_id": row["id"], "feature_present": present,
             "parameters": dict(params)},
        )

    # STATIC_CONFIG is a closed registry profile.  An unknown formula is not
    # upgraded merely because its table happens to contain an unrelated row.
    return False, None, {"kind": "unrecognized_static_contract", "source": dict(source)}


def _direct_source_evidence(conn: sqlite3.Connection, definition: Any) -> bool:
    """Conservatively detect feature-specific source rows for a disconnected adapter.

    This is not an alternate adapter.  It is used only for predicate two and
    requires exact identity/parameter filters plus a non-null source value
    where the registry names one.  A generic populated table never suffices.
    """

    # The canonical adapter evidence pass below owns every normal source
    # contract.  This one narrow exception recognizes a valid, non-null manual
    # nutrition total even when the day lacks the completeness revision needed
    # to make it analytically eligible.  No generic table fallback is allowed.
    if _value(definition, "formula_id") != "complete_daily_sum_v1":
        return False
    source = _source(definition)
    table = source.get("table")
    if table != "nutrition_log":
        return False
    key = str(_value(definition, "key"))
    field = key.rsplit(".", 1)[-1]
    columns = _table_columns(conn, table)
    if field not in {"kcal", "protein_g", "carbs_g", "fat_g", "fiber_g"} or field not in columns:
        return False
    return conn.execute(
        f'SELECT 1 FROM "nutrition_log" WHERE "{field}" IS NOT NULL LIMIT 1'
    ).fetchone() is not None


def _prerequisites(definition: Any, context: AdapterContext) -> list[str]:
    key = str(_value(definition, "key"))
    formula = _value(definition, "formula_id")
    params = _parameters(definition)
    if formula == "same_row_wcr_v1":
        return ["body.waist_cm", "body.chest_cm"]
    if formula == "ratio_of_known_totals_v1":
        base = key.rsplit(".", 1)[0]
        if key.endswith(".pace_min_per_km"):
            return [f"{base}.duration_min", f"{base}.distance_km"]
        if key.endswith(".intensity_kcal_per_min"):
            return [f"{base}.kcal", f"{base}.duration_min"]
    if formula == "recovery_combined_score_v1":
        provider = params.get("provider")
        hrv = "apple_sdnn" if provider == "apple" else "fitbit_rmssd"
        return [f"recovery.rhr_score.{provider}", f"recovery.hrv_score.{hrv}"]
    if formula in {"tested_ratio_v1", "distance_to_band_v1"}:
        ratio, side = params.get("ratio"), params.get("side")
        seeds = context.constants.get("RATIO_SEED", ())
        item = next((entry for entry in seeds if isinstance(entry, Mapping)
                     and entry.get("key") == ratio), None)
        if item and isinstance(side, str):
            return [f"fitness.test.{item['num']}.{side}.value",
                    f"fitness.test.{item['den']}.{side}.value"]
    if formula == "side_gap_v1":
        movement = params.get("movement")
        ratio = params.get("ratio")
        if isinstance(movement, str):
            return [f"fitness.test.{movement}.left.value",
                    f"fitness.test.{movement}.right.value"]
        if isinstance(ratio, str):
            return [f"fitness.ratio.{ratio}.left.value",
                    f"fitness.ratio.{ratio}.right.value"]
    if formula in {"target_fraction_v1", "target_met_v1"}:
        nutrient = params.get("nutrient")
        return [f"nutrition.nutrient.{nutrient}.amount",
                f"config:nutrition_target.{nutrient}"]
    if formula == "timing_adherence_v1":
        metric = params.get("metric")
        return [f"timing_actual.{metric}", f"config:planned_time.{metric}"]
    if formula == "logged_planned_ratio_v1":
        group = params.get("group")
        return [f"training.group.{group}.effective_sets",
                f"training.plan.group.{group}.effective_sets"]
    if formula == "athletic_axis_v1":
        axis = params.get("axis")
        movement = params.get("movement")
        if axis == "strength":
            lifts = params.get("target_lifts", ())
            prerequisites = []
            for lift in lifts if isinstance(lifts, (list, tuple)) else ():
                if isinstance(lift, str) and normalized_label(lift):
                    _identity, token, _normal = identity_parts("exercise", lift)
                    prerequisites.append(f"training.exercise.{token}.best_e1rm_kg")
            return [*prerequisites, "config:athletic_target.strength"]
        catalog = context.constants.get("CATALOG", {})
        info = catalog.get(movement, {}) if isinstance(catalog, Mapping) else {}
        sides = ("left", "right") if isinstance(info, Mapping) and info.get("unilateral") else ("bilateral",)
        tests = [f"fitness.test.{movement}.{side}.value" for side in sides]
        return [*tests, f"config:athletic_target.{axis}"]
    if formula == "assessment_fraction_v1":
        return [key.rsplit(".", 1)[0] + ".score", "config:assessment.max_score"]
    return []


def _needed(
    definition: Any, state: str, *, observations: int, minimum: int,
    stale_days: int | None, gate_failures: list[str], context: AdapterContext,
) -> str:
    formula = _value(definition, "formula_id")
    params = _parameters(definition)
    if state == "logic_not_implemented":
        return "logic is explicitly deferred pending an owner-approved capture/computation contract"
    if state == "present_not_connected":
        return f"connect the registered {_value(definition, 'adapter')} adapter to existing valid rows"
    if state == "stale":
        return f"refresh the latest protocol-valid measurement (stale after {stale_days} days)"
    if state == "too_sparse_for_analysis":
        short = ", ".join(gate_failures) if gate_failures else "analysis gate"
        return f"collect enough aligned protocol-valid observations to pass: {short}"
    if state == "sufficient":
        return "sufficient for this readiness query"
    if formula in {"tested_ratio_v1", "distance_to_band_v1"}:
        side = params.get("side", "same")
        return f"one protocol-consistent {side}-side pair; repeat next quarter for change"
    if formula == "side_gap_v1":
        return "one protocol-consistent left/right pair; repeat next quarter for change"
    if formula == "same_row_wcr_v1":
        return "one same-session waist and chest pair; repeat at the next body episode for change"
    if formula in {"target_fraction_v1", "target_met_v1"}:
        return "one unit-compatible nutrient amount plus its effective code/owner target"
    if formula == "timing_adherence_v1":
        return "one valid actual time plus the effective planned time and tolerance"
    if formula == "logged_planned_ratio_v1":
        return "one complete Hevy interval plus an effective positive training plan"
    if formula == "athletic_axis_v1":
        return "the exact axis test protocol plus an explicit owner target"
    if formula == "assessment_fraction_v1":
        return "one exact scale/part score with a positive recorded maximum"
    region, side = params.get("region"), params.get("side")
    if formula == "pain_daily_latest_v1":
        return (f"one explicit {region} {side} NRS observation from 0 to 10; "
                "missing is never pain-free")
    if formula == "reported_onset_marker_v1":
        return (f"one owner-reported {region} {side} onset date with exact or "
                "approximate precision")
    if formula == "first_positive_marker_v1":
        return f"one explicit positive {region} {side} NRS observation; never infer onset"
    if formula == "resolution_marker_v1":
        return f"one explicit {region} {side} NRS=0 observation; absence is not resolution"
    if formula == "fitness_protocol_value_v1":
        return (f"one protocol-consistent {params.get('movement')} {params.get('side')} "
                f"{params.get('kind')} result in {_value(definition, 'unit')}; retain equipment/protocol")
    if formula == "self_test_result_v1":
        return (f"one explicit {params.get('test')} {params.get('side')} self-test result; "
                "evidence only, not a diagnosis")
    if formula in {"rehab_response_v1", "rehab_pain_during_v1"}:
        return (f"one explicit {params.get('drill')} trial for {params.get('target')} with "
                "recorded dose/context; never infer treatment effect")
    if str(_value(definition, "key")).startswith("vitals."):
        vital = str(_value(definition, "key")).split(".", 1)[1].replace("_", " ")
        return f"one cuff/protocol-valid {vital} observation with its actual date/time"
    identity = _source(definition).get("identity")
    label = (identity.get("original_label") if isinstance(identity, Mapping) else None)
    pillar = _value(definition, "pillar")
    if pillar == "adherence" and formula == "habit_explicit_v1":
        return (f"one explicit done/not-done log for {label}; absence requires an exact "
                "complete other-event attestation")
    if pillar == "supplement":
        return (f"one explicit taken/not-taken log for {label}; dose/time only when actually recorded")
    if pillar == "skincare":
        return (f"one explicit used/not-used log for active product {label}; no value from absence")
    if pillar == "lab":
        return (f"one exact {label} lab episode in a catalog-compatible unit; never guess a unit")
    if pillar == "medication":
        return f"one explicit {label} {str(_value(definition, 'key')).rsplit('.', 1)[-1]} record"
    if formula == "effective_plan_v1":
        if isinstance(identity, Mapping):
            return f"add exact exercise {label} with defined sets to a scheduled effective plan revision"
        return (f"add a scheduled exercise with a positive mapped {params.get('group')} "
                "contribution to an effective plan revision")
    if formula == "body_source_tier_episode_v1":
        measure = str(_value(definition, "key")).split(".", 1)[1].replace("_", " ")
        return f"one actual {measure} body-measurement episode; repeat only at the real cadence"
    if _value(definition, "temporal_type") == "slow_measurement":
        return "ready to measure one protocol-valid episode; repeat only at its real cadence"
    return "ready to measure or log one protocol-valid observation"


def _unlocks(definition: Any) -> list[str]:
    formula = _value(definition, "formula_id")
    key = str(_value(definition, "key"))
    if formula in {"tested_ratio_v1", "distance_to_band_v1"}:
        label = str(_parameters(definition).get("ratio", "ratio")).upper()
        return [f"{label} status and later pain-timeline context"]
    if formula == "side_gap_v1":
        return ["protocol-matched bilateral gap status and quarterly change"]
    if formula == "same_row_wcr_v1":
        return ["same-session waist-to-chest status and body-episode change"]
    if formula in {"target_fraction_v1", "target_met_v1"}:
        return ["target-relative nutrient readiness without inventing intake"]
    if formula == "timing_adherence_v1":
        return ["explicit timing adherence for the selected concrete outcome"]
    if formula == "athletic_axis_v1":
        return ["owner-targeted athletic-axis display"]
    params = _parameters(definition)
    if formula == "pain_daily_latest_v1":
        return [f"{params.get('region')} {params.get('side')} pain-state timeline and exact change"]
    if formula in {"reported_onset_marker_v1", "first_positive_marker_v1", "resolution_marker_v1"}:
        return [f"bounded {params.get('region')} {params.get('side')} pain timeline context"]
    if formula == "fitness_protocol_value_v1":
        return [f"protocol-comparable {params.get('movement')} {params.get('side')} status and change"]
    if formula == "self_test_result_v1":
        return ["bounded self-test evidence context; never treatment or diagnosis"]
    if formula in {"rehab_response_v1", "rehab_pain_during_v1"}:
        return ["bounded rehab-trial timeline context; never treatment effect"]
    pillar = _value(definition, "pillar")
    identity = _source(definition).get("identity")
    label = identity.get("original_label") if isinstance(identity, Mapping) else None
    if pillar == "vitals":
        return ["dated vital-sign readiness and medical-neutral outcome context"]
    if pillar == "adherence" and formula == "habit_explicit_v1":
        return [f"concrete {label} follow-through readiness"]
    if pillar == "supplement":
        return [f"concrete {label} exposure context with medical constraint"]
    if pillar == "skincare":
        return [f"concrete {label} use context for subjective outcomes only"]
    if pillar == "lab":
        return [f"dated {label} medical-constraint context; no generic candidate"]
    goals = list(_value(definition, "goal_families", []) or [])
    result = [f"{goal} readiness" for goal in goals]
    roles = set(_value(definition, "roles", []) or [])
    if "outcome" in roles:
        result.append("outcome-specific alignment")
    elif _value(definition, "candidate_enabled", False):
        result.append("eligible cross-domain exposure context")
    return list(dict.fromkeys(result))


def _collector_dependency(definition: Any, observations: list[Mapping[str, Any]]) -> str | None:
    params = _parameters(definition)
    if _value(definition, "source_semantics") == "operational_sync":
        source = params.get("source")
        return source if source in {"google-health", "hevy", "weather", "air"} else None
    pillar = _value(definition, "pillar")
    if pillar == "weather":
        return "weather"
    if pillar == "air":
        return "air"
    if pillar == "training" and not str(_value(definition, "key")).startswith("training.plan."):
        return "hevy"
    semantics = _value(definition, "source_semantics")
    latest = max((str(row.get("observed_at") or "") for row in observations), default="")
    sources = {str(row.get("source") or "").casefold() for row in observations
               if str(row.get("observed_at") or "") == latest}
    if semantics in {"selected_automated_daily", "automated_daily"} and "fitbit" in sources:
        return "google-health"
    return None


def _quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def _analysis_gates(
    definition: Any, rows: list[Mapping[str, Any]], *, aligned_n: int,
    missing_rate: float, minimum: int,
    outcome_rows: list[Mapping[str, Any]] | None,
    outcome_definition: Any | None,
) -> dict[str, Any]:
    """Expose Phase 3 readiness gates only; no effect/statistical finding is computed."""

    analysis_relevant = bool(
        _value(definition, "candidate_enabled", False)
        or "outcome" in set(_value(definition, "roles", []) or [])
        or "exposure" in set(_value(definition, "roles", []) or [])
    )
    enforce = analysis_relevant and minimum >= 30
    failures: list[str] = []
    if analysis_relevant and aligned_n < minimum:
        failures.append(f"aligned_n<{minimum}")
    if enforce and missing_rate > 0.50:
        failures.append("missing_rate>0.50")

    values = [row.get("value") for row in rows]
    value_kind = _value(definition, "value_kind")
    zero_semantics = _value(definition, "zero_semantics")
    binary_like = (value_kind == "binary"
                   or zero_semantics in {"explicit_binary", "structural_zero_if_complete"})
    binary_by_date: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        day = _date_of(row.get("observed_at"))
        if day is not None:
            binary_by_date[day].append(row.get("value"))
    exposed = sum(1 for day_values in binary_by_date.values()
                  if any(isinstance(value, (int, float, bool)) and value > 0
                         for value in day_values))
    unexposed = sum(1 for day_values in binary_by_date.values()
                    if day_values and all(
                        isinstance(value, (int, float, bool)) and value == 0
                        for value in day_values))
    if enforce and binary_like and (exposed < 10 or unexposed < 10):
        failures.append("binary_prevalence<10_each")

    numeric = [float(value) for value in values
               if isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(float(value))]
    distinct = len(set(numeric))
    q1, q3 = _quantile(numeric, 0.25), _quantile(numeric, 0.75)
    iqr = None if q1 is None or q3 is None else q3 - q1
    continuous_like = value_kind in {"continuous", "integer", "count", "time_minutes", "ratio"}
    if enforce and continuous_like and (distinct < 5 or iqr in (None, 0.0)):
        failures.append("continuous_variation")

    day_rows = (outcome_rows or []) if (
        outcome_definition is not None
        and _value(outcome_definition, "key") == "subjective.day_rating"
    ) else []
    day_counts = Counter(row.get("value") for row in day_rows)
    day_gate = {
        "red": day_counts.get(1, 0), "yellow": day_counts.get(2, 0),
        "green": day_counts.get(3, 0),
    }
    day_gate.update({
        "ordinal_pass": all(day_gate[color] >= 5 for color in ("red", "yellow", "green")),
        "green_contrast_pass": day_gate["green"] >= 10
        and day_gate["red"] + day_gate["yellow"] >= 10,
        "red_contrast_pass": day_gate["red"] >= 10
        and day_gate["yellow"] + day_gate["green"] >= 10,
    })
    if enforce and outcome_definition is not None and (
        _value(outcome_definition, "key") == "subjective.day_rating"
    ) and not all(
        day_gate[name] for name in ("ordinal_pass", "green_contrast_pass", "red_contrast_pass")
    ):
        failures.append("day_rating_modes")

    outcome_values = [row.get("value") for row in (outcome_rows or [])]
    outcome_kind = (_value(outcome_definition, "value_kind")
                    if outcome_definition is not None else None)
    outcome_zero = (_value(outcome_definition, "zero_semantics")
                    if outcome_definition is not None else None)
    outcome_binary = (outcome_kind == "binary"
                      or outcome_zero in {"explicit_binary", "structural_zero_if_complete"})
    outcome_by_date: dict[str, list[Any]] = defaultdict(list)
    for row in outcome_rows or []:
        day = _date_of(row.get("observed_at"))
        if day is not None:
            outcome_by_date[day].append(row.get("value"))
    outcome_positive = sum(1 for day_values in outcome_by_date.values()
                           if any(isinstance(value, (int, float, bool)) and value > 0
                                  for value in day_values))
    outcome_negative = sum(1 for day_values in outcome_by_date.values()
                           if day_values and all(
                               isinstance(value, (int, float, bool)) and value == 0
                               for value in day_values))
    outcome_numeric = [float(value) for value in outcome_values
                       if isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(float(value))]
    outcome_distinct = len(set(outcome_numeric))
    oq1, oq3 = _quantile(outcome_numeric, 0.25), _quantile(outcome_numeric, 0.75)
    outcome_iqr = None if oq1 is None or oq3 is None else oq3 - oq1
    outcome_ordinal = outcome_kind == "ordinal"
    outcome_continuous = outcome_kind in {
        "continuous", "integer", "count", "time_minutes", "ratio", "episode",
    }
    is_day_rating = bool(outcome_definition is not None
                         and _value(outcome_definition, "key") == "subjective.day_rating")
    if enforce and outcome_definition is not None and not is_day_rating:
        if outcome_binary and (outcome_positive < 10 or outcome_negative < 10):
            failures.append("outcome_prevalence<10_each")
        elif outcome_ordinal and (outcome_distinct < 2 or outcome_iqr in (None, 0.0)):
            failures.append("outcome_variation")
        elif outcome_continuous and (outcome_distinct < 5 or outcome_iqr in (None, 0.0)):
            failures.append("outcome_variation")

    return {
        "analysis_relevant": analysis_relevant,
        "enforced_frozen_gates": enforce,
        "minimum_observations": minimum,
        "sample_pass": aligned_n >= minimum,
        "missing_suppressed": enforce and missing_rate > 0.50,
        "missing_warning": enforce and 0.30 < missing_rate <= 0.50,
        "binary_exposed": exposed,
        "binary_unexposed": unexposed,
        "binary_prevalence_pass": not binary_like or (exposed >= 10 and unexposed >= 10),
        "continuous_distinct": distinct,
        "continuous_iqr": iqr,
        "continuous_variation_pass": not continuous_like or (distinct >= 5 and iqr not in (None, 0.0)),
        "day_rating": day_gate,
        "outcome_gate": {
            "feature_key": (_value(outcome_definition, "key")
                            if outcome_definition is not None else None),
            "aligned_rows": len(outcome_rows or []),
            "positive": outcome_positive,
            "negative_or_zero": outcome_negative,
            "binary_prevalence_pass": (not outcome_binary
                                       or (outcome_positive >= 10 and outcome_negative >= 10)),
            "distinct": outcome_distinct,
            "iqr": outcome_iqr,
            "variation_pass": (
                (not outcome_continuous and not outcome_ordinal)
                or (outcome_ordinal and outcome_distinct >= 2 and outcome_iqr not in (None, 0.0))
                or (outcome_continuous and outcome_distinct >= 5 and outcome_iqr not in (None, 0.0))
            ),
        },
        "gate_failures": failures,
    }


def _leading_tags(definition: Any, context: AdapterContext) -> set[str]:
    key = str(_value(definition, "key"))
    pillar = str(_value(definition, "pillar"))
    formula = str(_value(definition, "formula_id"))
    parameters = _parameters(definition)
    tags = {pillar}
    mapping = {
        "training": {"strength", "exercise", "muscle"},
        "adherence": {"adherence", "plans"},
        "subjective": {"wellbeing"},
        "substance": {"substances"},
        "event": {"events", "timeline"},
        "wearable": {"recovery"},
        "recovery": {"recovery"},
        "cardio": {"running"},
        "food": {"food", "nutrition"},
        "meal": {"food", "nutrition"},
        "supplement": {"nutrition"},
        "weather": {"environment"},
        "air": {"environment"},
        "calendar": {"environment"},
        "pain": {"pain", "timeline"},
        "rehab": {"pain", "timeline"},
    }
    tags.update(mapping.get(pillar, set()))
    if pillar == "fitness":
        if (formula == "fitness_protocol_value_v1"
                and parameters.get("kind") in {"strength", "hold", "control"}):
            tags.add("strength")
        if (formula == "athletic_axis_v1"
                and parameters.get("aggregation") == "e1rm_lift"):
            tags.add("strength")
        mobility = context.constants.get("MOBILITY_NORM", {})
        if (isinstance(mobility, Mapping)
                and parameters.get("movement") in mobility):
            tags.add("mobility")
    if pillar == "medication" and key.endswith((".first_dose_min", ".last_dose_min")):
        tags.add("med_timing")
    if key.startswith("training.plan.") or key.startswith("training.logged_vs_planned."):
        tags.add("plans")
    return tags


def _goal_multiplier(
    definition: Any, enabled_goals: list[Mapping[str, Any]], by_key: Mapping[str, Any],
    context: AdapterContext,
) -> tuple[float, int | None, list[dict[str, Any]]]:
    specs = {item["key"]: item for item in goal_definitions()}
    key = str(_value(definition, "key"))
    family_membership = set(_value(definition, "goal_families", []) or [])
    eligible: list[dict[str, Any]] = []
    for revision in enabled_goals:
        goal = revision["key"]
        selected = revision.get("outcome_key")
        if goal not in family_membership or selected not in by_key:
            continue
        prereqs = set(_prerequisites(by_key[selected], context))
        leading = set(specs[goal]["leading_feature_families"])
        reasons: list[str] = []
        if key == selected:
            reasons.append("selected_outcome")
        if key in prereqs:
            reasons.append("explicit_prerequisite")
        if "all_eligible" in leading or _leading_tags(definition, context) & leading:
            reasons.append("allowed_leading_family")
        if reasons:
            eligible.append({"goal": goal, "priority": revision.get("priority"),
                             "selected_outcome": selected, "reasons": reasons})
    priorities = [item["priority"] for item in eligible
                  if isinstance(item.get("priority"), int)]
    highest = max(priorities, default=None)
    multiplier = 1.0 if highest is None else 1 + 0.1 * (highest - 1)
    return multiplier, highest, eligible


def _validate_goal_outcome(goal: str, outcome: str, by_key: Mapping[str, Any]) -> None:
    errors: list[GoalError] = []
    for direction in ("increase", "decrease", "maintain"):
        try:
            validate_goal_pair(goal, outcome, direction, registered_keys=by_key)
            return
        except GoalError as exc:
            errors.append(exc)
    _validation("outcome is not an allowed concrete outcome for this goal")


def _nutrition_complete_dates(conn: sqlite3.Connection, dates: set[str]) -> set[str]:
    """Return dates whose effective nutrition_total revision is complete."""

    if not dates or not _table_exists(conn, "capture_completeness_revisions"):
        return set()
    complete: set[str] = set()
    for day in sorted(dates):
        row = conn.execute(
            "SELECT state FROM capture_completeness_revisions "
            "WHERE date=? AND scope='nutrition_total' AND entity_key IS NULL "
            "ORDER BY id DESC LIMIT 1", (day,),
        ).fetchone()
        if row is not None and row["state"] == "complete":
            complete.add(day)
    return complete


def build_readiness(
    conn: sqlite3.Connection,
    definitions: Iterable[FeatureDefinition] | Mapping[str, FeatureDefinition],
    date_range: DateRange,
    context: AdapterContext,
    *,
    goal: str | None = None,
    outcome: str | None = None,
    modules: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the six readiness states in their normative predicate order."""

    all_values = list(definitions.values() if isinstance(definitions, Mapping) else definitions)
    by_key = {_value(item, "key"): item for item in all_values}
    if len(by_key) != len(all_values):
        raise ReadinessError("registry_contract_error", "duplicate feature key")
    if goal is not None and goal not in GOAL_KEYS:
        _validation(f"goal must be one of: {', '.join(GOAL_KEYS)}")
    if outcome is not None:
        if outcome not in by_key:
            _validation("outcome is not a registered feature")
        if "outcome" not in set(_value(by_key[outcome], "roles", []) or []):
            _validation("outcome is not registered as an outcome feature")
    if goal is not None and outcome is not None:
        _validate_goal_outcome(goal, outcome, by_key)

    values = all_values
    if goal is not None:
        values = [item for item in values if goal in (_value(item, "goal_families", []) or [])]

    modules = dict(modules or load_adapters())
    statuses = adapter_status(all_values, modules)
    # Frames remain built from the complete registry so a selected outcome is
    # available even when --goal filters the returned readiness rows.
    current = build_feature_frame(conn, all_values, date_range, context, modules=modules)
    all_range = DateRange(start=None, end=None, kind="all")
    historical = build_feature_frame(conn, all_values, all_range, context, modules=modules)
    current_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in current["observations"]:
        current_by_key[row["feature_key"]].append(row)
    for row in historical["observations"]:
        all_by_key[row["feature_key"]].append(row)

    # Predicate two asks whether rows valid for this exact feature already
    # exist even though the caller-visible adapter handshake is broken.  Reuse
    # the code-owned adapter formulas for that evidence; do not approximate
    # validity from a populated table.  This pass is still SELECT-only.
    evidence_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disconnected = [
        item for item in all_values
        if not statuses.get(_value(item, "adapter"), {}).get("connected", False)
        and _value(item, "completeness_profile") != "STATIC_CONFIG"
    ]
    if disconnected:
        canonical_modules = load_adapters()
        canonical_status = adapter_status(disconnected, canonical_modules)
        provable = [item for item in disconnected
                    if canonical_status.get(_value(item, "adapter"), {}).get("connected")]
        if provable:
            evidence_frame = build_feature_frame(
                conn, provable, all_range, context, modules=canonical_modules,
            )
            for row in evidence_frame["observations"]:
                if row.get("state") in _OBSERVED_STATES:
                    evidence_by_key[row["feature_key"]].append(row)

    outcome_rows = _observed(current_by_key[outcome]) if outcome is not None else []
    outcome_dates = {_date_of(row.get("observed_at")) for row in outcome_rows}
    outcome_dates.discard(None)

    effective_goals = list_goals(conn, include_disabled=True)["goals"]
    enabled_goals = [item for item in effective_goals if item["enabled"]]
    range_days = _days_in_range(date_range)
    anchor = _anchor(date_range, context)
    historical_query = date_range.kind == "bounded" and anchor < context.today
    now_fn = context.functions.get("now") if isinstance(context.functions, Mapping) else None
    now = now_fn() if callable(now_fn) else datetime.now(ZoneInfo(context.timezone))

    rows: list[dict[str, Any]] = []
    for definition in values:
        key = str(_value(definition, "key"))
        implemented = bool(_value(definition, "implemented"))
        adapter_id = _value(definition, "adapter")
        connected = bool(statuses.get(adapter_id, {}).get("connected", False))
        tables = _source_tables(definition)
        storage_present = _storage_present(conn, tables)
        static = (_value(definition, "completeness_profile") == "STATIC_CONFIG"
                  or _value(definition, "temporal_type") == "static_config")
        config_present, config_latest, config_details = (
            _static_config_evidence(conn, definition, anchor, context)
            if static else (False, None, {})
        )
        current_obs = _observed(current_by_key[key])
        all_obs = _observed(all_by_key[key])
        observations = len(all_obs)
        source_rows_present = config_present if static else (
            bool(all_obs) or bool(evidence_by_key.get(key))
            or _direct_source_evidence(conn, definition)
        )

        as_of_dates = [parsed for parsed in
                       (_date_of(row.get("observed_at")) for row in all_obs)
                       if parsed is not None and parsed <= anchor.isoformat()]
        latest_at = config_latest if static else max(as_of_dates, default=None)
        stale_after = _value(definition, "stale_after_days")
        measurement_stale = bool(
            latest_at and stale_after is not None
            and (anchor - date.fromisoformat(latest_at)).days > stale_after
        )
        collector_source = _collector_dependency(definition, all_obs)
        freshness = collector_freshness(conn, collector_source, now=now) if collector_source else None
        # Collector freshness is operational/current state.  It must never
        # relabel a valid historical observation as stale at its recorded date.
        collector_stale = bool(
            not historical_query and freshness and freshness["status"] == "stale"
        )
        stale = measurement_stale or collector_stale

        current_valid = current_obs
        if key.startswith("nutrition.logged."):
            dates = {_date_of(row.get("observed_at")) for row in current_obs}
            dates.discard(None)
            complete_dates = _nutrition_complete_dates(conn, dates)
            current_valid = [row for row in current_obs
                             if _date_of(row.get("observed_at")) in complete_dates]

        current_dates = {_date_of(row.get("observed_at")) for row in current_valid}
        current_dates.discard(None)
        if static:
            eligible_units = 0
            missing_rate = 0.0 if config_present else 1.0
            aligned_rows: list[Mapping[str, Any]] = []
            aligned_n = 0
        else:
            aligned_rows = ([row for row in current_valid
                             if _date_of(row.get("observed_at")) in outcome_dates]
                            if outcome is not None else list(current_valid))
            temporal = _value(definition, "temporal_type")
            slow_episode = (temporal == "slow_measurement"
                            or _value(definition, "completeness_profile") == "SLOW_EPISODE")
            aligned_n = (len(aligned_rows) if slow_episode else len({
                parsed for parsed in
                (_date_of(row.get("observed_at")) for row in aligned_rows)
                if parsed is not None
            }))
            if slow_episode:
                eligible_units = len(outcome_dates) if outcome is not None else len(current_valid)
                missing_rate = 0.0 if aligned_rows else 1.0
            elif outcome is not None:
                eligible_units = len(outcome_dates)
                aligned_dates = {_date_of(row.get("observed_at")) for row in aligned_rows}
                aligned_dates.discard(None)
                missing_rate = (round(max(0.0, 1 - len(aligned_dates) / eligible_units), 6)
                                if eligible_units else 1.0)
            elif range_days is not None:
                eligible_units = range_days
                missing_rate = round(max(0.0, 1 - len(current_dates) / range_days), 6)
            else:
                eligible_units = len(current_dates)
                missing_rate = 0.0 if current_dates else 1.0

        minimum = int(_value(definition, "min_observations", 30) or 0)
        aligned_dates = {_date_of(row.get("observed_at")) for row in aligned_rows}
        aligned_dates.discard(None)
        if outcome is not None:
            outcome_gate_definition = by_key[outcome]
            outcome_gate_rows = [row for row in outcome_rows
                                 if _date_of(row.get("observed_at")) in aligned_dates]
        elif "outcome" in set(_value(definition, "roles", []) or []):
            outcome_gate_definition = definition
            outcome_gate_rows = list(aligned_rows)
        else:
            outcome_gate_definition = None
            outcome_gate_rows = None
        gates = _analysis_gates(
            definition, aligned_rows, aligned_n=aligned_n,
            missing_rate=missing_rate, minimum=minimum,
            outcome_rows=outcome_gate_rows,
            outcome_definition=outcome_gate_definition,
        )
        sparse = bool(gates["gate_failures"])

        # Exact predicate order.  Incidental rows never overrule a registry
        # declaration that logic is not implemented.
        if not implemented:
            state = "logic_not_implemented"
        elif not connected and source_rows_present:
            state = "present_not_connected"
        elif connected and not (config_present if static else observations > 0):
            state = "implemented_never_logged"
        elif stale:
            state = "stale"
        elif sparse:
            state = "too_sparse_for_analysis"
        else:
            state = "sufficient"

        multiplier, highest, eligible_goals = _goal_multiplier(
            definition, enabled_goals, by_key, context,
        )
        value_score = _value(definition, "readiness_value", 0)
        effort = _value(definition, "readiness_effort")
        priority = (round(value_score * multiplier / effort, 3)
                    if effort not in (None, 0) else 0.0)
        provenance = {
            "adapter": adapter_id,
            "adapter_version": statuses.get(adapter_id, {}).get("version"),
            "tables": tables,
            "source": _source(definition),
            "config": config_details if static else None,
        }
        rows.append({
            "feature_key": key,
            "state": state,
            "logic_implemented": implemented,
            "storage_present": storage_present,
            "connected": connected,
            "observations": observations,
            "eligible_units": eligible_units,
            "aligned_n": aligned_n,
            "latest_at": latest_at,
            "missing_rate": missing_rate,
            "stale_after_days": stale_after,
            "prerequisites": _prerequisites(definition, context),
            "needed": _needed(
                definition, state, observations=observations, minimum=minimum,
                stale_days=stale_after, gate_failures=gates["gate_failures"],
                context=context,
            ),
            "unlocks": _unlocks(definition),
            "effort": effort,
            "value": value_score,
            "priority": priority,
            "preferred_rank": _value(definition, "preferred_rank"),
            "factors": {
                "goal_multiplier": multiplier,
                "highest_matching_goal_priority": highest,
                "goal_eligibility": eligible_goals,
                **gates,
                "source_rows_present": source_rows_present,
                "config_present": config_present,
                "measurement_stale": measurement_stale,
                "staleness_anchor": anchor.isoformat(),
                "historical_staleness_basis": historical_query,
                "collector_source": collector_source,
                "collector_freshness": freshness,
                "collector_stale_applied": collector_stale,
                "stale": stale,
            },
            "provenance": provenance,
        })
    rows.sort(key=lambda row: (-row["priority"], row["preferred_rank"], row["feature_key"]))
    return {
        "ok": True,
        "meta": {
            "readiness_version": READINESS_VERSION,
            "registry_version": current["meta"]["registry_version"],
            "registry_sha256": current["meta"]["registry_sha256"],
            "timezone": context.timezone,
            "range": current["meta"]["range"],
            "goal": goal,
            "outcome": outcome,
            "feature_count": len(rows),
            "state_counts": dict(sorted(Counter(row["state"] for row in rows).items())),
            "predicate_order": list(READINESS_STATES),
        },
        "features": rows,
    }


__all__ = ["READINESS_STATES", "READINESS_VERSION", "ReadinessError", "build_readiness"]
