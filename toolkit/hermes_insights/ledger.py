"""Canonical Phase 5 analysis and hypothesis-ledger persistence.

The module is deliberately a database *helper*, not another command surface.
Command modules own CLI-boundary validation and transactions. This module
validates the engine boundary again, derives every identifier and numerical
field from Phase 4 output, and never commits.

Only private, immutable seals returned directly by the Phase 4 engine/replay
helpers may cross the write boundary.  The sealing path checks the current
engine/version and registry hashes, canonical identifiers, evidence
fingerprints, exact field sets, numerical consistency, finite JSON, and range
agreement.  A browser or model therefore has no API for supplying an effect,
count, status, confidence, or transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from . import associations
from .contracts import AdapterContext, DateRange, REGISTRY_VERSION, canonical_json
from .provenance import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_VERSION,
    engine_sha256,
    evidence_fingerprint as evidence_fingerprint,
)
from .registry import registry_content_checksum
from .settings import TIMEZONE_NAME
from ._ledger_contracts import (
    BATCH_KINDS,
    BATCH_STATUSES as BATCH_STATUSES,
    CONFIDENCE_VALUES,
    EVIDENCE_CLASSES,
    HYPOTHESIS_STATUSES,
    LEDGER_CONTRACT_VERSION,
    LedgerError,
    RANGE_PLAN_VERSION,
    RUN_STATUSES,
    SEMANTIC_COMPATIBILITY_SCOPE,
    SQLITE_MAX_ROWID as SQLITE_MAX_ROWID,
    TransitionDecision,
    _ANALYSIS_KEYS,
    _META_KEYS,
    _canonical_copy,
    _error,
    _evidence_fingerprint_for as _evidence_fingerprint_for,
    _exact_mapping,
    _feature,
    _normalized_compatibility_proofs,
    _range,
    _sha,
    canonical_annotation_identity,
    canonical_semantic_compatibility_proof,
)
from . import _ledger_hypotheses as _hypotheses
from ._ledger_hypotheses import hypothesis_brief, list_hypotheses
from . import _ledger_store as _store
from ._ledger_store import (
    _fetch_one,
    _finding_retry_material,
    _load_finding,
    _persist_finding as _persist_finding,
    add_analysis_range,
    assert_terminal_batch_integrity,
    require_ledger_schema,
)
from ._ledger_transitions import _transition as _transition
from ._ledger_validation import (
    _definition_descriptor_map,
    _validate_analysis_coverage,
    _validate_engine_meta,
    _verify_finding,
)

_VERIFIED_TOKEN = object()


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    _validate_engine_meta(meta, timezone_name=TIMEZONE_NAME)
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
    _validate_engine_meta(meta, timezone_name=TIMEZONE_NAME)
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

    return _store.create_analysis_batch(
        conn,
        run_kind=run_kind,
        anchor_date=anchor_date,
        outcome_selection=outcome_selection,
        outcomes=outcomes,
        ranges=ranges,
        registry_sha256_value=registry_sha256_value,
        initiator_key=initiator_key,
        range_plan_version=range_plan_version,
        analysis_version=analysis_version,
        registry_version=registry_version,
        engine_sha256_value=engine_sha256_value,
        started_at=started_at,
        clock=lambda: _now(),
    )


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

    return _store.start_analysis_run(
        conn,
        batch_id=batch_id,
        range_id=range_id,
        outcome_key=outcome_key,
        outcome_mode=outcome_mode,
        batch_outcomes=batch_outcomes,
        started_at=started_at,
        clock=lambda: _now(),
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

    return _store._persist_analysis_run(
        conn,
        run_id=run_id,
        read_payload=lambda: verified.payload,
        completed_at=completed_at,
        clock=lambda: _now(),
        timezone_name=TIMEZONE_NAME,
    )


def fail_analysis_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    reason_code: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Mark a running analysis attempt failed without fabricating result JSON."""

    return _store.fail_analysis_run(
        conn,
        run_id=run_id,
        reason_code=reason_code,
        completed_at=completed_at,
        clock=lambda: _now(),
    )


def finalize_analysis_batch(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Finalize a batch after every planned run has reached a terminal state."""

    return _store.finalize_analysis_batch(
        conn,
        batch_id=batch_id,
        completed_at=completed_at,
        clock=lambda: _now(),
    )


def _run_result_payload(
    conn: sqlite3.Connection,
    run: Mapping[str, Any],
) -> dict[str, Any]:
    """Return and authenticate the immutable public result attached to a run."""

    return _store._run_result_payload(
        conn,
        run,
        timezone_name=TIMEZONE_NAME,
    )


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

    return _store.finding_belongs_to_run(
        conn,
        run_id=run_id,
        finding_id_value=finding_id_value,
        timezone_name=TIMEZONE_NAME,
    )


def _findings_for_run(
    conn: sqlite3.Connection,
    run: Mapping[str, Any],
) -> list[dict[str, Any]]:

    return _store._findings_for_run(
        conn,
        run,
        timezone_name=TIMEZONE_NAME,
    )


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

    return _hypotheses._promote_stored_finding(
        conn,
        run_id=run_id,
        finding_id_value=finding_id_value,
        explicit=explicit,
        created_at=created_at,
        compatibility_proofs=compatibility_proofs,
        clock=lambda: _now(),
        timezone_name=TIMEZONE_NAME,
    )


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

    return _hypotheses.refresh_batch_hypotheses(
        conn,
        batch_id=batch_id,
        dormancy_reason=dormancy_reason,
        created_at=created_at,
        compatibility_proofs=compatibility_proofs,
        clock=lambda: _now(),
        timezone_name=TIMEZONE_NAME,
    )


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

    return _hypotheses.append_annotation(
        conn,
        hypothesis_id=hypothesis_id,
        annotation_kind=annotation_kind,
        content=content,
        source=source,
        evaluation_id=evaluation_id,
        synthesis_id=synthesis_id,
        context_version=context_version,
        prompt_sha256=prompt_sha256,
        model_id=model_id,
        provider=provider,
        supersedes_id=supersedes_id,
        annotation_id=annotation_id,
        created_at=created_at,
        clock=lambda: _now(),
    )


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
