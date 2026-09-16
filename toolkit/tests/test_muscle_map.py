"""§3f muscle figure: the `muscle-map` display payload + the authored
display mapping's integrity against the vendored body-muscles SVG data.

Two layers:
- mapping integrity — every mapped id must exist in the vendored asset and
  vice-versa (a vendored upgrade or a map typo fails loudly, never renders a
  dead region), and every engine vocabulary term must have a display home;
- behavior — authored rows land laterality-aware, coarse fallback is approx,
  mobility/unmatched/unrepresented are excluded AND surfaced, and the level
  bucketing is pinned (display convention, but a silent change would re-color
  the whole figure).
"""
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
VENDORED = ROOT.parent / "app" / "static" / "vendor" / "body-muscles" / "body-muscles.umd.min.js"
MAP_DOC = ROOT / "tests" / "fixtures" / "submuscle_map.synthetic.md"

sys.path.insert(0, str(ROOT))
import health  # noqa: E402


def vendored_ids():
    # the vendored UMD embeds each region as {id:"...",name:...}; the only
    # id:"..." literals in the file are these region objects (pinned below
    # by the 89-count assertion)
    ids = set(re.findall(r'id:"([a-z][a-z-]*)"', VENDORED.read_text()))
    assert len(ids) == 89, "vendored body-muscles region count changed"
    return ids


def sided_region_ids():
    return set(health.FIGURE_REGION_GROUP)


# ── mapping integrity ────────────────────────────────────────────────────────

def test_display_map_covers_vendored_svg_exactly():
    svg = vendored_ids()
    regions = sided_region_ids()
    non_muscle = set(health.FIGURE_NON_MUSCLE)
    assert regions & non_muscle == set()
    assert regions | non_muscle == svg


def test_every_mapping_target_is_a_known_region_base():
    bases = set(health._FIG_BASE_GROUP)
    for sub, (targets, _approx) in health.FIGURE_SUB_SVG.items():
        assert set(targets) <= bases, f"FIGURE_SUB_SVG[{sub!r}] targets unknown base"
    for tag, targets in health.FIGURE_COARSE_SVG.items():
        assert set(targets) <= bases, f"FIGURE_COARSE_SVG[{tag!r}] targets unknown base"


def test_region_groups_are_the_seven_axes():
    assert set(health._FIG_BASE_GROUP.values()) <= set(health.MUSCLE_GROUP_AXES)


def test_every_coarse_tag_has_a_display_entry_and_no_strays():
    assert set(health.FIGURE_COARSE_SVG) == set(health.MUSCLE_TO_GROUP)


def test_every_bundled_fixture_sub_region_is_displayable():
    # the bundled synthetic map must render fully — a new sub-region in the doc
    # without a display mapping would silently vanish from the figure (it IS
    # surfaced at runtime via unrepresented_sub_regions, but the shipped doc
    # should never be in that state)
    sections = health._parse_submuscle_map(MAP_DOC.read_text())
    subs = {r["sub_region"].strip().lower() for s in sections for r in s["rows"]}
    missing = subs - set(health.FIGURE_SUB_SVG)
    assert not missing, f"authored sub-regions with no display mapping: {missing}"


def test_level_bucketing_is_pinned():
    edges = [(0, 0), (0.1, 1), (1.9, 1), (2, 2), (3.9, 2), (4, 3),
             (6.9, 3), (7, 4), (9.9, 4), (10, 5), (25, 5)]
    for v, lvl in edges:
        assert health._fig_level(v) == lvl, (v, lvl)


def test_legend_labels_derive_from_the_same_edges():
    # labels and bucketing share FIGURE_LEVEL_EDGES — retuning one cannot
    # silently mislabel the other (review), and each label names the true
    # half-open boundary (4.0 is level 3, so level 2 must read "2–3.9")
    assert [l["label"] for l in health.FIGURE_LEVEL_LEGEND] == \
        ["0 sets", "<2", "2–3.9", "4–6.9", "7–9.9", "10+"]
    assert [l["level"] for l in health.FIGURE_LEVEL_LEGEND] == [0, 1, 2, 3, 4, 5]


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


def seed(db, hevy=(), muscles=(), submuscles=()):
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO hevy_sets(date,exercise_title,set_index,set_type,weight_kg,reps,source)"
        " VALUES(?,?,?,?,?,?, 'hevy')", hevy)
    con.executemany(
        "INSERT INTO exercise_muscles(exercise_title,muscle,weight) VALUES(?,?,?)", muscles)
    if submuscles:
        # the REAL schema (no hand-copied DDL that can go stale — review)
        health._ensure_submuscle_table(con)
        con.executemany(
            "INSERT INTO exercise_submuscles(exercise_title,muscle_group,sub_region,weight,laterality,source)"
            " VALUES(?,?,?,?,?,?)", submuscles)
    con.commit()
    con.close()


def _sets(title, n, day):
    return [(day, title, i, "normal", 40.0, 8) for i in range(1, n + 1)]


def today():
    from datetime import date
    return date.today().isoformat()


def test_activation_default_window_stays_7d_after_the_per_lens_days_change(db):
    # §3g made muscle-map --days default None so each lens picks its own window
    # (pain=120d). The activation lens must still fall back to 7d when --days is
    # omitted — the panel sends no --days for activation (review finding).
    d = run(db, "muscle-map")
    assert d["window_days"] == 7
    assert run(db, "muscle-map", "--days", "30")["window_days"] == 30


def test_empty_db_paints_everything_level_zero(db):
    d = run(db, "muscle-map")
    assert d["lens"] == "activation"
    assert len(d["regions"]) == 68
    assert len(d["non_muscle"]) == 21
    assert all(r["level"] == 0 and r["basis"] == "none" for r in d["regions"].values())
    assert d["coarse_fallback"] == [] and d["unmapped_exercises"] == []


def test_authored_rows_land_laterality_aware(db):
    seed(db, hevy=_sets("X", 5, today()),
         muscles=[("X", "glutes", 1.0)],   # authored must win over the tag
         submuscles=[("X", "Glutes", "glute max", 1.0, "bilateral", "lit:x"),
                     ("X", "Glutes", "glute med", 0.4, "left", "lit:x")])
    d = run(db, "muscle-map")
    r = d["regions"]
    assert r["gluteus-maximus-left"]["eff_sets"] == 5.0
    assert r["gluteus-maximus-right"]["eff_sets"] == 5.0
    assert r["gluteus-maximus-left"]["basis"] == "authored"
    assert r["gluteus-maximus-left"]["level"] == 3          # 4 <= 5 < 7
    assert r["gluteus-medius-left"]["eff_sets"] == 2.0      # 5 × 0.4, left only
    assert r["gluteus-medius-right"]["eff_sets"] == 0.0
    assert d["coarse_fallback"] == []                       # authored won


def test_deep_muscle_maps_approx_onto_overlying_region(db):
    seed(db, hevy=_sets("X", 3, today()),
         submuscles=[("X", "Glutes", "glute min", 0.4, "bilateral", "lit:x")])
    d = run(db, "muscle-map")
    reg = d["regions"]["gluteus-medius-left"]
    assert reg["eff_sets"] == 1.2 and reg["approx"] is True


def test_coarse_fallback_is_approx_and_surfaced(db):
    seed(db, hevy=_sets("Y", 3, today()), muscles=[("Y", "quads", 1.0)])
    d = run(db, "muscle-map")
    for side in ("left", "right"):
        reg = d["regions"][f"quads-{side}"]
        assert reg["eff_sets"] == 3.0 and reg["basis"] == "coarse" and reg["approx"] is True
    assert d["coarse_fallback"] == ["Y"]


def test_mobility_and_unmatched_are_excluded_and_surfaced(db):
    seed(db, hevy=_sets("sample mobility drill a", 2, today()) + _sets("Zzz", 2, today()))
    d = run(db, "muscle-map")
    assert d["mobility_excluded"] == ["sample mobility drill a"]
    assert d["unmapped_exercises"] == ["Zzz"]
    assert all(r["eff_sets"] == 0 for r in d["regions"].values())


def test_quarterly_balance_hold_is_not_counted_as_strength_volume(db):
    title = "Quarterly Test — Single-Leg Balance (Eyes Closed)"
    seed(db, hevy=_sets(title, 2, today()),
         muscles=[(title, "calves", 0.5), (title, "glutes", 0.5)])
    d = run(db, "muscle-map")
    assert d["non_volume_excluded"] == [title]
    assert d["coarse_fallback"] == []
    assert all(r["eff_sets"] == 0 for r in d["regions"].values())


def test_authored_tibialis_sets_land_on_tibialis_not_coarse_calves(db):
    title = "Quarterly Test — Tibialis Raise"
    seed(db, hevy=_sets(title, 2, today()),
         muscles=[(title, "calves", 1.0)],
         submuscles=[(title, "Legs", "tibialis anterior", 1.0,
                      "bilateral", "biomech")])
    d = run(db, "muscle-map")
    for side in ("left", "right"):
        assert d["regions"][f"tibialis-anterior-{side}"]["eff_sets"] == 2.0
        assert d["regions"][f"calves-gastroc-medial-{side}"]["eff_sets"] == 0.0
    assert d["coarse_fallback"] == []


def test_quarterly_unilateral_working_sets_land_one_per_side(db):
    title = "Quarterly Test — Tibialis Raise"
    con = sqlite3.connect(db)
    con.executemany(
        """INSERT INTO hevy_sets(
             date,workout_title,start_time,exercise_title,set_index,set_type,source)
           VALUES(?,?,?,?,?,?,'hevy')""",
        [(today(), "Quarterly Test 1 — Lower Body", "2026-07-26T10:00:00Z",
          title, 1, "warmup"),
         (today(), "Quarterly Test 1 — Lower Body", "2026-07-26T10:00:00Z",
          title, 2, "warmup"),
         (today(), "Quarterly Test 1 — Lower Body", "2026-07-26T10:00:00Z",
          title, 3, "normal"),
         (today(), "Quarterly Test 1 — Lower Body", "2026-07-26T10:00:00Z",
          title, 4, "normal")])
    con.execute(
        """INSERT INTO exercise_submuscles(
             exercise_title,muscle_group,sub_region,weight,laterality,source)
           VALUES(?,?,?,?,?,?)""",
        (title, "Legs", "tibialis anterior", 1.0, "bilateral", "biomech"))
    con.commit()
    con.close()
    d = run(db, "muscle-map")
    assert d["regions"]["tibialis-anterior-left"]["eff_sets"] == 1.0
    assert d["regions"]["tibialis-anterior-right"]["eff_sets"] == 1.0


def test_unrepresented_sub_region_is_surfaced_never_guessed(db):
    seed(db, hevy=_sets("X", 3, today()),
         submuscles=[("X", "Glutes", "mystery region", 1.0, "bilateral", "lit:x")])
    d = run(db, "muscle-map")
    assert d["unrepresented_sub_regions"] == ["mystery region"]
    assert all(r["eff_sets"] == 0 for r in d["regions"].values())


def test_bilateral_plus_sided_rows_take_max_not_sum(db):
    # PK (exercise, sub_region, laterality) allows a bilateral row AND a
    # curated sided refinement; the radar dedupes with MAX — the figure
    # must agree, not double-count the left region (review finding)
    seed(db, hevy=_sets("X", 5, today()),
         submuscles=[("X", "Glutes", "glute med", 0.4, "bilateral", "lit:x"),
                     ("X", "Glutes", "glute med", 0.66, "left", "lit:x")])
    d = run(db, "muscle-map")
    assert d["regions"]["gluteus-medius-left"]["eff_sets"] == 3.3   # 5 × MAX(.4,.66)
    assert d["regions"]["gluteus-medius-right"]["eff_sets"] == 2.0  # 5 × .4


def test_level_buckets_true_value_not_rounded_display(db):
    # 3 sets × 0.66 = 1.98 eff sets → displays 2.0 but must stay level 1
    # (bucket the true value, then round — review finding)
    seed(db, hevy=_sets("X", 3, today()),
         submuscles=[("X", "Glutes", "glute max", 0.66, "bilateral", "lit:x")])
    d = run(db, "muscle-map")
    reg = d["regions"]["gluteus-maximus-left"]
    assert reg["eff_sets"] == 2.0 and reg["level"] == 1


def test_figure_agrees_with_radar_basis_ladder(db):
    # same exercise authored AND tagged: the radar uses authored; the figure
    # must too (shared _basis_weights) — no coarse leakage into regions
    seed(db, hevy=_sets("X", 4, today()),
         muscles=[("X", "chest", 1.0)],
         submuscles=[("X", "Arms", "triceps", 1.0, "bilateral", "lit:x")])
    d = run(db, "muscle-map")
    assert d["regions"]["chest-upper-left"]["eff_sets"] == 0.0
    assert d["regions"]["triceps-long-left"]["eff_sets"] == 4.0
