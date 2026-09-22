"""Shared configuration and compatibility helpers for analytical commands."""

from datetime import date

from .. import events, migrations, registry, runtime
from ..contracts import DateRange


def require_phase3_schema(command_context):
    """Require the Phase 3 schema before reading registry-backed features."""
    status = migrations.schema_status(command_context.database)
    if status["current_version"] < 3:
        raise migrations.SchemaError(
            "schema_migration_required", "schema version 3 is required",
        )
    return status


def require_phase5_schema(command_context):
    """Require the exact schema used by the autonomous insights surface."""
    status = migrations.schema_status(command_context.database)
    if status["current_version"] != migrations.AUTONOMOUS_SCHEMA_VERSION:
        raise migrations.SchemaError(
            "schema_migration_required",
            "hypothesis-ledger-v1 requires the exact current schema version",
        )
    return status


def requested_range(args):
    """Build the old Phase 3 inclusive range after validating its flags."""
    low, high, kind = events.resolve_range(
        from_date=args.from_date,
        to_date=args.to_date,
        days=args.days,
        all_dates=args.all_dates,
    )
    return DateRange(
        start=date.fromisoformat(low) if low else None,
        end=date.fromisoformat(high) if high else None,
        kind="all" if kind == "all" else "bounded",
    )


def definitions(connection, adapter_context, family=None):
    """Coerce registry builder output to the definition sequence callers expect."""
    built = registry.build_registry(connection, adapter_context, family=family)
    if isinstance(built, dict):
        for key in ("features", "entries", "registry", "definitions"):
            if key in built:
                return list(built[key])
        return list(built.values())
    if hasattr(built, "definitions"):
        return list(built.definitions)
    return list(built)


def adapter_context(command_context, *, bindings=None):
    """Bind adapters to the caller's clock, timezone and optional compatibility names."""
    return runtime.adapter_context(
        clock=command_context.clock,
        timezone=command_context.timezone,
        bindings=bindings,
    )
