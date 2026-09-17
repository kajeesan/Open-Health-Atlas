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
