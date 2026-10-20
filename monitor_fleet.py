"""Opt-in, backward-compatible Iran monitoring pool.

No SSH, TLS or WebSocket transport is replaced. Only enrolled agents may be
selected. Shared non-admin endpoints require the server owner's opt-in and
administrator activation. No unrestricted cross-tenant server listing.
"""
from __future__ import annotations
import hashlib
import math
from datetime import datetime, timezone
from typing import Literal

POLICIES = ('latency', 'load', 'balanced')


def install_schema(db):
    with db.get_connection() as (conn, cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS monitor_fleet_nodes(
          server_id INTEGER PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
          offered_by BIGINT NOT NULL, approved_by BIGINT,
          enabled BOOLEAN NOT NULL DEFAULT FALSE,
          latency_ms DOUBLE PRECISION,
          cpu_pct DOUBLE PRECISION,
          last_probe_ok BOOLEAN,
          last_probe_at TIMESTAMPTZ,
          error_code TEXT,
          changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS monitor_fleet_policy(
          id SMALLINT PRIMARY KEY CHECK(id=1),
          strategy TEXT NOT NULL DEFAULT 'balanced' CHECK(strategy IN ('latency','load','balanced'))
        )''')
        cur.execute("INSERT INTO monitor_fleet_policy(id,strategy) VALUES(1,'balanced') ON CONFLICT DO NOTHING")
        cur.execute('ALTER TABLE monitor_fleet_policy ADD COLUMN IF NOT EXISTS last_server_id INTEGER NOT NULL DEFAULT 0')
        conn.commit()


def policy(db):
    row = db.execute('SELECT strategy FROM monitor_fleet_policy WHERE id=1',fetch='one')
    return row['strategy'] if row and row['strategy'] in POLICIES else 'balanced'


def available(db, admin_id, user_id=None):
    """Only opted-in Iran servers, never credentials or private ownership data."""
    uid = int(user_id or admin_id)
    owner = int(db.workspace_owner(uid))
    query = '''SELECT s.id,s.name,s.ip,s.ws_port,s.owner_id,s.location_type,
               s.last_cpu,s.last_agent_status,s.last_checked_at,
               f.enabled,f.approved_by,f.latency_ms,f.cpu_pct,f.last_probe_ok,f.last_probe_at,
               f.error_code
            FROM monitor_fleet_nodes f JOIN servers s ON s.id=f.server_id
            WHERE s.is_active=1 AND s.location_type='ir'
              AND (s.owner_id=%s OR s.owner_id=%s)
            ORDER BY s.id'''
    if uid==int(admin_id):
        query=query.replace('AND (s.owner_id=%s OR s.owner_id=%s)', '')
        rows=db.execute(query,fetch='all')
    else:
        rows=db.execute(query, (owner,int(admin_id)),fetch='all')
    result = [dict(r) for r in rows]
    if uid != int(admin_id):
        # Another tenant's node becomes visible only AFTER their offer is
        # approved and enabled. Never leak pending offers.
        result = [r for r in result if int(r['owner_id']) == owner or
                  (r['enabled'] and r['approved_by'] is not None)]
    return result


def offer(db, uid, server_id, enabled):
    uid=int(uid);server_id=int(server_id)
    owner=int(db.workspace_owner(uid))
    with db.get_connection() as (conn,cur):
        cur.execute('SELECT owner_id,location_type FROM servers WHERE id=%s AND owner_id=%s FOR UPDATE', (server_id,owner))
        row=cur.fetchone()
        if not row: raise PermissionError('Server outside workspace')
        if int(row['owner_id'])!=uid: raise PermissionError('Only the workspace owner may share its agent')
        if row['location_type']!='ir': raise ValueError('Iran node designation is required')
        if enabled:
            cur.execute('''INSERT INTO monitor_fleet_nodes(server_id,offered_by,enabled,approved_by)
                           VALUES (%s,%s,FALSE,NULL) ON CONFLICT(server_id) DO UPDATE
                           SET offered_by=EXCLUDED.offered_by, changed_at=NOW()''',(server_id,uid))
        else:
            cur.execute('DELETE FROM monitor_fleet_nodes WHERE server_id=%s AND offered_by=%s',(server_id,uid))
        conn.commit()


def approve(db, admin_id, server_id, enabled):
    with db.get_connection() as (conn,cur):
        cur.execute('''UPDATE monitor_fleet_nodes f SET enabled=%s,
            approved_by=CASE WHEN %s THEN %s ELSE NULL END,changed_at=NOW()
            FROM servers s WHERE s.id=f.server_id AND f.server_id=%s AND s.location_type='ir'
            RETURNING f.server_id''', (bool(enabled),bool(enabled),int(admin_id),int(server_id)))
        row=cur.fetchone()
        if not row:raise ValueError('Server has not been offered for monitoring')
        conn.commit()


def set_policy(db, strategy):
    if strategy not in POLICIES: raise ValueError('Unsupported balancing policy')
    db.execute('UPDATE monitor_fleet_policy SET strategy=%s WHERE id=1',(strategy,))


def record_probe(db, server_id, ok, latency_ms=None, cpu=None, error_code=None):
    # No raw exception strings or secrets in persistent health records.
    if error_code and (len(error_code)>80 or not error_code.replace('_','').isalnum()):
        error_code='AGENT_UNREACHABLE'
    db.execute('''UPDATE monitor_fleet_nodes SET last_probe_ok=%s,last_probe_at=NOW(),
         latency_ms=%s,cpu_pct=%s,error_code=%s WHERE server_id=%s''',
         (bool(ok),latency_ms if ok else None,cpu if ok else None,error_code,int(server_id)))


def score(item,strategy):
    """Lower is better; missing metrics are penalised, never turned into zero."""
    if item.get('last_probe_ok') is False: return math.inf
    if item.get('last_probe_at') is None: return math.inf
    last=item['last_probe_at']
    if last.tzinfo is None: last=last.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc)-last).total_seconds()>300: return math.inf
    ping = item.get('latency_ms')
    cpu = item.get('cpu_pct')
    try: ping=float(ping) if ping is not None and 0<=float(ping)<60000 else None
    except (TypeError,ValueError):ping=None
    try: cpu=float(cpu) if cpu is not None and 0<=float(cpu)<=100 else None
    except (TypeError,ValueError):cpu=None
    if strategy=='latency':return ping if ping is not None else math.inf
    if strategy=='load':return cpu if cpu is not None else math.inf
    if ping is None or cpu is None:return math.inf
    return .65*min(1,ping/450)+.35*(cpu/100)


def choose(rows,strategy='balanced',nonce=0):
    """Select only healthy approved agents; stable tie-break spreads loads."""
    eligible=[r for r in rows if r.get('enabled') and r.get('approved_by') is not None]
    eligible=[r for r in eligible if score(r,strategy)!=math.inf]
    if not eligible:return None
    return min(eligible,key=lambda r:(score(r,strategy), hashlib.blake2s(f'{r["id"]}:{nonce}'.encode(),digest_size=4).digest()))


def select_monitor_row(db,uid,admin_id):
    """Select an opted-in healthy node, or preserve legacy single Iran node."""
    try:
        candidate=choose(available(db,admin_id,uid),policy(db),int(uid))
        if candidate:
            row=db.execute('SELECT * FROM servers WHERE id=%s AND is_active=1',(int(candidate['id']),),fetch='one')
            if row:return row
    except Exception:
        # New fleet feature must not break the existing single-node fallback.
        import logging
        logging.getLogger(__name__).exception('Multi-agent selection failed; legacy fallback')
    owner=int(db.workspace_owner(uid))
    return db.execute('''SELECT * FROM servers WHERE is_monitor_node=1 AND is_active=1
       AND owner_id IN (%s,%s) ORDER BY (owner_id=%s) DESC,id LIMIT 1''',
       (owner,int(admin_id),owner),fetch='one')


def select_monitor_in_cursor(cur,uid,admin_id):
    """Reuse one DB connection inside legacy Telegram handlers."""
    try:
        cur.execute('SELECT owner_id FROM sonar_workspaces WHERE member_id=%s',(int(uid),))
        owner_row=cur.fetchone()
        owner=int(owner_row['owner_id']) if owner_row else int(uid)
        cur.execute('SELECT strategy FROM monitor_fleet_policy WHERE id=1')
        strategy_row=cur.fetchone()
        strategy=strategy_row['strategy'] if strategy_row and strategy_row['strategy'] in POLICIES else 'balanced'
        cur.execute('''SELECT s.id,s.owner_id,f.enabled,f.approved_by,f.last_probe_ok,
          f.last_probe_at,f.cpu_pct,f.latency_ms FROM monitor_fleet_nodes f
          JOIN servers s ON s.id=f.server_id WHERE s.is_active=1
          AND s.location_type='ir' AND s.owner_id IN (%s,%s)''',
          (owner,int(admin_id)))
        node=choose(cur.fetchall(),strategy,int(uid))
        if node:
            cur.execute('SELECT * FROM servers WHERE id=%s AND is_active=1',(int(node['id']),))
            found=cur.fetchone()
            if found:return found
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Fleet query failed; using legacy node')
    cur.execute('''SELECT * FROM servers WHERE is_monitor_node=1 AND is_active=1
           AND owner_id IN (%s,%s) ORDER BY (owner_id=%s) DESC,id LIMIT 1''',
           (owner if 'owner' in locals() else int(uid),int(admin_id),owner if 'owner' in locals() else int(uid)))
    return cur.fetchone()


async def fleet_heartbeat_job(context):
    """Refresh authenticated WSS telemetry with tight concurrency/time limits."""
    import asyncio
    from core import ServerMonitor
    from database import Database
    from settings import SUPER_ADMIN_ID
    if not SUPER_ADMIN_ID:return
    db=Database()
    rows=await asyncio.to_thread(available,db,SUPER_ADMIN_ID,SUPER_ADMIN_ID)
    items=[r for r in rows if r['enabled'] and r['approved_by'] is not None]
    # Rotate batches: operators with >60 pooled agents are never starved.
    cursor_row=await asyncio.to_thread(db.execute,'SELECT last_server_id FROM monitor_fleet_policy WHERE id=1',None,'one')
    cursor=int(cursor_row['last_server_id'] or 0) if cursor_row else 0
    after=[r for r in items if int(r['id'])>cursor]
    before=[r for r in items if int(r['id'])<=cursor]
    batch=(after+before)[:60]
    if batch:
        await asyncio.to_thread(db.execute,'UPDATE monitor_fleet_policy SET last_server_id=%s WHERE id=1',(int(batch[-1]['id']),))
    semaphore=asyncio.Semaphore(3)
    async def one(r):
        async with semaphore:
            start=asyncio.get_running_loop().time()
            try:
                result=await asyncio.wait_for(ServerMonitor.ws_send_command(r['ip'],int(r['ws_port']),None,{'action':'get_stats'},timeout=6),timeout=10)
                ok=isinstance(result,dict) and not result.get('error')
                if ok:
                    elapsed=(asyncio.get_running_loop().time()-start)*1000
                    await asyncio.to_thread(record_probe,db,r['id'],True,round(elapsed,1),result.get('cpu'))
                else:await asyncio.to_thread(record_probe,db,r['id'],False,None,None,'WSS_VERIFICATION_FAILED')
            except Exception:await asyncio.to_thread(record_probe,db,r['id'],False,None,None,'WSS_TIMEOUT')
    await asyncio.gather(*(one(r) for r in batch))


def install_job_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS monitor_fleet_install_jobs(
            id BIGSERIAL PRIMARY KEY, server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
            requested_by BIGINT NOT NULL, requested_port INTEGER NOT NULL,
            port_mode TEXT NOT NULL DEFAULT 'stored',
            state TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'queued',
            error_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )''')
        cur.execute("ALTER TABLE monitor_fleet_install_jobs ADD COLUMN IF NOT EXISTS port_mode TEXT NOT NULL DEFAULT 'stored'")
        cur.execute('CREATE INDEX IF NOT EXISTS monitor_fleet_jobs_pending ON monitor_fleet_install_jobs(state,created_at)')
        conn.commit()


def queue_installs(db,admin_id,server_ids,mode):
    """No cross-account installation: owner must be the main admin."""
    import secrets
    if mode not in ('stored','automatic'):raise ValueError('Invalid port mode')
    ids=list(dict.fromkeys(int(n) for n in server_ids))
    if not ids or len(ids)>12:raise ValueError('Select 1–12 nodes')
    with db.get_connection() as (conn,cur):
        cur.execute('SELECT id,ws_port,owner_id,location_type,is_active FROM servers WHERE id=ANY(%s) FOR UPDATE',(ids,))
        rows=cur.fetchall()
        if (len(rows)!=len(ids) or any(int(r['owner_id'])!=int(admin_id) or r['location_type']!='ir' or not r['is_active'] for r in rows)):
            raise PermissionError('Installation requires administrator-owned, enabled Iran servers')
        cur.execute("SELECT server_id FROM monitor_fleet_install_jobs WHERE server_id=ANY(%s) AND state IN ('queued','running')",(ids,))
        if cur.fetchone():raise ValueError('An installation is already pending for at least one selected server')
        job_ids=[]
        for row in rows:
            port=int(row['ws_port'] or 8080) if mode=='stored' else secrets.randbelow(40000)+20000
            cur.execute('''INSERT INTO monitor_fleet_install_jobs(server_id,requested_by,requested_port,port_mode)
                           VALUES(%s,%s,%s,%s) RETURNING id''',(row['id'],int(admin_id),port,mode))
            job_ids.append(int(cur.fetchone()['id']))
        conn.commit()
    return job_ids


def claim_install(db,admin_id):
    with db.get_connection() as (conn,cur):
        cur.execute("""UPDATE monitor_fleet_install_jobs SET state='needs_review',stage='stale_worker',updated_at=NOW()
              WHERE state='running' AND updated_at<NOW()-INTERVAL '25 minutes'""")
        cur.execute('''SELECT id,server_id,requested_port,port_mode FROM monitor_fleet_install_jobs
                    WHERE state='queued' AND requested_by=%s ORDER BY created_at,id
                    FOR UPDATE SKIP LOCKED LIMIT 1''',(int(admin_id),))
        row=cur.fetchone()
        if row:
            cur.execute("UPDATE monitor_fleet_install_jobs SET state='running',stage='preflight',updated_at=NOW() WHERE id=%s",(row['id'],))
        conn.commit()
        return dict(row) if row else None


def job_state(db,job_id,stage,state=None,error_code=None):
    stage=str(stage)[:50]
    if not stage.replace('_','').isalnum():stage='unknown'
    if error_code:
        error_code=str(error_code).split(':',1)[0][:60].upper()
        if not error_code.replace('_','').isalnum():error_code='INSTALL_FAILED'
    db.execute('''UPDATE monitor_fleet_install_jobs SET stage=%s,
       state=COALESCE(%s,state),error_code=%s,updated_at=NOW() WHERE id=%s''',
       (stage,state,error_code,int(job_id)))


async def fleet_install_job(context):
    import asyncio
    from core import ServerMonitor, sec
    from database import Database
    from settings import SUPER_ADMIN_ID
    if not SUPER_ADMIN_ID:return
    db=Database()
    task=await asyncio.to_thread(claim_install,db,SUPER_ADMIN_ID)
    if not task:return
    job_id=task['id']
    try:
        server=await asyncio.to_thread(db.execute,
          'SELECT * FROM servers WHERE id=%s AND owner_id=%s AND location_type=%s',
          (task['server_id'],SUPER_ADMIN_ID,'ir'),'one')
        if not server:
            await asyncio.to_thread(job_state,db,job_id,'not_found','failed','SERVER_NOT_FOUND');return
        def install():
            def progress(stage,_details):job_state(db,job_id,stage)
            return ServerMonitor.install_agent_service(server['ip'],int(server['port']),server['username'],
               sec.decrypt(server['password']),int(task['requested_port']),progress=progress)
        ok,detail=await asyncio.to_thread(install)
        # Only the port-preflight collision is safely retryable: deployment
        # aborts before modifying the remote files or managed service.
        if not ok and task.get('port_mode')=='automatic' and 'AGENT_PORT_CONFLICT' in str(detail):
            import secrets
            for _attempt in range(2):
                new_port=secrets.randbelow(40000)+20000
                if new_port==task['requested_port']:continue
                task['requested_port']=new_port
                await asyncio.to_thread(db.execute,
                    'UPDATE monitor_fleet_install_jobs SET requested_port=%s,stage=%s,updated_at=NOW() WHERE id=%s',
                    (new_port,'retry_free_port',job_id))
                ok,detail=await asyncio.to_thread(install)
                if ok or 'AGENT_PORT_CONFLICT' not in str(detail):break
        if not ok:
            await asyncio.to_thread(job_state,db,job_id,'failed','failed',str(detail));return
        # Hard requirement: verified TLS+Agent token, not just a listening TCP socket.
        start=asyncio.get_running_loop().time()
        response=await asyncio.wait_for(ServerMonitor.ws_send_command(server['ip'],int(task['requested_port']),None,{'action':'get_stats'},timeout=9),timeout=14)
        actual_rtt=round((asyncio.get_running_loop().time()-start)*1000,1)
        if not isinstance(response,dict) or response.get('error'):
            await asyncio.to_thread(job_state,db,job_id,'wss_unverified','needs_review','WSS_HEALTH_FAILED');return
        await asyncio.to_thread(db.update_server_ws_port,int(server['id']),int(task['requested_port']),int(SUPER_ADMIN_ID))
        await asyncio.to_thread(offer,db,int(SUPER_ADMIN_ID),int(server['id']),True)
        await asyncio.to_thread(approve,db,int(SUPER_ADMIN_ID),int(server['id']),True)
        await asyncio.to_thread(record_probe,db,int(server['id']),True,actual_rtt,response.get('cpu'))
        await asyncio.to_thread(job_state,db,job_id,'wss_verified','completed')
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('Fleet installation failed')
        await asyncio.to_thread(job_state,db,job_id,'failed','failed',type(exc).__name__)
