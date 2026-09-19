#!/usr/bin/env python3
"""Exercise a built MCP executable over real stdio using disposable fictional data.

Run with a verification Python environment containing requirements-mcp.txt.
The server under test uses only its own bundled runtime. No model is contacted.
Include --desktop-workspace to verify that unchanged client configuration
follows the selected workspace across a copied-generation upgrade.
Use --argument scripts/openhealthatlas_mcp.py with --executable /path/to/python
only for a source baseline; that does not verify the packaged runtime.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = {
    "outcome_key": "subjective.energy",
    "exposure_keys": ["sleep.duration_hours"],
    "range": {"from": "2026-03-02", "to": "2026-06-30"},
    "mode": "ordinal", "top": 1,
}
QUERY = {
    "feature_keys": ["sleep.duration_hours"],
    "range": {"from": "2026-06-24", "to": "2026-06-30"},
    "view": "summary",
}


class AcceptanceFailure(Exception):
    """A fixed, non-sensitive assertion message safe to publish."""


def require(condition, message):
    if not condition:
        raise AcceptanceFailure(message)


def safe_failure(exc):
    if isinstance(exc, AcceptanceFailure):
        return str(exc)
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            if message := safe_failure(child):
                return message
    return None


def fixture_processes(directory: Path) -> dict[int, str]:
    """Observe only processes whose arguments name this random test directory."""
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="], capture_output=True,
            text=True, check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AcceptanceFailure("Process inspection unavailable; worker cleanup is unverified") from exc
    return {
        int(fields[0]): fields[1]
        for line in result.stdout.splitlines()
        if len(fields := line.strip().split(None, 1)) == 2
        and str(directory) in fields[1]
        and int(fields[0]) != os.getpid()
    }


async def call(session, name, arguments, directory):
    response = await session.call_tool(name, arguments)
    require(len(response.content) == 1, "Expected one structured MCP response")
    body = json.loads(response.content[0].text)
    require(body == response.structuredContent, "MCP text and structured responses differ")
    require(str(directory) not in response.content[0].text, "MCP response disclosed a local path")
    return body, response.isError


async def finish(session, receipt, directory, timeout):
    require(receipt.get("status") in {"queued", "running", "completed"}, "Missing task receipt")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, error = await call(session, "health_task_status", {
            "task_id": receipt["task_id"], "wait_seconds": 5,
        }, directory)
        require(not error, "Task failed or was refused")
        if status["status"] == "completed":
            require(status["started_at"] <= status["completed_at"], "Invalid task timestamps")
            return status["result"]
        require(status["status"] in {"queued", "running"}, "Unexpected task state")
    raise AcceptanceFailure("Fictional analysis exceeded verification timeout")


async def exercise(parameters, directory, timeout, checks):
    import mcp
    from mcp.client.stdio import stdio_client

    async with stdio_client(parameters) as (reader, writer):
        async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
            initialized = await session.initialize()
            require(initialized.serverInfo.name == "OpenHealthAtlas Local", "Unexpected MCP server")
            listed = (await session.list_tools()).tools
            require({tool.name for tool in listed} == {
                "health_catalog", "health_query", "health_analyze",
                "health_evidence", "health_task_status",
            }, "Incorrect MCP tool discovery")
            require(all(tool.annotations.readOnlyHint for tool in listed), "Missing read-only hints")
            checks.append("initialize_and_discover")

            catalog, error = await call(session, "health_catalog", {"search": "sleep", "limit": 5}, directory)
            require(not error and catalog["status"] == "ok", "Catalog failed")
            query, error = await call(session, "health_query", QUERY, directory)
            require(not error and query["status"] == "ok", "Query failed")
            require(query["facts"] and query["evidence_refs"], "Query has no facts or evidence")
            require(query["dataset_id"] == catalog["dataset_id"], "Dataset identity changed")
            receipt, error = await call(session, "health_evidence", {"evidence_refs": query["evidence_refs"]}, directory)
            require(not error, "Query evidence was refused")
            evidence = await finish(session, receipt, directory, timeout)
            require(evidence["status"] == "ok" and evidence["facts"][0]["verified"], "Query evidence failed")
            checks.append("query_and_evidence")

            receipt, error = await call(session, "health_analyze", ANALYSIS, directory)
            require(not error, "Analysis was refused")
            result = await finish(session, receipt, directory, timeout)
            require(result["status"] == "ok" and result["findings"], "Expected positive fictional analysis")
            receipt, error = await call(session, "health_evidence", {"evidence_refs": result["evidence_refs"][:1]}, directory)
            require(not error, "Analysis evidence was refused")
            evidence = await finish(session, receipt, directory, timeout)
            require(evidence["status"] == "ok" and evidence["facts"][0]["verified"] is True, "Analysis evidence failed")
            checks.extend(["positive_analysis", "task_status", "analysis_evidence"])

    # A separate connection drops an in-flight real worker. A new exposure
    # avoids making this merely a completed cached-result shutdown check.
    async with stdio_client(parameters) as (reader, writer):
        async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            receipt, error = await call(session, "health_analyze", {
                **ANALYSIS, "exposure_keys": ["sleep.duration_hours", "wearable.hrv_ms"],
            }, directory)
            require(not error, "Disconnect test did not start a task")
            deadline = time.monotonic() + 5
            workers = {}
            while time.monotonic() < deadline:
                workers = {pid: command for pid, command in fixture_processes(directory).items()
                           if "--worker" in command}
                if workers:
                    break
                await asyncio.sleep(0.01)
            require(workers, "Could not observe a real analysis worker before disconnect")
            status, error = await call(session, "health_task_status", {
                "task_id": receipt["task_id"], "wait_seconds": 0,
            }, directory)
            require(not error and status["status"] in {"queued", "running"}, "Worker finished before cancellation was exercised")
    deadline = time.monotonic() + 10
    while fixture_processes(directory) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    require(not fixture_processes(directory), "MCP child process survived disconnect")
    checks.append("in_flight_disconnect_cleanup")
    return query


def select_generation(workspace, generation):
    metadata = workspace / "workspace.next.json"
    metadata.write_text(json.dumps({"version": 1, "kind": "demo", "generation": generation}), encoding="utf-8")
    metadata.replace(workspace / "workspace.json")


async def verify_generation_reconnect(parameters, directory, previous, checks):
    import mcp
    from mcp.client.stdio import stdio_client

    # The exact same client parameters must resolve the newly active generation.
    async with stdio_client(parameters) as (reader, writer):
        async with mcp.ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            current, error = await call(session, "health_query", QUERY, directory)
            require(not error and current["status"] == "ok", "Query after workspace upgrade failed")
            require(current["dataset_id"] != previous["dataset_id"], "Client still selected the old generation")
            require(current["facts"] == previous["facts"], "Query values changed after generation copy")
            require(current["coverage"] == previous["coverage"], "Query record counts changed after generation copy")
    require(not fixture_processes(directory), "MCP process survived upgrade verification")
    checks.append("stable_workspace_reconnect_after_generation_change")


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--argument", action="append", default=[], help="Prefix argument; repeat if needed")
    parser.add_argument("--desktop-workspace", action="store_true",
                        help="Exercise the stable desktop workspace selector across a generation change")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    checks = []
    try:
        import mcp

        require(args.timeout_seconds > 0, "Timeout must be positive")
        # Preserve a venv interpreter symlink for the optional source baseline.
        # Resolving it would silently select the base interpreter without SDK.
        executable = args.executable.expanduser().absolute()
        require(executable.is_file(), "Selected executable does not exist")
        require(os.access(executable, os.X_OK), "Selected file is not executable")
        with tempfile.TemporaryDirectory(prefix="oha-mcp-acceptance-") as temporary:
            directory = Path(temporary).resolve()
            database = directory / "fictional.db"
            workspace = directory / "fictional-workspace"
            if args.desktop_workspace:
                generation = secrets.token_hex(16)
                database = workspace / "generations" / generation / "health.db"
                database.parent.mkdir(parents=True)
                select_generation(workspace, generation)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "make_demo_db.py"),
                 "--output", str(database), "--anchor-date", "2026-06-30"],
                capture_output=True, check=True, timeout=90,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            selector = ["--workspace", str(workspace)] if args.desktop_workspace else ["--database", str(database)]
            parameters = mcp.StdioServerParameters(
                command=str(executable),
                args=[*args.argument, *selector, "--timezone", "UTC"],
                cwd=str(directory),
                env={"PATH": "/usr/bin:/bin", "HOME": str(directory),
                     "PYTHONDONTWRITEBYTECODE": "1", "OPENHEALTHATLAS_NATIVE_STATS": "off",
                     "HEALTH_DB": str(directory / "unrelated-never-created.db"),
                     "HERMES_TIMEZONE": "Europe/Paris"},
            )
            previous = asyncio.run(exercise(parameters, directory, args.timeout_seconds, checks))
            if args.desktop_workspace:
                next_generation = secrets.token_hex(16)
                next_database = workspace / "generations" / next_generation / "health.db"
                next_database.parent.mkdir()
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(next_database)) as target:
                        source.backup(target)
                copied_before = hashlib.sha256(next_database.read_bytes()).hexdigest()
                select_generation(workspace, next_generation)
                asyncio.run(verify_generation_reconnect(parameters, directory, previous, checks))
                require(hashlib.sha256(next_database.read_bytes()).hexdigest() == copied_before,
                        "MCP changed records in the new generation")
            require(hashlib.sha256(database.read_bytes()).hexdigest() == before, "MCP modified canonical records")
            require(not (directory / "unrelated-never-created.db").exists(), "Inherited database selection was used")
            checks.extend(["canonical_database_unchanged", "explicit_database_selection"])
        print(json.dumps({"status": "passed", "fixture": "comprehensive-persona-v1",
                          "requested_timezone": "UTC",
                          "selector": "workspace" if args.desktop_workspace else "database",
                          "mode": "source" if args.argument else "executable",
                          "elapsed_seconds": round(time.monotonic() - started, 2),
                          "checks": checks}))
        return 0
    except Exception as exc:
        # Subprocess and SDK exception messages may embed host paths. Keep the
        # portable report safe for CI artifacts; local stderr still shows server
        # diagnostics controlled by the production server itself.
        print(json.dumps({"status": "failed", "completed_checks": checks,
                          "elapsed_seconds": round(time.monotonic() - started, 2),
                          "error": safe_failure(exc) or
                          "MCP acceptance failed; inspect local verification environment and server diagnostics"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
