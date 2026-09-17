"""Only manifest-reviewed source bytes may enter a desktop distribution."""
import csv
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest

_spec = importlib.util.spec_from_file_location(
    'desktop_build_inputs', Path(__file__).resolve().parents[1] / 'scripts/build_desktop.py')
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


def write_manifest(root, files):
    with (root / 'RELEASE_MANIFEST.tsv').open('w', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(('path', 'sha256', 'bytes', 'kind'))
        for name, data in sorted(files.items()):
            writer.writerow((name, hashlib.sha256(data).hexdigest(), len(data), 'fixture'))


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    names = [*build.FILES, *build.REQUIRED_DESKTOP_FILES]
    names += [directory + '/reviewed.txt' for directory in build.DIRECTORIES]
    files = {name: ('Reviewed fictional fixture: ' + name + '\n').encode() for name in names}
    files['docs/not-a-runtime-resource.md'] = b'Reviewed, but outside the runtime scope.\n'
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    write_manifest(root, files)
    monkeypatch.setattr(build, 'ROOT', root)
    return root, files, tmp_path / 'packaged'


def test_ignored_and_unlisted_local_data_never_enters_bundle(source):
    root, files, target = source
    (root / '.gitignore').write_text('*.jsonl\n')
    git = Path('/Library/Developer/CommandLineTools/usr/bin/git')
    command = str(git) if git.exists() else shutil.which('git')
    assert command, 'The desktop build requires Git on its build machine.'
    subprocess.run([command, 'init', '-q', str(root)], check=True)
    ignored = root / 'app/ignored.jsonl'
    ignored.write_text('{"fictional_local_record": "must remain outside the installer"}\n')
    subprocess.run([command, '-C', str(root), 'check-ignore', '-q', 'app/ignored.jsonl'], check=True)
    (root / 'desktop/unreviewed.py').write_text('unreviewed local module\n')
    build.copy_sources(target)
    actual = {path.relative_to(target).as_posix(): path.read_bytes()
              for path in target.rglob('*') if path.is_file()}
    assert actual == {name: data for name, data in files.items()
                      if name != 'docs/not-a-runtime-resource.md'}


@pytest.mark.parametrize('replacement', [b'changed size', None])
def test_altered_reviewed_bytes_fail_before_creating_output(source, replacement):
    root, files, target = source
    name = 'app/reviewed.txt'
    (root / name).write_bytes(replacement if replacement is not None else b'x' * len(files[name]))
    with pytest.raises(ValueError, match='source (size|hash) mismatch'):
        build.copy_sources(target)
    assert not target.exists()


@pytest.mark.parametrize('location', ['leaf', 'directory', 'manifest'])
def test_source_symlinks_are_rejected_even_when_target_bytes_match(source, location):
    root, _, target = source
    if location == 'directory':
        original = root / 'app'
    elif location == 'manifest':
        original = root / 'RELEASE_MANIFEST.tsv'
    else:
        original = root / 'app/reviewed.txt'
    moved = root / ('reviewed-' + original.name)
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ValueError, match='symlinked'):
        build.copy_sources(target)
    assert not target.exists()


@pytest.mark.parametrize('missing', ['LICENSE', 'desktop/server.py'])
def test_missing_required_manifest_entry_is_rejected(source, missing):
    root, files, target = source
    del files[missing]
    write_manifest(root, files)
    with pytest.raises(ValueError, match='Required source is absent'):
        build.copy_sources(target)
    assert not target.exists()


def test_missing_manifest_listed_file_is_rejected(source):
    root, _, target = source
    (root / 'app/reviewed.txt').unlink()
    with pytest.raises(ValueError, match='missing'):
        build.copy_sources(target)
    assert not target.exists()


@pytest.mark.parametrize('unsafe', ['app/../outside', 'app//reviewed.txt', 'app/reviewed.txt'])
def test_noncanonical_or_duplicate_manifest_paths_are_rejected(source, unsafe):
    root, _, target = source
    with (root / 'RELEASE_MANIFEST.tsv').open('a') as stream:
        stream.write(unsafe + '\t' + hashlib.sha256(b'').hexdigest() + '\t0\tfixture\n')
    with pytest.raises(ValueError, match='duplicate or unsafe'):
        build.copy_sources(target)
    assert not target.exists()


def test_named_pipe_is_rejected_without_reading_or_blocking(source):
    root, _, target = source
    path = root / 'app/reviewed.txt'
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ValueError, match='not a regular file'):
        build.copy_sources(target)
    assert not target.exists()
