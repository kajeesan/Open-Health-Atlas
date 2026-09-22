"""Hevy import validation, transactions and quarterly observation coordination."""

from zoneinfo import ZoneInfo

from .. import migrations, runtime, orchestrator
from ..catalogs import CATALOG
from ..routine_history import require_history, snapshot
from ..importers import hevy_json as payload
from ..command_context import CommandContext
from ..importers.hevy_csv import replace_history


def import_csv(context: CommandContext, path, *, parse_number):
    """Replace imported Hevy history atomically, preserving manually logged sets.

    Schema, file and row errors propagate to the caller after rollback.
    """
    connection = runtime.connect(context.database)
    try:
        with connection:
            migrations.require_table(connection, "hevy_sets", ("source",))
            result = replace_history(connection, path, parse_number=parse_number)
        return result
    finally:
        connection.close()


def _import_quarterly_fitness_tests(c, workouts, *, quarterly_routines, parse_number, civil_timezone):
    """Append idempotent quarterly observations and audit corrections in this transaction."""
    migrations.require_table(c, "fitness_tests", ("degrees", "passed"))
    migrations.require_table(c, "athletic_targets")
    result = {"quarterly_tests_inserted": 0, "quarterly_tests_unchanged": 0,
              "quarterly_tests_superseded": 0, "quarterly_tests_rejected": 0,
              "quarterly_warnings": []}

    def warn(code, workout, exercise, movement, side=None, detail=None):
        result["quarterly_warnings"].append({
            "code": code, "workout_id": workout.get("id"),
            "exercise": payload.truncate(exercise.get("title")),
            "movement": movement, "side": side, "detail": detail,
        })

    value_fields = ("date", "movement", "side", "load_kg", "reps", "seconds",
                    "rating", "cm", "degrees", "passed")
    for workout in workouts:
        config = payload.quarterly_routine(workout, routines=quarterly_routines)
        if config is None:
            continue
        workout_id = workout.get("id")
        workout_date = payload.parse_api_date(workout.get("start_time"), civil_timezone=civil_timezone)
        if not workout_id or not workout_date:
            result["quarterly_tests_rejected"] += 1
            warn("invalid_workout_identity", workout, {}, None,
                 detail="quarterly workout needs id and valid start_time")
            continue
        body_weight = c.execute(
            """SELECT date,weight_kg FROM body_metrics
               WHERE date<=? AND weight_kg IS NOT NULL
               ORDER BY date DESC LIMIT 1""", (workout_date,)).fetchone()
        for exercise in (workout.get("exercises") or []):
            template_id = exercise.get("exercise_template_id")
            movement = config["templates"].get(template_id)
            if movement is None:
                continue
            spec = CATALOG[movement]
            working = [s for s in (exercise.get("sets") or [])
                       if (s.get("type") or "normal") != "warmup"]
            expected = 2 if spec["unilateral"] else 1
            if len(working) < expected:
                for ordinal in range(len(working), expected):
                    side = ("left", "right")[ordinal] if spec["unilateral"] else "bilateral"
                    result["quarterly_tests_rejected"] += 1
                    warn("missing_working_set", workout, exercise, movement, side)
            if len(working) > expected:
                result["quarterly_tests_rejected"] += len(working) - expected
                warn("extra_working_sets", workout, exercise, movement,
                     detail=f"expected {expected}, got {len(working)}; extras ignored")
            for ordinal, raw_set in enumerate(working[:expected]):
                side = ("left", "right")[ordinal] if spec["unilateral"] else "bilateral"
                # The exact quarterly calf template is a bodyweight movement.
                # Hevy correctly leaves external weight empty; its system load
                # is the latest measured body mass on/before the test date.
                # This mirrors the protocol's bodyweight-system-load rule and
                # is recorded in the note rather than silently inferred.
                fallback_load = (
                    body_weight["weight_kg"]
                    if movement == "calf-raise" and body_weight is not None
                    else None)
                vals, error, used_body_weight = payload.quarterly_value(
                    spec, raw_set, fallback_load, parse_number=parse_number)
                if error:
                    result["quarterly_tests_rejected"] += 1
                    warn("incompatible_measurement", workout, exercise, movement,
                         side, error)
                    continue
                if spec["kind"] == "strength" and not 6 <= vals["reps"] <= 8:
                    warn("outside_protocol_rep_range", workout, exercise, movement,
                         side, f"{vals['reps']} reps; protocol is 6–8")
                base_source = (
                    f"hevy-quarterly:{workout_id}:{template_id}:{ordinal + 1}")
                observation = {
                    "date": workout_date, "movement": movement, "side": side,
                    **vals,
                }
                active = c.execute(
                    """SELECT * FROM fitness_tests
                       WHERE voided=0 AND (source=? OR source LIKE ?)
                       ORDER BY id DESC LIMIT 1""",
                    (base_source, base_source + ":r%")).fetchone()
                if active is not None and all(active[field] == observation[field]
                                              for field in value_fields):
                    result["quarterly_tests_unchanged"] += 1
                    continue
                if active is not None:
                    c.execute("""UPDATE fitness_tests
                                 SET voided=1, void_reason=?
                                 WHERE id=?""",
                              ("superseded by corrected Hevy sync", active["id"]))
                    result["quarterly_tests_superseded"] += 1
                revision_count = c.execute(
                    """SELECT COUNT(*) FROM fitness_tests
                       WHERE source=? OR source LIKE ?""",
                    (base_source, base_source + ":r%")).fetchone()[0]
                source = (base_source if revision_count == 0
                          else f"{base_source}:r{revision_count + 1}")
                note = f"Hevy {config['title']} · {payload.truncate(exercise.get('title'), 120)}"
                if used_body_weight:
                    note += (f" · bodyweight system load {body_weight['weight_kg']:g} kg"
                             f" (measured {body_weight['date']})")
                if spec["kind"] == "hold" and parse_number(raw_set.get("weight_kg")) is not None:
                    note += f" · {parse_number(raw_set.get('weight_kg')):g} kg"
                c.execute("""INSERT INTO fitness_tests(
                    date,movement,side,load_kg,reps,seconds,rating,cm,degrees,
                    passed,equipment_note,source)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (observation["date"], observation["movement"], observation["side"],
                     observation["load_kg"], observation["reps"], observation["seconds"],
                     observation["rating"], observation["cm"], observation["degrees"],
                     observation["passed"], note, source))
                row_id = str(c.execute("SELECT last_insert_rowid()").fetchone()[0])
                if migrations.recorded_version(c) >= 4:
                    orchestrator.enqueue_internal_trigger(
                        c, trigger_kind="quarterly_observation",
                        source_table="fitness_tests", source_row_key=row_id,
                        event_date=workout_date)
                result["quarterly_tests_inserted"] += 1
    return result


def import_json(context: CommandContext, path, *, force, parse_number, quarterly_routines):
    """Replace Hevy history and derive quarterly observations in one transaction."""
    c = runtime.connect(context.database)
    try:
        with c:
            migrations.require_table(c, "hevy_sets", ("source",))
            workouts = payload.load_json(path, "workouts")
            incoming = sum(len(ex.get("sets") or [])
                           for w in workouts for ex in (w.get("exercises") or []))
            existing = c.execute(
                "SELECT COUNT(*) n FROM hevy_sets WHERE source='hevy'").fetchone()["n"]
            if existing and incoming < existing / 2 and not force:
                raise SystemExit(f"refusing reload: payload has {incoming} sets vs {existing} existing "
                         "hevy rows (API incident / response-shape drift?) — --force to override")
            c.execute("DELETE FROM hevy_sets WHERE source='hevy'")
            n = skipped = null_dates = 0
            for row in payload.workout_sets(workouts, parse_number=parse_number,
                                            civil_timezone=ZoneInfo(context.timezone)):
                if row is None:
                    skipped += 1
                    continue
                if row[0] is None:
                    null_dates += 1
                c.execute("""INSERT INTO hevy_sets(date,workout_title,start_time,end_time,description,
                          exercise_title,superset_id,exercise_notes,set_index,set_type,weight_kg,reps,
                          distance_km,duration_seconds,rpe)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          row)
                n += 1
            quarterly = _import_quarterly_fitness_tests(
                c, workouts, quarterly_routines=quarterly_routines,
                parse_number=parse_number, civil_timezone=ZoneInfo(context.timezone))
            c.commit()
            return ({"ok": True, "imported_sets": n, "workouts": len(workouts),
                 "skipped_sets": skipped, "null_date_sets": null_dates, **quarterly})
    finally:
        c.close()


def import_templates(context: CommandContext, path):
    """Refresh used Hevy muscle mappings while preserving manual curation."""
    c = runtime.connect(context.database)
    try:
        with c:
            migrations.require_table(c, "exercise_muscles", ("source",))
            templates = payload.load_json(path, "exercise_templates")
            used = {r["exercise_title"] for r in c.execute(
                "SELECT DISTINCT exercise_title FROM hevy_sets"
                " UNION SELECT DISTINCT exercise_title FROM routines")}
            mapped = skipped = 0
            for t in templates:
                title = payload.truncate(t.get("title"))
                if not title or title not in used:
                    continue
                # Stale hevy rows are ALWAYS retired first — even when manual curation
                # now owns the exercise, otherwise the two row sets coexist and the
                # radar double-counts the exercise forever.
                c.execute("DELETE FROM exercise_muscles WHERE exercise_title=? AND source='hevy'",
                          (title,))
                if c.execute("SELECT 1 FROM exercise_muscles WHERE exercise_title=? LIMIT 1",
                             (title,)).fetchone():
                    skipped += 1          # manual curation wins
                    continue
                for muscle, weight in payload.template_muscles(t):
                    c.execute("INSERT OR REPLACE INTO exercise_muscles(exercise_title,muscle,weight,source)"
                              " VALUES(?,?,?,'hevy')", (title, muscle, weight))
                mapped += 1
            c.commit()
            return ({"ok": True, "mapped_exercises": mapped, "skipped_manual": skipped})
    finally:
        c.close()


def import_routines(context: CommandContext, path, *, parse_number):
    """Snapshot and replace routine configuration without changing the schedule."""
    c = runtime.connect(context.database)
    try:
        with c:
            require_history(c)
            routines = payload.load_json(path, "routines")
            prior = c.execute("SELECT * FROM routines").fetchall()
            for p in prior:
                snapshot(c, "hevy-sync", p["routine_name"], p["exercise_title"], p)
            c.execute("DELETE FROM routines")
            agg, dupes = payload.routine_rows(routines, parse_number=parse_number)
            for (rname, ename), v in agg.items():
                c.execute("INSERT INTO routines(routine_name,exercise_title,ex_order,"
                          "target_sets,target_reps,target_weight_kg) VALUES(?,?,?,?,?,?)",
                          (rname, ename, v["ex_order"], v["target_sets"],
                           v["target_reps"], v["target_weight_kg"]))
            titles = {k[0] for k in agg}
            orphaned = {r["weekday"]: r["routine_name"] for r in c.execute(
                "SELECT weekday, routine_name FROM training_schedule")
                if r["routine_name"] not in titles and r["routine_name"] != "Rest"}
            c.commit()
            return ({"ok": True, "routines": len(routines), "snapshotted_rows": len(prior),
                 "duplicate_entries": dupes, "orphaned_schedule": orphaned})
    finally:
        c.close()


def import_body(context: CommandContext, path, *, parse_number):
    """Update or append Hevy body rows without deleting observations."""
    c = runtime.connect(context.database)
    try:
        with c:
            migrations.require_table(c, "body_metrics", ("source",))
            items = payload.load_json(path, "body_measurements")
            rows, clamped = payload.body_rows(
                items, parse_number=parse_number, civil_timezone=ZoneInfo(context.timezone))
            updated = inserted = 0
            for d, vals in sorted(rows.items()):
                cols = list(vals)
                existing = c.execute(
                    "SELECT id FROM body_metrics WHERE date=? AND source='hevy' "
                    "ORDER BY id DESC LIMIT 1", (d,)).fetchone()
                if existing:
                    # refresh ALL mapped columns (clearing ones Hevy no longer reports
                    # for the day) so an in-app correction propagates on the next sync
                    sets = ",".join(f"{col}=?" for col in payload.HEVY_BODY_FIELDS.values())
                    c.execute(f"UPDATE body_metrics SET {sets} WHERE id=?",
                              (*[vals.get(col) for col in payload.HEVY_BODY_FIELDS.values()],
                               existing["id"]))
                    updated += 1
                else:
                    ph = ",".join("?" * len(cols))
                    c.execute(f"INSERT INTO body_metrics(date,{','.join(cols)},source) "
                              f"VALUES(?,{ph},'hevy')", (d, *vals.values()))
                    inserted += 1
            c.commit()
            return ({"ok": True, "dates": len(rows), "inserted": inserted, "updated": updated,
                 "skipped_values_out_of_range": clamped})
    finally:
        c.close()
