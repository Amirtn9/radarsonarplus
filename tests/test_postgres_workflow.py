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
    assert db.execute('SELECT COUNT(*) AS n FROM sonar_schema_migrations', fetch='one')['n'] == 12


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
        public_guide=client.get('/api/guide/public')
        assert public_guide.status_code==200
        assert all(not chapter['admin'] for chapter in public_guide.json()['chapters'])
        assert all(not chapter['admin'] for chapter in client.get('/api/guide?admin=true').json()['chapters'])
        with monkeypatch.context() as admin_role:
            admin_role.setattr(pro_portal,'SUPER_ADMIN_ID',901)
            response=client.get('/api/guide')
            assert any(chapter['admin'] for chapter in response.json()['chapters'])
            assert 'no-store' in response.headers['cache-control']
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
        with monkeypatch.context() as miniapp_origin:
            miniapp_origin.delenv('SONAR_WEB_ORIGIN',raising=False)
            assert client.post('/api/web/login',json={'code':issue_code(db,901)},headers=headers).status_code==403
            response = client.post('/api/web/miniapp-session', headers={**headers, 'Authorization': 'tma ' + urlencode(fields)})
        assert response.status_code == 200, response.text
        assert client.get('/api/me').status_code == 200
        # A Mini App may have its own HTTPS host, distinct from the website.
        with monkeypatch.context() as separate_website:
            separate_website.setenv('SONAR_WEB_ORIGIN','https://website-only.example')
            separate=client.post('/api/web/miniapp-session',headers={**headers,'Authorization':'tma '+urlencode(fields)})
            assert separate.status_code==200,separate.text
            assert client.post('/api/web/miniapp-session',headers={'Origin':'https://untrusted.example','Authorization':'tma '+urlencode(fields)}).status_code==403
            assert client.post('/api/config-alerts',json={'notify_score':True},headers={**headers,'X-CSRF-Token':separate.json()['csrf']}).status_code==200
            assert client.post('/api/web/login',json={'code':issue_code(db,901)},headers=headers).status_code==403
        from pro_pdf import generate_pdf
        pdf = generate_pdf(901)
        assert pdf.startswith(b'%PDF-') and len(pdf) > 1000
    # Closing a web event loop must not shut down the process-wide bot pool.
    from runtime import SHARED_EXECUTOR
    assert SHARED_EXECUTOR.submit(lambda: 42).result(timeout=5) == 42


def test_config_alert_transitions_and_delivery_controls(isolated_database,monkeypatch):
    from pro_store import ProStore
    from schema_migrations import run_startup_migrations
    from config_alerts import AlertSettings,save_settings,consume_metrics,delivery_policy,claim_scheduled,report_due
    db,config=isolated_database
    run_startup_migrations(db)
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'Alerts'),(902,'Other')")
    cid=db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(901,'single','WG egress','vless://private-test-link') RETURNING id",fetch='one')['id']
    other=db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(902,'single','Other','vless://other-private') RETURNING id",fetch='one')['id']
    pro=ProStore(db)
    values=AlertSettings(notify_score=True,scheduled=True,report_enabled=True).model_dump()
    save_settings(pro,901,901,values)
    with pytest.raises(PermissionError):save_settings(pro,902,901,values)
    with pytest.raises(PermissionError):save_settings(pro,901,901,dict(values,muted_config_ids=[other]))
    def metric(ok,ping=None,jitter=None):
        pro.record_metric(901,cid,'Iran test node',ok,ping,jitter)
        consume_metrics(db)
    metric(False)
    assert db.execute('SELECT COUNT(*) n FROM pro_notification_outbox',fetch='one')['n']==0
    metric(False);metric(False);metric(True,80,3);metric(True,80,3)
    events=db.execute('SELECT event_type,event_key,message FROM pro_notification_outbox ORDER BY id',fetch='all')
    assert [row['event_type'] for row in events]==['config_down','config_recovery']
    assert all('private-test-link' not in row['message'] for row in events)
    assert consume_metrics(db)==0
    # Exercise the actual persistent dispatcher with a synthetic Telegram sink.
    import asyncio
    from types import SimpleNamespace
    from service_jobs import flush_notifications
    messages=[]
    async def send_message(**kwargs):messages.append(kwargs)
    context=SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    asyncio.run(flush_notifications(context,pro))
    assert len(messages)==2
    assert all(message['chat_id']==901 for message in messages)
    # An accumulated quality drop produces one score alert, then respects cooldown.
    db.execute("UPDATE config_alert_state SET last_score_at=NOW()-INTERVAL '1 hour'")
    metric(True,1200,200);metric(True,80,1)
    assert db.execute("SELECT COUNT(*) n FROM pro_notification_outbox WHERE event_type='config_score'",fetch='one')['n']==1
    assert delivery_policy(db,901,'config_down',events[0]['event_key'])[0]
    save_settings(pro,901,901,dict(values,muted_config_ids=[cid]))
    assert not delivery_policy(db,901,'config_down',events[0]['event_key'])[0]
    save_settings(pro,901,901,dict(values,enabled=False))
    assert not delivery_policy(db,901,'config_recovery',events[1]['event_key'])[0]
    asyncio.run(flush_notifications(context,pro))
    assert len(messages)==2
    assert db.execute("SELECT skipped_at FROM pro_notification_outbox WHERE event_type='config_score'",fetch='one')['skipped_at'] is not None
    assert claim_scheduled(db)==[]
    from datetime import datetime,timezone
    import pro_jobs
    class FrozenDate:
        @staticmethod
        def now(zone):return datetime(2026,11,5,8,0,tzinfo=timezone.utc).astimezone(zone)
    save_settings(pro,901,901,dict(values,quiet_start=10,quiet_end=12))
    with monkeypatch.context() as clock:
        clock.setattr(pro_jobs,'datetime',FrozenDate)
        assert delivery_policy(db,901,'config_down',events[0]['event_key'])==(True,True)
    save_settings(pro,901,901,values)
    assert len(claim_scheduled(db))==1 and claim_scheduled(db)==[]
    report_due(db);report_due(db)
    assert db.execute("SELECT COUNT(*) n FROM pro_notification_outbox WHERE event_type='config_report'",fetch='one')['n']==1


def test_config_alert_failure_rolls_back_cursor_and_transition(isolated_database,monkeypatch):
    import config_alerts
    from pro_store import ProStore
    from schema_migrations import run_startup_migrations
    db,_=isolated_database
    run_startup_migrations(db)
    db.execute("INSERT INTO users(user_id) VALUES(901)")
    cid=db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(901,'single','Atomic','vless://atomic') RETURNING id",fetch='one')['id']
    pro=ProStore(db)
    pro.record_metric(901,cid,'default',False)
    config_alerts.consume_metrics(db)
    cursor=db.execute("SELECT last_id FROM pro_job_cursors WHERE name='config_alert_metrics'",fetch='one')['last_id']
    pro.record_metric(901,cid,'default',False)
    def unavailable(*args):raise RuntimeError('injected outbox write failure')
    with monkeypatch.context() as patch:
        patch.setattr(config_alerts,'_enqueue',unavailable)
        with pytest.raises(RuntimeError,match='injected'):config_alerts.consume_metrics(db)
    assert db.execute("SELECT last_id FROM pro_job_cursors WHERE name='config_alert_metrics'",fetch='one')['last_id']==cursor
    assert db.execute('SELECT failures FROM config_alert_state WHERE config_id=%s',(cid,),fetch='one')['failures']==1
    assert config_alerts.consume_metrics(db)==1
    assert db.execute('SELECT COUNT(*) n FROM pro_notification_outbox',fetch='one')['n']==1


def test_probe_snapshot_updates_counts_and_unknown_does_not_mark_down(isolated_database,monkeypatch):
    from schema_migrations import run_startup_migrations
    from pro_store import ProStore
    import pro_jobs
    db,_=isolated_database
    run_startup_migrations(db)
    db.execute('INSERT INTO users(user_id) VALUES(901)')
    cid=db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link,last_status) VALUES(901,'single','WG path','vless://test-only','Fail') RETURNING id",fetch='one')['id']
    pro=ProStore(db)
    monkeypatch.setattr(pro_jobs,'store',pro)
    pro_jobs._record(901,cid,'scheduled:1',{'status':'OK','ping':80,'jitter':3})
    assert pro.summary(901)['configs']['healthy']==1
    assert db.execute('SELECT COUNT(*) n FROM pro_metrics',fetch='one')['n']==1
    pro_jobs._record(902,cid,'scheduled:1',{'status':'Fail'})
    assert pro.summary(901)['configs']['healthy']==1
    assert db.execute('SELECT COUNT(*) n FROM pro_metrics',fetch='one')['n']==1
    pro_jobs._record(901,cid,'scheduled:1',{'status':'Unknown','msg':'XRAY_NOT_READY'})
    assert pro.summary(901)['configs']['healthy']==1
    assert db.execute('SELECT COUNT(*) n FROM pro_metrics',fetch='one')['n']==1
    pro_jobs._record(901,cid,'scheduled:1',{'status':'OK','ping':float('nan'),'jitter':0})
    assert db.execute('SELECT COUNT(*) n FROM pro_metrics',fetch='one')['n']==1
    pro_jobs._record(901,cid,'scheduled:1',{'status':'Fail'})
    assert pro.summary(901)['configs']['healthy']==0


def test_bot_alert_controls_and_guide_role_boundary(isolated_database,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from schema_migrations import run_startup_migrations
    from pro_store import ProStore
    import pro_handlers
    import config_alert_bot
    import v6_ui
    from config_alerts import settings
    db,_=isolated_database
    run_startup_migrations(db)
    db.execute('INSERT INTO users(user_id) VALUES(901),(902)')
    db.execute("INSERT INTO pro_team(owner_id,member_id,role) VALUES(901,902,'viewer')")
    monkeypatch.setattr(pro_handlers,'store',ProStore(db))
    monkeypatch.setattr(v6_ui,'SUPER_ADMIN_ID',901)
    edits=[];answers=[]
    async def edit_message_text(text,**kwargs):edits.append((text,kwargs))
    async def answer(*args,**kwargs):answers.append((args,kwargs))
    query=SimpleNamespace(data='ca_home',edit_message_text=edit_message_text,answer=answer)
    update=SimpleNamespace(effective_user=SimpleNamespace(id=901),callback_query=query)
    context=SimpleNamespace(user_data={'pro_workspace':901})
    async def run():
        await config_alert_bot.menu(update,context)
        assert 'مرکز اعلان کانفیگ' in edits[-1][0]
        query.data='ca_toggle_notify_score'
        await config_alert_bot.menu(update,context)
        assert settings(db,901)['notify_score'] is True
        update.effective_user.id=902
        await config_alert_bot.menu(update,context)
        assert settings(db,901)['notify_score'] is True
        before=len(edits)
        query.data='v6_lesson_admin-fleet_0'
        await v6_ui.guide_lesson(update,context)
        assert len(edits)==before and answers[-1][1]['show_alert'] is True
        query.data='v6_lesson_configs_0'
        await v6_ui.guide_lesson(update,context)
        assert 'کانفیگ' in edits[-1][0]
    asyncio.run(run())
