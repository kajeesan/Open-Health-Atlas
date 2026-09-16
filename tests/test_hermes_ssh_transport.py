"""Bounded private SSH transport protocol and isolation tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
TRANSPORT = ROOT / "deploy" / "hermes-ssh-transport"


def _load_transport():
    return runpy.run_path(str(TRANSPORT))


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)
    return path


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    state = tmp_path / "ssh-state.json"
    prompts = tmp_path / "model-prompts.jsonl"
    actions = tmp_path / "model-actions.json"
    actions.write_text(json.dumps([
        {
            "action": "tool_calls",
            "calls": [{
                "command": "feature-frame",
                "args": [
                    "--from", "2026-05-17", "--to", "2026-06-30",
                    "--family", "subjective", "--include-provenance",
                ],
            }],
        },
        {
            "action": "final",
            "reply": "## Deterministic result\nExact result.\n\n"
                     "## Hermes interpretation\nBounded interpretation.",
        },
    ]), encoding="utf-8")
    ssh = _executable(tmp_path / "ssh", """#!/usr/bin/env python3
import json, os, pathlib, sys
command = sys.argv[-1]
if "/usr/bin/install" in command or "/usr/bin/rm -rf" in command:
    raise SystemExit(0)
prompt = sys.stdin.read()
with pathlib.Path(os.environ["FAKE_SSH_PROMPTS"]).open("a", encoding="utf-8") as h:
    h.write(json.dumps(prompt) + "\\n")
state_path = pathlib.Path(os.environ["FAKE_SSH_STATE"])
index = json.loads(state_path.read_text()) if state_path.exists() else 0
actions = json.loads(pathlib.Path(os.environ["FAKE_SSH_ACTIONS"]).read_text())
print(json.dumps(actions[index]))
state_path.write_text(json.dumps(index + 1))
""")
    tool = _executable(tmp_path / "openhealthatlas-tool", """#!/usr/bin/env python3
import json, sys
print(json.dumps({
    "ok": True,
    "tool_contract": "openhealthatlas-hermes-tool-v1",
    "selected_command": sys.argv[1:],
    "evidence_ids": ["sha256:" + "a" * 64],
    "result": {"observations": [{
        "feature_key": "subjective.day_rating",
        "observed_at": "2026-05-17",
        "value": 3,
        "source": "manual",
    }]},
}))
""")
    identity = tmp_path / "identity"
    identity.write_text("not-a-real-key", encoding="utf-8")
    identity.chmod(0o600)
    config = tmp_path / "transport.json"
    config.write_text(json.dumps({
        "destination": "demo@example.invalid",
        "identity_file": str(identity),
        "model": "gpt-test",
        "provider": "test-provider",
        "remote_hermes": "/opt/hermes/bin/hermes",
        "remote_home": "/srv/hermes",
        "remote_profile_root": "/srv/hermes/profiles",
        "remote_python": "/opt/hermes/venv/bin/python3",
        "remote_user": "hermes",
        "ssh_bin": str(ssh),
        "timeout_seconds": 30,
    }), encoding="utf-8")
    config.chmod(0o600)
    return config, tool, actions, prompts


def test_transport_runs_model_selected_local_tool_and_cleans_remote_profile(tmp_path):
    config, tool, actions, prompts = _fixture(tmp_path)
    receipt = tmp_path / "receipt.json"
    completed = subprocess.run(
        [
            sys.executable, str(TRANSPORT), "-z", "fictional trusted prompt",
            "--cli", "--continue", "hermes-panel",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "HERMES_SSH_TRANSPORT_CONFIG": str(config),
            "OPENHEALTHATLAS_TRANSPORT_TOOL": str(tool),
            "HERMES_SSH_TRANSPORT_RECEIPT": str(receipt),
            "FAKE_SSH_ACTIONS": str(actions),
            "FAKE_SSH_PROMPTS": str(prompts),
            "FAKE_SSH_STATE": str(tmp_path / "ssh-state.json"),
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("## Deterministic result")
    report = json.loads(receipt.read_text())
    assert report["contract"] == "openhealthatlas-hermes-ssh-transport-v1"
    assert report["remote"]["history_scope"] == "disposable-hermes-home"
    assert report["remote"]["cleanup_succeeded"] is True
    assert report["remote"]["profile_id"].startswith("ohademo")
    assert report["final_reply"] == completed.stdout.strip()
    assert report["tool_executions"][0]["command"] == "feature-frame"
    assert report["tool_executions"][0]["envelope"]["result"] == {
        "observations": [{
            "feature_key": "subjective.day_rating",
            "observed_at": "2026-05-17",
            "source": "manual",
            "value": 3,
        }],
    }
    model_prompts = [json.loads(line) for line in prompts.read_text().splitlines()]
    assert len(model_prompts) == 2
    assert "Exact OpenHealthAtlas executions so far:\n[]" in model_prompts[0]
    assert '"source":"manual","value":3' in model_prompts[1]
    assert "feature-registry" in model_prompts[0]
    assert "What the user did" in model_prompts[0]
    assert "physiological markers" in model_prompts[0]
    assert "JSON-escape every line break and quotation mark" in model_prompts[0]
    assert "select and replay at most three findings" in model_prompts[0]
    assert "example.invalid" not in receipt.read_text()


def test_model_projection_preserves_raw_rank_and_selects_registry_metadata():
    transport = _load_transport()
    finding = {
        "finding_id": "sha256:" + "a" * 64,
        "quality": {"eligible_for_hypothesis": True},
        "exposure": {"components": [{
            "exposure_key": "wearable.resting_hr_bpm",
            "lag_days": 0,
        }]},
    }
    analysis = transport["_model_projection"]("analysis-refresh", {
        "ok": True,
        "result": {
            "runs": [{"result_json": json.dumps({
                "ok": True,
                "findings": [finding],
                "coverage": {},
                "warnings": [],
            })}],
        },
    })
    projected_finding = analysis["result"]["runs"][0][
        "deterministic_result"
    ]["findings"][0]
    assert projected_finding["raw_statistical_rank"] == 1
    assert projected_finding["finding_id"] == finding["finding_id"]
    assert set(projected_finding) == {
        "raw_statistical_rank", "finding_id", "exposure", "oriented_effect",
        "q", "eligible_for_hypothesis", "stability_status",
    }
    assert analysis["result"]["runs"][0]["deterministic_result"][
        "projection"
    ]["ordering"] == "raw_deterministic_statistical_ranking"

    prior = [{
        "command": "analysis-refresh",
        "envelope": analysis,
    }]
    registry = transport["_model_projection"]("feature-registry", {
        "ok": True,
        "result": {
            "contract_version": "feature-registry-v1",
            "registry_version": "feature-registry-v1",
            "registry_sha256": "abc",
            "features": [
                {
                    "key": "wearable.resting_hr_bpm",
                    "display_name": "Resting HR",
                    "pillar": "wearable",
                    "actionability": "context",
                    "roles": ["exposure"],
                    "confounder_role": "context",
                    "temporal_type": "daily_measurement",
                    "lag_eligibility": {"lags": [0, 1]},
                    "direction": "lower_better",
                    "value_kind": "continuous",
                    "candidate_enabled": True,
                },
                {
                    "key": "unrelated.feature",
                    "display_name": "Unrelated",
                },
            ],
        },
    }, prior)
    assert [
        item["key"] for item in registry["result"]["features"]
    ] == ["wearable.resting_hr_bpm"]
    assert registry["result"]["features"][0]["actionability"] == "context"
    assert registry["result"]["projection"]["requested_keys"] == [
        "wearable.resting_hr_bpm"
    ]


def test_model_projection_omits_replayed_and_prepared_bulk():
    transport = _load_transport()
    replay = transport["_model_projection"]("finding-evidence", {
        "ok": True,
        "result_sha256": "sha256:" + "b" * 64,
        "result": {
            "ok": True,
            "contract_version": "finding-evidence-v1",
            "meta": {"from": "2026-05-17", "to": "2026-06-30"},
            "finding": {
                "finding_id": "sha256:" + "a" * 64,
                "effect": {"estimate": -0.7},
                "source_references": [{"large": "payload"}],
                "source_manifests": [{"large": "payload"}],
            },
        },
    })
    assert "source_references" not in replay["result"]["finding"]
    assert replay["result"]["finding"]["effect"] == {"estimate": -0.7}
    assert replay["result"]["projection"]["full_result_bound_by"] == (
        "sha256:" + "b" * 64
    )

    prepared = transport["_model_projection"]("synthesis-prepare", {
        "ok": True,
        "result_sha256": "sha256:" + "c" * 64,
        "result": {
            "ok": True,
            "assessment_state": "assessed",
            "structured_slots": {
                "contract_version": "synthesis-payload-v1",
                "cadence": "manual",
                "numeric_authority": "deterministic",
                "template": "governed",
                "transitions": [],
                "findings": [{"large": "payload"}],
                "readiness_data_gaps": {"large": "payload"},
                "slots": {"a": {"large": "payload"}},
            },
        },
    })
    assert prepared["result"]["structured_slots"]["finding_count"] == 1
    assert prepared["result"]["structured_slots"]["slot_count"] == 1
    assert "findings" not in prepared["result"]["structured_slots"]


def test_terminal_markdown_requires_synthesis_boundary_and_exact_headings():
    transport = _load_transport()
    reply = """## Deterministic result
Raw order.

## Hermes interpretation
### What the user did
Prior-day behavior.

### What the body showed
Same-day marker.

### What remains unknown
No causal certainty.
"""
    with pytest.raises(transport["Refused"], match="protocol JSON"):
        transport["_model_action"](reply)

    action = transport["_model_action"](reply, terminal_ready=True)
    assert action == {"action": "final", "reply": reply.strip()}

    with pytest.raises(transport["Refused"], match="protocol JSON"):
        transport["_model_action"](
            reply.replace("### What remains unknown", "### Unknown"),
            terminal_ready=True,
        )


def test_model_action_uses_total_call_budget_as_batch_bound():
    transport = _load_transport()
    call = {"command": "feature-registry", "args": []}
    allowed = {"action": "tool_calls", "calls": [call] * 5}
    assert transport["_model_action"](json.dumps(allowed)) == allowed

    refused = {
        "action": "tool_calls",
        "calls": [call] * (transport["MAX_TOOL_CALLS"] + 1),
    }
    with pytest.raises(transport["Refused"], match="wrong number"):
        transport["_model_action"](json.dumps(refused))


def test_transport_rejects_non_private_connection_config(tmp_path):
    config, tool, actions, prompts = _fixture(tmp_path)
    config.chmod(0o622)
    completed = subprocess.run(
        [
            sys.executable, str(TRANSPORT), "-z", "prompt",
            "--cli", "--continue", "hermes-panel",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "HERMES_SSH_TRANSPORT_CONFIG": str(config),
            "OPENHEALTHATLAS_TRANSPORT_TOOL": str(tool),
            "FAKE_SSH_ACTIONS": str(actions),
            "FAKE_SSH_PROMPTS": str(prompts),
            "FAKE_SSH_STATE": str(tmp_path / "ssh-state.json"),
        },
    )
    assert completed.returncode == 2
    assert "must not be group/world writable" in completed.stderr


def test_transport_rejects_model_requested_synthesis_persistence(tmp_path):
    config, tool, actions, prompts = _fixture(tmp_path)
    actions.write_text(json.dumps([{
        "action": "tool_calls",
        "calls": [{"command": "synthesis-record", "args": ["--stdin"]}],
    }]), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable, str(TRANSPORT), "-z", "prompt",
            "--cli", "--continue", "hermes-panel",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "HERMES_SSH_TRANSPORT_CONFIG": str(config),
            "OPENHEALTHATLAS_TRANSPORT_TOOL": str(tool),
            "FAKE_SSH_ACTIONS": str(actions),
            "FAKE_SSH_PROMPTS": str(prompts),
            "FAKE_SSH_STATE": str(tmp_path / "ssh-state.json"),
        },
    )
    assert completed.returncode == 2
    assert "invalid tool call" in completed.stderr
