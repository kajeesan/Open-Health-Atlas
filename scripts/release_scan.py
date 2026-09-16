#!/usr/bin/env python3
"""Fail closed on release-boundary privacy and repository hazards.

Output contains categories and repository-relative paths only, never matched
values. The scanner is intentionally dependency-free so it can inspect a clean
checkout before installing packages.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Iterable
from urllib.parse import urlsplit


FORBIDDEN_COMPONENTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
FORBIDDEN_SUFFIXES = {
    ".bak", ".db", ".dump", ".key", ".log", ".p12", ".pem", ".pfx",
    ".pyc", ".sqlite", ".sqlite3", ".tgz", ".trace", ".zip",
}
FORBIDDEN_NAMES = {"training_seed.py", ".gitmodules"}
ALLOWED_URL_HOSTS = {
    "127.0.0.1",
    "accounts.google.com",
    "air-quality-api.open-meteo.com",
    "api.hevyapp.com",
    "api.open-meteo.com",
    "api.telegram.org",
    "cdn.jsdelivr.net",
    "creativecommons.org",
    "docs.github.com",
    "doi.org",
    "example.invalid",
    "github.com",
    "health.googleapis.com",
    "localhost",
    "kajeesan.com",
    "myaccount.google.com",
    "oauth2.googleapis.com",
    "openrouter.ai",
    "pubmed.ncbi.nlm.nih.gov",
    "registry.npmjs.org",
    "repository.up.ac.za",
    "www.contributor-covenant.org",
    "www.googleapis.com",
    "www.w3.org",
}
ALLOWED_EMAIL_LITERALS = {"git@github.com"}
# Visually reviewed fictional documentation assets only. Replacement bytes or
# another path require a fresh review; this is not a general binary allowance.
REVIEWED_BINARY_ASSETS: dict[str, str] = {
    "docs/assets/fictional-dashboard.png":
        "3f396a93f70120e599004c981a73aa52f35a3bd11c4a29a272990aa4638b7d0f",
}
DOCUMENTATION_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "192.0.2.0/24",
    "198.51.100.0/24",
    "203.0.113.0/24",
))
EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
PRIVATE_HOME = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+(?:/|\b)")
IPV4 = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9])"
)
URL = re.compile(r"https?://[^\s<>'\"`)\\]+")
SECRET_TOKEN_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"-{5}BEGIN [A-Z ]+ PRIVATE KEY-{5}"),
)
SECRET_LITERAL = re.compile(
    r"(?im)^\s*(?:export\s+)?"
    r"(?:api[_-]?key|auth[_-]?token|bot[_-]?token|client[_-]?secret|password|"
    r"panel[_-]?secret[_-]?key|secret[_-]?key)\s*[:=]\s*"
    r"['\"]([^'\"\r\n]{8,})['\"]\s*$"
)
SAFE_LITERAL_MARKERS = {
    "changeme", "dummy", "example", "fake", "placeholder", "replace",
    "synthetic", "test",
}
_REMOVED_TERM_FRAGMENTS = (
    ("methyl", "phenidate"),
    ("con", "certa"),
    ("rita", "lin"),
    ("medi", "kinet"),
    ("ad", "hd"),
    ("as", "rs"),
    ("phq", "-9"),
    ("butter", " chicken"),
    ("copen", "hagen"),
    ("køben", "havn"),
    ("kirk", "land"),
    ("minox", "idil"),
    ("la ", "ro", "che-pos", "ay"),
    ("fl", "oss"),
)
REMOVED_PERSONAL_TERMS = tuple(
    re.compile("".join(parts), re.IGNORECASE)
    for parts in _REMOVED_TERM_FRAGMENTS
)


class Finding:
    def __init__(self, category: str, path: str):
        self.category = category
        self.path = path

    def as_dict(self) -> dict[str, str]:
        return {"category": self.category, "path": self.path}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    ).stdout


def _tracked(root: Path) -> list[str]:
    return sorted(
        item for item in _git(root, "ls-files", "-z").decode("utf-8").split("\0")
        if item
    )


def _path_findings(relative: str) -> list[Finding]:
    path = PurePosixPath(relative)
    findings = []
    if any(component in FORBIDDEN_COMPONENTS for component in path.parts):
        findings.append(Finding("generated_or_dependency_path", relative))
    if path.name in FORBIDDEN_NAMES:
        findings.append(Finding("forbidden_or_ambiguous_file", relative))
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        findings.append(Finding("data_secret_or_generated_suffix", relative))
    if path.name.startswith(".env") and path.name != ".env.example":
        findings.append(Finding("live_environment_file", relative))
    return findings


def _text_findings(text: str, relative: str) -> list[Finding]:
    findings = []
    if any(pattern.search(text) for pattern in REMOVED_PERSONAL_TERMS):
        findings.append(Finding("removed_personal_fixture_term", relative))
    if PRIVATE_HOME.search(text):
        findings.append(Finding("private_absolute_home_path", relative))
    for pattern in SECRET_TOKEN_PATTERNS:
        if pattern.search(text):
            findings.append(Finding("credential_or_private_key_pattern", relative))
            break
    for match in SECRET_LITERAL.finditer(text):
        value = match.group(1).lower()
        is_dynamic_shell_value = value.startswith(("$(", "${"))
        if not is_dynamic_shell_value and not any(
            marker in value for marker in SAFE_LITERAL_MARKERS
        ):
            findings.append(Finding("literal_secret_assignment", relative))
            break
    for value in EMAIL.findall(text):
        if value.lower() in ALLOWED_EMAIL_LITERALS:
            continue
        if value in {
            "hermes-checkin-ping@am.service",
            "hermes-checkin-ping@pm.service",
        }:
            continue
        domain = value.rsplit("@", 1)[1].lower()
        if domain not in {"example.com", "example.invalid"}:
            findings.append(Finding("non_example_email", relative))
            break
    for value in IPV4.findall(text):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        is_documentation_address = any(
            address in network for network in DOCUMENTATION_NETWORKS
        )
        if (
            address.is_private
            and not address.is_loopback
            and not address.is_unspecified
            and not is_documentation_address
        ):
            findings.append(Finding("private_ipv4_address", relative))
            break
    for raw in URL.findall(text):
        host = (urlsplit(raw).hostname or "").lower()
        if not host:
            continue
        reserved_test_host = host.endswith((".example", ".invalid", ".test"))
        if (
            host not in ALLOWED_URL_HOSTS
            and not host.endswith(".apache.org")
            and not reserved_test_host
        ):
            findings.append(Finding("unreviewed_url_host", relative))
            break
    return findings


def _scan_blob(
    data: bytes, relative: str, *, repository_path: str | None = None,
) -> list[Finding]:
    findings = _path_findings(relative)
    asset_path = relative if repository_path is None else repository_path
    reviewed_digest = REVIEWED_BINARY_ASSETS.get(asset_path)
    if reviewed_digest is not None:
        if hashlib.sha256(data).hexdigest() != reviewed_digest:
            findings.append(Finding("reviewed_binary_digest_mismatch", relative))
        return findings
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        findings.append(Finding("unexplained_binary", relative))
        return findings
    findings.extend(_text_findings(text, relative))
    return findings


def _tree_scan(root: Path) -> tuple[list[Finding], int, int]:
    findings: list[Finding] = []
    total_bytes = 0
    paths = _tracked(root)
    tracked = set(paths)
    for relative in paths:
        path = root / relative
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode):
            findings.append(Finding("unsafe_symlink", relative))
            continue
        if not stat.S_ISREG(details.st_mode):
            findings.append(Finding("non_regular_tracked_path", relative))
            continue
        data = path.read_bytes()
        total_bytes += len(data)
        findings.extend(_scan_blob(data, relative))
    for current, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        retained_directories = []
        for name in directory_names:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if relative == ".git":
                continue
            if candidate.is_symlink():
                findings.append(Finding("unsafe_symlink", relative))
                continue
            if name == ".git":
                findings.append(Finding("nested_git_repository", relative))
                continue
            path_findings = _path_findings(relative)
            findings.extend(path_findings)
            if name in FORBIDDEN_COMPONENTS:
                continue
            retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in file_names:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                continue
            if name == ".git":
                findings.append(Finding("nested_git_repository", relative))
                continue
            if candidate.is_symlink():
                findings.append(Finding("unsafe_symlink", relative))
                continue
            if relative not in tracked:
                findings.extend(_path_findings(relative))
                findings.append(Finding("untracked_worktree_file", relative))
    staged = _git(root, "ls-files", "--stage").decode("utf-8", errors="replace")
    if any(line.startswith("160000 ") for line in staged.splitlines()):
        findings.append(Finding("git_submodule", "<git-index>"))
    return findings, len(paths), total_bytes


def _history_scan(root: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    # One blob may have appeared at multiple paths. Check every historical
    # (blob, path) pair so a reviewed image cannot authorize an unreviewed copy.
    seen: set[tuple[str, str]] = set()
    blobs: dict[str, bytes] = {}
    commits = _git(root, "rev-list", "--all").decode("ascii").splitlines()
    for commit in commits:
        listing = _git(root, "ls-tree", "-r", "-z", commit)
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, kind, object_bytes = metadata.split()
            if mode == b"160000":
                findings.append(Finding("historical_git_submodule", "history/<git-tree>"))
            if mode == b"120000":
                findings.append(Finding("historical_symlink", "history/<git-tree>"))
            if kind != b"blob":
                continue
            object_id = object_bytes.decode("ascii")
            relative = path_bytes.decode("utf-8")
            pair = (object_id, relative)
            if pair in seen:
                continue
            seen.add(pair)
            if object_id not in blobs:
                blobs[object_id] = _git(root, "cat-file", "-p", object_id)
            findings.extend(_scan_blob(
                blobs[object_id], f"history/{relative}", repository_path=relative,
            ))
    metadata = _git(
        root,
        "log",
        "--all",
        "--format=%H%n%an%n%ae%n%cn%n%ce%n%B%x00",
    ).decode("utf-8", errors="replace")
    for record in metadata.split("\0"):
        if record.strip():
            findings.extend(_text_findings(record, "history/<commit-metadata>"))
    references = _git(
        root, "for-each-ref", "--format=%(refname)"
    ).decode("utf-8", errors="replace")
    findings.extend(_text_findings(references, "history/<references>"))
    return findings, len(blobs)


def _deduplicate(findings: Iterable[Finding]) -> list[Finding]:
    unique = {(item.category, item.path): item for item in findings}
    return [unique[key] for key in sorted(unique)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    findings, file_count, total_bytes = _tree_scan(root)
    history_blobs = 0
    if args.history:
        historical, history_blobs = _history_scan(root)
        findings.extend(historical)
    findings = _deduplicate(findings)
    report = {
        "ok": not findings,
        "tracked_files": file_count,
        "tracked_bytes": total_bytes,
        "history_blobs": history_blobs,
        "finding_counts": dict(sorted(Counter(
            item.category for item in findings
        ).items())),
        "findings": [item.as_dict() for item in findings],
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
