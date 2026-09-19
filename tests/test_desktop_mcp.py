"""Desktop-specific connection regressions; protocol coverage is retained separately."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from desktop.mcp_config import (
    ConnectionConfigurationError, bundled_executable, client_configuration,
    configuration_json,
)


@pytest.fixture
def selected_workspace(tmp_path):
    bundle = tmp_path / "App Folder" / "Open Health Atlas.app"
    executable = bundle / "Contents" / "MacOS" / "openhealthatlas-mcp"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    database = tmp_path / "Personal records" / "health.db"
    database.parent.mkdir()
    database.touch()
    return bundle, executable, database


def test_export_uses_installed_executable_explicit_database_and_timezone(selected_workspace):
    bundle, executable, database = selected_workspace
    assert bundled_executable(bundle) == executable
    config = json.loads(configuration_json(executable, database, "Europe/Paris"))
    assert config == {"mcpServers": {"openhealthatlas": {
        "command": str(executable),
        "args": ["--database", str(database.resolve()), "--timezone", "Europe/Paris"],
    }}}
    # Spaces remain one argument; exporting needs neither a shell nor env secrets.
    assert set(config["mcpServers"]["openhealthatlas"]) == {"command", "args"}


@pytest.mark.parametrize("invalid", ["Mars/Olympus", "../UTC", "", None])
def test_export_rejects_invalid_timezone_without_echoing_private_paths(selected_workspace, invalid):
    _, executable, database = selected_workspace
    with pytest.raises(ConnectionConfigurationError) as caught:
        client_configuration(executable, database, invalid)
    assert str(database) not in str(caught.value)


def test_export_does_not_create_workspace_or_fall_back_to_developer_python(selected_workspace, tmp_path):
    bundle, executable, database = selected_workspace
    missing = tmp_path / "missing.db"
    with pytest.raises(ConnectionConfigurationError):
        client_configuration(executable, missing, "UTC")
    assert not missing.exists()
    with pytest.raises(ConnectionConfigurationError):
        client_configuration(executable, Path("relative.db"), "UTC")
    executable.unlink()
    with pytest.raises(ConnectionConfigurationError):
        bundled_executable(bundle)


def test_bundled_wrapper_reports_failure_on_stderr_without_creating_database(tmp_path):
    wrapper = Path(__file__).resolve().parents[1] / "desktop" / "mcp.py"
    missing = tmp_path / "never-created.db"
    process = subprocess.run(
        [sys.executable, "-B", str(wrapper), "--database", str(missing), "--timezone", "UTC"],
        capture_output=True, text=True, timeout=20,
    )
    assert process.returncode == 2
    assert process.stdout == ""
    assert process.stderr and str(missing) not in process.stderr
    assert not missing.exists()


def test_isolated_launcher_keeps_workers_isolated_and_bundle_read_only():
    server = Path(__file__).resolve().parents[1] / "scripts" / "openhealthatlas_mcp.py"
    # Capture the actual worker spawn from an isolated parent interpreter. The
    # spawn is refused before any process or database can be opened.
    exercise = '''
import asyncio, json, pathlib, runpy, sys
scope = runpy.run_path(sys.argv[1])
async def capture(*args, **kwargs):
    print(json.dumps(list(args)))
    raise RuntimeError("spawn intercepted")
asyncio.create_subprocess_exec = capture
async def exercise():
    tasks = scope["AnalysisTasks"](pathlib.Path("unused.db"), "UTC")
    task = {"status": "queued"}
    await tasks._run("test", task, "{}")
    task["expiry"].cancel()
asyncio.run(exercise())
'''
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", exercise, str(server)],
        capture_output=True, text=True, timeout=20, check=True,
    )
    command = json.loads(result.stdout)
    assert command[1:3] == ["-I", "-B"]
    assert command[3] == str(server)
    assert "--worker" in command


def test_workspace_connection_follows_atomic_upgrade_without_changing_selected_workspace(selected_workspace, tmp_path):
    from desktop.mcp_config import workspace_database
    _, executable, _ = selected_workspace
    workspace = tmp_path / "selected-workspace"
    workspace.mkdir()
    metadata = workspace / "workspace.json"
    generations = ["1" * 32, "2" * 32]
    databases = []
    for generation in generations:
        database = workspace / "generations" / generation / "health.db"
        database.parent.mkdir(parents=True)
        database.touch()
        databases.append(database)
    def select(generation):
        metadata.write_text(json.dumps({"version": 1, "kind": "personal", "generation": generation}))
    select(generations[0])
    exported = client_configuration(executable, databases[0], "UTC", workspace=workspace)
    assert exported["mcpServers"]["openhealthatlas"]["args"] == [
        "--workspace", str(workspace.resolve()), "--timezone", "UTC",
    ]
    assert workspace_database(workspace) == databases[0].resolve()
    select(generations[1])
    assert workspace_database(workspace) == databases[1].resolve()
    assert client_configuration(executable, databases[1], "UTC", workspace=workspace) == exported
    with pytest.raises(ConnectionConfigurationError):
        client_configuration(executable, databases[0], "UTC", workspace=workspace)
    select("../unrelated")
    with pytest.raises(ConnectionConfigurationError):
        workspace_database(workspace)
