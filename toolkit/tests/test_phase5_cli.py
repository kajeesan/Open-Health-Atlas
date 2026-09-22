"""Phase 5 canonical health.py validation and command-surface boundaries."""

from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "health.py"
sys.path.insert(0, str(ROOT))

from datetime import datetime, timezone
from hermes_insights import runtime, migrations, registry, ledger, synthesis
from hermes_insights.command_context import CommandContext
from hermes_insights.commands import analytical
from hermes_insights.commands import ledger as ledger_commands
from hermes_insights.commands import synthesis as synthesis_commands
from hermes_insights import cli  # noqa: E402


PHASE5_COMMANDS = {
    "analysis-refresh",
    "hypothesis-promote",
    "hypothesis-refresh",
    "hypothesis-annotate",
    "hypotheses",
    "hypothesis-brief",
    "synthesis-prepare",
    "synthesis-record",
    "synthesis-history",
}
PHASE6_COMMANDS = {
    "insight-run-status",
    "insight-trigger-enqueue",
    "insight-trigger-claim",
    "insight-trigger-renew",
    "insight-trigger-complete",
    "insight-trigger-fail",
    "insight-notification-claim",
    "insight-notification-begin-dispatch",
    "insight-notification-ack",
    "insight-notification-fail",
    "insight-notification-resolve",
}


def _run(tmp_path, *args, stdin=None):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=15,
        env={
            **os.environ,
            "HEALTH_DB": str(tmp_path / "absent.db"),
            "HEALTH_VAULT": str(tmp_path / "vault"),
        },
    )
    payload = json.loads(result.stdout)
    return result, payload


def test_phase5_surface_remains_and_phase6_commands_are_additive():
    assert PHASE5_COMMANDS <= cli.JSON_COMMANDS
    assert PHASE6_COMMANDS <= cli.JSON_COMMANDS
    assert cli.REPEATABLE_FLAGS == {
        "analysis-refresh": {"--outcome"},
    }


def test_manual_refresh_allows_only_repeatable_outcome_flag(tmp_path):
    accepted, payload = _run(
        tmp_path,
        "analysis-refresh",
        "--kind", "manual",
        "--outcome", "subjective.energy",
        "--outcome", "subjective.mood",
        "--from", "2026-01-01",
        "--to", "2026-06-30",
    )
    # Parsing reached the absent disposable database rather than rejecting the
    # one explicitly repeatable flag.
    assert accepted.returncode == 1
    assert payload["error"]["code"] == "database_error"


def test_targeted_green_mode_requires_one_explicit_day_rating_outcome(tmp_path):
    accepted, payload = _run(
        tmp_path,
        "analysis-refresh",
        "--kind", "manual",
        "--outcome", "subjective.day_rating",
        "--mode", "green-vs-non-green",
        "--from", "2026-05-17",
        "--to", "2026-06-30",
    )
    assert accepted.returncode == 1
    assert payload["error"]["code"] == "database_error"

    rejected, payload = _run(
        tmp_path,
        "analysis-refresh",
        "--kind", "manual",
        "--mode", "green-vs-non-green",
        "--all",
    )
    assert rejected.returncode == 2
    assert payload["error"]["code"] == "validation_error"
    assert "exactly one explicit --outcome" in payload["error"]["message"]

    repeated, payload = _run(
        tmp_path,
        "analysis-refresh",
        "--kind", "manual",
        "--anchor", "2026-06-30",
        "--anchor", "2026-06-30",
        "--all",
    )
    assert repeated.returncode == 2
    assert payload["error"]["code"] == "validation_error"
    assert "repeated flag" in payload["error"]["message"]


def test_manual_refresh_fans_out_one_independent_engine_call_per_outcome_mode(
    monkeypatch,
):
    class ReadConnection:
        def execute(self, sql):
            assert sql == "BEGIN"

        def close(self):
            pass

    calls = []
    requested = object()
    context = object()
    definitions = (object(),)
    command_context = CommandContext("unused.db", lambda: datetime(2026, 7, 23, tzinfo=timezone.utc), "UTC", "/unused-vault", str(HEALTH))
    monkeypatch.setattr(ledger_commands, "require_phase5_schema", lambda _context: None)
    monkeypatch.setattr(runtime, "connect_read_only", lambda _database: ReadConnection())
    monkeypatch.setattr(
        migrations, "require_version", lambda _conn, _version: None,
    )
    monkeypatch.setattr(
        analytical, "definitions", lambda _conn, _context: definitions,
    )
    monkeypatch.setattr(
        ledger_commands,
        "selected_outcomes",
        lambda _conn, _explicit: (
            ["subjective.day_rating", "subjective.energy"],
            "explicit_set",
        ),
    )
    monkeypatch.setattr(
        registry,
        "registry_content_checksum",
        lambda _definitions: "f" * 64,
    )

    def compute(_conn, _definitions, seen_range, seen_context, **kwargs):
        assert seen_range is requested
        assert seen_context is context
        calls.append((kwargs["outcome_key"], kwargs["outcome_mode"]))
        return {"sealed": kwargs}

    monkeypatch.setattr(
        ledger, "compute_verified_analysis", compute,
    )
    outcomes, selection, registry_hash, verified = ledger_commands.compute_manual(
        command_context, requested, ["subjective.day_rating", "subjective.energy"],
        adapter_context=context,
    )
    assert outcomes == ["subjective.day_rating", "subjective.energy"]
    assert selection == "explicit_set"
    assert registry_hash == "sha256:" + "f" * 64
    assert calls == [
        ("subjective.day_rating", "ordinal"),
        ("subjective.day_rating", "green-vs-non-green"),
        ("subjective.day_rating", "red-vs-non-red"),
        ("subjective.energy", "ordinal"),
    ]
    assert len(verified) == len(calls)


def test_batch_initiator_is_order_stable_and_bound_to_every_input_fingerprint():
    def sealed(fingerprint):
        return SimpleNamespace(payload={
            "meta": {"input_fingerprint": fingerprint},
        })

    first = (
        "subjective.energy",
        "ordinal",
        sealed("sha256:" + "1" * 64),
    )
    second = (
        "subjective.day_rating",
        "green-vs-non-green",
        sealed("sha256:" + "2" * 64),
    )
    key = ledger_commands.input_bound_initiator(
        "manual-analysis-refresh", [first, second],
    )
    assert key == ledger_commands.input_bound_initiator(
        "manual-analysis-refresh", [second, first],
    )
    changed = (
        second[0],
        second[1],
        sealed("sha256:" + "3" * 64),
    )
    assert key != ledger_commands.input_bound_initiator(
        "manual-analysis-refresh", [first, changed],
    )
    assert key.startswith("manual-analysis-refresh/sha256:")


def test_promotion_requires_identifiers_and_explicit_range_before_db(tmp_path):
    finding = "sha256:" + "a" * 64
    fingerprint = "sha256:" + "b" * 64
    result, payload = _run(
        tmp_path,
        "hypothesis-promote",
        "--outcome", "subjective.day_rating",
        "--finding-id", finding,
        "--input-fingerprint", fingerprint,
    )
    assert result.returncode == 2
    assert payload["error"] == {
        "code": "validation_error",
        "message": "hypothesis-promote requires exactly one explicit range",
    }


def test_phase5_closed_stdin_rejects_ledger_and_numeric_overrides(tmp_path):
    annotation, payload = _run(
        tmp_path,
        "hypothesis-annotate",
        "--stdin",
        stdin='{"status":"replicated","confidence":"high"}',
    )
    assert annotation.returncode == 2
    assert payload["error"]["code"] == "validation_error"

    duplicate, payload = _run(
        tmp_path,
        "hypothesis-annotate",
        "--stdin",
        stdin='{"hypothesis_id":"one","hypothesis_id":"two"}',
    )
    assert duplicate.returncode == 2
    assert "duplicate JSON key" in payload["error"]["message"]

    huge_integer, payload = _run(
        tmp_path,
        "hypothesis-annotate",
        "--stdin",
        stdin='{"evaluation_id":' + "9" * 5000 + "}",
    )
    assert huge_integer.returncode == 2
    assert payload["error"]["code"] == "validation_error"
    assert "numeric literal exceeds" in payload["error"]["message"]

    bounded_but_oversized = {
        "annotation_id": "sha256:" + "1" * 64,
        "hypothesis_id": "sha256:" + "2" * 64,
        "evaluation_id": 10**100,
        "annotation_kind": "alternative",
        "content": "Bounded interpretation.",
        "source": "glm_synthesis",
        "synthesis_id": "sha256:" + "3" * 64,
        "context_version": "2",
        "prompt_sha256": "sha256:" + "4" * 64,
        "model_id": "z-ai/glm-5.2",
        "provider": "openrouter",
        "supersedes_id": None,
        "input_sha256": "sha256:" + "5" * 64,
    }
    oversized_rowid, payload = _run(
        tmp_path,
        "hypothesis-annotate",
        "--stdin",
        stdin=json.dumps(bounded_but_oversized),
    )
    assert oversized_rowid.returncode == 2
    assert payload["error"]["code"] == "validation_error"
    assert "SQLite row identifier" in payload["error"]["message"]

    invalid_utf8, payload = _run(
        tmp_path,
        "hypothesis-annotate",
        "--stdin",
        stdin='{"content":"\\ud800"}',
    )
    assert invalid_utf8.returncode == 2
    assert payload["error"]["code"] == "validation_error"
    assert "valid UTF-8" in payload["error"]["message"]

    synthesis, payload = _run(
        tmp_path,
        "synthesis-record",
        "--stdin",
        stdin='{"effect":0.9,"confidence":"high"}',
    )
    assert synthesis.returncode == 2
    assert payload["error"]["code"] == "validation_error"
    assert "unknown synthesis record key" in payload["error"]["message"]


def test_phase5_pagination_values_fail_before_database_open(tmp_path):
    hypotheses, payload = _run(
        tmp_path, "hypotheses", "--limit", "101",
    )
    assert hypotheses.returncode == 2
    assert payload["error"]["code"] == "validation_error"

    history, payload = _run(
        tmp_path, "synthesis-history", "--before", "not-a-hash",
    )
    assert history.returncode == 2
    assert payload["error"]["code"] == "validation_error"


def test_synthesis_record_commits_ledger_before_append_only_file(
    tmp_path, monkeypatch,
):
    class Connection:
        def __init__(self):
            self.in_transaction = False
            self.committed = False

        def execute(self, _sql):
            self.in_transaction = True

        def commit(self):
            self.committed = True
            self.in_transaction = False

        def rollback(self):
            self.in_transaction = False

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(analytical, "require_phase5_schema", lambda _context: {"current_version": 4})
    monkeypatch.setattr(runtime, "connect", lambda _database: connection)
    monkeypatch.setattr(
        migrations, "require_version", lambda _conn, _version: None,
    )
    monkeypatch.setattr(
        synthesis, "parse_synthesis_json", lambda _text: {},
    )
    monkeypatch.setattr(
        synthesis, "validate_synthesis_record", lambda payload: payload,
    )
    monkeypatch.setattr(
        synthesis,
        "record_synthesis",
        lambda _conn, _payload: {
            "ok": True,
            "synthesis_id": "sha256:" + "a" * 64,
            "message_eligible": True,
            "created": True,
        },
    )

    def append_after_commit(conn, _synthesis_id, *, vault_root):
        assert conn.committed is True
        assert Path(vault_root).is_absolute()
        return tmp_path / "vault" / "personal" / "patterns" / "brief.md"

    monkeypatch.setattr(
        synthesis,
        "write_synthesis_markdown",
        append_after_commit,
    )
    command_context = CommandContext(str(tmp_path / "health.db"), lambda: datetime(2026, 7, 23, tzinfo=timezone.utc), "UTC", str(tmp_path / "vault"), str(HEALTH))
    result = synthesis_commands.synthesis_record(command_context, SimpleNamespace(), stdin=io.StringIO("{}"))
    assert connection.committed is True
    assert result["created"] is True
