"""Ownership of the retained schema and read-only SQL commands."""

import re

from .. import migrations, runtime


SAFE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


def query(context, args):
    """Run one read-only SELECT and return its rows in the legacy envelope."""
    if not SAFE.match(args.sql) or ";" in args.sql.rstrip(";"):
        raise SystemExit("only single read-only SELECT statements are allowed")
    connection = runtime.connect_read_only(context.database)
    try:
        rows = [dict(row) for row in connection.execute(args.sql).fetchall()]
    finally:
        connection.close()
    return {"rows": rows, "count": len(rows)}


def schema(context, args):
    """List tables/views or the columns of one known table/view."""
    connection = runtime.connect(context.database)
    try:
        known = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY type DESC, name"
            )
        ]
        if args.table:
            if args.table not in known:
                raise SystemExit(f"no such table/view: {args.table}")
            return {
                "table": args.table,
                "columns": [
                    {"name": row[1], "type": row[2]}
                    for row in connection.execute(
                        f"PRAGMA table_info('{args.table}')"
                    )
                ],
            }
        return {
            "tables_and_views": {
                table: [
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info('{table}')")
                ]
                for table in known
            }
        }
    finally:
        connection.close()


def schema_status_cmd(context, args):
    """Return migration status without changing the canonical database."""
    return migrations.schema_status(context.database)


def schema_plan_cmd(context, args):
    """Return a migration plan without changing the canonical database."""
    return migrations.schema_plan(context.database, args.to)


def migrate_cmd(context, args):
    """Apply the explicitly requested migration transition."""
    return migrations.migrate(context.database, args.to, args.expected_from)
