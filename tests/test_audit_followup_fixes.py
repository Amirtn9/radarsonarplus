import base64
import hashlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import enterprise_runtime as runtime
from enterprise_services import EnterpriseStore


class _HealthDB:
    def __init__(self, row, policy=None):
        self.row = row
        self.policy = policy or {
            "weights": {"agent": 50, "cpu": 20, "ram": 20, "disk": 10},
            "thresholds": {"cpu": 100, "ram": 100, "disk": 100},
            "stale_seconds": 180,
        }

    def execute(self, query, params=(), fetch=None):
        if "FROM rs_health_policies" in query:
            return self.policy if fetch == "one" else [self.policy]
        if "FROM servers WHERE owner_id" in query:
            return [self.row]
        raise AssertionError(query)


class _HealthPro:
    def __init__(self, db):
        self.db = db

    def workspace(self, actor, owner=None):
        return int(owner or actor)

    def require(self, *args, **kwargs):
        return "owner"

    def audit(self, *args, **kwargs):
        return None


def _healthy_row(**overrides):
    row = {
        "id": 7,
        "name": "edge-7",
        "last_status": "Online",
        "last_agent_status": "Online",
        "last_cpu": 50,
        "last_ram": 10,
        "last_disk": 20,
        "last_checked_at": None,
        "agent_last_seen": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    }
    row.update(overrides)
    return row


def test_health_accepts_legacy_text_agent_last_seen():
    service = EnterpriseStore(_HealthPro(_HealthDB(_healthy_row())))
    result = service.health(100)
    assert result["servers"][0]["state"] == "healthy"
    assert result["servers"][0]["last_checked_at"].endswith("+00:00")


def test_health_does_not_score_missing_metrics_as_zero_load():
    service = EnterpriseStore(_HealthPro(_HealthDB(_healthy_row(last_cpu=None))))
    result = service.health(100)
    server = result["servers"][0]
    assert server["state"] == "unknown"
    assert server["score"] is None
    assert "cpu" in server["missing_metrics"]


def test_health_treats_invalid_timestamp_as_unknown():
    service = EnterpriseStore(_HealthPro(_HealthDB(_healthy_row(agent_last_seen="not-a-date"))))
    result = service.health(100)
    assert result["servers"][0]["state"] == "unknown"
    assert result["servers"][0]["stale"] is True


class _ReleaseDB:
    def __init__(self, existing):
        self.existing = existing
        self.insert_sql = ""

    def execute(self, query, params=(), fetch=None):
        if query.lstrip().startswith("INSERT INTO rs_agent_releases"):
            self.insert_sql = query
            return None
        if "SELECT id,sha256,signature,artifact FROM rs_agent_releases" in query:
            return self.existing
        raise AssertionError(query)


class _ReleasePro:
    def __init__(self, db):
        self.db = db

    def require(self, *args, **kwargs):
        return "owner"

    def audit(self, *args, **kwargs):
        return None


def test_agent_release_registration_is_idempotent_but_immutable(monkeypatch):
    artifact = b"AGENT_VERSION = '7.0.0'\n"
    sha = hashlib.sha256(artifact).hexdigest()
    signature = base64.b64encode(b"signed-artifact").decode()
    monkeypatch.setattr(runtime, "verify_agent_release_bytes", lambda *args: "verified")
    db = _ReleaseDB({"id": 19, "sha256": sha, "signature": signature, "artifact": artifact})
    store = EnterpriseStore(_ReleasePro(db))
    result = store.create_agent_release(100, 100, "7.0.0", sha,
        base64.b64encode(artifact).decode(), signature)
    assert result["id"] == 19
    assert "DO NOTHING" in db.insert_sql
    assert "DO UPDATE" not in db.insert_sql


def test_agent_release_rejects_changed_artifact_under_same_version(monkeypatch):
    original = b"AGENT_VERSION = '7.0.0'\n"
    changed = b"AGENT_VERSION = '7.0.0'\n# changed\n"
    old_sha = hashlib.sha256(original).hexdigest()
    new_sha = hashlib.sha256(changed).hexdigest()
    signature = base64.b64encode(b"signature").decode()
    monkeypatch.setattr(runtime, "verify_agent_release_bytes", lambda *args: "verified")
    db = _ReleaseDB({"id": 19, "sha256": old_sha, "signature": signature, "artifact": original})
    store = EnterpriseStore(_ReleasePro(db))
    with pytest.raises(ValueError, match="RELEASE_VERSION_IMMUTABLE"):
        store.create_agent_release(100, 100, "7.0.0", new_sha,
            base64.b64encode(changed).decode(), signature)


class _ServiceDownDB:
    def __init__(self):
        self.statements = []

    def execute(self, query, params=(), fetch=None):
        self.statements.append((query, params, fetch))
        if "FROM pro_services" in query:
            return [{"id": 3, "name": "api.example", "last_detail": "HTTP 503"}]
        if "INSERT INTO pro_notification_outbox" in query:
            return None
        raise AssertionError(query)


def test_service_down_automation_uses_probe_table_and_enqueues_notification():
    db = _ServiceDownDB()
    count = runtime._process_service_down_rule(db, {
        "id": 4, "owner_id": 100, "event": "service_down", "action": "notify"
    })
    assert count == 1
    assert any("FROM pro_services" in query for query, _, _ in db.statements)
    insert = next(params for query, params, _ in db.statements if "INSERT INTO pro_notification_outbox" in query)
    assert "api.example" in insert[2]


def test_service_down_automation_does_not_restart_unmapped_server():
    db = _ServiceDownDB()
    count = runtime._process_service_down_rule(db, {
        "id": 4, "owner_id": 100, "event": "service_down", "action": "restart_managed_service"
    })
    assert count == 0
    assert not any("INSERT INTO pro_notification_outbox" in query for query, _, _ in db.statements)


def test_automatic_rollout_rollback_queues_previous_signed_release(monkeypatch):
    calls = {}

    class DB:
        def execute(self, query, params=(), fetch=None):
            calls.setdefault("sql", []).append((query, params, fetch))
            if "SELECT DISTINCT i.resource_id" in query:
                return [{"resource_id": 5}, {"resource_id": 8}, {"resource_id": 5}]
            if "RETURNING rollback_attempts" in query:
                return {"rollback_attempts": 1}
            return None

    class FakeProStore:
        def __init__(self, db):
            calls["db"] = db

    class FakeEnterpriseStore:
        def __init__(self, pro):
            self.pro = pro

        def create_operation(self, actor, owner, kind, ids, key, payload):
            calls["operation"] = (actor, owner, kind, ids, key, payload)
            return {"id": "rollback-operation-1"}

    monkeypatch.setitem(sys.modules, "pro_store", types.SimpleNamespace(ProStore=FakeProStore))
    monkeypatch.setitem(sys.modules, "enterprise_services", types.SimpleNamespace(EnterpriseStore=FakeEnterpriseStore))
    result = runtime._begin_automatic_rollout_rollback(DB(), {
        "id": 12, "owner_id": 100, "previous_release_id": 9
    }, "batch_failed")
    assert result["state"] == "rolling_back"
    assert calls["operation"][2:5] == ("agent_update", [5, 8], "rollout-rollback:12:attempt:1")
    assert calls["operation"][5]["release_id"] == 9
    assert any("state='rolling_back'" in query for query, _, _ in calls["sql"])


def test_installer_waits_for_real_bot_readiness_and_imports_as_service_user():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "install.sh").read_text(encoding="utf-8")
    bot = (root / "bot.py").read_text(encoding="utf-8")
    assert "BOT_IMPORT_PREFLIGHT_OK" in installer
    assert "wait_for_bot_ready 120" in installer
    assert "BOT_STARTUP_READINESS_TIMEOUT" in installer
    assert "BOT_STARTUP_READY" in bot
    assert "bot-ready" in bot


def test_service_down_restart_is_not_advertised_as_supported_action():
    class Pro:
        def __init__(self):
            self.db = object()

        def require(self, *args, **kwargs):
            return "owner"

    store = EnterpriseStore(Pro())
    with pytest.raises(ValueError, match="allowlisted"):
        store.create_automation_rule(100, 100, "service_down", "restart_managed_service")


def test_manual_rollout_rollback_can_retry_after_previous_rollback_failed():
    class DB:
        def __init__(self):
            self.sql = []

        def execute(self, query, params=(), fetch=None):
            self.sql.append((query, params, fetch))
            if "FROM rs_agent_rollouts WHERE id=%s AND owner_id=%s" in query:
                return {"id": 12, "state": "rollback_failed", "server_ids": [5, 8],
                        "batch_offset": 2, "active_operation_id": None,
                        "previous_release_id": 9, "rollback_attempts": 1}
            if "SELECT DISTINCT i.resource_id" in query:
                return [{"resource_id": 5}, {"resource_id": 8}]
            if "RETURNING rollback_attempts" in query:
                return {"rollback_attempts": 2}
            return None

    class Pro:
        def __init__(self, db):
            self.db = db

        def require(self, *args, **kwargs):
            return "owner"

        def audit(self, *args, **kwargs):
            return None

    db = DB()
    result = EnterpriseStore(Pro(db)).control_agent_rollout(100, 100, 12, "rollback")
    assert result == {"id": 12, "action": "rollback"}
    reservation = next(query for query, _, _ in db.sql if "RETURNING rollback_attempts" in query)
    assert "state='rollback_pending'" in reservation
    assert "rollback_attempts=rollback_attempts+1" in reservation


def test_installer_can_resume_after_first_deploy_rolled_back_without_wiping_database():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "install.sh").read_text(encoding="utf-8")
    selection = installer.split("select_install_strategy() {", 1)[1].split("valid_port() {", 1)[0]
    assert "Resume installation and PRESERVE existing database/runtime data" in selection
    assert "Application missing" not in selection
    assert "Resuming fresh deployment" in selection


def test_rollout_worker_recovers_reserved_rollback_after_process_restart():
    source = (Path(__file__).resolve().parents[1] / "enterprise_runtime.py").read_text(encoding="utf-8")
    assert "'rollback_pending'" in source
    assert "await asyncio.to_thread(_resume_rollout_rollback,db,rollout,'reserved_rollback')" in source
    assert "rollback_attempts INTEGER NOT NULL DEFAULT 0" in source


def test_service_monitor_schema_is_installed_by_shared_versioned_migration_runner():
    root = Path(__file__).resolve().parents[1]
    bot = (root / "bot.py").read_text(encoding="utf-8")
    portal = (root / "pro_portal.py").read_text(encoding="utf-8")
    migrations = (root / "schema_migrations.py").read_text(encoding="utf-8")
    assert "from schema_migrations import run_startup_migrations" in bot
    assert "from schema_migrations import run_startup_migrations" in portal
    assert '(10, "service_monitor", service_monitor_schema)' in migrations
    assert "pg_advisory_lock" in migrations and "pg_advisory_unlock" in migrations


def test_runtime_migration_disables_legacy_unmapped_service_restart_rules():
    source = (Path(__file__).resolve().parents[1] / "enterprise_runtime.py").read_text(encoding="utf-8")
    assert "UPDATE rs_automation_rules SET enabled=FALSE" in source
    assert "event='service_down' AND action='restart_managed_service'" in source


def test_automation_api_rejects_unsupported_service_restart_pair():
    from pydantic import ValidationError
    from enterprise_api import AutomationRule

    with pytest.raises(ValidationError):
        AutomationRule(event="service_down", action="restart_managed_service")
    with pytest.raises(ValidationError):
        AutomationRule(event="disk_pressure", action="restart_managed_service")
    assert AutomationRule(event="agent_unreachable", action="restart_managed_service").action == "restart_managed_service"


def test_recovery_worker_keeps_automation_run_status_in_sync_with_operation():
    source = (Path(__file__).resolve().parents[1] / "enterprise_runtime.py").read_text(encoding="utf-8")
    worker = source.split("async def recovery_worker", 1)[1].split("async def rollout_worker", 1)[0]
    assert "UPDATE rs_automation_runs r" in worker
    assert "SET status=o.status" in worker
    assert "await asyncio.to_thread(db.execute" in worker


def test_manual_rollback_waits_for_cancelled_batch_to_settle():
    class DB:
        def execute(self, query, params=(), fetch=None):
            if "FROM rs_agent_rollouts WHERE id=%s AND owner_id=%s" in query:
                return {"id": 12, "state": "cancelled", "server_ids": [5],
                        "batch_offset": 0, "active_operation_id": "op-1",
                        "previous_release_id": 9, "rollback_attempts": 0}
            if "FROM rs_operations WHERE id=%s AND owner_id=%s" in query:
                return {"status": "running"}
            raise AssertionError(query)

    class Pro:
        def __init__(self, db): self.db = db
        def require(self, *args, **kwargs): return "owner"
        def audit(self, *args, **kwargs): return None

    with pytest.raises(ValueError, match="active rollout batch has not settled"):
        EnterpriseStore(Pro(DB())).control_agent_rollout(100, 100, 12, "rollback")
