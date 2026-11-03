# Sonar Radar Pro Security Notes

**Release:** `6.9` (see the repository `VERSION` file). The repository is a release candidate; these notes are not a completed security audit or production safety certification.

- **SSH:** host keys must be verified out of band. Never replace `RejectPolicy` with an automatically trusting policy. An operational account with minimum required sudo privilege is recommended.
- **WSS:** require a TLS certificate pinned to the target and an independent Agent authentication token. Avoid storing secrets in HTTP-accessible files.
- **Subscriptions:** retain public-address validation, pinned HTTPS connections, redirect rejection, bounded reads and supported-port allowlists. Do not broaden these controls to make a connection succeed.
- **Web Mini App:** validate signed initData; the default maximum age is 300 seconds with a hard cap of 600 seconds. Keep the Web API on loopback behind an HTTPS proxy. Check user ownership inside every mutation.
- **Support bundles:** sanitization is best-effort. Always review a bundle before sharing. Never forward `secret.key`, private SSH keys, `.env`, tokens or unredacted database dumps.
- **Supply chain:** `MANIFEST_SHA256.txt` is a file-integrity check, not a cryptographic signature of origin. GitHub installs should use a verified commit SHA; `main` without a pin is mutable.
- **Backups:** `--backup` performs a PostgreSQL restore rehearsal into a disposable database. The encryption key, environment file and database dump must remain together for genuine recovery.

Security concerns should be reported privately to the repository maintainer rather than disclosed with exploit details or secrets in public issues.
