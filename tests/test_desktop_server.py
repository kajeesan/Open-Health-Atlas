"""Distinct desktop origin/bootstrap boundary; existing app tests own its routes."""
import threading

import pytest
import time
from pathlib import Path

from werkzeug.test import Client
from werkzeug.wrappers import Response

from desktop.server import LocalBoundary, build_app
from desktop.workspaces import WorkspaceManager
from tests.support import csrf_from


def test_launch_session_single_use_and_retained_csrf(tmp_path):
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    app = build_app(manager, None, tmp_path / "socket", "test-launch-token",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    # The first-run page needs the actual theme, not a redirect to setup HTML.
    stylesheet = client.get("/static/css/panel.css")
    assert stylesheet.status_code == 200
    assert stylesheet.mimetype == "text/css"
    assert client.get("/desktop/").status_code == 401
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "wrong"}).status_code == 401
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "test-launch-token"}).status_code == 303
    assert client.get("/desktop/").status_code == 200
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "test-launch-token"}).status_code == 401
    assert client.post("/desktop/api/workspaces", json={"kind": "personal", "timezone": "UTC"}).status_code == 400
    assert client.get("/enroll").status_code == 401


@pytest.mark.parametrize(("route", "payload"), [
    ("workspaces", {"kind": [], "timezone": "UTC"}),
    ("workspaces", {"kind": "unknown", "timezone": "UTC"}),
    ("workspaces", {"kind": "personal", "timezone": []}),
    ("workspaces", {"kind": "personal", "timezone": "not/a-timezone"}),
    ("workspaces", {"kind": "import", "timezone": "UTC", "source_database": {"path": "sample.db"}}),
    ("workspaces", {"kind": "import", "timezone": "UTC", "source_database": ""}),
    ("select", {"id": []}),
])
def test_invalid_workspace_request_keeps_existing_helper_available(tmp_path, route, payload):
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    helper_running = threading.Event()
    helper_running.set()
    restart = threading.Event()
    app = build_app(manager, None, tmp_path / "socket", "validation-launch",
                    restart, "a" * 40, quiesce=helper_running.clear)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "validation-launch"})
    csrf = csrf_from(client.get("/desktop/").text)

    response = client.post(f"/desktop/api/{route}", json=payload,
                           headers={"X-CSRFToken": csrf})

    assert response.status_code == 400
    assert helper_running.is_set()
    assert not restart.is_set()
    assert manager.list_workspaces() == []


@pytest.mark.parametrize(("failure", "status"), [(OSError, 400), (TypeError, 500)])
def test_workspace_failure_after_stopping_helper_requests_recovery(tmp_path, monkeypatch, failure, status):
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    helper_running = threading.Event()
    helper_running.set()
    restart = threading.Event()

    def fail_create(**kwargs):
        raise failure("synthetic setup failure")

    monkeypatch.setattr(manager, "create", fail_create)
    app = build_app(manager, None, tmp_path / "socket", "recovery-launch",
                    restart, "a" * 40, quiesce=helper_running.clear)
    app.config.update(RATELIMIT_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
    client = app.test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "recovery-launch"})
    csrf = csrf_from(client.get("/desktop/").text)

    response = client.post("/desktop/api/workspaces",
                           json={"kind": "personal", "timezone": "UTC"},
                           headers={"X-CSRFToken": csrf})

    assert response.status_code == status
    assert not helper_running.is_set()
    assert restart.wait(5)
    assert "synthetic setup failure" not in response.text


def test_idle_desktop_logout_revokes_session_and_offers_native_reopen(tmp_path, monkeypatch):
    from app import auth
    clock = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    workspace = manager.create("personal", "UTC")
    app = build_app(manager, workspace, tmp_path / "socket", "original-launch",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "original-launch"}).status_code == 303
    token = csrf_from(client.get("/desktop/setup").text)
    old_cookie = client.get_cookie(auth.SESSION_COOKIE).value
    clock[0] += 3601  # Previously, the form expired before its 12-hour session.
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 302
    assert response.headers["Location"] == "/desktop/signed-out"
    page = client.get(response.headers["Location"])
    assert page.status_code == 200
    assert 'id="reopen-workspace"' in page.text
    assert "csrf-token" not in page.text and "original-launch" not in page.text
    assert str(tmp_path) not in page.text
    assert client.get("/desktop/static/session.js").status_code == 200
    assert client.get("/desktop/static/setup.css").status_code == 200
    assert client.get("/desktop/preferences.js").status_code == 401
    client.set_cookie(auth.SESSION_COOKIE, old_cookie)
    assert client.get("/desktop/api/workspaces").status_code == 401
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "original-launch"}).status_code == 401
    # Only a new capability delivered by a new native runtime can re-enter.
    restarted = build_app(manager, workspace, tmp_path / "socket", "fresh-launch",
                          threading.Event(), "a" * 40).test_client()
    assert restarted.post("/desktop/session", headers={"X-OHA-Launch-Token": "original-launch"}).status_code == 401
    assert restarted.post("/desktop/session", headers={"X-OHA-Launch-Token": "fresh-launch"}).status_code == 303
    assert restarted.get("/desktop/setup").status_code == 200


def test_desktop_session_expires_at_fixed_boundary_without_sliding_refresh(tmp_path, monkeypatch):
    from app import auth
    start = int(time.time())
    clock = [start]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    app = build_app(manager, None, tmp_path / "socket", "expiry-launch",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "expiry-launch"})
    token = csrf_from(client.get("/desktop/").text)
    clock[0] = start + auth.SESSION_TTL_SECONDS - 1
    assert client.post("/desktop/api/preferences", json={"key": "panel-theme", "value": "ember"},
                       headers={"X-CSRFToken": token}).status_code == 200
    clock[0] += 1
    assert client.post("/desktop/api/preferences", json={"key": "panel-theme", "value": "paper"},
                       headers={"X-CSRFToken": token}).status_code == 401
    assert client.get("/desktop/api/workspaces", headers={"Accept": "text/html"}).status_code == 401
    for path in ("/desktop/", "/login", "/desktop/help"):
        response = client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 302
        assert response.headers["Location"] == "/desktop/signed-out"
    assert client.get("/desktop/signed-out").status_code == 200
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "expiry-launch"}).status_code == 401


def test_loopback_boundary_rebinding_cross_origin_and_proxy_headers():
    boundary = LocalBoundary(Response("local"))
    boundary.origin = "http://127.0.0.1:8765"
    client = Client(boundary, Response)
    def request(**kwargs):
        return client.get("/", base_url=boundary.origin,
                          environ_overrides={"REMOTE_ADDR": "127.0.0.1", **kwargs.pop("environ", {})}, **kwargs)
    assert request().status_code == 200
    assert request(headers={"Origin": boundary.origin}).status_code == 200
    for headers in ({"Host": "example.invalid"}, {"Origin": "https://example.invalid"},
                    {"X-Forwarded-Proto": "https"}, {"Forwarded": "host=example.invalid"},
                    {"Sec-Fetch-Site": "cross-site"}):
        assert request(headers=headers).status_code == 403
    assert request(environ={"REMOTE_ADDR": "192.0.2.1"}).status_code == 403
    assert request(headers={"Origin": "null"}).status_code == 403
    assert client.post("/desktop/session", base_url=boundary.origin,
                       headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
                       environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_macos_or_linux_peer_identity():
    import os
    import socket
    from desktop.broker import peer_uid
    left, right = socket.socketpair()
    try:
        assert peer_uid(left) == os.getuid()
        assert peer_uid(right) == os.getuid()
    finally:
        left.close()
        right.close()


def test_owned_setup_child_finishes_with_parent_pipe_still_open(tmp_path):
    # A buffered daemon read of stdin used to crash Python at finalization.
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(root / "desktop/child.py"),
         "scripts/init_hermes.py", "--data-dir", str(tmp_path / "data")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "HERMES_CODE_VERSION": "a" * 40}, start_new_session=True,
    )
    try:
        assert process.wait(timeout=30) == 0
        assert (tmp_path / "data/health.db").is_file()
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait()


@pytest.mark.parametrize("route", ["/desktop/help", "/desktop/help?section=full", "/desktop/setup"])
def test_help_and_workspace_back_links_only_target_local_app_pages(tmp_path, route):
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    workspace = manager.create("personal", "UTC")
    app = build_app(manager, workspace, tmp_path / "socket", "navigation-launch",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "navigation-launch"})
    separator = "&" if "?" in route else "?"
    page = client.get(route + separator + "return_to=/training").text
    assert 'class="desktop-back" href="/training"' in page
    refused = client.get(route + separator + "return_to=//outside.invalid").text
    assert 'class="desktop-back" href="/"' in refused
    assert "outside.invalid" not in refused


def test_first_run_help_returns_to_setup_without_an_unopened_dashboard(tmp_path):
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    app = build_app(manager, None, tmp_path / "socket", "setup-launch",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    client.post("/desktop/session", headers={"X-OHA-Launch-Token": "setup-launch"})
    page = client.get("/desktop/help?return_to=/training").text
    assert 'class="desktop-back" href="/desktop/setup"' in page
    assert 'class="desktop-back"' not in client.get("/desktop/setup").text
