"""Authored-first muscle volume and separate relative-strength theories."""

from datetime import timedelta
import statistics as st
import sys

from . import calculations, migrations, runtime
from .calculations import e1rm
from .catalogs import CATALOG, MUSCLE_GROUP_AXES, MUSCLE_TO_GROUP, MOBILITY_EXERCISES, NON_VOLUME_EXERCISES
from .fitness_contracts import KIND_FIELDS
from .fitness import current_strength_distribution, relative_distribution


# Relative sub-region strength is an inference, not a measurement. Five
# distinct observation dates is the minimum recurrence gate requested by the
# owner; below it the engine reports insufficient evidence and emits no ranked
# theory. Direct tests use the authored exercise title that represents their
# protocol so the same cited anatomy weights apply to both data paths.
SUBREGION_THEORY_DAYS = 365

SUBREGION_MIN_OBSERVATIONS = 5

SUBREGION_IMBALANCE_GAP = 15

FITNESS_TO_AUTHORED_EXERCISE = {
    "hip-extension": "Quarterly Test — Standing Cable Hip Extension",
    "hip-flexion": "Quarterly Test — Standing Cable Hip Flexion",
    "hip-abduction": "Hip Abduction (Cable)",
    "hip-adduction": "Hip Adduction (Cable)",
    "calf-raise": "Single Leg Standing Calf Raise",
    "tibialis-raise": "Quarterly Test — Tibialis Raise",
    "biceps-curl": "Bicep Curl (Dumbbell)",
    "cable-crunch": "Cable Crunch",
    "back-extension": "Back Extension (Weighted Hyperextension)",
}


def require_submuscle_table(c):
    """Idempotent (§3b): the curated exercise→sub-region map. Rows may only
    be seeded deterministically from the user's CITED authored map
    (docs/authored-submuscle-map.md via import-submuscle-map); the model
    must never guess sub-region activation at render time (PRD §3b).
    `source` is NOT NULL so an uncited row cannot exist even by accident.
    Laterality is manual curation and is part of the key so a unilateral
    exercise can seed a left AND a right row."""
    migrations.require_table(c, "exercise_submuscles", ("approx", "iso", "confidence"))


def muscle_rows(c, source, days=7, *, clock):
    """Effective-set rows per raw muscle name (primary=1.0, secondary=0.5)
    from the coarse Hevy tags — the FLAT `muscle-volume` view only. Since
    v2.8 the radar/score/drill-down use _rollup7 (authored-first) instead,
    so this flat view intentionally shows the raw-tag perspective and CAN
    differ from the radar for authored exercises.
    COALESCE(set_type,'normal') matches the scores engine: import-hevy
    stores set_type verbatim, and NULL means a working set, not a warmup.
    NULL weights count as 0 instead of poisoning the SUM to None. Note the
    logged window is inclusive: date >= today-N spans N+1 calendar days —
    the engine-wide convention (scores uses the same bound)."""
    if source == "logged":
        return c.execute("""SELECT em.muscle, ROUND(SUM(COALESCE(em.weight,0)),1) AS eff_sets
            FROM hevy_sets h JOIN exercise_muscles em ON em.exercise_title=h.exercise_title
            WHERE h.date >= ? AND COALESCE(h.set_type,'normal')!='warmup'
            GROUP BY em.muscle ORDER BY eff_sets DESC""", (runtime.days_ago(days, clock=clock),)).fetchall()
    return c.execute("""SELECT em.muscle, ROUND(SUM(COALESCE(r.target_sets*em.weight,0)),1) AS eff_sets
        FROM training_schedule ts JOIN routines r ON r.routine_name=ts.routine_name
        JOIN exercise_muscles em ON em.exercise_title=r.exercise_title
        GROUP BY em.muscle ORDER BY eff_sets DESC""").fetchall()


def set_counts(c, source, days=7, anchor=None, *, clock):
    """Working-set count per exercise title. logged = non-warmup hevy_sets in
    the inclusive --days window (same COALESCE + bound conventions as
    _muscle_rows); planned = Σ target_sets across schedule occurrences —
    COALESCE(target_sets,0) keeps a routine-set-without---sets exercise in
    the dict at n=0, so group PRESENCE survives even when volume is unknown
    (the muscle_balance score counts planned membership by presence)."""
    if source == "logged":
        if anchor is not None:
            start = (anchor - timedelta(days=days)).isoformat()
            return {r["t"]: r["n"] for r in c.execute("""SELECT
                    h.exercise_title t, COUNT(*) n FROM hevy_sets h
                WHERE h.date >= ? AND h.date <= ?
                  AND COALESCE(h.set_type,'normal')!='warmup'
                GROUP BY h.exercise_title""", (start, anchor.isoformat()))}
        return {r["t"]: r["n"] for r in c.execute("""SELECT
                h.exercise_title t, COUNT(*) n FROM hevy_sets h
            WHERE h.date >= ? AND COALESCE(h.set_type,'normal')!='warmup'
            GROUP BY h.exercise_title""", (runtime.days_ago(days, clock=clock),))}
    return {r["t"]: r["n"] for r in c.execute("""SELECT
            r.exercise_title t, SUM(COALESCE(r.target_sets,0)) n
        FROM training_schedule ts JOIN routines r ON r.routine_name=ts.routine_name
        GROUP BY r.exercise_title""")}


def group_weight_maps(c):
    return calculations._group_weight_maps(c, muscle_to_group=MUSCLE_TO_GROUP)


def basis_weights(title, authored, coarse):
    return calculations._basis_weights(
        title, authored, coarse, mobility_exercises=MOBILITY_EXERCISES,
        non_volume_exercises=NON_VOLUME_EXERCISES,
    )


def rollup(c, source, days=7, anchor=None, *, clock):
    """v2.8 authored-first 7-group rollup — ONE source of truth shared by the
    radar (--by group), the muscle_balance score and the drill-down, so the
    three can never drift. Per exercise with sets (logged) or target sets
    (planned): if the user's cited exercise_submuscles map covers it, its
    contribution is n_sets × Σ(authored tiered weight) per group — iso rows
    COUNT for now (the flag is stored so isometric/stability work can be
    split out later); otherwise it FALLS BACK to Hevy's coarse
    exercise_muscles tags rolled through MUSCLE_TO_GROUP, and the title is
    surfaced in `fallback` ("pending source"). Mobility drills never count.
    Titles in neither map land in `unmatched` — surfaced, never guessed."""
    require_submuscle_table(c)
    counts = set_counts(c, source, days, anchor=anchor, clock=clock)
    authored, coarse = group_weight_maps(c)
    groups = {g: 0.0 for g in MUSCLE_GROUP_AXES}
    present, unmapped = set(), set()
    fallback, mobility, non_volume, unmatched = [], [], [], []
    for title, n in sorted(counts.items()):
        gmap, basis = basis_weights(title, authored, coarse)
        if basis == "mobility":             # authored map: not strength volume
            mobility.append(title)
            continue
        if basis == "non_volume":           # test result, not training volume
            non_volume.append(title)
            continue
        if basis != "authored" and title in coarse:
            unmapped |= coarse[title][1]    # bad tag names stay diagnosable
        if basis == "none":
            unmatched.append(title)
            continue
        if basis == "coarse":
            fallback.append(title)
        for g, w in gmap.items():
            groups[g] += (n or 0) * w
            present.add(g)                  # presence even at n=0 (see above)
    # round once at the end — per-add rounding would accumulate error
    return {"groups": {g: round(v, 1) for g, v in groups.items()},
            "present": present, "fallback": fallback, "mobility": mobility,
            "non_volume": non_volume,
            "unmapped": unmapped, "unmatched": unmatched}


def bounded_score(value):
    return max(0.0, min(100.0, float(value)))


def personal_performance(by_date):
    """Normalize one exercise/test only against its own history.

    Cross-exercise kilograms are never compared. The result combines the
    current value's within-history percentile with change from the earliest
    three observations. Five dates are required so one unusually good session
    cannot become a strength theory.
    """
    series = sorted((d, max(vals)) for d, vals in by_date.items() if vals)
    if len(series) < SUBREGION_MIN_OBSERVATIONS:
        return None
    values = [v for _d, v in series]
    baseline = st.median(values[:3])
    current = st.median(values[-3:])
    if baseline <= 0:
        return None
    # Mid-rank ties: a flat history is the 50th percentile, not the 100th.
    # Without this, unchanged performance would look artificially strong.
    lower = sum(v < current for v in values)
    equal = sum(v == current for v in values)
    percentile = 100.0 * (lower + 0.5 * equal) / len(values)
    change_pct = 100.0 * (current / baseline - 1.0)
    # +/-25% around the personal baseline spans the bounded progress scale.
    progress = bounded_score(50.0 + 2.0 * change_pct)
    score = round(0.55 * percentile + 0.45 * progress)
    return {
        "score": score,
        "observations": len(series),
        "baseline": round(baseline, 1),
        "current": round(current, 1),
        "change_pct": round(change_pct, 1),
        "from": series[0][0],
        "to": series[-1][0],
    }


def fitness_value(row):
    spec = CATALOG.get(row["movement"])
    if not spec:
        return None
    if spec["kind"] == "strength":
        return e1rm(row["load_kg"], row["reps"])
    field = KIND_FIELDS[spec["kind"]][0]
    value = row[field]
    if value is None:
        return None
    # Lower is better for timed tests. Reciprocal conversion keeps personal
    # normalization monotonic without mixing the raw unit with other tests.
    return (1.0 / value) if spec["kind"] == "timed" and value > 0 else value


def subregion_strength_theories(c, group, map_rows, *, clock):
    """Return ranked, explicitly probabilistic relative-strength theories.

    Evidence is kept in three separate signals:
    - exposure: weighted working-set recurrence (support only, never strength);
    - performance: e1RM normalized within each exercise's own history;
    - direct tests: repeated protocol values normalized within that test.

    The score is a comparative index within one muscle group, not an absolute
    strength percentage, diagnosis, or claim that activation was measured.
    """
    lo = runtime.days_ago(SUBREGION_THEORY_DAYS, clock=clock)
    rows_by_exercise = {}
    for row in map_rows:
        rows_by_exercise.setdefault(row["exercise_title"], []).append(row)

    hevy_dates = {}
    hevy_daily = {}
    hevy_sets = {}
    for row in c.execute(
            "SELECT date,exercise_title,weight_kg,reps FROM hevy_sets "
            "WHERE date>=? AND COALESCE(set_type,'normal')!='warmup' "
            "AND weight_kg IS NOT NULL AND reps IS NOT NULL ORDER BY date", (lo,)):
        title = row["exercise_title"]
        if title not in rows_by_exercise:
            continue
        hevy_dates.setdefault(title, set()).add(row["date"])
        hevy_sets[title] = hevy_sets.get(title, 0) + 1
        value = e1rm(row["weight_kg"], row["reps"])
        if value is not None:
            hevy_daily.setdefault(title, {}).setdefault(row["date"], []).append(value)
    hevy_perf = {title: personal_performance(days)
                 for title, days in hevy_daily.items()}

    test_dates = {}
    test_daily = {}
    for row in c.execute(
            "SELECT date,movement,side,load_kg,reps,seconds,rating,cm,degrees,passed "
            "FROM fitness_tests WHERE voided=0 AND date>=? ORDER BY date,id", (lo,)):
        title = FITNESS_TO_AUTHORED_EXERCISE.get(row["movement"])
        if title not in rows_by_exercise:
            continue
        value = fitness_value(row)
        if value is None:
            continue
        test_dates.setdefault(title, set()).add(row["date"])
        test_daily.setdefault(title, {}).setdefault(row["date"], []).append(value)
    test_perf = {title: personal_performance(days)
                 for title, days in test_daily.items()}

    regions = {}
    for row in map_rows:
        region = regions.setdefault(row["sub_region"], {
            "sub_region": row["sub_region"], "observation_dates": set(),
            "effective_sets": 0.0, "performance": [], "direct": [],
            "mapped_exercises": set(), "active_exercises": set(),
            "emg_weight": 0.0, "map_weight": 0.0,
        })
        title = row["exercise_title"]
        map_weight = float(row["weight"] or 0)
        dynamic_weight = map_weight * (0.5 if row["iso"] else 1.0)
        quality = 1.0 if row["confidence"] == "E" else 0.7
        region["mapped_exercises"].add(title)
        region["map_weight"] += map_weight
        if row["confidence"] == "E":
            region["emg_weight"] += map_weight
        dates = hevy_dates.get(title, set()) | test_dates.get(title, set())
        region["observation_dates"].update(dates)
        if dates:
            region["active_exercises"].add(title)
        region["effective_sets"] += hevy_sets.get(title, 0) * dynamic_weight
        hp = hevy_perf.get(title)
        if hp:
            region["performance"].append((hp["score"], dynamic_weight * quality, title, hp))
        tp = test_perf.get(title)
        if tp:
            region["direct"].append((tp["score"], map_weight * quality, title, tp))

    max_exposure = max((r["effective_sets"] for r in regions.values()), default=0.0)
    qualified, insufficient = [], []
    for region in regions.values():
        n_obs = len(region["observation_dates"])
        if n_obs < SUBREGION_MIN_OBSERVATIONS:
            insufficient.append({
                "sub_region": region["sub_region"],
                "observations": n_obs,
                "needed": SUBREGION_MIN_OBSERVATIONS,
                "reason": "fewer than five distinct exercise/test dates",
            })
            continue

        exposure_score = round(100 * region["effective_sets"] / max_exposure) if max_exposure else 0
        signals = [(exposure_score, 0.15, "exposure_proxy")]

        def weighted(items):
            total = sum(weight for _score, weight, _title, _detail in items)
            return (sum(score * weight for score, weight, _title, _detail in items) / total
                    if total else None)

        performance_score = weighted(region["performance"])
        direct_score = weighted(region["direct"])
        if performance_score is not None:
            signals.append((performance_score, 0.60, "normalized_performance"))
        if direct_score is not None:
            signals.append((direct_score, 0.25, "direct_fitness_tests"))
        total_weight = sum(weight for _score, weight, _name in signals)
        score = round(sum(value * weight for value, weight, _name in signals) / total_weight)

        mapping_quality = (region["emg_weight"] / region["map_weight"]
                           if region["map_weight"] else 0.0)
        recurrence = min(1.0, n_obs / 15.0)
        performance_coverage = min(1.0, len(region["performance"]) / 2.0)
        direct_coverage = min(1.0, len(region["direct"]))
        confidence_score = (0.35 * recurrence + 0.25 * (0.65 + 0.35 * mapping_quality)
                            + 0.25 * performance_coverage + 0.15 * direct_coverage)
        confidence = ("high" if confidence_score >= 0.72 else
                      "moderate" if confidence_score >= 0.48 else "low")
        qualified.append({
            "sub_region": region["sub_region"],
            "relative_strength_score": score,
            "confidence": confidence,
            "confidence_score": round(confidence_score, 2),
            "observations": n_obs,
            "effective_sets": round(region["effective_sets"], 1),
            "exposure_score": exposure_score,
            "performance_score": (round(performance_score) if performance_score is not None else None),
            "direct_test_score": (round(direct_score) if direct_score is not None else None),
            "signals": [name for _value, _weight, name in signals],
            "active_exercises": sorted(region["active_exercises"]),
            "performance_evidence": [
                {"exercise": title, **detail}
                for _score, _weight, title, detail in region["performance"]],
            "direct_test_evidence": [
                {"protocol": title, **detail}
                for _score, _weight, title, detail in region["direct"]],
        })

    qualified.sort(key=lambda r: (-r["relative_strength_score"], r["sub_region"]))
    base = {
        "window_days": SUBREGION_THEORY_DAYS,
        "minimum_observations": SUBREGION_MIN_OBSERVATIONS,
        "method": ("relative theory = within-exercise personal performance + repeated direct "
                   "tests + a small exposure-support term; raw loads across exercises are never compared"),
        "boundary": ("Estimated relative capacity within this muscle group; not measured activation, "
                     "absolute strength, injury risk, or diagnosis."),
        "insufficient_regions": sorted(insufficient, key=lambda r: r["sub_region"]),
    }
    if len(qualified) < 2:
        return {**base, "status": "insufficient_evidence", "theories": [],
                "reason": "at least two sub-regions need five recurring observation dates"}

    top, bottom = qualified[0], qualified[-1]
    gap = top["relative_strength_score"] - bottom["relative_strength_score"]
    if gap >= SUBREGION_IMBALANCE_GAP:
        imbalance = {
            "status": "possible_imbalance",
            "stronger": top["sub_region"], "weaker": bottom["sub_region"],
            "gap_points": gap,
            "confidence": "low" if "low" in (top["confidence"], bottom["confidence"])
                          else "moderate",
            "theory": (f"{top['sub_region']} may currently have greater relative capacity than "
                       f"{bottom['sub_region']}; the {gap}-point gap recurs across the available evidence."),
        }
    else:
        imbalance = {
            "status": "no_clear_imbalance",
            "gap_points": gap,
            "theory": "The recurring evidence does not currently show a clear sub-region gap.",
        }
    return {**base, "status": "theory", "theories": qualified,
            "imbalance": imbalance}


def activation_set_counts(c, days=7, *, clock, quarterly_routines, unilateral_titles):
    """Working-set counts by exercise and observed side for the body figure.

    Ordinary Hevy sets have no side field and therefore remain bilateral.
    The exact quarterly lower-body workout is the one safe exception: its
    signed contract says the first non-warmup set is left and the second is
    right. Warmups never consume a side position, matching fitness-test import.
    """
    rows = c.execute("""SELECT id, date, workout_title, start_time,
                              exercise_title, set_index
        FROM hevy_sets
        WHERE date >= ? AND COALESCE(set_type,'normal')!='warmup'
        ORDER BY date, start_time, exercise_title, set_index, id""",
        (runtime.days_ago(days, clock=clock),)).fetchall()
    quarterly_titles = {cfg["title"] for cfg in quarterly_routines.values()}
    ordinals, counts = {}, {}
    for r in rows:
        title = r["exercise_title"]
        side = "bilateral"
        if (r["workout_title"] in quarterly_titles
                and title in unilateral_titles):
            key = (r["date"], r["start_time"], r["workout_title"], title)
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            side = ("left", "right")[ordinal] if ordinal < 2 else "bilateral"
        per = counts.setdefault(title, {})
        per[side] = per.get(side, 0) + 1
    return counts


def muscle_volume(c, source, days, by, *, clock):
    """Effective weekly sets per muscle. --source planned (from routines+
    schedule) or logged (from hevy_sets over --days) — the flat per-muscle
    view (coarse Hevy tags, unchanged). --by group instead returns the
    7-axis radar payload for three independently toggleable, overlaid sources:
    the most recent completed quarterly strength-balance test, planned weekly
    volume, and combined logged volume over --days. Each source includes a
    relative distribution normalized to its own strongest group (100), so
    unlike raw units can share one honest radar. Volume uses the v2.8
    authored-first rollup (_rollup7 — the cited sub-muscle map where
    authored, coarse-tag fallback otherwise). --source contradicts --by
    group (which returns all three views) and is refused."""
    if by == "group":
        if source is not None:
            sys.exit("--source does not combine with --by group (it returns all views)")
        lg = rollup(c, "logged", days, clock=clock)
        pl = rollup(c, "planned", clock=clock)
        current = current_strength_distribution(c)
        return {"by": "group", "window_days": days, "axes": MUSCLE_GROUP_AXES,
             "current_strengths": current,
             "combined": lg["groups"], "planned": pl["groups"],
             "logged_distribution_pct": relative_distribution(lg["groups"]),
             "planned_distribution_pct": relative_distribution(pl["groups"]),
             "coarse_fallback": sorted(set(lg["fallback"]) | set(pl["fallback"])),
             "mobility_excluded": sorted(set(lg["mobility"]) | set(pl["mobility"])),
             "non_volume_excluded": sorted(set(lg["non_volume"]) |
                                           set(pl["non_volume"])),
             "unmapped_exercises": sorted(set(lg["unmatched"]) | set(pl["unmatched"])),
             "note": ("volume = sets × the user's cited sub-muscle weights "
                      "(authored map, iso rows counted); coarse_fallback "
                      "exercises use Hevy tags until authored"),
             "unmapped": sorted(lg["unmapped"] | pl["unmapped"]),
             "left_right": {"status": "insufficient_data",
                            "reason": "left/right is measured by per-side "
                                      "fitness tests (quarterly isolation "
                                      "battery), not training volume — not "
                                      "captured yet"}}
        return
    source = source or "planned"
    rows = muscle_rows(c, source, days, clock=clock)
    if source == "logged":
        return {"source": "logged", "window_days": days,
             "weekly_effective_sets": {r["muscle"]: r["eff_sets"] for r in rows}}
    else:
        return {"source": "planned",
             "weekly_effective_sets": {r["muscle"]: r["eff_sets"] for r in rows}}


def muscle_detail(c, group, days, *, clock):
    """§3b drill-down for ONE radar group. Two sections:
    - exercises: which lifts drove the group's volume over --days (REAL data,
      warmups excluded; the default 7-day window matches the radar). v2.8:
      authored-first via the SAME weight maps as the radar (_group_weight_maps)
      — an authored exercise contributes n_sets × Σ(its cited weights in this
      group) even where Hevy's coarse tag disagrees (this replaces the old
      map_drift flag: authored wins instead of being hidden); unauthored
      exercises fall back to the coarse tags and are flagged basis='coarse' +
      listed in pending_source. Mobility drills never count (as in the radar).
    - sub_regions: per-(sub-region, laterality) volume via exercise_submuscles
      — status='pending_source' while that curated map is empty (the scaffold
      state)."""
    want = (group or "").strip().lower()
    group = next((g for g in MUSCLE_GROUP_AXES if g.lower() == want), None)
    if group is None:
        sys.exit(f"group must be one of: {', '.join(MUSCLE_GROUP_AXES)}")
    require_submuscle_table(c)
    days = max(int(days), 1)
    lo = runtime.days_ago(days, clock=clock)
    # per-exercise contribution to this group — the radar's ladder + weight
    # maps (_basis_weights/_group_weight_maps), so the drill-down and the
    # axis clicked share one basis. Per-exercise values are rounded to 0.1
    # for display, so their sum can differ from the axis by rounding cents.
    # ONE set-count query feeds both sections (exercises + sub_regions) —
    # they must describe the same window.
    authored, coarse = group_weight_maps(c)
    set_rows = c.execute("""SELECT h.exercise_title ex, COUNT(*) n, MAX(h.date) last
        FROM hevy_sets h
        WHERE h.date >= ? AND COALESCE(h.set_type,'normal')!='warmup'
        GROUP BY h.exercise_title""", (lo,)).fetchall()
    exercises, pending = [], []
    for r in set_rows:
        t = r["ex"]
        gmap, basis = basis_weights(t, authored, coarse)
        if gmap is None:
            continue
        w = gmap.get(group)
        if w is None:       # 0 stays visible: 'weight unknown' is a signal
            continue
        if basis == "coarse":
            pending.append(t)
        exercises.append({"exercise": t, "eff_sets": round(r["n"] * w, 1),
                          "sets": r["n"], "last_date": r["last"],
                          "basis": basis})
    exercises.sort(key=lambda e: -e["eff_sets"])
    # sub-region rollup — only meaningful once the cited map has rows
    map_rows = c.execute(
        "SELECT exercise_title, sub_region, weight, laterality, source, approx, "
        "iso, confidence "
        "FROM exercise_submuscles WHERE muscle_group=?", (group,)).fetchall()
    if not map_rows:
        sub = {"status": "pending_source",
               "note": "sub-region map unpopulated — awaiting the user's "
                       "cited authored map (import-submuscle-map); "
                       "never guessed at render time"}
    else:
        # same fetched set counts as the exercises section; mobility titles
        # never contribute even if stale authored rows linger for them
        sets_by_ex = {r["ex"]: r["n"] for r in set_rows
                      if r["ex"] not in MOBILITY_EXERCISES
                      and r["ex"] not in NON_VOLUME_EXERCISES}
        regions = {}
        for m in map_rows:
            key = (m["sub_region"], m["laterality"])
            reg = regions.setdefault(key, {"sub_region": m["sub_region"],
                "laterality": m["laterality"],
                "eff_sets": 0.0, "exercises": [], "sources": set(),
                "approx": False})
            n = sets_by_ex.get(m["exercise_title"], 0)
            if n:
                reg["eff_sets"] = round(reg["eff_sets"] + n * (m["weight"] or 0), 1)
                reg["exercises"].append(m["exercise_title"])
            reg["sources"].add(m["source"])
            reg["approx"] = reg["approx"] or bool(m["approx"])
        for reg in regions.values():
            reg["sources"] = sorted(reg["sources"])
        sub = {"status": "ok", "regions": sorted(regions.values(),
                                                 key=lambda x: -x["eff_sets"]),
               "note": "emphasis weights are EMG-informed approximations, cited "
                       "per row — not exact activation shares. Since v2.8 the "
                       "exercises table shares this map for authored exercises; "
                       "coarse-fallback rows still use Hevy's tag weights"}
    strength_theories = (subregion_strength_theories(c, group, map_rows, clock=clock)
                         if map_rows else {
                             "status": "insufficient_evidence", "theories": [],
                             "minimum_observations": SUBREGION_MIN_OBSERVATIONS,
                             "reason": "the cited authored sub-region map is not seeded",
                         })
    return {"group": group, "window_days": days, "exercises": exercises,
         "pending_source": sorted(pending), "sub_regions": sub,
         "strength_theories": strength_theories}
