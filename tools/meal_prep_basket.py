#!/usr/bin/env python3
"""Optimize a quality-approved, package-rounded grocery basket.

Input is the candidate JSON documented in references/shopping-policy.md.
The script performs price arithmetic only; product-quality judgment stays with
the researching agent and is represented by quality_equivalent.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


BASE_UNITS = {"g", "ml", "piece"}
UNIT_ALIASES = {
    "g": ("g", Decimal("1")),
    "gram": ("g", Decimal("1")),
    "grams": ("g", Decimal("1")),
    "kg": ("g", Decimal("1000")),
    "ml": ("ml", Decimal("1")),
    "milliliter": ("ml", Decimal("1")),
    "milliliters": ("ml", Decimal("1")),
    "l": ("ml", Decimal("1000")),
    "liter": ("ml", Decimal("1000")),
    "liters": ("ml", Decimal("1000")),
    "piece": ("piece", Decimal("1")),
    "pieces": ("piece", Decimal("1")),
    "pcs": ("piece", Decimal("1")),
    "stk": ("piece", Decimal("1")),
}


def fail(message: str) -> None:
    raise ValueError(message)


def parse_iso(value: str | None, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD: {value}") from exc


def money(value: float) -> float:
    return round(value + 1e-9, 2)


def decimal_number(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not number.is_finite():
        fail(f"{field} must be finite")
    return number


def clean_number(value: Decimal) -> int | float:
    rounded = value.quantize(Decimal("0.001"))
    if rounded == rounded.to_integral():
        return int(rounded)
    return float(rounded)


def scale_requirements(data: dict, source_portions: Decimal, target_portions: Decimal) -> dict:
    """Scale a recipe ingredient export into optimizer-ready base-unit needs."""
    if source_portions <= 0 or target_portions <= 0:
        fail("source and target portions must be positive")
    ingredients = data.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        fail("recipe ingredients must be a non-empty list")

    factor = target_portions / source_portions
    scaled: list[dict] = []
    for ingredient in ingredients:
        name = str(ingredient.get("name") or "").strip()
        if not name:
            fail("every recipe ingredient needs a name")
        raw_unit = str(ingredient.get("unit") or "").strip().lower()
        alias = UNIT_ALIASES.get(raw_unit)

        if alias:
            base_unit, multiplier = alias
            amount = decimal_number(ingredient.get("amount"), f"{name}.amount")
            required = amount * multiplier * factor
        elif ingredient.get("weight_g") is not None:
            base_unit = "g"
            required = decimal_number(ingredient["weight_g"], f"{name}.weight_g") * factor
        else:
            fail(f"{name}: unsupported unit {raw_unit!r} and no weight_g fallback")

        if required <= 0:
            fail(f"{name}: scaled amount must be positive")
        scaled.append(
            {
                "name": name,
                "required_amount": clean_number(required),
                "unit": base_unit,
            }
        )

    return {
        "recipe_id": data.get("recipe_id"),
        "recipe_name": data.get("recipe_name"),
        "source_portions": clean_number(source_portions),
        "target_portions": clean_number(target_portions),
        "scale": clean_number(factor),
        "ingredients": scaled,
    }


def prepare(data: dict) -> tuple[list[dict], list[dict]]:
    shopping_date = parse_iso(data.get("shopping_date"), "shopping_date")
    prepared: list[dict] = []
    uncovered: list[dict] = []

    ingredients = data.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        fail("ingredients must be a non-empty list")

    for ingredient in ingredients:
        name = str(ingredient.get("name") or "").strip()
        if not name:
            fail("every ingredient needs a name")
        required = float(ingredient.get("required_amount") or 0)
        unit = ingredient.get("unit")
        if required <= 0:
            fail(f"{name}: required_amount must be positive")
        if unit not in BASE_UNITS:
            fail(f"{name}: unit must be one of {sorted(BASE_UNITS)}")

        candidates: list[dict] = []
        for offer in ingredient.get("offers") or []:
            if offer.get("quality_equivalent") is not True:
                continue
            if offer.get("package_unit") != unit:
                continue
            package_amount = float(offer.get("package_amount") or 0)
            package_price = float(offer.get("price") or 0)
            if package_amount <= 0 or package_price < 0:
                continue

            valid_from = parse_iso(offer.get("valid_from"), f"{name}.valid_from")
            valid_until = parse_iso(offer.get("valid_until"), f"{name}.valid_until")
            if shopping_date:
                if valid_from and shopping_date < valid_from:
                    continue
                if valid_until and shopping_date > valid_until:
                    continue

            packages = math.ceil(required / package_amount)
            limit = offer.get("purchase_limit_packages")
            if limit is not None and packages > int(limit):
                continue

            bought = packages * package_amount
            candidate = {
                "ingredient": name,
                "aisle": ingredient.get("aisle"),
                "quality_floor": ingredient.get("quality_floor"),
                "store": str(offer.get("store") or "").strip(),
                "product": str(offer.get("product") or "").strip(),
                "packages": packages,
                "package_amount": package_amount,
                "unit": unit,
                "required_amount": required,
                "purchased_amount": bought,
                "leftover_amount": round(bought - required, 3),
                "cost": money(packages * package_price),
                "package_price": money(package_price),
                "valid_from": offer.get("valid_from"),
                "valid_until": offer.get("valid_until"),
                "price_type": offer.get("price_type"),
                "quality_note": offer.get("quality_note"),
                "url": offer.get("url"),
            }
            if candidate["store"] and candidate["product"]:
                candidates.append(candidate)

        if not candidates:
            uncovered.append(
                {
                    "ingredient": name,
                    "required_amount": required,
                    "unit": unit,
                    "reason": "no quality-approved offer valid for the shopping date",
                }
            )
        else:
            prepared.append(
                {
                    "ingredient": name,
                    "required_amount": required,
                    "unit": unit,
                    "candidates": candidates,
                }
            )

    return prepared, uncovered


def best_plan_for_stores(prepared: list[dict], stores: frozenset[str]) -> dict | None:
    selected = []
    for ingredient in prepared:
        choices = [c for c in ingredient["candidates"] if c["store"] in stores]
        if not choices:
            return None
        selected.append(min(choices, key=lambda c: (c["cost"], c["leftover_amount"], c["store"])))
    used_stores = sorted({item["store"] for item in selected})
    return {
        "stores": used_stores,
        "store_count": len(used_stores),
        "total": money(sum(item["cost"] for item in selected)),
        "items": selected,
    }


def optimize(data: dict) -> dict:
    prepared, uncovered = prepare(data)
    if uncovered:
        return {
            "ok": False,
            "currency": data.get("currency", "DKK"),
            "shopping_date": data.get("shopping_date"),
            "uncovered": uncovered,
            "message": "Complete basket cannot be optimized until every ingredient has a valid offer.",
        }

    stores = sorted({c["store"] for ing in prepared for c in ing["candidates"]})
    if not stores:
        fail("no usable stores")
    if len(stores) > 14:
        fail("too many stores for household basket optimization (maximum 14)")

    plans_by_store_set: list[dict] = []
    for size in range(1, len(stores) + 1):
        for combo in itertools.combinations(stores, size):
            plan = best_plan_for_stores(prepared, frozenset(combo))
            if plan is not None:
                plans_by_store_set.append(plan)

    if not plans_by_store_set:
        fail("no complete basket can be formed")

    one_store_plans = [p for p in plans_by_store_set if p["store_count"] == 1]
    easiest = min(one_store_plans, key=lambda p: p["total"]) if one_store_plans else None

    # Deduplicate plans that declared different supersets but selected identical stores/items.
    unique: dict[tuple, dict] = {}
    for plan in plans_by_store_set:
        key = (
            tuple(plan["stores"]),
            tuple((i["ingredient"], i["store"], i["product"], i["cost"]) for i in plan["items"]),
        )
        previous = unique.get(key)
        if previous is None or plan["total"] < previous["total"]:
            unique[key] = plan

    threshold = float(data.get("extra_store_min_savings", 50))
    if threshold < 0:
        fail("extra_store_min_savings cannot be negative")

    def adjusted(plan: dict) -> tuple[float, float, int]:
        return (
            money(plan["total"] + threshold * max(0, plan["store_count"] - 1)),
            plan["total"],
            plan["store_count"],
        )

    recommended = copy.deepcopy(min(unique.values(), key=adjusted))
    recommended["selection_score"] = adjusted(recommended)[0]

    if easiest:
        recommended["savings_vs_one_store"] = money(easiest["total"] - recommended["total"])
    else:
        recommended["savings_vs_one_store"] = None

    best_actual = copy.deepcopy(min(unique.values(), key=lambda p: (p["total"], p["store_count"])))

    return {
        "ok": True,
        "currency": data.get("currency", "DKK"),
        "shopping_date": data.get("shopping_date"),
        "extra_store_min_savings": threshold,
        "recommended": recommended,
        "easiest_one_store": copy.deepcopy(easiest),
        "absolute_cheapest": best_actual,
        "uncovered": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_json", type=Path)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--scale-from", type=str)
    parser.add_argument("--scale-to", type=str)
    args = parser.parse_args()

    try:
        data = json.loads(args.candidate_json.read_text(encoding="utf-8"))
        if (args.scale_from is None) != (args.scale_to is None):
            fail("--scale-from and --scale-to must be supplied together")
        if args.scale_from is not None:
            result = scale_requirements(
                data,
                decimal_number(args.scale_from, "--scale-from"),
                decimal_number(args.scale_to, "--scale-to"),
            )
        else:
            result = optimize(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    if "ok" in result and not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
