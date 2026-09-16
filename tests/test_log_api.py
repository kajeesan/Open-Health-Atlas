"""Phase 5: the daily-loop write endpoints. A recording stub replaces the bridge
so we assert the exact health.py argv each endpoint produces + input validation."""
import json

import pytest

from app import auth as auth_mod
from app import bridge, create_app
from app.panel_db import get_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    calls = []

    def fake_run(subcmd, *args, **kw):
        calls.append((subcmd, list(args)))
        return {"ok": True}

    monkeypatch.setattr(bridge, "run", fake_run)
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
    })
    c = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    c.set_cookie(auth_mod.SESSION_COOKIE, token)
    c.calls = calls
    return c


def test_endpoints_require_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().post("/api/log/day-rating", json={"value": "green"}).status_code == 401


def test_day_rating(client):
    r = client.post("/api/log/day-rating", json={"value": "green"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert client.calls[-1] == ("day-rating", ["green", "--source", "panel-ui"])


def test_day_rating_rejects_bad_value(client):
    assert client.post("/api/log/day-rating", json={"value": "purple"}).status_code == 400
    assert client.calls == []  # never reached the bridge


def test_vitals_maps_to_health_py(client):
    r = client.post("/api/log/vitals", json={"systolic": 124, "diastolic": 80, "resting_hr": 62})
    assert r.status_code == 200
    assert client.calls[-1] == ("log", ["vitals", "systolic=124", "diastolic=80", "resting_hr=62"])


def test_vitals_hr_optional(client):
    client.post("/api/log/vitals", json={"systolic": 118, "diastolic": 76})
    assert client.calls[-1] == ("log", ["vitals", "systolic=118", "diastolic=76"])


def test_vitals_out_of_range_rejected(client):
    assert client.post("/api/log/vitals", json={"systolic": 400, "diastolic": 80}).status_code == 400
    assert client.post("/api/log/vitals", json={"systolic": 120}).status_code == 400
    assert client.calls == []


def test_vitals_systolic_must_exceed_diastolic(client):
    r = client.post("/api/log/vitals", json={"systolic": 90, "diastolic": 150})
    assert r.status_code == 400 and "higher than diastolic" in r.get_json()["error"]
    assert client.calls == []


def test_vitals_rejects_fractional_not_floors(client):
    # A non-UI JSON client posting 124.7 must be rejected, not silently floored.
    r = client.post("/api/log/vitals", json={"systolic": 124.7, "diastolic": 80})
    assert r.status_code == 400
    assert client.calls == []


def test_vitals_rejects_invalid_supplied_values_without_partial_write(client):
    for field, value in (
        ("resting_hr", "oops"), ("resting_hr", 250), ("resting_hr", 64.5),
        ("systolic", "Infinity"), ("diastolic", "NaN"),
        ("systolic", 10 ** 500),
        ("resting_hr", "-Infinity"), ("time", "25:30"), ("time", "08:10extra"),
    ):
        response = client.post("/api/log/vitals", json={
            "systolic": 120, "diastolic": 80, field: value,
        })
        assert response.status_code == 400, (field, value)
        assert response.get_json()["ok"] is False
    assert client.post("/api/log/vitals", json=[120, 80]).status_code == 400
    assert client.calls == []
    response = client.post("/api/log/vitals", json={
        "systolic": 120, "diastolic": 80, "resting_hr": 64, "time": "08:10",
    })
    assert response.status_code == 200
    assert client.calls == [("log", ["vitals", "systolic=120", "diastolic=80",
                                     "resting_hr=64", "time=08:10"])]


def test_subjective_only_provided_fields(client):
    client.post("/api/log/subjective", json={"focus": 4, "mood": 3})
    assert client.calls[-1] == ("log", ["subjective_daily", "focus=4", "mood=3"])


def test_subjective_rating_range(client):
    assert client.post("/api/log/subjective", json={"focus": 9}).status_code == 400
    assert client.post("/api/log/subjective", json={}).status_code == 400  # nothing to log
    assert client.calls == []


def test_subjective_brain_dump(client):
    client.post("/api/log/subjective", json={"brain_dump": "rough afternoon"})
    assert client.calls[-1] == ("log", ["subjective_daily", "brain_dump=rough afternoon"])


def test_body(client):
    client.post("/api/log/body", json={"weight_kg": 63.4, "waist_cm": 72})
    assert client.calls[-1] == ("log", ["body_metrics", "weight_kg=63.4", "waist_cm=72.0"])


def test_water(client):
    client.post("/api/log/water", json={"water_ml": 1500})
    assert client.calls[-1] == ("log", ["intake", "water_ml=1500.0"])


def test_bridge_error_becomes_400(client, monkeypatch):
    def boom(*a, **k):
        raise bridge.BridgeError("health.py said no")
    monkeypatch.setattr(bridge, "run", boom)
    r = client.post("/api/log/day-rating", json={"value": "red"})
    assert r.status_code == 400 and "health.py said no" in r.get_json()["error"]


def test_recent_feed_reads_panel_audit(client):
    with client.application.app_context():
        db = get_db()
        # Reads must not crowd write attempts out; failures remain explicit.
        for i in range(22):
            db.execute("INSERT INTO audit_log(ts,event,detail) VALUES(?,?,?)", (
                i, "bridge.eat", json.dumps({"args": [f"meal-{i}"], "outcome": "exit:0"}),
            ))
        for i in range(30):
            for event, detail in (
                ("bridge.labs", {"outcome": "exit:0"}),
                ("bridge.outcome-associations", {"outcome": "exit:0"}),
            ):
                db.execute("INSERT INTO audit_log(ts,event,detail) VALUES(?,?,?)", (
                    100 + i, event, json.dumps(detail),
                ))
        for event, detail in (
            ("bridge.eat", {"outcome": "exit:1"}),
            ("bridge.eat", {"outcome": "transport-error:TimeoutError"}),
            ("bridge.prep", {"outcome": "refused-client-flag"}),
            ("bridge.log", {"args": ["legacy-unconfirmed"]}),
        ):
            db.execute("INSERT INTO audit_log(ts,event,detail) VALUES(?,?,?)", (
                150, event, json.dumps(detail),
            ))
        for detail in ("not JSON", "null", "[]"):
            db.execute("INSERT INTO audit_log(ts,event,detail) VALUES(?,?,?)",
                       (200, "bridge.eat", detail))
        db.commit()
    r = client.get("/api/log/recent")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 20
    assert [item["status"] for item in items] == [
        *(["unconfirmed"] * 4), "failed", "unconfirmed", "failed",
        *(["succeeded"] * 13),
    ]
    assert [item["ts"] for item in items[7:]] == list(range(21, 8, -1))
    assert {item["event"] for item in items} <= {"bridge.eat", "bridge.prep", "bridge.log"}


def test_word_maps_to_log_commitment(client):
    r = client.post("/api/log/word", json={"status": "kept"})
    assert r.status_code == 200
    assert client.calls[-1] == ("log-commitment", ["kept", "--source", "panel-ui"])
    r = client.post("/api/log/word", json={"status": "broke", "why": "  late scroll  "})
    assert r.status_code == 200
    assert client.calls[-1] == ("log-commitment", ["broke", "--why", "late scroll",
                                                "--source", "panel-ui"])


def test_word_rejects_bad_status_and_long_why(client):
    assert client.post("/api/log/word", json={"status": "meh"}).status_code == 400
    assert client.post("/api/log/word",
                       json={"status": "kept", "why": "x" * 301}).status_code == 400
    assert client.calls == []


def test_checkin_maps_and_validates(client):
    r = client.post("/api/log/checkin", json={"kind": "energy", "value": 4})
    assert r.status_code == 200
    assert client.calls[-1] == ("checkin", ["energy", "4", "--source", "panel-ui"])
    assert client.post("/api/log/checkin", json={"kind": "vibes", "value": 3}).status_code == 400
    assert client.post("/api/log/checkin", json={"kind": "focus", "value": 6}).status_code == 400
    assert client.post("/api/log/checkin", json={"kind": "focus", "value": 3.5}).status_code == 400
