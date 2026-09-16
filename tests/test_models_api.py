"""Phase 9: models endpoints — status/set/budget/fitbit via stubbed bridge."""
import pytest

from app import auth as auth_mod
from app import bridge, create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    calls = []
    def fake(sub, *a, **k):
        calls.append((sub, list(a)))
        if sub == "hermes-status":
            return {"ok": True, "model": "z-ai/glm-5.2", "provider": "openrouter", "gateway_active": True}
        if sub == "hermes-spend":
            return {"ok": True, "usage_usd": 0.87, "usage_weekly_usd": 0.23, "limit_usd": 10}
        return {"ok": True}
    monkeypatch.setattr(bridge, "run", fake)
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_status_bundles_agent_spend_budget(client):
    d = client.get("/api/models/status").get_json()
    assert d["agent"]["model"] == "z-ai/glm-5.2"
    assert d["spend"]["usage_usd"] == 0.87
    assert d["budget_monthly_usd"] is None


def test_set_model_validates_and_maps(client):
    assert client.post("/api/models/set", json={"model": "google/gemini-2.5-pro"}).status_code == 200
    assert client.calls[-1] == ("hermes-model-set", ["google/gemini-2.5-pro"])
    assert client.post("/api/models/set", json={"model": "-rf /"}).status_code == 400
    assert client.post("/api/models/set", json={"model": "bad model!"}).status_code == 400


def test_budget_roundtrip(client):
    assert client.post("/api/models/budget", json={"monthly_usd": 15}).status_code == 200
    assert client.get("/api/models/status").get_json()["budget_monthly_usd"] == "15.0"
    assert client.post("/api/models/budget", json={"monthly_usd": -1}).status_code == 400


def test_fitbit(client):
    assert client.get("/api/models/fitbit").status_code == 200
    assert client.calls[-1][0] == "fitbit-status"


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p2.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().post("/api/models/set", json={"model": "x/y"}).status_code == 401
