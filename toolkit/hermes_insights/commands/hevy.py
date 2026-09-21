"""Hevy CSV transaction coordination."""

from .. import migrations, runtime
from ..command_context import CommandContext
from ..importers.hevy_csv import replace_history


def import_csv(context: CommandContext, path, *, parse_number):
    """Replace imported Hevy history atomically, preserving manually logged sets.

    Schema, file and row errors propagate to the caller after rollback.
    """
    connection = runtime.connect(context.database)
    try:
        with connection:
            migrations.require_table(connection, "hevy_sets", ("source",))
            result = replace_history(connection, path, parse_number=parse_number)
        return result
    finally:
        connection.close()
