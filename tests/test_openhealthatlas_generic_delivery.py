"""Required-delivery checks for the final generic fictional evidence tool."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "deploy" / "hermes-openhealthatlas-delivery-plugin" / "__init__.py"
OBSERVED_TOOL = (
    "mcp_openhealthatlas_fictional_openhealthatlas_health_evidence"
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("oha_generic_delivery", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(value) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _completion(status: str = "ok") -> dict:
    range_value = {"from": "2026-06-01", "to": "2026-06-30"}
    query_id = "sha256:" + "a" * 64
    surface = {
        "contract": "openhealthatlas-hermes-surface-v1",
        "operation": "health_evidence",
        "data_class": "fictional",
        "fixture_id": "comprehensive-persona-v1",
        "status": status,
        "range": range_value,
        "registry_id": "sha256:" + "b" * 64,
        "engine_id": "sha256:" + "c" * 64,
        "coverage": {
            "requested_references": 1,
            "verified_references": 1,
            "referenced_statuses": {status: 1},
            "work_units": 2,
        },
        "facts": [{
            "operation": "health_query",
            "result_id": query_id,
            "verified": True,
            "status": status,
            "range": range_value,
            "coverage": {"requested_features": 2, "observed_values": 60},
            "limitations": (
                [] if status == "ok" else [{"code": status, "message": "bounded limit"}]
            ),
            "deterministic_summary": {
                "operation": "health_query",
                "status": status,
                "range": range_value,
                "rows": [{
                    "feature_key": "sleep.duration_hours",
                    "unit": "hours",
                    "aggregates": {"count": 30, "mean": 7.1},
                }],
                "total_rows": 1,
                "shown_rows": 1,
            },
        }],
        "findings": [],
        "limitations": (
            [] if status == "ok" else [{
                "operation": "health_query",
                "result_id": query_id,
                "status": status,
                "limitations": [{"code": status, "message": "bounded limit"}],
            }]
        ),
    }
    surface["result_id"] = _sha(surface)
    surface["evidence_refs"] = []
    surface["turn_id"] = "oha-generic-turn"
    surface["interpretation_writeback"] = "disabled"
    surface["non_diagnostic_text"] = (
        "This is deterministic fictional health evidence, not a diagnosis, "
        "medical advice, proof of causation, or proof that unavailable data exists."
    )
    surface["allowed_scalar_claims"] = [
        "range: 2026-06-01 to 2026-06-30",
        "evidence: 1 of 1 references verified",
        "sleep.duration_hours count: 30 observations",
        "sleep.duration_hours mean: 7.1 hours",
    ]
    facts = {
        "data_class": "fictional",
        "fixture_id": surface["fixture_id"],
        "openhealthatlas_turn_id": surface["turn_id"],
        "operation": "health_evidence",
        "status": surface["status"],
        "range": surface["range"],
        "registry_id": surface["registry_id"],
        "engine_id": surface["engine_id"],
        "result_id": surface["result_id"],
        "coverage": surface["coverage"],
        "limitations": surface["limitations"],
        "interpretation_writeback": "disabled",
        "non_diagnostic_text": surface["non_diagnostic_text"],
        "allowed_scalar_claims": surface["allowed_scalar_claims"],
    }
    completion_id = _sha({
        "contract": "hermes-gateway-required-delivery-v1",
        "mcp_tool": "openhealthatlas_health_evidence",
        "turn_id": surface["turn_id"],
        "trusted_facts": facts,
    })
    surface["delivery_contract"] = {
        "contract": "hermes-gateway-required-delivery-v1",
        "renderer_id": "openhealthatlas-generic-evidence-delivery",
        "renderer_version": "1.0.2",
        "required": True,
        "trusted_source_requirement": (
            "successful_same_turn_openhealthatlas_mcp_completion"
        ),
        "correlation": {
            "openhealthatlas_turn_id": surface["turn_id"],
            "mcp_tool": "openhealthatlas_health_evidence",
            "completion_id": completion_id,
            "gateway_tool_call_id_required": True,
        },
        "delivery_order": ["trusted_disclosure", "optional_hermes_prose"],
        "complete_telegram_payload_validation_required": True,
        "missing_or_invalid_contract_action": "fail_closed_without_hermes_prose",
        "completion_role": "final",
        "final_tool_name": OBSERVED_TOOL,
        "trusted_facts": facts,
    }
    return {
        "tool_name": OBSERVED_TOOL,
        "result": surface,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "tool_call_id": "call-1",
        "api_request_id": "request-1",
        "task_id": "task-1",
    }


def test_generic_evidence_renders_disclosure_before_labelled_hermes_prose():
    plugin = _load_plugin()
    completion = _completion()
    rendered = plugin.render_generic_delivery(
        completion=completion,
        response_text=(
            "Deterministic result\nSleep and protein were queried over the "
            "bounded fixture range.\n\nThe association boundary remains exploratory."
        ),
        platform="telegram",
    )

    assert rendered["trusted_disclosure"].startswith("Data & evidence disclosure\n")
    assert "Verified evidence references: 1/1" in rendered["trusted_disclosure"]
    assert "Deterministic result" in rendered["trusted_disclosure"]
    assert "sleep.duration_hours" in rendered["trusted_disclosure"]
    assert '"mean":7.1' in rendered["trusted_disclosure"]
    assert "Interpretation write-back: disabled" in rendered["trusted_disclosure"]
    assert completion["result"]["non_diagnostic_text"] == plugin.GENERIC_NON_DIAGNOSTIC_TEXT
    assert plugin.GENERIC_NON_DIAGNOSTIC_TEXT not in rendered["trusted_disclosure"]
    assert rendered["optional_prose"].startswith(
        "Hermes interpretation (model-generated; deterministic values above are authoritative)\n"
    )
    assert "health.db" not in json.dumps(rendered)


def test_generic_disclosure_preserves_non_ok_deterministic_status():
    plugin = _load_plugin()
    rendered = plugin.render_generic_delivery(
        completion=_completion("unsupported"),
        response_text="The requested registered feature was unavailable.",
        platform="telegram",
    )
    assert "Deterministic status: unsupported" in rendered["trusted_disclosure"]
    assert "health_query: unsupported" in rendered["trusted_disclosure"]
    assert "Deterministic status: ok" not in rendered["trusted_disclosure"]


@pytest.mark.parametrize("prose", [
    "Sleep averaged 99.9 hours.",
    "This happened on 2025-01-01.",
    "Your HRV is 1 ms.",
    "sleep.duration_hours averaged 7.1 milliseconds.",
    "sleep.duration_hours averaged 7.1 hours; wearable.hrv_ms was 7.1 ms.",
    (
        "During June 2026, sleep averaged about 7.1 hours while protein was "
        "roughly 120.7 g, so the two signals can be discussed together."
    ),
])
def test_generic_optional_prose_allows_natural_scalars_and_dates(prose):
    plugin = _load_plugin()
    rendered = plugin.render_generic_delivery(
        completion=_completion(), response_text=prose, platform="telegram",
    )
    assert rendered["trusted_disclosure"].startswith("Data & evidence disclosure")
    assert rendered["optional_prose"].startswith(
        "Hermes interpretation (model-generated; deterministic values above "
        "are authoritative)"
    )
    assert prose in rendered["optional_prose"]


def test_generic_optional_prose_allows_exact_trusted_claim_lines():
    plugin = _load_plugin()
    claim = "sleep.duration_hours mean: 7.1 hours"
    rendered = plugin.render_generic_delivery(
        completion=_completion(),
        response_text=f"The sleep result supports comparison.\n- {claim}",
        platform="telegram",
    )
    assert claim in rendered["optional_prose"]
    assert not rendered["optional_prose"].startswith("Hermes interpretation omitted")


def test_generic_causation_caveat_does_not_hide_a_later_causal_assertion():
    plugin = _load_plugin()
    caveat = "Any observed relationship would not prove causation."
    rendered = plugin.render_generic_delivery(
        completion=_completion("insufficient_data"),
        response_text=caveat,
        platform="telegram",
    )
    assert caveat in rendered["optional_prose"]
    assert not rendered["optional_prose"].startswith("Hermes interpretation omitted")

    contradicted = plugin.render_generic_delivery(
        completion=_completion("insufficient_data"),
        response_text=caveat + " This proves causation.",
        platform="telegram",
    )
    assert contradicted["optional_prose"].startswith("Hermes interpretation omitted")


def test_generic_claim_numbers_preserve_small_deterministic_values():
    plugin = _load_plugin()
    assert plugin._claim_number(0.0072) == "0.0072"
    assert plugin._claim_number(0.004) == "0.004"
    assert plugin._claim_number(-0.781) == "-0.781"


def test_plugin_recomputes_claims_independently_of_marker_hash():
    plugin = _load_plugin()
    completion = deepcopy(_completion())
    forged = "sleep.duration_hours mean: 99.9 hours"
    completion["result"]["allowed_scalar_claims"].append(forged)
    trusted = completion["result"]["delivery_contract"]["trusted_facts"]
    trusted["allowed_scalar_claims"] = completion["result"]["allowed_scalar_claims"]
    completion["result"]["delivery_contract"]["correlation"]["completion_id"] = _sha({
        "contract": "hermes-gateway-required-delivery-v1",
        "mcp_tool": "openhealthatlas_health_evidence",
        "turn_id": completion["result"]["turn_id"],
        "trusted_facts": trusted,
    })
    with pytest.raises(plugin.DeliveryRefused):
        plugin.render_generic_delivery(
            completion=completion,
            response_text=f"- {forged}",
            platform="telegram",
        )


@pytest.mark.parametrize("prose", [
    "This proves causation.",
    "Interpretation write-back is enabled.",
    "Untrusted identity sha256:" + "d" * 64,
])
def test_generic_optional_safety_failure_preserves_disclosure_only(prose):
    plugin = _load_plugin()
    rendered = plugin.render_generic_delivery(
        completion=_completion(), response_text=prose, platform="telegram",
    )
    assert rendered["trusted_disclosure"].startswith("Data & evidence disclosure")
    assert rendered["optional_prose"].startswith("Hermes interpretation omitted")


@pytest.mark.parametrize("mutation", ["result_id", "verified", "writeback"])
def test_generic_evidence_fails_closed_on_identity_or_contract_drift(mutation):
    plugin = _load_plugin()
    completion = deepcopy(_completion())
    if mutation == "result_id":
        completion["result"]["result_id"] = "sha256:" + "d" * 64
    elif mutation == "verified":
        completion["result"]["facts"][0]["verified"] = False
    else:
        completion["result"]["interpretation_writeback"] = "enabled"

    with pytest.raises(plugin.DeliveryRefused):
        plugin.render_generic_delivery(
            completion=completion,
            response_text="Explain this bounded fictional evidence.",
            platform="telegram",
        )


def test_plugin_registration_includes_generic_final_evidence_tool(caplog):
    plugin = _load_plugin()
    calls = []

    class Context:
        def register_gateway_delivery_renderer(self, *args, **kwargs):
            calls.append((args, kwargs))

    with caplog.at_level("INFO"):
        plugin.register(Context())
    assert calls[0][1]["required_tool_names"] == (
        "mcp_openhealthatlas_fictional_openhealthatlas_recovery_snapshot",
        "mcp_openhealthatlas_fictional_openhealthatlas_recovery_detail",
    )
    assert calls[1][0][:2] == (
        "openhealthatlas-generic-evidence-delivery", "1.0.2",
    )
    assert calls[1][1]["required_tool_names"] == (
        "mcp_openhealthatlas_fictional_openhealthatlas_health_catalog",
        "mcp_openhealthatlas_fictional_openhealthatlas_health_query",
        "mcp_openhealthatlas_fictional_openhealthatlas_health_analyze",
        OBSERVED_TOOL,
    )
    assert (
        "OpenHealthAtlas generic delivery renderer registered: "
        "openhealthatlas-generic-evidence-delivery@1.0.2 (telegram)"
    ) in caplog.text
