"""Focused contracts for the generic fictional OpenHealthAtlas MCP surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import importlib.util
import runpy
import shutil
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "hermes-openhealthatlas-tool"
MCP = ROOT / "deploy" / "hermes-openhealthatlas-mcp"
CONFIG_MARKER = ROOT / "deploy" / "hermes-openhealthatlas-tool-config.json"
FICTIONAL_CONFIG = ROOT / "deploy" / "hermes-openhealthatlas-fictional-config.json"
GOVERNANCE = ROOT / "deploy" / "openhealthatlas-real-data-governance.json"
TRANSPORT = ROOT / "deploy" / "hermes-ssh-transport"
SHA_A = "sha256:" + "a" * 64
DELIVERY_PLUGIN = (
    ROOT / "deploy" / "hermes-openhealthatlas-delivery-plugin" / "__init__.py"
)


def _config(tmp_path: Path) -> tuple[Path, Path, Path]:
    database = tmp_path / "health.db"
    database.write_bytes(b"fictional-database")
    vault = tmp_path / "vault"
    vault.mkdir()
    health = tmp_path / "health.py"
    health.write_text("# fixture CLI marker\n", encoding="utf-8")
    config = tmp_path / "tool-config.json"
    config.write_text(json.dumps({
        "audit": str(tmp_path / "audit.jsonl"),
        "contract": "openhealthatlas-hermes-tool-v1",
        "data_class": "fictional",
        "data_dir": str(tmp_path),
        "fixture_id": "comprehensive-persona-v1",
        "health_cli": str(health),
        "health_db": str(database),
        "health_vault": str(vault),
        "range_from": "2026-03-02",
        "range_to": "2026-06-30",
        "readiness_ancestry_sidecar": None,
        "readiness_fixture_lane": "development-v6",
        "synthesis_writeback_enabled": False,
        "timeout_seconds": 5,
        "timezone": "UTC",
    }), encoding="utf-8")
    return config, database, tmp_path / "audit.jsonl"


def _fake_surface(tmp_path: Path) -> None:
    package = tmp_path / "hermes_insights"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "hermes_surface.py").write_text(
        "import hashlib, json, os, sys\n"
        "request = json.load(sys.stdin)\n"
        "body = {\n"
        " 'contract': 'openhealthatlas-hermes-surface-v1',\n"
        " 'operation': request['operation'],\n"
        " 'data_class': 'fictional',\n"
        " 'fixture_id': os.environ.get('OPENHEALTHATLAS_FIXTURE_ID'),\n"
        " 'status': 'ok',\n"
        " 'range': request['arguments'].get('range'),\n"
        " 'registry_id': 'sha256:' + 'b' * 64,\n"
        " 'engine_id': 'sha256:' + 'c' * 64,\n"
        " 'result_id': 'sha256:' + 'a' * 64,\n"
        " 'coverage': {}, 'facts': [{\n"
        "   'seen_db': os.environ.get('HEALTH_DB'),\n"
        "   'seen_data_class': os.environ.get('OPENHEALTHATLAS_DATA_CLASS'),\n"
        "   'seen_fixture_id': os.environ.get('OPENHEALTHATLAS_FIXTURE_ID'),\n"
        "   'seen_request': request,\n"
        " }], 'findings': [], 'limitations': [],\n"
        " 'evidence_refs': [],\n"
        "}\n"
        "print(json.dumps(body, sort_keys=True, separators=(',', ':')))\n",
        encoding="utf-8",
    )


def _run_adapter(tmp_path: Path, command: str, request: object):
    config, database, audit = _config(tmp_path)
    _fake_surface(tmp_path)
    environment = dict(os.environ)
    environment.update({
        "OPENHEALTHATLAS_SOURCE_TEST": "1",
        "OPENHEALTHATLAS_TEST_CONFIG": str(config),
        "OPENHEALTHATLAS_HERMES_TURN_ID": "generic-fixture-turn",
        # These owner-controlled overrides must not reach the facade.
        "HEALTH_DB": str(tmp_path / "owner.db"),
        "PYTHONPATH": str(tmp_path / "owner-python"),
    })
    completed = subprocess.run(
        [sys.executable, str(TOOL), command],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    return completed, json.loads(completed.stdout), database, audit


def _request(operation: str, arguments: dict) -> dict:
    return {"operation": operation, "arguments": arguments}


def test_adapter_runs_closed_query_through_fixed_fictional_environment(tmp_path):
    request = _request("health_query", {
        "feature_keys": ["sleep.duration_hours", "wearable.hrv_ms"],
        "range": {"from": "2026-06-01", "to": "2026-06-30"},
        "view": "summary",
        "grain": "day",
        "aggregates": ["mean", "min", "max"],
    })
    before = (tmp_path / "not-yet-created").exists()
    completed, body, database, audit = _run_adapter(
        tmp_path, "health-query", request,
    )
    assert before is False
    assert completed.returncode == 0
    assert body["ok"] is True
    assert body["fixture_id"] == "comprehensive-persona-v1"
    assert body["turn_id"] == "generic-fixture-turn"
    observed = body["result"]["facts"][0]
    assert observed["seen_db"] == str(database)
    assert observed["seen_data_class"] == "fictional"
    assert observed["seen_fixture_id"] == "comprehensive-persona-v1"
    assert observed["seen_request"] == request
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [row["phase"] for row in rows] == ["intent", "result"]
    assert all(row["surface_operation"] == "health_query" for row in rows)
    assert rows[-1]["database_before_sha256"] == rows[-1]["database_after_sha256"]


@pytest.mark.parametrize("command,payload,error", [
    (
        "health-query",
        _request("health_query", {
            "feature_keys": ["sleep.duration_hours"],
            "range": {"from": "2026-02-01", "to": "2026-06-30"},
            "view": "series",
        }),
        "outside the configured fictional fixture",
    ),
    (
        "health-query",
        _request("health_query", {
            "feature_keys": ["sleep.duration_hours"],
            "range": {"from": "2026-06-01", "to": "2026-06-30"},
            "view": "series",
            "sql": "select 1",
        }),
        "unsupported or missing fields",
    ),
    (
        "health-analyze",
        _request("health_analyze", {
            "outcome_key": "subjective.day_rating",
            "exposure_keys": ["sleep.duration_hours"],
            "range": {"from": "2026-03-02", "to": "2026-06-30"},
            "mode": "ordinal",
            "interactions": False,
            "min_n": 14,
            "top": 20,
        }),
        "min_n is outside the supported range",
    ),
    (
        "health-evidence",
        _request("health_evidence", {
            "evidence_refs": [{
                "contract": "openhealthatlas-evidence-ref-v1",
                "operation": "health_query",
                "arguments": {
                    "feature_keys": ["sleep.duration_hours"],
                    "range": {"from": "2026-03-02", "to": "2026-06-30"},
                    "view": "summary",
                    "path": "/forbidden/private-health",
                },
                "result_id": SHA_A,
            }],
            "detail": "summary",
        }),
        "unsupported or missing fields",
    ),
])
def test_adapter_refuses_unbounded_ambiguous_or_private_request_shape(
    tmp_path, command, payload, error,
):
    completed, body, _database, audit = _run_adapter(tmp_path, command, payload)
    assert completed.returncode == 2
    assert body["ok"] is False
    assert error in body["error"]
    assert not audit.exists()


def _load_mcp(tmp_path: Path, monkeypatch):
    install = tmp_path / "mcp-install"
    install.mkdir()
    for source in (MCP, TOOL, TRANSPORT):
        shutil.copy2(source, install / source.name)
    shutil.copy2(FICTIONAL_CONFIG, install / "tool-config.json")
    shutil.copy2(GOVERNANCE, install / GOVERNANCE.name)

    class FakeFastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        @staticmethod
        def tool(*_args, **_kwargs):
            return lambda function: function

        def run(self, *_args, **_kwargs):
            raise AssertionError("tests must not start MCP stdio")

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
    monkeypatch.setattr(os, "chdir", lambda _path: None)
    return runpy.run_path(str(install / MCP.name))


class _Recorder:
    def __init__(self):
        self.calls = []

    def execute(self, operation, arguments):
        self.calls.append((operation, arguments))
        return {"operation": operation, "arguments": arguments}


def test_mcp_preserves_eight_fixed_tools_and_adds_four_generic_tools(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp(tmp_path, monkeypatch)
    fixed = {
        "openhealthatlas_feature_frame",
        "openhealthatlas_data_readiness",
        "openhealthatlas_analysis_refresh",
        "openhealthatlas_feature_registry",
        "openhealthatlas_finding_evidence",
        "openhealthatlas_synthesis_prepare",
        "openhealthatlas_recovery_snapshot",
        "openhealthatlas_recovery_detail",
    }
    generic = {
        "openhealthatlas_health_catalog",
        "openhealthatlas_health_query",
        "openhealthatlas_health_analyze",
        "openhealthatlas_health_evidence",
    }
    assert fixed | generic <= set(namespace)


def test_mcp_shapes_generic_calls_without_reasoning_or_paths(tmp_path, monkeypatch):
    namespace = _load_mcp(tmp_path, monkeypatch)
    recorder = _Recorder()
    for name in (
        "openhealthatlas_health_catalog",
        "openhealthatlas_health_query",
        "openhealthatlas_health_analyze",
        "openhealthatlas_health_evidence",
    ):
        namespace[name].__globals__["_generic_mcp_result"] = recorder.execute

    namespace["openhealthatlas_health_catalog"](
        search="sleep", domains=["sleep"], limit=10,
    )
    namespace["openhealthatlas_health_query"](
        ["sleep.duration_hours"],
        {"from": "2026-06-01", "to": "2026-06-30"},
        "summary",
        aggregates=["mean"],
    )
    namespace["openhealthatlas_health_analyze"](
        "subjective.day_rating",
        ["sleep.duration_hours"],
        {"from": "2026-03-02", "to": "2026-06-30"},
        "ordinal", False, 30, 10,
    )
    ref = {
        "contract": "openhealthatlas-evidence-ref-v1",
        "operation": "health_query",
        "arguments": {
            "feature_keys": ["sleep.duration_hours"],
            "range": {"from": "2026-06-01", "to": "2026-06-30"},
            "view": "summary",
        },
        "result_id": SHA_A,
    }
    namespace["openhealthatlas_health_evidence"]([ref], "lineage")

    assert [operation for operation, _args in recorder.calls] == [
        "health_catalog", "health_query", "health_analyze", "health_evidence",
    ]
    assert recorder.calls[0][1] == {
        "search": "sleep", "domains": ["sleep"], "limit": 10,
    }
    assert recorder.calls[-1][1] == {
        "evidence_refs": [ref], "detail": "lineage",
    }


def test_real_fictional_query_evidence_and_delivery_renderer(
    tmp_path, monkeypatch,
):
    database = tmp_path / "health.db"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "make_demo_db.py"),
            "--output",
            str(database),
            "--anchor-date",
            "2026-06-30",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "real-tool-config.json"
    config.write_text(json.dumps({
        "audit": str(tmp_path / "audit.jsonl"),
        "contract": "openhealthatlas-hermes-tool-v1",
        "data_class": "fictional",
        "data_dir": str(tmp_path),
        "fixture_id": "comprehensive-persona-v1",
        "health_cli": str(ROOT / "toolkit" / "health.py"),
        "health_db": str(database),
        "health_vault": str(vault),
        "range_from": "2026-03-02",
        "range_to": "2026-06-30",
        "readiness_ancestry_sidecar": None,
        "readiness_fixture_lane": "development-v7",
        "synthesis_writeback_enabled": False,
        "timeout_seconds": 90,
        "timezone": "UTC",
    }), encoding="utf-8")
    namespace_root = tmp_path / "namespace"
    namespace_root.mkdir()
    namespace = _load_mcp(namespace_root, monkeypatch)
    monkeypatch.setenv("OPENHEALTHATLAS_SOURCE_TEST", "1")
    monkeypatch.setenv("OPENHEALTHATLAS_TEST_CONFIG", str(config))
    surface = namespace["generic_health_surface"]
    surface.execute.__func__.__globals__["TOOL"] = TOOL

    query = surface.execute("health_query", {
        "feature_keys": ["sleep.duration_hours", "nutrition.logged.protein_g"],
        "range": {"from": "2026-06-01", "to": "2026-06-30"},
        "view": "summary",
    })
    reference = query["evidence_refs"][0]
    evidence_function = namespace["openhealthatlas_health_evidence"]
    evidence = evidence_function([reference], "summary")
    assert evidence["operation"] == "health_evidence"
    assert evidence["coverage"]["requested_references"] == 1
    assert evidence["coverage"]["verified_references"] == 1
    assert evidence["coverage"]["referenced_statuses"] == {"ok": 1}
    assert evidence["delivery_contract"]["required"] is True
    assert evidence["delivery_contract"]["renderer_version"] == "1.0.2"
    trusted_claims = [
        claim for claim in evidence["allowed_scalar_claims"]
        if claim.startswith("sleep.duration_hours mean:")
        or claim.startswith("nutrition.logged.protein_g mean:")
    ]
    assert len(trusted_claims) == 2

    spec = importlib.util.spec_from_file_location("oha_generic_e2e_plugin", DELIVERY_PLUGIN)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    rendered = plugin.render_generic_delivery(
        completion={
            "tool_name": (
                "mcp_openhealthatlas_fictional_"
                "openhealthatlas_health_evidence"
            ),
            "result": evidence,
            "session_id": "session-e2e",
            "turn_id": "turn-e2e",
            "tool_call_id": "call-e2e",
            "api_request_id": "request-e2e",
            "task_id": "task-e2e",
        },
        response_text=(
            "During June 2026, sleep and protein can be compared naturally. "
            "Sleep was around 7.1 hours and protein was roughly 121 g, while "
            "the deterministic block above remains authoritative."
        ),
        platform="telegram",
    )
    assert rendered["trusted_disclosure"].startswith("Data & evidence disclosure")
    assert rendered["optional_prose"].startswith("Hermes interpretation (model-generated")
    assert "During June 2026" in rendered["optional_prose"]
    assert "around 7.1 hours" in rendered["optional_prose"]
    assert "roughly 121 g" in rendered["optional_prose"]
