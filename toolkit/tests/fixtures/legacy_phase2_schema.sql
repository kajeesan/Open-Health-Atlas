-- Hermes health DB — COMPLETE schema (structure only, NO data)
-- Historical fixture only; emit the current schema with: python3 toolkit/health.py schema

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
  confidence  TEXT NOT NULL DEFAULT 'cited' CHECK(confidence IN ('cited','user-confirmed')),
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
