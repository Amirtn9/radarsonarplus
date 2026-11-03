"""Boundaries for additive multi-agent pool and web login; offline deterministic tests."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio
import pytest
import httpx
from fastapi import FastAPI, HTTPException
from monitor_fleet import POLICIES, score, choose, available, queue_installs
from web_login import valid_token, identify, redeem, logout
from fleet_api import make_router


def fresh(id,latency=40,cpu=20,enabled=True,approved=999,owner=7):
    return {'id':id,'latency_ms':latency,'cpu_pct':cpu,'enabled':enabled,
            'approved_by':approved,'owner_id':owner,'last_probe_ok':True,
            'last_probe_at':datetime.now(timezone.utc)}


def test_no_selection_without_approved_and_recent_health():
    assert choose([fresh(1,approved=None),fresh(2,enabled=False)]) is None
    assert choose([dict(fresh(1),last_probe_at=datetime.now(timezone.utc)-timedelta(minutes=7))]) is None
    assert choose([dict(fresh(1),last_probe_ok=False)]) is None
    assert choose([fresh(1,30,90),fresh(2,60,12)],'latency')['id']==1
    assert choose([fresh(1,30,90),fresh(2,60,12)],'load')['id']==2
    assert choose([fresh(1,40,None),fresh(2,60,10)],'balanced')['id']==2
    assert len(POLICIES)==3

class ListingDB:
    def workspace_owner(self,user):return int(user)
    def execute(self,sql,args=None,fetch='all'):
        return [dict(fresh(1,owner=7),name='Mine',ip='8.8.8.8',ws_port=50000,
                     location_type='ir',last_cpu=11,last_agent_status='ready',last_checked_at=None,
                     error_code=None),
                dict(fresh(2,owner=999,approved=None),name='Unapproved',ip='1.1.1.1',ws_port=50002,
                     location_type='ir',last_cpu=11,last_agent_status='ready',last_checked_at=None,
                     error_code=None),
                dict(fresh(3,owner=999),name='Approved',ip='1.2.3.4',ws_port=50001,
                     location_type='ir',last_cpu=11,last_agent_status='ready',last_checked_at=None,
                     error_code=None)]

def test_other_users_pending_offers_not_disclosed():
    mine=available(ListingDB(),999,7)
    assert {r['id'] for r in mine}=={1,3}
    admin=available(ListingDB(),999,999)
    assert {r['id'] for r in admin}=={1,2,3}

@pytest.mark.parametrize('bad', ['1'*129, 'ą'*32, 'a b'*20,'../../../etc/shadow','',None])
def test_session_accepts_only_urlsafe_tokens(bad):
    assert not valid_token(bad)
    assert redeem(SimpleNamespace(),bad) is None
    assert identify(SimpleNamespace(),bad) is None
    logout(SimpleNamespace(),bad)


def test_port_diagnostics_reject_private_ips_before_network():
    class FakeDB:
        def workspace_owner(self,u):return u
        def execute(self,*args,**kwargs):return {'ip':'127.0.0.1','ws_port':8080,'id':4,'owner_id':5}
    async def actor():return 5
    async def guarded(fn,*args,**kwargs):return fn(*args,**kwargs)
    api=FastAPI();api.include_router(make_router(SimpleNamespace(db=FakeDB()),actor,guarded,999))
    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://test') as c:
            return await c.post('/api/fleet/diagnose',json={'server_id':4})
    r=asyncio.run(call())
    assert r.status_code==400
    assert 'public numeric IP' in r.text


def test_fleet_install_requires_admin_before_query():
    async def actor():return 5
    async def guarded(fn,*args,**kwargs):raise AssertionError('Must not access DB')
    api=FastAPI();api.include_router(make_router(SimpleNamespace(db=object()),actor,guarded,999))
    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://test') as c:
            return await c.post('/api/fleet/install',json={'server_ids':[2],'port_mode':'automatic','confirmed':True})
    assert asyncio.run(call()).status_code==403


def test_site_operations_validate_without_touching_database():
    from web_operations import make_router as web_router
    async def actor():return 3
    async def guarded(fn,*a,**kw):raise AssertionError('Cannot run unvalidated input')
    app=FastAPI();app.include_router(web_router(SimpleNamespace(db=object()),actor,guarded,999))
    async def checks():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as c:
            no_ip=await c.post('/api/operations/server',json={'name':'Iran','ip':'127.0.0.1','port':22,
                'username':'ubuntu','password':'test','location_type':'ir','ws_port':25000})
            no_link=await c.post('/api/operations/config',json={'name':'Something','link':'javascript:alert(1)'})
            mismatch=await c.post('/api/operations/server/5/delete',json={'confirm_id':6,'confirm_name':'test'})
            return no_ip,no_link,mismatch
    results=asyncio.run(checks())
    assert [r.status_code for r in results]==[400,400,400]
