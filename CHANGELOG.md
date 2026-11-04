# RadarSonarPlus 6.9 — Unified Web Panel and Telegram Mini App UI refresh (2026-11-04)

- Introduced a shared Apple-inspired design layer for desktop web and Telegram WebView without creating a second frontend or changing the API/database architecture.
- Reworked navigation, dashboard cards, forms, list surfaces, dialogs, responsive layouts, theme handling and mobile safe-area behavior.
- Added dashboard visibility into the existing Telegram notification outbox delivery counts.
- Connected the existing server/config favorites filter to the API and exposed incident lifecycle filtering.
- Improved mobile drawer accessibility, Escape-to-close behavior, active navigation semantics and reduced-motion support.
- Added regression coverage for shared-shell wiring, delivery telemetry, filter contracts, safe DOM rendering and responsive styles.
- No database migration or change to Telegram bot command/SSH/Agent execution logic was introduced.

# RadarSonarPlus 6.9 — Installer UI and full-purge patch

- Added a full-screen `whiptail` deployment menu with a colored terminal fallback when `whiptail` is unavailable.
- Added a separate irreversible `--purge` action that inventories and removes only local Sonar-owned files, services, configuration, runtime state, backups, logs, the local Sonar PostgreSQL database/role, and the dedicated service account when its home matches the managed install path.
- Full purge requires an interactive two-step confirmation and exits without reinstalling. It refuses symlinked or mounted project directories and refuses remote PostgreSQL targets before deleting local files.
- Shared PostgreSQL/Nginx packages, unrelated databases/sites, SSH, TLS certificates, remote Agents, and the shared systemd journal are intentionally preserved.
- Added release/source identity and SHA-256 manifest verification to preflight so operators can identify stale or mixed checkouts before installation.
- Added regression tests for purge scope, destructive confirmation, menu fallback, and systemd start-limit placement.

# RadarSonarPlus 6.9 — Post-audit reliability and installer recovery patch

- Fixed health timestamp parsing for legacy TEXT agent timestamps and stopped treating missing CPU/RAM/Disk data as zero load.
- Corrected `service_down` automation to query HTTP/TCP probe records; unsafe legacy restart rules are disabled and unsupported event/action pairs are rejected by both API validation and the service layer.
- Added the missing HTTP/TCP service-monitor schema migration to both Telegram Bot and Web API startup.
- Added automatic Agent Rollout rollback for successful hosts after a later batch fails, plus restart-safe rollback reservations and manual retry after rollback failure.
- Made signed Agent release identities immutable by version/channel.
- Moved synchronous database work out of async Webhook, Recovery and Rollout workers.
- Removed the unreachable per-server Backup operation path; scheduled PostgreSQL backups remain on their dedicated scheduler.
- Fixed venv permissions under the installer umask; added exact interpreter/mount diagnostics, systemd ExecStartPre checks and corrected Unit-level start limits.
- Added service-account bot import preflight, a post-migration startup readiness marker, bounded installer readiness wait and richer startup diagnostics.
- Added a non-destructive resume path when a first installation rolls back code but leaves PostgreSQL/runtime state intact.

# RadarSonarPlus 6.9.1 — Startup and transport reliability patch

- Deferred PostgreSQL initialization until the first real query and added bounded pool/schema retry after transient database startup races.
- Added named startup migration stages, explicit readiness logging, and complete traceback visibility.
- Standardized the bundled local PostgreSQL endpoint to `127.0.0.1:5432`.
- Added private service-owned enterprise backup storage and made the backup CLI load the protected environment file without shell evaluation.
- Applied one total deadline to WebSocket send and receive operations.
- Tracked checked-out and idle WebSocket connections and closed all of them safely during shutdown.
- Added a safe `.gitignore` and startup recovery guide.

# RadarSonarPlus 6.9 — Enterprise completion patch

- Added operation detail and pause/resume/cancel/retry controls with expired-lease recovery.
- Added Webhook CRUD, delivery history, dead-letter retry, API token expiry/list/revocation.
- Added configurable Health Score weights and thresholds plus 30-day server metrics history.
- Added Backup schedule controls, history and safer scheduler leases.
- Added signed Ed25519 Agent artifacts, hash/signature verification, canary rollout controls and group rollback.
- Added Telegram super-admin operation and rollout controls.
- Added integration tests for release signatures and configurable health scoring.

# RadarSonarPlus 6.9 — Enterprise Operations Completion

- Added PostgreSQL-backed operation execution workers with row leases, idempotency, bounded retries and persisted per-server results.
- Connected diagnostic, managed-service restart and Agent deployment tasks to existing Agent/SSH deployment paths.
- Added encrypted PostgreSQL backup schedules, local/S3-compatible destinations, retention cleanup and explicit-confirmation restore CLI.
- Added API token bearer authentication, scopes, revocation and 120 requests/minute token rate limiting.
- Added HTTPS HMAC-SHA256 webhooks with persistent delivery queue, retry/backoff and dead-letter state.
- Added allow-listed Auto Recovery notifications/restarts, canary/batched Agent rollout orchestration and organization analytics.
- Extended enterprise web panel and Telegram super-admin dashboard.
- Added unit tests for backup encryption, destination confinement, S3 bucket allowlisting and webhook signatures.

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
- This remains a release candidate until end-to-end installation, PostgreSQL, Telegram Bot API, SSH, WSS/TLS, and restore/rollback tests pass on a disposable VPS. See `docs/history/AUDIT_V6_RC1_FA.md` for the outstanding production-validation checklist.

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
