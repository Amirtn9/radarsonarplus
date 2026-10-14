# 🦇 Sonar Radar Pro 5.2.3 · Release Notes

## Fixed

1. Shared server callback action parsing and resource ownership inside SSH operation.
2. Fail-closed clean reset including backup verification and explicit return codes.
3. Service-account access to code and runtime import test (umask 077 deployment).
4. Mini App search input crash and Pro pagination kwargs forwarding.
5. Admin agent bulk-install ConversationHandler entry, cancellation, port validation and persisted WSS port.
6. Agent custom port, CLI Python path and WSS settings consistency.
7. Subscription owner SQL argument order, missing-subscription deletion response, and stable child IDs on sync.
8. Server monitoring persistence and per-owner CPU/RAM/disk alert thresholds and channel routing.
9. WebSocket cancellation disposal and canceled connection cleanup.

## Deployment

Source: https://github.com/Amirtn9/radarsonarplus

Use `install.sh`, upload all files in the ZIP to repository root, then run `--doctor`.
The clean reset option permanently deletes the Sonar database after validated backup and explicit confirmation.

## Verification

Automated offline tests are provided under `tests/`. Production Ubuntu, Telegram and PostgreSQL integration testing remains outstanding.
