"""Governed training retrieval and explicit, effective-dated context capture."""

from datetime import date, timedelta

from .. import runtime, training_context, training_evidence
from ..catalogs import PAIN_CAUSE_MAP
from ..command_context import CommandContext


def evidence(context: CommandContext, *, start=None, end=None, region="knee", side=None,
             adapter_context, catalog, ratio_seeds, e1rm):
    """Read one bounded packet using a read-only snapshot of canonical records."""
    if not isinstance(region, str) or region not in {"knee", *PAIN_CAUSE_MAP}:
        raise training_context.TrainingError("unknown_pain_region")
    end = end if end is not None else runtime.today(clock=context.clock)
    if start is None:
        try:
            start = (date.fromisoformat(end) - timedelta(days=119)).isoformat()
        except (TypeError, ValueError, OverflowError) as exc:
            raise training_context.TrainingError("invalid_evidence_range") from exc
    connection = runtime.connect_read_only(context.database)
    try:
        connection.execute("BEGIN")
        return training_evidence.build(
            connection, start=start, end=end, region=region, side=side,
            context=adapter_context, catalog=catalog, ratio_seeds=ratio_seeds, e1rm=e1rm,
        )
    finally:
        connection.close()


def capture(context: CommandContext, *, capture_id, subject_kind, subject_key, context_key,
            effective_from, effective_to=None, status="answered", value=None, supersedes_id=None):
    """Atomically persist one validated answer, skip or correction; retry IDs are immutable."""
    connection = runtime.connect(context.database)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            return training_context.capture_context(
                connection, capture_id=capture_id, subject_kind=subject_kind,
                subject_key=subject_key, context_key=context_key,
                effective_from=effective_from, effective_to=effective_to,
                status=status, value=value, supersedes_id=supersedes_id,
            )
    finally:
        connection.close()
