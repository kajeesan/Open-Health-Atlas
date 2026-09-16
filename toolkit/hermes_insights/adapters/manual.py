"""Manual vitals, named assessments, and explicit skincare observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from . import (
    DefinitionIndex, REGISTRY_VERSION, complete_dates, context_value, date_where,
    definition_identity_key, effective_completeness, finite, has_columns,
    identity_parts, in_range, make_observation, natural_key,
)


ADAPTER_ID = "manual"
VITAL_FIELDS = {
    "systolic": "mmHg", "diastolic": "mmHg", "resting_hr": "bpm",
}


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, observed_at, value, *, source=None,
    provenance=None, include_provenance=False, state="observed",
):
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, observed_at, value, state=state, source=source,
        provenance=provenance, include_provenance=include_provenance,
    ))


def _episode_at(row, hhmm):
    if hhmm is None:
        return row["date"]
    minute = hhmm(row.get("time"))
    if minute is None:
        return row["date"]
    return f"{row['date']}T{minute // 60:02d}:{minute % 60:02d}:00"


def _load_vitals(conn, definitions, date_range, context, out, include_provenance):
    columns = set()
    if has_columns(conn, "vitals", "id", "date", "time", "source"):
        from . import table_columns
        columns = table_columns(conn, "vitals")
    if not columns:
        return
    hhmm = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    where, params = date_where("date", date_range)
    rows = _rows(conn, "SELECT * FROM vitals WHERE " + where + " ORDER BY date,id", params)
    by_day = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)
        for metric, _unit in VITAL_FIELDS.items():
            if metric not in columns or finite(row.get(metric)) is None:
                continue
            _emit(out, definitions, f"vitals.{metric}_episode",
                  _episode_at(row, hhmm), row[metric],
                  source=row.get("source") or "manual", provenance={
                      "adapter": ADAPTER_ID, "table": "vitals",
                      "natural_key": natural_key("vitals", row),
                      "observation_date": row["date"],
                      "stored_time": row.get("time"),
                      "time_parseable": bool(hhmm and hhmm(row.get("time")) is not None),
                      "episode_not_forward_filled": True,
                  }, include_provenance=include_provenance)
    for day, day_rows in sorted(by_day.items()):
        for metric in VITAL_FIELDS:
            values = [row[metric] for row in day_rows
                      if metric in columns and finite(row.get(metric)) is not None]
            if not values:
                continue
            _emit(out, definitions, f"vitals.{metric}_mean", day,
                  sum(values) / len(values), source="vitals", provenance={
                      "adapter": ADAPTER_ID, "table": "vitals",
                      "formula": "daily_arithmetic_mean_known_episodes",
                      "episode_count": len(values),
                      "parent_natural_keys": [natural_key("vitals", row)
                                              for row in day_rows
                                              if finite(row.get(metric)) is not None],
                  }, include_provenance=include_provenance)


def _load_assessments(conn, definitions, date_range, context, out, include_provenance):
    if not has_columns(
        conn, "assessments", "id", "date", "scale", "part", "score",
        "max_score", "source",
    ):
        return
    previous = {}
    today = getattr(context, "today", date.today())
    for row in _rows(conn, "SELECT * FROM assessments WHERE date IS NOT NULL ORDER BY date,id"):
        scale = row.get("scale")
        if not isinstance(scale, str) or not scale.strip() or finite(row.get("score")) is None:
            continue
        part = row.get("part") if isinstance(row.get("part"), str) and row["part"].strip() else "total"
        scale_key, scale_token, _ = identity_parts("assessment", scale)
        part_key, part_token, _ = identity_parts("assessment_part", part)
        comparison_key = (scale_key, part_key)
        prior = previous.get(comparison_key)
        if not in_range(row["date"], date_range):
            previous[comparison_key] = row
            continue
        prefix = f"assessment.{scale_token}.{part_token}"
        provenance = {
            "adapter": ADAPTER_ID, "table": "assessments",
            "natural_key": natural_key("assessments", row),
            "scale_identity_key": scale_key, "part_identity_key": part_key,
            "original_scale": scale, "original_part": row.get("part"),
            "episode_not_daily_fill": True,
            "age_days": (today - date.fromisoformat(row["date"])).days,
            "prior_date": prior["date"] if prior else None,
            "prior_score": prior["score"] if prior else None,
            "change": row["score"] - prior["score"] if prior else None,
        }
        _emit(out, definitions, f"{prefix}.score", row["date"], row["score"],
              source=row.get("source") or "manual", provenance=provenance,
              include_provenance=include_provenance)
        maximum = finite(row.get("max_score"))
        if maximum is not None and maximum > 0:
            fraction = row["score"] / maximum
            prior_maximum = finite(prior.get("max_score")) if prior else None
            prior_fraction = (
                prior["score"] / prior_maximum
                if prior is not None and prior_maximum is not None
                and prior_maximum > 0 else None
            )
            _emit(out, definitions, f"{prefix}.fraction", row["date"],
                  fraction, source=row.get("source") or "manual",
                  provenance={**provenance, "max_score": maximum,
                              "prior_fraction": prior_fraction,
                              "change": (fraction - prior_fraction)
                                  if prior_fraction is not None else None,
                              "formula": "score/max_score_positive_denominator"},
                  include_provenance=include_provenance)
        previous[comparison_key] = row


def _load_skincare(
    conn, definitions, date_range, effective, out, include_provenance,
):
    if not has_columns(
        conn, "skincare_products", "product_id", "brand", "product_name", "active",
    ) or not has_columns(
        conn, "skincare_log", "id", "date", "product_id", "used", "source",
    ):
        return
    products = {}
    active_identity_keys = set()
    for row in _rows(conn, "SELECT * FROM skincare_products WHERE active=1 ORDER BY product_id"):
        brand = row.get("brand") if isinstance(row.get("brand"), str) else ""
        name = row.get("product_name") if isinstance(row.get("product_name"), str) else ""
        label = " ".join(value.strip() for value in (brand, name) if value.strip())
        if label:
            identity_key, token, _normalized = identity_parts("skincare", label)
            products[row["product_id"]] = (row, label, identity_key, token)
            active_identity_keys.add(identity_key)
    where, params = date_where("date", date_range)
    grouped = defaultdict(list)
    for row in _rows(conn, "SELECT * FROM skincare_log WHERE " + where + " ORDER BY date,id", params):
        if row.get("product_id") in products and row.get("used") in {0, 1}:
            product, label, identity_key, token = products[row["product_id"]]
            row["_product"] = product
            row["_product_label"] = label
            grouped[(row["date"], identity_key, token)].append(row)
    observed = set()
    for (day, identity_key, token), rows in sorted(grouped.items()):
        key = definitions.identity_key(
            f"skincare.product.{token}.used", prefix="skincare.product.",
            suffix=".used", identity_key=identity_key,
        )
        _emit(out, definitions, key, day, max(row["used"] for row in rows),
              source=rows[-1].get("source") or "manual", provenance={
            "adapter": ADAPTER_ID, "table": "skincare_log",
            "natural_keys": [natural_key("skincare_log", row) for row in rows],
            "product_natural_keys": sorted({
                f"skincare_products:{row['product_id']}" for row in rows
            }),
            "identity_key": identity_key,
            "original_brands": sorted({
                row["_product"].get("brand") for row in rows
                if row["_product"].get("brand")
            }),
            "original_product_names": sorted({
                row["_product"].get("product_name") for row in rows
                if row["_product"].get("product_name")
            }),
            "normalized_brand_plus_name_identity": True,
                  "duplicate_rule": "max_explicit_0_or_1",
                  "missing_is_not_miss": True,
              }, include_provenance=include_provenance)
        if key is not None:
            observed.add((day, key))
    for key, definition in definitions.by_key.items():
        if not key.startswith("skincare.product.") or not key.endswith(".used"):
            continue
        identity_key = definition_identity_key(definition)
        if identity_key not in active_identity_keys:
            continue
        for day, comp in complete_dates(effective, "other_event", identity_key).items():
            if (day, key) in observed:
                continue
            _emit(out, definitions, key, day, 0, source=comp["source"],
                  state="structural_zero", provenance={
                      "adapter": ADAPTER_ID,
                      "table": "capture_completeness_revisions",
                      "natural_key": f"capture_completeness_revisions:{comp['id']}",
                      "scope": "other_event", "identity_key": identity_key,
                  }, include_provenance=include_provenance)


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    effective = effective_completeness(conn, date_range)
    _load_vitals(conn, definitions, date_range, context, out, include_provenance)
    _load_assessments(conn, definitions, date_range, context, out, include_provenance)
    _load_skincare(conn, definitions, date_range, effective, out, include_provenance)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "REGISTRY_VERSION", "VITAL_FIELDS", "load"]
