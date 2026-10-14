#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR=/opt/radar-sonar
problems=0
printf '🦇 Sonar Radar - Service Health\n'
check() { if "$@"; then printf 'OK  %s\n' "$*"; else printf 'BAD %s\n' "$*"; problems=$((problems+1));fi; }
check test -f "$APP_DIR/bot.py"
check test -x "$APP_DIR/venv/bin/python"
check systemctl is-active --quiet sonar-bot
if systemctl is-enabled --quiet sonar-web >/dev/null 2>&1; then
  check systemctl is-active --quiet sonar-web
fi
if [[ -f "$APP_DIR/sonar_config.json" ]];then
  perms="$(stat -c '%a' "$APP_DIR/sonar_config.json")"
  [[ "$perms" == 600 ]] && printf 'OK  config permissions\n' || { echo 'BAD config permissions';problems=$((problems+1)); }
else
  echo 'BAD config missing';problems=$((problems+1))
fi
printf 'Health failures: %s\n' "$problems"
((problems == 0))
