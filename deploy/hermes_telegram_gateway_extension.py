"""Hermes Telegram gateway bridge for registered tap-first health actions.

This module is copied beside the Telegram adapter.  The adapter calls
``register`` once and delegates only callback data beginning with ``hx:``.
It deliberately does not interpret health values itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)
DEFAULT_HANDLER = "/usr/local/lib/hermes-bridge/hermes-telegram-actions"


def _handler() -> Path:
    return Path(os.environ.get("HERMES_TELEGRAM_ACTION_HANDLER", DEFAULT_HANDLER))


def _authorized(adapter: Any, *, user_id: str, chat_id: Any,
                chat_type: Any, thread_id: Any, user_name: Any) -> bool:
    return adapter._is_callback_user_authorized(
        user_id,
        chat_id=chat_id,
        chat_type=str(chat_type) if chat_type is not None else None,
        thread_id=str(thread_id) if thread_id is not None else None,
        user_name=user_name,
    )


async def _run(kind: str, payload: dict) -> dict:
    handler = _handler()
    if not handler.is_file() or not os.access(handler, os.X_OK):
        return {"ok": False, "answer_text": "Tap actions are temporarily unavailable."}
    try:
        process = await asyncio.create_subprocess_exec(
            str(handler),
            "dispatch",
            kind,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(
                (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
            ),
            timeout=50,
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return {"ok": False, "answer_text": "Logging timed out. Please tap once more."}
    except Exception as exc:
        logger.error("Telegram action handler failed to start: %s", exc)
        return {"ok": False, "answer_text": "Tap actions are temporarily unavailable."}
    try:
        value = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError:
        value = None
    if process.returncode not in (0, 1) or not isinstance(value, dict):
        logger.error(
            "Telegram action handler returned invalid output rc=%s stderr=%s",
            process.returncode,
            stderr.decode("utf-8", errors="replace")[-300:],
        )
        return {"ok": False, "answer_text": "The tap was not logged."}
    return value


async def handle_callback(adapter: Any, update: Any) -> None:
    query = getattr(update, "callback_query", None)
    if not query or not str(getattr(query, "data", "")).startswith("hx:"):
        return
    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None)
    user = getattr(query, "from_user", None)
    user_id = str(getattr(user, "id", ""))
    chat_id = getattr(message, "chat_id", None)
    chat_type = getattr(chat, "type", None)
    thread_id = getattr(message, "message_thread_id", None)
    if not _authorized(
        adapter,
        user_id=user_id,
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=thread_id,
        user_name=getattr(user, "first_name", None),
    ):
        await query.answer(text="⛔ You are not authorized to use this action.")
        return
    result = await _run(
        "callback",
        {
            "query_id": str(getattr(query, "id", "")),
            "data": str(query.data),
            "chat_id": str(chat_id),
            "message_id": str(getattr(message, "message_id", "")),
            "user_id": user_id,
        },
    )
    answer = str(result.get("answer_text") or (
        "Logged." if result.get("ok") else "The tap was not logged."
    ))[:190]
    await query.answer(text=answer)
    edit_text = result.get("edit_text")
    if isinstance(edit_text, str) and edit_text:
        try:
            markup = None if result.get("remove_keyboard") else getattr(
                message, "reply_markup", None
            )
            await query.edit_message_text(text=edit_text[:4096], reply_markup=markup)
        except Exception:
            logger.debug("Telegram action card edit failed", exc_info=True)


def _reaction_values(values: Any) -> list[dict]:
    result = []
    for value in values or []:
        emoji = getattr(value, "emoji", None)
        if isinstance(emoji, str):
            result.append({"type": "emoji", "emoji": emoji})
    return result


async def handle_reaction(adapter: Any, update: Any, _context: Any) -> None:
    reaction = getattr(update, "message_reaction", None)
    if reaction is None:
        return
    chat = getattr(reaction, "chat", None)
    user = getattr(reaction, "user", None)
    user_id = str(getattr(user, "id", ""))
    chat_id = getattr(chat, "id", None)
    if not _authorized(
        adapter,
        user_id=user_id,
        chat_id=chat_id,
        chat_type=getattr(chat, "type", None),
        thread_id=None,
        user_name=getattr(user, "first_name", None),
    ):
        return
    result = await _run(
        "reaction",
        {
            "update_id": getattr(update, "update_id", None),
            "chat_id": str(chat_id),
            "message_id": str(getattr(reaction, "message_id", "")),
            "user_id": user_id,
            "old_reaction": _reaction_values(getattr(reaction, "old_reaction", None)),
            "new_reaction": _reaction_values(getattr(reaction, "new_reaction", None)),
        },
    )
    edit_text = result.get("edit_text")
    if isinstance(edit_text, str) and edit_text and getattr(adapter, "_bot", None):
        try:
            await adapter._bot.edit_message_text(
                chat_id=chat_id,
                message_id=getattr(reaction, "message_id", None),
                text=edit_text[:4096],
                reply_markup=None if result.get("remove_keyboard") else None,
            )
        except Exception:
            logger.debug("Telegram reaction card edit failed", exc_info=True)


def register(adapter: Any, application: Any) -> bool:
    """Register reaction handling when the root-owned action worker is present."""
    handler = _handler()
    if not handler.is_file() or not os.access(handler, os.X_OK):
        logger.info("Telegram health actions not registered: %s is unavailable", handler)
        return False
    try:
        from telegram.ext import MessageReactionHandler
    except ImportError:
        logger.warning("Telegram SDK does not support MessageReactionHandler")
        return False
    application.add_handler(
        MessageReactionHandler(
            lambda update, context: handle_reaction(adapter, update, context)
        )
    )
    logger.info("Telegram tap-first health actions registered")
    return True
