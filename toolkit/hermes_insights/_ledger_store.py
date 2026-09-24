"""Analysis storage helpers on caller-owned SQLite transactions.

Connections, clocks and timezone are supplied by the ledger facade or caller.
These helpers do not open, close, commit or roll back a connection.
"""

from __future__ import annotations

import json
import sqlite3
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)

from . import associations, migrations
from .contracts import REGISTRY_VERSION, canonical_json
from .provenance import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_VERSION,
    candidate_key,
    engine_sha256,
    finding_id,
    sha256_id,
)
from ._ledger_contracts import (
    BATCH_KINDS,
    LEDGER_CONTRACT_VERSION,
    LedgerError,
    RANGE_PLAN_VERSION,
    _ANALYSIS_KEYS,
    _META_KEYS,
    _date,
    _effect_direction,
    _enum,
    _error,
    _evidence_fingerprint_for,
    _feature,
    _optional_text,
    _sha,
    _text,
    _timestamp,
)
from ._ledger_validation import _component_identity, _validate_analysis_coverage, _validate_engine_meta


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
    clock: Callable[[], str],
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
    started = _timestamp(started_at or clock(), "started_at")
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
    clock: Callable[[], str],
) -> dict[str, Any]:
    """Create or return the deterministic running row for one outcome/mode."""

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    range_id = _sha(range_id, "range_id")
    outcome_key = _feature(outcome_key, "outcome_key")
    outcome_mode = _text(outcome_mode, "outcome_mode", maximum=80, token=True)
    started = _timestamp(started_at or clock(), "started_at")
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


def _persist_analysis_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    read_payload: Callable[[], Mapping[str, Any]],
    completed_at: str | None = None,
    clock: Callable[[], str],
    timezone_name: str,
) -> dict[str, Any]:
    """Read the sealed payload only after run and ancestry guards pass."""

    run_id = _sha(run_id, "run_id")
    completed = _timestamp(completed_at or clock(), "completed_at")
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
    payload = _canonicalize_reused_finding_evidence(conn, read_payload())
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
            stored_payload = _run_result_payload(conn, run, timezone_name=timezone_name)
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
    clock: Callable[[], str],
) -> dict[str, Any]:
    """Mark a running analysis attempt failed without fabricating result JSON."""

    require_ledger_schema(conn)
    run_id = _sha(run_id, "run_id")
    reason_code = _text(reason_code, "reason_code", maximum=120, token=True)
    completed = _timestamp(completed_at or clock(), "completed_at")
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
    clock: Callable[[], str],
) -> dict[str, Any]:
    """Finalize a batch after every planned run has reached a terminal state."""

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    completed = _timestamp(completed_at or clock(), "completed_at")
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
    *,
    timezone_name: str,
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
        _validate_engine_meta(meta, timezone_name=timezone_name)
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
    timezone_name: str,
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
    payload = _run_result_payload(conn, run, timezone_name=timezone_name)
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
    *,
    timezone_name: str,
) -> list[dict[str, Any]]:
    payload = _run_result_payload(conn, run, timezone_name=timezone_name)
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
