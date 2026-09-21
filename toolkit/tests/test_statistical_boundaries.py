"""Inclusive statistical gates and finite numerical boundary regressions."""

from dataclasses import replace
from datetime import date, timedelta
import math
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

import health
from hermes_insights.associations import analyze_outcome
from hermes_insights.contracts import DateRange
from hermes_insights.interactions import analyze_pairwise
from hermes_insights.registry import build_registry
from hermes_insights.stats import percentile, wilson_interval


def test_wilson_accepts_the_largest_finite_confidence_below_one():
    lower, upper = wilson_interval(1, 2, math.nextafter(1.0, 0.0))

    assert 0 < lower < 0.5 < upper < 1
    assert lower + upper == pytest.approx(1)


@pytest.mark.parametrize(
    ("probability", "expected"), [(0.25, -5e307), (0.5, 0), (0.75, 5e307)],
)
def test_percentile_interpolates_finite_values_without_intermediate_overflow(
    probability, expected,
):
    result = percentile([-1e308, 1e308], probability)

    assert result == pytest.approx(expected)


@pytest.fixture()
def binary_association():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        (Path(__file__).resolve().parents[1] / "SCHEMA.sql").read_text()
    )
    runtime = health._phase3_context()
    context = replace(runtime, today=date(2026, 4, 1), functions={
        **runtime.functions,
        "nutrition_micro_targets": lambda: [
            {"nutrient": "magnesium", "target": 300, "source": "fictional test target"}
        ],
    })
    definitions = [
        item for item in build_registry(connection, context)
        if item.key in {"subjective.day_rating", "nutrition.nutrient.magnesium.target_met"}
    ]
    try:
        yield connection, context, definitions
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("exposed_positive", "unexposed_positive", "passes"),
    [(36, 32, True), (35, 32, False), (32, 36, True), (32, 35, False)],
)
def test_binary_effect_gate_includes_exactly_ten_percentage_points(
    binary_association, exposed_positive, unexposed_positive, passes,
):
    connection, context, definitions = binary_association
    start = date(2026, 1, 1)
    observations = (
        [(1, int(index < exposed_positive)) for index in range(40)]
        + [(0, int(index < unexposed_positive)) for index in range(40)]
    )
    for index, (exposed, positive) in enumerate(observations):
        day = (start + timedelta(days=index)).isoformat()
        connection.execute(
            "INSERT INTO subjective_daily(date,day_rating,source) VALUES(?,?,?)",
            (day, 3 if positive else 1, "manual"),
        )
        connection.execute(
            "INSERT INTO nutrient_daily(date,nutrient,amount,unit,source) "
            "VALUES(?,'magnesium',?,'mg','cronometer')", (day, 300 if exposed else 0),
        )

    result = analyze_outcome(
        connection, definitions,
        DateRange(start, start + timedelta(days=len(observations) - 1)), context,
        outcome_key="subjective.day_rating", mode="green-vs-non-green",
        attach_intervals=False, top=100,
    )
    matching = [finding for finding in result["findings"]
                if finding["exposure"]["components"][0]["lag_days"] == 0
                and finding["exposure"]["components"][0]["window_days"] == 1
                and finding["exposure"]["components"][0]["transform"] == "point"]
    assert len(matching) == 1, result["suppression_counts"]
    assert matching[0]["effect"]["method"] == "risk_difference"
    finding = matching[0]
    evidence_for = {item["code"] for item in finding["evidence_for"]}
    evidence_against = {item["code"] for item in finding["evidence_against"]}

    # 36/40 - 32/40 is exactly .10. Both outcome classes clear the sample gate.
    assert ("effect_gate_pass" in evidence_for) is passes
    assert ("effect_below_threshold" in evidence_against) is not passes


@pytest.mark.parametrize(
    ("cell_sizes", "other_positives", "both_positive", "suppressed"),
    [pytest.param((20, 20, 20, 20), (4, 16, 12), 18, 0, id="equal-cells-exact-threshold"),
     pytest.param((20, 20, 20, 20), (4, 16, 12), 17, 1, id="equal-cells-below-threshold"),
     pytest.param((20, 40, 20, 30), (4, 28, 16), 27, 0, id="unequal-cells-exact-threshold"),
     pytest.param((20, 40, 20, 30), (4, 28, 16), 24, 1, id="unequal-cells-zero-increment")],
)
def test_interaction_effect_gate_includes_exactly_ten_percentage_points(
    cell_sizes, other_positives, both_positive, suppressed,
):
    # In unequal cells B has the higher rate (16/20 versus 28/40), even
    # though A has more positive outcomes. The gate must compare rates.
    singles = []
    for column, key in enumerate(("event.a.occurred", "event.b.occurred")):
        rows = []
        cells = ((0, 0, other_positives[0]), (1, 0, other_positives[1]),
                 (0, 1, other_positives[2]), (1, 1, both_positive))
        for (a, b, positives), cell_size in zip(cells, cell_sizes):
            for offset in range(cell_size):
                index = len(rows)
                day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
                rows.append({
                    "unit_key": str(index), "date": day,
                    "exposure": (a, b)[column], "outcome": int(offset < positives),
                    "outcome_raw": int(offset < positives), "source_era": "synthetic",
                    "outcome_observations": [], "exposure_observations": [],
                })
        singles.append({
            "outcome": {"key": "subjective.day_rating", "mode": "green-vs-non-green"},
            "exposure": {"components": [{
                "exposure_key": key, "lag_days": 0, "window_days": 1, "transform": "point",
            }]},
            "sample": {"outcome_positive_n": sum(other_positives) + both_positive,
                       "outcome_negative_n": sum(cell_sizes) - sum(other_positives) - both_positive},
            "effect": {"oriented_estimate": 0.3}, "testing": {"q": 0.01},
            "quality": {"tier": "exploratory_unreplicated"},
            "_definition": SimpleNamespace(key=key, actionability="direct", preferred_rank=1),
            "_rows": rows, "_base_pass": True, "_effect_pass": True,
            "_q_pass": True, "_binary_exposure": True, "_orientation": 1,
        })

    _, suppression, families = analyze_pairwise(
        outcome=SimpleNamespace(key="subjective.day_rating"),
        modes=["green-vs-non-green"], singles=singles,
        analysis_range={"kind": "bounded", "from": "2026-01-01", "to": "2026-04-20"},
        baseline_range={"kind": "bounded", "from": "2026-01-01", "to": "2026-04-20"},
        input_fingerprint_value="sha256:" + "a" * 64,
        provenance={},
    )

    assert families["green-vs-non-green"] == 1
    assert suppression.get("interaction_increment_effect", 0) == suppressed
