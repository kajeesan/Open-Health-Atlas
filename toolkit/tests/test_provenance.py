from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from hermes_insights.contracts import canonical_json
from hermes_insights.provenance import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_VERSION,
    ProvenanceError,
    candidate_key,
    dependency_source_observations,
    dependency_snapshot,
    engine_manifest,
    engine_sha256,
    finding_id,
    input_fingerprint,
    normalize_row_references,
    sha256_id,
    source_completeness,
    source_manifests,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def _conn(*, dependencies: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if not dependencies:
        return conn
    conn.executescript(
        """
        CREATE TABLE capture_completeness_revisions(
          id INTEGER PRIMARY KEY,
          date TEXT NOT NULL,
          supersedes_id INTEGER
        );
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,
          status TEXT NOT NULL,
          coverage_from TEXT,
          coverage_to TEXT
        );
        CREATE TABLE entity_aliases(
          id INTEGER PRIMARY KEY,
          entity_type TEXT NOT NULL,
          alias_key TEXT NOT NULL,
          canonical_key TEXT NOT NULL,
          active INTEGER NOT NULL
        );
        CREATE TABLE training_plan_revisions(
          id INTEGER PRIMARY KEY,
          effective_from TEXT NOT NULL
        );
        CREATE TABLE insight_goal_revisions(
          id INTEGER PRIMARY KEY,
          goal_key TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO capture_completeness_revisions VALUES(1,'2026-07-01',NULL)"
    )
    conn.execute(
        "INSERT INTO source_sync_runs VALUES(1,'success','2026-07-01','2026-07-31')"
    )
    conn.execute(
        "INSERT INTO entity_aliases VALUES(1,'food','food:raw','food:canonical',1)"
    )
    conn.execute(
        "INSERT INTO training_plan_revisions VALUES(1,'2026-01-01')"
    )
    conn.execute(
        "INSERT INTO insight_goal_revisions VALUES(1,'green_days')"
    )
    return conn


def _observation(
    *,
    feature_key: str = "sleep.duration_hours",
    observed_at: str = "2026-07-01",
    value=7.5,
    state: str = "observed",
    source: str | None = "fitbit",
    provenance=None,
):
    return {
        "feature_key": feature_key,
        "observed_at": observed_at,
        "value": value,
        "state": state,
        "source": source,
        "provenance": provenance
        or {
            "adapter": "daily",
            "table": "daily_metrics",
            "natural_key": f"daily_metrics:{observed_at}:fitbit",
        },
    }


def _fingerprint(conn: sqlite3.Connection, observations=None, **kwargs) -> str:
    return input_fingerprint(
        observations or [_observation()],
        conn=conn,
        registry_version="feature-registry-v1",
        registry_sha256=HASH_A,
        analysis_sha256=HASH_B,
        date_from="2026-07-01",
        date_to="2026-07-31",
        **kwargs,
    )


def test_contract_version_and_sha256_use_shared_canonical_serialization():
    assert ANALYSIS_CONTRACT_VERSION == "outcome-associations-v1"
    assert ANALYSIS_VERSION == "outcome-v1"
    left = {"z": -0.0, "a": [1, 1.23456789012345]}
    right = {"a": [1, 1.23456789012345], "z": 0.0}
    expected = "sha256:" + hashlib.sha256(
        canonical_json(left).encode("utf-8")
    ).hexdigest()
    assert sha256_id(left) == expected
    assert sha256_id(left) == sha256_id(right)


def test_candidate_key_is_exact_and_component_order_invariant():
    first = {
        "exposure_key": "event.travel.occurred",
        "lag_days": 1,
        "window_days": 7,
        "transform": "any",
    }
    second = {
        "exposure_key": "sleep.duration_hours",
        "lag_days": 2,
        "window_days": 1,
        "transform": "point",
    }
    forward = candidate_key(
        "subjective.day_rating", "green-vs-non-green", [first, second]
    )
    reverse = candidate_key(
        "subjective.day_rating",
        "green-vs-non-green",
        [{**second, "temporal_direction": "exposure_precedes_outcome"}, first],
    )
    assert forward == reverse
    assert json.loads(forward) == {
        "outcome_key": "subjective.day_rating",
        "outcome_mode": "green-vs-non-green",
        "components": [first, second],
    }
    assert forward == canonical_json(json.loads(forward))


def test_candidate_key_rejects_unknown_fields_duplicates_and_triples():
    component = {
        "exposure_key": "sleep.duration_hours",
        "lag_days": 1,
        "window_days": 1,
        "transform": "point",
    }
    with pytest.raises(ProvenanceError, match="unknown"):
        candidate_key("subjective.day_rating", "ordinal", [
            {**component, "browser_row_id": 99}
        ])
    with pytest.raises(ProvenanceError, match="distinct"):
        candidate_key("subjective.day_rating", "ordinal", [component, component])
    with pytest.raises(ProvenanceError, match="one or two"):
        candidate_key(
            "subjective.day_rating", "ordinal",
            [
                component,
                {**component, "lag_days": 2},
                {**component, "lag_days": 3},
            ],
        )


def test_engine_sha256_hashes_python_and_native_sources(tmp_path: Path):
    contents = {
        "_native_stats.py": b"native loader and build policy\n",
        "native/rank_products.c": b"native products\n",
        "stats.py": b"stats\n",
        "associations.py": b"associations\n",
        "interactions.py": b"interactions\n",
        "provenance.py": b"provenance\n",
    }
    for filename, content in contents.items():
        (tmp_path / filename).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / filename).write_bytes(content)
    expected_manifest = [
        {
            "file": filename,
            "sha256": hashlib.sha256(contents[filename]).hexdigest(),
        }
        for filename in sorted(contents)
    ]
    assert engine_manifest(tmp_path) == expected_manifest
    assert engine_sha256(tmp_path) == sha256_id(expected_manifest)
    with pytest.raises(ProvenanceError, match="unavailable"):
        engine_sha256(tmp_path / "missing")


def test_row_references_support_singular_plural_and_parent_natural_keys():
    observations = [
        _observation(
            feature_key="event.travel.occurred",
            provenance={
                "adapter": "events",
                "table": "event_exposures",
                "merge": "exact_category",
                "natural_keys": ["event_exposures:2", "event_exposures:1"],
                "parent_natural_keys": [
                    "capture_completeness_revisions:7",
                    "event_exposures:1",
                ],
            },
        ),
        _observation(
            observed_at="2026-07-02",
            provenance={
                "adapter": "daily",
                "table": "daily_metrics",
                "natural_key": "daily_metrics:2026-07-02:fitbit",
            },
        ),
    ]
    references = normalize_row_references(observations)
    by_key = {item["natural_key"]: item for item in references}
    assert set(by_key) == {
        "capture_completeness_revisions:7",
        "daily_metrics:2026-07-02:fitbit",
        "event_exposures:1",
        "event_exposures:2",
    }
    assert by_key["event_exposures:1"]["relationship"] == "direct"
    assert (
        by_key["capture_completeness_revisions:7"]["relationship"] == "parent"
    )
    assert by_key["capture_completeness_revisions:7"]["table"] == (
        "capture_completeness_revisions"
    )
    assert references == normalize_row_references(reversed(observations))


def test_source_manifests_are_compact_deduplicated_and_order_invariant():
    observations = [
        _observation(
            feature_key="event.travel.occurred",
            observed_at="2026-07-01",
            source="manual",
            provenance={
                "adapter": "events",
                "table": "event_exposures",
                "merge": "exact_category",
                "natural_keys": ["event_exposures:1", "event_exposures:2"],
            },
        ),
        _observation(
            feature_key="event.travel.count",
            observed_at="2026-07-01",
            source="manual",
            provenance={
                "adapter": "events",
                "table": "event_exposures",
                "merge": "exact_category",
                "natural_key": "event_exposures:1",
            },
        ),
    ]
    manifests = source_manifests(observations)
    assert manifests == source_manifests(reversed(observations))
    assert manifests == [
        {
            "table": "event_exposures",
            "adapter": "events",
            "merge_rule": "exact_category",
            "source_labels": ["manual"],
            "natural_key_scheme": "table-prefixed-natural-key-v1",
            "row_count": 2,
            "date_from": "2026-07-01",
            "date_to": "2026-07-01",
            "digest": manifests[0]["digest"],
        }
    ]
    assert manifests[0]["digest"].startswith("sha256:")


def test_source_completeness_uses_source_dates_and_both_dependency_kinds():
    observations = [
        _observation(
            feature_key="subjective.day_rating",
            observed_at="2026-07-10",
            source="manual",
            provenance={
                "adapter": "daily",
                "table": "subjective_daily",
                "natural_key": "capture_completeness_revisions:11",
            },
        ),
        _observation(
            feature_key="event.travel.occurred",
            observed_at="2026-07-03",
            value=0,
            state="structural_zero",
            source="manual",
            provenance={
                "adapter": "events",
                "table": "capture_completeness_revisions",
                "completeness_revision_ids": [12, 11],
                "source_sync_run_id": 21,
            },
        ),
    ]
    assert source_completeness(observations) == {
        "from": "2026-07-03",
        "to": "2026-07-10",
        "completeness_revision_ids": [11, 12],
        "source_sync_interval_ids": [21],
    }


def test_dependency_source_observations_resolve_every_recomputed_id():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE capture_completeness_revisions(
          id INTEGER PRIMARY KEY,date TEXT,source TEXT);
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY,source TEXT,coverage_from TEXT,
          coverage_to TEXT,completed_at TEXT);
        CREATE TABLE entity_aliases(
          id INTEGER PRIMARY KEY,source TEXT,created_at TEXT);
        CREATE TABLE training_plan_revisions(
          id INTEGER PRIMARY KEY,source TEXT,effective_from TEXT);
        CREATE TABLE insight_goal_revisions(
          id INTEGER PRIMARY KEY,source TEXT,created_at TEXT);
        INSERT INTO capture_completeness_revisions
          VALUES(1,'2026-07-02','manual');
        INSERT INTO source_sync_runs
          VALUES(2,'hevy','2026-07-01','2026-07-31','2026-08-01T01:00:00');
        INSERT INTO entity_aliases
          VALUES(3,'owner','2026-06-01T10:00:00');
        INSERT INTO training_plan_revisions
          VALUES(4,'owner','2026-07-05');
        INSERT INTO insight_goal_revisions
          VALUES(5,'owner','2026-05-01T10:00:00');
        """
    )
    dependencies = {
        "completeness_revision_ids": [1],
        "source_sync_interval_ids": [2],
        "alias_revision_ids": [3],
        "training_plan_revision_ids": [4],
        "goal_revision_ids": [5],
    }
    observations = dependency_source_observations(
        conn,
        dependencies,
        date_from="2026-07-01",
        date_to="2026-07-31",
    )
    references = normalize_row_references(observations)
    assert {item["natural_key"] for item in references} == {
        "capture_completeness_revisions:1",
        "source_sync_runs:2",
        "entity_aliases:3",
        "training_plan_revisions:4",
        "insight_goal_revisions:5",
    }
    sync = [
        item for item in references
        if item["natural_key"] == "source_sync_runs:2"
    ]
    assert {item["observed_at"] for item in sync} == {
        "2026-07-01", "2026-07-31",
    }
    manifests = source_manifests(observations)
    assert {item["table"] for item in manifests} == {
        "capture_completeness_revisions",
        "source_sync_runs",
        "entity_aliases",
        "training_plan_revisions",
        "insight_goal_revisions",
    }
    with pytest.raises(ProvenanceError, match="missing"):
        dependency_source_observations(
            conn,
            {**dependencies, "goal_revision_ids": [99]},
            date_from="2026-07-01",
            date_to="2026-07-31",
        )


def test_missing_optional_tables_are_explicit_empty_dependencies():
    snapshot = dependency_snapshot(
        _conn(dependencies=False),
        date_from="2026-07-01",
        date_to="2026-07-31",
    )
    assert snapshot["completeness_revision_ids"] == []
    assert snapshot["source_sync_interval_ids"] == []
    assert snapshot["alias_revision_ids"] == []
    assert snapshot["training_plan_revision_ids"] == []
    assert snapshot["goal_revision_ids"] == []
    assert set(snapshot["availability"].values()) == {"missing"}
    assert snapshot["alias_revision_digest"].startswith("sha256:")


def test_dependency_snapshot_merges_ids_declared_by_observation_provenance():
    observation = _observation(
        provenance={
            "adapter": "fixture",
            "table": "capture_completeness_revisions",
            "natural_key": "capture_completeness_revisions:9",
            "source_sync_run_ids": [8],
            "alias_revision_ids": [7],
            "plan_revision_id": 6,
            "goal_revision_id": 5,
        }
    )
    snapshot = dependency_snapshot(
        _conn(dependencies=False),
        observations=[observation],
        date_from="2026-07-01",
        date_to="2026-07-31",
    )
    assert snapshot["completeness_revision_ids"] == [9]
    assert snapshot["source_sync_interval_ids"] == [8]
    assert snapshot["alias_revision_ids"] == [7]
    assert snapshot["training_plan_revision_ids"] == [6]
    assert snapshot["goal_revision_ids"] == [5]


@pytest.mark.parametrize(
    "dependency",
    ["completeness", "sync", "alias", "plan", "goal"],
)
def test_each_effective_revision_class_changes_input_fingerprint(dependency: str):
    conn = _conn()
    before = _fingerprint(conn)
    if dependency == "completeness":
        conn.execute(
            "INSERT INTO capture_completeness_revisions "
            "VALUES(2,'2026-07-01',1)"
        )
    elif dependency == "sync":
        conn.execute(
            "INSERT INTO source_sync_runs "
            "VALUES(2,'success','2026-07-15','2026-07-31')"
        )
    elif dependency == "alias":
        conn.execute(
            "INSERT INTO entity_aliases "
            "VALUES(2,'food','food:raw','food:new-canonical',1)"
        )
    elif dependency == "plan":
        conn.execute(
            "INSERT INTO training_plan_revisions VALUES(2,'2026-07-15')"
        )
    else:
        conn.execute(
            "INSERT INTO insight_goal_revisions VALUES(2,'green_days')"
        )
    if dependency == "alias":
        # The effective alias map affects dynamic registry expansion globally.
        assert _fingerprint(conn) != before
    else:
        # Unconsulted revision rows must not invalidate unrelated evidence.
        assert _fingerprint(conn) == before
        keyword = {
            "completeness": "completeness_ids",
            "sync": "source_sync_ids",
            "plan": "training_plan_revision_ids",
            "goal": "goal_revision_ids",
        }[dependency]
        assert _fingerprint(conn, **{keyword: [2]}) != before


def test_input_fingerprint_is_order_invariant_and_version_hash_sensitive():
    conn = _conn()
    observations = [
        _observation(),
        _observation(
            feature_key="subjective.day_rating",
            observed_at="2026-07-02",
            value=3,
            source="manual",
            provenance={
                "adapter": "daily",
                "table": "subjective_daily",
                "natural_key": "subjective_daily:2026-07-02",
            },
        ),
    ]
    forward = _fingerprint(conn, observations)
    assert forward == _fingerprint(conn, list(reversed(observations)))
    assert forward != input_fingerprint(
        observations,
        conn=conn,
        registry_version="feature-registry-v2",
        registry_sha256=HASH_A,
        analysis_sha256=HASH_B,
        date_from="2026-07-01",
        date_to="2026-07-31",
    )
    assert forward != input_fingerprint(
        observations,
        conn=conn,
        registry_version="feature-registry-v1",
        registry_sha256="c" * 64,
        analysis_sha256=HASH_B,
        date_from="2026-07-01",
        date_to="2026-07-31",
    )
    assert forward != input_fingerprint(
        observations,
        conn=conn,
        registry_version="feature-registry-v1",
        registry_sha256=HASH_A,
        analysis_sha256="d" * 64,
        date_from="2026-07-01",
        date_to="2026-07-31",
    )


def test_finding_id_hashes_exact_contract_fields():
    key = candidate_key(
        "subjective.day_rating",
        "ordinal",
        [{
            "exposure_key": "sleep.duration_hours",
            "lag_days": 1,
            "window_days": 1,
            "transform": "point",
        }],
    )
    analysis_range = {"from": "2026-06-01", "to": "2026-07-01"}
    baseline_range = {"from": "2026-05-31", "to": "2026-07-01"}
    fingerprint = "sha256:" + "e" * 64
    expected = sha256_id(
        {
            "contract_version": ANALYSIS_CONTRACT_VERSION,
            "analysis_version": ANALYSIS_VERSION,
            "candidate_key": key,
            "analysis_range": {"kind": "bounded", **analysis_range},
            "baseline_range": {"kind": "bounded", **baseline_range},
            "input_fingerprint": fingerprint,
        }
    )
    assert finding_id(
        candidate_key=key,
        analysis_range=analysis_range,
        baseline_range=baseline_range,
        input_fingerprint=fingerprint,
    ) == expected
    assert finding_id(
        candidate_key=key,
        analysis_range={"kind": "all", **analysis_range},
        baseline_range={"kind": "bounded", **baseline_range},
        input_fingerprint=fingerprint,
    ) != expected
    assert finding_id(
        candidate_key_value=key,
        analysis_range={"kind": "bounded", **analysis_range},
        baseline_range={"kind": "bounded", **baseline_range},
        input_fingerprint_value=fingerprint,
    ) == expected
    with pytest.raises(ProvenanceError, match="canonical"):
        finding_id(
            candidate_key=json.dumps(json.loads(key), indent=2),
            analysis_range=analysis_range,
            baseline_range=baseline_range,
            input_fingerprint=fingerprint,
        )


def test_dependency_and_fingerprint_paths_are_select_only():
    conn = _conn()
    before_changes = conn.total_changes
    before_schema = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    statements: list[str] = []
    allowed = {
        getattr(sqlite3, "SQLITE_READ", 20),
        getattr(sqlite3, "SQLITE_SELECT", 21),
        getattr(sqlite3, "SQLITE_FUNCTION", 31),
        getattr(sqlite3, "SQLITE_RECURSIVE", 33),
    }
    conn.set_trace_callback(statements.append)
    conn.set_authorizer(
        lambda action, _a1, _a2, _db, _trigger: (
            sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
        )
    )
    try:
        snapshot = dependency_snapshot(
            conn, date_from="2026-07-01", date_to="2026-07-31"
        )
        fingerprint = _fingerprint(conn)
    finally:
        conn.set_authorizer(None)
        conn.set_trace_callback(None)
    after_schema = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    assert snapshot["goal_revision_ids"] == []
    assert fingerprint.startswith("sha256:")
    assert conn.total_changes == before_changes
    assert [tuple(row) for row in after_schema] == [
        tuple(row) for row in before_schema
    ]
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT")
               for statement in statements)
