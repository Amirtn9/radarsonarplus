"""Optional Telegram Mini App API.
Deploy behind an HTTPS reverse proxy; NEVER expose the API as an unauthenticated service.
Every request verifies signed Telegram initData and resource ownership.
Run: uvicorn pro_portal:app --host 127.0.0.1 --port 8090 --proxy-headers
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from functools import lru_cache
from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from settings import SUPER_ADMIN_ID
from diagnostics import snapshot, build_bundle
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pro_auth import verify_init_data
from pro_store import ProStore

log=logging.getLogger(__name__)
BASE=Path(__file__).resolve().parent
app=FastAPI(title='Sonar Radar Pro V6',docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=BASE/'pro_web'/'static'),name='static')
store=ProStore()


@lru_cache(maxsize=1)
def get_bot_token():
    value=os.environ.get('SONAR_BOT_TOKEN','')
    if value:return value
    # Supports existing project config without publishing the token to the browser.
    try:
        with open(BASE/'sonar_config.json',encoding='utf-8') as f:return json.load(f).get('bot_token','')
    except (OSError,ValueError,TypeError):
        return ''


async def actor(authorization:str=Header(default='')):
    if not authorization.startswith('tma '):raise HTTPException(status_code=401,detail='Telegram authentication required')
    try:uid=verify_init_data(authorization[4:], get_bot_token(),
                                  max_age=min(600,max(60,int(os.getenv('SONAR_MINIAPP_SESSION_MAX_AGE', '300')))))
    except (PermissionError,ValueError,KeyError):raise HTTPException(status_code=401,detail='Invalid or expired Telegram authorization')
    try:
        allowed,_=await asyncio.to_thread(store.db.check_access,uid)
        if not allowed:raise HTTPException(status_code=403,detail='Inactive account')
    except HTTPException:raise
    except Exception:log.exception('Auth database failure');raise HTTPException(status_code=503,detail='Service unavailable')
    return uid


async def guarded(fn,*args,**kwargs):
    try:return await asyncio.to_thread(fn,*args,**kwargs)
    except PermissionError:raise HTTPException(status_code=403,detail='Not authorized')
    except ValueError:raise HTTPException(status_code=400,detail='Invalid request')


@app.middleware('http')
async def headers(request:Request,call_next):
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
    return response


@app.get('/')
async def index():return FileResponse(BASE/'pro_web'/'index.html')

@app.get('/healthz')
async def health():return {'ok':True,'version':(BASE/'VERSION').read_text().strip()}


@app.get('/readyz')
async def readiness():
    # Readiness is distinct from liveness: execute a real bounded DB query.
    def check():
        with store.db.get_connection() as (_connection,cursor):
            cursor.execute('SELECT 1 AS ok')
            row=cursor.fetchone()
            return bool(row and (row['ok'] if isinstance(row,dict) else row[0])==1)
    try:
        success=bool(get_bot_token()) and await asyncio.wait_for(asyncio.to_thread(check),timeout=5)
    except Exception:
        log.warning('Readiness database check failed')
        success=False
    return JSONResponse({'ready':success,'db':'ok' if success else 'unavailable'},
                        status_code=200 if success else 503)


@app.get('/api/summary')
async def summary(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.summary,uid,owner)

@app.get('/api/resources/{kind}')
async def resources(kind:str,uid:int=Depends(actor),owner:int|None=None,q:str='',tag:str|None=None,favorites:bool=False,page:int=Query(default=1,ge=1,le=10000)):
    if kind not in ('server','config'):raise HTTPException(status_code=404)
    return await guarded(store.list_resources,uid,kind,owner, '' if q.startswith('#') else q, q[1:] if q.startswith('#') else tag, favorites,page)


@app.get('/api/server-resources/{server_id}')
async def server_resources(server_id:int,uid:int=Depends(actor),owner:int|None=None):
    def read():
        workspace=store.workspace(uid,owner)
        server=store.db.execute('SELECT id,name FROM servers WHERE id=%s AND owner_id=%s',(server_id,workspace),fetch='one')
        if not server:raise ValueError('Server not found')
        rows=store.db.execute("SELECT cpu,ram,created_at FROM server_stats WHERE server_id=%s AND created_at >= NOW()-INTERVAL '24 hours' ORDER BY created_at DESC LIMIT 288",(server_id,),fetch='all')
        return {'server':dict(server),'samples':[{'cpu':r['cpu'],'ram':r['ram'],'time':r['created_at'].isoformat() if r['created_at'] else None} for r in reversed(rows)],'temperature':None,'network':None, 'retention_hours':24}
    return await guarded(read)

@app.get('/api/rankings')
async def rankings(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.rankings,uid,owner,30)

@app.get('/api/metrics/{config_id}')
async def metrics(config_id:int,uid:int=Depends(actor),owner:int|None=None,days:int=7):
    rows=await guarded(store.history,uid,config_id,owner,days)
    return [dict(r) for r in rows]

@app.get('/api/incidents')
async def incidents(uid:int=Depends(actor),owner:int|None=None,status:str='unresolved'):
    return await guarded(store.incidents,uid,owner,status)

@app.get('/api/audit')
async def audit(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.audit_rows,uid,owner)

@app.get('/api/tags')
async def tags(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.tags,uid,owner)

@app.get('/api/team')
async def team(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.team,uid,owner)

@app.get('/api/preferences')
async def preferences(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.preferences,uid,owner)

class Favorite(BaseModel):
    kind:str;rid:int=Field(gt=0);owner:int|None=None

class IncidentAction(BaseModel):
    incident_id:int=Field(gt=0);owner:int|None=None

class PreferencesUpdate(BaseModel):
    owner:int|None=None;key:str;value:bool|int|None

@app.post('/api/favorites')
async def toggle_fav(body:Favorite,uid:int=Depends(actor)):
    owner=body.owner or uid
    active=await guarded(store.toggle_favorite,uid,owner,body.kind,body.rid)
    return {'favorite':active}

@app.post('/api/incidents/ack')
async def acknowledge(body:IncidentAction,uid:int=Depends(actor)):
    return {'acknowledged':await guarded(store.acknowledge,uid,body.owner or uid,body.incident_id)}

@app.post('/api/preferences')
async def set_preferences(body:PreferencesUpdate,uid:int=Depends(actor)):
    await guarded(store.set_preference,uid,body.owner or uid,body.key,body.value)
    return {'ok':True}


@app.get('/api/me')
async def me(uid:int=Depends(actor)):
    return {'id':uid,'admin': bool(SUPER_ADMIN_ID and uid==SUPER_ADMIN_ID),'version':(BASE/'VERSION').read_text().strip()}

@app.get('/api/rules')
async def rules(uid:int=Depends(actor),owner:int|None=None):
    return await guarded(store.rules,uid,owner)

@app.get('/api/diagnostics/health')
async def diagnostic_health(uid:int=Depends(actor)):
    if not SUPER_ADMIN_ID or uid != SUPER_ADMIN_ID:
        raise HTTPException(status_code=403,detail='Admin only')
    return await asyncio.to_thread(snapshot)

@app.get('/api/diagnostics/bundle')
async def diagnostic_bundle(uid:int=Depends(actor)):
    if not SUPER_ADMIN_ID or uid != SUPER_ADMIN_ID:
        raise HTTPException(status_code=403,detail='Admin only')
    try:
        from database import Database
        agents=await asyncio.wait_for(asyncio.to_thread(lambda: Database().get_all_servers()),timeout=20)
        path=await asyncio.wait_for(asyncio.to_thread(build_bundle,agent_rows=agents),timeout=65)
    except Exception:
        log.exception('Diagnostic report failed')
        raise HTTPException(status_code=503,detail='Diagnostic report unavailable')
    return FileResponse(path,media_type='application/zip',filename='sonar-v6-support.zip',
                        background=BackgroundTask(path.unlink,missing_ok=True))

class TeamUpdate(BaseModel):
    member:int=Field(gt=0)
    role:str
    owner:int|None=None

@app.post('/api/team')
async def set_team(body:TeamUpdate,uid:int=Depends(actor)):
    await guarded(store.set_member,uid,body.owner or uid,body.member,body.role)
    return {'ok':True}
