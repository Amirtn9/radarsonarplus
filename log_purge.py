"""Admin-authorized, durable purge jobs with explicit results for every source."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from log_management import LogStore

log=logging.getLogger(__name__)
HELPER=Path(__file__).resolve().parent/'scripts'/'sonar_log_maintenance.py'
GOOD={'cleared','not_present'}


class PurgeScopeConflict(Exception):
    """An existing destructive job cannot silently acquire a different scope."""


def request_local():
    """No privilege escalation from the hardened web service."""
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(85)
        client.connect('/run/sonar-log-maintenance.sock')
        client.sendall(b'{"action":"purge_local","confirmation":"DELETE ALL LOGS"}\n')
        client.shutdown(socket.SHUT_WR)
        data=bytearray()
        while True:
            block=client.recv(4096)
            if not block:break
            data.extend(block)
            if len(data)>65536:raise ValueError('Invalid maintenance response size')
    result=json.loads(data)
    if not isinstance(result,dict) or not isinstance(result.get('sources'),dict):
        raise ValueError('Invalid maintenance response')
    return result


def request_remote(server):
    from core import ServerMonitor,sec
    # Constant reviewed program, carried inside the existing SSH transport;
    # no caller-controlled shell arguments, no replay after a command is sent.
    command='python3 -I -c '+shlex.quote(HELPER.read_text())+' --agent'
    ok,output=ServerMonitor._run_ssh_command(server['ip'],int(server.get('port') or 22),
        server['username'],sec.decrypt(server['password'] or ''),command,timeout=85)
    if not ok:return {'ok':False,'status':'unavailable','error':'SSH_MAINTENANCE_FAILED'}
    try:
        value=json.loads(output)
        if isinstance(value,dict) and isinstance(value.get('sources'),dict):return value
    except (ValueError,TypeError):pass
    return {'ok':False,'status':'unavailable','error':'INVALID_AGENT_MAINTENANCE_RESPONSE'}


class PurgeJobs:
    def __init__(self,db,admin_id):
        self.db=db;self.logs=LogStore(db,admin_id)

    def create(self,actor,confirmation,include_remote=False):
        self.logs.require_admin(actor)
        if confirmation!='DELETE ALL LOGS':raise ValueError('Confirmation required')
        with self.db.get_connection() as (conn,cur):
            # One global destructive job at a time, including pending jobs.
            cur.execute('SELECT pg_advisory_xact_lock(535314)')
            cur.execute("SELECT id,include_remote FROM pro_log_purge_jobs WHERE state IN ('pending','running') LIMIT 1")
            active=cur.fetchone()
            if active:
                if bool(active['include_remote'])!=bool(include_remote):raise PurgeScopeConflict()
                conn.commit();return {'id':str(active['id']),'existing':True,'include_remote':bool(include_remote)}
            job_id=str(uuid.uuid4())
            cur.execute('INSERT INTO pro_log_purge_jobs(id,actor_id,include_remote) VALUES(%s,%s,%s)',
                        (job_id,actor,bool(include_remote)))
            conn.commit()
        return {'id':job_id,'existing':False,'include_remote':bool(include_remote)}

    def read(self,actor,job_id):
        self.logs.require_admin(actor)
        try:job_id=str(uuid.UUID(job_id))
        except (ValueError,TypeError,AttributeError):raise ValueError('Invalid purge ID')
        row=self.db.execute('SELECT id,state,include_remote,result,created_at,updated_at FROM pro_log_purge_jobs WHERE id=%s',
                            (job_id,),fetch='one')
        if not row:raise ValueError('Purge job not found')
        return dict(row)

    def recover(self):
        # Never repeat a partially completed destructive job after restart.
        self.db.execute("UPDATE pro_log_purge_jobs SET state='interrupted',updated_at=NOW() WHERE state='running'")

    def claim(self):
        with self.db.get_connection() as (conn,cur):
            cur.execute("SELECT * FROM pro_log_purge_jobs WHERE state='pending' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1")
            row=cur.fetchone()
            if row:cur.execute("UPDATE pro_log_purge_jobs SET state='running',updated_at=NOW() WHERE id=%s",(row['id'],))
            conn.commit()
        return dict(row) if row else None

    def save(self,job_id,result,state='running'):
        self.db.execute('UPDATE pro_log_purge_jobs SET state=%s,result=%s::jsonb,updated_at=NOW() WHERE id=%s',
                        (state,json.dumps(result),str(job_id)))

    def run(self,job):
        result={'sources':{},'remote':{}}
        try:
            self.logs.require_admin(job['actor_id'])
            cleared=self.logs.clear(job['actor_id'],'DELETE ALL LOGS')
            result['sources']['database']={'status':'cleared'}
            result['sources']['retry_spool']={'status':cleared['retry_spool']}
            self.save(job['id'],result)
            try:result['sources'].update(request_local()['sources'])
            except Exception as exc:
                result['sources']['local_files_and_journals']={'status':'unavailable','error':type(exc).__name__}
            if os.getenv('SONAR_LOG_DIR','/var/log/sonar')!='/var/log/sonar':
                result['sources']['custom_log_directory']={'status':'unsupported'}
            self.save(job['id'],result)
            if job['include_remote']:
                servers=self.db.execute('SELECT id,ip,port,username,password FROM servers ORDER BY id',fetch='all')
                # Bound concurrency while reporting every saved host, including offline ones.
                with ThreadPoolExecutor(max_workers=3,thread_name_prefix='sonar-log-purge') as pool:
                    pending={pool.submit(request_remote,dict(server)):str(server['id']) for server in servers}
                    for future in as_completed(pending):
                        try:row=future.result()
                        except Exception as exc:row={'ok':False,'status':'unavailable','error':type(exc).__name__}
                        result['remote'][pending[future]]=row
                        self.save(job['id'],result)
            else:result['remote_requested']=False
            complete=all(row.get('status') in GOOD for row in result['sources'].values()) and all(
                row.get('ok') is True for row in result['remote'].values())
            result['ok']=complete
            result['atomic_scope']='database_only'
            self.save(job['id'],result,'complete' if complete else 'partial')
        except Exception as exc:
            result['error']=type(exc).__name__
            self.save(job['id'],result,'failed')


async def worker(db,admin_id,stop):
    def serve():
        jobs=PurgeJobs(db,admin_id)
        while not stop.is_set():
            try:
                # A session lock prevents a second Web process from declaring
                # a live leader's job interrupted or executing another purge.
                with db.get_connection() as (conn,cur):
                    cur.execute('SELECT pg_try_advisory_lock(535315) AS leader')
                    leader=cur.fetchone()['leader'];conn.commit()
                    if leader:
                        try:
                            jobs.recover()
                            while not stop.is_set():
                                cur.execute('SELECT 1');conn.commit()
                                job=jobs.claim()
                                if job:jobs.run(job)
                                else:time.sleep(0.25)
                        finally:
                            cur.execute('SELECT pg_advisory_unlock(535315)');conn.commit()
            except Exception:log.warning('Log purge worker unavailable',exc_info=False)
            if not stop.is_set():time.sleep(1)
    await asyncio.to_thread(serve)
