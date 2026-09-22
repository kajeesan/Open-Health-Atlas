"""Command boundary for governed Recovery/readiness evaluation."""

from datetime import date

from .. import readiness_ancestry, recovery, runtime
from ..importers.common import valid_date


def readiness(context, args):
    anchor = (
        date.fromisoformat(valid_date(args.anchor, "--anchor"))
        if args.anchor is not None
        else date.fromisoformat(runtime.today(clock=context.clock))
    )
    range_start = (
        date.fromisoformat(valid_date(args.from_date, "--from"))
        if args.from_date is not None
        else None
    )
    if range_start is not None and range_start > anchor:
        raise SystemExit("--from must not be after --anchor")
    with readiness_ancestry.verified_readiness_snapshot(
        context.database,
        range_start=range_start,
        anchor=anchor,
        calculation_context=recovery._readiness_calculation_context(),
    ) as (connection, snapshot_attestation):
        return recovery._readiness_result(
            connection,
            anchor=anchor,
            range_start=range_start,
            snapshot_attestation=snapshot_attestation,
            clock=context.clock,
        )
