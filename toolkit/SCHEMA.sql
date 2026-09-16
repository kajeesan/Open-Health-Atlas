-- Hermes health DB — COMPLETE schema (structure only, NO data)
-- The authoritative current schema is emitted by: python3 toolkit/health.py schema

CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum_sha256 TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  code_version TEXT NOT NULL
);

CREATE VIEW air_brief AS SELECT date,location,european_aqi_mean,european_aqi_max,pm2_5_ugm3,pm10_ugm3,
  grass_pollen,birch_pollen FROM air_quality;

CREATE VIEW weather_brief AS SELECT date,location,condition,temp_max_c,temp_min_c,feels_like_max_c,
  daylight_hours,sunshine_hours,uv_index_max,precipitation_mm,wind_speed_max_ms,humidity_mean_pct FROM weather;

CREATE TABLE air_quality (
  date TEXT PRIMARY KEY, location TEXT, european_aqi_mean REAL, european_aqi_max REAL,
  pm2_5_ugm3 REAL, pm10_ugm3 REAL, ozone_ugm3 REAL, no2_ugm3 REAL, so2_ugm3 REAL, co_ugm3 REAL,
  alder_pollen REAL, birch_pollen REAL, grass_pollen REAL, mugwort_pollen REAL, olive_pollen REAL, ragweed_pollen REAL,
  source TEXT DEFAULT 'open-meteo-aqi', ingested_at TEXT DEFAULT (datetime('now')) );

CREATE TABLE assessments (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, scale TEXT, part TEXT, score REAL, max_score REAL,
  subscores TEXT, notes TEXT, source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE body_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, weight_kg REAL, waist_cm REAL, chest_cm REAL,
  arm_cm REAL, thigh_cm REAL, hip_cm REAL, neck_cm REAL, body_fat_pct REAL, photo_ref TEXT, notes TEXT,
  source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE "daily_metrics" (
  date TEXT,
  source TEXT NOT NULL DEFAULT 'apple',
  "resting_hr" REAL,
  -- HRV in ms — neutral name (renamed from hrv_sdnn 2026-07-19, now that the
  -- Apple pipeline is retired). The ALGORITHM STILL VARIES BY SOURCE (T55
  -- owner flag 2): apple-era rows (<=2026-05-31) = SDNN (HealthKit), fitbit
  -- rows = RMSSD (Google Health API daily "averageHeartRateVariability
  -- Milliseconds"). Comparable only WITHIN a source — never merge/average
  -- across sources. Legacy DBs still carry hrv_sdnn (never dropped);
  -- Migration 001 owns the sole documented compatibility copy to hrv_ms.
  "hrv_ms" REAL,
  "hr_min" REAL,
  "hr_avg" REAL,
  "hr_max" REAL,
  "steps" REAL,
  "active_energy_kcal" REAL,
  "basal_energy_kcal" REAL,
  "exercise_min" REAL,
  "distance_km" REAL,
  "flights" REAL,
  "respiratory_rate" REAL,
  "spo2_pct" REAL,
  "walking_hr_avg" REAL,
  "sleep_hours" REAL,
  PRIMARY KEY (date, source)
);

CREATE TABLE exercise_muscles(
  exercise_title TEXT, muscle TEXT, weight REAL,
  source TEXT DEFAULT 'manual',  -- 'manual' = curated (sync never touches) | 'hevy' = template tags (refreshed by sync)
  PRIMARY KEY(exercise_title, muscle));

CREATE TABLE habits_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, habit TEXT, done INTEGER, streak INTEGER,
  xp INTEGER, notes TEXT, source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE hevy_sets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT, workout_title TEXT, start_time TEXT, end_time TEXT, description TEXT,
  exercise_title TEXT, superset_id TEXT, exercise_notes TEXT,
  set_index INTEGER, set_type TEXT, weight_kg REAL, reps INTEGER,
  distance_km REAL, duration_seconds REAL, rpe REAL,
  source TEXT DEFAULT 'hevy', ingested_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE intake (date TEXT PRIMARY KEY, water_ml REAL, notes TEXT, source TEXT DEFAULT 'manual',
  ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE labs (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, panel TEXT, test_name TEXT, value REAL, unit TEXT,
  reference_low REAL, reference_high REAL, flag TEXT, notes TEXT,
  source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')));

-- Labs pre-validation catalog (§labs): one row per canonical test. Seeded from
-- a user-supplied catalog (import-lab-catalog) and grown by explicit user
-- confirmation during ingest. `unit`+`ref_*` structural-check an OCR'd
-- row; `plaus_*` are wide "physically possible" safety bounds (catch a
-- dropped-comma 10x OCR error — NOT clinical-normal); `max_delta` gates an
-- implausible jump vs the last result. Config, not logged data.
CREATE TABLE lab_catalog (
  canonical   TEXT PRIMARY KEY,
  display     TEXT,
  panel       TEXT,
  unit        TEXT NOT NULL,
  ref_low     REAL, ref_high REAL,
  plaus_low   REAL NOT NULL, plaus_high REAL NOT NULL,
  max_delta   REAL,
  delta_kind  TEXT NOT NULL DEFAULT 'abs' CHECK(delta_kind IN ('abs','frac')),
  aliases     TEXT,
  source      TEXT NOT NULL,
  confidence  TEXT NOT NULL DEFAULT 'cited' CHECK(confidence IN ('cited','owner-confirmed','user-confirmed')),
  created_at  TEXT DEFAULT (datetime('now')));

CREATE TABLE meal_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id TEXT, portions_remaining REAL,
  grams_per_portion REAL, prepped_on TEXT, notes TEXT, ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE meds_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, drug TEXT, dose_mg REAL, time_taken TEXT,
  onset TEXT, peak_window TEXT, wear_off TEXT, rebound INTEGER, side_effects TEXT, notes TEXT,
  source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE nutrition_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, recipe_id TEXT, food_name TEXT, grams REAL,
  kcal REAL, protein_g REAL, carbs_g REAL, fat_g REAL, fiber_g REAL, notes TEXT,
  source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')),
  time TEXT, meal_type TEXT);

CREATE TABLE nutrient_daily (
  date TEXT NOT NULL, nutrient TEXT NOT NULL, amount REAL NOT NULL, unit TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'cronometer', ingested_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY(date, nutrient, source));

CREATE TABLE nutrition_targets (
  name TEXT PRIMARY KEY, target REAL NOT NULL, updated TEXT DEFAULT (datetime('now')));

CREATE TABLE owner_profile (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE recipe_nutrients (recipe_id TEXT, nutrient TEXT, unit TEXT, per_gram REAL, PRIMARY KEY (recipe_id, nutrient));

CREATE TABLE recipes (recipe_id TEXT PRIMARY KEY, name TEXT, batch_grams REAL, grams_per_portion REAL, portions INTEGER,
  notes TEXT, source TEXT DEFAULT 'cronometer', ingested_at TEXT DEFAULT (datetime('now')),
  meal_type TEXT);  -- breakfast|lunch|dinner|snack, nullable; Migration 001-owned

CREATE TABLE recipe_restock_state (
  recipe_id TEXT PRIMARY KEY,
  action TEXT NOT NULL CHECK(action IN
    ('notified','restock','alternatives','later','skip')),
  threshold REAL NOT NULL DEFAULT 2 CHECK(threshold >= 0 AND threshold <= 1000000),
  portions_at_notice REAL,
  snooze_until TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')));

CREATE TABLE routines(
  routine_name TEXT, exercise_title TEXT, ex_order INTEGER,
  target_sets INTEGER, target_reps INTEGER, target_weight_kg REAL,
  PRIMARY KEY(routine_name, exercise_title));

CREATE TABLE routines_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT (datetime('now')), op TEXT,
  routine_name TEXT, exercise_title TEXT, prior_existed INTEGER, ex_order INTEGER,
  target_sets INTEGER, target_reps INTEGER, target_weight_kg REAL, undone INTEGER DEFAULT 0);

CREATE TABLE skincare_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, slot TEXT, product_id INTEGER, used INTEGER,
  notes TEXT, source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE skincare_products (product_id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT, brand TEXT, product_name TEXT,
  active_ingredients TEXT, active INTEGER DEFAULT 1, notes TEXT, ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE sleep_log (date TEXT PRIMARY KEY, bedtime TEXT, wake_time TEXT, time_asleep_hours REAL, time_in_bed_hours REAL,
  deep_min REAL, rem_min REAL, light_min REAL, awake_min REAL, quality INTEGER, awakenings INTEGER, notes TEXT,
  source TEXT, provenance TEXT, ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE subjective_daily (date TEXT PRIMARY KEY, focus INTEGER, energy INTEGER, mood INTEGER,
  emotional_regulation INTEGER, anxiety INTEGER, motivation INTEGER, stress INTEGER,
  caffeine_mg REAL, alcohol_units REAL, brain_dump TEXT, notes TEXT,
  source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')), day_rating INTEGER,
  soreness_note TEXT);

CREATE TABLE commitments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, identity TEXT,
  trigger TEXT, floor TEXT, reward TEXT, active INTEGER DEFAULT 1, created TEXT);

CREATE TABLE commitments_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
  commitment_id INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN ('kept','partly','broke')), why TEXT,
  source TEXT DEFAULT 'ui', ingested_at TEXT DEFAULT (datetime('now')),
  UNIQUE(date, commitment_id));

CREATE TABLE checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, time TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('energy','focus','mood')),
  value INTEGER NOT NULL CHECK(value BETWEEN 1 AND 5), note TEXT,
  source TEXT DEFAULT 'ui', ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE planned_times (
  metric TEXT PRIMARY KEY CHECK(metric IN ('wake','bed','workout','dose')),
  planned TEXT NOT NULL, tolerance_min INTEGER NOT NULL,
  updated TEXT DEFAULT (datetime('now')));

CREATE TABLE supplement_products (
  supplement_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, brand TEXT, dose REAL, unit TEXT, form TEXT,
  schedule TEXT, active INTEGER DEFAULT 1, notes TEXT,
  ingested_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE supplements_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT, supplement_id INTEGER, taken INTEGER, dose_taken REAL, notes TEXT,
  source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')),
  time_taken TEXT
);

CREATE TABLE training_schedule(
  weekday TEXT PRIMARY KEY, routine_name TEXT);

-- §3b sub-muscle drill-down map (curated config, like exercise_muscles).
-- source is NOT NULL so an uncited row cannot exist; populated ONLY by
-- import-submuscle-map from the bundled public anatomy map
-- (docs/authored-submuscle-map.md; rows cite lit:*/biomech keys) — never guessed.
CREATE TABLE exercise_submuscles(
  exercise_title TEXT NOT NULL,
  muscle_group TEXT NOT NULL,          -- one of the 7 radar axes
  sub_region TEXT NOT NULL,            -- e.g. 'glute med', 'rear delt'
  weight REAL NOT NULL DEFAULT 1.0,    -- tiered emphasis (1.0/0.66/0.4/0.2)
  laterality TEXT NOT NULL DEFAULT 'bilateral'
      CHECK(laterality IN ('left','right','bilateral')),
  source TEXT NOT NULL,                -- the citation tag; required
  approx INTEGER NOT NULL DEFAULT 0,   -- 1 = approximate (mechanism-based) emphasis
  iso INTEGER NOT NULL DEFAULT 0,      -- 1 = isometric (stability) contribution
  confidence TEXT NOT NULL DEFAULT 'B'
      CHECK(confidence IN ('E','B')),  -- E = EMG-anchored, B = biomech estimate
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY(exercise_title, sub_region, laterality));

-- §3e fitness tests (append-only log; soft-void, never DELETE). The test
-- vocabulary (CATALOG) lives in health.py, not here.
CREATE TABLE fitness_tests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, movement TEXT NOT NULL, side TEXT NOT NULL DEFAULT 'bilateral',
  load_kg REAL, reps INTEGER, seconds REAL, rating INTEGER, cm REAL,
  degrees REAL, passed INTEGER,
  equipment_note TEXT, source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now')));

-- §3c athletic-radar owner targets (editable; strength targets are per-lift).
CREATE TABLE athletic_targets (
  axis TEXT NOT NULL, lift TEXT NOT NULL DEFAULT '', target REAL NOT NULL,
  updated TEXT DEFAULT (datetime('now')), PRIMARY KEY(axis, lift));

CREATE TABLE vitals (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, time TEXT, systolic INTEGER, diastolic INTEGER,
  resting_hr INTEGER, notes TEXT, source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')));

CREATE TABLE weather (
  date TEXT PRIMARY KEY, location TEXT, weather_code INTEGER, condition TEXT,
  temp_max_c REAL, temp_min_c REAL, feels_like_max_c REAL, feels_like_min_c REAL,
  precipitation_mm REAL, rain_mm REAL, snowfall_cm REAL, precipitation_hours REAL,
  wind_speed_max_ms REAL, wind_gusts_max_ms REAL, wind_dir_deg REAL,
  sunrise TEXT, sunset TEXT, daylight_hours REAL, sunshine_hours REAL,
  uv_index_max REAL, uv_index_clear_sky_max REAL, solar_radiation_mj REAL, et0_mm REAL,
  humidity_mean_pct REAL, pressure_mean_hpa REAL, cloud_cover_mean_pct REAL,
  source TEXT DEFAULT 'open-meteo', ingested_at TEXT DEFAULT (datetime('now')) );

CREATE TABLE workouts (date TEXT, type TEXT, minutes REAL, kcal REAL, km REAL, source TEXT DEFAULT 'apple');

-- §3g physio loop (append-only; soft-void, never DELETE — the deletion law).
-- Capture is collector/agent-only (Telegram + SSH), never panel-reachable; the
-- region/test/drill vocabularies live in code (PAIN_CAUSE_MAP / SELF_TEST_CATALOG
-- / REHAB_CATALOG), not here. Pain is 0–10 NRS (distinct from 1–5 wellbeing).
CREATE TABLE pain_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, region TEXT NOT NULL,
  side TEXT NOT NULL DEFAULT 'central',   -- left|right|central
  intensity INTEGER NOT NULL,             -- 0–10 NRS
  quality TEXT, pattern TEXT,
  flags TEXT,                             -- comma-joined red-flag tokens (PAIN_RED_FLAGS)
  note TEXT, source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  reported_onset_date TEXT,
  onset_precision TEXT CHECK(onset_precision IN ('exact','approximate','unknown')));

CREATE TABLE self_test_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, test TEXT NOT NULL,
  side TEXT NOT NULL DEFAULT 'central',
  result TEXT NOT NULL,                    -- positive|negative|equivocal
  note TEXT, source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now')));

CREATE TABLE exercise_trial_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, drill TEXT NOT NULL,
  target TEXT,                            -- a pain region (optional)
  dose TEXT, response TEXT NOT NULL,      -- 24–48h: better|same|worse
  pain_during INTEGER,                    -- 0–10 NRS (optional)
  note TEXT, source TEXT NOT NULL DEFAULT 'chat',
  voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  created_at TEXT DEFAULT (datetime('now')));

-- Phase 2 generic, lossless conversational event capture.
CREATE TABLE event_exposures (
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
);
CREATE INDEX event_exposures_date_category_idx
  ON event_exposures(date, category) WHERE voided=0;
CREATE INDEX event_exposures_entity_idx
  ON event_exposures(category, entity_key, date) WHERE voided=0;

CREATE TABLE raw_capture_entries (
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
);

CREATE TABLE raw_capture_resolutions (
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
);
CREATE INDEX raw_capture_resolution_latest_idx
  ON raw_capture_resolutions(capture_id, id DESC);

CREATE TABLE capture_completeness_revisions (
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
);
CREATE INDEX capture_completeness_latest_idx
  ON capture_completeness_revisions(date, scope, entity_key, id DESC);

CREATE TABLE entity_aliases (
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
);
CREATE INDEX entity_alias_latest_idx
  ON entity_aliases(entity_type, alias_key, id DESC);

-- Phase 3 feature configuration and collector provenance. All three tables are
-- append-only; code owns effective/latest revision selection.
CREATE TABLE insight_goal_revisions (
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
);
CREATE INDEX insight_goal_latest_idx
  ON insight_goal_revisions(goal_key, id DESC);

CREATE TABLE source_sync_runs (
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
);
CREATE INDEX source_sync_runs_latest_idx
  ON source_sync_runs(source, completed_at DESC);

CREATE TABLE training_plan_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  effective_from TEXT NOT NULL,
  schedule_json TEXT NOT NULL,
  routines_json TEXT NOT NULL,
  source TEXT NOT NULL,
  supersedes_id INTEGER REFERENCES training_plan_revisions(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX training_plan_revision_effective_idx
  ON training_plan_revisions(effective_from, id DESC);

-- Phase 5 immutable analysis, hypothesis, synthesis, and later orchestration
-- storage. Runtime orchestration is implemented separately; these objects are
-- additive schema owned by Migration 004.
CREATE TABLE analysis_batches (
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
);

CREATE TABLE analysis_range_requests (
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
);

CREATE TABLE analysis_runs (
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
);
CREATE INDEX analysis_runs_lookup_idx
  ON analysis_runs(outcome_key, outcome_mode, completed_at DESC);

CREATE TABLE analysis_findings (
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
);
CREATE INDEX analysis_findings_run_idx
  ON analysis_findings(run_id, quality_tier);

CREATE TABLE analysis_finding_components (
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
);

CREATE TABLE hypotheses (
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
);

CREATE TABLE hypothesis_components (
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  position INTEGER NOT NULL CHECK(position IN (1,2)),
  exposure_key TEXT NOT NULL,
  lag_days INTEGER NOT NULL CHECK(lag_days BETWEEN 0 AND 365),
  window_days INTEGER NOT NULL CHECK(window_days IN (1,3,7,28,90,365)),
  transform TEXT NOT NULL CHECK(transform IN
    ('point','mean','sum','count','delta_per_day','frequency_per_week','days_since')),
  PRIMARY KEY(hypothesis_id, position),
  UNIQUE(hypothesis_id, exposure_key)
);

CREATE TABLE hypothesis_evaluations (
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
);
CREATE INDEX hypothesis_eval_latest_idx
  ON hypothesis_evaluations(hypothesis_id, tested_at DESC, id DESC);

CREATE TABLE hypothesis_evidence_items (
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
);
CREATE INDEX hypothesis_evidence_history_idx
  ON hypothesis_evidence_items(hypothesis_id, created_at, evidence_item_id);

CREATE TABLE synthesis_runs (
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
     AND length(model_id) BETWEEN 1 AND 160
     AND model_id NOT GLOB '*[^A-Za-z0-9_.:/+-]*'
     AND substr(model_id,1,1) GLOB '[A-Za-z0-9]'
     AND length(provider) BETWEEN 1 AND 160
     AND provider NOT GLOB '*[^A-Za-z0-9_.:/+-]*'
     AND substr(provider,1,1) GLOB '[A-Za-z0-9]')),
  CHECK(status!='completed' OR
    (prompt_sha256 IS NOT NULL AND model_id IS NOT NULL AND provider IS NOT NULL
     AND narrative_md IS NOT NULL AND completed_at IS NOT NULL)),
  CHECK(status NOT IN ('insufficient_data','no_novelty','suppressed','failed')
     OR no_message_reason_code IS NOT NULL)
);

CREATE TABLE synthesis_analysis_runs (
  synthesis_id TEXT NOT NULL REFERENCES synthesis_runs(synthesis_id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
  purpose TEXT NOT NULL CHECK(purpose IN ('primary','recent','historical','trigger')),
  PRIMARY KEY(synthesis_id, run_id)
);

CREATE TABLE synthesis_finding_refs (
  synthesis_id TEXT NOT NULL REFERENCES synthesis_runs(synthesis_id),
  finding_id TEXT NOT NULL REFERENCES analysis_findings(finding_id),
  role TEXT NOT NULL CHECK(role IN ('primary','supporting','against','context')),
  PRIMARY KEY(synthesis_id, finding_id, role)
);

CREATE TABLE synthesis_hypothesis_refs (
  synthesis_id TEXT NOT NULL REFERENCES synthesis_runs(synthesis_id),
  hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
  evaluation_id INTEGER NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('active','changed','context')),
  PRIMARY KEY(synthesis_id, hypothesis_id, role),
  FOREIGN KEY(evaluation_id, hypothesis_id)
    REFERENCES hypothesis_evaluations(id, hypothesis_id)
);

CREATE TABLE hypothesis_annotations (
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
     AND length(model_id) BETWEEN 1 AND 160
     AND model_id NOT GLOB '*[^A-Za-z0-9_.:/+-]*'
     AND substr(model_id,1,1) GLOB '[A-Za-z0-9]'
     AND length(provider) BETWEEN 1 AND 160
     AND provider NOT GLOB '*[^A-Za-z0-9_.:/+-]*'
     AND substr(provider,1,1) GLOB '[A-Za-z0-9]')
  )
);
CREATE INDEX hypothesis_annotations_latest_idx
  ON hypothesis_annotations(hypothesis_id, annotation_kind, created_at DESC);
CREATE UNIQUE INDEX hypothesis_annotation_superseded_once_idx
  ON hypothesis_annotations(supersedes_id) WHERE supersedes_id IS NOT NULL;

CREATE TABLE insight_triggers (
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
);
CREATE INDEX insight_triggers_queue_idx
  ON insight_triggers(state, not_before, lease_expires_at, created_at);

CREATE TABLE insight_trigger_events (
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
);

CREATE TABLE insight_notification_outbox (
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
);
CREATE INDEX insight_notification_queue_idx
  ON insight_notification_outbox(state, not_before, lease_expires_at, created_at);

CREATE TABLE insight_notification_events (
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
);

CREATE TRIGGER hypotheses_no_update
BEFORE UPDATE ON hypotheses
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypotheses_no_delete
BEFORE DELETE ON hypotheses
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_components_no_update
BEFORE UPDATE ON hypothesis_components
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_components_no_delete
BEFORE DELETE ON hypothesis_components
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_evaluations_no_update
BEFORE UPDATE ON hypothesis_evaluations
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_evaluations_no_delete
BEFORE DELETE ON hypothesis_evaluations
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_evidence_items_no_update
BEFORE UPDATE ON hypothesis_evidence_items
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_evidence_items_no_delete
BEFORE DELETE ON hypothesis_evidence_items
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_annotations_no_update
BEFORE UPDATE ON hypothesis_annotations
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
CREATE TRIGGER hypothesis_annotations_no_delete
BEFORE DELETE ON hypothesis_annotations
BEGIN
  SELECT RAISE(ABORT,'append_only_table');
END;
