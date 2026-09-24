"""Deterministic Phase 6 cadence, trigger-queue, and notification-outbox logic.

This module owns no scheduler and performs no network or model calls.  Callers
must open the SQLite transaction explicitly; every mutating helper is designed
to run inside the owning ``BEGIN IMMEDIATE`` transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
import secrets
import sqlite3
import uuid
from typing import Any

from .contracts import DateRange, FeatureDefinition, canonical_json
from .goals import collector_freshness, list_goals
from .migrations import require_version
from .settings import CANON_TZ, TIMEZONE_NAME


ORCHESTRATOR_CONTRACT_VERSION = "insight-orchestrator-v1"
RANGE_PLAN_VERSION = "cadence-range-plan-v1"
NOTIFICATION_CONTRACT_VERSION = "insight-notification-v1"
CONTEXT_VERSION = "2"

BASE_OUTCOMES = (
    "subjective.day_rating",
    "subjective.energy",
    "subjective.focus",
    "subjective.mood",
    "adherence.word_kept",
)
COLLECTOR_SOURCES = ("google-health", "hevy", "weather", "air")
TRIGGER_KINDS = frozenset({
    "pain_started", "pain_worsened", "quarterly_observation",
    "running_restart", "training_load_change", "medication_regime_change",
    "hypothesis_reversal", "hypothesis_replicated", "outcome_milestone",
    "manual",
})
TRIGGER_TERMINAL_STATES = frozenset({"processed", "suppressed", "dead_letter"})
NOTIFICATION_TERMINAL_STATES = frozenset({
    "sent", "suppressed", "uncertain", "dead_letter",
})
RETRY_DELAYS_MINUTES = (5, 10, 20, 40, 80)
KNOWN_NOT_SENT_REASONS = frozenset({
    "local_validation_failed", "sender_spawn_failed", "provider_rejected",
})
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
SOURCE_ROW_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,255}$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


class OrchestrationError(RuntimeError):
    """Controlled Phase 6 validation, fencing, or invariant failure."""

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


def _validation(message: str) -> None:
    raise OrchestrationError("validation_error", message, validation=True)


def _exact_object(
    value: object,
    *,
    allowed: Iterable[str],
    required: Iterable[str],
    what: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        _validation(f"{what} must be one JSON object")
    item = value
    allowed_set, required_set = set(allowed), set(required)
    unknown, missing = set(item) - allowed_set, required_set - set(item)
    if unknown:
        _validation(f"unknown {what} key(s): {', '.join(sorted(unknown))}")
    if missing:
        _validation(f"missing {what} key(s): {', '.join(sorted(missing))}")
    return item


def parse_json_object(text: str, *, max_bytes: int = 131_072) -> dict[str, Any]:
    """Parse one bounded object while rejecting duplicate keys and trailing JSON."""

    if not isinstance(text, str):
        _validation("stdin must be text")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        _validation("stdin must be valid UTF-8")
    if len(encoded) > max_bytes:
        _validation("stdin exceeds the maximum size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _validation(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        decoder = json.JSONDecoder(
            object_pairs_hook=pairs,
            parse_constant=lambda _value: _validation(
                "non-finite JSON numbers are not allowed",
            ),
        )
        value, end = decoder.raw_decode(text)
    except OrchestrationError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
        _validation(f"stdin must contain valid JSON: {exc}")
    if text[end:].strip():
        _validation("stdin must contain exactly one JSON document")
    if type(value) is not dict:
        _validation("stdin must contain one JSON object")
    return value


def sha256_id(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _sha(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        _validation(f"{field} must be a lower-case sha256 identifier")
    return value


def _code(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or CODE_RE.fullmatch(value) is None:
        _validation(f"{field} must be a bounded code token")
    return value


def _source(value: object, field: str) -> str:
    if not isinstance(value, str) or SOURCE_RE.fullmatch(value) is None:
        _validation(f"{field} must be a bounded source token")
    return value


def _bounded(value: object, field: str, limit: int, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        _validation(f"{field} must contain 1-{limit} characters")
    return value


def _iso_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        _validation(f"{field} must be canonical ISO YYYY-MM-DD")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        _validation(f"{field} must be canonical ISO YYYY-MM-DD")
    return value


def canonical_timestamp(value: str | datetime | None = None, field: str = "timestamp") -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _validation(f"{field} must be a timezone-aware ISO timestamp")
    else:
        _validation(f"{field} must be a timezone-aware ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _validation(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timestamp_dt(value: object, field: str) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value, field))


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) > 36:
        _validation(f"{field} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _validation(f"{field} must be a canonical UUID")
    if str(parsed) != value:
        _validation(f"{field} must be a canonical lower-case UUID")
    return value


def _source_row_key(value: object) -> str:
    if not isinstance(value, str) or SOURCE_ROW_RE.fullmatch(value) is None:
        _validation("source_row_key must be a bounded opaque reference")
    return value


def _generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _validation("lease_generation must be a positive integer")
    return value


def _token(value: object) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        _validation("lease_token must be a 32-byte lower-case hexadecimal token")
    return value


def _token_sha256(token: str) -> str:
    return "sha256:" + hashlib.sha256(bytes.fromhex(token)).hexdigest()


def _range(role: str, start: date | None, end: date | None) -> dict[str, Any]:
    return {
        "range_role": role,
        "requested_range_kind": "all" if start is None else "bounded",
        "requested_from": start.isoformat() if start is not None else None,
        "requested_to": end.isoformat() if end is not None else None,
    }


def cadence_plan(
    kind: str,
    *,
    local_now: datetime | None = None,
    anchor: str | None = None,
    event_date: str | None = None,
    analysis_to: str | None = None,
) -> dict[str, Any]:
    """Return the frozen inclusive canonical-timezone range plan."""

    if kind not in {"nightly", "weekly", "monthly", "trigger"}:
        _validation("kind must be nightly, weekly, monthly, or trigger")
    now = local_now or datetime.now(CANON_TZ)
    if now.tzinfo is None or now.utcoffset() is None:
        _validation("local_now must include a timezone")
    local_day = now.astimezone(CANON_TZ).date()
    explicit = date.fromisoformat(_iso_date(anchor, "anchor")) if anchor else None
    if kind == "trigger":
        raw = analysis_to or event_date or anchor
        if raw is None:
            _validation("trigger plan requires event_date or analysis_to")
        anchor_day = date.fromisoformat(_iso_date(raw, "trigger anchor"))
    elif explicit is not None:
        anchor_day = explicit
        if kind == "weekly" and anchor_day.weekday() != 5:
            _validation("weekly anchor must be a Saturday")
    elif kind == "weekly":
        # Monday=0; preceding Saturday is always strictly before today.
        anchor_day = local_day - timedelta(days=((local_day.weekday() - 5) % 7 or 7))
    else:
        anchor_day = local_day - timedelta(days=1)
    if kind == "monthly":
        ranges = (
            _range("primary", None, None),
            _range("recent", anchor_day - timedelta(days=89), anchor_day),
            _range(
                "historical",
                anchor_day - timedelta(days=179),
                anchor_day - timedelta(days=90),
            ),
        )
    else:
        ranges = (_range("primary", anchor_day - timedelta(days=364), anchor_day),)
    return {
        "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
        "range_plan_version": RANGE_PLAN_VERSION,
        "timezone": TIMEZONE_NAME,
        "kind": kind,
        "anchor_date": anchor_day.isoformat(),
        "ranges": list(ranges),
        "dst_disclosure": (
            "nightly uses the configured civil timezone; UTC schedulers may "
            "shift local delivery across DST"
        ),
    }


def range_objects(plan: Mapping[str, Any]) -> list[tuple[str, DateRange]]:
    result: list[tuple[str, DateRange]] = []
    for item in plan["ranges"]:
        if item["requested_range_kind"] == "all":
            requested = DateRange(start=None, end=None, kind="all")
        else:
            requested = DateRange(
                start=date.fromisoformat(item["requested_from"]),
                end=date.fromisoformat(item["requested_to"]),
            )
        result.append((item["range_role"], requested))
    return result


def outcome_modes(
    conn: sqlite3.Connection,
    *,
    trigger_kind: str | None = None,
) -> dict[str, Any]:
    """Resolve base plus every enabled concrete goal outcome, sorted/deduped."""

    selected = set(BASE_OUTCOMES)
    for goal in list_goals(conn, include_disabled=False)["goals"]:
        if goal["enabled"] and goal["outcome_key"] is not None:
            selected.add(goal["outcome_key"])
    if trigger_kind is not None:
        if trigger_kind not in TRIGGER_KINDS:
            _validation("invalid trigger_kind")
        # Trigger relevance is a filter over enabled concrete outcomes only;
        # the immutable base set always remains present.
        if trigger_kind in {"pain_started", "pain_worsened"}:
            selected.update(
                goal["outcome_key"]
                for goal in list_goals(conn, include_disabled=False)["goals"]
                if goal["enabled"] and str(goal["outcome_key"] or "").startswith("pain.")
            )
    expanded = sorted(
        (key, mode)
        for key in selected
        for mode in (
            ("ordinal", "green-vs-non-green", "red-vs-non-red")
            if key == "subjective.day_rating" else ("ordinal",)
        )
    )
    return {
        "selection": "trigger_mapped" if trigger_kind else "base_and_enabled",
        "outcomes": sorted(selected),
        "outcome_modes": [
            {"outcome_key": key, "outcome_mode": mode} for key, mode in expanded
        ],
        "bh_family_scope": "range_id/outcome_key/outcome_mode",
        "outcome_set_sha256": sha256_id(expanded),
    }


def freshness_snapshot(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        _validation("freshness now must include a timezone")
    sources = []
    for source_name in COLLECTOR_SOURCES:
        item = dict(collector_freshness(conn, source_name, now=reference))
        if item["status"] == "unknown":
            item["status"] = "freshness_unknown"
        sources.append(item)
    sources.append({
        "source": "apple-health",
        "status": "retired_historical",
        "freshness_days": None,
        "run_id": None,
        "completed_at": None,
    })
    return {
        "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
        "thresholds_hours": {"fresh": 30, "late": 48},
        "sources": sources,
    }


def _definition_dependency(definition: FeatureDefinition) -> str | None:
    key, adapter = definition.key, definition.adapter
    if key.startswith(("weather.", "environment.weather.")):
        return "weather"
    if key.startswith(("air.", "environment.air.")):
        return "air"
    if adapter == "training" or key.startswith(("training.", "strength.")):
        return "hevy"
    if key == "wearable.hrv.apple_sdnn_ms":
        return None
    if key == "wearable.hrv.fitbit_rmssd_ms":
        return "google-health"
    if adapter == "daily" and definition.source_semantics in {
        "selected_automated_daily", "automated_daily",
    }:
        return "google-health"
    return None


def suppress_stale_dependencies(
    definitions: Sequence[FeatureDefinition],
    freshness: Mapping[str, Any],
) -> tuple[list[FeatureDefinition], list[dict[str, str]]]:
    """Remove only stale-source dependent candidates.

    ``freshness_unknown`` does not erase existing measurements.  Its adapters
    retain their definitions, and their existing completeness logic is the
    only code allowed to create structural zeros.
    """

    states = {item["source"]: item["status"] for item in freshness["sources"]}
    kept, suppressed = [], []
    for definition in definitions:
        dependency = _definition_dependency(definition)
        if (
            dependency is not None
            and states.get(dependency) == "stale"
            and definition.candidate_enabled
        ):
            suppressed.append({
                "feature_key": definition.key,
                "source": dependency,
                "reason_code": "stale_collector_dependency",
            })
        else:
            kept.append(definition)
    return kept, sorted(suppressed, key=lambda item: (item["source"], item["feature_key"]))


def trigger_identity(
    trigger_kind: str,
    source_table: str,
    source_row_key: str,
    event_date: str,
) -> dict[str, str]:
    if trigger_kind not in TRIGGER_KINDS:
        _validation("invalid trigger_kind")
    _source(source_table, "source_table")
    _source_row_key(source_row_key)
    _iso_date(event_date, "event_date")
    material = {
        "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
        "kind": "trigger",
        "trigger_kind": trigger_kind,
        "source_table": source_table,
        "source_row_key": source_row_key,
        "event_date": event_date,
    }
    dedupe_key = sha256_id(material)
    return {"trigger_id": sha256_id({**material, "identity": "id"}), "dedupe_key": dedupe_key}


def validate_trigger_enqueue(payload: object) -> dict[str, Any]:
    keys = {
        "trigger_id", "trigger_kind", "source_table", "source_row_key",
        "event_date", "dedupe_key", "not_before",
    }
    item = _exact_object(payload, allowed=keys, required=keys, what="trigger enqueue")
    trigger_id = _sha(item["trigger_id"], "trigger_id")
    dedupe_key = _sha(item["dedupe_key"], "dedupe_key")
    kind = item["trigger_kind"]
    if kind not in TRIGGER_KINDS:
        _validation("invalid trigger_kind")
    source_table = _source(item["source_table"], "source_table")
    source_row_key = _source_row_key(item["source_row_key"])
    event_date = _iso_date(item["event_date"], "event_date")
    not_before = canonical_timestamp(item["not_before"], "not_before")
    expected = trigger_identity(kind, source_table, source_row_key, event_date)
    if trigger_id != expected["trigger_id"] or dedupe_key != expected["dedupe_key"]:
        raise OrchestrationError(
            "identity_mismatch", "trigger_id/dedupe_key do not match canonical source identity",
            validation=True,
        )
    return {
        "trigger_id": trigger_id, "trigger_kind": kind,
        "source_table": source_table, "source_row_key": source_row_key,
        "event_date": event_date, "dedupe_key": dedupe_key,
        "not_before": not_before,
    }


def _row(conn: sqlite3.Connection, sql: str, args: Sequence[Any] = ()) -> dict[str, Any] | None:
    cursor = conn.execute(sql, tuple(args))
    value = cursor.fetchone()
    if value is None:
        return None
    if isinstance(value, sqlite3.Row):
        return dict(value)
    assert cursor.description is not None
    return {
        column[0]: field
        for column, field in zip(cursor.description, value, strict=True)
    }


def _event_id(
    conn: sqlite3.Connection,
    table: str,
    foreign_key: str,
    entity_id: str,
    event_kind: str,
    occurred_at: str,
    generation: int,
    attempt: int,
) -> str:
    sequence = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {foreign_key}=?", (entity_id,)
    ).fetchone()[0]
    return sha256_id({
        "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
        "table": table, "entity_id": entity_id, "event_kind": event_kind,
        "occurred_at": occurred_at, "lease_generation": generation,
        "attempt_no": attempt, "sequence": sequence,
    })


def _trigger_event(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
    event_kind: str,
    occurred_at: str,
    *,
    reason_code: str | None = None,
    analysis_batch_id: str | None = None,
    synthesis_id: str | None = None,
) -> None:
    event_id = _event_id(
        conn, "insight_trigger_events", "trigger_id", row["trigger_id"],
        event_kind, occurred_at, int(row["lease_generation"]), int(row["attempt_count"]),
    )
    conn.execute(
        """INSERT INTO insight_trigger_events(
             event_id,trigger_id,lease_generation,attempt_no,event_kind,
             reason_code,analysis_batch_id,synthesis_id,occurred_at
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            event_id, row["trigger_id"], row["lease_generation"],
            row["attempt_count"], event_kind, reason_code,
            analysis_batch_id, synthesis_id, occurred_at,
        ),
    )


def enqueue_trigger(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Insert/replay one trigger in the caller's owning transaction."""

    require_version(conn, 4)
    value = validate_trigger_enqueue(payload)
    timestamp = canonical_timestamp(now)
    existing = _row(
        conn, "SELECT * FROM insight_triggers WHERE dedupe_key=?",
        (value["dedupe_key"],),
    )
    if existing is not None:
        immutable = (
            "trigger_id", "trigger_kind", "source_table", "source_row_key",
            "event_date", "dedupe_key", "not_before",
        )
        if any(existing[key] != value[key] for key in immutable):
            raise OrchestrationError(
                "idempotency_conflict",
                "trigger dedupe_key already exists with different immutable content",
            )
        return {
            "ok": True, "trigger_id": existing["trigger_id"],
            "state": existing["state"], "created": False,
        }
    conn.execute(
        """INSERT INTO insight_triggers(
             trigger_id,trigger_kind,source_table,source_row_key,event_date,
             dedupe_key,state,not_before,attempt_count,max_attempts,
             lease_generation,lease_owner,lease_token_sha256,lease_expires_at,
             last_error_code,analysis_batch_id,synthesis_id,processed_at,
             created_at,updated_at
           ) VALUES(?,?,?,?,?,?,'pending',?,0,5,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,?)""",
        (
            value["trigger_id"], value["trigger_kind"], value["source_table"],
            value["source_row_key"], value["event_date"], value["dedupe_key"],
            value["not_before"], timestamp, timestamp,
        ),
    )
    stored = _row(conn, "SELECT * FROM insight_triggers WHERE trigger_id=?", (value["trigger_id"],))
    assert stored is not None
    _trigger_event(conn, stored, "enqueued", timestamp)
    return {
        "ok": True, "trigger_id": value["trigger_id"],
        "state": "pending", "created": True,
    }


def enqueue_internal_trigger(
    conn: sqlite3.Connection,
    *,
    trigger_kind: str,
    source_table: str,
    source_row_key: str,
    event_date: str,
    not_before: str | datetime | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    identity = trigger_identity(trigger_kind, source_table, source_row_key, event_date)
    stable_not_before = (
        canonical_timestamp(not_before)
        if not_before is not None
        else f"{event_date}T00:00:00+00:00"
    )
    return enqueue_trigger(conn, {
        **identity,
        "trigger_kind": trigger_kind,
        "source_table": source_table,
        "source_row_key": source_row_key,
        "event_date": event_date,
        "not_before": stable_not_before,
    }, now=now)


def _clear_lease_sql() -> str:
    return "lease_owner=NULL,lease_token_sha256=NULL,lease_expires_at=NULL"


def _expire_one_trigger(conn: sqlite3.Connection, now: str) -> bool:
    stale = _row(
        conn,
        """SELECT * FROM insight_triggers
           WHERE state='leased' AND lease_expires_at<=?
           ORDER BY lease_expires_at,created_at,trigger_id LIMIT 1""",
        (now,),
    )
    if stale is None:
        return False
    terminal = int(stale["attempt_count"]) >= int(stale["max_attempts"])
    state, event = ("dead_letter", "dead_letter") if terminal else ("retry_wait", "lease_expired")
    conn.execute(
        f"""UPDATE insight_triggers
               SET state=?,not_before=?,{_clear_lease_sql()},
                   last_error_code='lease_expired',
                   processed_at=?,updated_at=?
             WHERE trigger_id=? AND state='leased' AND lease_generation=?""",
        (
            state, now, now if terminal else None, now,
            stale["trigger_id"], stale["lease_generation"],
        ),
    )
    changed = dict(stale)
    _trigger_event(conn, changed, event, now, reason_code="lease_expired")
    return True


def claim_trigger(
    conn: sqlite3.Connection,
    worker_id: str,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    require_version(conn, 4)
    worker = _uuid(worker_id, "worker_id")
    timestamp = canonical_timestamp(now)
    # Bound expiry work to one oldest lease per claim transaction.
    _expire_one_trigger(conn, timestamp)
    candidate = _row(
        conn,
        """SELECT * FROM insight_triggers
           WHERE state IN ('pending','retry_wait') AND not_before<=?
           ORDER BY not_before,created_at,trigger_id LIMIT 1""",
        (timestamp,),
    )
    if candidate is None:
        return {"ok": True, "claimed": False, "trigger": None}
    token = secrets.token_bytes(32).hex()
    generation = int(candidate["lease_generation"]) + 1
    attempt = int(candidate["attempt_count"]) + 1
    expires = (
        datetime.fromisoformat(timestamp) + timedelta(minutes=20)
    ).isoformat(timespec="seconds")
    cursor = conn.execute(
        """UPDATE insight_triggers
              SET state='leased',attempt_count=?,lease_generation=?,
                  lease_owner=?,lease_token_sha256=?,lease_expires_at=?,
                  last_error_code=NULL,updated_at=?
            WHERE trigger_id=? AND state IN ('pending','retry_wait')""",
        (
            attempt, generation, worker, _token_sha256(token), expires,
            timestamp, candidate["trigger_id"],
        ),
    )
    if cursor.rowcount != 1:
        raise OrchestrationError("claim_race", "trigger claim lost its transaction fence")
    claimed = _row(conn, "SELECT * FROM insight_triggers WHERE trigger_id=?", (candidate["trigger_id"],))
    assert claimed is not None
    _trigger_event(conn, claimed, "claimed", timestamp)
    return {
        "ok": True, "claimed": True,
        "trigger": {
            key: claimed[key] for key in (
                "trigger_id", "trigger_kind", "source_table", "source_row_key",
                "event_date", "attempt_count", "lease_generation",
                "lease_expires_at",
            )
        },
        "lease_token": token,
    }


def _lease_payload(
    payload: object,
    *,
    object_key: str,
    extra_keys: Iterable[str] = (),
    required_extra: Iterable[str] = (),
    what: str,
) -> dict[str, Any]:
    base = {object_key, "lease_generation", "lease_token"}
    item = _exact_object(
        payload, allowed=base | set(extra_keys),
        required=base | set(required_extra), what=what,
    )
    _sha(item[object_key], object_key)
    _generation(item["lease_generation"])
    _token(item["lease_token"])
    return item


def _fenced_trigger(
    conn: sqlite3.Connection,
    payload: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    row = _row(conn, "SELECT * FROM insight_triggers WHERE trigger_id=?", (payload["trigger_id"],))
    if (
        row is None or row["state"] != "leased"
        or int(row["lease_generation"]) != payload["lease_generation"]
        or not secrets.compare_digest(row["lease_token_sha256"], _token_sha256(payload["lease_token"]))
        or row["lease_expires_at"] <= now
    ):
        raise OrchestrationError("stale_lease", "trigger lease is stale or fenced")
    return row


def validate_trigger_lease(payload: object) -> dict[str, Any]:
    return _lease_payload(payload, object_key="trigger_id", what="trigger lease")


def assert_trigger_lease(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Validate a trigger fence without renewing or otherwise mutating it."""

    value = validate_trigger_lease(payload)
    row = _fenced_trigger(conn, value, canonical_timestamp(now))
    return {
        key: row[key] for key in (
            "trigger_id", "trigger_kind", "source_table", "source_row_key",
            "event_date", "attempt_count", "lease_generation",
            "lease_expires_at", "analysis_batch_id", "synthesis_id",
        )
    }


def renew_trigger(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    value = validate_trigger_lease(payload)
    timestamp = canonical_timestamp(now)
    row = _fenced_trigger(conn, value, timestamp)
    expires = (
        datetime.fromisoformat(timestamp) + timedelta(minutes=20)
    ).isoformat(timespec="seconds")
    conn.execute(
        """UPDATE insight_triggers SET lease_expires_at=?,updated_at=?
           WHERE trigger_id=? AND lease_generation=?""",
        (expires, timestamp, row["trigger_id"], row["lease_generation"]),
    )
    changed = {**row, "lease_expires_at": expires}
    _trigger_event(conn, changed, "renewed", timestamp)
    return {
        "ok": True, "trigger_id": row["trigger_id"],
        "lease_generation": row["lease_generation"], "lease_expires_at": expires,
    }


def validate_trigger_complete(payload: object) -> dict[str, Any]:
    item = _lease_payload(
        payload, object_key="trigger_id",
        extra_keys={"disposition", "reason_code", "analysis_batch_id", "synthesis_id"},
        required_extra={"disposition", "reason_code", "analysis_batch_id", "synthesis_id"},
        what="trigger completion",
    )
    if item["disposition"] not in {"processed", "suppressed"}:
        _validation("disposition must be processed or suppressed")
    _code(item["reason_code"], "reason_code")
    _sha(item["analysis_batch_id"], "analysis_batch_id", nullable=True)
    _sha(item["synthesis_id"], "synthesis_id", nullable=True)
    if item["disposition"] == "processed" and item["analysis_batch_id"] is None:
        _validation("processed trigger requires analysis_batch_id")
    if item["synthesis_id"] is not None and item["analysis_batch_id"] is None:
        _validation("synthesis_id requires analysis_batch_id")
    return item


def complete_trigger(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    value = validate_trigger_complete(payload)
    timestamp = canonical_timestamp(now)
    row = _fenced_trigger(conn, value, timestamp)
    batch_id, synthesis_id = value["analysis_batch_id"], value["synthesis_id"]
    if batch_id is not None:
        batch = _row(
            conn,
            """SELECT status,run_kind,initiator_key
                 FROM analysis_batches WHERE batch_id=?""",
            (batch_id,),
        )
        if (
            batch is None or batch["status"] == "running"
            or batch["run_kind"] != "trigger"
            or batch["initiator_key"] != f"trigger/{row['trigger_id']}"
        ):
            raise OrchestrationError(
                "durability_invariant",
                "linked analysis batch must be this trigger's durable terminal batch",
            )
    if synthesis_id is not None:
        synthesis = _row(
            conn,
            """SELECT status,analysis_batch_id
                 FROM synthesis_runs WHERE synthesis_id=?""",
            (synthesis_id,),
        )
        if (
            synthesis is None or synthesis["status"] == "running"
            or synthesis["analysis_batch_id"] != batch_id
        ):
            raise OrchestrationError(
                "durability_invariant",
                "linked synthesis must be durable, terminal, and belong to the linked batch",
            )
        outbox_rows = [
            dict(item) for item in conn.execute(
                """SELECT analysis_batch_id,synthesis_id,trigger_id
                     FROM insight_notification_outbox WHERE synthesis_id=?""",
                (synthesis_id,),
            )
        ]
        if any(
            item["analysis_batch_id"] != batch_id
            or item["synthesis_id"] != synthesis_id
            or item["trigger_id"] != row["trigger_id"]
            for item in outbox_rows
        ):
            raise OrchestrationError(
                "durability_invariant",
                "linked notification outbox ancestry does not match the trigger",
            )
    conn.execute(
        f"""UPDATE insight_triggers
               SET state=?,{_clear_lease_sql()},last_error_code=?,
                   analysis_batch_id=?,synthesis_id=?,processed_at=?,updated_at=?
             WHERE trigger_id=? AND lease_generation=?""",
        (
            value["disposition"], value["reason_code"], batch_id, synthesis_id,
            timestamp, timestamp, row["trigger_id"], row["lease_generation"],
        ),
    )
    _trigger_event(
        conn, row, value["disposition"], timestamp,
        reason_code=value["reason_code"], analysis_batch_id=batch_id,
        synthesis_id=synthesis_id,
    )
    return {
        "ok": True, "trigger_id": row["trigger_id"],
        "state": value["disposition"], "reason_code": value["reason_code"],
    }


def validate_trigger_fail(payload: object) -> dict[str, Any]:
    item = _lease_payload(
        payload, object_key="trigger_id",
        extra_keys={"failure_class", "reason_code"},
        required_extra={"failure_class", "reason_code"},
        what="trigger failure",
    )
    if item["failure_class"] not in {"transient", "permanent"}:
        _validation("failure_class must be transient or permanent")
    _code(item["reason_code"], "reason_code")
    return item


def fail_trigger(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    value = validate_trigger_fail(payload)
    timestamp = canonical_timestamp(now)
    row = _fenced_trigger(conn, value, timestamp)
    terminal = (
        value["failure_class"] == "permanent"
        or int(row["attempt_count"]) >= int(row["max_attempts"])
    )
    if terminal:
        state, not_before, event = "dead_letter", row["not_before"], "dead_letter"
        processed_at = timestamp
    else:
        delay = RETRY_DELAYS_MINUTES[int(row["attempt_count"]) - 1]
        not_before = (
            datetime.fromisoformat(timestamp) + timedelta(minutes=delay)
        ).isoformat(timespec="seconds")
        state, event, processed_at = "retry_wait", "retry_scheduled", None
    conn.execute(
        f"""UPDATE insight_triggers
               SET state=?,not_before=?,{_clear_lease_sql()},last_error_code=?,
                   processed_at=?,updated_at=?
             WHERE trigger_id=? AND lease_generation=?""",
        (
            state, not_before, value["reason_code"], processed_at, timestamp,
            row["trigger_id"], row["lease_generation"],
        ),
    )
    _trigger_event(conn, row, event, timestamp, reason_code=value["reason_code"])
    return {
        "ok": True, "trigger_id": row["trigger_id"], "state": state,
        "not_before": not_before, "reason_code": value["reason_code"],
    }


_NOTIFICATION_KEYS = {
    "channel_class", "destination_class", "payload", "dedupe_key", "not_before",
    "idempotency_mode", "provider_idempotency_key",
}


def validate_notification(value: object) -> dict[str, Any]:
    item = _exact_object(
        value, allowed=_NOTIFICATION_KEYS, required=_NOTIFICATION_KEYS,
        what="notification",
    )
    channel = _source(item["channel_class"], "channel_class")
    destination = _source(item["destination_class"], "destination_class")
    payload = item["payload"]
    if type(payload) is not dict:
        _validation("notification payload must be one JSON object")
    encoded = canonical_json(payload)
    if len(encoded.encode("utf-8")) > 65_536:
        _validation("notification payload exceeds the maximum size")
    dedupe = _sha(item["dedupe_key"], "dedupe_key")
    not_before = canonical_timestamp(item["not_before"], "not_before")
    mode = item["idempotency_mode"]
    if mode not in {"none", "provider_key"}:
        _validation("idempotency_mode must be none or provider_key")
    provider_key = _bounded(
        item["provider_idempotency_key"], "provider_idempotency_key", 200,
        nullable=True,
    )
    if (mode == "none") != (provider_key is None):
        _validation("provider_idempotency_key must be set exactly for provider_key mode")
    return {
        "channel_class": channel, "destination_class": destination,
        "payload": payload, "payload_json": encoded,
        "payload_sha256": sha256_id(payload), "dedupe_key": dedupe,
        "not_before": not_before, "idempotency_mode": mode,
        "provider_idempotency_key": provider_key,
    }


def notification_dedupe_key(
    *,
    cadence: str,
    evidence_fingerprints: Iterable[str],
    destination_class: str,
    context_version: str = CONTEXT_VERSION,
) -> str:
    if cadence not in {"manual", "weekly", "monthly", "trigger"}:
        _validation("invalid notification cadence")
    fingerprints = sorted(set(_sha(value, "evidence_fingerprint") for value in evidence_fingerprints))
    if not fingerprints:
        _validation("notification dedupe requires evidence fingerprints")
    _source(destination_class, "destination_class")
    if context_version != CONTEXT_VERSION:
        _validation(f"context_version must be {CONTEXT_VERSION}")
    return sha256_id({
        "contract_version": NOTIFICATION_CONTRACT_VERSION,
        "cadence": cadence, "evidence_fingerprints": fingerprints,
        "destination_class": destination_class,
        "context_version": context_version,
    })


def defer_quiet_hours(not_before: str, *, timezone_name: str = TIMEZONE_NAME) -> str:
    utc = _timestamp_dt(not_before, "not_before")
    if timezone_name != TIMEZONE_NAME:
        _validation(f"quiet-hour timezone must be {TIMEZONE_NAME}")
    local = utc.astimezone(CANON_TZ)
    if local.time() >= time(22, 0):
        local = datetime.combine(
            local.date() + timedelta(days=1), time(7, 0), tzinfo=CANON_TZ,
        )
    elif local.time() < time(7, 0):
        local = datetime.combine(local.date(), time(7, 0), tzinfo=CANON_TZ)
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def _notification_event(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
    event_kind: str,
    occurred_at: str,
    *,
    reason_code: str | None = None,
    provider_reference_sha256: str | None = None,
) -> None:
    event_id = _event_id(
        conn, "insight_notification_events", "notification_id",
        row["notification_id"], event_kind, occurred_at,
        int(row["lease_generation"]), int(row["attempt_count"]),
    )
    conn.execute(
        """INSERT INTO insight_notification_events(
             event_id,notification_id,lease_generation,attempt_no,event_kind,
             reason_code,provider_reference_sha256,occurred_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            event_id, row["notification_id"], row["lease_generation"],
            row["attempt_count"], event_kind, reason_code,
            provider_reference_sha256, occurred_at,
        ),
    )


def enqueue_notification(
    conn: sqlite3.Connection,
    notification: object,
    *,
    analysis_batch_id: str,
    synthesis_id: str,
    trigger_id: str | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Insert/replay an outbox row inside the synthesis transaction."""

    require_version(conn, 4)
    value = validate_notification(notification)
    _sha(analysis_batch_id, "analysis_batch_id")
    _sha(synthesis_id, "synthesis_id")
    _sha(trigger_id, "trigger_id", nullable=True)
    timestamp = canonical_timestamp(now)
    not_before = defer_quiet_hours(value["not_before"])
    notification_id = sha256_id({
        "contract_version": NOTIFICATION_CONTRACT_VERSION,
        "kind": "notification", "dedupe_key": value["dedupe_key"],
    })
    existing = _row(
        conn, "SELECT * FROM insight_notification_outbox WHERE dedupe_key=?",
        (value["dedupe_key"],),
    )
    if existing is not None:
        if existing["payload_sha256"] != value["payload_sha256"]:
            raise OrchestrationError(
                "invariant_failure",
                "notification dedupe_key already exists with a different payload hash",
            )
        immutable = (
            ("channel_class", value["channel_class"]),
            ("destination_class", value["destination_class"]),
            ("idempotency_mode", value["idempotency_mode"]),
            ("provider_idempotency_key", value["provider_idempotency_key"]),
        )
        if any(existing[field] != expected for field, expected in immutable):
            raise OrchestrationError(
                "invariant_failure",
                "notification dedupe replay changed immutable delivery metadata",
            )
        _notification_event(
            conn, existing, "duplicate_observed", timestamp,
            reason_code="exact_duplicate",
        )
        return {
            "ok": True, "notification_id": existing["notification_id"],
            "state": existing["state"], "created": False,
            "quiet_hours_deferred": existing["not_before"] != value["not_before"],
        }
    conn.execute(
        """INSERT INTO insight_notification_outbox(
             notification_id,analysis_batch_id,synthesis_id,trigger_id,dedupe_key,
             channel_class,destination_class,payload_json,payload_sha256,
             idempotency_mode,provider_idempotency_key,state,not_before,
             attempt_count,max_attempts,lease_generation,lease_owner,
             lease_token_sha256,lease_expires_at,dispatch_started_at,
             last_error_code,sent_at,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,0,5,0,NULL,NULL,NULL,NULL,NULL,NULL,?,?)""",
        (
            notification_id, analysis_batch_id, synthesis_id, trigger_id,
            value["dedupe_key"], value["channel_class"],
            value["destination_class"], value["payload_json"],
            value["payload_sha256"], value["idempotency_mode"],
            value["provider_idempotency_key"], not_before, timestamp, timestamp,
        ),
    )
    stored = _row(
        conn, "SELECT * FROM insight_notification_outbox WHERE notification_id=?",
        (notification_id,),
    )
    assert stored is not None
    _notification_event(conn, stored, "enqueued", timestamp)
    return {
        "ok": True, "notification_id": notification_id, "state": "pending",
        "created": True, "quiet_hours_deferred": not_before != value["not_before"],
        "not_before": not_before,
    }


def _expire_one_notification(conn: sqlite3.Connection, now: str) -> bool:
    stale = _row(
        conn,
        """SELECT * FROM insight_notification_outbox
           WHERE state IN ('leased','dispatching') AND lease_expires_at<=?
           ORDER BY lease_expires_at,created_at,notification_id LIMIT 1""",
        (now,),
    )
    if stale is None:
        return False
    if stale["state"] == "dispatching":
        if stale["idempotency_mode"] == "none":
            state, event = "uncertain", "uncertain"
        elif int(stale["attempt_count"]) >= int(stale["max_attempts"]):
            state, event = "dead_letter", "dead_letter"
        else:
            state, event = "retry_wait", "retry_scheduled"
    elif int(stale["attempt_count"]) >= int(stale["max_attempts"]):
        state, event = "dead_letter", "dead_letter"
    else:
        state, event = "retry_wait", "lease_expired_before_dispatch"
    conn.execute(
        f"""UPDATE insight_notification_outbox
               SET state=?,not_before=?,{_clear_lease_sql()},
                   last_error_code='lease_expired',updated_at=?
             WHERE notification_id=? AND lease_generation=?""",
        (
            state, now, now, stale["notification_id"], stale["lease_generation"],
        ),
    )
    _notification_event(conn, stale, event, now, reason_code="lease_expired")
    return True


def claim_notification(
    conn: sqlite3.Connection,
    worker_id: str,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    require_version(conn, 4)
    worker = _uuid(worker_id, "worker_id")
    timestamp = canonical_timestamp(now)
    # Bound expiry work to one oldest lease per claim transaction.
    _expire_one_notification(conn, timestamp)
    candidate = _row(
        conn,
        """SELECT * FROM insight_notification_outbox
           WHERE state IN ('pending','retry_wait') AND not_before<=?
           ORDER BY not_before,created_at,notification_id LIMIT 1""",
        (timestamp,),
    )
    if candidate is None:
        return {"ok": True, "claimed": False, "notification": None}
    token = secrets.token_bytes(32).hex()
    generation = int(candidate["lease_generation"]) + 1
    attempt = int(candidate["attempt_count"]) + 1
    expires = (
        datetime.fromisoformat(timestamp) + timedelta(minutes=10)
    ).isoformat(timespec="seconds")
    cursor = conn.execute(
        """UPDATE insight_notification_outbox
              SET state='leased',attempt_count=?,lease_generation=?,
                  lease_owner=?,lease_token_sha256=?,lease_expires_at=?,
                  last_error_code=NULL,updated_at=?
            WHERE notification_id=? AND state IN ('pending','retry_wait')""",
        (
            attempt, generation, worker, _token_sha256(token), expires,
            timestamp, candidate["notification_id"],
        ),
    )
    if cursor.rowcount != 1:
        raise OrchestrationError("claim_race", "notification claim lost its transaction fence")
    claimed = _row(
        conn, "SELECT * FROM insight_notification_outbox WHERE notification_id=?",
        (candidate["notification_id"],),
    )
    assert claimed is not None
    _notification_event(conn, claimed, "claimed", timestamp)
    return {
        "ok": True, "claimed": True,
        "notification": {
            key: claimed[key] for key in (
                "notification_id", "channel_class", "destination_class",
                "payload_json", "payload_sha256", "idempotency_mode",
                "provider_idempotency_key", "attempt_count", "lease_generation",
                "lease_expires_at",
            )
        },
        "lease_token": token,
    }


def _fenced_notification(
    conn: sqlite3.Connection,
    payload: Mapping[str, Any],
    now: str,
    *,
    states: Iterable[str],
) -> dict[str, Any]:
    row = _row(
        conn, "SELECT * FROM insight_notification_outbox WHERE notification_id=?",
        (payload["notification_id"],),
    )
    if (
        row is None or row["state"] not in set(states)
        or int(row["lease_generation"]) != payload["lease_generation"]
        or not secrets.compare_digest(row["lease_token_sha256"], _token_sha256(payload["lease_token"]))
        or row["lease_expires_at"] <= now
    ):
        raise OrchestrationError("stale_lease", "notification lease is stale or fenced")
    return row


def begin_notification_dispatch(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    value = _lease_payload(
        payload, object_key="notification_id", what="notification dispatch",
    )
    timestamp = canonical_timestamp(now)
    row = _fenced_notification(conn, value, timestamp, states={"leased"})
    conn.execute(
        """UPDATE insight_notification_outbox
              SET state='dispatching',dispatch_started_at=?,updated_at=?
            WHERE notification_id=? AND lease_generation=?""",
        (timestamp, timestamp, row["notification_id"], row["lease_generation"]),
    )
    changed = {**row, "state": "dispatching", "dispatch_started_at": timestamp}
    _notification_event(conn, changed, "dispatch_committed", timestamp)
    return {
        "ok": True, "notification_id": row["notification_id"],
        "state": "dispatching", "dispatch_started_at": timestamp,
        "payload_sha256": row["payload_sha256"],
        "idempotency_mode": row["idempotency_mode"],
        "provider_idempotency_key": row["provider_idempotency_key"],
    }


def acknowledge_notification(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    value = _lease_payload(
        payload, object_key="notification_id",
        extra_keys={"provider_reference"}, required_extra={"provider_reference"},
        what="notification acknowledgement",
    )
    reference = _bounded(value["provider_reference"], "provider_reference", 500, nullable=True)
    timestamp = canonical_timestamp(now)
    row = _fenced_notification(conn, value, timestamp, states={"dispatching"})
    reference_sha = sha256_id({"provider_reference": reference}) if reference else None
    conn.execute(
        f"""UPDATE insight_notification_outbox
               SET state='sent',{_clear_lease_sql()},sent_at=?,last_error_code=NULL,
                   updated_at=?
             WHERE notification_id=? AND lease_generation=?""",
        (timestamp, timestamp, row["notification_id"], row["lease_generation"]),
    )
    _notification_event(
        conn, row, "provider_ack", timestamp,
        provider_reference_sha256=reference_sha,
    )
    return {"ok": True, "notification_id": row["notification_id"], "state": "sent"}


def validate_notification_fail(payload: object) -> dict[str, Any]:
    item = _lease_payload(
        payload, object_key="notification_id",
        extra_keys={"failure_class", "reason_code"},
        required_extra={"failure_class", "reason_code"},
        what="notification failure",
    )
    if item["failure_class"] not in {"known_not_sent", "ambiguous", "permanent"}:
        _validation("failure_class must be known_not_sent, ambiguous, or permanent")
    reason = _code(item["reason_code"], "reason_code")
    if item["failure_class"] == "known_not_sent" and reason not in KNOWN_NOT_SENT_REASONS:
        _validation("reason_code is not a provable known-not-sent class")
    return item


def fail_notification(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    value = validate_notification_fail(payload)
    timestamp = canonical_timestamp(now)
    row = _fenced_notification(
        conn, value, timestamp, states={"leased", "dispatching"},
    )
    failure = value["failure_class"]
    if failure == "ambiguous" and row["state"] == "dispatching" and row["idempotency_mode"] == "none":
        state, event, not_before = "uncertain", "uncertain", row["not_before"]
    elif failure == "permanent" or int(row["attempt_count"]) >= int(row["max_attempts"]):
        state, event, not_before = "dead_letter", "dead_letter", row["not_before"]
    else:
        if failure == "ambiguous" and row["idempotency_mode"] != "provider_key":
            state, event, not_before = "uncertain", "uncertain", row["not_before"]
        else:
            delay = RETRY_DELAYS_MINUTES[int(row["attempt_count"]) - 1]
            not_before = (
                datetime.fromisoformat(timestamp) + timedelta(minutes=delay)
            ).isoformat(timespec="seconds")
            state = "retry_wait"
            event = "known_not_sent" if failure == "known_not_sent" else "retry_scheduled"
    conn.execute(
        f"""UPDATE insight_notification_outbox
               SET state=?,not_before=?,{_clear_lease_sql()},last_error_code=?,
                   updated_at=?
             WHERE notification_id=? AND lease_generation=?""",
        (
            state, not_before, value["reason_code"], timestamp,
            row["notification_id"], row["lease_generation"],
        ),
    )
    _notification_event(conn, row, event, timestamp, reason_code=value["reason_code"])
    if state == "retry_wait" and event == "known_not_sent":
        _notification_event(
            conn, row, "retry_scheduled", timestamp,
            reason_code=value["reason_code"],
        )
    return {
        "ok": True, "notification_id": row["notification_id"],
        "state": state, "not_before": not_before,
    }


def resolve_notification(
    conn: sqlite3.Connection,
    payload: object,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    keys = {"notification_id", "resolution", "reason_code"}
    item = _exact_object(
        payload, allowed=keys, required=keys, what="notification resolution",
    )
    notification_id = _sha(item["notification_id"], "notification_id")
    resolution = item["resolution"]
    if resolution not in {"confirmed_sent", "confirmed_not_sent_retry"}:
        _validation("invalid operator resolution")
    reason = _code(item["reason_code"], "reason_code")
    timestamp = canonical_timestamp(now)
    row = _row(
        conn, "SELECT * FROM insight_notification_outbox WHERE notification_id=?",
        (notification_id,),
    )
    if row is None or row["state"] != "uncertain":
        raise OrchestrationError(
            "invalid_state", "operator resolution requires an uncertain notification",
        )
    if resolution == "confirmed_sent":
        state, event, sent_at, not_before = (
            "sent", "manual_resolved_sent", timestamp, row["not_before"],
        )
    else:
        if int(row["attempt_count"]) >= int(row["max_attempts"]):
            raise OrchestrationError(
                "attempts_exhausted",
                "confirmed-not-sent notification has exhausted its claims",
            )
        state, event, sent_at, not_before = (
            "retry_wait", "manual_resolved_retry", None, timestamp,
        )
    conn.execute(
        """UPDATE insight_notification_outbox
              SET state=?,not_before=?,sent_at=?,last_error_code=?,updated_at=?
            WHERE notification_id=? AND state='uncertain'""",
        (state, not_before, sent_at, reason, timestamp, notification_id),
    )
    _notification_event(conn, row, event, timestamp, reason_code=reason)
    return {"ok": True, "notification_id": notification_id, "state": state}


def insight_run_status(conn: sqlite3.Connection, *, limit: int = 20) -> dict[str, Any]:
    """Return a read-only, token/narrative/destination-secret-free audit view."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        _validation("limit must be between 1 and 100")
    batches = [
        dict(row) for row in conn.execute(
            """SELECT batch_id,run_kind,anchor_date,status,status_reason_code,
                      run_count,completed_count,insufficient_count,no_data_count,
                      failed_count,started_at,completed_at
                 FROM analysis_batches
                ORDER BY started_at DESC,batch_id DESC LIMIT ?""",
            (limit,),
        )
    ]
    triggers = [
        dict(row) for row in conn.execute(
            """SELECT trigger_id,trigger_kind,event_date,state,not_before,
                      attempt_count,max_attempts,lease_generation,
                      lease_expires_at,last_error_code,analysis_batch_id,
                      synthesis_id,processed_at,created_at,updated_at
                 FROM insight_triggers
                ORDER BY created_at DESC,trigger_id DESC LIMIT ?""",
            (limit,),
        )
    ]
    notifications = [
        dict(row) for row in conn.execute(
            """SELECT notification_id,analysis_batch_id,synthesis_id,trigger_id,
                      channel_class,destination_class,payload_sha256,
                      idempotency_mode,state,not_before,attempt_count,max_attempts,
                      lease_generation,lease_expires_at,dispatch_started_at,
                      last_error_code,sent_at,created_at,updated_at
                 FROM insight_notification_outbox
                ORDER BY created_at DESC,notification_id DESC LIMIT ?""",
            (limit,),
        )
    ]
    trigger_events = [
        dict(row) for row in conn.execute(
            """SELECT event_id,trigger_id,lease_generation,attempt_no,event_kind,
                      reason_code,analysis_batch_id,synthesis_id,occurred_at
                 FROM insight_trigger_events
                ORDER BY occurred_at DESC,event_id DESC LIMIT ?""",
            (limit,),
        )
    ]
    notification_events = [
        dict(row) for row in conn.execute(
            """SELECT event_id,notification_id,lease_generation,attempt_no,
                      event_kind,reason_code,provider_reference_sha256,occurred_at
                 FROM insight_notification_events
                ORDER BY occurred_at DESC,event_id DESC LIMIT ?""",
            (limit,),
        )
    ]
    return {
        "ok": True, "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
        "read_only": True, "limit": limit, "batches": batches,
        "triggers": triggers, "notifications": notifications,
        "trigger_events": trigger_events,
        "notification_events": notification_events,
        "redacted_fields": [
            "lease_owner", "lease_token_sha256", "payload_json",
            "provider_idempotency_key", "rendered_md", "narrative_md",
        ],
    }


@dataclass(frozen=True, slots=True)
class NoveltyDecision:
    eligible: bool
    status: str
    reason_code: str
    transition_fingerprints: tuple[str, ...]
    finding_fingerprints: tuple[str, ...]


def novelty_decision(
    *,
    cadence: str,
    finding_fingerprints: Iterable[str],
    transition_fingerprints: Iterable[str],
    approved_trigger: bool = False,
    useful_checkin: bool = False,
    has_weekly_baseline: bool = True,
) -> NoveltyDecision:
    findings = tuple(sorted(set(finding_fingerprints)))
    transitions = tuple(sorted(set(transition_fingerprints)))
    if cadence == "weekly" and not has_weekly_baseline:
        return NoveltyDecision(
            False, "no_novelty", "bootstrap_baseline_required", (), (),
        )
    if transitions or approved_trigger or useful_checkin:
        return NoveltyDecision(
            True, "completed", "novel_evidence", transitions, findings,
        )
    return NoveltyDecision(
        False, "no_novelty", "no_eligible_novel_evidence", transitions, findings,
    )


def structured_slots(
    *,
    cadence: str,
    run_rows: Sequence[Mapping[str, Any]],
    finding_rows: Sequence[Mapping[str, Any]],
    evaluation_rows: Sequence[Mapping[str, Any]],
    freshness: Mapping[str, Any],
    suppressed_dependencies: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build deterministic ancestry-bound preparation slots without prose."""

    def stored_json(row: Mapping[str, Any], field: str, expected: type) -> Any:
        raw = row.get(field)
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            raise OrchestrationError(
                "invariant_failure", f"stored {field} is not valid JSON",
            ) from exc
        if not isinstance(value, expected) or canonical_json(value) != raw:
            raise OrchestrationError(
                "invariant_failure", f"stored {field} is not canonical",
            )
        return value

    missing = lambda reason: {"status": "missing", "reason_code": reason}
    findings = []
    for row in finding_rows:
        evidence = stored_json(row, "evidence_json", dict)
        findings.append({
            "finding_id": row["finding_id"],
            "evidence_fingerprint": row["evidence_fingerprint"],
            "candidate_key": row["candidate_key"],
            "range_role": row.get("range_role", "primary"),
            "outcome_key": row["outcome_key"],
            "outcome_mode": row["outcome_mode"],
            "direction": row["direction"],
            "quality_tier": row["quality_tier"],
            "eligible_for_hypothesis": bool(row["eligible_for_hypothesis"]),
            "structured_evidence": evidence,
        })

    tier_order = {
        "replicated": 0,
        "exploratory_unreplicated": 1,
        "exploratory_screen": 2,
        "insufficient": 3,
    }

    def finding_rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
        evidence = item["structured_evidence"]
        testing = evidence.get("testing") if isinstance(evidence, Mapping) else None
        effect = evidence.get("effect") if isinstance(evidence, Mapping) else None
        q = testing.get("q") if isinstance(testing, Mapping) else None
        estimate = (
            effect.get("oriented_estimate")
            if isinstance(effect, Mapping) else None
        )
        return (
            tier_order.get(item["quality_tier"], 99),
            float(q) if isinstance(q, (int, float)) and not isinstance(q, bool) else math.inf,
            -abs(float(estimate))
            if isinstance(estimate, (int, float)) and not isinstance(estimate, bool)
            else math.inf,
            item["finding_id"],
        )

    findings.sort(key=finding_rank)
    transitions = sorted(
        (
            {
                "evaluation_id": row["id"], "hypothesis_id": row["hypothesis_id"],
                "previous_status": row["previous_status"], "status": row["status"],
                "change_reason": row["change_reason"],
                "evidence_fingerprint": row["evidence_fingerprint"],
                "evidence_class": row["evidence_class"],
                "sample_size": stored_json(row, "sample_size_json", dict),
                "effect_summary": stored_json(row, "effect_summary_json", dict),
                "stability": stored_json(row, "stability_json", dict),
                "evidence_for": stored_json(row, "evidence_for_json", list),
                "evidence_against": stored_json(
                    row, "evidence_against_json", list,
                ),
                "confounders": stored_json(row, "confounders_json", dict),
            }
            for row in evaluation_rows
            if row["transition_applied"]
        ),
        key=lambda item: (item["hypothesis_id"], item["evaluation_id"]),
    )
    readiness_gaps: dict[tuple[str, str], dict[str, Any]] = {}
    for row in run_rows:
        raw = row.get("result_json")
        if raw is None:
            continue
        result = stored_json(row, "result_json", dict)
        readiness = result.get("readiness")
        features = (
            readiness.get("features")
            if isinstance(readiness, Mapping) else None
        )
        if not isinstance(features, list):
            continue
        for feature in features:
            if not isinstance(feature, Mapping) or feature.get("state") == "sufficient":
                continue
            key = feature.get("feature_key")
            state = feature.get("state")
            if not isinstance(key, str) or not isinstance(state, str):
                continue
            readiness_gaps[(key, state)] = {
                field: feature.get(field)
                for field in (
                    "feature_key", "state", "aligned_n", "missing_rate",
                    "needed", "priority", "effort", "value",
                )
            }
    ranked_gaps = sorted(
        readiness_gaps.values(),
        key=lambda item: (
            -float(item["priority"])
            if isinstance(item["priority"], (int, float))
            and not isinstance(item["priority"], bool) else 0,
            item["feature_key"], item["state"],
        ),
    )
    missing_sources = [
        {"source": item["source"], "status": item["status"]}
        for item in freshness["sources"]
        if item["status"] in {"stale", "freshness_unknown"}
    ]
    base = {
        "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
        "cadence": cadence, "transitions": transitions, "findings": findings,
        "readiness_data_gaps": {
            "collector_states": missing_sources,
            "suppressed_dependencies": list(suppressed_dependencies),
            "ranked_feature_gaps": ranked_gaps,
        },
        "numeric_authority": "stored_structured_evidence_only",
    }
    if cadence == "monthly":
        by_candidate: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in findings:
            key = (
                item["candidate_key"], item["outcome_key"],
                item["outcome_mode"],
            )
            by_candidate.setdefault(key, {})[item["range_role"]] = item
        comparisons = [
            {
                "candidate_key": key[0],
                "outcome_key": key[1],
                "outcome_mode": key[2],
                "recent": windows.get(
                    "recent", missing("no_recent_eligible_finding"),
                ),
                "historical": windows.get(
                    "historical", missing("no_historical_eligible_finding"),
                ),
                "primary": windows.get(
                    "primary", missing("no_primary_eligible_finding"),
                ),
            }
            for key, windows in sorted(by_candidate.items())
        ]
        goal_findings = [
            item for item in findings if item["outcome_key"] not in BASE_OUTCOMES
        ]
        base["template"] = "monthly_deep_review_v1"
        base["slots"] = {
            "active_hypothesis_comparisons": (
                comparisons or missing("no_eligible_window_comparisons")
            ),
            "goal_progress_conflicts": (
                goal_findings or missing("no_eligible_goal_evidence")
            ),
            "regime_transitions": (
                transitions or missing("no_regime_transition")
            ),
            "highest_value_added_measurement": (
                ranked_gaps[0]
                if ranked_gaps
                else missing("no_ranked_readiness_gap")
            ),
        }
    else:
        green_findings = [
            item for item in findings
            if item["outcome_key"] == "subjective.day_rating"
        ]
        green_findings.sort(key=lambda item: (
            item["outcome_mode"] != "green-vs-non-green",
            finding_rank(item),
        ))
        goal_findings = [
            item for item in findings if item["outcome_key"] not in BASE_OUTCOMES
        ]
        selected = [
            item for item in (
                green_findings[0] if green_findings else None,
                goal_findings[0] if goal_findings else None,
            )
            if item is not None
        ]
        base["template"] = "weekly_or_trigger_v1"
        base["slots"] = {
            "what_changed": (
                transitions or missing("no_eligible_transition")
            ),
            "strongest_green_day_candidate": (
                green_findings[0]
                if green_findings
                else missing("no_eligible_green_day_candidate")
            ),
            "strongest_goal_candidate": (
                goal_findings[0]
                if goal_findings
                else missing("no_eligible_goal_candidate")
            ),
            "evidence_against_and_alternatives": (
                [
                    {
                        "finding_id": item["finding_id"],
                        "evidence_against": item["structured_evidence"].get(
                            "evidence_against", [],
                        ),
                        "confounders": item["structured_evidence"].get(
                            "confounders", {},
                        ),
                    }
                    for item in selected
                ]
                or missing("no_selected_candidate_evidence")
            ),
            "readiness_data_gaps": base["readiness_data_gaps"],
            "one_cheap_experiment": missing(
                "owner_or_model_proposal_required_no_automatic_experiment",
            ),
        }
    return base


__all__ = [
    "BASE_OUTCOMES", "CANON_TZ", "CONTEXT_VERSION", "TIMEZONE_NAME",
    "KNOWN_NOT_SENT_REASONS", "NOTIFICATION_CONTRACT_VERSION",
    "ORCHESTRATOR_CONTRACT_VERSION", "OrchestrationError",
    "RANGE_PLAN_VERSION", "RETRY_DELAYS_MINUTES", "TRIGGER_KINDS",
    "acknowledge_notification", "begin_notification_dispatch",
    "assert_trigger_lease",
    "cadence_plan", "canonical_timestamp", "claim_notification",
    "claim_trigger", "complete_trigger", "defer_quiet_hours",
    "enqueue_internal_trigger", "enqueue_notification", "enqueue_trigger",
    "fail_notification", "fail_trigger", "freshness_snapshot",
    "insight_run_status", "notification_dedupe_key", "novelty_decision",
    "outcome_modes", "parse_json_object", "range_objects",
    "renew_trigger", "resolve_notification", "sha256_id",
    "structured_slots", "suppress_stale_dependencies", "trigger_identity",
    "validate_notification", "validate_notification_fail",
    "validate_trigger_complete", "validate_trigger_enqueue",
    "validate_trigger_fail", "validate_trigger_lease",
]
