"""Fictional sparse-record behavior through the retained local read surface."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from hermes_insights import migrations, runtime
from hermes_insights.hermes_surface import SurfaceError
from hermes_insights.frame import FrameError
from hermes_insights.local_surface import LocalSurface


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "fictional-descriptive.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / "toolkit/SCHEMA.sql").read_text())
    migrations.migrate(str(path), 3, 0, code_version="b" * 40)
    monkeypatch.setattr(runtime, "now", lambda **kwargs: datetime(2026, 7, 1, tzinfo=timezone.utc))
    return path


def _records(database, *, energy, sleep=()):
    with sqlite3.connect(database) as connection:
        connection.executemany("INSERT INTO subjective_daily(date,energy) VALUES(?,?)", energy)
        connection.executemany("INSERT INTO sleep_log(date,time_asleep_hours) VALUES(?,?)", sleep)


def _request(exposures=("sleep.duration_hours",), **overrides):
    return {"operation": "health_describe", "arguments": {
        "outcome_key": "subjective.energy", "exposure_keys": list(exposures),
        "range": {"from": "2026-06-24", "to": "2026-06-30"}, "lag_days": 1,
        **overrides,
    }}


def test_sparse_records_return_actual_pair_counts_below_inference_gates(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)],
             sleep=[("2026-06-24", 5), ("2026-06-26", 9)])

    result = LocalSurface(database).execute(_request())

    assert result["status"] == "ok"
    assert result["findings"] == []
    description = result["facts"][0]
    assert description["eligible_for_hypothesis"] is False
    assert description["inference_status"] == "not_assessed"
    comparison = description["comparisons"][0]
    assert comparison["paired_units"] == 2
    assert comparison["eligible_outcome_units"] == 2
    assert comparison["groups"][0]["outcome"]["n"] == 1
    assert comparison["mean_difference"] == 6
    assert comparison["pattern_status"] == "observed_difference"


def test_unrelated_empty_feature_preserves_other_pairwise_observations(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)],
             sleep=[("2026-06-24", 5), ("2026-06-26", 9)])
    surface = LocalSurface(database)

    alone = surface.execute(_request())["facts"][0]
    extra = surface.execute(_request(("sleep.duration_hours", "wearable.steps")))["facts"][0]

    assert alone["comparisons"][0] == extra["comparisons"][0]
    missing = next(item for item in extra["features"] if item["feature_key"] == "wearable.steps")
    assert missing["observed_units"] == 0
    assert missing["summary"]["mean"] is None


def test_missing_exposure_records_are_unknown_not_recorded_zeros(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)],
             sleep=[("2026-06-24", 5)])

    comparison = LocalSurface(database).execute(_request())["facts"][0]["comparisons"][0]

    assert comparison["paired_units"] == 1
    assert comparison["missing_exposure_units"] == 1
    assert comparison["pattern_status"] == "insufficient_comparison"
    assert comparison["reason"] == "no_exposure_contrast"
    assert comparison["groups"][0]["exposure"]["mean"] == 5


def test_equal_observed_groups_have_no_clear_pattern_not_missing_evidence(database):
    _records(database, energy=[("2026-06-25", 5), ("2026-06-27", 5)],
             sleep=[("2026-06-24", 5), ("2026-06-26", 9)])

    comparison = LocalSurface(database).execute(_request())["facts"][0]["comparisons"][0]

    assert comparison["pattern_status"] == "no_clear_pattern"
    assert comparison["mean_difference"] == 0
    assert comparison["reason"] is None
    assert comparison["paired_units"] == 2


def test_single_observation_remains_available_without_a_comparison(database):
    _records(database, energy=[("2026-06-25", 4)])

    result = LocalSurface(database).execute(_request(()))

    feature = result["facts"][0]["features"][0]
    assert result["status"] == "ok"
    assert feature["summary"]["mean"] == 4
    assert feature["observed_days"] == 1
    assert feature["days_without_usable_observation"] == 6
    assert feature["period_compare"]["status"] == "insufficient_comparison"
    assert feature["period_compare"]["current"]["mean"] is None


def test_evidence_replays_descriptive_sources_without_writing_records(database):
    _records(database, energy=[("2026-06-25", 4)])
    surface = LocalSurface(database)
    before = database.read_bytes()
    result = surface.execute(_request())

    replay = surface.execute({"operation": "health_evidence", "arguments": {
        "evidence_refs": result["evidence_refs"], "detail": "lineage",
    }})

    assert replay["facts"][0]["verified"] is True
    assert replay["facts"][0]["facts"] == result["facts"]
    assert database.read_bytes() == before
    observed = result["facts"][0]["observations"][0]
    assert observed["source_row_ids"]
    assert observed["value"] == 4
    assert result["evidence_refs"][0].get("finding_id") is None


def test_changed_input_invalidates_descriptive_evidence(database):
    _records(database, energy=[("2026-06-25", 4)])
    surface = LocalSurface(database)
    result = surface.execute(_request())
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE subjective_daily SET energy=6")

    with pytest.raises(SurfaceError, match="identity no longer matches"):
        surface.execute({"operation": "health_evidence", "arguments": {
            "evidence_refs": result["evidence_refs"], "detail": "summary",
        }})


def test_shared_parent_features_are_explicitly_not_independent_corroboration(database):
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO daily_metrics(date,source,resting_hr,steps) VALUES(?,?,?,?)",
                           ("2026-06-25", "fictional-device", 60, 5000))

    description = LocalSurface(database).execute(_request(
        ("wearable.steps",), outcome_key="wearable.resting_hr_bpm", lag_days=0,
    ))["facts"][0]

    assert len(description["shared_source_rows"]) == 1
    assert description["shared_source_rows"][0]["independent_corroboration"] is False
    assert description["shared_source_rows"][0]["feature_keys"] == ["wearable.resting_hr_bpm", "wearable.steps"]


def test_source_changes_do_not_create_pooled_exposure_contrasts(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)])
    with sqlite3.connect(database) as connection:
        connection.executemany("INSERT INTO daily_metrics(date,source,steps) VALUES(?,?,?)", [
            ("2026-06-24", "fictional-device-a", 1000),
            ("2026-06-26", "fictional-device-b", 8000),
        ])

    comparison = LocalSurface(database).execute(_request(("wearable.steps",)))["facts"][0]["comparisons"][0]

    assert comparison["paired_units"] == 2
    assert comparison["reason"] == "source_incompatible"
    assert comparison["mean_difference"] is None


def test_another_source_preserves_the_original_compatible_contrast(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)],
             sleep=[("2026-06-24", 5), ("2026-06-26", 9)])
    surface = LocalSurface(database)
    original = surface.execute(_request())["facts"][0]["comparisons"][0]
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO subjective_daily(date,energy) VALUES(?,?)", ("2026-06-29", 1))
        connection.execute("INSERT INTO sleep_log(date,time_asleep_hours,source) VALUES(?,?,?)",
                           ("2026-06-28", 12, "fictional-second-device"))

    comparison = surface.execute(_request())["facts"][0]["comparisons"][0]

    compatible = comparison["source_comparisons"][0]
    assert "source_comparisons" not in original
    assert comparison["reason"] == "source_incompatible"
    assert comparison["mean_difference"] is None
    assert compatible["source_identity"] == {"outcome_sources": ["manual"], "exposure_sources": ["sleep_log"]}
    assert compatible["paired_units"] == original["paired_units"]
    assert compatible["groups"] == original["groups"]
    assert compatible["mean_difference"] == original["mean_difference"]
    assert compatible["counterexamples"] == original["counterexamples"]
    assert compatible["sample"] == original["sample"]
    assert compatible["independent_corroboration"] is False


def test_one_pair_source_stratum_does_not_estimate_a_contrast(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)])
    with sqlite3.connect(database) as connection:
        connection.executemany("INSERT INTO sleep_log(date,time_asleep_hours,source) VALUES(?,?,?)", [
            ("2026-06-24", 5, "fictional-device-a"), ("2026-06-26", 9, "fictional-device-b"),
        ])

    strata = LocalSurface(database).execute(_request())["facts"][0]["comparisons"][0]["source_comparisons"]

    assert [item["paired_units"] for item in strata] == [1, 1]
    assert [item["pattern_status"] for item in strata] == ["insufficient_comparison", "insufficient_comparison"]
    assert [item["mean_difference"] for item in strata] == [None, None]
    assert [item["reason"] for item in strata] == ["no_exposure_contrast", "no_exposure_contrast"]
    assert [item["eligible_outcome_units"] for item in strata] == [None, None]
    assert [item["missing_exposure_units"] for item in strata] == [None, None]


def test_each_source_stratum_replays_its_parent_identities(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)])
    with sqlite3.connect(database) as connection:
        connection.executemany("INSERT INTO sleep_log(date,time_asleep_hours,source) VALUES(?,?,?)", [
            ("2026-06-24", 5, "fictional-device-a"), ("2026-06-26", 9, "fictional-device-b"),
        ])
    surface = LocalSurface(database)
    result = surface.execute(_request())

    replay = surface.execute({"operation": "health_evidence", "arguments": {
        "evidence_refs": result["evidence_refs"], "detail": "lineage",
    }})

    assert replay["facts"][0]["facts"] == result["facts"]
    description = replay["facts"][0]["facts"][0]
    strata = description["comparisons"][0]["source_comparisons"]
    assert len({item["source_id"] for item in strata}) == 2
    assert [len(item["source_row_ids"]) for item in strata] == [2, 2]
    observation_ids = {item["observation_id"] for item in description["observations"]}
    assert {identity for stratum in strata for row in stratum["sample"]
            for field in ("outcome_observation_ids", "exposure_observation_ids")
            for identity in row[field]} <= observation_ids


@pytest.mark.parametrize(("second_protocol", "expected_status", "expected_difference"), [
    ("fictional machine a", "observed_difference", 0.5),
    ("fictional machine b", "protocol_incompatible", None),
    (None, "protocol_unavailable", None),
])
def test_slow_fitness_summaries_preserve_protocol_compatibility(
    database, second_protocol, expected_status, expected_difference,
):
    with sqlite3.connect(database) as connection:
        connection.executemany("""INSERT INTO fitness_tests
            (date,movement,side,load_kg,reps,equipment_note,source) VALUES(?,?,?,?,?,?,?)""", [
            ("2026-06-02", "leg-extension", "left", 50, 6, "fictional machine a", "manual"),
            ("2026-06-02", "leg-extension", "right", 50, 6, "fictional machine a", "manual"),
            ("2026-06-29", "leg-extension", "left", 100, 6, second_protocol, "manual"),
            ("2026-06-29", "leg-extension", "right", 50, 6, second_protocol, "manual"),
        ])

    description = LocalSurface(database).execute(_request(
        (), outcome_key="fitness.test.leg-extension.side_gap",
        range={"from": "2026-06-01", "to": "2026-06-30"},
    ))["facts"][0]

    feature = description["features"][0]
    assert feature["observed_units"] == 2
    assert feature["period_compare"]["status"] == expected_status
    assert feature["period_compare"]["mean_difference"] == expected_difference
    assert [row["value"] for row in feature["sample"]] == [0, 0.5]
    assert all("protocol_identity" in row for row in description["observations"])


def test_protocol_incompatibility_also_blocks_source_only_fitness_means(database):
    with sqlite3.connect(database) as connection:
        connection.executemany("""INSERT INTO fitness_tests
            (date,movement,side,load_kg,reps,equipment_note,source) VALUES(?,?,?,?,?,?,?)""", [
            ("2026-06-02", "leg-extension", "left", 50, 6, "fictional machine a", "manual"),
            ("2026-06-02", "leg-extension", "right", 50, 6, "fictional machine a", "manual"),
            ("2026-06-29", "leg-extension", "left", 100, 6, "fictional machine b", "manual"),
            ("2026-06-29", "leg-extension", "right", 50, 6, "fictional machine b", "manual"),
        ])

    feature = LocalSurface(database).execute(_request(
        (), outcome_key="fitness.test.leg-extension.side_gap",
        range={"from": "2026-06-01", "to": "2026-06-30"},
    ))["facts"][0]["features"][0]

    assert feature["summary"] is None
    assert feature["source_summaries"][0]["n"] == 2
    assert feature["source_summaries"][0]["mean"] is None
    assert feature["source_summaries"][0]["reason"] == "protocol_incompatible"


def test_mixed_protocols_remain_incomparable_within_a_source_stratum(database):
    with sqlite3.connect(database) as connection:
        connection.executemany("""INSERT INTO fitness_tests
            (date,movement,side,load_kg,reps,equipment_note,source) VALUES(?,?,?,?,?,?,?)""", [
            ("2026-06-04", "leg-extension", "left", 50, 6, "fictional machine a", "manual"),
            ("2026-06-04", "leg-extension", "right", 50, 6, "fictional machine a", "manual"),
            ("2026-06-06", "leg-extension", "left", 100, 6, "fictional machine b", "manual"),
            ("2026-06-06", "leg-extension", "right", 50, 6, "fictional machine b", "manual"),
            ("2026-06-08", "leg-extension", "left", 50, 6, "fictional machine b", "manual"),
            ("2026-06-08", "leg-extension", "right", 50, 6, "fictional machine b", "manual"),
        ])
        connection.executemany("INSERT INTO weather(date,location,temp_max_c,source) VALUES(?,?,?,?)", [
            ("2026-06-03", "Fictional garden", 5, "fictional-device-a"),
            ("2026-06-05", "Fictional garden", 9, "fictional-device-a"),
            ("2026-06-07", "Fictional garden", 6, "fictional-device-b"),
        ])
    surface = LocalSurface(database)
    catalog = surface.execute({"operation": "health_catalog", "arguments": {"domains": ["weather"], "limit": 50}})
    exposure_key = next(item["key"] for item in catalog["facts"] if item["key"].endswith(".temp_max_c"))

    comparison = surface.execute(_request(
        (exposure_key,),
        outcome_key="fitness.test.leg-extension.side_gap",
        range={"from": "2026-06-01", "to": "2026-06-30"},
    ))["facts"][0]["comparisons"][0]

    assert comparison["source_comparisons"][0]["paired_units"] == 2
    assert comparison["source_comparisons"][0]["reason"] == "protocol_incompatible"
    assert comparison["source_comparisons"][0]["mean_difference"] is None
    assert len(comparison["source_comparisons"][0]["comparability"]["outcome"]["protocols"]) == 2


def test_source_strata_display_is_bounded_without_hiding_its_total(database):
    start = date(2026, 6, 1)
    with sqlite3.connect(database) as connection:
        connection.executemany("INSERT INTO subjective_daily(date,energy) VALUES(?,?)", [
            ((start + timedelta(days=offset + 1)).isoformat(), 5) for offset in range(10)
        ])
        connection.executemany("INSERT INTO sleep_log(date,time_asleep_hours,source) VALUES(?,?,?)", [
            ((start + timedelta(days=offset)).isoformat(), 8, f"fictional-device-{offset}")
            for offset in range(10)
        ])

    comparison = LocalSurface(database).execute(_request(
        range={"from": "2026-06-01", "to": "2026-06-30"},
    ))["facts"][0]["comparisons"][0]

    assert comparison["paired_units"] == 10
    assert comparison["source_comparisons_total"] == 10
    assert comparison["source_comparisons_shown"] == 8
    assert len(comparison["source_comparisons"]) == 8
    assert comparison["source_comparisons_selection"] == "largest_pair_counts_then_source_identity"


def test_partial_logged_nutrition_remains_a_visible_subtotal(database):
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO nutrition_log(date,food_name,kcal,source) VALUES(?,?,?,?)",
                           ("2026-06-25", "Fictional lunch", 500, "manual"))

    result = LocalSurface(database).execute(_request((), outcome_key="nutrition.logged.kcal"))

    feature = result["facts"][0]["features"][0]
    assert result["status"] == "ok"
    assert feature["observed_units"] == 0
    assert feature["recorded_observation_rows"] == 1
    assert feature["partial_recorded_observation_rows"] == 1
    assert feature["summary"]["mean"] is None
    partial = feature["partial_recorded_facts"][0]
    assert partial["value"] == 500
    assert partial["unit"] == "kcal"
    assert partial["value_scope"] == "recorded_subtotal"
    assert partial["completeness"] == "full_day_coverage_unconfirmed"
    assert partial["candidate_eligible"] is False
    assert partial["source_row_ids"]


def test_partial_nutrition_cannot_enter_comparisons_but_replays_its_source(database):
    _records(database, energy=[("2026-06-26", 5)])
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO nutrition_log(date,food_name,kcal,source) VALUES(?,?,?,?)",
                           ("2026-06-25", "Fictional lunch", 500, "manual"))
    surface = LocalSurface(database)
    result = surface.execute(_request(("nutrition.logged.kcal",)))

    replay = surface.execute({"operation": "health_evidence", "arguments": {
        "evidence_refs": result["evidence_refs"], "detail": "lineage",
    }})

    assert replay["facts"][0]["facts"] == result["facts"]
    description = replay["facts"][0]["facts"][0]
    assert description["comparisons"][0]["paired_units"] == 0
    assert description["comparisons"][0]["missing_exposure_units"] == 1
    partial = description["features"][1]["partial_recorded_facts"][0]
    observation = next(item for item in description["observations"] if item["observation_id"] == partial["observation_id"])
    assert observation["value"] == 500
    assert observation["candidate_eligible"] is False
    assert observation["completeness"] == "full_day_coverage_unconfirmed"


def test_empty_records_do_not_suggest_a_clear_or_null_pattern(database):
    result = LocalSurface(database).execute(_request())

    assert result["status"] == "insufficient_data"
    assert result["facts"][0]["comparisons"][0]["pattern_status"] == "insufficient_comparison"
    assert result["facts"][0]["features"][0]["summary"]["mean"] is None


@pytest.mark.parametrize("overrides", [
    {"lag_days": True}, {"lag_days": -1}, {"lag_days": 4},
    {"range": {"from": "0001-01-01", "to": "0001-01-02"}},
    {"exposure_keys": ["sleep.duration_hours", "sleep.duration_hours"]},
    {"exposure_keys": ["subjective.energy"]}, {"min_n": 1},
    {"range": {"from": "2026-06-30", "to": "2026-06-24"}},
])
def test_malformed_requests_are_refused(database, overrides):
    with pytest.raises(SurfaceError):
        LocalSurface(database).execute(_request(**overrides))


def test_unregistered_features_are_unsupported(database):
    result = LocalSurface(database).execute(_request(("invented.sleep",)))

    assert result["status"] == "unsupported"
    assert result["facts"] == []


def test_malformed_observed_numbers_do_not_become_missing_or_zero(database):
    _records(database, energy=[("2026-06-25", "invalid-number")])

    with pytest.raises(FrameError, match="numeric observation is invalid"):
        LocalSurface(database).execute(_request())


def test_describing_sparse_records_does_not_change_formal_inference(database):
    _records(database, energy=[("2026-06-25", 2), ("2026-06-27", 8)],
             sleep=[("2026-06-24", 5), ("2026-06-26", 9)])
    surface = LocalSurface(database)
    request = {"operation": "health_analyze", "arguments": {
        "outcome_key": "subjective.energy", "exposure_keys": ["sleep.duration_hours"],
        "range": {"from": "2026-06-24", "to": "2026-06-30"}, "mode": "ordinal",
        "interactions": False, "min_n": 30, "top": 1,
    }}
    before = surface.execute(request)

    surface.execute(_request())
    after = surface.execute(request)

    assert after == before
    assert after["status"] == "insufficient_data"
    assert after["findings"] == []


def test_training_evidence_uses_the_same_read_only_replay_boundary(database):
    migrations.migrate(str(database), 8, 3, code_version="b" * 40)
    _records(database, energy=[("2026-06-25", 4)])
    surface = LocalSurface(database)
    before = database.read_bytes()

    result = surface.execute({"operation": "health_training_evidence", "arguments": {
        "range": {"from": "2026-06-24", "to": "2026-06-30"},
    }})
    replay = surface.execute({"operation": "health_evidence", "arguments": {
        "evidence_refs": result["evidence_refs"], "detail": "lineage",
    }})

    packet = result["facts"][0]
    assert packet["contract"] == "training-evidence-v1"
    assert packet["recovery"]["reported"][0]["energy"] == 4
    assert result["findings"] == []
    assert replay["facts"][0]["facts"] == result["facts"]
    assert database.read_bytes() == before
