"""Private Recovery snapshot ancestry and public evidence separation."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "toolkit"))

from hermes_insights import recovery  # noqa: E402
from hermes_insights.contracts import canonical_json  # noqa: E402
from hermes_insights.readiness_ancestry import (  # noqa: E402
    ACCEPTED_V5_LANE,
    DEVELOPMENT_V6_LANE,
    DEVELOPMENT_V7_LANE,
    LANE_ENV,
    ReadinessAncestryError,
    SIDECAR_ENV,
    build_accepted_v5_sidecar,
    verified_readiness_snapshot,
)


ANCHOR = date(2026, 6, 30)
RANGE_START = date(2026, 3, 2)


def _v5_database(path: Path, *, note: str = "Legs mildly sore") -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(
          version INTEGER PRIMARY KEY, name TEXT NOT NULL,
          checksum_sha256 TEXT NOT NULL, applied_at TEXT NOT NULL,
          code_version TEXT NOT NULL
        );
        CREATE TABLE daily_metrics(
          date TEXT, source TEXT NOT NULL, resting_hr REAL, hrv_ms REAL,
          sleep_hours REAL, PRIMARY KEY(date,source)
        );
        CREATE TABLE sleep_log(
          date TEXT PRIMARY KEY, time_asleep_hours REAL, quality INTEGER,
          source TEXT, provenance TEXT
        );
        CREATE TABLE subjective_daily(
          date TEXT PRIMARY KEY, soreness_note TEXT, source TEXT
        );
        CREATE TABLE hevy_sets(
          id INTEGER PRIMARY KEY, date TEXT, workout_title TEXT, start_time TEXT,
          exercise_title TEXT, set_index INTEGER, set_type TEXT,
          weight_kg REAL, reps INTEGER, source TEXT
        );
        CREATE TABLE exercise_submuscles(
          exercise_title TEXT NOT NULL, muscle_group TEXT NOT NULL,
          sub_region TEXT NOT NULL, weight REAL NOT NULL,
          laterality TEXT NOT NULL, source TEXT NOT NULL, approx INTEGER NOT NULL,
          iso INTEGER NOT NULL, confidence TEXT NOT NULL,
          PRIMARY KEY(exercise_title,sub_region,laterality)
        );
        CREATE TABLE exercise_muscles(
          exercise_title TEXT, muscle TEXT, weight REAL, source TEXT,
          PRIMARY KEY(exercise_title,muscle)
        );
        CREATE TABLE source_sync_runs(
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL, status TEXT NOT NULL, coverage_from TEXT,
          coverage_to TEXT, rows_seen INTEGER, rows_written INTEGER,
          error_code TEXT, details_json TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO schema_migrations VALUES(?,?,?,?,?)",
        [
            (version, f"migration-{version}", f"sha256:{version:064x}",
             "2026-06-30T00:00:00+00:00", "fixture")
            for version in range(1, 6)
        ],
    )
    connection.execute(
        "INSERT INTO sleep_log VALUES(?,?,?,?,?)",
        ("2026-06-30", 7.0, 5, "fictional-demo", "fictional wearable"),
    )
    connection.execute(
        "INSERT INTO subjective_daily VALUES(?,?,?)",
        ("2026-06-30", note, "fictional-demo"),
    )
    connection.execute(
        "INSERT INTO hevy_sets VALUES(?,?,?,?,?,?,?,?,?,?)",
        (1, "2026-06-29", "Fictional workout", "10:00", "Fictional squat",
         1, "normal", 20.0, 8, "synthetic-demo"),
    )
    connection.execute(
        "INSERT INTO exercise_muscles VALUES(?,?,?,?)",
        ("Fictional squat", "Quads", 1.0, "synthetic-demo"),
    )
    connection.commit()
    connection.close()
    return path


def _context() -> dict:
    return recovery._readiness_calculation_context()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mark_schema_v6(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO schema_migrations VALUES(?,?,?,?,?)",
            (6, "migration-6", "sha256:" + "6" * 64,
             "2026-06-30T00:00:00+00:00", "fixture"),
        )


def _write_sidecar(database: Path, output: Path) -> dict:
    value = build_accepted_v5_sidecar(
        database,
        range_start=RANGE_START,
        anchor=ANCHOR,
        calculation_context=_context(),
    )
    output.write_text(canonical_json(value) + "\n", encoding="utf-8")
    output.chmod(0o600)
    return value


def _result(database: Path, sidecar: Path):
    with verified_readiness_snapshot(
        database,
        range_start=RANGE_START,
        anchor=ANCHOR,
        calculation_context=_context(),
        fixture_lane=ACCEPTED_V5_LANE,
        sidecar_path=str(sidecar),
    ) as (connection, attestation):
        result = recovery._readiness_result(
            connection,
            anchor=ANCHOR,
            range_start=RANGE_START,
            snapshot_attestation=attestation,
            clock=lambda: datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        )
    return result, attestation


def test_private_v5_sidecar_verifies_without_exposing_private_identity(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "readiness-ancestry.json"
    private = _write_sidecar(database, sidecar)
    result, attestation = _result(database, sidecar)

    assert private["historical_generation_event_proven"] is False
    assert private["snapshot"]["database_sha256"] == attestation.database_sha256
    assert private["snapshot"]["canonical_recovery_rows_sha256"] == (
        attestation.canonical_recovery_rows_sha256
    )
    assert private["snapshot"]["training_set_manifest_sha256"]
    assert private["snapshot"]["authored_exercise_mapping_sha256"]
    assert private["snapshot"]["coarse_exercise_mapping_sha256"]
    assert private["snapshot"]["static_mapping_policy_sha256"]

    evidence = result["evidence"]
    assert evidence["contract"] == "readiness-evidence-v2"
    assert evidence["policy_sha256"] == recovery.READINESS_POLICY_SHA256
    assert evidence["snapshot_integrity"] == {
        "contract": "openhealthatlas-readiness-ancestry-v2",
        "status": "verified",
        "scope": "current_snapshot_integrity_and_reproducibility",
        "schema_version": 5,
        "external_provider_sync": "not_performed",
        "training_ancestry": "manifested",
    }
    serialized = canonical_json(evidence)
    for private_value in (
        str(sidecar),
        private["sidecar_sha256"],
        private["snapshot"]["database_sha256"],
        private["snapshot"]["canonical_recovery_rows_sha256"],
    ):
        assert private_value not in serialized
    assert "content_fingerprint" not in serialized
    assert result["soreness"]["note"] not in serialized


def test_accepted_v5_build_and_verification_never_migrate_database(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    before_sha256 = _file_sha256(database)
    before_stat = database.stat()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()[0] == 5

    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    _result(database, sidecar)

    after_stat = database.stat()
    assert _file_sha256(database) == before_sha256
    assert (after_stat.st_dev, after_stat.st_ino, after_stat.st_size,
            after_stat.st_mtime_ns, after_stat.st_ctime_ns) == (
        before_stat.st_dev, before_stat.st_ino, before_stat.st_size,
        before_stat.st_mtime_ns, before_stat.st_ctime_ns,
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()[0] == 5


def test_schema_v5_requires_explicit_lane_and_matching_private_sidecar(
    tmp_path, monkeypatch,
):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    monkeypatch.delenv(LANE_ENV, raising=False)
    monkeypatch.delenv(SIDECAR_ENV, raising=False)

    with pytest.raises(ReadinessAncestryError, match="explicit accepted-v5"):
        with verified_readiness_snapshot(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
        ):
            pass
    with pytest.raises(ReadinessAncestryError, match="require an ancestry sidecar"):
        with verified_readiness_snapshot(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
            fixture_lane=ACCEPTED_V5_LANE,
        ):
            pass
    with pytest.raises(ReadinessAncestryError, match="requires schema version 6"):
        with verified_readiness_snapshot(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
            fixture_lane=DEVELOPMENT_V6_LANE,
        ):
            pass


@pytest.mark.parametrize("schema_version, lane", [(6, DEVELOPMENT_V6_LANE), (7, DEVELOPMENT_V7_LANE)])
def test_development_lanes_are_distinct_and_reject_v5_sidecars(
    tmp_path, schema_version, lane,
):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    _mark_schema_v6(database)
    if schema_version == 7:
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO schema_migrations VALUES(?,?,?,?,?)",
                (7, "migration-7", "sha256:" + "7" * 64,
                 "2026-06-30T00:00:00+00:00", "fixture"),
            )

    with verified_readiness_snapshot(
        database,
        range_start=RANGE_START,
        anchor=ANCHOR,
        calculation_context=_context(),
        fixture_lane=lane,
    ) as (_connection, attestation):
        assert attestation.fixture_lane == lane
        assert attestation.schema_version == schema_version

    with pytest.raises(ReadinessAncestryError, match="cannot be used"):
        with verified_readiness_snapshot(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
            fixture_lane=lane,
            sidecar_path=str(sidecar),
        ):
            pass
    with pytest.raises(ReadinessAncestryError, match="requires schema version 5"):
        with verified_readiness_snapshot(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
            fixture_lane=ACCEPTED_V5_LANE,
            sidecar_path=str(sidecar),
        ):
            pass

    other_lane = DEVELOPMENT_V7_LANE if schema_version == 6 else DEVELOPMENT_V6_LANE
    with pytest.raises(ReadinessAncestryError, match="requires schema version"):
        with verified_readiness_snapshot(
            database, range_start=RANGE_START, anchor=ANCHOR,
            calculation_context=_context(), fixture_lane=other_lane,
        ):
            pass
    with verified_readiness_snapshot(
        database, range_start=RANGE_START, anchor=ANCHOR,
        calculation_context=_context(),
    ) as (_connection, attestation):
        assert attestation.fixture_lane == lane


def test_stale_sidecar_fails_but_note_equivalent_public_identities_stay_stable(tmp_path):
    database = _v5_database(tmp_path / "health.db", note="Legs mildly sore alpha")
    first_sidecar = tmp_path / "first.json"
    first_private = _write_sidecar(database, first_sidecar)
    first_result, _first_attestation = _result(database, first_sidecar)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE subjective_daily SET soreness_note=? WHERE date=?",
            ("Legs mildly sore beta", "2026-06-30"),
        )

    with pytest.raises(ReadinessAncestryError, match="sidecar .* stale"):
        _result(database, first_sidecar)

    second_sidecar = tmp_path / "second.json"
    second_private = _write_sidecar(database, second_sidecar)
    second_result, _second_attestation = _result(database, second_sidecar)

    assert first_private["snapshot"]["canonical_recovery_rows_sha256"] != (
        second_private["snapshot"]["canonical_recovery_rows_sha256"]
    )
    assert first_result["evidence"]["input_fingerprint"] == (
        second_result["evidence"]["input_fingerprint"]
    )
    assert first_result["evidence"]["public_evidence_identity"] == (
        second_result["evidence"]["public_evidence_identity"]
    )


def test_database_and_sidecar_symlinks_are_rejected(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    database_link = tmp_path / "linked.db"
    database_link.symlink_to(database)
    with pytest.raises(ReadinessAncestryError, match="symbolic link"):
        build_accepted_v5_sidecar(
            database_link,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
        )
    sidecar_link = tmp_path / "linked.json"
    sidecar_link.symlink_to(sidecar)
    with pytest.raises(ReadinessAncestryError, match="symbolic link"):
        _result(database, sidecar_link)


def test_private_sidecar_permissions_fail_closed(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    sidecar.chmod(0o640)
    with pytest.raises(ReadinessAncestryError, match="group or others"):
        _result(database, sidecar)


def test_sidecar_is_remeasured_after_the_calculation_transaction(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    with pytest.raises(ReadinessAncestryError, match="changed across"):
        with verified_readiness_snapshot(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
            fixture_lane=ACCEPTED_V5_LANE,
            sidecar_path=str(sidecar),
        ):
            sidecar.write_bytes(sidecar.read_bytes() + b" ")


def test_sidecar_parser_bytes_must_match_the_measured_file(
    tmp_path, monkeypatch,
):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    original_read_bytes = Path.read_bytes

    def substituted_read_bytes(path: Path) -> bytes:
        value = original_read_bytes(path)
        return value + b" " if path == sidecar else value

    monkeypatch.setattr(Path, "read_bytes", substituted_read_bytes)
    with pytest.raises(ReadinessAncestryError, match="changed while it was read"):
        _result(database, sidecar)


def test_database_is_rehashed_and_restatted_after_the_calculation_transaction(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "sidecar.json"
    _write_sidecar(database, sidecar)
    with pytest.raises(ReadinessAncestryError, match="snapshot changed across"):
        with verified_readiness_snapshot(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
            fixture_lane=ACCEPTED_V5_LANE,
            sidecar_path=str(sidecar),
        ):
            with database.open("ab") as handle:
                handle.write(b"post-transaction-integrity-test")


def test_accepted_v5_rejects_any_provider_sync_run(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO source_sync_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (1, "fitbit", "2026-06-30T00:00:00Z", "2026-06-30T00:01:00Z",
             "success", "2026-06-30", "2026-06-30", 1, 1, None, "{}"),
        )
    with pytest.raises(ReadinessAncestryError, match="provider sync runs"):
        build_accepted_v5_sidecar(
            database,
            range_start=RANGE_START,
            anchor=ANCHOR,
            calculation_context=_context(),
        )


def test_bounded_generator_writes_0600_once_and_generated_sidecar_verifies(tmp_path):
    database = _v5_database(tmp_path / "health.db")
    sidecar = tmp_path / "generated.json"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "build_readiness_ancestry.py"),
        "--database", str(database),
        "--from", RANGE_START.isoformat(),
        "--anchor", ANCHOR.isoformat(),
        "--output", str(sidecar),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "HERMES_TIMEZONE": "UTC"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {
        "ok": True,
        "contract": "openhealthatlas-readiness-ancestry-v2",
        "fixture_lane": "accepted-v5",
        "schema_version": 5,
        "scope": "current_snapshot_integrity_and_reproducibility",
    }
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    _result(database, sidecar)

    repeated = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "HERMES_TIMEZONE": "UTC"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert repeated.returncode == 1
    assert "refusing to replace" in repeated.stderr
