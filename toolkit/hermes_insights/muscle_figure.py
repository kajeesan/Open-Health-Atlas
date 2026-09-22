"""Activation, balance, pain and mobility projections onto the body figure."""

import sys

from . import runtime
from .catalogs import ATHLETIC_STALE_DAYS, PAIN_CAUSE_MAP, SELF_TEST_CATALOG, REHAB_CATALOG
from .fitness import require_fitness_tables, latest_tests, tested_ratios
from .muscles import require_submuscle_table, activation_set_counts, group_weight_maps, basis_weights
from .figure_contracts import (
    FIGURE_REGION_GROUP, FIGURE_NON_MUSCLE, FIGURE_SUB_SVG, FIGURE_COARSE_SVG,
    FIGURE_LEVEL_EDGES, FIGURE_LEVEL_LEGEND, FIGURE_RATIO_MAP, FIGURE_BALANCE_LEGEND,
    _BALANCE_RANK, RATIO_GAP_THRESHOLD, MOBILITY_STALE_DAYS, FIGURE_MOBILITY_LEGEND,
    _MOBILITY_RANK, FLEX_OVERLAP_NOTE,
)
from .physio import (
    require_physio_tables, pain_band, PAIN_STALE_DAYS, FIGURE_PAIN_LEGEND,
    BOUNDARY_NOTE, CV_NOTE,
)


def fig_level(v):
    if v <= 0:
        return 0
    for i, edge in enumerate(FIGURE_LEVEL_EDGES):
        if v < edge:
            return i + 1
    return len(FIGURE_LEVEL_EDGES) + 1

def mobility_status(value, norm):
    """restricted / normal / untested from a measured value + its cited norm.
    A missing value OR a norm with no defensible cutoff (normal_at None) is an
    honest `untested` — the figure never asserts a band it cannot cite."""
    cut = norm.get("normal_at")
    if value is None or cut is None:
        return "untested"
    if norm.get("better", "higher") == "higher":
        return "normal" if value >= cut else "restricted"
    return "normal" if value <= cut else "restricted"

def balance(c, side_mode, *, clock):
    """§3f strength-balance lens: tested agonist:antagonist ratios landed onto
    SVG regions. Reuses _tested_ratios() output verbatim (no new ratio math —
    the figure can never disagree with the Strength ratios card). Combined
    mode judges each side's ratio against its cited band; the L/R sub-mode
    judges only the >15% between-limb gap and flags the weaker side. DISPLAY
    payload only — the panel paints CSS classes."""
    require_fitness_tables(c)
    mode = side_mode
    regions = {rid: {"status": "untested", "approx": False, "tips": [],
                     "group": g} for rid, g in FIGURE_REGION_GROUP.items()}

    def apply(bases, side, status, tip, approx=False):
        sides = ("left", "right") if side == "bilateral" else (side,)
        for b in bases:
            for s in sides:
                reg = regions[f"{b}-{s}"]
                if _BALANCE_RANK[status] > _BALANCE_RANK[reg["status"]]:
                    reg["status"] = status
                reg["approx"] = reg["approx"] or approx
                if tip and tip not in reg["tips"]:
                    reg["tips"].append(tip)

    for row in tested_ratios(c, clock=clock):
        m = FIGURE_RATIO_MAP[row["key"]]
        num_ap = "num" in m.get("approx", ())
        den_ap = "den" in m.get("approx", ())
        label = f"{row['num_label']}:{row['den_label']}"
        if mode == "lr":
            # left/right view: the between-limb gap is the ONLY judgement
            # (Grygorowicz-validated independent of any ratio target, so it
            # applies to trend-only pairs too); bilateral pairs and single-
            # sided data carry no L/R information → honest untested
            if not row["per_side"]:
                continue
            both = {p["side"]: p for p in row["sides"]
                    if p.get("num_value") is not None}
            if set(both) != {"left", "right"}:
                continue
            for part, bases, ap, name in (
                    ("num_value", m["num"], num_ap, row["num_label"]),
                    ("den_value", m["den"], den_ap, row["den_label"])):
                vals = {s: both[s][part] for s in ("left", "right")}
                top = max(vals.values())
                if not top:
                    # both sides zero (bodyweight-only tests): no between-limb
                    # gap is computable — stay honest grey, never assert
                    # "balanced" from no information (review finding)
                    continue
                # round to 3 dp BEFORE the threshold compare, exactly like
                # _tested_ratios' side_gap — otherwise a raw gap of 0.1500x
                # paints red here while the Strength ratios card says ok
                # (review finding; the shared-invariant would break)
                gap = round((top - min(vals.values())) / top, 3)
                tip = (f"{name} L/R gap {gap * 100:.1f}% "
                       f"(flag >15% — Grygorowicz) · {row['cite']}")
                if gap > RATIO_GAP_THRESHOLD:
                    weak = min(vals, key=vals.get)
                    apply(bases, weak, "deficient", tip, ap)
                    apply(bases, "left" if weak == "right" else "right",
                          "balanced", tip, ap)
                else:
                    apply(bases, "bilateral", "balanced", tip, ap)
            continue
        for p in row["sides"]:
            if p.get("ratio") is None:      # insufficient_data → stays grey
                continue
            side_txt = "" if p["side"] == "bilateral" else f" {p['side']}"
            if row["band"] is None or not p.get("protocol_valid", True):
                if p.get("protocol_valid", True):
                    reason = ("measured; compare with your future same-method "
                              "result, no valid target range")
                else:
                    reason = "recorded, but outside the 6–8-rep protocol"
                tip = (f"{label}{side_txt} {p['ratio']} — {reason} · "
                       f"{row['cite']}")
                apply(m["num"], p["side"], "trend_only", tip, num_ap)
                apply(m["den"], p["side"], "trend_only", tip, den_ap)
                continue
            lo, hi = row["band"]
            band_txt = (f"band {lo if lo is not None else '…'}–"
                        f"{hi if hi is not None else '…'}")
            tip = (f"{label}{side_txt} {p['ratio']} ({band_txt}, "
                   f"ideal {row['ideal']}) · {row['cite']}")
            if m.get("unrepresentable"):
                tip += " · internal rotators undrawn — see note"
            if p["flag"] == "in_range":
                apply(m["num"], p["side"], "balanced", tip, num_ap)
                apply(m["den"], p["side"], "balanced", tip, den_ap)
            else:                           # out_of_range: color the weak link
                below = lo is not None and p["ratio"] < lo
                weak, weak_ap = (m["num"], num_ap) if below else (m["den"], den_ap)
                strong, strong_ap = (m["den"], den_ap) if below else (m["num"], num_ap)
                apply(weak, p["side"], "deficient", tip, weak_ap)
                apply(strong, p["side"], "balanced", tip, strong_ap)
    for reg in regions.values():
        reg["worth_focus"] = reg["status"] == "deficient"
    return {"lens": "strength-balance", "side_mode": mode, "regions": regions,
         "non_muscle": sorted(FIGURE_NON_MUSCLE),
         "legend": FIGURE_BALANCE_LEGEND,
         "unrepresentable": [{"key": k, "note": v["unrepresentable"]}
                             for k, v in FIGURE_RATIO_MAP.items()
                             if v.get("unrepresentable")],
         "window_days": ATHLETIC_STALE_DAYS,
         "note": ("colors come only from CITED tested ratios (quarterly "
                  "isolation tests, e1RM/hold — see Strength ratios); "
                  "untested regions stay grey. The everyday push:pull "
                  "estimate is movement-pattern level and never colors the "
                  "figure. Where ratios share a region the worst status "
                  "wins; the tooltip lists every contributing ratio.")}

def pain(c, days, *, clock):
    """§3g pain lens: color every SVG region (incl. JOINT regions — design rule
    3) by max logged NRS in the window, plus the physio LOOP (cited candidate
    causes per painful region, honestly narrowed by logged self-tests, first-
    line cited drills, and the trial-response trend). The loop ORGANISES cited
    evidence and NEVER diagnoses. DISPLAY payload only — the panel paints."""
    require_physio_tables(c)
    days = max(int(days), 1) if days else PAIN_STALE_DAYS
    since = runtime.days_ago(days, clock=clock)

    # every muscle region defaults to none/grey (like strength-balance untested)
    regions = {rid: {"status": "none", "nrs": 0, "boundary": False,
                     "group": FIGURE_REGION_GROUP[rid]}
               for rid in FIGURE_REGION_GROUP}
    # the pain lens ALSO colors joint bases (design rule 3): add their sided ids,
    # group=None (no muscle drill-down). These leave non_muscle for this payload.
    joint_ids = set()
    for spec in PAIN_CAUSE_MAP.values():
        for b in spec["svg"]:
            for side in ("left", "right"):
                rid = f"{b}-{side}"
                if rid not in regions and rid in set(FIGURE_NON_MUSCLE):
                    regions.setdefault(rid, {"status": "none", "nrs": 0,
                                             "boundary": False, "group": None})
                    joint_ids.add(rid)

    def region_ids(region_token, side):
        out_ids = []
        for b in PAIN_CAUSE_MAP[region_token]["svg"]:
            for s in (("left", "right") if side == "central" else (side,)):
                rid = f"{b}-{s}"
                if rid in regions:
                    out_ids.append(rid)
        return out_ids

    # ---- pain rows in window (max NRS + any red flag per region-token/side) ----
    rows = c.execute("""SELECT region, side, intensity, flags FROM pain_log
        WHERE voided=0 AND date >= ?""", (since,)).fetchall()
    per_region = {}   # (region, side) -> {nrs, flags:set}
    for r in rows:
        if r["region"] not in PAIN_CAUSE_MAP:
            continue
        if (r["intensity"] or 0) <= 0:
            # NRS 0 is a valid "no pain today" reading, but it is NOT a painful
            # area — it must not create a phantom loop entry, color a region, or
            # (with a stray red flag) raise the boundary note (review finding)
            continue
        key = (r["region"], r["side"])
        agg = per_region.setdefault(key, {"nrs": 0, "flags": set()})
        agg["nrs"] = max(agg["nrs"], r["intensity"] or 0)
        agg["flags"] |= {f for f in (r["flags"] or "").split(",") if f}

    for (region, side), agg in per_region.items():
        for rid in region_ids(region, side):
            reg = regions[rid]
            if agg["nrs"] > reg["nrs"]:
                reg["nrs"] = agg["nrs"]
                reg["status"] = pain_band(agg["nrs"])
            reg["boundary"] = reg["boundary"] or bool(agg["flags"])

    for reg in regions.values():
        reg["status"] = pain_band(reg["nrs"])   # in case max updated nrs

    # ---- self-tests + trials (latest non-voided per test/side; trials by date) ----
    # date is day-granular, so a same-day re-test must tie-break by id — ordering
    # ascending and letting later rows overwrite makes the LATEST row win (a bare
    # MAX(date) GROUP BY would pick an arbitrary tied result — review finding).
    st_rows = c.execute("""SELECT test, side, result FROM self_test_log
        WHERE voided=0 AND date >= ? ORDER BY date, id""", (since,)).fetchall()
    latest_test = {}
    for r in st_rows:
        latest_test[(r["test"], r["side"])] = r["result"]
    trial_rows = c.execute("""SELECT date, drill, target, response FROM exercise_trial_log
        WHERE voided=0 AND date >= ? ORDER BY date DESC""", (since,)).fetchall()

    # ---- the loop: one entry per painful (region, side) ----
    loop = []
    any_boundary = False
    for (region, side), agg in sorted(per_region.items()):
        spec = PAIN_CAUSE_MAP[region]
        boundary_flags = sorted(agg["flags"])
        any_boundary = any_boundary or bool(boundary_flags)
        causes = []
        for cause in spec["causes"]:
            support, evidence = "untested", []
            for t in cause["tests"]:
                res = latest_test.get((t["key"], side)) or latest_test.get((t["key"], "central"))
                if res is None or res == "equivocal":
                    continue
                name = SELF_TEST_CATALOG[t["key"]]["name"]
                if res == t["expect"]:
                    evidence.append(f"{t['key']} {res} — consistent ({name})")
                    if support != "against":
                        support = "supported"
                else:
                    evidence.append(f"{t['key']} {res} — argues against ({name})")
                    support = "against"
            causes.append({
                "cause": cause["cause"], "cite": cause["cite"], "support": support,
                "evidence": evidence,
                "self_tests": [{"key": t["key"], "name": SELF_TEST_CATALOG[t["key"]]["name"],
                                "cite": SELF_TEST_CATALOG[t["key"]]["cite"]}
                               for t in cause["tests"]],
                "drills": [{"key": d, **REHAB_CATALOG[d]} for d in cause["drills"]]})
        trials = [{"date": r["date"], "drill": r["drill"], "response": r["response"]}
                  for r in trial_rows if r["target"] == region]
        loop.append({"region": region, "side": side, "nrs": agg["nrs"],
                     "status": pain_band(agg["nrs"]), "causes": causes,
                     "boundary_flags": boundary_flags, "trials": trials,
                     "note": ("Self-tests are provocation aids for comparing "
                              "these candidate patterns.")})

    # joint ids the lens colored must leave THIS payload's non_muscle so the
    # dumb component paints them (design rule 3 — pain-lens-only exception)
    non_muscle = sorted(set(FIGURE_NON_MUSCLE) - joint_ids)
    return {"lens": "pain", "window_days": days, "regions": regions,
         "non_muscle": non_muscle, "legend": FIGURE_PAIN_LEGEND, "loop": loop,
         "boundary_note": BOUNDARY_NOTE if any_boundary else "",
         "cv_note": CV_NOTE,
         "note": ("color = max logged pain (0–10 NRS) per region in the window; "
                  "the pain lens also colors joint regions. The loop lists CITED "
                  "candidate causes + provocation self-tests + first-line drills "
                  "— it organises evidence, never diagnoses. Capture is via the "
                  "coach, not the panel.")}

def mobility(c, days=None, *, clock, mobility_norm):
    """§3h mobility lens: color each drawn muscle region by the status of the
    cited mobility test whose tissue maps to it (restricted / within norm /
    untested — design rule 4), plus a per-test detail list (latest value, cited
    norm, caveat, and the sit-and-reach↔radar overlap note). Reuses the §3e
    fitness_tests capture path (kind rom/binary). DISPLAY payload only."""
    require_fitness_tables(c)
    days = max(int(days), 1) if days else MOBILITY_STALE_DAYS
    latest = latest_tests(c, days, clock=clock)

    regions = {rid: {"status": "untested", "tips": [], "group": g}
               for rid, g in FIGURE_REGION_GROUP.items()}

    def apply(bases, side, status, tip):
        sides = ("left", "right") if side == "bilateral" else (side,)
        for b in bases:
            for s in sides:
                reg = regions.get(f"{b}-{s}")
                if reg is None:
                    continue
                if _MOBILITY_RANK[status] > _MOBILITY_RANK[reg["status"]]:
                    reg["status"] = status
                if tip and tip not in reg["tips"]:
                    reg["tips"].append(tip)

    tests = []
    for mv, norm in mobility_norm.items():
        unit = norm["unit"]
        cut = norm.get("normal_at")
        norm_txt = ("norm pass" if unit == "pass-fail"
                    else f"norm ≥{cut:g} {unit}" if cut is not None
                    else "no cited cutoff")
        sides = []
        for side, v in sorted((s, v) for (m, s), v in latest.items() if m == mv):
            status = mobility_status(v["value"], norm)
            val_txt = (("pass" if v["value"] else "fail") if unit == "pass-fail"
                       else f"{v['value']:g} {unit}")
            side_txt = "" if side == "bilateral" else f" ({side})"
            tip = (f"{norm['label']}{side_txt}: {val_txt} — {status} "
                   f"({norm_txt}) · {norm['cite']}")
            apply(norm["svg"], side, status, tip)
            sides.append({"side": side, "value": v["value"], "status": status,
                          "date": v["date"], "days_since": v["days_since"]})
        tests.append({"movement": mv, "name": norm["label"], "unit": unit,
                      "normal_at": cut, "cite": norm["cite"],
                      "caveat": norm.get("caveat", ""),
                      "flexibility_axis": bool(norm.get("flexibility_axis")),
                      "sides": sides})

    return {"lens": "mobility", "window_days": days, "regions": regions,
         "non_muscle": sorted(FIGURE_NON_MUSCLE),
         "legend": FIGURE_MOBILITY_LEGEND, "tests": tests,
         "flexibility_note": FLEX_OVERLAP_NOTE,
         "note": ("color = the cited mobility-norm status per region (restricted / "
                  "within norm / untested); the detail card lists each test's "
                  "latest value, its cited norm and caveat. These are movement "
                  "SCREENS, not a diagnosis. Capture is via the coach (mobility "
                  "is a fitness test), not the panel.")}

def activation(c, days, *, clock, quarterly_routines, unilateral_titles):
    """§3f payload for the SVG body figure. Lens 'activation': logged
    effective sets over --days landed onto SVG regions — authored
    exercise_submuscles rows land on their mapped regions (laterality-aware:
    a left row colors only the left region); coarse-fallback exercises land
    via their Hevy tags (approx by nature); mobility drills and unmatched
    titles never count and are surfaced, exactly as in the radar (the
    _basis_weights ladder, so the figure can't drift from the radar). A
    sub-region's eff_sets applies equally to every region it maps to (the
    regions are visual subdivisions of one muscle, not shares). DISPLAY
    payload only — all math lives here; the panel just paints levels."""
    require_submuscle_table(c)
    days = max(int(days), 1) if days else 7   # activation window default
    counts = activation_set_counts(c, days, clock=clock, quarterly_routines=quarterly_routines, unilateral_titles=unilateral_titles)
    authored, coarse = group_weight_maps(c)
    sub_rows, tag_rows = {}, {}
    # Per-(sub_region, side) weights with MAX across rows: a bilateral row
    # and a later curated sided row for the same sub-region must not SUM —
    # the radar's _group_weight_maps dedupes laterality pairs with MAX, and
    # the figure must agree with the radar (review finding). Sides stay
    # separate (a left row colors only the left region). Iso rows COUNT,
    # deliberately matching _rollup7 — if the radar ever splits iso out,
    # change BOTH together.
    for r in c.execute("SELECT exercise_title t, sub_region s, weight w,"
                       " laterality l FROM exercise_submuscles"):
        lat = (r["l"] or "bilateral").strip().lower()
        per = sub_rows.setdefault(r["t"], {})
        for side in (("left", "right") if lat == "bilateral" else (lat,)):
            key = (r["s"], side)
            per[key] = max(per.get(key, 0), r["w"] or 0)
    for r in c.execute("SELECT exercise_title t, muscle m, weight w"
                       " FROM exercise_muscles"):
        tag_rows.setdefault(r["t"], []).append(
            ((r["m"] or "").strip().lower(), r["w"] or 0))
    regions = {rid: {"eff_sets": 0.0, "basis": "none", "approx": False,
                     "group": g} for rid, g in FIGURE_REGION_GROUP.items()}

    def land(bases, lat, val, basis, approx):
        for b in bases:
            for side in ("left", "right"):
                if lat in ("bilateral", side):
                    reg = regions[f"{b}-{side}"]
                    reg["eff_sets"] += val
                    reg["approx"] = reg["approx"] or approx
                    reg["basis"] = (basis if reg["basis"] in ("none", basis)
                                    else "mixed")

    unrepresented, fallback, mobility, non_volume, unmatched = (
        set(), set(), set(), set(), set())
    for title, side_counts in sorted(counts.items()):
        _gmap, basis = basis_weights(title, authored, coarse)
        if basis == "mobility":
            mobility.add(title); continue
        if basis == "non_volume":
            non_volume.add(title); continue
        if basis == "none":
            unmatched.add(title); continue
        if basis == "authored":
            for (s, side), w in sub_rows.get(title, {}).items():
                hit = FIGURE_SUB_SVG.get((s or "").strip().lower())
                if hit is None:     # surfaced, never guessed onto a region
                    unrepresented.add(s); continue
                for observed_side, n in side_counts.items():
                    if side == "bilateral":
                        landed_side = observed_side
                    elif observed_side == "bilateral":
                        landed_side = side
                    elif observed_side == side:
                        landed_side = side
                    else:
                        continue
                    land(hit[0], landed_side, (n or 0) * w,
                         "authored", hit[1])
        else:                       # coarse fallback — Hevy tags
            fallback.add(title)
            for m, w in tag_rows.get(title, []):
                bases = FIGURE_COARSE_SVG.get(m)
                if bases is None:   # bad tag: already in the radar's unmapped
                    continue
                for observed_side, n in side_counts.items():
                    land(bases, observed_side, (n or 0) * w,
                         "coarse", True)
    for reg in regions.values():
        # bucket the TRUE value, then round for display — rounding first
        # would promote e.g. 1.96 → 2.0 into the next heat level (review)
        reg["level"] = fig_level(reg["eff_sets"])
        reg["eff_sets"] = round(reg["eff_sets"], 1)
    return {"lens": "activation", "window_days": days, "regions": regions,
         "non_muscle": sorted(FIGURE_NON_MUSCLE),
         "levels": FIGURE_LEVEL_LEGEND,
         "coarse_fallback": sorted(fallback),
         "mobility_excluded": sorted(mobility),
         "non_volume_excluded": sorted(non_volume),
         "unmapped_exercises": sorted(unmatched),
         "unrepresented_sub_regions": sorted(unrepresented),
         "note": ("heat = logged effective sets landed per drawn region "
                  "(authored map first, coarse Hevy tags as flagged fallback); "
                  "approx regions display a deep/undrawn muscle on the "
                  "overlying drawn region — see docs/muscle-figure-map.md")}
