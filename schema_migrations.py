"""Serialized, forward-only PostgreSQL schema migrations for Bot and Web.

Core schema version 1 is applied by Database.init_db(). This registry owns all
additive application schemas. Both entry points use the same advisory lock and
ledger, so independent systemd services cannot race through DDL.

Migration versions are immutable once deployed. Add a new version for future
schema changes; do not edit an already-published migration. Rollback is done by
restoring a verified database + matching encryption-key backup, not by guessing
reverse ALTER statements.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
MIGRATION_LOCK_ID = 535300


def _registry():
    from agent_traffic import install_schema as agent_traffic_schema
    from enterprise_runtime import install_runtime_schema
    from enterprise_services import install_schema as enterprise_services_schema
    from monitor_fleet import install_job_schema, install_schema as monitor_fleet_schema
    from pro_store import install_schema as pro_store_schema
    from service_monitor import install_schema as service_monitor_schema
    from subscription_health import install_schema as subscription_health_schema
    from web_agent_jobs import install_schema as web_agent_jobs_schema
    from web_login import install_schema as web_login_schema, install_security_schema as web_security_schema
    from config_alerts import install_schema as config_alert_schema
    from log_management import install_schema as log_schema
    from log_management import install_reliability_schema as log_reliability_schema

    from control_store import install_schema as control_schema
    from billing import install_schema as billing_schema
    from quota_policy import install_schema as quota_schema
    from product_admin import install_schema as product_schema

    return (
        (2, "pro_store", pro_store_schema),
        (3, "enterprise_services", enterprise_services_schema),
        (4, "enterprise_runtime", install_runtime_schema),
        (5, "subscription_health", subscription_health_schema),
        (6, "web_login", web_login_schema),
        (7, "monitor_fleet", monitor_fleet_schema),
        (8, "monitor_fleet_jobs", install_job_schema),
        (9, "agent_traffic", agent_traffic_schema),
        (10, "service_monitor", service_monitor_schema),
        (11, "web_agent_jobs", web_agent_jobs_schema),
        (12, "config_alerts", config_alert_schema),
        (13, "log_management", log_schema),
        (14, "log_reliability", log_reliability_schema),
        (15, "web_login_security", web_security_schema),
        (16, "billing", billing_schema),
        (17, "independent_quotas", quota_schema),
        (18, "account_controls", control_schema),
        (19, "product_workflows", product_schema),
    )


def run_startup_migrations(db) -> list[str]:
    """Apply each migration once while holding a cross-process PostgreSQL lock.

    The lock is session-scoped because each existing schema installer owns its
    transaction/connection. It is held on one pooled connection while those
    installers use other connections, then released in ``finally``.
    """
    applied: list[str] = []
    with db.get_connection() as (lock_conn, lock_cur):
        lock_cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
        lock_conn.commit()
        try:
            lock_cur.execute("""CREATE TABLE IF NOT EXISTS sonar_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
            lock_conn.commit()

            for version, name, callback in _registry():
                lock_cur.execute(
                    "SELECT name FROM sonar_schema_migrations WHERE version=%s",
                    (version,),
                )
                existing = lock_cur.fetchone()
                if existing:
                    existing_name = existing["name"]
                    if existing_name != name:
                        raise RuntimeError(
                            f"Schema migration version {version} is already assigned to {existing_name!r}"
                        )
                    continue

                log.info("SCHEMA_MIGRATION_BEGIN version=%s name=%s", version, name)
                callback(db)
                lock_cur.execute(
                    "INSERT INTO sonar_schema_migrations(version,name) VALUES(%s,%s)",
                    (version, name),
                )
                lock_conn.commit()
                applied.append(name)
                log.info("SCHEMA_MIGRATION_OK version=%s name=%s", version, name)

            # Recovery is operational maintenance, not a one-time DDL migration.
            from web_agent_jobs import recover_interrupted_jobs
            recover_interrupted_jobs(db)
            lock_conn.commit()
        except Exception:
            lock_conn.rollback()
            log.exception("SCHEMA_MIGRATION_FAILED")
            raise
        finally:
            try:
                lock_cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
                lock_conn.commit()
            except Exception:
                log.exception("SCHEMA_MIGRATION_UNLOCK_FAILED")
                try:
                    lock_conn.rollback()
                except Exception:
                    pass
    return applied
