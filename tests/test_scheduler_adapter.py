"""Live-Hermes compatibility contract for the stable scheduler adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import types


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "deploy" / "hermes-scheduler-adapter"
CONTRACT = ROOT / "deploy" / "hermes-autonomous-jobs.example.json"


def _fake_agent(tmp_path: Path) -> tuple[Path, Path]:
    agent = tmp_path / "agent"
    package = agent / "cron"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    state = tmp_path / "jobs.json"
    state.write_text(json.dumps([{
        "id": "existing-morning-job",
        "name": "Morning briefing",
        "enabled": True,
        "origin": {"platform": "telegram"},
        "prompt": "preserve me",
    }], sort_keys=True))
    (package / "jobs.py").write_text(
        """
import json, os, pathlib, uuid
path=pathlib.Path(os.environ["FAKE_HERMES_JOBS"])
def load(): return json.loads(path.read_text())
def save(value): path.write_text(json.dumps(value,sort_keys=True))
def list_jobs(include_disabled=False):
    value=load()
    return value if include_disabled else [j for j in value if j.get("enabled",True)]
def create_job(prompt,schedule,name=None,deliver=None,origin=None,model=None,
               provider=None,script=None,context_from=None,workdir=None,
               no_agent=False,**kwargs):
    value=load()
    job={"id":uuid.uuid4().hex[:12],"name":name,"prompt":prompt,
         "schedule":{"kind":"cron","expr":schedule,"display":schedule},
         "schedule_display":schedule,"deliver":deliver,"origin":origin,
         "model":model,"provider":provider,"model_snapshot":"snapshot-model",
         "provider_snapshot":"snapshot-provider","base_url":None,
         "script":script,"context_from":context_from,"workdir":workdir,
         "no_agent":no_agent,"enabled":True,"state":"scheduled"}
    value.append(job); save(value); return job
def update_job(job_id,updates):
    value=load()
    for job in value:
        if job["id"]==job_id:
            if isinstance(updates.get("schedule"),str):
                expr=updates["schedule"]
                updates=dict(updates)
                updates["schedule"]={"kind":"cron","expr":expr,"display":expr}
                updates["schedule_display"]=expr
            job.update(updates); save(value); return job
def pause_job(job_id,reason=None):
    return update_job(job_id,{"enabled":False,"state":"paused",
                              "paused_reason":reason})
def resume_job(job_id):
    return update_job(job_id,{"enabled":True,"state":"scheduled",
                              "paused_reason":None})
""",
    )
    return agent, state


def _environment(tmp_path: Path, agent: Path, state: Path) -> dict[str, str]:
    context = tmp_path / "hermes-autonomous-context.sh"
    context.write_text("#!/bin/sh\nexit 0\n")
    context.chmod(context.stat().st_mode | stat.S_IXUSR)
    return {
        **os.environ,
        "HERMES_AUTONOMOUS_TEST_MODE": "1",
        "HERMES_SCHEDULER_AGENT_ROOT": str(agent),
        "HERMES_AUTONOMOUS_JOBS_FILE": str(CONTRACT),
        "HERMES_AUTONOMOUS_CONTEXT_SCRIPT": str(context),
        "FAKE_HERMES_JOBS": str(state),
    }


def _run(environment: dict[str, str], *args: str, stdin: str | None = None):
    return subprocess.run(
        [str(ADAPTER), *args],
        input=stdin,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_production_configuration_paths_accept_absolute_operator_overrides(
    tmp_path, monkeypatch
):
    module = types.ModuleType("scheduler_adapter_contract")
    exec(compile(ADAPTER.read_text(), str(ADAPTER), "exec"), module.__dict__)
    configured = tmp_path / "jobs.json"
    configured.write_text("{}")
    monkeypatch.delenv("HERMES_AUTONOMOUS_TEST_MODE", raising=False)
    monkeypatch.setenv("HERMES_AUTONOMOUS_JOBS_FILE", str(configured))
    assert module._configured_path(
        "HERMES_AUTONOMOUS_JOBS_FILE", Path("/does/not/matter")
    ) == configured.resolve()
    monkeypatch.setenv("HERMES_AUTONOMOUS_JOBS_FILE", "relative.json")
    try:
        module._configured_path(
            "HERMES_AUTONOMOUS_JOBS_FILE", Path("/does/not/matter")
        )
    except SystemExit:
        pass
    else:
        raise AssertionError("relative production override was accepted")


def test_adapter_upserts_paused_named_jobs_and_preserves_existing_job(tmp_path):
    assert "DEFAULT_HERMES_PYTHON" in ADAPTER.read_text()
    assert "os.execve(" in ADAPTER.read_text()
    assert "str(DEFAULT_HERMES_PYTHON)" in ADAPTER.read_text()
    agent, state = _fake_agent(tmp_path)
    environment = _environment(tmp_path, agent, state)
    desired = json.loads(CONTRACT.read_text())
    for job in desired["jobs"]:
        result = _run(
            environment, "cron", "upsert", "--stdin",
            stdin=json.dumps(job),
        )
        assert result.returncode == 0, result.stderr

    actual = json.loads(state.read_text())
    morning = next(job for job in actual if job["id"] == "existing-morning-job")
    assert morning == {
        "id": "existing-morning-job",
        "name": "Morning briefing",
        "enabled": True,
        "origin": {"platform": "telegram"},
        "prompt": "preserve me",
    }
    installed = {job["name"]: job for job in actual if job.get("name") in {
        item["id"] for item in desired["jobs"]
    }}
    assert set(installed) == {item["id"] for item in desired["jobs"]}
    assert all(job["enabled"] is False for job in installed.values())
    assert all(job["state"] == "paused" for job in installed.values())
    assert all(job["model_snapshot"] is None for job in installed.values())
    assert all(job["provider_snapshot"] is None for job in installed.values())
    assert all(job["origin"]["owner"] == desired["owner"] for job in installed.values())

    listed = _run(environment, "cron", "list", "--json")
    assert listed.returncode == 0, listed.stderr
    rows = {
        job["id"]: job for job in json.loads(listed.stdout)["jobs"]
        if job.get("id") in installed
    }
    assert rows == {item["id"]: item for item in desired["jobs"]}


def test_adapter_enables_by_stable_name_and_rejects_owner_collision(tmp_path):
    agent, state = _fake_agent(tmp_path)
    environment = _environment(tmp_path, agent, state)
    desired = json.loads(CONTRACT.read_text())
    weekly = desired["jobs"][0]
    installed = _run(
        environment, "cron", "upsert", "--stdin",
        stdin=json.dumps(weekly),
    )
    assert installed.returncode == 0, installed.stderr
    enabled = _run(environment, "cron", "enable", weekly["id"])
    assert enabled.returncode == 0, enabled.stderr
    row = next(job for job in json.loads(state.read_text()) if job.get("name") == weekly["id"])
    assert row["enabled"] is True

    value = json.loads(state.read_text())
    next(job for job in value if job.get("name") == weekly["id"])["origin"]["owner"] = "somebody-else"
    state.write_text(json.dumps(value, sort_keys=True))
    refused = _run(
        environment, "cron", "upsert", "--stdin",
        stdin=json.dumps({**weekly, "enabled": True}),
    )
    assert refused.returncode != 0
    assert "different-owner" in refused.stderr
