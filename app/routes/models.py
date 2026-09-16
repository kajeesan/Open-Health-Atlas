"""Models & spend: current model, safe switching (auto-rollback in hermesctl),
OpenRouter spend vs a budget cap, Fitbit re-auth status.

Everything agent-side goes through the bridge to hermesctl (runs as hermes —
the panel never sees API keys). The budget cap is panel state (panel.db).
"""
import re
import time

from flask import Blueprint, jsonify, request

from app import bridge
from app.panel_db import get_db

bp = Blueprint("models", __name__, url_prefix="/api/models")

MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.:\-]{0,79}\Z")


def _setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _run(*args, timeout=30.0):
    try:
        return jsonify(ok=True, result=bridge.run(*args, timeout=timeout))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@bp.get("/status")
def status():
    out = {"budget_monthly_usd": _setting("budget_monthly_usd")}
    try:
        out["agent"] = bridge.run("hermes-status")
    except bridge.BridgeError as exc:
        out["agent"] = {"error": str(exc)}
    try:
        out["spend"] = bridge.run("hermes-spend")
    except bridge.BridgeError as exc:
        out["spend"] = {"error": str(exc)}
    return jsonify(out)


@bp.post("/set")
def set_model():
    b = request.get_json(silent=True) or {}
    model = (b.get("model") or "").strip()
    if not MODEL_RE.fullmatch(model):
        return jsonify(ok=False, error="model id has an unexpected shape"), 400
    # hermesctl restarts the gateway, health-checks and auto-rolls-back; give it time.
    return _run("hermes-model-set", model, timeout=160.0)


@bp.post("/budget")
def budget():
    b = request.get_json(silent=True) or {}
    try:
        val = float(b.get("monthly_usd"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="monthly_usd must be a number"), 400
    if not (0 <= val <= 10000):
        return jsonify(ok=False, error="monthly_usd out of range"), 400
    db = get_db()
    db.execute("INSERT INTO settings(key, value) VALUES('budget_monthly_usd', ?)"
               " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(val),))
    db.execute("INSERT INTO audit_log(ts, event, detail) VALUES(?,?,?)",
               (int(time.time()), "settings.budget", str(val)))
    db.commit()
    return jsonify(ok=True, budget_monthly_usd=val)


@bp.get("/fitbit")
def fitbit():
    return _run("fitbit-status")
