# Changelog

## 6.0.0-rc.1 · 2026-10-16

**Data safety**: subscription membership is now derived from fetched links, not test success; `Unknown` preserves stable IDs and historical measurement fields. Destructive sync is disabled for incomplete/truncated/structured subscriptions and for fetch failures.

**Protocol parsing**: preserve VLESS Reality `shortId`, TLS SNI, ALPN/fingerprint, sing-box WS/gRPC/uTLS, Clash VMess gRPC serviceName, URL-decoded Trojan credentials and IPv6 Shadowsocks endpoints. Unsupported security/transport settings are rejected instead of silently downgraded.

**Network**: HTTPS sub fetch supports allowlisted nonstandard ports and retries publicly validated IPv4/IPv6 DNS answers without following redirects.

**Telegram**: diagnostic ZIP and long-running monitor maintenance no longer block ordered updates. Admin authorization is enforced in the monitor maintenance operation itself. QR generation is local and checked against configuration ownership.

**Security and operations**: expanded redaction for environment variables, tokens, identifiers, IPv4/IPv6 and domains; agent token validation handles invalid Unicode; agent source moves over SFTP rather than oversized shell arguments; removed dangerous `/tmp/*` purge; web session defaults to 300 seconds; `/readyz` checks live database health; per-process DB pool default/cap reduced; installer backup requires an isolated PostgreSQL restore rehearsal; GitHub bootstrap optionally enforces a pinned commit hash; severe CI checks are blocking.

**Docs and QA**: refreshed README, recovery, security, publication notes, source audit, RC tests and installation smoke script. Historic 5.3 reports moved to documentation archive.

**Known limitations**: production end-to-end environment untested; no complete SSH key wizard, durable distributed test queue, native upload benchmark, or full web parity. See QA report.
