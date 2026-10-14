# Sonar Radar Pro 5.2.2-install-fix — Release notes

This is a deployment recovery and permissions fix built on the prior GitHub installer release.

- Canonical public repository: `https://github.com/Amirtn9/radarsonarplus` (`main`).
- The downloaded standalone `install.sh` bootstraps the full repository, including when running `--update`.
- **Menu 01 / `--install`** provisions PostgreSQL, the Telegram bot and private Mini App web API together.
- Web API health is checked before a deployment is marked successful; upgrades restart the web service.
- The advanced logs/diagnostics and backup/rollback commands are retained.
- Active version labels and links were consolidated; older documentation moved to `docs/archive/`.
- Installer settings support updating the logging level and Mini App session age.

## Changes for 5.2.2

- Resolve `203/EXEC` caused by Python venv directories created with `umask 077`: stage venv is made traversable, and the actual service user runs the interpreter and import smoke check before service start.
- Correct `StartLimitIntervalSec` and `StartLimitBurst` to systemd `[Unit]` sections.
- Install Mini App Python dependencies before promotion, rather than mutating a live venv after deploying code.
- Detect prior Sonar systemd units, env/config, application code, PostgreSQL role and database; offer a data-preserving upgrade or an explicitly authorized clean reset.
- A destructive reset requires exact `DELETE SONAR DATA` acknowledgement, verified archived backup and PostgreSQL availability; it NEVER wipes unrelated PostgreSQL databases.
- Animate actual installation subprocesses on an interactive terminal; non-interactive runs remain machine-readable and preserve real failure codes.
- Add user-level Python permission diagnostics, log improvements and regression tests.

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
