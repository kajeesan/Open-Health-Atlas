"""The documented fictional flow crosses its declared product boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SEED_SHA256 = (
    "sha256:2b77c73323a7f0656b845c0c3cdac21f"
    "e08e47dc1fda4c3882c680e57f8bd9be"
)
ACTIONABLE_SEED_SHA256 = (
    "sha256:01e9f7d900fbcc2b0531f3f4d5cf141a"
    "64727916d7b018362d0fabee3f2ca361"
)
MARKER_ONLY_SEED_SHA256 = (
    "sha256:31b85ea572cfff1df0aeb3567c6acae8"
    "79d8423c125a200e62942c9e6c3393c6"
)


def _seed_named_fixture(tmp_path: Path, fixture_id: str) -> tuple[Path, dict]:
    data_dir = tmp_path / fixture_id
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "demo_flow.py"),
            "--data-dir", str(data_dir),
            "--fixture-id", fixture_id,
            "--seed-only",
        ],
        cwd=ROOT,
        capture_output=True,
        env={**os.environ, "HERMES_TIMEZONE": "UTC"},
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return data_dir, json.loads(result.stdout)


def _fixture_health(data_dir: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(ROOT / "toolkit" / "health.py"), *args],
        cwd=ROOT,
        capture_output=True,
        env={
            **os.environ,
            "HEALTH_DB": str(data_dir / "health.db"),
            "HEALTH_VAULT": str(data_dir / "vault"),
            "HERMES_DATA_DIR": str(data_dir),
            "HERMES_TIMEZONE": "UTC",
            "HERMES_CODE_VERSION": "0" * 40,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_seed_only_creates_reproducible_green_day_fixture(tmp_path):
    data_dir = tmp_path / "green-days-v1"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo_flow.py"),
            "--data-dir",
            str(data_dir),
            "--seed-only",
        ],
        cwd=ROOT,
        capture_output=True,
        env={**os.environ, "HERMES_TIMEZONE": "UTC"},
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["contract"] == "openhealthatlas-fictional-fixture-v1"
    assert report["data_class"] == "fictional"
    assert report["fixture_id"] == "green-days-v1"
    assert report["range_from"] == "2026-05-17"
    assert report["range_to"] == "2026-06-30"
    assert report["day_count"] == 45
    assert report["green_count"] == 15
    assert report["seed_sha256"] == EXPECTED_SEED_SHA256
    assert report["analysis_runs"] == 0
    assert report["synthesis_runs"] == 0
    assert report["external_services_used"] is False
    assert len(report["green_dates"]) == 15
    manifest = json.loads(
        (data_dir / "fixture-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {key: value for key, value in report.items() if key != "ok"}
    database = data_dir / "health.db"
    assert database.is_file()

    verified = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo_flow.py"),
            "--data-dir",
            str(data_dir),
            "--verify-fixture",
            str(data_dir / "fixture-manifest.json"),
            "--audit",
            str(data_dir / "not-created.jsonl"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["database_sha256"] == report["database_sha256"]

    linked_data_dir = tmp_path / "linked-green-days-v1"
    linked_data_dir.symlink_to(data_dir, target_is_directory=True)
    linked = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo_flow.py"),
            "--data-dir",
            str(linked_data_dir),
            "--verify-fixture",
            str(data_dir / "fixture-manifest.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert linked.returncode != 0
    assert "symbolic links" in linked.stderr

    database.write_bytes(database.read_bytes() + b"audited-fictional-change")
    evolved_sha256 = "sha256:" + hashlib.sha256(database.read_bytes()).hexdigest()
    audit = data_dir / "openhealthatlas-tools.jsonl"
    audit.write_text(json.dumps({
        "contract": "openhealthatlas-hermes-tool-v1",
        "phase": "result",
        "fixture_id": "green-days-v1",
        "data_class": "fictional",
        "returncode": 0,
        "database_before_sha256": report["database_sha256"],
        "database_after_sha256": evolved_sha256,
    }) + "\n", encoding="utf-8")
    evolved = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo_flow.py"),
            "--data-dir",
            str(data_dir),
            "--verify-fixture",
            str(data_dir / "fixture-manifest.json"),
            "--audit",
            str(audit),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert evolved.returncode == 0, evolved.stdout + evolved.stderr
    assert json.loads(evolved.stdout)["database_sha256"] == evolved_sha256

    database.write_bytes(database.read_bytes() + b"unaudited-change")
    tampered = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo_flow.py"),
            "--data-dir",
            str(data_dir),
            "--verify-fixture",
            str(data_dir / "fixture-manifest.json"),
            "--audit",
            str(audit),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert tampered.returncode != 0
    assert "not the last audited state" in tampered.stderr


@pytest.mark.parametrize(
    ("fixture_id", "expected_sha256", "green_count"),
    [
        ("green-days-actionable-v1", ACTIONABLE_SEED_SHA256, 25),
        ("green-days-marker-only-v1", MARKER_ONLY_SEED_SHA256, 22),
    ],
)
def test_named_explanation_fixtures_are_reproducible(
    tmp_path, fixture_id, expected_sha256, green_count,
):
    data_dir, report = _seed_named_fixture(tmp_path, fixture_id)
    assert report["fixture_id"] == fixture_id
    assert report["day_count"] == 45
    assert report["green_count"] == green_count
    assert report["seed_sha256"] == expected_sha256
    assert report["analysis_runs"] == 0
    assert report["synthesis_runs"] == 0

    verified = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "demo_flow.py"),
            "--data-dir", str(data_dir),
            "--fixture-id", fixture_id,
            "--verify-fixture", str(data_dir / "fixture-manifest.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr


def test_actionable_fixture_keeps_marker_first_but_supports_prior_behavior(
    tmp_path,
):
    data_dir, _report = _seed_named_fixture(
        tmp_path, "green-days-actionable-v1",
    )
    analysis = _fixture_health(
        data_dir,
        "outcome-associations",
        "--outcome", "subjective.day_rating",
        "--mode", "green-vs-non-green",
        "--from", "2026-05-17",
        "--to", "2026-06-30",
        "--top", "30",
    )
    eligible = [
        finding for finding in analysis["findings"]
        if finding["quality"]["eligible_for_hypothesis"]
    ]
    marker = eligible[0]
    assert marker["exposure"]["components"][0] == {
        **marker["exposure"]["components"][0],
        "exposure_key": "wearable.resting_hr_bpm",
        "lag_days": 0,
        "window_days": 1,
        "transform": "point",
        "temporal_direction": "same_day_or_order_unknown",
    }
    assert 0.2 < abs(marker["effect"]["oriented_estimate"]) < 1
    upstream = next(
        finding for finding in eligible
        if finding["exposure"]["components"][0]["exposure_key"]
        == "substance.caffeine_mg"
        and finding["exposure"]["components"][0]["lag_days"] == 1
        and finding["exposure"]["components"][0]["window_days"] == 1
    )
    assert analysis["findings"].index(upstream) == 1
    assert upstream["stability"]["status"] == "stable"
    assert -1 < upstream["effect"]["oriented_estimate"] < -0.2
    assert any(
        item["key"] == "weekend"
        for item in (
            upstream["confounders"]["checked"]
            + upstream["confounders"]["unchecked"]
        )
    )

    registry = _fixture_health(data_dir, "feature-registry")
    definitions = {item["key"]: item for item in registry["features"]}
    assert definitions["wearable.resting_hr_bpm"]["pillar"] == "wearable"
    assert definitions["wearable.resting_hr_bpm"]["actionability"] == "context"
    assert definitions["substance.caffeine_mg"]["pillar"] == "substance"
    assert definitions["substance.caffeine_mg"]["actionability"] == "direct"


def test_marker_only_fixture_has_no_supported_upstream_behavior(tmp_path):
    data_dir, _report = _seed_named_fixture(
        tmp_path, "green-days-marker-only-v1",
    )
    analysis = _fixture_health(
        data_dir,
        "outcome-associations",
        "--outcome", "subjective.day_rating",
        "--mode", "green-vs-non-green",
        "--from", "2026-05-17",
        "--to", "2026-06-30",
        "--top", "30",
    )
    eligible = [
        finding for finding in analysis["findings"]
        if finding["quality"]["eligible_for_hypothesis"]
    ]
    assert eligible
    assert {
        finding["exposure"]["components"][0]["exposure_key"]
        for finding in eligible
    } == {"wearable.resting_hr_bpm"}
    leading = eligible[0]
    assert leading["exposure"]["components"][0]["lag_days"] == 0
    assert leading["stability"]["status"] == "stable"
    assert 0.2 < abs(leading["effect"]["oriented_estimate"]) < 1


def test_complete_fictional_demo_flow(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo_flow.py"),
            "--data-dir",
            str(tmp_path / "hermes-demo"),
        ],
        cwd=ROOT,
        capture_output=True,
        env={**os.environ, "HERMES_TIMEZONE": "UTC"},
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True and report["fictional"] is True
    assert report["external_services_used"] is False
    assert report["initialization"] == {
        "empty_before_import": True,
        "schema_version": 7,
    }
    assert report["synthetic_import"]["days"] == 45
    assert report["synthetic_import"]["validated_day_ratings"] == 45
    assert report["deterministic_intelligence"]["finding_count"] > 0
    assert report["deterministic_intelligence"]["ledger_status"] == "completed"
    assert report["deterministic_intelligence"]["ledger_runs"] > 0
    assert report["ui_api"]["dashboard_status"] == 200
    assert report["ui_api"]["metric_rows"] == 45
    assert report["ui_api"]["subjective_rows"] == 45
