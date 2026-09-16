"""Phase 3 append-only goals, collector provenance, and plan revision commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
CODE_VERSION = "c" * 40


def run(db: Path, *args: str, stdin: str | None = None, expect: int = 0):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args], input=stdin, text=True,
        capture_output=True, timeout=60,
        env={**os.environ, "HEALTH_DB": str(db), "HERMES_CODE_VERSION": CODE_VERSION},
    )
    assert result.returncode == expect, result.stderr or result.stdout
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else result


def rows(db: Path, sql: str, params=()):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in c.execute(sql, params)]
    finally:
        c.close()


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "phase3.db"
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO training_schedule(weekday,routine_name) VALUES('Mon','Base')")
    c.execute("""INSERT INTO routines(
        routine_name,exercise_title,ex_order,target_sets,target_reps,target_weight_kg)
        VALUES('Base','Row',1,3,8,50)""")
    c.commit(); c.close()
    run(path, "migrate", "--to", "3", "--expected-from", "0")
    return path


def test_goal_defaults_and_append_only_revisions(db: Path):
    base = run(db, "goal-list")
    assert [(item["key"], item["enabled"], item["outcome_key"])
            for item in base["goals"]] == [
        ("green_days", True, "subjective.day_rating")]
    all_goals = run(db, "goal-list", "--all")["goals"]
    assert len(all_goals) == 8
    assert all(not item["enabled"] for item in all_goals if item["key"] != "green_days")
    assert next(item for item in all_goals if item["key"] == "pain_reduction")["outcome_key"] is None

    first = run(
        db, "goal-set", "energy_focus_mood", "--enabled", "1", "--priority", "4",
        "--outcome", "subjective.energy", "--direction", "increase", "--source", "owner-ssh",
    )
    assert first["revision_id"] > 0 and first["supersedes_id"] is None
    second = run(
        db, "goal-set", "energy_focus_mood", "--enabled", "0", "--priority", "2",
        "--source", "owner-ssh",
    )
    assert second["supersedes_id"] == first["revision_id"]
    history = rows(db, "SELECT * FROM insight_goal_revisions ORDER BY id")
    assert len(history) == 2
    assert history[0]["enabled"] == 1 and history[1]["enabled"] == 0
    assert history[0]["outcome_key"] == history[1]["outcome_key"] == "subjective.energy"


def test_goal_set_rejects_invention_incomplete_pairs_and_base_disable(db: Path):
    invalid = run(
        db, "goal-set", "pain_reduction", "--enabled", "1", "--priority", "5",
        "--outcome", "pain.nrs.knee.left", "--direction", "decrease",
        "--source", "owner-ssh", expect=2,
    )
    assert invalid["error"]["code"] == "validation_error"
    incomplete = run(
        db, "goal-set", "energy_focus_mood", "--enabled", "1", "--priority", "3",
        "--outcome", "subjective.energy", "--source", "owner-ssh", expect=2,
    )
    assert incomplete["error"]["code"] == "validation_error"
    disabled = run(
        db, "goal-set", "green_days", "--enabled", "0", "--priority", "5",
        "--source", "owner-ssh", expect=2,
    )
    assert disabled["error"]["code"] == "validation_error"
    assert rows(db, "SELECT * FROM insight_goal_revisions") == []


def collector_payload(**changes):
    payload = {
        "source": "hevy", "started_at": "2026-07-22T01:00:00+00:00",
        "completed_at": "2026-07-22T01:01:00+00:00", "status": "success",
        "coverage_from": "2026-07-21", "coverage_to": "2026-07-21",
        "rows_seen": 12, "rows_written": 12, "error_code": None,
        "warning_codes": ["template_skipped", "template_skipped", "body_missing"],
    }
    payload.update(changes)
    return payload


def test_collector_run_exact_secret_free_idempotent_contract(db: Path):
    payload = collector_payload()
    first = run(db, "collector-run-record", "--stdin", stdin=json.dumps(payload))
    retry = run(db, "collector-run-record", "--stdin", stdin=json.dumps(payload))
    assert first["idempotent"] is False and retry == {**first, "idempotent": True}
    stored = rows(db, "SELECT * FROM source_sync_runs")[0]
    assert json.loads(stored["details_json"]) == {
        "warning_codes": ["body_missing", "template_skipped"]}
    conflict = run(
        db, "collector-run-record", "--stdin",
        stdin=json.dumps(collector_payload(rows_written=11)), expect=1,
    )
    assert conflict["error"]["code"] == "idempotency_conflict"
    for bad in (
        {**payload, "secret": "no"},
        {**payload, "warning_codes": ["https://secret.example/token"]},
        {**payload, "status": "success", "error_code": "failed"},
        {**payload, "coverage_to": None},
    ):
        result = run(db, "collector-run-record", "--stdin", stdin=json.dumps(bad), expect=2)
        assert result["error"]["code"] == "validation_error"
    assert len(rows(db, "SELECT * FROM source_sync_runs")) == 1


def test_collector_freshness_uses_latest_success_not_later_failure(db: Path):
    run(db, "collector-run-record", "--stdin", stdin=json.dumps(collector_payload()))
    sys.path.insert(0, str(ROOT))
    try:
        from hermes_insights.goals import collector_freshness
        c = sqlite3.connect(db); c.row_factory = sqlite3.Row
        successful_at = datetime(2026, 7, 22, 1, tzinfo=timezone.utc)
        result = collector_freshness(c, "hevy", now=successful_at + timedelta(hours=31))
        assert result["status"] == "late"
        c.execute("""INSERT INTO source_sync_runs(
          source,started_at,completed_at,status,error_code,details_json)
          VALUES('hevy','2026-07-22T02:00:00+00:00','2026-07-22T02:01:00+00:00',
                 'failed','api_failed','{}')""")
        c.commit()
        result = collector_freshness(c, "hevy", now=successful_at + timedelta(hours=49))
        assert result["status"] == "stale"
        assert result["run_status"] == "failed"
        assert result["successful_run_id"] == 1
        c.close()
    finally:
        sys.path.remove(str(ROOT))


def test_all_owner_plan_mutations_append_full_post_change_snapshot(db: Path):
    initial = rows(db, "SELECT * FROM training_plan_revisions")
    assert len(initial) == 1
    run(db, "schedule-set", "Tue", "Base")
    run(db, "routine-set", "Base", "Press", "--sets", "4", "--reps", "6")
    run(db, "routine-remove", "Base", "Press")
    run(db, "routine-undo")
    revisions = rows(db, "SELECT * FROM training_plan_revisions ORDER BY id")
    assert len(revisions) == 5
    assert [row["source"] for row in revisions] == [
        "migration-003", "schedule-set", "routine-set", "routine-remove", "routine-undo"]
    assert [row["supersedes_id"] for row in revisions] == [None, 1, 2, 3, 4]
    latest_routines = json.loads(revisions[-1]["routines_json"])
    assert any(row["exercise_title"] == "Press" for row in latest_routines)
    latest_schedule = json.loads(revisions[-1]["schedule_json"])
    assert {row["weekday"]: row["routine_name"] for row in latest_schedule} == {
        "Mon": "Base", "Tue": "Base"}


def test_plan_snapshot_failure_rolls_back_owner_config_mutation(db: Path):
    c = sqlite3.connect(db)
    c.execute("""CREATE TRIGGER reject_plan BEFORE INSERT ON training_plan_revisions
      BEGIN SELECT RAISE(ABORT,'snapshot blocked'); END""")
    before = list(c.execute("SELECT * FROM training_schedule ORDER BY weekday"))
    history_before = c.execute("SELECT COUNT(*) FROM routines_history").fetchone()[0]
    c.commit(); c.close()
    run(db, "schedule-set", "Wed", "Base", expect=1)
    c = sqlite3.connect(db)
    assert list(c.execute("SELECT * FROM training_schedule ORDER BY weekday")) == before
    assert c.execute("SELECT COUNT(*) FROM routines_history").fetchone()[0] == history_before
    c.close()


@pytest.mark.parametrize("version", [0, 2])
def test_plan_commands_remain_compatible_before_migration3(tmp_path: Path, version: int):
    path = tmp_path / f"legacy-{version}.db"
    c = sqlite3.connect(path); c.executescript(SCHEMA); c.commit(); c.close()
    if version:
        run(path, "migrate", "--to", str(version), "--expected-from", "0")
    run(path, "schedule-set", "Mon", "Legacy")
    assert rows(path, "SELECT weekday,routine_name FROM training_schedule") == [
        {"weekday": "Mon", "routine_name": "Legacy"}]
    assert rows(path, "SELECT * FROM training_plan_revisions") == []
