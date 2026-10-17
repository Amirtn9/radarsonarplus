# Sonar Radar Pro V6.1 Security Notes

**Release status:** `6.1.0-rc.1`. This is not a completed security audit or production safety certification.

- **SSH**: host keys must be verified out of band. Never replace `RejectPolicy` with an automatically trusting policy. An operational account with minimum required sudo privilege is recommended.
- **WSS**: require a TLS certificate pinned to the target, and independent agent authentication token. Avoid storing secrets in HTTP-accessible files.
- **Subscriptions**: DNS-public-address allowlist, pinned HTTPS connection, redirect rejection, bounded read (2 MiB), supported-port allowlist. Supported ports: 443, 8443, 2096, 2053, 2083, 2087. Keep this list restrictive.
- **Web Mini App**: validate signed initData; default maximum age 300 seconds and hard cap 600 seconds. Web API only on loopback behind HTTPS proxy. User ownership must be checked inside every mutation.
- **Support bundles**: sanitization is best-effort. Always review bundle before sharing. Never forward `secret.key`, private SSH keys, `.env`, tokens, or unredacted database dumps.
- **Supply chain**: the manifest is a file integrity check, not a cryptographic signature of origin. GitHub installs should use a verified commit SHA; `main` without a pin is mutable.
- **Backups**: `--backup` performs PostgreSQL restore rehearsal into a disposable database. Encryption key, env and DB dump must remain together for genuine recovery.

Security concerns should be reported privately to the repository maintainer rather than disclosed with exploit details or secrets in public issues.
