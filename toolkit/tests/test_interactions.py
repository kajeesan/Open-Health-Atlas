"""Pairwise-only incremental interaction gates and provenance."""

from __future__ import annotations

import pathlib
import sys
import copy
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hermes_insights.interactions import _source_completeness, analyze_pairwise


def _observation(key, index, value):
    return {
        "feature_key": key,
        "observed_at": f"2026-{index // 28 + 1:02d}-{index % 28 + 1:02d}",
        "value": value,
        "state": "observed",
        "source": "fixture",
        "provenance": {
            "adapter": "fixture",
            "table": "fixture_rows",
            "natural_key": f"fixture_rows:{key}:{index}",
        },
    }


def _single(key, rows, *, lag, window, transform, q=0.01, effect_pass=True):
    definition = SimpleNamespace(
        key=key,
        actionability="direct",
        preferred_rank=1,
        redundancy_group=None,
    )
    component = {
        "exposure_key": key,
        "lag_days": lag,
        "window_days": window,
        "transform": transform,
        "temporal_direction": (
            "same_day_or_order_unknown" if lag == 0 else "exposure_precedes_outcome"
        ),
    }
    positives = sum(int(row["outcome"]) for row in rows)
    return {
        "outcome": {"key": "subjective.day_rating", "mode": "green-vs-non-green"},
        "exposure": {"components": [component]},
        "sample": {
            "outcome_positive_n": positives,
            "outcome_negative_n": len(rows) - positives,
        },
        "effect": {"oriented_estimate": 0.3 if effect_pass else 0.01},
        "testing": {"q": q},
        "quality": {"tier": "exploratory_unreplicated" if effect_pass else "insufficient"},
        "_definition": definition,
        "_rows": rows,
        "_base_pass": True,
        "_effect_pass": effect_pass,
        "_q_pass": q <= 0.10,
        "_binary_exposure": True,
        "_orientation": 1,
    }


def _pair_singles(rates, *, per_cell=25, eras=1):
    left_rows, right_rows = [], []
    index = 0
    for a_value, b_value, name in (
        (0, 0, "neither"),
        (1, 0, "a_only"),
        (0, 1, "b_only"),
        (1, 1, "both"),
    ):
        positives = round(rates[name] * per_cell)
        for offset in range(per_cell):
            outcome = int(offset < positives)
            day = f"2026-{index // 28 + 1:02d}-{index % 28 + 1:02d}"
            common = {
                "unit_key": f"{index:03d}",
                "date": day,
                "outcome": outcome,
                "outcome_raw": outcome,
                "source_era": f"era-{(index * eras) // (per_cell * 4)}",
                "outcome_observations": [
                    _observation("subjective.day_rating", index, outcome)
                ],
            }
            left_rows.append({
                **common,
                "exposure": a_value,
                "exposure_observations": [_observation("event.a.occurred", index, a_value)],
            })
            right_rows.append({
                **common,
                "exposure": b_value,
                "exposure_observations": [_observation("event.b.occurred", index, b_value)],
            })
            index += 1
    return (
        _single("event.a.occurred", left_rows, lag=1, window=7, transform="count"),
        _single("event.b.occurred", right_rows, lag=3, window=1, transform="point"),
    )


def _analyze(left, right, *, orientation=1):
    left["_orientation"] = orientation
    right["_orientation"] = orientation
    return analyze_pairwise(
        outcome=SimpleNamespace(key="subjective.day_rating"),
        modes=["green-vs-non-green"],
        singles=[left, right],
        analysis_range={"kind": "bounded", "from": "2026-01-01", "to": "2026-06-01"},
        baseline_range={"kind": "bounded", "from": "2025-12-20", "to": "2026-06-01"},
        input_fingerprint_value="sha256:" + "a" * 64,
        provenance={
            "analysis_version": "outcome-v1",
            "registry_version": "feature-registry-v1",
            "engine_sha256": "sha256:" + "b" * 64,
            "registry_sha256": "sha256:" + "c" * 64,
            "input_fingerprint": "sha256:" + "a" * 64,
        },
    )


def test_pair_source_completeness_includes_outcome_and_both_exposures():
    rows = [{
        "date": "2026-07-10",
        "outcome_observations": [{
            **_observation("subjective.day_rating", 9, 3),
            "observed_at": "2026-07-10",
            "provenance": {
                "adapter": "daily",
                "table": "subjective_daily",
                "natural_key": "subjective_daily:2026-07-10",
                "completeness_revision_ids": [7],
            },
        }],
        "exposure_observations": [
            {
                **_observation("event.a.occurred", 2, 0),
                "observed_at": "2026-07-03",
                "provenance": {
                    "adapter": "events",
                    "table": "capture_completeness_revisions",
                    "natural_key": "capture_completeness_revisions:8",
                },
            },
            {
                **_observation("event.b.occurred", 4, 0),
                "observed_at": "2026-07-05",
                "provenance": {
                    "adapter": "training",
                    "table": "source_sync_runs",
                    "source_sync_run_ids": [9],
                },
            },
        ],
    }]
    assert _source_completeness(rows) == {
        "from": "2026-07-03",
        "to": "2026-07-10",
        "completeness_revision_ids": [7, 8],
        "source_sync_interval_ids": [9],
    }


def test_real_incremental_pair_surfaces_with_independent_components():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.28,
        "b_only": 0.28,
        "both": 0.88,
    }, eras=2)
    findings, suppression, family_sizes = _analyze(left, right)
    assert len(findings) == 1
    finding = findings[0]
    assert len(finding["exposure"]["components"]) == 2
    assert {
        (item["exposure_key"], item["lag_days"], item["window_days"], item["transform"])
        for item in finding["exposure"]["components"]
    } == {
        ("event.a.occurred", 1, 7, "count"),
        ("event.b.occurred", 3, 1, "point"),
    }
    assert "transform" not in finding["exposure"]
    assert finding["effect"]["oriented_estimate"] > 0.50
    assert finding["effect"]["ci95"][0] > 0
    assert finding["testing"]["family_size"] == 1
    assert family_sizes == {"green-vs-non-green": 1}
    assert finding["testing"]["q"] <= 0.05
    assert finding["testing"]["permutation_iterations"] == 2000
    assert finding["testing"]["bootstrap_iterations"] == 2000
    assert finding["rates"]["component_a_difference"] is not None
    assert finding["rates"]["component_b_difference"] is not None
    assert finding["confounders"]["checked"][0]["key"] == "source_era"
    assert not suppression.get("interaction_sparse_cell")


def test_additive_only_pair_is_suppressed_but_remains_in_q_family():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.28,
        "b_only": 0.48,
        "both": 0.52,
    })
    findings, suppression, family_sizes = _analyze(left, right)
    assert findings == []
    assert suppression["interaction_increment_effect"] == 1
    assert family_sizes == {"green-vs-non-green": 1}


def test_sparse_cell_is_suppressed_before_statistical_testing():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.30,
        "b_only": 0.30,
        "both": 0.90,
    }, per_cell=25)
    # Keep total N=85 but reduce the "both" cell from 25 to exactly 10.
    left["_rows"] = left["_rows"][:85]
    right["_rows"] = right["_rows"][:85]
    findings, suppression, family_sizes = _analyze(left, right)
    assert findings == []
    assert suppression["interaction_sparse_cell"] == 1
    assert family_sizes == {"green-vs-non-green": 0}


def test_component_gate_and_binary_only_boundary():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.28,
        "b_only": 0.28,
        "both": 0.88,
    })
    left["_effect_pass"] = False
    left["_q_pass"] = False
    right["_effect_pass"] = False
    right["_q_pass"] = False
    findings, suppression, _ = _analyze(left, right)
    assert findings == []
    assert suppression["interaction_component_gate"] == 1
    left["_binary_exposure"] = False
    findings, suppression, _ = _analyze(left, right)
    assert findings == []
    assert suppression["interaction_ineligible_single"] >= 1


def test_context_only_autoregressive_single_cannot_enter_pair_search():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.28,
        "b_only": 0.28,
        "both": 0.88,
    })
    left["_context_only_lineage"] = True
    findings, suppression, family_sizes = _analyze(left, right)
    assert findings == []
    assert suppression["interaction_ineligible_single"] == 1
    assert family_sizes == {"green-vs-non-green": 0}


def test_triples_are_structurally_impossible():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.28,
        "b_only": 0.28,
        "both": 0.88,
    })
    third = copy.deepcopy(right)
    third["_definition"].key = "event.c.occurred"
    third["exposure"]["components"][0]["exposure_key"] = "event.c.occurred"
    findings, _suppression, families = analyze_pairwise(
        outcome=SimpleNamespace(key="subjective.day_rating"),
        modes=["green-vs-non-green"],
        singles=[left, right, third],
        analysis_range={
            "kind": "bounded", "from": "2026-01-01", "to": "2026-06-01",
        },
        baseline_range={
            "kind": "bounded", "from": "2025-12-20", "to": "2026-06-01",
        },
        input_fingerprint_value="sha256:" + "a" * 64,
        provenance={
            "analysis_version": "outcome-v1",
            "registry_version": "feature-registry-v1",
            "engine_sha256": "sha256:" + "b" * 64,
            "registry_sha256": "sha256:" + "c" * 64,
            "input_fingerprint": "sha256:" + "a" * 64,
        },
    )
    assert all(len(item["exposure"]["components"]) == 2 for item in findings)
    assert families["green-vs-non-green"] == 2


def test_canonical_component_labels_match_a_b_memberships_and_comparisons():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.28,
        "b_only": 0.52,
        "both": 0.92,
    })
    left["_definition"].key = "z.event"
    left["_definition"].preferred_rank = 1
    left["exposure"]["components"][0]["exposure_key"] = "z.event"
    right["_definition"].key = "a.event"
    right["_definition"].preferred_rank = 2
    right["exposure"]["components"][0]["exposure_key"] = "a.event"
    findings, _, _ = _analyze(left, right)
    finding = findings[0]
    assert [
        item["exposure_key"] for item in finding["exposure"]["components"]
    ] == ["a.event", "z.event"]
    assert (
        finding["rates"]["component_a_difference"]
        > finding["rates"]["component_b_difference"]
    )


def test_negative_orientation_emits_matching_raw_and_oriented_intervals():
    left, right = _pair_singles({
        "neither": 0.20,
        "a_only": 0.28,
        "b_only": 0.28,
        "both": 0.88,
    })
    findings, _, _ = _analyze(left, right, orientation=-1)
    effect = findings[0]["effect"]
    assert effect["estimate"] > 0
    assert effect["oriented_estimate"] < 0
    assert effect["ci95"][0] > 0
    assert effect["oriented_ci95"][1] < 0
