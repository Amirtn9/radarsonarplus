#!/usr/bin/env python3
"""Build a deterministic source release after validating MANIFEST_SHA256.txt."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = "radarsonarplus-main"


def release_files() -> list[Path]:
    # Reuse the exact exclusion policy of the manifest verifier.
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_manifest import files

    selected = list(files())
    manifest = ROOT / "MANIFEST_SHA256.txt"
    if manifest.is_file():
        selected.append(manifest)
    return sorted(set(selected), key=lambda item: item.relative_to(ROOT).as_posix())


def build(output: Path) -> str:
    output = output.expanduser().resolve()
    dist_root = (ROOT / "dist").resolve()
    if ROOT == output or ROOT in output.parents and dist_root not in output.parents:
        raise ValueError("Release output must be outside the source tree or inside its dist/ directory")
    output.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_manifest.py")],
        cwd=ROOT,
        check=True,
    )
    files_to_add = release_files()
    fd, temp_name = tempfile.mkstemp(prefix=".sonar-release-", suffix=".zip", dir=output.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source in files_to_add:
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"Refusing non-regular release input: {source}")
                relative = source.relative_to(ROOT).as_posix()
                info = zipfile.ZipInfo(f"{RELEASE_ROOT}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = stat.S_IMODE(source.stat().st_mode)
                info.external_attr = ((stat.S_IFREG | mode) & 0xFFFF) << 16
                info.flag_bits |= 0x800  # UTF-8 names
                archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temp, output)
        os.chmod(output, 0o644)
    finally:
        temp.unlink(missing_ok=True)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"Built {output} ({output.stat().st_size} bytes)")
    print(f"SHA-256 {digest}")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    output = args.output or (ROOT / "dist" / f"SONAR-RADAR-PRO-{version}.zip")
    try:
        build(output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Release build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
