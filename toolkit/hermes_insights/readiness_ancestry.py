"""Private, fail-closed snapshot ancestry for deterministic Recovery output.

The accepted fictional database remains schema v5.  This module never
migrates it: it opens SQLite in ``mode=ro``, holds one explicit read
transaction across the caller's calculation, and verifies stable file hashes
and file statistics on both sides of that transaction.  The private sidecar
binds the exact database bytes to calculation-relevant canonical row
manifests.  Only :attr:`SnapshotAttestation.public_summary` may cross the
OpenHealthAtlas projection boundary.

The v5 sidecar proves the integrity and reproducibility of the *current*
snapshot.  It deliberately does not claim to prove the original historical
fixture-generation invocation.

Development-v6 and development-v7 databases have separate initialization contracts:
``SCHEMA.sql`` provides structure only, then the explicit migration workflow
must record the contiguous ledger through the selected lane version before Recovery reads the
database.  This module never treats an empty, missing, malformed, or gapped
ledger as an implicitly current development database.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Iterator, Mapping

from .provenance import sha256_id


ANCESTRY_CONTRACT = "openhealthatlas-readiness-ancestry-v2"
ACCEPTED_V5_LANE = "accepted-v5"
DEVELOPMENT_V6_LANE = "development-v6"
DEVELOPMENT_V7_LANE = "development-v7"
_LANE_SCHEMA = {ACCEPTED_V5_LANE: 5, DEVELOPMENT_V6_LANE: 6, DEVELOPMENT_V7_LANE: 7}
LANE_ENV = "OPENHEALTHATLAS_READINESS_FIXTURE_LANE"
SIDECAR_ENV = "OPENHEALTHATLAS_READINESS_ANCESTRY_SIDECAR"

# These are the exact files at the accepted fixture source revision recorded by
# the fictional VPS preflight ledger.  They are byte-identical at
# fbfe468f8a13308299c74158eb54661bf33da64e, the parent of the v6-introducing
# boundary commit 47c2ca51f624d2a455117736695b459d4967edd7.  They identify
# reproducibility materials; they are not evidence of the original generator
# invocation.
ACCEPTED_V5_SOURCE_ARTIFACTS = {
    "accepted_fixture_source_commit": (
        "4036d4b2e95d10af6a8140a3ba11711d82fa00aa"
    ),
    "equivalent_pre_v6_source_commit": (
        "fbfe468f8a13308299c74158eb54661bf33da64e"
    ),
    "v6_boundary_child_commit": "47c2ca51f624d2a455117736695b459d4967edd7",
    "schema_sql_sha256": (
        "sha256:11abbe1051ca933a384977b39912bb188770295bffe7b9d946ca17db3482eb7a"
    ),
    "migrations_py_sha256": (
        "sha256:e197c5a65cbaa5c19388ee769f6dc5104e828eb2ce7bcdf7b5f485e87a2dd3f3"
    ),
    "fixture_generator_sha256": (
        "sha256:cbe2e17c1322574c4add6a8a85ff5050f923459011a5059306ebbf195f69409c"
    ),
}

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReadinessAncestryError(RuntimeError):
    """A private snapshot cannot be trusted for a Recovery calculation."""

    validation = False

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SnapshotAttestation:
    """Verified private state plus the only projection-safe summary."""

    fixture_lane: str
    schema_version: int
    canonical_recovery_rows_sha256: str
    database_sha256: str
    public_summary: Mapping[str, Any]


def _fail(code: str, message: str) -> None:
    raise ReadinessAncestryError(code, message)


def _file_sha256(path: Path, *, label: str = "Recovery snapshot") -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        _fail("snapshot_unavailable", f"{label} is unavailable")
        raise AssertionError from exc
    return "sha256:" + digest.hexdigest()


def _stat_identity(path: Path, *, label: str = "Recovery snapshot") -> dict[str, int]:
    try:
        value = path.stat(follow_symlinks=False)
    except OSError as exc:
        _fail("snapshot_unavailable", f"{label} is unavailable")
        raise AssertionError from exc
    if not stat.S_ISREG(value.st_mode) or path.is_symlink():
        _fail("snapshot_path_rejected", f"{label} must be a regular non-symlink file")
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": stat.S_IMODE(value.st_mode),
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _stable_measurement(
    path: Path, *, label: str = "Recovery snapshot",
) -> dict[str, Any]:
    before = _stat_identity(path, label=label)
    digest = _file_sha256(path, label=label)
    after = _stat_identity(path, label=label)
    if before != after:
        _fail("snapshot_changed", "Recovery snapshot changed while it was hashed")
    return {"stat": before, "sha256": digest}


def _strict_regular_path(value: str | Path, *, label: str) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        _fail("snapshot_path_rejected", f"{label} must be an absolute path")
    normalized = Path(os.path.abspath(requested))
    if requested != normalized or requested.is_symlink():
        _fail(
            "snapshot_path_rejected",
            f"{label} must be a canonical path without a symbolic link",
        )
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        _fail("snapshot_unavailable", f"{label} is unavailable")
        raise AssertionError from exc
    if resolved != normalized:
        _fail(
            "snapshot_path_rejected",
            f"{label} must not traverse a symbolic link",
        )
    _stat_identity(resolved, label=label)
    return resolved


def _require_no_sqlite_sidecars(path: Path) -> None:
    # A main-file hash is an exact logical-snapshot identity only while no
    # SQLite write-ahead or rollback state is present.
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            _fail(
                "sqlite_sidecar_present",
                "Recovery snapshot has SQLite write state outside the main database file",
            )


def _open_ro(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return connection
    except sqlite3.Error as exc:
        _fail("snapshot_unavailable", "Recovery snapshot could not be opened read-only")
        raise AssertionError from exc


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _schema_version(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "schema_migrations"):
        _fail("schema_ledger_missing", "Recovery snapshot has no migration ledger")
    required_columns = {
        "version", "name", "checksum_sha256", "applied_at", "code_version",
    }
    if _columns(connection, "schema_migrations") != required_columns:
        _fail(
            "schema_ledger_invalid",
            "Recovery snapshot migration ledger is malformed",
        )
    try:
        raw_rows = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        rows = [int(row[0]) for row in raw_rows]
    except (sqlite3.Error, TypeError, ValueError):
        _fail(
            "schema_ledger_invalid",
            "Recovery snapshot migration ledger is malformed",
        )
    if not rows or rows != list(range(1, max(rows) + 1)):
        _fail("schema_ledger_invalid", "Recovery snapshot migration ledger is not contiguous")
    return rows[-1]


def _rows(
    connection: sqlite3.Connection,
    table: str,
    wanted_columns: tuple[str, ...],
    *,
    where: str = "",
    parameters: tuple[Any, ...] = (),
    order_by: tuple[str, ...],
) -> tuple[list[str], list[dict[str, Any]]]:
    available = _columns(connection, table)
    if not available:
        _fail("ancestry_table_missing", f"Recovery ancestry table is missing: {table}")
    selected = [column for column in wanted_columns if column in available]
    if not selected or any(column not in available for column in order_by):
        _fail("ancestry_schema_invalid", f"Recovery ancestry schema is incompatible: {table}")
    quoted = ",".join(f'"{column}"' for column in selected)
    ordering = ",".join(f'"{column}"' for column in order_by)
    sql = f'SELECT {quoted} FROM "{table}"'
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY " + ordering
    values = [dict(row) for row in connection.execute(sql, parameters)]
    return selected, values


def _manifest(
    table: str, columns: list[str], rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "table": table,
        "row_count": len(rows),
        "rows_sha256": sha256_id({"table": table, "columns": columns, "rows": rows}),
    }


def _canonical_manifests(
    connection: sqlite3.Connection,
    *,
    range_start: date | None,
    anchor: date,
) -> tuple[list[dict[str, Any]], str, str]:
    lower = range_start.isoformat() if range_start is not None else "0001-01-01"
    upper = anchor.isoformat()
    table_specs = (
        (
            "daily_metrics",
            ("date", "source", "resting_hr", "hrv_ms", "hrv_sdnn", "sleep_hours"),
            '"date">=? AND "date"<=?',
            (lower, upper),
            ("date", "source"),
        ),
        (
            "sleep_log",
            ("date", "time_asleep_hours", "quality", "source", "provenance"),
            '"date">=? AND "date"<=?',
            (lower, upper),
            ("date",),
        ),
        (
            "subjective_daily",
            ("date", "soreness_note", "source"),
            '"date">=? AND "date"<=?',
            ((anchor - timedelta(days=1)).isoformat(), upper),
            ("date",),
        ),
        (
            "hevy_sets",
            (
                "id", "date", "workout_title", "start_time", "exercise_title",
                "set_index", "set_type", "weight_kg", "reps", "source",
            ),
            '"date">=? AND "date"<=?',
            (lower, upper),
            ("id",),
        ),
        (
            "exercise_submuscles",
            (
                "exercise_title", "muscle_group", "sub_region", "weight",
                "laterality", "source", "approx", "iso", "confidence",
            ),
            "",
            (),
            ("exercise_title", "sub_region", "laterality"),
        ),
        (
            "exercise_muscles",
            ("exercise_title", "muscle", "weight", "source"),
            "",
            (),
            ("exercise_title", "muscle"),
        ),
        (
            "source_sync_runs",
            (
                "id", "source", "started_at", "completed_at", "status",
                "coverage_from", "coverage_to", "rows_seen", "rows_written",
                "error_code", "details_json",
            ),
            "",
            (),
            ("id",),
        ),
    )
    manifests: list[dict[str, Any]] = []
    sync_status = "not_performed"
    for table, wanted, where, parameters, order_by in table_specs:
        columns, values = _rows(
            connection,
            table,
            wanted,
            where=where,
            parameters=parameters,
            order_by=order_by,
        )
        if table == "source_sync_runs" and values:
            sync_status = "manifested"
        manifests.append(_manifest(table, columns, values))
    return manifests, sha256_id(manifests), sync_status


def _schema_manifest(connection: sqlite3.Connection) -> tuple[str, str]:
    ledger = [dict(row) for row in connection.execute(
        "SELECT version,name,checksum_sha256,applied_at,code_version "
        "FROM schema_migrations ORDER BY version"
    )]
    schema = [dict(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
    )]
    return sha256_id(ledger), sha256_id(schema)


def _snapshot(
    connection: sqlite3.Connection,
    *,
    database_measurement: Mapping[str, Any],
    range_start: date | None,
    anchor: date,
    calculation_context: Mapping[str, Any],
) -> dict[str, Any]:
    version = _schema_version(connection)
    manifests, rows_sha256, sync_status = _canonical_manifests(
        connection, range_start=range_start, anchor=anchor,
    )
    manifest_by_table = {item["table"]: item for item in manifests}
    static_mapping_policy = calculation_context.get("static_mapping_policy")
    if not isinstance(static_mapping_policy, Mapping):
        _fail(
            "calculation_context_invalid",
            "Recovery calculation context has no explicit static mapping policy",
        )
    ledger_sha256, schema_sha256 = _schema_manifest(connection)
    return {
        "database_sha256": database_measurement["sha256"],
        "database_size_bytes": database_measurement["stat"]["size"],
        "schema_version": version,
        "migration_ledger_sha256": ledger_sha256,
        "sqlite_schema_sha256": schema_sha256,
        "canonical_recovery_rows_sha256": rows_sha256,
        "table_manifests": manifests,
        "training_set_manifest_sha256": manifest_by_table["hevy_sets"]["rows_sha256"],
        "authored_exercise_mapping_sha256": (
            manifest_by_table["exercise_submuscles"]["rows_sha256"]
        ),
        "coarse_exercise_mapping_sha256": (
            manifest_by_table["exercise_muscles"]["rows_sha256"]
        ),
        "static_mapping_policy_sha256": sha256_id(static_mapping_policy),
        "source_sync_run_manifest_sha256": (
            manifest_by_table["source_sync_runs"]["rows_sha256"]
        ),
        "calculation_context_sha256": sha256_id(calculation_context),
        "external_provider_sync": sync_status,
    }


def _sidecar_identity(value: Mapping[str, Any]) -> str:
    return sha256_id({key: value[key] for key in value if key != "sidecar_sha256"})


def build_accepted_v5_sidecar(
    database: str | Path,
    *,
    range_start: date | None,
    anchor: date,
    calculation_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Return, but never write, a private sidecar for a stable v5 snapshot."""

    path = _strict_regular_path(database, label="Recovery snapshot database")
    _require_no_sqlite_sidecars(path)
    before = _stable_measurement(path)
    connection = _open_ro(path)
    try:
        snapshot = _snapshot(
            connection,
            database_measurement=before,
            range_start=range_start,
            anchor=anchor,
            calculation_context=calculation_context,
        )
        if snapshot["schema_version"] != 5:
            _fail("fixture_lane_mismatch", "Accepted v5 sidecars require schema version 5")
        if snapshot["external_provider_sync"] != "not_performed":
            _fail(
                "external_provider_sync_unexpected",
                "Accepted fictional v5 Recovery snapshot has provider sync runs",
            )
        connection.commit()
    finally:
        connection.close()
    _require_no_sqlite_sidecars(path)
    after = _stable_measurement(path)
    if before != after:
        _fail("snapshot_changed", "Recovery snapshot changed across its read transaction")
    body: dict[str, Any] = {
        "contract": ANCESTRY_CONTRACT,
        "fixture_lane": ACCEPTED_V5_LANE,
        "scope": "current_snapshot_integrity_and_reproducibility",
        "historical_generation_event_proven": False,
        "range": {
            "from": range_start.isoformat() if range_start is not None else None,
            "anchor": anchor.isoformat(),
        },
        "source_artifacts": ACCEPTED_V5_SOURCE_ARTIFACTS,
        "snapshot": snapshot,
    }
    body["sidecar_sha256"] = _sidecar_identity(body)
    return body


def _load_sidecar(path_value: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    path = Path(path_value)
    if not path.is_absolute():
        _fail("sidecar_path_rejected", "Recovery ancestry sidecar path must be absolute")
    path = _strict_regular_path(path, label="Recovery ancestry sidecar")
    before_measurement = _stable_measurement(
        path, label="Recovery ancestry sidecar",
    )
    if before_measurement["stat"]["mode"] & 0o077:
        _fail(
            "sidecar_permissions_invalid",
            "Recovery ancestry sidecar must not be accessible to group or others",
        )
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("sidecar_invalid", "Recovery ancestry sidecar is unavailable or invalid")
        raise AssertionError from exc
    after_measurement = _stable_measurement(
        path, label="Recovery ancestry sidecar",
    )
    raw_measurement = {
        "size": len(raw),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    if (
        before_measurement != after_measurement
        or raw_measurement["size"] != before_measurement["stat"]["size"]
        or raw_measurement["sha256"] != before_measurement["sha256"]
    ):
        _fail("sidecar_changed", "Recovery ancestry sidecar changed while it was read")
    if not isinstance(parsed, dict):
        _fail("sidecar_invalid", "Recovery ancestry sidecar must be a JSON object")
    return parsed, before_measurement, path


def _validate_sidecar(
    sidecar: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    range_start: date | None,
    anchor: date,
) -> None:
    expected_keys = {
        "contract", "fixture_lane", "scope", "historical_generation_event_proven",
        "range", "source_artifacts", "snapshot", "sidecar_sha256",
    }
    if set(sidecar) != expected_keys:
        _fail("sidecar_invalid", "Recovery ancestry sidecar fields do not match the contract")
    supplied_identity = sidecar.get("sidecar_sha256")
    if not isinstance(supplied_identity, str) or not _SHA256_RE.fullmatch(supplied_identity):
        _fail("sidecar_invalid", "Recovery ancestry sidecar identity is invalid")
    if supplied_identity != _sidecar_identity(sidecar):
        _fail(
            "sidecar_identity_mismatch",
            "Recovery ancestry sidecar identity does not match its body",
        )
    expected_range = {
        "from": range_start.isoformat() if range_start is not None else None,
        "anchor": anchor.isoformat(),
    }
    fixed = {
        "contract": ANCESTRY_CONTRACT,
        "fixture_lane": ACCEPTED_V5_LANE,
        "scope": "current_snapshot_integrity_and_reproducibility",
        "historical_generation_event_proven": False,
        "range": expected_range,
        "source_artifacts": ACCEPTED_V5_SOURCE_ARTIFACTS,
        "snapshot": snapshot,
    }
    for field, expected in fixed.items():
        if sidecar.get(field) != expected:
            _fail("stale_ancestry_sidecar", f"Recovery ancestry sidecar {field} is stale")


def _lane(configured: str | None, schema_version: int) -> str:
    if configured is None:
        if schema_version in {6, 7}:
            return DEVELOPMENT_V6_LANE if schema_version == 6 else DEVELOPMENT_V7_LANE
        _fail(
            "fixture_lane_required",
            "Schema-v5 Recovery snapshots require the explicit accepted-v5 fixture lane",
        )
    if configured not in _LANE_SCHEMA:
        _fail("fixture_lane_invalid", "Recovery fixture lane is invalid")
    expected = _LANE_SCHEMA[configured]
    if schema_version != expected:
        _fail(
            "fixture_lane_mismatch",
            f"Recovery fixture lane {configured} requires schema version {expected}",
        )
    return configured


@contextmanager
def verified_readiness_snapshot(
    database: str | Path,
    *,
    range_start: date | None,
    anchor: date,
    calculation_context: Mapping[str, Any],
    fixture_lane: str | None = None,
    sidecar_path: str | None = None,
) -> Iterator[tuple[sqlite3.Connection, SnapshotAttestation]]:
    """Yield one verified RO connection spanning the Recovery calculation."""

    path = _strict_regular_path(database, label="Recovery snapshot database")
    _require_no_sqlite_sidecars(path)
    before = _stable_measurement(path)
    connection = _open_ro(path)
    transaction_started = False
    sidecar_measurement: dict[str, Any] | None = None
    measured_sidecar_path: Path | None = None
    try:
        snapshot = _snapshot(
            connection,
            database_measurement=before,
            range_start=range_start,
            anchor=anchor,
            calculation_context=calculation_context,
        )
        lane = _lane(fixture_lane if fixture_lane is not None else os.environ.get(LANE_ENV),
                     int(snapshot["schema_version"]))
        configured_sidecar = (
            sidecar_path if sidecar_path is not None else os.environ.get(SIDECAR_ENV)
        )
        if lane == ACCEPTED_V5_LANE:
            if snapshot["external_provider_sync"] != "not_performed":
                _fail(
                    "external_provider_sync_unexpected",
                    "Accepted fictional v5 Recovery snapshot has provider sync runs",
                )
            if not configured_sidecar:
                _fail(
                    "sidecar_required",
                    "Accepted v5 Recovery snapshots require an ancestry sidecar",
                )
            loaded_sidecar, sidecar_measurement, measured_sidecar_path = _load_sidecar(
                configured_sidecar
            )
            _validate_sidecar(
                loaded_sidecar,
                snapshot=snapshot,
                range_start=range_start,
                anchor=anchor,
            )
        elif configured_sidecar:
            _fail(
                "sidecar_lane_mismatch",
                "A v5 ancestry sidecar cannot be used in a development lane",
            )

        public_summary = {
            "contract": ANCESTRY_CONTRACT,
            "status": "verified",
            "scope": "current_snapshot_integrity_and_reproducibility",
            "schema_version": int(snapshot["schema_version"]),
            "external_provider_sync": snapshot["external_provider_sync"],
            "training_ancestry": "manifested",
        }
        attestation = SnapshotAttestation(
            fixture_lane=lane,
            schema_version=int(snapshot["schema_version"]),
            canonical_recovery_rows_sha256=snapshot["canonical_recovery_rows_sha256"],
            database_sha256=snapshot["database_sha256"],
            public_summary=public_summary,
        )
        transaction_started = True
        yield connection, attestation
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        if transaction_started:
            _require_no_sqlite_sidecars(path)
            after = _stable_measurement(path)
            if before != after:
                _fail("snapshot_changed", "Recovery snapshot changed across its read transaction")
            if sidecar_measurement is not None and measured_sidecar_path is not None:
                after_sidecar = _stable_measurement(
                    measured_sidecar_path, label="Recovery ancestry sidecar",
                )
                if sidecar_measurement != after_sidecar:
                    _fail(
                        "sidecar_changed",
                        "Recovery ancestry sidecar changed across the read transaction",
                    )


__all__ = [
    "ACCEPTED_V5_LANE",
    "ANCESTRY_CONTRACT",
    "DEVELOPMENT_V6_LANE",
    "DEVELOPMENT_V7_LANE",
    "ReadinessAncestryError",
    "SnapshotAttestation",
    "build_accepted_v5_sidecar",
    "verified_readiness_snapshot",
]
