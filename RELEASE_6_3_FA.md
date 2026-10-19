# Sonar Radar Pro 6.3.0 — Uptime Monitoring Edition

**Status:** Release candidate (offline verified; not certified for production).

## Source projects and scope

- `uptime-kuma-master` (MIT, Louis Lam): studied heartbeat status UP/DOWN/PENDING,
  history, status visualization and incident-change notification concepts.
- `service-monitor-tgbot-master` (Apache-2.0): studied independent HTTP/TCP
  checks, scheduling, timeouts and success/failure thresholds.
- New Python implementations were authored specifically for Sonar;
  no JS/Go source was pasted into the distribution.
- **Not a full Uptime Kuma clone:** Docker, DNS, SMTP, JSON-query, SQL,
  push, maintenance scheduling, custom proxies, public status pages, and
  distributed geolocation checkers are intentionally not implemented.

## Added features

1. Multi-tenant persisted HTTP(S) and TCP uptime monitors with 60–3600 second intervals.
2. Configurable timeout (1–15 seconds), consecutive failure threshold (1–5), recovery threshold (1–5).
3. Bounded 20 monitor claims per 30-second job cycle (5 concurrent); PostgreSQL `SKIP LOCKED` avoids double claims across bot replicas.
4. Heartbeat persistence, configurable 14-day default cleanup, uptime history and HTML-free responsive WebApp status strip.
5. Incident lifecycle (open/acknowledged/resolved) shared with the existing Pro incident list and notification rules.
6. Telegram `/uptime`, `/uptime_add`, `/uptime_pause`, `/uptime_resume`, `/uptime_delete` commands.
7. Signed Telegram Mini App API: `GET/POST /api/services`, `PUT /api/services/{id}`, `POST /api/services/{id}/enabled`,
   `DELETE /api/services/{id}`, `GET /api/services/{id}/history`.
8. Explicit owner authorization on every read/write and history retrieval. Viewers cannot change monitors.
9. SSRF policy: resolved IPs must be public, IP is pinned for outbound connection, TLS certificates verified,
   automatic redirects disabled. Private targets are blocked unless the server operator **explicitly** sets
   `SONAR_SERVICE_ALLOW_PRIVATE=1` (unsafe for untrusted multi-tenant users).
10. Existing credential handling no longer confuses malformed `gAAAAAB...` plaintext with valid Fernet ciphertext;
    password-bearing Telegram messages are best-effort deleted.
11. `/readyz` checks presence of mandatory schema tables, while `/healthz` remains process liveness.
12. Distribution packaging includes hidden `.gitignore`, `.env.example`, CI checks and a newly validated SHA256 manifest.

## Migration and safety

Schema changes are additive, executed by `pro_store.install_schema()` during bot startup. New tables:
`pro_services`, `pro_service_history`. The `pro_migrations` marker `6` is inserted only after
successful DDL. No existing config, server or user table is dropped.

**Before installing:** take a PostgreSQL dump and file/agent key backups; validate restore in staging.
Do not replace the live GitHub repo with this ZIP without reviewing the diff and verifying the checksum.
An existing web-only deployment does not migrate DB itself; start bot migration first and wait for `/readyz`.

**Environment:** `.env.example` provides safe defaults. Set `SONAR_SERVICE_LIMIT_PER_WORKSPACE`
per load (default 50). Heartbeats are retained for 14 days by default. Keep `SONAR_SERVICE_ALLOW_PRIVATE=0` on shared deployments.
For active monitors, outbound DNS, TCP and HTTP(S) connectivity is required from the **bot server**.

### Telegram examples

```
/uptime
/uptime_add http Example|https://example.org/health
/uptime_add tcp SSH|example.org:443
/uptime_pause 1
/uptime_resume 1
/uptime_delete 1
```

### Validation completed offline

```bash
python -m unittest discover -s tests -p test_uptime_63_unittest.py -v
python -m compileall -q .
node --check pro_web/static/app.js
bash -n install.sh install_web_panel.sh scripts/*.sh
python scripts/verify_manifest.py
python -m pytest -q tests
```

Full integration validation STILL REQUIRED on disposable Ubuntu/Debian VPS:
PostgreSQL init and migration, Telegram `/uptime_add`, signed Mini App authentication,
HTTP/TCP test on public controlled host, 2 failures/1 recovery alert cycle,
HA dual worker claim test, installed SSL/WSS agent connection, backup-and-restore,
reverse proxy, and startup from systemd. The generic old tests may require
optional Python dependencies (`paramiko`, `python-telegram-bot`, `psycopg2`) not available
in the offline analysis environment.
