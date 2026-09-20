#!/usr/bin/env python3
"""Build a self-contained macOS app. Outputs and caches must stay outside source.

Build prerequisites (maintainers only): macOS, CLT/Swift, Python 3.11+, uv.
End users need none of these. Downloads are pinned and verified; signing and
notarization are opt-in and use an existing Keychain profile, never raw secrets.
"""
from __future__ import annotations
import argparse
import csv
import io
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_RELEASE = '20260623'
PYTHON_VERSION = '3.12.13'
RUNTIMES = {
    'arm64': ('aarch64', '41df7d3ae4757e84b97874f76d634268456aaa271740d33f968d826374998fb7'),
    'x86_64': ('x86_64', 'a6bbea996c5f14eb55ab275889d2df45408deec504b4a7219d7b59c045b2555e'),
}
# Reviewed product resources; no deployment installers, secrets, database or devserver.
DIRECTORIES = ('app', 'toolkit/hermes_insights', 'desktop/static', 'desktop/templates')
FILES = ('LICENSE', 'LICENSING.md', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'docs/LICENSE-MIT.md', 'toolkit/health.py',
         'toolkit/SCHEMA.sql', 'docs/authored-submuscle-map.md', 'deploy/bridge_commands.py', 'deploy/hermes-bridge',
         'scripts/init_hermes.py', 'scripts/make_demo_db.py', 'scripts/openhealthatlas_mcp.py')
REQUIRED_DESKTOP_FILES = tuple('desktop/' + name + '.py' for name in (
    '__init__', 'broker', 'child', 'launcher', 'mcp', 'mcp_config',
    'preferences', 'server', 'workspaces',
))
SOURCE_REQUIRED = ('LICENSE', 'LICENSING.md', 'NOTICE', 'THIRD_PARTY_NOTICES.md',
                   'docs/LICENSE-MIT.md', 'docs/DESKTOP_RELEASE.md',
                   'desktop/macos/App.swift', 'desktop/macos/MCPLauncher.swift',
                   'desktop/macos/Icon.swift', 'desktop/macos/icon.svg',
                   'desktop/macos/InstallerBackground.swift', 'desktop/macos/dmg-settings.py',
                   'requirements-desktop-build.lock',
                   'desktop/dependency-sources.json', 'scripts/build_desktop.py',
                   'scripts/audit_desktop_bundle.py', 'requirements-desktop.lock',
                   'requirements-desktop.txt', 'requirements-mcp.txt', 'requirements.txt')
EMBEDDED_SOURCES = {'certifi': 'LICENSE', 'ordered-set': 'MIT-LICENSE'}
FULL_RUNTIME_HASHES = {
    'arm64': 'ce5a2d552077d869f69dc25d834c2fe4d036f9d78e770fb5d916273db802cabc',
    'x86_64': '3b2ee510354f51b6bda71fec7d5cf70dfad29ed672aa5847d1f5216ee22fc5ef',
}
MACHO_MAGICS = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def run_private(args, **kwargs):
    """Signing tools may print account identities even on successful checks."""
    try:
        result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, **kwargs)
    except OSError:
        raise SystemExit('A signing verification tool could not start. Review it locally.') from None
    if result.returncode:
        raise SystemExit(Path(args[0]).name + ' failed with exit code ' + str(result.returncode)
                         + '. Review signing credentials or verification locally.') from None


def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def _reviewed_bytes(root, relative, expected_size=None):
    """Read through no-follow descriptors; never follow a substituted directory."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Reviewed source is not a regular file: ' + relative)
            if expected_size is not None and info.st_size != expected_size:
                raise ValueError('Reviewed source size mismatch: ' + relative)
            data = stream.read(info.st_size + 1)
            if len(data) != info.st_size:
                raise ValueError('Reviewed source changed while reading: ' + relative)
            return data, stat.S_IMODE(info.st_mode) & 0o777
    except OSError as error:
        raise ValueError('Reviewed source is missing, nonregular or symlinked: ' + relative) from error
    finally:
        os.close(directory)


def reviewed_manifest():
    manifest, _ = _reviewed_bytes(ROOT, 'RELEASE_MANIFEST.tsv')
    reader = csv.DictReader(io.StringIO(manifest.decode('utf-8')), dialect='excel-tab')
    if reader.fieldnames != ['path', 'sha256', 'bytes', 'kind']:
        raise ValueError('Release manifest header mismatch.')
    entries = {}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('Release manifest contains a malformed source row.')
        relative = row['path']
        path = PurePosixPath(relative)
        if (not relative or not path.parts or path.is_absolute() or path.as_posix() != relative
                or '..' in path.parts or '\\' in relative or '\x00' in relative
                or relative in entries):
            raise ValueError('Release manifest contains a duplicate or unsafe source path.')
        size, checksum = row['bytes'], row['sha256']
        if (not size or not size.isascii() or not size.isdecimal()
                or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum)):
            raise ValueError('Release manifest contains invalid source metadata: ' + relative)
        entries[relative] = (int(size), checksum)
    return manifest, entries


def checked_sources(entries, selected):
    verified = []
    for relative in sorted(selected):
        size, checksum = entries[relative]
        data, mode = _reviewed_bytes(ROOT, relative, size)
        if hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError('Reviewed source hash mismatch: ' + relative)
        verified.append((relative, data, mode))
    return verified


def copy_sources(target):
    _, entries = reviewed_manifest()
    for required in (*FILES, *REQUIRED_DESKTOP_FILES):
        if required not in entries:
            raise ValueError('Required source is absent from the release manifest: ' + required)
    for directory in DIRECTORIES:
        if not any(name.startswith(directory + '/') for name in entries):
            raise ValueError('Required source directory is absent from the release manifest: ' + directory)
    selected = [name for name in entries if name in FILES
                or any(name.startswith(directory + '/') for directory in DIRECTORIES)
                or (PurePosixPath(name).parent == PurePosixPath('desktop')
                    and PurePosixPath(name).suffix == '.py')]
    # Validate all inputs before creating output; write precisely the checked bytes,
    # not a second path-based read that could pick up a changed local file.
    verified = checked_sources(entries, selected)
    target.mkdir(parents=True, exist_ok=False)
    for relative, data, mode in verified:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(mode)


def locked_packages(data):
    packages = {}
    current = None
    for line in data.decode('utf-8').splitlines():
        match = re.match(r'^([a-z0-9][a-z0-9_.-]*)==([^\s]+)', line)
        if match:
            current = match.group(1)
            packages[current] = (match.group(2), set())
        elif current:
            packages[current][1].update(re.findall(r'--hash=sha256:([0-9a-f]{64})', line))
    return packages


def prepare_dependency_sources(cache, resources):
    _, entries = reviewed_manifest()
    checked = {name: data for name, data, _ in checked_sources(entries, (
        'desktop/dependency-sources.json', 'requirements-desktop.lock'))}
    inventory = json.loads(checked['desktop/dependency-sources.json'])
    locked = locked_packages(checked['requirements-desktop.lock'])
    if len(inventory) != len(locked) or {item['name'] for item in inventory} != set(locked):
        raise ValueError('Dependency source inventory does not match the lockfile.')
    sources = []
    notices = resources / 'SupplementalLicenses'
    notices.mkdir()
    for item in inventory:
        version, hashes = locked[item['name']]
        url = urlparse(item['source_url'])
        filename = item['source_filename']
        if (item['version'] != version or item['source_sha256'] not in hashes
                or url.scheme != 'https' or url.hostname != 'files.pythonhosted.org'
                or url.username or url.password or url.query or url.fragment
                or Path(filename).name != filename or not filename.endswith('.tar.gz')
                or not url.path.endswith('/' + filename)):
            raise ValueError('Invalid pinned dependency source: ' + item['name'])
        if item['name'] not in EMBEDDED_SOURCES:
            continue
        archive = cache / filename
        if not archive.exists():
            temporary = archive.with_suffix('.download')
            urllib.request.urlretrieve(item['source_url'], temporary)
            temporary.rename(archive)
        if digest(archive) != item['source_sha256']:
            raise ValueError('Dependency source checksum mismatch: ' + item['name'])
        with tarfile.open(archive) as tar:
            member = tar.getmember(filename.removesuffix('.tar.gz') + '/' + EMBEDDED_SOURCES[item['name']])
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError('Dependency license is not a bounded regular file.')
            license_text = tar.extractfile(member).read()
        (notices / (item['name'] + '-LICENSE.txt')).write_bytes(license_text)
        sources.append((item, archive.read_bytes()))
    return sources


def create_corresponding_source(target, commit, version, dependency_sources):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid corresponding source commit.')
    manifest, entries = reviewed_manifest()
    if any(name not in entries for name in SOURCE_REQUIRED):
        raise ValueError('Corresponding source lacks required build/license materials.')
    verified = checked_sources(entries, entries)
    members = [('Open-Health-Atlas/RELEASE_MANIFEST.tsv', manifest, 0o644)]
    members += [('Open-Health-Atlas/' + name, data, mode) for name, data, mode in verified]
    for item, data in dependency_sources:
        if hashlib.sha256(data).hexdigest() != item['source_sha256']:
            raise ValueError('Corresponding dependency source changed.')
        members.append(('DependencySources/' + item['source_filename'], data, 0o644))
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | (0o755 if mode & 0o111 else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return {'commit': commit, 'sha256': digest(target),
            'filename': f'openhealthatlas-{version}-source-{commit}.zip',
            'bundle_path': 'Contents/Resources/CorrespondingSource.zip',
            'manifest_sha256': hashlib.sha256(manifest).hexdigest()}


def notarize(path, profile, env):
    # Never forward raw tool output: failures can contain account or path details.
    result = subprocess.run(['/usr/bin/xcrun', 'notarytool', 'submit', str(path),
                             '--keychain-profile', profile, '--wait', '--output-format', 'json'],
                            env=env, capture_output=True, text=True)
    try:
        response = json.loads(result.stdout)
        submission = response['id']
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', submission):
            raise ValueError('Invalid submission identity')
        accepted = response.get('status') == 'Accepted'
    except (KeyError, TypeError, ValueError):
        raise SystemExit('Notarization returned no valid acceptance receipt. Check the Keychain profile locally.') from None
    if result.returncode or not accepted:
        raise SystemExit('Notarization was not accepted. Review submission ' + submission + ' locally.')
    return {'submission_id': submission, 'status': 'Accepted'}


def prepare_runtime(cache, runtime):
    architecture, expected = RUNTIMES[platform.machine()]
    name = f'cpython-{PYTHON_VERSION}+{RUNTIME_RELEASE}-{architecture}-apple-darwin-install_only_stripped.tar.gz'
    archive = cache / name
    if not archive.exists():
        url = f'https://github.com/astral-sh/python-build-standalone/releases/download/{RUNTIME_RELEASE}/{name}'
        temporary = archive.with_suffix('.download')
        urllib.request.urlretrieve(url, temporary)
        temporary.rename(archive)
    if digest(archive) != expected:
        raise SystemExit('Standalone Python checksum mismatch; remove the cached archive and investigate.')
    unpack = cache / 'unpacked'
    if unpack.exists(): shutil.rmtree(unpack)
    unpack.mkdir()
    with tarfile.open(archive) as tar:
        # Python 3.11 supports the data filter on security-patched builds.
        if not hasattr(tarfile, 'data_filter'):
            raise SystemExit('Use a security-patched Python 3.11+ with safe archive extraction.')
        tar.extractall(unpack, filter='data')
    shutil.copytree(unpack / 'python', runtime, symlinks=True)
    shutil.rmtree(unpack)
    return expected


def prepare_runtime_notices(cache, resources):
    architecture = RUNTIMES[platform.machine()][0]
    name = f'cpython-{PYTHON_VERSION}+{RUNTIME_RELEASE}-{architecture}-apple-darwin-pgo+lto-full.tar.zst'
    archive = cache / name
    if not archive.exists():
        url = f'https://github.com/astral-sh/python-build-standalone/releases/download/{RUNTIME_RELEASE}/{name}'
        temporary = archive.with_suffix('.download')
        urllib.request.urlretrieve(url, temporary); temporary.rename(archive)
    if digest(archive) != FULL_RUNTIME_HASHES[platform.machine()]:
        raise SystemExit('Python license archive checksum mismatch.')
    metadata = json.loads(subprocess.check_output(['/usr/bin/tar', '-xOf', str(archive), 'python/PYTHON.json']))
    license_paths = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'license_paths': license_paths.update(item)
                elif key == 'license_path': license_paths.add(item)
                else: collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    collect(metadata)
    destination = resources / 'PythonLicenses'; destination.mkdir()
    (destination / 'PYTHON.json').write_text(json.dumps(metadata, indent=2) + '\n')
    archive_paths = set(subprocess.check_output(['/usr/bin/tar', '-tf', str(archive)], text=True).splitlines())
    for name in sorted(license_paths):
        if 'python/' + name not in archive_paths:
            # This macOS PBS manifest mentions zlib-ng although its zlib extension
            # links only the OS-provided libz. It does not distribute zlib-ng.
            zlib = metadata['build_info']['extensions']['zlib']
            if name == 'licenses/LICENSE.zlib-ng.txt' and all(link.get('system') for extension in zlib for link in extension['links']):
                (destination / 'SYSTEM_ZLIB.txt').write_text('The upstream manifest also names zlib-ng. This macOS build links OS libz only; no zlib-ng library is redistributed. See PYTHON.json build_info.extensions.zlib.\n')
                continue
            raise SystemExit('An upstream bundled dependency license is absent: ' + name)
        if not name.startswith('licenses/') or '..' in Path(name).parts:
            raise SystemExit('Unexpected license archive path.')
        text = subprocess.check_output(['/usr/bin/tar', '-xOf', str(archive), 'python/' + name])
        (destination / Path(name).name).write_bytes(text)
    if not license_paths: raise SystemExit('Python dependency license metadata is missing.')


def native_files(bundle):
    for path in sorted(bundle.rglob('*'), key=lambda value: -len(value.parts)):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                if stream.read(4) in MACHO_MAGICS: yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--version', required=True, help='New explicit release version, for example 0.2.0')
    parser.add_argument('--arch', choices=tuple(RUNTIMES), default=platform.machine())
    parser.add_argument('--identity', help='Existing Developer ID Application signing identity')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile')
    parser.add_argument('--skip-archive', action='store_true', help='Build .app only for local acceptance')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}', args.version):
        parser.error('--version must contain one to three numeric components')
    if platform.system() != 'Darwin' or args.arch != platform.machine():
        parser.error('Build natively on the target macOS CPU architecture; cross-builds are not validated.')
    if args.notary_profile and not args.identity:
        parser.error('--notary-profile requires a Developer ID --identity')
    if args.identity and not args.identity.startswith('Developer ID Application:'):
        parser.error('Public distribution requires a Developer ID Application identity')
    output = args.output_dir.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Build output must be outside the source checkout')
    clt = Path('/Library/Developer/CommandLineTools')
    git = str(clt / 'usr/bin/git') if (clt / 'usr/bin/git').exists() else shutil.which('git')
    if not git: raise SystemExit('Git is required on the build machine to identify the packaged source.')
    code_version = subprocess.check_output([git, '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output([git, '-C', str(ROOT), 'status', '--porcelain'], text=True).strip():
        raise SystemExit('A corresponding-source distribution requires a clean reviewed source commit.')
    gate_env = dict(os.environ, PATH=str(Path(git).parent) + os.pathsep + os.environ.get('PATH', ''),
                    PYTHONDONTWRITEBYTECODE='1')
    run([sys.executable, '-I', '-B', ROOT / 'scripts/release_manifest.py', 'verify'], env=gate_env)
    run([sys.executable, '-I', '-B', ROOT / 'scripts/release_scan.py', '--history'], env=gate_env, cwd=ROOT)
    output.mkdir(parents=True, exist_ok=True)
    work = output / '.build'; work.mkdir(exist_ok=True)
    cache = work / 'downloads'; cache.mkdir(exist_ok=True)
    # Build/sign on the local temporary volume: synced document providers may
    # reattach FinderInfo during signing. Only finished deliverables are copied out.
    staging = Path(tempfile.mkdtemp(prefix='oha-desktop-build-'))
    bundle = staging / 'Open Health Atlas.app'
    contents = bundle / 'Contents'; resources = contents / 'Resources'; macos = contents / 'MacOS'
    runtime = resources / 'PythonRuntime'
    resources.mkdir(parents=True); macos.mkdir()
    runtime_hash = prepare_runtime(cache, runtime)
    prepare_runtime_notices(cache, resources)
    python = runtime / 'bin/python3'
    developer = os.environ.get('DEVELOPER_DIR') or (str(clt) if (clt / 'usr/bin/swiftc').exists() else subprocess.check_output(['/usr/bin/xcode-select', '-p'], text=True).strip())
    env = dict(os.environ, UV_CACHE_DIR=str(work / 'uv-cache'), UV_PYTHON_DOWNLOADS='never',
               PYTHONDONTWRITEBYTECODE='1', DEVELOPER_DIR=developer)
    # Require prebuilt reviewed wheels: no build scripts or compiler dependency at first use.
    run(['uv', 'pip', 'install', '--python', python, '--require-hashes', '--only-binary', ':all:', '--link-mode', 'copy',
         '-r', ROOT / 'requirements-desktop.lock'], env=env)
    copy_sources(resources / 'app')
    dependency_sources = prepare_dependency_sources(cache, resources)
    source_archive = resources / 'CorrespondingSource.zip'
    corresponding_source = create_corresponding_source(source_archive, code_version, args.version, dependency_sources)
    if (subprocess.check_output([git, '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip() != code_version
            or subprocess.check_output([git, '-C', str(ROOT), 'status', '--porcelain'], text=True).strip()):
        raise SystemExit('Source checkout changed while preparing the distribution.')
    build_receipt = {'version': args.version, 'source_commit': code_version, 'source_dirty': False,
                     'license': 'AGPL-3.0-only', 'corresponding_source': corresponding_source,
                     'runtime_sha256': runtime_hash, 'lock_sha256': digest(ROOT / 'requirements-desktop.lock'),
                     'architecture': args.arch, 'build_macos': platform.mac_ver()[0],
                     'signing': {'mode': 'Developer ID' if args.identity else 'ad hoc',
                                 'status': 'signed-not-notarized' if args.identity else 'local-preview',
                                 'gatekeeper_assessment': 'not-run',
                                 'clean_customer_install': 'not-evaluated-by-builder'}}
    (resources / 'app/desktop/build-info.json').write_text(json.dumps(build_receipt, indent=2) + '\n')
    # Remove bytecode build paths, development headers/static libraries and executable
    # console scripts whose absolute build shebangs are unsuitable for relocation.
    for path in list(runtime.rglob('__pycache__')):
        if path.is_dir(): shutil.rmtree(path)
    for path in list(runtime.rglob('*.pyc')): path.unlink()
    for name in ('include', 'share/man', 'lib/pkgconfig'):
        shutil.rmtree(runtime / name, ignore_errors=True)
    for path in list(runtime.rglob('*.a')): path.unlink()
    for path in list((runtime / 'bin').iterdir()):
        if path.name not in {'python3', 'python3.12', 'python'}:
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
    # CPython development configuration embeds upstream builder paths. It is never
    # used at runtime; remove config scripts without altering library semantics.
    for path in list((runtime / 'lib').glob('python*/config-*')):
        shutil.rmtree(path)
    # No package installation occurs on end-user machines. Omit the unused
    # bundled installer and its vendored dependencies; uv is a build tool only.
    stdlib = runtime / 'lib/python3.12'
    for path in [stdlib / 'ensurepip', stdlib / 'site-packages/pip'] + list((stdlib / 'site-packages').glob('pip-*.dist-info')):
        shutil.rmtree(path, ignore_errors=True)
    # Required wheel metadata/license directories deliberately remain in site-packages.
    notices = resources / 'THIRD_PARTY_RUNTIME.txt'
    inventory = subprocess.check_output([str(python), '-I', '-B', '-c',
        'import importlib.metadata,json; print(json.dumps(sorted([{ "name": d.metadata["Name"], "version": d.version, "license": d.metadata.get("License-Expression") or d.metadata.get("License") or "See bundled third-party notices" } for d in importlib.metadata.distributions()], key=lambda d:d["name"].lower()), indent=2))'], env=env, text=True)
    notices.write_text('Open Health Atlas bundles CPython from python-build-standalone.\nRuntime license texts and exact upstream metadata are in Resources/PythonLicenses; wheel license texts and metadata\nare retained in PythonRuntime/lib/python3.12/site-packages/*.dist-info.\nSupplementalLicenses contains the full certifi and ordered-set notices.\nCorrespondingSource.zip includes the exact project source, both dependency source archives,\nand desktop/dependency-sources.json with all locked dependency source URLs and hashes.\nThird-party components retain their own licenses.\n\n' + inventory)
    sdk = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-path'], env=env, text=True).strip()
    swift = subprocess.check_output(['/usr/bin/xcrun', '--find', 'swiftc'], env=env, text=True).strip()
    common = [swift, '-O', '-sdk', sdk, '-module-cache-path', work / 'swift-cache', '-target', f'{args.arch}-apple-macosx13.0', '-file-prefix-map', f'{ROOT}=/OpenHealthAtlas']
    run(common + [ROOT / 'desktop/macos/App.swift', '-o', macos / 'OpenHealthAtlas', '-framework', 'AppKit', '-framework', 'WebKit'], env=env)
    run(common + [ROOT / 'desktop/macos/MCPLauncher.swift', '-o', macos / 'openhealthatlas-mcp'], env=env)
    run(common + [ROOT / 'desktop/macos/Icon.swift', '-o', work / 'render-icon', '-framework', 'AppKit'], env=env)
    run([work / 'render-icon', work / 'icon.png'], env=env)
    iconset = work / 'AppIcon.iconset'; iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
            run(['/usr/bin/sips', '-z', str(size * scale), str(size * scale), work / 'icon.png', '--out', iconset / name], stdout=subprocess.DEVNULL)
    run(['/usr/bin/iconutil', '-c', 'icns', iconset, '-o', resources / 'AppIcon.icns'])
    info = {'CFBundleIdentifier': 'org.openhealthatlas.desktop', 'CFBundleName': 'Open Health Atlas',
            'CFBundleDisplayName': 'Open Health Atlas', 'CFBundleExecutable': 'OpenHealthAtlas',
            'CFBundleShortVersionString': args.version, 'CFBundleVersion': args.version,
            'CFBundlePackageType': 'APPL', 'CFBundleIconFile': 'AppIcon', 'LSMinimumSystemVersion': '13.0',
            'NSHighResolutionCapable': True, 'NSPrincipalClass': 'NSApplication',
            'NSHumanReadableCopyright': 'Copyright © 2026 Kajeesan Jeevendra. AGPL-3.0-only.',
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True,
                'NSExceptionDomains': {'127.0.0.1': {'NSExceptionAllowsInsecureHTTPLoads': True}}}}
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    # The output is newly generated; Finder/iCloud metadata is not distributable
    # code. Remove only these build-time attributes, never quarantine controls.
    for name in ('com.apple.FinderInfo', 'com.apple.ResourceFork'):
        subprocess.run(['/usr/bin/xattr', '-dr', name, str(bundle)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    signing = args.identity or '-'
    for binary in native_files(bundle):
        if binary == macos / 'OpenHealthAtlas': continue  # Outer bundle signs its main executable.
        command = ['/usr/bin/codesign', '--force', '--sign', signing]
        if args.identity: command += ['--options', 'runtime', '--timestamp']
        run_private(command + [binary])
    command = ['/usr/bin/codesign', '--force', '--sign', signing]
    if args.identity: command += ['--options', 'runtime', '--timestamp']
    run_private(command + [bundle])
    run_private(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle])
    run([python, '-I', '-B', '-c', 'import flask, waitress, mcp, sqlite3, ssl, cryptography; print("Bundled runtime imports passed")'], env=env)
    run([python, '-I', '-B', ROOT / 'scripts/audit_desktop_bundle.py', bundle, '--report', output / 'bundle-audit.json'], env=env)
    source_output = output / corresponding_source['filename']
    shutil.copyfile(source_archive, source_output)
    (output / 'build-receipt.json').write_text(json.dumps(build_receipt, indent=2) + '\n')
    published_app = output / bundle.name
    if published_app.exists(): shutil.rmtree(published_app)
    shutil.copytree(bundle, published_app, symlinks=True, copy_function=shutil.copy)
    run_private(['/usr/bin/codesign', '--verify', '--deep', '--strict', published_app])
    print(f'Built {bundle.name}: ' + ('Developer ID signed; notarization pending' if args.identity else 'ad hoc local test build; not a public release'))
    if args.skip_archive:
        shutil.rmtree(staging)
        return
    flavor = 'signed-unnotarized' if args.identity else 'local-unsigned'
    base = f'openhealthatlas-{args.version}-macos-{args.arch}'
    if args.notary_profile:
        submission = work / 'notarization.zip'
        run(['/usr/bin/ditto', '-c', '-k', '--keepParent', bundle, submission])
        build_receipt['app_notarization'] = notarize(submission, args.notary_profile, env)
        run_private(['/usr/bin/xcrun', 'stapler', 'staple', bundle], env=env)
        run_private(['/usr/bin/xcrun', 'stapler', 'validate', bundle], env=env)
        run_private(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle])
        run_private(['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=2', bundle])
        run([python, '-I', '-B', ROOT / 'scripts/audit_desktop_bundle.py', bundle, '--report', output / 'bundle-audit.json'], env=env)
        shutil.rmtree(published_app)
        shutil.copytree(bundle, published_app, symlinks=True, copy_function=shutil.copy)
        run_private(['/usr/bin/codesign', '--verify', '--deep', '--strict', published_app])
        flavor = 'signed-notarized'
    zipfile = output / f'{base}-{flavor}.zip'
    run(['/usr/bin/ditto', '-c', '-k', '--keepParent', bundle, zipfile])
    # Build-only tools stay outside both the runtime and corresponding product code.
    # Their exact lock and Finder settings are included in corresponding source.
    build_tools = work / 'dmg-tools'
    run(['uv', 'venv', '--python', sys.executable, build_tools], env=env)
    run(['uv', 'pip', 'install', '--python', build_tools / 'bin/python',
         '--require-hashes', '--only-binary', ':all:', '-r', ROOT / 'requirements-desktop-build.lock'], env=env)
    run(common + [ROOT / 'desktop/macos/InstallerBackground.swift', '-o', work / 'render-installer',
                  '-framework', 'AppKit'], env=env)
    background = work / 'installer.png'
    run([work / 'render-installer', background, '1'], env=env)
    run([work / 'render-installer', work / 'installer-retina.png', '2'], env=env)
    retina_background = work / 'installer.tiff'
    run(['/usr/bin/tiffutil', '-cathidpicheck', background, work / 'installer-retina.png',
         '-out', retina_background], env=env)
    dmg = staging / f'{base}-{flavor}.dmg'
    run([build_tools / 'bin/dmgbuild', '-s', ROOT / 'desktop/macos/dmg-settings.py',
         '-D', f'app={bundle}', '-D', f'background={retina_background}', 'Open Health Atlas', dmg], env=env)
    # Verify the payload after the packaging tool has copied it and written metadata.
    mounted = staging / 'verify-volume'
    mounted.mkdir()
    run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', mounted, dmg],
        stdout=subprocess.DEVNULL)
    try:
        run_private(['/usr/bin/codesign', '--verify', '--deep', '--strict', mounted / bundle.name])
        if (mounted / 'Applications').readlink() != Path('/Applications'):
            raise SystemExit('Installer Applications shortcut does not match its destination.')
        visible = sorted(path.name for path in mounted.iterdir() if not path.name.startswith('.'))
        if visible != ['Applications', bundle.name]:
            raise SystemExit('Installer contains unexpected visible items.')
    finally:
        run(['/usr/bin/hdiutil', 'detach', mounted], stdout=subprocess.DEVNULL)
    build_receipt['installer_layout'] = {
        'window_points': [640, 400], 'visible_items': ['Open Health Atlas.app', 'Applications'],
        'app_position': [160, 200], 'applications_position': [480, 200],
        'build_tools_lock_sha256': digest(ROOT / 'requirements-desktop-build.lock'),
        'background_1x_sha256': digest(background),
        'background_2x_sha256': digest(work / 'installer-retina.png'),
    }
    if args.notary_profile:
        run_private(['/usr/bin/codesign', '--force', '--sign', args.identity, '--timestamp', dmg])
        build_receipt['dmg_notarization'] = notarize(dmg, args.notary_profile, env)
        run_private(['/usr/bin/xcrun', 'stapler', 'staple', dmg], env=env)
        run_private(['/usr/bin/xcrun', 'stapler', 'validate', dmg], env=env)
        run_private(['/usr/bin/codesign', '--verify', '--strict', dmg])
        run_private(['/usr/sbin/spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', dmg])
        build_receipt['signing'].update(status='accepted-and-stapled', gatekeeper_assessment='app-and-dmg-passed')
    run(['/usr/bin/hdiutil', 'verify', dmg])
    final_dmg = output / dmg.name
    shutil.copyfile(dmg, final_dmg)
    if digest(dmg) != digest(final_dmg):
        raise SystemExit('Final disk-image copy failed integrity verification.')
    dmg = final_dmg
    build_receipt['artifacts'] = {path.name: {'sha256': digest(path), 'bytes': path.stat().st_size}
                                 for path in (zipfile, dmg, source_output)}
    (output / 'build-receipt.json').write_text(json.dumps(build_receipt, indent=2) + '\n')
    (output / f'{base}-{flavor}-SHA256SUMS.txt').write_text(''.join(f'{digest(path)}  {path.name}\n' for path in (zipfile, dmg, source_output)))
    print(f'Archives: {zipfile.name}, {dmg.name}')
    shutil.rmtree(staging)


if __name__ == '__main__': main()
