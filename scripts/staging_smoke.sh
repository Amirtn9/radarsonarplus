#!/usr/bin/env bash
# Non-destructive on-host smoke checks; statuses are PASS/FAIL/NOT_TESTED.
set -Eeuo pipefail
export LC_ALL=C
passed=0 failed=0
check() { local label="$1";shift; if "$@" >/dev/null 2>&1;then echo "PASS $label";passed=$((passed+1));else echo "FAIL $label";failed=$((failed+1));fi; }
for srv in sonar-bot sonar-web postgresql;do
  check "service:$srv" systemctl is-active --quiet "$srv"
done
check 'postgresql:socket_query' runuser -u postgres -- psql -v ON_ERROR_STOP=1 -tAc 'SELECT 1'
web_port="${SONAR_WEB_PORT:-8090}"
[[ "$web_port" =~ ^[0-9]+$ ]] || { echo 'FAIL web_port:invalid';exit 2; }
if command -v curl >/dev/null 2>&1;then
  check 'web:liveness' curl -fsS --max-time 5 "http://127.0.0.1:${web_port}/healthz"
  check 'web:database_readiness' curl -fsS --max-time 5 "http://127.0.0.1:${web_port}/readyz"
else echo 'NOT_TESTED web:HTTP (curl unavailable)';fi
printf 'NOT_TESTED Telegram API (needs real bot token/network)\nNOT_TESTED remote SSH/WSS/TLS (needs a dedicated staging node)\n'
echo "RESULT PASS=$passed FAIL=$failed"
[[ "$failed" -eq 0 ]]
