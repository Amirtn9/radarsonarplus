"""Session and technical collaboration controls, with server-side permissions."""
from fastapi import APIRouter,Depends,Request,HTTPException
from pydantic import BaseModel,Field
from typing import Literal
from control_store import Control
from web_login import digest

class Invite(BaseModel):
    member_id:int=Field(gt=0)
    role:Literal['manager','technical','monitor','server','viewer','auditor']
class Decide(BaseModel):
    accept:bool
class Role(BaseModel):
    role:Literal['manager','technical','monitor','server','viewer','auditor','remove']
class Maintenance(BaseModel):
    minutes:int=Field(ge=0,le=43200)
    reason:str=Field(min_length=3,max_length=500)
class StepUp(BaseModel):
    code:str=Field(min_length=28,max_length=128)


def router(store,actor,guarded):
    api=APIRouter(prefix='/api/control')
    def ctl():return Control(store)
    @api.get('/sessions')
    async def sessions(request:Request,uid:int=Depends(actor)):
        return await guarded(ctl().sessions,uid,request.cookies.get('sonar_web_session',''))
    @api.delete('/sessions/{ident}')
    async def revoke(ident:str,uid:int=Depends(actor)):
        if len(ident)!=64 or any(c not in '0123456789abcdef' for c in ident):raise HTTPException(status_code=400,detail='Invalid session')
        await guarded(ctl().revoke,uid,ident);return {'ok':True}
    @api.get('/workspaces')
    async def workspaces(uid:int=Depends(actor)):
        return await guarded(store.db.execute,'SELECT owner_id,role FROM pro_team WHERE member_id=%s ORDER BY owner_id',(uid,),'all')
    @api.get('/invites')
    async def invites(uid:int=Depends(actor)):return await guarded(ctl().invites,uid)
    @api.post('/invites')
    async def invite(body:Invite,uid:int=Depends(actor)):
        return {'id':await guarded(ctl().invite,uid,body.member_id,body.role)}
    @api.post('/invites/{ident}/decision')
    async def decide(ident:int,body:Decide,uid:int=Depends(actor)):
        await guarded(ctl().decide,uid,ident,body.accept);return {'ok':True}
    @api.put('/team/{member_id}')
    async def role(member_id:int,body:Role,uid:int=Depends(actor)):
        await guarded(ctl().role,uid,member_id,body.role);return {'ok':True}
    @api.post('/servers/{sid}/maintenance')
    async def maintenance(sid:int,body:Maintenance,uid:int=Depends(actor)):
        await guarded(ctl().maintenance,uid,sid,body.minutes,body.reason);return {'ok':True}
    @api.get('/step-up')
    async def step_up_status(request:Request,uid:int=Depends(actor)):
        elevated=await guarded(require_elevated,store.db,request.cookies.get('sonar_web_session',''))
        authorization=request.headers.get('authorization','')
        if not elevated and authorization.startswith('tma '):
            try:
                from pro_auth import verify_init_data
                from pro_portal import get_bot_token
                verify_init_data(authorization[4:],get_bot_token(),max_age=60)
                elevated=True
            except (PermissionError,ValueError,KeyError):pass
        return {'elevated':elevated}
    @api.post('/step-up')
    async def step_up(body:StepUp,request:Request,uid:int=Depends(actor)):
        session=request.cookies.get('sonar_web_session','')
        if not session:raise HTTPException(status_code=400,detail='Cookie session required')
        def elevate():
            ctl().rate_limit(uid,'step_up',5)
            with store.db.get_connection() as (conn,cur):
                cur.execute('''UPDATE sonar_web_codes SET used_at=NOW() WHERE code_hash=%s
                    AND user_id=%s AND used_at IS NULL AND expires_at>NOW() RETURNING user_id''',(digest(body.code),uid))
                if not cur.fetchone():raise HTTPException(status_code=401,detail='کد تازه معتبر نیست')
                cur.execute('UPDATE sonar_web_sessions SET elevated_at=NOW() WHERE token_hash=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>NOW() RETURNING user_id',(digest(session),uid))
                if not cur.fetchone():raise HTTPException(status_code=401,detail='نشست معتبر نیست')
                conn.commit()
            return {'ok':True,'valid_for_seconds':300}
        return await guarded(elevate)
    return api


def require_elevated(db,session):
    if not session:return False
    return bool(db.execute("SELECT 1 FROM sonar_web_sessions WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>NOW() AND elevated_at>NOW()-INTERVAL '5 minutes'",(digest(session),),fetch='one'))


def destructive_request(request):
    path=request.url.path
    return (request.method=='DELETE' and (path=='/api/admin/logs' or path.startswith('/api/services/')) or
            request.method=='POST' and (path in ('/api/web/logout-all','/api/admin/logs/purge') or path.startswith('/api/operations/') and (path.endswith('/delete') or path.endswith('/rotate-token'))))

class MonitorPolicy(BaseModel):
    interval_seconds:int=Field(default=120,ge=60,le=3600)
    cpu_threshold:int=Field(default=80,ge=1,le=100)
    ram_threshold:int=Field(default=80,ge=1,le=100)
    disk_threshold:int=Field(default=90,ge=1,le=100)
    cooldown_seconds:int=Field(default=600,ge=60,le=86400)


def policy_router(store,actor,guarded):
    api=APIRouter(prefix='/api/control')
    mapping={'interval_seconds':'observation_interval_seconds','cpu_threshold':'cpu_threshold','ram_threshold':'ram_threshold',
             'disk_threshold':'disk_threshold','cooldown_seconds':'resource_cooldown_seconds'}
    @api.get('/monitor-policy')
    async def get_policy(uid:int=Depends(actor)):
        def read():
            owner=store.workspace(uid);values={}
            for key,setting in mapping.items():
                value=store.db.get_setting(owner,setting)
                if value is not None:
                    try:values[key]=int(value)
                    except ValueError:pass
            minimum=store.db.execute('SELECT sonar_min_interval(%s) seconds',(owner,),fetch='one')['seconds']
            values['interval_seconds']=max(values.get('interval_seconds',120),minimum)
            return MonitorPolicy(**values).model_dump()
        return await guarded(read)
    @api.put('/monitor-policy')
    async def save_policy(body:MonitorPolicy,uid:int=Depends(actor)):
        def write():
            owner=store.workspace(uid);store.require(uid,owner,True)
            minimum=store.db.execute('SELECT sonar_min_interval(%s) seconds',(owner,),fetch='one')['seconds']
            if body.interval_seconds<minimum:raise HTTPException(status_code=400,detail='فاصله پایش از حداقل مجاز پلن کمتر است')
            with store.db.get_connection() as (conn,cur):
                for key,value in body.model_dump().items():
                    cur.execute('INSERT INTO settings(owner_id,key,value) VALUES(%s,%s,%s) ON CONFLICT(owner_id,key) DO UPDATE SET value=EXCLUDED.value',(owner,mapping[key],str(value)))
                conn.commit()
            store.audit(owner,uid,'monitor.policy','workspace','Intervals and thresholds updated')
            return {'ok':True,'applies_to':'next sampling cycle','requires_restart':False}
        return await guarded(write)
    return api
