#!/usr/bin/env bash
# Optional, explicitly run by root on the deployed VPS; no font bundled in ZIP.
set -Eeuo pipefail
umask 077
[[ "$EUID" -eq 0 ]] || { echo 'Run as root';exit 1; }
DEST=/var/lib/sonar-radar/fonts
OWNER=sonar-bot
# Tag from the SIL OFL 1.1 official source, not an unversioned branch.
BASE=https://raw.githubusercontent.com/rastikerdar/vazirmatn/v33.003
install -d -m 0750 -o "$OWNER" -g "$OWNER" "$DEST"
TMP="$(mktemp -d)";trap 'rm -rf -- "$TMP"' EXIT
curl --fail --show-error --silent --location --proto '=https' --tlsv1.2 --max-time 40 --max-filesize 5000000 \
  "$BASE/fonts/ttf/Vazirmatn-Regular.ttf" -o "$TMP/font.ttf"
curl --fail --show-error --silent --location --proto '=https' --tlsv1.2 --max-time 20 \
  "$BASE/OFL.txt" -o "$TMP/OFL.txt"
[[ "$(wc -c < "$TMP/font.ttf")" -ge 50000 ]] || { echo 'Font looks truncated';exit 1; }
[[ "$(wc -c < "$TMP/OFL.txt")" -ge 1000 ]] || { echo 'Font license incomplete';exit 1; }
if command -v fc-scan >/dev/null;then
  fc-scan --format '%{family}' "$TMP/font.ttf" | grep -qi 'Vazirmatn' || { echo 'Not a Vazirmatn font';exit 1; }
fi
install -m 0644 -o "$OWNER" -g "$OWNER" "$TMP/font.ttf" "$DEST/Vazirmatn-Regular.ttf"
install -m 0644 -o "$OWNER" -g "$OWNER" "$TMP/OFL.txt" "$DEST/OFL.txt"
echo 'Installed Vazirmatn locally for server-side PDFs (SIL OFL 1.1)'
