#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  echo 'Usage: sudo bash repair_manifest.sh /path/to/radarsonarplus-source'
  echo 'Repairs only the exact known mismatch for revision be30eef94b92.'
}
if [[ ${1:-} == '-h' || ${1:-} == '--help' ]]; then usage; exit 0; fi
if [[ $# -ne 1 ]]; then usage >&2; exit 2; fi

command -v python3 >/dev/null 2>&1 || { echo 'python3 is required.' >&2; exit 2; }
command -v sha256sum >/dev/null 2>&1 || { echo 'sha256sum is required.' >&2; exit 2; }
command -v realpath >/dev/null 2>&1 || { echo 'realpath is required.' >&2; exit 2; }

ROOT="$(realpath -e -- "$1")"
[[ -d "$ROOT" && "$ROOT" != '/' ]] || { echo "Invalid source directory: $ROOT" >&2; exit 2; }
[[ -f "$ROOT/install.sh" && -f "$ROOT/bot.py" && -f "$ROOT/MANIFEST_SHA256.txt" && -f "$ROOT/scripts/verify_manifest.py" ]] || {
  echo "Not a complete Sonar Radar source root: $ROOT" >&2; exit 2;
}

# This patch is pinned to the exact source revision shown in the user's log.
if git -C "$ROOT" rev-parse --verify HEAD >/dev/null 2>&1; then
  REV="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
  [[ "$REV" == 'be30eef94b92' ]] || {
    echo "Refusing unexpected source revision: $REV (expected be30eef94b92). No files changed." >&2
    exit 3
  }
else
  echo 'This repair requires a Git checkout at revision be30eef94b92; no files changed.' >&2
  exit 3
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PAYLOAD="$SCRIPT_DIR/payload"
for name in .gitattributes .gitignore; do
  [[ -f "$PAYLOAD/$name" && ! -L "$PAYLOAD/$name" ]] || { echo "Missing repair payload: $name" >&2; exit 4; }
  expected="$(awk -v f="$name" '$2 == f { print $1 }' "$ROOT/MANIFEST_SHA256.txt")"
  actual="$(sha256sum -- "$PAYLOAD/$name" | awk '{print $1}')"
  [[ -n "$expected" && "$actual" == "$expected" ]] || {
    echo "Payload hash does not match this checkout's Manifest: $name" >&2
    echo "Expected: ${expected:-<not-listed>}" >&2
    echo "Actual:   $actual" >&2
    exit 4
  }
done

# Require the exact failure pattern reported by this server. This avoids
# rewriting the manifest or masking unrelated source changes.
python3 - "$ROOT" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = root / 'MANIFEST_SHA256.txt'
expected = {}
for line in manifest.read_text(encoding='utf-8').splitlines():
    if line.strip():
        digest, name = line.split('  ', 1)
        expected[name] = digest
excluded = {'.git', '.pytest_cache', '__pycache__', '.mypy_cache', '.ruff_cache',
            '.venv', 'venv', 'env', '.cache', 'runtime', 'logs', 'backups',
            'node_modules', 'dist', 'build'}
found = {}
for path in sorted(root.rglob('*')):
    if not path.is_file() or path.is_symlink():
        continue
    rel = path.relative_to(root)
    if set(rel.parts) & excluded:
        continue
    if path.name in {'MANIFEST_SHA256.txt', 'sonar_config.json', 'secret.key'}:
        continue
    if path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example'):
        continue
    if path.suffix.lower() in {'.pyc', '.pyo', '.pem', '.key', '.log', '.dump', '.sqlite', '.sqlite3'}:
        continue
    found[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
missing = sorted(set(expected) - set(found))
new = sorted(set(found) - set(expected))
altered = sorted(k for k in expected.keys() & found.keys() if expected[k] != found[k])
want_missing = ['.gitattributes', '.gitignore']
want_new = ['README_FA.md', 'repair_manifest.sh']
if missing != want_missing or new != want_new or altered:
    print(f'Refusing unexpected integrity state: missing={missing}, new={new}, altered={altered}', file=sys.stderr)
    raise SystemExit(5)
for name in want_new:
    p = root / name
    if not p.is_file() or p.is_symlink():
        print(f'Refusing unexpected helper path: {name}', file=sys.stderr)
        raise SystemExit(5)
print('Exact known integrity mismatch confirmed; no unrelated file changes detected.')
PY

QUARANTINE="$(mktemp -d "$(dirname -- "$ROOT")/.sonar-manifest-quarantine.XXXXXXXX")"
INSTALLED=()
MOVED=()
rollback() {
  local name
  for name in "${MOVED[@]:-}"; do
    if [[ -e "$QUARANTINE/$name" && ! -e "$ROOT/$name" ]]; then mv -- "$QUARANTINE/$name" "$ROOT/$name" || true; fi
  done
  for name in "${INSTALLED[@]:-}"; do rm -f -- "$ROOT/$name" || true; done
  rmdir -- "$QUARANTINE" 2>/dev/null || true
}
trap 'rc=$?; if (( rc != 0 )); then echo "Repair failed; rolling back file moves." >&2; rollback; fi' EXIT

for name in .gitattributes .gitignore; do
  install -m 0644 -- "$PAYLOAD/$name" "$ROOT/$name"
  INSTALLED+=("$name")
  echo "Restored $name (SHA-256 verified against the release manifest)."
done
for name in README_FA.md repair_manifest.sh; do
  mv -- "$ROOT/$name" "$QUARANTINE/$name"
  MOVED+=("$name")
  echo "Preserved unlisted helper outside source: $QUARANTINE/$name"
done

python3 "$ROOT/scripts/verify_manifest.py"
echo
echo 'Manifest repair succeeded.'
echo "Source: $ROOT"
echo "Preserved helper files: $QUARANTINE"
trap - EXIT
