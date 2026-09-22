"""Profile targets and nutrition coverage with explicit configuration and clocks."""

from dataclasses import dataclass
from datetime import date
import json
import math
import statistics as st

from . import calculations, runtime
from .catalogs import MICRO_KEYS, MICRO_SEED
from .runtime import table_exists
from .score_contracts import band, clamp100


@dataclass(frozen=True)
class NutritionConfig:
    """Carry startup-resolved target settings without reading ambient configuration."""

    target_seed: dict
    micro_targets: dict
    phase_offsets: dict
    protein_per_kg: dict
    micro_seed: list
    micro_target_extra: list
    water_target_ml: float


OWNER_PROFILE_KEYS = {"height_cm", "sex", "dob", "activity_fallback"}

SEX_VALUES = {"male", "female"}

ACTIVITY_FALLBACK_VALUES = {"sedentary", "light", "moderate", "active", "very_active"}

DIET_PHASES = {"cut", "maintain", "bulk"}

# EPA and DHA sum into their specific target; the broader Omega-3 total
# includes ALA and cannot substitute for it.
RECIPE_MICRO_NAME_TO_KEY = {
    "vitamin d": "vitamin_d",
    "magnesium": "magnesium",
    "epa": "omega3_epa_dha",
    "dha": "omega3_epa_dha",
    "zinc": "zinc",
    "iron": "iron",
    "vitamin b12": "vitamin_b12",
    "b12": "vitamin_b12",
    "b12 (cobalamin)": "vitamin_b12",
    "calcium": "calcium",
    "potassium": "potassium",
    "vitamin c": "vitamin_c",
    "folate": "folate",
    "folate dfe": "folate",
}

RECIPE_MICRO_UNITS = {m["key"]: m["unit"] for m in MICRO_SEED}

# Editorial heuristic: protein 25 + kcal 15 + ten micros at 6 = 100.
NUTRITION_WEIGHTS = {"protein": 25, "kcal": 15, "micro_each": 6}

NUTRITION_TARGET_NAMES = {"protein_g", "kcal"}


def configured_positive_number(name, default, *, environ):
    """Resolve one positive startup number from the supplied environment."""
    raw = environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


def configured_phase_numbers(name, *, environ):
    """Resolve all three phase values, or retain absent configuration."""
    raw = environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be valid JSON") from exc
    if not isinstance(values, dict) or set(values) != DIET_PHASES:
        raise RuntimeError(f"{name} must define cut, maintain, and bulk")
    configured = {}
    for key, value in values.items():
        if isinstance(value, bool):
            raise RuntimeError(f"{name} values must be finite numbers")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{name} values must be finite numbers") from exc
        if not math.isfinite(number):
            raise RuntimeError(f"{name} values must be finite numbers")
        configured[key] = number
    return configured


def target_seed(*, phase_offsets, protein_per_kg, water_ml_per_kg,
                water_ml_per_exercise_hour, water_hot_day_bonus_ml, water_hot_day_temp_c):
    """Build the existing cited target definitions from resolved settings."""
    return {
        "bmr": {
            "formula": "male: 10*kg + 6.25*cm - 5*age + 5; female: same - 161 instead of +5",
            "source": "Mifflin-St Jeor 1990 (Am J Clin Nutr 51:241-247) resting energy equation"},
        "activity_factors": {
            "values": {"sedentary": 1.2, "light": 1.375, "moderate": 1.55,
                       "active": 1.725, "very_active": 1.9},
            "source": "standard TDEE activity multipliers (Harris-Benedict/Mifflin convention)"},
        "phase_offset_kcal": {
            "values": phase_offsets,
            "source": "installation-configured phase offset"},
        "protein_g_per_kg": {
            "values": protein_per_kg,
            "source": "installation-configured protein target"},
        "fat_pct_kcal": {
            "value": 0.25,
            "source": "25% of kcal — within the 20-35% acceptable range (position-stand "
                      "convention); kept above ~20% as the hormonal-health floor"},
        "kcal_band_pct": {
            "value": 0.10,
            "source": "+/-10% band — matches the existing nutrition-score kcal credit band"},
        "water": {
            "ml_per_kg": water_ml_per_kg,
            "ml_per_exercise_hour": water_ml_per_exercise_hour,
            "hot_day_bonus_ml": water_hot_day_bonus_ml,
            "hot_day_temp_c": water_hot_day_temp_c,
            "source": "installation-configured water heuristic"},
    }


def recipe_micro_factor(key, unit):
    """Convert one recipe nutrient unit to its canonical MICRO_SEED unit."""
    norm = {"µg": "ug", "mcg": "ug"}
    source = norm.get((unit or "").strip().lower(),
                      (unit or "").strip().lower())
    target = norm.get(RECIPE_MICRO_UNITS[key].lower(),
                      RECIPE_MICRO_UNITS[key].lower())
    if source == target:
        return 1.0
    if source == "g" and target == "mg":
        return 1000.0
    if key == "vitamin_d" and source == "iu" and target == "ug":
        return 0.025
    return None


def day_values(c, d):
    """Compose one day's score inputs without double counting.

    Logged kcal/protein come from nutrition_log. Recipe micronutrients are
    reconstructed from recipe total / batch grams * grams eaten. A
    nutrient_daily value then replaces the corresponding fallback value
    nutrient-by-nutrient because a Cronometer daily total already includes
    those foods.
    """
    values = {}
    if table_exists(c, "nutrition_log"):
        macro = c.execute(
            "SELECT SUM(kcal) kcal, SUM(protein_g) protein_g"
            " FROM nutrition_log WHERE date=?", (d,)).fetchone()
        if macro:
            if macro["kcal"] is not None:
                values["energy_kcal"] = macro["kcal"]
            if macro["protein_g"] is not None:
                values["protein_g"] = macro["protein_g"]

    if all(table_exists(c, name)
           for name in ("nutrition_log", "recipes", "recipe_nutrients")):
        rows = c.execute(
            """SELECT nl.grams, r.batch_grams, rn.nutrient, rn.unit,
                      rn.per_gram
                 FROM nutrition_log nl
                 JOIN recipes r ON r.recipe_id=nl.recipe_id
                 JOIN recipe_nutrients rn ON rn.recipe_id=nl.recipe_id
                WHERE nl.date=? AND nl.grams IS NOT NULL
                  AND r.batch_grams IS NOT NULL AND r.batch_grams>0
                  AND rn.per_gram IS NOT NULL""", (d,))
        for row in rows:
            key = RECIPE_MICRO_NAME_TO_KEY.get(
                (row["nutrient"] or "").strip().lower())
            if key is None:
                continue
            factor = recipe_micro_factor(key, row["unit"])
            if factor is None:
                continue
            amount = (row["per_gram"] / row["batch_grams"]
                      * row["grams"] * factor)
            values[key] = values.get(key, 0.0) + amount

    if table_exists(c, "nutrient_daily"):
        for row in c.execute(
                "SELECT nutrient, MAX(amount) amount FROM nutrient_daily"
                " WHERE date=? GROUP BY nutrient", (d,)):
            values[row["nutrient"]] = row["amount"]
    return values


def day_score(c, d, targets):
    """Shared per-day §4a scorer (T46).

    Scores the composed daily values from `day_values` against the
    same T44 targets used by nutrition-targets. Cronometer daily totals win
    per nutrient; logged recipe values fill only missing nutrients. The
    weights/credit shapes remain the original composite. Returns None only
    when neither source contains scoreable nutrition for the day.
    """
    nd = day_values(c, d)
    if not nd:
        return None
    w = NUTRITION_WEIGHTS
    kcal_t = targets["kcal"]["target"]
    protein_t = targets["protein_g"]["target"]

    protein = nd.get("protein_g")
    protein_frac = min(1.0, protein / protein_t) if protein is not None and protein_t else 0.0
    protein_part = w["protein"] * protein_frac

    kcal = nd.get("energy_kcal")
    if kcal is None or not kcal_t:
        kcal_part = 0.0
    else:
        off = abs(kcal - kcal_t) / kcal_t
        kcal_part = w["kcal"] * max(0.0, min(1.0, (0.25 - off) / 0.15))

    micros, low = [], []
    for m in targets["micros"]:
        if m["target"] is None:
            continue   # e.g. fibre — no Cronometer alias, nothing to score against
        amt = nd.get(m["nutrient"])
        frac = min(1.0, amt / m["target"]) if amt is not None else 0.0
        micros.append({"key": m["nutrient"], "amount": amt, "unit": m["unit"],
                       "target": m["target"], "pct": round(100 * frac),
                       "source": m["source"]})
        if frac < 0.8:
            low.append(m["nutrient"])
    micros_part = w["micro_each"] * sum(mi["pct"] for mi in micros) / 100.0

    score = clamp100(protein_part + kcal_part + micros_part)
    return {
        "score": score, "band": band(score),
        "components": {
            "protein_g": protein, "protein_target": protein_t,
            "protein_pct": round(100 * protein / protein_t) if protein is not None and protein_t else None,
            "kcal": kcal, "kcal_target": kcal_t,
            "kcal_pct": round(100 * kcal / kcal_t) if kcal is not None and kcal_t else None,
            "micros": micros, "low_micros": low,
            "micros_hit": sum(1 for mi in micros if mi["pct"] >= 100),
            "micros_total": len(micros),
        },
    }


def daymax(c, col, lo, hi=None):
    """Per-day MAX of a daily_metrics column across sources (documented
    provenance choice: rows are keyed (date, source) — Apple and Fitbit
    coexist per day, and a silent GROUP BY date would sum/average across
    sources; MAX per day is the deliberate, commented merge here)."""
    q = (f"SELECT date, MAX({col}) v FROM daily_metrics "
         f"WHERE {col} IS NOT NULL AND date>=? ")
    args = [lo]
    if hi is not None:
        q += "AND date<=? "
        args.append(hi)
    return {r["date"]: r["v"] for r in c.execute(q + "GROUP BY date", args)}


def water_target(c, weight_kg, *, clock, config):
    """Configured baseline/exercise/weather heuristic, rounded to 50 ml.
    Uses today's exercise_min / weather max temp, else yesterday's — whichever
    day first has any data; day_used records the choice. Without a weight the
    configured water_target_ml value is the labeled fallback."""
    seed = config.target_seed["water"]
    if weight_kg is None:
        return {"target": config.water_target_ml, "basis": "fallback",
                "components": {"baseline": None, "exercise": None, "weather": None},
                "day_used": None}
    day_used, ex_min, temp_max = runtime.today(clock=clock), 0.0, None
    for back in (0, 1):
        d = runtime.days_ago(back, clock=clock)
        ex = daymax(c, "exercise_min", d, d).get(d)   # per-day MAX across sources
        w = (c.execute("SELECT temp_max_c FROM weather WHERE date=?", (d,)).fetchone()
             if table_exists(c, "weather") else None)
        tm = w["temp_max_c"] if w else None
        if ex is not None or tm is not None:
            day_used, ex_min, temp_max = d, ex or 0.0, tm
            break
    baseline = round(seed["ml_per_kg"] * weight_kg)
    exercise = round(seed["ml_per_exercise_hour"] * ex_min / 60.0)
    weather = (seed["hot_day_bonus_ml"]
               if temp_max is not None and temp_max >= seed["hot_day_temp_c"] else 0)
    return {"target": int(round((baseline + exercise + weather) / 50.0) * 50),
            "basis": seed["source"],
            "components": {"baseline": baseline, "exercise": exercise,
                           "weather": weather},
            "day_used": day_used}


def compute_targets(c, *, clock, config):
    """READ-only compute (T44/T46): owner profile + logged data -> today's
    nutrition targets (pinned JSON contract — T45/T46/T47 build against it).
    Shared by the `nutrition-targets` CLI subcommand, scores()'s water and
    nutrition components, and `nutrition-coverage` — ONE targets source for
    the whole system, no rival computations. No writes: Migration 001 owns
    owner_profile and nutrition_targets; empty migrated tables simply provide
    no configuration rows. Missing inputs -> insufficient_data, never a guess (the
    labeled water fallback is the one allowed exception)."""
    prof = ({r["key"]: r["value"] for r in c.execute("SELECT key, value FROM owner_profile")}
            if table_exists(c, "owner_profile") else {})
    wrow = c.execute("""SELECT date, weight_kg FROM body_metrics
                        WHERE weight_kg IS NOT NULL AND date<=?
                        ORDER BY date DESC, id DESC LIMIT 1""", (runtime.today(clock=clock),)).fetchone()
    weight = wrow["weight_kg"] if wrow else None
    missing = [k for k in ("height_cm", "sex", "dob") if not prof.get(k)]
    if missing or weight is None:
        reason = (f"user profile incomplete — profile-set {'/'.join(missing)}"
                  if missing else
                  "no weight_kg in body_metrics — log a weight first")
        return {"status": "insufficient_data", "reason": reason,
                "targets": {"water_ml": water_target(c, weight, clock=clock, config=config)}}
    missing_micros = sorted(MICRO_KEYS - set(config.micro_targets))
    if missing_micros:
        return {
            "status": "insufficient_data",
            "reason": "nutrient targets are not configured",
            "missing_nutrient_targets": missing_micros,
            "targets": {"water_ml": water_target(c, weight, clock=clock, config=config)},
        }
    if (set(config.phase_offsets) != DIET_PHASES
            or set(config.protein_per_kg) != DIET_PHASES):
        return {
            "status": "insufficient_data",
            "reason": "nutrition phase and protein targets are not configured",
            "targets": {"water_ml": water_target(c, weight, clock=clock, config=config)},
        }

    # profile (owner_profile stores raw TEXT — float() on read)
    height = float(prof["height_cm"])
    sex = prof["sex"]
    dob = date.fromisoformat(prof["dob"])
    t = date.fromisoformat(runtime.today(clock=clock))
    age = t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))

    # activity over the trailing 28 days, derived deterministically
    lo = runtime.days_ago(28, clock=clock)
    hevy_dates = {r["date"] for r in c.execute(
        "SELECT DISTINCT date FROM hevy_sets WHERE date>=? AND date<=?", (lo, runtime.today(clock=clock)))}
    steps = daymax(c, "steps", lo, runtime.today(clock=clock))  # per-day MAX across sources
    ex_days = daymax(c, "exercise_min", lo, runtime.today(clock=clock))  # (provenance rule, see daymax)
    s = len(hevy_dates) / 4.0                 # sessions/week
    st_avg = round(st.mean(steps.values())) if steps else None
    days_with_data = len(hevy_dates | set(steps) | set(ex_days))
    fallback_level = prof.get("activity_fallback") or "moderate"
    if days_with_data < 14:
        level, basis = fallback_level, "fallback"
    else:
        basis = "logged"
        stv = st_avg or 0
        if s >= 6 or (s >= 4 and stv >= 12000):
            level = "very_active"
        elif s >= 4 or (s >= 3 and stv >= 10000):
            level = "active"
        elif s >= 2 or stv >= 8000:
            level = "moderate"
        elif s >= 1 or stv >= 5000:
            level = "light"
        else:
            level = "sedentary"
    factor = config.target_seed["activity_factors"]["values"][level]

    # Mifflin-St Jeor BMR -> maintenance -> phase-adjusted kcal
    bmr = 10 * weight + 6.25 * height - 5 * age + (5 if sex == "male" else -161)
    maintenance = round(bmr * factor)
    phase = prof.get("phase")
    phase_out = {"phase": phase or "maintain",
                 "started": prof.get("phase_started"), "set": phase is not None}
    offset = config.target_seed["phase_offset_kcal"]["values"][phase_out["phase"]]

    # legacy nutrition_targets rows (user's explicit manual setting) OVERRIDE
    # the computed kcal/protein; fat/carbs/band derive from the EFFECTIVE values
    overrides = ({r["name"]: r["target"] for r in
                 c.execute("SELECT name, target FROM nutrition_targets")}
                 if table_exists(c, "nutrition_targets") else {})
    kcal = maintenance + offset
    kcal_t = {"target": kcal,
              "source": (f"{config.target_seed['bmr']['source']} x activity factor "
                         f"({config.target_seed['activity_factors']['source']}); "
                         f"phase offset: {config.target_seed['phase_offset_kcal']['source']}; "
                         f"band: {config.target_seed['kcal_band_pct']['source']}")}
    if overrides.get("kcal") is not None:
        kcal = int(round(overrides["kcal"]))
        kcal_t.update(target=kcal, override=True,
                      source="owner override (nutrition_targets table)")
    band = config.target_seed["kcal_band_pct"]["value"]
    kcal_t["band_low"] = round(kcal * (1 - band))
    kcal_t["band_high"] = round(kcal * (1 + band))

    per_kg = config.target_seed["protein_g_per_kg"]["values"][phase_out["phase"]]
    protein = round(per_kg * weight)
    protein_t = {"target": protein, "per_kg": per_kg,
                 "source": config.target_seed["protein_g_per_kg"]["source"]}
    if overrides.get("protein_g") is not None:
        protein = int(round(overrides["protein_g"]))
        protein_t.update(target=protein, per_kg=round(protein / weight, 2),
                         override=True,
                         source="owner override (nutrition_targets table)")

    fat = round(config.target_seed["fat_pct_kcal"]["value"] * kcal / 9)
    carbs = round((kcal - protein * 4 - fat * 9) / 4)

    return {"status": "ok",
         "profile": {"height_cm": height, "sex": sex, "age": age,
                     "weight_kg": weight, "weight_date": wrow["date"]},
         "phase": phase_out,
         "activity": {"level": level, "factor": factor, "basis": basis,
                      "sessions_per_week": round(s, 2), "avg_steps": st_avg,
                      "days_with_data": days_with_data},
         "maintenance_kcal": maintenance,
         "targets": {
             "kcal": kcal_t,
             "protein_g": protein_t,
             "fat_g": {"target": fat, "pct_kcal": 25,
                       "source": config.target_seed["fat_pct_kcal"]["source"]},
             "carbs_g": {"target": carbs,
                         "source": "remainder: (kcal - protein*4 - fat*9) / 4 "
                                   "(Atwater 4/9/4 kcal per g)"},
             "water_ml": water_target(c, weight, clock=clock, config=config),
             "micros": calculations._micro_targets(micro_seed=config.micro_seed, micro_target_extra=config.micro_target_extra),
         }}


def profile_upsert(c, key, value, *, clock):
    """Update one profile value within the caller's transaction."""
    c.execute("""INSERT INTO owner_profile(key, value, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, runtime.today(clock=clock)))
