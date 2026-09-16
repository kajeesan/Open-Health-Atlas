"""Daily wearable, sleep, subjective, check-in, medication and recovery data."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import statistics
from typing import Any

from . import (
    DefinitionIndex, REGISTRY_VERSION, canonical_json, complete_dates,
    context_value, date_where, definition_identity_key, definition_value,
    effective_completeness, finite,
    has_columns, identity_parts, in_range, make_observation, natural_key,
    table_columns, table_exists, token_from_identity_key,
)
from .. import events as phase2_events
from ..normalize import normalized_label


ADAPTER_ID = "daily"

WEARABLE_FIELDS = {
    "resting_hr": ("wearable.resting_hr_bpm", "bpm"),
    "hr_min": ("wearable.hr_min_bpm", "bpm"),
    "hr_avg": ("wearable.hr_avg_bpm", "bpm"),
    "hr_max": ("wearable.hr_max_bpm", "bpm"),
    "steps": ("wearable.steps", "count"),
    "active_energy_kcal": ("wearable.active_energy_kcal", "kcal"),
    "basal_energy_kcal": ("wearable.basal_energy_kcal", "kcal"),
    "exercise_min": ("wearable.exercise_min", "min"),
    "distance_km": ("wearable.distance_km", "km"),
    "flights": ("wearable.flights", "count"),
    "respiratory_rate": ("wearable.respiratory_rate_brpm", "breaths_per_min"),
    "spo2_pct": ("wearable.spo2_pct", "percent"),
    "walking_hr_avg": ("wearable.walking_hr_avg_bpm", "bpm"),
}
SUBJECTIVE_FIELDS = {
    "day_rating": "subjective.day_rating",
    "focus": "subjective.focus",
    "energy": "subjective.energy",
    "mood": "subjective.mood",
    "emotional_regulation": "subjective.emotional_regulation",
    "anxiety": "subjective.anxiety",
    "motivation": "subjective.motivation",
    "stress": "subjective.stress",
    "caffeine_mg": "substance.caffeine_mg",
    "alcohol_units": "substance.alcohol_units",
}
SLEEP_FIELDS = {
    "time_in_bed_hours": ("sleep.time_in_bed_hours", "hours"),
    "deep_min": ("sleep.deep_min", "min"),
    "rem_min": ("sleep.rem_min", "min"),
    "light_min": ("sleep.light_min", "min"),
    "awake_min": ("sleep.awake_min", "min"),
    "quality": ("sleep.quality", None),
    "awakenings": ("sleep.awakenings", "count"),
}


def _rows(conn, sql: str, params=()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _provenance(table: str, row: dict[str, Any], *, details=None) -> dict[str, Any]:
    key_columns = ("date", "source") if table == "daily_metrics" else ("date",)
    value = {"adapter": ADAPTER_ID, "table": table,
             "natural_key": natural_key(table, row, *key_columns)}
    if details:
        value.update(details)
    return value


def _emit(
    out: list[Any], definitions: DefinitionIndex, key: str | None,
    observed_at: str, value: Any, *, unit: str | None = None,
    source: str | None = None, provenance=None, include_provenance=False,
    state="observed",
) -> None:
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, observed_at, value, state=state, unit=unit,
        source=source, provenance=provenance,
        include_provenance=include_provenance,
    ))


def _strict_hhmm(value: str | None, parser) -> int | None:
    if parser is None:
        return None
    try:
        parsed = parser(value)
    except (TypeError, ValueError):
        return None
    return int(parsed) if parsed is not None else None


def _load_wearables(conn, definitions, date_range, context, include_provenance, out):
    columns = table_columns(conn, "daily_metrics")
    if not {"date", "source"} <= columns:
        return
    hrv_column = "hrv_ms" if "hrv_ms" in columns else (
        "hrv_sdnn" if "hrv_sdnn" in columns else None)
    selected_columns = [name for name in WEARABLE_FIELDS if name in columns]
    if "sleep_hours" in columns:
        selected_columns.append("sleep_hours")
    if hrv_column:
        selected_columns.append(hrv_column)
    where, params = date_where("date", date_range)
    sql = "SELECT date,source," + ",".join(selected_columns) + \
          " FROM daily_metrics WHERE " + where
    by_date: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in _rows(conn, sql, params):
        by_date[row["date"]][(row.get("source") or "").casefold()] = row

    for day, sources in sorted(by_date.items()):
        for column, (key, unit) in WEARABLE_FIELDS.items():
            if column not in columns:
                continue
            primary = "apple" if column == "distance_km" else "fitbit"
            order = [primary] + sorted(name for name in sources if name != primary)
            chosen = next((sources[name] for name in order
                           if sources.get(name, {}).get(column) is not None), None)
            if chosen is None:
                continue
            provider = (chosen.get("source") or "").casefold()
            prov = _provenance("daily_metrics", chosen, details={
                "column": column, "selected_provider": provider,
            })
            _emit(out, definitions, key, day, chosen[column], unit=unit,
                  source=provider, provenance=prov,
                  include_provenance=include_provenance)

        if hrv_column:
            for provider, row in sorted(sources.items()):
                value = row.get(hrv_column)
                if value is None:
                    continue
                identity_key = None
                if provider == "fitbit":
                    key = "wearable.hrv.fitbit_rmssd_ms"
                elif provider == "apple":
                    key = "wearable.hrv.apple_sdnn_ms"
                else:
                    identity_key, token, _ = identity_parts(
                        "source", provider or "unclassified")
                    expected = f"wearable.hrv.unclassified.{token}"
                    key = definitions.identity_key(
                        expected, prefix="wearable.hrv.unclassified.", suffix="",
                        identity_key=identity_key,
                    )
                prov = _provenance("daily_metrics", row, details={
                    "column": hrv_column, "algorithm": (
                        "rmssd" if provider == "fitbit" else
                        "sdnn" if provider == "apple" else "unclassified"
                    ),
                    "identity_key": identity_key
                        if provider not in {"apple", "fitbit"} else None,
                    "original_provider": row.get("source"),
                })
                _emit(out, definitions, key, day, value, unit="ms", source=provider,
                      provenance=prov, include_provenance=include_provenance)

    _load_recovery(conn, definitions, date_range, context, include_provenance,
                   hrv_column, out)
    return by_date


def _load_recovery(
    conn, definitions, date_range, context, include_provenance, hrv_column, out,
):
    if hrv_column is None or not has_columns(
        conn, "daily_metrics", "date", "source", "resting_hr", hrv_column,
    ):
        return
    score_fn = context_value(context, "dev_score") or context_value(context, "_dev_score")
    if score_fn is None:
        return
    rows = _rows(conn, f"SELECT date,source,resting_hr,{hrv_column} hrv "
                       "FROM daily_metrics ORDER BY source,date")
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source = (row.get("source") or "").casefold()
        if source in {"apple", "fitbit"}:
            by_source[source].append(row)
    for source, series in by_source.items():
        prior_rhr: list[float] = []
        prior_hrv: list[float] = []
        for row in series:
            rhr_result = (score_fn(row["resting_hr"], prior_rhr[-14:], 500, True)
                          if row["resting_hr"] is not None else None)
            hrv_result = (score_fn(row["hrv"], prior_hrv[-14:], 250, False)
                          if row["hrv"] is not None else None)
            if in_range(row["date"], date_range):
                roots, component_values = [], []
                if rhr_result is not None:
                    value = rhr_result[0]
                    key = f"recovery.rhr_score.{source}"
                    prov = _provenance("daily_metrics", row, details={
                        "formula": "_dev_score", "baseline_n": min(len(prior_rhr), 14),
                        "baseline_median": rhr_result[1], "source_specific": True,
                    })
                    _emit(out, definitions, key, row["date"], value, unit="score_0_100",
                          source=source, provenance=prov,
                          include_provenance=include_provenance)
                    component_values.append(value); roots.append(key)
                if hrv_result is not None:
                    value = hrv_result[0]
                    suffix = "fitbit_rmssd" if source == "fitbit" else "apple_sdnn"
                    key = f"recovery.hrv_score.{suffix}"
                    prov = _provenance("daily_metrics", row, details={
                        "formula": "_dev_score", "baseline_n": min(len(prior_hrv), 14),
                        "baseline_median": hrv_result[1], "source_specific": True,
                    })
                    _emit(out, definitions, key, row["date"], value, unit="score_0_100",
                          source=source, provenance=prov,
                          include_provenance=include_provenance)
                    component_values.append(value); roots.append(key)
                if component_values:
                    _emit(out, definitions, f"recovery.score.{source}", row["date"],
                          statistics.mean(component_values), unit="score_0_100",
                          source=source, provenance={
                              "adapter": ADAPTER_ID, "formula": "mean_available_same_source",
                              "parents": roots,
                          }, include_provenance=include_provenance)
            if row["resting_hr"] is not None:
                prior_rhr.append(row["resting_hr"])
            if row["hrv"] is not None:
                prior_hrv.append(row["hrv"])


def _load_sleep(conn, definitions, date_range, context, include_provenance, out):
    daily_sleep: dict[str, tuple[Any, str, dict[str, Any]]] = {}
    if has_columns(conn, "daily_metrics", "date", "source", "sleep_hours"):
        where, params = date_where("date", date_range)
        by_date: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in _rows(conn, "SELECT date,source,sleep_hours FROM daily_metrics WHERE " + where,
                         params):
            by_date[row["date"]][(row.get("source") or "").casefold()] = row
        for day, sources in by_date.items():
            order = ["fitbit"] + sorted(name for name in sources if name != "fitbit")
            chosen = next((sources[name] for name in order
                           if name in sources
                           and sources[name].get("sleep_hours") is not None), None)
            if chosen:
                source = (chosen.get("source") or "").casefold()
                daily_sleep[day] = (chosen["sleep_hours"], source,
                                    _provenance("daily_metrics", chosen,
                                                details={"column": "sleep_hours",
                                                         "selected_provider": source}))

    if not table_exists(conn, "sleep_log"):
        for day, (value, source, prov) in daily_sleep.items():
            _emit(out, definitions, "sleep.duration_hours", day, value, unit="hours",
                  source=source, provenance=prov,
                  include_provenance=include_provenance)
        return
    columns = table_columns(conn, "sleep_log")
    if "date" not in columns:
        return
    where, params = date_where("date", date_range)
    rows = _rows(conn, "SELECT * FROM sleep_log WHERE " + where, params)
    parser = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    by_day = {row["date"]: row for row in rows}
    for day in sorted(set(daily_sleep) | set(by_day)):
        row = by_day.get(day)
        row_source = None
        if row:
            row_source = row.get("source") or "sleep_log"
        if row and row.get("time_asleep_hours") is not None:
            value, source = row["time_asleep_hours"], row_source
            prov = _provenance("sleep_log", row, details={"column": "time_asleep_hours"})
        elif day in daily_sleep:
            value, source, prov = daily_sleep[day]
        else:
            value = None
        if value is not None:
            _emit(out, definitions, "sleep.duration_hours", day, value, unit="hours",
                  source=source, provenance=prov,
                  include_provenance=include_provenance)
        if not row:
            continue
        prov = _provenance("sleep_log", row, details={
            "stored_provenance": row.get("provenance"),
        })
        for column, (key, unit) in SLEEP_FIELDS.items():
            if column in columns:
                _emit(out, definitions, key, day, row.get(column), unit=unit,
                      source=row_source, provenance={**prov, "column": column},
                      include_provenance=include_provenance)
        bedtime = _strict_hhmm(row.get("bedtime"), parser)
        wake = _strict_hhmm(row.get("wake_time"), parser)
        _emit(out, definitions, "sleep.bedtime_min", day, bedtime, unit="minute_of_day",
              source=row_source, provenance={**prov, "column": "bedtime"},
              include_provenance=include_provenance)
        _emit(out, definitions, "sleep.wake_min", day, wake, unit="minute_of_day",
              source=row_source, provenance={**prov, "column": "wake_time"},
              include_provenance=include_provenance)
        asleep, in_bed = row.get("time_asleep_hours"), row.get("time_in_bed_hours")
        efficiency = asleep / in_bed if asleep is not None and in_bed and in_bed > 0 else None
        _emit(out, definitions, "sleep.efficiency", day, efficiency, unit="ratio",
              source=row_source, provenance={
                  **prov, "formula": "time_asleep_hours/time_in_bed_hours",
              }, include_provenance=include_provenance)


def _load_subjective(conn, definitions, date_range, include_provenance, out):
    if not table_exists(conn, "subjective_daily"):
        return
    columns = table_columns(conn, "subjective_daily")
    if "date" not in columns:
        return
    where, params = date_where("date", date_range)
    for row in _rows(conn, "SELECT * FROM subjective_daily WHERE " + where, params):
        source = row.get("source") or "manual"
        prov = _provenance("subjective_daily", row)
        for column, key in SUBJECTIVE_FIELDS.items():
            if column in columns:
                _emit(out, definitions, key, row["date"], row.get(column), source=source,
                      provenance={**prov, "column": column},
                      include_provenance=include_provenance)
        if "brain_dump" in columns and (row.get("brain_dump") or "").strip():
            _emit(out, definitions, "subjective.brain_dump_logged", row["date"], 1,
                  source=source, provenance={**prov, "column": "brain_dump",
                                             "text_included": False},
                  include_provenance=include_provenance)

    effective = effective_completeness(conn, date_range)
    observed = {getattr(item, "observed_at", None) for item in out
                if getattr(item, "feature_key", None) == "subjective.brain_dump_logged"}
    for day, comp in complete_dates(effective, "other_event").items():
        if day in observed:
            continue
        _emit(out, definitions, "subjective.brain_dump_logged", day, 0,
              state="structural_zero", source=comp["source"], provenance={
                  "adapter": ADAPTER_ID, "table": "capture_completeness_revisions",
                  "natural_key": f"capture_completeness_revisions:{comp['id']}",
              }, include_provenance=include_provenance)


def _load_checkins(conn, definitions, date_range, context, include_provenance, out):
    if not has_columns(conn, "checkins", "id", "date", "time", "kind", "value"):
        return
    where, params = date_where("date", date_range)
    parser = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(
        conn, "SELECT * FROM checkins WHERE " + where + " ORDER BY date,id", params,
    ):
        minute = _strict_hhmm(row.get("time"), parser)
        if minute is None or row.get("kind") not in {"energy", "focus", "mood"}:
            continue
        bucket = "am" if minute < 720 else "pm" if minute < 1020 else "eve"
        grouped[(row["date"], row["kind"], bucket)].append(row)
    for (day, kind, bucket), rows in sorted(grouped.items()):
        values = [row["value"] for row in rows if finite(row.get("value")) is not None]
        if not values:
            continue
        sources = sorted({row.get("source") or "manual" for row in rows})
        _emit(out, definitions, f"subjective.checkin.{kind}.{bucket}", day,
              statistics.mean(values),
              source=sources[0] if len(sources) == 1 else "mixed", provenance={
                  "adapter": ADAPTER_ID, "table": "checkins", "aggregation": "mean",
                  "natural_keys": [natural_key("checkins", row) for row in rows],
              }, include_provenance=include_provenance)


def _medication_identity(conn, raw: str, aliases: set[str]) -> tuple[str, str, int | None, str]:
    label = raw.strip()
    normalized = normalized_label(label)
    if normalized in aliases:
        key, token, _ = identity_parts("medication", "medication")
        return key, token, None, "configured_medication_alias"
    if table_exists(conn, "entity_aliases"):
        key, revision = phase2_events.resolve_identity(conn, "medication", label)
        if key:
            return key, token_from_identity_key(key), revision, "phase2_exact_alias"
    key, token, _ = identity_parts("medication", label)
    return key, token, None, "exact_normalized_label"


def _load_medication(conn, definitions, date_range, context, include_provenance, out):
    if not has_columns(conn, "meds_log", "id", "date", "drug"):
        return
    where, params = date_where("date", date_range)
    aliases = {
        normalized_label(str(value))
        for value in context_value(context, "MEDICATION_ALIASES", set())
        if str(value).strip()
    }
    parser = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    identities: dict[str, str] = {}
    for row in _rows(
        conn, "SELECT * FROM meds_log WHERE " + where + " ORDER BY date,id", params,
    ):
        if not (row.get("drug") or "").strip():
            continue
        identity_key, token, revision, rule = _medication_identity(conn, row["drug"], aliases)
        row["_alias_revision_id"] = revision
        row["_identity_rule"] = rule
        identities[token] = identity_key
        grouped[(row["date"], token)].append(row)
    observed_pairs = set(grouped)
    for (day, token), rows in sorted(grouped.items()):
        identity_key = identities[token]
        prefix = f"medication.{token}"
        doses = [row.get("dose_mg") for row in rows]
        total = sum(doses) if all(finite(value) is not None for value in doses) else None
        times = [_strict_hhmm(row.get("time_taken"), parser) for row in rows]
        known_times = sorted(value for value in times if value is not None)
        rebound = [int(bool(row["rebound"])) for row in rows if row.get("rebound") is not None]
        sources = sorted({row.get("source") or "manual" for row in rows})
        source = sources[0] if len(sources) == 1 else "mixed"
        prov = {
            "adapter": ADAPTER_ID, "table": "meds_log", "identity_key": identity_key,
            "natural_keys": [natural_key("meds_log", row) for row in rows],
            "original_labels": sorted({row["drug"] for row in rows}),
            "alias_revision_ids": sorted({row["_alias_revision_id"] for row in rows
                                          if row["_alias_revision_id"] is not None}),
            "identity_rules": sorted({row["_identity_rule"] for row in rows}),
        }
        for suffix, value, unit in (
            ("dose_mg", total, "mg"), ("dose_count", len(rows), "count"),
            ("first_dose_min", min(known_times) if known_times else None, "minute_of_day"),
            ("last_dose_min", max(known_times) if known_times else None, "minute_of_day"),
            ("rebound", max(rebound) if rebound else None, "binary"),
        ):
            key = definitions.identity_key(
                f"{prefix}.{suffix}", prefix="medication.", suffix=f".{suffix}",
                identity_key=identity_key,
            )
            _emit(out, definitions, key, day, value, unit=unit, source=source,
                  provenance=prov, include_provenance=include_provenance)
        if total is not None and len(known_times) == len(rows):
            regime = canonical_json({
                "identity_key": identity_key, "total_dose_mg": total,
                "dose_times_min": known_times,
            })
            key = definitions.identity_key(
                f"{prefix}.regime", prefix="medication.", suffix=".regime",
                identity_key=identity_key,
            )
            _emit(out, definitions, key, day, regime, source=source,
                  provenance={**prov, "formula": "identity_total_sorted_times"},
                  include_provenance=include_provenance)

    effective = effective_completeness(conn, date_range)
    for key, definition in definitions.by_key.items():
        if not key.startswith("medication.") or not key.endswith((".dose_mg", ".dose_count")):
            continue
        identity_key = definition_identity_key(definition)
        if identity_key is None:
            continue
        token = token_from_identity_key(identity_key)
        for day, comp in complete_dates(effective, "medication", identity_key).items():
            if (day, token) in observed_pairs:
                continue
            _emit(out, definitions, key, day, 0, state="structural_zero",
                  unit="count" if key.endswith(".dose_count") else "mg",
                  source=comp["source"], provenance={
                      "adapter": ADAPTER_ID,
                      "table": "capture_completeness_revisions",
                      "natural_key": f"capture_completeness_revisions:{comp['id']}",
                  }, include_provenance=include_provenance)


def load(conn, definitions, date_range, context, include_provenance=False):
    """Return every connected daily-domain observation in the requested range."""
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    _load_wearables(conn, definitions, date_range, context, include_provenance, out)
    _load_sleep(conn, definitions, date_range, context, include_provenance, out)
    _load_subjective(conn, definitions, date_range, include_provenance, out)
    _load_checkins(conn, definitions, date_range, context, include_provenance, out)
    _load_medication(conn, definitions, date_range, context, include_provenance, out)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "REGISTRY_VERSION", "load"]
