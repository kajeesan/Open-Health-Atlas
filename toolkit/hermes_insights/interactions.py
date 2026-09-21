"""Deterministic pairwise interaction analysis for ``outcome-v1``."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
import math
from typing import Any, Mapping, Sequence

from .provenance import (
    candidate_key,
    evidence_fingerprint,
    finding_id,
    normalize_row_references,
    source_completeness,
)
from .stats import (
    ANALYSIS_VERSION,
    BOOTSTRAP_ITERATIONS,
    PERMUTATION_ITERATIONS,
    StatsError,
    benjamini_hochberg,
    bootstrap_interval,
    deterministic_seed,
    permutation_p_value,
)


_ACTIONABILITY = {"direct": 3, "indirect": 2}
_TIER = {
    "replicated": 0,
    "exploratory_unreplicated": 1,
    "exploratory_screen": 2,
    "insufficient": 3,
}


def _single_rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
    definition = item["_definition"]
    component = item["exposure"]["components"][0]
    return (
        _TIER[item["quality"]["tier"]],
        item["testing"]["q"] if item["testing"]["q"] is not None else math.inf,
        -abs(item["effect"]["oriented_estimate"]),
        -_ACTIONABILITY.get(definition.actionability, 0),
        definition.preferred_rank,
        (
            component["exposure_key"],
            component["lag_days"],
            component["window_days"],
            component["transform"],
        ),
    )


def _increment(rows: Sequence[tuple[int, int, int]]) -> float | None:
    cells: dict[tuple[int, int], list[int]] = {
        (0, 0): [], (1, 0): [], (0, 1): [], (1, 1): [],
    }
    for a_value, b_value, outcome in rows:
        cells[(a_value, b_value)].append(outcome)
    if any(not values for values in cells.values()):
        return None
    # The shared baseline cancels: p11 - max(p10, p01). Compare the
    # single-cell rates exactly, then divide once to retain threshold ties.
    a_positive, a_n = sum(cells[(1, 0)]), len(cells[(1, 0)])
    b_positive, b_n = sum(cells[(0, 1)]), len(cells[(0, 1)])
    if a_positive * b_n >= b_positive * a_n:
        best_positive, best_n = a_positive, a_n
    else:
        best_positive, best_n = b_positive, b_n
    both_positive, both_n = sum(cells[(1, 1)]), len(cells[(1, 1)])
    return (both_positive * best_n - best_positive * both_n) / (both_n * best_n)


def _cell_payload(rows: Sequence[tuple[int, int, int]]) -> dict[str, Any]:
    names = {
        (0, 0): "neither",
        (1, 0): "a_only",
        (0, 1): "b_only",
        (1, 1): "both",
    }
    grouped: dict[tuple[int, int], list[int]] = {
        key: [] for key in names
    }
    for a_value, b_value, outcome in rows:
        grouped[(a_value, b_value)].append(outcome)
    rates = {
        names[key]: sum(values) / len(values)
        for key, values in grouped.items()
    }
    counts = {names[key]: len(values) for key, values in grouped.items()}
    neither = rates["neither"]
    return {
        "baseline": neither,
        "neither": rates["neither"],
        "a_only": rates["a_only"],
        "b_only": rates["b_only"],
        "both": rates["both"],
        "component_a_difference": rates["a_only"] - neither,
        "component_b_difference": rates["b_only"] - neither,
        "incremental_risk_difference": _increment(rows),
        "cell_counts": counts,
    }


def _component_core(component: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: component[key]
        for key in ("exposure_key", "lag_days", "window_days", "transform")
    }


def _component_sort_key(component: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        component["exposure_key"],
        component["lag_days"],
        component["window_days"],
        component["transform"],
    )


def _feature_descriptor(definition: Any, *, mode: str | None = None) -> dict[str, Any]:
    source = getattr(definition, "source", {}) or {}
    result = {
        "key": definition.key,
        "display": getattr(definition, "display_name", definition.key),
        "unit": getattr(definition, "unit", None),
        "temporal_type": getattr(definition, "temporal_type", None),
        "direction": getattr(definition, "direction", None),
        "merge_rule": source.get("merge"),
        "zero_semantics": getattr(definition, "zero_semantics", None),
    }
    if mode is not None:
        result["mode"] = mode
    return result


def _display_component(item: Mapping[str, Any]) -> dict[str, Any]:
    definition = item["_definition"]
    component = item["exposure"]["components"][0]
    source = getattr(definition, "source", {}) or {}
    return {
        **_component_core(component),
        "display": getattr(definition, "display_name", definition.key),
        "unit": getattr(definition, "unit", None),
        "temporal_type": getattr(definition, "temporal_type", None),
        "direction": getattr(definition, "direction", None),
        "merge_rule": source.get("merge"),
        "zero_semantics": getattr(definition, "zero_semantics", None),
        "temporal_direction": component["temporal_direction"],
    }


def _source_completeness(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    observations = [
        observation
        for row in rows
        for observation in (
            list(row.get("outcome_observations", ()))
            + list(row.get("exposure_observations", ()))
        )
    ]
    return source_completeness(observations)


def _pair_rows(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[tuple[int, int, int]], list[str]]:
    left_rows = {row["unit_key"]: row for row in left["_rows"]}
    right_rows = {row["unit_key"]: row for row in right["_rows"]}
    combined: list[dict[str, Any]] = []
    numeric: list[tuple[int, int, int]] = []
    strata: list[str] = []
    for key in sorted(set(left_rows) & set(right_rows)):
        a_row, b_row = left_rows[key], right_rows[key]
        if (
            a_row["exposure"] not in (0, 1)
            or b_row["exposure"] not in (0, 1)
            or a_row["outcome"] not in (0, 1)
        ):
            continue
        a_value = int(a_row["exposure"])
        b_value = int(b_row["exposure"])
        outcome = int(a_row["outcome"])
        if outcome != int(b_row["outcome"]):
            continue
        numeric.append((a_value, b_value, outcome))
        era = f"{a_row.get('source_era', 'unknown')}|{b_row.get('source_era', 'unknown')}"
        strata.append(era)
        combined.append({
            "unit_key": key,
            "date": a_row["date"],
            "outcome": outcome,
            "exposure_a": a_value,
            "exposure_b": b_value,
            "source_era": era,
            "outcome_observations": a_row["outcome_observations"],
            "exposure_observations": (
                a_row["exposure_observations"] + b_row["exposure_observations"]
            ),
        })
    return combined, numeric, strata


def _pair_sort_key(pair: tuple[Mapping[str, Any], Mapping[str, Any]]) -> tuple[Any, ...]:
    return (_single_rank(pair[0]), _single_rank(pair[1]))


def analyze_pairwise(
    *,
    outcome: Any,
    modes: Sequence[str],
    singles: Sequence[Mapping[str, Any]],
    analysis_range: Mapping[str, Any],
    baseline_range: Mapping[str, Any],
    input_fingerprint_value: str,
    provenance: Mapping[str, Any],
    top_limit: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    """Test at most the deterministic top 30 eligible binary singles per mode."""

    suppression: Counter[str] = Counter()
    findings: list[dict[str, Any]] = []
    family_sizes: dict[str, int] = {}
    for mode in modes:
        eligible = [
            item for item in singles
            if item["outcome"]["mode"] == mode
            and item.get("_base_pass")
            and item.get("_binary_exposure")
            and not item.get("_context_only_lineage")
            and item["sample"]["outcome_positive_n"] is not None
            and _ACTIONABILITY.get(item["_definition"].actionability, 0) > 0
        ]
        suppression["interaction_ineligible_single"] += sum(
            item["outcome"]["mode"] == mode for item in singles
        ) - len(eligible)
        eligible.sort(key=_single_rank)
        eligible = eligible[: min(30, top_limit)]
        tested: list[dict[str, Any]] = []
        for left, right in sorted(combinations(eligible, 2), key=_pair_sort_key):
            if not (
                (left.get("_effect_pass") and left.get("_q_pass"))
                or (right.get("_effect_pass") and right.get("_q_pass"))
            ):
                suppression["interaction_component_gate"] += 1
                continue
            left_component = _component_core(left["exposure"]["components"][0])
            right_component = _component_core(right["exposure"]["components"][0])
            if _component_sort_key(right_component) < _component_sort_key(left_component):
                left, right = right, left
                left_component, right_component = right_component, left_component
            if left_component["exposure_key"] == right_component["exposure_key"]:
                suppression["interaction_same_feature"] += 1
                continue
            aligned, numeric, strata = _pair_rows(left, right)
            if len(numeric) < 80:
                suppression["interaction_aligned_n"] += 1
                continue
            cell_counts = Counter((row[0], row[1]) for row in numeric)
            # The commissioning brief is stricter than the design prose:
            # every cell must contain more than ten aligned units.
            if any(cell_counts[cell] <= 10 for cell in ((0, 0), (1, 0), (0, 1), (1, 1))):
                suppression["interaction_sparse_cell"] += 1
                continue
            positive = sum(row[2] for row in numeric)
            if positive <= 15 or len(numeric) - positive <= 15:
                suppression["interaction_outcome_class"] += 1
                continue
            orientation = int(left["_orientation"])
            core_components = [left_component, right_component]
            seed = deterministic_seed(
                analysis_version=ANALYSIS_VERSION,
                outcome_key=outcome.key,
                outcome_mode=mode,
                components=core_components,
                analysis_range=analysis_range,
                input_fingerprint=input_fingerprint_value,
            )
            memberships = [(row[0], row[1]) for row in numeric]
            outcomes = [row[2] for row in numeric]

            def permutation_statistic(
                fixed: Sequence[tuple[int, int]], labels: Sequence[int]
            ) -> float | None:
                value = _increment([
                    (membership[0], membership[1], label)
                    for membership, label in zip(fixed, labels)
                ])
                return value * orientation if value is not None else None

            p_value = permutation_p_value(
                memberships,
                outcomes,
                permutation_statistic,
                seed=seed,
                strata=strata,
            )

            def bootstrap_statistic(
                sample: Sequence[tuple[int, int, int]]
            ) -> float | None:
                value = _increment(sample)
                return value * orientation if value is not None else None

            try:
                ci95 = bootstrap_interval(
                    numeric,
                    bootstrap_statistic,
                    seed=seed,
                )
            except StatsError:
                # Exactly 2,000 rows are still drawn; an undefined resample
                # fails the CI gate instead of being silently discarded.
                ci95 = None
                suppression["interaction_bootstrap_undefined"] += 1
            raw_increment = _increment(numeric)
            assert raw_increment is not None
            tested.append({
                "left": left,
                "right": right,
                "rows": aligned,
                "numeric": numeric,
                "strata": strata,
                "components": core_components,
                "seed": seed,
                "p": p_value,
                "ci95": list(ci95) if ci95 is not None else None,
                "raw_increment": raw_increment,
                "oriented_increment": raw_increment * orientation,
                "orientation": orientation,
            })

        q_values = benjamini_hochberg([item["p"] for item in tested])
        family_sizes[mode] = len(tested)
        for item, q_value in zip(tested, q_values):
            increment = item["oriented_increment"]
            effect_pass = abs(increment) >= 0.10
            ci_pass = (
                item["ci95"] is not None
                and not (item["ci95"][0] <= 0 <= item["ci95"][1])
            )
            q_pass = q_value <= 0.05
            if not effect_pass:
                suppression["interaction_increment_effect"] += 1
            if not ci_pass:
                suppression["interaction_ci_includes_zero"] += 1
            if not q_pass:
                suppression["interaction_q"] += 1
            if not (effect_pass and ci_pass and q_pass):
                continue
            display_components = [
                _display_component(item["left"]),
                _display_component(item["right"]),
            ]
            key = candidate_key(outcome.key, mode, item["components"])
            fid = finding_id(
                candidate_key_value=key,
                analysis_range=analysis_range,
                baseline_range=baseline_range,
                input_fingerprint_value=input_fingerprint_value,
            )
            rates = _cell_payload(item["numeric"])
            rates["incremental_risk_difference"] = item["raw_increment"]
            rates["oriented_incremental_risk_difference"] = increment
            rates["oriented_component_a_difference"] = (
                rates["component_a_difference"] * item["orientation"]
            )
            rates["oriented_component_b_difference"] = (
                rates["component_b_difference"] * item["orientation"]
            )
            oriented_ci = item["ci95"]
            raw_ci = (
                list(oriented_ci)
                if item["orientation"] == 1
                else [-oriented_ci[1], -oriented_ci[0]]
            )
            source_observations = [
                observation
                for row in item["rows"]
                for observation in (
                    row["outcome_observations"] + row["exposure_observations"]
                )
            ]
            references = normalize_row_references(source_observations)
            medical_pillars = {
                "medication", "supplement", "lab", "vitals", "pain", "rehab",
            }
            warnings = ["association_not_causation", "multiple_testing"]
            if (
                getattr(outcome, "pillar", None) in medical_pillars
                or getattr(item["left"]["_definition"], "pillar", None)
                in medical_pillars
                or getattr(item["right"]["_definition"], "pillar", None)
                in medical_pillars
            ):
                warnings.append("medical_evidence_only")
            eligible_n = max(
                int(item["left"]["sample"].get(
                    "eligible_n", len(item["left"]["_rows"])
                )),
                int(item["right"]["sample"].get(
                    "eligible_n", len(item["right"]["_rows"])
                )),
            )
            finding = {
                "finding_id": fid,
                "candidate_key": key,
                "outcome": _feature_descriptor(outcome, mode=mode),
                "exposure": {"components": display_components},
                "sample": {
                    "eligible_n": eligible_n,
                    "complete_n": len(item["numeric"]),
                    "missing_n": max(0, eligible_n - len(item["numeric"])),
                    "exposed_n": None,
                    "unexposed_n": None,
                    "outcome_positive_n": sum(row[2] for row in item["numeric"]),
                    "outcome_negative_n": (
                        len(item["numeric"]) - sum(row[2] for row in item["numeric"])
                    ),
                    "interaction_cells": rates.pop("cell_counts"),
                    "source_completeness": _source_completeness(item["rows"]),
                },
                "rates": rates,
                "effect": {
                    "method": "incremental_risk_difference",
                    "estimate": item["raw_increment"],
                    "oriented_estimate": increment,
                    "ci95": raw_ci,
                    "oriented_ci95": list(oriented_ci),
                },
                "testing": {
                    "p": item["p"],
                    "q": q_value,
                    "family_size": len(tested),
                    "method": "source_era_stratified_permutation",
                    "seed": item["seed"],
                    "permutation_iterations": PERMUTATION_ITERATIONS,
                    "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
                },
                "stability": {
                    "status": "not_applicable",
                    "full": increment,
                    "first_half": None,
                    "second_half": None,
                },
                "confounders": {
                    "checked": [{"key": "source_era", "strata": dict(Counter(item["strata"]))}],
                    "unchecked": [],
                    "sensitive_to": [],
                    "weighted_effect": None,
                },
                "evidence_for": [
                    {"code": "sample_gate_pass", "value": len(item["numeric"]), "threshold": 80},
                    {"code": "interaction_effect_gate_pass", "value": increment, "threshold": 0.10},
                    {"code": "interaction_ci_excludes_zero", "value": True, "threshold": True},
                    {"code": "interaction_multiplicity_gate_pass", "value": q_value, "threshold": 0.05},
                ],
                "evidence_against": [],
                "warnings": warnings,
                "quality": {
                    "tier": "exploratory_unreplicated",
                    "eligible_for_hypothesis": True,
                },
                "provenance": dict(provenance),
                "_definition": min(
                    (item["left"]["_definition"], item["right"]["_definition"]),
                    key=lambda definition: (
                        -_ACTIONABILITY.get(definition.actionability, 0),
                        definition.preferred_rank,
                        definition.key,
                    ),
                ),
                "_rows": item["rows"],
                "_source_references": references,
                "_source_observations": source_observations,
                "_base_pass": True,
                "_effect_pass": True,
                "_q_pass": True,
                "_binary_exposure": False,
                "_orientation": item["orientation"],
            }
            finding["provenance"] = {
                **finding["provenance"],
                "evidence_fingerprint": evidence_fingerprint(finding),
            }
            findings.append(finding)
    return findings, dict(sorted(suppression.items())), family_sizes


__all__ = ["analyze_pairwise"]
