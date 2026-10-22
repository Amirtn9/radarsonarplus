#!/usr/bin/env bash
# Narrow root helper for support-log export. Invoked only through sudoers.
# It never executes arbitrary commands or accepts arbitrary paths.
set -Eeuo pipefail

action="${1:-}"
case "$action" in
  journal)
    service="${2:-}"
    days="${3:-7}"
    [[ "$days" =~ ^([1-9]|[12][0-9]|30)$ ]] || { echo 'INVALID_LOG_WINDOW' >&2; exit 64; }
    priority=()
    case "$service" in
      bot) units=(-u sonar-bot);;
      web) units=(-u sonar-web);;
      agent) units=(-u sonar-agent);;
      both) units=(-u sonar-bot -u sonar-web);;
      errors) units=(-u sonar-bot -u sonar-web); priority=(--priority err);;
      *) echo 'DIAGNOSTIC_SERVICE_NOT_ALLOWED' >&2; exit 64;;
    esac
    exec /usr/bin/journalctl "${units[@]}" --since "$days days ago" --no-pager --output=short-iso-precise "${priority[@]}"
    ;;
  service)
    case "${2:-}" in
      bot) unit=sonar-bot.service;;
      web) unit=sonar-web.service;;
      agent) unit=sonar-agent.service;;
      *) echo 'DIAGNOSTIC_SERVICE_NOT_ALLOWED' >&2; exit 64;;
    esac
    exec /usr/bin/systemctl show "$unit" --no-pager --property=LoadState,ActiveState,SubState,Result,NRestarts,ExecMainStatus
    ;;
  install)
    file=/var/log/sonar-install.log
    [[ -f "$file" && ! -L "$file" ]] || { echo 'INSTALL_LOG_NOT_FOUND'; exit 0; }
    exec /usr/bin/cat -- "$file"
    ;;
  app-file)
    name="${2:-}"
    [[ "$name" =~ ^(sonar\.log|sonar\.log\.[0-9]+|sonar\.json|sonar\.json\.[0-9]+|sonar_errors\.json|sonar_errors\.json\.[0-9]+|sonar_bot\.log|sonar-web\.log)$ ]] || { echo 'DIAGNOSTIC_FILE_NOT_ALLOWED' >&2; exit 64; }
    file="/var/log/sonar/$name"
    [[ -f "$file" && ! -L "$file" ]] || { echo "LOG_NOT_FOUND: $name"; exit 0; }
    exec /usr/bin/cat -- "$file"
    ;;
  *)
    echo 'USAGE: sonar-diagnostic-read journal {bot|web|agent|both|errors} {1..30} | service {bot|web|agent} | install | app-file NAME' >&2
    exit 64
    ;;
esac
