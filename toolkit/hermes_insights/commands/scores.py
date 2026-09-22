"""CLI ownership for dashboard scores."""

from .. import runtime, scores as scores_domain


def scores(context, args, *, nutrition_config=None):
    connection = runtime.connect(context.database)
    try:
        return scores_domain.scores(
            connection, args.days, clock=context.clock,
            timezone=context.timezone,
            nutrition_config=nutrition_config,
        )
    finally:
        connection.close()
