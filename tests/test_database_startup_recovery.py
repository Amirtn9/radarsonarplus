import threading

import pytest


def test_database_retries_core_schema_after_transient_pool_failure(monkeypatch):
    psycopg2 = pytest.importorskip("psycopg2")
    import database

    created = {"count": 0}
    initialized = {"count": 0}

    class Cursor:
        def execute(self, *args, **kwargs):
            return None
        def close(self):
            return None

    class Connection:
        def cursor(self, **kwargs):
            return Cursor()
        def rollback(self):
            return None

    class Pool:
        def getconn(self):
            return Connection()
        def putconn(self, conn):
            return None
        def closeall(self):
            return None

    def factory(*args, **kwargs):
        created["count"] += 1
        if created["count"] == 1:
            raise psycopg2.OperationalError("PostgreSQL is still starting")
        return Pool()

    monkeypatch.setattr(database.psycopg2.pool, "ThreadedConnectionPool", factory)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    db = object.__new__(database.Database)
    db.db_config = {}
    db.lock = threading.RLock()
    db.pool = None
    db._pool_error = None
    db._schema_initialized = False
    db._schema_initializing = False
    db._initialized = True

    def initialize_schema():
        initialized["count"] += 1

    db.init_db = initialize_schema

    with db.get_connection() as (conn, cur):
        assert conn is not None
        assert cur is not None

    assert created["count"] == 2
    assert initialized["count"] == 1
    assert db._schema_initialized is True
