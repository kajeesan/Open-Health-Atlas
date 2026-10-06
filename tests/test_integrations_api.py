"""Task-36: Integrations & privacy — /api/integrations/sources. Auth, shape,
and the deterministic connected/stale/none derivations (no new bridge
subcommands: hermes-status + fitbit-status are stubbed exactly like
test_models_api.py; db reads go through a real seeded/empty health.db)."""
import pathlib
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import auth as auth_mod
from app import bridge, canon, create_app
from desktop.server import build_app
from desktop.workspaces import WorkspaceManager

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()

LOCAL_NOW = datetime(2026, 6, 30, 12, tzinfo=ZoneInfo("Europe/Paris"))
LOCAL_TODAY = LOCAL_NOW.date()


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(canon, "now", lambda: LOCAL_NOW)


def _iso(days_ago):
    return (LOCAL_TODAY - timedelta(days=days_ago)).isoformat()


def _make_app(tmp_path, hdb):
    """Caller must monkeypatch bridge.run before calling this."""
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    return client


def _default_agent(sub):
    if sub == "hermes-status":
        return {"ok": True, "model": "z-ai/glm-5.2", "provider": "openrouter", "gateway_active": True}
    if sub == "fitbit-status":
        return {"ok": True, "auth_status": "ok", "reauth_steps": []}
    return {"ok": True}


@pytest.fixture()
def fresh_client(tmp_path, monkeypatch):
    """All four sources have data inside their freshness windows."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps, source) "
                "VALUES(?, 'Squat', 60, 8, 'hevy')", (_iso(2),))
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'apple', 60.0)", (_iso(1),))
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'fitbit', 58.0)", (_iso(2),))
    con.execute("INSERT INTO labs(date, panel, test_name, value, unit) "
                "VALUES(?, 'lipid', 'LDL', 2.5, 'mmol/L')", (_iso(10),))
    con.commit()
    con.close()
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: _default_agent(sub))
    return _make_app(tmp_path, hdb)


@pytest.fixture()
def empty_client(tmp_path, monkeypatch):
    """Schema exists but every relevant table is empty — honest 'none' everywhere."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: _default_agent(sub))
    return _make_app(tmp_path, hdb)


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/integrations/sources").status_code == 401


def test_shape_and_freshness(fresh_client):
    d = fresh_client.get("/api/integrations/sources").get_json()
    assert d["telegram"] == {"status": "connected", "model": "z-ai/glm-5.2",
                              "provider": "openrouter", "error": None}
    assert d["hevy"]["status"] == "connected"
    assert d["hevy"]["latest_date"] == _iso(2)
    assert d["wearable"]["status"] == "connected"
    assert d["wearable"]["latest_date"] == _iso(1)
    assert d["wearable"]["apple_latest"] == _iso(1)
    assert d["wearable"]["fitbit_latest"] == _iso(2)
    assert d["wearable"]["fitbit_auth_status"] == "ok"
    assert d["wearable"]["apple"]["status"] == "connected"
    assert d["wearable"]["apple"]["latest_date"] == _iso(1)
    assert d["wearable"]["fitbit"] == {"status": "connected", "latest_date": _iso(2), "auth_status": "ok"}
    assert d["labs"]["latest_date"] == _iso(10)


def test_stale_data_says_stale(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps, source) "
                "VALUES(?, 'Squat', 60, 8, 'hevy')", (_iso(40),))
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'fitbit', 58.0)", (_iso(10),))
    con.commit()
    con.close()
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: _default_agent(sub))
    client = _make_app(tmp_path, hdb)
    d = client.get("/api/integrations/sources").get_json()
    assert d["hevy"]["status"] == "stale"
    assert d["wearable"]["status"] == "stale"
    assert d["wearable"]["fitbit_latest"] == _iso(10)
    assert d["wearable"]["apple_latest"] is None
    assert d["wearable"]["apple"]["status"] == "none"
    assert d["wearable"]["apple"]["latest_date"] is None


@pytest.mark.parametrize(("records", "apple_status", "fitbit_status", "status", "latest"), [
    pytest.param([("apple", 1)], "connected", "none", "connected", _iso(1), id="apple_only"),
    pytest.param([("fitbit", 3)], "none", "connected", "connected", _iso(3), id="fitbit_cutoff"),
    pytest.param([("apple", 400), ("fitbit", 1)], "stale", "connected", "connected", _iso(1),
                 id="regression_stale_apple_does_not_hide_recent_fitbit"),
    pytest.param([("apple", 1), ("fitbit", 400)], "connected", "stale", "connected", _iso(1),
                 id="regression_recent_apple_is_not_retired"),
    pytest.param([("apple", 4)], "stale", "none", "stale", _iso(4), id="outside_cutoff"),
    pytest.param([], "none", "none", "none", None, id="neither"),
])
def test_wearable_freshness_uses_observed_sources(tmp_path, monkeypatch, records,
                                                apple_status, fitbit_status, status, latest):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, ?, 60.0)",
                    [(_iso(age), source) for source, age in records])
    con.commit()
    con.close()
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: _default_agent(sub))
    client = _make_app(tmp_path, hdb)

    wearable = client.get("/api/integrations/sources").get_json()["wearable"]

    assert wearable["status"] == status
    assert wearable["latest_date"] == latest
    assert wearable["apple"]["status"] == apple_status
    assert wearable["fitbit"]["status"] == fitbit_status


def test_telegram_not_running_and_unknown(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.commit()
    con.close()

    def not_running(sub, *a, **k):
        if sub == "hermes-status":
            return {"ok": True, "gateway_active": False}
        return {"ok": True}
    monkeypatch.setattr(bridge, "run", not_running)
    client = _make_app(tmp_path, hdb)
    d = client.get("/api/integrations/sources").get_json()
    assert d["telegram"]["status"] == "not_running"

    def erroring(sub, *a, **k):
        raise bridge.BridgeError("write bridge unavailable")
    monkeypatch.setattr(bridge, "run", erroring)
    client2 = _make_app(tmp_path, hdb)
    d2 = client2.get("/api/integrations/sources").get_json()
    assert d2["telegram"]["status"] == "unknown"
    assert d2["telegram"]["error"] == "write bridge unavailable"


def test_empty_db_is_honest_not_fabricated(empty_client):
    d = empty_client.get("/api/integrations/sources").get_json()
    assert d["hevy"] == {"status": "none", "latest_date": None}
    assert d["wearable"]["status"] == "none"
    assert d["wearable"]["apple_latest"] is None
    assert d["wearable"]["fitbit_latest"] is None
    assert d["wearable"]["apple"]["status"] == "none"
    assert d["wearable"]["apple"]["latest_date"] is None
    assert d["labs"]["latest_date"] is None


def test_health_db_unavailable(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    assert client.get("/api/integrations/sources").status_code == 503


def test_web_privacy_describes_passkey_signin(authed):
    response = authed.get("/integrations")

    assert response.status_code == 200
    assert "Web deployments use passkey (WebAuthn) sign-in" in response.text
    assert "The desktop app opens a local session" not in response.text


def test_desktop_privacy_describes_native_session(tmp_path):
    manager = WorkspaceManager(tmp_path, ROOT, "a" * 40)
    workspace = manager.create(kind="personal", timezone="UTC")
    app = build_app(manager, workspace, tmp_path / "socket", "privacy-launch",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "privacy-launch"})

    response = client.get("/integrations")

    assert response.status_code == 200
    assert "The desktop app opens a local session" in response.text
    assert "It does not require a passkey" in response.text
    assert "Web deployments use passkey" not in response.text


def test_privacy_discloses_conditional_providers_and_deployment_protection(authed):
    response = authed.get("/integrations")
    text = " ".join(response.text.split())

    assert "external Hermes or an MCP-compatible AI client" in text
    assert "chosen model provider" in text
    assert "If you use Telegram" in text
    assert "read-only database connection" in text
    assert "read-only mount is an additional deployment protection; it is not guaranteed here" in text
    assert "currently OpenRouter" not in text
    assert "nothing else goes out" not in text
