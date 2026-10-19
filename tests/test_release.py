import importlib.util
from pathlib import Path
import zipfile

import pytest

from phonepilot.server import ROOT

spec = importlib.util.spec_from_file_location('release_packager', ROOT / 'tools/package_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / 'source'
    for name in release.REQUIRED:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('public source\n')
    return root


def test_archive_excludes_runtime_credentials_and_private_captures(sources, tmp_path):
    for name in ['var/runtime.json', '.venv/token.txt', '.env', 'iphone-private.png',
                 'preview-recording.webm', 'codex-mcp.toml', '.codex/config.toml']:
        p = sources / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('must never be packaged')
    archive = tmp_path / 'source.zip'
    release.package(sources, archive)
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist()) == {'phonepilot-local/' + p for p in release.REQUIRED}
        assert all(b'must never be packaged' not in z.read(n) for n in z.namelist())


def test_archive_rejects_symlink_to_private_material(sources, tmp_path):
    private = tmp_path / 'private.py'
    private.write_text('private')
    (sources / 'phonepilot/leak.py').symlink_to(private)
    with pytest.raises(ValueError, match='Symlink'):
        release.collect_files(sources)


def test_archive_rejects_literal_credentials(sources):
    (sources / 'phonepilot/accidental.py').write_text('token = "' + 'Z' * 32 + '"')
    with pytest.raises(ValueError, match='private data'):
        release.collect_files(sources)


def test_archive_rejects_local_home_paths(sources):
    (sources / 'README.md').write_text('/' + 'Users' + '/example/private-project')
    with pytest.raises(ValueError, match='private data'):
        release.collect_files(sources)


def test_archive_is_reproducible_and_keeps_launcher_executable(sources, tmp_path):
    (sources / '启动 PhonePilot.command').write_text('#!/bin/zsh\n')
    a, b = tmp_path / 'a.zip', tmp_path / 'b.zip'
    release.package(sources, a)
    release.package(sources, b)
    assert a.read_bytes() == b.read_bytes()
    with zipfile.ZipFile(a) as z:
        assert z.getinfo('phonepilot-local/启动 PhonePilot.command').external_attr >> 16 & 0o111 == 0o111
