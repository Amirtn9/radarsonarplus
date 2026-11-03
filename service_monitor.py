"""Durable Sonar HTTP/TCP monitoring: tenant-safe settings and incident transitions.

Inspired by the monitor/state concepts in Uptime Kuma and service-monitor-tgbot;
this is an original implementation, not a source-code port.
"""
from __future__ import annotations

import os
from service_probes import normalize_target, ProbeResult

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pro_services (
      id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL,
      name VARCHAR(120) NOT NULL, kind VARCHAR(8) NOT NULL CHECK(kind IN ('http','tcp')),
      target TEXT NOT NULL, interval_seconds INTEGER NOT NULL DEFAULT 60 CHECK(interval_seconds BETWEEN 60 AND 3600),
      timeout_seconds SMALLINT NOT NULL DEFAULT 5 CHECK(timeout_seconds BETWEEN 1 AND 15),
      failure_threshold SMALLINT NOT NULL DEFAULT 2 CHECK(failure_threshold BETWEEN 1 AND 5),
      recovery_threshold SMALLINT NOT NULL DEFAULT 1 CHECK(recovery_threshold BETWEEN 1 AND 5),
      expected_status SMALLINT CHECK(expected_status BETWEEN 200 AND 599),
      enabled BOOLEAN NOT NULL DEFAULT TRUE, status VARCHAR(8) NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','up','down','paused')),
      fail_count SMALLINT NOT NULL DEFAULT 0, success_count SMALLINT NOT NULL DEFAULT 0,
      last_checked_at TIMESTAMPTZ, last_latency_ms DOUBLE PRECISION, last_detail VARCHAR(180),
      next_check_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    "CREATE INDEX IF NOT EXISTS pro_services_due ON pro_services(enabled,next_check_at) WHERE enabled=TRUE",
    "CREATE INDEX IF NOT EXISTS pro_services_owner ON pro_services(owner_id,id)",
    """CREATE TABLE IF NOT EXISTS pro_service_history (
      id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, service_id BIGINT NOT NULL
        REFERENCES pro_services(id) ON DELETE CASCADE,
      ok BOOLEAN NOT NULL, latency_ms DOUBLE PRECISION, detail VARCHAR(180) NOT NULL,
      checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    "CREATE INDEX IF NOT EXISTS pro_service_history_lookup ON pro_service_history(owner_id,service_id,checked_at DESC)",
)


def install_schema(db):
    with db.get_connection() as (conn,cur):
        for statement in SCHEMA:
            cur.execute(statement)
        cur.execute('INSERT INTO pro_migrations(version) VALUES (6) ON CONFLICT DO NOTHING')
        conn.commit()


def transition(status, failures, successes, ok, failure_threshold=2, recovery_threshold=1):
    """Stable state machine: no incident flapping from single transient errors."""
    failures = 0 if ok else min(32767, failures+1)
    successes = min(32767, successes+1) if ok else 0
    if status == 'paused': return status, failures, successes, None
    if ok and successes >= recovery_threshold and status in ('pending','down'):
        return 'up', failures, successes, 'recovery' if status == 'down' else None
    if not ok and failures >= failure_threshold and status in ('pending','up'):
        return 'down', failures, successes, 'failure'
    return status, failures, successes, None


class ServiceStore:
    def __init__(self, pro): self.pro = pro; self.db = pro.db

    def list(self, actor, owner=None):
        owner=self.pro.workspace(actor,owner)
        return [dict(x) for x in self.db.execute('''SELECT id,name,kind,target,interval_seconds,timeout_seconds,
            failure_threshold,recovery_threshold,expected_status,enabled,status,last_checked_at,
            last_latency_ms,last_detail,created_at FROM pro_services WHERE owner_id=%s ORDER BY id DESC LIMIT 100''',
            (owner,),fetch='all')]

    def create(self, actor, owner, name, kind, target, interval=60, timeout=5,
               failure_threshold=2, recovery_threshold=1, expected_status=None):
        self.pro.require(actor,owner,write=True)
        name=str(name).strip()
        if not 1<=len(name)<=120: raise ValueError('Name must have 1-120 characters')
        kind=str(kind).lower().strip()
        target=normalize_target(kind,target)
        interval=int(interval); timeout=int(timeout)
        failure_threshold=int(failure_threshold);recovery_threshold=int(recovery_threshold)
        if interval not in range(60,3601): raise ValueError('Interval must be 60-3600 seconds')
        if timeout not in range(1,16): raise ValueError('Timeout must be 1-15 seconds')
        if failure_threshold not in range(1,6) or recovery_threshold not in range(1,6):
            raise ValueError('Thresholds must be 1-5')
        if expected_status is not None:
            expected_status=int(expected_status)
            if kind!='http' or not 200<=expected_status<=599: raise ValueError('Invalid HTTP status')
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT count(*) n FROM pro_services WHERE owner_id=%s',(owner,))
            if cur.fetchone()['n']>=int(os.getenv('SONAR_SERVICE_LIMIT_PER_WORKSPACE','50')):
                raise ValueError('Service limit reached')
            cur.execute('''INSERT INTO pro_services(owner_id,name,kind,target,interval_seconds,timeout_seconds,
                failure_threshold,recovery_threshold,expected_status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id''',(owner,name,kind,target,interval,timeout,failure_threshold,recovery_threshold,expected_status))
            ident=cur.fetchone()['id'];conn.commit()
        self.pro.audit(owner,actor,'service_created',f'service:{ident}')
        return ident

    def update(self,actor,owner,ident,name,kind,target,interval=60,timeout=5,
               failure_threshold=2,recovery_threshold=1,expected_status=None):
        self.pro.require(actor,owner,write=True)
        name=str(name).strip();kind=str(kind).strip().lower()
        target=normalize_target(kind,target)
        if not 1<=len(name)<=120:raise ValueError('Invalid service name')
        interval,timeout=int(interval),int(timeout)
        failure_threshold,recovery_threshold=int(failure_threshold),int(recovery_threshold)
        if not 60<=interval<=3600 or not 1<=timeout<=15 or not 1<=failure_threshold<=5 or not 1<=recovery_threshold<=5:
            raise ValueError('Invalid monitoring settings')
        if expected_status is not None:
            expected_status=int(expected_status)
            if kind!='http' or not 200<=expected_status<=599:raise ValueError('Invalid expected status')
        with self.db.get_connection() as (conn,cur):
            cur.execute('''UPDATE pro_services SET name=%s,kind=%s,target=%s,interval_seconds=%s,
              timeout_seconds=%s,failure_threshold=%s,recovery_threshold=%s,expected_status=%s,
              status=CASE WHEN enabled=TRUE THEN 'pending' ELSE 'paused' END,fail_count=0,success_count=0,next_check_at=NOW(),updated_at=NOW()
              WHERE owner_id=%s AND id=%s RETURNING id''',
              (name,kind,target,interval,timeout,failure_threshold,recovery_threshold,expected_status,owner,int(ident)))
            if not cur.fetchone():raise ValueError('Service not found')
            cur.execute('''UPDATE pro_incidents SET status='resolved',resolved_at=NOW(),updated_at=NOW()
                 WHERE owner_id=%s AND resource_type='service' AND resource_id=%s AND status<>'resolved' ''',(owner,int(ident)))
            conn.commit()
        self.pro.audit(owner,actor,'service_updated',f'service:{ident}')
        return True

    def set_enabled(self, actor, owner, ident, enabled):
        self.pro.require(actor,owner,write=True)
        with self.db.get_connection() as (conn,cur):
            cur.execute('''UPDATE pro_services SET enabled=%s, status=%s, fail_count=0,
              success_count=0,next_check_at=NOW(),updated_at=NOW() WHERE id=%s AND owner_id=%s RETURNING id''',
              (bool(enabled),'pending' if enabled else 'paused',int(ident),owner))
            if not cur.fetchone(): raise ValueError('Service not found')
            if not enabled:
                cur.execute('''UPDATE pro_incidents SET status='resolved',resolved_at=NOW(),updated_at=NOW()
                     WHERE owner_id=%s AND resource_type='service' AND resource_id=%s
                     AND status<>'resolved' ''',(owner,int(ident)))
            conn.commit()
        self.pro.audit(owner,actor,'service_enabled',f'service:{ident}',str(bool(enabled)))
        return True

    def delete(self, actor, owner, ident):
        self.pro.require(actor,owner,write=True)
        with self.db.get_connection() as (conn,cur):
            cur.execute('DELETE FROM pro_services WHERE owner_id=%s AND id=%s RETURNING id',(owner,int(ident)))
            if not cur.fetchone(): raise ValueError('Service not found')
            cur.execute('''UPDATE pro_incidents SET status='resolved',resolved_at=NOW(),updated_at=NOW()
              WHERE owner_id=%s AND resource_type='service' AND resource_id=%s AND status<>'resolved' ''',(owner,int(ident)))
            conn.commit()
        self.pro.audit(owner,actor,'service_deleted',f'service:{ident}')
        return True

    def history(self,actor,owner,ident,days=7):
        owner=self.pro.workspace(actor,owner)
        if not self.db.execute('SELECT id FROM pro_services WHERE owner_id=%s AND id=%s',(owner,ident),fetch='one'):
            raise ValueError('Service not found')
        days=max(1,min(30,int(days)))
        rows=self.db.execute('''SELECT ok,latency_ms,detail,checked_at FROM pro_service_history
            WHERE owner_id=%s AND service_id=%s AND checked_at>=NOW()-(%s*INTERVAL '1 day')
            ORDER BY checked_at DESC LIMIT 1500''',(owner,ident,days),fetch='all')
        return [dict(r) for r in reversed(rows)]

    def claim(self,limit=20):
        """Reserve a bounded batch atomically; separate bot replicas avoid double probes."""
        with self.db.get_connection() as (conn,cur):
            cur.execute('''SELECT * FROM pro_services WHERE enabled=TRUE AND next_check_at<=NOW()
              ORDER BY next_check_at,id FOR UPDATE SKIP LOCKED LIMIT %s''',(max(1,min(30,int(limit))),))
            rows=[dict(row) for row in cur.fetchall()]
            for row in rows:
                cur.execute('''UPDATE pro_services SET next_check_at=NOW()+(%s*INTERVAL '1 second')
                     WHERE id=%s AND enabled=TRUE''',(max(row['interval_seconds'],row['timeout_seconds']+30),row['id']))
            conn.commit()
        return rows

    def record(self,row,result:ProbeResult):
        """Only update a row still enabled and owned by the original tenant."""
        with self.db.get_connection() as(conn,cur):
            cur.execute('''SELECT * FROM pro_services WHERE id=%s AND owner_id=%s AND enabled=TRUE
                      FOR UPDATE''',(row['id'],row['owner_id']))
            previous=cur.fetchone()
            if not previous or previous['updated_at'] != row['updated_at']:
                # A monitor changed while its probe was running: discard stale outcome.
                conn.commit();return None
            state,fail,success,event=transition(previous['status'],previous['fail_count'],previous['success_count'],
                 result.ok,previous['failure_threshold'],previous['recovery_threshold'])
            cur.execute('''UPDATE pro_services SET status=%s,fail_count=%s,success_count=%s,
                last_checked_at=NOW(),last_latency_ms=%s,last_detail=%s,updated_at=NOW()
                WHERE id=%s AND owner_id=%s''',
                (state,fail,success,result.latency_ms,result.detail[:180],row['id'],row['owner_id']))
            cur.execute('''INSERT INTO pro_service_history(owner_id,service_id,ok,latency_ms,detail)
                VALUES(%s,%s,%s,%s,%s) RETURNING id''',(row['owner_id'],row['id'],result.ok,result.latency_ms,result.detail[:180]))
            history_id=cur.fetchone()['id']
            if event in ('failure','recovery'):
                glyph='🔴' if event=='failure' else '🟢'
                state='قطع شد' if event=='failure' else 'بازیابی شد'
                msg=(f'{glyph} رادار سونار · سرویس {str(previous["name"])[:120]} {state}\n'
                     f'شناسه: {row["id"]} | {str(result.detail)[:180]}')
                cur.execute('''INSERT INTO pro_notification_outbox(owner_id,event_key,event_type,message)
                    VALUES(%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING''',
                    (row['owner_id'],f'service:{row["id"]}:{history_id}',
                     'service_failure' if event=='failure' else 'service_recovery',msg))
            if event=='failure':
                cur.execute('''INSERT INTO pro_incidents(owner_id,resource_type,resource_id,kind,title,severity)
                  VALUES(%s,'service',%s,'uptime',%s,'warning')
                  ON CONFLICT(owner_id,resource_type,resource_id,kind) WHERE status<>'resolved'
                  DO UPDATE SET occurrences=pro_incidents.occurrences+1,updated_at=NOW()''',
                  (row['owner_id'],row['id'],f'Service down: {previous["name"]}'[:240]))
            if event=='recovery':
                cur.execute('''UPDATE pro_incidents SET status='resolved',resolved_at=NOW(),updated_at=NOW()
                  WHERE owner_id=%s AND resource_type='service' AND resource_id=%s
                  AND kind='uptime' AND status<>'resolved' ''',(row['owner_id'],row['id']))
            conn.commit()
        return event

    def cleanup(self,days=None):
        if days is None: days=int(os.getenv('SONAR_SERVICE_HISTORY_DAYS','14'))
        self.db.execute('DELETE FROM pro_service_history WHERE checked_at<NOW()-(%s*INTERVAL \'1 day\')',
                        (max(1,min(365,int(days))),))
