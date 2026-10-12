"""Fictional sparse records through the authenticated Explorer read surface."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from toolkit.hermes_insights import migrations, runtime


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def description_database(app, tmp_path, monkeypatch):
    database = tmp_path / "fictional-explorer.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((ROOT / "toolkit/SCHEMA.sql").read_text())
    migrations.migrate(str(database), 3, 0, code_version="c" * 40)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO subjective_daily(date, energy) VALUES(?, ?)",
            [("2026-06-25", 2), ("2026-06-27", 8)],
        )
        connection.executemany(
            "INSERT INTO sleep_log(date, time_asleep_hours) VALUES(?, ?)",
            [("2026-06-24", 5), ("2026-06-26", 9)],
        )
    monkeypatch.setitem(app.config, "HEALTH_DB", str(database))
    monkeypatch.setitem(app.config, "HERMES_TIMEZONE", "Pacific/Auckland")
    monkeypatch.setattr(runtime, "now", lambda **kwargs: datetime(2026, 7, 1, tzinfo=timezone.utc))
    return database


@pytest.fixture
def description_request():
    return {
        "outcome_key": "subjective.energy",
        "exposure_keys": ["sleep.duration_hours", "wearable.steps"],
        "range": {"from": "2026-06-24", "to": "2026-06-30"},
        "lag_days": 1,
    }


def test_sparse_explorer_records_keep_denominators_and_database_unchanged(
    authed, description_database, description_request,
):
    before = hashlib.sha256(description_database.read_bytes()).hexdigest()

    response = authed.post("/api/insights/descriptions", json=description_request)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    result = response.get_json()["result"]
    assert result["status"] == "ok"
    assert result["findings"] == []
    packet = result["facts"][0]
    assert packet["inference_status"] == "not_assessed"
    assert packet["eligible_for_hypothesis"] is False
    assert packet["features"][0]["observed_days"] == 2
    assert packet["features"][0]["days_without_usable_observation"] == 5
    sleep, steps = packet["comparisons"]
    assert sleep["paired_units"] == 2
    assert sleep["eligible_outcome_units"] == 2
    assert sleep["mean_difference"] == 6
    assert steps["paired_units"] == 0
    assert steps["mean_difference"] is None
    assert packet["features"][2]["summary"]["mean"] is None
    assert result["evidence_refs"]
    assert hashlib.sha256(description_database.read_bytes()).hexdigest() == before


def test_catalog_uses_registered_choices_and_rejects_database_selection(
    authed, description_database,
):
    response = authed.get("/api/insights/descriptive-catalog?cursor=0")

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["operation"] == "health_catalog"
    assert result["facts"]
    assert all(item["key"] and item["display_name"] for item in result["facts"])
    assert all(item["implemented"] for item in result["facts"])
    assert response.headers["Cache-Control"] == "no-store"
    rejected = authed.get("/api/insights/descriptive-catalog?database=other.db")
    assert rejected.status_code == 400


def test_partial_meal_is_returned_without_becoming_a_daily_total_or_comparison(
    authed, description_database, description_request,
):
    with sqlite3.connect(description_database) as connection:
        connection.execute(
            "INSERT INTO nutrition_log(date, food_name, kcal, source) VALUES(?, ?, ?, ?)",
            ("2026-06-25", "Fictional lunch", 500, "manual"),
        )
        connection.execute("INSERT INTO subjective_daily(date, energy) VALUES(?, ?)",
                           ("2026-06-26", 5))

    response = authed.post("/api/insights/descriptions", json={
        **description_request, "exposure_keys": ["nutrition.logged.kcal"],
    })

    assert response.status_code == 200
    packet = response.get_json()["result"]["facts"][0]
    nutrition = packet["features"][1]
    assert nutrition["observed_units"] == 0
    assert nutrition["summary"]["mean"] is None
    partial = nutrition["partial_recorded_facts"][0]
    assert partial["value"] == 500
    assert partial["unit"] == "kcal"
    assert partial["value_scope"] == "recorded_subtotal"
    assert partial["completeness"] == "full_day_coverage_unconfirmed"
    assert partial["candidate_eligible"] is False
    assert partial["source_row_ids"]
    assert packet["comparisons"][0]["paired_units"] == 0
    assert packet["comparisons"][0]["missing_exposure_units"] == 3


@pytest.mark.parametrize("changes", [
    {"database": "other.db"},
    {"timezone": "UTC"},
    {"lag_days": True},
    {"lag_days": 4},
    {"exposure_keys": ["sleep.duration_hours", "sleep.duration_hours"]},
    {"exposure_keys": ["subjective.energy"]},
    {"range": {"from": "2025-01-01", "to": "2026-06-30"}},
    {"range": {"kind": "all"}},
])
def test_descriptive_route_rejects_invalid_scope(
    authed, description_database, description_request, changes,
):
    response = authed.post("/api/insights/descriptions", json={**description_request, **changes})

    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_unavailable_database_is_an_error_not_empty_records(authed, app, tmp_path, monkeypatch, description_request):
    monkeypatch.setitem(app.config, "HEALTH_DB", str(tmp_path / "absent.db"))

    response = authed.post("/api/insights/descriptions", json=description_request)

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "descriptive_unavailable"
    assert str(tmp_path) not in response.get_data(as_text=True)


def test_unknown_measurement_is_unsupported_not_zero(authed, description_database, description_request):
    response = authed.post("/api/insights/descriptions", json={
        **description_request, "outcome_key": "unknown.measurement",
    })

    assert response.status_code == 200
    assert response.get_json()["result"]["status"] == "unsupported"
    assert response.get_json()["result"]["facts"] == []


def test_descriptive_reads_require_authentication_and_post_csrf(authed, app, monkeypatch, description_request):
    unsigned = app.test_client()
    assert unsigned.get("/api/insights/descriptive-catalog").status_code == 401
    assert unsigned.post("/api/insights/descriptions", json=description_request).status_code == 401
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    assert authed.post("/api/insights/descriptions", json=description_request).status_code == 400


@pytest.mark.parametrize("scenario", [
    "sparse", "no_pattern", "source_change", "source_groups", "protocol", "units",
    "partial", "race", "errors", "scope",
])
def test_descriptive_browser_behavior(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for Explorer interaction checks")
    subprocess.run([node, "tests/fixtures/descriptions_browser.js", scenario],
                   cwd=ROOT, check=True, capture_output=True, text=True, timeout=20)
