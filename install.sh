#!/usr/bin/env bash
# Sonar Radar Pro 6.9.7 | resilient GitHub deployment manager
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
GITHUB_URL="https://github.com/${GITHUB_REPOSITORY}.git"

# The documented one-command bootstrap is intentionally usable as a normal
# account: elevate only commands that mutate the host. Read-only source checks
# and help remain unprivileged. Never silently continue without sudo/root.
case "${1:-}" in
  --check|--help|-h) ;;
  *)
    if (( EUID != 0 )); then
      if command -v sudo >/dev/null 2>&1; then
        printf 'Sonar Radar Pro needs administrator privileges; requesting sudo…\n' >&2
        exec sudo -- bash "$SOURCE_DIR/install.sh" "$@"
      fi
      printf 'Administrator access required. Install sudo or run: su -c "bash %q %s"\n' "$SOURCE_DIR/install.sh" "${*:-}" >&2
      exit 1
    fi
    ;;
esac
STATE_STAGE=''; STATE_OLD=''; STATE_SWITCH=0; STATE_OLD_PRESENT=0
STATE_BOT_ACTIVE=0; STATE_WEB_ACTIVE=0; STATE_BOT_UNIT=''; STATE_WEB_UNIT=''
STATE_OK=0
STATE_INSTALL_ATTEMPT=0
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
  if [[ ! "${SONAR_RELEASE_COMMIT:-}" =~ ^[a-fA-F0-9]{40}$ ]]; then
    printf 'Refusing unpinned GitHub bootstrap. Set SONAR_RELEASE_COMMIT to the approved 40-character release commit SHA.\n' >&2
    exit 1
  fi
  approved_commit="${SONAR_RELEASE_COMMIT,,}"
  mkdir -p "$_sonar_bootstrap_dir/source"
  git -C "$_sonar_bootstrap_dir/source" init -q
  git -C "$_sonar_bootstrap_dir/source" remote add origin "$GITHUB_URL"
  if ! GIT_TERMINAL_PROMPT=0 git -C "$_sonar_bootstrap_dir/source" fetch --quiet --no-tags --depth 1 origin "$approved_commit"; then
    printf 'Cannot fetch the approved Git commit. Verify the SHA and repository access.\n' >&2
    exit 1
  fi
  git -C "$_sonar_bootstrap_dir/source" checkout --quiet --detach FETCH_HEAD
  actual_commit="$(git -C "$_sonar_bootstrap_dir/source" rev-parse HEAD)"
  [[ "$actual_commit" == "$approved_commit" ]] || { echo 'Fetched repository commit does not match approved release' >&2;exit 1; }
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
verify_restore_rehearsal() {
  # Real recovery test, not just pg_restore --list. Never expose the private
  # backup directory to postgres: stage a private copy in a postgres-owned dir.
  # This matters because the installer runs with umask 077 and BACKUP_DIR is 0700.
  local dump="$1" scratch="sonar_restore_check_$$" rc=0 stage_dir=''
  [[ -s "$dump" && ! -L "$dump" ]] || die 'Restore rehearsal requires an existing regular dump.'
  stage_dir="$(mktemp -d /tmp/sonar-restore-check.XXXXXXXX)" || die 'Could not create isolated restore staging directory.'
  if ! chown postgres:postgres "$stage_dir" || ! chmod 0700 "$stage_dir" ||
     ! install -o postgres -g postgres -m 0600 -- "$dump" "$stage_dir/backup.dump"; then
    rm -f -- "$stage_dir/backup.dump" 2>/dev/null || true
    rmdir -- "$stage_dir" 2>/dev/null || true
    die 'Could not stage backup securely for restore rehearsal.'
  fi
  if ! runuser -u postgres -- createdb --template=template0 "$scratch" >> "$INSTALL_LOG" 2>&1; then
    rm -f -- "$stage_dir/backup.dump"; rmdir -- "$stage_dir" 2>/dev/null || true
    die 'Could not create isolated restore rehearsal database.'
  fi
  runuser -u postgres -- pg_restore --exit-on-error --no-owner --no-acl -d "$scratch" "$stage_dir/backup.dump" >> "$INSTALL_LOG" 2>&1 || rc=$?
  if ! runuser -u postgres -- dropdb --if-exists "$scratch" >> "$INSTALL_LOG" 2>&1; then
    rm -f -- "$stage_dir/backup.dump"; rmdir -- "$stage_dir" 2>/dev/null || true
    die 'Cannot remove temporary restore rehearsal database; remove manually.'
  fi
  rm -f -- "$stage_dir/backup.dump"
  rmdir -- "$stage_dir" || die 'Could not remove restore rehearsal staging directory.'
  (( rc == 0 )) || die 'Restore rehearsal FAILED. Backup cannot be marked verified; inspect PostgreSQL errors in the installer log.'
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
    [[ -s "$dest/database.dump" ]] && pg_restore --list "$dest/database.dump" >/dev/null || die 'Backup archive validation failed. Refusing reset.'
    verify_restore_rehearsal "$dest/database.dump"
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

# Full purge is deliberately separate from --clean-install and --uninstall:
# it deletes project data AND existing backups, then returns to the shell.
# It never removes PostgreSQL/nginx packages or resources owned by other apps.
purge_list_item() {
  local path="$1" label="$2"
  if [[ -e "$path" || -L "$path" ]]; then say "$label: $path"; fi
}
purge_check_tree() {
  local path="$1" expected="$2" mounted=''
  [[ "$path" == "$expected" ]] || die "Internal purge path mismatch: $path"
  [[ ! -L "$path" ]] || die "Refusing to follow symlink during purge: $path"
  [[ -d "$path" ]] || return 0
  if command -v findmnt >/dev/null 2>&1; then
    mounted="$(findmnt -rn -o TARGET 2>/dev/null | awk -v p="$path" '$0==p || index($0,p "/")==1')"
    [[ -z "$mounted" ]] || die "Mounted filesystem found under $path; unmount it before purging: $mounted"
  fi
}
purge_remove_tree() {
  local path="$1" expected="$2" mounted=''
  [[ "$path" == "$expected" ]] || die "Internal purge path mismatch: $path"
  [[ ! -L "$path" ]] || die "Refusing to follow symlink during purge: $path"
  [[ -d "$path" ]] || { [[ ! -e "$path" ]] || die "Expected a directory: $path"; return 0; }
  if command -v findmnt >/dev/null 2>&1; then
    mounted="$(findmnt -rn -o TARGET 2>/dev/null | awk -v p="$path" '$0==p || index($0,p "/")==1')"
    [[ -z "$mounted" ]] || die "Mounted filesystem found under $path; unmount it before purging: $mounted"
  fi
  rm -rf --one-file-system -- "$path" || die "Could not remove project directory: $path"
}
purge_remove_file() {
  local path="$1" expected="$2"
  [[ "$path" == "$expected" ]] || die "Internal purge file mismatch: $path"
  [[ ! -d "$path" || -L "$path" ]] || die "Expected a file, not a directory: $path"
  rm -f -- "$path" || die "Could not remove project file: $path"
}
purge_database() {
  local db_name db_user db_host db_port db_exists role_exists
  db_name="$(read_env SONAR_DB_NAME)"; db_name="${db_name:-$DB_NAME}"
  db_user="$(read_env SONAR_DB_USER)"; db_user="${db_user:-$DB_USER}"
  db_host="$(read_env SONAR_DB_HOST)"; db_host="${db_host:-127.0.0.1}"
  db_port="$(read_env SONAR_DB_PORT)"; db_port="${db_port:-5432}"
  [[ "$db_name" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || die 'Configured PostgreSQL database name is unsafe; refusing automatic deletion.'
  [[ "$db_user" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || die 'Configured PostgreSQL role name is unsafe; refusing automatic deletion.'
  [[ "$db_port" =~ ^[0-9]{1,5}$ ]] && (( 10#$db_port >= 1 && 10#$db_port <= 65535 )) || die 'Configured PostgreSQL port is invalid; refusing automatic deletion.'
  case "${db_host,,}" in
    127.0.0.1|localhost|::1|'') ;;
    *) die "Remote PostgreSQL host $db_host is configured. Purge stopped without deleting project files; remove the remote DB separately first.";;
  esac
  if ! id -u postgres >/dev/null 2>&1; then
    warn 'Local PostgreSQL account is absent; no local database/role can be removed.'
    return 0
  fi
  command -v psql >/dev/null 2>&1 || die 'PostgreSQL client is missing; cannot verify database deletion safely.'
  if systemctl cat postgresql.service >/dev/null 2>&1; then
    systemctl start postgresql >> "$INSTALL_LOG" 2>&1 || die 'PostgreSQL did not start; refusing to claim database removal.'
  fi
  runuser -u postgres -- psql -p "$db_port" -v ON_ERROR_STOP=1 -tAc 'SELECT 1' >> "$INSTALL_LOG" 2>&1 || die 'Cannot connect to local PostgreSQL; database purge stopped before deleting files.'
  db_exists="$(runuser -u postgres -- psql -p "$db_port" -v ON_ERROR_STOP=1 -v db="$db_name" -tAc "SELECT 1 FROM pg_database WHERE datname = :'db'")" || die 'Could not inspect local PostgreSQL databases.'
  role_exists="$(runuser -u postgres -- psql -p "$db_port" -v ON_ERROR_STOP=1 -v role="$db_user" -tAc "SELECT 1 FROM pg_roles WHERE rolname = :'role'")" || die 'Could not inspect local PostgreSQL roles.'
  if [[ "$db_exists" == 1 ]]; then
    runuser -u postgres -- psql -p "$db_port" -v ON_ERROR_STOP=1 -v db="$db_name" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'db' AND pid <> pg_backend_pid()" >> "$INSTALL_LOG" 2>&1 || die "Could not disconnect sessions from $db_name."
    runuser -u postgres -- dropdb -p "$db_port" --if-exists -- "$db_name" >> "$INSTALL_LOG" 2>&1 || die "Could not drop Sonar database $db_name."
    ok "Removed local PostgreSQL database: $db_name"
  fi
  if [[ "$role_exists" == 1 ]]; then
    if runuser -u postgres -- dropuser -p "$db_port" -- "$db_user" >> "$INSTALL_LOG" 2>&1; then
      ok "Removed local PostgreSQL role: $db_user"
    else
      warn "PostgreSQL role $db_user still owns or is referenced by objects in another database; it was retained to protect unrelated data."
    fi
  fi
}
purge_inventory() {
  section 'FULL PURGE  ·  Project inventory'
  local target_db target_role target_host
  target_db="$(read_env SONAR_DB_NAME)";target_db="${target_db:-$DB_NAME}"
  target_role="$(read_env SONAR_DB_USER)";target_role="${target_role:-$DB_USER}"
  target_host="$(read_env SONAR_DB_HOST)";target_host="${target_host:-127.0.0.1}"
  say "PostgreSQL target (no password shown): database=$target_db role=$target_role host=$target_host"
  purge_list_item "$APP_DIR" 'Application code and virtualenv'
  purge_list_item "$STATE_DIR" 'Runtime state, keys, SSH trust and local backups'
  purge_list_item "$ENV_FILE" 'Private environment / database credentials'
  purge_list_item "$BOT_UNIT" 'Bot systemd unit'
  purge_list_item "$WEB_UNIT" 'Web systemd unit'
  purge_list_item "${BOT_UNIT}.d" 'Bot systemd overrides'
  purge_list_item "${WEB_UNIT}.d" 'Web systemd overrides'
  purge_list_item "$NGINX_SITE" 'Sonar nginx site'
  purge_list_item "$NGINX_LINK" 'Sonar nginx enabled link'
  purge_list_item "$BACKUP_DIR" 'Installer backups and database dumps'
  purge_list_item "$LOG_DIR" 'Application logs'
  purge_list_item "$INSTALL_LOG" 'Installer log'
  purge_list_item '/usr/local/sbin/sonar-diagnostic-read' 'Restricted diagnostics helper'
  purge_list_item '/etc/sudoers.d/sonar-diagnostics' 'Diagnostics sudoers rule'
  purge_list_item '/usr/local/libexec/sonar-log-maintenance.py' 'Fixed-scope log maintenance helper'
  purge_list_item '/etc/systemd/system/sonar-log-maintenance.socket' 'Private log maintenance socket'
  purge_list_item '/etc/systemd/system/sonar-log-maintenance@.service' 'Log maintenance unit'
  if [[ "$SOURCE_DIR" == /root/* && "${SOURCE_DIR##*/}" =~ ^(radarsonarplus|radar-sonar|radarsonarplus-[A-Za-z0-9._-]+)$ ]]; then
    purge_list_item "$SOURCE_DIR" 'Current Git/source checkout (optional)'
  fi
  if id -u "$APP_USER" >/dev/null 2>&1; then say "Dedicated service account detected: $APP_USER"; fi
  if app_database_exists; then say "Local PostgreSQL database detected: $DB_NAME"; fi
  if app_role_exists; then say "Local PostgreSQL role detected: $DB_USER"; fi
  say 'Shared OS packages, PostgreSQL server, nginx server, SSH and other apps are outside the purge scope.'
}
purge_project() {
  require_root
  command -v systemctl >/dev/null 2>&1 || die 'systemd is required for safe service shutdown.'
  [[ ! -L "$APP_DIR" && ! -L "$STATE_DIR" && ! -L "$BACKUP_DIR" && ! -L "$LOG_DIR" ]] || die 'A project directory is a symlink. Refusing unsafe purge.'
  [[ ! -L "$ENV_FILE" ]] || die 'Environment file is a symlink. Refusing unsafe purge.'
  [[ ! -L "$BOT_UNIT" && ! -L "$WEB_UNIT" ]] || die 'A systemd unit path is a symlink. Refusing unsafe purge.'
  [[ ! -L "$INSTALL_LOG" ]] || die 'Installer log is a symlink. Refusing unsafe purge.'
  purge_check_tree "$APP_DIR" '/opt/radar-sonar'
  purge_check_tree "$STATE_DIR" '/var/lib/sonar-radar'
  purge_check_tree "$BACKUP_DIR" '/var/backups/sonar-radar'
  purge_check_tree "$LOG_DIR" '/var/log/sonar'
  purge_check_tree "${BOT_UNIT}.d" '/etc/systemd/system/sonar-bot.service.d'
  purge_check_tree "${WEB_UNIT}.d" '/etc/systemd/system/sonar-web.service.d'
  local purge_db_host
  purge_db_host="$(read_env SONAR_DB_HOST)"; purge_db_host="${purge_db_host:-127.0.0.1}"
  case "${purge_db_host,,}" in 127.0.0.1|localhost|::1|'') ;; *) die "Remote PostgreSQL host $purge_db_host is configured. No changes were made; remove the remote database separately first.";; esac
  install -d -m 0750 "$LOG_DIR"
  touch "$INSTALL_LOG";chmod 0600 "$INSTALL_LOG"
  purge_inventory
  [[ -t 0 && -t 1 ]] || die 'Full purge requires an interactive terminal; no files were removed.'
  local confirm='' answer='' remove_source=0 source_candidate=''
  if command -v whiptail >/dev/null 2>&1; then
    whiptail --title 'DANGER  |  FULL SONAR PURGE' --yesno       'This permanently deletes Sonar source, database, credentials, encryption keys, runtime state, logs and ALL local backups. It does not uninstall shared PostgreSQL/nginx packages or touch unrelated databases.' 18 78 || { warn 'Purge cancelled; no data was deleted.'; return 2; }
    confirm="$(whiptail --title 'FINAL CONFIRMATION' --inputbox 'Type exactly: DELETE ALL SONAR DATA' 10 72 3>&1 1>&2 2>&3)" || { warn 'Purge cancelled; no data was deleted.'; return 2; }
  else
    warn 'IRREVERSIBLE: no backup will be retained. Project DB, secrets, keys, source, logs and backups will be deleted.'
    read -r -p '  Continue? [y/N]: ' answer
    [[ "$answer" =~ ^[Yy]$ ]] || { warn 'Purge cancelled; no data was deleted.'; return 2; }
    read -r -p '  Type exactly DELETE ALL SONAR DATA: ' confirm
  fi
  [[ "$confirm" == 'DELETE ALL SONAR DATA' ]] || die 'Confirmation did not match. Nothing was deleted.'

  # Only offer to remove the current checkout when it is clearly a project
  # directory under /root. Never remove /root itself or an arbitrary cwd.
  if [[ "$SOURCE_DIR" != "$APP_DIR" && "$SOURCE_DIR" == /root/* && "${SOURCE_DIR##*/}" =~ ^(radarsonarplus|radar-sonar|radarsonarplus-[A-Za-z0-9._-]+)$ && ! -L "$SOURCE_DIR" && "$(readlink -f -- "$SOURCE_DIR" 2>/dev/null || true)" == "$SOURCE_DIR" ]]; then
    source_candidate="$SOURCE_DIR"
    if command -v whiptail >/dev/null 2>&1; then
      whiptail --title 'SOURCE CHECKOUT' --yesno "Also delete this Git/source checkout?

$source_candidate" 12 76 && remove_source=1 || true
    else
      read -r -p "  Also delete source checkout $source_candidate? [y/N]: " answer
      [[ "$answer" =~ ^[Yy]$ ]] && remove_source=1 || true
    fi
  fi

  # Recheck the optional source checkout before deleting it.
  if (( remove_source == 1 )); then purge_check_tree "$source_candidate" "$source_candidate"; fi

  # Stop the project before terminating its database sessions. If PostgreSQL
  # cannot be inspected, the app code is still present for a safe retry.
  systemctl stop "$SERVICE" "$WEB_SERVICE" >> "$INSTALL_LOG" 2>&1 || true
  systemctl disable --now sonar-log-maintenance.socket >> "$INSTALL_LOG" 2>&1 || true
  systemctl stop 'sonar-log-maintenance@*.service' >> "$INSTALL_LOG" 2>&1 || true
  purge_database
  systemctl disable "$SERVICE" "$WEB_SERVICE" >> "$INSTALL_LOG" 2>&1 || true

  # Remove only the exact resources owned by this installer.
  purge_remove_tree "$APP_DIR" '/opt/radar-sonar'
  purge_remove_tree "$STATE_DIR" '/var/lib/sonar-radar'
  purge_remove_tree "$BACKUP_DIR" '/var/backups/sonar-radar'
  purge_remove_tree "$LOG_DIR" '/var/log/sonar'
  purge_remove_tree "${BOT_UNIT}.d" '/etc/systemd/system/sonar-bot.service.d'
  purge_remove_tree "${WEB_UNIT}.d" '/etc/systemd/system/sonar-web.service.d'
  purge_remove_file "$ENV_FILE" '/etc/sonar-bot.env'
  purge_remove_file "$BOT_UNIT" '/etc/systemd/system/sonar-bot.service'
  purge_remove_file "$WEB_UNIT" '/etc/systemd/system/sonar-web.service'
  purge_remove_file '/usr/local/sbin/sonar-diagnostic-read' '/usr/local/sbin/sonar-diagnostic-read'
  purge_remove_file '/etc/sudoers.d/sonar-diagnostics' '/etc/sudoers.d/sonar-diagnostics'
  purge_remove_file '/usr/local/libexec/sonar-log-maintenance.py' '/usr/local/libexec/sonar-log-maintenance.py'
  purge_remove_file '/etc/systemd/system/sonar-log-maintenance.socket' '/etc/systemd/system/sonar-log-maintenance.socket'
  purge_remove_file '/etc/systemd/system/sonar-log-maintenance@.service' '/etc/systemd/system/sonar-log-maintenance@.service'
  if [[ -L "$NGINX_LINK" ]]; then
    if [[ "$(readlink -f -- "$NGINX_LINK" 2>/dev/null || true)" == "$NGINX_SITE" ]]; then rm -f -- "$NGINX_LINK"; else warn 'Sonar nginx link points elsewhere; it was preserved.'; fi
  elif [[ -e "$NGINX_LINK" ]]; then warn 'Sonar nginx enabled path is not a symlink; it was preserved for manual review.'; fi
  purge_remove_file "$NGINX_SITE" '/etc/nginx/sites-available/sonar-radar.conf'

  # Clean only temporary directories/files created with the installer's
  # private prefixes. Environment staging files are regular files, not dirs.
  local orphan temp_file
  for orphan in /opt/.sonar-stage.* /opt/.sonar-bootstrap.* /run/sonar-diagnostic-helper.*; do
    [[ -e "$orphan" || -L "$orphan" ]] || continue
    [[ -d "$orphan" && ! -L "$orphan" ]] || { warn "Skipped non-directory temporary path: $orphan"; continue; }
    case "$orphan" in
      /opt/.sonar-stage.[A-Za-z0-9]*|/opt/.sonar-bootstrap.[A-Za-z0-9]*|/run/sonar-diagnostic-helper.[A-Za-z0-9]*) purge_check_tree "$orphan" "$orphan"; rm -rf --one-file-system -- "$orphan";;
      *) warn "Skipped unexpected temporary path: $orphan";;
    esac
  done
  for temp_file in /etc/.sonar-env.*; do
    [[ -e "$temp_file" || -L "$temp_file" ]] || continue
    [[ -f "$temp_file" && ! -L "$temp_file" && "$temp_file" =~ ^/etc/\.sonar-env\.[A-Za-z0-9]+$ ]] || { warn "Skipped unexpected environment staging path: $temp_file"; continue; }
    rm -f -- "$temp_file"
  done

  # Delete the dedicated service account only if its passwd home proves that
  # it was created for this exact Sonar installation.
  if id -u "$APP_USER" >/dev/null 2>&1; then
    local service_home service_gid service_uid other_primary group_members
    service_home="$(getent passwd "$APP_USER" | cut -d: -f6)"
    service_uid="$(id -u "$APP_USER")"
    service_gid="$(id -g "$APP_USER")"
    if [[ "$service_home" == "$APP_DIR" ]]; then
      pkill -TERM -u "$APP_USER" 2>/dev/null || true
      sleep 1
      pkill -KILL -u "$APP_USER" 2>/dev/null || true
      if userdel "$APP_USER" >> "$INSTALL_LOG" 2>&1; then
        other_primary="$(getent passwd | awk -F: -v gid="$service_gid" -v name="$APP_USER" '$4==gid && $1!=name {print $1}')"
        if [[ -z "$other_primary" ]] && getent group "$APP_USER" >/dev/null 2>&1; then
          group_members="$(getent group "$APP_USER" | cut -d: -f4)"
          [[ -n "$group_members" ]] || groupdel "$APP_USER" >> /dev/null 2>&1 || true
        fi
        ok "Removed dedicated service account $APP_USER (uid $service_uid)."
      else
        warn "Could not remove dedicated service account $APP_USER."
      fi
    else
      warn "Service account $APP_USER has a different home ($service_home); it was retained."
    fi
  fi

  # Remove installer log last: database and user cleanup above still append
  # diagnostics to it.
  purge_remove_file "$INSTALL_LOG" '/var/log/sonar-install.log'
  systemctl daemon-reload >> /dev/null 2>&1 || true
  if command -v nginx >/dev/null 2>&1; then
    if nginx -t >> /dev/null 2>&1; then systemctl reload nginx >> /dev/null 2>&1 || true; else warn 'Nginx config test failed after removing the Sonar site; inspect nginx -t.'; fi
  fi
  if (( remove_source == 1 )); then
    purge_remove_tree "$source_candidate" "$source_candidate"
    ok "Removed source checkout: $source_candidate"
  fi
  ok 'Full Sonar purge completed. The installer will now exit.'
  warn 'Shared PostgreSQL/nginx packages, external database hosts, remote Agents, TLS certificates and unrelated server files were intentionally preserved.'
}
select_install_strategy() {
  # Return code 0 = fresh/resume install, 2 = upgrade, 1 = abort.
  if existing_components;then
    return 0
  fi
  [[ -t 0 ]] || die 'Existing Sonar resources found. Run interactively to choose resume, upgrade or clean reset.'
  local choice
  if [[ -f "$APP_DIR/bot.py" ]];then
    printf '\n  [1] Keep existing data and update the installed application\n'
    printf '  [2] Archive + REMOVE Sonar code, config, services and DATABASE, then install\n'
    printf '  [0] Cancel\n'
    read -r -p '  Select [0-2]: ' choice
    case "$choice" in
      1) return 2;;
      2) reset_project_components || return 1;return 0;;
      0|*) die 'Installation cancelled. Nothing changed.';;
    esac
  else
    # A failed first install rolls application code back but deliberately keeps
    # PostgreSQL, the private environment and encryption key. Let the next run
    # resume with those resources instead of forcing a destructive clean reset.
    printf '\n  [1] Resume installation and PRESERVE existing database/runtime data\n'
    printf '  [2] Archive + REMOVE Sonar code, config, services and DATABASE, then install\n'
    printf '  [0] Cancel\n'
    read -r -p '  Select [0-2]: ' choice
    case "$choice" in
      1) say 'Resuming fresh deployment; existing PostgreSQL, environment and runtime key will be reused.';return 0;;
      2) reset_project_components || return 1;return 0;;
      0|*) die 'Installation cancelled. Nothing changed.';;
    esac
  fi
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
    SONAR_STATE_DIR|SONAR_DB_PASSWORD|SONAR_WEBAPP_URL|SONAR_WEB_ORIGIN|SONAR_WS_POOL_MAX|SONAR_GLOBAL_TEST_LIMIT|SONAR_USER_TEST_LIMIT|SONAR_DB_POOL_MAX|SONAR_EXECUTOR_WORKERS|SONAR_WEB_PORT|SONAR_AGENT_BIND|SONAR_AGENT_RELAY_PORTS|SONAR_LOG_LEVEL|SONAR_MINIAPP_SESSION_MAX_AGE) ;;
    *) die 'Environment key not permitted';;
  esac
  [[ "$val" != *$'\n'* && "$val" != *$'\r'* ]] || die 'Newline in setting not allowed'
  if [[ "$key" == SONAR_LOG_LEVEL ]];then
    [[ "$val" =~ ^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$ ]] || die 'Invalid log level'
  fi
  if [[ "$key" == SONAR_MINIAPP_SESSION_MAX_AGE ]];then
    [[ "$val" =~ ^[0-9]{2,4}$ ]] && (( 10#$val >= 60 && 10#$val <= 600 )) || die 'Session age must be 60..600s'
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
  local release='unknown' revision='archive-unknown' check_script
  [[ ! -f "$SOURCE_DIR/VERSION" ]] || release="$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")"
  [[ ! -f "$SOURCE_DIR/SOURCE_REVISION" ]] || revision="$(tr -d '[:space:]' < "$SOURCE_DIR/SOURCE_REVISION")"
  if command -v git >/dev/null 2>&1 && git -C "$SOURCE_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    revision="$(git -C "$SOURCE_DIR" rev-parse --short=12 HEAD)"
  fi
  say "Release: $release | Source: $SOURCE_DIR | Revision: $revision"
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
  if [[ -f "$SOURCE_DIR/scripts/verify_manifest.py" && -f "$SOURCE_DIR/MANIFEST_SHA256.txt" ]]; then
    python3 "$SOURCE_DIR/scripts/verify_manifest.py" || die 'Source manifest verification failed; files may be mixed between releases.'
  else
    warn 'Source manifest is missing; release integrity could not be verified.'
  fi
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
  spinner_run 'Installing OS prerequisites' run_logged_command env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-venv python3-dev build-essential libssl-dev libffi-dev git curl openssh-client ca-certificates tar postgresql postgresql-contrib libpq-dev libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core whiptail || die "Dependency installation failed; $INSTALL_LOG"
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
  # Venv executables and package files must be traversable by the unprivileged
  # systemd account. The installer itself keeps umask 077 for credentials;
  # use 022 only while constructing this non-secret runtime environment.
  local previous_umask
  previous_umask="$(umask)"
  umask 022
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
  # The venv is non-secret and may be readable by the service user; the
  # encryption key must remain private before the staging directory is opened.
  chmod 0600 "$STATE_STAGE/secret.key"
  umask "$previous_umask"
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
  pg_restore --list "${file}.dump" >/dev/null || die 'Database dump validation failed.'
  verify_restore_rehearsal "${file}.dump"
  local -a checksum_files=("$(basename "${file}.dump")" "$(basename "${file}.secret.key")" "$(basename "${file}.config.json")")
  [[ ! -f "${file}.env" ]] || checksum_files+=("$(basename "${file}.env")")
  (cd "$BACKUP_DIR" && sha256sum "${checksum_files[@]}" > "$(basename "${file}").sha256") || die 'Backup checksum manifest creation failed.'

  chmod 0600 "${file}"*; printf '%s\n' "$file"
}
normalize_source_permissions() {
  local source="$1"
  # Keep credential files private while opening only source/readable code paths.
  find "$source" -type d -not -path "$source/venv/*" -exec chmod 0755 {} +
  find "$source" -type f -not -path "$source/venv/*"     ! -name sonar_config.json ! -name secret.key ! -name known_hosts ! -name .env     -exec chmod 0644 {} +
  find "$source" -type f -name '*.sh' -not -path "$source/venv/*" -exec chmod 0755 {} +
}
diagnose_service_exec() {
  local executable="${1:-$APP_DIR/venv/bin/python}" resolved=''
  printf '\n--- Sonar interpreter execution diagnostics ---\n' >&2
  printf 'Requested path: %s\n' "$executable" >&2
  if command -v namei >/dev/null 2>&1; then namei -l "$executable" >&2 || true; fi
  resolved="$(readlink -f -- "$executable" 2>/dev/null || true)"
  printf 'Resolved path: %s\n' "${resolved:-unresolved}" >&2
  stat -Lc 'mode=%A (%a) owner=%U:%G file=%n' "$executable" "$resolved" 2>&1 || true
  if command -v findmnt >/dev/null 2>&1; then
    findmnt -no TARGET,OPTIONS -T "$executable" 2>&1 || true
    [[ -z "$resolved" ]] || findmnt -no TARGET,OPTIONS -T "$resolved" 2>&1 || true
  fi
  if id -u "$APP_USER" >/dev/null 2>&1; then
    runuser -u "$APP_USER" -- test -x "$executable" 2>&1 || true
    runuser -u "$APP_USER" -- "$executable" -c 'import sys; print(sys.executable)' 2>&1 || true
  fi
  systemctl show "$SERVICE" -p FragmentPath -p User -p ExecStart -p ActiveState -p ExecMainCode -p ExecMainStatus 2>&1 || true
  printf '%s\n' '--- End interpreter diagnostics ---' >&2
}
normalize_venv_permissions() {
  local venv="$1" resolved options candidate
  [[ -d "$venv" && ! -L "$venv" ]] || { warn "Virtual environment directory is missing or unsafe: $venv"; return 1; }
  # Open only the venv's code/runtime tree. Secrets and application config live
  # outside the venv and retain their 0600 permissions.
  find "$venv" -type d -exec chmod 0755 {} + || return 1
  find "$venv" -type f -perm /111 -exec chmod a+rx {} + || return 1
  find "$venv" -type f ! -perm /111 -exec chmod a+r {} + || return 1
  for candidate in "$venv/bin/python" "$venv/bin/python3" "$venv"/bin/python3.*; do
    [[ -f "$candidate" && ! -L "$candidate" ]] || continue
    chmod 0755 "$candidate" || return 1
  done
  [[ -x "$venv/bin/python" ]] || { warn "Venv interpreter is not executable: $venv/bin/python"; return 1; }
  resolved="$(readlink -f -- "$venv/bin/python" 2>/dev/null || true)"
  [[ -n "$resolved" && -f "$resolved" && -x "$resolved" ]] || { warn "Venv interpreter target is missing or not executable: ${resolved:-unresolved}"; return 1; }
  if command -v findmnt >/dev/null 2>&1; then
    options="$(findmnt -no OPTIONS -T "$venv/bin/python" 2>/dev/null || true)"
    if printf '%s\n' "$options" | tr ',' '\n' | grep -qx noexec; then
      warn "The filesystem containing $venv/bin/python is mounted noexec ($options)."; return 1
    fi
  fi
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
  local relay_ports; relay_ports="$(read_env SONAR_AGENT_RELAY_PORTS)"
  [[ -n "$relay_ports" ]] || set_env SONAR_AGENT_RELAY_PORTS '22,443,2022,2222,10022,22022'
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
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -x $APP_DIR/venv/bin/python
ExecStartPre=/usr/bin/test -r $APP_DIR/bot.py
ExecStart=$APP_DIR/venv/bin/python -u $APP_DIR/bot.py
StandardOutput=journal
StandardError=journal
LogNamespace=sonar
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
wait_for_bot_ready() {
  local timeout="${1:-120}" attempt=0 ready_file="$STATE_DIR/bot-ready" main_status active_state
  while (( attempt < timeout )); do
    if [[ -s "$ready_file" ]] && systemctl is-active --quiet "$SERVICE"; then
      ok "Bot startup readiness verified after ${attempt}s"
      return 0
    fi
    # 203/EXEC is a deterministic launch failure, not a slow startup. Stop
    # waiting and expose the path/mount diagnostics immediately.
    main_status="$(systemctl show -p ExecMainStatus --value "$SERVICE" 2>/dev/null || true)"
    active_state="$(systemctl show -p ActiveState --value "$SERVICE" 2>/dev/null || true)"
    if [[ "$main_status" == 203 || "$active_state" == failed ]]; then
      warn "Bot process cannot start (ActiveState=${active_state:-unknown}, ExecMainStatus=${main_status:-unknown})."
      diagnose_service_exec "$APP_DIR/venv/bin/python"
      journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
      return 1
    fi
    attempt=$((attempt+1))
    sleep 1
  done
  warn "BOT_STARTUP_READINESS_TIMEOUT: no readiness marker after ${timeout}s."
  diagnose_service_exec "$APP_DIR/venv/bin/python"
  systemctl status "$SERVICE" --no-pager -l >&2 || true
  warn 'Last 60 Bot journal lines:'
  journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
  if [[ -f "$INSTALL_LOG" ]]; then
    warn 'Last 100 installer log lines:'
    tail -n 100 "$INSTALL_LOG" >&2 || true
  fi
  return 1
}

# Generates a bounded/redacted report regardless of whether system services are up.
# Never reads env files, SSH keys, raw DB records or private configuration values.
support_report() {
  require_root
  install -d -m 0700 "$BACKUP_DIR"
  local source="$SOURCE_DIR" stamp output
  [[ -f "$APP_DIR/diagnostics.py" ]] && source="$APP_DIR"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  output="$BACKUP_DIR/sonar-support-${stamp}.zip"
  if python3 "$source/diagnostics.py" --output "$output";then
    chmod 0600 "$output"
    say "Support ZIP: $output"
    say 'Review the bundle before sharing. Redaction cannot guarantee removal of every secret.'
  else
    warn 'Support bundle creation failed; check installer log.'
    return 1
  fi
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
  if (( STATE_INSTALL_ATTEMPT == 1 ));then
    # Errors are still available even when code deployment rolls back.
    support_report || true
  fi
  exit "$rc"
}
trap rollback EXIT
install_diagnostic_helper() {
  local source="${1:-}" helper=/usr/local/sbin/sonar-diagnostic-read sudoers=/etc/sudoers.d/sonar-diagnostics
  [[ -f "$source" && ! -L "$source" ]] || { warn 'Diagnostic helper source missing; full system journal access may require manual sudo.'; return 1; }
  [[ "$APP_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { warn 'Invalid service account name; skipped diagnostic sudoers rule.'; return 1; }
  local backup_dir; backup_dir="$(mktemp -d /run/sonar-diagnostic-helper.XXXXXXXX)"
  [[ ! -f "$helper" ]] || cp -p "$helper" "$backup_dir/helper.old"
  [[ ! -f "$sudoers" ]] || cp -p "$sudoers" "$backup_dir/sudoers.old"
  install -o root -g root -m 0755 "$source" "$helper" || { rm -rf -- "$backup_dir"; return 1; }
  if [[ "$APP_USER" != root ]]; then
    printf '%s ALL=(root) NOPASSWD: %s *\n' "$APP_USER" "$helper" > "$sudoers"
    chmod 0440 "$sudoers"
    if ! visudo -cf "$sudoers" >/dev/null 2>&1; then
      [[ ! -f "$backup_dir/sudoers.old" ]] && rm -f "$sudoers" || cp -p "$backup_dir/sudoers.old" "$sudoers"
      [[ ! -f "$backup_dir/helper.old" ]] && rm -f "$helper" || cp -p "$backup_dir/helper.old" "$helper"
      rm -rf -- "$backup_dir"
      warn 'Scoped diagnostic sudoers validation failed; previous helper/rule restored.'
      return 1
    fi
  fi
  rm -rf -- "$backup_dir"
  say 'Installed restricted diagnostic reader; service account can read only allowlisted Sonar logs/journals.'
}

install_log_maintenance() {
  # IPC preserves NoNewPrivileges/ProtectSystem on Bot/Web; no sudo escape.
  install -d -m 0755 /usr/local/libexec /etc/systemd/journald@sonar.conf.d
  install -o root -g root -m 0755 "$APP_DIR/scripts/sonar_log_maintenance.py" /usr/local/libexec/sonar-log-maintenance.py
  cat > /etc/systemd/journald@sonar.conf.d/retention.conf <<'SONAR_JOURNAL'
[Journal]
SystemMaxUse=128M
RuntimeMaxUse=64M
MaxRetentionSec=30day
SONAR_JOURNAL
  cat > /etc/systemd/system/sonar-log-maintenance.socket <<EOF
[Unit]
Description=Sonar private log maintenance socket
[Socket]
ListenStream=/run/sonar-log-maintenance.sock
SocketUser=$APP_USER
SocketGroup=$APP_USER
SocketMode=0600
Accept=yes
MaxConnections=4
RemoveOnStop=yes
[Install]
WantedBy=sockets.target
EOF
  cat > /etc/systemd/system/sonar-log-maintenance@.service <<'SONAR_MAINTENANCE'
[Unit]
Description=Sonar fixed-scope log maintenance
[Service]
Type=exec
ExecStart=/usr/bin/python3 -I /usr/local/libexec/sonar-log-maintenance.py
User=root
StandardInput=socket
StandardOutput=socket
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/log/sonar /var/log/sonar-install.log -/var/log/journal -/run/log/journal
RuntimeMaxSec=90
MemoryMax=64M
TasksMax=8
SONAR_MAINTENANCE
  chmod 0644 /etc/systemd/system/sonar-log-maintenance.socket /etc/systemd/system/sonar-log-maintenance@.service
  systemd-analyze verify /etc/systemd/system/sonar-log-maintenance.socket /etc/systemd/system/sonar-log-maintenance@.service
  systemctl daemon-reload
  systemctl enable --now sonar-log-maintenance.socket
}

install_app() {
  local mode="$1"
  STATE_INSTALL_ATTEMPT=1
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
  # Normalize permissions even on hosts whose Python venv builder or package
  # installer ignores the caller's umask. Never leave systemd with a root-only
  # interpreter or a noexec runtime path.
  normalize_venv_permissions "$STATE_STAGE/venv" || { diagnose_service_exec "$STATE_STAGE/venv/bin/python"; die 'Virtual environment permissions or mount options prevent execution.'; }
  # Explicitly check the service account can run the EXACT promoted interpreter.
  runuser -u "$APP_USER" -- "$STATE_STAGE/venv/bin/python" -c 'import sys, telegram, psycopg2, websockets, fastapi, uvicorn; print(sys.version.split()[0])' >> "$INSTALL_LOG" 2>&1 || { diagnose_service_exec "$STATE_STAGE/venv/bin/python"; die 'Service user cannot execute Python/dependencies (permission or import failure).'; }
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
  systemd-analyze verify "$BOT_UNIT" >> "$INSTALL_LOG" 2>&1 || { diagnose_service_exec "$APP_DIR/venv/bin/python"; die 'Invalid systemd unit generated; inspect systemd-analyze output.'; }
  normalize_venv_permissions "$APP_DIR/venv" || { diagnose_service_exec "$APP_DIR/venv/bin/python"; die 'Promoted virtual environment is not executable.'; }
  runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" -c 'import telegram, psycopg2, websockets' >> "$INSTALL_LOG" 2>&1 || { diagnose_service_exec "$APP_DIR/venv/bin/python"; die 'Installed interpreter is not executable by the service user.'; }
  # Import the real bot entry point as the service account before changing the
  # live service. This catches missing modules and import-time exceptions while
  # rollback is still straightforward and before Telegram/network startup.
  runuser -u "$APP_USER" -- env SONAR_STATE_DIR="$STATE_DIR" SONAR_LOG_DIR="$LOG_DIR" PYTHONPATH="$APP_DIR" \
    "$APP_DIR/venv/bin/python" -c 'import bot; print("BOT_IMPORT_PREFLIGHT_OK")' >> "$INSTALL_LOG" 2>&1 || {
      warn 'Bot import preflight failed. Last 100 installer log lines:'
      tail -n 100 "$INSTALL_LOG" >&2 || true
      die 'Bot source could not be imported by the service account.'
    }
  rm -f -- "$STATE_DIR/bot-ready"
  systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
  if ! systemctl enable --now "$SERVICE" >> "$INSTALL_LOG" 2>&1; then
    diagnose_service_exec "$APP_DIR/venv/bin/python"
    journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
    die 'Bot service could not be executed or started; diagnostics are shown above.'
  fi
  if ! spinner_run 'Waiting for Telegram, PostgreSQL migrations and Bot readiness' wait_for_bot_ready 120; then
    journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
    die 'Bot process did not reach startup readiness; diagnostics are shown above.'
  fi
  # A full installation includes the Mini App API, not just the Telegram Bot.
  # Keep this inside the rollback transaction: failure restores the old code/units.
  install_web
  STATE_SWITCH=0;STATE_OK=1
  install_diagnostic_helper "$APP_DIR/scripts/sonar-diagnostic-read.sh" || warn 'Full diagnostics may report journal permission errors until the restricted helper is repaired.'
  install_log_maintenance || warn 'Log maintenance socket unavailable; the panel will report incomplete purge instead of deleting shared system logs.'
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
# Background workers are started from FastAPI startup; keep one process until they are split into a dedicated worker service.
ExecStart=$APP_DIR/venv/bin/python -m uvicorn pro_portal:app --host 127.0.0.1 --port $port --workers 1 --access-log
StandardOutput=journal
StandardError=journal
LogNamespace=sonar
SyslogIdentifier=sonar-web
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$LOG_DIR $STATE_DIR
UMask=0077
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$WEB_UNIT"
}
wait_for_web_health() {
  local port="$1" timeout="${2:-30}" attempt unit_port='unknown'
  valid_port "$port" || { warn "Invalid local Web API port: $port"; return 1; }
  for ((attempt=1; attempt<=timeout; attempt++)); do
    if systemctl is-active --quiet "$WEB_SERVICE" && \
       curl --fail --silent --connect-timeout 1 --max-time 3 "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1 && \
       curl --fail --silent --connect-timeout 1 --max-time 3 "http://127.0.0.1:${port}/readyz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  if [[ -f "$WEB_UNIT" ]]; then
    unit_port="$(sed -nE '/^ExecStart=.*--port[[:space:]]+[0-9]+/s/.*--port[[:space:]]+([0-9]+).*/\1/p' "$WEB_UNIT" | head -1)"
    unit_port="${unit_port:-unknown}"
  fi
  warn "LOCAL_WEB_HEALTH_FAILED: configured port=$port; systemd unit port=$unit_port; waited ${timeout}s."
  systemctl status "$WEB_SERVICE" --no-pager -l >&2 || true
  journalctl -u "$WEB_SERVICE" -n 80 --no-pager >&2 || true
  if command -v ss >/dev/null 2>&1; then ss -ltnp >&2 || true; fi
  return 1
}
install_web() {
  require_root;require_install
  local port; port="$(read_env SONAR_WEB_PORT)";port="${port:-8090}"
  valid_port "$port" || die 'Invalid configured web API port.'
  say "Installing Mini App API (loopback port $port)"
  runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" -c 'import fastapi, uvicorn' >> "$INSTALL_LOG" 2>&1 || die 'Mini App dependencies missing. Run full installer repair/update.'
  runuser -u "$APP_USER" -- env SONAR_STATE_DIR="$STATE_DIR" SONAR_LOG_DIR="$LOG_DIR" PYTHONPATH="$APP_DIR" \
    "$APP_DIR/venv/bin/python" -c 'import pro_portal; print("WEB_IMPORT_PREFLIGHT_OK")' >> "$INSTALL_LOG" 2>&1 || {
      warn 'Web API import preflight failed. Last 100 installer log lines:'
      tail -n 100 "$INSTALL_LOG" >&2 || true
      die 'Web API source could not be imported by the service account.'
    }
  # Regenerate even when a legacy installed service contains a malformed
  # '>StandardOutput=journal' token appended to ExecStart.
  create_web_unit "$port";systemctl daemon-reload
  systemd-analyze verify "$WEB_UNIT" >> "$INSTALL_LOG" 2>&1 || die 'Invalid systemd web unit generated; inspect systemd-analyze output.'
  systemctl enable "$WEB_SERVICE" >> "$INSTALL_LOG" 2>&1 || die 'Could not enable Web service.'
  # enable --now does not restart an already-active old process on upgrades.
  if ! systemctl restart "$WEB_SERVICE" >> "$INSTALL_LOG" 2>&1; then
    systemctl status "$WEB_SERVICE" --no-pager -l >&2 || true
    journalctl -u "$WEB_SERVICE" -n 80 --no-pager >&2 || true
    die 'Web service restart failed; service diagnostics are shown above.'
  fi
  wait_for_web_health "$port" 45 || die 'Web API failed its HTTP health check; inspect the service status and journal above.'
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
    # Full seven-day diagnostics may collect remote Agent journals over SSH.
    # Keep this admin-only endpoint's timeout separate from ordinary UI requests.
    location = /api/diagnostics/bundle {
        proxy_pass http://127.0.0.1:$port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }
    # Browser SSH terminal requires an explicit WebSocket upgrade path.
    location ^~ /api/terminal/ {
        proxy_pass http://127.0.0.1:$port;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
        proxy_request_buffering off;
    }
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
  set_env SONAR_WEB_ORIGIN "https://$domain"
  systemctl restart "$SERVICE" || die 'Bot restart after web URL change failed.'
  if ! systemctl restart "$WEB_SERVICE"; then
    systemctl status "$WEB_SERVICE" --no-pager -l >&2 || true
    journalctl -u "$WEB_SERVICE" -n 80 --no-pager >&2 || true
    die 'Web restart after origin update failed; service diagnostics are shown above.'
  fi
  ok "HTTPS provisioned and Mini App URL enabled: https://$domain/"
}

configure_domain_https() (
  # Run inside subshell so EXIT trap always restores operator configuration on
  # failed DNS/TLS/nginx/warmup steps; no automatic deletion of ACME certs.
  require_root;require_install
  local domain="$1" email="${2:-}" oldsite=0 oldlink=0 oldenv=0 oldunit=0 oldbotactive=0 oldwebactive=0 api_port=''
  valid_domain "$domain" || die 'Invalid domain.'
  [[ ! -L "$NGINX_SITE" ]] || die 'Refusing symlinked Sonar nginx site.'
  [[ ! -L "$WEB_UNIT" ]] || die 'Refusing symlinked Sonar web systemd unit.'
  local backup
  backup="$(mktemp -d "$BACKUP_DIR/web-wizard.XXXXXXXX")" || die 'Cannot create wizard backup'
  chmod 0700 "$backup"
  if [[ -f "$NGINX_SITE" ]];then cp -p "$NGINX_SITE" "$backup/nginx.conf";oldsite=1;fi
  if [[ -e "$NGINX_LINK" || -L "$NGINX_LINK" ]];then
    oldlink=1
    cp -a --no-dereference "$NGINX_LINK" "$backup/nginx.link"
  fi
  if [[ -f "$ENV_FILE" ]];then cp -p "$ENV_FILE" "$backup/env";oldenv=1;fi
  if [[ -f "$WEB_UNIT" ]];then cp -p "$WEB_UNIT" "$backup/sonar-web.service";oldunit=1;fi
  systemctl is-active --quiet "$SERVICE" && oldbotactive=1 || true
  systemctl is-active --quiet "$WEB_SERVICE" && oldwebactive=1 || true
  restore_wizard() {
    local result=$?
    if ((result!=0));then
      warn 'Domain wizard failed; restoring previous Sonar nginx/env/systemd unit and restarting original services.'
      if ((oldsite));then cp -p "$backup/nginx.conf" "$NGINX_SITE";else rm -f "$NGINX_SITE";fi
      rm -f -- "$NGINX_LINK"
      if ((oldlink));then cp -a --no-dereference "$backup/nginx.link" "$NGINX_LINK";fi
      if ((oldenv));then cp -p "$backup/env" "$ENV_FILE";else rm -f "$ENV_FILE";fi
      if ((oldunit));then cp -p "$backup/sonar-web.service" "$WEB_UNIT";else rm -f "$WEB_UNIT";fi
      systemctl daemon-reload >/dev/null 2>&1 || true
      if command -v nginx >/dev/null;then nginx -t >> "$INSTALL_LOG" 2>&1 && systemctl reload nginx || true;fi
      if ((oldbotactive));then systemctl restart "$SERVICE" >/dev/null 2>&1 || systemctl start "$SERVICE" >/dev/null 2>&1 || true;else systemctl stop "$SERVICE" >/dev/null 2>&1 || true;fi
      if ((oldwebactive));then systemctl restart "$WEB_SERVICE" >/dev/null 2>&1 || systemctl start "$WEB_SERVICE" >/dev/null 2>&1 || true;else systemctl stop "$WEB_SERVICE" >/dev/null 2>&1 || true;fi
    fi
    rm -rf -- "$backup"
  }
  trap restore_wizard EXIT
  local resolved
  resolved="$(getent ahosts "$domain" 2>/dev/null | head -1 || true)"
  [[ -n "$resolved" ]] || die 'DNS_NOT_RESOLVED: create an A/AAAA record before HTTPS provisioning.'
  # Repair/canonicalize the local API before touching nginx or requesting TLS.
  # This keeps SONAR_WEB_PORT, ExecStart and reverse-proxy upstream in sync.
  install_web
  api_port="$(read_env SONAR_WEB_PORT)";api_port="${api_port:-8090}"
  if [[ -f "$NGINX_SITE" ]] && grep -qE '^[[:space:]]*listen[[:space:]]+443' "$NGINX_SITE";then
    grep -Fq "server_name $domain;" "$NGINX_SITE" || die 'DOMAIN_MIGRATION_REQUIRED: existing TLS site uses a different domain.'
    say 'Existing HTTPS site for same domain detected; verifying and renewing TLS only.'
  else
    set_domain "$domain"
  fi
  issue_tls "$domain" "$email"
  wait_for_web_health "$api_port" 30 || die 'LOCAL_WEB_HEALTH_FAILED: local API did not become healthy after TLS/environment restart; service diagnostics are shown above.'
  curl --fail --silent --show-error --connect-timeout 3 --max-time 15 "https://$domain/healthz" >/dev/null || die 'HTTPS_WEB_HEALTH_FAILED: verify public DNS, firewall, reverse proxy and certificate.'
  curl --fail --silent --show-error --connect-timeout 3 --max-time 15 "https://$domain/readyz" >/dev/null || die 'HTTPS_WEB_NOT_READY: verify database schema, bot token, reverse proxy and application startup.'
  ok "HTTPS, nginx, bot URL, website origin and application readiness verified: https://$domain/"
)

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
    SONAR_AGENT_RELAY_PORTS)
      [[ "$value" =~ ^[0-9]+(,[0-9]+)*$ ]] || die 'Relay ports must be a comma-separated numeric allowlist.'
      local item; IFS=',' read -r -a relay_port_items <<< "$value"
      for item in "${relay_port_items[@]}"; do
        (( item >= 1 && item <= 65535 )) || die 'Relay port values must be between 1 and 65535.'
      done
      set_env "$key" "$value";;
    SONAR_WEB_PORT) configure_web_port "$value";;
    *) die 'Unsupported setting';;
  esac
  if [[ "$key" != SONAR_WEB_PORT ]]; then
    systemctl restart "$SERVICE" || die 'Bot restart failed.'
    if [[ "$key" == SONAR_LOG_LEVEL || "$key" == SONAR_MINIAPP_SESSION_MAX_AGE || "$key" == SONAR_AGENT_RELAY_PORTS ]]; then
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
  if systemctl is-active --quiet "$SERVICE";then
    ok 'Bot service active'
    if [[ -s "$STATE_DIR/bot-ready" ]];then ok 'Bot startup migrations/readiness marker present';else warn 'Bot has not reported startup readiness';((problems+=1));fi
  else warn 'Bot not active';((problems+=1));fi
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
restore_backup_pair() {
  local base="$1" key_tmp="$STATE_DIR/.secret.key.restore.$$"
  runuser -u postgres -- pg_restore --exit-on-error --single-transaction --clean --if-exists --no-owner --no-acl --role="$DB_USER" -d "$DB_NAME" < "${base}.dump" || return 1
  install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$STATE_DIR" || return 1
  install -o "$APP_USER" -g "$APP_USER" -m 0600 "${base}.secret.key" "$key_tmp" || return 1
  mv -f -- "$key_tmp" "$STATE_DIR/secret.key" || { rm -f -- "$key_tmp"; return 1; }
  return 0
}
verify_restored_secret_store() {
  local db_password db_host db_port verifier="$STATE_DIR/.verify_secret_key.$$"
  db_password="$(read_env SONAR_DB_PASSWORD)"
  db_host="$(read_env SONAR_DB_HOST)"; db_host="${db_host:-127.0.0.1}"
  db_port="$(read_env SONAR_DB_PORT)"; db_port="${db_port:-5432}"
  [[ -f "$SOURCE_DIR/scripts/verify_secret_key.py" ]] || return 1
  install -o "$APP_USER" -g "$APP_USER" -m 0644 "$SOURCE_DIR/scripts/verify_secret_key.py" "$verifier" || return 1
  export SONAR_STATE_DIR="$STATE_DIR" SONAR_DB_NAME="$DB_NAME" SONAR_DB_USER="$DB_USER"
  export SONAR_DB_PASSWORD="$db_password" SONAR_DB_HOST="$db_host" SONAR_DB_PORT="$db_port"
  if ! runuser -u "$APP_USER" --preserve-environment -- \
      "$APP_DIR/venv/bin/python" "$verifier"; then
    rm -f -- "$verifier"
    return 1
  fi
  rm -f -- "$verifier"
  return 0
}
restore_dump() {
  require_root;require_install
  local dump="$1" confirm now base key_backup manifest key_tmp
  [[ -f "$dump" && ! -L "$dump" && "$dump" == *.dump ]] || die 'Provide an existing trusted .dump file.'
  base="${dump%.dump}"
  key_backup="${base}.secret.key"
  manifest="${base}.sha256"
  [[ -f "$key_backup" && ! -L "$key_backup" ]] || die 'Matching .secret.key sidecar is required; a database dump alone cannot recover encrypted SSH credentials.'
  chmod 0600 "$key_backup" || die 'Could not secure the backup encryption key.'
  "$APP_DIR/venv/bin/python" - "$key_backup" <<'PYKEY' || die 'Backup encryption key is invalid.'
import pathlib,sys
from cryptography.fernet import Fernet
Fernet(pathlib.Path(sys.argv[1]).read_bytes().strip())
PYKEY
  if [[ -f "$manifest" && ! -L "$manifest" ]]; then
    (cd "$(dirname -- "$dump")" && sha256sum -c "$(basename -- "$manifest")") >/dev/null || die 'Backup checksum verification failed.'
  else
    warn 'Legacy backup has no SHA-256 sidecar; verify its origin before continuing.'
    read -r -p '  Type RESTORE LEGACY BACKUP to continue: ' confirm
    [[ "$confirm" == 'RESTORE LEGACY BACKUP' ]] || { warn 'Cancelled';return 0; }
  fi
  pg_restore --list "$dump" >/dev/null || die 'Not a readable PostgreSQL custom dump.'
  warn 'This REPLACES the contents of the CURRENT database and restores the matching encryption key.'
  read -r -p '  Type RESTORE DATABASE to proceed: ' confirm
  [[ "$confirm" == 'RESTORE DATABASE' ]] || { warn 'Cancelled';return 0; }
  now="$(make_backup)";say "Safety backup: $now"
  systemctl stop "$WEB_SERVICE" "$SERVICE" >/dev/null 2>&1 || true
  key_tmp="$STATE_DIR/.secret.key.restore.$$"
  # Restore the database and its exact Fernet key as one recovery unit.
  if ! runuser -u postgres -- pg_restore --exit-on-error --single-transaction --clean --if-exists --no-owner --no-acl --role="$DB_USER" -d "$DB_NAME" < "$dump"; then
    warn 'Requested restore failed; attempting automatic rollback from the verified safety backup.'
    if restore_backup_pair "$now"; then
      systemctl start "$SERVICE" || true
      [[ -f "$WEB_UNIT" ]] && systemctl start "$WEB_SERVICE" || true
      die 'Restore failed; previous database and encryption key were restored.'
    fi
    die 'Restore and automatic rollback both failed. Keep services stopped and recover from the safety backup manually.'
  fi
  install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$STATE_DIR" || {
    warn 'Could not prepare runtime key directory; rolling back database and key.'
    restore_backup_pair "$now" || die 'Rollback failed; keep services stopped.'
    die 'Restore aborted; previous database and encryption key were restored.'
  }
  if ! install -o "$APP_USER" -g "$APP_USER" -m 0600 "$key_backup" "$key_tmp" || ! mv -f -- "$key_tmp" "$STATE_DIR/secret.key"; then
    rm -f -- "$key_tmp"
    warn 'Could not install matching encryption key; rolling back database and key.'
    restore_backup_pair "$now" || die 'Rollback failed; keep services stopped.'
    die 'Restore aborted; previous database and encryption key were restored.'
  fi
  if ! verify_restored_secret_store; then
    warn 'Restored key could not decrypt the database credentials; rolling back database and key.'
    restore_backup_pair "$now" || die 'Rollback failed; keep services stopped.'
    systemctl start "$SERVICE" || true
    [[ -f "$WEB_UNIT" ]] && systemctl start "$WEB_SERVICE" || true
    die 'Restore aborted; previous database and encryption key were restored.'
  fi
  unset SONAR_DB_PASSWORD
  systemctl start "$SERVICE" || die 'Database and key restored, but bot service did not start; inspect journal before starting web.'
  [[ -f "$WEB_UNIT" ]] && systemctl start "$WEB_SERVICE" || true
  ok 'Database and matching encryption key restored. Verify application data and Agent connections.'
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
run() {
  local rc=0
  bash "$SOURCE_DIR/install.sh" "$@" || rc=$?
  (( rc == 0 )) || warn "Action failed (exit $rc). See logs and diagnostics."
  return "$rc"
}
ui_input() {
  local title="$1" prompt="$2" default="${3:-}" value=''
  if command -v whiptail >/dev/null 2>&1 && [[ -t 0 && -t 1 ]]; then
    value="$(whiptail --title "$title" --inputbox "$prompt" 10 76 "$default" 3>&1 1>&2 2>&3)" || return 1
  else
    read -r -p "  $prompt " value || return 1
    [[ -n "$value" ]] || value="$default"
  fi
  printf '%s' "$value"
}
menu_whiptail() {
  local choice domain email action service path source
  while true; do
    choice="$(whiptail --title 'SONAR RADAR PRO 6.9.7' --backtitle 'Deployment  |  Operations  |  Recovery' \
      --menu 'Select an operation. Destructive actions are clearly marked.' 24 88 16 \
      '01' 'Install / Resume full stack (preserve existing data)' \
      '02' 'Safe update (backup + rollback)' \
      '03' 'Install / Repair Mini App panel' \
      '04' 'Domain, Nginx and HTTPS setup' \
      '05' 'Settings and Telegram credentials' \
      '06' 'Service control' \
      '07' 'Logs and live journal' \
      '08' 'Create verified backup' \
      '09' 'Restore database backup' \
      '10' 'Diagnostics and health checks' \
      '11' 'Preflight / source integrity check' \
      '12' 'Uninstall application (keep DB and backups)' \
      '13' 'Repair services and permissions' \
      '14' 'Clean reset + reinstall (archive first)' \
      '15' 'FULL PURGE: delete all Sonar data and EXIT' \
      '16' 'Create redacted support bundle' \
      '00' 'Exit' 3>&1 1>&2 2>&3)" || break
    case "$choice" in
      01) run --install;;
      02) run --update;;
      03) run --panel;;
      04)
        domain="$(ui_input 'DOMAIN / HTTPS' 'Domain name (FQDN): ' '')" || continue
        email="$(ui_input 'DOMAIN / HTTPS' 'ACME email (optional): ' '')" || continue
        run --wizard "$domain" "$email";;
      05) settings_menu;;
      06)
        service="$(whiptail --title 'SERVICE CONTROL' --menu 'Choose service' 12 64 2 bot 'Telegram Bot' web 'Mini App API' 3>&1 1>&2 2>&3)" || continue
        action="$(whiptail --title 'SERVICE CONTROL' --menu 'Choose action' 12 64 4 start 'Start' stop 'Stop' restart 'Restart' status 'Status' 3>&1 1>&2 2>&3)" || continue
        if [[ "$service" == bot ]]; then service=sonar-bot; else service=sonar-web; fi
        run --service "$service" "$action";;
      07)
        service="$(whiptail --title 'LOGS' --menu 'Select log stream' 16 68 6 bot 'Bot journal' web 'Web journal' all 'Bot + Web' errors 'Errors only' json 'JSON application log' install 'Installer log' 3>&1 1>&2 2>&3)" || continue
        if whiptail --title 'LOGS' --yesno 'Follow logs live?' 8 56; then bash "$SOURCE_DIR/scripts/logs.sh" "$service" --follow; else bash "$SOURCE_DIR/scripts/logs.sh" "$service"; fi;;
      08) run --backup;;
      09) path="$(ui_input 'RESTORE DATABASE' 'Path to verified .dump file:' '')" || continue;run --restore "$path";;
      10) run --doctor;;
      11) run --check;;
      12) run --uninstall;;
      13) run --repair;;
      14) run --clean-install;;
      15) if run --purge; then break; fi;;
      16) run --support;;
      00) break;;
    esac
  done
}
menu_text() {
  local choice service action domain email path
  while true; do
    clear 2>/dev/null || true
    printf '\n%b  ╭──────────────────────────────────────────────────────────────────────╮%b\n' "$C_BLUE" "$C_RESET"
    printf '%b  │%b  SONAR RADAR PRO 6.9.7  ·  DEPLOYMENT & OPERATIONS CONSOLE             %b│%b\n' "$C_BLUE" "$C_WHITE" "$C_BLUE" "$C_RESET"
    printf '%b  ╰──────────────────────────────────────────────────────────────────────╯%b\n' "$C_BLUE" "$C_RESET"
    printf '  %-37s %-37s\n' 'INSTALL & UPDATE' 'OPERATIONS'
    printf '  01  Install / Resume full stack     06  Service control\n'
    printf '  02  Safe update                     07  Logs / live journal\n'
    printf '  03  Mini App panel                  08  Create verified backup\n'
    printf '  04  Domain / HTTPS                  09  Restore database backup\n'
    printf '  05  Settings / credentials          10  Diagnostics / health\n'
    printf '  ────────────────────────────────────────────────────────────────────────\n'
    printf '  11  Preflight / source integrity\n  12  Uninstall application (keep DB and backups)\n  13  Repair services and permissions\n  14  Clean reset + reinstall (archive first)\n'
    printf '%b  15  FULL PURGE: delete all Sonar data and EXIT%b\n' "$C_RED" "$C_RESET"
    printf '  16  Create redacted support bundle\n  00  Exit\n\n'
    read -r -p '  SONAR › ' choice || break
    case "$choice" in
      1|01) run --install;;
      2|02) run --update;;
      3|03) run --panel;;
      4|04) read -r -p '  FQDN: ' domain;read -r -p '  ACME email (optional): ' email;run --wizard "$domain" "$email";;
      5|05) settings_menu;;
      6|06) read -r -p '  Service [bot/web]: ' service;read -r -p '  Action [start/stop/restart/status]: ' action;case "$service" in bot) service=sonar-bot;;web) service=sonar-web;;esac;run --service "$service" "$action";;
      7|07) read -r -p '  Logs [bot/web/all/errors/json/install]: ' service;[[ "$service" =~ ^(bot|web|all|errors|json|install)$ ]] || { warn 'Invalid log source';continue; };read -r -p '  Follow live? [y/N]: ' action;[[ "$action" =~ ^[Yy]$ ]] && bash "$SOURCE_DIR/scripts/logs.sh" "$service" --follow || bash "$SOURCE_DIR/scripts/logs.sh" "$service";;
      8|08) run --backup;;
      9|09) read -r -p '  Path to verified .dump: ' path;run --restore "$path";;
      10) run --doctor;;
      11) run --check;;
      12) run --uninstall;;
      13) run --repair;;
      14) run --clean-install;;
      15) if run --purge; then break; fi;;
      16) run --support;;
      0|00) break;;
      *) warn 'Unknown option.';;
    esac
    printf '\n';read -r -p '  Press Enter to continue...' _ || break
  done
}
menu() {
  require_root
  if [[ -t 0 && -t 1 ]] && command -v whiptail >/dev/null 2>&1; then
    menu_whiptail
  else
    menu_text
  fi
}

case "${1:-}" in
  '') menu;;
  --check) preflight;;
  --install) install_app new;;
  --update) install_app upgrade;;
  --clean-install) require_root;require_service_host;reset_project_components;install_app new;;
  --inventory) require_root;require_service_host;existing_components || true;;
  --panel) require_root;require_service_host;ensure_directories;install_web;;
  --font) require_root;require_service_host;require_install;bash "$SOURCE_DIR/scripts/install_persian_font.sh";;
  --wizard) require_root;require_service_host;ensure_directories;configure_domain_https "${2:-}" "${3:-}";;
  --domain) require_root;require_service_host;ensure_directories;set_domain "${2:-}";;
  --tls) require_root;require_service_host;ensure_directories;issue_tls "${2:-}" "${3:-}";;
  --credentials) set_telegram_credentials;;
  --set) change_setting "${2:-}" "${3:-}";;
  --trust-ssh) require_root;require_service_host;ensure_directories;trust_ssh_host "${2:-}" "${3:-22}";;
  --service) service_command "${2:-}" "${3:-}";;
  --backup) require_root;require_service_host;make_backup;;
  --restore) require_service_host;restore_dump "${2:-}";;
  --doctor) require_root;require_service_host;doctor;;
  --support) require_root;support_report;;
  --repair) require_root;require_service_host;bash "$SOURCE_DIR/scripts/repair.sh";;
  --status) require_root;status;;
  --logs) require_root;shift;bash "$SOURCE_DIR/scripts/logs.sh" "${1:-bot}" "${@:2}";;
  --uninstall) uninstall_app;;
  --purge) purge_project;;
  --help|-h) cat <<'USAGE'
Sonar Radar Pro 6.9.7 | Deployment Manager
  sudo bash install.sh                 Interactive whiptail/ANSI deployment console
  sudo bash install.sh --check         Offline source and OS checks
  sudo bash install.sh --install       Fresh FULL installation: Bot + DB + Mini App
  sudo bash install.sh --update        Upgrade whole stack; archive code and DB
  sudo bash install.sh --inventory     Detect leftover application services and database
  sudo bash install.sh --clean-install Archive and reset Sonar data+DB with explicit typed approval, then install
  sudo bash install.sh --purge         Irreversible full project purge; leaves shared OS services intact
  sudo bash install.sh --panel         Install local Telegram Mini App API
  sudo bash install.sh --wizard DOMAIN EMAIL  Configure DNS, nginx, HTTPS, origin and web health
  sudo bash install.sh --font          Install licensed Vazirmatn font on VPS
  sudo bash install.sh --domain NAME   Configure nginx HTTP vhost
  sudo bash install.sh --tls NAME EMAIL  Obtain Let's Encrypt certificate
  sudo bash install.sh --set KEY VALUE  Change allow-listed setting
  sudo bash install.sh --credentials    Change bot token/admin ID
  sudo bash install.sh --trust-ssh HOST PORT  Verify and trust remote SSH host key
  sudo bash install.sh --service sonar-bot restart
  sudo bash install.sh --status|--doctor|--repair
  sudo bash install.sh --support      Generate a redacted ZIP for diagnosis
  sudo bash install.sh --logs [bot|web|all|errors|json|install] [--follow] [--lines N] [--since TIME] [--grep TEXT]
  sudo bash install.sh --backup
  sudo bash install.sh --restore /path/backup.dump
  sudo bash install.sh --uninstall     Archive application, leave DB and backups intact
  sudo bash install.sh --purge         IRREVERSIBLE: delete Sonar code, DB, keys, logs and backups; then exit
For GitHub upload: .gitignore excludes credentials, keys, dumps, venv and logs.
USAGE
    ;;
  *) die 'Unknown command. Use --help.';;
esac
