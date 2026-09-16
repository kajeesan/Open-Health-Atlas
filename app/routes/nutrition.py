"""Nutrition: freezer menu (one-tap eat), log by grams, today's macros.

Reads that need health.py's deterministic per-portion math (menu) go through
the bridge; display reads (today's log, recipe list) via db_read. Writes
(eat / log-food) go through the bridge — health.py does the inventory
decrement + macro math and its confirmation is echoed back.
"""
from datetime import date
import json
import os
import re

from flask import Blueprint, current_app, jsonify, request

from app import bridge, canon, db_read

bp = Blueprint("nutrition", __name__, url_prefix="/api/nutrition")

# Whitelisted range windows for the Intake tab's header range dd
# (Day/Week/Month/Year/All time → these values). Same whitelist-and-validate
# style as dash.py's ALLOWED_DAYS: only fixed literals reach SQL, never a
# user int. "all" drops the lower bound.
INTAKE_ALLOWED_DAYS = {"1", "7", "30", "365", "all"}
MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}


def _food_optional_args(body):
    """Validate nullable occurrence metadata; provenance is server-owned."""
    args = []
    d = body.get("date")
    if d is not None:
        try:
            parsed = date.fromisoformat(d)
        except (TypeError, ValueError):
            return None, "date must be ISO YYYY-MM-DD"
        if parsed.isoformat() != d:
            return None, "date must be ISO YYYY-MM-DD"
        args.extend(["--date", d])
    time_value = body.get("time")
    if time_value is not None:
        if not isinstance(time_value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value):
            return None, "time must be HH:MM"
        args.extend(["--time", time_value])
    meal_type = body.get("meal_type")
    if meal_type is not None:
        if not isinstance(meal_type, str) or meal_type not in MEAL_TYPES:
            return None, "meal_type must be breakfast/lunch/dinner/snack"
        args.extend(["--meal-type", meal_type])
    args.extend(["--source", "panel-ui"])
    return args, None


def _run(*args):
    try:
        return jsonify(ok=True, result=bridge.run(*args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


def _guard():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    return None


def _engine(subcmd, *args, timeout=30.0):
    """Read-only engine passthrough (same shape as insights.py's `_engine`):
    the JSON comes back untouched, including an `insufficient_data` refusal —
    that is a normal, valid reply here (e.g. no `owner_profile` row yet), not
    a bridge error, so the client renders the honest 'profile not set' state
    itself rather than the panel guessing at one."""
    try:
        return jsonify(ok=True, result=bridge.run(subcmd, *args, timeout=timeout))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/targets")
def targets():
    """T44 `nutrition-targets` engine passthrough — pure compute, zero flags."""
    return _engine("nutrition-targets")


# T46: nutrition-coverage's own days whitelist — health.py's argparse
# `choices` is the real gate; this is defense in depth, same idiom as
# INTAKE_ALLOWED_DAYS above (copied from /water's validation).
COVERAGE_ALLOWED_DAYS = {"7", "30", "90", "365"}


@bp.get("/coverage")
def coverage():
    """T46 `nutrition-coverage` engine passthrough — per-day target-coverage
    rows (protein/kcal/micros hit vs the T44 targets), the SAME scorer that
    feeds the dashboard ring's nutrition score. `insufficient_data` is a
    normal 200 here too (see _engine's docstring)."""
    days = request.args.get("days", "7")
    if days not in COVERAGE_ALLOWED_DAYS:
        return jsonify(error="days must be one of 7/30/90/365"), 400
    return _engine("nutrition-coverage", "--days", days)


@bp.get("/menu")
def menu():
    """Freezer inventory with per-portion kcal/protein (health.py math)."""
    return _run("menu")


@bp.get("/recipes")
def recipes():
    """Recipe list including the Migration 001-owned meal_type field.

    SELECT * and ``.get`` preserve rollback compatibility without doing DDL.
    """
    if (resp := _guard()) is not None:
        return resp
    rows = db_read.query("SELECT * FROM recipes ORDER BY name")
    return jsonify(recipes=[
        {"recipe_id": r["recipe_id"], "name": r["name"], "batch_grams": r["batch_grams"],
         "grams_per_portion": r["grams_per_portion"], "meal_type": r.get("meal_type")}
        for r in rows])


# recipe_nutrients stores every nutrient by name; these four are the macros the
# detail page renders as its "MACROS / PORTION" bars + calorie stat. Anything
# else on a recipe is a micronutrient (none are seeded on the demo DB → the
# detail page's micro card is honest-empty). Kept here so the split is one
# vocabulary the route owns, not scattered magic strings.
_MACRO_NUTRIENTS = ("Energy", "Protein", "Carbs", "Fat")
_MACRO_ORDER = {"Protein": 0, "Carbs": 1, "Fat": 2}


def _recipe_ingredients(rid):
    """Read one validated health-vault ingredient sidecar without writing.

    Missing sidecars are a normal legacy state. Corrupt or mismatched files
    fail closed and never leak arbitrary vault content into the response.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,159}", rid or ""):
        return [], "invalid"
    vault = current_app.config.get("VAULT_DIR")
    if not vault:
        return [], "missing"
    vault = os.path.abspath(vault)
    target = os.path.abspath(os.path.join(
        vault, "personal", "recipes", f"{rid}.ingredients.json"))
    if os.path.commonpath([target, vault]) != vault:
        return [], "invalid"
    try:
        if os.path.getsize(target) > 100_000:
            return [], "invalid"
        with open(target, encoding="utf-8") as f:
            document = json.load(f)
    except FileNotFoundError:
        return [], "missing"
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], "invalid"
    if not isinstance(document, dict) or document.get("recipe_id") != rid:
        return [], "invalid"
    items = document.get("ingredients")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        return [], "invalid"
    allowed = {"name", "amount", "unit", "weight_g", "note"}
    for item in items:
        if (not isinstance(item, dict) or set(item) - allowed
                or not isinstance(item.get("name"), str)):
            return [], "invalid"
    return items, "ok"


@bp.get("/recipe/<rid>")
def recipe_detail(rid):
    """One recipe's per-portion breakdown for the detail sub-page (T33):
    macros (real), any micronutrients that genuinely exist, and the current
    freezer count. Read-only via db_read; 404 on an unknown id so a bad slug
    never renders a dead shell. No new bridge subcommand — recipe_nutrients
    holds the RECIPE TOTAL in per_gram (see health.py _compute_nutrients), so
    per-portion = total / batch_grams * grams_per_portion. When batch_grams
    is unset the per-portion math can't run → amounts come back null and the
    page renders the honest 'macros pending — batch weight not set' state."""
    if (resp := _guard()) is not None:
        return resp
    rows = db_read.query(
        "SELECT recipe_id, name, batch_grams, grams_per_portion, portions"
        " FROM recipes WHERE recipe_id = ?", (rid,))
    if not rows:
        return jsonify(error="recipe not found"), 404
    r = rows[0]
    freezer = db_read.query(
        "SELECT COALESCE(SUM(portions_remaining), 0) AS n FROM meal_inventory"
        " WHERE recipe_id = ?", (rid,))[0]["n"]
    nutr = db_read.query(
        "SELECT nutrient, unit, per_gram FROM recipe_nutrients WHERE recipe_id = ?", (rid,))

    bg, gpp = r["batch_grams"], r["grams_per_portion"]

    def per_portion(total):
        return round(total / bg * gpp, 1) if bg and gpp else None

    kcal = None
    macros, micros = [], []
    for n in nutr:
        row = {"name": n["nutrient"], "amount": per_portion(n["per_gram"]), "unit": n["unit"]}
        if n["nutrient"] == "Energy":
            kcal = row["amount"]
        elif n["nutrient"] in _MACRO_NUTRIENTS:
            macros.append(row)
        else:
            micros.append(row)
    macros.sort(key=lambda m: _MACRO_ORDER.get(m["name"], 9))
    ingredients, ingredients_status = _recipe_ingredients(rid)
    return jsonify(
        recipe={"recipe_id": r["recipe_id"], "name": r["name"], "batch_grams": bg,
                "grams_per_portion": gpp, "portions": r["portions"]},
        portions_remaining=freezer, kcal_per_portion=kcal,
        macros=macros, micronutrients=micros,
        ingredients=ingredients, ingredients_status=ingredients_status)


# ---- T47: "Closes today's gaps" ranking -------------------------------------
# recipe_nutrients stores Cronometer's display vocabulary ("Energy",
# "Vitamin D", ...); the T44 targets engine speaks MICRO_SEED keys. Only the
# names below participate in the gap ranking — Carbs/Fat ride inside kcal
# (derived remainders, no band of their own), and omega-3 would need the
# EPA+DHA summing the recipe export doesn't carry — deliberately not scored.
_GAP_NAME_TO_KEY = {
    "energy": "kcal", "protein": "protein_g", "vitamin d": "vitamin_d",
    "magnesium": "magnesium", "iron": "iron", "zinc": "zinc",
    "calcium": "calcium", "potassium": "potassium", "vitamin c": "vitamin_c",
    "vitamin b12": "vitamin_b12", "b12": "vitamin_b12",
    "folate": "folate", "folate dfe": "folate",
}
# human names for the `closes:` line (target keys -> display)
_GAP_LABEL = {
    "kcal": "kcal", "protein_g": "protein", "vitamin_d": "vitamin D",
    "magnesium": "magnesium", "iron": "iron", "zinc": "zinc",
    "calcium": "calcium", "potassium": "potassium", "vitamin_c": "vitamin C",
    "vitamin_b12": "vitamin B12", "folate": "folate",
}

# Canonical target units for micronutrients that can be reconstructed from a
# logged recipe. recipe_nutrients uses Cronometer's display names and stores
# the recipe total; nutrition_log retains the recipe id + grams eaten. EPA and
# DHA are deliberately summed into the EPA+DHA target rather than using the
# broader Omega-3 total, which also includes ALA.
_RECIPE_MICRO_SPECS = {
    "vitamin_d": ({"vitamin d"}, "ug"),
    "magnesium": ({"magnesium"}, "mg"),
    "iron": ({"iron"}, "mg"),
    "zinc": ({"zinc"}, "mg"),
    "vitamin_b12": ({"vitamin b12", "b12", "b12 (cobalamin)"}, "ug"),
    "calcium": ({"calcium"}, "mg"),
    "potassium": ({"potassium"}, "mg"),
    "vitamin_c": ({"vitamin c"}, "mg"),
    "folate": ({"folate", "folate dfe"}, "ug"),
    "omega3_epa_dha": ({"epa", "dha"}, "mg"),
}
_RECIPE_MICRO_NAME_TO_KEY = {
    name: key
    for key, (names, _unit) in _RECIPE_MICRO_SPECS.items()
    for name in names
}


def _gap_factor(key, unit, target_unit):
    """Convert a recipe nutrient's unit into the target's unit, or None when
    no safe conversion exists (a skipped nutrient is honest; a wrong-unit %
    would be fabrication). Mirrors health.py MICRO_SEED's unit tables:
    ug==µg==mcg, g->mg x1000, and Cronometer's one IU column (vitamin D,
    40 IU = 1 µg)."""
    norm = {"µg": "ug", "mcg": "ug"}
    u = (unit or "").strip().lower()
    u = norm.get(u, u)
    t = norm.get((target_unit or "").strip().lower(), target_unit)
    if u == t:
        return 1.0
    if u == "g" and t == "mg":
        return 1000.0
    if key == "vitamin_d" and u == "iu" and t == "ug":
        return 0.025
    return None


def _recipe_micros_for_day(day):
    """Derive canonical micronutrient totals from recipe-backed food logs.

    This is a read-time fallback: no historical rows are rewritten. A recipe
    nutrient is its whole-batch total, so each contribution is
    total / batch_grams * grams_logged. Unknown names or unsafe unit
    conversions are skipped rather than guessed.
    """
    rows = db_read.query(
        "SELECT nl.grams, r.batch_grams, rn.nutrient, rn.unit, rn.per_gram"
        " FROM nutrition_log nl"
        " JOIN recipes r ON r.recipe_id = nl.recipe_id"
        " JOIN recipe_nutrients rn ON rn.recipe_id = nl.recipe_id"
        " WHERE nl.date = ? AND nl.grams IS NOT NULL"
        " AND r.batch_grams IS NOT NULL AND r.batch_grams > 0"
        " AND rn.per_gram IS NOT NULL",
        (day,))
    totals = {}
    for row in rows:
        name = (row["nutrient"] or "").strip().lower()
        key = _RECIPE_MICRO_NAME_TO_KEY.get(name)
        if key is None:
            continue
        target_unit = _RECIPE_MICRO_SPECS[key][1]
        factor = _gap_factor(key, row["unit"], target_unit)
        if factor is None:
            continue
        contribution = (
            row["per_gram"] / row["batch_grams"] * row["grams"] * factor
        )
        totals[key] = totals.get(key, 0.0) + contribution
    return {
        key: {"amount": round(amount, 3), "unit": _RECIPE_MICRO_SPECS[key][1]}
        for key, amount in totals.items()
    }


@bp.get("/recipe-gaps")
def recipe_gaps():
    """Rank freezer-able recipes by how much one portion closes today's
    remaining nutrient deficits (T47 — backs the Recipes tab's "Closes
    today's gaps" pill). Read-composition ONLY, the /api/training/
    athletic-detail precedent: bridge `nutrition-targets` (the sole target
    authority) + db_read for today's consumed totals and recipe_nutrients —
    no new bridge subcommand, no write path.

    Score = Σ over targeted nutrients of min(per_portion_amount, deficit) /
    target, deficit = max(0, target − consumed-so-far). This is a
    PRESENTATION ranking (a normalized "which recipe helps most right now"
    ordering), NOT a clinical adequacy claim. It is deliberately computed
    fresh per request so deficits shrink as meals are logged through the day.
    Recipes without batch_grams+grams_per_portion are omitted (per-portion
    math can't run — honest omission, never a fabricated portion). When the
    targets engine reports anything but ok, the insufficient_data shape
    passes through and the client renders the honest no-ranking state."""
    if (resp := _guard()) is not None:
        return resp
    try:
        tg = bridge.run("nutrition-targets")
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502
    if tg.get("status") != "ok":
        return jsonify(ok=True, result={
            "status": "insufficient_data",
            "reason": tg.get("reason") or "targets engine has no profile yet"})

    # target per nutrient key -> (target, unit); micros with a null target
    # (fibre) are skipped — a target nothing emits would be fabrication.
    targets = {"kcal": (tg["targets"]["kcal"]["target"], "kcal"),
               "protein_g": (tg["targets"]["protein_g"]["target"], "g")}
    for m in tg["targets"].get("micros") or []:
        if m.get("target"):
            targets[m["nutrient"]] = (m["target"], m.get("unit") or "")

    # consumed today: summed log macros + per-nutrient MAX across Cronometer
    # sources (the same provenance rule /today documents), keyed like targets
    today_iso = canon.today_iso()
    tot = db_read.query(
        "SELECT COALESCE(SUM(kcal), 0) AS kcal, COALESCE(SUM(protein_g), 0) AS protein_g"
        " FROM nutrition_log WHERE date = ?", (today_iso,))[0]
    consumed = {"kcal": tot["kcal"], "protein_g": tot["protein_g"]}
    if db_read.query(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nutrient_daily'"):
        for r in db_read.query(
                "SELECT nutrient, MAX(amount) AS amount FROM nutrient_daily"
                " WHERE date = ? GROUP BY nutrient", (today_iso,)):
            consumed[r["nutrient"]] = r["amount"]
    deficits = {k: max(0.0, t - (consumed.get(k) or 0.0)) for k, (t, _u) in targets.items()}

    nutr_by_recipe = {}
    for n in db_read.query("SELECT recipe_id, nutrient, unit, per_gram FROM recipe_nutrients"):
        nutr_by_recipe.setdefault(n["recipe_id"], []).append(n)
    ranked = []
    for r in db_read.query(
            "SELECT recipe_id, batch_grams, grams_per_portion FROM recipes"
            " WHERE batch_grams IS NOT NULL AND grams_per_portion IS NOT NULL"):
        contribs = []
        for n in nutr_by_recipe.get(r["recipe_id"], []):
            key = _GAP_NAME_TO_KEY.get((n["nutrient"] or "").strip().lower())
            if key is None or key not in targets:
                continue
            target, tunit = targets[key]
            factor = _gap_factor(key, n["unit"], tunit)
            if factor is None or n["per_gram"] is None:
                continue
            # per_gram holds the RECIPE TOTAL (health.py convention) ->
            # per-portion = total / batch_grams * grams_per_portion
            per_portion = n["per_gram"] / r["batch_grams"] * r["grams_per_portion"] * factor
            contribs.append((min(per_portion, deficits[key]) / target, key))
        if not contribs:
            continue
        closes = [_GAP_LABEL[k] for c, k in
                  sorted(contribs, key=lambda x: (-x[0], _GAP_LABEL[x[1]])) if c > 0][:3]
        ranked.append({"recipe_id": r["recipe_id"],
                       "score": round(sum(c for c, _k in contribs), 3),
                       "closes": closes})
    ranked.sort(key=lambda x: (-x["score"], x["recipe_id"]))
    return jsonify(ok=True, result={"status": "ok", "recipes": ranked})


@bp.get("/today")
def today():
    """Today's logged nutrition + totals, on the configured canonical day
    (matches the date health.py stamps on eat/log-food writes).

    `micros` uses a daily Cronometer total when that nutrient was imported.
    Missing nutrients fall back to deterministic totals reconstructed from
    recipe-backed nutrition_log rows. The fallback is per nutrient, never
    added to a Cronometer daily total, so a full export cannot double count
    foods. `nutrient_daily`'s PK is (date, nutrient, source), so the imported
    side takes the per-nutrient MAX across sources, matching the provenance
    rule used for daily_metrics elsewhere in the app."""
    if (resp := _guard()) is not None:
        return resp
    today_iso = canon.today_iso()
    rows = db_read.query(
        "SELECT food_name, grams, kcal, protein_g, carbs_g, fat_g, fiber_g"
        " FROM nutrition_log WHERE date = ? ORDER BY id", (today_iso,))
    totals = {k: round(sum(r[k] or 0 for r in rows), 1)
              for k in ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g")}
    # Migration 001 and the final schema own nutrient_daily. The existence
    # check retains rollback compatibility and remains read-only.
    have_nutrient_daily = db_read.query(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nutrient_daily'")
    micros = _recipe_micros_for_day(today_iso)
    if have_nutrient_daily:
        micro_rows = db_read.query(
            "SELECT nutrient, unit, MAX(amount) AS amount FROM nutrient_daily"
            " WHERE date = ? GROUP BY nutrient, unit", (today_iso,))
        # Imported daily totals are authoritative for that nutrient. Assign
        # rather than add: Cronometer already includes the consumed recipes.
        for r in micro_rows:
            micros[r["nutrient"]] = {"amount": r["amount"], "unit": r["unit"]}
    return jsonify(entries=rows, totals=totals, micros=micros)


@bp.get("/water")
def water():
    """Hydration trend for the Nutrition page's Water card (design gauge +
    trend) — plain db_read over `intake`, windowed by the header range dd's
    whitelisted `days`. No target lives in this table; the panel fetches the
    real engine target separately from /api/dash/scores
    (scores.water.inputs.target_ml, health.py's WATER_TARGET_ML) rather than
    hardcoding one here."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "7")
    if rng not in INTAKE_ALLOWED_DAYS:
        return jsonify(error="days must be one of 1/7/30/365/all"), 400
    if rng == "all":
        rows = db_read.query("SELECT date, water_ml FROM intake ORDER BY date")
    else:
        rows = db_read.query(
            "SELECT date, water_ml FROM intake WHERE date >= ? ORDER BY date",
            (canon.days_ago_iso(int(rng)),))
    return jsonify(rows=rows)


@bp.get("/calories")
def calories():
    """Daily total kcal from the food log over the header range dd's
    whitelisted window, for the Intake tab's Calories card trend. Sums
    nutrition_log per day — these rows are additive line-items (distinct
    foods), so SUM across a day is the real intake; the per-source provenance
    caveat that applies to daily_metrics does not apply here. No calorie
    target exists yet, so this returns intake only; the honest 'no target'
    gauge state is rendered client-side, never a fabricated %."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "7")
    if rng not in INTAKE_ALLOWED_DAYS:
        return jsonify(error="days must be one of 1/7/30/365/all"), 400
    where, params = ("", ()) if rng == "all" else (
        "WHERE date >= ?", (canon.days_ago_iso(int(rng)),))
    rows = db_read.query(
        f"SELECT date, ROUND(SUM(kcal), 0) AS kcal FROM nutrition_log {where}"
        " GROUP BY date ORDER BY date", params)
    return jsonify(rows=rows)


@bp.get("/supplements")
def supplements():
    """Active supplement products + today's taken status for the Supplements
    tab's list card. `notes` is included so the Interactions & notes card can
    render each product's own logged note (real content, never a fabricated
    interaction fact) — unseeded on the demo DB → honest-empty until the user
    logs supplements."""
    if (resp := _guard()) is not None:
        return resp
    products = db_read.query(
        "SELECT supplement_id, name, brand, dose, unit, form, schedule, notes"
        " FROM supplement_products WHERE active = 1 ORDER BY name")
    taken_today = db_read.query(
        # Each row is an observation, not a correction of the preceding row.
        # Any explicit taken observation confirms intake that day; NULL stays
        # unknown when there are no explicit taken/not-taken observations.
        "SELECT supplement_id, MAX(CASE WHEN taken IN (0, 1) THEN taken END) AS taken"
        " FROM supplements_log WHERE date = ? GROUP BY supplement_id",
        (canon.today_iso(),))
    return jsonify(products=products, today=taken_today)


@bp.get("/supplements/adherence")
def supplements_adherence():
    """Taken-vs-planned adherence trend for the Supplements tab's Adherence
    card, windowed by the page header dd (whitelisted `days`, same values as
    /calories and /water).

    Formula: for each day in the window,
        pct = (# distinct ACTIVE supplements logged taken that day)
              / (# currently-active supplement_products) * 100
    capped at 100. Both sides of the fraction are filtered to CURRENTLY
    active products: the numerator joins supplements_log to
    supplement_products and requires active = 1, so a retired product's
    historical taken=1 rows can't inflate a day's count against the
    active-only denominator. The denominator is today's active-product
    COUNT — supplement_products carries no history table, so a product
    added or deactivated mid-window isn't reflected day-by-day; this is a
    plain approximation, not a precise historical planned-count (documented
    rather than hidden). Explicit not-taken days remain at zero; NULL-only
    days stay unknown. Honest-empty when there are no active products
    or no explicit taken/not-taken observations in the window."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "7")
    if rng not in INTAKE_ALLOWED_DAYS:
        return jsonify(error="days must be one of 1/7/30/365/all"), 400
    active = db_read.query(
        "SELECT COUNT(*) AS n FROM supplement_products WHERE active = 1")[0]["n"]
    if not active:
        return jsonify(rows=[], active_products=0)
    if rng == "all":
        rows = db_read.query(
            "SELECT sl.date AS date,"
            " COUNT(DISTINCT CASE WHEN sl.taken = 1 THEN sl.supplement_id END) AS taken"
            " FROM supplements_log sl JOIN supplement_products sp"
            " ON sp.supplement_id = sl.supplement_id"
            " WHERE sl.taken IN (0, 1) AND sp.active = 1 GROUP BY sl.date ORDER BY sl.date")
    else:
        rows = db_read.query(
            "SELECT sl.date AS date,"
            " COUNT(DISTINCT CASE WHEN sl.taken = 1 THEN sl.supplement_id END) AS taken"
            " FROM supplements_log sl JOIN supplement_products sp"
            " ON sp.supplement_id = sl.supplement_id"
            " WHERE sl.taken IN (0, 1) AND sp.active = 1 AND sl.date >= ?"
            " GROUP BY sl.date ORDER BY sl.date",
            (canon.days_ago_iso(int(rng)),))
    pct_rows = [{"date": r["date"], "pct": min(100.0, round(r["taken"] / active * 100, 1))}
                for r in rows]
    return jsonify(rows=pct_rows, active_products=active)


@bp.post("/eat")
def eat():
    b = request.get_json(silent=True) or {}
    if set(b) - {"recipe", "portions", "date", "time", "meal_type"}:
        return jsonify(ok=False, error="unknown food field"), 400
    recipe = (b.get("recipe") or "").strip()
    if not recipe or recipe.startswith("-"):
        return jsonify(ok=False, error="a valid recipe is required"), 400
    try:
        portions = int(b.get("portions", 1))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="portions must be a number"), 400
    if not (1 <= portions <= 10):
        return jsonify(ok=False, error="portions must be 1–10"), 400
    optional, error = _food_optional_args(b)
    if error:
        return jsonify(ok=False, error=error), 400
    return _run("eat", recipe, "--portions", str(portions), *optional)


@bp.post("/log-food")
def log_food():
    b = request.get_json(silent=True) or {}
    if set(b) - {"recipe", "grams", "date", "time", "meal_type"}:
        return jsonify(ok=False, error="unknown food field"), 400
    recipe = (b.get("recipe") or "").strip()
    if not recipe or recipe.startswith("-"):
        return jsonify(ok=False, error="a valid recipe is required"), 400
    try:
        grams = float(b.get("grams"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="grams must be a number"), 400
    if not (1 <= grams <= 5000):
        return jsonify(ok=False, error="grams must be 1–5000"), 400
    optional, error = _food_optional_args(b)
    if error:
        return jsonify(ok=False, error=error), 400
    return _run("log-food", recipe, "--grams", str(grams), *optional)


@bp.post("/prep")
def prep():
    """Log a meal-prep batch → adds portions to the freezer (health.py `prep`,
    already on both bridge allowlists — NO new subcommand). This is the ONLY
    write that raises inventory, so it backs both the Recipes tab's
    '+ log a batch' form and each card's '+' portion stepper (a single-portion
    prep). health.py does the inventory insert + optional batch-weight update;
    its confirmation echoes back."""
    b = request.get_json(silent=True) or {}
    recipe = (b.get("recipe") or "").strip()
    if not recipe or recipe.startswith("-"):
        return jsonify(ok=False, error="a valid recipe is required"), 400
    try:
        portions = int(b.get("portions", 1))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="portions must be a number"), 400
    if not (1 <= portions <= 20):
        return jsonify(ok=False, error="portions must be 1–20"), 400
    args = [recipe, "--portions", str(portions)]
    bg = b.get("batch_grams")
    if bg not in (None, ""):
        try:
            bgf = float(bg)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="batch_grams must be a number"), 400
        if not (1 <= bgf <= 20000):
            return jsonify(ok=False, error="batch_grams must be 1–20000"), 400
        args += ["--batch-grams", str(bgf)]
    return _run("prep", *args)
