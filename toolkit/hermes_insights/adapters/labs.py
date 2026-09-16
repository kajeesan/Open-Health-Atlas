"""Protocol-compatible laboratory measurement episodes.

The adapter is deliberately descriptive.  It preserves each accepted stored
episode, reports the source/catalog reference relationship, and never turns a
laboratory value into diagnosis or advice.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
from typing import Any

from . import (
    DefinitionIndex, REGISTRY_VERSION, context_value, finite, has_columns,
    identity_parts, in_range, make_observation, natural_key,
)
from ..normalize import normalized_label


ADAPTER_ID = "labs"


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, day, value, *, unit=None, source=None,
    provenance=None, include_provenance=False,
):
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, day, value, unit=unit, source=source,
        provenance=provenance, include_provenance=include_provenance,
    ))


def _catalog(conn):
    if not has_columns(
        conn, "lab_catalog", "canonical", "unit", "ref_low", "ref_high",
        "aliases",
    ):
        return {}, {}
    canonical, aliases = {}, {}
    for row in _rows(conn, "SELECT * FROM lab_catalog ORDER BY canonical"):
        name = row.get("canonical")
        if not isinstance(name, str) or not name.strip():
            continue
        canonical[normalized_label(name)] = row
        aliases.setdefault(normalized_label(name), row)
        try:
            values = json.loads(row.get("aliases") or "[]")
        except (TypeError, json.JSONDecodeError):
            values = []
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.strip():
                    aliases.setdefault(normalized_label(value), row)
    return canonical, aliases


def _reference_status(value, low, high, in_range_fn):
    if low is None and high is None:
        return "unknown"
    if low is not None and value < low:
        return "below"
    if high is not None and value > high:
        return "above"
    if in_range_fn is not None:
        try:
            result = in_range_fn(value, low, high)
        except (TypeError, ValueError):
            result = None
        if result is False:
            return "outside"
    return "within"


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    if not has_columns(
        conn, "labs", "id", "date", "test_name", "value", "unit",
        "reference_low", "reference_high",
    ):
        return out

    _canonical, aliases = _catalog(conn)
    norm_unit = context_value(context, "norm_lab_unit") or context_value(
        context, "_norm_lab_unit")
    in_range_fn = context_value(context, "lab_in_range") or context_value(
        context, "_lab_in_range")
    norm_unit = norm_unit or (lambda value: str(value or "").strip().casefold())

    # Exact re-ingest duplicates collapse to the newest id, matching the
    # existing labs read view while preserving distinct same-date values.
    deduped = {}
    for row in _rows(conn, "SELECT * FROM labs WHERE date IS NOT NULL ORDER BY date,id"):
        if finite(row.get("value")) is None or not isinstance(row.get("test_name"), str):
            continue
        deduped[(row["date"], row["test_name"], row["value"])] = row
    rows = sorted(deduped.values(), key=lambda row: (row["date"], row["id"]))

    prior_by_protocol = defaultdict(list)
    today = getattr(context, "today", date.today())
    if isinstance(today, str):
        today = date.fromisoformat(today[:10])
    for row in rows:
        raw_name = row["test_name"]
        catalog = aliases.get(normalized_label(raw_name))
        canonical = catalog["canonical"] if catalog else raw_name
        identity_key, token, _ = identity_parts("lab", canonical)
        prefix = f"lab.{token}"
        stored_unit = row.get("unit")
        catalog_unit = catalog.get("unit") if catalog else None
        compatible = bool(catalog and norm_unit(stored_unit) == norm_unit(catalog_unit))

        # The report's own interval wins whenever either side was stored.  A
        # fully absent report interval falls back to the catalog.
        own_low, own_high = row.get("reference_low"), row.get("reference_high")
        if own_low is None and own_high is None and catalog:
            low, high, interval_source = catalog.get("ref_low"), catalog.get("ref_high"), "catalog"
        else:
            low, high, interval_source = own_low, own_high, "report"
        if not catalog:
            status = "catalog_missing"
        elif not compatible:
            status = "incompatible_unit"
        else:
            status = _reference_status(row["value"], low, high, in_range_fn)

        protocol = (normalized_label(canonical), norm_unit(stored_unit))
        prior = prior_by_protocol[protocol][-1] if compatible and prior_by_protocol[protocol] else None
        provenance = {
            "adapter": ADAPTER_ID, "table": "labs",
            "natural_key": natural_key("labs", row),
            "identity_key": identity_key, "original_label": raw_name,
            "canonical": canonical, "stored_unit": stored_unit,
            "catalog_unit": catalog_unit, "unit_compatible": compatible,
            "reference_low": low, "reference_high": high,
            "reference_source": interval_source,
            "catalog_connected": catalog is not None,
            "prior_date": prior.get("date") if prior else None,
            "prior_value": prior.get("value") if prior else None,
            "change": (row["value"] - prior["value"]) if prior else None,
            "episode_not_daily_fill": True,
        }
        if in_range(row["date"], date_range):
            fields = [
                ("reference_status", status, "status"),
                ("age_days", (today - date.fromisoformat(row["date"])).days, "days"),
            ]
            # A catalog feature carries the catalog unit.  Emitting a raw value
            # from an incompatible unit under that key would silently relabel
            # the measurement, so retain it only in source storage/provenance.
            if compatible:
                fields.insert(0, ("value", row["value"], catalog_unit))
            for suffix, value, unit in fields:
                key = definitions.identity_key(
                    f"{prefix}.{suffix}", prefix="lab.", suffix=f".{suffix}",
                    identity_key=identity_key,
                )
                _emit(out, definitions, key, row["date"], value, unit=unit,
                      source=row.get("source") or "manual", provenance=provenance,
                      include_provenance=include_provenance)
        if compatible:
            prior_by_protocol[protocol].append(row)

    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "REGISTRY_VERSION", "load"]
