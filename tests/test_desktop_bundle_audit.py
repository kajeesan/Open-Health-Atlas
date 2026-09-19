"""Distribution boundaries absent from source-only publication scans."""
import importlib.util
import io
from pathlib import Path
import zipfile

_spec = importlib.util.spec_from_file_location(
    'desktop_bundle_audit', Path(__file__).resolve().parents[1] / 'scripts' / 'audit_desktop_bundle.py')
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def test_nested_archive_scans_content_without_exposing_values():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('cache.sqlite', b'SQLite format 3\x00fictional')
        archive.writestr('compiled.pyc', b'/'+b'Users/'+b'fictional-builder/work/module.py')
    findings = set()
    audit.audit_bytes(stream.getvalue(), 'runtime.zip', findings)
    assert ('database_in_distribution', 'runtime.zip!cache.sqlite') in findings
    assert ('absolute_build_home_path', 'runtime.zip!compiled.pyc') in findings
    assert all('fictional-builder' not in path for _, path in findings)


def test_external_symlink_and_missing_notices_fail(tmp_path):
    bundle = tmp_path / 'Trial.app'
    (bundle / 'Contents/MacOS').mkdir(parents=True)
    (bundle / 'Contents/Resources').mkdir()
    (bundle / 'Contents/Info.plist').write_text('fictional')
    outside = tmp_path / 'outside'
    outside.write_text('fictional')
    (bundle / 'Contents/Resources/external').symlink_to(outside)
    categories = {item['category'] for item in audit.audit_bundle(bundle)['findings']}
    assert 'external_or_broken_symlink' in categories
    assert 'missing_project_notice' in categories


def test_vendor_certificate_is_permitted_but_private_key_is_not():
    findings = set()
    audit.audit_bytes(b'-----BEGIN CERTIFICATE-----\nfictional', 'certifi/cacert.pem', findings)
    assert not findings
    audit.audit_bytes(b'-----BEGIN '+b'PRIVATE KEY-----\nfictional', 'runtime/secret.pem', findings)
    assert ('credential_pattern', 'runtime/secret.pem') in findings


def test_reviewed_upstream_paths_remain_scoped_and_detect_new_matches(monkeypatch):
    data = b'example = "' + b'/' + b'Users/fictional-upstream/example' + b'"'
    relative = 'Contents/Resources/PythonRuntime/upstream.py'
    parts = [audit._HOME_PATH_END.match(data, match.start()).group()
             for match in audit._PRIVATE_HOME.finditer(data)]
    fingerprint = audit.hashlib.sha256(b'\x00'.join(sorted(parts))).hexdigest()
    monkeypatch.setattr(audit, '_REVIEWED_UPSTREAM_HOME_MATCHES', {relative: fingerprint})
    findings = set()
    audit.audit_bytes(data, relative, findings)
    assert not findings
    for changed, path in ((data + data, relative),
                          (data.replace(b'example', b'private'), relative),
                          (data, 'another.py')):
        findings = set()
        audit.audit_bytes(changed, path, findings)
        assert ('absolute_build_home_path', path) in findings
    findings = set()
    audit.audit_bytes(data + b'-----BEGIN '+b'PRIVATE KEY-----', relative, findings)
    assert ('credential_pattern', relative) in findings


def test_reviewed_parser_marker_requires_exact_file_bytes(monkeypatch):
    data = b'marker = "-----BEGIN '+b'OPENSSH PRIVATE KEY-----"'
    relative = 'Contents/Resources/PythonRuntime/parser.py'
    monkeypatch.setattr(audit, '_REVIEWED_UPSTREAM_CREDENTIAL_FILES', {
        relative: audit.hashlib.sha256(data).hexdigest()})
    findings = set()
    audit.audit_bytes(data, relative, findings)
    assert not findings
    audit.audit_bytes(data + b'fictional-unreviewed-material', relative, findings)
    assert ('credential_pattern', relative) in findings


def test_exact_source_archive_binds_git_manifest_members_and_runtime(tmp_path, monkeypatch):
    import csv
    import json
    import stat
    required = ['LICENSE', 'LICENSING.md', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'docs/LICENSE-MIT.md',
                'desktop/macos/App.swift', 'desktop/macos/MCPLauncher.swift', 'desktop/macos/Icon.swift',
                'desktop/macos/icon.svg', 'scripts/build_desktop.py', 'scripts/audit_desktop_bundle.py',
                'docs/DESKTOP_RELEASE.md', 'requirements-desktop.lock', 'scripts/devserver.py', '.env.example']
    files = {name: ('Reviewed fictional source: ' + name).encode() for name in required}
    dependencies = {'certifi.tar.gz': b'fictional certifi source', 'ordered-set.tar.gz': b'fictional ordered-set source'}
    inventory = [{'name': name.removesuffix('.tar.gz'), 'source_filename': name,
                  'source_sha256': audit.hashlib.sha256(content).hexdigest()} for name, content in dependencies.items()]
    files['desktop/dependency-sources.json'] = json.dumps(inventory).encode()
    manifest_io = io.StringIO()
    writer = csv.writer(manifest_io, delimiter='\t', lineterminator='\n')
    writer.writerow(('path', 'sha256', 'bytes', 'kind'))
    for name, content in sorted(files.items()):
        writer.writerow((name, audit.hashlib.sha256(content).hexdigest(), len(content), 'fixture'))
    manifest = manifest_io.getvalue().encode()
    commit = 'a' * 40
    def git_read(command, **kwargs):
        if command[-1] == commit + ':RELEASE_MANIFEST.tsv': return manifest
        if command[-1] == commit + ':desktop/dependency-sources.json': return files['desktop/dependency-sources.json']
        if 'ls-tree' in command:
            values = dict(files, **{'RELEASE_MANIFEST.tsv': manifest})
            return b'\x00'.join(('100644 blob ' + audit.hashlib.sha1(
                b'blob ' + str(len(data)).encode() + b'\x00' + data).hexdigest() + '\t' + name).encode()
                for name, data in values.items())
        raise AssertionError(command)
    monkeypatch.setattr(audit.subprocess, 'check_output', git_read)
    entries = {'Open-Health-Atlas/' + name: data for name, data in files.items()}
    entries['Open-Health-Atlas/RELEASE_MANIFEST.tsv'] = manifest
    entries.update({'DependencySources/' + name: data for name, data in dependencies.items()})
    def zipped(members):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, data in members.items():
                info = zipfile.ZipInfo(name); info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, data)
        return stream.getvalue()
    def receipt(data):
        return {'source_commit': commit, 'source_dirty': False, 'license': 'AGPL-3.0-only',
                'corresponding_source': {'commit': commit, 'bundle_path': audit._SOURCE_ARCHIVE,
                'sha256': audit.hashlib.sha256(data).hexdigest(), 'manifest_sha256': audit.hashlib.sha256(manifest).hexdigest()}}
    valid = zipped(entries)
    assert audit.audit_corresponding_source(valid, receipt(valid), tmp_path, tmp_path)['project_files'] == len(files) + 1
    # Rehashing a changed archive and embedded receipt cannot authorize new bytes.
    bad_cases = []
    extra = dict(entries); extra['Open-Health-Atlas/private.jsonl'] = b'fictional private material'; bad_cases.append(extra)
    changed = dict(entries); changed['Open-Health-Atlas/LICENSE'] = b'changed'; bad_cases.append(changed)
    missing = dict(entries); del missing['Open-Health-Atlas/desktop/macos/App.swift']; bad_cases.append(missing)
    source_changed = dict(entries); source_changed['DependencySources/certifi.tar.gz'] = b'changed'; bad_cases.append(source_changed)
    import pytest
    for members in bad_cases:
        data = zipped(members)
        with pytest.raises(ValueError): audit.audit_corresponding_source(data, receipt(data), tmp_path, tmp_path)
    wrong = receipt(valid); wrong['corresponding_source']['commit'] = 'b' * 40
    with pytest.raises(ValueError): audit.audit_corresponding_source(valid, wrong, tmp_path, tmp_path)
    # Even a stale committed manifest cannot authorize bytes absent from that Git tree.
    files['LICENSE'] = b'different bytes in the actual commit blob'
    with pytest.raises(ValueError, match='exact Git commit blob'):
        audit.audit_corresponding_source(valid, receipt(valid), tmp_path, tmp_path)
    findings = set()
    audit.audit_bytes(valid, 'elsewhere.zip', findings)
    assert any(category == 'forbidden_distribution_file' for category, _ in findings)


def test_internal_source_archive_symlink_cannot_skip_verification(tmp_path):
    bundle = tmp_path / 'Trial.app'
    (bundle / 'Contents/MacOS').mkdir(parents=True)
    resources = bundle / 'Contents/Resources'
    (resources / 'app/docs').mkdir(parents=True)
    (bundle / 'Contents/Info.plist').write_text('fictional')
    for name in ('LICENSE', 'LICENSING.md', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'docs/LICENSE-MIT.md'):
        (resources / 'app' / name).write_text('fictional notice')
    (resources / 'CorrespondingSource.zip').symlink_to('app/LICENSE')
    report = audit.audit_bundle(bundle)
    assert report['corresponding_source'] is None
    assert {'category': 'missing_corresponding_source', 'path': audit._SOURCE_ARCHIVE} in report['findings']
