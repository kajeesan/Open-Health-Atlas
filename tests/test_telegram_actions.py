"""Telegram tap actions are bound, idempotent, and use health.py for writes."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKER = ROOT / "deploy/hermes-telegram-actions"
SERVICE = ROOT / "deploy/hermes-telegram-evening.service"
INSTALLER = ROOT / "deploy/install-telegram-actions.sh"
INTEGRATION_GUIDE = ROOT / "deploy/hermes-telegram-integration.md"
DAY = "2026-07-26"


@pytest.fixture()
def harness(tmp_path):
    home = tmp_path / "home"
    state = tmp_path / "state"
    home.mkdir()
    (home / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "TELEGRAM_HOME_CHANNEL=12345\n"
        "TELEGRAM_ALLOWED_USERS=77\n"
    )
    (home / ".env").chmod(0o600)
    health_log = tmp_path / "health-log.jsonl"
    water_total = tmp_path / "water-total"
    water_total.write_text("0")
    health = tmp_path / "health.py"
    health.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
log = pathlib.Path(os.environ["HEALTH_LOG"])
with log.open("a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "feedback-status":
    print(os.environ["FEEDBACK_STATUS"])
elif sys.argv[1] == "water-add":
    total_path = pathlib.Path(os.environ["WATER_TOTAL"])
    total = int(total_path.read_text()) + int(sys.argv[2])
    total_path.write_text(str(total))
    print(json.dumps({"ok": True, "total_ml": total, "target_ml": 1800}))
else:
    print(json.dumps({"ok": True}))
"""
    )
    health.chmod(0o755)
    outbox = tmp_path / "outbox.json"
    env = {
        **os.environ,
        "HERMES_TELEGRAM_ACTIONS_TESTING": "1",
        "HERMES_TELEGRAM_ACTIONS_HOME": str(home),
        "HERMES_TELEGRAM_ACTIONS_STATE": str(state),
        "HERMES_TELEGRAM_ACTIONS_HEALTH": str(health),
        "HERMES_TELEGRAM_ACTIONS_OUTBOX": str(outbox),
        "HEALTH_LOG": str(health_log),
        "WATER_TOTAL": str(water_total),
        "FEEDBACK_STATUS": json.dumps({
            "ok": True,
            "date": DAY,
            "prompt_fields": [],
            "active_commitments_missing_log": [],
            "pain_followup_due": [],
        }),
    }
    yield env, health_log, water_total, outbox


def run(env, *args, stdin=None, ok=True):
    result = subprocess.run(
        [sys.executable, str(WORKER), *args],
        input=json.dumps(stdin) if stdin is not None else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    return json.loads(result.stdout)


def _callback(card, code, query_id="q1", user_id="77"):
    return {
        "query_id": query_id,
        "data": f"hx:{card['token']}:{code}",
        "chat_id": "12345",
        "message_id": card["message_id"],
        "user_id": user_id,
    }


def test_checkin_card_is_deduplicated_and_has_exact_tap_rows(harness):
    env, _health_log, _water_total, outbox = harness
    first = run(env, "send-checkin", "--slot", "am", "--date", DAY)
    assert first["sent"] is True
    second = run(env, "send-checkin", "--slot", "am", "--date", DAY)
    assert second["duplicate_suppressed"] is True
    messages = json.loads(outbox.read_text())
    assert len(messages) == 1
    keyboard = messages[0]["reply_markup"]["inline_keyboard"]
    assert [button["text"] for button in keyboard[0]] == [
        "Energy 1", "Energy 2", "Energy 3", "Energy 4", "Energy 5"
    ]
    assert [button["text"] for button in keyboard[2]] == ["+250 ml", "+500 ml", "+1 L"]
    assert all(button["callback_data"].startswith(f"hx:{first['token']}:")
               for row in keyboard for button in row)


def test_credential_file_rejects_group_or_world_access(harness):
    env, _health_log, _water_total, _outbox = harness
    (pathlib.Path(env["HERMES_TELEGRAM_ACTIONS_HOME"]) / ".env").chmod(0o644)
    result = run(env, "send-water", "--date", DAY, ok=False)
    assert "mode 0600" in result["error"]


def test_service_paths_match_worker_state_and_use_systemd_credentials():
    unit = SERVICE.read_text()
    assert "HERMES_DATA_DIR=/var/lib/hermes" in unit
    assert "HERMES_TELEGRAM_ACTIONS_STATE=/var/lib/hermes/.runtime/telegram-actions" in unit
    assert "ReadWritePaths=/var/lib/hermes/.runtime/telegram-actions" in unit
    assert "LoadCredential=telegram.env:/etc/hermes/telegram.env" in unit
    assert "InaccessiblePaths=/etc/hermes" in unit


def test_installer_is_disabled_and_does_not_patch_external_gateway():
    installer = INSTALLER.read_text()
    assert "installed-disabled" in installer
    assert "external_gateway_modified" in installer
    assert "hermes-telegram-integration.md" in installer
    assert "git apply" not in installer
    assert ".patch" not in installer
    assert "enable --now" not in installer
    assert "systemctl --user" not in installer
    assert "handle_callback(adapter, update)" in INTEGRATION_GUIDE.read_text()
    checked = subprocess.run(
        ["sh", "-n", str(INSTALLER)], capture_output=True, text=True, timeout=30
    )
    assert checked.returncode == 0, checked.stderr


def test_water_callback_is_idempotent_per_telegram_query(harness):
    env, health_log, water_total, _outbox = harness
    card = run(env, "send-water", "--date", DAY)
    first = run(env, "dispatch", "callback", stdin=_callback(card, "w250"))
    assert first["answer_text"] == "+250 ml · 250 / 1800 ml today"
    replay = run(env, "dispatch", "callback", stdin=_callback(card, "w250"))
    assert replay == first
    second = run(
        env, "dispatch", "callback",
        stdin=_callback(card, "w500", query_id="q2"),
    )
    assert second["answer_text"] == "+500 ml · 750 / 1800 ml today"
    assert water_total.read_text() == "750"
    calls = [json.loads(line) for line in health_log.read_text().splitlines()]
    assert calls == [
        ["water-add", "250", "--date", DAY],
        ["water-add", "500", "--date", DAY],
    ]


def test_rating_and_authorization_binding_use_canonical_health_cli(harness):
    env, health_log, _water_total, _outbox = harness
    card = run(env, "send-rating", "--date", DAY)
    refused = run(
        env, "dispatch", "callback",
        stdin=_callback(card, "rgreen", user_id="88"),
        ok=False,
    )
    assert refused["answer_text"] == "wrong card owner"
    accepted = run(
        env, "dispatch", "callback",
        stdin=_callback(card, "rgreen", query_id="q2"),
    )
    assert accepted["answer_text"] == "🟢 Green day logged"
    calls = [json.loads(line) for line in health_log.read_text().splitlines()]
    assert calls == [
        ["day-rating", "green", "--date", DAY, "--source", "chat-telegram"]
    ]


def test_heart_means_done_only_on_a_registered_single_task_card(harness):
    env, _health_log, _water_total, _outbox = harness
    task = run(
        env, "send-task", "--title", "Ten-minute walk",
        "--goal", "Protect energy", "--floor", "Put shoes on", "--date", DAY,
    )
    event = {
        "update_id": 900,
        "chat_id": "12345",
        "message_id": task["message_id"],
        "user_id": "77",
        "old_reaction": [],
        "new_reaction": [{"type": "emoji", "emoji": "❤"}],
    }
    result = run(env, "dispatch", "reaction", stdin=event)
    assert result["task_status"] == "done"
    assert result["remove_keyboard"] is True
    assert "✅ Done" in result["edit_text"]
    tasks = run(env, "list-tasks", "--date", DAY)
    assert tasks["tasks"][0]["status"] == "done"

    water = run(env, "send-water", "--date", DAY)
    ignored = run(
        env, "dispatch", "reaction",
        stdin={**event, "update_id": 901, "message_id": water["message_id"]},
    )
    assert ignored == {"ignored": True, "ok": True}


def test_evening_sends_only_missing_tap_cards_including_commitments(harness):
    env, _health_log, _water_total, outbox = harness
    env["FEEDBACK_STATUS"] = json.dumps({
        "ok": True,
        "date": DAY,
        "prompt_fields": ["day_rating", "whole_day_word", "mood"],
        "active_commitments_missing_log": [{"id": 4, "name": "Pack gym bag"}],
        "pain_followup_due": [],
    })
    result = run(env, "send-evening", "--date", DAY)
    assert result["sent_count"] == 4
    messages = json.loads(outbox.read_text())
    assert [item["text"] for item in messages] == [
        "🌙 How was today overall?",
        "Did you keep your word to yourself today?",
        "Mood right now?",
        "Commitment: Pack gym bag",
    ]
    replay = run(env, "send-evening", "--date", DAY)
    assert replay["sent_count"] == 0 and replay["duplicate_count"] == 4
    assert len(json.loads(outbox.read_text())) == 4


def test_same_heart_selects_the_specific_plain_language_option(harness):
    env, _health_log, _water_total, _outbox = harness
    first = run(
        env, "send-option", "--group", "workout-choice",
        "--option", "Ten-minute walk", "--date", DAY,
    )
    second = run(
        env, "send-option", "--group", "workout-choice",
        "--option", "Mobility floor", "--date", DAY,
    )
    event = {
        "update_id": 910,
        "chat_id": "12345",
        "message_id": second["message_id"],
        "user_id": "77",
        "old_reaction": [],
        "new_reaction": [{"type": "emoji", "emoji": "❤"}],
    }
    selected = run(env, "dispatch", "reaction", stdin=event)
    assert selected["selection_status"] == "selected"
    options = run(env, "list-options", "--group", "workout-choice")["options"]
    assert [item["status"] for item in options] == ["available", "selected"]
    assert first["message_id"] != second["message_id"]
