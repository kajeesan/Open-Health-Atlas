"""Governed pain capture and retained self-test, trial and void workflows."""

import sys

from .. import events, migrations, orchestrator, runtime
from ..capture_contracts import CAPTURE_SOURCES
from ..catalogs import PAIN_CAUSE_MAP, SELF_TEST_CATALOG, REHAB_CATALOG
from ..importers.common import valid_date
from ..physio import (
    require_physio_tables, PAIN_RED_FLAGS, PAIN_QUALITY, PAIN_SIDES, PAIN_PATTERN,
    TEST_RESULTS, TRIAL_RESPONSES, PAIN_NRS_CLAMP,
)


def pain_log(context, a):
    """Log ONE pain report (region, side, 0–10 NRS, optional quality/pattern/
    red-flag flags). Append-only. Region must be a known pain region (never
    guessed onto the figure); NRS clamp 0–10 (distinct from 1–5 wellbeing)."""
    region = (a.region or "").strip().lower()
    if region not in PAIN_CAUSE_MAP:
        sys.exit(f"unknown pain region {region!r} — one of {sorted(PAIN_CAUSE_MAP)}")
    if a.intensity is None:
        sys.exit("--intensity (0–10 NRS) is required")
    lo, hi = PAIN_NRS_CLAMP
    if not lo <= a.intensity <= hi:
        sys.exit(f"--intensity {a.intensity} out of range [{lo}, {hi}] (0–10 NRS)")
    side = (a.side or "central").strip().lower()
    if side not in PAIN_SIDES:
        sys.exit(f"--side must be one of {sorted(PAIN_SIDES)}")
    quality = (a.quality or "").strip().lower() or None
    if quality and quality not in PAIN_QUALITY:
        sys.exit(f"--quality must be one of {sorted(PAIN_QUALITY)}")
    pattern = (a.pattern or "").strip().lower() or None
    if pattern and pattern not in PAIN_PATTERN:
        sys.exit(f"--pattern must be one of {sorted(PAIN_PATTERN)}")
    flags = [f.strip().lower() for f in (a.flags or "").split(",") if f.strip()]
    bad = [f for f in flags if f not in PAIN_RED_FLAGS]
    if bad:
        sys.exit(f"unknown red-flag {bad} — one of {sorted(PAIN_RED_FLAGS)}")
    try:
        d = events.iso_date(a.date, "--date") if a.date else runtime.today(clock=context.clock)
    except events.CaptureError as exc:
        sys.exit(str(exc))
    onset_date = getattr(a, "reported_onset_date", None)
    onset_precision = getattr(a, "onset_precision", None)
    if onset_date is not None:
        try:
            onset_date = events.iso_date(onset_date, "--reported-onset-date")
        except events.CaptureError as exc:
            sys.exit(str(exc))
        if onset_date > d:
            sys.exit("--reported-onset-date cannot follow the observation date")
        if onset_precision not in {"exact", "approximate"}:
            sys.exit("a reported onset date requires --onset-precision exact or approximate")
    elif onset_precision not in (None, "unknown"):
        sys.exit("--onset-precision exact/approximate requires --reported-onset-date")
    capture_id = getattr(a, "capture_id", None)
    if capture_id is not None:
        try:
            events.validate_capture_id(capture_id)
        except events.CaptureError as exc:
            sys.exit(str(exc))
    source = a.source or "chat"
    if capture_id is not None:
        source = events.event_source(source)
    elif a.source is not None and source not in CAPTURE_SOURCES | {"import"}:
        sys.exit(f"--source must be one of: {', '.join(sorted(CAPTURE_SOURCES | {'import'}))}")
    if source in {"chat-panel", "chat-telegram"} and capture_id is None:
        sys.exit(f"--source {source} requires --capture-id")
    if a.note is not None and (not a.note.strip() or len(a.note) > 2000):
        sys.exit("--note must contain 1-2000 characters")
    c = runtime.connect(context.database)
    try:
        c.execute("BEGIN IMMEDIATE")
        if capture_id is not None:
            migrations.require_version(c, 2)
        require_physio_tables(c)
        first_positive = (
            int(a.intensity) > 0
            and c.execute(
                """SELECT 1 FROM pain_log
                    WHERE region=? AND side=? AND intensity>0 AND voided=0
                    LIMIT 1""",
                (region, side),
            ).fetchone() is None
        )
        c.execute("""INSERT INTO pain_log(date, region, side, intensity, quality,
            pattern, flags, note, source, reported_onset_date, onset_precision)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (d, region, side, int(a.intensity), quality, pattern,
             (",".join(flags) or None), (a.note or None), source,
             onset_date, onset_precision))
        row_id = str(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        events.link_capture(
            c, capture_id, source, "pain_log", row_id, event_date=d,
        )
        entity_key_value = events.pain_identity_key(region, side)
        if migrations.recorded_version(c) >= 2:
            events.invalidate_explicit_none(
                c, d, "pain", entity_key_value, source, capture_id,
            )
        if first_positive and migrations.recorded_version(c) >= 4:
            orchestrator.enqueue_internal_trigger(
                c,
                trigger_kind="pain_started",
                source_table="pain_log",
                source_row_key=row_id,
                event_date=d,
            )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    logged = {"region": region, "side": side, "intensity": int(a.intensity),
              "flags": flags, "date": d}
    if onset_date is not None or onset_precision is not None or capture_id is not None:
        logged.update({"reported_onset_date": onset_date,
                       "onset_precision": onset_precision,
                       "capture_id": capture_id})
    return {"ok": True, "logged": logged}

def self_test_log(context, a):
    """Log ONE self-test result (cited provocation/pattern aid, NOT diagnostic)."""
    test = (a.test or "").strip().lower()
    if test not in SELF_TEST_CATALOG:
        sys.exit(f"unknown self-test {test!r} — one of {sorted(SELF_TEST_CATALOG)}")
    result = (a.result or "").strip().lower()
    if result not in TEST_RESULTS:
        sys.exit(f"--result must be one of {sorted(TEST_RESULTS)}")
    side = (a.side or "central").strip().lower()
    if side not in PAIN_SIDES:
        sys.exit(f"--side must be one of {sorted(PAIN_SIDES)}")
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    c = runtime.connect(context.database)
    try:
        require_physio_tables(c)
        c.execute("""INSERT INTO self_test_log(date, test, side, result, note, source)
            VALUES(?,?,?,?,?,?)""",
            (d, test, side, result, (a.note or None), (a.source or "chat")))
        c.commit()
        return {"ok": True, "logged": {"test": test, "side": side, "result": result, "date": d}}
    finally:
        c.close()

def exercise_trial_log(context, a):
    """Log ONE rehab-drill trial + its 24–48h response (the physio flare rule)."""
    drill = (a.drill or "").strip().lower()
    if drill not in REHAB_CATALOG:
        sys.exit(f"unknown drill {drill!r} — one of {sorted(REHAB_CATALOG)}")
    response = (a.response or "").strip().lower()
    if response not in TRIAL_RESPONSES:
        sys.exit(f"--response must be one of {sorted(TRIAL_RESPONSES)}")
    target = (a.target or "").strip().lower() or None
    if target and target not in PAIN_CAUSE_MAP:
        sys.exit(f"--target must be a known pain region {sorted(PAIN_CAUSE_MAP)}")
    pd = a.pain_during
    if pd is not None and not PAIN_NRS_CLAMP[0] <= pd <= PAIN_NRS_CLAMP[1]:
        sys.exit(f"--pain-during {pd} out of range [0, 10]")
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    c = runtime.connect(context.database)
    try:
        require_physio_tables(c)
        c.execute("""INSERT INTO exercise_trial_log(date, drill, target, dose,
            response, pain_during, note, source) VALUES(?,?,?,?,?,?,?,?)""",
            (d, drill, target, (a.dose or None), response,
             (int(pd) if pd is not None else None), (a.note or None), (a.source or "chat")))
        c.commit()
        return {"ok": True, "logged": {"drill": drill, "target": target,
             "response": response, "date": d}}
    finally:
        c.close()

def physio_void(context, a):
    """Soft-void a mistaken physio row (deletion law — the row is NEVER deleted;
    voided=1 + reason preserves the trail, every read filters voided=0)."""
    table = {"pain": "pain_log", "self-test": "self_test_log",
             "trial": "exercise_trial_log"}.get((a.kind or "").strip().lower())
    if table is None:
        sys.exit("--kind must be one of pain, self-test, trial")
    c = runtime.connect(context.database)
    try:
        require_physio_tables(c)
        row = c.execute(f"SELECT voided FROM {table} WHERE id=?", (a.id,)).fetchone()
        if row is None:
            sys.exit(f"no {table} row id={a.id}")
        c.execute(f"UPDATE {table} SET voided=1, void_reason=? WHERE id=?",
                  ((a.reason or "voided"), a.id))
        c.commit()
        return {"ok": True, "voided": {"kind": a.kind, "id": a.id}}
    finally:
        c.close()
