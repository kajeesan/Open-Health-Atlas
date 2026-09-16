"""§3f muscle figure, Phase 2: the `muscle-map --lens strength-balance`
payload + the authored FIGURE_RATIO_MAP's integrity.

Two layers, mirroring test_muscle_map.py:
- mapping integrity — every RATIO_SEED pair is either projected onto known
  figure region bases or EXPLICITLY listed unrepresentable (never silently
  dropped, never guessed onto a wrong region);
- behavior — statuses derive from _tested_ratios() output only: deficient
  (weak side of an out-of-band ratio), balanced (in cited band), trend_only
  (pattern signal, no cited band — design rule 2026-07-19), untested (honest
  grey default). Worst-status-wins for shared regions; the L/R sub-mode flags
  only the weaker side of a >15% between-limb gap (Grygorowicz).
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


def test_balance_legend_is_the_four_statuses_in_severity_order():
    assert [x["status"] for x in health.FIGURE_BALANCE_LEGEND] == \
        ["deficient", "balanced", "trend_only", "untested"]
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
        ["deficient", "balanced", "trend_only", "untested"]
    assert next(x["label"] for x in d["legend"]
                if x["status"] == "trend_only") == \
        "measured trend only — no valid target range"


def test_below_band_flags_num_regions_deficient_weak_side_only(db):
    # hq left = 50/100 = 0.50 < band lo 0.55 → hamstrings (num) weak → red,
    # quads (den) tested-not-weak → balanced; the untested right leg stays grey
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100)])
    d = balance(db)
    r = d["regions"]
    for b in ("hamstrings-medial", "hamstrings-lateral"):
        assert r[f"{b}-left"]["status"] == "deficient"
        assert r[f"{b}-left"]["worth_focus"] is True
        assert r[f"{b}-right"]["status"] == "untested"
    assert r["quads-left"]["status"] == "balanced"
    assert r["quads-right"]["status"] == "untested"


def test_in_band_ratio_is_balanced_both_muscles(db):
    seed_ft(db, [("leg-curl", "left", 60), ("leg-extension", "left", 100)])
    d = balance(db)
    assert d["regions"]["hamstrings-medial-left"]["status"] == "balanced"
    assert d["regions"]["quads-left"]["status"] == "balanced"


def test_above_band_flags_den_regions_deficient(db):
    # mcgill-fe = 120/100 = 1.2 > band hi 1.0 → extensors (den) weak → red on
    # both sides (bilateral hold); flexors (num) balanced
    seed_ft(db, [("mcgill-flexor", "bilateral", 120),
                 ("mcgill-extensor", "bilateral", 100)])
    d = balance(db)
    r = d["regions"]
    for s in ("left", "right"):
        assert r[f"lower-back-erectors-{s}"]["status"] == "deficient"
        assert r[f"abs-upper-{s}"]["status"] == "balanced"
        assert r[f"abs-lower-{s}"]["status"] == "balanced"


def test_trend_only_pair_is_amber_not_red_or_grey(db):
    # hip flex:ext has no cited band (RATIO_SEED band None) → amber pattern
    # signal on both muscles (design rule 1) — never deficient, never grey
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
    # Both muscles were tested, so they are amber rather than grey. The
    # 6–8-rep target band is not used to paint a red deficiency.
    assert d["regions"]["tibialis-anterior-left"]["status"] == "trend_only"
    assert d["regions"]["calves-gastroc-medial-left"]["status"] == "trend_only"
    assert "outside the 6–8-rep protocol" in \
        d["regions"]["tibialis-anterior-left"]["tips"][0]


def test_shared_region_takes_worst_status(db):
    # deltoid-rear is num of BOTH reardelt (0.50 in band → green) and erir
    # (0.50 < 0.66 floor → red) → red wins, and the tooltip lists both ratios.
    # (The other shared region, lower-back-erectors, can't be exercised this
    # way: mcgill-sbe pairs per-side against the bilateral Sørensen hold and
    # is insufficient_data in _tested_ratios as shipped — pre-existing.)
    seed_ft(db, [("rear-delt-fly", "left", 50), ("front-raise", "left", 100),
                 ("shoulder-er", "left", 50), ("shoulder-ir", "left", 100)])
    d = balance(db)
    reg = d["regions"]["deltoid-rear-left"]
    assert reg["status"] == "deficient"
    assert len(reg["tips"]) == 2
    assert d["regions"]["shoulder-front-left"]["status"] == "balanced"


def test_tooltip_lists_ratio_band_and_cite(db):
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100)])
    d = balance(db)
    tip = d["regions"]["hamstrings-medial-left"]["tips"][0]
    # exact prefix — a substring check like "0.5 in tip" would be vacuously
    # true via the band's own "0.55" (review finding)
    assert tip.startswith("Hamstring:Quadriceps left 0.5 (band 0.55–0.65, ideal 0.6)")
    assert "Aagaard" in tip                      # cite rides on the tooltip


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
    # ER weak (0.50 < 0.66 floor) → rear-delt colored (approx: ER shown on the
    # rear-delt region); the IR side is undrawn — surfaced in the payload and
    # the rear-delt tooltip, and NO chest region is ever colored by this ratio
    reg = d["regions"]["deltoid-rear-left"]
    assert reg["status"] == "deficient" and reg["approx"] is True
    assert any(x["key"] == "erir" for x in d["unrepresentable"])
    assert "internal rotator" in " ".join(reg["tips"]).lower()
    for b in ("chest-upper", "chest-lower"):
        for s in ("left", "right"):
            assert d["regions"][f"{b}-{s}"]["status"] == "untested"


def test_lr_mode_flags_only_the_weaker_side_of_a_gap(db):
    # hams: left 50 vs right 80 → gap 37.5% > 15% → LEFT (weaker) red, right
    # green. quads symmetric → green both. The right ratio (0.80) is above
    # band — combined-mode red on quads-right — but the L/R view judges the
    # between-limb gap only, so quads stay green here.
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100),
                 ("leg-curl", "right", 80), ("leg-extension", "right", 100)])
    d = balance(db, "--side-mode", "lr")
    assert d["side_mode"] == "lr"
    r = d["regions"]
    assert r["hamstrings-medial-left"]["status"] == "deficient"
    assert r["hamstrings-medial-left"]["worth_focus"] is True
    assert r["hamstrings-medial-right"]["status"] == "balanced"
    assert r["quads-left"]["status"] == "balanced"
    assert r["quads-right"]["status"] == "balanced"
    # combined mode on the same data: quads-right IS the weak link (0.80 above
    # band → den deficient)
    c = balance(db)
    assert c["regions"]["quads-right"]["status"] == "deficient"


def test_lr_mode_needs_both_sides_and_per_side_pairs(db):
    # single-sided data → no gap computable → untested in L/R view; the
    # bilateral McGill fe pair carries no left/right information at all
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100),
                 ("mcgill-flexor", "bilateral", 120),
                 ("mcgill-extensor", "bilateral", 100)])
    d = balance(db, "--side-mode", "lr")
    assert d["regions"]["hamstrings-medial-left"]["status"] == "untested"
    assert d["regions"]["lower-back-erectors-left"]["status"] == "untested"


def test_lr_gap_applies_to_trend_only_pairs_too(db):
    # the >15% between-limb threshold is Grygorowicz-validated independent of
    # any ratio target (RATIO_SEED note) — so the band-less hip pair still
    # flags its weaker side in the L/R view
    seed_ft(db, [("hip-flexion", "left", 60), ("hip-extension", "left", 100),
                 ("hip-flexion", "right", 80), ("hip-extension", "right", 100)])
    d = balance(db, "--side-mode", "lr")
    assert d["regions"]["hip-flexor-left"]["status"] == "deficient"
    assert d["regions"]["hip-flexor-right"]["status"] == "balanced"
    assert d["regions"]["gluteus-maximus-left"]["status"] == "balanced"


def test_lr_gap_boundary_matches_the_ratios_card(db):
    # raw hamstring gap (133.3-113.3)/133.3 = 0.150038 rounds to 0.150 — the
    # Strength-ratios card rounds to 3 dp BEFORE its >0.15 check and reports
    # 'ok', so the figure must too (review finding: the raw compare painted
    # red here while the card said ok, breaking the shared-invariant)
    seed_ft(db, [("leg-curl", "left", 113.3), ("leg-extension", "left", 100),
                 ("leg-curl", "right", 133.3), ("leg-extension", "right", 100)])
    d = balance(db, "--side-mode", "lr")
    assert d["regions"]["hamstrings-medial-left"]["status"] == "balanced"
    assert d["regions"]["hamstrings-medial-right"]["status"] == "balanced"


def test_lr_tooltip_shows_gap_percent_threshold_and_cite(db):
    # hams gap (80-50)/80 = 37.5% — the tip must show the true one-decimal
    # percent (an integer-rounded "15%" beside "flag >15%" self-contradicts
    # at the boundary — review finding) + the threshold's citation
    seed_ft(db, [("leg-curl", "left", 50), ("leg-extension", "left", 100),
                 ("leg-curl", "right", 80), ("leg-extension", "right", 100)])
    d = balance(db, "--side-mode", "lr")
    tip = " ".join(d["regions"]["hamstrings-medial-left"]["tips"])
    assert "37.5%" in tip and "Grygorowicz" in tip


def test_lr_zero_valued_pair_stays_untested_not_balanced(db):
    # load_kg 0 is clamp-legal (bodyweight-only test) → e1RM 0.0 on both
    # sides: no between-limb gap is computable (0/0) — the regions must stay
    # honest grey, never assert green "balanced" from no information
    # (review finding: top==0 fell into the gap-0 → balanced branch)
    seed_ft(db, [("tibialis-raise", "left", 0), ("calf-raise", "left", 100),
                 ("tibialis-raise", "right", 0), ("calf-raise", "right", 100)])
    d = balance(db, "--side-mode", "lr")
    assert d["regions"]["tibialis-anterior-left"]["status"] == "untested"
    assert d["regions"]["tibialis-anterior-right"]["status"] == "untested"
    # the denominator (calves, 100 both sides) still judges normally
    assert d["regions"]["calves-soleus-left"]["status"] == "balanced"


def test_bad_side_mode_is_refused(db):
    err = run_fail(db, "muscle-map", "--lens", "strength-balance",
                   "--side-mode", "sideways")
    assert "side-mode" in err


def test_activation_lens_payload_is_unchanged_by_phase2(db):
    d = run(db, "muscle-map")
    assert d["lens"] == "activation" and "levels" in d
    assert "legend" not in d and "status" not in next(iter(d["regions"].values()))
