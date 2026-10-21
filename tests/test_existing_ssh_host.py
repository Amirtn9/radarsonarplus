"""Existing-host SSH key enrollment: authorized server, no silent trust."""
import sys
from types import ModuleType
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import web_operations as web
import server_onboarding as boarding

FP='SHA256:'+'A'*43
FP_BAD='SHA256:'+'B'*43

class StubDB:
    def __init__(self):
        self.server={'id':7, 'owner_id':42,'ip':'8.8.8.8','port':2222,
                     'username':'root','password':'encrypted','name':'existing'}
        self.writes=0
    def get_server_for_user(self,sid,uid,admin):
        return self.server.copy() if (sid,uid)==(7,42) else None
    def add_server(self,*a,**kw):
        self.writes+=1
        raise AssertionError('existing server must never be recreated')

@pytest.fixture
def api(monkeypatch):
    db=StubDB()
    monitor=[]
    def scan(db_obj,admin,server):
        monitor.append(('probe',server['id']))
        return {'fingerprint':FP,'key_type':'ssh-ed25519',
                'route':'direct','agent_id':None,'verified':False}
    def confirm(db_obj,admin,server,expected):
        monitor.append(('confirm',server['id'],expected))
        if expected!=FP:raise PermissionError('HOST_FINGERPRINT_MISMATCH')
        return {'route':'direct','agent_id':None}
    monkeypatch.setattr(web,'probe_saved_server',scan)
    monkeypatch.setattr(web,'confirm_saved_server',confirm)
    app=FastAPI()
    async def actor():return 42
    async def guarded(fn,*args,**kwargs):return fn(*args,**kwargs)
    app.include_router(web.make_router(type('Store',(),{'db':db})(),actor,guarded,999))
    return TestClient(app),db,monitor

def test_existing_server_probe_does_not_write(api):
    c,db,monitor=api
    r=c.post('/api/operations/server/7/ssh-host/probe')
    assert r.status_code==200
    assert r.json()['fingerprint']==FP
    assert r.json()['verified'] is False
    assert monitor==[('probe',7)] and db.writes==0

def test_existing_host_accepts_confirmed_fingerprint_without_recreation(api):
    c,db,monitor=api
    r=c.post('/api/operations/server/7/ssh-host/confirm',json={'expected_fingerprint':FP})
    assert r.status_code==200
    assert r.json()['status']=='ssh_verified' and r.json()['server_id']==7
    assert monitor==[('confirm',7,FP)] and db.writes==0

def test_existing_host_unauthorized_or_missing(api):
    c,db,monitor=api
    assert c.post('/api/operations/server/100/ssh-host/probe').status_code==403
    assert c.post('/api/operations/server/100/ssh-host/confirm',json={'expected_fingerprint':FP}).status_code==403
    assert not monitor and db.writes==0

def test_existing_host_mismatch_must_not_claim_success(api):
    c,db,monitor=api
    r=c.post('/api/operations/server/7/ssh-host/confirm',json={'expected_fingerprint':FP_BAD})
    assert r.status_code==409 and r.json()['detail']=='HOST_FINGERPRINT_MISMATCH'
    assert db.writes==0

def test_saved_host_flow_uses_same_verification_as_new(monkeypatch):
    calls=[]
    def confirm(*args):
        calls.append(args)
        return {'route':'agent','agent_id':10}
    monkeypatch.setattr(boarding,'confirm_and_connect',confirm)
    core=ModuleType('core')
    class Crypt:
        def decrypt(self,x):return 'plain_password'
    core.sec=Crypt()
    monkeypatch.setitem(sys.modules,'core',core)
    result=boarding.confirm_saved_server('db',99,
        {'ip':'8.8.8.8','port':2022,'username':'root','password':'encrypted'},FP)
    assert result['route']=='agent'
    assert calls==[('db',99,'8.8.8.8',2022,'root','plain_password',FP)]


def test_probe_does_not_change_trust_store(monkeypatch):
    monkeypatch.setattr(boarding,'scan_direct_first',lambda db,admin,ip,port: (
        {'fingerprint':FP,'type':'ssh-ed25519'},4))
    result=boarding.probe_saved_server('db',99,{'ip':'8.8.8.8','port':22})
    assert result=={'fingerprint':FP,'key_type':'ssh-ed25519','route':'agent','agent_id':4,'verified':False}
