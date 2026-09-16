"""Clean-install and synthetic demonstration acceptance tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
    )


def test_empty_initialization_is_external_private_and_idempotence_safe(tmp_path):
    data_dir = tmp_path / "hermes-data"
    first = run("scripts/init_hermes.py", "--data-dir", str(data_dir))
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["ok"] is True and result["empty"] is True
    assert result["public_submuscle_rows"] == 42

    health = sqlite3.connect(data_dir / "health.db")
    panel = sqlite3.connect(data_dir / "panel.db")
    try:
        assert health.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 7
        assert health.execute("SELECT COUNT(*) FROM recipe_restock_state").fetchone()[0] == 0
        assert health.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0] == 0
        assert health.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 0
        assert health.execute("SELECT COUNT(*) FROM exercise_submuscles").fetchone()[0] == 42
        assert panel.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 0
        assert panel.execute("SELECT COUNT(*) FROM chat_conversations").fetchone()[0] == 0
    finally:
        health.close()
        panel.close()

    second = run("scripts/init_hermes.py", "--data-dir", str(data_dir))
    assert second.returncode != 0
    assert "refusing to overwrite" in second.stderr


def _normalized_demo(path: Path) -> dict:
    connection = sqlite3.connect(path)
    try:
        return {
            "versions": connection.execute(
                "SELECT version,name,checksum_sha256,code_version FROM schema_migrations ORDER BY version"
            ).fetchall(),
            "daily": connection.execute(
                "SELECT * FROM daily_metrics ORDER BY date,source"
            ).fetchall(),
            "subjective": connection.execute(
                "SELECT * FROM subjective_daily ORDER BY date"
            ).fetchall(),
            "fitness": connection.execute(
                "SELECT date,movement,side,load_kg,reps,seconds,degrees,passed,cm,source "
                "FROM fitness_tests ORDER BY date,movement,side"
            ).fetchall(),
            "submuscles": connection.execute(
                "SELECT exercise_title,muscle_group,sub_region,weight,laterality,source,"
                "approx,iso,confidence FROM exercise_submuscles "
                "ORDER BY exercise_title,muscle_group,sub_region,laterality"
            ).fetchall(),
        }
    finally:
        connection.close()

def test_demo_is_fictional_migrated_and_reproducible_for_fixed_anchor(tmp_path):
    left = tmp_path / "left.db"
    right = tmp_path / "right.db"
    reports = []
    for target in (left, right):
        result = run(
            "scripts/make_demo_db.py",
            "--output", str(target),
            "--anchor-date", "2026-06-30",
        )
        assert result.returncode == 0, result.stderr
        reports.append(json.loads(result.stdout))
    assert _normalized_demo(left) == _normalized_demo(right)
    for report in reports:
        assert report["contract"] == "openhealthatlas-fictional-persona-v1"
        assert report["fixture_id"] == "comprehensive-persona-v1"
        assert report["data_class"] == "fictional"
        assert report["range_from"] == "2026-03-02"
        assert report["range_to"] == "2026-06-30"
        assert report["scheduled_training_days"] == ["Mon", "Wed", "Fri"]
        assert all(count > 0 for count in report["collected_domain_rows"].values())
        assert all(count == 0 for count in report["system_generated_rows"].values())

    connection = sqlite3.connect(left)
    try:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 7
        drugs = {row[0] for row in connection.execute("SELECT DISTINCT drug FROM meds_log")}
        assert drugs == {"medication"}
        assert connection.execute("SELECT COUNT(*) FROM exercise_submuscles").fetchone()[0] == 42
        assert connection.execute("SELECT COUNT(*) FROM routines").fetchone()[0] == 15
        assert connection.execute("SELECT COUNT(*) FROM hevy_sets").fetchone()[0] > 700
        assert connection.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM nutrition_log").fetchone()[0] > 400
        assert connection.execute("SELECT COUNT(*) FROM intake").fetchone()[0] > 100
        assert connection.execute("SELECT COUNT(*) FROM sleep_log").fetchone()[0] > 100
        assert connection.execute("SELECT COUNT(*) FROM supplements_log").fetchone()[0] > 300
        assert connection.execute("SELECT COUNT(*) FROM skincare_log").fetchone()[0] > 100
        schedule = connection.execute(
            "SELECT weekday,routine_name FROM training_schedule ORDER BY weekday"
        ).fetchall()
        assert schedule == [
            ("Fri", "Fictional Full Body C"),
            ("Mon", "Fictional Full Body A"),
            ("Wed", "Fictional Full Body B"),
        ]
        for table in (
            "analysis_batches", "analysis_findings", "hypotheses",
            "synthesis_runs", "insight_notification_outbox",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()

    environment = {
        **os.environ,
        "HEALTH_DB": str(left),
        "HERMES_TIMEZONE": "UTC",
    }
    # Recipe storage must agree with the retained menu/eat calculation path,
    # not merely with the precomputed historical nutrition_log fixture rows.
    menu = subprocess.run(
        [sys.executable, "toolkit/health.py", "menu"], cwd=ROOT,
        env=environment, text=True, capture_output=True, timeout=30,
    )
    assert menu.returncode == 0, menu.stderr
    oats = next(item for item in json.loads(menu.stdout)["menu"]
                if item["recipe"] == "Fictional Berry Oats")
    assert (oats["kcal_per_portion"], oats["protein_g"]) == (510, 24)
    eaten = subprocess.run(
        [sys.executable, "toolkit/health.py", "eat", "Fictional Berry Oats",
         "--date", "2026-06-30", "--source", "panel-ui"],
        cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30,
    )
    assert eaten.returncode == 0, eaten.stderr
    meal = json.loads(eaten.stdout)
    assert (meal["kcal"], meal["protein_g"], meal["portions_left"]) == (510, 24, 2)
    with sqlite3.connect(left) as logged:
        assert logged.execute(
            "SELECT kcal,protein_g,carbs_g,fat_g,fiber_g FROM nutrition_log "
            "WHERE source='panel-ui'"
        ).fetchone() == (510, 24, 72, 14, 11)

    for family in ("sleep", "wearable", "training", "recovery", "subjective"):
        framed = subprocess.run(
            [
                sys.executable, "toolkit/health.py", "feature-frame",
                "--from", "2026-05-17", "--to", "2026-06-30",
                "--family", family, "--include-provenance",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert framed.returncode == 0, framed.stderr
        payload = json.loads(framed.stdout)
        assert payload["ok"] is True
        assert payload["observations"], family

    recovery = subprocess.run(
        [
            sys.executable, "toolkit/health.py", "readiness",
            "--from", "2026-03-02", "--anchor", "2026-06-30",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert recovery.returncode == 0, recovery.stderr
    recovery_payload = json.loads(recovery.stdout)
    assert recovery_payload["status"] == "insufficient_data"
    assert "score" not in recovery_payload
    assert "band" not in recovery_payload
    assert recovery_payload["anchor_date"] == "2026-06-30"
    assert recovery_payload["range_from"] == "2026-03-02"
    assert {row["key"] for row in recovery_payload["components"]} == {"sleep"}
    excluded = {
        row["key"]: row
        for row in recovery_payload["evidence"]["components"]
        if row["status"] == "excluded"
    }
    assert set(excluded) == {"hrv", "rhr"}
    assert all(
        row["reason_code"] == "insufficient_same_source_baseline"
        and row["baseline"]["source_label"] == "fitbit"
        and row["baseline"]["observation_count"] == 10
        for row in excluded.values()
    )
    assert any(row["sore"] for row in recovery_payload["muscle_recovery"])
