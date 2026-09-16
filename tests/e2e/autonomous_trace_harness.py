"""Deterministic Phase 8 fixture and trace replay helpers.

The harness uses a fresh temporary current-schema database for every trace. It
calls the production registry, adapters, readiness, association, orchestration,
and conversation contracts directly.  Only collector completeness, model,
sender, and broker boundaries are synthetic, and those boundaries are recorded
explicitly in each trace.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from app import auth as panel_auth
from app import bridge as panel_bridge
from app import create_app
from app.routes import chat as chat_routes


PANEL_ROOT = Path(__file__).resolve().parents[2]
TOOLKIT_ROOT = PANEL_ROOT / "toolkit"
FIXTURE_ROOT = TOOLKIT_ROOT / "tests" / "fixtures" / "autonomous_insights"
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))

import health  # noqa: E402
from hermes_insights import (  # noqa: E402
    associations,
    frame,
    interactions,
    ledger,
    migrations,
    orchestrator,
    provenance,
    readiness,
    registry,
    synthesis,
)
from hermes_insights.adapters import pain as pain_adapter  # noqa: E402
from hermes_insights.adapters import token_from_identity_key  # noqa: E402
from hermes_insights.contracts import (  # noqa: E402
    AdapterContext,
    DateRange,
    canonical_json,
)
from hermes_insights.normalize import identity_key  # noqa: E402


TRACE_CONTRACT = "autonomous-insight-e2e-trace-v1"
FIXTURE_CONTRACT = "autonomous-insight-e2e-fixture-v1"
FIXED_NOW = datetime(2026, 7, 23, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
MIGRATION_004_CHECKSUM = (
    "381006f8a4ea255062a9d8f922d7e30c0bd5dbfc94d47e031c2f107989718229"
)
MIGRATION_005_CHECKSUM = migrations.MIGRATION_BY_VERSION[5].checksum
EXPECTED_ENGINE_SHA256 = (
    "sha256:fe3e9e85303472d6a56555f1b8c9775a1fe712fe2ea927e48c8d029e2f36f70c"
)
INSIGHTS_JS_PATH = PANEL_ROOT / "app" / "static" / "js" / "insights.js"
INSIGHTS_JS_SHA256 = (
    "sha256:" + hashlib.sha256(INSIGHTS_JS_PATH.read_bytes()).hexdigest()
)


class TraceReplayError(AssertionError):
    """Raised when a committed trace cannot be reproduced exactly."""


def sha256_json(value: Any) -> str:
    """Hash one canonical JSON value using the repository's JSON contract."""

    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_fixture(path: Path) -> dict[str, Any]:
    """Read one closed, canonical fixture object."""

    raw = path.read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("fixture_contract") != FIXTURE_CONTRACT:
        raise TraceReplayError(f"invalid fixture contract: {path.name}")
    if canonical_json(value) + "\n" != raw:
        raise TraceReplayError(f"fixture is not canonical JSON: {path.name}")
    return value


def fixture_paths() -> list[Path]:
    """Return the ten numbered scenario fixtures in immutable order."""

    return sorted(
        path
        for path in FIXTURE_ROOT.glob("[0-9][0-9]-*.json")
        if path.name != "manifest.json"
        and not path.name.endswith(".trace.json")
    )


def create_current_database(
    path: Path,
) -> tuple[sqlite3.Connection, dict[str, Any]]:
    """Create a fresh schema and exercise the complete migration ledger."""

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript((TOOLKIT_ROOT / "SCHEMA.sql").read_text(encoding="utf-8"))
    connection.commit()
    connection.close()
    result = migrations.migrate(
        str(path),
        target=migrations.AUTONOMOUS_SCHEMA_VERSION,
        expected_from=0,
        code_version="phase8-synthetic-fixture",
    )
    if result["to_version"] != migrations.AUTONOMOUS_SCHEMA_VERSION:
        raise TraceReplayError("fresh fixture database did not reach current schema")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise TraceReplayError("fresh fixture database failed quick_check")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise TraceReplayError("fresh fixture database failed foreign_key_check")
    return connection, result


def fixture_context(*, running_completeness: bool = False) -> AdapterContext:
    """Return production catalogs/functions with a fixed local clock.

    ``fixture-workouts`` is deliberately injected only for scenarios whose
    manifest names the collector-completeness fake.  Production does not claim
    that its current Google Health collector covers workouts.
    """

    base = health._phase3_context()
    constants = dict(base.constants)
    if running_completeness:
        constants["RUN_COMPLETENESS_SOURCES"] = {"fixture-workouts"}
    functions = dict(base.functions)
    functions["now"] = lambda: FIXED_NOW
    return AdapterContext(
        today=FIXED_NOW.date(),
        timezone="Europe/Paris",
        constants=constants,
        functions=functions,
    )


def _bounded(value: Mapping[str, str]) -> DateRange:
    return DateRange(
        start=date.fromisoformat(value["from"]),
        end=date.fromisoformat(value["to"]),
        kind="bounded",
    )


def _rows_manifest(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [dict(row) for row in rows]
    return {
        "row_count": len(normalized),
        "tables": dict(sorted(Counter(row["table"] for row in normalized).items())),
        "rows_sha256": sha256_json(normalized),
    }


def _insert_subjective(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    day: str,
    *,
    day_rating: int | None = None,
    mood: int | None = None,
) -> None:
    connection.execute(
        """INSERT INTO subjective_daily(date,day_rating,mood,source)
           VALUES(?,?,?,'import')""",
        (day, day_rating, mood),
    )
    rows.append({
        "table": "subjective_daily",
        "date": day,
        "day_rating": day_rating,
        "mood": mood,
        "source": "import",
    })


def _insert_completeness(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    day: str,
    scope: str,
    entity_key_value: str,
    *,
    source: str = "import",
) -> None:
    connection.execute(
        """INSERT INTO capture_completeness_revisions(
             date,scope,entity_key,state,explicit_none,source)
           VALUES(?,?,?,'complete',0,?)""",
        (day, scope, entity_key_value, source),
    )
    rows.append({
        "table": "capture_completeness_revisions",
        "date": day,
        "scope": scope,
        "entity_key": entity_key_value,
        "state": "complete",
        "explicit_none": 0,
        "source": source,
    })


def _rating_for_binary(exposed: bool, pair_index: int) -> int:
    """Return a repeating Green/yellow/red pattern with a strong association."""

    slot = pair_index % 10
    if exposed:
        return 3 if slot < 8 else (2 if slot == 8 else 1)
    return 3 if slot < 2 else (2 if slot % 2 == 0 else 1)


def _seed_food(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = date.fromisoformat(fixture["range"]["from"])
    days = int(fixture["seed"]["days"])
    label = fixture["seed"]["food_label"]
    label_variant = fixture["seed"]["food_label_normalized_variant"]
    null_label = fixture["seed"]["null_control_food_label"]
    entity = identity_key("food", label)
    variant_entity = identity_key("food", label_variant)
    null_entity = identity_key("food", null_label)
    if variant_entity != entity or null_entity == entity:
        raise TraceReplayError("food identity normalization fixture drifted")
    for offset in range(days):
        current = start + timedelta(days=offset)
        day = current.isoformat()
        exposed = offset % 2 == 0
        pair_index = offset // 2
        source_era = "manual" if offset < days // 2 else "import"
        _insert_subjective(
            connection,
            rows,
            day,
            day_rating=_rating_for_binary(exposed, pair_index),
        )
        _insert_completeness(
            connection,
            rows,
            day,
            "food_identity",
            entity,
            source=source_era,
        )
        _insert_completeness(
            connection,
            rows,
            day,
            "food_identity",
            null_entity,
            source="import",
        )
        if exposed:
            observed_label = label if pair_index % 2 == 0 else label_variant
            connection.execute(
                """INSERT INTO nutrition_log(
                     date,food_name,grams,kcal,protein_g,time,meal_type,source)
                   VALUES(?,?,180,320,18,'12:00','lunch',?)""",
                (day, observed_label, source_era),
            )
            rows.append({
                "table": "nutrition_log",
                "date": day,
                "food_name": observed_label,
                "grams": 180,
                "kcal": 320,
                "protein_g": 18,
                "time": "12:00",
                "meal_type": "lunch",
                "source": source_era,
            })
        null_exposed = pair_index % 2 == 0
        if null_exposed:
            connection.execute(
                """INSERT INTO nutrition_log(
                     date,food_name,grams,kcal,protein_g,time,meal_type,source)
                   VALUES(?,?,40,80,4,'15:00','snack',
                          'import')""",
                (day, null_label),
            )
            rows.append({
                "table": "nutrition_log",
                "date": day,
                "food_name": null_label,
                "grams": 40,
                "kcal": 80,
                "protein_g": 4,
                "time": "15:00",
                "meal_type": "snack",
                "source": "import",
            })
    connection.commit()
    return rows, {
        "identity_key": entity,
        "normalized_variant_identity_key": variant_entity,
        "null_identity_key": null_entity,
        "feature_key": (
            f"food.named.{token_from_identity_key(entity)}.occurred"
        ),
        "null_feature_key": (
            f"food.named.{token_from_identity_key(null_entity)}.occurred"
        ),
        "target_lag_days": 0,
        "original_labels": [label, label_variant],
        "source_eras": ["manual", "import"],
    }


def _seed_social(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = date.fromisoformat(fixture["range"]["from"])
    days = int(fixture["seed"]["days"])
    label = fixture["seed"]["person_label"]
    entity = identity_key("person", label)
    for offset in range(days):
        current = start + timedelta(days=offset)
        day = current.isoformat()
        exposed = offset % 2 == 0
        _insert_subjective(
            connection,
            rows,
            day,
            mood=5 if exposed else 2,
        )
        _insert_completeness(connection, rows, day, "social", entity)
        if exposed:
            connection.execute(
                """INSERT INTO event_exposures(
                     date,time,category,entity_label,entity_key,duration_min,
                     intensity,valence,source)
                   VALUES(?,'18:00','social',?,?,90,3,1,'import')""",
                (day, label, entity),
            )
            rows.append({
                "table": "event_exposures",
                "date": day,
                "time": "18:00",
                "category": "social",
                "entity_label": label,
                "entity_key": entity,
                "duration_min": 90,
                "intensity": 3,
                "valence": 1,
                "source": "import",
            })
    connection.commit()
    return rows, {
        "identity_key": entity,
        "feature_prefix": "event.social.entity.",
        "feature_suffix": ".occurred",
        "target_lag_days": 0,
    }


def _seed_workout(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    outcome_start = date.fromisoformat(fixture["range"]["from"])
    days = int(fixture["seed"]["days"])
    workout_type = fixture["seed"]["workout_type"]
    coverage_start = outcome_start - timedelta(days=1)
    coverage_end = outcome_start + timedelta(days=days - 1)
    connection.execute(
        """INSERT INTO source_sync_runs(
             source,started_at,completed_at,status,coverage_from,coverage_to,
             rows_seen,rows_written,details_json)
           VALUES(
             'fixture-workouts','2026-03-31T23:00:00+02:00',
             '2026-07-23T11:00:00+02:00','success',?,?,50,50,'{}')""",
        (coverage_start.isoformat(), coverage_end.isoformat()),
    )
    rows.append({
        "table": "source_sync_runs",
        "source": "fixture-workouts",
        "started_at": "2026-03-31T23:00:00+02:00",
        "completed_at": "2026-07-23T11:00:00+02:00",
        "status": "success",
        "coverage_from": coverage_start.isoformat(),
        "coverage_to": coverage_end.isoformat(),
        "rows_seen": 50,
        "rows_written": 50,
        "details_json": "{}",
    })
    for offset in range(days):
        outcome_day = outcome_start + timedelta(days=offset)
        exposure_day = outcome_day - timedelta(days=1)
        exposed = offset % 2 == 0
        pair_index = offset // 2
        _insert_subjective(
            connection,
            rows,
            outcome_day.isoformat(),
            day_rating=_rating_for_binary(exposed, pair_index),
        )
        if exposed:
            connection.execute(
                """INSERT INTO workouts(date,type,minutes,kcal,km,source)
                   VALUES(?,?,35,280,5,'fixture-workouts')""",
                (exposure_day.isoformat(), workout_type),
            )
            rows.append({
                "table": "workouts",
                "date": exposure_day.isoformat(),
                "type": workout_type,
                "minutes": 35,
                "kcal": 280,
                "km": 5,
                "source": "fixture-workouts",
            })
    connection.commit()
    return rows, {
        "identity_key": None,
        "feature_prefix": "cardio.type.",
        "feature_suffix": ".session",
        "target_lag_days": 1,
    }


def _seed_pair(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = date.fromisoformat(fixture["range"]["from"])
    food_label = fixture["seed"]["food_label"]
    person_label = fixture["seed"]["person_label"]
    food_entity = identity_key("food", food_label)
    person_entity = identity_key("person", person_label)
    per_cell_seen: defaultdict[str, int] = defaultdict(int)
    declared_cells = fixture["seed"]["cell_counts"]
    per_cell = declared_cells["neither"]
    if set(declared_cells.values()) != {per_cell}:
        raise TraceReplayError("pair fixture requires equal deterministic cells")
    declared_green = fixture["seed"]["green_counts"]
    green_targets = {
        "neither": declared_green["neither"],
        "food": declared_green["food_only"],
        "social": declared_green["social_only"],
        "both": declared_green["both"],
    }
    for offset in range(per_cell * 4):
        current = start + timedelta(days=offset)
        day = current.isoformat()
        food = offset % 4 in (1, 3)
        social = offset % 4 in (2, 3)
        cell = "both" if food and social else "food" if food else "social" if social else "neither"
        rank = per_cell_seen[cell]
        per_cell_seen[cell] += 1
        green = (rank * 7) % per_cell < green_targets[cell]
        rating = 3 if green else (2 if rank % 2 == 0 else 1)
        _insert_subjective(connection, rows, day, day_rating=rating)
        _insert_completeness(connection, rows, day, "food_identity", food_entity)
        _insert_completeness(connection, rows, day, "social", person_entity)
        if food:
            connection.execute(
                """INSERT INTO nutrition_log(
                     date,food_name,grams,kcal,protein_g,time,meal_type,source)
                   VALUES(?,?,160,300,16,'12:00','lunch','import')""",
                (day, food_label),
            )
            rows.append({
                "table": "nutrition_log",
                "date": day,
                "food_name": food_label,
                "grams": 160,
                "kcal": 300,
                "protein_g": 16,
                "time": "12:00",
                "meal_type": "lunch",
                "source": "import",
            })
        if social:
            connection.execute(
                """INSERT INTO event_exposures(
                     date,time,category,entity_label,entity_key,duration_min,
                     intensity,valence,source)
                   VALUES(?,'18:00','social',?,?,75,3,1,'import')""",
                (day, person_label, person_entity),
            )
            rows.append({
                "table": "event_exposures",
                "date": day,
                "time": "18:00",
                "category": "social",
                "entity_label": person_label,
                "entity_key": person_entity,
                "duration_min": 75,
                "intensity": 3,
                "valence": 1,
                "source": "import",
            })
    exact_cells = {
        "neither": per_cell_seen["neither"],
        "food_only": per_cell_seen["food"],
        "social_only": per_cell_seen["social"],
        "both": per_cell_seen["both"],
    }
    if exact_cells != declared_cells:
        raise TraceReplayError(
            f"pair fixture generated {exact_cells}, declared {declared_cells}"
        )
    connection.commit()
    return rows, {
        "identity_keys": [food_entity, person_entity],
        "feature_patterns": [
            ("food.named.", ".occurred"),
            ("event.social.entity.", ".occurred"),
        ],
    }


def _seed_running_timeline(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    first_run = date.fromisoformat(fixture["seed"]["first_run"])
    restart = date.fromisoformat(fixture["seed"]["restart"])
    pain_day = date.fromisoformat(fixture["seed"]["pain_observation"])
    connection.execute(
        """INSERT INTO source_sync_runs(
             source,started_at,completed_at,status,coverage_from,coverage_to,
             rows_seen,rows_written,details_json)
           VALUES(
             'fixture-workouts','2026-04-01T00:00:00+02:00',
             '2026-05-01T00:00:00+02:00','success',?,?,2,2,'{}')""",
        (first_run.isoformat(), pain_day.isoformat()),
    )
    rows.append({
        "table": "source_sync_runs",
        "source": "fixture-workouts",
        "started_at": "2026-04-01T00:00:00+02:00",
        "completed_at": "2026-05-01T00:00:00+02:00",
        "status": "success",
        "coverage_from": first_run.isoformat(),
        "coverage_to": pain_day.isoformat(),
        "rows_seen": 2,
        "rows_written": 2,
        "details_json": "{}",
    })
    for current, minutes, km in ((first_run, 30, 4), (restart, 24, 3)):
        connection.execute(
            """INSERT INTO workouts(date,type,minutes,kcal,km,source)
               VALUES(?,'running',?,240,?,'fixture-workouts')""",
            (current.isoformat(), minutes, km),
        )
        rows.append({
            "table": "workouts",
            "date": current.isoformat(),
            "type": "running",
            "minutes": minutes,
            "kcal": 240,
            "km": km,
            "source": "fixture-workouts",
        })
    connection.execute(
        """INSERT INTO pain_log(
             date,region,side,intensity,source,reported_onset_date,onset_precision)
           VALUES(?,'anterior-knee','left',3,'import',NULL,NULL)""",
        (pain_day.isoformat(),),
    )
    rows.append({
        "table": "pain_log",
        "date": pain_day.isoformat(),
        "region": "anterior-knee",
        "side": "left",
        "intensity": 3,
        "source": "import",
        "reported_onset_date": None,
        "onset_precision": None,
    })
    connection.commit()
    return rows, {}


def _seed_quarterly(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    episodes = [
        ("2025-10-01", "left", 55.0),
        ("2025-10-01", "right", 65.0),
        ("2026-01-01", "left", 58.0),
        ("2026-01-01", "right", 70.0),
        ("2026-04-01", "left", 60.0),
        ("2026-04-01", "right", 74.0),
        ("2026-07-01", "left", 62.0),
        ("2026-07-01", "right", 78.0),
    ]
    for day, side, load in episodes:
        connection.execute(
            """INSERT INTO fitness_tests(
                 date,movement,side,load_kg,reps,equipment_note,source)
               VALUES(?,'leg-extension',?,?,8,'fixture-machine','import')""",
            (day, side, load),
        )
        rows.append({
            "table": "fitness_tests",
            "date": day,
            "movement": "leg-extension",
            "side": side,
            "load_kg": load,
            "reps": 8,
            "equipment_note": "fixture-machine",
            "source": "import",
        })
        if side == "left":
            connection.execute(
                """INSERT INTO pain_log(date,region,side,intensity,source)
                   VALUES(?,'anterior-knee','left',?,'import')""",
                (day, 2 + len(rows) // 4),
            )
            rows.append({
                "table": "pain_log",
                "date": day,
                "region": "anterior-knee",
                "side": "left",
                "intensity": 2 + len(rows) // 4,
                "source": "import",
            })
    for movement, side, field, value in (
        ("balance-stand", "left", "seconds", 38.0),
        ("balance-stand", "right", "seconds", 42.0),
        ("ankle-df-wall", "left", "degrees", 34.0),
        ("ankle-df-wall", "right", "degrees", 37.0),
    ):
        connection.execute(
            f"""INSERT INTO fitness_tests(
                  date,movement,side,{field},equipment_note,source)
                VALUES('2026-07-01',?,?,?,'fixture-protocol','import')""",
            (movement, side, value),
        )
        rows.append({
            "table": "fitness_tests",
            "date": "2026-07-01",
            "movement": movement,
            "side": side,
            field: value,
            "equipment_note": "fixture-protocol",
            "source": "import",
        })
    connection.execute(
        """INSERT INTO hevy_sets(
             date,workout_title,exercise_title,set_index,set_type,
             weight_kg,reps,source)
           VALUES(
             '2026-07-01','Fixture Lower','Leg Extension',1,'normal',
             60,8,'hevy')"""
    )
    rows.append({
        "table": "hevy_sets",
        "date": "2026-07-01",
        "workout_title": "Fixture Lower",
        "exercise_title": "Leg Extension",
        "set_index": 1,
        "set_type": "normal",
        "weight_kg": 60,
        "reps": 8,
        "source": "hevy",
    })
    connection.execute(
        """INSERT INTO body_metrics(date,thigh_cm,source)
           VALUES('2026-07-01',58,'import')"""
    )
    rows.append({
        "table": "body_metrics",
        "date": "2026-07-01",
        "thigh_cm": 58,
        "source": "import",
    })
    connection.commit()
    return rows, {
        "feature_key": "fitness.test.leg-extension.side_gap",
        "outcome_key": "pain.nrs.anterior-knee.left",
    }


def _seed_insufficient(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return [], {
        "feature_key": fixture["seed"]["feature_key"],
        "outcome_key": fixture["seed"]["outcome_key"],
    }


def _seed_reversal(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Seed three disjoint windows; the third reverses the first two."""

    rows: list[dict[str, Any]] = []
    label = fixture["seed"]["food_label"]
    entity = identity_key("food", label)
    ranges = fixture["seed"]["windows"]
    for window_index, window in enumerate(ranges):
        start = date.fromisoformat(window["from"])
        days = (date.fromisoformat(window["to"]) - start).days + 1
        for offset in range(days):
            current = start + timedelta(days=offset)
            day = current.isoformat()
            exposed = offset % 2 == 0
            pair_index = offset // 2
            positive = window_index < 2
            rating = _rating_for_binary(exposed if positive else not exposed, pair_index)
            _insert_subjective(connection, rows, day, day_rating=rating)
            _insert_completeness(connection, rows, day, "food_identity", entity)
            if exposed:
                connection.execute(
                    """INSERT INTO nutrition_log(
                         date,food_name,grams,kcal,protein_g,time,meal_type,source)
                       VALUES(?,?,150,290,15,'12:00','lunch','import')""",
                    (day, label),
                )
                rows.append({
                    "table": "nutrition_log",
                    "date": day,
                    "food_name": label,
                    "grams": 150,
                    "kcal": 290,
                    "protein_g": 15,
                    "time": "12:00",
                    "meal_type": "lunch",
                    "source": "import",
                })
    connection.commit()
    return rows, {
        "identity_key": entity,
        "feature_prefix": "food.named.",
        "feature_suffix": ".occurred",
        "windows": ranges,
    }


def _seed_weekly(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = date(2026, 3, 1)
    label = fixture["seed"]["food_label"]
    entity = identity_key("food", label)
    for offset in range(120):
        day = (start + timedelta(days=offset)).isoformat()
        exposed = offset % 2 == 0
        _insert_subjective(
            connection,
            rows,
            day,
            day_rating=_rating_for_binary(exposed, offset // 2),
            mood=3,
        )
        _insert_completeness(connection, rows, day, "food_identity", entity)
        if exposed:
            connection.execute(
                """INSERT INTO nutrition_log(
                     date,food_name,grams,kcal,protein_g,time,meal_type,source)
                   VALUES(?,?,170,310,17,'12:00','lunch','import')""",
                (day, label),
            )
            rows.append({
                "table": "nutrition_log",
                "date": day,
                "food_name": label,
                "grams": 170,
                "kcal": 310,
                "protein_g": 17,
                "time": "12:00",
                "meal_type": "lunch",
                "source": "import",
            })
    connection.commit()
    return rows, {
        "identity_key": entity,
        "feature_prefix": "food.named.",
        "feature_suffix": ".occurred",
    }


def _seed_conversations(
    connection: sqlite3.Connection,
    fixture: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Conversation state belongs to panel.db and is exercised by the E2E test;
    # the health fixture remains intentionally empty.
    return [], {}


SEEDERS = {
    "food_green_day": _seed_food,
    "social_mood": _seed_social,
    "workout_next_day": _seed_workout,
    "food_social_pair": _seed_pair,
    "running_restart_pain": _seed_running_timeline,
    "quarterly_asymmetry": _seed_quarterly,
    "insufficient_data": _seed_insufficient,
    "reversal": _seed_reversal,
    "weekly_no_novelty": _seed_weekly,
    "conversation_isolation": _seed_conversations,
}


def _registry_bundle(
    connection: sqlite3.Connection,
    context: AdapterContext,
) -> tuple[list[Any], dict[str, Any]]:
    definitions = list(registry.build_registry(connection, context))
    serialized = registry.serialize_registry(definitions)
    return definitions, serialized


def _definition(
    definitions: Iterable[Any],
    *,
    exact: str | None = None,
    prefix: str | None = None,
    suffix: str | None = None,
) -> Any:
    matches = [
        item
        for item in definitions
        if (exact is None or item.key == exact)
        and (prefix is None or item.key.startswith(prefix))
        and (suffix is None or item.key.endswith(suffix))
    ]
    if len(matches) != 1:
        keys = [item.key for item in matches]
        raise TraceReplayError(
            f"expected one registered definition exact={exact!r} "
            f"prefix={prefix!r} suffix={suffix!r}, got {keys!r}"
        )
    return matches[0]


def _compact_finding(finding: Mapping[str, Any]) -> dict[str, Any]:
    evidence = finding.get("provenance", {})
    oriented = finding["effect"].get("oriented_estimate")
    return {
        "finding_id": finding["finding_id"],
        "candidate_key": finding["candidate_key"],
        "outcome_mode": finding["outcome"]["mode"],
        "direction": (
            "positive"
            if isinstance(oriented, (int, float)) and oriented > 0
            else "negative"
            if isinstance(oriented, (int, float)) and oriented < 0
            else "null"
        ),
        "quality_tier": finding["quality"]["tier"],
        "eligible_for_hypothesis": finding["quality"]["eligible_for_hypothesis"],
        "components": [
            {
                key: component[key]
                for key in ("exposure_key", "lag_days", "window_days", "transform")
            }
            for component in finding["exposure"]["components"]
        ],
        "sample": finding["sample"],
        "effect": finding["effect"],
        "testing": finding["testing"],
        "evidence_for": finding["evidence_for"],
        "evidence_against": finding["evidence_against"],
        "confounders": finding["confounders"],
        "warnings": finding["warnings"],
        "evidence_fingerprint": evidence.get("evidence_fingerprint"),
        "source_manifest_sha256": sha256_json(evidence.get("source_manifest", [])),
    }


def _association_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    requested: DateRange,
    *,
    outcome_key: str,
    exposure_definitions: Sequence[Any],
    mode: str,
    target_lags: Sequence[int],
    pair: bool = False,
) -> dict[str, Any]:
    selected = [_definition(definitions, exact=outcome_key), *exposure_definitions]
    feature_frame = frame.build_feature_frame(
        connection,
        selected,
        requested,
        context,
        include_provenance=True,
    )
    readiness_payload = readiness.build_readiness(
        connection,
        selected,
        requested,
        context,
        outcome=outcome_key,
    )
    result = associations.analyze_outcome(
        connection,
        selected,
        requested,
        context,
        outcome_key=outcome_key,
        mode=mode,
        min_n=30,
        interactions="pairwise" if pair else "none",
        top=100,
    )
    exposure_keys = {item.key for item in exposure_definitions}
    targets = []
    for finding in result["findings"]:
        components = finding["exposure"]["components"]
        if {item["exposure_key"] for item in components} != exposure_keys:
            continue
        if sorted(item["lag_days"] for item in components) != sorted(target_lags):
            continue
        if any(item["window_days"] != 1 for item in components):
            continue
        targets.append(finding)
    target = next(
        (
            item
            for item in targets
            if item["outcome"]["mode"]
            in (("green-vs-non-green",) if pair else (mode, "green-vs-non-green"))
        ),
        targets[0] if targets else None,
    )
    if target is None:
        raise TraceReplayError(
            f"planted finding did not surface for {sorted(exposure_keys)}: "
            f"{result['suppression_counts']}"
        )
    observations = [
        item
        for item in feature_frame["observations"]
        if item["feature_key"] in exposure_keys
    ]
    provenance_rows = [
        item.get("provenance") or {}
        for item in observations
        if item["state"] == "observed"
    ]
    return {
        "status": (
            "supported"
            if target["quality"]["tier"] == "replicated"
            else "exploratory"
        ),
        "registry_discovery": {
            "caller_named_source_tables": False,
            "feature_keys": sorted(exposure_keys),
            "adapters": sorted({item.adapter for item in exposure_definitions}),
        },
        "frame": {
            "frame_version": feature_frame["meta"]["frame_version"],
            "range": feature_frame["meta"]["range"],
            "state_counts": dict(sorted(Counter(item["state"] for item in observations).items())),
            "provenance_tables": sorted({
                value
                for item in provenance_rows
                for value in (
                    [item.get("table")] if item.get("table") else item.get("tables", [])
                )
            }),
            "identity_keys": sorted({
                item["identity_key"]
                for item in provenance_rows
                if item.get("identity_key")
            }),
            "original_labels": sorted({
                label
                for item in provenance_rows
                for label in item.get("original_labels", [])
            }),
        },
        "readiness": {
            item["feature_key"]: {
                "state": item["state"],
                "aligned_n": item["aligned_n"],
                "missing_rate": item["missing_rate"],
                "needed": item["needed"],
            }
            for item in readiness_payload["features"]
            if item["feature_key"] in exposure_keys
        },
        "analysis": {
            "analysis_version": result["meta"]["analysis_version"],
            "registry_version": result["meta"]["registry_version"],
            "engine_sha256": result["meta"]["engine_sha256"],
            "analysis_range": result["meta"]["analysis_range"],
            "baseline_range": result["meta"]["baseline_range"],
            "modes": result["meta"]["modes"],
            "candidate_family_sizes": result["meta"]["candidate_family_sizes"],
            "interaction_family_sizes": result["meta"]["interaction_family_sizes"],
            "mode_coverage": result["coverage"]["modes"],
            "source_manifests": result["coverage"]["source_manifests"],
            "dependencies": result["coverage"]["dependencies"],
            "suppression_counts": result["suppression_counts"],
            "finding": _compact_finding(target),
        },
    }


def _full_registry_discovery_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    requested: DateRange,
    *,
    outcome_key: str,
    exposure_key: str,
    mode: str,
    target_lag_days: int,
) -> dict[str, Any]:
    """Prove discovery without an exposure, adapter, or source-table filter."""

    result = associations.analyze_outcome(
        connection,
        definitions,
        requested,
        context,
        outcome_key=outcome_key,
        mode=mode,
        min_n=30,
        interactions="none",
        top=100,
    )
    discovered = next(
        (
            finding
            for finding in result["findings"]
            if len(finding["exposure"]["components"]) == 1
            and finding["exposure"]["components"][0]["exposure_key"]
            == exposure_key
            and finding["exposure"]["components"][0]["lag_days"]
            == target_lag_days
            and finding["exposure"]["components"][0]["window_days"] == 1
        ),
        None,
    )
    if discovered is None:
        raise TraceReplayError(
            "full-registry analysis did not discover the planted identity "
            f"{exposure_key}: {result['suppression_counts']}"
        )
    return {
        "caller_exposure_filter": None,
        "caller_adapter_filter": None,
        "caller_source_table_filter": None,
        "registered_definition_count": len(definitions),
        "discovered_feature_key": exposure_key,
        "discovered_adapter": next(
            item.adapter for item in definitions if item.key == exposure_key
        ),
        "finding": _compact_finding(discovered),
        "candidate_family_sizes": result["meta"]["candidate_family_sizes"],
        "source_manifests": result["coverage"]["source_manifests"],
        "suppression_counts": result["suppression_counts"],
    }


def _null_relationship_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    requested: DateRange,
    *,
    exposure_key: str,
) -> dict[str, Any]:
    """Run a planted null identity through the production association engine."""

    selected = [
        _definition(definitions, exact="subjective.day_rating"),
        _definition(definitions, exact=exposure_key),
    ]
    result = associations.analyze_outcome(
        connection,
        selected,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="green-vs-non-green",
        min_n=30,
        interactions="none",
        top=100,
    )
    finding = next(
        (
            item
            for item in result["findings"]
            if item["exposure"]["components"][0]["exposure_key"] == exposure_key
            and item["exposure"]["components"][0]["lag_days"] == 0
            and item["exposure"]["components"][0]["window_days"] == 1
            and item["exposure"]["components"][0]["transform"] == "point"
        ),
        None,
    )
    if finding is None:
        raise TraceReplayError("planted null relationship was not auditable")
    oriented = finding["effect"]["oriented_estimate"]
    if oriented != 0 or finding["quality"]["eligible_for_hypothesis"]:
        raise TraceReplayError(
            "planted null relationship unexpectedly became eligible"
        )
    return {
        "classification": "null_relationship_control",
        "finding": _compact_finding(finding),
        "suppression_counts": result["suppression_counts"],
        "hypothesis_eligible": False,
    }


def _identity_association_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
    seed_meta: Mapping[str, Any],
) -> dict[str, Any]:
    exposure = (
        _definition(definitions, exact=seed_meta["feature_key"])
        if seed_meta.get("feature_key")
        else _definition(
            definitions,
            prefix=seed_meta["feature_prefix"],
            suffix=seed_meta["feature_suffix"],
        )
    )
    outcome = "subjective.mood" if fixture["scenario_key"] == "social_mood" else "subjective.day_rating"
    proof = _association_proof(
        connection,
        context,
        definitions,
        _bounded(fixture["range"]),
        outcome_key=outcome,
        exposure_definitions=[exposure],
        mode=(
            "ordinal"
            if outcome == "subjective.mood"
            else "all"
            if fixture["scenario_key"] == "food_green_day"
            else "green-vs-non-green"
        ),
        target_lags=[seed_meta["target_lag_days"]],
    )
    proof["registry_discovery"]["full_registry_analysis"] = (
        _full_registry_discovery_proof(
            connection,
            context,
            definitions,
            _bounded(fixture["range"]),
            outcome_key=outcome,
            exposure_key=exposure.key,
            mode=(
                "ordinal"
                if outcome == "subjective.mood"
                else "all"
                if fixture["scenario_key"] == "food_green_day"
                else "green-vs-non-green"
            ),
            target_lag_days=seed_meta["target_lag_days"],
        )
    )
    if fixture["scenario_key"] == "food_green_day":
        observed_labels = proof["frame"]["original_labels"]
        expected_labels = sorted(seed_meta["original_labels"])
        if (
            seed_meta["identity_key"]
            != seed_meta["normalized_variant_identity_key"]
            or observed_labels != expected_labels
        ):
            raise TraceReplayError("identity normalization/collision proof drifted")
        proof["registry_discovery"]["identity_normalization_control"] = {
            "input_labels": seed_meta["original_labels"],
            "normalized_to_one_identity": True,
            "identity_key": seed_meta["identity_key"],
            "distinct_raw_labels_retained": observed_labels,
            "fuzzy_matching_used": False,
        }
        proof["analysis"]["null_relationship_control"] = (
            _null_relationship_proof(
                connection,
                context,
                definitions,
                _bounded(fixture["range"]),
                exposure_key=seed_meta["null_feature_key"],
            )
        )
        warnings = proof["analysis"]["finding"]["warnings"]
        if "source_transition" not in warnings:
            raise TraceReplayError("planted food source transition was not visible")
        proof["analysis"]["source_transition_control"] = {
            "source_eras": seed_meta["source_eras"],
            "warning": "source_transition",
            "warning_present": True,
        }
    return proof


def _pair_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
    seed_meta: Mapping[str, Any],
) -> dict[str, Any]:
    exposures = [
        _definition(definitions, prefix=prefix, suffix=suffix)
        for prefix, suffix in seed_meta["feature_patterns"]
    ]
    proof = _association_proof(
        connection,
        context,
        definitions,
        _bounded(fixture["range"]),
        outcome_key="subjective.day_rating",
        exposure_definitions=exposures,
        mode="green-vs-non-green",
        target_lags=[0, 0],
        pair=True,
    )
    cells = proof["analysis"]["finding"]["sample"]["interaction_cells"]
    proof["pair_gate_proof"] = {
        "aligned_n_at_least_80": proof["analysis"]["finding"]["sample"]["complete_n"] >= 80,
        "every_cell_strictly_over_10": all(value > 10 for value in cells.values()),
        "exact_cell_counts": cells,
        "component_contract": proof["analysis"]["finding"]["components"],
    }
    selected = [
        _definition(definitions, exact="subjective.day_rating"),
        *exposures,
    ]
    internal = associations.analyze_outcome(
        connection,
        selected,
        _bounded(fixture["range"]),
        context,
        outcome_key="subjective.day_rating",
        mode="green-vs-non-green",
        min_n=30,
        interactions="none",
        top=100,
        include_internal=True,
        return_all_internal=True,
    )
    singles = []
    for exposure in exposures:
        single = next(
            (
                item
                for item in internal["findings"]
                if len(item["exposure"]["components"]) == 1
                and item["exposure"]["components"][0]["exposure_key"] == exposure.key
                and item["exposure"]["components"][0]["lag_days"] == 0
                and item["exposure"]["components"][0]["window_days"] == 1
            ),
            None,
        )
        if single is None:
            raise TraceReplayError("real pair component single was not retained")
        singles.append(single)
    left_rows = {row["unit_key"]: row for row in singles[0]["_rows"]}
    right_rows = {row["unit_key"]: row for row in singles[1]["_rows"]}
    both_keys = [
        key
        for key in sorted(set(left_rows) & set(right_rows))
        if left_rows[key]["exposure"] == right_rows[key]["exposure"] == 1
    ]
    keep_keys = (
        (set(left_rows) & set(right_rows))
        - set(both_keys[10:])
    )
    sparse_singles = []
    for single in singles:
        copied = dict(single)
        copied["_rows"] = [
            row for row in single["_rows"] if row["unit_key"] in keep_keys
        ]
        sparse_singles.append(copied)
    sparse_findings, sparse_suppression, sparse_families = (
        interactions.analyze_pairwise(
            outcome=selected[0],
            modes=["green-vs-non-green"],
            singles=sparse_singles,
            analysis_range=internal["meta"]["analysis_range"],
            baseline_range=internal["meta"]["baseline_range"],
            input_fingerprint_value=internal["meta"]["input_fingerprint"],
            provenance=singles[0]["provenance"],
            top_limit=30,
        )
    )
    if sparse_findings or sparse_suppression.get("interaction_sparse_cell") != 1:
        raise TraceReplayError("real sparse pair control did not fail the cell gate")
    proof["sparse_negative_control"] = {
        "status": "suppressed",
        "both_cell_count": 10,
        "total_aligned_n": len(keep_keys),
        "findings": [],
        "suppression_counts": sparse_suppression,
        "interaction_family_sizes": sparse_families,
    }
    proof["notification_outbox_proof"] = _notification_outbox_proof(connection)
    return proof


def _seed_notification_ancestry(
    connection: sqlite3.Connection,
) -> tuple[str, str]:
    """Insert bounded synthetic ancestry required by the real outbox FK contract."""

    batch_id = provenance.sha256_id({"phase8_fixture": "notification_batch"})
    synthesis_id = provenance.sha256_id({
        "phase8_fixture": "notification_synthesis"
    })
    connection.execute(
        """INSERT INTO analysis_batches(
             batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
             range_plan_version,outcome_selection,outcome_set_sha256,
             analysis_version,registry_version,engine_sha256,registry_sha256,
             status,status_reason_code,run_count,completed_count,
             insufficient_count,no_data_count,failed_count,started_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            batch_id,
            provenance.sha256_id({"phase8_fixture": "notification_batch_dedupe"}),
            "weekly",
            "2026-07-18",
            "phase8-notification-fixture",
            "cadence-range-plan-v1",
            "base_and_enabled",
            provenance.sha256_id({"phase8_fixture": "notification_outcomes"}),
            provenance.ANALYSIS_VERSION,
            registry.REGISTRY_VERSION,
            EXPECTED_ENGINE_SHA256,
            provenance.sha256_id({"phase8_fixture": "notification_registry"}),
            "completed",
            None,
            0,
            0,
            0,
            0,
            0,
            "2026-07-23T08:00:00+00:00",
            "2026-07-23T08:01:00+00:00",
        ),
    )
    connection.execute(
        """INSERT INTO synthesis_runs(
             synthesis_id,analysis_batch_id,cadence,reason_code,cutoff_date,
             evidence_fingerprint,context_version,prompt_sha256,model_id,provider,
             finding_ids_json,hypothesis_ids_json,narrative_md,rendered_md,status,
             no_message_reason_code,created_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            synthesis_id,
            batch_id,
            "weekly",
            "novel_evidence",
            "2026-07-18",
            provenance.sha256_id({"phase8_fixture": "notification_evidence"}),
            orchestrator.CONTEXT_VERSION,
            provenance.sha256_id({"phase8_fixture": "notification_prompt"}),
            synthesis.LEGACY_MODEL_ID,
            synthesis.LEGACY_PROVIDER,
            "[]",
            "[]",
            "Fixture-generated narrative; no live model invoked.",
            "Fixture-generated rendered payload; no live model invoked.",
            "completed",
            None,
            "2026-07-23T08:02:00+00:00",
            "2026-07-23T08:02:00+00:00",
        ),
    )
    connection.commit()
    return batch_id, synthesis_id


def _notification_outbox_proof(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Exercise the durable Phase 6 sender boundary without calling a sender."""

    batch_id, synthesis_id = _seed_notification_ancestry(connection)
    payload = {
        "contract_version": orchestrator.NOTIFICATION_CONTRACT_VERSION,
        "message": "Synthetic fixture notification; fictional content only.",
        "synthesis_id": synthesis_id,
    }
    notification = {
        "channel_class": "telegram",
        "destination_class": "owner_primary",
        "payload": payload,
        "dedupe_key": orchestrator.notification_dedupe_key(
            cadence="weekly",
            evidence_fingerprints=[
                provenance.sha256_id({
                    "phase8_fixture": "notification_evidence"
                })
            ],
            destination_class="owner_primary",
            context_version=orchestrator.CONTEXT_VERSION,
        ),
        "not_before": "2026-07-23T10:00:00+00:00",
        "idempotency_mode": "none",
        "provider_idempotency_key": None,
    }

    connection.execute("BEGIN IMMEDIATE")
    enqueued = orchestrator.enqueue_notification(
        connection,
        notification,
        analysis_batch_id=batch_id,
        synthesis_id=synthesis_id,
        now="2026-07-23T09:00:00+00:00",
    )
    connection.commit()
    pending_row = dict(connection.execute(
        """SELECT notification_id,state,dedupe_key,payload_json,payload_sha256,
                  idempotency_mode,provider_idempotency_key,attempt_count,
                  lease_generation
             FROM insight_notification_outbox
            WHERE notification_id=?""",
        (enqueued["notification_id"],),
    ).fetchone())

    connection.execute("BEGIN IMMEDIATE")
    duplicate = orchestrator.enqueue_notification(
        connection,
        notification,
        analysis_batch_id=batch_id,
        synthesis_id=synthesis_id,
        now="2026-07-23T09:01:00+00:00",
    )
    connection.commit()

    worker_id = "12345678-1234-4234-8234-123456789abc"
    connection.execute("BEGIN IMMEDIATE")
    claim = orchestrator.claim_notification(
        connection,
        worker_id,
        now="2026-07-23T10:00:00+00:00",
    )
    connection.commit()
    if not claim["claimed"]:
        raise TraceReplayError("fixture outbox notification was not claimable")
    fence = {
        "notification_id": claim["notification"]["notification_id"],
        "lease_generation": claim["notification"]["lease_generation"],
        "lease_token": claim["lease_token"],
    }
    stale_fence_code = None
    connection.execute("BEGIN IMMEDIATE")
    try:
        orchestrator.begin_notification_dispatch(
            connection,
            {**fence, "lease_token": "0" * 64},
            now="2026-07-23T10:00:01+00:00",
        )
    except orchestrator.OrchestrationError as error:
        connection.rollback()
        stale_fence_code = error.code
    else:
        connection.rollback()
        raise TraceReplayError("wrong notification lease token was not fenced")

    connection.execute("BEGIN IMMEDIATE")
    dispatch = orchestrator.begin_notification_dispatch(
        connection,
        fence,
        now="2026-07-23T10:00:01+00:00",
    )
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    ambiguous = orchestrator.fail_notification(
        connection,
        {
            **fence,
            "failure_class": "ambiguous",
            "reason_code": "provider_timeout",
        },
        now="2026-07-23T10:00:02+00:00",
    )
    connection.commit()

    connection.execute("BEGIN IMMEDIATE")
    automatic_retry = orchestrator.claim_notification(
        connection,
        worker_id,
        now="2026-07-24T10:00:00+00:00",
    )
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    operator_resolution = orchestrator.resolve_notification(
        connection,
        {
            "notification_id": enqueued["notification_id"],
            "resolution": "confirmed_not_sent_retry",
            "reason_code": "operator_confirmed_not_sent",
        },
        now="2026-07-24T10:05:00+00:00",
    )
    connection.commit()

    final_row = dict(connection.execute(
        """SELECT notification_id,state,dedupe_key,payload_sha256,
                  idempotency_mode,provider_idempotency_key,attempt_count,
                  lease_generation,last_error_code
             FROM insight_notification_outbox
            WHERE notification_id=?""",
        (enqueued["notification_id"],),
    ).fetchone())
    events = [
        dict(row)
        for row in connection.execute(
            """SELECT event_kind,lease_generation,attempt_no,reason_code,
                      provider_reference_sha256,occurred_at
                 FROM insight_notification_events
                WHERE notification_id=?
                ORDER BY occurred_at,event_id""",
            (enqueued["notification_id"],),
        )
    ]
    if not (
        enqueued["created"] is True
        and pending_row["state"] == "pending"
        and duplicate["created"] is False
        and stale_fence_code == "stale_lease"
        and dispatch["state"] == "dispatching"
        and ambiguous["state"] == "uncertain"
        and automatic_retry["claimed"] is False
        and operator_resolution["state"] == "retry_wait"
        and final_row["state"] == "retry_wait"
    ):
        raise TraceReplayError("Phase 6 notification fixture boundary drifted")
    return {
        "boundary": "fixture_generated/not_live_sender",
        "external_sender_calls": [],
        "destination_identifier_present": False,
        "notification_input": notification,
        "pending": pending_row,
        "exact_duplicate": duplicate,
        "claim": {
            "claimed": claim["claimed"],
            "notification": claim["notification"],
            "lease_token_recorded": False,
        },
        "dispatch_fence": {
            "wrong_token_error_code": stale_fence_code,
            "valid_transition": dispatch,
        },
        "ambiguous_after_dispatch": ambiguous,
        "automatic_retry_after_uncertain": automatic_retry,
        "operator_only_resolution": operator_resolution,
        "final_row": final_row,
        "events": events,
    }


def _running_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    requested = _bounded(fixture["range"])
    selected = [
        _definition(definitions, exact=key)
        for key in ("running.session", "running.stop", "running.restart")
    ]
    proven = frame.build_feature_frame(
        connection,
        selected,
        requested,
        context,
        include_provenance=True,
    )
    unproven_context = fixture_context(running_completeness=False)
    unproven_definitions = list(registry.build_registry(connection, unproven_context))
    unproven_selected = [
        _definition(unproven_definitions, exact=key)
        for key in ("running.session", "running.stop", "running.restart")
    ]
    unproven = frame.build_feature_frame(
        connection,
        unproven_selected,
        requested,
        unproven_context,
        include_provenance=True,
    )
    supplemental = pain_adapter.supplemental(
        connection,
        requested,
        context,
        include_provenance=True,
    )

    def compact(
        payload: Mapping[str, Any],
        *,
        observed_only: bool,
    ) -> list[dict[str, Any]]:
        return [
            {
                "feature_key": item["feature_key"],
                "observed_at": item["observed_at"],
                "value": item["value"],
                "state": item["state"],
                "provenance": item.get("provenance"),
            }
            for item in payload["observations"]
            if item["feature_key"] in {"running.stop", "running.restart"}
            and (not observed_only or item["state"] == "observed")
        ]

    proven_markers = compact(proven, observed_only=True)
    if {item["feature_key"] for item in proven_markers} != {
        "running.stop",
        "running.restart",
    }:
        raise TraceReplayError("completeness-proven running markers were not emitted")
    unproven_observed_markers = compact(unproven, observed_only=True)
    if unproven_observed_markers:
        raise TraceReplayError("running markers were emitted without known coverage")
    unproven_state_counts = dict(sorted(Counter(
        (item["feature_key"], item["state"])
        for item in compact(unproven, observed_only=False)
    ).items()))
    return {
        "status": "supported",
        "collector_boundary": {
            "kind": "fixture_completeness_capability",
            "source": "fixture-workouts",
            "production_default_claims_workout_coverage": False,
        },
        "completeness_proven_markers": proven_markers,
        "missing_coverage_observed_markers": unproven_observed_markers,
        "missing_coverage_state_counts": [
            {
                "feature_key": key[0],
                "state": key[1],
                "count": value,
            }
            for key, value in unproven_state_counts.items()
        ],
        "missing_rows_do_not_prove_break": True,
        "pain_timeline": supplemental,
    }


def _quarterly_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
    seed_meta: Mapping[str, Any],
) -> dict[str, Any]:
    exposure = _definition(definitions, exact=seed_meta["feature_key"])
    outcome = _definition(definitions, exact=seed_meta["outcome_key"])
    requested = _bounded(fixture["range"])
    feature_frame = frame.build_feature_frame(
        connection,
        [exposure, outcome],
        requested,
        context,
        include_provenance=True,
    )
    result = associations.analyze_outcome(
        connection,
        [exposure, outcome],
        requested,
        context,
        outcome_key=outcome.key,
        mode="ordinal",
        min_n=30,
        interactions="none",
        top=100,
    )
    reason = result["suppression_counts"].get(
        "slow_episode_no_generic_alignment", 0
    )
    if reason != 1 or result["findings"]:
        raise TraceReplayError("quarterly slow episode was not honestly suppressed")
    observations = [
        item
        for item in feature_frame["observations"]
        if item["feature_key"] == exposure.key
    ]
    distinction_keys = {
        "measured_strength": "fitness.test.leg-extension.left.value",
        "measured_control": "fitness.test.balance-stand.left.value",
        "measured_mobility": "fitness.test.ankle-df-wall.left.value",
        "training_exposure": "training.group.legs.effective_sets",
        "body_measurement": "body.thigh_cm",
    }
    distinction_definitions = {
        role: _definition(definitions, exact=key)
        for role, key in distinction_keys.items()
    }
    distinction_frame = frame.build_feature_frame(
        connection,
        list(distinction_definitions.values()),
        requested,
        context,
        include_provenance=True,
    )
    by_feature = defaultdict(list)
    for observation in distinction_frame["observations"]:
        by_feature[observation["feature_key"]].append(observation)
    distinctions = {}
    for role, definition in distinction_definitions.items():
        distinctions[role] = {
            "feature_key": definition.key,
            "pillar": definition.pillar,
            "unit": definition.unit,
            "temporal_type": definition.temporal_type,
            "formula_id": definition.formula_id,
            "source_tables": list(definition.source.get("tables", []))
            or [definition.source.get("table")],
            "observations": [
                {
                    "observed_at": item["observed_at"],
                    "value": item["value"],
                    "unit": item["unit"],
                    "state": item["state"],
                }
                for item in by_feature[definition.key]
            ],
        }
    return {
        "status": "logic_not_implemented",
        "feature_key": exposure.key,
        "temporal_type": exposure.temporal_type,
        "aggregation_id": exposure.aggregation_id,
        "completeness_profile": exposure.completeness_profile,
        "roles": list(exposure.roles),
        "candidate_enabled": exposure.candidate_enabled,
        "measurement_episodes": [
            {
                "observed_at": item["observed_at"],
                "value": item["value"],
                "unit": item["unit"],
                "provenance": item.get("provenance"),
            }
            for item in observations
        ],
        "measurement_distinctions": distinctions,
        "muscle_size_claimed_from_strength_or_exposure": False,
        "analysis": {
            "findings": [],
            "suppression_reason": "slow_episode_no_generic_alignment",
            "suppression_count": reason,
        },
        "ledger_transition": None,
        "owner_gate": (
            "event-centered slow-measurement analysis is not implemented; "
            "Phase 8 must not fabricate a pain hypothesis"
        ),
    }


def _insufficient_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
    seed_meta: Mapping[str, Any],
) -> dict[str, Any]:
    exposure = _definition(definitions, exact=seed_meta["feature_key"])
    outcome = _definition(definitions, exact=seed_meta["outcome_key"])
    requested = _bounded(fixture["range"])
    payload = readiness.build_readiness(
        connection,
        [exposure, outcome],
        requested,
        context,
        outcome=outcome.key,
    )
    feature = next(item for item in payload["features"] if item["feature_key"] == exposure.key)
    result = associations.analyze_outcome(
        connection,
        [exposure, outcome],
        requested,
        context,
        outcome_key=outcome.key,
        mode="ordinal",
        min_n=30,
        interactions="none",
        top=100,
    )
    if result["findings"]:
        raise TraceReplayError("empty insufficient fixture fabricated a finding")
    return {
        "status": "insufficient",
        "readiness_state": feature["state"],
        "exact_needed": feature["needed"],
        "prerequisites": feature["prerequisites"],
        "findings": [],
        "suppression_counts": result["suppression_counts"],
        "measurement_recommendation_only": True,
    }


def _reversal_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
    seed_meta: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist and refresh three real windows through the frozen ledger."""

    exposure = _definition(
        definitions,
        prefix=seed_meta["feature_prefix"],
        suffix=seed_meta["feature_suffix"],
    )
    outcome = _definition(definitions, exact="subjective.day_rating")
    findings = []
    batch_ids = []
    hypothesis_id = None
    for window_index, window in enumerate(seed_meta["windows"], 1):
        requested = _bounded(window)
        verified = ledger.compute_verified_analysis(
            connection,
            [outcome, exposure],
            requested,
            context,
            outcome_key=outcome.key,
            outcome_mode="green-vs-non-green",
            interactions="none",
        )
        result = verified.payload
        target = next(
            (
                item
                for item in result["findings"]
                if item["exposure"]["components"][0]["exposure_key"] == exposure.key
                and item["exposure"]["components"][0]["lag_days"] == 0
                and item["exposure"]["components"][0]["window_days"] == 1
                and item["exposure"]["components"][0]["transform"] == "point"
            ),
            None,
        )
        if target is None:
            raise TraceReplayError(f"reversal window did not surface: {window}")
        findings.append(_compact_finding(target))
        timestamp = f"2026-07-23T0{window_index}:00:00+00:00"
        connection.execute("BEGIN IMMEDIATE")
        try:
            batch = ledger.create_analysis_batch(
                connection,
                run_kind="manual",
                anchor_date=window["to"],
                initiator_key=f"phase8-reversal-window-{window_index}",
                outcome_selection="explicit_set",
                outcomes=[(outcome.key, "green-vs-non-green")],
                ranges=[{
                    "range_role": "primary",
                    "requested_range_kind": "bounded",
                    "requested_from": window["from"],
                    "requested_to": window["to"],
                }],
                registry_sha256_value=result["meta"]["registry_sha256"],
                analysis_version=result["meta"]["analysis_version"],
                registry_version=result["meta"]["registry_version"],
                engine_sha256_value=result["meta"]["engine_sha256"],
                started_at=timestamp,
            )
            primary = next(
                item for item in batch["ranges"] if item["range_role"] == "primary"
            )
            run = ledger.start_analysis_run(
                connection,
                batch_id=batch["batch_id"],
                range_id=primary["range_id"],
                outcome_key=outcome.key,
                outcome_mode="green-vs-non-green",
                batch_outcomes=[(outcome.key, "green-vs-non-green")],
                started_at=timestamp,
            )
            persisted = ledger.persist_analysis_run(
                connection,
                run_id=run["run_id"],
                verified=verified,
                completed_at=timestamp,
            )
            ledger.finalize_analysis_batch(
                connection,
                batch_id=batch["batch_id"],
                completed_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        batch_ids.append(batch["batch_id"])

        if window_index == 1:
            replay = ledger.compute_verified_finding(
                connection,
                [outcome, exposure],
                requested,
                context,
                outcome_key=outcome.key,
                finding_id_value=target["finding_id"],
                input_fingerprint_value=result["meta"]["input_fingerprint"],
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                promoted = ledger.promote_verified_finding(
                    connection,
                    run_id=persisted["run_id"],
                    verified=replay,
                    explicit=True,
                    created_at=timestamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            hypothesis_id = promoted["hypothesis_id"]
        else:
            connection.execute("BEGIN IMMEDIATE")
            try:
                ledger.refresh_batch_hypotheses(
                    connection,
                    batch_id=batch["batch_id"],
                    created_at=timestamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    directions = [item["direction"] for item in findings]
    if not (directions[0] == directions[1] and directions[2] != directions[1]):
        raise TraceReplayError(
            f"reversal fixture directions are not opposite: {directions}"
        )
    evaluations = [
        dict(row)
        for row in connection.execute(
            """SELECT id,hypothesis_id,run_id,finding_id,evidence_class,
                      previous_status,status,change_reason,change_conditions,
                      confidence,previous_evaluation_id,comparison_evaluation_id,
                      new_eligible_observations,transition_applied,
                      evidence_for_json,evidence_against_json,confounders_json,
                      sample_size_json,effect_summary_json,stability_json,
                      evidence_fingerprint
                 FROM hypothesis_evaluations
                WHERE hypothesis_id=? ORDER BY id""",
            (hypothesis_id,),
        )
    ]
    for item in evaluations:
        for field in (
            "evidence_for_json",
            "evidence_against_json",
            "confounders_json",
            "sample_size_json",
            "effect_summary_json",
            "stability_json",
        ):
            item[field.removesuffix("_json")] = json.loads(item.pop(field))
    evidence_items = [
        dict(row)
        for row in connection.execute(
            """SELECT evidence_item_id,hypothesis_id,evaluation_id,finding_id,
                      polarity,evidence_kind,evidence_fingerprint,
                      source_analysis_version,range_from,range_to
                 FROM hypothesis_evidence_items
                WHERE hypothesis_id=?
                ORDER BY evaluation_id,evidence_item_id""",
            (hypothesis_id,),
        )
    ]
    evidence_classes = [item["evidence_class"] for item in evaluations]
    if evidence_classes != [
        "initial_discovery_pass",
        "same_pass_nonoverlap",
        "opposite_pass",
    ]:
        raise TraceReplayError(
            "reversal did not pass through the frozen transition sequence: "
            f"{evidence_classes}"
        )
    if evaluations[-1]["previous_evaluation_id"] != evaluations[-2]["id"]:
        raise TraceReplayError("reversal ancestry did not retain prior evaluation")
    synthesis_preparation = _production_synthesis_preparation(
        connection,
        batch_id=batch_ids[-1],
        cadence="manual",
        cutoff_date=seed_meta["windows"][-1]["to"],
        freshness=orchestrator.freshness_snapshot(
            connection,
            now=FIXED_NOW,
        ),
        suppressed_dependencies=[],
    )
    notification_proof = _notification_outbox_proof(connection)
    return {
        "status": "supported",
        "finding_windows": findings,
        "analysis_batch_ids": batch_ids,
        "hypothesis_id": hypothesis_id,
        "ledger_evaluations": evaluations,
        "ledger_evidence_items": evidence_items,
        "overwrite_permitted": False,
        "ancestry_required": True,
        "fixture_evidence_is_not_owner_evidence": True,
        "production_synthesis_preparation": synthesis_preparation,
        "notification_outbox_proof": notification_proof,
    }


def _production_synthesis_preparation(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    cadence: str,
    cutoff_date: str,
    freshness: Mapping[str, Any],
    suppressed_dependencies: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the exact production Phase 6 pre-model preparation from rows."""

    refs = health._phase6_refs_and_novelty(  # noqa: SLF001
        connection,
        {"batch_id": batch_id},
        cadence,
        None,
    )
    evidence_fingerprint = synthesis.synthesis_evidence_fingerprint(
        connection,
        analysis_batch_id=batch_id,
        run_refs=refs["run_refs"],
        finding_refs=refs["finding_refs"],
        hypothesis_refs=refs["hypothesis_refs"],
    )
    slots = orchestrator.structured_slots(
        cadence=cadence,
        run_rows=refs["runs"],
        finding_rows=refs["finding_rows"],
        evaluation_rows=refs["evaluation_rows"],
        freshness=freshness,
        suppressed_dependencies=suppressed_dependencies,
    )
    preparation = {
        "boundary": "synthesis-record",
        "analysis_batch_id": batch_id,
        "cadence": cadence,
        "cutoff_date": cutoff_date,
        "context_version": synthesis.SYNTHESIS_CONTEXT_VERSION,
        "evidence_fingerprint": evidence_fingerprint,
        "run_refs": refs["run_refs"],
        "finding_refs": refs["finding_refs"],
        "hypothesis_refs": refs["hypothesis_refs"],
        "structured_slots": slots,
        "model_invoked": False,
    }
    if (
        not refs["finding_refs"]
        or preparation["structured_slots"]["numeric_authority"]
        != "stored_structured_evidence_only"
    ):
        raise TraceReplayError(
            "production synthesis preparation lacks persisted evidence"
        )
    return {
        "source": "health._phase6_finalize preparation contract",
        "refs_source": "health._phase6_refs_and_novelty",
        "structured_slots_source": (
            "hermes_insights.orchestrator.structured_slots"
        ),
        "persisted_rows_only": True,
        "preparation": preparation,
        "preparation_sha256": sha256_json(preparation),
    }


def _scheduled_weekly(database_path: Path, anchor: str) -> dict[str, Any]:
    """Run the production Phase 6 weekly preparation/compute/finalize path."""

    original_db = health.DB
    original_now = health._now
    original_ledger_now = ledger._now  # noqa: SLF001
    original_synthesis_now = synthesis._utc_now  # noqa: SLF001
    fixed_utc = f"{anchor}T08:00:00+00:00"
    health.DB = str(database_path)
    health._now = lambda: FIXED_NOW
    ledger._now = lambda: fixed_utc  # noqa: SLF001
    synthesis._utc_now = lambda: fixed_utc  # noqa: SLF001
    try:
        plan = orchestrator.cadence_plan(
            "weekly",
            local_now=FIXED_NOW,
            anchor=anchor,
        )
        prepared = health._phase6_prepare_batch("weekly", plan, None)
        runs = health._phase6_compute_runs(prepared, plan)
        finalized = health._phase6_finalize(
            prepared, plan, "weekly", None, None,
        )
        return {
            "plan": plan,
            "outcomes": prepared["selection"],
            "freshness": prepared["freshness"],
            "suppressed_dependencies": prepared["suppressed_dependencies"],
            "runs": runs,
            **finalized,
        }
    finally:
        health.DB = original_db
        health._now = original_now
        ledger._now = original_ledger_now  # noqa: SLF001
        synthesis._utc_now = original_synthesis_now  # noqa: SLF001


def _manual_fixture_baseline(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    first_weekly: Mapping[str, Any],
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute the complete weekly selection as a real manual baseline."""

    manual_anchor = fixture["seed"]["manual_anchor"]
    plan = orchestrator.cadence_plan(
        "weekly",
        local_now=FIXED_NOW,
        anchor=manual_anchor,
    )
    primary_plan = plan["ranges"][0]
    requested = _bounded({
        "from": primary_plan["requested_from"],
        "to": primary_plan["requested_to"],
    })
    outcome_modes = first_weekly["outcomes"]["outcome_modes"]
    verified_runs = [
        (
            item,
            ledger.compute_verified_analysis(
                connection,
                definitions,
                requested,
                context,
                outcome_key=item["outcome_key"],
                outcome_mode=item["outcome_mode"],
            ),
        )
        for item in outcome_modes
    ]
    first_meta = verified_runs[0][1].payload["meta"]
    timestamp = "2026-07-12T08:00:00+00:00"
    connection.execute("BEGIN IMMEDIATE")
    try:
        batch = ledger.create_analysis_batch(
            connection,
            run_kind="manual",
            anchor_date=manual_anchor,
            initiator_key="phase8-weekly-manual-baseline",
            outcome_selection="explicit_set",
            outcomes=[
                (item["outcome_key"], item["outcome_mode"])
                for item in outcome_modes
            ],
            ranges=[{
                "range_role": "primary",
                "requested_range_kind": "bounded",
                "requested_from": requested.start.isoformat(),
                "requested_to": requested.end.isoformat(),
            }],
            registry_sha256_value=first_meta["registry_sha256"],
            analysis_version=first_meta["analysis_version"],
            registry_version=first_meta["registry_version"],
            engine_sha256_value=first_meta["engine_sha256"],
            range_plan_version=ledger.RANGE_PLAN_VERSION,
            started_at=timestamp,
        )
        primary = next(
            item for item in batch["ranges"] if item["range_role"] == "primary"
        )
        persisted_runs = []
        for outcome, verified in verified_runs:
            run = ledger.start_analysis_run(
                connection,
                batch_id=batch["batch_id"],
                range_id=primary["range_id"],
                outcome_key=outcome["outcome_key"],
                outcome_mode=outcome["outcome_mode"],
                batch_outcomes=[
                    (item["outcome_key"], item["outcome_mode"])
                    for item in outcome_modes
                ],
                started_at=timestamp,
            )
            persisted_runs.append(ledger.persist_analysis_run(
                connection,
                run_id=run["run_id"],
                verified=verified,
                completed_at=timestamp,
            ))
        ledger.finalize_analysis_batch(
            connection,
            batch_id=batch["batch_id"],
            completed_at=timestamp,
        )
        ledger_refresh = ledger.refresh_batch_hypotheses(
            connection,
            batch_id=batch["batch_id"],
            created_at=timestamp,
        )
        batch_id = batch["batch_id"]
        run_refs = [
            {"run_id": row["run_id"], "purpose": "primary"}
            for row in connection.execute(
                """SELECT run_id FROM analysis_runs
                    WHERE batch_id=? ORDER BY outcome_key,outcome_mode,run_id""",
                (batch_id,),
            )
        ]
        finding_refs = [
            {"finding_id": row["finding_id"], "role": "primary"}
            for row in connection.execute(
                """SELECT f.finding_id
                     FROM analysis_findings f
                     JOIN analysis_runs r ON r.run_id=f.run_id
                    WHERE r.batch_id=? AND f.eligible_for_hypothesis=1
                    ORDER BY f.outcome_key,f.finding_id""",
                (batch_id,),
            )
        ]
        hypothesis_refs = [
            {
                "hypothesis_id": row["hypothesis_id"],
                "evaluation_id": row["id"],
                "role": "changed" if row["transition_applied"] else "context",
            }
            for row in connection.execute(
                """SELECT e.id,e.hypothesis_id,e.transition_applied
                     FROM hypothesis_evaluations e
                     JOIN analysis_runs r ON r.run_id=e.run_id
                    WHERE r.batch_id=?
                    ORDER BY e.hypothesis_id,e.id""",
                (batch_id,),
            )
        ]
        if not finding_refs or not hypothesis_refs:
            raise TraceReplayError(
                "first weekly evidence did not create a manual baseline"
            )
        evidence_fingerprint = synthesis.synthesis_evidence_fingerprint(
            connection,
            analysis_batch_id=batch_id,
            run_refs=run_refs,
            finding_refs=finding_refs,
            hypothesis_refs=hypothesis_refs,
        )
        production_preparation = _production_synthesis_preparation(
            connection,
            batch_id=batch_id,
            cadence="manual",
            cutoff_date=manual_anchor,
            freshness=orchestrator.freshness_snapshot(
                connection,
                now=FIXED_NOW,
            ),
            suppressed_dependencies=[],
        )
        prompt_sha256 = provenance.sha256_id({
            "contract": "phase8-fixture-prompt-v1",
            "evidence_fingerprint": evidence_fingerprint,
        })
        synthesis_id = provenance.sha256_id({
            "contract": synthesis.SYNTHESIS_CONTRACT_VERSION,
            "kind": "phase8_manual_fixture_baseline",
            "evidence_fingerprint": evidence_fingerprint,
        })
        fixture_sections = _narrative(fixture, "supported")
        fixture_narrative_md = (
            "fixture_generated/not_live_model\n\n"
            + "\n\n".join(
                f"## {heading}\n{content}"
                for heading, content in fixture_sections.items()
            )
        )
        original_synthesis_now = synthesis._utc_now  # noqa: SLF001
        synthesis._utc_now = (  # noqa: SLF001
            lambda: "2026-07-12T08:00:00+00:00"
        )
        try:
            recorded = synthesis.record_synthesis(
                connection,
                {
                    "synthesis_id": synthesis_id,
                    "analysis_batch_id": batch_id,
                    "cadence": "manual",
                    "reason_code": "fixture_manual_baseline",
                    "cutoff_date": manual_anchor,
                    "evidence_fingerprint": evidence_fingerprint,
                    "context_version": synthesis.SYNTHESIS_CONTEXT_VERSION,
                    "prompt_sha256": prompt_sha256,
                    "model_id": synthesis.LEGACY_MODEL_ID,
                    "provider": synthesis.LEGACY_PROVIDER,
                    "run_refs": run_refs,
                    "finding_refs": finding_refs,
                    "hypothesis_refs": hypothesis_refs,
                    "narrative_md": fixture_narrative_md,
                    "rendered_md": None,
                    "status": "completed",
                    "no_message_reason_code": None,
                    "annotations": [],
                    "notification": None,
                },
            )
        finally:
            synthesis._utc_now = original_synthesis_now  # noqa: SLF001
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "analysis_batch_id": batch_id,
        "plan": plan,
        "persisted_runs": persisted_runs,
        "ledger_refresh": ledger_refresh,
        "run_refs": run_refs,
        "finding_refs": finding_refs,
        "hypothesis_refs": hypothesis_refs,
        "synthesis_id": recorded["synthesis_id"],
        "narrative_origin": "fixture_generated/not_live_model",
        "narrative_section_headings": list(fixture_sections),
        "model_invoked": False,
        "production_synthesis_preparation": production_preparation,
    }


def _weekly_proof(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
    seed_meta: Mapping[str, Any],
    database_path: Path,
) -> dict[str, Any]:
    first = _scheduled_weekly(
        database_path,
        fixture["seed"]["first_anchor"],
    )
    if (
        first["status"] != "no_novelty"
        or first["reason_code"] != "bootstrap_baseline_required"
        or first["synthesis"]["status"] != "no_novelty"
    ):
        raise TraceReplayError("persisted first weekly baseline did not fail closed")
    baseline = _manual_fixture_baseline(
        connection,
        context,
        definitions,
        first,
        fixture,
    )
    later = _scheduled_weekly(
        database_path,
        fixture["seed"]["later_anchor"],
    )
    if (
        later["status"] != "no_novelty"
        or later["reason_code"] != "no_eligible_novel_evidence"
        or later["synthesis"] is None
        or later["synthesis"]["status"] != "no_novelty"
    ):
        summary = {
            key: later.get(key)
            for key in (
                "status",
                "reason_code",
                "synthesis",
                "ledger",
                "preparation",
            )
        }
        raise TraceReplayError(
            "persisted later weekly run did not suppress no novelty: "
            f"{summary!r}"
        )
    synthesis_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT synthesis_id,analysis_batch_id,cadence,reason_code,
                      cutoff_date,evidence_fingerprint,context_version,
                      prompt_sha256,model_id,provider,status,
                      no_message_reason_code
                 FROM synthesis_runs ORDER BY synthesis_id"""
        )
    ]
    outbox_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT notification_id,state,dedupe_key,payload_sha256
                 FROM insight_notification_outbox ORDER BY notification_id"""
        )
    ]
    weekly_rows = [
        item for item in synthesis_rows if item["cadence"] == "weekly"
    ]
    if len(weekly_rows) != 2 or outbox_rows:
        raise TraceReplayError("weekly no-message persistence/outbox invariant failed")
    return {
        "status": "no_novelty",
        "first_baseline": {
            "plan": first["plan"],
            "batch_id": first["batch"]["batch_id"],
            "status": first["status"],
            "reason_code": first["reason_code"],
            "synthesis_id": first["synthesis"]["synthesis_id"],
        },
        "manual_baseline": baseline,
        "later_unchanged": {
            "plan": later["plan"],
            "batch_id": later["batch"]["batch_id"],
            "status": later["status"],
            "reason_code": later["reason_code"],
            "synthesis_id": later["synthesis"]["synthesis_id"],
        },
        "persisted_syntheses": synthesis_rows,
        "outbox_rows": outbox_rows,
    }


def _panel_database_rollback_proof(
    panel_database_path: Path,
) -> dict[str, Any]:
    """Record exact disposable panel-state counts without secret row values."""

    connection = sqlite3.connect(panel_database_path)
    connection.row_factory = sqlite3.Row
    try:
        integrity = {
            "quick_check": connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0],
            "foreign_key_violations": len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            ),
        }
        row_counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in (
                "chat_conversations",
                "chat_log",
                "settings",
                "sessions",
            )
        }
        delivery_status_counts = {
            row["delivery_status"]: row["count"]
            for row in connection.execute(
                """SELECT delivery_status,COUNT(*) AS count
                     FROM chat_log
                    GROUP BY delivery_status
                    ORDER BY delivery_status"""
            )
            if row["delivery_status"] is not None
        }
        lens_counts = {
            row["lens"]: row["count"]
            for row in connection.execute(
                """SELECT lens,COUNT(*) AS count
                     FROM chat_conversations
                    GROUP BY lens
                    ORDER BY lens"""
            )
        }
        archived_counts = {
            ("archived" if row["archived"] else "active"): row["count"]
            for row in connection.execute(
                """SELECT archived_at IS NOT NULL AS archived,
                          COUNT(*) AS count
                     FROM chat_conversations
                    GROUP BY archived
                    ORDER BY archived"""
            )
        }
        legacy_count = connection.execute(
            """SELECT COUNT(*) FROM chat_conversations
                WHERE id IN ('legacy-general','legacy-pain','legacy-mobility')"""
        ).fetchone()[0]
        conversation_origin_counts = {
            "legacy_seeded_by_panel_schema": legacy_count,
            "fixture_generated": (
                row_counts["chat_conversations"] - legacy_count
            ),
        }
    finally:
        connection.close()
    state = {
        "row_counts": row_counts,
        "delivery_status_counts": delivery_status_counts,
        "lens_counts": lens_counts,
        "archived_counts": archived_counts,
        "conversation_origin_counts": conversation_origin_counts,
    }
    return {
        "database_scope": "temporary_panel_fixture_only",
        "integrity": integrity,
        **state,
        "state_sha256": sha256_json(state),
        "sensitive_row_values_recorded": False,
        "recovery": "discard temporary panel database",
    }


def _conversation_contract_proof(
    fixture: Mapping[str, Any],
    panel_database_path: Path,
) -> dict[str, Any]:
    """Exercise the real panel API with only the external Hermes call faked."""

    calls: list[dict[str, Any]] = []
    api_exchanges: list[dict[str, Any]] = []
    ids = iter(("1" * 32, "2" * 32))
    updates = iter(range(1_800_000_000_000, 1_800_000_000_100))
    original_run = panel_bridge.run
    original_token_hex = chat_routes.secrets.token_hex
    original_updated = chat_routes._next_updated_at
    original_time = chat_routes.time

    def runner(subcommand: str, *args: str, **kwargs: Any) -> dict[str, Any]:
        envelope = json.loads(kwargs["stdin"])
        calls.append({
            "subcommand": subcommand,
            "args": list(args),
            "timeout": kwargs["timeout"],
            "envelope": envelope,
        })
        if envelope["message"] == "Synthetic uncertain fixture turn":
            raise panel_bridge.BridgeError(
                "Synthetic timeout after the dispatch boundary."
            )
        return {"ok": True, "reply": "Synthetic scoped fixture reply"}

    def record_exchange(
        label: str,
        method: str,
        path: str,
        response: Any,
        *,
        request_json: Mapping[str, Any] | None = None,
    ) -> None:
        request_record: dict[str, Any] = {
            "method": method,
            "path_and_query": path,
        }
        if request_json is not None:
            request_record["json"] = dict(request_json)
        body = response.get_json()
        api_exchanges.append({
            "label": label,
            "executed": True,
            "request": request_record,
            "status_code": response.status_code,
            "response": body,
            "response_sha256": sha256_json(body),
        })

    try:
        panel_bridge.run = runner
        app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "PANEL_DB": str(panel_database_path),
            "PANEL_COOKIE_SECURE": False,
        })
        with app.app_context():
            token = panel_auth.create_session()
        chat_routes.secrets.token_hex = lambda _n: next(ids)
        chat_routes._next_updated_at = lambda _db: next(updates)
        client = app.test_client()
        client.set_cookie(panel_auth.SESSION_COOKIE, token)
        general_context = fixture["seed"]["contexts"]["general"]
        pain_context = fixture["seed"]["contexts"]["pain"]
        chat_routes.time = SimpleNamespace(
            time=lambda: 1_800_000_000,
            time_ns=lambda: 1_800_000_000_000_000_000,
        )
        general_create_body = {
            "lens": "general",
            "context": general_context,
        }
        general_response = client.post(
            "/api/chat/conversations",
            json=general_create_body,
        )
        record_exchange(
            "create_general",
            "POST",
            "/api/chat/conversations",
            general_response,
            request_json=general_create_body,
        )
        pain_create_body = {
            "lens": "pain",
            "context": pain_context,
        }
        pain_response = client.post(
            "/api/chat/conversations",
            json=pain_create_body,
        )
        record_exchange(
            "create_pain",
            "POST",
            "/api/chat/conversations",
            pain_response,
            request_json=pain_create_body,
        )
        if general_response.status_code != 201 or pain_response.status_code != 201:
            raise TraceReplayError(
                "conversation creation API failed: "
                f"general={general_response.status_code}/"
                f"{general_response.get_json()!r}, "
                f"pain={pain_response.status_code}/{pain_response.get_json()!r}"
            )
        general = general_response.get_json()["conversation"]
        pain = pain_response.get_json()["conversation"]
        turns = (
            (
                general,
                general_context,
                "00000000-0000-4000-8000-000000000001",
                "Synthetic general fixture turn",
            ),
            (
                pain,
                pain_context,
                "00000000-0000-4000-8000-000000000002",
                "Synthetic pain fixture turn",
            ),
        )
        responses = []
        for label, conversation, context, turn_id, message in (
            ("send_general", *turns[0]),
            ("send_pain", *turns[1]),
        ):
            path = f"/api/chat/conversations/{conversation['id']}/send"
            request_body = {
                "message": message,
                "turn_id": turn_id,
                "context": context,
            }
            response = client.post(
                path,
                json=request_body,
            )
            record_exchange(
                label,
                "POST",
                path,
                response,
                request_json=request_body,
            )
            responses.append({
                "turn_id": turn_id,
                "status_code": response.status_code,
                "body": response.get_json(),
            })
        idempotent_path = (
            f"/api/chat/conversations/{general['id']}/send"
        )
        idempotent_body = {
            "message": turns[0][3],
            "turn_id": turns[0][2],
            "context": general_context,
        }
        idempotent = client.post(
            idempotent_path,
            json=idempotent_body,
        )
        record_exchange(
            "idempotent_complete_retry",
            "POST",
            idempotent_path,
            idempotent,
            request_json=idempotent_body,
        )
        uncertain_turn = "00000000-0000-4000-8000-000000000003"
        uncertain_body = {
            "message": "Synthetic uncertain fixture turn",
            "turn_id": uncertain_turn,
            "context": general_context,
        }
        uncertain_path = (
            f"/api/chat/conversations/{general['id']}/send"
        )
        uncertain_first = client.post(
            uncertain_path,
            json=uncertain_body,
        )
        record_exchange(
            "uncertain_first_attempt",
            "POST",
            uncertain_path,
            uncertain_first,
            request_json=uncertain_body,
        )
        uncertain_retry = client.post(
            uncertain_path,
            json=uncertain_body,
        )
        record_exchange(
            "uncertain_retry_blocked",
            "POST",
            uncertain_path,
            uncertain_retry,
            request_json=uncertain_body,
        )
        pain_patch_path = f"/api/chat/conversations/{pain['id']}"
        archived = client.patch(
            pain_patch_path,
            json={"archived": True},
        )
        record_exchange(
            "archive_pain",
            "PATCH",
            pain_patch_path,
            archived,
            request_json={"archived": True},
        )
        resumed = client.patch(
            pain_patch_path,
            json={"archived": False},
        )
        record_exchange(
            "resume_pain",
            "PATCH",
            pain_patch_path,
            resumed,
            request_json={"archived": False},
        )
        general_messages_path = (
            f"/api/chat/conversations/{general['id']}/messages"
        )
        general_messages = client.get(general_messages_path)
        record_exchange(
            "general_message_history",
            "GET",
            general_messages_path,
            general_messages,
        )
        pain_messages_path = (
            f"/api/chat/conversations/{pain['id']}/messages"
        )
        pain_messages = client.get(pain_messages_path)
        record_exchange(
            "pain_message_history",
            "GET",
            pain_messages_path,
            pain_messages,
        )
        connection = sqlite3.connect(panel_database_path)
        connection.row_factory = sqlite3.Row
        try:
            conversation_rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT id,title,surface,lens,selected_regions_json,
                              range_kind,range_from,range_to,archived_at,
                              hermes_session_id,source_context_json
                         FROM chat_conversations
                        WHERE id IN (?,?) ORDER BY id""",
                    (general["id"], pain["id"]),
                )
            ]
            message_rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT thread,role,content,conversation_id,turn_id,
                              context_json,delivery_status
                         FROM chat_log
                        WHERE conversation_id IN (?,?)
                        ORDER BY conversation_id,id""",
                    (general["id"], pain["id"]),
                )
            ]
        finally:
            connection.close()
    finally:
        panel_bridge.run = original_run
        chat_routes.secrets.token_hex = original_token_hex
        chat_routes._next_updated_at = original_updated
        chat_routes.time = original_time

    conversations_by_id = {
        row["id"]: row for row in conversation_rows
    }
    general_session_id = conversations_by_id[general["id"]]["hermes_session_id"]
    pain_session_id = conversations_by_id[pain["id"]]["hermes_session_id"]
    if general_session_id == pain_session_id:
        raise TraceReplayError("Pain and General shared a Hermes session")
    if not (
        idempotent.status_code == 200
        and idempotent.get_json().get("idempotent") is True
        and uncertain_first.status_code == 502
        and uncertain_retry.status_code == 409
        and uncertain_retry.get_json().get("code") == "turn_uncertain"
    ):
        raise TraceReplayError("conversation idempotent/uncertain boundary failed")
    if archived.status_code != 200 or resumed.status_code != 200:
        raise TraceReplayError("conversation archive/resume failed")
    general_history = general_messages.get_json()
    pain_history = pain_messages.get_json()
    if (
        general_messages.status_code != 200
        or pain_messages.status_code != 200
    ):
        raise TraceReplayError("conversation message-history API failed")
    general_turn_ids = {
        item["turn_id"]
        for item in general_history["messages"]
        if item["turn_id"] is not None
    }
    pain_turn_ids = {
        item["turn_id"]
        for item in pain_history["messages"]
        if item["turn_id"] is not None
    }
    if (
        general_turn_ids != {turns[0][2], uncertain_turn}
        or pain_turn_ids != {turns[1][2]}
        or general_turn_ids & pain_turn_ids
    ):
        raise TraceReplayError("conversation message histories were not isolated")
    expected_context_by_turn = {
        turns[0][2]: chat_routes._trusted_context(  # noqa: SLF001
            "general",
            general["id"],
            general_context,
        ),
        turns[1][2]: chat_routes._trusted_context(  # noqa: SLF001
            "pain",
            pain["id"],
            pain_context,
        ),
        uncertain_turn: chat_routes._trusted_context(  # noqa: SLF001
            "general",
            general["id"],
            general_context,
        ),
    }
    for history in (general_history, pain_history):
        for message in history["messages"]:
            turn_id = message["turn_id"]
            if (
                turn_id is not None
                and message["context"] != expected_context_by_turn[turn_id]
            ):
                raise TraceReplayError(
                    "conversation message-history context mutated"
                )
    if any(call["subcommand"] != "hermes-chat" for call in calls):
        raise TraceReplayError("conversation escaped the hermes-chat boundary")
    panel_rollback = _panel_database_rollback_proof(panel_database_path)
    return {
        "status": "supported",
        "surface": "panel",
        "telegram_used": False,
        "api_exchanges": api_exchanges,
        "api_responses": responses,
        "message_histories": {
            "general": general_history,
            "pain": pain_history,
        },
        "sessions": {
            "general": general_session_id,
            "pain": pain_session_id,
            "distinct": general_session_id != pain_session_id,
            "not_exposed_by_public_api": (
                "hermes_session_id" not in general
                and "hermes_session_id" not in pain
            ),
        },
        "range_contexts": {
            "general": general["context"]["range"],
            "pain": pain["context"]["range"],
        },
        "bridge_calls": calls,
        "conversation_rows": conversation_rows,
        "message_rows": message_rows,
        "conversation_rows_sha256": sha256_json(conversation_rows),
        "message_rows_sha256": sha256_json(message_rows),
        "immutable_per_turn_context": all(
            len({
                row["context_json"]
                for row in message_rows
                if row["turn_id"] == turn_id
            }) == 1
            for turn_id in (turns[0][2], turns[1][2])
        ),
        "archive_resume": resumed.get_json()["conversation"]["archived"] is False,
        "idempotent_complete_retry": idempotent.get_json()["idempotent"] is True,
        "ambiguous_delivery": {
            "first_status": uncertain_first.status_code,
            "retry_status": uncertain_retry.status_code,
            "retry_code": uncertain_retry.get_json()["code"],
            "external_attempts": sum(
                call["envelope"]["message"] == "Synthetic uncertain fixture turn"
                for call in calls
            ),
        },
        "bridge_contract": "hermes-panel-turn-v1",
        "panel_database_rollback": panel_rollback,
    }


def _narrative(fixture: Mapping[str, Any], status: str) -> dict[str, str]:
    values = {
        "Hypothesis": fixture["narrative"]["hypothesis"],
        "Outcome": fixture["narrative"]["outcome"],
        "Exposure/pattern": fixture["narrative"]["exposure_pattern"],
        "Direction/timing": fixture["narrative"]["direction_timing"],
        "Observed result": fixture["narrative"]["observed_result"],
        "Sample size/coverage": "Use the immutable structured evidence above.",
        "Evidence for": "Use only the engine-owned evidence-for records above.",
        "Evidence against": "Retain every engine-owned counterpoint and missingness caveat.",
        "Alternative explanations": fixture["narrative"]["alternative_explanations"],
        "Stability": "Use only the engine-owned chronological stability result.",
        "Confidence": "Use only the ledger-owned confidence; prose cannot change it.",
        "What changes conclusion": fixture["narrative"]["changes_conclusion"],
        "One cheap next experiment": fixture["narrative"]["next_step"],
        "Safety/causality note": (
            "Synthetic fixture only, never an owner finding. Association is not "
            "causation; this is not diagnosis, treatment, or medication advice."
        ),
    }
    if status in {"insufficient", "logic_not_implemented", "suppressed"}:
        values["Observed result"] = (
            "No result is claimed; the exact missing data or owner-approved logic "
            "boundary is recorded."
        )
    return values


def _data_quality_proof(
    connection: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    proof: Mapping[str, Any],
    *,
    scenario_key: str,
) -> dict[str, Any]:
    rating_counts = Counter(
        int(row["day_rating"])
        for row in rows
        if row["table"] == "subjective_daily"
        and row.get("day_rating") in (1, 2, 3)
    )
    ratings_applicable = bool(rating_counts)
    if ratings_applicable and not all(
        rating_counts[value] > 0 for value in (1, 2, 3)
    ):
        raise TraceReplayError("day-rating fixture did not preserve all three levels")
    three_way = {
        "applicable": ratings_applicable,
        "semantic_values": (
            {"Green": 3, "yellow": 2, "red": 1}
            if ratings_applicable
            else None
        ),
        "exact_counts": (
            {
                "Green": rating_counts[3],
                "yellow": rating_counts[2],
                "red": rating_counts[1],
            }
            if ratings_applicable
            else None
        ),
        "all_three_preserved": True if ratings_applicable else None,
        "yellow_collapsed_to_red": False if ratings_applicable else None,
    }
    if scenario_key == "food_green_day":
        modes = proof["analysis"]["mode_coverage"]
        required_modes = {
            "ordinal",
            "green-vs-non-green",
            "red-vs-non-red",
        }
        if set(modes) != required_modes:
            raise TraceReplayError("three-way day-rating modes were not all exercised")
        three_way["actual_mode_coverage"] = modes
    source_sync_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT source,started_at,completed_at,status,coverage_from,
                      coverage_to,rows_seen,rows_written,details_json
                 FROM source_sync_runs ORDER BY source,completed_at,id"""
        )
    ]
    completeness_counts = dict(sorted(Counter(
        (
            str(row["scope"]),
            str(row["state"]),
            str(row["source"]),
        )
        for row in rows
        if row["table"] == "capture_completeness_revisions"
    ).items()))
    if isinstance(proof.get("frame"), Mapping):
        frame_missingness = proof["frame"].get("state_counts")
    elif "missing_coverage_state_counts" in proof:
        frame_missingness = proof["missing_coverage_state_counts"]
    elif "measurement_distinctions" in proof:
        frame_missingness = dict(sorted(Counter(
            observation["state"]
            for item in proof["measurement_distinctions"].values()
            for observation in item.get("observations", [])
        ).items()))
    elif proof.get("status") == "insufficient":
        frame_missingness = {
            "readiness_state": proof.get("readiness_state"),
            "finding_count": len(proof.get("findings", [])),
        }
    else:
        frame_missingness = None
    return {
        "day_rating_three_way": three_way,
        "completeness_by_scope_state_source": [
            {
                "scope": key[0],
                "state": key[1],
                "source": key[2],
                "count": value,
            }
            for key, value in completeness_counts.items()
        ],
        "source_sync_rows": source_sync_rows,
        "collector_freshness": orchestrator.freshness_snapshot(
            connection,
            now=FIXED_NOW,
        ),
        "missingness_evidence": {
            "actual_frame_or_readiness_states": frame_missingness,
            "seed_null_field_count": sum(
                value is None
                for row in rows
                for key, value in row.items()
                if key != "table"
            ),
        },
    }


def _ledger_boundary_proof(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    evaluation_query = (
        "SELECT id,hypothesis_id,run_id,finding_id,evidence_class,"
        "previous_status,status,change_reason,confidence,"
        "previous_evaluation_id,comparison_evaluation_id,"
        "new_eligible_observations,transition_applied,evidence_fingerprint "
        "FROM hypothesis_evaluations ORDER BY id"
    )
    evaluation_rows = [
        dict(row) for row in connection.execute(evaluation_query)
    ]
    hypothesis_query = (
        "SELECT hypothesis_id,outcome_key,outcome_mode,candidate_kind,"
        "initial_direction,created_by_run,created_by_finding "
        "FROM hypotheses ORDER BY hypothesis_id"
    )
    hypothesis_rows = [
        dict(row) for row in connection.execute(hypothesis_query)
    ]
    evidence_query = (
        "SELECT evidence_item_id,hypothesis_id,evaluation_id,finding_id,"
        "polarity,evidence_kind,evidence_fingerprint,range_from,range_to "
        "FROM hypothesis_evidence_items "
        "ORDER BY evaluation_id,evidence_item_id"
    )
    evidence_rows = [
        dict(row) for row in connection.execute(evidence_query)
    ]
    finding_query = (
        "SELECT f.finding_id,f.run_id,f.outcome_key,f.outcome_mode,"
        "f.quality_tier,f.eligible_for_hypothesis,f.evidence_fingerprint "
        "FROM analysis_findings f ORDER BY f.finding_id"
    )
    finding_rows = [
        dict(row) for row in connection.execute(finding_query)
    ]
    transition_rows = [
        row for row in evaluation_rows if row["transition_applied"] == 1
    ]
    return {
        "queries": {
            "evaluations": evaluation_query,
            "hypotheses": hypothesis_query,
            "evidence_items": evidence_query,
            "findings": finding_query,
        },
        "transition": {
            "evaluation_count": len(evaluation_rows),
            "applied_count": len(transition_rows),
            "none_reason": (
                "no_hypothesis_promotion_or_refresh_requested"
                if not evaluation_rows
                else "evaluations_exist_but_no_transition_applied"
                if not transition_rows
                else None
            ),
            "applied_rows": transition_rows,
        },
        "hypotheses": hypothesis_rows,
        "evaluations": evaluation_rows,
        "evidence_items": evidence_rows,
        "analysis_finding_count": len(finding_rows),
        "analysis_findings_sha256": sha256_json(finding_rows),
        "ledger_rows_sha256": sha256_json({
            "hypotheses": hypothesis_rows,
            "evaluations": evaluation_rows,
            "evidence_items": evidence_rows,
        }),
    }


def _rollback_database_proof(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    tables = (
        "schema_migrations",
        "analysis_batches",
        "analysis_range_requests",
        "analysis_runs",
        "analysis_findings",
        "hypotheses",
        "hypothesis_evaluations",
        "hypothesis_evidence_items",
        "synthesis_runs",
        "insight_triggers",
        "insight_trigger_events",
        "insight_notification_outbox",
        "insight_notification_events",
    )
    row_counts = {
        table: connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        for table in tables
    }

    def states(table: str) -> dict[str, int]:
        return {
            row["state"]: row["count"]
            for row in connection.execute(
                f"""SELECT state,COUNT(*) AS count FROM {table}
                     GROUP BY state ORDER BY state"""
            )
        }

    batch_statuses = {
        row["status"]: row["count"]
        for row in connection.execute(
            """SELECT status,COUNT(*) AS count FROM analysis_batches
                GROUP BY status ORDER BY status"""
        )
    }
    synthesis_statuses = {
        row["status"]: row["count"]
        for row in connection.execute(
            """SELECT status,COUNT(*) AS count FROM synthesis_runs
                GROUP BY status ORDER BY status"""
        )
    }
    return {
        "database_scope": "temporary_fixture_only",
        "integrity": {
            "quick_check": connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0],
            "foreign_key_violations": len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            ),
        },
        "row_counts": row_counts,
        "row_counts_sha256": sha256_json(row_counts),
        "state_counts": {
            "analysis_batches": batch_statuses,
            "synthesis_runs": synthesis_statuses,
            "insight_triggers": states("insight_triggers"),
            "insight_notification_outbox": states(
                "insight_notification_outbox"
            ),
        },
        "recovery": "discard temporary databases",
    }


def _actual_contract_calls(
    fixture: Mapping[str, Any],
    proof: Mapping[str, Any],
    *,
    migration_result: Mapping[str, Any],
    registry_payload: Mapping[str, Any],
    exact_panel_payloads: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Describe only contract calls that this replay actually completed."""

    scenario_key = fixture["scenario_key"]
    requested = {
        "kind": "bounded",
        "from": fixture["range"]["from"],
        "to": fixture["range"]["to"],
    }

    def call(
        contract: str,
        *,
        arguments: Mapping[str, Any],
        result: Any,
        invocation_count: int = 1,
        result_digest_scope: str = "trace_projection",
    ) -> dict[str, Any]:
        return {
            "contract": contract,
            "boundary": "direct_in_process_python",
            "invoked": True,
            "invocation_count": invocation_count,
            "arguments": dict(arguments),
            "result_digest_scope": result_digest_scope,
            "result_sha256": sha256_json(result),
        }

    calls = [
        call(
            "hermes_insights.migrations.migrate",
            arguments={
                "from_version": 0,
                "to_version": migrations.AUTONOMOUS_SCHEMA_VERSION,
            },
            result=migration_result,
            result_digest_scope="full_return_value",
        ),
        call(
            "hermes_insights.registry.serialize_registry",
            arguments={
                "adapter_context_version": "v2",
                "caller_source_table_filter": None,
            },
            result=registry_payload,
            result_digest_scope="full_return_value",
        ),
    ]

    def outbox_calls(
        outbox: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        common_arguments = {
            "sender_invoked": False,
            "destination_identifier": None,
        }
        return [
            call(
                "hermes_insights.orchestrator.enqueue_notification",
                arguments=common_arguments,
                result={
                    "pending": outbox["pending"],
                    "exact_duplicate": outbox["exact_duplicate"],
                },
                invocation_count=2,
            ),
            call(
                "hermes_insights.orchestrator.claim_notification",
                arguments=common_arguments,
                result={
                    "claim": outbox["claim"],
                    "automatic_retry_after_uncertain": outbox[
                        "automatic_retry_after_uncertain"
                    ],
                },
                invocation_count=2,
            ),
            call(
                "hermes_insights.orchestrator.begin_notification_dispatch",
                arguments=common_arguments,
                result=outbox["dispatch_fence"],
                invocation_count=2,
            ),
            call(
                "hermes_insights.orchestrator.fail_notification",
                arguments={
                    **common_arguments,
                    "failure_class": "ambiguous",
                },
                result=outbox["ambiguous_after_dispatch"],
            ),
            call(
                "hermes_insights.orchestrator.resolve_notification",
                arguments={
                    **common_arguments,
                    "resolution": "confirmed_not_sent_retry",
                },
                result=outbox["operator_only_resolution"],
            ),
        ]
    if scenario_key in {
        "food_green_day",
        "social_mood",
        "workout_next_day",
        "food_social_pair",
    }:
        outcome = (
            "subjective.mood"
            if scenario_key == "social_mood"
            else "subjective.day_rating"
        )
        mode = (
            "ordinal"
            if scenario_key == "social_mood"
            else "all"
            if scenario_key == "food_green_day"
            else "green-vs-non-green"
        )
        calls.extend([
            call(
                "hermes_insights.frame.build_feature_frame",
                arguments={
                    "range": requested,
                    "include_provenance": True,
                },
                result=proof["frame"],
            ),
            call(
                "hermes_insights.readiness.build_readiness",
                arguments={"range": requested, "outcome": outcome},
                result=proof["readiness"],
            ),
            call(
                "hermes_insights.associations.analyze_outcome",
                arguments={
                    "range": requested,
                    "outcome_key": outcome,
                    "mode": mode,
                    "min_n": 30,
                    "interactions": (
                        "pairwise"
                        if scenario_key == "food_social_pair"
                        else "none"
                    ),
                    "top": 100,
                },
                result=proof["analysis"],
            ),
        ])
        if scenario_key in {
            "food_green_day",
            "social_mood",
            "workout_next_day",
        }:
            calls.append(call(
                "hermes_insights.associations.analyze_outcome",
                arguments={
                    "range": requested,
                    "outcome_key": outcome,
                    "mode": mode,
                    "caller_exposure_filter": None,
                    "caller_adapter_filter": None,
                    "caller_source_table_filter": None,
                    "min_n": 30,
                    "interactions": "none",
                    "top": 100,
                },
                result=proof["registry_discovery"][
                    "full_registry_analysis"
                ],
            ))
        if scenario_key == "food_green_day":
            calls.append(call(
                "hermes_insights.associations.analyze_outcome",
                arguments={
                    "range": requested,
                    "outcome_key": outcome,
                    "mode": "green-vs-non-green",
                    "control": "null_relationship",
                    "min_n": 30,
                    "interactions": "none",
                    "top": 100,
                },
                result=proof["analysis"]["null_relationship_control"],
            ))
        if scenario_key == "food_social_pair":
            calls.extend([
                call(
                    "hermes_insights.interactions.analyze_pairwise",
                    arguments={
                        "range": requested,
                        "mode": "green-vs-non-green",
                        "control": "both_cell_exactly_10",
                    },
                    result=proof["sparse_negative_control"],
                ),
                *outbox_calls(proof["notification_outbox_proof"]),
            ])
    elif scenario_key == "running_restart_pain":
        calls.extend([
            call(
                "hermes_insights.frame.build_feature_frame",
                arguments={
                    "range": requested,
                    "include_provenance": True,
                    "workout_completeness_capability": True,
                },
                result=proof["completeness_proven_markers"],
            ),
            call(
                "hermes_insights.frame.build_feature_frame",
                arguments={
                    "range": requested,
                    "include_provenance": True,
                    "workout_completeness_capability": False,
                },
                result=proof["missing_coverage_state_counts"],
            ),
            call(
                "hermes_insights.adapters.pain.supplemental",
                arguments={
                    "range": requested,
                    "include_provenance": True,
                },
                result=proof["pain_timeline"],
            ),
        ])
    elif scenario_key == "quarterly_asymmetry":
        calls.extend([
            call(
                "hermes_insights.frame.build_feature_frame",
                arguments={
                    "range": requested,
                    "include_provenance": True,
                    "purpose": "quarterly_measurement_and_outcome",
                },
                result=proof["measurement_episodes"],
            ),
            call(
                "hermes_insights.associations.analyze_outcome",
                arguments={
                    "range": requested,
                    "outcome_key": "pain.nrs.anterior-knee.left",
                    "mode": "ordinal",
                    "min_n": 30,
                    "interactions": "none",
                    "top": 100,
                },
                result=proof["analysis"],
            ),
            call(
                "hermes_insights.frame.build_feature_frame",
                arguments={
                    "range": requested,
                    "include_provenance": True,
                    "purpose": "measurement_role_distinctions",
                },
                result=proof["measurement_distinctions"],
            ),
        ])
    elif scenario_key == "insufficient_data":
        calls.extend([
            call(
                "hermes_insights.readiness.build_readiness",
                arguments={
                    "range": requested,
                    "outcome": "pain.nrs.anterior-knee.left",
                },
                result={
                    "state": proof["readiness_state"],
                    "needed": proof["exact_needed"],
                    "prerequisites": proof["prerequisites"],
                },
            ),
            call(
                "hermes_insights.associations.analyze_outcome",
                arguments={
                    "range": requested,
                    "outcome_key": "pain.nrs.anterior-knee.left",
                    "mode": "ordinal",
                    "min_n": 30,
                    "interactions": "none",
                    "top": 100,
                },
                result={
                    "findings": proof["findings"],
                    "suppression_counts": proof["suppression_counts"],
                },
            ),
        ])
    elif scenario_key == "reversal":
        calls.extend([
            call(
                "hermes_insights.ledger.compute_verified_analysis",
                arguments={
                    "windows": fixture["seed"]["windows"],
                    "outcome_key": "subjective.day_rating",
                    "outcome_mode": "green-vs-non-green",
                    "interactions": "none",
                },
                result=proof["finding_windows"],
                invocation_count=3,
            ),
            call(
                "hermes_insights.ledger.create_analysis_batch",
                arguments={
                    "batch_ids": proof["analysis_batch_ids"],
                    "append_only": True,
                },
                result=proof["analysis_batch_ids"],
                invocation_count=3,
            ),
            call(
                "hermes_insights.ledger.start_analysis_run",
                arguments={"batch_ids": proof["analysis_batch_ids"]},
                result=proof["finding_windows"],
                invocation_count=3,
            ),
            call(
                "hermes_insights.ledger.persist_analysis_run",
                arguments={"append_only": True},
                result=proof["finding_windows"],
                invocation_count=3,
            ),
            call(
                "hermes_insights.ledger.finalize_analysis_batch",
                arguments={"batch_ids": proof["analysis_batch_ids"]},
                result=proof["analysis_batch_ids"],
                invocation_count=3,
            ),
            call(
                "hermes_insights.ledger.compute_verified_finding",
                arguments={
                    "window": fixture["seed"]["windows"][0],
                    "explicit_recomputation": True,
                },
                result=proof["finding_windows"][0],
            ),
            call(
                "hermes_insights.ledger.promote_verified_finding",
                arguments={"explicit": True},
                result=proof["ledger_evaluations"][0],
            ),
            call(
                "hermes_insights.ledger.refresh_batch_hypotheses",
                arguments={
                    "batch_ids": proof["analysis_batch_ids"][1:],
                },
                result={
                    "evaluations": proof["ledger_evaluations"][1:],
                    "evidence_items": proof["ledger_evidence_items"],
                },
                invocation_count=2,
            ),
            call(
                "hermes_insights.orchestrator.structured_slots",
                arguments={
                    "cadence": "manual",
                    "persisted_rows_only": True,
                },
                result=proof["production_synthesis_preparation"],
            ),
            *outbox_calls(proof["notification_outbox_proof"]),
        ])
    elif scenario_key == "weekly_no_novelty":
        calls.extend([
            call(
                "health._phase6_prepare_batch",
                arguments={
                    "kind": "weekly",
                    "anchors": [
                        fixture["seed"]["first_anchor"],
                        fixture["seed"]["later_anchor"],
                    ],
                },
                result={
                    "first": proof["first_baseline"],
                    "later": proof["later_unchanged"],
                },
                invocation_count=2,
            ),
            call(
                "health._phase6_compute_runs",
                arguments={
                    "kind": "weekly",
                    "anchors": [
                        fixture["seed"]["first_anchor"],
                        fixture["seed"]["later_anchor"],
                    ],
                },
                result={
                    "first": proof["first_baseline"],
                    "later": proof["later_unchanged"],
                },
                invocation_count=2,
            ),
            call(
                "health._phase6_finalize",
                arguments={
                    "kind": "weekly",
                    "anchors": [
                        fixture["seed"]["first_anchor"],
                        fixture["seed"]["later_anchor"],
                    ],
                },
                result={
                    "first": proof["first_baseline"],
                    "later": proof["later_unchanged"],
                },
                invocation_count=2,
            ),
            call(
                "hermes_insights.orchestrator.structured_slots",
                arguments={
                    "cadence": "manual",
                    "persisted_rows_only": True,
                },
                result=proof["manual_baseline"][
                    "production_synthesis_preparation"
                ],
            ),
            call(
                "hermes_insights.synthesis.record_synthesis",
                arguments={
                    "cadence": "manual",
                    "origin": "fixture_generated/not_live_model",
                    "model_invoked": False,
                    "numeric_or_status_authority": False,
                },
                result={
                    "synthesis_id": proof["manual_baseline"][
                        "synthesis_id"
                    ],
                    "narrative_section_headings": proof[
                        "manual_baseline"
                    ]["narrative_section_headings"],
                },
            ),
        ])
    elif scenario_key != "conversation_isolation":
        raise TraceReplayError(
            f"actual contract call manifest missing for {scenario_key}"
        )

    if "outcome-associations" in exact_panel_payloads:
        outcome_key = (
            "subjective.mood"
            if scenario_key == "social_mood"
            else "pain.nrs.anterior-knee.left"
            if scenario_key == "quarterly_asymmetry"
            else "subjective.day_rating"
        )
        mode = (
            "ordinal"
            if scenario_key in {"social_mood", "quarterly_asymmetry"}
            else "green-vs-non-green"
        )
        calls.append(call(
            "hermes_insights.associations.analyze_outcome",
            arguments={
                "purpose": "exact_panel_bridge_payload",
                "range": requested,
                "full_registry": True,
                "outcome_key": outcome_key,
                "mode": mode,
                "min_n": associations.DEFAULT_MIN_N,
                "interactions": "none",
                "top": associations.DEFAULT_TOP,
            },
            result=exact_panel_payloads["outcome-associations"],
            result_digest_scope="full_return_value",
        ))
    if "data-readiness" in exact_panel_payloads:
        calls.append(call(
            "hermes_insights.readiness.build_readiness",
            arguments={
                "purpose": "exact_panel_bridge_payload",
                "range": requested,
                "full_registry": True,
                "goal": None,
                "outcome": (
                    "pain.nrs.anterior-knee.left"
                    if scenario_key == "insufficient_data"
                    else None
                ),
            },
            result=exact_panel_payloads["data-readiness"],
            result_digest_scope="full_return_value",
        ))
    if "hypothesis-brief" in exact_panel_payloads:
        calls.append(call(
            "hermes_insights.ledger.hypothesis_brief",
            arguments={
                "purpose": "exact_panel_bridge_payload",
                "hypothesis_id": proof["hypothesis_id"],
            },
            result=exact_panel_payloads["hypothesis-brief"],
            result_digest_scope="full_return_value",
        ))
    if "synthesis-history" in exact_panel_payloads:
        calls.append(call(
            "hermes_insights.synthesis.synthesis_history",
            arguments={
                "purpose": "exact_panel_bridge_payload",
                "limit": 10,
                "before": None,
            },
            result=exact_panel_payloads["synthesis-history"],
            result_digest_scope="full_return_value",
        ))
    if "insight-run-status" in exact_panel_payloads:
        calls.append(call(
            "hermes_insights.orchestrator.insight_run_status",
            arguments={
                "purpose": "exact_panel_bridge_payload",
                "limit": 10,
            },
            result=exact_panel_payloads["insight-run-status"],
            result_digest_scope="full_return_value",
        ))
    return calls


def _exact_panel_subcommand_payloads(
    connection: sqlite3.Connection,
    context: AdapterContext,
    definitions: Sequence[Any],
    fixture: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute the exact real payload builders behind the panel bridge."""

    scenario_key = fixture["scenario_key"]
    requested = _bounded(fixture["range"])
    if scenario_key in {
        "food_green_day",
        "food_social_pair",
        "workout_next_day",
        "social_mood",
        "quarterly_asymmetry",
    }:
        outcome_key = (
            "subjective.mood"
            if scenario_key == "social_mood"
            else "pain.nrs.anterior-knee.left"
            if scenario_key == "quarterly_asymmetry"
            else "subjective.day_rating"
        )
        mode = (
            "ordinal"
            if scenario_key in {"social_mood", "quarterly_asymmetry"}
            else "green-vs-non-green"
        )
        payload = associations.analyze_outcome(
            connection,
            definitions,
            requested,
            context,
            outcome_key=outcome_key,
            mode=mode,
            min_n=associations.DEFAULT_MIN_N,
            interactions="none",
            top=associations.DEFAULT_TOP,
        )
        if payload.get("contract_version") != (
            provenance.ANALYSIS_CONTRACT_VERSION
        ):
            raise TraceReplayError(
                "panel outcome-associations payload contract drifted"
            )
        return {"outcome-associations": payload}
    if scenario_key in {"running_restart_pain", "insufficient_data"}:
        outcome = (
            "pain.nrs.anterior-knee.left"
            if scenario_key == "insufficient_data"
            else None
        )
        payload = readiness.build_readiness(
            connection,
            definitions,
            requested,
            context,
            outcome=outcome,
        )
        if (
            payload.get("meta", {}).get("readiness_version")
            != readiness.READINESS_VERSION
            or payload.get("meta", {}).get("outcome") != outcome
        ):
            raise TraceReplayError(
                "panel data-readiness payload contract drifted"
            )
        return {"data-readiness": payload}
    if scenario_key == "reversal":
        payload = ledger.hypothesis_brief(
            connection,
            proof["hypothesis_id"],
        )
        if payload.get("contract_version") != ledger.LEDGER_CONTRACT_VERSION:
            raise TraceReplayError(
                "panel hypothesis-brief payload contract drifted"
            )
        return {"hypothesis-brief": payload}
    if scenario_key == "weekly_no_novelty":
        synthesis_payload = synthesis.synthesis_history(
            connection,
            limit=10,
        )
        run_payload = orchestrator.insight_run_status(
            connection,
            limit=10,
        )
        if (
            synthesis_payload.get("contract")
            != synthesis.SYNTHESIS_CONTRACT_VERSION
            or run_payload.get("contract_version")
            != orchestrator.ORCHESTRATOR_CONTRACT_VERSION
        ):
            raise TraceReplayError(
                "panel weekly history/status payload contract drifted"
            )
        return {
            "synthesis-history": synthesis_payload,
            "insight-run-status": run_payload,
        }
    raise TraceReplayError(
        f"no exact panel subcommand payload for {scenario_key}"
    )


def _panel_api_contract_proof(
    fixture: Mapping[str, Any],
    proof: Mapping[str, Any],
    panel_database_path: Path,
    exact_subcommand_payloads: Mapping[str, Any],
) -> dict[str, Any]:
    """Pass the real result proof through the production Flask JSON routes."""

    wire_payloads = {
        key: json.loads(canonical_json(value))
        for key, value in exact_subcommand_payloads.items()
    }
    scenario_key = fixture["scenario_key"]
    start, end = fixture["range"]["from"], fixture["range"]["to"]
    if scenario_key in {
        "food_green_day",
        "food_social_pair",
        "workout_next_day",
    }:
        request_path = (
            "/api/insights/outcomes?outcome=subjective.day_rating"
            "&mode=green-vs-non-green&range=bounded"
            f"&from={start}&to={end}"
        )
        expected_call = {
            "subcommand": "outcome-associations",
            "args": [
                "--outcome",
                "subjective.day_rating",
                "--mode",
                "green-vs-non-green",
                "--from",
                start,
                "--to",
                end,
            ],
            "timeout": 580.0,
        }
        request_specs = [(request_path, expected_call)]
    elif scenario_key == "social_mood":
        request_path = (
            "/api/insights/outcomes?outcome=subjective.mood"
            "&mode=ordinal&range=bounded"
            f"&from={start}&to={end}"
        )
        expected_call = {
            "subcommand": "outcome-associations",
            "args": [
                "--outcome",
                "subjective.mood",
                "--mode",
                "ordinal",
                "--from",
                start,
                "--to",
                end,
            ],
            "timeout": 580.0,
        }
        request_specs = [(request_path, expected_call)]
    elif scenario_key == "running_restart_pain":
        request_path = (
            "/api/insights/readiness?range=bounded"
            f"&from={start}&to={end}"
        )
        expected_call = {
            "subcommand": "data-readiness",
            "args": ["--from", start, "--to", end],
            "timeout": 75.0,
        }
        request_specs = [(request_path, expected_call)]
    elif scenario_key == "quarterly_asymmetry":
        request_path = (
            "/api/insights/outcomes?"
            "outcome=pain.nrs.anterior-knee.left"
            "&mode=ordinal&range=bounded"
            f"&from={start}&to={end}"
        )
        expected_call = {
            "subcommand": "outcome-associations",
            "args": [
                "--outcome",
                "pain.nrs.anterior-knee.left",
                "--mode",
                "ordinal",
                "--from",
                start,
                "--to",
                end,
            ],
            "timeout": 580.0,
        }
        request_specs = [(request_path, expected_call)]
    elif scenario_key == "insufficient_data":
        request_path = (
            "/api/insights/readiness?range=bounded"
            f"&from={start}&to={end}"
            "&outcome=pain.nrs.anterior-knee.left"
        )
        expected_call = {
            "subcommand": "data-readiness",
            "args": [
                "--from",
                start,
                "--to",
                end,
                "--outcome",
                "pain.nrs.anterior-knee.left",
            ],
            "timeout": 75.0,
        }
        request_specs = [(request_path, expected_call)]
    elif scenario_key == "reversal":
        request_path = (
            f"/api/insights/hypotheses/{proof['hypothesis_id']}"
        )
        expected_call = {
            "subcommand": "hypothesis-brief",
            "args": [proof["hypothesis_id"]],
            "timeout": 75.0,
        }
        request_specs = [(request_path, expected_call)]
    elif scenario_key == "weekly_no_novelty":
        request_specs = [
            (
                "/api/insights/syntheses?limit=10",
                {
                    "subcommand": "synthesis-history",
                    "args": ["--limit", "10"],
                    "timeout": 75.0,
                },
            ),
            (
                "/api/insights/runs?limit=10",
                {
                    "subcommand": "insight-run-status",
                    "args": ["--limit", "10"],
                    "timeout": 75.0,
                },
            ),
        ]
    else:
        raise TraceReplayError(
            f"no panel API proof route for {scenario_key}"
        )

    calls: list[dict[str, Any]] = []
    original_run = panel_bridge.run

    def runner(
        subcommand: str,
        *args: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        calls.append({
            "subcommand": subcommand,
            "args": list(args),
            "timeout": kwargs["timeout"],
        })
        try:
            return wire_payloads[subcommand]
        except KeyError as exc:
            raise TraceReplayError(
                f"no exact fake payload for {subcommand}"
            ) from exc

    exchanges: list[dict[str, Any]] = []
    try:
        panel_bridge.run = runner
        app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "PANEL_DB": str(panel_database_path),
            "PANEL_COOKIE_SECURE": False,
        })
        with app.app_context():
            token = panel_auth.create_session()
        client = app.test_client()
        client.set_cookie(panel_auth.SESSION_COOKIE, token)
        for request_path, expected_call in request_specs:
            prior_call_count = len(calls)
            response = client.get(request_path)
            body = response.get_json()
            exact_payload = wire_payloads[
                expected_call["subcommand"]
            ]
            expected = {"ok": True, "result": exact_payload}
            new_calls = calls[prior_call_count:]
            if (
                response.status_code != 200
                or body != expected
                or new_calls != [expected_call]
            ):
                raise TraceReplayError(
                    "panel canonical JSON API pass-through proof failed: "
                    f"status={response.status_code}, "
                    f"body_matches={body == expected}, "
                    f"body_sha256={sha256_json(body)}, "
                    f"expected_sha256={sha256_json(expected)}, "
                    f"calls={new_calls!r}, expected_call={expected_call!r}"
                )
            exchanges.append({
                "executed": True,
                "request": {
                    "method": "GET",
                    "path_and_query": request_path,
                },
                "bridge_call": new_calls[0],
                "status_code": response.status_code,
                "response": body,
                "response_sha256": sha256_json(body),
            })
    finally:
        panel_bridge.run = original_run
    if calls != [item[1] for item in request_specs]:
        raise TraceReplayError(
            "panel JSON API bridge-call sequence drifted"
        )
    canonical_response: Any = (
        exchanges[0]["response"]
        if len(exchanges) == 1
        else [item["response"] for item in exchanges]
    )
    panel_rollback = _panel_database_rollback_proof(panel_database_path)
    return {
        "actual_flask_test_client": True,
        "api_exchanges": exchanges,
        "requests": [item["request"] for item in exchanges],
        "bridge_calls": calls,
        "system_service_boundary": (
            "deterministic_fake_exact_result/no_live_broker"
        ),
        "canonical_response": canonical_response,
        "canonical_response_sha256": sha256_json(canonical_response),
        "all_results_match_exact_subcommand_payloads": all(
            item["response"]["result"]
            == wire_payloads[item["bridge_call"]["subcommand"]]
            for item in exchanges
        ),
        "panel_database_rollback": panel_rollback,
    }


def _execution_record(
    fixture: Mapping[str, Any],
    *,
    actual_contract_calls: Sequence[Mapping[str, Any]],
    panel_api_replay: Mapping[str, Any],
) -> dict[str, Any]:
    """Separate non-invoked CLI examples from actually executed API calls."""

    actual_exchanges = panel_api_replay["api_exchanges"]
    conversation_ids = {
        item["response"]["conversation"]["lens"]: item["response"][
            "conversation"
        ]["id"]
        for item in actual_exchanges
        if item.get("label") in {"create_general", "create_pain"}
    }
    canonical_references = []
    declared_api_references = []
    for statement in fixture["commands_or_api"]:
        prefix = statement.split(" ", 1)[0]
        if prefix not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            if statement.startswith("fixture collector boundary:"):
                reason = "fixture_boundary_description_not_a_command"
            elif statement.split(" ", 1)[0] in {
                "capture-raw",
                "event-log",
                "capture-completeness-set",
                "fitness-test-log",
            }:
                reason = "fixture_rows_seeded_directly_not_via_cli"
            else:
                reason = "canonical_cli_reference_not_invoked"
            canonical_references.append({
                "statement": statement,
                "classification": "canonical_contract_reference",
                "invoked_as_cli_or_subprocess": False,
                "equivalent_real_contracts_recorded": False,
                "not_invoked_reason": reason,
                "actual_operations_recorded_independently": True,
            })
            continue
        parts = statement.split(" ", 2)
        declared_method = parts[0]
        declared_path = parts[1]
        qualifiers = parts[2].split() if len(parts) == 3 else []

        def qualifier_match(item: Mapping[str, Any]) -> bool:
            body = item["request"].get("json", {})
            for qualifier in qualifiers:
                key, separator, value = qualifier.partition("=")
                if not separator:
                    continue
                if key == "lens" and body.get("lens") != value:
                    return False
                if key == "archived" and body.get("archived") is not (
                    value == "true"
                ):
                    return False
                if key == "range":
                    actual_range = body.get("context", {}).get("range")
                    if value == "all" and actual_range != {"kind": "all"}:
                        return False
                    if ".." in value:
                        range_start, range_end = value.split("..", 1)
                        if actual_range != {
                            "kind": "bounded",
                            "from": range_start,
                            "to": range_end,
                        }:
                            return False
            return True

        if "<" in declared_path:
            import re

            path_template = declared_path
            for lens, conversation_id in conversation_ids.items():
                path_template = path_template.replace(
                    f"<{lens}>",
                    conversation_id,
                )
            pattern = "^" + re.sub(
                r"<[^>]+>",
                r"[^/?]+",
                re.escape(path_template)
                .replace(r"\<", "<")
                .replace(r"\>", ">"),
            ) + "$"
            matches = [
                item
                for item in actual_exchanges
                if item["request"]["method"] == declared_method
                and re.fullmatch(pattern, item["request"]["path_and_query"])
                and qualifier_match(item)
            ]
            classification = "executed_route_template"
        else:
            matches = [
                item
                for item in actual_exchanges
                if item["request"]["method"] == declared_method
                and item["request"]["path_and_query"] == declared_path
                and qualifier_match(item)
            ]
            classification = "executed_exact_request"
        if not matches:
            raise TraceReplayError(
                f"declared API was not executed: {statement}"
            )
        declared_api_references.append({
            "statement": statement,
            "classification": classification,
            "executed": True,
            "matched_exchange_labels": [
                item.get("label", item["request"]["path_and_query"])
                for item in matches
            ],
        })
    return {
        "inclusive_range": fixture["range"],
        "timezone": "Europe/Paris",
        "canonical_contract_references_not_invoked_as_cli": (
            canonical_references
        ),
        "declared_api_references": declared_api_references,
        "actual_contract_calls": list(actual_contract_calls),
        "actual_api_exchanges": actual_exchanges,
    }


def _scenario_panel_boundary(
    fixture: Mapping[str, Any],
    proof: Mapping[str, Any],
    panel_api_replay: Mapping[str, Any],
) -> dict[str, Any]:
    """State exactly what the API/static proof does and does not establish."""

    scenario_key = fixture["scenario_key"]
    if scenario_key == "food_social_pair":
        exact_payload = panel_api_replay["api_exchanges"][0]["response"][
            "result"
        ]
        pair_finding_id = proof["analysis"]["finding"]["finding_id"]
        returned_ids = [
            item["finding_id"] for item in exact_payload["findings"]
        ]
        source = INSIGHTS_JS_PATH.read_text(encoding="utf-8")
        static_checks = {
            "joins_component_names_with_plus": (
                '.filter(Boolean).join(" + ")' in source
            ),
            "iterates_every_component": (
                "components.forEach((component) => {" in source
            ),
        }
        if (
            exact_payload["meta"]["interactions"] != "none"
            or pair_finding_id in returned_ids
            or not all(static_checks.values())
        ):
            raise TraceReplayError(
                "pair panel reachability/static boundary drifted"
            )
        return {
            "status": "suppressed",
            "reason_code": "canonical_panel_route_interactions_none",
            "actual_api_contains_pair_finding": False,
            "actual_api_interactions_argument": "none",
            "real_pair_engine_proof_sha256": sha256_json({
                "finding": proof["analysis"]["finding"],
                "gate": proof["pair_gate_proof"],
                "sparse_control": proof["sparse_negative_control"],
            }),
            "static_multi_component_card_structure": {
                "source": "app/static/js/insights.js",
                "source_sha256": INSIGHTS_JS_SHA256,
                **static_checks,
            },
            "static_structure_reachable_from_current_pair_api": False,
            "static_structure_is_rendered_review": False,
            "pair_ui_rendering_claimed": False,
        }
    if scenario_key == "running_restart_pain":
        return {
            "status": "suppressed",
            "reason_code": (
                "readiness_route_does_not_return_supplemental_pain_timeline"
            ),
            "actual_api_contains_running_pain_timeline": False,
            "real_timeline_engine_proof_sha256": sha256_json({
                "markers": proof["completeness_proven_markers"],
                "pain_timeline": proof["pain_timeline"],
            }),
            "timeline_ui_rendering_claimed": False,
            "static_structure_is_rendered_review": False,
        }
    return {
        "status": "supported",
        "proof_scope": "exact_api_contract_and_static_structure_only",
        "actual_screenshot_or_rendered_owner_review": False,
    }


def build_trace(
    fixture: Mapping[str, Any],
    database_path: Path,
) -> dict[str, Any]:
    """Replay one scenario into a canonical, secret-free trace object."""

    scenario_key = fixture["scenario_key"]
    if scenario_key not in SEEDERS:
        raise TraceReplayError(f"unknown scenario key: {scenario_key}")
    connection, migration_result = create_current_database(database_path)
    try:
        running_boundary = scenario_key in {"workout_next_day", "running_restart_pain"}
        context = fixture_context(running_completeness=running_boundary)
        rows, seed_meta = SEEDERS[scenario_key](connection, fixture)
        definitions, serialized = _registry_bundle(connection, context)
        if scenario_key in {"food_green_day", "social_mood", "workout_next_day"}:
            proof = _identity_association_proof(
                connection, context, definitions, fixture, seed_meta
            )
        elif scenario_key == "food_social_pair":
            proof = _pair_proof(
                connection, context, definitions, fixture, seed_meta
            )
        elif scenario_key == "running_restart_pain":
            proof = _running_proof(connection, context, definitions, fixture)
        elif scenario_key == "quarterly_asymmetry":
            proof = _quarterly_proof(
                connection, context, definitions, fixture, seed_meta
            )
        elif scenario_key == "insufficient_data":
            proof = _insufficient_proof(
                connection, context, definitions, fixture, seed_meta
            )
        elif scenario_key == "reversal":
            proof = _reversal_proof(
                connection, context, definitions, fixture, seed_meta
            )
        elif scenario_key == "weekly_no_novelty":
            proof = _weekly_proof(
                connection,
                context,
                definitions,
                fixture,
                seed_meta,
                database_path,
            )
        else:
            proof = _conversation_contract_proof(
                fixture,
                database_path.with_name(f"{database_path.stem}-panel.db"),
            )

        migration_rows = [
            dict(row)
            for row in connection.execute(
                """SELECT version,name,checksum_sha256,code_version
                     FROM schema_migrations ORDER BY version"""
            )
        ]
        status = proof["status"]
        data_quality = _data_quality_proof(
            connection,
            rows,
            proof,
            scenario_key=scenario_key,
        )
        ledger_boundary = _ledger_boundary_proof(connection)
        rollback_database = _rollback_database_proof(connection)
        exact_panel_payloads: dict[str, Any] = {}
        if scenario_key == "conversation_isolation":
            conversation_panel_response = {
                "api_exchanges": proof["api_exchanges"],
                "api_responses": proof["api_responses"],
                "message_histories": proof["message_histories"],
                "conversation_rows": proof["conversation_rows"],
                "message_rows": proof["message_rows"],
            }
            panel_api_replay = {
                "actual_flask_test_client": True,
                "api_exchanges": proof["api_exchanges"],
                "requests": [
                    item["request"] for item in proof["api_exchanges"]
                ],
                "bridge_calls": proof["bridge_calls"],
                "system_service_boundary": (
                    "deterministic_fake_exact_result/no_live_broker"
                ),
                "canonical_response": conversation_panel_response,
                "canonical_response_sha256": sha256_json(
                    conversation_panel_response
                ),
                "panel_database_rollback": proof[
                    "panel_database_rollback"
                ],
            }
        else:
            exact_panel_payloads = _exact_panel_subcommand_payloads(
                connection,
                context,
                definitions,
                fixture,
                proof,
            )
            panel_api_replay = _panel_api_contract_proof(
                fixture,
                proof,
                database_path.with_name(
                    f"{database_path.stem}-panel-proof.db"
                ),
                exact_panel_payloads,
            )
        actual_contract_calls = _actual_contract_calls(
            fixture,
            proof,
            migration_result=migration_result,
            registry_payload=serialized,
            exact_panel_payloads=exact_panel_payloads,
        )
        execution = _execution_record(
            fixture,
            actual_contract_calls=actual_contract_calls,
            panel_api_replay=panel_api_replay,
        )
        panel_result_scope = _scenario_panel_boundary(
            fixture,
            proof,
            panel_api_replay,
        )
        production_preparation = (
            proof["production_synthesis_preparation"]
            if scenario_key == "reversal"
            else proof["manual_baseline"][
                "production_synthesis_preparation"
            ]
            if scenario_key == "weekly_no_novelty"
            else None
        )
        production_model_input = (
            production_preparation["preparation"]
            if production_preparation is not None
            else None
        )
        if scenario_key == "weekly_no_novelty":
            no_model_reasons: Any = {
                "scheduled_first": proof["first_baseline"]["reason_code"],
                "scheduled_later": proof["later_unchanged"]["reason_code"],
                "manual_baseline": "fixture_boundary_no_live_model",
            }
        elif scenario_key == "reversal":
            no_model_reasons = {
                "manual_preparation": "fixture_boundary_no_live_model",
            }
        else:
            no_model_reasons = {
                "scenario": (
                    "no_eligible_persisted_synthesis_preparation_for_scenario"
                ),
            }
        trace = {
            "trace_contract": TRACE_CONTRACT,
            "status_vocabulary": [
                "supported",
                "exploratory",
                "insufficient",
                "suppressed",
                "no_novelty",
                "logic_not_implemented",
            ],
            "scenario_id": fixture["scenario_id"],
            "title": fixture["title"],
            "fixture": {
                "synthetic": True,
                "contains_owner_data": False,
                "contains_secrets_or_destination_ids": False,
                "input_manifest": {
                    **_rows_manifest(rows),
                    "rows": rows,
                },
                "seed_contract": fixture["seed"],
            },
            "execution": execution,
            "contracts": {
                "schema_version": migrations.AUTONOMOUS_SCHEMA_VERSION,
                "migration_004_checksum": MIGRATION_004_CHECKSUM,
                "migration_005_checksum": MIGRATION_005_CHECKSUM,
                "migration_rows_sha256": sha256_json(migration_rows),
                "registry_version": serialized["registry_version"],
                "registry_sha256": serialized["registry_sha256"],
                "frame_version": frame.FRAME_VERSION,
                "readiness_version": readiness.READINESS_VERSION,
                "analysis_contract_version": (
                    provenance.ANALYSIS_CONTRACT_VERSION
                ),
                "analysis_version": provenance.ANALYSIS_VERSION,
                "engine_sha256": provenance.engine_sha256(),
                "ledger_version": ledger.LEDGER_CONTRACT_VERSION,
                "analysis_range_plan_version": ledger.RANGE_PLAN_VERSION,
                "synthesis_version": synthesis.SYNTHESIS_CONTRACT_VERSION,
                "synthesis_context_version": (
                    synthesis.SYNTHESIS_CONTEXT_VERSION
                ),
                "orchestration_version": orchestrator.ORCHESTRATOR_CONTRACT_VERSION,
                "cadence_range_plan_version": orchestrator.RANGE_PLAN_VERSION,
                "notification_version": orchestrator.NOTIFICATION_CONTRACT_VERSION,
                "orchestration_context_version": (
                    orchestrator.CONTEXT_VERSION
                ),
                "panel_conversation_context_version": 1,
            },
            "result": proof,
            "source_provenance_completeness_freshness": data_quality,
            "ledger_boundary": ledger_boundary,
            "model_boundary": {
                "invoked": False,
                "eligible_preparation_available": (
                    production_model_input is not None
                ),
                "input": production_model_input,
                "input_sha256": (
                    sha256_json(production_model_input)
                    if production_model_input is not None
                    else None
                ),
                "preparation_provenance": production_preparation,
                "not_invoked_reasons": no_model_reasons,
                "fixture_narrative": {
                    "origin": "fixture_generated/not_live_model",
                    "persisted": scenario_key == "weekly_no_novelty",
                    "numeric_or_status_authority": False,
                },
                "recorded_live_invariant": {
                    "model_id": synthesis.LEGACY_MODEL_ID,
                    "provider": synthesis.LEGACY_PROVIDER,
                    "queried_live": False,
                },
            },
            "narrative": _narrative(fixture, status),
            "notification_boundary": {
                "sender_invoked": False,
                "destination_configured": False,
                "decision": fixture["notification_decision"],
                "contract_version": orchestrator.NOTIFICATION_CONTRACT_VERSION,
                "durable_outbox_proof": proof.get(
                    "notification_outbox_proof",
                    {
                        "actual_query": (
                            "SELECT notification_id,state,dedupe_key,payload_sha256 "
                            "FROM insight_notification_outbox"
                        ),
                        "rows": proof.get("outbox_rows", []),
                    },
                ),
            },
            "panel_proof": {
                "method": (
                    "actual Flask JSON API pass-through with a deterministic "
                    "broker fake"
                ),
                "fixture_declared_method": fixture["panel_proof"],
                "server_rendered_proof": False,
                "actual_screenshot_review": False,
                "screenshot_review": False,
                "visual_owner_signoff": "unresolved",
                "static_structure_is_visual_review": False,
                "result_scope": panel_result_scope,
                "canonical_api_replay": panel_api_replay,
            },
            "safety": {
                "association_not_causation": True,
                "medical_evidence_only": fixture["medical_boundary"],
                "fixture_result_is_not_owner_finding": True,
            },
            "rollback_state": {
                "database": rollback_database,
                "panel_database": panel_api_replay[
                    "panel_database_rollback"
                ],
                "temporary_database_count": 2,
                "external_mutations": [],
                "live_migration": False,
                "schedule_or_delivery_activation": False,
                "recovery": "discard both temporary databases",
            },
        }
        if trace["contracts"]["engine_sha256"] != EXPECTED_ENGINE_SHA256:
            raise TraceReplayError("Phase 4 engine hash changed")
        # Normalize tuples and any other JSON-compatible container variants
        # before handing the trace to callers.  The frozen artifact is
        # canonical JSON, so replay must expose that exact machine-readable
        # value rather than a Python-only value that merely serializes to the
        # same bytes.
        return json.loads(canonical_json(trace))
    finally:
        connection.close()


def assert_no_sensitive_fixture_text(value: Any) -> None:
    """Reject likely production secrets, destinations, and owner-only text."""

    text = canonical_json(value).lower()
    forbidden = (
        "telegram_chat_id",
        "bot_token",
        "api_key",
        "authorization:",
        "private_home_path_marker",
        "/var/backups",
        "private_owner_marker",
    )
    found = [token for token in forbidden if token in text]
    if found:
        raise TraceReplayError(f"fixture contains forbidden sensitive token(s): {found}")
