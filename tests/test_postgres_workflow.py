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


def test_database_log_windows_purge_rollback_and_operational_data(isolated_database):
    from datetime import datetime, timedelta, timezone
    from log_management import LogStore
    from pro_store import ProStore
    from schema_migrations import run_startup_migrations
    db, _ = isolated_database
    run_startup_migrations(db)
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'preserve admin')")
    db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(901,'preserve node','127.0.0.1')")
    logs = LogStore(db, 901)
    now = datetime.now(timezone.utc)
    rows = [(now-timedelta(hours=hours), 'ERROR' if hours==30 else 'INFO', 'node',
             'test', f'age-{hours}', 'recorded') for hours in (1,30,100,200)]
    logs.write(rows)
    for hours, expected in (('24',1),('48',2),('168',3),('all',4)):
        assert logs.list(901, hours)['total'] == expected
    assert logs.list(901,'all',level='ERROR')['total'] == 1
    assert logs.list(901,'custom',now-timedelta(hours=40),now-timedelta(hours=2))['total'] == 1
    assert len(logs.list(901,'all',page=2,per_page=2)['items']) == 2
    pro = ProStore(db)
    pro.audit(901,901,'test',detail='password=never-persist-this')
    assert 'never-persist-this' not in db.execute('SELECT detail FROM pro_audit',fetch='one')['detail']
    # Inject a real database failure after application-log deletion has run.
    db.execute('''CREATE FUNCTION reject_log_purge() RETURNS trigger LANGUAGE plpgsql
        AS $$ BEGIN RAISE EXCEPTION 'injected purge failure'; END $$''')
    db.execute('''CREATE TRIGGER reject_log_purge BEFORE DELETE ON pro_audit
        FOR EACH ROW EXECUTE FUNCTION reject_log_purge()''')
    with pytest.raises(Exception, match='injected purge failure'):
        logs.clear(901,'DELETE ALL LOGS')
    assert logs.list(901,'all')['total'] == 5
    db.execute('DROP TRIGGER reject_log_purge ON pro_audit')
    assert logs.clear(901,'DELETE ALL LOGS')['ok']
    assert logs.list(901,'all')['total'] == 0
    # The writer can receive old queued batches after the purge completes.
    logs.write(rows)
    assert logs.list(901,'all')['total'] == 0
    fresh = datetime.now(timezone.utc)
    logs.write([(fresh,'INFO','system','test','new event','recorded')])
    assert logs.list(901,'all')['total'] == 1
    assert db.execute('SELECT full_name FROM users WHERE user_id=901',fetch='one')['full_name'] == 'preserve admin'
    assert db.execute('SELECT name FROM servers WHERE owner_id=901',fetch='one')['name'] == 'preserve node'


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
    # Router factories capture their service stores at import time. A prior
    # API unit test may already have imported the portal with a different DB.
    if 'pro_portal' in sys.modules:
        import importlib
        importlib.reload(sys.modules['pro_portal'])
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
    assert db.execute('SELECT COUNT(*) AS n FROM sonar_schema_migrations', fetch='one')['n'] == 19


def test_upgrade_from_695_schema_and_failed_migration_preserve_credentials(isolated_database,monkeypatch,tmp_path):
    from contextlib import contextmanager
    import schema_migrations,log_management
    from secret_crypto import Security
    db,_=isolated_database
    registry=schema_migrations._registry()
    # 6.9.5 ends at migration 13; create that real predecessor schema first.
    with monkeypatch.context() as previous:
        previous.setattr(schema_migrations,'_registry',lambda:tuple(item for item in registry if item[0]<=13))
        schema_migrations.run_startup_migrations(db)
    security=Security(tmp_path/'upgrade-key.key')
    credential=security.encrypt('synthetic-preserved-password')
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'upgrade owner')")
    server=db.execute("INSERT INTO servers(owner_id,name,ip,password) VALUES(901,'upgrade server','192.0.2.1',%s) RETURNING id",(credential,),fetch='one')['id']
    db.execute("INSERT INTO settings(owner_id,key,value) VALUES(901,'upgrade_fixture','preserve setting')")
    db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(901,'single','upgrade config','vless://synthetic-upgrade-fixture')")
    db.execute("INSERT INTO pro_application_logs(created_at,level,category,source,message) VALUES(NOW(),'INFO','system','fixture','legacy record')")
    installer=log_management.install_reliability_schema
    class FailingCursor:
        def __init__(self,cur):self.cur=cur
        def execute(self,sql,*args,**kwargs):
            if sql.startswith('CREATE UNIQUE INDEX IF NOT EXISTS pro_application_logs_event'):
                # A real PostgreSQL error after ALTER must roll back that ALTER.
                return self.cur.execute('SELECT 1/0')
            return self.cur.execute(sql,*args,**kwargs)
        def __getattr__(self,name):return getattr(self.cur,name)
    class FailingDatabase:
        @contextmanager
        def get_connection(self):
            with db.get_connection() as (conn,cur):yield conn,FailingCursor(cur)
    with monkeypatch.context() as failure:
        failure.setattr(log_management,'install_reliability_schema',lambda unused:installer(FailingDatabase()))
        import psycopg2
        with pytest.raises(psycopg2.errors.DivisionByZero):schema_migrations.run_startup_migrations(db)
    assert db.execute("SELECT COUNT(*) n FROM information_schema.columns WHERE table_name='pro_application_logs' AND column_name='event_id'",fetch='one')['n']==0
    assert db.execute('SELECT COUNT(*) n FROM sonar_schema_migrations WHERE version=14',fetch='one')['n']==0
    assert schema_migrations.run_startup_migrations(db)==['log_reliability','web_login_security','billing','independent_quotas','account_controls','product_workflows']
    assert db.execute('SELECT full_name FROM users WHERE user_id=901',fetch='one')['full_name']=='upgrade owner'
    saved=db.execute('SELECT password FROM servers WHERE id=%s',(server,),fetch='one')['password']
    assert saved==credential and security.decrypt(saved)=='synthetic-preserved-password'
    assert db.execute("SELECT value FROM settings WHERE owner_id=901 AND key='upgrade_fixture'",fetch='one')['value']=='preserve setting'
    assert db.execute("SELECT COUNT(*) n FROM tunnel_configs WHERE owner_id=901 AND name='upgrade config'",fetch='one')['n']==1
    assert db.execute("SELECT message FROM pro_application_logs WHERE source='fixture'",fetch='one')['message']=='legacy record'


def test_log_retry_deduplication_and_purge_spool(isolated_database):
    from datetime import datetime,timezone
    from schema_migrations import run_startup_migrations
    from log_management import LogStore
    from log_spool import LogSpool
    db,_=isolated_database;run_startup_migrations(db)
    logs=LogStore(db,901)
    row=(datetime.now(timezone.utc),'INFO','system','fixture','retry fixture','recorded',str(uuid.uuid4()))
    logs.write([row])
    # Simulate replay after a commit succeeded but the acknowledgement was lost.
    logs.write([row])
    assert logs.list(901,'all')['total']==1
    spool=LogSpool();spool.add([row])
    try:
        assert logs.clear(901,'DELETE ALL LOGS')['retry_spool']=='cleared'
        assert spool.count()==0
        logs.write([row])
        assert logs.list(901,'all')['total']==0
    finally:spool.close()


def test_multi_source_log_purge_job_reports_partial_and_preserves_data(isolated_database,monkeypatch):
    from datetime import datetime,timezone
    from schema_migrations import run_startup_migrations
    from log_management import LogStore
    import log_purge
    db,_=isolated_database;run_startup_migrations(db)
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'preserve owner')")
    server=db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(901,'preserve host','192.0.2.1') RETURNING id",fetch='one')['id']
    LogStore(db,901).write([(datetime.now(timezone.utc),'INFO','system','fixture','old event','recorded')])
    jobs=log_purge.PurgeJobs(db,901)
    with pytest.raises(PermissionError):jobs.create(902,'DELETE ALL LOGS',True)
    created=jobs.create(901,'DELETE ALL LOGS',True)
    assert jobs.create(901,'DELETE ALL LOGS',True)['id']==created['id']
    with pytest.raises(log_purge.PurgeScopeConflict):jobs.create(901,'DELETE ALL LOGS',False)
    with pytest.raises(PermissionError):jobs.read(902,created['id'])
    monkeypatch.setattr(log_purge,'request_local',lambda:{'sources':{
        'application_files':{'status':'cleared'},'installer_file':{'status':'cleared'},
        'namespace_journal':{'status':'cleared'},'shared_journal':{'status':'blocked_shared'}}})
    monkeypatch.setattr(log_purge,'request_remote',lambda server:{'ok':True,'sources':{'agent_files':{'status':'cleared'}}})
    jobs.run(jobs.claim())
    result=jobs.read(901,created['id'])
    assert result['state']=='partial'
    assert result['result']['sources']['shared_journal']['status']=='blocked_shared'
    assert result['result']['remote'][str(server)]['ok'] is True
    assert LogStore(db,901).list(901,'all')['total']==0
    assert db.execute('SELECT name FROM servers WHERE id=%s',(server,),fetch='one')['name']=='preserve host'
    assert db.execute('SELECT full_name FROM users WHERE user_id=901',fetch='one')['full_name']=='preserve owner'
    next_job=jobs.create(901,'DELETE ALL LOGS',False)
    jobs.claim();jobs.recover()
    assert jobs.read(901,next_job['id'])['state']=='interrupted'
    assert jobs.claim() is None


def test_log_purge_cookie_auth_requires_csrf_and_admin(isolated_database, monkeypatch):
    from fastapi.testclient import TestClient
    from schema_migrations import run_startup_migrations
    from web_login import create_session
    import pro_portal
    db, _ = isolated_database
    run_startup_migrations(db)
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'Admin'),(902,'Ordinary')")
    monkeypatch.setattr(pro_portal.store,'db',db)
    monkeypatch.setattr(pro_portal,'SUPER_ADMIN_ID',901)
    monkeypatch.setenv('SONAR_WEB_ORIGIN','https://testserver')
    client = TestClient(pro_portal.app,base_url='https://testserver')
    session, csrf = create_session(db,901)
    client.cookies.set('sonar_web_session',session)
    body = {'confirmation':'DELETE ALL LOGS'}
    assert client.request('DELETE','/api/admin/logs',json=body).status_code == 403
    headers = {'Origin':'https://testserver','X-CSRF-Token':csrf}
    assert client.request('DELETE','/api/admin/logs',headers=headers,json=body).status_code == 200
    session, csrf = create_session(db,902)
    client.cookies.set('sonar_web_session',session)
    headers['X-CSRF-Token'] = csrf
    assert client.request('DELETE','/api/admin/logs',headers=headers,json=body).status_code == 403


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
    import web_reporting
    async def idle_collector(db,stop):await stop.wait()
    monkeypatch.setattr(web_reporting,'observation_worker',idle_collector)
    import pro_portal
    monkeypatch.setattr(pro_portal.store, 'db', db)
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
                         'me', 'rules', 'services', 'reports/overview', 'enterprise/health', 'enterprise/operations',
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
        report=client.get('/api/reports/overview').json()
        assert [row['id'] for row in report['servers']]==[owned]
        exported=client.get('/api/reports/servers.csv')
        assert exported.status_code==200 and 'Owned server' in exported.text and 'Private other tenant' not in exported.text
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


def test_reporting_windows_exports_and_owner_boundary(isolated_database):
    from schema_migrations import run_startup_migrations
    from pro_store import ProStore
    from web_reporting import server_history
    db,_=isolated_database;run_startup_migrations(db)
    db.execute('INSERT INTO users(user_id) VALUES(901),(902)')
    sid=db.execute("INSERT INTO servers(owner_id,name) VALUES(901,'report') RETURNING id",fetch='one')['id']
    db.execute("INSERT INTO server_stats(server_id,cpu,ram,created_at) VALUES(%s,25,50,NOW()),(%s,60,75,NOW()-INTERVAL '36 hours')",(sid,sid))
    pro=ProStore(db)
    assert len(server_history(pro,901,sid,1)['samples'])==1
    assert len(server_history(pro,901,sid,48)['samples'])==2
    current=server_history(pro,901,sid,1)['latest']
    assert current['cpu']==25 and current['ram']==50
    assert current['time'].endswith('+00:00')
    # Two observations inside one bucket: the graph averages them, while the
    # current gauge must keep the exact newest percentage (including zero).
    db.execute('INSERT INTO server_stats(server_id,cpu,ram,disk,created_at) VALUES(%s,0,42.6,13.6,NOW()+INTERVAL \'1 second\')',(sid,))
    result=server_history(pro,901,sid,1)
    assert result['latest']['cpu']==0
    assert result['latest']['ram']==42.6 and result['latest']['disk']==13.6
    with pytest.raises(PermissionError):server_history(pro,902,sid,48)
    with pytest.raises(ValueError):server_history(pro,901,sid,2)


def test_web_login_retention_codes_and_success_reservations(isolated_database):
    from schema_migrations import run_startup_migrations
    from web_login import issue_code,redeem,allow_login_attempt,release_login_attempt
    db,_=isolated_database;run_startup_migrations(db)
    first,second=issue_code(db,901),issue_code(db,901)
    assert redeem(db,first)[0]==901 and redeem(db,second)[0]==901
    client='a'*64
    db.execute("INSERT INTO sonar_web_login_attempts(client_hash,attempts,window_started) VALUES(%s,4,NOW()-INTERVAL '2 days')",('b'*64,))
    for _ in range(15):
        assert allow_login_attempt(db,client)
        release_login_attempt(db,client)
    assert db.execute('SELECT attempts FROM sonar_web_login_attempts WHERE client_hash=%s',(client,),fetch='one')['attempts']==0
    assert not db.execute('SELECT client_hash FROM sonar_web_login_attempts WHERE client_hash=%s',('b'*64,),fetch='one')
    for _ in range(10):assert allow_login_attempt(db,client)
    assert not allow_login_attempt(db,client)
    # A timestamp near the old fixed-window boundary cannot reset the budget.
    db.execute("UPDATE sonar_web_login_attempts SET window_started=NOW()-INTERVAL '14 minutes' WHERE client_hash=%s",(client,))
    assert not allow_login_attempt(db,client)
    codes=[issue_code(db,902) for _ in range(5)]
    assert redeem(db,codes[0]) is None
    assert all(redeem(db,code)[0]==902 for code in codes[-3:])
    indexes={row['indexname'] for row in db.execute("SELECT indexname FROM pg_indexes WHERE tablename LIKE 'sonar_web_%'",fetch='all')}
    assert 'sonar_web_sessions_expiry_idx' in indexes and 'sonar_web_attempts_window_idx' in indexes


def test_bot_instance_lease_excludes_duplicate_token_and_releases(isolated_database):
    from runtime import bot_instance_lock
    db,_=isolated_database
    with bot_instance_lock(db,'synthetic-poller-token'):
        with pytest.raises(RuntimeError,match='BOT_INSTANCE_ALREADY_RUNNING'):
            with bot_instance_lock(db,'synthetic-poller-token'):pass
        with bot_instance_lock(db,'synthetic-other-token'):pass
    with bot_instance_lock(db,'synthetic-poller-token'):pass


def test_csrf_read_is_stable_across_tabs_and_preserves_issued_proof(isolated_database, monkeypatch):
    from fastapi.testclient import TestClient
    from schema_migrations import run_startup_migrations
    from web_login import create_session, digest
    import pro_portal
    db, _ = isolated_database
    run_startup_migrations(db)
    monkeypatch.setattr(pro_portal.store, 'db', db)
    monkeypatch.setattr(db, 'check_access', lambda uid: (True, ''))
    monkeypatch.setattr(db, 'workspace_owner', lambda uid: uid)
    monkeypatch.setattr(pro_portal.store, 'set_preference', lambda *args: None)
    monkeypatch.setenv('SONAR_WEB_ORIGIN', 'https://testserver')
    session, issued = create_session(db, 901)
    client = TestClient(pro_portal.app, base_url='https://testserver')
    client.cookies.set('sonar_web_session', session)
    before = db.execute('SELECT csrf_hash FROM sonar_web_sessions WHERE token_hash=%s', (digest(session),), fetch='one')
    first = client.get('/api/web/csrf').json()['csrf']
    second = client.get('/api/web/csrf').json()['csrf']
    assert first == second and first != session
    after = db.execute('SELECT csrf_hash FROM sonar_web_sessions WHERE token_hash=%s', (digest(session),), fetch='one')
    assert after == before
    for proof in (issued, first, second):
        response = client.post('/api/preferences', json={'key': 'daily_digest', 'value': True},
                               headers={'Origin': 'https://testserver', 'X-CSRF-Token': proof})
        assert response.status_code == 200, response.text
    for proof in ('', 'x' * 64, session):
        assert client.post('/api/preferences', json={'key': 'daily_digest', 'value': True},
                           headers={'Origin': 'https://testserver', 'X-CSRF-Token': proof}).status_code == 403
    assert client.post('/api/web/logout', headers={'Origin': 'https://testserver', 'X-CSRF-Token': first}).status_code == 200
    assert client.get('/api/web/csrf').status_code == 401


def test_statistics_tehran_labels_do_not_depend_on_database_timezone(isolated_database, monkeypatch):
    from datetime import datetime, timezone
    db, _ = isolated_database
    sid = db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(901,'timezone test','192.0.2.1') RETURNING id", fetch='one')['id']
    moment = datetime.now(timezone.utc).replace(minute=15, second=0, microsecond=0)
    db.execute('INSERT INTO server_stats(server_id,cpu,created_at) VALUES(%s,20,%s)', (sid, moment.replace(tzinfo=None)))
    def in_timezone(zone):
        def execute(query, params, fetch):
            with db.get_connection() as (_, cur):
                cur.execute("SELECT set_config('TimeZone',%s,true)", (zone,))
                cur.execute(query, params)
                return cur.fetchall()
        monkeypatch.setattr(db, 'execute', execute)
        return db.get_server_stats(sid)[0]['time_str']
    from zoneinfo import ZoneInfo
    expected = moment.astimezone(ZoneInfo('Asia/Tehran')).strftime('%H:%M')
    assert in_timezone('UTC') == expected
    assert in_timezone('America/New_York') == expected
