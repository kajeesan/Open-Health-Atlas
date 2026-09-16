"""Owner-only, scoped panel conversations with legacy compatibility adapters.

Conversation identity, Hermes continuation IDs, lens, range and body regions
are server-resolved. A short SQLite lease serializes one continuation without
holding a database lock while Hermes is running.
"""
import json
import re
import secrets
import time
import uuid

from flask import Blueprint, current_app, jsonify, request

from app import auth, bridge, limiter
from app.canon import CanonicalRangeError, canonical_range
from app.panel_db import LEGACY_CONVERSATIONS, get_db

bp = Blueprint("chat", __name__, url_prefix="/api/chat")

MAX_MESSAGE = 4000
MAX_EDITED_TITLE = 120
MAX_DEFAULT_TITLE = 80
MAX_ACTIVE_CONVERSATIONS = 50
MAX_SELECTED_FINDINGS = 3
MAX_TOOL_RECEIPTS = 16
MAX_EVIDENCE_REFS = 32
MAX_TOOL_EVIDENCE_IDS = 256
LEASE_SECONDS = 130
LENSES = {"general", "pain", "mobility"}
REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
CONVERSATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DELIVERY_STATES = {"pending", "complete", "failed", "uncertain", "legacy"}

THREAD_SESSIONS = {
    thread: values[2] for thread, values in LEGACY_CONVERSATIONS.items()
}
THREAD_CONVERSATIONS = {
    thread: values[0] for thread, values in LEGACY_CONVERSATIONS.items()
}


def _legacy_reference_enabled():
    return (
        current_app.testing
        and current_app.config.get(
            "ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES"
        ) is True
    )


def _legacy_route_quarantined():
    return jsonify(
        ok=False,
        error={
            "code": "legacy_narrative_route_quarantined",
            "message": "Use the scoped conversation API.",
        },
    ), 410


def _ensure_legacy_conversation(thread):
    """Create a legacy adapter shell only when that adapter is first used.

    Fresh installations therefore remain empty, while the retained `/send`
    and `/history` compatibility surface remains functional.
    """
    conversation_id, title, session_id = LEGACY_CONVERSATIONS[thread]
    db = get_db()
    if db.execute(
        "SELECT 1 FROM chat_conversations WHERE id=?", (conversation_id,)
    ).fetchone() is not None:
        return conversation_id
    public_context = {
        "version": 1,
        "range": {"kind": "all"},
        "selected_region_ids": [],
    }
    try:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        now_ms = _next_updated_at(db)
        source = _canonical_json(
            _trusted_context(thread, conversation_id, public_context)
        )
        db.execute(
            """INSERT OR IGNORE INTO chat_conversations(
                 id,title,surface,lens,selected_regions_json,range_kind,
                 range_from,range_to,archived_at,hermes_session_id,
                 source_context_json,created_at,updated_at,busy_turn_id,busy_until
               ) VALUES(?,?,'panel',?,'[]','all',NULL,NULL,NULL,?,?,?, ?,NULL,NULL)""",
            (conversation_id, title, thread, session_id, source, now_ms, now_ms),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return conversation_id


class RequestContractError(ValueError):
    """A closed request contract was not satisfied."""


def _owner_key():
    row = auth.current_session()
    return f"owner:{row['token_hash']}" if row is not None else "owner:unauthenticated"


def _send_rate_key():
    """Apply the send budget independently to each server-bound session."""
    owner = _owner_key()
    conversation_id = (request.view_args or {}).get("conversation_id")
    if conversation_id is not None:
        return f"{owner}:conversation:{conversation_id}"
    body = request.get_json(silent=True)
    thread = _thread(body.get("thread")) if isinstance(body, dict) else None
    return f"{owner}:legacy:{thread or 'invalid'}"


def _error(message, status=400, *, code="invalid_request"):
    return jsonify(ok=False, error=message, code=code), status


def _strict_json():
    raw = request.get_data(cache=True)
    if not raw:
        raise RequestContractError("A JSON body is required.")

    def closed_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RequestContractError(f"Duplicate field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=closed_pairs)
    except RequestContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestContractError("The request body is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise RequestContractError("The request body must be an object.")
    return value


def _closed_keys(value, *, allowed, required=()):
    extra = set(value) - set(allowed)
    missing = set(required) - set(value)
    if extra:
        raise RequestContractError(
            "Unexpected field" + ("s" if len(extra) != 1 else "") + ": "
            + ", ".join(sorted(extra))
        )
    if missing:
        raise RequestContractError(
            "Missing field" + ("s" if len(missing) != 1 else "") + ": "
            + ", ".join(sorted(missing))
        )


def _query(*, allowed):
    values = {}
    extra = set(request.args) - set(allowed)
    if extra:
        raise RequestContractError("Unexpected query field: " + sorted(extra)[0])
    for key, items in request.args.lists():
        if len(items) != 1:
            raise RequestContractError(f"{key} may appear only once.")
        values[key] = items[0]
    return values


def _bounded_int(value, *, field, default=None, low, high):
    if value is None:
        if default is None:
            return None
        return default
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise RequestContractError(f"{field} must be a whole number.")
    parsed = int(value)
    if parsed < low or parsed > high:
        raise RequestContractError(f"{field} must be between {low} and {high}.")
    return parsed


def _thread(value):
    value = (value or "general").strip() if isinstance(value, str) else ""
    return value if value in THREAD_CONVERSATIONS else None


def _conversation_id(value):
    if value in {item[0] for item in LEGACY_CONVERSATIONS.values()}:
        return value
    return value if isinstance(value, str) and CONVERSATION_ID_RE.fullmatch(value) else None


def _turn_id(value):
    if not isinstance(value, str):
        raise RequestContractError("turn_id must be a lower-case UUID.")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise RequestContractError("turn_id must be a lower-case UUID.") from exc
    if parsed.version is None or str(parsed) != value:
        raise RequestContractError("turn_id must be a lower-case UUID.")
    return value


def _title(value, *, optional=True):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise RequestContractError("The title must be text.")
    collapsed = " ".join(value.split())
    if not collapsed:
        raise RequestContractError("The title cannot be empty.")
    if len(collapsed) > MAX_EDITED_TITLE:
        raise RequestContractError("The title must be 120 characters or fewer.")
    return collapsed


def _default_title(message):
    return " ".join(message.split())[:MAX_DEFAULT_TITLE]


def _public_context(value, *, optional=False, default=False):
    if value is None and optional:
        return None
    if value is None and default:
        value = {"version": 1, "range": {"kind": "all"}, "selected_region_ids": []}
    if value is None:
        raise RequestContractError("Context must be an object.")
    if not isinstance(value, dict):
        raise RequestContractError("Context must be an object.")
    _closed_keys(
        value,
        allowed={"version", "range", "selected_region_ids"},
        required={"version", "range", "selected_region_ids"},
    )
    if type(value["version"]) is not int or value["version"] != 1:
        raise RequestContractError("The context version is not supported.")
    try:
        range_value = canonical_range(value["range"])
    except CanonicalRangeError as exc:
        raise RequestContractError(str(exc)) from exc
    regions = value["selected_region_ids"]
    if not isinstance(regions, list) or len(regions) > 20:
        raise RequestContractError("Choose no more than 20 body regions.")
    if any(not isinstance(item, str) or not REGION_RE.fullmatch(item) for item in regions):
        raise RequestContractError("One or more body regions are not recognized.")
    if len(set(regions)) != len(regions):
        raise RequestContractError("Each body region may be selected only once.")
    return {
        "version": 1,
        "range": range_value,
        "selected_region_ids": list(regions),
    }


def _selected_findings(value, *, optional=True):
    if value is None and optional:
        return []
    if not isinstance(value, list) or len(value) > MAX_SELECTED_FINDINGS:
        raise RequestContractError("Choose no more than three findings.")
    selected = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise RequestContractError("Each selected finding must be an object.")
        _closed_keys(
            item,
            allowed={"outcome", "finding_id", "input_fingerprint"},
            required={"outcome", "finding_id", "input_fingerprint"},
        )
        if item["outcome"] != "subjective.day_rating":
            raise RequestContractError("Only day-rating findings can be selected here.")
        finding_id = item["finding_id"]
        fingerprint = item["input_fingerprint"]
        if not isinstance(finding_id, str) or SHA256_ID_RE.fullmatch(finding_id) is None:
            raise RequestContractError("A selected finding identifier is invalid.")
        if not isinstance(fingerprint, str) or SHA256_ID_RE.fullmatch(fingerprint) is None:
            raise RequestContractError("A selected finding fingerprint is invalid.")
        if finding_id in seen:
            raise RequestContractError("Each finding may be selected only once.")
        seen.add(finding_id)
        selected.append({
            "outcome": item["outcome"],
            "finding_id": finding_id,
            "input_fingerprint": fingerprint,
        })
    return selected


def _trusted_context(lens, conversation_id, public_context, selected_findings=None):
    selected_findings = list(selected_findings or [])
    context = {
        "version": 1,
        "surface": "panel",
        "lens": lens,
        "conversation_id": conversation_id,
        "range": public_context["range"],
        "selected_region_ids": public_context["selected_region_ids"],
        "evidence_contract": "health-tool-v1",
    }
    if selected_findings:
        context.update({
            "version": 2,
            "selected_findings": selected_findings,
            "evidence_contract": "health-tool-v2",
        })
    return context


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _context_from_row(row):
    range_value = {"kind": row["range_kind"]}
    if row["range_kind"] == "bounded":
        range_value.update({"from": row["range_from"], "to": row["range_to"]})
    return {
        "version": 1,
        "range": range_value,
        "selected_region_ids": json.loads(row["selected_regions_json"]),
    }


def _row_context_values(lens, conversation_id, public_context):
    range_value = public_context["range"]
    trusted = _trusted_context(lens, conversation_id, public_context)
    return (
        _canonical_json(public_context["selected_region_ids"]),
        range_value["kind"],
        range_value.get("from"),
        range_value.get("to"),
        _canonical_json(trusted),
    )


def _now_ms():
    return time.time_ns() // 1_000_000


def _next_updated_at(db, now_ms=None):
    now_ms = _now_ms() if now_ms is None else now_ms
    row = db.execute("SELECT MAX(updated_at) FROM chat_conversations").fetchone()
    maximum = row[0] if row and row[0] is not None else 0
    return max(now_ms, maximum + 1)


def _message_json(row):
    context = None
    if row["context_json"]:
        try:
            context = json.loads(row["context_json"])
        except (TypeError, ValueError):
            context = None
    evidence = None
    if "evidence_json" in row.keys() and row["evidence_json"]:
        try:
            evidence = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            evidence = None
    return {
        "id": row["id"],
        "ts": row["ts"],
        "role": row["role"],
        "content": row["content"],
        "turn_id": row["turn_id"],
        "context": context,
        "evidence": evidence,
        "delivery_status": row["delivery_status"],
    }


def _selection_record(range_value, selected_findings):
    if not selected_findings:
        return None
    return {
        "contract": "openhealthatlas-chat-evidence-v1",
        "kind": "selection",
        "range": range_value,
        "selected_findings": selected_findings,
    }


def _finding_replay(command, range_value):
    if command[2] != "finding-evidence":
        return None
    args = command[3:]
    if len(args) % 2:
        raise RequestContractError("Hermes returned a non-canonical evidence replay.")
    flags = {}
    for index in range(0, len(args), 2):
        flag, value = args[index:index + 2]
        if not isinstance(flag, str) or not isinstance(value, str) or flag in flags:
            raise RequestContractError("Hermes returned a non-canonical evidence replay.")
        flags[flag] = value
    if range_value.get("kind") != "bounded" or set(flags) != {
        "--outcome", "--finding-id", "--input-fingerprint", "--from", "--to",
    }:
        raise RequestContractError("Hermes returned an out-of-scope evidence replay.")
    if (
        flags["--outcome"] != "subjective.day_rating"
        or SHA256_ID_RE.fullmatch(flags["--finding-id"]) is None
        or SHA256_ID_RE.fullmatch(flags["--input-fingerprint"]) is None
        or flags["--from"] != range_value["from"]
        or flags["--to"] != range_value["to"]
    ):
        raise RequestContractError("Hermes returned an out-of-scope evidence replay.")
    return {
        "outcome": flags["--outcome"],
        "finding_id": flags["--finding-id"],
        "input_fingerprint": flags["--input-fingerprint"],
    }


def _normalized_evidence_receipt(result, range_value, selected_findings):
    trace = result.get("openhealthatlas_tool_trace")
    refs = result.get("openhealthatlas_evidence_refs")
    if trace is None and refs is None:
        if selected_findings:
            raise RequestContractError("Hermes returned no OpenHealthAtlas evidence receipt.")
        return None
    if trace == [] and refs == []:
        if selected_findings:
            raise RequestContractError("Hermes returned no OpenHealthAtlas evidence receipt.")
        return None
    if (
        not isinstance(trace, list)
        or not 1 <= len(trace) <= MAX_TOOL_RECEIPTS
        or not isinstance(refs, list)
        or len(refs) > MAX_EVIDENCE_REFS
        or any(not isinstance(item, str) or SHA256_ID_RE.fullmatch(item) is None for item in refs)
        or len(set(refs)) != len(refs)
    ):
        raise RequestContractError("Hermes returned an invalid OpenHealthAtlas evidence receipt.")

    receipts = []
    replayed_findings = []
    replayed_finding_receipts = []
    authorized_evidence_ids = set()
    fixtures = set()
    data_classes = set()
    for item in trace:
        if not isinstance(item, dict):
            raise RequestContractError("Hermes returned an invalid OpenHealthAtlas tool receipt.")
        command = item.get("command")
        evidence_ids = item.get("evidence_ids")
        result_sha256 = item.get("result_sha256")
        if (
            not isinstance(command, list)
            or len(command) < 3
            or not isinstance(command[2], str)
            or not isinstance(evidence_ids, list)
            or len(evidence_ids) > MAX_TOOL_EVIDENCE_IDS
            or any(
                not isinstance(value, str) or SHA256_ID_RE.fullmatch(value) is None
                for value in evidence_ids
            )
            or not isinstance(result_sha256, str)
            or SHA256_ID_RE.fullmatch(result_sha256) is None
        ):
            raise RequestContractError("Hermes returned an invalid OpenHealthAtlas tool receipt.")
        fixture_id = item.get("fixture_id")
        data_class = item.get("data_class")
        if not isinstance(fixture_id, str) or not fixture_id or data_class != "fictional":
            raise RequestContractError("Hermes returned an unrecognized evidence source.")
        fixtures.add(fixture_id)
        data_classes.add(data_class)
        receipt = {
            "command": command[2],
            "result_sha256": result_sha256,
            "evidence_ids": list(evidence_ids),
        }
        if item.get("assessment_state") in {"assessed", "insufficient_data"}:
            receipt["assessment_state"] = item["assessment_state"]
        receipts.append(receipt)
        authorized_evidence_ids.update(evidence_ids)
        replay = _finding_replay(command, range_value) if selected_findings else None
        if replay is not None:
            replayed_findings.append(replay)
            replayed_finding_receipts.append((replay, set(evidence_ids)))
    if len(fixtures) != 1 or data_classes != {"fictional"}:
        raise RequestContractError("Hermes changed evidence source within one turn.")
    if not set(refs).issubset(authorized_evidence_ids):
        raise RequestContractError("Hermes cited evidence outside the same-turn tool trace.")
    if selected_findings:
        cited_evidence_ids = set(refs)
        selected_keys = {
            (item["outcome"], item["finding_id"], item["input_fingerprint"])
            for item in selected_findings
        }
        replayed_keys = {
            (item["outcome"], item["finding_id"], item["input_fingerprint"])
            for item in replayed_findings
        }
        if (
            len(replayed_findings) != len(selected_findings)
            or len(replayed_findings) != len(replayed_keys)
            or replayed_keys != selected_keys
            or any(
                replay["finding_id"] not in evidence_ids
                or not cited_evidence_ids.intersection(evidence_ids)
                for replay, evidence_ids in replayed_finding_receipts
            )
        ):
            raise RequestContractError(
                "Hermes evidence references did not match the selected findings."
            )
    return {
        "contract": "openhealthatlas-chat-evidence-v1",
        "kind": "verified_receipt",
        "data_class": "fictional",
        "fixture_id": next(iter(fixtures)),
        "range": range_value,
        "selected_findings": selected_findings,
        "evidence_refs": list(refs),
        "tool_receipts": receipts,
    }


def _conversation_json(row):
    return {
        "id": row["id"],
        "title": row["title"] or "New conversation",
        "lens": row["lens"],
        "context": _context_from_row(row),
        "archived": row["archived_at"] is not None,
        "archived_at": row["archived_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "latest_owner_excerpt": row["latest_owner_excerpt"] if "latest_owner_excerpt" in row.keys() else None,
        "busy": bool(row["busy_turn_id"] and (row["busy_until"] or 0) > int(time.time())),
    }


def _conversation_row(db, conversation_id, *, excerpt=False):
    if excerpt:
        return db.execute(
            """SELECT c.*,
                      (SELECT substr(l.content,1,240)
                         FROM chat_log l
                        WHERE l.conversation_id=c.id AND l.role='user'
                        ORDER BY l.id DESC LIMIT 1) AS latest_owner_excerpt
                 FROM chat_conversations c WHERE c.id=?""",
            (conversation_id,),
        ).fetchone()
    return db.execute(
        "SELECT * FROM chat_conversations WHERE id=?", (conversation_id,)
    ).fetchone()


@bp.get("/conversations")
def conversations():
    try:
        args = _query(allowed={"lens", "archived", "limit", "before_updated_at"})
        lens = args.get("lens")
        if lens is not None and lens not in LENSES:
            raise RequestContractError("Choose General, Pain, or Mobility.")
        archived = args.get("archived", "0")
        if archived not in {"0", "1"}:
            raise RequestContractError("archived must be 0 or 1.")
        limit = _bounded_int(args.get("limit"), field="limit", default=20, low=1, high=100)
        before = _bounded_int(
            args.get("before_updated_at"),
            field="before_updated_at",
            low=1,
            high=9_223_372_036_854_775_807,
        )
    except RequestContractError as exc:
        return _error(str(exc))

    where = ["c.archived_at IS " + ("NOT NULL" if archived == "1" else "NULL")]
    params = []
    if lens is not None:
        where.append("c.lens=?")
        params.append(lens)
    if before is not None:
        where.append("c.updated_at<?")
        params.append(before)
    params.append(limit)
    rows = get_db().execute(
        f"""SELECT c.*,
                   (SELECT substr(l.content,1,240)
                      FROM chat_log l
                     WHERE l.conversation_id=c.id AND l.role='user'
                     ORDER BY l.id DESC LIMIT 1) AS latest_owner_excerpt
              FROM chat_conversations c
             WHERE {' AND '.join(where)}
             ORDER BY c.updated_at DESC LIMIT ?""",
        params,
    ).fetchall()
    return jsonify(
        conversations=[_conversation_json(row) for row in rows],
        next_before_updated_at=rows[-1]["updated_at"] if len(rows) == limit else None,
    )


@bp.post("/conversations")
@limiter.limit("20 per hour", key_func=_owner_key)
def create_conversation():
    try:
        body = _strict_json()
        _closed_keys(body, allowed={"lens", "title", "context"}, required={"lens"})
        lens = body["lens"]
        if lens not in LENSES:
            raise RequestContractError("Choose General, Pain, or Mobility.")
        title = _title(body["title"], optional=False) if "title" in body else None
        context = (
            _public_context(body["context"])
            if "context" in body else _public_context(None, default=True)
        )
    except RequestContractError as exc:
        return _error(str(exc))

    db = get_db()
    try:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        active = db.execute(
            "SELECT COUNT(*) FROM chat_conversations WHERE archived_at IS NULL"
        ).fetchone()[0]
        if active >= MAX_ACTIVE_CONVERSATIONS:
            db.rollback()
            return _error(
                "Archive a conversation before starting another one.",
                409,
                code="active_conversation_limit",
            )
        conversation_id = secrets.token_hex(16)
        session_id = f"hermes-panel-c-{conversation_id}"
        now_ms = _next_updated_at(db)
        regions, kind, start, end, source = _row_context_values(
            lens, conversation_id, context
        )
        db.execute(
            """INSERT INTO chat_conversations(
                 id,title,surface,lens,selected_regions_json,range_kind,
                 range_from,range_to,archived_at,hermes_session_id,
                 source_context_json,created_at,updated_at,busy_turn_id,busy_until
               ) VALUES(?,?,'panel',?,?,?,?,?,NULL,?,?,?,?,NULL,NULL)""",
            (
                conversation_id,
                title or "",
                lens,
                regions,
                kind,
                start,
                end,
                session_id,
                source,
                now_ms,
                now_ms,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    row = _conversation_row(db, conversation_id, excerpt=True)
    return jsonify(ok=True, conversation=_conversation_json(row)), 201


@bp.get("/conversations/<conversation_id>")
def conversation_detail(conversation_id):
    conversation_id = _conversation_id(conversation_id)
    if conversation_id is None:
        return _error("Conversation not found.", 404, code="not_found")
    row = _conversation_row(get_db(), conversation_id, excerpt=True)
    if row is None:
        return _error("Conversation not found.", 404, code="not_found")
    return jsonify(conversation=_conversation_json(row))


@bp.patch("/conversations/<conversation_id>")
@limiter.limit("60 per minute", key_func=_owner_key)
def patch_conversation(conversation_id):
    conversation_id = _conversation_id(conversation_id)
    if conversation_id is None:
        return _error("Conversation not found.", 404, code="not_found")
    try:
        body = _strict_json()
        _closed_keys(body, allowed={"title", "archived", "context"})
        if not body:
            raise RequestContractError("Choose something to update.")
        title = _title(body["title"], optional=False) if "title" in body else None
        archived = body.get("archived")
        if "archived" in body and type(archived) is not bool:
            raise RequestContractError("archived must be true or false.")
        context = _public_context(body["context"]) if "context" in body else None
    except RequestContractError as exc:
        return _error(str(exc))

    db = get_db()
    try:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        row = _conversation_row(db, conversation_id)
        if row is None:
            db.rollback()
            return _error("Conversation not found.", 404, code="not_found")
        now_s = int(time.time())
        if row["busy_turn_id"] and (row["busy_until"] or 0) > now_s:
            db.rollback()
            return _error(
                "This conversation is already answering.",
                409,
                code="conversation_busy",
            )
        if row["busy_turn_id"]:
            db.execute(
                """UPDATE chat_log SET delivery_status='uncertain'
                    WHERE conversation_id=? AND turn_id=? AND role='user'
                      AND delivery_status='pending'""",
                (conversation_id, row["busy_turn_id"]),
            )
            db.execute(
                """UPDATE chat_conversations
                      SET busy_turn_id=NULL,busy_until=NULL WHERE id=?""",
                (conversation_id,),
            )
        changes = []
        params = []
        if "title" in body:
            changes.append("title=?")
            params.append(title)
        if "archived" in body:
            changes.append("archived_at=?")
            params.append(_now_ms() if archived else None)
        if context is not None:
            regions, kind, start, end, source = _row_context_values(
                row["lens"], conversation_id, context
            )
            changes.extend(
                [
                    "selected_regions_json=?",
                    "range_kind=?",
                    "range_from=?",
                    "range_to=?",
                    "source_context_json=?",
                ]
            )
            params.extend([regions, kind, start, end, source])
        changes.append("updated_at=?")
        params.append(_next_updated_at(db))
        params.append(conversation_id)
        db.execute(
            f"UPDATE chat_conversations SET {','.join(changes)} WHERE id=?", params
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify(
        ok=True,
        conversation=_conversation_json(
            _conversation_row(db, conversation_id, excerpt=True)
        ),
    )


@bp.get("/conversations/<conversation_id>/messages")
def conversation_messages(conversation_id):
    conversation_id = _conversation_id(conversation_id)
    if conversation_id is None:
        return _error("Conversation not found.", 404, code="not_found")
    try:
        args = _query(allowed={"limit", "before_id"})
        limit = _bounded_int(args.get("limit"), field="limit", default=50, low=1, high=200)
        before = _bounded_int(
            args.get("before_id"),
            field="before_id",
            low=1,
            high=9_223_372_036_854_775_807,
        )
    except RequestContractError as exc:
        return _error(str(exc))
    db = get_db()
    if _conversation_row(db, conversation_id) is None:
        return _error("Conversation not found.", 404, code="not_found")
    where = "conversation_id=?"
    params = [conversation_id]
    if before is not None:
        where += " AND id<?"
        params.append(before)
    params.append(limit)
    rows = db.execute(
        f"""SELECT id,ts,role,content,turn_id,context_json,evidence_json,delivery_status
              FROM chat_log WHERE {where} ORDER BY id DESC LIMIT ?""",
        params,
    ).fetchall()
    return jsonify(
        messages=[_message_json(row) for row in reversed(rows)],
        next_before_id=rows[-1]["id"] if len(rows) == limit else None,
    )


def _ambiguous_delivery(exc):
    code = (getattr(exc, "code", None) or "").lower()
    message = str(exc).lower()
    return (
        code in {"timeout", "delivery_uncertain", "hermes_timeout"}
        or "timed out" in message
        or "timeout" in message
        or "took too long" in message
        or "malformed response" in message
        or "bridge unavailable" in message
    )


def _finish_failure(conversation_id, turn_id, exc):
    db = get_db()
    status = "uncertain" if _ambiguous_delivery(exc) else "failed"
    try:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """UPDATE chat_log SET delivery_status=?
                WHERE conversation_id=? AND turn_id=? AND role='user'
                  AND delivery_status='pending'""",
            (status, conversation_id, turn_id),
        )
        db.execute(
            """UPDATE chat_conversations
                  SET busy_turn_id=NULL,busy_until=NULL,updated_at=?
                WHERE id=? AND busy_turn_id=?""",
            (_next_updated_at(db), conversation_id, turn_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return status


def _send_turn(
    conversation_id, *, message, turn_id, public_context,
    selected_findings=None, legacy=False,
):
    selected_findings = list(selected_findings or [])
    db = get_db()
    now_s = int(time.time())
    try:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        row = _conversation_row(db, conversation_id)
        if row is None:
            db.rollback()
            return _error("Conversation not found.", 404, code="not_found")
        if row["archived_at"] is not None:
            db.rollback()
            return _error(
                "Unarchive this conversation before resuming it.",
                409,
                code="conversation_archived",
            )

        existing = db.execute(
            """SELECT id,delivery_status FROM chat_log
                WHERE conversation_id=? AND turn_id=? AND role='user'""",
            (conversation_id, turn_id),
        ).fetchone()
        if existing is not None:
            status = existing["delivery_status"]
            if status == "complete":
                assistant = db.execute(
                    """SELECT id,ts,role,content,turn_id,context_json,evidence_json,
                              delivery_status
                         FROM chat_log
                        WHERE conversation_id=? AND turn_id=? AND role='assistant'""",
                    (conversation_id, turn_id),
                ).fetchone()
                db.rollback()
                reply = assistant["content"] if assistant else "(no reply)"
                evidence = _message_json(assistant)["evidence"] if assistant else None
                return jsonify(
                    ok=True, reply=reply, evidence=evidence, idempotent=True,
                )
            if status == "pending" and (
                row["busy_turn_id"] == turn_id and (row["busy_until"] or 0) > now_s
            ):
                db.rollback()
                return _error(
                    "This conversation is already answering.",
                    409,
                    code="conversation_busy",
                )
            if status == "pending":
                db.execute(
                    """UPDATE chat_log SET delivery_status='uncertain'
                        WHERE conversation_id=? AND turn_id=? AND role='user'""",
                    (conversation_id, turn_id),
                )
                db.execute(
                    """UPDATE chat_conversations
                          SET busy_turn_id=NULL,busy_until=NULL
                        WHERE id=? AND busy_turn_id=?""",
                    (conversation_id, turn_id),
                )
                db.commit()
                status = "uncertain"
            else:
                db.rollback()
            message_text = (
                "OpenHealthAtlas may have received this message. It will not be sent again."
                if status == "uncertain"
                else "This message already finished with an error and was not sent again."
            )
            return _error(message_text, 409, code=f"turn_{status}")

        if row["busy_turn_id"]:
            if (row["busy_until"] or 0) > now_s:
                db.rollback()
                return _error(
                    "This conversation is already answering.",
                    409,
                    code="conversation_busy",
                )
            db.execute(
                """UPDATE chat_log SET delivery_status='uncertain'
                    WHERE conversation_id=? AND turn_id=? AND role='user'
                      AND delivery_status='pending'""",
                (conversation_id, row["busy_turn_id"]),
            )
            db.execute(
                """UPDATE chat_conversations
                      SET busy_turn_id=NULL,busy_until=NULL WHERE id=?""",
                (conversation_id,),
            )

        stored_context = _context_from_row(row)
        if public_context != stored_context:
            db.rollback()
            return _error(
                "The conversation scope changed. Reload it before sending.",
                409,
                code="conversation_context_stale",
            )
        if selected_findings and stored_context["range"]["kind"] != "bounded":
            db.rollback()
            return _error(
                "Choose a bounded date window before asking about selected evidence.",
                400,
                code="selected_evidence_requires_bounded_range",
            )
        trusted = _trusted_context(
            row["lens"], conversation_id, stored_context, selected_findings,
        )
        regions, kind, start, end, source = _row_context_values(
            row["lens"], conversation_id, stored_context
        )
        updated_at = _next_updated_at(db)
        title = row["title"] or _default_title(message)
        db.execute(
            """UPDATE chat_conversations
                  SET title=?,selected_regions_json=?,range_kind=?,range_from=?,
                      range_to=?,source_context_json=?,updated_at=?,
                      busy_turn_id=?,busy_until=?
                WHERE id=?""",
            (
                title,
                regions,
                kind,
                start,
                end,
                source,
                updated_at,
                turn_id,
                now_s + LEASE_SECONDS,
                conversation_id,
            ),
        )
        db.execute(
            """INSERT INTO chat_log(
                 ts,thread,role,content,conversation_id,turn_id,context_json,
                 evidence_json,delivery_status
               ) VALUES(?,?,?,?,?,?,?,?,'pending')""",
            (
                now_s,
                row["lens"],
                "user",
                message,
                conversation_id,
                turn_id,
                _canonical_json(trusted),
                _canonical_json(
                    _selection_record(stored_context["range"], selected_findings)
                ) if selected_findings else None,
            ),
        )
        session_id = row["hermes_session_id"]
        db.commit()
    except Exception:
        db.rollback()
        raise

    envelope = _canonical_json(
        {
            "contract": (
                "hermes-panel-turn-v2" if selected_findings
                else "hermes-panel-turn-v1"
            ),
            "message": message,
            "context": trusted,
        }
    )
    try:
        result = bridge.run(
            "hermes-chat", "--session", session_id, stdin=envelope, timeout=118.0
        )
    except bridge.BridgeError as exc:
        status = _finish_failure(conversation_id, turn_id, exc)
        if legacy:
            return jsonify(ok=False, error=str(exc)), 502
        if status == "uncertain":
            return _error(
                "OpenHealthAtlas may have received this message. It will not be sent again.",
                502,
                code="turn_uncertain",
            )
        return _error(
            "OpenHealthAtlas could not answer this message.",
            502,
            code="turn_failed",
        )

    reply = (result.get("reply") or "").strip() or "(no reply)"
    try:
        evidence = _normalized_evidence_receipt(
            result, stored_context["range"], selected_findings,
        )
    except RequestContractError as exc:
        _finish_failure(
            conversation_id,
            turn_id,
            bridge.BridgeError(str(exc), code="invalid_evidence_receipt"),
        )
        return _error(
            "OpenHealthAtlas could not verify the evidence returned for this turn.",
            502,
            code="turn_failed",
        )
    try:
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """INSERT OR IGNORE INTO chat_log(
                 ts,thread,role,content,conversation_id,turn_id,context_json,
                 evidence_json,delivery_status
               ) VALUES(?,?,?,?,?,?,?,?,'complete')""",
            (
                int(time.time()),
                row["lens"],
                "assistant",
                reply,
                conversation_id,
                turn_id,
                _canonical_json(trusted),
                _canonical_json(evidence) if evidence is not None else None,
            ),
        )
        db.execute(
            """UPDATE chat_log SET delivery_status='complete'
                WHERE conversation_id=? AND turn_id=? AND role='user'
                  AND delivery_status='pending'""",
            (conversation_id, turn_id),
        )
        db.execute(
            """UPDATE chat_conversations
                  SET busy_turn_id=NULL,busy_until=NULL,updated_at=?
                WHERE id=? AND busy_turn_id=?""",
            (_next_updated_at(db), conversation_id, turn_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify(ok=True, reply=reply, evidence=evidence)


@bp.post("/conversations/<conversation_id>/send")
@limiter.limit("6 per minute", key_func=_send_rate_key)
def conversation_send(conversation_id):
    conversation_id = _conversation_id(conversation_id)
    if conversation_id is None:
        return _error("Conversation not found.", 404, code="not_found")
    try:
        body = _strict_json()
        _closed_keys(
            body,
            allowed={"message", "turn_id", "context", "selected_findings"},
            required={"message", "turn_id", "context"},
        )
        message = body["message"]
        if not isinstance(message, str):
            raise RequestContractError("The message must be text.")
        message = message.strip()
        if not message:
            raise RequestContractError("Write a message before sending.")
        if len(message) > MAX_MESSAGE:
            raise RequestContractError("The message must be 4,000 characters or fewer.")
        turn_id = _turn_id(body["turn_id"])
        context = _public_context(body["context"])
        selected_findings = _selected_findings(body.get("selected_findings"))
    except RequestContractError as exc:
        return _error(str(exc))
    return _send_turn(
        conversation_id,
        message=message,
        turn_id=turn_id,
        public_context=context,
        selected_findings=selected_findings,
    )


# ---------------------------------------------------------------------------
# Legacy exact-shape adapters. They are unreachable in a normal panel process
# and retained only as explicit test fixtures while scoped conversations are
# the sole supported browser contract.


@bp.get("/history")
def history():
    if not _legacy_reference_enabled():
        return _legacy_route_quarantined()
    thread = _thread(request.args.get("thread", "general"))
    if thread is None:
        return jsonify(ok=False, error="unknown chat thread"), 400
    limit = request.args.get("limit", 50, type=int)
    limit = max(1, min(limit, 1000))
    conversation_id = _ensure_legacy_conversation(thread)
    rows = get_db().execute(
        """SELECT ts,role,content FROM chat_log
            WHERE conversation_id=? ORDER BY id DESC LIMIT ?""",
        (conversation_id, limit),
    ).fetchall()
    return jsonify(messages=[dict(row) for row in reversed(rows)])


@bp.post("/send")
@limiter.limit("6 per minute", key_func=_send_rate_key)
def send():
    if not _legacy_reference_enabled():
        return _legacy_route_quarantined()
    body = request.get_json(silent=True) or {}
    message = body.get("message", "")
    message = message.strip() if isinstance(message, str) else ""
    thread = _thread(body.get("thread", "general"))
    if thread is None:
        return jsonify(ok=False, error="unknown chat thread"), 400
    if not message:
        return jsonify(ok=False, error="empty message"), 400
    if len(message) > MAX_MESSAGE:
        return jsonify(ok=False, error="message too long"), 400
    return _send_turn(
        _ensure_legacy_conversation(thread),
        message=message,
        turn_id=str(uuid.uuid4()),
        public_context={
            "version": 1,
            "range": {"kind": "all"},
            "selected_region_ids": [],
        },
        legacy=True,
    )
