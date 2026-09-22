"""Validated fitness capture and fitness/body report coordination."""

import sys

from .. import migrations, orchestrator, runtime
from .. import fitness, body_measurements
from ..fitness import catalog_or_die, require_fitness_tables
from ..fitness_contracts import KIND_FIELDS, FT_CLAMPS
from ..calculations import e1rm
from ..catalogs import ATHLETIC_AXES
from ..importers.common import valid_date


def fitness_test_log(context, a):
    """Log ONE fitness test (movement, side, value, date) into fitness_tests.
    Validation is catalog-driven: the movement must exist, unilateral movements
    require a side, and the value fields must match the movement's kind. e1RM is
    NOT stored — it's derived at read time from load×reps (one formula home)."""
    mv = (a.movement or "").strip().lower()
    spec = catalog_or_die(mv)
    # side: required (left|right) for unilateral, must be bilateral otherwise.
    side = (a.side or "").strip().lower() or ("bilateral" if not spec["unilateral"] else "")
    if spec["unilateral"] and side not in ("left", "right"):
        sys.exit(f"'{mv}' is unilateral — pass --side left or --side right")
    if not spec["unilateral"] and side != "bilateral":
        sys.exit(f"'{mv}' is bilateral — omit --side (or use 'bilateral')")
    # Required fields depend on kind; additional valid value fields are retained.
    vals = {"load_kg": a.load, "reps": a.reps, "seconds": a.seconds,
            "rating": a.rating, "cm": a.cm, "degrees": a.degrees,
            "passed": a.passed}
    required = KIND_FIELDS[spec["kind"]]
    missing = [f for f in required if vals[f] is None]
    if missing:
        sys.exit(f"'{mv}' ({spec['kind']}) needs {', '.join(required)} — missing {', '.join(missing)}")
    for f, v in vals.items():
        if v is None:
            continue
        lo, hi = FT_CLAMPS[f]
        if not lo <= v <= hi:
            sys.exit(f"{f}={v} out of range [{lo}, {hi}]")
        if f == "reps" and float(v) != int(v):
            sys.exit("reps must be a whole number")
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    c = runtime.connect(context.database)
    try:
        c.execute("BEGIN IMMEDIATE")
        require_fitness_tables(c)
        c.execute("""INSERT INTO fitness_tests(date, movement, side, load_kg, reps,
            seconds, rating, cm, degrees, passed, equipment_note, source)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d, mv, side, vals["load_kg"], vals["reps"], vals["seconds"],
             vals["rating"], vals["cm"], vals["degrees"], vals["passed"],
             (a.note or None), (a.source or "chat")))
        row_id = str(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        if migrations.recorded_version(c) >= 4:
            orchestrator.enqueue_internal_trigger(
                c,
                trigger_kind="quarterly_observation",
                source_table="fitness_tests",
                source_row_key=row_id,
                event_date=d,
            )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    res = {"ok": True, "logged": {"movement": mv, "name": spec["name"], "side": side,
                                  "kind": spec["kind"], "date": d}}
    if spec["kind"] == "strength":
        res["e1rm"] = e1rm(vals["load_kg"], vals["reps"])
        if not 6 <= vals["reps"] <= 8:
            res["outside_protocol_range"] = True   # protocol asks 6–8RM; stored anyway
    return res

def fitness_test_void(context, a):
    """Soft-void a mistaken test row (validated behavior 2026-07-10). The row is
    NEVER deleted — voided=1 + reason preserves the audit trail; every read
    skips voided rows. Deliberately NOT in the bridge allowlists (chat/SSH only),
    so the sandboxed panel can never void logged data."""
    c = runtime.connect(context.database)
    try:
        require_fitness_tables(c)
        row = c.execute("SELECT id, voided FROM fitness_tests WHERE id=?", (a.id,)).fetchone()
        if row is None:
            sys.exit(f"no fitness_tests row id={a.id}")
        if row["voided"]:
            sys.exit(f"row id={a.id} already voided")
        c.execute("UPDATE fitness_tests SET voided=1, void_reason=? WHERE id=?",
                  ((a.reason or "").strip() or None, a.id))
        c.commit()
        return {"ok": True, "voided": a.id, "reason": (a.reason or "").strip() or None}
    finally:
        c.close()

def athletic_target_set(context, a):
    """Set/replace an owner target for a §3c axis (editable seeded targets). For
    Strength, --lift names the Hevy exercise the target e1RM applies to; other
    axes use the bare axis. Targets are owner config, never model-guessed."""
    axis = (a.axis or "").strip().lower()
    if axis not in ATHLETIC_AXES:
        sys.exit(f"axis must be one of {', '.join(ATHLETIC_AXES)}")
    lift = (a.lift or "").strip()
    if axis == "strength" and not lift:
        sys.exit("--lift is required for the strength axis (per-lift e1RM target)")
    if axis != "strength" and lift:
        sys.exit(f"--lift only applies to the strength axis, not {axis}")
    if a.target <= 0:
        sys.exit("target must be positive")
    c = runtime.connect(context.database)
    try:
        require_fitness_tables(c)
        c.execute("""INSERT INTO athletic_targets(axis, lift, target, updated)
            VALUES(?,?,?,datetime('now'))
            ON CONFLICT(axis, lift) DO UPDATE SET target=excluded.target, updated=datetime('now')""",
            (axis, lift, float(a.target)))
        c.commit()
        return {"ok": True, "axis": axis, "lift": lift or None, "target": a.target}
    finally:
        c.close()

def fitness_tests(context, a):
    """Return the retained fitness tests report."""
    c = runtime.connect(context.database)
    try:
        return fitness.fitness_tests(c, a.days, clock=context.clock)
    finally:
        c.close()


def athletic_radar(context, a):
    """Return the retained athletic radar report."""
    c = runtime.connect(context.database)
    try:
        return fitness.athletic_radar(c, clock=context.clock)
    finally:
        c.close()


def strength_ratios(context, a):
    """Return the retained strength ratios report."""
    c = runtime.connect(context.database)
    try:
        return fitness.strength_ratios(c, a.view, clock=context.clock)
    finally:
        c.close()


def vtaper(context, a):
    """Return the retained vtaper report."""
    c = runtime.connect(context.database)
    try:
        return body_measurements.vtaper(c, a.days, clock=context.clock)
    finally:
        c.close()
