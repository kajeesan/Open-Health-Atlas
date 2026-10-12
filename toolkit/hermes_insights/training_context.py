"""Versioned imported identities and dated, explicitly reported context."""

from datetime import date

from .contracts import canonical_json
from .provenance import sha256_id
from . import migrations


class TrainingError(ValueError):
    """Expose a stable validation code at command and evidence boundaries."""

    validation = True

    def __init__(self, code):
        super().__init__(code)
        self.code = code


CONTEXT_KEYS = {
    "exercise": {"setup", "side", "pain_limited"},
    "fitness": {"setup", "pain_limited"},
    "pain": {"location", "provoking_movement"},
    "history": {"history_note"},
}


def record_source(conn, source_key, payload):
    """Retain a source revision without multiplying repeated imports."""
    fingerprint = sha256_id(payload)
    conn.execute("""INSERT OR IGNORE INTO exercise_source_revisions
        (source_key,fingerprint,payload_json) VALUES(?,?,?)""",
        (source_key, fingerprint, canonical_json(payload)))
    return conn.execute("""SELECT id FROM exercise_source_revisions
        WHERE source_key=? AND fingerprint=?""", (source_key, fingerprint)).fetchone()[0]


def import_set_source(conn, set_id, workout, exercise, raw_set, exercise_index, set_index):
    """Link a current set to its immutable provider revision inside the import transaction."""
    workout_id = workout.get("id")
    template_id = exercise.get("exercise_template_id")
    if template_id and not conn.execute(
            "SELECT 1 FROM exercise_template_sources WHERE template_id=?", (template_id,)).fetchone():
        import_template_source(conn, {"id": template_id, "title": exercise.get("title")})
    payload = {
        "workout_id": workout_id, "template_id": template_id,
        "routine_id": workout.get("routine_id"),
        "workout_start": workout.get("start_time"),
        "exercise_index": exercise.get("index", exercise_index),
        "exercise_title": exercise.get("title"),
        "exercise_notes": exercise.get("notes"),
        "set": raw_set,
        "working_ordinal": sum(item.get("type") != "warmup"
                               for item in (exercise.get("sets") or [])[:set_index + 1]),
        "workout_metadata": {key: value for key, value in workout.items() if key != "exercises"},
        "exercise_metadata": {key: value for key, value in exercise.items() if key != "sets"},
    }
    if workout_id and template_id:
        source_key = "hevy:set:" + canonical_json([
            workout_id, payload["exercise_index"], template_id,
            raw_set["index"] if raw_set.get("index") is not None else set_index,
        ])
        payload["identity_quality"] = "provider"
    else:
        source_key = "hevy:unidentified:" + sha256_id(payload)
        payload["identity_quality"] = "content_only"
    revision_id = record_source(conn, source_key, payload)
    conn.execute("INSERT INTO hevy_set_sources(set_id,revision_id) VALUES(?,?)",
                 (set_id, revision_id))


def import_template_source(conn, template):
    """Keep template identities, equipment categories and custom status as supplied."""
    if not isinstance(template, dict):
        raise TrainingError("invalid_template_payload")
    if not template.get("id"):
        return
    if not isinstance(template["id"], str):
        raise TrainingError("invalid_template_identity")
    revision_id = record_source(conn, "hevy:template:" + template["id"], template)
    conn.execute("""INSERT INTO exercise_template_sources(template_id,revision_id) VALUES(?,?)
        ON CONFLICT(template_id) DO UPDATE SET revision_id=excluded.revision_id""",
        (template["id"], revision_id))


def link_fitness_source(conn, fitness_id, workout, exercise, raw_set, body_mass=None):
    """Preserve the exact imported parent, even after a later import corrects that set."""
    exercise_index = next(i for i, item in enumerate(workout["exercises"]) if item is exercise)
    set_index = next(i for i, item in enumerate(exercise["sets"]) if item is raw_set)
    source_key = "hevy:set:" + canonical_json([
        workout["id"], exercise.get("index", exercise_index), exercise["exercise_template_id"],
        raw_set["index"] if raw_set.get("index") is not None else set_index,
    ])
    parent = conn.execute("""SELECT r.id FROM exercise_source_revisions r
        JOIN hevy_set_sources s ON s.revision_id=r.id WHERE r.source_key=?""", (source_key,)).fetchone()
    if parent is None:
        raise TrainingError("missing_quarterly_source_parent")
    body_parent = record_source(conn, "body_metrics:" + str(body_mass["id"]), dict(body_mass)) if body_mass is not None else None
    conn.execute("""INSERT OR IGNORE INTO fitness_source_revisions
        (fitness_id,source_revision_id,body_mass_revision_id) VALUES(?,?,?)""",
        (fitness_id, parent["id"], body_parent))


def unique_workouts(workouts):
    """Deduplicate identical pagination repeats, rejecting conflicting provider identities."""
    if not isinstance(workouts, list):
        raise TrainingError("invalid_workout_payload")
    seen, result = {}, []
    for workout in workouts:
        if (not isinstance(workout, dict) or
                (workout.get("exercises") is not None and not isinstance(workout["exercises"], list))):
            raise TrainingError("invalid_workout_payload")
        if workout.get("id") is not None and not isinstance(workout["id"], str):
            raise TrainingError("invalid_workout_identity")
        exercise_indices = set()
        for ordinal, exercise in enumerate(workout.get("exercises") or []):
            if (not isinstance(exercise, dict) or
                    (exercise.get("sets") is not None and not isinstance(exercise["sets"], list))):
                raise TrainingError("invalid_exercise_payload")
            if exercise.get("exercise_template_id") is not None and not isinstance(exercise["exercise_template_id"], str):
                raise TrainingError("invalid_template_identity")
            exercise_index = exercise.get("index", ordinal)
            if isinstance(exercise_index, bool) or not isinstance(exercise_index, int) or exercise_index < 0:
                raise TrainingError("invalid_exercise_index")
            if exercise_index in exercise_indices:
                raise TrainingError("duplicate_exercise_index")
            exercise_indices.add(exercise_index)
            set_indices = set()
            for position, raw_set in enumerate(exercise.get("sets") or []):
                if not isinstance(raw_set, dict):
                    raise TrainingError("invalid_set_payload")
                set_index = raw_set.get("index", position)
                if set_index is None:
                    set_index = position
                if isinstance(set_index, bool) or not isinstance(set_index, int) or set_index < 0:
                    raise TrainingError("invalid_set_index")
                if set_index in set_indices:
                    raise TrainingError("duplicate_set_index")
                set_indices.add(set_index)
        key = workout.get("id") or sha256_id(workout)
        fingerprint = sha256_id(workout)
        if key in seen:
            if seen[key] != fingerprint:
                raise TrainingError("conflicting_workout_identity")
            continue
        seen[key] = fingerprint
        result.append(workout)
    return result


def context_rows(conn, through):
    """Read immutable answers through the date; resolution applies correction intervals."""
    rows = conn.execute("""WITH RECURSIVE relevant(id) AS (
          SELECT id FROM training_context_revisions WHERE effective_from<=?
          UNION
          SELECT r.supersedes_id FROM training_context_revisions r
          JOIN relevant child ON child.id=r.id WHERE r.supersedes_id IS NOT NULL)
        SELECT r.* FROM training_context_revisions r JOIN relevant ON relevant.id=r.id
        ORDER BY r.id""", (through,)).fetchmany(10_001)
    if len(rows) > 10_000:
        raise TrainingError("evidence_too_large")
    return [dict(row) for row in rows]


def resolve_context(rows, kind, key, day):
    """Resolve reported values on a measurement date, keeping skipped answers unknown."""
    result = {}
    applicable = [row for row in rows if row["subject_kind"] == kind
                  and row["subject_key"] == str(key) and row["effective_from"] <= day
                  and (row["effective_to"] is None or day <= row["effective_to"])]
    by_id = {row["id"]: row for row in rows}
    superseded = set()
    for row in applicable:
        parent = row["supersedes_id"]
        while parent is not None and parent not in superseded:
            superseded.add(parent)
            parent = by_id.get(parent, {}).get("supersedes_id")
    for row in sorted(applicable, key=lambda item: (item["effective_from"], item["id"])):
        if row["id"] not in superseded:
            result[row["context_key"]] = row
    return result


def capture_context(conn, *, capture_id, subject_kind, subject_key, context_key,
                    effective_from, effective_to=None, status="answered", value=None,
                    supersedes_id=None):
    """Append a validated answer or correction. Retries must have identical content.

    The caller owns the transaction. No inferred answer or default laterality is stored.
    """
    migrations.require_version(conn, 8)
    if (not isinstance(subject_kind, str) or not isinstance(context_key, str)
            or subject_kind not in CONTEXT_KEYS or context_key not in CONTEXT_KEYS[subject_kind]):
        raise TrainingError("invalid_context_key")
    for text in (capture_id, subject_key):
        if not isinstance(text, str) or not text.strip() or text != text.strip() or len(text) > 200:
            raise TrainingError("invalid_context_identity")
    if subject_kind in {"fitness", "pain"} and (
            not subject_key.isascii() or not subject_key.isdigit()
            or str(int(subject_key)) != subject_key or int(subject_key) < 1):
        raise TrainingError("invalid_context_identity")
    for day in (effective_from, effective_to):
        if day is not None:
            try:
                if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                    raise TrainingError("invalid_context_date")
            except ValueError as exc:
                raise TrainingError("invalid_context_date") from exc
    if effective_from is None or (effective_to is not None and effective_to < effective_from):
        raise TrainingError("invalid_context_interval")
    if not isinstance(status, str) or status not in {"answered", "skipped"}:
        raise TrainingError("invalid_context_status")
    if status == "skipped":
        if value is not None:
            raise TrainingError("skipped_answer_must_be_unknown")
    elif not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise TrainingError("invalid_context_value")
    if status == "answered" and context_key == "side" and value not in {"left", "right", "bilateral"}:
        raise TrainingError("invalid_side")
    if status == "answered" and context_key == "pain_limited" and value not in {"yes", "no"}:
        raise TrainingError("invalid_pain_limited")
    if supersedes_id is not None and (
            isinstance(supersedes_id, bool) or not isinstance(supersedes_id, int) or supersedes_id < 1):
        raise TrainingError("invalid_correction_identity")
    fields = (capture_id, subject_kind, subject_key, context_key, effective_from,
              effective_to, status, value, supersedes_id)
    existing = conn.execute("SELECT * FROM training_context_revisions WHERE capture_id=?",
                            (capture_id,)).fetchone()
    names = ("capture_id", "subject_kind", "subject_key", "context_key", "effective_from",
             "effective_to", "status", "value", "supersedes_id")
    if existing:
        if tuple(existing[name] for name in names) != fields:
            raise TrainingError("capture_id_conflict")
        return {"ok": True, "id": existing["id"], "unchanged": True}
    if subject_kind in {"fitness", "pain"}:
        table = "fitness_tests" if subject_kind == "fitness" else "pain_log"
        if not conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND voided=0", (subject_key,)).fetchone():
            raise TrainingError("unknown_context_subject")
    elif subject_kind == "exercise":
        if not conn.execute("SELECT 1 FROM exercise_source_revisions WHERE source_key=?",
                            ("hevy:template:" + subject_key,)).fetchone():
            raise TrainingError("unknown_exercise_template")
    elif subject_key != "personal":
        raise TrainingError("invalid_history_subject")
    if supersedes_id is not None:
        old = conn.execute("SELECT * FROM training_context_revisions WHERE id=?", (supersedes_id,)).fetchone()
        if old is None or any(old[name] != val for name, val in (
                ("subject_kind", subject_kind), ("subject_key", subject_key), ("context_key", context_key))):
            raise TrainingError("invalid_correction_subject")
        if conn.execute("SELECT 1 FROM training_context_revisions WHERE supersedes_id=?", (supersedes_id,)).fetchone():
            raise TrainingError("context_already_corrected")
    row = conn.execute("""INSERT INTO training_context_revisions
        (capture_id,subject_kind,subject_key,context_key,effective_from,effective_to,
         status,value,supersedes_id) VALUES(?,?,?,?,?,?,?,?,?)""", fields)
    return {"ok": True, "id": row.lastrowid, "unchanged": False}
