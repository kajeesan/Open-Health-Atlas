#!/usr/bin/env python3
"""Bounded distribution audit; reports categories and bundle-relative paths only.

This supplements source/history review. It cannot prove the absence of all
private data, and must not be used as a substitute for a clean build input.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

_PRIVATE_HOME = re.compile(rb"/(?:Users|home)/[A-Za-z0-9._-]+(?:/|\b)")
_SECRETS = tuple(re.compile(pattern) for pattern in (
    rb"\bAKIA[0-9A-Z]{16}\b", rb"\bgh[opusr]_[A-Za-z0-9]{30,}\b",
    rb"\bsk-[A-Za-z0-9_-]{20,}\b", rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
    rb"-{5}BEGIN [A-Z ]*PRIVATE KEY-{5}",
))
_FORBIDDEN_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.p12', '.pfx', '.key', '.log'}
_FORBIDDEN_NAMES = {'.git', '.env', '.DS_Store', 'devserver.py'}
_MAX_MEMBER = 256 * 1024 * 1024
_MAX_ARCHIVE_EXPANDED = 1024 * 1024 * 1024

# Reviewed public upstream examples/build paths, scoped to exact bundle file
# and exact full matched path multiset (including repetitions). These values
# originate in the SHA-pinned CPython 3.12.13 / 20260623 runtime and the hash-
# pinned desktop wheels. No local builder path is permitted. Signing changes
# executable hashes, so only this finding class uses a match fingerprint.
# Updated dependency bytes/matches require a fresh review; all other finding
# categories are still checked. See docs/DESKTOP.md.
_HOME_PATH_END = re.compile(rb"[^\x00\r\n\t \"'<>]*")
_REVIEWED_UPSTREAM_HOME_MATCHES = {'Contents/Resources/PythonLicenses/PYTHON.json': '635574d24bdcdab26ac1c3e78e32687c2da2da2de7d29d6fd583f83f99d349d1',
 'Contents/Resources/PythonRuntime/bin/python3.12': 'baa86d3ed148db477562bddc797bfcf62a9e43e433ab98484048ffabc39e12a6',
 'Contents/Resources/PythonRuntime/lib/libpython3.12.dylib': 'baa86d3ed148db477562bddc797bfcf62a9e43e433ab98484048ffabc39e12a6',
 'Contents/Resources/PythonRuntime/lib/python3.12/ntpath.py': 'd9bea3ef21735988cbc4439c529422aac0930b50f35cc73a42ca8bdd3ce63bbf',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/_cffi_backend.cpython-312-darwin.so': '0df880be4ffdcaac43ab3e74f996425a8d41e3dac692755f1aceefef8a047f46',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/cbor2/_cbor2.cpython-312-darwin.so': '13c28916b8d85e962d55ca1f1c027bc3fa06089ab0a99f723214941978bda5d3',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/cryptography-50.0.1.dist-info/sboms/cryptography-rust.cyclonedx.json': '93b1c9d72e81ff29d860fa95be96ddbb1ebdc71e35b88604df0c33cedbfb4719',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/cryptography/hazmat/bindings/_rust.abi3.so': '74087da7a31203b2e88a5165eeb1de21d35f6e0ebf88bd0e9cdcc2c272cc582f',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/gunicorn/config.py': '94932fab85cbbfbcc1cc203c63ea462a0274b5423c951c2cda3d6e6b81031b86',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/markupsafe/_speedups.cpython-312-darwin.so': '7c94460730d7188d2609938406f35ba173e25f583b78f9aaf05e763bf6ec5f28',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/pydantic_core-2.46.5.dist-info/sboms/pydantic-core.cyclonedx.json': 'a9cd9d612f63f69135698e57102a52b01d14fbf9c269ec8be8d2eb0a07710a97',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/pydantic_core/_pydantic_core.cpython-312-darwin.so': '258b1bb3ba442d02fb41aa127cb382d8fbb322dd52066042e1bc3c99424e488b',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/rpds/rpds.cpython-312-darwin.so': 'd8a5c35a687bd5761c0f591f13866a3ad9ee1d1bdea9dcab996788cb2a440a2c',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/rpds_py-2026.6.3.dist-info/sboms/rpds-py.cyclonedx.json': 'b924e8b38fa328cc149c884095180a08fee75014f68fd72cab8fefa66486bbf5',
 'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/wrapt/_wrappers.cpython-312-darwin.so': 'b0011feaab9cb29d5d16d2dcf19044a75cfc2cf973ec494dbb5464c0d6b345bc'}
# cryptography's source defines the OpenSSH PEM delimiter for its parser.
# It contains no key material. Unlike signed binaries, this source must match
# its full reviewed file hash before the delimiter false positive is allowed.
_REVIEWED_UPSTREAM_CREDENTIAL_FILES = {'Contents/Resources/PythonRuntime/lib/python3.12/site-packages/cryptography/hazmat/primitives/serialization/ssh.py': '162b177bf9d429d3c67ea10d5612a99a86b399a23ca87f067be5466dcd1dca4c'}


def _reviewed_home_matches(data: bytes, relative: str) -> bool:
    expected = _REVIEWED_UPSTREAM_HOME_MATCHES.get(relative)
    if expected is None:
        return False
    parts = [_HOME_PATH_END.match(data, match.start()).group()
             for match in _PRIVATE_HOME.finditer(data)]
    return hashlib.sha256(b"\x00".join(sorted(parts))).hexdigest() == expected



def audit_bytes(data: bytes, relative: str, findings: set[tuple[str, str]], depth: int = 0) -> None:
    path = PurePosixPath(relative)
    if (set(path.parts) & _FORBIDDEN_NAMES or path.suffix.lower() in _FORBIDDEN_SUFFIXES
            or path.name.startswith('.env.')):
        findings.add(('forbidden_distribution_file', relative))
    if data.startswith(b'SQLite format 3\x00'):
        findings.add(('database_in_distribution', relative))
    if _PRIVATE_HOME.search(data) and not _reviewed_home_matches(data, relative):
        findings.add(('absolute_build_home_path', relative))
    if (any(pattern.search(data) for pattern in _SECRETS)
            and hashlib.sha256(data).hexdigest() != _REVIEWED_UPSTREAM_CREDENTIAL_FILES.get(relative)):
        findings.add(('credential_pattern', relative))
    if data.startswith(b'PK\x03\x04'):
        if depth >= 3:
            findings.add(('archive_nesting_limit', relative))
            return
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                expanded = 0
                for info in archive.infolist():
                    expanded += info.file_size
                    if info.file_size > _MAX_MEMBER or expanded > _MAX_ARCHIVE_EXPANDED:
                        findings.add(('archive_size_limit', relative))
                        break
                    if '..' in PurePosixPath(info.filename).parts or info.filename.startswith('/'):
                        findings.add(('unsafe_archive_path', relative))
                        continue
                    if not info.is_dir():
                        audit_bytes(archive.read(info), relative + '!' + info.filename, findings, depth + 1)
        except (zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError):
            findings.add(('unreadable_archive', relative))


def audit_bundle(bundle: Path) -> dict:
    bundle = bundle.resolve()
    if not bundle.is_dir() or bundle.suffix != '.app':
        raise ValueError('Expected an existing macOS .app directory')
    findings: set[tuple[str, str]] = set()
    hashes = {}
    for path in sorted(bundle.rglob('*')):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(bundle)
            except (ValueError, OSError, RuntimeError):
                findings.add(('external_or_broken_symlink', relative))
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > _MAX_MEMBER:
            findings.add(('file_size_limit', relative))
            continue
        data = path.read_bytes()
        hashes[relative] = hashlib.sha256(data).hexdigest()
        audit_bytes(data, relative, findings)
    for required in ('Contents/Info.plist', 'Contents/MacOS', 'Contents/Resources'):
        if not (bundle / required).exists():
            findings.add(('missing_app_structure', required))
    for notice in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        if not any(path.name == notice for path in bundle.rglob(notice)):
            findings.add(('missing_project_notice', notice))
    return {'schema_version': 1, 'files_checked': len(hashes),
            'findings': [{'category': category, 'path': path} for category, path in sorted(findings)],
            'file_sha256': hashes,
            'limits': ['Pattern-based audit, not exhaustive privacy certification',
                       'Compressed executable containers require separate build-tool inspection',
                       'Dependency license completeness requires reviewed build inventory']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--report', type=Path, help='Write full inventory outside the source checkout')
    args = parser.parse_args()
    report = audit_bundle(args.bundle)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('schema_version', 'files_checked', 'findings', 'limits')}, indent=2))
    return bool(report['findings'])


if __name__ == '__main__':
    raise SystemExit(main())
