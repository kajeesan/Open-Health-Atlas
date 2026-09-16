"""Phase 4: the panel-side bridge client talks to the broker over a Unix socket.

A tiny in-process stub broker stands in for deploy/hermes-bridge here; focused
tests exercise request validation and Unix peer-credential handling."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from app import bridge, create_app
from app.panel_db import get_db
from deploy.bridge_commands import COMMAND_FLAGS


def _short_sock():
    """AF_UNIX paths must be <108 chars; pytest's tmp_path can exceed that on
    macOS, so put the socket under the system temp root."""
    socket_root = os.environ.get("HERMES_TEST_SOCKET_DIR")
    if socket_root is None and os.path.isdir("/private/tmp"):
        socket_root = "/private/tmp"
    fd, path = tempfile.mkstemp(prefix="hb-", suffix=".sock",
                                dir=socket_root or tempfile.gettempdir())
    os.close(fd)
    os.unlink(path)
    return path


class StubBroker:
    """Answers one line of JSON per connection, like deploy/hermes-bridge."""

    def __init__(self, path, responder):
        self.path = path
        self.responder = responder
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(65536)
                try:
                    req = json.loads(data.decode())
                except ValueError:
                    conn.sendall(b'{"ok": false, "code": 1, "stderr": "bad"}\n')
                    continue
                conn.sendall((json.dumps(self.responder(req)) + "\n").encode())

    def close(self):
        self.sock.close()


def _respond(req):
    sub = req["subcmd"]
    if sub == "day-rating":
        return {"ok": True, "code": 0, "stdout": '{"ok": true, "day_rating": 3}', "stderr": ""}
    if sub == "eat":
        return {"ok": False, "code": 1, "stdout": "", "stderr": "no portions of 'sample-stew' left"}
    if sub == "hermes-chat":
        return {"ok": False, "code": 1,
                "stdout": '{"ok": false, "error": "Hermes took too long"}',
                "stderr": ""}
    if sub == "hypothesis-promote":
        return {
            "ok": False,
            "code": 1,
            "stdout": json.dumps({
                "ok": False,
                "error": {
                    "code": "stale_finding",
                    "message": "input fingerprint changed",
                },
            }),
            "stderr": "",
        }
    return {"ok": False, "code": 2, "stdout": "", "stderr": "unhandled"}


def test_broker_sigterm_exits_without_deadlocking(tmp_path):
    sock_path = _short_sock()
    broker = Path(__file__).resolve().parents[1] / "deploy" / "hermes-bridge"
    process = subprocess.Popen(
        [sys.executable, str(broker)],
        env={
            **os.environ,
            "BRIDGE_SOCK": sock_path,
            "BRIDGE_ALLOWED_UID": str(os.getuid()),
            "BRIDGE_AUDIT": str(tmp_path / "audit.jsonl"),
        },
    )
    try:
        for _ in range(100):
            if os.path.exists(sock_path):
                break
            if process.poll() is not None:
                pytest.fail(f"broker exited early with {process.returncode}")
            time.sleep(0.02)
        else:
            pytest.fail("broker socket did not appear")
        process.terminate()
        assert process.wait(timeout=3) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if os.path.exists(sock_path):
            os.unlink(sock_path)


@pytest.fixture()
def app(tmp_path):
    sock_path = _short_sock()
    broker = StubBroker(sock_path, _respond)
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "BRIDGE_SOCKET": sock_path,
    })
    yield app
    broker.close()
    if os.path.exists(sock_path):
        os.unlink(sock_path)


def _audit_rows(app):
    with app.app_context():
        return [dict(r) for r in get_db().execute(
            "SELECT event, detail FROM audit_log WHERE event LIKE 'bridge.%'")]


@pytest.mark.parametrize("subcmd", ["import-hevy", "import-recipes"])
def test_disallowed_subcommand_never_reaches_socket(app, subcmd):
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="not allowed"):
            bridge.run(subcmd, "/tmp/x.csv")
    rows = _audit_rows(app)  # refusals ARE audited (panel.db), fixing the earlier docstring lie
    assert len(rows) == 1
    assert json.loads(rows[0]["detail"])["outcome"] == "refused-client"


def test_disallowed_capture_flag_never_reaches_socket(tmp_path):
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "BRIDGE_SOCKET": str(tmp_path / "must-not-be-opened.sock"),
    })
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="flag '--evil' is not allowed"):
            bridge.run("day-rating", "green", "--evil", "x")
    assert json.loads(_audit_rows(app)[0]["detail"])["outcome"] == "refused-client-flag"


def test_phase3_no_value_flag_cannot_hide_a_following_bad_flag(tmp_path):
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "BRIDGE_SOCKET": str(tmp_path / "must-not-be-opened.sock"),
    })
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="flag '--evil' is not allowed"):
            bridge.run("feature-frame", "--all", "--evil")
        with pytest.raises(bridge.BridgeError, match="takes no value"):
            bridge.run("feature-frame", "--include-provenance=true")
        with pytest.raises(bridge.BridgeError, match="flag '--evil' is not allowed"):
            bridge.run("outcome-associations", "--all", "--evil")
        with pytest.raises(bridge.BridgeError, match="takes no value"):
            bridge.run("finding-evidence", "--all=true")


def test_success_returns_parsed_json_and_audits(app):
    with app.app_context():
        result = bridge.run("day-rating", "green")
    assert result["ok"] is True and result["day_rating"] == 3
    detail = json.loads(_audit_rows(app)[0]["detail"])
    assert detail["args"] == ["green"] and detail["outcome"] == "exit:0"


def test_trusted_server_code_can_select_an_isolated_read_only_socket(
    tmp_path,
):
    main_path = _short_sock()
    isolated_path = _short_sock()
    main_calls = []
    isolated_calls = []
    main = StubBroker(
        main_path,
        lambda request: (
            main_calls.append(request)
            or {"ok": False, "code": 2, "stdout": "", "stderr": "wrong broker"}
        ),
    )
    isolated = StubBroker(
        isolated_path,
        lambda request: (
            isolated_calls.append(request)
            or {"ok": True, "code": 0, "stdout": '{"contract_version":"outcome-associations-v1"}', "stderr": ""}
        ),
    )
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "isolated-panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "BRIDGE_SOCKET": main_path,
    })
    try:
        with app.app_context():
            result = bridge.run(
                "outcome-associations",
                "--outcome", "subjective.day_rating",
                "--mode", "green-vs-non-green",
                "--days", "45",
                socket_path=isolated_path,
            )
        assert result["contract_version"] == "outcome-associations-v1"
        assert main_calls == []
        assert isolated_calls[0]["subcmd"] == "outcome-associations"
    finally:
        main.close()
        isolated.close()
        for path in (main_path, isolated_path):
            if os.path.exists(path):
                os.unlink(path)


def test_failure_raises_with_health_py_message(app):
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="no portions"):
            bridge.run("eat", "sample-stew")
    assert json.loads(_audit_rows(app)[0]["detail"])["outcome"] == "exit:1"


def test_hermesctl_json_error_is_unwrapped_for_the_ui(app):
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="^Hermes took too long$"):
            bridge.run("hermes-chat", "--session", "hermes-panel-pain",
                       stdin="hello")


def test_structured_health_error_preserves_safe_code_and_message(app):
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="input fingerprint changed") as caught:
            bridge.run(
                "hypothesis-promote",
                "--outcome", "subjective.day_rating",
                "--finding-id", "sha256:" + "a" * 64,
                "--input-fingerprint", "sha256:" + "b" * 64,
                "--all",
            )
    assert caught.value.code == "stale_finding"


def test_transport_error_is_reported_and_audited(tmp_path):
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "BRIDGE_SOCKET": str(tmp_path / "does-not-exist.sock"),
    })
    with app.app_context():
        with pytest.raises(bridge.BridgeError, match="unavailable"):
            bridge.run("day-rating", "green")
        rows = [dict(r) for r in get_db().execute("SELECT detail FROM audit_log")]
    assert "transport-error" in json.loads(rows[0]["detail"])["outcome"]


def test_fitness_writes_are_not_panel_reachable():
    """Capture is Telegram-only + the panel is analysis-only, so the §3e/§3c
    writes must NOT be reachable through the panel bridge (minimal surface).
    Reads are; the void command never is."""
    assert "fitness-tests" in bridge.ALLOWED       # reads allowed
    assert "athletic-radar" in bridge.ALLOWED
    assert "strength-ratios" in bridge.ALLOWED
    for w in ("fitness-test-log", "athletic-target-set", "fitness-test-void"):
        assert w not in bridge.ALLOWED             # writes are not
    assert "vtaper" in bridge.ALLOWED              # §3f read is
    assert "import-hevy-body" not in bridge.ALLOWED   # collector-only import is not
    assert "timing-adherence" in bridge.ALLOWED    # §4b read is
    assert "planned-time-set" not in bridge.ALLOWED   # §4b owner config is not
    assert "water-add" not in bridge.ALLOWED       # §4c capture is Telegram-only
    for w in ("import-cronometer", "nutrition-target-set", "recipe-tag",
              "profile-set", "phase-set"):
        assert w not in bridge.ALLOWED             # §4a import/config: agent/SSH only
        # (recipe-tag T47 meal tags, profile-set/phase-set T43 owner profile —
        #  all athletic-target-set posture: coach-side, never panel-reachable)
    for w in ("journal-capture", "transcript-capture"):
        assert w not in bridge.ALLOWED             # §6 raw capture: agent path only
    for w in ("schema-status", "schema-plan", "migrate", "capture-raw",
              "capture-resolve", "event-log", "event-correct", "event-void",
              "events", "capture-completeness-set", "capture-completeness",
              "entity-alias-set", "entity-alias-retire", "entity-alias-history",
              "supplement-log"):
        assert w not in bridge.ALLOWED             # Phase 2 agent/SSH-only surface


def test_phase3_and_phase4_exact_reads_allowed_and_all_writers_excluded():
    assert {
        "readiness", "feature-registry", "feature-frame", "data-readiness",
        "goal-list", "outcome-associations", "finding-evidence",
        "hypothesis-promote", "hypotheses", "hypothesis-brief",
        "synthesis-history", "insight-run-status",
    } <= bridge.ALLOWED
    assert "correlate" not in bridge.ALLOWED
    assert "day-signature" not in bridge.ALLOWED
    for command in (
        "goal-set", "collector-run-record", "schema-status", "schema-plan", "migrate",
        "capture-raw", "capture-resolve", "event-log", "event-correct", "event-void",
        "capture-completeness-set", "entity-alias-set", "entity-alias-retire",
        "analysis-refresh", "finding-record", "hypothesis-refresh",
        "hypothesis-annotate", "synthesis-record", "hypothesis-evaluate",
        "synthesis-generate", "trigger-record",
        "insight-trigger-enqueue", "insight-trigger-claim",
        "insight-trigger-renew", "insight-trigger-complete",
        "insight-trigger-fail", "insight-notification-claim",
        "insight-notification-begin-dispatch", "insight-notification-ack",
        "insight-notification-fail", "insight-notification-resolve",
    ):
        assert command not in bridge.ALLOWED
    for forbidden_flag in ("--row-id", "--source-table", "--source-domain"):
        assert forbidden_flag not in bridge.ALLOWED_FLAGS["finding-evidence"]
        assert forbidden_flag not in bridge.ALLOWED_FLAGS["outcome-associations"]


def test_physio_writes_are_not_panel_reachable():
    """§3g physio: pain/self-test/trial capture is a COACH conversation, not a
    panel form (design rule 6) — the writers must never be panel-reachable. The
    pain lens READ rides muscle-map --lens pain (already allowed, no new flag),
    so no allowlist change was needed for the read."""
    assert "muscle-map" in bridge.ALLOWED          # the pain lens read is allowed…
    for w in ("pain-log", "self-test-log", "exercise-trial-log", "physio-void"):
        assert w not in bridge.ALLOWED             # …but every §3g write is not


def test_labs_read_allowed_writes_not_reachable():
    """§labs: the read is bridge-exposed; the three writers (capture / ingest /
    catalog seed) are collector-only and must never be panel-reachable.

    The subtle path (audit finding M2): the generic `log` writer IS bridge-
    reachable, and the labs table must not be writable through it either — so
    `labs` must NOT be a LOGGABLE table in health.py. Without this, a bridge
    `log labs test_name=… value=…` would bypass the cited-catalog pipeline."""
    assert "labs" in bridge.ALLOWED                # read allowed
    for w in ("lab-capture", "lab-ingest", "import-lab-catalog"):
        assert w not in bridge.ALLOWED             # collector-only writers are not

    import pathlib
    import re
    assert "log" in bridge.ALLOWED                 # the generic daily-loop writer IS reachable…
    health_src = (pathlib.Path(__file__).resolve().parent.parent
                  / "toolkit" / "health.py").read_text()
    block = health_src.split("LOGGABLE = {", 1)[1].split("\n}", 1)[0]
    loggable = set(re.findall(r'^\s*"([^"]+)"\s*:', block, re.M))
    assert "labs" not in loggable                  # …but it cannot target the labs table
    assert "vitals" in loggable                    # (sanity: the block parse actually found tables)


def test_import_recipes_stays_absent_and_set_batch_flags_pinned():
    """T56/T57: import-recipes gained --per-serving/--servings and set-batch
    gained --portions in toolkit/health.py, but the bridge retains its narrower
    surface."""
    assert "import-recipes" not in bridge.ALLOWED   # unchanged import posture

    assert COMMAND_FLAGS["set-batch"] == {"--grams"}
