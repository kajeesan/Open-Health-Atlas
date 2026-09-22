"""Fitness protocol queries, quarterly coverage and personal target reports."""

from datetime import date
from functools import partial
import sys

from . import calculations, migrations, runtime
from .calculations import e1rm, _axis_score as axis_score, _ratio_flag as ratio_flag
from .catalogs import ATHLETIC_AXES, ATHLETIC_STALE_DAYS, CATALOG, KIND_BETTER, MUSCLE_GROUP_AXES, RATIO_SEED


# §3d EVERYDAY view — movement-PATTERN balance from compound e1RM. HEURISTIC
# targets only (never the clinical cut-offs above); flags are trend-only, no
# injury-risk claims. PATTERN_MAP contains fictional example titles;
# unmapped user titles are surfaced, never guessed. Added weight only for
# bodyweight lifts (Hevy logs no bodyweight) → e1RM understates: pattern-level
# estimate, not muscle-level.
PATTERN_MAP = {
    "Seated Cable Row": "horizontal-pull",
    "Band Pull-Apart": "horizontal-pull",
    "Lat Pulldown": "vertical-pull",
    "Dumbbell Shoulder Press": "vertical-push",
    "Incline Push-Up": "horizontal-push",
    "Dumbbell Floor Press": "horizontal-push",
    "Goblet Squat": "squat",
    "Step-Up": "squat",
    "Dumbbell Hinge": "hinge",
}

# Everyday pairs: (num_pattern, den_pattern, label, target|None). target None =
# trend-only (shown, never flagged — no defensible heuristic target).
PATTERN_PAIRS = [
    ("horizontal-push", "horizontal-pull", "Horizontal push : pull", 1.0),
    ("vertical-push", "vertical-pull", "Vertical push : pull", None),
    ("squat", "hinge", "Squat : hinge", 1.0),
]


def require_fitness_tables(c):
    """Require the migration-owned fitness schema without mutating it."""
    migrations.require_table(c, "fitness_tests", ("degrees", "passed"))
    migrations.require_table(c, "athletic_targets")


def catalog_or_die(mv):
    spec = CATALOG.get(mv)
    if spec is None:
        near = [m for m in CATALOG if mv and (mv in m or m.startswith(mv[:3]))][:5]
        hint = f" — did you mean: {', '.join(near)}?" if near else ""
        sys.exit(f"unknown movement '{mv}'{hint} (see the catalog)")
    return spec


def latest_tests(c, days=ATHLETIC_STALE_DAYS, *, clock):
    return calculations._latest_tests(
        c, days, today=partial(runtime.today, clock=clock), days_ago=partial(runtime.days_ago, clock=clock), catalog=CATALOG, estimate_1rm=e1rm,
    )


def relative_distribution(groups):
    """Normalize one 7-axis profile to its own strongest group (100).

    Strength tests are measured in mean e1RM kg while planned/logged training
    is measured in effective sets. Normalizing each source independently keeps
    the radar honest: it compares balance/shape, never unlike raw units.
    """
    top = max((float(groups.get(g) or 0) for g in MUSCLE_GROUP_AXES),
              default=0.0)
    if top <= 0:
        return {g: 0.0 for g in MUSCLE_GROUP_AXES}
    return {
        g: round(100.0 * float(groups.get(g) or 0) / top, 1)
        for g in MUSCLE_GROUP_AXES
    }


def current_strength_distribution(c):
    """The latest COMPLETE calendar-quarter isolation-strength profile.

    A quarter is complete only when every priority strength movement has the
    required side(s). This prevents a partly started new quarter from replacing
    previous completed test. When no completed baseline exists yet, the latest
    partial quarter is returned instead so already completed movements appear
    immediately; untested groups remain explicit zeros and are listed, never
    inferred. Each unilateral movement first averages its two e1RMs; each
    7-axis group then averages its completed priority movements, so groups with
    a longer protocol do not win merely because they contain more tests. The
    result is a personal protocol profile in mean e1RM kg, not a clinical
    cross-muscle target or a training-volume estimate.
    """
    require_fitness_tables(c)
    required = {
        mv: spec for mv, spec in CATALOG.items()
        if spec["priority"] and spec["kind"] == "strength" and spec["group"]
    }
    quarters = {}
    rows = c.execute("""SELECT id, date, movement, side, load_kg, reps
        FROM fitness_tests WHERE voided=0
        ORDER BY date, id""").fetchall()
    for row in rows:
        if row["movement"] not in required:
            continue
        try:
            d = date.fromisoformat(row["date"])
        except (TypeError, ValueError):
            continue
        qkey = f"{d.year}-Q{((d.month - 1) // 3) + 1}"
        # Ordered rows make the latest same-day retest win, matching
        # _latest_tests and the rest of the fitness engine.
        quarters.setdefault(qkey, {})[(row["movement"], row["side"])] = row

    for qkey in sorted(quarters, reverse=True):
        latest = quarters[qkey]
        missing = []
        for mv, spec in required.items():
            sides = ("left", "right") if spec["unilateral"] else ("bilateral",)
            for side in sides:
                row = latest.get((mv, side))
                if row is None or e1rm(row["load_kg"], row["reps"]) is None:
                    missing.append({"movement": mv, "side": side})
        if missing:
            continue

        per_group = {g: [] for g in MUSCLE_GROUP_AXES}
        used_dates = []
        for mv, spec in required.items():
            sides = ("left", "right") if spec["unilateral"] else ("bilateral",)
            movement_rows = [latest[(mv, side)] for side in sides]
            values = [e1rm(row["load_kg"], row["reps"]) for row in movement_rows]
            per_group[spec["group"]].append(sum(values) / len(values))
            used_dates.extend(row["date"] for row in movement_rows)
        groups = {
            group: (round(sum(values) / len(values), 1) if values else 0.0)
            for group, values in per_group.items()
        }
        return {
            "status": "complete",
            "quarter": qkey,
            "completed_on": max(used_dates),
            "unit": "mean_e1rm_kg",
            "groups": groups,
            "distribution_pct": relative_distribution(groups),
            "method": ("mean e1RM of priority isolation-strength movements; "
                       "left/right averaged before each group"),
        }

    # No complete baseline exists yet. Surface the latest quarter's complete
    # movement pairs instead of collapsing real observations into a centre
    # dot. A unilateral movement contributes only after BOTH sides exist.
    if quarters:
        qkey = sorted(quarters, reverse=True)[0]
        latest = quarters[qkey]
        per_group = {g: [] for g in MUSCLE_GROUP_AXES}
        completed, missing, used_dates = [], [], []
        for mv, spec in required.items():
            sides = ("left", "right") if spec["unilateral"] else ("bilateral",)
            movement_rows = [latest.get((mv, side)) for side in sides]
            values = [e1rm(row["load_kg"], row["reps"]) if row else None
                      for row in movement_rows]
            if any(value is None for value in values):
                missing.append({"movement": mv,
                                "missing_sides": [side for side, value
                                                  in zip(sides, values)
                                                  if value is None]})
                continue
            per_group[spec["group"]].append(sum(values) / len(values))
            completed.append(mv)
            used_dates.extend(row["date"] for row in movement_rows)
        if completed:
            groups = {
                group: (round(sum(values) / len(values), 1) if values else 0.0)
                for group, values in per_group.items()
            }
            tested_groups = [group for group, values in per_group.items() if values]
            return {
                "status": "partial",
                "quarter": qkey,
                "completed_on": max(used_dates),
                "unit": "mean_e1rm_kg",
                "groups": groups,
                "distribution_pct": relative_distribution(groups),
                "completed_movements": len(completed),
                "total_movements": len(required),
                "tested_groups": tested_groups,
                "untested_groups": [g for g in MUSCLE_GROUP_AXES
                                    if g not in tested_groups],
                "missing_movements": missing,
                "method": ("partial baseline: mean e1RM of completed priority "
                           "isolation-strength movements; left/right averaged "
                           "before each group; untested groups are zero"),
            }

    return {
        "status": "insufficient_data",
        "reason": "No completed quarterly strength-balance test yet.",
        "unit": "mean_e1rm_kg",
        "groups": {group: 0.0 for group in MUSCLE_GROUP_AXES},
        "distribution_pct": {group: 0.0 for group in MUSCLE_GROUP_AXES},
    }


def fitness_tests(c, days, *, clock):
    """Read: latest non-voided result per (movement, side), plus priority coverage
    (what the quarter still needs). e1RM computed here, never stored."""
    require_fitness_tables(c)
    latest = latest_tests(c, days, clock=clock)
    tests = []
    for (mv, side), v in sorted(latest.items()):
        spec = CATALOG[mv]
        tests.append({"movement": mv, "name": spec["name"], "side": side,
                      "kind": spec["kind"], "group": spec["group"],
                      "value": v["value"], "date": v["date"],
                      "days_since": v["days_since"],
                      "outside_protocol_range": (
                          spec["kind"] == "strength"
                          and v["reps"] is not None
                          and not 6 <= v["reps"] <= 8)})
    # priority coverage: a movement counts as covered when every side it needs is
    # present THIS CALENDAR QUARTER (both sides for unilateral, the one
    # bilateral entry otherwise). The general `tests` list above still honors
    # --days so older trend data remains visible.
    now = date.fromisoformat(runtime.today(clock=clock))
    quarter_start = date(now.year, 3 * ((now.month - 1) // 3) + 1, 1)
    quarter_latest = latest_tests(c, (now - quarter_start).days, clock=clock)
    quarter = f"{now.year}-Q{((now.month - 1) // 3) + 1}"
    missing = []
    for mv, spec in CATALOG.items():
        if not spec["priority"]:
            continue
        need = ("left", "right") if spec["unilateral"] else ("bilateral",)
        have = [s for s in need if (mv, s) in quarter_latest]
        if len(have) < len(need):
            missing.append({"movement": mv, "name": spec["name"],
                            "missing_sides": [s for s in need if s not in have]})
    core = [m for m, s in CATALOG.items() if s["priority"]]
    return {"days": days, "quarter": quarter, "tests": tests,
         "priority_total": len(core), "priority_missing": missing,
         "priority_covered": len(core) - len(missing)}


def lookup_target(c, axis, lift=""):
    r = c.execute("SELECT target FROM athletic_targets WHERE axis=? AND lift=?",
                  (axis, lift)).fetchone()
    return r["target"] if r else None


def strength_axis(c, *, clock):
    """Strength axis: mean of per-lift scores (best 28-day Hevy working-set e1RM
    vs the user's per-lift target). insufficient_data until a lift target is set
    AND that lift has a recent working set."""
    targets = c.execute("SELECT lift, target FROM athletic_targets WHERE axis='strength'").fetchall()
    if not targets:
        return {"status": "insufficient_data", "reason": "no strength lift target set", "test": "hevy-e1rm"}
    lifts, missing = [], []
    for t in targets:
        row = c.execute("""SELECT weight_kg, reps FROM hevy_sets
            WHERE exercise_title=? AND COALESCE(set_type,'normal')!='warmup'
              AND weight_kg IS NOT NULL AND reps IS NOT NULL AND date >= ?
            ORDER BY weight_kg*(1+reps/30.0) DESC LIMIT 1""",
            (t["lift"], runtime.days_ago(28, clock=clock))).fetchone()
        if row is None:
            missing.append(t["lift"]); continue
        best = e1rm(row["weight_kg"], row["reps"])
        lifts.append({"lift": t["lift"], "e1rm": best, "target": t["target"],
                      "score": axis_score(best, t["target"], "higher")})
    if not lifts:
        return {"status": "insufficient_data", "reason": "no recent working sets for the targeted lifts",
                "test": "hevy-e1rm", "awaiting": missing}
    return {"score": round(sum(x["score"] for x in lifts) / len(lifts)),
            "test": "hevy-e1rm", "lifts": lifts, "missing_lifts": missing}


def athletic_radar(c, *, clock):
    """§3c: 5 axes (Strength, Endurance, Speed, Balance, Flexibility) each scored
    0–100 vs an owner target. insufficient_data until BOTH a target is set and a
    test is logged. Scores older than the quarterly window are kept but flagged
    stale. Balance uses the weaker side (also feeds the §3a left/right radar)."""
    require_fitness_tables(c)
    latest = latest_tests(c, ATHLETIC_STALE_DAYS + 3650, clock=clock)   # wide read; staleness flagged, not hidden
    axes = {}
    for axis, cfg in ATHLETIC_AXES.items():
        if cfg["agg"] == "e1rm_lift":
            axes[axis] = strength_axis(c, clock=clock)
            continue
        mv = cfg["test"]
        target = lookup_target(c, axis)
        if cfg["agg"] == "min_side":
            l, r = latest.get((mv, "left")), latest.get((mv, "right"))
            # weakest-link needs BOTH sides — one side alone can't say which is
            # weaker, and scoring it would overstate the axis.
            if l and r:
                src = min((l, r), key=lambda x: x["value"]); got = src["value"]
            else:
                src, got = None, None
        else:
            src = latest.get((mv, "bilateral"))
            got = src["value"] if src else None
        if got is None or not target:
            reason = ("no target set — athletic-target-set" if not target
                      else "needs both sides logged" if cfg["agg"] == "min_side"
                      else f"no {mv} test logged")
            axes[axis] = {"status": "insufficient_data", "reason": reason,
                          "test": mv, "target": target}
            continue
        better = KIND_BETTER[CATALOG[mv]["kind"]]
        axes[axis] = {"score": axis_score(got, target, better), "test": mv,
                      "result": got, "target": target, "tested_date": src["date"],
                      "stale": src["days_since"] > ATHLETIC_STALE_DAYS,
                      "days_since": src["days_since"]}
    return {"axes": ["strength", "endurance", "speed", "balance", "flexibility"],
         "scores": axes, "stale_after_days": ATHLETIC_STALE_DAYS}


def tested_ratios(c, *, clock):
    return calculations._tested_ratios(
        c, latest_tests=partial(latest_tests, clock=clock), stale_days=ATHLETIC_STALE_DAYS,
        ratio_seed=RATIO_SEED, ratio_flag=ratio_flag,
    )


def pattern_e1rm(c, pattern, *, clock):
    """Best 28-day working-set e1RM across the compound lifts mapped to a pattern."""
    titles = [t for t, p in PATTERN_MAP.items() if p == pattern]
    if not titles:
        return None
    ph = ",".join("?" * len(titles))
    row = c.execute(f"""SELECT weight_kg, reps FROM hevy_sets
        WHERE exercise_title IN ({ph}) AND COALESCE(set_type,'normal')!='warmup'
          AND weight_kg IS NOT NULL AND reps IS NOT NULL AND date >= ?
        ORDER BY weight_kg*(1+reps/30.0) DESC LIMIT 1""",
        (*titles, runtime.days_ago(28, clock=clock))).fetchone()
    return e1rm(row["weight_kg"], row["reps"]) if row else None


def everyday_ratios(c, *, clock):
    """Everyday view: movement-PATTERN balance from compound e1RM. HEURISTIC
    targets, trend-only where none is defensible. Explicitly pattern-level — a
    weak row can't localise to a muscle (that's the tested view's job)."""
    rows = []
    for num_p, den_p, label, target in PATTERN_PAIRS:
        n = pattern_e1rm(c, num_p, clock=clock); d = pattern_e1rm(c, den_p, clock=clock)
        if n is None or d is None or not d:
            rows.append({"label": label, "num_pattern": num_p, "den_pattern": den_p,
                         "status": "insufficient_data", "target": target,
                         "evidence": "heuristic"})
            continue
        ratio = round(n / d, 2)
        flag = "trend_only" if target is None else (
            "in_range" if abs(ratio - target) <= 0.25 * target else "watch")
        rows.append({"label": label, "num_pattern": num_p, "den_pattern": den_p,
                     "ratio": ratio, "num_e1rm": n, "den_e1rm": d, "target": target,
                     "flag": flag, "evidence": "heuristic"})
    unmapped = sorted({r["exercise_title"] for r in c.execute(
        "SELECT DISTINCT exercise_title FROM hevy_sets WHERE exercise_title IS NOT NULL").fetchall()
        if r["exercise_title"] not in PATTERN_MAP})
    return rows, unmapped


def strength_ratios(c, view, *, clock):
    """§3d: agonist:antagonist ratios. Two views — 'tested' (valid, from quarterly
    isolation tests, clinical targets) and 'everyday' (estimate, from compound
    e1RM, heuristic targets, no injury-risk claims). The e1RM-vs-isokinetic caveat
    rides on every payload."""
    require_fitness_tables(c)
    view = (view or "tested").lower()
    caveat = ("Estimated 1RM (Epley) is not isokinetic dynamometry; targets are "
              "guides. Everyday view is movement-pattern level, "
              "not muscle-specific.")
    if view == "everyday":
        rows, unmapped = everyday_ratios(c, clock=clock)
        return {"view": "everyday", "rows": rows, "unmapped_exercises": unmapped,
             "caveat": caveat, "note": "heuristic targets; trend-only flags — never clinical"}
    else:
        return {"view": "tested", "rows": tested_ratios(c, clock=clock), "caveat": caveat,
             "asymmetry_threshold": 0.15}
