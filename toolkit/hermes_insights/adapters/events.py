"""Lossless structured-event observations with completeness-gated absences."""

from __future__ import annotations

from collections import defaultdict
import statistics
from typing import Any, Mapping

from . import (
    DefinitionIndex, REGISTRY_VERSION, complete_dates, date_where,
    definition_identity_key, effective_completeness, finite, has_columns,
    make_observation, natural_key, token_from_identity_key,
)
from ..events import EVENT_TO_SCOPE


ADAPTER_ID = "events"
NUMERIC_SUFFIXES = {
    "duration_min": ("duration_min", "sum", "min"),
    "intensity_mean": ("intensity", "mean", None),
    "valence_mean": ("valence", "mean", None),
}
ZEROABLE_SUFFIXES = (".occurred", ".count", ".duration_min")


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, day, value, *, unit=None, source=None,
    provenance=None, include_provenance=False, state="observed",
):
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, day, value, unit=unit, source=source, state=state,
        provenance=provenance, include_provenance=include_provenance,
    ))


def _numeric(rows, column, aggregation):
    values = [row.get(column) for row in rows]
    if not values or not all(finite(value) is not None for value in values):
        return None
    return sum(values) if aggregation == "sum" else statistics.mean(values)


def _source(rows):
    values = sorted({row.get("source") or "manual" for row in rows})
    return values[0] if len(values) == 1 else "mixed"


def _aggregate(
    out, definitions, prefix, rows, identity_key, include_provenance,
):
    day = rows[0]["date"]
    source = _source(rows)
    prov = {
        "adapter": ADAPTER_ID, "table": "event_exposures",
        "natural_keys": [natural_key("event_exposures", row) for row in rows],
        "identity_key": identity_key,
        "original_labels": sorted({row["entity_label"] for row in rows
                                   if row.get("entity_label")}),
        "all_matching_values_required": True,
    }
    for suffix, value, unit in (
        ("occurred", 1, "binary"), ("count", len(rows), "count"),
    ):
        _emit(out, definitions, f"{prefix}.{suffix}", day, value, unit=unit,
              source=source, provenance=prov,
              include_provenance=include_provenance)
    for suffix, (column, aggregation, unit) in NUMERIC_SUFFIXES.items():
        value = _numeric(rows, column, aggregation)
        _emit(out, definitions, f"{prefix}.{suffix}", day, value, unit=unit,
              source=source, provenance={**prov, "aggregation": aggregation,
                                          "column": column},
              include_provenance=include_provenance)


def _definition_identity(definition):
    return definition_identity_key(definition)


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    if not has_columns(
        conn, "event_exposures", "id", "date", "category", "entity_key",
        "voided", "duration_min", "intensity", "valence",
    ):
        return out
    where, params = date_where("date", date_range)
    rows = _rows(conn, "SELECT * FROM event_exposures WHERE voided=0 AND " + where
                 + " ORDER BY date,time,id", params)
    by_category = defaultdict(list)
    by_entity = defaultdict(list)
    for row in rows:
        category = row.get("category")
        if category not in EVENT_TO_SCOPE:
            continue
        by_category[(row["date"], category)].append(row)
        if row.get("entity_key"):
            by_entity[(row["date"], category, row["entity_key"])].append(row)

    observed: set[tuple[str, str]] = set()
    for (day, category), item_rows in sorted(by_category.items()):
        prefix = f"event.{category}"
        _aggregate(out, definitions, prefix, item_rows, None, include_provenance)
        for suffix in ("occurred", "count", *NUMERIC_SUFFIXES):
            if f"{prefix}.{suffix}" in definitions:
                observed.add((day, f"{prefix}.{suffix}"))
    for (day, category, identity_key), item_rows in sorted(by_entity.items()):
        token = token_from_identity_key(identity_key)
        prefix = f"event.{category}.entity.{token}"
        _aggregate(out, definitions, prefix, item_rows, identity_key,
                   include_provenance)
        for suffix in ("occurred", "count", *NUMERIC_SUFFIXES):
            if f"{prefix}.{suffix}" in definitions:
                observed.add((day, f"{prefix}.{suffix}"))

    effective = effective_completeness(conn, date_range)
    for key, definition in definitions.by_key.items():
        if not key.startswith("event.") or not key.endswith(ZEROABLE_SUFFIXES):
            continue
        rest = key[len("event."):]
        category = rest.split(".", 1)[0]
        scope = EVENT_TO_SCOPE.get(category)
        if scope is None:
            continue
        identity_key = _definition_identity(definition) if ".entity." in key else None
        for day, comp in complete_dates(effective, scope, identity_key).items():
            if (day, key) in observed:
                continue
            _emit(out, definitions, key, day, 0, state="structural_zero",
                  unit="binary" if key.endswith(".occurred") else
                       "count" if key.endswith(".count") else "min",
                  source=comp["source"], provenance={
                      "adapter": ADAPTER_ID,
                      "table": "capture_completeness_revisions",
                      "natural_key": f"capture_completeness_revisions:{comp['id']}",
                      "scope": scope, "identity_key": identity_key,
                  }, include_provenance=include_provenance)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "REGISTRY_VERSION", "load"]
