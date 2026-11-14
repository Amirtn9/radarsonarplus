"""Iran-first independent HTTP/TCP observations through approved TLS Agents."""
from __future__ import annotations
import asyncio
import math
from dataclasses import asdict
from psycopg2.extras import Json
from service_probes import ProbeResult


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_service_regions (
            id BIGSERIAL PRIMARY KEY,owner_id BIGINT NOT NULL,service_id BIGINT NOT NULL REFERENCES pro_services(id) ON DELETE CASCADE,
            region VARCHAR(12) NOT NULL CHECK(region IN ('ir','ext','central')),
            node_id BIGINT,ok BOOLEAN,latency_ms DOUBLE PRECISION,detail VARCHAR(180) NOT NULL,
            diagnostics JSONB NOT NULL DEFAULT '{}',checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
            CREATE INDEX IF NOT EXISTS sonar_service_region_history ON sonar_service_regions(owner_id,service_id,region,checked_at DESC)''')
        conn.commit()


async def check_regions(db,item,local_result):
    from settings import SUPER_ADMIN_ID
    from monitor_fleet import available_relay_nodes
    from ws_client import GLOBAL_WS_POOL
    nodes=await asyncio.to_thread(available_relay_nodes,db,SUPER_ADMIN_ID,user_id=item['owner_id'])
    observations=[dict(region='central',node_id=None,**asdict(local_result))]
    primary=None
    for region in ('ir','ext'):
        candidates=[n for n in nodes if n['location_type']==region]
        result={'ok':None,'latency_ms':None,'detail':'REGION_NODE_UNAVAILABLE','diagnostics':{}}
        node=candidates[0] if candidates else None
        if node:
            try:
                token,cert=await asyncio.to_thread(db.get_agent_transport,node['ip'],node['ws_port'])
                payload=await asyncio.wait_for(GLOBAL_WS_POOL.request(node['ip'],node['ws_port'],token,
                    {'action':'service_probe','kind':item['kind'],'target':item['target'],
                     'timeout':item['timeout_seconds'],'expected_status':item.get('expected_status')},
                    timeout=item['timeout_seconds']+3,retries=0,tls_certificate=cert),timeout=item['timeout_seconds']+5)
                if type(payload.get('ok')) is bool and isinstance(payload.get('detail'),str):
                    latency=payload.get('latency_ms')
                    if latency is not None and (type(latency) not in (int,float) or not math.isfinite(latency) or latency<0):raise ValueError('Invalid latency')
                    diagnostics=payload.get('diagnostics') or {}
                    # Only fixed numerical/certificate metadata; never arbitrary remote text.
                    diagnostics={k:v for k,v in diagnostics.items() if k in ('http_status','dns_ms','connect_ms','response_ms','tls_expires_at','tls_verified') and ((type(v) in (int,float) and math.isfinite(v)) or type(v) in (bool,type(None)) or (isinstance(v,str) and len(v)<=100))}
                    result={'ok':payload['ok'],'latency_ms':latency,'detail':payload['detail'][:180],'diagnostics':diagnostics}
            except Exception:
                result['detail']='REGION_PROBE_UNAVAILABLE'
        if region=='ir' and result['ok'] is not None:primary=ProbeResult(result['ok'],result['latency_ms'],result['detail'],result['diagnostics'])
        observations.append({'region':region,'node_id':node['id'] if node else None,**result})
    def persist():
        with db.get_connection() as (conn,cur):
            for result in observations:
                cur.execute('''INSERT INTO sonar_service_regions(owner_id,service_id,region,node_id,ok,latency_ms,detail,diagnostics)
                   SELECT %s,%s,%s,%s,%s,%s,%s,%s WHERE EXISTS(SELECT 1 FROM pro_services WHERE id=%s AND owner_id=%s AND enabled=TRUE AND updated_at=%s)''',
                   (item['owner_id'],item['id'],result['region'],result['node_id'],result['ok'],result['latency_ms'],result['detail'],Json(result.get('diagnostics') or {}),item['id'],item['owner_id'],item['updated_at']))
            cur.execute("DELETE FROM sonar_service_regions WHERE checked_at<NOW()-INTERVAL '30 days'")
            conn.commit()
    await asyncio.to_thread(persist)
    # If no Iranian measurement exists, the explicitly labelled central result
    # retains existing installations' monitoring; missing regions stay unknown.
    return primary or local_result


def report(pro,uid,sid,days=7):
    owner=pro.workspace(uid)
    if not pro.db.execute('SELECT 1 FROM pro_services WHERE id=%s AND owner_id=%s',(sid,owner),fetch='one'):raise PermissionError('Service unavailable')
    rows=pro.db.execute('''SELECT region,COUNT(*) samples,COUNT(ok) measured,COUNT(*) FILTER(WHERE ok IS NULL) unknown,
       AVG(CASE WHEN ok THEN 100.0 WHEN NOT ok THEN 0 END) sample_availability,
       AVG(latency_ms) average_ms,MIN(latency_ms) min_ms,MAX(latency_ms) max_ms,
       MAX(checked_at) last_checked FROM sonar_service_regions
       WHERE owner_id=%s AND service_id=%s AND checked_at>=NOW()-(LEAST(%s,sonar_retention_days(owner_id))*INTERVAL '1 day') GROUP BY region ORDER BY region''',(owner,sid,days),fetch='all')
    history=pro.db.execute('''SELECT region,ok,latency_ms,detail,diagnostics,checked_at FROM sonar_service_regions
       WHERE owner_id=%s AND service_id=%s AND checked_at>=NOW()-(LEAST(%s,sonar_retention_days(owner_id))*INTERVAL '1 day') ORDER BY checked_at DESC LIMIT 1500''',(owner,sid,days),fetch='all')
    return {'summary':rows,'history':history,'days':days,'primary_region':'ir','central_fallback':True}
