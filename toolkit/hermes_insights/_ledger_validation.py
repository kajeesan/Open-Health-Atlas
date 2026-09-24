"""Validate analytical evidence without creating seals or persisting records."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from . import associations
from .provenance import candidate_key, finding_id
from ._ledger_contracts import (
    _AUDIT_FINDING_KEYS,
    _COMPONENT_KEYS,
    _CONFOUNDER_KEYS,
    _COVERAGE_KEYS,
    _EFFECT_KEYS,
    _FINDING_KEYS,
    _MODE_COVERAGE_KEYS,
    _OUTCOME_DESCRIPTOR_KEYS,
    _PROVENANCE_KEYS,
    _QUALITY_TIERS,
    _SAMPLE_KEYS,
    _STABILITY_KEYS,
    _TEMPORAL_DIRECTIONS,
    _TESTING_KEYS,
    _TRANSFORMS,
    _WINDOWS,
    _canonical_copy,
    _enum,
    _error,
    _evidence_fingerprint_for,
    _exact_mapping,
    _feature,
    _finite_number,
    _integer,
    _nonnegative_count,
    _numerically_equal,
    _optional_finite_number,
    _probability,
    _sha,
    _text,
    _timestamp,
    _validate_interval,
)


def _component_identity(component: Mapping[str, Any], name: str) -> dict[str, Any]:
    _exact_mapping(component, _COMPONENT_KEYS, name)
    exposure_key = _feature(component["exposure_key"], f"{name}.exposure_key")
    lag_days = _integer(component["lag_days"], f"{name}.lag_days", maximum=365)
    window_days = _integer(component["window_days"], f"{name}.window_days", minimum=1)
    if window_days not in _WINDOWS:
        raise _error("invalid_engine_output", f"{name}.window_days is not allowed")
    transform = _enum(component["transform"], _TRANSFORMS, f"{name}.transform")
    temporal = _enum(
        component["temporal_direction"],
        _TEMPORAL_DIRECTIONS,
        f"{name}.temporal_direction",
    )
    expected_temporal = (
        "same_day_or_order_unknown"
        if lag_days == 0
        else "exposure_precedes_outcome"
    )
    if temporal != expected_temporal:
        raise _error(
            "mismatched_engine_output",
            f"{name}.temporal_direction does not match lag_days",
        )
    for field in (
        "display",
        "unit",
        "temporal_type",
        "direction",
        "merge_rule",
        "zero_semantics",
    ):
        if component[field] is not None and not isinstance(component[field], str):
            raise _error("invalid_engine_output", f"{name}.{field} must be text or null")
    return {
        "exposure_key": exposure_key,
        "lag_days": lag_days,
        "window_days": window_days,
        "transform": transform,
        "temporal_direction": temporal,
    }


def _validate_sample_counts(sample: Mapping[str, Any]) -> int:
    eligible = _nonnegative_count(sample.get("eligible_n"), "sample.eligible_n")
    complete = _nonnegative_count(sample.get("complete_n"), "sample.complete_n")
    missing = _nonnegative_count(sample.get("missing_n"), "sample.missing_n")
    assert eligible is not None and complete is not None and missing is not None
    if complete > eligible or missing != eligible - complete:
        raise _error(
            "invalid_engine_output",
            "sample complete/missing counts do not partition eligible_n",
        )
    for left_name, right_name in (
        ("exposed_n", "unexposed_n"),
        ("outcome_positive_n", "outcome_negative_n"),
    ):
        left = _nonnegative_count(
            sample.get(left_name), f"sample.{left_name}", nullable=True
        )
        right = _nonnegative_count(
            sample.get(right_name), f"sample.{right_name}", nullable=True
        )
        if (left is None) != (right is None):
            raise _error(
                "invalid_engine_output",
                f"sample {left_name}/{right_name} must both be null or counts",
            )
        if left is not None and right is not None and left + right != complete:
            raise _error(
                "invalid_engine_output",
                f"sample {left_name}/{right_name} do not partition complete_n",
            )

    return complete


def _validate_effect_orientation(
    *,
    estimate: float | None,
    oriented: float | None,
    raw_ci: list[float] | None,
    oriented_ci: list[float] | None,
) -> set[int]:
    if (estimate is None) != (oriented is None):
        raise _error(
            "invalid_engine_output",
            "raw and oriented effects must both be null or finite",
        )
    orientation_candidates: set[int] = set()
    if estimate is not None and oriented is not None:
        if _numerically_equal(oriented, estimate):
            orientation_candidates.add(1)
        if _numerically_equal(oriented, -estimate):
            orientation_candidates.add(-1)
        if not orientation_candidates:
            raise _error(
                "invalid_engine_output",
                "oriented effect must equal the raw effect times one orientation",
            )
    if (raw_ci is None) != (oriented_ci is None):
        raise _error(
            "invalid_engine_output",
            "raw and oriented confidence intervals must have matching nullability",
        )
    if (
        raw_ci is not None
        and oriented_ci is not None
        and orientation_candidates
    ):
        orientation_candidates = {
            orientation
            for orientation in orientation_candidates
            if all(
                _numerically_equal(actual, expected)
                for actual, expected in zip(
                    oriented_ci,
                    (
                        raw_ci
                        if orientation == 1
                        else [-raw_ci[1], -raw_ci[0]]
                    ),
                    strict=True,
                )
            )
        }
        if not orientation_candidates:
            raise _error(
                "invalid_engine_output",
                "oriented confidence interval disagrees with effect orientation",
            )
    return orientation_candidates


def _validate_effect_method(
    method: Any,
    *,
    estimate: float | None,
    oriented: float | None,
    raw_ci: list[float] | None,
    oriented_ci: list[float] | None,
    rates: Mapping[str, Any] | None,
) -> None:
    if method is None:
        if any(value is not None for value in (estimate, oriented, raw_ci, oriented_ci)):
            raise _error(
                "invalid_engine_output",
                "an absent effect method requires null effect values",
            )
        if rates is not None:
            raise _error(
                "invalid_engine_output",
                "an absent effect method cannot have rates",
            )
    elif not isinstance(method, str) or not method:
        raise _error("invalid_engine_output", "effect.method is invalid")
    elif method not in {
        "risk_difference",
        "spearman",
        "incremental_risk_difference",
    }:
        raise _error("invalid_engine_output", "effect.method is unknown")
    elif estimate is None:
        raise _error(
            "invalid_engine_output",
            "a computed effect method requires finite estimates",
        )


def _validate_testing(testing: Mapping[str, Any], *, method: str | None) -> None:
    p_value = testing.get("p")
    q_value = testing.get("q")
    for value, name in ((p_value, "testing.p"), (q_value, "testing.q")):
        if value is not None:
            _probability(value, name)
    family_size = _nonnegative_count(
        testing.get("family_size"), "testing.family_size", nullable=True
    )
    seed = _nonnegative_count(testing.get("seed"), "testing.seed", nullable=True)
    for key in ("permutation_iterations", "bootstrap_iterations"):
        _nonnegative_count(testing.get(key), f"testing.{key}")
    testing_method = testing.get("method")
    if method is None:
        if any(
            value is not None
            for value in (p_value, q_value, family_size, seed, testing_method)
        ):
            raise _error(
                "invalid_engine_output",
                "an uncomputed effect cannot have test statistics",
            )
    elif (
        p_value is None
        or q_value is None
        or seed is None
        or family_size is None
        or family_size < 1
        or not isinstance(testing_method, str)
        or not testing_method
    ):
        raise _error(
            "invalid_engine_output",
            "a computed effect requires a positive family and test method",
        )
    expected_testing = {
        "risk_difference": ("fisher_exact", 0, 0),
        "spearman": (
            "spearman_permutation",
            associations.PERMUTATION_ITERATIONS,
            associations.BOOTSTRAP_ITERATIONS,
        ),
        "incremental_risk_difference": (
            "source_era_stratified_permutation",
            associations.PERMUTATION_ITERATIONS,
            associations.BOOTSTRAP_ITERATIONS,
        ),
    }
    if method is not None:
        expected_method, expected_permutations, expected_bootstraps = (
            expected_testing[method]
        )
        if (
            testing_method != expected_method
            or testing["permutation_iterations"] != expected_permutations
            or testing["bootstrap_iterations"] != expected_bootstraps
        ):
            raise _error(
                "invalid_engine_output",
                "effect and testing methods/iterations are inconsistent",
            )


def _validate_stability(
    stability: Mapping[str, Any],
    *,
    method: str | None,
    oriented: float | None,
    component_count: int,
) -> None:
    stability_status = stability.get("status")
    for key, value in stability.items():
        if key != "status" and value is not None:
            _finite_number(value, f"stability.{key}")
    stability_full = stability.get("full")
    if (
        stability_full is not None
        and oriented is not None
        and not _numerically_equal(float(stability_full), oriented)
    ):
        raise _error(
            "invalid_engine_output",
            "stability.full must equal the oriented full-sample effect",
        )
    if component_count == 2:
        if (
            stability_status != "not_applicable"
            or stability_full is None
            or stability.get("first_half") is not None
            or stability.get("second_half") is not None
        ):
            raise _error(
                "invalid_engine_output",
                "interaction stability must be not_applicable with only the full effect",
            )
    else:
        _enum(
            stability_status,
            frozenset({"stable", "unstable", "insufficient"}),
            "stability.status",
        )
        if method is None:
            if (
                stability_status != "insufficient"
                or stability_full is not None
                or stability.get("first_half") is not None
                or stability.get("second_half") is not None
            ):
                raise _error(
                    "invalid_engine_output",
                    "an uncomputed single effect requires null insufficient stability",
                )
        elif stability_full is None:
            raise _error(
                "invalid_engine_output",
                "a computed single effect requires full-sample stability",
            )
        else:
            first_half = stability.get("first_half")
            second_half = stability.get("second_half")
            if first_half is None or second_half is None or stability_full == 0:
                expected_stability = "insufficient"
            else:
                sign = 1 if float(stability_full) > 0 else -1
                expected_stability = (
                    "stable"
                    if (
                        float(first_half) * sign > 0
                        and float(second_half) * sign > 0
                        and abs(float(first_half))
                        >= 0.5 * abs(float(stability_full))
                        and abs(float(second_half))
                        >= 0.5 * abs(float(stability_full))
                    )
                    else "unstable"
                )
            if stability_status != expected_stability:
                raise _error(
                    "mismatched_engine_output",
                    "stability.status disagrees with the frozen chronological-half gates",
                )


def _validate_single_rates(
    rates: Mapping[str, Any],
    *,
    sample: Mapping[str, Any],
    complete: int,
    estimate: float | None,
    oriented: float | None,
) -> None:
    required = {
        "baseline",
        "exposed",
        "unexposed",
        "risk_difference",
        "risk_ratio",
        "oriented_risk_difference",
    }
    if set(rates) != required:
        raise _error("invalid_engine_output", "risk-difference rate fields mismatch")
    baseline = _probability(rates["baseline"], "rates.baseline")
    exposed_rate = _probability(rates["exposed"], "rates.exposed")
    unexposed_rate = _probability(rates["unexposed"], "rates.unexposed")
    risk_difference = _finite_number(
        rates["risk_difference"], "rates.risk_difference"
    )
    oriented_difference = _finite_number(
        rates["oriented_risk_difference"],
        "rates.oriented_risk_difference",
    )
    if not _numerically_equal(risk_difference, exposed_rate - unexposed_rate):
        raise _error(
            "invalid_engine_output",
            "risk_difference does not equal exposed minus unexposed",
        )
    if estimate is None or oriented is None or not (
        _numerically_equal(estimate, risk_difference)
        and _numerically_equal(oriented, oriented_difference)
    ):
        raise _error(
            "invalid_engine_output",
            "risk-difference rates disagree with effect estimates",
        )
    expected_ratio = (
        None if unexposed_rate == 0 else exposed_rate / unexposed_rate
    )
    actual_ratio = rates["risk_ratio"]
    if expected_ratio is None:
        if actual_ratio is not None:
            raise _error(
                "invalid_engine_output",
                "risk_ratio must be null for a zero denominator",
            )
    elif actual_ratio is None or not _numerically_equal(
        _finite_number(actual_ratio, "rates.risk_ratio"), expected_ratio
    ):
        raise _error("invalid_engine_output", "risk_ratio is inconsistent")
    exposed_n = sample["exposed_n"]
    unexposed_n = sample["unexposed_n"]
    for rate, count, name in (
        (exposed_rate, exposed_n, "exposed"),
        (unexposed_rate, unexposed_n, "unexposed"),
    ):
        if (
            count is not None
            and abs(rate * count - round(rate * count)) > 1e-9
        ):
            raise _error(
                "invalid_engine_output",
                f"{name} rate implies a fractional outcome count",
            )
    if exposed_n is not None and complete:
        expected_baseline = (
            exposed_rate * exposed_n + unexposed_rate * unexposed_n
        ) / complete
        if not _numerically_equal(baseline, expected_baseline):
            raise _error(
                "invalid_engine_output",
                "baseline rate is inconsistent with exposure groups",
            )
    positive_n = sample["outcome_positive_n"]
    if (
        positive_n is not None
        and complete
        and abs(baseline * complete - positive_n) > 0.5 + 1e-9
    ):
        raise _error(
            "invalid_engine_output",
            "outcome class counts are inconsistent with baseline rate",
        )
    return


def _validate_pair_rates(
    rates: Mapping[str, Any],
    *,
    sample: Mapping[str, Any],
    complete: int,
    estimate: float | None,
    oriented: float | None,
    orientation_candidates: set[int],
) -> None:
    required = {
        "baseline",
        "neither",
        "a_only",
        "b_only",
        "both",
        "component_a_difference",
        "component_b_difference",
        "incremental_risk_difference",
        "oriented_incremental_risk_difference",
        "oriented_component_a_difference",
        "oriented_component_b_difference",
    }
    if set(rates) != required:
        raise _error("invalid_engine_output", "interaction rate fields mismatch")
    neither = _probability(rates["neither"], "rates.neither")
    a_only = _probability(rates["a_only"], "rates.a_only")
    b_only = _probability(rates["b_only"], "rates.b_only")
    both = _probability(rates["both"], "rates.both")
    baseline = _probability(rates["baseline"], "rates.baseline")
    if not _numerically_equal(baseline, neither):
        raise _error("invalid_engine_output", "interaction baseline must equal neither")
    component_a = _finite_number(
        rates["component_a_difference"], "rates.component_a_difference"
    )
    component_b = _finite_number(
        rates["component_b_difference"], "rates.component_b_difference"
    )
    incremental = _finite_number(
        rates["incremental_risk_difference"],
        "rates.incremental_risk_difference",
    )
    oriented_incremental = _finite_number(
        rates["oriented_incremental_risk_difference"],
        "rates.oriented_incremental_risk_difference",
    )
    oriented_component_a = _finite_number(
        rates["oriented_component_a_difference"],
        "rates.oriented_component_a_difference",
    )
    oriented_component_b = _finite_number(
        rates["oriented_component_b_difference"],
        "rates.oriented_component_b_difference",
    )
    if not (
        _numerically_equal(component_a, a_only - neither)
        and _numerically_equal(component_b, b_only - neither)
        and _numerically_equal(
            incremental,
            both - neither - max(a_only - neither, b_only - neither),
        )
        and estimate is not None
        and oriented is not None
        and _numerically_equal(estimate, incremental)
        and _numerically_equal(oriented, oriented_incremental)
        and any(
            _numerically_equal(
                oriented_component_a,
                component_a * orientation,
            )
            and _numerically_equal(
                oriented_component_b,
                component_b * orientation,
            )
            for orientation in orientation_candidates
        )
    ):
        raise _error(
            "invalid_engine_output",
            "interaction rates disagree with component/effect estimates",
        )
    cells = sample.get("interaction_cells")
    if not isinstance(cells, Mapping) or set(cells) != {
        "neither",
        "a_only",
        "b_only",
        "both",
    }:
        raise _error(
            "invalid_engine_output",
            "interaction cell-count fields mismatch",
        )
    cell_total = sum(
        _nonnegative_count(value, f"sample.interaction_cells.{key}") or 0
        for key, value in cells.items()
    )
    if cell_total != complete:
        raise _error(
            "invalid_engine_output",
            "interaction cells do not partition complete_n",
        )
    for rate, key in (
        (neither, "neither"),
        (a_only, "a_only"),
        (b_only, "b_only"),
        (both, "both"),
    ):
        if abs(rate * cells[key] - round(rate * cells[key])) > 1e-9:
            raise _error(
                "invalid_engine_output",
                f"interaction {key} rate implies a fractional outcome count",
            )
    expected_positive = (
        neither * cells["neither"]
        + a_only * cells["a_only"]
        + b_only * cells["b_only"]
        + both * cells["both"]
    )
    positive_n = sample["outcome_positive_n"]
    if (
        positive_n is None
        or abs(expected_positive - positive_n) > 0.5 + 1e-9
    ):
        raise _error(
            "invalid_engine_output",
            "interaction outcome counts disagree with cell rates",
        )
    return


def _validate_numeric_finding(
    finding: Mapping[str, Any],
    *,
    component_count: int,
) -> None:
    sample = finding["sample"]
    complete = _validate_sample_counts(sample)
    rates = finding["rates"]
    effect = finding["effect"]
    testing = finding["testing"]
    method = effect.get("method")
    estimate = _optional_finite_number(effect.get("estimate"), "effect.estimate")
    oriented = _optional_finite_number(
        effect.get("oriented_estimate"), "effect.oriented_estimate"
    )
    raw_ci = _validate_interval(effect.get("ci95"), "effect.ci95")
    oriented_ci = _validate_interval(
        effect.get("oriented_ci95"), "effect.oriented_ci95"
    )
    orientation_candidates = _validate_effect_orientation(
        estimate=estimate,
        oriented=oriented,
        raw_ci=raw_ci,
        oriented_ci=oriented_ci,
    )
    _validate_effect_method(
        method, estimate=estimate, oriented=oriented,
        raw_ci=raw_ci, oriented_ci=oriented_ci, rates=rates,
    )

    _validate_testing(testing, method=method)

    _validate_stability(
        finding["stability"], method=method,
        oriented=oriented, component_count=component_count,
    )
    confounder_effect = finding["confounders"].get("weighted_effect")
    if confounder_effect is not None:
        _finite_number(confounder_effect, "confounders.weighted_effect")

    if rates is None:
        if method in {"risk_difference", "incremental_risk_difference"}:
            raise _error(
                "invalid_engine_output",
                f"{method} requires a rates object",
            )
        if (
            method == "spearman"
            and estimate is not None
            and oriented is not None
            and (abs(estimate) > 1 or abs(oriented) > 1)
        ):
            raise _error(
                "invalid_engine_output",
                "Spearman estimates must lie between -1 and 1",
            )
        return
    if not isinstance(rates, Mapping):
        raise _error("invalid_engine_output", "finding.rates must be an object or null")

    if component_count == 1 and method == "risk_difference":
        _validate_single_rates(
            rates, sample=sample, complete=complete,
            estimate=estimate, oriented=oriented,
        )
        return

    if component_count == 2 and method == "incremental_risk_difference":
        _validate_pair_rates(
            rates, sample=sample, complete=complete,
            estimate=estimate, oriented=oriented,
            orientation_candidates=orientation_candidates,
        )
        return

    raise _error(
        "invalid_engine_output",
        "rates/effect method do not match the candidate kind",
    )


def _verify_finding(
    finding: Any,
    meta: Mapping[str, Any],
    *,
    audit: bool,
    definition_descriptors: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    allowed = _AUDIT_FINDING_KEYS if audit else _FINDING_KEYS
    mapping = _exact_mapping(finding, allowed, "finding")
    clean = _canonical_copy(mapping, "finding")

    outcome = _exact_mapping(
        clean["outcome"], _OUTCOME_DESCRIPTOR_KEYS, "finding.outcome"
    )
    outcome_key = _feature(outcome.get("key"), "finding.outcome.key")
    outcome_mode = _text(
        outcome.get("mode"),
        "finding.outcome.mode",
        maximum=80,
        token=True,
    )
    if outcome_key != meta["outcome"] or outcome_mode not in meta["modes"]:
        raise _error("mismatched_engine_output", "finding outcome does not match run metadata")
    expected_outcome = definition_descriptors.get(outcome_key)
    if expected_outcome is None:
        raise _error("mismatched_engine_output", "finding outcome is absent from registry")
    for field in (
        "display",
        "unit",
        "temporal_type",
        "direction",
        "merge_rule",
        "zero_semantics",
    ):
        if outcome.get(field) != expected_outcome[field]:
            raise _error(
                "mismatched_engine_output",
                f"finding outcome {field} disagrees with registry",
            )

    exposure = clean["exposure"]
    if not isinstance(exposure, Mapping) or set(exposure) != {"components"}:
        raise _error("invalid_engine_output", "finding.exposure fields mismatch")
    components_value = exposure["components"]
    if not isinstance(components_value, list) or len(components_value) not in (1, 2):
        raise _error("invalid_engine_output", "finding must have one or two components")
    components = [
        _component_identity(value, f"finding.component[{index}]")
        for index, value in enumerate(components_value)
    ]
    if len({item["exposure_key"] for item in components}) != len(components):
        raise _error("invalid_engine_output", "finding component exposures must be distinct")
    for component in components_value:
        expected_component = definition_descriptors.get(component["exposure_key"])
        if expected_component is None:
            raise _error(
                "mismatched_engine_output",
                "finding component is absent from registry",
            )
        for field in (
            "display",
            "unit",
            "temporal_type",
            "direction",
            "merge_rule",
            "zero_semantics",
        ):
            if component.get(field) != expected_component[field]:
                raise _error(
                    "mismatched_engine_output",
                    f"finding component {field} disagrees with registry",
                )
    identity_components = [
        {
            "exposure_key": item["exposure_key"],
            "lag_days": item["lag_days"],
            "window_days": item["window_days"],
            "transform": item["transform"],
        }
        for item in components
    ]
    expected_candidate = candidate_key(outcome_key, outcome_mode, identity_components)
    if clean["candidate_key"] != expected_candidate:
        raise _error("mismatched_engine_output", "candidate key does not match components")

    expected_finding = finding_id(
        candidate_key=expected_candidate,
        analysis_range=meta["analysis_range"],
        baseline_range=meta["baseline_range"],
        input_fingerprint=meta["input_fingerprint"],
    )
    if clean["finding_id"] != expected_finding:
        raise _error("mismatched_engine_output", "finding ID does not match provenance")

    quality = clean["quality"]
    if not isinstance(quality, Mapping) or set(quality) != {
        "tier",
        "eligible_for_hypothesis",
    }:
        raise _error("invalid_engine_output", "finding.quality fields mismatch")
    tier = _enum(quality["tier"], _QUALITY_TIERS, "finding.quality.tier")
    if type(quality["eligible_for_hypothesis"]) is not bool:
        raise _error("invalid_engine_output", "finding eligibility must be boolean")
    if tier == "insufficient" and quality["eligible_for_hypothesis"]:
        raise _error("mismatched_engine_output", "insufficient evidence cannot be eligible")

    provenance = _exact_mapping(
        clean["provenance"], _PROVENANCE_KEYS, "finding.provenance"
    )
    for field in (
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
        "evidence_fingerprint",
    ):
        if field not in provenance:
            raise _error("invalid_engine_output", f"finding provenance missing {field}")
    for field in (
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
    ):
        if provenance[field] != meta[field]:
            raise _error("mismatched_engine_output", f"finding provenance {field} mismatch")
    stored_evidence = _sha(provenance["evidence_fingerprint"], "evidence_fingerprint")
    computed_evidence = _evidence_fingerprint_for(
        clean, provenance["analysis_version"]
    )
    if stored_evidence != computed_evidence:
        raise _error("mismatched_engine_output", "evidence fingerprint mismatch")

    for field in ("evidence_for", "evidence_against", "warnings"):
        if not isinstance(clean[field], list):
            raise _error("invalid_engine_output", f"finding.{field} must be an array")
    sample_allowed = (
        _SAMPLE_KEYS | frozenset({"interaction_cells"})
        if len(components) == 2
        else _SAMPLE_KEYS
    )
    _exact_mapping(clean["sample"], sample_allowed, "finding.sample")
    _exact_mapping(clean["effect"], _EFFECT_KEYS, "finding.effect")
    _exact_mapping(clean["testing"], _TESTING_KEYS, "finding.testing")
    _exact_mapping(clean["stability"], _STABILITY_KEYS, "finding.stability")
    _exact_mapping(
        clean["confounders"], _CONFOUNDER_KEYS, "finding.confounders"
    )
    for polarity in ("evidence_for", "evidence_against"):
        for index, item in enumerate(clean[polarity]):
            if (
                not isinstance(item, Mapping)
                or set(item) not in (
                    {"code", "value", "threshold"},
                    {"code", "value", "threshold", "detail"},
                )
            ):
                raise _error(
                    "invalid_engine_output",
                    f"finding.{polarity}[{index}] fields mismatch",
                )
    if clean["rates"] is not None and not isinstance(clean["rates"], Mapping):
        raise _error("invalid_engine_output", "finding.rates must be an object or null")
    _validate_numeric_finding(clean, component_count=len(components))

    testing = clean["testing"]
    family_map = (
        meta["candidate_family_sizes"]
        if len(components) == 1
        else meta["interaction_family_sizes"]
    )
    if testing["family_size"] is not None and testing["family_size"] != family_map.get(
        outcome_mode
    ):
        raise _error(
            "mismatched_engine_output",
            "finding family_size disagrees with analysis metadata",
        )
    if (
        testing["p"] is not None
        and testing["q"] is not None
        and testing["family_size"] is not None
    ):
        p_value = float(testing["p"])
        q_value = float(testing["q"])
        upper_bound = min(1.0, p_value * int(testing["family_size"]))
        if q_value < p_value and not _numerically_equal(q_value, p_value):
            raise _error(
                "invalid_engine_output",
                "BH-adjusted q cannot be below its raw p value",
            )
        if q_value > upper_bound and not _numerically_equal(
            q_value, upper_bound
        ):
            raise _error(
                "invalid_engine_output",
                "BH-adjusted q exceeds the maximum possible family adjustment",
            )

    effect = clean["effect"]
    oriented_effect = effect["oriented_estimate"]
    context_only = any(
        item.get("code") == "autoregressive_lineage"
        for item in clean["evidence_against"]
        if isinstance(item, Mapping)
    )
    if len(components) == 2:
        interval = effect["oriented_ci95"]
        discovery_pass = (
            oriented_effect is not None
            and abs(float(oriented_effect)) >= 0.10
            and testing["q"] is not None
            and float(testing["q"]) <= 0.05
            and interval is not None
            and not (interval[0] <= 0 <= interval[1])
        )
        expected_tier = (
            "exploratory_unreplicated" if discovery_pass else "insufficient"
        )
        expected_eligible = discovery_pass and not context_only
    else:
        threshold = 0.10 if effect["method"] == "risk_difference" else 0.20
        effect_pass = (
            oriented_effect is not None
            and abs(float(oriented_effect)) >= threshold
        )
        discovery_pass = (
            effect_pass
            and testing["q"] is not None
            and float(testing["q"]) <= 0.10
            and clean["stability"]["status"] == "stable"
        )
        expected_tier = (
            "insufficient"
            if not effect_pass
            else (
                "exploratory_unreplicated"
                if discovery_pass
                else "exploratory_screen"
            )
        )
        expected_eligible = effect_pass and not context_only
    if tier != expected_tier or quality["eligible_for_hypothesis"] != expected_eligible:
        raise _error(
            "mismatched_engine_output",
            "finding quality does not match deterministic numeric gates",
        )

    return clean


def _validate_engine_meta(
    meta: Mapping[str, Any], *, timezone_name: str,
) -> None:
    _timestamp(meta.get("generated_at"), "meta.generated_at")
    if meta.get("timezone") != timezone_name:
        raise _error(
            "invalid_engine_output",
            f"analysis timezone must be {timezone_name}",
        )
    if meta.get("min_n") != associations.DEFAULT_MIN_N:
        raise _error("invalid_engine_output", "analysis min_n is not ledger-safe")
    if meta.get("interactions") not in {"none", "pairwise"}:
        raise _error("invalid_engine_output", "analysis interaction mode is invalid")
    if meta.get("top") != 100:
        raise _error("invalid_engine_output", "analysis top boundary must be 100")
    for field in ("candidate_family_sizes", "interaction_family_sizes"):
        values = meta.get(field)
        if not isinstance(values, Mapping):
            raise _error("invalid_engine_output", f"meta.{field} must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise _error("invalid_engine_output", f"meta.{field} key is invalid")
            _nonnegative_count(value, f"meta.{field}.{key}")


def _validate_analysis_coverage(payload: Mapping[str, Any]) -> None:
    """Bind Phase 4 family metadata, coverage counts, and returned findings."""

    meta = payload["meta"]
    coverage = _exact_mapping(
        payload["coverage"], _COVERAGE_KEYS, "analysis coverage"
    )
    eligible_n = _nonnegative_count(
        coverage["outcome_eligible_n"], "coverage.outcome_eligible_n"
    )
    modes = coverage["modes"]
    if not isinstance(modes, Mapping) or set(modes) != set(meta["modes"]):
        raise _error(
            "mismatched_engine_output",
            "coverage modes do not match the requested analysis modes",
        )
    if not isinstance(coverage["source_manifests"], list):
        raise _error(
            "invalid_engine_output",
            "coverage.source_manifests must be an array",
        )
    if not isinstance(coverage["dependencies"], Mapping):
        raise _error(
            "invalid_engine_output",
            "coverage.dependencies must be an object",
        )
    candidate_sizes = meta["candidate_family_sizes"]
    interaction_sizes = meta["interaction_family_sizes"]
    if set(candidate_sizes) != set(meta["modes"]):
        raise _error(
            "mismatched_engine_output",
            "candidate family modes do not match analysis modes",
        )
    expected_interaction_modes = (
        set(meta["modes"]) if meta["interactions"] == "pairwise" else set()
    )
    if set(interaction_sizes) != expected_interaction_modes:
        raise _error(
            "mismatched_engine_output",
            "interaction family modes do not match interaction selection",
        )

    single_counts = {mode: 0 for mode in meta["modes"]}
    pair_counts = {mode: 0 for mode in meta["modes"]}
    findings = payload["findings"]
    if not isinstance(findings, list):
        raise _error(
            "invalid_engine_output", "analysis findings must be an array"
        )
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise _error(
                "invalid_engine_output", "analysis finding must be an object"
            )
        outcome = finding.get("outcome")
        exposure = finding.get("exposure")
        mode = outcome.get("mode") if isinstance(outcome, Mapping) else None
        components = (
            exposure.get("components") if isinstance(exposure, Mapping) else None
        )
        if mode not in single_counts or not isinstance(components, list):
            raise _error(
                "mismatched_engine_output",
                "finding mode/components disagree with analysis coverage",
            )
        if len(components) == 1:
            single_counts[mode] += 1
        elif len(components) == 2:
            pair_counts[mode] += 1
        else:
            raise _error(
                "invalid_engine_output",
                "finding component count is outside the frozen contract",
            )

    for mode in meta["modes"]:
        current = _exact_mapping(
            modes[mode], _MODE_COVERAGE_KEYS, f"coverage.modes.{mode}"
        )
        counts = {
            key: _nonnegative_count(
                current[key], f"coverage.modes.{mode}.{key}"
            )
            for key in _MODE_COVERAGE_KEYS
        }
        if counts["outcome_eligible_n"] != eligible_n:
            raise _error(
                "mismatched_engine_output",
                "mode outcome coverage disagrees with overall coverage",
            )
        if (
            counts["family_size"] != counts["tested_candidates"]
            or candidate_sizes[mode] != counts["tested_candidates"]
        ):
            raise _error(
                "mismatched_engine_output",
                "candidate family metadata disagrees with tested coverage",
            )
        retained = (
            counts["tested_candidates"] + counts["insufficient_candidates"]
        )
        if retained > counts["generated_candidates"]:
            raise _error(
                "invalid_engine_output",
                "retained candidate counts exceed generated candidates",
            )
        if single_counts[mode] != retained:
            raise _error(
                "mismatched_engine_output",
                "returned single findings disagree with candidate coverage",
            )
        if pair_counts[mode] > interaction_sizes.get(mode, 0):
            raise _error(
                "mismatched_engine_output",
                "returned pair findings exceed their interaction family",
            )


def _definition_descriptor_map(
    definitions: Iterable[Any] | Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    values = definitions.values() if isinstance(definitions, Mapping) else definitions
    result: dict[str, dict[str, Any]] = {}
    for raw in values:
        get = raw.get if isinstance(raw, Mapping) else lambda key: getattr(raw, key)
        key = get("key")
        source = get("source")
        if (
            not isinstance(key, str)
            or key in result
            or not isinstance(source, Mapping)
        ):
            raise _error(
                "registry_contract_error",
                "registry descriptor definitions are invalid",
            )
        result[key] = {
            "display": get("display_name"),
            "unit": get("unit"),
            "temporal_type": get("temporal_type"),
            "direction": get("direction"),
            "merge_rule": source.get("merge"),
            "zero_semantics": get("zero_semantics"),
        }
    return result
