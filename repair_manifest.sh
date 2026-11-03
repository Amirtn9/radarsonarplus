#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  echo "Usage: sudo bash repair_manifest.sh /path/to/radarsonarplus"
  echo "Restores the two manifest-listed Git dotfiles and moves the unlisted patch note outside the source tree."
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" || $# -ne 1 ]]; then usage; exit $([[ $# -eq 1 ]] && echo 0 || echo 2); fi
ROOT="$(realpath -e -- "$1")"
[[ -d "$ROOT" && "$ROOT" != / ]] || { echo "Invalid source directory: $ROOT" >&2; exit 2; }
[[ -f "$ROOT/bot.py" && -f "$ROOT/install.sh" && -f "$ROOT/MANIFEST_SHA256.txt" && -f "$ROOT/scripts/verify_manifest.py" ]] || {
  echo "This does not look like a complete RadarSonarPlus source root: $ROOT" >&2; exit 2;
}
PAYLOAD="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/payload"
for name in .gitattributes .gitignore; do
  [[ -f "$PAYLOAD/$name" ]] || { echo "Missing repair payload: $name" >&2; exit 2; }
  expected="$(awk -v f="$name" '$2==f {print $1}' "$ROOT/MANIFEST_SHA256.txt")"
  actual="$(sha256sum "$PAYLOAD/$name" | awk '{print $1}')"
  [[ -n "$expected" && "$actual" == "$expected" ]] || {
    echo "Repair payload hash does not match this release manifest: $name" >&2; exit 3;
  }
  install -m 0644 -- "$PAYLOAD/$name" "$ROOT/$name"
  echo "Restored $name (release hash verified)."
done

if [[ -f "$ROOT/APPLY_PATCH_FA.txt" ]]; then
  backup="$(dirname -- "$ROOT")/APPLY_PATCH_FA.txt.saved.$(date -u +%Y%m%dT%H%M%SZ)"
  [[ ! -e "$backup" ]] || { echo "Backup already exists: $backup" >&2; exit 4; }
  cp -a -- "$ROOT/APPLY_PATCH_FA.txt" "$backup"
  rm -- "$ROOT/APPLY_PATCH_FA.txt"
  echo "Moved unlisted helper note outside source tree: $backup"
fi

python3 "$ROOT/scripts/verify_manifest.py"
echo
echo "Manifest repair succeeded. Source: $ROOT"
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Git changes to publish (if you want future GitHub bootstrap to work):"
  git -C "$ROOT" status --short
  echo "Then run: git -C '$ROOT' add -A && git -C '$ROOT' commit -m 'Fix release manifest metadata' && git -C '$ROOT' push origin main"
fi
