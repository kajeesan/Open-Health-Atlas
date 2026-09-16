"""Task-36: Integrations & privacy — /api/integrations/sources. Auth, shape,
and the deterministic connected/stale/none derivations (no new bridge
subcommands: hermes-status + fitbit-status are stubbed exactly like
test_models_api.py; db reads go through a real seeded/empty health.db)."""
import pathlib
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import auth as auth_mod
from app import bridge, create_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()

# Same canonical-day anchor as test_dash_api.py: routes compare against the
# canonical timezone day (app.canon), not SQLite's UTC date('now').
LOCAL_TODAY = datetime.now(ZoneInfo("Europe/Paris")).date()


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
    # Wearable status/date now derive from fitbit ONLY (the live source) —
    # apple's date no longer participates even though it exists and is newer.
    assert d["wearable"]["status"] == "connected"
    assert d["wearable"]["latest_date"] == _iso(2)
    assert d["wearable"]["apple_latest"] == _iso(1)
    assert d["wearable"]["fitbit_latest"] == _iso(2)
    assert d["wearable"]["fitbit_auth_status"] == "ok"
    assert d["wearable"]["apple"]["status"] == "retired"
    assert d["wearable"]["apple"]["latest_date"] == _iso(1)
    assert d["wearable"]["apple"]["since"] == "2026-05-31"
    assert "2026-05-31" in d["wearable"]["apple"]["note"]
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
    assert d["wearable"]["apple"]["status"] == "retired"
    assert d["wearable"]["apple"]["latest_date"] is None


def test_apple_stale_forever_does_not_drag_wearable_when_fitbit_fresh(tmp_path, monkeypatch):
    """Core regression: the retired apple source is frozen far in the past,
    but fitbit (the live pipeline) is fresh — wearable.status must read
    'connected' off fitbit alone, never 'stale' because of apple."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'apple', 60.0)", (_iso(400),))
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'fitbit', 58.0)", (_iso(1),))
    con.commit()
    con.close()
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: _default_agent(sub))
    client = _make_app(tmp_path, hdb)
    d = client.get("/api/integrations/sources").get_json()
    assert d["wearable"]["status"] == "connected"
    assert d["wearable"]["latest_date"] == _iso(1)
    assert d["wearable"]["apple_latest"] == _iso(400)
    assert d["wearable"]["apple"]["status"] == "retired"
    assert d["wearable"]["apple"]["latest_date"] == _iso(400)


def test_fitbit_absent_apple_present_wearable_is_none(tmp_path, monkeypatch):
    """Apple rows existing alone must not fabricate a wearable status — a
    retired source's dates say nothing about the live pipeline."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr) VALUES(?, 'apple', 60.0)", (_iso(1),))
    con.commit()
    con.close()
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: _default_agent(sub))
    client = _make_app(tmp_path, hdb)
    d = client.get("/api/integrations/sources").get_json()
    assert d["wearable"]["status"] == "none"
    assert d["wearable"]["latest_date"] is None
    assert d["wearable"]["apple_latest"] == _iso(1)
    assert d["wearable"]["apple"]["status"] == "retired"
    assert d["wearable"]["apple"]["latest_date"] == _iso(1)


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
    assert d["wearable"]["apple"]["status"] == "retired"
    assert d["wearable"]["apple"]["latest_date"] is None
    assert d["labs"]["latest_date"] is None


def test_health_db_unavailable(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    assert client.get("/api/integrations/sources").status_code == 503
