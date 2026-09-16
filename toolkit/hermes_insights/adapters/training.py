"""Hevy training, exact exercise identities, muscle exposure and forward plans."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json
import statistics
from typing import Any, Mapping

from . import (
    DefinitionIndex, REGISTRY_VERSION, context_value, date_where, finite,
    has_columns, identity_parts, interval_dates, make_observation, natural_key,
    source_sync_intervals, table_exists,
)
from ..normalize import normalized_label


ADAPTER_ID = "training"
SUMMARY_WINDOWS = (7, 28, 90, 365)
ZEROABLE_SUFFIXES = (
    ".session", ".working_sets", ".effective_sets",
)


def window_summary(
    values: Mapping[str, float], anchor: date, window: int,
    *, positive_dates: set[str] | None = None,
) -> dict[str, Any]:
    """Exact current/preceding non-overlapping load-window summary.

    ``values`` must contain structural zero entries for known zero dates.  A
    missing date makes that whole window unknown instead of silently zero.
    """
    if window not in SUMMARY_WINDOWS:
        raise ValueError("window must be one of 7, 28, 90, 365")
    positive_dates = positive_dates or {day for day, value in values.items() if value > 0}

    def days(end: date) -> list[str]:
        start = end - timedelta(days=window - 1)
        return [(start + timedelta(days=offset)).isoformat() for offset in range(window)]

    current_days = days(anchor)
    previous_days = days(anchor - timedelta(days=window))
    current = sum(values[day] for day in current_days) if all(day in values for day in current_days) else None
    previous = sum(values[day] for day in previous_days) if all(day in values for day in previous_days) else None
    delta = current - previous if current is not None and previous is not None else None
    latest = max((date.fromisoformat(day) for day in positive_dates
                  if date.fromisoformat(day) <= anchor), default=None)
    distinct = sum(day in positive_dates for day in current_days)
    # Appendix B defines this from the latest observed positive date. Missing
    # intervening dates do not erase that observation; callers must not present
    # it as proof that no later unobserved event occurred.
    days_since = (anchor - latest).days if latest is not None else None
    return {
        "window_days": window,
        "current": current,
        "previous": previous,
        "delta": delta,
        "delta_per_day": delta / window if delta is not None else None,
        "percent": 100 * delta / previous if delta is not None and previous > 0 else None,
        "new_exposure": bool(current is not None and current > 0 and previous == 0),
        "frequency_per_week": 7 * distinct / window if current is not None else None,
        "days_since": days_since,
    }


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, day, value, *, unit=None, source=None,
    provenance=None, include_provenance=False, state="observed",
):
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, day, value, unit=unit, source=source, state=state,
        provenance=provenance, include_provenance=include_provenance,
    ))


def _identity_key(definitions, namespace, label, prefix, suffix):
    identity_key, token, _ = identity_parts(namespace, label)
    expected = f"{prefix}{token}{suffix}"
    return definitions.identity_key(
        expected, prefix=prefix, suffix=suffix, identity_key=identity_key,
    ), identity_key


def _source(rows):
    values = sorted({(row.get("source") or "hevy") for row in rows})
    return values[0] if len(values) == 1 else "mixed"


def _aggregate_rows(rows, e1rm_fn):
    working = [row for row in rows
               if (row.get("set_type") or "normal") != "warmup"]
    loaded = [row for row in working
              if finite(row.get("weight_kg")) is not None
              and finite(row.get("reps")) is not None]
    rpes = [row["rpe"] for row in working if finite(row.get("rpe")) is not None]
    durations = [row["duration_seconds"] for row in working
                 if finite(row.get("duration_seconds")) is not None]
    distances = [row["distance_km"] for row in working
                 if finite(row.get("distance_km")) is not None]
    e1rms = [e1rm_fn(row["weight_kg"], row["reps"]) for row in loaded] if e1rm_fn else []
    e1rms = [value for value in e1rms if value is not None]
    volume = (sum(row["weight_kg"] * row["reps"] for row in loaded)
              if loaded else 0 if not working else None)
    return {
        # Appendix B training features exclude warmups.  A warmup-only day is
        # an observed zero working session, not a positive training exposure.
        "session": 1 if working else 0 if rows else None,
        "working_sets": len(working),
        "loaded_volume_kg": volume,
        "mean_rpe": statistics.mean(rpes) if rpes else None,
        "best_e1rm_kg": max(e1rms) if e1rms else None,
        "duration_sec": sum(durations) if durations else None,
        "distance_km": sum(distances) if distances else None,
        "working": working,
        "eligible_loaded_sets": len(loaded),
        "total_working_sets": len(working),
    }


def _plan_revisions(conn):
    if not has_columns(
        conn, "training_plan_revisions", "id", "effective_from", "schedule_json",
        "routines_json",
    ):
        return []
    result = []
    for row in _rows(conn, "SELECT * FROM training_plan_revisions ORDER BY effective_from,id"):
        try:
            row["schedule"] = json.loads(row["schedule_json"])
            row["routines"] = json.loads(row["routines_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        result.append(row)
    return result


def _revision_on(revisions, day):
    eligible = [row for row in revisions if row["effective_from"] <= day]
    return eligible[-1] if eligible else None


def _routine_names(revision):
    routines = revision.get("routines") or {}
    if isinstance(routines, dict):
        return set(routines)
    if isinstance(routines, list):
        return {str(row.get("routine_name") or row.get("name")) for row in routines
                if isinstance(row, dict) and (row.get("routine_name") or row.get("name"))}
    return set()


def _scheduled_routine(revision, day):
    schedule = revision.get("schedule") or {}
    weekday = date.fromisoformat(day).strftime("%a")
    if isinstance(schedule, dict):
        return schedule.get(weekday) or schedule.get(weekday.casefold())
    if isinstance(schedule, list):
        for row in schedule:
            if not isinstance(row, dict):
                continue
            if str(row.get("weekday", "")).casefold() == weekday.casefold():
                return row.get("routine_name")
    return None


def _routine_exercises(revision, routine):
    routines = revision.get("routines") or {}
    if isinstance(routines, dict):
        value = routines.get(routine, [])
        if isinstance(value, dict):
            value = value.get("exercises", [])
        return value if isinstance(value, list) else []
    if isinstance(routines, list):
        return [row for row in routines if isinstance(row, dict)
                and row.get("routine_name") == routine]
    return []


def _exercise_name(row):
    return row.get("exercise_title") or row.get("exercise") or row.get("title")


def _target_sets(row):
    value = row.get("target_sets") if isinstance(row, dict) else None
    if value is None and isinstance(row, dict):
        value = row.get("sets")
    return finite(value)


def _muscle_maps(conn, context):
    group_map_fn = context_value(context, "group_weight_maps") or context_value(
        context, "_group_weight_maps")
    basis_fn = context_value(context, "basis_weights") or context_value(
        context, "_basis_weights")
    if group_map_fn is None or basis_fn is None:
        return None, None, None
    try:
        authored, coarse = group_map_fn(conn)
    except Exception:  # schema absence is an availability result, not read repair
        return None, None, None
    return authored, coarse, basis_fn


def _subregion_maps(conn):
    if not has_columns(
        conn, "exercise_submuscles", "exercise_title", "sub_region", "weight",
        "laterality",
    ):
        return {}
    # Left/right authored rows for one subregion count once: maximum side weight.
    rows = _rows(conn, """SELECT exercise_title,sub_region,MAX(COALESCE(weight,0)) weight
        FROM exercise_submuscles GROUP BY exercise_title,sub_region""")
    result = defaultdict(dict)
    for row in rows:
        result[row["exercise_title"]][row["sub_region"]] = row["weight"]
    return result


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    if not has_columns(conn, "hevy_sets", "id", "date", "exercise_title"):
        return out
    where, params = date_where("date", date_range)
    rows = _rows(conn, "SELECT * FROM hevy_sets WHERE date IS NOT NULL AND " + where
                 + " ORDER BY date,id", params)
    by_day = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)

    e1rm_fn = context_value(context, "e1rm")
    authored, coarse, basis_fn = _muscle_maps(conn, context)
    subregions = _subregion_maps(conn)
    revisions = _plan_revisions(conn)
    observed: set[tuple[str, str]] = set()
    group_logged: dict[tuple[str, str], float] = {}
    group_mapping_unknown: set[str] = set()
    subregion_mapping_unknown: set[str] = set()

    for day, day_rows in sorted(by_day.items()):
        summary = _aggregate_rows(day_rows, e1rm_fn)
        source = _source(day_rows)
        prov = {
            "adapter": ADAPTER_ID, "table": "hevy_sets",
            "natural_keys": [natural_key("hevy_sets", row) for row in day_rows],
            "eligible_loaded_sets": summary["eligible_loaded_sets"],
            "total_working_sets": summary["total_working_sets"],
        }
        for suffix, unit in (
            ("session", "binary"), ("working_sets", "count"),
            ("loaded_volume_kg", "kg_reps"), ("mean_rpe", "rpe_0_10"),
        ):
            key = f"training.{suffix}"
            _emit(out, definitions, key, day, summary[suffix], unit=unit, source=source,
                  provenance=prov, include_provenance=include_provenance)
            if summary[suffix] is not None:
                observed.add((day, key))

        revision = _revision_on(revisions, day)
        routine_names = _routine_names(revision) if revision else set()
        by_workout, by_exercise = defaultdict(list), defaultdict(list)
        for row in day_rows:
            workout_title = row.get("workout_title") or ""
            exercise_title = row.get("exercise_title") or ""
            if normalized_label(workout_title):
                by_workout[normalized_label(workout_title)].append(row)
            if normalized_label(exercise_title):
                by_exercise[normalized_label(exercise_title)].append(row)

        normalized_routines = {
            normalized_label(name): name for name in routine_names
            if normalized_label(name)
        }

        for title, title_rows in sorted(by_workout.items()):
            original_labels = sorted({row["workout_title"] for row in title_rows})
            title_session = int(any(
                (row.get("set_type") or "normal") != "warmup"
                for row in title_rows
            ))
            key, identity_key = _identity_key(
                definitions, "workout", title, "training.workout.", ".session")
            tprov = {**prov, "identity_key": identity_key,
                     "original_labels": original_labels,
                     "natural_keys": [natural_key("hevy_sets", row) for row in title_rows]}
            _emit(out, definitions, key, day, title_session, unit="binary",
                  source=_source(title_rows),
                  provenance=tprov, include_provenance=include_provenance)
            if key:
                observed.add((day, key))
            if revision and title in normalized_routines:
                rkey, rid = _identity_key(
                    definitions, "routine", title, "training.routine.", ".session")
                _emit(out, definitions, rkey, day, title_session, unit="binary",
                      source=_source(title_rows), provenance={
                          **tprov, "identity_key": rid,
                          "plan_revision_id": revision["id"], "exact_name_match": True,
                      }, include_provenance=include_provenance)
                if rkey:
                    observed.add((day, rkey))

        for title, title_rows in sorted(by_exercise.items()):
            es = _aggregate_rows(title_rows, e1rm_fn)
            # Exercise identities are non-warmup exposures.  A title appearing
            # only in warmup rows is an explicit zero working-session, not a
            # positive exercise session.
            es["session"] = 1 if es["working"] else 0
            identity_key, token, _ = identity_parts("exercise", title)
            eprov = {
                **prov, "identity_key": identity_key,
                "original_labels": sorted({
                    row["exercise_title"] for row in title_rows
                }),
                "natural_keys": [natural_key("hevy_sets", row) for row in title_rows],
                "eligible_loaded_sets": es["eligible_loaded_sets"],
                "total_working_sets": es["total_working_sets"],
            }
            for suffix, unit in (
                ("session", "binary"), ("working_sets", "count"),
                ("loaded_volume_kg", "kg_reps"), ("best_e1rm_kg", "kg"),
                ("mean_rpe", "rpe_0_10"), ("duration_sec", "seconds"),
                ("distance_km", "km"),
            ):
                key = definitions.identity_key(
                    f"training.exercise.{token}.{suffix}",
                    prefix="training.exercise.", suffix=f".{suffix}",
                    identity_key=identity_key,
                )
                _emit(out, definitions, key, day, es[suffix], unit=unit,
                      source=_source(title_rows), provenance=eprov,
                      include_provenance=include_provenance)
                if key and es[suffix] is not None:
                    observed.add((day, key))

        # One contribution per non-warmup set, using the existing authored-first
        # basis function. Unknown mappings remain absent and are surfaced in
        # provenance rather than guessed into a group.
        group_values = defaultdict(float)
        group_basis = defaultdict(set)
        sub_values = defaultdict(float)
        if authored is not None and basis_fn is not None:
            for row in summary["working"]:
                title = row.get("exercise_title")
                if not title:
                    group_mapping_unknown.add(day)
                    subregion_mapping_unknown.add(day)
                    continue
                weights, basis = basis_fn(title, authored, coarse)
                if weights is None:
                    group_mapping_unknown.add(day)
                    subregion_mapping_unknown.add(day)
                    continue
                for group, weight in weights.items():
                    if finite(weight) is not None:
                        group_values[group] += weight
                        group_basis[group].add(basis)
                if basis == "authored":
                    for subregion, weight in subregions.get(title, {}).items():
                        if finite(weight) is not None:
                            sub_values[subregion] += weight
                else:
                    # Coarse mappings prove group exposure only.  They do not
                    # prove that every absent authored subregion was zero.
                    subregion_mapping_unknown.add(day)
        elif summary["working"]:
            group_mapping_unknown.add(day)
            subregion_mapping_unknown.add(day)
        for group, value in sorted(group_values.items()):
            slug = str(group).casefold()
            key = f"training.group.{slug}.effective_sets"
            _emit(out, definitions, key, day, value, unit="effective_sets",
                  source=source, provenance={
                      **prov, "formula": "basis_weights_once_per_working_set",
                      "basis": sorted(group_basis[group]),
                  }, include_provenance=include_provenance)
            if key in definitions:
                observed.add((day, key)); group_logged[(day, slug)] = value
        for label, value in sorted(sub_values.items()):
            identity_key, token, _ = identity_parts("subregion", label)
            key = definitions.identity_key(
                f"training.subregion.{token}.effective_sets",
                prefix="training.subregion.", suffix=".effective_sets",
                identity_key=identity_key,
            )
            _emit(out, definitions, key, day, value, unit="effective_sets",
                  source=source, provenance={
                      **prov, "identity_key": identity_key, "original_label": label,
                      "formula": "max_laterality_weight_once_per_working_set",
                  }, include_provenance=include_provenance)
            if key:
                observed.add((day, key))

    # Successful Hevy intervals prove rowless dates are structural zeros. They
    # do not turn unavailable RPE/e1RM values into zero.
    covered = interval_dates(source_sync_intervals(conn, {"hevy"}), date_range)
    for day, run_ids in sorted(covered.items()):
        effective_revision = _revision_on(revisions, day)
        effective_routine_keys = set()
        if effective_revision is not None:
            for routine_name in _routine_names(effective_revision):
                if not normalized_label(routine_name):
                    continue
                routine_identity, routine_token, _ = identity_parts(
                    "routine", routine_name,
                )
                routine_key = definitions.identity_key(
                    f"training.routine.{routine_token}.session",
                    prefix="training.routine.", suffix=".session",
                    identity_key=routine_identity,
                )
                if routine_key:
                    effective_routine_keys.add(routine_key)
        for key in definitions.by_key:
            if not key.startswith("training.") or key.startswith("training.plan."):
                continue
            if not key.endswith(ZEROABLE_SUFFIXES) or (day, key) in observed:
                continue
            # A routine identity is meaningful only after the plan revision
            # that owns it becomes effective.  Complete Hevy coverage before
            # that date cannot prove an absence for a future routine.
            if (key.startswith("training.routine.")
                    and key not in effective_routine_keys):
                continue
            if key.startswith("training.group.") and day in group_mapping_unknown:
                continue
            if key.startswith("training.subregion.") and day in subregion_mapping_unknown:
                continue
            zero_provenance = {
                "adapter": ADAPTER_ID, "table": "source_sync_runs",
                "source_sync_run_ids": run_ids,
            }
            if key.startswith("training.routine."):
                # A routine absence is meaningful only under the effective
                # plan revision that declared that routine.  Retain the exact
                # revision so superseding plans change the analysis
                # fingerprint even when the structural-zero value stays 0.
                zero_provenance["training_plan_revision_id"] = (
                    effective_revision["id"]
                )
            _emit(out, definitions, key, day, 0, state="structural_zero",
                  source="hevy", provenance=zero_provenance,
                  include_provenance=include_provenance)

    # Forward-only plan features. No plan revision means no retrospective plan.
    for day in sorted(set(by_day) | set(covered)):
        revision = _revision_on(revisions, day)
        if revision is None:
            continue
        routine = _scheduled_routine(revision, day)
        if not routine or routine == "Rest":
            continue
        exercises = _routine_exercises(revision, routine)
        planned_groups = defaultdict(float)
        for item in exercises:
            if not isinstance(item, dict):
                continue
            title, sets = _exercise_name(item), _target_sets(item)
            if not title or sets is None:
                continue
            identity_key, token, _ = identity_parts("exercise", title)
            pprov = {
                "adapter": ADAPTER_ID, "table": "training_plan_revisions",
                "natural_key": f"training_plan_revisions:{revision['id']}",
                "identity_key": identity_key, "routine": routine,
            }
            if authored is not None and basis_fn is not None:
                weights, _basis = basis_fn(title, authored, coarse)
                if weights:
                    for group, weight in weights.items():
                        if finite(weight) is not None:
                            planned_groups[str(group).casefold()] += sets * weight
        for group, planned in sorted(planned_groups.items()):
            pprov = {
                "adapter": ADAPTER_ID, "table": "training_plan_revisions",
                "natural_key": f"training_plan_revisions:{revision['id']}",
                "routine": routine,
            }
            if planned > 0 and day in covered and day not in group_mapping_unknown:
                ratio = group_logged.get((day, group), 0.0) / planned
                _emit(out, definitions, f"training.logged_vs_planned.{group}.ratio",
                      day, ratio, unit="ratio", source="hevy+plan", provenance={
                          **pprov, "source_sync_run_ids": covered[day],
                          "formula": "logged_effective_sets/planned_effective_sets",
                      }, include_provenance=include_provenance)

    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "REGISTRY_VERSION", "SUMMARY_WINDOWS", "load",
           "window_summary"]
