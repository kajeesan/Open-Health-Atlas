"""Phase 4 mobility: mobility-test capture (reuses the §3e fitness_tests path
with two new value kinds — rom/degrees + binary/passed) + the cited MOBILITY_NORM
catalog + the `muscle-map --lens mobility` figure/detail payload.

design rules (2026-07-20, all honored here):
1. storage  — reuse fitness_tests (kind carries the unit), no new table
2. unit     — per-test in the catalog (deg / cm / pass-fail)
3. palette  — cool blue/teal ramp (CSS-side; engine just emits statuses)
4. bands    — restricted / normal / untested (hypermobility deferred)
5. v1 scope — thomas + ankle-df-wall + shoulder flexion/ER + sit-and-reach
6. overlap  — sit-and-reach is REUSED (also the radar flexibility axis), not forked
7. capture  — collector/agent-only (fitness-test-log), panel read-only

Norms are CITED (see .superpowers/sdd/muscle-map-phase4-citations.md); tests here
reference MOBILITY_NORM cutoffs dynamically so the LOGIC is verified independent of
the exact numbers, and a separate integrity test pins that every entry has a cite.
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
    return r.stderr or r.stdout


def today(back=0):
    return (date.today() - timedelta(days=back)).isoformat()


def mob(db, *args):
    return run(db, "muscle-map", "--lens", "mobility", *args)


def ft(db, movement, *args, ok=True):
    return run(db, "fitness-test-log", movement, *args, ok=ok)


# ── new value kinds: rom (degrees) + binary (passed) ──────────────────────────

def test_new_kinds_are_registered():
    assert health.KIND_FIELDS["rom"] == ("degrees",)
    assert health.KIND_FIELDS["binary"] == ("passed",)
    assert health.KIND_BETTER["rom"] == "higher"
    assert health.KIND_BETTER["binary"] == "higher"
    assert health.FT_CLAMPS["degrees"][0] == 0
    assert health.FT_CLAMPS["passed"] == (0, 1)


def test_fitness_test_log_accepts_rom_degrees(db):
    d = ft(db, "shoulder-flexion-rom", "--side", "left", "--degrees", "165")
    assert d["ok"] and d["logged"]["movement"] == "shoulder-flexion-rom"
    assert d["logged"]["kind"] == "rom"
    # the read-back value is the degrees, not a strength e1rm or a rating
    row = run(db, "fitness-tests")["tests"]
    hit = next(t for t in row if t["movement"] == "shoulder-flexion-rom")
    assert hit["value"] == 165


def test_fitness_test_log_accepts_binary_passed(db):
    d = ft(db, "thomas", "--side", "right", "--passed", "1")
    assert d["ok"] and d["logged"]["kind"] == "binary"
    hit = next(t for t in run(db, "fitness-tests")["tests"] if t["movement"] == "thomas")
    assert hit["value"] == 1


def test_rom_missing_degrees_is_refused(db):
    assert "degrees" in ft(db, "shoulder-flexion-rom", "--side", "left", ok=False)


def test_degrees_and_passed_are_clamped(db):
    assert "range" in ft(db, "shoulder-flexion-rom", "--side", "left", "--degrees", "999", ok=False)
    assert "range" in ft(db, "thomas", "--side", "left", "--passed", "2", ok=False)


def test_rom_and_binary_are_unilateral(db):
    # both are per-side tests → a side is required (owner: per-joint detail)
    assert "unilateral" in ft(db, "shoulder-flexion-rom", "--degrees", "160", ok=False)
    assert "unilateral" in ft(db, "thomas", "--passed", "1", ok=False)


def test_writer_refuses_old_fitness_shape_without_ddl(tmp_path):
    # Migration 001 is the only owner of degrees/passed.
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.execute("DROP TABLE fitness_tests")
    con.execute("""CREATE TABLE fitness_tests(
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
        movement TEXT NOT NULL, side TEXT NOT NULL DEFAULT 'bilateral',
        load_kg REAL, reps INTEGER, seconds REAL, rating INTEGER, cm REAL,
        equipment_note TEXT, source TEXT NOT NULL DEFAULT 'chat',
        voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
        created_at TEXT DEFAULT (datetime('now')))""")
    con.commit(); con.close()
    message = ft(p, "shoulder-er-rom", "--side", "left", "--degrees", "80", ok=False)
    assert "schema_migration_required" in message
    cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(fitness_tests)")}
    assert {"degrees", "passed"}.isdisjoint(cols)


# ── MOBILITY_NORM catalog integrity ───────────────────────────────────────────

def test_v1_scope_is_the_owner_five(db):
    assert set(health.MOBILITY_NORM) == {
        "thomas", "ankle-df-wall", "shoulder-flexion-rom",
        "shoulder-er-rom", "sit-and-reach"}


def test_every_norm_movement_exists_in_catalog():
    for mv in health.MOBILITY_NORM:
        assert mv in health.CATALOG, mv


def test_every_norm_carries_a_citation():
    for mv, norm in health.MOBILITY_NORM.items():
        assert norm.get("cite"), mv


def test_norm_svg_bases_are_real_muscle_regions():
    # the mobility lens colors MUSCLE regions only (no joint exception — unlike
    # the pain lens); every svg base must be a drawn muscle base
    muscle = set(health._FIG_BASE_GROUP)
    for mv, norm in health.MOBILITY_NORM.items():
        assert norm["svg"], mv
        assert set(norm["svg"]) <= muscle, (mv, norm["svg"])


def test_sit_and_reach_is_reused_not_forked(db):
    # design rule 6: the mobility lens reuses the EXISTING sit-and-reach test that
    # already feeds the radar flexibility axis — same slug, one catalog entry
    assert "sit-and-reach" in health.MOBILITY_NORM
    assert health.ATHLETIC_AXES["flexibility"]["test"] == "sit-and-reach"
    assert health.CATALOG["sit-and-reach"]["kind"] == "distance"


# ── status bands (design rule 4: restricted / normal / untested) ───────────────

def test_status_bands_are_pinned():
    ankle = health.MOBILITY_NORM["ankle-df-wall"]     # higher = better (degrees)
    cut = ankle["normal_at"]
    assert health._mobility_status(cut + 5, ankle) == "normal"
    assert health._mobility_status(cut - 5, ankle) == "restricted"
    assert health._mobility_status(cut, ankle) == "normal"          # at cutoff = normal
    assert health._mobility_status(None, ankle) == "untested"       # no value logged
    # a norm with no defensible cutoff can never be called restricted/normal
    assert health._mobility_status(999, {"normal_at": None, "better": "higher"}) == "untested"


# ── the mobility lens: figure + detail ────────────────────────────────────────

def test_empty_db_is_all_untested(db):
    d = mob(db)
    assert d["lens"] == "mobility"
    assert all(r["status"] == "untested" for r in d["regions"].values())
    assert [x["status"] for x in d["legend"]] == ["restricted", "normal", "untested"]
    # mobility uses muscle regions only → non_muscle is the full set (no joints removed)
    assert set(d["non_muscle"]) == set(health.FIGURE_NON_MUSCLE)


def test_restricted_and_normal_color_the_mapped_regions(db):
    norm = health.MOBILITY_NORM["ankle-df-wall"]
    cut = norm["normal_at"]
    ft(db, "ankle-df-wall", "--side", "left", "--degrees", str(cut - 6))
    ft(db, "ankle-df-wall", "--side", "right", "--degrees", str(cut + 6))
    d = mob(db)
    base = norm["svg"][0]
    assert d["regions"][f"{base}-left"]["status"] == "restricted"
    assert d["regions"][f"{base}-right"]["status"] == "normal"


def test_per_side_coloring_left_test_does_not_touch_right(db):
    norm = health.MOBILITY_NORM["shoulder-er-rom"]
    ft(db, "shoulder-er-rom", "--side", "left", "--degrees", str(norm["normal_at"] - 20))
    d = mob(db)
    base = norm["svg"][0]
    assert d["regions"][f"{base}-left"]["status"] == "restricted"
    assert d["regions"][f"{base}-right"]["status"] == "untested"


def test_binary_thomas_pass_is_normal_fail_is_restricted(db):
    ft(db, "thomas", "--side", "left", "--passed", "0")
    ft(db, "thomas", "--side", "right", "--passed", "1")
    d = mob(db)
    assert d["regions"]["hip-flexor-left"]["status"] == "restricted"
    assert d["regions"]["hip-flexor-right"]["status"] == "normal"


def test_bilateral_sit_and_reach_colors_both_sides(db):
    norm = health.MOBILITY_NORM["sit-and-reach"]
    ft(db, "sit-and-reach", "--cm", str(norm["normal_at"] - 4))
    d = mob(db)
    for base in norm["svg"]:
        assert d["regions"][f"{base}-left"]["status"] == "restricted", base
        assert d["regions"][f"{base}-right"]["status"] == "restricted", base


def test_shipped_norms_do_not_overlap_so_worst_wins_is_inert(db):
    # the shipped v1 norm map has no two entries sharing an svg base, so the
    # worst-wins branch never fires in production — this pins that invariant, so
    # adding a future overlapping norm (e.g. a hip test sharing hip-flexor) can't
    # silently start relying on untested behaviour. The branch IS exercised by
    # test_worst_wins_and_pending_norm_via_full_payload below.
    seen = {}
    for mv, norm in health.MOBILITY_NORM.items():
        for base in norm["svg"]:
            assert base not in seen, f"{mv} and {seen[base]} share {base} — worst-wins now matters"
            seen[base] = mv


def test_worst_wins_and_pending_norm_via_full_payload(db, monkeypatch):
    # genuinely exercise apply()'s worst-wins AND the pending-norm (normal_at=None
    # → untested) path through the FULL payload. A patched norm map can't cross a
    # subprocess boundary, so drive the lens in-process against a DB the real
    # writer populated. Movements stay REAL (only the norm map is patched).
    monkeypatch.setenv("HEALTH_DB", str(db))   # for the subprocess ft() writers
    monkeypatch.setattr(health, "DB", str(db))  # for the in-process lens read (DB is bound at import)
    monkeypatch.setattr(health, "MOBILITY_NORM", {
        # two REAL tests pointed at the SAME base → a contested region
        "ankle-df-wall": {"label": "AR", "svg": ("quads",), "unit": "deg",
                          "better": "higher", "normal_at": 30, "cite": "x"},
        "shoulder-flexion-rom": {"label": "BN", "svg": ("quads",), "unit": "deg",
                                 "better": "higher", "normal_at": 30, "cite": "y"},
        # a pending norm: value logged, no cited cutoff → honest untested
        "thomas": {"label": "CP", "svg": ("biceps",), "unit": "pass-fail",
                   "better": "higher", "normal_at": None, "cite": "z"},
    })
    ft(db, "ankle-df-wall", "--side", "left", "--degrees", "20")          # restricted
    ft(db, "shoulder-flexion-rom", "--side", "left", "--degrees", "60")   # normal
    ft(db, "thomas", "--side", "left", "--passed", "1")                   # pending → untested
    box = {}
    monkeypatch.setattr(health, "out", lambda d: box.update(d))
    import types
    health._muscle_map_mobility(types.SimpleNamespace(lens="mobility", days=None, side_mode="combined"))
    # restricted (rank 2) beats normal (rank 1) on the shared quads-left region
    assert box["regions"]["quads-left"]["status"] == "restricted"
    thomas = next(t for t in box["tests"] if t["movement"] == "thomas")
    left = next(x for x in thomas["sides"] if x["side"] == "left")
    assert left["value"] == 1 and left["status"] == "untested"   # value kept, band withheld


def test_default_window_is_mobility_stale_not_activation_7d(db):
    norm = health.MOBILITY_NORM["ankle-df-wall"]
    ft(db, "ankle-df-wall", "--side", "left", "--degrees",
       str(norm["normal_at"] - 6), "--date", today(health.MOBILITY_STALE_DAYS + 20))
    d = mob(db)                                   # no --days → mobility default
    assert d["window_days"] == health.MOBILITY_STALE_DAYS
    # the old reading is outside the window → the region is honest untested
    assert d["regions"][f"{norm['svg'][0]}-left"]["status"] == "untested"


def test_detail_lists_every_v1_test_tested_or_not(db):
    ft(db, "thomas", "--side", "left", "--passed", "0")
    d = mob(db)
    movements = {t["movement"] for t in d["tests"]}
    assert movements == set(health.MOBILITY_NORM)          # all five surfaced
    thomas = next(t for t in d["tests"] if t["movement"] == "thomas")
    assert thomas["cite"] and thomas["unit"]
    # a tested entry carries its value + status + date; an untested one says so
    left = next(x for x in thomas["sides"] if x["side"] == "left")
    assert left["value"] == 0 and left["status"] == "restricted" and left["date"]
    untested = next(t for t in d["tests"] if t["movement"] == "sit-and-reach")
    assert untested["sides"] == [] or all(s["status"] == "untested" for s in untested["sides"])


def test_flexibility_overlap_note_is_surfaced(db):
    # design rule 6: the payload must name the sit-and-reach ↔ radar overlap so it
    # reads as one test at two altitudes, never a double count
    d = mob(db)
    note = d.get("flexibility_note", "").lower()
    assert "sit-and-reach" in note and ("flexibility" in note or "radar" in note)


def test_mobility_is_display_only_no_diagnosis_no_verdict(db):
    ft(db, "thomas", "--side", "left", "--passed", "0")
    d = mob(db)
    blob = json.dumps(d).lower()
    for bad in ('"diagnosis"', '"verdict"', "you have"):
        assert bad not in blob
    # the screen-not-a-diagnosis doctrine lives in the ENGINE payload (not only
    # the panel template), matching the pain lens
    assert "screen" in d["note"].lower() and "not a diagnosis" in d["note"].lower()


def test_every_test_surfaces_its_cited_caveat(db):
    # each cited norm ships an honest caveat (Thomas pelvic-tilt, ankle cutoff
    # population, sit-and-reach protocol) — verify they actually reach the payload
    d = mob(db)
    by_mv = {t["movement"]: t for t in d["tests"]}
    for mv, norm in health.MOBILITY_NORM.items():
        assert by_mv[mv]["caveat"] == norm.get("caveat", "")
    assert "pelvic tilt" in by_mv["thomas"]["caveat"].lower()


def test_voiding_a_mobility_test_hides_it(db):
    norm = health.MOBILITY_NORM["ankle-df-wall"]
    ft(db, "ankle-df-wall", "--side", "left", "--degrees", str(norm["normal_at"] - 6))
    base = norm["svg"][0]
    assert mob(db)["regions"][f"{base}-left"]["status"] == "restricted"
    run(db, "fitness-test-void", "1", "--reason", "mis-measured")
    assert mob(db)["regions"][f"{base}-left"]["status"] == "untested"


def test_same_day_retest_uses_latest_row_not_an_arbitrary_tie(db):
    # two ankle-DF measures the SAME day for the same side: the LATER row (higher
    # id) must win. A bare MAX(date) GROUP BY would resolve the tie arbitrarily
    # and could keep the stale reading, flipping the region's status.
    norm = health.MOBILITY_NORM["ankle-df-wall"]
    cut = norm["normal_at"]
    ft(db, "ankle-df-wall", "--side", "left", "--degrees", str(cut - 8))   # restricted (earlier)
    ft(db, "ankle-df-wall", "--side", "left", "--degrees", str(cut + 8))   # normal (later, wins)
    assert mob(db)["regions"][f"{norm['svg'][0]}-left"]["status"] == "normal"


def test_other_lenses_unchanged_by_mobility(db):
    a = run(db, "muscle-map")
    assert a["lens"] == "activation" and "tests" not in a
    s = run(db, "muscle-map", "--lens", "strength-balance")
    assert s["lens"] == "strength-balance"
    p = run(db, "muscle-map", "--lens", "pain")
    assert p["lens"] == "pain" and "loop" in p


def test_flexibility_radar_axis_still_reads_sit_and_reach(db):
    # non-regression for the reuse (call 6): adding sit-and-reach to MOBILITY_NORM
    # must not change how the radar's flexibility axis consumes it
    run(db, "athletic-target-set", "flexibility", "--target", "30")
    ft(db, "sit-and-reach", "--cm", "27")
    r = run(db, "athletic-radar")
    assert r["scores"]["flexibility"]["test"] == "sit-and-reach"
    assert r["scores"]["flexibility"]["result"] == 27
