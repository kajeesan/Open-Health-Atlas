"""Multiplicity, deterministic ranking, and redundancy suppression."""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hermes_insights.associations import _rank_key, _redundancy_filter
from hermes_insights.stats import benjamini_hochberg


def test_bh_family_keeps_nonsignificant_candidates_and_restores_order():
    p_values = [0.001, 0.92, 0.04, 0.51, 0.02]
    q_values = benjamini_hochberg(p_values)
    assert len(q_values) == len(p_values)
    assert q_values[1] == pytest.approx(0.92)
    assert q_values[3] > 0.10
    assert q_values[0] == 0.005


def _candidate(
    key: str,
    values,
    *,
    group="equivalent",
    actionability="direct",
    preferred_rank=1,
):
    definition = SimpleNamespace(
        key=key,
        redundancy_group=group,
        actionability=actionability,
        preferred_rank=preferred_rank,
    )
    return {
        "definition": definition,
        "component": {
            "exposure_key": key,
            "lag_days": 1,
            "window_days": 1,
            "transform": "point",
        },
        "rows": [
            {"unit_key": f"{index:03d}", "exposure": float(value)}
            for index, value in enumerate(values)
        ],
    }


def test_redundancy_is_greedy_with_actionability_then_rank_then_key():
    values = list(range(30))
    candidates = [
        _candidate("z.low", values, actionability="context", preferred_rank=1),
        _candidate("b.rank", values, actionability="direct", preferred_rank=2),
        _candidate("a.winner", values, actionability="direct", preferred_rank=1),
    ]
    suppression = __import__("collections").Counter()
    retained = _redundancy_filter(candidates, suppression)
    assert [item["definition"].key for item in retained] == ["a.winner"]
    assert suppression["redundant_equivalent"] == 2


def test_redundancy_spans_transforms_but_requires_same_group_and_n30():
    values = list(range(29))
    left = _candidate("a", values, group="one")
    right = _candidate("b", values, group="one")
    third = _candidate("c", list(range(30)), group="two")
    suppression = __import__("collections").Counter()
    retained = _redundancy_filter([left, right, third], suppression)
    assert {item["definition"].key for item in retained} == {"a", "b", "c"}
    assert not suppression
    right["rows"].append({"unit_key": "029", "exposure": 29.0})
    left["rows"].append({"unit_key": "029", "exposure": 29.0})
    right["component"]["transform"] = "mean"
    retained = _redundancy_filter([left, right], suppression)
    assert len(retained) == 1
    assert suppression["redundant_equivalent"] == 1


def _finding(
    tier,
    q,
    effect,
    key,
    *,
    actionability="direct",
    preferred_rank=1,
):
    definition = SimpleNamespace(
        actionability=actionability,
        preferred_rank=preferred_rank,
    )
    return {
        "quality": {"tier": tier},
        "testing": {"q": q},
        "effect": {"oriented_estimate": effect},
        "exposure": {"components": [{
            "exposure_key": key,
            "lag_days": 1,
            "window_days": 1,
            "transform": "point",
        }]},
        "_definition": definition,
    }


def test_rank_ladder_is_tier_q_effect_actionability_rank_component():
    findings = [
        _finding("exploratory_screen", 0.001, 0.9, "screen"),
        _finding("exploratory_unreplicated", 0.08, 0.2, "weak"),
        _finding("exploratory_unreplicated", 0.01, 0.3, "winner"),
        _finding("insufficient", None, None, "insufficient"),
    ]
    ordered = sorted(findings, key=_rank_key)
    assert [item["exposure"]["components"][0]["exposure_key"] for item in ordered] == [
        "winner", "weak", "screen", "insufficient"
    ]
