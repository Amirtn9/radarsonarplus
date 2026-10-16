#!/usr/bin/env bash
# Sonar Radar Pro 5.3.0 | resilient GitHub deployment manager
# Supported: Ubuntu/Debian with systemd. Never execute remotely piped scripts as root.
set -Eeuo pipefail
umask 077
export LC_ALL=C.UTF-8

APP_DIR=/opt/radar-sonar
APP_USER=sonar-bot
STATE_DIR=/var/lib/sonar-radar
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
UI_ENABLED=0
[[ -t 1 && -t 0 && -z "${NO_COLOR:-}" ]] && UI_ENABLED=1

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
# Animated indicator is informational only. It never hides failure codes.
run_logged_command() { "$@" >> "$INSTALL_LOG" 2>&1; }
spinner_run() {
  local label="$1" pid rc=0 frames='|/-\' i=0
  shift
  if (( UI_ENABLED == 0 )); then
    say "$label"
    "$@"; return $?
  fi
  "$@" & pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    printf '\r  %b◐%b %-48s %s' "$C_BLUE" "$C_RESET" "$label" "${frames:i%4:1}"
    i=$((i+1)); sleep 0.12
  done
  wait "$pid" || rc=$?
  printf '\r\033[2K'
  (( rc == 0 )) && ok "$label" || warn "$label (exit $rc)"
  return "$rc"
}
ok() { printf '  %b✓%b %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '  %b!%b %s\n' "$C_ORANGE" "$C_RESET" "$*" >&2; }
die() { printf '  %b✕%b %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }
section() { printf '\n%b  ┌────────────────────────────────────────────────────────┐%b\n' "$C_BLUE" "$C_RESET"; printf '%b  │%b  %-54s%b│%b\n' "$C_BLUE" "$C_WHITE" "$1" "$C_BLUE" "$C_RESET"; printf '%b  └────────────────────────────────────────────────────────┘%b\n' "$C_BLUE" "$C_RESET"; }
require_root() { (( EUID == 0 )) || die 'Root access required. Run: sudo bash install.sh'; }
require_service_host() {
  [[ -f /etc/os-release ]] || die 'Missing /etc/os-release'
  # shellcheck source=/dev/null
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in *ubuntu*|*debian*) ;; *) die 'Only Debian and Ubuntu are supported.';; esac
  command -v systemctl >/dev/null && command -v apt-get >/dev/null && command -v runuser >/dev/null || die 'systemd, apt-get and runuser are required.'
  if command -v findmnt >/dev/null 2>&1 && findmnt -no OPTIONS -T /opt 2>/dev/null | tr ',' '\n' | grep -qx noexec;then
    die '/opt is mounted noexec. Executable virtual environments require an exec-enabled filesystem.'
  fi
}
ensure_directories() { install -d -m 0700 "$BACKUP_DIR"; install -d -m 0750 "$LOG_DIR"; touch "$INSTALL_LOG"; chmod 0600 "$INSTALL_LOG"; }
# Only resources created for this project may be detected/removed.
app_database_exists() {
  command -v psql >/dev/null 2>&1 && id -u postgres >/dev/null 2>&1 &&
    runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" 2>/dev/null | grep -qx 1
}
app_role_exists() {
  command -v psql >/dev/null 2>&1 && id -u postgres >/dev/null 2>&1 &&
    runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" 2>/dev/null | grep -qx 1
}
existing_components() {
  local found=0
  section 'EXISTING INSTALLATION  ·  Inventory'
  if [[ -d "$STATE_DIR" ]];then say "Runtime data: $STATE_DIR";found=1;fi
  if [[ -e "$APP_DIR" || -L "$APP_DIR" ]]; then say "Application: $APP_DIR";found=1;fi
  if [[ -e "$BOT_UNIT" || -L "$BOT_UNIT" ]];then say "Bot service: $BOT_UNIT";found=1;fi
  if [[ -e "$WEB_UNIT" || -L "$WEB_UNIT" ]];then say "Web service: $WEB_UNIT";found=1;fi
  if [[ -e "$ENV_FILE" || -L "$ENV_FILE" ]];then say "Private environment: $ENV_FILE";found=1;fi
  if [[ -e "$NGINX_SITE" || -L "$NGINX_SITE" || -L "$NGINX_LINK" ]];then say 'Sonar nginx configuration detected';found=1;fi
  if app_database_exists;then say "PostgreSQL database: $DB_NAME";found=1;fi
  if app_role_exists;then say "PostgreSQL role: $DB_USER";found=1;fi
  local leftover
  for leftover in /opt/.sonar-stage.*;do
    [[ -d "$leftover" && ! -L "$leftover" ]] || continue
    say "Interrupted installer staging folder: $leftover";found=1
  done
  if (( found == 0 ));then ok 'No previous Sonar installation detected';fi
  return "$found"
}
archive_existing_components() {
  # Archive all of the project-owned configuration before a destructive reset.
  local dest db_present=''
  dest="$BACKUP_DIR/before-reset-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  install -d -m 0700 "$dest"
  if [[ -d "$APP_DIR" && ! -L "$APP_DIR" ]];then
    tar -C /opt -czf "$dest/app.tar.gz" radar-sonar || die 'Application archive failed. Refusing reset.'
  fi
  [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]] || cp -p "$ENV_FILE" "$dest/sonar-bot.env" || die 'Environment archive failed.'
  [[ ! -f "$BOT_UNIT" || -L "$BOT_UNIT" ]] || cp -p "$BOT_UNIT" "$dest/sonar-bot.service" || die 'Bot unit archive failed.'
  [[ ! -f "$WEB_UNIT" || -L "$WEB_UNIT" ]] || cp -p "$WEB_UNIT" "$dest/sonar-web.service" || die 'Web unit archive failed.'
  [[ ! -f "$NGINX_SITE" || -L "$NGINX_SITE" ]] || cp -p "$NGINX_SITE" "$dest/sonar-nginx.conf" || die 'nginx archive failed.'
  if command -v psql >/dev/null 2>&1 && id -u postgres >/dev/null 2>&1;then
    db_present="$(runuser -u postgres -- psql -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")" || die 'Database inventory failed; refusing reset without a verified archive.'
  fi
  if [[ "$db_present" == 1 ]];then
    runuser -u postgres -- pg_dump -Fc --no-owner --no-acl -d "$DB_NAME" > "$dest/database.dump" || die 'Database archive failed. Refusing reset.'
    [[ -s "$dest/database.dump" ]] && pg_restore --list "$dest/database.dump" >/dev/null || die 'Backup verification failed. Refusing reset.'
  fi
  if [[ -d "$STATE_DIR" && ! -L "$STATE_DIR" ]]; then
    tar -C /var/lib -czf "$dest/runtime.tar.gz" sonar-radar || die 'Runtime archive failed. Refusing reset.'
  fi
  chmod -R go-rwx "$dest"
  ok "Verified reset archive saved: $dest"
}
reset_project_components() {
  require_root;require_service_host;ensure_directories
  [[ ! -L "$APP_DIR" && ! -L "$STATE_DIR" && ! -L "$ENV_FILE" && ! -L "$NGINX_SITE" ]] || die 'Symlinked Sonar path detected. Refusing unsafe reset.'
  # Fail closed: never mistake an offline PostgreSQL instance for an absent database.
  if command -v psql >/dev/null 2>&1 && id -u postgres >/dev/null 2>&1;then
    systemctl start postgresql >> "$INSTALL_LOG" 2>&1 || die 'PostgreSQL cannot be started; refusing unsafe reset.'
    runuser -u postgres -- psql -tAc 'SELECT 1' 2>> "$INSTALL_LOG" | grep -qx 1 || die 'PostgreSQL unreachable; refusing unsafe reset.'
  fi
  if existing_components;then return 0;fi
  local confirm
  warn 'This removes ONLY Sonar-specific code, services, nginx config, env, DB, and role.'
  warn 'It does NOT remove PostgreSQL itself, SSH, other databases, or unrelated services.'
  warn 'A verified backup of existing Sonar data is REQUIRED before deletion.'
  [[ -t 0 ]] || die 'Interactive approval required for destructive reset. Use a terminal.'
  read -r -p '  Type DELETE SONAR DATA to reset including PostgreSQL database: ' confirm
  [[ "$confirm" == 'DELETE SONAR DATA' ]] || die 'Reset cancelled; no data was deleted.'
  archive_existing_components || return 1
  systemctl disable --now "$SERVICE" "$WEB_SERVICE" >> "$INSTALL_LOG" 2>&1 || true
  if app_database_exists;then
    # Only sessions connected to Sonar DB are terminated.
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DB_NAME}' AND pid <> pg_backend_pid()" >> "$INSTALL_LOG" 2>&1 || die 'Could not disconnect Sonar database users.'
    runuser -u postgres -- dropdb --if-exists "$DB_NAME" >> "$INSTALL_LOG" 2>&1 || die 'Failed to drop Sonar database; archive retained.'
  fi
  if app_role_exists;then
    if ! runuser -u postgres -- dropuser "$DB_USER" >> "$INSTALL_LOG" 2>&1;then
      warn 'Database role is referenced elsewhere; keeping role instead of forcing deletion.'
    fi
  fi
  [[ "$APP_DIR" == /opt/radar-sonar && ! -L "$APP_DIR" ]] || die 'Refusing unsafe application path.'
  rm -rf -- "$APP_DIR"
  [[ "$STATE_DIR" == /var/lib/sonar-radar && ! -L "$STATE_DIR" ]] || die 'Refusing unsafe runtime path.'
  rm -rf -- "$STATE_DIR"
  [[ ! -L "$ENV_FILE" ]] || die 'Unsafe env symlink; remove it manually.'
  rm -f -- "$ENV_FILE" "$BOT_UNIT" "$WEB_UNIT"
  if [[ -L "$NGINX_LINK" ]] && [[ "$(readlink -f "$NGINX_LINK")" == "$NGINX_SITE" ]];then rm -f -- "$NGINX_LINK";fi
  [[ ! -L "$NGINX_SITE" ]] || die 'Unsafe nginx symlink; remove manually.'
  rm -f -- "$NGINX_SITE"
  # Orphaned staging directories use a dedicated, predictable /opt prefix.
  local orphan
  for orphan in /opt/.sonar-stage.*;do
    [[ -d "$orphan" && ! -L "$orphan" ]] || continue
    [[ "$orphan" =~ ^/opt/\.sonar-stage\.[A-Za-z0-9]+$ ]] || { warn "Skipped unexpected staging path: $orphan";continue; }
    rm -rf -- "$orphan"
  done
  systemctl daemon-reload
  if command -v nginx >/dev/null 2>&1 && nginx -t >> "$INSTALL_LOG" 2>&1;then systemctl reload nginx || true;fi
  ok 'Previous Sonar deployment and its database removed. Backups remain available.'
}
select_install_strategy() {
  # Return code 0 = proceed fresh, 2 = upgrade, 1 = abort.
  if existing_components;then
    return 0
  fi
  [[ -t 0 ]] || die 'Existing Sonar resources found. Run interactively to choose upgrade or clean reset.'
  printf '\n  [1] Keep existing data and update (requires installed application)\n'
  printf '  [2] Archive + REMOVE Sonar code, config, services and DATABASE, then install\n'
  printf '  [0] Cancel\n'
  local choice
  read -r -p '  Select [0-2]: ' choice
  case "$choice" in
    1) if [[ -f "$APP_DIR/bot.py" ]];then return 2;else die 'Application missing. Choose archive and reset for a clean installation.';fi;;
    2) reset_project_components || return 1;return 0;;
    0|*) die 'Installation cancelled. Nothing changed.';;
  esac
}

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
    SONAR_STATE_DIR|SONAR_DB_PASSWORD|SONAR_WEBAPP_URL|SONAR_WS_POOL_MAX|SONAR_GLOBAL_TEST_LIMIT|SONAR_USER_TEST_LIMIT|SONAR_DB_POOL_MAX|SONAR_EXECUTOR_WORKERS|SONAR_WEB_PORT|SONAR_AGENT_BIND|SONAR_LOG_LEVEL|SONAR_MINIAPP_SESSION_MAX_AGE) ;;
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
  local check_script
  for check_script in "$SOURCE_DIR/install.sh" "$SOURCE_DIR/install_web_panel.sh" "$SOURCE_DIR"/scripts/*.sh;do bash -n "$check_script" || die 'Bash syntax check failed';done
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
  spinner_run 'Refreshing package repositories' run_logged_command env DEBIAN_FRONTEND=noninteractive apt-get update || die "apt update failed; $INSTALL_LOG"
  spinner_run 'Installing OS prerequisites' run_logged_command env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-venv python3-dev build-essential libssl-dev libffi-dev git curl openssh-client ca-certificates tar postgresql postgresql-contrib libpq-dev libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core || die "Dependency installation failed; $INSTALL_LOG"
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
  tar -C "$SOURCE_DIR" --exclude='./venv' --exclude='./.venv' --exclude='./.git' --exclude='./__pycache__' --exclude='./.pytest_cache' --exclude='./secret.key' --exclude='./.env' --exclude='./tests' --exclude='./logs' --exclude='./runtime' -cf - . | tar -C "$STATE_STAGE" -xf -
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
  if [[ -f "$STATE_DIR/secret.key" ]]; then
    [[ ! -L "$STATE_DIR/secret.key" ]] || die 'Unsafe runtime encryption key symlink'
    cp -p "$STATE_DIR/secret.key" "$STATE_STAGE/secret.key"
  elif [[ -f "$APP_DIR/secret.key" ]]; then
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
  spinner_run 'Creating isolated Python environment' python3 -m venv "$STATE_STAGE/venv" || die 'venv creation failed.'
  spinner_run 'Installing pip tooling' run_logged_command "$STATE_STAGE/venv/bin/python" -m pip install -U pip setuptools wheel || die 'pip bootstrap failed.'
  spinner_run 'Installing Bot requirements' run_logged_command "$STATE_STAGE/venv/bin/python" -m pip install -r "$STATE_STAGE/requirements.txt" || die 'Python dependency installation failed.'
  spinner_run 'Installing Mini App requirements' run_logged_command "$STATE_STAGE/venv/bin/python" -m pip install -r "$STATE_STAGE/requirements-web.txt" || die 'Mini App dependencies failed.'
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
  [[ ! -f "$ENV_FILE" ]] || cp -p "$ENV_FILE" "${file}.env" || die 'Environment backup failed.'
  cp -p "$APP_DIR/sonar_config.json" "${file}.config.json" || die 'Configuration backup failed.'
  local key_source="$STATE_DIR/secret.key"
  [[ -f "$key_source" ]] || key_source="$APP_DIR/secret.key"
  [[ -f "$key_source" && ! -L "$key_source" ]] || die 'Encryption key missing; cannot make a usable backup.'
  cp -p "$key_source" "${file}.secret.key" || die 'Encryption key backup failed.'
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -tAc 'SELECT 1' | grep -qx 1 || die 'PostgreSQL unreachable; no verified database backup was created.'
  local db_present
  db_present="$(runuser -u postgres -- psql -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")" || die 'Database inventory failed; refusing backup success.'
  [[ "$db_present" == 1 ]] || die 'Sonar database is missing; cannot create a verified database backup.'
  runuser -u postgres -- pg_dump -Fc -d "$DB_NAME" > "${file}.dump" || { rm -f "${file}.dump"; die 'pg_dump failed, refusing to proceed.'; }
  [[ -s "${file}.dump" ]] || die 'Database backup empty!'
  pg_restore --list "${file}.dump" >/dev/null || die 'Database dump verification failed.'

  chmod 0600 "${file}"*; printf '%s\n' "$file"
}
normalize_source_permissions() {
  local source="$1"
  # Keep credential files private while opening only source/readable code paths.
  find "$source" -type d -not -path "$source/venv/*" -exec chmod 0755 {} +
  find "$source" -type f -not -path "$source/venv/*"     ! -name sonar_config.json ! -name secret.key ! -name known_hosts ! -name .env     -exec chmod 0644 {} +
  find "$source" -type f -name '*.sh' -not -path "$source/venv/*" -exec chmod 0755 {} +
}
prepare_runtime_state() {
  [[ "$STATE_DIR" == /var/lib/sonar-radar && ! -L "$STATE_DIR" ]] || die 'Unsafe runtime directory'
  install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$STATE_DIR"
  if [[ ! -f "$STATE_DIR/secret.key" ]];then
    [[ -f "$APP_DIR/secret.key" && ! -L "$APP_DIR/secret.key" ]] || die 'Encryption key missing before runtime migration'
    cp -p "$APP_DIR/secret.key" "$STATE_DIR/secret.key"
  fi
  [[ ! -L "$STATE_DIR/secret.key" ]] || die 'Unsafe runtime key symlink'
  chown "$APP_USER:$APP_USER" "$STATE_DIR/secret.key";chmod 0600 "$STATE_DIR/secret.key"
  install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$STATE_DIR/.ssh"
  if [[ -f "$APP_DIR/.ssh/known_hosts" && ! -f "$STATE_DIR/.ssh/known_hosts" ]];then
    cp -p "$APP_DIR/.ssh/known_hosts" "$STATE_DIR/.ssh/known_hosts"
  fi
  if [[ -f "$STATE_DIR/.ssh/known_hosts" ]];then
    chown "$APP_USER:$APP_USER" "$STATE_DIR/.ssh/known_hosts";chmod 0600 "$STATE_DIR/.ssh/known_hosts"
  fi
  set_env SONAR_STATE_DIR "$STATE_DIR"
}
verify_service_sources() {
  local source="$1"
  runuser -u "$APP_USER" -- "$source/venv/bin/python" - "$source" <<'SERVICE_SOURCE_PY'
from pathlib import Path
import ast,sys
root=Path(sys.argv[1])
for p in list(root.glob('*.py'))+list((root/'bot_logic_mod').glob('*.py')):
 ast.parse(p.read_text('utf8'),filename=str(p))
for p in [root/'pro_web/index.html',root/'pro_web/static/app.js',root/'agent_runtime.lock.json']:
 p.read_bytes()
print('Service user can read all application sources/assets')
SERVICE_SOURCE_PY
}

create_unit() {
  cat > "$BOT_UNIT" <<EOF
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
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/bot.py
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
  if [[ "$mode" == new ]]; then
    local chosen=0
    select_install_strategy || chosen=$?
    case "$chosen" in
      0) : ;;
      2) mode=upgrade; say 'Preserving data and upgrading existing installation.' ;;
      *) die 'Installation cancelled.' ;;
    esac
  fi
  if [[ "$mode" == upgrade ]];then
    [[ -d "$APP_DIR" && -f "$APP_DIR/bot.py" ]] || die 'No complete installation to upgrade.'
    [[ "$SOURCE_DIR" != "$APP_DIR" ]] || die 'Upgrade from a separate Git checkout, not the live directory.'
  fi
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
  normalize_source_permissions "$STATE_STAGE"
  chmod 0755 "$STATE_STAGE"
  # Python venv created with umask 077 has 0700 directories. Fix traversability.
  # This ONLY opens read/execute on the venv; config and secret.key stay 0600.
  chmod -R a+rX "$STATE_STAGE/venv"
  # Explicitly check the service account can run the EXACT promoted interpreter.
  runuser -u "$APP_USER" -- "$STATE_STAGE/venv/bin/python" -c 'import sys, telegram, psycopg2, websockets, fastapi, uvicorn; print(sys.version.split()[0])' >> "$INSTALL_LOG" 2>&1 || die 'Service user cannot execute Python/dependencies (permission or import failure).'
  chown "$APP_USER:$APP_USER" "$STATE_STAGE/sonar_config.json" "$STATE_STAGE/secret.key"
  chmod 0600 "$STATE_STAGE/sonar_config.json" "$STATE_STAGE/secret.key"
  if [[ -f "$STATE_STAGE/.ssh/known_hosts" ]]; then
    chown root:"$APP_USER" "$STATE_STAGE/.ssh" "$STATE_STAGE/.ssh/known_hosts"
    chmod 0750 "$STATE_STAGE/.ssh";chmod 0640 "$STATE_STAGE/.ssh/known_hosts"
  fi
  verify_service_sources "$STATE_STAGE" || die 'Service user cannot read application sources/assets.'
  chown "$APP_USER:$APP_USER" "$LOG_DIR"
  if systemctl is-active --quiet "$SERVICE"; then STATE_BOT_ACTIVE=1; fi
  if systemctl is-active --quiet "$WEB_SERVICE"; then STATE_WEB_ACTIVE=1; fi
  if [[ -f "$BOT_UNIT" ]]; then STATE_BOT_UNIT="$(mktemp "$BACKUP_DIR/unit-bot.XXXXXX")";cp -p "$BOT_UNIT" "$STATE_BOT_UNIT"; fi
  if [[ -f "$WEB_UNIT" ]]; then STATE_WEB_UNIT="$(mktemp "$BACKUP_DIR/unit-web.XXXXXX")";cp -p "$WEB_UNIT" "$STATE_WEB_UNIT"; fi
  systemctl stop "$SERVICE" "$WEB_SERVICE" >/dev/null 2>&1 || true
  STATE_SWITCH=1
  if [[ -d "$APP_DIR" ]]; then STATE_OLD="$BACKUP_DIR/previous-code-$(date -u +%Y%m%dT%H%M%SZ)-$$";mv -- "$APP_DIR" "$STATE_OLD";fi
  mv -- "$STATE_STAGE" "$APP_DIR";STATE_STAGE=''
  prepare_runtime_state
  create_unit
  systemctl daemon-reload
  systemd-analyze verify "$BOT_UNIT" >> "$INSTALL_LOG" 2>&1 || die 'Invalid systemd unit generated.'
  runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" -c 'import telegram, psycopg2, websockets' >> "$INSTALL_LOG" 2>&1 || die 'Installed interpreter is not executable by the service user.'
  systemctl enable --now "$SERVICE" >> "$INSTALL_LOG" 2>&1 || die 'Bot service did not start.'
  spinner_run 'Checking bot startup' sleep 5
  if ! systemctl is-active --quiet "$SERVICE";then
    warn 'Bot startup failed. Last 60 log lines:'
    journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
    die 'Bot process exited after startup.'
  fi
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
StartLimitIntervalSec=120
StartLimitBurst=8
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
  runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" -c 'import fastapi, uvicorn' >> "$INSTALL_LOG" 2>&1 || die 'Mini App dependencies missing. Run full installer repair/update.'
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
  [[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || die 'Invalid SSH port.'
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
  prepare_runtime_state
  local candidate
  candidate="$(python3 - "$temp" "$host" "$port" <<'SSH_CANDIDATE_PY'
import json,sys
line=next(line for line in open(sys.argv[1]) if line.strip() and not line.startswith('#'))
_,kind,key=line.split()[:3]
print(json.dumps({'host':sys.argv[2],'port':int(sys.argv[3]),'type':kind,'key':key}))
SSH_CANDIDATE_PY
)" || die 'Invalid SSH candidate key'
  runuser -u "$APP_USER" -- env SONAR_STATE_DIR="$STATE_DIR" SONAR_SOURCE_DIR="$APP_DIR" \
    "$APP_DIR/venv/bin/python" -c 'import json,os,sys;sys.path.insert(0,os.environ["SONAR_SOURCE_DIR"]);from ssh_support import trust_host_key;trust_host_key(json.loads(sys.argv[1]),sys.argv[2])' \
    "$candidate" "$expected" || die 'SSH key enrollment failed; existing trust was preserved.'
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
  if command -v getent >/dev/null 2>&1 && ! getent hosts "$(hostname)" >/dev/null 2>&1;then
    warn "Hostname $(hostname) does not resolve; verify /etc/hostname and /etc/hosts (not changed automatically)."
    ((problems+=1))
  fi
  [[ -f "$APP_DIR/bot.py" ]] && ok 'Bot sources found' || { warn 'Bot code missing';((problems+=1)); }
  [[ -f "$BOT_UNIT" ]] && ok 'Bot unit exists' || { warn 'Bot unit missing';((problems+=1)); }
  if [[ -f "$APP_DIR/sonar_config.json" ]]; then
    if [[ "$(read_cfg admin_id 0)" != 0 ]]; then ok 'Admin configured';else warn 'No admin configured';((problems+=1));fi
    if [[ "$(stat -c '%a' "$APP_DIR/sonar_config.json")" == 600 ]];then ok 'Config permissions 0600';else warn 'Config not 0600';((problems+=1));fi
  fi
  if systemctl is-active --quiet "$SERVICE";then ok 'Bot service active';else warn 'Bot not active';((problems+=1));fi
  if [[ -x "$APP_DIR/venv/bin/python" ]]; then
    if id -u "$APP_USER" >/dev/null 2>&1 && runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" -c 'import sys;print(sys.version)' >/dev/null 2>&1;then
      ok 'Python executable works as sonar-bot (not root)'
    else
      warn 'Python is not executable by sonar-bot. Inspect directory permissions (namei -l).'
      namei -l "$APP_DIR/venv/bin/python" >&2 || true
      ((problems+=1))
    fi
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
    printf '%b  │%b    /\  /\        SONAR  /  RADAR                  %b│%b\n' "$C_BLUE" "$C_WHITE" "$C_BLUE" "$C_RESET"
    printf '%b  │%b   ( \__/ )       PRO 5.3.0 · Control Center      %b│%b\n' "$C_BLUE" "$C_DIM" "$C_BLUE" "$C_RESET"
    printf '%b  ╰────────────────────────────────────────────────────────╯%b\n' "$C_BLUE" "$C_RESET"
    printf '  ─────── DEPLOY ────────────────────────────────────────\n  01  Install Full Stack (Bot + DB + Mini App)\n  02  Update application safely\n  03  Install / repair Mini App panel\n  04  Configure domain + HTTPS\n  05  Settings & credentials\n  06  Service management\n  07  Professional logs (live/filter/JSON)\n  08  Create verified backup\n  09  Restore database from .dump\n  10  Diagnostics / health\n  11  Preflight source check\n  12  Uninstall (archive only)\n  13  Repair installed services\n  14  Clean reset + reinstall (includes Sonar database)\n  00  Exit\n\n'
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
      10) run --doctor;;11) run --check;;12) run --uninstall;;13) run --repair;;14) run --clean-install;;
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
  --clean-install) require_root;require_service_host;reset_project_components;install_app new;;
  --inventory) require_root;require_service_host;existing_components || true;;
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
Sonar Radar Pro 5.3.0 | Deployment Manager
  sudo bash install.sh                 Interactive Apple-inspired terminal UI
  sudo bash install.sh --check         Offline source and OS checks
  sudo bash install.sh --install       Fresh FULL installation: Bot + DB + Mini App
  sudo bash install.sh --update        Upgrade whole stack; archive code and DB
  sudo bash install.sh --inventory     Detect leftover application services and database
  sudo bash install.sh --clean-install Archive and reset Sonar data+DB with explicit typed approval, then install
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
