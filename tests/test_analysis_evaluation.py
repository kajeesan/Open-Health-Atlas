"""Small semantic checks for the opt-in fictional evaluation command."""

import json

import pytest

from scripts import analysis_evaluation as evaluation


def test_missing_observations_do_not_become_zero_or_delete_other_stream_coverage():
    result = evaluation.evaluate_pairs([0, None, 2, 3], [0, 1, None, 6], permutation_seed=73)

    assert result["calendar_units"] == 4
    assert result["left_observed_units"] == 3
    assert result["right_observed_units"] == 3
    assert result["paired_units"] == 2
    assert result["rho"] == pytest.approx(1)


def test_rank_evidence_is_unchanged_by_positive_unit_conversions():
    original = evaluation.evaluate_pairs([1, 2, 3, 4], [4, 1, 3, 2], permutation_seed=73)
    converted = evaluation.evaluate_pairs([60, 120, 180, 240], [140, 110, 130, 120], permutation_seed=73)

    assert original["rho"] == pytest.approx(-0.4)
    assert converted["rho"] == original["rho"]
    assert converted["p_value"] == original["p_value"]


@pytest.mark.parametrize(("left", "right"), [
    pytest.param([1, 1, 1], [1, 2, 3], id="constant_exposure"),
    pytest.param([None, 1], [2, None], id="no_paired_observations"),
    pytest.param([1, None], [2, None], id="one_paired_observation"),
])
def test_unevaluable_pairs_are_reported_as_skipped(left, right):
    result = evaluation.evaluate_pairs(left, right, permutation_seed=73)

    assert result["status"] == "insufficient_or_constant"
    assert result["p_value"] is None
    assert result["p_le_alpha"] is None


def test_selected_scenario_replays_independently_of_other_scenarios():
    selected = evaluation.run_evaluation(
        split="development", replicates=1, scenario_keys=["sparse_signal"], suite="component",
    )
    expanded = evaluation.run_evaluation(
        split="development", replicates=1, scenario_keys=["iid_null", "sparse_signal"], suite="component",
    )

    assert selected["component_cases"][0]["replicates"] == expanded["component_cases"][1]["replicates"]


def test_confirmation_split_uses_different_seed_streams():
    report = evaluation.run_evaluation(
        split="all", replicates=1, scenario_keys=["sparse_signal"], suite="component",
    )
    development, held_out = report["component_cases"]

    assert development["split"] == "development"
    assert held_out["split"] == "held_out"
    assert set(development["replicates"][0]["seeds"].values()).isdisjoint(
        held_out["replicates"][0]["seeds"].values()
    )


def test_report_exposes_component_denominators_and_sampling_uncertainty():
    report = evaluation.run_evaluation(
        split="development", replicates=1, scenario_keys=["sparse_signal"], suite="component",
    )
    case = report["component_cases"][0]
    rate = case["threshold_exceedance"]

    assert case["replicates_evaluated"] == 1
    assert case["paired_units"]["minimum"] == 12
    assert rate["denominator"] == 1
    assert rate["wilson_95"][0] < rate["wilson_95"][1]
    assert "not a full-engine" in report["measurement_scope"]


def test_descriptive_command_writes_reviewable_boundary_evidence(tmp_path):
    output = tmp_path / "descriptive-evaluation.json"

    status = evaluation.main(["--output", str(output), "--suite", "descriptive"])
    report = json.loads(output.read_text())

    assert status == 0
    assert report["descriptive_checks"]["passed"] is True
    assert all(check["passed"] for check in report["descriptive_checks"]["checks"])
    assert report["source"]["unchanged_during_run"] is True


def test_changed_fixture_causes_a_failed_evaluation_instead_of_a_new_expected_result(tmp_path):
    fixture = json.loads((evaluation.ROOT / "toolkit/tests/fixtures/analysis_evaluation/sparse_sleep_energy.json").read_text())
    fixture["records"][0]["energy"] = 1
    fixture["records"][-1]["energy"] = 5
    changed = tmp_path / "contradictions-removed.json"
    changed.write_text(json.dumps(fixture))

    report = evaluation.run_descriptive_checks(fixture_path=changed)

    assert report["passed"] is False
    assert [item["case"] for item in report["checks"] if not item["passed"]] == [
        "counterexamples_remain_visible_in_observed_difference",
    ]


def test_artifact_command_refuses_to_replace_existing_evidence(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text('{"retained":true}\n')

    with pytest.raises(SystemExit) as error:
        evaluation.main(["--output", str(output), "--suite", "descriptive"])

    assert error.value.code == 2
    assert json.loads(output.read_text()) == {"retained": True}


def test_artifact_command_refuses_checkout_output():
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--output", str(evaluation.ROOT / "must-not-create-evaluation.json")])

    assert error.value.code == 2


def test_unequal_calendar_streams_are_rejected():
    with pytest.raises(ValueError, match="same calendar length"):
        evaluation.evaluate_pairs([1, 2], [1], permutation_seed=73)


@pytest.mark.parametrize("replicates", [0, 2001, True])
def test_unbounded_or_ambiguous_replicate_counts_are_rejected(replicates):
    with pytest.raises(ValueError, match="replicates must be an integer"):
        evaluation.run_evaluation(split="development", replicates=replicates, suite="component")
