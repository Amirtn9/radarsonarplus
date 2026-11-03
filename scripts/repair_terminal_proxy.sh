#!/usr/bin/env bash
set -Eeuo pipefail

SITE="${SONAR_NGINX_SITE:-/etc/nginx/sites-available/sonar-radar.conf}"
ENV_FILE="${SONAR_ENV_FILE:-/etc/sonar-bot.env}"
BACKUP_DIR="/var/backups/sonar-radar"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/repair_terminal_proxy.sh" >&2
  exit 1
fi
command -v nginx >/dev/null 2>&1 || { echo "nginx is not installed." >&2; exit 1; }
[[ -f "$SITE" ]] || { echo "Nginx site not found: $SITE" >&2; exit 1; }

PORT="${SONAR_WEB_PORT:-}"
if [[ -z "$PORT" && -f "$ENV_FILE" ]]; then
  PORT="$(grep -m1 '^SONAR_WEB_PORT=' "$ENV_FILE" | cut -d= -f2- | sed "s/[\"']//g" | tr -d '[:space:]')"
fi
PORT="${PORT:-8090}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT >= 1 && PORT <= 65535 )) || {
  echo "Invalid SONAR_WEB_PORT value." >&2
  exit 1
}

install -d -m 0700 "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_DIR/nginx-terminal-${STAMP}.conf"
cp -a "$SITE" "$BACKUP"

python3 - "$SITE" "$PORT" <<'PY'
from pathlib import Path
import re
import sys

site = Path(sys.argv[1])
port = int(sys.argv[2])
text = site.read_text(encoding="utf-8")

block = f'''    # Browser SSH terminal: WebSocket upgrade and long-lived PTY stream.
    location ^~ /api/terminal/ {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
        proxy_request_buffering off;
    }}
'''

pattern = re.compile(
    r'^[ \t]*location\s+(?:\^~\s+)?/api/terminal/\s*\{.*?^[ \t]*\}',
    re.MULTILINE | re.DOTALL,
)
match = pattern.search(text)
if match:
    text = text[:match.start()] + block.rstrip() + text[match.end():]
else:
    marker = re.search(r'^[ \t]*location\s+/\s*\{', text, re.MULTILINE)
    if not marker:
        raise SystemExit("Could not find 'location / {' in the managed Nginx site; no changes applied.")
    text = text[:marker.start()] + block + text[marker.start():]

site.write_text(text, encoding="utf-8")
PY

if ! nginx -t; then
  cp -a "$BACKUP" "$SITE"
  echo "Nginx validation failed. Original config restored from $BACKUP" >&2
  exit 1
fi

if ! systemctl reload nginx; then
  cp -a "$BACKUP" "$SITE"
  nginx -t >/dev/null 2>&1 || true
  systemctl reload nginx >/dev/null 2>&1 || true
  echo "Nginx reload failed. Original config restored from $BACKUP" >&2
  exit 1
fi
echo "Nginx WebSocket proxy is configured."
echo "Backup: $BACKUP"
echo "Next: restart the web API after deploying the Sonar patch:"
echo "  sudo systemctl restart sonar-web"
