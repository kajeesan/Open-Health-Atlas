"""Nutrition totals, nutrients, exact food/meal identity, water and supplements."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from . import (
    DefinitionIndex, REGISTRY_VERSION, complete_dates, completeness_row,
    context_value, date_where, definition_identity_key, effective_completeness,
    finite, has_columns,
    identity_parts, make_observation, natural_key, table_columns, table_exists,
    token_from_identity_key,
)
from .. import events as phase2_events


ADAPTER_ID = "nutrition"
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
LOGGED_FIELDS = {
    "kcal": ("nutrition.logged.kcal", "kcal"),
    "protein_g": ("nutrition.logged.protein_g", "g"),
    "carbs_g": ("nutrition.logged.carbs_g", "g"),
    "fat_g": ("nutrition.logged.fat_g", "g"),
    "fiber_g": ("nutrition.logged.fiber_g", "g"),
}
CRONOMETER_FIELDS = {
    "energy_kcal": ("nutrition.cronometer.energy_kcal", "kcal"),
    "protein_g": ("nutrition.cronometer.protein_g", "g"),
    "carbs_g": ("nutrition.cronometer.carbs_g", "g"),
    "fat_g": ("nutrition.cronometer.fat_g", "g"),
    "fiber_g": ("nutrition.cronometer.fiber_g", "g"),
}


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, when, value, *, unit=None, source=None,
    provenance=None, include_provenance=False, state="observed",
):
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, when, value, unit=unit, source=source, state=state,
        provenance=provenance, include_provenance=include_provenance,
    ))


def _strict_time(value, parser):
    if parser is None:
        return None
    try:
        result = parser(value)
    except (TypeError, ValueError):
        return None
    return int(result) if result is not None else None


def _identity(conn, entity_type, label):
    if table_exists(conn, "entity_aliases"):
        key, revision = phase2_events.resolve_identity(conn, entity_type, label)
        if key:
            return key, token_from_identity_key(key), revision
    key, token, _ = identity_parts(entity_type, label)
    return key, token, None


def _known_total(rows, column):
    values = [row.get(column) for row in rows]
    return sum(values) if values and all(finite(value) is not None for value in values) else None


def _load_logged(conn, definitions, date_range, effective, include_provenance, out):
    columns = table_columns(conn, "nutrition_log")
    if "date" not in columns:
        return []
    where, params = date_where("date", date_range)
    rows = _rows(conn, "SELECT * FROM nutrition_log WHERE " + where + " ORDER BY date,id", params)
    by_day = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)
    for day, day_rows in sorted(by_day.items()):
        comp = completeness_row(effective, day, "nutrition_total")
        complete = comp is not None and comp["state"] == "complete"
        for column, (key, unit) in LOGGED_FIELDS.items():
            if column not in columns:
                continue
            value = _known_total(day_rows, column)
            _emit(out, definitions, key, day, value, unit=unit, source="nutrition_log",
                  provenance={
                      "adapter": ADAPTER_ID, "table": "nutrition_log",
                      "natural_keys": [natural_key("nutrition_log", row) for row in day_rows],
                      "all_matching_values_known": value is not None,
                      "candidate_eligible": complete and value is not None,
                      "completeness_revision_id": comp["id"] if complete else None,
                  }, include_provenance=include_provenance)
    # A complete nutrition-total attestation/source interval with no rows is
    # the sole case where absence proves a numeric zero.  Partial/unknown, or
    # rows whose values are themselves missing, remain missing.
    for day, comp in complete_dates(effective, "nutrition_total").items():
        if day in by_day:
            continue
        keys = set(comp.keys()) if hasattr(comp, "keys") else set()
        for column, (key, unit) in LOGGED_FIELDS.items():
            if column not in columns:
                continue
            _emit(out, definitions, key, day, 0, unit=unit,
                  state="structural_zero", source=comp["source"], provenance={
                      "adapter": ADAPTER_ID,
                      "table": "capture_completeness_revisions",
                      "natural_key": f"capture_completeness_revisions:{comp['id']}",
                      "scope": "nutrition_total",
                      "explicit_none": bool(comp["explicit_none"])
                          if "explicit_none" in keys else False,
                      "no_nutrition_rows": True,
                  }, include_provenance=include_provenance)
    return rows


def _micro_specs(context):
    specs = {}
    for item in context_value(context, "MICRO_SEED", ()) or ():
        if isinstance(item, Mapping) and item.get("key"):
            specs[item["key"]] = item
    return specs


def _effective_micro_targets(context):
    """Read targets only from the existing reviewed shared targets path."""
    provider = context_value(context, "nutrition_micro_targets") or context_value(
        context, "micro_targets")
    if provider is None:
        return {}
    values = provider() if callable(provider) else provider
    result = {}
    for item in values or ():
        if isinstance(item, Mapping) and item.get("nutrient"):
            result[item["nutrient"]] = item
    return result


def _convert_seed_amount(row, spec):
    unit = str(row.get("unit") or "").strip().casefold().replace("μ", "µ")
    units = {str(key).casefold().replace("μ", "µ"): value
             for key, value in (spec.get("units") or {}).items()}
    if unit == str(spec.get("unit") or "").casefold().replace("μ", "µ"):
        factor = 1.0
    elif unit in units:
        factor = units[unit]
    else:
        return None
    amount = finite(row.get("amount"))
    return amount * factor if amount is not None else None


def _load_nutrients(conn, definitions, date_range, context, include_provenance, out):
    if not has_columns(conn, "nutrient_daily", "date", "nutrient", "amount", "unit", "source"):
        return
    where, params = date_where("date", date_range)
    rows = _rows(conn, "SELECT * FROM nutrient_daily WHERE " + where
                 + " ORDER BY date,nutrient,source", params)
    selected = {}
    for row in rows:
        pair = (row["date"], row["nutrient"])
        rank = (0 if (row.get("source") or "").casefold() == "cronometer" else 1,
                (row.get("source") or "").casefold())
        if pair not in selected or rank < selected[pair][0]:
            selected[pair] = (rank, row)
    micro = _micro_specs(context)
    effective_targets = _effective_micro_targets(context)
    for (_day, nutrient), (_rank, row) in sorted(selected.items()):
        day, source = row["date"], row.get("source") or "nutrient_daily"
        prov = {
            "adapter": ADAPTER_ID, "table": "nutrient_daily",
            "natural_key": natural_key("nutrient_daily", row, "date", "nutrient", "source"),
            "selected_source": source,
        }
        if nutrient in CRONOMETER_FIELDS and source.casefold() == "cronometer":
            key, unit = CRONOMETER_FIELDS[nutrient]
            expected = unit.casefold()
            if str(row.get("unit") or "").casefold() == expected:
                _emit(out, definitions, key, day, row["amount"], unit=unit, source=source,
                      provenance=prov, include_provenance=include_provenance)
        spec = micro.get(nutrient)
        if spec is None:
            continue
        amount = _convert_seed_amount(row, spec)
        if amount is None:
            continue
        prefix = f"nutrition.nutrient.{nutrient}"
        _emit(out, definitions, f"{prefix}.amount", day, amount,
              unit=spec.get("unit"), source=source, provenance={
                  **prov, "unit_validated_against_seed": True,
              }, include_provenance=include_provenance)
        target_spec = effective_targets.get(nutrient) or {}
        target = finite(target_spec.get("target"))
        if target is not None and target > 0:
            fraction = amount / target
            derived = {
                "adapter": ADAPTER_ID, "formula": "amount/effective_target",
                "parent_natural_keys": [prov["natural_key"]], "target": target,
                "target_source": target_spec.get("source"),
                "shared_targets_path": "nutrition_micro_targets",
                "owner_override": bool(target_spec.get("override")),
            }
            _emit(out, definitions, f"{prefix}.target_fraction", day, fraction,
                  unit="fraction", source=source, provenance=derived,
                  include_provenance=include_provenance)
            _emit(out, definitions, f"{prefix}.target_met", day, int(fraction >= 1),
                  unit="binary", source=source, provenance=derived,
                  include_provenance=include_provenance)


def _food_identity(conn, row):
    if row.get("recipe_id"):
        key = f"recipe:{row['recipe_id']}"
        return "recipe", key, token_from_identity_key(key), None
    if (row.get("food_name") or "").strip():
        key, token, revision = _identity(conn, "food", row["food_name"])
        return "named", key, token, revision
    return None


def _load_food(conn, definitions, rows, date_range, context, effective,
               include_provenance, out):
    parser = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    grouped = defaultdict(list)
    meal_grouped = defaultdict(list)
    observed_food: set[tuple[str, str]] = set()
    for row in rows:
        identity = _food_identity(conn, row)
        if identity:
            kind, identity_key, token, revision = identity
            grouped[(row["date"], kind, identity_key, token, revision)].append(row)
        meal_type = row.get("meal_type")
        if meal_type in MEAL_TYPES:
            meal_grouped[(row["date"], meal_type)].append(row)
        minute = _strict_time(row.get("time"), parser)
        if minute is not None:
            when = f"{row['date']}T{minute // 60:02d}:{minute % 60:02d}"
            _emit(out, definitions, "meal.time_min", when, minute,
                  unit="minute_of_day", source=row.get("source"), provenance={
                      "adapter": ADAPTER_ID, "table": "nutrition_log",
                      "natural_key": natural_key("nutrition_log", row),
                      "explicit_time": True,
                  }, include_provenance=include_provenance)

    # Meal type is a daily occurrence, not an item count. Multiple foods in the
    # same explicitly labelled meal therefore produce one binary observation
    # with every contributing row retained in lineage.
    for (day, meal_type), item_rows in sorted(meal_grouped.items()):
        key = f"meal.type.{meal_type}.occurred"
        sources = sorted({row.get("source") or "nutrition_log" for row in item_rows})
        _emit(out, definitions, key, day, 1, unit="binary",
              source=sources[0] if len(sources) == 1 else "mixed", provenance={
                  "adapter": ADAPTER_ID, "table": "nutrition_log",
                  "natural_keys": [
                      natural_key("nutrition_log", row) for row in item_rows
                  ],
                  "explicit_meal_type": True,
                  "daily_binary_aggregation": True,
              }, include_provenance=include_provenance)
        if key in definitions:
            observed_food.add((day, key))

    for (day, kind, identity_key, token, revision), item_rows in sorted(grouped.items()):
        prefix = f"food.{kind}.{token}"
        prov = {
            "adapter": ADAPTER_ID, "table": "nutrition_log",
            "identity_key": identity_key,
            "original_labels": sorted({row.get("food_name") for row in item_rows
                                       if row.get("food_name")}),
            "alias_revision_id": revision,
            "natural_keys": [natural_key("nutrition_log", row) for row in item_rows],
        }
        for suffix, value, unit in (
            ("occurred", 1, "binary"),
            ("grams", _known_total(item_rows, "grams"), "g"),
            ("kcal", _known_total(item_rows, "kcal"), "kcal"),
            ("protein_g", _known_total(item_rows, "protein_g"), "g"),
        ):
            key = definitions.identity_key(
                f"{prefix}.{suffix}", prefix=f"food.{kind}.", suffix=f".{suffix}",
                identity_key=identity_key,
            )
            _emit(out, definitions, key, day, value, unit=unit,
                  source=item_rows[-1].get("source"), provenance=prov,
                  include_provenance=include_provenance)
            if key:
                observed_food.add((day, key))

    for key, definition in definitions.by_key.items():
        zeroable = (key.startswith("food.") and key.endswith((".occurred", ".grams", ".kcal", ".protein_g"))) \
            or (key.startswith("meal.type.") and key.endswith(".occurred"))
        if not zeroable:
            continue
        identity_key = definition_identity_key(definition)
        for day, comp in complete_dates(effective, "food_identity", identity_key).items():
            if (day, key) in observed_food:
                continue
            _emit(out, definitions, key, day, 0, state="structural_zero",
                  source=comp["source"], provenance={
                      "adapter": ADAPTER_ID,
                      "table": "capture_completeness_revisions",
                      "natural_key": f"capture_completeness_revisions:{comp['id']}",
                  }, include_provenance=include_provenance)


def _load_water(conn, definitions, date_range, include_provenance, out):
    if not has_columns(conn, "intake", "date", "water_ml"):
        return
    where, params = date_where("date", date_range)
    for row in _rows(conn, "SELECT * FROM intake WHERE " + where, params):
        _emit(out, definitions, "nutrition.water_ml", row["date"], row.get("water_ml"),
              unit="ml", source=row.get("source"), provenance={
                  "adapter": ADAPTER_ID, "table": "intake",
                  "natural_key": natural_key("intake", row, "date"),
              }, include_provenance=include_provenance)


def _load_supplements(conn, definitions, date_range, context, effective,
                      include_provenance, out):
    if not has_columns(conn, "supplements_log", "id", "date", "supplement_id", "taken") \
            or not has_columns(conn, "supplement_products", "supplement_id", "name"):
        return
    where, params = date_where("l.date", date_range)
    rows = _rows(conn, """SELECT l.*,p.name product_name,p.brand product_brand,p.active product_active
        FROM supplements_log l JOIN supplement_products p
          ON p.supplement_id=l.supplement_id WHERE """ + where + " ORDER BY l.date,l.id", params)
    parser = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    observed = set()
    identities = {}
    for row in rows:
        label = row.get("product_name")
        if not (label or "").strip():
            continue
        identity_key, token, revision = _identity(conn, "supplement", label)
        identities[token] = identity_key
        prefix = f"supplement.{token}"
        minute = _strict_time(row.get("time_taken"), parser)
        prov = {
            "adapter": ADAPTER_ID, "table": "supplements_log",
            "natural_key": natural_key("supplements_log", row),
            "identity_key": identity_key, "original_label": label,
            "alias_revision_id": revision,
        }
        for suffix, value, unit in (
            ("taken", row.get("taken") if row.get("taken") in (0, 1) else None, "binary"),
            ("dose", row.get("dose_taken"), None),
            ("time_min", minute, "minute_of_day"),
        ):
            key = definitions.identity_key(
                f"{prefix}.{suffix}", prefix="supplement.", suffix=f".{suffix}",
                identity_key=identity_key,
            )
            when = (f"{row['date']}T{minute // 60:02d}:{minute % 60:02d}"
                    if suffix == "time_min" and minute is not None else row["date"])
            _emit(out, definitions, key, when, value, unit=unit,
                  source=row.get("source"), provenance=prov,
                  include_provenance=include_provenance)
            if key:
                observed.add((row["date"], key))

    for key, definition in definitions.by_key.items():
        if not key.startswith("supplement.") or not key.endswith(".taken"):
            continue
        identity_key = definition_identity_key(definition)
        if not identity_key:
            continue
        for day, comp in complete_dates(effective, "supplement", identity_key).items():
            if (day, key) in observed:
                continue
            _emit(out, definitions, key, day, 0, state="structural_zero",
                  unit="binary", source=comp["source"], provenance={
                      "adapter": ADAPTER_ID,
                      "table": "capture_completeness_revisions",
                      "natural_key": f"capture_completeness_revisions:{comp['id']}",
                  }, include_provenance=include_provenance)


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    effective = effective_completeness(conn, date_range)
    rows = _load_logged(conn, definitions, date_range, effective,
                        include_provenance, out) if table_exists(conn, "nutrition_log") else []
    _load_nutrients(conn, definitions, date_range, context, include_provenance, out)
    _load_food(conn, definitions, rows, date_range, context, effective,
               include_provenance, out)
    _load_water(conn, definitions, date_range, include_provenance, out)
    _load_supplements(conn, definitions, date_range, context, effective,
                      include_provenance, out)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "REGISTRY_VERSION", "load"]
