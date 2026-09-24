"""Exact workout/cardio identities and completeness-gated running boundaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
from typing import Any, Iterable, Mapping

from . import (
    DefinitionIndex, REGISTRY_VERSION, canonical_json, context_value,
    finite, has_columns, identity_parts, in_range, interval_dates,
    make_observation, range_bounds, source_sync_intervals,
)
from .training import SUMMARY_WINDOWS, window_summary
from ..normalize import normalized_label


ADAPTER_ID = "running"
RESTART_COMPLETE_DAYS = 21


def workout_natural_keys(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Appendix-B multiset keys for the PK-less workouts table."""
    canonical = []
    for row in rows:
        fields = [row.get(name) for name in
                  ("date", "type", "minutes", "kcal", "km", "source")]
        payload = canonical_json(fields)
        canonical.append((payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()))
    seen: Counter[str] = Counter()
    result = []
    for payload, digest in sorted(canonical):
        seen[payload] += 1
        result.append(f"workouts:{digest}:{seen[payload]}")
    return result


def stop_restart_events(
    run_dates: set[str], complete_dates: set[str], threshold: int = RESTART_COMPLETE_DAYS,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return confirmed stop/restart markers without treating missing as no-run."""
    if threshold != RESTART_COMPLETE_DAYS:
        raise ValueError("running restart threshold is fixed at 21")
    runs = sorted(date.fromisoformat(day) for day in run_dates)
    stops, restarts = [], []

    # A stop becomes historical fact as soon as the first ``threshold`` days
    # after a run are both complete and run-free.  Later missing coverage must
    # not erase that already-confirmed boundary.
    run_day_strings = {day.isoformat() for day in runs}
    for prior in runs:
        first_no_run = prior + timedelta(days=1)
        confirmation_days = {
            (first_no_run + timedelta(days=offset)).isoformat()
            for offset in range(threshold)
        }
        if (confirmation_days <= complete_dates
                and not confirmation_days & run_day_strings):
            stops.append({
                "date": first_no_run.isoformat(),
                "confirmed_at": (
                    first_no_run + timedelta(days=threshold - 1)
                ).isoformat(),
                "prior_run": prior.isoformat(),
            })

    # A restart additionally requires the entire interval since the immediately
    # preceding run to be known-complete.  A post-confirmation coverage hole can
    # therefore preserve the stop while correctly suppressing the restart.
    for prior, following in zip(runs, runs[1:]):
        no_run_count = (following - prior).days - 1
        gap = {(prior + timedelta(days=offset)).isoformat()
               for offset in range(1, no_run_count + 1)}
        if no_run_count < threshold or not gap <= complete_dates or gap & run_dates:
            continue
        restarts.append({
            "date": following.isoformat(), "prior_run": prior.isoformat(),
            "complete_no_run_days": str(no_run_count),
        })
    return stops, restarts


def consecutive_complete_running_weeks(
    anchor: date, run_dates: set[str], complete_dates: set[str],
) -> int:
    """Count backward through complete configured timezone ISO weeks containing a run."""
    # Sunday itself may close a fully observed ISO week; on every other weekday
    # the latest candidate is the preceding Sunday.
    end = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    count = 0
    while True:
        start = end - timedelta(days=6)
        days = {(start + timedelta(days=offset)).isoformat() for offset in range(7)}
        if not days <= complete_dates or not (days & run_dates):
            return count
        count += 1
        end = start - timedelta(days=1)


def prior_28_weekly_mean(values: Mapping[str, float], anchor: date) -> float | None:
    """Raw prior-28 total divided by four; no acute-load interpretation."""
    days = [(anchor - timedelta(days=7 + offset)).isoformat() for offset in range(28)]
    return sum(values[day] for day in days) / 4 if all(day in values for day in days) else None


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


def _aggregate(rows):
    def total(column):
        values = [row.get(column) for row in rows]
        return sum(values) if values and all(finite(value) is not None for value in values) else None
    duration, distance, kcal = total("minutes"), total("km"), total("kcal")
    return {
        "session": 1,
        "duration_min": duration,
        "distance_km": distance,
        "kcal": kcal,
        "pace_min_per_km": duration / distance
            if duration is not None and distance is not None and distance > 0 else None,
        "intensity_kcal_per_min": kcal / duration
            if kcal is not None and duration is not None and duration > 0 else None,
    }


def _source(rows):
    values = sorted({row.get("source") or "workouts" for row in rows})
    return values[0] if len(values) == 1 else "mixed"


def _known_run_coverage(conn, date_range, context):
    # The current Google Health collector does not fetch workouts. Completeness
    # is therefore opt-in only when a real provider fixture/capability is passed
    # in AdapterContext. Merely having a successful Google Health run is not
    # sufficient evidence of a no-run day.
    sources = context_value(context, "RUN_COMPLETENESS_SOURCES", set())
    sources = set(sources or ())
    if not sources:
        return {}
    return interval_dates(source_sync_intervals(conn, sources), date_range)


def _weekly_progress(
    rows, run_dates, complete, date_range, definitions, out, include_provenance,
):
    if not complete:
        return
    by_week = defaultdict(list)
    for row in rows:
        d = date.fromisoformat(row["date"])
        year, week, _ = d.isocalendar()
        by_week[(year, week)].append(row)
    first, last = min(date.fromisoformat(day) for day in complete), max(
        date.fromisoformat(day) for day in complete)
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        week_days = {(monday + timedelta(days=offset)).isoformat() for offset in range(7)}
        sunday = monday + timedelta(days=6)
        if week_days <= set(complete) and in_range(sunday.isoformat(), date_range):
            week_rows = by_week.get(sunday.isocalendar()[:2], [])
            duration = ([row.get("minutes") for row in week_rows])
            distance = ([row.get("km") for row in week_rows])
            duration_total = sum(duration) if all(finite(v) is not None for v in duration) else None
            distance_total = sum(distance) if all(finite(v) is not None for v in distance) else None
            frequency = len({row["date"] for row in week_rows})
            pace = (duration_total / distance_total
                    if duration_total is not None and distance_total is not None
                    and distance_total > 0 else None)
            prov = {
                "adapter": ADAPTER_ID, "table": "workouts",
                "iso_year": sunday.isocalendar().year,
                "iso_week": sunday.isocalendar().week,
                "complete_week": True,
                "source_sync_run_ids": sorted({
                    run_id for day in week_days for run_id in complete[day]
                }),
                "natural_keys": workout_natural_keys(week_rows),
            }
            for key, value, unit in (
                ("running.progress.weekly_duration_min", duration_total, "min_per_week"),
                ("running.progress.weekly_distance_km", distance_total, "km_per_week"),
                ("running.progress.weekly_frequency", frequency, "runs_per_week"),
                ("running.progress.weekly_pace_min_per_km", pace, "min_per_km"),
            ):
                _emit(out, definitions, key, sunday.isoformat(), value, unit=unit,
                      source="workouts", provenance=prov,
                      include_provenance=include_provenance)
        monday += timedelta(days=7)


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    if not has_columns(conn, "workouts", "date", "type", "minutes", "kcal", "km", "source"):
        return out
    # Read full history for stop/restart and days-since, but emit only the range.
    all_rows = _rows(conn, """SELECT date,type,minutes,kcal,km,source FROM workouts
        WHERE date IS NOT NULL ORDER BY date,type,minutes,kcal,km,source""")
    rows = [row for row in all_rows if in_range(row["date"], date_range)]
    run_keys = context_value(context, "RUN_TYPE_KEYS", set())
    run_keys = set(run_keys.keys() if isinstance(run_keys, Mapping) else run_keys or ())
    run_keys = {normalized_label(value) for value in run_keys}

    by_type_day = defaultdict(list)
    for row in rows:
        normalized = normalized_label(row.get("type") or "")
        if normalized:
            by_type_day[(row["date"], normalized)].append(row)
    observed: set[tuple[str, str]] = set()
    for (day, workout_type), type_rows in sorted(by_type_day.items()):
        identity_key, token, _ = identity_parts("cardio_type", workout_type)
        prefix = f"cardio.type.{token}"
        aggregate = _aggregate(type_rows)
        prov = {
            "adapter": ADAPTER_ID, "table": "workouts",
            "identity_key": identity_key, "original_labels": sorted({r["type"] for r in type_rows}),
            "natural_keys": workout_natural_keys(type_rows),
            "original_sources": sorted({r.get("source") or "workouts" for r in type_rows}),
            "numeric_totals_require_all_rows": True,
        }
        for suffix, unit in (
            ("session", "binary"), ("duration_min", "min"),
            ("distance_km", "km"), ("kcal", "kcal"),
        ):
            key = definitions.identity_key(
                f"{prefix}.{suffix}", prefix="cardio.type.", suffix=f".{suffix}",
                identity_key=identity_key,
            )
            _emit(out, definitions, key, day, aggregate[suffix], unit=unit,
                  source=_source(type_rows), provenance=prov,
                  include_provenance=include_provenance)
            if key and aggregate[suffix] is not None:
                observed.add((day, key))

    all_run_rows = [row for row in all_rows
                    if normalized_label(row.get("type") or "") in run_keys]
    run_rows = [row for row in all_run_rows if in_range(row["date"], date_range)]
    by_run_day = defaultdict(list)
    for row in run_rows:
        by_run_day[row["date"]].append(row)
    for day, day_rows in sorted(by_run_day.items()):
        aggregate = _aggregate(day_rows)
        prov = {
            "adapter": ADAPTER_ID, "table": "workouts",
            "natural_keys": workout_natural_keys(day_rows),
            "exact_run_types": sorted({normalized_label(row["type"]) for row in day_rows}),
            "original_sources": sorted({row.get("source") or "workouts"
                                         for row in day_rows}),
            "numeric_totals_require_all_rows": True,
        }
        for suffix, unit in (
            ("session", "binary"), ("duration_min", "min"),
            ("distance_km", "km"), ("kcal", "kcal"),
            ("pace_min_per_km", "min_per_km"),
            ("intensity_kcal_per_min", "kcal_per_min"),
        ):
            key = f"running.{suffix}"
            _emit(out, definitions, key, day, aggregate[suffix], unit=unit,
                  source=_source(day_rows), provenance={
                      **prov, "proxy": suffix == "intensity_kcal_per_min",
                  }, include_provenance=include_provenance)
            if aggregate[suffix] is not None:
                observed.add((day, key))

    from ..contracts import DateRange
    covered_all = _known_run_coverage(
        conn, DateRange(start=None, end=None, kind="all"), context)
    covered = {day: run_ids for day, run_ids in covered_all.items()
               if in_range(day, date_range)}
    cardio_session_keys = sorted(
        key for key in definitions.by_key
        if key.startswith("cardio.type.") and key.endswith(".session")
    )
    for day, run_ids in sorted(covered.items()):
        zero_provenance = {
            "adapter": ADAPTER_ID, "table": "source_sync_runs",
            "source_sync_run_ids": run_ids,
            "workout_capability_proven": True,
            "numeric_auto_values_remain_missing": True,
        }
        if day not in by_run_day:
            _emit(out, definitions, "running.session", day, 0, unit="binary",
                  state="structural_zero", source="workouts",
                  provenance=zero_provenance,
                  include_provenance=include_provenance)
        for key in cardio_session_keys:
            if (day, key) in observed:
                continue
            _emit(out, definitions, key, day, 0, unit="binary",
                  state="structural_zero", source="workouts",
                  provenance={**zero_provenance, "registered_cardio_key": key},
                  include_provenance=include_provenance)

    run_dates = {row["date"] for row in all_run_rows}
    complete = set(covered_all)
    stops, restarts = stop_restart_events(run_dates, complete)
    all_by_run_day = defaultdict(list)
    for row in all_run_rows:
        all_by_run_day[row["date"]].append(row)
    duration_by_day = {day: _aggregate(day_rows)["duration_min"]
                       for day, day_rows in all_by_run_day.items()}
    distance_by_day = {day: _aggregate(day_rows)["distance_km"]
                       for day, day_rows in all_by_run_day.items()}
    for marker, key in ((stops, "running.stop"), (restarts, "running.restart")):
        for event in marker:
            if not in_range(event["date"], date_range):
                continue
            _emit(out, definitions, key, event["date"], 1, unit="binary",
                  source="workouts", provenance={
                      "adapter": ADAPTER_ID, "formula": "21_complete_no_run_dates",
                      **event,
                      "prior_duration_min": duration_by_day.get(event.get("prior_run")),
                      "prior_distance_km": distance_by_day.get(event.get("prior_run")),
                      "after_duration_min": duration_by_day.get(event["date"]),
                      "after_distance_km": distance_by_day.get(event["date"]),
                  }, include_provenance=include_provenance)

    requested_lo, requested_hi = range_bounds(date_range)
    anchor = None
    if requested_lo and requested_hi:
        latest_run = None
        current = date.fromisoformat(requested_lo)
        end = date.fromisoformat(requested_hi)
        historical = sorted(date.fromisoformat(day) for day in run_dates
                            if day < requested_lo)
        if historical:
            latest_run = historical[-1]
        while current <= end:
            if current.isoformat() in run_dates:
                latest_run = current
            if latest_run is not None:
                _emit(out, definitions, "running.days_since", current.isoformat(),
                      (current - latest_run).days, unit="days", source="workouts",
                      provenance={
                          "adapter": ADAPTER_ID,
                          "formula": "date-last_observed_positive_run_date",
                          "last_run_date": latest_run.isoformat(),
                          "observed_positive_only": True,
                          "does_not_assert_intervening_absence": True,
                      }, include_provenance=include_provenance)
            current += timedelta(days=1)
        anchor = date.fromisoformat(requested_hi)
    elif requested_lo is None and requested_hi is None:
        # Readiness asks adapters for their all-time valid observations.  These
        # current-state derivatives have no stored event row to enumerate, so
        # emit one deterministic as-of observation at the shared configured timezone
        # context date instead of disappearing from all-range readiness.
        anchor = getattr(context, "today", date.today())
        latest_run = max(
            (date.fromisoformat(day) for day in run_dates
             if date.fromisoformat(day) <= anchor),
            default=None,
        )
        if latest_run is not None:
            _emit(out, definitions, "running.days_since", anchor.isoformat(),
                  (anchor - latest_run).days, unit="days", source="workouts",
                  provenance={
                      "adapter": ADAPTER_ID,
                      "formula": "date-last_observed_positive_run_date",
                      "last_run_date": latest_run.isoformat(),
                      "observed_positive_only": True,
                      "does_not_assert_intervening_absence": True,
                      "all_range_as_of_context_today": True,
                  }, include_provenance=include_provenance)
    else:
        anchor = date.fromisoformat(requested_hi) if requested_hi else getattr(
            context, "today", date.today(),
        )

    if complete and anchor is not None:
        _emit(out, definitions, "running.consecutive_weeks", anchor.isoformat(),
              consecutive_complete_running_weeks(anchor, run_dates, complete),
              unit="count", source="workouts", provenance={
                  "adapter": ADAPTER_ID,
                  "formula": "backward_consecutive_complete_configured_timezone_iso_weeks",
                  "all_range_as_of_context_today": (
                      requested_lo is None and requested_hi is None
                  ),
              }, include_provenance=include_provenance)

    _weekly_progress(all_run_rows, run_dates, covered_all, date_range, definitions, out,
                     include_provenance)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = [
    "ADAPTER_ID", "REGISTRY_VERSION", "RESTART_COMPLETE_DAYS", "SUMMARY_WINDOWS",
    "consecutive_complete_running_weeks", "load", "prior_28_weekly_mean",
    "stop_restart_events", "window_summary", "workout_natural_keys",
]
