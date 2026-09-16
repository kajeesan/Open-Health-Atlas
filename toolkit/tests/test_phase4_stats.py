"""Reference and adversarial checks for the deterministic Phase 4 statistics."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import random
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_insights.contracts import canonical_json
from hermes_insights import stats
from hermes_insights.stats import (
    ANALYSIS_VERSION,
    BOOTSTRAP_ITERATIONS,
    PERMUTATION_ITERATIONS,
    StatsError,
    _rank_permutation_statistic,
    average_ranks,
    benjamini_hochberg,
    bootstrap_interval,
    deterministic_seed,
    deterministic_seed_material,
    finite_float,
    fisher_exact_two_sided,
    interquartile_range,
    newcombe_risk_difference_interval,
    pearson,
    percentile,
    permutation_p_value,
    spearman,
    wilson_interval,
)


def test_versions_iteration_counts_and_structured_float_contract():
    assert ANALYSIS_VERSION == "outcome-v1"
    assert PERMUTATION_ITERATIONS == 2_000
    assert BOOTSTRAP_ITERATIONS == 2_000
    assert canonical_json({"value": 1.234567890123456}) == \
        '{"value":1.23456789012}'

    normalized = finite_float(-0.0)
    assert normalized == 0.0
    assert math.copysign(1.0, normalized) == 1.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "1"])
def test_finite_float_rejects_nonfinite_and_lossy_values(value):
    with pytest.raises(StatsError, match="finite number"):
        finite_float(value)


def test_average_tie_ranks_and_spearman_reference_values():
    assert average_ranks([10, 20, 20, 40]) == [1.0, 2.5, 2.5, 4.0]
    assert average_ranks([]) == []
    assert spearman([1, 2, 2, 4], [4, 1, 1, 2]) == pytest.approx(-1 / 3)
    assert spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_pearson_refuses_mismatch_constants_and_nonfinite_calculation():
    assert pearson([1, 1, 1], [1, 2, 3]) is None
    assert pearson([1], [2]) is None
    with pytest.raises(StatsError, match="equal length"):
        pearson([1, 2], [1])
    with pytest.raises(StatsError, match="non-finite"):
        pearson([1e308, 1e308, -1e308], [1, 2, 3])

    zero = pearson([1, -1, 1, -1], [1, 1, -1, -1])
    assert zero == 0.0
    assert math.copysign(1.0, zero) == 1.0


def test_fisher_two_sided_fixed_margin_reference_and_equal_tail_inclusion():
    # Canonical reference also used by R/scipy documentation examples.
    assert fisher_exact_two_sided(((1, 9), (11, 3))) == pytest.approx(
        0.0027594561852200836,
    )
    # Both equally probable extreme tails must be included.
    assert fisher_exact_two_sided(((0, 10), (10, 0))) == pytest.approx(
        2 / math.comb(20, 10),
    )
    assert fisher_exact_two_sided(((250, 250), (250, 250))) == 1.0
    assert fisher_exact_two_sided(((0, 0), (0, 0))) == 1.0


@pytest.mark.parametrize(
    "table",
    [
        ((1, 2, 3), (4, 5, 6)),
        ((1, -1), (2, 3)),
        ((True, 1), (2, 3)),
    ],
)
def test_fisher_rejects_malformed_or_noncount_tables(table):
    with pytest.raises(StatsError):
        fisher_exact_two_sided(table)


def test_wilson_and_newcombe_reference_values_without_continuity_correction():
    assert wilson_interval(12, 20) == pytest.approx(
        (0.38658150076225317, 0.7811934676271829),
    )
    assert wilson_interval(5, 20) == pytest.approx(
        (0.11186170140766569, 0.4687008776187439),
    )
    assert newcombe_risk_difference_interval(12, 20, 5, 20) == pytest.approx(
        (0.04442262896591814, 0.5778448205440446),
    )
    lower, upper = wilson_interval(0, 10)
    assert lower == 0.0
    assert 0.0 < upper < 1.0

    lower, upper = wilson_interval(10, 10)
    assert 0.0 < lower < 1.0
    assert upper == 1.0


@pytest.mark.parametrize(
    ("successes", "total"),
    [(1, 0), (2, 1), (-1, 10), (True, 10)],
)
def test_wilson_rejects_invalid_counts(successes, total):
    with pytest.raises(StatsError):
        wilson_interval(successes, total)


def test_percentile_interpolation_and_iqr_are_exact():
    values = [0, 10, 20, 30]
    assert percentile(values, 0.025) == pytest.approx(0.75)
    assert percentile(values, 0.975) == pytest.approx(29.25)
    assert percentile([3], 0.5) == 3.0
    assert interquartile_range([0, 1, 2, 3, 4]) == 2.0
    assert interquartile_range([4, 4, 4, 4]) == 0.0
    with pytest.raises(StatsError, match="at least one"):
        percentile([], 0.5)
    with pytest.raises(StatsError, match="between zero and one"):
        percentile([1, 2], 1.1)


def _seed_arguments(**changes):
    values = {
        "analysis_version": "outcome-v1",
        "outcome_key": "subjective.day_rating",
        "outcome_mode": "green-vs-non-green",
        "components": [{
            "exposure_key": "sleep.duration_hours",
            "lag_days": 1,
            "window_days": 1,
            "transform": "point",
        }],
        "analysis_range": {
            "kind": "bounded",
            "from": "2026-01-01",
            "to": "2026-07-23",
        },
        "input_fingerprint": "sha256:" + "a" * 64,
    }
    values.update(changes)
    return values


def test_seed_has_exact_canonical_envelope_and_first_64_sha256_bits():
    material = deterministic_seed_material(**_seed_arguments())
    serialized = canonical_json(material)
    assert serialized == (
        '{"analysis_version":"outcome-v1","components":['
        '{"feature_key":"sleep.duration_hours","lag_days":1,"window_days":1}],'
        '"input_fingerprint":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaa","outcome":{"key":"subjective.day_rating",'
        '"mode":"green-vs-non-green"},"range":{"from":"2026-01-01",'
        '"kind":"bounded","to":"2026-07-23"}}'
    )
    assert hashlib.sha256(serialized.encode()).hexdigest() == (
        "6a3ed3b15975ada18d1535de6dad8719e9c1606bfd308b6a060439ac7b7e1803"
    )
    assert deterministic_seed(**_seed_arguments()) == 7_655_789_175_239_978_401


def test_seed_is_order_independent_but_mode_and_input_sensitive():
    components = [
        ("training.session", 1, 7),
        ("sleep.duration_hours", 0, 3),
    ]
    first = deterministic_seed(**_seed_arguments(components=components))
    second = deterministic_seed(**_seed_arguments(components=list(reversed(components))))
    assert first == second
    assert first != deterministic_seed(**_seed_arguments(
        components=components, outcome_mode="red-vs-non-red",
    ))
    assert first != deterministic_seed(**_seed_arguments(
        components=components, input_fingerprint="sha256:" + "b" * 64,
    ))

    # Transform is deliberately absent from the frozen seed tuple.
    point = deterministic_seed(**_seed_arguments())
    transformed = deterministic_seed(**_seed_arguments(components=[{
        "feature_key": "sleep.duration_hours",
        "lag_days": 1,
        "window_days": 1,
        "transform": "mean",
    }]))
    assert point == transformed


@pytest.mark.parametrize(
    "components",
    [
        [("feature", -1, 1)],
        [("feature", 0, 0)],
        [{"feature_key": "a", "exposure_key": "b", "lag_days": 0, "window_days": 1}],
    ],
)
def test_seed_rejects_invalid_components(components):
    with pytest.raises(StatsError):
        deterministic_seed(**_seed_arguments(components=components))


def test_permutation_reference_reproducibility_and_plus_one_formula():
    xs = [1, 2, 3, 4, 5, 6]
    ys = [1, 1, 2, 3, 5, 8]
    first = permutation_p_value(xs, ys, spearman, seed=12_345)
    second = permutation_p_value(xs, ys, spearman, seed=12_345)
    assert first == second == pytest.approx(0.005997001499250375)
    # A statistic invariant to permutation produces (1 + 2000) / 2001.
    assert permutation_p_value(
        xs, ys, lambda _xs, _ys: 1.0, seed=9,
    ) == 1.0


def test_finite_rank_permutations_match_every_validated_pearson_result():
    # Realistic tied ordinal exposures and binary day outcomes, including the
    # complete 2,000-draw stream rather than only its rounded final p-value.
    xs = average_ranks([index % 7 for index in range(108)])
    ys = average_ranks([int(index % 5 < 2) for index in range(108)])
    prepared = _rank_permutation_statistic(xs, ys)
    checked = 0

    def compare(left, right):
        nonlocal checked
        result = prepared(left, right)
        assert result == pearson(left, right)
        checked += 1
        return result

    actual = permutation_p_value(xs, ys, compare, seed=9182)
    assert checked == PERMUTATION_ITERATIONS + 1
    assert actual == permutation_p_value(xs, ys, pearson, seed=9182)


@pytest.mark.parametrize("size", [0, 1, 2, 31, 108, 365])
def test_prepared_shuffle_preserves_every_swap_and_random_state(size):
    reference = random.Random(9182)
    prepared = random.Random(9182)
    steps = stats._shuffle_steps(size)
    for _ in range(PERMUTATION_ITERATIONS):
        expected = list(range(size))
        actual = list(expected)
        reference.shuffle(expected)
        stats._shuffle_with_steps(actual, prepared.getrandbits, steps)
        assert actual == expected
    assert prepared.getstate() == reference.getstate()


@pytest.mark.parametrize("size,levels", [(2, 2), (31, 3), (108, 2), (365, 11)])
def test_scaled_rank_permutation_preserves_every_coefficient(monkeypatch, size, levels):
    left = average_ranks([index % 13 for index in range(size)])
    right = average_ranks([index % levels for index in range(size)])
    reference = random.Random(9182)
    expected = [pearson(left, right)]
    for _ in range(PERMUTATION_ITERATIONS):
        labels = list(right)
        reference.shuffle(labels)
        expected.append(pearson(left, labels))
    actual = []
    original = stats.permutation_p_value

    def capture(xs, ys, statistic, **options):
        def record(x, y):
            value = statistic(x, y)
            actual.append(value)
            return value
        return original(xs, ys, record, **options)

    monkeypatch.setattr(stats, "permutation_p_value", capture)
    result = stats._rank_permutation_p_value(left, right, seed=9182)
    assert actual == expected
    assert result == (1 + sum(abs(value) >= abs(expected[0])
                              for value in expected[1:])) / (PERMUTATION_ITERATIONS + 1)


@pytest.mark.parametrize("pairs", [
    [(float(index % 9), float(index % 3)) for index in range(35)],
    [(index / 7.0, float(index % 3)) for index in range(35)],
    [(-0.0, 1e308), (0.0, -1e308), (-1e308, 0.0), (1e308, -0.0)],
    [(1.0, 2.0)] * 4,
    [(1.0, 2.0)],
])
def test_prepared_bootstrap_preserves_ties_and_undefined_samples(pairs):
    prepared = stats._spearman_bootstrap_statistic(pairs)
    generator = random.Random(9182)
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample = [pairs[generator.randrange(len(pairs))] for _ in pairs]
        expected = spearman([pair[0] for pair in sample], [pair[1] for pair in sample])
        assert prepared(sample) == expected


def test_permutation_is_within_source_era_and_single_era_uses_whole_set():
    xs = [0, 0, 0, 0]
    ys = [0, 1, 10, 11]
    observed_permutations: list[tuple[int, ...]] = []

    def capture(_xs, labels):
        observed_permutations.append(tuple(labels))
        return 1.0

    permutation_p_value(
        xs, ys, capture, seed=77, strata=["a", "a", "b", "b"], iterations=20,
    )
    for labels in observed_permutations[1:]:
        assert set(labels[:2]) == {0, 1}
        assert set(labels[2:]) == {10, 11}

    no_strata = permutation_p_value(
        [1, 2, 3, 4, 5, 6], [1, 1, 2, 3, 5, 8],
        spearman, seed=12_345,
    )
    one_era = permutation_p_value(
        [1, 2, 3, 4, 5, 6], [1, 1, 2, 3, 5, 8],
        spearman, seed=12_345, strata=["only"] * 6,
    )
    assert one_era == no_strata


def test_permutation_rejects_bad_alignment_seed_and_undefined_statistics():
    with pytest.raises(StatsError, match="equal length"):
        permutation_p_value([1], [1, 2], spearman, seed=0)
    with pytest.raises(StatsError, match="unsigned 64-bit"):
        permutation_p_value([1], [1], lambda _x, _y: 1.0, seed=-1)
    with pytest.raises(StatsError, match="undefined"):
        permutation_p_value([1], [1], lambda _x, _y: None, seed=0)
    with pytest.raises(StatsError, match="strata"):
        permutation_p_value(
            [1, 2], [1, 2], lambda _x, _y: 1.0,
            seed=0, strata=["one"],
        )


def test_bootstrap_reference_reproducibility_and_exact_default_iterations():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    mean = lambda sample: sum(sample) / len(sample)
    first = bootstrap_interval(values, mean, seed=12_345)
    second = bootstrap_interval(values, mean, seed=12_345)
    assert first == second == pytest.approx((1.8, 4.2))

    calls = 0

    def counted_mean(sample):
        nonlocal calls
        calls += 1
        return sum(sample) / len(sample)

    bootstrap_interval(values, counted_mean, seed=1)
    assert calls == BOOTSTRAP_ITERATIONS


def test_bootstrap_rejects_empty_bad_seed_and_undefined_or_nonfinite_estimates():
    with pytest.raises(StatsError, match="aligned values"):
        bootstrap_interval([], lambda values: 0.0, seed=0)
    with pytest.raises(StatsError, match="unsigned 64-bit"):
        bootstrap_interval([1], lambda values: 1.0, seed=1 << 64)
    with pytest.raises(StatsError, match="undefined"):
        bootstrap_interval([1], lambda values: None, seed=0, iterations=1)
    with pytest.raises(StatsError, match="finite number"):
        bootstrap_interval(
            [1], lambda values: float("nan"), seed=0, iterations=1,
        )


def test_bh_reference_family_completeness_duplicates_and_bounds():
    p_values = [0.01, 0.04, 0.03, 0.20]
    assert benjamini_hochberg(p_values) == pytest.approx(
        [0.04, 0.05333333333333334, 0.05333333333333334, 0.20],
    )
    assert benjamini_hochberg([0.01, 0.01, 1.0, 0.0]) == pytest.approx(
        [0.013333333333333334, 0.013333333333333334, 1.0, 0.0],
    )
    assert benjamini_hochberg([]) == []
    with pytest.raises(StatsError, match="between zero and one"):
        benjamini_hochberg([0.1, 1.1])
    with pytest.raises(StatsError, match="finite number"):
        benjamini_hochberg([float("nan")])
