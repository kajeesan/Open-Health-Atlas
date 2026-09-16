"""Real optional MCP client/server subprocess contracts; no model or provider."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

mcp = pytest.importorskip("mcp", reason="install requirements-mcp.txt for protocol verification")
from mcp.client.stdio import stdio_client
from toolkit.hermes_insights.contracts import canonical_json


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "openhealthatlas_mcp.py"


def _parameters(database, tmp_path):
    return mcp.StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER), "--database", str(database), "--timezone", "UTC"],
        cwd=str(tmp_path),
        env={"PYTHONDONTWRITEBYTECODE": "1", "OPENHEALTHATLAS_NATIVE_STATS": "off",
             # Explicit selection must override inherited deployment settings.
             "HEALTH_DB": str(tmp_path / "unrelated-never-created.db"),
             "HERMES_TIMEZONE": "Europe/Paris"},
    )


async def _call(session, name, arguments):
    response = await session.call_tool(name, arguments)
    assert len(response.content) == 1
    body = json.loads(response.content[0].text)
    assert body == response.structuredContent
    assert response.content[0].text == canonical_json(body)
    assert str(ROOT) not in response.content[0].text
    return body, response.isError


async def _finish(session, receipt):
    assert receipt["status"] in {"queued", "running", "completed"}
    task_id = receipt["task_id"]
    for _ in range(12):
        status, error = await _call(session, "health_task_status", {
            "task_id": task_id, "wait_seconds": 5,
        })
        assert not error
        if status["status"] == "completed":
            assert status["started_at"] <= status["completed_at"]
            return status["result"]
    pytest.fail("fictional task did not complete within one minute")


def test_real_stdio_catalog_query_analysis_evidence_and_refusal(tmp_path):
    database = tmp_path / "fictional.db"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_demo_db.py"),
         "--output", str(database), "--anchor-date", "2026-06-30"],
        check=True, capture_output=True, timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    async def exercise():
        async with stdio_client(_parameters(database, tmp_path)) as (reader, writer):
            async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "OpenHealthAtlas Local"
                listed = (await session.list_tools()).tools
                assert {tool.name for tool in listed} == {
                    "health_catalog", "health_query", "health_analyze",
                    "health_evidence", "health_task_status",
                }
                query_schema = next(t for t in listed if t.name == "health_query").inputSchema
                assert query_schema["properties"]["range"]["required"] == ["from", "to"]
                assert all(t.annotations.readOnlyHint for t in listed)

                bad, error = await _call(session, "health_query", {
                    "feature_keys": ["sleep.duration_hours"],
                    "range": {"start": "2026-06-24", "end": "2026-06-30"},
                    "database": "/untrusted/model/path",
                })
                assert error and bad["status"] == "refused"
                assert "/untrusted/model/path" not in json.dumps(bad)
                unknown, error = await _call(session, "write_health", {})
                assert error and unknown["status"] == "refused"

                catalog, error = await _call(session, "health_catalog", {"search": "sleep", "limit": 5})
                assert not error and catalog["status"] == "ok"
                assert catalog["contract"] == "openhealthatlas-surface-v1"
                assert "fixture_id" not in catalog
                assert catalog["data_class"] == "personal"

                query, error = await _call(session, "health_query", {
                    "feature_keys": ["sleep.duration_hours"],
                    "range": {"from": "2026-06-24", "to": "2026-06-30"},
                    "view": "summary",
                })
                assert not error and query["status"] == "ok"
                assert query["dataset_id"] == catalog["dataset_id"]
                assert query["range"] == {"from": "2026-06-24", "to": "2026-06-30"}
                assert query["facts"] and query["evidence_refs"]
                assert str(database) not in json.dumps(query)

                evidence_receipt, error = await _call(session, "health_evidence", {
                    "evidence_refs": query["evidence_refs"],
                })
                assert not error
                evidence = await _finish(session, evidence_receipt)
                assert evidence["status"] == "ok"
                assert evidence["dataset_id"] == query["dataset_id"]

                analysis_receipt, error = await _call(session, "health_analyze", {
                    "outcome_key": "subjective.energy", "exposure_keys": ["sleep.duration_hours"],
                    "range": {"from": "2026-06-24", "to": "2026-06-30"},
                    "mode": "ordinal", "top": 1,
                })
                assert not error
                analysis = await _finish(session, analysis_receipt)
                assert analysis["status"] == "insufficient_data"
                assert analysis["evidence_refs"]
                replay_receipt, error = await _call(session, "health_evidence", {
                    "evidence_refs": analysis["evidence_refs"][:1],
                })
                assert not error
                replay = await _finish(session, replay_receipt)
                assert replay["status"] == "insufficient_data"

                positive_receipt, error = await _call(session, "health_analyze", {
                    "outcome_key": "subjective.energy", "exposure_keys": ["sleep.duration_hours"],
                    "range": {"from": "2026-03-02", "to": "2026-06-30"},
                    "mode": "ordinal", "top": 1,
                })
                assert not error
                positive = await _finish(session, positive_receipt)
                assert positive["status"] == "ok" and positive["findings"]
                positive_replay, error = await _call(session, "health_evidence", {
                    "evidence_refs": positive["evidence_refs"][:1],
                })
                assert not error
                positive_evidence = await _finish(session, positive_replay)
                assert positive_evidence["status"] == "ok"
                assert positive_evidence["facts"][0]["verified"] is True

                invalid_receipt, error = await _call(session, "health_evidence", {
                    "evidence_refs": ["hash-only-is-invalid"],
                })
                assert not error
                invalid_completed, error = await _call(session, "health_task_status", {
                    "task_id": invalid_receipt["task_id"], "wait_seconds": 20,
                })
                assert error and invalid_completed["status"] == "completed"
                assert invalid_completed["result"]["status"] == "refused"

                oversized, error = await _call(session, "health_catalog", {"search": "x" * 131_073})
                assert error and oversized["status"] == "refused"

                invalid_status, error = await _call(session, "health_task_status", {
                    "task_id": analysis_receipt["task_id"], "wait_seconds": True,
                })
                assert error and invalid_status["status"] == "refused"

    asyncio.run(exercise())
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not (tmp_path / "unrelated-never-created.db").exists()


def test_real_stdio_database_failure_is_sanitized_and_session_survives(tmp_path):
    database = tmp_path / "fictional-broken.db"
    database.write_bytes(b"not-a-database")

    async def exercise():
        async with stdio_client(_parameters(database, tmp_path)) as (reader, writer):
            async with mcp.ClientSession(reader, writer) as session:
                await session.initialize()
                result, error = await _call(session, "health_catalog", {})
                assert error and result["status"] == "refused"
                assert str(tmp_path) not in json.dumps(result)
                assert "Traceback" not in json.dumps(result)
                assert len((await session.list_tools()).tools) == 5

    asyncio.run(exercise())
    assert database.read_bytes() == b"not-a-database"


def test_startup_requires_existing_database_without_initializing(tmp_path):
    database = tmp_path / "not-created.db"
    result = subprocess.run(
        [sys.executable, str(SERVER), "--database", str(database)],
        capture_output=True, text=True, timeout=10, cwd=tmp_path,
    )
    assert result.returncode == 2 and result.stdout == ""
    assert str(database) not in result.stderr
    assert "Traceback" not in result.stderr
    assert not database.exists()


def test_real_stdio_deduplicates_work_and_reaps_worker_on_disconnect(tmp_path):
    database = tmp_path / "fictional.db"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_demo_db.py"),
         "--output", str(database), "--anchor-date", "2026-06-30"],
        check=True, capture_output=True, timeout=60,
    )
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    pid_file = tmp_path / "owned-worker.pid"
    launcher = tmp_path / "launch.py"
    launcher.write_text(
        "import asyncio, runpy, sys\nfrom pathlib import Path\n"
        "spawn = asyncio.create_subprocess_exec\n"
        "async def record(*args, **kwargs):\n"
        "    process = await spawn(*args, **kwargs)\n"
        f"    Path({str(pid_file)!r}).write_text(str(process.pid))\n"
        "    return process\n"
        "asyncio.create_subprocess_exec = record\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    parameters = _parameters(database, tmp_path)
    parameters.args.insert(0, str(launcher))
    worker_pids = []

    async def exercise():
        async with stdio_client(parameters) as (reader, writer):
            async with mcp.ClientSession(reader, writer) as session:
                await session.initialize()
                arguments = {
                    "outcome_key": "subjective.energy", "exposure_keys": ["sleep.duration_hours"],
                    "range": {"from": "2026-03-02", "to": "2026-06-30"},
                    "mode": "ordinal", "top": 1,
                }
                first, error = await _call(session, "health_analyze", arguments)
                assert not error and first["status"] in {"queued", "running"}
                repeated, error = await _call(session, "health_analyze", arguments)
                assert not error and repeated["task_id"] == first["task_id"]
                busy, error = await _call(session, "health_analyze", {**arguments, "top": 2})
                assert error and busy["status"] == "refused"
                status, error = await _call(session, "health_task_status", {
                    "task_id": first["task_id"], "wait_seconds": 0,
                })
                assert not error and status["status"] in {"queued", "running"}
                assert len((await session.list_tools()).tools) == 5
                for _ in range(100):
                    if pid_file.exists():
                        break
                    await asyncio.sleep(0.02)
                worker_pids.append(int(pid_file.read_text()))


    asyncio.run(exercise())
    for pid in worker_pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_oversize_worker_output_is_bounded_and_reaped(tmp_path, monkeypatch):
    module = runpy.run_path(str(SERVER), run_name="local_mcp_test")
    real_spawn = asyncio.create_subprocess_exec
    owned_pids = []

    async def oversized_worker(*args, **kwargs):
        # Substitute only the executable's behavior; pipes, backpressure and
        # process cleanup are real. This reproduces the full-pipe shutdown bug.
        process = await real_spawn(
            sys.executable, "-c",
            "import os,sys; sys.stdin.buffer.read(); os.write(1,b'x'*3000000)",
            **kwargs,
        )
        owned_pids.append(process.pid)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", oversized_worker)

    async def exercise():
        tasks = module["AnalysisTasks"](tmp_path / "unused.db", "UTC")
        receipt = tasks.submit({"operation": "health_analyze", "arguments": {}})
        runner = tasks.tasks[receipt["task_id"]]["runner"]
        await asyncio.wait_for(runner, timeout=5)
        status = tasks.status(receipt["task_id"])
        assert status["status"] == "failed"
        assert status["completed_at"] and "result" not in status
        await tasks.close()

    asyncio.run(exercise())
    for pid in owned_pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
