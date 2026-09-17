#!/usr/bin/env python3
"""Build a self-contained macOS app. Outputs and caches must stay outside source.

Build prerequisites (maintainers only): macOS, CLT/Swift, Python 3.11+, uv.
End users need none of these. Downloads are pinned and verified; signing and
notarization are opt-in and use an existing Keychain profile, never raw secrets.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_RELEASE = '20260623'
PYTHON_VERSION = '3.12.13'
RUNTIMES = {
    'arm64': ('aarch64', '41df7d3ae4757e84b97874f76d634268456aaa271740d33f968d826374998fb7'),
    'x86_64': ('x86_64', 'a6bbea996c5f14eb55ab275889d2df45408deec504b4a7219d7b59c045b2555e'),
}
# Reviewed product resources; no deployment installers, secrets, database or devserver.
DIRECTORIES = ('app', 'toolkit/hermes_insights', 'desktop/static', 'desktop/templates')
FILES = ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'toolkit/health.py',
         'toolkit/SCHEMA.sql', 'docs/authored-submuscle-map.md', 'deploy/bridge_commands.py', 'deploy/hermes-bridge',
         'scripts/init_hermes.py', 'scripts/make_demo_db.py', 'scripts/openhealthatlas_mcp.py')
FULL_RUNTIME_HASHES = {
    'arm64': 'ce5a2d552077d869f69dc25d834c2fe4d036f9d78e770fb5d916273db802cabc',
    'x86_64': '3b2ee510354f51b6bda71fec7d5cf70dfad29ed672aa5847d1f5216ee22fc5ef',
}
MACHO_MAGICS = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def copy_sources(target):
    for directory in DIRECTORIES:
        shutil.copytree(ROOT / directory, target / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.so', '.DS_Store'))
    for filename in FILES:
        destination = target / filename; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / filename, destination)
    # Desktop package is new reviewed code; keep native build sources out of runtime.
    for source in sorted((ROOT / 'desktop').glob('*.py')):
        destination = target / 'desktop' / source.name
        shutil.copy2(source, destination)
    if not (target / 'desktop/launcher.py').exists():
        raise SystemExit('Desktop launcher is missing; refusing an incomplete application.')


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
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--arch', choices=tuple(RUNTIMES), default=platform.machine())
    parser.add_argument('--identity', help='Existing Developer ID Application signing identity')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile')
    parser.add_argument('--skip-archive', action='store_true', help='Build .app only for local acceptance')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or args.arch != platform.machine():
        parser.error('Build natively on the target macOS CPU architecture; cross-builds are not validated.')
    if args.notary_profile and not args.identity:
        parser.error('--notary-profile requires a Developer ID --identity')
    if args.identity and not args.identity.startswith('Developer ID Application:'):
        parser.error('Public distribution requires a Developer ID Application identity')
    output = args.output_dir.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Build output must be outside the source checkout')
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
    clt = Path('/Library/Developer/CommandLineTools')
    developer = os.environ.get('DEVELOPER_DIR') or (str(clt) if (clt / 'usr/bin/swiftc').exists() else subprocess.check_output(['/usr/bin/xcode-select', '-p'], text=True).strip())
    env = dict(os.environ, UV_CACHE_DIR=str(work / 'uv-cache'), UV_PYTHON_DOWNLOADS='never',
               PYTHONDONTWRITEBYTECODE='1', DEVELOPER_DIR=developer)
    # Require prebuilt reviewed wheels: no build scripts or compiler dependency at first use.
    run(['uv', 'pip', 'install', '--python', python, '--require-hashes', '--only-binary', ':all:', '--link-mode', 'copy',
         '-r', ROOT / 'requirements-desktop.lock'], env=env)
    copy_sources(resources / 'app')
    git = str(clt / 'usr/bin/git') if (clt / 'usr/bin/git').exists() else shutil.which('git')
    if not git: raise SystemExit('Git is required on the build machine to identify the packaged source.')
    code_version = subprocess.check_output([git, '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output([git, '-C', str(ROOT), 'status', '--porcelain'], text=True).strip())
    if args.identity and dirty:
        raise SystemExit('Signed distribution requires a clean reviewed source commit.')
    (resources / 'app/desktop/build-info.json').write_text(json.dumps({'version': args.version, 'source_commit': code_version, 'source_dirty': dirty, 'runtime_sha256': runtime_hash, 'lock_sha256': digest(ROOT / 'requirements-desktop.lock'), 'architecture': args.arch, 'build_macos': platform.mac_ver()[0]}, indent=2) + '\n')
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
        'import importlib.metadata,json; print(json.dumps(sorted([{ "name": d.metadata["Name"], "version": d.version, "license": d.metadata.get("License-Expression") or d.metadata.get("License") or "See bundled dist-info licenses" } for d in importlib.metadata.distributions()], key=lambda d:d["name"].lower()), indent=2))'], env=env, text=True)
    notices.write_text('Open Health Atlas bundles CPython from python-build-standalone.\nRuntime license texts and exact upstream metadata are in Resources/PythonLicenses; wheel license texts and metadata\nare retained in PythonRuntime/lib/python3.12/site-packages/*.dist-info.\n\n' + inventory)
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
            'NSHumanReadableCopyright': 'Copyright © 2026 Kajeesan Jeevendra. MIT License.',
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
        run(command + [binary], stdout=subprocess.DEVNULL)
    command = ['/usr/bin/codesign', '--force', '--sign', signing]
    if args.identity: command += ['--options', 'runtime', '--timestamp']
    run(command + [bundle])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle])
    run([python, '-I', '-B', '-c', 'import flask, waitress, mcp, sqlite3, ssl, cryptography; print("Bundled runtime imports passed")'], env=env)
    run([python, '-I', '-B', ROOT / 'scripts/audit_desktop_bundle.py', bundle, '--report', output / 'bundle-audit.json'], env=env)
    published_app = output / bundle.name
    if published_app.exists(): shutil.rmtree(published_app)
    shutil.copytree(bundle, published_app, symlinks=True, copy_function=shutil.copy)
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', published_app])
    print(f'Built {bundle.name}: ' + ('Developer ID signed; notarization pending' if args.identity else 'ad hoc local test build; not a public release'))
    if args.skip_archive:
        shutil.rmtree(staging)
        return
    flavor = 'signed-unnotarized' if args.identity else 'local-unsigned'
    base = f'openhealthatlas-{args.version}-macos-{args.arch}'
    if args.notary_profile:
        submission = work / 'notarization.zip'
        run(['/usr/bin/ditto', '-c', '-k', '--keepParent', bundle, submission])
        run(['/usr/bin/xcrun', 'notarytool', 'submit', submission, '--keychain-profile', args.notary_profile, '--wait'], env=env)
        run(['/usr/bin/xcrun', 'stapler', 'staple', bundle], env=env)
        run(['/usr/bin/xcrun', 'stapler', 'validate', bundle], env=env)
        run(['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=2', bundle])
        run([python, '-I', '-B', ROOT / 'scripts/audit_desktop_bundle.py', bundle, '--report', output / 'bundle-audit.json'], env=env)
        shutil.rmtree(published_app)
        shutil.copytree(bundle, published_app, symlinks=True, copy_function=shutil.copy)
        flavor = 'signed-notarized'
    zipfile = output / f'{base}-{flavor}.zip'
    run(['/usr/bin/ditto', '-c', '-k', '--keepParent', bundle, zipfile])
    volume = staging / 'volume'
    if volume.exists(): shutil.rmtree(volume)
    volume.mkdir(); shutil.copytree(bundle, volume / bundle.name, symlinks=True, copy_function=shutil.copy)
    (volume / 'Applications').symlink_to('/Applications')
    (volume / 'Install.txt').write_text('Drag Open Health Atlas into Applications, then open it there.\nYour records remain in your user Library/Application Support/Open Health Atlas.\nRemoving the app does not erase these records.\n' + ('\nLOCAL TEST BUILD: not signed for public distribution.\n' if not args.notary_profile else ''))
    dmg = output / f'{base}-{flavor}.dmg'
    run(['/usr/bin/hdiutil', 'create', '-volname', 'Open Health Atlas', '-srcfolder', volume, '-ov', '-format', 'UDZO', dmg])
    if args.notary_profile:
        run(['/usr/bin/codesign', '--force', '--sign', args.identity, '--timestamp', dmg])
        run(['/usr/bin/xcrun', 'notarytool', 'submit', dmg, '--keychain-profile', args.notary_profile, '--wait'], env=env)
        run(['/usr/bin/xcrun', 'stapler', 'staple', dmg], env=env)
        run(['/usr/bin/xcrun', 'stapler', 'validate', dmg], env=env)
    (output / f'{base}-{flavor}-SHA256SUMS.txt').write_text(''.join(f'{digest(path)}  {path.name}\n' for path in (zipfile, dmg)))
    print(f'Archives: {zipfile.name}, {dmg.name}')
    shutil.rmtree(staging)


if __name__ == '__main__': main()
