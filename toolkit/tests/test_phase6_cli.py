"""Phase 6 canonical health.py command and cadence integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hermes_insights import migrations, orchestrator
from hermes_insights.contracts import AdapterContext
import health


ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "health.py"
SCHEMA_SQL = (ROOT / "SCHEMA.sql").read_text()


@pytest.fixture
def health_db(tmp_path):
    path = tmp_path / "health.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        """INSERT INTO schema_migrations(
             version,name,checksum_sha256,applied_at,code_version)
           VALUES(?,?,?,?,?)""",
        [
            (
                migration.version, migration.name, migration.checksum,
                "2026-07-22T00:00:00+00:00", "test",
            )
            for migration in migrations.MIGRATIONS
        ],
    )
    conn.commit()
    conn.close()
    return path


def _run(path, *args, stdin=None):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args],
        input=stdin, text=True, capture_output=True, timeout=120,
        env={**os.environ, "HEALTH_DB": str(path)},
    )
    payload = json.loads(result.stdout)
    return result, payload


def test_exact_phase6_command_surface_and_bridge_exclusion():
    commands = {
        "insight-trigger-enqueue", "insight-trigger-claim",
        "insight-trigger-renew", "insight-trigger-complete",
        "insight-trigger-fail", "insight-notification-claim",
        "insight-notification-begin-dispatch", "insight-notification-ack",
        "insight-notification-fail", "insight-notification-resolve",
        "insight-run-status",
    }
    assert commands <= health.JSON_COMMANDS


def test_nightly_empty_data_fans_out_and_is_idempotent_without_synthesis(health_db):
    first, payload = _run(
        health_db, "analysis-refresh", "--kind", "nightly",
        "--anchor", "2026-07-22",
    )
    assert first.returncode == 0, first.stderr
    assert payload["plan"]["ranges"][0]["requested_from"] == "2025-07-23"
    assert payload["batch"]["run_count"] == 7
    assert payload["batch"]["no_data_count"] + payload["batch"]["insufficient_count"] == 7
    assert payload["synthesis"] is None
    assert payload["synthesis_preparation"] is None
    assert payload["ledger"] == {
        "ok": True, "changed": False, "skipped": True,
        "reason_code": "nightly_analysis_only",
    }
    assert payload["freshness"]["sources"][-1]["status"] == "retired_historical"
    conn = sqlite3.connect(health_db)
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 0
    first_batch = payload["batch"]["batch_id"]
    first_results = conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
    conn.close()

    second, replay = _run(
        health_db, "analysis-refresh", "--kind", "nightly",
        "--anchor", "2026-07-22",
    )
    assert second.returncode == 0, second.stderr
    assert replay["batch"]["batch_id"] == first_batch
    conn = sqlite3.connect(health_db)
    assert conn.execute("SELECT COUNT(*) FROM analysis_batches").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == first_results
    conn.close()


def test_first_weekly_records_bootstrap_no_novelty_and_no_outbox(health_db):
    result, payload = _run(
        health_db, "analysis-refresh", "--kind", "weekly",
        "--anchor", "2026-07-18",
    )
    assert result.returncode == 0, result.stderr
    assert payload["status"] == "no_novelty"
    assert payload["reason_code"] == "bootstrap_baseline_required"
    assert payload["synthesis"]["message_eligible"] is False
    assert payload["ledger"]["reason_code"] == "bootstrap_baseline_required"
    conn = sqlite3.connect(health_db)
    row = conn.execute(
        "SELECT status,no_message_reason_code FROM synthesis_runs"
    ).fetchone()
    assert row == ("no_novelty", "bootstrap_baseline_required")
    assert conn.execute(
        "SELECT COUNT(*) FROM insight_notification_outbox"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM hypothesis_evaluations"
    ).fetchone()[0] == 0
    conn.close()


def test_monthly_has_three_independent_ranges_and_21_base_runs(health_db):
    result, payload = _run(
        health_db, "analysis-refresh", "--kind", "monthly",
        "--anchor", "2026-07-22",
    )
    assert result.returncode == 0, result.stderr
    assert [item["range_role"] for item in payload["plan"]["ranges"]] == [
        "primary", "recent", "historical",
    ]
    assert payload["batch"]["run_count"] == 21
    conn = sqlite3.connect(health_db)
    families = conn.execute(
        """SELECT q.range_role,r.outcome_key,r.outcome_mode,COUNT(*)
             FROM analysis_runs r
             JOIN analysis_range_requests q ON q.range_id=r.range_id
            GROUP BY q.range_role,r.outcome_key,r.outcome_mode"""
    ).fetchall()
    assert len(families) == 21
    assert all(count == 1 for *_identity, count in families)
    conn.close()


def test_mixed_compute_failure_is_audited_as_partial_batch(
    health_db, monkeypatch, capsys,
):
    monkeypatch.setattr(health, "DB", str(health_db))
    original = health.insight_ledger.compute_verified_analysis
    calls = 0

    def fail_one_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("fixture detail must not enter the audit")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        health.insight_ledger, "compute_verified_analysis", fail_one_run,
    )
    health.analysis_refresh_cmd(SimpleNamespace(
        kind="nightly", anchor="2026-07-22", outcome=[],
        from_date=None, to_date=None, days=None, all_dates=False,
        stdin=False,
    ))
    payload = json.loads(capsys.readouterr().out)
    assert payload["batch"]["status"] == "partial"
    assert payload["batch"]["status_reason_code"] == "some_runs_failed"
    assert payload["batch"]["failed_count"] == 1
    assert len(payload["runs"]) == 7
    conn = sqlite3.connect(health_db)
    failed = conn.execute(
        """SELECT status,status_reason_code FROM analysis_runs
             WHERE status='failed'"""
    ).fetchall()
    assert failed == [("failed", "analysis_compute_failed")]
    assert "fixture detail" not in health_db.read_bytes().decode(
        "utf-8", errors="ignore",
    )
    conn.close()


def test_trigger_refresh_requires_exact_live_fence_and_links_durable_result(health_db):
    conn = sqlite3.connect(health_db)
    conn.row_factory = sqlite3.Row
    enqueued = orchestrator.enqueue_internal_trigger(
        conn, trigger_kind="manual", source_table="operator_requests",
        source_row_key="request-1", event_date="2026-07-20",
        now="2026-07-23T10:00:00+00:00",
    )
    conn.commit()
    claim_time = datetime.now(timezone.utc)
    claimed = orchestrator.claim_trigger(
        conn, "12345678-1234-4234-8234-123456789abc",
        now=claim_time,
    )
    conn.commit()
    conn.close()
    fence = {
        "trigger_id": enqueued["trigger_id"],
        "lease_generation": claimed["trigger"]["lease_generation"],
        "lease_token": claimed["lease_token"],
    }
    result, payload = _run(
        health_db, "analysis-refresh", "--kind", "trigger", "--stdin",
        stdin=json.dumps(fence),
    )
    assert result.returncode == 0, result.stderr
    assert payload["plan"]["anchor_date"] == "2026-07-20"
    assert payload["status"] == "insufficient_data"
    synthesis_id = payload["synthesis"]["synthesis_id"]
    complete = {
        **fence,
        "disposition": "processed",
        "reason_code": "durable_no_message",
        "analysis_batch_id": payload["batch"]["batch_id"],
        "synthesis_id": synthesis_id,
    }
    completed, body = _run(
        health_db, "insight-trigger-complete", "--stdin",
        stdin=json.dumps(complete),
    )
    assert completed.returncode == 0, completed.stderr
    assert body["state"] == "processed"

    stale, error = _run(
        health_db, "analysis-refresh", "--kind", "trigger", "--stdin",
        stdin=json.dumps(fence),
    )
    assert stale.returncode == 1
    assert error["error"]["code"] == "stale_lease"


def test_eligible_capture_producers_enqueue_only_frozen_trigger_branches(health_db):
    pain, body = _run(
        health_db, "pain-log", "anterior-knee",
        "--intensity", "3", "--side", "left", "--date", "2026-07-20",
        "--source", "manual",
    )
    assert pain.returncode == 0, pain.stderr
    # A later/higher report does not invent the disabled pain-worsened threshold.
    pain2, _body = _run(
        health_db, "pain-log", "anterior-knee",
        "--intensity", "7", "--side", "left", "--date", "2026-07-21",
        "--source", "manual",
    )
    assert pain2.returncode == 0, pain2.stderr
    quarterly, _body = _run(
        health_db, "fitness-test-log", "balance-stand",
        "--side", "left", "--seconds", "30", "--date", "2026-07-20",
        "--source", "manual",
    )
    assert quarterly.returncode == 0, quarterly.stderr
    event = {
        "date": "2026-07-20", "category": "medication_change",
        "entity_label": "fixture-medication", "source": "manual",
    }
    medication, _body = _run(
        health_db, "event-log", "--stdin", stdin=json.dumps(event),
    )
    assert medication.returncode == 0, medication.stderr
    conn = sqlite3.connect(health_db)
    kinds = [
        row[0] for row in conn.execute(
            "SELECT trigger_kind FROM insight_triggers ORDER BY trigger_kind"
        )
    ]
    assert kinds == [
        "medication_regime_change", "pain_started", "quarterly_observation",
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM insight_triggers WHERE trigger_kind='pain_worsened'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM insight_triggers WHERE trigger_kind='training_load_change'"
    ).fetchone()[0] == 0
    conn.close()


def test_outcome_count_milestone_is_30_and_capture_replay_dedupes(health_db):
    conn = sqlite3.connect(health_db)
    conn.executemany(
        "INSERT INTO subjective_daily(date,day_rating,source) VALUES(?,3,'manual')",
        [(f"2026-06-{day:02d}",) for day in range(1, 30)],
    )
    conn.commit()
    conn.close()
    first, _body = _run(
        health_db, "day-rating", "green", "--date", "2026-06-30",
        "--source", "manual",
    )
    replay, _body = _run(
        health_db, "day-rating", "yellow", "--date", "2026-06-30",
        "--source", "manual",
    )
    assert first.returncode == replay.returncode == 0
    conn = sqlite3.connect(health_db)
    rows = conn.execute(
        """SELECT trigger_kind,source_row_key FROM insight_triggers
            WHERE trigger_kind='outcome_milestone'"""
    ).fetchall()
    assert rows == [("outcome_milestone", "day_rating_count_30")]
    conn.close()


def test_completeness_proven_running_restart_enqueues_in_batch_transaction(
    health_db, monkeypatch,
):
    conn = sqlite3.connect(health_db)
    conn.executemany(
        """INSERT INTO workouts(date,type,minutes,kcal,km,source)
           VALUES(?,?,?,?,?,?)""",
        [
            ("2026-06-01", "Running", 60, 500, 10, "fixture"),
            ("2026-06-23", "Running", 30, 250, 5, "fixture"),
        ],
    )
    conn.execute(
        """INSERT INTO source_sync_runs(
             source,started_at,completed_at,status,coverage_from,coverage_to,
             rows_seen,rows_written,error_code,details_json)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "fixture", "2026-06-23T23:58:00+00:00",
            "2026-06-24T00:00:00+00:00", "success",
            "2026-06-02", "2026-06-22", 0, 0, None,
            '{"warning_codes":[]}',
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(health, "DB", str(health_db))
    original_context = health._phase3_context()
    complete_context = AdapterContext(
        today=original_context.today,
        timezone=original_context.timezone,
        constants={
            **original_context.constants,
            "RUN_COMPLETENESS_SOURCES": {"fixture"},
        },
        functions=original_context.functions,
    )
    monkeypatch.setattr(health, "_phase3_context", lambda: complete_context)
    plan = orchestrator.cadence_plan(
        "nightly", anchor="2026-07-22",
        local_now=datetime(2026, 7, 23, 6, 10, tzinfo=health.CANON_TZ),
    )
    prepared = health._phase6_prepare_batch("nightly", plan, None)
    assert prepared["producer_triggers"] == [{
        "ok": True,
        "trigger_id": prepared["producer_triggers"][0]["trigger_id"],
        "state": "pending",
        "created": True,
    }]
    conn = sqlite3.connect(health_db)
    row = conn.execute(
        """SELECT trigger_kind,source_table,source_row_key,event_date
             FROM insight_triggers"""
    ).fetchone()
    assert row == (
        "running_restart", "workouts", "restart:2026-06-23", "2026-06-23",
    )
    conn.close()


def test_pain_producer_failure_rolls_back_capture_and_trigger(
    health_db, monkeypatch,
):
    monkeypatch.setattr(health, "DB", str(health_db))

    def fail_enqueue(*_args, **_kwargs):
        raise orchestrator.OrchestrationError(
            "fixture_failure", "fixture producer failure",
        )

    monkeypatch.setattr(
        health.insight_orchestrator, "enqueue_internal_trigger", fail_enqueue,
    )
    args = SimpleNamespace(
        region="anterior-knee", intensity=3, side="left", quality=None,
        pattern=None, flags=None, note=None, date="2026-07-20",
        source="manual", reported_onset_date=None, onset_precision=None,
        capture_id=None,
    )
    with pytest.raises(orchestrator.OrchestrationError):
        health.pain_log(args)
    conn = sqlite3.connect(health_db)
    assert conn.execute("SELECT COUNT(*) FROM pain_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM insight_triggers").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "args",
    [
        ("analysis-refresh", "--kind", "nightly", "--outcome", "subjective.mood"),
        ("analysis-refresh", "--kind", "weekly", "--all"),
        ("analysis-refresh", "--kind", "monthly", "--stdin"),
        ("analysis-refresh", "--kind", "trigger"),
    ],
)
def test_scheduled_refresh_rejects_caller_overrides(health_db, args):
    result, payload = _run(health_db, *args)
    assert result.returncode == 2
    assert payload["error"]["code"] == "validation_error"


def test_run_status_is_read_only_and_redacted(health_db):
    before = health_db.read_bytes()
    result, payload = _run(health_db, "insight-run-status", "--limit", "7")
    assert result.returncode == 0, result.stderr
    assert payload["read_only"] is True
    assert payload["limit"] == 7
    assert health_db.read_bytes() == before
    text = json.dumps(payload)
    assert "lease_token_sha256" not in text.replace(
        '"redacted_fields": ["lease_owner", "lease_token_sha256",', ""
    )
    assert "payload_json" not in text.replace('"payload_json",', "")
