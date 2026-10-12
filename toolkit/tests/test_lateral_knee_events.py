"""Fictional CLI capture through registered lateral-knee evidence and readiness."""

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_insights import events, runtime
from hermes_insights.adapters import pain
from hermes_insights.contracts import DateRange
from hermes_insights.readiness import build_readiness
from hermes_insights.registry import build_registry


def run_cli(database, *arguments, expected=0):
    result = subprocess.run(
        [sys.executable, str(ROOT / "health.py"), *arguments],
        env={**os.environ, "HEALTH_DB": str(database), "HERMES_TIMEZONE": "UTC"},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == expected, result.stderr or result.stdout
    return json.loads(result.stdout) if expected == 0 else result.stderr


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "fictional-knee.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / "SCHEMA.sql").read_text())
    run_cli(path, "migrate", "--to", "3", "--expected-from", "0")
    return path


def frame(database, start="2026-07-01", end="2026-07-31"):
    return run_cli(
        database, "feature-frame", "--family", "pain", "--from", start,
        "--to", end, "--include-provenance",
    )


@pytest.mark.parametrize(("side", "precision"), [
    ("left", "approximate"), ("right", "exact"), ("central", "approximate"),
])
def test_lateral_knee_cli_emits_its_four_registered_features(database, side, precision):
    run_cli(database, "pain-log", "lateral-knee", "--side", side,
            "--date", "2026-07-03", "--intensity", "4", "--source", "manual",
            "--reported-onset-date", "2026-07-01", "--onset-precision", precision)
    run_cli(database, "pain-log", "lateral-knee", "--side", side,
            "--date", "2026-07-06", "--intensity", "0", "--source", "manual")

    payload = frame(database)
    observations = [item for item in payload["observations"]
                    if item["state"] == "observed"]
    prefix = f"pain.nrs.lateral-knee.{side}"
    actual = {(item["feature_key"], item["observed_at"][:10]): item["value"]
              for item in observations}

    assert actual == {
        (prefix, "2026-07-03"): 4,
        (prefix, "2026-07-06"): 0,
        (f"{prefix}.reported_onset", "2026-07-01"): 1,
        (f"{prefix}.first_observed_positive", "2026-07-03"): 1,
        (f"{prefix}.resolution_observed", "2026-07-06"): 1,
    }
    onset = next(item for item in observations
                 if item["feature_key"] == f"{prefix}.reported_onset")
    assert onset["source"] == "manual"
    assert onset["provenance"]["natural_key"] == "pain_log:1"
    assert onset["provenance"]["onset_precision"] == precision
    assert onset["provenance"]["observation_date"] == "2026-07-03"


def test_unknown_onset_does_not_become_observed_onset(database):
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-03", "--intensity", "4", "--onset-precision", "unknown")

    payload = frame(database, "2026-07-01", "2026-07-05")
    observed = [item for item in payload["observations"]
                if item["state"] == "observed"]

    assert {item["feature_key"] for item in observed} == {
        "pain.nrs.lateral-knee.left",
        "pain.nrs.lateral-knee.left.first_observed_positive",
    }
    assert payload["feature_states"]["pain.nrs.lateral-knee.left.reported_onset"] == "missing"
    assert payload["feature_states"]["pain.nrs.lateral-knee.left.resolution_observed"] == "missing"


def test_gap_does_not_close_pain_or_restart_lifetime_first_positive(database):
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-06-01", "--intensity", "4")
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-10", "--intensity", "3")
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-11", "--intensity", "0")
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-12", "--intensity", "5")

    payload = frame(database)
    observed = [item for item in payload["observations"]
                if item["state"] == "observed"]
    markers = [(item["feature_key"], item["observed_at"][:10]) for item in observed
               if item["feature_key"].endswith(("first_observed_positive", "resolution_observed"))]

    assert markers == [("pain.nrs.lateral-knee.left.resolution_observed", "2026-07-11")]
    assert {item["observed_at"][:10] for item in observed} == {
        "2026-07-10", "2026-07-11", "2026-07-12",
    }


def test_same_day_correction_removes_phantom_positive_and_onset(database):
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-03", "--intensity", "8",
            "--reported-onset-date", "2026-07-01", "--onset-precision", "exact")
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-03", "--intensity", "0")
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-05", "--intensity", "3")

    observations = [item for item in frame(database)["observations"]
                    if item["state"] == "observed"]
    prefix = "pain.nrs.lateral-knee.left"

    assert {(item["feature_key"], item["observed_at"][:10]): item["value"]
            for item in observations} == {
        (prefix, "2026-07-03"): 0,
        (prefix, "2026-07-05"): 3,
        (f"{prefix}.first_observed_positive", "2026-07-05"): 1,
    }


def test_voided_resolution_leaves_later_observed_resolution_valid(database):
    run_cli(database, "pain-log", "lateral-knee", "--side", "right",
            "--date", "2026-07-03", "--intensity", "4")
    run_cli(database, "pain-log", "lateral-knee", "--side", "right",
            "--date", "2026-07-04", "--intensity", "0")
    run_cli(database, "physio-void", "--kind", "pain", "2", "--reason", "mistaken date")
    run_cli(database, "pain-log", "lateral-knee", "--side", "right",
            "--date", "2026-07-08", "--intensity", "0")

    observations = frame(database)["observations"]
    resolved = [item for item in observations if item["state"] == "observed"
                and item["feature_key"].endswith("resolution_observed")]

    assert [item["observed_at"][:10] for item in resolved] == ["2026-07-08"]
    assert resolved[0]["provenance"]["natural_key"] == "pain_log:3"


def test_completeness_marks_only_exact_scope_event_absence(database):
    entity = events.pain_identity_key("lateral-knee", "left")
    run_cli(database, "capture-completeness-set", "--date", "2026-07-02",
            "--scope", "pain", "--entity-key", entity,
            "--state", "complete", "--source", "manual", "--explicit-none")
    run_cli(database, "capture-completeness-set", "--date", "2026-07-03",
            "--scope", "pain", "--state", "complete", "--source", "manual")

    payload = frame(database, "2026-07-01", "2026-07-03")
    present = [item for item in payload["observations"]
               if item["state"] in {"observed", "structural_zero"}]

    assert {(item["feature_key"], item["observed_at"][:10], item["state"], item["value"])
            for item in present} == {
        (f"pain.nrs.lateral-knee.left.{marker}", "2026-07-02", "structural_zero", 0)
        for marker in ("reported_onset", "first_observed_positive", "resolution_observed")
    }
    assert payload["feature_states"]["pain.nrs.lateral-knee.left"] == "missing"


@pytest.mark.parametrize(("arguments", "message"), [
    (("lateral-knee", "--side", "bilateral"), "--side"),
    (("outer-knee", "--side", "left"), "unknown pain region"),
    (("lateral-knee", "--reported-onset-date", "2026-07-04", "--onset-precision", "exact"),
     "cannot follow"),
    (("lateral-knee", "--reported-onset-date", "2026-07-01", "--onset-precision", "unknown"),
     "requires --onset-precision"),
    (("lateral-knee", "--onset-precision", "exact"), "requires --reported-onset-date"),
])
def test_unsupported_lateral_knee_capture_is_rejected(database, arguments, message):
    error = run_cli(database, "pain-log", *arguments, "--date", "2026-07-03",
                    "--intensity", "4", expected=1)
    assert message in error
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pain_log").fetchone()[0] == 0


def test_sparse_lateral_knee_readiness_uses_existing_observation_gate(database):
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-03", "--intensity", "4")
    context = runtime.adapter_context(
        clock=lambda: datetime(2026, 7, 10, tzinfo=timezone.utc), timezone="UTC",
    )
    with runtime.connect_read_only(database) as connection:
        definitions = [item for item in build_registry(connection, context)
                       if item.key.startswith("pain.nrs.lateral-knee.")]
        payload = build_readiness(
            connection, definitions, DateRange(date(2026, 7, 1), date(2026, 7, 10)), context,
        )

    states = {item["feature_key"]: item["state"] for item in payload["features"]}
    severity = next(item for item in payload["features"]
                    if item["feature_key"] == "pain.nrs.lateral-knee.left")
    assert states["pain.nrs.lateral-knee.left"] == "too_sparse_for_analysis"
    assert states["pain.nrs.lateral-knee.right"] == "implemented_never_logged"
    assert "logic_not_implemented" not in states.values()
    assert severity["observations"] == 1
    assert severity["aligned_n"] == 1
    assert severity["eligible_units"] == 10
    assert severity["missing_rate"] == 0.9


def test_lateral_knee_goal_uses_existing_pain_reduction_contract(database):
    outcome = "pain.nrs.lateral-knee.left"

    result = run_cli(
        database, "goal-set", "pain_reduction", "--enabled", "1", "--priority", "2",
        "--outcome", outcome, "--direction", "decrease", "--source", "manual",
    )

    assert result["ok"] is True
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT outcome_key,target_direction FROM insight_goal_revisions WHERE goal_key='pain_reduction'"
        ).fetchone()
    assert row == (outcome, "decrease")


def test_lateral_knee_association_remains_below_the_formal_gate(database):
    run_cli(database, "pain-log", "lateral-knee", "--side", "left",
            "--date", "2026-07-03", "--intensity", "4")

    result = run_cli(
        database, "outcome-associations", "--outcome", "pain.nrs.lateral-knee.left",
        "--from", "2026-07-01", "--to", "2026-07-10",
    )

    assert result["meta"]["min_n"] == 30
    assert result["coverage"]["outcome_eligible_n"] == 1
    assert result["findings"] == []


def test_adapter_ignores_unsupported_location_or_side_from_legacy_rows(database):
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO pain_log(date,region,side,intensity,source) VALUES(?,?,?,?,?)",
            [
                ("2026-07-03", "lateral-knee", "bilateral", 4, "manual"),
                ("2026-07-03", "outer-knee", "left", 4, "manual"),
            ],
        )
    context = runtime.adapter_context(
        clock=lambda: datetime(2026, 7, 10, tzinfo=timezone.utc), timezone="UTC",
    )
    with runtime.connect_read_only(database) as connection:
        definitions = build_registry(connection, context, family="pain")
        result = pain.load(connection, definitions,
                           DateRange(date(2026, 7, 1), date(2026, 7, 10)), context)

    assert result == []
