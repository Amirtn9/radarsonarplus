"""Bounded durable configuration probes using the existing verified Xray Agent path."""
import asyncio
import math
from psycopg2.extras import Json
from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field
from user_errors import UserError


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_config_probes (
          id BIGSERIAL PRIMARY KEY,owner_id BIGINT NOT NULL,actor_id BIGINT NOT NULL,config_id BIGINT NOT NULL,
          request_key VARCHAR(150) NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
          result JSONB NOT NULL DEFAULT '{}',error_code VARCHAR(80),lease_until TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),completed_at TIMESTAMPTZ,
          UNIQUE(owner_id,request_key));
          CREATE INDEX IF NOT EXISTS sonar_config_probes_queue ON sonar_config_probes(state,id);
          ALTER TABLE tunnel_configs ADD COLUMN IF NOT EXISTS monitoring_enabled BOOLEAN NOT NULL DEFAULT TRUE;
          ALTER TABLE tunnel_configs ADD COLUMN IF NOT EXISTS config_revision INTEGER NOT NULL DEFAULT 1;
          CREATE OR REPLACE FUNCTION sonar_config_revision_bump() RETURNS TRIGGER LANGUAGE plpgsql AS $$
          BEGIN
            IF NEW.link IS DISTINCT FROM OLD.link OR NEW.type IS DISTINCT FROM OLD.type THEN
              NEW.config_revision := GREATEST(NEW.config_revision, OLD.config_revision+1);
            END IF;
            RETURN NEW;
          END $$;
          DROP TRIGGER IF EXISTS sonar_config_revision_guard ON tunnel_configs;
          CREATE TRIGGER sonar_config_revision_guard BEFORE UPDATE ON tunnel_configs
            FOR EACH ROW EXECUTE FUNCTION sonar_config_revision_bump()''')
        conn.commit()


def enqueue(pro,uid,ids,key):
    from control_store import Control
    owner=pro.workspace(uid);pro.require(uid,owner,True);Control(pro).rate_limit(uid,'config_probes',10)
    with pro.db.get_connection() as (conn,cur):
        cur.execute('SELECT pg_advisory_xact_lock(535322,hashtext(%s))',(str(owner),))
        cur.execute("SELECT COUNT(*) n FROM sonar_config_probes WHERE owner_id=%s AND state IN ('pending','running')",(owner,))
        pending=cur.fetchone()['n']
        cur.execute('SELECT COUNT(*) n FROM sonar_config_probes WHERE owner_id=%s AND request_key=ANY(%s)',(owner,[key+':'+str(i) for i in set(ids)]))
        additional=len(set(ids))-cur.fetchone()['n']
        if pending+additional>100:raise UserError('حداکثر ۱۰۰ تست در صف هر فضای کاری مجاز است')
        result=[]
        for ident in dict.fromkeys(ids):
            cur.execute("SELECT id FROM tunnel_configs WHERE id=%s AND owner_id=%s AND type IN ('single','sub_item','json') AND sonar_monitor_allowed(owner_id,'configs',id)",(ident,owner))
            if not cur.fetchone():raise UserError('کانفیگ خارج از فضای کاری یا ظرفیت مجاز است')
            cur.execute('''INSERT INTO sonar_config_probes(owner_id,actor_id,config_id,request_key)
               VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,request_key) DO UPDATE SET request_key=EXCLUDED.request_key RETURNING id,state''',(owner,uid,ident,key+':'+str(ident)))
            result.append(dict(cur.fetchone()))
        conn.commit();return result


def claim(db):
    with db.get_connection() as (conn,cur):
        cur.execute("UPDATE sonar_config_probes SET state='failed',error_code='WORKER_INTERRUPTED',completed_at=NOW() WHERE state='running' AND lease_until<NOW()")
        cur.execute("SELECT * FROM sonar_config_probes WHERE state='pending' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 5")
        rows=[dict(r) for r in cur.fetchall()]
        for row in rows:cur.execute("UPDATE sonar_config_probes SET state='running',lease_until=NOW()+INTERVAL '6 minutes' WHERE id=%s",(row['id'],))
        conn.commit();return rows


async def execute(pro,job):
    result={};error=None
    try:
        from settings import SUPER_ADMIN_ID
        from monitor_fleet import select_monitor_row
        from tunnel_logic import tunnel_manager
        from pro_jobs import _record
        await asyncio.to_thread(pro.require,job['actor_id'],job['owner_id'],True)
        cfg=await asyncio.to_thread(pro.db.execute,"SELECT link,config_revision FROM tunnel_configs WHERE id=%s AND owner_id=%s AND sonar_monitor_allowed(owner_id,'configs',id)",(job['config_id'],job['owner_id']),'one')
        if not cfg:raise UserError('QUOTA_OR_RESOURCE_UNAVAILABLE')
        monitor=await asyncio.to_thread(select_monitor_row,pro.db,job['owner_id'],SUPER_ADMIN_ID)
        if not monitor:raise UserError('MONITOR_NODE_UNAVAILABLE')
        ok,data=await asyncio.wait_for(tunnel_manager._test_via_ws(monitor,cfg['link'],size=0,timeout=30,owner_id=job['owner_id']),timeout=35)
        if not ok or not isinstance(data,dict) or data.get('status') not in ('OK','Fail'):raise UserError('PROBE_UNAVAILABLE')
        if data['status']=='OK' and any(type(data.get(k)) not in (int,float) or not math.isfinite(data[k]) or data[k]<(0 if k=='jitter' else .000001) for k in ('ping','jitter')):raise UserError('INVALID_PROBE_RESULT')
        result={k:data[k] for k in ('status','ping','jitter') if k in data}
        region=f"{monitor.get('location_type','unknown')}:{monitor['id']}"
        await asyncio.to_thread(_record,job['owner_id'],job['config_id'],region,data,pro,cfg['config_revision'])
    except asyncio.CancelledError:raise
    except UserError as exc:error=str(exc)[:80]
    except Exception:error='PROBE_EXECUTION_UNAVAILABLE'
    await asyncio.to_thread(pro.db.execute,"UPDATE sonar_config_probes SET state=%s,result=%s,error_code=%s,completed_at=NOW(),lease_until=NULL WHERE id=%s",('failed' if error else 'succeeded',Json(result),error,job['id']))


async def worker(db,stop):
    from pro_store import ProStore
    import logging
    log=logging.getLogger(__name__);pro=ProStore(db)
    while not stop.is_set():
        try:
            jobs=await asyncio.to_thread(claim,db)
            if jobs:await asyncio.gather(*(execute(pro,job) for job in jobs))
        except asyncio.CancelledError:raise
        except Exception:log.exception('Configuration probe queue unavailable')
        try:await asyncio.wait_for(stop.wait(),timeout=3)
        except asyncio.TimeoutError:pass


class ProbeRequest(BaseModel):
    config_ids:list[int]=Field(min_length=1,max_length=50)
    request_key:str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
class Enabled(BaseModel):enabled:bool


def router(pro,actor,guarded):
    api=APIRouter(prefix='/api/config-probes')
    @api.post('')
    async def create(body:ProbeRequest,uid:int=Depends(actor)):
        return await guarded(enqueue,pro,uid,body.config_ids,body.request_key)
    @api.get('/{config_id}')
    async def history(config_id:int,uid:int=Depends(actor)):
        owner=await guarded(pro.workspace,uid)
        return await guarded(pro.db.execute,'SELECT id,config_id,state,result,error_code,created_at,completed_at FROM sonar_config_probes WHERE owner_id=%s AND config_id=%s ORDER BY id DESC LIMIT 20',(owner,config_id),'all')
    @api.put('/{config_id}/enabled')
    async def enabled(config_id:int,body:Enabled,uid:int=Depends(actor)):
        owner=await guarded(pro.workspace,uid);await guarded(pro.require,uid,owner,True)
        row=await guarded(pro.db.execute,'UPDATE tunnel_configs SET monitoring_enabled=%s WHERE id=%s AND owner_id=%s RETURNING id',(body.enabled,config_id,owner),'one')
        if not row:raise UserError('کانفیگ یافت نشد')
        await guarded(pro.audit,owner,uid,'config.monitoring',str(config_id),str(body.enabled))
        return {'ok':True}
    return api
