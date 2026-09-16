"""Governed Insights APIs plus explicitly quarantined compatibility fixtures."""
from datetime import date
import hashlib
import json
import os
import re

from deploy.bridge_commands import OUTCOME_ANALYSIS_TIMEOUT

from flask import Blueprint, current_app, jsonify, request

from app import bridge, limiter
from app.canon import CanonicalRangeError, canonical_range, range_bounds
bp = Blueprint("insights", __name__, url_prefix="/api/insights")

# Consistency page's range dropdown (owner-review r2), mirroring dash.py's
# ALLOWED_DAYS whitelist-and-validate style. "all" maps to health.py's own
# days=0 sentinel (_daily_frame: "0 = all", window from the earliest logged
# row) — genuinely unbounded, not a big-number stand-in.
# "1" added in owner-review r3 (task-26): the Consistency dropdown gained a
# "Day" option globally, so the follow-through card must honor a 1-day window.
ADHERENCE_ALLOWED_DAYS = {"1": 1, "7": 7, "30": 30, "90": 90, "365": 365, "all": 0}
OUTCOME_MODES = {
    "all", "ordinal", "green-vs-non-green", "red-vs-non-red",
}
FEATURE_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
GOAL_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")
SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
HYPOTHESIS_STATUSES = {
    "candidate", "exploratory", "strengthening", "replicated",
    "weakened", "rejected", "dormant",
}
# Allow the broad outcome read to finish and return its controlled timeout
# before the web worker expires. Other broker operations keep their own caps.
PHASE4_ENGINE_TIMEOUT = float(OUTCOME_ANALYSIS_TIMEOUT + 10)
FIXTURE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def _engine(subcmd, *args, timeout=75.0):
    """Run a read-only engine command; engine JSON passes through untouched so
    n / suppressed counts / insufficient-data refusals stay visible.

    Timeout chain (review finding): legacy reads use the broker's 60s child
    limit and this helper's 75s socket default. Phase 4 outcomes explicitly use
    the broker's 570s / client 580s budget. Gunicorn's 600s worker limit must
    exceed the applicable pair or the worker gets SIGKILLed mid-request."""
    try:
        return jsonify(ok=True, result=bridge.run(subcmd, *args, timeout=timeout))
    except bridge.BridgeError as exc:
        if exc.code is not None:
            status = 409 if exc.code == "stale_finding" else 502
            return jsonify(
                ok=False,
                error={"code": exc.code, "message": str(exc)},
            ), status
        return jsonify(ok=False, error=str(exc)), 502


def _invalid_query(error):
    return jsonify(error=error), 400


def _canonical_result_sha256(result):
    encoded = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _job_flags(command, args):
    """Compare complete operation scopes without depending on flag order."""
    flags = {"--mode": "all", "--min-n": "30", "--interactions": "none",
             "--top": "30"} if command == "outcome-associations" else {}
    supplied = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if not isinstance(flag, str) or not flag.startswith("--") or flag in supplied:
            raise ValueError("invalid job scope")
        supplied.add(flag)
        if flag == "--all":
            flags[flag] = True
        else:
            index += 1
            if index >= len(args) or not isinstance(args[index], str):
                raise ValueError("invalid job scope")
            flags[flag] = (str(int(args[index])) if flag in {"--min-n", "--top", "--days"}
                           else args[index])
        index += 1
    if not supplied.intersection({"--from", "--to", "--days", "--all"}):
        flags["--days"] = "365"
    return flags


def _analysis_job(command, argv, *, job_id=None, socket_path=None, scope=False):
    """Job state stays outside the unchanged deterministic result envelope."""
    if job_id is not None and JOB_ID.fullmatch(job_id) is None:
        return _invalid_query("invalid_job_id")
    if job_id is None and request.get_json(silent=True) != {}:
        return _invalid_query("invalid_job_request")
    kwargs = {"timeout": 30.0}
    if socket_path is not None:
        kwargs["socket_path"] = socket_path
    try:
        if job_id is None:
            job = bridge.run(
                "analysis-job-start",
                stdin=json.dumps({"command": command, "args": list(argv)}),
                **kwargs,
            )
        else:
            job = bridge.run("analysis-job-status", job_id, **kwargs)
    except bridge.BridgeError as exc:
        status = 409 if exc.code in {"stale_finding", "stale_job"} else 502
        return jsonify(ok=False, error={"code": exc.code or "job_unavailable",
                                         "message": str(exc)}), status
    try:
        stored = job["request"]
        matches = (
            stored["command"] == command
            and _job_flags(command, stored["args"]) == _job_flags(command, argv)
        )
        valid = (
            JOB_ID.fullmatch(job["job_id"]) is not None
            and (job_id is None or job["job_id"] == job_id)
            and job["status"] in {"queued", "running", "completed", "failed", "stale"}
        )
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="Invalid analysis job response."), 502
    if not valid:
        return jsonify(ok=False, error="Invalid analysis job response."), 502
    if not matches:
        # A remembered identifier is never authority to reveal another scope.
        return jsonify(ok=False, error={"code": "job_scope_mismatch",
                                         "message": "This job belongs to another analysis window."}), 409
    public = {"job_id": job["job_id"], "status": job["status"]}
    if job["status"] == "completed":
        if not isinstance(job.get("result"), dict):
            return jsonify(ok=False, error="Analysis job has no result."), 502
        public["result"] = job["result"]
        if scope is not False:
            evidence = {
                "contract": "openhealthatlas-dashboard-green-days-evidence-v1",
                "deterministic_authority": "OpenHealthAtlas",
                "command": command, "args": list(argv),
                "result_sha256": _canonical_result_sha256(job["result"]),
            }
            if scope is not None:
                evidence.update(scope)
            public["evidence"] = evidence
    elif job["status"] in {"failed", "stale"}:
        public["error"] = job.get("error") or {
            "code": "analysis_failed", "message": "Analysis could not finish. Try again.",
        }
    response = jsonify(ok=True, job=public)
    response.headers["Cache-Control"] = "no-store"
    return response, (202 if job["status"] in {"queued", "running"} else 200)


def _dashboard_green_days_scope():
    """Return one trusted display range; reject malformed server config."""
    scope = current_app.config.get("DASHBOARD_GREEN_DAYS_FIXED_SCOPE")
    if scope is None:
        return ("--days", "365"), None
    expected = {
        "data_class", "fixture_id", "range_from", "range_to",
        "seed_sha256", "database_sha256",
    }
    if not isinstance(scope, dict) or set(scope) != expected:
        raise ValueError("invalid dashboard Green-day fixed scope")
    if (
        scope["data_class"] != "fictional"
        or not isinstance(scope["fixture_id"], str)
        or FIXTURE_ID.fullmatch(scope["fixture_id"]) is None
        or SHA256_ID.fullmatch(scope.get("seed_sha256", "")) is None
        or SHA256_ID.fullmatch(scope.get("database_sha256", "")) is None
    ):
        raise ValueError("invalid dashboard Green-day fixed scope")
    try:
        start = date.fromisoformat(scope["range_from"])
        end = date.fromisoformat(scope["range_to"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid dashboard Green-day fixed scope") from exc
    if (
        start.isoformat() != scope["range_from"]
        or end.isoformat() != scope["range_to"]
        or start > end
    ):
        raise ValueError("invalid dashboard Green-day fixed scope")
    return ("--from", scope["range_from"], "--to", scope["range_to"]), dict(scope)


def _legacy_reference_enabled():
    return (
        current_app.testing
        and current_app.config.get(
            "ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES"
        ) is True
    )


def _legacy_route_quarantined(replacement):
    return jsonify(
        ok=False,
        error={
            "code": "legacy_insight_route_quarantined",
            "message": f"Use {replacement}.",
        },
    ), 410


def _range_args(allowed_keys):
    """Validate the exact explicit API range contract before bridge access."""
    supplied = set(request.args)
    extra = supplied - allowed_keys
    if extra:
        return None, _invalid_query("unexpected_query_parameter")
    if any(len(request.args.getlist(key)) != 1 for key in supplied):
        return None, _invalid_query("duplicate_query_parameter")

    range_kind = request.args.get("range")
    has_from = "from" in supplied
    has_to = "to" in supplied
    if range_kind == "all":
        if has_from or has_to:
            return None, _invalid_query("invalid_range")
        return ["--all"], None
    if range_kind != "bounded" or not has_from or not has_to:
        return None, _invalid_query("invalid_range")

    try:
        normalized = canonical_range({
            "kind": "bounded",
            "from": request.args["from"],
            "to": request.args["to"],
        })
    except CanonicalRangeError:
        return None, _invalid_query("invalid_range")
    return ["--from", normalized["from"], "--to", normalized["to"]], None


@bp.get("/range")
def canonical_window():
    """Server-owned canonical day/week/month/year/all range controls."""
    supplied = set(request.args)
    if supplied - {"granularity", "anchor", "shift"}:
        return _invalid_query("unexpected_query_parameter")
    if any(len(request.args.getlist(key)) != 1 for key in supplied):
        return _invalid_query("duplicate_query_parameter")
    if "granularity" not in supplied:
        return _invalid_query("invalid_granularity")
    raw_shift = request.args.get("shift", "0")
    if raw_shift not in {"-1", "0", "1"}:
        return _invalid_query("invalid_shift")
    try:
        result = range_bounds(
            request.args["granularity"],
            anchor=request.args.get("anchor"),
            shift=int(raw_shift),
        )
    except CanonicalRangeError:
        return _invalid_query("invalid_range")
    return jsonify(ok=True, result=result)


@bp.get("/outcomes")
def outcomes():
    argv, error = _outcome_args()
    if error is not None:
        return error
    return _engine("outcome-associations", *argv, timeout=PHASE4_ENGINE_TIMEOUT)


def _outcome_args():
    allowed = {"outcome", "mode", "range", "from", "to"}
    range_argv, error = _range_args(allowed)
    if error is not None:
        return None, error
    outcome = request.args.get("outcome")
    mode = request.args.get("mode")
    if outcome is None or FEATURE_KEY.fullmatch(outcome) is None:
        return None, _invalid_query("invalid_outcome")
    if mode not in OUTCOME_MODES:
        return None, _invalid_query("invalid_mode")
    return ["--outcome", outcome, "--mode", mode, *range_argv], None


@bp.post("/outcome-jobs")
@limiter.limit("12/minute")
def start_outcome_job():
    return outcome_job()


@bp.get("/outcome-jobs/<job_id>")
def outcome_job(job_id=None):
    argv, error = _outcome_args()
    if error is not None:
        return error
    return _analysis_job("outcome-associations", argv, job_id=job_id)


@bp.post("/finding-jobs")
@limiter.limit("12/minute")
def start_finding_job():
    return finding_job()


@bp.get("/finding-jobs/<job_id>")
def finding_job(job_id=None):
    argv, error = _range_args({
        "range", "from", "to", "outcome", "finding_id", "input_fingerprint",
    })
    if error is not None:
        return error
    outcome = request.args.get("outcome", "")
    finding_id = request.args.get("finding_id", "")
    fingerprint = request.args.get("input_fingerprint", "")
    if FEATURE_KEY.fullmatch(outcome) is None:
        return _invalid_query("invalid_outcome")
    if SHA256_ID.fullmatch(finding_id) is None:
        return _invalid_query("invalid_finding_id")
    if SHA256_ID.fullmatch(fingerprint) is None:
        return _invalid_query("invalid_input_fingerprint")
    return _analysis_job("finding-evidence", [
        "--outcome", outcome, "--finding-id", finding_id,
        "--input-fingerprint", fingerprint, *argv,
    ], job_id=job_id)


@bp.get("/dashboard-green-days")
def dashboard_green_days():
    """Governed, deterministic Green-vs-non-Green dashboard evidence."""
    prepared, error = _dashboard_green_days_request()
    if error is not None:
        return error
    argv, scope, fixture_socket = prepared
    try:
        run_kwargs = {"timeout": PHASE4_ENGINE_TIMEOUT}
        if fixture_socket is not None:
            run_kwargs["socket_path"] = fixture_socket
        result = bridge.run("outcome-associations", *argv, **run_kwargs)
    except bridge.BridgeError as exc:
        if exc.code is not None:
            status = 409 if exc.code == "stale_finding" else 502
            return jsonify(ok=False, error={"code": exc.code, "message": str(exc)}), status
        return jsonify(ok=False, error=str(exc)), 502
    evidence = {
        "contract": "openhealthatlas-dashboard-green-days-evidence-v1",
        "deterministic_authority": "OpenHealthAtlas",
        "command": "outcome-associations", "args": list(argv),
        "result_sha256": _canonical_result_sha256(result),
    }
    if scope is not None:
        evidence.update(scope)
    return jsonify(ok=True, result=result, evidence=evidence)


@bp.post("/dashboard-green-days/jobs")
@limiter.limit("12/minute")
def start_dashboard_green_days_job():
    return dashboard_green_days_job()


@bp.get("/dashboard-green-days/jobs/<job_id>")
def dashboard_green_days_job(job_id=None):
    prepared, error = _dashboard_green_days_request()
    if error is not None:
        return error
    argv, scope, fixture_socket = prepared
    return _analysis_job("outcome-associations", argv, job_id=job_id,
                         socket_path=fixture_socket, scope=scope)


def _dashboard_green_days_request():
    if request.args:
        return None, _invalid_query("unexpected_query_parameter")
    try:
        range_argv, scope = _dashboard_green_days_scope()
    except ValueError:
        return None, (jsonify(
            ok=False,
            error="dashboard Green-day display configuration is invalid",
        ), 503)
    fixture_socket = current_app.config.get(
        "DASHBOARD_GREEN_DAYS_BRIDGE_SOCKET"
    )
    if scope is not None and (
        not isinstance(fixture_socket, str)
        or not fixture_socket.startswith("/")
        or "\x00" in fixture_socket
    ):
        return None, (jsonify(
            ok=False,
            error="dashboard Green-day fixture broker is unavailable",
        ), 503)
    if scope is None and fixture_socket is not None:
        return None, (jsonify(
            ok=False,
            error="dashboard Green-day display configuration is invalid",
        ), 503)
    argv = (
        "--outcome", "subjective.day_rating",
        "--mode", "green-vs-non-green",
        *range_argv,
        "--min-n", "30",
        "--interactions", "none",
        "--top", "3",
    )
    return (argv, scope, fixture_socket), None


@bp.get("/readiness")
def readiness():
    allowed = {"range", "from", "to", "goal", "outcome"}
    range_argv, error = _range_args(allowed)
    if error is not None:
        return error

    argv = list(range_argv)
    goal = request.args.get("goal")
    outcome = request.args.get("outcome")
    if goal is not None:
        if GOAL_KEY.fullmatch(goal) is None:
            return _invalid_query("invalid_goal")
        argv.extend(("--goal", goal))
    if outcome is not None:
        if FEATURE_KEY.fullmatch(outcome) is None:
            return _invalid_query("invalid_outcome")
        argv.extend(("--outcome", outcome))
    return _engine("data-readiness", *argv)


def _cursor_args(allowed_keys, *, status=False, outcome=False):
    """Validate the shared immutable-ledger pagination contract."""
    supplied = set(request.args)
    if supplied - allowed_keys:
        return None, _invalid_query("unexpected_query_parameter")
    if any(len(request.args.getlist(key)) != 1 for key in supplied):
        return None, _invalid_query("duplicate_query_parameter")
    argv = []
    if status and "status" in supplied:
        value = request.args["status"]
        if value not in HYPOTHESIS_STATUSES:
            return None, _invalid_query("invalid_status")
        argv.extend(("--status", value))
    if outcome and "outcome" in supplied:
        value = request.args["outcome"]
        if FEATURE_KEY.fullmatch(value) is None:
            return None, _invalid_query("invalid_outcome")
        argv.extend(("--outcome", value))
    if "limit" in supplied:
        raw = request.args["limit"]
        if (
            len(raw) > 3
            or not raw.isascii()
            or not raw.isdigit()
            or not 1 <= int(raw) <= 100
        ):
            return None, _invalid_query("invalid_limit")
        argv.extend(("--limit", str(int(raw))))
    if "before" in supplied:
        value = request.args["before"]
        if SHA256_ID.fullmatch(value) is None:
            return None, _invalid_query("invalid_before")
        argv.extend(("--before", value))
    return argv, None


@bp.get("/hypotheses")
def hypotheses():
    """Canonical paginated hypothesis list; no panel-side evidence math."""
    argv, error = _cursor_args(
        {"status", "outcome", "limit", "before"}, status=True, outcome=True,
    )
    if error is not None:
        return error
    return _engine("hypotheses", *argv)


@bp.get("/hypotheses/<path:hypothesis_id>")
def hypothesis_detail(hypothesis_id):
    """One immutable ledger history, passed through from the canonical CLI."""
    if SHA256_ID.fullmatch(hypothesis_id) is None:
        return _invalid_query("invalid_hypothesis_id")
    if request.args:
        return _invalid_query("unexpected_query_parameter")
    return _engine("hypothesis-brief", hypothesis_id)


@bp.post("/hypotheses/promote")
@limiter.limit("6/minute")
def hypothesis_promote():
    """Identifier-only promotion; health.py recomputes before any write."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {
        "outcome", "finding_id", "input_fingerprint", "range",
    }:
        return _invalid_query("invalid_promotion")
    outcome = body.get("outcome")
    finding_id = body.get("finding_id")
    fingerprint = body.get("input_fingerprint")
    range_value = body.get("range")
    if not isinstance(outcome, str) or FEATURE_KEY.fullmatch(outcome) is None:
        return _invalid_query("invalid_outcome")
    if not isinstance(finding_id, str) or SHA256_ID.fullmatch(finding_id) is None:
        return _invalid_query("invalid_finding_id")
    if not isinstance(fingerprint, str) or SHA256_ID.fullmatch(fingerprint) is None:
        return _invalid_query("invalid_input_fingerprint")
    try:
        range_value = canonical_range(range_value)
    except CanonicalRangeError:
        return _invalid_query("invalid_range")
    if range_value["kind"] == "all":
        range_argv = ["--all"]
    else:
        range_argv = ["--from", range_value["from"], "--to", range_value["to"]]
    return _engine(
        "hypothesis-promote",
        "--outcome", outcome,
        "--finding-id", finding_id,
        "--input-fingerprint", fingerprint,
        *range_argv,
        # Preserve the write route's existing wait; broad read budgets are separate.
        timeout=190.0,
    )


@bp.get("/syntheses")
def syntheses():
    """Canonical structured synthesis history; legacy Markdown stays separate."""
    argv, error = _cursor_args({"limit", "before"})
    if error is not None:
        return error
    return _engine("synthesis-history", *argv)


@bp.get("/runs")
def insight_runs():
    """Read-only redacted Phase 6 batch/queue/outbox audit."""
    supplied = set(request.args)
    if supplied - {"limit"}:
        return _invalid_query("unexpected_query_parameter")
    if any(len(request.args.getlist(key)) != 1 for key in supplied):
        return _invalid_query("duplicate_query_parameter")
    argv = []
    if "limit" in supplied:
        raw = request.args["limit"]
        if (
            len(raw) > 3
            or not raw.isascii()
            or not raw.isdigit()
            or not 1 <= int(raw) <= 100
        ):
            return _invalid_query("invalid_limit")
        argv = ["--limit", str(int(raw))]
    return _engine("insight-run-status", *argv)


@bp.get("/correlations")
def correlations():
    if not _legacy_reference_enabled():
        return _legacy_route_quarantined("the governed outcome analysis API")
    # 365d window, min-n 10: strict enough to suppress noise, loose enough to
    # let early sparse data start showing up. The engine reports what it
    # suppressed, and the page shows that count.
    if request.args:
        return _invalid_query("legacy_endpoint_no_range")
    return _engine("correlate", "--days", "365", "--min-n", "10", "--top", "30")


@bp.get("/signature")
def signature():
    if not _legacy_reference_enabled():
        return _legacy_route_quarantined(
            "the governed dashboard Green-day analysis API"
        )
    if request.args:
        return _invalid_query("legacy_endpoint_no_range")
    return _engine("day-signature", "--days", "365")


@bp.get("/adherence")
def adherence():
    """Follow-through trend for the Insight Explorer card (no params, default
    90d window, unchanged) AND the Consistency page's "Kept your word" card,
    which now genuinely re-windows via the range dropdown (owner-review r2) —
    `days` is optional and whitelisted, same style as /api/dash/sleep."""
    rng = request.args.get("days")
    if rng is None:
        days = 90
    elif rng in ADHERENCE_ALLOWED_DAYS:
        days = ADHERENCE_ALLOWED_DAYS[rng]
    else:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    return _engine("adherence", "--days", str(days))


@bp.get("/timing")
def timing():
    """§4b timing-aware adherence — done-rate vs on-time-rate per metric.
    Window math, tolerances and streaks are all health.py's (no flags sent)."""
    return _engine("timing-adherence")


@bp.get("/coverage")
def coverage():
    return _engine("data-coverage", "--days", "90")


@bp.get("/brief")
def brief():
    """Test-only compatibility read for unvalidated vault Markdown."""
    if not _legacy_reference_enabled():
        return _legacy_route_quarantined("the governed synthesis history API")
    vault = current_app.config.get("VAULT_DIR")
    if not vault:
        return jsonify(brief=None)
    pdir = os.path.join(vault, "personal", "patterns")
    try:
        files = [f for f in os.listdir(pdir) if f.endswith(".md")]
        if not files:
            return jsonify(brief=None)
        newest = max(files, key=lambda f: os.path.getmtime(os.path.join(pdir, f)))
        path = os.path.join(pdir, newest)
        # Defense in depth: refuse symlinks that resolve outside the vault —
        # the RO bind mount stops writes, not reads through a planted link.
        real_vault = os.path.realpath(vault)
        if not os.path.realpath(path).startswith(real_vault + os.sep):
            return jsonify(brief=None)
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        # covers a missing patterns/ dir and delete races between listdir/stat
        return jsonify(brief=None)
    if len(content) > 40_000:
        content = content[:40_000] + "\n… (truncated)"
    return jsonify(brief={"file": newest, "content": content})
