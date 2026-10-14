#!/usr/bin/env bash
# Sonar Radar Pro 5.2.1 | GitHub deployment manager
# Supported: Ubuntu/Debian with systemd. Never execute remotely piped scripts as root.
set -Eeuo pipefail
umask 077
export LC_ALL=C.UTF-8

APP_DIR=/opt/radar-sonar
APP_USER=sonar-bot
ENV_FILE=/etc/sonar-bot.env
SERVICE=sonar-bot
WEB_SERVICE=sonar-web
BOT_UNIT=/etc/systemd/system/sonar-bot.service
WEB_UNIT=/etc/systemd/system/sonar-web.service
BACKUP_DIR=/var/backups/sonar-radar
LOG_DIR=/var/log/sonar
INSTALL_LOG=/var/log/sonar-install.log
DB_NAME=sonar_ultra_pro
DB_USER=sonar_user
NGINX_SITE=/etc/nginx/sites-available/sonar-radar.conf
NGINX_LINK=/etc/nginx/sites-enabled/sonar-radar.conf
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# The same canonical repository is used for the public bootstrap and documentation.
GITHUB_REPOSITORY='Amirtn9/radarsonarplus'
GITHUB_BRANCH='main'
GITHUB_URL="https://github.com/${GITHUB_REPOSITORY}.git"
STATE_STAGE=''; STATE_OLD=''; STATE_SWITCH=0; STATE_OLD_PRESENT=0
STATE_BOT_ACTIVE=0; STATE_WEB_ACTIVE=0; STATE_BOT_UNIT=''; STATE_WEB_UNIT=''
STATE_OK=0

# Standalone GitHub bootstrap: makes the published three-line wget installer work.
# If this file was downloaded without its repository, fetch an isolated checkout
# of the same public project and run the full installer from that checkout.
if [[ ! -f "$SOURCE_DIR/bot.py" || ! -f "$SOURCE_DIR/pro_portal.py" || ! -f "$SOURCE_DIR/requirements.txt" ]]; then
  if (( EUID != 0 )); then
    printf 'Administrator access required: run sudo ./install.sh\n' >&2
    exit 1
  fi
  if [[ "${1:-}" == '--help' || "${1:-}" == '-h' ]]; then
    printf 'Sonar Radar Pro: download the full source from %s, then show the management menu.\n' "$GITHUB_URL"
    printf 'Run with sudo: sudo ./install.sh [--install | --update | --check | --doctor | --logs ...]\n'
    exit 0
  fi
  if [[ ! -d /opt ]]; then
    printf 'Missing /opt directory; unsupported host.\n' >&2
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 || { printf 'apt-get is required to install git.\n' >&2; exit 1; }
    printf 'Installing Git for source bootstrap...\n'
    DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git ca-certificates || exit 1
  fi
  _sonar_bootstrap_dir="$(mktemp -d /opt/.sonar-bootstrap.XXXXXXXX)"
  trap 'rm -rf -- "$_sonar_bootstrap_dir"' EXIT
  printf 'Fetching Sonar Radar full source from GitHub...\n'
  if ! GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch "$GITHUB_BRANCH" -- \
      "$GITHUB_URL" "$_sonar_bootstrap_dir/source"; then
    printf 'Cannot clone GitHub repository. Ensure the project exists, is public, and main is published.\n' >&2
    exit 1
  fi
  if [[ ! -f "$_sonar_bootstrap_dir/source/bot.py" || ! -f "$_sonar_bootstrap_dir/source/install.sh" || ! -f "$_sonar_bootstrap_dir/source/pro_portal.py" ]]; then
    printf 'Repository does not contain the complete Sonar Radar source.\n' >&2
    exit 1
  fi
  bash "$_sonar_bootstrap_dir/source/install.sh" "$@"
  exit $?
fi

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BLUE=$'\033[38;5;39m'; C_GREEN=$'\033[38;5;40m'; C_ORANGE=$'\033[38;5;208m'; C_RED=$'\033[38;5;196m'; C_WHITE=$'\033[1;37m'
else C_RESET='';C_DIM='';C_BLUE='';C_GREEN='';C_ORANGE='';C_RED='';C_WHITE=''; fi
say() { printf '  %b●%b %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok() { printf '  %b✓%b %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '  %b!%b %s\n' "$C_ORANGE" "$C_RESET" "$*" >&2; }
die() { printf '  %b✕%b %s\n' "$C_RED" "$C_RESET" "$*" >&2; return 1; }
section() { printf '\n%b  ┌────────────────────────────────────────────────────────┐%b\n' "$C_BLUE" "$C_RESET"; printf '%b  │%b  %-54s%b│%b\n' "$C_BLUE" "$C_WHITE" "$1" "$C_BLUE" "$C_RESET"; printf '%b  └────────────────────────────────────────────────────────┘%b\n' "$C_BLUE" "$C_RESET"; }
require_root() { (( EUID == 0 )) || die 'Root access required. Run: sudo bash install.sh'; }
require_service_host() {
  [[ -f /etc/os-release ]] || die 'Missing /etc/os-release'
  # shellcheck source=/dev/null
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in *ubuntu*|*debian*) ;; *) die 'Only Debian and Ubuntu are supported.';; esac
  command -v systemctl >/dev/null && command -v apt-get >/dev/null && command -v runuser >/dev/null || die 'systemd, apt-get and runuser are required.'
}
ensure_directories() { install -d -m 0700 "$BACKUP_DIR"; install -d -m 0750 "$LOG_DIR"; touch "$INSTALL_LOG"; chmod 0600 "$INSTALL_LOG"; }
valid_port() { [[ "$1" =~ ^[0-9]{2,5}$ ]] && (( 10#$1 >= 1024 && 10#$1 <= 65535 )); }
valid_domain() {
  [[ ${#1} -le 253 && "$1" == *.* && "$1" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && "$1" != *..* ]] || return 1
  local part
  IFS=. read -r -a parts <<< "$1"
  for part in "${parts[@]}"; do (( ${#part} <= 63 && ${#part} > 0 )) || return 1; [[ "$part" != -* && "$part" != *- ]] || return 1; done
}
validate_token() { [[ "$1" =~ ^[0-9]{6,15}:[A-Za-z0-9_-]{20,}$ ]]; }
require_install() { [[ -f "$APP_DIR/bot.py" && -x "$APP_DIR/venv/bin/python" ]] || die 'The bot is not installed. Use New installation first.'; }
config_file() { printf '%s/sonar_config.json' "$APP_DIR"; }
read_cfg() {
  local key="$1" default="${2:-}"
  if [[ ! -f "$(config_file)" ]]; then printf '%s' "$default"; return; fi
  python3 - "$(config_file)" "$key" "$default" <<'PY'
import sys,json
try:
 with open(sys.argv[1],encoding='utf8') as f: x=json.load(f)
 print(x.get(sys.argv[2],sys.argv[3]),end='')
except (OSError,ValueError):print(sys.argv[3],end='')
PY
}
read_env() {
  local key="$1" value=''
  [[ -f "$ENV_FILE" ]] || { printf ''; return; }
  value="$(grep -m1 "^${key}=" "$ENV_FILE" || true)"
  printf '%s' "${value#*=}"
}
# No shell eval and no source of secrets; strict allow-list of editable keys.
set_env() {
  local key="$1" val="$2" out
  case "$key" in
    SONAR_DB_PASSWORD|SONAR_WEBAPP_URL|SONAR_WS_POOL_MAX|SONAR_GLOBAL_TEST_LIMIT|SONAR_USER_TEST_LIMIT|SONAR_DB_POOL_MAX|SONAR_EXECUTOR_WORKERS|SONAR_WEB_PORT|SONAR_AGENT_BIND|SONAR_LOG_LEVEL|SONAR_MINIAPP_SESSION_MAX_AGE) ;;
    *) die 'Environment key not permitted';;
  esac
  [[ "$val" != *$'\n'* && "$val" != *$'\r'* ]] || die 'Newline in setting not allowed'
  if [[ "$key" == SONAR_LOG_LEVEL ]];then
    [[ "$val" =~ ^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$ ]] || die 'Invalid log level'
  fi
  if [[ "$key" == SONAR_MINIAPP_SESSION_MAX_AGE ]];then
    [[ "$val" =~ ^[0-9]{2,6}$ ]] && (( 10#$val <= 86400 )) || die 'Session age must be <= 86400s'
  fi
  mkdir -p /etc
  [[ ! -L "$ENV_FILE" ]] || die 'Refusing symlinked environment file'
  out="$(mktemp /etc/.sonar-env.XXXXXXXX)"
  if [[ -f "$ENV_FILE" ]]; then grep -v "^${key}=" "$ENV_FILE" > "$out" || true; fi
  printf '%s=%s\n' "$key" "$val" >> "$out"
  chmod 0600 "$out"; chown root:root "$out"; mv -f -- "$out" "$ENV_FILE"
}
# Config fields are saved atomically; never printed by CLI.
set_cfg() {
  local key="$1" val="$2" path
  require_install
  case "$key" in bot_token|admin_id|agent_port|ws_pool_max|ws_ping_interval|ws_ping_timeout) ;; *) die 'Config key not permitted';; esac
  path="$(config_file)"
  [[ ! -L "$path" ]] || die 'Refusing symlinked configuration file'
  SONAR_CONFIG_INPUT="$val" "$APP_DIR/venv/bin/python" - "$path" "$key" <<'PY'
import json,os,sys,tempfile
p,k=sys.argv[1:]
with open(p,encoding='utf8') as f:d=json.load(f)
v=os.environ['SONAR_CONFIG_INPUT']
if k in ('admin_id','agent_port','ws_pool_max','ws_ping_interval','ws_ping_timeout'):v=int(v)
d[k]=v
fd,path=tempfile.mkstemp(prefix='.sonar-config-',dir=os.path.dirname(p))
try:
 with os.fdopen(fd,'w',encoding='utf8') as f:
  json.dump(d,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 os.chmod(path,0o600);os.chown(path, __import__('pwd').getpwnam('sonar-bot').pw_uid,__import__('grp').getgrnam('sonar-bot').gr_gid)
 os.replace(path,p)
finally:
 if os.path.exists(path):os.unlink(path)
PY
}
preflight() {
  section 'PREFLIGHT  ·  Source and environment'
  [[ -f "$SOURCE_DIR/bot.py" && -f "$SOURCE_DIR/settings.py" && -f "$SOURCE_DIR/requirements.txt" && -f "$SOURCE_DIR/pro_portal.py" && -f "$SOURCE_DIR/install_web_panel.sh" ]] || die "Run from a complete repository checkout ($SOURCE_DIR)."
  bash -n "$SOURCE_DIR/install.sh" "$SOURCE_DIR/install_web_panel.sh" || die 'Bash syntax check failed'
  command -v python3 >/dev/null || die 'Python3 is required for preflight. Install python3 then retry.'
  python3 - "$SOURCE_DIR" <<'PY'
import pathlib,json,sys,ast
root=pathlib.Path(sys.argv[1]);count=0
for f in root.rglob('*.py'):
 if any(p in ('.git','venv','.venv') for p in f.parts):continue
 ast.parse(f.read_text('utf8'),filename=str(f));count+=1
p=root/'sonar_config.json'
if p.exists():
 d=json.loads(p.read_text('utf8'))
 if d.get('bot_token') or str(d.get('admin_id','0')) not in ('0',''):
  raise SystemExit('Refusing source with embedded bot credentials. Use an empty sonar_config.json in Git.')
print('Validated',count,'Python sources and configuration')
PY
  if [[ -f /etc/os-release ]]; then require_service_host; else warn 'Not a systemd OS; source validation only'; fi
  ok 'Source preflight completed. It does not verify live network services.'
}
ensure_python_version() {
  python3 - <<'PY'
import sys
if sys.version_info < (3,10):raise SystemExit('Python >= 3.10 required')
PY
}
install_deps() {
  say 'Installing operating-system dependencies (logs stored locally)'
  DEBIAN_FRONTEND=noninteractive apt-get update >> "$INSTALL_LOG" 2>&1 || die "apt update failed; $INSTALL_LOG"
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-venv python3-dev build-essential libssl-dev libffi-dev git curl openssh-client ca-certificates tar postgresql postgresql-contrib libpq-dev libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core >> "$INSTALL_LOG" 2>&1 || die "Dependency installation failed; $INSTALL_LOG"
  ensure_python_version
}
ask_new_credentials() {
  local token admin
  if [[ -n "${SONAR_INSTALL_TOKEN:-}" ]]; then token="$SONAR_INSTALL_TOKEN"; else read -r -s -p '  Telegram bot token (hidden): ' token; printf '\n'; fi
  validate_token "$token" || die 'Invalid bot token format.'
  if [[ -n "${SONAR_INSTALL_ADMIN:-}" ]]; then admin="$SONAR_INSTALL_ADMIN"; else read -r -p '  Telegram numeric admin ID: ' admin; fi
  [[ "$admin" =~ ^[1-9][0-9]{0,18}$ ]] || die 'Invalid admin ID.'
  SONAR_INITIAL_TOKEN="$token" SONAR_INITIAL_ADMIN="$admin" python3 - "$STATE_STAGE/sonar_config.json" <<'PY'
import sys,os,json
p=sys.argv[1]
try:
 with open(p,encoding='utf8') as f:d=json.load(f)
except FileNotFoundError:d={}
d.update(bot_token=os.environ['SONAR_INITIAL_TOKEN'],admin_id=os.environ['SONAR_INITIAL_ADMIN'])
with open(p,'w',encoding='utf8') as f:json.dump(d,f,ensure_ascii=False,indent=2);f.write('\n')
PY
  unset token
}
prepare_release() {
  STATE_STAGE="$(mktemp -d /opt/.sonar-stage.XXXXXXXX)"
  tar -C "$SOURCE_DIR" --exclude='./venv' --exclude='./.venv' --exclude='./.git' --exclude='./__pycache__' --exclude='./.pytest_cache' --exclude='./secret.key' --exclude='./.env' --exclude='./tests' --exclude='./logs' -cf - . | tar -C "$STATE_STAGE" -xf -
  if command -v git >/dev/null 2>&1 && git -C "$SOURCE_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    git -C "$SOURCE_DIR" rev-parse HEAD > "$STATE_STAGE/SOURCE_REVISION"
  else
    printf 'archive-unknown\n' > "$STATE_STAGE/SOURCE_REVISION"
  fi
  if [[ ! -f "$STATE_STAGE/sonar_config.json" && -f "$STATE_STAGE/sonar_config.example.json" ]]; then
    cp "$STATE_STAGE/sonar_config.example.json" "$STATE_STAGE/sonar_config.json"
  fi
  if [[ -f "$APP_DIR/sonar_config.json" ]]; then
    [[ ! -L "$APP_DIR/sonar_config.json" ]] || die 'Unsafe existing config symlink'
    cp -p "$APP_DIR/sonar_config.json" "$STATE_STAGE/sonar_config.json"
  else
    ask_new_credentials
  fi
  if [[ -f "$APP_DIR/secret.key" ]]; then
    [[ ! -L "$APP_DIR/secret.key" ]] || die 'Unsafe existing encryption key symlink'
    cp -p "$APP_DIR/secret.key" "$STATE_STAGE/secret.key"
  fi
  if [[ -f "$APP_DIR/.ssh/known_hosts" && ! -L "$APP_DIR/.ssh/known_hosts" ]]; then
    install -d -m 0750 "$STATE_STAGE/.ssh"
    cp -p "$APP_DIR/.ssh/known_hosts" "$STATE_STAGE/.ssh/known_hosts"
  fi
  [[ -f "$STATE_STAGE/sonar_config.json" ]] || die 'Configuration is missing.'
  chmod 0600 "$STATE_STAGE/sonar_config.json"
  python3 - "$STATE_STAGE/sonar_config.json" <<'PY'
import json,sys,re
with open(sys.argv[1],encoding='utf8') as f:d=json.load(f)
t=d.get('bot_token','');u=str(d.get('admin_id',''))
if not re.fullmatch(r'[0-9]{6,15}:[A-Za-z0-9_-]{20,}',str(t)) or not re.fullmatch(r'[1-9][0-9]{0,18}',u):
 raise SystemExit('Installed Telegram credentials invalid; use Settings > Telegram credentials.')
PY
}
create_venv() {
  say 'Building an isolated Python environment'
  python3 -m venv "$STATE_STAGE/venv"
  "$STATE_STAGE/venv/bin/python" -m pip install -U pip setuptools wheel >> "$INSTALL_LOG" 2>&1 || die 'pip bootstrap failed.'
  "$STATE_STAGE/venv/bin/python" -m pip install -r "$STATE_STAGE/requirements.txt" >> "$INSTALL_LOG" 2>&1 || die 'Python dependency installation failed.'
  "$STATE_STAGE/venv/bin/python" -m compileall -q "$STATE_STAGE" || die 'Python source compilation failed.'
  # Absolute interpreter path in Python entry points must be replaced before promotion.
  python3 - "$STATE_STAGE" "$APP_DIR" <<'PY'
from pathlib import Path
import sys
stage,live=map(Path,sys.argv[1:]);bin=stage/'venv/bin'
for p in bin.iterdir():
 if p.is_symlink() or not p.is_file():continue
 data=p.read_bytes()
 if data.startswith(b'#!') and str(stage).encode() in data.split(b'\n',1)[0]:data=data.replace(str(stage).encode(),str(live).encode(),1)
 elif p.name.startswith('activate'):data=data.replace(str(stage).encode(),str(live).encode())
 else:continue
 p.write_bytes(data)
PY
  if [[ ! -f "$STATE_STAGE/secret.key" ]]; then
    "$STATE_STAGE/venv/bin/python" - "$STATE_STAGE/secret.key" <<'PY'
from cryptography.fernet import Fernet
import pathlib,sys
pathlib.Path(sys.argv[1]).write_bytes(Fernet.generate_key()+b'\n')
PY
  fi
  [[ -s "$STATE_STAGE/secret.key" ]] || die 'Encryption key missing.'
  "$STATE_STAGE/venv/bin/python" - "$STATE_STAGE/secret.key" <<'PY'
from cryptography.fernet import Fernet
import pathlib,sys
Fernet(pathlib.Path(sys.argv[1]).read_bytes().strip())
PY
}
ensure_db() {
  say 'Preparing local PostgreSQL with a dedicated account'
  systemctl enable --now postgresql >> "$INSTALL_LOG" 2>&1 || die 'PostgreSQL service failed.'
  local dbpass
  dbpass="$(read_env SONAR_DB_PASSWORD)"
  if [[ -z "$dbpass" ]]; then
    dbpass="$(python3 -c 'import secrets;print(secrets.token_urlsafe(36))')"
    set_env SONAR_DB_PASSWORD "$dbpass"
  fi
  [[ "$dbpass" =~ ^[A-Za-z0-9_-]{20,}$ ]] || die 'Database password format not supported; use URL-safe password.'
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -qx 1; then
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "CREATE ROLE ${DB_USER} LOGIN" >> "$INSTALL_LOG" 2>&1 || die 'Database role creation failed.'
  fi
  printf 'ALTER ROLE %s WITH LOGIN PASSWORD '\''%s'\'';\n' "$DB_USER" "$dbpass" | runuser -u postgres -- psql -v ON_ERROR_STOP=1 >/dev/null || die 'Database role setup failed.'
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -qx 1; then
    runuser -u postgres -- createdb -O "$DB_USER" "$DB_NAME" || die 'Database creation failed.'
  fi
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER}" >/dev/null || die 'Database permissions failed.'
  PGPASSWORD="$dbpass" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -tAc 'SELECT 1' | grep -qx 1 || die 'Database authentication failed.'
}
make_backup() {
  ensure_directories
  require_install
  local stamp file
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$";file="$BACKUP_DIR/sonar-${stamp}"
  [[ -f "$ENV_FILE" ]] && cp -p "$ENV_FILE" "${file}.env"
  cp -p "$APP_DIR/sonar_config.json" "${file}.config.json"
  [[ -f "$APP_DIR/secret.key" ]] && cp -p "$APP_DIR/secret.key" "${file}.secret.key"
  if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -qx 1; then
    runuser -u postgres -- pg_dump -Fc -d "$DB_NAME" > "${file}.dump" || { rm -f "${file}.dump"; die 'pg_dump failed, refusing to proceed.'; }
    [[ -s "${file}.dump" ]] || die 'Database backup empty!'
    pg_restore --list "${file}.dump" >/dev/null || die 'Database dump verification failed.'
  fi
  chmod 0600 "${file}"*; printf '%s\n' "$file"
}
create_unit() {
  cat > "$BOT_UNIT" <<EOF
[Unit]
Description=Sonar Radar Pro Telegram Bot
Wants=network-online.target
After=network-online.target postgresql.service
[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/bot.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sonar-bot
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=120
StartLimitBurst=8
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$LOG_DIR $APP_DIR/sonar_config.json $APP_DIR/secret.key
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$BOT_UNIT"
}
rollback() {
  local rc=$?
  trap - EXIT
  if (( rc != 0 && STATE_SWITCH == 1 )); then
    warn 'Deployment failed: restoring application code/services. Database migrations are NOT automatically reverted.'
    systemctl stop "$SERVICE" "$WEB_SERVICE" >/dev/null 2>&1 || true
    if [[ -n "$STATE_OLD" && -d "$STATE_OLD" ]]; then
      rm -rf -- "$APP_DIR"; mv -- "$STATE_OLD" "$APP_DIR" || warn 'Manual recovery is required for the previous release!'
    else rm -rf -- "$APP_DIR"; fi
    if [[ -n "$STATE_BOT_UNIT" ]]; then cp -p "$STATE_BOT_UNIT" "$BOT_UNIT"; else systemctl disable "$SERVICE" >/dev/null 2>&1 || true;rm -f "$BOT_UNIT"; fi
    if [[ -n "$STATE_WEB_UNIT" ]]; then cp -p "$STATE_WEB_UNIT" "$WEB_UNIT"; else systemctl disable "$WEB_SERVICE" >/dev/null 2>&1 || true;rm -f "$WEB_UNIT"; fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    if (( STATE_BOT_ACTIVE == 1 )); then systemctl start "$SERVICE" || true; fi
    if (( STATE_WEB_ACTIVE == 1 )); then systemctl start "$WEB_SERVICE" || true; fi
  fi
  [[ -z "$STATE_STAGE" || ! -d "$STATE_STAGE" ]] || rm -rf -- "$STATE_STAGE"
  exit "$rc"
}
trap rollback EXIT
install_app() {
  local mode="$1"
  require_root; require_service_host; ensure_directories; preflight
  [[ ! -L "$APP_DIR" ]] || die 'Refusing symlinked installation root.'
  if [[ "$mode" == new ]]; then [[ ! -e "$APP_DIR" ]] || die 'App already installed. Select Upgrade.'
  else [[ -d "$APP_DIR" ]] || die 'No existing installation to upgrade.'; [[ "$SOURCE_DIR" != "$APP_DIR" ]] || die 'Upgrade from a separate Git checkout, not the live directory.'; fi
  if [[ "$mode" == upgrade ]]; then
    say 'Creating safety backup BEFORE modifying packages or code'
    make_backup
  fi
  install_deps
  prepare_release
  create_venv
  ensure_db
  id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  chown -R root:root "$STATE_STAGE"
  chmod 0755 "$STATE_STAGE"
  chown "$APP_USER:$APP_USER" "$STATE_STAGE/sonar_config.json" "$STATE_STAGE/secret.key"
  chmod 0600 "$STATE_STAGE/sonar_config.json" "$STATE_STAGE/secret.key"
  if [[ -f "$STATE_STAGE/.ssh/known_hosts" ]]; then
    chown root:"$APP_USER" "$STATE_STAGE/.ssh" "$STATE_STAGE/.ssh/known_hosts"
    chmod 0750 "$STATE_STAGE/.ssh";chmod 0640 "$STATE_STAGE/.ssh/known_hosts"
  fi
  chown "$APP_USER:$APP_USER" "$LOG_DIR"
  if systemctl is-active --quiet "$SERVICE"; then STATE_BOT_ACTIVE=1; fi
  if systemctl is-active --quiet "$WEB_SERVICE"; then STATE_WEB_ACTIVE=1; fi
  if [[ -f "$BOT_UNIT" ]]; then STATE_BOT_UNIT="$(mktemp "$BACKUP_DIR/unit-bot.XXXXXX")";cp -p "$BOT_UNIT" "$STATE_BOT_UNIT"; fi
  if [[ -f "$WEB_UNIT" ]]; then STATE_WEB_UNIT="$(mktemp "$BACKUP_DIR/unit-web.XXXXXX")";cp -p "$WEB_UNIT" "$STATE_WEB_UNIT"; fi
  systemctl stop "$SERVICE" "$WEB_SERVICE" >/dev/null 2>&1 || true
  STATE_SWITCH=1
  if [[ -d "$APP_DIR" ]]; then STATE_OLD="$BACKUP_DIR/previous-code-$(date -u +%Y%m%dT%H%M%SZ)-$$";mv -- "$APP_DIR" "$STATE_OLD";fi
  mv -- "$STATE_STAGE" "$APP_DIR";STATE_STAGE=''
  create_unit
  systemctl daemon-reload
  systemctl enable --now "$SERVICE" >> "$INSTALL_LOG" 2>&1 || die 'Bot service did not start.'
  sleep 5
  systemctl is-active --quiet "$SERVICE" || die 'Bot process exited after startup.'
  # A full installation includes the Mini App API, not just the Telegram Bot.
  # Keep this inside the rollback transaction: failure restores the old code/units.
  install_web
  STATE_SWITCH=0;STATE_OK=1
  ok "Full stack installed: Bot, PostgreSQL and private Mini App API ($APP_DIR)"
  say 'Run --doctor and test Telegram /start before migrating production traffic.'
  [[ -z "$STATE_OLD" ]] || say "Previous source archived at: $STATE_OLD"
}
configure_web_port() {
  local port="$1";valid_port "$port" || die 'Port must be between 1024 and 65535.'
  [[ "$port" != 80 && "$port" != 443 ]] || die 'This port is reserved for nginx.'
  set_env SONAR_WEB_PORT "$port"
  # Regenerate the web unit, keeping its loopback-only binding.
  if [[ -f "$WEB_UNIT" ]]; then create_web_unit "$port";systemctl daemon-reload;systemctl restart "$WEB_SERVICE";fi
  if [[ -f "$NGINX_SITE" ]] && grep -q 'proxy_pass http://127.0.0.1:' "$NGINX_SITE";then
    local copy;copy="$(mktemp "$BACKUP_DIR/webport.XXXXXXXX")";cp -p "$NGINX_SITE" "$copy"
    sed -i -E "s@(proxy_pass http://127\.0\.0\.1:)[0-9]+;@\1${port};@g" "$NGINX_SITE"
    if ! nginx -t;then cp -p "$copy" "$NGINX_SITE";die 'Invalid nginx config; previous nginx config restored.';fi
    systemctl reload nginx || { cp -p "$copy" "$NGINX_SITE";die 'nginx reload failed; config restored'; }
  fi
  ok "Web API port set to $port (127.0.0.1 only)"
}
create_web_unit() {
  local port="$1";valid_port "$port" || die 'Invalid web API port.'
  cat > "$WEB_UNIT" <<EOF
[Unit]
Description=Sonar Radar Pro Secure Mini App API
Wants=network-online.target
After=network-online.target postgresql.service
[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python -m uvicorn pro_portal:app --host 127.0.0.1 --port $port --workers 2 --access-log
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sonar-web
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$LOG_DIR
UMask=0077
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$WEB_UNIT"
}
install_web() {
  require_root;require_install
  local port; port="$(read_env SONAR_WEB_PORT)";port="${port:-8090}"
  valid_port "$port" || die 'Invalid configured web API port.'
  say "Installing Mini App API (loopback port $port)"
  "$APP_DIR/venv/bin/python" -m pip install -r "$APP_DIR/requirements-web.txt" >> "$INSTALL_LOG" 2>&1 || die 'Web dependencies failed.'
  create_web_unit "$port";systemctl daemon-reload
  systemctl enable "$WEB_SERVICE" >> "$INSTALL_LOG" 2>&1 || die 'Could not enable Web service.'
  # enable --now does not restart an already-active old process on upgrades.
  systemctl restart "$WEB_SERVICE" >> "$INSTALL_LOG" 2>&1 || die 'Web service failed.'
  local attempts=0
  until curl --fail --silent --max-time 3 "http://127.0.0.1:${port}/healthz" >/dev/null; do
    ((attempts+=1))
    if ((attempts >= 12)); then
      journalctl -u "$WEB_SERVICE" -n 30 --no-pager >&2 || true
      die 'Web API failed its HTTP health check.'
    fi
    sleep 1
  done
  ok "Mini App API active on http://127.0.0.1:$port"
  say 'To publish: configure domain + HTTPS. Never expose this port publicly.'
}
set_domain() {
  local domain="$1";valid_domain "$domain" || die 'Invalid domain (hostname only, no scheme/path).'
  require_root;require_install
  [[ "$domain" != 'example.com' && "$domain" != *.example.com ]] || die 'Use a domain you control.'
  command -v nginx >/dev/null || {
    DEBIAN_FRONTEND=noninteractive apt-get update >> "$INSTALL_LOG" 2>&1 || die 'apt update failed'
    DEBIAN_FRONTEND=noninteractive apt-get install -y nginx >> "$INSTALL_LOG" 2>&1 || die 'Could not install nginx'
  }
  local port;port="$(read_env SONAR_WEB_PORT)";port="${port:-8090}"
  valid_port "$port" || die 'Invalid API port.'
  local old=''
  if [[ -f "$NGINX_SITE" ]]; then
    if grep -qE '^[[:space:]]*listen[[:space:]]+443' "$NGINX_SITE";then
      die 'Existing HTTPS nginx config detected. Change domain with deliberate manual nginx/ACME migration to avoid dropping TLS.'
    fi
    old="$(mktemp "$BACKUP_DIR/nginx.XXXXXXXX")";cp -p "$NGINX_SITE" "$old"
  fi
  cat > "$NGINX_SITE" <<EOF
# Managed by Sonar installer. TLS issued via certbot --nginx.
server {
    listen 80;
    listen [::]:80;
    server_name $domain;
    client_max_body_size 32k;
    add_header X-Content-Type-Options nosniff always;
    location / {
        proxy_pass http://127.0.0.1:$port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 35s;
    }
}
EOF
  ln -sfn "$NGINX_SITE" "$NGINX_LINK"
  if ! nginx -t; then
    [[ -z "$old" ]] && rm -f "$NGINX_SITE" || cp -p "$old" "$NGINX_SITE"
    [[ -n "$old" ]] || rm -f "$NGINX_LINK"
    die 'nginx syntax validation failed. Previous site restored.'
  fi
  systemctl enable --now nginx >/dev/null;systemctl reload nginx
  ok "HTTP virtual host configured for $domain. Next: Request TLS certificate."
}
issue_tls() {
  require_root;require_install
  local domain="${1:-}" email="${2:-}" addr
  valid_domain "$domain" || die 'Provide a valid domain.'
  [[ -f "$NGINX_SITE" ]] || die 'Configure web domain first.'
  systemctl is-active --quiet "$WEB_SERVICE" || die 'Install and start the Mini App first (--panel).'
  grep -q "server_name ${domain};" "$NGINX_SITE" || die 'Domain differs from nginx configuration.'
  [[ "${email:-}" != *$'\n'* ]] || die 'Invalid email.'
  if [[ -n "$email" && ! "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then die 'Invalid email format.';fi
  # DNS is a strong preflight signal; a proxy/CDN may resolve differently, hence warn only.
  addr="$(getent ahostsv4 "$domain" 2>/dev/null | head -1 | awk '{print $1}' || true)"
  [[ -n "$addr" ]] || warn 'Domain does not resolve to IPv4 locally; ACME validation may fail.'
  DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx >> "$INSTALL_LOG" 2>&1 || die 'Failed to install certbot.'
  local -a args=(--nginx -d "$domain" --non-interactive --agree-tos --redirect --keep-until-expiring)
  if [[ -n "$email" ]]; then args+=(--email "$email"); else args+=(--register-unsafely-without-email);fi
  say 'Requesting public TLS certificate (domain DNS + inbound TCP/80 must work)'
  certbot "${args[@]}" || die 'TLS issuance failed. Fix DNS, ports 80/443 and retry. HTTP config retained.'
  [[ -s "/etc/letsencrypt/live/$domain/fullchain.pem" ]] || die 'TLS certificate missing.'
  nginx -t && systemctl reload nginx || die 'nginx reload failed.'
  set_env SONAR_WEBAPP_URL "https://$domain/"
  systemctl restart "$SERVICE" || die 'Bot restart after web URL change failed.'
  ok "HTTPS provisioned and Mini App URL enabled: https://$domain/"
}
set_telegram_credentials() {
  require_root;require_install
  local token admin
  read -r -s -p '  New Telegram bot token (hidden; Enter to keep): ' token;printf '\n'
  read -r -p '  Admin numeric Telegram ID (Enter to keep): ' admin
  if [[ -n "$token" ]]; then validate_token "$token" || die 'Invalid token format';set_cfg bot_token "$token";fi
  if [[ -n "$admin" ]]; then [[ "$admin" =~ ^[1-9][0-9]{0,18}$ ]] || die 'Invalid admin ID';set_cfg admin_id "$admin";fi
  systemctl restart "$SERVICE";ok 'Telegram credentials saved and bot restarted.'
}
change_setting() {
  require_root;require_install
  local key="$1" value="$2"
  case "$key" in
    agent_port|ws_pool_max|ws_ping_interval|ws_ping_timeout)
      [[ "$value" =~ ^[0-9]+$ ]] || die 'Must be an integer.'
      if [[ "$key" == agent_port ]]; then valid_port "$value" || die 'Invalid agent port.';fi
      if [[ "$key" != agent_port ]]; then (( value >= 1 && value <= 600 )) || die 'Value out of range (1–600).';fi
      set_cfg "$key" "$value";;
    SONAR_WS_POOL_MAX|SONAR_GLOBAL_TEST_LIMIT|SONAR_USER_TEST_LIMIT|SONAR_DB_POOL_MAX|SONAR_EXECUTOR_WORKERS)
      [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= 128 )) || die 'Value must be between 1 and 128.'
      set_env "$key" "$value";;
    SONAR_LOG_LEVEL)
      [[ "$value" =~ ^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$ ]] || die 'Invalid log level.'
      set_env "$key" "$value";;
    SONAR_MINIAPP_SESSION_MAX_AGE)
      [[ "$value" =~ ^[0-9]{2,6}$ ]] && (( 10#$value >= 300 && 10#$value <= 86400 )) || die 'Session age must be 300..86400s.'
      set_env "$key" "$value";;
    SONAR_WEB_PORT) configure_web_port "$value";;
    *) die 'Unsupported setting';;
  esac
  if [[ "$key" != SONAR_WEB_PORT ]]; then
    systemctl restart "$SERVICE" || die 'Bot restart failed.'
    if [[ "$key" == SONAR_LOG_LEVEL || "$key" == SONAR_MINIAPP_SESSION_MAX_AGE ]]; then
      if systemctl is-active --quiet "$WEB_SERVICE"; then systemctl restart "$WEB_SERVICE" || die 'Web restart failed.'; fi
    fi
  fi
  ok "$key updated."
}
trust_ssh_host() {
  require_root;require_install
  local host="$1" port="${2:-22}" scan fingerprint expected storedir temp
  [[ "$host" =~ ^[A-Za-z0-9.:_-]{1,253}$ && "$host" != -* ]] || die 'Invalid SSH host.'
  valid_port "$port" || { [[ "$port" == 22 ]] || die 'Invalid SSH port.'; }
  command -v ssh-keyscan >/dev/null && command -v ssh-keygen >/dev/null || die 'Install openssh-client first.'
  say "Fetching SSH host key from $host:$port (not yet trusted)"
  temp="$(mktemp "$BACKUP_DIR/ssh-hostkey.XXXXXXXX")"
  ssh-keyscan -T 8 -p "$port" -t ed25519 "$host" 2>/dev/null > "$temp" || true
  [[ -s "$temp" ]] || die 'No ED25519 host key received; verify SSH access.'
  fingerprint="$(ssh-keygen -lf "$temp" -E sha256 | awk 'NR==1{print $2}')"
  say "Candidate SHA256 fingerprint: $fingerprint"
  warn 'Verify this SHA256 fingerprint independently with the server provider or console; an SSH keyscan alone does NOT authenticate the host.'
  read -r -p '  Paste independently verified SHA256 fingerprint to trust: ' expected
  [[ "$expected" == "$fingerprint" ]] || die 'Fingerprint mismatch. Not trusted.'
  storedir="$APP_DIR/.ssh"
  install -d -o root -g "$APP_USER" -m 0750 "$storedir"
  touch "$storedir/known_hosts"
  chmod 0640 "$storedir/known_hosts";chown root:"$APP_USER" "$storedir/known_hosts"
  if ! grep -Fqx -f "$temp" "$storedir/known_hosts";then cat "$temp" >> "$storedir/known_hosts";fi
  rm -f "$temp"
  ok 'Verified SSH host key added. Remote agent installs can now validate this host.'
}
service_command() {
  local service="$1" action="$2"
  [[ "$service" == "$SERVICE" || "$service" == "$WEB_SERVICE" ]] || die 'Invalid service'
  case "$action" in start|stop|restart|status) ;; *) die 'Invalid service action';;esac
  require_root
  systemctl "$action" "$service"
}
status() {
  section 'SERVICE HEALTH'
  if [[ -f "$APP_DIR/VERSION" ]];then say "Release: $(cat "$APP_DIR/VERSION")";fi
  if [[ -f "$APP_DIR/SOURCE_REVISION" ]];then say "Source: $(cat "$APP_DIR/SOURCE_REVISION")";fi
  for s in "$SERVICE" "$WEB_SERVICE" postgresql nginx; do
    if systemctl is-active --quiet "$s" 2>/dev/null;then ok "$s: active"; else warn "$s: not active";fi
  done
  if [[ -f "$APP_DIR/sonar_config.json" ]]; then say "Admin ID: $(read_cfg admin_id unset) | Agent port: $(read_cfg agent_port 8080)";fi
  if [[ -f "$ENV_FILE" ]]; then say "Web API port: $(read_env SONAR_WEB_PORT) | Mini App URL: $(read_env SONAR_WEBAPP_URL)";fi
}
doctor() {
  section 'INSTALLATION DIAGNOSTICS'
  local problems=0 port
  [[ -f "$APP_DIR/bot.py" ]] && ok 'Bot sources found' || { warn 'Bot code missing';((problems+=1)); }
  [[ -f "$BOT_UNIT" ]] && ok 'Bot unit exists' || { warn 'Bot unit missing';((problems+=1)); }
  if [[ -f "$APP_DIR/sonar_config.json" ]]; then
    if [[ "$(read_cfg admin_id 0)" != 0 ]]; then ok 'Admin configured';else warn 'No admin configured';((problems+=1));fi
    if [[ "$(stat -c '%a' "$APP_DIR/sonar_config.json")" == 600 ]];then ok 'Config permissions 0600';else warn 'Config not 0600';((problems+=1));fi
  fi
  if systemctl is-active --quiet "$SERVICE";then ok 'Bot service active';else warn 'Bot not active';((problems+=1));fi
  if [[ -x "$APP_DIR/venv/bin/python" ]]; then
    "$APP_DIR/venv/bin/python" -m compileall -q "$APP_DIR" && ok 'Python compile passes' || { warn 'Python compile failed';((problems+=1)); }
  fi
  port="$(read_env SONAR_WEB_PORT)";port="${port:-8090}"
  if [[ ! -f "$WEB_UNIT" ]];then warn 'Mini App unit missing';((problems+=1));fi
  if systemctl is-active --quiet "$WEB_SERVICE";then
    if curl -fsS --max-time 6 "http://127.0.0.1:$port/healthz" >/dev/null;then ok 'Web API health OK';else warn 'Web API health FAIL';((problems+=1));fi
  else warn 'Mini App not running';((problems+=1));fi
  if systemctl is-active --quiet postgresql;then
    if runuser -u postgres -- psql -tAc 'SELECT 1' | grep -qx 1;then ok 'PostgreSQL responds';else warn 'PostgreSQL unhealthy';((problems+=1));fi
  fi
  if ((problems));then warn "$problems diagnostic warnings. Check journalctl and README.";return 1;fi
  ok 'Checks passed. Telegram/remote agent end-to-end verification still required.'
}
restore_dump() {
  require_root;require_install
  local dump="$1" confirm now
  [[ -f "$dump" && ! -L "$dump" && "$dump" == *.dump ]] || die 'Provide an existing trusted .dump file.'
  pg_restore --list "$dump" >/dev/null || die 'Not a readable PostgreSQL custom dump.'
  warn 'This REPLACES the contents of the CURRENT database. A safety backup will be created.'
  read -r -p '  Type RESTORE DATABASE to proceed: ' confirm
  [[ "$confirm" == 'RESTORE DATABASE' ]] || { warn 'Cancelled';return 0; }
  now="$(make_backup)";say "Safety backup: $now"
  systemctl stop "$WEB_SERVICE" "$SERVICE" >/dev/null 2>&1 || true
  # Restoring as the owner avoids superuser objects in source backups.
  if ! runuser -u postgres -- pg_restore --exit-on-error --clean --if-exists --no-owner --no-acl --role="$DB_USER" -d "$DB_NAME" < "$dump";then
    warn 'Restore failed: database may be partially restored. Reapply safety backup before starting.'
    die 'Database restore failed.'
  fi
  systemctl start "$SERVICE"
  [[ -f "$WEB_UNIT" ]] && systemctl start "$WEB_SERVICE" || true
  ok 'Database restored. Verify application data and agent connections.'
}
uninstall_app() {
  require_root;require_install
  local answer dest
  warn 'Archives application/config, removes systemd services and nginx site; leaves database, OS packages and backups intact.'
  read -r -p '  Type UNINSTALL SONAR to confirm: ' answer
  [[ "$answer" == 'UNINSTALL SONAR' ]] || { warn 'Cancelled';return 0; }
  ensure_directories
  dest="$BACKUP_DIR/uninstalled-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  systemctl disable --now "$SERVICE" "$WEB_SERVICE" >/dev/null 2>&1 || true
  mv -- "$APP_DIR" "$dest"
  [[ -f "$ENV_FILE" ]] && cp -p "$ENV_FILE" "${dest}.env"
  rm -f -- "$BOT_UNIT" "$WEB_UNIT"
  if [[ -L "$NGINX_LINK" ]] && [[ "$(readlink -f "$NGINX_LINK")" == "$NGINX_SITE" ]];then rm -f "$NGINX_LINK";fi
  [[ -f "$NGINX_SITE" ]] && mv "$NGINX_SITE" "${dest}.nginx.conf"
  systemctl daemon-reload
  if command -v nginx >/dev/null;then nginx -t && systemctl reload nginx || true;fi
  ok "Uninstalled application. Archive: $dest"
  warn 'Database and encryption backups were NOT erased. Remove them manually only when absolutely certain.'
}
settings_menu() {
  while true; do
    section 'SETTINGS  ·  Safe configuration'
    printf '  13 Log verbosity\n  1  Telegram token / admin ID\n  2  Agent port\n  3  Web API port\n  4  Web domain / nginx\n  5  Obtain/renew HTTPS certificate\n  6  Global test concurrency\n  7  Per-user test concurrency\n  8  Database pool size\n  9  WS connection pool\n  10 Agent TLS: always enforced per node\n  11 Executor workers\n  12 Trust remote SSH host key\n  0  Back\n'
    local a v domain email
    read -r -p '  Settings › ' a || return
    case "$a" in
      1) run --credentials;;
      2) read -r -p '  Agent port (1024–65535): ' v;run --set agent_port "$v";;
      3) read -r -p '  Private Web API port: ' v;run --set SONAR_WEB_PORT "$v";;
      4) read -r -p '  Domain (e.g. sonar.example.org): ' domain;run --domain "$domain";;
      5) read -r -p '  Domain: ' domain;read -r -p '  ACME email (optional): ' email;run --tls "$domain" "$email";;
      6) read -r -p '  Limit: ' v;run --set SONAR_GLOBAL_TEST_LIMIT "$v";;
      7) read -r -p '  Limit: ' v;run --set SONAR_USER_TEST_LIMIT "$v";;
      8) read -r -p '  Connections: ' v;run --set SONAR_DB_POOL_MAX "$v";;
      9) read -r -p '  Per-node WS pool: ' v;run --set SONAR_WS_POOL_MAX "$v";;
      10) say 'WSS is mandatory per agent. Re-enroll agent to rotate its pinned certificate.';;
      11) read -r -p '  Workers: ' v;run --set SONAR_EXECUTOR_WORKERS "$v";;
      13) read -r -p '  Log level [DEBUG/INFO/WARNING/ERROR]: ' v;run --set SONAR_LOG_LEVEL "$v";;
      12) read -r -p '  Remote SSH hostname or IP: ' domain;read -r -p '  SSH port [22]: ' v;run --trust-ssh "$domain" "${v:-22}";;
      0) return;; *) warn 'Unknown setting.';;
    esac
  done
}
run() { bash "$SOURCE_DIR/install.sh" "$@" || warn "Action failed (exit $?). See logs and diagnostics."; }
menu() {
  require_root
  while true;do
    clear 2>/dev/null || true
    printf '\n%b  ╭────────────────────────────────────────────────────────╮%b\n' "$C_BLUE" "$C_RESET"
    printf '%b  │%b  SONAR  /  RADAR                                    %b│%b\n' "$C_BLUE" "$C_WHITE" "$C_BLUE" "$C_RESET"
    printf '%b  │%b  PRO 5.2.1 · Deployment & Control Center          %b│%b\n' "$C_BLUE" "$C_DIM" "$C_BLUE" "$C_RESET"
    printf '%b  ╰────────────────────────────────────────────────────────╯%b\n' "$C_BLUE" "$C_RESET"
    printf '  01  Install Full Stack (Bot + DB + Mini App)\n  02  Update application safely\n  03  Install / repair Mini App panel\n  04  Configure domain + HTTPS\n  05  Settings & credentials\n  06  Service management\n  07  Professional logs (live/filter/JSON)\n  08  Create verified backup\n  09  Restore database from .dump\n  10  Diagnostics / health\n  11  Preflight source check\n  12  Uninstall (archive only)\n  13  Repair installed services\n  00  Exit\n\n'
    local choice s a d e p
    read -r -p '  SONAR › ' choice || break
    case "$choice" in
      1|01) run --install;;2|02) run --update;;3|03) run --panel;;
      4|04) read -r -p '  FQDN: ' d;run --domain "$d";read -r -p '  Request TLS certificate now? [y/N]: ' a;if [[ "$a" =~ ^[Yy]$ ]];then read -r -p '  Email (optional): ' e;run --tls "$d" "$e";fi;;
      5|05) settings_menu;;
      6|06) read -r -p '  Service [bot/web]: ' s;read -r -p '  Action [start/stop/restart/status]: ' a;case "$s" in bot) s="$SERVICE";;web) s="$WEB_SERVICE";;esac;run --service "$s" "$a";;
      7|07) printf '  Logs [bot/web/all/errors/json/install]: ';read -r s
            [[ "$s" =~ ^(bot|web|all|errors|json|install)$ ]] || { warn 'Invalid log source';continue; }
            read -r -p '  Follow live? [y/N]: ' a
            [[ "$a" =~ ^[Yy]$ ]] && bash "$SOURCE_DIR/scripts/logs.sh" "$s" --follow || bash "$SOURCE_DIR/scripts/logs.sh" "$s";;
      8|08) run --backup;;
      9|09) read -r -p '  Path to verified .dump: ' p;run --restore "$p";;
      10) run --doctor;;11) run --check;;12) run --uninstall;;13) run --repair;;
      0|00) break;; *) warn 'Unknown option.';;
    esac
    printf '\n';read -r -p '  Press Enter to continue...' _ || break
  done
}
case "${1:-}" in
  '') menu;;
  --check) preflight;;
  --install) install_app new;;
  --update) install_app upgrade;;
  --panel) require_root;require_service_host;ensure_directories;install_web;;
  --domain) require_root;require_service_host;ensure_directories;set_domain "${2:-}";;
  --tls) require_root;require_service_host;ensure_directories;issue_tls "${2:-}" "${3:-}";;
  --credentials) set_telegram_credentials;;
  --set) change_setting "${2:-}" "${3:-}";;
  --trust-ssh) require_root;require_service_host;ensure_directories;trust_ssh_host "${2:-}" "${3:-22}";;
  --service) service_command "${2:-}" "${3:-}";;
  --backup) require_root;require_service_host;make_backup;;
  --restore) require_service_host;restore_dump "${2:-}";;
  --doctor) require_root;require_service_host;doctor;;
  --repair) require_root;require_service_host;bash "$SOURCE_DIR/scripts/repair.sh";;
  --status) require_root;status;;
  --logs) require_root;shift;bash "$SOURCE_DIR/scripts/logs.sh" "${1:-bot}" "${@:2}";;
  --uninstall) uninstall_app;;
  --help|-h) cat <<'USAGE'
Sonar Radar Pro 5.2.1 | Deployment Manager
  sudo bash install.sh                 Interactive Apple-inspired terminal UI
  sudo bash install.sh --check         Offline source and OS checks
  sudo bash install.sh --install       Fresh FULL installation: Bot + DB + Mini App
  sudo bash install.sh --update        Upgrade whole stack; archive code and DB
  sudo bash install.sh --panel         Install local Telegram Mini App API
  sudo bash install.sh --domain NAME   Configure nginx HTTP vhost
  sudo bash install.sh --tls NAME EMAIL  Obtain Let's Encrypt certificate
  sudo bash install.sh --set KEY VALUE  Change allow-listed setting
  sudo bash install.sh --credentials    Change bot token/admin ID
  sudo bash install.sh --trust-ssh HOST PORT  Verify and trust remote SSH host key
  sudo bash install.sh --service sonar-bot restart
  sudo bash install.sh --status|--doctor|--repair
  sudo bash install.sh --logs [bot|web|all|errors|json|install] [--follow] [--lines N] [--since TIME] [--grep TEXT]
  sudo bash install.sh --backup
  sudo bash install.sh --restore /path/backup.dump
  sudo bash install.sh --uninstall     Archive installation, leave DB intact
For GitHub upload: .gitignore excludes credentials, keys, dumps, venv and logs.
USAGE
    ;;
  *) die 'Unknown command. Use --help.';;
esac
