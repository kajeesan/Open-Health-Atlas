"""T46 — `nutrition-coverage`: per-day target-coverage rows, built on the
SAME shared scorer (_nutrition_day_score) and targets source
(_compute_targets) as scores()'s nutrition component — one path, not a
rival formula. Same conventions as the other suites (real CLI, temp DB from
SCHEMA.sql)."""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import date, timedelta

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True, timeout=60)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
        return json.loads(r.stdout)
    assert r.returncode != 0
    return r.stderr


def seed_profile(db, kg=62.0):
    run(db, "profile-set", "height_cm", "165")
    run(db, "profile-set", "sex", "female")
    run(db, "profile-set", "dob", "2001-01-15")
    con = sqlite3.connect(db)
    con.execute("INSERT INTO body_metrics(date, weight_kg) VALUES(?,?)",
               (date.today().isoformat(), kg))
    con.commit(); con.close()


def day(back=0):
    return (date.today() - timedelta(days=back)).isoformat()


HEADER = ["Date", "Energy (kcal)", "Protein (g)", "Vitamin D (IU)", "Magnesium (mg)",
          "EPA (mg)", "DHA (mg)", "Zinc (mg)", "Iron (mg)", "B12 (µg)", "Calcium (mg)",
          "Potassium (mg)", "Vitamin C (mg)", "Folate (µg)"]


def full_day(d, kcal=2200, protein=100):
    # These values exactly match the fictional targets configured in conftest.
    # Vitamin D is imported as IU and normalized to micrograms by the engine.
    return [d, kcal, protein, 480, 300, 120, 80, 10, 12, 3, 800, 3000, 90, 300]


def csvfile(tmp_path, rows, name="cronometer.csv"):
    p = tmp_path / name
    p.write_text("\n".join([",".join(HEADER)] + [",".join(map(str, r)) for r in rows]) + "\n")
    return str(p)


def coverage(db, *args):
    return run(db, "nutrition-coverage", *args)


# ── flag validation ─────────────────────────────────────────────────────────

def test_days_whitelist_rejects_off_list_values(db):
    err = run(db, "nutrition-coverage", "--days", "10", expect_ok=False)
    assert "invalid choice" in err


def test_default_days_is_7(db):
    # profile but no nutrient data at all -> insufficient, but the `days`
    # echoed back proves the default landed without an explicit flag
    seed_profile(db)
    r = coverage(db)
    assert r["days"] == 7


# ── honest refusals ──────────────────────────────────────────────────────────

def test_insufficient_without_profile(db):
    r = coverage(db, "--days", "7")
    assert r["status"] == "insufficient_data"
    assert r["rows"] == []
    assert "profile" in r["reason"]


def test_insufficient_with_profile_but_no_nutrient_data(db):
    seed_profile(db)
    r = coverage(db, "--days", "7")
    assert r["status"] == "insufficient_data"
    assert r["rows"] == []
    assert "Cronometer" in r["reason"] or "nutrient" in r["reason"]


# ── scored rows ───────────────────────────────────────────────────────────────

def test_logged_recipe_day_scores_without_daily_cronometer_import(db):
    """Food logs carry macros directly and recipe nutrient totals can supply
    every scoreable micro. No nutrient_daily row is required."""
    seed_profile(db)
    run(db, "nutrition-target-set", "protein_g", "--target", "100")
    run(db, "nutrition-target-set", "kcal", "--target", "2200")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO recipes(recipe_id,name,batch_grams)"
        " VALUES('sample-stew','Sample Vegetable Stew',1000)")
    con.execute(
        "INSERT INTO nutrition_log(date,recipe_id,food_name,grams,kcal,protein_g)"
        " VALUES(?,'sample-stew','Sample Vegetable Stew',500,2200,100)", (day(0),))
    for nutrient, unit, total in (
            ("Vitamin D", "IU", 960),
            ("Magnesium", "mg", 600),
            ("EPA", "g", 0.24),
            ("DHA", "g", 0.16),
            ("Zinc", "mg", 20),
            ("Iron", "mg", 24),
            ("B12 (Cobalamin)", "ug", 6),
            ("Calcium", "mg", 1600),
            ("Potassium", "mg", 6000),
            ("Vitamin C", "mg", 180),
            ("Folate", "ug", 600),
            # Must not be substituted for the EPA+DHA target.
            ("Omega-3", "g", 50)):
        con.execute(
            "INSERT INTO recipe_nutrients(recipe_id,nutrient,unit,per_gram)"
            " VALUES('sample-stew',?,?,?)", (nutrient, unit, total))
    con.commit()
    con.close()

    r = coverage(db, "--days", "7")
    assert r["status"] == "ok"
    assert r["rows"] == [{
        "date": day(0), "score": 100, "band": "good",
        "components": {
            "protein_pct": 100, "kcal_pct": 100,
            "micros_hit": 10, "micros_total": 10,
        },
    }]


def test_daily_cronometer_value_replaces_recipe_fallback(db):
    """A daily value is authoritative for its nutrient and is not added to
    the recipe-derived contribution."""
    seed_profile(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO recipes(recipe_id,name,batch_grams)"
        " VALUES('sample-stew','Sample Vegetable Stew',1000)")
    con.execute(
        "INSERT INTO nutrition_log(date,recipe_id,food_name,grams,kcal,protein_g)"
        " VALUES(?,'sample-stew','Sample Vegetable Stew',500,2200,100)", (day(0),))
    con.execute(
        "INSERT INTO recipe_nutrients(recipe_id,nutrient,unit,per_gram)"
        " VALUES('sample-stew','Magnesium','mg',700)")
    con.execute(
        "INSERT INTO nutrient_daily(date,nutrient,amount,unit,source)"
        " VALUES(?,'magnesium',100,'mg','cronometer')", (day(0),))
    con.commit()
    con.close()

    score = run(db, "scores")["scores"]["nutrition"]
    magnesium = next(
        m for m in score["inputs"]["micros"] if m["key"] == "magnesium")
    assert magnesium["amount"] == 100


def test_multi_day_rows_and_empty_day_omitted(db, tmp_path):
    """3-day window; data on day(0) and day(2) only -> day(1) (no import)
    is OMITTED from rows, never scored as 0. Rows come back date-ascending."""
    seed_profile(db)
    run(db, "nutrition-target-set", "protein_g", "--target", "100")
    run(db, "nutrition-target-set", "kcal", "--target", "2200")
    run(db, "import-cronometer", csvfile(tmp_path, [full_day(day(2), kcal=1600, protein=60)], "d2.csv"))
    run(db, "import-cronometer", csvfile(tmp_path, [full_day(day(0))], "d0.csv"))
    r = coverage(db, "--days", "7")
    assert r["status"] == "ok"
    assert [row["date"] for row in r["rows"]] == [day(2), day(0)]   # ascending
    today_row = r["rows"][1]
    assert today_row["score"] == 100 and today_row["band"] == "good"
    assert today_row["components"] == {
        "protein_pct": 100, "kcal_pct": 100, "micros_hit": 10, "micros_total": 10,
    }
    old_row = r["rows"][0]
    # kcal 1600 vs target 2200: 27% off -> 0 credit; protein 60/100 = 60%
    assert old_row["components"]["protein_pct"] == 60
    assert old_row["components"]["kcal_pct"] == 73
    assert old_row["score"] == 75


def test_days_window_excludes_older_rows(db, tmp_path):
    seed_profile(db)
    run(db, "nutrition-target-set", "protein_g", "--target", "100")
    run(db, "nutrition-target-set", "kcal", "--target", "2200")
    run(db, "import-cronometer", csvfile(tmp_path, [full_day(day(10))], "old.csv"))
    run(db, "import-cronometer", csvfile(tmp_path, [full_day(day(0))], "new.csv"))
    r = coverage(db, "--days", "7")
    assert [row["date"] for row in r["rows"]] == [day(0)]   # the day(10) row falls outside


def test_water_not_included_in_coverage_components(db, tmp_path):
    seed_profile(db)
    run(db, "nutrition-target-set", "protein_g", "--target", "100")
    run(db, "nutrition-target-set", "kcal", "--target", "2200")
    run(db, "import-cronometer", csvfile(tmp_path, [full_day(day(0))]))
    r = coverage(db, "--days", "7")
    assert "water" not in json.dumps(r["rows"][0]["components"]).lower()
