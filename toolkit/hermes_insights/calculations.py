"""Deterministic calculations shared by adapters and command handlers.

Database reads receive a connection; date-sensitive reads receive their clock
functions. These functions do not open a database or import the CLI.
"""

from datetime import date, datetime
import re
import statistics as st

from . import catalogs
from .settings import CANON_TZ


def e1rm(load, reps):
    """Epley estimated 1RM — the single home for this formula (matches the
    panel's display math and the design). Computed at read time, never stored."""
    if load is None or reps is None:
        return None
    return round(load * (1 + reps / 30.0), 1)


def _group_weight_maps(c, *, muscle_to_group=None):
    """Per-title per-SET group weights, both bases: authored = Σ(cited tiered
    weight) per group from exercise_submuscles; coarse = Hevy exercise_muscles
    tags rolled through MUSCLE_TO_GROUP, with that title's unmappable muscle
    names alongside. Shared by _rollup7 and muscle_detail so the radar and
    the drill-down agree by construction. Returns
    (authored {title: {group: w}}, coarse {title: ({group: w}, {bad names})}).
    Laterality: a left+right row pair for one sub-region counts ONCE (MAX
    across sides, not SUM) — combined volume can't tell sides apart, and
    summing would double the axis the moment per-side rows are curated."""
    if muscle_to_group is None:
        muscle_to_group = catalogs.MUSCLE_TO_GROUP
    authored = {}
    for r in c.execute("SELECT t, g, SUM(w) w FROM"
                       " (SELECT exercise_title t, muscle_group g, sub_region,"
                       "  MAX(COALESCE(weight,0)) w FROM exercise_submuscles"
                       "  GROUP BY exercise_title, muscle_group, sub_region)"
                       " GROUP BY t, g"):
        authored.setdefault(r["t"], {})[r["g"]] = r["w"]
    coarse = {}
    for r in c.execute("SELECT exercise_title t, muscle, weight FROM exercise_muscles"):
        gmap, bad = coarse.setdefault(r["t"], ({}, set()))
        grp = muscle_to_group.get((r["muscle"] or "").strip().lower())
        if grp is None:
            bad.add(r["muscle"] or "(missing muscle)")
        else:
            gmap[grp] = gmap.get(grp, 0) + (r["weight"] or 0)
    return authored, coarse


def _basis_weights(title, authored, coarse, *, mobility_exercises=None,
                   non_volume_exercises=None):
    """The v2.8 precedence ladder in ONE place (shared by _rollup7 and
    muscle_detail so the radar and the drill-down cannot drift): mobility
    drills never count; authored beats coarse; a coarse entry whose tags ALL
    miss MUSCLE_TO_GROUP counts as nothing (its volume is fully dropped, not
    'counted coarsely'). Returns (gmap|None, basis) with basis one of
    'mobility' | 'non_volume' | 'authored' | 'coarse' | 'none'."""
    if mobility_exercises is None:
        mobility_exercises = catalogs.MOBILITY_EXERCISES
    if non_volume_exercises is None:
        non_volume_exercises = catalogs.NON_VOLUME_EXERCISES
    if title in mobility_exercises:
        return None, "mobility"
    if title in non_volume_exercises:
        return None, "non_volume"
    if title in authored:
        return authored[title], "authored"
    if title in coarse and coarse[title][0]:
        return coarse[title][0], "coarse"
    return None, "none"


def _circ_diff_min(a_min, b_min):
    """Minute distance on the 24h clock circle — 23:50 vs 00:10 is 20, not 1420
    (bedtimes cross midnight)."""
    d = abs(a_min - b_min)
    return min(d, 1440 - d)


def _start_hhmm(s, *, datetime_type=datetime, timezone=CANON_TZ):
    """HH:MM (CANON_TZ) from a hevy_sets.start_time — ISO UTC from the API
    sync, or the CSV export's local wall-clock formats. None when unparseable
    (counted, never guessed)."""
    if not s:
        return None
    try:
        dt = datetime_type.fromisoformat(s.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:                 # CSV-era local wall-clock
            return dt.strftime("%H:%M")
        return dt.astimezone(timezone).strftime("%H:%M")
    except ValueError:
        pass
    for fmt in ("%b %d, %Y at %I:%M %p", "%d %b %Y, %H:%M", "%d %b %Y at %H:%M"):
        try:
            return datetime_type.strptime(s.strip(), fmt).strftime("%H:%M")
        except ValueError:
            pass
    return None


def _hhmm_min(s):
    """'HH:MM[:SS]' -> minutes from midnight, else None. Never guesses."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$", s or "")
    if not m: return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def _dev_score(value, baseline_vals, coef, invert):
    """The 50±k baseline-median-deviation mapping (RHR: coef=500, invert=True
    — lower than baseline is better; HRV: coef=250, invert=False — higher is
    better) — the ONE formula scores()'s recovery component and readiness()'s
    hrv/rhr components both use. Needs >=14 baseline days and a positive
    median (a zeroed wearable import must not crash the strip) or returns
    None. Returns (score_0_100, baseline_median) on success."""
    if len(baseline_vals) < 14:
        return None
    b = st.median(baseline_vals)
    if b <= 0:
        return None
    dev = (b - value) if invert else (value - b)
    return max(0.0, min(100.0, 50 + coef * dev / b)), b


def _latest_tests(c, days=catalogs.ATHLETIC_STALE_DAYS, *, today, days_ago,
                  catalog=None, estimate_1rm=e1rm):
    """Latest non-voided entry per (movement, side) within the window, with e1RM
    (strength) and days-since. The shared input for §3a L/R, §3c, §3d, §3h.
    Same-day re-tests break the tie by id (latest logged wins): iterate all rows
    ascending by (date, id) and let the last write win — a bare `MAX(date)`
    GROUP BY resolves a same-date tie arbitrarily (the exact bug the pain lens
    fixed in its own query; the §3h mobility lens now reaches this path on a
    same-day re-measure, e.g. re-testing ankle DF across the 30° cutoff)."""
    if catalog is None:
        catalog = catalogs.CATALOG
    rows = c.execute("""SELECT movement, side, load_kg, reps, seconds, rating, cm,
        degrees, passed, date FROM fitness_tests
        WHERE voided=0 AND date >= ? ORDER BY date, id""",
        (days_ago(days),)).fetchall()
    out_map = {}
    for r in rows:
        spec = catalog.get(r["movement"])
        if spec is None:
            continue
        val = estimate_1rm(r["load_kg"], r["reps"]) if spec["kind"] == "strength" else (
            r["seconds"] if spec["kind"] in ("hold", "timed") else
            r["cm"] if spec["kind"] == "distance" else
            r["degrees"] if spec["kind"] == "rom" else
            r["passed"] if spec["kind"] == "binary" else r["rating"])
        out_map[(r["movement"], r["side"])] = {
            "value": val, "date": r["date"], "kind": spec["kind"],
            "reps": r["reps"],
            "outside_protocol_range": (
                spec["kind"] == "strength" and r["reps"] is not None
                and not 6 <= r["reps"] <= 8),
            "days_since": (date.fromisoformat(today()) - date.fromisoformat(r["date"])).days}
    return out_map


def _axis_score(result, target, better):
    """0–100 progress toward target (target-anchored, deterministic, no curve)."""
    if result in (None, 0) or not target:
        return None
    pct = (result / target) if better == "higher" else (target / result)
    return min(100, round(100 * pct))


def _ratio_flag(ratio, band):
    if band is None:            # trend-only pair: no defensible target exists
        return "trend_only"
    lo, hi = band
    if (lo is not None and ratio < lo) or (hi is not None and ratio > hi):
        return "out_of_range"
    return "in_range"


def _tested_ratios(c, *, latest_tests, stale_days=catalogs.ATHLETIC_STALE_DAYS,
                   ratio_seed=None, ratio_flag=_ratio_flag):
    """Tested view: valid agonist:antagonist ratios from isolation tests. Per-side
    pairs also carry a >15% between-limb gap flag (Grygorowicz threshold)."""
    if ratio_seed is None:
        ratio_seed = catalogs.RATIO_SEED
    latest = latest_tests(c, stale_days)
    rows = []
    for seed in ratio_seed:
        sides = (("left",), ("right",)) if seed["per_side"] else (("bilateral",),)
        per = []
        for (side,) in sides:
            n = latest.get((seed["num"], side)); d = latest.get((seed["den"], side))
            if not n or not d or not d["value"]:
                per.append({"side": side, "status": "insufficient_data"})
                continue
            ratio = round(n["value"] / d["value"], 2)
            protocol_valid = not (
                seed["kind"] == "strength"
                and (n["outside_protocol_range"] or d["outside_protocol_range"]))
            per.append({"side": side, "ratio": ratio, "num_value": n["value"],
                        "den_value": d["value"], "num_reps": n["reps"],
                        "den_reps": d["reps"],
                        "flag": (ratio_flag(ratio, seed["band"]) if protocol_valid
                                 else "outside_protocol_range"),
                        "protocol_valid": protocol_valid})
        row = {"key": seed["key"], "num_label": seed["num_label"],
               "den_label": seed["den_label"], "ideal": seed["ideal"],
               "band": seed["band"], "evidence": seed["evidence"],
               "method": seed["method"], "cite": seed["cite"], "per_side": seed["per_side"],
               "sides": per}
        # between-limb gap (only when both sides measured). Check BOTH muscles —
        # an asymmetric quad with symmetric hamstrings is still a red flag the
        # numerator alone would miss. Report the larger gap.
        both = [p for p in per if p.get("num_value") is not None]
        if len(both) == 2:
            def _gap(key):
                vs = [b[key] for b in both]
                return (max(vs) - min(vs)) / max(vs) if max(vs) else 0
            gap = round(max(_gap("num_value"), _gap("den_value")), 3)
            row["side_gap"] = gap
            row["side_gap_flag"] = "asymmetry" if gap > 0.15 else "ok"
        rows.append(row)
    return rows


def _norm_lab_unit(u, *, superscripts=None):
    if superscripts is None:
        superscripts = catalogs._SUP
    u = (u or "").strip().replace("μ", "µ")     # Greek mu → micro sign
    u = re.sub("[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+",
               lambda m: "^" + "".join(superscripts[ch] for ch in m.group(0)), u)
    u = re.sub(r"10\s*[Ee](?=-?\d)", "10^", u)            # 10E9/L → 10^9/L
    u = u.replace("×", "").replace("·", "")
    return re.sub(r"\s+", "", u).lower()                  # spaces gone on BOTH sides


def _lab_in_range(value, low, high):
    if value is None or (low is None and high is None):
        return None
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def _micro_targets(*, micro_seed=None, micro_target_extra=None):
    if micro_seed is None:
        micro_seed = catalogs.MICRO_SEED
    if micro_target_extra is None:
        micro_target_extra = catalogs.MICRO_TARGET_EXTRA
    micros = []
    for m in micro_seed:
        micros.append({"nutrient": m["key"], "unit": m["unit"],
                       "target": m["target"], "source": m["cite"]})
    for m in micro_target_extra:
        micros.append({"nutrient": m["key"], "unit": m["unit"],
                       "target": m["target"], "source": m["cite"],
                       "note": m["note"]})
    return micros
