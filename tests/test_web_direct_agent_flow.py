"""Isolated regression tests: no real SSH, VPS, token or PostgreSQL needed."""
from __future__ import annotations
import importlib.util
from pathlib import Path
from types import ModuleType
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
FP='SHA256:'+'A'*43


def onboarding(monkeypatch, direct_ok=False, relay_ok=True):
    calls=[]
    def scan(ip,port,relay=None):
        calls.append(('scan',ip,port,relay and relay['id']))
        if relay is None and not direct_ok:raise TimeoutError('no route')
        if relay is not None and not relay_ok:raise TimeoutError('relay down')
        return {'host':ip,'port':port,'fingerprint':FP,'type':'ssh-ed25519','key':'test'}
    ssh=ModuleType('ssh_support')
    ssh.scan_host_key=scan
    ssh.authenticate_candidate=lambda *a:calls.append(('authenticate',a[-1] and a[-1]['id']))
    ssh.trust_host_key=lambda *a:calls.append(('trust',a[1]))
    ssh.describe_ssh_error=lambda exc: 'SSH_TIMEOUT' if isinstance(exc,TimeoutError) else 'AUTH_FAILED'
    relay=ModuleType('ssh_relay')
    relay.eligible_relay_nodes=lambda db,admin_id,exclude_ip=None:[{'id':42,'ip':'8.8.4.4'}]
    relay.retryable_direct_error=lambda code:code=='SSH_TIMEOUT'
    monitor=ModuleType('core')
    class ServerMonitor:
        @staticmethod
        def check_ssh_connection(ip,port,username,password,nodes):
            calls.append(('check',len(nodes)))
            return {'status':'Online'}
    monitor.ServerMonitor=ServerMonitor
    for name,mod in [('ssh_support',ssh),('ssh_relay',relay),('core',monitor)]:
        monkeypatch.setitem(sys.modules,name,mod)
    spec=importlib.util.spec_from_file_location('isolated_server_onboarding',ROOT/'server_onboarding.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module,calls


def test_onboarding_always_tries_center_first(monkeypatch):
    m,calls=onboarding(monkeypatch,direct_ok=True)
    candidate,agent=m.scan_direct_first(object(),12,'8.8.8.8',22)
    assert agent is None and candidate['fingerprint']==FP
    assert calls==[('scan','8.8.8.8',22,None)]


def test_onboarding_uses_approved_agent_only_on_network_failure(monkeypatch):
    m,calls=onboarding(monkeypatch,direct_ok=False)
    result=m.confirm_and_connect(object(),12,'8.8.8.8',22,'root','secret',FP)
    assert result=={'route':'agent','agent_id':42}
    assert calls[:2]==[('scan','8.8.8.8',22,None),('scan','8.8.8.8',22,42)]
    assert [x[0] for x in calls[2:]]==['authenticate','trust','check']


def test_onboarding_mismatched_fingerprint_never_authenticates_or_trusts(monkeypatch):
    m,calls=onboarding(monkeypatch)
    with pytest.raises(PermissionError,match='FINGERPRINT'):
        m.confirm_and_connect(object(),12,'8.8.8.8',22,'root','secret','SHA256:'+'B'*43)
    assert all(x[0] not in ('authenticate','trust') for x in calls)


def test_onboarding_rejects_ssrf_and_invalid_inputs(monkeypatch):
    m,_=onboarding(monkeypatch)
    for host in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','192.0.2.1','localhost']:
        with pytest.raises(ValueError):m.public_destination(host,22)
    with pytest.raises(ValueError):m.public_destination('8.8.8.8',0)
    with pytest.raises(ValueError):m.public_destination('8.8.8.8',65536)


def test_no_fallback_after_nontransport_error(monkeypatch):
    m,calls=onboarding(monkeypatch)
    def auth_failure(ip,port,relay=None):
        calls.append(('scan',ip,port,None));raise PermissionError('bad host key')
    monkeypatch.setattr(sys.modules['ssh_support'],'scan_host_key',auth_failure)
    with pytest.raises(PermissionError):m.scan_direct_first(object(),12,'8.8.8.8',22)
    assert len(calls)==1


def test_all_relays_failed_must_not_register_server(monkeypatch):
    m,calls=onboarding(monkeypatch,relay_ok=False)
    with pytest.raises(TimeoutError):m.confirm_and_connect(object(),12,'8.8.8.8',22,'root','secret',FP)
    assert all(x[0] not in ('authenticate','trust') for x in calls)


def test_web_jobs_tenant_scope_and_pending_guard():
    from web_agent_jobs import queue,list_for_server
    class DB:
        def workspace_owner(self,uid):return uid
        def get_server_for_user(self,sid,uid,admin):return {'owner_id':54} if (sid,uid)==(2,54) else None
        def execute(self,query,params=None,fetch=None):
            if 'INSERT INTO web_agent_install_jobs' in query:return {'id':5}
            if 'SELECT id,server_id,port,state' in query:return [{'id':5,'server_id':2,'state':'pending'}]
    db=DB()
    assert queue(db,54,2,8080,999)==5
    with pytest.raises(PermissionError):queue(db,55,2,8080,999)
    with pytest.raises(PermissionError):list_for_server(db,55,2,999)
    assert list_for_server(db,54,2,999)[0]['id']==5
