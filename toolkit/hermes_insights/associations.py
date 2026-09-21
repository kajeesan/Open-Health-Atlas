"""Deterministic, registry-driven outcome association analysis.

The module accepts an already-open read-only SQLite connection.  It never
chooses a database path, mutates schema/data, or delegates calculations to a
browser or model.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import math
import re
import sqlite3
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .contracts import (
    AdapterContext,
    DateRange,
    FeatureDefinition,
    REGISTRY_VERSION,
)
from .frame import build_feature_frame
from .provenance import (
    ANALYSIS_CONTRACT_VERSION,
    ProvenanceError,
    candidate_key,
    dependency_source_observations,
    dependency_snapshot,
    engine_sha256,
    evidence_fingerprint,
    finding_id,
    input_fingerprint,
    normalize_row_references,
    source_completeness,
    source_manifests,
)
from .registry import registry_content_checksum
from .readiness import build_readiness
from .stats import (
    ANALYSIS_VERSION,
    BOOTSTRAP_ITERATIONS,
    PERMUTATION_ITERATIONS,
    StatsError,
    _rank_permutation_p_value,
    _spearman_bootstrap_statistic,
    average_ranks,
    benjamini_hochberg,
    bootstrap_interval,
    deterministic_seed,
    fisher_exact_two_sided,
    newcombe_risk_difference_interval,
    percentile,
    spearman,
)


DEFAULT_MIN_N = 30
DEFAULT_TOP = 30
MODE_VALUES = (
    "all",
    "ordinal",
    "green-vs-non-green",
    "red-vs-non-red",
)
INTERACTION_VALUES = ("none", "pairwise")
_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OBSERVED = {"observed", "structural_zero"}
_DayValue = tuple[float | None, list[Mapping[str, Any]], list[str]]
_WindowKey = tuple[date, int, str, str]
_ACTIONABILITY = {
    "direct": 3,
    "indirect": 2,
    "context": 1,
    "non_actionable": 0,
    # Dual-role features such as sleep remain legal exposures, but an
    # outcome-labelled feature receives no actionability preference.
    "outcome": 0,
}
_TIER_ORDER = {
    "replicated": 0,
    "exploratory_unreplicated": 1,
    "exploratory_screen": 2,
    "insufficient": 3,
}
_MEDICAL_PILLARS = {
    "medication", "supplement", "lab", "vitals", "pain", "rehab",
}
_SUPPRESSION_REASONS = (
    "aligned_n",
    "autoregressive_lineage",
    "candidate_disabled",
    "composite_root",
    "exposure_prevalence",
    "exposure_variation",
    "forbidden_relation",
    "high_missingness",
    "interaction_aligned_n",
    "interaction_bootstrap_undefined",
    "interaction_ci_includes_zero",
    "interaction_component_gate",
    "interaction_increment_effect",
    "interaction_ineligible_single",
    "interaction_outcome_class",
    "interaction_q",
    "interaction_same_feature",
    "interaction_sparse_cell",
    "mechanical_tautology",
    "no_observations",
    "outcome_class_gate",
    "outcome_variation",
    "overlapping_running_week",
    "pain_self_derivation",
    "redundant_equivalent",
    "requested_min_n",
    "same_feature",
    "same_session_e1rm",
    "slow_episode_no_generic_alignment",
    "source_specific_hrv",
    "temporal_boundary",
    "timing_unavailable",
    "undefined_statistic",
    "unknown_absence",
    "unsupported_transform",
)


class AssociationError(RuntimeError):
    """Controlled Phase 4 validation or analysis failure."""

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


def _validation(message: str) -> None:
    raise AssociationError("validation_error", message, validation=True)


def validate_options(
    *,
    outcome_key: str,
    mode: str,
    min_n: int,
    interactions: str,
    top: int,
) -> None:
    """Validate caller-controlled scalars before any database operation."""

    if not isinstance(outcome_key, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{0,159}", outcome_key
    ):
        _validation("outcome must be a registered feature key")
    if mode not in MODE_VALUES:
        _validation("mode must be all, ordinal, green-vs-non-green, or red-vs-non-red")
    # Day-rating is the only v1 outcome with the two binary color modes.
    # This key/mode compatibility is code-owned and can therefore fail before
    # a schema check or registry read.  Dynamic registration is still verified
    # against the built registry once the read-only connection is open.
    if (
        outcome_key != "subjective.day_rating"
        and mode not in {"all", "ordinal"}
    ):
        _validation("this outcome supports only ordinal monotonic mode")
    if isinstance(min_n, bool) or not isinstance(min_n, int) or not 30 <= min_n <= 500:
        _validation("min-n must be between 30 and 500")
    if interactions not in INTERACTION_VALUES:
        _validation("interactions must be none or pairwise")
    if isinstance(top, bool) or not isinstance(top, int) or not 1 <= top <= 100:
        _validation("top must be between 1 and 100")


def validate_sha256_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _validation(f"{field} must be sha256 followed by 64 lowercase hexadecimal characters")


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _iso_day(value: object) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return parsed.isoformat()


def _range_dict(value: DateRange, *, include_empty_bounds: bool = False) -> dict[str, Any]:
    if value.kind == "all":
        result: dict[str, Any] = {"kind": "all"}
        if include_empty_bounds:
            result.update({"from": None, "to": None})
        return result
    return {
        "kind": "bounded",
        "from": value.start.isoformat(),
        "to": value.end.isoformat(),
    }


def _definition_map(
    definitions: Iterable[FeatureDefinition] | Mapping[str, FeatureDefinition],
) -> tuple[list[FeatureDefinition], dict[str, FeatureDefinition]]:
    values = list(definitions.values() if isinstance(definitions, Mapping) else definitions)
    by_key = {item.key: item for item in values}
    if len(values) != len(by_key):
        raise AssociationError("registry_contract_error", "duplicate registered feature key")
    return values, by_key


def _validate_outcome(
    by_key: Mapping[str, FeatureDefinition], outcome_key: str, mode: str
) -> tuple[FeatureDefinition, list[str]]:
    outcome = by_key.get(outcome_key)
    if (
        outcome is None
        or "outcome" not in outcome.roles
        or not outcome.implemented
        or not outcome.candidate_enabled
    ):
        _validation("outcome is not an implemented registered outcome")
    if outcome_key.startswith("pain.nrs.lateral-knee."):
        raise AssociationError(
            "logic_not_implemented",
            "lateral-knee outcome logic is not implemented",
            validation=True,
        )
    if outcome_key == "subjective.day_rating":
        modes = [
            "ordinal",
            "green-vs-non-green",
            "red-vs-non-red",
        ] if mode == "all" else [mode]
    else:
        if mode not in {"all", "ordinal"}:
            _validation("this outcome supports only ordinal monotonic mode")
        modes = ["ordinal"]
    return outcome, modes


def _query_goal_revision(
    conn: sqlite3.Connection, outcome_key: str
) -> tuple[int, str] | None:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='insight_goal_revisions'"
    ).fetchone()
    if table is None:
        return None
    row = conn.execute(
        """SELECT id,target_direction FROM insight_goal_revisions r
           WHERE enabled=1 AND outcome_key=? AND NOT EXISTS (
             SELECT 1 FROM insight_goal_revisions n
             WHERE n.goal_key=r.goal_key AND n.id>r.id)
           ORDER BY priority DESC,id DESC LIMIT 1""",
        (outcome_key,),
    ).fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return int(row["id"]), str(row["target_direction"])
    return int(row[0]), str(row[1])


def _query_goal_direction(
    conn: sqlite3.Connection, outcome_key: str
) -> str | None:
    revision = _query_goal_revision(conn, outcome_key)
    return revision[1] if revision is not None else None


def _orientation(
    conn: sqlite3.Connection, outcome: FeatureDefinition, mode: str
) -> int:
    if mode == "red-vs-non-red":
        return -1
    if mode in {"green-vs-non-green"}:
        return 1
    if outcome.direction == "higher_better":
        return 1
    if outcome.direction == "lower_better":
        return -1
    goal_direction = _query_goal_direction(conn, outcome.key)
    if goal_direction == "increase":
        return 1
    if goal_direction == "decrease":
        return -1
    raise AssociationError(
        "outcome_direction_unconfigured",
        "neutral or target-range outcome requires an enabled explicit goal direction",
        validation=True,
    )


def _outcome_group(key: str) -> str:
    if key.startswith("subjective."):
        return "subjective"
    if key.startswith("adherence."):
        return "adherence"
    if key.startswith("pain."):
        return "pain"
    if key.startswith("sleep."):
        return "sleep"
    if key.startswith("recovery."):
        return "recovery"
    if key.startswith("training."):
        return "training"
    if key.startswith("running."):
        return "running"
    if key.startswith("body."):
        return "body"
    if key.startswith("fitness."):
        return "fitness"
    if key.startswith("vitals."):
        return "vitals"
    return key.split(".", 1)[0]


_ALLOWED_GROUPS: dict[str, set[str]] = {
    "wearable": {
        "subjective", "adherence", "pain", "sleep", "recovery",
        "training", "running", "body",
    },
    "sleep": {
        "subjective", "adherence", "pain", "sleep", "recovery",
        "training", "running", "body",
    },
    "medication": {"subjective", "sleep", "recovery", "pain", "vitals"},
    "supplement": {"subjective", "sleep", "recovery", "pain", "vitals"},
    "training": {
        "subjective", "adherence", "sleep", "recovery", "pain",
        "training", "running", "body", "fitness",
    },
    "cardio": {
        "subjective", "adherence", "sleep", "recovery", "pain",
        "running", "body", "fitness",
    },
    "running": {
        "subjective", "adherence", "sleep", "recovery", "pain",
        "running", "body", "fitness",
    },
    "nutrition": {
        "subjective", "adherence", "sleep", "recovery", "pain",
        "training", "running", "body",
    },
    "food": {
        "subjective", "adherence", "sleep", "recovery", "pain",
        "training", "running", "body",
    },
    "meal": {
        "subjective", "adherence", "sleep", "recovery", "pain",
        "training", "running", "body",
    },
    "body": {"pain", "body", "fitness", "training", "running"},
    "fitness": {"pain", "body", "fitness", "training", "running"},
    "adherence": {
        "subjective", "sleep", "recovery", "pain", "adherence",
        "body", "training", "running",
    },
    "skincare": {"subjective"},
    "substance": {
        "subjective", "adherence", "sleep", "recovery", "pain",
        "training", "running", "body",
    },
}


def _relation_allowed(exposure: FeatureDefinition, outcome: FeatureDefinition) -> bool:
    group = _outcome_group(outcome.key)
    if exposure.pillar in {"calendar", "weather", "air", "source"}:
        return True
    if exposure.pillar == "event":
        return group not in {"pain", "vitals"}
    if exposure.pillar in {"pain", "rehab", "lab", "assessment"}:
        return False
    return group in _ALLOWED_GROUPS.get(exposure.pillar, set())


def _lineage_reason(
    exposure: FeatureDefinition, outcome: FeatureDefinition
) -> str | None:
    if exposure.key == outcome.key:
        return "same_feature"
    # Cross-algorithm HRV and pain-timeline relations are forbidden
    # independently of date alignment.  Other shared-root/composite relations
    # must wait until rows are aligned: Appendix B distinguishes overlapping
    # mechanical reuse from strictly prior, context-only lineage.
    if outcome.key.startswith("recovery.hrv_score.") and exposure.key.startswith(
        "recovery.hrv_score."
    ):
        return "source_specific_hrv"
    if outcome.key.startswith("pain.") and exposure.pillar in {"pain", "rehab"}:
        return "pain_self_derivation"
    return None


def _component_lineage_reason(
    exposure: FeatureDefinition,
    outcome: FeatureDefinition,
    component: Mapping[str, Any],
) -> str | None:
    """Return a component-level prohibition knowable before row alignment."""

    lag = int(component["lag_days"])
    if (
        outcome.key.startswith("training.exercise.")
        and outcome.key.endswith(".best_e1rm_kg")
        and exposure.pillar == "training"
        and lag == 0
    ):
        return "same_session_e1rm"
    return None


def _observation_lineage_parts(
    observations: Sequence[Mapping[str, Any]],
) -> tuple[set[str], set[str]]:
    """Return exact source-row keys and represented dates for overlap checks."""

    natural_keys: set[str] = set()
    dates: set[str] = set()
    for observation in observations:
        observed_day = _iso_day(observation.get("observed_at"))
        if observed_day is not None:
            dates.add(observed_day)
        provenance = observation.get("provenance") or {}
        for field in ("natural_key",):
            value = provenance.get(field)
            if isinstance(value, str) and value:
                natural_keys.add(value)
        for field in ("natural_keys", "parent_natural_keys"):
            values = provenance.get(field) or ()
            if isinstance(values, (str, bytes)):
                continue
            natural_keys.update(
                value for value in values
                if isinstance(value, str) and value
            )
    return natural_keys, dates


def _shared_lineage_overlap(
    rows: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether any aligned unit reuses a source row or represented date."""

    for row in rows:
        outcome_keys, outcome_dates = _observation_lineage_parts(
            row.get("outcome_observations", ())
        )
        exposure_keys, exposure_dates = _observation_lineage_parts(
            row.get("exposure_observations", ())
        )
        if outcome_keys & exposure_keys or outcome_dates & exposure_dates:
            return True
    return False


def _overlapping_lineage_reason(
    exposure: FeatureDefinition,
    outcome: FeatureDefinition,
) -> str:
    """Preserve named composite accounting once actual overlap is proven."""

    shared = set(exposure.lineage_roots) & set(outcome.lineage_roots)
    if shared and (
        (
            outcome.key.startswith("recovery.")
            and (
                exposure.key.startswith("recovery.")
                or any(
                    root.startswith((
                        "hrv.", "daily_metrics.resting_hr", "recovery.rhr",
                    ))
                    for root in exposure.lineage_roots
                )
            )
        )
        or (
            outcome.key == "body.wcr"
            and exposure.key in {"body.waist_cm", "body.chest_cm"}
        )
        or outcome.key.startswith((
            "fitness.ratio.", "fitness.athletic_axis.",
        ))
    ):
        return "composite_root"
    return "mechanical_tautology"


def _candidate_lineage_status(
    exposure: FeatureDefinition,
    outcome: FeatureDefinition,
    rows: Sequence[Mapping[str, Any]],
) -> str | None:
    """Classify shared lineage using actual aligned source rows/dates."""

    if not set(exposure.lineage_roots) & set(outcome.lineage_roots):
        return None
    if _shared_lineage_overlap(rows):
        return _overlapping_lineage_reason(exposure, outcome)
    return "autoregressive_lineage"


def _profile(definition: FeatureDefinition) -> str:
    lags = tuple(definition.lag_eligibility.get("lags", ()))
    windows = tuple(definition.lag_eligibility.get("windows", ()))
    signatures = {
        ((0, 1, 2, 3, 7), ("point", 3, 7, 28)): "state",
        ((0, 1, 2), ("point", 3, 7, 28)): "short",
        ((0, 1, 2, 3, 7), (1, 3, 7, 28)): "event",
        ((0, 1, 2, 3, 7), (1, 3, 7, 28, 90, 365)): "load",
        ((0, 1, 2, 3), ("point", 3, 7, 28)): "environment",
        ((0,), ("point",)): "timed",
        ((), ()): "none",
    }
    return signatures.get((lags, windows), "unsupported")


def _window_transform(definition: FeatureDefinition, profile: str) -> str:
    if profile in {"state", "environment"}:
        if definition.aggregation_id in {"sum", "daily_sum"} or (
            profile == "environment"
            and any(
                token in definition.key
                for token in (
                    "precip", "rain_", "snow", "sunshine_duration",
                    "sunshine_hours",
                )
            )
        ):
            return "sum"
        return "mean"
    if profile == "short":
        if definition.value_kind in {"binary", "count"}:
            return "count"
        if definition.value_kind == "time_minutes":
            return "mean"
        return "sum" if definition.temporal_type == "event_occurrence" else "mean"
    if profile == "event":
        if definition.value_kind in {"binary", "count", "integer"}:
            return "count"
        return "sum"
    if profile == "load":
        if definition.value_kind in {"binary", "count"} or definition.key.endswith(
            (".session", ".occurrence")
        ):
            return "count"
        return "sum"
    return "unsupported"


def _components_for(definition: FeatureDefinition) -> list[dict[str, Any]]:
    profile = _profile(definition)
    if profile in {"none", "unsupported"}:
        return []
    result: list[dict[str, Any]] = []
    for lag in definition.lag_eligibility["lags"]:
        for window in definition.lag_eligibility["windows"]:
            if window == "point":
                result.append({
                    "exposure_key": definition.key,
                    "lag_days": int(lag),
                    "window_days": 1,
                    "transform": "point",
                })
                continue
            transform = _window_transform(definition, profile)
            result.append({
                "exposure_key": definition.key,
                "lag_days": int(lag),
                "window_days": int(window),
                "transform": transform,
            })
            if profile == "load":
                if transform in {"sum", "count"}:
                    result.append({
                        "exposure_key": definition.key,
                        "lag_days": int(lag),
                        "window_days": int(window),
                        "transform": "delta_per_day",
                    })
                result.append({
                    "exposure_key": definition.key,
                    "lag_days": int(lag),
                    "window_days": int(window),
                    "transform": "frequency_per_week",
                })
                result.append({
                    "exposure_key": definition.key,
                    "lag_days": int(lag),
                    "window_days": int(window),
                    "transform": "days_since",
                })
    return result


def _component_lookback_days(component: Mapping[str, Any]) -> int:
    """Return the finite baseline needed by one transformed component."""

    lag = int(component["lag_days"])
    width = int(component["window_days"])
    # A delta compares the current W dates with the immediately preceding W
    # dates.  All other finite transforms consume only their declared W dates.
    span = 2 * width if component["transform"] == "delta_per_day" else width
    return lag + span - 1


def _strict_boundary(
    outcome_key: str,
    component: Mapping[str, Any],
    exposure: FeatureDefinition,
) -> str | None:
    lag = int(component["lag_days"])
    if (
        outcome_key.startswith("subjective.checkin.")
        and lag == 0
        and (exposure.pillar in {"medication", "event"} or _profile(exposure) == "timed")
    ):
        return "timing_unavailable"
    if outcome_key.startswith(
        ("pain.", "sleep.", "fitness.", "body.", "training.exercise.")
    ) and lag == 0:
        return "temporal_boundary"
    if outcome_key.startswith("running.progress.") and lag < 7:
        return "overlapping_running_week"
    return None


def _by_feature(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, list[Mapping[str, Any]]]]:
    result: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in observations:
        day = _iso_day(row.get("observed_at"))
        if day:
            result[str(row["feature_key"])][day].append(row)
    for days in result.values():
        for rows in days.values():
            rows.sort(key=lambda row: (
                str(row.get("source") or ""),
                str((row.get("provenance") or {}).get("natural_key") or ""),
            ))
    return result


def _enrich_observations(
    observations: Sequence[Mapping[str, Any]],
    definitions: Mapping[str, FeatureDefinition],
) -> list[dict[str, Any]]:
    """Attach registry-owned source semantics used by audit manifests."""

    result: list[dict[str, Any]] = []
    for row in observations:
        definition = definitions.get(str(row["feature_key"]))
        provenance_value = row.get("provenance")
        if definition is not None and provenance_value:
            provenance_value = {
                "adapter": definition.adapter,
                "table": definition.source.get("table"),
                "merge": definition.source.get("merge"),
                **provenance_value,
            }
        result.append({**row, "provenance": provenance_value})
    return result


def _one_day_value(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[float | None, list[Mapping[str, Any]], list[str]]:
    usable = [
        row for row in rows
        if row.get("state") in _OBSERVED
        and (row.get("provenance") or {}).get("candidate_eligible", True) is not False
        and isinstance(row.get("value"), (int, float))
        and not isinstance(row.get("value"), bool)
        and math.isfinite(float(row["value"]))
    ]
    if not usable:
        return None, list(rows), [
            (
                "candidate_ineligible"
                if (row.get("provenance") or {}).get("candidate_eligible", True)
                is False
                else str(row.get("state"))
            )
            for row in rows
        ]
    values = {float(row["value"]) for row in usable}
    if len(values) != 1:
        return None, list(usable), ["duplicate_conflict"]
    return values.pop(), list(usable), [str(row.get("state")) for row in usable]


def _date_span(end: date, days: int) -> list[str]:
    start = end - timedelta(days=days - 1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]


def _day_value(
    days: Mapping[str, list[Mapping[str, Any]]],
    day: str,
    day_values: dict[str, _DayValue] | None,
) -> tuple[float | None, list[Mapping[str, Any]], list[str]]:
    if day_values is None:
        return _one_day_value(days.get(day, ()))
    if day not in day_values:
        day_values[day] = _one_day_value(days.get(day, ()))
    return day_values[day]


def _aggregate_window(
    days: Mapping[str, list[Mapping[str, Any]]],
    end: date,
    width: int,
    transform: str,
    profile: str,
    *,
    day_values: dict[str, _DayValue] | None = None,
    date_spans: dict[tuple[date, int], list[str]] | None = None,
) -> tuple[float | None, list[Mapping[str, Any]], list[str]]:
    if date_spans is None:
        dates = _date_span(end, width)
    else:
        span_key = (end, width)
        if span_key not in date_spans:
            date_spans[span_key] = _date_span(end, width)
        dates = date_spans[span_key]
    values: list[float] = []
    references: list[Mapping[str, Any]] = []
    states: list[str] = []
    for day in dates:
        value, refs, day_states = _day_value(days, day, day_values)
        references.extend(refs)
        states.extend(day_states or ["missing"])
        if value is not None:
            values.append(value)
    strict_complete = profile in {"event", "load"} or transform in {
        "sum", "count", "delta_per_day", "frequency_per_week"
    }
    if strict_complete and len(values) != width:
        return None, references, states
    if not strict_complete and len(values) < math.ceil(width / 2):
        return None, references, states
    if transform == "mean":
        return mean(values), references, states
    if transform in {"sum", "count"}:
        return sum(values), references, states
    if transform == "frequency_per_week":
        return sum(value > 0 for value in values) * 7.0 / width, references, states
    return None, references, states


def _component_value(
    definition: FeatureDefinition,
    component: Mapping[str, Any],
    outcome_day: str,
    days: Mapping[str, list[Mapping[str, Any]]],
    *,
    day_values: dict[str, _DayValue] | None = None,
    date_spans: dict[tuple[date, int], list[str]] | None = None,
    window_values: dict[_WindowKey, _DayValue] | None = None,
) -> tuple[float | None, list[Mapping[str, Any]], list[str]]:
    lag = int(component["lag_days"])
    width = int(component["window_days"])
    transform = str(component["transform"])
    end = date.fromisoformat(outcome_day) - timedelta(days=lag)
    if transform == "point":
        value, refs, states = _day_value(days, end.isoformat(), day_values)
        return value, list(refs), list(states)
    profile = _profile(definition)
    if transform == "days_since":
        references: list[Mapping[str, Any]] = []
        states: list[str] = []
        for day in sorted(days, reverse=True):
            parsed = date.fromisoformat(day)
            if parsed > end:
                continue
            value, refs, row_states = _day_value(days, day, day_values)
            references.extend(refs)
            states.extend(row_states)
            if value is not None and value > 0:
                return float((end - parsed).days), references, states
        return None, references, states
    if transform == "delta_per_day":
        current, refs_a, states_a = _window_value(
            days, end, width, "sum", profile,
            day_values=day_values, date_spans=date_spans, window_values=window_values,
        )
        previous, refs_b, states_b = _window_value(
            days, end - timedelta(days=width), width, "sum", profile,
            day_values=day_values, date_spans=date_spans, window_values=window_values,
        )
        if current is None or previous is None:
            return None, refs_a + refs_b, states_a + states_b
        return (current - previous) / width, refs_a + refs_b, states_a + states_b
    return _window_value(
        days, end, width, transform, profile,
        day_values=day_values, date_spans=date_spans, window_values=window_values,
    )


def _window_value(
    days: Mapping[str, list[Mapping[str, Any]]],
    end: date,
    width: int,
    transform: str,
    profile: str,
    *,
    day_values: dict[str, _DayValue] | None,
    date_spans: dict[tuple[date, int], list[str]] | None,
    window_values: dict[_WindowKey, _DayValue] | None,
) -> _DayValue:
    if window_values is None:
        return _aggregate_window(
            days, end, width, transform, profile,
            day_values=day_values, date_spans=date_spans,
        )
    key = (end, width, transform, profile)
    if key not in window_values:
        window_values[key] = _aggregate_window(
            days, end, width, transform, profile,
            day_values=day_values, date_spans=date_spans,
        )
    value, refs, states = window_values[key]
    return value, list(refs), list(states)


def _outcome_rows(
    by_feature: Mapping[str, Mapping[str, list[Mapping[str, Any]]]],
    outcome_key: str,
    analysis_range: DateRange,
    outcome_definition: FeatureDefinition | None = None,
) -> list[dict[str, Any]]:
    result = []
    for day, rows in sorted(by_feature.get(outcome_key, {}).items()):
        if analysis_range.kind == "bounded" and not (
            analysis_range.start.isoformat() <= day <= analysis_range.end.isoformat()
        ):
            continue
        slow = bool(
            outcome_definition is not None
            and (
                outcome_definition.temporal_type == "slow_measurement"
                or outcome_definition.completeness_profile == "SLOW_EPISODE"
            )
        )
        units: list[tuple[str, float, list[Mapping[str, Any]], list[str]]] = []
        if slow:
            seen: set[str] = set()
            for index, row in enumerate(rows):
                value, refs, states = _one_day_value([row])
                if value is None:
                    continue
                provenance = row.get("provenance") or {}
                natural_key = provenance.get("natural_key")
                unit_key = (
                    str(natural_key)
                    if isinstance(natural_key, str) and natural_key
                    else f"{row.get('observed_at')}|{row.get('source')}|{index}"
                )
                if unit_key in seen:
                    continue
                seen.add(unit_key)
                units.append((unit_key, value, refs, states))
        else:
            value, refs, states = _one_day_value(rows)
            if value is not None:
                units.append((day, value, refs, states))
        for unit_key, value, refs, states in units:
            result.append({
                "unit_key": unit_key,
                "date": day,
                "value": value,
                "observations": refs,
                "states": states,
                "source": "|".join(sorted({
                    str(row.get("source") or "unknown") for row in refs
                })),
            })
    return result


def _mode_value(value: float, mode: str) -> float:
    if mode == "green-vs-non-green":
        return float(value == 3)
    if mode == "red-vs-non-red":
        return float(value == 1)
    return value


def _aligned_candidate(
    outcome_rows: Sequence[Mapping[str, Any]],
    exposure: FeatureDefinition,
    component: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, list[Mapping[str, Any]]]],
    mode: str,
    *,
    day_values: dict[str, _DayValue] | None = None,
    date_spans: dict[tuple[date, int], list[str]] | None = None,
    window_values: dict[_WindowKey, _DayValue] | None = None,
) -> dict[str, Any]:
    aligned = []
    consulted: list[Mapping[str, Any]] = []
    exposure_days = observations.get(exposure.key, {})
    for outcome_row in outcome_rows:
        consulted.extend(outcome_row["observations"])
        value, refs, states = _component_value(
            exposure, component, str(outcome_row["date"]), exposure_days,
            day_values=day_values, date_spans=date_spans, window_values=window_values,
        )
        consulted.extend(refs)
        if value is None or not math.isfinite(value):
            continue
        sources = sorted({str(row.get("source") or "unknown") for row in refs})
        aligned.append({
            "unit_key": outcome_row["unit_key"],
            "date": outcome_row["date"],
            "outcome": _mode_value(float(outcome_row["value"]), mode),
            "outcome_raw": float(outcome_row["value"]),
            "exposure": float(value),
            "exposure_states": states,
            "outcome_observations": list(outcome_row["observations"]),
            "exposure_observations": refs,
            "source_era": (
                f"outcome:{outcome_row['source']}|exposure:{'|'.join(sources) or 'unknown'}"
            ),
        })
    return {
        "component": dict(component),
        "definition": exposure,
        "eligible_n": len(outcome_rows),
        "rows": aligned,
        "complete_n": len(aligned),
        "missing_n": len(outcome_rows) - len(aligned),
        "missing_rate": (
            (len(outcome_rows) - len(aligned)) / len(outcome_rows)
            if outcome_rows else 1.0
        ),
        "_input_observations": consulted,
    }


def _iqr(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return percentile(ordered, 0.75) - percentile(ordered, 0.25)


def _binary(values: Sequence[float]) -> bool:
    return bool(values) and all(value in (0.0, 1.0) for value in values)


def _mode_gate(
    rows: Sequence[Mapping[str, Any]],
    mode: str,
    outcome_key: str | None = None,
) -> str | None:
    values = [float(row["outcome"]) for row in rows]
    if mode == "ordinal":
        raw = [float(row["outcome_raw"]) for row in rows]
        if outcome_key == "subjective.day_rating":
            counts = Counter(raw)
            if any(counts[level] < 5 for level in (1.0, 2.0, 3.0)):
                return "outcome_class_gate"
        elif _binary(raw):
            if sum(raw) < 10 or len(raw) - sum(raw) < 10:
                return "outcome_class_gate"
        elif len(set(values)) < 2:
            return "outcome_variation"
    elif sum(values) < 10 or len(values) - sum(values) < 10:
        return "outcome_class_gate"
    return None


def _candidate_is_binary(candidate: Mapping[str, Any]) -> bool:
    definition = candidate.get("definition")
    values = [float(row["exposure"]) for row in candidate["rows"]]
    value_kind = getattr(definition, "value_kind", None)
    return bool(
        definition is None
        or value_kind is None
        or value_kind in {"binary", "count"}
    ) and _binary(values)


def _known_unexposed(
    row: Mapping[str, Any],
    definition: FeatureDefinition | None,
) -> bool:
    states = list(row.get("exposure_states") or ())
    if not states:
        return False
    semantics = getattr(definition, "zero_semantics", None)
    if semantics == "structural_zero_if_complete":
        # An interval can be fully known through a mixture of direct observed
        # zeros (for example a warmup-only training day) and completeness-backed
        # structural zeros.  Missing/ineligible dates still fail closed.
        return all(state in _OBSERVED for state in states) and bool(
            row.get("exposure_observations")
        )
    if semantics == "explicit_binary":
        return all(state in _OBSERVED for state in states) and bool(
            row.get("exposure_observations")
        )
    if semantics in {"valid", "derived_strict"}:
        return all(state in _OBSERVED for state in states)
    # Test fixtures without registry metadata still have to prove the zero
    # explicitly or through a completeness-backed structural zero.
    if definition is None:
        return all(state in _OBSERVED for state in states)
    return False


def _base_gate(
    candidate: Mapping[str, Any],
    mode: str,
    min_n: int,
    outcome_key: str | None = None,
) -> str | None:
    rows = candidate["rows"]
    if len(rows) < min_n:
        return "aligned_n"
    if candidate["missing_rate"] > 0.50:
        return "high_missingness"
    outcome_gate = _mode_gate(rows, mode, outcome_key)
    if outcome_gate:
        return outcome_gate
    values = [float(row["exposure"]) for row in rows]
    if _candidate_is_binary(candidate):
        exposed = sum(value == 1 for value in values)
        unexposed = len(values) - exposed
        if exposed < 10 or unexposed < 10:
            return "exposure_prevalence"
        for row in rows:
            if row["exposure"] == 0 and not _known_unexposed(
                row, candidate.get("definition")
            ):
                return "unknown_absence"
    elif len(set(values)) < 5 or _iqr(values) == 0:
        return "exposure_variation"
    return None


def _risk_effect(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    exposed = [row for row in rows if row["exposure"] == 1]
    unexposed = [row for row in rows if row["exposure"] == 0]
    ep = sum(int(row["outcome"] == 1) for row in exposed)
    up = sum(int(row["outcome"] == 1) for row in unexposed)
    en, un = len(exposed), len(unexposed)
    exposed_rate = ep / en
    unexposed_rate = up / un
    baseline = (ep + up) / (en + un)
    # Keep exact count arithmetic until division so an inclusive .10 gate
    # does not reject 18/20 - 16/20 after floating-point cancellation.
    rd = (ep * un - up * en) / (en * un)
    rr = exposed_rate / unexposed_rate if unexposed_rate > 0 else None
    p = fisher_exact_two_sided(((ep, en - ep), (up, un - up)))
    ci = newcombe_risk_difference_interval(ep, en, up, un)
    return {
        "method": "risk_difference",
        "estimate": rd,
        "p": p,
        "ci95": list(ci),
        "rates": {
            "baseline": baseline,
            "exposed": exposed_rate,
            "unexposed": unexposed_rate,
            "risk_difference": rd,
            "risk_ratio": rr,
        },
        "sample_extra": {
            "exposed_n": en,
            "unexposed_n": un,
            "outcome_positive_n": ep + up,
            "outcome_negative_n": en + un - ep - up,
        },
        "test_method": "fisher_exact",
    }


def _rho_effect(
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int,
) -> dict[str, Any]:
    pairs = [(float(row["exposure"]), float(row["outcome"])) for row in rows]
    estimate = spearman(
        [pair[0] for pair in pairs],
        [pair[1] for pair in pairs],
    )
    if estimate is None:
        raise StatsError("spearman is undefined")

    exposure_ranks = average_ranks([pair[0] for pair in pairs])
    outcome_ranks = average_ranks([pair[1] for pair in pairs])
    p = _rank_permutation_p_value(
        exposure_ranks,
        outcome_ranks,
        seed=seed,
    )
    return {
        "method": "spearman",
        "estimate": estimate,
        "p": p,
        # Bootstrap is attached only to findings that survive deterministic
        # output ranking.  Every candidate is still tested and enters BH; this
        # avoids spending 2,000 resamples on findings the bounded response does
        # not return.
        "ci95": None,
        "rates": None,
        "sample_extra": {
            "exposed_n": None,
            "unexposed_n": None,
            "outcome_positive_n": (
                int(sum(pair[1] == 1 for pair in pairs)) if _binary([p[1] for p in pairs])
                else None
            ),
            "outcome_negative_n": (
                int(sum(pair[1] == 0 for pair in pairs)) if _binary([p[1] for p in pairs])
                else None
            ),
        },
        "test_method": "spearman_permutation",
    }


def _calculate_effect(
    candidate: Mapping[str, Any],
    *,
    seed: int,
    outcome_binary: bool,
) -> dict[str, Any]:
    rows = candidate["rows"]
    exposure = [float(row["exposure"]) for row in rows]
    outcome = [float(row["outcome"]) for row in rows]
    if _candidate_is_binary(candidate) and outcome_binary and _binary(outcome):
        return _risk_effect(rows)
    return _rho_effect(rows, seed=seed)


def _half_effect(
    rows: Sequence[Mapping[str, Any]],
    method: str,
) -> float | None:
    if method == "risk_difference":
        exposure = [float(row["exposure"]) for row in rows]
        outcome = [float(row["outcome"]) for row in rows]
        if not (_binary(exposure) and _binary(outcome)):
            return None
        cells = Counter(zip(exposure, outcome))
        if any(
            cells[(exposure_value, outcome_value)] < 5
            for exposure_value in (0.0, 1.0)
            for outcome_value in (0.0, 1.0)
        ):
            return None
        return float(_risk_effect(rows)["estimate"])
    return spearman(
        [float(row["exposure"]) for row in rows],
        [float(row["outcome"]) for row in rows],
    )


def _stability(
    rows: Sequence[Mapping[str, Any]],
    method: str,
    estimate: float,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["date"], row["unit_key"]))
    half = len(ordered) // 2
    first = ordered[:half]
    second = ordered[half:]
    if len(first) < 15 or len(second) < 15:
        return {
            "status": "insufficient",
            "full": estimate,
            "first_half": None,
            "second_half": None,
        }
    first_effect = _half_effect(first, method)
    second_effect = _half_effect(second, method)
    if first_effect is None or second_effect is None or estimate == 0:
        status = "insufficient"
    else:
        sign = 1 if estimate > 0 else -1
        status = "stable" if (
            first_effect * sign > 0
            and second_effect * sign > 0
            and abs(first_effect) >= 0.5 * abs(estimate)
            and abs(second_effect) >= 0.5 * abs(estimate)
        ) else "unstable"
    return {
        "status": status,
        "full": estimate,
        "first_half": first_effect,
        "second_half": second_effect,
    }


def _series_rho(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[int, float | None]:
    a = {row["unit_key"]: row["exposure"] for row in left["rows"]}
    b = {row["unit_key"]: row["exposure"] for row in right["rows"]}
    keys = sorted(set(a) & set(b))
    return len(keys), spearman([a[key] for key in keys], [b[key] for key in keys])


def _redundancy_filter(
    candidates: Sequence[dict[str, Any]],
    suppression: Counter[str],
) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: (
        -_ACTIONABILITY.get(item["definition"].actionability, 0),
        item["definition"].preferred_rank,
        item["definition"].key,
        item["component"]["lag_days"],
        item["component"]["window_days"],
        item["component"]["transform"],
    ))
    retained: list[dict[str, Any]] = []
    for candidate in ordered:
        group = candidate["definition"].redundancy_group
        redundant = False
        if group:
            for prior in retained:
                if prior["definition"].redundancy_group != group:
                    continue
                overlap, rho = _series_rho(candidate, prior)
                if overlap >= 30 and rho is not None and abs(rho) >= 0.90:
                    redundant = True
                    break
        if redundant:
            suppression["redundant_equivalent"] += 1
        else:
            retained.append(candidate)
    return retained


def _same_day_value(
    observations: Mapping[str, Mapping[str, list[Mapping[str, Any]]]],
    keys: Sequence[str],
    day: str,
) -> str | None:
    found: list[str] = []
    for key in keys:
        rows = observations.get(key, {}).get(day, ())
        value, _, _ = _one_day_value(rows)
        if value is not None:
            found.append(str(value))
            continue
        categorical = {
            str(row["value"])
            for row in rows
            if row.get("state") in _OBSERVED
            and isinstance(row.get("value"), str)
            and row["value"]
        }
        if len(categorical) == 1:
            found.append(categorical.pop())
    return "|".join(sorted(found)) if found else None


def _confounder_labels(
    rows: Sequence[Mapping[str, Any]],
    observations: Mapping[str, Mapping[str, list[Mapping[str, Any]]]],
) -> dict[str, list[str | None]]:
    event_keys = {
        "illness": [key for key in observations if key.startswith("event.illness.") and key.endswith(".occurred")],
        "travel": [key for key in observations if key.startswith("event.travel.") and key.endswith(".occurred")],
        "training_phase": [
            key for key in observations
            if key.startswith("event.training_phase.") and key.endswith(".occurred")
        ],
        "medication_regime": [
            key for key in observations
            if key.startswith("medication.") and key.endswith(".regime")
        ],
    }
    result: dict[str, list[str | None]] = {
        "weekend": [],
        "calendar_quarter": [],
        "illness": [],
        "travel": [],
        "medication_regime": [],
        "training_phase": [],
        "source_era": [],
        "source_transition": [],
    }
    prior_era: str | None = None
    source_segment = 0
    for row in rows:
        parsed = date.fromisoformat(str(row["date"]))
        era = str(row.get("source_era") or "unknown")
        result["weekend"].append("weekend" if parsed.weekday() >= 5 else "weekday")
        result["calendar_quarter"].append(f"Q{(parsed.month - 1) // 3 + 1}")
        for name in ("illness", "travel", "medication_regime", "training_phase"):
            result[name].append(_same_day_value(
                observations, event_keys[name], str(row["date"])
            ))
        result["source_era"].append(era)
        if prior_era is not None and era != prior_era:
            source_segment += 1
        result["source_transition"].append(f"source_segment_{source_segment}")
        prior_era = era
    return result


def _stratified_effect(
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[str | None],
    method: str,
) -> tuple[float | None, dict[str, int]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row, label in zip(rows, labels):
        if label is not None:
            grouped[label].append(row)
    counts = {key: len(value) for key, value in sorted(grouped.items())}
    if len(grouped) < 2 or any(len(value) < 10 for value in grouped.values()):
        return None, counts
    effects: list[tuple[int, float]] = []
    for group_rows in grouped.values():
        if method == "risk_difference":
            exposure = [float(row["exposure"]) for row in group_rows]
            outcome = [float(row["outcome"]) for row in group_rows]
            if not (
                _binary(exposure)
                and _binary(outcome)
                and any(value == 0 for value in exposure)
                and any(value == 1 for value in exposure)
            ):
                return None, counts
            effect = float(_risk_effect(group_rows)["estimate"])
        else:
            effect = spearman(
                [float(row["exposure"]) for row in group_rows],
                [float(row["outcome"]) for row in group_rows],
            )
        if effect is None:
            return None, counts
        effects.append((len(group_rows), effect))
    total = sum(item[0] for item in effects)
    return sum(n * effect for n, effect in effects) / total, counts


def _confounders(
    rows: Sequence[Mapping[str, Any]],
    observations: Mapping[str, Mapping[str, list[Mapping[str, Any]]]],
    method: str,
    oriented_effect: float,
    orientation: int,
) -> dict[str, Any]:
    checked = []
    unchecked = []
    sensitive_to = []
    labels = _confounder_labels(rows, observations)
    for name in (
        "weekend", "calendar_quarter", "illness", "travel",
        "medication_regime", "training_phase", "source_era", "source_transition",
    ):
        weighted_raw, counts = _stratified_effect(rows, labels[name], method)
        if weighted_raw is None:
            reason = (
                "fewer_than_two_strata"
                if len(counts) < 2
                else "stratum_below_10_or_effect_undefined"
            )
            unchecked.append({"key": name, "reason": reason})
            continue
        weighted = weighted_raw * orientation
        sensitive = (
            (oriented_effect != 0 and weighted * oriented_effect < 0)
            or abs(weighted) < 0.60 * abs(oriented_effect)
        )
        checked.append({
            "key": name,
            "strata": counts,
            "weighted_effect": weighted,
            "sensitive": sensitive,
        })
        if sensitive:
            sensitive_to.append(name)
    weighted_effect = (
        min((item["weighted_effect"] for item in checked), key=abs)
        if checked else None
    )
    return {
        "checked": checked,
        "unchecked": unchecked,
        "sensitive_to": sensitive_to,
        "weighted_effect": weighted_effect,
    }


def _evidence(
    *,
    min_n: int,
    complete_n: int,
    effect: float,
    effect_threshold: float,
    q: float,
    stability: Mapping[str, Any],
    missing_rate: float,
    confounders: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence_for = [
        {"code": "sample_gate_pass", "value": complete_n, "threshold": min_n},
        {"code": "variation_gate_pass", "value": True, "threshold": True},
    ]
    evidence_against: list[dict[str, Any]] = []
    if abs(effect) >= effect_threshold:
        evidence_for.append({
            "code": "effect_gate_pass",
            "value": effect,
            "threshold": effect_threshold,
        })
    else:
        evidence_against.append({
            "code": "effect_below_threshold",
            "value": effect,
            "threshold": effect_threshold,
        })
    if q <= 0.10:
        evidence_for.append({
            "code": "multiplicity_gate_pass",
            "value": q,
            "threshold": 0.10,
        })
    else:
        evidence_against.append({
            "code": "multiplicity_gate_fail",
            "value": q,
            "threshold": 0.10,
        })
    if stability["status"] == "stable":
        evidence_for.append({
            "code": "chronological_halves_same_sign",
            "value": True,
            "threshold": True,
        })
    else:
        evidence_against.append({
            "code": "chronological_halves_fail",
            "value": stability["status"],
            "threshold": "stable",
        })
    if missing_rate > 0.30:
        evidence_against.append({
            "code": "material_missingness",
            "value": missing_rate,
            "threshold": 0.30,
        })
    for key in confounders["sensitive_to"]:
        item = next(value for value in confounders["checked"] if value["key"] == key)
        evidence_against.append({
            "code": "confounder_attenuation",
            "value": item["weighted_effect"],
            "threshold": 0.60 * abs(effect),
            "detail": key,
        })
    for item in confounders["unchecked"]:
        evidence_against.append({
            "code": "unchecked_confounder",
            "value": item["reason"],
            "threshold": "checked",
            "detail": item["key"],
        })
    return evidence_for, evidence_against


def _quality(
    *,
    effect_pass: bool,
    q_pass: bool,
    stability_pass: bool,
    context_only_lineage: bool = False,
) -> dict[str, Any]:
    if not effect_pass:
        tier = "insufficient"
    elif q_pass and stability_pass:
        tier = "exploratory_unreplicated"
    else:
        tier = "exploratory_screen"
    return {
        "tier": tier,
        "eligible_for_hypothesis": (
            effect_pass and not context_only_lineage
        ),
    }


def _rank_key(finding: Mapping[str, Any]) -> tuple[Any, ...]:
    q = finding["testing"]["q"]
    effect = finding["effect"]["oriented_estimate"]
    definition = finding["_definition"]
    components = finding["exposure"]["components"]
    actionability = (
        "context"
        if finding.get("_context_only_lineage")
        else definition.actionability
    )
    return (
        _TIER_ORDER[finding["quality"]["tier"]],
        q if q is not None else math.inf,
        -abs(effect) if effect is not None else math.inf,
        -_ACTIONABILITY.get(actionability, 0),
        definition.preferred_rank,
        tuple(
            (item["exposure_key"], item["lag_days"], item["window_days"], item["transform"])
            for item in components
        ),
    )


def _feature_descriptor(
    definition: FeatureDefinition,
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    result = {
        "key": definition.key,
        "display": definition.display_name,
        "unit": definition.unit,
        "temporal_type": definition.temporal_type,
        "direction": definition.direction,
        "merge_rule": definition.source.get("merge"),
        "zero_semantics": definition.zero_semantics,
    }
    if mode is not None:
        result["mode"] = mode
    return result


def _component_payload(
    component: Mapping[str, Any],
    definition: FeatureDefinition,
) -> dict[str, Any]:
    return {
        **component,
        "display": definition.display_name,
        "unit": definition.unit,
        "temporal_type": definition.temporal_type,
        "direction": definition.direction,
        "merge_rule": definition.source.get("merge"),
        "zero_semantics": definition.zero_semantics,
        "temporal_direction": (
            "same_day_or_order_unknown"
            if component["lag_days"] == 0
            else "exposure_precedes_outcome"
        ),
    }


def _source_completeness_interval(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    # Completeness is a set of source dates and revision IDs; overlapping
    # windows must not repeatedly normalize the same source observation.
    observations = {
        id(observation): observation
        for row in rows
        for observation in (
            list(row.get("outcome_observations", ()))
            + list(row.get("exposure_observations", ()))
        )
    }
    return source_completeness(observations.values())


def _seal_evidence(finding: dict[str, Any]) -> dict[str, Any]:
    finding["provenance"] = {
        **finding["provenance"],
        "evidence_fingerprint": evidence_fingerprint(finding),
    }
    return finding


def _insufficient_finding(
    candidate: Mapping[str, Any],
    *,
    mode: str,
    outcome: FeatureDefinition,
    reason: str,
    provenance: Mapping[str, Any],
    analysis_range: Mapping[str, Any],
    baseline_range: Mapping[str, Any],
    attach_source_references: bool = True,
) -> dict[str, Any]:
    component = candidate["component"]
    key = candidate_key(outcome.key, mode, [component])
    fid = finding_id(
        candidate_key_value=key,
        analysis_range=analysis_range,
        baseline_range=baseline_range,
        input_fingerprint_value=provenance["input_fingerprint"],
    )
    result = {
        "finding_id": fid,
        "candidate_key": key,
        "outcome": _feature_descriptor(outcome, mode=mode),
        "exposure": {
            "components": [
                _component_payload(component, candidate["definition"])
            ]
        },
        "sample": {
            "eligible_n": candidate["eligible_n"],
            "complete_n": candidate["complete_n"],
            "missing_n": candidate["missing_n"],
            "exposed_n": None,
            "unexposed_n": None,
            "outcome_positive_n": None,
            "outcome_negative_n": None,
            "source_completeness": _source_completeness_interval(
                candidate["rows"]
            ),
        },
        "rates": None,
        "effect": {
            "method": None,
            "estimate": None,
            "oriented_estimate": None,
            "ci95": None,
            "oriented_ci95": None,
        },
        "testing": {
            "p": None,
            "q": None,
            "family_size": None,
            "method": None,
            "seed": None,
            "permutation_iterations": 0,
            "bootstrap_iterations": 0,
        },
        "stability": {
            "status": "insufficient",
            "full": None,
            "first_half": None,
            "second_half": None,
        },
        "confounders": {
            "checked": [],
            "unchecked": [
                {"key": key, "reason": "not_checked_base_gate"}
                for key in (
                    "weekend", "calendar_quarter", "illness", "travel",
                    "medication_regime", "training_phase", "source_era",
                    "source_transition",
                )
            ],
            "sensitive_to": [],
            "weighted_effect": None,
        },
        "evidence_for": [],
        "evidence_against": (
            [{
                "code": reason,
                "value": candidate["complete_n"],
                "threshold": None,
            }]
            + (
                [{
                    "code": "autoregressive_lineage",
                    "value": True,
                    "threshold": False,
                }]
                if candidate.get("_context_only_lineage")
                else []
            )
        ),
        "warnings": sorted({
            "association_not_causation",
            *(
                ["medical_evidence_only"]
                if (
                    outcome.pillar in _MEDICAL_PILLARS
                    or candidate["definition"].pillar in _MEDICAL_PILLARS
                )
                else []
            ),
            *(
                ["autoregressive_lineage"]
                if candidate.get("_context_only_lineage")
                else []
            ),
        }),
        "quality": {"tier": "insufficient", "eligible_for_hypothesis": False},
        "provenance": dict(provenance),
        "_definition": candidate["definition"],
        "_rows": candidate["rows"],
        "_source_observations": candidate["_input_observations"],
        "_context_only_lineage": bool(
            candidate.get("_context_only_lineage")
        ),
    }
    if attach_source_references:
        result["_source_references"] = normalize_row_references(
            candidate["_input_observations"]
        )
    return _seal_evidence(result)


def _strip_internal(finding: Mapping[str, Any], *, include_evidence: bool = False) -> dict[str, Any]:
    result = {
        key: value for key, value in finding.items()
        if not key.startswith("_")
    }
    if include_evidence:
        result["source_references"] = finding.get("_source_references", [])
        result["source_manifests"] = source_manifests(
            finding.get("_source_observations", [])
        )
    return result


def _attach_bootstrap_ci(finding: dict[str, Any]) -> None:
    if (
        finding.get("effect", {}).get("method") != "spearman"
        or finding["effect"].get("ci95") is not None
    ):
        return
    pairs = [
        (float(row["exposure"]), float(row["outcome"]))
        for row in finding.get("_rows", [])
    ]

    ci = bootstrap_interval(
        pairs,
        _spearman_bootstrap_statistic(pairs),
        seed=int(finding["testing"]["seed"]),
    )
    finding["effect"]["ci95"] = list(ci)
    orientation = int(finding.get("_orientation", 1))
    finding["effect"]["oriented_ci95"] = (
        list(ci) if orientation == 1 else [-ci[1], -ci[0]]
    )


def _generated_at(context: AdapterContext) -> str:
    zone = ZoneInfo(context.timezone)
    return datetime.combine(context.today, datetime.min.time(), zone).isoformat()


def analyze_outcome(
    conn: sqlite3.Connection,
    definitions: Iterable[FeatureDefinition] | Mapping[str, FeatureDefinition],
    requested: DateRange,
    context: AdapterContext,
    *,
    outcome_key: str,
    mode: str = "all",
    min_n: int = DEFAULT_MIN_N,
    interactions: str = "none",
    top: int = DEFAULT_TOP,
    include_internal: bool = False,
    attach_intervals: bool = True,
    return_all_internal: bool = False,
) -> dict[str, Any]:
    """Analyze one registered outcome across every eligible connected exposure."""

    validate_options(
        outcome_key=outcome_key,
        mode=mode,
        min_n=min_n,
        interactions=interactions,
        top=top,
    )
    values, by_key = _definition_map(definitions)
    outcome, modes = _validate_outcome(by_key, outcome_key, mode)
    orientations = {item: _orientation(conn, outcome, item) for item in modes}
    goal_revision = (
        _query_goal_revision(conn, outcome.key)
        if outcome.direction in {"target_range", "neutral", "unknown"}
        else None
    )
    goal_revision_ids = [goal_revision[0]] if goal_revision is not None else []

    suppression: Counter[str] = Counter()
    eligible_definitions: list[FeatureDefinition] = []
    components: list[tuple[FeatureDefinition, dict[str, Any]]] = []
    for definition in values:
        if definition.key == outcome.key:
            suppression["same_feature"] += 1
            continue
        if "exposure" not in definition.roles:
            continue
        if not definition.implemented or not definition.candidate_enabled:
            suppression["candidate_disabled"] += 1
            continue
        reason = _lineage_reason(definition, outcome)
        if reason:
            suppression[reason] += 1
            continue
        if not _relation_allowed(definition, outcome):
            suppression["forbidden_relation"] += 1
            continue
        generated = _components_for(definition)
        if not generated:
            suppression[
                "slow_episode_no_generic_alignment"
                if _profile(definition) == "none"
                else "unsupported_transform"
            ] += 1
            continue
        accepted = []
        for component in generated:
            boundary = (
                _component_lineage_reason(definition, outcome, component)
                or _strict_boundary(outcome.key, component, definition)
            )
            if boundary:
                suppression[boundary] += 1
            else:
                accepted.append(component)
        if accepted:
            eligible_definitions.append(definition)
            components.extend((definition, item) for item in accepted)

    max_lookback = max(
        (_component_lookback_days(component) for _, component in components),
        default=0,
    )
    history_definitions = sorted(
        {
            definition.key: definition
            for definition, component in components
            if component["transform"] == "days_since"
        }.values(),
        key=lambda definition: definition.key,
    )
    history_max_lags = {
        definition.key: max(
            int(component["lag_days"])
            for candidate_definition, component in components
            if (
                candidate_definition.key == definition.key
                and component["transform"] == "days_since"
            )
        )
        for definition in history_definitions
    }
    if requested.kind == "all":
        # First discover outcome bounds alone, then rebuild the complete frame
        # only over the actual analysis/baseline interval.  Unaligned future
        # source rows must not alter manifests, dependencies, or fingerprints.
        discovery = build_feature_frame(
            conn, [outcome], requested, context, include_provenance=True
        )
        discovery_observations = _enrich_observations(
            discovery["observations"], by_key
        )
        discovery_index = _by_feature(discovery_observations)
        initial_outcomes = _outcome_rows(
            discovery_index, outcome.key, requested, outcome
        )
        if initial_outcomes:
            actual_from = date.fromisoformat(initial_outcomes[0]["date"])
            actual_to = date.fromisoformat(initial_outcomes[-1]["date"])
            analysis_range = DateRange(actual_from, actual_to)
            baseline_start = actual_from - timedelta(days=max_lookback)
            frame_range = DateRange(baseline_start, actual_to)
            frame = build_feature_frame(
                conn, values, frame_range, context, include_provenance=True
            )
            enriched_observations = _enrich_observations(
                frame["observations"], by_key
            )
        else:
            analysis_range = requested
            baseline_start = None
            enriched_observations = discovery_observations
    else:
        analysis_range = requested
        baseline_start = requested.start - timedelta(days=max_lookback)
        frame_range = DateRange(baseline_start, requested.end)
        frame = build_feature_frame(
            conn, values, frame_range, context, include_provenance=True
        )
        enriched_observations = _enrich_observations(
            frame["observations"], by_key
        )
    observation_index = _by_feature(enriched_observations)
    outcome_rows = _outcome_rows(
        observation_index, outcome.key, analysis_range, outcome
    )
    if outcome_rows and baseline_start is not None and history_definitions:
        # days_since is defined against the actual last historical positive,
        # not merely the largest finite rolling baseline.  Ask the same
        # registered adapters for their all-time observations, then retain
        # only rows strictly before the already-loaded baseline and no later
        # than the final aligned outcome.  Future source rows therefore cannot
        # enter alignment, manifests, dependencies, or fingerprints.
        historical_frame = build_feature_frame(
            conn,
            history_definitions,
            DateRange(start=None, end=None, kind="all"),
            context,
            include_provenance=True,
        )
        historical_observations = _enrich_observations(
            historical_frame["observations"], by_key
        )
        finite_baseline_start = baseline_start.isoformat()
        prior_pool = [
            row
            for row in historical_observations
            if (
                (day := _iso_day(row.get("observed_at"))) is not None
                and day < finite_baseline_start
            )
        ]
        historical_index = _by_feature(prior_pool)
        prior_observations: list[Mapping[str, Any]] = []
        first_outcome_day = date.fromisoformat(str(outcome_rows[0]["date"]))
        for definition in history_definitions:
            earliest_end = first_outcome_day - timedelta(
                days=history_max_lags[definition.key]
            )
            bounded_days = observation_index.get(definition.key, {})
            bounded_has_positive = any(
                (
                    value := _one_day_value(rows)[0]
                ) is not None
                and value > 0
                for day, rows in bounded_days.items()
                if date.fromisoformat(day) <= earliest_end
            )
            if bounded_has_positive:
                continue
            feature_history = historical_index.get(definition.key, {})
            eligible_days = [
                day for day in feature_history
                if date.fromisoformat(day) <= earliest_end
            ]
            latest_positive = next(
                (
                    day
                    for day in sorted(eligible_days, reverse=True)
                    if (
                        (value := _one_day_value(feature_history[day])[0])
                        is not None
                        and value > 0
                    )
                ),
                None,
            )
            retained_days = [
                day for day in eligible_days
                if latest_positive is None or day >= latest_positive
            ]
            prior_observations.extend(
                row
                for day in sorted(retained_days)
                for row in feature_history[day]
            )
        if prior_observations:
            enriched_observations = prior_observations + enriched_observations
            baseline_start = min(
                baseline_start,
                min(
                    date.fromisoformat(str(row["observed_at"])[:10])
                    for row in prior_observations
                ),
            )
            observation_index = _by_feature(enriched_observations)
            outcome_rows = _outcome_rows(
                observation_index, outcome.key, analysis_range, outcome
            )
    if requested.kind == "all":
        analysis_dict = {
            "kind": "all",
            "from": outcome_rows[0]["date"] if outcome_rows else None,
            "to": outcome_rows[-1]["date"] if outcome_rows else None,
        }
    else:
        analysis_dict = _range_dict(analysis_range)
    baseline_dict = {
        "kind": "bounded" if baseline_start is not None else "all",
        "from": baseline_start.isoformat() if baseline_start else None,
        "to": (
            analysis_dict["to"]
            if analysis_dict.get("to") is not None
            else None
        ),
    }

    observed_features = {
        key for key, days in observation_index.items()
        if any(
            row.get("state") in _OBSERVED
            for rows in days.values()
            for row in rows
        )
    }
    raw_candidates: dict[str, list[dict[str, Any]]] = {item: [] for item in modes}
    # The index is final, including older days_since history. Reuse daily values
    # only for the current feature; no observations survive this analysis call.
    cached_feature: str | None = None
    day_values: dict[str, _DayValue] = {}
    date_spans: dict[tuple[date, int], list[str]] = {}
    window_values: dict[_WindowKey, _DayValue] = {}
    for definition, component in components:
        if definition.key not in observed_features:
            suppression["no_observations"] += 1
            continue
        if cached_feature != definition.key:
            day_values = {}
            window_values = {}
            cached_feature = definition.key
        # Exposure alignment, missingness and consulted observations do not
        # depend on the outcome mode. Share those read-only values within this
        # component; each mode still gets its own outcome rows and statistics.
        aligned = _aligned_candidate(
            outcome_rows,
            definition,
            component,
            observation_index,
            modes[0],
            day_values=day_values,
            date_spans=date_spans,
            window_values=window_values,
        )
        for current_mode in modes:
            candidate = aligned if current_mode == modes[0] else {
                **aligned,
                "rows": [
                    {**row, "outcome": _mode_value(row["outcome_raw"], current_mode)}
                    for row in aligned["rows"]
                ],
            }
            lineage_status = _candidate_lineage_status(
                definition, outcome, candidate["rows"]
            )
            if (
                lineage_status is not None
                and lineage_status != "autoregressive_lineage"
            ):
                suppression[lineage_status] += 1
                continue
            if lineage_status == "autoregressive_lineage":
                candidate["_context_only_lineage"] = True
            raw_candidates[current_mode].append(candidate)

    # Provenance canonicalizes sets of rows and revision IDs. Avoid repeatedly
    # normalizing the same immutable observation consulted by overlapping
    # windows; distinct objects (including conflicting rows) remain distinct.
    # Per-candidate evidence lists retain their original order and repetitions.
    fingerprint_by_identity = {
        id(observation): observation
        for row in outcome_rows
        for observation in row["observations"]
    }
    fingerprint_by_identity.update(
        (id(observation), observation)
        for candidates in raw_candidates.values()
        for candidate in candidates
        for observation in candidate["_input_observations"]
    )
    fingerprint_observations = list(fingerprint_by_identity.values())
    registry_sha = registry_content_checksum(values)
    fingerprint = input_fingerprint(
        fingerprint_observations,
        conn=conn,
        registry_version=REGISTRY_VERSION,
        registry_sha256=registry_sha,
        analysis_version=ANALYSIS_VERSION,
        analysis_sha256=engine_sha256(),
        date_from=baseline_dict["from"],
        date_to=baseline_dict["to"],
        goal_revision_ids=goal_revision_ids,
    )
    dependencies = dependency_snapshot(
        conn,
        observations=fingerprint_observations,
        date_from=baseline_dict["from"],
        date_to=baseline_dict["to"],
        goal_revision_ids=goal_revision_ids,
    )
    provenance = {
        "analysis_version": ANALYSIS_VERSION,
        "registry_version": REGISTRY_VERSION,
        "engine_sha256": engine_sha256(),
        "registry_sha256": f"sha256:{registry_sha}",
        "input_fingerprint": fingerprint,
        "dependencies": dependencies,
    }

    all_findings: list[dict[str, Any]] = []
    family_sizes: dict[str, int] = {}
    mode_coverage: dict[str, Any] = {}
    tested_internal: list[dict[str, Any]] = []
    for current_mode in modes:
        retained = _redundancy_filter(raw_candidates[current_mode], suppression)
        tested: list[dict[str, Any]] = []
        insufficient: list[dict[str, Any]] = []
        for candidate in retained:
            reason = _base_gate(
                candidate, current_mode, DEFAULT_MIN_N, outcome.key
            )
            if reason:
                suppression[reason] += 1
                insufficient.append(_insufficient_finding(
                    candidate,
                    mode=current_mode,
                    outcome=outcome,
                    reason=reason,
                    provenance=provenance,
                    analysis_range=analysis_dict,
                    baseline_range=baseline_dict,
                    attach_source_references=False,
                ))
                continue
            component = candidate["component"]
            seed = deterministic_seed(
                analysis_version=ANALYSIS_VERSION,
                outcome_key=outcome.key,
                outcome_mode=current_mode,
                components=[component],
                analysis_range=analysis_dict,
                input_fingerprint=fingerprint,
            )
            try:
                calculation = _calculate_effect(
                    candidate,
                    seed=seed,
                    outcome_binary=(
                        current_mode != "ordinal"
                        or outcome.value_kind == "binary"
                    ),
                )
            except StatsError:
                suppression["undefined_statistic"] += 1
                insufficient.append(_insufficient_finding(
                    candidate,
                    mode=current_mode,
                    outcome=outcome,
                    reason="undefined_statistic",
                    provenance=provenance,
                    analysis_range=analysis_dict,
                    baseline_range=baseline_dict,
                    attach_source_references=False,
                ))
                continue
            tested.append({
                **candidate,
                "calculation": calculation,
                "seed": seed,
            })
        q_values = benjamini_hochberg([
            item["calculation"]["p"] for item in tested
        ])
        family_sizes[current_mode] = len(tested)
        for candidate, q in zip(tested, q_values):
            calculation = candidate["calculation"]
            raw_effect = float(calculation["estimate"])
            orientation = orientations[current_mode]
            oriented = raw_effect * orientation
            stability_raw = _stability(
                candidate["rows"],
                calculation["method"],
                raw_effect,
            )
            stability = {
                **stability_raw,
                "full": (
                    stability_raw["full"] * orientation
                    if stability_raw["full"] is not None else None
                ),
                "first_half": (
                    stability_raw["first_half"] * orientation
                    if stability_raw["first_half"] is not None else None
                ),
                "second_half": (
                    stability_raw["second_half"] * orientation
                    if stability_raw["second_half"] is not None else None
                ),
            }
            confounders = _confounders(
                candidate["rows"],
                observation_index,
                calculation["method"],
                oriented,
                orientation,
            )
            threshold = 0.10 if calculation["method"] == "risk_difference" else 0.20
            effect_pass = abs(raw_effect) >= threshold
            q_pass = q <= 0.10
            stability_pass = stability["status"] == "stable"
            evidence_for, evidence_against = _evidence(
                min_n=DEFAULT_MIN_N,
                complete_n=candidate["complete_n"],
                effect=oriented,
                effect_threshold=threshold,
                q=q,
                stability=stability,
                missing_rate=candidate["missing_rate"],
                confounders=confounders,
            )
            context_only_lineage = bool(
                candidate.get("_context_only_lineage")
            )
            if context_only_lineage:
                evidence_against.append({
                    "code": "autoregressive_lineage",
                    "value": True,
                    "threshold": False,
                })
            warnings = ["association_not_causation", "multiple_testing"]
            if context_only_lineage:
                warnings.append("autoregressive_lineage")
            if (
                outcome.pillar in _MEDICAL_PILLARS
                or candidate["definition"].pillar in _MEDICAL_PILLARS
            ):
                warnings.append("medical_evidence_only")
            if candidate["complete_n"] < 60:
                warnings.append("small_n")
            if candidate["missing_rate"] > 0.30:
                warnings.append("high_missingness")
            if stability["status"] != "stable":
                warnings.append("unstable_halves")
            if candidate["component"]["lag_days"] == 0:
                warnings.append("timing_unknown")
            if confounders["sensitive_to"]:
                warnings.append("confounder_sensitive")
            if len(set(row["source_era"] for row in candidate["rows"])) > 1:
                warnings.append("source_transition")
            if (
                outcome.temporal_type == "slow_measurement"
                or candidate["definition"].temporal_type == "slow_measurement"
            ):
                warnings.append("slow_measurement")
            if (
                analysis_dict.get("to")
                and date.fromisoformat(analysis_dict["to"]) < context.today
            ):
                warnings.append("historical_only")
            if candidate["definition"].identity_dimension and not any(
                (observation.get("provenance") or {}).get("alias_revision_id")
                or (observation.get("provenance") or {}).get("alias_revision_ids")
                for row in candidate["rows"]
                for observation in row["exposure_observations"]
            ):
                warnings.append("identity_unaliased")
            component = _component_payload(
                candidate["component"], candidate["definition"]
            )
            key = candidate_key(
                outcome.key, current_mode, [candidate["component"]]
            )
            fid = finding_id(
                candidate_key_value=key,
                analysis_range=analysis_dict,
                baseline_range=baseline_dict,
                input_fingerprint_value=fingerprint,
            )
            rates = (
                {
                    **calculation["rates"],
                    "oriented_risk_difference": (
                        calculation["rates"]["risk_difference"] * orientation
                    ),
                }
                if calculation["rates"] is not None
                else None
            )
            raw_ci = calculation["ci95"]
            oriented_ci = (
                list(raw_ci)
                if raw_ci is not None and orientation == 1
                else (
                    [-raw_ci[1], -raw_ci[0]]
                    if raw_ci is not None
                    else None
                )
            )
            finding = {
                "finding_id": fid,
                "candidate_key": key,
                "outcome": _feature_descriptor(outcome, mode=current_mode),
                "exposure": {"components": [component]},
                "sample": {
                    "eligible_n": candidate["eligible_n"],
                    "complete_n": candidate["complete_n"],
                    "missing_n": candidate["missing_n"],
                    **calculation["sample_extra"],
                    "source_completeness": (
                        _source_completeness_interval(candidate["rows"])
                    ),
                },
                "rates": rates,
                "effect": {
                    "method": calculation["method"],
                    "estimate": raw_effect,
                    "oriented_estimate": oriented,
                    "ci95": raw_ci,
                    "oriented_ci95": oriented_ci,
                },
                "testing": {
                    "p": calculation["p"],
                    "q": q,
                    "family_size": len(tested),
                    "method": calculation["test_method"],
                    "seed": candidate["seed"],
                    "permutation_iterations": (
                        PERMUTATION_ITERATIONS
                        if calculation["test_method"] == "spearman_permutation"
                        else 0
                    ),
                    "bootstrap_iterations": (
                        BOOTSTRAP_ITERATIONS
                        if calculation["method"] != "risk_difference"
                        else 0
                    ),
                },
                "stability": stability,
                "confounders": confounders,
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
                "warnings": sorted(set(warnings)),
                "quality": _quality(
                    effect_pass=effect_pass,
                    q_pass=q_pass,
                    stability_pass=stability_pass,
                    context_only_lineage=context_only_lineage,
                ),
                "provenance": dict(provenance),
                "_definition": candidate["definition"],
                "_rows": candidate["rows"],
                "_source_observations": candidate["_input_observations"],
                "_base_pass": True,
                "_effect_pass": effect_pass,
                "_q_pass": q_pass,
                "_binary_exposure": _binary([
                    row["exposure"] for row in candidate["rows"]
                ]),
                "_orientation": orientation,
                "_context_only_lineage": context_only_lineage,
            }
            _seal_evidence(finding)
            tested_internal.append(finding)
            all_findings.append(finding)
        all_findings.extend(insufficient)
        mode_coverage[current_mode] = {
            "outcome_eligible_n": len(outcome_rows),
            "generated_candidates": len(raw_candidates[current_mode]),
            "tested_candidates": len(tested),
            "insufficient_candidates": len(insufficient),
            "family_size": len(tested),
        }

    interaction_family_sizes: dict[str, int] = {}
    if interactions == "pairwise":
        from .interactions import analyze_pairwise

        pairs, pair_suppression, interaction_family_sizes = analyze_pairwise(
            outcome=outcome,
            modes=modes,
            singles=tested_internal,
            analysis_range=analysis_dict,
            baseline_range=baseline_dict,
            input_fingerprint_value=fingerprint,
            provenance=provenance,
            top_limit=30,
        )
        all_findings.extend(pairs)
        suppression.update(pair_suppression)

    all_findings.sort(key=_rank_key)
    visible_findings = (
        list(all_findings)
        if min_n == DEFAULT_MIN_N
        else [
            item for item in all_findings
            if item["sample"]["complete_n"] >= min_n
        ]
    )
    suppression["requested_min_n"] += len(all_findings) - len(visible_findings)
    returned = (
        visible_findings
        if include_internal and return_all_internal
        else visible_findings[:top]
    )
    # References do not participate in ranking, testing or the evidence digest.
    # Unfinalized full-internal replay retains raw observations; it constructs
    # references after selecting one finding and adding dependency observations.
    # Other callers, including finalized full-internal ledger reads, retain them.
    defer_replay_references = (
        include_internal and return_all_internal and not attach_intervals
    )
    if not defer_replay_references:
        for finding in returned:
            if "_source_references" not in finding:
                finding["_source_references"] = normalize_row_references(
                    finding["_source_observations"]
                )
    if attach_intervals:
        for finding in returned:
            try:
                _attach_bootstrap_ci(finding)
            except StatsError:
                finding["effect"]["ci95"] = None
                finding["warnings"] = sorted(set(
                    finding.get("warnings", []) + ["bootstrap_undefined"]
                ))
            _seal_evidence(finding)
    manifests = source_manifests(fingerprint_observations)
    readiness_payload = build_readiness(
        conn,
        values,
        requested,
        context,
        outcome=outcome.key,
    )
    response = {
        "ok": True,
        "contract_version": ANALYSIS_CONTRACT_VERSION,
        "meta": {
            "analysis_version": ANALYSIS_VERSION,
            "registry_version": REGISTRY_VERSION,
            "engine_sha256": provenance["engine_sha256"],
            "registry_sha256": provenance["registry_sha256"],
            "input_fingerprint": fingerprint,
            "generated_at": _generated_at(context),
            "timezone": context.timezone,
            "requested_range": _range_dict(
                requested, include_empty_bounds=True
            ),
            "analysis_range": analysis_dict,
            "baseline_range": baseline_dict,
            "outcome": outcome.key,
            "modes": modes,
            "min_n": min_n,
            "interactions": interactions,
            "top": top,
            "candidate_family_sizes": family_sizes,
            "interaction_family_sizes": interaction_family_sizes,
        },
        "coverage": {
            "outcome_eligible_n": len(outcome_rows),
            "modes": mode_coverage,
            "source_manifests": manifests,
            "dependencies": dependencies,
        },
        "readiness": readiness_payload,
        "findings": (
            returned if include_internal
            else [_strip_internal(item) for item in returned]
        ),
        "suppression_counts": {
            reason: suppression.get(reason, 0)
            for reason in _SUPPRESSION_REASONS
        },
        "warnings": sorted({
            warning
            for item in returned
            for warning in item.get("warnings", [])
        }),
    }
    return response


def recompute_finding_evidence(
    conn: sqlite3.Connection,
    definitions: Iterable[FeatureDefinition] | Mapping[str, FeatureDefinition],
    requested: DateRange,
    context: AdapterContext,
    *,
    outcome_key: str,
    finding_id_value: str,
    input_fingerprint_value: str,
) -> dict[str, Any]:
    """Rerun all modes/pairs and return audit references for one exact finding."""

    validate_sha256_id(finding_id_value, "finding-id")
    validate_sha256_id(input_fingerprint_value, "input-fingerprint")
    result = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key=outcome_key,
        mode="all",
        min_n=DEFAULT_MIN_N,
        interactions="pairwise",
        top=100,
        include_internal=True,
        attach_intervals=False,
        return_all_internal=True,
    )
    actual = result["meta"]["input_fingerprint"]
    if actual != input_fingerprint_value:
        raise AssociationError(
            "stale_finding",
            "input fingerprint no longer matches the recomputed analysis",
        )
    finding = next(
        (item for item in result["findings"] if item["finding_id"] == finding_id_value),
        None,
    )
    if finding is None:
        raise AssociationError(
            "stale_finding",
            "finding is absent from the recomputed analysis",
        )
    try:
        _attach_bootstrap_ci(finding)
    except StatsError:
        finding["effect"]["ci95"] = None
        finding["warnings"] = sorted(set(
            finding.get("warnings", []) + ["bootstrap_undefined"]
        ))
    try:
        dependency_observations = dependency_source_observations(
            conn,
            result["coverage"]["dependencies"],
            date_from=result["meta"]["baseline_range"]["from"],
            date_to=result["meta"]["baseline_range"]["to"],
        )
    except ProvenanceError as exc:
        raise AssociationError(
            "stale_finding",
            "finding dependencies are no longer auditable",
        ) from exc
    evidence_observations = (
        list(finding.get("_source_observations", ()))
        + dependency_observations
    )
    finding["_source_observations"] = evidence_observations
    finding["_source_references"] = normalize_row_references(
        evidence_observations
    )
    _seal_evidence(finding)
    return {
        "ok": True,
        "contract_version": ANALYSIS_CONTRACT_VERSION,
        "meta": result["meta"],
        "finding": _strip_internal(finding, include_evidence=True),
    }


__all__ = [
    "ANALYSIS_VERSION",
    "AssociationError",
    "DEFAULT_MIN_N",
    "DEFAULT_TOP",
    "MODE_VALUES",
    "analyze_outcome",
    "recompute_finding_evidence",
    "validate_options",
    "validate_sha256_id",
]
