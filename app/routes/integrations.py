"""Integrations & privacy settings page (task-36): a thin, read-only status
board over the sources the vault already integrates with. No new bridge
subcommands — this composes db_read SELECTs with the existing hermes-status
and fitbit-status bridge reads (both already ALLOWED and panel-reachable via
/api/models/*; see app/bridge.py).

Every chip below is a DETERMINISTIC derivation, documented here, never a
hardcoded "Connected" — stale or missing data says so (owner ground rule).
"""
from flask import Blueprint, jsonify

from app import bridge, canon, db_read

bp = Blueprint("integrations", __name__, url_prefix="/api/integrations")

# "Connected" freshness windows, in days. The only two rows here with a data
# DATE to compare against "now" (Telegram is a live gateway health check, not
# a date; Bloodwork is manual-upload by design, so its date is informational
# only — see below). Hevy: 2-3 sessions/week is normal, so a run without a
# hard rest week still reads Connected at 14 days. Wearable: Fitbit syncs
# roughly daily, so 3 days catches a real sync outage without flagging a
# single day the phone wasn't opened.
HEVY_FRESH_DAYS = 14
WEARABLE_FRESH_DAYS = 3

# Product decision: the Apple wearable pipeline is retired (device
# replaced; last apple daily_metrics row 2026-05-31). Apple's MAX(date) is
# frozen going forward, so it must never drag or prop up the live wearable
# status — occasional manual archive imports may still land (roughly
# monthly/quarterly), so apple_latest keeps reading the real DB date.
RETIRED_SOURCES = {"apple": "2026-05-31"}


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


def _telegram_status(agent):
    if "error" in agent:
        return "unknown"
    if agent.get("gateway_active") is True:
        return "connected"
    if agent.get("gateway_active") is False:
        return "not_running"
    return "unknown"


@bp.get("/sources")
def sources():
    """Composes the Connected-sources card (design image 31): Telegram bot /
    Hermes agent (live gateway check via hermes-status), Hevy training sync
    (last hevy_sets date), Wearable (Fitbit daily_metrics date + auth state —
    Apple is retired per RETIRED_SOURCES and reported separately so it can
    never drag or prop up the live status), Bloodwork lab (latest labs date —
    manual-upload by design, so it gets no freshness judgment, only the real
    date if any)."""
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
    # Apple is retired (see RETIRED_SOURCES) — the combined wearable status
    # comes from fitbit alone now, even when apple rows exist and are newer.
    wearable_latest = fitbit_latest

    return jsonify(
        telegram={
            "status": _telegram_status(agent),
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
                "status": "retired",
                "latest_date": apple_latest,
                "since": RETIRED_SOURCES["apple"],
                "note": f"retired {RETIRED_SOURCES['apple']} — wearable replaced; "
                        "archive imports may still land occasionally",
            },
        },
        labs={
            "latest_date": labs_latest,
        },
    )
