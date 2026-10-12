"""Fictional fitness setup qualification through retained analytical surfaces."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from hermes_insights import associations, frame, readiness, registry, runtime
from hermes_insights.contracts import DateRange


@pytest.fixture
def fitness_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((Path(__file__).resolve().parents[1] / "SCHEMA.sql").read_text())
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def context():
    return runtime.adapter_context(
        clock=lambda: datetime(2026, 7, 10, 12, tzinfo=timezone.utc), timezone="UTC",
    )


def seed_protocols(conn, protocols):
    for offset, protocol in enumerate(protocols):
        day = (date(2026, 6, 1) + timedelta(days=offset)).isoformat()
        for movement, side, load in (
            ("leg-extension", "left", 40 + offset),
            ("leg-extension", "right", 50 + offset),
            ("leg-curl", "left", 30),
            ("leg-curl", "right", 35),
        ):
            conn.execute(
                "INSERT INTO fitness_tests(date,movement,side,load_kg,reps,equipment_note) "
                "VALUES(?,?,?,?,7,?)", (day, movement, side, load, protocol),
            )


@pytest.mark.parametrize("protocol", ["fixture-machine", " bodyweight ", "no equipment"])
def test_explicit_setup_retains_eligible_measurements(fitness_db, context, protocol):
    seed_protocols(fitness_db, [protocol] * 40)
    definitions = registry.build_registry(fitness_db, context, family="fitness")
    window = DateRange(date(2026, 6, 1), date(2026, 7, 10))
    outcome = "fitness.test.leg-extension.left.value"

    result = associations.analyze_outcome(
        fitness_db, definitions, window, context, outcome_key=outcome, attach_intervals=False,
    )

    assert result["coverage"]["outcome_eligible_n"] == 40
    row = next(item for item in result["readiness"]["features"] if item["feature_key"] == outcome)
    assert row["aligned_n"] == 40
    assert row["state"] == "sufficient"


@pytest.mark.parametrize("protocol", [None, " \t "])
@pytest.mark.parametrize("outcome", [
    "fitness.test.leg-extension.left.value",
    "fitness.test.leg-extension.side_gap",
    "fitness.ratio.hq.side_gap",
])
def test_unknown_setup_cannot_pass_analysis_gates(fitness_db, context, protocol, outcome):
    seed_protocols(fitness_db, [protocol] * 40)
    definitions = registry.build_registry(fitness_db, context, family="fitness")
    window = DateRange(date(2026, 6, 1), date(2026, 7, 10))

    result = associations.analyze_outcome(
        fitness_db, definitions, window, context, outcome_key=outcome, attach_intervals=False,
    )

    assert result["coverage"]["outcome_eligible_n"] == 0
    row = next(item for item in result["readiness"]["features"] if item["feature_key"] == outcome)
    assert row["aligned_n"] == 0
    assert row["candidate_qualification"]["reasons"] == ["missing_protocol"]
    assert row["state"] == "too_sparse_for_analysis"


def test_unknown_setup_preserves_descriptive_ratios_without_band_or_change(fitness_db, context):
    seed_protocols(fitness_db, [None, None])
    definitions = registry.build_registry(fitness_db, context, family="fitness")
    window = DateRange(date(2026, 6, 1), date(2026, 6, 2))

    result = frame.build_feature_frame(
        fitness_db, definitions, window, context, include_provenance=True,
    )

    ratios = [row for row in result["observations"]
              if row["feature_key"] == "fitness.ratio.hq.left.value"]
    assert len(ratios) == 2
    assert all(row["state"] == "observed" for row in ratios)
    assert all(row["provenance"]["candidate_eligible"] is False for row in ratios)
    assert all(row["provenance"]["change"] is None for row in ratios)
    assert not [row for row in result["observations"]
                if row["feature_key"] == "fitness.ratio.hq.left.distance_to_band"]


@pytest.mark.parametrize("outcome", [
    "fitness.test.leg-extension.left.value",
    "fitness.test.leg-extension.side_gap",
    "fitness.ratio.hq.side_gap",
])
def test_distinct_known_setups_are_not_pooled(fitness_db, context, outcome):
    seed_protocols(fitness_db, ["fixture-machine-a"] * 20 + ["fixture-machine-b"] * 20)
    definitions = registry.build_registry(fitness_db, context, family="fitness")
    window = DateRange(date(2026, 6, 1), date(2026, 7, 10))

    result = associations.analyze_outcome(
        fitness_db, definitions, window, context, outcome_key=outcome, attach_intervals=False,
    )

    assert result["coverage"]["outcome_eligible_n"] == 0
    row = next(item for item in result["readiness"]["features"] if item["feature_key"] == outcome)
    assert row["aligned_n"] == 0
    assert row["candidate_qualification"]["reasons"] == ["mixed_protocols"]


def test_research_reference_does_not_create_band_observations(fitness_db, context):
    seed_protocols(fitness_db, ["fixture-machine"] * 5)
    definitions = registry.build_registry(fitness_db, context, family="fitness")
    window = DateRange(date(2026, 6, 1), date(2026, 6, 5))

    result = frame.build_feature_frame(
        fitness_db, definitions, window, context, include_provenance=True,
    )

    assert [row for row in result["observations"]
            if row["feature_key"] == "fitness.ratio.hq.left.value"]
    assert not [row for row in result["observations"]
                if row["feature_key"].endswith(".distance_to_band")]


def test_single_setup_range_ignores_other_protocols_outside_range(fitness_db, context):
    seed_protocols(fitness_db, ["fixture-machine-a"] * 10 + ["fixture-machine-b"] * 30)
    definitions = registry.build_registry(fitness_db, context)
    window = DateRange(date(2026, 6, 11), date(2026, 7, 10))

    result = associations.analyze_outcome(
        fitness_db, definitions, window, context,
        outcome_key="fitness.test.leg-extension.left.value", attach_intervals=False,
    )

    assert result["coverage"]["outcome_eligible_n"] == 30
    row = next(item for item in result["readiness"]["features"]
               if item["feature_key"] == "fitness.test.leg-extension.left.value")
    assert row["aligned_n"] == 30
    assert row["state"] == "sufficient"


def test_known_observations_remain_usable_among_unknown_setups(fitness_db, context):
    seed_protocols(fitness_db, ["fixture-machine"] * 35 + [None] * 5)
    definitions = registry.build_registry(fitness_db, context, family="fitness")
    window = DateRange(date(2026, 6, 1), date(2026, 7, 10))
    outcome = "fitness.test.leg-extension.left.value"

    result = associations.analyze_outcome(
        fitness_db, definitions, window, context, outcome_key=outcome, attach_intervals=False,
    )

    assert result["coverage"]["outcome_eligible_n"] == 35
    row = next(item for item in result["readiness"]["features"] if item["feature_key"] == outcome)
    assert row["aligned_n"] == 35
    assert row["candidate_qualification"]["excluded_observations"] == 5


def test_normalized_setup_labels_are_comparable(fitness_db, context):
    seed_protocols(fitness_db, ["Fixture  Machine"] * 20 + [" fixture machine "] * 20)
    definitions = registry.build_registry(fitness_db, context, family="fitness")
    window = DateRange(date(2026, 6, 1), date(2026, 7, 10))
    # Tested ratios carry no target band; the ratio side gap is the
    # directional ratio outcome that exercises component-protocol identity.
    outcome = "fitness.ratio.hq.side_gap"

    result = associations.analyze_outcome(
        fitness_db, definitions, window, context, outcome_key=outcome, attach_intervals=False,
    )

    assert result["coverage"]["outcome_eligible_n"] == 40
    row = next(item for item in result["readiness"]["features"] if item["feature_key"] == outcome)
    assert row["aligned_n"] == 40


@pytest.mark.parametrize("protocol,expected_scores", [("bodyweight, no equipment", [75]), (None, [])])
def test_target_scores_require_recorded_setup(fitness_db, context, protocol, expected_scores):
    fitness_db.executemany(
        "INSERT INTO fitness_tests(date,movement,side,seconds,equipment_note) "
        "VALUES('2026-07-01','balance-stand',?,?,?)",
        [("left", 45, protocol), ("right", 60, protocol)],
    )
    fitness_db.execute("INSERT INTO athletic_targets(axis,lift,target) VALUES('balance','',60)")
    definitions = registry.build_registry(fitness_db, context, family="fitness")

    result = frame.build_feature_frame(
        fitness_db, definitions, DateRange(date(2026, 7, 1), date(2026, 7, 1)), context,
    )

    assert [row["value"] for row in result["observations"]
            if row["feature_key"] == "fitness.athletic_axis.balance.score"] == expected_scores


@pytest.mark.parametrize("range_start,expected_aligned", [(date(2026, 1, 1), 30), (date(2026, 7, 1), 0)])
def test_unknown_setup_does_not_refresh_old_qualified_measurements(
    fitness_db, context, range_start, expected_aligned,
):
    fitness_db.executemany(
        "INSERT INTO fitness_tests(date,movement,side,load_kg,reps,equipment_note) "
        "VALUES(?,'leg-extension','left',?,7,'fixture-machine')",
        [((date(2026, 1, 1) + timedelta(days=offset)).isoformat(), 40 + offset)
         for offset in range(30)],
    )
    fitness_db.execute(
        "INSERT INTO fitness_tests(date,movement,side,load_kg,reps) "
        "VALUES('2026-07-10','leg-extension','left',80,7)"
    )
    definitions = registry.build_registry(fitness_db, context, family="fitness")

    result = readiness.build_readiness(
        fitness_db, definitions, DateRange(range_start, date(2026, 7, 10)), context,
    )

    row = next(item for item in result["features"]
               if item["feature_key"] == "fitness.test.leg-extension.left.value")
    assert row["aligned_n"] == expected_aligned
    assert row["latest_at"] == "2026-01-30"
    assert row["state"] == "stale"


def test_only_unknown_setup_has_no_qualified_latest_measurement(fitness_db, context):
    seed_protocols(fitness_db, [None] * 40)
    definitions = registry.build_registry(fitness_db, context, family="fitness")

    result = readiness.build_readiness(
        fitness_db, definitions, DateRange(date(2026, 6, 1), date(2026, 7, 10)), context,
    )

    row = next(item for item in result["features"]
               if item["feature_key"] == "fitness.test.leg-extension.left.value")
    assert row["latest_at"] is None
    assert row["aligned_n"] == 0
    assert row["observations"] == 40
