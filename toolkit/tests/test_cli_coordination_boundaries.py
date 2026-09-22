"""Characterize atomic Phase 2 event-trigger coordination at the CLI boundary."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEALTH = ROOT / "toolkit" / "health.py"
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()
ENV_VERSION = "b" * 40
if str(ROOT / "toolkit") not in sys.path:
    sys.path.insert(0, str(ROOT / "toolkit"))
from hermes_insights import orchestrator


@pytest.fixture()
def database(tmp_path):
    path = tmp_path / "event-coordination.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
    run(path, "migrate", "--to", "4", "--expected-from", "0")
    return path


def run(database, *args, stdin=None, expect=0):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=30,
        env={
            **os.environ,
            "HEALTH_DB": str(database),
            "HERMES_CODE_VERSION": ENV_VERSION,
        },
    )
    assert result.returncode == expect, result.stderr or result.stdout
    return json.loads(result.stdout)


def rows(database, sql, params=()):
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def capture(database, token, *, date="2026-07-20", time="18:45"):
    return run(
        database,
        "capture-raw",
        "--stdin",
        stdin=json.dumps(
            {
                "client_event_id": token,
                "event_date": date,
                "event_time": time,
                "surface": "ssh",
                "source": "manual",
                "raw_text": f"fictional capture {token}",
            }
        ),
    )


def event_payload(capture_id, *, category="social", entity_label="Alice", reason=None):
    payload = {
        "date": "2026-07-20",
        "time": "18:45",
        "category": category,
        "entity_label": entity_label,
        "value_text": "fictional value",
        "value_num": None,
        "unit": None,
        "duration_min": 60,
        "intensity": 4,
        "valence": 2,
        "source": "manual",
        "capture_id": capture_id,
        "note": "fictional note",
    }
    if reason is not None:
        payload["reason"] = reason
    return payload


def mark_medication_complete(database):
    return run(
        database,
        "capture-completeness-set",
        "--date",
        "2026-07-20",
        "--scope",
        "medication",
        "--state",
        "complete",
        "--explicit-none",
        "--source",
        "manual",
    )


def seed_conflicting_trigger(database, source_row_key):
    identity = orchestrator.trigger_identity(
        "medication_regime_change",
        "event_exposures",
        source_row_key,
        "2026-07-20",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO insight_triggers(
                trigger_id,trigger_kind,source_table,source_row_key,event_date,
                dedupe_key,state,not_before,attempt_count,max_attempts,
                lease_generation,lease_owner,lease_token_sha256,lease_expires_at,
                last_error_code,analysis_batch_id,synthesis_id,processed_at,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,'pending',?,0,5,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,?)
            """,
            (
                identity["trigger_id"],
                "medication_regime_change",
                "event_exposures",
                "conflicting-source",
                "2026-07-20",
                identity["dedupe_key"],
                "2026-07-20T00:00:00+00:00",
                "2026-07-20T00:00:00+00:00",
                "2026-07-20T00:00:00+00:00",
            ),
        )


def test_successful_medication_correction_queues_replacement_event_id(database):
    original_capture = capture(database, "original-event")
    original = run(
        database,
        "event-log",
        "--stdin",
        stdin=json.dumps(event_payload(original_capture["capture_id"])),
    )
    correction_capture = capture(database, "medication-correction")

    corrected = run(
        database,
        "event-correct",
        str(original["event_id"]),
        "--stdin",
        stdin=json.dumps(
            event_payload(
                correction_capture["capture_id"],
                category="medication_change",
                entity_label="fictional medication",
                reason="owner correction",
            )
        ),
    )

    triggers = rows(
        database,
        "SELECT source_row_key,source_table,event_date FROM insight_triggers",
    )
    assert triggers == [
        {
            "source_row_key": str(corrected["replacement_event_id"]),
            "source_table": "event_exposures",
            "event_date": "2026-07-20",
        }
    ]
    assert rows(
        database,
        "SELECT id,voided FROM event_exposures ORDER BY id",
    ) == [
        {"id": original["event_id"], "voided": 1},
        {"id": corrected["replacement_event_id"], "voided": 0},
    ]


def test_failed_medication_event_leaves_capture_and_completeness_unchanged(database):
    mark_medication_complete(database)
    medication_capture = capture(database, "medication-event")
    seed_conflicting_trigger(database, "1")

    failed = run(
        database,
        "event-log",
        "--stdin",
        expect=1,
        stdin=json.dumps(
            event_payload(
                medication_capture["capture_id"],
                category="medication_change",
                entity_label="fictional medication",
            )
        ),
    )

    assert failed["error"]["code"] == "idempotency_conflict"
    assert rows(database, "SELECT id FROM event_exposures") == []
    assert rows(
        database,
        "SELECT status,target_table,target_row_key FROM raw_capture_resolutions",
    ) == [{"status": "pending", "target_table": None, "target_row_key": None}]
    assert rows(
        database,
        "SELECT state,explicit_none,supersedes_id FROM capture_completeness_revisions",
    ) == [{"state": "complete", "explicit_none": 1, "supersedes_id": None}]
    assert rows(
        database,
        "SELECT source_row_key,state FROM insight_triggers",
    ) == [{"source_row_key": "conflicting-source", "state": "pending"}]


def test_failed_medication_correction_keeps_original_event_active(database):
    original_capture = capture(database, "original-social")
    original = run(
        database,
        "event-log",
        "--stdin",
        stdin=json.dumps(event_payload(original_capture["capture_id"])),
    )
    mark_medication_complete(database)
    correction_capture = capture(database, "failed-medication-correction")
    seed_conflicting_trigger(database, "2")

    failed = run(
        database,
        "event-correct",
        str(original["event_id"]),
        "--stdin",
        expect=1,
        stdin=json.dumps(
            event_payload(
                correction_capture["capture_id"],
                category="medication_change",
                entity_label="fictional medication",
                reason="owner correction",
            )
        ),
    )

    assert failed["error"]["code"] == "idempotency_conflict"
    assert rows(
        database,
        "SELECT id,voided,supersedes_id FROM event_exposures",
    ) == [{"id": original["event_id"], "voided": 0, "supersedes_id": None}]
    assert rows(
        database,
        "SELECT status,target_table,target_row_key FROM raw_capture_resolutions "
        "WHERE capture_id=?",
        (correction_capture["capture_id"],),
    ) == [{"status": "pending", "target_table": None, "target_row_key": None}]
    assert rows(
        database,
        "SELECT state,explicit_none,supersedes_id FROM capture_completeness_revisions "
        "ORDER BY id",
    ) == [{"state": "complete", "explicit_none": 1, "supersedes_id": None}]
    assert rows(
        database,
        "SELECT source_row_key,state FROM insight_triggers",
    ) == [{"source_row_key": "conflicting-source", "state": "pending"}]
