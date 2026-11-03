#!/usr/bin/env bash
# Non-destructive repair for the installed Sonar bot service.
# Repairs venv traversal/execute permissions and regenerates the systemd unit.
set -Eeuo pipefail

APP_DIR=/opt/radar-sonar
APP_USER=sonar-bot
STATE_DIR=/var/lib/sonar-radar
LOG_DIR=/var/log/sonar
ENV_FILE=/etc/sonar-bot.env
UNIT=/etc/systemd/system/sonar-bot.service
SERVICE=sonar-bot

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '  • %s\n' "$*"; }
ok() { printf '  ✓ %s\n' "$*"; }

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'Run as root: sudo bash scripts/repair.sh'
command -v systemctl >/dev/null 2>&1 || fail 'systemd is not available.'
command -v runuser >/dev/null 2>&1 || fail 'runuser is not available.'
command -v findmnt >/dev/null 2>&1 || fail 'findmnt is required for safe mount checks.'
id -u "$APP_USER" >/dev/null 2>&1 || fail "Service account '$APP_USER' does not exist. Run the full installer first."
[[ -d "$APP_DIR" && -f "$APP_DIR/bot.py" ]] || fail "Application source not found at $APP_DIR."
[[ -d "$APP_DIR/venv" ]] || fail "Virtual environment not found at $APP_DIR/venv. Run the full installer/update."
[[ ! -L "$UNIT" ]] || fail "Refusing to replace symlinked systemd unit: $UNIT"

PYTHON="$APP_DIR/venv/bin/python"
[[ -e "$PYTHON" ]] || fail "Virtualenv interpreter is missing: $PYTHON"
REAL_PYTHON="$(readlink -f -- "$PYTHON")" || fail 'Could not resolve the virtualenv interpreter symlink.'
[[ -f "$REAL_PYTHON" ]] || fail "Resolved Python interpreter is not a regular file: $REAL_PYTHON"

# chmod cannot override a noexec mount. Detect this before changing permissions.
mount_options="$(findmnt -n -o OPTIONS -T "$REAL_PYTHON" 2>/dev/null || true)"
case ",${mount_options}," in
  *,noexec,*)
    fail "Python resolves to $REAL_PYTHON on a noexec mount ($mount_options). Move the installation/venv to an exec-enabled filesystem; chmod will not fix this."
    ;;
esac

info 'Repairing traversal and read/execute permissions for the virtual environment.'
# The installer uses umask 077; Python venv directories/files can otherwise be
# inaccessible to the unprivileged sonar-bot account. Secrets are not in venv.
chmod a+rx /opt "$APP_DIR" "$APP_DIR/venv" "$APP_DIR/venv/bin"
chmod -R a+rX "$APP_DIR/venv"

# Test execution as the actual systemd service account, not as root.
if ! runuser -u "$APP_USER" -- "$PYTHON" -c 'import sys; print(sys.version.split()[0])'; then
  info 'Path permissions after repair:'
  namei -l "$PYTHON" >&2 || true
  fail 'sonar-bot still cannot execute the virtualenv Python. Check ACLs, AppArmor and the resolved mount options.'
fi
ok 'Virtualenv Python executes as sonar-bot.'

if ! runuser -u "$APP_USER" -- "$PYTHON" -c 'import telegram, psycopg2, websockets'; then
  fail 'Python executes, but required Bot modules are missing or broken. Run the full installer update and inspect /var/log/sonar-install.log.'
fi
ok 'Core Bot Python dependencies import successfully.'

# Preserve the previous unit before replacing it. The old unit may have
# StartLimitIntervalSec inside [Service], which systemd ignores.
install -d -m 0700 /var/backups/sonar-radar
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -f "$UNIT" && ! -L "$UNIT" ]]; then
  cp -p -- "$UNIT" "/var/backups/sonar-radar/sonar-bot.service.pre-repair-$stamp"
  chmod 0600 "/var/backups/sonar-radar/sonar-bot.service.pre-repair-$stamp"
fi

unit_tmp="$(mktemp /etc/systemd/system/.sonar-bot.service.XXXXXXXX)"
trap 'rm -f -- "$unit_tmp"' EXIT
cat > "$unit_tmp" <<EOF_UNIT
[Unit]
Description=Sonar Radar Pro Telegram Bot
Wants=network-online.target
After=network-online.target postgresql.service
StartLimitIntervalSec=120
StartLimitBurst=8

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON -u $APP_DIR/bot.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sonar-bot
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$LOG_DIR $STATE_DIR
UMask=0077

[Install]
WantedBy=multi-user.target
EOF_UNIT
chmod 0644 "$unit_tmp"
chown root:root "$unit_tmp"
install -o root -g root -m 0644 "$unit_tmp" "$UNIT"

if ! systemd-analyze verify "$UNIT"; then
  if [[ -n "${stamp:-}" && -f "/var/backups/sonar-radar/sonar-bot.service.pre-repair-$stamp" ]]; then
    cp -p -- "/var/backups/sonar-radar/sonar-bot.service.pre-repair-$stamp" "$UNIT"
  else
    rm -f -- "$UNIT"
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  fail 'Generated systemd unit failed validation; previous unit was restored when available.'
fi
systemctl daemon-reload
ok 'Systemd unit regenerated; StartLimit settings are in [Unit].'

systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
if ! systemctl restart "$SERVICE"; then
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  fail 'systemd could not restart the Bot. Review the journal above.'
fi
sleep 3
if ! systemctl is-active --quiet "$SERVICE"; then
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  fail 'Bot service is not active after restart. The Python EXEC permission issue was checked; inspect the application startup error above.'
fi
ok 'sonar-bot.service is active.'
systemctl --no-pager --full status "$SERVICE" || true
printf '\nRepair finished. This verifies process startup only; Telegram polling, DB migrations, web panel and remote SSH still need end-to-end checks.\n'
