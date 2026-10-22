# Changelog

## 6.6.0-rc3 · Domain wizard readiness and recovery

- Domain/HTTPS wizard now verifies and repairs the local Mini App service before changing nginx or requesting TLS.
- Local `/healthz` validation waits for service readiness instead of issuing a single immediate curl after a systemd restart.
- On timeout, the installer prints the effective configured port, systemd unit port, service status, recent journal, and listening sockets.
- Wizard rollback now also restores the previous `sonar-web.service` unit, then reloads systemd before restarting original services.
- Added regression tests for readiness polling, port mismatch diagnostics, and rollback of the web unit.
- This patch addresses the installer failure mode `LOCAL_WEB_HEALTH_FAILED`; it does not claim the user's live service is repaired until RC3 is installed and the wizard is rerun successfully.

## 6.3.0 · Service uptime integration

- Added opt-in per-workspace HTTP/HTTPS and TCP monitoring, durable incident history, probe hysteresis, tenant-safe REST API, Telegram commands and Mini App controls.
- Added SSRF protections and pinned-DNS connections; no redirects, private addresses blocked by default.
- Added schema readiness check, sanitized probe errors, explicit expiry messaging and packaging integrity improvements.

## 6.1.0-rc.1 · 2026-10-17

**Release packaging and consistency**
- Bumped the application, installer banner, web panel badge, and `VERSION` file to `6.1.0-rc.1` so operators can identify the same build across entry points.
- Updated the version consistency regression test to validate the 6.1 release line.
- Preserved the complete V6 feature set and the existing upgrade, diagnostics, security, agent, Telegram, and web-panel flows. This release does not intentionally remove any capability.
- Rebuilt the SHA-256 integrity manifest after the release metadata updates.

**Validation notes**
- Python bytecode compilation and installer preflight pass in the packaging environment.
- Automated regression suite requires runtime dependencies declared in `requirements.txt` and `requirements-web.txt`. In the isolated build environment, `paramiko` and `python-telegram-bot` are unavailable and network access prevents installing them, so SSH and real Telegram registration tests cannot be fully run here.
- This remains a release candidate until end-to-end installation, PostgreSQL, Telegram Bot API, SSH, WSS/TLS, and restore/rollback tests pass on a disposable VPS. See `AUDIT_V6_RC1_FA.md` for the outstanding production-validation checklist.

## 6.0.0-rc.1 · 2026-10-16

**Data safety**: subscription membership is now derived from fetched links, not test success; `Unknown` preserves stable IDs and historical measurement fields. Destructive sync is disabled for incomplete/truncated/structured subscriptions and for fetch failures.

**Protocol parsing**: preserve VLESS Reality `shortId`, TLS SNI, ALPN/fingerprint, sing-box WS/gRPC/uTLS, Clash VMess gRPC serviceName, URL-decoded Trojan credentials and IPv6 Shadowsocks endpoints. Unsupported security/transport settings are rejected instead of silently downgraded.

**Network**: HTTPS sub fetch supports allowlisted nonstandard ports and retries publicly validated IPv4/IPv6 DNS answers without following redirects.

**Telegram**: diagnostic ZIP and long-running monitor maintenance no longer block ordered updates. Admin authorization is enforced in the monitor maintenance operation itself. QR generation is local and checked against configuration ownership.

**Security and operations**: expanded redaction for environment variables, tokens, identifiers, IPv4/IPv6 and domains; agent token validation handles invalid Unicode; agent source moves over SFTP rather than oversized shell arguments; removed dangerous `/tmp/*` purge; web session defaults to 300 seconds; `/readyz` checks live database health; per-process DB pool default/cap reduced; installer backup requires an isolated PostgreSQL restore rehearsal; GitHub bootstrap optionally enforces a pinned commit hash; severe CI checks are blocking.

**Docs and QA**: refreshed README, recovery, security, publication notes, source audit, RC tests and installation smoke script. Historic 5.3 reports moved to documentation archive.

**Known limitations**: production end-to-end environment untested; no complete SSH key wizard, durable distributed test queue, native upload benchmark, or full web parity. See QA report.

## 6.1.0-rc.1 package hardening (local revision)

- The standalone GitHub bootstrap now requests `sudo` automatically for host-mutating actions, so the documented `wget` / `chmod` / `./install.sh` flow works when started from a regular shell account.
- `--check` and `--help` remain usable without root.
- Added regression checks for direct bootstrap privilege escalation and for preserving the backup-before-upgrade ordering.
- Regenerated `MANIFEST_SHA256.txt` after the package changes.
- Validation in this build container: Bash syntax, Python compilation, and source manifest passed. The complete suite remains environment-blocked where optional runtime dependencies (`paramiko`, `python-telegram-bot`) are not installed.

## Reliability patch — 2026-10-22

- Added a real Python venv + pip preflight and version-specific Ubuntu `python3.X-venv` recovery before staging the Agent runtime.
- Changed Agent dependency provisioning to prefer central wheelhouse download, SHA-256 verification and SFTP transfer through direct SSH or approved Agent relays; target-side pip is a fallback. If Ubuntu prerequisite installation fails, the bootstrap can also transfer target-specific `.deb` packages from verifiable public APT URIs and install them with `--no-download`.
- Added a cross-region approved relay pool (Iran + foreign regions), owner opt-in checks, operator route priority, a three-minute relay cooldown after repeated failures, and 30-minute persistent destination affinity.
- Added the administrator-only web route balancer page and custom SSH relay port allowlist support.
- Added a root-owned allowlisted diagnostics helper for systemd journals and restricted log files, with narrowly scoped passwordless sudo rules for the service and SSH login accounts. Bot and web exports now default to full seven-day logs without per-file tail truncation; source logs are read-only.
- Expanded support ZIP contents to include all saved server Agent journals and allowlisted operational-state snapshots, with in-place secret redaction.
- See `docs/operations/RELIABILITY_AGENT_BALANCER_AND_LOGS_FA.md` for deployment and verification steps.
