"""Command coordination for durable analysis ledgers and hypotheses."""

import json

from . import analytical
from .. import associations as insight_associations
from .. import events as insight_events
from .. import goals as insight_goals
from .. import ledger as insight_ledger
from .. import migrations as insight_migrations
from .. import orchestrator as insight_orchestrator
from .. import provenance as insight_provenance
from .. import registry as insight_registry
from .. import runtime
from . import scheduled_analysis


_PHASE5_BASE_OUTCOMES = (
    "subjective.day_rating",
    "subjective.energy",
    "subjective.focus",
    "subjective.mood",
    "adherence.word_kept",
)
_PHASE5_ANNOTATION_KEYS = {
    "annotation_id",
    "hypothesis_id",
    "evaluation_id",
    "annotation_kind",
    "content",
    "source",
    "synthesis_id",
    "context_version",
    "prompt_sha256",
    "model_id",
    "provider",
    "supersedes_id",
    "input_sha256",
}


require_phase5_schema = analytical.require_phase5_schema


def range_record(requested):
    if requested.kind == "all":
        return {
            "range_role": "primary",
            "requested_range_kind": "all",
            "requested_from": None,
            "requested_to": None,
        }
    return {
        "range_role": "primary",
        "requested_range_kind": "bounded",
        "requested_from": requested.start.isoformat(),
        "requested_to": requested.end.isoformat(),
    }


def anchor(command_context, requested, explicit=None):
    if explicit is not None:
        return insight_events.iso_date(explicit, "--anchor")
    if requested.end is not None:
        return requested.end.isoformat()
    return runtime.today(clock=command_context.clock)


def outcome_modes(outcome_key):
    return (
        ("ordinal", "green-vs-non-green", "red-vs-non-red")
        if outcome_key == "subjective.day_rating"
        else ("ordinal",)
    )


def selected_outcomes(connection, explicit):
    if explicit:
        if len(explicit) > 64:
            raise insight_ledger.LedgerError(
                "validation_error",
                "--outcome may be repeated at most 64 times",
                validation=True,
            )
        selected = set(explicit)
        selection = "explicit_set"
    else:
        selected = set(_PHASE5_BASE_OUTCOMES)
        goals = insight_goals.list_goals(
            connection,
            include_disabled=False,
        )["goals"]
        selected.update(
            goal["outcome_key"]
            for goal in goals
            if goal["enabled"] and goal["outcome_key"] is not None
        )
        selection = "base_and_enabled"
    for outcome_key in selected:
        insight_associations.validate_options(
            outcome_key=outcome_key,
            mode="all",
            min_n=insight_associations.DEFAULT_MIN_N,
            interactions="pairwise",
            top=100,
        )
    return sorted(selected), selection


def compute_manual(
    command_context,
    requested,
    explicit_outcomes,
    explicit_mode=None,
    *,
    adapter_context=None,
):
    """Compute every selected outcome/mode before opening a writer."""

    require_phase5_schema(command_context)
    bound_context = (
        adapter_context
        if adapter_context is not None
        else analytical.adapter_context(command_context)
    )
    connection = runtime.connect_read_only(command_context.database)
    try:
        connection.execute("BEGIN")
        insight_migrations.require_version(connection, 4)
        definitions = analytical.definitions(connection, bound_context)
        outcomes, selection = selected_outcomes(connection, explicit_outcomes)
        if explicit_mode is not None:
            if len(outcomes) != 1:
                raise insight_ledger.LedgerError(
                    "validation_error",
                    "--mode requires exactly one explicit --outcome",
                    validation=True,
                )
            if explicit_mode not in outcome_modes(outcomes[0]):
                raise insight_ledger.LedgerError(
                    "validation_error",
                    "--mode is not supported for the selected outcome",
                    validation=True,
                )
        registry_hash = (
            "sha256:" + insight_registry.registry_content_checksum(definitions)
        )
        verified = []
        for outcome_key in outcomes:
            modes = (
                (explicit_mode,)
                if explicit_mode is not None
                else outcome_modes(outcome_key)
            )
            for selected_mode in modes:
                verified.append(
                    (
                        outcome_key,
                        selected_mode,
                        insight_ledger.compute_verified_analysis(
                            connection,
                            definitions,
                            requested,
                            bound_context,
                            outcome_key=outcome_key,
                            outcome_mode=selected_mode,
                        ),
                    )
                )
    finally:
        connection.close()
    return outcomes, selection, registry_hash, verified


def input_bound_initiator(prefix, verified):
    """Bind a batch identity to every per-outcome input fingerprint."""

    inputs = sorted(
        [
            {
                "outcome_key": outcome_key,
                "outcome_mode": outcome_mode,
                "input_fingerprint": result.payload["meta"]["input_fingerprint"],
            }
            for outcome_key, outcome_mode, result in verified
        ],
        key=lambda item: (
            item["outcome_key"],
            item["outcome_mode"],
            item["input_fingerprint"],
        ),
    )
    return (
        f"{prefix}/"
        + insight_provenance.sha256_id(
            {
                "contract_version": insight_ledger.LEDGER_CONTRACT_VERSION,
                "kind": "analysis_batch_inputs",
                "inputs": inputs,
            }
        )
    )


def analysis_refresh(
    command_context,
    args,
    *,
    stdin=None,
    adapter_context=None,
):
    """Refresh a manual ledger or dispatch a scheduled analysis run."""

    if args.kind != "manual":
        if (
            args.outcome
            or getattr(args, "mode", None) is not None
            or args.from_date is not None
            or args.to_date is not None
            or args.days is not None
            or args.all_dates
        ):
            raise insight_orchestrator.OrchestrationError(
                "validation_error",
                "scheduled analysis-refresh does not accept caller range/outcome overrides",
                validation=True,
            )
        if args.kind == "trigger":
            if not args.stdin or args.anchor is not None:
                raise insight_orchestrator.OrchestrationError(
                    "validation_error",
                    "trigger analysis-refresh requires only --kind trigger --stdin",
                    validation=True,
                )
        elif args.stdin:
            raise insight_orchestrator.OrchestrationError(
                "validation_error",
                "--stdin is valid only for trigger analysis-refresh",
                validation=True,
            )
        return scheduled_analysis.analysis_refresh(
            command_context,
            args,
            stdin=stdin,
            adapter_context=adapter_context,
        )
    if args.stdin:
        raise insight_orchestrator.OrchestrationError(
            "validation_error",
            "manual analysis-refresh does not accept --stdin",
            validation=True,
        )
    explicit_mode = getattr(args, "mode", None)
    if explicit_mode is not None:
        if not args.outcome or len(args.outcome) != 1:
            raise insight_ledger.LedgerError(
                "validation_error",
                "--mode requires exactly one explicit --outcome",
                validation=True,
            )
        if explicit_mode not in outcome_modes(args.outcome[0]):
            raise insight_ledger.LedgerError(
                "validation_error",
                "--mode is not supported for the selected outcome",
                validation=True,
            )
    requested = analytical.requested_range(args)
    batch_anchor = anchor(command_context, requested, args.anchor)
    outcomes, selection, registry_hash, verified = compute_manual(
        command_context,
        requested,
        args.outcome,
        explicit_mode,
        adapter_context=adapter_context,
    )
    outcome_modes_payload = [
        {"outcome_key": outcome_key, "outcome_mode": outcome_mode}
        for outcome_key, outcome_mode, _result in verified
    ]
    range_payload = range_record(requested)
    connection = runtime.connect(command_context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        batch = insight_ledger.create_analysis_batch(
            connection,
            run_kind="manual",
            anchor_date=batch_anchor,
            initiator_key=input_bound_initiator(
                "manual-analysis-refresh",
                verified,
            ),
            outcome_selection=selection,
            outcomes=outcome_modes_payload,
            ranges=[range_payload],
            registry_sha256_value=registry_hash,
        )
        primary = next(
            item
            for item in batch["ranges"]
            if item["range_role"] == "primary"
        )
        runs = []
        for outcome_key, selected_mode, result in verified:
            run = insight_ledger.start_analysis_run(
                connection,
                batch_id=batch["batch_id"],
                range_id=primary["range_id"],
                outcome_key=outcome_key,
                outcome_mode=selected_mode,
                batch_outcomes=outcome_modes_payload,
            )
            runs.append(
                insight_ledger.persist_analysis_run(
                    connection,
                    run_id=run["run_id"],
                    verified=result,
                )
            )
        completed = insight_ledger.finalize_analysis_batch(
            connection,
            batch_id=batch["batch_id"],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "ok": True,
        "contract_version": insight_ledger.LEDGER_CONTRACT_VERSION,
        "batch": completed,
        "outcomes": outcomes,
        "runs": runs,
    }


def hypothesis_promote(
    command_context,
    args,
    *,
    adapter_context=None,
):
    """Recompute and explicitly promote one verified finding."""

    insight_associations.validate_options(
        outcome_key=args.outcome,
        mode="all",
        min_n=insight_associations.DEFAULT_MIN_N,
        interactions="pairwise",
        top=100,
    )
    insight_associations.validate_sha256_id(args.finding_id, "finding-id")
    insight_associations.validate_sha256_id(
        args.input_fingerprint,
        "input-fingerprint",
    )
    range_forms = (
        int(args.from_date is not None or args.to_date is not None)
        + int(args.days is not None)
        + int(args.all_dates)
    )
    if range_forms != 1:
        raise insight_ledger.LedgerError(
            "validation_error",
            "hypothesis-promote requires exactly one explicit range",
            validation=True,
        )
    requested = analytical.requested_range(args)
    batch_anchor = anchor(command_context, requested)
    require_phase5_schema(command_context)
    bound_context = (
        adapter_context
        if adapter_context is not None
        else analytical.adapter_context(command_context)
    )
    connection = runtime.connect(command_context.database)
    try:
        # Recompute, persist if absent, and promote from one locked snapshot.
        # A concurrent capture cannot make a verified browser finding stale in
        # the gap between the replay and the ledger write.
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        definitions = analytical.definitions(connection, bound_context)
        replay = insight_ledger.compute_verified_finding(
            connection,
            definitions,
            requested,
            bound_context,
            outcome_key=args.outcome,
            finding_id_value=args.finding_id,
            input_fingerprint_value=args.input_fingerprint,
        )
        finding = replay.payload["finding"]
        selected_mode = finding["outcome"]["mode"]
        persisted = connection.execute(
            "SELECT run_id FROM analysis_findings WHERE finding_id=?",
            (finding["finding_id"],),
        ).fetchone()
        if persisted is None:
            analysis = insight_ledger.compute_verified_analysis(
                connection,
                definitions,
                requested,
                bound_context,
                outcome_key=args.outcome,
                outcome_mode=selected_mode,
            )
            matching = next(
                (
                    item
                    for item in analysis.payload["findings"]
                    if item["finding_id"] == finding["finding_id"]
                ),
                None,
            )
            if (
                matching is None
                or matching["provenance"]["evidence_fingerprint"]
                != finding["provenance"]["evidence_fingerprint"]
            ):
                raise insight_ledger.LedgerError(
                    "stale_finding",
                    "replayed finding changed before persistence",
                )
            meta = analysis.payload["meta"]
            batch = insight_ledger.create_analysis_batch(
                connection,
                run_kind="manual",
                anchor_date=batch_anchor,
                initiator_key=input_bound_initiator(
                    "hypothesis-promote",
                    [(args.outcome, selected_mode, analysis)],
                ),
                outcome_selection="explicit_set",
                outcomes=[(args.outcome, selected_mode)],
                ranges=[range_record(requested)],
                registry_sha256_value=meta["registry_sha256"],
                analysis_version=meta["analysis_version"],
                registry_version=meta["registry_version"],
                engine_sha256_value=meta["engine_sha256"],
            )
            primary = next(
                item
                for item in batch["ranges"]
                if item["range_role"] == "primary"
            )
            run = insight_ledger.start_analysis_run(
                connection,
                batch_id=batch["batch_id"],
                range_id=primary["range_id"],
                outcome_key=args.outcome,
                outcome_mode=selected_mode,
                batch_outcomes=[(args.outcome, selected_mode)],
            )
            persisted_run = insight_ledger.persist_analysis_run(
                connection,
                run_id=run["run_id"],
                verified=analysis,
            )
            insight_ledger.finalize_analysis_batch(
                connection,
                batch_id=batch["batch_id"],
            )
            run_id = persisted_run["run_id"]
        else:
            run_id = persisted["run_id"]
        result = insight_ledger.promote_verified_finding(
            connection,
            run_id=run_id,
            verified=replay,
            explicit=True,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return result


def hypothesis_refresh(command_context, args):
    """Refresh hypothesis states for one completed analysis batch."""

    insight_associations.validate_sha256_id(args.batch_id, "batch-id")
    require_phase5_schema(command_context)
    connection = runtime.connect(command_context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        result = insight_ledger.refresh_batch_hypotheses(
            connection,
            batch_id=args.batch_id,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return result


def annotation_payload(stdin):
    text = stdin.read()
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise insight_ledger.LedgerError(
            "validation_error",
            "stdin must be valid UTF-8",
            validation=True,
        ) from exc
    if not encoded or len(encoded) > 32_768:
        raise insight_ledger.LedgerError(
            "validation_error",
            "stdin must contain 1-32768 UTF-8 bytes",
            validation=True,
        )

    def closed_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise insight_ledger.LedgerError(
                    "validation_error",
                    f"duplicate JSON key is not allowed: {key}",
                    validation=True,
                )
            result[key] = value
        return result

    try:
        payload = json.loads(text, object_pairs_hook=closed_object)
    except insight_ledger.LedgerError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        detail = (
            exc.msg
            if isinstance(exc, json.JSONDecodeError)
            else "JSON nesting exceeds the supported bound"
            if isinstance(exc, RecursionError)
            else "numeric literal exceeds the supported bound"
        )
        raise insight_ledger.LedgerError(
            "validation_error",
            f"stdin is not valid JSON: {detail}",
            validation=True,
        ) from exc
    if isinstance(payload, dict):
        for key, value in payload.items():
            for text_value in (key, value):
                if isinstance(text_value, str):
                    try:
                        text_value.encode("utf-8")
                    except UnicodeEncodeError as exc:
                        raise insight_ledger.LedgerError(
                            "validation_error",
                            "annotation strings must be valid UTF-8",
                            validation=True,
                        ) from exc
    if not isinstance(payload, dict) or set(payload) != _PHASE5_ANNOTATION_KEYS:
        raise insight_ledger.LedgerError(
            "validation_error",
            "hypothesis annotation fields do not match the closed contract",
            validation=True,
        )
    evaluation_id = payload["evaluation_id"]
    if (
        evaluation_id is not None
        and (
            isinstance(evaluation_id, bool)
            or not isinstance(evaluation_id, int)
            or evaluation_id < 1
            or evaluation_id > insight_ledger.SQLITE_MAX_ROWID
        )
    ):
        raise insight_ledger.LedgerError(
            "validation_error",
            "evaluation_id must be a positive SQLite row identifier",
            validation=True,
        )
    return payload


def hypothesis_annotate(command_context, args, *, stdin):
    """Append one validated hypothesis annotation from closed JSON stdin."""

    payload = annotation_payload(stdin)
    require_phase5_schema(command_context)
    connection = runtime.connect(command_context.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        insight_migrations.require_version(connection, 4)
        result = insight_ledger.append_annotation(
            connection,
            hypothesis_id=payload["hypothesis_id"],
            evaluation_id=payload["evaluation_id"],
            annotation_kind=payload["annotation_kind"],
            content=payload["content"],
            source=payload["source"],
            synthesis_id=payload["synthesis_id"],
            context_version=payload["context_version"],
            prompt_sha256=payload["prompt_sha256"],
            model_id=payload["model_id"],
            provider=payload["provider"],
            supersedes_id=payload["supersedes_id"],
            annotation_id=payload["annotation_id"],
        )
        if result["input_sha256"] != payload["input_sha256"]:
            raise insight_ledger.LedgerError(
                "mismatched_annotation",
                "input_sha256 does not match the canonical annotation",
                validation=True,
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "ok": True,
        "contract_version": insight_ledger.LEDGER_CONTRACT_VERSION,
        "annotation": result,
    }


def hypotheses(command_context, args):
    """List hypotheses with stable pagination and validation."""

    if not 1 <= args.limit <= 100:
        raise insight_ledger.LedgerError(
            "validation_error",
            "--limit must be between 1 and 100",
            validation=True,
        )
    if args.before is not None:
        insight_associations.validate_sha256_id(args.before, "before")
    if args.outcome is not None:
        insight_associations.validate_options(
            outcome_key=args.outcome,
            mode="all",
            min_n=insight_associations.DEFAULT_MIN_N,
            interactions="pairwise",
            top=100,
        )
    require_phase5_schema(command_context)
    connection = runtime.connect_read_only(command_context.database)
    try:
        return insight_ledger.list_hypotheses(
            connection,
            status=args.status,
            outcome_key=args.outcome,
            limit=args.limit,
            before=args.before,
        )
    finally:
        connection.close()


def hypothesis_brief(command_context, args):
    """Return one fully audited hypothesis and its evidence history."""

    insight_associations.validate_sha256_id(args.hypothesis_id, "hypothesis-id")
    require_phase5_schema(command_context)
    connection = runtime.connect_read_only(command_context.database)
    try:
        return insight_ledger.hypothesis_brief(connection, args.hypothesis_id)
    finally:
        connection.close()

