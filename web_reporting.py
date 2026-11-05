"""Owner-scoped report exports and time-bucketed observations shared by web clients."""
import math
import csv
import io
from datetime import timezone
from fastapi import APIRouter,Depends,Query
from fastapi.responses import Response

WINDOWS=(1,3,12,24,48)

def finite_json(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {key:finite_json(item) for key,item in value.items()}
    if isinstance(value,list):return [finite_json(item) for item in value]
    return value

def server_history(store,uid,sid,hours=24):
    if hours not in WINDOWS:raise ValueError('بازه زمانی معتبر نیست')
    owner=store.workspace(uid)
    server=store.db.execute('SELECT id,name,last_checked_at,last_agent_status FROM servers WHERE id=%s AND owner_id=%s',(sid,owner),fetch='one')
    if not server:raise PermissionError('Server is not in your workspace')
    # Bound chart size while preserving the entire requested period, not just
    # the most recent LIMIT rows. Nulls remain null, never synthetic zeroes.
    rows=store.db.execute('''SELECT to_timestamp(floor(extract(epoch FROM created_at)/%s)*%s) AS created_at,
       AVG(cpu) cpu,AVG(ram) ram,AVG(disk) disk,MAX(traffic_gb) traffic_gb
       FROM server_stats WHERE server_id=%s AND created_at>=NOW()-(%s*INTERVAL '1 hour')
       GROUP BY floor(extract(epoch FROM created_at)/%s) ORDER BY created_at''',
       (max(60,hours*5),max(60,hours*5),sid,hours,max(60,hours*5)),fetch='all')
    samples=[]
    for row in rows:
        stamp=row['created_at']
        samples.append({**{key:float(row[key]) if row[key] is not None else None for key in ('cpu','ram','disk','traffic_gb')},
                        'time':(stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).isoformat()})
    for sample in samples:
        for key in ('cpu','ram','disk','traffic_gb'):
            value=sample[key]
            if value is not None and (not math.isfinite(value) or value<0 or (key!='traffic_gb' and value>100)):sample[key]=None
    return {'server':dict(server),'samples':samples,'hours':hours,'retention_hours':720,'temperature':None,'network':samples[-1]['traffic_gb'] if samples else None}


def router(store,actor,guarded):
    api=APIRouter(prefix='/api/reports')
    @api.get('/overview')
    async def overview(uid:int=Depends(actor)):
        def read():
            owner=store.workspace(uid)
            return finite_json({'summary':store.summary(uid,owner),
                    'servers':[dict(row) for row in store.db.execute('''SELECT id,name,ip,last_status,last_agent_status,last_cpu,last_ram,last_disk,last_checked_at
                        FROM servers WHERE owner_id=%s ORDER BY id''',(owner,),fetch='all')],
                    'configs':store.rankings(uid,owner,100),
                    'incidents':[dict(row) for row in store.incidents(uid,owner,'all',100)]})
        return await guarded(read)
    @api.get('/servers.csv')
    async def export(uid:int=Depends(actor)):
        def build():
            owner=store.workspace(uid)
            rows=store.db.execute('''SELECT name,ip,last_status,last_cpu,last_ram,last_disk,last_checked_at FROM servers WHERE owner_id=%s ORDER BY id''',(owner,),fetch='all')
            stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['نام','IP','وضعیت','CPU','RAM','Disk','زمان آخرین سنجش'])
            for row in rows:
                values=[]
                for value in row.values():
                    value='' if value is None else str(value)
                    if value.startswith(('=','+','-','@','\t','\r')):value="'"+value
                    values.append(value)
                writer.writerow(values)
            return '\ufeff'+stream.getvalue()
        text=await guarded(build)
        return Response(text,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="sonar-servers.csv"'})
    return api


async def observation_worker(db,stop):
    """Keep web-only installations collecting data; existing fresh bot samples win."""
    import asyncio
    import logging
    from server_stats import StatsManager
    from core import sec
    log=logging.getLogger(__name__)
    while not stop.is_set():
        try:
            await asyncio.to_thread(db.execute,"INSERT INTO pro_job_cursors(name,last_id) VALUES('web_observations',0) ON CONFLICT DO NOTHING")
            position=await asyncio.to_thread(db.execute,"SELECT last_id FROM pro_job_cursors WHERE name='web_observations'",None,'one')
            servers=await asyncio.to_thread(db.execute,'''SELECT id,owner_id,ip,port,username,password,ws_port FROM servers
                WHERE is_active=1 AND (last_checked_at IS NULL OR last_checked_at<NOW()-INTERVAL '90 seconds')
                ORDER BY (id>%s) DESC,id LIMIT 3''',(position['last_id'],),'all')
            if servers:await asyncio.to_thread(db.execute,"UPDATE pro_job_cursors SET last_id=%s WHERE name='web_observations'",(servers[-1]['id'],))
            for server in servers:
                if stop.is_set():break
                allowed,_=await asyncio.to_thread(db.check_access,server['owner_id'])
                if not allowed:continue
                try:
                    result=await asyncio.wait_for(StatsManager.check_full_stats(server['ip'],server['port'],server['username'],sec.decrypt(server['password'] or ''),ws_port=server['ws_port']),timeout=45)
                except Exception:
                    result={'status':'Unknown','error':'OBSERVATION_UNAVAILABLE'}
                await asyncio.to_thread(db.persist_server_observation,server,result)
        except asyncio.CancelledError:raise
        except Exception:log.warning('Web observation cycle unavailable',exc_info=False)
        try:await asyncio.wait_for(stop.wait(),timeout=30)
        except asyncio.TimeoutError:pass
