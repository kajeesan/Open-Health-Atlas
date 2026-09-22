#!/usr/bin/env python3
"""
health.py — the deterministic command layer for a Hermes health database.

The LLM coach NEVER writes raw numbers. It only maps intent -> a call here,
and this script validates inputs, does all math, and inserts. Every write
prints a confirmation so a wrong match is caught immediately.

DB path: $HEALTH_DB or $HERMES_DATA_DIR/health.db
Run `python3 health.py --help` for commands.
"""
import csv, io, itertools, json, math, os, re, sqlite3, statistics as st, sys
from datetime import date, datetime, timedelta, timezone

from hermes_insights import cli as insight_cli
from hermes_insights.command_context import CommandContext
from hermes_insights.commands import (
    cronometer as cronometer_commands, google_health as google_health_commands,
    hevy as hevy_commands, lab_catalog as lab_catalog_commands,
    recipes as recipe_commands, submuscle_map as submuscle_commands,
)
from hermes_insights.fitness_contracts import KIND_FIELDS, FT_CLAMPS
from hermes_insights.importers.common import stdin_text, valid_date
from hermes_insights.importers.cronometer import MICRO_CITATION_STATUS
from hermes_insights.importers.hevy_json import load_quarterly_config
from hermes_insights.importers.lab_catalog import LAB_CATALOG_NOTE
from hermes_insights.routine_history import (
    require_history as _ensure_routines_history, snapshot as _snap,
)
from hermes_insights.commands.hevy import import_csv as import_hevy_csv
from hermes_insights.importers.hevy_csv import parse_date as _hevy_date
from hermes_insights import calculations as insight_calculations
from hermes_insights import catalogs as insight_catalogs
from hermes_insights import runtime as insight_runtime
from hermes_insights.catalogs import (
    ATHLETIC_AXES, ATHLETIC_STALE_DAYS, CATALOG, CONFIGURED_MICRO_TARGETS,
    DM_METRICS, DM_PRIMARY, KIND_BETTER, MEDICATION_ALIASES, MICRO_KEYS,
    MICRO_SEED, MICRO_TARGET_EXTRA, MOBILITY_EXERCISES, MOBILITY_NORM,
    MUSCLE_GROUP_AXES, MUSCLE_TO_GROUP, NON_VOLUME_EXERCISES, PAIN_CAUSE_MAP,
    PRIMARY_MEDICATION, RATIO_SEED, REHAB_CATALOG, RUN_TYPE_KEYS,
    SELF_TEST_CATALOG, SIT_REACH_NORMAL_CM, _SUP,
)
from hermes_insights import events as insight_events
from hermes_insights import frame as insight_frame
from hermes_insights import goals as insight_goals
from hermes_insights import migrations as insight_migrations
from hermes_insights import readiness as insight_readiness
from hermes_insights import readiness_ancestry as insight_readiness_ancestry
from hermes_insights import registry as insight_registry
from hermes_insights import associations as insight_associations
from hermes_insights import ledger as insight_ledger
from hermes_insights import orchestrator as insight_orchestrator
from hermes_insights import provenance as insight_provenance
from hermes_insights import synthesis as insight_synthesis
from hermes_insights.contracts import AdapterContext, DateRange, canonical_json
from hermes_insights.settings import CANON_TZ, TIMEZONE_NAME, resolve_vault_root

DATA_DIR = insight_runtime.DATA_DIR
DB = insight_runtime.DB

# Every date decision goes through today()/_now() so the CLI, panel, and
# insight engine agree on the deployment-configured civil day.

def _now():
    return insight_runtime.now(datetime_type=datetime, timezone=CANON_TZ)


def cx():
    return insight_runtime.connect(DB)


_RO_ACTIONS = insight_runtime.RO_ACTIONS
_RO_SCHEMA_PRAGMAS = insight_runtime.RO_SCHEMA_PRAGMAS


def _ro_authorizer(action, a1, a2, _db, _trig):
    return insight_runtime.read_only_authorizer(
        action, a1, a2, _db, _trig,
        actions=_RO_ACTIONS, schema_pragmas=_RO_SCHEMA_PRAGMAS,
    )


def cx_ro():
    return insight_runtime.connect_read_only(DB, authorizer=_ro_authorizer)


def today():
    return insight_runtime.today(clock=_now)


def days_ago(n):
    return insight_runtime.days_ago(n, clock=_now)

def num(x):
    try: return float(x) if x not in (None, "", "null", "NaN") else None
    except: return None
def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")
def out(d):
    insight_cli.out(d)

def _require_schema(c, table, *columns):
    """Fail closed without DDL when an explicit migration has not run."""
    insight_migrations.require_table(c, table, tuple(columns))

# ---- tables the coach may write to via `log`, with their allowed columns ----
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
# Loggable tables keyed by a single `date` PK: a second log the same day must
# UPDATE that day's row, not raise a UNIQUE error (so re-logging mood/water works).
UPSERT_DATE_TABLES = {"subjective_daily", "intake", "sleep_log"}

# =========================================================== ingestion
def _ensure_hevy_source(c):
    """Require migration-owned provenance; never alter schema lazily."""
    _require_schema(c, "hevy_sets", "source")

def _command_context():
    return CommandContext(
        database=DB, clock=_now, timezone=TIMEZONE_NAME,
        vault=resolve_vault_root(DB), cli_path=__file__,
    )


def import_hevy(a):
    """Compatibility entry point for the extracted Hevy CSV command."""
    out(import_hevy_csv(_command_context(), a.csv, parse_number=num))


# Exact Hevy routine/template identities are external account configuration.
# Title-only fuzzy matching remains forbidden because ordinary workouts can
# contain the same exercises as the fixed quarterly measurement protocol.
HEVY_QUARTERLY_ROUTINES, HEVY_QUARTERLY_UNILATERAL_TITLES = (
    load_quarterly_config(os.environ.get("HERMES_HEVY_QUARTERLY_CONFIG"))
)


def import_hevy_json(a):
    """Compatibility entry point for the extracted import command."""
    out(hevy_commands.import_json(
        _command_context(), a.json_file, force=a.force, parse_number=num,
        quarterly_routines=HEVY_QUARTERLY_ROUTINES))


def _ensure_muscle_source(c):
    """Require migration-owned provenance; never alter schema lazily."""
    _require_schema(c, "exercise_muscles", "source")


def import_hevy_templates(a):
    """Compatibility entry point for the extracted import command."""
    out(hevy_commands.import_templates(_command_context(), a.json_file))


def import_hevy_routines(a):
    """Compatibility entry point for the extracted import command."""
    out(hevy_commands.import_routines(
        _command_context(), a.json_file, parse_number=num))


def _ensure_body_source(c):
    """Require migration-owned provenance; never alter schema lazily."""
    _require_schema(c, "body_metrics", "source")


def import_hevy_body(a):
    """Compatibility entry point for the extracted import command."""
    out(hevy_commands.import_body(
        _command_context(), a.json_file, parse_number=num))


# =========================================================== training layer
def log_set(a):
    """Log ONE strength set from the UI into hevy_sets, tagged source='ui'.
    set_index auto-increments per exercise per day so multiple sets stack, and
    the source tag is what protects these rows from the import-hevy full reload
    (which only deletes source='hevy'). Validated; all math stays deterministic."""
    ex = (a.exercise or "").strip()
    if not ex:
        sys.exit("exercise is required")
    reps = a.reps
    if reps is not None and not (1 <= reps <= 100):
        sys.exit("reps must be 1-100")
    w = num(a.weight)
    if w is not None and not (0 <= w <= 1000):
        sys.exit("weight_kg must be 0-1000")
    if reps is None and w is None:
        sys.exit("a set needs at least reps or weight")
    rpe = num(a.rpe)
    if rpe is not None and not (1 <= rpe <= 10):
        sys.exit("rpe must be 1-10")
    c = cx(); _ensure_hevy_source(c)
    d = valid_date(a.date) if a.date else today()
    idx = a.set_index
    if idx is None:
        row = c.execute("SELECT COALESCE(MAX(set_index),0)+1 AS n FROM hevy_sets "
                        "WHERE date=? AND exercise_title=?", (d, ex)).fetchone()
        idx = row["n"]
    c.execute("""INSERT INTO hevy_sets(date, workout_title, exercise_title, set_index,
                 set_type, weight_kg, reps, rpe, source)
                 VALUES(?,?,?,?,?,?,?,?, 'ui')""",
              (d, a.workout_title, ex, idx, a.set_type or "normal", w, reps, rpe))
    c.commit()
    out({"ok": True, "logged_set": {"date": d, "exercise": ex, "set_index": idx,
         "weight_kg": w, "reps": reps, "rpe": rpe, "source": "ui"}})

def today_session(a):
    """What's on today: the scheduled routine + its exercises with target sets/reps/starting weight."""
    d = a.date or today()
    wd = datetime.strptime(d, "%Y-%m-%d").strftime("%a")   # Mon, Tue, ...
    c = cx()
    sch = c.execute("SELECT routine_name FROM training_schedule WHERE weekday=?", (wd,)).fetchone()
    routine = sch["routine_name"] if sch else None
    if not routine or routine == "Rest":
        out({"date": d, "weekday": wd, "routine": "Rest", "exercises": []}); return
    ex = c.execute("""SELECT exercise_title, target_sets, target_reps, target_weight_kg
                      FROM routines WHERE routine_name=? ORDER BY ex_order, exercise_title""", (routine,)).fetchall()
    out({"date": d, "weekday": wd, "routine": routine,
         "exercises": [dict(r) for r in ex]})


# ── §3e/§3c/§3d fitness testing ──────────────────────────────────────────────
# CATALOG is the fixed test vocabulary — one source of truth in code (like
# MUSCLE_TO_GROUP), so a typo can't invent a movement and every consumer knows
# exactly what a row feeds. movement slug -> dict(name, kind, unilateral, group,
# ref, priority). `better` is a pure function of kind (KIND_BETTER), never stored.
#   kind: strength (load×reps → e1RM) | hold (seconds, ↑) | timed (seconds, ↓)
#         | control (rating 1–3, ↑) | distance (cm, ↑)
#         | rom (degrees, ↑) | binary (passed 0/1, ↑)   ← Phase 4 mobility
# rom/binary are the two mobility units (design rule 2 — per-test unit): shoulder
# and ankle ROM read in degrees, the Thomas hip-flexor screen reads pass/fail.


def _ft(name, kind, group=None, ref=None, unilateral=0, priority=0):
    return insight_catalogs._ft(name, kind, group, ref, unilateral, priority)


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


def e1rm(load, reps):
    return insight_calculations.e1rm(load, reps)


def _ensure_fitness_tables(c):
    """Require the migration-owned fitness schema without mutating it."""
    _require_schema(c, "fitness_tests", "degrees", "passed")
    _require_schema(c, "athletic_targets")


def _muscle_rows(c, source, days=7):
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
            GROUP BY em.muscle ORDER BY eff_sets DESC""", (days_ago(days),)).fetchall()
    return c.execute("""SELECT em.muscle, ROUND(SUM(COALESCE(r.target_sets*em.weight,0)),1) AS eff_sets
        FROM training_schedule ts JOIN routines r ON r.routine_name=ts.routine_name
        JOIN exercise_muscles em ON em.exercise_title=r.exercise_title
        GROUP BY em.muscle ORDER BY eff_sets DESC""").fetchall()


def _set_counts(c, source, days=7, anchor=None):
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
            GROUP BY h.exercise_title""", (days_ago(days),))}
    return {r["t"]: r["n"] for r in c.execute("""SELECT
            r.exercise_title t, SUM(COALESCE(r.target_sets,0)) n
        FROM training_schedule ts JOIN routines r ON r.routine_name=ts.routine_name
        GROUP BY r.exercise_title""")}


def _activation_set_counts(c, days=7):
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
        (days_ago(days),)).fetchall()
    quarterly_titles = {cfg["title"] for cfg in HEVY_QUARTERLY_ROUTINES.values()}
    ordinals, counts = {}, {}
    for r in rows:
        title = r["exercise_title"]
        side = "bilateral"
        if (r["workout_title"] in quarterly_titles
                and title in HEVY_QUARTERLY_UNILATERAL_TITLES):
            key = (r["date"], r["start_time"], r["workout_title"], title)
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            side = ("left", "right")[ordinal] if ordinal < 2 else "bilateral"
        per = counts.setdefault(title, {})
        per[side] = per.get(side, 0) + 1
    return counts


def _group_weight_maps(c):
    return insight_calculations._group_weight_maps(c, muscle_to_group=MUSCLE_TO_GROUP)


def _basis_weights(title, authored, coarse):
    return insight_calculations._basis_weights(
        title, authored, coarse, mobility_exercises=MOBILITY_EXERCISES,
        non_volume_exercises=NON_VOLUME_EXERCISES,
    )


def _rollup7(c, source, days=7, anchor=None):
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
    _ensure_submuscle_table(c)
    counts = _set_counts(c, source, days, anchor=anchor)
    authored, coarse = _group_weight_maps(c)
    groups = {g: 0.0 for g in MUSCLE_GROUP_AXES}
    present, unmapped = set(), set()
    fallback, mobility, non_volume, unmatched = [], [], [], []
    for title, n in sorted(counts.items()):
        gmap, basis = _basis_weights(title, authored, coarse)
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


def _relative_distribution(groups):
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


def muscle_volume(a):
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
    c = cx()
    if a.by == "group":
        if a.source is not None:
            sys.exit("--source does not combine with --by group (it returns all views)")
        lg = _rollup7(c, "logged", a.days)
        pl = _rollup7(c, "planned")
        current = _current_strength_distribution(c)
        out({"by": "group", "window_days": a.days, "axes": MUSCLE_GROUP_AXES,
             "current_strengths": current,
             "combined": lg["groups"], "planned": pl["groups"],
             "logged_distribution_pct": _relative_distribution(lg["groups"]),
             "planned_distribution_pct": _relative_distribution(pl["groups"]),
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
                                      "captured yet"}})
        return
    source = a.source or "planned"
    rows = _muscle_rows(c, source, a.days)
    if source == "logged":
        out({"source": "logged", "window_days": a.days,
             "weekly_effective_sets": {r["muscle"]: r["eff_sets"] for r in rows}})
    else:
        out({"source": "planned",
             "weekly_effective_sets": {r["muscle"]: r["eff_sets"] for r in rows}})

def last_session(a):
    """Numbers to beat: the most recent logged session for an exercise (each set's weight x reps)."""
    c = cx()
    row = c.execute("""SELECT MAX(date) d FROM hevy_sets WHERE exercise_title=? AND date IS NOT NULL""",
                    (a.exercise,)).fetchone()
    if not row or not row["d"]:
        # fall back to the prescribed target so the coach still has a number
        t = c.execute("SELECT target_sets,target_reps,target_weight_kg FROM routines WHERE exercise_title=? LIMIT 1",
                      (a.exercise,)).fetchone()
        out({"exercise": a.exercise, "logged": False,
             "prescribed": dict(t) if t else None}); return
    sets = c.execute("""SELECT set_index, weight_kg, reps, rpe FROM hevy_sets
                        WHERE exercise_title=? AND date=? ORDER BY set_index""",
                     (a.exercise, row["d"])).fetchall()
    out({"exercise": a.exercise, "logged": True, "last_date": row["d"],
         "sets": [dict(s) for s in sets]})

# =========================================================== program editing
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def routine_set(a):
    """Add or edit an exercise's targets in a routine (upsert). Snapshots the
    prior state to routines_history so the change can be undone."""
    routine = (a.routine or "").strip(); exercise = (a.exercise or "").strip()
    if not routine or not exercise: sys.exit("routine and exercise are required")
    for label, v, lo, hi in (("sets", a.sets, 1, 20), ("reps", a.reps, 1, 100), ("order", a.order, 1, 99)):
        if v is not None and not (lo <= v <= hi): sys.exit(f"{label} out of range")
    w = num(a.weight)
    if w is not None and not (0 <= w <= 1000): sys.exit("weight out of range")
    c = cx(); _ensure_routines_history(c)
    prior = c.execute("SELECT ex_order,target_sets,target_reps,target_weight_kg FROM routines "
                      "WHERE routine_name=? AND exercise_title=?", (routine, exercise)).fetchone()
    _snap(c, "set", routine, exercise, prior)
    if prior:
        order = a.order if a.order is not None else prior["ex_order"]
        sets = a.sets if a.sets is not None else prior["target_sets"]
        reps = a.reps if a.reps is not None else prior["target_reps"]
        weight = w if a.weight is not None else prior["target_weight_kg"]
        c.execute("UPDATE routines SET ex_order=?,target_sets=?,target_reps=?,target_weight_kg=? "
                  "WHERE routine_name=? AND exercise_title=?", (order, sets, reps, weight, routine, exercise))
    else:
        nxt = c.execute("SELECT COALESCE(MAX(ex_order),0)+1 n FROM routines WHERE routine_name=?", (routine,)).fetchone()["n"]
        order = a.order if a.order is not None else nxt
        c.execute("INSERT INTO routines(routine_name,exercise_title,ex_order,target_sets,target_reps,target_weight_kg) "
                  "VALUES(?,?,?,?,?,?)", (routine, exercise, order, a.sets, a.reps, w))
    if insight_migrations.recorded_version(c) >= 3:
        insight_migrations.require_version(c, 3)
        insight_migrations.append_training_plan_revision(
            c, effective_from=today(), source="routine-set")
    c.commit()
    out({"ok": True, "routine": routine, "exercise": exercise, "was_new": prior is None})

def routine_remove(a):
    """Remove an exercise from a routine (program config, not logged data).
    Snapshots the full prior row so it can be undone."""
    routine = (a.routine or "").strip(); exercise = (a.exercise or "").strip()
    c = cx(); _ensure_routines_history(c)
    prior = c.execute("SELECT ex_order,target_sets,target_reps,target_weight_kg FROM routines "
                      "WHERE routine_name=? AND exercise_title=?", (routine, exercise)).fetchone()
    if not prior: sys.exit("no such exercise in that routine")
    _snap(c, "remove", routine, exercise, prior)
    c.execute("DELETE FROM routines WHERE routine_name=? AND exercise_title=?", (routine, exercise))
    if insight_migrations.recorded_version(c) >= 3:
        insight_migrations.require_version(c, 3)
        insight_migrations.append_training_plan_revision(
            c, effective_from=today(), source="routine-remove")
    c.commit()
    out({"ok": True, "removed": exercise, "from": routine})

def routine_undo(a):
    """Reverse the most recent program edit — routine OR schedule change.
    op='hevy-sync' snapshot rows are NOT candidates: they're the bulk sync's
    audit trail, and 'undoing' one would be a lying no-op that buries the
    latest explicit manual edit under N journal rows."""
    c = cx(); _ensure_routines_history(c)
    h = c.execute("SELECT * FROM routines_history WHERE undone=0 AND op!='hevy-sync'"
                  " ORDER BY id DESC LIMIT 1").fetchone()
    if not h: sys.exit("nothing to undo")
    r, e = h["routine_name"], h["exercise_title"]
    if h["op"] == "schedule":
        # 'schedule' journal reuses columns: routine_name=weekday, exercise_title=prior routine.
        if h["prior_existed"]:
            c.execute("UPDATE training_schedule SET routine_name=? WHERE weekday=?", (e, r))
        else:
            c.execute("DELETE FROM training_schedule WHERE weekday=?", (r,))
    elif h["op"] == "set" and h["prior_existed"]:
        c.execute("UPDATE routines SET ex_order=?,target_sets=?,target_reps=?,target_weight_kg=? "
                  "WHERE routine_name=? AND exercise_title=?",
                  (h["ex_order"], h["target_sets"], h["target_reps"], h["target_weight_kg"], r, e))
    elif h["op"] == "set":                        # edit had added a new row -> remove it
        c.execute("DELETE FROM routines WHERE routine_name=? AND exercise_title=?", (r, e))
    elif h["op"] == "remove":                     # re-insert the removed row
        c.execute("INSERT INTO routines(routine_name,exercise_title,ex_order,target_sets,target_reps,target_weight_kg) "
                  "VALUES(?,?,?,?,?,?)", (r, e, h["ex_order"], h["target_sets"], h["target_reps"], h["target_weight_kg"]))
    c.execute("UPDATE routines_history SET undone=1 WHERE id=?", (h["id"],))
    if insight_migrations.recorded_version(c) >= 3:
        insight_migrations.require_version(c, 3)
        insight_migrations.append_training_plan_revision(
            c, effective_from=today(), source="routine-undo")
    c.commit()
    out({"ok": True, "undid": {"op": h["op"], "routine": r, "exercise": e}})

def schedule_set(a):
    """Set a weekday's routine (Mon..Sun -> routine name, or 'Rest'). Journaled
    to routines_history so `routine-undo` reverses schedule changes too."""
    if a.weekday not in WEEKDAYS: sys.exit(f"weekday must be one of {', '.join(WEEKDAYS)}")
    routine = (a.routine or "").strip()
    if not routine: sys.exit("routine is required (use 'Rest' for a rest day)")
    c = cx(); _ensure_routines_history(c)
    prior = c.execute("SELECT routine_name FROM training_schedule WHERE weekday=?", (a.weekday,)).fetchone()
    # reuse columns: routine_name=weekday, exercise_title=prior routine to restore
    c.execute("INSERT INTO routines_history(op, routine_name, exercise_title, prior_existed) "
              "VALUES('schedule',?,?,?)", (a.weekday, prior["routine_name"] if prior else None, 1 if prior else 0))
    c.execute("INSERT INTO training_schedule(weekday,routine_name) VALUES(?,?) "
              "ON CONFLICT(weekday) DO UPDATE SET routine_name=excluded.routine_name", (a.weekday, routine))
    if insight_migrations.recorded_version(c) >= 3:
        insight_migrations.require_version(c, 3)
        insight_migrations.append_training_plan_revision(
            c, effective_from=today(), source="schedule-set")
    c.commit()
    out({"ok": True, "weekday": a.weekday, "routine": routine})

def import_recipes(a):
    """Compatibility entry point for the extracted import command."""
    out(recipe_commands.import_csv(
        _command_context(), a, parse_number=num, slug=slug, meal_types=MEAL_TYPES))

def set_batch(a):
    """Record the finished cooked weight of a recipe's batch (enables per-gram math).
    Optional --portions N also sets recipes.portions + grams_per_portion (= grams/N)
    for the recipe page's per-portion math. Unlike `prep`, this NEVER touches
    meal_inventory — it's the side-effect-free restore path (e.g. re-syncing
    batch/portions after a re-import), not a fresh prep event."""
    if a.portions is not None and a.portions < 1: sys.exit("--portions must be >= 1")
    c = cx()
    r = c.execute("SELECT recipe_id,name FROM recipes WHERE recipe_id=? OR name=?", (a.recipe, a.recipe)).fetchone()
    if not r: sys.exit(f"recipe not found: {a.recipe}")
    res = {"ok": True, "recipe": r["name"], "batch_grams": a.grams}
    if a.portions is not None:
        gpp = round(a.grams / a.portions, 1)
        c.execute("UPDATE recipes SET batch_grams=?, portions=?, grams_per_portion=? WHERE recipe_id=?",
                  (a.grams, a.portions, gpp, r["recipe_id"]))
        res.update({"portions": a.portions, "grams_per_portion": gpp})
    else:
        c.execute("UPDATE recipes SET batch_grams=? WHERE recipe_id=?", (a.grams, r["recipe_id"]))
    c.commit(); out(res)

def _recipe(c, key):
    return c.execute("SELECT * FROM recipes WHERE recipe_id=? OR name=?", (key, key)).fetchone()


def _recipe_ingredients_path(recipe_id):
    """Validated vault sidecar for one recipe's structured ingredient list."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,159}", recipe_id or ""):
        sys.exit("recipe has an unsafe id; ingredients were not written")
    vault = _vault_root()
    target = os.path.abspath(os.path.join(
        vault, "personal", "recipes", f"{recipe_id}.ingredients.json"))
    if os.path.commonpath([target, vault]) != vault:
        sys.exit("ingredient path escapes the vault")
    return target


def _ingredient_number(value, field, index):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        sys.exit(f"ingredient {index} {field} must be a number")
    value = float(value)
    if not (math.isfinite(value) and 0 < value <= 1_000_000):
        sys.exit(f"ingredient {index} {field} must be finite and > 0")
    return int(value) if value.is_integer() else value


def recipe_ingredients_set(a):
    """Replace one recipe's structured ingredient sidecar from JSON on stdin.

    This is agent/SSH-only (never panel-bridge reachable). It validates the
    recipe against the database, normalizes a small explicit JSON contract,
    and atomically replaces only personal/recipes/<id>.ingredients.json.
    Nutrition totals, logs, and inventory are untouched.
    """
    c = cx_ro()
    try:
        r = _recipe(c, a.recipe)
    finally:
        c.close()
    if not r:
        sys.exit(f"recipe not found: {a.recipe}")

    raw = _stdin_text("recipe ingredients")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"recipe ingredients must be valid JSON: {exc.msg}")
    items = payload.get("ingredients") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        sys.exit("ingredients must be a JSON list containing 1-100 items")

    normalized = []
    allowed = {"name", "amount", "unit", "weight_g", "note"}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            sys.exit(f"ingredient {index} must be an object")
        unexpected = set(item) - allowed
        if unexpected:
            sys.exit(f"ingredient {index} has unsupported fields: {', '.join(sorted(unexpected))}")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            sys.exit(f"ingredient {index} name must be 1-200 characters")
        clean = {"name": name.strip()}
        if "amount" in item:
            clean["amount"] = _ingredient_number(item["amount"], "amount", index)
            unit = item.get("unit")
            if not isinstance(unit, str) or not unit.strip() or len(unit.strip()) > 32:
                sys.exit(f"ingredient {index} unit must be 1-32 characters when amount is set")
            clean["unit"] = unit.strip()
        elif "unit" in item:
            sys.exit(f"ingredient {index} unit requires amount")
        if "weight_g" in item:
            clean["weight_g"] = _ingredient_number(item["weight_g"], "weight_g", index)
        if "note" in item:
            note = item["note"]
            if not isinstance(note, str) or not note.strip() or len(note.strip()) > 300:
                sys.exit(f"ingredient {index} note must be 1-300 characters")
            clean["note"] = note.strip()
        normalized.append(clean)

    target = _recipe_ingredients_path(r["recipe_id"])
    os.makedirs(os.path.dirname(target), exist_ok=True)
    document = {
        "recipe_id": r["recipe_id"],
        "recipe_name": r["name"],
        "ingredients": normalized,
        "updated_at": _now().isoformat(timespec="seconds"),
    }
    tmp = target + f".tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(document, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    rel = os.path.relpath(target, _vault_root())
    out({"ok": True, "recipe": r["name"], "recipe_id": r["recipe_id"],
         "ingredients_count": len(normalized), "file": rel,
         "ingredients": normalized})


# T47: whitelist for recipe-tag; the panel's Recipes meal filter renders these.
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")


def _recipes_has_meal_type(c):
    """Require the migration-owned recipe column without DDL."""
    _require_schema(c, "recipes", "meal_type")
    return True


def recipe_tag(a):
    """Tag a recipe with a meal type (breakfast/lunch/dinner/snack) for the
    panel's Recipes filter, or 'clear' to null it. Coach/agent-side config —
    NOT in either panel bridge allowlist (same posture as athletic-target-set).
    Migration 001 owns recipes.meal_type; this writer only validates and uses it."""
    mt = (a.meal_type or "").strip().lower()
    if mt not in MEAL_TYPES + ("clear",):
        sys.exit(f"meal_type must be one of: {', '.join(MEAL_TYPES)} (or 'clear' to remove)")
    c = cx()
    r = _recipe(c, a.recipe)
    if not r: sys.exit(f"recipe not found: {a.recipe}")
    _recipes_has_meal_type(c)
    val = None if mt == "clear" else mt
    c.execute("UPDATE recipes SET meal_type=? WHERE recipe_id=?", (val, r["recipe_id"]))
    c.commit()
    out({"ok": True, "recipe": r["name"], "meal_type": val})

def _compute_nutrients(c, rid, grams, batch_grams):
    """Deterministic: per_gram = total/batch_grams; nutrient_eaten = per_gram * grams."""
    rows = c.execute("SELECT nutrient,unit,per_gram FROM recipe_nutrients WHERE recipe_id=?", (rid,)).fetchall()
    res = {}
    for r in rows:
        res[r["nutrient"]] = round(r["per_gram"] / batch_grams * grams, 3)
    return res


# ------------------------------------------------------- freezer restock state
RESTOCK_ACTIONS = {"notified", "restock", "alternatives", "later", "skip"}
RESTOCK_THRESHOLD_MAX = 1_000_000


def _restock_state_ready(c):
    _require_schema(
        c,
        "recipe_restock_state",
        "recipe_id",
        "action",
        "threshold",
        "portions_at_notice",
        "snooze_until",
        "updated_at",
    )


def _restock_threshold(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        sys.exit("--threshold must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= RESTOCK_THRESHOLD_MAX:
        sys.exit(
            f"--threshold must be finite and between 0 and {RESTOCK_THRESHOLD_MAX}"
        )
    return result


def _restock_due(c, recipe_id, portions, threshold, on_date):
    if portions > threshold:
        return False
    state = c.execute(
        """SELECT action,snooze_until FROM recipe_restock_state
             WHERE recipe_id=?""",
        (recipe_id,),
    ).fetchone()
    if not state:
        return True
    if state["action"] == "later" and state["snooze_until"]:
        return state["snooze_until"] <= on_date
    return False


def restock_check(a):
    """Return low-stock recipes whose notification state is currently due.

    This read does not mark an item as notified. A caller records the state only
    after its notification or user-choice surface succeeds.
    """
    threshold = _restock_threshold(a.threshold)
    d = valid_date(a.date) if a.date else today()
    c = cx()
    _restock_state_ready(c)
    rows = c.execute(
        """SELECT r.recipe_id, r.name,
                  COALESCE(SUM(m.portions_remaining), 0) AS portions,
                  MAX(m.prepped_on) AS latest_batch
             FROM meal_inventory m
             JOIN recipes r ON r.recipe_id=m.recipe_id
         GROUP BY r.recipe_id, r.name
         ORDER BY portions, r.name"""
    ).fetchall()
    alerts = []
    for row in rows:
        if _restock_due(c, row["recipe_id"], row["portions"], threshold, d):
            alerts.append({
                "recipe_id": row["recipe_id"],
                "recipe": row["name"],
                "portions_left": row["portions"],
                "threshold": threshold,
                "prepped_on": row["latest_batch"],
            })
    out({"date": d, "threshold": threshold, "alerts": alerts,
         "count": len(alerts)})


def restock_mark(a):
    """Persist a delivered restock prompt or the user's selected response."""
    if a.action not in RESTOCK_ACTIONS:
        sys.exit(f"action must be one of: {', '.join(sorted(RESTOCK_ACTIONS))}")
    threshold = _restock_threshold(a.threshold)
    d = valid_date(a.date) if a.date else today()
    c = cx()
    _restock_state_ready(c)
    recipe = _recipe(c, a.recipe)
    if not recipe:
        sys.exit(f"recipe not found: {a.recipe}")
    inventory = c.execute(
        """SELECT COALESCE(SUM(portions_remaining), 0) AS portions
             FROM meal_inventory WHERE recipe_id=?""",
        (recipe["recipe_id"],),
    ).fetchone()
    snooze_until = a.snooze_until
    if a.action == "later" and not snooze_until:
        snooze_until = (date.fromisoformat(d) + timedelta(days=3)).isoformat()
    if snooze_until:
        snooze_until = valid_date(snooze_until, "--snooze-until")
    c.execute(
        """INSERT INTO recipe_restock_state
             (recipe_id,action,threshold,portions_at_notice,snooze_until,updated_at)
             VALUES(?,?,?,?,?,datetime('now'))
             ON CONFLICT(recipe_id) DO UPDATE SET
               action=excluded.action,
               threshold=excluded.threshold,
               portions_at_notice=excluded.portions_at_notice,
               snooze_until=excluded.snooze_until,
               updated_at=datetime('now')""",
        (recipe["recipe_id"], a.action, threshold,
         inventory["portions"], snooze_until),
    )
    c.commit()
    out({
        "ok": True,
        "recipe_id": recipe["recipe_id"],
        "recipe": recipe["name"],
        "action": a.action,
        "portions_left": inventory["portions"],
        "threshold": threshold,
        "snooze_until": snooze_until,
    })

def prep(a):
    """Log a meal-prep batch: set batch weight + add portions to the freezer inventory."""
    c = cx(); r = _recipe(c, a.recipe)
    if not r: sys.exit(f"recipe not found: {a.recipe}")
    _restock_state_ready(c)
    gpp = round(a.batch_grams / a.portions, 1) if a.batch_grams else r["grams_per_portion"]
    if a.batch_grams:
        c.execute("UPDATE recipes SET batch_grams=?, grams_per_portion=?, portions=? WHERE recipe_id=?",
                  (a.batch_grams, gpp, a.portions, r["recipe_id"]))
    c.execute("INSERT INTO meal_inventory(recipe_id,portions_remaining,grams_per_portion,prepped_on) VALUES(?,?,?,?)",
              (r["recipe_id"], a.portions, gpp, today()))
    # A new batch begins a new stock cycle. Earlier skip/snooze/notice state
    # must not suppress the next genuine low-stock transition.
    c.execute("DELETE FROM recipe_restock_state WHERE recipe_id=?", (r["recipe_id"],))
    c.commit()
    out({"ok": True, "prepped": r["name"], "portions": a.portions, "grams_per_portion": gpp, "batch_grams": a.batch_grams or r["batch_grams"]})

def _log_nutrition(c, rid, name, grams, d, *, time_value=None, meal_type=None, source=None):
    _require_schema(c, "nutrition_log", "time", "meal_type")
    r = c.execute("SELECT batch_grams FROM recipes WHERE recipe_id=?", (rid,)).fetchone()
    bg = r["batch_grams"] if r else None
    n = _compute_nutrients(c, rid, grams, bg) if bg else {}
    cols = ["date", "recipe_id", "food_name", "grams", "kcal", "protein_g",
            "carbs_g", "fat_g", "fiber_g", "time", "meal_type"]
    values = [d, rid, name, grams, n.get("Energy"), n.get("Protein"), n.get("Carbs"),
              n.get("Fat"), n.get("Fiber"), time_value, meal_type]
    if source is not None:
        cols.append("source"); values.append(source)
    c.execute(f"INSERT INTO nutrition_log({','.join(cols)}) VALUES({','.join('?' * len(cols))})", values)
    return n, bg

def _food_capture_values(a):
    try:
        d = insight_events.iso_date(a.date, "--date") if a.date else today()
    except insight_events.CaptureError as exc:
        sys.exit(str(exc))
    t = getattr(a, "time", None)
    if t is not None:
        try:
            insight_events.hhmm(t, "--time", nullable=False)
        except insight_events.CaptureError as exc:
            sys.exit(str(exc))
    meal_type = getattr(a, "meal_type", None)
    if meal_type is not None:
        meal_type = meal_type.strip().lower()
        if meal_type not in MEAL_TYPES:
            sys.exit(f"--meal-type must be one of: {', '.join(MEAL_TYPES)}")
    return d, t, meal_type, _capture_source(a)

def log_food(a):
    if not (math.isfinite(a.grams) and 0 < a.grams <= 100_000):
        sys.exit("--grams must be a positive finite number no greater than 100000")
    c = cx(); r = _recipe(c, a.recipe)
    if not r: sys.exit(f"recipe not found: {a.recipe}")
    d, t, meal_type, source = _food_capture_values(a)
    n, bg = _log_nutrition(c, r["recipe_id"], r["name"], a.grams, d,
                           time_value=t, meal_type=meal_type, source=source)
    if insight_migrations.recorded_version(c) >= 2:
        insight_events.invalidate_explicit_none(
            c, d, "food_identity", f"recipe:{r['recipe_id']}", source or "manual", None,
        )
        insight_events.invalidate_explicit_none(
            c, d, "nutrition_total", None, source or "manual", None,
        )
    c.commit()
    extra = ({"time": t, "meal_type": meal_type, "source": source}
             if any(v is not None for v in (t, meal_type, source)) else {})
    if not bg:
        out({"ok": True, "logged": r["name"], "grams": a.grams, "date": d,
             "warning": "no batch weight set -> macros not computed. run `set-batch`.", **extra}); return
    out({"ok": True, "logged": r["name"], "grams": a.grams, "date": d,
         "kcal": n.get("Energy"), "protein_g": n.get("Protein"), "carbs_g": n.get("Carbs"), "fat_g": n.get("Fat"), **extra})

def eat(a):
    """Eat one (or more) portions from the freezer: decrement inventory + log nutrition."""
    if not 1 <= a.portions <= 100:
        sys.exit("--portions must be 1-100")
    c = cx(); r = _recipe(c, a.recipe)
    if not r: sys.exit(f"recipe not found: {a.recipe}")
    _restock_state_ready(c)
    inv = c.execute("""SELECT id,portions_remaining,grams_per_portion FROM meal_inventory
                       WHERE recipe_id=? AND portions_remaining>0 ORDER BY prepped_on LIMIT 1""", (r["recipe_id"],)).fetchone()
    if not inv: sys.exit(f"no portions of '{r['name']}' left in inventory")
    p = a.portions
    if inv["portions_remaining"] < p: sys.exit(f"only {inv['portions_remaining']} portions left")
    grams = inv["grams_per_portion"] * p
    d, t, meal_type, source = _food_capture_values(a)
    n, bg = _log_nutrition(c, r["recipe_id"], r["name"], grams, d,
                           time_value=t, meal_type=meal_type, source=source)
    c.execute("UPDATE meal_inventory SET portions_remaining=portions_remaining-? WHERE id=?", (p, inv["id"]))
    if insight_migrations.recorded_version(c) >= 2:
        insight_events.invalidate_explicit_none(
            c, d, "food_identity", f"recipe:{r['recipe_id']}", source or "manual", None,
        )
        insight_events.invalidate_explicit_none(
            c, d, "nutrition_total", None, source or "manual", None,
        )
    left = c.execute(
        """SELECT COALESCE(SUM(portions_remaining), 0) AS portions
             FROM meal_inventory WHERE recipe_id=?""",
        (r["recipe_id"],),
    ).fetchone()["portions"]
    # Snoozes are operational reminders, so compare them with the current day
    # even when the consumption record itself is backdated.
    alert = _restock_due(c, r["recipe_id"], left, 2, today())
    c.commit()
    extra = ({"date": d, "time": t, "meal_type": meal_type, "source": source}
             if any(v is not None for v in (t, meal_type, source)) else {})
    out({"ok": True, "ate": r["name"], "portions": p, "grams": grams, "portions_left": left,
         "kcal": n.get("Energy"), "protein_g": n.get("Protein"),
         "restock_alert": alert,
         "restock_next": (
             "deliver restock choices, then run restock-mark --action notified"
             if alert else None
         ), **extra})

def menu(a):
    c = cx()
    # Migration 001 owns meal_type. This read requires it and never runs DDL.
    _recipes_has_meal_type(c)
    mt_col = ", r.meal_type"
    rows = c.execute(f"""SELECT r.name, r.recipe_id, SUM(m.portions_remaining) AS portions, m.grams_per_portion, r.batch_grams{mt_col}
        FROM meal_inventory m JOIN recipes r ON r.recipe_id=m.recipe_id
        WHERE m.portions_remaining>0 GROUP BY r.recipe_id ORDER BY portions DESC""").fetchall()
    items = []
    for r in rows:
        kcal = protein = None
        if r["batch_grams"]:
            n = _compute_nutrients(c, r["recipe_id"], r["grams_per_portion"], r["batch_grams"])
            kcal, protein = n.get("Energy"), n.get("Protein")
        items.append({"recipe": r["name"], "portions_available": r["portions"],
                      "grams_per_portion": r["grams_per_portion"], "kcal_per_portion": kcal, "protein_g": protein,
                      "meal_type": r["meal_type"]})
    out({"date": a.date or today(), "menu": items})

# =========================================================== generic logging
def _subjective_daily_has_soreness_note(c):
    """Require Migration 001's soreness field without running DDL."""
    _require_schema(c, "subjective_daily", "soreness_note")
    return True


def _validate_supplement_product(data):
    """Validate the existing generic product-create path before opening the DB."""
    name = data.get("name", "").strip()
    if not name:
        sys.exit("supplement name is required")
    data["name"] = name
    for field, maximum in (("name", 300), ("brand", 300), ("unit", 80),
                           ("form", 100), ("schedule", 300), ("notes", 2000)):
        value = data.get(field)
        if value is not None and (len(value) > maximum or "\x00" in value):
            sys.exit(f"supplement {field} must be at most {maximum} characters without NUL")
    if "active" in data:
        if data["active"] not in ("0", "1"):
            sys.exit("supplement active must be 0 or 1")
        data["active"] = int(data["active"])
    if "dose" in data:
        try:
            dose = float(data["dose"])
        except (TypeError, ValueError):
            sys.exit("supplement dose must be a finite number between 0 and 1000000")
        if not math.isfinite(dose) or not 0 <= dose <= 1_000_000:
            sys.exit("supplement dose must be a finite number between 0 and 1000000")
        if not data.get("unit", "").strip():
            sys.exit("supplement unit is required when a dose is supplied")
        data["dose"] = dose


def log(a):
    table = a.table
    if table not in LOGGABLE: sys.exit(f"not a loggable table. allowed: {', '.join(LOGGABLE)}")
    allowed = LOGGABLE[table]
    data = {}
    for pair in a.fields:
        if "=" not in pair: sys.exit(f"bad field '{pair}', use key=value")
        k, v = pair.split("=", 1)
        if k not in allowed: sys.exit(f"'{k}' not allowed for {table}. allowed: {', '.join(allowed)}")
        # validate ratings (day_rating is the 1-3 traffic light, others 1-5)
        if k == "day_rating":
            iv = int(v)
            if not 1 <= iv <= 3: sys.exit("day_rating must be 1-3 (red/yellow/green)")
            data[k] = iv
        elif k in RATING:
            iv = int(v)
            if not 1 <= iv <= 5: sys.exit(f"{k} must be 1-5")
            data[k] = iv
        else:
            data[k] = v
    if "date" in allowed and "date" not in data: data["date"] = today()
    if table == "supplement_products":
        _validate_supplement_product(data)
    c = cx()
    # The generic writer may use, but never create, the migration-owned field.
    if table == "subjective_daily":
        _subjective_daily_has_soreness_note(c)
    cols = ",".join(data); ph = ",".join("?" * len(data))
    sql = f"INSERT INTO {table}({cols}) VALUES({ph})"
    if table in UPSERT_DATE_TABLES and "date" in data:
        # Re-logging the same day updates that day's row instead of erroring.
        upd = ",".join(f"{k}=excluded.{k}" for k in data if k != "date")
        sql += f" ON CONFLICT(date) DO UPDATE SET {upd}" if upd else " ON CONFLICT(date) DO NOTHING"
    c.execute(sql, list(data.values()))
    phase2_applied = insight_migrations.recorded_version(c) >= 2
    if table == "meds_log" and phase2_applied:
        key, _ = insight_events.resolve_identity(c, "medication", data.get("drug"))
        insight_events.invalidate_explicit_none(
            c, data.get("date", today()), "medication", key, "manual", None,
        )
    elif table == "supplements_log" and phase2_applied:
        product = c.execute(
            "SELECT name FROM supplement_products WHERE supplement_id=?",
            (data.get("supplement_id"),),
        ).fetchone()
        key, _ = insight_events.resolve_identity(
            c, "supplement", product["name"] if product else None)
        insight_events.invalidate_explicit_none(
            c, data.get("date", today()), "supplement", key, "manual", None,
        )
    c.commit()
    out({"ok": True, "table": table, "inserted": data})

def water_add(a):
    """§4c frictionless water capture: ADD ml to today's intake.water_ml —
    the Telegram preset taps (+250 / +500 / +bottle) land here. Incremental
    by design: `log intake water_ml=X` REPLACES the day's value (upsert), which
    is wrong for taps that must accumulate. Deliberately NOT in the bridge
    allowlists (capture is Telegram/agent-path only, like fitness-test-log).
    Echoes the running total vs target so the coach can confirm in one line.
    Preset mapping is a generic interface convenience, not a personal target."""
    ml = int(a.ml)
    if not 1 <= ml <= 3000:
        sys.exit("ml must be 1-3000 per tap (a bottle is a few hundred ml)")
    d = valid_date(a.date) if a.date else today()
    c = cx()
    c.execute("""INSERT INTO intake(date, water_ml) VALUES(?,?)
                 ON CONFLICT(date) DO UPDATE SET
                   water_ml = COALESCE(intake.water_ml, 0) + excluded.water_ml""",
              (d, ml))
    c.commit()
    total = c.execute("SELECT water_ml FROM intake WHERE date=?", (d,)).fetchone()["water_ml"]
    out({"ok": True, "date": d, "added_ml": ml, "total_ml": total,
         "target_ml": WATER_TARGET_ML,
         "pct_of_target": round(100 * total / WATER_TARGET_ML)})


DAYMAP = {"red":1,"yellow":2,"green":3,"bad":1,"okay":2,"ok":2,"good":3,"1":1,"2":2,"3":3}
# Optional provenance for owner-entered measurements. The legacy defaults
# (manual/ui/chat) remain valid only so old calls and old rows keep their exact
# behavior; trusted new surfaces use the three explicit *-panel/telegram tags.
# `scheduler` is intentionally absent: a timer may ask, but it may never invent
# an owner measurement.
CAPTURE_SOURCES = {"manual", "ui", "chat", "chat-panel", "chat-telegram", "panel-ui"}

def _capture_source(a):
    source = getattr(a, "source", None)
    if source is not None and source not in CAPTURE_SOURCES:
        sys.exit(f"--source must be one of: {', '.join(sorted(CAPTURE_SOURCES))}")
    return source

def day_rating(a):
    """One-tap end-of-day traffic light: green(3)/yellow(2)/red(1). The never-skip minimum capture."""
    v = DAYMAP.get(str(a.rating).lower())
    if v is None: sys.exit("rating must be green | yellow | red")
    d = valid_date(a.date) if a.date else today()
    source = _capture_source(a)
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        existed = c.execute(
            "SELECT 1 FROM subjective_daily WHERE date=? AND day_rating IS NOT NULL",
            (d,),
        ).fetchone() is not None
        if source is None:
            # Preserve the pre-Phase-1 SQL/default and response shape byte-for-byte.
            c.execute("""INSERT INTO subjective_daily(date, day_rating) VALUES(?,?)
                         ON CONFLICT(date) DO UPDATE SET day_rating=excluded.day_rating""", (d, v))
        else:
            c.execute("""INSERT INTO subjective_daily(date, day_rating, source) VALUES(?,?,?)
                         ON CONFLICT(date) DO UPDATE SET
                           day_rating=excluded.day_rating, source=excluded.source""",
                      (d, v, source))
        if not existed and insight_migrations.recorded_version(c) >= 4:
            count = int(c.execute(
                "SELECT COUNT(*) FROM subjective_daily WHERE day_rating IS NOT NULL"
            ).fetchone()[0])
            # The frozen statistical eligibility floor is 30; every new block
            # of 30 observations is a deterministic count milestone.
            if count >= 30 and count % 30 == 0:
                insight_orchestrator.enqueue_internal_trigger(
                    c,
                    trigger_kind="outcome_milestone",
                    source_table="subjective_daily",
                    source_row_key=f"day_rating_count_{count}",
                    event_date=d,
                )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    result = {"ok": True, "date": d, "day_rating": v,
              "meaning": {1:"red (bad day)",2:"yellow (okay day)",3:"green (good day)"}[v]}
    if source is not None:
        result["source"] = source
    out(result)

# =========================================================== read
SAFE = re.compile(r"^\s*SELECT\b", re.I)
def query(a):
    if not SAFE.match(a.sql) or ";" in a.sql.rstrip(";"):
        sys.exit("only single read-only SELECT statements are allowed")
    c = cx_ro()   # read-only connection is the trust boundary; the regex is a first layer
    rows = [dict(r) for r in c.execute(a.sql).fetchall()]
    out({"rows": rows, "count": len(rows)})

def _daily_metrics_has_hrv_ms(c):
    """Require Migration 001's canonical HRV field without running DDL."""
    _require_schema(c, "daily_metrics", "hrv_ms")
    return True

def summary(a):
    c = cx(); d = a.days; lo = days_ago(d)
    hrv_col = "hrv_ms" if _daily_metrics_has_hrv_ms(c) else "hrv_sdnn"
    q = f"""SELECT ROUND(AVG(resting_hr),1) resting_hr, ROUND(AVG({hrv_col}),1) hrv,
                  ROUND(AVG(sleep_hours),2) sleep_h, ROUND(AVG(steps),0) steps
           FROM daily_metrics WHERE date >= ?"""
    row = dict(c.execute(q, (lo,)).fetchone())
    subj = c.execute("""SELECT ROUND(AVG(focus),1) focus, ROUND(AVG(mood),1) mood, ROUND(AVG(energy),1) energy
            FROM subjective_daily WHERE date >= ?""", (lo,)).fetchone()
    row.update({k: subj[k] for k in subj.keys()})
    out({"window_days": d, "averages": row})

def bp_brief(a):
    """Return vitals and dose totals for one configured medication.

    A generic SUM over meds_log would combine unrelated medications. The
    installation-owned alias set keeps the selection explicit and testable.
    """
    c = cx(); d = int(a.days); lo = days_ago(d)
    vitals = [dict(r) for r in c.execute(
        """SELECT date, time, systolic, diastolic, resting_hr FROM vitals
           WHERE date >= ? ORDER BY date, time""", (lo,))]
    drug = (a.drug or "").strip().lower()
    names = sorted(MEDICATION_ALIASES) if drug in MEDICATION_ALIASES else [drug]
    ph = ",".join("?" * len(names))
    doses = [dict(r) for r in c.execute(
        f"""SELECT date, ROUND(SUM(dose_mg), 1) AS dose_total_mg FROM meds_log
            WHERE LOWER(drug) IN ({ph}) AND date >= ?
            GROUP BY date ORDER BY date""", (*names, lo))]
    out({"days": d, "drug": a.drug, "matched_names": names,
         "vitals": vitals, "doses": doses})

# =========================================================== vault notes
# The ONLY vault Markdown files the panel may overwrite. An exact-match
# whitelist (no globbing, no '..') is the whole safety boundary — CLAUDE.md,
# health.py, the DB, wiki/, daily entries, etc. are all unreachable.
EDITABLE_NOTES = {"personal/plan.md", "personal/habits.md",
                  "personal/profile.md", "personal/goals.md"}


def _vault_root():
    return resolve_vault_root(DB)


def write_note(a):
    """Overwrite a whitelisted vault Markdown note with content read from stdin.
    Atomic (temp + rename). Content is freeform text — never executed."""
    rel = (a.path or "").strip()
    if rel not in EDITABLE_NOTES:
        sys.exit(f"not an editable note: {rel}")
    content = sys.stdin.read()
    if len(content) > 500_000:
        sys.exit("note too large (>500k)")
    vault = _vault_root()
    target = os.path.join(vault, rel)
    # Defense in depth: the resolved path must still live inside the vault.
    if os.path.commonpath([os.path.abspath(target), vault]) != vault:
        sys.exit("path escapes the vault")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, target)
    out({"ok": True, "wrote": rel, "bytes": len(content)})

# =========================================================== §6 qualitative
# Raw text is PRESERVED VERBATIM before any extraction (vault law: raw/ is
# immutable). The extraction step itself is the agent mapping words to the
# EXISTING validated `log subjective_daily …` command — no new write surface.
# Neither command below is in the bridge allowlists (Telegram/agent path only).

def _vault_write_path(rel_dir, filename):
    """Resolve <vault>/<rel_dir>/<filename> with the write-note escape guard.
    filename is a single validated component — never a path. Must START with
    an alphanumeric: an empty audio name would otherwise yield the hidden
    file '.txt', and a leading '-' reads as a flag everywhere else."""
    if (not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$", filename or "")
            or ".." in filename):
        sys.exit(f"bad filename: {filename!r} (letters, digits, . _ - only; "
                 "must start with a letter or digit)")
    vault = _vault_root()
    target = os.path.abspath(os.path.join(vault, rel_dir, filename))
    if os.path.commonpath([target, vault]) != vault:
        sys.exit("path escapes the vault")
    return target


def _stdin_text(what):
    """Compatibility wrapper using the current command input stream."""
    return stdin_text(sys.stdin, what)


def journal_capture(a):
    """§6 typed status: stdin lands VERBATIM in raw/journal/YYYY-MM-DD.md.
    Append-only — same-day entries stack under '## HH:MM' separators, nothing
    is ever overwritten. The agent extracts fields AFTERWARDS via the
    validated `log subjective_daily` path (raw survives any extraction bug)."""
    content = _stdin_text("journal entry")
    d = valid_date(a.date) if a.date else today()
    t = a.time or _now().strftime("%H:%M")
    if _hhmm_min(t) is None:
        sys.exit("--time must be HH:MM")
    target = _vault_write_path("raw/journal", f"{d}.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    new_file = not os.path.exists(target)
    with open(target, "a", encoding="utf-8") as f:
        if new_file:
            f.write(f"# Journal {d}\n")
        f.write(f"\n## {t}\n\n{content.rstrip()}\n")
    out({"ok": True, "file": f"raw/journal/{d}.md", "date": d, "time": t,
         "bytes": len(content), "created": new_file})


def transcript_capture(a):
    """§6 voice memo: the whisper.cpp transcript (stdin) lands next to its
    audio as raw/voice/<audio>.txt — verbatim, refuse-on-exists (raw/ is
    immutable; a re-run must pick a new name, never silently replace)."""
    content = _stdin_text("transcript")
    target = _vault_write_path("raw/voice", f"{a.audio}.txt")
    if os.path.exists(target):
        sys.exit(f"raw/voice/{a.audio}.txt already exists — raw files are "
                 "immutable; save a re-transcription under a new name")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "x", encoding="utf-8") as f:
        f.write(content)
    out({"ok": True, "file": f"raw/voice/{a.audio}.txt", "bytes": len(content)})


def schema(a):
    """List tables/views and their columns — so the coach can orient without raw python."""
    c = cx()
    known = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")]
    if a.table:
        if a.table not in known: sys.exit(f"no such table/view: {a.table}")
        out({"table": a.table, "columns": [{"name": r[1], "type": r[2]} for r in c.execute(f"PRAGMA table_info('{a.table}')")]})
        return
    out({"tables_and_views": {t: [r[1] for r in c.execute(f"PRAGMA table_info('{t}')")] for t in known}})

# =========================================================== weather
WMO = {0:"clear",1:"mainly clear",2:"partly cloudy",3:"overcast",45:"fog",48:"rime fog",
       51:"light drizzle",53:"drizzle",55:"dense drizzle",56:"freezing drizzle",57:"freezing drizzle",
       61:"light rain",63:"rain",65:"heavy rain",66:"freezing rain",67:"freezing rain",
       71:"light snow",73:"snow",75:"heavy snow",77:"snow grains",80:"rain showers",81:"rain showers",
       82:"violent showers",85:"snow showers",86:"snow showers",95:"thunderstorm",96:"thunderstorm+hail",99:"thunderstorm+hail"}
WEATHER_PROVIDER_FIELDS = {
    "weather_code", "temp_max_c", "temp_min_c", "feels_like_max_c",
    "feels_like_min_c", "precipitation_mm", "rain_mm", "snowfall_cm",
    "precipitation_hours", "wind_speed_max_ms", "wind_gusts_max_ms",
    "wind_dir_deg", "sunrise", "sunset", "daylight_hours", "sunshine_hours",
    "uv_index_max", "uv_index_clear_sky_max", "solar_radiation_mj", "et0_mm",
    "humidity_mean_pct", "pressure_mean_hpa", "cloud_cover_mean_pct",
}
AIR_PROVIDER_FIELDS = {
    "european_aqi_mean", "european_aqi_max", "pm2_5_ugm3", "pm10_ugm3",
    "ozone_ugm3", "no2_ugm3", "so2_ugm3", "co_ugm3", "alder_pollen",
    "birch_pollen", "grass_pollen", "mugwort_pollen", "olive_pollen",
    "ragweed_pollen",
}


def _missing_provider_fields(row, expected):
    return sorted(field for field in expected if row.get(field) is None)

def _fetch_weather_impl(a):
    import urllib.request
    d = a.date or today()
    daily = ("weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,"
             "sunrise,sunset,daylight_duration,sunshine_duration,uv_index_max,uv_index_clear_sky_max,"
             "precipitation_sum,rain_sum,snowfall_sum,precipitation_hours,wind_speed_10m_max,wind_gusts_10m_max,"
             "wind_direction_10m_dominant,shortwave_radiation_sum,et0_fao_evapotranspiration")
    hourly = "relative_humidity_2m,surface_pressure,cloud_cover"
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&daily=%s&hourly=%s"
           "&timezone=auto&temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm"
           "&start_date=%s&end_date=%s" % (a.lat, a.lon, daily, hourly, d, d))
    with urllib.request.urlopen(url, timeout=25) as r:
        j = json.load(r)
    dl = j["daily"]; hh = j.get("hourly", {})
    def g(k):
        v = dl.get(k); return v[0] if v else None
    def hrs(k):
        v = g(k); return round(v/3600, 2) if v is not None else None
    def mean(k):
        vals = [x for x in hh.get(k, []) if x is not None]
        return round(sum(vals)/len(vals), 1) if vals else None
    cond = WMO.get(g("weather_code"), str(g("weather_code")))
    row = {
        "date": d, "location": a.location, "weather_code": g("weather_code"), "condition": cond,
        "temp_max_c": g("temperature_2m_max"), "temp_min_c": g("temperature_2m_min"),
        "feels_like_max_c": g("apparent_temperature_max"), "feels_like_min_c": g("apparent_temperature_min"),
        "precipitation_mm": g("precipitation_sum"), "rain_mm": g("rain_sum"), "snowfall_cm": g("snowfall_sum"),
        "precipitation_hours": g("precipitation_hours"),
        "wind_speed_max_ms": g("wind_speed_10m_max"), "wind_gusts_max_ms": g("wind_gusts_10m_max"),
        "wind_dir_deg": g("wind_direction_10m_dominant"),
        "sunrise": g("sunrise"), "sunset": g("sunset"),
        "daylight_hours": hrs("daylight_duration"), "sunshine_hours": hrs("sunshine_duration"),
        "uv_index_max": g("uv_index_max"), "uv_index_clear_sky_max": g("uv_index_clear_sky_max"),
        "solar_radiation_mj": g("shortwave_radiation_sum"), "et0_mm": g("et0_fao_evapotranspiration"),
        "humidity_mean_pct": mean("relative_humidity_2m"), "pressure_mean_hpa": mean("surface_pressure"),
        "cloud_cover_mean_pct": mean("cloud_cover"), "source": "open-meteo",
    }
    cols = ",".join(row); ph = ",".join("?" * len(row))
    c = cx(); c.execute(f"INSERT OR REPLACE INTO weather({cols}) VALUES({ph})", list(row.values())); c.commit()
    result = {"ok": True, "date": d, "location": a.location, "condition": cond,
         "temp_max_c": row["temp_max_c"], "feels_like_max_c": row["feels_like_max_c"],
         "sunshine_hours": row["sunshine_hours"], "daylight_hours": row["daylight_hours"],
         "uv_index_max": row["uv_index_max"], "wind_speed_max_ms": row["wind_speed_max_ms"],
         "collected_fields": len(row)}
    out(result)
    return result, _missing_provider_fields(row, WEATHER_PROVIDER_FIELDS)

def _fetch_air_impl(a):
    """Air quality + pollen (Open-Meteo Air Quality API, hourly -> daily mean/max). All metric (ug/m3, grains/m3)."""
    import urllib.request
    d = a.date or today()
    hourly = ("pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,european_aqi,"
              "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen")
    url = ("https://air-quality-api.open-meteo.com/v1/air-quality?latitude=%s&longitude=%s&hourly=%s"
           "&timezone=auto&start_date=%s&end_date=%s" % (a.lat, a.lon, hourly, d, d))
    with urllib.request.urlopen(url, timeout=25) as r:
        j = json.load(r)
    hh = j.get("hourly", {})
    def mean(k):
        v = [x for x in hh.get(k, []) if x is not None]; return round(sum(v)/len(v), 1) if v else None
    def mx(k):
        v = [x for x in hh.get(k, []) if x is not None]; return round(max(v), 1) if v else None
    row = {
        "date": d, "location": a.location,
        "european_aqi_mean": mean("european_aqi"), "european_aqi_max": mx("european_aqi"),
        "pm2_5_ugm3": mean("pm2_5"), "pm10_ugm3": mean("pm10"), "ozone_ugm3": mean("ozone"),
        "no2_ugm3": mean("nitrogen_dioxide"), "so2_ugm3": mean("sulphur_dioxide"), "co_ugm3": mean("carbon_monoxide"),
        "alder_pollen": mean("alder_pollen"), "birch_pollen": mean("birch_pollen"), "grass_pollen": mean("grass_pollen"),
        "mugwort_pollen": mean("mugwort_pollen"), "olive_pollen": mean("olive_pollen"), "ragweed_pollen": mean("ragweed_pollen"),
        "source": "open-meteo-aqi",
    }
    cols = ",".join(row); ph = ",".join("?" * len(row))
    c = cx(); c.execute(f"INSERT OR REPLACE INTO air_quality({cols}) VALUES({ph})", list(row.values())); c.commit()
    result = {"ok": True, "date": d, "european_aqi_mean": row["european_aqi_mean"], "european_aqi_max": row["european_aqi_max"],
         "pm2_5_ugm3": row["pm2_5_ugm3"], "grass_pollen": row["grass_pollen"], "birch_pollen": row["birch_pollen"],
         "collected_fields": len(row)}
    out(result)
    return result, _missing_provider_fields(row, AIR_PROVIDER_FIELDS)


def _best_effort_collector_record(payload):
    """Record operational provenance without changing a collector's truth.

    During package-before-migration deployment, or if provenance recording has
    its own incident, the primary collection result remains authoritative.
    """
    try:
        insight_goals.validate_collector_run(payload)
        c = cx()
        try:
            c.execute("BEGIN IMMEDIATE")
            insight_migrations.require_version(c, 3)
            insight_goals.record_collector_run(c, payload)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    except Exception:
        return False
    return True


def _collector_coverage_date(value):
    try:
        return insight_events.iso_date(value, "date")
    except insight_events.CaptureError:
        return None


def _direct_collector_payload(source, started_at, status, coverage_date,
                              *, rows_seen, rows_written, error_code=None,
                              warning_codes=()):
    return {
        "source": source,
        "started_at": started_at,
        "completed_at": _now().isoformat(),
        "status": status,
        "coverage_from": coverage_date,
        "coverage_to": coverage_date,
        "rows_seen": rows_seen,
        "rows_written": rows_written,
        "error_code": error_code,
        "warning_codes": list(warning_codes),
    }


def fetch_weather(a):
    started = _now().isoformat()
    coverage = _collector_coverage_date(a.date or today())
    try:
        result, missing = _fetch_weather_impl(a)
    except Exception:
        _best_effort_collector_record(_direct_collector_payload(
            "weather", started, "failed", coverage,
            rows_seen=0, rows_written=0, error_code="collection_failed"))
        raise
    status = "partial" if missing else "success"
    warnings = ("missing_provider_fields",) if missing else ()
    _best_effort_collector_record(_direct_collector_payload(
        "weather", started, status, coverage,
        rows_seen=1, rows_written=1, warning_codes=warnings))
    return result


def fetch_air(a):
    started = _now().isoformat()
    coverage = _collector_coverage_date(a.date or today())
    try:
        result, missing = _fetch_air_impl(a)
    except Exception:
        _best_effort_collector_record(_direct_collector_payload(
            "air", started, "failed", coverage,
            rows_seen=0, rows_written=0, error_code="collection_failed"))
        raise
    status = "partial" if missing else "success"
    warnings = ("missing_provider_fields",) if missing else ()
    _best_effort_collector_record(_direct_collector_payload(
        "air", started, status, coverage,
        rows_seen=1, rows_written=1, warning_codes=warnings))
    return result

# =========================================================== follow-through
# The self-integrity layer: "did I keep my word to myself today?" Commitments
# are identity-framed config (name / when-then trigger / can't-fail floor /
# reward); commitments_log is the nightly kept/partly/broke record — the
# whole-day one-tap uses commitment_id=0. checkins are timed 1-5 spot readings
# (energy/focus/mood) so dose-timing effects become correlatable within a day.

COMMIT_STATUS = {"kept", "partly", "broke"}
CHECKIN_KINDS = {"energy", "focus", "mood"}

def _ensure_followthrough(c):
    """Require migration-owned follow-through tables without DDL."""
    _require_schema(c, "commitments")
    _require_schema(c, "commitments_log")
    _require_schema(c, "checkins")

def commitment_set(a):
    """Create or edit a commitment (upsert by name; config, not logged data)."""
    name = (a.name or "").strip()
    if not name: sys.exit("name is required")
    if a.active is not None and a.active not in (0, 1): sys.exit("--active must be 0 or 1")
    c = cx(); _ensure_followthrough(c)
    prior = c.execute("SELECT * FROM commitments WHERE name=?", (name,)).fetchone()
    if prior:
        vals = {f: getattr(a, f) if getattr(a, f) is not None else prior[f]
                for f in ("identity", "trigger", "floor", "reward", "active")}
        c.execute("UPDATE commitments SET identity=?,trigger=?,floor=?,reward=?,active=? "
                  "WHERE name=?", (*vals.values(), name))
    else:
        c.execute("INSERT INTO commitments(name,identity,trigger,floor,reward,active,created) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (name, a.identity, a.trigger, a.floor, a.reward,
                   a.active if a.active is not None else 1, today()))
    c.commit()
    row = c.execute("SELECT * FROM commitments WHERE name=?", (name,)).fetchone()
    out({"ok": True, "was_new": prior is None, "commitment": dict(row)})

def commitment_list(a):
    c = cx(); _ensure_followthrough(c)
    q = "SELECT * FROM commitments" + ("" if a.all else " WHERE active=1") + " ORDER BY id"
    out({"commitments": [dict(r) for r in c.execute(q)]})

def log_commitment(a):
    """Nightly kept/partly/broke. Without --id it's the whole-day 'did I keep
    my word?' one-tap; with --id it scores one commitment. Re-logging the same
    day updates (never duplicates, never deletes)."""
    status = (a.status or "").strip().lower()
    if status not in COMMIT_STATUS:
        sys.exit("status must be kept | partly | broke")
    source = _capture_source(a)
    c = cx(); _ensure_followthrough(c)
    cid = a.id or 0
    name = None
    if cid:
        row = c.execute("SELECT name FROM commitments WHERE id=?", (cid,)).fetchone()
        if not row: sys.exit(f"no commitment with id {cid}")
        name = row["name"]
    d = valid_date(a.date) if a.date else today()
    # On re-log: a new why always wins; the old why survives only if the status
    # is unchanged — a "broke" excuse must never annotate a later "kept" (review).
    if source is None:
        c.execute("""INSERT INTO commitments_log(date, commitment_id, status, why)
                     VALUES(?,?,?,?) ON CONFLICT(date, commitment_id)
                     DO UPDATE SET status=excluded.status,
                       why=CASE WHEN excluded.why IS NOT NULL THEN excluded.why
                                WHEN commitments_log.status=excluded.status THEN commitments_log.why
                                ELSE NULL END""",
                  (d, cid, status, a.why))
    else:
        c.execute("""INSERT INTO commitments_log(date, commitment_id, status, why, source)
                     VALUES(?,?,?,?,?) ON CONFLICT(date, commitment_id)
                     DO UPDATE SET status=excluded.status, source=excluded.source,
                       why=CASE WHEN excluded.why IS NOT NULL THEN excluded.why
                                WHEN commitments_log.status=excluded.status THEN commitments_log.why
                                ELSE NULL END""",
                  (d, cid, status, a.why, source))
    c.commit()
    result = {"ok": True, "date": d, "scope": name or "whole-day", "status": status,
              "why": a.why}
    if source is not None:
        result["source"] = source
    out(result)

def checkin(a):
    """Timed 1-5 spot reading (energy/focus/mood) — makes within-day timing
    effects (dose onset/peak/wear-off) correlatable."""
    kind = (a.kind or "").strip().lower()
    if kind not in CHECKIN_KINDS: sys.exit("kind must be energy | focus | mood")
    if not 1 <= a.value <= 5: sys.exit("value must be 1-5")
    t = a.time or _now().strftime("%H:%M")
    if _hhmm_min(t) is None: sys.exit("--time must be HH:MM")
    d = valid_date(a.date) if a.date else today()
    source = _capture_source(a)
    c = cx(); _ensure_followthrough(c)
    if source is None:
        c.execute("INSERT INTO checkins(date, time, kind, value, note) VALUES(?,?,?,?,?)",
                  (d, t, kind, a.value, a.note))
    else:
        c.execute("INSERT INTO checkins(date, time, kind, value, note, source) VALUES(?,?,?,?,?,?)",
                  (d, t, kind, a.value, a.note, source))
    c.commit()
    result = {"ok": True, "date": d, "time": t, "kind": kind, "value": a.value}
    if source is not None:
        result["source"] = source
    out(result)


def feedback_status(a):
    """Read-only evening-feedback completeness for one configured timezone date.

    Silence is never converted into an answer. Energy/focus stay in the
    existing daytime pings; the evening prompt asks only for a missing day
    rating, whole-day word, mood, and at most two still-positive recent pain
    regions without an observation today.
    """
    try:
        d = valid_date(a.date)
    except SystemExit as exc:
        out({"ok": False, "error": {"code": "validation_error", "message": str(exc)}})
        raise SystemExit(2)
    requested = date.fromisoformat(d)
    cutoff = (requested - timedelta(days=13)).isoformat()  # 14 inclusive dates
    c = cx_ro()

    rating = None
    if _table_exists(c, "subjective_daily"):
        rating = c.execute(
            "SELECT day_rating FROM subjective_daily WHERE date=?", (d,)).fetchone()
    rating_value = rating["day_rating"] if rating else None
    day_state = {"present": rating_value in (1, 2, 3), "value": rating_value}

    word = None
    if _table_exists(c, "commitments_log"):
        word = c.execute(
            "SELECT status FROM commitments_log WHERE date=? AND commitment_id=0",
            (d,)).fetchone()
    word_value = word["status"] if word else None
    word_state = {"present": word_value in COMMIT_STATUS, "value": word_value}

    missing_commitments = []
    if _table_exists(c, "commitments"):
        if _table_exists(c, "commitments_log"):
            missing_commitments = [dict(r) for r in c.execute(
                """SELECT cm.id, cm.name FROM commitments cm
                   LEFT JOIN commitments_log cl
                     ON cl.commitment_id=cm.id AND cl.date=?
                   WHERE cm.active=1 AND cl.id IS NULL ORDER BY cm.id""", (d,))]
        else:
            missing_commitments = [dict(r) for r in c.execute(
                "SELECT id, name FROM commitments WHERE active=1 ORDER BY id")]

    checkin_states = {}
    for kind in ("energy", "focus", "mood"):
        rows = []
        if _table_exists(c, "checkins"):
            rows = c.execute(
                """SELECT time, value FROM checkins
                   WHERE date=? AND kind=? ORDER BY time, id""", (d, kind)).fetchall()
        latest = rows[-1] if rows else None
        checkin_states[kind] = {
            "count": len(rows),
            "latest_time": latest["time"] if latest else None,
            "latest_value": latest["value"] if latest else None,
            "missing_today": latest is None,
        }

    latest_pain = {}
    if _table_exists(c, "pain_log"):
        for row in c.execute(
                """SELECT id, date, region, side, intensity FROM pain_log
                   WHERE voided=0 AND date>=? AND date<=?
                   ORDER BY date, id""", (cutoff, d)):
            latest_pain[(row["region"], row["side"])] = row
    due = [{"region": key[0], "side": key[1],
            "latest_intensity": row["intensity"], "latest_date": row["date"]}
           for key, row in latest_pain.items()
           if row["date"] != d and row["intensity"] is not None and row["intensity"] > 0]
    due.sort(key=lambda item: (-item["latest_intensity"], -date.fromisoformat(
        item["latest_date"]).toordinal(), item["region"], item["side"]))
    due = due[:2]

    prompt_fields = []
    if not day_state["present"]:
        prompt_fields.append("day_rating")
    if not word_state["present"]:
        prompt_fields.append("whole_day_word")
    if checkin_states["mood"]["missing_today"]:
        prompt_fields.append("mood")
    if due:
        prompt_fields.append("pain_change")
    out({"ok": True, "date": d, "day_rating": day_state,
         "whole_day_word": word_state,
         "active_commitments_missing_log": missing_commitments,
         "checkins": checkin_states, "pain_followup_due": due,
         "prompt_fields": prompt_fields,
         "complete_for_prompt": not prompt_fields})

# conditions contrasted between kept and broken days by `adherence`
CONDITION_FIELDS = ("medication_dose_mg", "medication_first_dose_min", "sleep_hours",
                    "sl_bedtime_min", "mood", "energy", "stress", "anxiety",
                    "tr_session", "is_weekend", "habits_done", "steps",
                    "caffeine_mg", "alcohol_units", "word_kept_r7")

def adherence(a):
    """Follow-through rate + trend + the CONDITIONS that predict a kept vs
    broken day. All computed here; 'partly' counts 0.5 toward the rate and is
    excluded from the kept/broke contrast (it is neither side's evidence)."""
    c = cx()
    if not _table_exists(c, "commitments_log"):
        out({"insufficient_data": True, "reason": "no follow-through data logged yet"})
        return
    dates, rows, cov = _daily_frame(c, a.days)
    _add_features(dates, rows)
    logged = [(d, rows[d]["word_kept"]) for d in dates if "word_kept" in rows[d]]
    base = {"meta": _meta(cov), "days_logged": len(logged),
            "window_days": len(dates)}
    if len(logged) < 3:
        out({**base, "insufficient_data": True,
             "needed": ">= 3 whole-day kept/partly/broke logs"})
        return
    vals = [v for _, v in logged]
    counts = {"kept": vals.count(1.0), "partly": vals.count(0.5), "broke": vals.count(0.0)}
    half = len(logged) // 2
    trend = {"first_half_rate": _rnd(st.mean(v for _, v in logged[:half]), 3),
             "second_half_rate": _rnd(st.mean(v for _, v in logged[half:]), 3)} if half >= 2 else None
    # streak: consecutive CALENDAR days ending at the most recent logged day —
    # a gap in logging breaks it (a streak with holes isn't a streak), and so
    # does a 'broke' day.
    streak = 0
    prev = None
    for d, v in reversed(logged):
        if v == 0.0: break
        if prev is not None:
            gap = (date.fromisoformat(prev) - date.fromisoformat(d)).days
            if gap != 1: break
        streak += 1
        prev = d
    # per-commitment rates over the SAME window as the frame
    per = [dict(r) for r in c.execute(
        """SELECT cl.commitment_id, cm.name, COUNT(*) n,
           ROUND(AVG(CASE cl.status WHEN 'kept' THEN 1.0 WHEN 'partly' THEN 0.5
                     ELSE 0.0 END), 3) rate
           FROM commitments_log cl JOIN commitments cm ON cm.id=cl.commitment_id
           WHERE cl.commitment_id != 0 AND cl.date >= ?
           GROUP BY cl.commitment_id ORDER BY rate""", (dates[0],))]
    # conditions: contrast kept vs broke days across the engine's features
    kept_d = [d for d, v in logged if v == 1.0]
    broke_d = [d for d, v in logged if v == 0.0]
    conditions = None
    if len(kept_d) >= 3 and len(broke_d) >= 3:
        conditions = []
        for f in CONDITION_FIELDS:
            kv = [rows[d][f] for d in kept_d if rows[d].get(f) is not None]
            bv = [rows[d][f] for d in broke_d if rows[d].get(f) is not None]
            if len(kv) < 3 or len(bv) < 3: continue
            delta = st.mean(kv) - st.mean(bv)
            pooled = st.pstdev(kv + bv)
            conditions.append({"field": f,
                "kept": {"n": len(kv), "mean": _rnd(st.mean(kv))},
                "broke": {"n": len(bv), "mean": _rnd(st.mean(bv))},
                "delta_mean": _rnd(delta),
                "effect": _rnd(delta / pooled, 3) if pooled > 0 else None})
        conditions.sort(key=lambda s: -(abs(s["effect"]) if s["effect"] is not None else 0))
    out({**base, "rate": _rnd(st.mean(vals), 3), "counts": counts,
         "streak_non_broke": streak, "trend": trend,
         "per_commitment": per,
         "conditions": conditions if conditions is not None else
             f"insufficient data (need >= 3 kept AND >= 3 broke days; have {len(kept_d)}/{len(broke_d)})"})

# =========================================================== §4b timing adherence
# Default tolerance windows (minutes) — owner-configurable per metric via
# planned-time-set. Generous BY DESIGN (no-guilt margins, redesign brief §9f):
# on-time rewards the rhythm; a 10-minute slip still counts as kept.
TIMING_DEFAULT_TOL = {"wake": 30, "bed": 30, "workout": 60, "dose": 30}


def _ensure_planned_times(c):
    """Require migration-owned planned-time config without DDL."""
    _require_schema(c, "planned_times")


def planned_time_set(a):
    """Set/replace the user's planned time (+ tolerance) for one §4b metric.
    Config command — NOT in the bridge allowlists (agent/SSH path only)."""
    metric = (a.metric or "").strip().lower()
    if metric not in TIMING_DEFAULT_TOL:
        sys.exit(f"metric must be one of: {', '.join(TIMING_DEFAULT_TOL)}")
    if _hhmm_min(a.time) is None:
        sys.exit("time must be HH:MM (24h)")
    tol = a.tolerance if a.tolerance is not None else TIMING_DEFAULT_TOL[metric]
    if not 5 <= tol <= 240:
        sys.exit("--tolerance must be 5-240 minutes")
    c = cx(); _ensure_planned_times(c)
    c.execute("""INSERT INTO planned_times(metric, planned, tolerance_min, updated)
        VALUES(?,?,?,datetime('now'))
        ON CONFLICT(metric) DO UPDATE SET planned=excluded.planned,
          tolerance_min=excluded.tolerance_min, updated=datetime('now')""",
        (metric, a.time.strip(), tol))
    c.commit()
    out({"ok": True, "metric": metric, "planned": a.time.strip(), "tolerance_min": tol})


def _circ_diff_min(a_min, b_min):
    return insight_calculations._circ_diff_min(a_min, b_min)


def _start_hhmm(s):
    return insight_calculations._start_hhmm(s, datetime_type=datetime, timezone=CANON_TZ)


def timing_adherence(a):
    """§4b: done AND on time. A task is 'on time' when its actual time falls
    within the metric's tolerance window of the user's planned time; the
    report carries BOTH the done-rate and the on-time-rate, plus an on-time
    streak per metric. Complete days only (today is still in progress).
    Actuals: wake/bed <- sleep_log, first configured medication dose <- meds_log, workout start
    <- hevy_sets (only weekdays scheduled non-Rest count as expected).
    insufficient_data wherever a plan or the actuals are missing — and the
    margins are generous on purpose (no-guilt): a small slip still counts."""
    c = cx(); _ensure_planned_times(c)
    days = max(int(a.days), 7)
    end = date.fromisoformat(today()) - timedelta(days=1)       # complete days only
    start = end - timedelta(days=days - 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    lo = dates[0]
    plans = {r["metric"]: r for r in c.execute("SELECT * FROM planned_times")}

    def gather(metric):
        """{date: minutes-from-midnight} of actuals + unparseable count."""
        actuals, unparsed = {}, 0
        if metric in ("wake", "bed") and _table_exists(c, "sleep_log"):
            col = "wake_time" if metric == "wake" else "bedtime"
            for r in c.execute(f"SELECT date, {col} v FROM sleep_log "
                               f"WHERE date>=? AND {col} IS NOT NULL", (lo,)):
                v = _hhmm_min(r["v"])
                if v is None:
                    unparsed += 1
                else:
                    actuals[r["date"]] = v
        elif metric == "dose" and _table_exists(c, "meds_log"):
            ph = ",".join("?" * len(MEDICATION_ALIASES))
            per = {}
            for r in c.execute(f"SELECT date, time_taken v FROM meds_log "
                               f"WHERE LOWER(drug) IN ({ph}) AND date>=? "
                               f"AND time_taken IS NOT NULL", (*sorted(MEDICATION_ALIASES), lo)):
                v = _hhmm_min(r["v"])           # min() in MINUTES — '8:00' vs '12:30'
                if v is None:                   # sorts wrong as text
                    unparsed += 1
                elif r["date"] not in per or v < per[r["date"]]:
                    per[r["date"]] = v
            actuals = per
        elif metric == "workout" and _table_exists(c, "hevy_sets"):
            per = {}
            for r in c.execute("""SELECT date, MIN(start_time) v FROM hevy_sets
                WHERE date>=? AND COALESCE(set_type,'normal')!='warmup'
                GROUP BY date""", (lo,)):
                v = _start_hhmm(r["v"])
                if v is None:
                    per[r["date"]] = None       # trained, but no usable time
                    unparsed += 1
                else:
                    per[r["date"]] = _hhmm_min(v)
            actuals = per
        return actuals, unparsed

    # workout is only EXPECTED on scheduled (non-Rest) weekdays
    sched_days = set()
    if _table_exists(c, "training_schedule"):
        sched_days = {r["weekday"] for r in c.execute(
            "SELECT weekday, routine_name FROM training_schedule")
            if (r["routine_name"] or "Rest") != "Rest"}

    metrics = {}
    for metric in ("wake", "bed", "workout", "dose"):
        plan = plans.get(metric)
        if plan is None:
            metrics[metric] = {"status": "insufficient_data",
                               "reason": "no planned time set (planned-time-set)",
                               "default_tolerance_min": TIMING_DEFAULT_TOL[metric]}
            continue
        planned_min = _hhmm_min(plan["planned"])
        tol = plan["tolerance_min"]
        actuals, unparsed = gather(metric)
        # complete days only: gather reads date>=lo, so today's (in-progress)
        # rows can be present — drop them before the insufficiency check
        actuals = {d: v for d, v in actuals.items() if d <= dates[-1]}
        if metric == "workout":
            expected = [d for d in dates
                        if WEEKDAYS[date.fromisoformat(d).weekday()] in sched_days]
        else:
            expected = dates
        if not actuals or not expected:
            metrics[metric] = {"status": "insufficient_data",
                               "planned": plan["planned"], "tolerance_min": tol,
                               "reason": ("no scheduled training days" if not expected
                                          else "no actual times logged in the window")}
            continue
        done_days = [d for d in expected if d in actuals]
        timed = {d: v for d, v in actuals.items() if v is not None and d in expected}
        on_time = {d: _circ_diff_min(v, planned_min) <= tol for d, v in timed.items()}
        deltas = sorted(_circ_diff_min(v, planned_min) for v in timed.values())
        # streak: walk back from the most recent expected day; on-time extends,
        # a miss or an off-window day breaks, a day with an unusable time (or a
        # non-expected day, e.g. a rest day) is skipped — neither way.
        streak = 0
        for d in reversed(expected):
            if d not in actuals:
                break                            # expected but not done
            if actuals[d] is None:
                continue                         # done, time unknown — skip
            if on_time[d]:
                streak += 1
            else:
                break
        metrics[metric] = {
            "planned": plan["planned"], "tolerance_min": tol,
            "days_expected": len(expected),
            "done": {"days": len(done_days),
                     "rate": _rnd(len(done_days) / len(expected), 3)},
            "on_time": {"days": sum(on_time.values()),
                        "of_timed_days": len(timed),
                        "rate": _rnd(sum(on_time.values()) / len(timed), 3) if timed else None},
            "median_abs_delta_min": deltas[len(deltas) // 2] if deltas else None,
            "streak_on_time": streak,
            "unparsed_times": unparsed,
        }
    out({"window_days": days, "window": {"from": dates[0], "to": dates[-1]},
         "metrics": metrics,
         "note": ("on-time = within the tolerance window of the planned time "
                  "(generous by design — a small slip still counts; rates are "
                  "rhythm information, never a grade)")})


# =========================================================== insight engine
# Every statistic lives HERE, pure stdlib (repo determinism law): the LLM only
# reasons over this JSON and never computes a number itself. Sparse data comes
# out as explicit nulls / "insufficient data" — never a fabricated value.


# frame field -> pillar; correlate only reports BETWEEN-pillar pairs.
PILLARS = {
  "sleep_hours": "sleep", "sl_asleep_h": "sleep", "sl_quality": "sleep",
  "sl_bedtime_min": "sleep", "sl_awakenings": "sleep", "sl_deep_min": "sleep", "sl_rem_min": "sleep",
  "resting_hr": "recovery", "hrv_ms": "recovery", "respiratory_rate": "recovery",
  "spo2_pct": "recovery", "hr_avg": "recovery", "hr_min": "recovery", "hr_max": "recovery",
  "walking_hr_avg": "recovery",
  "steps": "activity", "active_energy_kcal": "activity", "basal_energy_kcal": "activity",
  "exercise_min": "activity", "distance_km": "activity", "flights": "activity",
  "cardio_min": "activity", "cardio_km": "activity", "cardio_kcal": "activity",
  "tr_sets": "training", "tr_volume_kg": "training", "tr_session": "training", "tr_mean_rpe": "training",
  "day_rating": "subjective", "focus": "subjective", "energy": "subjective", "mood": "subjective",
  "emotional_regulation": "subjective", "anxiety": "subjective", "stress": "subjective",
  "motivation": "subjective",
  "caffeine_mg": "substances", "alcohol_units": "substances",
  "medication_dose_mg": "meds", "medication_first_dose_min": "meds", "medication_n_doses": "meds", "medication_rebound": "meds",
  "bp_sys": "vitals", "bp_dia": "vitals", "cuff_hr": "vitals",
  "nut_kcal": "nutrition", "nut_protein_g": "nutrition", "water_ml": "nutrition",
  "weight_kg": "body", "waist_cm": "body",
  "wx_temp_max_c": "environment", "wx_sunshine_h": "environment", "wx_daylight_h": "environment",
  "wx_precip_mm": "environment", "wx_uv_max": "environment",
  "air_aqi": "environment", "air_pm2_5": "environment", "air_grass_pollen": "environment",
  "air_birch_pollen": "environment",
  "word_kept": "integrity", "commit_kept_rate": "integrity", "habits_done": "integrity",
}
# timed check-ins, bucketed am (<12:00) / pm (<17:00) / eve — within-day
# readings are how dose-timing -> afternoon-crash questions become answerable
PILLARS.update({f"chk_{k}_{b}": "subjective"
                for k in ("energy", "focus", "mood") for b in ("am", "pm", "eve")})
# Symptom fields (higher = worse, per vault CLAUDE.md rating families): correlate
# flips their sign so every reported correlation reads in wellbeing space
# ("positive" always means good-goes-with-good). Flips are listed in meta.
FLIPPED = {"anxiety", "stress", "medication_rebound", "sl_awakenings"}

# fields that get rolling means + day-over-day deltas in `features`
FEATURE_BASE = ("sleep_hours", "resting_hr", "hrv_ms", "steps", "mood", "energy",
                "focus", "anxiety", "stress", "tr_volume_kg", "nut_protein_g",
                "nut_kcal", "medication_dose_mg", "word_kept")
# yesterday's value, for did-X-yesterday -> how-is-today questions
LAG1_FIELDS = ("tr_volume_kg", "tr_session", "medication_dose_mg", "alcohol_units",
               "caffeine_mg", "cardio_min", "sl_bedtime_min", "sleep_hours", "word_kept")

def _table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') "
                     "AND name=?", (name,)).fetchone() is not None

def _hhmm_min(s):
    return insight_calculations._hhmm_min(s)

def _bedtime_min(s):
    """Bedtime as minutes from the PREVIOUS noon, so 23:30 (690) < 00:30 (750)
    orders correctly across midnight and correlates monotonically with 'late'."""
    v = _hhmm_min(s)
    if v is None: return None
    return v + 1440 - 720 if v < 720 else v - 720

def _rnd(v, nd=4):
    return round(v, nd) if isinstance(v, float) else v

def _daily_frame(c, days):
    """One record per calendar day joining every table that exists. Missing
    tables are skipped (and reported), never fabricated. Returns (dates,
    rows: {date: {field: value}}, coverage)."""
    cov = {"missing_tables": [], "table_rows": {}, "dm_source_counts": {},
           "other_medications": [], "unparsed_times": 0}
    end = _now().date()

    def _tbl(name):
        if _table_exists(c, name): return True
        cov["missing_tables"].append(name); return False

    # window start: --days N back from today, or the earliest row anywhere (0 = all)
    if days:
        start = end - timedelta(days=days - 1)
    else:
        mins = []
        for t in ("daily_metrics", "subjective_daily", "workouts", "hevy_sets",
                  "meds_log", "sleep_log", "nutrition_log", "vitals",
                  "commitments_log", "checkins"):
            if _table_exists(c, t):
                r = c.execute(f"SELECT MIN(date) m FROM {t}").fetchone()
                if r and r["m"]: mins.append(r["m"])
        start = date.fromisoformat(min(mins)) if mins else end
    lo = start.isoformat()
    dates = [(start + timedelta(days=i)).isoformat()
             for i in range((end - start).days + 1)]
    rows = {d: {} for d in dates}

    def put(d, field, value):
        if d in rows and value is not None:
            rows[d][field] = _rnd(value)

    def zero_fill_era(seen_dates, fields):
        """Hevy exports / Apple workout history are COMPLETE within the period
        they cover, so between the first and last logged date a day with NO
        rows is a genuine rest day (0), not missing data. Days that DO have a
        row keep their per-field nulls (a 45-min run with unknown km must stay
        km=null, never 0 — review finding), and outside the era we honestly
        don't know, so everything stays null."""
        if not seen_dates: return
        first, last = min(seen_dates), max(seen_dates)
        seen = set(seen_dates)
        for d in dates:
            if first <= d <= last and d not in seen:
                for f in fields: rows[d][f] = 0

    # -- daily_metrics: per-metric primary source with fallback --------------
    if _tbl("daily_metrics"):
        by_date = {}
        n = 0
        for raw in c.execute("SELECT * FROM daily_metrics WHERE date>=?", (lo,)):
            # DM_METRICS indexes rows by "hrv_ms" (the canonical name) — a
            # legacy DB's SELECT * carries "hrv_sdnn" instead (no "hrv_ms" key
            # at all), which would KeyError below. Read path — never DDL, just
            # a Python-side COALESCE: prefer hrv_ms, fall back to hrv_sdnn when
            # hrv_ms is absent or (defensively) null. See _daily_metrics_has_hrv_ms
            # for the writer-side migration; nothing mutates the DB here.
            r = dict(raw)
            if r.get("hrv_ms") is None:
                r["hrv_ms"] = r.get("hrv_sdnn")
            by_date.setdefault(r["date"], {})[(r["source"] or "").lower()] = r
            n += 1
        cov["table_rows"]["daily_metrics"] = n
        counts = {}
        for d, srcs in by_date.items():
            for m in DM_METRICS:
                prio = [DM_PRIMARY[m]] + sorted(s for s in srcs if s != DM_PRIMARY[m])
                for s in prio:
                    r = srcs.get(s)
                    if r is not None and r[m] is not None:
                        put(d, m, r[m])
                        counts.setdefault(m, {}).setdefault(s, 0)
                        counts[m][s] += 1
                        break
        cov["dm_source_counts"] = counts

    # -- subjective_daily -----------------------------------------------------
    if _tbl("subjective_daily"):
        n = 0
        for r in c.execute("SELECT * FROM subjective_daily WHERE date>=?", (lo,)):
            n += 1
            for f in ("day_rating", "focus", "energy", "mood", "emotional_regulation",
                      "anxiety", "stress", "motivation", "caffeine_mg", "alcohol_units"):
                put(r["date"], f, r[f])
        cov["table_rows"]["subjective_daily"] = n

    # -- vitals: day means (several cuff readings per day are normal) --------
    if _tbl("vitals"):
        n = 0
        for r in c.execute("""SELECT date, AVG(systolic) s, AVG(diastolic) d,
                              AVG(resting_hr) h, COUNT(*) n FROM vitals
                              WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]
            put(r["date"], "bp_sys", r["s"]); put(r["date"], "bp_dia", r["d"])
            put(r["date"], "cuff_hr", r["h"])
        cov["table_rows"]["vitals"] = n

    # -- meds_log: configured medication only; unrelated rows never summed --
    if _tbl("meds_log"):
        n = 0; others = set()
        agg = {}
        for r in c.execute("SELECT * FROM meds_log WHERE date>=?", (lo,)):
            n += 1
            drug = (r["drug"] or "").strip().lower()
            if drug not in MEDICATION_ALIASES:
                if drug: others.add(drug)
                continue
            a = agg.setdefault(r["date"], {"mg": 0.0, "n": 0, "times": [], "reb": None})
            if r["dose_mg"] is not None:
                a["mg"] += r["dose_mg"]; a["n"] += 1
            t = _hhmm_min(r["time_taken"])
            if t is not None: a["times"].append(t)
            elif r["time_taken"]: cov["unparsed_times"] += 1
            if r["rebound"] is not None:
                a["reb"] = max(a["reb"] or 0, 1 if r["rebound"] else 0)
        for d, a in agg.items():
            if a["n"]:
                put(d, "medication_dose_mg", a["mg"]); put(d, "medication_n_doses", a["n"])
            if a["times"]: put(d, "medication_first_dose_min", min(a["times"]))
            if a["reb"] is not None: put(d, "medication_rebound", a["reb"])
        cov["table_rows"]["meds_log"] = n
        cov["other_medications"] = sorted(others)

    # -- sleep_log (manual/coach-logged sleep; wearable sleep_hours is separate)
    if _tbl("sleep_log"):
        n = 0
        for r in c.execute("SELECT * FROM sleep_log WHERE date>=?", (lo,)):
            n += 1
            put(r["date"], "sl_asleep_h", r["time_asleep_hours"])
            put(r["date"], "sl_quality", r["quality"])
            put(r["date"], "sl_awakenings", r["awakenings"])
            put(r["date"], "sl_deep_min", r["deep_min"])
            put(r["date"], "sl_rem_min", r["rem_min"])
            put(r["date"], "sl_bedtime_min", _bedtime_min(r["bedtime"]))
        cov["table_rows"]["sleep_log"] = n

    # -- hevy_sets: strength session summary ---------------------------------
    # A warmup-only day is still a training day (tr_session=1): warmups are
    # excluded only from the working-set count/volume, not from "did I train".
    if _tbl("hevy_sets"):
        n = 0; seen = []
        for r in c.execute("""SELECT date, COUNT(*) all_sets,
              SUM(CASE WHEN COALESCE(set_type,'normal')!='warmup' THEN 1 ELSE 0 END) sets,
              SUM(CASE WHEN COALESCE(set_type,'normal')!='warmup'
                  THEN COALESCE(weight_kg,0)*COALESCE(reps,0) ELSE 0 END) vol,
              AVG(CASE WHEN COALESCE(set_type,'normal')!='warmup' THEN rpe END) rpe
              FROM hevy_sets WHERE date>=? AND date IS NOT NULL
              GROUP BY date""", (lo,)):
            n += r["all_sets"]; seen.append(r["date"])
            put(r["date"], "tr_sets", r["sets"])
            put(r["date"], "tr_volume_kg", r["vol"])
            put(r["date"], "tr_session", 1)
            put(r["date"], "tr_mean_rpe", r["rpe"])
        zero_fill_era(seen, ("tr_sets", "tr_volume_kg", "tr_session"))
        cov["table_rows"]["hevy_sets"] = n

    # -- workouts (cardio history) -------------------------------------------
    if _tbl("workouts"):
        n = 0; seen = []
        for r in c.execute("""SELECT date, SUM(minutes) m, SUM(km) km, SUM(kcal) k,
                              COUNT(*) n FROM workouts WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]; seen.append(r["date"])
            put(r["date"], "cardio_min", r["m"]); put(r["date"], "cardio_km", r["km"])
            put(r["date"], "cardio_kcal", r["k"])
        zero_fill_era(seen, ("cardio_min", "cardio_km", "cardio_kcal"))
        cov["table_rows"]["workouts"] = n

    # -- nutrition / intake ----------------------------------------------------
    if _tbl("nutrition_log"):
        n = 0
        for r in c.execute("""SELECT date, SUM(kcal) k, SUM(protein_g) p, COUNT(*) n
                              FROM nutrition_log WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]
            put(r["date"], "nut_kcal", r["k"]); put(r["date"], "nut_protein_g", r["p"])
        cov["table_rows"]["nutrition_log"] = n
    if _tbl("intake"):
        n = 0
        for r in c.execute("SELECT date, water_ml FROM intake WHERE date>=?", (lo,)):
            n += 1; put(r["date"], "water_ml", r["water_ml"])
        cov["table_rows"]["intake"] = n

    # -- habits ---------------------------------------------------------------
    if _tbl("habits_log"):
        n = 0
        for r in c.execute("""SELECT date, SUM(CASE WHEN done THEN 1 ELSE 0 END) d,
                              COUNT(*) n FROM habits_log WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]; put(r["date"], "habits_done", r["d"])
        cov["table_rows"]["habits_log"] = n

    # -- body -----------------------------------------------------------------
    if _tbl("body_metrics"):
        n = 0
        for r in c.execute("""SELECT date, weight_kg, waist_cm FROM body_metrics
                              WHERE date>=? ORDER BY date, id""", (lo,)):
            n += 1
            put(r["date"], "weight_kg", r["weight_kg"]); put(r["date"], "waist_cm", r["waist_cm"])
        cov["table_rows"]["body_metrics"] = n

    # -- environment ----------------------------------------------------------
    if _tbl("weather"):
        n = 0
        for r in c.execute("SELECT * FROM weather WHERE date>=?", (lo,)):
            n += 1
            put(r["date"], "wx_temp_max_c", r["temp_max_c"])
            put(r["date"], "wx_sunshine_h", r["sunshine_hours"])
            put(r["date"], "wx_daylight_h", r["daylight_hours"])
            put(r["date"], "wx_precip_mm", r["precipitation_mm"])
            put(r["date"], "wx_uv_max", r["uv_index_max"])
        cov["table_rows"]["weather"] = n
    if _tbl("air_quality"):
        n = 0
        for r in c.execute("SELECT * FROM air_quality WHERE date>=?", (lo,)):
            n += 1
            put(r["date"], "air_aqi", r["european_aqi_mean"])
            put(r["date"], "air_pm2_5", r["pm2_5_ugm3"])
            put(r["date"], "air_grass_pollen", r["grass_pollen"])
            put(r["date"], "air_birch_pollen", r["birch_pollen"])
        cov["table_rows"]["air_quality"] = n

    # -- follow-through layer (created by Part C; tolerate absence) ----------
    if _table_exists(c, "commitments_log"):
        n = 0
        stat = {"kept": 1.0, "partly": 0.5, "broke": 0.0}
        day_word = {}; per_commit = {}
        for r in c.execute("SELECT * FROM commitments_log WHERE date>=?", (lo,)):
            n += 1
            v = stat.get(r["status"])
            if v is None: continue
            if not r["commitment_id"]:             # 0 (or legacy NULL) = whole-day
                day_word[r["date"]] = v
            else:
                per_commit.setdefault(r["date"], []).append(v)
        for d, v in day_word.items(): put(d, "word_kept", v)
        for d, vs in per_commit.items():
            put(d, "commit_kept_rate", sum(vs) / len(vs))
        cov["table_rows"]["commitments_log"] = n
    else:
        cov["missing_tables"].append("commitments_log")

    # -- timed check-ins, bucketed into day parts -----------------------------
    if _table_exists(c, "checkins"):
        n = 0; agg = {}
        for r in c.execute("SELECT date, time, kind, value FROM checkins WHERE date>=?", (lo,)):
            n += 1
            t = _hhmm_min(r["time"])
            if t is None:
                cov["unparsed_times"] += 1; continue
            bucket = "am" if t < 720 else "pm" if t < 1020 else "eve"
            agg.setdefault((r["date"], r["kind"], bucket), []).append(r["value"])
        for (d, kind, bucket), vs in agg.items():
            put(d, f"chk_{kind}_{bucket}", sum(vs) / len(vs))
        cov["table_rows"]["checkins"] = n
    else:
        cov["missing_tables"].append("checkins")

    # calendar context
    for d in dates:
        wd = date.fromisoformat(d).weekday()        # 0=Mon
        rows[d]["weekday"] = wd
        rows[d]["is_weekend"] = 1 if wd >= 5 else 0

    cov["window"] = {"from": lo, "to": end.isoformat(), "days": len(dates)}
    return dates, rows, cov

def _meta(cov):
    return {"tz": str(CANON_TZ), "window": cov["window"], "coverage": cov,
            "flipped_in_correlations": sorted(FLIPPED),
            "note": "deterministic output; correlation is not causation; "
                    "all numbers computed by health.py, none by the LLM"}

def _sparse(dates, rows):
    return [dict(date=d, **{k: v for k, v in rows[d].items() if v is not None})
            for d in dates]

def build_daily_frame(a):
    """ONE day-by-day record across every pillar — the substrate for features/
    correlate/day-signature and for the coach's Synthesise workflow."""
    dates, rows, cov = _daily_frame(cx(), a.days)
    out({"meta": _meta(cov), "days": _sparse(dates, rows)})

def _add_features(dates, rows):
    """Derived fields raw graphs hide: trailing rolling means (r3 needs >=2,
    r7 needs >=3 non-null days), day-over-day deltas, yesterday's value
    (_lag1), and the configured medication dose-regime run each day belongs to."""
    series = {f: [rows[d].get(f) for d in dates]
              for f in set(FEATURE_BASE) | set(LAG1_FIELDS)}
    for f in FEATURE_BASE:
        vals = series[f]
        for i, d in enumerate(dates):
            for w, mn, tag in ((3, 2, "_r3"), (7, 3, "_r7")):
                win = [v for v in vals[max(0, i - w + 1):i + 1] if v is not None]
                if len(win) >= mn:
                    rows[d][f + tag] = _rnd(sum(win) / len(win))
            if i and vals[i] is not None and vals[i - 1] is not None:
                rows[d][f + "_d1"] = _rnd(vals[i] - vals[i - 1])
    for f in LAG1_FIELDS:
        vals = series[f]
        for i, d in enumerate(dates):
            if i and vals[i - 1] is not None:
                rows[d][f + "_lag1"] = vals[i - 1]
    # dose regime: consecutive run of the same daily total (dosed days only)
    prev_total, run = None, 0
    for d in dates:
        mg = rows[d].get("medication_dose_mg")
        if mg is None: continue
        run = run + 1 if mg == prev_total else 1
        prev_total = mg
        label = int(mg) if float(mg).is_integer() else _rnd(mg)
        rows[d]["medication_regime"] = f"medication:{label}"
        rows[d]["medication_regime_day"] = run
    return rows

def features(a):
    dates, rows, cov = _daily_frame(cx(), a.days)
    _add_features(dates, rows)
    out({"meta": _meta(cov), "days": _sparse(dates, rows)})

def _pillar_of(field):
    base = re.sub(r"_(r3|r7|d1|lag1)$", "", field)
    return PILLARS.get(base), base

def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0: return None
    return sxy / (sxx * syy) ** 0.5

def _ranks(vs):
    order = sorted(range(len(vs)), key=lambda i: vs[i])
    ranks = [0.0] * len(vs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vs[order[j + 1]] == vs[order[i]]:
            j += 1
        r = (i + j) / 2 + 1                          # average rank for ties
        for k in range(i, j + 1): ranks[order[k]] = r
        i = j + 1
    return ranks

def _spearman(xs, ys):
    return _pearson(_ranks(xs), _ranks(ys))

def _strength(rho):
    a = abs(rho)
    return ("negligible" if a < 0.1 else "weak" if a < 0.3 else
            "moderate" if a < 0.5 else "strong" if a < 0.7 else "very strong")

def correlate(a):
    """Cross-PILLAR pairwise associations. Pearson + Spearman with n, direction
    and lag tags; pairs under --min-n are suppressed (counted, never shown as
    findings). Data only — interpretation belongs to the coach workflow."""
    if a.min_n < 3: sys.exit("--min-n must be >= 3")
    dates, rows, cov = _daily_frame(cx(), a.days)
    _add_features(dates, rows)
    fields = sorted({f for d in dates for f in rows[d]
                     if _pillar_of(f)[0] and isinstance(rows[d][f], (int, float))})
    pairs, suppressed, constant = [], 0, 0
    for fa, fb in itertools.combinations(fields, 2):
        pa, ba = _pillar_of(fa); pb, bb = _pillar_of(fb)
        if pa == pb or ba == bb:                    # between-pillar, never self-vs-derived
            continue
        xs, ys = [], []
        for d in dates:
            x, y = rows[d].get(fa), rows[d].get(fb)
            if x is not None and y is not None:
                xs.append(x); ys.append(y)
        if len(xs) < a.min_n:
            suppressed += 1; continue
        r, rho = _pearson(xs, ys), _spearman(xs, ys)
        if r is None or rho is None:
            constant += 1; continue
        flip = (-1 if ba in FLIPPED else 1) * (-1 if bb in FLIPPED else 1)
        lag = ("_lag1" in fa and fa) or ("_lag1" in fb and fb) or None
        pairs.append({"a": fa, "b": fb, "pillars": [pa, pb], "n": len(xs),
                      "pearson": _rnd(r * flip, 3), "spearman": _rnd(rho * flip, 3),
                      "strength": _strength(rho),
                      "lag": f"{lag} is yesterday's value" if lag else None})
    pairs.sort(key=lambda p: (-abs(p["spearman"]), p["a"], p["b"]))
    out({"meta": _meta(cov), "min_n": a.min_n,
         "pairs": pairs[:a.top], "pairs_total": len(pairs),
         "suppressed_below_min_n": suppressed, "skipped_constant": constant})

def day_signature(a):
    """Deterministic contrast of green (day_rating 3) vs red (1) days: mean/
    median of every numeric feature on each side, so 'what makes a good day'
    is computed, not eyeballed. Honest refusal when either side is too thin."""
    if a.min_days < 1: sys.exit("--min-days must be >= 1")
    dates, rows, cov = _daily_frame(cx(), a.days)
    _add_features(dates, rows)
    green = [d for d in dates if rows[d].get("day_rating") == 3]
    red = [d for d in dates if rows[d].get("day_rating") == 1]
    yellow = [d for d in dates if rows[d].get("day_rating") == 2]
    base = {"meta": _meta(cov), "green_days": len(green), "red_days": len(red),
            "yellow_days_excluded": len(yellow), "min_days_per_side": a.min_days}
    if len(green) < a.min_days or len(red) < a.min_days:
        out({**base, "insufficient_data": True,
             "needed": f">= {a.min_days} green AND >= {a.min_days} red rated days"})
        return
    fields = sorted({f for d in green + red for f in rows[d]
                     if f != "day_rating" and _pillar_of(f)[0]
                     and isinstance(rows[d][f], (int, float))})
    sig = []
    for f in fields:
        gv = [rows[d][f] for d in green if rows[d].get(f) is not None]
        rv = [rows[d][f] for d in red if rows[d].get(f) is not None]
        if len(gv) < a.min_days or len(rv) < a.min_days:
            continue
        delta = st.mean(gv) - st.mean(rv)
        pooled = st.pstdev(gv + rv)
        sig.append({"field": f, "pillar": _pillar_of(f)[0],
                    "green": {"n": len(gv), "mean": _rnd(st.mean(gv)), "median": _rnd(st.median(gv))},
                    "red": {"n": len(rv), "mean": _rnd(st.mean(rv)), "median": _rnd(st.median(rv))},
                    "delta_mean": _rnd(delta),
                    "effect": _rnd(delta / pooled, 3) if pooled > 0 else None})
    sig.sort(key=lambda s: -(abs(s["effect"]) if s["effect"] is not None else 0))
    out({**base, "signature": sig})

# --------------------------------------------------------- goal scores
# Dashboard score cards. Every score is an int 0-100 with an ENGINE-assigned
# band and its inputs echoed, or an explicit insufficient_data refusal — the
# panel renders these verbatim and never computes or invents a number.
# Formulas are documented heuristics (info, not grades — see the tone rule):
#   consistency  word-kept rate * 100 over the last <=30 logged days
#                (kept=1, partly=0.5, broke=0) + the calendar streak.  THE hero.
#   sleep        most recent night within 2 days: 70% hours-vs-8h target +
#                30% quality/5 when quality exists, else hours only.
#   recovery     RHR + HRV vs PERSONAL baseline (median over the window,
#                >=14 baseline days required): 50 + 5 pts per 1% RHR below
#                baseline, 50 + 2.5 pts per 1% HRV above, averaged (clamped).
#   muscle_balance  7d logged volume on the radar's 7-group map (MUSCLE_TO_
#                GROUP): 60% coverage of the planned groups + 40% evenness
#                (1 - coefficient of variation across planned groups).
#   water        today's intake vs the configured target.
#   nutrition    ALWAYS insufficient until nutrition data and user-configured
#                targets are available.
def _configured_positive_number(name, default):
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


WATER_TARGET_ML = _configured_positive_number("HERMES_WATER_FALLBACK_ML", 2000)
WATER_ML_PER_KG = _configured_positive_number("HERMES_WATER_ML_PER_KG", 30)
WATER_ML_PER_EXERCISE_HOUR = _configured_positive_number(
    "HERMES_WATER_ML_PER_EXERCISE_HOUR", 500
)
WATER_HOT_DAY_BONUS_ML = _configured_positive_number(
    "HERMES_WATER_HOT_DAY_BONUS_ML", 300
)
WATER_HOT_DAY_TEMP_C = _configured_positive_number(
    "HERMES_WATER_HOT_DAY_TEMP_C", 26
)
SLEEP_TARGET_H = 8.0
# `scores`'s own --days default — T48's readiness engine reuses the SAME
# baseline window (never a second invented constant) so the two engines'
# RHR/HRV baselines can't drift apart.
SCORES_DEFAULT_DAYS = 90


def _configured_micro_targets():
    return insight_catalogs._configured_micro_targets(micro_keys=MICRO_KEYS)


# Cronometer recipe exports use display names rather than the canonical keys
# above. These aliases are intentionally narrow: EPA and DHA are summed into
# the EPA+DHA target, while the broader Omega-3 total (which includes ALA) is
# never substituted for it.
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
# composite weights (HEURISTIC editorial split, documented on the payload):
# protein 25 + kcal 15 + 10 micros × 6 = 100
NUTRITION_WEIGHTS = {"protein": 25, "kcal": 15, "micro_each": 6}
NUTRITION_TARGET_NAMES = {"protein_g", "kcal"}   # owner macro targets


def _ensure_nutrient_tables(c):
    """Idempotent (§4a): per-day nutrient amounts (Cronometer import) + the
    user's macro targets. Amounts upsert per (date, nutrient, source) — a
    reimport corrects in place, nothing is ever deleted."""
    _require_schema(c, "nutrient_daily")
    _require_schema(c, "nutrition_targets")


def nutrition_target_set(a):
    """Owner macro targets for the §4a composite (protein_g, kcal). Config —
    NOT in the bridge allowlists (agent/SSH path only)."""
    name = (a.name or "").strip().lower()
    if name not in NUTRITION_TARGET_NAMES:
        sys.exit(f"name must be one of: {', '.join(sorted(NUTRITION_TARGET_NAMES))}")
    if not (math.isfinite(a.target) and a.target > 0):
        sys.exit("target must be a positive finite number")   # inf zeroes the
        # score forever and NaN dies in sqlite's NULL binding — refuse both
    c = cx(); _ensure_nutrient_tables(c)
    c.execute("""INSERT INTO nutrition_targets(name, target, updated)
        VALUES(?,?,datetime('now'))
        ON CONFLICT(name) DO UPDATE SET target=excluded.target, updated=datetime('now')""",
        (name, float(a.target)))
    c.commit()
    out({"ok": True, "name": name, "target": a.target})


def import_cronometer(a):
    """Compatibility entry point for the extracted import command."""
    out(cronometer_commands.import_csv(_command_context(), a.csv, parse_number=num))

def import_google_health(a):
    """Compatibility entry point for the extracted import command."""
    out(google_health_commands.import_json(
        _command_context(), a.json_file, parse_number=num, stdin=sys.stdin))


SCORE_BAD_CUTOFF = 40
SCORE_GOOD_CUTOFF = 70


def _band(v):
    return (
        "bad" if v < SCORE_BAD_CUTOFF
        else "warn" if v < SCORE_GOOD_CUTOFF
        else "good"
    )

def _clamp100(v):
    return int(round(max(0.0, min(100.0, v))))

def _score(v, **inputs):
    v = _clamp100(v)
    return {"score": v, "band": _band(v), "inputs": inputs}

def _no_data(reason, **inputs):
    d = {"insufficient_data": True, "reason": reason}
    if inputs: d["inputs"] = inputs
    return d


def _recipe_micro_factor(key, unit):
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


def _nutrition_day_values(c, d):
    """Compose one day's score inputs without double counting.

    Logged kcal/protein come from nutrition_log. Recipe micronutrients are
    reconstructed from recipe total / batch grams * grams eaten. A
    nutrient_daily value then replaces the corresponding fallback value
    nutrient-by-nutrient because a Cronometer daily total already includes
    those foods.
    """
    values = {}
    if _table_exists(c, "nutrition_log"):
        macro = c.execute(
            "SELECT SUM(kcal) kcal, SUM(protein_g) protein_g"
            " FROM nutrition_log WHERE date=?", (d,)).fetchone()
        if macro:
            if macro["kcal"] is not None:
                values["energy_kcal"] = macro["kcal"]
            if macro["protein_g"] is not None:
                values["protein_g"] = macro["protein_g"]

    if all(_table_exists(c, name)
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
            factor = _recipe_micro_factor(key, row["unit"])
            if factor is None:
                continue
            amount = (row["per_gram"] / row["batch_grams"]
                      * row["grams"] * factor)
            values[key] = values.get(key, 0.0) + amount

    if _table_exists(c, "nutrient_daily"):
        for row in c.execute(
                "SELECT nutrient, MAX(amount) amount FROM nutrient_daily"
                " WHERE date=? GROUP BY nutrient", (d,)):
            values[row["nutrient"]] = row["amount"]
    return values


def _nutrition_day_score(c, d, targets):
    """Shared per-day §4a scorer (T46).

    Scores the composed daily values from `_nutrition_day_values` against the
    same T44 targets used by nutrition-targets. Cronometer daily totals win
    per nutrient; logged recipe values fill only missing nutrients. The
    weights/credit shapes remain the original composite. Returns None only
    when neither source contains scoreable nutrition for the day.
    """
    nd = _nutrition_day_values(c, d)
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

    score = _clamp100(protein_part + kcal_part + micros_part)
    return {
        "score": score, "band": _band(score),
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

def _anchor_day(raw=None):
    """Return the explicit analysis day, or preserve the ordinary live day."""
    return (date.fromisoformat(valid_date(raw, "--anchor"))
            if raw is not None else _now().date())


def _sleep_score(c, anchor=None, *, include_ancestry=False):
    """Sleep component: most recent night within 2 days (sleep_log first,
    else daily_metrics fitbit-preferred fallback), 70% hours-vs-target + 30%
    quality/5 when quality exists, else hours only. ONE function — scores()'s
    sleep component AND readiness()'s sleep component both call this (T48:
    factor, don't fork). Returns a _score()/_no_data() shape."""
    night = None
    for back in (0, 1):
        d = ((anchor - timedelta(days=back)).isoformat()
             if anchor is not None else days_ago(back))
        r = c.execute(
            "SELECT time_asleep_hours h, quality q, source FROM sleep_log WHERE date=?",
            (d,),
        ).fetchone() if _table_exists(c, "sleep_log") else None
        if r and r["h"] is not None:
            night = {"date": d, "hours": r["h"], "quality": r["q"], "source": "sleep_log"}
            if include_ancestry:
                night["ancestry"] = {
                    "table": "sleep_log",
                    "locator": f"sleep_log:{d}",
                    "source_label": r["source"],
                    "observed_at": d,
                }
            break
        rows = {(x["source"] or "").lower(): x for x in c.execute(
            "SELECT source, sleep_hours FROM daily_metrics WHERE date=?", (d,))}
        for srcname in ["fitbit"] + sorted(k for k in rows if k != "fitbit"):
            selected = rows.get(srcname)
            if selected is not None and selected["sleep_hours"] is not None:
                night = {
                    "date": d,
                    "hours": selected["sleep_hours"],
                    "quality": None,
                    "source": srcname,
                }
                if include_ancestry:
                    night["ancestry"] = {
                        "table": "daily_metrics",
                        "locator": f"daily_metrics:{d}:{selected['source']}",
                        "source_label": selected["source"],
                        "observed_at": d,
                    }
                break
        if night: break
    if not night:
        return _no_data("no sleep data for today or yesterday")
    hours_part = min(night["hours"] / SLEEP_TARGET_H, 1.0) * 100
    if night["quality"] is not None:
        val = 0.7 * hours_part + 0.3 * (night["quality"] / 5 * 100)
    else:
        val = hours_part
    return _score(val, **night, target_h=SLEEP_TARGET_H)


def _recovery_baseline_rows(c, days, anchor=None, start=None):
    """date-deduped daily_metrics rows (fitbit-preferred provenance — the
    user's merge rule) carrying resting_hr/hrv_ms plus each selected source,
    over the trailing
    `days` window. ONE query — shared by scores()'s combined recovery
    component and readiness()'s separate hrv/rhr components (T48: factor,
    don't fork). Rows with neither value are dropped. Read path — resolves
    the migration-owned hrv_ms via a no-DDL guard."""
    hrv_col = "hrv_ms" if _daily_metrics_has_hrv_ms(c) else "hrv_sdnn"
    by_date = {}
    if anchor is None:
        query = (f"""SELECT date, source, resting_hr, {hrv_col} AS hrv_ms FROM daily_metrics
                    WHERE date>=? AND date<=? ORDER BY date""", (days_ago(days), today()))
    else:
        lower = anchor - timedelta(days=days)
        if start is not None:
            lower = max(lower, start)
        query = (f"""SELECT date, source, resting_hr, {hrv_col} AS hrv_ms FROM daily_metrics
                    WHERE date>=? AND date<=? ORDER BY date""",
                 (lower.isoformat(), anchor.isoformat()))
    for r in c.execute(*query):
        by_date.setdefault(r["date"], {})[(r["source"] or "").lower()] = r
    def _pick(srcs, f):
        for sname in ["fitbit"] + sorted(k for k in srcs if k != "fitbit"):
            if sname in srcs and srcs[sname][f] is not None:
                return srcs[sname][f], srcs[sname]["source"], sname
        return None, None, None
    def _available(srcs, f):
        return {
            sname: {
                "source_label": row["source"],
                "value": row[f],
            }
            for sname, row in sorted(srcs.items())
            if row[f] is not None
        }
    base_rows = []
    for d, srcs in sorted(by_date.items()):
        resting_hr, resting_hr_source, resting_hr_source_key = _pick(
            srcs, "resting_hr"
        )
        hrv_ms, hrv_ms_source, hrv_ms_source_key = _pick(srcs, "hrv_ms")
        base_rows.append({
            "date": d,
            "resting_hr": resting_hr,
            "resting_hr_source": resting_hr_source,
            "resting_hr_source_key": resting_hr_source_key,
            "resting_hr_sources": _available(srcs, "resting_hr"),
            "hrv_ms": hrv_ms,
            "hrv_ms_source": hrv_ms_source,
            "hrv_ms_source_key": hrv_ms_source_key,
            "hrv_ms_sources": _available(srcs, "hrv_ms"),
        })
    return [r for r in base_rows if r["resting_hr"] is not None or r["hrv_ms"] is not None]


def _dev_score(value, baseline_vals, coef, invert):
    return insight_calculations._dev_score(value, baseline_vals, coef, invert)


def scores(a):
    c = cx()
    days = max(int(a.days), 14)
    lo = days_ago(days)
    out_scores = {}

    # -- consistency (hero) --------------------------------------------------
    if _table_exists(c, "commitments_log"):
        logged = [(r["date"], {"kept": 1.0, "partly": 0.5, "broke": 0.0}[r["status"]])
                  for r in c.execute(
                      """SELECT date, status FROM commitments_log
                         WHERE commitment_id=0 AND date>=? ORDER BY date""",
                      (days_ago(30),))]
        if len(logged) >= 3:
            rate = st.mean(v for _, v in logged)
            # a streak is only a streak if it reaches the present: the run must
            # include today or yesterday (tonight's answer may not exist yet)
            streak, prev = 0, None
            if logged[-1][0] >= days_ago(1):
                for d, v in reversed(logged):
                    if v == 0.0: break
                    if prev is not None and (date.fromisoformat(prev) - date.fromisoformat(d)).days != 1:
                        break
                    streak += 1; prev = d
            out_scores["consistency"] = _score(rate * 100,
                days_logged=len(logged),
                kept=sum(1 for _, v in logged if v == 1.0),
                partly=sum(1 for _, v in logged if v == 0.5),
                broke=sum(1 for _, v in logged if v == 0.0),
                streak=streak, window_days=30)
        else:
            out_scores["consistency"] = _no_data(
                "needs >= 3 evening kept/partly/broke answers", days_logged=len(logged))
    else:
        out_scores["consistency"] = _no_data("no follow-through data logged yet")

    # -- sleep ----------------------------------------------------------------
    # T48: factored into _sleep_score — readiness()'s sleep component calls
    # the SAME function, never a re-derivation.
    out_scores["sleep"] = _sleep_score(c)

    # -- recovery (personal-baseline relative) --------------------------------
    # T48: the baseline query (_recovery_baseline_rows) and the 50±k mapping
    # (_dev_score) are factored out — readiness()'s separate hrv/rhr
    # components call the SAME two helpers. This block's gating/output is
    # byte-identical to the pre-T48 inline version (known-answer tests pin it).
    base_rows = _recovery_baseline_rows(c, days)
    today_row = next((r for b in (0, 1) for r in base_rows if r["date"] == days_ago(b)), None)
    rhr_base = [r["resting_hr"] for r in base_rows if r["resting_hr"] is not None
                and (not today_row or r["date"] != today_row["date"])]
    hrv_base = [r["hrv_ms"] for r in base_rows if r["hrv_ms"] is not None
                and (not today_row or r["date"] != today_row["date"])]
    if today_row and (len(rhr_base) >= 14 or len(hrv_base) >= 14):
        parts, inputs = [], {"date": today_row["date"]}
        if today_row["resting_hr"] is not None:
            r = _dev_score(today_row["resting_hr"], rhr_base, 500, True)
            if r:
                sc, b = r
                parts.append(sc)
                inputs.update(rhr=today_row["resting_hr"], rhr_baseline=_rnd(b, 1))
        if today_row["hrv_ms"] is not None:
            r = _dev_score(today_row["hrv_ms"], hrv_base, 250, False)
            if r:
                sc, b = r
                parts.append(sc)
                inputs.update(hrv=today_row["hrv_ms"], hrv_baseline=_rnd(b, 1))
        if parts:
            out_scores["recovery"] = _score(st.mean(parts), **inputs,
                                            baseline_days=max(len(rhr_base), len(hrv_base)))
        else:
            out_scores["recovery"] = _no_data("no RHR/HRV reading today or yesterday")
    else:
        out_scores["recovery"] = _no_data(
            "needs a current RHR/HRV reading + >= 14 baseline days",
            baseline_days=max(len(rhr_base), len(hrv_base)))

    # -- muscle balance (7d logged vs planned, on the radar's 7-group map) -----
    # validated behavior 2026-07-09: the score and the radar share ONE rollup —
    # since v2.8 that is _rollup7 (authored-first cited sub-muscle map, coarse
    # Hevy-tag fallback), so the card's "X of Y groups" agrees with the 7-axis
    # radar (Y <= 7). A group counts as PLANNED by presence (any scheduled
    # exercise touches it), not by volume — routine-set without --sets leaves
    # target_sets NULL and must not silently shrink the denominator. Unmapped
    # muscle names are echoed in inputs so dropped volume is diagnosable.
    lg7 = _rollup7(c, "logged", 7)
    pl7 = _rollup7(c, "planned")
    logged_v = lg7["groups"]
    planned_groups = [g for g in MUSCLE_GROUP_AXES if g in pl7["present"]]
    unmapped = sorted(lg7["unmapped"] | pl7["unmapped"])
    unmapped_ex = sorted(set(lg7["unmatched"]) | set(pl7["unmatched"]))
    if sum(logged_v.values()) >= 3 and planned_groups:
        covered = sum(1 for g in planned_groups if logged_v.get(g, 0) >= 2)
        coverage = covered / len(planned_groups)
        vals = [logged_v.get(g, 0.0) for g in planned_groups]
        mean_v = st.mean(vals)
        cv = (st.pstdev(vals) / mean_v) if mean_v > 0 else 1.0
        evenness = max(0.0, 1.0 - min(cv, 1.0))
        out_scores["muscle_balance"] = _score(60 * coverage + 40 * evenness,
            groups_planned=len(planned_groups), groups_covered=covered,
            evenness=_rnd(evenness, 2), window_days=7, unmapped=unmapped,
            unmapped_exercises=unmapped_ex)
    else:
        reason = ("no scheduled exercise maps to the 7 muscle groups — check "
                  "the routine/schedule and the muscle map" if not planned_groups
                  else "needs logged sets this week")
        out_scores["muscle_balance"] = _no_data(
            reason, effective_sets_7d=_rnd(sum(logged_v.values()), 1),
            unmapped=unmapped, unmapped_exercises=unmapped_ex)

    # -- water ------------------------------------------------------------
    # T46: target now comes from the T44 compute engine (_compute_targets ->
    # _water_target: configured baseline + exercise/weather heuristic) instead of the
    # flat WATER_TARGET_ML constant, so the dashboard ring and the Nutrition
    # page's Water surfaces agree on one number. WATER_TARGET_ML remains the
    # engine's own documented no-weight fallback (see _water_target) — both
    # the "ok" and "insufficient_data" shapes of _compute_targets always
    # carry targets.water_ml, so this doesn't need its own profile gate.
    tg = _compute_targets(c)
    water_target = tg["targets"]["water_ml"]["target"]
    w = c.execute("SELECT water_ml FROM intake WHERE date=?", (today(),)).fetchone()
    if w and w["water_ml"] is not None:
        out_scores["water"] = _score(w["water_ml"] / water_target * 100,
                                     water_ml=w["water_ml"], target_ml=water_target)
    else:
        out_scores["water"] = _no_data("no water logged today", target_ml=water_target)

    # -- nutrition (§4a composite: protein 25 + kcal 15 + top-10 micros 60) ----
    # T46: ONE shared scorer (_nutrition_day_score) and ONE targets source
    # (_compute_targets, the T44 engine — the same one nutrition-coverage and
    # the Nutrition page's "% of target" card use) replace the old
    # Cronometer-data + manually-set nutrition_targets-rows gate. A legacy
    # nutrition_targets override (nutrition-target-set) still applies, but
    # now layers ON TOP OF a complete profile rather than substituting for
    # one — see _compute_targets. Weights/credit shapes are UNCHANGED
    # (NUTRITION_WEIGHTS, ±10%→0-at-±25% kcal band, 80%-low-micro flag);
    # only WHERE the targets come from changed.
    nut_day = None
    for back in (0, 1):
        dd = days_ago(back)
        if _nutrition_day_values(c, dd):
            nut_day = dd
            break
    if nut_day is None:
        out_scores["nutrition"] = _no_data(
            "no logged nutrition or Cronometer data for today/yesterday",
            citation_status=MICRO_CITATION_STATUS)
    elif tg["status"] != "ok":
        out_scores["nutrition"] = _no_data(tg["reason"], date=nut_day,
                                           citation_status=MICRO_CITATION_STATUS)
    else:
        res = _nutrition_day_score(c, nut_day, tg["targets"])
        comp = res["components"]
        out_scores["nutrition"] = _score(
            res["score"], date=nut_day, protein_g=comp["protein_g"],
            protein_target=comp["protein_target"], kcal=comp["kcal"],
            kcal_target=comp["kcal_target"], micros=comp["micros"],
            low_micros=comp["low_micros"], weights=NUTRITION_WEIGHTS,
            citation_status=MICRO_CITATION_STATUS)

    # -- mind (T49: stated-weights composite over WHATEVER of these has data —
    #    equal-weight mean, weights stated in output. NO sentiment analysis on
    #    brain_dump text (determinism law); it only feeds a streak count.
    #    anxiety is deliberately NOT included in v1 (symptom-direction field,
    #    owner didn't list it in the spec). A `social` component would join
    #    here once social logging exists in the schema — not emitted as a
    #    null placeholder in the meantime.) ------------------------------
    lo30 = days_ago(30)
    mind_components = []
    if _table_exists(c, "subjective_daily"):
        for key in ("mood", "focus", "emotional_regulation"):
            vals = [r[0] for r in c.execute(
                f"SELECT {key} FROM subjective_daily WHERE date>=? AND {key} IS NOT NULL",
                (lo30,))]
            if vals:
                mv = st.mean(vals)
                mind_components.append({"key": key, "score": _clamp100(mv * 20),
                    "basis": f"30d mean {mv:.1f}/5 over {len(vals)} day(s)"})
        bd_dates = [r[0] for r in c.execute(
            "SELECT date FROM subjective_daily WHERE date>=?"
            " AND TRIM(COALESCE(brain_dump,'')) != '' ORDER BY date", (lo30,))]
        if bd_dates:
            streak, prev = 0, None
            if bd_dates[-1] >= days_ago(1):    # only a run reaching today/yesterday counts
                for d in reversed(bd_dates):
                    if prev is not None and (date.fromisoformat(prev) - date.fromisoformat(d)).days != 1:
                        break
                    streak += 1; prev = d
            capped = min(streak, 14)           # 14-day cap: presentation scaling for
                                                # the 0-100 score, not a clinical claim
            mind_components.append({"key": "brain_dump_streak",
                "score": _clamp100(capped / 14 * 100),
                "basis": f"{streak}-day brain-dump streak (14-day cap for scoring)"})
    if _table_exists(c, "habits_log"):
        hrows = c.execute("SELECT done FROM habits_log WHERE date>=?", (lo30,)).fetchall()
        if hrows:
            done_n = sum(1 for r in hrows if r["done"])
            pct = 100 * done_n / len(hrows)
            mind_components.append({"key": "habit_consistency", "score": _clamp100(pct),
                "basis": f"{done_n}/{len(hrows)} habits_log rows done ({_rnd(pct, 1)}%)"})
    if len(mind_components) < 2:
        have = [mc["key"] for mc in mind_components]
        missing = [k for k in ("mood", "focus", "emotional_regulation",
                                "brain_dump_streak", "habit_consistency") if k not in have]
        out_scores["mind"] = _no_data(
            "needs >= 2 of mood/focus/emotional_regulation/brain_dump_streak/"
            f"habit_consistency logged in the last 30 days (have {len(have)}: "
            f"{', '.join(have) or 'none'}; missing {', '.join(missing)})",
            components=mind_components, window_days=30)
    else:
        mind_val = st.mean(mc["score"] for mc in mind_components)
        out_scores["mind"] = _score(mind_val, components=mind_components,
                                    weights="equal", window_days=30)

    # -- external care (T49: 30-day skincare-routine adherence) ---------------
    # ONE definition, two readers: done = used=1 rows, expected = logged rows,
    # joined to skincare_products with BOTH sides of the fraction filtered to
    # active=1 — the T34 lesson (supplements adherence once filtered only the
    # denominator to active products, inflating/deflating the score off a
    # retired product's historical rows; fixed there by filtering both sides
    # together, applied proactively here from day one). app/routes/dash.py's
    # care() route was aligned to this same active-only semantic in T55
    # (owner flag 1), closing the discrepancy T49 had documented and parked.
    care_lo = days_ago(30)
    care_row = None
    if _table_exists(c, "skincare_log") and _table_exists(c, "skincare_products"):
        care_row = c.execute(
            "SELECT COUNT(*) AS expected, SUM(sl.used) AS done FROM skincare_log sl"
            " JOIN skincare_products sp ON sp.product_id = sl.product_id"
            " WHERE sp.active = 1 AND sl.date >= ?", (care_lo,)).fetchone()
    if care_row and care_row["expected"]:
        expected, done = care_row["expected"], care_row["done"] or 0
        out_scores["care"] = _score(100 * done / expected,
                                    done=done, expected=expected, window_days=30)
    else:
        out_scores["care"] = _no_data(
            "no skincare_log rows for an active product in the last 30 days",
            window_days=30)

    # -- habits & streaks (for the habits card; streak = consecutive days done,
    #    ending at the habit's most recent log; active = logged in last 21 days)
    habits = []
    if _table_exists(c, "habits_log"):
        names = [r["h"] for r in c.execute(
            "SELECT DISTINCT habit h FROM habits_log WHERE date>=? ORDER BY habit",
            (days_ago(21),))]
        for h in names:
            rows = c.execute(
                """SELECT date, MAX(done) done FROM habits_log WHERE habit=? AND date>=?
                   GROUP BY date ORDER BY date""", (h, lo)).fetchall()
            streak, prev = 0, None
            if rows and rows[-1]["date"] >= days_ago(1):   # current runs only
                for r in reversed(rows):
                    if not r["done"]: break
                    if prev is not None and (date.fromisoformat(prev) - date.fromisoformat(r["date"])).days != 1:
                        break
                    streak += 1; prev = r["date"]
            habits.append({"habit": h, "streak": streak,
                           "last_done": next((r["date"] for r in reversed(rows) if r["done"]), None),
                           "done_7d": sum(1 for r in rows if r["done"] and r["date"] >= days_ago(7))})
    out({"meta": {"tz": str(CANON_TZ), "date": today(), "window_days": days,
                  "note": "all scores computed here; bands engine-assigned; "
                          "missing inputs -> insufficient_data, never a guess"},
         "scores": out_scores, "habits": habits})

# --------------------------------------------------------- T48 readiness engine
READINESS_DISCLAIMER = ("Transparent heuristic over your own baselines — "
                        "not medical advice.")
READINESS_EVIDENCE_CONTRACT = "readiness-evidence-v2"
READINESS_MINIMUM_SAME_SOURCE_BASELINE = 14
READINESS_HRV_DEVIATION_COEFFICIENT = 250
READINESS_RHR_DEVIATION_COEFFICIENT = 500
READINESS_TRAINING_VOLUME_DAYS = 7
READINESS_TRAINING_PERFORMANCE_DAYS = 28

# Soreness-note -> muscle_recovery group highlighting. A DISPLAY AID, not
# NLP: plain case-insensitive substring match against the user's own
# free-text note (always shown verbatim regardless of any match). Each
# group's own name is checked automatically; these are just the common
# colloquial synonyms for the 7 MUSCLE_GROUP_AXES.
SORENESS_SYNONYMS = {
    "Legs":      ["quad", "hamstring", "calv", "thigh"],
    "Glutes":    ["glute", "booty"],
    "Back":      ["lat", "trap", "spine"],
    "Chest":     ["pec"],
    "Shoulders": ["delt"],
    "Arms":      ["bicep", "tricep", "forearm"],
    "Core":      ["abs", "abdominal", "oblique"],
}

READINESS_POLICY = {
    "policy_id": "openhealthatlas-readiness-policy",
    "version": "1.0.0",
    "meaning": "non_diagnostic_readiness_heuristic",
    "baseline_days": SCORES_DEFAULT_DAYS,
    "minimum_same_source_baseline_observations": (
        READINESS_MINIMUM_SAME_SOURCE_BASELINE
    ),
    "component_keys": ["sleep", "hrv", "rhr"],
    "composite": "equal_weight_mean_of_available_components",
    "minimum_components": 2,
    "sleep": {
        "target_hours": SLEEP_TARGET_H,
        "method": "hours_target_70pct_plus_optional_quality_30pct",
        "quality_scale_max": 5,
    },
    "hrv": {
        "method": "same_source_median_deviation",
        "deviation_coefficient": READINESS_HRV_DEVIATION_COEFFICIENT,
        "higher_is_better": True,
    },
    "resting_hr": {
        "method": "same_source_median_deviation",
        "deviation_coefficient": READINESS_RHR_DEVIATION_COEFFICIENT,
        "lower_is_better": True,
    },
    "bands": {
        "bad_below": SCORE_BAD_CUTOFF,
        "warn_below": SCORE_GOOD_CUTOFF,
        "good_at_or_above": SCORE_GOOD_CUTOFF,
    },
    "training": {
        "effective_sets_window_days": READINESS_TRAINING_VOLUME_DAYS,
        "performance_window_days": READINESS_TRAINING_PERFORMANCE_DAYS,
        "e1rm_method": "epley-v1",
        "performance_comparison": (
            "latest_session_mean_vs_window_working_set_median"
        ),
        "below_median_rule": "signed_percentage_delta_lt_zero",
        "muscle_mapping": "authored_then_coarse-v2.8",
    },
    "soreness": {
        "derivation": "case_insensitive_group_or_synonym_substring-v1",
        "synonym_map_sha256": insight_provenance.sha256_id(SORENESS_SYNONYMS),
        "meaning": "display_flag_only",
    },
}
READINESS_POLICY_SHA256 = insight_provenance.sha256_id(READINESS_POLICY)


def _readiness_calculation_context():
    """Private code-owned inputs that can change Recovery interpretation.

    This complete context is bound inside the private snapshot sidecar and the
    deterministic input fingerprint.  Its mapping details are not copied into
    the public evidence projection.
    """

    static_mapping_policy = {
        "muscle_group_axes": list(MUSCLE_GROUP_AXES),
        "muscle_to_group": MUSCLE_TO_GROUP,
        "mobility_exercises": sorted(MOBILITY_EXERCISES),
        "non_volume_exercises": sorted(NON_VOLUME_EXERCISES),
    }
    return {
        "policy": READINESS_POLICY,
        "policy_sha256": READINESS_POLICY_SHA256,
        "static_mapping_policy": static_mapping_policy,
        "soreness_synonyms": SORENESS_SYNONYMS,
    }


def _muscle_recovery(c, anchor=None, start=None):
    """Per-7-group (MUSCLE_GROUP_AXES) recovery snapshot for the readiness
    drill — days since a mapped exercise was last trained (ALL-TIME,
    non-warmup hevy_sets), effective sets in the trailing 7d (the SAME
    _rollup7 rollup the radar/muscle_balance score share — never a rival
    count), and for the group's most-frequently-SET exercise over the
    trailing 28d: last-session mean e1RM vs the trailing-28d median e1RM as a
    signed %% delta. `below_median` is SIGN ONLY (delta < 0) — no invented
    magnitude threshold; e1rm() is the one shared formula, never re-derived.
    Groups with no mapped exercise EVER trained get an honest
    days_since=None + note='never logged' row rather than being omitted."""
    authored, coarse = _group_weight_maps(c)

    def _groups_for(title):
        gmap, basis = _basis_weights(title, authored, coarse)
        return set(gmap) if gmap else set()

    if anchor is None:
        last_query = ("""SELECT exercise_title, MAX(date) last FROM hevy_sets
                          WHERE COALESCE(set_type,'normal')!='warmup'
                          GROUP BY exercise_title""", ())
        window_query = ("""SELECT exercise_title, date, weight_kg, reps FROM hevy_sets
            WHERE date >= ? AND COALESCE(set_type,'normal')!='warmup'
            ORDER BY date""", (days_ago(READINESS_TRAINING_PERFORMANCE_DAYS),))
        reference_day = date.fromisoformat(today())
    else:
        last_query = ("""SELECT exercise_title, MAX(date) last FROM hevy_sets
                          WHERE date>=? AND date<=?
                            AND COALESCE(set_type,'normal')!='warmup'
                          GROUP BY exercise_title""",
                      ((start or date.min).isoformat(), anchor.isoformat()))
        window_start = anchor - timedelta(days=READINESS_TRAINING_PERFORMANCE_DAYS)
        if start is not None:
            window_start = max(window_start, start)
        window_query = ("""SELECT exercise_title, date, weight_kg, reps FROM hevy_sets
            WHERE date >= ? AND date <= ?
              AND COALESCE(set_type,'normal')!='warmup'
            ORDER BY date""",
            (window_start.isoformat(), anchor.isoformat()))
        reference_day = anchor
    last_by_ex = {r["exercise_title"]: r["last"] for r in c.execute(*last_query)}
    sets7 = _rollup7(
        c, "logged", READINESS_TRAINING_VOLUME_DAYS, anchor=anchor,
    )["groups"]

    win_rows = c.execute(*window_query).fetchall()
    by_group_ex = {g: {} for g in MUSCLE_GROUP_AXES}
    for r in win_rows:
        for g in _groups_for(r["exercise_title"]):
            by_group_ex[g].setdefault(r["exercise_title"], []).append(r)

    rows_out = []
    for g in MUSCLE_GROUP_AXES:
        last_dates = [d for t, d in last_by_ex.items() if d and g in _groups_for(t)]
        days_since = ((reference_day - date.fromisoformat(max(last_dates))).days
                      if last_dates else None)
        entry = {"group": g, "days_since": days_since, "sets_7d": sets7.get(g, 0.0),
                 "exercise": None, "e1rm_delta_pct": None, "below_median": None,
                 "sore": False}
        if days_since is None:
            entry["note"] = "never logged"
            rows_out.append(entry)
            continue
        exs = by_group_ex.get(g, {})
        if exs:
            # most-frequently-SET exercise (by working-set count in the 28d
            # window), deterministic alpha tiebreak
            top_ex, ex_rows = sorted(exs.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
            entry["exercise"] = top_ex
            last_date = max(r["date"] for r in ex_rows)
            last_vals = [v for v in (e1rm(r["weight_kg"], r["reps"]) for r in ex_rows
                                     if r["date"] == last_date) if v is not None]
            all_vals = [v for v in (e1rm(r["weight_kg"], r["reps"]) for r in ex_rows) if v is not None]
            if last_vals and all_vals:
                median28 = st.median(all_vals)
                if median28 > 0:
                    delta = round(100 * (st.mean(last_vals) - median28) / median28, 1)
                    entry["e1rm_delta_pct"] = delta
                    entry["below_median"] = delta < 0
        rows_out.append(entry)
    return rows_out


def _readiness_metric_evidence(base_rows, today_row, key):
    """Return one same-source baseline plus bounded, non-value ancestry.

    ``daily_metrics`` permits one row per date and source.  The existing merge
    rule still chooses Fitbit first for each metric on each date, but a
    baseline may use only rows selected from the current observation's source.
    This is essential for HRV, whose stored algorithm differs by source, and
    keeps the same integrity rule for resting HR.
    """

    field = "hrv_ms" if key == "hrv" else "resting_hr"
    source_field = f"{field}_source"
    source_key_field = f"{field}_source_key"
    transformation = (
        "same-source-baseline-deviation-hrv-v1"
        if key == "hrv"
        else "same-source-baseline-deviation-rhr-v1"
    )
    current_value = today_row.get(field) if today_row else None
    current_source = today_row.get(source_field) if today_row else None
    current_source_key = today_row.get(source_key_field) if today_row else None
    if current_value is None or current_source is None:
        ancestry = {
            "key": key,
            "status": "missing",
            "reason_code": "no_current_observation",
            "ancestry_state": "missing",
            "current": None,
            "baseline": None,
            "transformation": transformation,
        }
        return ancestry, [], {"key": key, "status": "missing"}, None

    current = {
        "table": "daily_metrics",
        "locator": f"daily_metrics:{today_row['date']}:{current_source}",
        "source_label": current_source,
        "observed_at": today_row["date"],
    }
    available_field = f"{field}_sources"
    baseline_rows = [
        row for row in base_rows if row["date"] != today_row["date"]
    ]
    same_source = [
        (row, row[available_field][current_source_key])
        for row in baseline_rows
        if current_source_key in row[available_field]
    ]
    baseline = {
        "source_label": current_source,
        "observation_count": len(same_source),
        "range_from": same_source[0][0]["date"] if same_source else None,
        "range_to": same_source[-1][0]["date"] if same_source else None,
        "locators": [
            f"daily_metrics:{row['date']}:{selected['source_label']}"
            for row, selected in same_source
        ],
    }
    required = READINESS_MINIMUM_SAME_SOURCE_BASELINE
    baseline_values = [selected["value"] for _row, selected in same_source]
    enough = len(same_source) >= required
    positive = enough and st.median(baseline_values) > 0
    reason_code = (
        None
        if positive
        else (
            "insufficient_same_source_baseline"
            if not enough
            else "nonpositive_same_source_baseline"
        )
    )
    ancestry = {
        "key": key,
        "status": "included" if positive else "excluded",
        "reason_code": reason_code,
        "ancestry_state": "source_rows_identified",
        "current": current,
        "baseline": baseline,
        "transformation": transformation,
    }
    fingerprint_input = {
        "key": key,
        "status": ancestry["status"],
        "reason_code": ancestry["reason_code"],
        "current": {**current, "value": current_value},
        "baseline": [
            {
                "locator": f"daily_metrics:{row['date']}:{selected['source_label']}",
                "source_label": selected["source_label"],
                "observed_at": row["date"],
                "value": selected["value"],
            }
            for row, selected in same_source
        ],
        "transformation": transformation,
    }
    warning = None
    if not positive:
        warning = {
            "code": reason_code,
            "component": key,
            "source_label": current_source,
            "required": required,
            "observed": len(same_source),
            "other_source_observations_excluded": (
                sum(
                    1
                    for row in baseline_rows
                    for source_key in row[available_field]
                    if source_key != current_source_key
                )
            ),
        }
    return ancestry, baseline_values, fingerprint_input, warning


def _readiness_result(c, *, anchor, range_start, snapshot_attestation):
    """T48 readiness engine: a transparent equal-weight-mean composite of the
    SAME sleep/hrv/rhr math scores() uses — sleep via _sleep_score, hrv/rhr
    via _recovery_baseline_rows + _dev_score (factored, never forked; same
    candidate window via SCORES_DEFAULT_DAYS and same constants). Readiness
    additionally requires every HRV/RHR baseline to match the selected current
    source. `drag[i].points` =
    (100-component)/n, the EXACT arithmetic shortfall each included
    component contributes under that equal-weight mean — deterministic, no
    invented per-component weights. Fewer than 2 available components ->
    insufficient_data (muscle_recovery + soreness are independent of the
    composite and are still returned)."""
    components = []

    evidence_components = []
    evidence_warnings = []
    fingerprint_components = []

    sleep_c = _sleep_score(
        c, anchor=anchor,
        include_ancestry=True,
    )
    if not sleep_c.get("insufficient_data"):
        i = sleep_c["inputs"]
        q = f" · quality {i['quality']}/5" if i.get("quality") is not None else ""
        components.append({"key": "sleep", "score": sleep_c["score"], "value": i["hours"],
            "basis": f"{i['hours']:.1f}h{q} vs {SLEEP_TARGET_H:.0f}h target"})
        sleep_ancestry = i["ancestry"]
        evidence_components.append({
            "key": "sleep",
            "status": "included",
            "reason_code": None,
            "ancestry_state": (
                "source_row_identified"
                if sleep_ancestry.get("source_label")
                else "row_identified_source_missing"
            ),
            "current": sleep_ancestry,
            "baseline": None,
            "transformation": "sleep-score-v1",
        })
        fingerprint_components.append({
            "key": "sleep",
            "status": "included",
            "current": {
                **sleep_ancestry,
                "hours": i["hours"],
                "quality": i.get("quality"),
            },
            "target_hours": SLEEP_TARGET_H,
            "transformation": "sleep-score-v1",
        })
    else:
        evidence_components.append({
            "key": "sleep",
            "status": "missing",
            "reason_code": "no_current_observation",
            "ancestry_state": "missing",
            "current": None,
            "baseline": None,
            "transformation": "sleep-score-v1",
        })
        fingerprint_components.append({"key": "sleep", "status": "missing"})

    bounded_anchor = anchor
    base_rows = _recovery_baseline_rows(
        c, SCORES_DEFAULT_DAYS, anchor=bounded_anchor, start=range_start,
    )
    current_dates = [(anchor - timedelta(days=b)).isoformat() for b in (0, 1)]
    today_row = next((r for d in current_dates for r in base_rows if r["date"] == d), None)
    for key, field, coef, invert, unit in (
        (
            "hrv", "hrv_ms", READINESS_HRV_DEVIATION_COEFFICIENT,
            False, "ms",
        ),
        (
            "rhr", "resting_hr", READINESS_RHR_DEVIATION_COEFFICIENT,
            True, "bpm",
        ),
    ):
        ancestry, baseline_values, fingerprint_input, warning = (
            _readiness_metric_evidence(base_rows, today_row, key)
        )
        evidence_components.append(ancestry)
        fingerprint_components.append(fingerprint_input)
        if warning is not None:
            evidence_warnings.append(warning)
        if ancestry["status"] != "included":
            continue
        dev = _dev_score(today_row[field], baseline_values, coef, invert)
        if dev:
            sc, b = dev
            components.append({
                "key": key,
                "score": _clamp100(sc),
                "value": today_row[field],
                "basis": (
                    f"{today_row[field]:.0f} {unit} vs {b:.0f} {unit} "
                    "same-source baseline (14+ day median)"
                ),
            })

    muscle_recovery = _muscle_recovery(
        c, anchor=bounded_anchor, start=range_start,
    )

    # soreness: today's, else yesterday's, subjective_daily.soreness_note
    # VERBATIM inside the private deterministic result. The field is owned by
    # Migration 001: this read requires it and never performs compatibility
    # DDL. With a trusted ledger, a legacy column shape therefore reports the
    # explicit schema_migration_required error.
    soreness = None
    soreness_source = None
    if _table_exists(c, "subjective_daily") and _subjective_daily_has_soreness_note(c):
        for back in (0, 1):
            d = (anchor - timedelta(days=back)).isoformat()
            row = c.execute(
                "SELECT soreness_note, source FROM subjective_daily WHERE date=?",
                (d,),
            ).fetchone()
            if row and row["soreness_note"]:
                soreness = {"date": d, "note": row["soreness_note"]}
                soreness_source = row["source"]
                break
    if soreness:
        note_l = soreness["note"].lower()
        for entry in muscle_recovery:
            terms = [entry["group"].lower()] + SORENESS_SYNONYMS.get(entry["group"], [])
            entry["sore"] = any(t in note_l for t in terms)

    if soreness:
        soreness_evidence = {
            "status": "present",
            "table": "subjective_daily",
            "locator": f"subjective_daily:{soreness['date']}",
            "source_label": soreness_source,
            "observed_at": soreness["date"],
        }
        soreness_fingerprint_input = {
            **soreness_evidence,
            "sore_groups": sorted(
                entry["group"] for entry in muscle_recovery if entry["sore"]
            ),
        }
    else:
        soreness_evidence = {
            "status": "missing",
            "table": "subjective_daily",
            "locator": None,
            "source_label": None,
            "observed_at": None,
        }
        soreness_fingerprint_input = {"status": "missing"}

    fingerprint_payload = {
        "contract": READINESS_EVIDENCE_CONTRACT,
        "policy_sha256": READINESS_POLICY_SHA256,
        "range": {
            "from": range_start.isoformat() if range_start else None,
            "anchor": anchor.isoformat(),
        },
        "components": fingerprint_components,
        "muscle_recovery": muscle_recovery,
        "soreness": soreness_fingerprint_input,
    }
    # The private sidecar separately binds exact row bytes, including the raw
    # note.  Neither that digest nor the raw note enters this public identity:
    # equivalent wording with the same bounded sore-group flags has the same
    # calculation identity.
    evidence = {
        "contract": READINESS_EVIDENCE_CONTRACT,
        "input_fingerprint": insight_provenance.sha256_id(fingerprint_payload),
        "fingerprint_scope": [
            "calculation_relevant_projection", "calculation_policy", "range",
        ],
        "policy": READINESS_POLICY,
        "policy_sha256": READINESS_POLICY_SHA256,
        "snapshot_integrity": dict(snapshot_attestation.public_summary),
        "components": evidence_components,
        "soreness": soreness_evidence,
        "warnings": evidence_warnings,
        "remaining_ancestry_gaps": [],
    }
    evidence["public_evidence_identity"] = insight_provenance.sha256_id(evidence)

    if len(components) < 2:
        return {"status": "insufficient_data", "anchor_date": anchor.isoformat(),
                "range_from": range_start.isoformat() if range_start else None,
                "reason": f"needs >= 2 of sleep/HRV/resting HR (have {len(components)})",
                "components": components, "muscle_recovery": muscle_recovery,
                "soreness": soreness, "evidence": evidence,
                "disclaimer": READINESS_DISCLAIMER}

    n = len(components)
    score = _clamp100(st.mean(comp["score"] for comp in components))
    drag = sorted(({"key": comp["key"],
                    "points": round((100 - comp["score"]) / n, 1),
                    "label": f"{comp['key']} — {round((100 - comp['score']) / n, 1)} pts"}
                   for comp in components), key=lambda d: -d["points"])
    return {"status": "ok", "anchor_date": anchor.isoformat(),
            "range_from": range_start.isoformat() if range_start else None,
            "score": score, "band": _band(score),
            "components": components, "drag": drag,
            "muscle_recovery": muscle_recovery, "soreness": soreness,
            "evidence": evidence, "disclaimer": READINESS_DISCLAIMER}


def readiness(a):
    """Run Recovery inside one file-bound, read-only SQLite snapshot."""

    anchor = _anchor_day(a.anchor)
    range_start = (date.fromisoformat(valid_date(a.from_date, "--from"))
                   if a.from_date is not None else None)
    if range_start is not None and range_start > anchor:
        sys.exit("--from must not be after --anchor")
    with insight_readiness_ancestry.verified_readiness_snapshot(
        DB,
        range_start=range_start,
        anchor=anchor,
        calculation_context=_readiness_calculation_context(),
    ) as (connection, snapshot_attestation):
        result = _readiness_result(
            connection,
            anchor=anchor,
            range_start=range_start,
            snapshot_attestation=snapshot_attestation,
        )
    out(result)

# --------------------------------------------------------- data-to-add ranker
# What each signal unlocks and how much it costs to capture. `weight` (1-5,
# how much analysis it enables), `effort` (1 = one tap, 2 = semi-automatic,
# 3 = manual work) and `target_pct` (expected coverage at PERFECT adherence —
# a weekly weigh-in tops out at ~14%, a 3x/week cuff at ~40%) are EDITORIAL
# CONSTANTS — priorities, not measurements; the only computed inputs are the
# live coverage percentages.
COVERAGE_CATALOG = [
  {"field": "day_rating", "target_pct": 90, "how": "one tap, dashboard/Telegram, evenings", "effort": 1, "weight": 5,
   "unlocks": "the green-vs-red day signature — without rated days 'what makes a good day' cannot be computed at all"},
  {"field": "word_kept", "target_pct": 90, "how": "one tap: kept/partly/broke, evenings", "effort": 1, "weight": 5,
   "unlocks": "follow-through rate + the conditions that predict keeping your word (the self-integrity goal is unmeasurable without it)"},
  {"field": "medication_dose_mg", "target_pct": 80, "how": "tell the coach dose+time when you take it", "effort": 1, "weight": 5,
   "unlocks": "dose/timing vs focus, rebound, and sleep for discussion with a clinician"},
  {"field": "bp_sys", "target_pct": 40, "how": "cuff reading, ~3x/week (quick-log)", "effort": 2, "weight": 5,
   "unlocks": "descriptive blood-pressure context alongside a configured medication"},
  {"field": "focus", "target_pct": 90, "how": "quick-log 1-5, evenings", "effort": 1, "weight": 4,
   "unlocks": "sleep→focus and dose→focus links for a user-selected outcome"},
  {"field": "chk_energy_pm", "target_pct": 60, "how": "one tap 'energy now' in the afternoon", "effort": 1, "weight": 4,
   "unlocks": "wear-off/afternoon-crash detection — daily ratings average it away"},
  {"field": "sl_bedtime_min", "target_pct": 90, "how": "log bedtime (or wearable sleep import)", "effort": 1, "weight": 4,
   "unlocks": "bedtime vs next-day everything — the most controllable upstream lever"},
  {"field": "tr_volume_kg", "target_pct": 90, "how": "log sets in the Training tab / Hevy", "effort": 1, "weight": 4,
   "unlocks": "training→mood/sleep lags and progression vs recovery"},
  {"field": "tr_mean_rpe", "target_pct": 40, "how": "add RPE when logging sets", "effort": 1, "weight": 3,
   "unlocks": "effort-adjusted training load — distinguishes a heavy day from a long one"},
  {"field": "nut_protein_g", "target_pct": 90, "how": "eat/log-food from the freezer menu", "effort": 2, "weight": 4,
   "unlocks": "protein vs recovery/strength — the recomposition pillar's only input"},
  {"field": "caffeine_mg", "target_pct": 90, "how": "mention coffees in the evening check-in", "effort": 1, "weight": 3,
   "unlocks": "separates caffeine from configured-medication effects"},
  {"field": "alcohol_units", "target_pct": 90, "how": "mention drinks in the evening check-in", "effort": 1, "weight": 3,
   "unlocks": "alcohol vs sleep quality/HRV — a classic hidden confounder"},
  {"field": "weight_kg", "target_pct": 14, "how": "scale, ~weekly", "effort": 2, "weight": 3,
   "unlocks": "recomposition trend context (waist/photos stay the headline)"},
  {"field": "water_ml", "target_pct": 90, "how": "+250/+500 taps", "effort": 1, "weight": 2,
   "unlocks": "hydration vs headaches/energy (weak signal, cheapest capture)"},
]

def data_coverage(a):
    """Rank the data NOT being captured by insight-per-effort. Coverage %s are
    computed from the live frame; weights/effort are editorial constants (see
    COVERAGE_CATALOG). score = (1 - coverage) * weight / effort."""
    dates, rows, cov = _daily_frame(cx(), a.days)
    n_days = len(dates)
    ranked = []
    for item in COVERAGE_CATALOG:
        f = item["field"]
        have = sum(1 for d in dates if rows[d].get(f) is not None)
        coverage = have / n_days if n_days else 0.0
        # attainment: coverage relative to the signal's own target cadence, so
        # a perfect weekly weigh-in (14%) reads as done, not forever-missing.
        attainment = min(1.0, coverage / (item["target_pct"] / 100))
        ranked.append({**item, "days_with_data": have, "window_days": n_days,
                       "coverage_pct": _rnd(100 * coverage, 1),
                       "attainment_pct": _rnd(100 * attainment, 1),
                       "score": _rnd((1 - attainment) * item["weight"] / item["effort"], 3)})
    ranked.sort(key=lambda r: (-r["score"], r["field"]))
    out({"meta": _meta(cov), "window_days": n_days, "ranked": ranked,
         "note": "score = (1-attainment) * weight / effort; attainment = "
                 "coverage vs the signal's target cadence; weight/effort/"
                 "target are editorial constants, coverage is measured"})

# =========================================================== §3e/§3c/§3d fitness
def _catalog_or_die(mv):
    spec = CATALOG.get(mv)
    if spec is None:
        near = [m for m in CATALOG if mv and (mv in m or m.startswith(mv[:3]))][:5]
        hint = f" — did you mean: {', '.join(near)}?" if near else ""
        sys.exit(f"unknown movement '{mv}'{hint} (see the catalog)")
    return spec


def fitness_test_log(a):
    """Log ONE fitness test (movement, side, value, date) into fitness_tests.
    Validation is catalog-driven: the movement must exist, unilateral movements
    require a side, and the value fields must match the movement's kind. e1RM is
    NOT stored — it's derived at read time from load×reps (one formula home)."""
    mv = (a.movement or "").strip().lower()
    spec = _catalog_or_die(mv)
    # side: required (left|right) for unilateral, must be bilateral otherwise.
    side = (a.side or "").strip().lower() or ("bilateral" if not spec["unilateral"] else "")
    if spec["unilateral"] and side not in ("left", "right"):
        sys.exit(f"'{mv}' is unilateral — pass --side left or --side right")
    if not spec["unilateral"] and side != "bilateral":
        sys.exit(f"'{mv}' is bilateral — omit --side (or use 'bilateral')")
    # kind gating: exactly the required value fields, clamped.
    vals = {"load_kg": a.load, "reps": a.reps, "seconds": a.seconds,
            "rating": a.rating, "cm": a.cm, "degrees": a.degrees,
            "passed": a.passed}
    required = KIND_FIELDS[spec["kind"]]
    missing = [f for f in required if vals[f] is None]
    if missing:
        sys.exit(f"'{mv}' ({spec['kind']}) needs {', '.join(required)} — missing {', '.join(missing)}")
    for f, v in vals.items():
        if v is None:
            continue
        lo, hi = FT_CLAMPS[f]
        if not lo <= v <= hi:
            sys.exit(f"{f}={v} out of range [{lo}, {hi}]")
        if f == "reps" and float(v) != int(v):
            sys.exit("reps must be a whole number")
    d = valid_date(a.date) if a.date else today()
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        _ensure_fitness_tables(c)
        c.execute("""INSERT INTO fitness_tests(date, movement, side, load_kg, reps,
            seconds, rating, cm, degrees, passed, equipment_note, source)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d, mv, side, vals["load_kg"], vals["reps"], vals["seconds"],
             vals["rating"], vals["cm"], vals["degrees"], vals["passed"],
             (a.note or None), (a.source or "chat")))
        row_id = str(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        if insight_migrations.recorded_version(c) >= 4:
            insight_orchestrator.enqueue_internal_trigger(
                c,
                trigger_kind="quarterly_observation",
                source_table="fitness_tests",
                source_row_key=row_id,
                event_date=d,
            )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    res = {"ok": True, "logged": {"movement": mv, "name": spec["name"], "side": side,
                                  "kind": spec["kind"], "date": d}}
    if spec["kind"] == "strength":
        res["e1rm"] = e1rm(vals["load_kg"], vals["reps"])
        if not 6 <= vals["reps"] <= 8:
            res["outside_protocol_range"] = True   # protocol asks 6–8RM; stored anyway
    out(res)


def fitness_test_void(a):
    """Soft-void a mistaken test row (validated behavior 2026-07-10). The row is
    NEVER deleted — voided=1 + reason preserves the audit trail; every read
    skips voided rows. Deliberately NOT in the bridge allowlists (chat/SSH only),
    so the sandboxed panel can never void logged data."""
    c = cx(); _ensure_fitness_tables(c)
    row = c.execute("SELECT id, voided FROM fitness_tests WHERE id=?", (a.id,)).fetchone()
    if row is None:
        sys.exit(f"no fitness_tests row id={a.id}")
    if row["voided"]:
        sys.exit(f"row id={a.id} already voided")
    c.execute("UPDATE fitness_tests SET voided=1, void_reason=? WHERE id=?",
              ((a.reason or "").strip() or None, a.id))
    c.commit()
    out({"ok": True, "voided": a.id, "reason": (a.reason or "").strip() or None})


def _latest_tests(c, days=ATHLETIC_STALE_DAYS):
    return insight_calculations._latest_tests(
        c, days, today=today, days_ago=days_ago, catalog=CATALOG, estimate_1rm=e1rm,
    )


def _current_strength_distribution(c):
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
    _ensure_fitness_tables(c)
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
            "distribution_pct": _relative_distribution(groups),
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
                "distribution_pct": _relative_distribution(groups),
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


def fitness_tests(a):
    """Read: latest non-voided result per (movement, side), plus priority coverage
    (what the quarter still needs). e1RM computed here, never stored."""
    c = cx(); _ensure_fitness_tables(c)
    latest = _latest_tests(c, a.days)
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
    now = date.fromisoformat(today())
    quarter_start = date(now.year, 3 * ((now.month - 1) // 3) + 1, 1)
    quarter_latest = _latest_tests(c, (now - quarter_start).days)
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
    out({"days": a.days, "quarter": quarter, "tests": tests,
         "priority_total": len(core), "priority_missing": missing,
         "priority_covered": len(core) - len(missing)})


def _target(c, axis, lift=""):
    r = c.execute("SELECT target FROM athletic_targets WHERE axis=? AND lift=?",
                  (axis, lift)).fetchone()
    return r["target"] if r else None


def athletic_target_set(a):
    """Set/replace an owner target for a §3c axis (editable seeded targets). For
    Strength, --lift names the Hevy exercise the target e1RM applies to; other
    axes use the bare axis. Targets are owner config, never model-guessed."""
    axis = (a.axis or "").strip().lower()
    if axis not in ATHLETIC_AXES:
        sys.exit(f"axis must be one of {', '.join(ATHLETIC_AXES)}")
    lift = (a.lift or "").strip()
    if axis == "strength" and not lift:
        sys.exit("--lift is required for the strength axis (per-lift e1RM target)")
    if axis != "strength" and lift:
        sys.exit(f"--lift only applies to the strength axis, not {axis}")
    if a.target <= 0:
        sys.exit("target must be positive")
    c = cx(); _ensure_fitness_tables(c)
    c.execute("""INSERT INTO athletic_targets(axis, lift, target, updated)
        VALUES(?,?,?,datetime('now'))
        ON CONFLICT(axis, lift) DO UPDATE SET target=excluded.target, updated=datetime('now')""",
        (axis, lift, float(a.target)))
    c.commit()
    out({"ok": True, "axis": axis, "lift": lift or None, "target": a.target})


def _axis_score(result, target, better):
    return insight_calculations._axis_score(result, target, better)


def athletic_radar(a):
    """§3c: 5 axes (Strength, Endurance, Speed, Balance, Flexibility) each scored
    0–100 vs an owner target. insufficient_data until BOTH a target is set and a
    test is logged. Scores older than the quarterly window are kept but flagged
    stale. Balance uses the weaker side (also feeds the §3a left/right radar)."""
    c = cx(); _ensure_fitness_tables(c)
    latest = _latest_tests(c, ATHLETIC_STALE_DAYS + 3650)   # wide read; staleness flagged, not hidden
    axes = {}
    for axis, cfg in ATHLETIC_AXES.items():
        if cfg["agg"] == "e1rm_lift":
            axes[axis] = _strength_axis(c)
            continue
        mv = cfg["test"]
        target = _target(c, axis)
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
        axes[axis] = {"score": _axis_score(got, target, better), "test": mv,
                      "result": got, "target": target, "tested_date": src["date"],
                      "stale": src["days_since"] > ATHLETIC_STALE_DAYS,
                      "days_since": src["days_since"]}
    out({"axes": ["strength", "endurance", "speed", "balance", "flexibility"],
         "scores": axes, "stale_after_days": ATHLETIC_STALE_DAYS})


def _strength_axis(c):
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
            (t["lift"], days_ago(28))).fetchone()
        if row is None:
            missing.append(t["lift"]); continue
        best = e1rm(row["weight_kg"], row["reps"])
        lifts.append({"lift": t["lift"], "e1rm": best, "target": t["target"],
                      "score": _axis_score(best, t["target"], "higher")})
    if not lifts:
        return {"status": "insufficient_data", "reason": "no recent working sets for the targeted lifts",
                "test": "hevy-e1rm", "awaiting": missing}
    return {"score": round(sum(x["score"] for x in lifts) / len(lifts)),
            "test": "hevy-e1rm", "lifts": lifts, "missing_lifts": missing}


def _ratio_flag(ratio, band):
    return insight_calculations._ratio_flag(ratio, band)


def _tested_ratios(c):
    return insight_calculations._tested_ratios(
        c, latest_tests=_latest_tests, stale_days=ATHLETIC_STALE_DAYS,
        ratio_seed=RATIO_SEED, ratio_flag=_ratio_flag,
    )


def _pattern_e1rm(c, pattern):
    """Best 28-day working-set e1RM across the compound lifts mapped to a pattern."""
    titles = [t for t, p in PATTERN_MAP.items() if p == pattern]
    if not titles:
        return None
    ph = ",".join("?" * len(titles))
    row = c.execute(f"""SELECT weight_kg, reps FROM hevy_sets
        WHERE exercise_title IN ({ph}) AND COALESCE(set_type,'normal')!='warmup'
          AND weight_kg IS NOT NULL AND reps IS NOT NULL AND date >= ?
        ORDER BY weight_kg*(1+reps/30.0) DESC LIMIT 1""",
        (*titles, days_ago(28))).fetchone()
    return e1rm(row["weight_kg"], row["reps"]) if row else None


def _everyday_ratios(c):
    """Everyday view: movement-PATTERN balance from compound e1RM. HEURISTIC
    targets, trend-only where none is defensible. Explicitly pattern-level — a
    weak row can't localise to a muscle (that's the tested view's job)."""
    rows = []
    for num_p, den_p, label, target in PATTERN_PAIRS:
        n = _pattern_e1rm(c, num_p); d = _pattern_e1rm(c, den_p)
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


def strength_ratios(a):
    """§3d: agonist:antagonist ratios. Two views — 'tested' (valid, from quarterly
    isolation tests, clinical targets) and 'everyday' (estimate, from compound
    e1RM, heuristic targets, no injury-risk claims). The e1RM-vs-isokinetic caveat
    rides on every payload."""
    c = cx(); _ensure_fitness_tables(c)
    view = (a.view or "tested").lower()
    caveat = ("Estimated 1RM (Epley) is not isokinetic dynamometry; targets are "
              "guides. Everyday view is movement-pattern level, "
              "not muscle-specific.")
    if view == "everyday":
        rows, unmapped = _everyday_ratios(c)
        out({"view": "everyday", "rows": rows, "unmapped_exercises": unmapped,
             "caveat": caveat, "note": "heuristic targets; trend-only flags — never clinical"})
    else:
        out({"view": "tested", "rows": _tested_ratios(c), "caveat": caveat,
             "asymmetry_threshold": 0.15})


# =========================================================== §3b sub-muscle scaffold
def _ensure_submuscle_table(c):
    """Idempotent (§3b): the curated exercise→sub-region map. Rows may only
    be seeded deterministically from the user's CITED authored map
    (docs/authored-submuscle-map.md via import-submuscle-map); the model
    must never guess sub-region activation at render time (PRD §3b).
    `source` is NOT NULL so an uncited row cannot exist even by accident.
    Laterality is manual curation and is part of the key so a unilateral
    exercise can seed a left AND a right row."""
    _require_schema(c, "exercise_submuscles", "approx", "iso", "confidence")


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


def _bounded_score(value):
    return max(0.0, min(100.0, float(value)))


def _personal_performance(by_date):
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
    progress = _bounded_score(50.0 + 2.0 * change_pct)
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


def _fitness_value(row):
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


def _subregion_strength_theories(c, group, map_rows):
    """Return ranked, explicitly probabilistic relative-strength theories.

    Evidence is kept in three separate signals:
    - exposure: weighted working-set recurrence (support only, never strength);
    - performance: e1RM normalized within each exercise's own history;
    - direct tests: repeated protocol values normalized within that test.

    The score is a comparative index within one muscle group, not an absolute
    strength percentage, diagnosis, or claim that activation was measured.
    """
    lo = days_ago(SUBREGION_THEORY_DAYS)
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
    hevy_perf = {title: _personal_performance(days)
                 for title, days in hevy_daily.items()}

    test_dates = {}
    test_daily = {}
    for row in c.execute(
            "SELECT date,movement,side,load_kg,reps,seconds,rating,cm,degrees,passed "
            "FROM fitness_tests WHERE voided=0 AND date>=? ORDER BY date,id", (lo,)):
        title = FITNESS_TO_AUTHORED_EXERCISE.get(row["movement"])
        if title not in rows_by_exercise:
            continue
        value = _fitness_value(row)
        if value is None:
            continue
        test_dates.setdefault(title, set()).add(row["date"])
        test_daily.setdefault(title, {}).setdefault(row["date"], []).append(value)
    test_perf = {title: _personal_performance(days)
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


def muscle_detail(a):
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
    want = (a.group or "").strip().lower()
    group = next((g for g in MUSCLE_GROUP_AXES if g.lower() == want), None)
    if group is None:
        sys.exit(f"group must be one of: {', '.join(MUSCLE_GROUP_AXES)}")
    c = cx()
    _ensure_submuscle_table(c)
    days = max(int(a.days), 1)
    lo = days_ago(days)
    # per-exercise contribution to this group — the radar's ladder + weight
    # maps (_basis_weights/_group_weight_maps), so the drill-down and the
    # axis clicked share one basis. Per-exercise values are rounded to 0.1
    # for display, so their sum can differ from the axis by rounding cents.
    # ONE set-count query feeds both sections (exercises + sub_regions) —
    # they must describe the same window.
    authored, coarse = _group_weight_maps(c)
    set_rows = c.execute("""SELECT h.exercise_title ex, COUNT(*) n, MAX(h.date) last
        FROM hevy_sets h
        WHERE h.date >= ? AND COALESCE(h.set_type,'normal')!='warmup'
        GROUP BY h.exercise_title""", (lo,)).fetchall()
    exercises, pending = [], []
    for r in set_rows:
        t = r["ex"]
        gmap, basis = _basis_weights(t, authored, coarse)
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
    strength_theories = (_subregion_strength_theories(c, group, map_rows)
                         if map_rows else {
                             "status": "insufficient_evidence", "theories": [],
                             "minimum_observations": SUBREGION_MIN_OBSERVATIONS,
                             "reason": "the cited authored sub-region map is not seeded",
                         })
    out({"group": group, "window_days": days, "exercises": exercises,
         "pending_source": sorted(pending), "sub_regions": sub,
         "strength_theories": strength_theories})


# ── §3f muscle figure: engine payload for the SVG body map ───────────────────
# Display mapping from the engine's two muscle vocabularies onto the 89 region
# ids of the vendored body-muscles SVG (app/static/vendor/body-muscles/,
# sha-pinned, consumed read-only for its path data). This is an ANATOMICAL
# DISPLAY mapping — definitional, not a research claim — documented in
# docs/muscle-figure-map.md. Deep or undrawn muscles render on the overlying
# drawn region flagged approx=True (e.g. glute min → the glute-med region);
# never silently, never invented. Region ids are stored side-less here and
# expanded to -left/-right at build time (every muscle region in the SVG is
# sided; central non-muscle ids live in FIGURE_NON_MUSCLE).

# svg base id -> the 7-axis group it belongs to for scoping/drill-down.
# lower-back rows count as Core to match MUSCLE_TO_GROUP ("erectors are
# posterior core in this system").
_FIG_BASE_GROUP = {
    "chest-upper": "Chest", "chest-lower": "Chest",
    "lats-upper": "Back", "lats-mid": "Back", "lats-lower": "Back",
    "traps-upper": "Back", "traps-mid": "Back", "traps-lower": "Back",
    "lower-back-erectors": "Core", "lower-back-ql": "Core",
    "abs-upper": "Core", "abs-lower": "Core", "obliques": "Core",
    "shoulder-front": "Shoulders", "shoulder-side": "Shoulders",
    "deltoid-rear": "Shoulders", "serratus-anterior": "Shoulders",
    "biceps": "Arms", "triceps-long": "Arms", "triceps-lateral": "Arms",
    "forearm": "Arms", "forearm-flexors": "Arms", "forearm-extensors": "Arms",
    "quads": "Legs", "hamstrings-medial": "Legs", "hamstrings-lateral": "Legs",
    "adductors": "Legs", "calves-gastroc-medial": "Legs",
    "calves-gastroc-lateral": "Legs", "calves-soleus": "Legs",
    "tibialis-anterior": "Legs", "hip-flexor": "Legs",
    "gluteus-maximus": "Glutes", "gluteus-medius": "Glutes",
}
FIGURE_REGION_GROUP = {f"{b}-{s}": g for b, g in _FIG_BASE_GROUP.items()
                       for s in ("left", "right")}

# SVG ids that are not tracked muscle (bone/joint/extremity/silhouette) —
# always rendered neutral by the panel, never colored by any lens.
FIGURE_NON_MUSCLE = [
    "head", "face", "neck-left", "neck-right", "head-back", "nape", "spine",
    "elbow-left", "elbow-right", "hand-left", "hand-right",
    "hand-back-left", "hand-back-right", "knee-left", "knee-right",
    "knee-back-left", "knee-back-right", "foot-left", "foot-right",
    "foot-back-left", "foot-back-right",
]

# authored sub_region (docs/authored-submuscle-map.md, matched lowercased) ->
# (svg base ids, display_approx). display_approx=True = the muscle has no
# drawn region of its own and is shown on the overlying/nearest drawn region.
FIGURE_SUB_SVG = {
    "quads": (("quads",), False),
    "hamstrings": (("hamstrings-medial", "hamstrings-lateral"), False),
    "adductor magnus": (("adductors",), False),
    "hip flexors": (("hip-flexor",), False),   # doc: "hip flexors (iliopsoas)"
    "gastrocnemius": (("calves-gastroc-medial", "calves-gastroc-lateral"), False),
    "soleus": (("calves-soleus",), False),
    "tibialis anterior": (("tibialis-anterior",), False),
    "glute max": (("gluteus-maximus",), False),   # incl. "(upper fibres)" rows
    "glute med": (("gluteus-medius",), False),
    "glute min": (("gluteus-medius",), True),          # deep to glute med
    "tfl": (("hip-flexor",), True),                    # no TFL region drawn
    "erector spinae": (("lower-back-erectors",), False),
    "rectus abdominis": (("abs-upper", "abs-lower"), False),
    "obliques": (("obliques",), False),
    "transverse abdominis": (("abs-lower",), True),    # deep abdominal wall
    "lats": (("lats-upper", "lats-mid", "lats-lower"), False),
    "upper traps": (("traps-upper",), False),
    "mid traps": (("traps-mid",), False),
    "lower traps": (("traps-lower",), False),
    "rhomboids": (("traps-mid",), True),               # deep to mid traps
    "teres major": (("lats-upper",), True),            # no drawn region
    "front delt": (("shoulder-front",), False),
    "side delt": (("shoulder-side",), False),
    "rear delt": (("deltoid-rear",), False),
    "serratus anterior": (("serratus-anterior",), False),
    # doc: "external rotators (infraspinatus/teres minor)" — no drawn region
    "external rotators": (("deltoid-rear",), True),
    "pec clavicular": (("chest-upper",), False),
    "pec sternal": (("chest-lower",), False),
    "biceps": (("biceps",), False),
    "triceps": (("triceps-long", "triceps-lateral"), False),
    "brachialis": (("biceps",), True),                 # deep to biceps
    "brachioradialis": (("forearm",), True),           # front forearm mass
    "forearm flexors": (("forearm-flexors",), False),
    "anconeus": (("triceps-lateral",), True),          # no drawn region
}

# coarse Hevy tag (MUSCLE_TO_GROUP keys) -> svg base ids, used only for
# coarse-fallback exercises; inherently approximate (basis='coarse').
FIGURE_COARSE_SVG = {
    "chest": ("chest-upper", "chest-lower"), "pecs": ("chest-upper", "chest-lower"),
    "back": ("lats-upper", "lats-mid", "lats-lower", "traps-mid", "traps-lower"),
    "back/lats": ("lats-upper", "lats-mid", "lats-lower"),
    "lats": ("lats-upper", "lats-mid", "lats-lower"),
    "upper back": ("traps-upper", "traps-mid"),
    "upper_back": ("traps-upper", "traps-mid"),
    "upper back/posture": ("traps-upper", "traps-mid"),
    "traps": ("traps-upper", "traps-mid", "traps-lower"),
    "rhomboids": ("traps-mid",),
    "biceps": ("biceps",),
    "triceps": ("triceps-long", "triceps-lateral"),
    "forearms": ("forearm", "forearm-flexors", "forearm-extensors"),
    "grip/forearms": ("forearm", "forearm-flexors", "forearm-extensors"),
    "shoulders": ("shoulder-front", "shoulder-side", "deltoid-rear"),
    "delts": ("shoulder-front", "shoulder-side", "deltoid-rear"),
    "front delts": ("shoulder-front",), "side delts": ("shoulder-side",),
    "rear delts": ("deltoid-rear",),
    "quads": ("quads",), "quadriceps": ("quads",),
    "hamstrings": ("hamstrings-medial", "hamstrings-lateral"),
    "calves": ("calves-gastroc-medial", "calves-gastroc-lateral", "calves-soleus"),
    "adductors": ("adductors",), "tibialis": ("tibialis-anterior",),
    "core": ("abs-upper", "abs-lower", "obliques"),
    "core/abs": ("abs-upper", "abs-lower", "obliques"),
    "abs": ("abs-upper", "abs-lower"), "abdominals": ("abs-upper", "abs-lower"),
    "obliques": ("obliques",),
    "lower back": ("lower-back-erectors", "lower-back-ql"),
    "lower_back": ("lower-back-erectors", "lower-back-ql"),
    "lower back/erectors": ("lower-back-erectors", "lower-back-ql"),
    "glutes": ("gluteus-maximus", "gluteus-medius"),
    "glute max": ("gluteus-maximus",), "glute med": ("gluteus-medius",),
    "glute med/abductors": ("gluteus-medius",), "abductors": ("gluteus-medius",),
}

# Display bucketing for the activation lens (effective sets over the window →
# heat level 0–5). Buckets are half-open [lo, hi); the legend labels are
# DERIVED from the same edges so text and code cannot drift, and each label
# names the true boundary (review finding: "2–4" would misread a 4.0).
# Display convention only — the top bucket sits at the common ~10 weekly-set
# volume landmark; NOT a cited claim.
FIGURE_LEVEL_EDGES = (2, 4, 7, 10)
FIGURE_LEVEL_LEGEND = (
    [{"level": 0, "label": "0 sets"},
     {"level": 1, "label": f"<{FIGURE_LEVEL_EDGES[0]}"}]
    + [{"level": i + 2, "label": f"{lo}–{hi - 0.1:g}"}
       for i, (lo, hi) in enumerate(zip(FIGURE_LEVEL_EDGES, FIGURE_LEVEL_EDGES[1:]))]
    + [{"level": len(FIGURE_LEVEL_EDGES) + 1, "label": f"{FIGURE_LEVEL_EDGES[-1]}+"}]
)


def _fig_level(v):
    if v <= 0:
        return 0
    for i, edge in enumerate(FIGURE_LEVEL_EDGES):
        if v < edge:
            return i + 1
    return len(FIGURE_LEVEL_EDGES) + 1


# ── §3f Phase 2: strength-balance lens ───────────────────────────────────────
# RATIO_SEED key -> the figure region bases its numerator/denominator muscles
# draw on. ANATOMICAL DISPLAY mapping, definitional like FIGURE_SUB_SVG
# (docs/muscle-figure-map.md); every judgement (band, flag, gap) comes only
# from RATIO_SEED + _tested_ratios(). An empty side is allowed ONLY with an
# `unrepresentable` reason — surfaced in the payload, never guessed onto a
# wrong region (the ER:IR internal rotators are subscapularis-deep; coloring
# the chest would be anatomically wrong). `approx` marks a side whose muscle
# is itself undrawn and shown on the nearest drawn region (ER external
# rotators → the rear-delt region, matching FIGURE_SUB_SVG).
FIGURE_RATIO_MAP = {
    "hq":       {"num": ("hamstrings-medial", "hamstrings-lateral"),
                 "den": ("quads",)},
    "elbow":    {"num": ("biceps",), "den": ("triceps-long", "triceps-lateral")},
    "reardelt": {"num": ("deltoid-rear",), "den": ("shoulder-front",)},
    "erir":     {"num": ("deltoid-rear",), "den": (), "approx": ("num",),
                 "unrepresentable": "internal rotators (subscapularis) have no "
                 "drawn region — an IR result cannot be colored and is "
                 "surfaced here instead"},
    "ankle":    {"num": ("tibialis-anterior",),
                 "den": ("calves-gastroc-medial", "calves-gastroc-lateral",
                         "calves-soleus")},
    "hip":      {"num": ("hip-flexor",), "den": ("gluteus-maximus",)},
    "hip-abadd": {"num": ("gluteus-medius",), "den": ("adductors",)},
    "pushpull-isolation": {
        "num": ("chest-upper", "chest-lower"),
        "den": ("lats-upper", "lats-mid", "traps-mid")},
    "wrist": {"num": ("forearm-flexors",), "den": ("forearm-extensors",)},
    "core-strength": {"num": ("abs-upper", "abs-lower"),
                      "den": ("lower-back-erectors",)},
    "mcgill-fe":  {"num": ("abs-upper", "abs-lower"),
                   "den": ("lower-back-erectors",)},
    "mcgill-sbe": {"num": ("obliques", "lower-back-ql"),
                   "den": ("lower-back-erectors",)},
}

# The four region statuses, worst first. deficient = the weak side of an
# out-of-band cited ratio (red means exactly this, nothing else); balanced =
# tested (green — in band, or the tested-not-weak partner of a flagged pair;
# the tooltip always shows the ratio + band); trend_only = the pair has no
# cited band, amber pattern signal (design rule 2026-07-19 — NOT grey, data
# exists); untested = the honest grey default until the isolation battery
# lands. The legend is served with the payload so labels can't drift from
# the statuses the engine emits.
FIGURE_BALANCE_LEGEND = [
    {"status": "deficient", "label": "weak link — worth focus"},
    {"status": "balanced", "label": "balanced / not the weak link"},
    {"status": "trend_only",
     "label": "measured trend only — no valid target range"},
    {"status": "untested", "label": "untested"},
]
_BALANCE_RANK = {"untested": 0, "balanced": 1, "trend_only": 2, "deficient": 3}
# Between-limb gap threshold (Grygorowicz 2010) — the same 0.15 literal
# _tested_ratios pins in side_gap_flag and strength_ratios reports as
# asymmetry_threshold; change all three together or the views disagree.
RATIO_GAP_THRESHOLD = 0.15


def _muscle_map_balance(a):
    """§3f strength-balance lens: tested agonist:antagonist ratios landed onto
    SVG regions. Reuses _tested_ratios() output verbatim (no new ratio math —
    the figure can never disagree with the Strength ratios card). Combined
    mode judges each side's ratio against its cited band; the L/R sub-mode
    judges only the >15% between-limb gap and flags the weaker side. DISPLAY
    payload only — the panel paints CSS classes."""
    c = cx()
    _ensure_fitness_tables(c)
    mode = a.side_mode
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

    for row in _tested_ratios(c):
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
    out({"lens": "strength-balance", "side_mode": mode, "regions": regions,
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
                  "wins; the tooltip lists every contributing ratio.")})


def muscle_map(a):
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
    if a.lens == "strength-balance":
        return _muscle_map_balance(a)
    if a.lens == "pain":
        return _muscle_map_pain(a)
    if a.lens == "mobility":
        return _muscle_map_mobility(a)
    if a.lens != "activation":
        # argparse choices already guards; this branch is the dispatch seam
        # future lenses extend — an unimplemented token must never fall
        # through and return activation data under another lens's name
        sys.exit(f"lens {a.lens!r} is not implemented")
    c = cx()
    _ensure_submuscle_table(c)
    days = max(int(a.days), 1) if a.days else 7   # activation window default
    counts = _activation_set_counts(c, days)
    authored, coarse = _group_weight_maps(c)
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
        _gmap, basis = _basis_weights(title, authored, coarse)
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
        reg["level"] = _fig_level(reg["eff_sets"])
        reg["eff_sets"] = round(reg["eff_sets"], 1)
    out({"lens": "activation", "window_days": days, "regions": regions,
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
                  "overlying drawn region — see docs/muscle-figure-map.md")})


# =========================================================== §3g physio loop
# Pain lens + physio loop + cited catalogs (design contract 2026-07-20). The loop
# ORGANISES cited evidence and NEVER diagnoses — it mirrors the vault's
# diagnosis-review doctrine (decompose, show the evidence, produce questions,
# only a clinician diagnoses). Determinism law: all logic lives here; the panel
# paints CSS classes. Capture (pain-log / self-test-log / exercise-trial-log)
# is collector/agent-only — pinned OUT of both bridge allowlists by a test.
#
# CITED SEEDS ONLY. The catalogs below cite landmark sports-physio literature;
# self-tests are PROVOCATION/PATTERN aids (not diagnostic gold standards) and
# drills are FIRST-LINE loading protocols — never a treatment prescription.

PAIN_RED_FLAGS = {
    # Signals genuinely OUTSIDE a mechanical-pattern model → an honest
    # model-boundary line, surfaced ONCE (never a reflexive "see a doctor").
    "radiating", "numbness", "night-pain", "progressive", "trauma",
    "systemic", "cauda-equina"}
PAIN_QUALITY = {"ache", "sharp", "burning", "stiff", "throb"}
PAIN_SIDES = {"left", "right", "central"}
PAIN_PATTERN = {"load", "rest", "morning", "night", "constant", "intermittent"}
TEST_RESULTS = {"positive", "negative", "equivocal"}
TRIAL_RESPONSES = {"better", "same", "worse"}
PAIN_NRS_CLAMP = (0, 10)
PAIN_STALE_DAYS = 120   # default read window; older pain isn't "current"


# NRS bands (0–10): none / mild 1–3 / moderate 4–6 / severe 7–10 — the standard
# clinical mild/moderate/severe pain bands. Half-open [lo, hi); pinned by tests.
PAIN_BAND_EDGES = (1, 4, 7)
FIGURE_PAIN_LEGEND = [
    {"status": "none", "label": "no pain logged"},
    {"status": "mild", "label": "mild (NRS 1–3)"},
    {"status": "moderate", "label": "moderate (NRS 4–6)"},
    {"status": "severe", "label": "severe (NRS 7–10)"},
]
_PAIN_STATUSES = ("none", "mild", "moderate", "severe")
# The configured-medication vitals line stays visible on the physio surface
# note): cardiac symptoms are urgent and NOT a mechanical-pattern problem.
CV_NOTE = ("Cardiac symptoms — chest pain, fainting, a racing/irregular heart — "
           "are urgent and not part of this pattern model: stop and seek care now.")
BOUNDARY_NOTE = ("Some logged pain carries a signal outside what this "
                 "mechanical-pattern tool can reason about (radiating pain, "
                 "numbness/tingling, night pain, progressive/unremitting pain, "
                 "trauma, systemic illness, or saddle/bladder change). That's a "
                 "model boundary, not an alarm — worth raising with a clinician.")


def _pain_band(nrs):
    if nrs is None or nrs <= 0:
        return "none"
    for i, edge in enumerate(PAIN_BAND_EDGES):
        if nrs < edge:
            return _PAIN_STATUSES[i]
    return _PAIN_STATUSES[-1]


def _ensure_physio_tables(c):
    """Idempotent (§3g). Append-only + soft-void; mirrors _ensure_fitness_tables.
    The region/test/drill vocabularies live in code, never in the DB."""
    _require_schema(c, "pain_log", "reported_onset_date", "onset_precision")
    _require_schema(c, "self_test_log")
    _require_schema(c, "exercise_trial_log")


# ── §3g writers (collector/agent-only — NOT in either bridge allowlist) ───────
def pain_log(a):
    """Log ONE pain report (region, side, 0–10 NRS, optional quality/pattern/
    red-flag flags). Append-only. Region must be a known pain region (never
    guessed onto the figure); NRS clamp 0–10 (distinct from 1–5 wellbeing)."""
    region = (a.region or "").strip().lower()
    if region not in PAIN_CAUSE_MAP:
        sys.exit(f"unknown pain region {region!r} — one of {sorted(PAIN_CAUSE_MAP)}")
    if a.intensity is None:
        sys.exit("--intensity (0–10 NRS) is required")
    lo, hi = PAIN_NRS_CLAMP
    if not lo <= a.intensity <= hi:
        sys.exit(f"--intensity {a.intensity} out of range [{lo}, {hi}] (0–10 NRS)")
    side = (a.side or "central").strip().lower()
    if side not in PAIN_SIDES:
        sys.exit(f"--side must be one of {sorted(PAIN_SIDES)}")
    quality = (a.quality or "").strip().lower() or None
    if quality and quality not in PAIN_QUALITY:
        sys.exit(f"--quality must be one of {sorted(PAIN_QUALITY)}")
    pattern = (a.pattern or "").strip().lower() or None
    if pattern and pattern not in PAIN_PATTERN:
        sys.exit(f"--pattern must be one of {sorted(PAIN_PATTERN)}")
    flags = [f.strip().lower() for f in (a.flags or "").split(",") if f.strip()]
    bad = [f for f in flags if f not in PAIN_RED_FLAGS]
    if bad:
        sys.exit(f"unknown red-flag {bad} — one of {sorted(PAIN_RED_FLAGS)}")
    try:
        d = insight_events.iso_date(a.date, "--date") if a.date else today()
    except insight_events.CaptureError as exc:
        sys.exit(str(exc))
    onset_date = getattr(a, "reported_onset_date", None)
    onset_precision = getattr(a, "onset_precision", None)
    if onset_date is not None:
        try:
            onset_date = insight_events.iso_date(onset_date, "--reported-onset-date")
        except insight_events.CaptureError as exc:
            sys.exit(str(exc))
        if onset_date > d:
            sys.exit("--reported-onset-date cannot follow the observation date")
        if onset_precision not in {"exact", "approximate"}:
            sys.exit("a reported onset date requires --onset-precision exact or approximate")
    elif onset_precision not in (None, "unknown"):
        sys.exit("--onset-precision exact/approximate requires --reported-onset-date")
    capture_id = getattr(a, "capture_id", None)
    if capture_id is not None:
        try:
            insight_events.validate_capture_id(capture_id)
        except insight_events.CaptureError as exc:
            sys.exit(str(exc))
    source = a.source or "chat"
    if capture_id is not None:
        source = insight_events.event_source(source)
    elif a.source is not None and source not in CAPTURE_SOURCES | {"import"}:
        sys.exit(f"--source must be one of: {', '.join(sorted(CAPTURE_SOURCES | {'import'}))}")
    if source in {"chat-panel", "chat-telegram"} and capture_id is None:
        sys.exit(f"--source {source} requires --capture-id")
    if a.note is not None and (not a.note.strip() or len(a.note) > 2000):
        sys.exit("--note must contain 1-2000 characters")
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        if capture_id is not None:
            insight_migrations.require_version(c, 2)
        _ensure_physio_tables(c)
        first_positive = (
            int(a.intensity) > 0
            and c.execute(
                """SELECT 1 FROM pain_log
                    WHERE region=? AND side=? AND intensity>0 AND voided=0
                    LIMIT 1""",
                (region, side),
            ).fetchone() is None
        )
        c.execute("""INSERT INTO pain_log(date, region, side, intensity, quality,
            pattern, flags, note, source, reported_onset_date, onset_precision)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (d, region, side, int(a.intensity), quality, pattern,
             (",".join(flags) or None), (a.note or None), source,
             onset_date, onset_precision))
        row_id = str(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        insight_events.link_capture(
            c, capture_id, source, "pain_log", row_id, event_date=d,
        )
        entity_key_value = insight_events.pain_identity_key(region, side)
        if insight_migrations.recorded_version(c) >= 2:
            insight_events.invalidate_explicit_none(
                c, d, "pain", entity_key_value, source, capture_id,
            )
        if first_positive and insight_migrations.recorded_version(c) >= 4:
            insight_orchestrator.enqueue_internal_trigger(
                c,
                trigger_kind="pain_started",
                source_table="pain_log",
                source_row_key=row_id,
                event_date=d,
            )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    logged = {"region": region, "side": side, "intensity": int(a.intensity),
              "flags": flags, "date": d}
    if onset_date is not None or onset_precision is not None or capture_id is not None:
        logged.update({"reported_onset_date": onset_date,
                       "onset_precision": onset_precision,
                       "capture_id": capture_id})
    out({"ok": True, "logged": logged})


def self_test_log(a):
    """Log ONE self-test result (cited provocation/pattern aid, NOT diagnostic)."""
    test = (a.test or "").strip().lower()
    if test not in SELF_TEST_CATALOG:
        sys.exit(f"unknown self-test {test!r} — one of {sorted(SELF_TEST_CATALOG)}")
    result = (a.result or "").strip().lower()
    if result not in TEST_RESULTS:
        sys.exit(f"--result must be one of {sorted(TEST_RESULTS)}")
    side = (a.side or "central").strip().lower()
    if side not in PAIN_SIDES:
        sys.exit(f"--side must be one of {sorted(PAIN_SIDES)}")
    d = valid_date(a.date) if a.date else today()
    c = cx(); _ensure_physio_tables(c)
    c.execute("""INSERT INTO self_test_log(date, test, side, result, note, source)
        VALUES(?,?,?,?,?,?)""",
        (d, test, side, result, (a.note or None), (a.source or "chat")))
    c.commit()
    out({"ok": True, "logged": {"test": test, "side": side, "result": result, "date": d}})


def exercise_trial_log(a):
    """Log ONE rehab-drill trial + its 24–48h response (the physio flare rule)."""
    drill = (a.drill or "").strip().lower()
    if drill not in REHAB_CATALOG:
        sys.exit(f"unknown drill {drill!r} — one of {sorted(REHAB_CATALOG)}")
    response = (a.response or "").strip().lower()
    if response not in TRIAL_RESPONSES:
        sys.exit(f"--response must be one of {sorted(TRIAL_RESPONSES)}")
    target = (a.target or "").strip().lower() or None
    if target and target not in PAIN_CAUSE_MAP:
        sys.exit(f"--target must be a known pain region {sorted(PAIN_CAUSE_MAP)}")
    pd = a.pain_during
    if pd is not None and not PAIN_NRS_CLAMP[0] <= pd <= PAIN_NRS_CLAMP[1]:
        sys.exit(f"--pain-during {pd} out of range [0, 10]")
    d = valid_date(a.date) if a.date else today()
    c = cx(); _ensure_physio_tables(c)
    c.execute("""INSERT INTO exercise_trial_log(date, drill, target, dose,
        response, pain_during, note, source) VALUES(?,?,?,?,?,?,?,?)""",
        (d, drill, target, (a.dose or None), response,
         (int(pd) if pd is not None else None), (a.note or None), (a.source or "chat")))
    c.commit()
    out({"ok": True, "logged": {"drill": drill, "target": target,
         "response": response, "date": d}})


def physio_void(a):
    """Soft-void a mistaken physio row (deletion law — the row is NEVER deleted;
    voided=1 + reason preserves the trail, every read filters voided=0)."""
    table = {"pain": "pain_log", "self-test": "self_test_log",
             "trial": "exercise_trial_log"}.get((a.kind or "").strip().lower())
    if table is None:
        sys.exit("--kind must be one of pain, self-test, trial")
    c = cx(); _ensure_physio_tables(c)
    row = c.execute(f"SELECT voided FROM {table} WHERE id=?", (a.id,)).fetchone()
    if row is None:
        sys.exit(f"no {table} row id={a.id}")
    c.execute(f"UPDATE {table} SET voided=1, void_reason=? WHERE id=?",
              ((a.reason or "voided"), a.id))
    c.commit()
    out({"ok": True, "voided": {"kind": a.kind, "id": a.id}})


# ── §3g read: the pain lens (figure colors + the physio loop) ─────────────────
def _muscle_map_pain(a):
    """§3g pain lens: color every SVG region (incl. JOINT regions — design rule
    3) by max logged NRS in the window, plus the physio LOOP (cited candidate
    causes per painful region, honestly narrowed by logged self-tests, first-
    line cited drills, and the trial-response trend). The loop ORGANISES cited
    evidence and NEVER diagnoses. DISPLAY payload only — the panel paints."""
    c = cx(); _ensure_physio_tables(c)
    days = max(int(a.days), 1) if a.days else PAIN_STALE_DAYS
    since = days_ago(days)

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
                reg["status"] = _pain_band(agg["nrs"])
            reg["boundary"] = reg["boundary"] or bool(agg["flags"])

    for reg in regions.values():
        reg["status"] = _pain_band(reg["nrs"])   # in case max updated nrs

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
                     "status": _pain_band(agg["nrs"]), "causes": causes,
                     "boundary_flags": boundary_flags, "trials": trials,
                     "note": ("Self-tests are provocation aids for comparing "
                              "these candidate patterns.")})

    # joint ids the lens colored must leave THIS payload's non_muscle so the
    # dumb component paints them (design rule 3 — pain-lens-only exception)
    non_muscle = sorted(set(FIGURE_NON_MUSCLE) - joint_ids)
    out({"lens": "pain", "window_days": days, "regions": regions,
         "non_muscle": non_muscle, "legend": FIGURE_PAIN_LEGEND, "loop": loop,
         "boundary_note": BOUNDARY_NOTE if any_boundary else "",
         "cv_note": CV_NOTE,
         "note": ("color = max logged pain (0–10 NRS) per region in the window; "
                  "the pain lens also colors joint regions. The loop lists CITED "
                  "candidate causes + provocation self-tests + first-line drills "
                  "— it organises evidence, never diagnoses. Capture is via the "
                  "coach, not the panel.")})


# =========================================================== §3h mobility lens
# Phase 4 (design contract 2026-07-20, the LAST muscle-map phase): mobility capture
# REUSES the §3e fitness_tests path (kind rom/binary carries the unit — owner
# calls 1/2); cited norms live here like RATIO_SEED (owner: never a fabricated
# number — every cutoff cites a source; see .superpowers/sdd/
# muscle-map-phase4-citations.md). The lens colors MUSCLE regions only — unlike
# the pain lens it needs no joint exception (the vendored SVG draws no ankle
# joint, so ankle DF colors the calf tissue that limits dorsiflexion).
# Determinism law: all logic here; the panel paints CSS classes (blue/teal ramp).
MOBILITY_STALE_DAYS = 180   # mobility shifts slowly; wider than the 120d fitness
                            # window so a quarterly screen doesn't silently vanish


MOBILITY_STATUSES = ("restricted", "normal", "untested")
FIGURE_MOBILITY_LEGEND = [
    {"status": "restricted", "label": "restricted — below the cited norm"},
    {"status": "normal", "label": "within the cited norm"},
    {"status": "untested", "label": "untested / no cited cutoff"},
]
# worst wins on a shared region (mirrors the balance lens rank)
_MOBILITY_RANK = {"untested": 0, "normal": 1, "restricted": 2}
FLEX_OVERLAP_NOTE = ("Sit-and-reach also feeds the Athletic radar's Flexibility "
                     "axis (one rolled-up 0–100 score); here the SAME test is "
                     "shown as posterior-chain range detail per region — one test "
                     "at two altitudes, not double-counted.")


def _mobility_status(value, norm):
    """restricted / normal / untested from a measured value + its cited norm.
    A missing value OR a norm with no defensible cutoff (normal_at None) is an
    honest `untested` — the figure never asserts a band it cannot cite."""
    cut = norm.get("normal_at")
    if value is None or cut is None:
        return "untested"
    if norm.get("better", "higher") == "higher":
        return "normal" if value >= cut else "restricted"
    return "normal" if value <= cut else "restricted"


def _muscle_map_mobility(a):
    """§3h mobility lens: color each drawn muscle region by the status of the
    cited mobility test whose tissue maps to it (restricted / within norm /
    untested — design rule 4), plus a per-test detail list (latest value, cited
    norm, caveat, and the sit-and-reach↔radar overlap note). Reuses the §3e
    fitness_tests capture path (kind rom/binary). DISPLAY payload only."""
    c = cx(); _ensure_fitness_tables(c)
    days = max(int(a.days), 1) if a.days else MOBILITY_STALE_DAYS
    latest = _latest_tests(c, days)

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
    for mv, norm in MOBILITY_NORM.items():
        unit = norm["unit"]
        cut = norm.get("normal_at")
        norm_txt = ("norm pass" if unit == "pass-fail"
                    else f"norm ≥{cut:g} {unit}" if cut is not None
                    else "no cited cutoff")
        sides = []
        for side, v in sorted((s, v) for (m, s), v in latest.items() if m == mv):
            status = _mobility_status(v["value"], norm)
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

    out({"lens": "mobility", "window_days": days, "regions": regions,
         "non_muscle": sorted(FIGURE_NON_MUSCLE),
         "legend": FIGURE_MOBILITY_LEGEND, "tests": tests,
         "flexibility_note": FLEX_OVERLAP_NOTE,
         "note": ("color = the cited mobility-norm status per region (restricted / "
                  "within norm / untested); the detail card lists each test's "
                  "latest value, its cited norm and caveat. These are movement "
                  "SCREENS, not a diagnosis. Capture is via the coach (mobility "
                  "is a fitness test), not the panel.")})


def import_submuscle_map(a):
    """Compatibility entry point for the extracted import command."""
    out(submuscle_commands.import_map(
        _command_context(), a.md_file, seed=a.seed,
        seed_all=bool(getattr(a, "seed_all", False)), figure_sub_svg=FIGURE_SUB_SVG))


# =========================================================== §3f v-taper
# WCR (waist ÷ chest) is THE tracked V-taper metric — the peer-reviewed one
# (Garza et al. 2017, Evolutionary Psychology, WCR ≈0.7). The 1.618 "golden
# ratio" is an aesthetics-community convention, deliberately NOT the goal
# (validated behavior in fable-master-prd.md §3f). Lower WCR = more taper, so a
# reading is "at target" when it is AT OR BELOW 0.7.
VTAPER_TARGET_WCR = 0.7


def vtaper(a):
    """§3f: WCR = waist_cm / chest_cm from body_metrics, SAME-ROW pairing only
    (a waist taped this week against a chest taped last month is not a ratio —
    both tapes must come from one measurement session/row). Latest row per
    date wins; honest insufficient_data until a paired measurement exists."""
    c = cx()
    _ensure_body_source(c)
    lo = days_ago(max(int(a.days), 1))
    per_date = {}
    # Tie-break within a date: a MANUAL tape entry beats a hevy-synced one (a
    # manual row is the user's deliberate correction and must not be shadowed
    # by the nightly sync), then the higher id wins within the same source.
    for r in c.execute("""SELECT id, date, waist_cm, chest_cm, source FROM body_metrics
        WHERE waist_cm IS NOT NULL AND chest_cm IS NOT NULL AND chest_cm > 0
          AND date >= ?
        ORDER BY date, CASE WHEN source='hevy' THEN 0 ELSE 1 END, id""", (lo,)):
        per_date[r["date"]] = r      # last processed wins: manual > hevy, then id
    series = [{"date": d, "waist_cm": r["waist_cm"], "chest_cm": r["chest_cm"],
               "wcr": round(r["waist_cm"] / r["chest_cm"], 3), "source": r["source"]}
              for d, r in sorted(per_date.items())]
    base = {"target_wcr": VTAPER_TARGET_WCR, "days": int(a.days),
            "cite": "Garza et al. 2017, Evolutionary Psychology (WCR ≈0.7)",
            "note": ("waist ÷ chest, lower is more taper; the 1.618 golden ratio is "
                     "deliberately not the goal. Measure the same landmarks, relaxed, "
                     "same time of day — consistency makes the trend real.")}
    if not series:
        out({**base, "insufficient_data": True,
             "reason": "no measurement with BOTH waist_cm and chest_cm in the window "
                       "— log a tape session (or Hevy measurements once synced)"})
        return
    cur = series[-1]
    trend = round(cur["wcr"] - series[0]["wcr"], 3) if len(series) >= 2 else None
    out({**base, "current": cur, "at_target": cur["wcr"] <= VTAPER_TARGET_WCR,
         "delta_to_target": round(cur["wcr"] - VTAPER_TARGET_WCR, 3),
         "trend": trend, "n_measurements": len(series), "series": series})


# =========================================================== labs (§labs)
# Bloodwork ingestion + view. Raw photo/PDF preserved in raw/labs/ (immutable);
# LOCAL OCR/pdftotext extracts text (image never leaves the box); the extracted
# text is parsed + pre-validated against a CITED catalog before any write. All
# three writers (lab-capture / lab-ingest / import-lab-catalog) are
# collector-only — the sandboxed panel never writes labs; only `labs` (read) is
# bridge-exposed.

def _ensure_lab_tables(c):
    """Require the Migration 001 labs catalog without altering schema."""
    _require_schema(c, "lab_catalog")


def import_lab_catalog(a):
    """Compatibility entry point for the extracted import command."""
    out(lab_catalog_commands.import_catalog(
        _command_context(), a.md_file, seed=a.seed))


# ── labs: raw preservation + OCR-text ingestion + read ──────────────────────

def lab_capture(a):
    """Preserve a lab photo/PDF verbatim in raw/labs/ (the source of truth any
    auto-accepted row can be re-checked against). Bytes arrive on stdin so a
    binary image is never mangled; refuse-on-exists (raw/ is immutable — a
    re-scan must pick a new name). Collector-only, NOT bridge-exposed."""
    data = sys.stdin.buffer.read()
    if not data:
        sys.exit("empty capture — nothing on stdin")
    if len(data) > 25_000_000:
        sys.exit("capture too large (>25 MB)")
    target = _vault_write_path("raw/labs", a.name)
    if os.path.exists(target):
        sys.exit(f"raw/labs/{a.name} already exists — raw files are immutable; "
                 "save a re-scan under a new name")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "xb") as f:
        f.write(data)
    out({"ok": True, "file": f"raw/labs/{a.name}", "bytes": len(data)})


def _norm_lab_unit(u):
    return insight_calculations._norm_lab_unit(u, superscripts=_SUP)


def _lab_report_ref(s):
    """A report's own reference cell → (low, high).

    Dot or comma decimals and open-ended bounds are accepted generically; the
    report's interval wins over the catalog.
    """
    s = (s or "").strip().replace(",", ".")
    if not s:
        return (None, None)
    if s.startswith("<"):
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return (None, float(m.group()) if m else None)
    if s.startswith(">"):
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return (float(m.group()) if m else None, None)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return (None, None)


def _lab_in_range(value, low, high):
    return insight_calculations._lab_in_range(value, low, high)


def _parse_lab_report(text):
    """Flat OCR/pdftotext text → candidate rows. Tabular reports are
    column-aligned, so we split each line on runs of 2+
    spaces: [name, value, unit, reference?, flag?]. A line whose 2nd column is
    not a number is a header/blank and is skipped. Messy single-spaced OCR that
    yields too few columns is surfaced (skipped), not mis-parsed — the owner
    reviews the dry-run and the raw file is preserved."""
    rows, skipped = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]
        if len(cols) < 3 or not re.fullmatch(r"-?\d+(?:[.,]\d+)?", cols[1]):
            skipped.append(line.strip())
            continue
        name, val_s, unit = cols[0], cols[1], cols[2]
        ref_low, ref_high = _lab_report_ref(cols[3]) if len(cols) >= 4 else (None, None)
        flag = cols[4] if len(cols) >= 5 else (cols[3] if len(cols) == 4
                                               and ref_low is None and ref_high is None
                                               else None)
        rows.append({"raw_name": name, "value": float(val_s.replace(",", ".")),
                     "unit": unit, "reference_low": ref_low,
                     "reference_high": ref_high, "flag": flag})
    return rows, skipped


def _lab_catalog_index(c):
    """canonical + alias (both lower-cased) → catalog row, for name resolution."""
    idx = {}
    for r in c.execute("SELECT * FROM lab_catalog"):
        d = dict(r)
        idx[d["canonical"].lower()] = d
        for al in json.loads(d["aliases"] or "[]"):
            idx.setdefault(al.lower(), d)
    return idx


def _validate_lab_row(row, cat, last_value):
    """Structural + physically-possible-plausibility + delta. Returns the row
    enriched with canonical/panel/checks/reasons/tier. `cat` is the matched
    catalog dict or None; `last_value` the most recent prior result or None."""
    reasons, checks = [], {}
    if cat is None:
        checks["structural"] = "new_test"
        reasons.append("new_test")
        row.update({"canonical": row["raw_name"], "panel": None,
                    "src_low": row["reference_low"], "src_high": row["reference_high"],
                    "checks": checks, "reasons": reasons, "tier": "blocked",
                    "last_value": last_value})
        return row
    canonical = cat["canonical"]
    unit_ok = _norm_lab_unit(row["unit"]) == _norm_lab_unit(cat["unit"])
    checks["structural"] = "ok" if unit_ok else "unit_mismatch"
    if not unit_ok:
        reasons.append("unit_mismatch")
        checks["plausibility"] = checks["delta"] = "skipped_unit"   # bounds are unit-specific
    else:
        v = row["value"]
        if v < cat["plaus_low"] or v > cat["plaus_high"]:
            checks["plausibility"] = "implausible"
            reasons.append("implausible_value")
        else:
            checks["plausibility"] = "ok"
        if row.get("semi_quant"):
            checks["delta"] = "semi_quant"     # a bound can't be delta-compared
        elif cat["max_delta"] is None:
            checks["delta"] = "no_gate"
        elif last_value is None:
            checks["delta"] = "no_prior"
        else:
            change = abs(v - last_value)
            if cat["delta_kind"] == "frac":
                change = change / abs(last_value) if last_value else float("inf")
            if change > cat["max_delta"]:
                checks["delta"] = "large_delta"
                reasons.append("large_delta")
            else:
                checks["delta"] = "ok"
    # The source's OWN interval is what we STORE (None for the overview matrix,
    # which prints no reference). The EFFECTIVE interval used for the in/out
    # flag is the source's if present, else the catalog's — computed here for
    # display and RE-COMPUTED at read time. That's deliberate: because matrix
    # rows store no reference, correcting a catalog range re-flags every stored
    # row without a re-ingest (the report's own printed interval always wins).
    src_low, src_high = row["reference_low"], row["reference_high"]
    if src_low is None and src_high is None:
        eff_low, eff_high = cat["ref_low"], cat["ref_high"]
    else:
        eff_low, eff_high = src_low, src_high
    row["src_low"], row["src_high"] = src_low, src_high        # stored (report-only)
    row["reference_low"], row["reference_high"] = eff_low, eff_high  # effective: display + in_range
    row.update({"canonical": canonical, "panel": cat["panel"], "checks": checks,
                "reasons": reasons, "tier": "auto" if not reasons else "blocked",
                "last_value": last_value})
    return row


def lab_ingest(a):
    """Parse extracted lab text, pre-validate each row against lab_catalog, and
    (with --commit) write the AUTO rows. DRY-RUN IS THE DEFAULT. Tiers:
    known+unit-match+plausible+reasonable-delta → auto (always shown);
    new/unit-mismatch/implausible/large-delta → blocked. A blocked row is
    written only if the owner names it in --confirm (which, for a NEW test,
    also grows the catalog from the report itself). Collector-only, NOT
    bridge-exposed — the sandboxed panel never writes labs."""
    d = valid_date(a.date) if a.date else today()
    text = sys.stdin.read()
    fmt, (parsed, skipped) = "detail", _parse_lab_report(text)
    c = cx()
    _ensure_lab_tables(c)
    idx = _lab_catalog_index(c)
    confirmed = set(a.confirm or [])
    result = []
    for row in parsed:
        row["date"] = row.get("date") or d
        cat = idx.get(row["raw_name"].lower())
        last = None
        if cat is not None:
            pr = c.execute(
                "SELECT value FROM labs WHERE test_name=? AND value IS NOT NULL"
                " ORDER BY date DESC, id DESC LIMIT 1", (cat["canonical"],)).fetchone()
            last = pr["value"] if pr else None
        r = _validate_lab_row(row, cat, last)
        r["in_range"] = _lab_in_range(r["value"], r["reference_low"], r["reference_high"])
        result.append(r)
    counts = {"auto": sum(r["tier"] == "auto" for r in result),
              "blocked": sum(r["tier"] == "blocked" for r in result)}
    base = {"dry_run": not a.commit, "date": d, "format": fmt, "rows": result,
            "counts": counts, "skipped_lines": skipped, "confirm": sorted(confirmed),
            "note": LAB_CATALOG_NOTE}
    if not a.commit:
        out(base)
        return
    panel_default = a.panel or "Uncategorized"
    committed, confirmed_written, skipped_blocked = 0, [], 0
    for r in result:
        is_confirmed = r["canonical"] in confirmed or r["raw_name"] in confirmed
        if r["tier"] == "blocked" and not is_confirmed:
            skipped_blocked += 1
            continue
        # a confirmed NEW test grows the catalog from the report itself so the
        # same test flows automatically next time (validated once, per §labs)
        if "new_test" in r["reasons"]:
            confirmed_written.append(r["canonical"])
            c.execute(
                "INSERT OR REPLACE INTO lab_catalog(canonical, display, panel,"
                " unit, ref_low, ref_high, plaus_low, plaus_high, max_delta,"
                " delta_kind, aliases, source, confidence) VALUES"
                "(?,?,?,?,?,?,?,?,?, 'abs', '[]', 'user-confirmed', 'user-confirmed')",
                (r["canonical"], r["canonical"], a.panel or panel_default, r["unit"],
                 r["reference_low"], r["reference_high"], 0.0,
                 max(r["value"], r["reference_high"] or 0) * 1000 + 1000, None))
        # keep the raw name + (semi-quant) comparator in notes so an
        # auto-accepted row is always re-checkable against the source
        notes = f"ocr:{r['raw_name']}={r.get('comparator') or ''}{r['value']}{r['unit']}"
        c.execute(
            "INSERT INTO labs(date, panel, test_name, value, unit, reference_low,"
            " reference_high, flag, notes, source) VALUES(?,?,?,?,?,?,?,?,?, 'labs-ocr')",
            (r["date"], r["panel"] or a.panel or panel_default, r["canonical"], r["value"],
             r["unit"], r.get("src_low"), r.get("src_high"), r["flag"], notes))
        committed += 1
    c.commit()
    out({**base, "committed": committed, "skipped_blocked": skipped_blocked,
         "confirmed": confirmed_written})


# A marker's CURRENT status is its single most-recent reading (never an average
# or blend of older ones). A latest reading older than this is 'historical' —
# shown in the view but not read as current, and current suggestions ignore it
# (the recommendation path stays pull-only). configuration rule 2026-07-12.
LABS_STALE_DAYS = 365


def _lab_age_days(date_str):
    try:
        return (_now().date() - date.fromisoformat(date_str)).days
    except (TypeError, ValueError):
        return None


def labs(a):
    """Read-only labs view feed. Default: latest result per test, grouped by
    panel (newest report first is a per-test 'latest wins' — CURRENT status is
    the most recent reading only, never blended with older ones). --test
    <canonical>: that test's series over time for a trend (insufficient_data
    until 2 points). Range flags come from the stored (report) interval, catalog
    as fallback. A latest reading older than LABS_STALE_DAYS is flagged
    `historical`. This is the ONLY bridge-exposed labs command."""
    c = cx()
    _ensure_lab_tables(c)
    cat = {r["canonical"]: dict(r) for r in c.execute("SELECT * FROM lab_catalog")}
    days = int(a.days) if a.days else None
    lo = days_ago(days) if days else None

    def _row_range(r):
        low, high = r["reference_low"], r["reference_high"]
        if low is None and high is None and r["test_name"] in cat:
            low, high = cat[r["test_name"]]["ref_low"], cat[r["test_name"]]["ref_high"]
        return low, high

    if a.test:
        q = ("SELECT * FROM labs WHERE test_name=?"
             + (" AND date>=?" if lo else "") + " ORDER BY date, id")
        params = (a.test, lo) if lo else (a.test,)
        # collapse exact re-ingest duplicates: for one (date, value) the newest
        # row (max id, seen last) wins — a corrected re-ingest supersedes the
        # older row in the view while the deletion law keeps it in the table.
        by_dv = {}
        for r in c.execute(q, params):
            low, high = _row_range(r)
            by_dv[(r["date"], r["value"])] = {
                "date": r["date"], "value": r["value"], "flag": r["flag"],
                "in_range": _lab_in_range(r["value"], low, high)}
        series = sorted(by_dv.values(), key=lambda s: s["date"])
        meta = cat.get(a.test, {})
        latest_date = series[-1]["date"] if series else None
        age = _lab_age_days(latest_date) if latest_date else None
        base = {"canonical": a.test, "display": meta.get("display"),
                "unit": meta.get("unit"), "ref_low": meta.get("ref_low"),
                "ref_high": meta.get("ref_high"), "n": len(series),
                "latest_date": latest_date, "latest_age_days": age,
                "historical": age is not None and age > LABS_STALE_DAYS,
                "stale_after_days": LABS_STALE_DAYS}
        if len(series) < 2:
            out({**base, "insufficient_data": True, "series": series})
            return
        out({**base, "series": series})
        return

    # latest row per test (max date, then id) — the table's headline values
    q = ("SELECT * FROM labs" + (" WHERE date>=?" if lo else "")
         + " ORDER BY test_name, date DESC, id DESC")
    latest = {}
    for r in c.execute(q, (lo,) if lo else ()):
        if r["test_name"] not in latest:
            latest[r["test_name"]] = dict(r)
    if not latest:
        out({"insufficient_data": True,
             "reason": "no lab results ingested yet"})
        return
    panels = {}
    for name, r in latest.items():
        low, high = _row_range(r)
        meta = cat.get(name, {})
        panel = r["panel"] or meta.get("panel") or "Uncategorized"
        age = _lab_age_days(r["date"])
        panels.setdefault(panel, []).append({
            "canonical": name, "display": meta.get("display") or name,
            "value": r["value"], "unit": r["unit"], "date": r["date"],
            "reference_low": low, "reference_high": high, "flag": r["flag"],
            "in_range": _lab_in_range(r["value"], low, high),
            "age_days": age,
            "historical": age is not None and age > LABS_STALE_DAYS})
    if a.panel:
        panels = {k: v for k, v in panels.items() if k == a.panel}
    # out-of-range tests sort to the top of each panel
    out_panels = [{"panel": p, "tests": sorted(
        ts, key=lambda t: (t["in_range"] is not False, t["display"]))}
        for p, ts in sorted(panels.items())]
    out({"panels": out_panels, "n_tests": len(latest),
         "stale_after_days": LABS_STALE_DAYS})


# =========================================================== owner profile / phase
OWNER_PROFILE_KEYS = {"height_cm", "sex", "dob", "activity_fallback"}
SEX_VALUES = {"male", "female"}
ACTIVITY_FALLBACK_VALUES = {"sedentary", "light", "moderate", "active", "very_active"}
DIET_PHASES = {"cut", "maintain", "bulk"}


def _configured_phase_numbers(name):
    raw = os.environ.get(name, "").strip()
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


CONFIGURED_PHASE_OFFSETS = _configured_phase_numbers("HERMES_PHASE_OFFSETS_JSON")
CONFIGURED_PROTEIN_PER_KG = _configured_phase_numbers("HERMES_PROTEIN_PER_KG_JSON")
if any(value <= 0 for value in CONFIGURED_PROTEIN_PER_KG.values()):
    raise RuntimeError("HERMES_PROTEIN_PER_KG_JSON values must be greater than zero")


def _ensure_profile_table(c):
    """Idempotent: user profile config (height/sex/dob/activity fallback) +
    diet phase, keyed rows. Feeds the T44 targets engine (Mifflin-St Jeor +
    phase-relative kcal) — config only, no health data."""
    _require_schema(c, "owner_profile")


def _profile_upsert(c, key, value):
    c.execute("""INSERT INTO owner_profile(key, value, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, today()))


def profile_set(a):
    """User profile config for the T44 targets engine. Config — NOT in the
    bridge allowlists (agent/SSH path only)."""
    key = (a.key or "").strip().lower()
    if key not in OWNER_PROFILE_KEYS:
        sys.exit(f"key must be one of: {', '.join(sorted(OWNER_PROFILE_KEYS))}")
    value = a.value
    if key == "height_cm":
        try:
            v = float(value)
        except (TypeError, ValueError):
            sys.exit(f"height_cm must be a number, got: {value!r}")
        if not (100 <= v <= 250):
            sys.exit(f"height_cm must be between 100 and 250, got: {value!r}")
    elif key == "sex":
        if value not in SEX_VALUES:
            sys.exit(f"sex must be one of: {', '.join(sorted(SEX_VALUES))}")
    elif key == "dob":
        try:
            d = date.fromisoformat(value)
        except (TypeError, ValueError):
            sys.exit(f"dob must be ISO YYYY-MM-DD, got: {value!r}")
        if not (date(1900, 1, 1) <= d <= date.fromisoformat(today())):
            sys.exit(f"dob must be between 1900-01-01 and today, got: {value!r}")
    elif key == "activity_fallback":
        if value not in ACTIVITY_FALLBACK_VALUES:
            sys.exit(f"activity_fallback must be one of: {', '.join(sorted(ACTIVITY_FALLBACK_VALUES))}")
    c = cx(); _ensure_profile_table(c)
    _profile_upsert(c, key, value)
    c.commit()
    out({"ok": True, "key": key, "value": value})


def phase_set(a):
    """Diet phase (cut/maintain/bulk) for the T44 targets engine's phase-
    relative kcal adjustment. A phase change always restarts phase_started."""
    phase = (a.phase or "").strip().lower()
    if phase not in DIET_PHASES:
        sys.exit(f"phase must be one of: {', '.join(sorted(DIET_PHASES))}")
    c = cx(); _ensure_profile_table(c)
    started = today()
    _profile_upsert(c, "phase", phase)
    _profile_upsert(c, "phase_started", started)
    c.commit()
    out({"ok": True, "phase": phase, "phase_started": started})


# =========================================================== T44 targets engine
# Every physiological constant below carries a source string (MICRO_SEED /
# lab_catalog precedent). This is nutrition PLANNING, not medical advice — no
# demographic targets are assumed here. Phase and protein values are explicit
# installation configuration; the water heuristic is labeled as such.
TARGETS_SEED = {
    "bmr": {
        "formula": "male: 10*kg + 6.25*cm - 5*age + 5; female: same - 161 instead of +5",
        "source": "Mifflin-St Jeor 1990 (Am J Clin Nutr 51:241-247) resting energy equation"},
    "activity_factors": {
        "values": {"sedentary": 1.2, "light": 1.375, "moderate": 1.55,
                   "active": 1.725, "very_active": 1.9},
        "source": "standard TDEE activity multipliers (Harris-Benedict/Mifflin convention)"},
    "phase_offset_kcal": {
        "values": CONFIGURED_PHASE_OFFSETS,
        "source": "installation-configured phase offset"},
    "protein_g_per_kg": {
        "values": CONFIGURED_PROTEIN_PER_KG,
        "source": "installation-configured protein target"},
    "fat_pct_kcal": {
        "value": 0.25,
        "source": "25% of kcal — within the 20-35% acceptable range (position-stand "
                  "convention); kept above ~20% as the hormonal-health floor"},
    "kcal_band_pct": {
        "value": 0.10,
        "source": "+/-10% band — matches the existing nutrition-score kcal credit band"},
    "water": {
        "ml_per_kg": WATER_ML_PER_KG,
        "ml_per_exercise_hour": WATER_ML_PER_EXERCISE_HOUR,
        "hot_day_bonus_ml": WATER_HOT_DAY_BONUS_ML,
        "hot_day_temp_c": WATER_HOT_DAY_TEMP_C,
        "source": "installation-configured water heuristic"},
}


def _daymax(c, col, lo, hi=None):
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


def _water_target(c, weight_kg):
    """Configured baseline/exercise/weather heuristic, rounded to 50 ml.
    Uses today's exercise_min / weather max temp, else yesterday's — whichever
    day first has any data; day_used records the choice. Without a weight the
    existing WATER_TARGET_ML constant is the labeled fallback."""
    seed = TARGETS_SEED["water"]
    if weight_kg is None:
        return {"target": WATER_TARGET_ML, "basis": "fallback",
                "components": {"baseline": None, "exercise": None, "weather": None},
                "day_used": None}
    day_used, ex_min, temp_max = today(), 0.0, None
    for back in (0, 1):
        d = days_ago(back)
        ex = _daymax(c, "exercise_min", d, d).get(d)   # per-day MAX across sources
        w = (c.execute("SELECT temp_max_c FROM weather WHERE date=?", (d,)).fetchone()
             if _table_exists(c, "weather") else None)
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


def _micro_targets():
    return insight_calculations._micro_targets(
        micro_seed=MICRO_SEED, micro_target_extra=MICRO_TARGET_EXTRA,
    )


def _compute_targets(c):
    """READ-only compute (T44/T46): owner profile + logged data -> today's
    nutrition targets (pinned JSON contract — T45/T46/T47 build against it).
    Shared by the `nutrition-targets` CLI subcommand, scores()'s water and
    nutrition components, and `nutrition-coverage` — ONE targets source for
    the whole system, no rival computations. No writes: Migration 001 owns
    owner_profile and nutrition_targets; empty migrated tables simply provide
    no configuration rows. Missing inputs -> insufficient_data, never a guess (the
    labeled water fallback is the one allowed exception)."""
    prof = ({r["key"]: r["value"] for r in c.execute("SELECT key, value FROM owner_profile")}
            if _table_exists(c, "owner_profile") else {})
    wrow = c.execute("""SELECT date, weight_kg FROM body_metrics
                        WHERE weight_kg IS NOT NULL AND date<=?
                        ORDER BY date DESC, id DESC LIMIT 1""", (today(),)).fetchone()
    weight = wrow["weight_kg"] if wrow else None
    missing = [k for k in ("height_cm", "sex", "dob") if not prof.get(k)]
    if missing or weight is None:
        reason = (f"user profile incomplete — profile-set {'/'.join(missing)}"
                  if missing else
                  "no weight_kg in body_metrics — log a weight first")
        return {"status": "insufficient_data", "reason": reason,
                "targets": {"water_ml": _water_target(c, weight)}}
    missing_micros = sorted(MICRO_KEYS - set(CONFIGURED_MICRO_TARGETS))
    if missing_micros:
        return {
            "status": "insufficient_data",
            "reason": "nutrient targets are not configured",
            "missing_nutrient_targets": missing_micros,
            "targets": {"water_ml": _water_target(c, weight)},
        }
    if (set(CONFIGURED_PHASE_OFFSETS) != DIET_PHASES
            or set(CONFIGURED_PROTEIN_PER_KG) != DIET_PHASES):
        return {
            "status": "insufficient_data",
            "reason": "nutrition phase and protein targets are not configured",
            "targets": {"water_ml": _water_target(c, weight)},
        }

    # profile (owner_profile stores raw TEXT — float() on read)
    height = float(prof["height_cm"])
    sex = prof["sex"]
    dob = date.fromisoformat(prof["dob"])
    t = date.fromisoformat(today())
    age = t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))

    # activity over the trailing 28 days, derived deterministically
    lo = days_ago(28)
    hevy_dates = {r["date"] for r in c.execute(
        "SELECT DISTINCT date FROM hevy_sets WHERE date>=? AND date<=?", (lo, today()))}
    steps = _daymax(c, "steps", lo, today())  # per-day MAX across sources
    ex_days = _daymax(c, "exercise_min", lo, today())  # (provenance rule, see _daymax)
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
    factor = TARGETS_SEED["activity_factors"]["values"][level]

    # Mifflin-St Jeor BMR -> maintenance -> phase-adjusted kcal
    bmr = 10 * weight + 6.25 * height - 5 * age + (5 if sex == "male" else -161)
    maintenance = round(bmr * factor)
    phase = prof.get("phase")
    phase_out = {"phase": phase or "maintain",
                 "started": prof.get("phase_started"), "set": phase is not None}
    offset = TARGETS_SEED["phase_offset_kcal"]["values"][phase_out["phase"]]

    # legacy nutrition_targets rows (user's explicit manual setting) OVERRIDE
    # the computed kcal/protein; fat/carbs/band derive from the EFFECTIVE values
    overrides = ({r["name"]: r["target"] for r in
                 c.execute("SELECT name, target FROM nutrition_targets")}
                 if _table_exists(c, "nutrition_targets") else {})
    kcal = maintenance + offset
    kcal_t = {"target": kcal,
              "source": (f"{TARGETS_SEED['bmr']['source']} x activity factor "
                         f"({TARGETS_SEED['activity_factors']['source']}); "
                         f"phase offset: {TARGETS_SEED['phase_offset_kcal']['source']}; "
                         f"band: {TARGETS_SEED['kcal_band_pct']['source']}")}
    if overrides.get("kcal") is not None:
        kcal = int(round(overrides["kcal"]))
        kcal_t.update(target=kcal, override=True,
                      source="owner override (nutrition_targets table)")
    band = TARGETS_SEED["kcal_band_pct"]["value"]
    kcal_t["band_low"] = round(kcal * (1 - band))
    kcal_t["band_high"] = round(kcal * (1 + band))

    per_kg = TARGETS_SEED["protein_g_per_kg"]["values"][phase_out["phase"]]
    protein = round(per_kg * weight)
    protein_t = {"target": protein, "per_kg": per_kg,
                 "source": TARGETS_SEED["protein_g_per_kg"]["source"]}
    if overrides.get("protein_g") is not None:
        protein = int(round(overrides["protein_g"]))
        protein_t.update(target=protein, per_kg=round(protein / weight, 2),
                         override=True,
                         source="owner override (nutrition_targets table)")

    fat = round(TARGETS_SEED["fat_pct_kcal"]["value"] * kcal / 9)
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
                       "source": TARGETS_SEED["fat_pct_kcal"]["source"]},
             "carbs_g": {"target": carbs,
                         "source": "remainder: (kcal - protein*4 - fat*9) / 4 "
                                   "(Atwater 4/9/4 kcal per g)"},
             "water_ml": _water_target(c, weight),
             "micros": _micro_targets(),
         }}


def nutrition_targets(a):
    """CLI wrapper (T44/T46): pure read/compute, zero flags. Delegates to
    _compute_targets (shared by scores()/nutrition-coverage)."""
    out(_compute_targets(cx()))


def nutrition_coverage(a):
    """READ-only per-day target-coverage rows (T46): the user's
    redefinition of 'food quality' as % of macro+micro targets hit that
    day. Uses the SAME scorer (_nutrition_day_score) and targets source
    (_compute_targets) as scores()'s nutrition component — one path, not a
    rival formula. Targets are computed ONCE from the CURRENT profile/
    weight/phase (compute-on-read, same as nutrition-targets); a historical
    day's row is scored against TODAY's targets, not a period-accurate
    re-derivation of that day's own weight/phase — an honest limitation,
    documented rather than silently assumed (out of scope for T46). Water
    is NOT included (the owner defined coverage as macro+micro targets
    only, not hydration). Days with neither logged nutrition nor Cronometer
    data are OMITTED, never zero-scored; an empty window is its own
    insufficient_data."""
    c = cx()
    tg = _compute_targets(c)
    if tg["status"] != "ok":
        out({"days": a.days, "rows": [], "status": "insufficient_data",
             "reason": tg["reason"]})
        return
    rows = []
    for back in range(a.days):
        d = days_ago(back)
        res = _nutrition_day_score(c, d, tg["targets"])
        if res is None:
            continue
        comp = res["components"]
        rows.append({"date": d, "score": res["score"], "band": res["band"],
                     "components": {"protein_pct": comp["protein_pct"],
                                    "kcal_pct": comp["kcal_pct"],
                                    "micros_hit": comp["micros_hit"],
                                    "micros_total": comp["micros_total"]}})
    if not rows:
        out({"days": a.days, "rows": [], "status": "insufficient_data",
             "reason": "no logged nutrition or Cronometer data in this window"})
        return
    rows.sort(key=lambda r: r["date"])
    out({"days": a.days, "rows": rows, "status": "ok"})


# =========================================================== Phase 2 capture
def _write_insight(fn, *args, **kwargs):
    """Run one Phase 2 write in a single explicit transaction."""
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 2)
        result = fn(c, *args, **kwargs)
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    out(result)


def _stdin_json():
    return insight_events.parse_json_stdin(sys.stdin.read())


def schema_status_cmd(a):
    out(insight_migrations.schema_status(DB))


def schema_plan_cmd(a):
    out(insight_migrations.schema_plan(DB, a.to))


def migrate_cmd(a):
    out(insight_migrations.migrate(DB, a.to, a.expected_from))


def capture_raw_cmd(a):
    payload = _stdin_json()
    insight_events.validate_capture_raw(payload)
    _write_insight(insight_events.capture_raw, payload)


def capture_resolve_cmd(a):
    payload = _stdin_json()
    insight_events.validate_capture_resolve(payload)
    _write_insight(insight_events.capture_resolve, payload)


def event_log_cmd(a):
    payload = _stdin_json()
    insight_events.validate_event(payload)
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 2)
        result = insight_events.event_log(c, payload)
        if (
            payload["category"] == "medication_change"
            and insight_migrations.recorded_version(c) >= 4
        ):
            insight_orchestrator.enqueue_internal_trigger(
                c,
                trigger_kind="medication_regime_change",
                source_table="event_exposures",
                source_row_key=str(result["event_id"]),
                event_date=result["date"],
            )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    out(result)


def event_correct_cmd(a):
    insight_events.bounded_int(a.id, "id", 1, 2_147_483_647, nullable=False)
    payload = _stdin_json()
    insight_events.validate_event(payload, correction=True)
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 2)
        result = insight_events.event_correct(c, a.id, payload)
        if (
            payload["category"] == "medication_change"
            and insight_migrations.recorded_version(c) >= 4
        ):
            insight_orchestrator.enqueue_internal_trigger(
                c,
                trigger_kind="medication_regime_change",
                source_table="event_exposures",
                source_row_key=str(result["replacement_event_id"]),
                event_date=payload["date"],
            )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    out(result)


def event_void_cmd(a):
    insight_events.bounded_int(a.id, "id", 1, 2_147_483_647, nullable=False)
    insight_events.bounded_text(a.reason, "reason", 500, nullable=False)
    _write_insight(insight_events.event_void, a.id, a.reason)


def events_cmd(a):
    status = insight_migrations.schema_status(DB)
    if status["current_version"] < 2:
        raise insight_migrations.SchemaError(
            "schema_migration_required", "schema version 2 is required")
    c = cx_ro()
    try:
        result = insight_events.list_events(
            c, from_date=a.from_date, to_date=a.to_date, days=a.days,
            all_dates=a.all_dates, category=a.category,
            entity_key_value=a.entity_key,
        )
    finally:
        c.close()
    out(result)


def capture_completeness_set_cmd(a):
    insight_events.validate_completeness_input(
        event_date=a.date, scope=a.scope, state=a.state,
        explicit_none=a.explicit_none, entity_key_value=a.entity_key,
        source=a.source, capture_id=a.capture_id, note=a.note,
    )
    _write_insight(
        insight_events.completeness_set, event_date=a.date, scope=a.scope,
        state=a.state, explicit_none=a.explicit_none,
        entity_key_value=a.entity_key, source=a.source,
        capture_id=a.capture_id, note=a.note,
    )


def capture_completeness_cmd(a):
    status = insight_migrations.schema_status(DB)
    if status["current_version"] < 2:
        raise insight_migrations.SchemaError(
            "schema_migration_required", "schema version 2 is required")
    c = cx_ro()
    try:
        result = insight_events.completeness_read(
            c, from_date=a.from_date, to_date=a.to_date, days=a.days,
            all_dates=a.all_dates, scope=a.scope,
        )
    finally:
        c.close()
    out(result)


def entity_alias_set_cmd(a):
    insight_events.validate_alias_set(a.type, a.alias, a.canonical, a.label)
    _write_insight(insight_events.alias_set, a.type, a.alias, a.canonical, a.label)


def entity_alias_retire_cmd(a):
    insight_events.validate_alias_retire(a.type, a.alias)
    _write_insight(insight_events.alias_retire, a.type, a.alias)


def entity_alias_history_cmd(a):
    status = insight_migrations.schema_status(DB)
    if status["current_version"] < 2:
        raise insight_migrations.SchemaError(
            "schema_migration_required", "schema version 2 is required")
    c = cx_ro()
    try:
        result = insight_events.alias_history(c, a.type, a.alias)
    finally:
        c.close()
    out(result)


def supplement_log(a):
    """Append one explicitly sourced supplement observation with nullable time."""
    supplement = insight_events.bounded_text(
        a.supplement, "supplement", 300, nullable=False, preserve=False)
    try:
        d = insight_events.iso_date(a.date) if a.date else today()
    except insight_events.CaptureError:
        raise
    if a.time is not None:
        insight_events.hhmm(a.time, "--time", nullable=False)
    if a.taken is not None and a.taken not in (0, 1):
        raise insight_events.CaptureError(
            "validation_error", "--taken must be 0 or 1", validation=True)
    if a.dose is not None and not (math.isfinite(a.dose) and 0 <= a.dose <= 1_000_000):
        raise insight_events.CaptureError(
            "validation_error", "--dose must be a finite number between 0 and 1000000",
            validation=True)
    if a.taken == 0 and a.dose not in (None, 0):
        raise insight_events.CaptureError(
            "validation_error", "--taken 0 cannot include a positive dose", validation=True)
    if a.note is not None and (not a.note.strip() or len(a.note) > 2000):
        raise insight_events.CaptureError(
            "validation_error", "--note must contain 1-2000 characters", validation=True)
    source = insight_events.event_source(a.source)
    insight_events.validate_capture_id(a.capture_id, nullable=True)
    if source in {"chat-panel", "chat-telegram"} and a.capture_id is None:
        raise insight_events.CaptureError(
            "validation_error", f"--source {source} requires --capture-id", validation=True)
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 2)
        _require_schema(c, "supplements_log", "time_taken")
        rows = []
        if supplement.isdigit():
            rows = c.execute("SELECT * FROM supplement_products WHERE supplement_id=?",
                             (int(supplement),)).fetchall()
        if not rows:
            rows = c.execute("SELECT * FROM supplement_products WHERE name=?",
                             (supplement,)).fetchall()
        if not rows:
            raise insight_events.CaptureError(
                "not_found", f"supplement not found: {supplement}")
        if len(rows) != 1:
            raise insight_events.CaptureError(
                "validation_error", "supplement name is ambiguous; use its numeric ID",
                validation=True)
        product = rows[0]
        c.execute("""INSERT INTO supplements_log(
            date,supplement_id,taken,dose_taken,time_taken,notes,source)
            VALUES(?,?,?,?,?,?,?)""",
            (d, product["supplement_id"], a.taken, a.dose, a.time, a.note, source))
        row_id = str(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        insight_events.link_capture(
            c, a.capture_id, source, "supplements_log", row_id,
            event_date=d, event_time=a.time, check_time=True,
        )
        entity_key_value, _ = insight_events.resolve_identity(
            c, "supplement", product["name"])
        insight_events.invalidate_explicit_none(
            c, d, "supplement", entity_key_value, source, a.capture_id,
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    out({"ok": True, "id": int(row_id), "date": d, "supplement_id": product["supplement_id"],
         "supplement": product["name"], "taken": a.taken, "dose": a.dose,
         "time": a.time, "source": source, "capture_id": a.capture_id})


# =========================================================== Phase 3 feature integration
def _phase3_schema_ready():
    status = insight_migrations.schema_status(DB)
    if status["current_version"] < 3:
        raise insight_migrations.SchemaError(
            "schema_migration_required", "schema version 3 is required")
    return status


def _phase4_schema_ready():
    return insight_runtime.require_analytical_schema(DB)


def _phase3_context():
    # Resolve the compatibility bindings now, including replaced catalogs and
    # the wrappers that keep nested date-sensitive calculations on this clock.
    return insight_runtime.adapter_context(clock=_now, bindings=globals())


def _phase3_range(a):
    lo, hi, kind = insight_events.resolve_range(
        from_date=a.from_date, to_date=a.to_date, days=a.days,
        all_dates=a.all_dates,
    )
    return DateRange(
        start=date.fromisoformat(lo) if lo else None,
        end=date.fromisoformat(hi) if hi else None,
        kind="all" if kind == "all" else "bounded",
    )


def _phase3_definitions(c, context, family=None):
    built = insight_registry.build_registry(c, context, family=family)
    if isinstance(built, dict):
        for key in ("features", "entries", "registry", "definitions"):
            if key in built:
                return list(built[key])
        return list(built.values())
    if hasattr(built, "definitions"):
        return list(built.definitions)
    return list(built)


def feature_registry_cmd(a):
    _phase3_schema_ready()
    context = _phase3_context()
    c = cx_ro()
    try:
        definitions = _phase3_definitions(c, context, family=a.family)
        result = insight_registry.serialize_registry(definitions)
    finally:
        c.close()
    out(result)


def feature_frame_cmd(a):
    requested = _phase3_range(a)  # validate before opening the database
    _phase3_schema_ready()
    context = _phase3_context()
    c = cx_ro()
    try:
        definitions = _phase3_definitions(c, context, family=a.family)
        result = insight_frame.build_feature_frame(
            c, definitions, requested, context,
            include_provenance=a.include_provenance,
        )
    finally:
        c.close()
    out(result)


def data_readiness_cmd(a):
    requested = _phase3_range(a)  # validate before opening the database
    _phase3_schema_ready()
    context = _phase3_context()
    c = cx_ro()
    try:
        definitions = _phase3_definitions(c, context)
        result = insight_readiness.build_readiness(
            c, definitions, requested, context, goal=a.goal, outcome=a.outcome,
        )
    finally:
        c.close()
    out(result)


def outcome_associations_cmd(a):
    from hermes_insights import exact_cache
    # Scalar/range validation deliberately precedes every database check.
    insight_associations.validate_options(
        outcome_key=a.outcome, mode=a.mode, min_n=a.min_n,
        interactions=a.interactions, top=a.top,
    )
    requested = _phase3_range(a)
    _phase4_schema_ready()
    context = _phase3_context()
    c = cx_ro()
    try:
        c.execute("BEGIN")
        definitions = _phase3_definitions(c, context)
        result = exact_cache.cached_compute(
            DB, {"operation": "outcome-associations", "range": requested.to_dict(),
                 "outcome": a.outcome, "mode": a.mode, "min_n": a.min_n,
                 "interactions": a.interactions, "top": a.top},
            lambda: insight_associations.analyze_outcome(
                c, definitions, requested, context,
                outcome_key=a.outcome, mode=a.mode, min_n=a.min_n,
                interactions=a.interactions, top=a.top,
            ), connection=c, context=context, definitions=definitions,
        )
        result = exact_cache.refresh_readiness(
            DB, result, connection=c, context=context, definitions=definitions,
        )
    except exact_cache.AnalysisBusy as exc:
        raise insight_associations.AssociationError("analysis_busy", str(exc)) from exc
    finally:
        c.close()
    print(canonical_json(result))


def finding_evidence_cmd(a):
    from hermes_insights import exact_cache
    insight_associations.validate_options(
        outcome_key=a.outcome, mode="all",
        min_n=insight_associations.DEFAULT_MIN_N,
        interactions="pairwise", top=100,
    )
    insight_associations.validate_sha256_id(a.finding_id, "finding-id")
    insight_associations.validate_sha256_id(
        a.input_fingerprint, "input-fingerprint")
    requested = _phase3_range(a)
    _phase4_schema_ready()
    context = _phase3_context()
    c = cx_ro()
    try:
        c.execute("BEGIN")
        definitions = _phase3_definitions(c, context)
        result = exact_cache.cached_compute(
            DB, {"operation": "finding-evidence", "range": requested.to_dict(),
                 "outcome": a.outcome, "finding_id": a.finding_id,
                 "input_fingerprint": a.input_fingerprint},
            lambda: insight_associations.recompute_finding_evidence(
                c, definitions, requested, context,
                outcome_key=a.outcome, finding_id_value=a.finding_id,
                input_fingerprint_value=a.input_fingerprint,
            ), connection=c, context=context, definitions=definitions,
        )
    except exact_cache.AnalysisBusy as exc:
        raise insight_associations.AssociationError("analysis_busy", str(exc)) from exc
    finally:
        c.close()
    print(canonical_json(result))


def goal_list_cmd(a):
    _phase3_schema_ready()
    c = cx_ro()
    try:
        result = insight_goals.list_goals(c, include_disabled=a.all_goals)
    finally:
        c.close()
    out(result)


def goal_set_cmd(a):
    # Build/validate the concrete registered outcome before opening a write
    # transaction; an invalid pair cannot acquire a write lock.
    _phase3_schema_ready()
    context = _phase3_context()
    c = cx()
    try:
        definitions = _phase3_definitions(c, context)
        registered = {
            getattr(item, "key", item.get("key") if isinstance(item, dict) else None): item
            for item in definitions
        }
        registered.pop(None, None)
        insight_goals.prepare_goal_revision(
            c, goal_key=a.goal, enabled=a.enabled, priority=a.priority,
            outcome_key=a.outcome, direction=a.direction, note=a.note,
            source=a.source, registered_keys=registered,
        )
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 3)
        result = insight_goals.set_goal(
            c, goal_key=a.goal, enabled=a.enabled, priority=a.priority,
            outcome_key=a.outcome, direction=a.direction, note=a.note,
            source=a.source, registered_keys=registered,
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    out(result)


def collector_run_record_cmd(a):
    payload = _stdin_json()
    insight_goals.validate_collector_run(payload)
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 3)
        result = insight_goals.record_collector_run(c, payload)
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    out(result)


# =========================================================== Phase 5 ledger/synthesis
_PHASE5_BASE_OUTCOMES = (
    "subjective.day_rating",
    "subjective.energy",
    "subjective.focus",
    "subjective.mood",
    "adherence.word_kept",
)
_PHASE5_ANNOTATION_KEYS = {
    "annotation_id", "hypothesis_id", "evaluation_id", "annotation_kind",
    "content", "source", "synthesis_id", "context_version",
    "prompt_sha256", "model_id", "provider", "supersedes_id",
    "input_sha256",
}


def _phase5_schema_ready():
    """Phase 5 writers and structured reads require exact Migration 004."""
    status = insight_migrations.schema_status(DB)
    if status["current_version"] != insight_migrations.AUTONOMOUS_SCHEMA_VERSION:
        raise insight_migrations.SchemaError(
            "schema_migration_required",
            "hypothesis-ledger-v1 requires the exact current schema version",
        )
    return status


def _phase5_range_record(requested):
    if requested.kind == "all":
        return {
            "range_role": "primary",
            "requested_range_kind": "all",
            "requested_from": None,
            "requested_to": None,
        }
    return {
        "range_role": "primary",
        "requested_range_kind": "bounded",
        "requested_from": requested.start.isoformat(),
        "requested_to": requested.end.isoformat(),
    }


def _phase5_anchor(requested, explicit=None):
    if explicit is not None:
        return insight_events.iso_date(explicit, "--anchor")
    if requested.end is not None:
        return requested.end.isoformat()
    return today()


def _phase5_modes(outcome_key):
    return (
        ("ordinal", "green-vs-non-green", "red-vs-non-red")
        if outcome_key == "subjective.day_rating"
        else ("ordinal",)
    )


def _phase5_selected_outcomes(c, explicit):
    if explicit:
        if len(explicit) > 64:
            raise insight_ledger.LedgerError(
                "validation_error",
                "--outcome may be repeated at most 64 times",
                validation=True,
            )
        selected = set(explicit)
        selection = "explicit_set"
    else:
        selected = set(_PHASE5_BASE_OUTCOMES)
        goals = insight_goals.list_goals(c, include_disabled=False)["goals"]
        selected.update(
            goal["outcome_key"]
            for goal in goals
            if goal["enabled"] and goal["outcome_key"] is not None
        )
        selection = "base_and_enabled"
    for outcome_key in selected:
        insight_associations.validate_options(
            outcome_key=outcome_key,
            mode="all",
            min_n=insight_associations.DEFAULT_MIN_N,
            interactions="pairwise",
            top=100,
        )
    return sorted(selected), selection


def _phase5_compute_manual(requested, explicit_outcomes, explicit_mode=None):
    """Compute one snapshot of every outcome/mode before opening a writer."""
    _phase5_schema_ready()
    context = _phase3_context()
    c = cx_ro()
    try:
        c.execute("BEGIN")
        insight_migrations.require_version(c, 4)
        definitions = _phase3_definitions(c, context)
        outcomes, selection = _phase5_selected_outcomes(c, explicit_outcomes)
        if explicit_mode is not None:
            if len(outcomes) != 1:
                raise insight_ledger.LedgerError(
                    "validation_error",
                    "--mode requires exactly one explicit --outcome",
                    validation=True,
                )
            if explicit_mode not in _phase5_modes(outcomes[0]):
                raise insight_ledger.LedgerError(
                    "validation_error",
                    "--mode is not supported for the selected outcome",
                    validation=True,
                )
        registry_hash = (
            "sha256:"
            + insight_registry.registry_content_checksum(definitions)
        )
        verified = []
        for outcome_key in outcomes:
            modes = (
                (explicit_mode,)
                if explicit_mode is not None
                else _phase5_modes(outcome_key)
            )
            for outcome_mode in modes:
                verified.append(
                    (
                        outcome_key,
                        outcome_mode,
                        insight_ledger.compute_verified_analysis(
                            c,
                            definitions,
                            requested,
                            context,
                            outcome_key=outcome_key,
                            outcome_mode=outcome_mode,
                        ),
                    )
                )
    finally:
        c.close()
    return outcomes, selection, registry_hash, verified


def _phase5_input_bound_initiator(prefix, verified):
    """Bind batch identity to the exact per-outcome Phase 4 input snapshots."""

    inputs = sorted(
        [
            {
                "outcome_key": outcome_key,
                "outcome_mode": outcome_mode,
                "input_fingerprint": result.payload["meta"]["input_fingerprint"],
            }
            for outcome_key, outcome_mode, result in verified
        ],
        key=lambda item: (
            item["outcome_key"],
            item["outcome_mode"],
            item["input_fingerprint"],
        ),
    )
    return (
        f"{prefix}/"
        + insight_provenance.sha256_id(
            {
                "contract_version": insight_ledger.LEDGER_CONTRACT_VERSION,
                "kind": "analysis_batch_inputs",
                "inputs": inputs,
            }
        )
    )


def _phase6_prepare_batch(kind, plan, trigger):
    """Create the deterministic fan-out before expensive read-only computation."""

    context = _phase3_context()
    c = cx_ro()
    try:
        c.execute("BEGIN")
        all_definitions = _phase3_definitions(c, context)
        freshness = insight_orchestrator.freshness_snapshot(
            c, now=_now().astimezone(timezone.utc),
        )
        definitions, suppressed = insight_orchestrator.suppress_stale_dependencies(
            all_definitions, freshness,
        )
        selection = insight_orchestrator.outcome_modes(
            c,
            trigger_kind=trigger["trigger_kind"] if trigger is not None else None,
        )
        for outcome in selection["outcomes"]:
            insight_associations.validate_options(
                outcome_key=outcome,
                mode="all",
                min_n=insight_associations.DEFAULT_MIN_N,
                interactions="pairwise",
                top=100,
            )
        registry_hash = (
            "sha256:" + insight_registry.registry_content_checksum(definitions)
        )
    finally:
        c.close()

    initiator = (
        f"trigger/{trigger['trigger_id']}"
        if trigger is not None
        else f"scheduled-{kind}-v1"
    )
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        producer_triggers = []
        restart_definitions = [
            definition for definition in definitions
            if definition.key == "running.restart"
        ]
        if restart_definitions:
            primary = next(
                requested for role, requested
                in insight_orchestrator.range_objects(plan)
                if role == "primary"
            )
            running_adapter = insight_frame.load_adapters()["running"]
            restart_observations = running_adapter.load(
                c,
                tuple(restart_definitions),
                primary,
                context,
                include_provenance=True,
            )
            for observation in restart_observations:
                if (
                    observation.feature_key == "running.restart"
                    and observation.state == "observed"
                    and observation.value == 1
                ):
                    producer_triggers.append(
                        insight_orchestrator.enqueue_internal_trigger(
                            c,
                            trigger_kind="running_restart",
                            source_table="workouts",
                            source_row_key=f"restart:{observation.observed_at}",
                            event_date=observation.observed_at,
                        )
                    )
        batch = insight_ledger.create_analysis_batch(
            c,
            run_kind=kind,
            anchor_date=plan["anchor_date"],
            initiator_key=initiator,
            outcome_selection=selection["selection"],
            outcomes=selection["outcome_modes"],
            ranges=plan["ranges"],
            registry_sha256_value=registry_hash,
            range_plan_version=insight_orchestrator.RANGE_PLAN_VERSION,
        )
        runs = []
        for range_row in batch["ranges"]:
            for outcome in selection["outcome_modes"]:
                runs.append(insight_ledger.start_analysis_run(
                    c,
                    batch_id=batch["batch_id"],
                    range_id=range_row["range_id"],
                    outcome_key=outcome["outcome_key"],
                    outcome_mode=outcome["outcome_mode"],
                    batch_outcomes=selection["outcome_modes"],
                ))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return {
        "batch": batch, "runs": runs, "definitions": definitions,
        "context": context, "selection": selection, "freshness": freshness,
        "suppressed_dependencies": suppressed,
        "producer_triggers": producer_triggers,
    }


def _phase6_compute_runs(prepared, plan):
    """Compute all running fan-out members on one read-only snapshot."""

    ranges = {
        item["range_role"]: requested
        for item, (_role, requested) in zip(
            plan["ranges"], insight_orchestrator.range_objects(plan), strict=True,
        )
    }
    by_range = {
        item["range_id"]: item["range_role"]
        for item in prepared["batch"]["ranges"]
    }
    computed, failed = [], []
    c = cx_ro()
    try:
        c.execute("BEGIN")
        # Rebuild inside the frozen DB snapshot, then apply the same freshness
        # dependency filter calculated for the batch identity.
        definitions = _phase3_definitions(c, prepared["context"])
        definitions, _unused = insight_orchestrator.suppress_stale_dependencies(
            definitions, prepared["freshness"],
        )
        for run in prepared["runs"]:
            if run["status"] != "running":
                continue
            role = by_range[run["range_id"]]
            try:
                verified = insight_ledger.compute_verified_analysis(
                    c,
                    definitions,
                    ranges[role],
                    prepared["context"],
                    outcome_key=run["outcome_key"],
                    outcome_mode=run["outcome_mode"],
                )
                computed.append((run, verified))
            except Exception as exc:
                code = getattr(exc, "code", None)
                if not isinstance(code, str) or re.fullmatch(
                    r"[a-z][a-z0-9_]{0,79}", code,
                ) is None:
                    code = "analysis_compute_failed"
                failed.append((run, code))
    finally:
        c.close()

    persisted = []
    for run, verified in computed:
        c = cx()
        try:
            c.execute("BEGIN IMMEDIATE")
            persisted.append(insight_ledger.persist_analysis_run(
                c, run_id=run["run_id"], verified=verified,
            ))
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    for run, reason in failed:
        c = cx()
        try:
            c.execute("BEGIN IMMEDIATE")
            persisted.append(insight_ledger.fail_analysis_run(
                c, run_id=run["run_id"], reason_code=reason,
            ))
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    return persisted


def _phase6_refs_and_novelty(c, batch, kind, trigger):
    runs = [
        dict(row) for row in c.execute(
            """SELECT r.*,q.range_role
                 FROM analysis_runs r
                 JOIN analysis_range_requests q ON q.range_id=r.range_id
                WHERE r.batch_id=?
                ORDER BY q.range_role,r.outcome_key,r.outcome_mode,r.run_id""",
            (batch["batch_id"],),
        )
    ]
    run_refs = [
        {
            "run_id": row["run_id"],
            "purpose": (
                "trigger" if kind == "trigger"
                else row["range_role"]
            ),
        }
        for row in runs
    ]
    finding_rows = [
        dict(row) for row in c.execute(
            """SELECT f.*,q.range_role
                 FROM analysis_findings f
                 JOIN analysis_runs r ON r.run_id=f.run_id
                 JOIN analysis_range_requests q ON q.range_id=r.range_id
                WHERE r.batch_id=? AND f.eligible_for_hypothesis=1
                ORDER BY q.range_role,f.outcome_key,f.finding_id LIMIT 256""",
            (batch["batch_id"],),
        )
    ]
    finding_refs = [
        {"finding_id": row["finding_id"], "role": "primary"}
        for row in finding_rows
    ]
    evaluation_rows = [
        dict(row) for row in c.execute(
            """SELECT e.*
                 FROM hypothesis_evaluations e
                 JOIN analysis_runs r ON r.run_id=e.run_id
                WHERE r.batch_id=?
                ORDER BY e.hypothesis_id,e.id DESC""",
            (batch["batch_id"],),
        )
    ]
    latest_evaluations = []
    seen_hypotheses = set()
    for row in evaluation_rows:
        if row["hypothesis_id"] in seen_hypotheses:
            continue
        seen_hypotheses.add(row["hypothesis_id"])
        latest_evaluations.append(row)
    latest_evaluations = latest_evaluations[:256]
    hypothesis_refs = [
        {
            "hypothesis_id": row["hypothesis_id"],
            "evaluation_id": row["id"],
            "role": "changed" if row["transition_applied"] else "context",
        }
        for row in latest_evaluations
    ]
    has_weekly_baseline = True
    if kind == "weekly":
        has_weekly_baseline = c.execute(
            """SELECT 1 FROM synthesis_runs
                WHERE cadence='manual' AND status='completed'
                ORDER BY completed_at DESC LIMIT 1"""
        ).fetchone() is not None
    decision = insight_orchestrator.novelty_decision(
        cadence=kind,
        finding_fingerprints=(
            row["evidence_fingerprint"] for row in finding_rows
        ),
        transition_fingerprints=(
            row["evidence_fingerprint"]
            for row in latest_evaluations if row["transition_applied"]
        ),
        approved_trigger=trigger is not None,
        has_weekly_baseline=has_weekly_baseline,
    )
    return {
        "runs": runs, "run_refs": run_refs,
        "finding_rows": finding_rows, "finding_refs": finding_refs,
        "evaluation_rows": latest_evaluations,
        "hypothesis_refs": hypothesis_refs, "novelty": decision,
    }


def _phase6_record_no_message(c, *, batch, kind, anchor, refs, status, reason):
    evidence_fingerprint = insight_synthesis.synthesis_evidence_fingerprint(
        c,
        analysis_batch_id=batch["batch_id"],
        run_refs=refs["run_refs"],
        finding_refs=[],
        hypothesis_refs=[],
    )
    synthesis_id = insight_provenance.sha256_id({
        "contract_version": insight_synthesis.SYNTHESIS_CONTRACT_VERSION,
        "kind": "scheduled_no_message",
        "analysis_batch_id": batch["batch_id"],
        "cadence": kind, "status": status, "reason": reason,
        "evidence_fingerprint": evidence_fingerprint,
    })
    result = insight_synthesis.record_synthesis(c, {
        "synthesis_id": synthesis_id,
        "analysis_batch_id": batch["batch_id"],
        "cadence": kind,
        "reason_code": reason,
        "cutoff_date": anchor,
        "evidence_fingerprint": evidence_fingerprint,
        "context_version": insight_synthesis.SYNTHESIS_CONTEXT_VERSION,
        "prompt_sha256": None,
        "model_id": None,
        "provider": None,
        "run_refs": refs["run_refs"],
        "finding_refs": [],
        "hypothesis_refs": [],
        "narrative_md": None,
        "rendered_md": None,
        "status": status,
        "no_message_reason_code": reason,
        "annotations": [],
        "notification": None,
    })
    return result


def _phase6_finalize(prepared, plan, kind, trigger, trigger_payload):
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        if trigger_payload is not None:
            # A long analytical computation never inherits authority from an
            # expired worker: re-check the exact generation/token fence.
            insight_orchestrator.assert_trigger_lease(c, trigger_payload)
        batch = insight_ledger.finalize_analysis_batch(
            c, batch_id=prepared["batch"]["batch_id"],
        )
        has_weekly_baseline = True
        if kind == "weekly":
            has_weekly_baseline = c.execute(
                """SELECT 1 FROM synthesis_runs
                    WHERE cadence='manual' AND status='completed'
                    ORDER BY completed_at DESC LIMIT 1"""
            ).fetchone() is not None
        dormancy = None
        if kind == "monthly":
            dormancy = (
                "dormant_stale_prerequisite"
                if prepared["suppressed_dependencies"]
                else "dormant_no_eligible_data"
            )
        if kind == "nightly" or (kind == "weekly" and not has_weekly_baseline):
            ledger = {
                "ok": True,
                "changed": False,
                "skipped": True,
                "reason_code": (
                    "nightly_analysis_only"
                    if kind == "nightly"
                    else "bootstrap_baseline_required"
                ),
            }
        else:
            ledger = insight_ledger.refresh_batch_hypotheses(
                c, batch_id=batch["batch_id"], dormancy_reason=dormancy,
            )

        # Derived producers share this transaction with the ledger transition.
        transitions = [
            dict(row) for row in c.execute(
                """SELECT e.*
                     FROM hypothesis_evaluations e
                     JOIN analysis_runs r ON r.run_id=e.run_id
                    WHERE r.batch_id=? AND e.transition_applied=1
                    ORDER BY e.id""",
                (batch["batch_id"],),
            )
        ]
        derived_triggers = list(prepared["producer_triggers"])
        for row in transitions:
            trigger_kind = (
                "hypothesis_replicated" if row["status"] == "replicated"
                else "hypothesis_reversal"
                if row["evidence_class"] == "opposite_pass"
                else None
            )
            if trigger_kind is not None:
                derived_triggers.append(insight_orchestrator.enqueue_internal_trigger(
                    c,
                    trigger_kind=trigger_kind,
                    source_table="hypothesis_evaluations",
                    source_row_key=str(row["id"]),
                    event_date=row["range_to"] or plan["anchor_date"],
                ))
        refs = _phase6_refs_and_novelty(c, batch, kind, trigger)
        terminal_status = "completed"
        reason = refs["novelty"].reason_code
        synthesis = None
        preparation = None
        terminal_runs = refs["runs"]
        usable = sum(
            row["status"] == "completed" for row in terminal_runs
        )
        if (
            kind == "weekly"
            and refs["novelty"].reason_code == "bootstrap_baseline_required"
        ):
            terminal_status, reason = "no_novelty", "bootstrap_baseline_required"
        elif batch["status"] == "failed":
            terminal_status, reason = "failed", "all_analysis_runs_failed"
        elif usable == 0:
            terminal_status, reason = "insufficient_data", "no_eligible_analysis_data"
        elif not refs["novelty"].eligible:
            terminal_status = "no_novelty"
        if kind != "nightly":
            if terminal_status in {
                "failed", "insufficient_data", "no_novelty", "suppressed",
            }:
                synthesis = _phase6_record_no_message(
                    c, batch=batch, kind=kind, anchor=plan["anchor_date"],
                    refs=refs, status=terminal_status, reason=reason,
                )
            else:
                evidence_fingerprint = insight_synthesis.synthesis_evidence_fingerprint(
                    c,
                    analysis_batch_id=batch["batch_id"],
                    run_refs=refs["run_refs"],
                    finding_refs=refs["finding_refs"],
                    hypothesis_refs=refs["hypothesis_refs"],
                )
                preparation = {
                    "boundary": "synthesis-record",
                    "analysis_batch_id": batch["batch_id"],
                    "cadence": kind,
                    "cutoff_date": plan["anchor_date"],
                    "context_version": insight_synthesis.SYNTHESIS_CONTEXT_VERSION,
                    "evidence_fingerprint": evidence_fingerprint,
                    "run_refs": refs["run_refs"],
                    "finding_refs": refs["finding_refs"],
                    "hypothesis_refs": refs["hypothesis_refs"],
                    "structured_slots": insight_orchestrator.structured_slots(
                        cadence=kind,
                        run_rows=refs["runs"],
                        finding_rows=refs["finding_rows"],
                        evaluation_rows=refs["evaluation_rows"],
                        freshness=prepared["freshness"],
                        suppressed_dependencies=prepared["suppressed_dependencies"],
                    ),
                    "model_invoked": False,
                }
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return {
        "batch": batch, "ledger": ledger, "status": terminal_status,
        "reason_code": reason, "synthesis": synthesis,
        "synthesis_preparation": preparation,
        "derived_triggers": derived_triggers,
    }


def _phase6_analysis_refresh(a):
    _phase5_schema_ready()
    trigger_payload = None
    trigger = None
    if a.kind == "trigger":
        trigger_payload = _orchestration_payload()
        insight_orchestrator.validate_trigger_lease(trigger_payload)
        _phase5_schema_ready()
        c = cx_ro()
        try:
            trigger = insight_orchestrator.assert_trigger_lease(
                c, trigger_payload,
            )
            source_run = None
            if trigger["source_table"] == "hypothesis_evaluations":
                source_run = c.execute(
                    """SELECT r.analysis_to
                         FROM hypothesis_evaluations e
                         JOIN analysis_runs r ON r.run_id=e.run_id
                        WHERE e.id=?""",
                    (trigger["source_row_key"],),
                ).fetchone()
        finally:
            c.close()
        plan = insight_orchestrator.cadence_plan(
            "trigger",
            local_now=_now(),
            event_date=trigger["event_date"],
            analysis_to=source_run["analysis_to"] if source_run else None,
        )
    else:
        plan = insight_orchestrator.cadence_plan(
            a.kind, local_now=_now(), anchor=a.anchor,
        )
    prepared = _phase6_prepare_batch(a.kind, plan, trigger)
    runs = _phase6_compute_runs(prepared, plan)
    finalized = _phase6_finalize(
        prepared, plan, a.kind, trigger, trigger_payload,
    )
    print(canonical_json({
        "ok": True,
        "contract_version": insight_orchestrator.ORCHESTRATOR_CONTRACT_VERSION,
        "plan": plan,
        "outcomes": prepared["selection"],
        "freshness": prepared["freshness"],
        "suppressed_dependencies": prepared["suppressed_dependencies"],
        "runs": runs,
        **finalized,
    }))


def analysis_refresh_cmd(a):
    if a.kind != "manual":
        if (
            a.outcome
            or getattr(a, "mode", None) is not None
            or a.from_date is not None
            or a.to_date is not None
            or a.days is not None
            or a.all_dates
        ):
            raise insight_orchestrator.OrchestrationError(
                "validation_error",
                "scheduled analysis-refresh does not accept caller range/outcome overrides",
                validation=True,
            )
        if a.kind == "trigger":
            if not a.stdin or a.anchor is not None:
                raise insight_orchestrator.OrchestrationError(
                    "validation_error",
                    "trigger analysis-refresh requires only --kind trigger --stdin",
                    validation=True,
                )
        elif a.stdin:
            raise insight_orchestrator.OrchestrationError(
                "validation_error",
                "--stdin is valid only for trigger analysis-refresh",
                validation=True,
            )
        return _phase6_analysis_refresh(a)
    if a.stdin:
        raise insight_orchestrator.OrchestrationError(
            "validation_error",
            "manual analysis-refresh does not accept --stdin",
            validation=True,
        )
    explicit_mode = getattr(a, "mode", None)
    if explicit_mode is not None:
        if not a.outcome or len(a.outcome) != 1:
            raise insight_ledger.LedgerError(
                "validation_error",
                "--mode requires exactly one explicit --outcome",
                validation=True,
            )
        if explicit_mode not in _phase5_modes(a.outcome[0]):
            raise insight_ledger.LedgerError(
                "validation_error",
                "--mode is not supported for the selected outcome",
                validation=True,
            )
    requested = _phase3_range(a)
    anchor = _phase5_anchor(requested, a.anchor)
    outcomes, selection, registry_hash, verified = _phase5_compute_manual(
        requested, a.outcome, explicit_mode,
    )
    outcome_modes = [
        {"outcome_key": outcome_key, "outcome_mode": outcome_mode}
        for outcome_key, outcome_mode, _result in verified
    ]
    range_record = _phase5_range_record(requested)
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        batch = insight_ledger.create_analysis_batch(
            c,
            run_kind="manual",
            anchor_date=anchor,
            initiator_key=_phase5_input_bound_initiator(
                "manual-analysis-refresh", verified,
            ),
            outcome_selection=selection,
            outcomes=outcome_modes,
            ranges=[range_record],
            registry_sha256_value=registry_hash,
        )
        primary = next(
            item for item in batch["ranges"] if item["range_role"] == "primary"
        )
        runs = []
        for outcome_key, outcome_mode, result in verified:
            run = insight_ledger.start_analysis_run(
                c,
                batch_id=batch["batch_id"],
                range_id=primary["range_id"],
                outcome_key=outcome_key,
                outcome_mode=outcome_mode,
                batch_outcomes=outcome_modes,
            )
            runs.append(
                insight_ledger.persist_analysis_run(
                    c, run_id=run["run_id"], verified=result,
                )
            )
        completed = insight_ledger.finalize_analysis_batch(
            c, batch_id=batch["batch_id"],
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    print(canonical_json({
        "ok": True,
        "contract_version": insight_ledger.LEDGER_CONTRACT_VERSION,
        "batch": completed,
        "outcomes": outcomes,
        "runs": runs,
    }))


def hypothesis_promote_cmd(a):
    insight_associations.validate_options(
        outcome_key=a.outcome,
        mode="all",
        min_n=insight_associations.DEFAULT_MIN_N,
        interactions="pairwise",
        top=100,
    )
    insight_associations.validate_sha256_id(a.finding_id, "finding-id")
    insight_associations.validate_sha256_id(
        a.input_fingerprint, "input-fingerprint",
    )
    range_forms = (
        int(a.from_date is not None or a.to_date is not None)
        + int(a.days is not None)
        + int(a.all_dates)
    )
    if range_forms != 1:
        raise insight_ledger.LedgerError(
            "validation_error",
            "hypothesis-promote requires exactly one explicit range",
            validation=True,
        )
    requested = _phase3_range(a)
    anchor = _phase5_anchor(requested)
    _phase5_schema_ready()
    context = _phase3_context()
    c = cx()
    try:
        # Recompute, persist if absent, and promote from one locked snapshot.
        # A concurrent capture cannot make a verified browser finding stale in
        # the gap between the replay and the ledger write.
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        definitions = _phase3_definitions(c, context)
        replay = insight_ledger.compute_verified_finding(
            c,
            definitions,
            requested,
            context,
            outcome_key=a.outcome,
            finding_id_value=a.finding_id,
            input_fingerprint_value=a.input_fingerprint,
        )
        finding = replay.payload["finding"]
        outcome_mode = finding["outcome"]["mode"]
        persisted = c.execute(
            "SELECT run_id FROM analysis_findings WHERE finding_id=?",
            (finding["finding_id"],),
        ).fetchone()
        if persisted is None:
            analysis = insight_ledger.compute_verified_analysis(
                c,
                definitions,
                requested,
                context,
                outcome_key=a.outcome,
                outcome_mode=outcome_mode,
            )
            matching = next(
                (
                    item for item in analysis.payload["findings"]
                    if item["finding_id"] == finding["finding_id"]
                ),
                None,
            )
            if (
                matching is None
                or matching["provenance"]["evidence_fingerprint"]
                != finding["provenance"]["evidence_fingerprint"]
            ):
                raise insight_ledger.LedgerError(
                    "stale_finding",
                    "replayed finding changed before persistence",
                )
            meta = analysis.payload["meta"]
            range_record = _phase5_range_record(requested)
            batch = insight_ledger.create_analysis_batch(
                c,
                run_kind="manual",
                anchor_date=anchor,
                initiator_key=_phase5_input_bound_initiator(
                    "hypothesis-promote",
                    [(a.outcome, outcome_mode, analysis)],
                ),
                outcome_selection="explicit_set",
                outcomes=[(a.outcome, outcome_mode)],
                ranges=[range_record],
                registry_sha256_value=meta["registry_sha256"],
                analysis_version=meta["analysis_version"],
                registry_version=meta["registry_version"],
                engine_sha256_value=meta["engine_sha256"],
            )
            primary = next(
                item for item in batch["ranges"] if item["range_role"] == "primary"
            )
            run = insight_ledger.start_analysis_run(
                c,
                batch_id=batch["batch_id"],
                range_id=primary["range_id"],
                outcome_key=a.outcome,
                outcome_mode=outcome_mode,
                batch_outcomes=[(a.outcome, outcome_mode)],
            )
            persisted_run = insight_ledger.persist_analysis_run(
                c, run_id=run["run_id"], verified=analysis,
            )
            insight_ledger.finalize_analysis_batch(
                c, batch_id=batch["batch_id"],
            )
            run_id = persisted_run["run_id"]
        else:
            run_id = persisted["run_id"]
        result = insight_ledger.promote_verified_finding(
            c,
            run_id=run_id,
            verified=replay,
            explicit=True,
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    print(canonical_json(result))


def hypothesis_refresh_cmd(a):
    insight_associations.validate_sha256_id(a.batch_id, "batch-id")
    _phase5_schema_ready()
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        result = insight_ledger.refresh_batch_hypotheses(
            c, batch_id=a.batch_id,
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    print(canonical_json(result))


def _phase5_annotation_payload():
    text = sys.stdin.read()
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise insight_ledger.LedgerError(
            "validation_error", "stdin must be valid UTF-8", validation=True,
        ) from exc
    if not encoded or len(encoded) > 32_768:
        raise insight_ledger.LedgerError(
            "validation_error",
            "stdin must contain 1-32768 UTF-8 bytes",
            validation=True,
        )

    def closed_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise insight_ledger.LedgerError(
                    "validation_error",
                    f"duplicate JSON key is not allowed: {key}",
                    validation=True,
                )
            result[key] = value
        return result

    try:
        payload = json.loads(text, object_pairs_hook=closed_object)
    except insight_ledger.LedgerError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        detail = (
            exc.msg
            if isinstance(exc, json.JSONDecodeError)
            else "JSON nesting exceeds the supported bound"
            if isinstance(exc, RecursionError)
            else "numeric literal exceeds the supported bound"
        )
        raise insight_ledger.LedgerError(
            "validation_error",
            f"stdin is not valid JSON: {detail}",
            validation=True,
        ) from exc
    if isinstance(payload, dict):
        for key, value in payload.items():
            for text_value in (key, value):
                if isinstance(text_value, str):
                    try:
                        text_value.encode("utf-8")
                    except UnicodeEncodeError as exc:
                        raise insight_ledger.LedgerError(
                            "validation_error",
                            "annotation strings must be valid UTF-8",
                            validation=True,
                        ) from exc
    if not isinstance(payload, dict) or set(payload) != _PHASE5_ANNOTATION_KEYS:
        raise insight_ledger.LedgerError(
            "validation_error",
            "hypothesis annotation fields do not match the closed contract",
            validation=True,
        )
    evaluation_id = payload["evaluation_id"]
    if (
        evaluation_id is not None
        and (
            isinstance(evaluation_id, bool)
            or not isinstance(evaluation_id, int)
            or evaluation_id < 1
            or evaluation_id > insight_ledger.SQLITE_MAX_ROWID
        )
    ):
        raise insight_ledger.LedgerError(
            "validation_error",
            "evaluation_id must be a positive SQLite row identifier",
            validation=True,
        )
    return payload


def hypothesis_annotate_cmd(a):
    payload = _phase5_annotation_payload()
    _phase5_schema_ready()
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        result = insight_ledger.append_annotation(
            c,
            hypothesis_id=payload["hypothesis_id"],
            evaluation_id=payload["evaluation_id"],
            annotation_kind=payload["annotation_kind"],
            content=payload["content"],
            source=payload["source"],
            synthesis_id=payload["synthesis_id"],
            context_version=payload["context_version"],
            prompt_sha256=payload["prompt_sha256"],
            model_id=payload["model_id"],
            provider=payload["provider"],
            supersedes_id=payload["supersedes_id"],
            annotation_id=payload["annotation_id"],
        )
        if result["input_sha256"] != payload["input_sha256"]:
            raise insight_ledger.LedgerError(
                "mismatched_annotation",
                "input_sha256 does not match the canonical annotation",
                validation=True,
            )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    print(canonical_json({
        "ok": True,
        "contract_version": insight_ledger.LEDGER_CONTRACT_VERSION,
        "annotation": result,
    }))


def hypotheses_cmd(a):
    if not 1 <= a.limit <= 100:
        raise insight_ledger.LedgerError(
            "validation_error",
            "--limit must be between 1 and 100",
            validation=True,
        )
    if a.before is not None:
        insight_associations.validate_sha256_id(a.before, "before")
    if a.outcome is not None:
        insight_associations.validate_options(
            outcome_key=a.outcome,
            mode="all",
            min_n=insight_associations.DEFAULT_MIN_N,
            interactions="pairwise",
            top=100,
        )
    _phase5_schema_ready()
    c = cx_ro()
    try:
        result = insight_ledger.list_hypotheses(
            c,
            status=a.status,
            outcome_key=a.outcome,
            limit=a.limit,
            before=a.before,
        )
    finally:
        c.close()
    print(canonical_json(result))


def hypothesis_brief_cmd(a):
    insight_associations.validate_sha256_id(a.hypothesis_id, "hypothesis-id")
    _phase5_schema_ready()
    c = cx_ro()
    try:
        result = insight_ledger.hypothesis_brief(c, a.hypothesis_id)
    finally:
        c.close()
    print(canonical_json(result))


def synthesis_record_cmd(a):
    payload = insight_synthesis.parse_synthesis_json(sys.stdin.read())
    insight_synthesis.validate_synthesis_record(payload)
    _phase5_schema_ready()
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        result = insight_synthesis.record_synthesis(c, payload)
        # Publish SQLite first. If the filesystem append then fails, an exact
        # retry is a verified DB no-op and can safely finish the idempotent
        # file append. No final file can outlive a failed DB commit.
        c.commit()
        markdown_path = None
        if result["message_eligible"]:
            vault_root = _vault_root()
            markdown_path = insight_synthesis.write_synthesis_markdown(
                c, result["synthesis_id"], vault_root=vault_root,
            )
    except Exception:
        if c.in_transaction:
            c.rollback()
        raise
    finally:
        c.close()
    print(canonical_json({
        **result,
        "markdown_path": str(markdown_path) if markdown_path is not None else None,
    }))


def synthesis_prepare_cmd(a):
    """Return ledger-owned references for one external Hermes synthesis turn."""

    insight_associations.validate_sha256_id(a.batch_id, "batch-id")
    _phase5_schema_ready()
    c = cx_ro()
    try:
        batch = c.execute(
            """SELECT batch_id,run_kind,anchor_date,status
                 FROM analysis_batches WHERE batch_id=?""",
            (a.batch_id,),
        ).fetchone()
        if batch is None:
            raise insight_synthesis.SynthesisError(
                "stale_reference", "analysis batch does not exist",
            )
        batch = dict(batch)
        cadence = batch["run_kind"]
        if cadence not in insight_synthesis.CADENCES:
            raise insight_synthesis.SynthesisError(
                "validation_error",
                "analysis batch cadence cannot produce a Hermes synthesis",
                validation=True,
            )
        refs = _phase6_refs_and_novelty(c, batch, cadence, None)
        evidence_fingerprint = insight_synthesis.synthesis_evidence_fingerprint(
            c,
            analysis_batch_id=batch["batch_id"],
            run_refs=refs["run_refs"],
            finding_refs=refs["finding_refs"],
            hypothesis_refs=refs["hypothesis_refs"],
        )
        freshness = insight_orchestrator.freshness_snapshot(
            c, now=_now().astimezone(timezone.utc),
        )
        structured_slots = insight_orchestrator.structured_slots(
            cadence=cadence,
            run_rows=refs["runs"],
            finding_rows=refs["finding_rows"],
            evaluation_rows=refs["evaluation_rows"],
            freshness=freshness,
            suppressed_dependencies=[],
        )
    finally:
        c.close()
    print(canonical_json({
        "ok": True,
        "boundary": "synthesis-record",
        "analysis_batch_id": batch["batch_id"],
        "cadence": cadence,
        "cutoff_date": batch["anchor_date"],
        "context_version": insight_synthesis.SYNTHESIS_CONTEXT_VERSION,
        "evidence_fingerprint": evidence_fingerprint,
        "run_refs": refs["run_refs"],
        "finding_refs": refs["finding_refs"],
        "hypothesis_refs": refs["hypothesis_refs"],
        "assessment_state": (
            "assessed" if refs["finding_refs"] else "insufficient_data"
        ),
        "structured_slots": structured_slots,
        "model_invoked": False,
    }))


def synthesis_history_cmd(a):
    if not 1 <= a.limit <= 100:
        raise insight_synthesis.SynthesisError(
            "validation_error",
            "--limit must be between 1 and 100",
            validation=True,
        )
    if a.before is not None:
        insight_associations.validate_sha256_id(a.before, "before")
    _phase5_schema_ready()
    c = cx_ro()
    try:
        result = insight_synthesis.synthesis_history(
            c, limit=a.limit, before=a.before,
        )
    finally:
        c.close()
    print(canonical_json(result))


# =========================================================== Phase 6 orchestration
def _orchestration_payload():
    return insight_orchestrator.parse_json_object(sys.stdin.read())


def _orchestration_write(fn, *args):
    """Run one queue/outbox transition under the canonical immediate fence."""

    _phase5_schema_ready()
    c = cx()
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 4)
        result = fn(c, *args)
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    print(canonical_json(result))


def insight_trigger_enqueue_cmd(a):
    payload = _orchestration_payload()
    insight_orchestrator.validate_trigger_enqueue(payload)
    _orchestration_write(insight_orchestrator.enqueue_trigger, payload)


def insight_trigger_claim_cmd(a):
    _orchestration_write(insight_orchestrator.claim_trigger, a.worker_id)


def insight_trigger_renew_cmd(a):
    payload = _orchestration_payload()
    insight_orchestrator.validate_trigger_lease(payload)
    _orchestration_write(insight_orchestrator.renew_trigger, payload)


def insight_trigger_complete_cmd(a):
    payload = _orchestration_payload()
    insight_orchestrator.validate_trigger_complete(payload)
    _orchestration_write(insight_orchestrator.complete_trigger, payload)


def insight_trigger_fail_cmd(a):
    payload = _orchestration_payload()
    insight_orchestrator.validate_trigger_fail(payload)
    _orchestration_write(insight_orchestrator.fail_trigger, payload)


def insight_notification_claim_cmd(a):
    _orchestration_write(insight_orchestrator.claim_notification, a.worker_id)


def insight_notification_begin_dispatch_cmd(a):
    payload = _orchestration_payload()
    _orchestration_write(
        insight_orchestrator.begin_notification_dispatch, payload,
    )


def insight_notification_ack_cmd(a):
    payload = _orchestration_payload()
    _orchestration_write(insight_orchestrator.acknowledge_notification, payload)


def insight_notification_fail_cmd(a):
    payload = _orchestration_payload()
    insight_orchestrator.validate_notification_fail(payload)
    _orchestration_write(insight_orchestrator.fail_notification, payload)


def insight_notification_resolve_cmd(a):
    payload = _orchestration_payload()
    _orchestration_write(insight_orchestrator.resolve_notification, payload)


def insight_run_status_cmd(a):
    if not 1 <= a.limit <= 100:
        raise insight_orchestrator.OrchestrationError(
            "validation_error",
            "--limit must be between 1 and 100",
            validation=True,
        )
    _phase5_schema_ready()
    c = cx_ro()
    try:
        result = insight_orchestrator.insight_run_status(c, limit=a.limit)
    finally:
        c.close()
    print(canonical_json(result))


# =========================================================== cli
def analysis_job_cmd(a):
    from hermes_insights import analysis_jobs
    analysis_jobs.cli(a.cmd, DB, a, {
        "outcome-associations": outcome_associations_cmd,
        "finding-evidence": finding_evidence_cmd,
    }, _command_context().cli_path)


def main():
    """Launch the shared CLI with the remaining legacy handlers wired explicitly."""
    insight_cli.run(
        _command_context(),
        {
            "analysis-job-start": analysis_job_cmd,
            "analysis-job-status": analysis_job_cmd,
            "analysis-job-work": analysis_job_cmd,
            "analysis-job-execute": analysis_job_cmd,
            "today": today_session,
            "muscle-volume": muscle_volume,
            "last-session": last_session,
            "log-set": log_set,
            "routine-set": routine_set,
            "routine-remove": routine_remove,
            "routine-undo": routine_undo,
            "schedule-set": schedule_set,
            "fitness-test-log": fitness_test_log,
            "fitness-tests": fitness_tests,
            "fitness-test-void": fitness_test_void,
            "athletic-radar": athletic_radar,
            "athletic-target-set": athletic_target_set,
            "strength-ratios": strength_ratios,
            "vtaper": vtaper,
            "muscle-detail": muscle_detail,
            "muscle-map": muscle_map,
            "pain-log": pain_log,
            "self-test-log": self_test_log,
            "exercise-trial-log": exercise_trial_log,
            "physio-void": physio_void,
            "lab-capture": lab_capture,
            "lab-ingest": lab_ingest,
            "labs": labs,
            "write-note": write_note,
            "journal-capture": journal_capture,
            "transcript-capture": transcript_capture,
            "recipe-ingredients-set": recipe_ingredients_set,
            "nutrition-target-set": nutrition_target_set,
            "profile-set": profile_set,
            "phase-set": phase_set,
            "nutrition-targets": nutrition_targets,
            "nutrition-coverage": nutrition_coverage,
            "set-batch": set_batch,
            "recipe-tag": recipe_tag,
            "prep": prep,
            "eat": eat,
            "log-food": log_food,
            "menu": menu,
            "restock-check": restock_check,
            "restock-mark": restock_mark,
            "log": log,
            "day-rating": day_rating,
            "water-add": water_add,
            "query": query,
            "bp-brief": bp_brief,
            "summary": summary,
            "schema": schema,
            "build-daily-frame": build_daily_frame,
            "features": features,
            "correlate": correlate,
            "day-signature": day_signature,
            "commitment-set": commitment_set,
            "commitment-list": commitment_list,
            "log-commitment": log_commitment,
            "checkin": checkin,
            "feedback-status": feedback_status,
            "adherence": adherence,
            "planned-time-set": planned_time_set,
            "timing-adherence": timing_adherence,
            "data-coverage": data_coverage,
            "scores": scores,
            "readiness": readiness,
            "schema-status": schema_status_cmd,
            "schema-plan": schema_plan_cmd,
            "migrate": migrate_cmd,
            "capture-raw": capture_raw_cmd,
            "capture-resolve": capture_resolve_cmd,
            "event-log": event_log_cmd,
            "event-correct": event_correct_cmd,
            "event-void": event_void_cmd,
            "events": events_cmd,
            "capture-completeness-set": capture_completeness_set_cmd,
            "capture-completeness": capture_completeness_cmd,
            "entity-alias-set": entity_alias_set_cmd,
            "entity-alias-retire": entity_alias_retire_cmd,
            "entity-alias-history": entity_alias_history_cmd,
            "supplement-log": supplement_log,
            "feature-registry": feature_registry_cmd,
            "feature-frame": feature_frame_cmd,
            "data-readiness": data_readiness_cmd,
            "outcome-associations": outcome_associations_cmd,
            "finding-evidence": finding_evidence_cmd,
            "analysis-refresh": analysis_refresh_cmd,
            "hypothesis-promote": hypothesis_promote_cmd,
            "hypothesis-refresh": hypothesis_refresh_cmd,
            "hypothesis-annotate": hypothesis_annotate_cmd,
            "hypotheses": hypotheses_cmd,
            "hypothesis-brief": hypothesis_brief_cmd,
            "synthesis-prepare": synthesis_prepare_cmd,
            "synthesis-record": synthesis_record_cmd,
            "synthesis-history": synthesis_history_cmd,
            "insight-trigger-enqueue": insight_trigger_enqueue_cmd,
            "insight-trigger-claim": insight_trigger_claim_cmd,
            "insight-trigger-renew": insight_trigger_renew_cmd,
            "insight-trigger-complete": insight_trigger_complete_cmd,
            "insight-trigger-fail": insight_trigger_fail_cmd,
            "insight-notification-claim": insight_notification_claim_cmd,
            "insight-notification-begin-dispatch": insight_notification_begin_dispatch_cmd,
            "insight-notification-ack": insight_notification_ack_cmd,
            "insight-notification-fail": insight_notification_fail_cmd,
            "insight-notification-resolve": insight_notification_resolve_cmd,
            "insight-run-status": insight_run_status_cmd,
            "goal-list": goal_list_cmd,
            "goal-set": goal_set_cmd,
            "collector-run-record": collector_run_record_cmd,
            "fetch-weather": fetch_weather,
            "fetch-air": fetch_air,
        },
        argv=sys.argv[1:], output=out, parse_number=num,
        meal_types=MEAL_TYPES, restock_actions=RESTOCK_ACTIONS,
        scores_default_days=SCORES_DEFAULT_DAYS,
        quarterly_routines=HEVY_QUARTERLY_ROUTINES, stdin=sys.stdin,
        slug=slug, figure_sub_svg=FIGURE_SUB_SVG,
    )


if __name__ == "__main__":
    main()
