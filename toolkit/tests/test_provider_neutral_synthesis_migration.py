"""Migration 006 provider-neutral synthesis provenance contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_VERSION = "6" * 40
sys.path.insert(0, str(ROOT))

import hermes_insights.migrations as migrations  # noqa: E402
from hermes_insights.migrations import (  # noqa: E402
    AUTONOMOUS_SCHEMA_VERSION,
    MIGRATION_006_SQL,
    MIGRATION_BY_VERSION,
    HYPOTHESIS_ANNOTATIONS_V6_DDL,
    PHASE5_TABLE_DDL,
    SchemaError,
    migrate,
    schema_plan,
    schema_status,
    SYNTHESIS_RUNS_V6_DDL,
)


def _schema_form(ddl: str) -> str:
    return ddl.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1)


def _legacy_phase5_schema(schema: str) -> str:
    replacements = (
        (
            r"CREATE TABLE synthesis_runs \(.*?\n\);\n+CREATE TABLE synthesis_analysis_runs",
            _schema_form(PHASE5_TABLE_DDL["synthesis_runs"])
            + "\n\nCREATE TABLE synthesis_analysis_runs",
        ),
        (
            r"CREATE TABLE hypothesis_annotations \(.*?\n\);\n+CREATE INDEX hypothesis_annotations_latest_idx",
            _schema_form(PHASE5_TABLE_DDL["hypothesis_annotations"])
            + "\n\nCREATE INDEX hypothesis_annotations_latest_idx",
        ),
    )
    for pattern, replacement in replacements:
        schema, count = re.subn(pattern, replacement, schema, count=1, flags=re.DOTALL)
        assert count == 1
    return schema


CURRENT_SCHEMA = (ROOT / "SCHEMA.sql").read_text(encoding="utf-8")
SCHEMA = _legacy_phase5_schema(CURRENT_SCHEMA)


FROZEN_MIGRATION_006_CHECKSUM = (
    "55137d10cf7e860df8d09b917eb738f01049cb113259db62b8d7275042319c34"
)


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _v5_database(path: Path) -> Path:
    connection = _connection(path)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()
    assert migrate(str(path), 5, 0, CODE_VERSION)["to_version"] == 5
    return path


def _insert_legacy_synthesis(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO analysis_batches(
          batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
          range_plan_version,outcome_selection,outcome_set_sha256,
          analysis_version,registry_version,engine_sha256,registry_sha256,
          status,status_reason_code,run_count,completed_count,insufficient_count,
          no_data_count,failed_count,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "sha256:" + "1" * 64, "legacy-batch", "manual", "2026-07-22",
            "fixture", "analysis-range-plan-v1", "explicit_set",
            "sha256:" + "2" * 64, "outcome-v1", "feature-registry-v1",
            "sha256:" + "3" * 64, "sha256:" + "4" * 64, "completed", None,
            0, 0, 0, 0, 0, "2026-07-23T08:00:00+00:00",
            "2026-07-23T08:00:01+00:00",
        ),
    )
    connection.execute(
        """INSERT INTO analysis_range_requests(
          range_id,batch_id,range_role,requested_range_kind,requested_from,requested_to
        ) VALUES(?,?,?,?,?,?)""",
        (
            "legacy-range", "sha256:" + "1" * 64, "primary", "bounded",
            "2026-01-01", "2026-06-30",
        ),
    )
    connection.execute(
        """INSERT INTO analysis_runs(
          run_id,batch_id,range_id,outcome_key,outcome_mode,range_resolution,
          analysis_from,analysis_to,baseline_from,baseline_to,input_fingerprint,
          status,result_json,result_sha256,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-run", "sha256:" + "1" * 64, "legacy-range",
            "subjective.day_rating", "ordinal", "bounded_exact", "2026-01-01",
            "2026-06-30", "2025-12-25", "2026-06-30", "legacy-input",
            "completed", "{}", "legacy-result", "2026-07-23T08:00:00+00:00",
            "2026-07-23T08:00:01+00:00",
        ),
    )
    connection.execute(
        """INSERT INTO analysis_findings(
          finding_id,run_id,candidate_key,candidate_kind,outcome_key,outcome_mode,
          direction,quality_tier,eligible_for_hypothesis,evidence_json,
          evidence_for_json,evidence_against_json,evidence_fingerprint
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-finding", "legacy-run", "sleep.duration_hours", "single",
            "subjective.day_rating", "ordinal", "positive",
            "exploratory_unreplicated", 1, "{}", "[]", "[]",
            "legacy-evidence",
        ),
    )
    connection.execute(
        """INSERT INTO hypotheses(
          hypothesis_id,dedupe_key,outcome_key,outcome_mode,candidate_kind,
          initial_direction,first_seen,created_at,created_by_run,created_by_finding
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-hypothesis", "legacy-hypothesis-dedupe",
            "subjective.day_rating", "ordinal", "single", "positive",
            "2026-01-01", "2026-07-23T08:00:01+00:00", "legacy-run",
            "legacy-finding",
        ),
    )
    evaluation = connection.execute(
        """INSERT INTO hypothesis_evaluations(
          hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,evidence_class,
          evidence_for_json,evidence_against_json,confounders_json,sample_size_json,
          effect_summary_json,stability_json,confidence,status,change_reason,
          change_conditions,source_analysis_version,input_fingerprint,
          evidence_fingerprint,new_eligible_observations,compatible_with_prior,
          transition_applied,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-hypothesis", "legacy-run", "legacy-finding",
            "2026-07-23T08:00:01+00:00", "2026-01-01", "2026-06-30",
            "initial_discovery_pass", "[]", "[]", "{}", "{}", "{}", "{}",
            "low", "candidate", "initial discovery", "new evidence", "outcome-v1",
            "legacy-input", "legacy-evidence", 30, 1, 1,
            "2026-07-23T08:00:01+00:00",
        ),
    )
    connection.execute(
        """INSERT INTO synthesis_runs(
          synthesis_id,analysis_batch_id,cadence,reason_code,cutoff_date,
          evidence_fingerprint,context_version,prompt_sha256,model_id,provider,
          finding_ids_json,hypothesis_ids_json,narrative_md,rendered_md,status,
          no_message_reason_code,created_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "sha256:" + "5" * 64, "sha256:" + "1" * 64, "manual",
            "fixture_review", "2026-07-22", "sha256:" + "6" * 64, "2",
            "sha256:" + "7" * 64, "z-ai/glm-5.2", "openrouter", "[]", "[]",
            "Legacy bounded interpretation.", "Legacy rendered output.",
            "completed", None, "2026-07-23T09:00:00+00:00",
            "2026-07-23T09:00:00+00:00",
        ),
    )
    connection.execute(
        """INSERT INTO synthesis_analysis_runs(synthesis_id,run_id,purpose)
           VALUES(?,?,?)""",
        ("sha256:" + "5" * 64, "legacy-run", "primary"),
    )
    connection.execute(
        """INSERT INTO hypothesis_annotations(
          annotation_id,hypothesis_id,evaluation_id,annotation_kind,content,source,
          synthesis_id,context_version,prompt_sha256,model_id,provider,input_sha256,
          created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-annotation", "legacy-hypothesis", int(evaluation.lastrowid),
            "mechanism", "Legacy model annotation.", "glm_synthesis",
            "sha256:" + "5" * 64, "2", "sha256:" + "7" * 64,
            "z-ai/glm-5.2", "openrouter", "legacy-annotation-input",
            "2026-07-23T09:00:00+00:00",
        ),
    )
    connection.commit()


def test_migration_006_name_and_checksum_are_frozen():
    migration = MIGRATION_BY_VERSION[6]
    normalized = MIGRATION_006_SQL.replace("\r\n", "\n").replace("\r", "\n")
    assert AUTONOMOUS_SCHEMA_VERSION == 7
    assert migration.name == "006_provider_neutral_synthesis"
    assert migration.checksum == FROZEN_MIGRATION_006_CHECKSUM
    assert hashlib.sha256(normalized.encode()).hexdigest() == migration.checksum


def test_current_schema_can_intentionally_stop_at_v5_then_upgrade_to_v6(tmp_path):
    database = tmp_path / "fresh-current.db"
    connection = _connection(database)
    connection.executescript(CURRENT_SCHEMA)
    connection.commit()
    connection.close()

    first = migrate(str(database), 5, 0, CODE_VERSION)
    assert [item["version"] for item in first["applied"]] == [1, 2, 3, 4, 5]
    at_v5 = schema_status(str(database))
    assert at_v5["current_version"] == 5
    assert at_v5["status"] == "pending"
    assert [item["version"] for item in at_v5["pending"]] == [6, 7]

    plan = schema_plan(str(database), 6)
    assert [item["version"] for item in plan["steps"]] == [6]
    second = migrate(str(database), 6, 5, CODE_VERSION)
    assert [item["version"] for item in second["applied"]] == [6]
    assert schema_status(str(database))["status"] == "pending"

    connection = _connection(database)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    connection.close()


def test_v5_upgrade_preserves_legacy_synthesis_and_accepts_neutral_shape(tmp_path):
    database = _v5_database(tmp_path / "upgrade.db")
    connection = _connection(database)
    _insert_legacy_synthesis(connection)
    before = dict(connection.execute(
        "SELECT * FROM synthesis_runs WHERE synthesis_id=?",
        ("sha256:" + "5" * 64,),
    ).fetchone())
    child_before = dict(connection.execute(
        "SELECT * FROM synthesis_analysis_runs WHERE synthesis_id=?",
        ("sha256:" + "5" * 64,),
    ).fetchone())
    annotation_before = dict(connection.execute(
        "SELECT * FROM hypothesis_annotations WHERE annotation_id='legacy-annotation'"
    ).fetchone())
    connection.close()

    plan = schema_plan(str(database), 6)
    assert [step["version"] for step in plan["steps"]] == [6]
    assert plan["steps"][0]["destructive"] is True
    result = migrate(str(database), 6, 5, CODE_VERSION)
    assert result["applied"][0]["name"] == "006_provider_neutral_synthesis"

    connection = _connection(database)
    after = dict(connection.execute(
        "SELECT * FROM synthesis_runs WHERE synthesis_id=?",
        ("sha256:" + "5" * 64,),
    ).fetchone())
    assert after == before
    assert dict(connection.execute(
        "SELECT * FROM synthesis_analysis_runs WHERE synthesis_id=?",
        ("sha256:" + "5" * 64,),
    ).fetchone()) == child_before
    assert dict(connection.execute(
        "SELECT * FROM hypothesis_annotations WHERE annotation_id='legacy-annotation'"
    ).fetchone()) == annotation_before
    annotation_indexes = {
        row["name"]
        for row in connection.execute("PRAGMA index_list('hypothesis_annotations')")
    }
    assert {
        "hypothesis_annotations_latest_idx",
        "hypothesis_annotation_superseded_once_idx",
    } <= annotation_indexes
    annotation_triggers = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='hypothesis_annotations'"
        )
    }
    assert annotation_triggers == {
        "hypothesis_annotations_no_update",
        "hypothesis_annotations_no_delete",
    }
    with pytest.raises(sqlite3.IntegrityError, match="append_only_table"):
        connection.execute(
            "UPDATE hypothesis_annotations SET content=content WHERE annotation_id='legacy-annotation'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append_only_table"):
        connection.execute(
            "DELETE FROM hypothesis_annotations WHERE annotation_id='legacy-annotation'"
        )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    connection.close()
    assert schema_status(str(database))["status"] == "pending"


def test_v6_database_constraints_accept_two_canonical_provider_pairs(tmp_path):
    database = _v5_database(tmp_path / "pairs.db")
    migrate(str(database), 6, 5, CODE_VERSION)
    connection = _connection(database)
    _insert_legacy_synthesis(connection)
    base = dict(connection.execute(
        "SELECT * FROM synthesis_runs WHERE synthesis_id=?",
        ("sha256:" + "5" * 64,),
    ).fetchone())
    columns = tuple(base)
    statement = "INSERT INTO synthesis_runs({}) VALUES({})".format(
        ",".join(columns), ",".join("?" for _ in columns),
    )
    for index, (model_id, provider) in enumerate((
        ("gpt-5.6-sol", "openai-codex"),
        ("claude-sonnet-4.5", "anthropic"),
    ), start=8):
        row = {**base, "synthesis_id": "sha256:" + str(index) * 64,
               "model_id": model_id, "provider": provider}
        connection.execute(statement, tuple(row[column] for column in columns))
    with pytest.raises(sqlite3.IntegrityError):
        row = {**base, "synthesis_id": "sha256:" + "a" * 64,
               "model_id": "model with spaces", "provider": "provider"}
        connection.execute(statement, tuple(row[column] for column in columns))
    connection.close()


def test_mid_migration_006_failure_rolls_back_tables_and_ledger(tmp_path, monkeypatch):
    database = _v5_database(tmp_path / "rollback.db")
    before = database.read_bytes()
    apply_006 = migrations._apply_006

    def fail_after_rebuild(connection):
        apply_006(connection)
        raise RuntimeError("injected provider migration failure")

    monkeypatch.setattr(migrations, "_apply_006", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="injected provider migration failure"):
        migrate(str(database), 6, 5, CODE_VERSION)
    assert database.read_bytes() == before
    connection = _connection(database)
    assert connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0] == 5
    connection.close()
