"""CLI ownership for feature registry, readiness and goal workflows."""

from .. import frame, goals, migrations, readiness, registry, runtime
from . import analytical


def feature_registry_cmd(context, args, *, adapter_context=None):
    analytical.require_phase3_schema(context)
    if adapter_context is None:
        adapter_context = analytical.adapter_context(context)
    connection = runtime.connect_read_only(context.database)
    try:
        definitions = analytical.definitions(
            connection, adapter_context, family=args.family,
        )
        return registry.serialize_registry(definitions)
    finally:
        connection.close()


def feature_frame_cmd(context, args, *, adapter_context=None):
    requested = analytical.requested_range(args)
    analytical.require_phase3_schema(context)
    if adapter_context is None:
        adapter_context = analytical.adapter_context(context)
    connection = runtime.connect_read_only(context.database)
    try:
        definitions = analytical.definitions(
            connection, adapter_context, family=args.family,
        )
        return frame.build_feature_frame(
            connection,
            definitions,
            requested,
            adapter_context,
            include_provenance=args.include_provenance,
        )
    finally:
        connection.close()


def data_readiness_cmd(context, args, *, adapter_context=None):
    requested = analytical.requested_range(args)
    analytical.require_phase3_schema(context)
    if adapter_context is None:
        adapter_context = analytical.adapter_context(context)
    connection = runtime.connect_read_only(context.database)
    try:
        definitions = analytical.definitions(connection, adapter_context)
        return readiness.build_readiness(
            connection,
            definitions,
            requested,
            adapter_context,
            goal=args.goal,
            outcome=args.outcome,
        )
    finally:
        connection.close()


def goal_list_cmd(context, args):
    analytical.require_phase3_schema(context)
    connection = runtime.connect_read_only(context.database)
    try:
        return goals.list_goals(connection, include_disabled=args.all_goals)
    finally:
        connection.close()


def goal_set_cmd(context, args, *, adapter_context=None):
    """Validate a registered goal before acquiring its write transaction."""
    analytical.require_phase3_schema(context)
    if adapter_context is None:
        adapter_context = analytical.adapter_context(context)
    connection = runtime.connect(context.database)
    try:
        definitions = analytical.definitions(connection, adapter_context)
        registered = {
            getattr(
                item,
                "key",
                item.get("key") if isinstance(item, dict) else None,
            ): item
            for item in definitions
        }
        registered.pop(None, None)
        goals.prepare_goal_revision(
            connection,
            goal_key=args.goal,
            enabled=args.enabled,
            priority=args.priority,
            outcome_key=args.outcome,
            direction=args.direction,
            note=args.note,
            source=args.source,
            registered_keys=registered,
        )
        connection.execute("BEGIN IMMEDIATE")
        migrations.require_version(connection, 3)
        result = goals.set_goal(
            connection,
            goal_key=args.goal,
            enabled=args.enabled,
            priority=args.priority,
            outcome_key=args.outcome,
            direction=args.direction,
            note=args.note,
            source=args.source,
            registered_keys=registered,
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
