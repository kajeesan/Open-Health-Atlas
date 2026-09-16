"""Training: today's session + numbers-to-beat, set logging, per-lift
progression/PR, the 7-axis muscle radar.

Simple reads go through db_read (read-only). ENGINE outputs — the muscle
radar, like /dash/scores — go through the bridge to health.py so their math
has exactly one home (determinism law). The one write — logging a set — goes
through the bridge to `health.py log-set`, which tags the row source='ui' so a
later Hevy reimport (source='hevy' only) can't wipe it. e1RM is Epley
(weight × (1 + reps/30)), computed here from raw sets — display math, not a
stored value, same pattern as the Phase-3 dashboard.
"""
import re
from datetime import date

from flask import Blueprint, jsonify, request

from app import bridge, canon, db_read

bp = Blueprint("training", __name__, url_prefix="/api/training")


def _guard():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    return None


def _e1rm(weight, reps):
    if weight is None or reps is None:
        return None
    return round(weight * (1 + reps / 30.0), 1)


# ── §3c athletic drill pages ────────────────────────────────────────────────
# The 5 athletic axes are a FIXED UI contract, same as the radar's axes list.
# `slug`/`kind`/`test_name` mirror health.py's ATHLETIC_AXES + CATALOG for the
# single-field axes (endurance/speed/balance/flexibility); Strength is the
# multi-lift Hevy-e1RM axis and carries no single test. This is DISPLAY config
# only — the engine (athletic-radar) remains the sole source of truth for every
# SCORE; the drill endpoint only *presents* those engine scores + reads raw
# test history (db_read, SELECT-only) for the progress chart and the
# improving/on-target/below chips.
AXIS_META = {
    "strength":    {"label": "Strength"},
    "endurance":   {"label": "Endurance",   "slug": "run-3k",        "kind": "timed",
                    "test_name": "3 km run",           "unilateral": False},
    "speed":       {"label": "Speed",       "slug": "sprint-30m",    "kind": "timed",
                    "test_name": "30 m sprint",        "unilateral": False},
    "balance":     {"label": "Balance",     "slug": "balance-stand", "kind": "hold",
                    "test_name": "Single-leg balance", "unilateral": True},
    "flexibility": {"label": "Flexibility", "slug": "sit-and-reach", "kind": "distance",
                    "test_name": "Sit-and-reach",      "unilateral": False},
}
# Direction of "better" per test kind — mirrors health.py KIND_BETTER (used only
# to read the improving/below chips off raw history; never to re-score).
KIND_BETTER = {"strength": "higher", "hold": "higher", "distance": "higher", "timed": "lower"}
KIND_UNIT = {"strength": "kg", "hold": "s", "distance": "cm", "timed": "s"}


def _band(score):
    """score→color band. score is the engine's %-of-target (100 = at target):
    good = at/above target, warn = within reach (≥80%), bad = well short."""
    if score is None:
        return "bad"
    if score >= 100:
        return "good"
    if score >= 80:
        return "warn"
    return "bad"


def _fmt_value(kind, v):
    """Display a raw test value in its native unit (timed → mm:ss)."""
    if v is None:
        return "—"
    if kind == "strength":
        return f"{v:g} kg"
    if kind == "timed":
        s = int(round(v))
        return f"{s // 60}:{s % 60:02d}"
    if kind == "hold":
        return f"{v:g} s"
    if kind == "distance":
        return f"{v:g} cm"
    return f"{v:g}"


def _chip(value, target, prev, better):
    """Deterministic field-test status chip:
      • 'on target' when the latest value meets/beats the owner target,
      • else 'improving' when it beat the PREVIOUS test of the same kind,
      • else 'below'. None when target-vs-value can't be judged (no target).
    `better` sets the comparison direction (timed = lower is better)."""
    met = None
    if value is not None and target is not None:
        met = value >= target if better == "higher" else value <= target
    improving = None
    if value is not None and prev is not None:
        improving = value > prev if better == "higher" else value < prev
    if met is True:
        return "on target"
    if improving is True:
        return "improving"
    if met is False:
        return "below"
    return None


def _hevy_best_by_date(lift):
    """Best e1RM per session date for one lift (Epley, the same display math as
    the progression chart). Feeds the strength drill's progress + chips."""
    rows = db_read.query(
        "SELECT date, weight_kg, reps FROM hevy_sets"
        " WHERE exercise_title = ? AND COALESCE(set_type,'normal') != 'warmup'"
        " AND weight_kg IS NOT NULL AND reps IS NOT NULL AND date IS NOT NULL"
        " ORDER BY date", (lift,))
    best = {}
    for r in rows:
        e = _e1rm(r["weight_kg"], r["reps"])
        if e is not None and e > best.get(r["date"], 0.0):
            best[r["date"]] = e
    return [(d, best[d]) for d in sorted(best)]


def _fitness_history(slug, kind):
    """Raw value per test date for a bilateral movement (SELECT-only). `col` is
    a fixed literal from the kind, never user input."""
    col = "cm" if kind == "distance" else "seconds"
    rows = db_read.query(
        f"SELECT date, {col} AS v FROM fitness_tests"
        f" WHERE movement = ? AND side = 'bilateral' AND voided = 0"
        f" AND date IS NOT NULL AND {col} IS NOT NULL ORDER BY date", (slug,))
    return [(r["date"], r["v"]) for r in rows]


def _fitness_history_sides(slug):
    """Per-side (left/right) seconds history for a unilateral movement."""
    rows = db_read.query(
        "SELECT date, side, seconds AS v FROM fitness_tests"
        " WHERE movement = ? AND side IN ('left','right') AND voided = 0"
        " AND date IS NOT NULL AND seconds IS NOT NULL ORDER BY date", (slug,))
    out = {"left": [], "right": []}
    for r in rows:
        out[r["side"]].append((r["date"], r["v"]))
    return out


def _weaker_by_date(sides):
    """Weaker-side value per date that has BOTH sides — mirrors the engine's
    Balance min-side aggregation, so the progress line matches the axis score."""
    left, right = dict(sides.get("left") or []), dict(sides.get("right") or [])
    return [(d, min(left[d], right[d])) for d in sorted(set(left) & set(right))]


def _strength_detail(result, sc):
    """Strength axis: one sub-test bar + field-test row + progress line per
    targeted lift (from the engine's own per-lift scores; history from Hevy)."""
    lifts = sc.get("lifts") or []
    result["score"] = sc.get("score")
    result["missing_lifts"] = sc.get("missing_lifts") or []
    result["target_caption"] = ("bar = % of your per-lift e1RM target · "
                                "dashed line = target (100%)")
    sub, fields, series, below = [], [], [], []
    for lf in lifts:
        name, e1, tgt, s = lf["lift"], lf["e1rm"], lf["target"], lf["score"]
        sub.append({"name": name, "score": s, "band": _band(s)})
        hist = _hevy_best_by_date(name)
        prev = hist[-2][1] if len(hist) >= 2 else None
        fields.append({"name": name, "value_txt": _fmt_value("strength", e1),
                       "chip": _chip(e1, tgt, prev, "higher")})
        pts = [{"date": d, "value": v} for d, v in hist[-5:]]
        if pts:
            series.append({"name": name, "points": pts})
        if s < 100:
            below.append((s, name, e1, tgt))
    result["sub_tests"] = sub
    result["field_tests"] = fields
    result["progress"] = {"series": series, "unit": "kg", "better": "higher",
                          "caption": "e1RM per targeted lift over your recent sessions "
                                     "(kg, higher is better)"}
    below.sort()
    if below:
        s0, n0, e0, t0 = below[0]
        status = (f"{n0} is furthest below target — {s0}% "
                  f"({_fmt_value('strength', e0)} vs {_fmt_value('strength', t0)}).")
        bullets = [f"{n}: {s}% of target ({_fmt_value('strength', e)} → "
                   f"{_fmt_value('strength', t)})" for s, n, e, t in below[:3]]
    else:
        status = "Every targeted lift is at or above its e1RM target."
        bullets = []
    if result["missing_lifts"]:
        bullets.append("No recent working sets for: "
                       + ", ".join(result["missing_lifts"]) + " — log to score.")
    result["summary"] = {"status_txt": status, "bullets": bullets}


def _field_detail(result, axis, meta, sc):
    """Single-field axes (endurance/speed/balance/flexibility): one scored
    sub-test bar, the latest field value(s) + chip, and a raw-value progress
    line over test history."""
    kind, slug = meta["kind"], meta["slug"]
    better = KIND_BETTER[kind]
    score, tgt, res_val = sc.get("score"), sc.get("target"), sc.get("result")
    result["score"] = score
    result["stale"] = bool(sc.get("stale"))
    result["days_since"] = sc.get("days_since")
    tgt_txt = _fmt_value(kind, tgt)
    result["target_caption"] = f"bar = % of your target · dashed line = target (100% = {tgt_txt})"
    result["sub_tests"] = [{"name": meta["test_name"], "score": score, "band": _band(score)}]
    direction = "higher is better" if better == "higher" else "lower is better"
    if meta["unilateral"]:
        sides = _fitness_history_sides(slug)
        fields = []
        for side in ("left", "right"):
            h = sides.get(side) or []
            if not h:
                continue
            val, prev = h[-1][1], (h[-2][1] if len(h) >= 2 else None)
            fields.append({"name": f"{meta['test_name']} ({side})",
                           "value_txt": _fmt_value(kind, val),
                           "chip": _chip(val, tgt, prev, better)})
        result["field_tests"] = fields
        weaker = _weaker_by_date(sides)
        pts = [{"date": d, "value": v} for d, v in weaker[-5:]]
        result["progress"] = {"series": [{"name": "weaker side", "points": pts}] if pts else [],
                              "unit": KIND_UNIT[kind], "better": better,
                              "caption": f"Weaker-side {meta['test_name'].lower()} over your "
                                         f"recent tests ({direction})"}
    else:
        hist = _fitness_history(slug, kind)
        prev = hist[-2][1] if len(hist) >= 2 else None
        result["field_tests"] = [{"name": meta["test_name"],
                                  "value_txt": _fmt_value(kind, res_val),
                                  "chip": _chip(res_val, tgt, prev, better)}]
        pts = [{"date": d, "value": v} for d, v in hist[-5:]]
        result["progress"] = {"series": [{"name": meta["test_name"], "points": pts}] if pts else [],
                              "unit": KIND_UNIT[kind], "better": better,
                              "caption": f"{meta['test_name']} over your recent tests ({direction})"}
    status = (f"{meta['label']} is at {score}% of target "
              f"({_fmt_value(kind, res_val)} vs target {tgt_txt}).")
    bullets = []
    gap = (tgt - res_val) if better == "higher" else (res_val - tgt)
    if score is not None and score < 100 and gap and gap > 0:
        bullets.append(f"{_fmt_value(kind, gap)} {'below' if better == 'higher' else 'over'} "
                       "target — retest after a training block.")
    if result["stale"]:
        bullets.append(f"Last tested {result['days_since']} days ago — log a fresh test.")
    result["summary"] = {"status_txt": status, "bullets": bullets}


@bp.get("/today")
def today():
    """Scheduled routine + each exercise's target and last-session sets to beat."""
    if (resp := _guard()) is not None:
        return resp
    sched = db_read.query(
        "SELECT routine_name FROM training_schedule WHERE weekday = ?",
        (canon.weekday(),))
    routine = sched[0]["routine_name"] if sched else None
    if not routine or routine == "Rest":
        return jsonify(routine=routine or "Rest", exercises=[])

    exercises = db_read.query(
        "SELECT exercise_title, target_sets, target_reps, target_weight_kg"
        " FROM routines WHERE routine_name = ? ORDER BY ex_order, exercise_title", (routine,))

    # Last logged session per exercise (numbers to beat), one query for the lot.
    titles = [e["exercise_title"] for e in exercises]
    last_by_ex = {}
    if titles:
        placeholders = ",".join("?" * len(titles))
        rows = db_read.query(
            "SELECT h.exercise_title, h.date, h.set_index, h.weight_kg, h.reps"
            " FROM hevy_sets h JOIN ("
            "   SELECT exercise_title, MAX(date) md FROM hevy_sets"
            f"   WHERE COALESCE(set_type,'normal') != 'warmup' AND exercise_title IN ({placeholders})"
            "   GROUP BY exercise_title) last"
            " ON last.exercise_title = h.exercise_title AND last.md = h.date"
            " WHERE COALESCE(h.set_type,'normal') != 'warmup'"
            " ORDER BY h.exercise_title, h.set_index", titles)
        for r in rows:
            last_by_ex.setdefault(r["exercise_title"], {"date": r["date"], "sets": []})
            last_by_ex[r["exercise_title"]]["sets"].append(
                {"weight_kg": r["weight_kg"], "reps": r["reps"]})

    for e in exercises:
        e["last_session"] = last_by_ex.get(e["exercise_title"])
    return jsonify(routine=routine, exercises=exercises)


@bp.get("/progression")
def progression():
    """Per-session best set for one exercise: top weight + best e1RM over time,
    with a running-max PR flag."""
    if (resp := _guard()) is not None:
        return resp
    exercise = (request.args.get("exercise") or "").strip()
    if not exercise:
        return jsonify(error="exercise is required"), 400
    rows = db_read.query(
        "SELECT date, weight_kg, reps FROM hevy_sets"
        " WHERE exercise_title = ? AND COALESCE(set_type,'normal') != 'warmup'"
        " AND weight_kg IS NOT NULL AND reps IS NOT NULL AND date IS NOT NULL"
        " ORDER BY date", (exercise,))
    per_date = {}
    for r in rows:
        e = _e1rm(r["weight_kg"], r["reps"])
        cur = per_date.get(r["date"])
        if cur is None:
            per_date[r["date"]] = {"date": r["date"], "top_weight": r["weight_kg"],
                                   "reps": r["reps"], "e1rm": e}
            continue
        # top_weight is the heaviest set of the day, independent of which set
        # has the best e1RM (a heavier low-rep single can precede a higher-e1RM
        # backoff set — don't let it get overwritten).
        cur["top_weight"] = max(cur["top_weight"], r["weight_kg"])
        if e > cur["e1rm"]:
            cur["e1rm"] = e
            cur["reps"] = r["reps"]
    points = sorted(per_date.values(), key=lambda p: p["date"])
    best = 0.0
    for p in points:
        p["pr"] = p["e1rm"] > best
        best = max(best, p["e1rm"])
    return jsonify(exercise=exercise, points=points)


@bp.get("/overall-progress")
def overall_progress():
    """Combined strength across ALL logged lifts + a weight-to-strength ratio.

    Two deterministic, server-side series (charted client-side):

    Overall strength index — the default Progression view, answering "how
    strong is my body compared to the past" across every lift:
      • Bucket every non-warmup set into its ISO week; per week × exercise keep
        that week's best e1RM (Epley, the same helper the per-lift chart uses).
      • Normalize each exercise to its OWN first-recorded week = 100, so a
        heavy lift can't drown out a light one — each contributes % change.
      • Carry an exercise's last-known e1RM forward over weeks it wasn't
        trained, but ONLY after its first record (never back-fill before it
        existed).
      • Weekly index = mean of the indexed values of the exercises that have
        started by that week. 100 = your starting strength across your lifts.

    Weight-to-strength ratio — relative strength over time:
      • For each week, sum the carried-forward best e1RMs (kg) of the live
        exercises and divide by the body weight recorded nearest at-or-before
        the end of that week.
      • Weeks before any body-weight record → no ratio point; no body weight
        at all → wsr empty + has_weight=False (the card shows an honest empty).

    Weeks are keyed by their Monday (ISO week start) for the time axis.
    """
    if (resp := _guard()) is not None:
        return resp
    rows = db_read.query(
        "SELECT date, exercise_title, weight_kg, reps FROM hevy_sets"
        " WHERE COALESCE(set_type,'normal') != 'warmup' AND weight_kg IS NOT NULL"
        " AND reps IS NOT NULL AND date IS NOT NULL AND exercise_title IS NOT NULL"
        " ORDER BY date")

    # best e1RM per (ISO-week-Monday, exercise)
    best = {}
    for r in rows:
        try:
            y, w, _ = date.fromisoformat(r["date"]).isocalendar()
        except ValueError:
            continue    # skip a malformed date rather than crash the chart
        wk = date.fromisocalendar(y, w, 1).isoformat()
        e = _e1rm(r["weight_kg"], r["reps"])
        key = (wk, r["exercise_title"])
        if e is not None and e > best.get(key, 0.0):
            best[key] = e

    by_ex = {}
    for (wk, ex), e in best.items():
        by_ex.setdefault(ex, {})[wk] = e
    weeks = sorted({wk for (wk, _ex) in best})

    # body weights ascending, for the nearest-at-or-before lookup
    weights = [(r["date"], r["weight_kg"]) for r in db_read.query(
        "SELECT date, weight_kg FROM body_metrics"
        " WHERE weight_kg IS NOT NULL AND date IS NOT NULL ORDER BY date")]

    def weight_at_or_before(cutoff):
        chosen = None
        for d, kg in weights:      # ascending — the last one <= cutoff wins
            if d <= cutoff:
                chosen = kg
            else:
                break
        return chosen

    carried, base = {}, {}
    index_points, wsr_points = [], []
    for wk in weeks:
        for ex, wkmap in by_ex.items():
            if wk in wkmap:
                carried[ex] = wkmap[wk]
                base.setdefault(ex, wkmap[wk])   # first-record week = 100
        live = list(carried)                     # exercises started by this week
        vals = [carried[ex] / base[ex] * 100 for ex in live]
        index_points.append({"week": wk, "value": round(sum(vals) / len(vals), 1)})
        y, w, _ = date.fromisoformat(wk).isocalendar()
        cutoff = date.fromisocalendar(y, w, 7).isoformat()   # Sunday of the week
        kg = weight_at_or_before(cutoff)
        if kg:
            total = sum(carried[ex] for ex in live)
            wsr_points.append({"week": wk, "value": round(total / kg, 3),
                               "total_e1rm": round(total, 1), "weight_kg": kg})

    return jsonify(index=index_points, wsr=wsr_points,
                   exercise_count=len(by_ex), has_weight=bool(weights))


@bp.get("/exercises")
def exercises():
    """Distinct exercises that have logged sets — the progression picker offers
    these on top of today's routine, so any past lift can be charted."""
    if (resp := _guard()) is not None:
        return resp
    rows = db_read.query(
        "SELECT DISTINCT exercise_title FROM hevy_sets"
        " WHERE exercise_title IS NOT NULL ORDER BY exercise_title")
    return jsonify(exercises=[r["exercise_title"] for r in rows])


@bp.get("/muscle-radar")
def muscle_radar():
    """The 7-axis Muscle Balance payload passes through verbatim from health.py:
    latest completed quarterly strengths, planned weekly distribution, Logged
    7d volume, and mapping diagnostics are computed in the engine, never here."""
    try:
        return jsonify(ok=True, result=bridge.run(
            "muscle-volume", "--by", "group", "--days", "7"))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/athletic-radar")
def athletic_radar():
    """§3c: 5-axis athletic radar. Engine JSON passes through verbatim — all
    scoring (target-anchored 0–100, insufficient_data, staleness) is health.py."""
    try:
        return jsonify(ok=True, result=bridge.run("athletic-radar"))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/strength-ratios")
def strength_ratios():
    """§3d: agonist:antagonist ratios. `view` = tested|everyday; everything
    (ratios, cited targets, flags, caveats) is computed in health.py."""
    view = "everyday" if request.args.get("view") == "everyday" else "tested"
    try:
        return jsonify(ok=True, result=bridge.run("strength-ratios", "--view", view))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/fitness-tests")
def fitness_tests():
    """§3e: latest test per movement/side + quarter (priority) coverage."""
    try:
        return jsonify(ok=True, result=bridge.run("fitness-tests", "--days", "120"))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/vtaper")
def vtaper():
    """§3f: V-taper WCR (waist ÷ chest, target ~0.7 — Garza 2017). Engine JSON
    verbatim; the ratio, target, window default and honesty states are all
    health.py's (no flags sent — one source of truth for the default)."""
    try:
        return jsonify(ok=True, result=bridge.run("vtaper"))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/muscle-detail")
def muscle_detail():
    """§3b drill-down: exercises driving one radar group + the sub-region
    breakdown (engine-reported `pending_source` until the owner's cited map
    lands). Group vocabulary is validated by health.py — one home."""
    group = (request.args.get("group") or "").strip()
    if not group or group.startswith("-"):
        return jsonify(ok=False, error="a muscle group is required"), 400
    try:
        return jsonify(ok=True, result=bridge.run("muscle-detail", group))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/muscle-map")
def muscle_map():
    """§3f body figure: per-SVG-region display payload for the muscle map.
    Lens vocabulary is validated by health.py (argparse choices — one home);
    the panel only pins the token SHAPE: lowercase-ascii-with-hyphens, so the
    planned lens names (strength-balance, pain, mobility) pass while path
    tricks and flag injection cannot (review: isidentifier() rejected
    hyphens and admitted non-ASCII identifiers)."""
    lens = (request.args.get("lens") or "activation").strip()
    if not re.fullmatch(r"[a-z][a-z-]{0,31}", lens):
        return jsonify(ok=False, error="unknown lens"), 400
    args = ["--lens", lens]
    # §3f Phase 2: strength-balance's Combined|L/R sub-view. Same posture as
    # the lens token: the panel pins SHAPE only, health.py's argparse choices
    # is the vocabulary home (a bad value comes back as a 502 from there).
    side = (request.args.get("side") or "").strip()
    if side:
        if not re.fullmatch(r"[a-z][a-z-]{0,15}", side):
            return jsonify(ok=False, error="unknown side mode"), 400
        args += ["--side-mode", side]
    try:
        return jsonify(ok=True, result=bridge.run("muscle-map", *args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/athletic-detail")
def athletic_detail():
    """§3c drill-down: the per-axis breakdown behind one athletic-radar spoke.

    Composes the engine's `athletic-radar` (the sole SCORE authority) with
    raw test-history reads (db_read, SELECT-only) — NO bridge subcommand is
    added. Returns a uniform shape the shared drill page renders: scored
    sub-test bars, latest field values + deterministic chips, a progress line
    over test history, and deterministic status prose. Honest empty (status
    insufficient_data) until a target is set AND a test is logged."""
    if (resp := _guard()) is not None:
        return resp
    axis = (request.args.get("axis") or "").strip().lower()
    if axis not in AXIS_META:
        return jsonify(ok=False, error="unknown athletic axis"), 400
    meta = AXIS_META[axis]
    try:
        radar = bridge.run("athletic-radar")
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502
    sc = (radar.get("scores") or {}).get(axis) or {}
    result = {"axis": axis, "label": meta["label"],
              "stale_after_days": radar.get("stale_after_days")}
    if "score" not in sc:
        # insufficient_data: no target set and/or no test logged (the honest,
        # expected local state — the demo DB seeds neither).
        result["status"] = "insufficient_data"
        result["reason"] = sc.get("reason") or "No data for this axis yet."
        return jsonify(ok=True, result=result)
    result["status"] = "ok"
    if axis == "strength":
        _strength_detail(result, sc)
    else:
        _field_detail(result, axis, meta, sc)
    return jsonify(ok=True, result=result)


@bp.post("/log-set")
def log_set():
    """Log one strength set via the bridge -> health.py log-set (source='ui')."""
    body = request.get_json(silent=True) or {}
    exercise = (body.get("exercise") or "").strip()
    if not exercise or exercise.startswith("-"):
        return jsonify(ok=False, error="a valid exercise is required"), 400
    args = [exercise]
    try:
        reps = body.get("reps")
        weight = body.get("weight_kg")
        if reps not in (None, ""):
            fr = float(reps)                 # reject fractional reps rather than
            if not fr.is_integer():          # silently truncating (e.g. 3.9 -> 3)
                return jsonify(ok=False, error="reps must be a whole number"), 400
            args += ["--reps", str(int(fr))]
        if weight not in (None, ""):
            args += ["--weight", str(float(weight))]
        if body.get("rpe") not in (None, ""):
            args += ["--rpe", str(float(body["rpe"]))]
    except (TypeError, ValueError):
        return jsonify(ok=False, error="weight/reps/rpe must be numbers"), 400
    if "--reps" not in args and "--weight" not in args:
        return jsonify(ok=False, error="a set needs at least reps or weight"), 400
    if body.get("workout_title"):
        args += ["--workout-title", str(body["workout_title"])[:80]]
    try:
        return jsonify(ok=True, result=bridge.run("log-set", *args))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400
