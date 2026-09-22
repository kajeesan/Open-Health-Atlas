"""Bounded note overwrite, journal append and immutable transcript capture."""

import os

from .. import calculations, runtime
from ..command_context import CommandContext
from ..importers.common import stdin_text, valid_date
from ..vault_notes import EDITABLE_NOTES, vault_write_path


def write_note(context: CommandContext, a, *, stdin):
    """Overwrite a whitelisted vault Markdown note with content read from stdin.
    Atomic (temp + rename). Content is freeform text — never executed."""
    rel = (a.path or "").strip()
    if rel not in EDITABLE_NOTES:
        raise SystemExit(f"not an editable note: {rel}")
    content = stdin.read()
    if len(content) > 500_000:
        raise SystemExit("note too large (>500k)")
    vault = context.vault
    target = os.path.join(vault, rel)
    # Defense in depth: the resolved path must still live inside the vault.
    if os.path.commonpath([os.path.abspath(target), vault]) != vault:
        raise SystemExit("path escapes the vault")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, target)
    return {"ok": True, "wrote": rel, "bytes": len(content)}


def journal_capture(context: CommandContext, a, *, stdin):
    """§6 typed status: stdin lands VERBATIM in raw/journal/YYYY-MM-DD.md.
    Append-only — same-day entries stack under '## HH:MM' separators, nothing
    is ever overwritten. The agent extracts fields AFTERWARDS via the
    validated `log subjective_daily` path (raw survives any extraction bug)."""
    content = stdin_text(stdin, "journal entry")
    d = valid_date(a.date) if a.date else runtime.today(clock=context.clock)
    t = a.time or context.clock().strftime("%H:%M")
    if calculations._hhmm_min(t) is None:
        raise SystemExit("--time must be HH:MM")
    target = vault_write_path(context.vault, "raw/journal", f"{d}.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    new_file = not os.path.exists(target)
    with open(target, "a", encoding="utf-8") as f:
        if new_file:
            f.write(f"# Journal {d}\n")
        f.write(f"\n## {t}\n\n{content.rstrip()}\n")
    return {"ok": True, "file": f"raw/journal/{d}.md", "date": d, "time": t,
         "bytes": len(content), "created": new_file}


def transcript_capture(context: CommandContext, a, *, stdin):
    """§6 voice memo: the whisper.cpp transcript (stdin) lands next to its
    audio as raw/voice/<audio>.txt — verbatim, refuse-on-exists (raw/ is
    immutable; a re-run must pick a new name, never silently replace)."""
    content = stdin_text(stdin, "transcript")
    target = vault_write_path(context.vault, "raw/voice", f"{a.audio}.txt")
    if os.path.exists(target):
        raise SystemExit(f"raw/voice/{a.audio}.txt already exists — raw files are "
                 "immutable; save a re-transcription under a new name")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "x", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "file": f"raw/voice/{a.audio}.txt", "bytes": len(content)}
