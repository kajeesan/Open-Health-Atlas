"""Explicitly enabled, locally trusted optional rank-permutation accelerator.

Absent/incompatible libraries use Python. Imports and analysis never compile
or download anything; the separate, explicit build CLI uses a local compiler.
The receipt and self-check establish compatibility, not a sandbox for untrusted
native code: operators must trust the binary they explicitly configure.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import stat
import sys

ENVIRONMENT_VARIABLE = "HEALTH_STATS_NATIVE_LIBRARY"
FORMAT = "oha-rank-native-v1"
MAX_VALUES = 65_536
MAX_PRODUCTS = 262_144
MAX_DRAWS = 128
BUILD_FLAGS = ("-O3", "-std=c11", "-fPIC", "-ffp-contract=off", "-fno-fast-math")
SOURCE_FILES = (
    "_native_stats.py",
    "native/rank_products.c",
)


def source_build_identity() -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        digest.update(name.encode("ascii") + b"\0")
        digest.update((root / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def platform_identity() -> dict:
    return {"system": platform.system(), "machine": platform.machine(),
            "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
            "byteorder": sys.byteorder}


def _trusted_file(path: Path, *, limit: int) -> bytes:
    if not path.is_absolute() or path != path.resolve(strict=True):
        raise ValueError("native paths must be absolute and canonical")
    owners = {0, os.geteuid()}
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners:
            raise ValueError("untrusted native parent")
        writable = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        if writable and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError("writable native parent")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or info.st_size > limit):
            raise ValueError("untrusted native file")
        data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError("oversized native file")
        return data


def _state(generator: random.Random):
    version, words, gaussian = generator.getstate()
    if (version != 3 or len(words) != 625 or gaussian is not None
            or any(type(word) is not int or not 0 <= word <= 0xffffffff
                   for word in words[:624]) or not 0 <= words[624] <= 624):
        raise ValueError("unsupported Python random state")
    return (ctypes.c_uint32 * 625)(*words)


def _product_chunks(library, state, left, right, iterations):
    size = len(left)
    if not 2 <= size <= MAX_VALUES or len(right) != size or iterations < 1:
        raise ValueError("unsupported native dimensions")
    chunk_size = min(MAX_DRAWS, MAX_PRODUCTS // size)
    array_type = ctypes.c_double * size
    left_array, right_array = array_type(*left), array_type(*right)
    products = (ctypes.c_double * (size * chunk_size))()
    view = memoryview(products).cast("B").cast("d")
    for start in range(0, iterations, chunk_size):
        draws = min(chunk_size, iterations - start)
        if library.oha_rank_products(state, left_array, right_array, size, draws,
                                     products, len(products)) != 0:
            raise ValueError("native calculation failed")
        yield view[:size * draws]


def _self_check(library) -> None:
    # Distinct labels expose every swap; begin next to a twist rollover. Python
    # remains the reference for its current RNG and binary64 multiplication.
    for advance in (0, 623, 624):
        reference = random.Random(9182)
        for _ in range(advance):
            reference.getrandbits(5)
        state = _state(reference)
        left = [1.0] * 33
        right = list(map(float, range(33)))
        for chunk in _product_chunks(library, state, left, right, 129):
            for offset in range(0, len(chunk), 33):
                labels = list(right)
                reference.shuffle(labels)
                if list(chunk[offset:offset + 33]) != labels:
                    raise ValueError("native shuffle self-check failed")
        if tuple(state) != reference.getstate()[1]:
            raise ValueError("native state self-check failed")
    reference = random.Random(1)
    state = _state(reference)
    left = [(index - 53.5) / 53.5 for index in range(108)]
    right = [-1.0 if index % 3 else 0.7 for index in range(108)]
    for chunk in _product_chunks(library, state, left, right, 129):
        for offset in range(0, len(chunk), 108):
            labels = list(right)
            reference.shuffle(labels)
            expected = [a * b for a, b in zip(left, labels)]
            actual = chunk[offset:offset + 108]
            if ([value.hex() for value in actual] != [value.hex() for value in expected]
                    or math.fsum(actual).hex() != math.fsum(expected).hex()):
                raise ValueError("native arithmetic self-check failed")
    if tuple(state) != reference.getstate()[1]:
        raise ValueError("native state self-check failed")


@lru_cache(maxsize=1)
def _load(path_string: str):
    try:
        identity = platform_identity()
        suffix = {"Linux": ".so", "Darwin": ".dylib"}.get(identity["system"])
        path = Path(path_string)
        if (suffix is None or path.suffix != suffix or identity["pointer_bits"] != 64
                or sys.implementation.name != "cpython"):
            return None, "unsupported_platform"
        receipt = json.loads(_trusted_file(Path(str(path) + ".json"), limit=65_536))
        binary = _trusted_file(path, limit=16 * 1024 * 1024)
        build_id = source_build_identity()
        if (receipt.get("format") != FORMAT
                or any(receipt.get(key) != value for key, value in identity.items())
                or receipt.get("flags") != list(BUILD_FLAGS)
                or receipt.get("source_build_sha256") != build_id
                or receipt.get("library_sha256") != hashlib.sha256(binary).hexdigest()):
            return None, "identity_mismatch"
        # Only operator-trusted, checked bytes reach the native loader. Native
        # crashes cannot be caught by Python; this is not an untrusted-code API.
        library = ctypes.CDLL(str(path))
        library.oha_rank_abi.restype = ctypes.c_uint
        library.oha_pointer_bits.restype = ctypes.c_uint
        library.oha_source_build.restype = ctypes.c_char_p
        if (library.oha_rank_abi() != 1 or library.oha_pointer_bits() != 64
                or library.oha_source_build() != build_id.encode("ascii")):
            return None, "abi_mismatch"
        library.oha_rank_products.argtypes = [
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
        ]
        library.oha_rank_products.restype = ctypes.c_int
        _self_check(library)
        return library, "enabled"
    except (OSError, ValueError, TypeError, AttributeError, OverflowError,
            RecursionError, MemoryError):
        return None, "unavailable_or_untrusted"


def native_status() -> dict:
    path = os.environ.get(ENVIRONMENT_VARIABLE)
    if not path:
        return {"configured": False, "enabled": False, "reason": "not_configured"}
    library, reason = _load(path)
    return {"configured": True, "enabled": library is not None, "reason": reason}


def rank_permutation_p_value(left, right, *, left_sum, right_sum, seed, iterations):
    """Return an exact p-value or None to restart the original Python path."""
    path = os.environ.get(ENVIRONMENT_VARIABLE)
    if not path or not 2 <= len(left) <= MAX_VALUES:
        return None
    library, _reason = _load(path)
    if library is None:
        return None
    try:
        from .stats import _pearson_scaled, normalize_float
        observed = _pearson_scaled(left, right, left_sum=left_sum, right_sum=right_sum)
        if observed is None:
            return None
        denominator = math.sqrt(left_sum) * math.sqrt(right_sum)
        state = _state(random.Random(seed))
        exceedances = 0
        size = len(left)
        for chunk in _product_chunks(library, state, left, right, iterations):
            for offset in range(0, len(chunk), size):
                result = math.fsum(chunk[offset:offset + size]) / denominator
                if not math.isfinite(result):
                    return None
                result = normalize_float(max(-1.0, min(1.0, result)), name="correlation")
                exceedances += abs(result) >= abs(observed)
        return normalize_float((1 + exceedances) / (iterations + 1), name="permutation p-value")
    except (OSError, ValueError, TypeError, OverflowError, ArithmeticError, MemoryError):
        # State and partial counts are local. The caller restarts from its
        # original seed rather than continuing any partially consumed stream.
        return None


def build_main() -> None:
    """Explicit build CLI; no compiler is invoked by ordinary library loading."""
    import argparse
    import shutil
    import subprocess
    import tempfile

    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="absolute external .so/.dylib path")
    parser.add_argument("--cc", default="cc", help="existing local compiler; never installed")
    parser.add_argument("--sysroot", type=Path, help="existing SDK path, if required by the compiler")
    args = parser.parse_args()
    output = args.output
    identity = platform_identity()
    suffix = {"Linux": ".so", "Darwin": ".dylib"}.get(identity["system"])
    if (not output.is_absolute() or output.suffix != suffix or suffix is None
            or output.resolve().is_relative_to(root) or identity["pointer_bits"] != 64):
        parser.error("output must be an absolute external library path on supported 64-bit Linux/macOS")
    if output.exists() or Path(str(output) + ".json").exists():
        parser.error("output or receipt already exists; choose a new versioned path")
    compiler = shutil.which(args.cc)
    if not compiler:
        parser.error("requested compiler is not installed")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Validate the destination before invoking the explicitly requested compiler.
    with tempfile.NamedTemporaryFile(dir=output.parent) as probe:
        _trusted_file(Path(probe.name), limit=1)
    build_id = source_build_identity()
    with tempfile.TemporaryDirectory(prefix="oha-native-build-", dir=output.parent) as work:
        built = Path(work) / output.name
        command = [compiler, *BUILD_FLAGS,
                   "-shared" if suffix == ".so" else "-dynamiclib",
                   '-DOHA_SOURCE_BUILD="' + build_id + '"']
        if args.sysroot:
            command.extend(["-isysroot", str(args.sysroot)])
        command.extend([str(Path(__file__).resolve().parent / "native/rank_products.c"), "-o", str(built)])
        subprocess.run(command, check=True, timeout=120)
        os.chmod(built, 0o644)
        receipt = {"format": FORMAT, **identity, "flags": list(BUILD_FLAGS),
                   "source_build_sha256": build_id,
                   "library_sha256": hashlib.sha256(built.read_bytes()).hexdigest()}
        manifest = Path(str(built) + ".json")
        manifest.write_text(json.dumps(receipt, indent=2) + "\n")
        os.chmod(manifest, 0o644)
        library, reason = _load(str(built))
        if library is None:
            raise RuntimeError("built library failed validation: " + reason)
        # Refuse concurrent replacement. Both outputs are new operator paths.
        os.link(built, output)
        try:
            os.link(manifest, Path(str(output) + ".json"))
        except BaseException:
            output.unlink()
            raise
    print(json.dumps({"library": str(output), "receipt": str(output) + ".json", **receipt}))
