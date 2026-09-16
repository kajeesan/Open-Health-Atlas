"""Deterministic standard-library statistics for ``outcome-v1``.

This module deliberately owns no database or feature-registry behavior.  It
accepts already validated/aligned observations and implements the frozen Phase
4 numerical primitives.  Every randomized operation constructs a candidate-
local ``random.Random`` instance, so traversal order and result truncation
cannot change its draws.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from collections import Counter
import hashlib
import math
from operator import mul
import random
from statistics import NormalDist
from typing import Any, TypeVar

from .contracts import ContractError, canonical_json
from ._native_stats import rank_permutation_p_value as _native_rank_permutation_p_value


ANALYSIS_VERSION = "outcome-v1"
PERMUTATION_ITERATIONS = 2_000
BOOTSTRAP_ITERATIONS = 2_000
_SEED_LIMIT = 1 << 64

T = TypeVar("T")
X = TypeVar("X")
Y = TypeVar("Y")


class StatsError(ValueError):
    """Raised when an input or computed statistic violates the contract."""


def finite_float(value: object, *, name: str = "value") -> float:
    """Return a finite float, normalizing both signs of zero to ``0.0``."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatsError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise StatsError(f"{name} must be a finite number") from None
    if not math.isfinite(result):
        raise StatsError(f"{name} must be a finite number")
    return 0.0 if result == 0.0 else result


def normalize_float(value: object, *, name: str = "value") -> float:
    """Public spelling for structured finite-float normalization."""

    return finite_float(value, name=name)


def _finite_series(values: Sequence[object], *, name: str) -> list[float]:
    return [
        finite_float(value, name=f"{name}[{index}]")
        for index, value in enumerate(values)
    ]


def average_ranks(values: Sequence[object]) -> list[float]:
    """Return one-based average ranks, assigning tied values their mean rank."""

    numeric = _finite_series(values, name="values")
    order = sorted(range(len(numeric)), key=numeric.__getitem__)
    ranks = [0.0] * len(numeric)
    cursor = 0
    while cursor < len(order):
        end = cursor
        while (
            end + 1 < len(order)
            and numeric[order[end + 1]] == numeric[order[cursor]]
        ):
            end += 1
        rank = (cursor + end) / 2.0 + 1.0
        for position in range(cursor, end + 1):
            ranks[order[position]] = rank
        cursor = end + 1
    return ranks


def pearson(xs: Sequence[object], ys: Sequence[object]) -> float | None:
    """Return Pearson correlation, or ``None`` for a short/constant series."""

    if len(xs) != len(ys):
        raise StatsError("correlation series must have equal length")
    if len(xs) < 2:
        return None
    left = _finite_series(xs, name="xs")
    right = _finite_series(ys, name="ys")
    return _pearson_finite(left, right)


def _pearson_finite(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Correlate aligned finite series of length >= 2 already validated locally."""

    try:
        mean_left = math.fsum(left) / len(left)
        mean_right = math.fsum(right) / len(right)
        centered_left = [value - mean_left for value in left]
        centered_right = [value - mean_right for value in right]
    except (OverflowError, ValueError):
        raise StatsError("correlation calculation was non-finite") from None
    scale_left = max(map(abs, centered_left))
    scale_right = max(map(abs, centered_right))
    if scale_left == 0.0 or scale_right == 0.0:
        return None
    scaled_left = [value / scale_left for value in centered_left]
    scaled_right = [value / scale_right for value in centered_right]
    return _pearson_scaled(scaled_left, scaled_right)


def _pearson_scaled(
    scaled_left: Sequence[float],
    scaled_right: Sequence[float],
    *,
    left_sum: float | None = None,
    right_sum: float | None = None,
) -> float | None:
    numerator = math.fsum(map(mul, scaled_left, scaled_right))
    if left_sum is None:
        left_sum = math.fsum(value * value for value in scaled_left)
    if right_sum is None:
        right_sum = math.fsum(value * value for value in scaled_right)
    denominator = math.sqrt(left_sum) * math.sqrt(right_sum)
    if denominator == 0.0 or not math.isfinite(denominator):
        return None
    result = numerator / denominator
    if not math.isfinite(result):
        raise StatsError("correlation calculation was non-finite")
    # Roundoff may place a mathematically bounded correlation a few ulps outside
    # [-1, 1].  Clamping preserves that invariant without changing valid values.
    return normalize_float(max(-1.0, min(1.0, result)), name="correlation")


def _rank_permutation_statistic(
    left: Sequence[float], right: Sequence[float],
) -> Callable[[Sequence[float], Sequence[float]], float | None]:
    """Prepare Pearson for fixed finite ranks and permutations of the right ranks."""

    mean_left = math.fsum(left) / len(left)
    centered_left = [value - mean_left for value in left]
    scale_left = max(map(abs, centered_left))
    mean_right = math.fsum(right) / len(right)
    centered_right = [value - mean_right for value in right]
    scale_right = max(map(abs, centered_right))
    if scale_left == 0.0 or scale_right == 0.0:
        return _pearson_finite
    scaled_left = [value / scale_left for value in centered_left]
    left_sum = math.fsum(value * value for value in scaled_left)
    right_values = {
        value: (value - mean_right) / scale_right for value in right
    }
    scaled_right = [right_values[value] for value in right]
    right_sum = math.fsum(value * value for value in scaled_right)

    def statistic(_left: Sequence[float], permuted: Sequence[float]) -> float | None:
        # Retain the original ordered mean calculation; only equal means reuse
        # scaling and variance. Squared scaled ranks are a bounded nonnegative
        # multiset, unchanged by permutation. Cross-products follow draw order.
        if math.fsum(permuted) / len(permuted) != mean_right:
            return _pearson_finite(left, permuted)
        scaled_right = [right_values[value] for value in permuted]
        return _pearson_scaled(
            scaled_left, scaled_right, left_sum=left_sum, right_sum=right_sum,
        )

    return statistic


def _rank_permutation_p_value(
    left: Sequence[float], right: Sequence[float], *, seed: int,
) -> float:
    """Permute fixed ranks after their exact, order-independent scaling."""

    size = len(right)
    # Half-integer ranks in this bound have an exactly representable sum in
    # every order. Thus every permuted mean and scaled value is identical to
    # _rank_permutation_statistic's result. Keep its fallback for other inputs.
    if (
        size < 2 or size > 1 << 26 or len(left) != size
        or any(not 0 < value <= size or value * 2 != int(value * 2)
               for value in right)
    ):
        return permutation_p_value(
            left, right, _rank_permutation_statistic(left, right), seed=seed,
        )
    mean_left = math.fsum(left) / size
    mean_right = math.fsum(right) / size
    centered_left = [value - mean_left for value in left]
    centered_right = [value - mean_right for value in right]
    scale_left = max(map(abs, centered_left))
    scale_right = max(map(abs, centered_right))
    if scale_left == 0.0 or scale_right == 0.0:
        return permutation_p_value(
            left, right, _rank_permutation_statistic(left, right), seed=seed,
        )
    scaled_left = [value / scale_left for value in centered_left]
    scaled_right = [value / scale_right for value in centered_right]
    left_sum = math.fsum(value * value for value in scaled_left)
    right_sum = math.fsum(value * value for value in scaled_right)

    def statistic(_left: Sequence[float], permuted: Sequence[float]) -> float | None:
        return _pearson_scaled(
            scaled_left, permuted, left_sum=left_sum, right_sum=right_sum,
        )

    native = _native_rank_permutation_p_value(
        scaled_left, scaled_right, left_sum=left_sum, right_sum=right_sum,
        seed=_seed(seed), iterations=PERMUTATION_ITERATIONS,
    )
    if native is not None:
        return native
    return permutation_p_value(scaled_left, scaled_right, statistic, seed=seed)


def spearman(xs: Sequence[object], ys: Sequence[object]) -> float | None:
    """Return average-tie Spearman correlation."""

    if len(xs) != len(ys):
        raise StatsError("correlation series must have equal length")
    return pearson(average_ranks(xs), average_ranks(ys))


def _spearman_bootstrap_statistic(
    pairs: Sequence[tuple[float, float]],
) -> Callable[[Sequence[tuple[float, float]]], float | None]:
    """Prepare ranks for resamples of these already validated finite pairs."""

    left_order = sorted({pair[0] for pair in pairs})
    right_order = sorted({pair[1] for pair in pairs})

    def ranks(values: list[float], order: list[float]) -> list[float]:
        counts = Counter(values)
        assigned = {}
        cursor = 0
        for value in order:
            count = counts[value]
            if count:
                # This is average_ranks' original expression, with the same
                # sorted start/end positions; absent values occupy no ranks.
                assigned[value] = (cursor + cursor + count - 1) / 2.0 + 1.0
                cursor += count
        return [assigned[value] for value in values]

    def statistic(sample: Sequence[tuple[float, float]]) -> float | None:
        if len(sample) < 2:
            return None
        left = ranks([pair[0] for pair in sample], left_order)
        right = ranks([pair[1] for pair in sample], right_order)
        return _pearson_finite(left, right)

    return statistic


def _count(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StatsError(f"{name} must be a nonnegative integer")
    return value


def fisher_exact_two_sided(
    table: Sequence[Sequence[int]],
) -> float:
    """Return the fixed-margin two-sided Fisher exact p-value.

    The table is ``[[exposed_positive, exposed_negative],
    [unexposed_positive, unexposed_negative]]``.  Integer hypergeometric weights
    make the ``probability <= observed probability`` inclusion comparison exact;
    only the final division is floating point.
    """

    if (
        len(table) != 2
        or any(not isinstance(row, Sequence) or len(row) != 2 for row in table)
    ):
        raise StatsError("Fisher table must be 2x2")
    a = _count(table[0][0], name="table[0][0]")
    b = _count(table[0][1], name="table[0][1]")
    c = _count(table[1][0], name="table[1][0]")
    d = _count(table[1][1], name="table[1][1]")
    exposed_total = a + b
    positive_total = a + c
    negative_total = b + d
    total = exposed_total + c + d
    low = max(0, exposed_total - negative_total)
    high = min(exposed_total, positive_total)
    denominator = math.comb(total, exposed_total)
    observed_weight = (
        math.comb(positive_total, a)
        * math.comb(negative_total, exposed_total - a)
    )
    current = (
        math.comb(positive_total, low)
        * math.comb(negative_total, exposed_total - low)
    )
    selected_weight = 0
    for possible in range(low, high + 1):
        if current <= observed_weight:
            selected_weight += current
        if possible < high:
            numerator = (
                current
                * (positive_total - possible)
                * (exposed_total - possible)
            )
            divisor = (
                (possible + 1)
                * (negative_total - exposed_total + possible + 1)
            )
            current, remainder = divmod(numerator, divisor)
            if remainder:
                raise StatsError("Fisher exact recurrence lost integer precision")
    return normalize_float(
        min(1.0, selected_weight / denominator), name="Fisher p-value",
    )


def _probability(value: object, *, name: str) -> float:
    result = finite_float(value, name=name)
    if not 0.0 <= result <= 1.0:
        raise StatsError(f"{name} must be between zero and one")
    return result


def wilson_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return a Wilson score interval without continuity correction."""

    successes = _count(successes, name="successes")
    total = _count(total, name="total")
    if total == 0:
        raise StatsError("Wilson interval requires a positive total")
    if successes > total:
        raise StatsError("successes must not exceed total")
    confidence_value = finite_float(confidence, name="confidence")
    if not 0.0 < confidence_value < 1.0:
        raise StatsError("confidence must be strictly between zero and one")
    z_value = NormalDist().inv_cdf(0.5 + confidence_value / 2.0)
    proportion = successes / total
    z_squared = z_value * z_value
    denominator = 1.0 + z_squared / total
    center = (proportion + z_squared / (2.0 * total)) / denominator
    half_width = (
        z_value
        * math.sqrt(
            proportion * (1.0 - proportion) / total
            + z_squared / (4.0 * total * total)
        )
        / denominator
    )
    # Pin the mathematically exact endpoints. Different Python/libm versions can
    # otherwise leave a tiny floating-point residue (for example ~2.8e-17 for
    # the lower bound when there are zero successes).
    lower = 0.0 if successes == 0 else max(0.0, center - half_width)
    upper = 1.0 if successes == total else min(1.0, center + half_width)
    return (
        normalize_float(lower, name="Wilson lower"),
        normalize_float(upper, name="Wilson upper"),
    )


def newcombe_risk_difference_interval(
    exposed_positive: int,
    exposed_total: int,
    unexposed_positive: int,
    unexposed_total: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return Newcombe's hybrid-score interval for two independent risks."""

    exposed_low, exposed_high = wilson_interval(
        exposed_positive, exposed_total, confidence,
    )
    unexposed_low, unexposed_high = wilson_interval(
        unexposed_positive, unexposed_total, confidence,
    )
    exposed_rate = exposed_positive / exposed_total
    unexposed_rate = unexposed_positive / unexposed_total
    difference = exposed_rate - unexposed_rate
    lower = difference - math.sqrt(
        (exposed_rate - exposed_low) ** 2
        + (unexposed_high - unexposed_rate) ** 2
    )
    upper = difference + math.sqrt(
        (exposed_high - exposed_rate) ** 2
        + (unexposed_rate - unexposed_low) ** 2
    )
    return (
        normalize_float(max(-1.0, lower), name="Newcombe lower"),
        normalize_float(min(1.0, upper), name="Newcombe upper"),
    )


def percentile(values: Sequence[object], probability: float) -> float:
    """Return a linearly interpolated percentile at ``h=(n-1)*p``."""

    if not values:
        raise StatsError("percentile requires at least one value")
    p_value = _probability(probability, name="probability")
    ordered = sorted(_finite_series(values, name="values"))
    position = (len(ordered) - 1) * p_value
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return normalize_float(ordered[lower], name="percentile")
    result = (
        ordered[lower]
        + (ordered[upper] - ordered[lower]) * (position - lower)
    )
    return normalize_float(result, name="percentile")


def interquartile_range(values: Sequence[object]) -> float:
    """Return Q3-Q1 using the contract's linear percentile interpolation."""

    return normalize_float(
        percentile(values, 0.75) - percentile(values, 0.25),
        name="interquartile range",
    )


def _iterations(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StatsError(f"{name} must be a positive integer")
    return value


def _seed(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value < _SEED_LIMIT
    ):
        raise StatsError("seed must be an unsigned 64-bit integer")
    return value


def _computed_statistic(value: object, *, name: str) -> float:
    if value is None:
        raise StatsError(f"{name} was undefined")
    return finite_float(value, name=name)


def _shuffle_steps(size: int) -> list[tuple[int, int, int]]:
    return [(index, index + 1, (index + 1).bit_length())
            for index in range(size - 1, 0, -1)]


def _shuffle_with_steps(
    values: list[T],
    getrandbits: Callable[[int], int],
    steps: Sequence[tuple[int, int, int]],
) -> None:
    """The Random.shuffle swaps and rejection draws, with fixed bounds reused."""

    for index, bound, bits in steps:
        selected = getrandbits(bits)
        while selected >= bound:
            selected = getrandbits(bits)
        values[index], values[selected] = values[selected], values[index]


def permutation_p_value(
    xs: Sequence[X],
    ys: Sequence[Y],
    statistic: Callable[[Sequence[X], Sequence[Y]], float | None],
    *,
    seed: int,
    strata: Sequence[object] | None = None,
    iterations: int = PERMUTATION_ITERATIONS,
) -> float:
    """Return a deterministic two-sided permutation p-value.

    ``ys`` are permuted relative to fixed ``xs``.  When two or more strata are
    present, labels are shuffled independently within each stratum.  A single
    stratum intentionally takes the whole-set path required by the contract.
    """

    if len(xs) != len(ys):
        raise StatsError("permutation series must have equal length")
    if not xs:
        raise StatsError("permutation test requires aligned values")
    iteration_count = _iterations(iterations, name="iterations")
    generator = random.Random(_seed(seed))
    observed = _computed_statistic(statistic(xs, ys), name="observed statistic")

    grouped_indices: list[list[int]]
    if strata is None:
        grouped_indices = [list(range(len(ys)))]
    else:
        if len(strata) != len(ys):
            raise StatsError("strata must have the aligned series length")
        groups: dict[str, list[int]] = {}
        for index, value in enumerate(strata):
            try:
                key = canonical_json(value)
            except ContractError as exc:
                raise StatsError(f"invalid stratum at index {index}: {exc}") from exc
            groups.setdefault(key, []).append(index)
        grouped_indices = (
            [list(range(len(ys)))]
            if len(groups) <= 1
            else [groups[key] for key in sorted(groups)]
        )

    shuffle_steps = [_shuffle_steps(len(indices)) for indices in grouped_indices]
    getrandbits = generator.getrandbits
    exceedances = 0
    for iteration in range(iteration_count):
        permuted = list(ys)
        if len(grouped_indices) == 1:
            # The sole group is the original complete index range. Shuffle the
            # same labels directly, preserving every random draw and swap.
            _shuffle_with_steps(permuted, getrandbits, shuffle_steps[0])
        else:
            for indices, steps in zip(grouped_indices, shuffle_steps):
                labels = [ys[index] for index in indices]
                _shuffle_with_steps(labels, getrandbits, steps)
                for index, label in zip(indices, labels):
                    permuted[index] = label
        permuted_statistic = _computed_statistic(
            statistic(xs, permuted),
            name=f"permuted statistic {iteration}",
        )
        if abs(permuted_statistic) >= abs(observed):
            exceedances += 1
    return normalize_float(
        (1 + exceedances) / (iteration_count + 1),
        name="permutation p-value",
    )


def bootstrap_interval(
    values: Sequence[T],
    statistic: Callable[[Sequence[T]], float | None],
    *,
    seed: int,
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return a deterministic percentile bootstrap interval."""

    if not values:
        raise StatsError("bootstrap requires aligned values")
    iteration_count = _iterations(iterations, name="iterations")
    confidence_value = finite_float(confidence, name="confidence")
    if not 0.0 < confidence_value < 1.0:
        raise StatsError("confidence must be strictly between zero and one")
    generator = random.Random(_seed(seed))
    sample_size = len(values)
    estimates: list[float] = []
    for iteration in range(iteration_count):
        sample = [
            values[generator.randrange(sample_size)]
            for _ in range(sample_size)
        ]
        estimates.append(_computed_statistic(
            statistic(sample), name=f"bootstrap statistic {iteration}",
        ))
    alpha = 1.0 - confidence_value
    return (
        percentile(estimates, alpha / 2.0),
        percentile(estimates, 1.0 - alpha / 2.0),
    )


def benjamini_hochberg(p_values: Sequence[object]) -> list[float]:
    """Return standard BH q-values via a reverse cumulative minimum."""

    probabilities = [
        _probability(value, name=f"p_values[{index}]")
        for index, value in enumerate(p_values)
    ]
    family_size = len(probabilities)
    if family_size == 0:
        return []
    ordered = sorted(
        enumerate(probabilities), key=lambda item: (item[1], item[0]),
    )
    adjusted = [1.0] * family_size
    running = 1.0
    for position in range(family_size - 1, -1, -1):
        original_index, probability = ordered[position]
        rank = position + 1
        running = min(running, probability * family_size / rank, 1.0)
        adjusted[original_index] = normalize_float(running, name="BH q-value")
    return adjusted


def _component_tuple(component: object) -> tuple[str, int, int]:
    if isinstance(component, Mapping):
        feature_key = component.get("feature_key")
        exposure_key = component.get("exposure_key")
        if feature_key is not None and exposure_key is not None:
            raise StatsError("seed component must not contain two feature keys")
        key = feature_key if feature_key is not None else exposure_key
        lag = component.get("lag_days")
        window = component.get("window_days")
    elif (
        isinstance(component, Sequence)
        and not isinstance(component, (str, bytes, bytearray))
        and len(component) == 3
    ):
        key, lag, window = component
    else:
        raise StatsError("seed component must be a mapping or three-item tuple")
    if not isinstance(key, str) or not key:
        raise StatsError("seed component feature key must be non-empty text")
    if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
        raise StatsError("seed component lag_days must be nonnegative")
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise StatsError("seed component window_days must be positive")
    return key, lag, window


def deterministic_seed_material(
    *,
    analysis_version: str,
    outcome_key: str,
    outcome_mode: str,
    components: Sequence[object],
    analysis_range: object,
    input_fingerprint: str,
) -> dict[str, Any]:
    """Return the documented canonical seed envelope.

    The frozen seed tuple intentionally excludes candidate transform.  Outcome
    mode is carried inside the contract's ``outcome`` object so separate
    day-rating contrasts do not share seed material.
    """

    for name, value in (
        ("analysis_version", analysis_version),
        ("outcome_key", outcome_key),
        ("outcome_mode", outcome_mode),
        ("input_fingerprint", input_fingerprint),
    ):
        if not isinstance(value, str) or not value:
            raise StatsError(f"{name} must be non-empty text")
    normalized_components = sorted(
        (_component_tuple(component) for component in components),
        key=lambda item: (item[0], item[1], item[2]),
    )
    material = {
        "analysis_version": analysis_version,
        "outcome": {"key": outcome_key, "mode": outcome_mode},
        "components": [
            {
                "feature_key": feature_key,
                "lag_days": lag_days,
                "window_days": window_days,
            }
            for feature_key, lag_days, window_days in normalized_components
        ],
        "range": analysis_range,
        "input_fingerprint": input_fingerprint,
    }
    try:
        # Validate the complete envelope here rather than allowing hash callers
        # to discover an unsupported/non-finite range value later.
        canonical_json(material)
    except ContractError as exc:
        raise StatsError(f"seed material is not canonical: {exc}") from exc
    return material


def deterministic_seed(
    *,
    analysis_version: str,
    outcome_key: str,
    outcome_mode: str,
    components: Sequence[object],
    analysis_range: object,
    input_fingerprint: str,
) -> int:
    """Return the first 64 bits of SHA-256 over canonical seed material."""

    material = deterministic_seed_material(
        analysis_version=analysis_version,
        outcome_key=outcome_key,
        outcome_mode=outcome_mode,
        components=components,
        analysis_range=analysis_range,
        input_fingerprint=input_fingerprint,
    )
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


__all__ = [
    "ANALYSIS_VERSION", "BOOTSTRAP_ITERATIONS", "PERMUTATION_ITERATIONS",
    "StatsError", "average_ranks", "benjamini_hochberg",
    "bootstrap_interval", "deterministic_seed", "deterministic_seed_material",
    "finite_float", "fisher_exact_two_sided", "interquartile_range",
    "newcombe_risk_difference_interval", "normalize_float", "pearson",
    "percentile", "permutation_p_value", "spearman", "wilson_interval",
]
