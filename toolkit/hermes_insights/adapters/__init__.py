"""Shared, read-only primitives for the Phase 3 feature adapters.

Adapters deliberately contain no schema creation or repair code.  A missing
table/column produces no observations; readiness distinguishes that from an
unconnected adapter by inspecting the registry handshake separately.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import re
import sqlite3
from typing import TYPE_CHECKING, Any

from ..normalize import normalized_label

if TYPE_CHECKING:  # pragma: no cover - imported lazily at runtime
    from ..contracts import AdapterContext, DateRange, FeatureDefinition, Observation


REGISTRY_VERSION = "feature-registry-v1"
OBSERVATION_STATES = {
    "observed", "structural_zero", "missing", "stale", "not_applicable",
    "not_connected",
}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUALIFIED_IDENTIFIER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$"
)
_HASH_KEY = re.compile(r"^[a-z0-9_-]+:([0-9a-f]{64})$")


def definition_value(definition: Any, name: str, default: Any = None) -> Any:
    if isinstance(definition, Mapping):
        return definition.get(name, default)
    return getattr(definition, name, default)


def definition_identity_key(definition: Any) -> str | None:
    """Read the normative identity key from ``source.identity`` metadata."""
    source = definition_value(definition, "source", {})
    if isinstance(source, Mapping):
        identity = source.get("identity")
        if isinstance(identity, Mapping):
            value = identity.get("identity_key")
            return str(value) if value else None
    # Mapping fallback is retained for lightweight adapter unit-test fixtures.
    value = definition_value(definition, "identity_key")
    return str(value) if value else None


class DefinitionIndex:
    """Small compatibility wrapper for dataclass or mapping definitions."""

    def __init__(self, definitions: Iterable[Any] | Mapping[str, Any]):
        values = definitions.values() if isinstance(definitions, Mapping) else definitions
        self.by_key = {
            str(definition_value(item, "key")): item
            for item in values
            if definition_value(item, "key")
        }

    def __contains__(self, key: str) -> bool:
        return key in self.by_key

    def get(self, key: str) -> Any:
        return self.by_key.get(key)

    def first(self, *keys: str) -> str | None:
        return next((key for key in keys if key in self.by_key), None)

    def identity_key(
        self, expected: str, *, prefix: str, suffix: str,
        identity_key: str | None = None,
    ) -> str | None:
        """Return the exact dynamic definition, with metadata as a fallback.

        The expected Appendix-B key wins.  The metadata fallback makes adapters
        resilient while registry construction and adapters are developed in
        parallel, without accepting fuzzy labels.
        """
        if expected in self.by_key:
            return expected
        if identity_key is None:
            return None
        for key, definition in self.by_key.items():
            if not key.startswith(prefix) or not key.endswith(suffix):
                continue
            if definition_identity_key(definition) == identity_key:
                return key
        return None

    def unit(self, key: str, fallback: str | None = None) -> str | None:
        return definition_value(self.by_key.get(key), "unit", fallback)


def _iso(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def range_bounds(date_range: Any) -> tuple[str | None, str | None]:
    return _iso(getattr(date_range, "start", None)), _iso(getattr(date_range, "end", None))


def in_range(value: str | date, date_range: Any) -> bool:
    current = _iso(value)
    lo, hi = range_bounds(date_range)
    return bool(current and (lo is None or current[:10] >= lo[:10])
                and (hi is None or current[:10] <= hi[:10]))


def iter_dates(date_range: Any) -> list[str]:
    lo, hi = range_bounds(date_range)
    if lo is None or hi is None:
        return []
    start, end = date.fromisoformat(lo[:10]), date.fromisoformat(hi[:10])
    return [(start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    if not _IDENTIFIER.fullmatch(table):
        raise ValueError("unsafe table identifier")
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    # Cursor metadata from a zero-row SELECT avoids PRAGMA entirely.  The
    # identifier was validated by table_exists(), and no user/table value can
    # escape the quoted identifier.  Trace and authorizer audits therefore see
    # only SELECT statements on every adapter read path.
    cursor = conn.execute(f'SELECT * FROM "{table}" WHERE 0')
    return {str(item[0]) for item in (cursor.description or ())}


def has_columns(conn: sqlite3.Connection, table: str, *columns: str) -> bool:
    present = table_columns(conn, table)
    return bool(present) and set(columns) <= present


def date_where(column: str, date_range: Any) -> tuple[str, list[str]]:
    if not _QUALIFIED_IDENTIFIER.fullmatch(column):
        raise ValueError("unsafe column identifier")
    lo, hi = range_bounds(date_range)
    clauses, params = [], []
    if lo is not None:
        clauses.append(f"{column}>=?")
        params.append(lo[:10])
    if hi is not None:
        clauses.append(f"{column}<=?")
        params.append(hi[:10])
    return (" AND ".join(clauses) if clauses else "1=1"), params


def finite(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def context_value(context: Any, name: str, default: Any = None) -> Any:
    for collection_name in ("constants", "functions"):
        collection = getattr(context, collection_name, None)
        if isinstance(collection, Mapping) and name in collection:
            return collection[name]
        if collection is not None and hasattr(collection, name):
            return getattr(collection, name)
    return getattr(context, name, default)


def identity_parts(namespace: str, label: str) -> tuple[str, str, str]:
    normalized = normalized_label(label)
    if not normalized:
        raise ValueError("identity label must not be blank")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}", f"i_{digest}", normalized


def resolved_identity_parts(
    conn: sqlite3.Connection, namespace: str, label: str,
) -> tuple[str, str, str, int | None]:
    """Apply only an exact active Phase-2 alias revision, if one exists.

    This intentionally performs no fuzzy/punctuation/person inference.  The
    original normalized label is returned alongside the canonical key so an
    adapter can retain both raw provenance and the exact alias revision used.
    """
    raw_key, _raw_token, normalized = identity_parts(namespace, label)
    if namespace not in {
        "person", "food", "location", "activity", "supplement",
        "medication", "other",
    } or not has_columns(
        conn, "entity_aliases", "id", "entity_type", "alias_key",
        "canonical_key", "active",
    ):
        return raw_key, token_from_identity_key(raw_key), normalized, None
    row = conn.execute(
        """SELECT id,canonical_key,active FROM entity_aliases
           WHERE entity_type=? AND alias_key=? ORDER BY id DESC LIMIT 1""",
        (namespace, raw_key),
    ).fetchone()
    if row is None:
        return raw_key, token_from_identity_key(raw_key), normalized, None
    canonical = row["canonical_key"] if row["active"] == 1 else raw_key
    return canonical, token_from_identity_key(canonical), normalized, int(row["id"])


def token_from_identity_key(identity_key: str) -> str:
    match = _HASH_KEY.fullmatch(identity_key)
    digest = match.group(1) if match else hashlib.sha256(
        identity_key.encode("utf-8")
    ).hexdigest()
    return f"i_{digest}"


def natural_key(table: str, row: Mapping[str, Any], *pk: str) -> str:
    if row.get("id") is not None:
        return f"{table}:{row['id']}"
    parts = pk or (("date",) if row.get("date") is not None else ())
    return table + ":" + ":".join(str(row.get(name, "")) for name in parts)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_observation(
    definitions: DefinitionIndex,
    feature_key: str,
    observed_at: date | datetime | str,
    value: Any,
    *,
    state: str = "observed",
    unit: str | None = None,
    source: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    include_provenance: bool = False,
) -> Any:
    if state not in OBSERVATION_STATES:
        raise ValueError(f"invalid observation state: {state}")
    from ..contracts import Observation

    return Observation(
        feature_key=feature_key,
        observed_at=_iso(observed_at),
        value=value,
        state=state,
        # An explicit adapter unit is evidence and must survive to the frame's
        # handshake validator. Falling back to registry metadata is correct
        # only when the adapter intentionally has no source/formula unit of its
        # own; silently replacing a wrong literal would mask contract drift.
        unit=unit if unit is not None else definitions.unit(feature_key),
        source=source,
        provenance=dict(provenance) if include_provenance and provenance else None,
    )


def effective_completeness(
    conn: sqlite3.Connection, date_range: Any,
) -> dict[tuple[str, str, str | None], sqlite3.Row]:
    if not has_columns(
        conn, "capture_completeness_revisions", "id", "date", "scope",
        "entity_key", "state", "supersedes_id",
    ):
        return {}
    where, params = date_where("r.date", date_range)
    rows = conn.execute(
        """SELECT r.* FROM capture_completeness_revisions r
           WHERE NOT EXISTS (
             SELECT 1 FROM capture_completeness_revisions n
             WHERE n.supersedes_id=r.id
           ) AND """ + where + " ORDER BY r.date,r.scope,r.entity_key,r.id",
        params,
    ).fetchall()
    return {(row["date"], row["scope"], row["entity_key"]): row for row in rows}


def completeness_row(
    effective: Mapping[tuple[str, str, str | None], Any],
    day: str,
    scope: str,
    entity_key: str | None = None,
) -> Any | None:
    exact = effective.get((day, scope, entity_key))
    if exact is not None:
        return exact
    return effective.get((day, scope, None)) if entity_key is not None else None


def complete_dates(
    effective: Mapping[tuple[str, str, str | None], Any],
    scope: str,
    entity_key: str | None = None,
) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for (day, row_scope, row_entity), row in effective.items():
        if row_scope != scope or row["state"] != "complete":
            continue
        if entity_key is None and row_entity is not None:
            continue
        if entity_key is not None and row_entity not in (None, entity_key):
            continue
        prior = found.get(day)
        if prior is None or (row_entity == entity_key and prior["entity_key"] is None):
            found[day] = row
    return found


def source_sync_intervals(
    conn: sqlite3.Connection, sources: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not has_columns(
        conn, "source_sync_runs", "id", "source", "status", "coverage_from",
        "coverage_to", "completed_at",
    ):
        return []
    rows = conn.execute(
        """SELECT * FROM source_sync_runs
           WHERE status='success' AND coverage_from IS NOT NULL
             AND coverage_to IS NOT NULL
           ORDER BY source,completed_at,id"""
    ).fetchall()
    return [dict(row) for row in rows if sources is None or row["source"] in sources]


def interval_dates(intervals: Iterable[Mapping[str, Any]], date_range: Any) -> dict[str, list[int]]:
    lo, hi = range_bounds(date_range)
    covered: dict[str, list[int]] = {}
    for row in intervals:
        start = date.fromisoformat(row["coverage_from"])
        end = date.fromisoformat(row["coverage_to"])
        if lo is not None:
            start = max(start, date.fromisoformat(lo[:10]))
        if hi is not None:
            end = min(end, date.fromisoformat(hi[:10]))
        while start <= end:
            covered.setdefault(start.isoformat(), []).append(int(row["id"]))
            start += timedelta(days=1)
    return covered


__all__ = [
    "DefinitionIndex", "REGISTRY_VERSION", "canonical_json", "complete_dates",
    "completeness_row", "context_value", "date_where", "definition_identity_key",
    "definition_value",
    "effective_completeness", "finite", "has_columns", "identity_parts",
    "in_range", "interval_dates", "iter_dates", "make_observation",
    "natural_key", "range_bounds", "resolved_identity_parts",
    "source_sync_intervals", "table_columns", "table_exists",
    "token_from_identity_key",
]
