"""§labs panel view: /api/labs (list + per-test trend) via the stubbed bridge,
and the /labs page shell. The panel is read-only — it calls the `labs` read
command through the bridge and never any writer."""
import pathlib
import sqlite3

import pytest

from app import auth as auth_mod
from app import bridge, create_app

SCHEMA = (pathlib.Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.commit(); con.close()
    calls = []
    monkeypatch.setattr(bridge, "run",
                        lambda sub, *a, **k: (calls.append((sub, list(a))) or {"panels": []}))
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_labs_list_via_bridge(client):
    assert client.get("/api/labs").status_code == 200
    assert client.calls[-1] == ("labs", [])


def test_labs_list_panel_filter(client):
    client.get("/api/labs?panel=Lipids")
    assert client.calls[-1] == ("labs", ["--panel", "Lipids"])


def test_labs_trend_via_bridge(client):
    assert client.get("/api/labs/test/Creatinine").status_code == 200
    assert client.calls[-1] == ("labs", ["--test", "Creatinine"])


def test_labs_page_renders(client):
    r = client.get("/labs")
    assert r.status_code == 200 and b"Labs" in r.data


def test_labs_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p2.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/labs").status_code == 401
