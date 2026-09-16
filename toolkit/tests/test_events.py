"""Phase 2 lossless event, correction, completeness and validation contracts."""

import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
ENV_VERSION = "b" * 40


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "events.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    run(path, "migrate", "--to", "2", "--expected-from", "0")
    return path


def run(db, *args, stdin=None, expect=0):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args], input=stdin, text=True,
        capture_output=True, timeout=30,
        env={**os.environ, "HEALTH_DB": str(db), "HERMES_CODE_VERSION": ENV_VERSION},
    )
    assert result.returncode == expect, result.stderr or result.stdout
    return json.loads(result.stdout)


def capture(db, token="turn-1", raw="  Met Alice at café — felt great 😊\n"):
    payload = {
        "client_event_id": token, "event_date": "2026-07-20",
        "event_time": "18:45", "surface": "panel", "source": "chat-panel",
        "raw_text": raw,
    }
    return run(db, "capture-raw", "--stdin", stdin=json.dumps(payload, ensure_ascii=False))


def event_payload(capture_id, **changes):
    payload = {
        "date": "2026-07-20", "time": "18:45", "category": "social",
        "entity_label": "Alice", "value_text": "coffee", "value_num": None,
        "unit": None, "duration_min": 60, "intensity": 4, "valence": 2,
        "source": "chat-panel", "capture_id": capture_id, "note": "good talk",
    }
    payload.update(changes)
    return payload


def rows(db, sql, params=()):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(sql, params)]
    finally:
        con.close()


def test_capture_requires_recorded_phase2_migration_without_ddl(tmp_path):
    path = tmp_path / "unmigrated.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    before = con.execute("SELECT COUNT(*) FROM raw_capture_entries").fetchone()[0]
    con.close()
    result = run(path, "capture-raw", "--stdin", expect=1, stdin=json.dumps({
        "client_event_id": "unmigrated", "event_date": "2026-07-20",
        "event_time": None, "surface": "panel", "source": "chat-panel",
        "raw_text": "must not write",
    }))
    assert result["error"]["code"] == "schema_migration_required"
    assert rows(path, "SELECT COUNT(*) AS n FROM raw_capture_entries")[0]["n"] == before


def test_raw_capture_is_exact_idempotent_and_conflicts_on_changed_text(db):
    raw = "  Met Alice at café — felt great 😊\n"
    first = capture(db, raw=raw)
    retry = capture(db, raw=raw)
    assert retry == {**first, "idempotent": True}
    stored = rows(db, "SELECT raw_text,raw_text_sha256 FROM raw_capture_entries")[0]
    assert stored == {"raw_text": raw, "raw_text_sha256": hashlib.sha256(raw.encode()).hexdigest()}
    conflict = run(
        db, "capture-raw", "--stdin", expect=1,
        stdin=json.dumps({
            "client_event_id": "turn-1", "event_date": "2026-07-20",
            "event_time": "18:45", "surface": "panel", "source": "chat-panel",
            "raw_text": "changed",
        }),
    )
    assert conflict["error"]["code"] == "idempotency_conflict"
    assert rows(db, "SELECT COUNT(*) AS n FROM raw_capture_entries")[0]["n"] == 1


def test_event_link_is_lossless_and_retry_reports_linked(db):
    cap = capture(db)
    logged = run(db, "event-log", "--stdin", stdin=json.dumps(event_payload(cap["capture_id"])))
    event = rows(db, "SELECT * FROM event_exposures")[0]
    assert logged["entity_label"] == event["entity_label"] == "Alice"
    assert event["raw_text"] == "  Met Alice at café — felt great 😊\n"
    assert event["raw_ref"] == cap["capture_id"]
    resolutions = rows(db, "SELECT status,target_table,target_row_key,supersedes_id FROM raw_capture_resolutions ORDER BY id")
    assert [row["status"] for row in resolutions] == ["pending", "linked"]
    assert resolutions[1]["target_table"] == "event_exposures"
    assert capture(db)["status"] == "linked"
    assert len(rows(db, "SELECT * FROM raw_capture_resolutions")) == 2


def test_event_link_requires_capture_date_and_time_match(db):
    cap = capture(db)
    for changes in ({"date": "2026-07-21"}, {"time": "18:46"}):
        result = run(
            db, "event-log", "--stdin", expect=2,
            stdin=json.dumps(event_payload(cap["capture_id"], **changes)),
        )
        assert result["error"]["code"] == "validation_error"
    assert rows(db, "SELECT * FROM event_exposures") == []


def test_conversational_event_requires_capture_and_rejects_unknown_json(db):
    missing = event_payload(None)
    result = run(db, "event-log", "--stdin", stdin=json.dumps(missing), expect=2)
    assert result["error"]["code"] == "validation_error"
    cap = capture(db)
    unknown = event_payload(cap["capture_id"], invented=True)
    result = run(db, "event-log", "--stdin", stdin=json.dumps(unknown), expect=2)
    assert "unknown" in result["error"]["message"]
    assert rows(db, "SELECT * FROM event_exposures") == []


def test_new_cli_rejects_repeated_flags_as_json(db):
    result = run(db, "events", "--days", "7", "--days", "30", expect=2)
    assert result["error"]["code"] == "validation_error"
    assert "repeated flag" in result["error"]["message"]


@pytest.mark.parametrize("event_id", ["0", "-1"])
def test_correction_and_void_reject_nonpositive_ids_before_write(db, event_id):
    voided = run(db, "event-void", event_id, "--reason", "invalid", expect=2)
    assert voided["error"]["code"] == "validation_error"
    corrected = run(db, "event-correct", event_id, "--stdin", expect=2, stdin="{}")
    assert corrected["error"]["code"] == "validation_error"
    assert rows(db, "SELECT * FROM event_exposures") == []


def test_atomic_correction_preserves_history_and_refuses_reuse(db):
    first_cap = capture(db, "turn-original")
    original = run(db, "event-log", "--stdin", stdin=json.dumps(event_payload(first_cap["capture_id"])))
    correction_cap = capture(db, "turn-correction", "Correction: it was Alicia, not Alice")
    corrected_payload = event_payload(
        correction_cap["capture_id"], entity_label="Alicia", reason="owner correction",
    )
    corrected = run(
        db, "event-correct", str(original["event_id"]), "--stdin",
        stdin=json.dumps(corrected_payload),
    )
    events = rows(db, "SELECT id,entity_label,supersedes_id,voided,void_reason FROM event_exposures ORDER BY id")
    assert events == [
        {"id": original["event_id"], "entity_label": "Alice", "supersedes_id": None,
         "voided": 1, "void_reason": "owner correction"},
        {"id": corrected["replacement_event_id"], "entity_label": "Alicia",
         "supersedes_id": original["event_id"], "voided": 0, "void_reason": None},
    ]
    third_cap = capture(db, "turn-third", "another correction")
    failed = run(
        db, "event-correct", str(original["event_id"]), "--stdin", expect=1,
        stdin=json.dumps(event_payload(third_cap["capture_id"], reason="again")),
    )
    assert failed["error"]["code"] == "invalid_state"
    assert len(rows(db, "SELECT * FROM event_exposures")) == 2


def test_correction_rolls_back_insert_and_void_when_capture_is_terminal(db):
    first_cap = capture(db, "turn-original")
    original = run(db, "event-log", "--stdin", stdin=json.dumps(event_payload(first_cap["capture_id"])))
    bad_cap = capture(db, "turn-cancelled", "never mind")
    pending_id = rows(
        db, "SELECT id FROM raw_capture_resolutions WHERE capture_id=? ORDER BY id DESC LIMIT 1",
        (bad_cap["capture_id"],),
    )[0]["id"]
    run(db, "capture-resolve", "--stdin", stdin=json.dumps({
        "capture_id": bad_cap["capture_id"], "status": "cancelled",
        "reason_code": "owner_cancelled", "source": "chat-panel",
        "supersedes_id": pending_id,
    }))
    failed = run(
        db, "event-correct", str(original["event_id"]), "--stdin", expect=1,
        stdin=json.dumps(event_payload(bad_cap["capture_id"], reason="should rollback")),
    )
    assert failed["error"]["code"] == "invalid_state"
    assert rows(db, "SELECT id,voided FROM event_exposures") == [
        {"id": original["event_id"], "voided": 0}
    ]


def test_void_is_one_way_and_events_read_has_explicit_coverage(db):
    cap = capture(db)
    event_id = run(db, "event-log", "--stdin", stdin=json.dumps(event_payload(cap["capture_id"]))) ["event_id"]
    assert run(db, "event-void", str(event_id), "--reason", "duplicate")["voided"] is True
    again = run(db, "event-void", str(event_id), "--reason", "again", expect=1)
    assert again["error"]["code"] == "invalid_state"
    result = run(db, "events", "--all")
    assert result["events"] == []
    assert result["coverage"] == {"active_count": 0, "by_category": {}}
    assert result["meta"]["range_kind"] == "all"


def test_explicit_none_conflict_and_atomic_invalidation(db):
    none = run(
        db, "capture-completeness-set", "--date", "2026-07-20",
        "--scope", "social", "--state", "complete", "--explicit-none",
        "--source", "manual",
    )
    assert none["structural_zero_eligible"] is True
    cap = capture(db)
    run(db, "event-log", "--stdin", stdin=json.dumps(event_payload(cap["capture_id"])))
    effective = run(db, "capture-completeness", "--all", "--scope", "social")["effective"]
    assert len(effective) == 1
    assert effective[0]["state"] == "partial" and effective[0]["explicit_none"] == 0
    assert effective[0]["observation_count"] == 1
    conflict = run(
        db, "capture-completeness-set", "--date", "2026-07-20",
        "--scope", "social", "--state", "complete", "--explicit-none",
        "--source", "manual", expect=1,
    )
    assert conflict["error"]["code"] == "conflicting_observation"


def test_nutrition_total_explicit_none_rejects_existing_rows(db):
    con = sqlite3.connect(db)
    con.execute("""INSERT INTO nutrient_daily(date,nutrient,amount,unit,source)
        VALUES('2026-07-20','Energy',2200,'kcal','cronometer')""")
    con.commit(); con.close()
    conflict = run(
        db, "capture-completeness-set", "--date", "2026-07-20",
        "--scope", "nutrition_total", "--state", "complete", "--explicit-none",
        "--source", "manual", expect=1,
    )
    assert conflict["error"]["code"] == "conflicting_observation"


@pytest.mark.parametrize("field,value", [
    ("duration_min", 10081), ("intensity", 0), ("valence", 3),
    ("value_num", float("nan")), ("time", "24:00"),
])
def test_event_validation_bounds_leave_no_partial_rows(db, field, value):
    cap = capture(db, token=f"bad-{field}")
    payload = event_payload(cap["capture_id"], **{field: value})
    result = run(db, "event-log", "--stdin", stdin=json.dumps(payload), expect=2)
    assert result["error"]["code"] == "validation_error"
    assert rows(db, "SELECT * FROM event_exposures") == []
