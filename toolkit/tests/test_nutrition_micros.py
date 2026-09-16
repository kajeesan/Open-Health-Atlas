"""§4a nutrition micros: Cronometer import + the composite score.

Targets come from the fictional test configuration. Production defaults are
empty, so the score stays insufficient until users configure their own values.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import date

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
                       capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else {}
    assert r.returncode != 0
    return r.stderr


def seed_profile(db):
    """T46: the nutrition score's targets now come from the T44 compute
    engine, which needs a complete user profile + a logged weight before
    ANYTHING (including a legacy manual override) unlocks — see
    toolkit/health.py's _compute_targets."""
    run(db, "profile-set", "height_cm", "165")
    run(db, "profile-set", "sex", "female")
    run(db, "profile-set", "dob", "2000-01-01")
    con = sqlite3.connect(db)
    con.execute("INSERT INTO body_metrics(date, weight_kg) VALUES(?,?)",
               (date.today().isoformat(), 62.0))
    con.commit(); con.close()


def csvfile(tmp_path, header, rows):
    p = tmp_path / "cronometer.csv"
    p.write_text("\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows]) + "\n")
    return str(p)


HEADER = ["Date", "Energy (kcal)", "Protein (g)", "Vitamin D (IU)", "Magnesium (mg)",
          "EPA (mg)", "DHA (mg)", "Zinc (mg)", "Iron (mg)", "B12 (µg)", "Calcium (mg)",
          "Potassium (mg)", "Vitamin C (mg)", "Folate (µg)", "Omega-3 (g)", "Mystery (zorb)"]


def full_day(d, kcal=2800, protein=170):
    # Every value matches the fictional target configuration from conftest.py.
    return [d, kcal, protein, 480, 300, 120, 80, 10, 12, 3, 800, 3000, 90, 300, 2.5, 42]


def test_import_maps_units_and_reports_unmatched(db, tmp_path):
    today = date.today().isoformat()
    r = run(db, "import-cronometer", csvfile(tmp_path, HEADER, [full_day(today)]))
    assert r["days"] == 1 and r["citation_status"] == "user-configured"
    # the bare Omega-3 total (includes ALA) and unknown columns are refused
    assert "Omega-3 (g)" in r["unmatched_columns"]
    assert "Mystery (zorb)" in r["unmatched_columns"]
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    got = {x["nutrient"]: (x["amount"], x["unit"]) for x in
           con.execute("SELECT nutrient, amount, unit FROM nutrient_daily")}
    assert got["vitamin_d"] == (12.0, "ug")          # 480 IU -> 12 ug
    assert got["omega3_epa_dha"] == (200.0, "mg")    # EPA 120 + DHA 80
    assert got["energy_kcal"] == (2800.0, "kcal")


def test_reimport_corrects_in_place_no_deletes(db, tmp_path):
    today = date.today().isoformat()
    run(db, "import-cronometer", csvfile(tmp_path, HEADER, [full_day(today, kcal=2000)]))
    run(db, "import-cronometer", csvfile(tmp_path, HEADER, [full_day(today, kcal=2600)]))
    con = sqlite3.connect(db)
    rows = con.execute("SELECT amount FROM nutrient_daily WHERE nutrient='energy_kcal'").fetchall()
    assert rows == [(2600.0,)]                        # one row, corrected


def test_score_locked_until_data_then_targets(db, tmp_path):
    s = run(db, "scores")["scores"]["nutrition"]
    assert s["insufficient_data"] and "Cronometer" in s["reason"]
    today = date.today().isoformat()
    run(db, "import-cronometer", csvfile(tmp_path, HEADER, [full_day(today)]))
    # T46: the old "needs manually-set nutrition_targets rows" gate is gone —
    # targets are computed now, so once Cronometer data exists the remaining
    # gate is "targets need a complete user profile" (the T44 engine).
    s = run(db, "scores")["scores"]["nutrition"]
    assert s["insufficient_data"] and "profile" in s["reason"]
    assert s["inputs"]["citation_status"] == "user-configured"


def test_composite_score_math(db, tmp_path):
    today = date.today().isoformat()
    seed_profile(db)
    run(db, "nutrition-target-set", "protein_g", "--target", "170")
    run(db, "nutrition-target-set", "kcal", "--target", "2800")
    row = full_day(today)
    run(db, "import-cronometer", csvfile(tmp_path, HEADER, [row]))
    s = run(db, "scores")["scores"]["nutrition"]
    assert s["score"] == 100                          # everything at target
    assert s["inputs"]["low_micros"] == []
    assert s["inputs"]["citation_status"] == "user-configured"
    assert all(m["source"] == "user-configured target"
               for m in s["inputs"]["micros"])


def test_composite_flags_shortfalls_never_over_100(db, tmp_path):
    today = date.today().isoformat()
    seed_profile(db)
    run(db, "nutrition-target-set", "protein_g", "--target", "170")
    run(db, "nutrition-target-set", "kcal", "--target", "2800")
    row = full_day(today, kcal=3900, protein=400)     # kcal way over, protein over
    row[HEADER.index("Magnesium (mg)")] = 100         # 33% of target -> low
    row[HEADER.index("Vitamin D (IU)")] = 0
    run(db, "import-cronometer", csvfile(tmp_path, HEADER, [row]))
    s = run(db, "scores")["scores"]["nutrition"]
    i = s["inputs"]
    assert "magnesium" in i["low_micros"] and "vitamin_d" in i["low_micros"]
    # protein capped at 100% of its 25 points; kcal ±25%+ off -> 0 of 15
    # micros: 8 at 100% + magnesium 33% + vitamin_d 0% -> about 50 points
    assert s["score"] == 75
    assert s["band"] == "good"


def test_manual_override_still_respected_with_profile(db, tmp_path):
    """T46: a legacy nutrition-target-set override still wins over the T44
    compute — but now layers ON TOP OF a complete profile rather than
    substituting for one (see test_score_locked_until_data_then_targets)."""
    today = date.today().isoformat()
    seed_profile(db)                                   # fictional profile; override still wins
    run(db, "nutrition-target-set", "protein_g", "--target", "170")
    run(db, "nutrition-target-set", "kcal", "--target", "2800")
    row = full_day(today)
    run(db, "import-cronometer", csvfile(tmp_path, HEADER, [row]))
    s = run(db, "scores")["scores"]["nutrition"]
    assert s["inputs"]["protein_target"] == 170 and s["inputs"]["kcal_target"] == 2800
    assert s["score"] == 100


def test_target_set_validates(db):
    assert "must be one of" in run(db, "nutrition-target-set", "sugar", "--target", "10", expect_ok=False)
    assert "positive" in run(db, "nutrition-target-set", "kcal", "--target", "0", expect_ok=False)
    # inf would zero the score forever; nan dies inside sqlite — refuse both cleanly
    assert "finite" in run(db, "nutrition-target-set", "kcal", "--target", "inf", expect_ok=False)
    assert "finite" in run(db, "nutrition-target-set", "kcal", "--target", "nan", expect_ok=False)


# ── adversarial regression tests ─────────────────────────────────────────────

def test_nan_inf_cells_are_skipped_not_fatal(db, tmp_path):
    today = date.today().isoformat()
    row = full_day(today)
    row[HEADER.index("Magnesium (mg)")] = "nan"
    row[HEADER.index("Zinc (mg)")] = "inf"
    r = run(db, "import-cronometer", csvfile(tmp_path, HEADER, [row]))
    assert r["ok"] and r["days"] == 1                 # import survives
    assert r["skipped_values_unparseable"] == 2
    con = sqlite3.connect(db)
    got = {x[0] for x in con.execute("SELECT nutrient FROM nutrient_daily")}
    assert "magnesium" not in got and "zinc" not in got
    assert "vitamin_d" in got                         # the sane fields landed


def test_bad_dates_are_counted_not_hidden(db, tmp_path):
    r = run(db, "import-cronometer", csvfile(
        tmp_path, HEADER, [full_day("07/10/2026"), full_day(date.today().isoformat())]))
    assert r["days"] == 1 and r["skipped_rows_bad_date"] == 1


def test_bom_is_tolerated_non_utf8_fails_clean(db, tmp_path):
    today = date.today().isoformat()
    p = tmp_path / "bom.csv"
    body = ",".join(HEADER) + "\n" + ",".join(map(str, full_day(today))) + "\n"
    p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))     # Excel-style BOM
    r = run(db, "import-cronometer", str(p))
    assert r["days"] == 1                             # BOM must not mask Date
    p2 = tmp_path / "latin1.csv"
    p2.write_bytes("Date,B12 (µg)\n2026-07-01,4\n".encode("latin-1"))
    assert "not UTF-8" in run(db, "import-cronometer", str(p2), expect_ok=False)


def test_duplicate_epa_units_do_not_double_count(db, tmp_path):
    header = ["Date", "EPA (mg)", "EPA (g)", "DHA (mg)"]
    r = run(db, "import-cronometer", csvfile(
        tmp_path, header, [[date.today().isoformat(), 100, 0.1, 50]]))
    assert r["ok"]
    con = sqlite3.connect(db)
    amt = con.execute("SELECT amount FROM nutrient_daily WHERE nutrient='omega3_epa_dha'").fetchone()[0]
    assert amt == 150.0                               # last EPA column wins; never 250


def test_exact_duplicate_headers_are_surfaced(db, tmp_path):
    header = ["Date", "Magnesium (mg)", "Magnesium (mg)"]
    r = run(db, "import-cronometer", csvfile(
        tmp_path, header, [[date.today().isoformat(), 111, 222]]))
    assert r["duplicate_columns"] == ["Magnesium (mg)"]
