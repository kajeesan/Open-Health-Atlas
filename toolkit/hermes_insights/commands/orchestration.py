"""Command coordination for trigger queues and notification outbox writes."""

from .. import migrations as insight_migrations
from .. import orchestrator as insight_orchestrator
from .. import runtime
from . import analytical


def payload(stdin):
    """Parse one closed orchestration JSON object from the supplied stream."""

    return insight_orchestrator.parse_json_object(stdin.read())


def write(command_context, fn, *args):
    """Run one queue/outbox transition under the canonical immediate fence."""

    analytical.require_phase5_schema(command_context)
    connection = runtime.connect(command_context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        result = fn(connection, *args)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return result


def insight_trigger_enqueue(command_context, args, *, stdin):
    value = payload(stdin)
    insight_orchestrator.validate_trigger_enqueue(value)
    return write(command_context, insight_orchestrator.enqueue_trigger, value)


def insight_trigger_claim(command_context, args):
    return write(command_context, insight_orchestrator.claim_trigger, args.worker_id)


def insight_trigger_renew(command_context, args, *, stdin):
    value = payload(stdin)
    insight_orchestrator.validate_trigger_lease(value)
    return write(command_context, insight_orchestrator.renew_trigger, value)


def insight_trigger_complete(command_context, args, *, stdin):
    value = payload(stdin)
    insight_orchestrator.validate_trigger_complete(value)
    return write(command_context, insight_orchestrator.complete_trigger, value)


def insight_trigger_fail(command_context, args, *, stdin):
    value = payload(stdin)
    insight_orchestrator.validate_trigger_fail(value)
    return write(command_context, insight_orchestrator.fail_trigger, value)


def insight_notification_claim(command_context, args):
    return write(
        command_context,
        insight_orchestrator.claim_notification,
        args.worker_id,
    )


def insight_notification_begin_dispatch(command_context, args, *, stdin):
    return write(
        command_context,
        insight_orchestrator.begin_notification_dispatch,
        payload(stdin),
    )


def insight_notification_ack(command_context, args, *, stdin):
    return write(
        command_context,
        insight_orchestrator.acknowledge_notification,
        payload(stdin),
    )


def insight_notification_fail(command_context, args, *, stdin):
    value = payload(stdin)
    insight_orchestrator.validate_notification_fail(value)
    return write(command_context, insight_orchestrator.fail_notification, value)


def insight_notification_resolve(command_context, args, *, stdin):
    return write(
        command_context,
        insight_orchestrator.resolve_notification,
        payload(stdin),
    )


def insight_run_status(command_context, args):
    """Return the redacted, read-only autonomous run status."""

    if not 1 <= args.limit <= 100:
        raise insight_orchestrator.OrchestrationError(
            "validation_error",
            "--limit must be between 1 and 100",
            validation=True,
        )
    analytical.require_phase5_schema(command_context)
    connection = runtime.connect_read_only(command_context.database)
    try:
        return insight_orchestrator.insight_run_status(
            connection,
            limit=args.limit,
        )
    finally:
        connection.close()

