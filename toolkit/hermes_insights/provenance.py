"""Deterministic provenance and identifiers for outcome association evidence.

This module never opens a database and never mutates the injected connection.
Optional Phase 2/3 provenance tables are inspected with ``SELECT`` statements
only.  Missing or incompatible optional tables are represented explicitly so
an evidence fingerprint cannot silently pretend that dependency data existed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .contracts import canonical_json


ANALYSIS_CONTRACT_VERSION = "outcome-associations-v1"
ANALYSIS_VERSION = "outcome-v1"

_ENGINE_FILES = (
    "_native_stats.py",
    "associations.py",
    "interactions.py",
    "native/rank_products.c",
    "provenance.py",
    "stats.py",
)
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPTIONAL_TABLES = (
    "capture_completeness_revisions",
    "source_sync_runs",
    "entity_aliases",
    "training_plan_revisions",
    "insight_goal_revisions",
)


class ProvenanceError(ValueError):
    """Raised when analytical provenance cannot be represented canonically."""

    code = "provenance_error"
    validation = False


def sha256_id(value: Any) -> str:
    """Return a prefixed SHA-256 over the shared canonical JSON serialization."""

    payload = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _normalize_sha256(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ProvenanceError(f"{name} must be a SHA-256 identifier")
    match = _SHA256_RE.fullmatch(value)
    if match is None:
        raise ProvenanceError(f"{name} must be a SHA-256 identifier")
    return f"sha256:{match.group(1)}"


def _nonempty_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProvenanceError(f"{name} must be non-empty text")
    return value


def _component_value(component: Any, name: str) -> Any:
    if isinstance(component, Mapping):
        return component.get(name)
    return getattr(component, name, None)


def _normalize_component(component: Any) -> dict[str, Any]:
    required = {"exposure_key", "lag_days", "window_days", "transform"}
    if isinstance(component, Mapping):
        missing = sorted(required - set(component))
        # ``temporal_direction`` is a presentation field carried by the
        # association result.  It never participates in conceptual candidate
        # identity, but accepting and dropping that one known field keeps the
        # identifier boundary usable with result component objects.
        unknown = sorted(set(component) - required - {"temporal_direction"})
        if missing or unknown:
            raise ProvenanceError(
                f"component fields mismatch missing={missing} unknown={unknown}"
            )
    exposure_key = _nonempty_text(
        _component_value(component, "exposure_key"), "exposure_key"
    )
    lag_days = _component_value(component, "lag_days")
    window_days = _component_value(component, "window_days")
    transform = _nonempty_text(_component_value(component, "transform"), "transform")
    if isinstance(lag_days, bool) or not isinstance(lag_days, int) or lag_days < 0:
        raise ProvenanceError("lag_days must be a nonnegative integer")
    if (
        isinstance(window_days, bool)
        or not isinstance(window_days, int)
        or window_days < 1
    ):
        raise ProvenanceError("window_days must be a positive integer")
    return {
        "exposure_key": exposure_key,
        "lag_days": lag_days,
        "window_days": window_days,
        "transform": transform,
    }


def candidate_key(
    outcome_key: str,
    outcome_mode: str,
    components: Iterable[Mapping[str, Any] | Any],
) -> str:
    """Return the canonical one- or two-component analytical candidate key."""

    outcome_key = _nonempty_text(outcome_key, "outcome_key")
    outcome_mode = _nonempty_text(outcome_mode, "outcome_mode")
    normalized = [_normalize_component(component) for component in components]
    if len(normalized) not in (1, 2):
        raise ProvenanceError("candidate must contain exactly one or two components")
    normalized.sort(
        key=lambda item: (
            item["exposure_key"],
            item["lag_days"],
            item["window_days"],
            item["transform"],
        )
    )
    if len(normalized) == 2 and normalized[0] == normalized[1]:
        raise ProvenanceError("candidate components must be distinct")
    return canonical_json(
        {
            "outcome_key": outcome_key,
            "outcome_mode": outcome_mode,
            "components": normalized,
        }
    )


def engine_manifest(package_dir: str | Path | None = None) -> list[dict[str, str]]:
    """Return sorted digests for the Python engine and optional native kernel."""

    root = Path(package_dir) if package_dir is not None else Path(__file__).parent
    manifest: list[dict[str, str]] = []
    for filename in sorted(_ENGINE_FILES):
        path = root / filename
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ProvenanceError(f"engine file unavailable: {filename}") from exc
        manifest.append(
            {"file": filename, "sha256": hashlib.sha256(content).hexdigest()}
        )
    return manifest


def engine_sha256(package_dir: str | Path | None = None) -> str:
    """Hash the canonical sorted manifest of the complete Phase 4 engine."""

    return sha256_id(engine_manifest(package_dir))


def _observation_value(observation: Any, name: str, default: Any = None) -> Any:
    if isinstance(observation, Mapping):
        return observation.get(name, default)
    return getattr(observation, name, default)


def _iso_observed_at(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if not isinstance(value, str) or not value:
        raise ProvenanceError("observation observed_at must be a non-empty ISO value")
    try:
        parsed = date.fromisoformat(value[:10])
        if parsed.isoformat() != value[:10]:
            raise ValueError
        if len(value) > 10:
            datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ProvenanceError("observation observed_at must be ISO date or datetime") from None
    return value


def _canonical_value(value: Any) -> Any:
    """Round-trip through the sole canonical serializer to expose normalization."""

    return json.loads(canonical_json(value))


def _natural_key_items(provenance: Mapping[str, Any]) -> list[tuple[str, str]]:
    found: dict[str, str] = {}

    direct = provenance.get("natural_key")
    if direct is not None:
        if not isinstance(direct, str) or not direct:
            raise ProvenanceError("natural_key must be non-empty text or null")
        found[direct] = "direct"

    for field, relationship in (
        ("natural_keys", "direct"),
        ("parent_natural_keys", "parent"),
    ):
        values = provenance.get(field, ())
        if values is None:
            continue
        if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
            raise ProvenanceError(f"{field} must be an array")
        for item in values:
            if not isinstance(item, str) or not item:
                raise ProvenanceError(f"{field} must contain non-empty text")
            # A direct reference wins if the same source row is also declared
            # as a parent by a derived observation.
            if item not in found or relationship == "direct":
                found[item] = relationship
    return sorted(found.items(), key=lambda item: (item[0], item[1]))


def _table_from_reference(natural_key: str, fallback: Any) -> str | None:
    prefix, separator, _rest = natural_key.partition(":")
    if separator and _IDENTIFIER_RE.fullmatch(prefix):
        return prefix
    if isinstance(fallback, str) and fallback:
        return fallback
    return None


def normalize_row_references(observations: Iterable[Any]) -> list[dict[str, Any]]:
    """Expand direct and parent natural keys into stable audit references."""

    unique: dict[str, dict[str, Any]] = {}
    for observation in observations:
        feature_key = _nonempty_text(
            _observation_value(observation, "feature_key"), "feature_key"
        )
        observed_at = _iso_observed_at(
            _observation_value(observation, "observed_at")
        )
        state = _nonempty_text(_observation_value(observation, "state"), "state")
        source = _observation_value(observation, "source")
        if source is not None and (not isinstance(source, str) or not source):
            raise ProvenanceError("observation source must be non-empty text or null")
        provenance = _observation_value(observation, "provenance") or {}
        if not isinstance(provenance, Mapping):
            raise ProvenanceError("observation provenance must be an object or null")
        adapter = provenance.get("adapter")
        table = provenance.get("table")
        merge = provenance.get("merge")
        for name, value in (("adapter", adapter), ("table", table), ("merge", merge)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ProvenanceError(f"provenance {name} must be non-empty text or null")
        for natural_key, relationship in _natural_key_items(provenance):
            reference = {
                "feature_key": feature_key,
                "observed_at": observed_at,
                "source": source,
                "state": state,
                "table": _table_from_reference(natural_key, table),
                "adapter": adapter,
                "merge_rule": merge,
                "natural_key": natural_key,
                "relationship": relationship,
            }
            unique[canonical_json(reference)] = reference
    return [unique[key] for key in sorted(unique)]


def source_manifests(observations: Iterable[Any]) -> list[dict[str, Any]]:
    """Summarize source rows without returning the full row-reference list."""

    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    for reference in normalize_row_references(observations):
        group_key = (
            reference["table"],
            reference["adapter"],
            reference["merge_rule"],
        )
        grouped.setdefault(group_key, []).append(reference)

    manifests: list[dict[str, Any]] = []
    for (table, adapter, merge_rule), references in grouped.items():
        rows: dict[str, dict[str, Any]] = {}
        all_dates: set[str] = set()
        all_sources: set[str] = set()
        for reference in references:
            natural_key = reference["natural_key"]
            date_value = reference["observed_at"][:10]
            all_dates.add(date_value)
            if reference["source"] is not None:
                all_sources.add(reference["source"])
            row = rows.setdefault(
                natural_key,
                {
                    "natural_key": natural_key,
                    "dates": set(),
                    "source_labels": set(),
                    "relationships": set(),
                },
            )
            row["dates"].add(date_value)
            if reference["source"] is not None:
                row["source_labels"].add(reference["source"])
            row["relationships"].add(reference["relationship"])

        digest_rows = [
            {
                "natural_key": row["natural_key"],
                "dates": sorted(row["dates"]),
                "source_labels": sorted(row["source_labels"]),
                "relationships": sorted(row["relationships"]),
            }
            for row in sorted(rows.values(), key=lambda item: item["natural_key"])
        ]
        manifests.append(
            {
                "table": table,
                "adapter": adapter,
                "merge_rule": merge_rule,
                "source_labels": sorted(all_sources),
                "natural_key_scheme": "table-prefixed-natural-key-v1",
                "row_count": len(digest_rows),
                "date_from": min(all_dates) if all_dates else None,
                "date_to": max(all_dates) if all_dates else None,
                "digest": sha256_id(digest_rows),
            }
        )
    return sorted(manifests, key=canonical_json)


def source_completeness(
    observations: Iterable[Any],
) -> dict[str, Any]:
    """Summarize the actual source dates and completeness dependencies used.

    The bounds belong to the source observations, not to an outcome-alignment
    date.  This distinction matters for lagged/windowed candidates, whose
    exposure evidence can precede the outcome row.  Both outcome and exposure
    observations should be supplied by the caller.
    """

    dates: set[str] = set()
    completeness: set[int] = set()
    sync: set[int] = set()
    observation_list = list(observations)
    for observation in observation_list:
        dates.add(_iso_observed_at(
            _observation_value(observation, "observed_at")
        )[:10])
        provenance = _observation_value(observation, "provenance") or {}
        if not isinstance(provenance, Mapping):
            raise ProvenanceError(
                "observation provenance must be an object or null"
            )
        completeness.update(_ids_from_field(
            provenance,
            "completeness_revision_id",
            "completeness_revision_ids",
        ))
        sync.update(_ids_from_field(
            provenance,
            "source_sync_run_id",
            "source_sync_run_ids",
        ))
        for natural_key, _relationship in _natural_key_items(provenance):
            table, separator, identifier = natural_key.partition(":")
            if not (
                separator and identifier.isdigit() and int(identifier) > 0
            ):
                continue
            if table == "capture_completeness_revisions":
                completeness.add(int(identifier))
            elif table == "source_sync_runs":
                sync.add(int(identifier))
    return {
        "from": min(dates) if dates else None,
        "to": max(dates) if dates else None,
        "completeness_revision_ids": sorted(completeness),
        "source_sync_interval_ids": sorted(sync),
    }


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str] | None:
    if not _IDENTIFIER_RE.fullmatch(table):
        raise ProvenanceError("unsafe optional-table identifier")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if exists is None:
        return None
    cursor = conn.execute(f'SELECT * FROM "{table}" WHERE 0')
    return {str(item[0]) for item in (cursor.description or ())}


def _select_dicts(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, tuple(params))
    names = [str(item[0]) for item in (cursor.description or ())]
    return [
        dict(row) if isinstance(row, Mapping) else dict(zip(names, row))
        for row in cursor.fetchall()
    ]


def _date_bound(value: date | str | None, name: str) -> str | None:
    if value is None:
        return None
    result = value.isoformat() if isinstance(value, date) else value
    if not isinstance(result, str):
        raise ProvenanceError(f"{name} must be a canonical ISO date or null")
    try:
        if date.fromisoformat(result).isoformat() != result:
            raise ValueError
    except ValueError:
        raise ProvenanceError(f"{name} must be a canonical ISO date or null") from None
    return result


def _positive_ids(values: Iterable[Any], name: str) -> list[int]:
    result: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProvenanceError(f"{name} must contain positive integer IDs")
        result.add(value)
    return sorted(result)


def _ids_from_field(provenance: Mapping[str, Any], singular: str, plural: str) -> list[int]:
    values: list[Any] = []
    if provenance.get(singular) is not None:
        values.append(provenance[singular])
    plural_value = provenance.get(plural, ())
    if plural_value is not None:
        if isinstance(plural_value, (str, bytes)) or not isinstance(
            plural_value, Iterable
        ):
            raise ProvenanceError(f"{plural} must be an array")
        values.extend(plural_value)
    return _positive_ids(values, plural)


def _provenance_dependency_ids(observations: Iterable[Any]) -> dict[str, list[int]]:
    result = {
        "completeness_revision_ids": set(),
        "source_sync_interval_ids": set(),
        "alias_revision_ids": set(),
        "training_plan_revision_ids": set(),
        "goal_revision_ids": set(),
    }
    table_key_map = {
        "capture_completeness_revisions": "completeness_revision_ids",
        "source_sync_runs": "source_sync_interval_ids",
        "entity_aliases": "alias_revision_ids",
        "training_plan_revisions": "training_plan_revision_ids",
        "insight_goal_revisions": "goal_revision_ids",
    }
    field_map = (
        ("completeness_revision_id", "completeness_revision_ids", "completeness_revision_ids"),
        ("source_sync_run_id", "source_sync_run_ids", "source_sync_interval_ids"),
        ("alias_revision_id", "alias_revision_ids", "alias_revision_ids"),
        ("plan_revision_id", "plan_revision_ids", "training_plan_revision_ids"),
        (
            "training_plan_revision_id",
            "training_plan_revision_ids",
            "training_plan_revision_ids",
        ),
        ("goal_revision_id", "goal_revision_ids", "goal_revision_ids"),
    )
    for observation in observations:
        provenance = _observation_value(observation, "provenance") or {}
        if not isinstance(provenance, Mapping):
            raise ProvenanceError("observation provenance must be an object or null")
        for singular, plural, output_key in field_map:
            result[output_key].update(
                _ids_from_field(provenance, singular, plural)
            )
        for natural_key, _relationship in _natural_key_items(provenance):
            table, separator, identifier = natural_key.partition(":")
            if (
                separator
                and table in table_key_map
                and identifier.isdigit()
                and int(identifier) > 0
            ):
                result[table_key_map[table]].add(int(identifier))
    return {key: sorted(values) for key, values in result.items()}


def dependency_snapshot(
    conn: sqlite3.Connection,
    *,
    observations: Iterable[Any] = (),
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    completeness_ids: Iterable[int] = (),
    source_sync_ids: Iterable[int] = (),
    alias_revision_ids: Iterable[int] = (),
    training_plan_revision_ids: Iterable[int] = (),
    goal_revision_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Collect effective Phase 2/3 dependency IDs using SELECT statements only."""

    if conn is None or not hasattr(conn, "execute"):
        raise ProvenanceError("an injected SQLite connection is required")
    start = _date_bound(date_from, "date_from")
    end = _date_bound(date_to, "date_to")
    if start is not None and end is not None and start > end:
        raise ProvenanceError("date_from must not follow date_to")

    observation_list = list(observations)
    explicit = _provenance_dependency_ids(observation_list)
    availability: dict[str, str] = {}

    completeness_revision_set: set[int] = set(
        explicit["completeness_revision_ids"]
    )
    completeness_revision_set.update(
        _positive_ids(completeness_ids, "completeness_ids")
    )
    required = {"id", "date", "supersedes_id"}
    columns = _table_columns(conn, "capture_completeness_revisions")
    if columns is None:
        availability["capture_completeness_revisions"] = "missing"
    elif not required <= columns:
        availability["capture_completeness_revisions"] = "incompatible"
    else:
        availability["capture_completeness_revisions"] = "available"

    source_sync_set: set[int] = set(explicit["source_sync_interval_ids"])
    source_sync_set.update(_positive_ids(source_sync_ids, "source_sync_ids"))
    required = {"id", "status", "coverage_from", "coverage_to"}
    columns = _table_columns(conn, "source_sync_runs")
    if columns is None:
        availability["source_sync_runs"] = "missing"
    elif not required <= columns:
        availability["source_sync_runs"] = "incompatible"
    else:
        availability["source_sync_runs"] = "available"

    alias_ids: set[int] = set(explicit["alias_revision_ids"])
    alias_ids.update(
        _positive_ids(alias_revision_ids, "alias_revision_ids")
    )
    alias_rows: list[dict[str, Any]] = []
    required = {"id", "entity_type", "alias_key", "canonical_key", "active"}
    columns = _table_columns(conn, "entity_aliases")
    if columns is None:
        availability["entity_aliases"] = "missing"
    elif not required <= columns:
        availability["entity_aliases"] = "incompatible"
    else:
        availability["entity_aliases"] = "available"
        rows = _select_dicts(
            conn,
            """SELECT id,entity_type,alias_key,canonical_key,active
               FROM entity_aliases ORDER BY entity_type,alias_key,id""",
        )
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            latest[(str(row["entity_type"]), str(row["alias_key"]))] = row
        alias_rows = [
            {
                "id": int(row["id"]),
                "entity_type": row["entity_type"],
                "alias_key": row["alias_key"],
                "canonical_key": row["canonical_key"],
                "active": int(row["active"]),
            }
            for _key, row in sorted(latest.items())
        ]
        alias_ids.update(row["id"] for row in alias_rows)

    plan_ids: set[int] = set(explicit["training_plan_revision_ids"])
    plan_ids.update(
        _positive_ids(
            training_plan_revision_ids, "training_plan_revision_ids"
        )
    )
    required = {"id", "effective_from"}
    columns = _table_columns(conn, "training_plan_revisions")
    if columns is None:
        availability["training_plan_revisions"] = "missing"
    elif not required <= columns:
        availability["training_plan_revisions"] = "incompatible"
    else:
        availability["training_plan_revisions"] = "available"

    goal_ids: set[int] = set(explicit["goal_revision_ids"])
    goal_ids.update(_positive_ids(goal_revision_ids, "goal_revision_ids"))
    required = {"id", "goal_key"}
    columns = _table_columns(conn, "insight_goal_revisions")
    if columns is None:
        availability["insight_goal_revisions"] = "missing"
    elif not required <= columns:
        availability["insight_goal_revisions"] = "incompatible"
    else:
        availability["insight_goal_revisions"] = "available"

    normalized_alias_ids = sorted(alias_ids)
    return {
        "completeness_revision_ids": sorted(completeness_revision_set),
        "source_sync_interval_ids": sorted(source_sync_set),
        "alias_revision_ids": normalized_alias_ids,
        "alias_revision_digest": sha256_id(
            {"effective_rows": alias_rows, "revision_ids": normalized_alias_ids}
        ),
        "training_plan_revision_ids": sorted(plan_ids),
        "goal_revision_ids": sorted(goal_ids),
        "availability": {
            table: availability.get(table, "missing") for table in _OPTIONAL_TABLES
        },
    }


_DEPENDENCY_ID_FIELDS = (
    ("completeness_revision_ids", "capture_completeness_revisions"),
    ("source_sync_interval_ids", "source_sync_runs"),
    ("alias_revision_ids", "entity_aliases"),
    ("training_plan_revision_ids", "training_plan_revisions"),
    ("goal_revision_ids", "insight_goal_revisions"),
)


def _dependency_row_dates(
    table: str,
    row: Mapping[str, Any],
    *,
    date_from: str | None,
    date_to: str | None,
) -> list[str]:
    candidates: list[Any]
    if table == "capture_completeness_revisions":
        candidates = [row.get("date")]
    elif table == "source_sync_runs":
        candidates = [row.get("coverage_from"), row.get("coverage_to")]
        if not any(
            isinstance(value, str) and len(value) >= 10
            for value in candidates
        ):
            candidates = [row.get("completed_at")]
    elif table == "training_plan_revisions":
        candidates = [row.get("effective_from")]
    else:
        candidates = [row.get("created_at")]
    dates: set[str] = set()
    for value in candidates:
        if not isinstance(value, str) or len(value) < 10:
            continue
        try:
            parsed = date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            continue
        dates.add(parsed)
    if not dates:
        fallback = date_from or date_to
        if fallback is None:
            raise ProvenanceError(
                f"{table} dependency row has no auditable date"
            )
        dates.add(fallback)
    return sorted(dates)


def dependency_source_observations(
    conn: sqlite3.Connection,
    dependencies: Mapping[str, Any],
    *,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
) -> list[dict[str, Any]]:
    """Resolve engine-owned dependency IDs into auditable source references.

    IDs come only from the recomputed dependency snapshot.  The command/API
    boundary never accepts them, so a browser cannot choose arbitrary rows.
    Every referenced ID must still exist; an absent row fails closed.
    """

    if conn is None or not hasattr(conn, "execute"):
        raise ProvenanceError("an injected SQLite connection is required")
    if not isinstance(dependencies, Mapping):
        raise ProvenanceError("dependencies must be an object")
    start = _date_bound(date_from, "date_from")
    end = _date_bound(date_to, "date_to")
    if start is not None and end is not None and start > end:
        raise ProvenanceError("date_from must not follow date_to")

    observations: list[dict[str, Any]] = []
    for field, table in _DEPENDENCY_ID_FIELDS:
        ids = _positive_ids(dependencies.get(field, ()), field)
        if not ids:
            continue
        columns = _table_columns(conn, table)
        if columns is None or "id" not in columns:
            raise ProvenanceError(
                f"{table} dependency rows are unavailable"
            )
        selected = sorted(
            column for column in (
                "id", "date", "coverage_from", "coverage_to",
                "completed_at", "effective_from", "created_at", "source",
            )
            if column in columns
        )
        placeholders = ",".join("?" for _item in ids)
        rows = _select_dicts(
            conn,
            f'SELECT {",".join(selected)} FROM "{table}" '
            f"WHERE id IN ({placeholders}) ORDER BY id",
            ids,
        )
        by_id = {
            int(row["id"]): row
            for row in rows
            if isinstance(row.get("id"), int)
            and not isinstance(row.get("id"), bool)
        }
        missing = sorted(set(ids) - set(by_id))
        if missing:
            raise ProvenanceError(
                f"{table} dependency rows are missing: {missing}"
            )
        for identifier in ids:
            row = by_id[identifier]
            source = row.get("source")
            if not isinstance(source, str) or not source:
                source = table
            for observed_at in _dependency_row_dates(
                table, row, date_from=start, date_to=end
            ):
                observations.append({
                    "feature_key": f"source.dependency.{table}",
                    "observed_at": observed_at,
                    "value": identifier,
                    "state": "observed",
                    "source": source,
                    "provenance": {
                        "adapter": "provenance",
                        "table": table,
                        "merge": "dependency_id",
                        "natural_key": f"{table}:{identifier}",
                    },
                })
    return sorted(
        observations,
        key=lambda item: (
            item["feature_key"],
            item["observed_at"],
            item["provenance"]["natural_key"],
        ),
    )


def _observation_fingerprint_rows(observations: Iterable[Any]) -> list[list[Any]]:
    unique: dict[str, list[Any]] = {}
    for observation in observations:
        feature_key = _nonempty_text(
            _observation_value(observation, "feature_key"), "feature_key"
        )
        observed_at = _iso_observed_at(
            _observation_value(observation, "observed_at")
        )
        source = _observation_value(observation, "source")
        if source is not None and (not isinstance(source, str) or not source):
            raise ProvenanceError("observation source must be non-empty text or null")
        state = _nonempty_text(_observation_value(observation, "state"), "state")
        value = _canonical_value(_observation_value(observation, "value"))
        provenance = _observation_value(observation, "provenance") or {}
        if not isinstance(provenance, Mapping):
            raise ProvenanceError("observation provenance must be an object or null")
        keys = [key for key, _relationship in _natural_key_items(provenance)]
        for natural_key in keys or [None]:
            row = [
                feature_key,
                observed_at,
                source,
                natural_key,
                state,
                value,
            ]
            unique[canonical_json(row)] = row
    return [unique[key] for key in sorted(unique)]


def _observation_bounds(observations: Iterable[Any]) -> tuple[str | None, str | None]:
    dates = [
        _iso_observed_at(_observation_value(item, "observed_at"))[:10]
        for item in observations
    ]
    return (min(dates), max(dates)) if dates else (None, None)


def input_fingerprint(
    observations: Iterable[Any],
    *,
    conn: sqlite3.Connection,
    registry_version: str,
    registry_sha256: str,
    analysis_version: str = ANALYSIS_VERSION,
    analysis_sha256: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    completeness_ids: Iterable[int] = (),
    source_sync_ids: Iterable[int] = (),
    alias_revision_ids: Iterable[int] = (),
    training_plan_revision_ids: Iterable[int] = (),
    goal_revision_ids: Iterable[int] = (),
) -> str:
    """Fingerprint all eligible inputs and effective revision dependencies."""

    observation_list = list(observations)
    registry_version = _nonempty_text(registry_version, "registry_version")
    analysis_version = _nonempty_text(analysis_version, "analysis_version")
    registry_hash = _normalize_sha256(registry_sha256, "registry_sha256")
    analysis_hash = _normalize_sha256(
        analysis_sha256 if analysis_sha256 is not None else engine_sha256(),
        "analysis_sha256",
    )
    observed_from, observed_to = _observation_bounds(observation_list)
    effective_from = _date_bound(date_from, "date_from") if date_from is not None else observed_from
    effective_to = _date_bound(date_to, "date_to") if date_to is not None else observed_to
    dependencies = dependency_snapshot(
        conn,
        observations=observation_list,
        date_from=effective_from,
        date_to=effective_to,
        completeness_ids=completeness_ids,
        source_sync_ids=source_sync_ids,
        alias_revision_ids=alias_revision_ids,
        training_plan_revision_ids=training_plan_revision_ids,
        goal_revision_ids=goal_revision_ids,
    )

    payload = {
        "contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_version": analysis_version,
        "analysis_sha256": analysis_hash,
        "registry_version": registry_version,
        "registry_sha256": registry_hash,
        "observations": _observation_fingerprint_rows(observation_list),
        "dependencies": {
            key: value
            for key, value in dependencies.items()
            if key != "availability"
        },
    }
    return sha256_id(payload)


def _range_payload(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProvenanceError(f"{name} must be an object")
    expected = {"from", "to"}
    allowed = expected | {"kind"}
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - allowed)
    if missing or unknown:
        raise ProvenanceError(
            f"{name} fields mismatch missing={missing} unknown={unknown}"
        )
    if "kind" in value and value["kind"] not in {"all", "bounded"}:
        raise ProvenanceError(f"{name}.kind must be all or bounded")
    start = _date_bound(value["from"], f"{name}.from")
    end = _date_bound(value["to"], f"{name}.to")
    if (start is None) != (end is None):
        raise ProvenanceError(f"{name} bounds must both be dates or both be null")
    if start is not None and start > end:
        raise ProvenanceError(f"{name}.from must not follow {name}.to")
    kind = value.get("kind", "all" if start is None else "bounded")
    if kind == "all" and start is not None:
        # An all-history request carries its discovered actual bounds while
        # retaining request identity; bounded and all must never collide.
        pass
    if kind == "bounded" and start is None:
        raise ProvenanceError(f"{name}.kind bounded requires date bounds")
    return {"kind": kind, "from": start, "to": end}


_EVIDENCE_FIELDS = (
    "finding_id",
    "candidate_key",
    "outcome",
    "exposure",
    "sample",
    "rates",
    "effect",
    "testing",
    "stability",
    "confounders",
    "evidence_for",
    "evidence_against",
    "warnings",
    "quality",
)


def evidence_fingerprint(finding: Mapping[str, Any]) -> str:
    """Hash the complete engine-owned evidence for one finding.

    Provenance is deliberately outside the payload to avoid recursion.  Its
    semantic versions and input fingerprint already participate in
    ``finding_id``; all evidence-bearing fields are included here.
    """

    if not isinstance(finding, Mapping):
        raise ProvenanceError("finding evidence must be an object")
    missing = [field for field in _EVIDENCE_FIELDS if field not in finding]
    if missing:
        raise ProvenanceError(f"finding evidence missing fields: {missing}")
    return sha256_id({
        "contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "finding": {field: finding[field] for field in _EVIDENCE_FIELDS},
    })


def finding_id(
    *,
    candidate_key: str | None = None,
    candidate_key_value: str | None = None,
    analysis_range: Mapping[str, Any],
    baseline_range: Mapping[str, Any],
    input_fingerprint: str | None = None,
    input_fingerprint_value: str | None = None,
    analysis_version: str = ANALYSIS_VERSION,
    contract_version: str = ANALYSIS_CONTRACT_VERSION,
) -> str:
    """Hash the exact Section 12 finding identity fields."""

    if candidate_key is None:
        candidate_key = candidate_key_value
    elif candidate_key_value is not None and candidate_key_value != candidate_key:
        raise ProvenanceError("conflicting candidate key values")
    if input_fingerprint is None:
        input_fingerprint = input_fingerprint_value
    elif (
        input_fingerprint_value is not None
        and input_fingerprint_value != input_fingerprint
    ):
        raise ProvenanceError("conflicting input fingerprint values")
    if not isinstance(candidate_key, str):
        raise ProvenanceError("candidate_key must be canonical JSON text")
    try:
        parsed_candidate = json.loads(candidate_key)
    except json.JSONDecodeError:
        raise ProvenanceError("candidate_key must be canonical JSON text") from None
    if canonical_json(parsed_candidate) != candidate_key:
        raise ProvenanceError("candidate_key must use canonical JSON serialization")
    return sha256_id(
        {
            "contract_version": _nonempty_text(
                contract_version, "contract_version"
            ),
            "analysis_version": _nonempty_text(
                analysis_version, "analysis_version"
            ),
            "candidate_key": candidate_key,
            "analysis_range": _range_payload(analysis_range, "analysis_range"),
            "baseline_range": _range_payload(baseline_range, "baseline_range"),
            "input_fingerprint": _normalize_sha256(
                input_fingerprint, "input_fingerprint"
            ),
        }
    )


__all__ = [
    "ANALYSIS_CONTRACT_VERSION",
    "ANALYSIS_VERSION",
    "ProvenanceError",
    "candidate_key",
    "dependency_source_observations",
    "dependency_snapshot",
    "engine_manifest",
    "engine_sha256",
    "evidence_fingerprint",
    "finding_id",
    "input_fingerprint",
    "normalize_row_references",
    "sha256_id",
    "source_completeness",
    "source_manifests",
]
