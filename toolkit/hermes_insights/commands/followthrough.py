"""Commitment configuration and owner-entered follow-through captures."""

from .. import calculations, runtime
from ..capture_contracts import capture_source
from ..command_context import CommandContext
from ..followthrough import CHECKIN_KINDS, COMMIT_STATUS, feedback_report, require_followthrough
from ..importers.common import valid_date


def commitment_set(context: CommandContext, a):
    """Create or edit a commitment (upsert by name; config, not logged data)."""
    name = (a.name or "").strip()
    if not name: raise SystemExit("name is required")
    if a.active is not None and a.active not in (0, 1): raise SystemExit("--active must be 0 or 1")
    c = runtime.connect(context.database)
    try:
        with c:
            require_followthrough(c)
            prior = c.execute("SELECT * FROM commitments WHERE name=?", (name,)).fetchone()
            if prior:
                vals = {f: getattr(a, f) if getattr(a, f) is not None else prior[f]
                        for f in ("identity", "trigger", "floor", "reward", "active")}
                c.execute("UPDATE commitments SET identity=?,trigger=?,floor=?,reward=?,active=? "
                          "WHERE name=?", (*vals.values(), name))
            else:
                c.execute("INSERT INTO commitments(name,identity,trigger,floor,reward,active,created) "
                          "VALUES(?,?,?,?,?,?,?)",
                          (name, a.identity, a.trigger, a.floor, a.reward,
                           a.active if a.active is not None else 1, runtime.today(clock=context.clock)))
            c.commit()
            row = c.execute("SELECT * FROM commitments WHERE name=?", (name,)).fetchone()
            return {"ok": True, "was_new": prior is None, "commitment": dict(row)}
    finally:
        c.close()


def commitment_list(context: CommandContext, a):
    """List commitments in ID order, including inactive rows only when requested."""
    c = runtime.connect(context.database)
    try:
        with c:
            require_followthrough(c)
            q = "SELECT * FROM commitments" + ("" if a.all else " WHERE active=1") + " ORDER BY id"
            return {"commitments": [dict(r) for r in c.execute(q)]}
    finally:
        c.close()


def log_commitment(context: CommandContext, a):
    """Nightly kept/partly/broke. Without --id it's the whole-day 'did I keep
    my word?' one-tap; with --id it scores one commitment. Re-logging the same
    day updates (never duplicates, never deletes)."""
    status = (a.status or "").strip().lower()
    if status not in COMMIT_STATUS:
        raise SystemExit("status must be kept | partly | broke")
    source = capture_source(getattr(a, "source", None))
    c = runtime.connect(context.database)
    try:
        with c:
            require_followthrough(c)
            cid = a.id or 0
            name = None
            if cid:
                row = c.execute("SELECT name FROM commitments WHERE id=?", (cid,)).fetchone()
                if not row: raise SystemExit(f"no commitment with id {cid}")
                name = row["name"]
            d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
            # On re-log: a new why always wins; the old why survives only if the status
            # is unchanged — a "broke" excuse must never annotate a later "kept" (review).
            if source is None:
                c.execute("""INSERT INTO commitments_log(date, commitment_id, status, why)
                             VALUES(?,?,?,?) ON CONFLICT(date, commitment_id)
                             DO UPDATE SET status=excluded.status,
                               why=CASE WHEN excluded.why IS NOT NULL THEN excluded.why
                                        WHEN commitments_log.status=excluded.status THEN commitments_log.why
                                        ELSE NULL END""",
                          (d, cid, status, a.why))
            else:
                c.execute("""INSERT INTO commitments_log(date, commitment_id, status, why, source)
                             VALUES(?,?,?,?,?) ON CONFLICT(date, commitment_id)
                             DO UPDATE SET status=excluded.status, source=excluded.source,
                               why=CASE WHEN excluded.why IS NOT NULL THEN excluded.why
                                        WHEN commitments_log.status=excluded.status THEN commitments_log.why
                                        ELSE NULL END""",
                          (d, cid, status, a.why, source))
            c.commit()
            result = {"ok": True, "date": d, "scope": name or "whole-day", "status": status,
                      "why": a.why}
            if source is not None:
                result["source"] = source
            return result
    finally:
        c.close()


def checkin(context: CommandContext, a):
    """Timed 1-5 spot reading (energy/focus/mood) — makes within-day timing
    effects (dose onset/peak/wear-off) correlatable."""
    kind = (a.kind or "").strip().lower()
    if kind not in CHECKIN_KINDS: raise SystemExit("kind must be energy | focus | mood")
    if not 1 <= a.value <= 5: raise SystemExit("value must be 1-5")
    t = a.time or context.clock().strftime("%H:%M")
    if calculations._hhmm_min(t) is None: raise SystemExit("--time must be HH:MM")
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    source = capture_source(getattr(a, "source", None))
    c = runtime.connect(context.database)
    try:
        with c:
            require_followthrough(c)
            if source is None:
                c.execute("INSERT INTO checkins(date, time, kind, value, note) VALUES(?,?,?,?,?)",
                          (d, t, kind, a.value, a.note))
            else:
                c.execute("INSERT INTO checkins(date, time, kind, value, note, source) VALUES(?,?,?,?,?,?)",
                          (d, t, kind, a.value, a.note, source))
            c.commit()
            result = {"ok": True, "date": d, "time": t, "kind": kind, "value": a.value}
            if source is not None:
                result["source"] = source
            return result
    finally:
        c.close()


def feedback_status(context: CommandContext, a, *, output):
    """Read completeness, retaining the dedicated JSON validation-error contract."""
    try:
        d = valid_date(a.date)
    except SystemExit as exc:
        output({"ok": False, "error": {"code": "validation_error", "message": str(exc)}})
        raise SystemExit(2)
    c = runtime.connect_read_only(context.database)
    try:
        return feedback_report(c, d)
    finally:
        c.close()
