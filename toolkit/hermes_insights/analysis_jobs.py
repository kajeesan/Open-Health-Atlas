"""Private, installation-owned analysis queue; only health.py writes its state.

The broker owns a small worker thread, not another service. A persistent flock
is inherited by the calculation child, so killing a supervisor cannot permit
another calculation while its child is alive. SQLite attempt IDs fence result
publication. No job can choose an executable, database, path, or arbitrary flag.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

from . import associations, events, runtime
from .contracts import canonical_json

MAX_PENDING = 4
MAX_RETAINED = 64
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 8192
JOB_TIMEOUT = 570
JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
LOCK_ENV = "OHA_ANALYSIS_LOCK_FD"
COMMANDS = {"outcome-associations", "finding-evidence"}
RANGE_FLAGS = {"--from", "--to", "--days", "--all"}


class JobError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def validate_request(value):
    """Closed argv grammar plus the existing deterministic scalar validation."""
    if not isinstance(value, dict) or set(value) != {"command", "args"}:
        raise JobError("validation_error", "analysis request requires command and args")
    command, args = value["command"], value["args"]
    if not isinstance(command, str) or command not in COMMANDS:
        raise JobError("validation_error", "unsupported analysis command")
    if (not isinstance(args, list) or len(args) > 24
            or any(not isinstance(item, str) or len(item) > 200 for item in args)):
        raise JobError("validation_error", "invalid analysis arguments")
    allowed = RANGE_FLAGS | {"--outcome"}
    allowed |= ({"--mode", "--min-n", "--interactions", "--top"}
                if command == "outcome-associations" else
                {"--finding-id", "--input-fingerprint"})
    values = {}
    index = 0
    while index < len(args):
        flag = args[index]
        if flag not in allowed or flag in values:
            raise JobError("validation_error", "unknown or repeated analysis flag")
        if flag == "--all":
            values[flag] = True
        else:
            index += 1
            if index >= len(args) or args[index].startswith("--"):
                raise JobError("validation_error", "missing analysis flag value")
            values[flag] = args[index]
        index += 1
    try:
        days = int(values["--days"]) if "--days" in values else (
            365 if not RANGE_FLAGS.intersection(values) else None)
        minimum = int(values.get("--min-n", associations.DEFAULT_MIN_N))
        top = int(values.get("--top", associations.DEFAULT_TOP))
        events.resolve_range(from_date=values.get("--from"),
                             to_date=values.get("--to"), days=days,
                             all_dates=values.get("--all", False))
        associations.validate_options(
            outcome_key=values.get("--outcome"), mode=values.get("--mode", "all"),
            min_n=minimum, interactions=values.get("--interactions", "none"), top=top,
        )
        if command == "finding-evidence":
            for flag in ("--finding-id", "--input-fingerprint"):
                associations.validate_sha256_id(values.get(flag), flag[2:])
    except (ValueError, RuntimeError) as exc:
        raise JobError("validation_error", str(exc)) from exc
    namespace = argparse.Namespace(
        outcome=values["--outcome"], from_date=values.get("--from"),
        to_date=values.get("--to"), days=days, all_dates=values.get("--all", False),
        mode=values.get("--mode", "all"), min_n=minimum,
        interactions=values.get("--interactions", "none"), top=top,
        finding_id=values.get("--finding-id"),
        input_fingerprint=values.get("--input-fingerprint"),
    )
    return {"command": command, "args": list(args)}, namespace


def _cache():
    # Imported lazily: validating a refused request never touches private state.
    from . import exact_cache
    return exact_cache


def _identity(database, *, connection=None, context=None):
    return canonical_json(_cache().current_identity(database, connection=connection, context=context))


@contextmanager
def _snapshot(database):
    context = runtime.adapter_context()
    connection = runtime.connect_read_only(str(database))
    try:
        connection.execute("BEGIN")
        yield connection, context
    finally:
        connection.close()


@contextmanager
def _store(database):
    connection = _cache().connect_store(database)
    connection.execute("""CREATE TABLE IF NOT EXISTS analysis_jobs (
        job_id TEXT PRIMARY KEY, request TEXT NOT NULL, request_key TEXT NOT NULL,
        identity TEXT NOT NULL, status TEXT NOT NULL, attempt TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
        updated REAL NOT NULL, result TEXT, result_sha256 TEXT, error TEXT
    )""")
    connection.commit()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _view(row):
    response = {"ok": True, "job_id": row["job_id"], "status": row["status"],
                "request": json.loads(row["request"])}
    if row["status"] == "completed" and row["result"] is not None:
        response["result"] = json.loads(row["result"])
    if row["error"] is not None:
        response["error"] = json.loads(row["error"])
    return response


def _response(database, row, source, context):
    response = _view(row)
    if response["status"] == "completed":
        # Verify the persisted bytes before touching the envelope. Collector
        # ages remain current without repeating expensive association work.
        response["result"] = _cache().refresh_readiness(
            database, response["result"], connection=source, context=context)
    return response


def _verify_result(connection, row):
    if row["status"] != "completed":
        return row
    try:
        raw = row["result"].encode("utf-8")
        valid = (len(raw) <= MAX_RESULT_BYTES and
                 hashlib.sha256(raw).hexdigest() == row["result_sha256"] and
                 canonical_json(json.loads(raw)).encode("utf-8") == raw)
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        connection.execute(
            "UPDATE analysis_jobs SET status='failed',result=NULL,result_sha256=NULL,error=? WHERE job_id=?",
            (canonical_json({"code": "analysis_result_invalid", "message": "Stored result is invalid; start a new analysis."}),
             row["job_id"]),
        )
        return connection.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (row["job_id"],)).fetchone()
    return row


def _stale(connection, job_id):
    connection.execute(
        "UPDATE analysis_jobs SET status='stale',result=NULL,error=?,updated=? WHERE job_id=?",
        (canonical_json({"code": "stale_job", "message": "Inputs changed; start a new analysis."}),
         time.time(), job_id),
    )


def start(database, request):
    validate_request(request)  # reject before opening even the canonical DB
    with _snapshot(database) as (source, context):
        return _start(database, request, source, context)


def _start(database, request, source, context):
    request, options = validate_request(request)
    identity = _identity(database, connection=source, context=context)
    # Defaults and argument order share a dedup key; the response preserves the
    # submitted descriptor so consumers can independently validate its scope.
    key = hashlib.sha256(canonical_json({
        "command": request["command"], "options": vars(options), "identity": identity,
    }).encode()).hexdigest()
    with _store(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM analysis_jobs WHERE request_key=? AND status IN "
            "('queued','running','completed') ORDER BY created DESC LIMIT 1", (key,),
        ).fetchone()
        if row:
            row = _verify_result(connection, row)
        if row and row["status"] in {"queued", "running", "completed"}:
            return _response(database, row, source, context)
        # Obsolete queued/completed jobs never block the current input revision.
        for row in connection.execute(
                "SELECT job_id FROM analysis_jobs WHERE identity<>? AND status IN ('queued','completed')",
                (identity,)).fetchall():
            _stale(connection, row["job_id"])
        count = connection.execute(
            "SELECT count(*) FROM analysis_jobs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        if count >= MAX_PENDING:
            raise JobError("analysis_queue_full", "Analysis queue is full; wait for a running analysis.")
        job_id = uuid.uuid4().hex
        now = time.time()
        connection.execute(
            "INSERT INTO analysis_jobs(job_id,request,request_key,identity,status,created,updated) "
            "VALUES(?,?,?,?,'queued',?,?)",
            (job_id, canonical_json(request), key, identity, now, now),
        )
        connection.execute(
            "DELETE FROM analysis_jobs WHERE status NOT IN ('queued','running') AND job_id NOT IN "
            "(SELECT job_id FROM analysis_jobs ORDER BY created DESC LIMIT ?)", (MAX_RETAINED,),
        )
        return _view(connection.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone())


def status(database, job_id):
    if not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None:
        raise JobError("validation_error", "invalid analysis job identifier")
    # Pending state is only a queue observation, never a freshness claim.
    # Avoid repeatedly scanning canonical records while the bounded analysis
    # owns the CPU. Select no result/error bytes, even from a damaged row.
    with _store(database) as connection:
        pending = connection.execute(
            "SELECT job_id,status,request FROM analysis_jobs "
            "WHERE job_id=? AND status IN ('queued','running')", (job_id,),
        ).fetchone()
    if pending is not None:
        request, _options = validate_request(json.loads(pending["request"]))
        return {"ok": True, "job_id": pending["job_id"],
                "status": pending["status"], "request": request}
    with _snapshot(database) as (source, context):
        return _status(database, job_id, source, context)


def _status(database, job_id, source, context):
    identity = _identity(database, connection=source, context=context)
    with _store(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobError("job_not_found", "Analysis job was not found; start a new analysis.")
        if row["identity"] != identity and row["status"] != "stale":
            _stale(connection, job_id)
            row = connection.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
        return _response(database, _verify_result(connection, row), source, context)


def _publish(database, job_id, attempt, state, *, result=None, error=None):
    raw = canonical_json(result) if result is not None else None
    with _store(database) as connection:
        connection.execute(
            "UPDATE analysis_jobs SET status=?,result=?,result_sha256=?,error=?,updated=? "
            "WHERE job_id=? AND attempt=? AND status='running'",
            (state, raw, hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else None,
             canonical_json(error) if error else None, time.time(), job_id, attempt),
        )


class _BoundedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.bytes = 0

    def write(self, value):
        self.bytes += len(value.encode("utf-8"))
        if self.bytes > MAX_RESULT_BYTES:
            raise JobError("analysis_output_limit", "Analysis response exceeded its output limit.")
        return super().write(value)


def execute(database, job_id, attempt, handlers):
    """Internal child: only a claimed stored request can select a fixed handler."""
    if JOB_ID.fullmatch(job_id) is None or JOB_ID.fullmatch(attempt) is None:
        raise JobError("validation_error", "invalid internal analysis attempt")
    # Fail closed outside the supervisor. The descriptor must be its inherited
    # lock inode; no request can inject this environment or an arbitrary path.
    if LOCK_ENV not in os.environ:
        raise JobError("validation_error", "analysis execution lock is missing")
    with _cache().computation_lock(database):
        _execute_locked(database, job_id, attempt, handlers)


def _execute_locked(database, job_id, attempt, handlers):
    # SIGALRM remains effective if the supervisor is killed; no orphan computes
    # indefinitely while holding the inherited global lock.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(JOB_TIMEOUT)
    with _store(database) as connection:
        row = connection.execute(
            "SELECT * FROM analysis_jobs WHERE job_id=? AND attempt=? AND status='running'",
            (job_id, attempt),
        ).fetchone()
    if row is None:
        return
    try:
        if row["identity"] != _identity(database):
            _publish(database, job_id, attempt, "stale", error={
                "code": "stale_job", "message": "Inputs changed; start a new analysis."})
            return
        request, options = validate_request(json.loads(row["request"]))
        output = _BoundedOutput()
        with redirect_stdout(output):
            handlers[request["command"]](options)
        result = json.loads(output.getvalue())
        if row["identity"] != _identity(database):
            _publish(database, job_id, attempt, "stale", error={
                "code": "stale_job", "message": "Inputs changed; start a new analysis."})
        else:
            _publish(database, job_id, attempt, "completed", result=result)
    except Exception as exc:
        # Errors may embed SQL, free text or private paths; return only a
        # code-owned message. Existing structured stale-finding remains useful.
        code = getattr(exc, "code", "analysis_failed")
        code = code if re.fullmatch(r"[a-z_]{1,64}", str(code)) else "analysis_failed"
        _publish(database, job_id, attempt, "failed", error={
            "code": code, "message": "Analysis could not complete; retry the analysis."})
    finally:
        signal.alarm(0)


def work(database, health_script):
    """Run at most one queued request; return whether the queue made progress."""
    with _store(database):
        pass
    try:
        with _cache().computation_lock(database) as fd:
            return _work_locked(database, health_script, fd)
    except _cache().AnalysisBusy:
        return False


def _work_locked(database, health_script, fd):
    try:
        with _store(database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Acquiring the never-unlinked OS lock proves every earlier
            # execution child has exited, even across broker restarts.
            connection.execute(
                "UPDATE analysis_jobs SET status=CASE WHEN attempts<2 THEN 'queued' ELSE 'failed' END, "
                "error=CASE WHEN attempts<2 THEN NULL ELSE ? END, updated=? WHERE status='running'",
                (canonical_json({"code": "analysis_interrupted", "message": "Analysis was interrupted; retry."}),
                 time.time()),
            )
            row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            attempt = uuid.uuid4().hex
            connection.execute(
                "UPDATE analysis_jobs SET status='running',attempt=?,attempts=attempts+1,updated=? WHERE job_id=?",
                (attempt, time.time(), row["job_id"]),
            )
        child = subprocess.Popen(
            [sys.executable, str(health_script), "analysis-job-execute", row["job_id"], attempt],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, LOCK_ENV: str(fd)}, pass_fds=(fd,),
        )
        try:
            code = child.wait(timeout=JOB_TIMEOUT + 5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            code = 124
        if code:
            _publish(database, row["job_id"], attempt, "failed", error={
                "code": "analysis_timeout" if code in (124, -signal.SIGALRM) else "analysis_failed",
                "message": "Analysis exceeded its runtime limit." if code in (124, -signal.SIGALRM)
                else "Analysis could not complete; retry the analysis.",
            })
        return True
    except OSError:
        # A claim committed before a failed spawn remains recoverable by the
        # next worker, which must again prove the global execution lock is free.
        return False


def cli(action, database, args, handlers, health_script):
    try:
        if action == "analysis-job-start":
            raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
            if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise JobError("validation_error", "analysis request is too large")
            try:
                request = json.loads(raw)
            except ValueError as exc:
                raise JobError("validation_error", "invalid analysis request JSON") from exc
            result = start(database, request)
        elif action == "analysis-job-status":
            result = status(database, args.job_id)
        elif action == "analysis-job-work":
            result = {"ok": True, "processed": work(database, health_script)}
        else:
            execute(database, args.job_id, args.attempt, handlers)
            return
        print(canonical_json(result))
    except (JobError, ValueError, OSError, sqlite3.Error) as exc:
        print(canonical_json({"ok": False, "error": {
            "code": getattr(exc, "code", "analysis_unavailable"),
            "message": str(exc) if isinstance(exc, JobError) else "Analysis state is unavailable.",
        }}))
        raise SystemExit(2 if getattr(exc, "code", None) == "validation_error" else 1)
