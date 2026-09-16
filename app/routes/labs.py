"""§labs: bloodwork view feed. Read-only — the panel calls the `labs` read
command through the bridge (health.py does the grouping + range flags) and
never any writer. Ingestion (OCR/parse/validate) is the coach path.
"""
from flask import Blueprint, jsonify, request

from app import bridge

bp = Blueprint("labs", __name__, url_prefix="/api/labs")


def _run(*args):
    try:
        return jsonify(bridge.run(*args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@bp.get("")
def labs_list():
    """Latest result per test, grouped by panel (health.py computes the range
    flags). Optional ?panel= filters to one panel."""
    panel = (request.args.get("panel") or "").strip()
    return _run("labs", "--panel", panel) if panel else _run("labs")


@bp.get("/test/<path:canonical>")
def labs_trend(canonical):
    """One test's series over time for the trend chart."""
    return _run("labs", "--test", canonical)
