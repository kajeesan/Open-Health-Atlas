"""CLI ownership for cached, read-only association analysis."""

from .. import associations as associations_engine
from .. import exact_cache, runtime
from . import analytical


def outcome_associations_cmd(context, args, *, adapter_context=None):
    """Compute or reuse an association result against one read-only snapshot."""
    associations_engine.validate_options(
        outcome_key=args.outcome,
        mode=args.mode,
        min_n=args.min_n,
        interactions=args.interactions,
        top=args.top,
    )
    requested = analytical.requested_range(args)
    runtime.require_analytical_schema(context.database)
    if adapter_context is None:
        adapter_context = analytical.adapter_context(context)
    connection = runtime.connect_read_only(context.database)
    try:
        connection.execute("BEGIN")
        definitions = analytical.definitions(connection, adapter_context)
        result = exact_cache.cached_compute(
            context.database,
            {
                "operation": "outcome-associations",
                "range": requested.to_dict(),
                "outcome": args.outcome,
                "mode": args.mode,
                "min_n": args.min_n,
                "interactions": args.interactions,
                "top": args.top,
            },
            lambda: associations_engine.analyze_outcome(
                connection,
                definitions,
                requested,
                adapter_context,
                outcome_key=args.outcome,
                mode=args.mode,
                min_n=args.min_n,
                interactions=args.interactions,
                top=args.top,
            ),
            connection=connection,
            context=adapter_context,
            definitions=definitions,
        )
        return exact_cache.refresh_readiness(
            context.database,
            result,
            connection=connection,
            context=adapter_context,
            definitions=definitions,
        )
    except exact_cache.AnalysisBusy as exc:
        raise associations_engine.AssociationError("analysis_busy", str(exc)) from exc
    finally:
        connection.close()


def finding_evidence_cmd(context, args, *, adapter_context=None):
    """Recompute one finding's evidence against the same read-only contract."""
    associations_engine.validate_options(
        outcome_key=args.outcome,
        mode="all",
        min_n=associations_engine.DEFAULT_MIN_N,
        interactions="pairwise",
        top=100,
    )
    associations_engine.validate_sha256_id(args.finding_id, "finding-id")
    associations_engine.validate_sha256_id(
        args.input_fingerprint, "input-fingerprint",
    )
    requested = analytical.requested_range(args)
    runtime.require_analytical_schema(context.database)
    if adapter_context is None:
        adapter_context = analytical.adapter_context(context)
    connection = runtime.connect_read_only(context.database)
    try:
        connection.execute("BEGIN")
        definitions = analytical.definitions(connection, adapter_context)
        return exact_cache.cached_compute(
            context.database,
            {
                "operation": "finding-evidence",
                "range": requested.to_dict(),
                "outcome": args.outcome,
                "finding_id": args.finding_id,
                "input_fingerprint": args.input_fingerprint,
            },
            lambda: associations_engine.recompute_finding_evidence(
                connection,
                definitions,
                requested,
                adapter_context,
                outcome_key=args.outcome,
                finding_id_value=args.finding_id,
                input_fingerprint_value=args.input_fingerprint,
            ),
            connection=connection,
            context=adapter_context,
            definitions=definitions,
        )
    except exact_cache.AnalysisBusy as exc:
        raise associations_engine.AssociationError("analysis_busy", str(exc)) from exc
    finally:
        connection.close()
