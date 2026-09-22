"""Hevy payload decoding and deterministic provider-field normalization."""

import json
from datetime import datetime, timezone

from ..body_contracts import BODY_CLAMPS
from ..fitness_contracts import KIND_FIELDS, FT_CLAMPS
from .hevy_csv import parse_date as parse_csv_date


def parse_api_date(s, *, civil_timezone):
    """Convert provider timestamps to a civil date, retaining the CSV fallback."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return parse_csv_date(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(civil_timezone).date().isoformat()


def load_json(path, key):
    """Read a keyed Hevy object or its legacy top-level list."""
    with open(path) as f:
        data = json.load(f)
    return data.get(key, []) if isinstance(data, dict) else data


def truncate(s, n=300):
    """Bound cloud-sourced strings before they enter canonical storage."""
    return s[:n] if isinstance(s, str) else s


def load_quarterly_config(path):
    """Load exact routine and template identities; reject malformed configuration."""
    if not path:
        return {}, frozenset()
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or set(payload) != {
        "routines", "unilateral_titles"
    }:
        raise ValueError(
            "HERMES_HEVY_QUARTERLY_CONFIG must contain routines and unilateral_titles"
        )
    routines = payload["routines"]
    unilateral = payload["unilateral_titles"]
    if not isinstance(routines, dict) or not isinstance(unilateral, list):
        raise ValueError("invalid Hevy quarterly configuration types")
    clean = {}
    for routine_id, item in routines.items():
        if not isinstance(routine_id, str) or not routine_id.strip():
            raise ValueError("Hevy quarterly routine IDs must be non-empty strings")
        if not isinstance(item, dict) or set(item) != {"title", "templates"}:
            raise ValueError("each Hevy quarterly routine needs title and templates")
        title, templates = item["title"], item["templates"]
        if not isinstance(title, str) or not title.strip() or not isinstance(templates, dict):
            raise ValueError("invalid Hevy quarterly routine definition")
        if not templates or not all(
            isinstance(key, str) and key.strip()
            and isinstance(value, str) and value.strip()
            for key, value in templates.items()
        ):
            raise ValueError("Hevy quarterly template mappings must be non-empty strings")
        clean[routine_id] = {"title": title, "templates": dict(templates)}
    if not all(isinstance(title, str) and title.strip() for title in unilateral):
        raise ValueError("Hevy quarterly unilateral titles must be non-empty strings")
    return clean, frozenset(unilateral)


def quarterly_routine(workout, *, routines):
    """Resolve an immutable routine ID or a unique exact configured title."""
    routine_id = workout.get("routine_id")
    if routine_id in routines:
        return routines[routine_id]
    title = workout.get("title")
    matches = [v for v in routines.values()
               if title == v["title"]]
    return matches[0] if len(matches) == 1 else None


def quarterly_value(spec, raw_set, fallback_load_kg=None, *, parse_number):
    """Translate a working set without guessing missing measurements or load."""
    vals = {field: None for field in
            ("load_kg", "reps", "seconds", "rating", "cm", "degrees", "passed")}
    kind = spec["kind"]
    used_fallback_load = False
    if kind == "strength":
        vals["load_kg"] = parse_number(raw_set.get("weight_kg"))
        if vals["load_kg"] is None and fallback_load_kg is not None:
            vals["load_kg"] = fallback_load_kg
            used_fallback_load = True
        reps = parse_number(raw_set.get("reps"))
        if reps is not None and float(reps).is_integer():
            vals["reps"] = int(reps)
    elif kind in ("hold", "timed"):
        vals["seconds"] = parse_number(raw_set.get("duration_seconds"))
    elif kind == "control":
        rating = parse_number(raw_set.get("reps"))
        if rating is not None and float(rating).is_integer():
            vals["rating"] = int(rating)
    required = KIND_FIELDS[kind]
    missing = [field for field in required if vals[field] is None]
    if missing:
        return None, f"missing {', '.join(missing)}", False
    for field in required:
        lo, hi = FT_CLAMPS[field]
        if not lo <= vals[field] <= hi:
            return None, f"{field} outside [{lo}, {hi}]", False
    return vals, None, used_fallback_load


# Hevy's BodyMeasurement schema uses bare waist/hips but chest_cm/neck_cm
# (official api.hevyapp.com/docs spec, checked 2026-07-10). Both spellings
# remain accepted; fields without a body_metrics column are not imported.
HEVY_BODY_FIELDS = {
    "weight_kg": "weight_kg",
    "waist": "waist_cm", "waist_cm": "waist_cm",          # spec: waist
    "chest": "chest_cm", "chest_cm": "chest_cm",          # spec: chest_cm
    "neck": "neck_cm", "neck_cm": "neck_cm",              # spec: neck_cm
    "hips": "hip_cm", "hip_cm": "hip_cm",                 # spec: hips
    "fat_percent": "body_fat_pct", "body_fat_pct": "body_fat_pct",  # spec: fat_percent
}



def body_rows(items, *, parse_number, civil_timezone):
    """Normalize body measurements; the last nonempty measurement for a date wins."""
    rows, clamped = {}, 0
    for m in items:
        if not isinstance(m, dict):
            continue
        d = parse_api_date(m.get("date"), civil_timezone=civil_timezone)
        if d is None:
            continue
        vals = {}
        for field, col in HEVY_BODY_FIELDS.items():
            v = parse_number(m.get(field))
            if v is None:
                continue
            lo, hi = BODY_CLAMPS[col]
            if not lo <= v <= hi:
                clamped += 1
                continue
            vals[col] = v
        if vals:
            rows[d] = vals          # one measurement set per date (last wins)
    return rows, clamped


def routine_rows(routines, *, parse_number):
    """Aggregate repeated exercises while preserving the first block targets."""
    agg, dupes = {}, 0
    for r in routines:
        rname = truncate(r.get("title"))
        for ex in (r.get("exercises") or []):
            work = [s for s in (ex.get("sets") or [])
                    if (s.get("type") or "normal") != "warmup"]
            first = work[0] if work else None
            key = (rname, truncate(ex.get("title")))
            if key in agg:
                dupes += 1
                agg[key]["target_sets"] = ((agg[key]["target_sets"] or 0)
                                           + len(work)) or None
                continue
            agg[key] = {
                "ex_order": ex.get("index"),
                "target_sets": len(work) or None,
                "target_reps": int(first["reps"]) if first and first.get("reps") is not None else None,
                "target_weight_kg": parse_number(first.get("weight_kg")) if first else None,
            }
    return agg, dupes


def workout_sets(workouts, *, parse_number, civil_timezone):
    """Yield normalized set rows, or None for each implausible skipped set."""
    for w in workouts:
        d = parse_api_date(w.get("start_time"), civil_timezone=civil_timezone)
        for ex in (w.get("exercises") or []):
            for s in (ex.get("sets") or []):
                wkg, rv, rpe = parse_number(s.get("weight_kg")), parse_number(s.get("reps")), parse_number(s.get("rpe"))
                dist, dur = parse_number(s.get("distance_meters")), parse_number(s.get("duration_seconds"))
                if not ((wkg is None or 0 <= wkg <= 1000)
                        and (rv is None or 0 <= rv <= 1000)
                        and (rpe is None or 0 <= rpe <= 10)
                        and (dist is None or 0 <= dist <= 1_000_000)
                        and (dur is None or 0 <= dur <= 604_800)):
                    yield None
                    continue
                idx = s.get("index")
                yield (d, truncate(w.get("title")), w.get("start_time"), w.get("end_time"),
                   truncate(w.get("description")), truncate(ex.get("title")),
                   ex.get("supersets_id"), truncate(ex.get("notes")),
                   int(idx) + 1 if isinstance(idx, (int, float)) else None,
                   truncate(s.get("type"), 20), wkg,
                   int(rv) if rv is not None else None,
                   round(dist / 1000.0, 3) if dist is not None else None,
                   dur, rpe)


def template_muscles(template):
    """Yield unique muscle tags without letting a secondary downgrade the primary."""
    primary = template.get("primary_muscle_group")
    if primary:
        yield truncate(primary, 50), 1.0
    for secondary in dict.fromkeys(template.get("secondary_muscle_groups") or []):
        if secondary and secondary != primary:
            yield truncate(secondary, 50), 0.5
