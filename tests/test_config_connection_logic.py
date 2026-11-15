"""Regressions for share links, probe identity and comparable quality scores."""
import base64
import json
from urllib.parse import urlencode
import pytest
import monitor_agent as agent

UUID='00000000-0000-4000-8000-000000000001'


@pytest.mark.parametrize('protocol',['vless','trojan','vmess'])
def test_httpupgrade_preserves_path_and_host(protocol):
    options={'type':'httpupgrade','security':'tls','host':'front.example.org','path':'/upgrade?token=synthetic'}
    if protocol=='vmess':
        data={**options,'add':'192.0.2.1','port':443,'id':UUID,'net':'httpupgrade','tls':'tls'}
        link='vmess://'+base64.b64encode(json.dumps(data).encode()).decode()
    else:
        link=f'{protocol}://{UUID}@192.0.2.1:443?'+urlencode(options)
    settings=agent.parse_xray_config(link)['streamSettings']
    assert settings['httpupgradeSettings']=={'host':'front.example.org','path':'/upgrade?token=synthetic'}


def test_trojan_password_preserves_colons_in_userinfo():
    for password in ('part:two:three','part%3Atwo%3Athree'):
        result=agent.parse_xray_config(f'trojan://{password}@example.org:443?security=tls')
        assert result['settings']['servers'][0]['password']=='part:two:three'


@pytest.mark.parametrize('scheme',['vless','trojan'])
def test_zero_port_is_not_silently_replaced_by_443(scheme):
    assert agent.parse_xray_config(f'{scheme}://{UUID}@example.org:0?security=tls') is None


def test_full_profile_uses_proxy_inbound_tag_instead_of_dns_listener():
    profile={'inbounds':[{'protocol':'dokodemo-door','tag':'dns-in'},{'protocol':'socks','tag':'desktop'}],
             'outbounds':[{'protocol':'freedom','tag':'direct'}],
             'routing':{'rules':[{'inboundTag':['desktop'],'outboundTag':'direct'}]}}
    assert agent.build_probe_config(profile,10888)['inbounds'][0]['tag']=='desktop'


def test_invalid_or_missing_measurements_cannot_earn_full_score():
    from scoring import ScoreEngine
    for ping,jitter in [(0,0),(-5,0),(float('nan'),0),(12,float('inf'))]:
        assert ScoreEngine.calculate_config_score(ping,jitter)[0]==0


def test_optional_invalid_score_inputs_do_not_propagate_nan_or_raise():
    from pro_scoring import score_config
    for value in ('invalid',float('nan'),float('inf'),-1):
        result=score_config(100,15,True,uptime_percent=value,download_mbps=value)
        assert 0<=result<=100


@pytest.mark.asyncio
@pytest.mark.parametrize('ping,jitter',[(0,0),(None,0),(20,-1),(float('nan'),0),(20,float('inf'))])
async def test_controller_does_not_accept_invalid_success_measurements(monkeypatch,ping,jitter):
    from core import ServerMonitor
    async def send(*args,**kwargs):return {'status':'OK','ping':ping,'jitter':jitter,'score':10}
    monkeypatch.setattr(ServerMonitor,'ws_send_command',send)
    ok,result=await ServerMonitor._ws_test_config_unmetered('192.0.2.1',8080,None,'synthetic')
    assert ok and result['status']=='Unknown' and result['score']==0
    assert result['error_code']=='INVALID_PROBE_MEASUREMENT'


@pytest.mark.asyncio
async def test_controller_uses_same_score_and_allows_the_full_agent_budget(monkeypatch):
    from core import ServerMonitor
    from pro_scoring import score_config
    from scoring import ScoreEngine
    captured=[]
    async def send(*args,**kwargs):
        captured.append(kwargs['timeout'])
        return {'status':'OK','ping':100,'jitter':15,'score':99}
    monkeypatch.setattr(ServerMonitor,'ws_send_command',send)
    ok,result=await ServerMonitor._ws_test_config_unmetered('192.0.2.1',8080,None,'synthetic',timeout=30)
    assert result['score']==round(score_config(100,15,True)/10)==ScoreEngine.calculate_config_score(100,15)[0]
    assert captured[0]>=65


@pytest.mark.asyncio
async def test_busy_agent_returns_unknown_without_starting_another_core(monkeypatch):
    import asyncio
    class Busy:
        async def acquire(self):raise asyncio.TimeoutError
        def release(self):raise AssertionError('No capacity was acquired')
    class Socket:
        def __init__(self):self.sent=[]
        async def recv(self):return 'synthetic-agent-token'
        def __aiter__(self):return self.messages()
        async def messages(self):
            yield json.dumps({'action':'test_config','link':f'vless://{UUID}@example.org:443?security=tls'})
        async def send(self,value):self.sent.append(json.loads(value))
    monkeypatch.setattr(agent,'AGENT_TOKEN','synthetic-agent-token')
    monkeypatch.setattr(agent,'CountingWS',lambda ws:ws)
    monkeypatch.setattr(agent,'_get_test_semaphore',lambda:Busy())
    def forbidden(*args):raise AssertionError('Busy node must not start a test')
    monkeypatch.setattr(agent,'test_config_logic',forbidden)
    websocket=Socket()
    await agent.ws_handler(websocket)
    assert websocket.sent==[{'error':'AGENT_BUSY','error_code':'AGENT_BUSY','status':'Unknown'}]


def test_core_start_error_is_actionable_without_exposing_profile_secrets():
    import io
    for message,expected in [
        (b'Failed to load config: invalid field password=never-disclose','XRAY_CONFIG_INVALID'),
        (b'failed reading geoip.dat at /private/path','XRAY_ASSET_MISSING'),
        (b'listen: address already in use','XRAY_LISTENER_FAILED')]:
        assert agent._core_start_error(io.BytesIO(message))==expected


def test_jitter_does_not_mix_measurements_from_different_destinations(tmp_path,monkeypatch):
    import subprocess
    (tmp_path/'xray').touch();(tmp_path/'geoip.dat').touch()
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path));monkeypatch.setattr(agent,'XRAY_BIN',str(tmp_path/'xray'))
    monkeypatch.setattr(agent,'check_port_open',lambda *a:True)
    monkeypatch.setattr(agent,'_health_urls',lambda:('https://first.example/health','https://second.example/health'))
    class Process:
        def poll(self):return None
        def kill(self):pass
        def wait(self,**kwargs):pass
    monkeypatch.setattr(agent.subprocess,'Popen',lambda *a,**k:Process())
    responses=iter([(0,'204 0.1'),(28,'000 0'),(28,'000 0'),(0,'204 0.9'),(0,'204 0.9'),(0,'204 0.9')])
    def run(command,**kwargs):
        status,output=next(responses)
        return subprocess.CompletedProcess(command,status,stdout=output,stderr='')
    monkeypatch.setattr(agent.subprocess,'run',run)
    result=agent.test_config_logic({'protocol':'vmess'},0)
    assert result['status']=='OK'
    assert result['ping']==900 and result['jitter']==0
    assert result['sample_count']==3 and result['latency_target']=='second.example'
