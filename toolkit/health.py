#!/usr/bin/env python3
"""
health.py — the stable executable and compatibility facade for a Hermes health database.

The shared CLI parses commands and dispatches to domain owners that validate
inputs, compute deterministic results and perform validated writes.

DB path: $HEALTH_DB or $HERMES_DATA_DIR/health.db
Run `python3 health.py --help` for commands.
"""
import os, re, sys
from datetime import date, datetime

from hermes_insights import cli as insight_cli
from hermes_insights.command_context import CommandContext
from hermes_insights.commands import (
    analytical as analytical_commands, schema as schema_commands,
    events as event_commands, ledger as ledger_commands,
    orchestration as orchestration_commands, scheduled_analysis,
)
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
from hermes_insights import migrations as insight_migrations
from hermes_insights.contracts import canonical_json
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
SAFE = schema_commands.SAFE
def query(a):
    out(schema_commands.query(_command_context(), a))

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
    out(schema_commands.schema(_command_context(), a))

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


# =========================================================== insight compatibility
# Domain modules own deterministic calculations; these aliases preserve the
# established Python seams while the CLI delegates directly to those owners.


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
    out(event_commands._write_insight(_command_context(), fn, *args, **kwargs))


def _stdin_json():
    return event_commands._stdin_json(sys.stdin)


def supplement_log(a):
    """Compatibility entry point for the extracted daily command."""
    out(daily_capture_commands.supplement_log(_command_context(), a))


# =========================================================== Phase 3 feature integration
def _phase3_schema_ready():
    return analytical_commands.require_phase3_schema(_command_context())


def _phase4_schema_ready():
    return insight_runtime.require_analytical_schema(DB)


def _phase3_context():
    # Resolve the compatibility bindings now, including replaced catalogs and
    # the wrappers that keep nested date-sensitive calculations on this clock.
    return insight_runtime.adapter_context(clock=_now, bindings=globals())


_phase3_range = analytical_commands.requested_range


_phase3_definitions = analytical_commands.definitions


# =========================================================== Phase 5 ledger/synthesis
_PHASE5_BASE_OUTCOMES = ledger_commands._PHASE5_BASE_OUTCOMES
_PHASE5_ANNOTATION_KEYS = ledger_commands._PHASE5_ANNOTATION_KEYS


def _phase5_schema_ready():
    return analytical_commands.require_phase5_schema(_command_context())


_phase5_range_record = ledger_commands.range_record


def _phase5_anchor(requested, explicit=None):
    return ledger_commands.anchor(_command_context(), requested, explicit)


_phase5_modes = ledger_commands.outcome_modes


_phase5_selected_outcomes = ledger_commands.selected_outcomes


def _phase5_compute_manual(requested, explicit_outcomes, explicit_mode=None):
    return ledger_commands.compute_manual(
        _command_context(), requested, explicit_outcomes, explicit_mode,
        adapter_context=_phase3_context(),
    )


_phase5_input_bound_initiator = ledger_commands.input_bound_initiator


def _phase6_prepare_batch(kind, plan, trigger):
    return scheduled_analysis.prepare_batch(
        _command_context(), kind, plan, trigger, adapter_context=_phase3_context(),
    )


def _phase6_compute_runs(prepared, plan):
    return scheduled_analysis.compute_runs(_command_context(), prepared, plan)


_phase6_refs_and_novelty = scheduled_analysis.refs_and_novelty


_phase6_record_no_message = scheduled_analysis.record_no_message


def _phase6_finalize(prepared, plan, kind, trigger, trigger_payload):
    return scheduled_analysis.finalize(
        _command_context(), prepared, plan, kind, trigger, trigger_payload,
    )


def _phase6_analysis_refresh(a):
    print(canonical_json(scheduled_analysis.analysis_refresh(
        _command_context(), a, stdin=sys.stdin,
    )))


def _phase5_annotation_payload():
    return ledger_commands.annotation_payload(sys.stdin)


# =========================================================== Phase 6 orchestration
def _orchestration_payload():
    return orchestration_commands.payload(sys.stdin)


def _orchestration_write(fn, *args):
    print(canonical_json(orchestration_commands.write(_command_context(), fn, *args)))


# =========================================================== cli
def main():
    """Launch the shared CLI with explicit command configuration."""
    insight_cli.run(
        _command_context(),
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
