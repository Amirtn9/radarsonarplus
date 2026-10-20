"""Authenticated fleet administration, additive to the current portal.
This endpoint intentionally does NOT expose private SSH credentials, agent
secrets, or scan arbitrary hosts and ports.
"""
from __future__ import annotations
import asyncio
import time
import ipaddress
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from monitor_fleet import available, offer, approve, policy, set_policy, record_probe, choose, queue_installs

class PoolOffer(BaseModel):
    server_id:int=Field(gt=0)
    enabled:bool

class PoolApproval(BaseModel):
    server_ids:list[int]=Field(min_length=1,max_length=30)
    enabled:bool

class PoolPolicy(BaseModel):
    strategy:Literal['latency','load','balanced']

class FleetInstall(BaseModel):
    server_ids:list[int]=Field(min_length=1,max_length=12)
    port_mode:Literal['stored','automatic']='stored'
    confirmed:bool=False

class ScanPorts(BaseModel):
    server_id:int=Field(gt=0)
    # One short list, never a general-purpose TCP port scanner.
    ports:list[int]=Field(default_factory=list,max_length=5)

SAFE_PROBE_PORTS=(8080,8443,9443,18080,28080)


def make_router(store,actor,guarded,admin_id):
    r=APIRouter(prefix='/api/fleet',tags=['fleet'])
    def ensure_admin(uid):
        if not admin_id or int(uid)!=int(admin_id):
            raise HTTPException(status_code=403,detail='Only the main administrator may manage the shared fleet')

    @r.get('/candidates')
    async def candidates(uid:int=Depends(actor)):
        ensure_admin(uid)
        def read():
            rows=store.db.execute("""SELECT id,name,ip,ws_port,location_type,last_agent_status
                 FROM servers WHERE owner_id=%s AND is_active=1 AND location_type='ir'
                 ORDER BY id LIMIT 300""",(uid,),fetch='all')
            return [dict(row) for row in rows]
        return await guarded(read)

    @r.post('/install')
    async def install(body:FleetInstall,uid:int=Depends(actor)):
        ensure_admin(uid)
        if not body.confirmed:raise HTTPException(status_code=400,detail='Explicit confirmation required')
        jobs=await guarded(queue_installs,store.db,uid,body.server_ids,body.port_mode)
        return {'queued':jobs,'count':len(jobs),'message':'Queued; requires successful WSS validation'}

    @r.get('/install/jobs')
    async def install_jobs(uid:int=Depends(actor)):
        ensure_admin(uid)
        def read():
            rows=store.db.execute("""SELECT id,server_id,requested_port,state,stage,error_code,created_at,updated_at
                    FROM monitor_fleet_install_jobs WHERE requested_by=%s ORDER BY id DESC LIMIT 40""",(uid,),fetch='all')
            return [dict(r) for r in rows]
        return await guarded(read)

    @r.get('/nodes')
    async def nodes(uid:int=Depends(actor)):
        def read():
            rows=available(store.db,admin_id,uid)
            for row in rows:
                # Do not leak another tenant's IP even if offered without approval.
                if int(uid)!=int(admin_id) and int(row['owner_id']) != int(store.db.workspace_owner(uid)):
                    row['ip']='shared agent';row['name']='نود عمومی تأییدشده'
            return [{'id':x['id'],'name':x['name'],'ip':x['ip'],'owner_id':x['owner_id'] if uid==admin_id else None,
                 'port':x['ws_port'],'latency_ms':x['latency_ms'],'cpu':x['cpu_pct'],
                 'last_probe_ok':x['last_probe_ok'],'last_probe_at':str(x['last_probe_at']) if x['last_probe_at'] else None,
                 'enabled':x['enabled'],'approved':x['approved_by'] is not None,'error_code':x['error_code']} for x in rows]
        return {'strategy':await guarded(policy,store.db), 'nodes':await guarded(read)}

    @r.post('/offer')
    async def offer_node(data:PoolOffer,uid:int=Depends(actor)):
        await guarded(offer,store.db,uid,data.server_id,data.enabled)
        return {'ok':True,'status':'awaiting-admin-approval' if data.enabled else 'revoked'}

    @r.post('/approve')
    async def approval(data:PoolApproval,uid:int=Depends(actor)):
        ensure_admin(uid)
        # Partial failure is reported; no unauthorised other-tenant reads.
        def apply():
            for sid in set(data.server_ids):approve(store.db,uid,sid,data.enabled)
        await guarded(apply)
        return {'ok':True,'count':len(set(data.server_ids))}

    @r.post('/policy')
    async def policy_update(data:PoolPolicy,uid:int=Depends(actor)):
        ensure_admin(uid)
        await guarded(set_policy,store.db,data.strategy)
        return {'ok':True,'strategy':data.strategy}

    @r.get('/choice')
    async def choice(uid:int=Depends(actor)):
        # Private diagnostics: only nodes owned by administrator or this workspace.
        rows=await guarded(available,store.db,admin_id,uid)
        node=choose(rows,await guarded(policy,store.db),uid)
        return {'selected_id':node['id'] if node else None,
                'reason':'healthy approved node' if node else 'No fresh, verified agent telemetry (last 5 min)'}

    @r.get('/reachability/{server_id}')
    async def reachability(server_id:int,uid:int=Depends(actor)):
        if server_id<1:raise HTTPException(status_code=400,detail='Invalid server ID')
        owner=int(await guarded(store.db.workspace_owner,uid))
        row=await guarded(store.db.execute,
            'SELECT id,name,ip,port FROM servers WHERE id=%s AND owner_id=%s',
            (server_id,owner),'one')
        if not row:raise HTTPException(status_code=404,detail='Server not found')
        try:
            address=ipaddress.ip_address(str(row['ip']))
            if not address.is_global:raise ValueError('non-public IP')
        except ValueError:
            raise HTTPException(status_code=400,detail='Multi-vantage checks require a public numeric IP address')
        port=int(row['port'] or 22)
        # Safety: a fixed list prevents turning an Agent into a generic port scanner.
        if port not in (22,80,443,8080,8443,2022,2222):
            return {'classification':'unsupported_port','main':None,'iran':None,
                    'detail':'This SSH port is outside the safe probe allowlist'}
        async def direct():
            try:
                _reader,writer=await asyncio.wait_for(asyncio.open_connection(str(address),port),timeout=4)
                writer.close();await writer.wait_closed();return True
            except (OSError,asyncio.TimeoutError):return False
        main=await direct()
        nodes=await guarded(available,store.db,admin_id,uid)
        eligible=[r for r in nodes if r.get('enabled') and r.get('approved_by') is not None and r.get('last_probe_ok') is True]
        from core import ServerMonitor
        async def from_node(node):
            try:
                response=await asyncio.wait_for(ServerMonitor.ws_send_command(
                    node['ip'],int(node['ws_port']),None,
                    {'action':'probe_public_tcp','target':str(address),'port':port},timeout=6),timeout=9)
                if isinstance(response,dict) and isinstance(response.get('reachable'),bool):return response['reachable']
            except (OSError,asyncio.TimeoutError):pass
            return None
        probes=await asyncio.gather(*(from_node(n) for n in eligible[:3]))
        iran=True if any(x is True for x in probes) else False if probes and all(x is False for x in probes) else None
        if not main and iran is True:status='possible_iran_only'
        elif main:status='reachable_from_main'
        elif iran is False:status='unreachable_from_available_vantage_points'
        else:status='insufficient_evidence'
        return {'server_id':server_id,'classification':status,'main':main,'iran':iran,
                'agent_samples':len(probes),
                'detail':'This is TCP reachability, not ICMP ping. Full outage requires independent external confirmation.'}

    @r.post('/diagnose')
    async def diagnose(data:ScanPorts,uid:int=Depends(actor)):
        # Only a saved server in this user's workspace. The admin may inspect
        # offered nodes, not arbitrary addresses supplied through the browser.
        def get_target():
            workspace=store.db.workspace_owner(uid)
            row=store.db.execute('''SELECT id,ip,ws_port,owner_id FROM servers
              WHERE id=%s AND owner_id=%s''',(data.server_id,workspace),fetch='one')
            if not row and int(uid)==int(admin_id):
                row=store.db.execute('''SELECT s.id,s.ip,s.ws_port,s.owner_id FROM servers s
                     JOIN monitor_fleet_nodes f ON f.server_id=s.id
                     WHERE s.id=%s AND f.offered_by=s.owner_id''',(data.server_id,),fetch='one')
            if not row:raise PermissionError('Target is outside your workspace')
            return dict(row)
        target=await guarded(get_target)
        try:
            target_ip=ipaddress.ip_address(str(target['ip']))
            if not target_ip.is_global:raise ValueError('not public')
        except ValueError:
            raise HTTPException(status_code=400,detail='A saved public numeric IP is required for port diagnostics')
        stored=int(target['ws_port'] or 8080)
        ports=list(dict.fromkeys(int(p) for p in (data.ports or [stored,8080,8443,9443])))
        if len(ports)>5 or any(not 1024<=p<=65535 or (p not in SAFE_PROBE_PORTS and p!=stored) for p in ports):
            raise HTTPException(status_code=400,detail='Port list outside bounded, supported probe ports')
        async def tcp(port):
            started=time.monotonic()
            try:
                _r,w=await asyncio.wait_for(asyncio.open_connection(target['ip'],port),timeout=3)
                w.close();await w.wait_closed()
                return {'port':port,'tcp':'open','rtt_ms':round((time.monotonic()-started)*1000,1)}
            except (OSError,asyncio.TimeoutError):return {'port':port,'tcp':'closed_or_filtered','rtt_ms':None}
        answers=await asyncio.gather(*(tcp(p) for p in ports))
        # TCP open does not validate TLS or token. Only known enrolled port is tested.
        enrolled=next((x for x in answers if x['port']==stored and x['tcp']=='open'),None)
        if enrolled:
            from core import ServerMonitor
            started=time.monotonic()
            try:
                outcome=await asyncio.wait_for(ServerMonitor.ws_send_command(target['ip'],stored,None,{'action':'get_stats'},timeout=7),timeout=11)
                ok=isinstance(outcome,dict) and not outcome.get('error')
                enrolled['wss']='verified' if ok else 'failed'
                if ok:
                    enrolled['rtt_ms']=round((time.monotonic()-started)*1000,1)
                    enrolled['cpu']=outcome.get('cpu')
                    if int(uid)==int(admin_id):
                        await guarded(record_probe,store.db,data.server_id,True,enrolled['rtt_ms'],outcome.get('cpu'))
                elif int(uid)==int(admin_id):await guarded(record_probe,store.db,data.server_id,False,None,None,'WSS_VERIFICATION_FAILED')
            except (OSError,asyncio.TimeoutError):
                enrolled['wss']='timeout'
                if int(uid)==int(admin_id):await guarded(record_probe,store.db,data.server_id,False,None,None,'WSS_TIMEOUT')
        return {'server_id':data.server_id,'ports':answers,'note':'Only WSS=verified proves TLS and Agent token; open TCP alone is not sufficient.'}
    return r
