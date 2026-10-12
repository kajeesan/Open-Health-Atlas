"""Profile editing uses the authenticated, CSRF-protected broker boundary."""

from pathlib import Path
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from app import bridge, canon
from tests.support import csrf_from
from toolkit.hermes_insights.command_context import CommandContext
from toolkit.hermes_insights.commands.nutrition import nutrition_profile_set


@pytest.mark.parametrize("scenario", [
    "profile_save", "profile_error", "profile_load_error", "weight_save",
    "profile_weight_serialized", "weight_profile_serialized", "stale_initial_targets",
    "stale_initial_profile",
])
def test_browser_profile_editor_workflows(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the profile editor browser contract")
    result = subprocess.run(
        [node, "tests/fixtures/nutrition_browser.js", scenario],
        cwd=Path(__file__).parents[1], check=True, capture_output=True, text=True, timeout=20,
    )
    assert result.stdout.strip() == f"PASS {scenario}"


@pytest.fixture()
def profile_db(app, tmp_path, monkeypatch):
    path = tmp_path / "fictional-selected-health.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((Path(__file__).parents[1] / "toolkit" / "SCHEMA.sql").read_text())
    app.config["HEALTH_DB"] = str(path)
    monkeypatch.setattr(canon, "today_iso", lambda: "2026-01-15")
    return path


@pytest.fixture()
def writer_boundary(app, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def write(command, *args):
        if command != "nutrition-profile-set":
            raise AssertionError("unexpected broker command")
        flags = dict(zip(args[::2], args[1::2]))
        context = CommandContext(
            database=app.config["HEALTH_DB"], timezone="Europe/Paris",
            clock=lambda: datetime(2026, 1, 15, 12, tzinfo=ZoneInfo("Europe/Paris")),
            vault="unused", cli_path="unused",
        )
        return nutrition_profile_set(context, SimpleNamespace(
            height_cm=flags["--height-cm"], sex=flags["--sex"], dob=flags["--dob"]))

    monkeypatch.setattr(bridge, "run", write)


def test_profile_get_returns_missing_values_without_inventing_defaults(authed, profile_db):
    response = authed.get("/api/nutrition/profile")

    assert response.status_code == 200
    assert response.get_json()["result"] == {
        "profile": {"height_cm": None, "sex": None, "dob": None},
        "weight": None, "canonical_today": "2026-01-15",
    }


def test_profile_get_uses_the_selected_workspace_and_latest_eligible_weight(authed, profile_db):
    with sqlite3.connect(profile_db) as connection:
        connection.execute("INSERT INTO owner_profile(key,value,updated_at) VALUES('height_cm','168','2026-01-01')")
        connection.executemany(
            "INSERT INTO body_metrics(date,weight_kg) VALUES(?,?)",
            [("2026-01-15", 60), ("2026-01-15", 61), ("2026-01-16", 70)],
        )

    response = authed.get("/api/nutrition/profile")

    assert response.get_json()["result"]["profile"]["height_cm"] == "168"
    assert response.get_json()["result"]["weight"] == {"weight_kg": 61, "date": "2026-01-15"}


def test_authenticated_csrf_profile_save_persists_values(app, authed, profile_db, writer_boundary):
    app.config["WTF_CSRF_ENABLED"] = True
    token = csrf_from(authed.get("/nutrition").get_data(as_text=True))

    response = authed.post("/api/nutrition/profile", json={
        "height_cm": "168.5", "sex": "female", "dob": "2001-01-15",
    }, headers={"X-CSRFToken": token})

    assert response.status_code == 200
    assert response.get_json()["result"]["profile"]["height_cm"] == 168.5
    assert authed.get("/api/nutrition/profile").get_json()["result"]["profile"] == {
        "height_cm": "168.5", "sex": "female", "dob": "2001-01-15",
    }


@pytest.mark.parametrize("method", ["get", "post"])
def test_profile_requires_authentication(client, profile_db, method):
    response = getattr(client, method)("/api/nutrition/profile")
    assert response.status_code == 401


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_profile_save_requires_csrf(app, authed, profile_db, token):
    app.config["WTF_CSRF_ENABLED"] = True
    response = authed.post("/api/nutrition/profile", json={
        "height_cm": 165, "sex": "female", "dob": "2001-01-15",
    }, headers={} if token is None else {"X-CSRFToken": token})

    assert response.status_code == 400
    with sqlite3.connect(profile_db) as connection:
        assert connection.execute("SELECT key FROM owner_profile").fetchall() == []


@pytest.mark.parametrize("payload", [
    None, [], "invalid", {},
    {"height_cm": 165, "sex": "female"},
    {"height_cm": 165, "sex": "female", "dob": "2001-01-15", "activity_fallback": "active"},
    {"height_cm": 165, "sex": "female", "dob": "2001-01-15", "database": "another.db"},
    {"height_cm": True, "sex": "female", "dob": "2001-01-15"},
    {"height_cm": "Infinity", "sex": "female", "dob": "2001-01-15"},
    {"height_cm": 165, "sex": [], "dob": "2001-01-15"},
    {"height_cm": 165, "sex": "female", "dob": "20010115"},
])
def test_profile_rejects_invalid_json_fields_without_writing(authed, profile_db, payload):
    response = authed.post("/api/nutrition/profile", json=payload)

    assert response.status_code == 400
    assert response.get_json()["error_code"] == "invalid_nutrition_profile"
    with sqlite3.connect(profile_db) as connection:
        assert connection.execute("SELECT key FROM owner_profile").fetchall() == []


def test_broker_failure_is_visible_without_claiming_saved_values(authed, profile_db, monkeypatch):
    def unavailable(*args):
        raise bridge.BridgeError("fictional write broker unavailable")
    monkeypatch.setattr(bridge, "run", unavailable)

    response = authed.post("/api/nutrition/profile", json={
        "height_cm": 165, "sex": "female", "dob": "2001-01-15",
    })

    assert response.status_code == 400
    assert response.get_json()["ok"] is False
    assert "broker unavailable" in response.get_json()["error"]
