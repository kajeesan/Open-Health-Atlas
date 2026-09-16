"""Phase 3 weather/air provenance classification without live collection."""

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import health  # noqa: E402


EXACT_KEYS = {
    "source", "started_at", "completed_at", "status", "coverage_from",
    "coverage_to", "rows_seen", "rows_written", "error_code", "warning_codes",
}


def _args():
    return SimpleNamespace(
        date="2026-07-22", lat="55.6761", lon="12.5683", location="canonical timezone",
    )


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(
        health, "_now", lambda: datetime(2026, 7, 22, 4, 5, tzinfo=timezone.utc))


@pytest.mark.parametrize("command,impl_name,source", [
    (health.fetch_weather, "_fetch_weather_impl", "weather"),
    (health.fetch_air, "_fetch_air_impl", "air"),
])
def test_direct_collector_success_is_recorded_without_changing_result(
    monkeypatch, command, impl_name, source,
):
    response = {"ok": True, "date": "2026-07-22", "collected_fields": 9}
    monkeypatch.setattr(health, impl_name, lambda _args: (response, []))
    recorded = []
    monkeypatch.setattr(
        health, "_best_effort_collector_record",
        lambda payload: recorded.append(payload) or False,  # recorder failure is harmless
    )

    assert command(_args()) is response
    payload = recorded[0]
    assert set(payload) == EXACT_KEYS
    assert payload["source"] == source and payload["status"] == "success"
    assert payload["coverage_from"] == payload["coverage_to"] == "2026-07-22"
    assert payload["rows_seen"] == payload["rows_written"] == 1
    assert payload["error_code"] is None and payload["warning_codes"] == []


@pytest.mark.parametrize("command,impl_name,source", [
    (health.fetch_weather, "_fetch_weather_impl", "weather"),
    (health.fetch_air, "_fetch_air_impl", "air"),
])
def test_direct_collector_missing_provider_fields_are_partial(
    monkeypatch, command, impl_name, source,
):
    response = {"ok": True, "date": "2026-07-22", "collected_fields": 9}
    monkeypatch.setattr(
        health, impl_name, lambda _args: (response, ["provider_field"]),
    )
    recorded = []
    monkeypatch.setattr(
        health, "_best_effort_collector_record",
        lambda payload: recorded.append(payload) or True,
    )

    assert command(_args()) is response
    payload = recorded[0]
    assert payload["source"] == source and payload["status"] == "partial"
    assert payload["warning_codes"] == ["missing_provider_fields"]
    assert payload["error_code"] is None


@pytest.mark.parametrize("command,impl_name,source", [
    (health.fetch_weather, "_fetch_weather_impl", "weather"),
    (health.fetch_air, "_fetch_air_impl", "air"),
])
def test_direct_collector_failure_persists_only_a_bounded_code(
    monkeypatch, command, impl_name, source,
):
    secret_detail = "https://provider.invalid/?token=TOPSECRET"

    def fail(_args):
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(health, impl_name, fail)
    recorded = []
    monkeypatch.setattr(
        health, "_best_effort_collector_record",
        lambda payload: recorded.append(payload) or True,
    )

    with pytest.raises(RuntimeError, match="TOPSECRET"):
        command(_args())
    payload = recorded[0]
    assert payload["source"] == source and payload["status"] == "failed"
    assert payload["error_code"] == "collection_failed"
    assert payload["rows_seen"] == payload["rows_written"] == 0
    assert "TOPSECRET" not in json.dumps(payload)
    assert "provider.invalid" not in json.dumps(payload)


def test_missing_provider_fields_checks_expected_values_only():
    assert health._missing_provider_fields(
        {"present": 0, "missing": None, "unrelated": None}, {"present", "missing"},
    ) == ["missing"]
