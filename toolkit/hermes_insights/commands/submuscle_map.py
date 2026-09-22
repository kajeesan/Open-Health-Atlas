"""Authored submuscle catalog preview and configuration replacement."""

from .. import calculations, migrations, runtime
from ..catalogs import MOBILITY_EXERCISES, MUSCLE_TO_GROUP, NON_VOLUME_EXERCISES
from ..command_context import CommandContext
from ..importers.submuscle_map import parse_map, resolve_title, SUBMAP_NOTE


def import_map(context: CommandContext, path, *, seed, seed_all, figure_sub_svg):
    """Preview or seed the cited map, preserving the explicit configuration-delete scope."""
    with open(path, encoding="utf-8") as f:
        sections = parse_map(f.read())
    c = runtime.connect(context.database)
    try:
        with c:
            migrations.require_table(c, "exercise_submuscles", ("approx", "iso", "confidence"))
            candidates = [r["exercise_title"] for r in c.execute(
                "SELECT DISTINCT exercise_title FROM exercise_muscles ORDER BY 1")]
            existing = {r["t"]: r["n"] for r in c.execute(
                "SELECT exercise_title t, COUNT(*) n FROM exercise_submuscles"
                " GROUP BY exercise_title")}
            _, coarse = calculations._group_weight_maps(c, muscle_to_group=MUSCLE_TO_GROUP)
            proposed, not_in_db, mapped = [], [], set()
            seeded_without_candidate = []
            for s in sections:
                matched, title = resolve_title(s["title"], candidates)
                if matched is None:
                    not_in_db.append(s["title"])
                    if not seed_all:
                        continue
                    seeded_without_candidate.append(title)
                if title in MOBILITY_EXERCISES:
                    raise SystemExit(f"{title!r} is a mobility drill — it must never seed "
                             "strength-volume rows (authored map § Mobility)")
                if title in mapped:         # silent last-wins would hide authored rows
                    raise SystemExit(f"two map sections resolve to the same exercise {title!r}")
                mapped.add(title)
                # authored-wins honesty: which coarse Hevy groups lose their credit
                # for this exercise once the authored rows take over (empty for a
                # complete authored entry; review signal, not render-time noise)
                authored_groups = {r["muscle_group"] for r in s["rows"]}
                dropped = sorted(set(coarse.get(title, ({}, set()))[0]) - authored_groups)
                proposed.append({"exercise": title, "source": s["source"],
                                 "uni": s["uni"], "rows": s["rows"],
                                 "counts": {"rows": len(s["rows"]),
                                            "iso": sum(r["iso"] for r in s["rows"]),
                                            "E": sum(r["confidence"] == "E" for r in s["rows"]),
                                            "B": sum(r["confidence"] == "B" for r in s["rows"])},
                                 "replaces_rows": existing.get(title, 0),
                                 "coarse_groups_dropped": dropped})
            retire_rows = [dict(r) for r in c.execute(
                "SELECT exercise_title, muscle_group, sub_region, weight, source "
                "FROM exercise_submuscles WHERE source LIKE 'exercisedb:%' "
                "ORDER BY exercise_title, sub_region")]
            mobility = sorted(t for t in candidates if t in MOBILITY_EXERCISES)
            base = {"dry_run": not seed, "seed_all": seed_all,
                    "map_file": path,
                    "proposed": proposed,
                    "totals": {"exercises": len(proposed),
                               "rows": sum(p["counts"]["rows"] for p in proposed),
                               "iso": sum(p["counts"]["iso"] for p in proposed),
                               "E": sum(p["counts"]["E"] for p in proposed),
                               "B": sum(p["counts"]["B"] for p in proposed)},
                    "will_retire": {"count": len(retire_rows), "rows": retire_rows},
                    "mobility_excluded": mobility,
                    "non_volume_excluded": sorted(t for t in candidates
                                                  if t in NON_VOLUME_EXERCISES),
                    "unmapped_candidates": sorted(set(candidates) - mapped
                                                  - MOBILITY_EXERCISES
                                                  - NON_VOLUME_EXERCISES),
                    "map_titles_not_in_db": not_in_db, "note": SUBMAP_NOTE,
                    "seeded_without_candidate": sorted(seeded_without_candidate),
                    # §3f visibility (review): sub-regions the body figure has no
                    # drawn region for — they still COUNT in the radar/drill-down,
                    # but won't light up on the figure. Informational, not an error
                    # (a legitimately authored muscle may predate a display mapping;
                    # the figure also surfaces these at render time).
                    "unrepresented_on_figure": sorted(
                        {r["sub_region"] for s in sections for r in s["rows"]
                         if r["sub_region"].strip().lower() not in figure_sub_svg})}
            if not seed:
                return base
            seeded_rows = 0
            for p in proposed:
                # full replace: the authored map owns its exercises' rows
                c.execute("DELETE FROM exercise_submuscles WHERE exercise_title=?",
                          (p["exercise"],))
                for r in p["rows"]:
                    c.execute("INSERT INTO exercise_submuscles(exercise_title,"
                              " muscle_group, sub_region, weight, laterality, source,"
                              " approx, iso, confidence) VALUES(?,?,?,?,?,?,?,?,?)",
                              (p["exercise"], r["muscle_group"], r["sub_region"],
                               r["weight"], r["laterality"], p["source"],
                               int(r["approx"]), int(r["iso"]), r["confidence"]))
                    seeded_rows += 1
            # retire the v2.6 ExerciseDB seed — end state: zero exercisedb rows
            c.execute("DELETE FROM exercise_submuscles WHERE source LIKE 'exercisedb:%'")
            c.commit()
            return ({**base, "seeded_exercises": len(proposed), "seeded_rows": seeded_rows,
                 "retired_exercisedb_rows": len(retire_rows)})
    finally:
        c.close()
