"""Behavioral tests for Mini App launch, multi-target health checks and the guide."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_signed_launch_payload_is_available_without_telegram_cdn():
    payload=urlencode({'auth_date':'123','user':'{"id":901}','hash':'a'*64})
    fragment=urlencode({'tgWebAppData':payload,'tgWebAppVersion':'8.0'})
    script=(ROOT/'pro_web/static/app.js').read_text().split('let telegramSdkPromise',1)[0]
    code='''const vm=require('vm'),assert=require('assert');
const context={window:{location:{hash:process.argv[1]}},URLSearchParams};
vm.createContext(context);vm.runInContext(process.argv[2],context);
assert.equal(vm.runInContext('auth',context),process.argv[3]);
assert.equal(vm.runInContext('isMiniApp()',context),true);
'''
    subprocess.run(['node','-e',code,'#'+fragment,script,payload],check=True,capture_output=True,text=True)


def test_wireguard_egress_blocking_primary_target_uses_independent_https(tmp_path,monkeypatch):
    import monitor_agent as agent
    (tmp_path/'xray').write_bytes(b'test-only')
    (tmp_path/'geoip.dat').write_bytes(b'test-only')
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path))
    monkeypatch.setattr(agent,'XRAY_BIN',str(tmp_path/'xray'))
    monkeypatch.setattr(agent,'get_free_port',lambda:18081)
    monkeypatch.setattr(agent,'check_port_open',lambda *a:True)
    monkeypatch.setattr(agent,'_lowprio_prefix',lambda:[])
    monkeypatch.setattr(agent,'_health_urls',lambda:('https://www.gstatic.com/generate_204','https://cp.cloudflare.com/generate_204'))
    class Process:
        stderr=None
        def poll(self):return None
        def kill(self):pass
        def wait(self,**kwargs):pass
    monkeypatch.setattr(agent.subprocess,'Popen',lambda *a,**k:Process())
    calls=[]
    def request(command,**kwargs):
        calls.append(command)
        assert command[command.index('--proxy')+1].startswith('socks5h://127.0.0.1:')
        assert command[command.index('--noproxy')+1]==''
        assert '--insecure' not in command
        return subprocess.CompletedProcess(command,0,stdout='403 0.10' if 'gstatic' in command[-1] else '204 0.08',stderr='')
    monkeypatch.setattr(agent.subprocess,'run',request)
    result=agent.test_config_logic({'protocol':'vless','tag':'proxy','settings':{}},.25)
    assert result['status']=='OK' and result['ping']==80
    assert len(calls)==4
    assert all('cloudflare' in call[-1] for call in calls[1:])
    assert not list(tmp_path.glob('config_*.json'))


def test_agent_runtime_unavailable_is_unknown_not_a_config_outage(tmp_path,monkeypatch):
    import monitor_agent as agent
    monkeypatch.setattr(agent,'XRAY_BIN',str(tmp_path/'missing'))
    assert agent.test_config_logic({})['status']=='Unknown'


def test_default_targets_are_independent_and_use_verified_https(monkeypatch):
    import monitor_agent as agent
    monkeypatch.delenv('SONAR_AGENT_HEALTH_URLS',raising=False)
    urls=agent._health_urls()
    assert len(urls)>=2 and all(url.startswith('https://') for url in urls)
    assert any('cloudflare' in url for url in urls)


def test_guide_filters_admin_content_on_the_server():
    from guide_content import content
    user=content(False);admin=content(True)
    assert len(user['chapters'])>=10
    assert len(user['buttons'])>200
    assert all(not row['admin'] for row in user['chapters']+user['buttons'])
    assert not any(row['id'].startswith('admin-') for row in user['chapters'])
    assert any(row['id']=='admin-fleet' for row in admin['chapters'])
    assert any(row['admin'] for row in admin['buttons'])
    assert (ROOT/'pro_web/static/fonts/Vazirmatn-Regular.woff2').read_bytes().startswith(b'wOF2')


def test_button_dictionary_matches_current_product_buttons():
    import importlib.util
    spec=importlib.util.spec_from_file_location('guide_builder',ROOT/'scripts/build_guide_catalog.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    saved=json.loads((ROOT/'docs/guide_buttons.json').read_text())
    assert saved==mod.build()


@pytest.mark.parametrize('values',[{'probe_interval_minutes':1},{'failure_threshold':0},{'score_delta':101},{'timezone':'not/a/zone'},{'muted_config_ids':[-1]},{'quiet_start':22},{'owner_id':902}])
def test_invalid_alert_settings_are_rejected(values):
    from config_alerts import AlertSettings
    with pytest.raises(ValueError):AlertSettings(**values)


def test_legacy_config_buttons_share_the_durable_pipeline(monkeypatch):
    import ast
    import pro_jobs
    source=ROOT/'bot_logic_mod/tunnels.py'
    tree=ast.parse(source.read_text())
    helper=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='persist_config_results')
    namespace={'extract_safe_json':json.loads}
    exec(compile(ast.Module(body=[helper],type_ignores=[]),str(source),'exec'),namespace)
    recorded=[]
    monkeypatch.setattr(pro_jobs,'_record',lambda *args:recorded.append(args))
    rows=[{'id':1},{'id':2},{'id':3},{'id':4}]
    namespace['persist_config_results'](901,rows,[(True,json.dumps({'status':'OK','ping':80,'jitter':3})),
        (True,{'status':'Fail'}),(False,'transport error'),(True,{'status':'Unknown','msg':'XRAY_NOT_READY'})])
    assert [row[1] for row in recorded]==[1,2]
    assert all(row[0]==901 and row[2]=='default' for row in recorded)
    assert not any('last_status' in ast.unparse(node) for node in tree.body
                   if isinstance(node,ast.AsyncFunctionDef) and node.name in ('perform_fast_scan','background_update_all'))


def test_web_collector_persists_real_observations_without_bot(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from web_reporting import observation_worker
    from server_stats import StatsManager
    import core
    observed=[]
    async def run():
        stop=asyncio.Event()
        class DB:
            def execute(self,*args):
                if 'SELECT last_id' in args[0]:return {'last_id':0}
                return [{'id':1,'owner_id':901,'ip':'192.0.2.1','port':22,'username':'test','password':'encrypted-test','ws_port':8080}]
            def check_access(self,owner):return True,'ok'
            def persist_server_observation(self,server,result):observed.append((server,result));stop.set()
        monkeypatch.setattr(core.sec,'decrypt',lambda value:'synthetic-test')
        probe=AsyncMock(return_value={'status':'Online','cpu':25,'ram':50})
        monkeypatch.setattr(StatsManager,'check_full_stats',probe)
        await asyncio.wait_for(observation_worker(DB(),stop),timeout=3)
        assert observed[0][1]['cpu']==25
        assert probe.await_count==1
    asyncio.run(run())
