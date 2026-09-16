"""Small, environment-backed product settings shared by the toolkit.

Personal deployment values stay outside the source tree.  Import-time
validation is intentional: a misspelled IANA timezone must fail before a
write or analysis can be assigned to the wrong civil day.
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "UTC"
TIMEZONE_NAME = os.environ.get("HERMES_TIMEZONE", DEFAULT_TIMEZONE).strip()

if not TIMEZONE_NAME:
    raise RuntimeError("HERMES_TIMEZONE must not be empty")

try:
    CANON_TZ = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError("HERMES_TIMEZONE must name an installed IANA timezone") from exc


def resolve_vault_root(database=None, *, environ=None):
    """Resolve the shared vault location without inspecting or creating files.

    Explicit vault settings win over the standard data-directory layout.
    Legacy callers that supply only a database keep their DB-adjacent vault,
    including callers that rebind the database path in Python.
    """
    values = os.environ if environ is None else environ
    for key in ("HEALTH_VAULT", "VAULT_DIR"):
        if values.get(key):
            return os.path.abspath(os.path.expanduser(values[key]))

    if values.get("HERMES_DATA_DIR"):
        return os.path.abspath(os.path.expanduser(
            os.path.join(values["HERMES_DATA_DIR"], "vault")))

    default_data_dir = os.path.join(
        os.path.expanduser("~"), ".local", "share", "hermes",
    )
    default_database = os.path.join(default_data_dir, "health.db")
    selected_database = (
        database if database is not None
        else values.get("HEALTH_DB", default_database)
    )
    selected_database = os.path.abspath(os.path.expanduser(selected_database))
    if values.get("HEALTH_DB") or selected_database != default_database:
        return os.path.dirname(selected_database)
    return os.path.join(default_data_dir, "vault")
