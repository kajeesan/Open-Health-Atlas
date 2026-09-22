"""Allowed generic writes and optional owner-entered capture provenance."""

import math

from . import migrations, runtime


LOGGABLE = {
  "meds_log": ["date","drug","dose_mg","time_taken","onset","peak_window","wear_off","rebound","side_effects","notes"],
  "vitals": ["date","time","systolic","diastolic","resting_hr","notes"],
  "body_metrics": ["date","weight_kg","waist_cm","chest_cm","arm_cm","thigh_cm","hip_cm","neck_cm","body_fat_pct","photo_ref","notes"],
  "subjective_daily": ["date","day_rating","focus","energy","mood","emotional_regulation","anxiety","motivation","stress","caffeine_mg","alcohol_units","brain_dump","notes","soreness_note"],
  "sleep_log": ["date","bedtime","wake_time","time_asleep_hours","time_in_bed_hours","deep_min","rem_min","light_min","awake_min","quality","awakenings","notes","provenance"],
  "assessments": ["date","scale","part","score","max_score","subscores","notes"],
  "habits_log": ["date","habit","done","streak","xp","notes"],
  # NB: `labs` is deliberately NOT loggable — the labs table is written ONLY by
  # the validated cited pipeline (lab-ingest/lab-capture against lab_catalog),
  # never by the generic `log` writer (which does no catalog/plausibility check).
  "intake": ["date","water_ml","notes"],
  "supplements_log": ["date","supplement_id","taken","dose_taken","notes"],
  "skincare_log": ["date","slot","product_id","used","notes"],
  "supplement_products": ["name","brand","dose","unit","form","schedule","active","notes"],
  "skincare_products": ["slot","brand","product_name","active_ingredients","active","notes"],
}


RATING = {"focus","energy","mood","emotional_regulation","anxiety","motivation","stress","quality"}


# A second log updates these date-keyed rows without replacing omitted fields.
UPSERT_DATE_TABLES = {"subjective_daily", "intake", "sleep_log"}


DAYMAP = {"red":1,"yellow":2,"green":3,"bad":1,"okay":2,"ok":2,"good":3,"1":1,"2":2,"3":3}


# Legacy defaults remain valid for existing callers; scheduler is excluded
# because a timer may ask for an observation but cannot invent one.
CAPTURE_SOURCES = {"manual", "ui", "chat", "chat-panel", "chat-telegram", "panel-ui"}


def _validate_supplement_product(data):
    """Validate the existing generic product-create path before opening the DB."""
    name = data.get("name", "").strip()
    if not name:
        raise SystemExit("supplement name is required")
    data["name"] = name
    for field, maximum in (("name", 300), ("brand", 300), ("unit", 80),
                           ("form", 100), ("schedule", 300), ("notes", 2000)):
        value = data.get(field)
        if value is not None and (len(value) > maximum or "\x00" in value):
            raise SystemExit(f"supplement {field} must be at most {maximum} characters without NUL")
    if "active" in data:
        if data["active"] not in ("0", "1"):
            raise SystemExit("supplement active must be 0 or 1")
        data["active"] = int(data["active"])
    if "dose" in data:
        try:
            dose = float(data["dose"])
        except (TypeError, ValueError):
            raise SystemExit("supplement dose must be a finite number between 0 and 1000000")
        if not math.isfinite(dose) or not 0 <= dose <= 1_000_000:
            raise SystemExit("supplement dose must be a finite number between 0 and 1000000")
        if not data.get("unit", "").strip():
            raise SystemExit("supplement unit is required when a dose is supplied")
        data["dose"] = dose


def capture_source(source):
    """Validate optional owner-entered provenance without inventing a source."""
    if source is not None and source not in CAPTURE_SOURCES:
        raise SystemExit(f"--source must be one of: {', '.join(sorted(CAPTURE_SOURCES))}")
    return source


def require_soreness_note(c):
    """Require the migration-owned soreness field without creating schema."""
    migrations.require_table(c, "subjective_daily", ("soreness_note",))
    return True


def prepare_log(table, fields, *, clock):
    """Validate the generic writer allowlist and preserve supplied date text."""
    if table not in LOGGABLE: raise SystemExit(f"not a loggable table. allowed: {', '.join(LOGGABLE)}")
    allowed = LOGGABLE[table]
    data = {}
    for pair in fields:
        if "=" not in pair: raise SystemExit(f"bad field '{pair}', use key=value")
        k, v = pair.split("=", 1)
        if k not in allowed: raise SystemExit(f"'{k}' not allowed for {table}. allowed: {', '.join(allowed)}")
        # validate ratings (day_rating is the 1-3 traffic light, others 1-5)
        if k == "day_rating":
            iv = int(v)
            if not 1 <= iv <= 3: raise SystemExit("day_rating must be 1-3 (red/yellow/green)")
            data[k] = iv
        elif k in RATING:
            iv = int(v)
            if not 1 <= iv <= 5: raise SystemExit(f"{k} must be 1-5")
            data[k] = iv
        else:
            data[k] = v
    if "date" in allowed and "date" not in data: data["date"] = runtime.today(clock=clock)
    if table == "supplement_products":
        _validate_supplement_product(data)
    return table, data
