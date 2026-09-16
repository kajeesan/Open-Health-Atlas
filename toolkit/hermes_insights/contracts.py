"""Strict contracts shared by the Phase 3 feature registry and adapters.

The registry is intentionally a closed, code-owned schema.  Keeping validation
here prevents an adapter, database column, or caller from quietly inventing
metadata or changing feature meaning without a registry-version change.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Mapping


REGISTRY_VERSION = "feature-registry-v1"

FEATURE_KEY_RE = re.compile(
    r"^(?=.{1,160}$)[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$"
)
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
LINEAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/|-]{0,511}$")
IDENTITY_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}:[0-9a-f]{64}$")
RECIPE_IDENTITY_KEY_RE = re.compile(r"^recipe:[a-z0-9][a-z0-9._-]{0,159}$")
IDENTITY_TOKEN_RE = re.compile(r"^i_[0-9a-f]{64}$")

ROLE_VALUES = frozenset({"exposure", "outcome", "confounder", "readiness", "display"})
TEMPORAL_TYPES = frozenset({
    "daily_measurement", "event_occurrence", "rolling_exposure",
    "slow_measurement", "static_config", "outcome", "unavailable",
})
OBSERVATION_STATES = frozenset({
    "observed", "structural_zero", "missing", "stale",
    "not_applicable", "not_connected",
})
ACTIONABILITY_VALUES = frozenset({
    "outcome", "direct", "indirect", "context", "non_actionable",
})
DIRECTION_VALUES = frozenset({
    "higher_better", "lower_better", "target_range", "neutral", "unknown",
})
VALUE_KINDS = frozenset({
    "continuous", "integer", "ordinal", "binary", "count", "categorical",
    "time_minutes", "ratio", "episode", "config",
})
ZERO_SEMANTICS_VALUES = frozenset({
    "invalid", "valid", "structural_zero_if_complete", "explicit_binary",
    "not_observation", "derived_strict",
})
CONFOUNDER_ROLE_VALUES = frozenset({
    "none", "outcome", "context", "source_era", "medical_constraint",
})
SOURCE_SEMANTICS_VALUES = frozenset({
    "generated_calendar", "operational_sync", "selected_automated_daily",
    "source_specific_daily", "automated_daily", "manual_daily",
    "automated_event", "manual_event", "slow_episode", "static_config",
    "derived",
})
COVERAGE_CADENCES = frozenset({"daily", "event", "weekly", "episodic", "static"})

COMPLETENESS_SCOPES = frozenset({
    "food_identity", "nutrition_total", "social", "location", "activity",
    "travel", "illness", "stress", "training_phase", "other_event",
    "medication", "supplement", "pain",
})
BASE_COMPLETENESS_PROFILES = frozenset({
    "AUTO_VALUE", "AUTO_EVENT", "MANUAL_VALUE", "SLOW_EPISODE",
    "STATIC_CONFIG", "DERIVED",
})

LAG_PROFILES: dict[str, dict[str, tuple[int | str, ...]]] = {
    "LP_STATE": {"lags": (0, 1, 2, 3, 7), "windows": ("point", 3, 7, 28)},
    "LP_SHORT": {"lags": (0, 1, 2), "windows": ("point", 3, 7, 28)},
    "LP_EVENT": {"lags": (0, 1, 2, 3, 7), "windows": (1, 3, 7, 28)},
    "LP_LOAD": {"lags": (0, 1, 2, 3, 7), "windows": (1, 3, 7, 28, 90, 365)},
    "LP_ENV": {"lags": (0, 1, 2, 3), "windows": ("point", 3, 7, 28)},
    "LP_TIMED": {"lags": (0,), "windows": ("point",)},
    "LP_SLOW": {"lags": (), "windows": ()},
    "LP_NONE": {"lags": (), "windows": ()},
}

READINESS_VALUE_EFFORT = frozenset({
    (5, 1), (5, 2), (5, 3), (4, 1), (4, 2), (3, 1), (3, 2),
    (3, 3), (2, 1), (1, 1), (0, None),
})
PREFERRED_RANKS = frozenset({1, 2, 3, 5, 100})
GOAL_FAMILIES = frozenset({
    "body_recomposition", "strength_and_muscle", "imbalance_correction",
    "pain_reduction", "cardio_improvement", "follow_through", "green_days",
    "energy_focus_mood",
})
ADAPTER_IDS = frozenset({
    "daily", "training", "running", "nutrition", "events", "quarterly",
    "pain", "labs", "environment", "adherence", "manual",
})
PILLARS = frozenset({
    "calendar", "source", "wearable", "sleep", "subjective", "substance",
    "vitals", "medication", "training", "cardio", "running", "nutrition",
    "food", "meal", "supplement", "event", "body", "fitness", "pain",
    "rehab", "lab", "assessment", "adherence", "skincare", "weather",
    "air", "recovery",
})

SOURCE_REQUIRED_KEYS = frozenset({"table", "columns", "merge"})
SOURCE_OPTIONAL_KEYS = frozenset({"tables", "identity", "parameters"})
SOURCE_IDENTITY_KEYS = frozenset({
    "namespace", "identity_key", "identity_token", "original_label",
    "alias_revision_id",
})


class ContractError(ValueError):
    """Raised when code or input violates the frozen registry contract."""


def _canonical_value(value: Any) -> Any:
    """Return a JSON-safe, deterministic value without lossy coercion."""

    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("canonical object keys must be strings")
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError("non-finite values are not canonical")
        if value == 0:
            return 0.0
        return float(format(value, ".12g"))
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ContractError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize using the design's canonical compact UTF-8 JSON rules."""

    return json.dumps(
        _canonical_value(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class DateRange:
    """Inclusive canonical date range requested from an adapter."""

    start: date | None
    end: date | None
    kind: str = "bounded"

    def __post_init__(self) -> None:
        if self.kind not in {"bounded", "all"}:
            raise ContractError("range kind must be bounded or all")
        if self.kind == "all":
            if self.start is not None or self.end is not None:
                raise ContractError("all range must not carry bounds")
            return
        if type(self.start) is not date or type(self.end) is not date:
            raise ContractError("bounded range requires exact date bounds")
        if self.start > self.end:
            raise ContractError("range start must not follow end")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "from": self.start, "to": self.end}


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureDefinition:
    """One complete entry in the closed ``feature-registry-v1`` schema."""

    key: str
    display_name: str
    pillar: str
    source: Mapping[str, Any]
    unit: str
    direction: str
    temporal_type: str
    valid_from: str | None
    coverage: Mapping[str, Any]
    actionability: str
    lag_eligibility: Mapping[str, Any]
    confounder_role: str
    value_kind: str
    zero_semantics: str
    stale_after_days: int | None
    adapter: str
    implemented: bool
    identity_dimension: str | None
    source_semantics: str
    min_observations: int
    redundancy_group: str | None
    preferred_rank: int
    roles: tuple[str, ...]
    formula_id: str
    aggregation_id: str
    completeness_profile: str
    candidate_enabled: bool
    lineage_roots: tuple[str, ...]
    goal_families: tuple[str, ...]
    readiness_value: int
    readiness_effort: int | None

    def __post_init__(self) -> None:
        validate_feature_definition(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FeatureDefinition":
        """Build from an exact mapping, rejecting missing and unknown keys."""

        if not isinstance(raw, Mapping):
            raise ContractError("feature definition must be an object")
        expected = {item.name for item in fields(cls)}
        actual = set(raw)
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        if missing or unknown:
            raise ContractError(f"feature metadata mismatch missing={missing} unknown={unknown}")
        value = dict(raw)
        for name in ("roles", "lineage_roots", "goal_families"):
            if not isinstance(value[name], (list, tuple)):
                raise ContractError(f"{name} must be an array")
            value[name] = tuple(value[name])
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _canonical_value(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class Observation:
    """One adapter-produced observation with explicit missingness state."""

    feature_key: str
    observed_at: str
    value: object
    state: str
    unit: str | None = None
    source: str | None = None
    provenance: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.feature_key, str) or not FEATURE_KEY_RE.fullmatch(self.feature_key):
            raise ContractError("invalid observation feature key")
        if not isinstance(self.observed_at, str) or not self.observed_at:
            raise ContractError("observed_at must be a non-empty ISO string")
        try:
            observed_date = date.fromisoformat(self.observed_at[:10])
            if observed_date.isoformat() != self.observed_at[:10]:
                raise ValueError
            if len(self.observed_at) > 10:
                datetime.fromisoformat(self.observed_at)
        except (TypeError, ValueError):
            raise ContractError("observed_at is not ISO date or datetime") from None
        if self.state not in OBSERVATION_STATES:
            raise ContractError("invalid observation state")
        if self.state == "observed" and self.value is None:
            raise ContractError("observed observation must carry a value")
        if self.state in {"missing", "not_applicable", "not_connected"} and self.value is not None:
            raise ContractError(f"{self.state} observation must not carry a value")
        if self.state == "structural_zero" and (isinstance(self.value, bool) or self.value != 0):
            raise ContractError("structural_zero observation must carry numeric zero")
        if self.unit is not None and (not isinstance(self.unit, str) or not self.unit):
            raise ContractError("observation unit must be non-empty text or null")
        if self.source is not None and (not isinstance(self.source, str) or not self.source):
            raise ContractError("observation source must be non-empty text or null")
        if self.provenance is not None and not isinstance(self.provenance, dict):
            raise ContractError("observation provenance must be an object or null")
        _canonical_value(self.value)
        _canonical_value(self.provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_key": self.feature_key, "observed_at": self.observed_at,
            "value": _canonical_value(self.value), "state": self.state,
            "unit": self.unit, "source": self.source,
            "provenance": _canonical_value(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class AdapterContext:
    """Server-owned deterministic functions/constants made available to adapters."""

    today: date
    timezone: str
    constants: Mapping[str, Any]
    functions: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.today) is not date:
            raise ContractError("adapter context today must be a date")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise ContractError("adapter context timezone must be non-empty")
        if not isinstance(self.constants, Mapping) or not isinstance(self.functions, Mapping):
            raise ContractError("adapter constants/functions must be mappings")

    def to_dict(self) -> dict[str, Any]:
        # Values may be callables or catalog objects; exposing only stable keys
        # avoids pretending executable state belongs in analytical provenance.
        return {
            "today": self.today.isoformat(), "timezone": self.timezone,
            "constant_keys": sorted(str(key) for key in self.constants),
            "function_keys": sorted(str(key) for key in self.functions),
        }


def _exact_mapping(value: Any, required: frozenset[str], optional: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - required - optional)
    if missing or unknown:
        raise ContractError(f"{name} metadata mismatch missing={missing} unknown={unknown}")
    return dict(value)


def valid_completeness_profile(value: str) -> bool:
    if value in BASE_COMPLETENESS_PROFILES:
        return True
    for prefix in ("MANUAL_EVENT:", "EXPLICIT_BINARY:"):
        if value.startswith(prefix):
            return value[len(prefix):] in COMPLETENESS_SCOPES
    return False


def validate_feature_definition(feature: FeatureDefinition) -> None:
    """Validate every field and nested object in one registry entry."""

    if not isinstance(feature.key, str) or not FEATURE_KEY_RE.fullmatch(feature.key):
        raise ContractError(f"invalid feature key: {feature.key!r}")
    if not isinstance(feature.display_name, str) or not feature.display_name.strip():
        raise ContractError(f"{feature.key}: display_name must be non-empty")
    if feature.pillar not in PILLARS:
        raise ContractError(f"{feature.key}: unknown pillar {feature.pillar!r}")

    source = _exact_mapping(feature.source, SOURCE_REQUIRED_KEYS, SOURCE_OPTIONAL_KEYS, "source")
    if not isinstance(source["table"], str) or not TOKEN_RE.fullmatch(source["table"]):
        raise ContractError(f"{feature.key}: invalid source table")
    if not isinstance(source["columns"], (tuple, list)) or not source["columns"]:
        raise ContractError(f"{feature.key}: source columns must be non-empty")
    if any(not isinstance(item, str) or not TOKEN_RE.fullmatch(item) for item in source["columns"]):
        raise ContractError(f"{feature.key}: invalid source column")
    if len(set(source["columns"])) != len(source["columns"]):
        raise ContractError(f"{feature.key}: duplicate source column")
    if not isinstance(source["merge"], str) or not TOKEN_RE.fullmatch(source["merge"]):
        raise ContractError(f"{feature.key}: invalid source merge id")
    if "tables" in source:
        if not isinstance(source["tables"], (tuple, list)) or not source["tables"]:
            raise ContractError(f"{feature.key}: source tables must be a non-empty array")
        if tuple(source["tables"]) != tuple(sorted(set(source["tables"]))):
            raise ContractError(f"{feature.key}: source tables must be sorted and unique")
        if source["table"] not in source["tables"]:
            raise ContractError(f"{feature.key}: primary source table missing from tables")
    if "parameters" in source:
        if not isinstance(source["parameters"], Mapping):
            raise ContractError(f"{feature.key}: source parameters must be an object")
        _canonical_value(source["parameters"])
    if "identity" in source:
        identity = _exact_mapping(source["identity"], SOURCE_IDENTITY_KEYS, frozenset(), "source.identity")
        if not isinstance(identity["namespace"], str) or not TOKEN_RE.fullmatch(identity["namespace"]):
            raise ContractError(f"{feature.key}: invalid identity namespace")
        key = identity["identity_key"]
        if not isinstance(key, str) or not (
            IDENTITY_KEY_RE.fullmatch(key) or RECIPE_IDENTITY_KEY_RE.fullmatch(key)
        ):
            raise ContractError(f"{feature.key}: invalid identity key")
        if IDENTITY_KEY_RE.fullmatch(key) and key.split(":", 1)[0] != identity["namespace"]:
            raise ContractError(f"{feature.key}: identity namespace/key drift")
        if not isinstance(identity["identity_token"], str) or not IDENTITY_TOKEN_RE.fullmatch(identity["identity_token"]):
            raise ContractError(f"{feature.key}: invalid identity token")
        digest = (key.rsplit(":", 1)[1] if IDENTITY_KEY_RE.fullmatch(key)
                  else hashlib.sha256(key.encode("utf-8")).hexdigest())
        if identity["identity_token"] != f"i_{digest}":
            raise ContractError(f"{feature.key}: identity token/key drift")
        if not isinstance(identity["original_label"], str) or not identity["original_label"].strip():
            raise ContractError(f"{feature.key}: original identity label is required")
        alias_id = identity["alias_revision_id"]
        if alias_id is not None and (type(alias_id) is not int or alias_id < 1):
            raise ContractError(f"{feature.key}: invalid alias revision id")

    if not isinstance(feature.unit, str) or not feature.unit:
        raise ContractError(f"{feature.key}: unit must be non-empty")
    if feature.direction not in DIRECTION_VALUES:
        raise ContractError(f"{feature.key}: invalid direction")
    if feature.temporal_type not in TEMPORAL_TYPES:
        raise ContractError(f"{feature.key}: invalid temporal type")
    if feature.valid_from is not None:
        try:
            if date.fromisoformat(feature.valid_from).isoformat() != feature.valid_from:
                raise ValueError
        except (TypeError, ValueError):
            raise ContractError(f"{feature.key}: valid_from must be canonical ISO date") from None

    coverage = _exact_mapping(feature.coverage, frozenset({"cadence", "target_pct"}), frozenset(), "coverage")
    if coverage["cadence"] not in COVERAGE_CADENCES:
        raise ContractError(f"{feature.key}: invalid coverage cadence")
    target = coverage["target_pct"]
    if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float)) or not 0 <= target <= 100):
        raise ContractError(f"{feature.key}: target_pct must be 0..100 or null")

    if feature.actionability not in ACTIONABILITY_VALUES:
        raise ContractError(f"{feature.key}: invalid actionability")
    lag = _exact_mapping(feature.lag_eligibility, frozenset({"lags", "windows"}), frozenset(), "lag_eligibility")
    if not isinstance(lag["lags"], (tuple, list)) or not isinstance(lag["windows"], (tuple, list)):
        raise ContractError(f"{feature.key}: lag metadata must be arrays")
    expected_lag = LAG_PROFILES.get(feature.aggregation_id)
    if expected_lag is None:
        raise ContractError(f"{feature.key}: unknown aggregation id")
    if tuple(lag["lags"]) != expected_lag["lags"] or tuple(lag["windows"]) != expected_lag["windows"]:
        raise ContractError(f"{feature.key}: lag metadata drifts from {feature.aggregation_id}")

    if feature.confounder_role not in CONFOUNDER_ROLE_VALUES:
        raise ContractError(f"{feature.key}: invalid confounder role")
    if feature.value_kind not in VALUE_KINDS:
        raise ContractError(f"{feature.key}: invalid value kind")
    if feature.zero_semantics not in ZERO_SEMANTICS_VALUES:
        raise ContractError(f"{feature.key}: invalid zero semantics")
    if feature.stale_after_days is not None and (
        type(feature.stale_after_days) is not int or feature.stale_after_days < 0
    ):
        raise ContractError(f"{feature.key}: stale_after_days must be nonnegative or null")
    if feature.adapter not in ADAPTER_IDS:
        raise ContractError(f"{feature.key}: unknown adapter")
    if type(feature.implemented) is not bool or type(feature.candidate_enabled) is not bool:
        raise ContractError(f"{feature.key}: implemented/candidate_enabled must be booleans")
    if feature.identity_dimension is not None and (
        not isinstance(feature.identity_dimension, str) or not TOKEN_RE.fullmatch(feature.identity_dimension)
    ):
        raise ContractError(f"{feature.key}: invalid identity dimension")
    if feature.source_semantics not in SOURCE_SEMANTICS_VALUES:
        raise ContractError(f"{feature.key}: invalid source semantics")
    if type(feature.min_observations) is not int or feature.min_observations < 0:
        raise ContractError(f"{feature.key}: min_observations must be nonnegative integer")
    if feature.redundancy_group is not None and (
        not isinstance(feature.redundancy_group, str) or not TOKEN_RE.fullmatch(feature.redundancy_group)
    ):
        raise ContractError(f"{feature.key}: invalid redundancy group")
    if feature.preferred_rank not in PREFERRED_RANKS:
        raise ContractError(f"{feature.key}: invalid preferred rank")
    if feature.redundancy_group is None and feature.preferred_rank != 100:
        raise ContractError(f"{feature.key}: entries outside redundancy groups use preferred_rank=100")

    if not isinstance(feature.roles, tuple) or not feature.roles:
        raise ContractError(f"{feature.key}: roles must be a non-empty tuple")
    if feature.roles != tuple(sorted(set(feature.roles))) or not set(feature.roles) <= ROLE_VALUES:
        raise ContractError(f"{feature.key}: roles must be sorted, unique, and known")
    if not isinstance(feature.formula_id, str) or not TOKEN_RE.fullmatch(feature.formula_id):
        raise ContractError(f"{feature.key}: invalid formula id")
    if not valid_completeness_profile(feature.completeness_profile):
        raise ContractError(f"{feature.key}: invalid completeness profile")
    if feature.completeness_profile == "STATIC_CONFIG":
        if feature.temporal_type != "static_config":
            raise ContractError(f"{feature.key}: STATIC_CONFIG requires static_config temporal type")
        if feature.aggregation_id != "LP_NONE":
            raise ContractError(f"{feature.key}: STATIC_CONFIG requires LP_NONE")
        if feature.zero_semantics != "not_observation":
            raise ContractError(f"{feature.key}: STATIC_CONFIG is never an observation")
        if feature.candidate_enabled or set(feature.roles) & {"exposure", "outcome"}:
            raise ContractError(f"{feature.key}: static config cannot be an analytical candidate")
        if feature.min_observations != 0:
            raise ContractError(f"{feature.key}: STATIC_CONFIG uses zero observation minimum")
    elif feature.temporal_type == "static_config":
        raise ContractError(f"{feature.key}: static_config temporal type requires STATIC_CONFIG")
    if feature.temporal_type == "unavailable" and feature.implemented:
        raise ContractError(f"{feature.key}: unavailable logic cannot be implemented")
    if not feature.implemented and feature.temporal_type != "unavailable":
        raise ContractError(f"{feature.key}: unimplemented logic must be unavailable")
    if feature.temporal_type == "unavailable" and feature.candidate_enabled:
        raise ContractError(f"{feature.key}: unavailable logic cannot be a candidate")

    if not isinstance(feature.lineage_roots, tuple) or not feature.lineage_roots:
        raise ContractError(f"{feature.key}: lineage_roots must be non-empty")
    if feature.lineage_roots != tuple(sorted(set(feature.lineage_roots))):
        raise ContractError(f"{feature.key}: lineage_roots must be sorted and unique")
    if any(not isinstance(root, str) or not LINEAGE_RE.fullmatch(root) for root in feature.lineage_roots):
        raise ContractError(f"{feature.key}: invalid lineage root")
    if not isinstance(feature.goal_families, tuple):
        raise ContractError(f"{feature.key}: goal_families must be a tuple")
    if feature.goal_families != tuple(sorted(set(feature.goal_families))) or not set(feature.goal_families) <= GOAL_FAMILIES:
        raise ContractError(f"{feature.key}: goal_families must be sorted, unique, and known")
    if (feature.readiness_value, feature.readiness_effort) not in READINESS_VALUE_EFFORT:
        raise ContractError(f"{feature.key}: invalid readiness value/effort pair")


FEATURE_METADATA_FIELDS = tuple(item.name for item in fields(FeatureDefinition))


__all__ = [
    "ACTIONABILITY_VALUES", "ADAPTER_IDS", "AdapterContext",
    "COMPLETENESS_SCOPES", "ContractError", "DateRange", "DIRECTION_VALUES",
    "FEATURE_KEY_RE", "FEATURE_METADATA_FIELDS", "FeatureDefinition",
    "GOAL_FAMILIES", "LAG_PROFILES", "OBSERVATION_STATES", "Observation",
    "REGISTRY_VERSION", "ROLE_VALUES", "TEMPORAL_TYPES", "canonical_json",
    "validate_feature_definition", "valid_completeness_profile",
]
