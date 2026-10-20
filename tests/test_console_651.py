"""Regression tests for additive v6.5.1 web console; no network/VPS required."""
from __future__ import annotations
import asyncio
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import httpx
from fastapi import FastAPI,Header,HTTPException
from console_api import make_router,safe_status

ROOT=Path(__file__).resolve().parents[1]

class StubDB:
    def __init__(self):
        self.queries=[]
    def execute(self, sql,params,fetch=None):
        self.queries.append((sql,params,fetch))
        owner=params[1] if 'FROM servers' in sql or 'FROM tunnel_configs' in sql else params[0]
        if owner != 71:return None if fetch=='one' else []
        if 'FROM servers' in sql:
            return {'id':8,'name':'Test A','ip':'192.0.2.8','port':22,'ws_port':8081,'location_type':'ext',
                    'is_active':1,'last_status':'Online','last_agent_status':'Online','last_cpu':10,
                    'last_ram':25,'last_disk':47,'last_checked_at':datetime(2026,10,20,tzinfo=timezone.utc),
                    'agent_last_seen':'2026-10-20 10:00','group_id':None,
                    'password':'secret','agent_token':'secret','agent_cert_pem':'secret'}
        if 'FROM tunnel_configs' in sql:
            return {'id':3,'name':'Test config','type':'single','last_status':'OK','last_ping':90,
                    'last_jitter':7,'last_speed_up':'3 Mbps','last_speed_down':'4 Mbps',
                    'quality_score':88,'added_at':'2026-10-20','link':'vless://secret'}
        if 'FROM channels' in sql:
            return [{'id':1,'name':'NOC','usage_type':'all','topic_id':0,'chat_id':'-10012345678'}]
        if 'FROM pro_audit' in sql:
            return [{'actor_id':71,'event':'server_added',
                     'created_at':datetime(2026,10,20,tzinfo=timezone.utc)}]
        raise AssertionError(sql)

class StubStore:
    def __init__(self):self.db=StubDB();self.writes=[]
    def add_tag(self,uid,owner,kind,rid,tag):
        self.writes.append(('tag',uid,owner,kind,rid,tag))
    def set_rule(self,uid,owner,event,action,enabled):
        self.writes.append(('rule',uid,owner,event,action,enabled))
    def workspace(self,uid):
        if uid!=71:raise PermissionError('No access')
        return 71


def app_for_test():
    app=FastAPI();store=StubStore()
    async def actor(authorization:str=Header(default='')):
        if authorization!='tma verified':raise HTTPException(status_code=401)
        return 71
    async def guarded(fn):
        try:return fn()
        except ValueError:raise HTTPException(status_code=404)
        except PermissionError:raise HTTPException(status_code=403)
    app.include_router(make_router(store,actor,guarded))
    return app,store

def fetch(path, auth='tma verified', method='GET', payload=None):
    app,store=app_for_test()
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
            return await client.request(method,path,headers={'Authorization':auth},json=payload)
    return asyncio.run(go()),store

def test_no_auth_all_console_paths():
    for route in ['/api/console/servers/8','/api/console/configs/3','/api/console/channels','/api/console/activity']:
        result,_=fetch(route,'')
        assert result.status_code==401,route

def test_no_secret_returned_and_owner_bound():
    for route in ('/api/console/servers/8','/api/console/configs/3'):
        result,store=fetch(route)
        assert result.status_code==200,result.text
        assert 'secret' not in result.text
        assert 'password' not in result.text
        sql,params,fetch_type=store.db.queries[0]
        assert 'owner_id=%s' in sql and params[1]==71 and fetch_type=='one'

def test_channel_identifier_redacted():
    r,store=fetch('/api/console/channels')
    assert r.status_code==200
    assert r.json()[0]['chat_hint']=='…5678'
    assert '-10012345678' not in r.text
    assert store.db.queries[0][1]==(71,)

def test_activity_is_bounded_and_scoped():
    r,store=fetch('/api/console/activity?limit=51')
    assert r.status_code==422
    r,store=fetch('/api/console/activity?limit=10')
    assert r.status_code==200
    assert store.db.queries[0][1]==(71,10)
    assert r.json()[0]['event']=='server_added'

def test_sanitized_status():
    assert safe_status('Online')=='Online'
    assert safe_status('token=ABC')=='Unknown'
    assert safe_status('failed: https://private.local/key')=='Unknown'

def test_web_console_is_separate_from_agent_and_routing():
    from html.parser import HTMLParser
    idx=(ROOT/'pro_web/index.html').read_text()
    assert '/static/console.js' in idx and '/static/console.css' in idx
    assert 'data-tab="channels"' in idx
    for field in ['server-detail','config-detail','channels','recent-activity']:
        assert f'id="{field}"' in idx
    assert 'WebSocket' not in (ROOT/'console_api.py').read_text().split('def make_router')[1]


def test_writes_require_signed_access_and_validate_input():
    for endpoint,content in [('/api/console/tags',{'kind':'server','rid':5,'tag':'tag'}),
                             ('/api/console/rules',{'event':'server_down','action':'notify','enabled':True})]:
        r,_=fetch(endpoint,'',method='POST',payload=content)
        assert r.status_code==401
    r,_=fetch('/api/console/tags',method='POST',payload={'kind':'ssh','rid':5,'tag':'bad'})
    assert r.status_code==422
    r,_=fetch('/api/console/rules',method='POST',payload={'event':'firewall_change','action':'notify','enabled':True})
    assert r.status_code==422


def test_valid_writes_delegate_to_existing_services():
    r,store=fetch('/api/console/tags',method='POST',payload={'kind':'server','rid':5,'tag':'iran'})
    assert r.status_code==200 and r.json()['ok']
    assert store.writes==[('tag',71,71,'server',5,'iran')]
    r,store=fetch('/api/console/rules',method='POST',payload={'event':'server_down','action':'notify','enabled':True})
    assert r.status_code==200 and r.json()['ok']
    assert store.writes==[('rule',71,71,'server_down','notify',True)]
