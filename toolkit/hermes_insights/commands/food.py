"""Food writes, freezer inventory and replaceable recipe ingredient sidecars."""

from datetime import date, timedelta
import json
import math
import os
from pathlib import Path

from .. import events, migrations, runtime
from ..food import (
    MEAL_TYPES, RESTOCK_ACTIONS, compute_nutrients, food_capture_values,
    ingredient_number, log_nutrition, recipe, recipe_ingredients_path,
    recipes_has_meal_type, restock_due, restock_state_ready, restock_threshold,
)
from ..importers.common import stdin_text, valid_date


def set_batch(context, a):
    """Record the finished cooked weight of a recipe's batch (enables per-gram math).
    Optional --portions N also sets recipes.portions + grams_per_portion (= grams/N)
    for the recipe page's per-portion math. Unlike `prep`, this NEVER touches
    meal_inventory — it's the side-effect-free restore path (e.g. re-syncing
    batch/portions after a re-import), not a fresh prep event."""
    if a.portions is not None and a.portions < 1: raise SystemExit("--portions must be >= 1")
    c = runtime.connect(context.database)
    try:
        r = c.execute("SELECT recipe_id,name FROM recipes WHERE recipe_id=? OR name=?", (a.recipe, a.recipe)).fetchone()
        if not r: raise SystemExit(f"recipe not found: {a.recipe}")
        res = {"ok": True, "recipe": r["name"], "batch_grams": a.grams}
        if a.portions is not None:
            gpp = round(a.grams / a.portions, 1)
            c.execute("UPDATE recipes SET batch_grams=?, portions=?, grams_per_portion=? WHERE recipe_id=?",
                      (a.grams, a.portions, gpp, r["recipe_id"]))
            res.update({"portions": a.portions, "grams_per_portion": gpp})
        else:
            c.execute("UPDATE recipes SET batch_grams=? WHERE recipe_id=?", (a.grams, r["recipe_id"]))
        c.commit()
        return res
    finally:
        c.close()


def recipe_tag(context, a):
    """Tag a recipe with a meal type (breakfast/lunch/dinner/snack) for the
    panel's Recipes filter, or 'clear' to null it. Coach/agent-side config —
    NOT in either panel bridge allowlist (same posture as athletic-target-set).
    Migration 001 owns recipes.meal_type; this writer only validates and uses it."""
    mt = (a.meal_type or "").strip().lower()
    if mt not in MEAL_TYPES + ("clear",):
        raise SystemExit(f"meal_type must be one of: {', '.join(MEAL_TYPES)} (or 'clear' to remove)")
    c = runtime.connect(context.database)
    try:
        r = recipe(c, a.recipe)
        if not r: raise SystemExit(f"recipe not found: {a.recipe}")
        recipes_has_meal_type(c)
        val = None if mt == "clear" else mt
        c.execute("UPDATE recipes SET meal_type=? WHERE recipe_id=?", (val, r["recipe_id"]))
        c.commit()
        return {"ok": True, "recipe": r["name"], "meal_type": val}
    finally:
        c.close()


def recipe_ingredients_set(context, a, *, stdin):
    """Replace one recipe's structured ingredient sidecar from JSON on stdin.

    This is agent/SSH-only (never panel-bridge reachable). It validates the
    recipe against the database, normalizes a small explicit JSON contract,
    and atomically replaces only personal/recipes/<id>.ingredients.json.
    Nutrition totals, logs, and inventory are untouched.
    """
    c = runtime.connect_read_only(context.database)
    try:
        r = recipe(c, a.recipe)
    finally:
        c.close()
    if not r:
        raise SystemExit(f"recipe not found: {a.recipe}")

    raw = stdin_text(stdin, "recipe ingredients")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"recipe ingredients must be valid JSON: {exc.msg}")
    items = payload.get("ingredients") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise SystemExit("ingredients must be a JSON list containing 1-100 items")

    normalized = []
    allowed = {"name", "amount", "unit", "weight_g", "note"}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise SystemExit(f"ingredient {index} must be an object")
        unexpected = set(item) - allowed
        if unexpected:
            raise SystemExit(f"ingredient {index} has unsupported fields: {', '.join(sorted(unexpected))}")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise SystemExit(f"ingredient {index} name must be 1-200 characters")
        clean = {"name": name.strip()}
        if "amount" in item:
            clean["amount"] = ingredient_number(item["amount"], "amount", index)
            unit = item.get("unit")
            if not isinstance(unit, str) or not unit.strip() or len(unit.strip()) > 32:
                raise SystemExit(f"ingredient {index} unit must be 1-32 characters when amount is set")
            clean["unit"] = unit.strip()
        elif "unit" in item:
            raise SystemExit(f"ingredient {index} unit requires amount")
        if "weight_g" in item:
            clean["weight_g"] = ingredient_number(item["weight_g"], "weight_g", index)
        if "note" in item:
            note = item["note"]
            if not isinstance(note, str) or not note.strip() or len(note.strip()) > 300:
                raise SystemExit(f"ingredient {index} note must be 1-300 characters")
            clean["note"] = note.strip()
        normalized.append(clean)

    target = recipe_ingredients_path(r["recipe_id"], vault=context.vault)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    document = {
        "recipe_id": r["recipe_id"],
        "recipe_name": r["name"],
        "ingredients": normalized,
        "updated_at": context.clock().isoformat(timespec="seconds"),
    }
    tmp = Path(target).with_name(f"{Path(target).name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(document, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    rel = os.path.relpath(target, context.vault)
    return {"ok": True, "recipe": r["name"], "recipe_id": r["recipe_id"],
         "ingredients_count": len(normalized), "file": rel,
         "ingredients": normalized}


def prep(context, a):
    """Log a meal-prep batch: set batch weight + add portions to the freezer inventory."""
    c = runtime.connect(context.database)
    try:
        r = recipe(c, a.recipe)
        if not r: raise SystemExit(f"recipe not found: {a.recipe}")
        restock_state_ready(c)
        gpp = round(a.batch_grams / a.portions, 1) if a.batch_grams else r["grams_per_portion"]
        if a.batch_grams:
            c.execute("UPDATE recipes SET batch_grams=?, grams_per_portion=?, portions=? WHERE recipe_id=?",
                      (a.batch_grams, gpp, a.portions, r["recipe_id"]))
        c.execute("INSERT INTO meal_inventory(recipe_id,portions_remaining,grams_per_portion,prepped_on) VALUES(?,?,?,?)",
                  (r["recipe_id"], a.portions, gpp, runtime.today(clock=context.clock)))
        # A new batch begins a new stock cycle. Earlier skip/snooze/notice state
        # must not suppress the next genuine low-stock transition.
        c.execute("DELETE FROM recipe_restock_state WHERE recipe_id=?", (r["recipe_id"],))
        c.commit()
        return {"ok": True, "prepped": r["name"], "portions": a.portions, "grams_per_portion": gpp, "batch_grams": a.batch_grams or r["batch_grams"]}
    finally:
        c.close()


def eat(context, a):
    """Eat one (or more) portions from the freezer: decrement inventory + log nutrition."""
    if not 1 <= a.portions <= 100:
        raise SystemExit("--portions must be 1-100")
    c = runtime.connect(context.database)
    try:
        r = recipe(c, a.recipe)
        if not r: raise SystemExit(f"recipe not found: {a.recipe}")
        restock_state_ready(c)
        inv = c.execute("""SELECT id,portions_remaining,grams_per_portion FROM meal_inventory
                           WHERE recipe_id=? AND portions_remaining>0 ORDER BY prepped_on LIMIT 1""", (r["recipe_id"],)).fetchone()
        if not inv: raise SystemExit(f"no portions of '{r['name']}' left in inventory")
        p = a.portions
        if inv["portions_remaining"] < p: raise SystemExit(f"only {inv['portions_remaining']} portions left")
        grams = inv["grams_per_portion"] * p
        d, t, meal_type, source = food_capture_values(a, clock=context.clock)
        n, bg = log_nutrition(c, r["recipe_id"], r["name"], grams, d,
                               time_value=t, meal_type=meal_type, source=source)
        c.execute("UPDATE meal_inventory SET portions_remaining=portions_remaining-? WHERE id=?", (p, inv["id"]))
        if migrations.recorded_version(c) >= 2:
            events.invalidate_explicit_none(
                c, d, "food_identity", f"recipe:{r['recipe_id']}", source or "manual", None,
            )
            events.invalidate_explicit_none(
                c, d, "nutrition_total", None, source or "manual", None,
            )
        left = c.execute(
            """SELECT COALESCE(SUM(portions_remaining), 0) AS portions
                 FROM meal_inventory WHERE recipe_id=?""",
            (r["recipe_id"],),
        ).fetchone()["portions"]
        # Snoozes are operational reminders, so compare them with the current day
        # even when the consumption record itself is backdated.
        alert = restock_due(c, r["recipe_id"], left, 2, runtime.today(clock=context.clock))
        c.commit()
        extra = ({"date": d, "time": t, "meal_type": meal_type, "source": source}
                 if any(v is not None for v in (t, meal_type, source)) else {})
        return {"ok": True, "ate": r["name"], "portions": p, "grams": grams, "portions_left": left,
             "kcal": n.get("Energy"), "protein_g": n.get("Protein"),
             "restock_alert": alert,
             "restock_next": (
                 "deliver restock choices, then run restock-mark --action notified"
                 if alert else None
             ), **extra}
    finally:
        c.close()


def log_food(context, a):
    if not (math.isfinite(a.grams) and 0 < a.grams <= 100_000):
        raise SystemExit("--grams must be a positive finite number no greater than 100000")
    c = runtime.connect(context.database)
    try:
        r = recipe(c, a.recipe)
        if not r: raise SystemExit(f"recipe not found: {a.recipe}")
        d, t, meal_type, source = food_capture_values(a, clock=context.clock)
        n, bg = log_nutrition(c, r["recipe_id"], r["name"], a.grams, d,
                               time_value=t, meal_type=meal_type, source=source)
        if migrations.recorded_version(c) >= 2:
            events.invalidate_explicit_none(
                c, d, "food_identity", f"recipe:{r['recipe_id']}", source or "manual", None,
            )
            events.invalidate_explicit_none(
                c, d, "nutrition_total", None, source or "manual", None,
            )
        c.commit()
        extra = ({"time": t, "meal_type": meal_type, "source": source}
                 if any(v is not None for v in (t, meal_type, source)) else {})
        if not bg:
            return {"ok": True, "logged": r["name"], "grams": a.grams, "date": d,
                 "warning": "no batch weight set -> macros not computed. run `set-batch`.", **extra}
        return {"ok": True, "logged": r["name"], "grams": a.grams, "date": d,
             "kcal": n.get("Energy"), "protein_g": n.get("Protein"), "carbs_g": n.get("Carbs"), "fat_g": n.get("Fat"), **extra}
    finally:
        c.close()


def menu(context, a):
    c = runtime.connect(context.database)
    try:
        # Migration 001 owns meal_type. This read requires it and never runs DDL.
        recipes_has_meal_type(c)
        mt_col = ", r.meal_type"
        rows = c.execute(f"""SELECT r.name, r.recipe_id, SUM(m.portions_remaining) AS portions, m.grams_per_portion, r.batch_grams{mt_col}
            FROM meal_inventory m JOIN recipes r ON r.recipe_id=m.recipe_id
            WHERE m.portions_remaining>0 GROUP BY r.recipe_id ORDER BY portions DESC""").fetchall()
        items = []
        for r in rows:
            kcal = protein = None
            if r["batch_grams"]:
                n = compute_nutrients(c, r["recipe_id"], r["grams_per_portion"], r["batch_grams"])
                kcal, protein = n.get("Energy"), n.get("Protein")
            items.append({"recipe": r["name"], "portions_available": r["portions"],
                          "grams_per_portion": r["grams_per_portion"], "kcal_per_portion": kcal, "protein_g": protein,
                          "meal_type": r["meal_type"]})
        return {"date": a.date or runtime.today(clock=context.clock), "menu": items}
    finally:
        c.close()


def restock_check(context, a):
    """Return low-stock recipes whose notification state is currently due.

    This read does not mark an item as notified. A caller records the state only
    after its notification or user-choice surface succeeds.
    """
    threshold = restock_threshold(a.threshold)
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    c = runtime.connect(context.database)
    try:
        restock_state_ready(c)
        rows = c.execute(
            """SELECT r.recipe_id, r.name,
                      COALESCE(SUM(m.portions_remaining), 0) AS portions,
                      MAX(m.prepped_on) AS latest_batch
                 FROM meal_inventory m
                 JOIN recipes r ON r.recipe_id=m.recipe_id
             GROUP BY r.recipe_id, r.name
             ORDER BY portions, r.name"""
        ).fetchall()
        alerts = []
        for row in rows:
            if restock_due(c, row["recipe_id"], row["portions"], threshold, d):
                alerts.append({
                    "recipe_id": row["recipe_id"],
                    "recipe": row["name"],
                    "portions_left": row["portions"],
                    "threshold": threshold,
                    "prepped_on": row["latest_batch"],
                })
        return {"date": d, "threshold": threshold, "alerts": alerts,
             "count": len(alerts)}
    finally:
        c.close()


def restock_mark(context, a):
    """Persist a delivered restock prompt or the user's selected response."""
    if a.action not in RESTOCK_ACTIONS:
        raise SystemExit(f"action must be one of: {', '.join(sorted(RESTOCK_ACTIONS))}")
    threshold = restock_threshold(a.threshold)
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    c = runtime.connect(context.database)
    try:
        restock_state_ready(c)
        selected = recipe(c, a.recipe)
        if not selected:
            raise SystemExit(f"recipe not found: {a.recipe}")
        inventory = c.execute(
            """SELECT COALESCE(SUM(portions_remaining), 0) AS portions
                 FROM meal_inventory WHERE recipe_id=?""",
            (selected["recipe_id"],),
        ).fetchone()
        snooze_until = a.snooze_until
        if a.action == "later" and not snooze_until:
            snooze_until = (date.fromisoformat(d) + timedelta(days=3)).isoformat()
        if snooze_until:
            snooze_until = valid_date(snooze_until, "--snooze-until")
        c.execute(
            """INSERT INTO recipe_restock_state
                 (recipe_id,action,threshold,portions_at_notice,snooze_until,updated_at)
                 VALUES(?,?,?,?,?,datetime('now'))
                 ON CONFLICT(recipe_id) DO UPDATE SET
                   action=excluded.action,
                   threshold=excluded.threshold,
                   portions_at_notice=excluded.portions_at_notice,
                   snooze_until=excluded.snooze_until,
                   updated_at=datetime('now')""",
            (selected["recipe_id"], a.action, threshold,
             inventory["portions"], snooze_until),
        )
        c.commit()
        return {
            "ok": True,
            "recipe_id": selected["recipe_id"],
            "recipe": selected["name"],
            "action": a.action,
            "portions_left": inventory["portions"],
            "threshold": threshold,
            "snooze_until": snooze_until,
        }
    finally:
        c.close()
