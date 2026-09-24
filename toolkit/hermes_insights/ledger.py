"""Canonical Phase 5 analysis and hypothesis-ledger persistence.

The module is deliberately a database *helper*, not another command surface.
``toolkit/health.py`` owns validation at the CLI boundary and transaction
ownership.  This module validates the engine boundary again, derives every
identifier and numerical field from Phase 4 output, and never commits.

Only private, immutable seals returned directly by the Phase 4 engine/replay
helpers may cross the write boundary.  The sealing path checks the current
engine/version and registry hashes, canonical identifiers, evidence
fingerprints, exact field sets, numerical consistency, finite JSON, and range
agreement.  A browser or model therefore has no API for supplying an effect,
count, status, confidence, or transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from . import associations, migrations
from .contracts import AdapterContext, DateRange, REGISTRY_VERSION, canonical_json
from .provenance import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_VERSION,
    candidate_key,
    engine_sha256,
    evidence_fingerprint,
    finding_id,
    sha256_id,
)
from .registry import registry_content_checksum
from .settings import TIMEZONE_NAME


LEDGER_CONTRACT_VERSION = "hypothesis-ledger-v1"
RANGE_PLAN_VERSION = "analysis-range-plan-v1"
SEMANTIC_COMPATIBILITY_SCOPE = "analysis_and_registry_semantics"
SQLITE_MAX_ROWID = 9_223_372_036_854_775_807

BATCH_KINDS = frozenset({"manual", "nightly", "weekly", "monthly", "trigger"})
BATCH_STATUSES = frozenset({"running", "completed", "partial", "failed"})
RUN_STATUSES = frozenset(
    {"running", "completed", "insufficient_data", "no_data", "failed"}
)
HYPOTHESIS_STATUSES = frozenset(
    {
        "candidate",
        "exploratory",
        "strengthening",
        "replicated",
        "weakened",
        "rejected",
        "dormant",
    }
)
CONFIDENCE_VALUES = frozenset({"insufficient", "low", "moderate", "high"})
EVIDENCE_CLASSES = frozenset(
    {
        "initial_exploratory",
        "initial_discovery_pass",
        "same_pass_overlap",
        "same_pass_nonoverlap",
        "same_exploratory",
        "weakening_window",
        "null_nonoverlap",
        "opposite_pass",
        "incompatible_version",
        "dormant_no_eligible_data",
        "dormant_stale_prerequisite",
    }
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_MODEL_PROVENANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,159}$")
_FEATURE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
_TRANSFORMS = frozenset(
    {
        "point",
        "mean",
        "sum",
        "count",
        "delta_per_day",
        "frequency_per_week",
        "days_since",
    }
)
_WINDOWS = frozenset({1, 3, 7, 28, 90, 365})
_TEMPORAL_DIRECTIONS = frozenset(
    {
        "exposure_precedes_outcome",
        "same_day_ordered",
        "same_day_or_order_unknown",
    }
)
_QUALITY_TIERS = frozenset(
    {
        "insufficient",
        "exploratory_screen",
        "exploratory_unreplicated",
        "replicated",
    }
)
_DIRECTIONS = frozenset({"positive", "negative", "mixed", "unknown"})
_EVIDENCE_KINDS = frozenset(
    {
        "same_direction_effect",
        "nonoverlap_replication",
        "weak_or_unstable",
        "null_window",
        "opposite_direction_effect",
        "confounder_sensitivity",
        "method_incompatibility",
    }
)
_ANNOTATION_KINDS = frozenset(
    {"mechanism", "alternative", "next_experiment", "owner_note"}
)
_COMPATIBILITY_PROOF_KEYS = frozenset(
    {
        "proof_sha256",
        "from_analysis_version",
        "from_registry_version",
        "to_analysis_version",
        "to_registry_version",
        "scope",
        "reason_code",
        "evidence_sha256",
    }
)

_VERIFIED_TOKEN = object()

_ANALYSIS_KEYS = frozenset(
    {
        "ok",
        "contract_version",
        "meta",
        "coverage",
        "readiness",
        "findings",
        "suppression_counts",
        "warnings",
    }
)
_META_KEYS = frozenset(
    {
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
        "generated_at",
        "timezone",
        "requested_range",
        "analysis_range",
        "baseline_range",
        "outcome",
        "modes",
        "min_n",
        "interactions",
        "top",
        "candidate_family_sizes",
        "interaction_family_sizes",
    }
)
_COVERAGE_KEYS = frozenset(
    {
        "outcome_eligible_n",
        "modes",
        "source_manifests",
        "dependencies",
    }
)
_MODE_COVERAGE_KEYS = frozenset(
    {
        "outcome_eligible_n",
        "generated_candidates",
        "tested_candidates",
        "insufficient_candidates",
        "family_size",
    }
)
_FINDING_KEYS = frozenset(
    {
        "finding_id",
        "candidate_key",
        "outcome",
        "exposure",
        "sample",
        "rates",
        "effect",
        "testing",
        "stability",
        "confounders",
        "evidence_for",
        "evidence_against",
        "warnings",
        "quality",
        "provenance",
    }
)
_AUDIT_FINDING_KEYS = _FINDING_KEYS | frozenset(
    {"source_references", "source_manifests"}
)
_EVIDENCE_FINGERPRINT_FIELDS = (
    "finding_id",
    "candidate_key",
    "outcome",
    "exposure",
    "sample",
    "rates",
    "effect",
    "testing",
    "stability",
    "confounders",
    "evidence_for",
    "evidence_against",
    "warnings",
    "quality",
)
_COMPONENT_KEYS = frozenset(
    {
        "exposure_key",
        "lag_days",
        "window_days",
        "transform",
        "display",
        "unit",
        "temporal_type",
        "direction",
        "merge_rule",
        "zero_semantics",
        "temporal_direction",
    }
)
_OUTCOME_DESCRIPTOR_KEYS = frozenset(
    {
        "key",
        "display",
        "unit",
        "temporal_type",
        "direction",
        "merge_rule",
        "zero_semantics",
        "mode",
    }
)
_SAMPLE_KEYS = frozenset(
    {
        "eligible_n",
        "complete_n",
        "missing_n",
        "exposed_n",
        "unexposed_n",
        "outcome_positive_n",
        "outcome_negative_n",
        "source_completeness",
    }
)
_EFFECT_KEYS = frozenset(
    {"method", "estimate", "oriented_estimate", "ci95", "oriented_ci95"}
)
_TESTING_KEYS = frozenset(
    {
        "p",
        "q",
        "family_size",
        "method",
        "seed",
        "permutation_iterations",
        "bootstrap_iterations",
    }
)
_STABILITY_KEYS = frozenset(
    {"status", "full", "first_half", "second_half"}
)
_CONFOUNDER_KEYS = frozenset(
    {"checked", "unchecked", "sensitive_to", "weighted_effect"}
)
_PROVENANCE_KEYS = frozenset(
    {
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
        "dependencies",
        "evidence_fingerprint",
    }
)

class LedgerError(ValueError):
    """Raised when a ledger invariant or validation boundary fails."""

    code = "ledger_error"
    validation = False

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


@dataclass(frozen=True, slots=True)
class _VerifiedPhase4Analysis:
    """A complete, current, single-mode Phase 4 result safe to persist."""

    _payload_json: str
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _VERIFIED_TOKEN:
            raise LedgerError(
                "unverified_engine_output",
                "analysis persistence requires verified Phase 4 output",
                validation=True,
            )

    @property
    def payload(self) -> Mapping[str, Any]:
        # Return a fresh tree so even a caller holding the seal cannot mutate
        # the value later consumed by the persistence boundary.
        return json.loads(self._payload_json)


@dataclass(frozen=True, slots=True)
class _VerifiedPhase4Finding:
    """One replay-verified Phase 4 finding safe for explicit promotion."""

    _payload_json: str
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _VERIFIED_TOKEN:
            raise LedgerError(
                "unverified_engine_output",
                "finding promotion requires verified Phase 4 output",
                validation=True,
            )

    @property
    def payload(self) -> Mapping[str, Any]:
        return json.loads(self._payload_json)


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    """The complete deterministic result of one Section 11.6 transition."""

    evidence_class: str
    status: str
    confidence: str
    change_reason: str
    transition_applied: bool
    comparison_evaluation_id: int | None
    compatible_with_prior: bool
    new_eligible_observations: int
    evidence_kinds: tuple[tuple[str, str], ...]
    conditions: Mapping[str, Any]


def _error(code: str, message: str, *, validation: bool = False) -> LedgerError:
    return LedgerError(code, message, validation=validation)


def _exact_mapping(value: Any, keys: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error("invalid_engine_output", f"{name} must be an object")
    missing = sorted(keys - set(value))
    unknown = sorted(set(value) - keys)
    if missing or unknown:
        raise _error(
            "invalid_engine_output",
            f"{name} fields mismatch missing={missing} unknown={unknown}",
        )
    return value


def _canonical_copy(value: Any, name: str) -> Any:
    try:
        encoded = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise _error(
            "nonfinite_engine_output",
            f"{name} is not finite canonical JSON",
        ) from exc
    return json.loads(encoded)


def _text(
    value: Any,
    name: str,
    *,
    maximum: int = 4000,
    token: bool = False,
) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _error("validation_error", f"{name} must be bounded non-empty text", validation=True)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _error(
            "validation_error",
            f"{name} must be valid UTF-8 text",
            validation=True,
        ) from exc
    if token and _TOKEN_RE.fullmatch(value) is None:
        raise _error("validation_error", f"{name} must be a code token", validation=True)
    return value


def _optional_text(
    value: Any,
    name: str,
    *,
    maximum: int = 4000,
    token: bool = False,
) -> str | None:
    if value is None:
        return None
    return _text(value, name, maximum=maximum, token=token)


def _enum(value: Any, allowed: frozenset[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _error(
            "validation_error",
            f"{name} must be one of {sorted(allowed)}",
            validation=True,
        )
    return value


def _integer(
    value: Any,
    name: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _error("validation_error", f"{name} must be an integer", validation=True)
    if maximum is not None and value > maximum:
        raise _error("validation_error", f"{name} is out of range", validation=True)
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _error("invalid_engine_output", f"{name} must be a prefixed SHA-256")
    return value


def _feature(value: Any, name: str) -> str:
    if not isinstance(value, str) or _FEATURE_RE.fullmatch(value) is None:
        raise _error("validation_error", f"{name} must be a feature key", validation=True)
    return value


def canonical_semantic_compatibility_proof(
    *,
    from_analysis_version: str,
    from_registry_version: str,
    to_analysis_version: str,
    to_registry_version: str,
    reason_code: str,
    evidence_sha256: str,
) -> dict[str, str]:
    """Create an auditable proof covering both semantic-version dimensions."""

    values = {
        "from_analysis_version": _text(
            from_analysis_version,
            "from_analysis_version",
            maximum=120,
            token=True,
        ),
        "from_registry_version": _text(
            from_registry_version,
            "from_registry_version",
            maximum=120,
            token=True,
        ),
        "to_analysis_version": _text(
            to_analysis_version,
            "to_analysis_version",
            maximum=120,
            token=True,
        ),
        "to_registry_version": _text(
            to_registry_version,
            "to_registry_version",
            maximum=120,
            token=True,
        ),
        "scope": SEMANTIC_COMPATIBILITY_SCOPE,
        "reason_code": _text(
            reason_code,
            "reason_code",
            maximum=120,
            token=True,
        ),
        "evidence_sha256": _sha(evidence_sha256, "evidence_sha256"),
    }
    return {
        "proof_sha256": sha256_id(
            {
                "contract_version": LEDGER_CONTRACT_VERSION,
                "kind": "semantic_compatibility_proof",
                **values,
            }
        ),
        **values,
    }


def _normalized_compatibility_proofs(
    values: Iterable[Mapping[str, Any]] | None,
) -> dict[tuple[str, str, str, str], dict[str, str]]:
    result: dict[tuple[str, str, str, str], dict[str, str]] = {}
    if values is None:
        return result
    if isinstance(values, (str, bytes, Mapping)):
        raise _error(
            "validation_error",
            "compatibility_proofs must be an array of canonical proofs",
            validation=True,
        )
    for index, raw in enumerate(values):
        mapping = _exact_mapping(
            raw,
            _COMPATIBILITY_PROOF_KEYS,
            f"compatibility_proofs[{index}]",
        )
        expected = canonical_semantic_compatibility_proof(
            from_analysis_version=mapping["from_analysis_version"],
            from_registry_version=mapping["from_registry_version"],
            to_analysis_version=mapping["to_analysis_version"],
            to_registry_version=mapping["to_registry_version"],
            reason_code=mapping["reason_code"],
            evidence_sha256=mapping["evidence_sha256"],
        )
        if dict(mapping) != expected:
            raise _error(
                "validation_error",
                "compatibility proof ID does not match canonical proof fields",
                validation=True,
            )
        key = (
            expected["from_analysis_version"],
            expected["from_registry_version"],
            expected["to_analysis_version"],
            expected["to_registry_version"],
        )
        if key in result and result[key] != expected:
            raise _error(
                "validation_error",
                "conflicting semantic compatibility proofs",
                validation=True,
            )
        result[key] = expected
    return result


def _date(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise _error("validation_error", f"{name} must be ISO YYYY-MM-DD", validation=True)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _error("validation_error", f"{name} must be ISO YYYY-MM-DD", validation=True) from exc
    if parsed.isoformat() != value:
        raise _error("validation_error", f"{name} must be canonical ISO date", validation=True)
    return value


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise _error("validation_error", f"{name} must be an ISO timestamp", validation=True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _error("validation_error", f"{name} must be an ISO timestamp", validation=True) from exc
    if parsed.tzinfo is None:
        raise _error("validation_error", f"{name} must include a timezone", validation=True)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_dict(row: sqlite3.Row | Sequence[Any], columns: Sequence[str]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return dict(zip(columns, row, strict=True))


def require_ledger_schema(conn: sqlite3.Connection) -> None:
    """Require the exact recorded Migration 004 inventory with FK enforcement."""

    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise _error(
            "foreign_keys_required",
            "ledger writes require PRAGMA foreign_keys=ON",
        )
    try:
        version = migrations.recorded_version(conn)
        if version != migrations.AUTONOMOUS_SCHEMA_VERSION:
            raise _error(
                "schema_migration_required",
                "the exact current autonomous schema version is required",
            )
        # The public migration guard validates all prerequisite shapes plus
        # Migration 004's complete table/index/trigger inventory.
        migrations.require_version(conn, migrations.AUTONOMOUS_SCHEMA_VERSION)
    except migrations.SchemaError as exc:
        raise _error(exc.code, str(exc), validation=exc.validation) from exc


def _range(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _error("invalid_engine_output", f"{name} must be an object")
    allowed = frozenset({"kind", "from", "to"})
    _exact_mapping(value, allowed, name)
    kind = _enum(value["kind"], frozenset({"all", "bounded"}), f"{name}.kind")
    start = _date(value["from"], f"{name}.from", nullable=True)
    end = _date(value["to"], f"{name}.to", nullable=True)
    if (start is None) != (end is None):
        raise _error("invalid_engine_output", f"{name} bounds must both be null or dates")
    if start is not None and start > end:
        raise _error("invalid_engine_output", f"{name}.from follows {name}.to")
    if kind == "bounded" and start is None:
        raise _error("invalid_engine_output", f"{name} bounded range requires dates")
    return {"kind": kind, "from": start, "to": end}


def _component_identity(component: Mapping[str, Any], name: str) -> dict[str, Any]:
    _exact_mapping(component, _COMPONENT_KEYS, name)
    exposure_key = _feature(component["exposure_key"], f"{name}.exposure_key")
    lag_days = _integer(component["lag_days"], f"{name}.lag_days", maximum=365)
    window_days = _integer(component["window_days"], f"{name}.window_days", minimum=1)
    if window_days not in _WINDOWS:
        raise _error("invalid_engine_output", f"{name}.window_days is not allowed")
    transform = _enum(component["transform"], _TRANSFORMS, f"{name}.transform")
    temporal = _enum(
        component["temporal_direction"],
        _TEMPORAL_DIRECTIONS,
        f"{name}.temporal_direction",
    )
    expected_temporal = (
        "same_day_or_order_unknown"
        if lag_days == 0
        else "exposure_precedes_outcome"
    )
    if temporal != expected_temporal:
        raise _error(
            "mismatched_engine_output",
            f"{name}.temporal_direction does not match lag_days",
        )
    for field in (
        "display",
        "unit",
        "temporal_type",
        "direction",
        "merge_rule",
        "zero_semantics",
    ):
        if component[field] is not None and not isinstance(component[field], str):
            raise _error("invalid_engine_output", f"{name}.{field} must be text or null")
    return {
        "exposure_key": exposure_key,
        "lag_days": lag_days,
        "window_days": window_days,
        "transform": transform,
        "temporal_direction": temporal,
    }


def _effect_direction(finding: Mapping[str, Any]) -> str:
    effect = finding.get("effect")
    if not isinstance(effect, Mapping):
        raise _error("invalid_engine_output", "finding.effect must be an object")
    value = effect.get("oriented_estimate")
    if value is None:
        return "unknown"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error("nonfinite_engine_output", "oriented effect must be finite or null")
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "unknown"


def _evidence_fingerprint_for(
    finding: Mapping[str, Any],
    analysis_version: str,
) -> str:
    missing = [
        field for field in _EVIDENCE_FINGERPRINT_FIELDS if field not in finding
    ]
    if missing:
        raise _error(
            "ledger_corrupt",
            f"finding evidence is missing {missing[0]}",
        )
    return sha256_id(
        {
            "contract_version": ANALYSIS_CONTRACT_VERSION,
            "analysis_version": analysis_version,
            "finding": {
                field: finding[field] for field in _EVIDENCE_FINGERPRINT_FIELDS
            },
        }
    )


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error("invalid_engine_output", f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise _error("nonfinite_engine_output", f"{name} must be finite")
    return result


def _optional_finite_number(value: Any, name: str) -> float | None:
    return None if value is None else _finite_number(value, name)


def _nonnegative_count(value: Any, name: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _error(
            "invalid_engine_output",
            f"{name} must be a nonnegative integer",
        )
    return value


def _probability(value: Any, name: str) -> float:
    result = _finite_number(value, name)
    if result < 0 or result > 1:
        raise _error("invalid_engine_output", f"{name} must be between zero and one")
    return result


def _numerically_equal(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)


def _validate_interval(value: Any, name: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise _error("invalid_engine_output", f"{name} must be a two-value interval")
    lower = _finite_number(value[0], f"{name}[0]")
    upper = _finite_number(value[1], f"{name}[1]")
    if lower > upper:
        raise _error("invalid_engine_output", f"{name} bounds are reversed")
    return [lower, upper]


def _validate_sample_counts(sample: Mapping[str, Any]) -> int:
    eligible = _nonnegative_count(sample.get("eligible_n"), "sample.eligible_n")
    complete = _nonnegative_count(sample.get("complete_n"), "sample.complete_n")
    missing = _nonnegative_count(sample.get("missing_n"), "sample.missing_n")
    assert eligible is not None and complete is not None and missing is not None
    if complete > eligible or missing != eligible - complete:
        raise _error(
            "invalid_engine_output",
            "sample complete/missing counts do not partition eligible_n",
        )
    for left_name, right_name in (
        ("exposed_n", "unexposed_n"),
        ("outcome_positive_n", "outcome_negative_n"),
    ):
        left = _nonnegative_count(
            sample.get(left_name), f"sample.{left_name}", nullable=True
        )
        right = _nonnegative_count(
            sample.get(right_name), f"sample.{right_name}", nullable=True
        )
        if (left is None) != (right is None):
            raise _error(
                "invalid_engine_output",
                f"sample {left_name}/{right_name} must both be null or counts",
            )
        if left is not None and right is not None and left + right != complete:
            raise _error(
                "invalid_engine_output",
                f"sample {left_name}/{right_name} do not partition complete_n",
            )

    return complete


def _validate_effect_orientation(
    *,
    estimate: float | None,
    oriented: float | None,
    raw_ci: list[float] | None,
    oriented_ci: list[float] | None,
) -> set[int]:
    if (estimate is None) != (oriented is None):
        raise _error(
            "invalid_engine_output",
            "raw and oriented effects must both be null or finite",
        )
    orientation_candidates: set[int] = set()
    if estimate is not None and oriented is not None:
        if _numerically_equal(oriented, estimate):
            orientation_candidates.add(1)
        if _numerically_equal(oriented, -estimate):
            orientation_candidates.add(-1)
        if not orientation_candidates:
            raise _error(
                "invalid_engine_output",
                "oriented effect must equal the raw effect times one orientation",
            )
    if (raw_ci is None) != (oriented_ci is None):
        raise _error(
            "invalid_engine_output",
            "raw and oriented confidence intervals must have matching nullability",
        )
    if (
        raw_ci is not None
        and oriented_ci is not None
        and orientation_candidates
    ):
        orientation_candidates = {
            orientation
            for orientation in orientation_candidates
            if all(
                _numerically_equal(actual, expected)
                for actual, expected in zip(
                    oriented_ci,
                    (
                        raw_ci
                        if orientation == 1
                        else [-raw_ci[1], -raw_ci[0]]
                    ),
                    strict=True,
                )
            )
        }
        if not orientation_candidates:
            raise _error(
                "invalid_engine_output",
                "oriented confidence interval disagrees with effect orientation",
            )
    return orientation_candidates


def _validate_numeric_finding(
    finding: Mapping[str, Any],
    *,
    component_count: int,
) -> None:
    sample = finding["sample"]
    complete = _validate_sample_counts(sample)
    rates = finding["rates"]
    effect = finding["effect"]
    testing = finding["testing"]
    method = effect.get("method")
    estimate = _optional_finite_number(effect.get("estimate"), "effect.estimate")
    oriented = _optional_finite_number(
        effect.get("oriented_estimate"), "effect.oriented_estimate"
    )
    raw_ci = _validate_interval(effect.get("ci95"), "effect.ci95")
    oriented_ci = _validate_interval(
        effect.get("oriented_ci95"), "effect.oriented_ci95"
    )
    orientation_candidates = _validate_effect_orientation(
        estimate=estimate,
        oriented=oriented,
        raw_ci=raw_ci,
        oriented_ci=oriented_ci,
    )
    if method is None:
        if any(value is not None for value in (estimate, oriented, raw_ci, oriented_ci)):
            raise _error(
                "invalid_engine_output",
                "an absent effect method requires null effect values",
            )
        if rates is not None:
            raise _error(
                "invalid_engine_output",
                "an absent effect method cannot have rates",
            )
    elif not isinstance(method, str) or not method:
        raise _error("invalid_engine_output", "effect.method is invalid")
    elif method not in {
        "risk_difference",
        "spearman",
        "incremental_risk_difference",
    }:
        raise _error("invalid_engine_output", "effect.method is unknown")
    elif estimate is None:
        raise _error(
            "invalid_engine_output",
            "a computed effect method requires finite estimates",
        )

    p_value = testing.get("p")
    q_value = testing.get("q")
    for value, name in ((p_value, "testing.p"), (q_value, "testing.q")):
        if value is not None:
            _probability(value, name)
    family_size = _nonnegative_count(
        testing.get("family_size"), "testing.family_size", nullable=True
    )
    seed = _nonnegative_count(testing.get("seed"), "testing.seed", nullable=True)
    for key in ("permutation_iterations", "bootstrap_iterations"):
        _nonnegative_count(testing.get(key), f"testing.{key}")
    testing_method = testing.get("method")
    if method is None:
        if any(
            value is not None
            for value in (p_value, q_value, family_size, seed, testing_method)
        ):
            raise _error(
                "invalid_engine_output",
                "an uncomputed effect cannot have test statistics",
            )
    elif (
        p_value is None
        or q_value is None
        or seed is None
        or family_size is None
        or family_size < 1
        or not isinstance(testing_method, str)
        or not testing_method
    ):
        raise _error(
            "invalid_engine_output",
            "a computed effect requires a positive family and test method",
        )
    expected_testing = {
        "risk_difference": ("fisher_exact", 0, 0),
        "spearman": (
            "spearman_permutation",
            associations.PERMUTATION_ITERATIONS,
            associations.BOOTSTRAP_ITERATIONS,
        ),
        "incremental_risk_difference": (
            "source_era_stratified_permutation",
            associations.PERMUTATION_ITERATIONS,
            associations.BOOTSTRAP_ITERATIONS,
        ),
    }
    if method is not None:
        expected_method, expected_permutations, expected_bootstraps = (
            expected_testing[method]
        )
        if (
            testing_method != expected_method
            or testing["permutation_iterations"] != expected_permutations
            or testing["bootstrap_iterations"] != expected_bootstraps
        ):
            raise _error(
                "invalid_engine_output",
                "effect and testing methods/iterations are inconsistent",
            )

    stability = finding["stability"]
    stability_status = stability.get("status")
    for key, value in stability.items():
        if key != "status" and value is not None:
            _finite_number(value, f"stability.{key}")
    stability_full = stability.get("full")
    if (
        stability_full is not None
        and oriented is not None
        and not _numerically_equal(float(stability_full), oriented)
    ):
        raise _error(
            "invalid_engine_output",
            "stability.full must equal the oriented full-sample effect",
        )
    if component_count == 2:
        if (
            stability_status != "not_applicable"
            or stability_full is None
            or stability.get("first_half") is not None
            or stability.get("second_half") is not None
        ):
            raise _error(
                "invalid_engine_output",
                "interaction stability must be not_applicable with only the full effect",
            )
    else:
        _enum(
            stability_status,
            frozenset({"stable", "unstable", "insufficient"}),
            "stability.status",
        )
        if method is None:
            if (
                stability_status != "insufficient"
                or stability_full is not None
                or stability.get("first_half") is not None
                or stability.get("second_half") is not None
            ):
                raise _error(
                    "invalid_engine_output",
                    "an uncomputed single effect requires null insufficient stability",
                )
        elif stability_full is None:
            raise _error(
                "invalid_engine_output",
                "a computed single effect requires full-sample stability",
            )
        else:
            first_half = stability.get("first_half")
            second_half = stability.get("second_half")
            if first_half is None or second_half is None or stability_full == 0:
                expected_stability = "insufficient"
            else:
                sign = 1 if float(stability_full) > 0 else -1
                expected_stability = (
                    "stable"
                    if (
                        float(first_half) * sign > 0
                        and float(second_half) * sign > 0
                        and abs(float(first_half))
                        >= 0.5 * abs(float(stability_full))
                        and abs(float(second_half))
                        >= 0.5 * abs(float(stability_full))
                    )
                    else "unstable"
                )
            if stability_status != expected_stability:
                raise _error(
                    "mismatched_engine_output",
                    "stability.status disagrees with the frozen chronological-half gates",
                )
    confounder_effect = finding["confounders"].get("weighted_effect")
    if confounder_effect is not None:
        _finite_number(confounder_effect, "confounders.weighted_effect")

    if rates is None:
        if method in {"risk_difference", "incremental_risk_difference"}:
            raise _error(
                "invalid_engine_output",
                f"{method} requires a rates object",
            )
        if (
            method == "spearman"
            and estimate is not None
            and oriented is not None
            and (abs(estimate) > 1 or abs(oriented) > 1)
        ):
            raise _error(
                "invalid_engine_output",
                "Spearman estimates must lie between -1 and 1",
            )
        return
    if not isinstance(rates, Mapping):
        raise _error("invalid_engine_output", "finding.rates must be an object or null")

    if component_count == 1 and method == "risk_difference":
        required = {
            "baseline",
            "exposed",
            "unexposed",
            "risk_difference",
            "risk_ratio",
            "oriented_risk_difference",
        }
        if set(rates) != required:
            raise _error("invalid_engine_output", "risk-difference rate fields mismatch")
        baseline = _probability(rates["baseline"], "rates.baseline")
        exposed_rate = _probability(rates["exposed"], "rates.exposed")
        unexposed_rate = _probability(rates["unexposed"], "rates.unexposed")
        risk_difference = _finite_number(
            rates["risk_difference"], "rates.risk_difference"
        )
        oriented_difference = _finite_number(
            rates["oriented_risk_difference"],
            "rates.oriented_risk_difference",
        )
        if not _numerically_equal(risk_difference, exposed_rate - unexposed_rate):
            raise _error(
                "invalid_engine_output",
                "risk_difference does not equal exposed minus unexposed",
            )
        if estimate is None or oriented is None or not (
            _numerically_equal(estimate, risk_difference)
            and _numerically_equal(oriented, oriented_difference)
        ):
            raise _error(
                "invalid_engine_output",
                "risk-difference rates disagree with effect estimates",
            )
        expected_ratio = (
            None if unexposed_rate == 0 else exposed_rate / unexposed_rate
        )
        actual_ratio = rates["risk_ratio"]
        if expected_ratio is None:
            if actual_ratio is not None:
                raise _error(
                    "invalid_engine_output",
                    "risk_ratio must be null for a zero denominator",
                )
        elif actual_ratio is None or not _numerically_equal(
            _finite_number(actual_ratio, "rates.risk_ratio"), expected_ratio
        ):
            raise _error("invalid_engine_output", "risk_ratio is inconsistent")
        exposed_n = sample["exposed_n"]
        unexposed_n = sample["unexposed_n"]
        for rate, count, name in (
            (exposed_rate, exposed_n, "exposed"),
            (unexposed_rate, unexposed_n, "unexposed"),
        ):
            if (
                count is not None
                and abs(rate * count - round(rate * count)) > 1e-9
            ):
                raise _error(
                    "invalid_engine_output",
                    f"{name} rate implies a fractional outcome count",
                )
        if exposed_n is not None and complete:
            expected_baseline = (
                exposed_rate * exposed_n + unexposed_rate * unexposed_n
            ) / complete
            if not _numerically_equal(baseline, expected_baseline):
                raise _error(
                    "invalid_engine_output",
                    "baseline rate is inconsistent with exposure groups",
                )
        positive_n = sample["outcome_positive_n"]
        if (
            positive_n is not None
            and complete
            and abs(baseline * complete - positive_n) > 0.5 + 1e-9
        ):
            raise _error(
                "invalid_engine_output",
                "outcome class counts are inconsistent with baseline rate",
            )
        return

    if component_count == 2 and method == "incremental_risk_difference":
        required = {
            "baseline",
            "neither",
            "a_only",
            "b_only",
            "both",
            "component_a_difference",
            "component_b_difference",
            "incremental_risk_difference",
            "oriented_incremental_risk_difference",
            "oriented_component_a_difference",
            "oriented_component_b_difference",
        }
        if set(rates) != required:
            raise _error("invalid_engine_output", "interaction rate fields mismatch")
        neither = _probability(rates["neither"], "rates.neither")
        a_only = _probability(rates["a_only"], "rates.a_only")
        b_only = _probability(rates["b_only"], "rates.b_only")
        both = _probability(rates["both"], "rates.both")
        baseline = _probability(rates["baseline"], "rates.baseline")
        if not _numerically_equal(baseline, neither):
            raise _error("invalid_engine_output", "interaction baseline must equal neither")
        component_a = _finite_number(
            rates["component_a_difference"], "rates.component_a_difference"
        )
        component_b = _finite_number(
            rates["component_b_difference"], "rates.component_b_difference"
        )
        incremental = _finite_number(
            rates["incremental_risk_difference"],
            "rates.incremental_risk_difference",
        )
        oriented_incremental = _finite_number(
            rates["oriented_incremental_risk_difference"],
            "rates.oriented_incremental_risk_difference",
        )
        oriented_component_a = _finite_number(
            rates["oriented_component_a_difference"],
            "rates.oriented_component_a_difference",
        )
        oriented_component_b = _finite_number(
            rates["oriented_component_b_difference"],
            "rates.oriented_component_b_difference",
        )
        if not (
            _numerically_equal(component_a, a_only - neither)
            and _numerically_equal(component_b, b_only - neither)
            and _numerically_equal(
                incremental,
                both - neither - max(a_only - neither, b_only - neither),
            )
            and estimate is not None
            and oriented is not None
            and _numerically_equal(estimate, incremental)
            and _numerically_equal(oriented, oriented_incremental)
            and any(
                _numerically_equal(
                    oriented_component_a,
                    component_a * orientation,
                )
                and _numerically_equal(
                    oriented_component_b,
                    component_b * orientation,
                )
                for orientation in orientation_candidates
            )
        ):
            raise _error(
                "invalid_engine_output",
                "interaction rates disagree with component/effect estimates",
            )
        cells = sample.get("interaction_cells")
        if not isinstance(cells, Mapping) or set(cells) != {
            "neither",
            "a_only",
            "b_only",
            "both",
        }:
            raise _error(
                "invalid_engine_output",
                "interaction cell-count fields mismatch",
            )
        cell_total = sum(
            _nonnegative_count(value, f"sample.interaction_cells.{key}") or 0
            for key, value in cells.items()
        )
        if cell_total != complete:
            raise _error(
                "invalid_engine_output",
                "interaction cells do not partition complete_n",
            )
        for rate, key in (
            (neither, "neither"),
            (a_only, "a_only"),
            (b_only, "b_only"),
            (both, "both"),
        ):
            if abs(rate * cells[key] - round(rate * cells[key])) > 1e-9:
                raise _error(
                    "invalid_engine_output",
                    f"interaction {key} rate implies a fractional outcome count",
                )
        expected_positive = (
            neither * cells["neither"]
            + a_only * cells["a_only"]
            + b_only * cells["b_only"]
            + both * cells["both"]
        )
        positive_n = sample["outcome_positive_n"]
        if (
            positive_n is None
            or abs(expected_positive - positive_n) > 0.5 + 1e-9
        ):
            raise _error(
                "invalid_engine_output",
                "interaction outcome counts disagree with cell rates",
            )
        return

    raise _error(
        "invalid_engine_output",
        "rates/effect method do not match the candidate kind",
    )


def _verify_finding(
    finding: Any,
    meta: Mapping[str, Any],
    *,
    audit: bool,
    definition_descriptors: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    allowed = _AUDIT_FINDING_KEYS if audit else _FINDING_KEYS
    mapping = _exact_mapping(finding, allowed, "finding")
    clean = _canonical_copy(mapping, "finding")

    outcome = _exact_mapping(
        clean["outcome"], _OUTCOME_DESCRIPTOR_KEYS, "finding.outcome"
    )
    outcome_key = _feature(outcome.get("key"), "finding.outcome.key")
    outcome_mode = _text(
        outcome.get("mode"),
        "finding.outcome.mode",
        maximum=80,
        token=True,
    )
    if outcome_key != meta["outcome"] or outcome_mode not in meta["modes"]:
        raise _error("mismatched_engine_output", "finding outcome does not match run metadata")
    expected_outcome = definition_descriptors.get(outcome_key)
    if expected_outcome is None:
        raise _error("mismatched_engine_output", "finding outcome is absent from registry")
    for field in (
        "display",
        "unit",
        "temporal_type",
        "direction",
        "merge_rule",
        "zero_semantics",
    ):
        if outcome.get(field) != expected_outcome[field]:
            raise _error(
                "mismatched_engine_output",
                f"finding outcome {field} disagrees with registry",
            )

    exposure = clean["exposure"]
    if not isinstance(exposure, Mapping) or set(exposure) != {"components"}:
        raise _error("invalid_engine_output", "finding.exposure fields mismatch")
    components_value = exposure["components"]
    if not isinstance(components_value, list) or len(components_value) not in (1, 2):
        raise _error("invalid_engine_output", "finding must have one or two components")
    components = [
        _component_identity(value, f"finding.component[{index}]")
        for index, value in enumerate(components_value)
    ]
    if len({item["exposure_key"] for item in components}) != len(components):
        raise _error("invalid_engine_output", "finding component exposures must be distinct")
    for component in components_value:
        expected_component = definition_descriptors.get(component["exposure_key"])
        if expected_component is None:
            raise _error(
                "mismatched_engine_output",
                "finding component is absent from registry",
            )
        for field in (
            "display",
            "unit",
            "temporal_type",
            "direction",
            "merge_rule",
            "zero_semantics",
        ):
            if component.get(field) != expected_component[field]:
                raise _error(
                    "mismatched_engine_output",
                    f"finding component {field} disagrees with registry",
                )
    identity_components = [
        {
            "exposure_key": item["exposure_key"],
            "lag_days": item["lag_days"],
            "window_days": item["window_days"],
            "transform": item["transform"],
        }
        for item in components
    ]
    expected_candidate = candidate_key(outcome_key, outcome_mode, identity_components)
    if clean["candidate_key"] != expected_candidate:
        raise _error("mismatched_engine_output", "candidate key does not match components")

    expected_finding = finding_id(
        candidate_key=expected_candidate,
        analysis_range=meta["analysis_range"],
        baseline_range=meta["baseline_range"],
        input_fingerprint=meta["input_fingerprint"],
    )
    if clean["finding_id"] != expected_finding:
        raise _error("mismatched_engine_output", "finding ID does not match provenance")

    quality = clean["quality"]
    if not isinstance(quality, Mapping) or set(quality) != {
        "tier",
        "eligible_for_hypothesis",
    }:
        raise _error("invalid_engine_output", "finding.quality fields mismatch")
    tier = _enum(quality["tier"], _QUALITY_TIERS, "finding.quality.tier")
    if type(quality["eligible_for_hypothesis"]) is not bool:
        raise _error("invalid_engine_output", "finding eligibility must be boolean")
    if tier == "insufficient" and quality["eligible_for_hypothesis"]:
        raise _error("mismatched_engine_output", "insufficient evidence cannot be eligible")

    provenance = _exact_mapping(
        clean["provenance"], _PROVENANCE_KEYS, "finding.provenance"
    )
    for field in (
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
        "evidence_fingerprint",
    ):
        if field not in provenance:
            raise _error("invalid_engine_output", f"finding provenance missing {field}")
    for field in (
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
    ):
        if provenance[field] != meta[field]:
            raise _error("mismatched_engine_output", f"finding provenance {field} mismatch")
    stored_evidence = _sha(provenance["evidence_fingerprint"], "evidence_fingerprint")
    computed_evidence = _evidence_fingerprint_for(
        clean, provenance["analysis_version"]
    )
    if stored_evidence != computed_evidence:
        raise _error("mismatched_engine_output", "evidence fingerprint mismatch")

    for field in ("evidence_for", "evidence_against", "warnings"):
        if not isinstance(clean[field], list):
            raise _error("invalid_engine_output", f"finding.{field} must be an array")
    sample_allowed = (
        _SAMPLE_KEYS | frozenset({"interaction_cells"})
        if len(components) == 2
        else _SAMPLE_KEYS
    )
    _exact_mapping(clean["sample"], sample_allowed, "finding.sample")
    _exact_mapping(clean["effect"], _EFFECT_KEYS, "finding.effect")
    _exact_mapping(clean["testing"], _TESTING_KEYS, "finding.testing")
    _exact_mapping(clean["stability"], _STABILITY_KEYS, "finding.stability")
    _exact_mapping(
        clean["confounders"], _CONFOUNDER_KEYS, "finding.confounders"
    )
    for polarity in ("evidence_for", "evidence_against"):
        for index, item in enumerate(clean[polarity]):
            if (
                not isinstance(item, Mapping)
                or set(item) not in (
                    {"code", "value", "threshold"},
                    {"code", "value", "threshold", "detail"},
                )
            ):
                raise _error(
                    "invalid_engine_output",
                    f"finding.{polarity}[{index}] fields mismatch",
                )
    if clean["rates"] is not None and not isinstance(clean["rates"], Mapping):
        raise _error("invalid_engine_output", "finding.rates must be an object or null")
    _validate_numeric_finding(clean, component_count=len(components))

    testing = clean["testing"]
    family_map = (
        meta["candidate_family_sizes"]
        if len(components) == 1
        else meta["interaction_family_sizes"]
    )
    if testing["family_size"] is not None and testing["family_size"] != family_map.get(
        outcome_mode
    ):
        raise _error(
            "mismatched_engine_output",
            "finding family_size disagrees with analysis metadata",
        )
    if (
        testing["p"] is not None
        and testing["q"] is not None
        and testing["family_size"] is not None
    ):
        p_value = float(testing["p"])
        q_value = float(testing["q"])
        upper_bound = min(1.0, p_value * int(testing["family_size"]))
        if q_value < p_value and not _numerically_equal(q_value, p_value):
            raise _error(
                "invalid_engine_output",
                "BH-adjusted q cannot be below its raw p value",
            )
        if q_value > upper_bound and not _numerically_equal(
            q_value, upper_bound
        ):
            raise _error(
                "invalid_engine_output",
                "BH-adjusted q exceeds the maximum possible family adjustment",
            )

    effect = clean["effect"]
    oriented_effect = effect["oriented_estimate"]
    context_only = any(
        item.get("code") == "autoregressive_lineage"
        for item in clean["evidence_against"]
        if isinstance(item, Mapping)
    )
    if len(components) == 2:
        interval = effect["oriented_ci95"]
        discovery_pass = (
            oriented_effect is not None
            and abs(float(oriented_effect)) >= 0.10
            and testing["q"] is not None
            and float(testing["q"]) <= 0.05
            and interval is not None
            and not (interval[0] <= 0 <= interval[1])
        )
        expected_tier = (
            "exploratory_unreplicated" if discovery_pass else "insufficient"
        )
        expected_eligible = discovery_pass and not context_only
    else:
        threshold = 0.10 if effect["method"] == "risk_difference" else 0.20
        effect_pass = (
            oriented_effect is not None
            and abs(float(oriented_effect)) >= threshold
        )
        discovery_pass = (
            effect_pass
            and testing["q"] is not None
            and float(testing["q"]) <= 0.10
            and clean["stability"]["status"] == "stable"
        )
        expected_tier = (
            "insufficient"
            if not effect_pass
            else (
                "exploratory_unreplicated"
                if discovery_pass
                else "exploratory_screen"
            )
        )
        expected_eligible = effect_pass and not context_only
    if tier != expected_tier or quality["eligible_for_hypothesis"] != expected_eligible:
        raise _error(
            "mismatched_engine_output",
            "finding quality does not match deterministic numeric gates",
        )

    return clean


def _validate_engine_meta(meta: Mapping[str, Any]) -> None:
    _timestamp(meta.get("generated_at"), "meta.generated_at")
    if meta.get("timezone") != TIMEZONE_NAME:
        raise _error(
            "invalid_engine_output",
            f"analysis timezone must be {TIMEZONE_NAME}",
        )
    if meta.get("min_n") != associations.DEFAULT_MIN_N:
        raise _error("invalid_engine_output", "analysis min_n is not ledger-safe")
    if meta.get("interactions") not in {"none", "pairwise"}:
        raise _error("invalid_engine_output", "analysis interaction mode is invalid")
    if meta.get("top") != 100:
        raise _error("invalid_engine_output", "analysis top boundary must be 100")
    for field in ("candidate_family_sizes", "interaction_family_sizes"):
        values = meta.get(field)
        if not isinstance(values, Mapping):
            raise _error("invalid_engine_output", f"meta.{field} must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise _error("invalid_engine_output", f"meta.{field} key is invalid")
            _nonnegative_count(value, f"meta.{field}.{key}")


def _validate_analysis_coverage(payload: Mapping[str, Any]) -> None:
    """Bind Phase 4 family metadata, coverage counts, and returned findings."""

    meta = payload["meta"]
    coverage = _exact_mapping(
        payload["coverage"], _COVERAGE_KEYS, "analysis coverage"
    )
    eligible_n = _nonnegative_count(
        coverage["outcome_eligible_n"], "coverage.outcome_eligible_n"
    )
    modes = coverage["modes"]
    if not isinstance(modes, Mapping) or set(modes) != set(meta["modes"]):
        raise _error(
            "mismatched_engine_output",
            "coverage modes do not match the requested analysis modes",
        )
    if not isinstance(coverage["source_manifests"], list):
        raise _error(
            "invalid_engine_output",
            "coverage.source_manifests must be an array",
        )
    if not isinstance(coverage["dependencies"], Mapping):
        raise _error(
            "invalid_engine_output",
            "coverage.dependencies must be an object",
        )
    candidate_sizes = meta["candidate_family_sizes"]
    interaction_sizes = meta["interaction_family_sizes"]
    if set(candidate_sizes) != set(meta["modes"]):
        raise _error(
            "mismatched_engine_output",
            "candidate family modes do not match analysis modes",
        )
    expected_interaction_modes = (
        set(meta["modes"]) if meta["interactions"] == "pairwise" else set()
    )
    if set(interaction_sizes) != expected_interaction_modes:
        raise _error(
            "mismatched_engine_output",
            "interaction family modes do not match interaction selection",
        )

    single_counts = {mode: 0 for mode in meta["modes"]}
    pair_counts = {mode: 0 for mode in meta["modes"]}
    findings = payload["findings"]
    if not isinstance(findings, list):
        raise _error(
            "invalid_engine_output", "analysis findings must be an array"
        )
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise _error(
                "invalid_engine_output", "analysis finding must be an object"
            )
        outcome = finding.get("outcome")
        exposure = finding.get("exposure")
        mode = outcome.get("mode") if isinstance(outcome, Mapping) else None
        components = (
            exposure.get("components") if isinstance(exposure, Mapping) else None
        )
        if mode not in single_counts or not isinstance(components, list):
            raise _error(
                "mismatched_engine_output",
                "finding mode/components disagree with analysis coverage",
            )
        if len(components) == 1:
            single_counts[mode] += 1
        elif len(components) == 2:
            pair_counts[mode] += 1
        else:
            raise _error(
                "invalid_engine_output",
                "finding component count is outside the frozen contract",
            )

    for mode in meta["modes"]:
        current = _exact_mapping(
            modes[mode], _MODE_COVERAGE_KEYS, f"coverage.modes.{mode}"
        )
        counts = {
            key: _nonnegative_count(
                current[key], f"coverage.modes.{mode}.{key}"
            )
            for key in _MODE_COVERAGE_KEYS
        }
        if counts["outcome_eligible_n"] != eligible_n:
            raise _error(
                "mismatched_engine_output",
                "mode outcome coverage disagrees with overall coverage",
            )
        if (
            counts["family_size"] != counts["tested_candidates"]
            or candidate_sizes[mode] != counts["tested_candidates"]
        ):
            raise _error(
                "mismatched_engine_output",
                "candidate family metadata disagrees with tested coverage",
            )
        retained = (
            counts["tested_candidates"] + counts["insufficient_candidates"]
        )
        if retained > counts["generated_candidates"]:
            raise _error(
                "invalid_engine_output",
                "retained candidate counts exceed generated candidates",
            )
        if single_counts[mode] != retained:
            raise _error(
                "mismatched_engine_output",
                "returned single findings disagree with candidate coverage",
            )
        if pair_counts[mode] > interaction_sizes.get(mode, 0):
            raise _error(
                "mismatched_engine_output",
                "returned pair findings exceed their interaction family",
            )


def _definition_descriptor_map(
    definitions: Iterable[Any] | Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    values = definitions.values() if isinstance(definitions, Mapping) else definitions
    result: dict[str, dict[str, Any]] = {}
    for raw in values:
        get = raw.get if isinstance(raw, Mapping) else lambda key: getattr(raw, key)
        key = get("key")
        source = get("source")
        if (
            not isinstance(key, str)
            or key in result
            or not isinstance(source, Mapping)
        ):
            raise _error(
                "registry_contract_error",
                "registry descriptor definitions are invalid",
            )
        result[key] = {
            "display": get("display_name"),
            "unit": get("unit"),
            "temporal_type": get("temporal_type"),
            "direction": get("direction"),
            "merge_rule": source.get("merge"),
            "zero_semantics": get("zero_semantics"),
        }
    return result


def _seal_engine_analysis_output(
    result: Any,
    *,
    expected_registry_sha256: str,
    definitions: Iterable[Any] | Mapping[str, Any],
) -> _VerifiedPhase4Analysis:
    """Validate and privately seal one direct Phase 4 engine result."""

    mapping = _exact_mapping(result, _ANALYSIS_KEYS, "analysis result")
    clean = _canonical_copy(mapping, "analysis result")
    if clean["ok"] is not True or clean["contract_version"] != ANALYSIS_CONTRACT_VERSION:
        raise _error("invalid_engine_output", "analysis result contract is not successful Phase 4")
    meta = _exact_mapping(clean["meta"], _META_KEYS, "analysis meta")
    _validate_engine_meta(meta)
    if meta["analysis_version"] != ANALYSIS_VERSION:
        raise _error("incompatible_analysis_version", "analysis semantic version is not current")
    if meta["registry_version"] != REGISTRY_VERSION:
        raise _error("incompatible_registry_version", "registry semantic version is not current")
    if meta["engine_sha256"] != engine_sha256():
        raise _error("stale_engine_output", "analysis engine hash is not current")
    expected_registry_sha256 = _sha(
        expected_registry_sha256, "expected_registry_sha256"
    )
    if meta["registry_sha256"] != expected_registry_sha256:
        raise _error(
            "stale_registry_output",
            "analysis registry hash does not match supplied definitions",
        )
    _sha(meta["input_fingerprint"], "input_fingerprint")
    _feature(meta["outcome"], "analysis outcome")
    if (
        not isinstance(meta["modes"], list)
        or len(meta["modes"]) != 1
        or not isinstance(meta["modes"][0], str)
    ):
        raise _error(
            "invalid_engine_output",
            "persisted analysis runs require one explicit outcome mode",
        )
    _range(meta["requested_range"], "requested_range")
    meta["analysis_range"] = _range(meta["analysis_range"], "analysis_range")
    meta["baseline_range"] = _range(meta["baseline_range"], "baseline_range")
    descriptors = _definition_descriptor_map(definitions)
    if not isinstance(clean["findings"], list):
        raise _error("invalid_engine_output", "analysis findings must be an array")
    clean["findings"] = [
        _verify_finding(
            item,
            meta,
            audit=False,
            definition_descriptors=descriptors,
        )
        for item in clean["findings"]
    ]
    finding_ids = [item["finding_id"] for item in clean["findings"]]
    if len(finding_ids) != len(set(finding_ids)):
        raise _error("invalid_engine_output", "analysis contains duplicate findings")
    if not isinstance(clean["coverage"], Mapping):
        raise _error("invalid_engine_output", "analysis coverage must be an object")
    if not isinstance(clean["suppression_counts"], Mapping):
        raise _error("invalid_engine_output", "analysis suppression counts must be an object")
    if not isinstance(clean["warnings"], list):
        raise _error("invalid_engine_output", "analysis warnings must be an array")
    _validate_analysis_coverage(clean)
    return _VerifiedPhase4Analysis(canonical_json(clean), _VERIFIED_TOKEN)


def _seal_engine_finding_output(
    result: Any,
    *,
    expected_registry_sha256: str,
    definitions: Iterable[Any] | Mapping[str, Any],
) -> _VerifiedPhase4Finding:
    """Validate and privately seal one direct Phase 4 replay response."""

    mapping = _exact_mapping(
        result,
        frozenset({"ok", "contract_version", "meta", "finding"}),
        "finding evidence result",
    )
    clean = _canonical_copy(mapping, "finding evidence result")
    if clean["ok"] is not True or clean["contract_version"] != ANALYSIS_CONTRACT_VERSION:
        raise _error("invalid_engine_output", "finding evidence contract is invalid")
    meta = _exact_mapping(clean["meta"], _META_KEYS, "finding evidence meta")
    _validate_engine_meta(meta)
    if meta["analysis_version"] != ANALYSIS_VERSION:
        raise _error("incompatible_analysis_version", "analysis semantic version is not current")
    if meta["registry_version"] != REGISTRY_VERSION:
        raise _error("incompatible_registry_version", "registry semantic version is not current")
    if meta["engine_sha256"] != engine_sha256():
        raise _error("stale_engine_output", "finding engine hash is not current")
    expected_registry_sha256 = _sha(
        expected_registry_sha256, "expected_registry_sha256"
    )
    if meta["registry_sha256"] != expected_registry_sha256:
        raise _error(
            "stale_registry_output",
            "finding registry hash does not match supplied definitions",
        )
    _sha(meta["input_fingerprint"], "input_fingerprint")
    _feature(meta["outcome"], "finding outcome")
    if not isinstance(meta["modes"], list) or not meta["modes"]:
        raise _error("invalid_engine_output", "finding evidence modes are invalid")
    meta["analysis_range"] = _range(meta["analysis_range"], "analysis_range")
    meta["baseline_range"] = _range(meta["baseline_range"], "baseline_range")
    _range(meta["requested_range"], "requested_range")
    clean["finding"] = _verify_finding(
        clean["finding"],
        meta,
        audit=True,
        definition_descriptors=_definition_descriptor_map(definitions),
    )
    return _VerifiedPhase4Finding(canonical_json(clean), _VERIFIED_TOKEN)


def _materialized_definitions(
    definitions: Iterable[Any] | Mapping[str, Any],
) -> tuple[tuple[Any, ...], str]:
    values = tuple(
        definitions.values() if isinstance(definitions, Mapping) else definitions
    )
    try:
        checksum = registry_content_checksum(values)
    except Exception as exc:
        raise _error(
            "registry_contract_error",
            "registry definitions cannot be canonically checksummed",
        ) from exc
    return values, f"sha256:{checksum}"


def _strip_internal_findings(result: Mapping[str, Any]) -> dict[str, Any]:
    """Strip Phase 4 calculation objects before validation or persistence."""

    public = dict(result)
    findings = public.get("findings")
    if not isinstance(findings, list):
        raise _error("invalid_engine_output", "analysis findings must be an array")
    public["findings"] = [
        {
            key: value
            for key, value in finding.items()
            if not str(key).startswith("_")
        }
        if isinstance(finding, Mapping)
        else finding
        for finding in findings
    ]
    return public


def compute_verified_analysis(
    conn: sqlite3.Connection,
    definitions: Iterable[Any] | Mapping[str, Any],
    requested: DateRange,
    context: AdapterContext,
    *,
    outcome_key: str,
    outcome_mode: str,
    interactions: str = "pairwise",
) -> _VerifiedPhase4Analysis:
    """Run Phase 4 directly and return a result accepted by the ledger writer."""

    if outcome_mode == "all":
        raise _error(
            "validation_error",
            "one persisted run requires one explicit outcome mode",
            validation=True,
        )
    materialized, expected_registry = _materialized_definitions(definitions)
    result = associations.analyze_outcome(
        conn,
        materialized,
        requested,
        context,
        outcome_key=outcome_key,
        mode=outcome_mode,
        min_n=associations.DEFAULT_MIN_N,
        interactions=interactions,
        top=100,
        include_internal=True,
        return_all_internal=True,
    )
    return _seal_engine_analysis_output(
        _strip_internal_findings(result),
        expected_registry_sha256=expected_registry,
        definitions=materialized,
    )


def compute_verified_finding(
    conn: sqlite3.Connection,
    definitions: Iterable[Any] | Mapping[str, Any],
    requested: DateRange,
    context: AdapterContext,
    *,
    outcome_key: str,
    finding_id_value: str,
    input_fingerprint_value: str,
) -> _VerifiedPhase4Finding:
    """Replay Phase 4 and return one stale-safe finding accepted for promotion."""

    materialized, expected_registry = _materialized_definitions(definitions)
    result = associations.recompute_finding_evidence(
        conn,
        materialized,
        requested,
        context,
        outcome_key=outcome_key,
        finding_id_value=finding_id_value,
        input_fingerprint_value=input_fingerprint_value,
    )
    return _seal_engine_finding_output(
        result,
        expected_registry_sha256=expected_registry,
        definitions=materialized,
    )


def _fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any],
) -> dict[str, Any] | None:
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    return None if row is None else _row_dict(row, [item[0] for item in cursor.description])


def _fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any],
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    return [_row_dict(row, columns) for row in cursor.fetchall()]


def _assert_existing(
    existing: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    name: str,
    ignored: frozenset[str] = frozenset(),
) -> None:
    differences = [
        key
        for key, value in expected.items()
        if key not in ignored and existing.get(key) != value
    ]
    if differences:
        raise _error(
            "ledger_invariant",
            f"{name} conflicts on {differences[0]}",
        )


def _outcome_set(outcomes: Iterable[Sequence[str] | Mapping[str, str]]) -> list[dict[str, str]]:
    normalized: set[tuple[str, str]] = set()
    for index, item in enumerate(outcomes):
        if isinstance(item, Mapping):
            if set(item) != {"outcome_key", "outcome_mode"}:
                raise _error(
                    "validation_error",
                    f"outcome_set[{index}] fields mismatch",
                    validation=True,
                )
            outcome_key = item["outcome_key"]
            outcome_mode = item["outcome_mode"]
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) == 2:
            outcome_key, outcome_mode = item
        else:
            raise _error(
                "validation_error",
                f"outcome_set[{index}] must contain outcome key and mode",
                validation=True,
            )
        normalized.add(
            (
                _feature(outcome_key, f"outcome_set[{index}].outcome_key"),
                _text(
                    outcome_mode,
                    f"outcome_set[{index}].outcome_mode",
                    maximum=80,
                    token=True,
                ),
            )
        )
    if not normalized:
        raise _error(
            "validation_error",
            "outcome_set must not be empty",
            validation=True,
        )
    return [
        {"outcome_key": outcome_key, "outcome_mode": outcome_mode}
        for outcome_key, outcome_mode in sorted(normalized)
    ]


def _outcome_set_hash(outcomes: Sequence[Mapping[str, str]]) -> str:
    return sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "outcomes": list(outcomes),
        }
    )


def _range_plan(ranges: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(ranges):
        if not isinstance(item, Mapping) or set(item) != {
            "range_role",
            "requested_range_kind",
            "requested_from",
            "requested_to",
        }:
            raise _error(
                "validation_error",
                f"ranges[{index}] fields mismatch",
                validation=True,
            )
        role = _enum(
            item["range_role"],
            frozenset({"primary", "recent", "historical"}),
            f"ranges[{index}].range_role",
        )
        if role in seen:
            raise _error(
                "validation_error",
                "range roles must be unique",
                validation=True,
            )
        seen.add(role)
        kind = _enum(
            item["requested_range_kind"],
            frozenset({"all", "bounded"}),
            f"ranges[{index}].requested_range_kind",
        )
        start = _date(
            item["requested_from"],
            f"ranges[{index}].requested_from",
            nullable=True,
        )
        end = _date(
            item["requested_to"],
            f"ranges[{index}].requested_to",
            nullable=True,
        )
        if kind == "all" and (start is not None or end is not None):
            raise _error(
                "validation_error",
                "all range cannot carry bounds",
                validation=True,
            )
        if kind == "bounded" and (
            start is None or end is None or start > end
        ):
            raise _error(
                "validation_error",
                "bounded range requires ordered bounds",
                validation=True,
            )
        normalized.append(
            {
                "range_role": role,
                "requested_range_kind": kind,
                "requested_from": start,
                "requested_to": end,
            }
        )
    if not normalized or "primary" not in seen:
        raise _error(
            "validation_error",
            "range plan requires a primary range",
            validation=True,
        )
    return sorted(normalized, key=lambda item: item["range_role"])


def create_analysis_batch(
    conn: sqlite3.Connection,
    *,
    run_kind: str,
    anchor_date: str,
    outcome_selection: str,
    outcomes: Iterable[Sequence[str] | Mapping[str, str]],
    ranges: Iterable[Mapping[str, Any]],
    registry_sha256_value: str,
    initiator_key: str | None = None,
    range_plan_version: str = RANGE_PLAN_VERSION,
    analysis_version: str = ANALYSIS_VERSION,
    registry_version: str = REGISTRY_VERSION,
    engine_sha256_value: str | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Create or return one deterministic analysis batch.

    The caller owns the surrounding transaction.  Equal dedupe material is an
    idempotent read; a conflicting existing row is an invariant failure.
    """

    require_ledger_schema(conn)
    run_kind = _enum(run_kind, BATCH_KINDS, "run_kind")
    anchor_date = _date(anchor_date, "anchor_date") or ""
    outcome_selection = _enum(
        outcome_selection,
        frozenset({"explicit_set", "base_and_enabled", "trigger_mapped"}),
        "outcome_selection",
    )
    initiator_key = _optional_text(
        initiator_key, "initiator_key", maximum=256, token=True
    )
    range_plan_version = _text(
        range_plan_version, "range_plan_version", maximum=80, token=True
    )
    analysis_version = _text(
        analysis_version, "analysis_version", maximum=80, token=True
    )
    registry_version = _text(
        registry_version, "registry_version", maximum=80, token=True
    )
    engine_hash = _sha(
        engine_sha256_value if engine_sha256_value is not None else engine_sha256(),
        "engine_sha256",
    )
    registry_hash = _sha(registry_sha256_value, "registry_sha256")
    started = _timestamp(started_at or _now(), "started_at")
    outcome_values = _outcome_set(outcomes)
    range_values = _range_plan(ranges)
    outcome_hash = _outcome_set_hash(outcome_values)
    dedupe_key = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "analysis_batch",
            "run_kind": run_kind,
            "anchor_date": anchor_date,
            "initiator_key": initiator_key,
            "range_plan_version": range_plan_version,
            "outcome_selection": outcome_selection,
            "outcome_set_sha256": outcome_hash,
            "ranges": range_values,
            "analysis_version": analysis_version,
            "registry_version": registry_version,
            "engine_sha256": engine_hash,
            "registry_sha256": registry_hash,
        }
    )
    batch_id = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "batch_id",
            "dedupe_key": dedupe_key,
        }
    )
    expected = {
        "batch_id": batch_id,
        "dedupe_key": dedupe_key,
        "run_kind": run_kind,
        "anchor_date": anchor_date,
        "initiator_key": initiator_key,
        "range_plan_version": range_plan_version,
        "outcome_selection": outcome_selection,
        "outcome_set_sha256": outcome_hash,
        "analysis_version": analysis_version,
        "registry_version": registry_version,
        "engine_sha256": engine_hash,
        "registry_sha256": registry_hash,
        "status": "running",
        "status_reason_code": None,
        "run_count": 0,
        "completed_count": 0,
        "insufficient_count": 0,
        "no_data_count": 0,
        "failed_count": 0,
        "started_at": started,
        "completed_at": None,
    }
    existing = _fetch_one(
        conn,
        "SELECT * FROM analysis_batches WHERE dedupe_key=?",
        (dedupe_key,),
    )
    if existing is not None:
        _assert_existing(
            existing,
            expected,
            name="analysis batch",
            ignored=frozenset(
                {
                    "status",
                    "status_reason_code",
                    "run_count",
                    "completed_count",
                    "insufficient_count",
                    "no_data_count",
                    "failed_count",
                    "started_at",
                    "completed_at",
                }
            ),
        )
        range_rows = [
            add_analysis_range(conn, batch_id=batch_id, **item)
            for item in range_values
        ]
        return {**existing, "created": False, "ranges": range_rows}
    conn.execute(
        """INSERT INTO analysis_batches(
             batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
             range_plan_version,outcome_selection,outcome_set_sha256,
             analysis_version,registry_version,engine_sha256,registry_sha256,
             status,status_reason_code,run_count,completed_count,
             insufficient_count,no_data_count,failed_count,started_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        tuple(expected.values()),
    )
    range_rows = [
        add_analysis_range(conn, batch_id=batch_id, **item)
        for item in range_values
    ]
    return {**expected, "created": True, "ranges": range_rows}


def add_analysis_range(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    range_role: str,
    requested_range_kind: str,
    requested_from: str | None = None,
    requested_to: str | None = None,
) -> dict[str, Any]:
    """Create or return one deterministic requested range for a batch."""

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    batch = _fetch_one(
        conn,
        "SELECT batch_id,status FROM analysis_batches WHERE batch_id=?",
        (batch_id,),
    )
    if batch is None:
        raise _error("unknown_batch", "analysis batch does not exist", validation=True)
    range_role = _enum(
        range_role, frozenset({"primary", "recent", "historical"}), "range_role"
    )
    requested_range_kind = _enum(
        requested_range_kind, frozenset({"all", "bounded"}), "requested_range_kind"
    )
    start = _date(requested_from, "requested_from", nullable=True)
    end = _date(requested_to, "requested_to", nullable=True)
    if requested_range_kind == "all":
        if start is not None or end is not None:
            raise _error(
                "validation_error",
                "all range cannot carry bounds",
                validation=True,
            )
    elif start is None or end is None or start > end:
        raise _error(
            "validation_error",
            "bounded range requires ordered bounds",
            validation=True,
        )
    range_id = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "analysis_range",
            "batch_id": batch_id,
            "range_role": range_role,
            "requested_range_kind": requested_range_kind,
            "requested_from": start,
            "requested_to": end,
        }
    )
    expected = {
        "range_id": range_id,
        "batch_id": batch_id,
        "range_role": range_role,
        "requested_range_kind": requested_range_kind,
        "requested_from": start,
        "requested_to": end,
    }
    existing = _fetch_one(
        conn,
        "SELECT * FROM analysis_range_requests WHERE batch_id=? AND range_role=?",
        (batch_id, range_role),
    )
    if existing is not None:
        _assert_existing(existing, expected, name="analysis range")
        return {**existing, "created": False}
    if batch["status"] != "running":
        raise _error(
            "terminal_batch",
            "a terminal analysis batch cannot accept a new range",
        )
    conn.execute(
        """INSERT INTO analysis_range_requests(
             range_id,batch_id,range_role,requested_range_kind,requested_from,requested_to)
           VALUES(?,?,?,?,?,?)""",
        tuple(expected.values()),
    )
    return {**expected, "created": True}


def start_analysis_run(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    range_id: str,
    outcome_key: str,
    outcome_mode: str,
    batch_outcomes: Iterable[Sequence[str] | Mapping[str, str]],
    started_at: str | None = None,
) -> dict[str, Any]:
    """Create or return the deterministic running row for one outcome/mode."""

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    range_id = _sha(range_id, "range_id")
    outcome_key = _feature(outcome_key, "outcome_key")
    outcome_mode = _text(outcome_mode, "outcome_mode", maximum=80, token=True)
    started = _timestamp(started_at or _now(), "started_at")
    declared_outcomes = _outcome_set(batch_outcomes)
    batch = _fetch_one(
        conn,
        """SELECT batch_id,status,outcome_set_sha256
             FROM analysis_batches WHERE batch_id=?""",
        (batch_id,),
    )
    if batch is None:
        raise _error("unknown_batch", "analysis batch does not exist", validation=True)
    if _outcome_set_hash(declared_outcomes) != batch["outcome_set_sha256"]:
        raise _error(
            "outcome_set_mismatch",
            "declared outcomes do not match the analysis batch",
            validation=True,
        )
    if {
        "outcome_key": outcome_key,
        "outcome_mode": outcome_mode,
    } not in declared_outcomes:
        raise _error(
            "undeclared_outcome",
            "analysis run outcome/mode is absent from the batch outcome set",
            validation=True,
        )
    requested = _fetch_one(
        conn,
        """SELECT range_id,batch_id FROM analysis_range_requests
           WHERE range_id=? AND batch_id=?""",
        (range_id, batch_id),
    )
    if requested is None:
        raise _error(
            "unknown_range",
            "range does not belong to the analysis batch",
            validation=True,
        )
    run_id = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "analysis_run",
            "batch_id": batch_id,
            "range_id": range_id,
            "outcome_key": outcome_key,
            "outcome_mode": outcome_mode,
        }
    )
    existing = _fetch_one(
        conn,
        """SELECT * FROM analysis_runs
           WHERE range_id=? AND outcome_key=? AND outcome_mode=?""",
        (range_id, outcome_key, outcome_mode),
    )
    if existing is not None:
        _assert_existing(
            existing,
            {
                "run_id": run_id,
                "batch_id": batch_id,
                "range_id": range_id,
                "outcome_key": outcome_key,
                "outcome_mode": outcome_mode,
            },
            name="analysis run",
        )
        return {**existing, "created": False}
    if batch["status"] != "running":
        raise _error(
            "terminal_batch",
            "a terminal analysis batch cannot accept a new run",
        )
    conn.execute(
        """INSERT INTO analysis_runs(
             run_id,batch_id,range_id,outcome_key,outcome_mode,status,
             status_reason_code,started_at)
           VALUES(?,?,?,?,?,'running',NULL,?)""",
        (run_id, batch_id, range_id, outcome_key, outcome_mode, started),
    )
    _refresh_batch_counts(conn, batch_id)
    result = _fetch_one(conn, "SELECT * FROM analysis_runs WHERE run_id=?", (run_id,))
    assert result is not None
    return {**result, "created": True}


def _status_for_result(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    meta = payload["meta"]
    coverage = payload["coverage"]
    analysis_range = meta["analysis_range"]
    eligible_n = coverage.get("outcome_eligible_n")
    if isinstance(eligible_n, bool) or not isinstance(eligible_n, int) or eligible_n < 0:
        raise _error("invalid_engine_output", "coverage.outcome_eligible_n is invalid")
    if analysis_range["from"] is None:
        return "no_data", "all_no_outcome_data"
    if eligible_n < associations.DEFAULT_MIN_N:
        return "insufficient_data", "insufficient_outcome_data"
    mode_coverage = coverage.get("modes")
    if not isinstance(mode_coverage, Mapping):
        raise _error("invalid_engine_output", "coverage.modes must be an object")
    current = mode_coverage.get(meta["modes"][0])
    if not isinstance(current, Mapping):
        raise _error("invalid_engine_output", "coverage is missing the persisted mode")
    tested = current.get("tested_candidates")
    if isinstance(tested, bool) or not isinstance(tested, int) or tested < 0:
        raise _error("invalid_engine_output", "tested candidate count is invalid")
    if tested == 0:
        return "insufficient_data", "no_testable_candidates"
    return "completed", None


def _finding_retry_material(finding: Mapping[str, Any]) -> dict[str, Any]:
    provenance = finding.get("provenance")
    warnings = finding.get("warnings")
    if not isinstance(provenance, Mapping) or not isinstance(warnings, list):
        raise _error("invalid_engine_output", "analysis finding retry material is malformed")
    return {
        **{
            key: value
            for key, value in finding.items()
            if key not in {"source_references", "source_manifests"}
        },
        "warnings": [
            item for item in warnings if item != "historical_only"
        ],
        "provenance": {
            key: value
            for key, value in provenance.items()
            if key != "evidence_fingerprint"
        },
    }


def _analysis_retry_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic analytical evidence used for terminal retry equality.

    Wall-clock movement legitimately changes the generated timestamp, bounded
    historical-only presentation warning, and operational data-readiness
    snapshot (collector freshness, stale state/counts, and owner guidance)
    without changing the input fingerprint or any analytical evidence.  Those
    fields remain stored from the first immutable result but cannot turn an
    exact same-input/version retry into a conflict.  Every finding identity,
    count, rate, effect, interval, p/q value, stability result, engine warning
    other than ``historical_only``, and provenance ancestor remains compared.
    """

    meta = payload.get("meta")
    if not isinstance(meta, Mapping):
        raise _error("invalid_engine_output", "analysis result meta is missing")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise _error("invalid_engine_output", "analysis result findings are missing")
    normalized_findings: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise _error("invalid_engine_output", "analysis finding is malformed")
        normalized_findings.append(_finding_retry_material(finding))
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        raise _error("invalid_engine_output", "analysis result warnings are malformed")
    return {
        **{
            key: value
            for key, value in payload.items()
            if key not in {"meta", "readiness", "findings", "warnings"}
        },
        "meta": {
            key: value
            for key, value in meta.items()
            if key != "generated_at"
        },
        "findings": normalized_findings,
        "warnings": [
            item for item in warnings if item != "historical_only"
        ],
    }


def _canonicalize_reused_finding_evidence(
    conn: sqlite3.Connection,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse the first immutable finding for clock-only cross-run variants."""

    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise _error("invalid_engine_output", "analysis result findings are missing")
    result = dict(payload)
    canonical_findings: list[dict[str, Any]] = []
    reused_presentation = False
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise _error("invalid_engine_output", "analysis finding is malformed")
        finding_id_value = finding.get("finding_id")
        if not isinstance(finding_id_value, str):
            raise _error("invalid_engine_output", "analysis finding ID is malformed")
        existing = _fetch_one(
            conn,
            "SELECT finding_id FROM analysis_findings WHERE finding_id=?",
            (finding_id_value,),
        )
        if existing is None:
            canonical_findings.append(dict(finding))
            continue
        stored = _load_finding(conn, finding_id_value)["evidence"]
        if canonical_json(_finding_retry_material(stored)) == canonical_json(
            _finding_retry_material(finding)
        ):
            canonical_findings.append(stored)
            reused_presentation = reused_presentation or stored != finding
        else:
            canonical_findings.append(dict(finding))
    result["findings"] = canonical_findings
    if reused_presentation:
        warnings = result.get("warnings")
        if not isinstance(warnings, list):
            raise _error("invalid_engine_output", "analysis result warnings are malformed")
        without_clock = [
            item for item in warnings if item != "historical_only"
        ]
        if any(
            "historical_only" in item.get("warnings", [])
            for item in canonical_findings
        ):
            without_clock.append("historical_only")
        result["warnings"] = sorted(set(without_clock))
    return result


def _persist_finding(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    finding: Mapping[str, Any],
) -> None:
    components = [
        _component_identity(item, f"finding.component[{index}]")
        for index, item in enumerate(finding["exposure"]["components"])
    ]
    candidate_kind = "single" if len(components) == 1 else "pair"
    direction = _effect_direction(finding)
    evidence_json = canonical_json(finding)
    evidence_for_json = canonical_json(finding["evidence_for"])
    evidence_against_json = canonical_json(finding["evidence_against"])
    fingerprint = finding["provenance"]["evidence_fingerprint"]
    expected = {
        "finding_id": finding["finding_id"],
        "run_id": run_id,
        "candidate_key": finding["candidate_key"],
        "candidate_kind": candidate_kind,
        "outcome_key": finding["outcome"]["key"],
        "outcome_mode": finding["outcome"]["mode"],
        "direction": direction,
        "quality_tier": finding["quality"]["tier"],
        "eligible_for_hypothesis": int(
            finding["quality"]["eligible_for_hypothesis"]
        ),
        "evidence_json": evidence_json,
        "evidence_for_json": evidence_for_json,
        "evidence_against_json": evidence_against_json,
        "evidence_fingerprint": fingerprint,
    }
    existing = _fetch_one(
        conn,
        "SELECT * FROM analysis_findings WHERE finding_id=?",
        (finding["finding_id"],),
    )
    if existing is None:
        conn.execute(
            """INSERT INTO analysis_findings(
                 finding_id,run_id,candidate_key,candidate_kind,outcome_key,
                 outcome_mode,direction,quality_tier,eligible_for_hypothesis,
                 evidence_json,evidence_for_json,evidence_against_json,
                 evidence_fingerprint)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(expected.values()),
        )
    else:
        # ``finding_id`` intentionally excludes run/batch identity.  The first
        # run is retained as the immutable origin row; later exact occurrences
        # are members of their runs through those runs' canonical result JSON.
        _assert_existing(
            existing,
            expected,
            name="analysis finding",
            ignored=frozenset({"run_id"}),
        )
    for position, component in enumerate(components, 1):
        component_expected = {
            "finding_id": finding["finding_id"],
            "position": position,
            **component,
        }
        existing_component = _fetch_one(
            conn,
            """SELECT * FROM analysis_finding_components
               WHERE finding_id=? AND position=?""",
            (finding["finding_id"], position),
        )
        if existing_component is None:
            conn.execute(
                """INSERT INTO analysis_finding_components(
                     finding_id,position,exposure_key,lag_days,window_days,
                     transform,temporal_direction)
                   VALUES(?,?,?,?,?,?,?)""",
                tuple(component_expected.values()),
            )
        else:
            _assert_existing(
                existing_component,
                component_expected,
                name="analysis finding component",
            )


def persist_analysis_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    verified: _VerifiedPhase4Analysis,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Persist one verified terminal run and all normalized findings.

    A plain mapping is intentionally rejected.  Terminal rows are immutable at
    this API: an exact retry is a no-op and a different result is a conflict.
    """

    require_ledger_schema(conn)
    if (
        not isinstance(verified, _VerifiedPhase4Analysis)
        or verified._token is not _VERIFIED_TOKEN
    ):
        raise _error(
            "unverified_engine_output",
            "analysis persistence requires a direct Phase 4 engine seal",
            validation=True,
        )
    run_id = _sha(run_id, "run_id")
    completed = _timestamp(completed_at or _now(), "completed_at")
    run = _fetch_one(
        conn,
        """SELECT r.*,b.analysis_version,b.registry_version,b.engine_sha256,
                  b.registry_sha256
           FROM analysis_runs r
           JOIN analysis_batches b ON b.batch_id=r.batch_id
           WHERE r.run_id=?""",
        (run_id,),
    )
    if run is None:
        raise _error("unknown_run", "analysis run does not exist", validation=True)
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (run["batch_id"],)
    )
    requested = _fetch_one(
        conn,
        "SELECT * FROM analysis_range_requests WHERE range_id=?",
        (run["range_id"],),
    )
    if batch is None or requested is None:
        raise _error("ledger_invariant", "analysis run ancestry is incomplete")
    payload = _canonicalize_reused_finding_evidence(conn, verified.payload)
    meta = payload["meta"]
    if meta["outcome"] != run["outcome_key"] or meta["modes"] != [run["outcome_mode"]]:
        raise _error("mismatched_engine_output", "analysis result does not match run identity")
    for field in (
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
    ):
        if meta[field] != batch[field]:
            raise _error("mismatched_engine_output", f"batch {field} does not match result")
    requested_meta = meta["requested_range"]
    expected_requested = {
        "kind": requested["requested_range_kind"],
        "from": requested["requested_from"],
        "to": requested["requested_to"],
    }
    if requested_meta != expected_requested:
        raise _error("mismatched_engine_output", "requested range does not match range row")
    actual = meta["analysis_range"]
    baseline = meta["baseline_range"]
    if requested["requested_range_kind"] == "bounded":
        if actual != {
            "kind": "bounded",
            "from": requested["requested_from"],
            "to": requested["requested_to"],
        }:
            raise _error("mismatched_engine_output", "bounded analysis range was changed")
        range_resolution = "bounded_exact"
    elif actual["from"] is None:
        range_resolution = "all_no_data"
    else:
        range_resolution = "all_observed"
    status, reason = _status_for_result(payload)
    result_json = canonical_json(payload)
    result_hash = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "analysis_result",
            "result": payload,
        }
    )
    if run["status"] != "running":
        if (
            run["status"] == status
            and run["input_fingerprint"] == meta["input_fingerprint"]
        ):
            stored_payload = _run_result_payload(conn, run)
            if canonical_json(
                _analysis_retry_material(stored_payload)
            ) == canonical_json(_analysis_retry_material(payload)):
                return {**run, "persisted": False}
        raise _error("terminal_run_conflict", "terminal analysis run cannot be replaced")
    for finding in payload["findings"]:
        _persist_finding(conn, run_id=run_id, finding=finding)
    conn.execute(
        """UPDATE analysis_runs
           SET range_resolution=?,analysis_from=?,analysis_to=?,
               baseline_from=?,baseline_to=?,input_fingerprint=?,status=?,
               status_reason_code=?,result_json=?,result_sha256=?,completed_at=?
           WHERE run_id=? AND status='running'""",
        (
            range_resolution,
            actual["from"],
            actual["to"],
            baseline["from"],
            baseline["to"],
            meta["input_fingerprint"],
            status,
            reason,
            result_json,
            result_hash,
            completed,
            run_id,
        ),
    )
    _refresh_batch_counts(conn, run["batch_id"])
    result = _fetch_one(conn, "SELECT * FROM analysis_runs WHERE run_id=?", (run_id,))
    assert result is not None
    return {**result, "persisted": True}


def fail_analysis_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    reason_code: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Mark a running analysis attempt failed without fabricating result JSON."""

    require_ledger_schema(conn)
    run_id = _sha(run_id, "run_id")
    reason_code = _text(reason_code, "reason_code", maximum=120, token=True)
    completed = _timestamp(completed_at or _now(), "completed_at")
    run = _fetch_one(
        conn,
        """SELECT r.*,b.analysis_version,b.registry_version,b.engine_sha256,
                  b.registry_sha256
           FROM analysis_runs r
           JOIN analysis_batches b ON b.batch_id=r.batch_id
           WHERE r.run_id=?""",
        (run_id,),
    )
    if run is None:
        raise _error("unknown_run", "analysis run does not exist", validation=True)
    if run["status"] == "failed":
        if run["status_reason_code"] != reason_code:
            raise _error("terminal_run_conflict", "failed run reason cannot be replaced")
        return {**run, "changed": False}
    if run["status"] != "running":
        raise _error("terminal_run_conflict", "terminal analysis run cannot fail again")
    conn.execute(
        """UPDATE analysis_runs
           SET status='failed',status_reason_code=?,completed_at=?
           WHERE run_id=? AND status='running'""",
        (reason_code, completed, run_id),
    )
    _refresh_batch_counts(conn, run["batch_id"])
    result = _fetch_one(conn, "SELECT * FROM analysis_runs WHERE run_id=?", (run_id,))
    assert result is not None
    return {**result, "changed": True}


def _batch_status_counts(
    conn: sqlite3.Connection,
    batch_id: str,
) -> dict[str, int]:
    return {
        row["status"] if isinstance(row, sqlite3.Row) else row[0]: int(
            row["n"] if isinstance(row, sqlite3.Row) else row[1]
        )
        for row in conn.execute(
            "SELECT status,COUNT(*) AS n FROM analysis_runs WHERE batch_id=? GROUP BY status",
            (batch_id,),
        )
    }


def _refresh_batch_counts(conn: sqlite3.Connection, batch_id: str) -> None:
    counts = _batch_status_counts(conn, batch_id)
    conn.execute(
        """UPDATE analysis_batches
           SET run_count=?,completed_count=?,insufficient_count=?,
               no_data_count=?,failed_count=?
           WHERE batch_id=?""",
        (
            sum(counts.values()),
            counts.get("completed", 0),
            counts.get("insufficient_data", 0),
            counts.get("no_data", 0),
            counts.get("failed", 0),
            batch_id,
        ),
    )


def _assert_batch_fanout(
    conn: sqlite3.Connection,
    batch: Mapping[str, Any],
    *,
    error_code: str,
) -> list[dict[str, str]]:
    """Authenticate the frozen range × outcome/mode Cartesian fan-out."""

    batch_id = str(batch["batch_id"])
    ranges = _fetch_all(
        conn,
        """SELECT range_id FROM analysis_range_requests
           WHERE batch_id=? ORDER BY range_role""",
        (batch_id,),
    )
    if not ranges:
        raise _error(error_code, "analysis batch has no requested ranges")
    runs = _fetch_all(
        conn,
        """SELECT range_id,outcome_key,outcome_mode
           FROM analysis_runs WHERE batch_id=?
           ORDER BY range_id,outcome_key,outcome_mode""",
        (batch_id,),
    )
    if not runs:
        raise _error(error_code, "analysis batch has no runs")
    range_ids = {item["range_id"] for item in ranges}
    by_range: dict[str, set[tuple[str, str]]] = {
        range_id: set() for range_id in range_ids
    }
    for run in runs:
        if run["range_id"] not in by_range:
            raise _error(error_code, "analysis run references an undeclared range")
        pair = (run["outcome_key"], run["outcome_mode"])
        if pair in by_range[run["range_id"]]:
            raise _error(error_code, "analysis batch repeats a range/outcome run")
        by_range[run["range_id"]].add(pair)
    first = next(iter(by_range.values()))
    if not first or any(values != first for values in by_range.values()):
        raise _error(
            error_code,
            "analysis batch is missing its exact range/outcome Cartesian fan-out",
        )
    try:
        outcomes = _outcome_set(sorted(first))
    except LedgerError as exc:
        raise _error(error_code, "analysis batch outcome set is malformed") from exc
    if _outcome_set_hash(outcomes) != batch["outcome_set_sha256"]:
        raise _error(
            error_code,
            "analysis batch runs do not match its declared outcome set",
        )
    if len(runs) != len(ranges) * len(outcomes):
        raise _error(
            error_code,
            "analysis batch run count disagrees with its Cartesian fan-out",
        )
    return outcomes


def assert_terminal_batch_integrity(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
) -> dict[str, Any]:
    """Read-only authentication of terminal batch counts, status, and fan-out."""

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (batch_id,)
    )
    if batch is None:
        raise _error("unknown_batch", "analysis batch does not exist", validation=True)
    _assert_batch_fanout(conn, batch, error_code="ledger_corrupt")
    counts = _batch_status_counts(conn, batch_id)
    expected_counts = {
        "run_count": sum(counts.values()),
        "completed_count": counts.get("completed", 0),
        "insufficient_count": counts.get("insufficient_data", 0),
        "no_data_count": counts.get("no_data", 0),
        "failed_count": counts.get("failed", 0),
    }
    if counts.get("running", 0) or expected_counts["run_count"] == 0:
        raise _error(
            "ledger_corrupt",
            "terminal analysis batch has running runs or no runs",
        )
    if any(batch[field] != value for field, value in expected_counts.items()):
        raise _error("ledger_corrupt", "analysis batch terminal counts drift")
    expected_status = (
        "failed"
        if expected_counts["failed_count"] == expected_counts["run_count"]
        else "partial"
        if expected_counts["failed_count"]
        else "completed"
    )
    expected_reason = (
        "all_runs_failed"
        if expected_status == "failed"
        else "some_runs_failed"
        if expected_status == "partial"
        else None
    )
    if (
        batch["status"] != expected_status
        or batch["status_reason_code"] != expected_reason
        or batch["completed_at"] is None
    ):
        raise _error("ledger_corrupt", "analysis batch terminal status drift")
    return batch


def finalize_analysis_batch(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Finalize a batch after every planned run has reached a terminal state."""

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    completed = _timestamp(completed_at or _now(), "completed_at")
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (batch_id,)
    )
    if batch is None:
        raise _error("unknown_batch", "analysis batch does not exist", validation=True)
    if batch["status"] != "running":
        verified = assert_terminal_batch_integrity(conn, batch_id=batch_id)
        return {**verified, "changed": False}
    _assert_batch_fanout(conn, batch, error_code="batch_incomplete")
    _refresh_batch_counts(conn, batch_id)
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (batch_id,)
    )
    assert batch is not None
    running = conn.execute(
        "SELECT COUNT(*) FROM analysis_runs WHERE batch_id=? AND status='running'",
        (batch_id,),
    ).fetchone()[0]
    if running:
        raise _error("batch_incomplete", "analysis batch still has running runs")
    if batch["run_count"] == 0:
        raise _error("batch_incomplete", "analysis batch has no runs")
    if batch["failed_count"] == batch["run_count"]:
        status, reason = "failed", "all_runs_failed"
    elif batch["failed_count"]:
        status, reason = "partial", "some_runs_failed"
    else:
        status, reason = "completed", None
    conn.execute(
        """UPDATE analysis_batches
           SET status=?,status_reason_code=?,completed_at=?
           WHERE batch_id=? AND status='running'""",
        (status, reason, completed, batch_id),
    )
    result = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (batch_id,)
    )
    assert result is not None
    return {**result, "changed": True}


def _load_finding(conn: sqlite3.Connection, finding_id_value: str) -> dict[str, Any]:
    row = _fetch_one(
        conn,
        "SELECT * FROM analysis_findings WHERE finding_id=?",
        (finding_id_value,),
    )
    if row is None:
        raise _error("unknown_finding", "analysis finding does not exist", validation=True)
    try:
        evidence = json.loads(row["evidence_json"])
        evidence_for = json.loads(row["evidence_for_json"])
        evidence_against = json.loads(row["evidence_against_json"])
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise _error("ledger_corrupt", "stored finding JSON is invalid") from exc
    if (
        canonical_json(evidence) != row["evidence_json"]
        or canonical_json(evidence_for) != row["evidence_for_json"]
        or canonical_json(evidence_against) != row["evidence_against_json"]
    ):
        raise _error("ledger_corrupt", "stored finding JSON is not canonical")
    if evidence.get("evidence_for") != evidence_for or evidence.get(
        "evidence_against"
    ) != evidence_against:
        raise _error("ledger_corrupt", "stored finding evidence arrays disagree")
    stored_analysis_version = evidence.get("provenance", {}).get(
        "analysis_version"
    )
    if not isinstance(stored_analysis_version, str):
        raise _error("ledger_corrupt", "stored finding analysis version is absent")
    computed = _evidence_fingerprint_for(evidence, stored_analysis_version)
    if (
        computed != row["evidence_fingerprint"]
        or evidence.get("provenance", {}).get("evidence_fingerprint") != computed
    ):
        raise _error("ledger_corrupt", "stored finding evidence fingerprint is invalid")
    components = [
        _row_dict(item, [value[0] for value in cursor.description])
        for cursor in [
            conn.execute(
                """SELECT position,exposure_key,lag_days,window_days,transform,
                          temporal_direction
                   FROM analysis_finding_components
                   WHERE finding_id=? ORDER BY position""",
                (finding_id_value,),
            )
        ]
        for item in cursor.fetchall()
    ]
    expected_count = 1 if row["candidate_kind"] == "single" else 2
    if len(components) != expected_count:
        raise _error("ledger_corrupt", "stored finding component count is invalid")
    identity = [
        {
            "exposure_key": item["exposure_key"],
            "lag_days": item["lag_days"],
            "window_days": item["window_days"],
            "transform": item["transform"],
        }
        for item in components
    ]
    if candidate_key(row["outcome_key"], row["outcome_mode"], identity) != row[
        "candidate_key"
    ]:
        raise _error("ledger_corrupt", "stored finding candidate key is invalid")
    return {
        **row,
        "evidence": evidence,
        "components": components,
    }


def _run_result_payload(
    conn: sqlite3.Connection,
    run: Mapping[str, Any],
) -> dict[str, Any]:
    """Return and authenticate the immutable public result attached to a run."""

    text = run.get("result_json")
    if not isinstance(text, str):
        raise _error("ledger_invariant", "terminal run has no result JSON")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _error("ledger_corrupt", "stored run result JSON is invalid") from exc
    if not isinstance(payload, dict) or canonical_json(payload) != text:
        raise _error("ledger_corrupt", "stored run result JSON is not canonical")
    expected_hash = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "analysis_result",
            "result": payload,
        }
    )
    if run.get("result_sha256") != expected_hash:
        raise _error("ledger_corrupt", "stored run result hash is invalid")
    if (
        set(payload) != _ANALYSIS_KEYS
        or payload.get("ok") is not True
        or payload.get("contract_version") != ANALYSIS_CONTRACT_VERSION
    ):
        raise _error("ledger_corrupt", "stored run result contract is invalid")
    meta = payload.get("meta")
    if not isinstance(meta, Mapping) or set(meta) != _META_KEYS:
        raise _error("ledger_corrupt", "stored run result metadata is invalid")
    requested = _fetch_one(
        conn,
        """SELECT requested_range_kind,requested_from,requested_to
             FROM analysis_range_requests WHERE range_id=?""",
        (run.get("range_id"),),
    )
    if requested is None:
        raise _error("ledger_invariant", "run range ancestry is missing")
    expected_requested = {
        "kind": requested["requested_range_kind"],
        "from": requested["requested_from"],
        "to": requested["requested_to"],
    }
    resolution = run.get("range_resolution")
    if resolution == "bounded_exact":
        actual_kind = "bounded"
    elif resolution in {"all_observed", "all_no_data"}:
        actual_kind = "all"
    else:
        raise _error("ledger_corrupt", "stored run range resolution is invalid")
    expected_actual = {
        "kind": actual_kind,
        "from": run.get("analysis_from"),
        "to": run.get("analysis_to"),
    }
    expected_baseline = {
        "kind": "all" if run.get("baseline_from") is None else "bounded",
        "from": run.get("baseline_from"),
        "to": run.get("baseline_to"),
    }
    expected_meta = {
        "analysis_version": run.get("analysis_version"),
        "registry_version": run.get("registry_version"),
        "engine_sha256": run.get("engine_sha256"),
        "registry_sha256": run.get("registry_sha256"),
        "input_fingerprint": run.get("input_fingerprint"),
        "outcome": run.get("outcome_key"),
        "modes": [run.get("outcome_mode")],
        "requested_range": expected_requested,
        "analysis_range": expected_actual,
        "baseline_range": expected_baseline,
    }
    if any(meta.get(key) != value for key, value in expected_meta.items()):
        raise _error(
            "ledger_corrupt",
            "stored run result metadata disagrees with run ancestry",
        )
    try:
        _validate_engine_meta(meta)
        _validate_analysis_coverage(payload)
        expected_status, expected_reason = _status_for_result(payload)
    except LedgerError as exc:
        raise _error(
            "ledger_corrupt",
            "stored run result status inputs are invalid",
        ) from exc
    if (
        run.get("status") != expected_status
        or run.get("status_reason_code") != expected_reason
    ):
        raise _error(
            "ledger_corrupt",
            "stored run terminal status disagrees with its result",
        )
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise _error("ledger_corrupt", "stored run findings are invalid")
    return payload


def finding_belongs_to_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    finding_id_value: str,
) -> bool:
    """Resolve global finding membership from a run's authenticated result.

    This is the deterministic rule used by ledger promotion and synthesis:
    a global finding belongs to a run exactly when its ID and complete public
    evidence occur in that run's canonical, hash-verified ``result_json``.
    """

    require_ledger_schema(conn)
    run_id = _sha(run_id, "run_id")
    finding_id_value = _sha(finding_id_value, "finding_id")
    run = _fetch_one(
        conn,
        """SELECT r.*,b.analysis_version,b.registry_version,b.engine_sha256,
                  b.registry_sha256
           FROM analysis_runs r
           JOIN analysis_batches b ON b.batch_id=r.batch_id
           WHERE r.run_id=?""",
        (run_id,),
    )
    if run is None:
        raise _error("unknown_run", "analysis run does not exist", validation=True)
    payload = _run_result_payload(conn, run)
    matches = [
        item
        for item in payload["findings"]
        if isinstance(item, Mapping) and item.get("finding_id") == finding_id_value
    ]
    if not matches:
        return False
    if len(matches) != 1:
        raise _error("ledger_corrupt", "run result repeats a finding ID")
    stored = _load_finding(conn, finding_id_value)
    _assert_finding_run_semantics(stored, run)
    if matches[0] != stored["evidence"]:
        raise _error(
            "ledger_corrupt",
            "run finding membership disagrees with global immutable evidence",
        )
    return True


def _findings_for_run(
    conn: sqlite3.Connection,
    run: Mapping[str, Any],
) -> list[dict[str, Any]]:
    payload = _run_result_payload(conn, run)
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload["findings"]:
        if not isinstance(item, Mapping):
            raise _error("ledger_corrupt", "run finding is not an object")
        finding_id_value = item.get("finding_id")
        if not isinstance(finding_id_value, str) or finding_id_value in seen:
            raise _error("ledger_corrupt", "run finding identity is invalid")
        seen.add(finding_id_value)
        stored = _load_finding(conn, finding_id_value)
        _assert_finding_run_semantics(stored, run)
        if item != stored["evidence"]:
            raise _error(
                "ledger_corrupt",
                "run result disagrees with global immutable finding evidence",
            )
        findings.append(stored)
    return findings


def _hypothesis_dedupe(finding: Mapping[str, Any]) -> str:
    try:
        parsed = json.loads(finding["candidate_key"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise _error("ledger_corrupt", "candidate key is not JSON") from exc
    if canonical_json(parsed) != finding["candidate_key"]:
        raise _error("ledger_corrupt", "candidate key is not canonical")
    return sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "hypothesis",
            "candidate": parsed,
        }
    )


def _assert_finding_run_semantics(
    finding: Mapping[str, Any],
    run: Mapping[str, Any],
) -> None:
    provenance = finding["evidence"].get("provenance", {})
    if (
        provenance.get("analysis_version") != run["analysis_version"]
        or provenance.get("registry_version") != run["registry_version"]
        or provenance.get("engine_sha256") != run["engine_sha256"]
        or provenance.get("registry_sha256") != run["registry_sha256"]
        or provenance.get("input_fingerprint") != run["input_fingerprint"]
        or finding["outcome_key"] != run["outcome_key"]
        or finding["outcome_mode"] != run["outcome_mode"]
    ):
        raise _error(
            "ledger_invariant",
            "finding provenance or outcome identity does not match its analysis run",
        )
    expected_id = finding_id(
        candidate_key=finding["candidate_key"],
        analysis_range={
            "kind": (
                "all"
                if run["range_resolution"] == "all_observed"
                else "bounded"
            ),
            "from": run["analysis_from"],
            "to": run["analysis_to"],
        },
        baseline_range={
            "kind": "bounded",
            "from": run["baseline_from"],
            "to": run["baseline_to"],
        },
        input_fingerprint=run["input_fingerprint"],
        analysis_version=run["analysis_version"],
    )
    if finding["finding_id"] != expected_id:
        raise _error(
            "ledger_invariant",
            "finding identity does not match the run ranges and fingerprint",
        )


def _ensure_hypothesis(
    conn: sqlite3.Connection,
    *,
    run: Mapping[str, Any],
    finding: Mapping[str, Any],
    explicit: bool,
    created_at: str,
) -> tuple[dict[str, Any], bool]:
    quality = finding["quality_tier"]
    eligible = bool(finding["eligible_for_hypothesis"])
    if not eligible or quality == "insufficient":
        raise _error(
            "finding_not_eligible",
            "finding is not eligible for a hypothesis",
            validation=True,
        )
    if quality == "exploratory_screen" and not explicit:
        raise _error(
            "manual_promotion_required",
            "an exploratory screen cannot be auto-created",
            validation=True,
        )
    if quality not in {"exploratory_screen", "exploratory_unreplicated"}:
        raise _error(
            "finding_not_eligible",
            "finding quality cannot create a v1 hypothesis",
            validation=True,
        )
    dedupe_key = _hypothesis_dedupe(finding)
    hypothesis_id = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "hypothesis_id",
            "dedupe_key": dedupe_key,
        }
    )
    first_seen = run["analysis_from"] or run["analysis_to"]
    if first_seen is None:
        raise _error("ledger_invariant", "a finding cannot have a null analysis range")
    expected = {
        "hypothesis_id": hypothesis_id,
        "dedupe_key": dedupe_key,
        "outcome_key": finding["outcome_key"],
        "outcome_mode": finding["outcome_mode"],
        "candidate_kind": finding["candidate_kind"],
        "initial_direction": finding["direction"],
        "first_seen": first_seen,
        "created_at": created_at,
        "created_by_run": run["run_id"],
        "created_by_finding": finding["finding_id"],
    }
    existing = _fetch_one(
        conn, "SELECT * FROM hypotheses WHERE dedupe_key=?", (dedupe_key,)
    )
    created = existing is None
    if existing is None:
        conn.execute(
            """INSERT INTO hypotheses(
                 hypothesis_id,dedupe_key,outcome_key,outcome_mode,candidate_kind,
                 initial_direction,first_seen,created_at,created_by_run,
                 created_by_finding)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            tuple(expected.values()),
        )
        for component in finding["components"]:
            conn.execute(
                """INSERT INTO hypothesis_components(
                     hypothesis_id,position,exposure_key,lag_days,window_days,transform)
                   VALUES(?,?,?,?,?,?)""",
                (
                    hypothesis_id,
                    component["position"],
                    component["exposure_key"],
                    component["lag_days"],
                    component["window_days"],
                    component["transform"],
                ),
            )
        existing = expected
    else:
        _assert_existing(
            existing,
            expected,
            name="hypothesis",
            ignored=frozenset(
                {
                    "initial_direction",
                    "first_seen",
                    "created_at",
                    "created_by_run",
                    "created_by_finding",
                }
            ),
        )
        stored_components = [
            tuple(row)
            for row in conn.execute(
                """SELECT position,exposure_key,lag_days,window_days,transform
                   FROM hypothesis_components
                   WHERE hypothesis_id=? ORDER BY position""",
                (hypothesis_id,),
            )
        ]
        current_components = [
            (
                item["position"],
                item["exposure_key"],
                item["lag_days"],
                item["window_days"],
                item["transform"],
            )
            for item in finding["components"]
        ]
        if stored_components != current_components:
            raise _error("ledger_invariant", "hypothesis component identity drifted")
    return dict(existing), created


def _parse_canonical(text: Any, name: str) -> Any:
    if not isinstance(text, str):
        raise _error("ledger_corrupt", f"{name} is not text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _error("ledger_corrupt", f"{name} is invalid JSON") from exc
    if canonical_json(value) != text:
        raise _error("ledger_corrupt", f"{name} is not canonical JSON")
    return value


def _load_evaluations(
    conn: sqlite3.Connection,
    hypothesis_id: str,
) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """SELECT * FROM hypothesis_evaluations
           WHERE hypothesis_id=? ORDER BY tested_at,id""",
        (hypothesis_id,),
    )
    columns = [item[0] for item in cursor.description]
    result: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        item = _row_dict(raw, columns)
        for field in (
            "evidence_for_json",
            "evidence_against_json",
            "confounders_json",
            "sample_size_json",
            "effect_summary_json",
            "stability_json",
            "change_conditions",
        ):
            item[field.removesuffix("_json")] = _parse_canonical(
                item[field], f"evaluation.{field}"
            )
        result.append(item)
    return result


def _eval_direction(evaluation: Mapping[str, Any]) -> str:
    value = evaluation["effect_summary"].get("direction")
    if value not in _DIRECTIONS:
        raise _error("ledger_corrupt", "evaluation direction is invalid")
    return value


def _eval_magnitude(evaluation: Mapping[str, Any]) -> float | None:
    effect = evaluation["effect_summary"].get("effect")
    value = effect.get("oriented_estimate") if isinstance(effect, Mapping) else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error("ledger_corrupt", "evaluation effect is invalid")
    return abs(float(value))


def _finding_state(finding: Mapping[str, Any]) -> dict[str, Any]:
    evidence = finding["evidence"]
    effect = evidence["effect"]
    value = effect.get("oriented_estimate")
    magnitude = None if value is None else abs(float(value))
    interval = effect.get("oriented_ci95")
    if interval is not None:
        if (
            not isinstance(interval, list)
            or len(interval) != 2
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in interval
            )
        ):
            raise _error("ledger_corrupt", "finding confidence interval is invalid")
        interval = [float(interval[0]), float(interval[1])]
    testing = evidence["testing"]
    base_pass = (
        effect.get("method") is not None
        and value is not None
        and testing.get("family_size") is not None
    )
    quality = finding["quality_tier"]
    direction = finding["direction"]
    confounder_sensitive = bool(evidence["confounders"].get("sensitive_to"))
    eligible_n = evidence["sample"].get("eligible_n")
    if isinstance(eligible_n, bool) or not isinstance(eligible_n, int) or eligible_n < 0:
        raise _error("ledger_corrupt", "finding eligible_n is invalid")
    return {
        "quality": quality,
        "direction": direction,
        "magnitude": magnitude,
        "interval": interval,
        "base_pass": base_pass,
        "discovery_pass": quality in {"exploratory_unreplicated", "replicated"},
        "screen": quality == "exploratory_screen",
        "confounder_sensitive": confounder_sensitive,
        "eligible_n": eligible_n,
        "one_per_day": evidence["outcome"].get("temporal_type")
        != "slow_measurement",
    }


def _is_context_only_finding(finding: Mapping[str, Any]) -> bool:
    """Identify an engine-owned lineage finding that may inform context only."""

    evidence = finding.get("evidence")
    against = evidence.get("evidence_against") if isinstance(evidence, Mapping) else None
    return (
        not bool(finding.get("eligible_for_hypothesis"))
        and isinstance(against, list)
        and any(
            isinstance(item, Mapping)
            and item.get("code") == "autoregressive_lineage"
            for item in against
        )
    )


def _interval_includes_zero(interval: Sequence[float] | None) -> bool:
    return interval is not None and interval[0] <= 0 <= interval[1]


def _interval_excludes_zero(interval: Sequence[float] | None) -> bool:
    return interval is not None and (interval[1] < 0 or interval[0] > 0)


def _later_disjoint(
    current_from: str | None,
    comparison_to: str | None,
) -> bool:
    return (
        current_from is not None
        and comparison_to is not None
        and current_from > comparison_to
    )


def _overlaps(
    left_from: str | None,
    left_to: str | None,
    right_from: str | None,
    right_to: str | None,
) -> bool:
    return (
        left_from is not None
        and left_to is not None
        and right_from is not None
        and right_to is not None
        and left_from <= right_to
        and right_from <= left_to
    )


def _new_eligible_observations(
    current: Mapping[str, Any],
    comparisons: Sequence[Mapping[str, Any]],
) -> int:
    current_n = int(current["eligible_n"])
    bounded = [
        item
        for item in comparisons
        if item.get("finding_id") is not None
        and item.get("range_from") is not None
        and item.get("range_to") is not None
    ]
    if not bounded:
        return current_n
    latest_to = max(item["range_to"] for item in bounded)
    if _later_disjoint(current["range_from"], latest_to):
        return current_n
    current_from = current.get("range_from")
    current_to = current.get("range_to")
    if not current.get("one_per_day", False):
        prior_total = 0
        for item in bounded:
            sample = item.get("sample_size")
            prior_n = (
                sample.get("eligible_n")
                if isinstance(sample, Mapping)
                else None
            )
            if isinstance(prior_n, bool) or not isinstance(prior_n, int):
                continue
            prior_total += prior_n
        return max(0, current_n - prior_total)

    # Daily outcomes have at most one analytical unit per date.  Merge all
    # prior compatible windows after clipping them to the current window; the
    # resulting day count is an upper bound on already evaluated current rows.
    # Only rows beyond that bound are guaranteed new.  This deliberately
    # refuses to infer novelty from count growth between shifted/narrowed
    # windows whose observation identities are unavailable at the ledger.
    intervals: list[tuple[date, date]] = []
    prior_overlap_bound = 0
    current_start = date.fromisoformat(current_from)
    current_end = date.fromisoformat(current_to)
    for item in bounded:
        start = max(current_start, date.fromisoformat(item["range_from"]))
        end = min(current_end, date.fromisoformat(item["range_to"]))
        if start <= end:
            intervals.append((start, end))
            overlap_days = (end - start).days + 1
            sample = item.get("sample_size")
            prior_n = (
                sample.get("eligible_n")
                if isinstance(sample, Mapping)
                else None
            )
            if isinstance(prior_n, bool) or not isinstance(prior_n, int):
                prior_n = overlap_days
            prior_overlap_bound += min(prior_n, overlap_days)
    intervals.sort()
    merged: list[tuple[date, date]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end))
        elif end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    overlap_capacity = sum(
        (end - start).days + 1 for start, end in merged
    )
    already_seen_upper_bound = min(
        overlap_capacity, prior_overlap_bound,
    )
    return max(0, current_n - already_seen_upper_bound)


def _is_same_direction(current: str, comparison: str) -> bool:
    return current in {"positive", "negative"} and current == comparison


def _is_opposite_direction(current: str, comparison: str) -> bool:
    return {current, comparison} == {"positive", "negative"}


def _latest_discovery(
    evaluations: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for item in reversed(evaluations):
        if item["finding_id"] is not None and (
            item["evidence_class"]
            in {
                "initial_discovery_pass",
                "same_pass_overlap",
                "same_pass_nonoverlap",
            }
            or (
                item["evidence_class"]
                in {"same_exploratory", "incompatible_version"}
                and item["effect_summary"].get("quality", {}).get("tier")
                in {"exploratory_unreplicated", "replicated"}
            )
        ):
            return item
    return None


def _latest_coverage_anchor(
    evaluations: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the furthest compatible range already evaluated.

    Replication and held-out null evidence must be later than *every* prior
    compatible, non-dormant evidence window in the semantic lineage.  Choosing
    only the most recently inserted or latest-magnitude discovery would allow
    a narrowed/backfilled window to move the non-overlap boundary backwards
    and launder already evaluated rows into replication.
    """

    bounded = [
        item
        for item in evaluations
        if item.get("finding_id") is not None
        and item.get("range_from") is not None
        and item.get("range_to") is not None
    ]
    if not bounded:
        return None
    return max(
        bounded,
        key=lambda item: (
            item["range_to"],
            -date.fromisoformat(item["range_from"]).toordinal(),
            item["id"],
        ),
    )


def _prior_null_without_reset(
    evaluations: Sequence[Mapping[str, Any]],
    *,
    after_id: int | None,
) -> Mapping[str, Any] | None:
    for item in reversed(evaluations):
        if after_id is not None and item["id"] <= after_id:
            break
        if (
            item["finding_id"] is not None
            and bool(item["compatible_with_prior"])
            and item["effect_summary"].get("quality", {}).get("tier")
            in {"exploratory_unreplicated", "replicated"}
        ):
            return None
        if item["evidence_class"] == "null_nonoverlap":
            return item
        # Dormancy, incompatible-method observations, overlapping screens, and
        # weakening windows neither increment nor reset the consecutive count.
    return None


def _evidence_kinds(
    *,
    evidence_class: str,
    same_direction: bool,
    confounder_sensitive: bool,
    weak_or_unstable: bool = False,
) -> tuple[tuple[str, str], ...]:
    items: list[tuple[str, str]] = []
    if same_direction and evidence_class not in {
        "null_nonoverlap",
        "incompatible_version",
    }:
        items.append(("for", "same_direction_effect"))
    if evidence_class == "same_pass_nonoverlap":
        items.append(("for", "nonoverlap_replication"))
    if evidence_class == "weakening_window" or weak_or_unstable:
        items.append(("against", "weak_or_unstable"))
    if evidence_class == "null_nonoverlap":
        items.append(("against", "null_window"))
    if evidence_class == "opposite_pass":
        items.append(("against", "opposite_direction_effect"))
    if evidence_class == "incompatible_version":
        items.append(("against", "method_incompatibility"))
    if confounder_sensitive:
        items.append(("against", "confounder_sensitivity"))
    return tuple(items)


def _transition(
    *,
    current: Mapping[str, Any] | None,
    latest: Mapping[str, Any] | None,
    prior_substantive: Mapping[str, Any] | None,
    lineage: Sequence[Mapping[str, Any]],
    compatible: bool,
    explicit: bool,
    dormancy_reason: str | None,
    range_from: str | None,
    range_to: str | None,
    _respect_terminal: bool = True,
) -> TransitionDecision:
    """Evaluate the frozen Section 11.6 matrix in deterministic priority order."""

    if latest is not None and prior_substantive is not None:
        prior_status = prior_substantive["status"]
        prior_confidence = prior_substantive["confidence"]
    else:
        prior_status = None
        prior_confidence = None

    if prior_status == "rejected" and _respect_terminal:
        underlying = _transition(
            current=current,
            latest=latest,
            prior_substantive=prior_substantive,
            lineage=lineage,
            compatible=compatible,
            explicit=explicit,
            dormancy_reason=dormancy_reason,
            range_from=range_from,
            range_to=range_to,
            _respect_terminal=False,
        )
        return TransitionDecision(
            underlying.evidence_class,
            "rejected",
            "insufficient",
            underlying.change_reason,
            False,
            underlying.comparison_evaluation_id,
            underlying.compatible_with_prior,
            underlying.new_eligible_observations,
            underlying.evidence_kinds,
            {
                **underlying.conditions,
                "rejected_terminal": True,
                "would_status": underlying.status,
                "would_confidence": underlying.confidence,
                "would_transition": underlying.transition_applied,
            },
        )

    if prior_substantive is not None and not compatible:
        kinds = (
            ()
            if current is None
            else _evidence_kinds(
                evidence_class="incompatible_version",
                same_direction=False,
                confounder_sensitive=current["confounder_sensitive"],
            )
        )
        return TransitionDecision(
            "incompatible_version",
            prior_status,
            prior_confidence,
            "semantic_version_incompatible",
            False,
            prior_substantive["id"],
            False,
            0,
            kinds,
            {
                "compatibility_proof": False,
                "finding_available": current is not None,
            },
        )

    if current is None:
        if dormancy_reason not in {
            "dormant_no_eligible_data",
            "dormant_stale_prerequisite",
        }:
            raise _error("ledger_invariant", "missing finding requires a dormancy reason")
        if prior_substantive is None:
            raise _error("ledger_invariant", "a hypothesis cannot begin dormant")
        return TransitionDecision(
            dormancy_reason,
            "dormant",
            "insufficient",
            dormancy_reason,
            True,
            prior_substantive["id"],
            compatible,
            0,
            (),
            {"reason": dormancy_reason},
        )

    if prior_substantive is None:
        if current["quality"] == "exploratory_screen" and explicit:
            evidence_class, status = "initial_exploratory", "exploratory"
        elif current["discovery_pass"]:
            evidence_class, status = "initial_discovery_pass", "candidate"
        else:
            raise _error(
                "finding_not_eligible",
                "initial finding is not eligible for the requested transition",
                validation=True,
            )
        kinds = _evidence_kinds(
            evidence_class=evidence_class,
            same_direction=True,
            confounder_sensitive=current["confounder_sensitive"],
            weak_or_unstable=current["screen"],
        )
        return TransitionDecision(
            evidence_class,
            status,
            "low",
            evidence_class,
            True,
            None,
            True,
            current["eligible_n"],
            kinds,
            {"initial": True, "explicit": explicit},
        )

    discovery = _latest_discovery(lineage)
    if discovery is None and current["discovery_pass"]:
        comparison = lineage[-1]
        new_n = _new_eligible_observations(
            {**current, "range_from": range_from, "range_to": range_to},
            lineage,
        )
        comparison_direction = _eval_direction(comparison)
        opposite_screen = _is_opposite_direction(
            current["direction"], comparison_direction,
        )
        if opposite_screen:
            excludes_zero = _interval_excludes_zero(current["interval"])
            evidence_class = (
                "opposite_pass" if excludes_zero else "same_exploratory"
            )
            kinds = _evidence_kinds(
                evidence_class=evidence_class,
                same_direction=False,
                confounder_sensitive=current["confounder_sensitive"],
                weak_or_unstable=not excludes_zero,
            )
            return TransitionDecision(
                evidence_class,
                "rejected" if excludes_zero else prior_status,
                "insufficient" if excludes_zero else prior_confidence,
                (
                    "significantly_opposite_pass"
                    if excludes_zero
                    else "exploratory_opposite_caveat"
                ),
                excludes_zero,
                comparison["id"],
                True,
                new_n,
                kinds,
                {
                    "opposite_direction": True,
                    "ci_excludes_zero": excludes_zero,
                    "discovery_pass": True,
                    "prior_was_screen_only": True,
                },
            )
        kinds = _evidence_kinds(
            evidence_class="initial_discovery_pass",
            same_direction=True,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "initial_discovery_pass",
            "candidate",
            "low",
            "first_discovery_after_exploratory_screen",
            True,
            comparison["id"],
            True,
            new_n,
            kinds,
            {
                "first_compatible_discovery": True,
                "prior_was_screen_only": True,
            },
        )

    coverage_anchor = _latest_coverage_anchor(lineage)
    comparison = discovery or coverage_anchor or lineage[-1]
    range_comparison = coverage_anchor or comparison
    comparison_direction = _eval_direction(comparison)
    same_direction = _is_same_direction(current["direction"], comparison_direction)
    opposite = _is_opposite_direction(current["direction"], comparison_direction)
    new_n = _new_eligible_observations(
        {**current, "range_from": range_from, "range_to": range_to},
        lineage,
    )
    disjoint = _later_disjoint(range_from, range_comparison["range_to"])
    overlapping_references = [
        item
        for item in lineage
        if item.get("finding_id") is not None
        and _overlaps(
            range_from,
            range_to,
            item.get("range_from"),
            item.get("range_to"),
        )
    ]
    if discovery is not None and discovery in overlapping_references:
        overlap_reference = discovery
    elif overlapping_references:
        overlap_reference = max(
            overlapping_references,
            key=lambda item: (item["tested_at"], item["id"]),
        )
    else:
        overlap_reference = None
    overlap = overlap_reference is not None
    reference_magnitude = (
        _eval_magnitude(discovery) if discovery is not None else None
    )
    weaker = (
        current["base_pass"]
        and current["magnitude"] is not None
        and reference_magnitude is not None
        and current["magnitude"] < 0.5 * reference_magnitude
    )
    interval_zero = current["base_pass"] and _interval_includes_zero(
        current["interval"]
    )

    if (
        discovery is not None
        and
        opposite
        and current["discovery_pass"]
        and _interval_excludes_zero(current["interval"])
    ):
        evidence_class = "opposite_pass"
        kinds = _evidence_kinds(
            evidence_class=evidence_class,
            same_direction=False,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            evidence_class,
            "rejected",
            "insufficient",
            "significantly_opposite_pass",
            True,
            comparison["id"],
            True,
            new_n,
            kinds,
            {
                "opposite_direction": True,
                "ci_excludes_zero": True,
                "discovery_pass": True,
            },
        )

    null_held_out = (
        discovery is not None
        and
        disjoint
        and current["base_pass"]
        and (not current["discovery_pass"])
        and (weaker or interval_zero or current["quality"] == "insufficient")
    )
    if null_held_out:
        prior_null = _prior_null_without_reset(
            lineage,
            after_id=discovery["id"] if discovery is not None else None,
        )
        second = (
            prior_null is not None
            and _later_disjoint(range_from, prior_null["range_to"])
        )
        status = "rejected" if second else "weakened"
        confidence = "insufficient" if second else "low"
        reason = "second_consecutive_nonoverlap_null" if second else "first_nonoverlap_null"
        kinds = _evidence_kinds(
            evidence_class="null_nonoverlap",
            same_direction=False,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "null_nonoverlap",
            status,
            confidence,
            reason,
            True,
            prior_null["id"] if second else range_comparison["id"],
            True,
            new_n,
            kinds,
            {
                "consecutive_null_count": 2 if second else 1,
                "mutually_disjoint": bool(second),
                "prior_null_evaluation_id": (
                    prior_null["id"] if prior_null is not None else None
                ),
            },
        )

    if current["discovery_pass"] and same_direction:
        if new_n > 0 and disjoint:
            evidence_class = "same_pass_nonoverlap"
            confidence = "moderate" if current["confounder_sensitive"] else "high"
            kinds = _evidence_kinds(
                evidence_class=evidence_class,
                same_direction=True,
                confounder_sensitive=current["confounder_sensitive"],
            )
            return TransitionDecision(
                evidence_class,
                "replicated",
                confidence,
                "later_nonoverlap_replication",
                True,
                range_comparison["id"],
                True,
                new_n,
                kinds,
                {"later": True, "disjoint": True},
            )
        if new_n > 0 and overlap:
            status = "replicated" if prior_status == "replicated" else "strengthening"
            confidence = prior_confidence if status == "replicated" else "moderate"
            kinds = _evidence_kinds(
                evidence_class="same_pass_overlap",
                same_direction=True,
                confounder_sensitive=current["confounder_sensitive"],
            )
            return TransitionDecision(
                "same_pass_overlap",
                status,
                confidence,
                "same_direction_overlap_new_observations",
                True,
                overlap_reference["id"],
                True,
                new_n,
                kinds,
                {"overlap": True, "new_eligible_observations": new_n},
            )
        kinds = _evidence_kinds(
            evidence_class="same_exploratory",
            same_direction=True,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "same_exploratory",
            prior_status,
            prior_confidence,
            "same_pass_without_new_eligible_observations",
            False,
            range_comparison["id"],
            True,
            new_n,
            kinds,
            {"new_eligible_observations": new_n},
        )

    if discovery is not None and current["base_pass"] and (weaker or interval_zero):
        kinds = _evidence_kinds(
            evidence_class="weakening_window",
            same_direction=same_direction,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "weakening_window",
            "weakened",
            "low",
            "effect_below_half_reference" if weaker else "interval_includes_zero",
            True,
            comparison["id"],
            True,
            new_n,
            kinds,
            {
                "weaker_than_half": weaker,
                "interval_includes_zero": interval_zero,
                "reference_magnitude": reference_magnitude,
            },
        )

    # A q/split failure, or an exploratory opposite signal that does not pass
    # reversal gates, is recorded as a caveat without changing the last
    # substantive conclusion.
    kinds = _evidence_kinds(
        evidence_class="same_exploratory",
        same_direction=same_direction,
        confounder_sensitive=current["confounder_sensitive"],
        weak_or_unstable=True,
    )
    return TransitionDecision(
        "same_exploratory",
        prior_status,
        prior_confidence,
        "exploratory_caveat_only",
        False,
        comparison["id"],
        True,
        new_n,
        kinds,
        {
            "same_direction": same_direction,
            "opposite_direction": opposite,
            "discovery_pass": current["discovery_pass"],
        },
    )


def _evaluation_semantics(evaluation: Mapping[str, Any]) -> dict[str, str]:
    provenance = evaluation.get("effect_summary", {}).get("provenance")
    if not isinstance(provenance, Mapping):
        raise _error("ledger_corrupt", "evaluation semantic provenance is absent")
    analysis_version = provenance.get("analysis_version")
    registry_version = provenance.get("registry_version")
    engine_hash = provenance.get("engine_sha256")
    registry_hash = provenance.get("registry_sha256")
    if analysis_version != evaluation.get("source_analysis_version"):
        raise _error(
            "ledger_corrupt",
            "evaluation analysis-version provenance is inconsistent",
        )
    for value, name in (
        (analysis_version, "analysis_version"),
        (registry_version, "registry_version"),
    ):
        if not isinstance(value, str) or not value:
            raise _error("ledger_corrupt", f"evaluation {name} is absent")
    _sha(engine_hash, "evaluation.engine_sha256")
    _sha(registry_hash, "evaluation.registry_sha256")
    return {
        "analysis_version": analysis_version,
        "registry_version": registry_version,
        "engine_sha256": engine_hash,
        "registry_sha256": registry_hash,
    }


def _run_semantics(run: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in (
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
    ):
        value = run.get(field)
        if field.endswith("_sha256"):
            result[field] = _sha(value, f"run.{field}")
        elif not isinstance(value, str) or not value:
            raise _error("ledger_invariant", f"run {field} is absent")
        else:
            result[field] = value
    return result


def _semantic_compatibility(
    prior: Mapping[str, Any],
    *,
    current: Mapping[str, str],
    proofs: Mapping[tuple[str, str, str, str], Mapping[str, str]],
) -> tuple[bool, Mapping[str, str] | None, dict[str, Any]]:
    before = _evaluation_semantics(prior)
    same = (
        before["analysis_version"] == current["analysis_version"]
        and before["registry_version"] == current["registry_version"]
    )
    key = (
        before["analysis_version"],
        before["registry_version"],
        current["analysis_version"],
        current["registry_version"],
    )
    proof = None if same else proofs.get(key)
    compatible = same or proof is not None
    audit = {
        "from": before,
        "to": dict(current),
        "semantic_versions_equal": same,
        "proof": dict(proof) if proof is not None else None,
        "compatible": compatible,
    }
    return compatible, proof, audit


def _evaluation_payloads(
    *,
    finding: Mapping[str, Any] | None,
    audit_finding: Mapping[str, Any] | None,
    decision: TransitionDecision,
    run: Mapping[str, Any],
) -> dict[str, str]:
    if finding is None:
        empty: list[Any] = []
        if audit_finding is not None:
            evidence = audit_finding["evidence"]
            return {
                "evidence_for_json": canonical_json(empty),
                "evidence_against_json": canonical_json(empty),
                "confounders_json": canonical_json(evidence["confounders"]),
                "sample_size_json": canonical_json(evidence["sample"]),
                "effect_summary_json": canonical_json(
                    {
                        "effect": evidence["effect"],
                        "rates": evidence["rates"],
                        "testing": evidence["testing"],
                        "quality": evidence["quality"],
                        "direction": audit_finding["direction"],
                        "provenance": {
                            key: evidence["provenance"].get(key)
                            for key in (
                                "analysis_version",
                                "registry_version",
                                "engine_sha256",
                                "registry_sha256",
                                "input_fingerprint",
                            )
                        },
                    }
                ),
                "stability_json": canonical_json(evidence["stability"]),
            }
        return {
            "evidence_for_json": canonical_json(empty),
            "evidence_against_json": canonical_json(empty),
            "confounders_json": canonical_json(
                {"checked": [], "unchecked": [], "sensitive_to": [], "weighted_effect": None}
            ),
            "sample_size_json": canonical_json(
                {
                    "eligible_n": 0,
                    "complete_n": 0,
                    "missing_n": 0,
                    "reason": decision.evidence_class,
                }
            ),
            "effect_summary_json": canonical_json(
                {
                    "effect": None,
                    "rates": None,
                    "testing": None,
                    "quality": None,
                    "direction": "unknown",
                    "provenance": {
                        key: run[key]
                        for key in (
                            "analysis_version",
                            "registry_version",
                            "engine_sha256",
                            "registry_sha256",
                            "input_fingerprint",
                        )
                    },
                }
            ),
            "stability_json": canonical_json({"status": "not_evaluated"}),
        }
    evidence = finding["evidence"]
    return {
        "evidence_for_json": finding["evidence_for_json"],
        "evidence_against_json": finding["evidence_against_json"],
        "confounders_json": canonical_json(evidence["confounders"]),
        "sample_size_json": canonical_json(evidence["sample"]),
        "effect_summary_json": canonical_json(
            {
                "effect": evidence["effect"],
                "rates": evidence["rates"],
                "testing": evidence["testing"],
                "quality": evidence["quality"],
                "direction": finding["direction"],
                "provenance": {
                    key: evidence["provenance"].get(key)
                    for key in (
                        "analysis_version",
                        "registry_version",
                        "engine_sha256",
                        "registry_sha256",
                    )
                },
            }
        ),
        "stability_json": canonical_json(evidence["stability"]),
    }


def _insert_evidence_items(
    conn: sqlite3.Connection,
    *,
    hypothesis_id: str,
    evaluation_id: int,
    finding: Mapping[str, Any] | None,
    decision: TransitionDecision,
    analysis_version: str,
    range_from: str | None,
    range_to: str | None,
    created_at: str,
) -> list[str]:
    if finding is None or not decision.evidence_kinds:
        return []
    if range_from is None or range_to is None:
        raise _error("ledger_invariant", "finding evidence requires a concrete range")
    identifiers: list[str] = []
    for polarity, evidence_kind in decision.evidence_kinds:
        if polarity not in {"for", "against"} or evidence_kind not in _EVIDENCE_KINDS:
            raise _error("ledger_invariant", "state machine emitted an unknown evidence mapping")
        evidence_item_id = sha256_id(
            {
                "contract_version": LEDGER_CONTRACT_VERSION,
                "kind": "hypothesis_evidence_item",
                "hypothesis_id": hypothesis_id,
                "finding_id": finding["finding_id"],
                "evidence_kind": evidence_kind,
            }
        )
        expected = {
            "evidence_item_id": evidence_item_id,
            "hypothesis_id": hypothesis_id,
            "evaluation_id": evaluation_id,
            "finding_id": finding["finding_id"],
            "polarity": polarity,
            "evidence_kind": evidence_kind,
            "evidence_fingerprint": finding["evidence_fingerprint"],
            "source_analysis_version": analysis_version,
            "range_from": range_from,
            "range_to": range_to,
            "created_at": created_at,
        }
        existing = _fetch_one(
            conn,
            """SELECT * FROM hypothesis_evidence_items
               WHERE hypothesis_id=? AND finding_id=? AND evidence_kind=?""",
            (hypothesis_id, finding["finding_id"], evidence_kind),
        )
        if existing is None:
            conn.execute(
                """INSERT INTO hypothesis_evidence_items(
                     evidence_item_id,hypothesis_id,evaluation_id,finding_id,
                     polarity,evidence_kind,evidence_fingerprint,
                     source_analysis_version,range_from,range_to,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(expected.values()),
            )
        else:
            _assert_existing(
                existing, expected, name="hypothesis evidence item"
            )
        identifiers.append(evidence_item_id)
    return identifiers


def _append_evaluation(
    conn: sqlite3.Connection,
    *,
    hypothesis: Mapping[str, Any],
    run: Mapping[str, Any],
    finding: Mapping[str, Any] | None,
    explicit: bool,
    dormancy_reason: str | None,
    compatibility_proofs: Mapping[
        tuple[str, str, str, str],
        Mapping[str, str],
    ],
    created_at: str,
    audit_finding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if run["status"] not in {"completed", "insufficient_data", "no_data"}:
        raise _error("run_not_terminal", "hypothesis evaluation requires a terminal data run")
    input_fingerprint_value = run["input_fingerprint"]
    if input_fingerprint_value is None:
        raise _error("ledger_invariant", "terminal data run lacks an input fingerprint")
    analysis_version = run["analysis_version"]
    current_semantics = _run_semantics(run)
    tested_at = _timestamp(run["completed_at"] or created_at, "tested_at")
    duplicate = _fetch_one(
        conn,
        """SELECT * FROM hypothesis_evaluations
           WHERE hypothesis_id=? AND input_fingerprint=?
             AND source_analysis_version=?""",
        (
            hypothesis["hypothesis_id"],
            input_fingerprint_value,
            analysis_version,
        ),
    )
    if duplicate is not None:
        duplicate_evaluation = next(
            item
            for item in _load_evaluations(conn, hypothesis["hypothesis_id"])
            if item["id"] == duplicate["id"]
        )
        if (
            _evaluation_semantics(duplicate_evaluation)["registry_version"]
            != current_semantics["registry_version"]
        ):
            raise _error(
                "ledger_invariant",
                "one input fingerprint cannot span registry semantic versions",
            )
        return {
            "evaluation": duplicate,
            "created": False,
            "no_op_reason": "same_fingerprint_version",
            "evidence_item_ids": [],
        }
    same_run = _fetch_one(
        conn,
        """SELECT id FROM hypothesis_evaluations
           WHERE hypothesis_id=? AND run_id=?""",
        (hypothesis["hypothesis_id"], run["run_id"]),
    )
    if same_run is not None:
        raise _error("ledger_invariant", "hypothesis run already has a different evaluation")

    evaluations = _load_evaluations(conn, hypothesis["hypothesis_id"])
    latest = evaluations[-1] if evaluations else None
    if latest is not None and tested_at < latest["tested_at"]:
        raise _error(
            "historical_evaluation_order",
            "hypothesis evaluations cannot be inserted before the latest audit event",
        )
    substantive = [
        item
        for item in evaluations
        if item["evidence_class"]
        not in {"dormant_no_eligible_data", "dormant_stale_prerequisite"}
    ]
    prior_substantive = substantive[-1] if substantive else None
    if prior_substantive is None:
        compatible = True
        compatibility_audit: dict[str, Any] = {
            "from": None,
            "to": dict(current_semantics),
            "semantic_versions_equal": True,
            "proof": None,
            "compatible": True,
        }
    else:
        compatible, _proof, compatibility_audit = _semantic_compatibility(
            prior_substantive,
            current=current_semantics,
            proofs=compatibility_proofs,
        )
    if compatible:
        lineage = [
            item
            for item in evaluations
            if _semantic_compatibility(
                item,
                current=current_semantics,
                proofs=compatibility_proofs,
            )[0]
            and item["evidence_class"]
            not in {"dormant_no_eligible_data", "dormant_stale_prerequisite"}
        ]
        if not lineage and prior_substantive is not None:
            lineage = [prior_substantive]
    else:
        # A first result from a changed semantic version starts a separate
        # comparison lineage.  Its retained-status incompatible evaluation is
        # available as the reference for later same-version windows.
        lineage = [
            item
            for item in evaluations
            if (
                _evaluation_semantics(item)["analysis_version"]
                == current_semantics["analysis_version"]
                and _evaluation_semantics(item)["registry_version"]
                == current_semantics["registry_version"]
            )
            and item["finding_id"] is not None
        ]
        if lineage:
            compatible = True
            compatibility_audit = {
                "from": _evaluation_semantics(lineage[-1]),
                "to": dict(current_semantics),
                "semantic_versions_equal": True,
                "proof": None,
                "compatible": True,
                "separate_lineage": True,
            }

    current = _finding_state(finding) if finding is not None else None
    decision = _transition(
        current=current,
        latest=latest,
        prior_substantive=prior_substantive,
        lineage=lineage,
        compatible=compatible,
        explicit=explicit,
        dormancy_reason=dormancy_reason,
        range_from=run["analysis_from"],
        range_to=run["analysis_to"],
    )
    decision = TransitionDecision(
        evidence_class=decision.evidence_class,
        status=decision.status,
        confidence=decision.confidence,
        change_reason=decision.change_reason,
        transition_applied=decision.transition_applied,
        comparison_evaluation_id=decision.comparison_evaluation_id,
        compatible_with_prior=decision.compatible_with_prior,
        new_eligible_observations=decision.new_eligible_observations,
        evidence_kinds=decision.evidence_kinds,
        conditions={
            **decision.conditions,
            "semantic_compatibility": compatibility_audit,
        },
    )
    if finding is None and decision.evidence_class == "incompatible_version":
        # Migration 004 intentionally permits a null finding only for dormant
        # evaluations, while method-incompatibility evidence must reference an
        # immutable finding.  A no-finding run therefore cannot truthfully
        # append either class.  Retain the prior conclusion as an audited
        # analysis-run no-op instead of letting lower-priority dormancy win.
        return {
            "evaluation": latest or prior_substantive,
            "created": False,
            "no_op_reason": "incompatible_version_without_finding",
            "evidence_item_ids": [],
        }
    payloads = _evaluation_payloads(
        finding=finding,
        audit_finding=audit_finding,
        decision=decision,
        run=run,
    )
    evidence_fp = (
        finding["evidence_fingerprint"]
        if finding is not None
        else sha256_id(
            {
                "contract_version": LEDGER_CONTRACT_VERSION,
                "kind": "dormant_evidence",
                "hypothesis_id": hypothesis["hypothesis_id"],
                "run_id": run["run_id"],
                "input_fingerprint": input_fingerprint_value,
                "reason": decision.evidence_class,
                "analysis_version": run["analysis_version"],
                "registry_version": run["registry_version"],
                "engine_sha256": run["engine_sha256"],
                "registry_sha256": run["registry_sha256"],
                "range_from": run["analysis_from"],
                "range_to": run["analysis_to"],
                "audit_finding_evidence_fingerprint": (
                    audit_finding["evidence_fingerprint"]
                    if audit_finding is not None
                    else None
                ),
            }
        )
    )
    previous_status = latest["status"] if latest is not None else None
    cursor = conn.execute(
        """INSERT INTO hypothesis_evaluations(
             hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,
             evidence_class,previous_status,evidence_for_json,
             evidence_against_json,confounders_json,sample_size_json,
             effect_summary_json,stability_json,confidence,status,
             change_reason,change_conditions,source_analysis_version,
             input_fingerprint,evidence_fingerprint,previous_evaluation_id,
             comparison_evaluation_id,new_eligible_observations,
             compatible_with_prior,transition_applied,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            hypothesis["hypothesis_id"],
            run["run_id"],
            finding["finding_id"] if finding is not None else None,
            tested_at,
            run["analysis_from"],
            run["analysis_to"],
            decision.evidence_class,
            previous_status,
            payloads["evidence_for_json"],
            payloads["evidence_against_json"],
            payloads["confounders_json"],
            payloads["sample_size_json"],
            payloads["effect_summary_json"],
            payloads["stability_json"],
            decision.confidence,
            decision.status,
            decision.change_reason,
            canonical_json(decision.conditions),
            analysis_version,
            input_fingerprint_value,
            evidence_fp,
            latest["id"] if latest is not None else None,
            decision.comparison_evaluation_id,
            decision.new_eligible_observations,
            int(decision.compatible_with_prior),
            int(decision.transition_applied),
            created_at,
        ),
    )
    evaluation_id = int(cursor.lastrowid)
    evidence_ids = _insert_evidence_items(
        conn,
        hypothesis_id=hypothesis["hypothesis_id"],
        evaluation_id=evaluation_id,
        finding=finding,
        decision=decision,
        analysis_version=analysis_version,
        range_from=run["analysis_from"],
        range_to=run["analysis_to"],
        created_at=created_at,
    )
    evaluation = _fetch_one(
        conn, "SELECT * FROM hypothesis_evaluations WHERE id=?", (evaluation_id,)
    )
    assert evaluation is not None
    return {
        "evaluation": evaluation,
        "created": True,
        "no_op_reason": None,
        "evidence_item_ids": evidence_ids,
    }


def _promote_stored_finding(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    finding_id_value: str,
    explicit: bool = True,
    created_at: str | None = None,
    compatibility_proofs: Mapping[
        tuple[str, str, str, str],
        Mapping[str, str],
    ] | None = None,
) -> dict[str, Any]:
    """Internal automatic primitive for one already persisted finding.

    ``explicit=False`` is the automatic path and accepts only a Phase 4
    discovery pass.  ``explicit=True`` also permits an eligible exploratory
    screen.  No numerical or status override is accepted.
    """

    require_ledger_schema(conn)
    run_id = _sha(run_id, "run_id")
    finding_id_value = _sha(finding_id_value, "finding_id")
    created = _timestamp(created_at or _now(), "created_at")
    if type(explicit) is not bool:
        raise _error("validation_error", "explicit must be boolean", validation=True)
    run = _fetch_one(
        conn,
        """SELECT r.*,b.analysis_version,b.registry_version,b.engine_sha256,
                  b.registry_sha256,b.run_kind
           FROM analysis_runs r
           JOIN analysis_batches b ON b.batch_id=r.batch_id
           WHERE r.run_id=?""",
        (run_id,),
    )
    if run is None:
        raise _error("unknown_run", "analysis run does not exist", validation=True)
    assert_terminal_batch_integrity(conn, batch_id=run["batch_id"])
    finding = _load_finding(conn, finding_id_value)
    if not finding_belongs_to_run(
        conn,
        run_id=run_id,
        finding_id_value=finding_id_value,
    ):
        raise _error("mismatched_finding", "finding does not belong to the run")
    _assert_finding_run_semantics(finding, run)
    hypothesis, hypothesis_created = _ensure_hypothesis(
        conn,
        run=run,
        finding=finding,
        explicit=explicit,
        created_at=created,
    )
    evaluation = _append_evaluation(
        conn,
        hypothesis=hypothesis,
        run=run,
        finding=finding,
        explicit=explicit,
        dormancy_reason=None,
        compatibility_proofs=compatibility_proofs or {},
        created_at=created,
    )
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "hypothesis_id": hypothesis["hypothesis_id"],
        "hypothesis_created": hypothesis_created,
        "evaluation": evaluation,
    }


def promote_verified_finding(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    verified: _VerifiedPhase4Finding,
    explicit: bool = True,
    created_at: str | None = None,
    compatibility_proofs: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Promote only when replay evidence exactly matches the persisted run/finding."""

    if (
        not isinstance(verified, _VerifiedPhase4Finding)
        or verified._token is not _VERIFIED_TOKEN
    ):
        raise _error(
            "unverified_engine_output",
            "promotion requires a direct Phase 4 replay seal",
            validation=True,
        )
    run = _fetch_one(conn, "SELECT * FROM analysis_runs WHERE run_id=?", (run_id,))
    if run is None:
        raise _error("unknown_run", "analysis run does not exist", validation=True)
    meta = verified.payload["meta"]
    finding = verified.payload["finding"]
    if (
        meta["input_fingerprint"] != run["input_fingerprint"]
        or not finding_belongs_to_run(
            conn,
            run_id=run_id,
            finding_id_value=finding["finding_id"],
        )
    ):
        raise _error("stale_finding", "verified replay does not match the persisted run")
    stored = _load_finding(conn, finding["finding_id"])
    if (
        stored["evidence_fingerprint"]
        != finding["provenance"]["evidence_fingerprint"]
        and canonical_json(_finding_retry_material(stored["evidence"]))
        != canonical_json(_finding_retry_material(finding))
    ):
        raise _error("stale_finding", "verified replay evidence changed")
    proof_index = _normalized_compatibility_proofs(compatibility_proofs)
    return _promote_stored_finding(
        conn,
        run_id=run_id,
        finding_id_value=finding["finding_id"],
        explicit=explicit,
        created_at=created_at,
        compatibility_proofs=proof_index,
    )


def refresh_batch_hypotheses(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    dormancy_reason: str | None = None,
    created_at: str | None = None,
    compatibility_proofs: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Process the state-bearing terminal runs through the Section 11.6 ledger.

    Discovery-pass findings auto-create hypotheses.  Existing conceptual
    hypotheses are reevaluated from the matching finding.  A monthly batch
    applies transitions only from its all-history primary run; its recent and
    historical runs remain immutable comparison evidence.  Monthly callers
    may pass one exact dormancy class for hypotheses with no evaluable finding;
    other callers leave them unchanged.
    """

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    created = _timestamp(created_at or _now(), "created_at")
    proof_index = _normalized_compatibility_proofs(compatibility_proofs)
    if dormancy_reason is not None:
        _enum(
            dormancy_reason,
            frozenset(
                {"dormant_no_eligible_data", "dormant_stale_prerequisite"}
            ),
            "dormancy_reason",
        )
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (batch_id,)
    )
    if batch is None:
        raise _error("unknown_batch", "analysis batch does not exist", validation=True)
    assert_terminal_batch_integrity(conn, batch_id=batch_id)
    if dormancy_reason is not None and batch["run_kind"] != "monthly":
        raise _error(
            "validation_error",
            "only a monthly review may apply dormancy",
            validation=True,
        )
    run_cursor = conn.execute(
        """SELECT r.*,b.analysis_version,b.registry_version,b.engine_sha256,
                  b.registry_sha256,b.run_kind
           FROM analysis_runs r
           JOIN analysis_batches b ON b.batch_id=r.batch_id
           JOIN analysis_range_requests q
             ON q.range_id=r.range_id AND q.batch_id=r.batch_id
           WHERE r.batch_id=?
             AND r.status IN ('completed','insufficient_data','no_data')
             AND (b.run_kind<>'monthly' OR q.range_role='primary')
           ORDER BY COALESCE(r.analysis_from,''),COALESCE(r.analysis_to,''),
                    r.outcome_key,r.outcome_mode,r.run_id""",
        (batch_id,),
    )
    columns = [item[0] for item in run_cursor.description]
    runs = [_row_dict(item, columns) for item in run_cursor.fetchall()]
    summary = {
        "hypotheses_created": 0,
        "evaluations_created": 0,
        "same_fingerprint_noops": 0,
        "incompatible_version_noops": 0,
        "dormant_created": 0,
        "skipped_insufficient": 0,
        "skipped_context_only": 0,
    }
    for run in runs:
        touched: set[str] = set()
        findings = sorted(
            _findings_for_run(conn, run),
            key=lambda item: item["finding_id"],
        )
        for finding in findings:
            _assert_finding_run_semantics(finding, run)
        by_dedupe = {_hypothesis_dedupe(item): item for item in findings}
        # Automatic creation is deliberately limited to a complete first pass.
        for finding in findings:
            if (
                finding["quality_tier"] != "exploratory_unreplicated"
                or not bool(finding["eligible_for_hypothesis"])
            ):
                continue
            hypothesis, was_created = _ensure_hypothesis(
                conn,
                run=run,
                finding=finding,
                explicit=False,
                created_at=created,
            )
            if was_created:
                summary["hypotheses_created"] += 1
            result = _append_evaluation(
                conn,
                hypothesis=hypothesis,
                run=run,
                finding=finding,
                explicit=False,
                dormancy_reason=None,
                compatibility_proofs=proof_index,
                created_at=created,
            )
            touched.add(hypothesis["hypothesis_id"])
            if result["created"]:
                summary["evaluations_created"] += 1
            elif result["no_op_reason"] == "incompatible_version_without_finding":
                summary["incompatible_version_noops"] += 1
            else:
                summary["same_fingerprint_noops"] += 1

        hypothesis_cursor = conn.execute(
            """SELECT * FROM hypotheses
               WHERE outcome_key=? AND outcome_mode=? ORDER BY hypothesis_id""",
            (run["outcome_key"], run["outcome_mode"]),
        )
        hypothesis_columns = [item[0] for item in hypothesis_cursor.description]
        hypotheses = [
            _row_dict(item, hypothesis_columns)
            for item in hypothesis_cursor.fetchall()
        ]
        for hypothesis in hypotheses:
            if hypothesis["hypothesis_id"] in touched:
                continue
            finding = by_dedupe.get(hypothesis["dedupe_key"])
            audit_finding = None
            effective_dormancy = dormancy_reason
            if finding is not None:
                if _is_context_only_finding(finding):
                    if dormancy_reason is None:
                        summary["skipped_context_only"] += 1
                        continue
                    audit_finding = finding
                    finding = None
                else:
                    state = _finding_state(finding)
                    if not state["base_pass"]:
                        if dormancy_reason is None:
                            summary["skipped_insufficient"] += 1
                            continue
                        audit_finding = finding
                        finding = None
            elif dormancy_reason is None:
                continue
            result = _append_evaluation(
                conn,
                hypothesis=hypothesis,
                run=run,
                finding=finding,
                explicit=False,
                dormancy_reason=effective_dormancy if finding is None else None,
                compatibility_proofs=proof_index,
                created_at=created,
                audit_finding=audit_finding,
            )
            touched.add(hypothesis["hypothesis_id"])
            if result["created"]:
                summary["evaluations_created"] += 1
                if finding is None:
                    summary["dormant_created"] += 1
            elif result["no_op_reason"] == "incompatible_version_without_finding":
                summary["incompatible_version_noops"] += 1
            else:
                summary["same_fingerprint_noops"] += 1
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "batch_id": batch_id,
        **summary,
    }


def canonical_annotation_identity(
    *,
    hypothesis_id: str,
    evaluation_id: int | None,
    annotation_kind: str,
    content: str,
    source: str,
    synthesis_id: str | None,
    context_version: str | None,
    prompt_sha256: str | None,
    model_id: str | None,
    provider: str | None,
    supersedes_id: str | None,
) -> dict[str, str]:
    """Return the one canonical annotation input hash and deterministic ID.

    This pure helper is shared with synthesis validation; it deliberately has
    no numerical-evidence or hypothesis-status fields.
    """

    input_hash = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "hypothesis_annotation_input",
            "hypothesis_id": hypothesis_id,
            "evaluation_id": evaluation_id,
            "annotation_kind": annotation_kind,
            "content": content,
            "source": source,
            "synthesis_id": synthesis_id,
            "context_version": context_version,
            "prompt_sha256": prompt_sha256,
            "model_id": model_id,
            "provider": provider,
            "supersedes_id": supersedes_id,
        }
    )
    return {
        "input_sha256": input_hash,
        "annotation_id": sha256_id(
            {
                "contract_version": LEDGER_CONTRACT_VERSION,
                "kind": "hypothesis_annotation",
                "input_sha256": input_hash,
            }
        ),
    }


def append_annotation(
    conn: sqlite3.Connection,
    *,
    hypothesis_id: str,
    annotation_kind: str,
    content: str,
    source: str,
    evaluation_id: int | None = None,
    synthesis_id: str | None = None,
    context_version: str | None = None,
    prompt_sha256: str | None = None,
    model_id: str | None = None,
    provider: str | None = None,
    supersedes_id: str | None = None,
    annotation_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Append one owner/model prose annotation without accepting evidence fields."""

    require_ledger_schema(conn)
    hypothesis_id = _sha(hypothesis_id, "hypothesis_id")
    annotation_kind = _enum(
        annotation_kind, _ANNOTATION_KINDS, "annotation_kind"
    )
    limit = 2000 if annotation_kind in {"mechanism", "owner_note"} else 1000
    content = _text(content.strip(), "content", maximum=limit)
    source = _enum(
        source,
        frozenset({"owner", "glm_synthesis", "hermes_synthesis"}),
        "source",
    )
    created = _timestamp(created_at or _now(), "created_at")
    if _fetch_one(
        conn,
        "SELECT hypothesis_id FROM hypotheses WHERE hypothesis_id=?",
        (hypothesis_id,),
    ) is None:
        raise _error("unknown_hypothesis", "hypothesis does not exist", validation=True)
    if evaluation_id is not None:
        evaluation_id = _integer(
            evaluation_id,
            "evaluation_id",
            minimum=1,
            maximum=SQLITE_MAX_ROWID,
        )
        linked = _fetch_one(
            conn,
            """SELECT id FROM hypothesis_evaluations
               WHERE id=? AND hypothesis_id=?""",
            (evaluation_id, hypothesis_id),
        )
        if linked is None:
            raise _error(
                "mismatched_evaluation",
                "evaluation does not belong to hypothesis",
                validation=True,
            )
    if source == "owner":
        if any(
            value is not None
            for value in (
                synthesis_id,
                context_version,
                prompt_sha256,
                model_id,
                provider,
            )
        ):
            raise _error(
                "validation_error",
                "owner annotation cannot claim synthesis/model provenance",
                validation=True,
            )
    else:
        if annotation_kind == "owner_note":
            raise _error(
                "validation_error",
                "Hermes synthesis cannot create an owner_note annotation",
                validation=True,
            )
        synthesis_id = _sha(synthesis_id, "synthesis_id")
        context_version = _text(
            context_version, "context_version", maximum=120, token=True
        )
        if context_version != "2":
            raise _error(
                "validation_error",
                "Hermes synthesis annotation requires context_version 2",
                validation=True,
            )
        prompt_sha256 = _sha(prompt_sha256, "prompt_sha256")
        if (
            not isinstance(model_id, str)
            or _MODEL_PROVENANCE_RE.fullmatch(model_id) is None
            or not isinstance(provider, str)
            or _MODEL_PROVENANCE_RE.fullmatch(provider) is None
        ):
            raise _error(
                "validation_error",
                "Hermes synthesis provenance identifiers are not canonical",
                validation=True,
            )
        if source == "glm_synthesis" and (
            model_id != "z-ai/glm-5.2" or provider != "openrouter"
        ):
            raise _error(
                "validation_error",
                "legacy GLM synthesis provenance must use "
                "z-ai/glm-5.2 through openrouter",
                validation=True,
            )
        synthesis = _fetch_one(
            conn,
            """SELECT synthesis_id,status,context_version,prompt_sha256,
                      model_id,provider
               FROM synthesis_runs WHERE synthesis_id=?""",
            (synthesis_id,),
        )
        if synthesis is None:
            raise _error(
                "unknown_synthesis",
                "synthesis run does not exist",
                validation=True,
            )
        if synthesis["status"] != "completed":
            raise _error(
                "incomplete_synthesis",
                "Hermes annotation requires a completed synthesis",
                validation=True,
            )
        for field, supplied in (
            ("context_version", context_version),
            ("prompt_sha256", prompt_sha256),
            ("model_id", model_id),
            ("provider", provider),
        ):
            if synthesis[field] != supplied:
                raise _error(
                    "mismatched_synthesis_provenance",
                    f"annotation {field} does not match synthesis",
                    validation=True,
                )
        if evaluation_id is None:
            raise _error(
                "mismatched_synthesis_reference",
                "Hermes synthesis annotation requires its exact referenced evaluation",
                validation=True,
            )
        synthesis_evaluations = {
            int(row[0])
            for row in conn.execute(
                """SELECT evaluation_id FROM synthesis_hypothesis_refs
                   WHERE synthesis_id=? AND hypothesis_id=?""",
                (synthesis_id, hypothesis_id),
            )
        }
        if not synthesis_evaluations:
            raise _error(
                "mismatched_synthesis_reference",
                "synthesis does not reference the annotated hypothesis",
                validation=True,
            )
        if evaluation_id not in synthesis_evaluations:
            raise _error(
                "mismatched_synthesis_reference",
                "synthesis does not reference the annotated evaluation",
                validation=True,
            )
    superseding_child: dict[str, Any] | None = None
    if supersedes_id is not None:
        supersedes_id = _sha(supersedes_id, "supersedes_id")
        prior = _fetch_one(
            conn,
            "SELECT * FROM hypothesis_annotations WHERE annotation_id=?",
            (supersedes_id,),
        )
        if prior is None:
            raise _error(
                "unknown_annotation",
                "superseded annotation does not exist",
                validation=True,
            )
        if (
            prior["hypothesis_id"] != hypothesis_id
            or prior["annotation_kind"] != annotation_kind
        ):
            raise _error(
                "mismatched_annotation",
                "superseded annotation must share hypothesis and kind",
                validation=True,
            )
        superseding_child = _fetch_one(
            conn,
            "SELECT * FROM hypothesis_annotations WHERE supersedes_id=?",
            (supersedes_id,),
        )
    identity = canonical_annotation_identity(
        hypothesis_id=hypothesis_id,
        evaluation_id=evaluation_id,
        annotation_kind=annotation_kind,
        content=content,
        source=source,
        synthesis_id=synthesis_id,
        context_version=context_version,
        prompt_sha256=prompt_sha256,
        model_id=model_id,
        provider=provider,
        supersedes_id=supersedes_id,
    )
    input_hash = identity["input_sha256"]
    derived_id = identity["annotation_id"]
    if annotation_id is not None and _sha(annotation_id, "annotation_id") != derived_id:
        raise _error(
            "mismatched_annotation",
            "annotation ID must match canonical input",
            validation=True,
        )
    annotation_id = derived_id
    if (
        superseding_child is not None
        and superseding_child["annotation_id"] != annotation_id
    ):
        raise _error(
            "stale_annotation",
            "only the current annotation leaf may be superseded",
            validation=True,
        )
    expected = {
        "annotation_id": annotation_id,
        "hypothesis_id": hypothesis_id,
        "evaluation_id": evaluation_id,
        "annotation_kind": annotation_kind,
        "content": content,
        "source": source,
        "synthesis_id": synthesis_id,
        "context_version": context_version,
        "prompt_sha256": prompt_sha256,
        "model_id": model_id,
        "provider": provider,
        "supersedes_id": supersedes_id,
        "input_sha256": input_hash,
        "created_at": created,
    }
    existing = _fetch_one(
        conn,
        "SELECT * FROM hypothesis_annotations WHERE annotation_id=?",
        (annotation_id,),
    )
    if existing is not None:
        _assert_existing(
            existing,
            expected,
            name="hypothesis annotation",
            ignored=frozenset({"created_at"}),
        )
        return {**existing, "created": False}
    conn.execute(
        """INSERT INTO hypothesis_annotations(
             annotation_id,hypothesis_id,evaluation_id,annotation_kind,content,
             source,synthesis_id,context_version,prompt_sha256,model_id,provider,
             supersedes_id,input_sha256,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        tuple(expected.values()),
    )
    return {**expected, "created": True}


def _evaluation_public(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if not key.endswith("_json")
    }


def _latest_annotations(
    annotations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    superseded = {
        item["supersedes_id"]
        for item in annotations
        if item["supersedes_id"] is not None
    }
    return [
        dict(item)
        for item in annotations
        if item["annotation_id"] not in superseded
    ]


def list_hypotheses(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    outcome_key: str | None = None,
    limit: int = 50,
    before: str | None = None,
) -> dict[str, Any]:
    """Return JSON-safe cursor-paginated hypotheses with latest evaluations."""

    require_ledger_schema(conn)
    if status is not None:
        status = _enum(status, HYPOTHESIS_STATUSES, "status")
    if outcome_key is not None:
        outcome_key = _feature(outcome_key, "outcome_key")
    limit = _integer(limit, "limit", minimum=1, maximum=100)
    before_created: str | None = None
    before_id: str | None = None
    if before is not None:
        before = _sha(before, "before")
        cursor_row = _fetch_one(
            conn,
            "SELECT hypothesis_id,created_at FROM hypotheses WHERE hypothesis_id=?",
            (before,),
        )
        if cursor_row is None:
            raise _error("invalid_cursor", "hypothesis cursor does not exist", validation=True)
        before_created, before_id = cursor_row["created_at"], cursor_row["hypothesis_id"]
    clauses: list[str] = []
    params: list[Any] = []
    if outcome_key is not None:
        clauses.append("h.outcome_key=?")
        params.append(outcome_key)
    if before_created is not None:
        clauses.append("(h.created_at<? OR (h.created_at=? AND h.hypothesis_id<?))")
        params.extend((before_created, before_created, before_id))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    cursor = conn.execute(
        f"""SELECT h.* FROM hypotheses h{where}
            ORDER BY h.created_at DESC,h.hypothesis_id DESC""",
        params,
    )
    columns = [item[0] for item in cursor.description]
    items: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        hypothesis = _row_dict(raw, columns)
        evaluations = _load_evaluations(conn, hypothesis["hypothesis_id"])
        latest = evaluations[-1] if evaluations else None
        if status is not None and (latest is None or latest["status"] != status):
            continue
        components = [
            _row_dict(item, ["position", "exposure_key", "lag_days", "window_days", "transform"])
            for item in conn.execute(
                """SELECT position,exposure_key,lag_days,window_days,transform
                   FROM hypothesis_components
                   WHERE hypothesis_id=? ORDER BY position""",
                (hypothesis["hypothesis_id"],),
            )
        ]
        items.append(
            {
                **hypothesis,
                "components": components,
                "latest_evaluation": (
                    _evaluation_public(latest) if latest is not None else None
                ),
            }
        )
        if len(items) == limit + 1:
            break
    has_more = len(items) > limit
    visible = items[:limit]
    next_before = visible[-1]["hypothesis_id"] if has_more else None
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "items": visible,
        "next_before": next_before,
    }


def hypothesis_brief(
    conn: sqlite3.Connection,
    hypothesis_id: str,
) -> dict[str, Any]:
    """Return one fully audited hypothesis, evaluation and immutable evidence history."""

    require_ledger_schema(conn)
    hypothesis_id = _sha(hypothesis_id, "hypothesis_id")
    hypothesis = _fetch_one(
        conn, "SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)
    )
    if hypothesis is None:
        raise _error("unknown_hypothesis", "hypothesis does not exist", validation=True)
    components = [
        _row_dict(item, ["position", "exposure_key", "lag_days", "window_days", "transform"])
        for item in conn.execute(
            """SELECT position,exposure_key,lag_days,window_days,transform
               FROM hypothesis_components WHERE hypothesis_id=? ORDER BY position""",
            (hypothesis_id,),
        )
    ]
    evaluations = _load_evaluations(conn, hypothesis_id)
    evidence_cursor = conn.execute(
        """SELECT e.*,f.evidence_json
           FROM hypothesis_evidence_items e
           JOIN analysis_findings f ON f.finding_id=e.finding_id
           WHERE e.hypothesis_id=?
           ORDER BY e.created_at,e.evidence_item_id""",
        (hypothesis_id,),
    )
    evidence_columns = [item[0] for item in evidence_cursor.description]
    evidence_items: list[dict[str, Any]] = []
    for raw in evidence_cursor.fetchall():
        item = _row_dict(raw, evidence_columns)
        evidence = _parse_canonical(item.pop("evidence_json"), "finding.evidence_json")
        version = evidence.get("provenance", {}).get("analysis_version")
        if (
            not isinstance(version, str)
            or _evidence_fingerprint_for(evidence, version)
            != item["evidence_fingerprint"]
        ):
            raise _error("ledger_corrupt", "evidence item fingerprint is invalid")
        evidence_items.append({**item, "finding": evidence})
    annotation_cursor = conn.execute(
        """SELECT * FROM hypothesis_annotations
           WHERE hypothesis_id=? ORDER BY created_at,annotation_id""",
        (hypothesis_id,),
    )
    annotation_columns = [item[0] for item in annotation_cursor.description]
    annotations = [
        _row_dict(item, annotation_columns)
        for item in annotation_cursor.fetchall()
    ]
    latest = evaluations[-1] if evaluations else None
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "hypothesis": {
            **hypothesis,
            "components": components,
            "latest_evaluation": (
                _evaluation_public(latest) if latest is not None else None
            ),
        },
        "evaluations": [_evaluation_public(item) for item in evaluations],
        "evidence_for": [
            item for item in evidence_items if item["polarity"] == "for"
        ],
        "evidence_against": [
            item for item in evidence_items if item["polarity"] == "against"
        ],
        "annotations": annotations,
        "latest_annotations": _latest_annotations(annotations),
    }


__all__ = [
    "ANALYSIS_VERSION",
    "BATCH_KINDS",
    "CONFIDENCE_VALUES",
    "EVIDENCE_CLASSES",
    "HYPOTHESIS_STATUSES",
    "LEDGER_CONTRACT_VERSION",
    "LedgerError",
    "RANGE_PLAN_VERSION",
    "RUN_STATUSES",
    "SEMANTIC_COMPATIBILITY_SCOPE",
    "TransitionDecision",
    "add_analysis_range",
    "append_annotation",
    "assert_terminal_batch_integrity",
    "canonical_annotation_identity",
    "canonical_semantic_compatibility_proof",
    "compute_verified_analysis",
    "compute_verified_finding",
    "create_analysis_batch",
    "fail_analysis_run",
    "finding_belongs_to_run",
    "finalize_analysis_batch",
    "hypothesis_brief",
    "list_hypotheses",
    "persist_analysis_run",
    "promote_verified_finding",
    "refresh_batch_hypotheses",
    "require_ledger_schema",
    "start_analysis_run",
]
