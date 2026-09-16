"""Fictional Green-day journey through the real panel and broker path.

Only the unavailable external Hermes/model executable is simulated. The test
proves connection and deterministic evidence flow, not genuine model reasoning.
"""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
HEALTH = ROOT / "toolkit" / "health.py"
TOOL = ROOT / "deploy" / "hermes-openhealthatlas-tool"
HERMESCTL = ROOT / "deploy" / "hermesctl"
BROKER = ROOT / "deploy" / "hermes-bridge"
FAKE_HERMES = ROOT / "tests" / "e2e" / "fake_external_hermes_green_days.py"
ANCHOR = date(2026, 6, 30)
FIRST = ANCHOR - timedelta(days=44)


def _json_run(argv: list[str], *, env: dict[str, str], stdin=None, timeout=260):
    completed = subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    value = json.loads(completed.stdout)
    assert value["ok"] is True
    return value


def _seed_fictional_database(
    tmp_path: Path, fixture_id: str,
) -> tuple[Path, dict]:
    data_dir = tmp_path / fixture_id
    receipt = _json_run(
        [
            sys.executable,
            str(ROOT / "scripts" / "demo_flow.py"),
            "--data-dir",
            str(data_dir),
            "--fixture-id",
            fixture_id,
            "--seed-only",
        ],
        env={**os.environ, "HERMES_TIMEZONE": "UTC"},
    )
    return data_dir / "health.db", receipt


def _write_tool_config(
    path: Path, database: Path, fixture: dict, audit: Path,
) -> None:
    path.write_text(json.dumps({
        "audit": str(audit),
        "contract": "openhealthatlas-hermes-tool-v1",
        "data_class": "fictional",
        "data_dir": str(database.parent),
        "fixture_id": fixture["fixture_id"],
        "health_cli": str(HEALTH),
        "health_db": str(database),
        "health_vault": str(database.parent / "vault"),
        "range_from": fixture["range_from"],
        "range_to": fixture["range_to"],
        "readiness_ancestry_sidecar": None,
        "readiness_fixture_lane": "development-v7",
        "synthesis_writeback_enabled": False,
        "timeout_seconds": 90,
        "timezone": "UTC",
    }, sort_keys=True), encoding="utf-8")


def _install_context(tmp_path: Path) -> tuple[Path, Path]:
    text = (
        "# Hermes Autonomous Health Context\n\n"
        "Contract-Version: 2\nUse only the fictional acceptance database.\n"
    )
    context = tmp_path / "AUTONOMOUS-INSIGHT-CONTEXT.md"
    manifest = tmp_path / "AUTONOMOUS-INSIGHT-CONTEXT.manifest.json"
    context.write_text(text, encoding="utf-8")
    manifest.write_text(json.dumps({
        "contract_version": 2,
        "context_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }), encoding="utf-8")
    return context, manifest


def _short_socket() -> str:
    socket_root = "/private/tmp" if os.path.isdir("/private/tmp") else tempfile.gettempdir()
    descriptor, path = tempfile.mkstemp(prefix="oha-e2e-", suffix=".sock", dir=socket_root)
    os.close(descriptor)
    os.unlink(path)
    return path


def _launcher(
    tmp_path: Path,
    *,
    context: Path,
    context_manifest: Path,
    audit: Path,
    fake_hermes: Path,
) -> Path:
    launcher = tmp_path / "hermesctl-launcher.py"
    launcher.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        f"source_path = {str(HERMESCTL)!r}\n"
        f"os.environ['HERMES_TRANSPORT_BIN'] = {str(fake_hermes)!r}\n"
        "namespace = {'__file__': source_path, '__name__': 'hermesctl_acceptance'}\n"
        "with open(source_path, encoding='utf-8') as source:\n"
        "    exec(compile(source.read(), source_path, 'exec'), namespace)\n"
        f"namespace['VAULT'] = {str(tmp_path)!r}\n"
        f"namespace['AUTONOMOUS_CONTEXT'] = {str(context)!r}\n"
        f"namespace['AUTONOMOUS_MANIFEST'] = {str(context_manifest)!r}\n"
        f"namespace['OPENHEALTHATLAS_TOOL'] = {str(TOOL)!r}\n"
        f"namespace['OPENHEALTHATLAS_TOOL_AUDIT'] = {str(audit)!r}\n"
        f"namespace['OPENHEALTHATLAS_HEALTH_CLI'] = {str(HEALTH)!r}\n"
        "namespace['main']()\n",
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    return launcher


def _start_broker(
    tmp_path: Path,
    *,
    socket_path: str,
    launcher: Path,
    tool_config: Path,
    capture: Path,
) -> tuple[subprocess.Popen, Path]:
    broker_audit = tmp_path / "bridge.jsonl"
    process = subprocess.Popen(
        [sys.executable, str(BROKER)],
        env={
            **os.environ,
            "BRIDGE_SOCK": socket_path,
            "BRIDGE_ALLOWED_UID": str(os.getuid()),
            "BRIDGE_AUDIT": str(broker_audit),
            "BRIDGE_HERMESCTL": str(launcher),
            "OPENHEALTHATLAS_SOURCE_TEST": "1",
            "OPENHEALTHATLAS_TEST_CONFIG": str(tool_config),
            "OPENHEALTHATLAS_TEST_TOOL": str(TOOL),
            "OPENHEALTHATLAS_TEST_CAPTURE": str(capture),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(200):
        if os.path.exists(socket_path):
            break
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"broker exited early: {stdout}{stderr}")
        time.sleep(0.02)
    else:
        process.kill()
        stdout, stderr = process.communicate()
        raise AssertionError(f"broker socket did not appear: {stdout}{stderr}")
    return process, broker_audit


def _bounded_context(start: str, end: str) -> dict:
    return {
        "version": 1,
        "range": {"kind": "bounded", "from": start, "to": end},
        "selected_region_ids": [],
    }


def _create_and_send(client, start: str, end: str) -> tuple[str, dict]:
    context = _bounded_context(start, end)
    created = client.post(
        "/api/chat/conversations",
        json={"lens": "general", "context": context},
    )
    assert created.status_code == 201, created.get_json()
    conversation_id = created.get_json()["conversation"]["id"]
    response = client.post(
        f"/api/chat/conversations/{conversation_id}/send",
        json={
            "message": "Which days were Green and why?",
            "turn_id": str(uuid.uuid4()),
            "context": context,
        },
    )
    assert response.status_code == 200, response.get_json()
    return conversation_id, response.get_json()


def test_fictional_green_day_full_panel_journey_and_insufficient_data(tmp_path):
    database, fixture = _seed_fictional_database(
        tmp_path, "green-days-actionable-v1",
    )
    marker_database, marker_fixture = _seed_fictional_database(
        tmp_path, "green-days-marker-only-v1",
    )
    cycle_database, cycle_fixture = _seed_fictional_database(
        tmp_path, "green-days-v1",
    )
    audit = tmp_path / "openhealthatlas-tools.jsonl"
    capture = tmp_path / "synthetic-hermes-capture.jsonl"
    tool_config = tmp_path / "openhealthatlas-tool-config.json"
    _write_tool_config(tool_config, database, fixture, audit)
    context, context_manifest = _install_context(tmp_path)
    fake_hermes = tmp_path / "hermes"
    fake_hermes.write_text(FAKE_HERMES.read_text(encoding="utf-8"), encoding="utf-8")
    fake_hermes.chmod(0o700)
    launcher = _launcher(
        tmp_path,
        context=context,
        context_manifest=context_manifest,
        audit=audit,
        fake_hermes=fake_hermes,
    )
    socket_path = _short_socket()
    broker, broker_audit = _start_broker(
        tmp_path,
        socket_path=socket_path,
        launcher=launcher,
        tool_config=tool_config,
        capture=capture,
    )
    try:
        from app import auth as auth_mod
        from app import create_app

        app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "PANEL_DB": str(tmp_path / "panel.db"),
            "PANEL_COOKIE_SECURE": False,
            "BRIDGE_SOCKET": socket_path,
            "HEALTH_DB": str(database),
        })
        client = app.test_client()
        with app.app_context():
            client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

        assessed_id, assessed = _create_and_send(
            client, FIRST.isoformat(), ANCHOR.isoformat(),
        )
        _write_tool_config(
            tool_config, marker_database, marker_fixture, audit,
        )
        marker_id, marker_only = _create_and_send(
            client, FIRST.isoformat(), ANCHOR.isoformat(),
        )
        _write_tool_config(tool_config, cycle_database, cycle_fixture, audit)
        cycle_id, cycle = _create_and_send(
            client, FIRST.isoformat(), ANCHOR.isoformat(),
        )
        _write_tool_config(tool_config, database, fixture, audit)
        short_start = (ANCHOR - timedelta(days=4)).isoformat()
        insufficient_id, insufficient = _create_and_send(
            client, short_start, ANCHOR.isoformat(),
        )

        for response in (assessed, marker_only, cycle, insufficient):
            assert "## Deterministic result" in response["reply"]
            assert "## Hermes interpretation" in response["reply"]
            assert "### What the user did" in response["reply"]
            assert "### What the body showed" in response["reply"]
            assert "### What remains unknown" in response["reply"]
        assert "Raw deterministic statistical ranking" in assessed["reply"]
        assert "Lower prior-day caffeine exposure" in assessed["reply"]
        assert "Resting heart rate is the strongest raw physiological marker" in (
            assessed["reply"]
        )
        assert "moderate confidence" in assessed["reply"]
        assert "cannot determine what the user did" in marker_only["reply"]
        assert "Lower prior-day caffeine exposure" not in marker_only["reply"]
        assert "exact repeating three-day Green-Yellow-Red fixture" in cycle[
            "reply"
        ]
        assert "No actionable behavior can be ranked" in cycle["reply"]
        assert "Insufficient data" in insufficient["reply"]
        assert "### Ranked hypotheses" not in insufficient["reply"]
        expected_green_dates = fixture["green_dates"]
        assert all(day in assessed["reply"] for day in expected_green_dates)
        assert "Coverage: aligned_n=" in assessed["reply"]
        assert "source=`manual`" in assessed["reply"]
        assert "Limitations: association_not_causation" in assessed["reply"]

        assessed_messages = client.get(
            f"/api/chat/conversations/{assessed_id}/messages"
        ).get_json()["messages"]
        insufficient_messages = client.get(
            f"/api/chat/conversations/{insufficient_id}/messages"
        ).get_json()["messages"]
        marker_messages = client.get(
            f"/api/chat/conversations/{marker_id}/messages"
        ).get_json()["messages"]
        cycle_messages = client.get(
            f"/api/chat/conversations/{cycle_id}/messages"
        ).get_json()["messages"]
        assert [item["role"] for item in assessed_messages] == ["user", "assistant"]
        assert assessed_messages[-1]["content"] == assessed["reply"]
        assert [item["role"] for item in insufficient_messages] == ["user", "assistant"]
        assert insufficient_messages[-1]["content"] == insufficient["reply"]
        assert marker_messages[-1]["content"] == marker_only["reply"]
        assert cycle_messages[-1]["content"] == cycle["reply"]
    finally:
        broker.terminate()
        try:
            broker.wait(timeout=3)
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.wait(timeout=3)
        if os.path.exists(socket_path):
            os.unlink(socket_path)

    captures = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(captures) == 4
    assert captures[0]["prepared"]["assessment_state"] == "assessed"
    assert captures[0]["recorded"] is None
    assert [row["observed_at"] for row in captures[0]["green"]] == expected_green_dates
    assert all(row["source"] == "manual" for row in captures[0]["green"])
    assert captures[0]["replayed"]
    assert captures[0]["classified"][0]["exposure_key"] == (
        "wearable.resting_hr_bpm"
    )
    assert captures[0]["classified"][0]["classification"] == (
        "physiological_marker"
    )
    caffeine = next(
        item for item in captures[0]["classified"]
        if item["exposure_key"] == "substance.caffeine_mg"
        and item["classification"] == "actionable_upstream_contributor"
    )
    assert caffeine["raw_statistical_rank"] == 2
    assert captures[1]["prepared"]["assessment_state"] == "assessed"
    assert captures[1]["classified"]
    assert {
        item["classification"] for item in captures[1]["classified"]
    } == {"physiological_marker"}
    assert captures[2]["prepared"]["assessment_state"] == "assessed"
    assert not any(
        item["classification"] == "actionable_upstream_contributor"
        for item in captures[2]["classified"]
    )
    assert captures[3]["prepared"]["assessment_state"] == "insufficient_data"
    assert captures[3]["replayed"] == []
    assert captures[3]["recorded"] is None

    records = [json.loads(line) for line in audit.read_text().splitlines()]
    result_records = [row for row in records if row["phase"] == "result"]
    turn_ids = list(dict.fromkeys(row["turn_id"] for row in result_records))
    assert len(turn_ids) == 4
    traces = [
        [row for row in result_records if row["turn_id"] == turn_id]
        for turn_id in turn_ids
    ]
    for trace in traces[:3]:
        names = [row["command"][2] for row in trace]
        assert names[:4] == [
            "feature-frame", "data-readiness", "analysis-refresh",
            "feature-registry",
        ]
        assert names[-1] == "synthesis-prepare"
        assert names[4:-1]
        assert set(names[4:-1]) == {"finding-evidence"}
    assert [row["command"][2] for row in traces[3]] == [
        "feature-frame", "data-readiness", "analysis-refresh",
        "synthesis-prepare",
    ]
    assert all(row["data_class"] == "fictional" for row in result_records)
    assert [trace[0]["fixture_id"] for trace in traces] == [
        "green-days-actionable-v1",
        "green-days-marker-only-v1",
        "green-days-v1",
        "green-days-actionable-v1",
    ]
    for trace in traces:
        for previous, current in zip(trace, trace[1:]):
            assert previous["database_after_sha256"] == current[
                "database_before_sha256"
            ]
    assert fixture["seed_sha256"] == (
        "sha256:01e9f7d900fbcc2b0531f3f4d5cf141a"
        "64727916d7b018362d0fabee3f2ca361"
    )
    assert marker_fixture["seed_sha256"] == (
        "sha256:31b85ea572cfff1df0aeb3567c6acae8"
        "79d8423c125a200e62942c9e6c3393c6"
    )
    assert cycle_fixture["seed_sha256"] == (
        "sha256:2b77c73323a7f0656b845c0c3cdac21f"
        "e08e47dc1fda4c3882c680e57f8bd9be"
    )
    assert fixture["data_class"] == "fictional"
    assert fixture["analysis_runs"] == 0
    assert fixture["synthesis_runs"] == 0
    for trace, seeded in zip(
        traces[:3],
        (fixture, marker_fixture, cycle_fixture),
    ):
        assert trace[0]["database_before_sha256"] == seeded["database_sha256"]
    assert traces[3][0]["database_before_sha256"] == traces[0][-1][
        "database_after_sha256"
    ]

    bridge_records = [
        json.loads(line) for line in broker_audit.read_text().splitlines()
    ]
    assert [row["phase"] for row in bridge_records] == [
        "intent", "result",
        "intent", "result",
        "intent", "result",
        "intent", "result",
    ]
    assert all(row["argv"][0] == "hermes-chat" for row in bridge_records)
    assert all("Which days" not in json.dumps(row) for row in bridge_records)
