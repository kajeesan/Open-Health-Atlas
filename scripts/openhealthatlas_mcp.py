#!/usr/bin/env python3
"""Local stdio MCP access to bounded OpenHealthAtlas evidence.

The caller selects an existing local database at startup. No model, network
listener, provider credentials, database initialization or migration runs here.
Install requirements-mcp.txt separately from the ordinary product dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone as utc_timezone
import json
import hashlib
import os
import secrets
import signal
import time
from pathlib import Path
import sys
from typing import Any


MAX_REQUEST_BYTES = 131_072
MAX_RESULT_BYTES = 1_048_576
MAX_TOOL_BYTES = MAX_RESULT_BYTES + 4096
WORKER_TIMEOUT_SECONDS = 570

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "toolkit"))


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


def _strings(maximum: int, minimum: int = 0) -> dict:
    return {"type": "array", "items": {"type": "string"},
            "minItems": minimum, "maxItems": maximum, "uniqueItems": True}


DATE_RANGE = _object({
    "from": {"type": "string", "format": "date",
             "description": "Inclusive start, YYYY-MM-DD."},
    "to": {"type": "string", "format": "date",
           "description": "Inclusive end, YYYY-MM-DD; at most 366 days."},
}, ("from", "to"))
EVIDENCE_REFERENCE = _object({
    "contract": {"type": "string", "const": "openhealthatlas-evidence-ref-v1"},
    "operation": {"type": "string", "enum": [
        "health_catalog", "health_query", "health_analyze",
    ]},
    "arguments": {"type": "object",
                  "description": "Copy the complete returned arguments unchanged."},
    "result_id": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    "finding_id": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$",
                   "description": "Copy only when present in the reference."},
}, ("contract", "operation", "arguments", "result_id"))

TOOL_SCHEMAS = {
    "health_catalog": _object({
        "search": {"type": "string", "maxLength": 80},
        "domains": _strings(12), "roles": _strings(8),
        "availability": {**_strings(4), "items": {"type": "string", "enum": [
            "implemented", "not_implemented", "connected", "not_connected",
        ]}},
        "cursor": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
    }),
    "health_query": _object({
        "feature_keys": _strings(32, 1), "range": DATE_RANGE,
        "view": {"type": "string", "enum": [
            "latest", "series", "summary", "period_compare",
        ], "default": "latest"},
        "grain": {"type": "string", "enum": ["day"]},
        "aggregates": {**_strings(7), "items": {"type": "string", "enum": [
            "count", "min", "max", "mean", "median", "sum", "stddev",
        ]}},
    }, ("feature_keys", "range")),
    "health_analyze": _object({
        "outcome_key": {"type": "string"}, "exposure_keys": _strings(32, 1),
        "range": DATE_RANGE,
        "mode": {"type": "string", "enum": [
            "all", "ordinal", "green-vs-non-green", "red-vs-non-red",
        ], "default": "all"},
        "interactions": {"type": "boolean", "default": False},
        "min_n": {"type": "integer", "minimum": 30, "maximum": 500, "default": 30},
        "top": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
    }, ("outcome_key", "exposure_keys", "range")),
    "health_evidence": _object({
        "evidence_refs": {"type": "array", "minItems": 1, "maxItems": 20,
                          "items": EVIDENCE_REFERENCE},
        "detail": {"type": "string", "enum": ["summary", "lineage"],
                   "default": "summary"},
    }, ("evidence_refs",)),
}
TOOL_DESCRIPTIONS = {
    "health_catalog": "Discover registered health features and availability. No SQL or paths.",
    "health_query": "Read registered features as latest, daily series, summary or period comparison. Preserves source, time, units and missingness.",
    "health_analyze": "Calculate bounded deterministic associations for an outcome and explicit exposures. Associations do not establish causation.",
    "health_evidence": "Replay one to 20 complete evidence_refs objects from earlier results before explaining them. Copy contract, operation, arguments, result_id and optional finding_id unchanged. At most one analysis reference. Hash strings alone are invalid.",
}
DEFAULTS = {
    "health_catalog": {"limit": 20},
    "health_query": {"view": "latest"},
    "health_analyze": {"mode": "all", "interactions": False, "min_n": 30, "top": 10},
    "health_evidence": {"detail": "summary"},
}


def encode_result(value):
    from hermes_insights.contracts import canonical_json
    return canonical_json(value)


def execute_request(surface, request):
    from hermes_insights.hermes_surface import SurfaceError

    try:
        result = surface.execute(request)
        encoded = encode_result(result)
        if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("response too large")
        return result
    except SurfaceError as exc:
        return {"status": "refused", "error": {"code": exc.code, "message": str(exc)}}
    except Exception:
        return {"status": "refused", "error": "local evidence request failed closed"}


class AnalysisTasks:
    """One owned process, bounded in-memory results, and no persisted job state."""

    def __init__(self, database: Path, timezone: str):
        self.database, self.timezone = database, timezone
        self.tasks: dict[str, dict] = {}

    def _discard(self, task_id: str):
        task = self.tasks.pop(task_id, None)
        if task and "expiry" in task:
            task["expiry"].cancel()

    def _prune(self):
        completed = [key for key, task in self.tasks.items()
                     if task["status"] not in {"queued", "running"}]
        for key in completed:
            if time.monotonic() - self.tasks[key]["finished"] > 600:
                self._discard(key)
        completed = [key for key in completed if key in self.tasks]
        for key in completed[:-8]:
            self._discard(key)

    def submit(self, request: dict) -> dict:
        self._prune()
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            return {"status": "refused", "error": "request too large"}
        identity = hashlib.sha256(encoded.encode()).hexdigest()
        for task_id, task in self.tasks.items():
            if task["status"] in {"queued", "running"}:
                if task["identity"] == identity:
                    return self.status(task_id)
                return {"status": "refused", "error": "another analysis is running; check its task first"}
        task_id = secrets.token_hex(16)
        task = {"status": "queued", "identity": identity, "operation": request["operation"],
                "started_at": datetime.now(utc_timezone.utc).isoformat()}
        self.tasks[task_id] = task
        task["runner"] = asyncio.create_task(self._run(task_id, task, encoded))
        return self.status(task_id)

    async def _run(self, task_id: str, task: dict, encoded: str):
        process = None
        terminal_status = "failed"
        try:
            # Bundled launchers use isolated Python. Retain that boundary in
            # workers, and pass -B explicitly because -I ignores Python env.
            python_flags = (["-I"] if sys.flags.isolated else []) + ["-B"]
            process = await asyncio.create_subprocess_exec(
                sys.executable, *python_flags, str(Path(__file__).resolve()), "--database", str(self.database),
                "--timezone", self.timezone, "--worker",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            task["status"] = "running"
            process.stdin.write(encoded.encode())
            await process.stdin.drain()
            process.stdin.close()

            async def collect():
                stdout = await process.stdout.read(MAX_RESULT_BYTES + 1)
                if len(stdout) > MAX_RESULT_BYTES:
                    raise ValueError("worker response too large")
                # read(n) may return a short pipe chunk before EOF.
                while chunk := await process.stdout.read(MAX_RESULT_BYTES + 1 - len(stdout)):
                    stdout += chunk
                    if len(stdout) > MAX_RESULT_BYTES:
                        raise ValueError("worker response too large")
                await process.wait()
                return stdout

            stdout = await asyncio.wait_for(collect(), timeout=WORKER_TIMEOUT_SECONDS)
            if process.returncode != 0 or len(stdout) > MAX_RESULT_BYTES:
                raise ValueError("worker failed")
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError("invalid worker result")
            terminal_status = "completed"
            task["result"] = result
        except asyncio.CancelledError:
            terminal_status = "cancelled"
        except asyncio.TimeoutError:
            terminal_status = "failed"
            task["error"] = "analysis exceeded its execution limit"
        except Exception:
            terminal_status = "failed"
            task["error"] = "local analysis failed closed"
        finally:
            if process is not None:
                if process.returncode is None:
                    process.kill()
                # Drain after killing too: a paused full pipe can otherwise keep
                # asyncio Process.wait pending even after the child has exited.
                while await process.stdout.read(65_536):
                    pass
                await process.wait()
            task["finished"] = time.monotonic()
            task["completed_at"] = datetime.now(utc_timezone.utc).isoformat()
            task["status"] = terminal_status
            task["expiry"] = asyncio.get_running_loop().call_later(600, self._discard, task_id)
            self._prune()

    def status(self, task_id: str) -> dict:
        self._prune()
        task = self.tasks.get(task_id)
        if task is None:
            return {"status": "refused", "error": "task unavailable or expired"}
        response = {"task_id": task_id, "status": task["status"], "operation": task["operation"],
                    "started_at": task["started_at"]}
        if "completed_at" in task:
            response["completed_at"] = task["completed_at"]
        if "result" in task:
            response["result"] = task["result"]
        if "error" in task:
            response["error"] = task["error"]
        if task["status"] in {"queued", "running"}:
            response["poll_after_seconds"] = 5
        return response

    async def wait(self, task_id: str, seconds: int) -> dict:
        task = self.tasks.get(task_id)
        if task and task["status"] in {"queued", "running"}:
            try:
                await asyncio.wait_for(asyncio.shield(task["runner"]), timeout=seconds)
            except asyncio.TimeoutError:
                pass
        return self.status(task_id)

    async def close(self):
        runners = [task["runner"] for task in self.tasks.values()]
        for runner in runners:
            if not runner.done():
                runner.cancel()
        await asyncio.gather(*runners, return_exceptions=True)
        for task_id in list(self.tasks):
            self._discard(task_id)


def create_server(surface, tasks):
    # Import lazily: --help and the product itself do not need the optional SDK.
    from mcp import types
    from mcp.server import Server

    server = Server("OpenHealthAtlas Local", version="1.0.0", instructions=(
        "Read-only deterministic tools over the explicitly selected local dataset. "
        "Discover features, query or analyze, then replay evidence_refs before explaining. "
        "Analysis and evidence return task receipts: poll health_task_status every five "
        "seconds until completed and use its result as a snapshot, not a freshness claim. "
        "Only one heavy task runs at a time; "
        "tasks expire ten minutes after completion and are lost when this connection closes. "
        "Preserve returned sources, dates, units, missingness and limitations. "
        "The connected client chooses its model and owns interpretation; this server makes "
        "no provider calls. Do not claim diagnosis or causation. Local transport does not "
        "prevent the client from sending tool results to its selected model provider."
    ))

    @server.list_tools()
    async def list_tools():
        tools = [types.Tool(
            name=name, description=TOOL_DESCRIPTIONS[name], inputSchema=schema,
            annotations=types.ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, openWorldHint=False,
            ),
        ) for name, schema in TOOL_SCHEMAS.items()]
        tools.append(types.Tool(
            name="health_task_status",
            description="Wait up to wait_seconds for an analysis/evidence task. Completed result is a snapshot in result; a new invocation checks current data. No health records change.",
            inputSchema=_object({
                "task_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 20, "default": 5},
            }, ("task_id",)),
            annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        ))
        return tools

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        # Closed validators own value validation. No SDK coercion or SDK
        # exception strings may replace that boundary.
        try:
            encoded_request = json.dumps(arguments, allow_nan=False, separators=(",", ":"))
            if len(encoded_request.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise ValueError("request too large")
            if name == "health_task_status":
                if (not isinstance(arguments, dict)
                        or not {"task_id"} <= arguments.keys() <= {"task_id", "wait_seconds"}
                        or not isinstance(arguments["task_id"], str)
                        or len(arguments["task_id"]) != 32
                        or type(arguments.get("wait_seconds", 5)) is not int
                        or not 0 <= arguments.get("wait_seconds", 5) <= 20):
                    raise ValueError("invalid task arguments")
                result = await tasks.wait(arguments["task_id"], arguments.get("wait_seconds", 5))
            else:
                if name not in TOOL_SCHEMAS or not isinstance(arguments, dict):
                    raise ValueError("unavailable operation")
                request = {"operation": name, "arguments": {**DEFAULTS[name], **arguments}}
                if name in {"health_analyze", "health_evidence"}:
                    result = tasks.submit(request)
                else:
                    result = await asyncio.to_thread(execute_request, surface, request)
            encoded = encode_result(result)
            if len(encoded.encode("utf-8")) > MAX_TOOL_BYTES:
                raise ValueError("response too large")
        except Exception:
            result = {"status": "refused", "error": "local evidence request failed closed"}
            encoded = encode_result(result)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=encoded)],
            structuredContent=json.loads(encoded),
            isError=(result.get("status") in {"refused", "failed"}
                     or (result.get("status") == "completed"
                         and result.get("result", {}).get("status") == "refused")),
        )

    return server


async def serve(surface, database: Path, timezone: str):
    from mcp.server.stdio import stdio_server

    tasks = AnalysisTasks(database, timezone)
    server = create_server(surface, tasks)
    loop = asyncio.get_running_loop()
    running = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, running.cancel)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await tasks.close()
        loop.remove_signal_handler(signal.SIGTERM)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="Existing local health database")
    parser.add_argument("--timezone", default="UTC", help="Calendar timezone (default: UTC)")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        # Retain a deadline even if the owning stdio process is killed abruptly.
        signal.alarm(WORKER_TIMEOUT_SECONDS)
    try:
        from hermes_insights.local_surface import LocalSurface
        surface = LocalSurface(database=args.database, timezone=args.timezone)
        if args.worker:
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if not raw or len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            sys.stdout.write(encode_result(execute_request(surface, json.loads(raw))))
            signal.alarm(0)
        else:
            asyncio.run(serve(surface, Path(surface.database), args.timezone))
    except (asyncio.CancelledError, KeyboardInterrupt):
        return 0
    except ImportError:
        print("Install requirements-mcp.txt in this Python environment.", file=sys.stderr)
        return 2
    except Exception:
        print("Local MCP startup failed; check the selected database, timezone and dependencies.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
