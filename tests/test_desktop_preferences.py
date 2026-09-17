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
    assert client.post("/desktop/api/preferences", json={"key": "panel-theme", "value": "alpine"}).status_code == 200
    assert client.post("/desktop/api/preferences", json={"key": "arbitrary-data", "value": "hello"}).status_code == 400
    assert client.post("/desktop/api/preferences", json={"key": "panel-theme", "value": "invalid"}).status_code == 400
    second = app().test_client()
    assert second.get("/desktop/preferences.js").status_code == 401
    second.post("/desktop/session", headers={"X-OHA-Launch-Token": "test-launch"})
    assert '"panel-theme": "alpine"' in second.get("/desktop/preferences.js").text
    assert second.post("/desktop/api/preferences", json={"key": "panel-theme", "value": None}).status_code == 200
    assert '"panel-theme": "alpine"' not in second.get("/desktop/preferences.js").text
