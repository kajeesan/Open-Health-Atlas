"""Plans & vault: edit the training program (routines / weekly schedule, with
undo) and the vault Markdown notes (with a diff preview).

Program reads go through db_read (read-only). Every edit — and every note write —
goes through the bridge to a validated health.py command. Note READS and the
diff are done by the panel directly (it has read-only vault access); the note
WRITE goes through health.py write-note (the panel can't write the vault).

task-50 item 6 (owner C3): the panel's /program editor PAGE was removed —
every route in this file stays fully live regardless. The Hermes agent
(same brain as Telegram, chat-driven) still edits the program through these
exact endpoints, and /vault-notes still uses the note routes; only the
panel's own standalone editor UI went away.
"""
import difflib
import os

from flask import Blueprint, current_app, jsonify, request

from app import bridge, db_read

bp = Blueprint("plans", __name__, url_prefix="/api/plans")

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# Mirror of health.py EDITABLE_NOTES — a test guards them against drift.
EDITABLE_NOTES = {"personal/plan.md", "personal/habits.md",
                  "personal/profile.md", "personal/goals.md"}


def _run(*args, **kw):
    try:
        return jsonify(ok=True, result=bridge.run(*args, **kw))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 400


def _clean(s):
    return (s or "").strip()


def _note_path(rel):
    """Absolute path of a whitelisted note under the vault, or None."""
    if rel not in EDITABLE_NOTES:
        return None
    vault = current_app.config.get("VAULT_DIR")
    if not vault:
        return None
    return os.path.join(vault, rel)


# --------------------------------------------------------------- program read
@bp.get("/program")
def program():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    rows = db_read.query(
        "SELECT routine_name, exercise_title, ex_order, target_sets, target_reps,"
        " target_weight_kg FROM routines ORDER BY routine_name, ex_order, exercise_title")
    routines = {}
    for r in rows:
        routines.setdefault(r["routine_name"], []).append(r)
    schedule = {r["weekday"]: r["routine_name"]
                for r in db_read.query("SELECT weekday, routine_name FROM training_schedule")}
    return jsonify(routines=routines, schedule=schedule,
                   routine_names=sorted(routines.keys()))


# --------------------------------------------------------------- program edits
@bp.post("/routine-set")
def routine_set():
    b = request.get_json(silent=True) or {}
    routine, exercise = _clean(b.get("routine")), _clean(b.get("exercise"))
    if not routine or routine.startswith("-") or not exercise or exercise.startswith("-"):
        return jsonify(ok=False, error="valid routine and exercise are required"), 400
    args = [routine, exercise]
    try:
        for key, flag, lo, hi in (("sets", "--sets", 1, 20), ("reps", "--reps", 1, 100),
                                  ("order", "--order", 1, 99)):
            if b.get(key) not in (None, ""):
                iv = int(b[key])
                if not (lo <= iv <= hi):
                    return jsonify(ok=False, error=f"{key} out of range"), 400
                args += [flag, str(iv)]
        if b.get("weight") not in (None, ""):
            args += ["--weight", str(float(b["weight"]))]
    except (TypeError, ValueError):
        return jsonify(ok=False, error="sets/reps/weight/order must be numbers"), 400
    return _run("routine-set", *args)


@bp.post("/routine-remove")
def routine_remove():
    b = request.get_json(silent=True) or {}
    routine, exercise = _clean(b.get("routine")), _clean(b.get("exercise"))
    if not routine or routine.startswith("-") or not exercise or exercise.startswith("-"):
        return jsonify(ok=False, error="valid routine and exercise are required"), 400
    return _run("routine-remove", routine, exercise)


@bp.post("/routine-undo")
def routine_undo():
    return _run("routine-undo")


@bp.post("/schedule-set")
def schedule_set():
    b = request.get_json(silent=True) or {}
    weekday, routine = _clean(b.get("weekday")), _clean(b.get("routine"))
    if weekday not in WEEKDAYS:
        return jsonify(ok=False, error="weekday must be Mon..Sun"), 400
    if not routine or routine.startswith("-"):
        return jsonify(ok=False, error="routine is required (use 'Rest' for a rest day)"), 400
    return _run("schedule-set", weekday, routine)


# --------------------------------------------------------------- vault notes
@bp.get("/note")
def note_read():
    rel = request.args.get("path", "")
    path = _note_path(rel)
    if path is None:
        return jsonify(error="not an editable note"), 400
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""   # a not-yet-created note edits as empty
    return jsonify(path=rel, content=content)


@bp.post("/note-diff")
def note_diff():
    """Unified diff of the submitted content vs the file on disk (read-only)."""
    b = request.get_json(silent=True) or {}
    rel = _clean(b.get("path"))
    path = _note_path(rel)
    if path is None:
        return jsonify(error="not an editable note"), 400
    new = b.get("content")
    if not isinstance(new, str):
        return jsonify(error="content required"), 400
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
    except FileNotFoundError:
        old = ""
    diff = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=rel + " (current)", tofile=rel + " (new)", lineterm=""))
    return jsonify(path=rel, diff=diff, changed=(old != new))


@bp.post("/note")
def note_write():
    b = request.get_json(silent=True) or {}
    rel = _clean(b.get("path"))
    if rel not in EDITABLE_NOTES:
        return jsonify(ok=False, error="not an editable note"), 400
    content = b.get("content")
    if not isinstance(content, str):
        return jsonify(ok=False, error="content required"), 400
    if len(content) > 500_000:
        return jsonify(ok=False, error="note too large"), 400
    return _run("write-note", rel, stdin=content)
