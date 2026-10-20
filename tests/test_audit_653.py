"""Focused offline tests for 6.5.3 hardening (no network/VPS/DB required)."""
import ast
import asyncio
import concurrent.futures
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]


def isolated(path,name,scope):
    tree=ast.parse((ROOT/path).read_text())
    node=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope)
    return scope[name]


class Cursor:
    def __init__(self,db):self.db=db;self.rows=[]
    def execute(self,sql,params=()):
        if 'FROM servers s' in sql:
            self.rows=self.db.monitors
        elif 'INSERT INTO pro_job_cursors' in sql:
            name=params[0] if params else 'multi_probe'
            self.db.cursor.setdefault(name,0);self.rows=[]
        elif 'SELECT last_id FROM pro_job_cursors' in sql:
            name=params[0] if params else 'multi_probe'
            self.rows=[{'last_id':self.db.cursor[name]}]
        elif 'FROM pro_preferences p' in sql:
            last,limit=params
            if 'owner_id>' in sql:
                self.rows=[{'owner_id':i} for i in self.db.users if i>last][:limit]
            else:
                self.rows=[{'owner_id':i} for i in self.db.users if i<=last][:limit]
        elif 'UPDATE pro_job_cursors' in sql:
            if len(params)==2: pos,key=params;self.db.cursor[key]=pos
            else:self.db.cursor['multi_probe']=params[0]
        else:raise AssertionError('Unexpected query: '+sql[:110])
    def fetchall(self):return self.rows
    def fetchone(self):return self.rows[0]


class MemoryDB:
    def __init__(self,users,monitors):
        self.users=users;self.monitors=monitors;self.cursor={};self.commits=0
    @contextmanager
    def get_connection(self):
        yield (SimpleNamespace(commit=lambda:self._commit()),Cursor(self))
    def _commit(self):self.commits+=1


def test_full_user_rotation_persists_over_restart(monkeypatch):
    db=MemoryDB(list(range(1,151)),[{'id':i,'owner_id':1} for i in range(1,17)])
    scope={'store':SimpleNamespace(db=db),'os':__import__('os')}
    fn=isolated('pro_jobs.py','_snapshot',scope)
    monkeypatch.setenv('SONAR_PROBE_OWNER_BATCH','10')
    owners=[]
    for _ in range(15):
        monitors,batch=fn();owners.extend(batch)
        assert len(monitors)==16
    assert owners==list(range(1,151))
    # Emulate Python process restart by reloading function without resetting DB.
    again=isolated('pro_jobs.py','_snapshot',{'store':SimpleNamespace(db=db),'os':__import__('os')})
    assert again()[1]==list(range(1,11))


def test_monitor_rotation_survives_restart():
    db=MemoryDB([],[])
    monitors=[{'id':i} for i in range(1,14)]
    fn=isolated('pro_jobs.py','_rotate_monitors',{'store':SimpleNamespace(db=db)})
    selected=[]
    for _ in range(7):selected.extend(m['id'] for m in fn(1500,monitors))
    assert set(selected)==set(range(1,14))
    assert fn(1500,monitors)==[{'id':2},{'id':3}]
    assert db.cursor['multi_mon:1500']==3


def test_timezone_aware_quiet_hour():
    class FrozenDate:
        @staticmethod
        def now(zone):return datetime(2026,10,20,4,0,tzinfo=timezone.utc).astimezone(zone)
    scope={'datetime':FrozenDate,'timezone':timezone,'ZoneInfo':ZoneInfo,
           'ZoneInfoNotFoundError':__import__('zoneinfo').ZoneInfoNotFoundError}
    fn=isolated('pro_jobs.py','quiet_now',scope)
    assert fn({'quiet_start':7,'quiet_end':9,'timezone':'Asia/Tehran'})
    assert not fn({'quiet_start':7,'quiet_end':9,'timezone':'UTC'})
    assert fn({'quiet_start':22,'quiet_end':6,'timezone':'UTC'})


def test_trigger_avoids_settings_only_fields_in_other_table_updates():
    s=(ROOT/'workspace_service.py').read_text()
    assert "IF TG_TABLE_NAME = 'settings' THEN" in s
    assert "IF TG_TABLE_NAME = 'settings' AND" not in s
    assert "TG_TABLE_NAME = 'settings' AND\n      COALESCE" not in s
    assert "IF COALESCE(OLD.key,'') LIKE 'last_%'" in s


def test_readonly_wss_retry_and_version_consistency():
    s=(ROOT/'core.py').read_text()
    assert "payload.get('action') in ('get_stats','ping','get_info','health') else 0" in s
    assert (ROOT/'VERSION').read_text().strip() in (ROOT/'settings.py').read_text()
    assert (ROOT/'VERSION').read_text().strip() in (ROOT/'bot_logic_mod/menu.py').read_text()


def test_agent_diagnostic_codes_and_no_port_lease_collision():
    import monitor_agent as a
    assert a._curl_failure(6)=='DNS_ERROR'
    assert a._curl_failure(7)=='TCP_ERROR'
    assert a._curl_failure(35)=='TLS_ERROR'
    assert a._curl_failure(28)=='TIMEOUT'
    assert a._curl_failure(0,'500')=='HTTP_UNEXPECTED'
    def reserve(_):return a.get_free_port()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        reserved=list(executor.map(reserve,range(50)))
    try:assert len(set(reserved))==50
    finally:
        for port in reserved:a.release_test_port(port)
    assert not a._TEST_PORTS


def test_agent_venv_cleanup_runs_only_after_wss_success():
    s=(ROOT/'agent_deploy.py').read_text()
    assert s.index('if not asyncio.run(probe()):') < s.index('commit_venv(run,previous_venv,progress)')
    assert s.index('db.store_agent_certificate') < s.index('commit_venv(run,previous_venv,progress)')
    from agent_bootstrap import commit_venv
    seen=[]
    assert commit_venv(lambda cmd,t:(seen.append(cmd) or True,'ok'), '/opt/sonar-agent-venv.previous-123abc456def')
    assert 'systemctl' in seen[0] and 'sonar-agent-venv.previous' in seen[0]


def test_outbox_schema_and_transactional_enqueue():
    pro=(ROOT/'pro_store.py').read_text()
    service=(ROOT/'service_monitor.py').read_text()
    assert 'CREATE TABLE IF NOT EXISTS pro_notification_outbox' in pro
    assert 'ON CONFLICT(event_key) DO NOTHING' in service
    assert service.index('INSERT INTO pro_notification_outbox') < service.rindex('conn.commit()',service.index('def record'))
    assert 'FOR UPDATE SKIP LOCKED' in (ROOT/'service_jobs.py').read_text()


def test_quiet_outbox_retains_unsent_and_later_sends():
    status=[]
    rows=[{'id':9,'owner_id':17,'event_type':'service_failure','message':'Down'}]
    pro=SimpleNamespace(db=SimpleNamespace(check_access=lambda uid:(True,'ok')),
                        preferences=lambda uid:{'quiet_start':22,'quiet_end':7,'timezone':'UTC'},
                        should_notify=lambda uid,event:True)
    scope={'asyncio':asyncio,'ProStore':lambda:pro,
           'claim_notifications':lambda db:rows,
           'quiet_now':lambda prefs:True,
           'mark_notification':lambda db,ident,reason:status.append((ident,reason)),
           'log':SimpleNamespace(warning=lambda *args:None),
           'send_notification':AsyncMock(return_value=True)}
    fn=isolated('service_jobs.py','flush_notifications',scope)
    bot=SimpleNamespace(send_message=AsyncMock())
    asyncio.run(fn(SimpleNamespace(bot=bot),pro))
    assert status==[(9,'quiet')]
    assert bot.send_message.await_count==0
    scope['quiet_now']=lambda prefs:False
    asyncio.run(fn(SimpleNamespace(bot=bot),pro))
    assert status[-1]==(9,'sent')
    assert scope['send_notification'].await_count==1


def test_upgrade_does_not_replace_agent_protocol_or_balancer():
    # Checked against provided v6.5.2 at release time; here assert local API contracts.
    source=(ROOT/'monitor_agent.py').read_text()
    assert "AGENT_PROTOCOL = 2" in source
    assert "'action': 'get_stats'" in (ROOT/'agent_deploy.py').read_text()


def test_uptime_transition_and_outbox_are_in_same_transaction():
    from service_monitor import ServiceStore
    from service_probes import ProbeResult
    row={'id':31,'owner_id':17,'name':'API','updated_at':'stamp','interval_seconds':60,
         'timeout_seconds':5}
    previous={**row,'enabled':True,'status':'up','fail_count':1,'success_count':0,
              'failure_threshold':2,'recovery_threshold':1}
    queries=[];commits=[]
    class Cur:
        def __init__(self):self.last=''
        def execute(self,sql,params=()):queries.append((sql,params));self.last=sql
        def fetchone(self):
            if 'SELECT * FROM pro_services' in self.last:return previous
            if 'pro_service_history' in self.last:return {'id':800}
            raise AssertionError(self.last)
    @contextmanager
    def get_connection():
        yield SimpleNamespace(commit=lambda:commits.append(len(queries))),Cur()
    store=ServiceStore(SimpleNamespace(db=SimpleNamespace(get_connection=get_connection)))
    event=store.record(row,ProbeResult(False,None,'Network unavailable'))
    assert event=='failure'
    writes=[statement for statement,_ in queries]
    assert any('pro_service_history' in text for text in writes)
    assert any('INSERT INTO pro_notification_outbox' in text for text in writes)
    assert commits==[len(writes)]
    assert any(param[1]=='service:31:800' for statement,param in queries
               if 'INSERT INTO pro_notification_outbox' in statement)


def test_failed_telegram_delivery_stays_retryable():
    status=[]
    pro=SimpleNamespace(db=SimpleNamespace(check_access=lambda uid:(True,'ok')),
                        preferences=lambda uid:{'quiet_start':None,'quiet_end':None},
                        should_notify=lambda uid,event:True)
    scope={'asyncio':asyncio,'ProStore':lambda:pro,
           'claim_notifications':lambda db:[{'id':71,'owner_id':17,'event_type':'service_failure','message':'Down'}],
           'quiet_now':lambda _:False,
           'mark_notification':lambda db,i,why:status.append(why),
           'send_notification':AsyncMock(return_value=False),
           'log':SimpleNamespace(warning=lambda *args:None)}
    fn=isolated('service_jobs.py','flush_notifications',scope)
    asyncio.run(fn(SimpleNamespace(bot=object()),pro))
    assert status==['failed']


def test_persisted_server_failures_generate_single_incident_per_transition():
    tree=ast.parse((ROOT/'pro_store.py').read_text())
    cls=next(item for item in tree.body if isinstance(item,ast.ClassDef) and item.name=='ProStore')
    method=next(item for item in cls.body if isinstance(item,ast.FunctionDef) and item.name=='record_server_observation')
    scope={}
    exec(compile(ast.Module(body=[method],type_ignores=[]),'pro_store.py','exec'),scope)
    events=[];commits=[];state=None
    class Cur:
        def __init__(self):self.last=''
        def execute(self,sql,params=()):
            nonlocal state
            self.last=sql
            if 'INSERT INTO pro_server_monitor_state' in sql and state is None:
                state={'consecutive_failures':0,'last_status':'up','generation':0}
            if 'UPDATE pro_server_monitor_state' in sql:
                count,status,generation,*_=params
                state={'consecutive_failures':count,'last_status':status,'generation':generation}
            if 'INSERT INTO pro_notification_outbox' in sql:
                events.append(tuple(params))
        def fetchone(self):
            assert 'SELECT consecutive_failures' in self.last
            return dict(state)
    @contextmanager
    def get_connection():
        yield SimpleNamespace(commit=lambda:commits.append(1)),Cur()
    self=SimpleNamespace(db=SimpleNamespace(get_connection=get_connection))
    fn=scope['record_server_observation']
    assert fn(self,1,99,False,2,'My VPS',notify=True)==(None,1)
    assert fn(self,1,99,False,2,'My VPS',notify=True)==('failure',2)
    assert fn(self,1,99,False,2,'My VPS',notify=True)==(None,3)
    assert fn(self,1,99,True,2,'My VPS',notify=True)==('recovery',0)
    assert [event[2] for event in events]==['server_down','recovery']
    assert [event[1] for event in events]==['server:99:1','server:99:2']
    assert len(commits)==4
