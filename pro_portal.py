"""Optional Telegram Mini App API.
Deploy behind an HTTPS reverse proxy; NEVER expose the API as an unauthenticated service.
Every request verifies signed Telegram initData and resource ownership.
Run: uvicorn pro_portal:app --host 127.0.0.1 --port 8090 --proxy-headers
"""
from __future__ import annotations
import asyncio
import json
import logging
import hashlib
import hmac
import os
from pathlib import Path
from datetime import timezone
from functools import lru_cache
from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from settings import SUPER_ADMIN_ID
from diagnostics import snapshot, build_bundle, collect_agent_journals, collect_operational_state
from runtime import run_sync
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import secrets
from web_login import digest as session_digest, redeem, identify, logout, logout_all, allow_login_attempt, install_schema as install_login_schema
from monitor_fleet import install_schema as install_fleet_schema, install_job_schema
from subscription_health import install_schema as install_subscription_schema, summaries as subscription_summaries
from pro_auth import verify_init_data
from pro_store import ProStore
from service_monitor import ServiceStore

log=logging.getLogger(__name__)
BASE=Path(__file__).resolve().parent
app=FastAPI(title='Sonar Radar Pro V6.6',docs_url=None,redoc_url=None,openapi_url=None)

@app.on_event('startup')
async def _configure_executor():
    # Share the same bounded pool with all asyncio.to_thread() calls in the API.
    asyncio.get_running_loop().set_default_executor(__import__('runtime').SHARED_EXECUTOR)
app.mount('/static',StaticFiles(directory=BASE/'pro_web'/'static'),name='static')
store=ProStore()
service_store=ServiceStore(store)


@lru_cache(maxsize=1)
def get_bot_token():
    value=os.environ.get('SONAR_BOT_TOKEN','')
    if value:return value
    # Supports existing project config without publishing the token to the browser.
    try:
        with open(BASE/'sonar_config.json',encoding='utf-8') as f:return json.load(f).get('bot_token','')
    except (OSError,ValueError,TypeError):
        return ''


async def actor(request:Request,authorization:str=Header(default='')):
    cookie_auth=False
    if authorization.startswith('tma '):
        try:uid=verify_init_data(authorization[4:], get_bot_token(),
                       max_age=min(600,max(60,int(os.getenv('SONAR_MINIAPP_SESSION_MAX_AGE','300')))))
        except (PermissionError,ValueError,KeyError):raise HTTPException(status_code=401,detail='Invalid or expired Telegram authorization')
    else:
        session=request.cookies.get('sonar_web_session','')
        try:session_data=await asyncio.to_thread(identify,store.db,session)
        except Exception:raise HTTPException(status_code=503,detail='Session database unavailable')
        if not session_data:raise HTTPException(status_code=401,detail='Please sign in with a one-time code from the Telegram bot')
        uid=int(session_data['user_id'])
        cookie_auth=True
        if request.method not in ('GET','HEAD','OPTIONS'):
            expected=os.getenv('SONAR_WEB_ORIGIN','').rstrip('/')
            if not expected or request.headers.get('origin')!=expected:
                raise HTTPException(status_code=403,detail='Origin check failed')
            sent=request.headers.get('x-csrf-token','')
            from web_login import valid_token
            if not valid_token(sent) or not secrets.compare_digest(session_digest(sent),session_data['csrf_hash']):
                raise HTTPException(status_code=403,detail='CSRF validation failed')
    try:
        allowed,_=await asyncio.to_thread(store.db.check_access,uid)
        if not allowed:raise HTTPException(status_code=403,detail='Inactive account')
    except HTTPException:raise
    except Exception:log.exception('Auth database failure');raise HTTPException(status_code=503,detail='Service unavailable')
    from workspace_service import ACTOR_ID
    token=ACTOR_ID.set(uid)
    try:
        yield uid
    finally:
        ACTOR_ID.reset(token)


async def guarded(fn,*args,**kwargs):
    try:return await asyncio.to_thread(fn,*args,**kwargs)
    except PermissionError:raise HTTPException(status_code=403,detail='Not authorized')
    except ValueError:raise HTTPException(status_code=400,detail='Invalid request')


def shared_owner(uid:int, explicit:int|None=None):
    """The logged-in account stays private; technical resources route to team."""
    if explicit is not None and int(explicit)!=int(uid):
        return int(explicit)
    route=getattr(store.db,'workspace_owner',None)
    return int(route(uid)) if callable(route) else int(uid)


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

@app.get('/guide')
async def public_guide():return FileResponse(BASE/'pro_web'/'guide.html')

class WebCode(BaseModel):
    code: str = Field(min_length=28,max_length=128)


def require_web_origin(request):
    expected=os.getenv('SONAR_WEB_ORIGIN','').rstrip('/')
    if not expected or request.headers.get('origin')!=expected:
        raise HTTPException(status_code=403,detail='Secure website origin is not configured')

@app.post('/api/web/login')
async def web_login_endpoint(body:WebCode,request:Request,response:Response):
    require_web_origin(request)
    secret=get_bot_token()
    if not secret:raise HTTPException(status_code=503,detail='Login secret unavailable')
    # request.client is populated by trusted reverse-proxy configuration;
    # never take a caller-controlled X-Forwarded-For header directly.
    client_ip=request.client.host if request.client else 'unknown'
    client_hash=hmac.new(secret.encode(),client_ip.encode(),hashlib.sha256).hexdigest()
    if not await asyncio.to_thread(allow_login_attempt,store.db,client_hash):
        raise HTTPException(status_code=429,detail='Too many login attempts; retry later')
    result=await asyncio.to_thread(redeem,store.db,body.code)
    if not result:raise HTTPException(status_code=401,detail='Code is invalid, expired or already used')
    uid,session,csrf=result
    allowed,_=await asyncio.to_thread(store.db.check_access,uid)
    if not allowed:
        await asyncio.to_thread(logout,store.db,session)
        raise HTTPException(status_code=403,detail='Inactive account')
    response.set_cookie('sonar_web_session',session,max_age=8*3600,secure=True,
                         httponly=True,samesite='strict',path='/')
    return {'ok':True,'csrf':csrf}

@app.get('/api/web/csrf')
async def refresh_csrf(request:Request,uid:int=Depends(actor)):
    session=request.cookies.get('sonar_web_session','')
    if not session:raise HTTPException(status_code=400,detail='Not a website session')
    new=secrets.token_urlsafe(32)
    await asyncio.to_thread(store.db.execute,
      'UPDATE sonar_web_sessions SET csrf_hash=%s WHERE token_hash=%s AND user_id=%s',
      (session_digest(new),session_digest(session),uid))
    return {'csrf':new}

@app.post('/api/web/logout')
async def web_logout(request:Request,response:Response,uid:int=Depends(actor)):
    await asyncio.to_thread(logout,store.db,request.cookies.get('sonar_web_session',''))
    response.delete_cookie('sonar_web_session',path='/')
    return {'ok':True}

@app.post('/api/web/logout-all')
async def web_logout_all(response:Response,uid:int=Depends(actor)):
    await asyncio.to_thread(logout_all,store.db,uid)
    response.delete_cookie('sonar_web_session',path='/')
    return {'ok':True}

@app.on_event('startup')
async def bootstrap_additive_schemas():
    # Versioned, additive tables only; no changes to existing WSS identity.
    await asyncio.to_thread(install_subscription_schema,store.db)
    await asyncio.to_thread(install_login_schema,store.db)
    await asyncio.to_thread(install_fleet_schema,store.db)
    from agent_traffic import install_schema as install_meter_schema
    await asyncio.to_thread(install_meter_schema,store.db)
    await asyncio.to_thread(install_job_schema,store.db)
    from web_agent_jobs import install_schema as install_web_agent_schema, worker as web_agent_worker
    await asyncio.to_thread(install_web_agent_schema,store.db)
    app.state.web_agent_stop=asyncio.Event()
    app.state.web_agent_task=asyncio.create_task(web_agent_worker(store.db,app.state.web_agent_stop))

@app.on_event('shutdown')
async def stop_web_agent_queue():
    if hasattr(app.state,'web_agent_stop'):
        app.state.web_agent_stop.set()
        try:await asyncio.wait_for(app.state.web_agent_task,timeout=15)
        except asyncio.TimeoutError:
            log.warning('Web Agent worker still processing a remote installation at shutdown')

@app.get('/healthz')
async def health():return {'ok':True,'version':(BASE/'VERSION').read_text().strip()}


@app.get('/readyz')
async def readiness():
    # Readiness is distinct from liveness: execute a real bounded DB query.
    def check():
        with store.db.get_connection() as (_connection,cursor):
            # A standalone web process must not claim readiness with missing schema.
            cursor.execute("SELECT to_regclass('public.pro_preferences') AS prefs,"
                           " to_regclass('public.pro_services') AS services,"
                           " to_regclass('public.pro_service_history') AS history")
            row=cursor.fetchone()
            return bool(row and row['prefs'] and row['services'] and row['history'])
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
        return {'server':dict(server),'samples':[{'cpu':r['cpu'],'ram':r['ram'],'time':(r['created_at'].replace(tzinfo=timezone.utc) if r['created_at'].tzinfo is None else r['created_at']).isoformat() if r['created_at'] else None} for r in reversed(rows)],'temperature':None,'network':None, 'retention_hours':24}
    return await guarded(read)

@app.get('/api/subscriptions')
async def subscription_overview(uid:int=Depends(actor)):
    # Authenticated owners only. Never include the subscription link itself.
    owner=await guarded(store.db.workspace_owner,uid)
    rows=await guarded(subscription_summaries,store.db,owner)
    return {'subscriptions':[{
        **{k:v for k,v in item.items() if k not in ('started_at','completed_at','last_success_at')},
        **{k:item[k].isoformat() if item[k] else None for k in ('started_at','completed_at','last_success_at')}
    } for item in rows]}

@app.get('/api/rankings')
async def rankings(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.rankings,uid,owner,30)

@app.get('/api/metrics/{config_id}')
async def metrics(config_id:int,uid:int=Depends(actor),owner:int|None=None,days:int=7):
    rows=await guarded(store.history,uid,config_id,owner,days)
    return [dict(r) for r in rows]

@app.get('/api/incidents')
async def incidents(uid:int=Depends(actor),owner:int|None=None,status:str='unresolved'):
    return await guarded(store.incidents,uid,owner,status)

@app.get('/api/notifications/delivery')
async def notification_delivery(uid:int=Depends(actor)):
    """Tenant-scoped visibility into delayed/failed alerts; no message secrets."""
    def read():
        owner=store.workspace(uid)
        row=store.db.execute("""SELECT
            COUNT(*) FILTER(WHERE sent_at IS NULL AND skipped_at IS NULL AND dead_at IS NULL) AS pending,
            COUNT(*) FILTER(WHERE sent_at IS NOT NULL) AS delivered,
            COUNT(*) FILTER(WHERE dead_at IS NOT NULL) AS exhausted,
            COUNT(*) FILTER(WHERE skipped_at IS NOT NULL) AS skipped
            FROM pro_notification_outbox WHERE owner_id=%s""",(owner,),fetch='one')
        return dict(row) if row else {'pending':0,'delivered':0,'exhausted':0,'skipped':0}
    return await guarded(read)


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
    owner:int|None=None;key:str;value:bool|int|str|None

@app.post('/api/favorites')
async def toggle_fav(body:Favorite,uid:int=Depends(actor)):
    owner=shared_owner(uid,body.owner)
    active=await guarded(store.toggle_favorite,uid,owner,body.kind,body.rid)
    return {'favorite':active}

@app.post('/api/incidents/ack')
async def acknowledge(body:IncidentAction,uid:int=Depends(actor)):
    return {'acknowledged':await guarded(store.acknowledge,uid,shared_owner(uid,body.owner),body.incident_id)}

@app.post('/api/preferences')
async def set_preferences(body:PreferencesUpdate,uid:int=Depends(actor)):
    await guarded(store.set_preference,uid,shared_owner(uid,body.owner),body.key,body.value)
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
    return await run_sync(snapshot)

@app.get('/api/diagnostics/bundle')
async def diagnostic_bundle(days:int=Query(default=7,ge=1,le=30),
                            include_remote:bool=Query(default=True),uid:int=Depends(actor)):
    if not SUPER_ADMIN_ID or uid != SUPER_ADMIN_ID:
        raise HTTPException(status_code=403,detail='Admin only')
    stage='database_connect'
    path=None
    try:
        from database import Database
        db=Database()
        stage='server_inventory'
        agents=await asyncio.wait_for(run_sync(db.get_all_servers),timeout=60)
        stage='operational_state'
        state=await asyncio.wait_for(run_sync(collect_operational_state,db,days),timeout=90)
        remote=None
        if include_remote:
            stage='remote_agent_logs'
            # The collector has its own bounded deadline and emits an explicit status
            # for every host it could not finish; one timeout no longer cancels all logs.
            remote=await run_sync(collect_agent_journals,agents,None,days,
                                  timeout_seconds=360,max_workers=16)
        stage='build_zip'
        path=await run_sync(build_bundle,agent_rows=agents,remote_logs=remote,
                            operational_state=state,days=days)
    except asyncio.TimeoutError:
        log.warning('Diagnostics export timed out at stage=%s; source logs remain untouched',stage)
        raise HTTPException(status_code=504,detail=f'Diagnostics stage timed out: {stage}. Source logs were not changed.')
    except Exception as exc:
        log.exception('Diagnostic report failed at stage=%s type=%s',stage,type(exc).__name__)
        raise HTTPException(status_code=503,detail=f'Diagnostic report unavailable at stage {stage}: {type(exc).__name__}')
    return FileResponse(path,media_type='application/zip',filename=f'sonar-v6.6-full-logs-{days}d.zip',
                        background=BackgroundTask(path.unlink,missing_ok=True))

class TeamUpdate(BaseModel):
    member:int=Field(gt=0)
    role:str
    owner:int|None=None

@app.post('/api/team')
async def set_team(body:TeamUpdate,uid:int=Depends(actor)):
    await guarded(store.set_member,uid,shared_owner(uid,body.owner),body.member,body.role)
    return {'ok':True}


# Uptime monitoring: all resource access is owner scoped and manager writes are required.
class ServiceCreate(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    kind:str
    target:str=Field(min_length=1,max_length=1024)
    interval:int=Field(default=60,ge=60,le=3600)
    timeout:int=Field(default=5,ge=1,le=15)
    failure_threshold:int=Field(default=2,ge=1,le=5)
    recovery_threshold:int=Field(default=1,ge=1,le=5)
    expected_status:int|None=Field(default=None,ge=200,le=599)
    owner:int|None=None

class ServiceAction(BaseModel):
    owner:int|None=None
    enabled:bool|None=None

@app.get('/api/services')
async def services(uid:int=Depends(actor),owner:int|None=None):
    return await guarded(service_store.list,uid,owner)

@app.post('/api/services',status_code=201)
async def add_service(body:ServiceCreate,uid:int=Depends(actor)):
    sid=await guarded(service_store.create,uid,shared_owner(uid,body.owner),body.name,body.kind,body.target,
             body.interval,body.timeout,body.failure_threshold,body.recovery_threshold,body.expected_status)
    return {'id':sid}

@app.put('/api/services/{sid}')
async def update_service(sid:int,body:ServiceCreate,uid:int=Depends(actor)):
    await guarded(service_store.update,uid,shared_owner(uid,body.owner),sid,body.name,body.kind,body.target,
             body.interval,body.timeout,body.failure_threshold,body.recovery_threshold,body.expected_status)
    return {'ok':True}

@app.post('/api/services/{sid}/enabled')
async def service_enable(sid:int,body:ServiceAction,uid:int=Depends(actor)):
    if body.enabled is None:raise HTTPException(status_code=400,detail='enabled is required')
    await guarded(service_store.set_enabled,uid,shared_owner(uid,body.owner),sid,body.enabled)
    return {'ok':True}

@app.delete('/api/services/{sid}')
async def service_delete(sid:int,uid:int=Depends(actor),owner:int|None=None):
    await guarded(service_store.delete,uid,shared_owner(uid,owner),sid)
    return {'ok':True}

@app.get('/api/services/{sid}/history')
async def service_history(sid:int,uid:int=Depends(actor),owner:int|None=None,days:int=Query(default=7,ge=1,le=30)):
    return await guarded(service_store.history,uid,shared_owner(uid,owner),sid,days)


# Additive, read-only console features. Legacy transport and access control are unchanged.
from console_api import make_router as _make_console_router
app.include_router(_make_console_router(store,actor,guarded))

from fleet_api import make_router as _make_fleet_router
app.include_router(_make_fleet_router(store, actor, guarded, SUPER_ADMIN_ID))

# Writes are deliberately narrow: reuse Telegram's current database services.
# Remote SSH actions, payment capture and TLS identity changes remain exclusive
# to audited existing flows until separately tested in staging.
from web_operations import make_router as _make_operations_router
app.include_router(_make_operations_router(store, actor, guarded, SUPER_ADMIN_ID))
