#!/usr/bin/env python3
"""Build or verify the integrity inventory for the current public release."""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import stat
import subprocess


MANIFEST = "RELEASE_MANIFEST.tsv"
FIELDS = ("path", "sha256", "bytes", "kind")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tracked(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode("utf-8").split("\0")
    return sorted(path for path in paths if path and path != MANIFEST)


def _regular_bytes(root: Path, relative: str) -> bytes:
    path = root / relative
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"manifest path is not a regular file: {relative}")
    path.resolve().relative_to(root.resolve())
    return path.read_bytes()


def _kind(relative: str) -> str:
    suffix = Path(relative).suffix.lower()
    return {
        ".css": "css",
        ".html": "html",
        ".js": "javascript",
        ".json": "json",
        ".md": "documentation",
        ".py": "python",
        ".service": "systemd-service",
        ".sql": "sql",
        ".timer": "systemd-timer",
        ".tsv": "tsv",
        ".txt": "dependency-list",
    }.get(suffix, "executable-or-config")


def build(root: Path) -> None:
    rows = []
    for relative in _tracked(root):
        data = _regular_bytes(root, relative)
        rows.append(
            {
                "path": relative,
                "sha256": _sha256(data),
                "bytes": str(len(data)),
                "kind": _kind(relative),
            }
        )
    with (root / MANIFEST).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDS,
            dialect="excel-tab",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def verify(root: Path) -> None:
    with (root / MANIFEST).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, dialect="excel-tab")
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise RuntimeError("release manifest header mismatch")
        rows = list(reader)
    paths = [row["path"] for row in rows]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RuntimeError("release manifest paths are not sorted and unique")
    tracked = _tracked(root)
    if paths != tracked:
        missing = sorted(set(tracked) - set(paths))
        extra = sorted(set(paths) - set(tracked))
        raise RuntimeError(f"manifest/tracked mismatch missing={missing} extra={extra}")
    for row in rows:
        data = _regular_bytes(root, row["path"])
        if row["sha256"] != _sha256(data) or row["bytes"] != str(len(data)):
            raise RuntimeError(f"content mismatch: {row['path']}")
    print(f"ok: {len(rows)} committed files match {MANIFEST}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "verify"))
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "build":
        build(root)
    else:
        verify(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
