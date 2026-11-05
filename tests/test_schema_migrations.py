from contextlib import contextmanager
from types import SimpleNamespace
import sys

from schema_migrations import run_startup_migrations


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def execute(self, sql, params=None):
        self.db.sql.append((" ".join(str(sql).split()), params))
        normalized = " ".join(str(sql).split())
        if normalized.startswith("SELECT name FROM sonar_schema_migrations"):
            self.result = {"name": self.db.applied[params[0]]} if params[0] in self.db.applied else None
        elif normalized.startswith("INSERT INTO sonar_schema_migrations"):
            self.db.applied[params[0]] = params[1]
        elif "pg_advisory_lock" in normalized:
            self.db.locked = True
        elif "pg_advisory_unlock" in normalized:
            self.db.locked = False

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, db):
        self.cursor_obj = FakeCursor(db)

    def commit(self):
        pass

    def rollback(self):
        pass


class FakeDB:
    def __init__(self):
        self.applied = {1: "core_schema"}
        self.sql = []
        self.locked = False
        self.connection = FakeConnection(self)

    @contextmanager
    def get_connection(self):
        yield self.connection, self.connection.cursor_obj

    def execute(self, sql, params=None, **kwargs):
        self.sql.append((" ".join(str(sql).split()), params))
        return None


def install_modules(monkeypatch, events):
    def module(name, **functions):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**functions))

    for name, fn in (
        ("pro_store", "install_schema"),
        ("enterprise_services", "install_schema"),
        ("enterprise_runtime", "install_runtime_schema"),
        ("subscription_health", "install_schema"),
        ("web_login", "install_schema"),
        ("monitor_fleet", "install_schema"),
        ("agent_traffic", "install_schema"),
        ("service_monitor", "install_schema"),
        ("web_agent_jobs", "install_schema"),
        ("config_alerts", "install_schema"),
    ):
        module(name, **{fn: lambda db, name=name: events.append(name)})
    module("monitor_fleet", install_schema=lambda db: events.append("monitor_fleet"),
           install_job_schema=lambda db: events.append("monitor_fleet_jobs"))
    module("web_agent_jobs", install_schema=lambda db: events.append("web_agent_jobs"),
           recover_interrupted_jobs=lambda db: events.append("recover_jobs"))


def test_bot_and_web_share_one_versioned_migration_ledger(monkeypatch):
    events = []
    install_modules(monkeypatch, events)
    db = FakeDB()

    first = run_startup_migrations(db)
    second = run_startup_migrations(db)

    assert first == [
        "pro_store", "enterprise_services", "enterprise_runtime",
        "subscription_health", "web_login", "monitor_fleet",
        "monitor_fleet_jobs", "agent_traffic", "service_monitor", "web_agent_jobs", "config_alerts",
    ]
    assert second == []
    assert events.count("recover_jobs") == 2
    assert len([event for event in events if event != "recover_jobs"]) == 11
    assert db.applied[1] == "core_schema"
    assert len(db.applied) == 12
    assert db.locked is False
    assert any("pg_advisory_lock" in sql for sql, _ in db.sql)
    assert any("pg_advisory_unlock" in sql for sql, _ in db.sql)


def test_migration_version_name_collision_fails_closed(monkeypatch):
    events = []
    install_modules(monkeypatch, events)
    db = FakeDB()
    db.applied[2] = "wrong_migration"
    import pytest
    with pytest.raises(RuntimeError, match="already assigned"):
        run_startup_migrations(db)
    assert db.locked is False
