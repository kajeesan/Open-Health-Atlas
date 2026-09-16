"""Synthetic deployment tests for the disabled-by-default autonomous lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "install-autonomous-insights.sh"
CONTEXT = ROOT / "deploy" / "hermes-autonomous-context"
CONTRACT = ROOT / "deploy" / "hermes-autonomous-jobs.example.json"
OWNER = "hermes-open-source-insights-v1"
WEEKLY = "hermes-autonomous-weekly-v1"
MONTHLY = "hermes-autonomous-monthly-v1"
TRIGGER = "hermes-autonomous-trigger-v1"
INSIGHT_TIMER = "hermes-insight-refresh.timer"
EVENING_TIMER = "hermes-evening-feedback.timer"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o700)


def _fake_scheduler(path: Path) -> None:
    _write_executable(
        path,
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_SCHEDULER_STATE"])
log_path = Path(os.environ["FAKE_SCHEDULER_LOG"])
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")

state = json.loads(state_path.read_text(encoding="utf-8"))
failure = os.environ.get("FAKE_SCHEDULER_FAIL", "")

def save():
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

if args == ["cron", "list", "--json"]:
    print(json.dumps({{"jobs": state}}, sort_keys=True))
    raise SystemExit(0)

if args == ["cron", "upsert", "--stdin"]:
    payload = json.load(sys.stdin)
    identifier = payload["id"]
    if failure == "upsert-before:" + identifier:
        raise SystemExit(31)
    matches = [index for index, row in enumerate(state)
               if row.get("id") == identifier or row.get("name") == identifier]
    if len(matches) > 1:
        raise SystemExit(32)
    if matches:
        state[matches[0]] = payload
    else:
        state.append(payload)
    save()
    if failure == "upsert-after:" + identifier:
        raise SystemExit(33)
    print(json.dumps({{"ok": True, "id": identifier}}, sort_keys=True))
    raise SystemExit(0)

if len(args) == 3 and args[:2] == ["cron", "enable"]:
    identifier = args[2]
    if failure == "enable-before:" + identifier:
        raise SystemExit(41)
    matches = [row for row in state
               if row.get("id") == identifier or row.get("name") == identifier]
    if len(matches) != 1 or matches[0].get("owner") != {OWNER!r}:
        raise SystemExit(42)
    matches[0]["enabled"] = True
    save()
    if failure == "enable-after:" + identifier:
        raise SystemExit(43)
    print(json.dumps({{"ok": True, "id": identifier}}, sort_keys=True))
    raise SystemExit(0)

raise SystemExit(64)
""",
    )


def _fake_systemctl(path: Path) -> None:
    _write_executable(
        path,
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_SYSTEMCTL_STATE"])
log_path = Path(os.environ["FAKE_SYSTEMCTL_LOG"])
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
state = json.loads(state_path.read_text(encoding="utf-8"))
failure = os.environ.get("FAKE_SYSTEMCTL_FAIL", "")

def row(unit):
    return state.setdefault(unit, {{"active": False, "enabled": False}})

def save():
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

if args == ["daemon-reload"]:
    raise SystemExit(0)
if len(args) == 2 and args[0] == "is-enabled":
    enabled = row(args[1])["enabled"]
    print("enabled" if enabled else "disabled")
    raise SystemExit(0 if enabled else 1)
if len(args) == 2 and args[0] == "is-active":
    active = row(args[1])["active"]
    print("active" if active else "inactive")
    raise SystemExit(0 if active else 3)
if len(args) == 3 and args[:2] == ["enable", "--now"]:
    unit = args[2]
    if failure == "enable-before:" + unit:
        raise SystemExit(51)
    row(unit).update(enabled=True, active=True)
    save()
    if failure == "enable-after:" + unit:
        raise SystemExit(52)
    raise SystemExit(0)
if len(args) == 3 and args[:2] == ["disable", "--now"]:
    unit = args[2]
    row(unit).update(enabled=False, active=False)
    save()
    raise SystemExit(0)
raise SystemExit(64)
""",
    )


def _deployment(tmp_path: Path) -> dict[str, object]:
    # Resolve the pytest root so macOS's /var -> /private/var alias does not
    # create an accidental symlink component in the deployment safety tests.
    base = tmp_path.resolve()
    commands = base / "commands"
    commands.mkdir()
    scheduler = commands / "scheduler"
    systemctl = commands / "systemctl"
    _fake_scheduler(scheduler)
    _fake_systemctl(systemctl)

    scheduler_state = base / "scheduler-state.json"
    scheduler_log = base / "scheduler.log"
    systemctl_state = base / "systemctl-state.json"
    systemctl_log = base / "systemctl.log"
    unrelated = {
        "id": "fictional-unrelated-job",
        "enabled": True,
        "owner": "fictional-operator",
        "task": "preserve this synthetic row",
    }
    scheduler_state.write_text(json.dumps([unrelated]), encoding="utf-8")
    scheduler_log.write_text("", encoding="utf-8")
    systemctl_state.write_text(
        json.dumps({
            EVENING_TIMER: {"active": True, "enabled": True},
            INSIGHT_TIMER: {"active": False, "enabled": False},
        }),
        encoding="utf-8",
    )
    systemctl_log.write_text("", encoding="utf-8")

    context = base / "operator-context.md"
    context.write_text(
        "# Fictional operator context\nUse only synthetic demonstration data.\n",
        encoding="utf-8",
    )
    context.chmod(0o600)

    systemd_root = base / "systemd"
    library_root = base / "library"
    context_root = base / "etc-hermes"
    backup_root = base / "backups"
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HERMES_AUTONOMOUS_PYTHON": sys.executable,
        "HERMES_AUTONOMOUS_TEST_MODE": "1",
        "HERMES_AUTONOMOUS_SYSTEMD_ROOT": str(systemd_root),
        "HERMES_AUTONOMOUS_LIB_ROOT": str(library_root),
        "HERMES_AUTONOMOUS_CONTEXT_SCRIPT_ROOT": str(context_root),
        "HERMES_AUTONOMOUS_CONTEXT_FILE": str(context),
        "HERMES_AUTONOMOUS_BACKUP_ROOT": str(backup_root),
        "HERMES_AUTONOMOUS_SCHEDULER": str(scheduler),
        "HERMES_AUTONOMOUS_SYSTEMCTL": str(systemctl),
        "FAKE_SCHEDULER_STATE": str(scheduler_state),
        "FAKE_SCHEDULER_LOG": str(scheduler_log),
        "FAKE_SYSTEMCTL_STATE": str(systemctl_state),
        "FAKE_SYSTEMCTL_LOG": str(systemctl_log),
    }
    return {
        "base": base,
        "environment": environment,
        "scheduler_state": scheduler_state,
        "scheduler_log": scheduler_log,
        "systemctl_state": systemctl_state,
        "systemctl_log": systemctl_log,
        "systemd_root": systemd_root,
        "library_root": library_root,
        "context_root": context_root,
        "backup_root": backup_root,
        "context": context,
        "unrelated": unrelated,
    }


def _run(deployment: dict[str, object], mode: str | None = None, **extra: str):
    command = [str(INSTALLER)]
    if mode is not None:
        command.append(mode)
    environment = dict(deployment["environment"])
    environment.update(extra)
    return subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _read(path: object):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _target_jobs(deployment: dict[str, object]) -> dict[str, dict[str, object]]:
    identifiers = {WEEKLY, MONTHLY, TRIGGER}
    return {
        row["id"]: row
        for row in _read(deployment["scheduler_state"])
        if row.get("id") in identifiers
    }


def _assert_job_mode(deployment: dict[str, object], enabled: set[str]) -> None:
    jobs = _target_jobs(deployment)
    desired = {row["id"]: row for row in json.loads(CONTRACT.read_text())["jobs"]}
    assert set(jobs) == set(desired)
    for identifier, expected in desired.items():
        expected = {**expected, "enabled": identifier in enabled}
        assert jobs[identifier] == expected
    assert _read(deployment["scheduler_state"])[0] == deployment["unrelated"]


def _approve(deployment: dict[str, object], mode: str, **extra: str):
    return _run(
        deployment,
        mode,
        HERMES_AUTONOMOUS_ENABLE_APPROVED="YES",
        **extra,
    )


def test_context_feeder_accepts_only_safe_operator_text(tmp_path):
    base = tmp_path.resolve()
    safe = base / "context.md"
    safe.write_text("Synthetic context only.\n", encoding="utf-8")
    safe.chmod(0o600)
    environment = {**os.environ, "HERMES_AUTONOMOUS_CONTEXT_FILE": str(safe)}
    accepted = subprocess.run(
        [str(CONTEXT)], env=environment, capture_output=True, timeout=10
    )
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout == safe.read_bytes()

    safe.chmod(0o620)
    writable = subprocess.run(
        [str(CONTEXT)], env=environment, capture_output=True, timeout=10
    )
    assert writable.returncode != 0
    assert b"group- or world-writable" in writable.stderr
    safe.chmod(0o600)

    link = base / "context-link.md"
    link.symlink_to(safe)
    linked = subprocess.run(
        [str(CONTEXT)],
        env={**environment, "HERMES_AUTONOMOUS_CONTEXT_FILE": str(link)},
        capture_output=True,
        timeout=10,
    )
    assert linked.returncode != 0
    assert b"symlink" in linked.stderr

    real_directory = base / "real-directory"
    real_directory.mkdir()
    through_link_target = real_directory / "context.md"
    through_link_target.write_text("Synthetic context only.\n", encoding="utf-8")
    through_link_target.chmod(0o600)
    linked_directory = base / "linked-directory"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    linked_ancestor = subprocess.run(
        [str(CONTEXT)],
        env={
            **environment,
            "HERMES_AUTONOMOUS_CONTEXT_FILE": str(linked_directory / "context.md"),
        },
        capture_output=True,
        timeout=10,
    )
    assert linked_ancestor.returncode != 0
    assert b"symlink" in linked_ancestor.stderr

    empty = base / "empty.md"
    empty.write_bytes(b"")
    empty.chmod(0o600)
    rejected_empty = subprocess.run(
        [str(CONTEXT)],
        env={**environment, "HERMES_AUTONOMOUS_CONTEXT_FILE": str(empty)},
        capture_output=True,
        timeout=10,
    )
    assert rejected_empty.returncode != 0
    assert b"must not be empty" in rejected_empty.stderr

    nul = base / "nul.md"
    nul.write_bytes(b"fictional\x00context")
    nul.chmod(0o600)
    rejected_nul = subprocess.run(
        [str(CONTEXT)],
        env={**environment, "HERMES_AUTONOMOUS_CONTEXT_FILE": str(nul)},
        capture_output=True,
        timeout=10,
    )
    assert rejected_nul.returncode != 0
    assert b"without NUL" in rejected_nul.stderr

    invalid_utf8 = base / "invalid-utf8.md"
    invalid_utf8.write_bytes(b"fictional context: \xff")
    invalid_utf8.chmod(0o600)
    rejected_encoding = subprocess.run(
        [str(CONTEXT)],
        env={
            **environment,
            "HERMES_AUTONOMOUS_CONTEXT_FILE": str(invalid_utf8),
        },
        capture_output=True,
        timeout=10,
    )
    assert rejected_encoding.returncode != 0
    assert b"valid UTF-8" in rejected_encoding.stderr

    relative = subprocess.run(
        [str(CONTEXT)],
        env={**environment, "HERMES_AUTONOMOUS_CONTEXT_FILE": "context.md"},
        capture_output=True,
        timeout=10,
    )
    assert relative.returncode != 0
    assert b"absolute path" in relative.stderr


def test_default_dry_run_hashes_artifacts_without_external_calls_or_writes(tmp_path):
    deployment = _deployment(tmp_path)
    result = _run(deployment)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == "dry-run"
    assert report["timer_enabled"] is False
    assert report["jobs_enabled"] is False
    assert set(report["artifacts"]) == {
        "hermes-autonomous-context",
        "hermes-autonomous-jobs.example.json",
        "hermes-insight-refresh.service",
        "hermes-insight-refresh.timer",
        "hermes-scheduler-adapter",
    }
    assert Path(deployment["scheduler_log"]).read_text() == ""
    assert Path(deployment["systemctl_log"]).read_text() == ""
    assert not Path(deployment["systemd_root"]).exists()
    assert not Path(deployment["library_root"]).exists()
    assert not Path(deployment["context_root"]).exists()


def test_install_preserves_unrelated_state_and_has_disabled_postcondition(tmp_path):
    deployment = _deployment(tmp_path)
    installed = _run(deployment, "--install")
    assert installed.returncode == 0, installed.stderr
    assert json.loads(installed.stdout)["mode"] == "installed-disabled"
    _assert_job_mode(deployment, set())

    copies = {
        Path(deployment["systemd_root"]) / "hermes-insight-refresh.service":
            ROOT / "deploy" / "hermes-insight-refresh.service",
        Path(deployment["systemd_root"]) / "hermes-insight-refresh.timer":
            ROOT / "deploy" / "hermes-insight-refresh.timer",
        Path(deployment["library_root"]) / "hermes-scheduler-adapter":
            ROOT / "deploy" / "hermes-scheduler-adapter",
        Path(deployment["context_root"]) / "autonomous-context.sh": CONTEXT,
        Path(deployment["context_root"]) / "hermes-autonomous-jobs.json": CONTRACT,
    }
    for target, source in copies.items():
        assert target.is_file() and not target.is_symlink()
        assert target.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(
        (Path(deployment["context_root"]) / "autonomous-context.sh").stat().st_mode
    ) == 0o755
    timer = _read(deployment["systemctl_state"])[INSIGHT_TIMER]
    assert timer == {"active": False, "enabled": False}
    calls = [json.loads(line) for line in Path(deployment["systemctl_log"]).read_text().splitlines()]
    assert ["daemon-reload"] in calls
    assert not any(call and call[0] in {"enable", "disable"} for call in calls)


def test_install_refuses_owner_collision_before_any_target_mutation(tmp_path):
    deployment = _deployment(tmp_path)
    collision = {
        "id": WEEKLY,
        "enabled": False,
        "owner": "fictional-different-owner",
    }
    Path(deployment["scheduler_state"]).write_text(
        json.dumps([deployment["unrelated"], collision]), encoding="utf-8"
    )
    result = _run(deployment, "--install")
    assert result.returncode != 0
    assert "collision" in result.stderr
    assert not Path(deployment["systemd_root"]).exists()
    assert not Path(deployment["library_root"]).exists()
    assert not Path(deployment["context_root"]).exists()
    assert Path(deployment["systemctl_log"]).read_text() == ""


def test_install_refuses_symlink_target_without_changing_referent(tmp_path):
    deployment = _deployment(tmp_path)
    systemd_root = Path(deployment["systemd_root"])
    systemd_root.mkdir()
    referent = Path(deployment["base"]) / "do-not-change.service"
    referent.write_text("synthetic sentinel\n", encoding="utf-8")
    (systemd_root / "hermes-insight-refresh.service").symlink_to(referent)

    result = _run(deployment, "--install")
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert referent.read_text(encoding="utf-8") == "synthetic sentinel\n"
    assert not Path(deployment["library_root"]).exists()
    assert not Path(deployment["context_root"]).exists()


def test_install_failure_rolls_back_files_and_compensates_schedules_disabled(tmp_path):
    deployment = _deployment(tmp_path)
    systemd_root = Path(deployment["systemd_root"])
    systemd_root.mkdir()
    prior_service = systemd_root / "hermes-insight-refresh.service"
    prior_service.write_text("synthetic prior service\n", encoding="utf-8")
    prior_service.chmod(0o640)

    failed = _run(
        deployment,
        "--install",
        FAKE_SCHEDULER_FAIL="upsert-before:" + WEEKLY,
    )
    assert failed.returncode != 0
    assert "rolled back" in failed.stderr
    assert prior_service.read_text(encoding="utf-8") == "synthetic prior service\n"
    assert stat.S_IMODE(prior_service.stat().st_mode) == 0o640
    assert not (systemd_root / "hermes-insight-refresh.timer").exists()
    assert not (Path(deployment["library_root"]) / "hermes-scheduler-adapter").exists()
    assert not (Path(deployment["context_root"]) / "autonomous-context.sh").exists()
    assert not (Path(deployment["context_root"]) / "hermes-autonomous-jobs.json").exists()

    scheduler_state = _read(deployment["scheduler_state"])
    assert scheduler_state[0] == deployment["unrelated"]
    managed = [row for row in scheduler_state if row.get("owner") == OWNER]
    assert managed
    assert all(row["enabled"] is False for row in managed)
    assert _read(deployment["systemctl_state"])[INSIGHT_TIMER] == {
        "active": False,
        "enabled": False,
    }


def test_activation_requires_approval_evening_dependency_and_order(tmp_path):
    deployment = _deployment(tmp_path)
    assert _run(deployment, "--install").returncode == 0

    no_approval = _run(deployment, "--enable-nightly")
    assert no_approval.returncode != 0
    assert "APPROVED=YES" in no_approval.stderr

    state = _read(deployment["systemctl_state"])
    state[EVENING_TIMER] = {"active": False, "enabled": False}
    Path(deployment["systemctl_state"]).write_text(json.dumps(state), encoding="utf-8")
    no_evening = _approve(deployment, "--enable-nightly")
    assert no_evening.returncode != 0
    assert "evening feedback" in no_evening.stderr
    assert _read(deployment["systemctl_state"])[INSIGHT_TIMER] == {
        "active": False,
        "enabled": False,
    }

    state = _read(deployment["systemctl_state"])
    state[EVENING_TIMER] = {"active": True, "enabled": True}
    Path(deployment["systemctl_state"]).write_text(json.dumps(state), encoding="utf-8")
    out_of_order = _approve(deployment, "--enable-weekly")
    assert out_of_order.returncode != 0
    assert "requires the nightly timer" in out_of_order.stderr
    _assert_job_mode(deployment, set())


def test_activation_compensates_partial_failures_and_retries_idempotently(tmp_path):
    deployment = _deployment(tmp_path)
    assert _run(deployment, "--install").returncode == 0

    failed_nightly = _approve(
        deployment,
        "--enable-nightly",
        FAKE_SYSTEMCTL_FAIL="enable-after:" + INSIGHT_TIMER,
    )
    assert failed_nightly.returncode != 0
    assert "compensated disabled" in failed_nightly.stderr
    assert _read(deployment["systemctl_state"])[INSIGHT_TIMER] == {
        "active": False,
        "enabled": False,
    }
    _assert_job_mode(deployment, set())
    nightly = _approve(deployment, "--enable-nightly")
    assert nightly.returncode == 0, nightly.stderr
    assert _approve(deployment, "--enable-nightly").returncode == 0

    failed_weekly = _approve(
        deployment,
        "--enable-weekly",
        FAKE_SCHEDULER_FAIL="enable-after:" + WEEKLY,
    )
    assert failed_weekly.returncode != 0
    assert "weekly job was compensated disabled" in failed_weekly.stderr
    _assert_job_mode(deployment, set())
    weekly = _approve(deployment, "--enable-weekly")
    assert weekly.returncode == 0, weekly.stderr
    assert _approve(deployment, "--enable-weekly").returncode == 0
    _assert_job_mode(deployment, {WEEKLY})

    failed_final = _approve(
        deployment,
        "--enable-monthly-trigger",
        FAKE_SCHEDULER_FAIL="enable-after:" + TRIGGER,
    )
    assert failed_final.returncode != 0
    assert "monthly and trigger jobs were compensated disabled" in failed_final.stderr
    _assert_job_mode(deployment, {WEEKLY})
    final = _approve(deployment, "--enable-monthly-trigger")
    assert final.returncode == 0, final.stderr
    assert _approve(deployment, "--enable-monthly-trigger").returncode == 0
    _assert_job_mode(deployment, {WEEKLY, MONTHLY, TRIGGER})


def test_activation_rejects_contract_drift_before_enabling_timer(tmp_path):
    deployment = _deployment(tmp_path)
    assert _run(deployment, "--install").returncode == 0
    state = _read(deployment["scheduler_state"])
    next(row for row in state if row.get("id") == WEEKLY)["task"] = "drifted task"
    Path(deployment["scheduler_state"]).write_text(json.dumps(state), encoding="utf-8")
    result = _approve(deployment, "--enable-nightly")
    assert result.returncode != 0
    assert "requires all scheduler jobs disabled" in result.stderr
    assert _read(deployment["systemctl_state"])[INSIGHT_TIMER] == {
        "active": False,
        "enabled": False,
    }
