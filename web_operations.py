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

class NewServer(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    ip:str=Field(min_length=3,max_length=45)
    port:int=Field(ge=1,le=65535)
    username:str=Field(min_length=1,max_length=64)
    password:str=Field(min_length=1,max_length=2048)
    location_type:Literal['ir','ext']
    ws_port:int=Field(default=8080,ge=1024,le=65535)

class NewConfig(BaseModel):
    name:str=Field(min_length=2,max_length=128)
    link:str=Field(min_length=10,max_length=8192)
    kind:Literal['single','sub_source']='single'

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

    @r.post('/server')
    async def create_server(body:NewServer,uid:int=Depends(actor)):
        if not valid_public_ip(body.ip):raise HTTPException(status_code=400,detail='Enter a public numeric IPv4/IPv6 address')
        if not valid_label(body.name) or not valid_label(body.username):
            raise HTTPException(status_code=400,detail='Invalid name or username')
        def save():
            # Delegate quota, owner assignment, encryption and constraint checks
            # to the *same* method used by the existing Telegram bot.
            result=store.db.add_server(uid,0,body.model_dump(),int(admin_id or 0))
            if result==-1:raise ValueError('Server quota exceeded')
            if result==-2:raise ValueError('Server already exists')
            if result==-3:raise ValueError('Server group is not accessible')
            if int(result)<=0:raise ValueError('Could not add server')
            return int(result)
        server_id=await guarded(save)
        return {'id':server_id,'status':'registered','agent':'not_installed',
                'message':'Server saved. Agent installation requires a separate confirmed operation.'}

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
