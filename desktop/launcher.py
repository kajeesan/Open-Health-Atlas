"""Bundled local runtime supervisor. Native stdin owns the entire lifetime."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "toolkit")]


def source_version():
    metadata = ROOT / "desktop" / "build-info.json"
    if metadata.exists():
        value = json.loads(metadata.read_text())["source_commit"]
    else:
        git = "/Library/Developer/CommandLineTools/usr/bin/git" if sys.platform == "darwin" else "git"
        value = subprocess.check_output([git, "-C", str(ROOT), "rev-parse", "HEAD"],
                                        text=True, stderr=subprocess.DEVNULL).strip()
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Invalid source identity")
    return value


def clean_environment():
    # No inherited Hermes/database/provider or Python module configuration.
    keep = {key: os.environ[key] for key in ("HOME", "TMPDIR", "LANG") if key in os.environ}
    keep.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", PYTHONDONTWRITEBYTECODE="1",
                PYTHONUTF8="1", PYTHONNOUSERSITE="1", HERMES_TIMEZONE="UTC")
    os.environ.clear()
    os.environ.update(keep)


class Broker:
    def __init__(self, environment, path):
        self.environment, self.path = environment, path
        self.child = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            self.child = subprocess.Popen(
                [sys.executable, "-I", "-B", str(ROOT / "desktop/broker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=ROOT, env=self.environment, start_new_session=True,
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.child.poll() is not None:
                    raise RuntimeError("Local data service did not start")
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.settimeout(0.2)
                    probe.connect(str(self.path))
                    return
                except OSError:
                    time.sleep(0.05)
                finally:
                    probe.close()
            raise RuntimeError("Local data service timed out")

    def stop(self):
        with self.lock:
            child, self.child = self.child, None
            if child is None:
                return
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # Descendants can survive the broker's exit; reap the owned group.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            child.stdin.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    version = source_version()
    clean_environment()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)  # No raw application errors/health values in native logs.
    from desktop.workspaces import WorkspaceManager, WorkspaceError, default_data_root
    data_root = (args.data_root or default_data_root()).expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    data_root.chmod(0o700)
    lock_file = (data_root / "desktop.lock").open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"error": "Open Health Atlas is already using this workspace."}), flush=True)
        return 2
    stopped, restart = threading.Event(), threading.Event()

    def parent_lifetime():
        try:
            while os.read(sys.stdin.fileno(), 1):
                pass
        finally:
            stopped.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    threading.Thread(target=parent_lifetime, daemon=True).start()

    def run_script(script, arguments, environment):
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(
                [sys.executable, "-I", "-B", str(ROOT / "desktop/child.py"), script, *arguments],
                stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL,
                cwd=ROOT, env={**os.environ, **environment, "HERMES_CODE_VERSION": version},
                start_new_session=True,
            )
            deadline = time.monotonic() + 240
            try:
                while child.poll() is None:
                    if stopped.wait(0.05) or time.monotonic() > deadline:
                        raise RuntimeError("Setup interrupted")
                if child.returncode:
                    raise RuntimeError("Setup did not finish")
                output.seek(0)
                value = json.loads(output.read(1048576).decode().strip().splitlines()[-1])
                if not isinstance(value, dict) or value.get("ok") is False:
                    raise RuntimeError("Setup did not finish")
                return value
            finally:
                child.stdin.close()
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()

    manager = WorkspaceManager(data_root, ROOT, version, run_script=run_script)
    startup_message = None
    workspace = None
    try:
        workspace = manager.current()
        if workspace:
            os.environ.update(workspace.environment())
            workspace = manager.prepare(workspace)
            os.environ.update(workspace.environment())
    except WorkspaceError as error:
        workspace = None
        startup_message = str(error)
        os.environ["HERMES_TIMEZONE"] = "UTC"
    if workspace is None:
        os.environ.update(HERMES_DATA_DIR=str(data_root), HEALTH_DB=str(data_root / "unselected.db"),
                          HEALTH_VAULT=str(data_root / "unused-vault"))
    os.environ["HERMES_CODE_VERSION"] = version
    runtime = Path(tempfile.mkdtemp(prefix="oha-", dir="/tmp"))
    runtime.chmod(0o700)
    socket_path = runtime / "bridge.sock"
    broker = Broker({**os.environ, "BRIDGE_HEALTH": str(ROOT / "toolkit/health.py"),
                     "BRIDGE_HERMESCTL": str(ROOT / "deploy/hermesctl"),
                     "BRIDGE_SOCK": str(socket_path), "BRIDGE_ALLOWED_UID": str(os.getuid()),
                     "BRIDGE_AUDIT": str(data_root / "local-service-audit.jsonl")}, socket_path)
    server = None
    try:
        if workspace:
            broker.start()
        from desktop.server import build_app, LocalBoundary
        from waitress import create_server
        token = secrets.token_urlsafe(32)
        app = build_app(manager, workspace, socket_path, token, restart, version,
                        quiesce=broker.stop, startup_message=startup_message)
        boundary = LocalBoundary(app)
        server = create_server(boundary, host="127.0.0.1", port=0, threads=4,
                               clear_untrusted_proxy_headers=False,
                               max_request_body_size=131072, expose_tracebacks=False)
        boundary.origin = "http://127.0.0.1:" + str(server.effective_port)
        app.config["PANEL_ORIGIN"] = boundary.origin
        threading.Thread(target=server.run, daemon=True).start()
        print(json.dumps({"url": boundary.origin + "/desktop/", "token": token}), flush=True)
        while not stopped.wait(0.2) and not restart.is_set():
            if workspace and broker.child and broker.child.poll() is not None:
                print(json.dumps({"error": "The local data service stopped. Reopen the app to recover."}), flush=True)
                stopped.set()
    finally:
        if server:
            server.close()
        broker.stop()
        shutil.rmtree(runtime, ignore_errors=True)
        lock_file.close()
    if restart.is_set() and not stopped.is_set():
        os.execv(sys.executable, [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                 "--data-root", str(data_root)])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"error": "Open Health Atlas could not start. Your saved data was kept. Try reopening the app."}), flush=True)
        raise SystemExit(1)
