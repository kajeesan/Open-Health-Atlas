"""The panel's OWN state database (WebAuthn credentials, sessions, enrollment
tokens, auth audit trail, and health-adjacent panel conversations). The panel
DB is sensitive even though canonical measurements remain in the health DB.

The lint contract permits sqlite3.connect only here and in db_read.py.
"""
import json
import sqlite3
import time

from flask import current_app, g

CHAT_SCHEMA_VERSION = "2"
LEGACY_CONVERSATIONS = {
    "general": ("legacy-general", "General", "hermes-panel"),
    "pain": ("legacy-pain", "Pain", "hermes-panel-pain"),
    "mobility": ("legacy-mobility", "Mobility", "hermes-panel-mobility"),
}

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS credentials (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  credential_id BLOB UNIQUE NOT NULL,
  public_key    BLOB NOT NULL,
  sign_count    INTEGER NOT NULL DEFAULT 0,
  transports    TEXT,
  label         TEXT,
  created_at    INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS sessions (
  token_hash   TEXT PRIMARY KEY,
  created_at   INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL,
  last_seen_at INTEGER,
  revoked      INTEGER NOT NULL DEFAULT 0
)""",
    """CREATE TABLE IF NOT EXISTS enroll_tokens (
  token_hash TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL,
  used       INTEGER NOT NULL DEFAULT 0
)""",
    """CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
)""",
    """CREATE TABLE IF NOT EXISTS chat_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      INTEGER NOT NULL,
  thread  TEXT NOT NULL DEFAULT 'general',
  role    TEXT NOT NULL,
  content TEXT NOT NULL,
  evidence_json TEXT
)""",
    """CREATE TABLE IF NOT EXISTS audit_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     INTEGER NOT NULL,
  event  TEXT NOT NULL,
  detail TEXT
)""",
)

CONVERSATION_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS chat_conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  surface TEXT NOT NULL CHECK(surface='panel'),
  lens TEXT NOT NULL CHECK(lens IN ('general','pain','mobility')),
  selected_regions_json TEXT NOT NULL DEFAULT '[]',
  range_kind TEXT NOT NULL DEFAULT 'all' CHECK(range_kind IN ('all','bounded')),
  range_from TEXT,
  range_to TEXT,
  archived_at INTEGER,
  hermes_session_id TEXT NOT NULL UNIQUE,
  source_context_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  busy_turn_id TEXT,
  busy_until INTEGER,
  CHECK((range_kind='all' AND range_from IS NULL AND range_to IS NULL)
     OR (range_kind='bounded' AND range_from IS NOT NULL AND range_to IS NOT NULL))
)""",
    """CREATE INDEX IF NOT EXISTS chat_conversations_active_idx
  ON chat_conversations(archived_at, updated_at DESC)""",
    """CREATE INDEX IF NOT EXISTS chat_conversations_lens_idx
  ON chat_conversations(lens, archived_at, updated_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_chat_log_conversation_id_id
  ON chat_log(conversation_id, id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_log_conversation_turn_role
  ON chat_log(conversation_id, turn_id, role) WHERE turn_id IS NOT NULL""",
)


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _panel_context(lens, conversation_id):
    return {
        "version": 1,
        "surface": "panel",
        "lens": lens,
        "conversation_id": conversation_id,
        "range": {"kind": "all"},
        "selected_region_ids": [],
        "evidence_contract": "health-tool-v1",
    }


def _legacy_message_context(lens, conversation_id):
    return {
        "contract": "legacy-panel-context-v1",
        "conversation_id": conversation_id,
        "lens": lens,
        "range": {"kind": "legacy_no_explicit_range"},
        "selected_region_ids": [],
    }


def init_db(app) -> None:
    """Create and migrate panel-owned state in one short idempotent transaction."""
    con = sqlite3.connect(app.config["PANEL_DB"])
    try:
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA_STATEMENTS:
            con.execute(statement)

        cols = {row[1] for row in con.execute("PRAGMA table_info(chat_log)")}
        if "thread" not in cols:
            con.execute("ALTER TABLE chat_log ADD COLUMN thread TEXT NOT NULL DEFAULT 'general'")
        for name in (
            "conversation_id", "turn_id", "context_json", "delivery_status",
            "evidence_json",
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE chat_log ADD COLUMN {name} TEXT")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_log_thread_id ON chat_log(thread, id)"
        )
        for statement in CONVERSATION_STATEMENTS:
            con.execute(statement)

        now_ms = time.time_ns() // 1_000_000
        for offset, (
            thread,
            (conversation_id, title, session_id),
        ) in enumerate(LEGACY_CONVERSATIONS.items()):
            bounds = con.execute(
                "SELECT MIN(ts),MAX(ts) FROM chat_log WHERE thread=?", (thread,)
            ).fetchone()
            # A fresh installation is genuinely empty. Stable legacy shells
            # are created only when there are legacy rows to preserve.
            if bounds[0] is None:
                continue
            created_at = bounds[0] * 1000
            updated_at = bounds[1] * 1000 + offset
            source_context = _canonical_json(_panel_context(thread, conversation_id))
            con.execute(
                """INSERT OR IGNORE INTO chat_conversations(
                     id,title,surface,lens,selected_regions_json,range_kind,
                     range_from,range_to,archived_at,hermes_session_id,
                     source_context_json,created_at,updated_at,busy_turn_id,busy_until
                   ) VALUES(?,?, 'panel', ?, '[]', 'all', NULL, NULL, NULL, ?,
                            ?, ?, ?, NULL, NULL)""",
                (
                    conversation_id, title, thread, session_id, source_context,
                    created_at, updated_at,
                ),
            )
            legacy_context = _canonical_json(
                _legacy_message_context(thread, conversation_id)
            )
            con.execute(
                """UPDATE chat_log
                      SET conversation_id=?,context_json=?,delivery_status='legacy'
                    WHERE conversation_id IS NULL AND thread=?""",
                (conversation_id, legacy_context, thread),
            )

        # Old code accepted only these three thread values. Fail-safe any
        # unexpected historical value into General without changing its
        # original thread column, which remains retained indefinitely.
        unknown = con.execute(
            "SELECT MIN(ts),MAX(ts) FROM chat_log WHERE conversation_id IS NULL"
        ).fetchone()
        if unknown[0] is not None:
            legacy_context = _canonical_json(
                _legacy_message_context("general", "legacy-general")
            )
            source_context = _canonical_json(
                _panel_context("general", "legacy-general")
            )
            con.execute(
                """INSERT OR IGNORE INTO chat_conversations(
                     id,title,surface,lens,selected_regions_json,range_kind,
                     range_from,range_to,archived_at,hermes_session_id,
                     source_context_json,created_at,updated_at,busy_turn_id,busy_until
                   ) VALUES('legacy-general','General','panel','general','[]','all',
                            NULL,NULL,NULL,'hermes-panel',?,?,?,NULL,NULL)""",
                (source_context, unknown[0] * 1000, unknown[1] * 1000),
            )
            con.execute(
                """UPDATE chat_log
                      SET conversation_id='legacy-general',context_json=?,
                          delivery_status='legacy'
                    WHERE conversation_id IS NULL""",
                (legacy_context,),
            )
        con.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES('chat_schema_version',?)",
            (CHAT_SCHEMA_VERSION,),
        )
        recorded = con.execute(
            "SELECT value FROM settings WHERE key='chat_schema_version'"
        ).fetchone()
        if recorded is not None and recorded[0] == "1":
            con.execute(
                "UPDATE settings SET value=? WHERE key='chat_schema_version'",
                (CHAT_SCHEMA_VERSION,),
            )
            recorded = (CHAT_SCHEMA_VERSION,)
        if recorded is None or recorded[0] != CHAT_SCHEMA_VERSION:
            raise RuntimeError("unsupported panel chat schema version")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def get_db() -> sqlite3.Connection:
    """Per-request connection, closed by the teardown registered in the factory."""
    if "panel_db" not in g:
        con = sqlite3.connect(current_app.config["PANEL_DB"])
        con.execute("PRAGMA busy_timeout=5000")
        con.row_factory = sqlite3.Row
        g.panel_db = con
    return g.panel_db


def close_db(_exc=None) -> None:
    con = g.pop("panel_db", None)
    if con is not None:
        con.close()
