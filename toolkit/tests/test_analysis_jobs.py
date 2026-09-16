"""Disposable-state regression tests for durable, bounded analysis jobs."""
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from hermes_insights import analysis_jobs as jobs, exact_cache

TOOLKIT = Path(__file__).resolve().parents[1]
REQUEST = {"command": "outcome-associations", "args": [
    "--outcome", "subjective.day_rating", "--from", "2026-01-01", "--to", "2026-01-31",
]}


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "health.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE fictional(value INTEGER)")
        connection.execute("INSERT INTO fictional VALUES(1)")
    monkeypatch.setenv("OPENHEALTHATLAS_ANALYSIS_DIR", str(tmp_path / "private-analysis"))
    monkeypatch.setenv("HEALTH_DB", str(path))
    monkeypatch.setenv("PYTHONPATH", str(TOOLKIT))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return path


def _wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("timed out waiting for isolated worker")


def _worker_script(tmp_path):
    script = tmp_path / "fictional_worker.py"
    script.write_text('''import json, os, pathlib, sys, time
from types import SimpleNamespace
from hermes_insights import analysis_jobs as jobs
jobs.JOB_TIMEOUT = int(os.environ.get("JOB_TEST_TIMEOUT", "570"))
def analyze(options):
    marker = pathlib.Path(os.environ["JOB_TEST_MARKER"])
    with marker.open("a") as stream:
        stream.write("executed\\n")
    time.sleep(float(os.environ.get("JOB_TEST_SLEEP", "0")))
    print(json.dumps({"ok": True, "fictional": True, "outcome": options.outcome}))
args = SimpleNamespace(job_id=sys.argv[2] if len(sys.argv)>2 else None,
                       attempt=sys.argv[3] if len(sys.argv)>3 else None)
jobs.cli(sys.argv[1], os.environ["HEALTH_DB"], args,
         {"outcome-associations": analyze}, __file__)
''')
    return script


@pytest.mark.parametrize("payload", [
    {"command": "query", "args": ["SELECT 1"]},
    {**REQUEST, "database": "/tmp/forbidden.db"},
    {**REQUEST, "args": ["--outcome", "subjective.day_rating", "--top", "101"]},
    {**REQUEST, "args": ["--outcome", "subjective.day_rating", "--all", "--days", "30"]},
    {**REQUEST, "args": ["--outcome", "subjective.day_rating", "--outcome", "x"]},
    {**REQUEST, "args": ["--outcome", "subjective.day_rating", "--config", "x"]},
    {"command": "finding-evidence", "args": ["--outcome", "subjective.day_rating", "--all"]},
])
def test_closed_request_validation_precedes_private_state(payload, tmp_path):
    with pytest.raises(jobs.JobError, match="."):
        jobs.start(tmp_path / "absent.db", payload)
    assert list(tmp_path.iterdir()) == []


def test_cross_process_submit_deduplicates_and_bounds_backlog(database):
    command = [sys.executable, str(TOOLKIT / "health.py"), "analysis-job-start"]
    children = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for _ in range(3)]
    for child in children:
        child.stdin.write(json.dumps(REQUEST))
        child.stdin.close()
        child.stdin = None
    results = []
    for child in children:
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr or stdout
        results.append(json.loads(stdout))
    assert len({item["job_id"] for item in results}) == 1
    assert {item["status"] for item in results} == {"queued"}
    for top in range(1, jobs.MAX_PENDING):
        jobs.start(database, {**REQUEST, "args": [*REQUEST["args"], "--top", str(top)]})
    with pytest.raises(jobs.JobError) as error:
        jobs.start(database, {**REQUEST, "args": [*REQUEST["args"], "--top", "99"]})
    assert error.value.code == "analysis_queue_full"
    assert exact_cache.store_path(database).stat().st_mode & 0o777 == 0o600
    assert exact_cache.cache_root(database).stat().st_mode & 0o777 == 0o700
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("fictional",)]


def test_completed_result_is_persistent_fresh_and_attempt_fenced(database, tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    monkeypatch.setenv("JOB_TEST_MARKER", str(marker))
    script = _worker_script(tmp_path)
    job = jobs.start(database, REQUEST)
    jobs.work(database, script)
    result = jobs.status(database, job["job_id"])
    assert result["status"] == "completed"
    assert result["result"]["fictional"] is True
    assert jobs.start(database, REQUEST)["job_id"] == job["job_id"]
    assert marker.read_text().splitlines() == ["executed"]
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE fictional SET value=2")
    stale = jobs.status(database, job["job_id"])
    assert stale["status"] == "stale"
    assert "result" not in stale
    assert jobs.start(database, REQUEST)["job_id"] != job["job_id"]


def test_pending_status_never_scans_health_or_returns_stored_result(database, monkeypatch):
    job = jobs.start(database, REQUEST)

    def forbidden(*args, **kwargs):
        raise AssertionError("pending status must not open or fingerprint health records")

    with monkeypatch.context() as patch:
        patch.setattr(jobs, "_snapshot", forbidden)
        patch.setattr(exact_cache, "current_identity", forbidden)
        patch.setattr(jobs.runtime, "connect_read_only", forbidden)
        for state in ("queued", "running"):
            with jobs._store(database) as connection:
                connection.execute(
                    "UPDATE analysis_jobs SET status=?,attempt=?,result=?,error=? WHERE job_id=?",
                    (state, "1" * 32, '{"forged":"private result"}',
                     '{"message":"private error"}', job["job_id"]),
                )
            assert jobs.status(database, job["job_id"]) == {
                "ok": True, "job_id": job["job_id"], "status": state, "request": REQUEST,
            }
    # Once completed, the original coherent freshness path still rejects
    # an otherwise correctly hashed result for changed canonical inputs.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE fictional SET value=2")
    jobs._publish(database, job["job_id"], "1" * 32, "completed", result={"fictional": True})
    result = jobs.status(database, job["job_id"])
    assert result["status"] == "stale"
    assert "result" not in result


def test_corrupted_stored_result_is_never_served(database, tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_TEST_MARKER", str(tmp_path / "marker"))
    job = jobs.start(database, REQUEST)
    jobs.work(database, _worker_script(tmp_path))
    with jobs._store(database) as connection:
        connection.execute("UPDATE analysis_jobs SET result=? WHERE job_id=?",
                           ('{"fictional":"tampered"}', job["job_id"]))
    response = jobs.status(database, job["job_id"])
    assert response["status"] == "failed"
    assert response["error"]["code"] == "analysis_result_invalid"
    assert "result" not in response
    assert jobs.start(database, REQUEST)["job_id"] != job["job_id"]


def test_child_runtime_is_bounded_without_relying_on_parent_timeout(database, tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_TEST_MARKER", str(tmp_path / "marker"))
    monkeypatch.setenv("JOB_TEST_TIMEOUT", "1")
    monkeypatch.setenv("JOB_TEST_SLEEP", "20")
    job = jobs.start(database, REQUEST)
    start = time.monotonic()
    jobs.work(database, _worker_script(tmp_path))
    response = jobs.status(database, job["job_id"])
    assert response["status"] == "failed"
    assert response["error"]["code"] == "analysis_timeout"
    assert time.monotonic() - start < 5


def test_child_keeps_execution_lock_after_supervisor_death(database, tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    monkeypatch.setenv("JOB_TEST_MARKER", str(marker))
    monkeypatch.setenv("JOB_TEST_SLEEP", "1.5")
    script = _worker_script(tmp_path)
    first = jobs.start(database, REQUEST)
    second = jobs.start(database, {**REQUEST, "args": [*REQUEST["args"], "--top", "3"]})
    supervisor = subprocess.Popen([sys.executable, str(script), "analysis-job-work"])
    try:
        _wait(marker.exists)
        supervisor.kill()
        supervisor.wait(timeout=3)
        # A new broker worker must neither recover the still-live attempt nor
        # execute the next queued request while the orphan holds the lock.
        jobs.work(database, script)
        assert jobs.status(database, second["job_id"])["status"] == "queued"
        assert marker.read_text().splitlines() == ["executed"]
        _wait(lambda: jobs.status(database, first["job_id"])["status"] == "completed")
        monkeypatch.setenv("JOB_TEST_SLEEP", "0")
        def drain_next():
            # Publication precedes process exit. Its inherited lock correctly
            # remains held until that exit; the broker retries on its next tick.
            jobs.work(database, script)
            return jobs.status(database, second["job_id"])["status"] == "completed"
        _wait(drain_next)
        assert marker.read_text().splitlines() == ["executed", "executed"]
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=3)


def test_claim_without_child_recovers_only_after_lock_is_available(database, tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    monkeypatch.setenv("JOB_TEST_MARKER", str(marker))
    script = _worker_script(tmp_path)
    job = jobs.start(database, REQUEST)
    with jobs._store(database) as connection:
        connection.execute("UPDATE analysis_jobs SET status='running',attempt=?,attempts=1 WHERE job_id=?",
                           ("1" * 32, job["job_id"]))
    # A late, older attempt must not publish into its running replacement.
    jobs._publish(database, job["job_id"], "0" * 32, "completed", result={"wrong_attempt": True})
    running = jobs.status(database, job["job_id"])
    assert running["status"] == "running"
    assert "result" not in running
    with exact_cache.computation_lock(database):
        jobs.work(database, script)
        assert jobs.status(database, job["job_id"])["status"] == "running"
    jobs.work(database, script)
    assert jobs.status(database, job["job_id"])["status"] == "completed"
    with jobs._store(database) as connection:
        row = connection.execute("SELECT attempt,attempts FROM analysis_jobs WHERE job_id=?", (job["job_id"],)).fetchone()
    assert row["attempt"] != "1" * 32
    assert row["attempts"] == 2


def test_changed_inputs_during_calculation_never_publish_result(database, tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    monkeypatch.setenv("JOB_TEST_MARKER", str(marker))
    monkeypatch.setenv("JOB_TEST_SLEEP", "0.5")
    script = _worker_script(tmp_path)
    job = jobs.start(database, REQUEST)
    supervisor = subprocess.Popen([sys.executable, str(script), "analysis-job-work"])
    try:
        _wait(marker.exists)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE fictional SET value=3")
        assert supervisor.wait(timeout=5) == 0
        result = jobs.status(database, job["job_id"])
        assert result["status"] == "stale"
        assert "result" not in result
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=3)


def test_broker_startup_resumes_queue_and_returns_result_over_authenticated_socket(database, tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    monkeypatch.setenv("JOB_TEST_MARKER", str(marker))
    script = _worker_script(tmp_path)
    job = jobs.start(database, REQUEST)
    pending = [jobs.start(database, {**REQUEST, "args": [*REQUEST["args"], "--top", str(top)]})
               for top in (1, 2)]
    # macOS AF_UNIX path limits require a short, exclusively reserved directory.
    with tempfile.TemporaryDirectory(prefix="oha-job-", dir="/private/tmp" if Path("/private/tmp").exists() else None) as directory:
        sock = str(Path(directory) / "broker.sock")
        process = subprocess.Popen(
            [sys.executable, str(TOOLKIT.parent / "deploy" / "hermes-bridge")],
            env={**os.environ, "BRIDGE_HEALTH": str(script), "BRIDGE_SOCK": sock,
                 "BRIDGE_AUDIT": str(tmp_path / "audit.jsonl"),
                 "BRIDGE_ALLOWED_UID": str(os.getuid())},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            _wait(lambda: Path(sock).exists())
            _wait(lambda: jobs.status(database, job["job_id"])["status"] == "completed")
            _wait(lambda: all(jobs.status(database, item["job_id"])["status"] == "completed"
                              for item in pending))
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(sock)
                connection.sendall((json.dumps({"subcmd": "analysis-job-status", "args": [job["job_id"]]}) + "\n").encode())
                response = json.loads(connection.makefile("rb").readline())
            assert response["ok"] is True
            assert json.loads(response["stdout"])["result"]["fictional"] is True
            assert marker.read_text().splitlines() == ["executed"] * 3
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
