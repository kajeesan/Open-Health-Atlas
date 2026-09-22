"""Canonical environmental writes followed by separate best-effort provenance."""

from .. import events as insight_events, goals as insight_goals
from .. import migrations as insight_migrations, runtime
from ..command_context import CommandContext
from ..importers import open_meteo


def best_effort_record(context: CommandContext, payload):
    """Record operational provenance without changing a collector's truth.

    During package-before-migration deployment, or if provenance recording has
    its own incident, the primary collection result remains authoritative.
    """
    try:
        insight_goals.validate_collector_run(payload)
        c = runtime.connect(context.database)
        try:
            c.execute("BEGIN IMMEDIATE")
            insight_migrations.require_version(c, 3)
            insight_goals.record_collector_run(c, payload)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    except Exception:
        return False
    return True


def _collector_coverage_date(value):
    try:
        return insight_events.iso_date(value, "date")
    except insight_events.CaptureError:
        return None


def _direct_collector_payload(context, source, started_at, status, coverage_date,
                              *, rows_seen, rows_written, error_code=None,
                              warning_codes=()):
    return {
        "source": source,
        "started_at": started_at,
        "completed_at": context.clock().isoformat(),
        "status": status,
        "coverage_from": coverage_date,
        "coverage_to": coverage_date,
        "rows_seen": rows_seen,
        "rows_written": rows_written,
        "error_code": error_code,
        "warning_codes": list(warning_codes),
    }


def _collect(context, a, *, reader, table, source, open_url, output):
    started = context.clock().isoformat()
    coverage = _collector_coverage_date(a.date or runtime.today(clock=context.clock))
    c = None
    try:
        try:
            row, result, missing = reader(
                a, day=a.date or runtime.today(clock=context.clock), open_url=open_url)
            cols = ",".join(row)
            placeholders = ",".join("?" * len(row))
            c = runtime.connect(context.database)
            c.execute(f"INSERT OR REPLACE INTO {table}({cols}) VALUES({placeholders})",
                      list(row.values()))
            c.commit()
            output(result)
        except Exception:
            # Retain the primary transaction during failure recording: a rejected
            # INSERT can hold the write lock, so legacy provenance also fails.
            best_effort_record(context, _direct_collector_payload(context,
                source, started, "failed", coverage,
                rows_seen=0, rows_written=0, error_code="collection_failed"))
            raise
        status = "partial" if missing else "success"
        warnings = ("missing_provider_fields",) if missing else ()
        best_effort_record(context, _direct_collector_payload(context,
            source, started, status, coverage,
            rows_seen=1, rows_written=1, warning_codes=warnings))
        return result
    finally:
        if c is not None:
            try:
                c.rollback()
            finally:
                c.close()


def fetch_weather(context: CommandContext, a, *, open_url, output):
    """Commit and emit weather before attempting separate operational provenance."""
    return _collect(context, a, reader=open_meteo.read_weather, table="weather",
                    source="weather", open_url=open_url, output=output)


def fetch_air(context: CommandContext, a, *, open_url, output):
    """Commit and emit air quality before attempting separate operational provenance."""
    return _collect(context, a, reader=open_meteo.read_air, table="air_quality",
                    source="air", open_url=open_url, output=output)


def collector_run_record(context: CommandContext, a, *, stdin):
    """Validate and record strict collector provenance in one write transaction."""
    payload = insight_events.parse_json_stdin(stdin.read())
    insight_goals.validate_collector_run(payload)
    c = runtime.connect(context.database)
    try:
        c.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(c, 3)
        result = insight_goals.record_collector_run(c, payload)
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return result
