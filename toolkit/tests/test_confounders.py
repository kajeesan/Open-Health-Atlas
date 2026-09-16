"""Descriptive confounder sensitivity paths and exact stratum gates."""

from __future__ import annotations

from datetime import date, timedelta
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hermes_insights.associations import (
    _confounder_labels,
    _confounders,
    _stratified_effect,
)


def _observation(key, day, value):
    return {
        "feature_key": key,
        "observed_at": day,
        "value": value,
        "state": "observed",
        "source": "fixture",
        "provenance": {
            "adapter": "events",
            "table": "fixture",
            "natural_key": f"fixture:{key}:{day}",
        },
    }


def _rows_and_observations():
    rows = []
    observations = {
        "event.illness.occurred": {},
        "event.travel.occurred": {},
        "event.training_phase.occurred": {},
        "medication.i_" + "a" * 64 + ".regime": {},
    }
    for group in range(2):
        start = date(2026, 1, 1) if group == 0 else date(2026, 4, 1)
        for offset in range(40):
            index = group * 40 + offset
            day = (start + timedelta(days=offset)).isoformat()
            exposure = float(offset < 20)
            if group == 0:
                # RD +0.5: exposed 15/20, unexposed 5/20.
                outcome = float((offset < 15) or (20 <= offset < 25))
            else:
                # RD -0.5: exposed 5/20, unexposed 15/20.
                outcome = float((offset < 5) or (20 <= offset < 35))
            rows.append({
                "unit_key": f"{index:03d}",
                "date": day,
                "exposure": exposure,
                "outcome": outcome,
                "source_era": f"era-{group}",
            })
            values = {
                "event.illness.occurred": group,
                "event.travel.occurred": 1 - group,
                "event.training_phase.occurred": group,
                "medication.i_" + "a" * 64 + ".regime": f"regime-{group}",
            }
            for key, value in values.items():
                observations[key][day] = [_observation(key, day, value)]
    return rows, observations


def test_all_eight_registered_confounder_paths_are_checked():
    rows, observations = _rows_and_observations()
    labels = _confounder_labels(rows, observations)
    assert set(labels) == {
        "weekend",
        "calendar_quarter",
        "illness",
        "travel",
        "medication_regime",
        "training_phase",
        "source_era",
        "source_transition",
    }
    assert set(labels["calendar_quarter"]) == {"Q1", "Q2"}
    assert set(labels["source_transition"]) == {
        "source_segment_0", "source_segment_1"
    }
    assert set(labels["medication_regime"]) == {"regime-0", "regime-1"}
    result = _confounders(
        rows,
        observations,
        "risk_difference",
        oriented_effect=0.5,
        orientation=1,
    )
    assert {item["key"] for item in result["checked"]} == set(labels)
    assert result["unchecked"] == []
    # Each constructed two-group confounder erases the unstratified magnitude.
    assert {
        "calendar_quarter", "illness", "travel", "medication_regime",
        "training_phase", "source_era", "source_transition",
    } <= set(result["sensitive_to"])


def test_confounder_requires_two_strata_and_ten_per_stratum():
    rows, _ = _rows_and_observations()
    one_stratum = ["one"] * len(rows)
    effect, counts = _stratified_effect(rows, one_stratum, "risk_difference")
    assert effect is None and counts == {"one": 80}
    sparse = ["small"] * 9 + ["large"] * 71
    effect, counts = _stratified_effect(rows, sparse, "risk_difference")
    assert effect is None and counts["small"] == 9


def test_unchecked_confounders_always_include_a_reason():
    rows, _ = _rows_and_observations()
    result = _confounders(
        rows[:20],
        {},
        "risk_difference",
        oriented_effect=0.5,
        orientation=1,
    )
    assert result["unchecked"]
    assert all(set(item) == {"key", "reason"} for item in result["unchecked"])
    assert {
        "illness", "travel", "medication_regime", "training_phase"
    } <= {item["key"] for item in result["unchecked"]}
