"""Read-only access to the HEALTH database — the ONLY module allowed to open it.

Independent layers, each sufficient to prevent writes:
1. SQLite URI `mode=ro` — write access refused at open time.
2. `PRAGMA query_only=ON` — the connection rejects every mutating statement.
3. An authorizer that denies all actions except reading/SELECT.
4. The example service additionally bind-mounts the vault read-only, enforced
   by the kernel.

The health DB uses journal_mode=delete. If it is
ever switched to WAL, cross-user read-only access needs the -wal/-shm files —
re-verify the service boundary before deployment.
Writes NEVER happen here or anywhere else in the panel: they go through
validated health.py subcommands via the local broker.
"""
import os
import sqlite3

from flask import current_app, g

_ALLOWED_ACTIONS = {
    getattr(sqlite3, "SQLITE_READ", 20),
    getattr(sqlite3, "SQLITE_SELECT", 21),
    getattr(sqlite3, "SQLITE_FUNCTION", 31),
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _authorizer(action, _arg1, _arg2, _db_name, _trigger):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def available() -> bool:
    path = current_app.config.get("HEALTH_DB")
    return bool(path) and os.path.exists(path)


def get_health_db() -> sqlite3.Connection:
    """Per-request read-only connection; closed by the factory's teardown."""
    if "health_db" not in g:
        path = current_app.config["HEALTH_DB"]
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        # Pragmas before the authorizer is installed — it would deny them.
        con.execute("PRAGMA busy_timeout=2000")
        con.execute("PRAGMA query_only=ON")
        con.set_authorizer(_authorizer)
        g.health_db = con
    return g.health_db


def close_health_db(_exc=None) -> None:
    con = g.pop("health_db", None)
    if con is not None:
        con.close()


def init_app(app) -> None:
    """Registers teardown + a JSON handler for health-DB errors.

    Lives here (not the factory) so 'sqlite3' never appears outside the two
    sanctioned modules — the lint test enforces that. A busy lock (health.py
    writing >2s) or a corrupt file must degrade to a JSON 503 the dashboard
    can show per-card, not a generic HTML 500."""
    app.teardown_appcontext(close_health_db)

    @app.errorhandler(sqlite3.Error)
    def _health_db_error(exc):
        app.logger.warning("health DB read failed: %s", exc)
        return {"error": "health database temporarily unavailable"}, 503


def query(sql: str, params=()) -> list[dict]:
    """SELECT-only helper returning a list of dicts."""
    return [dict(r) for r in get_health_db().execute(sql, params).fetchall()]


def columns(table: str) -> list[str]:
    """Column names for a table/view without PRAGMA (which the authorizer denies).
    A zero-row SELECT still populates cursor.description. Caller must have
    validated `table` against the live schema."""
    cur = get_health_db().execute(f"SELECT * FROM '{table}' LIMIT 0")
    return [d[0] for d in cur.description]


def snapshot(dst_path: str) -> None:
    """Consistent full-DB copy via the sqlite backup API, from a read-only
    source connection. The ONLY place besides get_health_db that opens the DB;
    the lint test keeps sqlite3 confined to the sanctioned modules."""
    src = sqlite3.connect(f"file:{current_app.config['HEALTH_DB']}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
