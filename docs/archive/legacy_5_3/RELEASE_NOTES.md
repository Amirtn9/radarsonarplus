# Sonar Radar Pro 5.3.0

Fixes the server-add flow that reduced SSH failures to `SSH Error`, preserves the selected WSS port (default **8080**), and verifies a deployed agent with a real TLS/token/Xray health probe before reporting success.

- Strict SSH host verification with independent fingerprint confirmation in both server and Iran-node enrollment. Failed authentication never persists the proposed host key. Typed diagnostics distinguish unknown/changed keys, authentication, DNS, timeout and unreachable SSH ports.
- One server-action parser and repeated ownership checks at execution boundaries; shared paid-plan capability policy for plans 1/2/3.
- Stable physical-agent identity, encrypted token and certificate enrollment across shared records; repair zero legacy ports and reject ambiguous transports.
- Unified root/passwordless-sudo deployment, unprivileged agent service, exact listener checks, actual-port UFW rules and background installation/repair.
- Pinned Xray `v26.3.27` archives for x86_64/aarch64 with verified SHA-256, executable validation, geodata installation and cache verification. Preserve custom pinned environment settings on repair.
- Fail closed on reset cancellation, failed backup or PostgreSQL inventory; normalize source and venv access for the service user. Keep mutable state under `/var/lib/sonar-radar` while preserving legacy encryption keys.
- Fix Mini App startup/search input access, Pro keyword forwarding, missing agent buttons, bulk-install conversation state and read-only callback-data mutation in multi-delete navigation.
- Persist server status/resource history, honor thresholds and down-alert preferences, route notifications/reports/expiry to owner channels and topics, and run scheduled server reports.
- Use WSS for config operations, tenant-scope monitor selection, include JSON configs, honor bounded test sizes and use Mbps with null for unmeasured speeds. Upload speed is explicitly unmeasured.
- Dispose cancelled WebSocket requests and release reserved capacity; run long callbacks as managed background tasks. Avoid sending raw updates or exception messages in error summaries.
- Preserve and validate the existing scoped subscription-link migration. The earlier audit's global-UNIQUE finding was corrected after executing the complete migration.

Validation: **68 passed, 1 environment-related skip**, Python/Shell/JavaScript checks, real local SSH authentication/enrollment, real local WSS TLS/token/config RPC, native x86_64 Xray startup with geodata, and **310 SQL statements** against PostgreSQL via PGlite. See `QA_5.3_FA.md` for scope and remaining deployment checks. Native aarch64, live Telegram and full Ubuntu/systemd/UFW installation were not executed.

Extract the complete ZIP and run the installer from its source directory; `UPGRADE_NOTES_FA.md` explains data-preserving upgrades. Publishing is separate: upload the **contents of the project folder** to the repository root of `Amirtn9/radarsonarplus` on `main`. This delivery does not publish to GitHub or modify a live server.
