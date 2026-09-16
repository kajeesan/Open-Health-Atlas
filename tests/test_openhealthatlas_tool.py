"""Bounded external-Hermes OpenHealthAtlas tool adapter tests."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "hermes-openhealthatlas-tool"
TOOL_SOURCE_MARKER = ROOT / "deploy" / "hermes-openhealthatlas-tool-config.json"
FICTIONAL_CONFIG = ROOT / "deploy" / "hermes-openhealthatlas-fictional-config.json"
MCP = ROOT / "deploy" / "hermes-openhealthatlas-mcp"
REAL_DATA_GOVERNANCE = ROOT / "deploy" / "openhealthatlas-real-data-governance.json"
RECOVERY_STAGING_MANIFEST = (
    ROOT / "deploy" / "recovery-item6-staging-manifest.json"
)
BRIDGE_SERVICE = ROOT / "deploy" / "hermes-bridge.service"
INSTALLER = ROOT / "deploy" / "install-openhealthatlas-tool.sh"
SKILL = ROOT / "deploy" / "hermes-openhealthatlas-skill.md"


def _fake_health(tmp_path: Path) -> Path:
    path = tmp_path / "health.py"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "print(json.dumps({'ok': True, 'finding_id': 'sha256:' + 'a' * 64, "
        "'argv': sys.argv[1:], 'health_db': os.environ.get('HEALTH_DB'), "
        "'timezone': os.environ.get('HERMES_TIMEZONE'), "
        "'vault': os.environ.get('HEALTH_VAULT'), "
        "'fixture_lane': os.environ.get('OPENHEALTHATLAS_READINESS_FIXTURE_LANE'), "
        "'ancestry_sidecar': os.environ.get('OPENHEALTHATLAS_READINESS_ANCESTRY_SIDECAR'), "
        "'assessment_state': 'assessed'}))\n",
        encoding="utf-8",
    )
    return path


def _tool_env(extra: dict[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("OPENHEALTHATLAS_SOURCE_TEST", None)
    environment.pop("OPENHEALTHATLAS_TEST_CONFIG", None)
    environment.update(extra)
    return environment


def _run(
    tmp_path: Path,
    *args: str,
    stdin: str | None = None,
    tool: Path = TOOL,
    synthesis_writeback_enabled: bool = False,
    readiness_fixture_lane: str = "development-v6",
    readiness_ancestry_sidecar: str | None = None,
):
    database = tmp_path / "fictional.db"
    database.touch(exist_ok=True)
    audit = tmp_path / "audit.jsonl"
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    config = tmp_path / "tool-config.json"
    config.write_text(json.dumps({
        "audit": str(audit),
        "contract": "openhealthatlas-hermes-tool-v1",
        "data_class": "fictional",
        "data_dir": str(tmp_path),
        "fixture_id": "unit-green-days",
        "health_cli": str(_fake_health(tmp_path)),
        "health_db": str(database),
        "health_vault": str(vault),
        "range_from": "2026-05-17",
        "range_to": "2026-06-30",
        "readiness_ancestry_sidecar": readiness_ancestry_sidecar,
        "readiness_fixture_lane": readiness_fixture_lane,
        "synthesis_writeback_enabled": synthesis_writeback_enabled,
        "timeout_seconds": 5,
        "timezone": "UTC",
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(tool), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=_tool_env({
            "HEALTH_DB": str(tmp_path / "owner.db"),
            "HEALTH_VAULT": str(tmp_path / "owner-vault"),
            "HERMES_TIMEZONE": "Europe/Paris",
            "OPENHEALTHATLAS_SOURCE_TEST": "1",
            "OPENHEALTHATLAS_TEST_CONFIG": str(config),
            "OPENHEALTHATLAS_HERMES_TURN_ID": "oha-fixture-turn",
        }),
        timeout=10,
    )
    return result, json.loads(result.stdout), audit


def _source_tool_without_agents(tmp_path: Path) -> Path:
    deploy = tmp_path / "fresh-source" / "deploy"
    deploy.mkdir(parents=True)
    tool = deploy / TOOL.name
    shutil.copy2(TOOL, tool)
    shutil.copy2(TOOL_SOURCE_MARKER, deploy / TOOL_SOURCE_MARKER.name)
    assert not (deploy.parent / "AGENTS.md").exists()
    return tool


def _invoke(tool: Path, *args: str, env: dict[str, str]):
    result = subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True,
        text=True,
        env=_tool_env(env),
        timeout=10,
    )
    return result, json.loads(result.stdout)


def _load_mcp_namespace(
    tmp_path: Path, monkeypatch, governance: object = "default",
):
    install = tmp_path / "mcp-install"
    install.mkdir(parents=True)
    for source in (MCP, TOOL, ROOT / "deploy" / "hermes-ssh-transport"):
        shutil.copy2(source, install / source.name)
    shutil.copy2(FICTIONAL_CONFIG, install / "tool-config.json")
    if governance == "default":
        shutil.copy2(REAL_DATA_GOVERNANCE, install / REAL_DATA_GOVERNANCE.name)
    elif isinstance(governance, dict):
        (install / REAL_DATA_GOVERNANCE.name).write_text(
            json.dumps(governance), encoding="utf-8"
        )

    class FakeFastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        @staticmethod
        def tool(*_args, **_kwargs):
            return lambda function: function

        def run(self, *_args, **_kwargs):
            raise AssertionError("unit contract tests must not start MCP stdio")

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
    monkeypatch.setattr(os, "chdir", lambda _path: None)
    return runpy.run_path(str(install / MCP.name))


def _recovery_result_with_evidence():
    sha = lambda character: "sha256:" + character * 64
    result = {
        "status": "insufficient_data",
        "anchor_date": "2026-06-30",
        "range_from": "2026-03-02",
        "reason": "fewer than 2 eligible recovery components",
        "components": [
            {"key": "sleep", "score": 92, "value": 7.09, "basis": "fixture"},
        ],
        "muscle_recovery": [
            {
                "group": "Legs", "days_since": 1, "sets_7d": 18.6,
                "exercise": "Fictional squat", "e1rm_delta_pct": -0.8,
                "below_median": True, "sore": True,
            }
        ],
        "soreness": {
            "date": "2026-06-30",
            "note": "SECRET RAW FICTIONAL SORENESS TEXT",
        },
        "disclaimer": "Transparent heuristic — not medical advice.",
        "evidence": {
            "contract": "readiness-evidence-v2",
            "input_fingerprint": sha("a"),
            "fingerprint_scope": [
                "calculation_relevant_projection",
                "calculation_policy",
                "range",
            ],
            "policy": {
                "policy_id": "openhealthatlas-readiness-policy",
                "version": "1.0.0",
                "meaning": "non_diagnostic_readiness_heuristic",
            },
            "policy_sha256": None,
            "public_evidence_identity": None,
            "snapshot_integrity": {
                "contract": "openhealthatlas-readiness-ancestry-v2",
                "status": "verified",
                "scope": "current_snapshot_integrity_and_reproducibility",
                "schema_version": 5,
                "external_provider_sync": "not_performed",
                "training_ancestry": "manifested",
            },
            "components": [
                {
                    "key": "sleep",
                    "status": "included",
                    "reason_code": None,
                    "ancestry_state": "source_row_identified",
                    "current": {
                        "table": "sleep_log",
                        "locator": "sleep_log:2026-06-30",
                        "source_label": "fictional-demo",
                        "observed_at": "2026-06-30",
                    },
                    "baseline": None,
                    "transformation": "sleep-score-v1",
                },
                {
                    "key": "hrv",
                    "status": "excluded",
                    "reason_code": "insufficient_same_source_baseline",
                    "ancestry_state": "source_rows_identified",
                    "current": None,
                    "baseline": {
                        "source_label": "fitbit",
                        "observation_count": 10,
                        "range_from": "2026-06-16",
                        "range_to": "2026-06-29",
                        "locators": [],
                    },
                    "transformation": "same-source-baseline-deviation-hrv-v1",
                },
                {
                    "key": "rhr",
                    "status": "excluded",
                    "reason_code": "insufficient_same_source_baseline",
                    "ancestry_state": "source_rows_identified",
                    "current": None,
                    "baseline": {
                        "source_label": "fitbit",
                        "observation_count": 10,
                        "range_from": "2026-06-16",
                        "range_to": "2026-06-29",
                        "locators": [],
                    },
                    "transformation": "same-source-baseline-deviation-rhr-v1",
                },
            ],
            "soreness": {
                "status": "present",
                "table": "subjective_daily",
                "locator": "subjective_daily:2026-06-30",
                "source_label": "fictional-demo",
                "observed_at": "2026-06-30",
                "content_fingerprint": sha("b"),
            },
            "warnings": [
                {
                    "code": "insufficient_same_source_baseline",
                    "component": "hrv",
                    "source_label": "fitbit",
                    "required": 14,
                    "observed": 10,
                    "other_source_observations_excluded": 86,
                },
                {
                    "code": "insufficient_same_source_baseline",
                    "component": "rhr",
                    "source_label": "fitbit",
                    "required": 14,
                    "observed": 10,
                    "other_source_observations_excluded": 86,
                },
            ],
            "remaining_ancestry_gaps": [],
        },
    }
    return _refresh_public_evidence_identities(result)


def _refresh_public_evidence_identities(result):
    evidence = result["evidence"]
    policy_top = (
        "policy_id", "version", "meaning", "baseline_days",
        "minimum_same_source_baseline_observations", "component_keys",
        "composite", "minimum_components",
    )
    policy_sections = {
        "sleep": ("target_hours", "method", "quality_scale_max"),
        "hrv": ("method", "deviation_coefficient", "higher_is_better"),
        "resting_hr": ("method", "deviation_coefficient", "lower_is_better"),
        "bands": ("bad_below", "warn_below", "good_at_or_above"),
        "training": (
            "effective_sets_window_days", "performance_window_days",
            "e1rm_method", "performance_comparison", "below_median_rule",
            "muscle_mapping",
        ),
        "soreness": ("derivation", "synonym_map_sha256", "meaning"),
    }
    projected_policy = {
        key: evidence["policy"][key]
        for key in policy_top if key in evidence["policy"]
    }
    for name, fields in policy_sections.items():
        if isinstance(evidence["policy"].get(name), dict):
            projected_policy[name] = {
                key: evidence["policy"][name][key]
                for key in fields if key in evidence["policy"][name]
            }
    evidence["policy_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(
            projected_policy, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    public_preimage = {
        "contract": "readiness-evidence-v2",
        "input_fingerprint": evidence["input_fingerprint"],
        "fingerprint_scope": list(evidence["fingerprint_scope"]),
        "policy": projected_policy,
        "policy_sha256": evidence["policy_sha256"],
        "snapshot_integrity": {
            key: evidence["snapshot_integrity"][key]
            for key in (
                "contract", "status", "scope", "schema_version",
                "external_provider_sync", "training_ancestry",
            )
        },
        "components": [],
        "soreness": None,
        "warnings": [],
        "remaining_ancestry_gaps": [],
    }
    for row in evidence.get("components", []):
        item = {
            key: row[key]
            for key in (
                "key", "status", "reason_code", "ancestry_state",
                "transformation",
            ) if key in row
        }
        item["current"] = (
            {
                key: row["current"][key]
                for key in ("table", "locator", "source_label", "observed_at")
                if key in row["current"]
            }
            if isinstance(row.get("current"), dict) else None
        )
        item["baseline"] = (
            {
                key: row["baseline"][key]
                for key in (
                    "source_label", "observation_count", "range_from",
                    "range_to", "locators",
                ) if key in row["baseline"]
            }
            if isinstance(row.get("baseline"), dict) else None
        )
        public_preimage["components"].append(item)
    if isinstance(evidence.get("soreness"), dict):
        public_preimage["soreness"] = {
            key: evidence["soreness"].get(key)
            for key in (
                "status", "table", "locator", "source_label", "observed_at",
            )
        }
    for row in evidence.get("warnings", []):
        public_preimage["warnings"].append({
            key: row[key]
            for key in (
                "code", "component", "source_label", "required", "observed",
                "other_source_observations_excluded",
            ) if key in row
        })
    for row in evidence.get("remaining_ancestry_gaps", []):
        public_preimage["remaining_ancestry_gaps"].append({
            key: row[key] for key in ("code", "scope") if key in row
        })
    evidence["public_evidence_identity"] = "sha256:" + hashlib.sha256(
        json.dumps(
            public_preimage, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return result
def test_adapter_returns_exact_result_and_records_intent_and_result(tmp_path):
    result, payload, audit = _run(
        tmp_path,
        "feature-frame",
        "--from", "2026-05-17",
        "--to", "2026-06-30",
        "--family", "subjective",
        "--include-provenance",
    )
    assert result.returncode == 0
    assert payload["ok"] is True
    assert payload["data_class"] == "fictional"
    assert payload["fixture_id"] == "unit-green-days"
    assert payload["result"]["finding_id"] == "sha256:" + "a" * 64
    assert payload["result"]["health_db"].endswith("fictional.db")
    assert payload["result"]["timezone"] == "UTC"
    assert payload["result"]["vault"].endswith("/vault")
    assert payload["result"]["argv"] == [
        "feature-frame", "--from", "2026-05-17", "--to", "2026-06-30",
        "--family", "subjective", "--include-provenance",
    ]
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [row["phase"] for row in rows] == ["intent", "result"]
    assert rows[0]["command"] == payload["selected_command"]
    assert rows[1]["result_sha256"] == payload["result_sha256"]
    assert rows[1]["evidence_ids"] == ["sha256:" + "a" * 64]
    assert rows[1]["database_before_sha256"] == rows[1]["database_after_sha256"]


def test_readiness_passes_only_the_explicit_fixture_lane_and_private_sidecar(
    tmp_path,
):
    sidecar = tmp_path / "readiness-ancestry-v2.json"
    sidecar.write_text("{}", encoding="utf-8")
    completed, payload, _ = _run(
        tmp_path,
        "readiness",
        "--from", "2026-05-17",
        "--anchor", "2026-06-30",
        readiness_fixture_lane="accepted-v5",
        readiness_ancestry_sidecar=str(sidecar),
    )
    assert completed.returncode == 0
    assert payload["result"]["fixture_lane"] == "accepted-v5"
    assert payload["result"]["ancestry_sidecar"] == str(sidecar)


@pytest.mark.parametrize("lane", ["development-v6", "development-v7"])
def test_development_lane_omits_sidecar_and_rejects_configured_path(tmp_path, lane):
    completed, payload, _ = _run(
        tmp_path,
        "readiness",
        "--from", "2026-05-17",
        "--anchor", "2026-06-30",
        readiness_fixture_lane=lane,
    )
    assert completed.returncode == 0
    assert payload["result"]["fixture_lane"] == lane
    assert payload["result"]["ancestry_sidecar"] is None

    sidecar = tmp_path / "not-allowed.json"
    sidecar.write_text("{}", encoding="utf-8")
    refused, body, _ = _run(
        tmp_path,
        "readiness",
        "--from", "2026-05-17",
        "--anchor", "2026-06-30",
        readiness_fixture_lane=lane,
        readiness_ancestry_sidecar=str(sidecar),
    )
    assert refused.returncode == 2
    assert body["ok"] is False
    assert "must not configure" in body["error"]


def test_explicit_source_config_works_without_agents_file(tmp_path):
    tool = _source_tool_without_agents(tmp_path)
    result, payload, _audit = _run(
        tmp_path,
        "feature-registry",
        tool=tool,
    )
    assert result.returncode == 0
    assert payload["ok"] is True
    assert payload["fixture_id"] == "unit-green-days"


def test_installed_tool_refuses_test_override_and_requires_trusted_default(tmp_path):
    installed = tmp_path / "installed" / TOOL.name
    installed.parent.mkdir()
    shutil.copy2(TOOL, installed)
    override = tmp_path / "override.json"
    override.write_text("{}", encoding="utf-8")
    test_env = {
        "OPENHEALTHATLAS_SOURCE_TEST": "1",
        "OPENHEALTHATLAS_TEST_CONFIG": str(override),
    }
    result, payload = _invoke(installed, "feature-registry", env=test_env)
    assert result.returncode == 2
    assert payload["error"] == (
        "source test config is unavailable for the installed tool"
    )

    default = installed.with_name("tool-config.json")
    default.write_text("{}", encoding="utf-8")
    default.chmod(0o666)
    result, payload = _invoke(installed, "feature-registry", env={})
    assert result.returncode == 2
    assert payload["error"] == (
        "installed tool config must be root-owned and not writable"
    )


def test_source_test_config_missing_or_invalid_fails_closed(tmp_path):
    tool = _source_tool_without_agents(tmp_path)
    common = {"OPENHEALTHATLAS_SOURCE_TEST": "1"}

    result, payload = _invoke(
        tool,
        "feature-registry",
        env={**common, "OPENHEALTHATLAS_TEST_CONFIG": str(tmp_path / "missing.json")},
    )
    assert result.returncode == 2
    assert payload["error"] == "tool config must be a regular file"

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    result, payload = _invoke(
        tool,
        "feature-registry",
        env={**common, "OPENHEALTHATLAS_TEST_CONFIG": str(invalid)},
    )
    assert result.returncode == 2
    assert payload["error"] == "tool config has the wrong fields"

    result, payload = _invoke(
        tool,
        "feature-registry",
        env={"OPENHEALTHATLAS_TEST_CONFIG": str(invalid)},
    )
    assert result.returncode == 2
    assert "requires OPENHEALTHATLAS_SOURCE_TEST=1" in payload["error"]


def test_adapter_refuses_scope_expansion_before_running_health(tmp_path):
    result, payload, audit = _run(
        tmp_path,
        "analysis-refresh",
        "--kind", "weekly",
        "--outcome", "subjective.day_rating",
        "--mode", "green-vs-non-green",
        "--all",
    )
    assert result.returncode == 2
    assert payload["ok"] is False
    assert "--kind manual" in payload["error"]
    assert not audit.exists()


def test_synthesis_record_is_rejected_by_default_owner_gate(tmp_path):
    result, payload, audit = _run(
        tmp_path, "synthesis-record", "--stdin", stdin="not-json",
    )
    assert result.returncode == 2
    assert payload["ok"] is False
    assert payload["error"] == (
        "synthesis-record is disabled by the owner-controlled writeback gate"
    )
    assert not audit.exists()


def test_owner_gate_must_be_explicit_and_still_keeps_record_input_bounded(tmp_path):
    invalid, payload, audit = _run(
        tmp_path,
        "synthesis-record",
        "--stdin",
        stdin="not-json",
        synthesis_writeback_enabled=True,
    )
    assert invalid.returncode == 2
    assert "valid UTF-8 JSON" in payload["error"]
    assert not audit.exists()

    accepted, payload, audit = _run(
        tmp_path,
        "synthesis-record",
        "--stdin",
        stdin="{}",
        synthesis_writeback_enabled=True,
    )
    assert accepted.returncode == 0
    assert payload["result"]["argv"] == ["synthesis-record", "--stdin"]
    assert [
        json.loads(line)["phase"] for line in audit.read_text().splitlines()
    ] == ["intent", "result"]


def test_synthesis_prepare_receipt_binds_deterministic_assessment_state(tmp_path):
    result, payload, audit = _run(
        tmp_path,
        "synthesis-prepare",
        "--batch-id",
        "sha256:" + "b" * 64,
    )
    assert result.returncode == 0
    assert payload["result"]["assessment_state"] == "assessed"
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert rows[-1]["assessment_state"] == "assessed"


def test_feature_registry_is_available_only_as_an_argument_free_read(tmp_path):
    result, payload, audit = _run(tmp_path, "feature-registry")
    assert result.returncode == 0
    assert payload["result"]["argv"] == ["feature-registry"]
    assert [
        json.loads(line)["phase"] for line in audit.read_text().splitlines()
    ] == ["intent", "result"]

    refused, payload, _audit = _run(
        tmp_path, "feature-registry", "--family", "wearable",
    )
    assert refused.returncode == 2
    assert "unsupported or repeated flag" in payload["error"]


def test_adapter_allows_only_fixed_date_recovery_reads(tmp_path):
    result, payload, audit = _run(
        tmp_path, "readiness", "--from", "2026-05-17",
        "--anchor", "2026-06-30",
    )
    assert result.returncode == 0
    assert payload["result"]["argv"] == [
        "readiness", "--from", "2026-05-17", "--anchor", "2026-06-30",
    ]
    assert [
        json.loads(line)["phase"] for line in audit.read_text().splitlines()
    ] == ["intent", "result"]
    audit_before = audit.read_text()

    outside, payload, outside_audit = _run(
        tmp_path, "readiness", "--from", "2026-05-17",
        "--anchor", "2026-06-29",
    )
    assert outside.returncode == 2
    assert payload["error"] == "readiness range must equal the trusted fixture range"
    assert outside_audit.read_text() == audit_before


def test_adapter_refuses_unbounded_or_outside_fixture_range(tmp_path):
    unbounded, payload, audit = _run(
        tmp_path,
        "feature-frame", "--all", "--family", "subjective",
        "--include-provenance",
    )
    assert unbounded.returncode == 2
    assert "trusted bounded date range" in payload["error"]
    assert not audit.exists()

    outside, payload, audit = _run(
        tmp_path,
        "data-readiness", "--from", "2026-05-16", "--to", "2026-06-30",
        "--outcome", "subjective.day_rating",
    )
    assert outside.returncode == 2
    assert "outside the configured fictional fixture" in payload["error"]
    assert not audit.exists()


def test_bridge_sandbox_allows_only_owner_state_and_fictional_fixture():
    line = next(
        item for item in BRIDGE_SERVICE.read_text().splitlines()
        if item.startswith("ReadWritePaths=")
    )
    assert line == (
        "ReadWritePaths=/var/lib/hermes "
        "/var/lib/openhealthatlas-fictional"
    )


def test_installer_builds_and_verifies_the_named_fictional_fixture():
    source = INSTALLER.read_text(encoding="utf-8")
    assert 'ROOT=/usr/local/lib/hermes-openhealthatlas-fictional' in source
    assert 'FIXTURE_BUILDER=/opt/openhealthatlas-fictional/scripts/demo_flow.py' in source
    assert 'FIXTURE_ID=green-days-actionable-v1' in source
    assert '--fixture-id "$FIXTURE_ID" --seed-only' in source
    assert '--fixture-id "$FIXTURE_ID"' in source
    assert '--verify-fixture "$VERIFY_MANIFEST"' in source
    assert '--audit "$TOOL_AUDIT"' in source
    assert "Refusing a pre-existing fixture without a root-owned seed receipt" in source
    assert source.index("touch \"$TOOL_AUDIT\"") < source.index(
        "systemctl restart hermes-bridge.service"
    )


def test_installed_fixture_identity_matches_skill_and_keeps_control_distinct():
    config = json.loads(
        TOOL_SOURCE_MARKER.read_text(encoding="utf-8")
    )
    skill = SKILL.read_text(encoding="utf-8")
    assert config["fixture_id"] == "green-days-actionable-v1"
    assert "FIXTURE_ID=green-days-actionable-v1" in INSTALLER.read_text(
        encoding="utf-8"
    )
    assert "`green-days-actionable-v1`" in skill
    assert "`comprehensive-persona-v1`" in skill
    assert "`green-days-v1` acceptance control" in skill
    assert config["synthesis_writeback_enabled"] is False
    assert "Do not call `synthesis-record`" in skill


def test_recovery_deployment_uses_configured_comprehensive_fixture():
    config = json.loads(FICTIONAL_CONFIG.read_text(encoding="utf-8"))
    source = MCP.read_text(encoding="utf-8")
    assert config["data_class"] == "fictional"
    assert config["fixture_id"] == "comprehensive-persona-v1"
    assert config["range_from"] == "2026-03-02"
    assert config["range_to"] == "2026-06-30"
    assert 'CONFIG = SCRIPT.with_name("tool-config.json")' in source
    assert "FIXTURE_ID, RANGE_FROM, RANGE_TO = _fixture_configuration()" in source
    assert 'FIXTURE_ID = "green-days-actionable-v1"' not in source


def test_recovery_mcp_projects_exact_disclosure_without_raw_soreness(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    result = _recovery_result_with_evidence()
    projected = namespace["_model_readiness_result"](result)
    disclosure = namespace["_recovery_disclosure"](
        response_kind="snapshot",
        focus=None,
        result=result,
        result_sha256="sha256:" + "c" * 64,
        missing=[],
    )

    serialized = json.dumps({"result": projected, "disclosure": disclosure})
    assert "SECRET RAW FICTIONAL SORENESS TEXT" not in serialized
    assert "your own baselines" not in serialized
    assert projected["disclaimer"] == (
        "This is a transparent fictional-fixture heuristic, not a diagnosis, "
        "medical advice, proof of readiness, or proof of causation."
    )
    assert projected["soreness"] == {
        "present": True,
        "date": "2026-06-30",
        "source_label": "fictional-demo",
        "source_locator": "subjective_daily:2026-06-30",
    }
    assert projected["evidence"]["fingerprint_scope"] == [
        "calculation_relevant_projection",
        "calculation_policy",
        "range",
    ]
    assert disclosure["contract"] == "openhealthatlas-recovery-disclosure-v1"
    assert disclosure["data_class"] == "fictional"
    assert disclosure["fixture_id"] == "comprehensive-persona-v1"
    assert disclosure["evidence_identity"] == {
        "result_sha256": "sha256:" + "c" * 64,
        "result_identity_scope": "privacy_safe_snapshot_projection",
        "input_fingerprint": "sha256:" + "a" * 64,
    }
    assert disclosure["calculation_policy"] == {
        "policy_id": "openhealthatlas-readiness-policy",
        "version": "1.0.0",
        "policy_sha256": result["evidence"]["policy_sha256"],
    }
    assert disclosure["public_evidence_identity"] == (
        result["evidence"]["public_evidence_identity"]
    )
    assert disclosure["snapshot_integrity"] == {
        "contract": "openhealthatlas-readiness-ancestry-v2",
        "status": "verified",
        "scope": "current_snapshot_integrity_and_reproducibility",
        "schema_version": 5,
        "external_provider_sync": "not_performed",
        "training_ancestry": "manifested",
    }
    assert "sharing_authorization" in disclosure["shared_field_paths"]
    assert "delivery_contract" in disclosure["shared_field_paths"]
    assert (
        "result.soreness.{present,date,source_label,source_locator}"
        in disclosure["shared_field_paths"]
    )
    assert "soreness_content_fingerprint" in disclosure["excluded_field_groups"]
    assert "canonical_row_digests" in disclosure["excluded_field_groups"]
    assert "full_database_identity" in disclosure["excluded_field_groups"]


def test_recovery_snapshot_and_soreness_detail_never_face_model_with_raw_note(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    result = _recovery_result_with_evidence()
    envelope = {
        "result": result,
        "result_sha256": "sha256:" + "c" * 64,
    }
    journey = namespace["RecoveryJourney"]()
    journey._execute_readiness = lambda: envelope
    journey._save_receipt = lambda _focus=None: None

    snapshot = journey.snapshot()
    detail = journey.detail("soreness")
    serialized = json.dumps({"snapshot": snapshot, "detail": detail})
    assert "SECRET RAW FICTIONAL SORENESS TEXT" not in serialized
    assert snapshot["result"]["soreness"]["present"] is True
    assert detail["detail"]["soreness"] == snapshot["result"]["soreness"]
    assert detail["detail"]["flagged_groups"] == result["muscle_recovery"]
    missingness = snapshot["required_disclosure"]["component_missingness"]
    assert missingness["status"] == "none_missing"
    assert missingness["missing"] == []
    assert {row["key"] for row in missingness["excluded"]} == {"hrv", "rhr"}
    assert all(
        row["observed"] == 10
        and row["required"] == 14
        and row["other_source_observations_excluded"] == 86
        for row in missingness["excluded"]
    )
    expected_reply_requirement = {
        "required_final_heading": "Data & evidence disclosure",
        "source_field": "required_disclosure",
        "include_every_disclosure_field": True,
        "omission_is_acceptance_failure": True,
    }
    assert snapshot["reply_requirement"] == expected_reply_requirement
    assert detail["reply_requirement"] == expected_reply_requirement
    snapshot_keys = list(snapshot)
    detail_keys = list(detail)
    assert snapshot_keys.index("required_disclosure") < snapshot_keys.index("result")
    assert detail_keys.index("required_disclosure") < detail_keys.index("detail")
    assert snapshot_keys.index("required_disclosure") < snapshot_keys.index(
        "delivery_contract"
    ) < snapshot_keys.index("result")
    assert detail_keys.index("required_disclosure") < detail_keys.index(
        "delivery_contract"
    ) < detail_keys.index("detail")
    assert detail["required_disclosure"]["source_ancestry_status"] == (
        "verified_current_snapshot"
    )
    assert detail["required_disclosure"]["historical_generation_event_status"] == (
        "not_attested"
    )
    assert detail["required_disclosure"]["shared_field_paths"] == [
        "data_class",
        "fixture_id",
        "turn_id",
        "result_sha256",
        "missing[]",
        "required_disclosure",
        "reply_requirement",
        "sharing_authorization",
        "delivery_contract",
        "status",
        "focus",
        "deterministic_components",
        "detail.{soreness,flagged_groups[]}",
        "evidence",
        "disclaimer",
        "instruction",
    ]
    assert "your own baselines" not in serialized


def test_recovery_delivery_contract_is_same_completion_turn_bound_and_private_safe(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    result = _recovery_result_with_evidence()
    private_values = {
        "sidecar_path": "/private/verifier/ancestry.json",
        "database_sha256": "sha256:" + "8" * 64,
        "canonical_rows_sha256": "sha256:" + "9" * 64,
        "canonical_recovery_rows_sha256": "sha256:" + "7" * 64,
        "sidecar_sha256": "sha256:" + "0" * 64,
        "note_digest": "sha256:" + "1" * 64,
    }
    result["evidence"].update(private_values)
    result["evidence"]["snapshot_integrity"].update(private_values)
    result["evidence"]["soreness"]["content_fingerprint"] = (
        "sha256:" + "b" * 64
    )
    envelope = {
        "result": result,
        "result_sha256": "sha256:" + "e" * 64,
    }
    journey = namespace["RecoveryJourney"]()
    journey._execute_readiness = lambda: envelope
    journey._save_receipt = lambda _focus=None: None

    snapshot = journey.snapshot()
    expected_turn_id = snapshot["turn_id"]
    delivery = snapshot["delivery_contract"]
    serialized = json.dumps(snapshot)

    assert delivery["contract"] == "hermes-gateway-required-delivery-v1"
    assert delivery["renderer_id"] == "openhealthatlas-recovery-delivery"
    assert delivery["renderer_version"] == "1.0.0"
    assert delivery["trusted_source_requirement"] == (
        "successful_same_turn_openhealthatlas_mcp_completion"
    )
    assert delivery["correlation"] == {
        "openhealthatlas_turn_id": expected_turn_id,
        "mcp_tool": "openhealthatlas_recovery_snapshot",
        "completion_id": delivery["correlation"]["completion_id"],
        "gateway_tool_call_id_required": True,
    }
    assert delivery["complete_telegram_payload_validation_required"] is True
    assert delivery["delivery_order"] == [
        "trusted_disclosure", "optional_hermes_prose",
    ]
    assert delivery["trusted_facts"]["required_disclosure"] == (
        snapshot["required_disclosure"]
    )
    assert delivery["trusted_facts"]["sharing_authorization"] == (
        snapshot["sharing_authorization"]
    )
    assert delivery["trusted_facts"]["result_sha256"] == snapshot["result_sha256"]
    assert snapshot["result_sha256"] != envelope["result_sha256"]
    assert delivery["trusted_facts"]["openhealthatlas_turn_id"] == expected_turn_id
    assert result["evidence"]["soreness"]["content_fingerprint"] not in serialized
    for value in private_values.values():
        assert value not in serialized
    assert "last-recovery-receipt" not in json.dumps(delivery)


def test_recovery_sharing_authorization_is_destination_specific_not_fingerprint_input(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    result = _recovery_result_with_evidence()
    authorization = namespace["_sharing_authorization"](
        response_kind="snapshot", focus=None, result=result
    )

    assert authorization["authorization_id"] == (
        "openhealthatlas-fictional-recovery-telegram"
    )
    assert authorization["version"] == "1.0.0"
    assert authorization["destination"] == "telegram_via_external_hermes_gateway"
    assert authorization["destination_binding"] == (
        "same_authenticated_gateway_source"
    )
    assert authorization["calculation_fingerprint_member"] is False
    assert authorization["interpretation_writeback"] == "disabled"
    assert authorization["qualitative_source_authorization"] == {
        "contract": "openhealthatlas-qualitative-source-authorization-v1",
        "authorization_id": "openhealthatlas-fictional-soreness-summary-telegram",
        "version": "1.0.0",
        "status": "authorized",
        "permitted_summary_paths": [
            "result.soreness.present",
            "result.soreness.date",
            "result.soreness.source_label",
            "result.soreness.source_locator",
            "result.muscle_recovery[].sore",
        ],
        "raw_note_permitted": False,
        "note_derived_secret_permitted": False,
    }
    assert authorization["real_data_governance_gate"]["status"] == (
        "blocked_unresolved_decisions"
    )
    projected_evidence = namespace["_readiness_evidence"](result)
    assert projected_evidence["input_fingerprint"] == "sha256:" + "a" * 64
    assert "authorization" not in json.dumps(projected_evidence)


def test_recovery_v2_evidence_fails_closed_without_public_identities(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    for missing in ("policy_sha256", "public_evidence_identity"):
        result = _recovery_result_with_evidence()
        del result["evidence"][missing]
        with pytest.raises(ValueError, match="governed readiness evidence"):
            namespace["_model_readiness_result"](result)


def test_public_result_identity_ignores_raw_note_but_tracks_permitted_projection(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    first = _recovery_result_with_evidence()
    second = _recovery_result_with_evidence()
    second["soreness"]["note"] = "A DIFFERENT PRIVATE FICTIONAL RAW NOTE"

    first_projected = namespace["_model_readiness_result"](first)
    second_projected = namespace["_model_readiness_result"](second)
    assert namespace["_public_result_sha256"]("snapshot", first_projected) == (
        namespace["_public_result_sha256"]("snapshot", second_projected)
    )

    second["muscle_recovery"][0]["sore"] = False
    changed_projected = namespace["_model_readiness_result"](second)
    assert namespace["_public_result_sha256"]("snapshot", first_projected) != (
        namespace["_public_result_sha256"]("snapshot", changed_projected)
    )


def test_real_data_governance_registry_is_explicit_and_fail_closed():
    gate = json.loads(REAL_DATA_GOVERNANCE.read_text(encoding="utf-8"))
    assert gate["contract"] == "openhealthatlas-real-data-governance-gate-v1"
    assert gate["activation_allowed"] is False
    assert gate["fictional_tests_only"] is True
    assert gate["missing_unknown_or_unapproved_action"] == "deny"
    assert gate["real_data_status"] == "blocked_unresolved_decisions"
    assert set(gate["decisions"]) == {
        "consent", "retention", "provider_transfer", "deletion",
    }
    for decision in gate["decisions"].values():
        assert decision["status"] == "unresolved"
        assert decision["required"]


def test_recovery_item6_staging_manifest_is_exact_and_non_mutating():
    manifest = json.loads(RECOVERY_STAGING_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["contract"] == (
        "openhealthatlas-recovery-item6-staging-manifest-v2"
    )
    assert manifest["deployment_authorized"] is False
    assert manifest["staging_only"] is True
    assert manifest["write_or_restart_actions"] == []
    assert manifest["legacy_installer_allowed"] is False
    assert manifest["legacy_installer_path"] == INSTALLER.relative_to(ROOT).as_posix()
    topology = manifest["accepted_openhealthatlas_topology"]
    assert topology == {
        "fixture_root": "/var/lib/openhealthatlas-fictional",
        "installed_profile_root": (
            "/usr/local/lib/hermes-openhealthatlas-fictional"
        ),
        "mcp_server_alias": "openhealthatlas_fictional",
        "observed_recovery_tool_names": [
            "mcp_openhealthatlas_fictional_"
            "openhealthatlas_recovery_snapshot",
            "mcp_openhealthatlas_fictional_"
            "openhealthatlas_recovery_detail",
        ],
        "source_root": "/opt/openhealthatlas-fictional",
    }
    bundle = manifest["deterministic_python_bundle"]
    assert bundle["source_root"] == "toolkit"
    assert bundle["live_destination_root"] == (
        "/opt/openhealthatlas-fictional/toolkit"
    )
    assert bundle["tracked_files_only"] is True
    assert bundle["executable_files"] == ["health.py"]
    assert bundle["executable_file_mode"] == "0755"
    assert bundle["ordinary_file_mode"] == "0644"
    assert "file_mode" not in bundle
    assert len(bundle["files"]) == len(set(bundle["files"]))
    assert "health.py" in bundle["files"]
    assert set(bundle["executable_files"]) <= set(bundle["files"])
    bundle_modes = {
        relative: (
            bundle["executable_file_mode"]
            if relative in bundle["executable_files"]
            else bundle["ordinary_file_mode"]
        )
        for relative in bundle["files"]
    }
    assert bundle_modes["health.py"] == "0755"
    assert {
        mode for relative, mode in bundle_modes.items()
        if relative != "health.py"
    } == {"0644"}
    assert "hermes_insights/readiness_ancestry.py" in bundle["files"]
    assert "hermes_insights/hermes_surface.py" in bundle["files"]
    assert all("interpretation" not in Path(relative).parts for relative in bundle["files"])
    tracked = set(subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines())
    assert all(
        f"{bundle['source_root']}/{relative}" in tracked
        for relative in bundle["files"]
    )
    sources = {row["source"] for row in manifest["source_files"]}
    assert {
        "scripts/build_readiness_ancestry.py",
        "deploy/hermes-openhealthatlas-tool",
        "deploy/hermes-openhealthatlas-fictional-config.json",
        "deploy/hermes-openhealthatlas-mcp",
        "deploy/openhealthatlas-real-data-governance.json",
        "deploy/hermes-openhealthatlas-skill.md",
        "deploy/hermes-openhealthatlas-delivery-plugin/__init__.py",
        "deploy/hermes-openhealthatlas-delivery-plugin/plugin.yaml",
        "deploy/hermes-openhealthatlas-delivery-plugin/README.md",
        "deploy/hermes-openhealthatlas-delivery-plugin/enablement.example.yaml",
    } == sources
    for row in manifest["source_files"]:
        source = ROOT / row["source"]
        assert source.is_file()
        if "live_destination" in row:
            assert row["live_destination"].startswith("/")
        else:
            assert row["destination_root"] == "external_hermes_home"
            assert not row["relative_destination"].startswith("/")
            assert ".." not in Path(row["relative_destination"]).parts
    assert all(row["owner"] in {"root", "hermes"} for row in manifest["source_files"])
    assert all(row["group"] in {"root", "hermes"} for row in manifest["source_files"])
    assert all(row["mode"] in {"0640", "0644", "0755"} for row in manifest["source_files"])
    release_paths = {
        line.split("\t", 1)[0]
        for line in (ROOT / "RELEASE_MANIFEST.tsv").read_text(
            encoding="utf-8"
        ).splitlines()[1:]
    }
    staged_openhealthatlas_sources = {
        row["source"] for row in manifest["source_files"]
    } | {
        f"{bundle['source_root']}/{relative}" for relative in bundle["files"]
    }
    assert staged_openhealthatlas_sources <= release_paths
    installed_profile = topology["installed_profile_root"] + "/"
    installed_rows = [
        row for row in manifest["source_files"]
        if row.get("live_destination", "").startswith("/usr/local/lib/")
    ]
    assert installed_rows
    assert all(
        row["live_destination"].startswith(installed_profile)
        for row in installed_rows
    )
    hermes_home = manifest["external_hermes_home"]
    assert hermes_home["resolved_live_root"] is None
    assert hermes_home["status"] == "must_resolve_and_bind_in_deployment_record"
    generated = manifest["generated_private_artifacts"]
    assert len(generated) == 1
    assert generated[0]["mode"] == "0600"
    assert generated[0]["replacement_allowed"] is False
    deployed = manifest["generated_deployment_artifacts"]
    assert deployed == [{
        "contract": "openhealthatlas-deployed-manifest-v1",
        "content_template": "git_commit=<40-lowercase-hex>\n",
        "git_commit_source": (
            "candidate_commits.openhealthatlas.contract_candidate"
        ),
        "group": "root",
        "live_destination": (
            "/opt/openhealthatlas-fictional/toolkit/DEPLOYED.manifest"
        ),
        "mode": "0644",
        "owner": "root",
        "prechange_inventory_required": True,
    }]
    assert manifest["external_hermes"]["candidate_commit_source"] == (
        "candidate_commits.external_hermes.combined_candidate"
    )
    external_hermes = manifest["external_hermes"]
    assert external_hermes["canonical_upstream_repository"] == (
        "https://github.com/NousResearch/hermes-agent.git"
    )
    assert external_hermes["approved_candidate_repository"] == (
        "https://github.com/kajeesan/hermes-agent.git"
    )
    assert external_hermes["candidate_repository_url_must_equal_live_origin"] is False
    assert external_hermes["clean_combined_candidate_required"] is True
    assert external_hermes["upstream_base_commit"] == (
        "a6b9597d5fb92969d605a858d5f14536e805553a"
    )
    assert "live_checkout" not in external_hermes
    assert external_hermes["clean_candidate_checkout_root"] == (
        "/opt/hermes-agent-releases"
    )
    assert external_hermes["clean_candidate_checkout_template"] == (
        "/opt/hermes-agent-releases/<40-lowercase-hex-commit>"
    )
    assert external_hermes["current_live_checkout_resolution_source"] == (
        "read_only_external_hermes_service_configuration"
    )
    assert external_hermes["current_live_checkout_rule"] == (
        "inventory_and_preserve_as_untouched_rollback_source"
    )
    assert external_hermes["service_cutover_target_source"] == (
        "candidate_commits.external_hermes.combined_candidate"
    )
    assert manifest["external_hermes"]["runtime_files"] == [
        "agent/turn_finalizer.py",
        "gateway/platforms/base.py",
        "gateway/required_delivery.py",
        "gateway/run.py",
        "hermes_cli/plugins.py",
        "model_tools.py",
        "plugins/platforms/telegram/adapter.py",
        "plugins/platforms/telegram/health_actions.py",
    ]
    materialization = manifest["clean_release_materialization"]
    assert materialization["source_commit"] == (
        "candidate_commits.external_hermes.combined_candidate"
    )
    assert materialization["source_tree"] == (
        "candidate_commits.external_hermes.git_tree_id"
    )
    assert materialization["destination"] == (
        "/opt/hermes-agent-releases/<40-lowercase-hex-commit>"
    )
    assert materialization["runtime_python_must_remain_unchanged"] is True
    assert materialization["materialization"].startswith("full_tracked_git_tree")
    config_post = manifest["hermes_configuration_post_state"]
    mcp_post = config_post["mcp_servers"]["openhealthatlas_fictional"]
    assert mcp_post["command"] == "<existing-gateway-exec-start-python>"
    assert mcp_post["args"] == [
        "/usr/local/lib/hermes-openhealthatlas-fictional/"
        "hermes-openhealthatlas-mcp"
    ]
    assert mcp_post["enabled"] is True
    assert mcp_post["tools"]["include"] == [
        "openhealthatlas_analysis_refresh",
        "openhealthatlas_data_readiness",
        "openhealthatlas_feature_frame",
        "openhealthatlas_feature_registry",
        "openhealthatlas_finding_evidence",
        "openhealthatlas_health_analyze",
        "openhealthatlas_health_catalog",
        "openhealthatlas_health_evidence",
        "openhealthatlas_health_query",
        "openhealthatlas_recovery_detail",
        "openhealthatlas_recovery_snapshot",
        "openhealthatlas_synthesis_prepare",
    ]
    assert config_post["plugins_enabled_merge"] == {
        "add": "openhealthatlas-recovery-delivery",
        "preserve_existing_entries": True,
        "duplicates_forbidden": True,
    }
    cutover = manifest["gateway_service_cutover"]
    assert cutover["unit"] == "hermes-gateway.service"
    assert cutover["dropin_relative_path"] == (
        "hermes-gateway.service.d/openhealthatlas-item6.conf"
    )
    assert cutover["dropin_content"] == (
        "[Service]\nWorkingDirectory=/opt/hermes-agent-releases/"
        "<40-lowercase-hex-commit>\n"
    )
    assert cutover["daemon_reload_and_restart_require_separate_approval"] is True
    rollback = manifest["required_prechange_inventory"]
    assert rollback["contract"] == (
        "openhealthatlas-recovery-item6-rollback-inventory-v1"
    )
    assert rollback["backup_root_mode"] == "0700"
    assert rollback["backup_manifest_mode"] == "0600"
    assert rollback["completion_rule"] == (
        "every_planned_mutation_target_must_be_exactly_covered_by_inventory_"
        "and_all_mandatory_protected_controls_must_be_inventoried_and_"
        "isolated_restore_rehearsal_must_pass"
    )
    assert rollback["live_dirty_checkout_rule"].startswith("never_reset")
    assert "DEPLOYED_manifest" in rollback["planned_mutation_target_sets"][1]
    assert "accepted_v5_database" in rollback["mandatory_protected_control_sets"][1]
    assert "untouched_live_dirty_checkout" in rollback[
        "mandatory_protected_control_sets"
    ][-1]
    assert rollback["clean_release_rollback_rule"].startswith(
        "move_failed_release_to_restricted_quarantine"
    )



@pytest.mark.parametrize("mutation", ["activation", "required_replaced", "required_missing"])
def test_mcp_fails_closed_when_governance_registry_is_missing_or_tampered(
    tmp_path, monkeypatch, mutation,
):
    with pytest.raises(RuntimeError, match="governance gate is unavailable"):
        _load_mcp_namespace(
            tmp_path / f"missing-{mutation}", monkeypatch, governance=None
        )

    tampered = json.loads(REAL_DATA_GOVERNANCE.read_text(encoding="utf-8"))
    if mutation == "activation":
        tampered["activation_allowed"] = True
    elif mutation == "required_replaced":
        tampered["decisions"]["consent"]["required"] = ["placeholder_only"]
    else:
        tampered["decisions"]["deletion"]["required"].pop()
    with pytest.raises(RuntimeError, match="must remain fail closed"):
        _load_mcp_namespace(
            tmp_path / f"tampered-{mutation}", monkeypatch, governance=tampered
        )


def test_recovery_model_projection_drops_unapproved_nested_fields(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    result = _recovery_result_with_evidence()
    sentinel = "SECRET UNAPPROVED NESTED VALUE"
    result["components"][0]["raw_history"] = sentinel
    result["muscle_recovery"][0]["raw_note"] = sentinel
    result["evidence"]["policy"]["raw_policy_input"] = sentinel
    result["evidence"]["policy"]["sleep"] = {
        "target_hours": 8.0,
        "method": "hours_target_70pct_plus_optional_quality_30pct",
        "quality_scale_max": 5,
        "raw_rows": sentinel,
    }
    component = result["evidence"]["components"][0]
    component["raw_values"] = sentinel
    component["current"]["raw_value"] = sentinel
    component["baseline"] = {
        "source_label": "fictional-demo",
        "observation_count": 14,
        "range_from": "2026-06-16",
        "range_to": "2026-06-29",
        "locators": ["sleep_log:2026-06-16"],
        "raw_values": sentinel,
    }
    result["evidence"]["warnings"] = [{
        "code": "fixture-warning",
        "component": "sleep",
        "raw_values": sentinel,
    }]
    result["evidence"]["remaining_ancestry_gaps"].append({
        "code": "fixture_gap", "scope": ["fixture"], "raw_rows": sentinel,
    })
    result["evidence"]["soreness"]["raw_note"] = sentinel
    _refresh_public_evidence_identities(result)

    projected = namespace["_model_readiness_result"](result)
    assert sentinel not in json.dumps(projected)
    assert projected["evidence"]["policy"]["sleep"] == {
        "target_hours": 8.0,
        "method": "hours_target_70pct_plus_optional_quality_30pct",
        "quality_scale_max": 5,
    }
    assert projected["evidence"]["components"][0]["current"] == {
        "table": "sleep_log",
        "locator": "sleep_log:2026-06-30",
        "source_label": "fictional-demo",
        "observed_at": "2026-06-30",
    }


def test_recovery_disclosure_distinguishes_excluded_from_missing_and_ancestry(
    tmp_path, monkeypatch,
):
    namespace = _load_mcp_namespace(tmp_path, monkeypatch)
    result = _recovery_result_with_evidence()
    journey = namespace["RecoveryJourney"]()
    missing = journey._missing(result)
    disclosure = namespace["_recovery_disclosure"](
        response_kind="snapshot",
        focus=None,
        result=result,
        result_sha256="sha256:" + "c" * 64,
        missing=missing,
    )

    assert missing == []
    missingness = disclosure["component_missingness"]
    assert missingness["status"] == "none_missing"
    assert missingness["missing"] == []
    assert {row["key"] for row in missingness["excluded"]} == {"hrv", "rhr"}
    assert all(
        row["source_label"] == "fitbit"
        and row["required"] == 14
        and row["observed"] == 10
        and row["other_source_observations_excluded"] == 86
        for row in missingness["excluded"]
    )
    assert disclosure["source_ancestry_status"] == "verified_current_snapshot"
    assert disclosure["historical_generation_event_status"] == "not_attested"
