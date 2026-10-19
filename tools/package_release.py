#!/usr/bin/env python3
"""Build a source-only archive; never traverse runtime directories or environments."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import zipfile

ROOT_FILES = {
    '.gitignore', '.gitattributes', '.prettierrc.json', 'LICENSE', 'THIRD_PARTY.md',
    'README.md', 'README.zh-CN.md', 'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md',
    'VERIFICATION.md', 'pyproject.toml', 'uv.lock', 'package.json', 'package-lock.json',
    'MANIFEST.in', '启动 PhonePilot.command', '首次安装.command',
}
SOURCE_TREES = {
    'phonepilot': {'.py', '.html', '.css', '.js'},
    'skills': {'.md', '.py', '.yaml'},
    'tools': {'.py'},
    'tests': {'.py', '.cjs'},
    'docs': {'.md'},
}
EXTRA_FILES = {
    'docs/images/console-light.png', 'docs/images/console-dark.png',
    '.github/workflows/ci.yml', '.github/ISSUE_TEMPLATE/bug_report.md',
    '.github/pull_request_template.md',
}
REQUIRED = {'README.md', 'LICENSE', 'pyproject.toml', 'uv.lock', 'phonepilot/server.py'}
PRIVATE_PATTERNS = [
    re.compile(r'/' + r'(?:Users|home)/[^/\s]+/'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'),
    re.compile(r'AKIA[A-Z0-9]{16}'),
    re.compile(r'-----BEGIN ' + r'(?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'''(?i)(?:token|secret|password)\s*[:=]\s*["']([A-Za-z0-9_+/=-]{24,})["']'''),
]


def collect_files(root: Path) -> list[Path]:
    root = root.absolute()
    if root.is_symlink():
        raise ValueError('Source root must not be a symlink')
    candidates = {root / name for name in ROOT_FILES | EXTRA_FILES if (root / name).exists()}
    for directory, extensions in SOURCE_TREES.items():
        folder = root / directory
        if folder.is_symlink():
            raise ValueError(f'Symlink is not distributable: {directory}')
        if folder.is_dir():
            for path in folder.rglob('*'):
                rel = path.relative_to(root)
                if any(part.startswith('.') or part == '__pycache__' for part in rel.parts):
                    continue
                if path.suffix in extensions:
                    candidates.add(path)
    files = []
    for path in sorted(candidates):
        rel = path.relative_to(root)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
            raise ValueError(f'Symlink is not distributable: {rel}')
        if not path.is_file():
            raise ValueError(f'Expected a regular source file: {rel}')
        if path.suffix != '.png':
            content = path.read_text(encoding='utf-8')
            if any(pattern.search(content) for pattern in PRIVATE_PATTERNS):
                raise ValueError(f'Potential private data in source: {rel}')
        files.append(path)
    missing = REQUIRED - {path.relative_to(root).as_posix() for path in files}
    if missing:
        raise ValueError(f'Missing required source files: {", ".join(sorted(missing))}')
    return files


def package(root: Path, output: Path) -> int:
    root = root.absolute()
    files = collect_files(root)
    output = output.absolute()
    if output in files:
        raise ValueError('Output must not overwrite a source file')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    stamp = datetime.fromtimestamp(int(os.getenv('SOURCE_DATE_EPOCH', '1767225600')), timezone.utc)
    if not 1980 <= stamp.year <= 2107:
        raise ValueError('SOURCE_DATE_EPOCH is outside the ZIP timestamp range')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                relative = path.relative_to(root).as_posix()
                info = zipfile.ZipInfo('phonepilot-local/' + relative, stamp.timetuple()[:6])
                info.create_system = 3
                mode = 0o755 if path.suffix == '.command' else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('dist/phonepilot-local-source.zip'))
    parser.add_argument('--check', action='store_true', help='Validate distributable sources without writing an archive')
    args = parser.parse_args()
    root = Path(__file__).absolute().parent.parent
    try:
        count = len(collect_files(root)) if args.check else package(root, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Release check failed: {exc}\n')
    print(f'{count} source files validated' if args.check else f'{count} source files packaged: {args.output}')


if __name__ == '__main__':
    main()
