#!/usr/bin/env python3
"""Build one immutable private Recovery ancestry sidecar for a v5 snapshot."""

from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "toolkit"))

import health  # noqa: E402
from hermes_insights.contracts import canonical_json  # noqa: E402
from hermes_insights.readiness_ancestry import (  # noqa: E402
    ReadinessAncestryError,
    build_accepted_v5_sidecar,
)


def _iso(value: str, flag: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{flag} must be ISO YYYY-MM-DD") from exc


def _output_path(raw: str) -> Path:
    requested = Path(raw).expanduser()
    if not requested.is_absolute():
        raise ValueError("--output must be an absolute path")
    if requested.exists() or requested.is_symlink():
        raise ValueError("refusing to replace an existing or symlinked sidecar")
    try:
        parent = requested.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError("--output parent directory is unavailable") from exc
    if parent != requested.parent:
        raise ValueError("--output path must not traverse a symlink")
    if not parent.is_dir():
        raise ValueError("--output parent is not a directory")
    return requested


def _write_exclusive(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Destination was checked above and the temporary file is in the same
        # directory.  link() preserves refusal-to-replace atomically.
        os.link(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--from", dest="range_from", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    range_start = _iso(args.range_from, "--from")
    anchor = _iso(args.anchor, "--anchor")
    if range_start > anchor:
        parser.error("--from must not be after --anchor")
    try:
        output = _output_path(args.output)
        sidecar = build_accepted_v5_sidecar(
            args.database,
            range_start=range_start,
            anchor=anchor,
            calculation_context=health._readiness_calculation_context(),
        )
        _write_exclusive(output, (canonical_json(sidecar) + "\n").encode("utf-8"))
    except (ReadinessAncestryError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    # Deliberately print no path or private digest.  Those identities remain
    # confined to the private artifact and verifier.
    print(canonical_json({
        "ok": True,
        "contract": sidecar["contract"],
        "fixture_lane": sidecar["fixture_lane"],
        "schema_version": sidecar["snapshot"]["schema_version"],
        "scope": sidecar["scope"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
