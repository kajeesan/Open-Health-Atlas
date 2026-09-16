"""Additive, explicit schema migrations for autonomous health insights.

Only :func:`migrate` opens a writable database.  Status and plan use a
``mode=ro`` URI and never execute DDL against the health database.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from .settings import CANON_TZ


AUTONOMOUS_SCHEMA_VERSION = 7
SCHEMA_CONTRACT = "hermes-autonomous-schema-v1"
class SchemaError(RuntimeError):
    """A controlled schema validation or migration failure."""

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


LEDGER_DDL = """CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum_sha256 TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  code_version TEXT NOT NULL
);"""

LAZY_TABLE_DDL = {
    "routines_history": """CREATE TABLE IF NOT EXISTS routines_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (datetime('now')),
  op TEXT,
  routine_name TEXT,
  exercise_title TEXT,
  prior_existed INTEGER,
  ex_order INTEGER,
  target_sets INTEGER,
  target_reps INTEGER,
  target_weight_kg REAL,
  undone INTEGER DEFAULT 0
);""",
    "commitments": """CREATE TABLE IF NOT EXISTS commitments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  identity TEXT,
  trigger TEXT,
  floor TEXT,
  reward TEXT,
  active INTEGER DEFAULT 1,
  created TEXT
);""",
    "commitments_log": """CREATE TABLE IF NOT EXISTS commitments_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  commitment_id INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN ('kept','partly','broke')),
  why TEXT,
  source TEXT DEFAULT 'ui',
  ingested_at TEXT DEFAULT (datetime('now')),
  UNIQUE(date, commitment_id)
);""",
    "checkins": """CREATE TABLE IF NOT EXISTS checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  time TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('energy','focus','mood')),
  value INTEGER NOT NULL CHECK(value BETWEEN 1 AND 5),
  note TEXT,
  source TEXT DEFAULT 'ui',
  ingested_at TEXT DEFAULT (datetime('now'))
);""",
    "planned_times": """CREATE TABLE IF NOT EXISTS planned_times (
  metric TEXT PRIMARY KEY CHECK(metric IN ('wake','bed','workout','dose')),
  planned TEXT NOT NULL,
  tolerance_min INTEGER NOT NULL,
  updated TEXT DEFAULT (datetime('now'))
);""",
    "nutrient_daily": """CREATE TABLE IF NOT EXISTS nutrient_daily (
  date TEXT NOT NULL,
  nutrient TEXT NOT NULL,
  amount REAL NOT NULL,
  unit TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'cronometer',
  ingested_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY(date, nutrient, source)
);""",
    "nutrition_targets": """CREATE TABLE IF NOT EXISTS nutrition_targets (
  name TEXT PRIMARY KEY,
  target REAL NOT NULL,
  updated TEXT DEFAULT (datetime('now'))
);""",
    "owner_profile": """CREATE TABLE IF NOT EXISTS owner_profile (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);""",
    "fitness_tests": """CREATE TABLE IF NOT EXISTS fitness_tests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, movement TEXT NOT NULL,
  side TEXT NOT NULL DEFAULT 'bilateral',
  load_kg REAL, reps INTEGER, seconds REAL, rating INTEGER, cm REAL,
  degrees REAL, passed INTEGER,
  equipment_note TEXT, source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);""",
    "athletic_targets": """CREATE TABLE IF NOT EXISTS athletic_targets (
  axis TEXT NOT NULL, lift TEXT NOT NULL DEFAULT '', target REAL NOT NULL,
  updated TEXT DEFAULT (datetime('now')), PRIMARY KEY(axis, lift)
);""",
    "exercise_submuscles": """CREATE TABLE IF NOT EXISTS exercise_submuscles (
  exercise_title TEXT NOT NULL,
  muscle_group TEXT NOT NULL,
  sub_region TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  laterality TEXT NOT NULL DEFAULT 'bilateral'
    CHECK(laterality IN ('left','right','bilateral')),
  source TEXT NOT NULL,
  approx INTEGER NOT NULL DEFAULT 0,
  iso INTEGER NOT NULL DEFAULT 0,
  confidence TEXT NOT NULL DEFAULT 'B' CHECK(confidence IN ('E','B')),
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY(exercise_title, sub_region, laterality)
);""",
    "pain_log": """CREATE TABLE IF NOT EXISTS pain_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, region TEXT NOT NULL,
  side TEXT NOT NULL DEFAULT 'central', intensity INTEGER NOT NULL,
  quality TEXT, pattern TEXT, flags TEXT, note TEXT,
  source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);""",
    "self_test_log": """CREATE TABLE IF NOT EXISTS self_test_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, test TEXT NOT NULL,
  side TEXT NOT NULL DEFAULT 'central', result TEXT NOT NULL,
  note TEXT, source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);""",
    "exercise_trial_log": """CREATE TABLE IF NOT EXISTS exercise_trial_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, drill TEXT NOT NULL, target TEXT, dose TEXT,
  response TEXT NOT NULL, pain_during INTEGER, note TEXT,
  source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);""",
    "lab_catalog": """CREATE TABLE IF NOT EXISTS lab_catalog (
  canonical TEXT PRIMARY KEY,
  display TEXT,
  panel TEXT,
  unit TEXT NOT NULL,
  ref_low REAL, ref_high REAL,
  plaus_low REAL NOT NULL, plaus_high REAL NOT NULL,
  max_delta REAL,
  delta_kind TEXT NOT NULL DEFAULT 'abs' CHECK(delta_kind IN ('abs','frac')),
  aliases TEXT,
  source TEXT NOT NULL,
  confidence TEXT NOT NULL DEFAULT 'cited'
    CHECK(confidence IN ('cited','user-confirmed')),
  created_at TEXT DEFAULT (datetime('now'))
);""",
}

GUARDED_001 = {
    ("hevy_sets", "source"): "ALTER TABLE hevy_sets ADD COLUMN source TEXT DEFAULT 'hevy';",
    ("exercise_muscles", "source"): "ALTER TABLE exercise_muscles ADD COLUMN source TEXT DEFAULT 'manual';",
    ("body_metrics", "source"): "ALTER TABLE body_metrics ADD COLUMN source TEXT DEFAULT 'manual';",
    ("fitness_tests", "degrees"): "ALTER TABLE fitness_tests ADD COLUMN degrees REAL;",
    ("fitness_tests", "passed"): "ALTER TABLE fitness_tests ADD COLUMN passed INTEGER;",
    ("recipes", "meal_type"): "ALTER TABLE recipes ADD COLUMN meal_type TEXT;",
    ("subjective_daily", "soreness_note"): "ALTER TABLE subjective_daily ADD COLUMN soreness_note TEXT;",
    ("daily_metrics", "hrv_ms"): "ALTER TABLE daily_metrics ADD COLUMN hrv_ms REAL;",
    ("exercise_submuscles", "approx"): "ALTER TABLE exercise_submuscles ADD COLUMN approx INTEGER NOT NULL DEFAULT 0;",
    ("exercise_submuscles", "iso"): "ALTER TABLE exercise_submuscles ADD COLUMN iso INTEGER NOT NULL DEFAULT 0;",
    ("exercise_submuscles", "confidence"): """ALTER TABLE exercise_submuscles ADD COLUMN confidence TEXT NOT NULL DEFAULT 'B'
  CHECK(confidence IN ('E','B'));""",
}

EVENT_TABLE_DDL = {
    "event_exposures": """CREATE TABLE IF NOT EXISTS event_exposures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  time TEXT,
  category TEXT NOT NULL CHECK(category IN (
    'social','location','activity','travel','illness','stress',
    'food','meal','medication_change','training_phase','other')),
  entity_label TEXT,
  entity_key TEXT,
  value_text TEXT,
  value_num REAL,
  unit TEXT,
  duration_min INTEGER CHECK(duration_min IS NULL OR duration_min BETWEEN 0 AND 10080),
  intensity INTEGER CHECK(intensity IS NULL OR intensity BETWEEN 1 AND 5),
  valence INTEGER CHECK(valence IS NULL OR valence BETWEEN -2 AND 2),
  source TEXT NOT NULL,
  raw_text TEXT,
  raw_ref TEXT,
  note TEXT,
  supersedes_id INTEGER REFERENCES event_exposures(id),
  voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1)),
  void_reason TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK(voided=0 OR length(trim(COALESCE(void_reason,'')))>0)
);""",
    "raw_capture_entries": """CREATE TABLE IF NOT EXISTS raw_capture_entries (
  capture_id TEXT PRIMARY KEY,
  client_event_id TEXT NOT NULL,
  event_date TEXT NOT NULL,
  event_time TEXT,
  surface TEXT NOT NULL CHECK(surface IN ('panel','telegram','ssh','import')),
  source TEXT NOT NULL CHECK(source IN ('chat-panel','chat-telegram','manual','import')),
  raw_text TEXT NOT NULL CHECK(length(raw_text) BETWEEN 1 AND 4000),
  raw_text_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(surface, client_event_id)
);""",
    "raw_capture_resolutions": """CREATE TABLE IF NOT EXISTS raw_capture_resolutions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  capture_id TEXT NOT NULL REFERENCES raw_capture_entries(capture_id),
  status TEXT NOT NULL CHECK(status IN (
    'pending','linked','needs_clarification','not_health_event','cancelled')),
  target_table TEXT CHECK(target_table IN (
    'event_exposures','nutrition_log','meds_log','supplements_log','pain_log',
    'subjective_daily','commitments_log','checkins')),
  target_row_key TEXT,
  reason_code TEXT,
  source TEXT NOT NULL,
  supersedes_id INTEGER REFERENCES raw_capture_resolutions(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK((status='linked' AND target_table IS NOT NULL AND target_row_key IS NOT NULL)
     OR (status!='linked' AND target_table IS NULL AND target_row_key IS NULL))
);""",
    "capture_completeness_revisions": """CREATE TABLE IF NOT EXISTS capture_completeness_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN (
    'food_identity','nutrition_total','social','location','activity','travel',
    'illness','stress','training_phase','other_event','medication','supplement','pain')),
  entity_key TEXT,
  state TEXT NOT NULL CHECK(state IN ('complete','partial','unknown')),
  explicit_none INTEGER NOT NULL DEFAULT 0 CHECK(explicit_none IN (0,1)),
  source TEXT NOT NULL CHECK(source IN ('chat-panel','chat-telegram','manual','import')),
  capture_id TEXT REFERENCES raw_capture_entries(capture_id),
  note TEXT,
  supersedes_id INTEGER REFERENCES capture_completeness_revisions(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK(explicit_none=0 OR state='complete')
);""",
    "entity_aliases": """CREATE TABLE IF NOT EXISTS entity_aliases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL CHECK(entity_type IN (
    'person','food','location','activity','supplement','medication','other')),
  alias_key TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  canonical_label TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'owner',
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  supersedes_id INTEGER REFERENCES entity_aliases(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);""",
}

EVENT_INDEX_DDL = {
    "event_exposures_date_category_idx": """CREATE INDEX IF NOT EXISTS event_exposures_date_category_idx
  ON event_exposures(date, category) WHERE voided=0;""",
    "event_exposures_entity_idx": """CREATE INDEX IF NOT EXISTS event_exposures_entity_idx
  ON event_exposures(category, entity_key, date) WHERE voided=0;""",
    "raw_capture_resolution_latest_idx": """CREATE INDEX IF NOT EXISTS raw_capture_resolution_latest_idx
  ON raw_capture_resolutions(capture_id, id DESC);""",
    "capture_completeness_latest_idx": """CREATE INDEX IF NOT EXISTS capture_completeness_latest_idx
  ON capture_completeness_revisions(date, scope, entity_key, id DESC);""",
    "entity_alias_latest_idx": """CREATE INDEX IF NOT EXISTS entity_alias_latest_idx
  ON entity_aliases(entity_type, alias_key, id DESC);""",
}

PHASE3_TABLE_DDL = {
    "insight_goal_revisions": """CREATE TABLE IF NOT EXISTS insight_goal_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal_key TEXT NOT NULL,
  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 5),
  outcome_key TEXT NOT NULL,
  target_direction TEXT NOT NULL CHECK(target_direction IN ('increase','decrease','maintain')),
  note TEXT,
  source TEXT NOT NULL,
  supersedes_id INTEGER REFERENCES insight_goal_revisions(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);""",
    "source_sync_runs": """CREATE TABLE IF NOT EXISTS source_sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('success','partial','failed')),
  coverage_from TEXT,
  coverage_to TEXT,
  rows_seen INTEGER CHECK(rows_seen IS NULL OR rows_seen >= 0),
  rows_written INTEGER CHECK(rows_written IS NULL OR rows_written >= 0),
  error_code TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(source, started_at)
);""",
    "training_plan_revisions": """CREATE TABLE IF NOT EXISTS training_plan_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  effective_from TEXT NOT NULL,
  schedule_json TEXT NOT NULL,
  routines_json TEXT NOT NULL,
  source TEXT NOT NULL,
  supersedes_id INTEGER REFERENCES training_plan_revisions(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);""",
}

PHASE3_INDEX_DDL = {
    "insight_goal_latest_idx": """CREATE INDEX IF NOT EXISTS insight_goal_latest_idx
  ON insight_goal_revisions(goal_key, id DESC);""",
    "source_sync_runs_latest_idx": """CREATE INDEX IF NOT EXISTS source_sync_runs_latest_idx
  ON source_sync_runs(source, completed_at DESC);""",
    "training_plan_revision_effective_idx": """CREATE INDEX IF NOT EXISTS training_plan_revision_effective_idx
  ON training_plan_revisions(effective_from, id DESC);""",
}

PHASE5_TABLE_DDL = {
    "analysis_batches": """CREATE TABLE IF NOT EXISTS analysis_batches (
  batch_id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL UNIQUE,
  run_kind TEXT NOT NULL CHECK(run_kind IN
    ('manual','nightly','weekly','monthly','trigger')),
  anchor_date TEXT NOT NULL,
  initiator_key TEXT,
  range_plan_version TEXT NOT NULL,
  outcome_selection TEXT NOT NULL CHECK(outcome_selection IN
    ('explicit_set','base_and_enabled','trigger_mapped')),
  outcome_set_sha256 TEXT NOT NULL,
  analysis_version TEXT NOT NULL,
  registry_version TEXT NOT NULL,
  engine_sha256 TEXT NOT NULL,
  registry_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN
    ('running','completed','partial','failed')),
  status_reason_code TEXT,
  run_count INTEGER NOT NULL DEFAULT 0 CHECK(run_count >= 0),
  completed_count INTEGER NOT NULL DEFAULT 0 CHECK(completed_count >= 0),
  insufficient_count INTEGER NOT NULL DEFAULT 0 CHECK(insufficient_count >= 0),
  no_data_count INTEGER NOT NULL DEFAULT 0 CHECK(no_data_count >= 0),
  failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
  started_at TEXT NOT NULL,
  completed_at TEXT
);""",
    "analysis_range_requests": """CREATE TABLE IF NOT EXISTS analysis_range_requests (
  range_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES analysis_batches(batch_id),
  range_role TEXT NOT NULL CHECK(range_role IN ('primary','recent','historical')),
  requested_range_kind TEXT NOT NULL CHECK(requested_range_kind IN ('all','bounded')),
  requested_from TEXT,
  requested_to TEXT,
  UNIQUE(batch_id, range_role),
  UNIQUE(range_id, batch_id),
  CHECK((requested_range_kind='all' AND requested_from IS NULL AND requested_to IS NULL)
     OR (requested_range_kind='bounded' AND requested_from IS NOT NULL
         AND requested_to IS NOT NULL AND requested_from <= requested_to))
);""",
    "analysis_runs": """CREATE TABLE IF NOT EXISTS analysis_runs (
  run_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  range_id TEXT NOT NULL,
  outcome_key TEXT NOT NULL,
  outcome_mode TEXT NOT NULL,
  range_resolution TEXT CHECK(range_resolution IN
    ('bounded_exact','all_observed','all_no_data')),
  analysis_from TEXT,
  analysis_to TEXT,
  baseline_from TEXT,
  baseline_to TEXT,
  input_fingerprint TEXT,
  status TEXT NOT NULL CHECK(status IN
    ('running','completed','insufficient_data','no_data','failed')),
  status_reason_code TEXT,
  result_json TEXT,
  result_sha256 TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(range_id, batch_id)
    REFERENCES analysis_range_requests(range_id, batch_id),
  CHECK((analysis_from IS NULL AND analysis_to IS NULL)
     OR (analysis_from IS NOT NULL AND analysis_to IS NOT NULL AND analysis_from <= analysis_to)),
  CHECK((baseline_from IS NULL AND baseline_to IS NULL)
     OR (baseline_from IS NOT NULL AND baseline_to IS NOT NULL AND baseline_from <= baseline_to)),
  CHECK(status IN ('running','failed') OR
    (range_resolution IS NOT NULL AND input_fingerprint IS NOT NULL
     AND result_json IS NOT NULL AND result_sha256 IS NOT NULL AND completed_at IS NOT NULL)),
  CHECK(range_resolution IS NULL
     OR (range_resolution='all_no_data' AND analysis_from IS NULL AND analysis_to IS NULL)
     OR (range_resolution IN ('bounded_exact','all_observed')
         AND analysis_from IS NOT NULL AND analysis_to IS NOT NULL)),
  UNIQUE(range_id, outcome_key, outcome_mode)
);""",
    "analysis_findings": """CREATE TABLE IF NOT EXISTS analysis_findings (
  finding_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
  candidate_key TEXT NOT NULL,
  candidate_kind TEXT NOT NULL CHECK(candidate_kind IN ('single','pair')),
  outcome_key TEXT NOT NULL,
  outcome_mode TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('positive','negative','mixed','unknown')),
  quality_tier TEXT NOT NULL CHECK(quality_tier IN
    ('insufficient','exploratory_screen','exploratory_unreplicated','replicated')),
  eligible_for_hypothesis INTEGER NOT NULL CHECK(eligible_for_hypothesis IN (0,1)),
  evidence_json TEXT NOT NULL,
  evidence_for_json TEXT NOT NULL,
  evidence_against_json TEXT NOT NULL,
  evidence_fingerprint TEXT NOT NULL,
  UNIQUE(run_id, candidate_key)
);""",
    "analysis_finding_components": """CREATE TABLE IF NOT EXISTS analysis_finding_components (
  finding_id TEXT NOT NULL REFERENCES analysis_findings(finding_id),
  position INTEGER NOT NULL CHECK(position IN (1,2)),
  exposure_key TEXT NOT NULL,
  lag_days INTEGER NOT NULL CHECK(lag_days BETWEEN 0 AND 365),
  window_days INTEGER NOT NULL CHECK(window_days IN (1,3,7,28,90,365)),
  transform TEXT NOT NULL CHECK(transform IN
    ('point','mean','sum','count','delta_per_day','frequency_per_week','days_since')),
  temporal_direction TEXT NOT NULL CHECK(temporal_direction IN
    ('exposure_precedes_outcome','same_day_ordered','same_day_or_order_unknown')),
  PRIMARY KEY(finding_id, position),
  UNIQUE(finding_id, exposure_key)
);""",
    "hypotheses": """CREATE TABLE IF NOT EXISTS hypotheses (
  hypothesis_id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL UNIQUE,
  outcome_key TEXT NOT NULL,
  outcome_mode TEXT NOT NULL,
  candidate_kind TEXT NOT NULL CHECK(candidate_kind IN ('single','pair')),
  initial_direction TEXT NOT NULL CHECK(initial_direction IN
    ('positive','negative','mixed','unknown')),
  first_seen TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by_run TEXT NOT NULL REFERENCES analysis_runs(run_id),
  created_by_finding TEXT NOT NULL REFERENCES analysis_findings(finding_id)
);""",
    "hypothesis_components": """CREATE TABLE IF NOT EXISTS hypothesis_components (
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  position INTEGER NOT NULL CHECK(position IN (1,2)),
  exposure_key TEXT NOT NULL,
  lag_days INTEGER NOT NULL CHECK(lag_days BETWEEN 0 AND 365),
  window_days INTEGER NOT NULL CHECK(window_days IN (1,3,7,28,90,365)),
  transform TEXT NOT NULL CHECK(transform IN
    ('point','mean','sum','count','delta_per_day','frequency_per_week','days_since')),
  PRIMARY KEY(hypothesis_id, position),
  UNIQUE(hypothesis_id, exposure_key)
);""",
    "hypothesis_evaluations": """CREATE TABLE IF NOT EXISTS hypothesis_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
  finding_id TEXT REFERENCES analysis_findings(finding_id),
  tested_at TEXT NOT NULL,
  range_from TEXT,
  range_to TEXT,
  evidence_class TEXT NOT NULL CHECK(evidence_class IN
    ('initial_exploratory','initial_discovery_pass','same_pass_overlap',
     'same_pass_nonoverlap','same_exploratory','weakening_window',
     'null_nonoverlap','opposite_pass','incompatible_version',
     'dormant_no_eligible_data','dormant_stale_prerequisite')),
  previous_status TEXT CHECK(previous_status IN
    ('candidate','exploratory','strengthening','replicated','weakened','rejected','dormant')),
  evidence_for_json TEXT NOT NULL,
  evidence_against_json TEXT NOT NULL,
  confounders_json TEXT NOT NULL,
  sample_size_json TEXT NOT NULL,
  effect_summary_json TEXT NOT NULL,
  stability_json TEXT NOT NULL,
  confidence TEXT NOT NULL CHECK(confidence IN ('insufficient','low','moderate','high')),
  status TEXT NOT NULL CHECK(status IN
    ('candidate','exploratory','strengthening','replicated','weakened','rejected','dormant')),
  change_reason TEXT NOT NULL,
  change_conditions TEXT NOT NULL,
  source_analysis_version TEXT NOT NULL,
  input_fingerprint TEXT NOT NULL,
  evidence_fingerprint TEXT NOT NULL,
  previous_evaluation_id INTEGER REFERENCES hypothesis_evaluations(id),
  comparison_evaluation_id INTEGER REFERENCES hypothesis_evaluations(id),
  new_eligible_observations INTEGER NOT NULL CHECK(new_eligible_observations >= 0),
  compatible_with_prior INTEGER NOT NULL CHECK(compatible_with_prior IN (0,1)),
  transition_applied INTEGER NOT NULL CHECK(transition_applied IN (0,1)),
  created_at TEXT NOT NULL,
  CHECK((range_from IS NULL AND range_to IS NULL)
     OR (range_from IS NOT NULL AND range_to IS NOT NULL AND range_from <= range_to)),
  CHECK((evidence_class IN ('dormant_no_eligible_data','dormant_stale_prerequisite')
         AND finding_id IS NULL)
     OR (evidence_class NOT IN ('dormant_no_eligible_data','dormant_stale_prerequisite')
         AND finding_id IS NOT NULL)),
  UNIQUE(hypothesis_id, run_id),
  UNIQUE(hypothesis_id, input_fingerprint, source_analysis_version),
  UNIQUE(id, hypothesis_id)
);""",
    "hypothesis_evidence_items": """CREATE TABLE IF NOT EXISTS hypothesis_evidence_items (
  evidence_item_id TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  evaluation_id INTEGER NOT NULL REFERENCES hypothesis_evaluations(id),
  finding_id TEXT NOT NULL REFERENCES analysis_findings(finding_id),
  polarity TEXT NOT NULL CHECK(polarity IN ('for','against')),
  evidence_kind TEXT NOT NULL CHECK(evidence_kind IN
    ('same_direction_effect','nonoverlap_replication','weak_or_unstable',
     'null_window','opposite_direction_effect','confounder_sensitivity',
     'method_incompatibility')),
  evidence_fingerprint TEXT NOT NULL,
  source_analysis_version TEXT NOT NULL,
  range_from TEXT NOT NULL,
  range_to TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(hypothesis_id, finding_id, evidence_kind)
);""",
    "synthesis_runs": """CREATE TABLE IF NOT EXISTS synthesis_runs (
  synthesis_id TEXT PRIMARY KEY,
  analysis_batch_id TEXT NOT NULL REFERENCES analysis_batches(batch_id),
  cadence TEXT NOT NULL CHECK(cadence IN ('manual','weekly','monthly','trigger')),
  reason_code TEXT NOT NULL,
  cutoff_date TEXT NOT NULL,
  evidence_fingerprint TEXT NOT NULL,
  context_version TEXT NOT NULL,
  prompt_sha256 TEXT,
  model_id TEXT,
  provider TEXT,
  finding_ids_json TEXT NOT NULL,
  hypothesis_ids_json TEXT NOT NULL,
  narrative_md TEXT,
  rendered_md TEXT,
  status TEXT NOT NULL CHECK(status IN
    ('running','completed','insufficient_data','no_novelty','suppressed','failed')),
  no_message_reason_code TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  CHECK(status!='completed' OR
    (prompt_sha256 IS NOT NULL AND model_id='z-ai/glm-5.2' AND provider='openrouter'
     AND narrative_md IS NOT NULL AND completed_at IS NOT NULL)),
  CHECK(status NOT IN ('insufficient_data','no_novelty','suppressed','failed')
     OR no_message_reason_code IS NOT NULL)
);""",
    "synthesis_analysis_runs": """CREATE TABLE IF NOT EXISTS synthesis_analysis_runs (
  synthesis_id TEXT NOT NULL REFERENCES synthesis_runs(synthesis_id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
  purpose TEXT NOT NULL CHECK(purpose IN ('primary','recent','historical','trigger')),
  PRIMARY KEY(synthesis_id, run_id)
);""",
    "synthesis_finding_refs": """CREATE TABLE IF NOT EXISTS synthesis_finding_refs (
  synthesis_id TEXT NOT NULL REFERENCES synthesis_runs(synthesis_id),
  finding_id TEXT NOT NULL REFERENCES analysis_findings(finding_id),
  role TEXT NOT NULL CHECK(role IN ('primary','supporting','against','context')),
  PRIMARY KEY(synthesis_id, finding_id, role)
);""",
    "synthesis_hypothesis_refs": """CREATE TABLE IF NOT EXISTS synthesis_hypothesis_refs (
  synthesis_id TEXT NOT NULL REFERENCES synthesis_runs(synthesis_id),
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  evaluation_id INTEGER NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('active','changed','context')),
  PRIMARY KEY(synthesis_id, hypothesis_id, role),
  FOREIGN KEY(evaluation_id, hypothesis_id)
    REFERENCES hypothesis_evaluations(id, hypothesis_id)
);""",
    "hypothesis_annotations": """CREATE TABLE IF NOT EXISTS hypothesis_annotations (
  annotation_id TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  evaluation_id INTEGER REFERENCES hypothesis_evaluations(id),
  annotation_kind TEXT NOT NULL CHECK(annotation_kind IN
    ('mechanism','alternative','next_experiment','owner_note')),
  content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 4000),
  source TEXT NOT NULL CHECK(source IN ('owner','glm_synthesis')),
  synthesis_id TEXT REFERENCES synthesis_runs(synthesis_id),
  context_version TEXT,
  prompt_sha256 TEXT,
  model_id TEXT,
  provider TEXT,
  supersedes_id TEXT REFERENCES hypothesis_annotations(annotation_id),
  input_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK(source!='glm_synthesis' OR
    (synthesis_id IS NOT NULL AND context_version IS NOT NULL AND prompt_sha256 IS NOT NULL
     AND model_id='z-ai/glm-5.2' AND provider='openrouter'))
);""",
    "insight_triggers": """CREATE TABLE IF NOT EXISTS insight_triggers (
  trigger_id TEXT PRIMARY KEY,
  trigger_kind TEXT NOT NULL CHECK(trigger_kind IN
    ('pain_started','pain_worsened','quarterly_observation','running_restart','training_load_change',
     'medication_regime_change','hypothesis_reversal','hypothesis_replicated',
     'outcome_milestone','manual')),
  source_table TEXT NOT NULL,
  source_row_key TEXT NOT NULL,
  event_date TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK(state IN
    ('pending','leased','retry_wait','processed','suppressed','dead_letter')),
  not_before TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK(max_attempts BETWEEN 1 AND 10),
  lease_generation INTEGER NOT NULL DEFAULT 0 CHECK(lease_generation >= 0),
  lease_owner TEXT,
  lease_token_sha256 TEXT,
  lease_expires_at TEXT,
  last_error_code TEXT,
  analysis_batch_id TEXT REFERENCES analysis_batches(batch_id),
  synthesis_id TEXT REFERENCES synthesis_runs(synthesis_id),
  processed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL,
  CHECK((state='leased' AND lease_owner IS NOT NULL
         AND lease_token_sha256 IS NOT NULL AND lease_expires_at IS NOT NULL)
     OR (state!='leased' AND lease_owner IS NULL
         AND lease_token_sha256 IS NULL AND lease_expires_at IS NULL)),
  CHECK(state NOT IN ('processed','suppressed','dead_letter') OR processed_at IS NOT NULL)
);""",
    "insight_trigger_events": """CREATE TABLE IF NOT EXISTS insight_trigger_events (
  event_id TEXT PRIMARY KEY,
  trigger_id TEXT NOT NULL REFERENCES insight_triggers(trigger_id),
  lease_generation INTEGER NOT NULL,
  attempt_no INTEGER NOT NULL CHECK(attempt_no >= 0),
  event_kind TEXT NOT NULL CHECK(event_kind IN
    ('enqueued','claimed','renewed','lease_expired','processed','suppressed',
     'retry_scheduled','dead_letter')),
  reason_code TEXT,
  analysis_batch_id TEXT REFERENCES analysis_batches(batch_id),
  synthesis_id TEXT REFERENCES synthesis_runs(synthesis_id),
  occurred_at TEXT NOT NULL
);""",
    "insight_notification_outbox": """CREATE TABLE IF NOT EXISTS insight_notification_outbox (
  notification_id TEXT PRIMARY KEY,
  analysis_batch_id TEXT REFERENCES analysis_batches(batch_id),
  synthesis_id TEXT REFERENCES synthesis_runs(synthesis_id),
  trigger_id TEXT REFERENCES insight_triggers(trigger_id),
  dedupe_key TEXT NOT NULL UNIQUE,
  channel_class TEXT NOT NULL,
  destination_class TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  idempotency_mode TEXT NOT NULL CHECK(idempotency_mode IN ('none','provider_key')),
  provider_idempotency_key TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('pending','leased','dispatching','retry_wait','sent','suppressed','uncertain','dead_letter')),
  not_before TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK(max_attempts BETWEEN 1 AND 10),
  lease_generation INTEGER NOT NULL DEFAULT 0 CHECK(lease_generation >= 0),
  lease_owner TEXT,
  lease_token_sha256 TEXT,
  lease_expires_at TEXT,
  dispatch_started_at TEXT,
  last_error_code TEXT,
  sent_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL,
  CHECK((idempotency_mode='none' AND provider_idempotency_key IS NULL)
     OR (idempotency_mode='provider_key' AND provider_idempotency_key IS NOT NULL)),
  CHECK((state IN ('leased','dispatching') AND lease_owner IS NOT NULL
         AND lease_token_sha256 IS NOT NULL AND lease_expires_at IS NOT NULL)
     OR (state NOT IN ('leased','dispatching') AND lease_owner IS NULL
         AND lease_token_sha256 IS NULL AND lease_expires_at IS NULL)),
  CHECK(state!='dispatching' OR dispatch_started_at IS NOT NULL),
  CHECK(state!='sent' OR sent_at IS NOT NULL)
);""",
    "insight_notification_events": """CREATE TABLE IF NOT EXISTS insight_notification_events (
  event_id TEXT PRIMARY KEY,
  notification_id TEXT NOT NULL REFERENCES insight_notification_outbox(notification_id),
  lease_generation INTEGER NOT NULL,
  attempt_no INTEGER NOT NULL CHECK(attempt_no >= 0),
  event_kind TEXT NOT NULL CHECK(event_kind IN
    ('enqueued','duplicate_observed','claimed','lease_expired_before_dispatch',
     'dispatch_committed','provider_ack','known_not_sent','retry_scheduled',
     'suppressed','uncertain','dead_letter','manual_resolved_sent','manual_resolved_retry')),
  reason_code TEXT,
  provider_reference_sha256 TEXT,
  occurred_at TEXT NOT NULL
);""",
}

PHASE5_INDEX_DDL = {
    "analysis_runs_lookup_idx": """CREATE INDEX IF NOT EXISTS analysis_runs_lookup_idx
  ON analysis_runs(outcome_key, outcome_mode, completed_at DESC);""",
    "analysis_findings_run_idx": """CREATE INDEX IF NOT EXISTS analysis_findings_run_idx
  ON analysis_findings(run_id, quality_tier);""",
    "hypothesis_eval_latest_idx": """CREATE INDEX IF NOT EXISTS hypothesis_eval_latest_idx
  ON hypothesis_evaluations(hypothesis_id, tested_at DESC, id DESC);""",
    "hypothesis_evidence_history_idx": """CREATE INDEX IF NOT EXISTS hypothesis_evidence_history_idx
  ON hypothesis_evidence_items(hypothesis_id, created_at, evidence_item_id);""",
    "hypothesis_annotations_latest_idx": """CREATE INDEX IF NOT EXISTS hypothesis_annotations_latest_idx
  ON hypothesis_annotations(hypothesis_id, annotation_kind, created_at DESC);""",
    "hypothesis_annotation_superseded_once_idx": """CREATE UNIQUE INDEX IF NOT EXISTS hypothesis_annotation_superseded_once_idx
  ON hypothesis_annotations(supersedes_id) WHERE supersedes_id IS NOT NULL;""",
    "insight_triggers_queue_idx": """CREATE INDEX IF NOT EXISTS insight_triggers_queue_idx
  ON insight_triggers(state, not_before, lease_expires_at, created_at);""",
    "insight_notification_queue_idx": """CREATE INDEX IF NOT EXISTS insight_notification_queue_idx
  ON insight_notification_outbox(state, not_before, lease_expires_at, created_at);""",
}

_APPEND_ONLY_TABLES = (
    "hypotheses",
    "hypothesis_components",
    "hypothesis_evaluations",
    "hypothesis_evidence_items",
    "hypothesis_annotations",
)


def _append_only_trigger_ddl(table: str, operation: str) -> str:
    name = f"{table}_no_{operation.lower()}"
    return f"""CREATE TRIGGER IF NOT EXISTS {name}
BEFORE {operation} ON {table}
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;"""


PHASE5_TRIGGER_DDL = {
    f"{table}_no_{operation.lower()}": _append_only_trigger_ddl(table, operation)
    for table in _APPEND_ONLY_TABLES
    for operation in ("UPDATE", "DELETE")
}

GUARDED_002 = {
    ("nutrition_log", "time"): "ALTER TABLE nutrition_log ADD COLUMN time TEXT;",
    ("nutrition_log", "meal_type"): "ALTER TABLE nutrition_log ADD COLUMN meal_type TEXT;",
    ("supplements_log", "time_taken"): "ALTER TABLE supplements_log ADD COLUMN time_taken TEXT;",
    ("pain_log", "reported_onset_date"): "ALTER TABLE pain_log ADD COLUMN reported_onset_date TEXT;",
    ("pain_log", "onset_precision"): """ALTER TABLE pain_log ADD COLUMN onset_precision TEXT
  CHECK(onset_precision IN ('exact','approximate','unknown'));""",
}

MIGRATION_001_SQL = "\n\n".join(
    [LEDGER_DDL, *LAZY_TABLE_DDL.values(), *GUARDED_001.values(),
     "UPDATE hevy_sets SET source='hevy' WHERE source IS NULL;",
     "UPDATE exercise_muscles SET source='manual' WHERE source IS NULL;",
     "UPDATE body_metrics SET source='manual' WHERE source IS NULL;",
     "UPDATE daily_metrics SET hrv_ms=hrv_sdnn WHERE hrv_ms IS NULL AND hrv_sdnn IS NOT NULL;"]
) + "\n"
MIGRATION_002_SQL = "\n\n".join(
    [*EVENT_TABLE_DDL.values(), *EVENT_INDEX_DDL.values(), *GUARDED_002.values()]
) + "\n"
MIGRATION_003_SQL = "\n\n".join(
    [*PHASE3_TABLE_DDL.values(), *PHASE3_INDEX_DDL.values()]
) + "\n"
MIGRATION_004_SQL = "\n\n".join(
    [*PHASE5_TABLE_DDL.values(), *PHASE5_INDEX_DDL.values(), *PHASE5_TRIGGER_DDL.values()]
) + "\n"

RESTOCK_STATE_DDL = """CREATE TABLE IF NOT EXISTS recipe_restock_state (
  recipe_id TEXT PRIMARY KEY,
  action TEXT NOT NULL CHECK(action IN
    ('notified','restock','alternatives','later','skip')),
  threshold REAL NOT NULL DEFAULT 2 CHECK(threshold >= 0 AND threshold <= 1000000),
  portions_at_notice REAL,
  snooze_until TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);"""
MIGRATION_005_SQL = RESTOCK_STATE_DDL + "\n"

_MODEL_ID_SQL_CHECK = (
    "length(model_id) BETWEEN 1 AND 160 "
    "AND model_id NOT GLOB '*[^A-Za-z0-9_.:/+-]*' "
    "AND substr(model_id,1,1) GLOB '[A-Za-z0-9]'"
)
_PROVIDER_SQL_CHECK = (
    "length(provider) BETWEEN 1 AND 160 "
    "AND provider NOT GLOB '*[^A-Za-z0-9_.:/+-]*' "
    "AND substr(provider,1,1) GLOB '[A-Za-z0-9]'"
)

SYNTHESIS_RUNS_V6_DDL = f"""CREATE TABLE IF NOT EXISTS synthesis_runs (
  synthesis_id TEXT PRIMARY KEY,
  analysis_batch_id TEXT NOT NULL REFERENCES analysis_batches(batch_id),
  cadence TEXT NOT NULL CHECK(cadence IN ('manual','weekly','monthly','trigger')),
  reason_code TEXT NOT NULL,
  cutoff_date TEXT NOT NULL,
  evidence_fingerprint TEXT NOT NULL,
  context_version TEXT NOT NULL,
  prompt_sha256 TEXT,
  model_id TEXT,
  provider TEXT,
  finding_ids_json TEXT NOT NULL,
  hypothesis_ids_json TEXT NOT NULL,
  narrative_md TEXT,
  rendered_md TEXT,
  status TEXT NOT NULL CHECK(status IN
    ('running','completed','insufficient_data','no_novelty','suppressed','failed')),
  no_message_reason_code TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  CHECK((prompt_sha256 IS NULL AND model_id IS NULL AND provider IS NULL) OR
    (prompt_sha256 IS NOT NULL AND model_id IS NOT NULL AND provider IS NOT NULL
     AND {_MODEL_ID_SQL_CHECK} AND {_PROVIDER_SQL_CHECK})),
  CHECK(status!='completed' OR
    (prompt_sha256 IS NOT NULL AND model_id IS NOT NULL AND provider IS NOT NULL
     AND narrative_md IS NOT NULL AND completed_at IS NOT NULL)),
  CHECK(status NOT IN ('insufficient_data','no_novelty','suppressed','failed')
     OR no_message_reason_code IS NOT NULL)
);"""

HYPOTHESIS_ANNOTATIONS_V6_DDL = f"""CREATE TABLE IF NOT EXISTS hypothesis_annotations (
  annotation_id TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  evaluation_id INTEGER REFERENCES hypothesis_evaluations(id),
  annotation_kind TEXT NOT NULL CHECK(annotation_kind IN
    ('mechanism','alternative','next_experiment','owner_note')),
  content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 4000),
  source TEXT NOT NULL CHECK(source IN ('owner','glm_synthesis','hermes_synthesis')),
  synthesis_id TEXT REFERENCES synthesis_runs(synthesis_id),
  context_version TEXT,
  prompt_sha256 TEXT,
  model_id TEXT,
  provider TEXT,
  supersedes_id TEXT REFERENCES hypothesis_annotations(annotation_id),
  input_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK(
    (source='owner' AND synthesis_id IS NULL AND context_version IS NULL
     AND prompt_sha256 IS NULL AND model_id IS NULL AND provider IS NULL) OR
    (source='glm_synthesis' AND synthesis_id IS NOT NULL
     AND context_version IS NOT NULL AND prompt_sha256 IS NOT NULL
     AND model_id='z-ai/glm-5.2' AND provider='openrouter') OR
    (source='hermes_synthesis' AND synthesis_id IS NOT NULL
     AND context_version IS NOT NULL AND prompt_sha256 IS NOT NULL
     AND model_id IS NOT NULL AND provider IS NOT NULL
     AND {_MODEL_ID_SQL_CHECK} AND {_PROVIDER_SQL_CHECK})
  )
);"""

MIGRATION_006_SQL = "\n\n".join(
    (SYNTHESIS_RUNS_V6_DDL, HYPOTHESIS_ANNOTATIONS_V6_DDL)
) + "\n"


# Preserve Migration 001's published SQL and the exact earlier installed
# declaration. These identities differ only in this closed confidence CHECK.
LEGACY_MIGRATION_001_CHECKSUM = (
    "2666d2dfca470c08f9996ab2242f848aa878bc1774fc94c542407cd14685e459"
)
LEGACY_LAB_CATALOG_DDL = LAZY_TABLE_DDL["lab_catalog"].replace(
    "CHECK(confidence IN ('cited','user-confirmed'))",
    "CHECK(confidence IN ('cited','owner-confirmed'))",
)
LAB_CATALOG_V7_DDL = LAZY_TABLE_DDL["lab_catalog"].replace(
    "CHECK(confidence IN ('cited','user-confirmed'))",
    "CHECK(confidence IN ('cited','owner-confirmed','user-confirmed'))",
)
MIGRATION_007_SQL = LAB_CATALOG_V7_DDL + "\n"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        normalized = self.sql.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


MIGRATIONS = (
    Migration(1, "001_reconcile_baseline", MIGRATION_001_SQL),
    Migration(2, "002_event_exposures", MIGRATION_002_SQL),
    Migration(3, "003_feature_config_and_provenance", MIGRATION_003_SQL),
    Migration(4, "004_insight_ledger", MIGRATION_004_SQL),
    Migration(5, "005_recipe_restock_state", MIGRATION_005_SQL),
    Migration(6, "006_provider_neutral_synthesis", MIGRATION_006_SQL),
    Migration(7, "007_lab_catalog_confidence_history", MIGRATION_007_SQL),
)
MIGRATION_BY_VERSION = {m.version: m for m in MIGRATIONS}


def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_map(c: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    rows = c.execute(f"PRAGMA table_info('{table}')")
    return {(row["name"] if isinstance(row, sqlite3.Row) else row[1]): row for row in rows}


def _affinity(declared: str) -> str:
    value = (declared or "").upper()
    if "INT" in value:
        return "INTEGER"
    if any(token in value for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in value or not value:
        return "BLOB"
    if any(token in value for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _default(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", "", str(value)).lower()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return text


def _expected_columns(ddl: str, table: str) -> dict[str, dict]:
    # This scratch database is never the health database.  It lets SQLite own
    # affinity/default/PK parsing while target status/plan remain mode=ro.
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    try:
        scratch.executescript(ddl)
        return {r["name"]: dict(r) for r in scratch.execute(f"PRAGMA table_info('{table}')")}
    finally:
        scratch.close()


def _unique_keys(c: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    keys = set()
    for row in c.execute(f"PRAGMA index_list('{table}')"):
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        unique = row["unique"] if isinstance(row, sqlite3.Row) else row[2]
        if unique:
            columns = []
            for item in c.execute(f"PRAGMA index_info('{name}')"):
                columns.append(item["name"] if isinstance(item, sqlite3.Row) else item[2])
            keys.add(tuple(columns))
    return keys


def _foreign_keys(c: sqlite3.Connection, table: str) -> set[tuple[object, ...]]:
    result = set()
    for row in c.execute(f"PRAGMA foreign_key_list('{table}')"):
        values = tuple(row)
        # Foreign-key IDs are parser allocation details; sequence and semantic
        # fields are the shape contract.
        result.add(values[1:])
    return result


def _expected_relations(ddl: str, table: str) -> tuple[set[tuple[str, ...]], set[tuple[object, ...]]]:
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    try:
        scratch.executescript(ddl)
        return _unique_keys(scratch, table), _foreign_keys(scratch, table)
    finally:
        scratch.close()


def _sql_key(value: str) -> str:
    # sqlite_master omits the statement terminator while checked-in DDL keeps
    # it for direct execution.  The terminator has no bearing on shape.
    return re.sub(r"[\s\"`\[\]]+", "", (value or "").lower()).rstrip(";")


def _check_expressions(sql: str) -> list[tuple[str, str]]:
    """Extract balanced CHECK expressions as raw text and semantic keys."""

    result: list[tuple[str, str]] = []
    for match in re.finditer(r"\bcheck\s*\(", sql or "", flags=re.IGNORECASE):
        start = match.end()
        depth, quote, index = 1, None, start
        while index < len(sql) and depth:
            char = sql[index]
            if quote:
                if char == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 1
                    else:
                        quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        if depth:
            raise SchemaError("incompatible_schema", "unbalanced CHECK constraint")
        raw = sql[start:index - 1]
        result.append((raw, _sql_key(raw)))
    return result


def _references_column(expression: str, column: str) -> bool:
    return re.search(
        rf"(?<![a-z0-9_]){re.escape(column.lower())}(?![a-z0-9_])",
        expression.lower(),
    ) is not None


REQUIRED_SQL = {
    "commitments_log": ["CHECK(status IN ('kept','partly','broke'))"],
    "checkins": ["CHECK(kind IN ('energy','focus','mood'))", "CHECK(value BETWEEN 1 AND 5)"],
    "planned_times": ["CHECK(metric IN ('wake','bed','workout','dose'))"],
    "exercise_submuscles": ["CHECK(laterality IN ('left','right','bilateral'))"],
    "lab_catalog": ["CHECK(delta_kind IN ('abs','frac'))"],
    "event_exposures": ["CHECK(voided IN (0,1))", "CHECK(voided=0 OR length(trim(COALESCE(void_reason,'')))>0)"],
    "raw_capture_entries": ["CHECK(length(raw_text) BETWEEN 1 AND 4000)"],
    "raw_capture_resolutions": ["CHECK((status='linked' AND target_table IS NOT NULL AND target_row_key IS NOT NULL) OR (status!='linked' AND target_table IS NULL AND target_row_key IS NULL))"],
    "capture_completeness_revisions": ["CHECK(explicit_none=0 OR state='complete')"],
    "entity_aliases": ["CHECK(active IN (0,1))"],
}


def _validate_table(
    c: sqlite3.Connection, table: str, ddl: str, *, allow_missing: set[str] | None = None,
    allow_extra: set[str] | None = None,
) -> None:
    allow_missing = allow_missing or set()
    allow_extra = allow_extra or set()
    if not _table_exists(c, table):
        raise SchemaError("incompatible_schema", f"required table is absent: {table}")
    actual = _column_map(c, table)
    expected = _expected_columns(ddl, table)
    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    if missing - allow_missing or extra - allow_extra:
        raise SchemaError(
            "incompatible_schema",
            f"{table} columns differ (missing={sorted(missing)}, extra={sorted(extra)})",
        )
    for name in set(expected) & set(actual):
        got, want = actual[name], expected[name]
        if (_affinity(got["type"]), int(got["notnull"]), int(got["pk"]), _default(got["dflt_value"])) != (
            _affinity(want["type"]), int(want["notnull"]), int(want["pk"]), _default(want["dflt_value"])
        ):
            raise SchemaError("incompatible_schema", f"{table}.{name} has an incompatible declaration")
    expected_unique, expected_fks = _expected_relations(ddl, table)
    actual_unique = _unique_keys(c, table)
    # This partial unique key is an explicitly named Migration 004 index, not
    # a table-level UNIQUE clause. Its name/predicate are verified separately.
    if table == "hypothesis_annotations":
        actual_unique.discard(("supersedes_id",))
    if actual_unique != expected_unique:
        raise SchemaError("incompatible_schema", f"{table} unique keys differ")
    if _foreign_keys(c, table) != expected_fks:
        raise SchemaError("incompatible_schema", f"{table} foreign keys differ")
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    raw_sql = row[0] if row else ""
    sql = _sql_key(raw_sql)
    expected_checks = _check_expressions(ddl)
    actual_checks = _check_expressions(raw_sql)
    expected_checks = [
        item for item in expected_checks
        if not any(_references_column(item[0], column) for column in missing & allow_missing)
    ]
    actual_checks = [
        item for item in actual_checks
        if not any(_references_column(item[0], column) for column in extra & allow_extra)
    ]
    if Counter(item[1] for item in actual_checks) != Counter(item[1] for item in expected_checks):
        raise SchemaError("incompatible_schema", f"{table} CHECK constraints differ")
    if (
        table in EVENT_TABLE_DDL
        or table in PHASE3_TABLE_DDL
        or table in PHASE5_TABLE_DDL
        or table in {"schema_migrations", "lab_catalog"}
    ):
        if table == "schema_migrations":
            expected_sql = LEDGER_DDL
        elif table in EVENT_TABLE_DDL:
            expected_sql = EVENT_TABLE_DDL[table]
        elif table in PHASE3_TABLE_DDL:
            expected_sql = PHASE3_TABLE_DDL[table]
        else:
            expected_sql = ddl
        if sql.replace("createtableifnotexists", "createtable", 1) != \
                _sql_key(expected_sql).replace("createtableifnotexists", "createtable", 1):
            raise SchemaError("incompatible_schema", f"{table} SQL definition differs")
    for fragment in REQUIRED_SQL.get(table, []):
        if table == "exercise_submuscles" and "confidence" not in actual and "confidence" in fragment:
            continue
        if _sql_key(fragment) not in sql:
            raise SchemaError("incompatible_schema", f"{table} is missing required constraint")


def _expected_guard_column(table: str, column: str, migration: int) -> dict:
    if migration == 1 and table in LAZY_TABLE_DDL:
        return _expected_columns(LAZY_TABLE_DDL[table], table)[column]
    if migration == 2 and table == "pain_log":
        ddl = LAZY_TABLE_DDL["pain_log"].rstrip(";\n")[:-1] + ", reported_onset_date TEXT, onset_precision TEXT CHECK(onset_precision IN ('exact','approximate','unknown')));"
    elif migration == 2 and table == "nutrition_log":
        ddl = "CREATE TABLE nutrition_log(id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, meal_type TEXT);"
    elif migration == 2 and table == "supplements_log":
        ddl = "CREATE TABLE supplements_log(id INTEGER PRIMARY KEY AUTOINCREMENT, time_taken TEXT);"
    else:
        declarations = {
            ("hevy_sets", "source"): "CREATE TABLE hevy_sets(source TEXT DEFAULT 'hevy');",
            ("exercise_muscles", "source"): "CREATE TABLE exercise_muscles(source TEXT DEFAULT 'manual');",
            ("body_metrics", "source"): "CREATE TABLE body_metrics(source TEXT DEFAULT 'manual');",
            ("recipes", "meal_type"): "CREATE TABLE recipes(meal_type TEXT);",
            ("subjective_daily", "soreness_note"): "CREATE TABLE subjective_daily(soreness_note TEXT);",
            ("daily_metrics", "hrv_ms"): "CREATE TABLE daily_metrics(hrv_ms REAL);",
        }
        ddl = declarations[(table, column)]
    return _expected_columns(ddl, table)[column]


def _validate_guard(c: sqlite3.Connection, table: str, column: str, migration: int) -> None:
    if not _table_exists(c, table):
        raise SchemaError("incompatible_schema", f"guarded table is absent: {table}")
    got = _column_map(c, table).get(column)
    if got is None:
        return
    want = _expected_guard_column(table, column, migration)
    if (_affinity(got["type"]), int(got["notnull"]), _default(got["dflt_value"])) != (
        _affinity(want["type"]), int(want["notnull"]), _default(want["dflt_value"])
    ):
        raise SchemaError("incompatible_schema", f"{table}.{column} has an incompatible declaration")
    if (table, column) in {("exercise_submuscles", "confidence"), ("pain_log", "onset_precision")}:
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        expected = "CHECK(confidence IN ('E','B'))" if column == "confidence" else "CHECK(onset_precision IN ('exact','approximate','unknown'))"
        if _sql_key(expected) not in _sql_key(row[0] if row else ""):
            raise SchemaError("incompatible_schema", f"{table}.{column} is missing its check constraint")


def require_table(c: sqlite3.Connection, table: str, columns: tuple[str, ...] = ()) -> None:
    """Non-mutating guard used by legacy commands after migration ownership."""

    if not _table_exists(c, table):
        raise SchemaError("schema_migration_required", f"required schema is absent: {table}")
    present = _column_map(c, table)
    missing = [name for name in columns if name not in present]
    if missing:
        raise SchemaError("schema_migration_required", f"required schema is absent: {table}.{missing[0]}")


def require_version(c: sqlite3.Connection, minimum: int) -> None:
    """Require an applied, checksum-valid migration version without writing."""

    current = _current_version(c)
    if current < minimum:
        raise SchemaError(
            "schema_migration_required",
            f"schema version {minimum} is required; current version is {current}",
        )
    if minimum >= 1:
        _preflight_001(c)
    if minimum >= 2:
        _preflight_002(c)
        _validate_indexes(c)
    if minimum >= 3:
        _preflight_003(c)
        _validate_phase3_indexes(c)
    if minimum >= 4:
        _preflight_004(c)
        _validate_phase5_objects(
            c, provider_neutral=_existing_phase5_provider_neutral(c),
        )


def recorded_version(c: sqlite3.Connection) -> int:
    """Return the checksum-validated applied version without writing."""

    return _current_version(c)


def _validate_ledger(c: sqlite3.Connection) -> None:
    _validate_table(c, "schema_migrations", LEDGER_DDL)


def _current_version(c: sqlite3.Connection) -> int:
    if not _table_exists(c, "schema_migrations"):
        return 0
    _validate_ledger(c)
    rows = list(c.execute("SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version"))
    versions = [int(r["version"]) for r in rows]
    if versions and versions != list(range(1, max(versions) + 1)):
        raise SchemaError("incompatible_schema", "schema migration ledger contains a version gap")
    for row in rows:
        migration = MIGRATION_BY_VERSION.get(int(row["version"]))
        if migration is None:
            raise SchemaError("unsupported_schema_version", f"unknown recorded migration {row['version']}")
        expected_checksums = {migration.checksum}
        if migration.version == 1:
            expected_checksums.add(LEGACY_MIGRATION_001_CHECKSUM)
        if row["name"] != migration.name or row["checksum_sha256"] not in expected_checksums:
            raise SchemaError("migration_checksum_mismatch", f"recorded migration {row['version']} differs from this code")
    current = max(versions, default=0)
    if current:
        # A recognized historical checksum is not sufficient by itself: its
        # corresponding complete catalog shape must also match.
        _lab_catalog_ddl(
            c, current=current,
            legacy=rows[0]["checksum_sha256"] == LEGACY_MIGRATION_001_CHECKSUM,
        )
    return current


def _lab_catalog_sql_key(sql: str) -> str:
    # SQLite keywords/identifier quoting are cosmetic, but confidence values
    # and defaults are case-sensitive data. Preserve single-quoted literals.
    pieces = re.split(r"('(?:''|[^'])*')", sql)
    return "".join(
        piece if index % 2 else _sql_key(piece)
        for index, piece in enumerate(pieces)
    ).replace("createtableifnotexists", "createtable", 1)


def _lab_catalog_ddl(
    c: sqlite3.Connection, *, current: int, legacy: bool,
) -> str:
    if current >= 7:
        candidates = (LAB_CATALOG_V7_DDL,)
    elif legacy:
        candidates = (LEGACY_LAB_CATALOG_DDL,)
    else:
        # Fresh SCHEMA.sql already describes the latest exact shape. As with
        # provider-neutral synthesis, recording an intentional older target
        # may adopt that shape while truthfully leaving Migration 007 pending.
        candidates = (LAZY_TABLE_DDL["lab_catalog"], LAB_CATALOG_V7_DDL)
    for ddl in candidates:
        try:
            _validate_table(c, "lab_catalog", ddl)
            actual = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='lab_catalog'"
            ).fetchone()[0]
            if _lab_catalog_sql_key(actual) != _lab_catalog_sql_key(ddl):
                raise SchemaError("incompatible_schema", "lab catalog SQL differs from its recorded profile")
        except SchemaError:
            if ddl == candidates[-1]:
                raise
        else:
            return ddl
    raise AssertionError("lab catalog has no declared profile")


def _existing_lab_catalog_ddl(c: sqlite3.Connection) -> str:
    current = _current_version(c)
    legacy = False
    if current:
        legacy = c.execute(
            "SELECT checksum_sha256 FROM schema_migrations WHERE version=1"
        ).fetchone()[0] == LEGACY_MIGRATION_001_CHECKSUM
    return _lab_catalog_ddl(c, current=current, legacy=legacy)


def _preflight_001(c: sqlite3.Connection) -> None:
    if _table_exists(c, "schema_migrations"):
        _validate_ledger(c)
    guarded_by_table: dict[str, set[str]] = {}
    for table, column in GUARDED_001:
        guarded_by_table.setdefault(table, set()).add(column)
    for table, ddl in LAZY_TABLE_DDL.items():
        if _table_exists(c, table):
            if table == "lab_catalog":
                ddl = _existing_lab_catalog_ddl(c)
            future = {"reported_onset_date", "onset_precision"} if table == "pain_log" else set()
            _validate_table(
                c, table, ddl, allow_missing=guarded_by_table.get(table, set()),
                allow_extra=future,
            )
    for table, column in GUARDED_001:
        if not _table_exists(c, table) and table in LAZY_TABLE_DDL:
            continue
        _validate_guard(c, table, column, 1)


def _apply_001(c: sqlite3.Connection) -> None:
    c.execute(LEDGER_DDL)
    for table, ddl in LAZY_TABLE_DDL.items():
        if not _table_exists(c, table):
            c.execute(ddl)
    for (table, column), ddl in GUARDED_001.items():
        if column not in _column_map(c, table):
            c.execute(ddl)
    c.execute("UPDATE hevy_sets SET source='hevy' WHERE source IS NULL")
    c.execute("UPDATE exercise_muscles SET source='manual' WHERE source IS NULL")
    c.execute("UPDATE body_metrics SET source='manual' WHERE source IS NULL")
    if "hrv_sdnn" in _column_map(c, "daily_metrics"):
        c.execute("UPDATE daily_metrics SET hrv_ms=hrv_sdnn WHERE hrv_ms IS NULL AND hrv_sdnn IS NOT NULL")
    for table, ddl in LAZY_TABLE_DDL.items():
        if table == "lab_catalog":
            ddl = _existing_lab_catalog_ddl(c)
        future = {"reported_onset_date", "onset_precision"} if table == "pain_log" else set()
        _validate_table(c, table, ddl, allow_extra=future)
    for table, column in GUARDED_001:
        _validate_guard(c, table, column, 1)


def _preflight_002(c: sqlite3.Connection, *, prospective_from_zero: bool = False) -> None:
    for table, ddl in EVENT_TABLE_DDL.items():
        if _table_exists(c, table):
            _validate_table(c, table, ddl)
    for table, column in GUARDED_002:
        if prospective_from_zero and not _table_exists(c, table) and table == "pain_log":
            continue
        _validate_guard(c, table, column, 2)
    _validate_existing_indexes(c)


def _validate_existing_indexes(c: sqlite3.Connection) -> None:
    """Reject conflicting ownership of a Phase 2 index before any migration write."""
    for name, ddl in EVENT_INDEX_DDL.items():
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if row is None:
            continue
        got = _sql_key(row[0]).replace("createindex", "createindexifnotexists", 1)
        if got != _sql_key(ddl):
            raise SchemaError("incompatible_schema", f"index definition differs: {name}")


def _validate_indexes(c: sqlite3.Connection) -> None:
    for name, ddl in EVENT_INDEX_DDL.items():
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if row is None:
            raise SchemaError("incompatible_schema", f"required index is absent: {name}")
        # CREATE INDEX and CREATE INDEX IF NOT EXISTS are semantically identical.
        got = _sql_key(row[0]).replace("createindex", "createindexifnotexists", 1)
        if got != _sql_key(ddl):
            raise SchemaError("incompatible_schema", f"index definition differs: {name}")


def _apply_002(c: sqlite3.Connection) -> None:
    for table, ddl in EVENT_TABLE_DDL.items():
        if not _table_exists(c, table):
            c.execute(ddl)
    for ddl in EVENT_INDEX_DDL.values():
        c.execute(ddl)
    for (table, column), ddl in GUARDED_002.items():
        if column not in _column_map(c, table):
            c.execute(ddl)
    for table, ddl in EVENT_TABLE_DDL.items():
        _validate_table(c, table, ddl)
    for table, column in GUARDED_002:
        _validate_guard(c, table, column, 2)
    _validate_indexes(c)


def _require_training_snapshot_sources(c: sqlite3.Connection) -> None:
    required = {
        "training_schedule": {"weekday", "routine_name"},
        "routines": {
            "routine_name", "exercise_title", "ex_order", "target_sets",
            "target_reps", "target_weight_kg",
        },
    }
    for table, columns in required.items():
        if not _table_exists(c, table):
            raise SchemaError("incompatible_schema", f"required table is absent: {table}")
        missing = columns - set(_column_map(c, table))
        if missing:
            raise SchemaError(
                "incompatible_schema",
                f"{table} is missing required column(s): {', '.join(sorted(missing))}",
            )


def _validate_existing_phase3_indexes(c: sqlite3.Connection) -> None:
    """Reject conflicting ownership of a Phase 3 index before any write."""

    for name, ddl in PHASE3_INDEX_DDL.items():
        row = c.execute(
            "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            continue
        if row["type"] != "index":
            raise SchemaError("incompatible_schema", f"schema object conflicts with index: {name}")
        got = _sql_key(row["sql"]).replace("createindex", "createindexifnotexists", 1)
        if got != _sql_key(ddl):
            raise SchemaError("incompatible_schema", f"index definition differs: {name}")


def _validate_phase3_indexes(c: sqlite3.Connection) -> None:
    for name, ddl in PHASE3_INDEX_DDL.items():
        row = c.execute(
            "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
        ).fetchone()
        if row is None or row["type"] != "index":
            raise SchemaError("incompatible_schema", f"required index is absent: {name}")
        got = _sql_key(row["sql"]).replace("createindex", "createindexifnotexists", 1)
        if got != _sql_key(ddl):
            raise SchemaError("incompatible_schema", f"index definition differs: {name}")


def _normalized_named_object_sql(sql: str, object_type: str) -> str:
    normalized = _sql_key(sql)
    if object_type == "index":
        if normalized.startswith("createuniqueindexifnotexists"):
            return normalized
        if normalized.startswith("createuniqueindex"):
            return normalized.replace(
                "createuniqueindex", "createuniqueindexifnotexists", 1
            )
        if normalized.startswith("createindexifnotexists"):
            return normalized
        return normalized.replace("createindex", "createindexifnotexists", 1)
    if object_type == "trigger":
        if normalized.startswith("createtriggerifnotexists"):
            return normalized
        return normalized.replace("createtrigger", "createtriggerifnotexists", 1)
    return normalized


def _phase5_explicit_object_names(
    c: sqlite3.Connection, object_type: str
) -> set[str]:
    table_names = tuple(PHASE5_TABLE_DDL)
    placeholders = ",".join("?" for _ in table_names)
    return {
        row["name"]
        for row in c.execute(
            f"""SELECT name
                  FROM sqlite_master
                 WHERE type=? AND tbl_name IN ({placeholders})
                   AND sql IS NOT NULL""",
            (object_type, *table_names),
        )
    }


def _validate_existing_phase5_objects(
    c: sqlite3.Connection, *, provider_neutral: bool = False,
) -> None:
    """Reject partial/conflicting Migration 004 ownership before any write."""

    for name, ddl in _phase5_table_definitions(
        provider_neutral=provider_neutral,
    ).items():
        row = c.execute(
            "SELECT type FROM sqlite_master WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            continue
        if row["type"] != "table":
            raise SchemaError(
                "incompatible_schema", f"schema object conflicts with table: {name}"
            )
        _validate_table(c, name, ddl)
    for object_type, definitions in (
        ("index", PHASE5_INDEX_DDL),
        ("trigger", PHASE5_TRIGGER_DDL),
    ):
        unexpected = _phase5_explicit_object_names(c, object_type) - set(definitions)
        if unexpected:
            raise SchemaError(
                "incompatible_schema",
                f"unexpected Phase 5 {object_type}(s): {', '.join(sorted(unexpected))}",
            )
        for name, ddl in definitions.items():
            row = c.execute(
                "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                continue
            if row["type"] != object_type:
                raise SchemaError(
                    "incompatible_schema",
                    f"schema object conflicts with {object_type}: {name}",
                )
            if _normalized_named_object_sql(row["sql"], object_type) != \
                    _normalized_named_object_sql(ddl, object_type):
                raise SchemaError(
                    "incompatible_schema",
                    f"{object_type} definition differs: {name}",
                )


def _phase5_table_definitions(*, provider_neutral: bool) -> dict[str, str]:
    definitions = dict(PHASE5_TABLE_DDL)
    if provider_neutral:
        definitions["synthesis_runs"] = SYNTHESIS_RUNS_V6_DDL
        definitions["hypothesis_annotations"] = HYPOTHESIS_ANNOTATIONS_V6_DDL
    return definitions


def _existing_phase5_provider_neutral(c: sqlite3.Connection) -> bool:
    """Identify either exact coupled-table generation, rejecting mixed shapes."""

    generation: bool | None = None
    for name, neutral_ddl in (
        ("synthesis_runs", SYNTHESIS_RUNS_V6_DDL),
        ("hypothesis_annotations", HYPOTHESIS_ANNOTATIONS_V6_DDL),
    ):
        if not _table_exists(c, name):
            continue
        try:
            _validate_table(c, name, neutral_ddl)
            current = True
        except SchemaError:
            # Raise the exact compatibility error if this is neither frozen
            # Migration 004 nor Migration 006 shape.
            _validate_table(c, name, PHASE5_TABLE_DDL[name])
            current = False
        if generation is not None and current != generation:
            raise SchemaError(
                "incompatible_schema",
                "provider-coupled Phase 5 tables have mixed generations",
            )
        generation = current
    return generation is True


def _validate_phase5_objects(
    c: sqlite3.Connection, *, provider_neutral: bool = False,
) -> None:
    for name, ddl in _phase5_table_definitions(
        provider_neutral=provider_neutral,
    ).items():
        _validate_table(c, name, ddl)
    for object_type, definitions in (
        ("index", PHASE5_INDEX_DDL),
        ("trigger", PHASE5_TRIGGER_DDL),
    ):
        actual_names = _phase5_explicit_object_names(c, object_type)
        if actual_names != set(definitions):
            raise SchemaError(
                "incompatible_schema",
                f"Phase 5 {object_type} inventory differs",
            )
        for name, ddl in definitions.items():
            row = c.execute(
                "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            if row is None or row["type"] != object_type:
                raise SchemaError(
                    "incompatible_schema", f"required {object_type} is absent: {name}"
                )
            if _normalized_named_object_sql(row["sql"], object_type) != \
                    _normalized_named_object_sql(ddl, object_type):
                raise SchemaError(
                    "incompatible_schema",
                    f"{object_type} definition differs: {name}",
                )


def _preflight_004(c: sqlite3.Connection) -> None:
    if _current_version(c) >= 6:
        _validate_phase5_objects(c, provider_neutral=True)
    else:
        _validate_existing_phase5_objects(
            c, provider_neutral=_existing_phase5_provider_neutral(c),
        )


def _apply_004(c: sqlite3.Connection) -> None:
    provider_neutral = _existing_phase5_provider_neutral(c)
    for name, ddl in _phase5_table_definitions(
        provider_neutral=provider_neutral,
    ).items():
        if not _table_exists(c, name):
            c.execute(ddl)
    for definitions in (PHASE5_INDEX_DDL, PHASE5_TRIGGER_DDL):
        for ddl in definitions.values():
            c.execute(ddl)
    _validate_phase5_objects(c, provider_neutral=provider_neutral)


def _preflight_005(c: sqlite3.Connection) -> None:
    if _table_exists(c, "recipe_restock_state"):
        _validate_table(c, "recipe_restock_state", RESTOCK_STATE_DDL)


def _apply_005(c: sqlite3.Connection) -> None:
    if not _table_exists(c, "recipe_restock_state"):
        c.execute(RESTOCK_STATE_DDL)
    _validate_table(c, "recipe_restock_state", RESTOCK_STATE_DDL)


_SYNTHESIS_RUN_COLUMNS = (
    "synthesis_id", "analysis_batch_id", "cadence", "reason_code",
    "cutoff_date", "evidence_fingerprint", "context_version",
    "prompt_sha256", "model_id", "provider", "finding_ids_json",
    "hypothesis_ids_json", "narrative_md", "rendered_md", "status",
    "no_message_reason_code", "created_at", "completed_at",
)
_HYPOTHESIS_ANNOTATION_COLUMNS = (
    "annotation_id", "hypothesis_id", "evaluation_id", "annotation_kind",
    "content", "source", "synthesis_id", "context_version",
    "prompt_sha256", "model_id", "provider", "supersedes_id",
    "input_sha256", "created_at",
)


def _preflight_006(c: sqlite3.Connection) -> None:
    _validate_phase5_objects(
        c, provider_neutral=_existing_phase5_provider_neutral(c),
    )


def _apply_006(c: sqlite3.Connection) -> None:
    """Rebuild only the two provider-coupled tables, preserving every row."""

    if _existing_phase5_provider_neutral(c):
        _validate_phase5_objects(c, provider_neutral=True)
        return

    c.execute("PRAGMA defer_foreign_keys=ON")
    synthesis_temp_ddl = SYNTHESIS_RUNS_V6_DDL.replace(
        "CREATE TABLE IF NOT EXISTS synthesis_runs",
        "CREATE TABLE synthesis_runs_v6",
        1,
    )
    annotation_temp_ddl = (
        HYPOTHESIS_ANNOTATIONS_V6_DDL.replace(
            "CREATE TABLE IF NOT EXISTS hypothesis_annotations",
            "CREATE TABLE hypothesis_annotations_v6",
            1,
        )
        .replace(
            "REFERENCES synthesis_runs(synthesis_id)",
            "REFERENCES synthesis_runs_v6(synthesis_id)",
        )
        .replace(
            "REFERENCES hypothesis_annotations(annotation_id)",
            "REFERENCES hypothesis_annotations_v6(annotation_id)",
        )
    )
    c.execute(synthesis_temp_ddl)
    synthesis_columns = ",".join(_SYNTHESIS_RUN_COLUMNS)
    c.execute(
        f"INSERT INTO synthesis_runs_v6({synthesis_columns}) "
        f"SELECT {synthesis_columns} FROM synthesis_runs"
    )
    c.execute(annotation_temp_ddl)
    annotation_columns = ",".join(_HYPOTHESIS_ANNOTATION_COLUMNS)
    c.execute(
        f"INSERT INTO hypothesis_annotations_v6({annotation_columns}) "
        f"SELECT {annotation_columns} FROM hypothesis_annotations"
    )

    c.execute("DROP TABLE hypothesis_annotations")
    c.execute("DROP TABLE synthesis_runs")
    c.execute("ALTER TABLE synthesis_runs_v6 RENAME TO synthesis_runs")
    c.execute(
        "ALTER TABLE hypothesis_annotations_v6 RENAME TO hypothesis_annotations"
    )
    for name in (
        "hypothesis_annotations_latest_idx",
        "hypothesis_annotation_superseded_once_idx",
    ):
        c.execute(PHASE5_INDEX_DDL[name])
    for name in (
        "hypothesis_annotations_no_update",
        "hypothesis_annotations_no_delete",
    ):
        c.execute(PHASE5_TRIGGER_DDL[name])
    _validate_phase5_objects(c, provider_neutral=True)



_LAB_CATALOG_COLUMNS = (
    "canonical", "display", "panel", "unit", "ref_low", "ref_high",
    "plaus_low", "plaus_high", "max_delta", "delta_kind", "aliases",
    "source", "confidence", "created_at",
)


def _validate_lab_catalog_dependencies(c: sqlite3.Connection) -> None:
    # No attached custom objects or incoming dependencies belong to the
    # published catalog contract. Refuse them before DROP rather than silently
    # discard an installation's index, trigger, view or referencing table.
    objects = list(c.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL"
    ))
    for row in objects:
        if row["name"].casefold() == "lab_catalog":
            continue
        if row["tbl_name"].casefold() == "lab_catalog" or (
            row["type"] in {"view", "trigger"}
            and _references_column(row["sql"], "lab_catalog")
        ):
            raise SchemaError("incompatible_schema", "lab catalog has an unsupported dependent object")
        if row["type"] == "table":
            escaped = row["name"].replace("'", "''")
            if any(fk["table"].casefold() == "lab_catalog" for fk in c.execute(
                f"PRAGMA foreign_key_list('{escaped}')"
            )):
                raise SchemaError("incompatible_schema", "lab catalog has an unsupported incoming foreign key")


def _preflight_007(c: sqlite3.Connection) -> None:
    if _table_exists(c, "lab_catalog"):
        _existing_lab_catalog_ddl(c)
        _validate_lab_catalog_dependencies(c)
    if c.execute(
        "SELECT 1 FROM sqlite_master WHERE name COLLATE NOCASE='lab_catalog_v7'"
    ).fetchone() is not None:
        raise SchemaError("incompatible_schema", "temporary lab catalog object already exists")


def _apply_007(c: sqlite3.Connection) -> None:
    """Preserve every value while supporting both recorded confidence labels."""
    if _existing_lab_catalog_ddl(c) == LAB_CATALOG_V7_DDL:
        return
    c.execute(LAB_CATALOG_V7_DDL.replace(
        "CREATE TABLE IF NOT EXISTS lab_catalog",
        "CREATE TABLE lab_catalog_v7", 1,
    ))
    columns = ",".join(_LAB_CATALOG_COLUMNS)
    c.execute(
        f"INSERT INTO lab_catalog_v7({columns}) SELECT {columns} FROM lab_catalog"
    )
    c.execute("DROP TABLE lab_catalog")
    c.execute("ALTER TABLE lab_catalog_v7 RENAME TO lab_catalog")
    _validate_table(c, "lab_catalog", LAB_CATALOG_V7_DDL)
    _validate_lab_catalog_dependencies(c)


def _preflight_003(c: sqlite3.Connection) -> None:
    _require_training_snapshot_sources(c)
    for table, ddl in PHASE3_TABLE_DDL.items():
        if _table_exists(c, table):
            _validate_table(c, table, ddl)
    _validate_existing_phase3_indexes(c)


def canonical_training_plan_snapshot(c: sqlite3.Connection) -> tuple[str, str]:
    """Return the stable, compact JSON snapshot owned by migration 003."""

    _require_training_snapshot_sources(c)
    schedule = [
        {"weekday": row["weekday"], "routine_name": row["routine_name"]}
        for row in c.execute(
            "SELECT weekday,routine_name FROM training_schedule ORDER BY weekday"
        )
    ]
    routines = [
        {
            "routine_name": row["routine_name"],
            "exercise_title": row["exercise_title"],
            "ex_order": row["ex_order"],
            "target_sets": row["target_sets"],
            "target_reps": row["target_reps"],
            "target_weight_kg": row["target_weight_kg"],
        }
        for row in c.execute(
            """SELECT routine_name,exercise_title,ex_order,target_sets,target_reps,
                      target_weight_kg
                 FROM routines
             ORDER BY routine_name,exercise_title"""
        )
    ]
    options = {
        "ensure_ascii": False,
        "allow_nan": False,
        "sort_keys": True,
        "separators": (",", ":"),
    }
    return json.dumps(schedule, **options), json.dumps(routines, **options)


def _preflight_003_seed(c: sqlite3.Connection) -> None:
    """Prove the migration-owned seed is serializable before any step writes."""

    try:
        canonical_training_plan_snapshot(c)
    except (TypeError, ValueError) as exc:
        raise SchemaError(
            "incompatible_schema", "training plan cannot be serialized canonically",
        ) from exc


def append_training_plan_revision(
    c: sqlite3.Connection, *, effective_from: str, source: str
) -> int:
    """Append one full plan snapshot inside the caller's open transaction."""

    schedule_json, routines_json = canonical_training_plan_snapshot(c)
    latest = c.execute(
        "SELECT id FROM training_plan_revisions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    cursor = c.execute(
        """INSERT INTO training_plan_revisions(
               effective_from,schedule_json,routines_json,source,supersedes_id)
           VALUES(?,?,?,?,?)""",
        (effective_from, schedule_json, routines_json, source,
         latest["id"] if latest else None),
    )
    return int(cursor.lastrowid)


def _apply_003(c: sqlite3.Connection) -> None:
    for table, ddl in PHASE3_TABLE_DDL.items():
        if not _table_exists(c, table):
            c.execute(ddl)
    for ddl in PHASE3_INDEX_DDL.values():
        c.execute(ddl)
    for table, ddl in PHASE3_TABLE_DDL.items():
        _validate_table(c, table, ddl)
    _validate_phase3_indexes(c)
    append_training_plan_revision(
        c,
        effective_from=datetime.now(CANON_TZ).date().isoformat(),
        source="migration-003",
    )


def _integrity(c: sqlite3.Connection) -> None:
    fk = list(c.execute("PRAGMA foreign_key_check"))
    if fk:
        raise SchemaError("foreign_key_check_failed", "foreign_key_check returned violations")
    quick = [row[0] for row in c.execute("PRAGMA quick_check")]
    if quick != ["ok"]:
        raise SchemaError("quick_check_failed", "quick_check did not return ok")


def _ro(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c


def _migration_rows(c: sqlite3.Connection) -> list[dict]:
    if not _table_exists(c, "schema_migrations"):
        return []
    return [dict(r) for r in c.execute(
        "SELECT version,name,checksum_sha256,applied_at,code_version FROM schema_migrations ORDER BY version"
    )]


def schema_status(db_path: str) -> dict:
    c = _ro(db_path)
    try:
        current = _current_version(c)
        if current >= 1:
            _preflight_001(c)
        if current >= 2:
            _preflight_002(c)
            _validate_indexes(c)
        if current >= 3:
            _preflight_003(c)
            _validate_phase3_indexes(c)
        if current >= 4:
            _preflight_004(c)
            _validate_phase5_objects(
                c, provider_neutral=_existing_phase5_provider_neutral(c),
            )
        if current >= 5:
            _preflight_005(c)
        if current >= 7:
            _preflight_007(c)
        applied = _migration_rows(c)
        return {
            "ok": True,
            "contract": SCHEMA_CONTRACT,
            "current_version": current,
            "target_version": AUTONOMOUS_SCHEMA_VERSION,
            "ledger_present": _table_exists(c, "schema_migrations"),
            "status": "up_to_date" if current == AUTONOMOUS_SCHEMA_VERSION else "pending",
            "applied": applied,
            "pending": [
                {"version": m.version, "name": m.name, "checksum_sha256": m.checksum}
                for m in MIGRATIONS if m.version > current
            ],
        }
    finally:
        c.close()


def _validate_target(target: int, current: int) -> None:
    if not isinstance(target, int) or not 0 <= target <= AUTONOMOUS_SCHEMA_VERSION:
        raise SchemaError("validation_error", f"--to must be between 0 and {AUTONOMOUS_SCHEMA_VERSION}", validation=True)
    if target < current:
        raise SchemaError("down_migration_forbidden", "down-migrations are not supported", validation=True)


def schema_plan(db_path: str, target: int) -> dict:
    c = _ro(db_path)
    try:
        current = _current_version(c)
        _validate_target(target, current)
        if current >= 1:
            _preflight_001(c)
        if current >= 2:
            _preflight_002(c)
            _validate_indexes(c)
        if current >= 3:
            _preflight_003(c)
            _validate_phase3_indexes(c)
        if current >= 4:
            _preflight_004(c)
            _validate_phase5_objects(
                c, provider_neutral=_existing_phase5_provider_neutral(c),
            )
        if current >= 5:
            _preflight_005(c)
        if current < 1 <= target:
            _preflight_001(c)
        if current < 2 <= target:
            _preflight_002(c, prospective_from_zero=current == 0)
        if current < 3 <= target:
            _preflight_003(c)
            _preflight_003_seed(c)
        if current < 4 <= target:
            _preflight_004(c)
        if current < 5 <= target:
            _preflight_005(c)
        if current < 6 <= target:
            _preflight_006(c)
        if current < 7 <= target or current >= 7:
            _preflight_007(c)
        steps = [
            {"version": m.version, "name": m.name, "checksum_sha256": m.checksum,
             "transaction": "BEGIN IMMEDIATE", "destructive": m.version in {6, 7}}
            for m in MIGRATIONS if current < m.version <= target
        ]
        return {
            "ok": True, "contract": SCHEMA_CONTRACT, "from_version": current,
            "to_version": target, "status": "up_to_date" if not steps else "planned",
            "steps": steps,
        }
    finally:
        c.close()


def resolve_code_version() -> str:
    """Resolve the exact deploying commit without making it a CLI flag."""

    value = os.environ.get("HERMES_CODE_VERSION", "").strip().lower()
    deployed_manifest = Path(
        os.environ.get(
            "HERMES_DEPLOYED_MANIFEST",
            "/opt/hermes/toolkit/DEPLOYED.manifest",
        )
    )
    candidates = [
        Path(__file__).resolve().parents[1] / "DEPLOYED.manifest",
        deployed_manifest,
    ]
    if not value:
        for path in candidates:
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("git_commit="):
                        value = line.split("=", 1)[1].strip().lower()
                        break
            except OSError:
                continue
            if value:
                break
    if not value:
        try:
            root = Path(__file__).resolve().parents[2]
            value = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
                stderr=subprocess.DEVNULL, timeout=3,
            ).strip().lower()
        except (OSError, subprocess.SubprocessError):
            value = ""
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SchemaError("code_version_unavailable", "exact deploying Git commit is unavailable")
    return value


def migrate(db_path: str, target: int, expected_from: int, code_version: str | None = None) -> dict:
    # Gate against the read-only observed version before opening any writer.
    initial = schema_status(db_path)["current_version"]
    _validate_target(target, initial)
    if expected_from != initial:
        raise SchemaError(
            "expected_from_mismatch",
            f"expected schema version {expected_from}, found {initial}",
            validation=True,
        )
    # Validate every requested step before opening a writer.  This prevents a
    # later incompatible shape from leaving an earlier migration committed.
    schema_plan(db_path, target)
    if target == initial:
        c = _ro(db_path)
        try:
            _integrity(c)
        finally:
            c.close()
        return {
            "ok": True, "contract": SCHEMA_CONTRACT, "from_version": initial,
            "to_version": target, "status": "up_to_date", "applied": [],
        }

    code_version = code_version or resolve_code_version()
    applied: list[dict] = []
    for migration in MIGRATIONS:
        if not initial < migration.version <= target:
            continue
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            # SQLite cannot rebuild a referenced parent table with foreign-key
            # enforcement enabled: dropping the old parent records a deferred
            # violation that is not cleared when the replacement takes its
            # name.  Migration 006 therefore follows SQLite's documented table
            # rebuild procedure with enforcement off, while still requiring a
            # clean foreign_key_check before the transaction can commit.
            c.execute(
                "PRAGMA foreign_keys={}".format(
                    "OFF" if migration.version == 6 else "ON"
                )
            )
            c.execute("BEGIN IMMEDIATE")
            current = _current_version(c)
            if current != migration.version - 1:
                raise SchemaError("expected_from_mismatch", f"schema changed concurrently; found {current}")
            if migration.version == 1:
                _preflight_001(c)
                _apply_001(c)
            elif migration.version == 2:
                _preflight_002(c)
                _apply_002(c)
            elif migration.version == 3:
                _preflight_003(c)
                _apply_003(c)
            elif migration.version == 4:
                _preflight_004(c)
                _apply_004(c)
            elif migration.version == 5:
                _preflight_005(c)
                _apply_005(c)
            elif migration.version == 6:
                _preflight_006(c)
                _apply_006(c)
            elif migration.version == 7:
                _preflight_007(c)
                _apply_007(c)
            c.execute(
                "INSERT INTO schema_migrations(version,name,checksum_sha256,applied_at,code_version) VALUES(?,?,?,?,?)",
                (migration.version, migration.name, migration.checksum,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), code_version),
            )
            _integrity(c)
            c.commit()
            if migration.version == 6:
                c.execute("PRAGMA foreign_keys=ON")
                _integrity(c)
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        verify = _ro(db_path)
        try:
            _integrity(verify)
            if _current_version(verify) != migration.version:
                raise SchemaError("migration_verification_failed", "ledger version did not persist")
        finally:
            verify.close()
        applied.append({"version": migration.version, "name": migration.name,
                        "checksum_sha256": migration.checksum})
    return {
        "ok": True, "contract": SCHEMA_CONTRACT, "from_version": initial,
        "to_version": target, "status": "migrated", "applied": applied,
    }
