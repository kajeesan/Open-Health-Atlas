"""Recovery evidence and explicit context through the real local broker."""

import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from app import auth, bridge, canon, create_app
from tests.support import csrf_from


ROOT = Path(__file__).resolve().parents[1]
FIXED_SCOPE = {
    "data_class": "fictional", "fixture_id": "fictional-recovery-context",
    "range_from": "2026-03-02", "anchor_date": "2026-06-30",
    "readiness_fixture_lane": "development-v8",
}


def cli(database, *args):
    result = subprocess.run(
        [sys.executable, str(ROOT / "toolkit/health.py"), *args],
        env={**os.environ, "HEALTH_DB": str(database), "HERMES_TIMEZONE": "UTC"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


@pytest.fixture
def broker_app(tmp_path, request):
    data = tmp_path / "fictional-data"
    subprocess.run([sys.executable, str(ROOT / "scripts/init_hermes.py"), "--data-dir", str(data)],
                   check=True, capture_output=True, text=True, timeout=30)
    database = data / "health.db"
    if getattr(request, "param", None) == "demo":
        database = data / "fictional-demo.db"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/make_demo_db.py"), "--output", str(database),
             "--anchor-date", "2026-06-30"],
            check=True, capture_output=True, text=True, timeout=30,
        )
    with tempfile.TemporaryDirectory(prefix="oha-recovery-", dir="/tmp") as socket_dir:
        path = str(Path(socket_dir) / "broker.sock")
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "deploy/hermes-bridge")],
            env={**os.environ, "BRIDGE_SOCK": path, "BRIDGE_ALLOWED_UID": str(os.getuid()),
                 "BRIDGE_AUDIT": str(tmp_path / "broker-audit.jsonl"),
                 "BRIDGE_HEALTH": str(ROOT / "toolkit/health.py"), "HEALTH_DB": str(database)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while not os.path.exists(path):
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail("fictional broker did not start")
                time.sleep(0.01)
            application = create_app({
                "TESTING": True, "WTF_CSRF_ENABLED": True, "RATELIMIT_ENABLED": False,
                "PANEL_DB": str(data / "panel.db"), "PANEL_COOKIE_SECURE": False,
                "HEALTH_DB": str(database), "BRIDGE_SOCKET": path,
                "RECOVERY_FIXED_SCOPE": dict(FIXED_SCOPE),
            })
            client = application.test_client()
            with application.app_context():
                client.set_cookie(auth.SESSION_COOKIE, auth.create_session())
            token = csrf_from(client.get("/recovery").get_data(as_text=True))
            yield application, client, database, {"X-CSRFToken": token}
        finally:
            process.terminate()
            process.communicate(timeout=5)


def answer(question, **overrides):
    return {
        "capture_id": "fictional-recovery-answer", "subject_kind": question["subject_kind"],
        "subject_key": question["subject_key"], "context_key": question["context_key"],
        "effective_from": question["effective_from"], "status": "answered", "value": "Outside the left knee",
        **overrides,
    }


def test_empty_evidence_is_successful_and_uses_fixture_anchor(broker_app):
    _app, client, _database, _headers = broker_app

    response = client.get("/api/recovery/training-evidence")

    assert response.status_code == 200
    body = response.get_json()
    assert body["result"]["range"] == {"from": "2026-06-24", "to": "2026-06-30"}
    assert body["result"]["training_records"] == []
    assert body["result"]["pain_reports"] == []
    assert body["result"]["next_questions"] == []
    assert body["display"]["data_class"] == "fictional"
    assert {row["state"] for row in body["result"]["coverage"]} == {"unknown"}


@pytest.mark.parametrize("broker_app", ["demo"], indirect=True)
def test_generated_demo_packet_preserves_canonical_float_fingerprint(broker_app):
    _app, client, database, _headers = broker_app
    canonical_packet = cli(database, "training-evidence", "--from", "2026-06-24", "--to", "2026-06-30")

    response = client.get("/api/recovery/training-evidence?region=knee")

    assert response.status_code == 200, response.get_json()
    packet = response.get_json()["result"]
    assert packet["evidence_id"] == canonical_packet["evidence_id"]
    assert packet["training_exposure"] == canonical_packet["training_exposure"]
    assert any(
        row["best_e1rm"] is not None and row["best_e1rm"] != int(row["best_e1rm"])
        for row in packet["training_exposure"]
    )


def test_context_answer_retry_correction_is_retained_by_cli(broker_app):
    _app, client, database, headers = broker_app
    cli(database, "pain-log", "lateral-knee", "--side", "left", "--date", "2026-06-28", "--intensity", "4")
    packet = client.get("/api/recovery/training-evidence").get_json()["result"]
    question = packet["next_questions"][0]
    request = answer(question)

    first = client.post("/api/recovery/training-context", json=request, headers=headers)
    repeated = client.post("/api/recovery/training-context", json=request, headers=headers)
    corrected = client.post("/api/recovery/training-context", headers=headers, json=answer(
        question, capture_id="fictional-recovery-correction", value="Just outside the kneecap",
        supersedes_id=first.get_json()["result"]["id"],
    ))
    persisted = cli(database, "training-evidence", "--from", "2026-06-24", "--to", "2026-06-30")

    assert first.status_code == repeated.status_code == corrected.status_code == 200
    assert repeated.get_json()["result"]["unchanged"] is True
    assert repeated.get_json()["result"]["id"] == first.get_json()["result"]["id"]
    context = persisted["pain_reports"][0]["reported_context"]["location"]
    assert context["value"] == "Just outside the kneecap"
    assert context["supersedes_id"] == first.get_json()["result"]["id"]
    assert len(persisted["remembered_context"]) == 2
    assert all(item["context_key"] != "location" for item in persisted["next_questions"])


def test_skip_is_stored_as_unknown_without_an_answer(broker_app):
    _app, client, database, headers = broker_app
    cli(database, "pain-log", "lateral-knee", "--side", "left", "--date", "2026-06-28", "--intensity", "4")
    question = client.get("/api/recovery/training-evidence").get_json()["result"]["next_questions"][0]

    response = client.post("/api/recovery/training-context", headers=headers,
                           json=answer(question, status="skipped", value=None))
    packet = client.get("/api/recovery/training-evidence").get_json()["result"]

    assert response.status_code == 200
    saved = packet["pain_reports"][0]["reported_context"]["location"]
    assert saved["status"] == "skipped"
    assert saved["value"] is None


def test_correcting_right_knee_context_preserves_left_answer(broker_app):
    _app, client, database, headers = broker_app
    cli(database, "pain-log", "lateral-knee", "--side", "left", "--date", "2026-06-28", "--intensity", "4")
    cli(database, "pain-log", "lateral-knee", "--side", "right", "--date", "2026-06-28", "--intensity", "4")
    cli(database, "training-context-log", "--capture-id", "fictional-left-answer", "--subject-kind", "pain",
        "--subject-key", "1", "--context-key", "location", "--effective-from", "2026-06-28", "--value", "Outside knee")
    right = cli(database, "training-context-log", "--capture-id", "fictional-right-answer", "--subject-kind", "pain",
                "--subject-key", "2", "--context-key", "location", "--effective-from", "2026-06-28", "--value", "Outside knee")

    response = client.post("/api/recovery/training-context", headers=headers, json={
        "capture_id": "fictional-right-correction", "subject_kind": "pain", "subject_key": "2",
        "context_key": "location", "effective_from": "2026-06-28", "status": "answered",
        "value": "Outside the right kneecap", "supersedes_id": right["id"],
    })
    packet = cli(database, "training-evidence", "--from", "2026-06-24", "--to", "2026-06-30")

    assert response.status_code == 200
    answers = {row["side"]: row["reported_context"]["location"] for row in packet["pain_reports"]}
    assert answers["left"]["value"] == "Outside knee"
    assert answers["left"]["subject_key"] == "1"
    assert answers["right"]["value"] == "Outside the right kneecap"
    assert answers["right"]["subject_key"] == "2"
    assert answers["right"]["supersedes_id"] == right["id"]


def test_context_write_requires_csrf(broker_app):
    _app, client, _database, _headers = broker_app
    request = answer({"subject_kind": "pain", "subject_key": "1", "context_key": "location", "effective_from": "2026-06-28"})

    response = client.post("/api/recovery/training-context", json=request)

    assert response.status_code == 400
    assert "CSRF" in response.get_json()["error"]


@pytest.mark.parametrize("method,path", [
    ("get", "/api/recovery/training-evidence"), ("post", "/api/recovery/training-context"),
])
def test_training_endpoints_require_authentication(client, method, path):
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("query", [
    "from=2026-06-01", "from=2026-06-01&to=2026-05-31",
    "from=2025-06-01&to=2026-06-30", "from=2026-06-01&to=2026-07-01",
    "from=20260601&to=2026-06-30", "side=bilateral", "region=outer-knee",
    "from=2026-06-01&from=2026-06-02&to=2026-06-30", "unknown=true",
])
def test_invalid_range_is_rejected_before_broker(app, authed, query):
    app.config["RECOVERY_FIXED_SCOPE"] = dict(FIXED_SCOPE)
    response = authed.get("/api/recovery/training-evidence?" + query)
    assert response.status_code == 400


@pytest.mark.parametrize("change", [
    {"subject_kind": []}, {"subject_key": 1}, {"context_key": "invented"},
    {"effective_from": "2026-07-01"}, {"effective_to": "2026-06-20"},
    {"status": "skipped", "value": "assumed no pain"}, {"value": ""},
    {"value": "a" * 1001}, {"supersedes_id": True}, {"source": "agent"},
])
def test_malformed_context_is_rejected_before_broker(app, authed, change):
    app.config["RECOVERY_FIXED_SCOPE"] = dict(FIXED_SCOPE)
    request = answer({"subject_kind": "pain", "subject_key": "1", "context_key": "location", "effective_from": "2026-06-28"}, **change)
    assert authed.post("/api/recovery/training-context", json=request).status_code == 400


def test_transport_error_is_not_presented_as_empty_evidence(authed, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise bridge.BridgeError("fictional broker unavailable")
    monkeypatch.setattr(bridge, "run", unavailable)

    response = authed.get("/api/recovery/training-evidence")

    assert response.status_code == 502
    assert "result" not in response.get_json()


def test_unscoped_default_uses_canonical_date(app, authed, monkeypatch):
    monkeypatch.setattr(canon, "today_iso", lambda: "2026-07-12")
    seen = []
    def unavailable(command, *args):
        seen.append((command, args))
        raise bridge.BridgeError("fixture transport offline")
    monkeypatch.setattr(bridge, "run", unavailable)

    response = authed.get("/api/recovery/training-evidence")

    assert response.status_code == 502
    assert seen == [("training-evidence", ("--from", "2026-07-06", "--to", "2026-07-12", "--region", "knee"))]


def test_broker_rejects_extra_context_flags(broker_app):
    app, _client, _database, _headers = broker_app
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(app.config["BRIDGE_SOCKET"])
        sock.sendall((json.dumps({"subcmd": "training-context-log", "args": ["--invented", "value"]}) + "\n").encode())
        response = json.loads(sock.makefile("rb").readline())
    assert response["ok"] is False
    assert response["stderr"] == "unknown or repeated workflow flag"


@pytest.mark.parametrize("scenario", [
    "scoped_evidence", "empty", "read_error", "stable_retry", "skip",
    "correction", "rejected_edit", "bounded_all", "sided_targets", "unknown_target", "exercise_fitness_targets", "first_question_target",
])
def test_recovery_context_browser_behavior(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for Recovery browser regression checks")
    subprocess.run([node, "tests/fixtures/recovery_context_browser.js", scenario],
                   cwd=ROOT, check=True, capture_output=True, text=True, timeout=20)
