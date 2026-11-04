"""Additive enterprise capabilities for RadarSonarPlus 6.9.2.

This module deliberately separates durable orchestration records from remote execution.
No arbitrary shell command is accepted here; execution must go through the existing,
audited server-operation services after authorization and staging validation.
"""
from __future__ import annotations
import hashlib, json, secrets, re
from datetime import datetime, timezone

SCHEMA = (
"""CREATE TABLE IF NOT EXISTS rs_operations (
 id UUID PRIMARY KEY, owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
 kind VARCHAR(40) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'queued'
 CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 idempotency_key VARCHAR(120) NOT NULL, payload JSONB NOT NULL DEFAULT '{}'::jsonb,
 result JSONB NOT NULL DEFAULT '{}'::jsonb, total INTEGER NOT NULL DEFAULT 0,
 succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(owner_id,idempotency_key))""",
"CREATE INDEX IF NOT EXISTS rs_operations_owner_time ON rs_operations(owner_id,created_at DESC)",
"""CREATE TABLE IF NOT EXISTS rs_health_policies (
 owner_id BIGINT PRIMARY KEY, weights JSONB NOT NULL DEFAULT '{"agent":25,"cpu":25,"ram":25,"disk":25}'::jsonb,
 thresholds JSONB NOT NULL DEFAULT '{"cpu":90,"ram":90,"disk":90}'::jsonb,
 stale_seconds INTEGER NOT NULL DEFAULT 180 CHECK(stale_seconds BETWEEN 30 AND 3600),
 updated_by BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS rs_operation_items (
 id BIGSERIAL PRIMARY KEY, operation_id UUID NOT NULL REFERENCES rs_operations(id) ON DELETE CASCADE,
 resource_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'queued'
 CHECK(status IN ('queued','running','succeeded','failed','skipped')),
 detail VARCHAR(500) NOT NULL DEFAULT '', started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
 UNIQUE(operation_id,resource_id))""",
"""CREATE TABLE IF NOT EXISTS rs_incident_notes (
 id BIGSERIAL PRIMARY KEY, incident_id BIGINT NOT NULL REFERENCES pro_incidents(id) ON DELETE CASCADE,
 owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL, note TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS rs_incident_assignments (
 incident_id BIGINT PRIMARY KEY REFERENCES pro_incidents(id) ON DELETE CASCADE,
 owner_id BIGINT NOT NULL, assignee_id BIGINT NOT NULL, assigned_by BIGINT NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS rs_backup_schedules (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
 name VARCHAR(100) NOT NULL, cadence VARCHAR(20) NOT NULL CHECK(cadence IN ('daily','weekly')),
 destination VARCHAR(500) NOT NULL, encrypted BOOLEAN NOT NULL DEFAULT TRUE,
 retention_count SMALLINT NOT NULL DEFAULT 7 CHECK(retention_count BETWEEN 1 AND 90),
 enabled BOOLEAN NOT NULL DEFAULT TRUE, last_run_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""ALTER TABLE rs_backup_schedules ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW()""",
"""CREATE TABLE IF NOT EXISTS rs_backup_runs (
 id BIGSERIAL PRIMARY KEY, schedule_id BIGINT NOT NULL REFERENCES rs_backup_schedules(id) ON DELETE CASCADE,
 owner_id BIGINT NOT NULL, path TEXT, sha256 CHAR(64), bytes BIGINT,
 status VARCHAR(16) NOT NULL, error_code VARCHAR(80),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"CREATE INDEX IF NOT EXISTS rs_backup_runs_schedule ON rs_backup_runs(schedule_id,created_at DESC)",
"""CREATE TABLE IF NOT EXISTS rs_automation_rules (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
 event VARCHAR(40) NOT NULL, action VARCHAR(40) NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE, max_attempts SMALLINT NOT NULL DEFAULT 1 CHECK(max_attempts BETWEEN 1 AND 3),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(owner_id,event,action))""",
"""CREATE TABLE IF NOT EXISTS rs_api_tokens (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
 label VARCHAR(80) NOT NULL, token_hash CHAR(64) NOT NULL UNIQUE, scopes TEXT[] NOT NULL,
 expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS rs_webhooks (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
 url TEXT NOT NULL, secret_hash CHAR(64) NOT NULL, events TEXT[] NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS rs_webhook_deliveries (
 id BIGSERIAL PRIMARY KEY, webhook_id BIGINT NOT NULL REFERENCES rs_webhooks(id) ON DELETE CASCADE,
 event_key VARCHAR(120) NOT NULL, payload JSONB NOT NULL, attempts SMALLINT NOT NULL DEFAULT 0,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), delivered_at TIMESTAMPTZ,
 UNIQUE(webhook_id,event_key))""",
)

def install_schema(db):
    with db.get_connection() as (conn, cur):
        for sql in SCHEMA: cur.execute(sql)
        # Expand the legacy team-role constraint without changing existing memberships.
        cur.execute("ALTER TABLE pro_team DROP CONSTRAINT IF EXISTS pro_team_role_check")
        cur.execute("ALTER TABLE pro_team ADD CONSTRAINT pro_team_role_check CHECK(role IN ('manager','viewer','operator','auditor'))")
        conn.commit()

class EnterpriseStore:
    def __init__(self, pro_store): self.pro=pro_store; self.db=pro_store.db

    def health(self, actor, owner=None, stale_seconds=None):
        """Return health only when every weighted signal has usable source data.

        Legacy agent_last_seen values are TEXT, while last_checked_at is a
        PostgreSQL TIMESTAMPTZ. Normalize both explicitly and fail closed when
        timestamps or metric values are absent/invalid; missing data must never
        be converted into a healthy zero-load reading.
        """
        import math

        def as_utc(value):
            if isinstance(value, datetime):
                return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            if isinstance(value, str) and value.strip():
                raw = value.strip()
                try:
                    parsed = datetime.fromisoformat(raw[:-1] + '+00:00' if raw.endswith(('Z', 'z')) else raw)
                except ValueError:
                    return None
                return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            return None

        def numeric(value):
            if value is None or isinstance(value, bool):
                return None
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return max(0.0, min(100.0, number)) if math.isfinite(number) else None

        owner=self.pro.workspace(actor,owner)
        policy=self.db.execute("SELECT weights,thresholds,stale_seconds FROM rs_health_policies WHERE owner_id=%s",(owner,),fetch="one")
        defaults={"agent":25,"cpu":25,"ram":25,"disk":25}
        weights={key:float((policy or {}).get('weights',{}).get(key,default)) for key,default in defaults.items()}
        threshold_defaults={"cpu":90,"ram":90,"disk":90}
        thresholds={key:max(1.0,float((policy or {}).get('thresholds',{}).get(key,default)))
                    for key,default in threshold_defaults.items()}
        stale_seconds=int(stale_seconds or (policy["stale_seconds"] if policy else 180))
        rows=self.db.execute("""SELECT id,name,last_status,last_agent_status,last_cpu,last_ram,last_disk,
            last_checked_at,agent_last_seen FROM servers WHERE owner_id=%s ORDER BY id""",(owner,),fetch='all')
        result=[]
        now=datetime.now(timezone.utc)
        for r in rows:
            stamp=as_utc(r.get('last_checked_at')) or as_utc(r.get('agent_last_seen'))
            age=(now-stamp) if stamp else None
            stale=age is None or age.total_seconds()>stale_seconds
            agent=str(r.get('last_agent_status') or '').strip().lower()
            status=str(r.get('last_status') or '').strip().lower()
            offline=status in ('offline','down','fail','failed') or agent in ('offline','down','disconnected','fail','failed')
            metric_values={
                'agent':100.0 if agent in ('online','up','ok','connected','healthy') else None,
                'cpu':None,
                'ram':None,
                'disk':None,
            }
            for key in ('cpu','ram','disk'):
                raw=numeric(r.get('last_'+key))
                if raw is not None:
                    metric_values[key]=max(0.0,100.0-min(100.0,raw/thresholds[key]*100.0))
            missing=[key for key,value in metric_values.items() if value is None]
            if offline:
                score=None; state='critical'
            elif stale or missing:
                score=None; state='unknown'
            else:
                score=round(sum(weights[key]*metric_values[key] for key in metric_values)/100)
                state='healthy' if score>=85 else 'warning' if score>=60 else 'critical'
            result.append({'server_id':r['id'],'name':r['name'],'score':score,'state':state,'stale':stale,
                           'missing_metrics':missing,'last_checked_at':stamp.isoformat() if stamp else None})
        return {'owner_id':owner,'policy':{'weights':weights,'thresholds':thresholds,'stale_seconds':stale_seconds},
                'servers':result,'healthy':sum(x['state']=='healthy' for x in result),
                'warning':sum(x['state']=='warning' for x in result),'critical':sum(x['state']=='critical' for x in result),
                'unknown':sum(x['state']=='unknown' for x in result)}

    def create_operation(self, actor, owner, kind, resource_ids, idempotency_key, payload=None):
        if self.pro.role(actor,owner) not in ('owner','manager','operator'): raise PermissionError('Operation role required')
        allowed,_=self.db.check_access(owner)
        if not allowed: raise PermissionError('Workspace owner is inactive')
        if kind not in ('agent_update','service_restart','diagnostic','maintenance'):
            raise ValueError('Unsupported operation type')
        if not re.fullmatch(r'[A-Za-z0-9._:-]{8,120}',idempotency_key or ''): raise ValueError('Invalid idempotency key')
        ids=sorted(set(int(x) for x in resource_ids))
        if not ids or len(ids)>100 or min(ids)<1: raise ValueError('Select between 1 and 100 resources')
        # Ensure every target belongs to this workspace before creating any work.
        found=self.db.execute('SELECT id FROM servers WHERE owner_id=%s AND id=ANY(%s)',(owner,ids),fetch='all')
        if {int(x['id']) for x in found} != set(ids): raise ValueError('One or more servers are outside this workspace')
        opid=self.db.execute("""INSERT INTO rs_operations(id,owner_id,actor_id,kind,idempotency_key,payload,total)
          VALUES(gen_random_uuid(),%s,%s,%s,%s,%s::jsonb,%s)
          ON CONFLICT(owner_id,idempotency_key) DO NOTHING RETURNING id,status,total""",
          (owner,actor,kind,idempotency_key,json.dumps(payload or {}),len(ids)),fetch='one')
        created = opid is not None
        if not created:
            opid=self.db.execute('SELECT id,status,total FROM rs_operations WHERE owner_id=%s AND idempotency_key=%s',
                                 (owner,idempotency_key),fetch='one')
            if not opid or int(opid['total']) != len(ids): raise ValueError('Idempotency key was already used for a different request')
            saved_ids=self.db.execute('SELECT resource_id FROM rs_operation_items WHERE operation_id=%s',(opid['id'],),fetch='all')
            if {int(x['resource_id']) for x in saved_ids} != set(ids):
                raise ValueError('Idempotency key was already used for different servers')
        if created:
            for rid in ids:self.db.execute('INSERT INTO rs_operation_items(operation_id,resource_id) VALUES(%s,%s)',(opid['id'],rid))
        self.pro.audit(owner,actor,'operation_created',str(opid['id']),kind)
        return {'id':str(opid['id']),'status':opid['status'],'total':opid['total']}

    def operations(self,actor,owner=None,limit=30):
        owner=self.pro.workspace(actor,owner)
        rows=self.db.execute('SELECT id,kind,status,total,succeeded,failed,created_at,updated_at FROM rs_operations WHERE owner_id=%s ORDER BY created_at DESC LIMIT %s',(owner,max(1,min(100,int(limit)))),fetch='all')
        return [dict(x, id=str(x['id']),created_at=x['created_at'].isoformat(),updated_at=x['updated_at'].isoformat()) for x in rows]

    def operation_detail(self,actor,owner,operation_id):
        owner=self.pro.workspace(actor,owner)
        row=self.db.execute("""SELECT id,kind,status,payload,result,total,succeeded,failed,created_at,updated_at,finished_at
          FROM rs_operations WHERE id=%s AND owner_id=%s""",(str(operation_id),owner),fetch='one')
        if not row: raise ValueError("Operation not found")
        items=self.db.execute("""SELECT i.resource_id,i.status,i.attempts,i.detail,i.last_error,
          i.started_at,i.finished_at,s.name FROM rs_operation_items i
          LEFT JOIN servers s ON s.id=i.resource_id AND s.owner_id=%s
          WHERE i.operation_id=%s ORDER BY i.id""",(owner,str(operation_id)),fetch='all')
        def iso(v): return v.isoformat() if hasattr(v,'isoformat') else v
        return {"operation":{k:(str(v) if k=="id" else iso(v) if k.endswith("_at") else v) for k,v in dict(row).items()},
                "items":[{k:(iso(v) if k.endswith("_at") else v) for k,v in dict(x).items()} for x in items]}

    def control_operation(self,actor,owner,operation_id,action):
        self.pro.require(actor,owner,write=True)
        if action not in {"pause","resume","cancel","retry"}: raise ValueError("Invalid operation action")
        with self.db.get_connection() as (conn,cur):
            cur.execute("SELECT id,status FROM rs_operations WHERE id=%s AND owner_id=%s FOR UPDATE",(str(operation_id),owner))
            row=cur.fetchone()
            if not row: raise ValueError("Operation not found")
            status=row["status"]
            if action=="pause":
                if status not in ("queued","running"): raise ValueError("Only queued or running operations can be paused")
                cur.execute("UPDATE rs_operations SET status='paused',updated_at=NOW() WHERE id=%s",(str(operation_id),))
            elif action=="resume":
                if status!="paused": raise ValueError("Only paused operations can be resumed")
                cur.execute("""UPDATE rs_operations SET status='running',updated_at=NOW()
                  WHERE id=%s AND EXISTS(SELECT 1 FROM rs_operation_items WHERE operation_id=%s AND status IN ('queued','running'))""",
                  (str(operation_id),str(operation_id)))
                if cur.rowcount==0: raise ValueError("No pending items to resume")
            elif action=="cancel":
                if status not in ("queued","running","paused"): raise ValueError("Operation cannot be cancelled")
                cur.execute("UPDATE rs_operation_items SET status='skipped',finished_at=NOW(),detail='cancelled_before_execution' WHERE operation_id=%s AND status='queued'",(str(operation_id),))
                cur.execute("""UPDATE rs_operations SET status=CASE WHEN EXISTS(
                    SELECT 1 FROM rs_operation_items WHERE operation_id=%s AND status='running')
                    THEN 'cancel_requested' ELSE 'cancelled' END,updated_at=NOW(),
                    finished_at=CASE WHEN EXISTS(SELECT 1 FROM rs_operation_items WHERE operation_id=%s AND status='running')
                    THEN NULL ELSE NOW() END WHERE id=%s""",(str(operation_id),str(operation_id),str(operation_id)))
            elif action=="retry":
                if status not in ("failed","cancelled"): raise ValueError("Only failed/cancelled operations can be retried")
                cur.execute("""UPDATE rs_operation_items SET status='queued',attempts=0,next_attempt_at=NOW(),
                  lease_until=NULL,finished_at=NULL,last_error=NULL,detail='' WHERE operation_id=%s AND status IN ('failed','skipped')""",(str(operation_id),))
                if cur.rowcount==0: raise ValueError("No failed items to retry")
                cur.execute("""UPDATE rs_operations SET status='queued',failed=0,finished_at=NULL,updated_at=NOW()
                  WHERE id=%s""",(str(operation_id),))
            conn.commit()
        self.pro.audit(owner,actor,"operation_"+action,str(operation_id))
        return {"id":str(operation_id),"status":{"pause":"paused","resume":"running","cancel":"cancel_requested","retry":"queued"}[action]}

    def list_incidents(self,actor,owner=None,status="open",limit=50):
        owner=self.pro.workspace(actor,owner)
        if status not in {"open","acknowledged","investigating","resolved","all"}: raise ValueError("Invalid incident status")
        where="i.owner_id=%s" if status=="all" else "i.owner_id=%s AND i.status=%s"
        args=(owner,) if status=="all" else (owner,status)
        rows=self.db.execute(f"""SELECT i.id,i.resource_type,i.resource_id,i.kind,i.title,i.severity,i.status,
          i.occurrences,i.opened_at,i.updated_at,i.resolved_at,a.assignee_id
          FROM pro_incidents i LEFT JOIN rs_incident_assignments a ON a.incident_id=i.id
          WHERE {where} ORDER BY i.updated_at DESC LIMIT %s""",args+(max(1,min(200,int(limit))),),fetch="all")
        return [{k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in dict(r).items()} for r in rows]

    def incident_detail(self,actor,owner,incident_id):
        owner=self.pro.workspace(actor,owner)
        row=self.db.execute("""SELECT i.*,a.assignee_id FROM pro_incidents i
          LEFT JOIN rs_incident_assignments a ON a.incident_id=i.id
          WHERE i.id=%s AND i.owner_id=%s""",(int(incident_id),owner),fetch="one")
        if not row: raise ValueError("Incident not found")
        notes=self.db.execute("""SELECT actor_id,note,created_at FROM rs_incident_notes
          WHERE incident_id=%s AND owner_id=%s ORDER BY id""",(int(incident_id),owner),fetch="all")
        data=dict(row)
        for k,v in list(data.items()):
            if hasattr(v,"isoformat"): data[k]=v.isoformat()
        return {"incident":data,"notes":[{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(n).items()} for n in notes]}

    def server_metrics(self,actor,owner,server_id,days=7):
        owner=self.pro.workspace(actor,owner)
        days=max(1,min(30,int(days)))
        server=self.db.execute("SELECT id,name FROM servers WHERE id=%s AND owner_id=%s",(int(server_id),owner),fetch="one")
        if not server: raise ValueError("Server not found")
        rows=self.db.execute("""SELECT cpu,ram,disk,traffic_gb,bytes_sent,bytes_recv,uptime_sec,created_at
          FROM server_stats WHERE server_id=%s AND created_at>=NOW()-(%s||' days')::interval
          ORDER BY created_at ASC LIMIT 10000""",(int(server_id),days),fetch="all")
        return {"server":dict(server),"period_days":days,"samples":[{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(r).items()} for r in rows]}

    def health_policy(self,actor,owner=None):
        owner=self.pro.workspace(actor,owner)
        row=self.db.execute("SELECT weights,thresholds,stale_seconds,updated_at FROM rs_health_policies WHERE owner_id=%s",(owner,),fetch="one")
        if not row:
            return {"weights":{"agent":25,"cpu":25,"ram":25,"disk":25},
                    "thresholds":{"cpu":90,"ram":90,"disk":90},"stale_seconds":180}
        return {"weights":row["weights"],"thresholds":row["thresholds"],"stale_seconds":row["stale_seconds"],
                "updated_at":row["updated_at"].isoformat()}

    def set_health_policy(self,actor,owner,weights,thresholds,stale_seconds):
        self.pro.require(actor,owner,write=True)
        keys={"agent","cpu","ram","disk"}
        if set(weights)!=keys or any(not isinstance(v,(int,float)) or v<0 or v>100 for v in weights.values()) or sum(weights.values())!=100:
            raise ValueError("Health weights must be 0-100 and sum to 100")
        if set(thresholds)!={"cpu","ram","disk"} or any(not isinstance(v,(int,float)) or not 1<=v<=100 for v in thresholds.values()):
            raise ValueError("Thresholds must be 1-100")
        if not 30<=int(stale_seconds)<=3600: raise ValueError("Stale window must be 30-3600 seconds")
        self.db.execute("""INSERT INTO rs_health_policies(owner_id,weights,thresholds,stale_seconds,updated_by)
          VALUES(%s,%s::jsonb,%s::jsonb,%s,%s) ON CONFLICT(owner_id) DO UPDATE SET
          weights=EXCLUDED.weights,thresholds=EXCLUDED.thresholds,stale_seconds=EXCLUDED.stale_seconds,
          updated_by=EXCLUDED.updated_by,updated_at=NOW()""",
          (owner,json.dumps(weights),json.dumps(thresholds),int(stale_seconds),actor))
        self.pro.audit(owner,actor,"health_policy_updated","health",json.dumps({"weights":weights,"thresholds":thresholds,"stale_seconds":stale_seconds}))
        return self.health_policy(actor,owner)

    def backup_schedules(self,actor,owner=None):
        owner=self.pro.workspace(actor,owner)
        rows=self.db.execute("""SELECT id,name,cadence,destination,retention_count,enabled,last_run_at,next_run_at,created_at
          FROM rs_backup_schedules WHERE owner_id=%s ORDER BY id DESC""",(owner,),fetch="all")
        return [{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(r).items()} for r in rows]

    def update_backup_schedule(self,actor,owner,schedule_id,name=None,cadence=None,destination=None,retention=None):
        from settings import SUPER_ADMIN_ID
        if int(actor)!=int(SUPER_ADMIN_ID) or int(owner)!=int(SUPER_ADMIN_ID):
            raise PermissionError("Full PostgreSQL backups are platform-admin only")
        self.pro.require(actor,owner,write=True)
        fields=[]; args=[]
        if name is not None:
            name=str(name).strip()
            if not name or len(name)>100: raise ValueError("Invalid backup name")
            fields.append("name=%s");args.append(name)
        if cadence is not None:
            if cadence not in ("daily","weekly"): raise ValueError("Invalid backup cadence")
            fields.append("cadence=%s");args.append(cadence)
        if destination is not None:
            if not str(destination).strip() or len(str(destination))>120: raise ValueError("Invalid backup destination")
            from enterprise_runtime import validate_backup_destination
            validate_backup_destination(str(destination))
            fields.append("destination=%s");args.append(str(destination))
        if retention is not None:
            if not 1<=int(retention)<=90: raise ValueError("Retention must be 1-90")
            fields.append("retention_count=%s");args.append(int(retention))
        if not fields: raise ValueError("No backup schedule changes supplied")
        fields.extend(["next_run_at=NOW()","updated_at=NOW()"])
        row=self.db.execute("UPDATE rs_backup_schedules SET "+",".join(fields)+" WHERE id=%s AND owner_id=%s RETURNING id",
                            tuple(args+[int(schedule_id),owner]),fetch="one")
        if not row: raise ValueError("Backup schedule not found")
        self.pro.audit(owner,actor,"backup_schedule_updated",str(schedule_id))
        return {"id":int(schedule_id),"updated":True}

    def backup_runs(self,actor,owner,schedule_id):
        from settings import SUPER_ADMIN_ID
        if int(actor)!=int(SUPER_ADMIN_ID) or int(owner)!=int(SUPER_ADMIN_ID):
            raise PermissionError("Backup run history is platform-admin only")
        self.pro.require(actor,owner)
        rows=self.db.execute("""SELECT id,path,sha256,bytes,status,error_code,created_at FROM rs_backup_runs
          WHERE owner_id=%s AND schedule_id=%s ORDER BY id DESC LIMIT 100""",(owner,int(schedule_id)),fetch="all")
        return [{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(r).items()} for r in rows]

    def control_backup_schedule(self,actor,owner,schedule_id,action):
        from settings import SUPER_ADMIN_ID
        if int(actor)!=int(SUPER_ADMIN_ID) or int(owner)!=int(SUPER_ADMIN_ID):
            raise PermissionError("Full PostgreSQL backups are platform-admin only")
        self.pro.require(actor,owner,write=True)
        if action not in {"enable","disable","run_now","delete"}: raise ValueError("Invalid backup action")
        if action=="run_now":
            row=self.db.execute("UPDATE rs_backup_schedules SET enabled=TRUE,next_run_at=NOW() WHERE id=%s AND owner_id=%s RETURNING id",(int(schedule_id),owner),fetch="one")
        elif action=="delete":
            active=self.db.execute("SELECT id FROM rs_backup_schedules WHERE id=%s AND owner_id=%s AND lease_until>NOW()",
                                   (int(schedule_id),owner),fetch="one")
            if active: raise ValueError("Backup schedule is currently running")
            paths=self.db.execute("SELECT path FROM rs_backup_runs WHERE schedule_id=%s AND owner_id=%s AND path IS NOT NULL",
                                  (int(schedule_id),owner),fetch="all")
            row=self.db.execute("DELETE FROM rs_backup_schedules WHERE id=%s AND owner_id=%s RETURNING id",(int(schedule_id),owner),fetch="one")
            if row:
                from enterprise_runtime import _delete_backup
                for item in paths:
                    try: _delete_backup(item["path"])
                    except Exception: pass
        else:
            row=self.db.execute("UPDATE rs_backup_schedules SET enabled=%s WHERE id=%s AND owner_id=%s RETURNING id",(action=="enable",int(schedule_id),owner),fetch="one")
        if not row: raise ValueError("Backup schedule not found")
        self.pro.audit(owner,actor,"backup_schedule_"+action,str(schedule_id))
        return {"id":int(schedule_id),"action":action}

    def list_api_tokens(self,actor,owner=None):
        owner=self.pro.workspace(actor,owner)
        rows=self.db.execute("""SELECT id,label,scopes,expires_at,revoked_at,created_at,request_count,request_window_at
          FROM rs_api_tokens WHERE owner_id=%s ORDER BY id DESC LIMIT 100""",(owner,),fetch="all")
        return [{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(r).items()} for r in rows]

    def list_webhooks(self,actor,owner=None):
        owner=self.pro.workspace(actor,owner)
        rows=self.db.execute("""SELECT id,url,events,enabled,created_at,updated_at FROM rs_webhooks
          WHERE owner_id=%s ORDER BY id DESC LIMIT 100""",(owner,),fetch="all")
        return [{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(r).items()} for r in rows]

    def webhook_deliveries(self,actor,owner,webhook_id):
        owner=self.pro.workspace(actor,owner)
        rows=self.db.execute("""SELECT d.id,d.event_key,d.attempts,d.next_attempt_at,d.delivered_at,d.dead_at,
          d.last_status,d.last_error,d.created_at FROM rs_webhook_deliveries d JOIN rs_webhooks w ON w.id=d.webhook_id
          WHERE w.id=%s AND w.owner_id=%s ORDER BY d.id DESC LIMIT 100""",(int(webhook_id),owner),fetch="all")
        return [{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(r).items()} for r in rows]

    def update_webhook(self,actor,owner,webhook_id,url=None,events=None,enabled=None,rotate_secret=False):
        self.pro.require(actor,owner,write=True)
        row=self.db.execute("SELECT id FROM rs_webhooks WHERE id=%s AND owner_id=%s",(int(webhook_id),owner),fetch="one")
        if not row: raise ValueError("Webhook not found")
        fields=[]; args=[]
        if url is not None:
            from enterprise_runtime import _public_https_url
            _public_https_url(url); fields.append("url=%s"); args.append(url)
        if events is not None:
            allowed={"incident.opened","incident.resolved","operation.completed","server.offline"}
            events=sorted(set(events))
            if not events or set(events)-allowed: raise ValueError("Unsupported webhook event")
            fields.append("events=%s"); args.append(events)
        if enabled is not None: fields.append("enabled=%s"); args.append(bool(enabled))
        secret=None
        if rotate_secret:
            import os
            from cryptography.fernet import Fernet
            key=os.getenv("SONAR_WEBHOOK_ENCRYPTION_KEY") or os.getenv("SONAR_BACKUP_ENCRYPTION_KEY","")
            if not key: raise RuntimeError("Configure SONAR_WEBHOOK_ENCRYPTION_KEY")
            secret=secrets.token_urlsafe(40)
            fields.extend(["secret_hash=%s","secret_ciphertext=%s"])
            args.extend([hashlib.sha256(secret.encode()).hexdigest(),Fernet(key.encode()).encrypt(secret.encode()).decode()])
        if fields:
            fields.append("updated_at=NOW()")
            self.db.execute("UPDATE rs_webhooks SET "+",".join(fields)+" WHERE id=%s AND owner_id=%s",tuple(args+[int(webhook_id),owner]))
        self.pro.audit(owner,actor,"webhook_updated",str(webhook_id))
        return {"id":int(webhook_id),"secret":secret}

    def delete_webhook(self,actor,owner,webhook_id):
        self.pro.require(actor,owner,write=True)
        row=self.db.execute("DELETE FROM rs_webhooks WHERE id=%s AND owner_id=%s RETURNING id",(int(webhook_id),owner),fetch="one")
        if not row: raise ValueError("Webhook not found")
        self.pro.audit(owner,actor,"webhook_deleted",str(webhook_id))
        return True

    def retry_webhook_delivery(self,actor,owner,webhook_id,delivery_id):
        self.pro.require(actor,owner,write=True)
        row=self.db.execute("""UPDATE rs_webhook_deliveries d SET attempts=0,next_attempt_at=NOW(),
          delivered_at=NULL,dead_at=NULL,last_error=NULL,lease_until=NULL
          FROM rs_webhooks w WHERE d.webhook_id=w.id AND w.id=%s AND w.owner_id=%s AND d.id=%s
          RETURNING d.id""",(int(webhook_id),owner,int(delivery_id)),fetch="one")
        if not row: raise ValueError("Delivery not found")
        self.pro.audit(owner,actor,"webhook_delivery_retry",str(delivery_id))
        return True

    def delete_api_token(self,actor,owner,token_id):
        return self.revoke_api_token(actor,owner,token_id)

    def transition_incident(self,actor,owner,incident_id,status):
        if self.pro.role(actor,owner) not in ('owner','manager','operator'): raise PermissionError('Incident role required')
        allowed,_=self.db.check_access(owner)
        if not allowed: raise PermissionError('Workspace owner is inactive')
        if status not in ('acknowledged','investigating','resolved'):raise ValueError('Invalid incident state')
        row=self.db.execute('UPDATE pro_incidents SET status=%s,resolved_at=CASE WHEN %s=\'resolved\' THEN NOW() ELSE NULL END,updated_at=NOW(),ack_by=CASE WHEN %s=\'acknowledged\' THEN %s ELSE ack_by END WHERE id=%s AND owner_id=%s AND status<>\'resolved\' RETURNING id',(status,status,status,actor,int(incident_id),owner),fetch='one')
        if not row:raise ValueError('Incident not found or already resolved')
        self.pro.audit(owner,actor,'incident_transition',str(incident_id),status)
        if status=='resolved':
            try:
                from enterprise_runtime import enqueue_webhook_event
                enqueue_webhook_event(self.db,owner,'incident.resolved',{'incident_id':int(incident_id),'status':status},event_key=f'incident:{incident_id}:resolved')
            except Exception: pass
        return True

    def incident_note(self,actor,owner,incident_id,note):
        self.pro.require(actor,owner,write=True)
        note=str(note).strip()
        if not note or len(note)>2000:raise ValueError('Note must contain 1-2000 characters')
        if not self.db.execute('SELECT id FROM pro_incidents WHERE id=%s AND owner_id=%s',(incident_id,owner),fetch='one'):raise ValueError('Incident not found')
        self.db.execute('INSERT INTO rs_incident_notes(incident_id,owner_id,actor_id,note) VALUES(%s,%s,%s,%s)',(incident_id,owner,actor,note))
        self.pro.audit(owner,actor,'incident_note',str(incident_id))

    def assign_incident(self,actor,owner,incident_id,assignee):
        self.pro.require(actor,owner,write=True)
        if not (int(assignee)==int(owner) or self.db.execute('SELECT 1 FROM sonar_workspaces WHERE owner_id=%s AND member_id=%s',(owner,assignee),fetch='one') or self.db.execute('SELECT 1 FROM pro_team WHERE owner_id=%s AND member_id=%s',(owner,assignee),fetch='one')):raise ValueError('Assignee is not a workspace member')
        if not self.db.execute('SELECT id FROM pro_incidents WHERE id=%s AND owner_id=%s',(incident_id,owner),fetch='one'):raise ValueError('Incident not found')
        self.db.execute('''INSERT INTO rs_incident_assignments(incident_id,owner_id,assignee_id,assigned_by) VALUES(%s,%s,%s,%s)
          ON CONFLICT(incident_id) DO UPDATE SET assignee_id=EXCLUDED.assignee_id,assigned_by=EXCLUDED.assigned_by,updated_at=NOW()''',(incident_id,owner,assignee,actor))

    def set_role(self,actor,owner,member,role):
        if int(actor)!=int(owner):raise PermissionError('Only owner can change team roles')
        if role not in ('manager','operator','viewer','auditor','remove'):raise ValueError('Invalid role')
        if role=='remove':self.db.execute('DELETE FROM pro_team WHERE owner_id=%s AND member_id=%s',(owner,member))
        else:
            if not self.db.get_user(int(member)):raise ValueError('Member must start the bot first')
            self.db.execute('''INSERT INTO pro_team(owner_id,member_id,role) VALUES(%s,%s,%s)
             ON CONFLICT(owner_id,member_id) DO UPDATE SET role=EXCLUDED.role''',(owner,member,role))
        self.pro.audit(owner,actor,'team_role',str(member),role)

    def create_backup_schedule(self,actor,owner,name,cadence,destination,retention=7):
        from settings import SUPER_ADMIN_ID
        if int(actor)!=int(SUPER_ADMIN_ID) or int(owner)!=int(SUPER_ADMIN_ID):
            raise PermissionError('Full PostgreSQL backups are platform-admin only; tenant-scoped export is not yet enabled')
        self.pro.require(actor,owner,write=True)
        if cadence not in ('daily','weekly'):raise ValueError('Invalid cadence')
        if not str(name).strip() or len(name)>100 or not str(destination).strip() or len(destination)>120:raise ValueError('Invalid backup schedule')
        from enterprise_runtime import validate_backup_destination
        validate_backup_destination(str(destination))
        if not 1<=int(retention)<=90:raise ValueError('Retention must be 1-90')
        # Store schedule metadata only. Credentials and actual backup transfer are managed by deployment secrets.
        row=self.db.execute('''INSERT INTO rs_backup_schedules(owner_id,actor_id,name,cadence,destination,retention_count)
          VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''',(owner,actor,name.strip(),cadence,destination.strip(),int(retention)),fetch='one')
        self.pro.audit(owner,actor,'backup_schedule_created',str(row['id']),cadence)
        return {'id':row['id'],'status':'scheduled'}

    def create_automation_rule(self,actor,owner,event,action,max_attempts=1):
        self.pro.require(actor,owner,write=True)
        allowed={'agent_unreachable':{'notify','restart_managed_service'},'service_down':{'notify'},'disk_pressure':{'notify'}}
        if action not in allowed.get(event,set()):raise ValueError('Automation action is not allowlisted for this event')
        if not 1<=int(max_attempts)<=3:raise ValueError('Attempts must be 1-3')
        row=self.db.execute('''INSERT INTO rs_automation_rules(owner_id,actor_id,event,action,max_attempts) VALUES(%s,%s,%s,%s,%s)
          ON CONFLICT(owner_id,event,action) DO UPDATE SET enabled=TRUE,max_attempts=EXCLUDED.max_attempts RETURNING id''',(owner,actor,event,action,int(max_attempts)),fetch='one')
        self.pro.audit(owner,actor,'automation_rule',str(row['id']),f'{event}:{action}')
        return {'id':row['id'],'enabled':True}

    def create_agent_release(self,actor,owner,version,sha256,artifact_base64,signature,channel="stable"):
        self.pro.require(actor,owner,write=True)
        import base64
        if not re.fullmatch(r'[0-9A-Za-z._+-]{1,40}',str(version)): raise ValueError("Invalid release version")
        if channel not in ("stable","beta"): raise ValueError("Invalid release channel")
        if not re.fullmatch(r'[a-fA-F0-9]{64}',str(sha256)): raise ValueError("Invalid SHA-256")
        try:
            artifact=base64.b64decode(artifact_base64,validate=True)
        except Exception:
            raise ValueError("Agent artifact must be valid base64") from None
        if len(artifact)>2_000_000: raise ValueError("Agent artifact exceeds 2 MB")
        from enterprise_runtime import verify_agent_release_bytes
        verify_agent_release_bytes(version,sha256,signature,artifact)
        # A release identity is immutable. Re-registering identical bytes is
        # idempotent; changing bytes under the same version/channel is rejected.
        row=self.db.execute("""INSERT INTO rs_agent_releases(version,artifact_url,sha256,signature,artifact,channel,created_by)
          VALUES(%s,'db://signed-artifact',%s,%s,%s,%s,%s)
          ON CONFLICT(version,channel) DO NOTHING RETURNING id""",
          (version,str(sha256).lower(),signature,artifact,channel,actor),fetch="one")
        if row is None:
            existing=self.db.execute("""SELECT id,sha256,signature,artifact FROM rs_agent_releases
              WHERE version=%s AND channel=%s""",(version,channel),fetch="one")
            if not existing:
                raise RuntimeError("RELEASE_CONFLICT_LOOKUP_FAILED")
            same=(str(existing.get('sha256') or '').lower()==str(sha256).lower()
                  and str(existing.get('signature') or '')==str(signature)
                  and bytes(existing.get('artifact') or b'')==artifact)
            if not same:
                raise ValueError("RELEASE_VERSION_IMMUTABLE: publish changed Agent content under a new version")
            row={'id':existing['id']}
        self.pro.audit(owner,actor,'agent_release_registered',str(row['id']),version+":"+channel)
        return {'id':row['id'],'version':version,'sha256':str(sha256).lower(),'channel':channel,'signature_verified':True}

    def create_agent_rollout(self,actor,owner,release_id,server_ids,batch_size=5,canary_count=1):
        self.pro.require(actor,owner,write=True)
        ids=sorted(set(int(x) for x in server_ids))
        if not ids or len(ids)>100 or min(ids)<1: raise ValueError("Select 1-100 servers")
        if not 1<=int(canary_count)<=min(5,len(ids)) or not 1<=int(batch_size)<=20:
            raise ValueError("Invalid rollout batch configuration")
        release=self.db.execute("SELECT id,channel,created_at,artifact,signature FROM rs_agent_releases WHERE id=%s",(release_id,),fetch="one")
        if not release or not release.get("artifact") or not release.get("signature"):
            raise ValueError("A signed Agent artifact is required")
        previous=self.db.execute("""SELECT id FROM rs_agent_releases WHERE channel=%s AND created_at<%s
          AND artifact IS NOT NULL AND signature IS NOT NULL
          ORDER BY created_at DESC,id DESC LIMIT 1""",(release["channel"],release["created_at"]),fetch="one")
        previous_id=int(previous["id"]) if previous else None
        rows=self.db.execute("SELECT id FROM servers WHERE owner_id=%s AND id=ANY(%s)",(owner,ids),fetch="all")
        if {int(x['id']) for x in rows}!=set(ids): raise ValueError("A server is outside this workspace")
        row=self.db.execute("""INSERT INTO rs_agent_rollouts(owner_id,actor_id,release_id,previous_release_id,server_ids,batch_size,canary_count)
          VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",(owner,actor,release_id,previous_id,ids,batch_size,canary_count),fetch="one")
        self.pro.audit(owner,actor,'agent_rollout_created',str(row['id']),f'{len(ids)} servers')
        return {'id':row['id'],'state':'queued','servers':len(ids),'canary_count':canary_count,'batch_size':batch_size,
                'previous_release_id':previous_id}

    def agent_rollouts(self,actor,owner=None):
        owner=self.pro.workspace(actor,owner)
        rows=self.db.execute("""SELECT r.id,r.state,r.server_ids,r.batch_offset,r.created_at,r.updated_at,
          r.previous_release_id,r.active_operation_id,a.version FROM rs_agent_rollouts r JOIN rs_agent_releases a ON a.id=r.release_id
          WHERE r.owner_id=%s ORDER BY r.id DESC LIMIT 30""",(owner,),fetch='all')
        return [dict(x,created_at=x['created_at'].isoformat(),updated_at=x['updated_at'].isoformat()) for x in rows]

    def control_agent_rollout(self,actor,owner,rollout_id,action):
        self.pro.require(actor,owner,write=True)
        if action not in {"pause","resume","cancel","rollback"}: raise ValueError("Invalid rollout action")
        row=self.db.execute("""SELECT id,state,server_ids,batch_offset,active_operation_id,previous_release_id,rollback_attempts
          FROM rs_agent_rollouts WHERE id=%s AND owner_id=%s""",(int(rollout_id),owner),fetch="one")
        if not row: raise ValueError("Rollout not found")
        if action=="pause":
            if row["state"] not in ("queued","running"): raise ValueError("Rollout cannot be paused")
            self.db.execute("UPDATE rs_agent_rollouts SET state='paused',lease_until=NULL,updated_at=NOW() WHERE id=%s",(int(rollout_id),))
        elif action=="resume":
            if row["state"]!="paused": raise ValueError("Only paused rollouts can resume")
            self.db.execute("UPDATE rs_agent_rollouts SET state='running',lease_until=NULL,updated_at=NOW() WHERE id=%s",(int(rollout_id),))
        elif action=="cancel":
            if row["state"] not in ("queued","running","paused"): raise ValueError("Rollout cannot be cancelled")
            if row.get("active_operation_id"):
                try: self.control_operation(actor,owner,row["active_operation_id"],"cancel")
                except ValueError: pass
            self.db.execute("UPDATE rs_agent_rollouts SET state='cancelled',lease_until=NULL,updated_at=NOW() WHERE id=%s",(int(rollout_id),))
        elif action=="rollback":
            if row["state"] not in ("completed","failed","cancelled","rollback_failed"):
                raise ValueError("Only completed, failed, cancelled or rollback_failed rollouts can be rolled back")
            if row.get("active_operation_id"):
                active=self.db.execute("SELECT status FROM rs_operations WHERE id=%s AND owner_id=%s",
                    (row["active_operation_id"],owner),fetch="one")
                if active and active.get("status") not in ("succeeded","failed","cancelled"):
                    raise ValueError("The active rollout batch has not settled; wait for it to finish before rollback")
            if not row.get("previous_release_id"): raise ValueError("No previous signed release is available for rollback")
            completed=self.db.execute("""SELECT DISTINCT i.resource_id FROM rs_operation_items i
              JOIN rs_operations o ON o.id=i.operation_id
              WHERE o.owner_id=%s AND o.idempotency_key LIKE %s AND i.status='succeeded'""",
              (owner,f"rollout:{int(rollout_id)}:%"),fetch="all")
            successful=sorted({int(x["resource_id"]) for x in completed})
            if not successful: raise ValueError("No successfully updated servers are available to roll back")
            # Reserve the retry atomically. The worker resumes rollback_pending
            # rows, so a process crash between reservation and queue insertion
            # cannot strand the rollout or create duplicate rollback operations.
            reservation=self.db.execute("""UPDATE rs_agent_rollouts SET state='rollback_pending',
              rollback_attempts=rollback_attempts+1,rollback_operation_id=NULL,lease_until=NULL,
              detail=%s::jsonb,updated_at=NOW()
              WHERE id=%s AND owner_id=%s AND state IN ('completed','failed','cancelled','rollback_failed')
              RETURNING rollback_attempts""",
              (json.dumps({'reason':'manual_rollback','rollback':'pending'}),int(rollout_id),owner),fetch="one")
            if not reservation: raise ValueError("Rollout state changed; refresh and retry")
        self.pro.audit(owner,actor,"agent_rollout_"+action,str(rollout_id))
        return {"id":int(rollout_id),"action":action}

    def create_webhook(self,actor,owner,url,events):
        self.pro.require(actor,owner,write=True)
        from enterprise_runtime import _public_https_url
        _public_https_url(url)
        allowed={"incident.opened","incident.resolved","operation.completed","server.offline"}
        events=sorted(set(events))
        if not events or set(events)-allowed: raise ValueError("Unsupported webhook event")
        import os
        from cryptography.fernet import Fernet
        key=os.getenv("SONAR_WEBHOOK_ENCRYPTION_KEY") or os.getenv("SONAR_BACKUP_ENCRYPTION_KEY","")
        if not key: raise RuntimeError("Configure SONAR_WEBHOOK_ENCRYPTION_KEY")
        secret=secrets.token_urlsafe(40)
        encrypted=Fernet(key.encode()).encrypt(secret.encode()).decode()
        digest=hashlib.sha256(secret.encode()).hexdigest()
        row=self.db.execute("""INSERT INTO rs_webhooks(owner_id,actor_id,url,secret_hash,secret_ciphertext,events)
          VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",(owner,actor,url,digest,encrypted,events),fetch="one")
        self.pro.audit(owner,actor,"webhook_created",str(row["id"]))
        return {"id":row["id"],"secret":secret,"events":events}

    def analytics(self,actor,owner=None,days=30):
        owner=self.pro.workspace(actor,owner)
        from enterprise_runtime import analytics_summary
        return analytics_summary(self.db,owner,days)

    def authenticate_api_token(self,raw_token):
        if not isinstance(raw_token,str) or not raw_token.startswith("rs_") or len(raw_token)>100:
            return None
        digest=hashlib.sha256(raw_token.encode()).hexdigest()
        row=self.db.execute("""UPDATE rs_api_tokens SET
          request_count=CASE WHEN request_window_at<NOW()-INTERVAL '1 minute' THEN 1 ELSE request_count+1 END,
          request_window_at=CASE WHEN request_window_at<NOW()-INTERVAL '1 minute' THEN NOW() ELSE request_window_at END
          WHERE token_hash=%s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>NOW())
            AND (request_window_at<NOW()-INTERVAL '1 minute' OR request_count<120)
          RETURNING owner_id,actor_id,scopes""",(digest,),fetch="one")
        if row: return dict(row)
        existing=self.db.execute("SELECT id FROM rs_api_tokens WHERE token_hash=%s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>NOW())",
          (digest,),fetch="one")
        return {'rate_limited':True} if existing else None

    def revoke_api_token(self,actor,owner,token_id):
        self.pro.require(actor,owner,write=True)
        row=self.db.execute("UPDATE rs_api_tokens SET revoked_at=NOW() WHERE id=%s AND owner_id=%s RETURNING id",
          (int(token_id),int(owner)),fetch="one")
        if not row: raise ValueError("Token not found")
        self.pro.audit(owner,actor,"api_token_revoked",str(token_id))
        return True

    def create_api_token(self,actor,owner,label,scopes,expires_in_days=None):
        self.pro.require(actor,owner,write=True)
        allowed={'servers:read','incidents:read','incidents:write','metrics:read','operations:read','operations:write','workspace:admin','backups:write','automation:write','webhooks:write','tokens:write','analytics:read','agents:write','agents:read','backups:read','webhooks:read','tokens:read','health:write'}
        scopes=sorted(set(scopes))
        if not scopes or set(scopes)-allowed:raise ValueError('Invalid API scopes')
        if expires_in_days is not None and not 1<=int(expires_in_days)<=3650: raise ValueError('Token expiry must be 1-3650 days')
        raw='rs_'+secrets.token_urlsafe(32); digest=hashlib.sha256(raw.encode()).hexdigest()
        row=self.db.execute("""INSERT INTO rs_api_tokens(owner_id,actor_id,label,token_hash,scopes,expires_at)
          VALUES(%s,%s,%s,%s,%s,CASE WHEN %s IS NULL THEN NULL ELSE NOW()+make_interval(days => %s) END) RETURNING id,expires_at""",
          (owner,actor,label[:80],digest,scopes,expires_in_days,expires_in_days),fetch='one')
        self.pro.audit(owner,actor,'api_token_created',str(row['id']))
        return {'id':row['id'],'token':raw,'scopes':scopes,'expires_at':row['expires_at'].isoformat() if row['expires_at'] else None}

