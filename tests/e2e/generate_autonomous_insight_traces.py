"""Regenerate canonical Phase 8 fixture and live-replay digest pins."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tempfile

# The frozen Phase 8 scenarios use one fictional civil-time contract. Keep the
# standalone generator identical to the pytest environment regardless of a
# caller's deployment timezone.
os.environ["HERMES_TIMEZONE"] = "Europe/Paris"

from tests.e2e.autonomous_trace_harness import (
    FIXTURE_ROOT,
    TRACE_CONTRACT,
    build_trace,
    canonical_json,
    fixture_paths,
    load_fixture,
    sha256_json,
)


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def regenerate(*, check: bool) -> list[dict[str, str]]:
    generated: list[tuple[Path, dict, str]] = []
    with tempfile.TemporaryDirectory(prefix="hermes-phase8-traces-") as raw:
        temporary_root = Path(raw)
        for fixture_path in fixture_paths():
            fixture = load_fixture(fixture_path)
            trace = build_trace(
                fixture,
                temporary_root / f"{fixture['scenario_id']}.db",
            )
            trace_sha256 = sha256_json(trace)
            fixture["expected_trace_sha256"] = trace_sha256
            generated.append((fixture_path, fixture, trace_sha256))

    manifest_rows: list[dict[str, str]] = []
    for fixture_path, fixture, trace_sha256 in generated:
        fixture_bytes = (canonical_json(fixture) + "\n").encode("utf-8")
        if check:
            if fixture_path.read_bytes() != fixture_bytes:
                raise SystemExit(f"fixture drift: {fixture_path.name}")
        else:
            fixture_path.write_bytes(fixture_bytes)
        manifest_rows.append({
            "fixture_file": fixture_path.name,
            "fixture_sha256": (
                "sha256:" + hashlib.sha256(fixture_bytes).hexdigest()
            ),
            "status": fixture["expected_status"],
            "trace_sha256": trace_sha256,
        })

    manifest = {
        "generated_by": "tests/e2e/generate_autonomous_insight_traces.py",
        "scenarios": manifest_rows,
        "trace_contract": TRACE_CONTRACT,
    }
    manifest_path = FIXTURE_ROOT / "manifest.json"
    manifest_bytes = (canonical_json(manifest) + "\n").encode("utf-8")
    if check:
        if manifest_path.read_bytes() != manifest_bytes:
            raise SystemExit("trace manifest drift")
        for item in manifest_rows:
            fixture_path = FIXTURE_ROOT / item["fixture_file"]
            if _file_sha256(fixture_path) != item["fixture_sha256"]:
                raise SystemExit(f"fixture hash drift: {fixture_path.name}")
    else:
        manifest_path.write_bytes(manifest_bytes)
    return manifest_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="replay and byte-compare without writing artifacts",
    )
    args = parser.parse_args()
    rows = regenerate(check=args.check)
    for row in rows:
        print(
            f"{row['fixture_file']} {row['status']} "
            f"{row['trace_sha256']}"
        )


if __name__ == "__main__":
    main()
