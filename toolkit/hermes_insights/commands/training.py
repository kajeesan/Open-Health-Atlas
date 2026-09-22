"""Training set capture, scheduled session reads and reversible routine edits."""

from datetime import datetime
import sys

from .. import migrations, runtime
from ..importers.common import valid_date
from ..routine_history import require_history, snapshot


def log_set(context, a, *, parse_number):
    """Log ONE strength set from the UI into hevy_sets, tagged source='ui'.
    set_index auto-increments per exercise per day so multiple sets stack, and
    the source tag is what protects these rows from the import-hevy full reload
    (which only deletes source='hevy'). Validated; all math stays deterministic."""
    ex = (a.exercise or "").strip()
    if not ex:
        sys.exit("exercise is required")
    reps = a.reps
    if reps is not None and not (1 <= reps <= 100):
        sys.exit("reps must be 1-100")
    w = parse_number(a.weight)
    if w is not None and not (0 <= w <= 1000):
        sys.exit("weight_kg must be 0-1000")
    if reps is None and w is None:
        sys.exit("a set needs at least reps or weight")
    rpe = parse_number(a.rpe)
    if rpe is not None and not (1 <= rpe <= 10):
        sys.exit("rpe must be 1-10")
    c = runtime.connect(context.database)
    try:
        migrations.require_table(c, "hevy_sets", ("source",))
        d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
        idx = a.set_index
        if idx is None:
            row = c.execute("SELECT COALESCE(MAX(set_index),0)+1 AS n FROM hevy_sets "
                            "WHERE date=? AND exercise_title=?", (d, ex)).fetchone()
            idx = row["n"]
        c.execute("""INSERT INTO hevy_sets(date, workout_title, exercise_title, set_index,
                     set_type, weight_kg, reps, rpe, source)
                     VALUES(?,?,?,?,?,?,?,?, 'ui')""",
                  (d, a.workout_title, ex, idx, a.set_type or "normal", w, reps, rpe))
        c.commit()
        return {"ok": True, "logged_set": {"date": d, "exercise": ex, "set_index": idx,
             "weight_kg": w, "reps": reps, "rpe": rpe, "source": "ui"}}
    finally:
        c.close()

def today_session(context, a):
    """What's on today: the scheduled routine + its exercises with target sets/reps/starting weight."""
    d = a.date or runtime.today(clock=context.clock)
    wd = datetime.strptime(d, "%Y-%m-%d").strftime("%a")   # Mon, Tue, ...
    c = runtime.connect(context.database)
    try:
        sch = c.execute("SELECT routine_name FROM training_schedule WHERE weekday=?", (wd,)).fetchone()
        routine = sch["routine_name"] if sch else None
        if not routine or routine == "Rest":
            return {"date": d, "weekday": wd, "routine": "Rest", "exercises": []}
        ex = c.execute("""SELECT exercise_title, target_sets, target_reps, target_weight_kg
                          FROM routines WHERE routine_name=? ORDER BY ex_order, exercise_title""", (routine,)).fetchall()
        return {"date": d, "weekday": wd, "routine": routine,
             "exercises": [dict(r) for r in ex]}
    finally:
        c.close()

def last_session(context, a):
    """Numbers to beat: the most recent logged session for an exercise (each set's weight x reps)."""
    c = runtime.connect(context.database)
    try:
        row = c.execute("""SELECT MAX(date) d FROM hevy_sets WHERE exercise_title=? AND date IS NOT NULL""",
                        (a.exercise,)).fetchone()
        if not row or not row["d"]:
            # fall back to the prescribed target so the coach still has a number
            t = c.execute("SELECT target_sets,target_reps,target_weight_kg FROM routines WHERE exercise_title=? LIMIT 1",
                          (a.exercise,)).fetchone()
            return {"exercise": a.exercise, "logged": False,
                 "prescribed": dict(t) if t else None}
        sets = c.execute("""SELECT set_index, weight_kg, reps, rpe FROM hevy_sets
                            WHERE exercise_title=? AND date=? ORDER BY set_index""",
                         (a.exercise, row["d"])).fetchall()
        return {"exercise": a.exercise, "logged": True, "last_date": row["d"],
             "sets": [dict(s) for s in sets]}
    finally:
        c.close()

def routine_set(context, a, *, parse_number):
    """Add or edit an exercise's targets in a routine (upsert). Snapshots the
    prior state to routines_history so the change can be undone."""
    routine = (a.routine or "").strip(); exercise = (a.exercise or "").strip()
    if not routine or not exercise: sys.exit("routine and exercise are required")
    for label, v, lo, hi in (("sets", a.sets, 1, 20), ("reps", a.reps, 1, 100), ("order", a.order, 1, 99)):
        if v is not None and not (lo <= v <= hi): sys.exit(f"{label} out of range")
    w = parse_number(a.weight)
    if w is not None and not (0 <= w <= 1000): sys.exit("weight out of range")
    c = runtime.connect(context.database)
    try:
        require_history(c)
        prior = c.execute("SELECT ex_order,target_sets,target_reps,target_weight_kg FROM routines "
                          "WHERE routine_name=? AND exercise_title=?", (routine, exercise)).fetchone()
        snapshot(c, "set", routine, exercise, prior)
        if prior:
            order = a.order if a.order is not None else prior["ex_order"]
            sets = a.sets if a.sets is not None else prior["target_sets"]
            reps = a.reps if a.reps is not None else prior["target_reps"]
            weight = w if a.weight is not None else prior["target_weight_kg"]
            c.execute("UPDATE routines SET ex_order=?,target_sets=?,target_reps=?,target_weight_kg=? "
                      "WHERE routine_name=? AND exercise_title=?", (order, sets, reps, weight, routine, exercise))
        else:
            nxt = c.execute("SELECT COALESCE(MAX(ex_order),0)+1 n FROM routines WHERE routine_name=?", (routine,)).fetchone()["n"]
            order = a.order if a.order is not None else nxt
            c.execute("INSERT INTO routines(routine_name,exercise_title,ex_order,target_sets,target_reps,target_weight_kg) "
                      "VALUES(?,?,?,?,?,?)", (routine, exercise, order, a.sets, a.reps, w))
        if migrations.recorded_version(c) >= 3:
            migrations.require_version(c, 3)
            migrations.append_training_plan_revision(
                c, effective_from=runtime.today(clock=context.clock), source="routine-set")
        c.commit()
        return {"ok": True, "routine": routine, "exercise": exercise, "was_new": prior is None}
    finally:
        c.close()

def routine_remove(context, a):
    """Remove an exercise from a routine (program config, not logged data).
    Snapshots the full prior row so it can be undone."""
    routine = (a.routine or "").strip(); exercise = (a.exercise or "").strip()
    c = runtime.connect(context.database)
    try:
        require_history(c)
        prior = c.execute("SELECT ex_order,target_sets,target_reps,target_weight_kg FROM routines "
                          "WHERE routine_name=? AND exercise_title=?", (routine, exercise)).fetchone()
        if not prior: sys.exit("no such exercise in that routine")
        snapshot(c, "remove", routine, exercise, prior)
        c.execute("DELETE FROM routines WHERE routine_name=? AND exercise_title=?", (routine, exercise))
        if migrations.recorded_version(c) >= 3:
            migrations.require_version(c, 3)
            migrations.append_training_plan_revision(
                c, effective_from=runtime.today(clock=context.clock), source="routine-remove")
        c.commit()
        return {"ok": True, "removed": exercise, "from": routine}
    finally:
        c.close()

def routine_undo(context, a):
    """Reverse the most recent program edit — routine OR schedule change.
    op='hevy-sync' snapshot rows are NOT candidates: they're the bulk sync's
    audit trail, and 'undoing' one would be a lying no-op that buries the
    latest explicit manual edit under N journal rows."""
    c = runtime.connect(context.database)
    try:
        require_history(c)
        h = c.execute("SELECT * FROM routines_history WHERE undone=0 AND op!='hevy-sync'"
                      " ORDER BY id DESC LIMIT 1").fetchone()
        if not h: sys.exit("nothing to undo")
        r, e = h["routine_name"], h["exercise_title"]
        if h["op"] == "schedule":
            # 'schedule' journal reuses columns: routine_name=weekday, exercise_title=prior routine.
            if h["prior_existed"]:
                c.execute("UPDATE training_schedule SET routine_name=? WHERE weekday=?", (e, r))
            else:
                c.execute("DELETE FROM training_schedule WHERE weekday=?", (r,))
        elif h["op"] == "set" and h["prior_existed"]:
            c.execute("UPDATE routines SET ex_order=?,target_sets=?,target_reps=?,target_weight_kg=? "
                      "WHERE routine_name=? AND exercise_title=?",
                      (h["ex_order"], h["target_sets"], h["target_reps"], h["target_weight_kg"], r, e))
        elif h["op"] == "set":                        # edit had added a new row -> remove it
            c.execute("DELETE FROM routines WHERE routine_name=? AND exercise_title=?", (r, e))
        elif h["op"] == "remove":                     # re-insert the removed row
            c.execute("INSERT INTO routines(routine_name,exercise_title,ex_order,target_sets,target_reps,target_weight_kg) "
                      "VALUES(?,?,?,?,?,?)", (r, e, h["ex_order"], h["target_sets"], h["target_reps"], h["target_weight_kg"]))
        c.execute("UPDATE routines_history SET undone=1 WHERE id=?", (h["id"],))
        if migrations.recorded_version(c) >= 3:
            migrations.require_version(c, 3)
            migrations.append_training_plan_revision(
                c, effective_from=runtime.today(clock=context.clock), source="routine-undo")
        c.commit()
        return {"ok": True, "undid": {"op": h["op"], "routine": r, "exercise": e}}
    finally:
        c.close()
