"""Opt-in real PostgreSQL tests; creates and drops only a new random test DB.

Run with SONAR_TEST_DB_DSN pointing at a dedicated PostgreSQL test administrator.
No existing application database is migrated or restored by these tests.
"""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

DSN = os.getenv('SONAR_TEST_DB_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='SONAR_TEST_DB_DSN is required for real PostgreSQL tests')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_database(monkeypatch, tmp_path):
    import psycopg2
    from psycopg2 import sql
    import database
    name = 'sonar_review_' + uuid.uuid4().hex
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    config = psycopg2.extensions.parse_dsn(DSN)
    config['dbname'] = name
    monkeypatch.setattr(database, 'DB_CONFIG', config)
    previous = database.Database._instance
    database.Database._instance = None
    db = database.Database()
    monkeypatch.setenv('SONAR_STATE_DIR', str(tmp_path))
    try:
        yield db, config
    finally:
        if db.pool:
            db.pool.closeall()
        database.Database._instance = previous
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        admin.close()


def test_parallel_startup_and_upgrade_preserve_existing_rows(isolated_database):
    db, config = isolated_database
    # Existing core installation with real, preserved application data.
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'preserve me')")
    env = os.environ.copy()
    for key in ('dbname', 'user', 'host', 'port', 'password'):
        env['SONAR_DB_' + {'dbname': 'NAME'}.get(key, key.upper())] = str(config.get(key, ''))
    command = [sys.executable, '-c',
               'from database import Database; from schema_migrations import run_startup_migrations; '
               'run_startup_migrations(Database())']
    children = [subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(2)]
    for child in children:
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, (out + err).decode()
    from schema_migrations import run_startup_migrations
    assert run_startup_migrations(db) == []
    assert db.execute('SELECT full_name FROM users WHERE user_id=901', fetch='one')['full_name'] == 'preserve me'
    assert db.execute('SELECT COUNT(*) AS n FROM sonar_schema_migrations', fetch='one')['n'] == 11


def test_encrypted_backup_restore_is_atomic(isolated_database, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from enterprise_runtime import create_encrypted_backup, restore_encrypted_backup
    db, config = isolated_database
    monkeypatch.setenv('SONAR_BACKUP_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('SONAR_BACKUP_DIR', str(tmp_path))
    db.execute('CREATE TABLE review_data(id INTEGER PRIMARY KEY, value TEXT)')
    db.execute("INSERT INTO review_data VALUES(1,'original')")
    create_encrypted_backup(config, 'snapshot.enc')
    db.execute("UPDATE review_data SET value='changed'")
    assert restore_encrypted_backup(config, 'snapshot.enc', confirm=True)['restored']
    assert db.execute('SELECT value FROM review_data', fetch='one')['value'] == 'original'
    # Restoring the snapshot would remove a table required by this new view.
    # A failed DROP must roll back earlier DROP/CREATE operations as well.
    db.execute('CREATE VIEW review_dependency AS SELECT * FROM review_data')
    db.execute("UPDATE review_data SET value='must survive failure'")
    with pytest.raises(RuntimeError, match='PG_RESTORE_FAILED'):
        restore_encrypted_backup(config, 'snapshot.enc', confirm=True)
    assert db.execute('SELECT value FROM review_dependency', fetch='one')['value'] == 'must survive failure'
    assert db.execute('SELECT COUNT(*) AS n FROM users', fetch='one')['n'] == 0


def test_web_miniapp_authentication_tenancy_and_pdf(isolated_database, monkeypatch):
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode
    from fastapi.testclient import TestClient
    from schema_migrations import run_startup_migrations
    from web_login import issue_code
    db, config = isolated_database
    run_startup_migrations(db)
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'Owner'),(902,'Other tenant')")
    from secret_crypto import Security
    security = Security(Path(os.environ['SONAR_STATE_DIR']) / 'secret.key')
    credential = security.encrypt('test-only-ssh-password')
    owned = db.execute("INSERT INTO servers(owner_id,name,ip,port,username,password) VALUES(901,'Owned server','192.0.2.1',22,'audit',%s) RETURNING id", (credential,), fetch='one')['id']
    other = db.execute("INSERT INTO servers(owner_id,name,ip,port,username,password) VALUES(902,'Private other tenant','192.0.2.2',22,'audit',%s) RETURNING id", (credential,), fetch='one')['id']
    monkeypatch.setenv('SONAR_BOT_TOKEN', '123456:local-test-token-never-used-remotely')
    monkeypatch.setenv('SONAR_WEB_ORIGIN', 'https://testserver')
    import pro_portal
    monkeypatch.setattr(pro_portal.store, 'db', db)
    pro_portal.get_bot_token.cache_clear()
    with TestClient(pro_portal.app, base_url='https://testserver') as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/readyz').status_code == 200
        with monkeypatch.context() as missing_token:
            missing_token.setattr(pro_portal, 'get_bot_token', lambda: '')
            readiness = client.get('/readyz')
            assert readiness.status_code == 503
            assert readiness.json() == {'ready': False, 'db': 'ok', 'auth': 'unconfigured'}
        assert client.get('/api/summary').status_code == 401
        code = issue_code(db, 901)
        headers = {'Origin': 'https://testserver'}
        login = client.post('/api/web/login', json={'code': code}, headers=headers)
        assert login.status_code == 200, login.text
        assert 'HttpOnly' in login.headers['set-cookie'] and 'Secure' in login.headers['set-cookie']
        assert client.post('/api/web/login', json={'code': code}, headers=headers).status_code == 401
        for endpoint in ('summary', 'resources/server', 'resources/config', 'subscriptions', 'rankings',
                         'incidents', 'notifications/delivery', 'audit', 'tags', 'team', 'preferences',
                         'me', 'rules', 'services', 'enterprise/health', 'enterprise/operations',
                         'enterprise/analytics', 'enterprise/health/policy', 'enterprise/backups/schedules',
                         'enterprise/tokens', 'enterprise/webhooks', 'enterprise/agent/rollouts'):
            response = client.get('/api/' + endpoint)
            assert response.status_code == 200, (endpoint, response.status_code, response.text)
        assert client.get('/api/summary?owner=902').status_code == 403
        details = client.get(f'/api/console/servers/{owned}')
        assert details.status_code == 200, details.text
        assert 'password' not in details.json() and credential not in details.text
        assert client.get(f'/api/console/servers/{other}').status_code == 404
        resources = client.get('/api/resources/server').text
        assert 'Owned server' in resources and 'Private other tenant' not in resources
        assert client.post('/api/web/logout', headers=headers).status_code == 403
        csrf_headers = {**headers, 'X-CSRF-Token': login.json()['csrf']}
        assert client.post('/api/web/logout', headers=csrf_headers).status_code == 200
        assert client.get('/api/summary').status_code == 401
        fields = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 901})}
        secret = hmac.new(b'WebAppData', os.environ['SONAR_BOT_TOKEN'].encode(), hashlib.sha256).digest()
        fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k,v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
        response = client.post('/api/web/miniapp-session', headers={**headers, 'Authorization': 'tma ' + urlencode(fields)})
        assert response.status_code == 200, response.text
        assert client.get('/api/me').status_code == 200
        from pro_pdf import generate_pdf
        pdf = generate_pdf(901)
        assert pdf.startswith(b'%PDF-') and len(pdf) > 1000
    # Closing a web event loop must not shut down the process-wide bot pool.
    from runtime import SHARED_EXECUTOR
    assert SHARED_EXECUTOR.submit(lambda: 42).result(timeout=5) == 42
