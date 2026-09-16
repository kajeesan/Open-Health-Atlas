"""Phase 6 deterministic cadence, fencing, retry, and outbox contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from hermes_insights import migrations, orchestrator
from hermes_insights.contracts import canonical_json


SCHEMA_SQL = (Path(__file__).resolve().parents[1] / "SCHEMA.sql").read_text()
TEST_TZ = ZoneInfo("Europe/Paris")
NOW = "2026-07-23T10:00:00+00:00"
WORKER = "12345678-1234-4234-8234-123456789abc"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
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
    yield conn
    conn.close()


def _trigger(db, *, kind="manual", row_key="row-1", event_date="2026-07-23"):
    return orchestrator.enqueue_internal_trigger(
        db,
        trigger_kind=kind,
        source_table="fixture_rows",
        source_row_key=row_key,
        event_date=event_date,
        now=NOW,
    )


def _seed_outbox_ancestry(db):
    batch_id = orchestrator.sha256_id({"fixture": "batch"})
    synthesis_id = orchestrator.sha256_id({"fixture": "synthesis"})
    db.execute(
        """INSERT INTO analysis_batches(
             batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
             range_plan_version,outcome_selection,outcome_set_sha256,
             analysis_version,registry_version,engine_sha256,registry_sha256,
             status,status_reason_code,run_count,completed_count,
             insufficient_count,no_data_count,failed_count,started_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            batch_id, orchestrator.sha256_id({"fixture": "dedupe"}), "weekly",
            "2026-07-18", "fixture", "cadence-range-plan-v1",
            "base_and_enabled", orchestrator.sha256_id({"fixture": "outcomes"}),
            "outcome-v1.0", "feature-registry-v1",
            orchestrator.sha256_id({"fixture": "engine"}),
            orchestrator.sha256_id({"fixture": "registry"}),
            "completed", None, 0, 0, 0, 0, 0,
            "2026-07-23T08:00:00+00:00", "2026-07-23T08:01:00+00:00",
        ),
    )
    db.execute(
        """INSERT INTO synthesis_runs(
             synthesis_id,analysis_batch_id,cadence,reason_code,cutoff_date,
             evidence_fingerprint,context_version,prompt_sha256,model_id,provider,
             finding_ids_json,hypothesis_ids_json,narrative_md,rendered_md,status,
             no_message_reason_code,created_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            synthesis_id, batch_id, "weekly", "novel_evidence", "2026-07-18",
            orchestrator.sha256_id({"fixture": "evidence"}), "2",
            orchestrator.sha256_id({"fixture": "prompt"}),
            "z-ai/glm-5.2", "openrouter", "[]", "[]",
            "bounded fixture narrative", "bounded fixture rendered", "completed",
            None, "2026-07-23T08:02:00+00:00", "2026-07-23T08:02:00+00:00",
        ),
    )
    return batch_id, synthesis_id


def _notification(
    *,
    dedupe=None,
    payload=None,
    not_before="2026-07-23T12:00:00+00:00",
    mode="none",
):
    return {
        "channel_class": "telegram",
        "destination_class": "owner_primary",
        "payload": payload or {"message": "bounded fixture"},
        "dedupe_key": dedupe or orchestrator.sha256_id({"fixture": "delivery"}),
        "not_before": not_before,
        "idempotency_mode": mode,
        "provider_idempotency_key": "provider-fixture-key" if mode == "provider_key" else None,
    }


def test_exact_cadence_ranges_and_preceding_saturday():
    now = datetime(2026, 7, 23, 6, 10, tzinfo=TEST_TZ)
    nightly = orchestrator.cadence_plan("nightly", local_now=now)
    assert nightly["anchor_date"] == "2026-07-22"
    assert nightly["ranges"] == [{
        "range_role": "primary", "requested_range_kind": "bounded",
        "requested_from": "2025-07-23", "requested_to": "2026-07-22",
    }]
    weekly = orchestrator.cadence_plan("weekly", local_now=now)
    assert weekly["anchor_date"] == "2026-07-18"
    assert weekly["ranges"][0]["requested_from"] == "2025-07-19"
    monthly = orchestrator.cadence_plan("monthly", local_now=now)
    assert monthly["ranges"] == [
        {
            "range_role": "primary", "requested_range_kind": "all",
            "requested_from": None, "requested_to": None,
        },
        {
            "range_role": "recent", "requested_range_kind": "bounded",
            "requested_from": "2026-04-24", "requested_to": "2026-07-22",
        },
        {
            "range_role": "historical", "requested_range_kind": "bounded",
            "requested_from": "2026-01-24", "requested_to": "2026-04-23",
        },
    ]
    trigger = orchestrator.cadence_plan(
        "trigger", local_now=now, event_date="2026-06-01",
        analysis_to="2026-06-05",
    )
    assert trigger["anchor_date"] == "2026-06-05"
    assert trigger["ranges"][0]["requested_from"] == "2025-06-06"
    with pytest.raises(orchestrator.OrchestrationError, match="Saturday"):
        orchestrator.cadence_plan(
            "weekly", local_now=now, anchor="2026-07-19",
        )


@pytest.mark.parametrize(
    ("instant", "anchor"),
    [
        (datetime(2026, 3, 29, 6, 10, tzinfo=TEST_TZ), "2026-03-28"),
        (datetime(2026, 10, 25, 6, 10, tzinfo=TEST_TZ), "2026-10-24"),
    ],
)
def test_configured_timezone_dst_dates_are_calendar_based(instant, anchor):
    plan = orchestrator.cadence_plan("nightly", local_now=instant)
    assert plan["anchor_date"] == anchor
    assert "DST" in plan["dst_disclosure"]
    start = datetime.fromisoformat(plan["ranges"][0]["requested_from"]).date()
    end = datetime.fromisoformat(plan["ranges"][0]["requested_to"]).date()
    assert (end - start).days + 1 == 365


def test_first_weekly_and_no_novelty_are_no_message():
    bootstrap = orchestrator.novelty_decision(
        cadence="weekly", finding_fingerprints=[], transition_fingerprints=[],
        has_weekly_baseline=False,
    )
    assert (bootstrap.eligible, bootstrap.status, bootstrap.reason_code) == (
        False, "no_novelty", "bootstrap_baseline_required",
    )
    unchanged = orchestrator.novelty_decision(
        cadence="monthly", finding_fingerprints=[], transition_fingerprints=[],
    )
    assert not unchanged.eligible
    assert unchanged.reason_code == "no_eligible_novel_evidence"


def test_outcome_fanout_adds_enabled_concrete_goals_and_dedupes_bh_families(db):
    db.executemany(
        """INSERT INTO insight_goal_revisions(
             goal_key,enabled,priority,outcome_key,target_direction,source)
           VALUES(?,1,3,?,?, 'owner-ssh')""",
        [
            ("energy_focus_mood", "subjective.energy", "increase"),
            ("body_recomposition", "body.waist_cm", "decrease"),
        ],
    )
    selected = orchestrator.outcome_modes(db)
    assert selected["outcomes"] == sorted({
        *orchestrator.BASE_OUTCOMES, "body.waist_cm",
    })
    assert len(selected["outcome_modes"]) == 8
    assert len({
        (item["outcome_key"], item["outcome_mode"])
        for item in selected["outcome_modes"]
    }) == 8
    assert selected["bh_family_scope"] == "range_id/outcome_key/outcome_mode"


def test_freshness_exact_thresholds_zero_row_success_and_unknown(db):
    reference = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    for source, hours, rows in (
        ("google-health", 30, 0),
        ("hevy", 48, 0),
        ("weather", 48.0001, 2),
    ):
        completed = reference - timedelta(hours=hours)
        started = completed - timedelta(minutes=1)
        db.execute(
            """INSERT INTO source_sync_runs(
                 source,started_at,completed_at,status,coverage_from,coverage_to,
                 rows_seen,rows_written,error_code,details_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                source, started.isoformat(), completed.isoformat(), "success",
                None, None, rows, rows, None, '{"warning_codes":[]}',
            ),
        )
    snapshot = orchestrator.freshness_snapshot(db, now=reference)
    states = {item["source"]: item["status"] for item in snapshot["sources"]}
    assert states == {
        "google-health": "fresh",
        "hevy": "late",
        "weather": "stale",
        "air": "freshness_unknown",
        "apple-health": "retired_historical",
    }


def test_stale_source_suppresses_only_dependent_candidates():
    weather = SimpleNamespace(
        key="weather.temperature_c", adapter="environment",
        source_semantics="automated_daily", candidate_enabled=True,
    )
    subjective = SimpleNamespace(
        key="subjective.mood", adapter="subjective_daily",
        source_semantics="manual_daily", candidate_enabled=True,
    )
    frame = {
        "sources": [
            {"source": "weather", "status": "stale"},
            {"source": "google-health", "status": "freshness_unknown"},
        ],
    }
    kept, suppressed = orchestrator.suppress_stale_dependencies(
        [weather, subjective], frame,
    )
    assert kept == [subjective]
    assert suppressed == [{
        "feature_key": "weather.temperature_c",
        "source": "weather",
        "reason_code": "stale_collector_dependency",
    }]


def test_structured_slots_use_stored_evidence_and_explicit_missing_values():
    evidence = {
        "testing": {"q": 0.01},
        "effect": {"oriented_estimate": 0.4},
        "evidence_against": ["confounding remains"],
        "confounders": {"checked": ["weekday"]},
    }
    run_result = {
        "readiness": {
            "features": [{
                "feature_key": "sleep.hours", "state": "too_sparse_for_analysis",
                "aligned_n": 12, "missing_rate": 0.5,
                "needed": "18 more observations", "priority": 4.0,
                "effort": 1, "value": 4,
            }],
        },
    }
    slots = orchestrator.structured_slots(
        cadence="weekly",
        run_rows=[{"result_json": canonical_json(run_result)}],
        finding_rows=[{
            "finding_id": "sha256:" + "1" * 64,
            "evidence_fingerprint": "sha256:" + "2" * 64,
            "candidate_key": "subjective.day_rating|fixture",
            "range_role": "primary",
            "outcome_key": "subjective.day_rating",
            "outcome_mode": "green-vs-non-green",
            "direction": "positive",
            "quality_tier": "replicated",
            "eligible_for_hypothesis": 1,
            "evidence_json": canonical_json(evidence),
        }],
        evaluation_rows=[],
        freshness={"sources": [{
            "source": "air", "status": "freshness_unknown",
        }]},
        suppressed_dependencies=[],
    )
    assert (
        slots["slots"]["strongest_green_day_candidate"]["structured_evidence"]
        == evidence
    )
    assert slots["slots"]["strongest_goal_candidate"] == {
        "status": "missing", "reason_code": "no_eligible_goal_candidate",
    }
    assert (
        slots["slots"]["readiness_data_gaps"]["ranked_feature_gaps"][0][
            "feature_key"
        ]
        == "sleep.hours"
    )
    assert slots["slots"]["one_cheap_experiment"]["status"] == "missing"


def test_trigger_enqueue_is_transactional_deduped_and_token_is_secret(db):
    db.execute("BEGIN IMMEDIATE")
    created = _trigger(db)
    db.rollback()
    assert db.execute("SELECT COUNT(*) FROM insight_triggers").fetchone()[0] == 0

    db.execute("BEGIN IMMEDIATE")
    created = _trigger(db)
    replay = _trigger(db, row_key="row-1")
    db.commit()
    assert created["created"] is True
    assert replay["created"] is False
    assert db.execute("SELECT COUNT(*) FROM insight_triggers").fetchone()[0] == 1

    db.execute("BEGIN IMMEDIATE")
    claimed = orchestrator.claim_trigger(db, WORKER, now=NOW)
    db.commit()
    token = claimed["lease_token"]
    assert len(token) == 64
    stored = dict(db.execute("SELECT * FROM insight_triggers").fetchone())
    assert token not in json.dumps(stored)
    assert stored["lease_token_sha256"].startswith("sha256:")
    assert {row["event_kind"] for row in db.execute("SELECT * FROM insight_trigger_events")} == {
        "enqueued", "claimed",
    }
    with pytest.raises(orchestrator.OrchestrationError, match="opaque reference"):
        orchestrator.trigger_identity(
            "manual", "operator_requests", "private note text", "2026-07-23",
        )
    with pytest.raises(orchestrator.OrchestrationError, match="lower-case UUID"):
        orchestrator.claim_trigger(db, WORKER.upper(), now=NOW)


def test_trigger_renew_fence_expiry_and_five_claim_exhaustion(db):
    _trigger(db)
    db.commit()
    current = datetime.fromisoformat(NOW)
    last = None
    for attempt in range(1, 6):
        db.execute("BEGIN IMMEDIATE")
        claim = orchestrator.claim_trigger(db, WORKER, now=current)
        assert claim["claimed"]
        if attempt == 1:
            renewed = orchestrator.renew_trigger(
                db,
                {
                    "trigger_id": claim["trigger"]["trigger_id"],
                    "lease_generation": claim["trigger"]["lease_generation"],
                    "lease_token": claim["lease_token"],
                },
                now=current,
            )
            assert renewed["lease_expires_at"] == (
                current + timedelta(minutes=20)
            ).isoformat(timespec="seconds")
        last = orchestrator.fail_trigger(
            db,
            {
                "trigger_id": claim["trigger"]["trigger_id"],
                "lease_generation": claim["trigger"]["lease_generation"],
                "lease_token": claim["lease_token"],
                "failure_class": "transient",
                "reason_code": "fixture_transient",
            },
            now=current,
        )
        db.commit()
        if attempt < 5:
            assert last["state"] == "retry_wait"
            current = datetime.fromisoformat(last["not_before"])
    assert last["state"] == "dead_letter"
    row = db.execute("SELECT * FROM insight_triggers").fetchone()
    assert row["attempt_count"] == 5
    assert row["processed_at"] is not None

    with pytest.raises(orchestrator.OrchestrationError, match="stale"):
        orchestrator.renew_trigger(
            db,
            {
                "trigger_id": row["trigger_id"],
                "lease_generation": row["lease_generation"],
                "lease_token": "0" * 64,
            },
            now=current,
        )


def test_expired_trigger_is_reclaimed_and_old_worker_is_fenced(db):
    _trigger(db)
    first = orchestrator.claim_trigger(db, WORKER, now=NOW)
    reclaimed = orchestrator.claim_trigger(
        db, WORKER, now="2026-07-23T10:21:00+00:00",
    )
    assert reclaimed["claimed"] is True
    assert reclaimed["trigger"]["lease_generation"] == 2
    with pytest.raises(orchestrator.OrchestrationError) as error:
        orchestrator.fail_trigger(
            db,
            {
                "trigger_id": first["trigger"]["trigger_id"],
                "lease_generation": first["trigger"]["lease_generation"],
                "lease_token": first["lease_token"],
                "failure_class": "transient",
                "reason_code": "stale_worker",
            },
            now="2026-07-23T10:21:01+00:00",
        )
    assert error.value.code == "stale_lease"
    assert db.execute(
        "SELECT 1 FROM insight_trigger_events WHERE event_kind='lease_expired'"
    ).fetchone()


def test_permanent_trigger_failure_dead_letters_immediately(db):
    _trigger(db)
    claim = orchestrator.claim_trigger(db, WORKER, now=NOW)
    result = orchestrator.fail_trigger(
        db,
        {
            "trigger_id": claim["trigger"]["trigger_id"],
            "lease_generation": claim["trigger"]["lease_generation"],
            "lease_token": claim["lease_token"],
            "failure_class": "permanent",
            "reason_code": "invalid_invariant",
        },
        now=NOW,
    )
    assert result["state"] == "dead_letter"


def test_trigger_completion_rejects_an_unrelated_terminal_batch(db):
    unrelated_batch, _synthesis_id = _seed_outbox_ancestry(db)
    _trigger(db)
    claim = orchestrator.claim_trigger(db, WORKER, now=NOW)
    with pytest.raises(orchestrator.OrchestrationError) as error:
        orchestrator.complete_trigger(
            db,
            {
                "trigger_id": claim["trigger"]["trigger_id"],
                "lease_generation": claim["trigger"]["lease_generation"],
                "lease_token": claim["lease_token"],
                "disposition": "processed",
                "reason_code": "fixture_complete",
                "analysis_batch_id": unrelated_batch,
                "synthesis_id": None,
            },
            now=NOW,
        )
    assert error.value.code == "durability_invariant"


def test_quiet_hours_duplicate_mismatch_and_no_attempt_consumption(db):
    batch_id, synthesis_id = _seed_outbox_ancestry(db)
    value = _notification(not_before="2026-07-23T22:30:00+02:00")
    created = orchestrator.enqueue_notification(
        db, value, analysis_batch_id=batch_id, synthesis_id=synthesis_id,
        now=NOW,
    )
    assert created["not_before"] == "2026-07-24T05:00:00+00:00"
    row = db.execute("SELECT * FROM insight_notification_outbox").fetchone()
    assert row["attempt_count"] == 0
    assert row["state"] == "pending"
    replay = orchestrator.enqueue_notification(
        db, value, analysis_batch_id=batch_id, synthesis_id=synthesis_id,
        now=NOW,
    )
    assert replay["created"] is False
    with pytest.raises(orchestrator.OrchestrationError) as error:
        orchestrator.enqueue_notification(
            db,
            _notification(
                dedupe=value["dedupe_key"],
                payload={"message": "different"},
                not_before=value["not_before"],
            ),
            analysis_batch_id=batch_id, synthesis_id=synthesis_id, now=NOW,
        )
    assert error.value.code == "invariant_failure"
    with pytest.raises(orchestrator.OrchestrationError) as metadata_error:
        orchestrator.enqueue_notification(
            db,
            {
                **value,
                "idempotency_mode": "provider_key",
                "provider_idempotency_key": "different-provider-contract",
            },
            analysis_batch_id=batch_id, synthesis_id=synthesis_id, now=NOW,
        )
    assert metadata_error.value.code == "invariant_failure"


def test_dispatch_commit_then_ambiguous_none_is_uncertain_never_retried(db):
    batch_id, synthesis_id = _seed_outbox_ancestry(db)
    enqueued = orchestrator.enqueue_notification(
        db, _notification(), analysis_batch_id=batch_id,
        synthesis_id=synthesis_id, now=NOW,
    )
    claim = orchestrator.claim_notification(db, WORKER, now="2026-07-23T12:00:00+00:00")
    fence = {
        "notification_id": claim["notification"]["notification_id"],
        "lease_generation": claim["notification"]["lease_generation"],
        "lease_token": claim["lease_token"],
    }
    begun = orchestrator.begin_notification_dispatch(
        db, fence, now="2026-07-23T12:00:01+00:00",
    )
    assert begun["state"] == "dispatching"
    assert db.execute(
        """SELECT 1 FROM insight_notification_events
            WHERE notification_id=? AND event_kind='dispatch_committed'""",
        (enqueued["notification_id"],),
    ).fetchone()
    result = orchestrator.fail_notification(
        db,
        {**fence, "failure_class": "ambiguous", "reason_code": "provider_timeout"},
        now="2026-07-23T12:00:02+00:00",
    )
    assert result["state"] == "uncertain"
    assert not orchestrator.claim_notification(
        db, WORKER, now="2026-07-24T12:00:00+00:00",
    )["claimed"]


def test_crash_before_dispatch_reclaims_but_expired_dispatch_is_uncertain(db):
    batch_id, synthesis_id = _seed_outbox_ancestry(db)
    orchestrator.enqueue_notification(
        db, _notification(), analysis_batch_id=batch_id,
        synthesis_id=synthesis_id, now=NOW,
    )
    first = orchestrator.claim_notification(
        db, WORKER, now="2026-07-23T12:00:00+00:00",
    )
    reclaimed = orchestrator.claim_notification(
        db, WORKER, now="2026-07-23T12:11:00+00:00",
    )
    assert reclaimed["claimed"] is True
    assert reclaimed["notification"]["lease_generation"] == (
        first["notification"]["lease_generation"] + 1
    )
    assert db.execute(
        """SELECT 1 FROM insight_notification_events
            WHERE event_kind='lease_expired_before_dispatch'"""
    ).fetchone()

    fence = {
        "notification_id": reclaimed["notification"]["notification_id"],
        "lease_generation": reclaimed["notification"]["lease_generation"],
        "lease_token": reclaimed["lease_token"],
    }
    orchestrator.begin_notification_dispatch(
        db, fence, now="2026-07-23T12:11:01+00:00",
    )
    after_dispatch = orchestrator.claim_notification(
        db, WORKER, now="2026-07-23T12:22:00+00:00",
    )
    assert after_dispatch["claimed"] is False
    assert db.execute(
        "SELECT state FROM insight_notification_outbox"
    ).fetchone()[0] == "uncertain"


def test_known_not_sent_retries_provider_key_reuses_payload_and_ack(db):
    batch_id, synthesis_id = _seed_outbox_ancestry(db)
    orchestrator.enqueue_notification(
        db, _notification(mode="provider_key"), analysis_batch_id=batch_id,
        synthesis_id=synthesis_id, now=NOW,
    )
    claim = orchestrator.claim_notification(db, WORKER, now="2026-07-23T12:00:00+00:00")
    first_payload = claim["notification"]["payload_sha256"]
    first_key = claim["notification"]["provider_idempotency_key"]
    fence = {
        "notification_id": claim["notification"]["notification_id"],
        "lease_generation": claim["notification"]["lease_generation"],
        "lease_token": claim["lease_token"],
    }
    orchestrator.begin_notification_dispatch(db, fence, now="2026-07-23T12:00:01+00:00")
    failed = orchestrator.fail_notification(
        db,
        {**fence, "failure_class": "ambiguous", "reason_code": "provider_timeout"},
        now="2026-07-23T12:00:02+00:00",
    )
    assert failed["state"] == "retry_wait"
    retry = orchestrator.claim_notification(db, WORKER, now=failed["not_before"])
    assert retry["notification"]["payload_sha256"] == first_payload
    assert retry["notification"]["provider_idempotency_key"] == first_key
    retry_fence = {
        "notification_id": retry["notification"]["notification_id"],
        "lease_generation": retry["notification"]["lease_generation"],
        "lease_token": retry["lease_token"],
    }
    orchestrator.begin_notification_dispatch(db, retry_fence, now=failed["not_before"])
    ack = orchestrator.acknowledge_notification(
        db, {**retry_fence, "provider_reference": "provider-message-1"},
        now=failed["not_before"],
    )
    assert ack["state"] == "sent"
    assert "provider-message-1" not in json.dumps(
        [dict(row) for row in db.execute("SELECT * FROM insight_notification_events")]
    )


def test_notification_five_claims_exhaust_without_external_sender(db):
    batch_id, synthesis_id = _seed_outbox_ancestry(db)
    orchestrator.enqueue_notification(
        db, _notification(), analysis_batch_id=batch_id,
        synthesis_id=synthesis_id, now=NOW,
    )
    current = datetime.fromisoformat("2026-07-23T12:00:00+00:00")
    waits = []
    last = None
    for attempt in range(1, 6):
        claim = orchestrator.claim_notification(db, WORKER, now=current)
        fence = {
            "notification_id": claim["notification"]["notification_id"],
            "lease_generation": claim["notification"]["lease_generation"],
            "lease_token": claim["lease_token"],
        }
        last = orchestrator.fail_notification(
            db,
            {
                **fence, "failure_class": "known_not_sent",
                "reason_code": "local_validation_failed",
            },
            now=current,
        )
        if attempt < 5:
            next_time = datetime.fromisoformat(last["not_before"])
            waits.append(int((next_time - current).total_seconds() / 60))
            current = next_time
    assert waits == [5, 10, 20, 40]
    assert last["state"] == "dead_letter"
    assert db.execute(
        "SELECT COUNT(*) FROM insight_notification_outbox WHERE attempt_count=5"
    ).fetchone()[0] == 1


def test_known_not_sent_class_is_closed_and_operator_resolution(db):
    batch_id, synthesis_id = _seed_outbox_ancestry(db)
    orchestrator.enqueue_notification(
        db, _notification(), analysis_batch_id=batch_id,
        synthesis_id=synthesis_id, now=NOW,
    )
    claim = orchestrator.claim_notification(db, WORKER, now="2026-07-23T12:00:00+00:00")
    fence = {
        "notification_id": claim["notification"]["notification_id"],
        "lease_generation": claim["notification"]["lease_generation"],
        "lease_token": claim["lease_token"],
    }
    with pytest.raises(orchestrator.OrchestrationError) as error:
        orchestrator.fail_notification(
            db,
            {
                **fence, "failure_class": "known_not_sent",
                "reason_code": "timeout",
            },
            now="2026-07-23T12:00:01+00:00",
        )
    assert error.value.validation
    orchestrator.begin_notification_dispatch(db, fence, now="2026-07-23T12:00:01+00:00")
    orchestrator.fail_notification(
        db,
        {**fence, "failure_class": "ambiguous", "reason_code": "provider_timeout"},
        now="2026-07-23T12:00:02+00:00",
    )
    resolved = orchestrator.resolve_notification(
        db,
        {
            "notification_id": fence["notification_id"],
            "resolution": "confirmed_not_sent_retry",
            "reason_code": "operator_confirmed_not_sent",
        },
        now="2026-07-23T12:05:00+00:00",
    )
    assert resolved["state"] == "retry_wait"


def test_read_only_status_redacts_tokens_payload_and_provider_key(db):
    batch_id, synthesis_id = _seed_outbox_ancestry(db)
    _trigger(db)
    orchestrator.enqueue_notification(
        db, _notification(mode="provider_key"), analysis_batch_id=batch_id,
        synthesis_id=synthesis_id, now=NOW,
    )
    status = orchestrator.insight_run_status(db, limit=10)
    serialized = json.dumps(status)
    assert status["read_only"] is True
    for forbidden in (
        "lease_token_sha256", "payload_json", "provider_idempotency_key",
        "narrative_md", "rendered_md",
    ):
        assert forbidden not in serialized or forbidden in status["redacted_fields"]
    before = db.total_changes
    orchestrator.insight_run_status(db, limit=10)
    assert db.total_changes == before


def test_json_parser_is_closed_bounded_and_duplicate_key_rejecting():
    assert orchestrator.parse_json_object('{"a":1}') == {"a": 1}
    with pytest.raises(orchestrator.OrchestrationError, match="duplicate"):
        orchestrator.parse_json_object('{"a":1,"a":2}')
    with pytest.raises(orchestrator.OrchestrationError, match="exactly one"):
        orchestrator.parse_json_object('{"a":1} {"b":2}')
    with pytest.raises(orchestrator.OrchestrationError, match="maximum"):
        orchestrator.parse_json_object('{"a":"' + "x" * 140_000 + '"}')
    with pytest.raises(orchestrator.OrchestrationError, match="non-finite"):
        orchestrator.parse_json_object('{"a":NaN}')
    with pytest.raises(orchestrator.OrchestrationError, match="UTF-8"):
        orchestrator.parse_json_object('{"a":"\ud800"}')
