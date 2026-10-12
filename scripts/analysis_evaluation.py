"""Opt-in fictional evaluation; no clinical or full-engine calibration claim.

Run from a checkout with ``python -B scripts/analysis_evaluation.py --output
/tmp/oha-evaluation.json --split development``. Run held_out separately only
after freezing the protocol; its seeds are disjoint, not permanently unseen.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import sqlite3
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "toolkit") not in sys.path:
    sys.path.insert(0, str(ROOT / "toolkit"))

from hermes_insights import associations, stats  # noqa: E402
from hermes_insights._native_stats import native_status  # noqa: E402


@dataclass(frozen=True)
class _Scenario:
    key: str
    phi: float
    innovation_correlation: float
    missingness: str
    observed_null: bool
    interpretation: str


_SCENARIOS = (
    _Scenario("iid_null", 0.0, 0.0, "complete", True,
              "Independent iid Gaussian series."),
    _Scenario("ar1_null", 0.85, 0.0, "complete", True,
              "Independent AR(1) series; row permutations do not preserve dependence."),
    _Scenario("mcar_null", 0.0, 0.0, "independent_mcar_35pct", True,
              "Independent iid series, independently missing 35% of each stream in expectation."),
    _Scenario("iid_signal", 0.0, 0.6, "complete", False,
              "Positive correlation from shared innovations; no causal interpretation."),
    _Scenario("ar1_signal", 0.85, 0.6, "complete", False,
              "Positive contemporaneous correlation with temporal dependence."),
    _Scenario("mcar_signal", 0.0, 0.6, "independent_mcar_35pct", False,
              "Correlated iid series with independently missing observations."),
    _Scenario("sparse_signal", 0.0, 0.6, "twelve_paired_days", False,
              "Twelve randomly selected paired days in a 90-day window."),
    _Scenario("selected_latent_null", 0.0, 0.0, "same_sign_selection", False,
              "Latent independent series; observing Y only when X and Y share a sign "
              "induces association. Exceedances are not observed-null false positives."),
)
_SPLIT_SEEDS = {"development": 2026100201, "held_out": 2026100202}
_OBSERVATIONS = 90
_BURN_IN = 200
_ALPHA = 0.05
_START = date(2026, 1, 1)


def _seed(split: str, scenario: str, replicate: int, purpose: str) -> int:
    payload = f"oha-evaluation-v1:{_SPLIT_SEEDS[split]}:{scenario}:{replicate}:{purpose}"
    return int.from_bytes(hashlib.sha256(payload.encode("ascii")).digest()[:8], "big")


def _series(scenario: _Scenario, seed: int):
    generator = random.Random(seed)
    left, right = [], []
    x = y = 0.0
    noise_scale = math.sqrt(1 - scenario.innovation_correlation ** 2)
    for index in range(_OBSERVATIONS + _BURN_IN):
        x_noise = generator.gauss(0, 1)
        y_noise = scenario.innovation_correlation * x_noise + noise_scale * generator.gauss(0, 1)
        x = scenario.phi * x + x_noise
        y = scenario.phi * y + y_noise
        if index >= _BURN_IN:
            left.append(x)
            right.append(y)
    return left, right


def _mask(scenario: _Scenario, left, right, seed: int):
    generator = random.Random(seed)
    if scenario.missingness == "independent_mcar_35pct":
        return (
            [value if generator.random() >= 0.35 else None for value in left],
            [value if generator.random() >= 0.35 else None for value in right],
        )
    if scenario.missingness == "twelve_paired_days":
        selected = set(generator.sample(range(len(left)), 12))
        return (
            [value if index in selected else None for index, value in enumerate(left)],
            [value if index in selected else None for index, value in enumerate(right)],
        )
    if scenario.missingness == "same_sign_selection":
        return list(left), [y if (x >= 0) == (y >= 0) else None for x, y in zip(left, right)]
    return list(left), list(right)


def evaluate_pairs(
    left: Sequence[float | None], right: Sequence[float | None], *, permutation_seed: int,
) -> dict:
    """Measure the production rank-permutation primitive on paired observations.

    None is missing, never zero. No readiness, multiplicity, lineage, hypothesis
    or surface eligibility gate is applied. Split-half status is an additional
    component measurement, not a substitute for those gates.
    """
    if len(left) != len(right):
        raise ValueError("observation streams must have the same calendar length")
    for value in (*left, *right):
        if value is not None:
            stats.finite_float(value)
    rows = [
        {"date": (_START + timedelta(days=index)).isoformat(), "unit_key": str(index),
         "exposure": x, "outcome": y}
        for index, (x, y) in enumerate(zip(left, right)) if x is not None and y is not None
    ]
    xs = [row["exposure"] for row in rows]
    ys = [row["outcome"] for row in rows]
    rho = stats.spearman(xs, ys)
    result = {
        "calendar_units": len(left),
        "left_observed_units": sum(value is not None for value in left),
        "right_observed_units": sum(value is not None for value in right),
        "paired_units": len(rows),
        "rho": rho,
        "p_value": None,
        "p_le_alpha": None,
        "split_half_status": "not_evaluated",
        "status": "insufficient_or_constant" if rho is None else "evaluated",
    }
    if rho is not None:
        p = stats._rank_permutation_p_value(
            stats.average_ranks(xs), stats.average_ranks(ys), seed=permutation_seed,
        )
        result.update(p_value=p, p_le_alpha=p <= _ALPHA,
                      split_half_status=associations._stability(rows, "spearman", rho)["status"])
    return result


def _rate(hits: int, denominator: int) -> dict:
    return {
        "count": hits, "denominator": denominator,
        "fraction": hits / denominator if denominator else None,
        "wilson_95": list(stats.wilson_interval(hits, denominator)) if denominator else None,
    }


def _component_case(split: str, scenario: _Scenario, replicates: int) -> dict:
    results = []
    for replicate in range(replicates):
        seeds = {purpose: _seed(split, scenario.key, replicate, purpose)
                 for purpose in ("data", "missingness", "permutation")}
        complete_left, complete_right = _series(scenario, seeds["data"])
        left, right = _mask(scenario, complete_left, complete_right, seeds["missingness"])
        result = evaluate_pairs(left, right, permutation_seed=seeds["permutation"])
        results.append({"replicate": replicate, "seeds": seeds,
                        "complete_series_rho": stats.spearman(complete_left, complete_right), **result})
    evaluated = [result for result in results if result["status"] == "evaluated"]
    hits = sum(result["p_le_alpha"] for result in evaluated)
    stable_hits = sum(result["p_le_alpha"] and result["split_half_status"] == "stable"
                      for result in evaluated)
    paired = [result["paired_units"] for result in results]
    return {
        "split": split, "scenario": asdict(scenario), "replicates_requested": replicates,
        "replicates_evaluated": len(evaluated), "replicates_skipped": replicates - len(evaluated),
        "status_counts": dict(sorted(Counter(result["status"] for result in results).items())),
        "paired_units": {"minimum": min(paired), "maximum": max(paired),
                         "mean": math.fsum(paired) / len(paired)},
        "threshold_exceedance": _rate(hits, len(evaluated)),
        "threshold_exceedance_and_split_half_stable": _rate(stable_hits, len(evaluated)),
        "positive_rho": _rate(sum(result["rho"] > 0 for result in evaluated), len(evaluated)),
        "replicates": results,
    }


def _source_identity(paths: Sequence[Path]) -> dict:
    files = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths)}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
    return {"sha256": digest, "files": files}


def _git_state() -> dict:
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout
    return {"revision": revision, "working_tree_has_changes": bool(status.strip())}


def _seed_descriptive_database(path: Path, fixture: dict, schema: str, *, source_switch=False, fallback=False):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(schema)
        for row in fixture["records"]:
            source = "fictional_replacement_device" if source_switch and row["date"] >= "2026-01-07" else "manual"
            connection.execute(
                "INSERT INTO sleep_log(date,time_asleep_hours,time_in_bed_hours,source,ingested_at) "
                "VALUES(?,?,?,?,?)",
                (row["date"], row["sleep_hours"], 10.0, source, fixture["ingested_at"]),
            )
            connection.execute(
                "INSERT INTO subjective_daily(date,energy,source,ingested_at) VALUES(?,?,?,?)",
                (row["date"], row["energy"], "manual", fixture["ingested_at"]),
            )
            if fallback:
                connection.execute(
                    "INSERT INTO daily_metrics(date,source,sleep_hours) VALUES(?,?,?)",
                    (row["date"], "apple", row["sleep_hours"]),
                )
        connection.commit()
    finally:
        connection.close()


def run_descriptive_checks(*, fixture_path: Path) -> dict:
    """Evaluate fixed fictional source/missingness boundaries through production adapters.

    Creates isolated temporary databases, then uses the read-only analytical
    connection. These cases cover selected paths, not a simulated full-engine
    error rate, a public MCP roundtrip or external-agent answer quality.
    """
    from hermes_insights import descriptive, hermes_surface, runtime
    from hermes_insights.contracts import DateRange
    from hermes_insights.registry import build_registry

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    requested = DateRange(date.fromisoformat(fixture["from"]), date.fromisoformat(fixture["to"]))
    context = runtime.adapter_context(
        clock=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc), timezone="UTC",
    )
    schema = (ROOT / "toolkit/SCHEMA.sql").read_text(encoding="utf-8")
    sleep_key, outcome_key = "sleep.duration_hours", "subjective.energy"
    reports = {}
    formal = None
    with tempfile.TemporaryDirectory(prefix="oha-analysis-evaluation-") as temporary:
        for name in ("baseline", "empty_feature", "shared_parent", "fallback_duplicate", "source_switch"):
            path = Path(temporary) / (name + ".db")
            _seed_descriptive_database(path, fixture, schema, source_switch=name == "source_switch",
                                       fallback=name == "fallback_duplicate")
            connection = runtime.connect_read_only(path)
            try:
                definitions = build_registry(connection, context)
                exposures = [sleep_key]
                if name == "empty_feature":
                    exposures.append("wearable.steps")
                if name == "shared_parent":
                    exposures.append("sleep.efficiency")
                reports[name] = descriptive.describe_outcome(
                    connection, definitions, requested, context, outcome_key=outcome_key,
                    exposure_keys=exposures, lag_days=0,
                )
                if name == "baseline":
                    formal = associations.analyze_outcome(
                        connection, [item for item in definitions if item.key in {sleep_key, outcome_key}],
                        requested, context, outcome_key=outcome_key, mode="ordinal",
                    )
            finally:
                connection.close()
    baseline = reports["baseline"]
    baseline_pair = baseline["comparisons"][0]
    baseline_features = {item["feature_key"]: item for item in baseline["features"]}
    empty_features = {item["feature_key"]: item for item in reports["empty_feature"]["features"]}
    shared_features = {item["feature_key"]: item for item in reports["shared_parent"]["features"]}
    shared_source_ids = set(shared_features[sleep_key]["source_row_ids"]) & set(
        shared_features["sleep.efficiency"]["source_row_ids"]
    )
    shared_diagnostics = [item for item in reports["shared_parent"]["shared_source_rows"]
                          if {sleep_key, "sleep.efficiency"} <= set(item["feature_keys"])]
    _, _, eligible_findings = hermes_surface._analysis_projection(formal)
    count = len(fixture["records"])
    range_days = (requested.end - requested.start).days + 1
    switched = reports["source_switch"]["comparisons"][0]
    checks = [
        {"case": "sparse_descriptions_preserve_actual_denominators",
         "passed": baseline_pair["paired_units"] == count
                   and baseline_features[sleep_key]["observed_units"] == count
                   and baseline_features[sleep_key]["days_without_usable_observation"] == range_days - count,
         "observed": {"paired_units": baseline_pair["paired_units"],
                      "sleep_observed_units": baseline_features[sleep_key]["observed_units"],
                      "sleep_unknown_days": baseline_features[sleep_key]["days_without_usable_observation"]},
         "expected": {"paired_units": count, "sleep_observed_units": count, "sleep_unknown_days": range_days - count}},
        {"case": "counterexamples_remain_visible_in_observed_difference",
         "passed": baseline_pair["counterexamples"]["lower_group"] == 1
                   and baseline_pair["counterexamples"]["higher_group"] == 1
                   and math.isclose(baseline_pair["mean_difference"], 1 / 3),
         "observed": {"counterexamples": baseline_pair["counterexamples"],
                      "mean_difference": baseline_pair["mean_difference"]},
         "expected": {"lower_group_counterexamples": 1, "higher_group_counterexamples": 1,
                      "mean_difference": 1 / 3}},
        {"case": "descriptive_output_does_not_bypass_formal_gate",
         "passed": baseline["eligible_for_hypothesis"] is False
                   and baseline["inference_status"] == "not_assessed" and not eligible_findings
                   and formal["suppression_counts"]["aligned_n"] > 0,
         "observed": {"descriptive_eligible_for_hypothesis": baseline["eligible_for_hypothesis"],
                      "inference_status": baseline["inference_status"],
                      "formal_surface_eligible_findings": len(eligible_findings),
                      "formal_suppression_counts": formal["suppression_counts"]}},
        {"case": "unrelated_empty_feature_does_not_delete_other_pairs",
         "passed": reports["empty_feature"]["comparisons"][0] == baseline_pair
                   and empty_features[outcome_key] == baseline_features[outcome_key]
                   and empty_features["wearable.steps"]["observed_units"] == 0,
         "observed": {"before_paired_units": baseline_pair["paired_units"],
                      "after_paired_units": reports["empty_feature"]["comparisons"][0]["paired_units"],
                      "empty_feature_observed_units": empty_features["wearable.steps"]["observed_units"]}},
        {"case": "derived_features_retain_shared_source_identity",
         "passed": len(shared_source_ids) == count
                   and len(shared_diagnostics) == count
                   and all(item["independent_corroboration"] is False for item in shared_diagnostics)
                   and reports["shared_parent"]["comparisons"][0] == baseline_pair,
         "observed": {"shared_source_row_count": len(shared_source_ids),
                      "explicit_dependent_source_row_count": len(shared_diagnostics),
                      "paired_units": reports["shared_parent"]["comparisons"][0]["paired_units"]},
         "interpretation": "Two derived features reuse the same parent records; counts cannot be "
                           "summed as independent corroboration. No statistical deduplication rate is estimated."},
        {"case": "redundant_fallback_records_do_not_inflate_pairs",
         "passed": reports["fallback_duplicate"]["comparisons"][0] == baseline_pair,
         "observed": {"before_paired_units": baseline_pair["paired_units"],
                      "after_paired_units": reports["fallback_duplicate"]["comparisons"][0]["paired_units"]},
         "interpretation": "Equal fallback wearable sleep records alongside primary sleep_log records; "
                           "this tests the canonical merge priority, not every duplicate-record shape."},
        {"case": "source_switch_prevents_pooled_contrast",
         "passed": switched["reason"] == "source_incompatible" and switched["mean_difference"] is None
                   and switched["paired_units"] == count and len(switched["source_eras"]) == 2,
         "observed": {"reason": switched["reason"], "mean_difference": switched["mean_difference"],
                      "paired_units": switched["paired_units"],
                      "source_eras": switched.get("source_eras", [])}},
    ]
    return {
        "scope": "Fictional SQLite records -> production registry/adapters/frame/alignment -> "
                 "describe_outcome; one sparse formal analyze_outcome/projection check. "
                 "Selected deterministic boundaries, not a full-pipeline false-positive estimate.",
        "fixture_id": fixture["fixture_id"], "data_class": "fictional", "lag_days": 0,
        "passed": all(check["passed"] for check in checks), "checks": checks,
        "baseline_descriptive": baseline,
    }


def run_evaluation(
    *, split: str, replicates: int, scenario_keys: Sequence[str] | None = None, suite: str = "all",
) -> dict:
    """Run the opt-in component protocol with reproducible, independently seeded replicates.

    The held-out split is a predeclared confirmation set. Once examined it must
    not be described as unseen, and it must not be used to tune runtime methods.
    """
    if split not in (*_SPLIT_SEEDS, "all"):
        raise ValueError("unknown seed split")
    if type(replicates) is not int or not 1 <= replicates <= 2000:
        raise ValueError("replicates must be an integer from 1 to 2000")
    if suite not in {"all", "component", "descriptive"}:
        raise ValueError("unknown evaluation suite")
    selected = list(scenario_keys) if scenario_keys is not None else [item.key for item in _SCENARIOS]
    if not selected or len(set(selected)) != len(selected) or set(selected) - {item.key for item in _SCENARIOS}:
        raise ValueError("scenario selection must contain unique known scenarios")
    scenarios = [item for item in _SCENARIOS if item.key in selected]
    splits = list(_SPLIT_SEEDS) if split == "all" else [split]
    protocol = {
        "id": "oha-analysis-evaluation-v1", "data_class": "fictional",
        "split_seeds": dict(_SPLIT_SEEDS), "planned_replicates_per_case": 200,
        "observations_before_missingness": _OBSERVATIONS, "ar1_burn_in": _BURN_IN,
        "alpha": _ALPHA, "scenarios": [asdict(item) for item in _SCENARIOS],
        "seed_derivation": "First 8 bytes, big endian, of SHA256(ASCII "
                           "oha-evaluation-v1:{split_seed}:{scenario_key}:{zero_based_replicate}:{purpose}); "
                           "purpose is data, missingness or permutation.",
        "holdout_policy": "Development and held_out schedules are fixed and disjoint. "
                          "Freeze methods before held_out; after inspection this is a reusable "
                          "confirmation set, not unseen evidence. No runtime method tuning in this harness.",
    }
    source_paths = [Path(__file__).resolve()] + [ROOT / "toolkit/hermes_insights" / name for name in (
        "stats.py", "associations.py", "contracts.py", "_native_stats.py", "native/rank_products.c",
    )]
    fixture_path = ROOT / "toolkit/tests/fixtures/analysis_evaluation/sparse_sleep_energy.json"
    if suite in {"all", "descriptive"}:
        source_paths = sorted(set(source_paths) | set((ROOT / "toolkit/hermes_insights").rglob("*.py"))
                              | {ROOT / "toolkit/SCHEMA.sql", fixture_path})
    source_before = _source_identity(source_paths)
    git_state = _git_state()
    started = time.perf_counter()
    cases = ([_component_case(seed_split, scenario, replicates)
              for seed_split in splits for scenario in scenarios]
             if suite in {"all", "component"} else [])
    descriptive_checks = run_descriptive_checks(fixture_path=fixture_path) if suite in {"all", "descriptive"} else None
    source_after = _source_identity(source_paths)
    return {
        "contract": "oha-analysis-evaluation-report-v1",
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(json.dumps(protocol, sort_keys=True).encode("utf-8")).hexdigest(),
        "invocation": {"split": split, "replicates": replicates, "scenarios": selected, "suite": suite},
        "source": {**git_state, "before": source_before, "after": source_after,
                   "unchanged_during_run": source_before == source_after},
        "method": {"callable": "hermes_insights.stats._rank_permutation_p_value",
                   "permutation_iterations": stats.PERMUTATION_ITERATIONS,
                   "p_value_resolution": 1 / (stats.PERMUTATION_ITERATIONS + 1),
                   "native_accelerator": native_status(),
                   "python_version": platform.python_version()},
        "measurement_scope": "Component cases measure the single-pair production rank-permutation "
                             "primitive plus split-half stability; not a full-engine false-positive rate. "
                             "Descriptive cases measure selected deterministic pipeline boundaries.",
        "uncertainty_scope": "95% Wilson intervals describe binomial variation across independently "
                             "seeded replicates. They are not individual prediction intervals, "
                             "p-value Monte Carlo intervals or clinical calibration.",
        "limitations": [
            "No feature search, multiple-testing adjustment, readiness, lineage, hypothesis gate, "
            "surface filtering or external-agent interpretation is measured by component cases.",
            "Known correlation is a simulator property, not a causal treatment effect.",
            "MCAR and same-sign selection are specified stress cases, not models of real logging behavior.",
            "Repeated seeds make reruns reproducible; reruns are not additional independent evidence.",
            "No clinical validation, patient prediction, runtime model selection or calibration improvement is claimed.",
        ],
        "component_cases": cases, "descriptive_checks": descriptive_checks,
        "elapsed_seconds": time.perf_counter() - started,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Write one new JSON artifact outside the checkout, refusing overwrite."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=(*_SPLIT_SEEDS, "all"), default="development")
    parser.add_argument("--replicates", type=int, default=200)
    parser.add_argument("--suite", choices=("all", "component", "descriptive"), default="all")
    parser.add_argument("--scenario", action="append", choices=[item.key for item in _SCENARIOS])
    arguments = parser.parse_args(argv)
    output = arguments.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error("evaluation artifacts must be outside the checkout")
    if output.exists():
        parser.error("refusing to overwrite an existing evaluation artifact")
    try:
        report = run_evaluation(split=arguments.split, replicates=arguments.replicates,
                                scenario_keys=arguments.scenario, suite=arguments.suite)
    except ValueError as error:
        parser.error(str(error))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"output": str(output), "contract": report["contract"],
                      "component_cases": len(report["component_cases"]),
                      "source_unchanged": report["source"]["unchanged_during_run"]}))
    failed = (not report["source"]["unchanged_during_run"]
              or report["descriptive_checks"] is not None and not report["descriptive_checks"]["passed"])
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
