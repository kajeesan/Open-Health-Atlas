"""Frozen sample, variation, missingness, effect-tier, and split gates."""

from __future__ import annotations

from datetime import date, timedelta
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hermes_insights.associations import _base_gate, _quality, _stability


def _row(index: int, exposure: float, outcome: float):
    return {
        "unit_key": f"{index:03d}",
        "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
        "exposure": exposure,
        "outcome": outcome,
        "outcome_raw": outcome,
        "exposure_states": ["observed"],
    }


def _candidate(rows, *, eligible=None):
    eligible = len(rows) if eligible is None else eligible
    return {
        "rows": rows,
        "eligible_n": eligible,
        "complete_n": len(rows),
        "missing_n": eligible - len(rows),
        "missing_rate": (eligible - len(rows)) / eligible,
        "definition": SimpleNamespace(
            value_kind="binary",
            zero_semantics="valid",
        ),
    }


def _binary_half(start: int, *, reverse: bool = False):
    rows = []
    for offset in range(40):
        exposure = float(offset < 20)
        positive = (offset < 15) if exposure else (20 <= offset < 25)
        outcome = float(not positive if reverse else positive)
        rows.append(_row(start + offset, exposure, outcome))
    return rows


def test_chronological_halves_same_sign_and_magnitude_are_stable():
    rows = _binary_half(0) + _binary_half(40)
    result = _stability(rows, "risk_difference", 0.5)
    assert result == {
        "status": "stable",
        "full": 0.5,
        "first_half": 0.5,
        "second_half": 0.5,
    }


def test_direction_flip_is_unstable_and_never_replication():
    rows = _binary_half(0) + _binary_half(40, reverse=True)
    result = _stability(rows, "risk_difference", 0.1)
    assert result["status"] == "unstable"
    assert result["first_half"] > 0 and result["second_half"] < 0
    assert _quality(
        effect_pass=True, q_pass=True, stability_pass=False
    )["tier"] == "exploratory_screen"


def test_halves_require_n15_and_binary_cells5():
    too_short = [_row(index, float(index % 2), float(index % 2)) for index in range(29)]
    assert _stability(too_short, "risk_difference", 1.0)["status"] == "insufficient"
    sparse = []
    for index in range(40):
        sparse.append(_row(index, float(index in {0, 20}), float(index in {0, 20})))
    assert _stability(sparse, "risk_difference", 1.0)["status"] == "insufficient"


def test_odd_split_partitions_every_unit_into_floor_and_ceiling_halves():
    rows = _binary_half(0) + [_row(40, 0.0, 0.0)] + _binary_half(41)
    result = _stability(rows, "risk_difference", 0.5)
    assert result["status"] == "stable"
    assert result["first_half"] == 0.5
    assert result["second_half"] == 0.5119047619047619


def test_missingness_boundary_and_aligned_minimum():
    rows = [_row(index, float(index % 2), float(index % 3 == 0)) for index in range(30)]
    exactly_half = _candidate(rows, eligible=60)
    assert _base_gate(exactly_half, "green-vs-non-green", 30) is None
    over_half = _candidate(rows, eligible=61)
    assert _base_gate(over_half, "green-vs-non-green", 30) == "high_missingness"
    assert _base_gate(_candidate(rows[:29]), "green-vs-non-green", 30) == "aligned_n"


def test_day_rating_class_gates_keep_all_three_levels():
    raw = [1.0] * 5 + [2.0] * 5 + [3.0] * 20
    rows = [
        {**_row(index, float(index % 2), value), "outcome_raw": value, "outcome": value}
        for index, value in enumerate(raw)
    ]
    assert _base_gate(
        _candidate(rows), "ordinal", 30, "subjective.day_rating"
    ) is None
    rows[4]["outcome_raw"] = rows[4]["outcome"] = 2.0
    assert _base_gate(
        _candidate(rows), "ordinal", 30, "subjective.day_rating"
    ) == "outcome_class_gate"


def test_continuous_exposure_needs_five_distinct_and_nonzero_iqr():
    outcome = [float(index % 3 + 1) for index in range(30)]
    four = [_row(index, float(index % 4), outcome[index]) for index in range(30)]
    assert _base_gate(_candidate(four), "ordinal", 30) == "exposure_variation"
    five_but_flat_iqr = [
        _row(index, float(index if index < 4 else 4), outcome[index])
        for index in range(30)
    ]
    assert _base_gate(
        _candidate(five_but_flat_iqr), "ordinal", 30
    ) == "exposure_variation"
    varied = [_row(index, float(index % 6), outcome[index]) for index in range(30)]
    assert _base_gate(_candidate(varied), "ordinal", 30) is None


def test_phase4_quality_tiers_never_manufacture_replication():
    assert _quality(
        effect_pass=False, q_pass=True, stability_pass=True
    ) == {"tier": "insufficient", "eligible_for_hypothesis": False}
    assert _quality(
        effect_pass=True, q_pass=False, stability_pass=True
    ) == {"tier": "exploratory_screen", "eligible_for_hypothesis": True}
    assert _quality(
        effect_pass=True, q_pass=True, stability_pass=True
    ) == {"tier": "exploratory_unreplicated", "eligible_for_hypothesis": True}
