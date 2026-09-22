"""Command coordination for provider-neutral synthesis records."""

from datetime import timezone

from .. import associations as insight_associations
from .. import migrations as insight_migrations
from .. import orchestrator as insight_orchestrator
from .. import runtime
from .. import synthesis as insight_synthesis
from . import analytical, scheduled_analysis


def synthesis_record(command_context, args, *, stdin):
    """Validate and durably record one synthesis supplied on JSON stdin."""

    payload = insight_synthesis.parse_synthesis_json(stdin.read())
    insight_synthesis.validate_synthesis_record(payload)
    analytical.require_phase5_schema(command_context)
    connection = runtime.connect(command_context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        result = insight_synthesis.record_synthesis(connection, payload)
        # Publish SQLite first. If the filesystem append then fails, an exact
        # retry is a verified DB no-op and can safely finish the idempotent
        # file append. No final file can outlive a failed DB commit.
        connection.commit()
        markdown_path = None
        if result["message_eligible"]:
            markdown_path = insight_synthesis.write_synthesis_markdown(
                connection,
                result["synthesis_id"],
                vault_root=command_context.vault,
            )
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    return {
        **result,
        "markdown_path": str(markdown_path) if markdown_path is not None else None,
    }


def synthesis_prepare(command_context, args):
    """Return ledger-owned references for one external Hermes synthesis turn."""

    insight_associations.validate_sha256_id(args.batch_id, "batch-id")
    analytical.require_phase5_schema(command_context)
    connection = runtime.connect_read_only(command_context.database)
    try:
        batch = connection.execute(
            """SELECT batch_id,run_kind,anchor_date,status
                 FROM analysis_batches WHERE batch_id=?""",
            (args.batch_id,),
        ).fetchone()
        if batch is None:
            raise insight_synthesis.SynthesisError(
                "stale_reference",
                "analysis batch does not exist",
            )
        batch = dict(batch)
        cadence = batch["run_kind"]
        if cadence not in insight_synthesis.CADENCES:
            raise insight_synthesis.SynthesisError(
                "validation_error",
                "analysis batch cadence cannot produce a Hermes synthesis",
                validation=True,
            )
        refs = scheduled_analysis.refs_and_novelty(
            connection,
            batch,
            cadence,
            None,
        )
        evidence_fingerprint = insight_synthesis.synthesis_evidence_fingerprint(
            connection,
            analysis_batch_id=batch["batch_id"],
            run_refs=refs["run_refs"],
            finding_refs=refs["finding_refs"],
            hypothesis_refs=refs["hypothesis_refs"],
        )
        freshness = insight_orchestrator.freshness_snapshot(
            connection,
            now=command_context.clock().astimezone(timezone.utc),
        )
        structured_slots = insight_orchestrator.structured_slots(
            cadence=cadence,
            run_rows=refs["runs"],
            finding_rows=refs["finding_rows"],
            evaluation_rows=refs["evaluation_rows"],
            freshness=freshness,
            suppressed_dependencies=[],
        )
    finally:
        connection.close()
    return {
        "ok": True,
        "boundary": "synthesis-record",
        "analysis_batch_id": batch["batch_id"],
        "cadence": cadence,
        "cutoff_date": batch["anchor_date"],
        "context_version": insight_synthesis.SYNTHESIS_CONTEXT_VERSION,
        "evidence_fingerprint": evidence_fingerprint,
        "run_refs": refs["run_refs"],
        "finding_refs": refs["finding_refs"],
        "hypothesis_refs": refs["hypothesis_refs"],
        "assessment_state": (
            "assessed" if refs["finding_refs"] else "insufficient_data"
        ),
        "structured_slots": structured_slots,
        "model_invoked": False,
    }


def synthesis_history(command_context, args):
    """Return bounded synthesis history with stable pagination."""

    if not 1 <= args.limit <= 100:
        raise insight_synthesis.SynthesisError(
            "validation_error",
            "--limit must be between 1 and 100",
            validation=True,
        )
    if args.before is not None:
        insight_associations.validate_sha256_id(args.before, "before")
    analytical.require_phase5_schema(command_context)
    connection = runtime.connect_read_only(command_context.database)
    try:
        return insight_synthesis.synthesis_history(
            connection,
            limit=args.limit,
            before=args.before,
        )
    finally:
        connection.close()
