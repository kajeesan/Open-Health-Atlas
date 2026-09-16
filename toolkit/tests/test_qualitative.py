"""§6 qualitative pipeline: journal-capture (verbatim, append-only) +
transcript-capture (verbatim, refuse-on-exists). Raw text is preserved BEFORE
extraction; the vault-escape guard mirrors write-note's.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import date

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()


@pytest.fixture()
def vault(tmp_path):
    """A temp vault: the DB lives at its root, exactly like the server."""
    p = tmp_path / "vault" / "health.db"
    p.parent.mkdir()
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args, stdin=None, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       input=stdin, capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else {}
    assert r.returncode != 0
    return r.stderr


def test_journal_appends_verbatim_never_overwrites(vault):
    d = date.today().isoformat()
    r = run(vault, "journal-capture", "--time", "09:15", stdin="Rough morning — slept 5h.")
    assert r["created"] is True and r["file"] == f"raw/journal/{d}.md"
    r = run(vault, "journal-capture", "--time", "21:40", stdin="Better evening.\nGym done.")
    assert r["created"] is False
    text = (vault.parent / "raw" / "journal" / f"{d}.md").read_text()
    assert text.startswith(f"# Journal {d}\n")
    assert "## 09:15" in text and "Rough morning — slept 5h." in text
    assert "## 21:40" in text and "Better evening.\nGym done." in text   # verbatim, both kept


def test_journal_validates(vault):
    assert "empty" in run(vault, "journal-capture", stdin="   ", expect_ok=False)
    assert "HH:MM" in run(vault, "journal-capture", "--time", "9pm", stdin="x", expect_ok=False)
    assert "--date" in run(vault, "journal-capture", "--date", "07/10", stdin="x", expect_ok=False)


def test_transcript_writes_once_refuses_overwrite(vault):
    r = run(vault, "transcript-capture", "memo-0930.ogg", stdin="I felt sharp until three.")
    assert r["file"] == "raw/voice/memo-0930.ogg.txt"
    p = vault.parent / "raw" / "voice" / "memo-0930.ogg.txt"
    assert p.read_text() == "I felt sharp until three."
    err = run(vault, "transcript-capture", "memo-0930.ogg", stdin="rerun", expect_ok=False)
    assert "immutable" in err
    assert p.read_text() == "I felt sharp until three."          # untouched


def test_invalid_utf8_stdin_fails_before_creating_the_file(vault):
    """Re-review finding: under a C-locale service, surrogateescape lets bad
    bytes through stdin.read() and the crash used to land AFTER open('x') —
    leaving a zero-byte 'immutable' squatter that blocks clean retries."""
    r = subprocess.run(
        [sys.executable, str(HEALTH), "transcript-capture", "memo9.ogg"],
        env={**os.environ, "HEALTH_DB": str(vault),
             "PYTHONIOENCODING": "utf-8:surrogateescape"},
        input=b"good \xff bad", capture_output=True)
    assert r.returncode != 0 and b"not valid UTF-8" in r.stderr
    assert not (vault.parent / "raw" / "voice" / "memo9.ogg.txt").exists()
    # a clean retry under the SAME name must now succeed
    ok = run(vault, "transcript-capture", "memo9.ogg", stdin="clean text")
    assert ok["ok"]


def test_empty_or_dotfile_audio_name_is_refused(vault):
    for name in ("", ".", ".hidden", "-flagish"):
        err = run(vault, "transcript-capture", "--", name, stdin="x", expect_ok=False)
        assert "bad filename" in err
    assert not (vault.parent / "raw" / "voice").exists()   # nothing was created


def test_vault_escape_is_refused(vault):
    for name in ("../../etc/passwd", "..", "a/b.ogg", "x" * 200):
        err = run(vault, "transcript-capture", name, stdin="x", expect_ok=False)
        assert "bad filename" in err or "escapes" in err
    # journal date is validated ISO, so its filename cannot be hostile
    assert "--date" in run(vault, "journal-capture", "--date", "../evil", stdin="x", expect_ok=False)
