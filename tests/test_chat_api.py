"""Chat API: fixed surface threads, bridge routing, and persistent history."""
import json
import sqlite3

import pytest
from app import auth as auth_mod
from app import bridge, create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    calls = []
    def fake(sub, *a, **k):
        calls.append((sub, a, k.get("stdin")))
        return {"ok": True, "reply": "You slept 6.5h — a bit short."}
    monkeypatch.setattr(bridge, "run", fake)
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES": True})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_send_routes_message_on_stdin(client):
    r = client.post("/api/chat/send", json={"message": "how did I sleep?"})
    assert r.status_code == 200 and "slept" in r.get_json()["reply"]
    subcmd, args, raw = client.calls[-1]
    assert (subcmd, args) == ("hermes-chat", ("--session", "hermes-panel"))
    envelope = json.loads(raw)
    assert envelope == {
        "contract": "hermes-panel-turn-v1",
        "message": "how did I sleep?",
        "context": {"version": 1, "surface": "panel", "lens": "general",
                    "conversation_id": "legacy-general", "range": {"kind": "all"},
                    "selected_region_ids": [], "evidence_contract": "health-tool-v1"},
    }
    assert raw == json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))


def test_history_persists_both_turns(client):
    client.post("/api/chat/send", json={"message": "hello"})
    msgs = client.get("/api/chat/history").get_json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello"


def test_workspace_threads_are_isolated_in_history_and_agent_context(client):
    client.post("/api/chat/send", json={"thread": "pain", "message": "left knee"})
    client.post("/api/chat/send", json={"thread": "mobility", "message": "tight ankle"})
    pain = client.get("/api/chat/history?thread=pain").get_json()["messages"]
    mobility = client.get("/api/chat/history?thread=mobility").get_json()["messages"]
    general = client.get("/api/chat/history").get_json()["messages"]
    assert [m["content"] for m in pain][0] == "left knee"
    assert [m["content"] for m in mobility][0] == "tight ankle"
    assert general == []
    assert client.calls[0][:2] == (
        "hermes-chat", ("--session", "hermes-panel-pain"))
    assert client.calls[1][:2] == (
        "hermes-chat", ("--session", "hermes-panel-mobility"))


def test_unknown_thread_is_rejected_before_bridge(client):
    assert client.post("/api/chat/send", json={
        "thread": "made-up", "message": "hello"}).status_code == 400
    assert client.get("/api/chat/history?thread=made-up").status_code == 400
    assert client.calls == []


def test_empty_and_oversize_rejected(client):
    assert client.post("/api/chat/send", json={"message": "  "}).status_code == 400
    assert client.post("/api/chat/send", json={"message": "x" * 5000}).status_code == 400
    assert client.calls == []


def test_bridge_error_is_502(client, monkeypatch):
    monkeypatch.setattr(bridge, "run", lambda *a, **k: (_ for _ in ()).throw(bridge.BridgeError("agent down")))
    r = client.post("/api/chat/send", json={"message": "hi"})
    assert r.status_code == 502


def test_history_limit_param(client):
    """T51: /insights/conversations needs more than the embedded chat's
    default 50, so `limit` is an optional query param — default unchanged
    (covered by test_history_persists_both_turns above), and out-of-range
    values clamp rather than error (1000 cap, never a raw unbounded read)."""
    for i in range(4):
        client.post("/api/chat/send", json={"message": f"msg {i}"})
    msgs = client.get("/api/chat/history?limit=2").get_json()["messages"]
    assert len(msgs) == 2
    assert msgs[-1]["content"] == "You slept 6.5h — a bit short."  # most recent (assistant reply)
    all_msgs = client.get("/api/chat/history?limit=999999").get_json()["messages"]
    assert len(all_msgs) == 8  # 4 sends × (user + assistant), clamp didn't error
    # Task 61 fix B: explicit `?limit=0` must clamp to the floor of 1, not get
    # swallowed by an `or 50` back to the default (round-5 doc-comment flagged
    # this inconsistency between the code and its own docstring).
    zero_msgs = client.get("/api/chat/history?limit=0").get_json()["messages"]
    assert len(zero_msgs) == 1


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p2.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().post("/api/chat/send", json={"message": "hi"}).status_code == 401


def test_legacy_chat_routes_are_quarantined_without_explicit_test_override(
    tmp_path, monkeypatch,
):
    calls = []
    monkeypatch.setattr(bridge, "run", lambda *args, **kwargs: calls.append(args))
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "quarantined.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    for response in (
        c.get("/api/chat/history"),
        c.post("/api/chat/send", json={"message": "hello"}),
    ):
        assert response.status_code == 410
        assert response.get_json()["error"]["code"] == (
            "legacy_narrative_route_quarantined"
        )
    assert calls == []


def test_legacy_chat_override_cannot_activate_in_normal_process(tmp_path):
    app = create_app({
        "TESTING": False,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "normal.db"),
        "PANEL_COOKIE_SECURE": False,
        "ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES": True,
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    assert c.get("/api/chat/history").status_code == 410


def test_existing_chat_log_migrates_to_general_thread(tmp_path):
    """The deployed panel DB predates `thread`; startup preserves old history
    under General while creating clean Pain/Mobility namespaces."""
    path = tmp_path / "old-panel.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE chat_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL)")
    con.execute("INSERT INTO chat_log(ts, role, content) VALUES(1,'user','old hello')")
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False,
                      "RATELIMIT_ENABLED": False, "PANEL_DB": str(path),
                      "PANEL_COOKIE_SECURE": False})
    with app.app_context():
        from app.panel_db import get_db
        row = get_db().execute(
            "SELECT thread, content FROM chat_log").fetchone()
    assert tuple(row) == ("general", "old hello")
