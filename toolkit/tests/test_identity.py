"""Phase 2 exact identity normalization and append-only alias revisions."""

import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
sys.path.insert(0, str(ROOT))

from hermes_insights.normalize import identity_key, normalized_label, recipe_key


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "identity.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit(); con.close()
    run(path, "migrate", "--to", "2", "--expected-from", "0")
    return path


def run(db, *args, stdin=None, expect=0):
    result = subprocess.run(
        [sys.executable, str(HEALTH), *args], input=stdin, text=True,
        capture_output=True, timeout=30,
        env={**os.environ, "HEALTH_DB": str(db), "HERMES_CODE_VERSION": "c" * 40},
    )
    assert result.returncode == expect, result.stderr or result.stdout
    return json.loads(result.stdout)


def capture_and_event(db, token, label):
    cap = run(db, "capture-raw", "--stdin", stdin=json.dumps({
        "client_event_id": token, "event_date": "2026-07-21", "event_time": None,
        "surface": "panel", "source": "chat-panel", "raw_text": f"Saw {label}",
    }))
    payload = {
        "date": "2026-07-21", "category": "social", "source": "chat-panel",
        "capture_id": cap["capture_id"], "entity_label": label,
    }
    return run(db, "event-log", "--stdin", stdin=json.dumps(payload))


def table_rows(db, table):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY id")]
    finally:
        con.close()


def test_normalizer_is_only_nfkc_trim_whitespace_and_casefold():
    assert normalized_label(" \tＡlice\u00a0  SMITH \n") == "alice smith"
    expected = hashlib.sha256("alice smith".encode()).hexdigest()
    assert identity_key("person", "Ａlice  SMITH") == f"person:{expected}"
    assert identity_key("person", "Alice-Smith") != identity_key("person", "Alice Smith")
    assert identity_key("person", "Jon") != identity_key("person", "John")
    assert identity_key("food", "Alice") != identity_key("person", "Alice")
    assert recipe_key(" sample-stew-01 ") == "recipe:sample-stew-01"


def test_case_and_whitespace_merge_but_original_labels_are_preserved(db):
    first = capture_and_event(db, "case-1", "  Alice   Smith ")
    second = capture_and_event(db, "case-2", "ＡLICE SMITH")
    assert first["entity_key"] == second["entity_key"]
    assert [row["entity_label"] for row in table_rows(db, "event_exposures")] == [
        "  Alice   Smith ", "ＡLICE SMITH",
    ]


def test_stronger_alias_is_owner_configured_append_only_and_retirable(db):
    canonical = identity_key("person", "Alice Smith")
    created = run(
        db, "entity-alias-set", "person", "Ally", "--canonical", canonical,
        "--label", "Alice Smith",
    )
    aliased = capture_and_event(db, "alias-1", "Ally")
    assert aliased["entity_key"] == canonical
    assert aliased["alias_revision_id"] == created["revision_id"]

    changed = run(
        db, "entity-alias-set", "person", "Ally", "--canonical",
        identity_key("person", "Alice Jones"), "--label", "Alice Jones",
    )
    retired = run(db, "entity-alias-retire", "person", "Ally")
    history = run(db, "entity-alias-history", "person", "Ally")["history"]
    assert [row["active"] for row in history] == [1, 1, 0]
    assert history[1]["supersedes_id"] == created["revision_id"]
    assert history[2]["supersedes_id"] == changed["revision_id"] == retired["revision_id"] - 1

    after = capture_and_event(db, "alias-2", "Ally")
    assert after["entity_key"] == identity_key("person", "Ally")
    assert after["alias_revision_id"] == retired["revision_id"]
    assert table_rows(db, "entity_aliases") == history


def test_alias_validation_rejects_wrong_namespace_and_fuzzy_names_remain_distinct(db):
    wrong = run(
        db, "entity-alias-set", "person", "Ally", "--canonical",
        identity_key("food", "Alice"), "--label", "Alice", expect=2,
    )
    assert wrong["error"]["code"] == "validation_error"
    jon = capture_and_event(db, "fuzzy-1", "Jon")
    john = capture_and_event(db, "fuzzy-2", "John")
    assert jon["entity_key"] != john["entity_key"]
    assert table_rows(db, "entity_aliases") == []
