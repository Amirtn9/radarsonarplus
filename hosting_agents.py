"""Limited, non-executing PHP hosting telemetry with revocable per-agent tokens."""
from __future__ import annotations
import hashlib
import json
import secrets
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,Header,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field,ConfigDict,FiniteFloat
from psycopg2.extras import Json


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_hosting_agents (
            id BIGSERIAL PRIMARY KEY,owner_id BIGINT NOT NULL,name VARCHAR(100) NOT NULL,
            token_hash CHAR(64) NOT NULL UNIQUE,token_cipher TEXT NOT NULL,
            revoked_at TIMESTAMPTZ,last_seen_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
            CREATE TABLE IF NOT EXISTS sonar_hosting_history (
            id BIGSERIAL PRIMARY KEY,agent_id BIGINT NOT NULL REFERENCES sonar_hosting_agents(id) ON DELETE CASCADE,
            report JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
            CREATE INDEX IF NOT EXISTS sonar_hosting_history_time ON sonar_hosting_history(agent_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS sonar_hosting_history_retention ON sonar_hosting_history(created_at);
            ''')
        cur.execute('DROP TRIGGER IF EXISTS sonar_quota_insert ON sonar_hosting_agents')
        cur.execute('CREATE TRIGGER sonar_quota_insert BEFORE INSERT OR UPDATE OF owner_id,revoked_at ON sonar_hosting_agents FOR EACH ROW EXECUTE FUNCTION sonar_quota_guard()')
        conn.commit()

class Name(BaseModel):name:str=Field(min_length=1,max_length=100)
class Report(BaseModel):
    model_config=ConfigDict(extra='forbid')
    php_version:str=Field(max_length=40)
    memory_bytes:int=Field(ge=0,le=2**53)
    disk_total_bytes:int|None=Field(default=None,ge=0,le=2**53)
    disk_free_bytes:int|None=Field(default=None,ge=0,le=2**53)
    load:list[FiniteFloat]|None=Field(default=None,max_length=3)


def php_source(origin,token):
    if urlsplit(origin).scheme!='https' or not urlsplit(origin).hostname:raise ValueError('HTTPS website origin is required')
    def quote(s):return "'"+s.replace('\\','\\\\').replace("'","\\'")+"'"
    return '''<?php
// Sonar limited hosting telemetry. Prefer a cron job outside public_html.
// No shell, file enumeration, arbitrary commands or inbound administration.
declare(strict_types=1);
if (!function_exists('curl_init')) { http_response_code(503); exit('cURL required'); }
$payload = [
 'php_version' => PHP_VERSION,
 'memory_bytes' => memory_get_usage(true),
 'disk_total_bytes' => function_exists('disk_total_space') ? (int) @disk_total_space(__DIR__) : null,
 'disk_free_bytes' => function_exists('disk_free_space') ? (int) @disk_free_space(__DIR__) : null,
 'load' => function_exists('sys_getloadavg') ? sys_getloadavg() : null
];
$ch = curl_init('''+quote(origin.rstrip('/')+'/api/hosting/telemetry')+''');
curl_setopt_array($ch, [CURLOPT_POST=>true, CURLOPT_POSTFIELDS=>json_encode($payload),
 CURLOPT_HTTPHEADER=>['Content-Type: application/json','Authorization: Bearer '''+token+''''],
 CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_FOLLOWLOCATION=>false,
 CURLOPT_CONNECTTIMEOUT=>5,CURLOPT_TIMEOUT=>10,CURLOPT_RETURNTRANSFER=>true]);
curl_exec($ch); $status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE); curl_close($ch);
if ($status===200 || $status===429) { echo "Sonar report submitted\\n"; }
else { http_response_code(503); echo "Sonar delivery unavailable\\n"; }
'''


def router(store,actor,guarded,get_origin):
    api=APIRouter(prefix='/api/hosting')
    @api.get('/agents')
    async def agents(uid:int=Depends(actor)):
        def read():
            owner=store.workspace(uid)
            return store.db.execute('''SELECT a.id,a.name,a.last_seen_at,a.revoked_at,
                (SELECT report FROM sonar_hosting_history h WHERE h.agent_id=a.id AND h.created_at>=NOW()-(sonar_retention_days(a.owner_id)*INTERVAL '1 day') ORDER BY id DESC LIMIT 1) latest
                FROM sonar_hosting_agents a WHERE a.owner_id=%s ORDER BY a.id DESC''',(owner,),fetch='all')
        return await guarded(read)
    @api.post('/agents')
    async def create(body:Name,uid:int=Depends(actor)):
        def write():
            owner=store.workspace(uid);store.require(uid,owner,write=True)
            from core import sec
            token=secrets.token_urlsafe(32)
            row=store.db.execute('INSERT INTO sonar_hosting_agents(owner_id,name,token_hash,token_cipher) VALUES(%s,%s,%s,%s) RETURNING id',(owner,body.name,hashlib.sha256(token.encode()).hexdigest(),sec.encrypt(token)),fetch='one')
            store.audit(owner,uid,'hosting.create',str(row['id']));return row
        return await guarded(write)
    @api.get('/agents/{ident}/download')
    async def download(ident:int,uid:int=Depends(actor)):
        def read():
            owner=store.workspace(uid);store.require(uid,owner,write=True)
            row=store.db.execute('SELECT token_cipher FROM sonar_hosting_agents WHERE id=%s AND owner_id=%s AND revoked_at IS NULL',(ident,owner),fetch='one')
            if not row:raise HTTPException(status_code=404,detail='Agent یافت نشد')
            from core import sec
            return php_source(get_origin(),sec.decrypt(row['token_cipher']))
        source=await guarded(read)
        return Response(source,media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="sonar-hosting-agent.php"','Cache-Control':'no-store'})
    @api.post('/agents/{ident}/rotate')
    async def rotate(ident:int,uid:int=Depends(actor)):
        def write():
            owner=store.workspace(uid);store.require(uid,owner,write=True)
            from core import sec
            token=secrets.token_urlsafe(32)
            row=store.db.execute('UPDATE sonar_hosting_agents SET token_hash=%s,token_cipher=%s,revoked_at=NULL WHERE id=%s AND owner_id=%s RETURNING id',(hashlib.sha256(token.encode()).hexdigest(),sec.encrypt(token),ident,owner),fetch='one')
            if not row:raise HTTPException(status_code=404,detail='Agent یافت نشد')
            store.audit(owner,uid,'hosting.rotate',str(ident));return {'ok':True}
        return await guarded(write)
    @api.delete('/agents/{ident}')
    async def revoke(ident:int,uid:int=Depends(actor)):
        owner=await guarded(store.workspace,uid);await guarded(store.require,uid,owner,True)
        await guarded(store.db.execute,'UPDATE sonar_hosting_agents SET revoked_at=NOW() WHERE id=%s AND owner_id=%s',(ident,owner));return {'ok':True}
    @api.post('/telemetry')
    async def telemetry(body:Report,authorization:str=Header(default='')):
        token=authorization[7:] if authorization.startswith('Bearer ') else ''
        if not 28<=len(token)<=128:raise HTTPException(status_code=401,detail='Agent authentication required')
        def save():
            digest=hashlib.sha256(token.encode()).hexdigest()
            with store.db.get_connection() as (conn,cur):
                cur.execute('SELECT id,owner_id FROM sonar_hosting_agents WHERE token_hash=%s AND revoked_at IS NULL FOR UPDATE',(digest,));row=cur.fetchone()
                if not row:raise HTTPException(status_code=401,detail='Agent authentication failed')
                cur.execute('SELECT 1 FROM users WHERE user_id=%s AND is_banned<>0',(row['owner_id'],))
                if cur.fetchone():raise HTTPException(status_code=403,detail='Owner account inactive')
                cur.execute('''SELECT 1 FROM sonar_subscriptions s WHERE user_id=%s AND
                    (status<>'active' OR (expires_at IS NOT NULL AND expires_at<=NOW()) OR snapshot->'features'->>'agents'='false' OR
                     (SELECT COUNT(*) FROM servers WHERE owner_id=s.user_id AND agent_id IS NOT NULL)+(SELECT COUNT(*) FROM sonar_hosting_agents WHERE owner_id=s.user_id AND revoked_at IS NULL AND id<=%s)>COALESCE((s.overrides->>'agents')::BIGINT,(s.snapshot->'limits'->>'agents')::BIGINT,0))''',(row['owner_id'],row['id']))
                if cur.fetchone():raise HTTPException(status_code=403,detail='Monitoring subscription inactive')
                cur.execute("UPDATE sonar_hosting_agents SET last_seen_at=NOW() WHERE id=%s AND (last_seen_at IS NULL OR last_seen_at<NOW()-INTERVAL '15 seconds') RETURNING id",(row['id'],))
                if not cur.fetchone():raise HTTPException(status_code=429,detail='Report interval too short')
                cur.execute('INSERT INTO sonar_hosting_history(agent_id,report) VALUES(%s,%s)',(row['id'],Json(body.model_dump())))
                cur.execute("DELETE FROM sonar_hosting_history WHERE created_at<NOW()-INTERVAL '30 days'")
                conn.commit();return {'ok':True,'scope':'hosting-process-and-filesystem-only'}
        return await guarded(save)
    return api
