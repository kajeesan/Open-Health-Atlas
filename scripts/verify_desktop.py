#!/usr/bin/env python3
"""Exercise the packaged runtime with fictional isolated data; no GUI claims."""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import re
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request


class Journey:
    def __init__(self, bundle, root):
        self.bundle, self.root = bundle, root
        self.child = None
        self.events = queue.Queue()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def launch(self, wait_ready=True):
        runtime = self.bundle / "Contents/Resources/PythonRuntime/bin/python3"
        script = self.bundle / "Contents/Resources/app/desktop/launcher.py"
        self.child = subprocess.Popen([str(runtime), "-I", "-B", str(script), "--data-root", str(self.root)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      env={"PATH": "/usr/bin:/bin", "HOME": str(self.root.parent), "LANG": "en_US.UTF-8"})
        def lines():
            for line in self.child.stdout:
                self.events.put(json.loads(line))
        threading.Thread(target=lines, daemon=True).start()
        return self.ready() if wait_ready else None

    def ready(self):
        event = self.events.get(timeout=240)
        assert "url" in event, "Packaged runtime did not become ready"
        self.origin = event["url"].removesuffix("/desktop/")
        self.token = event["token"]
        return event

    def request(self, path, data=None, headers=None, method=None, raw=False):
        encoded = json.dumps(data).encode() if data is not None else None
        values = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(self.origin + path, data=encoded, headers=values,
                                     method=method or ("POST" if data is not None else "GET"))
        try:
            with self.opener.open(req, timeout=240) as response:
                body = response.read()
                return response.status, body if raw else body.decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def login(self):
        code, _ = self.request("/desktop/session", headers={"X-OHA-Launch-Token": self.token}, method="POST")
        assert code == 200, "Native launch session failed"

    def csrf(self):
        status, page = self.request("/desktop/?setup=1")
        assert status == 200, "Setup page failed"
        return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)

    def post(self, path, data):
        return self.request(path, data, {"Origin": self.origin, "X-CSRFToken": self.csrf()})

    def switch(self, data):
        status, _ = self.post("/desktop/api/workspaces" if "kind" in data else "/desktop/api/select", data)
        assert status == 200, "Workspace operation failed"
        self.ready()
        self.login()

    def selected(self):
        settings = json.loads((self.root / "settings.json").read_text())
        folder = self.root / "workspaces" / settings["selected"]
        metadata = json.loads((folder / "workspace.json").read_text())
        return settings["selected"], folder / "generations" / metadata["generation"] / "health.db"

    def stop(self, abrupt=False, timeout=15):
        if self.child and self.child.poll() is None:
            if abrupt:
                self.child.kill()
            else:
                self.child.stdin.close()
            try:
                self.child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
                self.child = None
                raise AssertionError("Packaged runtime did not stop when its owner exited") from None
        self.child = None


def database_rows(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return {name: connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
                for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checks = []
    with tempfile.TemporaryDirectory(prefix="oha-desktop-acceptance-") as scratch:
        root = Path(scratch) / "data"
        journey = Journey(args.app.resolve(), root)
        try:
            journey.launch()
            assert journey.request("/desktop/")[0] == 401
            assert journey.request("/desktop/", headers={"Host": "example.invalid"})[0] == 403
            assert journey.request("/desktop/", headers={"Origin": "https://example.invalid"})[0] == 403
            assert journey.request("/desktop/", headers={"X-Forwarded-Proto": "https"})[0] == 403
            journey.login()
            assert journey.request("/desktop/session", method="POST", headers={"X-OHA-Launch-Token": journey.token})[0] == 401
            assert journey.request("/desktop/api/workspaces", {"kind": "personal", "timezone": "UTC"})[0] == 400
            checks.append("loopback_auth_origin_csrf_and_one_use_token")
            journey.switch({"kind": "personal", "timezone": "UTC"})
            personal, database = journey.selected()
            status, body = journey.post("/api/log/body", {"weight_kg": 73.25})
            assert status == 200 and json.loads(body)["ok"], "Packaged validated write failed"
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                assert connection.execute("SELECT weight_kg FROM body_metrics ORDER BY id DESC LIMIT 1").fetchone()[0] == 73.25
            before = database_rows(database)
            assert journey.post("/desktop/api/preferences", {"key": "panel-theme", "value": "alpine"})[0] == 200
            checks.append("empty_workspace_and_real_panel_broker_toolkit_write")
            for path in ("/", "/training", "/nutrition", "/recovery", "/labs", "/desktop/help"):
                assert journey.request(path)[0] == 200, "Retained page failed: " + path
            checks.append("retained_routes_rendered_no_browser_claim")
            journey.stop()
            journey.launch()
            journey.login()
            assert database_rows(journey.selected()[1]) == before
            assert '"panel-theme": "alpine"' in journey.request("/desktop/preferences.js")[1]
            checks.append("quit_reopen_exact_record_preservation")
            duplicate = Journey(args.app.resolve(), root)
            try:
                duplicate.launch()
                raise AssertionError("Duplicate launcher unexpectedly started")
            except AssertionError as error:
                assert str(error) == "Packaged runtime did not become ready"
            finally:
                duplicate.stop()
            checks.append("duplicate_runtime_refused_without_data_change")
            journey.switch({"kind": "demo", "timezone": "Europe/Paris"})
            demo, demo_db = journey.selected()
            assert demo != personal and demo_db != database
            assert "Fictional sample" in journey.request("/")[1]
            demo_records = database_rows(demo_db)
            journey.switch({"kind": "import", "timezone": "Europe/Paris", "source_database": str(demo_db)})
            imported, imported_db = journey.selected()
            assert imported not in (personal, demo)
            assert database_rows(imported_db) == demo_records
            assert database_rows(demo_db) == demo_records
            checks.append("supported_database_import_exact_copy_original_unchanged")
            journey.switch({"id": personal})
            assert database_rows(journey.selected()[1]) == before
            checks.append("fictional_personal_separation_and_switch")
            journey.stop()
            workspace_folder = database.parents[2]
            metadata_path = workspace_folder / "workspace.json"
            metadata = json.loads(metadata_path.read_text())
            old_generation = metadata["generation"]
            metadata["prepared_code_version"] = "0" * 40
            metadata_path.write_text(json.dumps(metadata))
            # Simulate loss of the native parent's pipe while an actual SQLite
            # backup waits on a source lock. The lock stays held until shutdown.
            backup_root = workspace_folder / "backups"
            prior_backups = set(backup_root.iterdir()) if backup_root.exists() else set()
            keeper = sqlite3.connect(database.with_name("panel.db"))
            keeper.execute("BEGIN EXCLUSIVE")
            try:
                journey.launch(wait_ready=False)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    assert journey.child.poll() is None, "Upgrade stopped before cancellation was tested"
                    created = set(backup_root.iterdir()) - prior_backups if backup_root.exists() else set()
                    if any((path / "health.db").is_file() and (path / "panel.db").is_file() for path in created):
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("Packaged upgrade did not reach the blocked backup")
                journey.stop(timeout=2)
                assert journey.events.empty(), "Cancelled upgrade unexpectedly started a web session"
                assert json.loads(metadata_path.read_text())["generation"] == old_generation
            finally:
                keeper.rollback()
                keeper.close()
            checks.append("parent_pipe_loss_cancels_blocked_upgrade_without_selecting_partial_data")
            journey.launch()
            journey.login()
            _, upgraded_db = journey.selected()
            assert upgraded_db != database
            assert database_rows(upgraded_db) == before == database_rows(database)
            backup = workspace_folder / "backups" / upgraded_db.parent.name
            assert database_rows(backup / "health.db") == before
            assert (backup / "panel.db").is_file()
            checks.append("simulated_code_upgrade_verified_backups_exact_record_preservation")
            assert '"panel-theme": "alpine"' in journey.request("/desktop/preferences.js")[1]
            checks.append("workspace_preferences_survive_restart_and_upgrade")
            journey.stop(abrupt=True)
            time.sleep(1)
            journey.launch()
            journey.login()
            assert database_rows(journey.selected()[1]) == before
            checks.append("abrupt_launcher_restart_preserves_records")
            journey.stop()
            (root / "settings.json").write_text("interrupted-settings-fixture")
            journey.launch()
            journey.login()
            assert "workspace-form" in journey.request("/desktop/")[1]
            journey.switch({"id": personal})
            assert database_rows(journey.selected()[1]) == before
            checks.append("startup_failure_recovers_through_workspace_selection")
            assert journey.request("/desktop/api/mcp-config")[0] == 200
            diagnostic = json.loads(journey.request("/desktop/api/diagnostics")[1])
            assert set(diagnostic) == {"product", "code_version", "system", "architecture", "workspace_kind", "selected"}
            checks.append("bundled_mcp_config_and_allowlisted_diagnostics")
        finally:
            journey.stop()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"status": "passed", "checks": checks,
                                      "scope": "packaged runtime; native icon/window, Gatekeeper and charts require separate acceptance"}, indent=2) + "\n")
    print(json.dumps({"status": "passed", "checks": checks}))


if __name__ == "__main__":
    main()
