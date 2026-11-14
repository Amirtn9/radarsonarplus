"""Optional Telegram Mini App API.
Deploy behind an HTTPS reverse proxy; NEVER expose the API as an unauthenticated service.
Every request verifies signed Telegram initData and resource ownership.
Run: uvicorn pro_portal:app --host 127.0.0.1 --port 8090 --proxy-headers
"""
from __future__ import annotations
from contextlib import asynccontextmanager
import asyncio
import json
import logging
import hashlib
import hmac
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode
from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from settings import SUPER_ADMIN_ID
from diagnostics import snapshot, build_bundle, collect_agent_journals, collect_operational_state
from runtime import run_sync
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import secrets
import time
from web_login import digest as session_digest, csrf_proof, redeem, create_session, identify, logout, logout_all, allow_login_attempt, release_login_attempt
from subscription_health import summaries as subscription_summaries
from pro_auth import verify_init_data
from pro_store import ProStore
from psycopg2.errors import RaiseException,UniqueViolation,ForeignKeyViolation
from service_monitor import ServiceStore
from user_errors import UserError

log=logging.getLogger(__name__)
BASE=Path(__file__).resolve().parent
@asynccontextmanager
async def lifespan(application):
    await _configure_executor()
    try:
        await bootstrap_additive_schemas()
        yield
    finally:
        await stop_web_agent_queue()

app=FastAPI(title='RadarSonarPlus 6.9.10',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

async def _configure_executor():
    # The event loop owns and shuts down its default executor. Giving it the
    # process-wide run_sync pool would break other loops and later app lifespans.
    from concurrent.futures import ThreadPoolExecutor
    from runtime import MAX_WORKERS
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='sonar-web')
    )
@app.exception_handler(UserError)
async def user_error_response(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=400)

app.mount('/static',StaticFiles(directory=BASE/'pro_web'/'static'),name='static')
store=ProStore()
service_store=ServiceStore(store)


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
    token_scopes=None
    request.state.api_scopes=None
    if authorization.startswith('Bearer rs_'):
        if not request.url.path.startswith('/api/enterprise'):
            raise HTTPException(status_code=401,detail='API tokens are restricted to enterprise endpoints')
        try:
            from enterprise_services import EnterpriseStore
            token_data=await asyncio.to_thread(EnterpriseStore(store).authenticate_api_token,authorization[7:])
        except Exception:
            raise HTTPException(status_code=503,detail='Token authentication unavailable')
        if not token_data:
            raise HTTPException(status_code=401,detail='Invalid or expired API token')
        if token_data.get('rate_limited'):
            raise HTTPException(status_code=429,detail='API token rate limit exceeded (120 requests/minute)')
        uid=int(token_data['actor_id'])
        request.state.api_token_owner=int(token_data['owner_id'])
        token_scopes=set(token_data.get('scopes') or [])
        request.state.api_scopes=token_scopes
    elif authorization.startswith('tma '):
        try:uid=verify_init_data(authorization[4:], get_bot_token(),
                       max_age=min(600,max(60,int(os.getenv('SONAR_MINIAPP_SESSION_MAX_AGE','300')))))
        except (PermissionError,ValueError,KeyError):raise HTTPException(status_code=401,detail='Invalid or expired Telegram authorization')
    else:
        request.state.api_scopes=None
        session=request.cookies.get('sonar_web_session','')
        try:session_data=await asyncio.to_thread(identify,store.db,session)
        except Exception:raise HTTPException(status_code=503,detail='Session database unavailable')
        if not session_data:raise HTTPException(status_code=401,detail='Please sign in with a one-time code from the Telegram bot')
        uid=int(session_data['user_id'])
        cookie_auth=True
        if request.headers.get('x-user-activity')=='1' or request.method not in ('GET','HEAD','OPTIONS'):
            from web_login import touch_session
            await asyncio.to_thread(touch_session,store.db,session)
        if request.method not in ('GET','HEAD','OPTIONS'):
            expected=os.getenv('SONAR_WEB_ORIGIN','').rstrip('/') or str(request.base_url).rstrip('/')
            allowed_origins={value for value in (expected,str(request.base_url).rstrip('/')) if value.startswith('https://')}
            if request.headers.get('origin') not in allowed_origins:
                raise HTTPException(status_code=403,detail='Origin check failed')
            sent=request.headers.get('x-csrf-token','')
            from web_login import valid_token
            if not valid_token(sent) or not (
                    secrets.compare_digest(session_digest(sent),session_data['csrf_hash']) or
                    secrets.compare_digest(sent,csrf_proof(session))):
                raise HTTPException(status_code=403,detail='CSRF validation failed')
    try:
        allowed,_=await asyncio.to_thread(getattr(store.db,'check_read_access',store.db.check_access),uid)
        if not allowed:raise HTTPException(status_code=403,detail='Inactive account')
    except HTTPException:raise
    except Exception:log.exception('Auth database failure');raise HTTPException(status_code=503,detail='Service unavailable')
    if (request.headers.get('x-user-activity')=='1' or request.method not in ('GET','HEAD','OPTIONS')) and callable(getattr(store.db,'execute',None)):
        await asyncio.to_thread(store.db.execute,'UPDATE users SET last_active_at=NOW() WHERE user_id=%s',(uid,))
    from control_api import destructive_request,require_elevated
    if destructive_request(request):
        if cookie_auth:
            elevated=await asyncio.to_thread(require_elevated,store.db,request.cookies.get('sonar_web_session',''))
        elif authorization.startswith('tma '):
            try:
                verify_init_data(authorization[4:],get_bot_token(),max_age=60)
                elevated=True
            except (PermissionError,ValueError,KeyError):elevated=False
        else:elevated=False
        if not elevated:raise HTTPException(status_code=403,detail='STEP_UP_REQUIRED')
    request.state.session_authenticated=cookie_auth
    from workspace_service import ACTOR_ID,ACTIVE_WORKSPACE
    selected=request.headers.get('x-workspace-id','')
    workspace_token=None
    if selected:
        try:selected=int(selected)
        except ValueError:raise HTTPException(status_code=400,detail='Invalid workspace')
        if selected!=uid:
            await guarded(store.require,uid,selected)
        workspace_token=ACTIVE_WORKSPACE.set(selected)
    token=ACTOR_ID.set(uid)
    try:
        path=request.url.path
        feature=('api' if token_scopes is not None else
                 'advanced_reports' if path.startswith('/api/reports') or '/analytics' in path else
                 'automation' if '/automation/' in path and request.method!='GET' else
                 'backups' if '/backups/' in path and request.method!='GET' else
                 'agents' if path.startswith('/api/hosting/agents') and request.method not in ('GET','DELETE') else None)
        if feature and callable(getattr(store.db,'execute',None)):
            from billing import Billing
            owner=getattr(request.state,'api_token_owner',None) or await asyncio.to_thread(store.db.workspace_owner,uid)
            subscription=await asyncio.to_thread(Billing(store.db,SUPER_ADMIN_ID).subscription,owner)
            if subscription and subscription['snapshot'].get('features',{}).get(feature) is False:
                raise HTTPException(status_code=403,detail='این قابلیت در پلن فضای کاری فعال نیست')
        yield uid
    finally:
        ACTOR_ID.reset(token)
        if workspace_token is not None:ACTIVE_WORKSPACE.reset(workspace_token)


async def guarded(fn,*args,**kwargs):
    try:return await asyncio.to_thread(fn,*args,**kwargs)
    except UniqueViolation:raise HTTPException(status_code=409,detail='این مورد قبلاً ثبت شده است') from None
    except ForeignKeyViolation:raise HTTPException(status_code=400,detail='منبع وابسته وجود ندارد یا هنوز استفاده می‌شود') from None
    except RaiseException as exc:
        code=str(exc).splitlines()[0]
        if code.startswith('QUOTA_EXCEEDED:'):
            raise HTTPException(status_code=409,detail='ظرفیت این منبع در اشتراک شما تکمیل شده است') from None
        if code=='PLAN_INTERVAL_TOO_SHORT':
            raise HTTPException(status_code=400,detail='فاصله پایش از حداقل مجاز پلن کمتر است') from None
        if code=='WORKSPACE_FORBIDDEN':
            raise HTTPException(status_code=403,detail='این عملیات با نقش شما مجاز نیست') from None
        if code=='SUBSCRIPTION_INACTIVE':
            raise HTTPException(status_code=403,detail='اشتراک فعال لازم است؛ اطلاعات قبلی حذف نشده‌اند') from None
        log.exception('Database policy failure')
        raise HTTPException(status_code=500,detail='Database operation failed') from None
    except UserError as exc:raise HTTPException(status_code=400,detail=str(exc)) from None
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
    started=time.monotonic()
    response=await call_next(request)
    route=getattr(request.scope.get('route'),'path','[unmatched]')
    # Record mutations and failures; avoid duplicating frequent read-only polls.
    # Templates exclude user IDs, query strings, credentials and arbitrary paths.
    if request.url.path.startswith('/api/') and route!='/api/admin/logs' and (
            request.method not in ('GET','HEAD','OPTIONS') or response.status_code>=400):
        event_log=logging.getLogger('sonar.authentication' if '/web/' in route else 'sonar.api')
        # Keep the durability acknowledgement without running disk fsync on
        # the event loop: other requests remain schedulable while emit waits.
        await asyncio.to_thread(event_log.log,logging.WARNING if response.status_code>=400 else logging.INFO,
                      '%s %s status=%s duration_ms=%.1f',request.method,route,
                      response.status_code,(time.monotonic()-started)*1000)
    if getattr(request.state,'session_authenticated',False) and not request.url.path.startswith('/api/web/logout'):
        response.set_cookie('sonar_web_session',request.cookies['sonar_web_session'],max_age=8*3600,
                            secure=True,httponly=True,samesite='strict',path='/')
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Cross-Origin-Opener-Policy']='same-origin'
    if request.url.scheme=='https':
        response.headers['Strict-Transport-Security']='max-age=31536000'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
    return response


@app.get('/login')
async def login_page():return FileResponse(BASE/'pro_web'/'index.html')

@app.get('/')
async def index(request:Request):
    return RedirectResponse(('/dashboard' if request.cookies.get('sonar_web_session') else '/login')+('?' + request.url.query if request.url.query else ''),status_code=303)

PANEL_PATHS=('/dashboard','/servers','/configs','/monitoring','/notifications','/team','/settings','/logs',
             '/billing','/users','/reports','/rankings','/incidents','/enterprise','/fleet','/balancer','/support','/resources/new')

async def panel_page(request:Request):
    session=request.cookies.get('sonar_web_session','')
    current=await asyncio.to_thread(identify,store.db,session) if session else None
    if not current:return RedirectResponse('/login?'+urlencode({'next':request.url.path+('?' + request.url.query if request.url.query else '')}),status_code=303)
    return FileResponse(BASE/'pro_web'/'index.html')

for detail_path in ('/servers/{resource_id:int}','/configs/{resource_id:int}'):
    app.add_api_route(detail_path,panel_page,methods=['GET'],include_in_schema=False)

for panel_path in PANEL_PATHS:
    app.add_api_route(panel_path,panel_page,methods=['GET'],include_in_schema=False)

@app.get('/guide')
async def public_guide():return FileResponse(BASE/'pro_web'/'guide.html')

@app.get('/api/guide/public')
async def public_guide_content():
    from guide_content import content
    return JSONResponse(content(False),headers={'Cache-Control':'no-store'})

@app.get('/api/guide')
async def private_guide_content(uid:int=Depends(actor)):
    from guide_content import content
    return JSONResponse(content(bool(SUPER_ADMIN_ID and uid==SUPER_ADMIN_ID)),
                        headers={'Cache-Control':'private, no-store'})

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
    reserved=await asyncio.to_thread(allow_login_attempt,store.db,client_hash)
    if not reserved:
        # Possession of an unguessable, live one-time code must remain usable
        # when unrelated users share a proxy/NAT address. It is still redeemed
        # atomically below. Do not trust caller-supplied forwarded headers.
        from web_login import live_code
        if not await asyncio.to_thread(live_code,store.db,body.code):
            raise HTTPException(status_code=429,detail='Too many login attempts; retry later')
    result=await asyncio.to_thread(redeem,store.db,body.code)
    if not result:raise HTTPException(status_code=401,detail='Code is invalid, expired or already used')
    uid,session,csrf=result
    allowed,_=await asyncio.to_thread(getattr(store.db,'check_read_access',store.db.check_access),uid)
    if not allowed:
        await asyncio.to_thread(logout,store.db,session)
        raise HTTPException(status_code=403,detail='Inactive account')
    if reserved:await asyncio.to_thread(release_login_attempt,store.db,client_hash)
    response.set_cookie('sonar_web_session',session,max_age=8*3600,secure=True,
                         httponly=True,samesite='strict',path='/')
    await asyncio.to_thread(store.db.execute,'UPDATE sonar_web_sessions SET client_label=%s WHERE token_hash=%s',(request.headers.get('user-agent','')[:200],session_digest(session)))
    return {'ok':True,'csrf':csrf}


@app.post('/api/web/miniapp-session')
async def miniapp_session(request:Request,response:Response,authorization:str=Header(default='')):
    """Exchange fresh Telegram-signed initData for a normal revocable web session.

    initData is intentionally short-lived; the resulting HttpOnly cookie keeps a
    Mini App usable after that short verification window without weakening it.
    """
    # A signed Mini App launched on the current HTTPS host can authenticate
    # without the separately configured standalone website login origin.
    expected=os.getenv('SONAR_WEB_ORIGIN','').rstrip('/') or str(request.base_url).rstrip('/')
    allowed_origins={value for value in (expected,str(request.base_url).rstrip('/')) if value.startswith('https://')}
    if request.headers.get('origin') not in allowed_origins:
        raise HTTPException(status_code=403,detail='Mini App origin check failed')
    if not authorization.startswith('tma '):
        raise HTTPException(status_code=401,detail='Telegram authorization required')
    secret=get_bot_token()
    if not secret:raise HTTPException(status_code=503,detail='Login secret unavailable')
    try:
        uid=verify_init_data(authorization[4:],secret,
            max_age=min(600,max(60,int(os.getenv('SONAR_MINIAPP_SESSION_MAX_AGE','300')))))
    except (PermissionError,ValueError,KeyError,TypeError):
        raise HTTPException(status_code=401,detail='Invalid or expired Telegram authorization')
    try:
        allowed,_=await asyncio.to_thread(getattr(store.db,'check_read_access',store.db.check_access),uid)
    except Exception:
        log.exception('Mini App authorization database check failed')
        raise HTTPException(status_code=503,detail='Service unavailable')
    if not allowed:raise HTTPException(status_code=403,detail='Inactive account')
    try:
        session,csrf=await asyncio.to_thread(create_session,store.db,uid)
    except Exception:
        log.exception('Mini App session creation failed')
        raise HTTPException(status_code=503,detail='Could not create a secure session')
    response.set_cookie('sonar_web_session',session,max_age=8*3600,secure=True,
                         httponly=True,samesite='strict',path='/')
    await asyncio.to_thread(store.db.execute,'UPDATE sonar_web_sessions SET client_label=%s WHERE token_hash=%s',(request.headers.get('user-agent','')[:200],session_digest(session)))
    return {'ok':True,'csrf':csrf,'expires_in':8*3600}

@app.get('/api/web/csrf')
async def refresh_csrf(request:Request,uid:int=Depends(actor)):
    session=request.cookies.get('sonar_web_session','')
    from web_login import valid_token
    if not valid_token(session):raise HTTPException(status_code=400,detail='Not a website session')
    # Signed Telegram auth may coexist with a cookie for a different account.
    # Only return the proof for an active cookie owned by this authenticated user.
    session_data=await asyncio.to_thread(identify,store.db,session)
    if not session_data or int(session_data['user_id'])!=uid:
        raise HTTPException(status_code=403,detail='Website session does not match account')
    return {'csrf':csrf_proof(session)}

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

async def bootstrap_additive_schemas():
    # The same versioned ledger and advisory lock are used by the Telegram Bot.
    # Either service may start first; only one process applies a migration.
    from schema_migrations import run_startup_migrations
    await asyncio.to_thread(run_startup_migrations, store.db)
    from log_management import attach_logging
    app.state.database_logs=attach_logging(store.db,SUPER_ADMIN_ID)
    from web_agent_jobs import worker as web_agent_worker
    app.state.web_agent_stop=asyncio.Event()
    app.state.web_agent_task=asyncio.create_task(web_agent_worker(store.db,app.state.web_agent_stop))
    from enterprise_runtime import (
        operations_worker, webhook_worker, recovery_worker,
        scheduled_backup_worker, rollout_worker
    )
    app.state.enterprise_stop=asyncio.Event()
    from service_jobs import web_worker as service_monitor_worker
    from billing_delivery import worker as billing_message_worker
    from config_probe_jobs import worker as config_probe_worker
    from web_reporting import observation_worker
    from log_purge import worker as log_purge_worker
    app.state.enterprise_tasks=[
        asyncio.create_task(service_monitor_worker(store.db,app.state.enterprise_stop,get_bot_token)),
        asyncio.create_task(billing_message_worker(store.db,app.state.enterprise_stop,get_bot_token)),
        asyncio.create_task(config_probe_worker(store.db,app.state.enterprise_stop)),
        asyncio.create_task(log_purge_worker(store.db,SUPER_ADMIN_ID,app.state.enterprise_stop)),
        asyncio.create_task(observation_worker(store.db,app.state.enterprise_stop)),
        asyncio.create_task(operations_worker(store.db,app.state.enterprise_stop)),
        asyncio.create_task(webhook_worker(store.db,app.state.enterprise_stop)),
        asyncio.create_task(recovery_worker(store.db,app.state.enterprise_stop)),
        asyncio.create_task(rollout_worker(store.db,app.state.enterprise_stop)),
        asyncio.create_task(asyncio.to_thread(scheduled_backup_worker,store.db,app.state.enterprise_stop)),
    ]

async def stop_web_agent_queue():
    if hasattr(app.state,'web_agent_stop'):
        app.state.web_agent_stop.set()
        try:await asyncio.wait_for(app.state.web_agent_task,timeout=15)
        except asyncio.TimeoutError:
            log.warning('Web Agent worker still processing a remote installation at shutdown')
    if hasattr(app.state,'enterprise_stop'):
        app.state.enterprise_stop.set()
        for task in getattr(app.state,'enterprise_tasks',[]):
            try: await asyncio.wait_for(task,timeout=20)
            except asyncio.TimeoutError: log.warning('Enterprise worker did not stop before timeout')
    if hasattr(app.state,'database_logs'):
        from log_management import detach_logging
        await asyncio.to_thread(detach_logging,app.state.database_logs)

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
        db_ready=await asyncio.wait_for(asyncio.to_thread(check),timeout=5)
    except Exception:
        log.warning('Readiness database check failed')
        db_ready=False
    auth_ready=bool(get_bot_token())
    success=db_ready and auth_ready
    return JSONResponse({'ready':success,'db':'ok' if db_ready else 'unavailable',
                         'auth':'ok' if auth_ready else 'unconfigured'},
                        status_code=200 if success else 503)


@app.get('/api/summary')
async def summary(uid:int=Depends(actor),owner:int|None=None):return await guarded(store.summary,uid,owner)

@app.get('/api/resources/{kind}')
async def resources(kind:str,response:Response,uid:int=Depends(actor),owner:int|None=None,q:str='',tag:str|None=None,favorites:bool=False,page:int=Query(default=1,ge=1,le=10000),per_page:int=Query(default=12,ge=1,le=50)):
    if kind not in ('server','config'):raise HTTPException(status_code=404)
    rows,total=await guarded(store.list_resources,uid,kind,owner, '' if q.startswith('#') else q, q[1:] if q.startswith('#') else tag, favorites,page,per_page,True)
    response.headers['X-Total-Count']=str(total)
    return rows

@app.get('/api/server-options')
async def server_options(uid:int=Depends(actor),q:str=Query(default='',max_length=80)):
    def read():
        owner=store.workspace(uid)
        return store.db.execute('SELECT id,name FROM servers WHERE owner_id=%s AND name ILIKE %s ORDER BY name,id LIMIT 10000',(owner,'%'+q+'%'),fetch='all')
    return await guarded(read)


@app.get('/api/server-resources/{server_id}')
async def server_resources(server_id:int,uid:int=Depends(actor),hours:int=Query(default=24,ge=1,le=720)):
    from web_reporting import server_history
    return await guarded(server_history,store,uid,server_id,hours)

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
async def metrics(config_id:int,uid:int=Depends(actor),owner:int|None=None,days:int=Query(default=7,ge=1,le=30)):
    rows=await guarded(store.history,uid,config_id,owner,days)
    return [dict(r) for r in rows]

@app.get('/api/incidents')
async def incidents(uid:int=Depends(actor),owner:int|None=None,status:Literal['unresolved','open','acknowledged','resolved','all']='unresolved'):
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


@app.get('/api/admin/logs')
async def admin_logs(uid:int=Depends(actor),window:str='24',start:datetime|None=None,
                     end:datetime|None=None,level:str='',category:str='',
                     page:int=Query(default=1,ge=1,le=10000),
                     per_page:int=Query(default=50,ge=1,le=100)):
    from log_management import LogStore
    result=await guarded(LogStore(store.db,SUPER_ADMIN_ID).list,uid,window,start,end,level,category,page,per_page)
    handler=getattr(app.state,'database_logs',None)
    if handler:result['logging_health']=await asyncio.to_thread(handler.health)
    return result


class ClearLogs(BaseModel):
    confirmation:str=Field(max_length=40)


@app.delete('/api/admin/logs')
async def clear_admin_logs(body:ClearLogs,uid:int=Depends(actor)):
    from log_management import LogStore
    return await guarded(LogStore(store.db,SUPER_ADMIN_ID).clear,uid,body.confirmation)


class PurgeAllLogs(ClearLogs):
    include_remote:bool=False


@app.post('/api/admin/logs/purge',status_code=202)
async def purge_all_logs(body:PurgeAllLogs,uid:int=Depends(actor)):
    from log_purge import PurgeJobs,PurgeScopeConflict
    try:return await guarded(PurgeJobs(store.db,SUPER_ADMIN_ID).create,uid,body.confirmation,body.include_remote)
    except PurgeScopeConflict:
        raise HTTPException(status_code=409,detail='پاک‌سازی با محدوده دیگری در حال اجراست؛ پس از پایان آن دوباره اقدام کنید.')


@app.get('/api/admin/logs/purge/{job_id}')
async def purge_log_status(job_id:str,uid:int=Depends(actor)):
    from log_purge import PurgeJobs
    return await guarded(PurgeJobs(store.db,SUPER_ADMIN_ID).read,uid,job_id)

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
    from billing import Billing
    permissions=await guarded(Billing(store.db,SUPER_ADMIN_ID).permissions,uid) if callable(getattr(store.db,'execute',None)) else []
    return {'billing_permissions':permissions,'id':uid,'admin': bool(SUPER_ADMIN_ID and uid==SUPER_ADMIN_ID),'version':(BASE/'VERSION').read_text().strip()}


from config_alerts import AlertSettings

@app.get('/api/config-alerts')
async def config_alert_settings(uid:int=Depends(actor)):
    from config_alerts import settings
    owner=await guarded(store.workspace,uid)
    return await guarded(settings,store.db,owner)

@app.post('/api/config-alerts')
async def update_config_alert_settings(body:AlertSettings,uid:int=Depends(actor)):
    from config_alerts import save_settings
    owner=await guarded(store.workspace,uid)
    return await guarded(save_settings,store,uid,owner,body.model_dump())

@app.get('/api/rules')
async def rules(uid:int=Depends(actor),owner:int|None=None):
    return await guarded(store.rules,uid,owner)

@app.get('/api/diagnostics/health')
async def diagnostic_health(uid:int=Depends(actor)):
    if not SUPER_ADMIN_ID or uid != SUPER_ADMIN_ID:
        raise HTTPException(status_code=403,detail='Admin only')
    return await run_sync(snapshot)

@app.get('/api/diagnostics/bundle')
async def diagnostic_bundle(days:float=Query(default=7,ge=0.125,le=30),
                            include_remote:bool=Query(default=True),uid:int=Depends(actor)):
    if not SUPER_ADMIN_ID or uid != SUPER_ADMIN_ID:
        raise HTTPException(status_code=403,detail='Admin only')
    if not (days*24).is_integer():raise HTTPException(status_code=422,detail='Whole hours required')
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
    return FileResponse(path,media_type='application/zip',filename=f'radarsonarplus-v6.9.10-full-logs-{int(days*24)}h.zip',
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

from enterprise_api import make_router as _make_enterprise_router
app.include_router(_make_enterprise_router(store,actor,guarded))

from fleet_api import make_router as _make_fleet_router
app.include_router(_make_fleet_router(store, actor, guarded, SUPER_ADMIN_ID))

# Writes are deliberately narrow: reuse Telegram's current database services.
# Remote SSH actions, payment capture and TLS identity changes remain exclusive
# to audited existing flows until separately tested in staging.
from web_operations import make_router as _make_operations_router
app.include_router(_make_operations_router(store, actor, guarded, SUPER_ADMIN_ID))

from ssh_terminal import make_router as _make_ssh_terminal_router
app.include_router(_make_ssh_terminal_router(store, SUPER_ADMIN_ID, verify_tma=verify_init_data, get_bot_token=get_bot_token))

from web_reporting import router as reporting_router
app.include_router(reporting_router(store,actor,guarded))

from billing_api import router as billing_router
app.include_router(billing_router(store,actor,guarded,SUPER_ADMIN_ID))

from control_api import router as control_router
app.include_router(control_router(store,actor,guarded))

@app.get('/api/services/{sid}/regions')
async def service_regions(sid:int,uid:int=Depends(actor),days:int=Query(default=7,ge=1,le=30)):
    from regional_monitor import report
    return await guarded(report,store,uid,sid,days)

from notification_api import router as notification_router
app.include_router(notification_router(store,actor,guarded,get_bot_token))

from hosting_agents import router as hosting_router
app.include_router(hosting_router(store,actor,guarded,lambda:os.getenv('SONAR_WEB_ORIGIN','')))


@app.get('/api/events')
async def live_events(request:Request,uid:int=Depends(actor),owner:int|None=None):
    from fastapi.responses import StreamingResponse
    session=request.cookies.get('sonar_web_session','')
    if not session:raise HTTPException(status_code=401,detail='Cookie session required')
    selected=await guarded(store.workspace,uid,owner)
    async def stream():
        sequence=0
        while not await request.is_disconnected():
            current=await asyncio.to_thread(identify,store.db,session)
            if not current or current['user_id']!=uid:break
            try:
                data=await asyncio.to_thread(store.summary,uid,selected)
                sequence+=1
                yield 'id: '+str(sequence)+'\nevent: summary\ndata: '+json.dumps(data,default=str,ensure_ascii=False)+'\n\n'
            except PermissionError:break
            await asyncio.sleep(15)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

from control_api import policy_router
app.include_router(policy_router(store,actor,guarded))

from config_probe_jobs import router as config_probe_router
app.include_router(config_probe_router(store,actor,guarded))
