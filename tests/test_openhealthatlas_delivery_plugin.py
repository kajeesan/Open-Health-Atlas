"""Standalone contract tests for the OHA-owned Hermes delivery renderer."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_openhealthatlas_tool import (
    _load_mcp_namespace,
    _recovery_result_with_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = (
    ROOT / "deploy" / "hermes-openhealthatlas-delivery-plugin" / "__init__.py"
)
INSTALLER = ROOT / "deploy" / "install-openhealthatlas-delivery-plugin.sh"


def _plugin():
    spec = importlib.util.spec_from_file_location("oha_delivery_plugin", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(tmp_path, monkeypatch):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    raw = _recovery_result_with_evidence()
    journey = namespace["RecoveryJourney"]()
    journey._execute_readiness = lambda: {
        "result": raw,
        "result_sha256": "sha256:" + "f" * 64,
    }
    journey._save_receipt = lambda _focus=None: None
    return journey.snapshot(), raw


def _completion(
    snapshot,
    tool_name=(
        "mcp_openhealthatlas_fictional_"
        "openhealthatlas_recovery_snapshot"
    ),
):
    return {
        "tool_name": tool_name,
        "result": snapshot,
        "session_id": "session-1",
        "turn_id": "hermes-turn-1",
        "tool_call_id": "tool-call-1",
        "api_request_id": "request-1",
        "task_id": None,
    }


def _rebind_trusted_facts(plugin, result):
    marker = result["delivery_contract"]
    marker["trusted_facts"]["required_disclosure"] = result[
        "required_disclosure"
    ]
    marker["trusted_facts"]["sharing_authorization"] = result[
        "sharing_authorization"
    ]
    marker["correlation"]["completion_id"] = plugin._sha256({
        "contract": plugin.DELIVERY_CONTRACT,
        "mcp_tool": marker["correlation"]["mcp_tool"],
        "turn_id": result["turn_id"],
        "trusted_facts": marker["trusted_facts"],
    })


def test_registers_separate_versioned_recovery_and_generic_renderers():
    plugin = _plugin()

    class Context:
        calls = []

        def register_gateway_delivery_renderer(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    context = Context()
    plugin.register(context)
    assert context.calls[0] == (
        (
            "openhealthatlas-recovery-delivery",
            "1.0.0",
            plugin.render_delivery,
        ),
        {
            "platforms": ("telegram",),
            "required_tool_names": (
                "mcp_openhealthatlas_fictional_"
                "openhealthatlas_recovery_snapshot",
                "mcp_openhealthatlas_fictional_"
                "openhealthatlas_recovery_detail",
            ),
        },
    )
    assert context.calls[1][0] == (
        "openhealthatlas-generic-evidence-delivery",
        "1.0.2",
        plugin.render_generic_delivery,
    )


def test_renders_trusted_disclosure_before_separate_optional_prose(
    tmp_path, monkeypatch,
):
    plugin = _plugin()
    snapshot, raw = _snapshot(tmp_path, monkeypatch)
    response = plugin.render_delivery(
        completion=_completion(snapshot),
        response_text="The deterministic result is limited by the stated evidence.",
        platform="telegram",
    )

    assert response["contract"] == "hermes-gateway-rendered-delivery-v1"
    assert response["trusted_disclosure"].startswith("Data & evidence disclosure\n")
    assert response["optional_prose"] == (
        "Hermes interpretation\n"
        "The deterministic result is limited by the stated evidence."
    )
    assert response["bounded_patterns_checked"] is True
    assert snapshot["required_disclosure"]["non_diagnostic_text"] == plugin.NON_DIAGNOSTIC_TEXT
    assert plugin.NON_DIAGNOSTIC_TEXT not in response["trusted_disclosure"]
    assert "Eligible deterministic components: sleep 92/100" in (
        response["trusted_disclosure"]
    )
    assert '"observed":10' in response["trusted_disclosure"]
    assert '"required":14' in response["trusted_disclosure"]
    assert '"other_source_observations_excluded":86' in (
        response["trusted_disclosure"]
    )
    assert "Historical generation event: not_attested" in (
        response["trusted_disclosure"]
    )
    assert "Shared field paths:" in response["trusted_disclosure"]
    assert "openhealthatlas-fictional-soreness-summary-telegram v1.0.0" in (
        response["trusted_disclosure"]
    )
    assert "raw note permitted false" in response["trusted_disclosure"]
    assert "58/100" not in response["trusted_disclosure"]
    serialized = json.dumps(response)
    assert raw["soreness"]["note"] not in serialized
    assert raw["evidence"]["soreness"]["content_fingerprint"] not in serialized
    assert "privacy_safe_model_projection" not in response["optional_prose"]


@pytest.mark.parametrize(
    "prose",
    [
        "Readiness score: 87/100",
        "Recovery status: ready",
        "fixture_id: another-fixture",
        "Approved range: 2026-01-01 to 2026-02-01",
        "Policy ID: another-policy version 9.0.0",
        "Interpretation write-back is enabled",
        "The result is a diagnosis.",
        "Soreness caused the low result.",
        "Evidence is sha256:" + "9" * 64,
    ],
)
def test_bounded_explicit_conflicts_fail_closed(
    tmp_path, monkeypatch, prose,
):
    plugin = _plugin()
    snapshot, _ = _snapshot(tmp_path, monkeypatch)
    with pytest.raises(plugin.DeliveryRefused):
        plugin.render_delivery(
            completion=_completion(snapshot),
            response_text=prose,
            platform="telegram",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_tool_call_contract",
        "wrong_oha_turn",
        "forged_trusted_fact",
        "private_sidecar",
        "caller_wrapper",
        "policy_drift",
        "public_evidence_drift",
        "development_v6_lane",
        "snapshot_projection_drift",
    ],
)
def test_malformed_or_private_completion_fails_closed(
    tmp_path, monkeypatch, mutation,
):
    plugin = _plugin()
    snapshot, _ = _snapshot(tmp_path, monkeypatch)
    completion = _completion(snapshot)
    if mutation == "wrong_tool_call_contract":
        snapshot["delivery_contract"]["renderer_version"] = "2.0.0"
    elif mutation == "wrong_oha_turn":
        snapshot["delivery_contract"]["correlation"][
            "openhealthatlas_turn_id"
        ] = "another-oha-turn"
    elif mutation == "forged_trusted_fact":
        snapshot["delivery_contract"]["trusted_facts"]["result_status"] = "ok"
    elif mutation == "private_sidecar":
        snapshot["result"]["evidence"]["sidecar_path"] = "/private/sidecar.json"
    elif mutation == "caller_wrapper":
        completion["caller_facts"] = {"status": "ok"}
    elif mutation == "policy_drift":
        snapshot["result"]["evidence"]["policy"]["version"] = "9.0.0"
    elif mutation == "public_evidence_drift":
        snapshot["result"]["evidence"]["public_evidence_identity"] = (
            "sha256:" + "9" * 64
        )
    elif mutation == "development_v6_lane":
        snapshot["result"]["evidence"]["snapshot_integrity"][
            "schema_version"
        ] = 6
    else:
        snapshot["result"]["components"][0]["score"] = 91
    with pytest.raises(plugin.DeliveryRefused):
        plugin.render_delivery(
            completion=completion,
            response_text="Optional explanation.",
            platform="telegram",
        )


def test_complete_payload_is_validated_before_return(tmp_path, monkeypatch):
    plugin = _plugin()
    snapshot, _ = _snapshot(tmp_path, monkeypatch)
    with pytest.raises(plugin.DeliveryRefused, match="too long"):
        plugin.render_delivery(
            completion=_completion(snapshot),
            response_text="x" * (plugin.MAX_OPTIONAL_PROSE_CHARS + 1),
            platform="telegram",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "approved_range",
        "authorization_raw_note_path",
        "authorization_exclusions",
        "disclosure_paths",
        "trusted_facts_range",
        "source_ancestry_status",
        "historical_generation_event_status",
        "remaining_ancestry_gaps",
    ],
)
def test_structured_scope_and_ancestry_mutations_fail_closed_after_rebinding(
    tmp_path, monkeypatch, mutation,
):
    plugin = _plugin()
    snapshot, _ = _snapshot(tmp_path, monkeypatch)
    disclosure = snapshot["required_disclosure"]
    authorization = snapshot["sharing_authorization"]
    if mutation == "approved_range":
        authorization["approved_range"]["from"] = "2026-03-03"
    elif mutation == "authorization_raw_note_path":
        authorization["permitted_field_paths"].append("result.soreness.note")
    elif mutation == "authorization_exclusions":
        authorization["excluded_field_groups"].remove("verbatim_soreness_note")
    elif mutation == "disclosure_paths":
        disclosure["shared_field_paths"].append("result.soreness.note")
    elif mutation == "trusted_facts_range":
        snapshot["delivery_contract"]["trusted_facts"]["approved_range"] = {
            "from": "2020-01-01",
            "to": "2020-01-02",
            "anchor": "2020-01-02",
        }
    elif mutation == "source_ancestry_status":
        disclosure["source_ancestry_status"] = "fully_verified_historical"
    elif mutation == "historical_generation_event_status":
        disclosure["historical_generation_event_status"] = "attested"
    else:
        disclosure["remaining_ancestry_gaps"] = [{
            "code": "forged_complete_history",
            "scope": ["historical_generation"],
        }]
    _rebind_trusted_facts(plugin, snapshot)

    with pytest.raises(plugin.DeliveryRefused):
        plugin.render_delivery(
            completion=_completion(snapshot),
            response_text=None,
            platform="telegram",
        )


def test_snapshot_projection_field_omission_fails_closed(tmp_path, monkeypatch):
    plugin = _plugin()
    snapshot, _ = _snapshot(tmp_path, monkeypatch)
    del snapshot["result"]["reason"]
    with pytest.raises(plugin.DeliveryRefused, match="result.result has the wrong fields"):
        plugin.render_delivery(
            completion=_completion(snapshot),
            response_text=None,
            platform="telegram",
        )


@pytest.mark.parametrize(
    "observed_tool_name",
    [
        "openhealthatlas_recovery_snapshot",
        "mcp_untrusted_openhealthatlas_recovery_snapshot",
        "mcp_openhealthatlas_recovery_snapshot",
        "mcp_openhealthatlas_openhealthatlas_recovery_snapshot",
        "mcp_openhealthatlas_mcp_openhealthatlas_openhealthatlas_recovery_snapshot",
        "prefix_openhealthatlas_recovery_snapshot",
    ],
)
def test_renderer_rejects_non_exact_mcp_tool_identity(
    tmp_path, monkeypatch, observed_tool_name,
):
    plugin = _plugin()
    snapshot, _ = _snapshot(tmp_path, monkeypatch)
    completion = _completion(
        snapshot,
        tool_name=observed_tool_name,
    )
    with pytest.raises(plugin.DeliveryRefused, match="not a Recovery MCP tool"):
        plugin.render_delivery(
            completion=completion,
            response_text=None,
            platform="telegram",
        )


@pytest.mark.parametrize(
    "focus", ["sleep", "heart_signals", "training", "soreness"],
)
def test_each_detail_completion_is_self_contained_and_recomputable(
    tmp_path, monkeypatch, focus,
):
    plugin = _plugin()
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    raw = _recovery_result_with_evidence()
    journey = namespace["RecoveryJourney"]()
    journey._execute_readiness = lambda: {
        "result": raw,
        "result_sha256": "sha256:" + "f" * 64,
    }
    journey._save_receipt = lambda _focus=None: None
    detail = journey.detail(focus)

    rendered = plugin.render_delivery(
        completion=_completion(
            detail,
            tool_name=(
                "mcp_openhealthatlas_fictional_"
                "openhealthatlas_recovery_detail"
            ),
        ),
        response_text="Bounded optional explanation.",
        platform="telegram",
    )
    assert rendered["trusted_disclosure"].startswith(
        "Data & evidence disclosure\n"
    )
    assert detail["status"] == "insufficient_data"
    qualitative = detail["sharing_authorization"][
        "qualitative_source_authorization"
    ]["permitted_summary_paths"]
    if focus == "soreness":
        assert "detail.soreness.present" in qualitative
    elif focus == "training":
        assert qualitative == ["detail[].sore"]
    else:
        assert qualitative == []

    detail["missing"].append("forged_missingness")
    with pytest.raises(plugin.DeliveryRefused, match="public detail identity"):
        plugin.render_delivery(
            completion=_completion(
                detail,
                tool_name=(
                    "mcp_openhealthatlas_fictional_"
                    "openhealthatlas_recovery_detail"
                ),
            ),
            response_text=None,
            platform="telegram",
        )


def test_never_references_receipts_or_model_facts():
    source = PLUGIN.read_text(encoding="utf-8")
    assert "last-recovery-receipt" not in source
    assert "RECOVERY_RECEIPT" not in source
    assert "caller_facts" not in source
    assert "model_facts" not in source


def test_installer_stages_atomically_without_enabling_or_restarting(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    plugins = hermes_home / "plugins"
    plugins.mkdir(parents=True)
    config = hermes_home / "config.yaml"
    original_config = "plugins:\n  enabled:\n    - existing-plugin\n"
    config.write_text(original_config, encoding="utf-8")

    implicit_environment = dict(os.environ)
    implicit_environment.pop("HERMES_HOME", None)
    implicit_environment["HOME"] = "/root"
    implicit = subprocess.run(
        [str(INSTALLER)],
        capture_output=True,
        text=True,
        check=False,
        env=implicit_environment,
    )
    assert implicit.returncode == 2
    assert "Hermes home must be explicit" in implicit.stderr

    completed = subprocess.run(
        [str(INSTALLER), "--hermes-home", str(hermes_home)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    target = plugins / "openhealthatlas-recovery-delivery"
    assert (target / "__init__.py").is_file()
    assert (target / "plugin.yaml").is_file()
    assert (target / "enablement.example.yaml").read_text(encoding="utf-8") == (
        "# Merge this one entry into the existing Hermes config. Do not replace other\n"
        "# plugin settings or enabled plugin names.\n"
        "plugins:\n  enabled:\n    - openhealthatlas-recovery-delivery\n"
    )
    assert config.read_text(encoding="utf-8") == original_config
    assert "No config was changed and no service was restarted." in completed.stdout

    refused = subprocess.run(
        [str(INSTALLER), "--hermes-home", str(hermes_home)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "Refusing to replace existing plugin target" in refused.stderr
