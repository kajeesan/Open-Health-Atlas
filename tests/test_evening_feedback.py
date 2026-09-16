"""Evening feedback is missing-only, deduplicated, and never writes health data."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKER = ROOT / "deploy/hermes-evening-feedback"
INSTALLER = ROOT / "deploy/install-autonomous-feedback.sh"
SERVICE = ROOT / "deploy/hermes-evening-feedback.service"
DAY = "2026-07-22"


@pytest.fixture()
def harness(tmp_path):
    vault = tmp_path / "vault"
    runtime = vault / ".runtime/evening-feedback"
    vault.mkdir()
    health = tmp_path / "health.py"
    health.write_text("""#!/usr/bin/env python3
import os
print(os.environ['STATUS_JSON'])
""")
    sender = tmp_path / "hermes"
    sender.write_text("""#!/usr/bin/env python3
import os, pathlib, sys, time
counter = pathlib.Path(os.environ['SEND_COUNTER'])
counter.write_text(str(int(counter.read_text()) + 1))
capture = os.environ.get('SEND_CAPTURE')
if capture:
    pathlib.Path(capture).write_text(sys.argv[-1])
behavior = os.environ.get('SEND_BEHAVIOR', 'success')
if not os.environ.get('XDG_RUNTIME_DIR'):
    raise SystemExit(8)
if behavior == 'timeout':
    time.sleep(5)
raise SystemExit(0 if behavior == 'success' else 7)
""")
    sender.chmod(0o755)
    counter = tmp_path / "counter"
    counter.write_text("0")
    capture = tmp_path / "prompt"
    base = {
        "ok": True,
        "date": DAY,
        "prompt_fields": ["day_rating", "mood", "pain_change"],
        "complete_for_prompt": False,
        "pain_followup_due": [
            {"region": "knee", "side": "left", "latest_intensity": 5, "latest_date": "2026-07-21"},
            {"region": "ankle", "side": "right", "latest_intensity": 3, "latest_date": "2026-07-20"},
        ],
    }
    env = {**os.environ, "HERMES_FEEDBACK_TESTING": "1",
           "HERMES_FEEDBACK_VAULT": str(vault),
           "HERMES_FEEDBACK_HEALTH": str(health),
           "HERMES_FEEDBACK_RUNTIME": str(runtime),
           "HERMES_FEEDBACK_SEND": str(sender), "HERMES_FEEDBACK_DATE": DAY,
           "HERMES_FEEDBACK_TIMEOUT": "1", "SEND_COUNTER": str(counter),
           "SEND_CAPTURE": str(capture), "STATUS_JSON": json.dumps(base)}
    return env, runtime, counter, capture


def run(env, ok=True):
    result = subprocess.run([sys.executable, str(WORKER)], env=env,
                            capture_output=True, text=True, timeout=15)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_acknowledged_prompt_contains_only_missing_fields_and_suppresses_duplicate(harness):
    env, runtime, counter, capture = harness
    result = run(env)
    assert result["status"] == "acknowledged" and counter.read_text() == "1"
    prompt = capture.read_text()
    assert "day rating" in prompt and "mood right now" in prompt
    assert "left knee" in prompt and "right ankle" in prompt
    assert "energy" not in prompt and "focus" not in prompt and "your word" not in prompt
    marker = json.loads((runtime / f"{DAY}.json").read_text())
    assert set(marker) == {"date", "prompt_fields", "payload_sha256", "send_time", "status"}
    assert "telegram" not in json.dumps(marker).lower() and prompt not in json.dumps(marker)
    duplicate = run(env)
    assert duplicate["status"] == "duplicate-suppressed" and counter.read_text() == "1"


def test_complete_status_sends_nothing_and_creates_no_marker(harness):
    env, runtime, counter, _capture = harness
    env["STATUS_JSON"] = json.dumps({"ok": True, "date": DAY, "prompt_fields": [],
                                      "complete_for_prompt": True, "pain_followup_due": []})
    result = run(env)
    assert result["status"] == "complete" and counter.read_text() == "0"
    assert not (runtime / f"{DAY}.json").exists()


def test_timeout_is_uncertain_and_never_automatically_retried(harness):
    env, runtime, counter, _capture = harness
    env.update({"SEND_BEHAVIOR": "timeout", "HERMES_FEEDBACK_TIMEOUT": "0.75"})
    result = run(env, ok=False)
    assert result["status"] == "uncertain" and counter.read_text() == "1"
    marker = json.loads((runtime / f"{DAY}.json").read_text())
    assert marker["status"] == "uncertain"
    duplicate = run(env)
    assert duplicate["status"] == "duplicate-suppressed" and counter.read_text() == "1"


def test_pre_dispatch_failure_retries_once_without_marker(harness):
    env, runtime, counter, _capture = harness
    env["HERMES_FEEDBACK_SEND"] = str(pathlib.Path(env["HERMES_FEEDBACK_SEND"]).with_name("absent"))
    result = run(env, ok=False)
    assert "before dispatch after one retry" in result["error"]
    assert counter.read_text() == "0" and not (runtime / f"{DAY}.json").exists()


def test_invalid_or_oversized_status_refuses_before_send(harness):
    env, _runtime, counter, _capture = harness
    bad = json.loads(env["STATUS_JSON"])
    bad["prompt_fields"] = ["pain_change", "mood"]
    env["STATUS_JSON"] = json.dumps(bad)
    assert "invalid prompt fields" in run(env, ok=False)["error"]
    assert counter.read_text() == "0"


def test_nonfinite_timeout_refuses_before_send(harness):
    env, _runtime, counter, _capture = harness
    env["HERMES_FEEDBACK_TIMEOUT"] = "nan"
    assert "invalid feedback send timeout" in run(env, ok=False)["error"]
    assert counter.read_text() == "0"


def test_installer_dry_run_and_units_are_disabled_by_default():
    result = subprocess.run(["sh", str(INSTALLER), "--dry-run"], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "dry-run" and report["enabled"] is False
    timer = (ROOT / "deploy/hermes-evening-feedback.timer").read_text()
    assert "OnCalendar=*-*-* 20:30 UTC" in timer
    assert "RandomizedDelaySec=5m" in timer and "Persistent=false" in timer
    installer = INSTALLER.read_text()
    assert "HERMES_AUTONOMOUS_ENABLE_APPROVED" in installer
    assert "enable --now hermes-evening-feedback.timer" in installer
    assert "--enable requires HERMES_AUTONOMOUS_ENABLE_APPROVED=YES" in installer
    service = SERVICE.read_text()
    assert "HERMES_DATA_DIR=/var/lib/hermes" in service
    assert "HERMES_FEEDBACK_RUNTIME=/var/lib/hermes/.runtime/evening-feedback" in service
    assert "ReadWritePaths=/var/lib/hermes/.runtime/evening-feedback" in service
    syntax = subprocess.run(
        ["sh", "-n", str(INSTALLER)], cwd=ROOT,
        capture_output=True, text=True, timeout=30,
    )
    assert syntax.returncode == 0, syntax.stderr


@pytest.fixture()
def feedback_installer_runtime(tmp_path):
    state = tmp_path / "systemctl-state.json"
    state.write_text(json.dumps({"enabled": [], "active": []}, sort_keys=True))
    fake = tmp_path / "systemctl"
    fake.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
path=pathlib.Path(os.environ["FAKE_FEEDBACK_SYSTEMCTL_STATE"])
state=json.loads(path.read_text())
args=sys.argv[1:]
if args[:2] == ["is-enabled","--quiet"] and len(args)==3:
    raise SystemExit(0 if args[2] in state["enabled"] else 1)
if args[:2] == ["is-active","--quiet"] and len(args)==3:
    raise SystemExit(0 if args[2] in state["active"] else 1)
if args[:2] == ["enable","--now"] and len(args)==3:
    if args[2] not in state["enabled"]: state["enabled"].append(args[2])
    if os.environ.get("FAKE_FEEDBACK_FAIL_ENABLE") == "1":
        path.write_text(json.dumps(state,sort_keys=True)); raise SystemExit(9)
    if args[2] not in state["active"]: state["active"].append(args[2])
    path.write_text(json.dumps(state,sort_keys=True)); raise SystemExit(0)
if args[:2] == ["disable","--now"] and len(args)==3:
    state["enabled"]=[item for item in state["enabled"] if item != args[2]]
    state["active"]=[item for item in state["active"] if item != args[2]]
    path.write_text(json.dumps(state,sort_keys=True)); raise SystemExit(0)
raise SystemExit(0)
""")
    fake.chmod(0o755)
    environment = {
        **os.environ,
        "HERMES_AUTONOMOUS_TEST_MODE": "1",
        "HERMES_FEEDBACK_LIB_ROOT": str(tmp_path / "lib"),
        "HERMES_FEEDBACK_SYSTEMD_ROOT": str(tmp_path / "systemd"),
        "HERMES_FEEDBACK_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "HERMES_FEEDBACK_SYSTEMCTL": str(fake),
        "FAKE_FEEDBACK_SYSTEMCTL_STATE": str(state),
    }
    return environment, state


def test_feedback_activation_is_explicitly_gated_compensated_and_idempotent(
    feedback_installer_runtime,
):
    environment, state = feedback_installer_runtime
    installed = subprocess.run(
        ["sh", str(INSTALLER), "--install"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert installed.returncode == 0, installed.stderr
    assert json.loads(installed.stdout)["status"] == "installed-disabled"

    refused = subprocess.run(
        ["sh", str(INSTALLER), "--enable"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert refused.returncode != 0 and "explicit gate" in refused.stdout
    assert json.loads(state.read_text()) == {"active": [], "enabled": []}

    environment["HERMES_AUTONOMOUS_ENABLE_APPROVED"] = "YES"
    environment["FAKE_FEEDBACK_FAIL_ENABLE"] = "1"
    partial = subprocess.run(
        ["sh", str(INSTALLER), "--enable"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert partial.returncode != 0 and "compensated" in partial.stdout
    assert json.loads(state.read_text()) == {"active": [], "enabled": []}

    environment.pop("FAKE_FEEDBACK_FAIL_ENABLE")
    enabled = subprocess.run(
        ["sh", str(INSTALLER), "--enable"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert enabled.returncode == 0, enabled.stderr
    assert json.loads(enabled.stdout)["status"] == "explicitly-enabled"
    replay = subprocess.run(
        ["sh", str(INSTALLER), "--enable"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert replay.returncode == 0
    assert json.loads(replay.stdout)["idempotent"] is True

    before = {
        path.name: path.read_bytes()
        for root in (
            pathlib.Path(environment["HERMES_FEEDBACK_LIB_ROOT"]),
            pathlib.Path(environment["HERMES_FEEDBACK_SYSTEMD_ROOT"]),
        )
        for path in root.iterdir()
    }
    reinstall = subprocess.run(
        ["sh", str(INSTALLER), "--install"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert reinstall.returncode != 0
    after = {
        path.name: path.read_bytes()
        for root in (
            pathlib.Path(environment["HERMES_FEEDBACK_LIB_ROOT"]),
            pathlib.Path(environment["HERMES_FEEDBACK_SYSTEMD_ROOT"]),
        )
        for path in root.iterdir()
    }
    assert after == before


@pytest.mark.parametrize("escape_kind", ["traversal", "symlink"])
def test_feedback_test_mode_roots_must_resolve_inside_temporary_storage(
    feedback_installer_runtime,
    tmp_path,
    escape_kind,
):
    environment, state = feedback_installer_runtime
    if escape_kind == "traversal":
        escaped_root = "/tmp/../dev/null/hermes-phase8-feedback"
    else:
        escape = tmp_path / "escape"
        escape.symlink_to("/dev/null")
        escaped_root = str(escape / "hermes-phase8-feedback")
    environment["HERMES_FEEDBACK_LIB_ROOT"] = escaped_root
    before = state.read_bytes()
    result = subprocess.run(
        ["sh", str(INSTALLER), "--install"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "must resolve beneath a temporary root" in result.stdout
    assert state.read_bytes() == before


@pytest.mark.parametrize(
    "invalid_kind",
    ["missing", "external", "escaping_symlink", "non_executable"],
)
def test_feedback_test_mode_requires_explicit_temporary_systemctl(
    feedback_installer_runtime,
    tmp_path,
    invalid_kind,
):
    environment, state = feedback_installer_runtime
    sentinel_called = tmp_path / "default-systemctl-was-called"
    sentinel_bin = tmp_path / "sentinel-bin"
    sentinel_bin.mkdir()
    sentinel = sentinel_bin / "systemctl"
    sentinel.write_text('#!/bin/sh\n: > "$SENTINEL_CALLED"\nexit 97\n')
    sentinel.chmod(0o755)
    environment["SENTINEL_CALLED"] = str(sentinel_called)
    environment["PATH"] = (
        f"{sentinel_bin}{os.pathsep}{environment['PATH']}"
    )

    if invalid_kind == "missing":
        environment.pop("HERMES_FEEDBACK_SYSTEMCTL")
    elif invalid_kind == "external":
        environment["HERMES_FEEDBACK_SYSTEMCTL"] = "/bin/true"
    elif invalid_kind == "escaping_symlink":
        escaped = tmp_path / "systemctl-escape"
        escaped.symlink_to("/bin/true")
        environment["HERMES_FEEDBACK_SYSTEMCTL"] = str(escaped)
    else:
        non_executable = tmp_path / "systemctl-not-executable"
        non_executable.write_text("#!/bin/sh\nexit 0\n")
        non_executable.chmod(0o600)
        environment["HERMES_FEEDBACK_SYSTEMCTL"] = str(non_executable)

    before = state.read_bytes()
    result = subprocess.run(
        ["sh", str(INSTALLER), "--install"], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "test mode" in result.stdout
    assert state.read_bytes() == before
    assert not sentinel_called.exists()
