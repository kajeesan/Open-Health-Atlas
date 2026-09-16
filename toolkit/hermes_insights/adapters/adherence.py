"""Whole-day, commitment, habit, and effective-plan timing adherence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import (
    DefinitionIndex, REGISTRY_VERSION, complete_dates, context_value, date_where,
    definition_identity_key, effective_completeness, finite, has_columns,
    identity_parts, in_range, make_observation, natural_key,
)
from ..normalize import normalized_label


ADAPTER_ID = "adherence"
STATUS_VALUE = {"kept": 1.0, "partly": 0.5, "broke": 0.0}
TIMING_METRICS = ("wake", "bed", "workout", "dose")


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, day, value, *, source=None, provenance=None,
    include_provenance=False, state="observed",
):
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, day, value, state=state, source=source, provenance=provenance,
        include_provenance=include_provenance,
    ))


def _load_commitments(conn, definitions, date_range, out, include_provenance):
    if not has_columns(
        conn, "commitments_log", "id", "date", "commitment_id", "status",
    ):
        return
    names = {}
    if has_columns(conn, "commitments", "id", "name"):
        names = {row["id"]: row["name"] for row in _rows(
            conn, "SELECT id,name FROM commitments ORDER BY id")
                 if isinstance(row.get("name"), str) and row["name"].strip()}
    where, params = date_where("date", date_range)
    rows = _rows(conn, "SELECT * FROM commitments_log WHERE " + where + " ORDER BY date,id", params)
    # UNIQUE(date,commitment_id) exists in current schemas, but newest-id wins
    # defensively for transitional fixtures that predate it.
    latest = {(row["date"], row["commitment_id"]): row for row in rows}
    for (_day, commitment_id), row in sorted(latest.items()):
        if row.get("status") not in STATUS_VALUE:
            continue
        if commitment_id == 0:
            key, identity_key, original = "adherence.word_kept", None, "whole-day"
        else:
            original = names.get(commitment_id)
            if not original:
                continue
            identity_key, token, _ = identity_parts("commitment", original)
            key = definitions.identity_key(
                f"adherence.commitment.{token}.kept",
                prefix="adherence.commitment.", suffix=".kept",
                identity_key=identity_key,
            )
        _emit(out, definitions, key, row["date"], STATUS_VALUE[row["status"]],
              source=row.get("source") or "manual", provenance={
                  "adapter": ADAPTER_ID, "table": "commitments_log",
                  "natural_key": natural_key("commitments_log", row),
                  "identity_key": identity_key, "original_label": original,
                  "stored_status": row["status"],
                  "why_display_only": bool(row.get("why")),
              }, include_provenance=include_provenance)


def _load_habits(
    conn, definitions, date_range, effective, out, include_provenance,
):
    if not has_columns(conn, "habits_log", "id", "date", "habit", "done"):
        return
    where, params = date_where("date", date_range)
    grouped = defaultdict(list)
    for row in _rows(conn, "SELECT * FROM habits_log WHERE " + where + " ORDER BY date,id", params):
        if isinstance(row.get("habit"), str) and row["habit"].strip() and row.get("done") in {0, 1}:
            grouped[(row["date"], normalized_label(row["habit"]))].append(row)
    observed = set()
    for (day, label), rows in sorted(grouped.items()):
        value = max(row["done"] for row in rows)
        identity_key, token, _ = identity_parts("habit", label)
        key = definitions.identity_key(
            f"adherence.habit.{token}.done", prefix="adherence.habit.",
            suffix=".done", identity_key=identity_key,
        )
        _emit(out, definitions, key, day, value, source=(
            rows[0].get("source") or "manual" if len({r.get("source") for r in rows}) == 1
            else "mixed"
        ), provenance={
            "adapter": ADAPTER_ID, "table": "habits_log",
            "natural_keys": [natural_key("habits_log", row) for row in rows],
            "identity_key": identity_key,
            "original_labels": sorted({row["habit"] for row in rows}),
            "duplicate_rule": "max_explicit_0_or_1",
            "streak_and_xp_display_only": True,
            "missing_is_not_miss": True,
        }, include_provenance=include_provenance)
        if key is not None:
            observed.add((day, key))
    for key, definition in definitions.by_key.items():
        if not key.startswith("adherence.habit.") or not key.endswith(".done"):
            continue
        identity_key = definition_identity_key(definition)
        if identity_key is None:
            continue
        for day, comp in complete_dates(effective, "other_event", identity_key).items():
            if (day, key) in observed:
                continue
            _emit(out, definitions, key, day, 0, source=comp["source"],
                  state="structural_zero", provenance={
                      "adapter": ADAPTER_ID,
                      "table": "capture_completeness_revisions",
                      "natural_key": f"capture_completeness_revisions:{comp['id']}",
                      "scope": "other_event", "identity_key": identity_key,
                  }, include_provenance=include_provenance)


def _actuals(conn, metric, date_range, context):
    hhmm = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    start_hhmm = context_value(context, "start_hhmm") or context_value(context, "_start_hhmm")
    if hhmm is None:
        return {}
    result = defaultdict(list)
    if metric in {"wake", "bed"} and has_columns(
        conn, "sleep_log", "date", "wake_time", "bedtime",
    ):
        column = "wake_time" if metric == "wake" else "bedtime"
        where, params = date_where("date", date_range)
        for row in _rows(conn, f"SELECT date,{column} actual,source FROM sleep_log WHERE {where}", params):
            value = hhmm(row.get("actual"))
            if value is not None:
                result[row["date"]].append((value, f"sleep_log:{row['date']}", row.get("source")))
    elif metric == "dose" and has_columns(
        conn, "meds_log", "id", "date", "drug", "time_taken",
    ):
        aliases = {
            normalized_label(str(value)) for value in
            (context_value(context, "MEDICATION_ALIASES", ()) or ())
            if str(value).strip()
        }
        where, params = date_where("date", date_range)
        for row in _rows(conn, "SELECT * FROM meds_log WHERE " + where + " ORDER BY date,id", params):
            if normalized_label(str(row.get("drug") or "")) not in aliases:
                continue
            value = hhmm(row.get("time_taken"))
            if value is not None:
                result[row["date"]].append((value, natural_key("meds_log", row), row.get("source")))
    elif metric == "workout" and start_hhmm is not None and has_columns(
        conn, "hevy_sets", "id", "date", "start_time", "set_type",
    ):
        where, params = date_where("date", date_range)
        for row in _rows(conn, "SELECT * FROM hevy_sets WHERE " + where + " ORDER BY date,id", params):
            if (row.get("set_type") or "normal") == "warmup":
                continue
            parsed = start_hhmm(row.get("start_time"))
            value = hhmm(parsed) if parsed is not None else None
            if value is not None:
                result[row["date"]].append((value, natural_key("hevy_sets", row), row.get("source")))
    return {day: min(values, key=lambda item: item[0]) for day, values in result.items()}


def _load_timing(conn, definitions, date_range, context, out, include_provenance):
    if not has_columns(
        conn, "planned_times", "metric", "planned", "tolerance_min", "updated",
    ):
        return
    hhmm = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    circ_diff = context_value(context, "circ_diff_min") or context_value(
        context, "_circ_diff_min")
    if hhmm is None or circ_diff is None:
        return
    plans = {row["metric"]: row for row in _rows(
        conn, "SELECT * FROM planned_times ORDER BY metric")}
    for metric in TIMING_METRICS:
        plan = plans.get(metric)
        if not plan:
            continue
        planned = hhmm(plan.get("planned"))
        tolerance = finite(plan.get("tolerance_min"))
        if planned is None or tolerance is None or tolerance < 0:
            continue
        effective_from = str(plan.get("updated") or "")[:10]
        actuals = _actuals(conn, metric, date_range, context)
        for day, (actual, natural, source) in sorted(actuals.items()):
            # The current table is a mutable snapshot, not revision history.
            # Applying it before its recorded update would rewrite the past.
            if effective_from and day < effective_from:
                continue
            delta = circ_diff(actual, planned)
            provenance = {
                "adapter": ADAPTER_ID, "table": "planned_times",
                "natural_key": f"planned_times:{metric}",
                "actual_natural_key": natural, "effective_from": effective_from or None,
                "planned_min": planned, "actual_min": actual,
                "tolerance_min": tolerance, "circular_difference": True,
                "missing_actual_is_not_miss": True,
            }
            _emit(out, definitions, f"adherence.timing.{metric}.abs_delta_min",
                  day, delta, source=source or metric, provenance=provenance,
                  include_provenance=include_provenance)
            _emit(out, definitions, f"adherence.timing.{metric}.on_time",
                  day, int(delta <= tolerance), source=source or metric,
                  provenance=provenance, include_provenance=include_provenance)


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    effective = effective_completeness(conn, date_range)
    _load_commitments(conn, definitions, date_range, out, include_provenance)
    _load_habits(conn, definitions, date_range, effective, out, include_provenance)
    _load_timing(conn, definitions, date_range, context, out, include_provenance)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "REGISTRY_VERSION", "STATUS_VALUE", "load"]
