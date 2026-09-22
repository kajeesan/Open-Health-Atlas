"""Muscle volume/detail reads and body-figure lens dispatch."""

from .. import runtime, muscles, muscle_figure

def muscle_volume(context, a):
    """Return raw-tag volume or the three-source group report."""
    c = runtime.connect(context.database)
    try:
        return muscles.muscle_volume(c, a.source, a.days, a.by, clock=context.clock)
    finally:
        c.close()


def muscle_detail(context, a):
    """Return volume and separately qualified subregion theories."""
    c = runtime.connect(context.database)
    try:
        return muscles.muscle_detail(c, a.group, a.days, clock=context.clock)
    finally:
        c.close()


def muscle_map(context, a, *, quarterly_routines, unilateral_titles, mobility_norm):
    """Project the requested retained lens without changing its window semantics."""
    if a.lens not in {"activation", "strength-balance", "pain", "mobility"}:
        raise SystemExit(f"lens {a.lens!r} is not implemented")
    c = runtime.connect(context.database)
    try:
        if a.lens == "strength-balance":
            return muscle_figure.balance(c, a.side_mode, clock=context.clock)
        if a.lens == "pain":
            return muscle_figure.pain(c, a.days, clock=context.clock)
        if a.lens == "mobility":
            return muscle_figure.mobility(c, a.days, clock=context.clock, mobility_norm=mobility_norm)
        return muscle_figure.activation(
            c, a.days, clock=context.clock, quarterly_routines=quarterly_routines,
            unilateral_titles=unilateral_titles,
        )
    finally:
        c.close()
