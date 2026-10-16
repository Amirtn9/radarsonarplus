#!/usr/bin/env python3
"""Validate packaged tracked-source integrity; no network, no external dependencies."""
from __future__ import annotations
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'MANIFEST_SHA256.txt'


def files():
    for file in sorted(ROOT.rglob('*')):
        if not file.is_file() or file.is_symlink():
            continue
        parts = set(file.relative_to(ROOT).parts)
        if parts.intersection({'.git', '.pytest_cache', '__pycache__', '.venv', 'venv', '.ruff_cache', 'runtime'}):
            continue
        if file.name in {'MANIFEST_SHA256.txt', 'sonar_config.json', 'secret.key'}:
            continue
        if file.suffix in {'.pyc', '.pyo'} or file.name.endswith(('.log','.dump')):
            continue
        yield file


def digest(file):
    h = hashlib.sha256()
    with file.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def main():
    found = {file.relative_to(ROOT).as_posix(): digest(file) for file in files()}
    if sys.argv[1:] == ['--write']:
        MANIFEST.write_text(''.join(f'{value}  {name}\n' for name,value in sorted(found.items())), encoding='utf8')
        print(f'Generated manifest for {len(found)} files')
        return 0
    expected = {}
    if not MANIFEST.exists():
        print('Manifest missing', file=sys.stderr)
        return 1
    for line in MANIFEST.read_text('utf8').splitlines():
        if not line.strip():continue
        value, name = line.split('  ', 1)
        expected[name] = value
    missing = sorted(set(expected) - set(found))
    new = sorted(set(found) - set(expected))
    altered = sorted(key for key in expected.keys() & found.keys() if found[key] != expected[key])
    if missing or new or altered:
        print(f'Integrity failed: missing={missing}, new={new}, altered={altered}', file=sys.stderr)
        return 1
    print(f'Integrity verified for {len(expected)} source files')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
