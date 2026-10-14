#!/usr/bin/env bash
# Non-destructive service recovery with explicit failure handling.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo' >&2;exit 1; }
ROOT=/opt/radar-sonar
[[ -f "$ROOT/bot.py" && -x "$ROOT/venv/bin/python" ]] || { echo 'Missing app or virtualenv; re-run installer update' >&2;exit 1; }
# A 077-created virtualenv may be executable only by root; open only venv read/execute.
chmod -R a+rX "$ROOT/venv"
id -u sonar-bot >/dev/null 2>&1 || { echo 'sonar-bot service account missing; use full installer' >&2;exit 1; }
runuser -u sonar-bot -- "$ROOT/venv/bin/python" -c 'import telegram, psycopg2, websockets' || { echo 'Service user cannot execute Python or import dependencies' >&2;exit 1; }
"$ROOT/venv/bin/python" -m compileall -q "$ROOT" || { echo 'Python compilation failed; refusing restart' >&2;exit 1; }
systemctl daemon-reload
systemctl restart sonar-bot
systemctl is-active --quiet sonar-bot || { journalctl -u sonar-bot -n 40 --no-pager;exit 1; }
if systemctl is-enabled --quiet sonar-web >/dev/null 2>&1;then
  systemctl restart sonar-web
  systemctl is-active --quiet sonar-web || { journalctl -u sonar-web -n 40 --no-pager;exit 1; }
fi
printf '✅ Services healthy. Database, agent reachability and SSL require separate checks.\n'
