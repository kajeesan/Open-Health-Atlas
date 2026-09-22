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
from hermes_insights import daily_frames as daily_frames_domain
from hermes_insights import scores as scores_domain
from hermes_insights import recovery as recovery_domain
from hermes_insights import labs as labs_domain
from hermes_insights.commands import food as food_commands, nutrition as nutrition_commands
from hermes_insights.commands import daily_frames as daily_frames_commands
from hermes_insights.commands import scores as scores_commands
from hermes_insights.commands import recovery as recovery_commands
from hermes_insights.commands import labs as labs_commands
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
    out(daily_frames_commands.summary(_command_context(), a))

def bp_brief(a):
    out(daily_frames_commands.bp_brief(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))

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

def adherence(a):
    out(daily_frames_commands.adherence(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))

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


PILLARS = daily_frames_domain.PILLARS
FLIPPED = daily_frames_domain.FLIPPED
FEATURE_BASE = daily_frames_domain.FEATURE_BASE
LAG1_FIELDS = daily_frames_domain.LAG1_FIELDS
CONDITION_FIELDS = daily_frames_domain.CONDITION_FIELDS

def _hhmm_min(s):
    return daily_frames_domain._hhmm_min(s)

def _bedtime_min(s):
    return daily_frames_domain._bedtime_min(s)

def _rnd(v, nd=4):
    return daily_frames_domain._rnd(v, nd)

def _daily_frame(c, days):
    return daily_frames_domain._daily_frame(
        c, days, clock=_now, medication_aliases=MEDICATION_ALIASES)

def _meta(cov):
    return daily_frames_domain._meta(cov, TIMEZONE_NAME)

def _sparse(dates, rows):
    return daily_frames_domain._sparse(dates, rows)

def build_daily_frame(a):
    out(daily_frames_commands.build_daily_frame(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))

def _add_features(dates, rows):
    return daily_frames_domain._add_features(dates, rows)

def features(a):
    out(daily_frames_commands.features(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))

def _pillar_of(field):
    return daily_frames_domain._pillar_of(field)

def _pearson(xs, ys):
    return daily_frames_domain._pearson(xs, ys)

def _ranks(vs):
    return daily_frames_domain._ranks(vs)

def _spearman(xs, ys):
    return daily_frames_domain._spearman(xs, ys)

def _strength(rho):
    return daily_frames_domain._strength(rho)

def correlate(a):
    out(daily_frames_commands.correlate(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))

def day_signature(a):
    out(daily_frames_commands.day_signature(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))

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
SLEEP_TARGET_H = scores_domain.SLEEP_TARGET_H
# `scores`'s own --days default — T48's readiness engine reuses the SAME
# baseline window (never a second invented constant) so the two engines'
# RHR/HRV baselines can't drift apart.
SCORES_DEFAULT_DAYS = scores_domain.SCORES_DEFAULT_DAYS


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
    return scores_domain._score(v, **inputs)

def _no_data(reason, **inputs):
    return scores_domain._no_data(reason, **inputs)


def _anchor_day(raw=None):
    return date.fromisoformat(valid_date(raw, "--anchor")) if raw is not None else _now().date()


def _sleep_score(c, anchor=None, *, include_ancestry=False):
    return scores_domain._sleep_score(c, anchor, clock=_now, include_ancestry=include_ancestry)


def _recovery_baseline_rows(c, days, anchor=None, start=None):
    return scores_domain._recovery_baseline_rows(c, days, anchor, start, clock=_now)


def _dev_score(value, baseline_vals, coef, invert):
    return scores_domain._dev_score(value, baseline_vals, coef, invert)


def scores(a):
    out(scores_commands.scores(
        _command_context(), a, nutrition_config=_nutrition_config()))

# --------------------------------------------------------- T48 readiness engine
READINESS_DISCLAIMER = recovery_domain.READINESS_DISCLAIMER
READINESS_EVIDENCE_CONTRACT = recovery_domain.READINESS_EVIDENCE_CONTRACT
READINESS_MINIMUM_SAME_SOURCE_BASELINE = recovery_domain.READINESS_MINIMUM_SAME_SOURCE_BASELINE
READINESS_HRV_DEVIATION_COEFFICIENT = recovery_domain.READINESS_HRV_DEVIATION_COEFFICIENT
READINESS_RHR_DEVIATION_COEFFICIENT = recovery_domain.READINESS_RHR_DEVIATION_COEFFICIENT
READINESS_TRAINING_VOLUME_DAYS = recovery_domain.READINESS_TRAINING_VOLUME_DAYS
READINESS_TRAINING_PERFORMANCE_DAYS = recovery_domain.READINESS_TRAINING_PERFORMANCE_DAYS
SORENESS_SYNONYMS = recovery_domain.SORENESS_SYNONYMS
READINESS_POLICY = recovery_domain.READINESS_POLICY
READINESS_POLICY_SHA256 = recovery_domain.READINESS_POLICY_SHA256

def _readiness_calculation_context():
    return recovery_domain._readiness_calculation_context()


def _muscle_recovery(c, anchor=None, start=None):
    return recovery_domain._muscle_recovery(c, anchor, start, clock=_now)


def _readiness_metric_evidence(base_rows, today_row, key):
    return recovery_domain._readiness_metric_evidence(base_rows, today_row, key)


def _readiness_result(c, *, anchor, range_start, snapshot_attestation):
    return recovery_domain._readiness_result(
        c, anchor=anchor, range_start=range_start,
        snapshot_attestation=snapshot_attestation, clock=_now)


def readiness(a):
    out(recovery_commands.readiness(_command_context(), a))

# --------------------------------------------------------- data-to-add ranker
# What each signal unlocks and how much it costs to capture. `weight` (1-5,
# how much analysis it enables), `effort` (1 = one tap, 2 = semi-automatic,
# 3 = manual work) and `target_pct` (expected coverage at PERFECT adherence —
# a weekly weigh-in tops out at ~14%, a 3x/week cuff at ~40%) are EDITORIAL
# CONSTANTS — priorities, not measurements; the only computed inputs are the
# live coverage percentages.
COVERAGE_CATALOG = daily_frames_domain.COVERAGE_CATALOG

def data_coverage(a):
    out(daily_frames_commands.data_coverage(
        _command_context(), a, medication_aliases=MEDICATION_ALIASES))

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
    return labs_domain._ensure_lab_tables(c)


def import_lab_catalog(a):
    """Compatibility entry point for the extracted import command."""
    out(lab_catalog_commands.import_catalog(
        _command_context(), a.md_file, seed=a.seed))


# ── labs: raw preservation + OCR-text ingestion + read ──────────────────────

def lab_capture(a):
    out(labs_commands.lab_capture(_command_context(), a, stdin=sys.stdin))


def _norm_lab_unit(u):
    return labs_domain._norm_lab_unit(u)


def _lab_report_ref(s):
    return labs_domain._lab_report_ref(s)


def _lab_in_range(value, low, high):
    return labs_domain._lab_in_range(value, low, high)


def _parse_lab_report(text):
    return labs_domain._parse_lab_report(text)


def _lab_catalog_index(c):
    return labs_domain._lab_catalog_index(c)


def _validate_lab_row(row, cat, last_value):
    return labs_domain._validate_lab_row(row, cat, last_value)


def lab_ingest(a):
    out(labs_commands.lab_ingest(_command_context(), a, stdin=sys.stdin))


# A marker's CURRENT status is its single most-recent reading (never an average
# or blend of older ones). A latest reading older than this is 'historical' —
# shown in the view but not read as current, and current suggestions ignore it
# (the recommendation path stays pull-only). configuration rule 2026-07-12.
LABS_STALE_DAYS = labs_domain.LABS_STALE_DAYS


def _lab_age_days(date_str):
    return labs_domain._lab_age_days(date_str, clock=_now)


def labs(a):
    out(labs_commands.labs(_command_context(), a))


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
            "query": query,
            "schema": schema,
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
