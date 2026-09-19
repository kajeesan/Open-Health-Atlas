#!/usr/bin/env python3
"""Bounded distribution audit; reports categories and bundle-relative paths only.

This supplements source/history review. It cannot prove the absence of all
private data, and must not be used as a substitute for a clean build input.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
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
_SOURCE_ARCHIVE = 'Contents/Resources/CorrespondingSource.zip'
_SOURCE_PREFIX = 'Open-Health-Atlas/'

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


def audit_corresponding_source(data, receipt, source_root, runtime_root):
    """Only this exact source ZIP has source (rather than executable) policy.

    Bind every member to the immutable Git commit's reviewed manifest; never
    trust an embedded manifest or archive hash as its own authority. Build-time
    source/privacy/history gates remain mandatory and separate from this audit.
    """
    commit = receipt['source_commit']
    source = receipt['corresponding_source']
    if (not re.fullmatch('[0-9a-f]{40}', commit) or receipt['source_dirty'] is not False
            or receipt['license'] != 'AGPL-3.0-only' or source['commit'] != commit
            or source['bundle_path'] != _SOURCE_ARCHIVE
            or source['sha256'] != hashlib.sha256(data).hexdigest()):
        raise ValueError('Corresponding source receipt mismatch')
    clt = Path('/Library/Developer/CommandLineTools/usr/bin/git')
    git = str(clt) if clt.exists() else shutil.which('git')
    if not git:
        raise ValueError('Source verification requires Git')
    def git_bytes(*args):
        return subprocess.check_output([git, '-C', str(source_root), *args], stderr=subprocess.DEVNULL)
    manifest = git_bytes('show', commit + ':RELEASE_MANIFEST.tsv')
    if hashlib.sha256(manifest).hexdigest() != source['manifest_sha256']:
        raise ValueError('Reviewed manifest identity mismatch')
    reader = csv.DictReader(io.StringIO(manifest.decode()), dialect='excel-tab')
    if reader.fieldnames != ['path', 'sha256', 'bytes', 'kind']:
        raise ValueError('Reviewed source manifest header mismatch')
    expected = {}
    for row in reader:
        name = row['path']
        if (name in expected or not PurePosixPath(name).parts or PurePosixPath(name).is_absolute()
                or PurePosixPath(name).as_posix() != name or '..' in PurePosixPath(name).parts
                or '\\' in name or '\x00' in name):
            raise ValueError('Invalid reviewed source path')
        expected[name] = (row['sha256'], int(row['bytes']))
    tree = {}
    for record in git_bytes('ls-tree', '-r', '-z', commit).decode().split('\x00'):
        if not record:
            continue
        metadata, name = record.split('\t', 1)
        mode, kind, object_id = metadata.split()
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('Git source tree includes a nonregular entry')
        tree[name] = object_id
    if set(tree) != set(expected) | {'RELEASE_MANIFEST.tsv'}:
        raise ValueError('Reviewed source manifest is incomplete')
    # Verify the same immutable source tree's inventory, not mutable working files.
    inventory_bytes = git_bytes('show', commit + ':desktop/dependency-sources.json')
    inventory = json.loads(inventory_bytes)
    dependency_members = {}
    for item in inventory:
        if item['name'] in {'certifi', 'ordered-set'}:
            name = item['source_filename']
            if PurePosixPath(name).name != name or not name.endswith('.tar.gz'):
                raise ValueError('Invalid dependency source path')
            dependency_members['DependencySources/' + name] = item['source_sha256']
    if len(dependency_members) != 2:
        raise ValueError('Required dependency sources missing')
    required = {'LICENSE', 'LICENSING.md', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'docs/LICENSE-MIT.md',
                'desktop/macos/App.swift', 'desktop/macos/MCPLauncher.swift', 'desktop/macos/Icon.swift',
                'desktop/macos/icon.svg', 'scripts/build_desktop.py', 'scripts/audit_desktop_bundle.py',
                'docs/DESKTOP_RELEASE.md', 'requirements-desktop.lock'}
    if not required <= set(expected):
        raise ValueError('Corresponding source build materials missing')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        expected_names = {_SOURCE_PREFIX + name for name in expected} | {
            _SOURCE_PREFIX + 'RELEASE_MANIFEST.tsv'} | set(dependency_members)
        if len(members) != len(expected_names) or {item.filename for item in members} != expected_names:
            raise ValueError('Unreviewed or missing corresponding source member')
        if sum(item.file_size for item in members) > _MAX_ARCHIVE_EXPANDED:
            raise ValueError('Source archive size limit')
        for item in members:
            mode = item.external_attr >> 16
            if (item.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                    or item.file_size > _MAX_MEMBER):
                raise ValueError('Source member is not a bounded regular file')
            content = archive.read(item)
            if item.filename.startswith(_SOURCE_PREFIX):
                name = item.filename[len(_SOURCE_PREFIX):]
                blob = b'blob ' + str(len(content)).encode('ascii') + b'\x00' + content
                if hashlib.sha1(blob).hexdigest() != tree[name]:
                    raise ValueError('Source differs from the exact Git commit blob')
            if item.filename in dependency_members:
                if hashlib.sha256(content).hexdigest() != dependency_members[item.filename]:
                    raise ValueError('Dependency source hash mismatch')
            elif item.filename == _SOURCE_PREFIX + 'RELEASE_MANIFEST.tsv':
                if content != manifest:
                    raise ValueError('Embedded manifest mismatch')
            else:
                name = item.filename[len(_SOURCE_PREFIX):]
                expected_hash, expected_size = expected[name]
                if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
                    raise ValueError('Corresponding source bytes mismatch')
                runtime = runtime_root / name
                if runtime.exists() and (runtime.is_symlink() or not runtime.is_file()
                                         or hashlib.sha256(runtime.read_bytes()).hexdigest() != expected_hash):
                    raise ValueError('Runtime source differs from corresponding source')
    return {'source_commit': commit, 'project_files': len(expected) + 1,
            'dependency_sources': len(dependency_members), 'sha256': source['sha256']}


def audit_bundle(bundle: Path, source_root: Path | None = None) -> dict:
    bundle = bundle.resolve()
    if not bundle.is_dir() or bundle.suffix != '.app':
        raise ValueError('Expected an existing macOS .app directory')
    findings: set[tuple[str, str]] = set()
    hashes = {}
    source_verification = None
    if source_root is None:
        source_root = Path(__file__).resolve().parents[1]
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
        if relative == _SOURCE_ARCHIVE:
            try:
                receipt = json.loads((bundle / 'Contents/Resources/app/desktop/build-info.json').read_text())
                source_verification = audit_corresponding_source(
                    data, receipt, source_root, bundle / 'Contents/Resources/app')
            except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError,
                    zipfile.BadZipFile, RuntimeError, NotImplementedError):
                findings.add(('invalid_corresponding_source', relative))
        else:
            audit_bytes(data, relative, findings)
    for required in ('Contents/Info.plist', 'Contents/MacOS', 'Contents/Resources'):
        if not (bundle / required).exists():
            findings.add(('missing_app_structure', required))
    for notice in ('LICENSE', 'LICENSING.md', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'docs/LICENSE-MIT.md'):
        if not (bundle / 'Contents/Resources/app' / notice).is_file():
            findings.add(('missing_project_notice', notice))
    if (bundle / _SOURCE_ARCHIVE).is_symlink() or not (bundle / _SOURCE_ARCHIVE).is_file():
        findings.add(('missing_corresponding_source', _SOURCE_ARCHIVE))
    elif source_verification is None:
        findings.add(('invalid_corresponding_source', _SOURCE_ARCHIVE))
    return {'schema_version': 1, 'files_checked': len(hashes),
            'corresponding_source': source_verification,
            'findings': [{'category': category, 'path': path} for category, path in sorted(findings)],
            'file_sha256': hashes,
            'limits': ['Pattern-based audit, not exhaustive privacy certification',
                       'Compressed executable containers require separate build-tool inspection',
                       'Dependency license completeness requires reviewed build inventory']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--report', type=Path, help='Write full inventory outside the source checkout')
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Public Git checkout containing the receipt commit; may be on a newer revision')
    args = parser.parse_args()
    report = audit_bundle(args.bundle, args.source_root)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('schema_version', 'files_checked', 'findings', 'limits')}, indent=2))
    return bool(report['findings'])


if __name__ == '__main__':
    raise SystemExit(main())
