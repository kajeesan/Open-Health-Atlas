"""Migration 004 schema, integrity, parity, and append-only contracts."""

from __future__ import annotations

import hashlib
import pathlib
import re
import sqlite3
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
CODE_VERSION = "4" * 40
sys.path.insert(0, str(ROOT))

from hermes_insights.migrations import (  # noqa: E402
    AUTONOMOUS_SCHEMA_VERSION,
    MIGRATION_004_SQL,
    MIGRATION_BY_VERSION,
    HYPOTHESIS_ANNOTATIONS_V6_DDL,
    PHASE5_INDEX_DDL,
    PHASE5_TABLE_DDL,
    PHASE5_TRIGGER_DDL,
    SchemaError,
    migrate,
    require_version,
    schema_plan,
    schema_status,
    SYNTHESIS_RUNS_V6_DDL,
)
import hermes_insights.migrations as migrations_module  # noqa: E402


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
PRE_PHASE5_SCHEMA = SCHEMA.split("-- Phase 5 immutable", 1)[0]


FROZEN_MIGRATION_004_CHECKSUM = (
    "381006f8a4ea255062a9d8f922d7e30c0bd5dbfc94d47e031c2f107989718229"
)
EXPECTED_TABLES = {
    "analysis_batches",
    "analysis_range_requests",
    "analysis_runs",
    "analysis_findings",
    "analysis_finding_components",
    "hypotheses",
    "hypothesis_components",
    "hypothesis_evaluations",
    "hypothesis_evidence_items",
    "synthesis_runs",
    "synthesis_analysis_runs",
    "synthesis_finding_refs",
    "synthesis_hypothesis_refs",
    "hypothesis_annotations",
    "insight_triggers",
    "insight_trigger_events",
    "insight_notification_outbox",
    "insight_notification_events",
}
EXPECTED_INDEXES = {
    "analysis_runs_lookup_idx",
    "analysis_findings_run_idx",
    "hypothesis_eval_latest_idx",
    "hypothesis_evidence_history_idx",
    "hypothesis_annotations_latest_idx",
    "hypothesis_annotation_superseded_once_idx",
    "insight_triggers_queue_idx",
    "insight_notification_queue_idx",
}
APPEND_ONLY_TABLES = {
    "hypotheses",
    "hypothesis_components",
    "hypothesis_evaluations",
    "hypothesis_evidence_items",
    "hypothesis_annotations",
}
EXPECTED_TRIGGERS = {
    f"{table}_no_{operation}"
    for table in APPEND_ONLY_TABLES
    for operation in ("update", "delete")
}
EXPECTED_CHECK_COUNTS = {
    "analysis_batches": 8,
    "analysis_range_requests": 3,
    "analysis_runs": 6,
    "analysis_findings": 4,
    "analysis_finding_components": 5,
    "hypotheses": 2,
    "hypothesis_components": 4,
    "hypothesis_evaluations": 9,
    "hypothesis_evidence_items": 2,
    "synthesis_runs": 4,
    "synthesis_analysis_runs": 1,
    "synthesis_finding_refs": 1,
    "synthesis_hypothesis_refs": 1,
    "hypothesis_annotations": 4,
    "insight_triggers": 7,
    "insight_trigger_events": 2,
    "insight_notification_outbox": 9,
    "insight_notification_events": 2,
}
EXPECTED_UNIQUE_KEYS = {
    "analysis_batches": {("batch_id",), ("dedupe_key",)},
    "analysis_range_requests": {
        ("range_id",),
        ("batch_id", "range_role"),
        ("range_id", "batch_id"),
    },
    "analysis_runs": {("run_id",), ("range_id", "outcome_key", "outcome_mode")},
    "analysis_findings": {("finding_id",), ("run_id", "candidate_key")},
    "analysis_finding_components": {
        ("finding_id", "position"),
        ("finding_id", "exposure_key"),
    },
    "hypotheses": {("hypothesis_id",), ("dedupe_key",)},
    "hypothesis_components": {
        ("hypothesis_id", "position"),
        ("hypothesis_id", "exposure_key"),
    },
    "hypothesis_evaluations": {
        ("hypothesis_id", "run_id"),
        ("hypothesis_id", "input_fingerprint", "source_analysis_version"),
        ("id", "hypothesis_id"),
    },
    "hypothesis_evidence_items": {
        ("evidence_item_id",),
        ("hypothesis_id", "finding_id", "evidence_kind"),
    },
    "synthesis_runs": {("synthesis_id",)},
    "synthesis_analysis_runs": {("synthesis_id", "run_id")},
    "synthesis_finding_refs": {("synthesis_id", "finding_id", "role")},
    "synthesis_hypothesis_refs": {("synthesis_id", "hypothesis_id", "role")},
    "hypothesis_annotations": {("annotation_id",), ("supersedes_id",)},
    "insight_triggers": {("trigger_id",), ("dedupe_key",)},
    "insight_trigger_events": {("event_id",)},
    "insight_notification_outbox": {("notification_id",), ("dedupe_key",)},
    "insight_notification_events": {("event_id",)},
}
EXPECTED_FOREIGN_KEYS = {
    "analysis_batches": set(),
    "analysis_range_requests": {
        ("analysis_batches", "batch_id", "batch_id"),
    },
    "analysis_runs": {
        ("analysis_range_requests", "range_id", "range_id"),
        ("analysis_range_requests", "batch_id", "batch_id"),
    },
    "analysis_findings": {("analysis_runs", "run_id", "run_id")},
    "analysis_finding_components": {
        ("analysis_findings", "finding_id", "finding_id"),
    },
    "hypotheses": {
        ("analysis_runs", "created_by_run", "run_id"),
        ("analysis_findings", "created_by_finding", "finding_id"),
    },
    "hypothesis_components": {
        ("hypotheses", "hypothesis_id", "hypothesis_id"),
    },
    "hypothesis_evaluations": {
        ("hypotheses", "hypothesis_id", "hypothesis_id"),
        ("analysis_runs", "run_id", "run_id"),
        ("analysis_findings", "finding_id", "finding_id"),
        ("hypothesis_evaluations", "previous_evaluation_id", "id"),
        ("hypothesis_evaluations", "comparison_evaluation_id", "id"),
    },
    "hypothesis_evidence_items": {
        ("hypotheses", "hypothesis_id", "hypothesis_id"),
        ("hypothesis_evaluations", "evaluation_id", "id"),
        ("analysis_findings", "finding_id", "finding_id"),
    },
    "synthesis_runs": {
        ("analysis_batches", "analysis_batch_id", "batch_id"),
    },
    "synthesis_analysis_runs": {
        ("synthesis_runs", "synthesis_id", "synthesis_id"),
        ("analysis_runs", "run_id", "run_id"),
    },
    "synthesis_finding_refs": {
        ("synthesis_runs", "synthesis_id", "synthesis_id"),
        ("analysis_findings", "finding_id", "finding_id"),
    },
    "synthesis_hypothesis_refs": {
        ("synthesis_runs", "synthesis_id", "synthesis_id"),
        ("hypotheses", "hypothesis_id", "hypothesis_id"),
        ("hypothesis_evaluations", "evaluation_id", "id"),
        ("hypothesis_evaluations", "hypothesis_id", "hypothesis_id"),
    },
    "hypothesis_annotations": {
        ("hypotheses", "hypothesis_id", "hypothesis_id"),
        ("hypothesis_evaluations", "evaluation_id", "id"),
        ("synthesis_runs", "synthesis_id", "synthesis_id"),
        ("hypothesis_annotations", "supersedes_id", "annotation_id"),
    },
    "insight_triggers": {
        ("analysis_batches", "analysis_batch_id", "batch_id"),
        ("synthesis_runs", "synthesis_id", "synthesis_id"),
    },
    "insight_trigger_events": {
        ("insight_triggers", "trigger_id", "trigger_id"),
        ("analysis_batches", "analysis_batch_id", "batch_id"),
        ("synthesis_runs", "synthesis_id", "synthesis_id"),
    },
    "insight_notification_outbox": {
        ("analysis_batches", "analysis_batch_id", "batch_id"),
        ("synthesis_runs", "synthesis_id", "synthesis_id"),
        ("insight_triggers", "trigger_id", "trigger_id"),
    },
    "insight_notification_events": {
        ("insight_notification_outbox", "notification_id", "notification_id"),
    },
}


def _connect(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _new_database(path: pathlib.Path, schema: str) -> pathlib.Path:
    connection = _connect(path)
    connection.executescript(schema)
    connection.commit()
    connection.close()
    return path


def _v3_database(tmp_path: pathlib.Path, name: str = "v3.db") -> pathlib.Path:
    path = _new_database(tmp_path / name, PRE_PHASE5_SCHEMA)
    result = migrate(str(path), 3, 0, CODE_VERSION)
    assert result["to_version"] == 3
    return path


def _v4_fresh_database(tmp_path: pathlib.Path, name: str = "fresh-v4.db") -> pathlib.Path:
    path = _new_database(tmp_path / name, SCHEMA)
    result = migrate(str(path), 4, 0, CODE_VERSION)
    assert result["to_version"] == 4
    return path


def _normalized_sql(value: str | None) -> str:
    normalized = re.sub(r"[\s\"`\[\]]+", "", (value or "").lower()).rstrip(";")
    for old, new in (
        ("createtableifnotexists", "createtable"),
        ("createuniqueindexifnotexists", "createuniqueindex"),
        ("createindexifnotexists", "createindex"),
        ("createtriggerifnotexists", "createtrigger"),
    ):
        normalized = normalized.replace(old, new, 1)
    return normalized


def _phase5_shape(path: pathlib.Path) -> list[tuple[str, str, str, str]]:
    names = EXPECTED_TABLES | EXPECTED_INDEXES | EXPECTED_TRIGGERS
    connection = _connect(path)
    rows = connection.execute(
        """SELECT type,name,tbl_name,sql
             FROM sqlite_master
            WHERE name IN ({})
         ORDER BY type,name""".format(",".join("?" for _ in names)),
        sorted(names),
    ).fetchall()
    connection.close()
    return [
        (row["type"], row["name"], row["tbl_name"], _normalized_sql(row["sql"]))
        for row in rows
    ]


def _unique_keys(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for row in connection.execute(f'PRAGMA index_list("{table}")'):
        if not row["unique"]:
            continue
        result.add(tuple(
            item["name"]
            for item in connection.execute(f'PRAGMA index_info("{row["name"]}")')
        ))
    return result


def _foreign_keys(
    connection: sqlite3.Connection, table: str
) -> set[tuple[str, str, str]]:
    return {
        (row["table"], row["from"], row["to"])
        for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')
    }


def _check_count(sql: str) -> int:
    return len(re.findall(r"\bCHECK\s*\(", sql, flags=re.IGNORECASE))


def _seed_append_only_rows(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO analysis_batches(
             batch_id,dedupe_key,run_kind,anchor_date,range_plan_version,
             outcome_selection,outcome_set_sha256,analysis_version,registry_version,
             engine_sha256,registry_sha256,status,run_count,completed_count,
             insufficient_count,no_data_count,failed_count,started_at,completed_at)
           VALUES('batch-1','batch-dedupe','manual','2026-07-22','range-v1',
             'explicit_set','outcomes-sha','outcome-v1','feature-registry-v1',
             'engine-sha','registry-sha','completed',1,1,0,0,0,
             '2026-07-22T00:00:00Z','2026-07-22T00:01:00Z')"""
    )
    connection.execute(
        """INSERT INTO analysis_range_requests(
             range_id,batch_id,range_role,requested_range_kind,requested_from,requested_to)
           VALUES('range-1','batch-1','primary','bounded','2026-01-01','2026-06-30')"""
    )
    connection.execute(
        """INSERT INTO analysis_runs(
             run_id,batch_id,range_id,outcome_key,outcome_mode,range_resolution,
             analysis_from,analysis_to,baseline_from,baseline_to,input_fingerprint,
             status,result_json,result_sha256,started_at,completed_at)
           VALUES('run-1','batch-1','range-1','subjective.day_rating','ordinal',
             'bounded_exact','2026-01-01','2026-06-30','2025-12-25','2026-06-30',
             'input-sha','completed','{}','result-sha',
             '2026-07-22T00:00:00Z','2026-07-22T00:01:00Z')"""
    )
    connection.execute(
        """INSERT INTO analysis_findings(
             finding_id,run_id,candidate_key,candidate_kind,outcome_key,outcome_mode,
             direction,quality_tier,eligible_for_hypothesis,evidence_json,
             evidence_for_json,evidence_against_json,evidence_fingerprint)
           VALUES('finding-1','run-1','candidate-1','single',
             'subjective.day_rating','ordinal','positive','exploratory_unreplicated',
             1,'{}','[]','[]','evidence-sha')"""
    )
    connection.execute(
        """INSERT INTO hypotheses(
             hypothesis_id,dedupe_key,outcome_key,outcome_mode,candidate_kind,
             initial_direction,first_seen,created_at,created_by_run,created_by_finding)
           VALUES('hypothesis-1','hypothesis-dedupe','subjective.day_rating','ordinal',
             'single','positive','2026-01-01','2026-07-22T00:01:00Z',
             'run-1','finding-1')"""
    )
    connection.execute(
        """INSERT INTO hypothesis_components(
             hypothesis_id,position,exposure_key,lag_days,window_days,transform)
           VALUES('hypothesis-1',1,'sleep.duration_hours',1,1,'point')"""
    )
    cursor = connection.execute(
        """INSERT INTO hypothesis_evaluations(
             hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,evidence_class,
             evidence_for_json,evidence_against_json,confounders_json,sample_size_json,
             effect_summary_json,stability_json,confidence,status,change_reason,
             change_conditions,source_analysis_version,input_fingerprint,
             evidence_fingerprint,new_eligible_observations,compatible_with_prior,
             transition_applied,created_at)
           VALUES('hypothesis-1','run-1','finding-1','2026-07-22T00:01:00Z',
             '2026-01-01','2026-06-30','initial_discovery_pass','[]','[]','{}','{}',
             '{}','{}','low','candidate','initial discovery','new evidence',
             'outcome-v1','input-sha','evidence-sha',30,1,1,
             '2026-07-22T00:01:00Z')"""
    )
    evaluation_id = int(cursor.lastrowid)
    connection.execute(
        """INSERT INTO hypothesis_evidence_items(
             evidence_item_id,hypothesis_id,evaluation_id,finding_id,polarity,
             evidence_kind,evidence_fingerprint,source_analysis_version,
             range_from,range_to,created_at)
           VALUES('evidence-item-1','hypothesis-1',?,'finding-1','for',
             'same_direction_effect','evidence-sha','outcome-v1',
             '2026-01-01','2026-06-30','2026-07-22T00:01:00Z')""",
        (evaluation_id,),
    )
    connection.execute(
        """INSERT INTO hypothesis_annotations(
             annotation_id,hypothesis_id,evaluation_id,annotation_kind,content,source,
             input_sha256,created_at)
           VALUES('annotation-1','hypothesis-1',?,'owner_note','Owner note','owner',
             'annotation-input-sha','2026-07-22T00:02:00Z')""",
        (evaluation_id,),
    )
    connection.commit()


def test_migration_004_frozen_inventory_and_checksum():
    migration = MIGRATION_BY_VERSION[4]
    normalized = MIGRATION_004_SQL.replace("\r\n", "\n").replace("\r", "\n")
    assert AUTONOMOUS_SCHEMA_VERSION >= 4
    assert migration.name == "004_insight_ledger"
    assert migration.checksum == FROZEN_MIGRATION_004_CHECKSUM
    assert hashlib.sha256(normalized.encode("utf-8")).hexdigest() == migration.checksum
    assert set(PHASE5_TABLE_DDL) == EXPECTED_TABLES
    assert set(PHASE5_INDEX_DDL) == EXPECTED_INDEXES
    assert set(PHASE5_TRIGGER_DDL) == EXPECTED_TRIGGERS
    assert len(PHASE5_TRIGGER_DDL) == 10


def test_fresh_schema_and_v3_upgrade_have_exact_phase5_parity(tmp_path):
    fresh = _v4_fresh_database(tmp_path)
    upgraded = _v3_database(tmp_path, "upgraded.db")
    before = _phase5_shape(upgraded)
    assert before == []
    result = migrate(str(upgraded), 4, 3, CODE_VERSION)
    assert result["applied"] == [{
        "version": 4,
        "name": "004_insight_ledger",
        "checksum_sha256": FROZEN_MIGRATION_004_CHECKSUM,
    }]
    assert _phase5_shape(fresh) == _phase5_shape(upgraded)
    assert len(_phase5_shape(fresh)) == 36
    assert schema_status(str(fresh))["status"] == "pending"
    assert schema_status(str(upgraded))["status"] == "pending"
    assert schema_plan(str(upgraded), 4)["steps"] == []


def test_all_phase5_checks_unique_keys_and_foreign_keys_are_frozen(tmp_path):
    database = _v4_fresh_database(tmp_path)
    connection = _connect(database)
    for table in sorted(EXPECTED_TABLES):
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert row is not None
        assert _check_count(row["sql"]) == EXPECTED_CHECK_COUNTS[table]
        assert _unique_keys(connection, table) == EXPECTED_UNIQUE_KEYS[table]
        assert _foreign_keys(connection, table) == EXPECTED_FOREIGN_KEYS[table]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    connection.close()


def test_all_ten_append_only_triggers_reject_update_and_delete(tmp_path):
    database = _v4_fresh_database(tmp_path)
    connection = _connect(database)
    _seed_append_only_rows(connection)
    mutations = {
        "hypotheses": (
            "UPDATE hypotheses SET outcome_key=outcome_key WHERE hypothesis_id='hypothesis-1'",
            "DELETE FROM hypotheses WHERE hypothesis_id='hypothesis-1'",
        ),
        "hypothesis_components": (
            "UPDATE hypothesis_components SET position=position WHERE hypothesis_id='hypothesis-1'",
            "DELETE FROM hypothesis_components WHERE hypothesis_id='hypothesis-1'",
        ),
        "hypothesis_evaluations": (
            "UPDATE hypothesis_evaluations SET tested_at=tested_at WHERE hypothesis_id='hypothesis-1'",
            "DELETE FROM hypothesis_evaluations WHERE hypothesis_id='hypothesis-1'",
        ),
        "hypothesis_evidence_items": (
            "UPDATE hypothesis_evidence_items SET polarity=polarity WHERE evidence_item_id='evidence-item-1'",
            "DELETE FROM hypothesis_evidence_items WHERE evidence_item_id='evidence-item-1'",
        ),
        "hypothesis_annotations": (
            "UPDATE hypothesis_annotations SET content=content WHERE annotation_id='annotation-1'",
            "DELETE FROM hypothesis_annotations WHERE annotation_id='annotation-1'",
        ),
    }
    trigger_names = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert trigger_names == EXPECTED_TRIGGERS
    for table, statements in mutations.items():
        for statement in statements:
            with pytest.raises(sqlite3.IntegrityError, match="append_only_table"):
                connection.execute(statement)
        assert connection.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0] == 1
    connection.close()


@pytest.mark.parametrize("conflict", ["table", "index", "trigger"])
def test_phase5_origin_preflight_fails_before_any_migration_write(tmp_path, conflict):
    database = _new_database(tmp_path / f"origin-{conflict}.db", PRE_PHASE5_SCHEMA)
    connection = _connect(database)
    if conflict == "table":
        connection.execute("CREATE TABLE analysis_batches(batch_id TEXT PRIMARY KEY)")
    elif conflict == "index":
        connection.execute(
            "CREATE INDEX analysis_runs_lookup_idx ON daily_metrics(date)"
        )
    else:
        connection.execute(
            """CREATE TRIGGER hypotheses_no_update
               BEFORE UPDATE ON daily_metrics BEGIN SELECT 1; END"""
        )
    connection.commit()
    connection.close()
    before = database.read_bytes()

    with pytest.raises(SchemaError) as planned:
        schema_plan(str(database), 4)
    assert planned.value.code == "incompatible_schema"
    with pytest.raises(SchemaError) as applied:
        migrate(str(database), 4, 0, CODE_VERSION)
    assert applied.value.code == "incompatible_schema"
    assert database.read_bytes() == before

    connection = _connect(database)
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM training_plan_revisions"
    ).fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize(
    "drift_sql",
    [
        "ALTER TABLE analysis_batches ADD COLUMN wrong_extra BLOB",
        "DROP INDEX analysis_runs_lookup_idx",
        "DROP TRIGGER hypotheses_no_update",
    ],
)
def test_applied_phase5_shape_index_and_trigger_drift_fail_status(tmp_path, drift_sql):
    database = _v4_fresh_database(tmp_path)
    connection = _connect(database)
    connection.execute(drift_sql)
    connection.commit()
    connection.close()
    with pytest.raises(SchemaError) as error:
        schema_status(str(database))
    assert error.value.code == "incompatible_schema"


def test_expected_origin_and_repeat_application_are_idempotent(tmp_path):
    database = _v3_database(tmp_path)
    before = database.read_bytes()
    with pytest.raises(SchemaError) as mismatch:
        migrate(str(database), 4, 2, CODE_VERSION)
    assert mismatch.value.code == "expected_from_mismatch"
    assert database.read_bytes() == before

    migrate(str(database), 4, 3, CODE_VERSION)
    shape = _phase5_shape(database)
    repeat = migrate(str(database), 4, 4, CODE_VERSION)
    assert repeat["status"] == "up_to_date"
    assert repeat["applied"] == []
    assert _phase5_shape(database) == shape
    connection = _connect(database)
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 4
    require_version(connection, 3)
    require_version(connection, 4)
    connection.close()


def test_v3_rows_are_byte_for_byte_preserved_by_additive_004(tmp_path):
    database = _v3_database(tmp_path)
    connection = _connect(database)
    connection.execute(
        """INSERT INTO subjective_daily(date,day_rating,focus,energy,mood,source)
           VALUES('2026-07-01',3,4,5,4,'manual')"""
    )
    connection.commit()
    before = [
        tuple(row)
        for row in connection.execute(
            """SELECT date,day_rating,focus,energy,mood,source
                 FROM subjective_daily ORDER BY date"""
        )
    ]
    plan_before = [
        tuple(row)
        for row in connection.execute(
            """SELECT id,effective_from,schedule_json,routines_json,source,
                      supersedes_id,created_at
                 FROM training_plan_revisions ORDER BY id"""
        )
    ]
    connection.close()

    migrate(str(database), 4, 3, CODE_VERSION)
    connection = _connect(database)
    assert [
        tuple(row)
        for row in connection.execute(
            """SELECT date,day_rating,focus,energy,mood,source
                 FROM subjective_daily ORDER BY date"""
        )
    ] == before
    assert [
        tuple(row)
        for row in connection.execute(
            """SELECT id,effective_from,schedule_json,routines_json,source,
                      supersedes_id,created_at
                 FROM training_plan_revisions ORDER BY id"""
        )
    ] == plan_before
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    connection.close()


def test_mid_migration_004_failure_rolls_back_objects_and_ledger(
    tmp_path, monkeypatch,
):
    database = _v3_database(tmp_path)

    def fail_after_first_object(connection):
        connection.execute(PHASE5_TABLE_DDL["analysis_batches"])
        raise RuntimeError("injected phase5 failure")

    monkeypatch.setattr(migrations_module, "_apply_004", fail_after_first_object)
    with pytest.raises(RuntimeError, match="injected phase5 failure"):
        migrate(str(database), 4, 3, CODE_VERSION)

    connection = _connect(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='analysis_batches'"
    ).fetchone() is None
    assert connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0] == 3
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    connection.close()


def test_recorded_migration_004_checksum_drift_fails_closed(tmp_path):
    database = _v4_fresh_database(tmp_path)
    connection = _connect(database)
    connection.execute(
        "UPDATE schema_migrations SET checksum_sha256=? WHERE version=4",
        ("0" * 64,),
    )
    connection.commit()
    connection.close()
    with pytest.raises(SchemaError) as error:
        schema_status(str(database))
    assert error.value.code == "migration_checksum_mismatch"
