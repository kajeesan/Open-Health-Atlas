"""Recovery: T48 readiness engine passthrough and display evidence.

Bridge passthrough, same shape as nutrition.py's `_engine` (T45/T46 pattern):
the JSON comes back untouched, including an `insufficient_data` refusal —
that is a normal, valid reply here, not a bridge error, so the client
renders the honest state itself rather than the panel guessing at one.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re

from flask import Blueprint, current_app, jsonify, request

from app import bridge

bp = Blueprint("recovery", __name__, url_prefix="/api/recovery")

_FIXTURE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_POLICY_ID = "openhealthatlas-readiness-policy"
_POLICY_VERSION = "1.0.0"
_FIXTURE_LANE_SCHEMA = {
    "accepted-v5": 5,
    "development-v6": 6,
    "development-v7": 7,
}
_FINGERPRINT_SCOPE = [
    "calculation_relevant_projection",
    "calculation_policy",
    "range",
]
_POLICY_TOP_LEVEL_FIELDS = (
    "policy_id", "version", "meaning", "baseline_days",
    "minimum_same_source_baseline_observations", "component_keys",
    "composite", "minimum_components",
)
_POLICY_SECTION_FIELDS = {
    "sleep": ("target_hours", "method", "quality_scale_max"),
    "hrv": ("method", "deviation_coefficient", "higher_is_better"),
    "resting_hr": ("method", "deviation_coefficient", "lower_is_better"),
    "bands": ("bad_below", "warn_below", "good_at_or_above"),
    "training": (
        "effective_sets_window_days", "performance_window_days", "e1rm_method",
        "performance_comparison", "below_median_rule", "muscle_mapping",
    ),
    "soreness": ("derivation", "synonym_map_sha256", "meaning"),
}
_EVIDENCE_COMPONENT_FIELDS = (
    "key", "status", "reason_code", "ancestry_state", "transformation",
)
_EVIDENCE_CURRENT_FIELDS = (
    "table", "locator", "source_label", "observed_at",
)
_EVIDENCE_BASELINE_FIELDS = (
    "source_label", "observation_count", "range_from", "range_to", "locators",
)
_EVIDENCE_WARNING_FIELDS = (
    "code", "component", "source_label", "required", "observed",
    "other_source_observations_excluded",
)


def _sha256(value):
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _project_fields(value, fields):
    if not isinstance(value, dict):
        raise ValueError("invalid recovery evidence object")
    return {key: deepcopy(value[key]) for key in fields if key in value}


def _public_readiness_evidence(result, *, expected_schema_version=None):
    """Verify and project the privacy-safe v2 evidence for the browser."""

    source = result.get("evidence") if isinstance(result, dict) else None
    if not isinstance(source, dict) or source.get("contract") != "readiness-evidence-v2":
        raise ValueError("missing governed recovery evidence")
    policy_source = source.get("policy")
    integrity = source.get("snapshot_integrity")
    hashes = (
        source.get("input_fingerprint"),
        source.get("policy_sha256"),
        source.get("public_evidence_identity"),
    )
    if (
        not isinstance(policy_source, dict)
        or not isinstance(integrity, dict)
        or any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in hashes
        )
    ):
        raise ValueError("invalid governed recovery evidence")

    policy = _project_fields(policy_source, _POLICY_TOP_LEVEL_FIELDS)
    for section, fields in _POLICY_SECTION_FIELDS.items():
        if section in policy_source:
            policy[section] = _project_fields(policy_source[section], fields)
    if (
        policy.get("policy_id") != _POLICY_ID
        or policy.get("version") != _POLICY_VERSION
        or policy.get("meaning") != "non_diagnostic_readiness_heuristic"
        or source.get("fingerprint_scope") != _FINGERPRINT_SCOPE
        or source["policy_sha256"] != _sha256(policy)
    ):
        raise ValueError("governed recovery policy identity drifted")

    integrity_fields = (
        "contract", "status", "scope", "schema_version",
        "external_provider_sync", "training_ancestry",
    )
    projected_integrity = _project_fields(integrity, integrity_fields)
    if (
        set(projected_integrity) != set(integrity_fields)
        or projected_integrity["contract"] != "openhealthatlas-readiness-ancestry-v2"
        or projected_integrity["status"] != "verified"
        or projected_integrity["scope"]
        != "current_snapshot_integrity_and_reproducibility"
        or projected_integrity["schema_version"] not in {5, 6, 7}
        or (
            expected_schema_version is not None
            and projected_integrity["schema_version"] != expected_schema_version
        )
        or projected_integrity["external_provider_sync"] != "not_performed"
        or projected_integrity["training_ancestry"] != "manifested"
    ):
        raise ValueError("governed recovery snapshot integrity drifted")

    for field in ("components", "warnings", "remaining_ancestry_gaps"):
        if not isinstance(source.get(field), list):
            raise ValueError("invalid governed recovery evidence collection")
    components = []
    for row in source["components"]:
        item = _project_fields(row, _EVIDENCE_COMPONENT_FIELDS)
        item["current"] = (
            _project_fields(row["current"], _EVIDENCE_CURRENT_FIELDS)
            if isinstance(row.get("current"), dict) else None
        )
        item["baseline"] = (
            _project_fields(row["baseline"], _EVIDENCE_BASELINE_FIELDS)
            if isinstance(row.get("baseline"), dict) else None
        )
        components.append(item)
    warnings = [
        _project_fields(row, _EVIDENCE_WARNING_FIELDS)
        for row in source["warnings"]
    ]
    gaps = [
        _project_fields(row, ("code", "scope"))
        for row in source["remaining_ancestry_gaps"]
    ]
    soreness = source.get("soreness")
    projected_soreness = (
        {
            key: deepcopy(soreness.get(key))
            for key in ("status", "table", "locator", "source_label", "observed_at")
        }
        if isinstance(soreness, dict) else None
    )
    projected = {
        "contract": "readiness-evidence-v2",
        "input_fingerprint": source["input_fingerprint"],
        "fingerprint_scope": list(_FINGERPRINT_SCOPE),
        "policy": policy,
        "policy_sha256": source["policy_sha256"],
        "public_evidence_identity": source["public_evidence_identity"],
        "snapshot_integrity": projected_integrity,
        "components": components,
        "soreness": projected_soreness,
        "warnings": warnings,
        "remaining_ancestry_gaps": gaps,
    }
    public_preimage = {
        key: deepcopy(value)
        for key, value in projected.items()
        if key != "public_evidence_identity"
    }
    if projected["public_evidence_identity"] != _sha256(public_preimage):
        raise ValueError("governed recovery public evidence identity drifted")
    return projected


def _validate_fixed_acceptance_result(result, evidence):
    """Pin the fixed fictional display to the corrected accepted outcome."""

    if (
        result.get("status") != "insufficient_data"
        or "score" in result
        or "band" in result
        or "drag" in result
    ):
        raise ValueError("fixed Recovery status drifted")
    result_components = result.get("components")
    if (
        not isinstance(result_components, list)
        or len(result_components) != 1
        or result_components[0].get("key") != "sleep"
        or result_components[0].get("score") != 92
    ):
        raise ValueError("fixed Recovery components drifted")

    component_by_key = {
        row.get("key"): row
        for row in evidence["components"]
        if isinstance(row, dict)
    }
    warning_by_key = {
        row.get("component"): row
        for row in evidence["warnings"]
        if isinstance(row, dict)
    }
    if (
        set(component_by_key) != {"sleep", "hrv", "rhr"}
        or component_by_key["sleep"].get("status") != "included"
        or any(
            component_by_key[key].get("status") != "excluded"
            or component_by_key[key].get("reason_code")
            != "insufficient_same_source_baseline"
            or warning_by_key.get(key, {}).get("source_label") != "fitbit"
            or warning_by_key.get(key, {}).get("observed") != 10
            or warning_by_key.get(key, {}).get("required") != 14
            or warning_by_key.get(key, {}).get(
                "other_source_observations_excluded"
            ) != 86
            for key in ("hrv", "rhr")
        )
        or evidence["remaining_ancestry_gaps"] != []
    ):
        raise ValueError("fixed Recovery evidence drifted")


def _public_readiness_result(result, evidence):
    """Return only the browser-facing deterministic and qualitative summary."""

    public = _project_fields(
        result,
        ("status", "anchor_date", "range_from", "reason", "score", "band"),
    )
    for key, fields in (
        ("components", ("key", "score", "value", "basis")),
        ("drag", ("key", "label", "score", "points")),
        (
            "muscle_recovery",
            (
                "group", "days_since", "sets_7d", "exercise", "e1rm_delta_pct",
                "below_median", "sore",
            ),
        ),
    ):
        rows = result.get(key, [])
        if not isinstance(rows, list):
            raise ValueError("invalid recovery result collection")
        public[key] = [_project_fields(row, fields) for row in rows]
    raw_soreness = result.get("soreness")
    evidence_soreness = evidence.get("soreness")
    public["soreness"] = None
    if isinstance(raw_soreness, dict):
        public["soreness"] = {
            "present": True,
            "date": raw_soreness.get("date"),
            "source_label": (
                evidence_soreness.get("source_label")
                if isinstance(evidence_soreness, dict) else None
            ),
            "source_locator": (
                evidence_soreness.get("locator")
                if isinstance(evidence_soreness, dict) else None
            ),
        }
    public["disclaimer"] = (
        "This is a transparent fictional-fixture heuristic, not a diagnosis, "
        "medical advice, proof of readiness, or proof of causation."
    )
    public["evidence"] = evidence
    return public


def _display_evidence(public_evidence):
    policy = public_evidence["policy"]
    return {
        "readiness_evidence_contract": "readiness-evidence-v2",
        "input_fingerprint": public_evidence["input_fingerprint"],
        "policy": {
            "policy_id": policy["policy_id"],
            "version": policy["version"],
            "policy_sha256": public_evidence["policy_sha256"],
        },
        "public_evidence_identity": public_evidence["public_evidence_identity"],
        "snapshot_integrity": deepcopy(public_evidence["snapshot_integrity"]),
    }


def _fixed_scope():
    """Return the trusted fictional scope or fail closed on bad app config."""
    scope = current_app.config.get("RECOVERY_FIXED_SCOPE")
    if scope is None:
        return (), None
    expected = {
        "data_class", "fixture_id", "range_from", "anchor_date",
        "readiness_fixture_lane",
    }
    if not isinstance(scope, dict) or set(scope) != expected:
        raise ValueError("invalid recovery fixed scope")
    if (
        scope["data_class"] != "fictional"
        or not isinstance(scope["fixture_id"], str)
        or _FIXTURE_ID.fullmatch(scope["fixture_id"]) is None
        or scope["readiness_fixture_lane"] not in _FIXTURE_LANE_SCHEMA
    ):
        raise ValueError("invalid recovery fixed scope")
    try:
        start = date.fromisoformat(scope["range_from"])
        anchor = date.fromisoformat(scope["anchor_date"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid recovery fixed scope") from exc
    if (
        start.isoformat() != scope["range_from"]
        or anchor.isoformat() != scope["anchor_date"]
        or start > anchor
    ):
        raise ValueError("invalid recovery fixed scope")
    args = ("--from", scope["range_from"], "--anchor", scope["anchor_date"])
    return args, dict(scope)


@bp.get("/readiness")
def readiness():
    """Return one deterministic readiness result and its display fingerprint."""
    if request.args:
        return jsonify(ok=False, error="unexpected query parameter"), 400
    try:
        args, scope = _fixed_scope()
    except ValueError:
        return jsonify(ok=False, error="recovery display configuration is invalid"), 503
    try:
        result = bridge.run("readiness", *args)
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502
    evidence = {
        "contract": "openhealthatlas-recovery-display-evidence-v1",
        "deterministic_authority": "OpenHealthAtlas",
        "command": "readiness",
        "args": list(args),
    }
    try:
        public_evidence = _public_readiness_evidence(
            result,
            expected_schema_version=(
                _FIXTURE_LANE_SCHEMA[
                    scope["readiness_fixture_lane"]
                ] if scope is not None else None
            ),
        )
        if scope is not None:
            if (
                result.get("range_from") != scope["range_from"]
                or result.get("anchor_date") != scope["anchor_date"]
            ):
                raise ValueError("recovery result scope drifted")
            _validate_fixed_acceptance_result(result, public_evidence)
        result = _public_readiness_result(result, public_evidence)
    except (AttributeError, TypeError, ValueError):
        return jsonify(
            ok=False,
            error="governed recovery result failed validation",
        ), 502
    evidence.update(_display_evidence(public_evidence))
    if scope is not None:
        evidence.update(scope)
    return jsonify(ok=True, result=result, evidence=evidence)
