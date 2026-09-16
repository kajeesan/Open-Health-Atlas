"""§3e fitness_tests capture + §3c athletic radar + §3d strength ratios.

Deterministic engine: health.py computes, the panel renders. Tests drive the
real CLI against a temp DB (same style as the other suites). e1RM is Epley,
computed at read time; nothing fabricated; honest insufficient_data everywhere.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
FROZEN_RECENT_DATE = "2026-07-10"
FROZEN_CLI = """
from datetime import datetime
from pathlib import Path
import runpy
import sys

health_path = Path(sys.argv[1])
frozen_date = sys.argv[2]
sys.path.insert(0, str(health_path.parent))
namespace = runpy.run_path(str(health_path), run_name="health_frozen_test")
module_globals = namespace["main"].__globals__
module_globals["_now"] = lambda: datetime.fromisoformat(
    frozen_date + "T12:00:00"
).replace(tzinfo=module_globals["CANON_TZ"])
sys.argv = [str(health_path), *sys.argv[3:]]
namespace["main"]()
"""


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


def run_at(db, frozen_date, *args):
    """Run the real CLI with a test-only frozen civil date."""

    result = subprocess.run(
        [sys.executable, "-c", FROZEN_CLI, str(HEALTH), frozen_date, *args],
        env={**os.environ, "HEALTH_DB": str(db)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


def hevy(db, rows):
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO hevy_sets(date,exercise_title,set_index,set_type,weight_kg,reps,source)"
        " VALUES(?,?,?,?,?,?, 'hevy')", rows)
    con.commit()
    con.close()


# ── §3e capture ──────────────────────────────────────────────────────────────

def test_log_strength_returns_epley_e1rm(db):
    r = run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "40", "--reps", "8")
    assert r["ok"] and r["e1rm"] == 50.7           # 40 × (1 + 8/30)
    assert r["logged"]["side"] == "left"


def test_unilateral_requires_side(db):
    err = run(db, "fitness-test-log", "leg-extension", "--load", "40", "--reps", "8", expect_ok=False)
    assert "unilateral" in err


def test_bilateral_rejects_a_side(db):
    err = run(db, "fitness-test-log", "mcgill-flexor", "--side", "left", "--seconds", "90", expect_ok=False)
    assert "bilateral" in err


def test_unknown_movement_refused_with_hint(db):
    err = run(db, "fitness-test-log", "leg-ex", "--side", "left", "--load", "40", "--reps", "8", expect_ok=False)
    assert "unknown movement" in err and "leg-extension" in err   # near-match suggested


def test_kind_gating_rejects_wrong_value_field(db):
    # a hold needs seconds, not load×reps
    err = run(db, "fitness-test-log", "mcgill-flexor", "--load", "40", "--reps", "8", expect_ok=False)
    assert "seconds" in err


def test_clamps_and_whole_reps(db):
    assert "out of range" in run(db, "fitness-test-log", "leg-extension", "--side", "left",
                                 "--load", "5000", "--reps", "8", expect_ok=False)
    assert "out of range" in run(db, "fitness-test-log", "leg-extension", "--side", "left",
                                 "--load", "40", "--reps", "99", expect_ok=False)


def test_outside_protocol_range_flagged_not_rejected(db):
    r = run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "30", "--reps", "12")
    assert r["ok"] and r["outside_protocol_range"] is True   # 12 reps ≠ 6–8RM, kept anyway


def test_latest_per_side_and_priority_coverage(db):
    run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "40", "--reps", "8", "--date", "2026-05-01")
    run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "44", "--reps", "8", "--date", "2026-06-01")
    r = run(db, "fitness-tests", "--days", "365")
    left = [t for t in r["tests"] if t["movement"] == "leg-extension" and t["side"] == "left"]
    assert len(left) == 1 and left[0]["date"] == "2026-06-01"    # only the latest
    assert r["priority_covered"] < r["priority_total"]           # most still missing


# ── §3e soft-void ────────────────────────────────────────────────────────────

def test_void_hides_row_but_never_deletes(db):
    run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "400", "--reps", "8")
    run(db, "fitness-test-void", "1", "--reason", "typo")
    assert run(db, "fitness-tests", "--days", "365")["tests"] == []   # voided skipped
    con = sqlite3.connect(db)
    row = con.execute("SELECT voided, void_reason FROM fitness_tests WHERE id=1").fetchone()
    con.close()
    assert row == (1, "typo")                                        # row preserved


def test_void_rejects_double_and_missing(db):
    run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "40", "--reps", "8")
    run(db, "fitness-test-void", "1")
    assert "already voided" in run(db, "fitness-test-void", "1", expect_ok=False)
    assert "no fitness_tests row" in run(db, "fitness-test-void", "999", expect_ok=False)


# ── §3c athletic radar ───────────────────────────────────────────────────────

def test_axis_insufficient_without_target(db):
    run(db, "fitness-test-log", "balance-stand", "--side", "left", "--seconds", "20")
    run(db, "fitness-test-log", "balance-stand", "--side", "right", "--seconds", "22")
    bal = run(db, "athletic-radar")["scores"]["balance"]
    assert bal["status"] == "insufficient_data" and "target" in bal["reason"]


def test_axis_insufficient_without_test(db):
    run(db, "athletic-target-set", "balance", "--target", "30")
    bal = run(db, "athletic-radar")["scores"]["balance"]
    assert bal["status"] == "insufficient_data"


def test_balance_uses_weaker_side(db):
    run(db, "athletic-target-set", "balance", "--target", "30")
    run(db, "fitness-test-log", "balance-stand", "--side", "left", "--seconds", "18")
    run(db, "fitness-test-log", "balance-stand", "--side", "right", "--seconds", "27")
    bal = run(db, "athletic-radar")["scores"]["balance"]
    assert bal["result"] == 18.0 and bal["score"] == 60           # min side, 18/30


def test_balance_needs_both_sides(db):
    run(db, "athletic-target-set", "balance", "--target", "30")
    run(db, "fitness-test-log", "balance-stand", "--side", "left", "--seconds", "18")
    bal = run(db, "athletic-radar")["scores"]["balance"]
    assert bal["status"] == "insufficient_data" and "both sides" in bal["reason"]


def test_lower_better_axis_inverts(db):
    run(db, "athletic-target-set", "endurance", "--target", "1080")   # 18:00 target, seconds
    run(db, "fitness-test-log", "run-3k", "--seconds", "1200")        # 20:00 actual, slower
    end = run(db, "athletic-radar")["scores"]["endurance"]
    assert end["score"] == 90                                         # 1080/1200


def test_strength_axis_from_hevy_e1rm(db):
    run(db, "athletic-target-set", "strength", "--lift", "Front Squat", "--target", "140")
    hevy(db, [("2026-07-09", "Front Squat", 1, "normal", 100, 5)])    # e1RM 116.7
    s = run_at(db, FROZEN_RECENT_DATE, "athletic-radar")["scores"]["strength"]
    assert s["score"] == 83 and s["lifts"][0]["e1rm"] == 116.7


def test_strength_axis_insufficient_without_recent_set(db):
    run(db, "athletic-target-set", "strength", "--lift", "Front Squat", "--target", "140")
    s = run(db, "athletic-radar")["scores"]["strength"]
    assert s["status"] == "insufficient_data"


def test_target_set_validation(db):
    assert "axis must be one" in run(db, "athletic-target-set", "power", "--target", "1", expect_ok=False)
    assert "--lift is required" in run(db, "athletic-target-set", "strength", "--target", "1", expect_ok=False)
    assert "only applies" in run(db, "athletic-target-set", "balance", "--target", "1", "--lift", "X", expect_ok=False)
    assert "positive" in run(db, "athletic-target-set", "balance", "--target", "0", expect_ok=False)


# ── §3d strength ratios ──────────────────────────────────────────────────────

def _log_pair(db, num_mv, den_mv, nval, dval, kind="strength"):
    for mv, v in ((num_mv, nval), (den_mv, dval)):
        for side in ("left", "right"):
            if kind == "strength":
                run(db, "fitness-test-log", mv, "--side", side, "--load", str(v), "--reps", "8")
            else:
                run(db, "fitness-test-log", mv, "--side", side, "--seconds", str(v))


def test_tested_hq_in_range_and_symmetric(db):
    _log_pair(db, "leg-curl", "leg-extension", 30, 50)               # ratio 0.6
    hq = next(r for r in run(db, "strength-ratios", "--view", "tested")["rows"] if r["key"] == "hq")
    assert hq["sides"][0]["ratio"] == 0.6 and hq["sides"][0]["flag"] == "in_range"
    assert hq["side_gap_flag"] == "ok"


def test_tested_side_gap_flags_asymmetry(db):
    run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "40", "--reps", "8")
    run(db, "fitness-test-log", "leg-extension", "--side", "right", "--load", "60", "--reps", "8")
    run(db, "fitness-test-log", "leg-curl", "--side", "left", "--load", "24", "--reps", "8")
    run(db, "fitness-test-log", "leg-curl", "--side", "right", "--load", "36", "--reps", "8")
    hq = next(r for r in run(db, "strength-ratios", "--view", "tested")["rows"] if r["key"] == "hq")
    assert hq["side_gap"] > 0.15 and hq["side_gap_flag"] == "asymmetry"   # 33% quad gap


def test_tested_side_gap_catches_denominator_asymmetry(db):
    # hamstrings symmetric, quads (denominator) asymmetric 40 vs 60 → still flagged
    run(db, "fitness-test-log", "leg-curl", "--side", "left", "--load", "30", "--reps", "8")
    run(db, "fitness-test-log", "leg-curl", "--side", "right", "--load", "30", "--reps", "8")
    run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "40", "--reps", "8")
    run(db, "fitness-test-log", "leg-extension", "--side", "right", "--load", "60", "--reps", "8")
    hq = next(r for r in run(db, "strength-ratios", "--view", "tested")["rows"] if r["key"] == "hq")
    assert hq["side_gap_flag"] == "asymmetry"     # numerator alone would miss this


def test_tested_insufficient_until_both_movements(db):
    run(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "40", "--reps", "8")
    hq = next(r for r in run(db, "strength-ratios", "--view", "tested")["rows"] if r["key"] == "hq")
    assert hq["sides"][0]["status"] == "insufficient_data"           # curl missing


def test_tested_mcgill_uses_hold_seconds(db):
    run(db, "fitness-test-log", "mcgill-flexor", "--seconds", "100")
    run(db, "fitness-test-log", "mcgill-extensor", "--seconds", "150")
    fe = next(r for r in run(db, "strength-ratios", "--view", "tested")["rows"] if r["key"] == "mcgill-fe")
    assert fe["sides"][0]["ratio"] == 0.67 and fe["sides"][0]["flag"] == "in_range"   # <1.0 band


def test_everyday_pattern_ratio_and_unmapped(db):
    hevy(db, [("2026-07-09", "Goblet Squat", 1, "normal", 60, 5),       # squat e1RM 70.0
              ("2026-07-09", "Dumbbell Hinge", 1, "normal", 84, 5),     # hinge e1RM 98.0
              ("2026-07-09", "Mystery Lift", 1, "normal", 50, 5)])
    r = run_at(
        db, FROZEN_RECENT_DATE, "strength-ratios", "--view", "everyday"
    )
    sq = next(row for row in r["rows"] if row["num_pattern"] == "squat")
    assert sq["ratio"] == 0.71 and sq["evidence"] == "heuristic" and "flag" in sq
    assert "Mystery Lift" in r["unmapped_exercises"]


def test_tested_hip_is_trend_only_no_target_no_flag(db):
    # Accepted contract: hip flex:ext population SD (±0.61) is too wide
    # to support a target — the ratio is shown/trended but NEVER range-flagged.
    run(db, "fitness-test-log", "hip-flexion", "--side", "left", "--load", "40", "--reps", "8")
    run(db, "fitness-test-log", "hip-extension", "--side", "left", "--load", "10", "--reps", "8")
    hip = next(r for r in run(db, "strength-ratios", "--view", "tested")["rows"] if r["key"] == "hip")
    assert hip["ideal"] is None and hip["band"] is None
    left = next(s for s in hip["sides"] if s["side"] == "left")
    # a ~4.0 ratio would scream out_of_range under any band — must stay trend_only
    assert left["ratio"] == 3.99 and left["flag"] == "trend_only"


def test_tested_hip_side_gap_flag_still_applies(db):
    # trend-only removes the ratio target, NOT the Grygorowicz >15% limb-gap flag
    for side, load in (("left", 40), ("right", 60)):
        run(db, "fitness-test-log", "hip-flexion", "--side", side, "--load", str(load), "--reps", "8")
        run(db, "fitness-test-log", "hip-extension", "--side", side, "--load", "30", "--reps", "8")
    hip = next(r for r in run(db, "strength-ratios", "--view", "tested")["rows"] if r["key"] == "hip")
    assert hip["side_gap_flag"] == "asymmetry"


def test_tested_ratio_outside_rep_protocol_is_not_range_flagged(db):
    run(db, "fitness-test-log", "tibialis-raise", "--side", "left",
        "--load", "12", "--reps", "7")
    run(db, "fitness-test-log", "calf-raise", "--side", "left",
        "--load", "83.4", "--reps", "15")
    ankle = next(r for r in run(
        db, "strength-ratios", "--view", "tested")["rows"]
        if r["key"] == "ankle")
    left = next(s for s in ankle["sides"] if s["side"] == "left")
    assert left["ratio"] > 0
    assert left["protocol_valid"] is False
    assert left["flag"] == "outside_protocol_range"
    assert left["num_reps"] == 7 and left["den_reps"] == 15
    assert ankle["band"] is None and ankle["ideal"] is None


def test_everyday_vertical_pushpull_is_trend_only(db):
    hevy(db, [("2026-07-09", "Dumbbell Shoulder Press", 1, "normal", 20, 5),
              ("2026-07-09", "Lat Pulldown", 1, "normal", 30, 5)])
    r = run_at(
        db, FROZEN_RECENT_DATE, "strength-ratios", "--view", "everyday"
    )
    vp = next(row for row in r["rows"] if row["num_pattern"] == "vertical-push")
    assert vp["target"] is None and vp["flag"] == "trend_only"       # no defensible target
