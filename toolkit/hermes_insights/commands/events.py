"""CLI ownership for Phase 2 capture, event and identity workflows."""

from .. import events as events_engine
from .. import migrations, orchestrator, runtime


def _stdin_json(stdin):
    if stdin is None:
        raise TypeError("event commands require an explicit stdin stream")
    return events_engine.parse_json_stdin(stdin.read())


def _write_insight(context, function, *args, **kwargs):
    """Run one Phase 2 write in its original explicit transaction."""
    connection = runtime.connect(context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        migrations.require_version(connection, 2)
        result = function(connection, *args, **kwargs)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def capture_raw_cmd(context, args, *, stdin):
    payload = _stdin_json(stdin)
    events_engine.validate_capture_raw(payload)
    return _write_insight(context, events_engine.capture_raw, payload)


def capture_resolve_cmd(context, args, *, stdin):
    payload = _stdin_json(stdin)
    events_engine.validate_capture_resolve(payload)
    return _write_insight(context, events_engine.capture_resolve, payload)


def event_log_cmd(context, args, *, stdin):
    payload = _stdin_json(stdin)
    events_engine.validate_event(payload)
    connection = runtime.connect(context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        migrations.require_version(connection, 2)
        result = events_engine.event_log(connection, payload)
        if (
            payload["category"] == "medication_change"
            and migrations.recorded_version(connection) >= 4
        ):
            orchestrator.enqueue_internal_trigger(
                connection,
                trigger_kind="medication_regime_change",
                source_table="event_exposures",
                source_row_key=str(result["event_id"]),
                event_date=result["date"],
            )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def event_correct_cmd(context, args, *, stdin):
    events_engine.bounded_int(
        args.id, "id", 1, 2_147_483_647, nullable=False,
    )
    payload = _stdin_json(stdin)
    events_engine.validate_event(payload, correction=True)
    connection = runtime.connect(context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        migrations.require_version(connection, 2)
        result = events_engine.event_correct(connection, args.id, payload)
        if (
            payload["category"] == "medication_change"
            and migrations.recorded_version(connection) >= 4
        ):
            orchestrator.enqueue_internal_trigger(
                connection,
                trigger_kind="medication_regime_change",
                source_table="event_exposures",
                source_row_key=str(result["replacement_event_id"]),
                event_date=payload["date"],
            )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def event_void_cmd(context, args):
    events_engine.bounded_int(
        args.id, "id", 1, 2_147_483_647, nullable=False,
    )
    events_engine.bounded_text(args.reason, "reason", 500, nullable=False)
    return _write_insight(context, events_engine.event_void, args.id, args.reason)


def events_cmd(context, args):
    status = migrations.schema_status(context.database)
    if status["current_version"] < 2:
        raise migrations.SchemaError(
            "schema_migration_required", "schema version 2 is required",
        )
    connection = runtime.connect_read_only(context.database)
    try:
        return events_engine.list_events(
            connection,
            from_date=args.from_date,
            to_date=args.to_date,
            days=args.days,
            all_dates=args.all_dates,
            category=args.category,
            entity_key_value=args.entity_key,
        )
    finally:
        connection.close()


def capture_completeness_set_cmd(context, args):
    events_engine.validate_completeness_input(
        event_date=args.date,
        scope=args.scope,
        state=args.state,
        explicit_none=args.explicit_none,
        entity_key_value=args.entity_key,
        source=args.source,
        capture_id=args.capture_id,
        note=args.note,
    )
    return _write_insight(
        context,
        events_engine.completeness_set,
        event_date=args.date,
        scope=args.scope,
        state=args.state,
        explicit_none=args.explicit_none,
        entity_key_value=args.entity_key,
        source=args.source,
        capture_id=args.capture_id,
        note=args.note,
    )


def capture_completeness_cmd(context, args):
    status = migrations.schema_status(context.database)
    if status["current_version"] < 2:
        raise migrations.SchemaError(
            "schema_migration_required", "schema version 2 is required",
        )
    connection = runtime.connect_read_only(context.database)
    try:
        return events_engine.completeness_read(
            connection,
            from_date=args.from_date,
            to_date=args.to_date,
            days=args.days,
            all_dates=args.all_dates,
            scope=args.scope,
        )
    finally:
        connection.close()


def entity_alias_set_cmd(context, args):
    events_engine.validate_alias_set(
        args.type, args.alias, args.canonical, args.label,
    )
    return _write_insight(
        context,
        events_engine.alias_set,
        args.type,
        args.alias,
        args.canonical,
        args.label,
    )


def entity_alias_retire_cmd(context, args):
    events_engine.validate_alias_retire(args.type, args.alias)
    return _write_insight(
        context, events_engine.alias_retire, args.type, args.alias,
    )


def entity_alias_history_cmd(context, args):
    status = migrations.schema_status(context.database)
    if status["current_version"] < 2:
        raise migrations.SchemaError(
            "schema_migration_required", "schema version 2 is required",
        )
    connection = runtime.connect_read_only(context.database)
    try:
        return events_engine.alias_history(connection, args.type, args.alias)
    finally:
        connection.close()
