"""Explicit command registration, parsing, dispatch and CLI error output."""

import argparse
from functools import partial
import json
import sqlite3

from . import associations as insight_associations
from . import events as insight_events
from . import frame as insight_frame
from . import goals as insight_goals
from . import ledger as insight_ledger
from . import migrations as insight_migrations
from . import orchestrator as insight_orchestrator
from . import provenance as insight_provenance
from . import readiness as insight_readiness
from . import readiness_ancestry as insight_readiness_ancestry
from . import registry as insight_registry
from . import synthesis as insight_synthesis
from .catalogs import PRIMARY_MEDICATION
from .commands import (
    collectors, cronometer, daily_capture, followthrough, google_health, hevy,
    food, nutrition, lab_catalog, notes, recipes, schedules, submuscle_map,
    training, fitness, muscles, physio,
)
from .commands.hevy import import_csv


def out(payload):
    """Write the established JSON confirmation and error representation."""
    print(json.dumps(payload, ensure_ascii=False, default=str))


JSON_COMMANDS = {
    "analysis-job-start", "analysis-job-status", "analysis-job-work", "analysis-job-execute",
    "schema-status", "schema-plan", "migrate", "capture-raw", "capture-resolve",
    "event-log", "event-correct", "event-void", "events",
    "capture-completeness-set", "capture-completeness", "entity-alias-set",
    "entity-alias-retire", "entity-alias-history", "supplement-log",
    "feature-registry", "feature-frame", "data-readiness", "goal-list",
    "goal-set", "collector-run-record", "outcome-associations",
    "finding-evidence", "analysis-refresh", "hypothesis-promote",
    "hypothesis-refresh", "hypothesis-annotate", "hypotheses",
    "hypothesis-brief", "synthesis-prepare", "synthesis-record",
    "synthesis-history",
    "insight-trigger-enqueue", "insight-trigger-claim",
    "insight-trigger-renew", "insight-trigger-complete",
    "insight-trigger-fail", "insight-notification-claim",
    "insight-notification-begin-dispatch", "insight-notification-ack",
    "insight-notification-fail", "insight-notification-resolve",
    "insight-run-status",
}
STRICT_REPEAT_COMMANDS = JSON_COMMANDS | {"eat", "log-food", "pain-log"}
REPEATABLE_FLAGS = {"analysis-refresh": {"--outcome"}}


class HealthArgumentParser(argparse.ArgumentParser):
    """Keep legacy argparse text while structured commands return JSON errors."""

    def __init__(self, *args, json_errors=False, output=out, **kwargs):
        super().__init__(*args, **kwargs)
        self.json_errors = json_errors
        self.output = output

    def error(self, message):
        if self.json_errors:
            self.output({"ok": False, "error": {"code": "validation_error", "message": message}})
            raise SystemExit(2)
        super().error(message)


def build_parser(handlers, *, json_errors, output, meal_types, restock_actions, scores_default_days):
    """Register every retained command with explicit caller-supplied handlers."""
    parser_type = partial(HealthArgumentParser, json_errors=json_errors, output=output)
    p = parser_type(description="Deterministic health DB toolkit")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=parser_type)
    def add(name, **kw):
        command = sub.add_parser(name, **kw)
        command.set_defaults(fn=handlers[name])
        return command

    add("analysis-job-start", allow_abbrev=False)
    s = add("analysis-job-status", allow_abbrev=False)
    s.add_argument("job_id")
    # Worker/execution are local toolkit internals, never bridge-allowlisted.
    add("analysis-job-work", allow_abbrev=False)
    s = add("analysis-job-execute", allow_abbrev=False)
    s.add_argument("job_id"); s.add_argument("attempt")

    s = add("import-hevy"); s.add_argument("csv")
    s = add("import-hevy-json"); s.add_argument("json_file"); s.add_argument("--force", action="store_true")
    s = add("import-hevy-body"); s.add_argument("json_file")
    s = add("import-hevy-templates"); s.add_argument("json_file")
    s = add("import-hevy-routines"); s.add_argument("json_file")
    s = add("today"); s.add_argument("--date")
    s = add("muscle-volume"); s.add_argument("--source", choices=["planned","logged"], default=None); s.add_argument("--days", type=int, default=7); s.add_argument("--by", choices=["group"])
    s = add("last-session"); s.add_argument("exercise")
    s = add("log-set"); s.add_argument("exercise")
    s.add_argument("--weight", type=float); s.add_argument("--reps", type=int)
    s.add_argument("--rpe", type=float); s.add_argument("--set-index", dest="set_index", type=int)
    s.add_argument("--date"); s.add_argument("--workout-title", dest="workout_title")
    s.add_argument("--set-type", dest="set_type")
    s = add("routine-set"); s.add_argument("routine"); s.add_argument("exercise")
    s.add_argument("--sets", type=int); s.add_argument("--reps", type=int)
    s.add_argument("--weight", type=float); s.add_argument("--order", type=int)
    s = add("routine-remove"); s.add_argument("routine"); s.add_argument("exercise")
    s = add("routine-undo")
    s = add("schedule-set"); s.add_argument("weekday"); s.add_argument("routine")
    # §3e fitness_tests capture + §3c/§3d engines
    s = add("fitness-test-log"); s.add_argument("movement")
    s.add_argument("--side"); s.add_argument("--load", type=float); s.add_argument("--reps", type=int)
    s.add_argument("--seconds", type=float); s.add_argument("--rating", type=int)
    s.add_argument("--cm", type=float)
    s.add_argument("--degrees", type=float); s.add_argument("--passed", type=int)   # Phase 4 mobility (rom/binary)
    s.add_argument("--note"); s.add_argument("--date")
    s.add_argument("--source")
    s = add("fitness-tests"); s.add_argument("--days", type=int, default=120)
    s = add("fitness-test-void"); s.add_argument("id", type=int); s.add_argument("--reason")
    s = add("athletic-radar")
    s = add("athletic-target-set"); s.add_argument("axis")
    s.add_argument("--target", type=float, required=True); s.add_argument("--lift", default="")
    s = add("strength-ratios"); s.add_argument("--view", choices=["tested", "everyday"], default="tested")
    s = add("vtaper"); s.add_argument("--days", type=int, default=365)
    s = add("muscle-detail"); s.add_argument("group"); s.add_argument("--days", type=int, default=7)
    s = add("muscle-map"); s.add_argument("--lens", choices=["activation", "strength-balance", "pain", "mobility"], default="activation"); s.add_argument("--days", type=int, default=None); s.add_argument("--side-mode", choices=["combined", "lr"], default="combined")
    # §3g physio capture — collector/agent-only writers (NOT in either bridge
    # allowlist; a drift test pins them out). Reads go through muscle-map --lens pain.
    s = add("pain-log"); s.add_argument("region")
    s.add_argument("--intensity", type=int); s.add_argument("--side")
    s.add_argument("--quality"); s.add_argument("--pattern"); s.add_argument("--flags")
    s.add_argument("--note"); s.add_argument("--date"); s.add_argument("--source")
    s.add_argument("--reported-onset-date", dest="reported_onset_date")
    s.add_argument("--onset-precision", dest="onset_precision",
                   choices=["exact", "approximate", "unknown"])
    s.add_argument("--capture-id", dest="capture_id")
    s = add("self-test-log"); s.add_argument("test")
    s.add_argument("--result"); s.add_argument("--side"); s.add_argument("--note")
    s.add_argument("--date"); s.add_argument("--source")
    s = add("exercise-trial-log"); s.add_argument("drill")
    s.add_argument("--response"); s.add_argument("--target"); s.add_argument("--dose")
    s.add_argument("--pain-during", dest="pain_during", type=int)
    s.add_argument("--note"); s.add_argument("--date"); s.add_argument("--source")
    s = add("physio-void"); s.add_argument("--kind"); s.add_argument("id", type=int)
    s.add_argument("--reason")
    s = add("import-submuscle-map"); s.add_argument("md_file"); s.add_argument("--seed", action="store_true"); s.add_argument("--seed-all", action="store_true")
    s = add("import-lab-catalog"); s.add_argument("md_file"); s.add_argument("--seed", action="store_true")
    s = add("lab-capture"); s.add_argument("name")
    s = add("lab-ingest"); s.add_argument("--date"); s.add_argument("--panel")
    s.add_argument("--commit", action="store_true"); s.add_argument("--confirm", action="append")
    s = add("labs"); s.add_argument("--days", type=int, default=None)
    s.add_argument("--test"); s.add_argument("--panel")
    s = add("write-note"); s.add_argument("path")
    s = add("journal-capture"); s.add_argument("--date"); s.add_argument("--time")
    s = add("transcript-capture"); s.add_argument("audio")
    s = add("import-recipes"); s.add_argument("csv")
    s.add_argument("--per-serving", dest="per_serving", action="store_true")
    s.add_argument("--per-gram", dest="per_gram", action="store_true")
    s.add_argument("--servings", type=int, default=None)
    s.add_argument("--batch-grams", dest="batch_grams", type=float, default=None)
    s.add_argument("--portions", type=int, default=None)
    s.add_argument("--meal-type", dest="meal_type", choices=meal_types, default=None)
    s = add("recipe-ingredients-set"); s.add_argument("recipe")
    s = add("import-cronometer"); s.add_argument("csv")
    s = add("import-google-health"); s.add_argument("json_file", nargs="?", default="-")
    s = add("nutrition-target-set"); s.add_argument("name")
    s.add_argument("--target", type=float, required=True)
    s = add("profile-set"); s.add_argument("key"); s.add_argument("value")
    s = add("phase-set"); s.add_argument("phase")
    s = add("nutrition-targets")
    s = add("nutrition-coverage")
    s.add_argument("--days", type=int, default=7, choices=[7, 30, 90, 365])
    s = add("set-batch"); s.add_argument("recipe"); s.add_argument("--grams", type=float, required=True)
    s.add_argument("--portions", type=int, default=None)
    s = add("recipe-tag"); s.add_argument("recipe"); s.add_argument("meal_type")
    s = add("prep"); s.add_argument("recipe"); s.add_argument("--portions", type=int, required=True); s.add_argument("--batch-grams", dest="batch_grams", type=float)
    s = add("eat"); s.add_argument("recipe"); s.add_argument("--portions", type=int, default=1); s.add_argument("--date")
    s.add_argument("--time"); s.add_argument("--meal-type", dest="meal_type"); s.add_argument("--source")
    s = add("log-food"); s.add_argument("recipe"); s.add_argument("--grams", type=float, required=True); s.add_argument("--date")
    s.add_argument("--time"); s.add_argument("--meal-type", dest="meal_type"); s.add_argument("--source")
    s = add("menu"); s.add_argument("--date")
    s = add("restock-check")
    s.add_argument("--threshold", type=float, default=2); s.add_argument("--date")
    s = add("restock-mark"); s.add_argument("recipe")
    s.add_argument("--action", required=True, choices=sorted(restock_actions))
    s.add_argument("--threshold", type=float, default=2)
    s.add_argument("--snooze-until"); s.add_argument("--date")
    s = add("log"); s.add_argument("table"); s.add_argument("fields", nargs="+")
    s = add("day-rating"); s.add_argument("rating"); s.add_argument("--date"); s.add_argument("--source")
    s = add("water-add"); s.add_argument("ml", type=int); s.add_argument("--date")
    s = add("query"); s.add_argument("sql")
    s = add("bp-brief"); s.add_argument("--days", type=int, default=90); s.add_argument("--drug", default=PRIMARY_MEDICATION)
    s = add("summary"); s.add_argument("--days", type=int, default=14)
    s = add("schema"); s.add_argument("table", nargs="?")
    s = add("build-daily-frame"); s.add_argument("--days", type=int, default=180)
    s = add("features"); s.add_argument("--days", type=int, default=180)
    s = add("correlate"); s.add_argument("--days", type=int, default=365)
    s.add_argument("--min-n", dest="min_n", type=int, default=10)
    s.add_argument("--top", type=int, default=40)
    s = add("day-signature"); s.add_argument("--days", type=int, default=365)
    s.add_argument("--min-days", dest="min_days", type=int, default=3)
    s = add("commitment-set"); s.add_argument("name")
    s.add_argument("--identity"); s.add_argument("--trigger"); s.add_argument("--floor")
    s.add_argument("--reward"); s.add_argument("--active", type=int)
    s = add("commitment-list"); s.add_argument("--all", action="store_true")
    s = add("log-commitment"); s.add_argument("status")
    s.add_argument("--id", type=int); s.add_argument("--why"); s.add_argument("--date"); s.add_argument("--source")
    s = add("checkin"); s.add_argument("kind"); s.add_argument("value", type=int)
    s.add_argument("--time"); s.add_argument("--note"); s.add_argument("--date"); s.add_argument("--source")
    s = add("feedback-status"); s.add_argument("--date", required=True)
    s = add("adherence"); s.add_argument("--days", type=int, default=90)
    s = add("planned-time-set"); s.add_argument("metric"); s.add_argument("time")
    s.add_argument("--tolerance", type=int)
    s = add("timing-adherence"); s.add_argument("--days", type=int, default=28)
    s = add("data-coverage"); s.add_argument("--days", type=int, default=90)
    s = add("scores"); s.add_argument("--days", type=int, default=scores_default_days)
    s = add("readiness"); s.add_argument("--anchor")
    s.add_argument("--from", dest="from_date")
    # Phase 2 explicit migrations and agent/SSH-only lossless capture.
    s = add("schema-status")
    s = add("schema-plan"); s.add_argument("--to", type=int, required=True)
    s = add("migrate"); s.add_argument("--to", type=int, required=True)
    s.add_argument("--expected-from", dest="expected_from", type=int, required=True)
    s = add("capture-raw"); s.add_argument("--stdin", action="store_true", required=True)
    s = add("capture-resolve"); s.add_argument("--stdin", action="store_true", required=True)
    s = add("event-log"); s.add_argument("--stdin", action="store_true", required=True)
    s = add("event-correct"); s.add_argument("id", type=int)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("event-void"); s.add_argument("id", type=int)
    s.add_argument("--reason", required=True)
    s = add("events")
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    s.add_argument("--category"); s.add_argument("--entity-key", dest="entity_key")
    s = add("capture-completeness-set")
    s.add_argument("--date", required=True); s.add_argument("--scope", required=True)
    s.add_argument("--state", required=True); s.add_argument("--explicit-none", dest="explicit_none", action="store_true")
    s.add_argument("--entity-key", dest="entity_key"); s.add_argument("--source", required=True)
    s.add_argument("--capture-id", dest="capture_id"); s.add_argument("--note")
    s = add("capture-completeness")
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    s.add_argument("--scope")
    s = add("entity-alias-set"); s.add_argument("type"); s.add_argument("alias")
    s.add_argument("--canonical", required=True); s.add_argument("--label", required=True)
    s = add("entity-alias-retire"); s.add_argument("type"); s.add_argument("alias")
    s = add("entity-alias-history"); s.add_argument("type"); s.add_argument("alias")
    s = add("supplement-log"); s.add_argument("supplement")
    s.add_argument("--taken", type=int, choices=[0, 1]); s.add_argument("--dose", type=float)
    s.add_argument("--time"); s.add_argument("--date"); s.add_argument("--note")
    s.add_argument("--capture-id", dest="capture_id"); s.add_argument("--source", required=True)
    # Phase 3 registry/frame/readiness reads plus agent/SSH-only config writers.
    s = add("feature-registry")
    s.add_argument("--family")
    s = add("feature-frame")
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    s.add_argument("--family"); s.add_argument("--include-provenance", dest="include_provenance", action="store_true")
    s = add("data-readiness")
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    s.add_argument("--goal", choices=insight_goals.GOAL_KEYS); s.add_argument("--outcome")
    # Phase 4 pure analytical reads.  Abbreviated long flags are refused on
    # these new surfaces without changing legacy argparse compatibility.
    s = add("outcome-associations", allow_abbrev=False)
    s.add_argument("--outcome", required=True)
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    s.add_argument("--mode", choices=insight_associations.MODE_VALUES, default="all")
    s.add_argument("--min-n", dest="min_n", type=int, default=insight_associations.DEFAULT_MIN_N)
    s.add_argument("--interactions", choices=("none", "pairwise"), default="none")
    s.add_argument("--top", type=int, default=insight_associations.DEFAULT_TOP)
    s = add("finding-evidence", allow_abbrev=False)
    s.add_argument("--outcome", required=True)
    s.add_argument("--finding-id", dest="finding_id", required=True)
    s.add_argument("--input-fingerprint", dest="input_fingerprint", required=True)
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    # Phase 5 canonical ledger/synthesis surface. Only identifier-only
    # hypothesis promotion and the three structured reads enter the bridge.
    # Every other command here remains local agent/SSH-only.
    s = add("analysis-refresh", allow_abbrev=False)
    s.add_argument(
        "--kind",
        choices=("manual", "nightly", "weekly", "monthly", "trigger"),
        required=True,
    )
    s.add_argument("--outcome", action="append")
    s.add_argument("--mode", choices=insight_associations.MODE_VALUES)
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    s.add_argument("--anchor")
    s.add_argument("--stdin", action="store_true")
    s = add("hypothesis-promote", allow_abbrev=False)
    s.add_argument("--outcome", required=True)
    s.add_argument("--finding-id", dest="finding_id", required=True)
    s.add_argument("--input-fingerprint", dest="input_fingerprint", required=True)
    s.add_argument("--from", dest="from_date"); s.add_argument("--to", dest="to_date")
    s.add_argument("--days", type=int); s.add_argument("--all", dest="all_dates", action="store_true")
    s = add("hypothesis-refresh", allow_abbrev=False)
    s.add_argument("--batch-id", dest="batch_id", required=True)
    s = add("hypothesis-annotate", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("hypotheses", allow_abbrev=False)
    s.add_argument("--status", choices=sorted(insight_ledger.HYPOTHESIS_STATUSES))
    s.add_argument("--outcome")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--before")
    s = add("hypothesis-brief", allow_abbrev=False)
    s.add_argument("hypothesis_id")
    s = add("synthesis-prepare", allow_abbrev=False)
    s.add_argument("--batch-id", dest="batch_id", required=True)
    s = add("synthesis-record", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("synthesis-history", allow_abbrev=False)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--before")
    # Phase 6 queue/outbox commands remain scheduler/agent-only.  The sole
    # panel-exposed command is the read-only, redacted run-status view.
    s = add("insight-trigger-enqueue", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("insight-trigger-claim", allow_abbrev=False)
    s.add_argument("--worker-id", dest="worker_id", required=True)
    s = add("insight-trigger-renew", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("insight-trigger-complete", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("insight-trigger-fail", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("insight-notification-claim", allow_abbrev=False)
    s.add_argument("--worker-id", dest="worker_id", required=True)
    s = add(
        "insight-notification-begin-dispatch",
        allow_abbrev=False,
    )
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("insight-notification-ack", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("insight-notification-fail", allow_abbrev=False)
    s.add_argument("--stdin", action="store_true", required=True)
    s = add(
        "insight-notification-resolve",
        allow_abbrev=False,
    )
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("insight-run-status", allow_abbrev=False)
    s.add_argument("--limit", type=int, default=20)
    s = add("goal-list"); s.add_argument("--all", dest="all_goals", action="store_true")
    s = add("goal-set"); s.add_argument("goal", choices=insight_goals.GOAL_KEYS)
    s.add_argument("--enabled", type=int, choices=[0, 1], required=True)
    s.add_argument("--priority", type=int, choices=range(1, 6), required=True)
    s.add_argument("--outcome"); s.add_argument("--direction", choices=["increase", "decrease", "maintain"])
    s.add_argument("--note"); s.add_argument("--source", required=True)
    s = add("collector-run-record")
    s.add_argument("--stdin", action="store_true", required=True)
    s = add("fetch-weather"); s.add_argument("--lat", required=True); s.add_argument("--lon", required=True); s.add_argument("--location", default=""); s.add_argument("--date")
    s = add("fetch-air"); s.add_argument("--lat", required=True); s.add_argument("--lon", required=True); s.add_argument("--location", default=""); s.add_argument("--date")

    return p


def run(context, legacy_handlers, *, argv, output, parse_number,
        meal_types, restock_actions, scores_default_days,
        quarterly_routines, quarterly_unilateral_titles, mobility_norm,
        stdin, slug, figure_sub_svg, water_target_ml, open_url,
        medication_aliases, nutrition_config):
    """Dispatch converted commands and explicitly wired compatibility handlers."""
    def hevy_csv(arguments):
        output(import_csv(context, arguments.csv, parse_number=parse_number))

    def hevy_json(arguments):
        output(hevy.import_json(
            context, arguments.json_file, force=arguments.force,
            parse_number=parse_number, quarterly_routines=quarterly_routines,
        ))

    def hevy_templates(arguments):
        output(hevy.import_templates(context, arguments.json_file))

    def hevy_routines(arguments):
        output(hevy.import_routines(
            context, arguments.json_file, parse_number=parse_number,
        ))

    def hevy_body(arguments):
        output(hevy.import_body(
            context, arguments.json_file, parse_number=parse_number,
        ))

    def cronometer_csv(arguments):
        output(cronometer.import_csv(
            context, arguments.csv, parse_number=parse_number,
        ))

    def google_health_json(arguments):
        output(google_health.import_json(
            context, arguments.json_file, parse_number=parse_number, stdin=stdin,
        ))

    def recipes_csv(arguments):
        output(recipes.import_csv(
            context, arguments, parse_number=parse_number, slug=slug,
            meal_types=meal_types,
        ))

    def submuscle_catalog(arguments):
        output(submuscle_map.import_map(
            context, arguments.md_file, seed=arguments.seed,
            seed_all=arguments.seed_all, figure_sub_svg=figure_sub_svg,
        ))

    def laboratory_catalog(arguments):
        output(lab_catalog.import_catalog(
            context, arguments.md_file, seed=arguments.seed,
        ))

    def log(arguments):
        output(daily_capture.log(context, arguments))

    def day_rating(arguments):
        output(daily_capture.day_rating(context, arguments))

    def water_add(arguments):
        output(daily_capture.water_add(context, arguments, water_target_ml=water_target_ml))

    def supplement_log(arguments):
        output(daily_capture.supplement_log(context, arguments))

    def commitment_set(arguments):
        output(followthrough.commitment_set(context, arguments))

    def commitment_list(arguments):
        output(followthrough.commitment_list(context, arguments))

    def log_commitment(arguments):
        output(followthrough.log_commitment(context, arguments))

    def checkin(arguments):
        output(followthrough.checkin(context, arguments))

    def feedback_status(arguments):
        output(followthrough.feedback_status(context, arguments, output=output))

    def schedule_set(arguments):
        output(schedules.schedule_set(context, arguments))

    def planned_time_set(arguments):
        output(schedules.planned_time_set(context, arguments))

    def timing_adherence(arguments):
        output(schedules.timing_adherence(context, arguments,
                                         medication_aliases=medication_aliases))

    def write_note(arguments):
        output(notes.write_note(context, arguments, stdin=stdin))

    def journal_capture(arguments):
        output(notes.journal_capture(context, arguments, stdin=stdin))

    def transcript_capture(arguments):
        output(notes.transcript_capture(context, arguments, stdin=stdin))

    def fetch_weather(arguments):
        collectors.fetch_weather(context, arguments, open_url=open_url, output=output)

    def fetch_air(arguments):
        collectors.fetch_air(context, arguments, open_url=open_url, output=output)

    def collector_run_record_cmd(arguments):
        output(collectors.collector_run_record(context, arguments, stdin=stdin))

    def set_batch(arguments):
        output(food.set_batch(context, arguments))

    def recipe_tag(arguments):
        output(food.recipe_tag(context, arguments))

    def recipe_ingredients_set(arguments):
        output(food.recipe_ingredients_set(context, arguments, stdin=stdin))

    def prep(arguments):
        output(food.prep(context, arguments))

    def eat(arguments):
        output(food.eat(context, arguments))

    def log_food(arguments):
        output(food.log_food(context, arguments))

    def menu(arguments):
        output(food.menu(context, arguments))

    def restock_check(arguments):
        output(food.restock_check(context, arguments))

    def restock_mark(arguments):
        output(food.restock_mark(context, arguments))

    def profile_set(arguments):
        output(nutrition.profile_set(context, arguments))

    def phase_set(arguments):
        output(nutrition.phase_set(context, arguments))

    def nutrition_target_set(arguments):
        output(nutrition.nutrition_target_set(context, arguments))

    def nutrition_targets(arguments):
        output(nutrition.nutrition_targets(context, arguments, config=nutrition_config))

    def nutrition_coverage(arguments):
        output(nutrition.nutrition_coverage(context, arguments, config=nutrition_config))

    def log_set(arguments):
        output(training.log_set(context, arguments, parse_number=parse_number))

    def today_session(arguments):
        output(training.today_session(context, arguments))

    def last_session(arguments):
        output(training.last_session(context, arguments))

    def routine_set(arguments):
        output(training.routine_set(context, arguments, parse_number=parse_number))

    def routine_remove(arguments):
        output(training.routine_remove(context, arguments))

    def routine_undo(arguments):
        output(training.routine_undo(context, arguments))

    def fitness_test_log(arguments):
        output(fitness.fitness_test_log(context, arguments))

    def fitness_test_void(arguments):
        output(fitness.fitness_test_void(context, arguments))

    def fitness_tests(arguments):
        output(fitness.fitness_tests(context, arguments))

    def athletic_target_set(arguments):
        output(fitness.athletic_target_set(context, arguments))

    def athletic_radar(arguments):
        output(fitness.athletic_radar(context, arguments))

    def strength_ratios(arguments):
        output(fitness.strength_ratios(context, arguments))

    def vtaper(arguments):
        output(fitness.vtaper(context, arguments))

    def muscle_volume(arguments):
        output(muscles.muscle_volume(context, arguments))

    def muscle_detail(arguments):
        output(muscles.muscle_detail(context, arguments))

    def muscle_map(arguments):
        output(muscles.muscle_map(context, arguments, quarterly_routines=quarterly_routines, unilateral_titles=quarterly_unilateral_titles, mobility_norm=mobility_norm))

    def pain_log(arguments):
        output(physio.pain_log(context, arguments))

    def self_test_log(arguments):
        output(physio.self_test_log(context, arguments))

    def exercise_trial_log(arguments):
        output(physio.exercise_trial_log(context, arguments))

    def physio_void(arguments):
        output(physio.physio_void(context, arguments))

    handlers = {
        **legacy_handlers,
        "log-set": log_set,
        "today": today_session,
        "last-session": last_session,
        "routine-set": routine_set,
        "routine-remove": routine_remove,
        "routine-undo": routine_undo,
        "fitness-test-log": fitness_test_log,
        "fitness-test-void": fitness_test_void,
        "fitness-tests": fitness_tests,
        "athletic-target-set": athletic_target_set,
        "athletic-radar": athletic_radar,
        "strength-ratios": strength_ratios,
        "vtaper": vtaper,
        "muscle-volume": muscle_volume,
        "muscle-detail": muscle_detail,
        "muscle-map": muscle_map,
        "pain-log": pain_log,
        "self-test-log": self_test_log,
        "exercise-trial-log": exercise_trial_log,
        "physio-void": physio_void,
        "set-batch": set_batch,
        "recipe-tag": recipe_tag,
        "recipe-ingredients-set": recipe_ingredients_set,
        "prep": prep,
        "eat": eat,
        "log-food": log_food,
        "menu": menu,
        "restock-check": restock_check,
        "restock-mark": restock_mark,
        "profile-set": profile_set,
        "phase-set": phase_set,
        "nutrition-target-set": nutrition_target_set,
        "nutrition-targets": nutrition_targets,
        "nutrition-coverage": nutrition_coverage,
        "import-hevy": hevy_csv,
        "import-hevy-json": hevy_json,
        "import-hevy-templates": hevy_templates,
        "import-hevy-routines": hevy_routines,
        "import-hevy-body": hevy_body,
        "import-cronometer": cronometer_csv,
        "import-google-health": google_health_json,
        "import-recipes": recipes_csv,
        "import-submuscle-map": submuscle_catalog,
        "import-lab-catalog": laboratory_catalog,
        "log": log,
        "day-rating": day_rating,
        "water-add": water_add,
        "supplement-log": supplement_log,
        "commitment-set": commitment_set,
        "commitment-list": commitment_list,
        "log-commitment": log_commitment,
        "checkin": checkin,
        "feedback-status": feedback_status,
        "schedule-set": schedule_set,
        "planned-time-set": planned_time_set,
        "timing-adherence": timing_adherence,
        "write-note": write_note,
        "journal-capture": journal_capture,
        "transcript-capture": transcript_capture,
        "fetch-weather": fetch_weather,
        "fetch-air": fetch_air,
        "collector-run-record": collector_run_record_cmd,
    }
    p = build_parser(
        handlers, json_errors=bool(argv and argv[0] in JSON_COMMANDS), output=output,
        meal_types=meal_types, restock_actions=restock_actions,
        scores_default_days=scores_default_days,
    )
    if argv and argv[0] in STRICT_REPEAT_COMMANDS:
        seen_flags = set()
        repeatable = REPEATABLE_FLAGS.get(argv[0], set())
        for token in argv[1:]:
            if not token.startswith("--"):
                continue
            flag = token.split("=", 1)[0]
            if flag in seen_flags and flag not in repeatable:
                p.error(f"repeated flag is not allowed: {flag}")
            seen_flags.add(flag)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except (insight_migrations.SchemaError, insight_events.CaptureError,
            insight_frame.FrameError, insight_goals.GoalError,
            insight_readiness.ReadinessError,
            insight_readiness_ancestry.ReadinessAncestryError,
            insight_registry.RegistryError,
            insight_associations.AssociationError,
            insight_provenance.ProvenanceError,
            insight_ledger.LedgerError,
            insight_orchestrator.OrchestrationError,
            insight_synthesis.SynthesisError) as exc:
        output({"ok": False, "error": {"code": exc.code, "message": str(exc)}})
        raise SystemExit(2 if exc.validation else 1)
    except sqlite3.Error as exc:
        if argv and argv[0] in JSON_COMMANDS:
            output({"ok": False, "error": {"code": "database_error", "message": str(exc)}})
            raise SystemExit(1)
        raise
