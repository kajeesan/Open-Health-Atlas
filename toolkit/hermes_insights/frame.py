"""Registry-driven, read-only Phase 3 feature-frame assembly."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import importlib
import json
import math
import sqlite3
from typing import Any, Iterable, Mapping

from .contracts import (
    AdapterContext, ContractError, DateRange, FeatureDefinition, Observation,
)


REGISTRY_VERSION = "feature-registry-v1"
FRAME_VERSION = "feature-frame-v1"
ADAPTER_MODULE_NAMES = (
    "daily", "training", "running", "nutrition", "events", "quarterly",
    "pain", "labs", "environment", "adherence", "manual",
)
OBSERVATION_STATES = {
    "observed", "structural_zero", "missing", "stale", "not_applicable",
    "not_connected",
}
_DAILY_TEMPORAL_TYPES = {
    "daily_measurement", "event_occurrence", "rolling_exposure", "outcome",
}


class FrameError(RuntimeError):
    """A deterministic adapter/registry contract failure."""

    def __init__(self, code: str, message: str, *, validation: bool = False):
        super().__init__(message)
        self.code = code
        self.validation = validation


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _definition_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    return dict(item)


def _observation_dict(item: Any, *, include_provenance: bool) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        value = item.to_dict()
    elif isinstance(item, Mapping):
        value = dict(item)
    else:
        value = {
            "feature_key": item.feature_key, "observed_at": item.observed_at,
            "value": item.value, "state": item.state, "unit": item.unit,
            "source": item.source, "provenance": item.provenance,
        }
    # Optional provenance means the field is absent, not a misleading empty
    # object that could be interpreted as complete lineage.
    if not include_provenance:
        value.pop("provenance", None)
    return value


def load_adapters() -> dict[str, Any]:
    """Load the code-owned adapter inventory and reject duplicate IDs."""

    modules: dict[str, Any] = {}
    for name in ADAPTER_MODULE_NAMES:
        module = importlib.import_module(f"{__package__}.adapters.{name}")
        adapter_id = getattr(module, "ADAPTER_ID", None)
        if not isinstance(adapter_id, str) or not adapter_id:
            raise FrameError("adapter_contract_error", f"adapter {name} has no ADAPTER_ID")
        if adapter_id in modules:
            raise FrameError("adapter_contract_error", f"duplicate adapter id: {adapter_id}")
        modules[adapter_id] = module
    return modules


def adapter_status(definitions: Iterable[Any], modules: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Return deterministic presence/version status for every referenced adapter."""

    modules = dict(modules or load_adapters())
    result: dict[str, dict[str, Any]] = {}
    for adapter_id in sorted({_value(item, "adapter") for item in definitions}):
        module = modules.get(adapter_id)
        version = getattr(module, "REGISTRY_VERSION", None) if module else None
        connected = bool(module and callable(getattr(module, "load", None))
                         and version == REGISTRY_VERSION)
        result[adapter_id] = {
            "adapter": adapter_id, "present": module is not None,
            "version": version, "expected_version": REGISTRY_VERSION,
            "connected": connected,
            "reason": (None if connected else
                       "adapter_absent" if module is None else
                       "version_handshake_failed" if version != REGISTRY_VERSION else
                       "loader_absent"),
        }
    return result


def _iter_dates(date_range: DateRange) -> list[str]:
    if date_range.start is None or date_range.end is None:
        return []
    start = date_range.start if isinstance(date_range.start, date) else date.fromisoformat(str(date_range.start))
    end = date_range.end if isinstance(date_range.end, date) else date.fromisoformat(str(date_range.end))
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _content_hash(definitions: Iterable[Any]) -> str:
    # The frame must identify the exact same registry document exposed by
    # ``feature-registry``; a second ad-hoc list hash would drift even when the
    # feature semantics were identical.
    from .registry import registry_content_checksum
    return registry_content_checksum(definitions)


def _load_summaries(
    conn: sqlite3.Connection, definitions: list[Any], context: AdapterContext,
    modules: Mapping[str, Any], date_range: DateRange, *, include_provenance: bool,
) -> list[dict[str, Any]]:
    """Build Appendix-B load windows without creating registry entries.

    Summary candidate keys are deliberately not editable registry entries.
    Every absent day must be represented by an adapter-proven structural zero;
    otherwise the affected aggregate remains unknown.
    """

    additive_suffixes = (
        ".session", ".working_sets", ".loaded_volume_kg", ".duration_sec",
        ".duration_min", ".distance_km", ".kcal", ".effective_sets",
    )
    load_definitions = [item for item in definitions
                        if _value(item, "aggregation_id") == "LP_LOAD"
                        and (_value(item, "key") == "training.session"
                             or _value(item, "key").endswith(additive_suffixes))]
    if not load_definitions:
        return []
    grouped: dict[str, list[Any]] = defaultdict(list)
    for item in load_definitions:
        grouped[_value(item, "adapter")].append(item)
    historical: list[Any] = []
    all_range = DateRange(start=None, end=None, kind="all")
    summary_registered = {
        _value(item, "key"): item for item in load_definitions
    }
    for adapter_id in sorted(grouped):
        module = modules.get(adapter_id)
        if module is None or getattr(module, "REGISTRY_VERSION", None) != REGISTRY_VERSION:
            continue
        loaded = module.load(
            conn, tuple(grouped[adapter_id]), all_range, context,
            include_provenance=include_provenance,
        )
        if not isinstance(loaded, (list, tuple)):
            raise FrameError("adapter_contract_error", f"adapter {adapter_id} did not return a list")
        historical.extend(
            _validate_observation(item, summary_registered, all_range)
            for item in loaded
        )

    by_feature_day: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    load_keys = {_value(definition, "key") for definition in load_definitions}
    for item in historical:
        key = _value(item, "feature_key")
        if key not in load_keys:
            continue
        if _value(item, "state") not in {"observed", "structural_zero"}:
            continue
        observed_at = _value(item, "observed_at")
        if not isinstance(observed_at, str) or len(observed_at) < 10:
            continue
        try:
            day = date.fromisoformat(observed_at[:10]).isoformat()
        except ValueError:
            continue
        by_feature_day[key][day].append(item)

    def zero_companion(key: str) -> str | None:
        """Return the event feature that can prove a numeric load is zero.

        AUTO_VALUE absence remains missing in the base frame.  For a window
        sum only, a structurally-proven zero event day contributes numeric
        zero; an observed event with an unknown amount remains unknown.
        """
        if key in {"running.duration_min", "running.distance_km", "running.kcal"}:
            return "running.session"
        if key == "training.loaded_volume_kg":
            return "training.session"
        for prefix in ("training.exercise.", "cardio.type."):
            if key.startswith(prefix):
                head, _separator, suffix = key.rpartition(".")
                if suffix in {
                    "loaded_volume_kg", "duration_sec", "duration_min",
                    "distance_km", "kcal",
                }:
                    return f"{head}.session"
        return None

    anchor = date_range.end or context.today
    windows = (7, 28, 90, 365)
    summary_fn = getattr(modules.get("training"), "window_summary", None)
    if not callable(summary_fn):
        raise FrameError("adapter_contract_error", "training window summary formula is absent")
    summaries: list[dict[str, Any]] = []
    for definition in sorted(load_definitions, key=lambda item: _value(item, "key")):
        key = _value(definition, "key")
        rows = by_feature_day.get(key, {})
        daily: dict[str, float] = {}
        for day, items in rows.items():
            numeric = [_value(item, "value") for item in items]
            if numeric and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                               and math.isfinite(value) for value in numeric):
                daily[day] = float(sum(numeric))
            elif numeric and all(isinstance(value, bool) for value in numeric):
                daily[day] = float(sum(numeric))
        companion = zero_companion(key)
        if companion is not None:
            for day, items in by_feature_day.get(companion, {}).items():
                if day not in daily and any(
                    _value(item, "state") == "structural_zero" for item in items
                ):
                    daily[day] = 0.0
        def days_between(start_offset: int, end_offset: int) -> list[str]:
            return [(anchor - timedelta(days=offset)).isoformat()
                    for offset in range(start_offset, end_offset)]

        def complete_sum(days: list[str]) -> float | None:
            return sum(daily[day] for day in days) if all(day in daily for day in days) else None

        for window in windows:
            calculated = summary_fn(
                daily, anchor, window,
                positive_dates={day for day, value in daily.items() if value > 0},
            )
            current = calculated["current"]
            previous = calculated["previous"]
            delta = calculated["delta"]
            summary = {
                "feature_key": key,
                "anchor_date": anchor.isoformat(),
                "window_days": window,
                "current": current,
                "previous": previous,
                "absolute_change": delta,
                "change_per_day": calculated["delta_per_day"],
                "percent_change": calculated["percent"],
                "new_exposure": calculated["new_exposure"],
                "frequency_per_week": calculated["frequency_per_week"],
                "days_since": calculated["days_since"],
                "complete_current": current is not None,
                "complete_previous": previous is not None,
                "candidate_keys": {
                    "current": (
                        f"{key}|lag=0|window={window}|transform="
                        f"{'count' if key.endswith('.session') else 'sum'}"
                    ),
                    "delta_per_day": f"{key}|lag=0|window={window}|transform=delta_per_day",
                    "frequency_per_week": f"{key}|lag=0|window={window}|transform=frequency_per_week",
                    "days_since": f"{key}|lag=0|window={window}|transform=days_since",
                },
            }
            if key.startswith("running.") and window == 7:
                prior_28_days = days_between(7, 35)
                prior_total = complete_sum(prior_28_days)
                prior_weekly = prior_total / 4 if prior_total is not None else None
                summary.update({
                    "prior_28_total": prior_total,
                    "prior_28_weekly_mean": prior_weekly,
                    "raw_7_minus_prior_28_weekly_mean": (
                        current - prior_weekly
                        if current is not None and prior_weekly is not None else None
                    ),
                    "comparison_label": "raw_7_day_vs_prior_28_day_weekly_mean",
                })
            summaries.append(summary)
    return summaries


def _validate_observation(
    item: Any, registered: Mapping[str, Any], date_range: DateRange,
) -> Observation:
    key, state, observed_at = (_value(item, "feature_key"), _value(item, "state"),
                               _value(item, "observed_at"))
    if key not in registered:
        raise FrameError("adapter_contract_error", f"adapter returned unregistered feature: {key}")
    definition = registered[key]
    if (_value(definition, "completeness_profile") == "STATIC_CONFIG"
            or _value(definition, "temporal_type") == "static_config"
            or _value(definition, "zero_semantics") == "not_observation"):
        raise FrameError(
            "adapter_contract_error",
            f"static config must not be emitted as an observation: {key}",
        )
    try:
        normalized = Observation(
            feature_key=key,
            observed_at=observed_at,
            value=_value(item, "value"),
            state=state,
            unit=_value(item, "unit"),
            source=_value(item, "source"),
            provenance=_value(item, "provenance"),
        )
    except ContractError as exc:
        raise FrameError("adapter_contract_error", f"{key}: {exc}") from exc
    expected_unit = _value(definition, "unit")
    if normalized.unit != expected_unit:
        raise FrameError(
            "adapter_contract_error",
            f"adapter unit drifts for {key}: {normalized.unit!r} != {expected_unit!r}",
        )
    try:
        observed_date = date.fromisoformat(observed_at[:10])
        if observed_date.isoformat() != observed_at[:10]:
            raise ValueError
        if len(observed_at) > 10:
            # Stored event times may be timezone-naive; accepting them preserves
            # the source time without pretending a zone was observed.
            from datetime import datetime
            datetime.fromisoformat(observed_at)
    except (TypeError, ValueError):
        raise FrameError(
            "adapter_contract_error", f"observation time is not ISO for {key}",
        ) from None
    if (date_range.kind == "bounded"
            and (observed_date < date_range.start or observed_date > date_range.end)):
        raise FrameError(
            "adapter_contract_error", f"adapter returned out-of-range observation: {key}",
        )
    value = normalized.value
    if isinstance(value, float) and not math.isfinite(value):
        raise FrameError("adapter_contract_error", f"non-finite value for {key}")
    if state in {"observed", "structural_zero"} and value is None:
        raise FrameError("adapter_contract_error", f"observed value is null for {key}")
    if state == "structural_zero" and value not in (0, 0.0, False):
        raise FrameError("adapter_contract_error", f"structural zero is nonzero for {key}")
    return normalized


def build_feature_frame(
    conn: sqlite3.Connection,
    definitions: Iterable[FeatureDefinition] | Mapping[str, FeatureDefinition],
    date_range: DateRange,
    context: AdapterContext,
    *,
    include_provenance: bool = False,
    modules: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask every registered adapter for data; callers never name sources.

    Missing daily values are represented explicitly.  Slow measurements are
    emitted only on their actual episode rows, so an as-of display can never
    inflate analytical N.  No query or helper in this function executes DDL.
    """

    values = list(definitions.values() if isinstance(definitions, Mapping) else definitions)
    registered = {_value(item, "key"): item for item in values}
    if len(registered) != len(values):
        raise FrameError("registry_contract_error", "duplicate feature key")
    modules = dict(modules or load_adapters())
    statuses = adapter_status(values, modules)
    grouped: dict[str, list[Any]] = defaultdict(list)
    for item in values:
        grouped[_value(item, "adapter")].append(item)

    observations: list[Any] = []
    supplemental: dict[str, Any] = {}
    feature_states: dict[str, str] = {}
    for adapter_id in sorted(grouped):
        status = statuses[adapter_id]
        keys = [_value(item, "key") for item in grouped[adapter_id]]
        if not status["connected"]:
            feature_states.update({key: "not_connected" for key in keys})
            continue
        loaded = modules[adapter_id].load(
            conn, tuple(grouped[adapter_id]), date_range, context,
            include_provenance=include_provenance,
        )
        if not isinstance(loaded, (list, tuple)):
            raise FrameError("adapter_contract_error", f"adapter {adapter_id} did not return a list")
        for item in loaded:
            observations.append(_validate_observation(item, registered, date_range))
        extra_loader = getattr(modules[adapter_id], "supplemental", None)
        if callable(extra_loader):
            extra = extra_loader(
                conn, date_range, context,
                include_provenance=include_provenance,
            )
            if not isinstance(extra, Mapping):
                raise FrameError("adapter_contract_error",
                                 f"adapter {adapter_id} supplemental payload is not an object")
            supplemental[adapter_id] = dict(extra)

    # Explicit missing states are generated only for date-grained contracts.
    # Event structural zeros must already have an effective completeness/run
    # revision in the adapter; this layer never upgrades missing to zero.
    dates = _iter_dates(date_range)
    present: set[tuple[str, str]] = set()
    for item in observations:
        observed_at = _value(item, "observed_at")
        if isinstance(observed_at, str) and len(observed_at) >= 10:
            present.add((_value(item, "feature_key"), observed_at[:10]))
    for definition in values:
        key = _value(definition, "key")
        if feature_states.get(key) == "not_connected":
            continue
        temporal_type = _value(definition, "temporal_type")
        if temporal_type not in _DAILY_TEMPORAL_TYPES:
            continue
        for day in dates:
            if (key, day) not in present:
                observations.append(Observation(
                    feature_key=key, observed_at=day, value=None,
                    state="missing", unit=_value(definition, "unit"),
                    source=None, provenance=None,
                ))

    observations.sort(key=lambda item: (
        _value(item, "observed_at"), _value(item, "feature_key"),
        str(_value(item, "source") or ""),
        json.dumps(_value(item, "provenance") or {}, sort_keys=True, default=str),
    ))
    counts = Counter(_value(item, "state") for item in observations)
    per_feature = Counter(_value(item, "feature_key") for item in observations
                          if _value(item, "state") in {"observed", "structural_zero"})
    for key in registered:
        if key not in feature_states:
            feature_states[key] = "observed" if per_feature[key] else "missing"
    dated = [_value(item, "observed_at")[:10] for item in observations
             if _value(item, "state") in {"observed", "structural_zero"}
             and isinstance(_value(item, "observed_at"), str)]
    requested = {
        "kind": date_range.kind,
        "from": date_range.start.isoformat() if isinstance(date_range.start, date) else date_range.start,
        "to": date_range.end.isoformat() if isinstance(date_range.end, date) else date_range.end,
    }
    if date_range.kind == "all":
        requested.pop("from", None); requested.pop("to", None)
    load_summaries = _load_summaries(
        conn, values, context, modules, date_range,
        include_provenance=include_provenance,
    )
    return {
        "ok": True,
        "meta": {
            "frame_version": FRAME_VERSION,
            "registry_version": REGISTRY_VERSION,
            "registry_sha256": _content_hash(values),
            "timezone": context.timezone,
            "range": requested,
            "observed_from": min(dated) if dated else None,
            "observed_to": max(dated) if dated else None,
            "feature_count": len(values),
            "adapter_count": len(statuses),
            "observation_count": len(observations),
            "observation_states": dict(sorted(counts.items())),
        },
        "adapters": [statuses[key] for key in sorted(statuses)],
        "feature_states": dict(sorted(feature_states.items())),
        "load_summaries": load_summaries,
        "supplemental": {key: supplemental[key] for key in sorted(supplemental)},
        "observations": [_observation_dict(item, include_provenance=include_provenance)
                         for item in observations],
    }


__all__ = [
    "ADAPTER_MODULE_NAMES", "FRAME_VERSION", "FrameError", "REGISTRY_VERSION",
    "adapter_status", "build_feature_frame", "load_adapters",
]
