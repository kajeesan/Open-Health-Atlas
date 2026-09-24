"""Pure ordered hypothesis transitions over findings and loaded lineage.

Callers retain database access and clock selection.
"""

from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Any, Mapping, Sequence

from ._ledger_contracts import TransitionDecision, _DIRECTIONS, _error, _sha


def _eval_direction(evaluation: Mapping[str, Any]) -> str:
    value = evaluation["effect_summary"].get("direction")
    if value not in _DIRECTIONS:
        raise _error("ledger_corrupt", "evaluation direction is invalid")
    return value


def _eval_magnitude(evaluation: Mapping[str, Any]) -> float | None:
    effect = evaluation["effect_summary"].get("effect")
    value = effect.get("oriented_estimate") if isinstance(effect, Mapping) else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error("ledger_corrupt", "evaluation effect is invalid")
    return abs(float(value))


def _finding_state(finding: Mapping[str, Any]) -> dict[str, Any]:
    evidence = finding["evidence"]
    effect = evidence["effect"]
    value = effect.get("oriented_estimate")
    magnitude = None if value is None else abs(float(value))
    interval = effect.get("oriented_ci95")
    if interval is not None:
        if (
            not isinstance(interval, list)
            or len(interval) != 2
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in interval
            )
        ):
            raise _error("ledger_corrupt", "finding confidence interval is invalid")
        interval = [float(interval[0]), float(interval[1])]
    testing = evidence["testing"]
    base_pass = (
        effect.get("method") is not None
        and value is not None
        and testing.get("family_size") is not None
    )
    quality = finding["quality_tier"]
    direction = finding["direction"]
    confounder_sensitive = bool(evidence["confounders"].get("sensitive_to"))
    eligible_n = evidence["sample"].get("eligible_n")
    if isinstance(eligible_n, bool) or not isinstance(eligible_n, int) or eligible_n < 0:
        raise _error("ledger_corrupt", "finding eligible_n is invalid")
    return {
        "quality": quality,
        "direction": direction,
        "magnitude": magnitude,
        "interval": interval,
        "base_pass": base_pass,
        "discovery_pass": quality in {"exploratory_unreplicated", "replicated"},
        "screen": quality == "exploratory_screen",
        "confounder_sensitive": confounder_sensitive,
        "eligible_n": eligible_n,
        "one_per_day": evidence["outcome"].get("temporal_type")
        != "slow_measurement",
    }


def _is_context_only_finding(finding: Mapping[str, Any]) -> bool:
    """Identify an engine-owned lineage finding that may inform context only."""

    evidence = finding.get("evidence")
    against = evidence.get("evidence_against") if isinstance(evidence, Mapping) else None
    return (
        not bool(finding.get("eligible_for_hypothesis"))
        and isinstance(against, list)
        and any(
            isinstance(item, Mapping)
            and item.get("code") == "autoregressive_lineage"
            for item in against
        )
    )


def _interval_includes_zero(interval: Sequence[float] | None) -> bool:
    return interval is not None and interval[0] <= 0 <= interval[1]


def _interval_excludes_zero(interval: Sequence[float] | None) -> bool:
    return interval is not None and (interval[1] < 0 or interval[0] > 0)


def _later_disjoint(
    current_from: str | None,
    comparison_to: str | None,
) -> bool:
    return (
        current_from is not None
        and comparison_to is not None
        and current_from > comparison_to
    )


def _overlaps(
    left_from: str | None,
    left_to: str | None,
    right_from: str | None,
    right_to: str | None,
) -> bool:
    return (
        left_from is not None
        and left_to is not None
        and right_from is not None
        and right_to is not None
        and left_from <= right_to
        and right_from <= left_to
    )


def _new_eligible_observations(
    current: Mapping[str, Any],
    comparisons: Sequence[Mapping[str, Any]],
) -> int:
    current_n = int(current["eligible_n"])
    bounded = [
        item
        for item in comparisons
        if item.get("finding_id") is not None
        and item.get("range_from") is not None
        and item.get("range_to") is not None
    ]
    if not bounded:
        return current_n
    latest_to = max(item["range_to"] for item in bounded)
    if _later_disjoint(current["range_from"], latest_to):
        return current_n
    current_from = current.get("range_from")
    current_to = current.get("range_to")
    if not current.get("one_per_day", False):
        prior_total = 0
        for item in bounded:
            sample = item.get("sample_size")
            prior_n = (
                sample.get("eligible_n")
                if isinstance(sample, Mapping)
                else None
            )
            if isinstance(prior_n, bool) or not isinstance(prior_n, int):
                continue
            prior_total += prior_n
        return max(0, current_n - prior_total)

    # Daily outcomes have at most one analytical unit per date.  Merge all
    # prior compatible windows after clipping them to the current window; the
    # resulting day count is an upper bound on already evaluated current rows.
    # Only rows beyond that bound are guaranteed new.  This deliberately
    # refuses to infer novelty from count growth between shifted/narrowed
    # windows whose observation identities are unavailable at the ledger.
    intervals: list[tuple[date, date]] = []
    prior_overlap_bound = 0
    current_start = date.fromisoformat(current_from)
    current_end = date.fromisoformat(current_to)
    for item in bounded:
        start = max(current_start, date.fromisoformat(item["range_from"]))
        end = min(current_end, date.fromisoformat(item["range_to"]))
        if start <= end:
            intervals.append((start, end))
            overlap_days = (end - start).days + 1
            sample = item.get("sample_size")
            prior_n = (
                sample.get("eligible_n")
                if isinstance(sample, Mapping)
                else None
            )
            if isinstance(prior_n, bool) or not isinstance(prior_n, int):
                prior_n = overlap_days
            prior_overlap_bound += min(prior_n, overlap_days)
    intervals.sort()
    merged: list[tuple[date, date]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end))
        elif end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    overlap_capacity = sum(
        (end - start).days + 1 for start, end in merged
    )
    already_seen_upper_bound = min(
        overlap_capacity, prior_overlap_bound,
    )
    return max(0, current_n - already_seen_upper_bound)


def _is_same_direction(current: str, comparison: str) -> bool:
    return current in {"positive", "negative"} and current == comparison


def _is_opposite_direction(current: str, comparison: str) -> bool:
    return {current, comparison} == {"positive", "negative"}


def _latest_discovery(
    evaluations: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for item in reversed(evaluations):
        if item["finding_id"] is not None and (
            item["evidence_class"]
            in {
                "initial_discovery_pass",
                "same_pass_overlap",
                "same_pass_nonoverlap",
            }
            or (
                item["evidence_class"]
                in {"same_exploratory", "incompatible_version"}
                and item["effect_summary"].get("quality", {}).get("tier")
                in {"exploratory_unreplicated", "replicated"}
            )
        ):
            return item
    return None


def _latest_coverage_anchor(
    evaluations: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the furthest compatible range already evaluated.

    Replication and held-out null evidence must be later than *every* prior
    compatible, non-dormant evidence window in the semantic lineage.  Choosing
    only the most recently inserted or latest-magnitude discovery would allow
    a narrowed/backfilled window to move the non-overlap boundary backwards
    and launder already evaluated rows into replication.
    """

    bounded = [
        item
        for item in evaluations
        if item.get("finding_id") is not None
        and item.get("range_from") is not None
        and item.get("range_to") is not None
    ]
    if not bounded:
        return None
    return max(
        bounded,
        key=lambda item: (
            item["range_to"],
            -date.fromisoformat(item["range_from"]).toordinal(),
            item["id"],
        ),
    )


def _prior_null_without_reset(
    evaluations: Sequence[Mapping[str, Any]],
    *,
    after_id: int | None,
) -> Mapping[str, Any] | None:
    for item in reversed(evaluations):
        if after_id is not None and item["id"] <= after_id:
            break
        if (
            item["finding_id"] is not None
            and bool(item["compatible_with_prior"])
            and item["effect_summary"].get("quality", {}).get("tier")
            in {"exploratory_unreplicated", "replicated"}
        ):
            return None
        if item["evidence_class"] == "null_nonoverlap":
            return item
        # Dormancy, incompatible-method observations, overlapping screens, and
        # weakening windows neither increment nor reset the consecutive count.
    return None


def _evidence_kinds(
    *,
    evidence_class: str,
    same_direction: bool,
    confounder_sensitive: bool,
    weak_or_unstable: bool = False,
) -> tuple[tuple[str, str], ...]:
    items: list[tuple[str, str]] = []
    if same_direction and evidence_class not in {
        "null_nonoverlap",
        "incompatible_version",
    }:
        items.append(("for", "same_direction_effect"))
    if evidence_class == "same_pass_nonoverlap":
        items.append(("for", "nonoverlap_replication"))
    if evidence_class == "weakening_window" or weak_or_unstable:
        items.append(("against", "weak_or_unstable"))
    if evidence_class == "null_nonoverlap":
        items.append(("against", "null_window"))
    if evidence_class == "opposite_pass":
        items.append(("against", "opposite_direction_effect"))
    if evidence_class == "incompatible_version":
        items.append(("against", "method_incompatibility"))
    if confounder_sensitive:
        items.append(("against", "confounder_sensitivity"))
    return tuple(items)


def _transition(
    *,
    current: Mapping[str, Any] | None,
    latest: Mapping[str, Any] | None,
    prior_substantive: Mapping[str, Any] | None,
    lineage: Sequence[Mapping[str, Any]],
    compatible: bool,
    explicit: bool,
    dormancy_reason: str | None,
    range_from: str | None,
    range_to: str | None,
    _respect_terminal: bool = True,
) -> TransitionDecision:
    """Evaluate the frozen Section 11.6 matrix in deterministic priority order."""

    if latest is not None and prior_substantive is not None:
        prior_status = prior_substantive["status"]
        prior_confidence = prior_substantive["confidence"]
    else:
        prior_status = None
        prior_confidence = None

    if prior_status == "rejected" and _respect_terminal:
        underlying = _transition(
            current=current,
            latest=latest,
            prior_substantive=prior_substantive,
            lineage=lineage,
            compatible=compatible,
            explicit=explicit,
            dormancy_reason=dormancy_reason,
            range_from=range_from,
            range_to=range_to,
            _respect_terminal=False,
        )
        return TransitionDecision(
            underlying.evidence_class,
            "rejected",
            "insufficient",
            underlying.change_reason,
            False,
            underlying.comparison_evaluation_id,
            underlying.compatible_with_prior,
            underlying.new_eligible_observations,
            underlying.evidence_kinds,
            {
                **underlying.conditions,
                "rejected_terminal": True,
                "would_status": underlying.status,
                "would_confidence": underlying.confidence,
                "would_transition": underlying.transition_applied,
            },
        )

    if prior_substantive is not None and not compatible:
        kinds = (
            ()
            if current is None
            else _evidence_kinds(
                evidence_class="incompatible_version",
                same_direction=False,
                confounder_sensitive=current["confounder_sensitive"],
            )
        )
        return TransitionDecision(
            "incompatible_version",
            prior_status,
            prior_confidence,
            "semantic_version_incompatible",
            False,
            prior_substantive["id"],
            False,
            0,
            kinds,
            {
                "compatibility_proof": False,
                "finding_available": current is not None,
            },
        )

    if current is None:
        if dormancy_reason not in {
            "dormant_no_eligible_data",
            "dormant_stale_prerequisite",
        }:
            raise _error("ledger_invariant", "missing finding requires a dormancy reason")
        if prior_substantive is None:
            raise _error("ledger_invariant", "a hypothesis cannot begin dormant")
        return TransitionDecision(
            dormancy_reason,
            "dormant",
            "insufficient",
            dormancy_reason,
            True,
            prior_substantive["id"],
            compatible,
            0,
            (),
            {"reason": dormancy_reason},
        )

    if prior_substantive is None:
        if current["quality"] == "exploratory_screen" and explicit:
            evidence_class, status = "initial_exploratory", "exploratory"
        elif current["discovery_pass"]:
            evidence_class, status = "initial_discovery_pass", "candidate"
        else:
            raise _error(
                "finding_not_eligible",
                "initial finding is not eligible for the requested transition",
                validation=True,
            )
        kinds = _evidence_kinds(
            evidence_class=evidence_class,
            same_direction=True,
            confounder_sensitive=current["confounder_sensitive"],
            weak_or_unstable=current["screen"],
        )
        return TransitionDecision(
            evidence_class,
            status,
            "low",
            evidence_class,
            True,
            None,
            True,
            current["eligible_n"],
            kinds,
            {"initial": True, "explicit": explicit},
        )

    discovery = _latest_discovery(lineage)
    if discovery is None and current["discovery_pass"]:
        comparison = lineage[-1]
        new_n = _new_eligible_observations(
            {**current, "range_from": range_from, "range_to": range_to},
            lineage,
        )
        comparison_direction = _eval_direction(comparison)
        opposite_screen = _is_opposite_direction(
            current["direction"], comparison_direction,
        )
        if opposite_screen:
            excludes_zero = _interval_excludes_zero(current["interval"])
            evidence_class = (
                "opposite_pass" if excludes_zero else "same_exploratory"
            )
            kinds = _evidence_kinds(
                evidence_class=evidence_class,
                same_direction=False,
                confounder_sensitive=current["confounder_sensitive"],
                weak_or_unstable=not excludes_zero,
            )
            return TransitionDecision(
                evidence_class,
                "rejected" if excludes_zero else prior_status,
                "insufficient" if excludes_zero else prior_confidence,
                (
                    "significantly_opposite_pass"
                    if excludes_zero
                    else "exploratory_opposite_caveat"
                ),
                excludes_zero,
                comparison["id"],
                True,
                new_n,
                kinds,
                {
                    "opposite_direction": True,
                    "ci_excludes_zero": excludes_zero,
                    "discovery_pass": True,
                    "prior_was_screen_only": True,
                },
            )
        kinds = _evidence_kinds(
            evidence_class="initial_discovery_pass",
            same_direction=True,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "initial_discovery_pass",
            "candidate",
            "low",
            "first_discovery_after_exploratory_screen",
            True,
            comparison["id"],
            True,
            new_n,
            kinds,
            {
                "first_compatible_discovery": True,
                "prior_was_screen_only": True,
            },
        )

    coverage_anchor = _latest_coverage_anchor(lineage)
    comparison = discovery or coverage_anchor or lineage[-1]
    range_comparison = coverage_anchor or comparison
    comparison_direction = _eval_direction(comparison)
    same_direction = _is_same_direction(current["direction"], comparison_direction)
    opposite = _is_opposite_direction(current["direction"], comparison_direction)
    new_n = _new_eligible_observations(
        {**current, "range_from": range_from, "range_to": range_to},
        lineage,
    )
    disjoint = _later_disjoint(range_from, range_comparison["range_to"])
    overlapping_references = [
        item
        for item in lineage
        if item.get("finding_id") is not None
        and _overlaps(
            range_from,
            range_to,
            item.get("range_from"),
            item.get("range_to"),
        )
    ]
    if discovery is not None and discovery in overlapping_references:
        overlap_reference = discovery
    elif overlapping_references:
        overlap_reference = max(
            overlapping_references,
            key=lambda item: (item["tested_at"], item["id"]),
        )
    else:
        overlap_reference = None
    overlap = overlap_reference is not None
    reference_magnitude = (
        _eval_magnitude(discovery) if discovery is not None else None
    )
    weaker = (
        current["base_pass"]
        and current["magnitude"] is not None
        and reference_magnitude is not None
        and current["magnitude"] < 0.5 * reference_magnitude
    )
    interval_zero = current["base_pass"] and _interval_includes_zero(
        current["interval"]
    )

    if (
        discovery is not None
        and
        opposite
        and current["discovery_pass"]
        and _interval_excludes_zero(current["interval"])
    ):
        evidence_class = "opposite_pass"
        kinds = _evidence_kinds(
            evidence_class=evidence_class,
            same_direction=False,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            evidence_class,
            "rejected",
            "insufficient",
            "significantly_opposite_pass",
            True,
            comparison["id"],
            True,
            new_n,
            kinds,
            {
                "opposite_direction": True,
                "ci_excludes_zero": True,
                "discovery_pass": True,
            },
        )

    null_held_out = (
        discovery is not None
        and
        disjoint
        and current["base_pass"]
        and (not current["discovery_pass"])
        and (weaker or interval_zero or current["quality"] == "insufficient")
    )
    if null_held_out:
        prior_null = _prior_null_without_reset(
            lineage,
            after_id=discovery["id"] if discovery is not None else None,
        )
        second = (
            prior_null is not None
            and _later_disjoint(range_from, prior_null["range_to"])
        )
        status = "rejected" if second else "weakened"
        confidence = "insufficient" if second else "low"
        reason = "second_consecutive_nonoverlap_null" if second else "first_nonoverlap_null"
        kinds = _evidence_kinds(
            evidence_class="null_nonoverlap",
            same_direction=False,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "null_nonoverlap",
            status,
            confidence,
            reason,
            True,
            prior_null["id"] if second else range_comparison["id"],
            True,
            new_n,
            kinds,
            {
                "consecutive_null_count": 2 if second else 1,
                "mutually_disjoint": bool(second),
                "prior_null_evaluation_id": (
                    prior_null["id"] if prior_null is not None else None
                ),
            },
        )

    if current["discovery_pass"] and same_direction:
        if new_n > 0 and disjoint:
            evidence_class = "same_pass_nonoverlap"
            confidence = "moderate" if current["confounder_sensitive"] else "high"
            kinds = _evidence_kinds(
                evidence_class=evidence_class,
                same_direction=True,
                confounder_sensitive=current["confounder_sensitive"],
            )
            return TransitionDecision(
                evidence_class,
                "replicated",
                confidence,
                "later_nonoverlap_replication",
                True,
                range_comparison["id"],
                True,
                new_n,
                kinds,
                {"later": True, "disjoint": True},
            )
        if new_n > 0 and overlap:
            status = "replicated" if prior_status == "replicated" else "strengthening"
            confidence = prior_confidence if status == "replicated" else "moderate"
            kinds = _evidence_kinds(
                evidence_class="same_pass_overlap",
                same_direction=True,
                confounder_sensitive=current["confounder_sensitive"],
            )
            return TransitionDecision(
                "same_pass_overlap",
                status,
                confidence,
                "same_direction_overlap_new_observations",
                True,
                overlap_reference["id"],
                True,
                new_n,
                kinds,
                {"overlap": True, "new_eligible_observations": new_n},
            )
        kinds = _evidence_kinds(
            evidence_class="same_exploratory",
            same_direction=True,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "same_exploratory",
            prior_status,
            prior_confidence,
            "same_pass_without_new_eligible_observations",
            False,
            range_comparison["id"],
            True,
            new_n,
            kinds,
            {"new_eligible_observations": new_n},
        )

    if discovery is not None and current["base_pass"] and (weaker or interval_zero):
        kinds = _evidence_kinds(
            evidence_class="weakening_window",
            same_direction=same_direction,
            confounder_sensitive=current["confounder_sensitive"],
        )
        return TransitionDecision(
            "weakening_window",
            "weakened",
            "low",
            "effect_below_half_reference" if weaker else "interval_includes_zero",
            True,
            comparison["id"],
            True,
            new_n,
            kinds,
            {
                "weaker_than_half": weaker,
                "interval_includes_zero": interval_zero,
                "reference_magnitude": reference_magnitude,
            },
        )

    # A q/split failure, or an exploratory opposite signal that does not pass
    # reversal gates, is recorded as a caveat without changing the last
    # substantive conclusion.
    kinds = _evidence_kinds(
        evidence_class="same_exploratory",
        same_direction=same_direction,
        confounder_sensitive=current["confounder_sensitive"],
        weak_or_unstable=True,
    )
    return TransitionDecision(
        "same_exploratory",
        prior_status,
        prior_confidence,
        "exploratory_caveat_only",
        False,
        comparison["id"],
        True,
        new_n,
        kinds,
        {
            "same_direction": same_direction,
            "opposite_direction": opposite,
            "discovery_pass": current["discovery_pass"],
        },
    )


def _evaluation_semantics(evaluation: Mapping[str, Any]) -> dict[str, str]:
    provenance = evaluation.get("effect_summary", {}).get("provenance")
    if not isinstance(provenance, Mapping):
        raise _error("ledger_corrupt", "evaluation semantic provenance is absent")
    analysis_version = provenance.get("analysis_version")
    registry_version = provenance.get("registry_version")
    engine_hash = provenance.get("engine_sha256")
    registry_hash = provenance.get("registry_sha256")
    if analysis_version != evaluation.get("source_analysis_version"):
        raise _error(
            "ledger_corrupt",
            "evaluation analysis-version provenance is inconsistent",
        )
    for value, name in (
        (analysis_version, "analysis_version"),
        (registry_version, "registry_version"),
    ):
        if not isinstance(value, str) or not value:
            raise _error("ledger_corrupt", f"evaluation {name} is absent")
    _sha(engine_hash, "evaluation.engine_sha256")
    _sha(registry_hash, "evaluation.registry_sha256")
    return {
        "analysis_version": analysis_version,
        "registry_version": registry_version,
        "engine_sha256": engine_hash,
        "registry_sha256": registry_hash,
    }


def _run_semantics(run: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in (
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
    ):
        value = run.get(field)
        if field.endswith("_sha256"):
            result[field] = _sha(value, f"run.{field}")
        elif not isinstance(value, str) or not value:
            raise _error("ledger_invariant", f"run {field} is absent")
        else:
            result[field] = value
    return result


def _semantic_compatibility(
    prior: Mapping[str, Any],
    *,
    current: Mapping[str, str],
    proofs: Mapping[tuple[str, str, str, str], Mapping[str, str]],
) -> tuple[bool, Mapping[str, str] | None, dict[str, Any]]:
    before = _evaluation_semantics(prior)
    same = (
        before["analysis_version"] == current["analysis_version"]
        and before["registry_version"] == current["registry_version"]
    )
    key = (
        before["analysis_version"],
        before["registry_version"],
        current["analysis_version"],
        current["registry_version"],
    )
    proof = None if same else proofs.get(key)
    compatible = same or proof is not None
    audit = {
        "from": before,
        "to": dict(current),
        "semantic_versions_equal": same,
        "proof": dict(proof) if proof is not None else None,
        "compatible": compatible,
    }
    return compatible, proof, audit
