"""Phase 7: plans endpoints — program read, routine/schedule edits via a stubbed
bridge, note read/diff (real files) + note write via the bridge."""
import json
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from app import auth as auth_mod
from app import bridge, create_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "toolkit" / "SCHEMA.sql").read_text()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO routines VALUES(?,?,?,?,?,?)", [
        ("A", "Front Squat", 1, 3, 8, 60.0),
        ("A", "Overhead Press", 2, 3, 5, 30.0),
    ])
    con.execute("INSERT INTO training_schedule VALUES('Mon', 'A')")
    con.commit(); con.close()

    # vault with a plan note
    vault = tmp_path
    (vault / "personal").mkdir()
    (vault / "personal" / "plan.md").write_text("# Plan\noriginal line\n")

    calls = []
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: (calls.append((sub, list(a), k.get("stdin"))) or {"ok": True}))
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb), "VAULT_DIR": str(vault),
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_program_read(client):
    p = client.get("/api/plans/program").get_json()
    assert p["routine_names"] == ["A"]
    assert [e["exercise_title"] for e in p["routines"]["A"]] == ["Front Squat", "Overhead Press"]
    assert p["schedule"] == {"Mon": "A"}


def test_routine_set_maps_to_bridge(client):
    r = client.post("/api/plans/routine-set", json={"routine": "A", "exercise": "Front Squat", "weight": 62.5})
    assert r.status_code == 200
    sub, args, _ = client.calls[-1]
    assert sub == "routine-set" and args[:2] == ["A", "Front Squat"] and "--weight" in args and "62.5" in args


def test_routine_set_rejects_flaglike(client):
    assert client.post("/api/plans/routine-set", json={"routine": "-x", "exercise": "y"}).status_code == 400
    assert client.post("/api/plans/routine-set", json={"routine": "A", "exercise": "--sets"}).status_code == 400
    assert client.calls == []


def test_routine_set_range_validation(client):
    assert client.post("/api/plans/routine-set", json={"routine": "A", "exercise": "X", "sets": 99}).status_code == 400
    assert client.post("/api/plans/routine-set", json={"routine": "A", "exercise": "X", "reps": 0}).status_code == 400
    assert client.calls == []


def test_routine_remove_and_undo(client):
    assert client.post("/api/plans/routine-remove", json={"routine": "A", "exercise": "Front Squat"}).status_code == 200
    assert client.calls[-1][0] == "routine-remove"
    assert client.post("/api/plans/routine-undo", json={}).status_code == 200
    assert client.calls[-1][0] == "routine-undo"


def test_schedule_set(client):
    assert client.post("/api/plans/schedule-set", json={"weekday": "Tue", "routine": "Rest"}).status_code == 200
    assert client.calls[-1][:2] == ("schedule-set", ["Tue", "Rest"])
    assert client.post("/api/plans/schedule-set", json={"weekday": "Funday", "routine": "A"}).status_code == 400


def test_note_read(client):
    d = client.get("/api/plans/note?path=personal/plan.md").get_json()
    assert d["content"] == "# Plan\noriginal line\n"
    assert client.get("/api/plans/note?path=CLAUDE.md").status_code == 400


def test_note_diff(client):
    d = client.post("/api/plans/note-diff",
                    json={"path": "personal/plan.md", "content": "# Plan\nEDITED line\n"}).get_json()
    assert d["changed"] is True
    assert any(l.startswith("+EDITED line") for l in d["diff"])
    assert any(l.startswith("-original line") for l in d["diff"])


def test_note_write_goes_through_bridge_with_stdin(client):
    r = client.post("/api/plans/note", json={"path": "personal/plan.md", "content": "# New\n"})
    assert r.status_code == 200
    sub, args, stdin = client.calls[-1]
    assert sub == "write-note" and args == ["personal/plan.md"] and stdin == "# New\n"


def test_note_save_and_reload_use_the_configured_vault(client, tmp_path, monkeypatch):
    vault = tmp_path / "configured-vault"
    client.application.config["VAULT_DIR"] = str(vault)
    monkeypatch.setenv("HEALTH_DB", client.application.config["HEALTH_DB"])
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path / "configured-data"))
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "fallback-vault"))
    monkeypatch.setenv("HEALTH_VAULT", str(vault))

    def run_cli(command, *args, stdin=None):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "toolkit" / "health.py"), command, *args],
            input=stdin, capture_output=True, text=True, timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    monkeypatch.setattr(bridge, "run", run_cli)
    note = {"path": "personal/goals.md", "content": "# Fictional goals\nKeep walking.\n"}
    assert client.post("/api/plans/note-diff", json=note).get_json()["changed"] is True
    assert client.post("/api/plans/note", json=note).status_code == 200
    reloaded = client.get("/api/plans/note?path=personal/goals.md").get_json()
    assert reloaded["content"] == note["content"]
    assert client.post("/api/plans/note-diff", json=note).get_json()["changed"] is False
    assert (vault / "personal" / "goals.md").read_text() == note["content"]
    assert not (tmp_path / "personal" / "goals.md").exists()


def test_note_write_rejects_non_whitelisted(client):
    assert client.post("/api/plans/note", json={"path": "CLAUDE.md", "content": "x"}).status_code == 400
    assert client.calls == []


def test_note_whitelist_matches_toolkit_owner(monkeypatch):
    """The panel and toolkit must expose the same four writable personal notes."""
    from app.routes.plans import EDITABLE_NOTES
    monkeypatch.syspath_prepend(str(ROOT / "toolkit"))
    from hermes_insights.vault_notes import EDITABLE_NOTES as TOOLKIT_EDITABLE_NOTES

    assert EDITABLE_NOTES == TOOLKIT_EDITABLE_NOTES == {
        "personal/plan.md", "personal/habits.md", "personal/profile.md", "personal/goals.md",
    }


def test_plans_endpoints_require_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/plans/program").status_code == 401
    assert app.test_client().post("/api/plans/routine-undo", json={}).status_code == 401
