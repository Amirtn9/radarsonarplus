#!/usr/bin/env bash
# Read-only Sonar Radar observability console.
set -Eeuo pipefail
# The support log reader is an administrator utility. Re-run under sudo so
# journalctl and the root-owned installer log are not silently incomplete.
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || { echo 'sudo is required; run: sudo bash scripts/logs.sh ...' >&2; exit 77; }
  exec sudo -- bash "$0" "$@"
fi
usage() {
  cat <<'USAGE'
Sonar Radar logs (read-only)
  sudo bash scripts/logs.sh bot [--follow] [--lines N] [--since TIME] [--grep PATTERN]
  sudo bash scripts/logs.sh web [--follow] [--lines N] [--since TIME]
  sudo bash scripts/logs.sh all [--follow] [--lines N]
  sudo bash scripts/logs.sh errors [--lines N]
  sudo bash scripts/logs.sh json [--lines N]
  sudo bash scripts/logs.sh install [--lines N]
USAGE
}
[[ $# -eq 0 || "${1:-}" == '--help' ]] && { usage; exit 0; }
kind="$1"; shift
lines=150; since=''; grep_pattern=''; follow=0
while (( $# )); do
  case "$1" in
    --follow|-f) follow=1; shift;;
    --lines|-n) [[ $# -ge 2 && "$2" =~ ^[0-9]{1,5}$ ]] || exit 2; lines="$2"; shift 2;;
    --since) [[ $# -ge 2 ]] || exit 2; since="$2";shift 2;;
    --grep) [[ $# -ge 2 ]] || exit 2; grep_pattern="$2"; shift 2;;
    *) printf 'Unknown argument: %s\n' "$1" >&2;exit 2;;
  esac
done
((lines <= 5000)) || { echo 'Max 5000 lines' >&2; exit 2; }
args=(-n "$lines" --no-pager --output=short-iso-precise)
(( follow )) && args+=(-f)
[[ -n "$since" ]] && args+=(--since "$since")
case "$kind" in
  bot) args+=(-u sonar-bot);;
  web) args+=(-u sonar-web);;
  all) args+=(-u sonar-bot -u sonar-web);;
  errors) args+=( -p err -u sonar-bot -u sonar-web );;
  json) file=/var/log/sonar/sonar.json;
        [[ -f "$file" ]] || { echo 'JSON log missing' >&2;exit 1; }
        if ((follow)); then tail -n "$lines" -F -- "$file";else tail -n "$lines" -- "$file";fi;exit;;
  install) file=/var/log/sonar-install.log;
           [[ -f "$file" ]] || { echo 'Install log missing' >&2;exit 1; }
           if ((follow)); then tail -n "$lines" -F -- "$file";else tail -n "$lines" -- "$file";fi;exit;;
  *) usage; exit 2;;
esac
if [[ -n "$grep_pattern" ]]; then
  journalctl "${args[@]}" | grep --line-buffered -i -F -- "$grep_pattern" || true
else
  journalctl "${args[@]}"
fi
