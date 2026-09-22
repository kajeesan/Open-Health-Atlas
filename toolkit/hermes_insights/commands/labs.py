"""CLI ownership for raw laboratory capture, ingestion and reads."""

import os
from .. import labs as labs_domain, runtime, vault_notes
from ..importers.common import valid_date


def lab_capture(context, args, *, stdin):
    if stdin is None:
        raise TypeError("lab_capture requires an explicit stdin stream")
    data = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read()
    if not data:
        raise SystemExit("empty capture — nothing on stdin")
    if len(data) > 25_000_000:
        raise SystemExit("capture too large (>25 MB)")
    target = vault_notes.vault_write_path(context.vault, "raw/labs", args.name)
    if os.path.exists(target):
        raise SystemExit(
            f"raw/labs/{args.name} already exists — raw files are immutable; "
            "save a re-scan under a new name"
        )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "xb") as handle:
        handle.write(data)
    return {"ok": True, "file": f"raw/labs/{args.name}", "bytes": len(data)}


def lab_ingest(context, args, *, stdin):
    if stdin is None:
        raise TypeError("lab_ingest requires an explicit stdin stream")
    report_date = (
        valid_date(args.date, "--date")
        if args.date else runtime.today(clock=context.clock)
    )
    stream = stdin
    return _ingest_stream(context, args, stream.read(), report_date)


def _ingest_stream(context, args, text, report_date):
    connection = runtime.connect(context.database)
    try:
        result = labs_domain.lab_ingest(
            connection, args, text, report_date=report_date,
        )
        if args.commit:
            connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def labs(context, args):
    connection = runtime.connect(context.database)
    try:
        return labs_domain.labs(connection, args, clock=context.clock)
    finally:
        connection.close()
