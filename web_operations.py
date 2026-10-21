"""Conservative website write operations, delegated to Sonar's existing database.
No new SSH command, payment gateway, router, or WebSocket transport is created.
"""
from __future__ import annotations
import ipaddress
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from access_policy import valid_agent_port, valid_subscription_url
from server_onboarding import (public_destination, scan_direct_first, confirm_and_connect,
                               probe_saved_server, confirm_saved_server)
from web_agent_jobs import queue as queue_install, list_for_server

class NewServer(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    ip:str=Field(min_length=3,max_length=45)
    port:int=Field(ge=1,le=65535)
    username:str=Field(min_length=1,max_length=64)
    password:str=Field(min_length=1,max_length=2048)
    location_type:Literal['ir','ext']
    ws_port:int=Field(default=8080,ge=1024,le=65535)
    group_id:int=Field(default=0,ge=0)
    expected_fingerprint:str|None=Field(default=None,min_length=50,max_length=60)
    install_agent:bool=False

class ServerProbe(BaseModel):
    ip:str=Field(min_length=3,max_length=45)
    port:int=Field(ge=1,le=65535)

class ExistingHostConfirm(BaseModel):
    expected_fingerprint:str=Field(min_length=50,max_length=60)

class EnableServer(BaseModel):
    enabled:bool

class InstallRequest(BaseModel):
    confirmed:bool=False

class GroupCreate(BaseModel):
    name:str=Field(min_length=1,max_length=80)

class MoveGroup(BaseModel):
    group_id:int=Field(ge=0)

class RenameServer(BaseModel):
    name:str=Field(min_length=2,max_length=100)

class ServerAgentPort(BaseModel):
    port:int=Field(ge=1024,le=65535)

class NewConfig(BaseModel):
    name:str=Field(min_length=2,max_length=128)
    link:str=Field(min_length=10,max_length=8192)
    kind:Literal['single','sub_source']='single'

class ConfigDeleteConfirmation(BaseModel):
    confirm_id:int=Field(gt=0)
    confirm_name:str=Field(min_length=1,max_length=128)

class DeleteConfirmation(BaseModel):
    confirm_id:int=Field(gt=0)
    confirm_name:str=Field(min_length=1,max_length=100)


def valid_public_ip(ip):
    try:return ipaddress.ip_address(ip).is_global
    except ValueError:return False


def valid_label(value):
    return value.strip()==value and not any(ord(c)<32 for c in value)


def safe_config(link,kind):
    if kind=='sub_source':return valid_subscription_url(link)
    if not link.startswith(('vless://','vmess://','trojan://','ss://')) or any(ord(c)<32 for c in link):
        raise ValueError('Invalid supported config format')
    return link


def make_router(store,actor,guarded,admin_id):
    r=APIRouter(prefix='/api/operations',tags=['operations'])

    @r.post('/server/probe')
    async def probe_server(body:ServerProbe,uid:int=Depends(actor)):
        try:ip, port = public_destination(body.ip,body.port)
        except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
        def probe():
            # The candidate key is untrusted until separately checked by the owner.
            candidate, relay_id=scan_direct_first(store.db,admin_id,ip,port)
            return {'ip':ip,'port':port,'fingerprint':candidate['fingerprint'],
                    'key_type':candidate['type'],'route':'agent' if relay_id is not None else 'direct',
                    'agent_id':relay_id,'verified':False}
        try:return await guarded(probe)
        except HTTPException:raise
        except Exception as exc:
            raise HTTPException(status_code=502,detail='SSH_HOST_SCAN_FAILED: '+type(exc).__name__)

    @r.post('/server')
    async def create_server(body:NewServer,uid:int=Depends(actor)):
        if not valid_label(body.name) or not valid_label(body.username):
            raise HTTPException(status_code=400,detail='Invalid name or username')
        try:ip, port = public_destination(body.ip,body.port)
        except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
        if not body.expected_fingerprint:
            raise HTTPException(status_code=400,detail='Verify and confirm the SSH SHA256 fingerprint first')
        def verify_then_save():
            # Same transport, host-key store and database method as Telegram.
            # Avoid any write until SSH + independent fingerprint are verified.
            path=confirm_and_connect(store.db,admin_id,ip,port,body.username,
                                     body.password,body.expected_fingerprint)
            data=body.model_dump(exclude={'expected_fingerprint','install_agent'})
            data['ip'],data['port']=ip,port
            data['ws_port']=valid_agent_port(body.ws_port)
            result=store.db.add_server(uid,body.group_id,data,int(admin_id or 0))
            if result==-1:raise ValueError('Server quota exceeded')
            if result==-2:raise ValueError('Server already exists')
            if result==-3:raise ValueError('Server group is not accessible')
            if int(result)<=0:raise ValueError('Could not add server')
            queued=None
            queue_error=None
            if body.install_agent:
                try:queued=queue_install(store.db,uid,int(result),body.ws_port,admin_id)
                except Exception:
                    queue_error='AGENT_QUEUE_FAILED'
            return {'id':int(result),'status':'ssh_verified','route':path['route'],
                    'agent_id':path['agent_id'],'install_job_id':queued,
                    'install_error':queue_error,
                    'agent':'queued' if queued else 'not_installed'}
        try:return await __import__('asyncio').to_thread(verify_then_save)
        except PermissionError as exc:
            if 'FINGERPRINT' in str(exc):
                raise HTTPException(status_code=400,detail='HOST_FINGERPRINT_MISMATCH: do not trust this key')
            raise HTTPException(status_code=403,detail='Not authorized')
        except ValueError as exc:
            safe='SERVER_QUOTA_OR_DUPLICATE' if 'Server' in str(exc) else 'SSH_VERIFICATION_INVALID'
            raise HTTPException(status_code=400,detail=safe)
        except Exception as exc:
            # Never return plaintext credentials, SSH stack traces or commands.
            raise HTTPException(status_code=502,detail='SSH_ONBOARDING_FAILED: '+type(exc).__name__)

    @r.get('/server/{server_id}/install-jobs')
    async def install_jobs(server_id:int,uid:int=Depends(actor)):
        return await guarded(list_for_server,store.db,uid,server_id,admin_id)

    @r.post('/server/{server_id}/install')
    async def install_server(server_id:int,body:InstallRequest,uid:int=Depends(actor)):
        if not body.confirmed:
            raise HTTPException(status_code=400,detail='Confirm remote Agent installation')
        def enqueue():
            saved=store.db.get_server_for_user(server_id,uid,admin_id)
            if not saved:raise PermissionError('Server is not in your workspace')
            return queue_install(store.db,uid,server_id,int(saved['ws_port']),admin_id)
        job_id=await guarded(enqueue)
        return {'job_id':job_id,'state':'pending'}

    @r.post('/server/{server_id}/ssh-host/probe')
    async def probe_existing_host(server_id:int,uid:int=Depends(actor)):
        def scan():
            server=store.db.get_server_for_user(server_id,uid,admin_id)
            if not server:raise PermissionError('Server is not in your workspace')
            return probe_saved_server(store.db,admin_id,server)
        try:return await __import__('asyncio').to_thread(scan)
        except PermissionError:
            raise HTTPException(status_code=403,detail='Not authorized')
        except ValueError:
            raise HTTPException(status_code=400,detail='SSH_HOST_ADDRESS_INVALID')
        except Exception as exc:
            raise HTTPException(status_code=502,detail='SSH_HOST_SCAN_FAILED: '+type(exc).__name__)

    @r.post('/server/{server_id}/ssh-host/confirm')
    async def confirm_existing_host(server_id:int,body:ExistingHostConfirm,uid:int=Depends(actor)):
        def confirm():
            server=store.db.get_server_for_user(server_id,uid,admin_id)
            if not server:raise PermissionError('Server is not in your workspace')
            result=confirm_saved_server(store.db,admin_id,server,body.expected_fingerprint)
            return {'ok':True,'server_id':server_id,'status':'ssh_verified',**result}
        try:return await __import__('asyncio').to_thread(confirm)
        except PermissionError as exc:
            if 'HOST_FINGERPRINT_MISMATCH' in str(exc):
                raise HTTPException(status_code=409,detail='HOST_FINGERPRINT_MISMATCH')
            if 'Existing host key differs' in str(exc):
                raise HTTPException(status_code=409,detail='HOST_KEY_CHANGED: verify rotation out-of-band')
            raise HTTPException(status_code=403,detail='Not authorized')
        except ValueError:
            raise HTTPException(status_code=400,detail='SSH_FINGERPRINT_INVALID')
        except Exception as exc:
            from ssh_support import describe_ssh_error
            error=describe_ssh_error(exc)
            # Do not disclose credentials, raw exception detail or internal relay IPs.
            raise HTTPException(status_code=502,detail=error.split(':',1)[0])

    @r.post('/server/{server_id}/check-ssh')
    async def check_server_ssh(server_id:int,uid:int=Depends(actor)):
        def check():
            from core import ServerMonitor,sec
            from ssh_relay import eligible_relay_nodes
            server=store.db.get_server_for_user(server_id,uid,admin_id)
            if not server:raise PermissionError('Server is not in your workspace')
            relays=eligible_relay_nodes(store.db,admin_id,exclude_ip=server['ip'])
            return ServerMonitor.check_ssh_connection(server['ip'],server['port'],
                      server['username'],sec.decrypt(server['password']),relays)
        return await guarded(check)

    @r.post('/server/{server_id}/enabled')
    async def set_server_enabled(server_id:int,body:EnableServer,uid:int=Depends(actor)):
        def update():
            owner=int(store.db.workspace_owner(uid))
            row=store.db.execute('UPDATE servers SET is_active=%s WHERE id=%s AND owner_id=%s RETURNING id',
              (1 if body.enabled else 0,server_id,owner),fetch='one')
            if not row:raise PermissionError('Server is not in your workspace')
        await guarded(update)
        return {'ok':True,'enabled':body.enabled}

    @r.post('/server/{server_id}/agent-port')
    async def agent_port(server_id:int,body:ServerAgentPort,uid:int=Depends(actor)):
        def update():
            if not store.db.update_server_ws_port(server_id,body.port,uid):
                raise PermissionError('Server is not in your workspace')
        await guarded(update)
        return {'ok':True,'port':body.port}

    @r.post('/server/{server_id}/group')
    async def move_server(server_id:int,body:MoveGroup,uid:int=Depends(actor)):
        def update():
            owner=int(store.db.workspace_owner(uid))
            if body.group_id and not store.db.execute(
                'SELECT 1 AS ok FROM groups WHERE id=%s AND owner_id=%s',
                (body.group_id,owner),fetch='one'):
                raise PermissionError('Group is not in your workspace')
            changed=store.db.execute('UPDATE servers SET group_id=%s WHERE id=%s AND owner_id=%s RETURNING id',
                (body.group_id or None,server_id,owner),fetch='one')
            if not changed:raise PermissionError('Server is not in your workspace')
        await guarded(update)
        return {'ok':True,'group_id':body.group_id}

    @r.post('/server/{server_id}/rename')
    async def rename_server(server_id:int,body:RenameServer,uid:int=Depends(actor)):
        if not valid_label(body.name):raise HTTPException(status_code=400,detail='Invalid server name')
        def update():
            owner=int(store.db.workspace_owner(uid))
            row=store.db.execute('UPDATE servers SET name=%s WHERE id=%s AND owner_id=%s RETURNING id',
                (body.name,server_id,owner),fetch='one')
            if not row:raise PermissionError('Server is not in your workspace')
        await guarded(update)
        return {'ok':True}

    @r.get('/groups')
    async def groups(uid:int=Depends(actor)):
        return await guarded(store.db.get_user_groups,uid)

    @r.post('/groups')
    async def add_group(body:GroupCreate,uid:int=Depends(actor)):
        if not valid_label(body.name):raise HTTPException(status_code=400,detail='Invalid group name')
        def create():
            if not store.db.add_group(uid,body.name):raise ValueError('Group already exists')
        await guarded(create)
        return {'ok':True}

    @r.delete('/groups/{group_id}')
    async def remove_group(group_id:int,uid:int=Depends(actor)):
        if group_id<1:raise HTTPException(status_code=400,detail='Invalid group')
        def delete():
            owner=store.db.workspace_owner(uid)
            if not store.db.execute('SELECT 1 AS ok FROM groups WHERE id=%s AND owner_id=%s',
                                    (group_id,owner),fetch='one'):
                raise PermissionError('Group is not in your workspace')
            store.db.delete_group(group_id,uid)
        await guarded(delete)
        return {'ok':True}

    @r.post('/config')
    async def create_config(body:NewConfig,uid:int=Depends(actor)):
        if not valid_label(body.name):raise HTTPException(status_code=400,detail='Invalid configuration name')
        try:link=safe_config(body.link,body.kind)
        except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
        def save():
            owner=int(store.db.workspace_owner(uid))
            existing=store.db.execute('''SELECT id FROM tunnel_configs
                WHERE owner_id=%s AND type=%s AND link=%s''',(owner,body.kind,link),fetch='one')
            if existing:raise ValueError('This configuration already exists')
            now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            row=store.db.execute('''INSERT INTO tunnel_configs
                (owner_id,type,link,name,added_at,quality_score,last_status)
                VALUES(%s,%s,%s,%s,%s,0,'Pending') RETURNING id''',
                (owner,body.kind,link,body.name,now),fetch='one')
            return int(row['id'])
        config_id=await guarded(save)
        return {'id':config_id,'status':'pending','message':'Registered. No connectivity test has been performed.'}

    @r.post('/config/{config_id}/delete')
    async def delete_config(config_id:int,body:ConfigDeleteConfirmation,uid:int=Depends(actor)):
        if config_id != body.confirm_id:
            raise HTTPException(status_code=400,detail='Confirmation config ID does not match')
        def remove():
            owner=int(store.db.workspace_owner(uid))
            row=store.db.execute('SELECT name FROM tunnel_configs WHERE id=%s AND owner_id=%s',
                                 (config_id,owner),fetch='one')
            if not row:raise PermissionError('Configuration is not in your workspace')
            if row['name']!=body.confirm_name:raise ValueError('Configuration name confirmation does not match')
            store.db.delete_tunnel_config(config_id,owner)
        await guarded(remove)
        return {'ok':True,'config_id':config_id}

    @r.post('/server/{server_id}/delete')
    async def delete_server(server_id:int,body:DeleteConfirmation,uid:int=Depends(actor)):
        if server_id!=body.confirm_id:raise HTTPException(status_code=400,detail='Confirmation server ID does not match')
        def remove():
            owner=int(store.db.workspace_owner(uid))
            row=store.db.execute('SELECT id,name FROM servers WHERE id=%s AND owner_id=%s',
                                 (server_id,owner),fetch='one')
            if not row:raise PermissionError('This server is not in your workspace')
            if row['name']!=body.confirm_name:raise ValueError('Server name confirmation does not match')
            store.db.delete_server(server_id,uid)
        await guarded(remove)
        return {'ok':True,'server_id':server_id,'message':'Delete requested for workspace; all linked members are affected.'}

    return r
