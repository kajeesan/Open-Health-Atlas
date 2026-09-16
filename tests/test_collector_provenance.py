"""Phase 3 collector provenance wrappers: bounded, secret-free and best-effort."""

from datetime import date
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
EXACT_KEYS = {
    "source", "started_at", "completed_at", "status", "coverage_from",
    "coverage_to", "rows_seen", "rows_written", "error_code", "warning_codes",
}


def load(name):
    path = ROOT / "deploy" / name
    loader = importlib.machinery.SourceFileLoader(f"test_{name.replace('-', '_')}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _completed():
    return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")


def test_hevy_success_record_has_exact_keys_and_bounded_complete_coverage(monkeypatch):
    module = load("hevy-collector")
    calls = []
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: calls.append((a, kw)) or _completed())
    state = {
        "started_at": "2026-07-23T01:02:03+00:00", "rows_seen": 9,
        "rows_written": 7, "warning_codes": {"skipped_sets"},
        "coverage_from": "2024-01-02", "coverage_to": "2026-07-22",
    }
    module._record_run(state, "success")

    args, kwargs = calls[0]
    assert args[0][-2:] == ["collector-run-record", "--stdin"]
    payload = json.loads(kwargs["input"])
    assert set(payload) == EXACT_KEYS
    assert payload["source"] == "hevy" and payload["status"] == "success"
    assert payload["rows_seen"] == 9 and payload["rows_written"] == 7
    assert payload["warning_codes"] == ["skipped_sets"] and payload["error_code"] is None
    assert payload["coverage_from"] == "2024-01-02"
    assert payload["coverage_to"] == "2026-07-22"


def test_hevy_coverage_starts_at_earliest_provider_evidence_and_excludes_today():
    module = load("hevy-collector")
    workouts = [
        {"start_time": "2026-07-23T01:00:00+02:00"},
        {"start_time": "2024-01-01T23:30:00Z"},  # Jan 2 in canonical timezone
    ]
    assert module._workout_coverage(workouts, date(2026, 7, 23)) == (
        "2024-01-02", "2026-07-22")


@pytest.mark.parametrize("workouts", [
    [],
    [{"start_time": "not-a-time"}],
    [{"start_time": "2026-07-23T12:00:00+02:00"}],
])
def test_hevy_coverage_never_asserts_pre_provider_or_unlocatable_days(workouts):
    module = load("hevy-collector")
    assert module._workout_coverage(workouts, date(2026, 7, 23)) == (None, None)


def test_google_partial_record_retains_attempted_coverage_without_success(monkeypatch):
    module = load("ghealth-sync")
    calls = []
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: calls.append((a, kw)) or _completed())
    state = {
        "started_at": "2026-07-23T01:02:03+00:00", "rows_seen": 3,
        "rows_written": 2, "coverage_from": "2026-07-21",
        "coverage_to": "2026-07-22", "warning_codes": {"fetch_failed.steps"},
    }
    module._record_run(state, "partial")

    payload = json.loads(calls[0][1]["input"])
    assert set(payload) == EXACT_KEYS
    assert payload["source"] == "google-health" and payload["status"] == "partial"
    assert payload["coverage_from"] == "2026-07-21"
    assert payload["coverage_to"] == "2026-07-22"
    assert payload["warning_codes"] == ["fetch_failed.steps"]


@pytest.mark.parametrize("name", ["hevy-collector", "ghealth-sync"])
def test_recording_failure_is_generic_and_does_not_raise_or_echo_output(name, monkeypatch, capsys):
    module = load(name)
    secret = "https://example.invalid/?token=TOPSECRET"
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout=secret, stderr=secret),
    )
    state = {
        "started_at": "2026-07-23T01:02:03+00:00", "rows_seen": 0,
        "rows_written": 0, "warning_codes": set(),
        "coverage_from": "2025-07-24", "coverage_to": "2026-07-23",
    }
    if name == "ghealth-sync":
        state.update(coverage_from="2026-07-21", coverage_to="2026-07-22")
    module._record_run(state, "success")
    captured = capsys.readouterr()
    assert "provenance was not recorded" in captured.err
    assert "TOPSECRET" not in captured.err and "example.invalid" not in captured.err


def test_hevy_failure_after_committed_import_is_recorded_partial_and_reraised(monkeypatch):
    module = load("hevy-collector")
    state = module._run_state()
    monkeypatch.setattr(module, "_run_state", lambda: state)

    def fail(active):
        active["stage"] = "fetch_body"
        active["imports_completed"] = 3
        raise SystemExit("body API failed with operational detail")

    recorded = []
    monkeypatch.setattr(module, "main", fail)
    monkeypatch.setattr(module, "_record_run", lambda *args: recorded.append(args))
    with pytest.raises(SystemExit, match="body API failed"):
        module.run_collector()
    assert recorded[0][1:] == ("partial", "api_fetch_failed")


@pytest.mark.parametrize("partial,expected", [(False, "success"), (True, "partial")])
def test_hevy_exit_zero_preserves_success_or_partial_status(monkeypatch, partial, expected):
    module = load("hevy-collector")
    state = module._run_state()
    monkeypatch.setattr(module, "_run_state", lambda: state)

    def finish(active):
        active["partial"] = partial

    recorded = []
    monkeypatch.setattr(module, "main", finish)
    monkeypatch.setattr(module, "_record_run", lambda *args: recorded.append(args))
    module.run_collector()
    assert recorded[0][1:] == (expected,)


@pytest.mark.parametrize("partial,expected", [(False, "success"), (True, "partial")])
def test_google_exit_zero_preserves_success_or_partial_status(monkeypatch, partial, expected):
    module = load("ghealth-sync")
    state = module._run_state()
    monkeypatch.setattr(module, "_run_state", lambda: state)

    def finish(active):
        active["partial"] = partial

    recorded = []
    monkeypatch.setattr(module, "main", finish)
    monkeypatch.setattr(module, "_record_run", lambda *args: recorded.append(args))
    module.run_collector()
    assert recorded[0][1:] == (expected,)
