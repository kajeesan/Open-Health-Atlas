"""§3g physio: pain/self-test/exercise-trial capture (append-only, soft-void,
agent-only writers) + the cited catalogs + the `muscle-map --lens pain`
figure/loop payload.

Doctrine under test (design contract 2026-07-20, mirrors the vault diagnosis-review
rules): the loop ORGANIsES cited evidence and NEVER diagnoses; pain is 0–10 NRS
(distinct from the 1–5 wellbeing ratings); the pain lens may color JOINT regions
(a documented pain-lens-only exception to "non-muscle always neutral"); a
model-boundary flag (radiating/numbness/night-pain + the can't-miss set) is
surfaced ONCE, never a reflexive referral; the configured-medication vitals line stays;
capture is collector/agent-only (writers pinned OUT of both bridge allowlists —
that drift guard lives in tests/test_bridge.py, this file pins the engine).
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


# ── catalog integrity (fail loudly on a typo, like the ratio-map tests) ───────

def test_pain_regions_map_to_known_svg_bases():
    # every pain region draws on real svg bases — muscle bases OR the joint
    # (non-muscle) bases the pain lens is allowed to color (design rule 3)
    muscle = set(health._FIG_BASE_GROUP)
    joint = {i.rsplit("-", 1)[0] if i.endswith(("-left", "-right")) else i
             for i in health.FIGURE_NON_MUSCLE}
    known = muscle | joint
    for region, spec in health.PAIN_CAUSE_MAP.items():
        assert spec["svg"], f"{region} has no svg projection"
        assert set(spec["svg"]) <= known, f"{region} svg {spec['svg']} unknown"


def test_every_referenced_self_test_and_drill_exists():
    for region, spec in health.PAIN_CAUSE_MAP.items():
        for cause in spec["causes"]:
            for t in cause["tests"]:
                assert t["key"] in health.SELF_TEST_CATALOG, (region, t["key"])
                assert t["expect"] in ("positive", "negative")
            for d in cause["drills"]:
                assert d in health.REHAB_CATALOG, (region, d)


def test_catalog_entries_carry_citations():
    for spec in health.PAIN_CAUSE_MAP.values():
        for cause in spec["causes"]:
            assert cause.get("cite"), cause.get("cause")
    for t in health.SELF_TEST_CATALOG.values():
        assert t.get("cite")
    for d in health.REHAB_CATALOG.values():
        assert d.get("cite")


def test_v1_seed_covers_the_owner_named_regions():
    # knee / low-back / glute-posterior-chain / shoulder (design rule 5)
    keys = set(health.PAIN_CAUSE_MAP)
    assert {"anterior-knee", "low-back", "lateral-hip", "hamstring", "shoulder"} <= keys


def test_red_flag_set_is_the_owner_confirmed_list():
    assert set(health.PAIN_RED_FLAGS) == {
        "radiating", "numbness", "night-pain", "progressive", "trauma",
        "systemic", "cauda-equina"}


def test_pain_bands_are_pinned():
    for nrs, band in [(0, "none"), (1, "mild"), (3, "mild"), (4, "moderate"),
                      (6, "moderate"), (7, "severe"), (10, "severe")]:
        assert health._pain_band(nrs) == band, (nrs, band)


# ── writers: append-only, validated, agent-only (subprocess, real DB) ─────────

@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args, ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True)
    if ok:
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    assert r.returncode != 0, f"expected failure, got {r.stdout}"
    return r.stderr


def today(back=0):
    return (date.today() - timedelta(days=back)).isoformat()


def pain(db, *args):
    return run(db, "muscle-map", "--lens", "pain", *args)


def test_pain_log_appends_and_validates(db):
    d = run(db, "pain-log", "anterior-knee", "--intensity", "6", "--side", "left")
    assert d["ok"] and d["logged"]["region"] == "anterior-knee"
    assert d["logged"]["intensity"] == 6 and d["logged"]["side"] == "left"
    # NRS is 0–10
    assert "0" in run(db, "pain-log", "anterior-knee", "--intensity", "11", ok=False)
    assert "0" in run(db, "pain-log", "anterior-knee", "--intensity", "-1", ok=False)
    # unknown region refused (no guessing onto the figure)
    assert "region" in run(db, "pain-log", "left-earlobe", "--intensity", "3", ok=False)
    # unknown quality / red-flag refused
    assert run(db, "pain-log", "low-back", "--intensity", "3", "--quality", "zesty", ok=False)
    assert run(db, "pain-log", "low-back", "--intensity", "3", "--flags", "haunted", ok=False)


def test_self_test_log_validates_test_and_result(db):
    d = run(db, "self-test-log", "decline-squat-pain", "--result", "positive", "--side", "left")
    assert d["ok"] and d["logged"]["test"] == "decline-squat-pain"
    assert run(db, "self-test-log", "made-up-test", "--result", "positive", ok=False)
    assert run(db, "self-test-log", "decline-squat-pain", "--result", "maybe", ok=False)


def test_exercise_trial_log_validates_drill_and_response(db):
    d = run(db, "exercise-trial-log", "patellar-isometrics", "--response", "better",
            "--target", "anterior-knee", "--dose", "5x45s", "--pain-during", "3")
    assert d["ok"] and d["logged"]["drill"] == "patellar-isometrics"
    assert d["logged"]["response"] == "better"
    assert run(db, "exercise-trial-log", "not-a-drill", "--response", "better", ok=False)
    assert run(db, "exercise-trial-log", "patellar-isometrics", "--response", "cured", ok=False)


def test_physio_void_is_soft_and_hides_the_row(db):
    run(db, "pain-log", "anterior-knee", "--intensity", "8", "--side", "left")
    before = pain(db)["regions"]["knee-left"]
    assert before["status"] == "severe"
    v = run(db, "physio-void", "--kind", "pain", "1", "--reason", "mis-logged side")
    assert v["ok"]
    # soft-void: the row still exists (deletion law), the read hides it
    con = sqlite3.connect(db)
    row = con.execute("SELECT voided, void_reason FROM pain_log WHERE id=1").fetchone()
    con.close()
    assert row[0] == 1 and "mis-logged" in row[1]
    assert pain(db)["regions"]["knee-left"]["status"] == "none"
    assert "no pain_log row" in run(db, "physio-void", "--kind", "pain", "999", ok=False)


# ── pain lens: figure + loop ──────────────────────────────────────────────────

def test_empty_db_is_all_none_grey(db):
    d = pain(db)
    assert d["lens"] == "pain"
    assert all(r["status"] == "none" for r in d["regions"].values())
    assert d["loop"] == []
    assert [x["status"] for x in d["legend"]] == ["none", "mild", "moderate", "severe"]
    assert "chest pain" in d["cv_note"].lower() or "racing" in d["cv_note"].lower()


def test_pain_colors_region_by_nrs_band(db):
    run(db, "pain-log", "low-back", "--intensity", "5", "--side", "central")
    d = pain(db)
    # low back is a MUSCLE region (core erectors + QL) — both sides colored for
    # a central log
    for rid in ("lower-back-erectors-left", "lower-back-erectors-right",
                "lower-back-ql-left", "lower-back-ql-right"):
        assert d["regions"][rid]["status"] == "moderate", rid
        assert d["regions"][rid]["nrs"] == 5


def test_pain_lens_colors_joint_regions_not_in_non_muscle(db):
    # design rule 3: the knee is a JOINT (normally FIGURE_NON_MUSCLE / neutral);
    # the pain lens colors it, so it must be present in regions AND removed from
    # this payload's non_muscle so the dumb component paints it
    run(db, "pain-log", "anterior-knee", "--intensity", "2", "--side", "right")
    d = pain(db)
    assert d["regions"]["knee-right"]["status"] == "mild"
    assert "knee-right" not in set(d["non_muscle"])
    # a joint pain never maps to still stays neutral (e.g. the head)
    assert "head" in set(d["non_muscle"])


def test_window_and_max_intensity_per_region(db):
    run(db, "pain-log", "low-back", "--intensity", "3", "--side", "central")
    run(db, "pain-log", "low-back", "--intensity", "8", "--side", "central")
    # windowed: an old row outside --days is ignored
    run(db, "pain-log", "low-back", "--intensity", "10", "--side", "central", "--date", today(60))
    d = pain(db, "--days", "30")
    assert d["regions"]["lower-back-erectors-left"]["nrs"] == 8   # max in window, not 10


def test_default_window_is_the_pain_stale_default_not_the_activation_7d(db):
    # muscle-map --days defaults are per-lens: the pain lens must use its own
    # long default (PAIN_STALE_DAYS), NOT the activation lens's 7d — else pain
    # logged >7d ago silently vanishes from the panel (which sends no --days)
    run(db, "pain-log", "anterior-knee", "--intensity", "7", "--side", "left",
        "--date", today(20))
    d = pain(db)                                   # no --days → pain default
    assert d["window_days"] == health.PAIN_STALE_DAYS
    assert d["regions"]["knee-left"]["status"] == "severe"


def test_loop_lists_cited_candidate_causes(db):
    run(db, "pain-log", "anterior-knee", "--intensity", "6", "--side", "left")
    d = pain(db)
    loop = [x for x in d["loop"] if x["region"] == "anterior-knee"]
    assert len(loop) == 1
    causes = {c["cause"] for c in loop[0]["causes"]}
    assert "Patellofemoral pain" in causes and "Patellar tendinopathy" in causes
    # every candidate rides its citation + suggested cited drills, never a push
    for c in loop[0]["causes"]:
        assert c["cite"] and c["drills"]
        assert c["support"] == "untested"      # no self-test logged yet


def test_self_test_result_marks_candidate_support_honestly(db):
    run(db, "pain-log", "anterior-knee", "--intensity", "6", "--side", "left")
    # a positive decline-squat points TOWARD patellar tendinopathy, AGAINST PFP
    run(db, "self-test-log", "decline-squat-pain", "--result", "positive", "--side", "left")
    d = pain(db)
    causes = {c["cause"]: c for c in
              next(x for x in d["loop"] if x["region"] == "anterior-knee")["causes"]}
    assert causes["Patellar tendinopathy"]["support"] == "supported"
    assert causes["Patellofemoral pain"]["support"] == "against"
    # honest: the evidence is shown, never a bare verdict
    assert any("decline-squat" in e for e in causes["Patellar tendinopathy"]["evidence"])
    assert "diagnosis" not in json.dumps(d).lower() or "not a diagnosis" in json.dumps(d).lower()


def test_trial_response_trend_rides_the_loop(db):
    run(db, "pain-log", "anterior-knee", "--intensity", "6", "--side", "left")
    run(db, "exercise-trial-log", "patellar-isometrics", "--response", "better", "--target", "anterior-knee")
    d = pain(db)
    trials = next(x for x in d["loop"] if x["region"] == "anterior-knee")["trials"]
    assert trials and trials[0]["drill"] == "patellar-isometrics"
    assert trials[0]["response"] == "better"


def test_model_boundary_flag_surfaced_once_never_a_referral_reflex(db):
    # TWO red-flag regions → the honest boundary line is surfaced exactly ONCE
    # (a single payload-level string), never repeated per region / per flag
    run(db, "pain-log", "low-back", "--intensity", "6", "--side", "central",
        "--flags", "radiating,numbness")
    run(db, "pain-log", "shoulder", "--intensity", "5", "--side", "left",
        "--flags", "night-pain")
    d = pain(db)
    lb = next(x for x in d["loop"] if x["region"] == "low-back")
    assert set(lb["boundary_flags"]) == {"radiating", "numbness"}
    # each flagged region carries its own hatch marker…
    assert d["regions"]["lower-back-erectors-left"]["boundary"] is True
    assert d["regions"]["deltoid-rear-left"]["boundary"] is True
    # …but the honest line is a SINGLE string (surfaced once), not concatenated,
    # and it names the model boundary — NOT a blanket "see a doctor"
    assert isinstance(d["boundary_note"], str)
    assert d["boundary_note"].count("model boundary") == 1
    assert "outside" in d["boundary_note"].lower()
    assert "see a doctor" not in d["boundary_note"].lower()


def test_nrs_zero_is_not_a_painful_area(db):
    # NRS 0 is a valid reading ("no pain today") but must NOT create a phantom
    # painful-area loop entry, color the region, or surface a boundary note —
    # the figure and the loop must agree (review finding)
    run(db, "pain-log", "anterior-knee", "--intensity", "0", "--side", "left",
        "--flags", "radiating")
    d = pain(db)
    assert d["loop"] == []
    assert d["regions"]["knee-left"]["status"] == "none"
    assert d["regions"]["knee-left"]["boundary"] is False
    assert d["boundary_note"] == ""


def test_same_day_retest_uses_the_latest_row_not_an_arbitrary_tie(db):
    # two self-tests for the same (test, side) on the SAME day: the LATEST row
    # (highest id) must win — a day-granular MAX(date) tie resolves arbitrarily
    # and could keep the stale result, flipping the support call (review)
    run(db, "pain-log", "anterior-knee", "--intensity", "6", "--side", "left")
    run(db, "self-test-log", "decline-squat-pain", "--result", "negative", "--side", "left")
    run(db, "self-test-log", "decline-squat-pain", "--result", "positive", "--side", "left")
    d = pain(db)
    causes = {c["cause"]: c for c in
              next(x for x in d["loop"] if x["region"] == "anterior-knee")["causes"]}
    # latest = positive → patellar tendinopathy supported, PFP against
    assert causes["Patellar tendinopathy"]["support"] == "supported"
    assert causes["Patellofemoral pain"]["support"] == "against"


def test_loop_never_emits_a_diagnosis_only_organised_evidence(db):
    # honest structure, not a string check: every candidate carries a bounded
    # support tag + its cite, and there is NO verdict/diagnosis field anywhere
    # in the payload.
    run(db, "pain-log", "anterior-knee", "--intensity", "6", "--side", "left")
    d = pain(db)
    entry = next(x for x in d["loop"] if x["region"] == "anterior-knee")
    for c in entry["causes"]:
        assert c["support"] in ("supported", "against", "untested")
        assert c["cite"]
    blob = json.dumps(d).lower()
    for verdict in ('"diagnosis"', '"verdict"', "you have", "confirmed cause"):
        assert verdict not in blob


def test_no_boundary_note_when_no_red_flags(db):
    run(db, "pain-log", "anterior-knee", "--intensity", "4", "--side", "left")
    d = pain(db)
    assert d["boundary_note"] == ""
    assert d["regions"]["knee-left"]["boundary"] is False


def test_physio_void_kind_is_whitelisted_and_all_kinds_work(db):
    # the --kind → table map is a fixed whitelist (no SQL injection via a
    # crafted --kind), and every kind soft-voids its own table
    run(db, "pain-log", "anterior-knee", "--intensity", "5", "--side", "left")
    run(db, "self-test-log", "decline-squat-pain", "--result", "positive", "--side", "left")
    run(db, "exercise-trial-log", "patellar-isometrics", "--response", "better", "--target", "anterior-knee")
    assert "kind" in run(db, "physio-void", "--kind", "pain_log; DROP TABLE pain_log", "1", ok=False)
    for kind in ("pain", "self-test", "trial"):
        assert run(db, "physio-void", "--kind", kind, "1")["ok"]
    # trial gone from the loop after its void
    run(db, "pain-log", "anterior-knee", "--intensity", "5", "--side", "left")
    d = pain(db)
    assert next(x for x in d["loop"] if x["region"] == "anterior-knee")["trials"] == []


def test_voided_self_test_does_not_sway_support(db):
    run(db, "pain-log", "anterior-knee", "--intensity", "6", "--side", "left")
    run(db, "self-test-log", "decline-squat-pain", "--result", "positive", "--side", "left")
    run(db, "physio-void", "--kind", "self-test", "1", "--reason", "wrong test")
    d = pain(db)
    causes = {c["cause"]: c for c in
              next(x for x in d["loop"] if x["region"] == "anterior-knee")["causes"]}
    assert causes["Patellar tendinopathy"]["support"] == "untested"


def test_other_lens_payloads_unchanged_by_pain(db):
    a = run(db, "muscle-map")
    assert a["lens"] == "activation" and "loop" not in a and "cv_note" not in a
    s = run(db, "muscle-map", "--lens", "strength-balance")
    assert s["lens"] == "strength-balance" and "loop" not in s
