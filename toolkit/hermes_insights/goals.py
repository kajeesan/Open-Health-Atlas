"""Phase 3 goal, plan-revision, and collector provenance contracts.

This module has no implicit database path and performs no schema creation.  The
canonical writer remains :mod:`toolkit.health`, which supplies an already-open
connection after the explicit migration gate has passed.
"""

from __future__ import annotations

from datetime import date, datetime
import json
import math
import re
import sqlite3
from typing import Any, Iterable, Mapping


GOAL_CONTRACT_VERSION = "insight-goals-v1"
COLLECTOR_CONTRACT_VERSION = "source-sync-run-v1"

GOAL_KEYS = (
    "body_recomposition",
    "strength_and_muscle",
    "imbalance_correction",
    "pain_reduction",
    "cardio_improvement",
    "follow_through",
    "green_days",
    "energy_focus_mood",
)

_DIRECTIONS = {"increase", "decrease", "maintain"}
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_FEATURE_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")


class GoalError(RuntimeError):
    """Controlled Phase 3 validation/state error."""

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


def _validation(message: str) -> None:
    raise GoalError("validation_error", message, validation=True)


# Appendix B.7.  Patterns deliberately encode only the exact registered
# outcome families; they never manufacture an exercise, side, region, or test.
_GOAL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "body_recomposition": {
        "display_name": "Body recomposition",
        "allowed": (
            (re.compile(r"^body\.(?:waist_cm|wcr|body_fat_pct)$"), {"decrease"}),
            (re.compile(r"^body\.weight_kg$"), {"maintain"}),
            (re.compile(r"^body\.(?:chest_cm|arm_cm|thigh_cm|hip_cm|neck_cm)$"),
             {"increase", "maintain"}),
        ),
        "leading_feature_families": ["nutrition", "adherence", "strength", "running", "sleep"],
        "data_requirements": "two comparable body episodes for change; 30 aligned observations for association",
        "review_frequency": "monthly",
        "unsafe_interpretations": ["weight loss is not health or recomposition", "never override strength, pain, or recovery"],
    },
    "strength_and_muscle": {
        "display_name": "Strength and muscle",
        "allowed": (
            (re.compile(r"^training\.exercise\.i_[0-9a-f]{64}\.best_e1rm_kg$"), {"increase"}),
            (re.compile(r"^fitness\.test\.[a-z0-9][a-z0-9._-]{0,159}\.(?:left|right|bilateral|central)\.value$"), {"increase"}),
            (re.compile(r"^body\.(?:arm_cm|chest_cm|thigh_cm)$"), {"increase"}),
        ),
        "leading_feature_families": ["exercise", "muscle", "nutrition", "recovery", "adherence"],
        "data_requirements": "a concrete exercise, test, or circumference outcome",
        "review_frequency": "weekly plus quarterly protocol review",
        "unsafe_interpretations": ["training volume is exposure, not proof of progress"],
    },
    "imbalance_correction": {
        "display_name": "Imbalance correction",
        "allowed": (
            (re.compile(r"^fitness\.(?:test\.[a-z0-9._-]+\.side_gap|ratio\.[a-z0-9._-]+\.(?:left|right|bilateral|central)\.distance_to_band|ratio\.[a-z0-9._-]+\.side_gap)$"), {"decrease"}),
            (re.compile(r"^fitness\.athletic_axis\.balance\.score$"), {"increase"}),
        ),
        "leading_feature_families": ["side_training", "mobility", "pain"],
        "data_requirements": "a concrete comparable bilateral protocol",
        "review_frequency": "quarterly",
        "unsafe_interpretations": ["asymmetry is not a diagnosis"],
    },
    "pain_reduction": {
        "display_name": "Pain reduction",
        "allowed": ((re.compile(r"^pain\.nrs\.(?!lateral-knee\.)[a-z0-9][a-z0-9._-]*\.(?:left|right|bilateral|central)$"), {"decrease"}),),
        "leading_feature_families": ["training", "running", "recovery", "events", "timeline"],
        "data_requirements": "an existing concrete region and side",
        "review_frequency": "weekly plus event-triggered",
        "unsafe_interpretations": ["temporal evidence is not causal diagnosis or treatment advice"],
    },
    "cardio_improvement": {
        "display_name": "Cardio improvement",
        "allowed": (
            (re.compile(r"^running\.progress\.weekly_(?:duration_min|distance_km|frequency)$"), {"increase"}),
            (re.compile(r"^running\.progress\.weekly_pace_min_per_km$"), {"decrease"}),
            (re.compile(r"^fitness\.test\.run-3k\.(?:bilateral|central)\.value$"), {"decrease"}),
        ),
        "leading_feature_families": ["running", "strength", "recovery", "adherence"],
        "data_requirements": "complete workout coverage and 30 complete weeks or comparable tests",
        "review_frequency": "weekly",
        "unsafe_interpretations": ["more load is not automatically better"],
    },
    "follow_through": {
        "display_name": "Follow through",
        "allowed": (
            (re.compile(r"^adherence\.word_kept$"), {"increase"}),
            (re.compile(r"^adherence\.(?:commitment|habit)\.i_[0-9a-f]{64}\.(?:kept|done)$"), {"increase"}),
            (re.compile(r"^adherence\.timing\.(?:wake|bed|workout|dose)\.on_time$"), {"increase"}),
        ),
        "leading_feature_families": ["sleep", "wellbeing", "plans", "events", "med_timing"],
        "data_requirements": "a concrete commitment/habit/timing outcome where identity is required",
        "review_frequency": "weekly",
        "unsafe_interpretations": ["misses are data; no opaque adherence score"],
    },
    "green_days": {
        "display_name": "Green days",
        "allowed": ((re.compile(r"^subjective\.day_rating$"), {"increase"}),),
        "leading_feature_families": ["all_eligible"],
        "data_requirements": "day rating with ordinal, Green/non-Green, and Red/non-Red modes",
        "review_frequency": "weekly",
        "unsafe_interpretations": ["never override pain, recovery, medication, or safety"],
    },
    "energy_focus_mood": {
        "display_name": "Energy, focus, or mood",
        "allowed": (
            (re.compile(r"^subjective\.(?:energy|focus|mood)$"), {"increase"}),
            (re.compile(r"^subjective\.checkin\.(?:energy|focus|mood)\.(?:am|pm|eve)$"), {"increase"}),
        ),
        "leading_feature_families": ["sleep", "med_timing", "substances", "events", "food", "training", "environment"],
        "data_requirements": "one concrete daily or timed wellbeing key",
        "review_frequency": "weekly",
        "unsafe_interpretations": ["no medication dose changes or causal mental-health claim"],
    },
}

_SAFE_DEFAULTS = {
    "body_recomposition": ("body.waist_cm", "decrease"),
    "cardio_improvement": ("running.progress.weekly_duration_min", "increase"),
    "follow_through": ("adherence.word_kept", "increase"),
    "green_days": ("subjective.day_rating", "increase"),
    "energy_focus_mood": ("subjective.energy", "increase"),
}


def goal_definitions() -> list[dict[str, Any]]:
    """Return the stable public goal contract without regex implementation details."""

    result = []
    for key in GOAL_KEYS:
        spec = _GOAL_DEFINITIONS[key]
        result.append({
            "key": key,
            "display_name": spec["display_name"],
            "allowed_outcomes": [
                {"feature_pattern": pattern.pattern, "directions": sorted(directions)}
                for pattern, directions in spec["allowed"]
            ],
            "leading_feature_families": list(spec["leading_feature_families"]),
            "data_requirements": spec["data_requirements"],
            "review_frequency": spec["review_frequency"],
            "unsafe_interpretations": list(spec["unsafe_interpretations"]),
            "default": ({"outcome_key": _SAFE_DEFAULTS[key][0], "target_direction": _SAFE_DEFAULTS[key][1]}
                        if key in _SAFE_DEFAULTS else None),
        })
    return result


def validate_goal_pair(goal_key: str, outcome_key: str, direction: str,
                       *, registered_keys: Iterable[str] | None = None) -> None:
    if goal_key not in _GOAL_DEFINITIONS:
        _validation(f"goal must be one of: {', '.join(GOAL_KEYS)}")
    if not isinstance(outcome_key, str) or not _FEATURE_KEY.fullmatch(outcome_key):
        _validation("outcome must be a registered feature key")
    if direction not in _DIRECTIONS:
        _validation("direction must be increase, decrease, or maintain")
    registered_map = registered_keys if isinstance(registered_keys, Mapping) else None
    known_keys = set(registered_map) if registered_map is not None else (
        set(registered_keys) if registered_keys is not None else None)
    if known_keys is not None and outcome_key not in known_keys:
        _validation("outcome is not registered")
    for pattern, directions in _GOAL_DEFINITIONS[goal_key]["allowed"]:
        if pattern.fullmatch(outcome_key) and direction in directions:
            if (goal_key == "strength_and_muscle"
                    and outcome_key.startswith("fitness.test.")
                    and registered_map is not None):
                definition = registered_map[outcome_key]
                source = (definition.get("source", {}) if isinstance(definition, Mapping)
                          else getattr(definition, "source", {}))
                kind = ((source.get("parameters") or {}).get("kind")
                        if isinstance(source, Mapping) else None)
                if kind not in {"strength", "hold", "control"}:
                    _validation("strength goal requires a strength, hold, or control test")
            return
    _validation("outcome/direction is not allowed for this goal")


def _latest_goal_rows(c: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = c.execute("""SELECT r.* FROM insight_goal_revisions r
        WHERE NOT EXISTS (
          SELECT 1 FROM insight_goal_revisions newer
          WHERE newer.goal_key=r.goal_key AND newer.id>r.id)
        ORDER BY r.goal_key""").fetchall()
    return {row["goal_key"]: dict(row) for row in rows}


def list_goals(c: sqlite3.Connection, *, include_disabled: bool = False) -> dict[str, Any]:
    """Return effective append-only revisions plus code-owned unconfigured state."""

    latest = _latest_goal_rows(c)
    goals: list[dict[str, Any]] = []
    for definition in goal_definitions():
        key = definition["key"]
        row = latest.get(key)
        if row:
            enabled = bool(row["enabled"])
            effective = {
                **definition,
                "configured": True,
                "enabled": enabled,
                "priority": row["priority"],
                "outcome_key": row["outcome_key"],
                "target_direction": row["target_direction"],
                "note": row["note"],
                "source": row["source"],
                "revision_id": row["id"],
                "supersedes_id": row["supersedes_id"],
                "created_at": row["created_at"],
            }
        else:
            default = definition["default"]
            enabled = key == "green_days"
            effective = {
                **definition,
                "configured": False,
                "enabled": enabled,
                # The always-available base carries the neutral multiplier
                # until the owner explicitly assigns a priority revision.
                "priority": 1 if enabled else None,
                "outcome_key": default["outcome_key"] if default else None,
                "target_direction": default["target_direction"] if default else None,
                "note": None,
                "source": "code-default" if enabled else None,
                "revision_id": None,
                "supersedes_id": None,
                "created_at": None,
            }
        if include_disabled or effective["enabled"]:
            goals.append(effective)
    return {"ok": True, "contract_version": GOAL_CONTRACT_VERSION, "goals": goals}


def prepare_goal_revision(
    c: sqlite3.Connection, *, goal_key: str, enabled: int, priority: int,
    outcome_key: str | None, direction: str | None, note: str | None,
    source: str, registered_keys: Iterable[str],
) -> dict[str, Any]:
    """Validate and resolve a revision without starting or performing a write."""
    if goal_key not in _GOAL_DEFINITIONS:
        _validation(f"goal must be one of: {', '.join(GOAL_KEYS)}")
    if enabled not in (0, 1) or isinstance(enabled, bool):
        _validation("enabled must be 0 or 1")
    if goal_key == "green_days" and enabled == 0:
        _validation("green_days is the always-available base goal and cannot be disabled")
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
        _validation("priority must be between 1 and 5")
    if not isinstance(source, str) or not _TOKEN.fullmatch(source):
        _validation("source must be a bounded code token")
    if note is not None and (not isinstance(note, str) or not note.strip() or len(note) > 500):
        _validation("note must contain 1-500 characters")
    latest_row = c.execute(
        "SELECT * FROM insight_goal_revisions WHERE goal_key=? ORDER BY id DESC LIMIT 1",
        (goal_key,),
    ).fetchone()
    if (outcome_key is None) != (direction is None):
        _validation("outcome and direction are required together")
    if enabled:
        if outcome_key is None:
            _validation("enabling a goal requires outcome and direction")
    elif outcome_key is None:
        if latest_row is not None:
            outcome_key, direction = latest_row["outcome_key"], latest_row["target_direction"]
        elif goal_key in _SAFE_DEFAULTS:
            outcome_key, direction = _SAFE_DEFAULTS[goal_key]
        else:
            _validation("this goal requires a concrete outcome and direction before it can be revised")
    assert outcome_key is not None and direction is not None
    validate_goal_pair(goal_key, outcome_key, direction, registered_keys=registered_keys)
    supersedes = latest_row["id"] if latest_row is not None else None
    return {
        "goal_key": goal_key, "enabled": enabled, "priority": priority,
        "outcome_key": outcome_key, "direction": direction,
        "note": note.strip() if note else None, "source": source,
        "supersedes": supersedes,
    }


def set_goal(c: sqlite3.Connection, *, goal_key: str, enabled: int, priority: int,
             outcome_key: str | None, direction: str | None, note: str | None,
             source: str, registered_keys: Iterable[str]) -> dict[str, Any]:
    """Append one validated effective goal revision.  Caller owns transaction."""

    prepared = prepare_goal_revision(
        c, goal_key=goal_key, enabled=enabled, priority=priority,
        outcome_key=outcome_key, direction=direction, note=note,
        source=source, registered_keys=registered_keys,
    )
    c.execute("""INSERT INTO insight_goal_revisions(
        goal_key,enabled,priority,outcome_key,target_direction,note,source,supersedes_id)
        VALUES(?,?,?,?,?,?,?,?)""",
        (prepared["goal_key"], prepared["enabled"], prepared["priority"],
         prepared["outcome_key"], prepared["direction"], prepared["note"],
         prepared["source"], prepared["supersedes"]),
    )
    revision_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {
        "ok": True, "contract_version": GOAL_CONTRACT_VERSION,
        "revision_id": revision_id, "supersedes_id": prepared["supersedes"],
        "goal_key": goal_key, "enabled": bool(enabled), "priority": priority,
        "outcome_key": prepared["outcome_key"],
        "target_direction": prepared["direction"], "source": source,
    }


def canonical_plan(c: sqlite3.Connection) -> tuple[str, str]:
    """Serialize the complete current plan deterministically."""

    schedule = [dict(row) for row in c.execute(
        "SELECT weekday,routine_name FROM training_schedule ORDER BY weekday")]
    routines = [dict(row) for row in c.execute("""SELECT routine_name,exercise_title,
        ex_order,target_sets,target_reps,target_weight_kg FROM routines
        ORDER BY routine_name,exercise_title""")]
    compact = {"ensure_ascii": False, "separators": (",", ":"), "sort_keys": True}
    return json.dumps(schedule, **compact), json.dumps(routines, **compact)


def append_training_plan_revision(c: sqlite3.Connection, *, effective_from: str,
                                  source: str) -> int:
    """Append a full post-change plan snapshot inside the caller transaction."""

    try:
        if date.fromisoformat(effective_from).isoformat() != effective_from:
            raise ValueError
    except (TypeError, ValueError):
        _validation("effective_from must be canonical ISO YYYY-MM-DD")
    if not isinstance(source, str) or not _TOKEN.fullmatch(source):
        _validation("source must be a bounded code token")
    schedule_json, routines_json = canonical_plan(c)
    prior = c.execute(
        "SELECT id FROM training_plan_revisions ORDER BY id DESC LIMIT 1").fetchone()
    c.execute("""INSERT INTO training_plan_revisions(
        effective_from,schedule_json,routines_json,source,supersedes_id)
        VALUES(?,?,?,?,?)""",
        (effective_from, schedule_json, routines_json, source,
         prior["id"] if prior else None),
    )
    return int(c.execute("SELECT last_insert_rowid()").fetchone()[0])


_COLLECTOR_KEYS = {
    "source", "started_at", "completed_at", "status", "coverage_from",
    "coverage_to", "rows_seen", "rows_written", "error_code", "warning_codes",
}
_COLLECTOR_SOURCES = {"google-health", "hevy", "weather", "air"}
_COLLECTOR_STATES = {"success", "partial", "failed"}
_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _timestamp(value: object, field: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or len(value) > 40 or "T" not in value:
        _validation(f"{field} must be a bounded timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _validation(f"{field} must be a timezone-aware ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _validation(f"{field} must include a timezone")
    return value, parsed


def _nullable_date(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _validation(f"{field} must be canonical ISO YYYY-MM-DD or null")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        _validation(f"{field} must be canonical ISO YYYY-MM-DD or null")
    return value


def validate_collector_run(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _validation("collector run must be one JSON object")
    unknown, missing = set(payload) - _COLLECTOR_KEYS, _COLLECTOR_KEYS - set(payload)
    if unknown:
        _validation(f"unknown collector run key(s): {', '.join(sorted(unknown))}")
    if missing:
        _validation(f"missing collector run key(s): {', '.join(sorted(missing))}")
    source, status = payload["source"], payload["status"]
    if source not in _COLLECTOR_SOURCES:
        _validation("invalid collector source")
    if status not in _COLLECTOR_STATES:
        _validation("invalid collector status")
    started, started_dt = _timestamp(payload["started_at"], "started_at")
    completed, completed_dt = _timestamp(payload["completed_at"], "completed_at")
    if completed_dt < started_dt:
        _validation("completed_at must not precede started_at")
    coverage_from = _nullable_date(payload["coverage_from"], "coverage_from")
    coverage_to = _nullable_date(payload["coverage_to"], "coverage_to")
    if (coverage_from is None) != (coverage_to is None):
        _validation("coverage_from and coverage_to must be null or present together")
    if coverage_from is not None and coverage_from > coverage_to:
        _validation("coverage_from must not follow coverage_to")
    numbers: dict[str, int | None] = {}
    for field in ("rows_seen", "rows_written"):
        value = payload[field]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            _validation(f"{field} must be a nonnegative integer or null")
        numbers[field] = value
    error = payload["error_code"]
    if error is not None and (not isinstance(error, str) or not _CODE.fullmatch(error)):
        _validation("error_code must be a bounded code token or null")
    if status == "success" and error is not None:
        _validation("a successful run cannot have error_code")
    if status == "failed" and error is None:
        _validation("a failed run requires error_code")
    warnings = payload["warning_codes"]
    if not isinstance(warnings, list) or len(warnings) > 20:
        _validation("warning_codes must be an array of at most 20 code tokens")
    if any(not isinstance(item, str) or not _CODE.fullmatch(item) for item in warnings):
        _validation("warning_codes must contain bounded code tokens only")
    warning_codes = sorted(set(warnings))
    return {
        "source": source, "started_at": started, "completed_at": completed,
        "status": status, "coverage_from": coverage_from, "coverage_to": coverage_to,
        **numbers, "error_code": error, "warning_codes": warning_codes,
    }


def record_collector_run(c: sqlite3.Connection, payload: object) -> dict[str, Any]:
    """Append or exactly replay a secret-free collector provenance record."""

    value = validate_collector_run(payload)
    details = json.dumps({"warning_codes": value["warning_codes"]},
                         ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    stored = (
        value["source"], value["started_at"], value["completed_at"], value["status"],
        value["coverage_from"], value["coverage_to"], value["rows_seen"],
        value["rows_written"], value["error_code"], details,
    )
    existing = c.execute(
        "SELECT * FROM source_sync_runs WHERE source=? AND started_at=?",
        (value["source"], value["started_at"]),
    ).fetchone()
    if existing is not None:
        actual = tuple(existing[key] for key in (
            "source", "started_at", "completed_at", "status", "coverage_from",
            "coverage_to", "rows_seen", "rows_written", "error_code", "details_json"))
        if actual != stored:
            raise GoalError("idempotency_conflict", "collector run key was already used with different content")
        return {"ok": True, "contract_version": COLLECTOR_CONTRACT_VERSION,
                "run_id": existing["id"], "idempotent": True}
    c.execute("""INSERT INTO source_sync_runs(
        source,started_at,completed_at,status,coverage_from,coverage_to,
        rows_seen,rows_written,error_code,details_json)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", stored)
    return {"ok": True, "contract_version": COLLECTOR_CONTRACT_VERSION,
            "run_id": int(c.execute("SELECT last_insert_rowid()").fetchone()[0]),
            "idempotent": False}


def collector_freshness(c: sqlite3.Connection, source: str, *, now: datetime) -> dict[str, Any]:
    """Describe the latest run without treating provenance time as measurement time."""

    if source not in _COLLECTOR_SOURCES:
        _validation("invalid collector source")
    latest = c.execute(
        "SELECT * FROM source_sync_runs WHERE source=? ORDER BY completed_at DESC,id DESC LIMIT 1",
        (source,),
    ).fetchone()
    successful = c.execute(
        """SELECT * FROM source_sync_runs WHERE source=? AND status='success'
           ORDER BY completed_at DESC,id DESC LIMIT 1""", (source,),
    ).fetchone()
    if latest is None:
        return {"source": source, "status": "unknown", "freshness_days": None,
                "run_id": None, "completed_at": None}
    if successful is None:
        return {"source": source, "status": "unknown", "freshness_days": None,
                "run_status": latest["status"], "run_id": latest["id"],
                "completed_at": latest["completed_at"], "successful_run_id": None}
    _, completed = _timestamp(successful["completed_at"], "completed_at")
    reference = now if now.tzinfo is not None else now.replace(tzinfo=completed.tzinfo)
    hours = max(0.0, (reference.astimezone(completed.tzinfo) - completed).total_seconds() / 3600)
    freshness = hours / 24
    if hours <= 30:
        state = "fresh"
    elif hours <= 48:
        state = "late"
    else:
        state = "stale"
    return {"source": source, "status": state, "run_status": latest["status"],
            "freshness_days": round(freshness, 6), "run_id": latest["id"],
            "completed_at": successful["completed_at"],
            "successful_run_id": successful["id"],
            "coverage_from": successful["coverage_from"],
            "coverage_to": successful["coverage_to"]}
