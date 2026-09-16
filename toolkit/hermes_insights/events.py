"""Validated, lossless event, completeness and alias capture primitives."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import math
import re
import sqlite3

from .normalize import ENTITY_TYPES, identity_key, normalized_label, valid_identity_key
from .settings import CANON_TZ, TIMEZONE_NAME


EVENT_CATEGORIES = {
    "social", "location", "activity", "travel", "illness", "stress", "food",
    "meal", "medication_change", "training_phase", "other",
}
EVENT_SOURCES = {"chat-panel", "chat-telegram", "manual", "import"}
CAPTURE_SURFACES = {"panel", "telegram", "ssh", "import"}
CAPTURE_STATUSES = {"needs_clarification", "not_health_event", "cancelled"}
COMPLETENESS_SCOPES = {
    "food_identity", "nutrition_total", "social", "location", "activity", "travel",
    "illness", "stress", "training_phase", "other_event", "medication", "supplement", "pain",
}
COMPLETENESS_STATES = {"complete", "partial", "unknown"}
MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
EVENT_TO_SCOPE = {
    "social": "social", "location": "location", "activity": "activity",
    "travel": "travel", "illness": "illness", "stress": "stress",
    "food": "food_identity", "meal": "food_identity",
    "medication_change": "medication", "training_phase": "training_phase",
    "other": "other_event",
}
CATEGORY_ENTITY_TYPE = {
    "social": "person", "location": "location", "activity": "activity",
    "food": "food", "meal": "food", "medication_change": "medication",
}
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_CAPTURE_ID = re.compile(r"^capture:[0-9a-f]{64}$")


class CaptureError(RuntimeError):
    """Controlled validation or state-transition failure."""

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


def _validation(message: str) -> None:
    raise CaptureError("validation_error", message, validation=True)


def exact_object(value: object, allowed: set[str], required: set[str], what: str) -> dict:
    """Validate a JSON object with ``additionalProperties=false`` semantics."""

    if not isinstance(value, dict):
        _validation(f"{what} must be one JSON object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        _validation(f"unknown {what} key(s): {', '.join(sorted(unknown))}")
    if missing:
        _validation(f"missing {what} key(s): {', '.join(sorted(missing))}")
    return value


def parse_json_stdin(text: str, *, max_bytes: int = 32_768) -> dict:
    """Parse one bounded UTF-8 JSON object, rejecting trailing documents."""

    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        _validation("stdin must be valid UTF-8")
    if not encoded or len(encoded) > max_bytes:
        _validation(f"stdin must contain 1-{max_bytes} UTF-8 bytes")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        _validation(f"stdin is not valid JSON: {exc.msg}")
    if not isinstance(value, dict):
        _validation("stdin must contain one JSON object")
    return value


def iso_date(value: object, field: str = "date") -> str:
    if not isinstance(value, str):
        _validation(f"{field} must be ISO YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _validation(f"{field} must be ISO YYYY-MM-DD")
    if parsed.isoformat() != value:
        _validation(f"{field} must be canonical ISO YYYY-MM-DD")
    return value


def hhmm(value: object, field: str = "time", *, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        _validation(f"{field} must be HH:MM")
    return value


def bounded_text(
    value: object, field: str, maximum: int, *, nullable: bool = True, preserve: bool = True
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        _validation(f"{field} must be text")
    if not value.strip():
        _validation(f"{field} must not be blank")
    if len(value) > maximum:
        _validation(f"{field} exceeds {maximum} characters")
    return value if preserve else value.strip()


def finite_number(value: object, field: str, *, nullable: bool = True) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _validation(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        _validation(f"{field} must be a finite number")
    return result


def bounded_int(value: object, field: str, low: int, high: int, *, nullable: bool = True) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _validation(f"{field} must be an integer between {low} and {high}")
    return value


def event_source(value: object) -> str:
    if value not in EVENT_SOURCES:
        _validation(f"source must be one of: {', '.join(sorted(EVENT_SOURCES))}")
    return str(value)


def validate_capture_id(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _CAPTURE_ID.fullmatch(value):
        _validation("capture_id is invalid")
    return value


def _capture_key(surface: str, client_event_id: str) -> str:
    material = json.dumps([surface, client_event_id], ensure_ascii=False, separators=(",", ":"))
    return "capture:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_capture_raw(payload: dict) -> dict:
    keys = {"client_event_id", "event_date", "event_time", "surface", "source", "raw_text"}
    exact_object(payload, keys, keys, "capture")
    client_id = bounded_text(payload["client_event_id"], "client_event_id", 128, nullable=False, preserve=False)
    if not _TOKEN.fullmatch(client_id):
        _validation("client_event_id must be a bounded code token")
    d = iso_date(payload["event_date"], "event_date")
    t = hhmm(payload["event_time"], "event_time")
    surface = payload["surface"]
    if surface not in CAPTURE_SURFACES:
        _validation(f"surface must be one of: {', '.join(sorted(CAPTURE_SURFACES))}")
    source = event_source(payload["source"])
    expected_source = {
        "panel": "chat-panel", "telegram": "chat-telegram",
        "ssh": "manual", "import": "import",
    }.get(surface)
    if expected_source and source != expected_source:
        _validation(f"surface {surface} requires source {expected_source}")
    raw = bounded_text(payload["raw_text"], "raw_text", 4000, nullable=False)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    capture_id = _capture_key(surface, client_id)
    return {"client_event_id": client_id, "event_date": d, "event_time": t,
            "surface": surface, "source": source, "raw_text": raw,
            "raw_text_sha256": digest, "capture_id": capture_id}


def capture_raw(c: sqlite3.Connection, payload: dict) -> dict:
    value = validate_capture_raw(payload)
    client_id, d, t = value["client_event_id"], value["event_date"], value["event_time"]
    surface, source, raw = value["surface"], value["source"], value["raw_text"]
    digest, capture_id = value["raw_text_sha256"], value["capture_id"]
    row = c.execute("SELECT * FROM raw_capture_entries WHERE surface=? AND client_event_id=?",
                    (surface, client_id)).fetchone()
    if row is not None:
        same = (row["capture_id"] == capture_id and row["event_date"] == d and row["event_time"] == t
                and row["source"] == source and row["raw_text_sha256"] == digest
                and row["raw_text"] == raw)
        if not same:
            raise CaptureError("idempotency_conflict", "client_event_id was already used with different content")
        latest = _latest_resolution(c, capture_id)
        return {"ok": True, "capture_id": capture_id, "status": latest["status"], "idempotent": True,
                "raw_text_sha256": digest}
    c.execute("""INSERT INTO raw_capture_entries(
        capture_id,client_event_id,event_date,event_time,surface,source,raw_text,raw_text_sha256)
        VALUES(?,?,?,?,?,?,?,?)""", (capture_id, client_id, d, t, surface, source, raw, digest))
    c.execute("""INSERT INTO raw_capture_resolutions(capture_id,status,source)
        VALUES(?,'pending',?)""", (capture_id, source))
    return {"ok": True, "capture_id": capture_id, "status": "pending", "idempotent": False,
            "raw_text_sha256": digest}


def _capture(c: sqlite3.Connection, capture_id: object) -> sqlite3.Row:
    validate_capture_id(capture_id)
    row = c.execute("SELECT * FROM raw_capture_entries WHERE capture_id=?", (capture_id,)).fetchone()
    if row is None:
        raise CaptureError("not_found", "capture_id does not exist")
    return row


def _latest_resolution(c: sqlite3.Connection, capture_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM raw_capture_resolutions WHERE capture_id=? ORDER BY id DESC LIMIT 1",
                    (capture_id,)).fetchone()
    if row is None:
        raise CaptureError("incompatible_schema", "capture has no resolution history")
    return row


def _append_resolution(
    c: sqlite3.Connection, capture_id: str, status: str, source: str, *,
    target_table: str | None = None, target_row_key: str | None = None,
    reason_code: str | None = None, supersedes_id: int | None = None,
) -> int:
    capture = _capture(c, capture_id)
    if capture["source"] != source:
        _validation("resolution source must match capture source")
    latest = _latest_resolution(c, capture_id)
    if supersedes_id is not None and supersedes_id != latest["id"]:
        raise CaptureError("stale_revision", "supersedes_id is not the latest capture resolution")
    if latest["status"] in {"linked", "cancelled", "not_health_event"}:
        raise CaptureError("invalid_state", f"capture is already {latest['status']}")
    c.execute("""INSERT INTO raw_capture_resolutions(
        capture_id,status,target_table,target_row_key,reason_code,source,supersedes_id)
        VALUES(?,?,?,?,?,?,?)""",
        (capture_id, status, target_table, target_row_key, reason_code, source, latest["id"]))
    return int(c.execute("SELECT last_insert_rowid()").fetchone()[0])


def validate_capture_resolve(payload: dict) -> dict:
    keys = {"capture_id", "status", "reason_code", "source", "supersedes_id"}
    exact_object(payload, keys, keys, "resolution")
    status = payload["status"]
    if status not in CAPTURE_STATUSES:
        _validation(f"status must be one of: {', '.join(sorted(CAPTURE_STATUSES))}")
    reason = payload["reason_code"]
    if reason is not None:
        reason = bounded_text(reason, "reason_code", 80, preserve=False)
        if not _TOKEN.fullmatch(reason):
            _validation("reason_code must be a bounded code token")
    supersedes = bounded_int(payload["supersedes_id"], "supersedes_id", 1, 2_147_483_647, nullable=False)
    source = event_source(payload["source"])
    capture_id = payload["capture_id"]
    if not isinstance(capture_id, str) or not _CAPTURE_ID.fullmatch(capture_id):
        _validation("capture_id is invalid")
    return {"capture_id": capture_id, "status": status, "reason_code": reason,
            "source": source, "supersedes_id": supersedes}


def capture_resolve(c: sqlite3.Connection, payload: dict) -> dict:
    value = validate_capture_resolve(payload)
    row_id = _append_resolution(c, value["capture_id"], value["status"], value["source"],
                                reason_code=value["reason_code"],
                                supersedes_id=value["supersedes_id"])
    return {"ok": True, "capture_id": value["capture_id"], "resolution_id": row_id,
            "status": value["status"]}


EVENT_FIELDS = {
    "date", "time", "category", "entity_label", "value_text", "value_num", "unit",
    "duration_min", "intensity", "valence", "source", "capture_id", "note",
}


def validate_event(payload: dict, *, correction: bool = False) -> dict:
    allowed = EVENT_FIELDS | ({"reason"} if correction else set())
    required = {"date", "category", "source"} | ({"capture_id", "reason"} if correction else set())
    exact_object(payload, allowed, required, "event")
    category = payload["category"]
    if category not in EVENT_CATEGORIES:
        _validation(f"category must be one of: {', '.join(sorted(EVENT_CATEGORIES))}")
    label = payload.get("entity_label")
    if label is not None:
        label = bounded_text(label, "entity_label", 300, nullable=False)
        if not normalized_label(label):
            _validation("entity_label must not be blank")
    result = {
        "date": iso_date(payload["date"]), "time": hhmm(payload.get("time")),
        "category": category, "entity_label": label,
        "value_text": bounded_text(payload.get("value_text"), "value_text", 1000),
        "value_num": finite_number(payload.get("value_num"), "value_num"),
        "unit": bounded_text(payload.get("unit"), "unit", 40),
        "duration_min": bounded_int(payload.get("duration_min"), "duration_min", 0, 10080),
        "intensity": bounded_int(payload.get("intensity"), "intensity", 1, 5),
        "valence": bounded_int(payload.get("valence"), "valence", -2, 2),
        "source": event_source(payload["source"]),
        "capture_id": payload.get("capture_id"),
        "note": bounded_text(payload.get("note"), "note", 2000),
    }
    if correction:
        result["reason"] = bounded_text(payload["reason"], "reason", 500, nullable=False)
        if result["capture_id"] is None:
            _validation("capture_id is required for event correction")
    elif result["source"] in {"chat-panel", "chat-telegram"} and result["capture_id"] is None:
        _validation("conversational events require capture_id")
    validate_capture_id(result["capture_id"], nullable=True)
    return result


def resolve_identity(
    c: sqlite3.Connection, entity_type: str, label: str | None,
) -> tuple[str | None, int | None]:
    if label is None:
        return None, None
    alias_key = identity_key(entity_type, label)
    alias = c.execute("""SELECT id,canonical_key,active FROM entity_aliases
        WHERE entity_type=? AND alias_key=? ORDER BY id DESC LIMIT 1""",
        (entity_type, alias_key)).fetchone()
    if alias is not None and alias["active"] == 1:
        return alias["canonical_key"], int(alias["id"])
    return alias_key, int(alias["id"]) if alias is not None else None


def pain_identity_key(region: str, side: str) -> str:
    return identity_key("other", f"pain {region} {side}")


def invalidate_explicit_none(
    c: sqlite3.Connection, event_date: str, scope: str, entity_key_value: str | None,
    source: str, capture_id: str | None,
) -> list[int]:
    rows = c.execute("""SELECT * FROM capture_completeness_revisions r
        WHERE r.date=? AND r.scope=? AND r.explicit_none=1
          AND (r.entity_key IS NULL OR r.entity_key IS ?)
          AND NOT EXISTS (SELECT 1 FROM capture_completeness_revisions n WHERE n.supersedes_id=r.id)
        ORDER BY r.id""", (event_date, scope, entity_key_value)).fetchall()
    appended = []
    for row in rows:
        # Legacy structured writers have older source vocabularies (for
        # example ``chat`` and ``panel-ui``).  Completeness provenance has the
        # narrower Phase 2 enum, so retain the superseded attestation source
        # when the conflicting writer has no representable Phase 2 source.
        revision_source = source if source in EVENT_SOURCES else row["source"]
        c.execute("""INSERT INTO capture_completeness_revisions(
            date,scope,entity_key,state,explicit_none,source,capture_id,note,supersedes_id)
            VALUES(?,?,?,'partial',0,?,?,?,?)""",
            (row["date"], row["scope"], row["entity_key"], revision_source, capture_id,
             "structured_event_conflict", row["id"]))
        appended.append(int(c.execute("SELECT last_insert_rowid()").fetchone()[0]))
    return appended


def _insert_event(c: sqlite3.Connection, event: dict, *, supersedes_id: int | None = None) -> tuple[int, str | None, int | None]:
    entity_type = CATEGORY_ENTITY_TYPE.get(event["category"], "other")
    entity_key_value, alias_revision = resolve_identity(c, entity_type, event["entity_label"])
    raw_text = raw_ref = None
    if event["capture_id"] is not None:
        capture = _capture(c, event["capture_id"])
        if capture["source"] != event["source"]:
            _validation("event source must match capture source")
        if capture["event_date"] != event["date"]:
            _validation("event date must match capture date")
        if capture["event_time"] is not None and capture["event_time"] != event["time"]:
            _validation("event time must match non-null capture time")
        raw_text, raw_ref = capture["raw_text"], capture["capture_id"]
    c.execute("""INSERT INTO event_exposures(
        date,time,category,entity_label,entity_key,value_text,value_num,unit,duration_min,
        intensity,valence,source,raw_text,raw_ref,note,supersedes_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (event["date"], event["time"], event["category"], event["entity_label"],
         entity_key_value, event["value_text"], event["value_num"], event["unit"],
         event["duration_min"], event["intensity"], event["valence"], event["source"],
         raw_text, raw_ref, event["note"], supersedes_id))
    event_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
    invalidate_explicit_none(
        c, event["date"], EVENT_TO_SCOPE[event["category"]], entity_key_value,
        event["source"], event["capture_id"],
    )
    if event["capture_id"] is not None:
        _append_resolution(c, event["capture_id"], "linked", event["source"],
                           target_table="event_exposures", target_row_key=str(event_id))
    return event_id, entity_key_value, alias_revision


def event_log(c: sqlite3.Connection, payload: dict) -> dict:
    event = validate_event(payload)
    event_id, key, alias_revision = _insert_event(c, event)
    return {"ok": True, "event_id": event_id, "date": event["date"],
            "category": event["category"], "entity_label": event["entity_label"],
            "entity_key": key, "alias_revision_id": alias_revision}


def event_correct(c: sqlite3.Connection, event_id: int, payload: dict) -> dict:
    event = validate_event(payload, correction=True)
    prior = c.execute("SELECT * FROM event_exposures WHERE id=?", (event_id,)).fetchone()
    if prior is None:
        raise CaptureError("not_found", "event does not exist")
    if prior["voided"]:
        raise CaptureError("invalid_state", "event is already voided")
    if c.execute("SELECT 1 FROM event_exposures WHERE supersedes_id=?", (event_id,)).fetchone():
        raise CaptureError("invalid_state", "event has already been superseded")
    replacement_id, key, alias_revision = _insert_event(c, event, supersedes_id=event_id)
    c.execute("UPDATE event_exposures SET voided=1,void_reason=? WHERE id=?",
              (event["reason"], event_id))
    return {"ok": True, "corrected_event_id": event_id, "replacement_event_id": replacement_id,
            "entity_label": event["entity_label"], "entity_key": key,
            "alias_revision_id": alias_revision}


def event_void(c: sqlite3.Connection, event_id: int, reason: str) -> dict:
    reason = bounded_text(reason, "reason", 500, nullable=False)
    row = c.execute("SELECT voided FROM event_exposures WHERE id=?", (event_id,)).fetchone()
    if row is None:
        raise CaptureError("not_found", "event does not exist")
    if row["voided"]:
        raise CaptureError("invalid_state", "event is already voided")
    c.execute("UPDATE event_exposures SET voided=1,void_reason=? WHERE id=?", (reason, event_id))
    return {"ok": True, "event_id": event_id, "voided": True}


def resolve_range(
    *, from_date: str | None, to_date: str | None, days: int | None, all_dates: bool,
) -> tuple[str | None, str | None, str]:
    forms = int(from_date is not None or to_date is not None) + int(days is not None) + int(all_dates)
    if forms == 0:
        days = 365
        forms = 1
    if forms != 1:
        _validation("use exactly one of --from/--to, --days, or --all")
    if from_date is not None or to_date is not None:
        if from_date is None or to_date is None:
            _validation("--from and --to are required together")
        lo, hi = iso_date(from_date, "--from"), iso_date(to_date, "--to")
        if lo > hi:
            _validation("--from must not follow --to")
        return lo, hi, "bounded"
    if days is not None:
        if isinstance(days, bool) or not 1 <= days <= 36500:
            _validation("--days must be between 1 and 36500")
        hi_date = datetime.now(CANON_TZ).date()
        return (hi_date - timedelta(days=days - 1)).isoformat(), hi_date.isoformat(), "days"
    return None, None, "all"


def list_events(
    c: sqlite3.Connection, *, from_date: str | None = None, to_date: str | None = None,
    days: int | None = None, all_dates: bool = False, category: str | None = None,
    entity_key_value: str | None = None,
) -> dict:
    lo, hi, kind = resolve_range(from_date=from_date, to_date=to_date, days=days, all_dates=all_dates)
    if category is not None and category not in EVENT_CATEGORIES:
        _validation("invalid category")
    if entity_key_value is not None and not valid_identity_key(entity_key_value):
        _validation("invalid entity key")
    where, params = ["voided=0"], []
    if lo is not None:
        where.extend(["date>=?", "date<=?"]); params.extend([lo, hi])
    if category is not None:
        where.append("category=?"); params.append(category)
    if entity_key_value is not None:
        where.append("entity_key=?"); params.append(entity_key_value)
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM event_exposures WHERE " + " AND ".join(where) + " ORDER BY date,time,id",
        params,
    )]
    counts = {r["category"]: r["n"] for r in c.execute(
        "SELECT category,COUNT(*) n FROM event_exposures WHERE " + " AND ".join(where) + " GROUP BY category ORDER BY category",
        params,
    )}
    return {"ok": True, "meta": {"range_kind": kind, "from": lo, "to": hi,
                                    "timezone": TIMEZONE_NAME},
            "coverage": {"active_count": len(rows), "by_category": counts}, "events": rows}


def _latest_completeness(c: sqlite3.Connection, d: str, scope: str, entity: str | None) -> sqlite3.Row | None:
    return c.execute("""SELECT * FROM capture_completeness_revisions
        WHERE date=? AND scope=? AND entity_key IS ? ORDER BY id DESC LIMIT 1""",
        (d, scope, entity)).fetchone()


def _structured_count(c: sqlite3.Connection, d: str, scope: str, entity: str | None) -> int:
    categories = [k for k, value in EVENT_TO_SCOPE.items() if value == scope]
    count = 0
    if categories:
        marks = ",".join("?" for _ in categories)
        sql = f"SELECT COUNT(*) FROM event_exposures WHERE voided=0 AND date=? AND category IN ({marks})"
        params: list[object] = [d, *categories]
        if entity is not None:
            sql += " AND entity_key=?"; params.append(entity)
        count += int(c.execute(sql, params).fetchone()[0])
    if scope == "food_identity":
        rows = c.execute("SELECT recipe_id,food_name FROM nutrition_log WHERE date=?", (d,)).fetchall()
        if entity is None:
            count += len(rows)
        else:
            for row in rows:
                key = f"recipe:{row['recipe_id']}" if row["recipe_id"] else (
                    resolve_identity(c, "food", row["food_name"])[0]
                    if row["food_name"] else None)
                count += int(key == entity)
    elif scope == "nutrition_total":
        count += int(c.execute(
            "SELECT COUNT(*) FROM nutrition_log WHERE date=?", (d,)
        ).fetchone()[0])
        count += int(c.execute(
            "SELECT COUNT(*) FROM nutrient_daily WHERE date=?", (d,)
        ).fetchone()[0])
    elif scope == "supplement":
        rows = c.execute("""SELECT p.name FROM supplements_log l
            JOIN supplement_products p ON p.supplement_id=l.supplement_id
            WHERE l.date=?""", (d,)).fetchall()
        count += len(rows) if entity is None else sum(
            resolve_identity(c, "supplement", row["name"])[0] == entity for row in rows)
    elif scope == "medication":
        rows = c.execute("SELECT drug FROM meds_log WHERE date=?", (d,)).fetchall()
        count += len(rows) if entity is None else sum(
            resolve_identity(c, "medication", row["drug"])[0] == entity for row in rows)
    elif scope == "pain":
        rows = c.execute(
            "SELECT region,side FROM pain_log WHERE date=? AND voided=0", (d,)
        ).fetchall()
        count += len(rows) if entity is None else sum(
            pain_identity_key(row["region"], row["side"]) == entity for row in rows)
    return count


def completeness_set(
    c: sqlite3.Connection, *, event_date: str, scope: str, state: str,
    explicit_none: bool, entity_key_value: str | None, source: str,
    capture_id: str | None, note: str | None,
) -> dict:
    validated = validate_completeness_input(
        event_date=event_date, scope=scope, state=state, explicit_none=explicit_none,
        entity_key_value=entity_key_value, source=source, capture_id=capture_id, note=note,
    )
    d, scope, state = validated["date"], validated["scope"], validated["state"]
    explicit_none, entity_key_value = validated["explicit_none"], validated["entity_key"]
    source, capture_id, note = validated["source"], validated["capture_id"], validated["note"]
    if capture_id is not None:
        capture = _capture(c, capture_id)
        if capture["source"] != source:
            _validation("completeness source must match capture source")
    observations = _structured_count(c, d, scope, entity_key_value)
    if explicit_none and observations:
        raise CaptureError("conflicting_observation", "explicit-none conflicts with structured observations")
    latest = _latest_completeness(c, d, scope, entity_key_value)
    c.execute("""INSERT INTO capture_completeness_revisions(
        date,scope,entity_key,state,explicit_none,source,capture_id,note,supersedes_id)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (d, scope, entity_key_value, state, int(explicit_none), source, capture_id, note,
         latest["id"] if latest else None))
    revision_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {"ok": True, "revision_id": revision_id, "date": d, "scope": scope,
            "entity_key": entity_key_value, "state": state, "explicit_none": bool(explicit_none),
            "observation_count": observations,
            "structural_zero_eligible": state == "complete"}


def validate_completeness_input(
    *, event_date: str, scope: str, state: str, explicit_none: bool,
    entity_key_value: str | None, source: str, capture_id: str | None,
    note: str | None,
) -> dict:
    d = iso_date(event_date)
    if scope not in COMPLETENESS_SCOPES:
        _validation("invalid completeness scope")
    if state not in COMPLETENESS_STATES:
        _validation("invalid completeness state")
    if explicit_none and state != "complete":
        _validation("--explicit-none requires state complete")
    if entity_key_value is not None and not valid_identity_key(entity_key_value):
        _validation("invalid entity key")
    source = event_source(source)
    note = bounded_text(note, "note", 1000)
    validate_capture_id(capture_id, nullable=True)
    return {"date": d, "scope": scope, "state": state, "explicit_none": explicit_none,
            "entity_key": entity_key_value, "source": source,
            "capture_id": capture_id, "note": note}


def completeness_read(
    c: sqlite3.Connection, *, from_date: str | None = None, to_date: str | None = None,
    days: int | None = None, all_dates: bool = False, scope: str | None = None,
) -> dict:
    lo, hi, kind = resolve_range(from_date=from_date, to_date=to_date, days=days, all_dates=all_dates)
    if scope is not None and scope not in COMPLETENESS_SCOPES:
        _validation("invalid completeness scope")
    where, params = [], []
    if lo is not None:
        where.extend(["r.date>=?", "r.date<=?"]); params.extend([lo, hi])
    if scope is not None:
        where.append("r.scope=?"); params.append(scope)
    sql = """SELECT r.* FROM capture_completeness_revisions r
        WHERE NOT EXISTS (SELECT 1 FROM capture_completeness_revisions n WHERE n.supersedes_id=r.id)"""
    if where:
        sql += " AND " + " AND ".join(where)
    sql += " ORDER BY r.date,r.scope,r.entity_key,r.id"
    items = []
    for row in c.execute(sql, params):
        item = dict(row)
        observations = _structured_count(c, row["date"], row["scope"], row["entity_key"])
        item["observation_count"] = observations
        item["structural_zero_eligible"] = row["state"] == "complete"
        items.append(item)
    return {"ok": True, "meta": {"range_kind": kind, "from": lo, "to": hi,
                                    "timezone": TIMEZONE_NAME},
            "effective": items, "count": len(items)}


def alias_set(
    c: sqlite3.Connection, entity_type: str, alias_label: str,
    canonical_key: str, canonical_label: str,
) -> dict:
    entity_type, alias_label, canonical_key, canonical_label, alias_key = \
        validate_alias_set(entity_type, alias_label, canonical_key, canonical_label)
    latest = c.execute("""SELECT * FROM entity_aliases WHERE entity_type=? AND alias_key=?
        ORDER BY id DESC LIMIT 1""", (entity_type, alias_key)).fetchone()
    if latest is not None and latest["active"] == 1 \
            and latest["canonical_key"] == canonical_key \
            and latest["canonical_label"] == canonical_label:
        return {"ok": True, "revision_id": latest["id"], "idempotent": True,
                "entity_type": entity_type, "alias_key": alias_key,
                "canonical_key": canonical_key, "canonical_label": canonical_label}
    c.execute("""INSERT INTO entity_aliases(
        entity_type,alias_key,canonical_key,canonical_label,source,active,supersedes_id)
        VALUES(?,?,?,?, 'owner',1,?)""",
        (entity_type, alias_key, canonical_key, canonical_label, latest["id"] if latest else None))
    revision_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {"ok": True, "revision_id": revision_id, "idempotent": False,
            "entity_type": entity_type, "alias_key": alias_key,
            "canonical_key": canonical_key, "canonical_label": canonical_label}


def validate_alias_set(
    entity_type: str, alias_label: str, canonical_key: str, canonical_label: str,
) -> tuple[str, str, str, str, str]:
    if entity_type not in ENTITY_TYPES:
        _validation("invalid entity type")
    alias_label = bounded_text(alias_label, "alias", 300, nullable=False)
    canonical_label = bounded_text(canonical_label, "label", 300, nullable=False)
    alias_key = identity_key(entity_type, alias_label)
    if not valid_identity_key(canonical_key, entity_type):
        _validation("canonical key does not match entity type")
    return entity_type, alias_label, canonical_key, canonical_label, alias_key


def alias_retire(c: sqlite3.Connection, entity_type: str, alias_label: str) -> dict:
    entity_type, alias_label, alias_key = validate_alias_retire(entity_type, alias_label)
    latest = c.execute("""SELECT * FROM entity_aliases WHERE entity_type=? AND alias_key=?
        ORDER BY id DESC LIMIT 1""", (entity_type, alias_key)).fetchone()
    if latest is None:
        raise CaptureError("not_found", "alias does not exist")
    if latest["active"] == 0:
        raise CaptureError("invalid_state", "alias is already retired")
    c.execute("""INSERT INTO entity_aliases(
        entity_type,alias_key,canonical_key,canonical_label,source,active,supersedes_id)
        VALUES(?,?,?,?, 'owner',0,?)""",
        (entity_type, alias_key, latest["canonical_key"], latest["canonical_label"], latest["id"]))
    revision_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {"ok": True, "revision_id": revision_id, "entity_type": entity_type,
            "alias_key": alias_key, "active": False}


def validate_alias_retire(entity_type: str, alias_label: str) -> tuple[str, str, str]:
    if entity_type not in ENTITY_TYPES:
        _validation("invalid entity type")
    alias_label = bounded_text(alias_label, "alias", 300, nullable=False)
    alias_key = identity_key(entity_type, alias_label)
    return entity_type, alias_label, alias_key


def alias_history(c: sqlite3.Connection, entity_type: str, alias_label: str) -> dict:
    if entity_type not in ENTITY_TYPES:
        _validation("invalid entity type")
    alias_label = bounded_text(alias_label, "alias", 300, nullable=False)
    alias_key = identity_key(entity_type, alias_label)
    rows = [dict(r) for r in c.execute("""SELECT * FROM entity_aliases
        WHERE entity_type=? AND alias_key=? ORDER BY id""", (entity_type, alias_key))]
    return {"ok": True, "entity_type": entity_type, "alias_key": alias_key,
            "original_alias_label": alias_label, "history": rows}


def link_capture(
    c: sqlite3.Connection, capture_id: str | None, source: str,
    target_table: str, target_row_key: str, *, event_date: str | None = None,
    event_time: str | None = None, check_time: bool = False,
) -> None:
    """Link another validated writer to raw capture in the same transaction."""

    if capture_id is not None:
        capture = _capture(c, capture_id)
        if event_date is not None and capture["event_date"] != event_date:
            _validation("structured date must match capture event_date")
        if check_time and capture["event_time"] is not None and capture["event_time"] != event_time:
            _validation("structured time must match capture event_time")
        _append_resolution(c, capture_id, "linked", source,
                           target_table=target_table, target_row_key=target_row_key)
