"""Closed Phase 5 synthesis contract and deterministic Markdown renderer.

The model-facing record accepts references and bounded prose only.  Numerical
evidence, hypothesis state, confidence, and the official Markdown tables are
always reloaded from the immutable Phase 4/5 ledger.  This module opens no
database and chooses no vault path; callers must inject both explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
from typing import Any

from .contracts import ContractError, canonical_json
from .ledger import (
    LEDGER_CONTRACT_VERSION,
    LedgerError,
    SQLITE_MAX_ROWID,
    append_annotation,
    assert_terminal_batch_integrity,
    canonical_annotation_identity,
    finding_belongs_to_run,
    _evidence_fingerprint_for as _versioned_finding_evidence_fingerprint,
)
from .migrations import SchemaError, require_version
from . import orchestrator
from .provenance import (
    ProvenanceError,
    candidate_key as canonical_candidate_key,
    finding_id as canonical_finding_id,
    sha256_id,
)


SYNTHESIS_CONTRACT_VERSION = "synthesis-v1"
SYNTHESIS_CONTEXT_VERSION = "2"
LEGACY_MODEL_ID = "z-ai/glm-5.2"
LEGACY_PROVIDER = "openrouter"

CADENCES = frozenset({"manual", "weekly", "monthly", "trigger"})
SYNTHESIS_STATUSES = frozenset({
    "running", "completed", "insufficient_data", "no_novelty", "suppressed",
    "failed",
})
NO_MESSAGE_STATUSES = frozenset({
    "insufficient_data", "no_novelty", "suppressed", "failed",
})
RUN_PURPOSES = frozenset({"primary", "recent", "historical", "trigger"})
FINDING_ROLES = frozenset({"primary", "supporting", "against", "context"})
HYPOTHESIS_ROLES = frozenset({"active", "changed", "context"})
ANNOTATION_KINDS = frozenset({
    "mechanism", "alternative", "next_experiment", "owner_note",
})

_TOP_LEVEL_KEYS = frozenset({
    "synthesis_id", "analysis_batch_id", "cadence", "reason_code",
    "cutoff_date", "evidence_fingerprint", "context_version", "prompt_sha256",
    "model_id", "provider", "run_refs", "finding_refs", "hypothesis_refs",
    "narrative_md", "rendered_md", "status", "no_message_reason_code",
    "annotations", "notification",
})
_RUN_REF_KEYS = frozenset({"run_id", "purpose"})
_FINDING_REF_KEYS = frozenset({"finding_id", "role"})
_HYPOTHESIS_REF_KEYS = frozenset({
    "hypothesis_id", "evaluation_id", "role",
})
_ANNOTATION_KEYS = frozenset({
    "annotation_id", "hypothesis_id", "evaluation_id", "annotation_kind",
    "content", "source", "synthesis_id", "context_version", "prompt_sha256",
    "model_id", "provider", "supersedes_id", "input_sha256",
})

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODEL_PROVENANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,159}$")
_MAX_REFS = 256
_MAX_ANNOTATIONS = 32
_MAX_NARRATIVE_CHARS = 16_000


class SynthesisError(RuntimeError):
    """Controlled validation, integrity, or append-only synthesis failure."""

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


def _validation(message: str) -> None:
    raise SynthesisError("validation_error", message, validation=True)


def _exact_object(
    value: object,
    allowed: frozenset[str],
    required: frozenset[str],
    what: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _validation(f"{what} must be one JSON object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        _validation(f"unknown {what} key(s): {', '.join(sorted(unknown))}")
    if missing:
        _validation(f"missing {what} key(s): {', '.join(sorted(missing))}")
    return value


def parse_synthesis_json(text: str, *, max_bytes: int = 131_072) -> dict[str, Any]:
    """Parse one bounded UTF-8 synthesis object with no trailing document."""

    if not isinstance(text, str):
        _validation("stdin must be text")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        _validation("stdin must be valid UTF-8")
    if not encoded or len(encoded) > max_bytes:
        _validation(f"stdin must contain 1-{max_bytes} UTF-8 bytes")
    def closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _validation(f"duplicate JSON key is not allowed: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=closed_object)
    except SynthesisError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        detail = (
            exc.msg
            if isinstance(exc, json.JSONDecodeError)
            else "JSON nesting exceeds the supported bound"
            if isinstance(exc, RecursionError)
            else "numeric literal exceeds the supported bound"
        )
        _validation(f"stdin is not valid JSON: {detail}")
    if not isinstance(value, dict):
        _validation("stdin must contain one JSON object")

    stack: list[tuple[object, int]] = [(value, 0)]
    item_count = 0
    while stack:
        item, depth = stack.pop()
        item_count += 1
        if item_count > 50_000:
            _validation("stdin JSON exceeds the supported item count")
        if depth > 128:
            _validation("stdin JSON nesting exceeds 128 levels")
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                _validation("stdin JSON strings must be valid UTF-8")
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
    return value


def _bounded_text(
    value: object,
    field: str,
    maximum: int,
    *,
    nullable: bool = False,
    strip: bool = True,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        _validation(f"{field} must be text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _validation(f"{field} must be valid UTF-8 text")
    normalized = value.strip() if strip else value
    if not normalized.strip():
        _validation(f"{field} must not be blank")
    if len(value) > maximum:
        _validation(f"{field} exceeds {maximum} characters")
    return normalized


def _code(value: object, field: str, *, nullable: bool = False) -> str | None:
    result = _bounded_text(value, field, 80, nullable=nullable)
    if result is not None and not _CODE_RE.fullmatch(result):
        _validation(f"{field} must be a lower-case reason code")
    return result


def _iso_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        _validation(f"{field} must be ISO YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _validation(f"{field} must be ISO YYYY-MM-DD")
    if parsed.isoformat() != value:
        _validation(f"{field} must be canonical ISO YYYY-MM-DD")
    return value


def _sha256(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        _validation(f"{field} must be a SHA-256 identifier")
    if _SHA256_RE.fullmatch(value) is None:
        _validation(f"{field} must be a canonical sha256:<64hex> identifier")
    return value


def _model_provenance_identifier(
    value: object, field: str, *, nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    normalized = _bounded_text(value, field, 160, nullable=nullable)
    if normalized is not None and (
        normalized != value or _MODEL_PROVENANCE_RE.fullmatch(normalized) is None
    ):
        _validation(f"{field} must be a canonical external model identifier")
    return normalized


def _canonical_timestamp(value: object, field: str) -> str:
    """Validate one timezone-aware, canonical ISO-8601 timestamp."""

    if not isinstance(value, str) or not value or len(value) > 64:
        _validation(f"{field} must be a canonical timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _validation(f"{field} must be a canonical timezone-aware ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _validation(f"{field} must include a timezone")
    timespec = "microseconds" if parsed.microsecond else "seconds"
    if parsed.isoformat(timespec=timespec) != value:
        _validation(f"{field} must be a canonical timezone-aware ISO timestamp")
    return value


def _positive_int(value: object, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > SQLITE_MAX_ROWID
    ):
        _validation(f"{field} must be a positive integer")
    return value


def _array(value: object, field: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        _validation(f"{field} must be an array")
    if len(value) > maximum:
        _validation(f"{field} exceeds {maximum} entries")
    return value


def _normalize_run_refs(value: object) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _array(value, "run_refs", _MAX_REFS):
        item = _exact_object(raw, _RUN_REF_KEYS, _RUN_REF_KEYS, "run reference")
        run_id = _sha256(item["run_id"], "run_id")
        assert run_id is not None
        purpose = item["purpose"]
        if purpose not in RUN_PURPOSES:
            _validation(f"run purpose must be one of: {', '.join(sorted(RUN_PURPOSES))}")
        if run_id in seen:
            _validation("run_refs must not repeat a run_id")
        seen.add(run_id)
        refs.append({"run_id": run_id, "purpose": purpose})
    return sorted(refs, key=lambda item: (item["purpose"], item["run_id"]))


def _normalize_finding_refs(value: object) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in _array(value, "finding_refs", _MAX_REFS):
        item = _exact_object(
            raw, _FINDING_REF_KEYS, _FINDING_REF_KEYS, "finding reference",
        )
        finding_id = _sha256(item["finding_id"], "finding_id")
        assert finding_id is not None
        role = item["role"]
        if role not in FINDING_ROLES:
            _validation(
                f"finding role must be one of: {', '.join(sorted(FINDING_ROLES))}"
            )
        key = (finding_id, role)
        if key in seen:
            _validation("finding_refs must not repeat an id/role pair")
        seen.add(key)
        refs.append({"finding_id": finding_id, "role": role})
    return sorted(refs, key=lambda item: (item["role"], item["finding_id"]))


def _normalize_hypothesis_refs(value: object) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in _array(value, "hypothesis_refs", _MAX_REFS):
        item = _exact_object(
            raw, _HYPOTHESIS_REF_KEYS, _HYPOTHESIS_REF_KEYS,
            "hypothesis reference",
        )
        hypothesis_id = _sha256(item["hypothesis_id"], "hypothesis_id")
        assert hypothesis_id is not None
        evaluation_id = _positive_int(item["evaluation_id"], "evaluation_id")
        role = item["role"]
        if role not in HYPOTHESIS_ROLES:
            _validation(
                "hypothesis role must be one of: "
                + ", ".join(sorted(HYPOTHESIS_ROLES))
            )
        key = (hypothesis_id, role)
        if key in seen:
            _validation("hypothesis_refs must not repeat an id/role pair")
        seen.add(key)
        refs.append({
            "hypothesis_id": hypothesis_id,
            "evaluation_id": evaluation_id,
            "role": role,
        })
    return sorted(
        refs,
        key=lambda item: (
            item["role"], item["hypothesis_id"], item["evaluation_id"],
        ),
    )


def annotation_input_sha256(
    *,
    synthesis_id: str,
    evidence_fingerprint: str,
    context_version: str,
    prompt_sha256: str,
    hypothesis_id: str,
    evaluation_id: int | None,
    annotation_kind: str,
    content: str,
    source: str,
    model_id: str,
    provider: str,
    supersedes_id: str | None,
) -> str:
    """Return the ledger-owned canonical annotation input identity.

    ``evidence_fingerprint`` remains in this compatibility-facing helper
    signature because the synthesis prompt is sealed to it.  The annotation
    identity itself is deliberately the single identity defined by
    :func:`ledger.canonical_annotation_identity`.
    """

    _sha256(evidence_fingerprint, "evidence_fingerprint")
    return canonical_annotation_identity(
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
    )["input_sha256"]


def _normalize_annotation(
    raw: object,
    *,
    synthesis_id: str,
    evidence_fingerprint: str,
    context_version: str,
    prompt_sha256: str | None,
    model_id: str | None,
    provider: str | None,
) -> dict[str, Any]:
    item = _exact_object(
        raw, _ANNOTATION_KEYS, _ANNOTATION_KEYS, "annotation",
    )
    annotation_id = _sha256(item["annotation_id"], "annotation_id")
    hypothesis_id = _sha256(item["hypothesis_id"], "hypothesis_id")
    assert annotation_id is not None and hypothesis_id is not None
    evaluation_id = _positive_int(
        item["evaluation_id"], "evaluation_id", nullable=True,
    )
    kind = item["annotation_kind"]
    if kind not in ANNOTATION_KINDS:
        _validation(
            f"annotation_kind must be one of: {', '.join(sorted(ANNOTATION_KINDS))}"
        )
    if kind == "owner_note":
        _validation("model synthesis cannot create an owner_note annotation")
    maximum = 2_000 if kind == "mechanism" else 1_000
    content = _bounded_text(
        item["content"], "content", maximum, nullable=False, strip=True,
    )
    assert content is not None
    source = item["source"]
    if source not in {"glm_synthesis", "hermes_synthesis"}:
        _validation(
            "synthesis annotations require source hermes_synthesis "
            "or the legacy glm_synthesis source"
        )
    if source == "glm_synthesis" and (
        model_id != LEGACY_MODEL_ID or provider != LEGACY_PROVIDER
    ):
        _validation("legacy glm_synthesis annotations require legacy provenance")
    if item["synthesis_id"] != synthesis_id:
        _validation("annotation synthesis_id must match its synthesis")
    if item["context_version"] != context_version:
        _validation("annotation context_version must match its synthesis")
    annotation_prompt = _sha256(item["prompt_sha256"], "annotation.prompt_sha256")
    if annotation_prompt != prompt_sha256:
        _validation("annotation prompt_sha256 must match its synthesis")
    if item["model_id"] != model_id or item["provider"] != provider:
        _validation("annotation model/provider must match its synthesis")
    supersedes_id = item["supersedes_id"]
    if supersedes_id is not None:
        supersedes_id = _sha256(supersedes_id, "supersedes_id")
        assert supersedes_id is not None
        if supersedes_id == annotation_id:
            _validation("an annotation cannot supersede itself")
    input_sha256 = _sha256(item["input_sha256"], "annotation.input_sha256")
    identity = canonical_annotation_identity(
        hypothesis_id=hypothesis_id,
        evaluation_id=evaluation_id,
        annotation_kind=kind,
        content=content,
        source=source,
        synthesis_id=synthesis_id,
        context_version=context_version,
        prompt_sha256=prompt_sha256,
        model_id=model_id,
        provider=provider,
        supersedes_id=supersedes_id,
    )
    if input_sha256 != identity["input_sha256"]:
        _validation("annotation input_sha256 does not match canonical annotation input")
    if annotation_id != identity["annotation_id"]:
        _validation("annotation_id does not match canonical annotation input")
    return {
        "annotation_id": annotation_id,
        "hypothesis_id": hypothesis_id,
        "evaluation_id": evaluation_id,
        "annotation_kind": kind,
        "content": content,
        "source": source,
        "synthesis_id": synthesis_id,
        "context_version": context_version,
        "prompt_sha256": prompt_sha256,
        "model_id": model_id,
        "provider": provider,
        "supersedes_id": supersedes_id,
        "input_sha256": input_sha256,
    }


def validate_synthesis_record(payload: object) -> dict[str, Any]:
    """Validate and normalize the exact ``synthesis-record`` stdin object."""

    item = _exact_object(
        payload, _TOP_LEVEL_KEYS, _TOP_LEVEL_KEYS, "synthesis record",
    )
    synthesis_id = _sha256(item["synthesis_id"], "synthesis_id")
    analysis_batch_id = _sha256(
        item["analysis_batch_id"], "analysis_batch_id",
    )
    assert synthesis_id is not None and analysis_batch_id is not None
    cadence = item["cadence"]
    if cadence not in CADENCES:
        _validation(f"cadence must be one of: {', '.join(sorted(CADENCES))}")
    reason_code = _code(item["reason_code"], "reason_code")
    cutoff_date = _iso_date(item["cutoff_date"], "cutoff_date")
    evidence_fingerprint = _sha256(
        item["evidence_fingerprint"], "evidence_fingerprint",
    )
    assert evidence_fingerprint is not None
    context_version = _bounded_text(
        item["context_version"], "context_version", 40, nullable=False,
    )
    if context_version != SYNTHESIS_CONTEXT_VERSION:
        _validation(
            f"context_version must be the approved version {SYNTHESIS_CONTEXT_VERSION}"
        )
    prompt_sha256 = _sha256(
        item["prompt_sha256"], "prompt_sha256", nullable=True,
    )
    model_id = _model_provenance_identifier(
        item["model_id"], "model_id", nullable=True,
    )
    provider = _model_provenance_identifier(
        item["provider"], "provider", nullable=True,
    )
    provenance_values = (prompt_sha256, model_id, provider)
    if any(value is not None for value in provenance_values):
        if any(value is None for value in provenance_values):
            _validation("prompt_sha256, model_id, and provider must be all set or all null")

    status = item["status"]
    if status not in SYNTHESIS_STATUSES:
        _validation(
            f"status must be one of: {', '.join(sorted(SYNTHESIS_STATUSES))}"
        )
    no_message_reason = _code(
        item["no_message_reason_code"],
        "no_message_reason_code",
        nullable=True,
    )
    narrative = _bounded_text(
        item["narrative_md"], "narrative_md", _MAX_NARRATIVE_CHARS,
        nullable=True, strip=False,
    )
    if item["rendered_md"] is not None:
        _validation("rendered_md is deterministic and must be null on input")
    notification = (
        orchestrator.validate_notification(item["notification"])
        if item["notification"] is not None else None
    )

    if status == "completed":
        if prompt_sha256 is None or narrative is None:
            _validation("completed synthesis requires approved model provenance and narrative")
        if no_message_reason is not None:
            _validation("completed synthesis must not carry a no-message reason")
    elif status in NO_MESSAGE_STATUSES:
        if no_message_reason is None:
            _validation(f"{status} synthesis requires no_message_reason_code")
        if narrative is not None:
            _validation("a no-message synthesis must not carry model narrative")
        if status != "failed" and any(
            value is not None for value in provenance_values
        ):
            _validation(f"{status} synthesis must not invoke a model")
    else:
        if no_message_reason is not None or narrative is not None:
            _validation("running synthesis cannot carry a terminal reason or narrative")
        if any(value is not None for value in provenance_values):
            _validation("running synthesis cannot carry completed model provenance")
    if notification is not None and status != "completed":
        _validation("only a completed synthesis may enqueue a notification")

    run_refs = _normalize_run_refs(item["run_refs"])
    finding_refs = _normalize_finding_refs(item["finding_refs"])
    hypothesis_refs = _normalize_hypothesis_refs(item["hypothesis_refs"])
    if status == "completed" and not (
        run_refs and (finding_refs or hypothesis_refs)
    ):
        _validation(
            "completed synthesis requires a run and at least one finding or hypothesis"
        )

    annotations_raw = _array(item["annotations"], "annotations", _MAX_ANNOTATIONS)
    if annotations_raw and status != "completed":
        _validation("annotations require a completed synthesis")
    annotations = sorted([
        _normalize_annotation(
            raw,
            synthesis_id=synthesis_id,
            evidence_fingerprint=evidence_fingerprint,
            context_version=context_version,
            prompt_sha256=prompt_sha256,
            model_id=model_id,
            provider=provider,
        )
        for raw in annotations_raw
    ], key=lambda annotation: annotation["annotation_id"])
    annotation_ids = [annotation["annotation_id"] for annotation in annotations]
    if len(annotation_ids) != len(set(annotation_ids)):
        _validation("annotations must not repeat annotation_id")
    referenced_evaluations = {
        (reference["hypothesis_id"], reference["evaluation_id"])
        for reference in hypothesis_refs
    }
    for annotation in annotations:
        if annotation["evaluation_id"] is None or (
            annotation["hypothesis_id"], annotation["evaluation_id"]
        ) not in referenced_evaluations:
            _validation(
                "annotation evaluation must exactly appear in hypothesis_refs"
            )

    return {
        "synthesis_id": synthesis_id,
        "analysis_batch_id": analysis_batch_id,
        "cadence": cadence,
        "reason_code": reason_code,
        "cutoff_date": cutoff_date,
        "evidence_fingerprint": evidence_fingerprint,
        "context_version": context_version,
        "prompt_sha256": prompt_sha256,
        "model_id": model_id,
        "provider": provider,
        "run_refs": run_refs,
        "finding_refs": finding_refs,
        "hypothesis_refs": hypothesis_refs,
        "narrative_md": narrative,
        "rendered_md": None,
        "status": status,
        "no_message_reason_code": no_message_reason,
        "annotations": annotations,
        "notification": notification,
    }


def _row_dict(cursor: sqlite3.Cursor, row: object) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, Mapping):
        return dict(row)
    assert cursor.description is not None
    return {
        description[0]: value
        for description, value in zip(cursor.description, row, strict=True)
    }


def _fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> dict[str, Any] | None:
    cursor = conn.execute(sql, tuple(parameters))
    row = cursor.fetchone()
    return None if row is None else _row_dict(cursor, row)


def _fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, tuple(parameters))
    return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _require_phase5(conn: sqlite3.Connection) -> None:
    if conn is None or not hasattr(conn, "execute"):
        raise SynthesisError(
            "validation_error",
            "an injected SQLite connection is required",
            validation=True,
        )
    try:
        require_version(conn, 4)
    except SchemaError as exc:
        raise SynthesisError(
            exc.code, str(exc), validation=bool(getattr(exc, "validation", False)),
        ) from exc


def _canonical_document(
    value: object,
    field: str,
    *,
    expected: type | tuple[type, ...],
) -> Any:
    if not isinstance(value, str):
        raise SynthesisError("integrity_error", f"{field} is not stored JSON")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SynthesisError("integrity_error", f"{field} is invalid JSON") from exc
    if not isinstance(parsed, expected):
        raise SynthesisError("integrity_error", f"{field} has the wrong JSON type")
    try:
        normalized = canonical_json(parsed)
    except ContractError as exc:
        raise SynthesisError("integrity_error", f"{field} is not canonical") from exc
    if normalized != value:
        raise SynthesisError("integrity_error", f"{field} is not canonical JSON")
    return parsed


def _stored_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise SynthesisError(
            "integrity_error", f"{field} is not a canonical sha256:<64hex> identifier",
        )
    return value


def _stored_timestamp(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    try:
        return _canonical_timestamp(value, field)
    except SynthesisError as exc:
        raise SynthesisError(
            "integrity_error", f"{field} is not a canonical timezone-aware timestamp",
        ) from exc


def _verify_run_result(
    row: Mapping[str, Any],
    range_row: Mapping[str, Any],
    batch: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Verify terminal run JSON/hash/range/provenance ancestry."""

    _stored_sha256(row["run_id"], "analysis_runs.run_id")
    _stored_sha256(row["batch_id"], "analysis_runs.batch_id")
    _stored_sha256(row["range_id"], "analysis_runs.range_id")
    _stored_timestamp(row["started_at"], "analysis_runs.started_at")
    if row["status"] == "running":
        raise SynthesisError("stale_reference", "synthesis cannot reference a running run")
    _stored_timestamp(row["completed_at"], "analysis_runs.completed_at")

    result_json = row["result_json"]
    result_sha256 = row["result_sha256"]
    if result_json is None or result_sha256 is None:
        if row["status"] == "failed" and result_json is None and result_sha256 is None:
            return None
        raise SynthesisError("integrity_error", "terminal run result is incomplete")
    result = _canonical_document(
        result_json, "analysis_runs.result_json", expected=dict,
    )
    expected_result_sha = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "analysis_result",
        "result": result,
    })
    if _stored_sha256(result_sha256, "analysis_runs.result_sha256") != expected_result_sha:
        raise SynthesisError("integrity_error", "analysis run result hash drift")

    meta = result.get("meta")
    if not isinstance(meta, Mapping):
        raise SynthesisError("integrity_error", "analysis result meta is missing")
    requested = {
        "kind": range_row["requested_range_kind"],
        "from": range_row["requested_from"],
        "to": range_row["requested_to"],
    }
    actual = {
        "kind": (
            "all" if range_row["requested_range_kind"] == "all" else "bounded"
        ),
        "from": row["analysis_from"],
        "to": row["analysis_to"],
    }
    baseline = {
        "kind": (
            "all" if row["baseline_from"] is None else "bounded"
        ),
        "from": row["baseline_from"],
        "to": row["baseline_to"],
    }
    expected_meta = (
        ("outcome", row["outcome_key"]),
        ("modes", [row["outcome_mode"]]),
        ("input_fingerprint", row["input_fingerprint"]),
        ("analysis_version", batch["analysis_version"]),
        ("registry_version", batch["registry_version"]),
        ("engine_sha256", batch["engine_sha256"]),
        ("registry_sha256", batch["registry_sha256"]),
        ("requested_range", requested),
        ("analysis_range", actual),
        ("baseline_range", baseline),
    )
    for field, expected in expected_meta:
        if meta.get(field) != expected:
            raise SynthesisError(
                "integrity_error", f"analysis result {field} ancestry drift",
            )
    _stored_sha256(row["input_fingerprint"], "analysis_runs.input_fingerprint")
    _stored_sha256(batch["engine_sha256"], "analysis_batches.engine_sha256")
    _stored_sha256(batch["registry_sha256"], "analysis_batches.registry_sha256")

    if range_row["requested_range_kind"] == "bounded":
        if (
            row["range_resolution"] != "bounded_exact"
            or row["analysis_from"] != range_row["requested_from"]
            or row["analysis_to"] != range_row["requested_to"]
        ):
            raise SynthesisError("integrity_error", "bounded run range ancestry drift")
    elif row["range_resolution"] == "all_no_data":
        if row["analysis_from"] is not None or row["analysis_to"] is not None:
            raise SynthesisError("integrity_error", "all-no-data run has bounds")
    elif row["range_resolution"] != "all_observed":
        raise SynthesisError("integrity_error", "all-history range resolution drift")
    return result


def _verify_finding(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    batch: Mapping[str, Any],
    result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    evidence = _canonical_document(
        row["evidence_json"], "analysis_findings.evidence_json", expected=dict,
    )
    evidence_for = _canonical_document(
        row["evidence_for_json"],
        "analysis_findings.evidence_for_json",
        expected=list,
    )
    evidence_against = _canonical_document(
        row["evidence_against_json"],
        "analysis_findings.evidence_against_json",
        expected=list,
    )
    finding_id = _stored_sha256(row["finding_id"], "analysis_findings.finding_id")
    if evidence.get("finding_id") != finding_id:
        raise SynthesisError("integrity_error", "finding_id drift in evidence_json")
    if run["batch_id"] != batch["batch_id"]:
        raise SynthesisError("integrity_error", "finding batch ancestry drift")
    if (
        row["outcome_key"] != run["outcome_key"]
        or row["outcome_mode"] != run["outcome_mode"]
    ):
        raise SynthesisError(
            "integrity_error", "finding outcome/mode does not match its run",
        )
    if evidence.get("candidate_key") != row["candidate_key"]:
        raise SynthesisError("integrity_error", "finding candidate key drift")
    outcome = evidence.get("outcome")
    quality = evidence.get("quality")
    provenance = evidence.get("provenance")
    if not isinstance(outcome, dict) or (
        outcome.get("key") != row["outcome_key"]
        or outcome.get("mode") != row["outcome_mode"]
    ):
        raise SynthesisError("integrity_error", "finding outcome drift")
    if not isinstance(quality, dict) or (
        quality.get("tier") != row["quality_tier"]
        or bool(quality.get("eligible_for_hypothesis"))
        != bool(row["eligible_for_hypothesis"])
    ):
        raise SynthesisError("integrity_error", "finding quality drift")
    if evidence.get("evidence_for") != evidence_for:
        raise SynthesisError("integrity_error", "finding evidence-for drift")
    if evidence.get("evidence_against") != evidence_against:
        raise SynthesisError("integrity_error", "finding evidence-against drift")
    if not isinstance(provenance, dict):
        raise SynthesisError("integrity_error", "finding provenance is malformed")
    for field, value in (
        ("analysis_version", batch["analysis_version"]),
        ("registry_version", batch["registry_version"]),
        ("engine_sha256", batch["engine_sha256"]),
        ("registry_sha256", batch["registry_sha256"]),
        ("input_fingerprint", run["input_fingerprint"]),
    ):
        if provenance.get(field) != value:
            raise SynthesisError(
                "integrity_error", f"finding provenance {field} ancestry drift",
            )
    try:
        expected = _versioned_finding_evidence_fingerprint(
            evidence, provenance["analysis_version"],
        )
    except LedgerError as exc:
        raise SynthesisError("integrity_error", "finding evidence contract is invalid") from exc
    if row["evidence_fingerprint"] != expected:
        raise SynthesisError("integrity_error", "stored finding evidence fingerprint drift")
    if provenance.get("evidence_fingerprint") != expected:
        raise SynthesisError("integrity_error", "finding provenance fingerprint drift")
    try:
        expected_id = canonical_finding_id(
            candidate_key=row["candidate_key"],
            analysis_range={
                "kind": "all" if run["range_resolution"] == "all_observed" else "bounded",
                "from": run["analysis_from"],
                "to": run["analysis_to"],
            },
            baseline_range={
                "kind": "all" if run["baseline_from"] is None else "bounded",
                "from": run["baseline_from"],
                "to": run["baseline_to"],
            },
            input_fingerprint=run["input_fingerprint"],
            analysis_version=batch["analysis_version"],
        )
    except ProvenanceError as exc:
        raise SynthesisError("integrity_error", "finding identity contract is invalid") from exc
    if finding_id != expected_id:
        raise SynthesisError("integrity_error", "finding ID does not match canonical ancestry")

    exposure = evidence.get("exposure")
    components = exposure.get("components") if isinstance(exposure, Mapping) else None
    if not isinstance(components, list) or len(components) not in (1, 2):
        raise SynthesisError("integrity_error", "finding components are malformed")
    stored_components = _fetch_all(
        conn,
        """SELECT position,exposure_key,lag_days,window_days,transform,
                  temporal_direction
             FROM analysis_finding_components
            WHERE finding_id=? ORDER BY position""",
        (finding_id,),
    )
    expected_components = []
    identity_components = []
    for position, component in enumerate(components, 1):
        if not isinstance(component, Mapping):
            raise SynthesisError("integrity_error", "finding component is malformed")
        expected_components.append({
            "position": position,
            "exposure_key": component.get("exposure_key"),
            "lag_days": component.get("lag_days"),
            "window_days": component.get("window_days"),
            "transform": component.get("transform"),
            "temporal_direction": component.get("temporal_direction"),
        })
        identity_components.append({
            "exposure_key": component.get("exposure_key"),
            "lag_days": component.get("lag_days"),
            "window_days": component.get("window_days"),
            "transform": component.get("transform"),
        })
    if stored_components != expected_components:
        raise SynthesisError("integrity_error", "finding component ancestry drift")
    if row["candidate_kind"] != ("single" if len(components) == 1 else "pair"):
        raise SynthesisError("integrity_error", "finding candidate kind drift")
    try:
        expected_candidate_key = canonical_candidate_key(
            row["outcome_key"], row["outcome_mode"], identity_components,
        )
    except ProvenanceError as exc:
        raise SynthesisError(
            "integrity_error", "finding candidate/component identity is invalid",
        ) from exc
    if row["candidate_key"] != expected_candidate_key:
        raise SynthesisError(
            "integrity_error", "finding candidate key/component ancestry drift",
        )
    effect = evidence.get("effect")
    if not isinstance(effect, Mapping):
        raise SynthesisError("integrity_error", "finding effect is malformed")
    oriented = effect.get("oriented_estimate")
    if oriented is None:
        expected_direction = "unknown"
    elif (
        isinstance(oriented, bool)
        or not isinstance(oriented, (int, float))
        or not math.isfinite(oriented)
    ):
        raise SynthesisError("integrity_error", "finding oriented effect is non-finite")
    elif oriented > 0:
        expected_direction = "positive"
    elif oriented < 0:
        expected_direction = "negative"
    else:
        expected_direction = "unknown"
    if row["direction"] != expected_direction:
        raise SynthesisError("integrity_error", "finding direction/effect drift")

    if result is None:
        raise SynthesisError("integrity_error", "finding belongs to a run without a result")
    result_findings = result.get("findings")
    if not isinstance(result_findings, list):
        raise SynthesisError("integrity_error", "analysis result findings are missing")
    matches = [
        item for item in result_findings
        if isinstance(item, Mapping) and item.get("finding_id") == finding_id
    ]
    if len(matches) != 1 or canonical_json(matches[0]) != row["evidence_json"]:
        raise SynthesisError("integrity_error", "finding is not identical to its run result")
    if not _finding_is_member(
        conn,
        run_id=str(run["run_id"]),
        finding_id=str(finding_id),
    ):
        raise SynthesisError(
            "stale_reference",
            "finding is absent from the referenced run's authenticated result",
        )
    return evidence


def _load_finding(
    conn: sqlite3.Connection,
    finding_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = _fetch_one(
        conn,
        """
        SELECT f.*, r.batch_id
          FROM analysis_findings AS f
          JOIN analysis_runs AS r ON r.run_id=f.run_id
         WHERE f.finding_id=?
        """,
        (finding_id,),
    )
    if row is None:
        raise SynthesisError("stale_reference", f"finding does not exist: {finding_id}")
    # Full ancestry verification occurs in ``_resolve_material``, where the
    # normalized batch/run/result objects are already available.
    return row, {}


def _finding_is_member(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    finding_id: str,
) -> bool:
    """Use the ledger-owned global-finding membership proof."""

    try:
        return finding_belongs_to_run(
            conn,
            run_id=run_id,
            finding_id_value=finding_id,
        )
    except LedgerError as exc:
        raise SynthesisError(
            exc.code, str(exc), validation=exc.validation,
        ) from exc


def _parse_evaluation(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for field, expected in (
        ("evidence_for_json", list),
        ("evidence_against_json", list),
        ("confounders_json", (dict, list)),
        ("sample_size_json", dict),
        ("effect_summary_json", dict),
        ("stability_json", dict),
    ):
        result[field.removesuffix("_json")] = _canonical_document(
            row[field], f"hypothesis_evaluations.{field}", expected=expected,
        )
    return result


def _load_evaluation(
    conn: sqlite3.Connection,
    hypothesis_id: str,
    evaluation_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    hypothesis = _fetch_one(
        conn, "SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,),
    )
    if hypothesis is None:
        raise SynthesisError(
            "stale_reference", f"hypothesis does not exist: {hypothesis_id}",
        )
    evaluation_row = _fetch_one(
        conn,
        """
        SELECT *
          FROM hypothesis_evaluations
         WHERE id=? AND hypothesis_id=?
        """,
        (evaluation_id, hypothesis_id),
    )
    if evaluation_row is None:
        raise SynthesisError(
            "stale_reference",
            f"evaluation {evaluation_id} does not belong to {hypothesis_id}",
        )
    evaluation = _parse_evaluation(evaluation_row)
    finding_id = evaluation_row["finding_id"]
    if finding_id is not None:
        finding_row, _finding = _load_finding(conn, finding_id)
        if finding_row["evidence_fingerprint"] != evaluation_row["evidence_fingerprint"]:
            raise SynthesisError(
                "integrity_error", "evaluation/finding evidence fingerprint drift",
            )
    return hypothesis, evaluation


def _verified_finding_ancestry(
    conn: sqlite3.Connection,
    finding_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    finding_row, _unused = _load_finding(conn, finding_id)
    run = _fetch_one(
        conn, "SELECT * FROM analysis_runs WHERE run_id=?", (finding_row["run_id"],),
    )
    if run is None:
        raise SynthesisError("integrity_error", "evidence finding run is missing")
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (run["batch_id"],),
    )
    range_row = _fetch_one(
        conn,
        """SELECT * FROM analysis_range_requests
             WHERE range_id=? AND batch_id=?""",
        (run["range_id"], run["batch_id"]),
    )
    if batch is None or range_row is None:
        raise SynthesisError("integrity_error", "evidence finding ancestry is incomplete")
    try:
        assert_terminal_batch_integrity(conn, batch_id=str(batch["batch_id"]))
    except LedgerError as exc:
        raise SynthesisError("integrity_error", str(exc)) from exc
    if batch["status"] == "running" or batch["completed_at"] is None:
        raise SynthesisError("integrity_error", "evidence finding batch is not terminal")
    _stored_sha256(batch["batch_id"], "analysis_batches.batch_id")
    _stored_timestamp(batch["completed_at"], "analysis_batches.completed_at")
    result = _verify_run_result(run, range_row, batch)
    evidence = _verify_finding(
        conn, finding_row, run=run, batch=batch, result=result,
    )
    return finding_row, evidence


def _load_hypothesis_evidence_history(
    conn: sqlite3.Connection,
    hypothesis_id: str,
    *,
    through_evaluation_id: int,
) -> list[dict[str, Any]]:
    ancestor_ids: set[int] = set()
    current_id: int | None = through_evaluation_id
    while current_id is not None:
        if current_id in ancestor_ids:
            raise SynthesisError(
                "integrity_error", "hypothesis evaluation history contains a cycle",
            )
        evaluation = _fetch_one(
            conn,
            """SELECT id,previous_evaluation_id
                 FROM hypothesis_evaluations
                WHERE id=? AND hypothesis_id=?""",
            (current_id, hypothesis_id),
        )
        if evaluation is None:
            raise SynthesisError(
                "integrity_error", "hypothesis evaluation history is incomplete",
            )
        ancestor_ids.add(current_id)
        current_id = evaluation["previous_evaluation_id"]
    placeholders = ",".join("?" for _ in ancestor_ids)
    rows = _fetch_all(
        conn,
        f"""SELECT *
             FROM hypothesis_evidence_items
            WHERE hypothesis_id=? AND evaluation_id IN ({placeholders})
            ORDER BY created_at,evidence_item_id""",
        (hypothesis_id, *sorted(ancestor_ids)),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        evidence_item_id = _stored_sha256(
            row["evidence_item_id"],
            "hypothesis_evidence_items.evidence_item_id",
        )
        _stored_sha256(row["hypothesis_id"], "hypothesis_evidence_items.hypothesis_id")
        finding_id = _stored_sha256(
            row["finding_id"], "hypothesis_evidence_items.finding_id",
        )
        _stored_sha256(
            row["evidence_fingerprint"],
            "hypothesis_evidence_items.evidence_fingerprint",
        )
        _stored_timestamp(
            row["created_at"], "hypothesis_evidence_items.created_at",
        )
        expected_id = sha256_id({
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "hypothesis_evidence_item",
            "hypothesis_id": hypothesis_id,
            "finding_id": finding_id,
            "evidence_kind": row["evidence_kind"],
        })
        if evidence_item_id != expected_id:
            raise SynthesisError(
                "integrity_error", "hypothesis evidence-item identity drift",
            )
        expected_polarity = (
            "for"
            if row["evidence_kind"] in {
                "same_direction_effect", "nonoverlap_replication",
            }
            else "against"
        )
        if row["polarity"] != expected_polarity:
            raise SynthesisError(
                "integrity_error", "hypothesis evidence-item polarity drift",
            )
        evaluation = _fetch_one(
            conn,
            """SELECT run_id,finding_id,source_analysis_version,range_from,range_to
                 FROM hypothesis_evaluations
                WHERE id=? AND hypothesis_id=?""",
            (row["evaluation_id"], hypothesis_id),
        )
        if evaluation is None or evaluation["finding_id"] != finding_id:
            raise SynthesisError(
                "integrity_error", "hypothesis evidence-item evaluation drift",
            )
        if not _finding_is_member(
            conn,
            run_id=str(evaluation["run_id"]),
            finding_id=str(finding_id),
        ):
            raise SynthesisError(
                "integrity_error",
                "hypothesis evidence item is absent from its evaluation run",
            )
        finding_row, evidence = _verified_finding_ancestry(conn, finding_id)
        if (
            row["evidence_fingerprint"] != finding_row["evidence_fingerprint"]
            or row["source_analysis_version"] != evaluation["source_analysis_version"]
            or row["range_from"] != evaluation["range_from"]
            or row["range_to"] != evaluation["range_to"]
        ):
            raise SynthesisError(
                "integrity_error", "hypothesis evidence-item source ancestry drift",
            )
        result.append({
            **row,
            "structured_evidence_for": evidence["evidence_for"],
            "structured_evidence_against": evidence["evidence_against"],
        })
    return result


def _load_hypothesis_evaluation_ancestors(
    conn: sqlite3.Connection,
    hypothesis_id: str,
    *,
    through_evaluation_id: int,
) -> list[dict[str, Any]]:
    """Load and authenticate the selected evaluation's complete parent chain."""

    ancestors: list[dict[str, Any]] = []
    seen: set[int] = set()
    current_id: int | None = through_evaluation_id
    while current_id is not None:
        if current_id in seen:
            raise SynthesisError(
                "integrity_error", "hypothesis evaluation history contains a cycle",
            )
        seen.add(current_id)
        row = _fetch_one(
            conn,
            """SELECT * FROM hypothesis_evaluations
                 WHERE id=? AND hypothesis_id=?""",
            (current_id, hypothesis_id),
        )
        if row is None:
            raise SynthesisError(
                "integrity_error", "hypothesis evaluation history is incomplete",
            )
        evaluation = _parse_evaluation(row)
        run = _fetch_one(
            conn,
            """SELECT run_id,batch_id,analysis_from,analysis_to,input_fingerprint,
                      completed_at
                 FROM analysis_runs WHERE run_id=?""",
            (evaluation["run_id"],),
        )
        if run is None:
            raise SynthesisError(
                "integrity_error", "hypothesis ancestor run is missing",
            )
        batch = _fetch_one(
            conn,
            "SELECT analysis_version FROM analysis_batches WHERE batch_id=?",
            (run["batch_id"],),
        )
        if batch is None:
            raise SynthesisError(
                "integrity_error", "hypothesis ancestor batch is missing",
            )
        if (
            evaluation["tested_at"] != run["completed_at"]
            or evaluation["input_fingerprint"] != run["input_fingerprint"]
            or evaluation["range_from"] != run["analysis_from"]
            or evaluation["range_to"] != run["analysis_to"]
            or evaluation["source_analysis_version"] != batch["analysis_version"]
        ):
            raise SynthesisError(
                "integrity_error", "hypothesis ancestor run ancestry drift",
            )
        ancestors.append({
            "evaluation_id": evaluation["id"],
            "range_from": evaluation["range_from"],
            "range_to": evaluation["range_to"],
            "run_analysis_from": run["analysis_from"],
            "run_analysis_to": run["analysis_to"],
        })
        current_id = evaluation["previous_evaluation_id"]
    return ancestors


def _resolve_material(
    conn: sqlite3.Connection,
    *,
    analysis_batch_id: str,
    run_refs: Sequence[Mapping[str, Any]],
    finding_refs: Sequence[Mapping[str, Any]],
    hypothesis_refs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (analysis_batch_id,),
    )
    if batch is None:
        raise SynthesisError(
            "stale_reference", f"analysis batch does not exist: {analysis_batch_id}",
        )
    _stored_sha256(batch["batch_id"], "analysis_batches.batch_id")
    _stored_sha256(batch["outcome_set_sha256"], "analysis_batches.outcome_set_sha256")
    _stored_sha256(batch["engine_sha256"], "analysis_batches.engine_sha256")
    _stored_sha256(batch["registry_sha256"], "analysis_batches.registry_sha256")
    _stored_timestamp(batch["started_at"], "analysis_batches.started_at")
    _stored_timestamp(
        batch["completed_at"], "analysis_batches.completed_at", nullable=True,
    )
    if batch["status"] == "running" or batch["completed_at"] is None:
        raise SynthesisError(
            "stale_reference", "synthesis cannot reference a non-terminal batch",
        )
    try:
        assert_terminal_batch_integrity(conn, batch_id=analysis_batch_id)
    except LedgerError as exc:
        raise SynthesisError("integrity_error", str(exc)) from exc
    count_rows = _fetch_all(
        conn,
        """SELECT status,COUNT(*) AS n
             FROM analysis_runs WHERE batch_id=? GROUP BY status""",
        (analysis_batch_id,),
    )
    counts = {row["status"]: int(row["n"]) for row in count_rows}
    expected_counts = {
        "run_count": sum(counts.values()),
        "completed_count": counts.get("completed", 0),
        "insufficient_count": counts.get("insufficient_data", 0),
        "no_data_count": counts.get("no_data", 0),
        "failed_count": counts.get("failed", 0),
    }
    if counts.get("running", 0) or expected_counts["run_count"] == 0:
        raise SynthesisError(
            "integrity_error", "terminal analysis batch has running runs or no runs",
        )
    if any(batch[field] != value for field, value in expected_counts.items()):
        raise SynthesisError("integrity_error", "analysis batch terminal counts drift")
    expected_batch_status = (
        "failed"
        if expected_counts["failed_count"] == expected_counts["run_count"]
        else "partial"
        if expected_counts["failed_count"]
        else "completed"
    )
    expected_reason = (
        "all_runs_failed"
        if expected_batch_status == "failed"
        else "some_runs_failed"
        if expected_batch_status == "partial"
        else None
    )
    if (
        batch["status"] != expected_batch_status
        or batch["status_reason_code"] != expected_reason
    ):
        raise SynthesisError("integrity_error", "analysis batch terminal status drift")

    runs: list[dict[str, Any]] = []
    run_ids = {str(reference["run_id"]) for reference in run_refs}
    run_by_id: dict[str, dict[str, Any]] = {}
    for reference in run_refs:
        row = _fetch_one(
            conn, "SELECT * FROM analysis_runs WHERE run_id=?", (reference["run_id"],),
        )
        if row is None:
            raise SynthesisError(
                "stale_reference", f"analysis run does not exist: {reference['run_id']}",
            )
        if row["batch_id"] != analysis_batch_id:
            raise SynthesisError("stale_reference", "analysis run belongs to another batch")
        range_row = _fetch_one(
            conn,
            """SELECT * FROM analysis_range_requests
                 WHERE range_id=? AND batch_id=?""",
            (row["range_id"], analysis_batch_id),
        )
        if range_row is None:
            raise SynthesisError("integrity_error", "analysis run range ancestry is missing")
        _stored_sha256(range_row["range_id"], "analysis_range_requests.range_id")
        result = _verify_run_result(row, range_row, batch)
        resolved_run = {
            "reference": dict(reference),
            "row": row,
            "range": range_row,
            "result": result,
        }
        runs.append(resolved_run)
        run_by_id[row["run_id"]] = resolved_run

    findings: list[dict[str, Any]] = []
    for reference in finding_refs:
        row, _unused = _load_finding(conn, str(reference["finding_id"]))
        membership_run_ids = sorted(
            run_id
            for run_id in run_ids
            if sum(
                1
                for finding in (run_by_id[run_id]["result"] or {}).get(
                    "findings", []
                )
                if isinstance(finding, Mapping)
                and finding.get("finding_id") == reference["finding_id"]
            )
            == 1
        )
        if not membership_run_ids:
            raise SynthesisError(
                "stale_reference",
                "finding is absent from every referenced run",
            )
        run_item = run_by_id[membership_run_ids[0]]
        evidence = _verify_finding(
            conn,
            row,
            run=run_item["row"],
            batch=batch,
            result=run_item["result"],
        )
        for membership_run_id in membership_run_ids:
            if not _finding_is_member(
                conn,
                run_id=membership_run_id,
                finding_id=str(reference["finding_id"]),
            ):
                raise SynthesisError(
                    "integrity_error",
                    "finding membership changed during synthesis validation",
                )
        findings.append({
            "reference": dict(reference),
            "row": row,
            "evidence": evidence,
            "membership_run_ids": membership_run_ids,
        })

    hypotheses: list[dict[str, Any]] = []
    for reference in hypothesis_refs:
        hypothesis, evaluation = _load_evaluation(
            conn,
            str(reference["hypothesis_id"]),
            int(reference["evaluation_id"]),
        )
        if evaluation["run_id"] not in run_ids:
            raise SynthesisError(
                "stale_reference", "hypothesis evaluation run is missing from run_refs",
            )
        evaluation_run = _fetch_one(
            conn,
            "SELECT batch_id FROM analysis_runs WHERE run_id=?",
            (evaluation["run_id"],),
        )
        if evaluation_run is None or evaluation_run["batch_id"] != analysis_batch_id:
            raise SynthesisError(
                "stale_reference", "hypothesis evaluation belongs to another batch",
            )
        _stored_sha256(hypothesis["hypothesis_id"], "hypotheses.hypothesis_id")
        _stored_sha256(hypothesis["created_by_run"], "hypotheses.created_by_run")
        _stored_sha256(
            hypothesis["created_by_finding"], "hypotheses.created_by_finding",
        )
        _stored_timestamp(hypothesis["created_at"], "hypotheses.created_at")
        _stored_timestamp(evaluation["tested_at"], "hypothesis_evaluations.tested_at")
        _stored_timestamp(evaluation["created_at"], "hypothesis_evaluations.created_at")
        _stored_sha256(
            evaluation["input_fingerprint"],
            "hypothesis_evaluations.input_fingerprint",
        )
        _stored_sha256(
            evaluation["evidence_fingerprint"],
            "hypothesis_evaluations.evidence_fingerprint",
        )
        run_item = run_by_id[evaluation["run_id"]]
        if evaluation["tested_at"] != run_item["row"]["completed_at"]:
            raise SynthesisError(
                "integrity_error", "evaluation tested-at/run ancestry drift",
            )
        if evaluation["input_fingerprint"] != run_item["row"]["input_fingerprint"]:
            raise SynthesisError(
                "integrity_error", "evaluation input fingerprint/run ancestry drift",
            )
        if evaluation["source_analysis_version"] != batch["analysis_version"]:
            raise SynthesisError(
                "integrity_error", "evaluation analysis-version ancestry drift",
            )
        if (
            evaluation["range_from"] != run_item["row"]["analysis_from"]
            or evaluation["range_to"] != run_item["row"]["analysis_to"]
        ):
            raise SynthesisError(
                "integrity_error", "evaluation range/run ancestry drift",
            )
        hypothesis_components = _fetch_all(
            conn,
            """SELECT position,exposure_key,lag_days,window_days,transform
                 FROM hypothesis_components
                WHERE hypothesis_id=? ORDER BY position""",
            (hypothesis["hypothesis_id"],),
        )
        if not hypothesis_components:
            raise SynthesisError("integrity_error", "hypothesis components are missing")
        if evaluation["finding_id"] is not None:
            finding_row, _unused = _load_finding(conn, evaluation["finding_id"])
            finding_evidence = _verify_finding(
                conn,
                finding_row,
                run=run_item["row"],
                batch=batch,
                result=run_item["result"],
            )
            if finding_row["evidence_fingerprint"] != evaluation["evidence_fingerprint"]:
                raise SynthesisError(
                    "integrity_error", "evaluation/finding evidence fingerprint drift",
                )
            if evaluation["evidence_for"] != finding_evidence["evidence_for"]:
                raise SynthesisError(
                    "integrity_error", "evaluation evidence-for/finding drift",
                )
            if evaluation["evidence_against"] != finding_evidence["evidence_against"]:
                raise SynthesisError(
                    "integrity_error", "evaluation evidence-against/finding drift",
                )
            for evaluation_field, finding_field in (
                ("confounders", "confounders"),
                ("sample_size", "sample"),
                ("stability", "stability"),
            ):
                if evaluation[evaluation_field] != finding_evidence[finding_field]:
                    raise SynthesisError(
                        "integrity_error",
                        f"evaluation {evaluation_field}/finding drift",
                    )
            expected_effect_summary = {
                "effect": finding_evidence["effect"],
                "rates": finding_evidence["rates"],
                "testing": finding_evidence["testing"],
                "quality": finding_evidence["quality"],
                "direction": finding_row["direction"],
                "provenance": {
                    key: finding_evidence["provenance"].get(key)
                    for key in (
                        "analysis_version",
                        "registry_version",
                        "engine_sha256",
                        "registry_sha256",
                    )
                },
            }
            if evaluation["effect_summary"] != expected_effect_summary:
                raise SynthesisError(
                    "integrity_error", "evaluation effect_summary/finding drift",
                )
        else:
            try:
                hypothesis_candidate_key = canonical_candidate_key(
                    hypothesis["outcome_key"],
                    hypothesis["outcome_mode"],
                    [
                        {
                            "exposure_key": component["exposure_key"],
                            "lag_days": component["lag_days"],
                            "window_days": component["window_days"],
                            "transform": component["transform"],
                        }
                        for component in hypothesis_components
                    ],
                )
            except ProvenanceError as exc:
                raise SynthesisError(
                    "integrity_error",
                    "dormant hypothesis candidate identity is invalid",
                ) from exc
            result_findings = (
                run_item["result"].get("findings", [])
                if isinstance(run_item["result"], Mapping)
                else []
            )
            audit_matches = [
                item
                for item in result_findings
                if isinstance(item, Mapping)
                and item.get("candidate_key") == hypothesis_candidate_key
            ]
            if len(audit_matches) > 1:
                raise SynthesisError(
                    "integrity_error",
                    "dormant run repeats the hypothesis candidate",
                )
            audit_finding_row = None
            audit_finding_evidence = None
            if audit_matches:
                audit_finding_id = audit_matches[0].get("finding_id")
                if not isinstance(audit_finding_id, str):
                    raise SynthesisError(
                        "integrity_error",
                        "dormant audit finding identity is malformed",
                    )
                audit_finding_row, _unused = _load_finding(
                    conn, audit_finding_id,
                )
                audit_finding_evidence = _verify_finding(
                    conn,
                    audit_finding_row,
                    run=run_item["row"],
                    batch=batch,
                    result=run_item["result"],
                )
            audit_fingerprint = (
                audit_finding_row["evidence_fingerprint"]
                if audit_finding_row is not None
                else None
            )
            dormant_provenance = {
                "analysis_version": evaluation["source_analysis_version"],
                "registry_version": batch["registry_version"],
                "engine_sha256": batch["engine_sha256"],
                "registry_sha256": batch["registry_sha256"],
                "input_fingerprint": evaluation["input_fingerprint"],
            }
            expected_dormant_fingerprint = sha256_id({
                "contract_version": LEDGER_CONTRACT_VERSION,
                "kind": "dormant_evidence",
                "hypothesis_id": hypothesis["hypothesis_id"],
                "run_id": evaluation["run_id"],
                "input_fingerprint": evaluation["input_fingerprint"],
                "reason": evaluation["evidence_class"],
                "analysis_version": evaluation["source_analysis_version"],
                "registry_version": batch["registry_version"],
                "engine_sha256": batch["engine_sha256"],
                "registry_sha256": batch["registry_sha256"],
                "range_from": evaluation["range_from"],
                "range_to": evaluation["range_to"],
                "audit_finding_evidence_fingerprint": audit_fingerprint,
            })
            if audit_finding_evidence is None:
                expected_dormant = {
                    "evidence_for": [],
                    "evidence_against": [],
                    "confounders": {
                        "checked": [],
                        "unchecked": [],
                        "sensitive_to": [],
                        "weighted_effect": None,
                    },
                    "sample_size": {
                        "eligible_n": 0,
                        "complete_n": 0,
                        "missing_n": 0,
                        "reason": evaluation["evidence_class"],
                    },
                    "effect_summary": {
                        "effect": None,
                        "rates": None,
                        "testing": None,
                        "quality": None,
                        "direction": "unknown",
                        "provenance": dormant_provenance,
                    },
                    "stability": {"status": "not_evaluated"},
                }
            else:
                expected_dormant = {
                    "evidence_for": [],
                    "evidence_against": [],
                    "confounders": audit_finding_evidence["confounders"],
                    "sample_size": audit_finding_evidence["sample"],
                    "effect_summary": {
                        "effect": audit_finding_evidence["effect"],
                        "rates": audit_finding_evidence["rates"],
                        "testing": audit_finding_evidence["testing"],
                        "quality": audit_finding_evidence["quality"],
                        "direction": audit_finding_row["direction"],
                        "provenance": dormant_provenance,
                    },
                    "stability": audit_finding_evidence["stability"],
                }
            if evaluation["evidence_fingerprint"] != expected_dormant_fingerprint:
                raise SynthesisError(
                    "integrity_error", "dormant evaluation fingerprint drift",
                )
            if any(evaluation[key] != value for key, value in expected_dormant.items()):
                raise SynthesisError(
                    "integrity_error", "dormant evaluation structured evidence drift",
                )
        created = _fetch_one(
            conn,
            "SELECT run_id FROM analysis_runs WHERE run_id=?",
            (hypothesis["created_by_run"],),
        )
        if created is None or not _finding_is_member(
            conn,
            run_id=str(hypothesis["created_by_run"]),
            finding_id=str(hypothesis["created_by_finding"]),
        ):
            raise SynthesisError(
                "integrity_error", "hypothesis creation ancestry drift",
            )
        created_components = _fetch_all(
            conn,
            """SELECT position,exposure_key,lag_days,window_days,transform
                 FROM analysis_finding_components
                WHERE finding_id=? ORDER BY position""",
            (hypothesis["created_by_finding"],),
        )
        if hypothesis_components != created_components:
            raise SynthesisError(
                "integrity_error", "hypothesis/finding component ancestry drift",
            )
        hypotheses.append({
            "reference": dict(reference),
            "hypothesis": hypothesis,
            "evaluation": evaluation,
            "components": hypothesis_components,
            "ancestor_evaluations": _load_hypothesis_evaluation_ancestors(
                conn,
                hypothesis["hypothesis_id"],
                through_evaluation_id=evaluation["id"],
            ),
            "evidence_items": _load_hypothesis_evidence_history(
                conn,
                hypothesis["hypothesis_id"],
                through_evaluation_id=evaluation["id"],
            ),
        })

    return {
        "batch": batch,
        "runs": sorted(
            runs,
            key=lambda item: (
                item["reference"]["purpose"], item["reference"]["run_id"],
            ),
        ),
        "findings": sorted(
            findings,
            key=lambda item: (
                item["reference"]["role"], item["reference"]["finding_id"],
            ),
        ),
        "hypotheses": sorted(
            hypotheses,
            key=lambda item: (
                item["reference"]["role"],
                item["reference"]["hypothesis_id"],
                item["reference"]["evaluation_id"],
            ),
        ),
    }


def _validate_terminal_ancestry(
    material: Mapping[str, Any],
    *,
    cadence: str,
    cutoff_date: str,
) -> None:
    """Enforce the frozen batch/cadence/range ancestry without scheduling it."""

    batch = material["batch"]
    if batch["run_kind"] != cadence:
        raise SynthesisError(
            "stale_reference", "synthesis cadence does not match analysis batch kind",
        )
    if batch["anchor_date"] != cutoff_date:
        raise SynthesisError(
            "stale_reference", "synthesis cutoff does not match batch anchor",
        )
    cutoff = date.fromisoformat(cutoff_date)

    def reject_future(value: Any, field: str) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            raise SynthesisError(
                "integrity_error", f"{field} is not an ISO date",
            )
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise SynthesisError(
                "integrity_error", f"{field} is not an ISO date",
            ) from exc
        if parsed.isoformat() != value:
            raise SynthesisError(
                "integrity_error", f"{field} is not a canonical ISO date",
            )
        if parsed > cutoff:
            raise SynthesisError(
                "stale_reference", "synthesis evidence range exceeds its cutoff",
            )

    for item in material["runs"]:
        reference = item["reference"]
        requested = item["range"]
        row = item["row"]
        range_role = requested["range_role"]
        purpose = reference["purpose"]
        expected_kind: str
        expected_from: str | None
        expected_to: str | None
        if cadence == "manual":
            if purpose != "primary" or range_role != "primary":
                raise SynthesisError(
                    "stale_reference", "manual synthesis requires a primary range",
                )
            expected_kind = requested["requested_range_kind"]
            expected_from = requested["requested_from"]
            expected_to = requested["requested_to"]
        elif cadence == "weekly":
            if purpose != "primary" or range_role != "primary":
                raise SynthesisError(
                    "stale_reference", "weekly synthesis requires its primary range",
                )
            expected_kind = "bounded"
            expected_from = (cutoff - timedelta(days=364)).isoformat()
            expected_to = cutoff_date
        elif cadence == "monthly":
            if purpose != range_role:
                raise SynthesisError(
                    "stale_reference", "monthly run purpose/range role mismatch",
                )
            if range_role == "primary":
                expected_kind, expected_from, expected_to = "all", None, None
            elif range_role == "recent":
                expected_kind = "bounded"
                expected_from = (cutoff - timedelta(days=89)).isoformat()
                expected_to = cutoff_date
            elif range_role == "historical":
                expected_kind = "bounded"
                expected_from = (cutoff - timedelta(days=179)).isoformat()
                expected_to = (cutoff - timedelta(days=90)).isoformat()
            else:
                raise SynthesisError(
                    "stale_reference", "monthly synthesis has an unknown range role",
                )
        else:
            if purpose != "trigger" or range_role != "primary":
                raise SynthesisError(
                    "stale_reference", "trigger synthesis requires its trigger range",
                )
            expected_kind = "bounded"
            expected_from = (cutoff - timedelta(days=364)).isoformat()
            expected_to = cutoff_date
        if (
            requested["requested_range_kind"] != expected_kind
            or requested["requested_from"] != expected_from
            or requested["requested_to"] != expected_to
        ):
            raise SynthesisError(
                "stale_reference", "synthesis run does not match the frozen range plan",
            )
        for field, value in (
            ("requested_to", requested["requested_to"]),
            ("analysis_to", row["analysis_to"]),
            ("baseline_to", row["baseline_to"]),
        ):
            reject_future(value, f"analysis run {field}")
        if row["status"] == "running":
            raise SynthesisError("stale_reference", "synthesis run is not terminal")

    for item in material["hypotheses"]:
        reject_future(
            item["hypothesis"]["first_seen"], "hypothesis first_seen",
        )
        for ancestor in item["ancestor_evaluations"]:
            reject_future(
                ancestor["range_to"], "hypothesis ancestor range_to",
            )
            reject_future(
                ancestor["run_analysis_to"],
                "hypothesis ancestor run analysis_to",
            )
        for evidence_item in item["evidence_items"]:
            reject_future(
                evidence_item["range_to"], "hypothesis evidence range_to",
            )


def _evaluation_hash_material(item: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = item["evaluation"]
    evaluation_material = {
        key: value
        for key, value in evaluation.items()
        if not key.endswith("_json")
    }
    for key in (
        "evidence_for",
        "evidence_against",
        "confounders",
        "sample_size",
        "effect_summary",
        "stability",
    ):
        evaluation_material[key] = evaluation[key]
    return {
        "hypothesis_id": item["reference"]["hypothesis_id"],
        "evaluation_id": item["reference"]["evaluation_id"],
        "role": item["reference"]["role"],
        "hypothesis": dict(item["hypothesis"]),
        "components": item["components"],
        "evaluation": evaluation_material,
        "evidence_items": item["evidence_items"],
    }


def synthesis_evidence_fingerprint(
    conn: sqlite3.Connection,
    *,
    analysis_batch_id: str,
    run_refs: object,
    finding_refs: object,
    hypothesis_refs: object,
) -> str:
    """Hash normalized refs plus immutable engine/ledger evidence."""

    _require_phase5(conn)
    normalized_runs = _normalize_run_refs(run_refs)
    normalized_findings = _normalize_finding_refs(finding_refs)
    normalized_hypotheses = _normalize_hypothesis_refs(hypothesis_refs)
    material = _resolve_material(
        conn,
        analysis_batch_id=_sha256(analysis_batch_id, "analysis_batch_id"),
        run_refs=normalized_runs,
        finding_refs=normalized_findings,
        hypothesis_refs=normalized_hypotheses,
    )
    return _material_fingerprint(analysis_batch_id, material)


def _material_fingerprint(
    analysis_batch_id: str,
    material: Mapping[str, Any],
) -> str:
    return sha256_id({
        "contract_version": SYNTHESIS_CONTRACT_VERSION,
        "analysis_batch_id": analysis_batch_id,
        "batch": {
            key: material["batch"][key]
            for key in (
                "run_kind", "anchor_date", "range_plan_version",
                "outcome_selection", "outcome_set_sha256", "analysis_version",
                "registry_version", "engine_sha256", "registry_sha256", "status",
                "status_reason_code", "run_count", "completed_count",
                "insufficient_count", "no_data_count", "failed_count",
            )
        },
        "runs": [
            {
                **item["reference"],
                "range_id": item["row"]["range_id"],
                "range_role": item["range"]["range_role"],
                "requested_range_kind": item["range"]["requested_range_kind"],
                "requested_from": item["range"]["requested_from"],
                "requested_to": item["range"]["requested_to"],
                "outcome_key": item["row"]["outcome_key"],
                "outcome_mode": item["row"]["outcome_mode"],
                "range_resolution": item["row"]["range_resolution"],
                "analysis_from": item["row"]["analysis_from"],
                "analysis_to": item["row"]["analysis_to"],
                "baseline_from": item["row"]["baseline_from"],
                "baseline_to": item["row"]["baseline_to"],
                "status": item["row"]["status"],
                "status_reason_code": item["row"]["status_reason_code"],
                "input_fingerprint": item["row"]["input_fingerprint"],
                "result_sha256": item["row"]["result_sha256"],
            }
            for item in material["runs"]
        ],
        "findings": [
            {
                **item["reference"],
                "run_id": item["row"]["run_id"],
                "candidate_key": item["row"]["candidate_key"],
                "candidate_kind": item["row"]["candidate_kind"],
                "outcome_key": item["row"]["outcome_key"],
                "outcome_mode": item["row"]["outcome_mode"],
                "direction": item["row"]["direction"],
                "quality_tier": item["row"]["quality_tier"],
                "eligible_for_hypothesis": item["row"]["eligible_for_hypothesis"],
                "evidence_fingerprint": item["row"]["evidence_fingerprint"],
                "membership_run_ids": item["membership_run_ids"],
            }
            for item in material["findings"]
        ],
        "hypotheses": [
            _evaluation_hash_material(item) for item in material["hypotheses"]
        ],
    })


def _format_number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SynthesisError("integrity_error", "non-finite structured evidence")
        if value == 0:
            return "0"
        return format(value, ".3g")
    return str(value)


def _md_cell(value: Any) -> str:
    text = _format_number(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _inline_json(value: Any) -> str:
    try:
        text = canonical_json(value)
    except ContractError as exc:
        raise SynthesisError("integrity_error", "structured evidence is not canonical") from exc
    return "`" + text.replace("`", "\\`").replace("|", "\\|") + "`"


def _component_label(evidence: Mapping[str, Any]) -> str:
    exposure = evidence.get("exposure")
    if not isinstance(exposure, Mapping):
        return "—"
    components = exposure.get("components")
    if not isinstance(components, list):
        return "—"
    labels = []
    for component in components:
        if not isinstance(component, Mapping):
            raise SynthesisError("integrity_error", "finding component is not an object")
        labels.append(
            f"{component.get('exposure_key', 'unknown')} "
            f"(lag {component.get('lag_days', '—')}d, "
            f"window {component.get('window_days', '—')}d, "
            f"{component.get('transform', 'unknown')})"
        )
    return " + ".join(labels) if labels else "—"


def _finding_table(material: Mapping[str, Any]) -> list[str]:
    lines = [
        "### Findings",
        "",
        "| Finding | Role | Outcome / mode | Exposure and timing | Complete n | "
        "Exposed n | Unexposed n | Positive n | Negative n | Effect method | "
        "Estimate | 95% CI | p | q | Stability |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---|---:|---:|---|",
    ]
    if not material["findings"]:
        lines.extend(["", "_No findings were referenced._"])
        return lines
    for item in material["findings"]:
        evidence = item["evidence"]
        sample = evidence.get("sample")
        effect = evidence.get("effect")
        testing = evidence.get("testing")
        stability = evidence.get("stability")
        outcome = evidence.get("outcome")
        for value, field in (
            (sample, "sample"), (effect, "effect"), (testing, "testing"),
            (stability, "stability"), (outcome, "outcome"),
        ):
            if not isinstance(value, Mapping):
                raise SynthesisError("integrity_error", f"finding {field} must be an object")
        ci = effect.get("ci95")
        if ci is None:
            ci_text = "—"
        elif isinstance(ci, list) and len(ci) == 2:
            ci_text = f"[{_format_number(ci[0])}, {_format_number(ci[1])}]"
        else:
            raise SynthesisError("integrity_error", "finding effect.ci95 must be a pair or null")
        lines.append("| " + " | ".join(_md_cell(value) for value in (
            item["reference"]["finding_id"],
            item["reference"]["role"],
            f"{outcome.get('key', '—')} / {outcome.get('mode', '—')}",
            _component_label(evidence),
            sample.get("complete_n"),
            sample.get("exposed_n"),
            sample.get("unexposed_n"),
            sample.get("outcome_positive_n"),
            sample.get("outcome_negative_n"),
            effect.get("method"),
            effect.get("estimate"),
            ci_text,
            testing.get("p"),
            testing.get("q"),
            stability.get("status"),
        )) + " |")
    return lines


def _rates_and_gates_table(material: Mapping[str, Any]) -> list[str]:
    lines = [
        "### Rates and engine-owned evidence gates",
        "",
        "| Finding | Baseline | Exposed | Unexposed | Risk difference | Risk ratio | "
        "Evidence for | Evidence against | Warnings |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    if not material["findings"]:
        lines.extend(["", "_No finding rates or gates were available._"])
        return lines
    for item in material["findings"]:
        evidence = item["evidence"]
        rates = evidence.get("rates")
        if rates is None:
            rates = {}
        if not isinstance(rates, Mapping):
            raise SynthesisError("integrity_error", "finding rates must be an object or null")
        evidence_for = evidence.get("evidence_for")
        evidence_against = evidence.get("evidence_against")
        warnings = evidence.get("warnings")
        if not isinstance(evidence_for, list) or not isinstance(evidence_against, list):
            raise SynthesisError("integrity_error", "finding evidence gates must be arrays")
        if not isinstance(warnings, list):
            raise SynthesisError("integrity_error", "finding warnings must be an array")
        if any(not isinstance(gate, Mapping) for gate in evidence_for + evidence_against):
            raise SynthesisError("integrity_error", "finding evidence gate is not an object")
        if any(not isinstance(warning, str) for warning in warnings):
            raise SynthesisError("integrity_error", "finding warning is not text")
        lines.append("| " + " | ".join(_md_cell(value) for value in (
            item["reference"]["finding_id"],
            rates.get("baseline"),
            rates.get("exposed"),
            rates.get("unexposed"),
            rates.get("risk_difference"),
            rates.get("risk_ratio"),
            "; ".join(
                f"{gate.get('code', 'unknown')}={_format_number(gate.get('value'))} "
                f"(gate {_format_number(gate.get('threshold'))})"
                for gate in evidence_for
            ) or "—",
            "; ".join(
                f"{gate.get('code', 'unknown')}={_format_number(gate.get('value'))} "
                f"(gate {_format_number(gate.get('threshold'))})"
                for gate in evidence_against
            ) or "—",
            ", ".join(str(warning) for warning in warnings) or "—",
        )) + " |")
    return lines


def _interaction_rates_table(material: Mapping[str, Any]) -> list[str]:
    pairs = [
        item
        for item in material["findings"]
        if item["row"]["candidate_kind"] == "pair"
    ]
    if not pairs:
        return []
    lines = [
        "### Interaction component rates",
        "",
        "| Finding | Neither n | A-only n | B-only n | Both n | Neither rate | "
        "A-only rate | B-only rate | Both rate | A difference | B difference | "
        "Incremental difference | Oriented A difference | Oriented B difference | "
        "Oriented incremental difference |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in pairs:
        evidence = item["evidence"]
        sample = evidence.get("sample")
        rates = evidence.get("rates")
        cells = sample.get("interaction_cells") if isinstance(sample, Mapping) else None
        if not isinstance(cells, Mapping) or not isinstance(rates, Mapping):
            raise SynthesisError(
                "integrity_error",
                "pair finding requires structured interaction cells and rates",
            )
        lines.append("| " + " | ".join(_md_cell(value) for value in (
            item["reference"]["finding_id"],
            cells.get("neither"),
            cells.get("a_only"),
            cells.get("b_only"),
            cells.get("both"),
            rates.get("neither"),
            rates.get("a_only"),
            rates.get("b_only"),
            rates.get("both"),
            rates.get("component_a_difference"),
            rates.get("component_b_difference"),
            rates.get("incremental_risk_difference"),
            rates.get("oriented_component_a_difference"),
            rates.get("oriented_component_b_difference"),
            rates.get("oriented_incremental_risk_difference"),
        )) + " |")
    return lines


def _hypothesis_table(material: Mapping[str, Any]) -> list[str]:
    lines = [
        "### Hypothesis ledger state",
        "",
        "| Hypothesis | Role | Evaluation | Evidence class | Status | Confidence | "
        "Window | New eligible observations | Transition applied | Evidence for | "
        "Evidence against | Confounders | Sample | Effect | Stability |",
        "|---|---|---:|---|---|---|---|---:|---|---|---|---|---|---|---|",
    ]
    if not material["hypotheses"]:
        lines.extend(["", "_No hypotheses were referenced._"])
        return lines
    for item in material["hypotheses"]:
        reference = item["reference"]
        evaluation = item["evaluation"]
        range_from = evaluation["range_from"] or "—"
        range_to = evaluation["range_to"] or "—"
        lines.append("| " + " | ".join(_md_cell(value) for value in (
            reference["hypothesis_id"],
            reference["role"],
            reference["evaluation_id"],
            evaluation["evidence_class"],
            evaluation["status"],
            evaluation["confidence"],
            f"{range_from} to {range_to}",
            evaluation["new_eligible_observations"],
            bool(evaluation["transition_applied"]),
            _inline_json(evaluation["evidence_for"]),
            _inline_json(evaluation["evidence_against"]),
            _inline_json(evaluation["confounders"]),
            _inline_json(evaluation["sample_size"]),
            _inline_json(evaluation["effect_summary"]),
            _inline_json(evaluation["stability"]),
        )) + " |")
    return lines


def _hypothesis_evidence_history_table(
    material: Mapping[str, Any],
) -> list[str]:
    lines = [
        "### Immutable hypothesis evidence history",
        "",
        "| Hypothesis | Evidence item | Polarity | Kind | Finding | Window | "
        "Structured evidence for | Structured evidence against |",
        "|---|---|---|---|---|---|---|---|",
    ]
    items = [
        (hypothesis["reference"]["hypothesis_id"], item)
        for hypothesis in material["hypotheses"]
        for item in hypothesis["evidence_items"]
    ]
    if not items:
        lines.extend(["", "_No immutable hypothesis evidence items were referenced._"])
        return lines
    for hypothesis_id, item in items:
        lines.append("| " + " | ".join(_md_cell(value) for value in (
            hypothesis_id,
            item["evidence_item_id"],
            item["polarity"],
            item["evidence_kind"],
            item["finding_id"],
            f"{item['range_from']} to {item['range_to']}",
            _inline_json(item["structured_evidence_for"]),
            _inline_json(item["structured_evidence_against"]),
        )) + " |")
    return lines


def _render_markdown(
    record: Mapping[str, Any],
    material: Mapping[str, Any],
) -> str:
    lines = [
        "---",
        "type: autonomous-insight-synthesis",
        f"date: {record['cutoff_date']}",
        f"synthesis_id: {record['synthesis_id']}",
        f"status: {record['status']}",
        f"evidence_fingerprint: {record['evidence_fingerprint']}",
        "---",
        "",
        f"# Autonomous insight synthesis — {record['cutoff_date']}",
        "",
        "## Deterministic evidence",
        "",
        "> The tables in this section are rendered only from stored structured "
        "engine and ledger evidence. Model prose is not a numerical authority.",
        "",
    ]
    lines.extend(_finding_table(material))
    lines.extend([""])
    lines.extend(_rates_and_gates_table(material))
    interaction_table = _interaction_rates_table(material)
    if interaction_table:
        lines.extend([""])
        lines.extend(interaction_table)
    lines.extend([""])
    lines.extend(_hypothesis_table(material))
    lines.extend([""])
    lines.extend(_hypothesis_evidence_history_table(material))
    lines.extend([
        "",
        "## Hermes interpretation (model-generated; not numerical authority)",
        "",
    ])
    narrative = record.get("narrative_md")
    if narrative is None:
        lines.append("_No model narrative was recorded for this audited outcome._")
    else:
        lines.append(str(narrative).rstrip())
    return "\n".join(lines).rstrip() + "\n"


def _validate_annotation_state(
    conn: sqlite3.Connection,
    annotation: Mapping[str, Any],
) -> None:
    evaluation_id = annotation["evaluation_id"]
    if evaluation_id is not None:
        row = _fetch_one(
            conn,
            """
            SELECT 1
              FROM hypothesis_evaluations
             WHERE id=? AND hypothesis_id=?
            """,
            (evaluation_id, annotation["hypothesis_id"]),
        )
        if row is None:
            raise SynthesisError(
                "stale_reference", "annotation evaluation does not belong to hypothesis",
            )
    supersedes_id = annotation["supersedes_id"]
    if supersedes_id is None:
        return
    prior = _fetch_one(
        conn,
        """
        SELECT *
          FROM hypothesis_annotations
         WHERE annotation_id=?
        """,
        (supersedes_id,),
    )
    if prior is None:
        raise SynthesisError("stale_reference", "superseded annotation does not exist")
    if (
        prior["hypothesis_id"] != annotation["hypothesis_id"]
        or prior["annotation_kind"] != annotation["annotation_kind"]
    ):
        raise SynthesisError(
            "stale_reference",
            "superseded annotation must have the same hypothesis and kind",
        )
    child = _fetch_one(
        conn,
        "SELECT annotation_id FROM hypothesis_annotations WHERE supersedes_id=?",
        (supersedes_id,),
    )
    if child is not None:
        raise SynthesisError("stale_revision", "superseded annotation is not a leaf")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_result(
    record: Mapping[str, Any],
    rendered: str | None,
    *,
    created: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "contract": SYNTHESIS_CONTRACT_VERSION,
        "synthesis_id": record["synthesis_id"],
        "status": record["status"],
        "evidence_fingerprint": record["evidence_fingerprint"],
        "finding_ids": sorted({
            item["finding_id"] for item in record["finding_refs"]
        }),
        "hypothesis_ids": sorted({
            item["hypothesis_id"] for item in record["hypothesis_refs"]
        }),
        "rendered_sha256": (
            "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            if rendered is not None else None
        ),
        "message_eligible": record["status"] == "completed",
        "no_message_reason_code": record["no_message_reason_code"],
        "created": created,
    }


def _record_boundary_view(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return only caller-owned fields for exact retry comparison."""

    return {
        key: (
            [
                {annotation_key: annotation[annotation_key] for annotation_key in _ANNOTATION_KEYS}
                for annotation in value["annotations"]
            ]
            if key == "annotations"
            else value[key]
        )
        for key in _TOP_LEVEL_KEYS
    }


def record_synthesis(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Atomically append one validated synthesis and its normalized refs."""

    _require_phase5(conn)
    record = validate_synthesis_record(payload)
    material = _resolve_material(
        conn,
        analysis_batch_id=record["analysis_batch_id"],
        run_refs=record["run_refs"],
        finding_refs=record["finding_refs"],
        hypothesis_refs=record["hypothesis_refs"],
    )
    expected_fingerprint = _material_fingerprint(
        record["analysis_batch_id"], material,
    )
    if record["evidence_fingerprint"] != expected_fingerprint:
        raise SynthesisError(
            "stale_evidence",
            "evidence_fingerprint does not match the normalized stored evidence",
        )
    _validate_terminal_ancestry(
        material,
        cadence=record["cadence"],
        cutoff_date=record["cutoff_date"],
    )

    timestamp = _canonical_timestamp(now or _utc_now(), "now")
    rendered = (
        _render_markdown(record, material)
        if record["status"] == "completed"
        else None
    )
    existing_row = _fetch_one(
        conn,
        "SELECT synthesis_id FROM synthesis_runs WHERE synthesis_id=?",
        (record["synthesis_id"],),
    )
    if existing_row is not None:
        existing = read_synthesis(conn, record["synthesis_id"])
        if record["notification"] is not None:
            outbox = _fetch_one(
                conn,
                "SELECT * FROM insight_notification_outbox WHERE synthesis_id=?",
                (record["synthesis_id"],),
            )
            if outbox is None:
                raise SynthesisError(
                    "integrity_error",
                    "synthesis retry expected its durable notification outbox row",
                )
            expected_notification = record["notification"]
            if (
                outbox["channel_class"] != expected_notification["channel_class"]
                or outbox["destination_class"]
                != expected_notification["destination_class"]
                or outbox["payload_json"] != expected_notification["payload_json"]
                or outbox["payload_sha256"]
                != expected_notification["payload_sha256"]
                or outbox["dedupe_key"] != expected_notification["dedupe_key"]
                or outbox["idempotency_mode"]
                != expected_notification["idempotency_mode"]
                or outbox["provider_idempotency_key"]
                != expected_notification["provider_idempotency_key"]
            ):
                raise SynthesisError(
                    "append_conflict",
                    "synthesis notification retry differs from durable outbox content",
                )
        existing_view = _record_boundary_view({
            **existing,
            "rendered_md": None,
            "notification": None,
        })
        expected_view = _record_boundary_view({**record, "notification": None})
        if canonical_json(existing_view) != canonical_json(expected_view):
            raise SynthesisError(
                "append_conflict",
                "synthesis_id already exists with different immutable content",
            )
        return _record_result(record, existing["rendered_md"], created=False)

    for annotation in record["annotations"]:
        _validate_annotation_state(conn, annotation)

    completed_at = None if record["status"] == "running" else timestamp
    savepoint = "synthesis_" + secrets.token_hex(8)
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        conn.execute(
            """
            INSERT INTO synthesis_runs(
              synthesis_id,analysis_batch_id,cadence,reason_code,cutoff_date,
              evidence_fingerprint,context_version,prompt_sha256,model_id,provider,
              finding_ids_json,hypothesis_ids_json,narrative_md,rendered_md,status,
              no_message_reason_code,created_at,completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record["synthesis_id"], record["analysis_batch_id"],
                record["cadence"], record["reason_code"], record["cutoff_date"],
                record["evidence_fingerprint"], record["context_version"],
                record["prompt_sha256"], record["model_id"], record["provider"],
                canonical_json(sorted({
                    item["finding_id"] for item in record["finding_refs"]
                })),
                canonical_json(sorted({
                    item["hypothesis_id"] for item in record["hypothesis_refs"]
                })),
                record["narrative_md"], rendered, record["status"],
                record["no_message_reason_code"], timestamp, completed_at,
            ),
        )
        conn.executemany(
            """
            INSERT INTO synthesis_analysis_runs(synthesis_id,run_id,purpose)
            VALUES(?,?,?)
            """,
            [
                (record["synthesis_id"], item["run_id"], item["purpose"])
                for item in record["run_refs"]
            ],
        )
        conn.executemany(
            """
            INSERT INTO synthesis_finding_refs(synthesis_id,finding_id,role)
            VALUES(?,?,?)
            """,
            [
                (record["synthesis_id"], item["finding_id"], item["role"])
                for item in record["finding_refs"]
            ],
        )
        conn.executemany(
            """
            INSERT INTO synthesis_hypothesis_refs(
              synthesis_id,hypothesis_id,evaluation_id,role
            ) VALUES(?,?,?,?)
            """,
            [
                (
                    record["synthesis_id"], item["hypothesis_id"],
                    item["evaluation_id"], item["role"],
                )
                for item in record["hypothesis_refs"]
            ],
        )
        for item in record["annotations"]:
            appended = append_annotation(
                conn,
                hypothesis_id=item["hypothesis_id"],
                evaluation_id=item["evaluation_id"],
                annotation_kind=item["annotation_kind"],
                content=item["content"],
                source=item["source"],
                synthesis_id=item["synthesis_id"],
                context_version=item["context_version"],
                prompt_sha256=item["prompt_sha256"],
                model_id=item["model_id"],
                provider=item["provider"],
                supersedes_id=item["supersedes_id"],
                annotation_id=item["annotation_id"],
                created_at=timestamp,
            )
            if appended["input_sha256"] != item["input_sha256"]:
                raise SynthesisError(
                    "integrity_error", "ledger annotation identity drift",
                )
        if record["notification"] is not None:
            evidence_fingerprints = [
                item["row"]["evidence_fingerprint"]
                for item in material["findings"]
            ] + [
                item["evaluation"]["evidence_fingerprint"]
                for item in material["hypotheses"]
                if item["reference"]["role"] == "changed"
            ]
            expected_dedupe = orchestrator.notification_dedupe_key(
                cadence=record["cadence"],
                evidence_fingerprints=evidence_fingerprints,
                destination_class=record["notification"]["destination_class"],
                context_version=record["context_version"],
            )
            if record["notification"]["dedupe_key"] != expected_dedupe:
                raise SynthesisError(
                    "identity_mismatch",
                    "notification dedupe_key does not match referenced evidence ancestry",
                    validation=True,
                )
            expected_payload = {
                "contract_version": orchestrator.NOTIFICATION_CONTRACT_VERSION,
                "synthesis_id": record["synthesis_id"],
                "rendered_md": rendered,
            }
            if record["notification"]["payload"] != expected_payload:
                raise SynthesisError(
                    "validation_error",
                    "notification payload must exactly wrap deterministic rendered Markdown",
                    validation=True,
                )
            trigger_id = None
            initiator = material["batch"]["initiator_key"]
            if record["cadence"] == "trigger":
                prefix = "trigger/"
                if not isinstance(initiator, str) or not initiator.startswith(prefix):
                    raise SynthesisError(
                        "integrity_error",
                        "trigger synthesis batch has no canonical trigger initiator",
                    )
                trigger_id = initiator[len(prefix):]
                if re.fullmatch(r"sha256:[0-9a-f]{64}", trigger_id) is None:
                    raise SynthesisError(
                        "integrity_error",
                        "trigger synthesis initiator is malformed",
                    )
            orchestrator.enqueue_notification(
                conn,
                {
                    key: record["notification"][key]
                    for key in (
                        "channel_class", "destination_class", "payload",
                        "dedupe_key", "not_before", "idempotency_mode",
                        "provider_idempotency_key",
                    )
                },
                analysis_batch_id=record["analysis_batch_id"],
                synthesis_id=record["synthesis_id"],
                trigger_id=trigger_id,
                now=timestamp,
            )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception as exc:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except sqlite3.Error:
            pass
        if isinstance(exc, SynthesisError):
            raise
        if isinstance(exc, LedgerError):
            raise SynthesisError(
                exc.code, str(exc), validation=exc.validation,
            ) from exc
        if isinstance(exc, orchestrator.OrchestrationError):
            raise SynthesisError(
                exc.code, str(exc), validation=exc.validation,
            ) from exc
        if isinstance(exc, sqlite3.IntegrityError):
            raise SynthesisError(
                "integrity_error", "synthesis append violated the ledger contract",
            ) from exc
        raise

    return _record_result(record, rendered, created=True)


def _refs_for_synthesis(
    conn: sqlite3.Connection,
    synthesis_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    run_refs = _fetch_all(
        conn,
        """
        SELECT run_id,purpose
          FROM synthesis_analysis_runs
         WHERE synthesis_id=?
         ORDER BY purpose,run_id
        """,
        (synthesis_id,),
    )
    finding_refs = _fetch_all(
        conn,
        """
        SELECT finding_id,role
          FROM synthesis_finding_refs
         WHERE synthesis_id=?
         ORDER BY role,finding_id
        """,
        (synthesis_id,),
    )
    hypothesis_refs = _fetch_all(
        conn,
        """
        SELECT hypothesis_id,evaluation_id,role
          FROM synthesis_hypothesis_refs
         WHERE synthesis_id=?
         ORDER BY role,hypothesis_id,evaluation_id
        """,
        (synthesis_id,),
    )
    return run_refs, finding_refs, hypothesis_refs


def _annotations_for_synthesis(
    conn: sqlite3.Connection,
    synthesis_id: str,
) -> list[dict[str, Any]]:
    return _fetch_all(
        conn,
        """
        SELECT annotation_id,hypothesis_id,evaluation_id,annotation_kind,content,
               source,synthesis_id,context_version,prompt_sha256,model_id,provider,
               supersedes_id,input_sha256,created_at
          FROM hypothesis_annotations
         WHERE synthesis_id=?
         ORDER BY created_at,annotation_id
        """,
        (synthesis_id,),
    )


def read_synthesis(
    conn: sqlite3.Connection,
    synthesis_id: str,
) -> dict[str, Any]:
    """Read and integrity-check one structured synthesis and rendered brief."""

    _require_phase5(conn)
    synthesis_id = _sha256(synthesis_id, "synthesis_id")
    assert synthesis_id is not None
    row = _fetch_one(
        conn, "SELECT * FROM synthesis_runs WHERE synthesis_id=?", (synthesis_id,),
    )
    if row is None:
        raise SynthesisError("not_found", "synthesis does not exist")
    run_refs, finding_refs, hypothesis_refs = _refs_for_synthesis(
        conn, synthesis_id,
    )
    raw_annotations = _annotations_for_synthesis(conn, synthesis_id)
    for annotation in raw_annotations:
        _stored_timestamp(
            annotation["created_at"], "hypothesis_annotations.created_at",
        )
    boundary_annotations = [
        {
            key: annotation[key]
            for key in _ANNOTATION_KEYS
        }
        for annotation in raw_annotations
    ]
    # Re-run the exact writer boundary on stored scalar/reference/prose fields.
    # The deterministic rendered Markdown is deliberately replaced with null
    # because it is never a caller-owned input.
    normalized_stored = validate_synthesis_record({
        "synthesis_id": row["synthesis_id"],
        "analysis_batch_id": row["analysis_batch_id"],
        "cadence": row["cadence"],
        "reason_code": row["reason_code"],
        "cutoff_date": row["cutoff_date"],
        "evidence_fingerprint": row["evidence_fingerprint"],
        "context_version": row["context_version"],
        "prompt_sha256": row["prompt_sha256"],
        "model_id": row["model_id"],
        "provider": row["provider"],
        "run_refs": run_refs,
        "finding_refs": finding_refs,
        "hypothesis_refs": hypothesis_refs,
        "narrative_md": row["narrative_md"],
        "rendered_md": None,
        "status": row["status"],
        "no_message_reason_code": row["no_message_reason_code"],
        "annotations": boundary_annotations,
        "notification": None,
    })
    for field in (
        "synthesis_id", "analysis_batch_id", "cadence", "reason_code",
        "cutoff_date", "evidence_fingerprint", "context_version",
        "prompt_sha256", "model_id", "provider", "narrative_md", "status",
        "no_message_reason_code",
    ):
        if normalized_stored[field] != row[field]:
            raise SynthesisError(
                "integrity_error", f"stored synthesis {field} is not canonical",
            )
    material = _resolve_material(
        conn,
        analysis_batch_id=row["analysis_batch_id"],
        run_refs=normalized_stored["run_refs"],
        finding_refs=normalized_stored["finding_refs"],
        hypothesis_refs=normalized_stored["hypothesis_refs"],
    )
    expected_fingerprint = _material_fingerprint(
        row["analysis_batch_id"], material,
    )
    if row["evidence_fingerprint"] != expected_fingerprint:
        raise SynthesisError("integrity_error", "synthesis evidence fingerprint drift")
    _validate_terminal_ancestry(
        material,
        cadence=row["cadence"],
        cutoff_date=row["cutoff_date"],
    )
    finding_ids = sorted({item["finding_id"] for item in finding_refs})
    hypothesis_ids = sorted({item["hypothesis_id"] for item in hypothesis_refs})
    if _canonical_document(
        row["finding_ids_json"], "synthesis_runs.finding_ids_json", expected=list,
    ) != finding_ids:
        raise SynthesisError("integrity_error", "normalized finding ID list drift")
    if _canonical_document(
        row["hypothesis_ids_json"], "synthesis_runs.hypothesis_ids_json", expected=list,
    ) != hypothesis_ids:
        raise SynthesisError("integrity_error", "normalized hypothesis ID list drift")

    stored = {
        "synthesis_id": row["synthesis_id"],
        "analysis_batch_id": row["analysis_batch_id"],
        "cadence": row["cadence"],
        "reason_code": row["reason_code"],
        "cutoff_date": row["cutoff_date"],
        "evidence_fingerprint": row["evidence_fingerprint"],
        "context_version": row["context_version"],
        "prompt_sha256": row["prompt_sha256"],
        "model_id": row["model_id"],
        "provider": row["provider"],
        "run_refs": run_refs,
        "finding_refs": finding_refs,
        "hypothesis_refs": hypothesis_refs,
        "narrative_md": row["narrative_md"],
        "rendered_md": row["rendered_md"],
        "status": row["status"],
        "no_message_reason_code": row["no_message_reason_code"],
        "annotations": raw_annotations,
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }
    _stored_timestamp(stored["created_at"], "synthesis_runs.created_at")
    _stored_timestamp(
        stored["completed_at"], "synthesis_runs.completed_at", nullable=True,
    )
    if stored["context_version"] != SYNTHESIS_CONTEXT_VERSION:
        raise SynthesisError("integrity_error", "synthesis context version is not approved")
    if stored["status"] == "completed":
        if (
            stored["prompt_sha256"] is None
            or stored["model_id"] is None
            or stored["provider"] is None
            or stored["narrative_md"] is None
            or stored["completed_at"] is None
        ):
            raise SynthesisError("integrity_error", "completed synthesis provenance drift")
        expected_rendered = _render_markdown(stored, material)
        if stored["rendered_md"] != expected_rendered:
            raise SynthesisError("integrity_error", "deterministic rendered Markdown drift")
    elif stored["status"] in NO_MESSAGE_STATUSES:
        if not stored["no_message_reason_code"]:
            raise SynthesisError("integrity_error", "no-message reason is missing")
        if stored["narrative_md"] is not None:
            raise SynthesisError("integrity_error", "no-message narrative must be null")
        if stored["rendered_md"] is not None:
            raise SynthesisError("integrity_error", "no-message rendered Markdown must be null")
    elif stored["rendered_md"] is not None:
        raise SynthesisError("integrity_error", "running rendered Markdown must be null")
    return {
        "ok": True,
        "contract": SYNTHESIS_CONTRACT_VERSION,
        **stored,
        "finding_ids": finding_ids,
        "hypothesis_ids": hypothesis_ids,
        "rendered_sha256": (
            "sha256:" + hashlib.sha256(
                stored["rendered_md"].encode("utf-8")
            ).hexdigest()
            if stored["rendered_md"] is not None else None
        ),
        "message_eligible": stored["status"] == "completed",
    }


def synthesis_history(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    before: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic cursor page of integrity-checked syntheses."""

    _require_phase5(conn)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        _validation("limit must be an integer between 1 and 100")
    parameters: list[Any] = []
    where = ""
    if before is not None:
        before = _sha256(before, "before")
        assert before is not None
        cursor_row = _fetch_one(
            conn,
            "SELECT created_at,synthesis_id FROM synthesis_runs WHERE synthesis_id=?",
            (before,),
        )
        if cursor_row is None:
            raise SynthesisError("not_found", "before synthesis does not exist")
        _stored_timestamp(
            cursor_row["created_at"], "synthesis_runs.created_at",
        )
        where = "WHERE (created_at < ? OR (created_at = ? AND synthesis_id < ?))"
        parameters.extend([
            cursor_row["created_at"], cursor_row["created_at"],
            cursor_row["synthesis_id"],
        ])
    parameters.append(limit + 1)
    rows = _fetch_all(
        conn,
        f"""
        SELECT synthesis_id
          FROM synthesis_runs
          {where}
         ORDER BY created_at DESC,synthesis_id DESC
         LIMIT ?
        """,
        parameters,
    )
    has_more = len(rows) > limit
    selected = rows[:limit]
    syntheses = [read_synthesis(conn, row["synthesis_id"]) for row in selected]
    return {
        "ok": True,
        "contract": SYNTHESIS_CONTRACT_VERSION,
        "syntheses": syntheses,
        "next_before": selected[-1]["synthesis_id"] if has_more and selected else None,
    }


def render_synthesis_markdown(
    conn: sqlite3.Connection,
    synthesis_id: str,
) -> str:
    """Return only the verified deterministic Markdown stored for a synthesis."""

    synthesis = read_synthesis(conn, synthesis_id)
    rendered = synthesis["rendered_md"]
    if rendered is None:
        raise SynthesisError(
            "no_message", "this synthesis has no owner-facing Markdown",
        )
    return rendered


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_child_directory(parent_fd: int, name: str) -> int:
    flags = _directory_flags()
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise SynthesisError(
                "unsafe_path", "vault directory component is not a safe directory",
            ) from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise SynthesisError(
                "unsafe_path", "vault path contains a symlink or non-directory",
            ) from exc
        raise


def _secure_patterns_directory(
    vault_root: str | os.PathLike[str],
) -> tuple[Path, int]:
    """Open/create the vault path without ever following a symlink component."""

    if not isinstance(vault_root, (str, os.PathLike)):
        _validation("vault_root must be an explicit filesystem path")
    root = Path(vault_root)
    if not root.is_absolute():
        _validation("vault_root must be absolute")
    normalized_root = Path(os.path.abspath(os.path.normpath(os.fspath(root))))
    if normalized_root != root or normalized_root == Path(normalized_root.anchor):
        _validation("vault_root must be a canonical, non-root absolute path")
    descriptor = os.open(normalized_root.anchor, _directory_flags())
    current = Path(normalized_root.anchor)
    try:
        for component in normalized_root.parts[1:]:
            next_descriptor = _open_child_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
            current /= component
        for component in ("personal", "patterns"):
            next_descriptor = _open_child_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
            current /= component
        return current, descriptor
    except Exception:
        os.close(descriptor)
        raise


def _brief_target(
    vault_root: str | os.PathLike[str],
    cutoff_date: str,
    synthesis_id: str,
) -> tuple[Path, Path]:
    patterns, descriptor = _secure_patterns_directory(vault_root)
    os.close(descriptor)
    filename = f"autonomous-insights-{cutoff_date}-{synthesis_id}.md"
    target = patterns / filename
    if target.parent != patterns:
        raise SynthesisError("unsafe_path", "synthesis path escaped personal/patterns")
    return patterns, target


def _read_existing_at(directory_fd: int, filename: str) -> bytes | None:
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise SynthesisError(
                "unsafe_path", "pattern brief target is a symlink or unsafe path",
            ) from exc
        raise
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SynthesisError(
                "unsafe_path", "pattern brief target is not a regular file",
            )
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SynthesisError(
                "unsafe_path", "pattern brief target must retain mode 0600",
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def write_synthesis_markdown(
    conn: sqlite3.Connection,
    synthesis_id: str,
    *,
    vault_root: str | os.PathLike[str],
) -> Path:
    """Atomically create the unique append-only personal-pattern brief.

    A same-directory temporary file is fsynced and then hard-linked into the
    final name.  ``link`` is atomic and refuses an existing target, so neither
    retries nor a symlink can overwrite a prior brief.
    """

    synthesis = read_synthesis(conn, synthesis_id)
    markdown = synthesis["rendered_md"]
    if markdown is None:
        raise SynthesisError("no_message", "no-message synthesis has no pattern brief")
    try:
        patterns, directory_descriptor = _secure_patterns_directory(vault_root)
    except SynthesisError:
        raise
    except OSError as exc:
        raise SynthesisError(
            "filesystem_error",
            "could not open the append-only pattern directory",
        ) from exc
    filename = (
        f"autonomous-insights-{synthesis['cutoff_date']}-"
        f"{synthesis['synthesis_id']}.md"
    )
    target = patterns / filename
    data = markdown.encode("utf-8")
    existing = _read_existing_at(directory_descriptor, filename)
    if existing is not None:
        os.close(directory_descriptor)
        if existing == data:
            return target
        raise SynthesisError(
            "append_conflict",
            "pattern brief exists with different append-only content",
        )
    temp_name = f".{filename}.tmp-{secrets.token_hex(12)}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(
                temp_name,
                filename,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            raced = _read_existing_at(directory_descriptor, filename)
            if raced != data:
                raise SynthesisError(
                    "append_conflict",
                    "pattern brief exists with different append-only content",
                ) from None
        os.fsync(directory_descriptor)
        os.unlink(temp_name, dir_fd=directory_descriptor)
        return target
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise SynthesisError(
                "append_conflict", "pattern brief append conflicted",
            ) from exc
        raise SynthesisError("filesystem_error", "could not append pattern brief") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temp_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        os.close(directory_descriptor)


__all__ = [
    "LEGACY_MODEL_ID",
    "LEGACY_PROVIDER",
    "SYNTHESIS_CONTEXT_VERSION",
    "SYNTHESIS_CONTRACT_VERSION",
    "SynthesisError",
    "annotation_input_sha256",
    "parse_synthesis_json",
    "read_synthesis",
    "record_synthesis",
    "render_synthesis_markdown",
    "synthesis_evidence_fingerprint",
    "synthesis_history",
    "validate_synthesis_record",
    "write_synthesis_markdown",
]
