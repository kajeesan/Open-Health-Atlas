"""Write endpoints — the daily-loop logging surface (Phase 5).

Every endpoint is a POST, CSRF-protected and auth-gated (the app-level gate),
and turns the request into a validated `health.py` call through the bridge.
Nothing here touches SQL: bridge.run() -> hermes-bridge -> health.py. Light
input checks give fast feedback; health.py remains the source of truth for
validation and all math, and its confirmation JSON is echoed back so the write
is visible ("determinism made visible").
"""
import json
import math
import re

from flask import Blueprint, jsonify, request

from app import bridge
from app.panel_db import get_db

bp = Blueprint("log", __name__, url_prefix="/api/log")

# Explicit mutations: new read commands must never become apparent writes.
ACTIVITY_WRITES = (
    "day-rating", "log", "log-set", "log-food", "eat", "prep", "set-batch",
    "commitment-set", "log-commitment", "checkin", "routine-set",
    "routine-remove", "routine-undo", "schedule-set", "write-note",
    "hypothesis-promote", "hermes-model-set",
)

DAY_RATINGS = {"green", "yellow", "red"}
# subjective 1–5 rating fields the quick-log offers
RATING_FIELDS = ("focus", "energy", "mood", "emotional_regulation",
                 "anxiety", "motivation", "stress")


def _run(*args):
    """Call the bridge, mapping BridgeError to a JSON 400 with a safe message."""
    try:
        return jsonify(ok=True, result=bridge.run(*args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


def _int_in_range(v, lo, hi):
    """Parse a whole number in [lo, hi]. Rejects (rather than floors) fractional
    input like 124.7 — the server is the trust boundary and must not silently
    coerce a vitals reading (found by review)."""
    try:
        fv = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(fv) or fv != int(fv):
        return None
    iv = int(fv)
    return iv if lo <= iv <= hi else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@bp.post("/day-rating")
def day_rating():
    value = (request.get_json(silent=True) or {}).get("value")
    if value not in DAY_RATINGS:
        return jsonify(ok=False, error="value must be green, yellow or red"), 400
    return _run("day-rating", value, "--source", "panel-ui")


WORD_STATUSES = {"kept", "partly", "broke"}
CHECKIN_KINDS = {"energy", "focus", "mood"}


@bp.post("/word")
def word():
    """The nightly self-integrity one-tap: did I keep my word to myself today?
    kept / partly / broke (+ optional one-line why) -> health.py log-commitment.
    Re-tapping the same day updates the row (health.py upserts)."""
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in WORD_STATUSES:
        return jsonify(ok=False, error="status must be kept, partly or broke"), 400
    args = ["log-commitment", status]
    why = (body.get("why") or "").strip()
    if why:
        if len(why) > 300:
            return jsonify(ok=False, error="why must be under 300 characters"), 400
        args += ["--why", why]
    args += ["--source", "panel-ui"]
    return _run(*args)


@bp.post("/checkin")
def checkin():
    """Timed 1-5 spot reading (energy/focus/mood), stamped with the current
    canonical time by health.py — feeds the within-day dose-timing analysis."""
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    if kind not in CHECKIN_KINDS:
        return jsonify(ok=False, error="kind must be energy, focus or mood"), 400
    value = _int_in_range(body.get("value"), 1, 5)
    if value is None:
        return jsonify(ok=False, error="value must be a whole number 1-5"), 400
    return _run("checkin", kind, str(value), "--source", "panel-ui")


@bp.post("/vitals")
def vitals():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(ok=False, error="vitals must be an object"), 400
    sys_bp = _int_in_range(body.get("systolic"), 60, 260)
    dia_bp = _int_in_range(body.get("diastolic"), 30, 180)
    if sys_bp is None or dia_bp is None:
        return jsonify(ok=False, error="systolic (60–260) and diastolic (30–180) are required"), 400
    if sys_bp <= dia_bp:
        return jsonify(ok=False, error="systolic must be higher than diastolic"), 400
    args = ["log", "vitals", f"systolic={sys_bp}", f"diastolic={dia_bp}"]
    hr = _int_in_range(body.get("resting_hr"), 30, 220)
    if body.get("resting_hr") not in (None, "") and hr is None:
        return jsonify(ok=False, error="resting_hr must be a whole number 30–220"), 400
    if hr is not None:
        args.append(f"resting_hr={hr}")
    time_value = body.get("time")
    if time_value not in (None, ""):
        if not isinstance(time_value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value):
            return jsonify(ok=False, error="time must be HH:MM"), 400
        args.append(f"time={time_value}")
    return _run(*args)


@bp.post("/subjective")
def subjective():
    body = request.get_json(silent=True) or {}
    args = ["log", "subjective_daily"]
    for f in RATING_FIELDS:
        if body.get(f) not in (None, ""):
            iv = _int_in_range(body[f], 1, 5)
            if iv is None:
                return jsonify(ok=False, error=f"{f} must be 1–5"), 400
            args.append(f"{f}={iv}")
    dump = (body.get("brain_dump") or "").strip()
    if dump:
        args.append(f"brain_dump={dump}")
    if len(args) == 2:
        return jsonify(ok=False, error="nothing to log"), 400
    return _run(*args)


@bp.post("/body")
def body_metrics():
    body = request.get_json(silent=True) or {}
    args = ["log", "body_metrics"]
    for field, lo, hi in (("weight_kg", 30, 400), ("waist_cm", 30, 250)):
        if body.get(field) not in (None, ""):
            val = _num(body[field])
            if val is None or not (lo <= val <= hi):
                return jsonify(ok=False, error=f"{field} out of range"), 400
            args.append(f"{field}={val}")
    if len(args) == 2:
        return jsonify(ok=False, error="nothing to log"), 400
    return _run(*args)


@bp.post("/water")
def water():
    body = request.get_json(silent=True) or {}
    ml = _num(body.get("water_ml"))
    if ml is None or not (0 <= ml <= 10000):
        return jsonify(ok=False, error="water_ml must be 0–10000"), 400
    return _run("log", "intake", f"water_ml={ml}")


@bp.get("/recent")
def recent():
    """Activity feed: panel-originated write attempts (panel.db's audit_log),
    newest first. Reads the panel's own state DB, not the health DB."""
    events = ["bridge." + command for command in ACTIVITY_WRITES]
    rows = get_db().execute(
        "SELECT ts, event, detail FROM audit_log"
        f" WHERE event IN ({','.join('?' for _ in events)}) ORDER BY id DESC LIMIT 20",
        events,
    )
    items = []
    for row in rows:
        outcome = None
        try:
            detail = json.loads(row["detail"])
            if isinstance(detail, dict):
                outcome = detail.get("outcome")
        except (TypeError, ValueError):
            pass
        status = "unconfirmed"
        if outcome == "exit:0":
            status = "succeeded"
        elif isinstance(outcome, str) and (
            re.fullmatch(r"exit:-?\d+", outcome) or outcome.startswith("refused-")
        ):
            status = "failed"
        items.append({**dict(row), "status": status})
    return jsonify(items=items)
