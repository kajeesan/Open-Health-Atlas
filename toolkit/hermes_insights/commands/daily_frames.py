"""CLI ownership for the legacy daily-frame family."""

from .. import daily_frames, runtime


def _with_connection(context, fn):
    connection = runtime.connect(context.database)
    try:
        return fn(connection)
    finally:
        connection.close()


def build_daily_frame(context, args, *, medication_aliases=None):
    return _with_connection(context, lambda c: daily_frames.build_daily_frame(
        c, args.days, clock=context.clock, timezone=context.timezone,
        medication_aliases=medication_aliases))


def features(context, args, *, medication_aliases=None):
    return _with_connection(context, lambda c: daily_frames.features(
        c, args.days, clock=context.clock, timezone=context.timezone,
        medication_aliases=medication_aliases))


def correlate(context, args, *, medication_aliases=None):
    if args.min_n < 3:
        raise SystemExit("--min-n must be >= 3")
    return _with_connection(context, lambda c: daily_frames.correlate(
        c, args.days, min_n=args.min_n, top=args.top, clock=context.clock,
        timezone=context.timezone, medication_aliases=medication_aliases))


def day_signature(context, args, *, medication_aliases=None):
    if args.min_days < 1:
        raise SystemExit("--min-days must be >= 1")
    return _with_connection(context, lambda c: daily_frames.day_signature(
        c, args.days, min_days=args.min_days, clock=context.clock,
        timezone=context.timezone, medication_aliases=medication_aliases))


def adherence(context, args, *, medication_aliases=None):
    return _with_connection(context, lambda c: daily_frames.adherence(
        c, args.days, clock=context.clock, timezone=context.timezone,
        medication_aliases=medication_aliases))


def summary(context, args):
    return _with_connection(context, lambda c: daily_frames.summary(
        c, args.days, clock=context.clock))


def bp_brief(context, args, *, medication_aliases=None):
    return _with_connection(context, lambda c: daily_frames.bp_brief(
        c, args.days, args.drug, clock=context.clock,
        medication_aliases=medication_aliases))


def data_coverage(context, args, *, medication_aliases=None):
    return _with_connection(context, lambda c: daily_frames.data_coverage(
        c, args.days, clock=context.clock, timezone=context.timezone,
        medication_aliases=medication_aliases))
