"""End-to-end contracts for the bounded external-Hermes health surface."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MAKE_DEMO = ROOT / "scripts" / "make_demo_db.py"
SURFACE_MODULE = "hermes_insights.hermes_surface"
SHA256_PREFIX = "sha256:"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def fictional_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    database = tmp_path / "health.db"
    subprocess.run(
        [
            sys.executable,
            str(MAKE_DEMO),
            "--output",
            str(database),
            "--anchor-date",
            "2026-06-30",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    environment = dict(os.environ)
    environment.update({
        "HEALTH_DB": str(database),
        "HERMES_DATA_DIR": str(tmp_path),
        "HERMES_TIMEZONE": "UTC",
        "OPENHEALTHATLAS_DATA_CLASS": "fictional",
        "OPENHEALTHATLAS_FIXTURE_ID": "comprehensive-persona-v1",
        "PYTHONPATH": str(ROOT / "toolkit"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return database, environment


def _run(
    fixture: tuple[Path, dict[str, str]], request: object,
) -> tuple[subprocess.CompletedProcess[str], dict, str, str]:
    database, environment = fixture
    before = _sha(database)
    completed = subprocess.run(
        [sys.executable, "-m", SURFACE_MODULE],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        env=environment,
        timeout=90,
    )
    body = json.loads(completed.stdout)
    return completed, body, before, _sha(database)


def _assert_common(body: dict, operation: str) -> None:
    assert set(body) == {
        "contract",
        "operation",
        "data_class",
        "fixture_id",
        "status",
        "range",
        "registry_id",
        "engine_id",
        "result_id",
        "coverage",
        "facts",
        "findings",
        "limitations",
        "evidence_refs",
    }
    assert body["contract"] == "openhealthatlas-hermes-surface-v1"
    assert body["operation"] == operation
    assert body["data_class"] == "fictional"
    assert body["fixture_id"] == "comprehensive-persona-v1"
    assert body["status"] in {"ok", "insufficient_data", "unsupported", "refused"}
    for key in ("registry_id", "engine_id", "result_id"):
        assert body[key].startswith(SHA256_PREFIX)
        assert len(body[key]) == len(SHA256_PREFIX) + 64


def test_analysis_evidence_reuses_exact_statistics_and_still_checks_replay_identity(
    tmp_path, monkeypatch,
):
    from hermes_insights import associations, exact_cache, hermes_surface, migrations, runtime

    database = tmp_path / "small-hermes.db"
    with sqlite3.connect(database) as writer:
        writer.executescript((ROOT / "toolkit" / "SCHEMA.sql").read_text())
    migrations.migrate(str(database), 3, 0, code_version="b" * 40)
    before = database.read_bytes()
    monkeypatch.setattr(runtime, "DB", str(database))
    monkeypatch.setenv("OPENHEALTHATLAS_DATA_CLASS", "fictional")
    original = associations.analyze_outcome
    calls = []

    def calculate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(associations, "analyze_outcome", calculate)
    analysis = hermes_surface.execute({
        "operation": "health_analyze",
        "arguments": {"outcome_key": "subjective.energy",
                      "exposure_keys": ["sleep.duration_hours"],
                      "range": {"from": "2026-06-24", "to": "2026-06-30"},
                      "mode": "ordinal", "interactions": False, "min_n": 30, "top": 1},
    })
    reference = analysis["evidence_refs"][0]
    evidence = hermes_surface.execute({
        "operation": "health_evidence",
        "arguments": {"evidence_refs": [reference], "detail": "summary"},
    })
    assert calls == [True]
    assert evidence["facts"][0]["verified"] is True
    assert evidence["facts"][0]["result_id"] == analysis["result_id"]
    assert database.read_bytes() == before
    with sqlite3.connect(exact_cache.store_path(database)) as store:
        assert store.execute("SELECT count(*) FROM exact_results").fetchone()[0] == 1
    with pytest.raises(hermes_surface.SurfaceError) as error:
        hermes_surface.execute({
            "operation": "health_evidence",
            "arguments": {"evidence_refs": [{**reference, "result_id": "sha256:" + "0" * 64}],
                          "detail": "summary"},
        })
    assert error.value.code == "stale_evidence"
    assert calls == [True]


def test_engine_identity_manifest_covers_context_contracts_and_every_adapter(
    fictional_fixture,
):
    _database, environment = fictional_fixture
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from hermes_insights.hermes_surface import _engine_manifest; "
                "print(json.dumps(_engine_manifest(), sort_keys=True))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    manifest = json.loads(completed.stdout)
    paths = {item["file"] for item in manifest}
    assert "health.py" in paths
    assert "hermes_insights/runtime.py" in paths
    assert {
        "hermes_insights/cli.py",
        "hermes_insights/command_context.py",
        "hermes_insights/commands/__init__.py",
        "hermes_insights/commands/hevy.py",
        "hermes_insights/importers/__init__.py",
        "hermes_insights/importers/hevy_csv.py",
        "hermes_insights/commands/cronometer.py",
        "hermes_insights/commands/google_health.py",
        "hermes_insights/commands/lab_catalog.py",
        "hermes_insights/commands/recipes.py",
        "hermes_insights/commands/submuscle_map.py",
        "hermes_insights/importers/common.py",
        "hermes_insights/importers/hevy_json.py",
        "hermes_insights/importers/cronometer.py",
        "hermes_insights/importers/google_health.py",
        "hermes_insights/importers/lab_catalog.py",
        "hermes_insights/importers/recipes.py",
        "hermes_insights/importers/submuscle_map.py",
        "hermes_insights/body_contracts.py",
        "hermes_insights/fitness_contracts.py",
        "hermes_insights/routine_history.py",
    } <= paths
    assert "hermes_insights/catalogs.py" in paths
    assert "hermes_insights/calculations.py" in paths
    assert "hermes_insights/contracts.py" in paths
    assert "hermes_insights/events.py" in paths
    assert "hermes_insights/goals.py" in paths
    assert "hermes_insights/normalize.py" in paths
    assert "hermes_insights/adapters/__init__.py" in paths
    assert "hermes_insights/registry.py" in paths
    assert "hermes_insights/frame.py" in paths
    assert {
        "hermes_insights/adapters/daily.py",
        "hermes_insights/adapters/training.py",
        "hermes_insights/adapters/running.py",
        "hermes_insights/adapters/nutrition.py",
        "hermes_insights/adapters/events.py",
        "hermes_insights/adapters/quarterly.py",
        "hermes_insights/adapters/pain.py",
        "hermes_insights/adapters/labs.py",
        "hermes_insights/adapters/environment.py",
        "hermes_insights/adapters/adherence.py",
        "hermes_insights/adapters/manual.py",
    } <= paths
    original = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    manifest[0]["sha256"] = "0" * 64
    changed = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    assert changed != original


def test_catalog_discovers_cross_domain_registered_features(fictional_fixture):
    completed, body, before, after = _run(fictional_fixture, {
        "operation": "health_catalog",
        "arguments": {"search": "protein", "roles": ["exposure"], "limit": 10},
    })

    assert completed.returncode == 0
    assert before == after
    _assert_common(body, "health_catalog")
    assert body["status"] == "ok"
    assert 1 <= len(body["facts"]) <= 10
    assert all("exposure" in item["roles"] for item in body["facts"])
    assert any(item["pillar"] in {"food", "nutrition"} for item in body["facts"])
    assert body["evidence_refs"][0]["operation"] == "health_catalog"
    assert "value" not in body["facts"][0]


def test_query_combines_sleep_training_nutrition_and_subjective_data(
    fictional_fixture,
):
    completed, body, before, after = _run(fictional_fixture, {
        "operation": "health_query",
        "arguments": {
            "feature_keys": [
                "sleep.duration_hours",
                "training.session",
                "nutrition.logged.protein_g",
                "subjective.energy",
            ],
            "range": {"from": "2026-06-01", "to": "2026-06-30"},
            "view": "summary",
            "aggregates": ["count", "mean", "min", "max"],
        },
    })

    assert completed.returncode == 0
    assert before == after
    _assert_common(body, "health_query")
    assert body["status"] == "ok"
    by_key = {item["feature_key"]: item for item in body["facts"]}
    assert set(by_key) == {
        "sleep.duration_hours",
        "training.session",
        "nutrition.logged.protein_g",
        "subjective.energy",
    }
    assert by_key["sleep.duration_hours"]["observed_count"] == 30
    assert by_key["training.session"]["observed_count"] > 0
    assert by_key["nutrition.logged.protein_g"]["aggregates"]["mean"] > 0
    assert by_key["subjective.energy"]["state_counts"]["missing"] > 0
    serialized = json.dumps(body)
    assert "health.db" not in serialized
    assert "vault" not in serialized.lower()
    assert "raw_note" not in serialized


def test_query_evidence_reference_replays_without_hidden_state(fictional_fixture):
    request = {
        "operation": "health_query",
        "arguments": {
            "feature_keys": ["sleep.duration_hours", "nutrition.logged.protein_g"],
            "range": {"from": "2026-06-01", "to": "2026-06-30"},
            "view": "period_compare",
            "aggregates": ["count", "mean"],
        },
    }
    first, result, before, after = _run(fictional_fixture, request)
    assert first.returncode == 0
    assert before == after
    reference = result["evidence_refs"][0]

    replay, evidence, replay_before, replay_after = _run(fictional_fixture, {
        "operation": "health_evidence",
        "arguments": {"evidence_refs": [reference], "detail": "lineage"},
    })
    assert replay.returncode == 0
    assert replay_before == replay_after
    _assert_common(evidence, "health_evidence")
    assert evidence["status"] == "ok"
    assert evidence["coverage"]["requested_references"] == 1
    assert evidence["coverage"]["verified_references"] == 1
    assert evidence["coverage"]["referenced_statuses"] == {"ok": 1}
    assert evidence["facts"][0]["verified"] is True
    assert evidence["facts"][0]["result_id"] == result["result_id"]


def test_analysis_returns_honest_insufficient_data_and_replayable_identity(
    fictional_fixture,
):
    completed, body, before, after = _run(fictional_fixture, {
        "operation": "health_analyze",
        "arguments": {
            "outcome_key": "subjective.energy",
            "exposure_keys": ["training.session"],
            "range": {"from": "2026-06-24", "to": "2026-06-30"},
            "mode": "ordinal",
            "interactions": False,
            "min_n": 30,
            "top": 5,
        },
    })
    assert completed.returncode == 0
    assert before == after
    _assert_common(body, "health_analyze")
    assert body["status"] == "insufficient_data"
    assert body["findings"] == []
    assert body["limitations"][0]["code"] == "insufficient_data"
    assert body["evidence_refs"][0]["result_id"] == body["result_id"]


def test_successful_analysis_exposes_only_eligible_finding_and_replays_it(
    fictional_fixture,
):
    _completed, analysis, before, after = _run(fictional_fixture, {
        "operation": "health_analyze",
        "arguments": {
            "outcome_key": "subjective.energy",
            "exposure_keys": ["sleep.duration_hours"],
            "range": {"from": "2026-03-02", "to": "2026-06-30"},
            "mode": "ordinal",
            "interactions": False,
            "min_n": 30,
            "top": 1,
        },
    })
    assert before == after
    assert analysis["status"] == "ok"
    assert len(analysis["findings"]) == 1
    finding = analysis["findings"][0]
    assert finding["quality"]["eligible_for_hypothesis"] is True
    assert finding["testing"]["q"] <= 0.1
    serialized = json.dumps(finding)
    for forbidden in (
        "_source_observations", "_source_references", "natural_key",
        "health.db", "sidecar_path", "raw_note",
    ):
        assert forbidden not in serialized
    finding_ref = next(
        item for item in analysis["evidence_refs"] if "finding_id" in item
    )

    _replay, evidence, replay_before, replay_after = _run(fictional_fixture, {
        "operation": "health_evidence",
        "arguments": {"evidence_refs": [finding_ref], "detail": "summary"},
    })
    assert replay_before == replay_after
    assert evidence["status"] == "ok"
    assert evidence["facts"][0]["verified"] is True
    assert evidence["facts"][0]["finding"]["finding_id"] == finding["finding_id"]
    summary = evidence["facts"][0]["deterministic_summary"]
    assert summary["operation"] == "health_analyze"
    assert summary["shown_rows"] == 1
    assert summary["rows"][0]["finding_id"] == finding["finding_id"]


def test_evidence_preserves_unsupported_status_and_rejects_duplicate_refs(
    fictional_fixture,
):
    _completed, unsupported, before, after = _run(fictional_fixture, {
        "operation": "health_query",
        "arguments": {
            "feature_keys": ["unsupported.example.feature"],
            "range": {"from": "2026-06-01", "to": "2026-06-30"},
            "view": "summary",
        },
    })
    assert before == after
    assert unsupported["status"] == "unsupported"
    reference = unsupported["evidence_refs"][0]

    _replay, evidence, replay_before, replay_after = _run(fictional_fixture, {
        "operation": "health_evidence",
        "arguments": {"evidence_refs": [reference], "detail": "summary"},
    })
    assert replay_before == replay_after
    assert evidence["status"] == "unsupported"
    assert evidence["coverage"]["referenced_statuses"] == {"unsupported": 1}
    assert evidence["limitations"][0]["status"] == "unsupported"

    _duplicate, refused, duplicate_before, duplicate_after = _run(
        fictional_fixture,
        {
            "operation": "health_evidence",
            "arguments": {
                "evidence_refs": [reference, reference],
                "detail": "summary",
            },
        },
    )
    assert duplicate_before == duplicate_after
    assert refused["status"] == "refused"
    assert refused["limitations"][0]["code"] == "validation_error"


def test_evidence_refuses_analysis_replay_above_work_budget(fictional_fixture):
    exposures = [f"sleep.synthetic-{index}" for index in range(32)]
    reference = {
        "contract": "openhealthatlas-evidence-ref-v1",
        "operation": "health_analyze",
        "arguments": {
            "outcome_key": "subjective.energy",
            "exposure_keys": exposures,
            "range": {"from": "2026-03-02", "to": "2026-06-30"},
            "mode": "ordinal",
            "interactions": True,
            "min_n": 30,
            "top": 5,
        },
        "result_id": "sha256:" + "a" * 64,
    }
    _completed, body, before, after = _run(fictional_fixture, {
        "operation": "health_evidence",
        "arguments": {"evidence_refs": [reference], "detail": "summary"},
    })
    assert before == after
    assert body["status"] == "refused"
    assert body["limitations"][0]["code"] == "work_budget_exceeded"

    _analysis, initial, initial_before, initial_after = _run(fictional_fixture, {
        "operation": "health_analyze",
        "arguments": reference["arguments"],
    })
    assert initial_before == initial_after
    assert initial["status"] == "refused"
    assert initial["limitations"][0]["code"] == "work_budget_exceeded"


@pytest.mark.parametrize("arguments", [
    {
        "feature_keys": ["sleep.duration_hours"],
        "range": {"from": "2026-06-01", "to": "2026-06-30"},
        "view": "summary",
        "sql": "select everything",
    },
    {
        "feature_keys": ["sleep.duration_hours"],
        "range": {"from": "2026-06-01", "to": "2026-06-30"},
        "view": "summary",
        "path": "/forbidden/private-health",
    },
])
def test_surface_refuses_raw_or_path_shaped_requests_without_db_change(
    fictional_fixture, arguments,
):
    completed, body, before, after = _run(fictional_fixture, {
        "operation": "health_query",
        "arguments": arguments,
    })
    assert completed.returncode == 0
    assert before == after
    assert body["status"] == "refused"
    assert body["facts"] == []
    assert body["findings"] == []
    assert body["evidence_refs"] == []
