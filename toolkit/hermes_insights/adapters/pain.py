"""Explicit pain episodes, onset metadata, self-tests and rehab evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from . import (
    DefinitionIndex, REGISTRY_VERSION, complete_dates, context_value,
    effective_completeness, finite, has_columns, identity_parts, in_range,
    make_observation, natural_key, range_bounds,
)


ADAPTER_ID = "pain"
PRE_OBSERVATION_WINDOWS = (3, 7, 28)
RESPONSE_ORDER = {"better": 1, "same": 0, "worse": -1}
PAIN_SIDES = {"left", "right", "central"}


def _valid_reported_onset(row):
    onset = row.get("reported_onset_date")
    if not onset or row.get("onset_precision") not in {"exact", "approximate"}:
        return False
    try:
        return date.fromisoformat(onset) <= date.fromisoformat(row["date"])
    except (TypeError, ValueError):
        return False


def pre_observation_summary(
    values: Mapping[str, float], observation_date: date, window: int,
) -> dict[str, Any]:
    if window not in PRE_OBSERVATION_WINDOWS:
        raise ValueError("pre-observation window must be 3, 7, or 28")
    start = observation_date - timedelta(days=window)
    days = [(start + timedelta(days=offset)).isoformat() for offset in range(window)]
    known = [values[day] for day in days if day in values]
    return {
        "window_days": window, "from": days[0], "to": days[-1],
        "value": sum(known) if len(known) == window else None,
        "known_dates": len(known), "missing_dates": window - len(known),
        "same_day": values.get(observation_date.isoformat()),
    }


def pain_timeline(pain_rows, self_test_rows, trial_rows, activity_rows=()):
    """Ordered union; no diagnostic or treatment-effect interpretation."""
    items = []
    for row in pain_rows:
        items.append({"date": row["date"], "kind": "pain_observation", "id": row["id"],
                      "region": row["region"], "side": row["side"],
                      "nrs": row["intensity"],
                      "prior_nrs": row.get("_prior_intensity"),
                      "prior_observation_date": row.get("_prior_date"),
                      "exact_change": row.get("_exact_change"),
                      "change_label": row.get("_change_label")})
        if _valid_reported_onset(row):
            items.append({
                "date": row["reported_onset_date"], "kind": "reported_onset",
                "observation_date": row["date"], "id": row["id"],
                "region": row["region"], "side": row["side"],
                "precision": row.get("onset_precision"), "owner_reported": True,
            })
    for row in self_test_rows:
        items.append({"date": row["date"], "kind": "self_test", "id": row["id"],
                      "test": row["test"], "result": row["result"]})
    for row in trial_rows:
        items.append({"date": row["date"], "kind": "rehab_trial", "id": row["id"],
                      "drill": row["drill"], "target": row.get("target"),
                      "response": row["response"], "pain_during": row.get("pain_during"),
                      "evidence_only": True, "not_treatment_effect": True})
    items.extend(activity_rows)
    return sorted(items, key=lambda item: (item["date"], item["kind"], item["id"]))


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
        definitions, key, day, value, state=state, unit=unit, source=source,
        provenance=provenance, include_provenance=include_provenance,
    ))


def _marker_key(definitions, marker, region, side):
    return definitions.first(
        f"pain.{marker}.{region}.{side}",
        f"pain.nrs.{region}.{side}.{marker}",
    )


def _load_pain(conn, definitions, date_range, context, include_provenance, out):
    required = {"id", "date", "region", "side", "intensity", "voided"}
    if not has_columns(conn, "pain_log", *required):
        return []
    allowed = set((context_value(context, "PAIN_CAUSE_MAP", {}) or {}).keys())
    rows = _rows(conn, "SELECT * FROM pain_log WHERE voided=0 ORDER BY date,id")
    by_day = {}
    for row in rows:
        if row.get("region") not in allowed or row.get("side") not in PAIN_SIDES:
            continue
        if finite(row.get("intensity")) is None or not 0 <= row["intensity"] <= 10:
            continue
        by_day[(row["date"], row["region"], row["side"])] = row
    active = sorted(by_day.values(), key=lambda row: (row["date"], row["id"]))
    by_identity = defaultdict(list)
    previous = {}
    for row in active:
        identity = (row["region"], row["side"])
        prior = previous.get(identity)
        change = row["intensity"] - prior["intensity"] if prior else None
        label = ("higher observed pain" if change is not None and change > 0 else
                 "lower observed pain" if change is not None and change < 0 else
                 "same observed pain" if change == 0 else None)
        row["_prior_intensity"] = prior["intensity"] if prior else None
        row["_prior_date"] = prior["date"] if prior else None
        row["_exact_change"] = change
        row["_change_label"] = label
        previous[identity] = row
        by_identity[identity].append(row)
        if in_range(row["date"], date_range):
            key = f"pain.nrs.{row['region']}.{row['side']}"
            _emit(out, definitions, key, row["date"], row["intensity"], unit="nrs_0_10",
                  source=row.get("source"), provenance={
                      "adapter": ADAPTER_ID, "table": "pain_log",
                      "natural_key": natural_key("pain_log", row),
                      "observation_date": row["date"],
                      "missing_is_not_pain_free": True,
                      "prior_observation_date": row["_prior_date"],
                      "prior_nrs": row["_prior_intensity"],
                      "exact_change": change, "change_label": label,
                  }, include_provenance=include_provenance)
        onset = row.get("reported_onset_date")
        precision = row.get("onset_precision")
        if _valid_reported_onset(row) and in_range(onset, date_range):
            key = _marker_key(definitions, "reported_onset", row["region"], row["side"])
            _emit(out, definitions, key, onset, 1, unit="binary",
                  source=row.get("source"), provenance={
                      "adapter": ADAPTER_ID, "table": "pain_log",
                      "natural_key": natural_key("pain_log", row),
                      "reported_onset_date": onset, "onset_precision": precision,
                      "observation_date": row["date"], "owner_reported": True,
                  }, include_provenance=include_provenance)

    for (region, side), series in sorted(by_identity.items()):
        first_positive = next((row for row in series if row["intensity"] > 0), None)
        if first_positive and in_range(first_positive["date"], date_range):
            key = _marker_key(definitions, "first_observed_positive", region, side)
            _emit(out, definitions, key, first_positive["date"], 1, unit="binary",
                  source=first_positive.get("source"), provenance={
                      "adapter": ADAPTER_ID, "table": "pain_log",
                      "natural_key": natural_key("pain_log", first_positive),
                      "not_owner_onset": True,
                  }, include_provenance=include_provenance)
        unresolved = False
        for row in series:
            if row["intensity"] > 0:
                unresolved = True
            elif row["intensity"] == 0 and unresolved:
                if in_range(row["date"], date_range):
                    key = _marker_key(definitions, "resolution_observed", region, side)
                    _emit(out, definitions, key, row["date"], 1, unit="binary",
                          source=row.get("source"), provenance={
                              "adapter": ADAPTER_ID, "table": "pain_log",
                              "natural_key": natural_key("pain_log", row),
                              "explicit_nrs_zero": True,
                          }, include_provenance=include_provenance)
                unresolved = False

    # Marker absence is a structural zero only under the exact effective pain
    # completeness scope for this region/side. The NRS value itself remains
    # missing: a complete capture day is not permission to call pain absent.
    effective = effective_completeness(conn, date_range)
    observed_markers = {
        (item.observed_at[:10], item.feature_key)
        for item in out
        if item.feature_key.endswith((
            ".reported_onset", ".first_observed_positive",
            ".resolution_observed",
        ))
    }
    for region in sorted(allowed):
        for side in ("left", "right", "central"):
            entity_key, _token, _normalized = identity_parts(
                "other", f"pain {region} {side}",
            )
            for day, comp in complete_dates(
                effective, "pain", entity_key,
            ).items():
                for marker in (
                    "reported_onset", "first_observed_positive",
                    "resolution_observed",
                ):
                    key = _marker_key(definitions, marker, region, side)
                    if key is None or (day, key) in observed_markers:
                        continue
                    _emit(
                        out, definitions, key, day, 0, unit="binary",
                        source=comp["source"], state="structural_zero",
                        provenance={
                            "adapter": ADAPTER_ID,
                            "table": "capture_completeness_revisions",
                            "natural_key": (
                                f"capture_completeness_revisions:{comp['id']}"
                            ),
                            "scope": "pain", "entity_key": entity_key,
                            "marker_absence_only": True,
                            "nrs_missing_is_not_pain_free": True,
                        },
                        include_provenance=include_provenance,
                    )
    return active


def _load_self_tests(conn, definitions, date_range, context, include_provenance, out):
    if not has_columns(
        conn, "self_test_log", "id", "date", "test", "side", "result", "voided",
    ):
        return []
    allowed = set((context_value(context, "SELF_TEST_CATALOG", {}) or {}).keys())
    rows = _rows(conn, "SELECT * FROM self_test_log WHERE voided=0 ORDER BY date,id")
    active = []
    for row in rows:
        if row.get("test") not in allowed or row.get("result") not in {
            "positive", "negative", "equivocal",
        } or row.get("side") not in PAIN_SIDES:
            continue
        active.append(row)
        if in_range(row["date"], date_range):
            _emit(out, definitions,
                  f"pain.self_test.{row['test']}.{row['side']}.result",
                  row["date"], row["result"], unit="result",
                  source=row.get("source"), provenance={
                      "adapter": ADAPTER_ID, "table": "self_test_log",
                      "natural_key": natural_key("self_test_log", row),
                      "evidence_only": True, "not_diagnostic": True,
                  }, include_provenance=include_provenance)
    return active


def _load_trials(conn, definitions, date_range, context, include_provenance, out):
    if not has_columns(
        conn, "exercise_trial_log", "id", "date", "drill", "target", "response",
        "pain_during", "voided",
    ):
        return []
    drills = set((context_value(context, "REHAB_CATALOG", {}) or {}).keys())
    targets = set((context_value(context, "PAIN_CAUSE_MAP", {}) or {}).keys())
    rows = _rows(conn, "SELECT * FROM exercise_trial_log WHERE voided=0 ORDER BY date,id")
    active = []
    for row in rows:
        if row.get("drill") not in drills or row.get("response") not in RESPONSE_ORDER:
            continue
        active.append(row)
        # A null/unknown target remains recorded context; it never fabricates a
        # generic target identity for the analytical key.
        target = row.get("target")
        if not isinstance(target, str) or not target.strip() or not in_range(row["date"], date_range):
            continue
        identity_key, token, _ = identity_parts("other", target)
        prefix = f"rehab.trial.{row['drill']}.{token}"
        provenance = {
            "adapter": ADAPTER_ID, "table": "exercise_trial_log",
            "natural_key": natural_key("exercise_trial_log", row),
            "evidence_only": True, "not_treatment_effect": True,
            "dose_display_only": bool(row.get("dose")),
            "identity_key": identity_key, "original_target": row["target"],
            "target_in_pain_catalog": target in targets,
            "no_clinical_pathway_inferred": target not in targets,
        }
        _emit(out, definitions, f"{prefix}.response", row["date"],
              RESPONSE_ORDER[row["response"]], unit="response_-1_1",
              source=row.get("source"), provenance=provenance,
              include_provenance=include_provenance)
        pain_during = finite(row.get("pain_during"))
        if pain_during is None or not 0 <= pain_during <= 10:
            pain_during = None
        _emit(out, definitions, f"{prefix}.pain_during_nrs", row["date"],
              pain_during, unit="nrs_0_10", source=row.get("source"),
              provenance=provenance, include_provenance=include_provenance)
    return active


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    pain_rows = _load_pain(conn, definitions, date_range, context,
                           include_provenance, out)
    test_rows = _load_self_tests(conn, definitions, date_range, context,
                                 include_provenance, out)
    trial_rows = _load_trials(conn, definitions, date_range, context,
                              include_provenance, out)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


def _supplemental_definitions(conn):
    keys = {
        "training.session", "training.working_sets", "training.loaded_volume_kg",
        "running.session", "running.duration_min", "running.distance_km", "running.kcal",
    }
    for group in ("chest", "back", "arms", "shoulders", "legs", "core", "glutes"):
        keys.add(f"training.group.{group}.effective_sets")
    if has_columns(conn, "exercise_submuscles", "sub_region"):
        for row in _rows(conn, "SELECT DISTINCT sub_region FROM exercise_submuscles "
                         "WHERE sub_region IS NOT NULL ORDER BY sub_region"):
            label = row["sub_region"]
            if isinstance(label, str) and label.strip():
                _identity, token, _ = identity_parts("subregion", label)
                keys.add(f"training.subregion.{token}.effective_sets")
    return {key: {"key": key, "unit": "value"} for key in keys}


def _exposure_payload(conn, start, end, context, include_provenance):
    from ..contracts import DateRange
    from . import running, training

    requested = DateRange(start=start, end=end)
    definitions = _supplemental_definitions(conn)
    observations = (
        training.load(conn, definitions, requested, context, include_provenance)
        + running.load(conn, definitions, requested, context, include_provenance)
    )
    series = defaultdict(dict)
    per_day = defaultdict(dict)
    proven_zero_sessions = defaultdict(dict)
    for observation in observations:
        if observation.state not in {"observed", "structural_zero"}:
            continue
        if finite(observation.value) is None:
            continue
        day = observation.observed_at[:10]
        series[observation.feature_key][day] = observation.value
        per_day[day][observation.feature_key] = observation.value
        if (observation.feature_key in {"training.session", "running.session"}
                and observation.state == "structural_zero"):
            proven_zero_sessions[observation.feature_key.split(".", 1)[0]][day] = (
                observation.provenance)
    # AUTO_VALUE base observations remain missing.  For additive exposure
    # summaries only, a source-proven zero session supplies an arithmetic zero
    # contribution without changing the feature's observation state.
    additive = {
        "training": ("training.loaded_volume_kg",),
        "running": ("running.duration_min", "running.distance_km", "running.kcal"),
    }
    zero_evidence = defaultdict(list)
    for family, dates in proven_zero_sessions.items():
        for day in dates:
            for feature_key in additive[family]:
                series[feature_key].setdefault(day, 0)
                per_day[day].setdefault(feature_key, 0)
                zero_evidence[feature_key].append(day)
    activity = []
    for day, values in sorted(per_day.items()):
        for family in ("training", "running"):
            if values.get(f"{family}.session") != 1:
                continue
            activity.append({
                "date": day, "kind": f"{family}_event",
                "id": f"{family}:{day}",
                "features": {key: value for key, value in sorted(values.items())
                             if key.startswith(family + ".")},
                "same_day_context_only": True,
            })
    return series, activity, {key: sorted(days) for key, days in zero_evidence.items()}


def _reported_onset_for_episode(rows):
    """Latest exact owner metadata for an identity; never infer an onset."""
    candidates = [row for row in rows if _valid_reported_onset(row)]
    return max(candidates, key=lambda row: (row["date"], row["id"])) \
        if candidates else None


def supplemental(conn, date_range, context, include_provenance=False):
    """Return the non-scalar pain timeline and pre-observation context.

    These records are display/readiness context, not findings.  Exposure
    aggregates are null unless every date is observed or structurally proven
    by its own adapter's source completeness.
    """
    empty_definitions = DefinitionIndex({})
    pain_rows = _load_pain(conn, empty_definitions, date_range, context, False, [])
    test_rows = _load_self_tests(conn, empty_definitions, date_range, context, False, [])
    trial_rows = _load_trials(conn, empty_definitions, date_range, context, False, [])
    by_identity = defaultdict(list)
    for row in pain_rows:
        by_identity[(row["region"], row["side"])].append(row)
    markers = []
    for (region, side), rows in sorted(by_identity.items()):
        first = next((row for row in rows if row["intensity"] > 0), None)
        if first is not None and in_range(first["date"], date_range):
            first_index = rows.index(first)
            resolution_index = next(
                (index for index in range(first_index + 1, len(rows))
                 if rows[index]["intensity"] == 0), len(rows) - 1)
            episode_rows = rows[first_index:resolution_index + 1]
            markers.append((region, side, first,
                            _reported_onset_for_episode(episode_rows)))

    all_dates = [date.fromisoformat(row["date"]) for _r, _s, row, _o in markers]
    all_dates += [date.fromisoformat(onset["reported_onset_date"])
                  for _r, _s, _row, onset in markers if onset is not None]
    requested_from, requested_to = range_bounds(date_range)
    if pain_rows and requested_from and requested_to:
        all_dates.extend((date.fromisoformat(requested_from[:10]),
                          date.fromisoformat(requested_to[:10])))
    if all_dates:
        marker_dates = [date.fromisoformat(row["date"])
                        for _r, _s, row, _o in markers]
        onset_dates = [date.fromisoformat(onset["reported_onset_date"])
                       for _r, _s, _row, onset in markers if onset is not None]
        pre_event_start = min(marker_dates + onset_dates) - timedelta(
            days=max(PRE_OBSERVATION_WINDOWS)) if marker_dates else min(all_dates)
        exposure_start = min(min(all_dates), pre_event_start)
        exposure_end = max(all_dates)
        series, activity, zero_evidence = _exposure_payload(
            conn, exposure_start, exposure_end, context, include_provenance)
    else:
        series, activity, zero_evidence = {}, [], {}

    summaries = []
    for region, side, row, onset in markers:
        views = [("observation_date", row["date"], None, None)]
        if onset is not None:
            views.append(("reported_onset", onset["reported_onset_date"],
                          onset.get("onset_precision"), onset))
        for label, event_date, precision, onset_row in views:
            window_values = []
            for feature_key, values in sorted(series.items()):
                for window in PRE_OBSERVATION_WINDOWS:
                    window_values.append({
                        "feature_key": feature_key,
                        **pre_observation_summary(
                            values, date.fromisoformat(event_date), window),
                    })
            summaries.append({
                "region": region, "side": side,
                "trigger": "first_observed_positive",
                "observation_date": row["date"],
                "view": label, "event_date": event_date,
                "onset_precision": precision,
                "onset_report_observation_date": onset_row["date"]
                    if onset_row else None,
                "onset_report_natural_key": natural_key("pain_log", onset_row)
                    if onset_row else None,
                "windows_end_before_event": True,
                "same_day_is_separate": True,
                "features": window_values,
                "not_a_statistical_finding": True,
            })

    timeline = pain_timeline(pain_rows, test_rows, trial_rows, activity)
    timeline = [item for item in timeline if in_range(item["date"], date_range)]
    return {
        "timeline": timeline,
        "pre_observation_summaries": summaries,
        "summary_zero_contribution_from_proven_session": zero_evidence,
        "missing_is_not_pain_free": True,
        "rehab_evidence_is_not_treatment_effect": True,
    }


__all__ = [
    "ADAPTER_ID", "PRE_OBSERVATION_WINDOWS", "REGISTRY_VERSION", "load",
    "pain_timeline", "pre_observation_summary", "supplemental",
]
