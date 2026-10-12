"""Body-figure projections preserve descriptive values without normative cutoffs.

Mapping checks retain explicit undrawn regions; behavior checks distinguish
recorded comparisons, missing data and comparison limitations.
"""
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

sys.path.insert(0, str(ROOT))
import health  # noqa: E402


# ── mapping integrity ────────────────────────────────────────────────────────

def test_ratio_map_covers_every_ratio_seed_key():
    # a new RATIO_SEED pair without a figure projection must fail loudly here,
    # not silently vanish from the lens
    assert set(health.FIGURE_RATIO_MAP) == {s["key"] for s in health.RATIO_SEED}


def test_ratio_map_targets_are_known_region_bases():
    bases = set(health._FIG_BASE_GROUP)
    for key, m in health.FIGURE_RATIO_MAP.items():
        assert set(m["num"]) | set(m["den"]) <= bases, key


def test_unprojectable_sides_are_explicitly_declared():
    # an empty num/den projection is allowed ONLY with a stated reason (the
    # ER:IR internal-rotator case — subscapularis is undrawn); anything else
    # empty-and-silent is a map bug
    for key, m in health.FIGURE_RATIO_MAP.items():
        if not m["num"] or not m["den"]:
            assert m.get("unrepresentable"), f"{key} has an empty side but no reason"
    assert not health.FIGURE_RATIO_MAP["erir"]["den"]
    assert "internal rotator" in health.FIGURE_RATIO_MAP["erir"]["unrepresentable"]


def test_balance_legend_distinguishes_recorded_comparisons_from_missing_data():
    assert [x["status"] for x in health.FIGURE_BALANCE_LEGEND] == \
        ["trend_only", "untested"]
    # Amber means a real measurement exists, but no defensible target range.
    trend = [x for x in health.FIGURE_BALANCE_LEGEND if x["status"] == "trend_only"][0]
    assert trend["label"] == "measured trend only — no valid target range"


# ── behavior (subprocess, real DB) ───────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def run_fail(db, *args):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True)
    assert r.returncode != 0
    return r.stderr


def today(back=0):
    return (date.today() - timedelta(days=back)).isoformat()


def seed_ft(db, rows):
    """rows: (movement, side, value[, days_back[, voided]]). Strength movements
    use the protocol-valid common 8-rep factor (ratios remain exact); hold
    movements store seconds."""
    con = sqlite3.connect(db)
    for row in rows:
        movement, side, value = row[0], row[1], row[2]
        back = row[3] if len(row) > 3 else 0
        voided = row[4] if len(row) > 4 else 0
        kind = health.CATALOG[movement]["kind"]
        con.execute(
            "INSERT INTO fitness_tests(date, movement, side, load_kg, reps, seconds, voided)"
            " VALUES(?,?,?,?,?,?,?)",
            (today(back), movement, side,
             value if kind == "strength" else None,
             8 if kind == "strength" else None,
             value if kind == "hold" else None, voided))
    con.commit()
    con.close()


def balance(db, *extra):
    return run(db, "muscle-map", "--lens", "strength-balance", *extra)


def test_empty_db_is_all_untested_grey(db):
    d = balance(db)
    assert d["lens"] == "strength-balance" and d["side_mode"] == "combined"
    assert len(d["regions"]) == 68 and len(d["non_muscle"]) == 21
    assert all(r["status"] == "untested" and r["worth_focus"] is False
               for r in d["regions"].values())
    assert [x["status"] for x in d["legend"]] == \
        ["trend_only", "untested"]
    assert next(x["label"] for x in d["legend"]
                if x["status"] == "trend_only") == \
        "measured trend only — no valid target range"


def test_low_ratio_is_recorded_without_flagging_a_weak_muscle(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100)])

    result = balance(db)

    for base in ("hamstrings-medial", "hamstrings-lateral", "quads"):
        assert result["regions"][f"{base}-left"]["status"] == "trend_only"
        assert result["regions"][f"{base}-left"]["worth_focus"] is False
        assert result["regions"][f"{base}-right"]["status"] == "untested"

def test_former_in_band_ratio_is_descriptive_for_both_muscles(db):
    seed_ft(db, [("leg-curl", "left", 60), ("leg-extension", "left", 100)])

    result = balance(db)

    assert result["regions"]["hamstrings-medial-left"]["status"] == "trend_only"
    assert result["regions"]["quads-left"]["status"] == "trend_only"

def test_high_bilateral_hold_ratio_does_not_infer_deficiency(db):
    seed_ft(db, [("mcgill-flexor", "bilateral", 120), ("mcgill-extensor", "bilateral", 100)])

    result = balance(db)

    for side in ("left", "right"):
        for base in ("lower-back-erectors", "abs-upper", "abs-lower"):
            assert result["regions"][f"{base}-{side}"]["status"] == "trend_only"
            assert result["regions"][f"{base}-{side}"]["worth_focus"] is False

def test_trend_only_pair_remains_distinct_from_missing_data(db):
    seed_ft(db, [("hip-flexion", "left", 60), ("hip-extension", "left", 100)])
    d = balance(db)
    assert d["regions"]["hip-flexor-left"]["status"] == "trend_only"
    assert d["regions"]["gluteus-maximus-left"]["status"] == "trend_only"
    assert d["regions"]["hip-flexor-left"]["worth_focus"] is False


def test_outside_rep_protocol_is_recorded_but_not_range_judged(db):
    con = sqlite3.connect(db)
    con.executemany(
        """INSERT INTO fitness_tests(
             date,movement,side,load_kg,reps,source)
           VALUES(?,?,?,?,?,'test')""",
        [(today(), "tibialis-raise", "left", 12, 7),
         (today(), "calf-raise", "left", 83.4, 15)])
    con.commit()
    con.close()
    d = balance(db)
    assert d["regions"]["tibialis-anterior-left"]["status"] == "trend_only"
    assert d["regions"]["calves-gastroc-medial-left"]["status"] == "trend_only"
    assert "outside protocol range" in \
        d["regions"]["tibialis-anterior-left"]["tips"][0]


def test_shared_region_retains_each_contributing_ratio_without_severity_ranking(db):
    seed_ft(db, [("rear-delt-fly", "left", 50), ("front-raise", "left", 100),
                 ("shoulder-er", "left", 50), ("shoulder-ir", "left", 100)])

    result = balance(db)

    region = result["regions"]["deltoid-rear-left"]
    assert region["status"] == "trend_only"
    assert len(region["tips"]) == 2
    assert result["regions"]["shoulder-front-left"]["status"] == "trend_only"
    assert "not independent muscle measurements" in result["note"]

def test_tooltip_lists_exact_ratio_and_source_without_normative_band(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100)])

    result = balance(db)

    tip = result["regions"]["hamstrings-medial-left"]["tips"][0]
    assert tip.startswith("Hamstring:Quadriceps left 0.5 — recorded comparison; no validated target range")
    assert "Aagaard" in tip
    assert "ideal" not in tip
    assert "band 0.55" not in tip

def test_voided_and_stale_tests_leave_regions_untested(db):
    seed_ft(db, [("leg-curl", "left", 50, 0, 1),          # voided
                 ("leg-extension", "left", 100, 0, 1),
                 ("biceps-curl", "left", 50, health.ATHLETIC_STALE_DAYS + 30),
                 ("triceps-extension", "left", 50, health.ATHLETIC_STALE_DAYS + 30)])
    d = balance(db)
    assert d["regions"]["hamstrings-medial-left"]["status"] == "untested"
    assert d["regions"]["biceps-left"]["status"] == "untested"


def test_erir_unrepresentable_is_surfaced_never_on_the_chest(db):
    seed_ft(db, [("shoulder-er", "left", 50), ("shoulder-ir", "left", 100)])
    d = balance(db)
    reg = d["regions"]["deltoid-rear-left"]
    assert reg["status"] == "trend_only" and reg["approx"] is True
    assert any(x["key"] == "erir" for x in d["unrepresentable"])
    assert "internal rotator" in " ".join(reg["tips"]).lower()
    for b in ("chest-upper", "chest-lower"):
        for s in ("left", "right"):
            assert d["regions"][f"{b}-{s}"]["status"] == "untested"


def test_lr_mode_records_both_sides_without_flagging_the_lower_value(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100),
                 ("leg-curl", "right", 80), ("leg-extension", "right", 100)])

    result = balance(db, "--side-mode", "lr")

    assert result["side_mode"] == "lr"
    for base in ("hamstrings-medial", "quads"):
        for side in ("left", "right"):
            assert result["regions"][f"{base}-{side}"]["status"] == "trend_only"
            assert result["regions"][f"{base}-{side}"]["worth_focus"] is False

def test_lr_mode_preserves_single_sided_records_without_inventing_other_side(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100),
                 ("mcgill-flexor", "bilateral", 120), ("mcgill-extensor", "bilateral", 100)])

    result = balance(db, "--side-mode", "lr")

    assert result["regions"]["hamstrings-medial-left"]["status"] == "trend_only"
    assert result["regions"]["hamstrings-medial-right"]["status"] == "untested"
    assert "right not recorded" in result["regions"]["hamstrings-medial-left"]["tips"][0]
    assert "L/R gap unavailable" in result["regions"]["hamstrings-medial-left"]["tips"][0]
    assert result["regions"]["lower-back-erectors-left"]["status"] == "untested"

def test_lr_gap_is_descriptive_for_tracking_pairs(db):
    seed_ft(db, [("hip-flexion", "left", 60), ("hip-extension", "left", 100),
                 ("hip-flexion", "right", 80), ("hip-extension", "right", 100)])

    result = balance(db, "--side-mode", "lr")

    assert result["regions"]["hip-flexor-left"]["status"] == "trend_only"
    assert result["regions"]["hip-flexor-right"]["status"] == "trend_only"
    assert result["regions"]["gluteus-maximus-left"]["status"] == "trend_only"

def test_former_lr_gap_boundary_is_reported_without_a_cutoff_judgment(db):
    # Retains the rounding-boundary regression without retaining its old cutoff.
    seed_ft(db, [("leg-curl", "left", 113.3), ("leg-extension", "left", 100),
                 ("leg-curl", "right", 133.3), ("leg-extension", "right", 100)])

    result = balance(db, "--side-mode", "lr")

    region = result["regions"]["hamstrings-medial-left"]
    assert region["status"] == "trend_only"
    assert "L/R gap 15.0%" in region["tips"][0]
    assert "flag" not in region["tips"][0]

def test_lr_tooltip_shows_exact_values_gap_and_source_with_limitations(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100),
                 ("leg-curl", "right", 80), ("leg-extension", "right", 100)])

    result = balance(db, "--side-mode", "lr")

    tip = result["regions"]["hamstrings-medial-left"]["tips"][0]
    assert "left 63.3 kg e1rm, right 101.3 kg e1rm" in tip
    assert "L/R gap 37.5%" in tip
    assert "setup unknown" in tip
    assert "effort unknown or pain limited" in tip
    assert "Grygorowicz" in tip
    assert "no validated cutoff" in tip

def test_lr_zero_values_remain_recorded_with_an_unavailable_gap(db):
    # A recorded zero is not missing, and 0/0 must not imply balanced limbs.
    seed_ft(db, [("tibialis-raise", "left", 0), ("calf-raise", "left", 100),
                 ("tibialis-raise", "right", 0), ("calf-raise", "right", 100)])

    result = balance(db, "--side-mode", "lr")

    assert result["regions"]["tibialis-anterior-left"]["status"] == "trend_only"
    assert result["regions"]["tibialis-anterior-right"]["status"] == "trend_only"
    tip = result["regions"]["tibialis-anterior-left"]["tips"][0]
    assert "left 0 kg e1rm, right 0 kg e1rm" in tip
    assert "L/R gap unavailable" in tip
    assert "both recorded values are zero" in tip
    assert result["regions"]["calves-soleus-left"]["status"] == "trend_only"
    assert "L/R gap 0.0%" in result["regions"]["calves-soleus-left"]["tips"][0]


def test_lr_comparison_does_not_require_an_opposing_muscle_test(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-curl", "right", 80)])

    result = balance(db, "--side-mode", "lr")

    assert result["regions"]["hamstrings-medial-left"]["status"] == "trend_only"
    assert "L/R gap 37.5%" in result["regions"]["hamstrings-medial-left"]["tips"][0]
    assert result["regions"]["quads-left"]["status"] == "untested"


def test_lr_different_equipment_preserves_values_with_a_comparison_limitation(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-curl", "right", 80)])
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE fitness_tests SET equipment_note='Fictional machine A' WHERE side='left'")
        connection.execute("UPDATE fitness_tests SET equipment_note='Fictional machine B' WHERE side='right'")

    result = balance(db, "--side-mode", "lr")

    region = result["regions"]["hamstrings-medial-left"]
    assert region["status"] == "trend_only"
    assert region["worth_focus"] is False
    assert "L/R gap 37.5%" in region["tips"][0]
    assert "different setups" in region["tips"][0]


def test_lr_outside_rep_protocol_is_visible_without_a_deficiency_label(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-curl", "right", 80)])
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE fitness_tests SET reps=15 WHERE side='right'")

    result = balance(db, "--side-mode", "lr")

    region = result["regions"]["hamstrings-medial-left"]
    assert region["status"] == "trend_only"
    assert "L/R gap 47.2%" in region["tips"][0]
    assert "outside protocol range" in region["tips"][0]


def test_combined_zero_denominator_reports_why_no_ratio_can_be_displayed(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 0)])

    result = balance(db)

    region = result["regions"]["hamstrings-medial-left"]
    assert region["status"] == "untested"
    assert "ratio unavailable" in region["tips"][0]
    assert "zero denominator" in region["tips"][0]


def test_side_bridge_projects_its_recorded_bilateral_denominator_on_both_sides(db):
    seed_ft(db, [("mcgill-side-bridge", "left", 60), ("mcgill-extensor", "bilateral", 100)])

    ratios = run(db, "strength-ratios", "--view", "tested")
    result = balance(db)

    row = next(row for row in ratios["rows"] if row["key"] == "mcgill-sbe")
    left = next(side for side in row["sides"] if side["side"] == "left")
    assert left["ratio"] == 0.6
    assert left["num_side"] == "left"
    assert left["den_side"] == "bilateral"
    assert result["regions"]["obliques-left"]["status"] == "trend_only"
    assert result["regions"]["obliques-right"]["status"] == "untested"
    for side in ("left", "right"):
        region = result["regions"][f"lower-back-erectors-{side}"]
        assert region["status"] == "trend_only"
        assert "left Side bridge / bilateral Trunk extensors 0.6" in region["tips"][0]
        assert "one bilateral measurement" in region["tips"][0]


def test_two_side_bridges_retain_one_shared_extensor_parent(db):
    seed_ft(db, [("mcgill-side-bridge", "left", 60), ("mcgill-side-bridge", "right", 70),
                 ("mcgill-extensor", "bilateral", 100)])

    ratios = run(db, "strength-ratios", "--view", "tested")
    result = balance(db)

    row = next(row for row in ratios["rows"] if row["key"] == "mcgill-sbe")
    left, right = row["sides"]
    assert (left["ratio"], right["ratio"]) == (0.6, 0.7)
    assert left["parent_ids"][1] == right["parent_ids"][1]
    assert left["den_side"] == right["den_side"] == "bilateral"
    for side in ("left", "right"):
        region = result["regions"][f"lower-back-erectors-{side}"]
        assert region["status"] == "trend_only"
        assert len(region["tips"]) == 2
        assert all("not independent left/right measurements" in tip for tip in region["tips"])


def test_side_bridge_without_bilateral_denominator_cannot_paint_extensors(db):
    seed_ft(db, [("mcgill-side-bridge", "left", 60)])

    ratios = run(db, "strength-ratios", "--view", "tested")
    result = balance(db)

    row = next(row for row in ratios["rows"] if row["key"] == "mcgill-sbe")
    assert row["sides"][0]["status"] == "insufficient_data"
    assert row["sides"][0].get("den_side") is None
    assert result["regions"]["lower-back-erectors-left"]["status"] == "untested"
    assert result["regions"]["lower-back-erectors-right"]["status"] == "untested"


def test_ordinary_unilateral_ratio_keeps_each_component_on_its_recorded_side(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100)])

    ratios = run(db, "strength-ratios", "--view", "tested")
    result = balance(db)

    row = next(row for row in ratios["rows"] if row["key"] == "hq")
    left = row["sides"][0]
    assert left["num_side"] == left["den_side"] == "left"
    assert left["ratio"] == 0.5
    assert result["regions"]["quads-left"]["status"] == "trend_only"
    assert result["regions"]["quads-right"]["status"] == "untested"


def test_bad_side_mode_is_refused(db):
    err = run_fail(db, "muscle-map", "--lens", "strength-balance",
                   "--side-mode", "sideways")
    assert "side-mode" in err


def test_activation_lens_payload_is_unchanged_by_phase2(db):
    d = run(db, "muscle-map")
    assert d["lens"] == "activation" and "levels" in d
    assert "legend" not in d and "status" not in next(iter(d["regions"].values()))
