"""Distinct desktop origin/bootstrap boundary; existing app tests own its routes."""
import threading
from pathlib import Path

from werkzeug.test import Client
from werkzeug.wrappers import Response

from desktop.server import LocalBoundary, build_app
from desktop.workspaces import WorkspaceManager


def test_launch_session_single_use_and_retained_csrf(tmp_path):
    manager = WorkspaceManager(tmp_path, Path(__file__).resolve().parents[1], "a" * 40)
    app = build_app(manager, None, tmp_path / "socket", "test-launch-token",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    assert client.get("/desktop/").status_code == 401
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "wrong"}).status_code == 401
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "test-launch-token"}).status_code == 303
    assert client.get("/desktop/").status_code == 200
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "test-launch-token"}).status_code == 401
    assert client.post("/desktop/api/workspaces", json={"kind": "personal", "timezone": "UTC"}).status_code == 400
    assert client.get("/enroll").status_code == 401


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
