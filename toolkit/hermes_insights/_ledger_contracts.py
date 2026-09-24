"""Shared ledger contracts, value validation and canonical identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
import re
from typing import Any, Iterable, Mapping

from .contracts import canonical_json
from .provenance import ANALYSIS_CONTRACT_VERSION, sha256_id


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
