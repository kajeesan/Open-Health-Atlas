"""Coordination for scheduled and trigger-driven autonomous analysis."""

from datetime import timezone
import re

from . import analytical
from .. import associations as insight_associations
from .. import frame as insight_frame
from .. import ledger as insight_ledger
from .. import migrations as insight_migrations
from .. import orchestrator as insight_orchestrator
from .. import provenance as insight_provenance
from .. import registry as insight_registry
from .. import runtime
from .. import synthesis as insight_synthesis


def prepare_batch(
    command_context,
    kind,
    plan,
    trigger,
    *,
    adapter_context=None,
):
    """Create the deterministic fan-out before expensive read-only computation."""

    bound_context = (
        adapter_context
        if adapter_context is not None
        else analytical.adapter_context(command_context)
    )
    connection = runtime.connect_read_only(command_context.database)
    try:
        connection.execute("BEGIN")
        all_definitions = analytical.definitions(connection, bound_context)
        freshness = insight_orchestrator.freshness_snapshot(
            connection,
            now=command_context.clock().astimezone(timezone.utc),
        )
        definitions, suppressed = insight_orchestrator.suppress_stale_dependencies(
            all_definitions, freshness,
        )
        selection = insight_orchestrator.outcome_modes(
            connection,
            trigger_kind=trigger["trigger_kind"] if trigger is not None else None,
        )
        for outcome in selection["outcomes"]:
            insight_associations.validate_options(
                outcome_key=outcome,
                mode="all",
                min_n=insight_associations.DEFAULT_MIN_N,
                interactions="pairwise",
                top=100,
            )
        registry_hash = (
            "sha256:" + insight_registry.registry_content_checksum(definitions)
        )
    finally:
        connection.close()

    initiator = (
        f"trigger/{trigger['trigger_id']}"
        if trigger is not None
        else f"scheduled-{kind}-v1"
    )
    connection = runtime.connect(command_context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        producer_triggers = []
        restart_definitions = [
            definition for definition in definitions
            if definition.key == "running.restart"
        ]
        if restart_definitions:
            primary = next(
                requested
                for role, requested in insight_orchestrator.range_objects(plan)
                if role == "primary"
            )
            running_adapter = insight_frame.load_adapters()["running"]
            restart_observations = running_adapter.load(
                connection,
                tuple(restart_definitions),
                primary,
                bound_context,
                include_provenance=True,
            )
            for observation in restart_observations:
                if (
                    observation.feature_key == "running.restart"
                    and observation.state == "observed"
                    and observation.value == 1
                ):
                    producer_triggers.append(
                        insight_orchestrator.enqueue_internal_trigger(
                            connection,
                            trigger_kind="running_restart",
                            source_table="workouts",
                            source_row_key=f"restart:{observation.observed_at}",
                            event_date=observation.observed_at,
                        )
                    )
        batch = insight_ledger.create_analysis_batch(
            connection,
            run_kind=kind,
            anchor_date=plan["anchor_date"],
            initiator_key=initiator,
            outcome_selection=selection["selection"],
            outcomes=selection["outcome_modes"],
            ranges=plan["ranges"],
            registry_sha256_value=registry_hash,
            range_plan_version=insight_orchestrator.RANGE_PLAN_VERSION,
        )
        runs = []
        for range_row in batch["ranges"]:
            for outcome in selection["outcome_modes"]:
                runs.append(
                    insight_ledger.start_analysis_run(
                        connection,
                        batch_id=batch["batch_id"],
                        range_id=range_row["range_id"],
                        outcome_key=outcome["outcome_key"],
                        outcome_mode=outcome["outcome_mode"],
                        batch_outcomes=selection["outcome_modes"],
                    )
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "batch": batch,
        "runs": runs,
        "definitions": definitions,
        "context": bound_context,
        "selection": selection,
        "freshness": freshness,
        "suppressed_dependencies": suppressed,
        "producer_triggers": producer_triggers,
    }


def compute_runs(
    command_context,
    prepared,
    plan,
    *,
    adapter_context=None,
):
    """Compute all running fan-out members on one read-only snapshot."""

    ranges = {
        item["range_role"]: requested
        for item, (_role, requested) in zip(
            plan["ranges"], insight_orchestrator.range_objects(plan), strict=True,
        )
    }
    by_range = {
        item["range_id"]: item["range_role"]
        for item in prepared["batch"]["ranges"]
    }
    bound_context = (
        prepared["context"]
        if adapter_context is None
        else adapter_context
    )
    computed, failed = [], []
    connection = runtime.connect_read_only(command_context.database)
    try:
        connection.execute("BEGIN")
        # Rebuild inside the frozen DB snapshot, then apply the same freshness
        # dependency filter calculated for the batch identity.
        definitions = analytical.definitions(connection, bound_context)
        definitions, _unused = insight_orchestrator.suppress_stale_dependencies(
            definitions, prepared["freshness"],
        )
        for run in prepared["runs"]:
            if run["status"] != "running":
                continue
            role = by_range[run["range_id"]]
            try:
                verified = insight_ledger.compute_verified_analysis(
                    connection,
                    definitions,
                    ranges[role],
                    bound_context,
                    outcome_key=run["outcome_key"],
                    outcome_mode=run["outcome_mode"],
                )
                computed.append((run, verified))
            except Exception as exc:
                code = getattr(exc, "code", None)
                if not isinstance(code, str) or re.fullmatch(
                    r"[a-z][a-z0-9_]{0,79}", code,
                ) is None:
                    code = "analysis_compute_failed"
                failed.append((run, code))
    finally:
        connection.close()

    persisted = []
    for run, verified in computed:
        connection = runtime.connect(command_context.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            persisted.append(
                insight_ledger.persist_analysis_run(
                    connection,
                    run_id=run["run_id"],
                    verified=verified,
                )
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    for run, reason in failed:
        connection = runtime.connect(command_context.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            persisted.append(
                insight_ledger.fail_analysis_run(
                    connection,
                    run_id=run["run_id"],
                    reason_code=reason,
                )
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    return persisted


def refs_and_novelty(connection, batch, kind, trigger):
    runs = [
        dict(row)
        for row in connection.execute(
            """SELECT r.*,q.range_role
                 FROM analysis_runs r
                 JOIN analysis_range_requests q ON q.range_id=r.range_id
                WHERE r.batch_id=?
                ORDER BY q.range_role,r.outcome_key,r.outcome_mode,r.run_id""",
            (batch["batch_id"],),
        )
    ]
    run_refs = [
        {
            "run_id": row["run_id"],
            "purpose": "trigger" if kind == "trigger" else row["range_role"],
        }
        for row in runs
    ]
    finding_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT f.*,q.range_role
                 FROM analysis_findings f
                 JOIN analysis_runs r ON r.run_id=f.run_id
                 JOIN analysis_range_requests q ON q.range_id=r.range_id
                WHERE r.batch_id=? AND f.eligible_for_hypothesis=1
                ORDER BY q.range_role,f.outcome_key,f.finding_id LIMIT 256""",
            (batch["batch_id"],),
        )
    ]
    finding_refs = [
        {"finding_id": row["finding_id"], "role": "primary"}
        for row in finding_rows
    ]
    evaluation_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT e.*
                 FROM hypothesis_evaluations e
                 JOIN analysis_runs r ON r.run_id=e.run_id
                WHERE r.batch_id=?
                ORDER BY e.hypothesis_id,e.id DESC""",
            (batch["batch_id"],),
        )
    ]
    latest_evaluations = []
    seen_hypotheses = set()
    for row in evaluation_rows:
        if row["hypothesis_id"] in seen_hypotheses:
            continue
        seen_hypotheses.add(row["hypothesis_id"])
        latest_evaluations.append(row)
    latest_evaluations = latest_evaluations[:256]
    hypothesis_refs = [
        {
            "hypothesis_id": row["hypothesis_id"],
            "evaluation_id": row["id"],
            "role": "changed" if row["transition_applied"] else "context",
        }
        for row in latest_evaluations
    ]
    has_weekly_baseline = True
    if kind == "weekly":
        has_weekly_baseline = connection.execute(
            """SELECT 1 FROM synthesis_runs
                WHERE cadence='manual' AND status='completed'
                ORDER BY completed_at DESC LIMIT 1"""
        ).fetchone() is not None
    decision = insight_orchestrator.novelty_decision(
        cadence=kind,
        finding_fingerprints=(row["evidence_fingerprint"] for row in finding_rows),
        transition_fingerprints=(
            row["evidence_fingerprint"]
            for row in latest_evaluations
            if row["transition_applied"]
        ),
        approved_trigger=trigger is not None,
        has_weekly_baseline=has_weekly_baseline,
    )
    return {
        "runs": runs,
        "run_refs": run_refs,
        "finding_rows": finding_rows,
        "finding_refs": finding_refs,
        "evaluation_rows": latest_evaluations,
        "hypothesis_refs": hypothesis_refs,
        "novelty": decision,
    }


def record_no_message(
    connection,
    *,
    batch,
    kind,
    anchor,
    refs,
    status,
    reason,
):
    evidence_fingerprint = insight_synthesis.synthesis_evidence_fingerprint(
        connection,
        analysis_batch_id=batch["batch_id"],
        run_refs=refs["run_refs"],
        finding_refs=[],
        hypothesis_refs=[],
    )
    synthesis_id = insight_provenance.sha256_id(
        {
            "contract_version": insight_synthesis.SYNTHESIS_CONTRACT_VERSION,
            "kind": "scheduled_no_message",
            "analysis_batch_id": batch["batch_id"],
            "cadence": kind,
            "status": status,
            "reason": reason,
            "evidence_fingerprint": evidence_fingerprint,
        }
    )
    return insight_synthesis.record_synthesis(
        connection,
        {
            "synthesis_id": synthesis_id,
            "analysis_batch_id": batch["batch_id"],
            "cadence": kind,
            "reason_code": reason,
            "cutoff_date": anchor,
            "evidence_fingerprint": evidence_fingerprint,
            "context_version": insight_synthesis.SYNTHESIS_CONTEXT_VERSION,
            "prompt_sha256": None,
            "model_id": None,
            "provider": None,
            "run_refs": refs["run_refs"],
            "finding_refs": [],
            "hypothesis_refs": [],
            "narrative_md": None,
            "rendered_md": None,
            "status": status,
            "no_message_reason_code": reason,
            "annotations": [],
            "notification": None,
        },
    )


def finalize(
    command_context,
    prepared,
    plan,
    kind,
    trigger,
    trigger_payload,
):
    connection = runtime.connect(command_context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        if trigger_payload is not None:
            # A long analytical computation never inherits authority from an
            # expired worker: re-check the exact generation/token fence.
            insight_orchestrator.assert_trigger_lease(connection, trigger_payload)
        batch = insight_ledger.finalize_analysis_batch(
            connection,
            batch_id=prepared["batch"]["batch_id"],
        )
        has_weekly_baseline = True
        if kind == "weekly":
            has_weekly_baseline = connection.execute(
                """SELECT 1 FROM synthesis_runs
                    WHERE cadence='manual' AND status='completed'
                    ORDER BY completed_at DESC LIMIT 1"""
            ).fetchone() is not None
        dormancy = None
        if kind == "monthly":
            dormancy = (
                "dormant_stale_prerequisite"
                if prepared["suppressed_dependencies"]
                else "dormant_no_eligible_data"
            )
        if kind == "nightly" or (kind == "weekly" and not has_weekly_baseline):
            ledger = {
                "ok": True,
                "changed": False,
                "skipped": True,
                "reason_code": (
                    "nightly_analysis_only"
                    if kind == "nightly"
                    else "bootstrap_baseline_required"
                ),
            }
        else:
            ledger = insight_ledger.refresh_batch_hypotheses(
                connection,
                batch_id=batch["batch_id"],
                dormancy_reason=dormancy,
            )

        # Derived producers share this transaction with the ledger transition.
        transitions = [
            dict(row)
            for row in connection.execute(
                """SELECT e.*
                     FROM hypothesis_evaluations e
                     JOIN analysis_runs r ON r.run_id=e.run_id
                    WHERE r.batch_id=? AND e.transition_applied=1
                    ORDER BY e.id""",
                (batch["batch_id"],),
            )
        ]
        derived_triggers = list(prepared["producer_triggers"])
        for row in transitions:
            trigger_kind = (
                "hypothesis_replicated"
                if row["status"] == "replicated"
                else "hypothesis_reversal"
                if row["evidence_class"] == "opposite_pass"
                else None
            )
            if trigger_kind is not None:
                derived_triggers.append(
                    insight_orchestrator.enqueue_internal_trigger(
                        connection,
                        trigger_kind=trigger_kind,
                        source_table="hypothesis_evaluations",
                        source_row_key=str(row["id"]),
                        event_date=row["range_to"] or plan["anchor_date"],
                    )
                )
        refs = refs_and_novelty(connection, batch, kind, trigger)
        terminal_status = "completed"
        reason = refs["novelty"].reason_code
        synthesis = None
        preparation = None
        terminal_runs = refs["runs"]
        usable = sum(row["status"] == "completed" for row in terminal_runs)
        if (
            kind == "weekly"
            and refs["novelty"].reason_code == "bootstrap_baseline_required"
        ):
            terminal_status, reason = "no_novelty", "bootstrap_baseline_required"
        elif batch["status"] == "failed":
            terminal_status, reason = "failed", "all_analysis_runs_failed"
        elif usable == 0:
            terminal_status, reason = "insufficient_data", "no_eligible_analysis_data"
        elif not refs["novelty"].eligible:
            terminal_status = "no_novelty"
        if kind != "nightly":
            if terminal_status in {
                "failed",
                "insufficient_data",
                "no_novelty",
                "suppressed",
            }:
                synthesis = record_no_message(
                    connection,
                    batch=batch,
                    kind=kind,
                    anchor=plan["anchor_date"],
                    refs=refs,
                    status=terminal_status,
                    reason=reason,
                )
            else:
                evidence_fingerprint = insight_synthesis.synthesis_evidence_fingerprint(
                    connection,
                    analysis_batch_id=batch["batch_id"],
                    run_refs=refs["run_refs"],
                    finding_refs=refs["finding_refs"],
                    hypothesis_refs=refs["hypothesis_refs"],
                )
                preparation = {
                    "boundary": "synthesis-record",
                    "analysis_batch_id": batch["batch_id"],
                    "cadence": kind,
                    "cutoff_date": plan["anchor_date"],
                    "context_version": insight_synthesis.SYNTHESIS_CONTEXT_VERSION,
                    "evidence_fingerprint": evidence_fingerprint,
                    "run_refs": refs["run_refs"],
                    "finding_refs": refs["finding_refs"],
                    "hypothesis_refs": refs["hypothesis_refs"],
                    "structured_slots": insight_orchestrator.structured_slots(
                        cadence=kind,
                        run_rows=refs["runs"],
                        finding_rows=refs["finding_rows"],
                        evaluation_rows=refs["evaluation_rows"],
                        freshness=prepared["freshness"],
                        suppressed_dependencies=prepared["suppressed_dependencies"],
                    ),
                    "model_invoked": False,
                }
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "batch": batch,
        "ledger": ledger,
        "status": terminal_status,
        "reason_code": reason,
        "synthesis": synthesis,
        "synthesis_preparation": preparation,
        "derived_triggers": derived_triggers,
    }


def analysis_refresh(
    command_context,
    args,
    *,
    stdin=None,
    adapter_context=None,
):
    """Run one scheduled or trigger-fenced analysis refresh."""

    analytical.require_phase5_schema(command_context)
    trigger_payload = None
    trigger = None
    if args.kind == "trigger":
        if stdin is None:
            raise insight_orchestrator.OrchestrationError(
                "validation_error",
                "trigger analysis-refresh requires JSON stdin",
                validation=True,
            )
        trigger_payload = insight_orchestrator.parse_json_object(stdin.read())
        insight_orchestrator.validate_trigger_lease(trigger_payload)
        analytical.require_phase5_schema(command_context)
        connection = runtime.connect_read_only(command_context.database)
        try:
            trigger = insight_orchestrator.assert_trigger_lease(
                connection,
                trigger_payload,
            )
            source_run = None
            if trigger["source_table"] == "hypothesis_evaluations":
                source_run = connection.execute(
                    """SELECT r.analysis_to
                         FROM hypothesis_evaluations e
                         JOIN analysis_runs r ON r.run_id=e.run_id
                        WHERE e.id=?""",
                    (trigger["source_row_key"],),
                ).fetchone()
        finally:
            connection.close()
        plan = insight_orchestrator.cadence_plan(
            "trigger",
            local_now=command_context.clock(),
            event_date=trigger["event_date"],
            analysis_to=source_run["analysis_to"] if source_run else None,
        )
    else:
        plan = insight_orchestrator.cadence_plan(
            args.kind,
            local_now=command_context.clock(),
            anchor=args.anchor,
        )
    prepared = prepare_batch(
        command_context,
        args.kind,
        plan,
        trigger,
        adapter_context=adapter_context,
    )
    runs = compute_runs(
        command_context,
        prepared,
        plan,
        adapter_context=adapter_context,
    )
    finalized = finalize(
        command_context,
        prepared,
        plan,
        args.kind,
        trigger,
        trigger_payload,
    )
    return {
        "ok": True,
        "contract_version": insight_orchestrator.ORCHESTRATOR_CONTRACT_VERSION,
        "plan": plan,
        "outcomes": prepared["selection"],
        "freshness": prepared["freshness"],
        "suppressed_dependencies": prepared["suppressed_dependencies"],
        "runs": runs,
        **finalized,
    }

