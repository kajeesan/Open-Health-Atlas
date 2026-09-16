"""Fail-closed Telegram delivery renderer for fictional OpenHealthAtlas evidence.

This plugin consumes only the successful same-turn MCP post-tool completion
provided by Hermes core. It never reads a receipt, accepts caller-supplied
health facts, or interprets model prose as trusted data.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
import logging
import re
from typing import Any


LOGGER = logging.getLogger(__name__)


RENDERER_ID = "openhealthatlas-recovery-delivery"
RENDERER_VERSION = "1.0.0"
GENERIC_RENDERER_ID = "openhealthatlas-generic-evidence-delivery"
GENERIC_RENDERER_VERSION = "1.0.2"
DELIVERY_CONTRACT = "hermes-gateway-required-delivery-v1"
RENDERED_CONTRACT = "hermes-gateway-rendered-delivery-v1"
RECOVERY_TOOL_CONTRACT = "openhealthatlas-fictional-recovery-tool-v1"
DISCLOSURE_CONTRACT = "openhealthatlas-recovery-disclosure-v1"
EVIDENCE_CONTRACT = "readiness-evidence-v2"
ANCESTRY_CONTRACT = "openhealthatlas-readiness-ancestry-v2"
AUTHORIZATION_CONTRACT = "openhealthatlas-fictional-sharing-authorization-v1"
AUTHORIZATION_ID = "openhealthatlas-fictional-recovery-telegram"
AUTHORIZATION_VERSION = "1.0.0"
NON_DIAGNOSTIC_TEXT = (
    "This is a transparent fictional-fixture heuristic, not a diagnosis, "
    "medical advice, proof of readiness, or proof of causation."
)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_SEARCH_RE = re.compile(r"sha256:[0-9a-f]{64}")
SAFE_ID_RE = re.compile(r"^-?[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
FIXTURE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
MAX_TELEGRAM_UTF16_UNITS = 4096
MAX_OPTIONAL_PROSE_CHARS = 2200

SNAPSHOT_TOOL = "openhealthatlas_recovery_snapshot"
DETAIL_TOOL = "openhealthatlas_recovery_detail"
GENERIC_EVIDENCE_TOOL = "openhealthatlas_health_evidence"
GENERIC_CATALOG_TOOL = "openhealthatlas_health_catalog"
GENERIC_QUERY_TOOL = "openhealthatlas_health_query"
GENERIC_ANALYZE_TOOL = "openhealthatlas_health_analyze"
MCP_SERVER_PREFIX = "mcp_openhealthatlas_fictional_"
OBSERVED_SNAPSHOT_TOOL = MCP_SERVER_PREFIX + SNAPSHOT_TOOL
OBSERVED_DETAIL_TOOL = MCP_SERVER_PREFIX + DETAIL_TOOL
OBSERVED_GENERIC_EVIDENCE_TOOL = MCP_SERVER_PREFIX + GENERIC_EVIDENCE_TOOL
OBSERVED_GENERIC_TOOL_NAMES = (
    MCP_SERVER_PREFIX + GENERIC_CATALOG_TOOL,
    MCP_SERVER_PREFIX + GENERIC_QUERY_TOOL,
    MCP_SERVER_PREFIX + GENERIC_ANALYZE_TOOL,
    OBSERVED_GENERIC_EVIDENCE_TOOL,
)
OBSERVED_TOOL_NAMES = {
    OBSERVED_SNAPSHOT_TOOL: SNAPSHOT_TOOL,
    OBSERVED_DETAIL_TOOL: DETAIL_TOOL,
}
GENERIC_SURFACE_CONTRACT = "openhealthatlas-hermes-surface-v1"
GENERIC_NON_DIAGNOSTIC_TEXT = (
    "This is deterministic fictional health evidence, not a diagnosis, "
    "medical advice, proof of causation, or proof that unavailable data exists."
)
GENERIC_SURFACE_FIELDS = frozenset({
    "contract", "operation", "data_class", "fixture_id", "status", "range",
    "registry_id", "engine_id", "result_id", "coverage", "facts",
    "findings", "limitations", "evidence_refs", "turn_id",
    "delivery_contract", "interpretation_writeback", "non_diagnostic_text",
    "allowed_scalar_claims",
})
GENERIC_TRUSTED_FACT_FIELDS = frozenset({
    "data_class", "fixture_id", "openhealthatlas_turn_id", "operation",
    "status", "range", "registry_id", "engine_id", "result_id", "coverage",
    "limitations", "interpretation_writeback", "non_diagnostic_text",
    "allowed_scalar_claims",
})
SNAPSHOT_RESULT_FIELDS = frozenset({
    "ok", "contract", "data_class", "fixture_id", "turn_id",
    "required_disclosure", "sharing_authorization", "delivery_contract",
    "reply_requirement", "result_sha256", "result", "missing",
    "follow_up_required",
})
DETAIL_RESULT_FIELDS = frozenset({
    "ok", "contract", "data_class", "fixture_id", "turn_id", "status", "focus",
    "required_disclosure", "sharing_authorization", "delivery_contract",
    "reply_requirement", "result_sha256", "detail", "evidence", "missing",
    "deterministic_components", "disclaimer", "instruction",
})
MODEL_RESULT_FIELDS = frozenset({
    "status", "anchor_date", "range_from", "reason", "score", "band",
    "components", "drag", "muscle_recovery", "disclaimer", "soreness",
    "evidence",
})
SNAPSHOT_MODEL_RESULT_FIELDS = frozenset({
    "status", "anchor_date", "range_from", "reason", "components",
    "muscle_recovery", "disclaimer", "soreness", "evidence",
})
EVIDENCE_FIELDS = frozenset({
    "contract", "input_fingerprint", "fingerprint_scope", "policy",
    "policy_sha256", "public_evidence_identity", "snapshot_integrity",
    "components", "soreness", "warnings", "remaining_ancestry_gaps",
})
SNAPSHOT_INTEGRITY_FIELDS = frozenset({
    "contract", "status", "scope", "schema_version",
    "external_provider_sync", "training_ancestry",
})
DISCLOSURE_FIELDS = frozenset({
    "contract", "data_class", "fixture_id", "approved_range", "destination",
    "sharing_scope", "shared_field_groups", "shared_field_paths",
    "evidence_identity", "calculation_policy", "public_evidence_identity",
    "deterministic_components", "snapshot_integrity",
    "component_missingness", "source_ancestry_status",
    "historical_generation_event_status", "remaining_ancestry_gaps",
    "interpretation_writeback", "excluded_field_groups",
    "non_diagnostic_text",
})
EVIDENCE_IDENTITY_FIELDS = frozenset({
    "result_sha256", "result_identity_scope", "input_fingerprint",
})
DELIVERY_FIELDS = frozenset({
    "contract", "renderer_id", "renderer_version", "required",
    "trusted_source_requirement",
    "correlation", "delivery_order",
    "complete_telegram_payload_validation_required",
    "missing_or_invalid_contract_action", "trusted_facts",
})
GENERIC_DELIVERY_FIELDS = frozenset(
    set(DELIVERY_FIELDS) | {"completion_role", "final_tool_name"}
)
TRUSTED_FACT_FIELDS = frozenset({
    "data_class", "fixture_id", "openhealthatlas_turn_id", "result_status",
    "approved_range", "result_sha256", "input_fingerprint",
    "calculation_policy", "public_evidence_identity", "snapshot_integrity",
    "component_missingness", "required_disclosure", "sharing_authorization",
    "non_diagnostic_text", "interpretation_writeback",
})
CORRELATION_FIELDS = frozenset({
    "openhealthatlas_turn_id", "mcp_tool", "completion_id",
    "gateway_tool_call_id_required",
})
AUTHORIZATION_FIELDS = frozenset({
    "contract", "authorization_id", "version", "status", "data_class",
    "fixture_id", "destination", "destination_binding", "audience", "approved_range",
    "permitted_field_paths", "excluded_field_groups",
    "interpretation_writeback", "calculation_fingerprint_member",
    "qualitative_source_authorization", "real_data_governance_gate",
})
QUALITATIVE_AUTHORIZATION_FIELDS = frozenset({
    "contract", "authorization_id", "version", "status",
    "permitted_summary_paths", "raw_note_permitted",
    "note_derived_secret_permitted",
})
PRIVATE_KEYS = frozenset({
    "content_fingerprint", "note", "raw_note", "note_digest", "health_db",
    "database_sha256", "database_identity", "canonical_row_digest",
    "canonical_rows_sha256", "canonical_recovery_rows_sha256",
    "sidecar_path", "sidecar_sha256",
    "sidecar_identity", "manifest_row_hashes", "private_sidecar",
})
APPROVED_RANGE = {
    "from": "2026-03-02",
    "to": "2026-06-30",
    "anchor": "2026-06-30",
}
EXPECTED_EXCLUDED_FIELD_GROUPS = [
    "private_health_data",
    "database_paths_and_contents",
    "vault_paths_and_contents",
    "raw_history_rows",
    "verbatim_soreness_note",
    "soreness_content_fingerprint",
    "private_ancestry_sidecar_paths_and_contents",
    "canonical_row_digests",
    "canonical_recovery_rows_sha256",
    "full_database_identity",
]
COMMON_PERMITTED_FIELD_PATHS = [
    "data_class",
    "fixture_id",
    "turn_id",
    "result_sha256",
    "missing[]",
    "required_disclosure",
    "reply_requirement",
    "sharing_authorization",
    "delivery_contract",
]


class DeliveryRefused(ValueError):
    """The completion cannot be delivered under the required contract."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeliveryRefused(f"{label} must be an object")
    return value


def _exact_fields(value: dict[str, Any], fields: frozenset[str], label: str) -> None:
    if set(value) != fields:
        raise DeliveryRefused(f"{label} has the wrong fields")


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DeliveryRefused(f"{label} is invalid")
    return value


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or SAFE_ID_RE.fullmatch(value) is None:
        raise DeliveryRefused(f"{label} is invalid")
    return value


def _no_private_keys(value: object, path: str = "result") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise DeliveryRefused(f"{path} has a non-string key")
            normalized = key.lower()
            if normalized in PRIVATE_KEYS or normalized.startswith("private_"):
                raise DeliveryRefused(f"{path}.{key} is not gateway-facing")
            _no_private_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _no_private_keys(child, f"{path}[{index}]")


def _trusted_sha_values(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            found.update(_trusted_sha_values(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_trusted_sha_values(child))
    elif isinstance(value, str) and SHA256_RE.fullmatch(value):
        found.add(value)
    return found


def _claim_number(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def _expected_scalar_claims(result: dict[str, Any]) -> list[str]:
    """Rebuild canonical scalar summaries retained for contract compatibility."""

    claims: list[str] = []
    range_value = result.get("range")
    if isinstance(range_value, dict):
        claims.append(f"range: {range_value.get('from')} to {range_value.get('to')}")
    coverage = result.get("coverage")
    if isinstance(coverage, dict):
        verified = coverage.get("verified_references")
        requested = coverage.get("requested_references")
        if type(verified) is int and type(requested) is int:
            claims.append(f"evidence: {verified} of {requested} references verified")
    for evidence_item in result.get("facts", []):
        if not isinstance(evidence_item, dict):
            continue
        summary = evidence_item.get("deterministic_summary")
        if not isinstance(summary, dict):
            continue
        operation = summary.get("operation")
        for row in summary.get("rows", []):
            if not isinstance(row, dict):
                continue
            if operation == "health_query":
                feature = row.get("feature_key")
                if not isinstance(feature, str):
                    continue
                unit = row.get("unit") if isinstance(row.get("unit"), str) else ""
                latest = row.get("latest")
                if isinstance(latest, dict):
                    number = _claim_number(latest.get("value"))
                    latest_unit = latest.get("unit") if isinstance(latest.get("unit"), str) else unit
                    observed_at = latest.get("observed_at")
                    if number is not None and isinstance(observed_at, str):
                        claims.append(
                            f"{feature} latest: {number} {latest_unit} on {observed_at}".replace("  ", " ")
                        )
                aggregates = row.get("aggregates")
                if isinstance(aggregates, dict):
                    for name in sorted(aggregates):
                        number = _claim_number(aggregates[name])
                        if number is not None:
                            claim_unit = "observations" if name == "count" else unit
                            claims.append(
                                f"{feature} {name}: {number} {claim_unit}".strip()
                            )
                for section_name in ("baseline", "current"):
                    section = row.get(section_name)
                    if not isinstance(section, dict):
                        continue
                    section_aggregates = section.get("aggregates")
                    if isinstance(section_aggregates, dict):
                        for name in sorted(section_aggregates):
                            number = _claim_number(section_aggregates[name])
                            if number is not None:
                                claim_unit = "observations" if name == "count" else unit
                                claims.append(
                                    f"{feature} {section_name} {name}: {number} {claim_unit}".strip()
                                )
                delta = row.get("delta")
                if isinstance(delta, dict):
                    for name in sorted(delta):
                        number = _claim_number(delta[name])
                        if number is not None:
                            claim_unit = "observations" if name == "count" else unit
                            claims.append(
                                f"{feature} change in {name}: {number} {claim_unit}".strip()
                            )
            elif operation == "health_analyze":
                finding_id = row.get("finding_id")
                effect = row.get("effect") if isinstance(row.get("effect"), dict) else {}
                sample = row.get("sample") if isinstance(row.get("sample"), dict) else {}
                testing = row.get("testing") if isinstance(row.get("testing"), dict) else {}
                stability = row.get("stability") if isinstance(row.get("stability"), dict) else {}
                estimate = _claim_number(effect.get("oriented_estimate", effect.get("estimate")))
                complete_n = _claim_number(sample.get("complete_n"))
                q_value = _claim_number(testing.get("q"))
                if isinstance(finding_id, str) and estimate and complete_n and q_value:
                    claims.append(
                        f"{finding_id} effect: {estimate}; n: {complete_n}; q: {q_value}; "
                        f"stability: {stability.get('status')}"
                    )
    return list(dict.fromkeys(claim for claim in claims if len(claim) <= 300))[:80]


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _validate_snapshot_integrity(value: object) -> dict[str, Any]:
    integrity = _dict(value, "snapshot_integrity")
    _exact_fields(integrity, SNAPSHOT_INTEGRITY_FIELDS, "snapshot_integrity")
    if (
        integrity.get("contract") != ANCESTRY_CONTRACT
        or integrity.get("status") != "verified"
        or integrity.get("scope")
        != "current_snapshot_integrity_and_reproducibility"
        or integrity.get("external_provider_sync") != "not_performed"
        or integrity.get("training_ancestry") != "manifested"
        or not isinstance(integrity.get("schema_version"), int)
        or isinstance(integrity.get("schema_version"), bool)
        or integrity.get("schema_version") != 5
    ):
        raise DeliveryRefused("snapshot_integrity is invalid")
    return integrity


def _validate_authorization(
    value: object,
    fixture_id: str,
    qualitative_paths: list[str],
    permitted_field_paths: list[str],
) -> dict[str, Any]:
    authorization = _dict(value, "sharing_authorization")
    _exact_fields(authorization, AUTHORIZATION_FIELDS, "sharing_authorization")
    gate = _dict(authorization.get("real_data_governance_gate"), "governance gate")
    qualitative = _dict(
        authorization.get("qualitative_source_authorization"),
        "qualitative source authorization",
    )
    _exact_fields(
        qualitative,
        QUALITATIVE_AUTHORIZATION_FIELDS,
        "qualitative source authorization",
    )
    if (
        authorization.get("contract") != AUTHORIZATION_CONTRACT
        or authorization.get("authorization_id") != AUTHORIZATION_ID
        or authorization.get("version") != AUTHORIZATION_VERSION
        or authorization.get("status") != "authorized"
        or authorization.get("data_class") != "fictional"
        or authorization.get("fixture_id") != fixture_id
        or authorization.get("destination")
        != "telegram_via_external_hermes_gateway"
        or authorization.get("destination_binding")
        != "same_authenticated_gateway_source"
        or authorization.get("audience") != "fictional_recovery_acceptance"
        or authorization.get("approved_range") != APPROVED_RANGE
        or authorization.get("interpretation_writeback") != "disabled"
        or authorization.get("calculation_fingerprint_member") is not False
        or authorization.get("permitted_field_paths") != permitted_field_paths
        or authorization.get("excluded_field_groups")
        != EXPECTED_EXCLUDED_FIELD_GROUPS
        or qualitative != {
            "contract": "openhealthatlas-qualitative-source-authorization-v1",
            "authorization_id": (
                "openhealthatlas-fictional-soreness-summary-telegram"
            ),
            "version": "1.0.0",
            "status": "authorized",
            "permitted_summary_paths": qualitative_paths,
            "raw_note_permitted": False,
            "note_derived_secret_permitted": False,
        }
        or gate != {
            "contract": "openhealthatlas-real-data-governance-gate-v1",
            "version": "1.0.0",
            "status": "blocked_unresolved_decisions",
        }
    ):
        raise DeliveryRefused("sharing_authorization is invalid")
    return authorization


def _expected_permitted_field_paths(
    tool_name: str, result: dict[str, Any]
) -> list[str]:
    if tool_name == SNAPSHOT_TOOL:
        return COMMON_PERMITTED_FIELD_PATHS + [
            "result.status",
            "result.anchor_date",
            "result.range_from",
            "result.reason",
            "result.components[]",
            "result.muscle_recovery[]",
            "result.soreness.{present,date,source_label,source_locator}",
            "result.disclaimer",
            "result.evidence",
            "follow_up_required",
        ]
    detail_path = {
        "sleep": "detail[]",
        "heart_signals": "detail[]",
        "training": "detail[]",
        "soreness": "detail.{soreness,flagged_groups[]}",
    }.get(result.get("focus"))
    if detail_path is None:
        raise DeliveryRefused("detail focus is invalid")
    return COMMON_PERMITTED_FIELD_PATHS + [
        "status",
        "focus",
        "deterministic_components",
        detail_path,
        "evidence",
        "disclaimer",
        "instruction",
    ]


def _expected_shared_field_groups(tool_name: str, result: dict[str, Any]) -> list[str]:
    focus = result.get("focus") if tool_name == DETAIL_TOOL else None
    groups = []
    if tool_name == SNAPSHOT_TOOL or focus in {"sleep", "heart_signals"}:
        groups.append("derived_recovery_components")
    if tool_name == SNAPSHOT_TOOL or focus == "training":
        groups.append("derived_training_recovery")
    if tool_name == SNAPSHOT_TOOL or focus == "soreness":
        groups.append("derived_soreness_flags")
    groups.extend(["governed_evidence_identity", "component_missingness"])
    return groups


def _validate_completion(completion: object) -> tuple[str, dict[str, Any]]:
    event = _dict(completion, "completion")
    expected_event_fields = {
        "tool_name", "result", "session_id", "turn_id", "tool_call_id",
        "api_request_id", "task_id",
    }
    if set(event) != expected_event_fields:
        raise DeliveryRefused("completion has the wrong fields")
    observed_tool_name = event.get("tool_name")
    tool_name = OBSERVED_TOOL_NAMES.get(observed_tool_name)
    if tool_name is None:
        raise DeliveryRefused("completion is not a Recovery MCP tool")
    for field in ("session_id", "turn_id", "tool_call_id"):
        _safe_id(event.get(field), f"completion.{field}")
    result = _dict(event.get("result"), "completion.result")
    expected_result_fields = (
        SNAPSHOT_RESULT_FIELDS if tool_name == SNAPSHOT_TOOL else DETAIL_RESULT_FIELDS
    )
    _exact_fields(result, expected_result_fields, "completion.result")
    if (
        result.get("ok") is not True
        or result.get("contract") != RECOVERY_TOOL_CONTRACT
        or result.get("data_class") != "fictional"
        or result.get("fixture_id") != "comprehensive-persona-v1"
    ):
        raise DeliveryRefused("completion.result is not an accepted fictional result")
    _no_private_keys(result)
    return tool_name, result


def _validate_model_and_evidence(
    tool_name: str, result: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if tool_name == SNAPSHOT_TOOL:
        model_result = _dict(result.get("result"), "result.result")
        _exact_fields(model_result, SNAPSHOT_MODEL_RESULT_FIELDS, "result.result")
        evidence = _dict(model_result.get("evidence"), "result.result.evidence")
        public_preimage = model_result
    else:
        evidence = _dict(result.get("evidence"), "result.evidence")
        # Detail carries the same evidence plus a bounded focus projection. Its
        # public hash was calculated over the full privacy-safe model result
        # before focus selection, so the evidence itself remains authoritative.
        public_preimage = None
    _exact_fields(evidence, EVIDENCE_FIELDS, "readiness evidence")
    if evidence.get("contract") != EVIDENCE_CONTRACT:
        raise DeliveryRefused("readiness evidence contract is invalid")
    _sha(evidence.get("input_fingerprint"), "input_fingerprint")
    policy_sha256 = _sha(evidence.get("policy_sha256"), "policy_sha256")
    public_evidence_identity = _sha(
        evidence.get("public_evidence_identity"), "public_evidence_identity"
    )
    _validate_snapshot_integrity(evidence.get("snapshot_integrity"))
    if policy_sha256 != _sha256(evidence.get("policy")):
        raise DeliveryRefused("policy identity does not match policy")
    evidence_preimage = {
        key: value for key, value in evidence.items()
        if key != "public_evidence_identity"
    }
    if public_evidence_identity != _sha256(evidence_preimage):
        raise DeliveryRefused("public evidence identity does not match evidence")
    if public_preimage is not None:
        expected_public_hash = _sha256({
            "contract": "openhealthatlas-public-recovery-snapshot-v1",
            "projection": public_preimage,
        })
        if result.get("result_sha256") != expected_public_hash:
            raise DeliveryRefused("public result identity does not match projection")
    else:
        detail_projection = {
            "status": result.get("status"),
            "focus": result.get("focus"),
            "detail": result.get("detail"),
            "evidence": result.get("evidence"),
            "deterministic_components": result.get("deterministic_components"),
            "missing": result.get("missing"),
            "disclaimer": result.get("disclaimer"),
            "instruction": result.get("instruction"),
        }
        expected_public_hash = _sha256({
            "contract": "openhealthatlas-public-recovery-detail-v1",
            "projection": detail_projection,
        })
        if result.get("result_sha256") != expected_public_hash:
            raise DeliveryRefused("public detail identity does not match projection")
    return evidence, public_preimage or {}


def _validate_delivery(
    tool_name: str, result: dict[str, Any], evidence: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    marker = _dict(result.get("delivery_contract"), "delivery_contract")
    _exact_fields(marker, DELIVERY_FIELDS, "delivery_contract")
    if (
        marker.get("contract") != DELIVERY_CONTRACT
        or marker.get("renderer_id") != RENDERER_ID
        or marker.get("renderer_version") != RENDERER_VERSION
        or marker.get("required") is not True
        or marker.get("trusted_source_requirement")
        != "successful_same_turn_openhealthatlas_mcp_completion"
        or marker.get("delivery_order")
        != ["trusted_disclosure", "optional_hermes_prose"]
        or marker.get("complete_telegram_payload_validation_required") is not True
        or marker.get("missing_or_invalid_contract_action")
        != "fail_closed_without_hermes_prose"
    ):
        raise DeliveryRefused("delivery_contract is invalid")
    correlation = _dict(marker.get("correlation"), "delivery correlation")
    _exact_fields(correlation, CORRELATION_FIELDS, "delivery correlation")
    if (
        correlation.get("mcp_tool") != tool_name
        or correlation.get("openhealthatlas_turn_id") != result.get("turn_id")
        or correlation.get("gateway_tool_call_id_required") is not True
    ):
        raise DeliveryRefused("delivery correlation is invalid")
    _sha(correlation.get("completion_id"), "completion_id")
    disclosure = _dict(result.get("required_disclosure"), "required_disclosure")
    _exact_fields(disclosure, DISCLOSURE_FIELDS, "required_disclosure")
    evidence_identity = _dict(
        disclosure.get("evidence_identity"), "disclosure evidence_identity"
    )
    _exact_fields(
        evidence_identity,
        EVIDENCE_IDENTITY_FIELDS,
        "disclosure evidence_identity",
    )
    permitted_field_paths = _expected_permitted_field_paths(tool_name, result)
    shared_field_groups = _expected_shared_field_groups(tool_name, result)
    if tool_name == SNAPSHOT_TOOL:
        qualitative_paths = [
            "result.soreness.present", "result.soreness.date",
            "result.soreness.source_label", "result.soreness.source_locator",
            "result.muscle_recovery[].sore",
        ]
    elif result.get("focus") == "soreness":
        qualitative_paths = [
            "detail.soreness.present", "detail.soreness.date",
            "detail.soreness.source_label", "detail.soreness.source_locator",
            "detail.flagged_groups[].sore",
        ]
    elif result.get("focus") == "training":
        qualitative_paths = ["detail[].sore"]
    else:
        qualitative_paths = []
    authorization = _validate_authorization(
        result.get("sharing_authorization"),
        result["fixture_id"],
        qualitative_paths,
        permitted_field_paths,
    )
    facts = _dict(marker.get("trusted_facts"), "trusted_facts")
    _exact_fields(facts, TRUSTED_FACT_FIELDS, "trusted_facts")
    policy = _dict(evidence.get("policy"), "calculation policy")
    expected_policy = {
        "policy_id": policy.get("policy_id"),
        "version": policy.get("version"),
        "policy_sha256": evidence["policy_sha256"],
    }
    expected_status = (
        result.get("result", {}).get("status")
        if tool_name == SNAPSHOT_TOOL else result.get("status")
    )
    component_summary = disclosure.get("deterministic_components")
    missingness = disclosure.get("component_missingness")
    excluded_rows = (
        missingness.get("excluded") if isinstance(missingness, dict) else None
    )
    excluded_by_key = {
        row.get("key"): row
        for row in excluded_rows or []
        if isinstance(row, dict)
    }
    result_components = (
        result.get("result", {}).get("components")
        if tool_name == SNAPSHOT_TOOL
        else result.get("deterministic_components")
    )
    warning_by_key = {
        row.get("component"): row
        for row in evidence.get("warnings", [])
        if isinstance(row, dict)
    }
    expected_exclusions = []
    for row in evidence.get("components", []):
        if not isinstance(row, dict) or row.get("status") != "excluded":
            continue
        warning = warning_by_key.get(row.get("key"), {})
        expected_exclusions.append({
            "key": row.get("key"),
            "reason_code": row.get("reason_code"),
            "source_label": warning.get("source_label"),
            "required": warning.get("required"),
            "observed": warning.get("observed"),
            "other_source_observations_excluded": warning.get(
                "other_source_observations_excluded"
            ),
        })
    expected_missingness = {
        "status": "none_missing" if not result.get("missing") else "missing",
        "missing": result.get("missing"),
        "excluded": expected_exclusions,
    }
    expected_identity = {
        "result_sha256": result["result_sha256"],
        "result_identity_scope": (
            "privacy_safe_snapshot_projection"
            if tool_name == SNAPSHOT_TOOL
            else "privacy_safe_detail_projection"
        ),
        "input_fingerprint": evidence["input_fingerprint"],
    }
    if (
        disclosure.get("contract") != DISCLOSURE_CONTRACT
        or disclosure.get("data_class") != "fictional"
        or disclosure.get("fixture_id") != result["fixture_id"]
        or disclosure.get("approved_range") != APPROVED_RANGE
        or disclosure.get("destination")
        != "Hermes reasoning model through the existing Telegram gateway"
        or disclosure.get("sharing_scope") != "fictional_recovery_only"
        or disclosure.get("shared_field_groups") != shared_field_groups
        or disclosure.get("shared_field_paths") != permitted_field_paths
        or disclosure.get("excluded_field_groups")
        != EXPECTED_EXCLUDED_FIELD_GROUPS
        or evidence_identity != expected_identity
        or disclosure.get("calculation_policy") != expected_policy
        or expected_policy.get("policy_id") != "openhealthatlas-readiness-policy"
        or expected_policy.get("version") != "1.0.0"
        or disclosure.get("public_evidence_identity")
        != evidence["public_evidence_identity"]
        or disclosure.get("snapshot_integrity") != evidence["snapshot_integrity"]
        or disclosure.get("component_missingness") != expected_missingness
        or disclosure.get("source_ancestry_status")
        != "verified_current_snapshot"
        or disclosure.get("historical_generation_event_status")
        != "not_attested"
        or disclosure.get("remaining_ancestry_gaps")
        != evidence.get("remaining_ancestry_gaps")
        or disclosure.get("interpretation_writeback") != "disabled"
        or disclosure.get("non_diagnostic_text") != NON_DIAGNOSTIC_TEXT
        or facts.get("data_class") != "fictional"
        or facts.get("fixture_id") != result["fixture_id"]
        or facts.get("openhealthatlas_turn_id") != result["turn_id"]
        or facts.get("result_status") != expected_status
        or facts.get("approved_range") != APPROVED_RANGE
        or expected_status != "insufficient_data"
        or not isinstance(component_summary, list)
        or component_summary != result_components
        or len(component_summary) != 1
        or component_summary[0].get("key") != "sleep"
        or component_summary[0].get("score") != 92
        or set(excluded_by_key) != {"hrv", "rhr"}
        or excluded_rows != expected_exclusions
        or any(
            excluded_by_key[key].get("source_label") != "fitbit"
            or excluded_by_key[key].get("observed") != 10
            or excluded_by_key[key].get("required") != 14
            or excluded_by_key[key].get("other_source_observations_excluded") != 86
            for key in ("hrv", "rhr")
        )
        or facts.get("result_sha256") != result["result_sha256"]
        or facts.get("input_fingerprint") != evidence["input_fingerprint"]
        or facts.get("calculation_policy") != expected_policy
        or facts.get("public_evidence_identity")
        != evidence["public_evidence_identity"]
        or facts.get("snapshot_integrity") != evidence["snapshot_integrity"]
        or facts.get("component_missingness")
        != disclosure.get("component_missingness")
        or facts.get("required_disclosure") != disclosure
        or facts.get("sharing_authorization") != authorization
        or facts.get("non_diagnostic_text") != NON_DIAGNOSTIC_TEXT
        or facts.get("interpretation_writeback") != "disabled"
    ):
        raise DeliveryRefused("trusted facts do not match the MCP result")
    expected_completion_id = _sha256({
        "contract": DELIVERY_CONTRACT,
        "mcp_tool": tool_name,
        "turn_id": result["turn_id"],
        "trusted_facts": facts,
    })
    if correlation["completion_id"] != expected_completion_id:
        raise DeliveryRefused("completion_id does not match trusted facts")
    return disclosure, authorization, facts


def _validate_generic_completion(
    completion: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one same-turn generic evidence completion and its marker."""

    event = _dict(completion, "completion")
    expected_event_fields = {
        "tool_name", "result", "session_id", "turn_id", "tool_call_id",
        "api_request_id", "task_id",
    }
    if set(event) != expected_event_fields:
        raise DeliveryRefused("completion has the wrong fields")
    if event.get("tool_name") != OBSERVED_GENERIC_EVIDENCE_TOOL:
        raise DeliveryRefused("completion is not the generic evidence MCP tool")
    for field in ("session_id", "turn_id", "tool_call_id"):
        _safe_id(event.get(field), f"completion.{field}")
    result = _dict(event.get("result"), "completion.result")
    _exact_fields(result, GENERIC_SURFACE_FIELDS, "completion.result")
    if (
        result.get("contract") != GENERIC_SURFACE_CONTRACT
        or result.get("operation") != "health_evidence"
        or result.get("data_class") != "fictional"
        or result.get("fixture_id") != "comprehensive-persona-v1"
        or result.get("status") not in {
            "ok", "insufficient_data", "unsupported", "refused",
        }
        or result.get("interpretation_writeback") != "disabled"
        or result.get("non_diagnostic_text") != GENERIC_NON_DIAGNOSTIC_TEXT
        or not isinstance(result.get("coverage"), dict)
        or any(
            not isinstance(result.get(key), list)
            for key in (
                "facts", "findings", "limitations", "evidence_refs",
                "allowed_scalar_claims",
            )
        )
        or result.get("findings") != []
        or result.get("evidence_refs") != []
        or len(result.get("allowed_scalar_claims", [])) > 80
        or len(result.get("allowed_scalar_claims", []))
        != len(set(result.get("allowed_scalar_claims", [])))
        or any(
            not isinstance(claim, str) or not claim or len(claim) > 300
            for claim in result.get("allowed_scalar_claims", [])
        )
        or result.get("allowed_scalar_claims") != _expected_scalar_claims(result)
    ):
        raise DeliveryRefused("generic evidence result is invalid")
    for key in ("registry_id", "engine_id", "result_id"):
        _sha(result.get(key), key)
    range_value = result.get("range")
    if range_value is not None:
        if not isinstance(range_value, dict) or set(range_value) != {"from", "to"}:
            raise DeliveryRefused("generic evidence range is invalid")
        try:
            start = date.fromisoformat(range_value["from"])
            end = date.fromisoformat(range_value["to"])
        except (TypeError, ValueError) as exc:
            raise DeliveryRefused("generic evidence range is invalid") from exc
        if (
            start.isoformat() != range_value["from"]
            or end.isoformat() != range_value["to"]
            or start > end
            or start < date.fromisoformat(APPROVED_RANGE["from"])
            or end > date.fromisoformat(APPROVED_RANGE["to"])
        ):
            raise DeliveryRefused("generic evidence range is outside the fictional scope")
    requested = result["coverage"].get("requested_references")
    verified = result["coverage"].get("verified_references")
    if (
        type(requested) is not int
        or type(verified) is not int
        or not 1 <= requested <= 20
        or verified != requested
        or len(result["facts"]) != verified
        or any(
            not isinstance(item, dict) or item.get("verified") is not True
            for item in result["facts"]
        )
    ):
        raise DeliveryRefused("generic evidence verification count is invalid")
    _no_private_keys(result)
    surface_preimage = {
        key: result[key]
        for key in (
            "contract", "operation", "data_class", "fixture_id", "status",
            "range", "registry_id", "engine_id", "coverage", "facts",
            "findings", "limitations",
        )
    }
    if result["result_id"] != _sha256(surface_preimage):
        raise DeliveryRefused("generic evidence result identity does not match")

    marker = _dict(result.get("delivery_contract"), "delivery_contract")
    _exact_fields(marker, GENERIC_DELIVERY_FIELDS, "delivery_contract")
    if (
        marker.get("contract") != DELIVERY_CONTRACT
        or marker.get("renderer_id") != GENERIC_RENDERER_ID
        or marker.get("renderer_version") != GENERIC_RENDERER_VERSION
        or marker.get("required") is not True
        or marker.get("trusted_source_requirement")
        != "successful_same_turn_openhealthatlas_mcp_completion"
        or marker.get("delivery_order")
        != ["trusted_disclosure", "optional_hermes_prose"]
        or marker.get("complete_telegram_payload_validation_required") is not True
        or marker.get("missing_or_invalid_contract_action")
        != "fail_closed_without_hermes_prose"
        or marker.get("completion_role") != "final"
        or marker.get("final_tool_name") != OBSERVED_GENERIC_EVIDENCE_TOOL
    ):
        raise DeliveryRefused("generic evidence delivery contract is invalid")
    correlation = _dict(marker.get("correlation"), "delivery correlation")
    _exact_fields(correlation, CORRELATION_FIELDS, "delivery correlation")
    if (
        correlation.get("mcp_tool") != GENERIC_EVIDENCE_TOOL
        or correlation.get("openhealthatlas_turn_id") != result.get("turn_id")
        or correlation.get("gateway_tool_call_id_required") is not True
    ):
        raise DeliveryRefused("generic evidence correlation is invalid")
    _sha(correlation.get("completion_id"), "completion_id")
    facts = _dict(marker.get("trusted_facts"), "trusted_facts")
    _exact_fields(facts, GENERIC_TRUSTED_FACT_FIELDS, "trusted_facts")
    expected_facts = {
        "data_class": "fictional",
        "fixture_id": result["fixture_id"],
        "openhealthatlas_turn_id": result["turn_id"],
        "operation": "health_evidence",
        "status": result["status"],
        "range": result["range"],
        "registry_id": result["registry_id"],
        "engine_id": result["engine_id"],
        "result_id": result["result_id"],
        "coverage": result["coverage"],
        "limitations": result["limitations"],
        "interpretation_writeback": "disabled",
        "non_diagnostic_text": GENERIC_NON_DIAGNOSTIC_TEXT,
        "allowed_scalar_claims": result["allowed_scalar_claims"],
    }
    if facts != expected_facts:
        raise DeliveryRefused("generic evidence trusted facts do not match")
    expected_completion_id = _sha256({
        "contract": DELIVERY_CONTRACT,
        "mcp_tool": GENERIC_EVIDENCE_TOOL,
        "turn_id": result["turn_id"],
        "trusted_facts": facts,
    })
    if correlation["completion_id"] != expected_completion_id:
        raise DeliveryRefused("generic evidence completion_id does not match")
    return result, facts


def _bounded_generic_prose(
    response_text: object, *, result: dict[str, Any]
) -> str | None:
    if response_text is None:
        return None
    if not isinstance(response_text, str):
        raise DeliveryRefused("optional Hermes prose must be text")
    prose = response_text.strip()
    if not prose:
        return None
    labelled = (
        "Hermes interpretation (model-generated; deterministic values above "
        "are authoritative)\n" + prose
    )
    if len(labelled) > MAX_OPTIONAL_PROSE_CHARS:
        raise DeliveryRefused("optional Hermes prose is too long")
    # A directly negated proof claim is a caveat. Check every occurrence so
    # that a caveat cannot conceal an affirmative claim elsewhere in the reply.
    causal_assertion = any(
        re.search(
            r"\b(?:not|never|cannot|[a-z]+n['’]t)(?:\s+necessarily)?\s*$",
            prose[:match.start()],
            flags=re.IGNORECASE,
        ) is None
        for match in re.finditer(r"\bproves?\s+causation\b", prose, re.IGNORECASE)
    )
    if causal_assertion or re.search(
        r"\b(?:this|the result|the evidence)\s+(?:is|shows|proves)\s+"
        r"(?:a\s+)?diagnos(?:is|tic)\b|"
        r"\b(?:sleep|hrv|training|nutrition|pain|mood)\s+caused\b",
        prose,
        flags=re.IGNORECASE,
    ):
        raise DeliveryRefused("optional prose contains an explicit diagnostic or causal claim")
    if re.search(r"\b(?:interpretation\s+)?write[- ]?back\s+(?:is\s+)?enabled\b", prose, re.I):
        raise DeliveryRefused("optional prose claims write-back is enabled")
    allowed_hashes = _trusted_sha_values(result)
    if any(match not in allowed_hashes for match in SHA256_SEARCH_RE.findall(prose)):
        raise DeliveryRefused("optional prose contains an untrusted identity")
    return labelled


def _render_generic_disclosure(
    result: dict[str, Any], facts: dict[str, Any]
) -> str:
    range_value = facts.get("range")
    range_text = (
        f"{range_value['from']} to {range_value['to']}"
        if isinstance(range_value, dict)
        else "multiple bounded fictional ranges"
    )
    deterministic_lines = ["Deterministic result"]
    shown = 0
    total = 0
    for evidence_item in result["facts"]:
        summary = evidence_item.get("deterministic_summary")
        if not isinstance(summary, dict):
            raise DeliveryRefused("generic deterministic summary is missing")
        operation = summary.get("operation")
        status = summary.get("status")
        rows = summary.get("rows")
        if (
            operation not in {"health_catalog", "health_query", "health_analyze"}
            or status not in {"ok", "insufficient_data", "unsupported", "refused"}
            or not isinstance(rows, list)
            or type(summary.get("total_rows")) is not int
            or type(summary.get("shown_rows")) is not int
            or summary["shown_rows"] != len(rows)
            or not 0 <= summary["shown_rows"] <= summary["total_rows"]
        ):
            raise DeliveryRefused("generic deterministic summary is invalid")
        total += summary["total_rows"]
        deterministic_lines.append(f"{operation}: {status}")
        for row in rows:
            if shown >= 12:
                break
            if not isinstance(row, dict):
                raise DeliveryRefused("generic deterministic summary row is invalid")
            if operation == "health_catalog":
                text = (
                    f"- {row.get('feature_key')} · {row.get('display_name')} "
                    f"· {row.get('availability')}"
                )
            elif operation == "health_query":
                detail = row.get("latest")
                if isinstance(detail, dict):
                    rendered_detail = (
                        f"latest {detail.get('value')} {detail.get('unit') or ''} "
                        f"at {detail.get('observed_at')}"
                    ).strip()
                elif isinstance(row.get("aggregates"), dict):
                    rendered_detail = (
                        "aggregates " + _canonical(row["aggregates"])
                        + f" {row.get('unit') or ''}"
                    ).strip()
                elif isinstance(row.get("baseline"), dict) and isinstance(row.get("current"), dict):
                    rendered_detail = (
                        "baseline "
                        + _canonical(row["baseline"].get("aggregates", {}))
                        + " · current "
                        + _canonical(row["current"].get("aggregates", {}))
                        + " · delta "
                        + _canonical(row.get("delta", {}))
                        + f" {row.get('unit') or ''}"
                    ).strip()
                elif isinstance(row.get("delta"), dict):
                    rendered_detail = (
                        "delta " + _canonical(row["delta"])
                        + f" {row.get('unit') or ''}"
                    ).strip()
                else:
                    rendered_detail = (
                        f"value {row.get('value')} at {row.get('observed_at')}"
                    )
                text = f"- {row.get('feature_key')} · {rendered_detail}"
            else:
                components = row.get("exposure_components")
                component = components[0] if isinstance(components, list) and components else {}
                outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
                effect = row.get("effect") if isinstance(row.get("effect"), dict) else {}
                sample = row.get("sample") if isinstance(row.get("sample"), dict) else {}
                testing = row.get("testing") if isinstance(row.get("testing"), dict) else {}
                stability = row.get("stability") if isinstance(row.get("stability"), dict) else {}
                text = (
                    f"- {component.get('exposure_key')} → {outcome.get('key')} · "
                    f"effect {effect.get('oriented_estimate', effect.get('estimate'))} · "
                    f"n {sample.get('complete_n')} · q {testing.get('q')} · "
                    f"stability {stability.get('status')}"
                )
            if len(text) > 300:
                raise DeliveryRefused("generic deterministic summary row is too long")
            deterministic_lines.append(text)
            shown += 1
    deterministic_lines.append(f"Displayed deterministic rows: {shown}/{total}")
    return "\n".join([
        "Data & evidence disclosure",
        f"Fictional scope: {facts['fixture_id']} · {range_text}",
        f"Deterministic operation: {facts['operation']}",
        f"Deterministic status: {facts['status']}",
        "Verified evidence references: "
        f"{facts['coverage']['verified_references']}/"
        f"{facts['coverage']['requested_references']}",
        *deterministic_lines,
        f"Result identity: {facts['result_id']}",
        f"Registry identity: {facts['registry_id']}",
        f"Engine identity: {facts['engine_id']}",
        f"Interpretation write-back: {facts['interpretation_writeback']}",
    ])


def _bounded_optional_prose(
    response_text: object, *, facts: dict[str, Any], fixture_id: str
) -> str | None:
    if response_text is None:
        return None
    if not isinstance(response_text, str):
        raise DeliveryRefused("optional Hermes prose must be text")
    prose = response_text.strip()
    if not prose:
        return None
    labelled_prose = "Hermes interpretation\n" + prose
    if len(labelled_prose) > MAX_OPTIONAL_PROSE_CHARS:
        raise DeliveryRefused("optional Hermes prose is too long")
    status = facts.get("result_status")
    explicit_status = re.search(
        r"\b(?:recovery|readiness)\s+status\s*[:=\-]\s*"
        r"(ok|insufficient[_ -]data|ready|good|warn|bad)\b",
        prose,
        flags=re.IGNORECASE,
    )
    if explicit_status is not None:
        normalized = explicit_status.group(1).lower().replace(" ", "_").replace("-", "_")
        if normalized != status:
            raise DeliveryRefused("optional prose has an explicit conflicting status")
    explicit_score = re.search(
        r"\b(?:overall\s+)?(?:recovery|readiness)(?:\s+score)?\s*[:=\-]?\s*"
        r"([0-9]{1,3})\s*(?:/\s*100|%)",
        prose,
        flags=re.IGNORECASE,
    )
    if explicit_score is not None:
        raise DeliveryRefused("optional prose contains a reserved overall score")
    explicit_fixture = re.search(
        r"\bfixture(?:[_ ]id)?\s*[:=]\s*([A-Za-z0-9][A-Za-z0-9-]{0,79})",
        prose,
        flags=re.IGNORECASE,
    )
    if explicit_fixture is not None and explicit_fixture.group(1) != fixture_id:
        raise DeliveryRefused("optional prose names a conflicting fixture")
    explicit_range = re.search(
        r"\bapproved range\s*[:=]\s*(\d{4}-\d{2}-\d{2})\s+to\s+"
        r"(\d{4}-\d{2}-\d{2})\b",
        prose,
        flags=re.IGNORECASE,
    )
    if explicit_range is not None and explicit_range.groups() != (
        facts["approved_range"]["from"], facts["approved_range"]["to"],
    ):
        raise DeliveryRefused("optional prose names a conflicting approved range")
    explicit_policy = re.search(
        r"\bpolicy(?:[_ ]id)?\s*[:=]\s*([A-Za-z0-9][A-Za-z0-9-]{0,99})"
        r"(?:\s+(?:version|v)\s*[:=]?\s*([0-9]+(?:\.[0-9]+){1,3}))?",
        prose,
        flags=re.IGNORECASE,
    )
    if explicit_policy is not None and (
        explicit_policy.group(1) != facts["calculation_policy"]["policy_id"]
        or (
            explicit_policy.group(2) is not None
            and explicit_policy.group(2) != facts["calculation_policy"]["version"]
        )
    ):
        raise DeliveryRefused("optional prose names a conflicting policy")
    if re.search(
        r"\b(?:this|the result|the score)\s+(?:is|shows|proves)\s+"
        r"(?:a\s+)?diagnos(?:is|tic)\b|\bproves?\s+causation\b|"
        r"\b(?:sleep|hrv|resting heart rate|training|soreness)\s+caused\b",
        prose,
        flags=re.IGNORECASE,
    ):
        raise DeliveryRefused("optional prose contains an explicit diagnostic or causal claim")
    if re.search(r"\b(?:interpretation\s+)?write[- ]?back\s+(?:is\s+)?enabled\b", prose, re.I):
        raise DeliveryRefused("optional prose claims write-back is enabled")
    allowed_hashes = {
        facts["result_sha256"],
        facts["input_fingerprint"],
        facts["calculation_policy"]["policy_sha256"],
        facts["public_evidence_identity"],
    }
    if any(match not in allowed_hashes for match in SHA256_SEARCH_RE.findall(prose)):
        raise DeliveryRefused("optional prose contains an untrusted identity")
    return labelled_prose


def _render_disclosure(
    disclosure: dict[str, Any], authorization: dict[str, Any], facts: dict[str, Any]
) -> str:
    policy = facts["calculation_policy"]
    integrity = facts["snapshot_integrity"]
    missingness = facts["component_missingness"]
    excluded = ", ".join(disclosure.get("excluded_field_groups", []))
    permitted_paths = ", ".join(authorization["permitted_field_paths"])
    qualitative = authorization["qualitative_source_authorization"]
    components = disclosure.get("deterministic_components")
    if not isinstance(components, list) or not components:
        raise DeliveryRefused("trusted disclosure has no deterministic components")
    component_text = ", ".join(
        f"{row['key']} {row['score']}/100"
        for row in components
        if isinstance(row, dict)
        and isinstance(row.get("key"), str)
        and isinstance(row.get("score"), (int, float))
        and not isinstance(row.get("score"), bool)
    )
    if not component_text:
        raise DeliveryRefused("trusted disclosure component summary is invalid")
    block = "\n".join([
        "Data & evidence disclosure",
        f"Fictional scope: {facts['fixture_id']} · "
        f"{facts['approved_range']['from']} to {facts['approved_range']['to']} "
        f"· anchor {facts['approved_range']['anchor']}",
        f"Deterministic status: {facts['result_status']}",
        f"Eligible deterministic components: {component_text}",
        f"Public result identity: {facts['result_sha256']}",
        f"Input fingerprint: {facts['input_fingerprint']}",
        f"Public evidence identity: {facts['public_evidence_identity']}",
        f"Calculation policy: {policy['policy_id']} v{policy['version']} "
        f"· {policy['policy_sha256']}",
        f"Snapshot integrity: {integrity['status']} · schema v{integrity['schema_version']} "
        f"· {integrity['scope']} · provider sync {integrity['external_provider_sync']} "
        f"· training ancestry {integrity['training_ancestry']}",
        "Component missingness: " + _canonical(missingness),
        f"Source ancestry status: {disclosure.get('source_ancestry_status')}",
        "Historical generation event: "
        f"{disclosure.get('historical_generation_event_status')}",
        f"Sharing authorization: {authorization['authorization_id']} "
        f"v{authorization['version']} · {authorization['destination']}",
        f"Shared field paths: {permitted_paths}",
        f"Qualitative source authorization: {qualitative['authorization_id']} "
        f"v{qualitative['version']} · raw note permitted "
        f"{str(qualitative['raw_note_permitted']).lower()} · note-derived secret "
        f"permitted {str(qualitative['note_derived_secret_permitted']).lower()} · "
        "paths " + ", ".join(qualitative["permitted_summary_paths"]),
        f"Interpretation write-back: {facts['interpretation_writeback']}",
        f"Excluded: {excluded}",
    ])
    if not block.strip():
        raise DeliveryRefused("trusted disclosure is empty")
    return block


def render_delivery(
    *,
    completion: object,
    response_text: object,
    platform: object,
) -> dict[str, Any]:
    """Validate the complete pre-format governed text before any send."""

    if platform != "telegram":
        raise DeliveryRefused("Recovery delivery is authorized only for Telegram")
    tool_name, result = _validate_completion(completion)
    evidence, _ = _validate_model_and_evidence(tool_name, result)
    disclosure, authorization, facts = _validate_delivery(tool_name, result, evidence)
    optional_prose = _bounded_optional_prose(
        response_text, facts=facts, fixture_id=result["fixture_id"]
    )
    trusted_disclosure = _render_disclosure(disclosure, authorization, facts)
    complete_payload = trusted_disclosure + (
        "\n\n" + optional_prose if optional_prose is not None else ""
    )
    if _utf16_units(complete_payload) > MAX_TELEGRAM_UTF16_UNITS:
        raise DeliveryRefused("complete Telegram payload exceeds the validated limit")
    return {
        "contract": RENDERED_CONTRACT,
        "trusted_disclosure": trusted_disclosure,
        "optional_prose": optional_prose,
        "bounded_patterns_checked": True,
    }


def render_generic_delivery(
    *,
    completion: object,
    response_text: object,
    platform: object,
) -> dict[str, Any]:
    """Render the separately versioned generic evidence contract."""

    if platform != "telegram":
        raise DeliveryRefused("Generic evidence delivery is authorized only for Telegram")
    generic_result, generic_facts = _validate_generic_completion(completion)
    trusted_disclosure = _render_generic_disclosure(
        generic_result, generic_facts,
    )
    try:
        optional_prose = _bounded_generic_prose(
            response_text, result=generic_result,
        )
    except DeliveryRefused:
        optional_prose = (
            "Hermes interpretation omitted\n"
            "The model-generated interpretation was withheld because it did "
            "not satisfy the evidence-bound response contract. The trusted "
            "deterministic result above remains valid."
        )
    complete_payload = trusted_disclosure + (
        "\n\n" + optional_prose if optional_prose is not None else ""
    )
    if _utf16_units(complete_payload) > MAX_TELEGRAM_UTF16_UNITS:
        raise DeliveryRefused("complete Telegram payload exceeds the validated limit")
    return {
        "contract": RENDERED_CONTRACT,
        "trusted_disclosure": trusted_disclosure,
        "optional_prose": optional_prose,
        "bounded_patterns_checked": True,
    }


def register(ctx: Any) -> None:
    """Register the renderer with the generic Hermes gateway seam."""

    ctx.register_gateway_delivery_renderer(
        RENDERER_ID,
        RENDERER_VERSION,
        render_delivery,
        platforms=("telegram",),
        required_tool_names=(
            OBSERVED_SNAPSHOT_TOOL,
            OBSERVED_DETAIL_TOOL,
        ),
    )
    ctx.register_gateway_delivery_renderer(
        GENERIC_RENDERER_ID,
        GENERIC_RENDERER_VERSION,
        render_generic_delivery,
        platforms=("telegram",),
        required_tool_names=OBSERVED_GENERIC_TOOL_NAMES,
    )
    LOGGER.info(
        "OpenHealthAtlas generic delivery renderer registered: %s@%s (telegram)",
        GENERIC_RENDERER_ID,
        GENERIC_RENDERER_VERSION,
    )
