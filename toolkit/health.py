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
from hermes_insights import muscles as muscles_domain, fitness as fitness_domain
from hermes_insights.commands import (training as training_commands, fitness as fitness_commands,
    muscles as muscles_commands, physio as physio_commands)
from hermes_insights.figure_contracts import (
    _FIG_BASE_GROUP, MOBILITY_STALE_DAYS,
    FIGURE_REGION_GROUP,
    FIGURE_NON_MUSCLE,
    FIGURE_SUB_SVG,
    FIGURE_COARSE_SVG,
    FIGURE_LEVEL_LEGEND,
    FIGURE_RATIO_MAP,
    FIGURE_BALANCE_LEGEND,
)
from hermes_insights.physio import (
    PAIN_RED_FLAGS,
    PAIN_STALE_DAYS,
)
from hermes_insights.muscles import (
    require_submuscle_table as _ensure_submuscle_table,
)
from hermes_insights.physio import (
    pain_band as _pain_band,
)
from hermes_insights.muscle_figure import (
    fig_level as _fig_level, mobility_status as _mobility_status,
)
from hermes_insights import nutrition as nutrition_domain
from hermes_insights.commands import food as food_commands, nutrition as nutrition_commands
from hermes_insights.food import MEAL_TYPES, RESTOCK_ACTIONS, RESTOCK_THRESHOLD_MAX
from hermes_insights.nutrition import (
    OWNER_PROFILE_KEYS, SEX_VALUES, ACTIVITY_FALLBACK_VALUES, DIET_PHASES,
    NUTRITION_TARGET_NAMES, NUTRITION_WEIGHTS, RECIPE_MICRO_NAME_TO_KEY, RECIPE_MICRO_UNITS,
    daymax as _daymax, day_values as _nutrition_day_values, day_score as _nutrition_day_score,
    recipe_micro_factor as _recipe_micro_factor,
)
from hermes_insights.score_contracts import (
    SCORE_BAD_CUTOFF, SCORE_GOOD_CUTOFF, band as _band, clamp100 as _clamp100,
)
from hermes_insights.capture_contracts import (
    CAPTURE_SOURCES, DAYMAP, LOGGABLE, RATING, UPSERT_DATE_TABLES, capture_source,
    require_soreness_note as _subjective_daily_has_soreness_note,
)
from hermes_insights.followthrough import CHECKIN_KINDS, COMMIT_STATUS
from hermes_insights.schedules import TIMING_DEFAULT_TOL, WEEKDAYS
from hermes_insights.vault_notes import EDITABLE_NOTES, vault_write_path
from hermes_insights.runtime import table_exists as _table_exists
from hermes_insights.commands import (
    daily_capture as daily_capture_commands, followthrough as followthrough_commands,
    schedules as schedule_commands, notes as note_commands, collectors as collector_commands,
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
    """Compatibility entry point for the extracted training family."""
    out(training_commands.log_set(_command_context(), a, parse_number=num))

def today_session(a):
    """Compatibility entry point for the extracted training family."""
    out(training_commands.today_session(_command_context(), a))


def _ft(name, kind, group=None, ref=None, unilateral=0, priority=0):
    return insight_catalogs._ft(name, kind, group, ref, unilateral, priority)


def e1rm(load, reps):
    return insight_calculations.e1rm(load, reps)


def _group_weight_maps(c):
    return insight_calculations._group_weight_maps(c, muscle_to_group=MUSCLE_TO_GROUP)


def _basis_weights(title, authored, coarse):
    return insight_calculations._basis_weights(
        title, authored, coarse, mobility_exercises=MOBILITY_EXERCISES,
        non_volume_exercises=NON_VOLUME_EXERCISES,
    )


def _rollup7(c, source, days=7, anchor=None):
    return muscles_domain.rollup(c, source, days, anchor=anchor, clock=_now)


def muscle_volume(a):
    """Compatibility entry point for the extracted training family."""
    out(muscles_commands.muscle_volume(_command_context(), a))

def last_session(a):
    """Compatibility entry point for the extracted training family."""
    out(training_commands.last_session(_command_context(), a))

# =========================================================== program editing


def routine_set(a):
    """Compatibility entry point for the extracted training family."""
    out(training_commands.routine_set(_command_context(), a, parse_number=num))

def routine_remove(a):
    """Compatibility entry point for the extracted training family."""
    out(training_commands.routine_remove(_command_context(), a))

def routine_undo(a):
    """Compatibility entry point for the extracted training family."""
    out(training_commands.routine_undo(_command_context(), a))

def schedule_set(a):
    """Compatibility entry point for the extracted daily command."""
    out(schedule_commands.schedule_set(_command_context(), a))

def import_recipes(a):
    """Compatibility entry point for the extracted import command."""
    out(recipe_commands.import_csv(
        _command_context(), a, parse_number=num, slug=slug, meal_types=MEAL_TYPES))

def set_batch(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.set_batch(_command_context(), a))


def recipe_ingredients_set(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.recipe_ingredients_set(_command_context(), a, stdin=sys.stdin))


def recipe_tag(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.recipe_tag(_command_context(), a))


# ------------------------------------------------------- freezer restock state


def restock_check(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.restock_check(_command_context(), a))


def restock_mark(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.restock_mark(_command_context(), a))

def prep(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.prep(_command_context(), a))


def log_food(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.log_food(_command_context(), a))

def eat(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.eat(_command_context(), a))

def menu(a):
    """Compatibility entry point for the extracted food command."""
    out(food_commands.menu(_command_context(), a))

# =========================================================== generic logging


def log(a):
    """Compatibility entry point for the extracted daily command."""
    out(daily_capture_commands.log(_command_context(), a))

def water_add(a):
    """Compatibility entry point for the extracted daily command."""
    out(daily_capture_commands.water_add(_command_context(), a, water_target_ml=WATER_TARGET_ML))


def _capture_source(a):
    return capture_source(getattr(a, "source", None))

def day_rating(a):
    """Compatibility entry point for the extracted daily command."""
    out(daily_capture_commands.day_rating(_command_context(), a))

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


def _vault_root():
    return resolve_vault_root(DB)


def write_note(a):
    """Compatibility entry point for the extracted daily command."""
    out(note_commands.write_note(_command_context(), a, stdin=sys.stdin))

# =========================================================== §6 qualitative
# Raw text is PRESERVED VERBATIM before any extraction (vault law: raw/ is
# immutable). The extraction step itself is the agent mapping words to the
# EXISTING validated `log subjective_daily …` command — no new write surface.
# Neither command below is in the bridge allowlists (Telegram/agent path only).

def _vault_write_path(rel_dir, filename):
    return vault_write_path(_vault_root(), rel_dir, filename)


def _stdin_text(what):
    """Compatibility wrapper using the current command input stream."""
    return stdin_text(sys.stdin, what)


def journal_capture(a):
    """Compatibility entry point for the extracted daily command."""
    out(note_commands.journal_capture(_command_context(), a, stdin=sys.stdin))


def transcript_capture(a):
    """Compatibility entry point for the extracted daily command."""
    out(note_commands.transcript_capture(_command_context(), a, stdin=sys.stdin))


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


def _open_collector_url(url, *, timeout):
    # Keep HTTP initialization off unrelated CLI startup paths.
    import urllib.request
    return urllib.request.urlopen(url, timeout=timeout)


def fetch_weather(a):
    """Compatibility entry point for the extracted collector command."""
    return collector_commands.fetch_weather(_command_context(), a, open_url=_open_collector_url, output=out)


def fetch_air(a):
    """Compatibility entry point for the extracted collector command."""
    return collector_commands.fetch_air(_command_context(), a, open_url=_open_collector_url, output=out)

# =========================================================== follow-through
# The self-integrity layer: "did I keep my word to myself today?" Commitments
# are identity-framed config (name / when-then trigger / can't-fail floor /
# reward); commitments_log is the nightly kept/partly/broke record — the
# whole-day one-tap uses commitment_id=0. checkins are timed 1-5 spot readings
# (energy/focus/mood) so dose-timing effects become correlatable within a day.


def commitment_set(a):
    """Compatibility entry point for the extracted daily command."""
    out(followthrough_commands.commitment_set(_command_context(), a))

def commitment_list(a):
    """Compatibility entry point for the extracted daily command."""
    out(followthrough_commands.commitment_list(_command_context(), a))

def log_commitment(a):
    """Compatibility entry point for the extracted daily command."""
    out(followthrough_commands.log_commitment(_command_context(), a))

def checkin(a):
    """Compatibility entry point for the extracted daily command."""
    out(followthrough_commands.checkin(_command_context(), a))


def feedback_status(a):
    """Compatibility entry point for the extracted daily command."""
    out(followthrough_commands.feedback_status(_command_context(), a, output=out))

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


def planned_time_set(a):
    """Compatibility entry point for the extracted daily command."""
    out(schedule_commands.planned_time_set(_command_context(), a))


def _circ_diff_min(a_min, b_min):
    return insight_calculations._circ_diff_min(a_min, b_min)


def _start_hhmm(s):
    return insight_calculations._start_hhmm(s, datetime_type=datetime, timezone=CANON_TZ)


def timing_adherence(a):
    """Compatibility entry point for the extracted daily command."""
    out(schedule_commands.timing_adherence(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))


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
    return nutrition_domain.configured_positive_number(name, default, environ=os.environ)


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


def nutrition_target_set(a):
    """Compatibility entry point for the extracted nutrition command."""
    out(nutrition_commands.nutrition_target_set(_command_context(), a))


def import_cronometer(a):
    """Compatibility entry point for the extracted import command."""
    out(cronometer_commands.import_csv(_command_context(), a.csv, parse_number=num))

def import_google_health(a):
    """Compatibility entry point for the extracted import command."""
    out(google_health_commands.import_json(
        _command_context(), a.json_file, parse_number=num, stdin=sys.stdin))


def _score(v, **inputs):
    v = _clamp100(v)
    return {"score": v, "band": _band(v), "inputs": inputs}

def _no_data(reason, **inputs):
    d = {"insufficient_data": True, "reason": reason}
    if inputs: d["inputs"] = inputs
    return d


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


def fitness_test_log(a):
    """Compatibility entry point for the extracted training family."""
    out(fitness_commands.fitness_test_log(_command_context(), a))


def fitness_test_void(a):
    """Compatibility entry point for the extracted training family."""
    out(fitness_commands.fitness_test_void(_command_context(), a))


def fitness_tests(a):
    """Compatibility entry point for the extracted training family."""
    out(fitness_commands.fitness_tests(_command_context(), a))


def athletic_target_set(a):
    """Compatibility entry point for the extracted training family."""
    out(fitness_commands.athletic_target_set(_command_context(), a))


def _axis_score(result, target, better):
    return insight_calculations._axis_score(result, target, better)


def athletic_radar(a):
    """Compatibility entry point for the extracted training family."""
    out(fitness_commands.athletic_radar(_command_context(), a))


def _tested_ratios(c):
    return fitness_domain.tested_ratios(c, clock=_now)


def strength_ratios(a):
    """Compatibility entry point for the extracted training family."""
    out(fitness_commands.strength_ratios(_command_context(), a))


def muscle_detail(a):
    """Compatibility entry point for the extracted training family."""
    out(muscles_commands.muscle_detail(_command_context(), a))


def muscle_map(a):
    """Compatibility entry point for the extracted training family."""
    out(muscles_commands.muscle_map(_command_context(), a, quarterly_routines=HEVY_QUARTERLY_ROUTINES, unilateral_titles=HEVY_QUARTERLY_UNILATERAL_TITLES, mobility_norm=MOBILITY_NORM))


def pain_log(a):
    """Compatibility entry point for the extracted training family."""
    out(physio_commands.pain_log(_command_context(), a))


def self_test_log(a):
    """Compatibility entry point for the extracted training family."""
    out(physio_commands.self_test_log(_command_context(), a))


def exercise_trial_log(a):
    """Compatibility entry point for the extracted training family."""
    out(physio_commands.exercise_trial_log(_command_context(), a))


def physio_void(a):
    """Compatibility entry point for the extracted training family."""
    out(physio_commands.physio_void(_command_context(), a))


def import_submuscle_map(a):
    """Compatibility entry point for the extracted import command."""
    out(submuscle_commands.import_map(
        _command_context(), a.md_file, seed=a.seed,
        seed_all=bool(getattr(a, "seed_all", False)), figure_sub_svg=FIGURE_SUB_SVG))


def vtaper(a):
    """Compatibility entry point for the extracted training family."""
    out(fitness_commands.vtaper(_command_context(), a))


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


def _configured_phase_numbers(name):
    return nutrition_domain.configured_phase_numbers(name, environ=os.environ)


CONFIGURED_PHASE_OFFSETS = _configured_phase_numbers("HERMES_PHASE_OFFSETS_JSON")
CONFIGURED_PROTEIN_PER_KG = _configured_phase_numbers("HERMES_PROTEIN_PER_KG_JSON")
if any(value <= 0 for value in CONFIGURED_PROTEIN_PER_KG.values()):
    raise RuntimeError("HERMES_PROTEIN_PER_KG_JSON values must be greater than zero")


def profile_set(a):
    """Compatibility entry point for the extracted nutrition command."""
    out(nutrition_commands.profile_set(_command_context(), a))


def phase_set(a):
    """Compatibility entry point for the extracted nutrition command."""
    out(nutrition_commands.phase_set(_command_context(), a))


# =========================================================== T44 targets engine
# Every physiological constant below carries a source string (MICRO_SEED /
# lab_catalog precedent). This is nutrition PLANNING, not medical advice — no
# demographic targets are assumed here. Phase and protein values are explicit
# installation configuration; the water heuristic is labeled as such.
TARGETS_SEED = nutrition_domain.target_seed(
    phase_offsets=CONFIGURED_PHASE_OFFSETS, protein_per_kg=CONFIGURED_PROTEIN_PER_KG,
    water_ml_per_kg=WATER_ML_PER_KG, water_ml_per_exercise_hour=WATER_ML_PER_EXERCISE_HOUR,
    water_hot_day_bonus_ml=WATER_HOT_DAY_BONUS_ML, water_hot_day_temp_c=WATER_HOT_DAY_TEMP_C,
)


def _nutrition_config():
    return nutrition_domain.NutritionConfig(
        target_seed=TARGETS_SEED, micro_targets=CONFIGURED_MICRO_TARGETS,
        phase_offsets=CONFIGURED_PHASE_OFFSETS, protein_per_kg=CONFIGURED_PROTEIN_PER_KG,
        micro_seed=MICRO_SEED, micro_target_extra=MICRO_TARGET_EXTRA,
        water_target_ml=WATER_TARGET_ML,
    )


def _water_target(c, weight_kg):
    return nutrition_domain.water_target(c, weight_kg, clock=_now, config=_nutrition_config())


def _micro_targets():
    return insight_calculations._micro_targets(
        micro_seed=MICRO_SEED, micro_target_extra=MICRO_TARGET_EXTRA,
    )


def _compute_targets(c):
    return nutrition_domain.compute_targets(c, clock=_now, config=_nutrition_config())


def nutrition_targets(a):
    """Compatibility entry point for the extracted nutrition command."""
    out(nutrition_commands.nutrition_targets(_command_context(), a, config=_nutrition_config()))


def nutrition_coverage(a):
    """Compatibility entry point for the extracted nutrition command."""
    out(nutrition_commands.nutrition_coverage(_command_context(), a, config=_nutrition_config()))


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
    """Compatibility entry point for the extracted daily command."""
    out(daily_capture_commands.supplement_log(_command_context(), a))


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
    """Compatibility entry point for the extracted daily command."""
    out(collector_commands.collector_run_record(_command_context(), a, stdin=sys.stdin))


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
            "lab-capture": lab_capture,
            "lab-ingest": lab_ingest,
            "labs": labs,
            "query": query,
            "bp-brief": bp_brief,
            "summary": summary,
            "schema": schema,
            "build-daily-frame": build_daily_frame,
            "features": features,
            "correlate": correlate,
            "day-signature": day_signature,
            "adherence": adherence,
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
        },
        argv=sys.argv[1:], output=out, parse_number=num,
        meal_types=MEAL_TYPES, restock_actions=RESTOCK_ACTIONS,
        scores_default_days=SCORES_DEFAULT_DAYS,
        quarterly_routines=HEVY_QUARTERLY_ROUTINES,
        quarterly_unilateral_titles=HEVY_QUARTERLY_UNILATERAL_TITLES,
        mobility_norm=MOBILITY_NORM, stdin=sys.stdin,
        slug=slug, figure_sub_svg=FIGURE_SUB_SVG,
        water_target_ml=WATER_TARGET_ML, open_url=_open_collector_url,
        medication_aliases=MEDICATION_ALIASES, nutrition_config=_nutrition_config(),
    )


if __name__ == "__main__":
    main()
