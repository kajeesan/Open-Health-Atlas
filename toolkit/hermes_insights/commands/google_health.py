"""Google Health source-scoped writes and sleep gap-fill coordination."""

import math

from .. import migrations, runtime
from ..body_contracts import BODY_CLAMPS
from ..command_context import CommandContext
from ..importers import google_health as payload


def import_json(context: CommandContext, path, *, parse_number, stdin):
    """Atomically upsert collector observations while preserving other sources."""
    data = payload.load_json(path, stdin=stdin)

    c = runtime.connect(context.database)
    try:
        with c:
            n_days = dm_upserts = sleep_upserted = sleep_gap_filled = weights = 0
            skipped = []
            for day in data["days"]:
                d = payload.day_date(day)
                n_days += 1

                metrics = payload.metrics_for_day(day, skipped, parse_number=parse_number)
                if metrics:
                    # Migration 001 owns the additive HRV compatibility copy. Writers
                    # require the canonical column and never perform schema DDL.
                    if "hrv_ms" in metrics:
                        migrations.require_table(c, "daily_metrics", ("hrv_ms",))
                    cols = ",".join(metrics)
                    upd = ",".join(f"{k}=excluded.{k}" for k in metrics)
                    c.execute(f"""INSERT INTO daily_metrics(date, source, {cols})
                                  VALUES(?, 'fitbit', {','.join('?' * len(metrics))})
                                  ON CONFLICT(date, source) DO UPDATE SET {upd}""",
                              (d, *metrics.values()))
                    dm_upserts += 1

                sleep = payload.clamped(day.get("sleep") or {}, payload.GH_SLEEP_CLAMPS,
                                        skipped, parse_number=parse_number)
                if sleep:
                    row = c.execute("SELECT provenance FROM sleep_log WHERE date=?",
                                    (d,)).fetchone()
                    if row is None or row["provenance"] == "fitbit":
                        cols = ",".join(sleep)
                        upd = ",".join(f"{k}=excluded.{k}" for k in sleep)
                        c.execute(f"""INSERT INTO sleep_log(date, provenance, {cols})
                                      VALUES(?, 'fitbit', {','.join('?' * len(sleep))})
                                      ON CONFLICT(date) DO UPDATE SET {upd},
                                        provenance='fitbit'""",
                                  (d, *sleep.values()))
                        sleep_upserted += 1
                    else:
                        # someone else's night: fill NULL columns only, never overwrite
                        upd = ",".join(f"{k}=COALESCE(sleep_log.{k}, ?)" for k in sleep)
                        c.execute(f"UPDATE sleep_log SET {upd} WHERE date=?",
                                  (*sleep.values(), d))
                        sleep_gap_filled += 1

                if day.get("weight_kg") is not None:
                    w = parse_number(day["weight_kg"])
                    lo, hi = BODY_CLAMPS["weight_kg"]
                    if w is None or not math.isfinite(w) or not lo <= w <= hi:
                        skipped.append("weight_kg")
                    else:
                        migrations.require_table(c, "body_metrics", ("source",))
                        ex = c.execute("SELECT id FROM body_metrics WHERE date=? AND "
                                       "source='fitbit' ORDER BY id DESC LIMIT 1",
                                       (d,)).fetchone()
                        if ex:
                            c.execute("UPDATE body_metrics SET weight_kg=? WHERE id=?",
                                      (w, ex["id"]))
                        else:
                            c.execute("INSERT INTO body_metrics(date, weight_kg, source)"
                                      " VALUES(?,?, 'fitbit')", (d, w))
                        weights += 1
            c.commit()
            return ({"ok": True, "days": n_days, "daily_metrics_upserts": dm_upserts,
                 "sleep_upserted": sleep_upserted, "sleep_gap_filled": sleep_gap_filled,
                 "weights": weights, "skipped_values_out_of_range": len(skipped),
                 "skipped_fields": sorted(set(skipped))})
    finally:
        c.close()
