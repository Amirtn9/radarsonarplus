# Sonar Radar Pro 5.2.1-github — Release notes

This is a GitHub deployment integration release, based on the 5.2.0-security snapshot.

- Canonical public repository: `https://github.com/Amirtn9/radarsonarplus` (`main`).
- The downloaded standalone `install.sh` bootstraps the full repository, including when running `--update`.
- **Menu 01 / `--install`** provisions PostgreSQL, the Telegram bot and private Mini App web API together.
- Web API health is checked before a deployment is marked successful; upgrades restart the web service.
- The advanced logs/diagnostics and backup/rollback commands are retained.
- Active version labels and links were consolidated; older documentation moved to `docs/archive/`.
- Installer settings support updating the logging level and Mini App session age.

## Important caveats

The remote agent must still be provisioned and enrolled on each managed machine. Public access to
Telegram Mini App requires a domain, accessible DNS and a trusted TLS certificate; this is handled
separately via menu option 04 / `--domain` and `--tls`. Code rollback does **not** undo database
schema migrations. An offline test suite is not a substitute for full staging Ubuntu, Telegram,
PostgreSQL, and ACME integration tests. Audit legacy Telegram RBAC before deploying to a
multi-tenant production environment.

## Upload instruction

Upload the **contents** of the ZIP to the *root* of `Amirtn9/radarsonarplus` on branch `main`.
The file `install.sh` must be at repository root, not inside a nested directory.
