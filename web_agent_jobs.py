"""Persistent, tenant-scoped web Agent installation queue.

A web process may restart; jobs are stored in PostgreSQL. Interrupted jobs
are explicitly marked failed, never blindly replayed after partial remote
installation (manual retry is safe once the operator inspects the host).
"""
from __future__ import annotations
import asyncio
import logging

log = logging.getLogger(__name__)


def install_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS web_agent_install_jobs (
        id BIGSERIAL PRIMARY KEY,
        server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
        owner_id BIGINT NOT NULL,
        requested_by BIGINT NOT NULL,
        port INTEGER NOT NULL CHECK(port BETWEEN 1024 AND 65535),
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','success','failed')),
        stage TEXT NOT NULL DEFAULT 'queued',
        error_code TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS web_agent_jobs_owner_idx
            ON web_agent_install_jobs(owner_id,server_id,id DESC)''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS web_agent_one_pending
            ON web_agent_install_jobs(server_id) WHERE state IN ('pending','running')''')
    # A running operation may have changed the remote host before the crash.
    db.execute('''UPDATE web_agent_install_jobs SET state='failed',stage='interrupted',
        error_code='WORKER_RESTARTED_VERIFY_BEFORE_RETRY',updated_at=NOW()
        WHERE state='running' AND updated_at < NOW()-INTERVAL '30 minutes' ''')


def queue(db, uid, server_id, ws_port, admin_id):
    from access_policy import valid_agent_port
    port = valid_agent_port(ws_port)
    saved = db.get_server_for_user(int(server_id), uid, admin_id)
    if not saved:
        raise PermissionError('Server is not in your workspace')
    owner = int(saved['owner_id'])
    row = db.execute('''INSERT INTO web_agent_install_jobs(server_id,owner_id,requested_by,port)
        VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',
        (int(server_id),owner,int(uid),port),fetch='one')
    if not row:raise ValueError('An Agent installation is already queued or running')
    return int(row['id'])


def list_for_server(db, uid, server_id, admin_id):
    saved=db.get_server_for_user(server_id,uid,admin_id)
    if not saved:
        raise PermissionError('Server is not in your workspace')
    owner=int(saved['owner_id'])
    return [dict(r) for r in db.execute('''SELECT id,server_id,port,state,stage,error_code,
        created_at,updated_at FROM web_agent_install_jobs
        WHERE server_id=%s AND owner_id=%s ORDER BY id DESC LIMIT 12''',
        (int(server_id),owner),fetch='all')]


def claim(db):
    # SKIP LOCKED means multiple web workers never run the same install.
    with db.get_connection() as (conn,cur):
        cur.execute('''SELECT id FROM web_agent_install_jobs
            WHERE state='pending' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1''')
        row=cur.fetchone()
        if not row:
            conn.commit();return None
        cur.execute('''UPDATE web_agent_install_jobs SET state='running',
            stage='connecting',updated_at=NOW() WHERE id=%s''',(row['id'],))
        cur.execute('''SELECT j.id,j.port,j.server_id,j.owner_id,s.ip,s.port AS ssh_port,
            s.username,s.password FROM web_agent_install_jobs j
            JOIN servers s ON s.id=j.server_id
            WHERE j.id=%s AND s.owner_id=j.owner_id AND s.is_active=1''',(row['id'],))
        job=cur.fetchone()
        conn.commit()
        return dict(job) if job else {'id':row['id'],'missing':True}


def mark(db, job_id, stage, state=None, error=None):
    # Store an allowlisted error label only: never record SSH passwords or output.
    import re
    cleaned = re.sub(r'[^A-Z0-9_]', '_', str(error or '').upper())[:72] if error else None
    db.execute('''UPDATE web_agent_install_jobs SET stage=%s,
        state=COALESCE(%s,state),error_code=%s,updated_at=NOW() WHERE id=%s''',
        (str(stage)[:50],state,cleaned,int(job_id)))


def process_one(db, job):
    if job.get('missing'):
        mark(db,job['id'],'server_missing','failed','SERVER_NOT_FOUND');return
    from core import ServerMonitor,sec
    try:
        def progress(stage, detail):
            try:mark(db,job['id'],stage)
            except Exception:log.exception('Agent install stage database error')
        ok, detail=ServerMonitor.install_agent_service(
            job['ip'],int(job['ssh_port']),job['username'],sec.decrypt(job['password']),
            int(job['port']),progress=progress)
        mark(db,job['id'],'verified' if ok else 'failed',
             'success' if ok else 'failed',None if ok else str(detail).split(':',1)[0])
        if ok: db.update_status(int(job['server_id']),'Online')
    except Exception as exc:
        log.exception('Web Agent install failed job=%s type=%s',job['id'],type(exc).__name__)
        mark(db,job['id'],'failed','failed','INSTALL_EXCEPTION')


def recover_interrupted(db):
    # Periodic recovery handles restarts that occurred less than 30 min after
    # a job began. The operation is never re-run automatically after a crash.
    db.execute('''UPDATE web_agent_install_jobs SET state='failed',stage='interrupted',
        error_code='WORKER_STALE_VERIFY_BEFORE_RETRY',updated_at=NOW()
        WHERE state='running' AND updated_at < NOW()-INTERVAL '60 minutes' ''')


async def worker(db, stop):
    passes=0
    while not stop.is_set():
        try:
            passes+=1
            if passes%12 == 1:
                await asyncio.to_thread(recover_interrupted,db)
            job=await asyncio.to_thread(claim,db)
            if job:
                await asyncio.to_thread(process_one,db,job)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Web Agent queue worker failed')
        try:await asyncio.wait_for(stop.wait(),timeout=6)
        except asyncio.TimeoutError:pass
