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

from . import associations, migrations
from .contracts import AdapterContext, DateRange, REGISTRY_VERSION, canonical_json
from .provenance import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_VERSION,
    candidate_key,
    engine_sha256,
    evidence_fingerprint as evidence_fingerprint,
    finding_id,
    sha256_id,
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
    SQLITE_MAX_ROWID,
    TransitionDecision,
    _ANALYSIS_KEYS,
    _ANNOTATION_KINDS,
    _EVIDENCE_KINDS,
    _META_KEYS,
    _MODEL_PROVENANCE_RE,
    _canonical_copy,
    _date,
    _effect_direction,
    _enum,
    _error,
    _evidence_fingerprint_for,
    _exact_mapping,
    _feature,
    _integer,
    _normalized_compatibility_proofs,
    _optional_text,
    _range,
    _sha,
    _text,
    _timestamp,
    canonical_annotation_identity,
    canonical_semantic_compatibility_proof,
)
from ._ledger_transitions import (
    _evaluation_semantics,
    _finding_state,
    _is_context_only_finding,
    _run_semantics,
    _semantic_compatibility,
    _transition,
)
from ._ledger_validation import (
    _component_identity,
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
        _validate_engine_meta(meta, timezone_name=TIMEZONE_NAME)
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
