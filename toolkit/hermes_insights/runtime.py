"""Database, clock, and adapter bindings shared by read-only entry points.

The runtime owns no command handlers. Callers can supply a database path and
clock; the CLI supplies its compatibility bindings without making independent
Hermes consumers import the CLI.
"""

from datetime import datetime, timedelta
from functools import partial
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from . import calculations, catalogs, migrations
from .contracts import AdapterContext
from .settings import CANON_TZ, TIMEZONE_NAME


DATA_DIR = os.environ.get(
    "HERMES_DATA_DIR", os.path.join(os.path.expanduser("~"), ".local", "share", "hermes")
)
DB = os.environ.get("HEALTH_DB", os.path.join(DATA_DIR, "health.db"))


def now(*, datetime_type=datetime, timezone=CANON_TZ):
    return datetime_type.now(timezone)


def today(*, clock=now):
    return clock().date().isoformat()


def days_ago(n, *, clock=now):
    """ISO date n days before today in the deployment's civil timezone."""
    return (clock().date() - timedelta(days=int(n))).isoformat()


def connect(database):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


# Read-only connections enforce the same three layers as the CLI query path:
# read-only open, query_only, then a closed SELECT/metadata authorizer.
RO_ACTIONS = {
    getattr(sqlite3, "SQLITE_READ", 20), getattr(sqlite3, "SQLITE_SELECT", 21),
    getattr(sqlite3, "SQLITE_FUNCTION", 31), getattr(sqlite3, "SQLITE_RECURSIVE", 33),
    # Explicit read transactions freeze one coherent analytical snapshot.
    # mode=ro + query_only still reject every DDL/DML action.
    getattr(sqlite3, "SQLITE_TRANSACTION", 22),
}
RO_SCHEMA_PRAGMAS = {
    "table_info", "index_list", "index_info", "foreign_key_list",
}


def read_only_authorizer(action, a1, a2, _db, _trig, *, actions=RO_ACTIONS,
                         schema_pragmas=RO_SCHEMA_PRAGMAS):
    # Migration validation needs this closed set of read-only metadata PRAGMAs.
    allowed = action in actions or (
        action == getattr(sqlite3, "SQLITE_PRAGMA", 19)
        and (
            a1 in schema_pragmas
            or (a1 == "foreign_keys" and a2 is None)
        )
    )
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def connect_read_only(database, *, authorizer=read_only_authorizer):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA query_only=ON")
    connection.set_authorizer(authorizer)
    return connection


def require_analytical_schema(database):
    """Accept v3 and every compatible additive later analytical schema."""
    status = migrations.schema_status(database)
    if status["current_version"] not in range(
        3, migrations.AUTONOMOUS_SCHEMA_VERSION + 1,
    ):
        raise migrations.SchemaError(
            "schema_migration_required",
            "outcome-v1 requires schema version 3 through "
            f"{migrations.AUTONOMOUS_SCHEMA_VERSION}",
        )
    return status


_CONTEXT_CONSTANTS = (
    "CATALOG", "RATIO_SEED", "ATHLETIC_AXES", "MOBILITY_NORM",
    "PAIN_CAUSE_MAP", "SELF_TEST_CATALOG", "REHAB_CATALOG", "MICRO_SEED",
    "RUN_TYPE_KEYS", "MUSCLE_GROUP_AXES", "DM_METRICS", "DM_PRIMARY",
    "MEDICATION_ALIASES", "KIND_BETTER",
)
_CONTEXT_FUNCTIONS = {
    "e1rm": "e1rm",
    "group_weight_maps": "_group_weight_maps",
    "basis_weights": "_basis_weights",
    "dev_score": "_dev_score",
    "norm_lab_unit": "_norm_lab_unit",
    "lab_in_range": "_lab_in_range",
    "nutrition_micro_targets": "_micro_targets",
    "circ_diff_min": "_circ_diff_min",
    "start_hhmm": "_start_hhmm",
    "hhmm_min": "_hhmm_min",
    "tested_ratios": "_tested_ratios",
    "axis_score": "_axis_score",
}


def adapter_context(*, clock=now, bindings=None, timezone=None):
    """Bind adapters to shared formulas, catalogs, and one caller-owned clock.

    ``bindings`` supplies optional replacements by their established toolkit
    names. Only the declared adapter bindings are read, so a CLI compatibility
    namespace cannot introduce additional capabilities into the context.
    """
    overrides = bindings if bindings is not None else {}
    constants = {
        name: overrides.get(name, getattr(catalogs, name))
        for name in _CONTEXT_CONSTANTS
    }
    latest_tests = partial(
        calculations._latest_tests,
        today=partial(today, clock=clock), days_ago=partial(days_ago, clock=clock),
    )
    defaults = {
        **vars(calculations),
        "_tested_ratios": partial(calculations._tested_ratios, latest_tests=latest_tests),
    }
    if timezone is not None:
        defaults["_start_hhmm"] = partial(calculations._start_hhmm,
                                          timezone=ZoneInfo(timezone))
    functions = {"now": clock}
    functions.update({
        exposed: overrides.get(name, defaults[name])
        for exposed, name in _CONTEXT_FUNCTIONS.items()
    })
    return AdapterContext(
        today=clock().date(), timezone=TIMEZONE_NAME if timezone is None else timezone,
        constants=constants, functions=functions,
    )
