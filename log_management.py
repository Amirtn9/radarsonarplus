"""Bounded asynchronous application logs and transactional database-log management.

System journals, remote hosts and rotated files are separate diagnostic sources;
this module never deletes those or any operational application tables.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from logging_config import redact

LOCK_ID = 535313
LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
CATEGORIES = ('api', 'node', 'authentication', 'database', 'system', 'user_activity')


def install_schema(db):
    with db.get_connection() as (conn, cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS pro_application_logs (
            id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL,
            level VARCHAR(10) NOT NULL, category VARCHAR(20) NOT NULL,
            source VARCHAR(120) NOT NULL, message TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'recorded')''')
        cur.execute('''CREATE INDEX IF NOT EXISTS pro_application_logs_time
            ON pro_application_logs(created_at DESC,id DESC)''')
        cur.execute('''CREATE INDEX IF NOT EXISTS pro_application_logs_filter
            ON pro_application_logs(category,level,created_at DESC)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS pro_log_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            cleared_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity')''')
        cur.execute('INSERT INTO pro_log_state(id) VALUES(1) ON CONFLICT DO NOTHING')
        conn.commit()


def install_reliability_schema(db):
    """Migration 14: deduplicate durable replay, including uncertain commits."""
    with db.get_connection() as (conn,cur):
        cur.execute('ALTER TABLE pro_application_logs ADD COLUMN IF NOT EXISTS event_id UUID')
        cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS pro_application_logs_event ON pro_application_logs(event_id)')
        cur.execute('''CREATE TABLE IF NOT EXISTS pro_log_purge_jobs (
            id UUID PRIMARY KEY,actor_id BIGINT NOT NULL,include_remote BOOLEAN NOT NULL,
            state VARCHAR(20) NOT NULL DEFAULT 'pending',result JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
        cur.execute('CREATE INDEX IF NOT EXISTS pro_log_purge_pending ON pro_log_purge_jobs(created_at) WHERE state=\'pending\'')
        conn.commit()


def category_for(source):
    source = source.lower()
    for category, fragments in (
        ('authentication', ('auth', 'login')),
        ('database', ('database', 'psycopg', 'migration')),
        ('node', ('agent', 'ws_client', 'fleet', 'ssh')),
        ('api', ('portal', 'api', 'uvicorn', 'httpx')),
    ):
        if any(fragment in source for fragment in fragments):
            return category
    return 'system'


def time_bounds(window='24', start=None, end=None):
    now = datetime.now(timezone.utc)
    if window not in ('24', '48', '168', 'all', 'custom'):
        raise ValueError('Invalid log window')
    if window == 'custom':
        if start is None or end is None:
            raise ValueError('Custom window requires both dates')
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError('Log dates must include a timezone')
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        if start >= end:
            raise ValueError('Start must precede end')
        return start, end
    return (None if window == 'all' else now-timedelta(hours=int(window))), now


class LogStore:
    def __init__(self, db, admin_id):
        self.db, self.admin_id = db, int(admin_id or 0)

    def require_admin(self, actor):
        if not self.admin_id or int(actor) != self.admin_id:
            raise PermissionError('Only the system administrator can manage logs')

    def list(self, actor, window='24', start=None, end=None, level='', category='', page=1, per_page=50):
        self.require_admin(actor)
        if level and level not in LEVELS:
            raise ValueError('Invalid log level')
        if category and category not in CATEGORIES:
            raise ValueError('Invalid log category')
        if not 1 <= page <= 10000 or not 1 <= per_page <= 100:
            raise ValueError('Invalid pagination')
        start, end = time_bounds(window, start, end)
        clauses, args = ['created_at <= %s'], [end]
        if start:
            clauses.append('created_at >= %s'); args.append(start)
        if level:
            clauses.append('level = %s'); args.append(level)
        if category:
            clauses.append('category = %s'); args.append(category)
        where = ' AND '.join(clauses)
        # UNION ALL preserves the existing audit events without duplicating them.
        source = '''(SELECT id,created_at,level,category,source,message,status
            FROM pro_application_logs UNION ALL
            SELECT id,created_at,'INFO','user_activity','audit',
                event || ' | ' || target || ' | ' || detail,'recorded' FROM pro_audit) logs'''
        with self.db.get_connection() as (conn, cur):
            # Keep purge out of this read transaction, including audit rows.
            cur.execute('SELECT pg_advisory_xact_lock_shared(%s)', (LOCK_ID,))
            cur.execute(f'SELECT COUNT(*) AS total FROM {source} WHERE {where}', args)
            total = cur.fetchone()['total']
            cur.execute(f'''SELECT * FROM {source} WHERE {where}
                ORDER BY created_at DESC,source,id DESC LIMIT %s OFFSET %s''',
                (*args, per_page, (page-1)*per_page))
            rows = [dict(row) for row in cur.fetchall()]
            conn.commit()
        for row in rows:
            # Legacy audit details may predate write-time redaction.
            row['message'] = redact(row['message'])
        return {'items': rows, 'total': total, 'page': page, 'per_page': per_page}

    def clear(self, actor, confirmation):
        self.require_admin(actor)
        if confirmation != 'DELETE ALL LOGS':
            raise ValueError('Type DELETE ALL LOGS to confirm')
        with self.db.get_connection() as (conn, cur):
            cur.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
            # Block concurrent audit INSERTs, too. Never TRUNCATE CASCADE.
            cur.execute('LOCK TABLE pro_application_logs,pro_audit IN SHARE ROW EXCLUSIVE MODE')
            cur.execute('UPDATE pro_log_state SET cleared_at=clock_timestamp() WHERE id=1 RETURNING cleared_at')
            cutoff=cur.fetchone()['cleared_at']
            cur.execute('DELETE FROM pro_application_logs')
            cur.execute('DELETE FROM pro_audit')
            conn.commit()
        # Old queued/retry records are also removed locally. PostgreSQL's
        # watermark remains authoritative for other processes and late batches.
        from log_spool import LogSpool
        try:
            spool=LogSpool()
            try:spool.discard_before(cutoff)
            finally:spool.close()
            spool_status='cleared'
        except Exception:
            spool_status='unavailable'
        return {'ok': True, 'scope': 'database_logs','retry_spool':spool_status}

    def write(self, rows):
        if not rows:
            return
        with self.db.get_connection() as (conn, cur):
            cur.execute('SELECT pg_advisory_xact_lock_shared(%s)', (LOCK_ID,))
            for row in rows:
                row=tuple(row) if len(row)==7 else (*row,str(uuid.uuid4()))
                # Queued pre-purge records cannot reappear after deletion,
                # including records queued by another web/bot process.
                cur.execute('''INSERT INTO pro_application_logs
                    (created_at,level,category,source,message,status,event_id)
                    SELECT %s,%s,%s,%s,%s,%s,%s FROM pro_log_state
                    WHERE id=1 AND cleared_at < %s
                    ON CONFLICT(event_id) DO NOTHING''', (*row, row[0]))
            conn.commit()

    def cleanup(self):
        with self.db.get_connection() as (conn, cur):
            cur.execute('SELECT pg_advisory_xact_lock_shared(%s)', (LOCK_ID,))
            # Bounded batches avoid long transactions on busy installations.
            for table, days in (('pro_application_logs', 30), ('pro_audit', 90)):
                cur.execute(f'''DELETE FROM {table} WHERE id IN
                    (SELECT id FROM {table} WHERE created_at < NOW()-(%s*INTERVAL '1 day')
                     ORDER BY created_at LIMIT 10000)''', (days,))
            conn.commit()


class DatabaseLogHandler(logging.Handler):
    """Never wait for PostgreSQL on the event loop or recursively log DB errors."""
    def __init__(self, store):
        super().__init__(logging.DEBUG)
        self.store = store
        self.pending = queue.Queue(maxsize=2000)
        self.stop = threading.Event()
        self.dropped = 0
        self.failures = 0
        self.maintenance_failures = 0
        from log_spool import LogSpool
        self.spool = None
        self.spool_error = None
        self.durability_failures = 0
        self.retry_pending = 0
        self.durable = os.getenv('SONAR_LOG_DURABLE','1')!='0'
        try:self.spool=LogSpool()
        except Exception as exc:self.spool_error=type(exc).__name__
        self.worker = threading.Thread(target=self._run, name='sonar-log-writer', daemon=True)
        self.worker.start()

    def emit(self, record):
        if self.stop.is_set() or threading.current_thread() is self.worker:
            return
        try:
            message = self.format(record)
            row = (datetime.fromtimestamp(record.created, timezone.utc), record.levelname[:10],
                   category_for(record.name), record.name[:120], redact(message)[:16000],
                   'failed' if record.levelno >= logging.ERROR else 'recorded',str(uuid.uuid4()))
            persisted=False
            if self.durable:
                try:
                    if self.spool is None:raise RuntimeError('Retry storage unavailable')
                    self.spool.add([row]);persisted=True
                except Exception as exc:
                    self.durability_failures+=1
                    self.spool_error=type(exc).__name__
            try:self.pending.put_nowait(row)
            except queue.Full:
                if not persisted:
                    if self.spool is None:raise RuntimeError('Retry storage unavailable')
                    self.spool.add([row])
        except Exception:
            self.dropped += 1

    def _run(self):
        maintenance = 0
        failure_notice = 0
        retry_at = 0
        retry_delay = 1
        offline = False
        retry_rows = []
        while not self.stop.is_set() or not self.pending.empty() or retry_rows:
            rows=retry_rows
            retry_rows=[]
            if not rows:
                try:
                    rows.append(self.pending.get(timeout=0.25))
                except queue.Empty:
                    pass
                while len(rows) < 100:
                    try:rows.append(self.pending.get_nowait())
                    except queue.Empty:break
            try:
                if rows:
                    if self.stop.is_set() or (offline and time.monotonic()<retry_at):
                        if self.spool is None:raise RuntimeError('Retry storage unavailable')
                        self.spool.add(rows)
                    else:
                        try:
                            self.store.write(rows)
                            if self.spool:self.spool.remove([row[6] for row in rows])
                        except Exception:
                            if self.spool:self.spool.add(rows)
                            raise
                        offline=False
                if self.spool and not self.stop.is_set() and time.monotonic()>=retry_at:
                    retry=self.spool.read()
                    if retry:
                        self.store.write(retry)
                        self.spool.remove([row[6] for row in retry])
                    retry_delay=1
                    offline=False
                    retry_at=time.monotonic()+0.25
            except Exception as exc:
                self.failures += 1
                offline=True
                # The failed batch is retained by the spool, not discarded.
                # If disk persistence also failed, make the loss observable.
                if rows:
                    try:
                        if self.spool is None:raise RuntimeError('Retry storage unavailable')
                        self.spool.add(rows)
                    except Exception:
                        # A temporary SQLite writer lock must not discard a
                        # batch while PostgreSQL is also unavailable. Retain
                        # this bounded batch and leave queue tasks outstanding.
                        retry_rows=rows
                        self.retry_pending=len(rows)
                retry_at=time.monotonic()+retry_delay
                retry_delay=min(30,retry_delay*2)
                if time.monotonic() >= failure_notice:
                    # The worker is excluded from this handler; existing console
                    # and file handlers still report unavailable DB logging.
                    logging.getLogger(__name__).warning(
                        'Database logging retry pending: %s; dropped=%s', type(exc).__name__, self.dropped)
                    failure_notice = time.monotonic()+60
            finally:
                if not retry_rows:
                    for _ in rows:self.pending.task_done()
                    self.retry_pending=0
            if retry_rows:time.sleep(.1)
            if not self.stop.is_set() and time.monotonic()>=maintenance:
                try:self.store.cleanup()
                except Exception:self.maintenance_failures+=1
                maintenance=time.monotonic()+3600
        if self.spool:self.spool.close()

    def health(self):
        try:durable=self.spool.count() if self.spool else None
        except Exception:durable=None
        return {'memory_pending':self.pending.qsize()+self.retry_pending,'durable_pending':durable,
                'dropped':self.dropped,'write_failures':self.failures,
                'maintenance_failures':self.maintenance_failures,'spool_error':self.spool_error,
                'durable_mode':self.durable,'durability_failures':self.durability_failures,
                'worker_alive':self.worker.is_alive()}

    def close(self):
        self.acquire()
        try:self.stop.set()
        finally:self.release()
        if threading.current_thread() is not self.worker:
            self.worker.join(timeout=5)
        super().close()


def attach_logging(db, admin_id):
    root = logging.getLogger()
    existing = next((h for h in root.handlers if isinstance(h, DatabaseLogHandler)), None)
    if existing:
        return existing
    handler = DatabaseLogHandler(LogStore(db, admin_id))
    root.setLevel(getattr(logging, os.getenv('SONAR_LOG_LEVEL','INFO').upper(), logging.INFO))
    root.addHandler(handler)
    # Advanced logging config has explicit non-propagating module loggers.
    for name in ('bot_logic', 'core', 'ws_client', 'database', 'telegram', 'websockets', 'paramiko', 'psycopg2'):
        logger = logging.getLogger(name)
        if not logger.propagate:
            logger.addHandler(handler)
    return handler


def detach_logging(handler):
    logging.getLogger().removeHandler(handler)
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            logger.removeHandler(handler)
    handler.close()
