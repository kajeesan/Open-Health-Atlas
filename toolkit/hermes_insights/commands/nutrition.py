"""Validated profile configuration and nutrition target reports."""

from datetime import date
import math

from .. import migrations, runtime
from ..nutrition import (
    ACTIVITY_FALLBACK_VALUES, DIET_PHASES, NUTRITION_TARGET_NAMES,
    OWNER_PROFILE_KEYS, SEX_VALUES, compute_targets, day_score, profile_upsert,
)


def _ensure_profile_table(c):
    migrations.require_table(c, "owner_profile")


def _ensure_nutrient_tables(c):
    migrations.require_table(c, "nutrient_daily")
    migrations.require_table(c, "nutrition_targets")


def profile_set(context, a):
    """User profile config for the T44 targets engine. Config — NOT in the
    bridge allowlists (agent/SSH path only)."""
    key = (a.key or "").strip().lower()
    if key not in OWNER_PROFILE_KEYS:
        raise SystemExit(f"key must be one of: {', '.join(sorted(OWNER_PROFILE_KEYS))}")
    value = a.value
    if key == "height_cm":
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise SystemExit(f"height_cm must be a number, got: {value!r}")
        if not (100 <= v <= 250):
            raise SystemExit(f"height_cm must be between 100 and 250, got: {value!r}")
    elif key == "sex":
        if value not in SEX_VALUES:
            raise SystemExit(f"sex must be one of: {', '.join(sorted(SEX_VALUES))}")
    elif key == "dob":
        try:
            d = date.fromisoformat(value)
        except (TypeError, ValueError):
            raise SystemExit(f"dob must be ISO YYYY-MM-DD, got: {value!r}")
        if not (date(1900, 1, 1) <= d <= date.fromisoformat(runtime.today(clock=context.clock))):
            raise SystemExit(f"dob must be between 1900-01-01 and today, got: {value!r}")
    elif key == "activity_fallback":
        if value not in ACTIVITY_FALLBACK_VALUES:
            raise SystemExit(f"activity_fallback must be one of: {', '.join(sorted(ACTIVITY_FALLBACK_VALUES))}")
    c = runtime.connect(context.database)
    try:
        _ensure_profile_table(c)
        profile_upsert(c, key, value, clock=context.clock)
        c.commit()
        return {"ok": True, "key": key, "value": value}
    finally:
        c.close()


def phase_set(context, a):
    """Diet phase (cut/maintain/bulk) for the T44 targets engine's phase-
    relative kcal adjustment. A phase change always restarts phase_started."""
    phase = (a.phase or "").strip().lower()
    if phase not in DIET_PHASES:
        raise SystemExit(f"phase must be one of: {', '.join(sorted(DIET_PHASES))}")
    c = runtime.connect(context.database)
    try:
        _ensure_profile_table(c)
        started = runtime.today(clock=context.clock)
        profile_upsert(c, "phase", phase, clock=context.clock)
        profile_upsert(c, "phase_started", started, clock=context.clock)
        c.commit()
        return {"ok": True, "phase": phase, "phase_started": started}
    finally:
        c.close()


def nutrition_target_set(context, a):
    """Owner macro targets for the §4a composite (protein_g, kcal). Config —
    NOT in the bridge allowlists (agent/SSH path only)."""
    name = (a.name or "").strip().lower()
    if name not in NUTRITION_TARGET_NAMES:
        raise SystemExit(f"name must be one of: {', '.join(sorted(NUTRITION_TARGET_NAMES))}")
    if not (math.isfinite(a.target) and a.target > 0):
        raise SystemExit("target must be a positive finite number")   # inf zeroes the
        # score forever and NaN dies in sqlite's NULL binding — refuse both
    c = runtime.connect(context.database)
    try:
        _ensure_nutrient_tables(c)
        c.execute("""INSERT INTO nutrition_targets(name, target, updated)
            VALUES(?,?,datetime('now'))
            ON CONFLICT(name) DO UPDATE SET target=excluded.target, updated=datetime('now')""",
            (name, float(a.target)))
        c.commit()
        return {"ok": True, "name": name, "target": a.target}
    finally:
        c.close()


def nutrition_targets(context, a, *, config):
    """Compute current targets without writing records."""
    c = runtime.connect(context.database)
    try:
        return compute_targets(c, clock=context.clock, config=config)
    finally:
        c.close()


def nutrition_coverage(context, a, *, config):
    """READ-only per-day target-coverage rows (T46): the user's
    redefinition of 'food quality' as % of macro+micro targets hit that
    day. Uses the SAME scorer (day_score) and targets source
    (compute_targets) as scores()'s nutrition component — one path, not a
    rival formula. Targets are computed ONCE from the CURRENT profile/
    weight/phase (compute-on-read, same as nutrition-targets); a historical
    day's row is scored against TODAY's targets, not a period-accurate
    re-derivation of that day's own weight/phase — an honest limitation,
    documented rather than silently assumed (out of scope for T46). Water
    is NOT included (the owner defined coverage as macro+micro targets
    only, not hydration). Days with neither logged nutrition nor Cronometer
    data are OMITTED, never zero-scored; an empty window is its own
    insufficient_data."""
    c = runtime.connect(context.database)
    try:
        tg = compute_targets(c, clock=context.clock, config=config)
        if tg["status"] != "ok":
            return {"days": a.days, "rows": [], "status": "insufficient_data",
                 "reason": tg["reason"]}
        rows = []
        for back in range(a.days):
            d = runtime.days_ago(back, clock=context.clock)
            res = day_score(c, d, tg["targets"])
            if res is None:
                continue
            comp = res["components"]
            rows.append({"date": d, "score": res["score"], "band": res["band"],
                         "components": {"protein_pct": comp["protein_pct"],
                                        "kcal_pct": comp["kcal_pct"],
                                        "micros_hit": comp["micros_hit"],
                                        "micros_total": comp["micros_total"]}})
        if not rows:
            return {"days": a.days, "rows": [], "status": "insufficient_data",
                 "reason": "no logged nutrition or Cronometer data in this window"}
        rows.sort(key=lambda r: r["date"])
        return {"days": a.days, "rows": rows, "status": "ok"}
    finally:
        c.close()
