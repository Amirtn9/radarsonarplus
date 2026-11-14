"""Permissions, session expiry, regional semantics and limited hosting integration."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import psycopg2
from test_billing_workflow import service,plan
from test_postgres_workflow import isolated_database
from pro_store import ProStore
from control_store import Control
from workspace_service import ACTOR_ID,ACTIVE_WORKSPACE


def test_invitation_requires_recipient_consent_and_roles_are_enforced_in_sql(service):
    pro=ProStore(service.db);control=Control(pro)
    sid=service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(901,'owned','192.0.2.1') RETURNING id",fetch='one')['id']
    invite=control.invite(901,902,'viewer')
    assert pro.role(902,901) is None
    with pytest.raises(PermissionError):control.decide(903,invite,True)
    control.decide(902,invite,True)
    assert pro.role(902,901)=='viewer'
    actor=ACTOR_ID.set(902);workspace=ACTIVE_WORKSPACE.set(901)
    try:
        assert service.db.workspace_owner(902)==901
        assert pro.workspace(902)==901
        with pytest.raises(PermissionError):pro.require(902,901,True)
        with pytest.raises(psycopg2.errors.RaiseException,match='WORKSPACE_FORBIDDEN'):
            service.db.execute('UPDATE servers SET name=%s WHERE id=%s',('forged edit',sid))
    finally:ACTOR_ID.reset(actor);ACTIVE_WORKSPACE.reset(workspace)
    control.role(901,902,'server')
    actor=ACTOR_ID.set(902)
    try:
        service.db.execute('UPDATE servers SET name=%s WHERE id=%s',('permitted server edit',sid))
        with pytest.raises(psycopg2.errors.RaiseException,match='WORKSPACE_FORBIDDEN'):
            service.db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(901,'single','bad','forged-config')")
    finally:ACTOR_ID.reset(actor)
    control.role(901,902,'remove')
    assert pro.role(902,901) is None


def test_session_idle_expiry_revocation_and_elevation(service):
    from web_login import create_session,identify,digest
    from control_api import require_elevated
    session,_=create_session(service.db,902)
    assert identify(service.db,session)['user_id']==902
    assert require_elevated(service.db,session)
    service.db.execute("UPDATE sonar_web_sessions SET elevated_at=NOW()-INTERVAL '6 minutes' WHERE token_hash=%s",(digest(session),))
    assert not require_elevated(service.db,session)
    assert Control(ProStore(service.db)).sessions(902,session)[0]['current']
    service.db.execute("UPDATE sonar_web_sessions SET last_seen_at=NOW()-INTERVAL '31 minutes' WHERE token_hash=%s",(digest(session),))
    assert identify(service.db,session) is None
    new,_=create_session(service.db,902);Control(ProStore(service.db)).revoke(902,digest(new))
    assert identify(service.db,new) is None


def test_terminal_limit_is_cross_worker_and_closed_leases_release_capacity(service):
    import uuid
    control=Control(ProStore(service.db));ids=[str(uuid.uuid4()) for _ in range(4)]
    for ident in ids[:3]:control.lease(902,1,ident)
    with pytest.raises(ValueError):control.lease(902,1,ids[3])
    control.terminal_touch(ids[0],20,100,True)
    control.lease(902,1,ids[3])
    row=service.db.execute('SELECT bytes_in,bytes_out,ended_at FROM sonar_terminal_leases WHERE id=%s',(ids[0],),fetch='one')
    assert row['bytes_in']==20 and row['bytes_out']==100 and row['ended_at']


def test_typed_expiry_fails_closed_without_rewriting_original_text(service):
    service.db.execute("UPDATE users SET expiry_date='not-a-date' WHERE user_id=902")
    row=service.db.get_user(902)
    assert row['expiry_date']=='not-a-date' and row['subscription_date_invalid']
    assert not service.db.check_access(902)[0]
    service.db.execute("UPDATE users SET expiry_date=NULL WHERE user_id=902")
    assert service.db.get_user(902)['subscription_unlimited'] and service.db.check_access(902)[0]


def test_iran_and_foreign_results_are_independent_and_missing_is_unknown(service,monkeypatch):
    from service_monitor import ServiceStore
    from service_probes import ProbeResult
    from regional_monitor import check_regions,report
    import monitor_fleet,ws_client
    pro=ProStore(service.db);monitors=ServiceStore(pro)
    monitors.create(902,902,'site','http','https://example.com')
    item=monitors.claim()[0]
    nodes=[{'id':1,'location_type':'ir','ip':'192.0.2.1','ws_port':8080},{'id':2,'location_type':'ext','ip':'192.0.2.2','ws_port':8080}]
    monkeypatch.setattr(monitor_fleet,'available_relay_nodes',lambda *a,**k:nodes)
    monkeypatch.setattr(service.db,'get_agent_transport',lambda *args:('synthetic-token','synthetic-certificate'))
    calls=[]
    async def request(ip,*args,**kwargs):
        calls.append(ip);return {'ok':ip.endswith('.2'),'latency_ms':30,'detail':'HTTP 200' if ip.endswith('.2') else 'Connection timeout','diagnostics':{'http_status':200}}
    monkeypatch.setattr(ws_client.GLOBAL_WS_POOL,'request',request)
    result=asyncio.run(check_regions(service.db,item,ProbeResult(True,10,'HTTP 200')))
    assert not result.ok and calls==['192.0.2.1','192.0.2.2']
    nodes.clear();asyncio.run(check_regions(service.db,item,ProbeResult(True,10,'HTTP 200')))
    data=report(pro,902,item['id'])
    for region in ('ir','ext'):
        summary=next(r for r in data['summary'] if r['region']==region)
        assert summary['unknown']==1 and summary['measured']==1


def test_hosting_agent_quota_authentication_revocation_and_sensor_scope(service,monkeypatch):
    from fastapi.testclient import TestClient
    from core import sec
    import pro_portal
    ident=plan(service,0);service.checkout(902,ident,'wallet','hosting-plan1')
    monkeypatch.setattr(pro_portal.store,'db',service.db)
    monkeypatch.setenv('SONAR_WEB_ORIGIN','https://testserver')
    pro_portal.app.dependency_overrides[pro_portal.actor]=lambda:902
    client=TestClient(pro_portal.app,base_url='https://testserver')
    try:
        created=client.post('/api/hosting/agents',json={'name':'PHP only'})
        assert created.status_code==200,created.text
        aid=created.json()['id']
        assert client.post('/api/hosting/agents',json={'name':'overflow'}).status_code==409
        download=client.get(f'/api/hosting/agents/{aid}/download')
        assert download.status_code==200 and 'CURLOPT_SSL_VERIFYPEER=>true' in download.text
        assert 'shell_exec' not in download.text and 'memory_get_usage' in download.text
        token=sec.decrypt(service.db.execute('SELECT token_cipher FROM sonar_hosting_agents WHERE id=%s',(aid,),fetch='one')['token_cipher'])
        report={'php_version':'8.3.0','memory_bytes':1024,'disk_total_bytes':100000,'disk_free_bytes':50000,'load':None}
        assert client.post('/api/hosting/telemetry',json=report).status_code==401
        assert client.post('/api/hosting/telemetry',json=report,headers={'Authorization':'Bearer '+token}).status_code==200
        assert client.post('/api/hosting/telemetry',json=report,headers={'Authorization':'Bearer '+token}).status_code==429
        assert client.delete(f'/api/hosting/agents/{aid}').status_code==200
        assert client.post('/api/hosting/telemetry',json=report,headers={'Authorization':'Bearer '+token}).status_code==401
        assert service.db.execute('SELECT COUNT(*) n FROM sonar_hosting_history',fetch='one')['n']==1
    finally:pro_portal.app.dependency_overrides.clear()


def test_proxy_rate_budget_does_not_lock_out_a_live_code(service,monkeypatch):
    from fastapi.testclient import TestClient
    from web_login import issue_code
    import pro_portal
    monkeypatch.setattr(pro_portal.store,'db',service.db)
    monkeypatch.setenv('SONAR_BOT_TOKEN','synthetic-token-for-proxy-limit')
    monkeypatch.setenv('SONAR_WEB_ORIGIN','https://testserver')
    client=TestClient(pro_portal.app,base_url='https://testserver')
    headers={'Origin':'https://testserver'}
    for _ in range(11):client.post('/api/web/login',json={'code':'x'*32},headers=headers)
    assert client.post('/api/web/login',json={'code':'y'*32},headers=headers).status_code==429
    code=issue_code(service.db,902)
    assert client.post('/api/web/login',json={'code':code},headers=headers).status_code==200


def test_sample_lease_excludes_parallel_observation_workers(service):
    from notification_api import claim_sample
    sid=service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(901,'sample','192.0.2.1') RETURNING id",fetch='one')['id']
    assert claim_sample(service.db,sid) and not claim_sample(service.db,sid)


def test_failed_startup_migration_prevents_lifespan_from_serving(monkeypatch):
    from fastapi.testclient import TestClient
    import schema_migrations,pro_portal
    def broken(db):raise RuntimeError('migration failed deliberately')
    monkeypatch.setattr(schema_migrations,'run_startup_migrations',broken)
    with pytest.raises(RuntimeError,match='migration failed deliberately'):
        with TestClient(pro_portal.app):pass


def test_plan_feature_is_enforced_on_server_not_just_hidden_in_ui(service,monkeypatch):
    from fastapi.testclient import TestClient
    from web_login import create_session
    import pro_portal
    ident=plan(service,0)
    service.db.execute('UPDATE sonar_plans SET features=%s WHERE id=%s',(__import__('psycopg2.extras',fromlist=['Json']).Json({'advanced_reports':False}),ident))
    service.checkout(902,ident,'wallet','feature-disabled-01')
    monkeypatch.setattr(pro_portal.store,'db',service.db)
    session,_=create_session(service.db,902)
    client=TestClient(pro_portal.app,base_url='https://testserver');client.cookies.set('sonar_web_session',session)
    assert client.get('/api/summary').status_code==200
    assert client.get('/api/reports/snapshot.json').status_code==403


def test_monitor_policy_applies_to_the_next_real_sample_claim(service):
    from notification_api import claim_sample
    sid=service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'interval','192.0.2.1') RETURNING id",fetch='one')['id']
    service.db.set_setting(902,'observation_interval_seconds','600')
    assert claim_sample(service.db,sid)
    remaining=service.db.execute('SELECT EXTRACT(EPOCH FROM next_observation_at-NOW()) seconds FROM servers WHERE id=%s',(sid,),fetch='one')['seconds']
    assert 595<=remaining<=600
    assert not claim_sample(service.db,sid)


def test_user_update_processor_preserves_conversation_order_without_blocking_other_users():
    from update_processor import OrderedUserProcessor
    async def scenario():
        processor=OrderedUserProcessor(4);started=asyncio.Event();release=asyncio.Event();other=asyncio.Event();order=[]
        async def first():
            order.append('first.start');started.set();await release.wait();order.append('first.end')
        async def second():order.append('second')
        async def independent():order.append('other');other.set()
        one=SimpleNamespace(effective_user=SimpleNamespace(id=1));two=SimpleNamespace(effective_user=SimpleNamespace(id=2))
        a=asyncio.create_task(processor.process_update(one,first()));await started.wait()
        b=asyncio.create_task(processor.process_update(one,second()));c=asyncio.create_task(processor.process_update(two,independent()))
        await asyncio.wait_for(other.wait(),1);assert 'second' not in order
        release.set();await asyncio.gather(a,b,c)
        assert order.index('first.end')<order.index('second') and not processor._users
    asyncio.run(scenario())


def test_queued_config_probe_is_idempotent_and_records_only_measured_results(service,monkeypatch):
    from config_probe_jobs import enqueue,claim,execute
    from tunnel_logic import tunnel_manager
    import monitor_fleet
    cid=service.db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(902,'single','probe','vless://synthetic') RETURNING id",fetch='one')['id']
    pro=ProStore(service.db)
    first=enqueue(pro,902,[cid],'queued-probe-0001');again=enqueue(pro,902,[cid],'queued-probe-0001')
    assert first[0]['id']==again[0]['id']
    monkeypatch.setattr(monitor_fleet,'select_monitor_row',lambda *a:{'id':1,'location_type':'ir'})
    monkeypatch.setattr(tunnel_manager,'_test_via_ws',AsyncMock(return_value=(True,{'status':'OK','ping':31.5,'jitter':2})))
    jobs=claim(service.db);assert len(jobs)==1
    asyncio.run(execute(pro,jobs[0]))
    assert service.db.execute('SELECT state FROM sonar_config_probes WHERE id=%s',(jobs[0]['id'],),fetch='one')['state']=='succeeded'
    assert service.db.execute('SELECT COUNT(*) n FROM pro_metrics WHERE config_id=%s',(cid,),fetch='one')['n']==1
    assert not claim(service.db)
    enqueue(pro,902,[cid],'queued-probe-0002');jobs=claim(service.db)
    monkeypatch.setattr(tunnel_manager,'_test_via_ws',AsyncMock(return_value=(False,{'error':'synthetic disconnected agent'})))
    asyncio.run(execute(pro,jobs[0]))
    assert service.db.execute('SELECT error_code FROM sonar_config_probes WHERE id=%s',(jobs[0]['id'],),fetch='one')['error_code']=='PROBE_UNAVAILABLE'
    assert service.db.execute('SELECT COUNT(*) n FROM pro_metrics WHERE config_id=%s',(cid,),fetch='one')['n']==1


def test_changed_config_cannot_receive_stale_inflight_test_result(service,monkeypatch):
    from config_probe_jobs import enqueue,claim,execute
    from tunnel_logic import tunnel_manager
    import monitor_fleet
    cid=service.db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(902,'single','revision','vless://revision') RETURNING id",fetch='one')['id']
    pro=ProStore(service.db);enqueue(pro,902,[cid],'revision-probe01');job=claim(service.db)[0]
    monkeypatch.setattr(monitor_fleet,'select_monitor_row',lambda *a:{'id':1,'location_type':'ir'})
    async def change(*args,**kwargs):
        service.db.execute("UPDATE tunnel_configs SET link=link||'-changed' WHERE id=%s",(cid,))
        return True,{'status':'OK','ping':25,'jitter':1}
    monkeypatch.setattr(tunnel_manager,'_test_via_ws',change)
    asyncio.run(execute(pro,job))
    assert service.db.execute('SELECT error_code FROM sonar_config_probes WHERE id=%s',(job['id'],),fetch='one')['error_code']=='CONFIG_CHANGED_DURING_PROBE'
    assert service.db.execute('SELECT COUNT(*) n FROM pro_metrics WHERE config_id=%s',(cid,),fetch='one')['n']==0


def test_custom_report_period_has_real_counts_and_validates_timezone(service):
    from datetime import datetime,timedelta,timezone
    from web_reporting import analytic_window
    sid=service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'report','192.0.2.1') RETURNING id",fetch='one')['id']
    service.db.execute('INSERT INTO server_stats(server_id,cpu,ram,disk) VALUES(%s,20,30,40)',(sid,))
    now=datetime.now(timezone.utc)
    data=analytic_window(ProStore(service.db),902,now-timedelta(hours=24),now+timedelta(seconds=1))
    assert data['servers']['samples']==1 and data['servers']['cpu_average']==20
    assert data['configs']['samples']==0 and data['configs']['average_ms'] is None
    with pytest.raises(ValueError):analytic_window(ProStore(service.db),902,now.replace(tzinfo=None),now)
    with pytest.raises(ValueError):analytic_window(ProStore(service.db),902,now-timedelta(days=32),now)


def test_plan_retention_removes_old_metrics_but_keeps_financial_records(service):
    from retention import sweep
    ident=plan(service,0);service.checkout(902,ident,'wallet','retention-plan01')
    service.db.execute("UPDATE sonar_subscriptions SET snapshot=jsonb_set(snapshot,'{policy,retention_days}','1') WHERE user_id=902")
    sid=service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'retention','192.0.2.1') RETURNING id",fetch='one')['id']
    service.db.execute("INSERT INTO server_stats(server_id,cpu,created_at) VALUES(%s,10,NOW()-INTERVAL '2 days'),(%s,20,NOW())",(sid,sid))
    assert sweep(service.db)>=1
    assert service.db.execute('SELECT COUNT(*) n FROM server_stats WHERE server_id=%s',(sid,),fetch='one')['n']==1
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_orders WHERE user_id=902',fetch='one')['n']==1
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_billing_audit WHERE user_id=902',fetch='one')['n']>0


def test_server_and_website_intervals_respect_plan_minimum(service):
    from service_monitor import ServiceStore
    from notification_api import claim_sample
    ident=plan(service,0);service.checkout(902,ident,'wallet','minimum-plan-01')
    service.db.execute("UPDATE sonar_subscriptions SET snapshot=jsonb_set(snapshot,'{policy,min_interval_seconds}','300') WHERE user_id=902")
    pro=ProStore(service.db)
    with pytest.raises(psycopg2.errors.RaiseException,match='PLAN_INTERVAL_TOO_SHORT'):
        ServiceStore(pro).create(902,902,'Too frequent','http','https://example.com',interval=60)
    assert ServiceStore(pro).create(902,902,'Allowed','http','https://example.com',interval=300)
    sid=service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'minimum','192.0.2.1') RETURNING id",fetch='one')['id']
    service.db.set_setting(902,'observation_interval_seconds','60')
    assert claim_sample(service.db,sid)
    remaining=service.db.execute('SELECT EXTRACT(EPOCH FROM next_observation_at-NOW()) seconds FROM servers WHERE id=%s',(sid,),fetch='one')['seconds']
    assert 295<=remaining<=300


def test_shared_viewers_cannot_invoke_mutating_bot_callbacks():
    from secure_handlers import team_callback_allowed
    assert team_callback_allowed('viewer','detail_10','server_detail','bot_logic_mod.servers')
    assert not team_callback_allowed('viewer','act_reboot_10','server_actions','bot_logic_mod.servers')
    assert not team_callback_allowed('viewer','pro_toggle_multi_probe','toggle_pref','pro_handlers')
    assert not team_callback_allowed('server','act_reboot_10','server_actions','bot_logic_mod.servers')
    assert team_callback_allowed('server','act_editexpiry_10','server_actions','bot_logic_mod.servers')
    assert team_callback_allowed('technical','act_reboot_10','server_actions','bot_logic_mod.servers')
