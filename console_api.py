"""Read-only tenant-scoped web console extensions for Sonar 6.5.1.

Never connects to Agent/WebSocket, executes SSH, or changes routing logic.
The portal injects the existing Telegram signed authorization dependency.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal


def safe_status(value):
    status = str(value or 'Unknown').strip()
    if len(status) > 64 or any(mark in status.lower() for mark in ('://','password','token=','authorization','\n')):
        return 'Unknown'
    return status


def when(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


class TagCreate(BaseModel):
    kind: Literal['server','config']
    rid: int = Field(gt=0)
    tag: str = Field(min_length=1,max_length=32)

class RuleUpdate(BaseModel):
    event: Literal['probe_failure','server_down','recovery']
    action: Literal['notify','digest']
    enabled: bool



def make_router(store, actor, guarded):
    router = APIRouter(prefix='/api/console', tags=['console'])

    @router.post('/tags')
    async def tag_resource(body: TagCreate, uid: int = Depends(actor)):
        def update():
            owner=store.workspace(uid)
            store.add_tag(uid,owner,body.kind,body.rid,body.tag)
            return {'ok':True}
        return await guarded(update)

    @router.post('/rules')
    async def update_rule(body: RuleUpdate, uid: int = Depends(actor)):
        def update():
            owner=store.workspace(uid)
            store.set_rule(uid,owner,body.event,body.action,body.enabled)
            return {'ok':True}
        return await guarded(update)


    @router.get('/servers/{server_id}')
    async def server_details(server_id: int, uid: int = Depends(actor)):
        if server_id < 1: raise HTTPException(status_code=400, detail='Invalid server id')
        def read():
            owner = store.workspace(uid)
            row = store.db.execute('''SELECT id,name,ip,port,ws_port,location_type,is_active,
                last_status,last_agent_status,last_cpu,last_ram,last_disk,
                last_checked_at,agent_last_seen,group_id
                FROM servers WHERE id=%s AND owner_id=%s''',(server_id,owner),fetch='one')
            if not row: raise HTTPException(status_code=404, detail='Server not found')
            allowed=('id','name','ip','port','ws_port','location_type','is_active',
                     'last_status','last_agent_status','last_cpu','last_ram','last_disk',
                     'last_checked_at','agent_last_seen','group_id')
            data={key:row.get(key) for key in allowed}
            for field in ('last_status','last_agent_status'):
                data[field] = safe_status(data.get(field))
            data['last_checked_at'] = when(data.get('last_checked_at'))
            # Never return password, agent_token, agent_cert_pem or log text.
            return data
        return await guarded(read)

    @router.get('/configs/{config_id}')
    async def config_details(config_id: int, uid: int = Depends(actor)):
        if config_id < 1: raise HTTPException(status_code=400, detail='Invalid config id')
        def read():
            owner = store.workspace(uid)
            row = store.db.execute('''SELECT id,name,type,last_status,last_ping,
                last_jitter,last_speed_up,last_speed_down,quality_score,added_at
                FROM tunnel_configs WHERE id=%s AND owner_id=%s''',(config_id,owner),fetch='one')
            if not row: raise HTTPException(status_code=404, detail='Config not found')
            allowed=('id','name','type','last_status','last_ping','last_jitter',
                     'last_speed_up','last_speed_down','quality_score','added_at')
            data={key:row.get(key) for key in allowed}
            data['last_status'] = safe_status(data.get('last_status'))
            # Deliberately exclude raw links/subscription and credentials.
            return data
        return await guarded(read)

    @router.get('/channels')
    async def notification_channels(uid: int = Depends(actor)):
        def read():
            owner = store.workspace(uid)
            rows = store.db.execute('''SELECT id,name,usage_type,topic_id,chat_id
                FROM channels WHERE owner_id=%s ORDER BY id DESC LIMIT 80''',(owner,),fetch='all')
            return [{'id':r['id'],'name':r['name'] or 'کانال بدون نام',
                     'usage_type':r['usage_type'] or 'all','topic_id':r['topic_id'],
                     'chat_hint':'…'+str(r['chat_id'] or '')[-4:]} for r in rows]
        return await guarded(read)

    @router.get('/activity')
    async def activity(uid: int = Depends(actor), limit: int = Query(default=25,ge=1,le=50)):
        def read():
            owner = store.workspace(uid)
            events=store.db.execute('''SELECT actor_id,event,created_at FROM pro_audit
                WHERE owner_id=%s ORDER BY id DESC LIMIT %s''',(owner,limit),fetch='all')
            return [{'actor_id':int(r['actor_id']),'event':safe_status(r['event']),
                     'time':when(r['created_at'])} for r in events]
        return await guarded(read)

    return router
