"""Source-backed observations with no statistical testing or ledger eligibility."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
import math
import statistics
from typing import Any, Mapping, Sequence

from . import associations
from .contracts import AdapterContext, DateRange, FeatureDefinition
from .frame import FrameError, build_feature_frame
from .provenance import sha256_id


CONTRACT = "descriptive-observations-v1"
_SAMPLE_LIMIT = 12
_SOURCE_COMPARISON_LIMIT = 8


def _summary(values: Sequence[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _source_ids(observations: Sequence[Mapping[str, Any]]) -> list[str]:
    keys, _ = associations._observation_lineage_parts(observations)
    return sorted(sha256_id({"source_row": key}) for key in keys)


def _observation_id(row: Mapping[str, Any]) -> str:
    return sha256_id({"contract": CONTRACT, "observation": row})


def _sample(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if len(rows) <= _SAMPLE_LIMIT:
        return list(rows)
    # Chronological endpoints are selected without searching for strong effects.
    return list(rows[:_SAMPLE_LIMIT // 2]) + list(rows[-_SAMPLE_LIMIT // 2:])


def _requires_protocol(definition):
    return definition.pillar == "fitness" and (
        definition.temporal_type == "slow_measurement"
        or definition.completeness_profile == "SLOW_EPISODE"
    )


def _protocol_identity(observation):
    provenance = observation.get("provenance") or {}
    identity = {field: provenance[field]
                for field in ("protocol", "component_protocol", "component_protocols")
                if field in provenance}
    if not identity:
        return None
    for value in identity.values():
        values = value if isinstance(value, (list, tuple)) else [value]
        if not values or any(not isinstance(item, str) or not item.strip() for item in values):
            return None
    for field in ("formula", "target"):
        if field in provenance:
            identity[field] = provenance[field]
    return identity


def _protocol_comparability(definition, observations):
    if not _requires_protocol(definition):
        return None
    identities = [_protocol_identity(row) for row in observations]
    known = {sha256_id(identity): identity for identity in identities if identity is not None}
    status = (
        "protocol_unavailable" if not identities or any(item is None for item in identities) else
        "protocol_incompatible" if len(known) > 1 else "compatible"
    )
    return {"status": status, "protocols": [known[key] for key in sorted(known)]}


def _source_summary(definition, source, rows):
    selected = [row for row in rows if row["source"] == source]
    comparability = _protocol_comparability(
        definition, [observation for row in selected for observation in row["observations"]],
    )
    if comparability is not None and comparability["status"] != "compatible":
        return {"source": source, **_summary([]), "n": len(selected),
                "reason": comparability["status"], "comparability": comparability}
    return {"source": source, **_summary([row["value"] for row in selected]),
            **({"comparability": comparability} if comparability is not None else {})}


def _partial_metadata(row):
    provenance = row.get("provenance") or {}
    if (row["state"] != "observed" or not row["feature_key"].startswith("nutrition.logged.")
            or provenance.get("adapter") != "nutrition" or provenance.get("table") != "nutrition_log"
            or provenance.get("candidate_eligible") is not False
            or provenance.get("all_matching_values_known") is not True):
        return {}
    return {"value_scope": "recorded_subtotal", "completeness": "full_day_coverage_unconfirmed",
            "candidate_eligible": False, "eligible_for_hypothesis": False}


def _partial_facts(observations):
    recorded = {_observation_id(row): row for row in observations
                if row["state"] in {"observed", "structural_zero"}}
    partial = [row for row in recorded.values() if _partial_metadata(row)]
    if not partial:
        return {}
    shown = _sample(partial)
    return {
        "recorded_observation_rows": len(recorded), "partial_recorded_observation_rows": len(partial),
        "partial_recorded_facts": [{
            "observation_id": _observation_id(row),
            **{field: row[field] for field in ("observed_at", "value", "unit", "source")},
            "source_row_ids": _source_ids([row]), **_partial_metadata(row),
        } for row in shown],
        "partial_recorded_facts_shown": len(shown),
    }


def _feature_summary(definition, rows, observations, requested, feature_state):
    range_days = (requested.end - requested.start).days + 1
    midpoint = requested.start + timedelta(days=range_days // 2)
    sources = sorted({row["source"] for row in rows})
    before = [row for row in rows if row["date"] < midpoint.isoformat()]
    after = [row for row in rows if row["date"] >= midpoint.isoformat()]
    comparability = _protocol_comparability(
        definition, [observation for row in rows for observation in row["observations"]],
    )
    reason = (comparability["status"] if comparability is not None
              and comparability["status"] != "compatible" else
              "source_incompatible" if len(sources) > 1 else None)
    compatible = reason is None
    left = _summary([row["value"] for row in before])
    right = _summary([row["value"] for row in after])
    difference = (
        right["mean"] - left["mean"]
        if compatible and left["n"] and right["n"] else None
    )
    sample_rows = _sample(rows)
    return {
        "feature_key": definition.key,
        "display_name": definition.display_name,
        "unit": definition.unit,
        "state": feature_state,
        "range_days": range_days,
        "observed_days": len({row["date"] for row in rows}),
        "observed_units": len(rows),
        "unit_type": "episode" if (definition.temporal_type == "slow_measurement"
                                    or definition.completeness_profile == "SLOW_EPISODE") else "day",
        "days_without_usable_observation": range_days - len({row["date"] for row in rows}),
        "observation_states": dict(sorted(Counter(row["state"] for row in observations).items())),
        "source_row_ids": _source_ids(observations),
        **_partial_facts(observations),
        **({"comparability": comparability} if comparability is not None else {}),
        "summary": _summary([row["value"] for row in rows]) if compatible else None,
        "source_summaries": [_source_summary(definition, source, rows) for source in sources],
        "period_compare": {
            "method": "chronological_calendar_halves",
            "split_date": midpoint.isoformat(),
            "baseline": left if compatible else None,
            "current": right if compatible else None,
            "mean_difference": difference,
            "status": (
                reason if not compatible else
                "insufficient_comparison" if difference is None else
                "no_clear_pattern" if difference == 0 else "observed_difference"
            ),
            "interpretation": "Observed period means only; not a reliable trend or a statistical test.",
        },
        "sample": [{
            "date": row["date"], "value": row["value"], "source": row["source"],
            "observation_ids": sorted({_observation_id(item) for item in row["observations"]}),
        } for row in sample_rows],
        "sample_shown_units": len(sample_rows),
    }


def _comparison(outcome, exposure, component, outcome_rows, indexed):
    result = {
        "outcome_key": outcome.key, "exposure_key": exposure.key,
        "outcome_unit": outcome.unit, "exposure_unit": exposure.unit,
        "component": component,
        "eligible_outcome_units": len(outcome_rows),
        "paired_units": 0, "missing_exposure_units": None,
        "pattern_status": "insufficient_comparison", "reason": None,
        "groups": [], "mean_difference": None, "sample": [],
        "eligible_for_hypothesis": False,
    }
    reason = None
    if "outcome" not in outcome.roles:
        reason = "unsupported_outcome"
    elif "exposure" not in exposure.roles or not exposure.candidate_enabled:
        reason = "unsupported_exposure"
    if reason is None:
        reason = associations._lineage_reason(exposure, outcome)
    if reason is None and not associations._relation_allowed(exposure, outcome):
        reason = "forbidden_relation"
    if reason is None and component is None:
        reason = "unsupported_one_day_alignment"
    if reason is None:
        reason = (
            associations._component_lineage_reason(exposure, outcome, component)
            or associations._strict_boundary(outcome.key, component, exposure)
        )
    if reason:
        return {**result, "reason": reason}
    candidate = associations._aligned_candidate(outcome_rows, exposure, component, indexed, "ordinal")
    rows = candidate["rows"]
    result.update(paired_units=len(rows), missing_exposure_units=candidate["missing_n"])
    lineage = associations._candidate_lineage_status(exposure, outcome, rows)
    result["lineage_status"] = lineage
    if lineage in {"mechanical_tautology", "composite_root"}:
        return {**result, "reason": lineage}
    eras = sorted({row["source_era"] for row in rows})
    result["source_eras"] = eras
    result["unique_source_rows"] = len(_source_ids([
        observation for row in rows
        for observation in (*row["outcome_observations"], *row["exposure_observations"])
    ]))
    result["sample"] = [{
        "date": row["date"], "outcome": row["outcome"], "exposure": row["exposure"],
        "outcome_observation_ids": sorted({_observation_id(item) for item in row["outcome_observations"]}),
        "exposure_observation_ids": sorted({_observation_id(item) for item in row["exposure_observations"]}),
    } for row in _sample(rows)]
    if len(eras) > 1:
        return {**result, "reason": "source_incompatible",
                **_source_comparisons(outcome, exposure, result, rows)}
    return _contrast(outcome, exposure, result, rows)


def _contrast(outcome, exposure, result, rows):
    comparability = {}
    for definition, field, label in (
        (outcome, "outcome_observations", "outcome"),
        (exposure, "exposure_observations", "exposure"),
    ):
        check = _protocol_comparability(definition, [observation for row in rows for observation in row[field]])
        if check is not None:
            comparability[label] = check
    if comparability:
        result["comparability"] = comparability
        reasons = [check["status"] for check in comparability.values() if check["status"] != "compatible"]
        if reasons:
            return {**result, "reason": reasons[0]}
    if not rows:
        return {**result, "reason": "no_paired_observations"}
    exposure_values = [row["exposure"] for row in rows]
    binary = set(exposure_values) <= {0.0, 1.0}
    threshold = 0.0 if binary else statistics.median(exposure_values)
    lower = [row for row in rows if row["exposure"] <= threshold]
    higher = [row for row in rows if row["exposure"] > threshold]
    result.update(grouping="recorded_zero_vs_one" if binary else "at_or_below_vs_above_observed_median",
                  exposure_threshold=threshold)
    result["groups"] = [{
        "label": label,
        "outcome": _summary([row["outcome"] for row in group]),
        "exposure": _summary([row["exposure"] for row in group]),
    } for label, group in (("lower", lower), ("higher", higher))]
    if not lower or not higher:
        return {**result, "reason": "no_exposure_contrast"}
    lower_mean = statistics.mean(row["outcome"] for row in lower)
    higher_mean = statistics.mean(row["outcome"] for row in higher)
    difference = higher_mean - lower_mean
    result.update(
        mean_difference=difference,
        pattern_status="no_clear_pattern" if difference == 0 else "observed_difference",
        counterexamples={
            "definition": "Units on the opposite side of the other group's observed outcome mean.",
            "lower_group": sum(row["outcome"] > higher_mean if difference > 0 else row["outcome"] < higher_mean for row in lower) if difference else None,
            "higher_group": sum(row["outcome"] < lower_mean if difference > 0 else row["outcome"] > lower_mean for row in higher) if difference else None,
        },
    )
    return result


def _source_pair(row):
    return tuple(
        tuple(sorted({str(observation.get("source") or "unknown") for observation in row[field]}))
        for field in ("outcome_observations", "exposure_observations")
    )


def _source_comparisons(outcome, exposure, pooled, rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(_source_pair(row), []).append(row)
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    results = []
    for (outcome_sources, exposure_sources), selected in ordered[:_SOURCE_COMPARISON_LIMIT]:
        source_identity = {"outcome_sources": list(outcome_sources), "exposure_sources": list(exposure_sources)}
        source_rows = _source_ids([
            observation for row in selected
            for observation in (*row["outcome_observations"], *row["exposure_observations"])
        ])
        result = {
            **{field: pooled[field] for field in ("outcome_key", "exposure_key", "outcome_unit", "exposure_unit", "component", "lineage_status")},
            "source_identity": source_identity,
            "source_id": sha256_id({"outcome_key": outcome.key, "exposure_key": exposure.key,
                                    "source_identity": source_identity}),
            "paired_units": len(selected), "eligible_outcome_units": None, "missing_exposure_units": None,
            "denominator_basis": "complete_pairs_with_known_sources",
            "source_row_ids": source_rows, "unique_source_rows": len(source_rows),
            "independent_corroboration": False, "eligible_for_hypothesis": False,
            "pattern_status": "insufficient_comparison", "reason": None,
            "groups": [], "mean_difference": None,
            "sample": [{
                "date": row["date"], "outcome": row["outcome"], "exposure": row["exposure"],
                "outcome_observation_ids": sorted({_observation_id(item) for item in row["outcome_observations"]}),
                "exposure_observation_ids": sorted({_observation_id(item) for item in row["exposure_observations"]}),
            } for row in _sample(selected)],
        }
        results.append(_contrast(outcome, exposure, result, selected))
    return {"source_comparisons": results, "source_comparisons_total": len(grouped),
            "source_comparisons_shown": len(results),
            "source_comparisons_selection": "largest_pair_counts_then_source_identity"}


def describe_outcome(
    conn, definitions: Sequence[FeatureDefinition], requested: DateRange,
    context: AdapterContext, *, outcome_key: str, exposure_keys: Sequence[str], lag_days: int = 1,
) -> dict[str, Any]:
    """Describe one bounded request using canonical frames and alignment rules.

    The caller validates registered numeric features and owns the read transaction.
    No hypothesis, confidence interval, p-value or predictive result is produced.
    """
    by_key = {definition.key: definition for definition in definitions}
    outcome = by_key[outcome_key]
    selected = [outcome] + [by_key[key] for key in exposure_keys]
    frame_range = DateRange(requested.start - timedelta(days=lag_days), requested.end)
    frame = build_feature_frame(conn, selected, frame_range, context, include_provenance=True)
    observations = associations._enrich_observations(frame["observations"], by_key)
    for row in observations:
        if row["state"] in {"observed", "structural_zero"} and (
            isinstance(row["value"], bool) or not isinstance(row["value"], (int, float))
            or not math.isfinite(row["value"])
        ):
            raise FrameError("adapter_contract_error", "descriptive numeric observation is invalid")
    indexed = associations._by_feature(observations)
    units = {definition.key: associations._outcome_rows(indexed, definition.key, requested, definition)
             for definition in selected}
    features = [_feature_summary(
        definition, units[definition.key],
        [row for row in observations if row["feature_key"] == definition.key
         and requested.start.isoformat() <= row["observed_at"][:10] <= requested.end.isoformat()],
        requested, frame["feature_states"][definition.key],
    ) for definition in selected]
    comparisons = []
    for exposure in selected[1:]:
        component = next((item for item in associations._components_for(exposure)
                          if item["lag_days"] == lag_days and item["window_days"] == 1
                          and item["transform"] in {"point", "mean", "sum", "count"}), None)
        comparisons.append(_comparison(outcome, exposure, component, units[outcome.key], indexed))
    shared_roots = {}
    for definition in selected:
        for root in definition.lineage_roots:
            shared_roots.setdefault(root, []).append(definition.key)
    shared_sources = {}
    for row in observations:
        for source_id in _source_ids([row]):
            shared_sources.setdefault(source_id, set()).add(row["feature_key"])
    sampled_comparisons = comparisons + [
        item for comparison in comparisons for item in comparison.get("source_comparisons", [])
    ]
    needed_ids = {
        identity for feature in features for row in feature["sample"] for identity in row["observation_ids"]
    } | {
        row["observation_id"] for feature in features for row in feature.get("partial_recorded_facts", [])
    } | {
        identity for comparison in sampled_comparisons for row in comparison["sample"]
        for field in ("outcome_observation_ids", "exposure_observation_ids") for identity in row[field]
    }
    sampled = {_observation_id(row): row for row in observations if _observation_id(row) in needed_ids}
    return {
        "contract": CONTRACT, "eligible_for_hypothesis": False, "inference_status": "not_assessed",
        "requested_range": {"from": requested.start.isoformat(), "to": requested.end.isoformat()},
        "loaded_range": {"from": frame_range.start.isoformat(), "to": frame_range.end.isoformat()},
        "lag_days": lag_days, "features": features, "comparisons": comparisons,
        "input_fingerprint": sha256_id({"definitions": [item.to_dict() for item in selected], "observations": observations}),
        "shared_lineage": [{"root": root, "feature_keys": sorted(keys), "independent_corroboration": False}
                           for root, keys in sorted(shared_roots.items()) if len(keys) > 1],
        "shared_source_rows": [{"source_row_id": identity, "feature_keys": sorted(keys), "independent_corroboration": False}
                               for identity, keys in sorted(shared_sources.items()) if len(keys) > 1],
        "observations": [{
            "observation_id": identity,
            **{field: row.get(field) for field in ("feature_key", "observed_at", "value", "state", "unit", "source")},
            "source_row_ids": _source_ids([row]),
            **_partial_metadata(row),
            **({"protocol_identity": _protocol_identity(row)}
               if _requires_protocol(by_key[row["feature_key"]]) else {}),
        } for identity, row in sorted(sampled.items())],
        "limitations": [
            {"code": "descriptive_only", "message": "Observed values and arithmetic comparisons only; no causation, diagnosis, prediction or confidence claim."},
            {"code": "inference_not_assessed", "message": "Formal association gates are unchanged. A descriptive difference does not establish an association."},
            {"code": "dependent_observations", "message": "Dates and shared source rows are not independent replications; temporal dependence is not adjusted here."},
            {"code": "missing_is_unknown", "message": "Unrecorded days are unknown. Zero is included only when supplied by the validated frame."},
            {"code": "pattern_definition", "message": "No clear pattern means equal observed group or period means for this contrast; it is not evidence of no relationship."},
            {"code": "sampled_lineage", "message": "Displayed observation samples contain at most the first six and last six units per summary or comparison; the input fingerprint covers all loaded observations."},
            *([{"code": "partial_recorded_facts", "message": "Recorded nutrition subtotals remain visible when full-day coverage is unconfirmed. They are not daily totals or inputs to comparisons or inference."}]
              if any(feature.get("partial_recorded_observation_rows") for feature in features) else []),
        ],
    }
