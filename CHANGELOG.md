# Changelog

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
