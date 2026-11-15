"""Regression checks for flaky tunnels, release identity and inventory access."""
import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import monitor_agent as agent
from agent_inventory import BUNDLE_FILES,bundle_fingerprint,compare_agent,check_versions
from pro_scoring import score_config


def test_build_identity_covers_helpers_and_normalizes_lock_serialization(tmp_path):
    for name in BUNDLE_FILES:
        (tmp_path/name).write_text('{"b":2,"a":1}' if name.endswith('.json') else name)
    first=bundle_fingerprint(tmp_path)
    (tmp_path/'agent_runtime.lock.json').write_text('{"a": 1, "b": 2}')
    assert bundle_fingerprint(tmp_path)==first
    (tmp_path/'pro_scoring.py').write_text('changed scoring')
    assert bundle_fingerprint(tmp_path)!=first
    (tmp_path/'ssh_relay.py').unlink()
    assert bundle_fingerprint(tmp_path) is None


def test_inventory_never_confuses_missing_metadata_or_newer_build_with_outdated():
    stats={'status':'Online','agent_version':'6.9.10','agent_protocol':2,'agent_build':'a'*64}
    assert compare_agent(stats,'6.9.10','a'*64,2)['state']=='current'
    assert compare_agent(stats,'6.9.10','b'*64,2)['state']=='different'
    assert compare_agent(dict(stats,agent_version='9.0.0'),'6.9.10','a'*64,2)['state']=='different'
    assert compare_agent(dict(stats,agent_build=None),'6.9.10','a'*64,2)['state']=='unknown'
    assert compare_agent(dict(stats,agent_protocol=1),'6.9.10','a'*64,2)['state']=='incompatible'
    assert compare_agent({'error':'offline'},'6.9.10','a'*64,2)['state']=='unknown'


def test_flaky_or_insufficient_samples_cannot_get_a_perfect_score():
    assert score_config(30,0,True,success_rate=1,sample_count=3)==100
    assert score_config(30,0,True,success_rate=.5,sample_count=3)==50
    assert score_config(30,0,True,success_rate=1,sample_count=1)<34
    for invalid in (-1,2,float('nan'),'bad'):
        assert score_config(30,0,True,success_rate=invalid,sample_count=3)==0


@pytest.mark.asyncio
async def test_controller_retains_reliability_penalty(monkeypatch):
    from core import ServerMonitor
    async def send(*args,**kwargs):
        return {'status':'OK','ping':30,'jitter':0,'success_rate':.5,'sample_count':3,'score':10}
    monkeypatch.setattr(ServerMonitor,'ws_send_command',send)
    ok,result=await ServerMonitor._ws_test_config_unmetered('192.0.2.1',8080,None,'synthetic')
    assert ok and result['score']==5


@pytest.mark.parametrize('samples,expected_ping,expected_rate',[
    ([(0,'204 0.1'),(0,'204 0.9'),(0,'204 0.11')],110,1),
    ([(0,'204 0.1'),(28,'000 0'),(0,'204 0.1'),(0,'204 0.1')],100,.75),
    ([(0,'204 0.1'),(28,'000 0'),(28,'000 0')],100,1/3),
])
def test_probe_uses_median_and_counts_failures(tmp_path,monkeypatch,samples,expected_ping,expected_rate):
    (tmp_path/'xray').touch();(tmp_path/'geoip.dat').touch()
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path));monkeypatch.setattr(agent,'XRAY_BIN',str(tmp_path/'xray'))
    monkeypatch.setattr(agent,'check_port_open',lambda *a:True)
    monkeypatch.setattr(agent,'_health_urls',lambda:('https://health.example/health',))
    class Process:
        def poll(self):return None
        def kill(self):pass
        def wait(self,**kwargs):pass
    monkeypatch.setattr(agent.subprocess,'Popen',lambda *a,**k:Process())
    responses=iter(samples)
    def run(command,**kwargs):
        assert command[command.index('curl')+1]=='--disable'
        status,output=next(responses)
        return subprocess.CompletedProcess(command,status,stdout=output,stderr='')
    monkeypatch.setattr(agent.subprocess,'run',run)
    result=agent.test_config_logic({'protocol':'vmess'},0)
    assert result['status']=='OK' and result['ping']==expected_ping
    assert result['success_rate']==expected_rate
    assert result['attempt_count']==len(samples)
    assert result['latency_statistic']=='median'
    if expected_rate<1:assert result['stability']=='unstable' and result['score']<10


def test_runtime_version_reads_binary_not_stale_install_record(tmp_path,monkeypatch):
    (tmp_path/'runtime-installed.json').write_text(json.dumps({'version':'v0.0.1'}))
    (tmp_path/'xray').write_text('#!/bin/sh\nprintf "Xray 26.10.31 (fixture)\\n"\n')
    (tmp_path/'xray').chmod(0o700)
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path))
    result=agent.runtime_versions()
    assert result['xray_version']=='v26.10.31'
    assert result['xray_recorded_version']=='v0.0.1'


@pytest.mark.asyncio
async def test_live_inventory_reports_match_and_unreachable_without_leaking_secrets(monkeypatch):
    from core import ServerMonitor
    from settings import PROJECT_VERSION
    async def send(ip,*args,**kwargs):
        if ip=='192.0.2.2':raise OSError('private secret exception')
        return {'status':'Online','agent_version':PROJECT_VERSION,'agent_protocol':2,
                'agent_build':bundle_fingerprint(Path(agent.__file__).parent),'agent_token':'secret',
                'xray_version':'v26.10.31','xray_compat_version':'v0.0.1'}
    monkeypatch.setattr(ServerMonitor,'ws_send_command',send)
    report=await check_versions([{'id':i,'name':f'Agent{i}','ip':f'192.0.2.{i}','ws_port':8080} for i in (1,2)])
    assert [node['state'] for node in report['nodes']]==['current','unknown']
    assert report['nodes'][0]['xray_version_state']=='current'
    assert report['nodes'][0]['xray_compat_version_state']=='different'
    assert 'secret' not in json.dumps(report)


@pytest.mark.asyncio
async def test_inventory_api_rejects_non_admin_before_query_or_network():
    from fleet_api import make_router
    async def actor():return 7
    async def guarded(*args,**kwargs):raise AssertionError('Must authorize first')
    api=FastAPI();api.include_router(make_router(SimpleNamespace(db=None),actor,guarded,999))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://test') as client:
        response=await client.post('/api/fleet/versions',json={'server_ids':[1]})
    assert response.status_code==403
