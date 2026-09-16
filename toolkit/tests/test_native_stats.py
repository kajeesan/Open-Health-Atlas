"""Optional native checks; actual binary tests require an explicit test path."""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_insights import _native_stats as native, stats


@pytest.fixture(autouse=True)
def isolated_native_selection(monkeypatch):
    monkeypatch.delenv(native.ENVIRONMENT_VARIABLE, raising=False)
    loader = native._load
    loader.cache_clear()
    yield
    loader.cache_clear()


def test_disabled_native_never_loads_or_reads_files(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled native loader was used")
    monkeypatch.setattr(native, "_load", forbidden)
    assert native.native_status() == {
        "configured": False, "enabled": False, "reason": "not_configured",
    }
    assert stats._rank_permutation_p_value([1., 2., 3.], [3., 2., 1.], seed=7) > 0


def _fake_receipt(tmp_path):
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    path = tmp_path / ("rank_products" + suffix)
    path.write_bytes(b"not executable")
    receipt = {"format": native.FORMAT, **native.platform_identity(),
               "flags": list(native.BUILD_FLAGS),
               "source_build_sha256": native.source_build_identity(),
               "library_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return path, receipt


@pytest.mark.parametrize("field,value", [
    ("format", "unknown"), ("source_build_sha256", "wrong"),
    ("library_sha256", "wrong"), ("machine", "wrong"),
    ("pointer_bits", 32), ("flags", ["-ffast-math"]),
])
def test_identity_mismatch_is_rejected_before_native_load(tmp_path, monkeypatch, field, value):
    path, receipt = _fake_receipt(tmp_path)
    receipt[field] = value
    Path(str(path) + ".json").write_text(json.dumps(receipt))
    def forbidden(*args, **kwargs):
        raise AssertionError("identity mismatch reached native loader")
    monkeypatch.setattr(native.ctypes, "CDLL", forbidden)
    assert native._load(str(path)) == (None, "identity_mismatch")


@pytest.mark.parametrize("case", ["file_write", "receipt_write", "parent_write", "symlink"])
def test_untrusted_path_is_rejected_before_native_load(tmp_path, monkeypatch, case):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    path, receipt = _fake_receipt(directory)
    manifest = Path(str(path) + ".json")
    manifest.write_text(json.dumps(receipt))
    if case == "file_write":
        path.chmod(0o666)
    elif case == "receipt_write":
        manifest.chmod(0o666)
    elif case == "parent_write":
        directory.chmod(0o777)
    else:
        target = directory / "actual"
        path.rename(target)
        path.symlink_to(target)
    def forbidden(*args, **kwargs):
        raise AssertionError("untrusted path reached native loader")
    monkeypatch.setattr(native.ctypes, "CDLL", forbidden)
    try:
        assert native._load(str(path)) == (None, "unavailable_or_untrusted")
    finally:
        directory.chmod(0o700)


def test_bad_random_state_is_rejected():
    class Invalid:
        def getstate(self):
            return 3, (0,) * 624 + (625,), None
    with pytest.raises(ValueError, match="random state"):
        native._state(Invalid())


def test_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "receipt"
    os.mkfifo(path, mode=0o600)
    with pytest.raises(ValueError, match="untrusted native file"):
        native._trusted_file(path, limit=65_536)


def test_deeply_nested_receipt_falls_back_before_native_load(tmp_path, monkeypatch):
    path, _receipt = _fake_receipt(tmp_path)
    Path(str(path) + ".json").write_text("[" * 2000 + "0" + "]" * 2000)
    def forbidden(*args, **kwargs):
        raise AssertionError("malformed receipt reached native loader")
    monkeypatch.setattr(native.ctypes, "CDLL", forbidden)
    assert native._load(str(path)) == (None, "unavailable_or_untrusted")


@pytest.mark.parametrize("field,value", [
    ("oha_rank_abi", 0), ("oha_pointer_bits", 32), ("oha_source_build", b"wrong"),
])
def test_loaded_binary_metadata_must_match_receipt(tmp_path, monkeypatch, field, value):
    path, receipt = _fake_receipt(tmp_path)
    Path(str(path) + ".json").write_text(json.dumps(receipt))
    class Function:
        def __init__(self, value):
            self.value = value
        def __call__(self):
            return self.value
    fields = {"oha_rank_abi": 1, "oha_pointer_bits": 64,
              "oha_source_build": native.source_build_identity().encode("ascii")}
    fields[field] = value
    library = SimpleNamespace(**{key: Function(val) for key, val in fields.items()})
    monkeypatch.setattr(native.ctypes, "CDLL", lambda _path: library)
    assert native._load(str(path)) == (None, "abi_mismatch")


@pytest.fixture
def library(monkeypatch):
    path = os.environ.get("OHA_TEST_NATIVE_LIBRARY")
    if not path:
        pytest.skip("explicit OHA_TEST_NATIVE_LIBRARY not supplied; Python is the default")
    monkeypatch.setenv(native.ENVIRONMENT_VARIABLE, path)
    result, reason = native._load(path)
    assert result is not None, reason
    assert native.native_status()["enabled"] is True
    return result


@pytest.mark.parametrize("size,seed,advance", [
    (2, 0, 0), (31, (1 << 32) - 1, 623), (32, 1 << 32, 624),
    (33, (1 << 64) - 1, 625), (108, 9182, 622), (365, 9182, 1248),
    (625, 1, 1),
])
def test_native_preserves_complete_swap_stream_and_state(library, size, seed, advance):
    reference = random.Random(seed)
    for _ in range(advance):
        reference.getrandbits(5)
    state = native._state(reference)
    right = list(map(float, range(size)))
    checked = 0
    for chunk in native._product_chunks(library, state, [1.] * size, right, 2000):
        assert len(chunk) <= native.MAX_PRODUCTS
        for offset in range(0, len(chunk), size):
            expected = list(right)
            reference.shuffle(expected)
            assert list(chunk[offset:offset + size]) == expected
            checked += 1
    assert checked == 2000
    assert tuple(state) == reference.getstate()[1]


@pytest.mark.parametrize("size,levels", [(31, 2), (108, 3), (365, 11)])
def test_native_preserves_all_coefficients_and_p_values(library, monkeypatch, size, levels):
    xs = stats.average_ranks([index % 13 for index in range(size)])
    ys = stats.average_ranks([index % levels for index in range(size)])
    mx, my = math.fsum(xs) / size, math.fsum(ys) / size
    x = [value - mx for value in xs]
    y = [value - my for value in ys]
    sx, sy = max(map(abs, x)), max(map(abs, y))
    x, y = [value / sx for value in x], [value / sy for value in y]
    denominator = math.sqrt(math.fsum(v * v for v in x)) * math.sqrt(math.fsum(v * v for v in y))
    reference = random.Random(9182)
    state = native._state(reference)
    checked = 0
    for chunk in native._product_chunks(library, state, x, y, 2000):
        for offset in range(0, len(chunk), size):
            labels = list(ys)
            reference.shuffle(labels)
            actual = stats.normalize_float(max(-1., min(1., math.fsum(chunk[offset:offset + size]) / denominator)))
            assert actual.hex() == stats.pearson(xs, labels).hex()
            checked += 1
    assert checked == 2000
    assert tuple(state) == reference.getstate()[1]
    result = stats._rank_permutation_p_value(xs, ys, seed=9182)
    monkeypatch.delenv(native.ENVIRONMENT_VARIABLE)
    assert result == stats._rank_permutation_p_value(xs, ys, seed=9182)


def test_failure_after_native_chunk_restarts_complete_python_stream(library, monkeypatch):
    xs = stats.average_ranks([index % 7 for index in range(108)])
    ys = stats.average_ranks([index % 3 for index in range(108)])
    original_chunks = native._product_chunks
    attempted = []
    def fail_after_chunk(*args, **kwargs):
        for chunk in original_chunks(*args, **kwargs):
            attempted.append(len(chunk))
            yield chunk
            raise ValueError("simulated later chunk failure")
    monkeypatch.setattr(native, "_product_chunks", fail_after_chunk)
    result = stats._rank_permutation_p_value(xs, ys, seed=9182)
    assert attempted == [108 * 128]
    monkeypatch.delenv(native.ENVIRONMENT_VARIABLE)
    assert result == stats._rank_permutation_p_value(xs, ys, seed=9182)


def test_native_c_checks_bounds_before_mutating_state(library):
    state = native._state(random.Random(9182))
    original = tuple(state)
    values = (ctypes.c_double * 2)(1., 2.)
    products = (ctypes.c_double * 2)()
    for size, draws, capacity in [(0, 1, 2), (2, 0, 2), (2, 129, 2),
                                   (2, 2, 2), (native.MAX_VALUES + 1, 1, 2),
                                   (2, 1, native.MAX_PRODUCTS + 1),
                                   (ctypes.c_size_t(-1).value, 1, 2)]:
        assert library.oha_rank_products(state, values, values, size, draws, products, capacity) != 0
        assert tuple(state) == original


def test_native_maximum_buffer_is_chunked_without_changing_stream(library):
    size = native.MAX_VALUES
    reference = random.Random(9182)
    state = native._state(reference)
    right = list(map(float, range(size)))
    sizes = []
    for chunk in native._product_chunks(library, state, [1.] * size, right, 5):
        sizes.append(len(chunk))
        for offset in range(0, len(chunk), size):
            expected = list(right)
            reference.shuffle(expected)
            assert list(chunk[offset:offset + size]) == expected
    assert sizes == [native.MAX_PRODUCTS, size]
    assert tuple(state) == reference.getstate()[1]


def test_constant_short_and_stratified_paths_remain_python(library, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported input dispatched to native")
    monkeypatch.setattr(native, "_product_chunks", forbidden)
    for xs, ys in [([1.], [1.]), ([1., 1.], [1., 2.]), ([1., 2.], [1., 1.])]:
        with pytest.raises(stats.StatsError, match="undefined"):
            stats._rank_permutation_p_value(xs, ys, seed=1)
    assert stats.permutation_p_value(
        [1, 2, 3, 4], [4, 3, 2, 1], stats.pearson,
        seed=9182, strata=["a", "a", "b", "b"], iterations=2000,
    ) > 0
