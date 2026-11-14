"""Owner-scoped report exports and time-bucketed observations shared by web clients."""
import math
import csv
import io
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,Query
from fastapi.responses import Response

WINDOWS=(1,3,12,24,48,168,720)

def finite_json(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {key:finite_json(item) for key,item in value.items()}
    if isinstance(value,list):return [finite_json(item) for item in value]
    return value

def server_history(store,uid,sid,hours=24):
    if hours not in WINDOWS:raise ValueError('بازه زمانی معتبر نیست')
    owner=store.workspace(uid)
    server=store.db.execute('SELECT id,name,last_status,last_checked_at,last_agent_status,last_cpu,last_ram,last_disk FROM servers WHERE id=%s AND owner_id=%s',(sid,owner),fetch='one')
    if not server:raise PermissionError('Server is not in your workspace')
    # Bound chart size while preserving the entire requested period, not just
    # the most recent LIMIT rows. Nulls remain null, never synthetic zeroes.
    rows=store.db.execute('''SELECT to_timestamp(floor(extract(epoch FROM created_at)/%s)*%s) AS created_at,
       AVG(cpu) cpu,AVG(ram) ram,AVG(disk) disk,MAX(traffic_gb) traffic_gb
       FROM server_stats WHERE server_id=%s AND created_at>=NOW()-(LEAST(%s,sonar_retention_days(%s)*24)*INTERVAL '1 hour')
       GROUP BY floor(extract(epoch FROM created_at)/%s) ORDER BY created_at''',
       (max(60,hours*5),max(60,hours*5),sid,hours,owner,max(60,hours*5)),fetch='all')
    samples=[]
    for row in rows:
        stamp=row['created_at']
        samples.append({**{key:float(row[key]) if row[key] is not None else None for key in ('cpu','ram','disk','traffic_gb')},
                        'time':(stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).isoformat()})
    for sample in samples:
        for key in ('cpu','ram','disk','traffic_gb'):
            value=sample[key]
            if value is not None and (not math.isfinite(value) or value<0 or (key!='traffic_gb' and value>100)):sample[key]=None
    latest=store.db.execute('SELECT cpu,ram,disk,created_at FROM server_stats WHERE server_id=%s ORDER BY created_at DESC LIMIT 1',(sid,),fetch='one')
    # Gauges show the exact latest observation, rather than the average of a
    # time bucket (which can disagree with the detail page's current values).
    current=None
    if latest:
        stamp=latest['created_at']
        current={key:float(latest[key]) if latest[key] is not None and math.isfinite(float(latest[key])) and 0<=float(latest[key])<=100 else None for key in ('cpu','ram','disk')}
        current['time']=(stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).isoformat()
    return {'server':dict(server),'latest':current,'samples':samples,'hours':hours,'retention_hours':int(store.db.execute('SELECT sonar_retention_days(%s) days',(owner,),fetch='one')['days'])*24,'temperature':None,'network':samples[-1]['traffic_gb'] if samples else None}


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
    @api.get('/snapshot.json')
    async def json_export(uid:int=Depends(actor)):
        from fastapi.encoders import jsonable_encoder
        from fastapi.responses import JSONResponse
        data=await overview(uid)
        return JSONResponse(jsonable_encoder(data),headers={'Content-Disposition':'attachment; filename="sonar-report.json"'})
    @api.get('/report.pdf')
    async def pdf_export(uid:int=Depends(actor)):
        from pro_pdf import generate_pdf
        owner=await guarded(store.workspace,uid)
        data=await guarded(generate_pdf,uid,owner,store)
        return Response(data,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="sonar-report.pdf"'})
    @api.get('/analytics')
    async def analytics(start:datetime,end:datetime,uid:int=Depends(actor)):
        return await guarded(analytic_window,store,uid,start,end)
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
                from notification_api import claim_sample
                if not await asyncio.to_thread(claim_sample,db,server['id']):continue
                try:
                    result=await asyncio.wait_for(StatsManager.check_full_stats(server['ip'],server['port'],server['username'],sec.decrypt(server['password'] or ''),ws_port=server['ws_port']),timeout=45)
                except Exception:
                    result={'status':'Unknown','error':'OBSERVATION_UNAVAILABLE'}
                await asyncio.to_thread(db.persist_server_observation,server,result)
        except asyncio.CancelledError:raise
        except Exception:log.warning('Web observation cycle unavailable',exc_info=False)
        try:await asyncio.wait_for(stop.wait(),timeout=30)
        except asyncio.TimeoutError:pass


def analytic_window(store,uid,start,end):
    from datetime import datetime,timezone
    from user_errors import UserError
    if start.tzinfo is None or end.tzinfo is None or not start<end or (end-start).total_seconds()>31*86400:
        raise UserError('بازه گزارش باید دارای منطقه زمانی و حداکثر ۳۱ روز باشد')
    if end.timestamp()>datetime.now(timezone.utc).timestamp()+60:raise UserError('انتهای گزارش نمی‌تواند در آینده باشد')
    owner=store.workspace(uid)
    servers=store.db.execute('''SELECT COUNT(*) samples,AVG(s.cpu) cpu_average,AVG(s.ram) ram_average,AVG(s.disk) disk_average,
        MIN(s.created_at) first_sample,MAX(s.created_at) last_sample FROM server_stats s JOIN servers r ON r.id=s.server_id
        WHERE r.owner_id=%s AND s.created_at AT TIME ZONE 'UTC'>=%s AND s.created_at AT TIME ZONE 'UTC'<%s AND s.created_at>=NOW()-(sonar_retention_days(r.owner_id)*INTERVAL '1 day')''',(owner,start,end),fetch='one')
    configs=store.db.execute('''SELECT COUNT(*) samples,COUNT(*) FILTER(WHERE ok) successful,
        AVG(ping) average_ms,MIN(ping) min_ms,MAX(ping) max_ms FROM pro_metrics WHERE owner_id=%s AND measured_at>=%s AND measured_at<%s AND measured_at>=NOW()-(sonar_retention_days(owner_id)*INTERVAL '1 day')''',(owner,start,end),fetch='one')
    regions=store.db.execute('''SELECT region,COUNT(ok) measured,COUNT(*) FILTER(WHERE ok IS NULL) unknown,
        AVG(CASE WHEN ok THEN 100.0 WHEN NOT ok THEN 0 END) sample_availability,
        AVG(latency_ms) average_ms,MIN(latency_ms) min_ms,MAX(latency_ms) max_ms,
        MIN(checked_at) first_sample,MAX(checked_at) last_sample FROM sonar_service_regions
        WHERE owner_id=%s AND checked_at>=%s AND checked_at<%s AND checked_at>=NOW()-(sonar_retention_days(owner_id)*INTERVAL '1 day') GROUP BY region''',(owner,start,end),fetch='all')
    incidents=store.db.execute('''SELECT COUNT(*) incidents,COALESCE(SUM(EXTRACT(EPOCH FROM
        LEAST(COALESCE(resolved_at,%s),%s)-GREATEST(opened_at,%s))),0) incident_seconds
        FROM pro_incidents WHERE owner_id=%s AND opened_at<%s AND COALESCE(resolved_at,%s)>%s''',
        (end,end,start,owner,end,end,start),fetch='one')
    return {'start':start,'end':end,'retention_days':int(store.db.execute('SELECT sonar_retention_days(%s) days',(owner,),fetch='one')['days']),'servers':servers,'configs':configs,'regions':regions,'incidents':incidents,
            'availability_basis':'measured samples; missing regional nodes are unknown; incident durations can overlap'}
