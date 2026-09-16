"""Phase 7 scoped panel conversations, migration, range and UI safety."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import auth as auth_mod
from app import bridge, create_app
from app.canon import CanonicalRangeError, canonical_range, range_bounds
from app.panel_db import CHAT_SCHEMA_VERSION, init_db
from tests.support import csrf_from


def _app(tmp_path, monkeypatch, runner=None):
    calls = []

    def default(subcmd, *args, **kwargs):
        calls.append((subcmd, args, kwargs))
        return {"ok": True, "reply": "Scoped reply"}

    monkeypatch.setattr(bridge, "run", runner or default)
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    with app.app_context():
        token = auth_mod.create_session()
    return app, token, calls


def _client(app, token):
    client = app.test_client()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def _context(kind="all"):
    range_value = {"kind": "all"} if kind == "all" else {
        "kind": "bounded", "from": "2026-07-01", "to": "2026-07-23",
    }
    return {"version": 1, "range": range_value, "selected_region_ids": []}


def _create(client, lens="general", context=None):
    response = client.post("/api/chat/conversations", json={
        "lens": lens, "context": context or _context(),
    })
    assert response.status_code == 201
    return response.get_json()["conversation"]


def _send(client, conversation, text="hello", turn_id=None, selected_findings=None):
    body = {
        "message": text,
        "turn_id": turn_id or str(uuid.uuid4()),
        "context": conversation["context"],
    }
    if selected_findings is not None:
        body["selected_findings"] = selected_findings
    return client.post(
        f"/api/chat/conversations/{conversation['id']}/send",
        json=body,
    )


def _schema_snapshot(path):
    connection = sqlite3.connect(path)
    try:
        return {
            "objects": connection.execute(
                """SELECT type,name,tbl_name,sql FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"""
            ).fetchall(),
            "conversations": connection.execute(
                "SELECT * FROM chat_conversations ORDER BY id"
            ).fetchall(),
            "messages": connection.execute(
                "SELECT * FROM chat_log ORDER BY id"
            ).fetchall(),
            "settings": connection.execute(
                "SELECT * FROM settings ORDER BY key"
            ).fetchall(),
        }
    finally:
        connection.close()


def test_exact_additive_migration_backfill_is_idempotent_and_preserves_journal(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute(
        """CREATE TABLE chat_log(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             ts INTEGER NOT NULL,
             thread TEXT NOT NULL DEFAULT 'general',
             role TEXT NOT NULL,
             content TEXT NOT NULL
           )"""
    )
    connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    connection.execute(
        "INSERT INTO settings(key,value) VALUES('chat_schema_version','1')"
    )
    originals = [
        (7, 100, "general", "user", "old <message>"),
        (8, 101, "pain", "assistant", "old answer"),
        (9, 102, "mobility", "user", "tight ankle"),
    ]
    connection.executemany(
        "INSERT INTO chat_log(id,ts,thread,role,content) VALUES(?,?,?,?,?)", originals
    )
    connection.commit()
    connection.close()

    app = SimpleNamespace(config={"PANEL_DB": str(path)})
    init_db(app)
    first = _schema_snapshot(path)
    first_bytes = path.read_bytes()
    init_db(app)
    assert _schema_snapshot(path) == first
    assert path.read_bytes() == first_bytes

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert connection.execute(
        "SELECT id,ts,thread,role,content FROM chat_log ORDER BY id"
    ).fetchall() == originals
    columns = {row[1]: row for row in connection.execute("PRAGMA table_info(chat_log)")}
    assert {
        "conversation_id", "turn_id", "context_json", "delivery_status",
        "evidence_json",
    } <= set(columns)
    assert all(columns[name][3] == 0 for name in (
        "conversation_id", "turn_id", "context_json", "delivery_status",
        "evidence_json",
    ))
    assert connection.execute(
        "SELECT value FROM settings WHERE key='chat_schema_version'"
    ).fetchone()[0] == CHAT_SCHEMA_VERSION
    rows = connection.execute(
        """SELECT id,conversation_id,turn_id,context_json,delivery_status
             FROM chat_log ORDER BY id"""
    ).fetchall()
    assert [row[1] for row in rows] == [
        "legacy-general", "legacy-pain", "legacy-mobility",
    ]
    assert all(row[2] is None and row[4] == "legacy" for row in rows)
    assert all(
        json.loads(row[3])["range"]["kind"] == "legacy_no_explicit_range"
        for row in rows
    )
    index_sql = {
        row[0]: row[1] for row in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_chat_log_conversation_id_id" in index_sql
    assert "WHERE turn_id IS NOT NULL" in index_sql[
        "idx_chat_log_conversation_turn_role"
    ]
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    conversation_columns = [
        (row[1], row[2], row[3], row[4], row[5])
        for row in connection.execute("PRAGMA table_info(chat_conversations)")
    ]
    assert conversation_columns == [
        ("id", "TEXT", 0, None, 1),
        ("title", "TEXT", 1, None, 0),
        ("surface", "TEXT", 1, None, 0),
        ("lens", "TEXT", 1, None, 0),
        ("selected_regions_json", "TEXT", 1, "'[]'", 0),
        ("range_kind", "TEXT", 1, "'all'", 0),
        ("range_from", "TEXT", 0, None, 0),
        ("range_to", "TEXT", 0, None, 0),
        ("archived_at", "INTEGER", 0, None, 0),
        ("hermes_session_id", "TEXT", 1, None, 0),
        ("source_context_json", "TEXT", 1, None, 0),
        ("created_at", "INTEGER", 1, None, 0),
        ("updated_at", "INTEGER", 1, None, 0),
        ("busy_turn_id", "TEXT", 0, None, 0),
        ("busy_until", "INTEGER", 0, None, 0),
    ]
    connection.close()


def test_empty_database_has_no_synthetic_legacy_conversations(tmp_path):
    path = tmp_path / "panel.db"
    init_db(SimpleNamespace(config={"PANEL_DB": str(path)}))
    connection = sqlite3.connect(path)
    rows = connection.execute(
        "SELECT id,lens,hermes_session_id FROM chat_conversations ORDER BY id"
    ).fetchall()
    assert rows == []
    connection.close()


def test_conversation_timestamp_cursor_never_skips_legacy_rows(tmp_path, monkeypatch):
    path = tmp_path / "panel.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE chat_log(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             ts INTEGER NOT NULL,
             thread TEXT NOT NULL DEFAULT 'general',
             role TEXT NOT NULL,
             content TEXT NOT NULL
           )"""
    )
    connection.executemany(
        "INSERT INTO chat_log(ts,thread,role,content) VALUES(?,?,?,?)",
        [
            (100, "general", "user", "legacy general"),
            (101, "pain", "assistant", "legacy pain"),
            (102, "mobility", "user", "legacy mobility"),
        ],
    )
    connection.commit()
    connection.close()
    app, token, _ = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    seen = []
    before = None
    while True:
        suffix = f"&before_updated_at={before}" if before else ""
        response = client.get(f"/api/chat/conversations?limit=1{suffix}")
        data = response.get_json()
        seen.extend(item["id"] for item in data["conversations"])
        before = data["next_before_updated_at"]
        if before is None:
            break
    assert set(seen) == {"legacy-general", "legacy-pain", "legacy-mobility"}


def test_two_same_lens_conversations_have_distinct_server_sessions_and_no_telegram(
    tmp_path, monkeypatch,
):
    app, token, _ = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    first = _create(client)
    second = _create(client)
    assert first["id"] != second["id"]
    assert len(first["id"]) == len(second["id"]) == 32
    assert first["id"].isalnum() and first["id"] == first["id"].lower()
    connection = sqlite3.connect(app.config["PANEL_DB"])
    sessions = connection.execute(
        "SELECT hermes_session_id FROM chat_conversations WHERE id IN (?,?)",
        (first["id"], second["id"]),
    ).fetchall()
    assert {row[0] for row in sessions} == {
        f"hermes-panel-c-{first['id']}", f"hermes-panel-c-{second['id']}",
    }
    assert all("telegram" not in row[0].lower() for row in sessions)
    connection.close()
    spoofed = client.post("/api/chat/conversations", json={
        "lens": "general", "context": _context(), "session_id": "hermes-panel",
    })
    assert spoofed.status_code == 400


def test_send_stores_exact_immutable_context_and_server_session(tmp_path, monkeypatch):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    context = {
        "version": 1,
        "range": {"kind": "bounded", "from": "2026-07-01", "to": "2026-07-23"},
        "selected_region_ids": ["knee-left", "hip-flexor-left"],
    }
    conversation = _create(client, lens="pain", context=context)
    response = _send(client, conversation)
    assert response.status_code == 200
    subcommand, args, kwargs = calls[-1]
    assert subcommand == "hermes-chat"
    assert args == ("--session", f"hermes-panel-c-{conversation['id']}")
    envelope = json.loads(kwargs["stdin"])
    assert envelope["context"] == {
        "version": 1,
        "surface": "panel",
        "lens": "pain",
        "conversation_id": conversation["id"],
        "range": context["range"],
        "selected_region_ids": context["selected_region_ids"],
        "evidence_contract": "health-tool-v1",
    }
    client.patch(
        f"/api/chat/conversations/{conversation['id']}",
        json={"context": _context()},
    )
    stored = client.get(
        f"/api/chat/conversations/{conversation['id']}/messages"
    ).get_json()["messages"][0]["context"]
    assert stored == envelope["context"]


def test_ordinary_v1_turn_accepts_explicit_empty_hermes_evidence_lists(
    tmp_path, monkeypatch,
):
    def runner(*_args, **_kwargs):
        return {
            "ok": True,
            "reply": "Ordinary conversational reply",
            "openhealthatlas_tool_trace": [],
            "openhealthatlas_evidence_refs": [],
        }

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    client = _client(app, token)
    conversation = _create(client)
    response = _send(client, conversation)
    assert response.status_code == 200
    assert response.get_json()["evidence"] is None
    messages = client.get(
        f"/api/chat/conversations/{conversation['id']}/messages"
    ).get_json()["messages"]
    assert [message["evidence"] for message in messages] == [None, None]


def test_selected_findings_bind_v2_range_and_persist_separate_evidence(
    tmp_path, monkeypatch,
):
    finding_id = "sha256:" + "d" * 64
    fingerprint = "sha256:" + "e" * 64
    evidence_fingerprint = "sha256:" + "1" * 64
    engine_sha256 = "sha256:" + "2" * 64
    registry_sha256 = "sha256:" + "3" * 64
    adapter_evidence_ids = [
        finding_id,
        evidence_fingerprint,
        fingerprint,
        engine_sha256,
        registry_sha256,
    ]
    prepare_evidence_ids = [
        "sha256:" + format(index, "064x") for index in range(10, 46)
    ]
    result_id = "sha256:" + "f" * 64
    prepare_result_id = "sha256:" + "c" * 64
    calls = []

    def runner(subcmd, *args, **kwargs):
        calls.append((subcmd, args, kwargs))
        return {
            "ok": True,
            "reply": (
                "## Deterministic result\nEvidence.\n\n## Hermes interpretation\n"
                f"Possible pattern supported by {evidence_fingerprint}."
            ),
            "openhealthatlas_evidence_refs": [evidence_fingerprint],
            "openhealthatlas_tool_trace": [{
                "command": [
                    "python3", "/opt/hermes/toolkit/health.py", "finding-evidence",
                    "--outcome", "subjective.day_rating",
                    "--finding-id", finding_id,
                    "--input-fingerprint", fingerprint,
                    "--from", "2026-07-01",
                    "--to", "2026-07-23",
                ],
                "data_class": "fictional",
                "fixture_id": "green-days-actionable-v1",
                "result_sha256": result_id,
                "evidence_ids": adapter_evidence_ids,
                "assessment_state": "assessed",
            }, {
                "command": [
                    "python3", "/opt/hermes/toolkit/health.py",
                    "synthesis-prepare", "--batch-id", "sha256:" + "b" * 64,
                ],
                "data_class": "fictional",
                "fixture_id": "green-days-actionable-v1",
                "result_sha256": prepare_result_id,
                "evidence_ids": prepare_evidence_ids,
                "assessment_state": "assessed",
            }],
        }

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    client = _client(app, token)
    context = {
        "version": 1,
        "range": {"kind": "bounded", "from": "2026-07-01", "to": "2026-07-23"},
        "selected_region_ids": [],
    }
    conversation = _create(client, context=context)
    selected = [{
        "outcome": "subjective.day_rating",
        "finding_id": finding_id,
        "input_fingerprint": fingerprint,
    }]
    turn_id = str(uuid.uuid4())
    response = _send(
        client, conversation, text="What does this mean?", turn_id=turn_id,
        selected_findings=selected,
    )
    assert response.status_code == 200
    subcommand, args, kwargs = calls[-1]
    assert subcommand == "hermes-chat"
    envelope = json.loads(kwargs["stdin"])
    assert envelope["contract"] == "hermes-panel-turn-v2"
    assert envelope["context"]["version"] == 2
    assert envelope["context"]["range"] == context["range"]
    assert envelope["context"]["selected_findings"] == selected
    assert envelope["context"]["evidence_contract"] == "health-tool-v2"

    receipt = response.get_json()["evidence"]
    assert receipt == {
        "contract": "openhealthatlas-chat-evidence-v1",
        "kind": "verified_receipt",
        "data_class": "fictional",
        "fixture_id": "green-days-actionable-v1",
        "range": context["range"],
        "selected_findings": selected,
        "evidence_refs": [evidence_fingerprint],
        "tool_receipts": [{
            "command": "finding-evidence",
            "result_sha256": result_id,
            "evidence_ids": adapter_evidence_ids,
            "assessment_state": "assessed",
        }, {
            "command": "synthesis-prepare",
            "result_sha256": prepare_result_id,
            "evidence_ids": prepare_evidence_ids,
            "assessment_state": "assessed",
        }],
    }
    messages = client.get(
        f"/api/chat/conversations/{conversation['id']}/messages"
    ).get_json()["messages"]
    assert messages[0]["evidence"]["kind"] == "selection"
    assert messages[0]["evidence"]["selected_findings"] == selected
    assert messages[1]["content"].endswith(f"{evidence_fingerprint}.")
    assert messages[1]["evidence"] == receipt

    replay = _send(
        client, conversation, text="What does this mean?", turn_id=turn_id,
        selected_findings=selected,
    )
    assert replay.status_code == 200
    assert replay.get_json()["idempotent"] is True
    assert replay.get_json()["evidence"] == receipt
    assert len(calls) == 1


def test_tool_receipt_evidence_ids_have_a_separate_finite_bound(
    tmp_path, monkeypatch,
):
    too_many_ids = [
        "sha256:" + format(index, "064x") for index in range(1, 258)
    ]

    def runner(*_args, **_kwargs):
        return {
            "ok": True,
            "reply": "Receipt is too large",
            "openhealthatlas_evidence_refs": [],
            "openhealthatlas_tool_trace": [{
                "command": [
                    "python3", "/opt/hermes/toolkit/health.py",
                    "synthesis-prepare", "--batch-id", "sha256:" + "b" * 64,
                ],
                "data_class": "fictional",
                "fixture_id": "green-days-actionable-v1",
                "result_sha256": "sha256:" + "c" * 64,
                "evidence_ids": too_many_ids,
                "assessment_state": "assessed",
            }],
        }

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    client = _client(app, token)
    conversation = _create(client)
    response = _send(client, conversation)
    assert response.status_code == 502
    assert response.get_json()["code"] == "turn_failed"


def test_selected_receipt_citation_must_come_from_its_finding_replay(
    tmp_path, monkeypatch,
):
    selected_id = "sha256:" + "a" * 64
    other_id = "sha256:" + "b" * 64
    fingerprint = "sha256:" + "d" * 64
    evidence_fingerprint = "sha256:" + "e" * 64

    def runner(*_args, **_kwargs):
        return {
            "ok": True,
            "reply": "Untrusted mismatch",
            "openhealthatlas_evidence_refs": [other_id],
            "openhealthatlas_tool_trace": [{
                "command": [
                    "python3", "/opt/hermes/toolkit/health.py", "finding-evidence",
                    "--outcome", "subjective.day_rating",
                    "--finding-id", selected_id,
                    "--input-fingerprint", fingerprint,
                    "--from", "2026-07-01",
                    "--to", "2026-07-23",
                ],
                "data_class": "fictional",
                "fixture_id": "green-days-v1",
                "result_sha256": "sha256:" + "c" * 64,
                "evidence_ids": [selected_id, evidence_fingerprint, fingerprint],
            }, {
                "command": [
                    "python3", "/opt/hermes/toolkit/health.py",
                    "synthesis-prepare", "--batch-id", "sha256:" + "f" * 64,
                ],
                "data_class": "fictional",
                "fixture_id": "green-days-v1",
                "result_sha256": "sha256:" + "1" * 64,
                "evidence_ids": [other_id],
                "assessment_state": "assessed",
            }],
        }

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    client = _client(app, token)
    conversation = _create(client, context={
        "version": 1,
        "range": {"kind": "bounded", "from": "2026-07-01", "to": "2026-07-23"},
        "selected_region_ids": [],
    })
    response = _send(client, conversation, selected_findings=[{
        "outcome": "subjective.day_rating",
        "finding_id": selected_id,
        "input_fingerprint": fingerprint,
    }])
    assert response.status_code == 502
    assert response.get_json()["code"] == "turn_failed"
    messages = client.get(
        f"/api/chat/conversations/{conversation['id']}/messages"
    ).get_json()["messages"]
    assert len(messages) == 1
    assert messages[0]["delivery_status"] == "failed"


@pytest.mark.parametrize("extra_id", [
    "sha256:" + "a" * 64,
    "sha256:" + "b" * 64,
])
def test_selected_receipt_rejects_duplicate_or_extra_finding_replay(
    tmp_path, monkeypatch, extra_id,
):
    selected_id = "sha256:" + "a" * 64
    fingerprint = "sha256:" + "d" * 64

    def command(finding_id):
        return [
            "python3", "/opt/hermes/toolkit/health.py", "finding-evidence",
            "--outcome", "subjective.day_rating",
            "--finding-id", finding_id,
            "--input-fingerprint", fingerprint,
            "--from", "2026-07-01",
            "--to", "2026-07-23",
        ]

    def runner(*_args, **_kwargs):
        return {
            "ok": True,
            "reply": "Untrusted duplicate or extra replay",
            "openhealthatlas_evidence_refs": [selected_id],
            "openhealthatlas_tool_trace": [{
                "command": command(selected_id),
                "data_class": "fictional",
                "fixture_id": "green-days-v1",
                "result_sha256": "sha256:" + "c" * 64,
                "evidence_ids": [selected_id],
            }, {
                "command": command(extra_id),
                "data_class": "fictional",
                "fixture_id": "green-days-v1",
                "result_sha256": "sha256:" + "e" * 64,
                "evidence_ids": [extra_id],
            }],
        }

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    client = _client(app, token)
    conversation = _create(client, context={
        "version": 1,
        "range": {"kind": "bounded", "from": "2026-07-01", "to": "2026-07-23"},
        "selected_region_ids": [],
    })
    response = _send(client, conversation, selected_findings=[{
        "outcome": "subjective.day_rating",
        "finding_id": selected_id,
        "input_fingerprint": fingerprint,
    }])
    assert response.status_code == 502
    assert response.get_json()["code"] == "turn_failed"


def test_send_rejects_stale_browser_context_before_bridge(tmp_path, monkeypatch):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    stale = dict(conversation)
    stale["context"] = {
        "version": 1,
        "range": {"kind": "bounded", "from": "2026-07-01", "to": "2026-07-23"},
        "selected_region_ids": [],
    }
    response = _send(client, stale)
    assert response.status_code == 409
    assert response.get_json()["code"] == "conversation_context_stale"
    assert calls == []


def test_selected_evidence_requires_server_owned_bounded_range(tmp_path, monkeypatch):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    response = _send(client, conversation, selected_findings=[{
        "outcome": "subjective.day_rating",
        "finding_id": "sha256:" + "a" * 64,
        "input_fingerprint": "sha256:" + "b" * 64,
    }])
    assert response.status_code == 400
    assert response.get_json()["code"] == "selected_evidence_requires_bounded_range"
    assert calls == []


@pytest.mark.parametrize("selected", [
    [{
        "outcome": "subjective.day_rating",
        "finding_id": "sha256:" + "a" * 64,
        "input_fingerprint": "sha256:" + "b" * 64,
    }] * 4,
    [{
        "outcome": "subjective.day_rating",
        "finding_id": "sha256:" + "a" * 64,
        "input_fingerprint": "sha256:" + "b" * 64,
    }, {
        "outcome": "subjective.day_rating",
        "finding_id": "sha256:" + "a" * 64,
        "input_fingerprint": "sha256:" + "c" * 64,
    }],
    [{
        "outcome": "subjective.day_rating",
        "finding_id": "not-an-id",
        "input_fingerprint": "sha256:" + "b" * 64,
    }],
    [{
        "outcome": "subjective.day_rating",
        "finding_id": "sha256:" + "a" * 64,
        "input_fingerprint": "sha256:" + "b" * 64,
        "browser_claim": "trusted",
    }],
])
def test_selected_findings_contract_is_closed_and_bounded(
    tmp_path, monkeypatch, selected,
):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    assert _send(client, conversation, selected_findings=selected).status_code == 400
    assert calls == []


def test_archive_read_unarchive_resume_and_no_delete(tmp_path, monkeypatch):
    app, token, _ = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    archived = client.patch(
        f"/api/chat/conversations/{conversation['id']}", json={"archived": True}
    )
    assert archived.get_json()["conversation"]["archived"] is True
    assert client.get(
        f"/api/chat/conversations/{conversation['id']}"
    ).status_code == 200
    assert _send(client, archived.get_json()["conversation"]).status_code == 409
    assert client.delete(
        f"/api/chat/conversations/{conversation['id']}"
    ).status_code == 405
    resumed = client.patch(
        f"/api/chat/conversations/{conversation['id']}", json={"archived": False}
    ).get_json()["conversation"]
    assert _send(client, resumed).status_code == 200


def test_active_conversation_limit_is_fifty(tmp_path, monkeypatch):
    app, token, _ = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    for _ in range(50):
        _create(client)
    response = client.post("/api/chat/conversations", json={
        "lens": "general", "context": _context(),
    })
    assert response.status_code == 409
    assert response.get_json()["code"] == "active_conversation_limit"


def test_deterministic_default_title_and_edited_title_limit(tmp_path, monkeypatch):
    app, token, _ = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    text = "  A   first \n message " + "x" * 100
    assert _send(client, conversation, text=text).status_code == 200
    detail = client.get(
        f"/api/chat/conversations/{conversation['id']}"
    ).get_json()["conversation"]
    assert detail["title"] == "A first message " + "x" * 64
    assert len(detail["title"]) == 80
    assert client.patch(
        f"/api/chat/conversations/{conversation['id']}",
        json={"title": "x" * 121},
    ).status_code == 400


def test_more_than_one_thousand_messages_use_lossless_cursor_pagination(
    tmp_path, monkeypatch,
):
    app, token, _ = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    connection = sqlite3.connect(app.config["PANEL_DB"])
    connection.executemany(
        """INSERT INTO chat_log(
             ts,thread,role,content,conversation_id,turn_id,context_json,delivery_status
           ) VALUES(1,'general','user',?,?,?,?,'complete')""",
        [
            (
                f"message-{index}",
                conversation["id"],
                str(uuid.uuid4()),
                json.dumps({
                    "version": 1, "surface": "panel", "lens": "general",
                    "conversation_id": conversation["id"], "range": {"kind": "all"},
                    "selected_region_ids": [], "evidence_contract": "health-tool-v1",
                }),
            )
            for index in range(1005)
        ],
    )
    connection.commit()
    connection.close()
    seen = []
    before = None
    while True:
        suffix = f"&before_id={before}" if before else ""
        data = client.get(
            f"/api/chat/conversations/{conversation['id']}/messages?limit=200{suffix}"
        ).get_json()
        seen.extend(message["id"] for message in data["messages"])
        before = data["next_before_id"]
        if before is None:
            break
    assert len(seen) == len(set(seen)) == 1005


def test_same_conversation_lease_race_has_one_winner(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    call_count = 0
    guard = threading.Lock()

    def runner(*_args, **_kwargs):
        nonlocal call_count
        with guard:
            call_count += 1
        entered.set()
        assert release.wait(5)
        return {"reply": "done"}

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    seed = _client(app, token)
    conversation = _create(seed)

    def first():
        return _send(_client(app, token), conversation, text="first")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first)
        assert entered.wait(5)
        loser = _send(_client(app, token), conversation, text="second")
        release.set()
        winner = pending.result(timeout=5)
    assert winner.status_code == 200
    assert loser.status_code == 409
    assert loser.get_json()["code"] == "conversation_busy"
    assert call_count == 1


def test_edit_respects_active_lease_and_recovers_expired_lease(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def runner(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return {"reply": "done"}

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    seed = _client(app, token)
    conversation = _create(seed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            _send, _client(app, token), conversation, "in progress"
        )
        assert entered.wait(5)
        edit = seed.patch(
            f"/api/chat/conversations/{conversation['id']}",
            json={"title": "Should wait"},
        )
        release.set()
        assert pending.result(timeout=5).status_code == 200
    assert edit.status_code == 409
    assert edit.get_json()["code"] == "conversation_busy"

    expired_turn = str(uuid.uuid4())
    connection = sqlite3.connect(app.config["PANEL_DB"])
    connection.execute(
        "UPDATE chat_conversations SET busy_turn_id=?,busy_until=? WHERE id=?",
        (expired_turn, int(time.time()) - 1, conversation["id"]),
    )
    connection.execute(
        """INSERT INTO chat_log(
             ts,thread,role,content,conversation_id,turn_id,context_json,delivery_status
           ) VALUES(1,'general','user','expired',?,?,?,'pending')""",
        (conversation["id"], expired_turn, "{}"),
    )
    connection.commit()
    connection.close()
    recovered = seed.patch(
        f"/api/chat/conversations/{conversation['id']}",
        json={"title": "Recovered safely"},
    )
    assert recovered.status_code == 200
    assert recovered.get_json()["conversation"]["title"] == "Recovered safely"
    messages = seed.get(
        f"/api/chat/conversations/{conversation['id']}/messages"
    ).get_json()["messages"]
    assert next(
        message for message in messages if message["turn_id"] == expired_turn
    )["delivery_status"] == "uncertain"


def test_different_conversations_run_in_parallel(tmp_path, monkeypatch):
    both_entered = threading.Event()
    release = threading.Event()
    count = 0
    guard = threading.Lock()

    def runner(*_args, **_kwargs):
        nonlocal count
        with guard:
            count += 1
            if count == 2:
                both_entered.set()
        assert release.wait(5)
        return {"reply": "done"}

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    seed = _client(app, token)
    first = _create(seed)
    second = _create(seed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(_send, _client(app, token), first)
        two = pool.submit(_send, _client(app, token), second)
        assert both_entered.wait(5)
        release.set()
        assert one.result(timeout=5).status_code == 200
        assert two.result(timeout=5).status_code == 200


def test_turn_retry_is_idempotent_and_never_redelivers(tmp_path, monkeypatch):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    turn = str(uuid.uuid4())
    first = _send(client, conversation, turn_id=turn)
    second = _send(client, conversation, turn_id=turn)
    assert first.status_code == second.status_code == 200
    assert second.get_json()["idempotent"] is True
    assert len(calls) == 1
    messages = client.get(
        f"/api/chat/conversations/{conversation['id']}/messages"
    ).get_json()["messages"]
    assert len(messages) == 2


def test_timeout_is_uncertain_and_retry_never_resends(tmp_path, monkeypatch):
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise bridge.BridgeError("Hermes took too long to respond.")

    app, token, _ = _app(tmp_path, monkeypatch, runner)
    client = _client(app, token)
    conversation = _create(client)
    turn = str(uuid.uuid4())
    assert _send(client, conversation, turn_id=turn).status_code == 502
    retry = _send(client, conversation, turn_id=turn)
    assert retry.status_code == 409
    assert retry.get_json()["code"] == "turn_uncertain"
    assert len(calls) == 1
    messages = client.get(
        f"/api/chat/conversations/{conversation['id']}/messages"
    ).get_json()["messages"]
    assert [message["delivery_status"] for message in messages] == ["uncertain"]


def test_expired_pending_lease_becomes_uncertain_without_delivery(tmp_path, monkeypatch):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    turn = str(uuid.uuid4())
    connection = sqlite3.connect(app.config["PANEL_DB"])
    connection.execute(
        "UPDATE chat_conversations SET busy_turn_id=?,busy_until=? WHERE id=?",
        (turn, int(time.time()) - 1, conversation["id"]),
    )
    connection.execute(
        """INSERT INTO chat_log(
             ts,thread,role,content,conversation_id,turn_id,context_json,delivery_status
           ) VALUES(1,'general','user','old',?,?,?,'pending')""",
        (conversation["id"], turn, "{}"),
    )
    connection.commit()
    connection.close()
    response = _send(client, conversation, turn_id=turn)
    assert response.status_code == 409
    assert response.get_json()["code"] == "turn_uncertain"
    assert calls == []


@pytest.mark.parametrize("bad_context", [
    None,
    {},
    {"version": 1, "range": {"kind": "all"}, "selected_region_ids": [], "extra": 1},
    {"version": True, "range": {"kind": "all"}, "selected_region_ids": []},
    {"version": 1, "range": {"kind": "bounded", "from": "2026-7-01", "to": "2026-07-23"},
     "selected_region_ids": []},
    {"version": 1, "range": {"kind": "all"}, "selected_region_ids": ["left knee"]},
    {"version": 1, "range": {"kind": "all"}, "selected_region_ids": ["a" * 81]},
    {"version": 1, "range": {"kind": "all"},
     "selected_region_ids": [f"region-{index}" for index in range(21)]},
    {"version": 1, "range": {"kind": "all"}, "selected_region_ids": ["knee-left", "knee-left"]},
])
def test_malformed_context_is_rejected(tmp_path, monkeypatch, bad_context):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    response = client.post("/api/chat/conversations", json={
        "lens": "pain", "context": bad_context,
    })
    assert response.status_code == 400
    assert calls == []


def test_oversized_message_is_rejected_before_delivery(tmp_path, monkeypatch):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    conversation = _create(client)
    response = _send(client, conversation, text="x" * 4001)
    assert response.status_code == 400
    assert calls == []


def test_duplicate_json_key_and_noncanonical_turn_uuid_are_rejected(tmp_path, monkeypatch):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    raw = '{"lens":"general","lens":"pain"}'
    assert client.post(
        "/api/chat/conversations", data=raw, content_type="application/json"
    ).status_code == 400
    conversation = _create(client)
    response = client.post(
        f"/api/chat/conversations/{conversation['id']}/send",
        json={"message": "hi", "turn_id": str(uuid.uuid4()).upper(), "context": _context()},
    )
    assert response.status_code == 400
    assert calls == []


def test_configured_timezone_calendar_windows_and_closed_range_contract(monkeypatch):
    import app.canon as canon

    class FixedDateTime:
        @staticmethod
        def now(_timezone):
            from datetime import datetime
            return datetime.fromisoformat("2026-07-23T00:30:00+02:00")

    monkeypatch.setattr(canon, "datetime", FixedDateTime)
    assert range_bounds("day")["range"] == {
        "kind": "bounded", "from": "2026-07-23", "to": "2026-07-23",
    }
    assert range_bounds("week")["range"] == {
        "kind": "bounded", "from": "2026-07-20", "to": "2026-07-26",
    }
    assert range_bounds("month")["range"] == {
        "kind": "bounded", "from": "2026-07-01", "to": "2026-07-31",
    }
    assert range_bounds("year")["range"] == {
        "kind": "bounded", "from": "2026-01-01", "to": "2026-12-31",
    }
    assert range_bounds(
        "year", anchor="2024-02-29", shift=1
    )["range"] == {
        "kind": "bounded", "from": "2025-01-01", "to": "2025-12-31",
    }
    assert range_bounds("all")["range"] == {"kind": "all"}
    with pytest.raises(CanonicalRangeError):
        canonical_range({"kind": "all", "from": "2026-01-01"})


def test_legacy_insight_routes_are_quarantined_or_reject_ranges_before_bridge(
    tmp_path, monkeypatch,
):
    app, token, calls = _app(tmp_path, monkeypatch)
    client = _client(app, token)
    correlations = client.get("/api/insights/correlations?range=all")
    assert correlations.status_code == 410
    assert correlations.get_json()["error"]["code"] == (
        "legacy_insight_route_quarantined"
    )
    signature = client.get("/api/insights/signature?range=all")
    assert signature.status_code == 410
    assert signature.get_json()["error"]["code"] == (
        "legacy_insight_route_quarantined"
    )
    assert calls == []


def test_panel_service_keeps_read_only_data_and_process_hardening():
    base = Path(__file__).parents[1]
    service = (base / "deploy/hermes-panel.service").read_text(encoding="utf-8")
    assert "NoNewPrivileges=yes" in service
    assert "BindReadOnlyPaths=/var/lib/hermes/health.db /var/lib/hermes/vault" in service
    assert "EnvironmentFile=/etc/hermes/panel.env" in service


def test_static_visual_fixture_renders_chat_controls(tmp_path):
    base = Path(__file__).parents[1]
    env = os.environ.copy()
    env.update({
        "HERMES_PHASE7_VISUAL_DB": str(tmp_path / "visual.db"),
        "HERMES_PHASE7_VISUAL_OUTPUT_DIR": str(tmp_path),
        "PYTHONPATH": str(base),
    })
    subprocess.run(
        [sys.executable, str(base / "scripts/phase7_visual_fixture.py"), "--render-static"],
        cwd=base, env=env, check=True, capture_output=True, text=True,
    )
    for name in ("insights", "conversations", "pain", "mobility"):
        text = (tmp_path / f"hermes-phase7-{name}.html").read_text(encoding="utf-8")
        assert 'meta name="csrf-token"' in text
        if name in ("insights", "conversations"):
            assert 'aria-label="Message Hermes"' in text
        else:
            assert 'aria-label="Message the Hermes coach"' in text


@pytest.mark.parametrize("lens", ["pain", "mobility"])
def test_report_workspace_sends_only_saved_visible_regions(lens):
    """Exercise real client handlers through failed/retried and delayed saves."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute the browser regression")
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const lens = process.argv[1];
const elements = new Map();
function element() {
  return {
    dataset: {lens}, value: '', disabled: false, children: [],
    classList: {remove() {}},
    addEventListener(type, callback) { this[type] = callback; },
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
    remove() {}, focus() {},
  };
}
const get = id => {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
};
const context = ids => ({version: 1, range: {kind: 'all'}, selected_region_ids: ids});
const records = {
  first: {id: 'first', title: 'First', context: context([])},
  second: {id: 'second', title: 'Second', context: context(['hip-left'])},
};
const clone = value => JSON.parse(JSON.stringify(value));
const sends = [];
let clickRegion, failSave = true, holdSave = false, releaseSave;
const fetch = async (url, options = {}) => {
  const body = options.body && JSON.parse(options.body);
  const id = url.split('/')[4];
  let result;
  if (options.method === 'PATCH') {
    if (holdSave) await new Promise(resolve => { releaseSave = resolve; });
    if (failSave) return {ok: false, status: 503, json: async () => ({error: 'Save unavailable'})};
    records[id].context = clone(body.context);
    result = {conversation: records[id]};
  } else if (options.method === 'POST' && url.endsWith('/send')) {
    sends.push({id, body});
    result = {reply: 'Fictional scoped reply'};
  } else if (url.includes('/messages?')) result = {messages: []};
  else if (url.includes('/muscle-map?')) result = {result: {non_muscle: []}};
  else if (url.includes('/conversations?')) result = {conversations: Object.values(records)};
  else result = {conversation: records[id]};
  const copy = clone(result);
  return {ok: true, status: 200, json: async () => copy};
};
const sandbox = {
  window: {
    BodyMuscles: {FRONT_MUSCLES: [{id: 'knee-left'}, {id: 'hip-left'}]},
    MuscleFigure: {
      create(node, options) { clickRegion = options.onRegionClick; return {mark() {}, update() {}}; },
      legend() {},
    },
  },
  document: {getElementById: get, querySelector: () => ({content: 'fictional'}), createElement: element},
  fetch, crypto: require('node:crypto').webcrypto, URLSearchParams,
  localStorage: {getItem() { return null; }, setItem() {}}, location: {search: ''},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/static/js/report_workspace.js', 'utf8'), sandbox);
const tick = () => new Promise(resolve => setImmediate(resolve));
const submit = () => get('rw-form').submit({preventDefault() {}});
(async () => {
  await tick();
  clickRegion('knee-left');
  await tick();
  get('rw-input').value = 'What about this selected spot?';
  await submit();
  assert.equal(sends.length, 0, 'failed region saves must prevent delivery');
  assert.equal(get('rw-input').value, 'What about this selected spot?');
  assert.equal(get('rw-asking-chips').children.length, 1, 'unsaved selection stays visible');
  failSave = false;
  await submit();
  assert.equal(sends.length, 1);
  assert.deepEqual(sends[0].body.context.selected_region_ids, ['knee-left']);
  assert.equal(get('rw-input').value, '');

  // A queued save for the previous investigation must not replace the new scope.
  holdSave = true;
  clickRegion('hip-left');
  await tick();
  await get('rw-conversation').change({target: {value: 'second'}});
  get('rw-input').value = 'Second investigation draft';
  const pending = submit();
  assert.equal(get('rw-conversation').disabled, true);
  assert.equal(get('rw-new-conversation').disabled, true);
  await get('rw-conversation').change({target: {value: 'first'}});
  releaseSave();
  await pending;
  assert.equal(sends.length, 2);
  assert.equal(sends[1].id, 'second');
  assert.deepEqual(sends[1].body.context.selected_region_ids, ['hip-left']);
  assert.equal(get('rw-conversation').disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(
        [node, "-e", script, lens], cwd=Path(__file__).parents[1],
        check=True, capture_output=True, text=True, timeout=15,
    )


def test_phase7_mutations_keep_csrf_csp_and_per_session_rate_limits(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        bridge, "run", lambda *_args, **_kwargs: {"ok": True, "reply": "safe"}
    )
    csrf_app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "csrf-panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    csrf_client = csrf_app.test_client()
    with csrf_app.app_context():
        csrf_token = auth_mod.create_session()
    csrf_client.set_cookie(auth_mod.SESSION_COOKIE, csrf_token)
    rejected = csrf_client.post(
        "/api/chat/conversations",
        json={"lens": "general", "context": _context()},
    )
    assert rejected.status_code == 400
    assert "CSRF" in rejected.get_json()["error"]
    assert "default-src 'self'" in rejected.headers["Content-Security-Policy"]
    page = csrf_client.get("/insights").get_data(as_text=True)
    assert "PHASE7_STATIC_FIXTURE_CSRF_TOKEN" not in page
    accepted = csrf_client.post(
        "/api/chat/conversations",
        json={"lens": "general", "context": _context()},
        headers={"X-CSRFToken": csrf_from(page)},
    )
    assert accepted.status_code == 201

    rate_app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": True,
        "PANEL_DB": str(tmp_path / "rate-panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    with rate_app.app_context():
        rate_token = auth_mod.create_session()
    rate_client = _client(rate_app, rate_token)
    first = _create(rate_client)
    second = _create(rate_client)
    assert all(
        _send(rate_client, first, turn_id=str(uuid.uuid4())).status_code == 200
        for _ in range(6)
    )
    assert _send(
        rate_client, second, turn_id=str(uuid.uuid4())
    ).status_code == 200
    assert _send(
        rate_client, first, turn_id=str(uuid.uuid4())
    ).status_code == 429

    create_app_rate = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": True,
        "PANEL_DB": str(tmp_path / "create-rate-panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    with create_app_rate.app_context():
        create_token = auth_mod.create_session()
    create_client = _client(create_app_rate, create_token)
    statuses = [
        create_client.post(
            "/api/chat/conversations",
            json={"lens": "general", "context": _context()},
        ).status_code
        for _ in range(21)
    ]
    assert statuses[:20] == [201] * 20
    assert statuses[20] == 429
