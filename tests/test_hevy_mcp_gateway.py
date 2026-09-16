"""Security and duplicate-safety contracts for the Hermes -> Hevy MCP gateway."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "deploy" / "hermes-hevy-mcp-gateway"
CLIENT = ROOT / "deploy" / "hermes-hevy-mcp-client"
INSTALLER = ROOT / "deploy" / "install-hevy-mcp-gateway.sh"
SERVICE = ROOT / "deploy" / "hermes-hevy-mcp-gateway.service"
HEVY_SKILL = ROOT / "deploy" / "hermes-hevy-skill.md"


def load_gateway():
    loader = importlib.machinery.SourceFileLoader("hevy_mcp_gateway", str(GATEWAY))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def gateway():
    return load_gateway()


def _routine():
    return {
        "title": "Quarterly Test",
        "folderId": 123,
        "notes": "Set 1 left; set 2 right",
        "exercises": [
            {
                "exerciseTemplateId": "75A4F6C4",
                "restSeconds": 120,
                "notes": "Clean 6–8RM",
                "sets": [
                    {"type": "normal", "reps": 8},
                    {"type": "normal", "reps": 8},
                ],
            }
        ],
    }


def test_tool_surface_excludes_completed_workouts_updates_and_measurements(gateway):
    assert gateway.WRITE_TOOLS == {
        "create-routine",
        "create-exercise-template",
        "create-routine-folder",
    }
    for forbidden in (
        "create-workout",
        "update-workout",
        "update-routine",
        "create-body-measurement",
        "update-body-measurement",
    ):
        with pytest.raises(gateway.Refused, match="not allowed"):
            gateway.validate_tool_call(forbidden, {})


def test_create_routine_validation_accepts_bounded_payload(gateway):
    gateway.validate_tool_call("create-routine", _routine())


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"unexpected": True}),
        lambda value: value.update({"title": ""}),
        lambda value: value.update({"exercises": []}),
        lambda value: value["exercises"][0].update(
            {"exerciseTemplateId": "../../etc/passwd"}
        ),
        lambda value: value["exercises"][0]["sets"][0].update({"reps": 1001}),
        lambda value: value["exercises"][0].update({"restSeconds": 901}),
    ],
)
def test_create_routine_validation_fails_closed(gateway, mutator):
    value = _routine()
    mutator(value)
    with pytest.raises(gateway.Refused):
        gateway.validate_tool_call("create-routine", value)


def test_tools_list_is_filtered_on_trusted_side(gateway):
    response = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {"name": "create-routine"},
                {"name": "create-workout"},
                {"name": "get-user-info"},
            ]
        },
    }
    filtered = gateway.filter_tools_response(response)
    assert [item["name"] for item in filtered["result"]["tools"]] == [
        "create-routine",
        "get-user-info",
    ]
    assert response["result"]["tools"][1]["name"] == "create-workout"


def _tool_response(key, rows):
    return {
        "jsonrpc": "2.0",
        "id": "gateway-test",
        "result": {"structuredContent": {key: rows}},
    }


def test_exact_title_duplicate_blocks_routine_create(gateway):
    calls = []

    def upstream(name, args):
        calls.append((name, args))
        return _tool_response(
            "routines",
            [{"id": "abc", "title": " quarterly test ", "folderId": None}],
        )

    duplicate = gateway.duplicate_for_create(
        "create-routine", _routine(), upstream
    )
    assert duplicate["id"] == "abc"
    assert calls == [
        ("search-routines", {"query": "Quarterly Test", "limit": 100})
    ]


def test_non_exact_search_hit_does_not_false_block(gateway):
    response = _tool_response(
        "routines", [{"id": "abc", "title": "Quarterly Test 2"}]
    )
    duplicate = gateway.duplicate_for_create(
        "create-routine", _routine(), lambda *_: response
    )
    assert duplicate is None


def test_inconclusive_duplicate_check_fails_closed(gateway):
    with pytest.raises(gateway.Refused, match="inconclusive"):
        gateway.duplicate_for_create(
            "create-routine",
            _routine(),
            lambda *_: {"jsonrpc": "2.0", "id": "x", "result": {}},
        )


def test_audit_contains_hash_not_routine_text(gateway, tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(gateway, "AUDIT_FILE", str(audit))
    gateway._audit("create-routine", _routine(), "intent", 1002)
    text = audit.read_text()
    value = json.loads(text)
    assert value["tool"] == "create-routine"
    assert value["peer_uid"] == 1002
    assert len(value["args_sha256"]) == 64
    assert "Quarterly Test" not in text
    assert "exerciseTemplateId" not in text


def test_installer_is_pinned_secret_preserving_and_shell_valid():
    text = INSTALLER.read_text()
    assert "VERSION=3.4.1" in text
    assert "EXPECTED_INTEGRITY='sha512-" in text
    assert 'root root 600' in text
    assert ".hermes/.env" not in text
    assert "MCP_HEVY_API_KEY" not in text
    assert "EnvironmentFile=" not in text
    assert "--ignore-scripts" in text
    assert '"$AGENT_HOME/skills/hevy/SKILL.md"' in text
    assert "useradd --system" in text
    assert "systemctl restart" not in text
    assert "systemctl enable hermes-hevy-mcp-gateway.service >/" not in text
    result = subprocess.run(
        ["sh", "-n", str(INSTALLER)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_service_uses_dedicated_identity_systemd_credential_and_is_hardened():
    text = SERVICE.read_text()
    for required in (
        "User=hermes-hevy-mcp",
        "Group=hermes",
        "LoadCredential=hevy.env:/etc/hermes/hevy.env",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "InaccessiblePaths=/etc/hermes /var/lib/hermes",
        "MemoryMax=512M",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert required in text
    assert "EnvironmentFile=" not in text
    assert "User=root" not in text


@pytest.mark.parametrize("path", [GATEWAY, CLIENT])
def test_python_entrypoints_compile(path):
    compile(path.read_text(), str(path), "exec")


def test_stdio_client_forwards_available_bytes_without_waiting_for_eof():
    text = CLIENT.read_text()
    assert "os.read(sys.stdin.fileno(), 65536)" in text
    assert "sys.stdin.buffer.read(65536)" not in text


def test_hevy_skill_routes_natural_language_to_mcp_without_secret_access():
    text = HEVY_SKILL.read_text()
    assert "Hermes, put this routine in Hevy" in text
    assert "mcp_hevy_create_routine" in text
    assert "mcp_hevy_search_routines" in text
    assert "never ask" in text
    assert "API key" in text
    assert "does not log completed workouts" in text
