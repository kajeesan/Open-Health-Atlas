"""Transactions for daily observations, hydration and supplement capture."""

import math

from .. import events as insight_events, migrations as insight_migrations
from .. import orchestrator as insight_orchestrator, runtime
from ..capture_contracts import (
    DAYMAP, UPSERT_DATE_TABLES, capture_source, prepare_log, require_soreness_note,
)
from ..command_context import CommandContext
from ..importers.common import valid_date


def log(context: CommandContext, a):
    """Write allowlisted fields and invalidate explicit-none capture atomically."""
    table, data = prepare_log(a.table, a.fields, clock=context.clock)
    c = runtime.connect(context.database)
    try:
        with c:
            # The generic writer may use, but never create, the migration-owned field.
            if table == "subjective_daily":
                require_soreness_note(c)
            cols = ",".join(data); ph = ",".join("?" * len(data))
            sql = f"INSERT INTO {table}({cols}) VALUES({ph})"
            if table in UPSERT_DATE_TABLES and "date" in data:
                # Re-logging the same day updates that day's row instead of erroring.
                upd = ",".join(f"{k}=excluded.{k}" for k in data if k != "date")
                sql += f" ON CONFLICT(date) DO UPDATE SET {upd}" if upd else " ON CONFLICT(date) DO NOTHING"
            c.execute(sql, list(data.values()))
            phase2_applied = insight_migrations.recorded_version(c) >= 2
            if table == "meds_log" and phase2_applied:
                key, _ = insight_events.resolve_identity(c, "medication", data.get("drug"))
                insight_events.invalidate_explicit_none(
                    c, data.get("date", runtime.today(clock=context.clock)), "medication", key, "manual", None,
                )
            elif table == "supplements_log" and phase2_applied:
                product = c.execute(
                    "SELECT name FROM supplement_products WHERE supplement_id=?",
                    (data.get("supplement_id"),),
                ).fetchone()
                key, _ = insight_events.resolve_identity(
                    c, "supplement", product["name"] if product else None)
                insight_events.invalidate_explicit_none(
                    c, data.get("date", runtime.today(clock=context.clock)), "supplement", key, "manual", None,
                )
            c.commit()
            return {"ok": True, "table": table, "inserted": data}
    finally:
        c.close()


def water_add(context: CommandContext, a, *, water_target_ml):
    """Add hydration, then read the committed total against the configured fallback."""
    ml = int(a.ml)
    if not 1 <= ml <= 3000:
        raise SystemExit("ml must be 1-3000 per tap (a bottle is a few hundred ml)")
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    c = runtime.connect(context.database)
    try:
        with c:
            c.execute("""INSERT INTO intake(date, water_ml) VALUES(?,?)
                         ON CONFLICT(date) DO UPDATE SET
                           water_ml = COALESCE(intake.water_ml, 0) + excluded.water_ml""",
                      (d, ml))
            c.commit()
            total = c.execute("SELECT water_ml FROM intake WHERE date=?", (d,)).fetchone()["water_ml"]
            return {"ok": True, "date": d, "added_ml": ml, "total_ml": total,
                 "target_ml": water_target_ml,
                 "pct_of_target": round(100 * total / water_target_ml)}
    finally:
        c.close()


def day_rating(context: CommandContext, a):
    """One-tap end-of-day traffic light: green(3)/yellow(2)/red(1). The never-skip minimum capture."""
    v = DAYMAP.get(str(a.rating).lower())
    if v is None: raise SystemExit("rating must be green | yellow | red")
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    source = capture_source(getattr(a, "source", None))
    c = runtime.connect(context.database)
    try:
        c.execute("BEGIN IMMEDIATE")
        existed = c.execute(
            "SELECT 1 FROM subjective_daily WHERE date=? AND day_rating IS NOT NULL",
            (d,),
        ).fetchone() is not None
        if source is None:
            # Preserve the pre-Phase-1 SQL/default and response shape byte-for-byte.
            c.execute("""INSERT INTO subjective_daily(date, day_rating) VALUES(?,?)
                         ON CONFLICT(date) DO UPDATE SET day_rating=excluded.day_rating""", (d, v))
        else:
            c.execute("""INSERT INTO subjective_daily(date, day_rating, source) VALUES(?,?,?)
                         ON CONFLICT(date) DO UPDATE SET
                           day_rating=excluded.day_rating, source=excluded.source""",
                      (d, v, source))
        if not existed and insight_migrations.recorded_version(c) >= 4:
            count = int(c.execute(
                "SELECT COUNT(*) FROM subjective_daily WHERE day_rating IS NOT NULL"
            ).fetchone()[0])
            # The frozen statistical eligibility floor is 30; every new block
            # of 30 observations is a deterministic count milestone.
            if count >= 30 and count % 30 == 0:
                insight_orchestrator.enqueue_internal_trigger(
                    c,
                    trigger_kind="outcome_milestone",
                    source_table="subjective_daily",
                    source_row_key=f"day_rating_count_{count}",
                    event_date=d,
                )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    result = {"ok": True, "date": d, "day_rating": v,
              "meaning": {1:"red (bad day)",2:"yellow (okay day)",3:"green (good day)"}[v]}
    if source is not None:
        result["source"] = source
    return result


def supplement_log(context: CommandContext, a):
    """Append one explicitly sourced supplement observation with nullable time."""
    supplement = insight_events.bounded_text(
        a.supplement, "supplement", 300, nullable=False, preserve=False)
    try:
        d = insight_events.iso_date(a.date) if a.date else runtime.today(clock=context.clock)
    except insight_events.CaptureError:
        raise
    if a.time is not None:
        insight_events.hhmm(a.time, "--time", nullable=False)
    if a.taken is not None and a.taken not in (0, 1):
        raise insight_events.CaptureError(
            "validation_error", "--taken must be 0 or 1", validation=True)
    if a.dose is not None and not (math.isfinite(a.dose) and 0 <= a.dose <= 1_000_000):
        raise insight_events.CaptureError(
            "validation_error", "--dose must be a finite number between 0 and 1000000",
            validation=True)
    if a.taken == 0 and a.dose not in (None, 0):
        raise insight_events.CaptureError(
            "validation_error", "--taken 0 cannot include a positive dose", validation=True)
    if a.note is not None and (not a.note.strip() or len(a.note) > 2000):
        raise insight_events.CaptureError(
            "validation_error", "--note must contain 1-2000 characters", validation=True)
    source = insight_events.event_source(a.source)
    insight_events.validate_capture_id(a.capture_id, nullable=True)
    if source in {"chat-panel", "chat-telegram"} and a.capture_id is None:
        raise insight_events.CaptureError(
            "validation_error", f"--source {source} requires --capture-id", validation=True)
    c = runtime.connect(context.database)
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 2)
        insight_migrations.require_table(c, "supplements_log", ("time_taken",))
        rows = []
        if supplement.isdigit():
            rows = c.execute("SELECT * FROM supplement_products WHERE supplement_id=?",
                             (int(supplement),)).fetchall()
        if not rows:
            rows = c.execute("SELECT * FROM supplement_products WHERE name=?",
                             (supplement,)).fetchall()
        if not rows:
            raise insight_events.CaptureError(
                "not_found", f"supplement not found: {supplement}")
        if len(rows) != 1:
            raise insight_events.CaptureError(
                "validation_error", "supplement name is ambiguous; use its numeric ID",
                validation=True)
        product = rows[0]
        c.execute("""INSERT INTO supplements_log(
            date,supplement_id,taken,dose_taken,time_taken,notes,source)
            VALUES(?,?,?,?,?,?,?)""",
            (d, product["supplement_id"], a.taken, a.dose, a.time, a.note, source))
        row_id = str(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        insight_events.link_capture(
            c, a.capture_id, source, "supplements_log", row_id,
            event_date=d, event_time=a.time, check_time=True,
        )
        entity_key_value, _ = insight_events.resolve_identity(
            c, "supplement", product["name"])
        insight_events.invalidate_explicit_none(
            c, d, "supplement", entity_key_value, source, a.capture_id,
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return {"ok": True, "id": int(row_id), "date": d, "supplement_id": product["supplement_id"],
         "supplement": product["name"], "taken": a.taken, "dose": a.dose,
         "time": a.time, "source": source, "capture_id": a.capture_id}
