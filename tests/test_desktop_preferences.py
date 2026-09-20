import threading
from pathlib import Path

from desktop.server import build_app
from desktop.workspaces import WorkspaceManager


def test_preferences_persist_in_panel_state_and_reject_arbitrary_values(tmp_path):
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    def app():
        value = build_app(manager, None, tmp_path / "sock", "test-launch", threading.Event(), "a" * 40)
        value.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
        return value
    client = app().test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "test-launch"})
    assert client.post("/desktop/api/preferences", json={"key": "panel-theme", "value": "blue-glacier"}).status_code == 200
    assert client.post("/desktop/api/preferences", json={"key": "arbitrary-data", "value": "hello"}).status_code == 400
    assert client.post("/desktop/api/preferences", json={"key": "panel-theme", "value": "invalid"}).status_code == 400
    for lens in ("insight", "pain", "mobility"):
        assert client.post("/desktop/api/preferences", json={"key": f"hermes.{lens}.conversation", "value": f"synthetic-{lens}-thread"}).status_code == 200
    second = app().test_client()
    assert second.get("/desktop/preferences.js").status_code == 401
    second.post("/desktop/session", headers={"X-OHA-Launch-Token": "test-launch"})
    assert '"panel-theme": "blue-glacier"' in second.get("/desktop/preferences.js").text
    for lens in ("insight", "pain", "mobility"):
        assert f'synthetic-{lens}-thread' in second.get("/desktop/preferences.js").text
    assert second.post("/desktop/api/preferences", json={"key": "panel-theme", "value": None}).status_code == 200
    assert '"panel-theme": "blue-glacier"' not in second.get("/desktop/preferences.js").text


def test_retired_saved_theme_falls_back_without_discarding_other_preferences(tmp_path):
    from app.panel_db import get_db
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    app = build_app(manager, None, tmp_path / "sock", "legacy-launch", threading.Event(), "a" * 40)
    app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    client = app.test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "legacy-launch"})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO settings(key,value) VALUES(?,?)", ("desktop-browser:panel-theme", "cosmos"))
        db.execute("INSERT INTO settings(key,value) VALUES(?,?)", ("desktop-browser:hermes.insight.conversation", "fictional-thread"))
        db.commit()
    restored = client.get("/desktop/preferences.js").text
    assert '\"panel-theme\": \"paper\"' in restored
    assert '\"hermes.insight.conversation\": \"fictional-thread\"' in restored
    assert 'data-theme="paper"' in client.get("/desktop/help").text
