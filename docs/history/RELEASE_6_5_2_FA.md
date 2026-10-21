# Sonar Radar Pro 6.5.2 — Agent port collision hotfix

## Evidence from the supplied support bundle
- `health.json` reports the bot and web unit are running; it does NOT prove agent health.
- `agents/status.json` reports two agents without TLS enrollment, one OK.
- Remote agent log shows `Errno 98` for `0.0.0.0:8080`, followed by systemd failures.
- `PS1: unbound variable` is an independent shell startup configuration error.

## Code changes
- `agent_deploy.py`: non-destructive preflight for listener ownership before creating the installer backup, changing TLS, files or runtime. Only a listener confirmed as owned by the current `sonar-agent.service` may be replaced through the normal restart path.
- `agent_deploy.py`: service startup validation checks that the expected systemd MainPID is actually listening on the requested port. A different application on 8080 is no longer considered proof of agent startup.
- No routing, balancer, WebSocket authentication protocol, database schema or Agent implementation has been modified.

## Target VPS diagnostics (execute as root or with sudo)
```
sudo ss -ltnp '( sport = :8080 )'
sudo systemctl status sonar-agent --no-pager -l
sudo systemctl show sonar-agent -p MainPID -p LoadState -p ActiveState
sudo journalctl -u sonar-agent -n 100 --no-pager
sudo grep -n 'PS1' /etc/bash.bashrc
```
Review the process name and PID; DO NOT kill the process blindly. If it is another service (e.g. an HTTP server), choose a free agent port in the Radarsonar settings and redeploy using the matching port; if it is a stale Agent install, inspect the unit and its process first. Do not manually regenerate certificates or delete the existing agent environment.

The PS1 error generally occurs when a globally sourced shell configuration expands `$PS1` while the installer runs bash with `set -u`. Once inspected, use `${PS1-}` or scope the prompt line to interactive shells rather than changing the privileged installer to ignore shell errors. Back up `/etc/bash.bashrc` before editing.

## Verification / limits
- Six focused offline tests passed for the preflight. Python compile and Bash syntax passed.
- No SSH session to the target VPS was available. No real port ownership, TLS enrollment, service restart, firewall, or WSS handshake was verified; this is a safety hotfix, not a confirmed repair of the running VPS.
- Do not install this over production without backing up first and testing on an equivalent VPS.
