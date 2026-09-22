"""Schedule and planned-time commands with history and snapshot transactions."""

from .. import calculations, migrations as insight_migrations, runtime
from ..command_context import CommandContext
from ..routine_history import require_history
from ..schedules import TIMING_DEFAULT_TOL, WEEKDAYS, timing_report


def schedule_set(context: CommandContext, a):
    """Set a weekday's routine (Mon..Sun -> routine name, or 'Rest'). Journaled
    to routines_history so `routine-undo` reverses schedule changes too."""
    if a.weekday not in WEEKDAYS: raise SystemExit(f"weekday must be one of {', '.join(WEEKDAYS)}")
    routine = (a.routine or "").strip()
    if not routine: raise SystemExit("routine is required (use 'Rest' for a rest day)")
    c = runtime.connect(context.database)
    try:
        with c:
            require_history(c)
            prior = c.execute("SELECT routine_name FROM training_schedule WHERE weekday=?", (a.weekday,)).fetchone()
            # reuse columns: routine_name=weekday, exercise_title=prior routine to restore
            c.execute("INSERT INTO routines_history(op, routine_name, exercise_title, prior_existed) "
                      "VALUES('schedule',?,?,?)", (a.weekday, prior["routine_name"] if prior else None, 1 if prior else 0))
            c.execute("INSERT INTO training_schedule(weekday,routine_name) VALUES(?,?) "
                      "ON CONFLICT(weekday) DO UPDATE SET routine_name=excluded.routine_name", (a.weekday, routine))
            if insight_migrations.recorded_version(c) >= 3:
                insight_migrations.require_version(c, 3)
                insight_migrations.append_training_plan_revision(
                    c, effective_from=runtime.today(clock=context.clock), source="schedule-set")
            c.commit()
            return {"ok": True, "weekday": a.weekday, "routine": routine}
    finally:
        c.close()


def planned_time_set(context: CommandContext, a):
    """Set/replace the user's planned time (+ tolerance) for one §4b metric.
    Config command — NOT in the bridge allowlists (agent/SSH path only)."""
    metric = (a.metric or "").strip().lower()
    if metric not in TIMING_DEFAULT_TOL:
        raise SystemExit(f"metric must be one of: {', '.join(TIMING_DEFAULT_TOL)}")
    if calculations._hhmm_min(a.time) is None:
        raise SystemExit("time must be HH:MM (24h)")
    tol = a.tolerance if a.tolerance is not None else TIMING_DEFAULT_TOL[metric]
    if not 5 <= tol <= 240:
        raise SystemExit("--tolerance must be 5-240 minutes")
    c = runtime.connect(context.database)
    try:
        with c:
            insight_migrations.require_table(c, "planned_times")
            c.execute("""INSERT INTO planned_times(metric, planned, tolerance_min, updated)
                VALUES(?,?,?,datetime('now'))
                ON CONFLICT(metric) DO UPDATE SET planned=excluded.planned,
                  tolerance_min=excluded.tolerance_min, updated=datetime('now')""",
                (metric, a.time.strip(), tol))
            c.commit()
            return {"ok": True, "metric": metric, "planned": a.time.strip(), "tolerance_min": tol}
    finally:
        c.close()


def timing_adherence(context: CommandContext, a, *, medication_aliases):
    """Read complete-day timing adherence without changing plan configuration."""
    c = runtime.connect(context.database)
    try:
        insight_migrations.require_table(c, "planned_times")
        return timing_report(c, a.days, clock=context.clock, timezone=context.timezone,
                             medication_aliases=medication_aliases)
    finally:
        c.close()
