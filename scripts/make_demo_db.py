#!/usr/bin/env python3
"""Build a deterministic, comprehensive fictional OpenHealthAtlas persona."""
import argparse
import hashlib
import json
import os
import pathlib
import random
import sqlite3
import subprocess
import sys
from datetime import date, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()
sys.path.insert(0, str(ROOT / "toolkit"))

from hermes_insights.migrations import AUTONOMOUS_SCHEMA_VERSION, migrate


parser = argparse.ArgumentParser(description=__doc__)
default_data_dir = pathlib.Path(
    os.environ.get(
        "HERMES_DATA_DIR",
        pathlib.Path.home() / ".local" / "share" / "hermes",
    )
)
parser.add_argument(
    "--output",
    type=pathlib.Path,
    default=default_data_dir / "demo" / "health-demo.db",
)
parser.add_argument("--anchor-date", default="2026-06-30")
parser.add_argument("--force", action="store_true")
args = parser.parse_args()

try:
    today = date.fromisoformat(args.anchor_date)
except ValueError as exc:
    parser.error(f"--anchor-date must be an ISO date: {exc}")

DB = args.output.expanduser().resolve()

random.seed(42)
DAYS = 120
FIXTURE_ID = "comprehensive-persona-v1"
FICTIONAL_SOURCE = "synthetic-demo"

DB.parent.mkdir(parents=True, exist_ok=True)
if DB.exists() and not args.force:
    parser.error(f"refusing to overwrite existing database without --force: {DB}")
if DB.exists():
    DB.unlink()
con = sqlite3.connect(DB)
con.executescript(SCHEMA)
con.commit()
con.close()

# Exercise the same checksum-ledger migration path used by an installation.
demo_code_version = hashlib.sha1(b"hermes-open-source-demo-v1").hexdigest()
migrate(str(DB), AUTONOMOUS_SCHEMA_VERSION, 0, demo_code_version)
con = sqlite3.connect(DB)

for i in range(DAYS, -1, -1):
    current = today - timedelta(days=i)
    d = current.isoformat()
    drift = (DAYS - i) / DAYS  # 0 -> 1 over time: gentle improvements
    recovery_drag = 1 if i in {3, 4, 31, 32, 64, 65, 94} else 0
    sleep_hours = round(min(
        9.2,
        max(4.8, random.gauss(6.8 + 0.45 * drift - 0.7 * recovery_drag, 0.45)),
    ), 2)
    resting_hr = round(random.gauss(60 - 2 * drift + 4 * recovery_drag, 1.8), 1)
    hrv_ms = round(random.gauss(44 + 6 * drift - 7 * recovery_drag, 4.5), 1)
    # Apple wearable data most days
    if i == 0 or i % 19 != 0:
        con.execute(
            """INSERT INTO daily_metrics(
                 date,source,resting_hr,hrv_ms,hr_min,hr_avg,hr_max,steps,
                 active_energy_kcal,basal_energy_kcal,exercise_min,distance_km,
                 flights,respiratory_rate,spo2_pct,walking_hr_avg,sleep_hours)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d, "apple", resting_hr, hrv_ms, resting_hr - 8,
                round(resting_hr + 19 + random.uniform(-2, 2), 1),
                round(142 + random.uniform(-8, 12), 1),
                int(max(2200, random.gauss(8200, 1800))),
                round(random.gauss(520, 80), 1), 1650.0,
                round(max(0, random.gauss(42, 15)), 1),
                round(max(0.5, random.gauss(6.2, 1.4)), 2),
                max(0, int(random.gauss(8, 3))),
                round(random.gauss(14.0, 0.45), 1),
                round(random.gauss(97.2, 0.45), 1),
                round(random.gauss(92, 4), 1), sleep_hours,
            ),
        )
    # Fitbit arrives for the last 10 days (provenance overlap on purpose)
    if i <= 10:
        con.execute(
            "INSERT INTO daily_metrics(date, source, resting_hr, hrv_ms, sleep_hours, steps)"
            " VALUES(?,?,?,?,?,?)",
            (d, "fitbit",
             round(resting_hr + random.uniform(-1.2, 1.2), 1),
             round(hrv_ms + random.uniform(-3, 3), 1),
             round(sleep_hours + random.uniform(-0.2, 0.2), 2),
             int(max(1500, random.gauss(8400, 1700)))),
        )
    # Detailed sleep is deliberately absent on a few dates so missingness is
    # visible even though the fictional persona is broadly populated.
    if i == 0 or i % 17 != 0:
        bedtime_min = 22 * 60 + 35 + recovery_drag * 75 + (i % 5) * 6
        bedtime = f"{(bedtime_min // 60) % 24:02d}:{bedtime_min % 60:02d}"
        wake_min = bedtime_min + int(sleep_hours * 60) + 35
        wake_time = f"{(wake_min // 60) % 24:02d}:{wake_min % 60:02d}"
        asleep_min = sleep_hours * 60
        deep_min = round(asleep_min * (0.19 - 0.02 * recovery_drag), 1)
        rem_min = round(asleep_min * 0.22, 1)
        awake_min = 28 + recovery_drag * 18 + i % 4
        light_min = round(max(0, asleep_min - deep_min - rem_min), 1)
        quality = max(1, min(5, round(3.6 + drift - recovery_drag)))
        con.execute(
            """INSERT INTO sleep_log(
                 date,bedtime,wake_time,time_asleep_hours,time_in_bed_hours,
                 deep_min,rem_min,light_min,awake_min,quality,awakenings,
                 notes,source,provenance)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d, bedtime, wake_time, sleep_hours,
                round(sleep_hours + awake_min / 60, 2), deep_min, rem_min,
                light_min, awake_min, quality, 2 + recovery_drag,
                "fictional sleep record", FICTIONAL_SOURCE,
                "fictional wearable sleep stages",
            ),
        )
    # Subjective most evenings
    if i == 0 or i % 9 != 0:
        mood = max(1, min(5, round(3.2 + drift - 0.8 * recovery_drag + random.uniform(-0.7, 0.7))))
        energy = max(1, min(5, round(3.1 + drift - recovery_drag + random.uniform(-0.6, 0.6))))
        focus = max(1, min(5, round(3.0 + drift - 0.7 * recovery_drag + random.uniform(-0.6, 0.6))))
        stress = max(1, min(5, 2 + recovery_drag + (1 if i % 23 == 0 else 0)))
        soreness = (
            "Legs and glutes mildly sore after the fictional workout"
            if current.weekday() in {1, 3, 5} else None
        )
        con.execute(
            "INSERT INTO subjective_daily("
            "date,focus,energy,mood,emotional_regulation,anxiety,motivation,"
            "stress,caffeine_mg,alcohol_units,brain_dump,notes,day_rating,"
            "soreness_note,source,ingested_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d, focus, energy, mood, max(1, min(5, mood)),
                max(1, min(5, 6 - mood)), max(1, min(5, energy)), stress,
                80 + (i % 4) * 25 + recovery_drag * 60,
                1.0 if i % 14 == 0 else 0.0,
                "Fictional evening reflection.",
                "Synthetic persona; no real person.",
                3 if min(mood, energy) >= 4 else (2 if min(mood, energy) >= 3 else 1),
                soreness, FICTIONAL_SOURCE, f"{d} 21:00:00",
            ),
        )
    if i == 0 or i % 13 != 0:
        con.execute(
            "INSERT INTO intake(date,water_ml,notes,source) VALUES(?,?,?,?)",
            (d, 1850 + (i % 6) * 170, "fictional daily water", FICTIONAL_SOURCE),
        )
    # BP + resting HR every ~4 days
    if i % 4 == 0:
        con.execute(
            "INSERT INTO vitals(date,time,systolic,diastolic,resting_hr,notes,source)"
            " VALUES(?,?,?,?,?,?,?)",
            (d, "08:30", int(random.gauss(124, 5)), int(random.gauss(79, 4)),
             int(resting_hr), "fictional home reading", FICTIONAL_SOURCE),
        )
    # Placeholder medication record used only to exercise timing surfaces.
    con.execute(
        "INSERT INTO meds_log(date, drug, dose_mg, time_taken, source) VALUES(?,?,?,?,?)",
        (d, "medication", 5, "08:00", "synthetic-demo"),
    )
    # Weekly body metrics
    if i % 7 == 0:
        con.execute(
            """INSERT INTO body_metrics(
                 date,weight_kg,waist_cm,chest_cm,arm_cm,thigh_cm,hip_cm,
                 neck_cm,body_fat_pct,notes,source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d, round(70.5 - 0.7 * drift + random.gauss(0, 0.2), 1),
                round(80.0 - 0.8 * drift + random.gauss(0, 0.3), 1),
                96.0, 32.0, 55.0, 94.0, 36.0,
                round(18.0 - 0.5 * drift, 1),
                "fictional weekly measurements", FICTIONAL_SOURCE,
            ),
        )

# ---- training layer (routines / schedule / muscle map / logged sets) ----
ROUTINES = [
    ("Fictional Full Body A", "Goblet Squat", 1, 3, 10, 18.0),
    ("Fictional Full Body A", "Incline Push-Up", 2, 3, 10, None),
    ("Fictional Full Body A", "Seated Cable Row", 3, 3, 10, 20.0),
    ("Fictional Full Body A", "Dumbbell Hinge", 4, 3, 8, 24.0),
    ("Fictional Full Body A", "Dead Bug", 5, 3, 10, None),
    ("Fictional Full Body B", "Step-Up", 1, 3, 10, 8.0),
    ("Fictional Full Body B", "Dumbbell Floor Press", 2, 3, 10, 12.0),
    ("Fictional Full Body B", "Lat Pulldown", 3, 3, 10, 22.0),
    ("Fictional Full Body B", "Hip Thrust", 4, 3, 10, 30.0),
    ("Fictional Full Body B", "Side Plank", 5, 3, 30, None),
    ("Fictional Full Body C", "Split Squat", 1, 3, 8, 10.0),
    ("Fictional Full Body C", "Dumbbell Overhead Press", 2, 3, 10, 8.0),
    ("Fictional Full Body C", "One-Arm Row", 3, 3, 10, 14.0),
    ("Fictional Full Body C", "Romanian Deadlift", 4, 3, 8, 28.0),
    ("Fictional Full Body C", "Pallof Press", 5, 3, 12, 8.0),
]
SCHEDULE = {
    "Mon": "Fictional Full Body A",
    "Wed": "Fictional Full Body B",
    "Fri": "Fictional Full Body C",
}
MUSCLES = {
    "Goblet Squat": {"Quads": 1, "Glutes": .5, "Core": .5},
    "Incline Push-Up": {"Chest": 1, "Triceps": .5},
    "Seated Cable Row": {"Back/Lats": 1, "Biceps": .5},
    "Dumbbell Hinge": {"Hamstrings": 1, "Glutes": .5},
    "Dead Bug": {"Core": 1},
    "Step-Up": {"Quads": 1, "Glutes": .5},
    "Dumbbell Floor Press": {"Chest": 1, "Triceps": .5},
    "Lat Pulldown": {"Back/Lats": 1, "Biceps": .5},
    "Hip Thrust": {"Glutes": 1, "Hamstrings": .4},
    "Side Plank": {"Core": 1, "Shoulders": .2},
    "Split Squat": {"Quads": 1, "Glutes": .6},
    "Dumbbell Overhead Press": {"Shoulders": 1, "Triceps": .5},
    "One-Arm Row": {"Back/Lats": 1, "Biceps": .5},
    "Romanian Deadlift": {"Hamstrings": 1, "Glutes": .6, "Back": .2},
    "Pallof Press": {"Core": 1, "Shoulders": .2},
}
con.executemany("INSERT INTO routines VALUES(?,?,?,?,?,?)", ROUTINES)
con.executemany("INSERT INTO training_schedule VALUES(?,?)", list(SCHEDULE.items()))
con.executemany("INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)",
                [(ex, m, w) for ex, mm in MUSCLES.items() for m, w in mm.items()])

# The program is scheduled three times per week. Logged history intentionally
# misses a handful of sessions so adherence and missingness remain testable.
routine_rows = {}
for routine_name, exercise_title, order, sets, reps, weight in ROUTINES:
    routine_rows.setdefault(routine_name, []).append(
        (exercise_title, order, sets, reps, weight)
    )
weekday_plan = {0: SCHEDULE["Mon"], 2: SCHEDULE["Wed"], 4: SCHEDULE["Fri"]}
first_training_day = today - timedelta(days=DAYS)
for offset in range(DAYS + 1):
    session_date = first_training_day + timedelta(days=offset)
    routine_name = weekday_plan.get(session_date.weekday())
    if routine_name is None:
        if session_date.weekday() in {1, 5} and offset % 3:
            con.execute(
                "INSERT INTO workouts(date,type,minutes,kcal,km,source) VALUES(?,?,?,?,?,?)",
                (session_date.isoformat(), "easy cardio", 32, 235, 4.2, FICTIONAL_SOURCE),
            )
        continue
    if offset % 41 == 0:
        continue
    week = offset // 7
    start_time = f"{session_date.isoformat()} 17:30:00"
    end_time = f"{session_date.isoformat()} 18:25:00"
    for exercise_title, _order, set_count, target_reps, base_weight in routine_rows[routine_name]:
        load = None if base_weight is None else round(base_weight + min(week, 12) * 0.35, 1)
        for set_index in range(1, set_count + 1):
            reps = target_reps - (1 if set_index == set_count and target_reps < 20 else 0)
            con.execute(
                """INSERT INTO hevy_sets(
                     date,workout_title,start_time,end_time,description,
                     exercise_title,set_index,set_type,weight_kg,reps,rpe,source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_date.isoformat(), routine_name, start_time, end_time,
                    "fictional three-day full-body program", exercise_title,
                    set_index, "normal", load, reps,
                    round(6.5 + 0.4 * set_index + (week % 3) * 0.2, 1),
                    "hevy-fictional",
                ),
            )

# ---- nutrition (recipes / inventory / meals / micronutrients / targets) ----
RECIPES = {
    "fictional-oats": {
        "name": "Fictional Berry Oats", "meal_type": "breakfast",
        "grams": 360, "macros": (510, 24, 72, 14, 11),
    },
    "fictional-bowl": {
        "name": "Fictional Chicken Rice Bowl", "meal_type": "lunch",
        "grams": 480, "macros": (680, 52, 78, 18, 9),
    },
    "fictional-curry": {
        "name": "Fictional Lentil Vegetable Curry", "meal_type": "dinner",
        "grams": 520, "macros": (620, 31, 84, 17, 18),
    },
    "fictional-yogurt": {
        "name": "Fictional Yogurt Snack", "meal_type": "snack",
        "grams": 240, "macros": (260, 23, 28, 7, 4),
    },
}
for recipe_id, spec in RECIPES.items():
    con.execute(
        """INSERT INTO recipes(
             recipe_id,name,batch_grams,grams_per_portion,portions,notes,source,meal_type)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            recipe_id, spec["name"], spec["grams"] * 5, spec["grams"], 5,
            "fictional recipe", FICTIONAL_SOURCE, spec["meal_type"],
        ),
    )
    kcal, protein, carbs, fat, fiber = spec["macros"]
    for nutrient, unit, per_portion in (
        ("Energy", "kcal", kcal), ("Protein", "g", protein),
        ("Carbs", "g", carbs), ("Fat", "g", fat), ("Fiber", "g", fiber),
    ):
        con.execute(
            "INSERT INTO recipe_nutrients VALUES(?,?,?,?)",
            # The legacy per_gram column stores whole-batch totals, as in
            # import-recipes. The calculator divides by batch weight at use.
            (recipe_id, nutrient, unit, per_portion * 5),
        )
for recipe_id, spec in RECIPES.items():
    con.execute(
        """INSERT INTO meal_inventory(
             recipe_id,portions_remaining,grams_per_portion,prepped_on,notes)
           VALUES(?,?,?,?,?)""",
        (
            recipe_id, 3 if recipe_id != "fictional-yogurt" else 5,
            spec["grams"], (today - timedelta(days=2)).isoformat(),
            "fictional current inventory",
        ),
    )

meal_order = ("fictional-oats", "fictional-bowl", "fictional-curry")
for offset in range(DAYS + 1):
    meal_date = (today - timedelta(days=offset)).isoformat()
    for meal_index, recipe_id in enumerate(meal_order):
        if (offset + meal_index) % 37 == 0:
            continue
        spec = RECIPES[recipe_id]
        kcal, protein, carbs, fat, fiber = spec["macros"]
        con.execute(
            """INSERT INTO nutrition_log(
                 date,recipe_id,food_name,grams,kcal,protein_g,carbs_g,fat_g,
                 fiber_g,notes,source,time,meal_type)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meal_date, recipe_id, spec["name"], spec["grams"], kcal,
                protein, carbs, fat, fiber, "fictional planned meal",
                FICTIONAL_SOURCE, ("07:30", "12:30", "19:00")[meal_index],
                spec["meal_type"],
            ),
        )
    if offset % 3:
        spec = RECIPES["fictional-yogurt"]
        kcal, protein, carbs, fat, fiber = spec["macros"]
        con.execute(
            """INSERT INTO nutrition_log(
                 date,recipe_id,food_name,grams,kcal,protein_g,carbs_g,fat_g,
                 fiber_g,notes,source,time,meal_type)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meal_date, "fictional-yogurt", spec["name"], spec["grams"],
                kcal, protein, carbs, fat, fiber, "fictional snack",
                FICTIONAL_SOURCE, "16:00", "snack",
            ),
        )
    for nutrient, amount, unit in (
        ("vitamin_d", 14 + offset % 4, "ug"),
        ("magnesium", 315 + offset % 30, "mg"),
        ("iron", 12 + offset % 3, "mg"),
        ("vitamin_b12", 4.0, "ug"),
        ("calcium", 880 + offset % 70, "mg"),
        ("potassium", 3350 + offset % 180, "mg"),
        ("vitamin_c", 105 + offset % 25, "mg"),
        ("folate", 360 + offset % 30, "ug"),
    ):
        con.execute(
            "INSERT INTO nutrient_daily(date,nutrient,amount,unit,source) VALUES(?,?,?,?,?)",
            (meal_date, nutrient, amount, unit, FICTIONAL_SOURCE),
        )

for key, value in {
    "height_cm": "176", "sex": "female", "dob": "1991-04-12",
    "activity_fallback": "moderate",
}.items():
    con.execute(
        "INSERT INTO owner_profile(key,value,updated_at) VALUES(?,?,?)",
        (key, value, today.isoformat()),
    )
for name, target in (
    ("protein_g", 120), ("water_ml", 2200), ("fiber_g", 28),
):
    con.execute(
        "INSERT INTO nutrition_targets(name,target,updated) VALUES(?,?,?)",
        (name, target, today.isoformat()),
    )

# ---- labs (fictional values for trend and range-state rendering) ----
LABS = [
    # (months_ago, panel, canonical, value, unit, lo, hi)
    (18, "Metabolic", "Creatinine", 72, "µmol/L", 60, 110),
    (8,  "Metabolic", "Creatinine", 75, "µmol/L", 60, 110),
    (1,  "Metabolic", "Creatinine", 77, "µmol/L", 60, 110),
    (12, "Vitamins", "Vitamin D", 48, "nmol/L", 50, 160),
    (1,  "Vitamins", "Vitamin D", 58, "nmol/L", 50, 160),
    (9,  "Lipids", "LDL cholesterol", 3.4, "mmol/L", None, 3.0),
    (1,  "Lipids", "LDL cholesterol", 2.9, "mmol/L", None, 3.0),
    (1,  "Inflammation", "CRP", 4.0, "mg/L", None, 10.0),
    (26, "Metabolic", "Sodium", 139, "mmol/L", 135, 145),
]
for months, panel, name, val, unit, lo_, hi_ in LABS:
    con.execute(
        "INSERT INTO labs(date, panel, test_name, value, unit,"
        " reference_low, reference_high, source)"
        " VALUES(?,?,?,?,?,?,?, 'demo')",
        ((today - timedelta(days=months * 30)).isoformat(), panel, name,
         val, unit, lo_, hi_))

# ---- mind, habits, commitments, supplements and external care ----
for scale, part, score, maximum in (
    ("fictional-wellbeing", "overall", 32, 40),
    ("fictional-stress", "overall", 11, 40),
    ("fictional-mobility", "lower-body", 18, 24),
):
    con.execute(
        """INSERT INTO assessments(
             date,scale,part,score,max_score,subscores,notes,source)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            (today - timedelta(days=21)).isoformat(), scale, part, score,
            maximum, "{}", "fictional assessment; not clinical",
            FICTIONAL_SOURCE,
        ),
    )

commitment_ids = {}
for name, identity, trigger, floor, reward in (
    ("Fictional evening walk", "person who moves daily", "after dinner", "10 minutes", "tea"),
    ("Fictional wind-down", "person who protects sleep", "21:45", "screens dimmed", "read"),
):
    cursor = con.execute(
        """INSERT INTO commitments(name,identity,trigger,floor,reward,active,created)
           VALUES(?,?,?,?,?,1,?)""",
        (name, identity, trigger, floor, reward, (today - timedelta(days=DAYS)).isoformat()),
    )
    commitment_ids[name] = cursor.lastrowid
for metric, planned, tolerance in (
    ("wake", "06:45", 30), ("bed", "22:45", 35),
    ("workout", "17:30", 60), ("dose", "08:00", 20),
):
    con.execute(
        "INSERT INTO planned_times(metric,planned,tolerance_min,updated) VALUES(?,?,?,?)",
        (metric, planned, tolerance, today.isoformat()),
    )
for offset in range(DAYS + 1):
    log_date = (today - timedelta(days=offset)).isoformat()
    for habit_index, habit in enumerate(("morning light", "evening walk", "wind-down")):
        done = 0 if (offset + habit_index) % 11 == 0 else 1
        con.execute(
            """INSERT INTO habits_log(date,habit,done,streak,xp,notes,source)
               VALUES(?,?,?,?,?,?,?)""",
            (log_date, habit, done, 0, 10 if done else 0, "fictional habit", FICTIONAL_SOURCE),
        )
    for kind_index, kind in enumerate(("energy", "focus", "mood")):
        if (offset + kind_index) % 10:
            con.execute(
                "INSERT INTO checkins(date,time,kind,value,note,source) VALUES(?,?,?,?,?,?)",
                (
                    log_date, ("09:00", "13:00", "20:30")[kind_index], kind,
                    max(1, min(5, 3 + ((offset + kind_index) % 3) - 1)),
                    "fictional check-in", FICTIONAL_SOURCE,
                ),
            )
    for commitment_index, (name, commitment_id) in enumerate(commitment_ids.items()):
        status = "broke" if (offset + commitment_index) % 13 == 0 else (
            "partly" if (offset + commitment_index) % 7 == 0 else "kept"
        )
        con.execute(
            """INSERT INTO commitments_log(
                 date,commitment_id,status,why,source)
               VALUES(?,?,?,?,?)""",
            (log_date, commitment_id, status, "fictional follow-through", FICTIONAL_SOURCE),
        )

supplements = []
for name, brand, dose, unit, form, schedule in (
    ("Fictional Vitamin D", "Demo", 20, "ug", "capsule", "morning"),
    ("Fictional Magnesium", "Demo", 200, "mg", "tablet", "evening"),
    ("Fictional Omega 3", "Demo", 1000, "mg", "capsule", "with dinner"),
):
    cursor = con.execute(
        """INSERT INTO supplement_products(
             name,brand,dose,unit,form,schedule,active,notes)
           VALUES(?,?,?,?,?,?,1,?)""",
        (name, brand, dose, unit, form, schedule, "fictional product"),
    )
    supplements.append((cursor.lastrowid, dose, schedule))
for offset in range(DAYS + 1):
    log_date = (today - timedelta(days=offset)).isoformat()
    for index, (supplement_id, dose, schedule) in enumerate(supplements):
        taken = 0 if (offset + index) % 17 == 0 else 1
        con.execute(
            """INSERT INTO supplements_log(
                 date,supplement_id,taken,dose_taken,notes,source,time_taken)
               VALUES(?,?,?,?,?,?,?)""",
            (
                log_date, supplement_id, taken, dose if taken else 0,
                "fictional supplement log", FICTIONAL_SOURCE,
                "08:05" if schedule == "morning" else "20:15",
            ),
        )

care_products = []
for slot, product_name, active_ingredients in (
    ("am", "Fictional Moisturizer", "ceramides"),
    ("am", "Fictional Sunscreen", "zinc oxide"),
    ("pm", "Fictional Cleanser", "gentle surfactants"),
):
    cursor = con.execute(
        """INSERT INTO skincare_products(
             slot,brand,product_name,active_ingredients,active,notes)
           VALUES(?,?,?,?,1,?)""",
        (slot, "Demo", product_name, active_ingredients, "fictional care product"),
    )
    care_products.append((cursor.lastrowid, slot))
for offset in range(45):
    log_date = (today - timedelta(days=offset)).isoformat()
    for index, (product_id, slot) in enumerate(care_products):
        used = 0 if (offset + index) % 14 == 0 else 1
        con.execute(
            "INSERT INTO skincare_log(date,slot,product_id,used,notes,source) VALUES(?,?,?,?,?,?)",
            (log_date, slot, product_id, used, "fictional care log", FICTIONAL_SOURCE),
        )

for offset, category, label, value, intensity in (
    (97, "training_phase", "fictional base phase", "three days per week", 3),
    (64, "stress", "fictional work deadline", "busy week", 4),
    (48, "travel", "fictional weekend trip", "two nights away", 2),
    (31, "illness", "fictional mild cold", "self-reported", 2),
    (18, "social", "fictional family dinner", "positive evening", 2),
):
    namespace = "person" if category == "social" else "other"
    entity_key = (
        f"{namespace}:"
        + hashlib.sha256(label.strip().casefold().encode("utf-8")).hexdigest()
    )
    con.execute(
        """INSERT INTO event_exposures(
             date,time,category,entity_label,entity_key,value_text,intensity,
             valence,source,note)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            (today - timedelta(days=offset)).isoformat(), "18:00", category,
            label, entity_key, value, intensity,
            1 if category == "social" else 0, FICTIONAL_SOURCE,
            "fictional contextual event",
        ),
    )

# ---- fitness tests (fabricated values selected only to exercise UI states) ----
FT = [
    ("leg-curl", "left", 18, None), ("leg-extension", "left", 36, None),
    ("leg-curl", "right", 22, None), ("leg-extension", "right", 37, None),
    ("biceps-curl", "left", 8, None), ("triceps-extension", "left", 7, None),
    ("biceps-curl", "right", 10, None), ("triceps-extension", "right", 8, None),
    ("hip-flexion", "left", 15, None), ("hip-extension", "left", 25, None),
    ("mcgill-flexor", "bilateral", None, 52),
    ("mcgill-extensor", "bilateral", None, 44),
]
for movement, side, load, secs in FT:
    con.execute(
        "INSERT INTO fitness_tests(date, movement, side, load_kg, reps, seconds, source)"
        " VALUES(?,?,?,?,?,?, 'demo')",
        ((today - timedelta(days=12)).isoformat(), movement, side,
         load, 6 if load is not None else None, secs))

# Repeated fabricated tests meet the theory engine's recurrence gate.
for weeks_ago in range(6, 0, -1):
    d = (today - timedelta(days=weeks_ago * 7)).isoformat()
    progress = 6 - weeks_ago
    for movement, left, right in (
        ("hip-abduction", 10 + progress * 0.5, 11 + progress * 0.5),
        ("hip-extension", 20 + progress * 0.6, 21 + progress * 0.6),
    ):
        for side, load in (("left", left), ("right", right)):
            con.execute(
                "INSERT INTO fitness_tests(date,movement,side,load_kg,reps,source)"
                " VALUES(?,?,?,?,8,'demo')", (d, movement, side, load))

# ---- §3h mobility (synthetic mobility screens so the Phase-4 mobility lens
# shows every state: a restricted side (below the cited norm → deep teal) beside
# a normal side (within norm → pale teal), a pass/fail Thomas, a bilateral
# sit-and-reach coloring the whole posterior chain, and untested muscles left
# grey. Uses the new rom (degrees) + binary (passed) value kinds. ----
MOB = [
    # (movement, side, degrees, passed)   — restricted L / normal R
    ("thomas", "left", None, 0), ("thomas", "right", None, 1),
    ("ankle-df-wall", "left", 28, None), ("ankle-df-wall", "right", 36, None),
    ("shoulder-flexion-rom", "left", 145, None), ("shoulder-flexion-rom", "right", 165, None),
    ("shoulder-er-rom", "left", 60, None), ("shoulder-er-rom", "right", 80, None),
    ("sit-and-reach", "bilateral", None, None),   # cm below → restricted both sides
]
for movement, side, deg, passed in MOB:
    con.execute(
        "INSERT INTO fitness_tests(date, movement, side, degrees, passed, cm, source)"
        " VALUES(?,?,?,?,?,?, 'demo')",
        ((today - timedelta(days=14)).isoformat(), movement, side, deg, passed,
         18 if movement == "sit-and-reach" else None))

# ---- §3g physio loop (fictional records for state rendering) ----
PAIN = [
    # (days_ago, region, side, intensity, quality, flags)
    (22, "anterior-knee", "left", 5, "ache", None),
    (17, "anterior-knee", "left", 3, "ache", None),
    (11, "anterior-knee", "left", 2, "ache", None),
    (7, "low-back", "central", 3, "stiff", None),
    (3, "shoulder", "right", 2, "ache", None),
]
for days, region, side, nrs, quality, flags in PAIN:
    con.execute(
        "INSERT INTO pain_log(date, region, side, intensity, quality, flags, source)"
        " VALUES(?,?,?,?,?,?, 'demo')",
        ((today - timedelta(days=days)).isoformat(), region, side, nrs, quality, flags))
# A fictional self-test and trial response exercise the evidence flow.
con.execute("INSERT INTO self_test_log(date, test, side, result, source)"
            " VALUES(?,?,?,?, 'demo')",
            ((today - timedelta(days=19)).isoformat(), "prolonged-sitting-pain", "left", "positive"))
for days, resp in ((18, "same"), (13, "better"), (8, "better")):
    con.execute(
        """INSERT INTO exercise_trial_log(
             date,drill,target,dose,response,pain_during,source)
           VALUES(?,?,?,?,?,?,?)""",
        (
            (today - timedelta(days=days)).isoformat(), "pfp-hip-knee-loading",
            "anterior-knee", "3 sets", resp, 2, FICTIONAL_SOURCE,
        ),
    )

for offset in range(DAYS + 1):
    environment_date = (today - timedelta(days=offset)).isoformat()
    seasonal = (DAYS - offset) / DAYS
    con.execute(
        """INSERT INTO weather(
             date,location,condition,temp_max_c,temp_min_c,feels_like_max_c,
             precipitation_mm,wind_speed_max_ms,sunshine_hours,daylight_hours,
             uv_index_max,humidity_mean_pct,source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            environment_date, "Fictional City",
            "rain" if offset % 11 == 0 else "partly cloudy",
            round(16 + 7 * seasonal + (offset % 5), 1),
            round(8 + 4 * seasonal + (offset % 3), 1),
            round(15 + 7 * seasonal + (offset % 5), 1),
            7.5 if offset % 11 == 0 else round((offset % 4) * 0.3, 1),
            round(4.0 + (offset % 6) * 0.7, 1),
            round(5.5 + seasonal * 4.5, 1),
            round(10.5 + seasonal * 5.2, 1),
            round(2.0 + seasonal * 4.0, 1),
            58 + offset % 17, FICTIONAL_SOURCE,
        ),
    )
    con.execute(
        """INSERT INTO air_quality(
             date,location,european_aqi_mean,european_aqi_max,pm2_5_ugm3,
             pm10_ugm3,ozone_ugm3,no2_ugm3,grass_pollen,birch_pollen,source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            environment_date, "Fictional City", 20 + offset % 12,
            27 + offset % 15, 3.5 + offset % 5, 7.0 + offset % 7,
            52 + offset % 10, 9 + offset % 6,
            20 + offset % 30 if seasonal > 0.5 else 2,
            8 + offset % 12 if seasonal > 0.35 else 0,
            FICTIONAL_SOURCE,
        ),
    )
con.commit()

con.close()
seeded = subprocess.run(
    [sys.executable, str(ROOT / "toolkit" / "health.py"),
     "import-submuscle-map", str(ROOT / "docs" / "authored-submuscle-map.md"),
     "--seed", "--seed-all"],
    env={**os.environ, "HEALTH_DB": str(DB)},
    capture_output=True, text=True, check=True, timeout=30,
)
seeded_rows = json.loads(seeded.stdout)["seeded_rows"]
COLLECTED_DOMAIN_TABLES = (
    "air_quality", "assessments", "body_metrics", "daily_metrics",
    "event_exposures", "exercise_trial_log", "fitness_tests", "habits_log",
    "hevy_sets", "intake", "labs", "meal_inventory", "meds_log",
    "nutrient_daily", "nutrition_log", "pain_log", "recipes",
    "self_test_log", "skincare_log", "sleep_log", "subjective_daily",
    "supplements_log", "vitals", "weather", "workouts",
)
connection = sqlite3.connect(DB)
try:
    row_counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in COLLECTED_DOMAIN_TABLES
    }
    system_rows = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "analysis_batches", "analysis_findings", "hypotheses",
            "synthesis_runs", "insight_notification_outbox",
        )
    }
finally:
    connection.close()
print(json.dumps({
    "contract": "openhealthatlas-fictional-persona-v1",
    "fixture_id": FIXTURE_ID,
    "data_class": "fictional",
    "database": str(DB),
    "range_from": (today - timedelta(days=DAYS)).isoformat(),
    "range_to": today.isoformat(),
    "scheduled_training_days": ["Mon", "Wed", "Fri"],
    "collected_domain_rows": row_counts,
    "system_generated_rows": system_rows,
    "public_submuscle_rows": seeded_rows,
}, sort_keys=True))
