"""Read-only source freshness and optional external gateway status."""
from flask import Blueprint, jsonify

from app import bridge, canon, db_read

bp = Blueprint("integrations", __name__, url_prefix="/api/integrations")

# Training can be weekly; wearable observations are usually daily.
HEVY_FRESH_DAYS = 14
WEARABLE_FRESH_DAYS = 3


def _guard():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    return None


def _max_date(sql):
    rows = db_read.query(sql)
    return rows[0]["m"] if rows and rows[0]["m"] else None


def _status_for(latest_date, fresh_days):
    """connected/stale/none against a fresh-days cutoff, computed on the
    canonical configured day (app.canon) — never SQLite's UTC 'now'."""
    if not latest_date:
        return "none"
    return "connected" if latest_date >= canon.days_ago_iso(fresh_days) else "stale"


def _gateway_status(agent):
    if "error" in agent:
        return "unknown"
    if agent.get("gateway_active") is True:
        return "connected"
    if agent.get("gateway_active") is False:
        return "not_running"
    return "unknown"


@bp.get("/sources")
def sources():
    """Report observed source freshness and the optional Hermes gateway state."""
    if (resp := _guard()) is not None:
        return resp

    try:
        agent = bridge.run("hermes-status")
    except bridge.BridgeError as exc:
        agent = {"error": str(exc)}

    try:
        fitbit = bridge.run("fitbit-status")
    except bridge.BridgeError as exc:
        fitbit = {"error": str(exc)}

    hevy_latest = _max_date("SELECT MAX(date) AS m FROM hevy_sets")
    apple_latest = _max_date("SELECT MAX(date) AS m FROM daily_metrics WHERE source = 'apple'")
    fitbit_latest = _max_date("SELECT MAX(date) AS m FROM daily_metrics WHERE source = 'fitbit'")
    labs_latest = _max_date("SELECT MAX(date) AS m FROM labs")
    wearable_latest = max((date for date in (apple_latest, fitbit_latest) if date), default=None)

    return jsonify(
        telegram={
            "status": _gateway_status(agent),
            "model": agent.get("model"),
            "provider": agent.get("provider"),
            "error": agent.get("error"),
        },
        hevy={
            "status": _status_for(hevy_latest, HEVY_FRESH_DAYS),
            "latest_date": hevy_latest,
        },
        wearable={
            "status": _status_for(wearable_latest, WEARABLE_FRESH_DAYS),
            "latest_date": wearable_latest,
            "apple_latest": apple_latest,
            "fitbit_latest": fitbit_latest,
            "fitbit_auth_status": fitbit.get("auth_status"),
            "apple": {
                "status": _status_for(apple_latest, WEARABLE_FRESH_DAYS),
                "latest_date": apple_latest,
            },
            "fitbit": {
                "status": _status_for(fitbit_latest, WEARABLE_FRESH_DAYS),
                "latest_date": fitbit_latest,
                "auth_status": fitbit.get("auth_status"),
            },
        },
        labs={
            "latest_date": labs_latest,
        },
    )
