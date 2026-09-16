"""Code-owned command and flag declarations for both bridge processes.

The source-tree panel imports this module as deploy.bridge_commands. The broker
imports the adjacent installed copy, which must stay root-owned and read-only
alongside its executable. This module contains declarations only: the broker
owns privileged validation, profiles, execution limits and audit handling.
"""

# Positional values and command semantics remain validated by health.py or
# hermesctl. Commands intentionally absent here remain unavailable to the panel.
COMMAND_FLAGS = {
    "today":         {"--date"},
    "summary":       {"--days"},
    "schema":        set(),
    "query":         set(),
    "menu":          {"--date"},
    "last-session":  set(),
    "muscle-volume": {"--source", "--days", "--by"},
    "bp-brief":      {"--days", "--drug"},
    "day-rating":    {"--date", "--source"},
    "log-set":       {"--weight", "--reps", "--rpe", "--set-index", "--date",
                      "--workout-title", "--set-type"},
    "routine-set":   {"--sets", "--reps", "--weight", "--order"},
    "routine-remove": set(),
    "routine-undo":  set(),
    "schedule-set":  set(),
    "write-note":    set(),   # content arrives on stdin, not as flags
    "log":           set(),
    # insight engine: read-only statistics computed in health.py (no writes)
    "build-daily-frame": {"--days"},
    "features":          {"--days"},
    "adherence":         {"--days"},
    "data-coverage":     {"--days"},
    "scores":            {"--days"},
    # follow-through capture (validated writes, upsert — nothing deletes)
    "commitment-set":  {"--identity", "--trigger", "--floor", "--reward", "--active"},
    "commitment-list": {"--all"},
    "log-commitment":  {"--id", "--why", "--date", "--source"},
    "checkin":         {"--time", "--note", "--date", "--source"},
    "eat":           {"--portions", "--date", "--time", "--meal-type", "--source"},
    "log-food":      {"--grams", "--date", "--time", "--meal-type", "--source"},
    "set-batch":     {"--grams"},
    "prep":          {"--portions", "--batch-grams"},
    # §3e/§3c/§3d — READS ONLY. Capture is Telegram-only and the panel is
    # analysis-only; the writes (fitness-test-log, athletic-target-set,
    # fitness-test-void) go through the agent's own health.py path / SSH, never
    # the sandboxed panel — minimal attack surface.
    "fitness-tests":       {"--days"},
    "athletic-radar":      set(),
    "strength-ratios":     {"--view"},
    # §3f V-taper — read-only (import-hevy-body is collector-only, NOT here)
    "vtaper":              {"--days"},
    # §3b drill-down — read-only (map seeding is SSH/agent-path, NOT here)
    "muscle-detail":       {"--days"},
    # §3f muscle figure — read-only display payload (map authored in health.py;
    # --side-mode is the Phase-2 strength-balance Combined|L/R sub-view)
    "muscle-map":          {"--lens", "--days", "--side-mode"},
    # §4b timing adherence — read-only (planned-time-set is NOT here)
    "timing-adherence":    {"--days"},
    # §labs bloodwork — read-only view feed (capture, OCR ingestion and the
    # cited catalog seed are collector-only, NOT here)
    "labs":                {"--days", "--test", "--panel"},
    # T44 targets engine — read-only compute, zero flags (profile-set /
    # phase-set / nutrition-target-set are owner config, NOT here)
    "nutrition-targets":   set(),
    # T46 target-coverage score — read-only, shares the T44 targets engine;
    # value whitelist (7/30/90/365) is enforced by health.py's own argparse
    # choices, this flag whitelist is defense in depth
    "nutrition-coverage":  {"--days"},
    # T48 readiness engine — read-only. The fixed range is server-selected for
    # fictional dashboard parity; browser requests cannot supply these flags.
    "readiness":           {"--from", "--anchor"},
    # Phase 3 unified integration — deterministic reads only. The goal,
    # collector-run and migration writers remain deliberately absent.
    "feature-registry": {"--family"},
    "feature-frame": {"--from", "--to", "--days", "--all", "--family",
                      "--include-provenance"},
    "data-readiness": {"--from", "--to", "--days", "--all", "--goal", "--outcome"},
    "goal-list": {"--all"},
    # Phase 4 deterministic association engine — pure reads only.
    "outcome-associations": {
        "--outcome", "--from", "--to", "--days", "--all", "--mode",
        "--min-n", "--interactions", "--top",
    },
    "finding-evidence": {
        "--outcome", "--finding-id", "--input-fingerprint",
        "--from", "--to", "--days", "--all",
    },
    # Private derived-analysis jobs: fixed closed request JSON on stdin;
    # status accepts only an opaque ID. Internal worker commands stay absent.
    "analysis-job-start": set(),
    "analysis-job-status": set(),
    # Phase 5 ledger/synthesis. Identifier-only promotion recomputes the
    # finding in health.py. Every unrestricted writer remains absent.
    "hypothesis-promote": {
        "--outcome", "--finding-id", "--input-fingerprint",
        "--from", "--to", "--days", "--all",
    },
    "hypotheses": {"--status", "--outcome", "--limit", "--before"},
    "hypothesis-brief": set(),
    "synthesis-history": {"--limit", "--before"},
    # Phase 6 read-only redacted orchestration audit. Queue/outbox writers and
    # every token-bearing command remain absent.
    "insight-run-status": {"--limit"},
    # agent/model operations -> hermesctl (runs as hermes), not health.py
    "hermes-status":    set(),
    "hermes-spend":     set(),
    "hermes-model-set": set(),
    "fitbit-status":    set(),
    "hermes-chat":      {"--session"},   # message on stdin; fixed session validated by hermesctl
}

# Preserve the existing client-side flag-check subset. The broker checks flags
# for every command; extending client checks is a separate behavior change.
CLIENT_FLAG_CHECKS = {
    "day-rating", "log-commitment", "checkin", "eat", "log-food", "readiness",
    "feature-registry", "feature-frame", "data-readiness", "goal-list",
    "outcome-associations", "finding-evidence", "hypothesis-promote",
    "analysis-job-start", "analysis-job-status",
    "hypotheses", "hypothesis-brief", "synthesis-history", "insight-run-status",
}

# store_true flags consume no following value; other allowed flags consume one.
NO_VALUE_FLAGS = {"--all", "--include-provenance"}

# Comprehensive fictional reads on the capped one-CPU host took 449s for
# all-mode Explorer and 443s for full pairwise evidence replay. The finite 570s
# budget includes 25% headroom, rounded up to 30s. Write and external-Hermes
# limits stay independent; the panel waits ten seconds longer for a response.
OUTCOME_ANALYSIS_TIMEOUT = 570
