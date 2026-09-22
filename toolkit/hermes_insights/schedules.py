"""Weekday and timing-plan contracts with the bounded adherence report."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from . import calculations, runtime


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# Generous default windows deliberately count small slips as on time.
TIMING_DEFAULT_TOL = {"wake": 30, "bed": 30, "workout": 60, "dose": 30}


def timing_report(c, window_days, *, clock, timezone, medication_aliases):
    """Report done and on-time rates for complete days using the configured civil clock."""
    days = max(int(window_days), 7)
    end = date.fromisoformat(runtime.today(clock=clock)) - timedelta(days=1)       # complete days only
    start = end - timedelta(days=days - 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    lo = dates[0]
    plans = {r["metric"]: r for r in c.execute("SELECT * FROM planned_times")}

    def gather(metric):
        """{date: minutes-from-midnight} of actuals + unparseable count."""
        actuals, unparsed = {}, 0
        if metric in ("wake", "bed") and runtime.table_exists(c, "sleep_log"):
            col = "wake_time" if metric == "wake" else "bedtime"
            for r in c.execute(f"SELECT date, {col} v FROM sleep_log "
                               f"WHERE date>=? AND {col} IS NOT NULL", (lo,)):
                v = calculations._hhmm_min(r["v"])
                if v is None:
                    unparsed += 1
                else:
                    actuals[r["date"]] = v
        elif metric == "dose" and runtime.table_exists(c, "meds_log"):
            ph = ",".join("?" * len(medication_aliases))
            per = {}
            for r in c.execute(f"SELECT date, time_taken v FROM meds_log "
                               f"WHERE LOWER(drug) IN ({ph}) AND date>=? "
                               f"AND time_taken IS NOT NULL", (*sorted(medication_aliases), lo)):
                v = calculations._hhmm_min(r["v"])           # min() in MINUTES — '8:00' vs '12:30'
                if v is None:                   # sorts wrong as text
                    unparsed += 1
                elif r["date"] not in per or v < per[r["date"]]:
                    per[r["date"]] = v
            actuals = per
        elif metric == "workout" and runtime.table_exists(c, "hevy_sets"):
            per = {}
            for r in c.execute("""SELECT date, MIN(start_time) v FROM hevy_sets
                WHERE date>=? AND COALESCE(set_type,'normal')!='warmup'
                GROUP BY date""", (lo,)):
                v = calculations._start_hhmm(r["v"], timezone=ZoneInfo(timezone))
                if v is None:
                    per[r["date"]] = None       # trained, but no usable time
                    unparsed += 1
                else:
                    per[r["date"]] = calculations._hhmm_min(v)
            actuals = per
        return actuals, unparsed

    # workout is only EXPECTED on scheduled (non-Rest) weekdays
    sched_days = set()
    if runtime.table_exists(c, "training_schedule"):
        sched_days = {r["weekday"] for r in c.execute(
            "SELECT weekday, routine_name FROM training_schedule")
            if (r["routine_name"] or "Rest") != "Rest"}

    metrics = {}
    for metric in ("wake", "bed", "workout", "dose"):
        plan = plans.get(metric)
        if plan is None:
            metrics[metric] = {"status": "insufficient_data",
                               "reason": "no planned time set (planned-time-set)",
                               "default_tolerance_min": TIMING_DEFAULT_TOL[metric]}
            continue
        planned_min = calculations._hhmm_min(plan["planned"])
        tol = plan["tolerance_min"]
        actuals, unparsed = gather(metric)
        # complete days only: gather reads date>=lo, so today's (in-progress)
        # rows can be present — drop them before the insufficiency check
        actuals = {d: v for d, v in actuals.items() if d <= dates[-1]}
        if metric == "workout":
            expected = [d for d in dates
                        if WEEKDAYS[date.fromisoformat(d).weekday()] in sched_days]
        else:
            expected = dates
        if not actuals or not expected:
            metrics[metric] = {"status": "insufficient_data",
                               "planned": plan["planned"], "tolerance_min": tol,
                               "reason": ("no scheduled training days" if not expected
                                          else "no actual times logged in the window")}
            continue
        done_days = [d for d in expected if d in actuals]
        timed = {d: v for d, v in actuals.items() if v is not None and d in expected}
        on_time = {d: calculations._circ_diff_min(v, planned_min) <= tol for d, v in timed.items()}
        deltas = sorted(calculations._circ_diff_min(v, planned_min) for v in timed.values())
        # streak: walk back from the most recent expected day; on-time extends,
        # a miss or an off-window day breaks, a day with an unusable time (or a
        # non-expected day, e.g. a rest day) is skipped — neither way.
        streak = 0
        for d in reversed(expected):
            if d not in actuals:
                break                            # expected but not done
            if actuals[d] is None:
                continue                         # done, time unknown — skip
            if on_time[d]:
                streak += 1
            else:
                break
        metrics[metric] = {
            "planned": plan["planned"], "tolerance_min": tol,
            "days_expected": len(expected),
            "done": {"days": len(done_days),
                     "rate": round(len(done_days) / len(expected), 3)},
            "on_time": {"days": sum(on_time.values()),
                        "of_timed_days": len(timed),
                        "rate": round(sum(on_time.values()) / len(timed), 3) if timed else None},
            "median_abs_delta_min": deltas[len(deltas) // 2] if deltas else None,
            "streak_on_time": streak,
            "unparsed_times": unparsed,
        }
    return {"window_days": days, "window": {"from": dates[0], "to": dates[-1]},
         "metrics": metrics,
         "note": ("on-time = within the tolerance window of the planned time "
                  "(generous by design — a small slip still counts; rates are "
                  "rhythm information, never a grade)")}
