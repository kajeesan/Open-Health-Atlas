"""Distinct local-instance isolation and compatibility regressions, using fiction only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from hermes_insights import associations, exact_cache, hermes_surface, migrations, runtime
from hermes_insights.local_surface import LocalSurface


ROOT = Path(__file__).resolve().parents[2]
RANGE = {"from": "2026-06-24", "to": "2026-06-30"}
QUERY = {"operation": "health_query", "arguments": {
    "feature_keys": ["subjective.energy"], "range": RANGE, "view": "summary",
}}
ANALYZE = {"operation": "health_analyze", "arguments": {
    "outcome_key": "subjective.energy", "exposure_keys": ["sleep.duration_hours"],
    "range": RANGE, "mode": "ordinal", "interactions": False, "min_n": 30, "top": 1,
}}


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "fictional.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / "toolkit/SCHEMA.sql").read_text())
    migrations.migrate(str(path), 3, 0, code_version="b" * 40)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO subjective_daily(date,energy) VALUES(?,?)",
                           ("2026-06-28", 7))
    return path


def evidence(result):
    return {"operation": "health_evidence", "arguments": {
        "evidence_refs": [result["evidence_refs"][0]], "detail": "lineage",
    }}


def test_instances_isolate_concurrent_reads_evidence_and_exact_cache(database, tmp_path, monkeypatch):
    second = tmp_path / "other-fictional.db"
    shutil.copyfile(database, second)
    a, b = LocalSurface(database), LocalSurface(second)
    original = associations.analyze_outcome
    calls = []

    def calculate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(associations, "analyze_outcome", calculate)
    original_database, original_environment = runtime.DB, dict(os.environ)
    before = {path: path.read_bytes() for path in (database, second)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, other = list(pool.map(lambda surface: surface.execute(QUERY), (a, b)))
    assert first["facts"] == other["facts"]
    assert first["dataset_id"] != other["dataset_id"]
    assert first["result_id"] != other["result_id"]
    assert a.execute(evidence(first))["facts"][0]["verified"] is True
    with pytest.raises(hermes_surface.SurfaceError, match="identity no longer matches"):
        b.execute(evidence(first))
    for surface in (a, b):
        analysis = surface.execute(ANALYZE)
        assert surface.execute(evidence(analysis))["facts"][0]["verified"] is True
    assert len(calls) == 2
    assert exact_cache.store_path(database) != exact_cache.store_path(second)
    assert runtime.DB == original_database and dict(os.environ) == original_environment
    assert all(path.read_bytes() == contents for path, contents in before.items())
    with sqlite3.connect(second) as connection:
        connection.execute("UPDATE subjective_daily SET energy=3")
    assert b.execute(QUERY)["facts"] != a.execute(QUERY)["facts"]
    assert a.execute(QUERY) == first


def test_local_and_legacy_share_results_without_relaxing_fictional_identity(database, monkeypatch):
    monkeypatch.setattr(runtime, "DB", str(database))
    monkeypatch.setenv("OPENHEALTHATLAS_DATA_CLASS", "fictional")
    monkeypatch.setenv("OPENHEALTHATLAS_FIXTURE_ID", "local-parity-fixture")
    local = LocalSurface(database)
    before = database.read_bytes()
    for request in ({"operation": "health_catalog", "arguments": {"search": "energy"}},
                    QUERY, ANALYZE):
        legacy, personal = hermes_surface.execute(request), local.execute(request)
        assert legacy["contract"] == "openhealthatlas-hermes-surface-v1"
        assert legacy["data_class"] == "fictional"
        assert legacy["fixture_id"] == "local-parity-fixture"
        assert personal["contract"] == "openhealthatlas-surface-v1"
        assert personal["data_class"] == "personal" and "fixture_id" not in personal
        for key in ("status", "range", "registry_id", "coverage", "facts",
                    "findings", "limitations"):
            assert personal[key] == legacy[key]
        old_evidence = hermes_surface.execute(evidence(legacy))
        new_evidence = local.execute(evidence(personal))
        for key in ("coverage", "facts", "findings", "limitations", "deterministic_summary"):
            assert new_evidence["facts"][0].get(key) == old_evidence["facts"][0].get(key)
        with pytest.raises(hermes_surface.SurfaceError, match="identity no longer matches"):
            hermes_surface.execute(evidence(personal))
    monkeypatch.setenv("OPENHEALTHATLAS_DATA_CLASS", "personal")
    with pytest.raises(hermes_surface.SurfaceError, match="fictional-only"):
        hermes_surface.execute(QUERY)
    assert local.execute(QUERY)["data_class"] == "personal"
    assert database.read_bytes() == before


def test_uri_filename_and_explicit_timezone_stay_bound_after_environment_changes(database, tmp_path, monkeypatch):
    special = tmp_path / "fictional ?#% records.db"
    shutil.copyfile(database, special)
    local = LocalSurface(special, timezone="America/Los_Angeles")
    before = special.read_bytes()
    monkeypatch.setenv("HEALTH_DB", str(tmp_path / "missing.db"))
    monkeypatch.setenv("HERMES_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("OPENHEALTHATLAS_DATA_CLASS", "fictional")
    result = local.execute(QUERY)
    assert result["facts"][0]["aggregates"]["mean"] == 7
    assert str(special) not in json.dumps(result)
    with pytest.raises(FrozenInstanceError):
        local.timezone = "UTC"
    fixed = lambda: datetime(2026, 7, 1, tzinfo=timezone.utc)
    explicit = runtime.adapter_context(clock=fixed, timezone=local.timezone)
    assert explicit.timezone == "America/Los_Angeles"
    assert explicit.functions["start_hhmm"]("2026-07-01T01:00:00+00:00") == "18:00"
    with runtime.connect_read_only(special) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE subjective_daily SET energy=1")
    assert special.read_bytes() == before
    assert not (tmp_path / "missing.db").exists()
