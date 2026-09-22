"""Recipe nutrient scaling, inventory guards and food capture validation."""

import math
import os
from pathlib import Path
import re

from . import events, migrations, runtime
from .capture_contracts import capture_source

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")

RESTOCK_ACTIONS = {"notified", "restock", "alternatives", "later", "skip"}

RESTOCK_THRESHOLD_MAX = 1_000_000


def recipe(c, key):
    """Find a recipe by its exact identifier or name."""
    return c.execute("SELECT * FROM recipes WHERE recipe_id=? OR name=?", (key, key)).fetchone()


def recipe_ingredients_path(recipe_id, *, vault):
    """Validated vault sidecar for one recipe's structured ingredient list."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,159}", recipe_id or ""):
        raise SystemExit("recipe has an unsafe id; ingredients were not written")
    target = os.path.abspath(Path(vault) / "personal" / "recipes" / f"{recipe_id}.ingredients.json")
    if os.path.commonpath([target, vault]) != vault:
        raise SystemExit("ingredient path escapes the vault")
    return target


def ingredient_number(value, field, index):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SystemExit(f"ingredient {index} {field} must be a number")
    value = float(value)
    if not (math.isfinite(value) and 0 < value <= 1_000_000):
        raise SystemExit(f"ingredient {index} {field} must be finite and > 0")
    return int(value) if value.is_integer() else value


def recipes_has_meal_type(c):
    """Require the migration-owned recipe column without DDL."""
    migrations.require_table(c, "recipes", ("meal_type",))
    return True


def compute_nutrients(c, rid, grams, batch_grams):
    """Scale stored batch totals (the legacy per_gram column) to eaten grams."""
    rows = c.execute("SELECT nutrient,unit,per_gram FROM recipe_nutrients WHERE recipe_id=?", (rid,)).fetchall()
    res = {}
    for r in rows:
        res[r["nutrient"]] = round(r["per_gram"] / batch_grams * grams, 3)
    return res


def restock_state_ready(c):
    """Require the migration-owned restock columns without creating them."""
    migrations.require_table(
        c, "recipe_restock_state",
        ("recipe_id", "action", "threshold", "portions_at_notice", "snooze_until", "updated_at"),
    )


def restock_threshold(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SystemExit("--threshold must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= RESTOCK_THRESHOLD_MAX:
        raise SystemExit(
            f"--threshold must be finite and between 0 and {RESTOCK_THRESHOLD_MAX}"
        )
    return result


def restock_due(c, recipe_id, portions, threshold, on_date):
    if portions > threshold:
        return False
    state = c.execute(
        """SELECT action,snooze_until FROM recipe_restock_state
             WHERE recipe_id=?""",
        (recipe_id,),
    ).fetchone()
    if not state:
        return True
    if state["action"] == "later" and state["snooze_until"]:
        return state["snooze_until"] <= on_date
    return False


def log_nutrition(c, rid, name, grams, d, *, time_value=None, meal_type=None, source=None):
    """Append scaled nutrition in the caller transaction, retaining omitted source defaults."""
    migrations.require_table(c, "nutrition_log", ("time", "meal_type"))
    r = c.execute("SELECT batch_grams FROM recipes WHERE recipe_id=?", (rid,)).fetchone()
    bg = r["batch_grams"] if r else None
    n = compute_nutrients(c, rid, grams, bg) if bg else {}
    cols = ["date", "recipe_id", "food_name", "grams", "kcal", "protein_g",
            "carbs_g", "fat_g", "fiber_g", "time", "meal_type"]
    values = [d, rid, name, grams, n.get("Energy"), n.get("Protein"), n.get("Carbs"),
              n.get("Fat"), n.get("Fiber"), time_value, meal_type]
    if source is not None:
        cols.append("source"); values.append(source)
    c.execute(f"INSERT INTO nutrition_log({','.join(cols)}) VALUES({','.join('?' * len(cols))})", values)
    return n, bg


def food_capture_values(a, *, clock):
    """Validate the optional date, time, meal type and capture source."""
    try:
        d = events.iso_date(a.date, "--date") if a.date else runtime.today(clock=clock)
    except events.CaptureError as exc:
        raise SystemExit(str(exc))
    t = getattr(a, "time", None)
    if t is not None:
        try:
            events.hhmm(t, "--time", nullable=False)
        except events.CaptureError as exc:
            raise SystemExit(str(exc))
    meal_type = getattr(a, "meal_type", None)
    if meal_type is not None:
        meal_type = meal_type.strip().lower()
        if meal_type not in MEAL_TYPES:
            raise SystemExit(f"--meal-type must be one of: {', '.join(MEAL_TYPES)}")
    return d, t, meal_type, capture_source(getattr(a, "source", None))
