
"""Operational workers for RadarSonarPlus 6.9.

Remote work is deliberately allow-listed. No API accepts arbitrary shell text.
"""
from __future__ import annotations
import asyncio, hashlib, hmac, json, logging, os, socket, ssl, subprocess, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import URLError, HTTPError

log = logging.getLogger("enterprise_runtime")
SERVICE_ALLOWLIST = {"sonar-agent", "nginx", "docker", "ssh"}
MAX_WEBHOOK_BYTES = 1_000_000

RUNTIME_SCHEMA = (
"""CREATE TABLE IF NOT EXISTS rs_agent_releases (
 id BIGSERIAL PRIMARY KEY, version VARCHAR(40) NOT NULL, artifact_url TEXT NOT NULL,
 sha256 CHAR(64) NOT NULL, signature TEXT, channel VARCHAR(16) NOT NULL DEFAULT 'stable',
 created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(version,channel))""",
"""CREATE TABLE IF NOT EXISTS rs_agent_rollouts (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
 release_id BIGINT NOT NULL REFERENCES rs_agent_releases(id), server_ids INTEGER[] NOT NULL,
 batch_size SMALLINT NOT NULL DEFAULT 1, canary_count SMALLINT NOT NULL DEFAULT 1,
 state VARCHAR(20) NOT NULL DEFAULT 'queued', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), detail JSONB NOT NULL DEFAULT '{}'::jsonb)""",
"""CREATE TABLE IF NOT EXISTS rs_automation_runs (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, rule_id BIGINT NOT NULL REFERENCES rs_automation_rules(id),
 server_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL, attempt SMALLINT NOT NULL DEFAULT 1,
 detail VARCHAR(500) NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""ALTER TABLE rs_api_tokens ADD COLUMN IF NOT EXISTS request_window_at TIMESTAMPTZ NOT NULL DEFAULT NOW()""",
"""ALTER TABLE rs_api_tokens ADD COLUMN IF NOT EXISTS request_count INTEGER NOT NULL DEFAULT 0""",
"""ALTER TABLE rs_agent_rollouts ADD COLUMN IF NOT EXISTS batch_offset INTEGER NOT NULL DEFAULT 0""",
"""ALTER TABLE rs_agent_rollouts ADD COLUMN IF NOT EXISTS active_operation_id UUID""",
"""ALTER TABLE rs_agent_rollouts ADD COLUMN IF NOT EXISTS active_batch_size SMALLINT NOT NULL DEFAULT 0""",
"""ALTER TABLE rs_automation_runs ADD COLUMN IF NOT EXISTS operation_id UUID""",
"""ALTER TABLE rs_backup_schedules ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW()""",
"""ALTER TABLE rs_backup_schedules ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ""",
"""ALTER TABLE rs_backup_schedules ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()""",
"""ALTER TABLE rs_operation_items ADD COLUMN IF NOT EXISTS attempts SMALLINT NOT NULL DEFAULT 0""",
"""ALTER TABLE rs_operation_items ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW()""",
"""ALTER TABLE rs_operation_items ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ""",
"""ALTER TABLE rs_operation_items ADD COLUMN IF NOT EXISTS last_error VARCHAR(80)""",
"""ALTER TABLE rs_operations DROP CONSTRAINT IF EXISTS rs_operations_status_check""",
"""ALTER TABLE rs_operations ADD CONSTRAINT rs_operations_status_check CHECK(status IN ('queued','running','paused','cancel_requested','succeeded','failed','cancelled'))""",
"""ALTER TABLE rs_webhook_deliveries ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ""",
"""ALTER TABLE rs_agent_rollouts ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ""",
"""ALTER TABLE rs_agent_rollouts ADD COLUMN IF NOT EXISTS previous_release_id BIGINT REFERENCES rs_agent_releases(id)""",
"""ALTER TABLE rs_agent_rollouts ADD COLUMN IF NOT EXISTS rollback_operation_id UUID""",
"""ALTER TABLE rs_agent_rollouts ADD COLUMN IF NOT EXISTS rollback_attempts INTEGER NOT NULL DEFAULT 0""",
"""ALTER TABLE rs_agent_releases ADD COLUMN IF NOT EXISTS artifact BYTEA""",
"""ALTER TABLE rs_agent_releases ADD COLUMN IF NOT EXISTS signature TEXT""",
"""CREATE INDEX IF NOT EXISTS rs_operation_items_claim ON rs_operation_items(status,next_attempt_at,lease_until)""",
"""ALTER TABLE rs_operations ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ""",
"""ALTER TABLE rs_webhooks ADD COLUMN IF NOT EXISTS secret_ciphertext TEXT""",
"""ALTER TABLE rs_webhooks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()""",
"""ALTER TABLE rs_webhook_deliveries ADD COLUMN IF NOT EXISTS last_status INTEGER""",
"""ALTER TABLE rs_webhook_deliveries ADD COLUMN IF NOT EXISTS last_error VARCHAR(80)""",
"""ALTER TABLE rs_webhook_deliveries ADD COLUMN IF NOT EXISTS dead_at TIMESTAMPTZ""",
"""ALTER TABLE rs_webhook_deliveries ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()""",
"""CREATE INDEX IF NOT EXISTS rs_webhook_delivery_claim ON rs_webhook_deliveries(next_attempt_at) WHERE delivered_at IS NULL""",
"""CREATE TABLE IF NOT EXISTS rs_agent_releases (
 id BIGSERIAL PRIMARY KEY, version VARCHAR(40) NOT NULL, artifact_url TEXT NOT NULL,
 sha256 CHAR(64) NOT NULL, signature TEXT, channel VARCHAR(16) NOT NULL DEFAULT 'stable',
 created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(version,channel))""",
"""CREATE TABLE IF NOT EXISTS rs_agent_rollouts (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
 release_id BIGINT NOT NULL REFERENCES rs_agent_releases(id), server_ids INTEGER[] NOT NULL,
 batch_size SMALLINT NOT NULL DEFAULT 1, canary_count SMALLINT NOT NULL DEFAULT 1,
 state VARCHAR(20) NOT NULL DEFAULT 'queued', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), detail JSONB NOT NULL DEFAULT '{}'::jsonb)""",
"""CREATE TABLE IF NOT EXISTS rs_automation_runs (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, rule_id BIGINT NOT NULL REFERENCES rs_automation_rules(id),
 server_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL, attempt SMALLINT NOT NULL DEFAULT 1,
 detail VARCHAR(500) NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
)

def install_runtime_schema(db):
    with db.get_connection() as (conn, cur):
        for sql in RUNTIME_SCHEMA:
            cur.execute(sql)
        conn.commit()

def _safe_destination(destination: str) -> Path:
    default_root = Path(os.getenv("SONAR_STATE_DIR", "/var/lib/sonar-radar")) / "backups"
    base = Path(os.getenv("SONAR_BACKUP_DIR", str(default_root))).resolve()
    candidate = (base / destination).resolve() if not Path(destination).is_absolute() else Path(destination).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("Backup destination must be inside SONAR_BACKUP_DIR")
    candidate.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return candidate

def _s3_location(destination):
    parsed=urlparse(str(destination))
    if parsed.scheme!='s3' or not parsed.netloc or not parsed.path.strip('/'):
        raise ValueError("Invalid S3 backup destination")
    allowed=os.getenv("SONAR_BACKUP_S3_BUCKET","")
    if not allowed or parsed.netloc!=allowed:
        raise ValueError("S3 bucket must match SONAR_BACKUP_S3_BUCKET")
    key=parsed.path.lstrip('/')
    if '..' in Path(key).parts:
        raise ValueError("Invalid S3 object key")
    return parsed.netloc,key

def validate_backup_destination(destination):
    if str(destination).startswith("s3://"):
        _s3_location(destination)
    else:
        _safe_destination(destination)

def _read_backup(destination):
    if str(destination).startswith("s3://"):
        import boto3
        bucket,key=_s3_location(destination)
        response=boto3.client("s3",region_name=os.getenv("SONAR_BACKUP_S3_REGION"),
             endpoint_url=os.getenv("SONAR_BACKUP_S3_ENDPOINT") or None).get_object(Bucket=bucket,Key=key)
        return response["Body"].read()
    return _safe_destination(destination).read_bytes()

def _delete_backup(destination):
    if str(destination).startswith("s3://"):
        import boto3
        bucket,key=_s3_location(destination)
        boto3.client("s3",region_name=os.getenv("SONAR_BACKUP_S3_REGION"),
             endpoint_url=os.getenv("SONAR_BACKUP_S3_ENDPOINT") or None).delete_object(Bucket=bucket,Key=key)
    else:
        _safe_destination(destination).unlink(missing_ok=True)

def create_encrypted_backup(db_config: dict, destination: str) -> dict:
    """Create encrypted pg_dump and write locally or to a configured S3 bucket."""
    from cryptography.fernet import Fernet
    key = os.getenv("SONAR_BACKUP_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("SONAR_BACKUP_ENCRYPTION_KEY is not configured")
    cipher = Fernet(key.encode("ascii"))
    validate_backup_destination(destination)
    env = os.environ.copy()
    env["PGPASSWORD"] = str(db_config.get("password") or "")
    cmd = ["pg_dump", "--no-owner", "--no-acl", "-Fc",
           "-h", str(db_config.get("host", "localhost")),
           "-p", str(db_config.get("port", 5432)),
           "-U", str(db_config.get("user", "")),
           "-d", str(db_config.get("dbname", ""))]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, timeout=int(os.getenv("SONAR_BACKUP_TIMEOUT", "1800")),
                          check=False)
    env.pop("PGPASSWORD", None)
    if proc.returncode:
        raise RuntimeError("PG_DUMP_FAILED")
    if len(proc.stdout) > int(os.getenv("SONAR_BACKUP_MAX_BYTES", str(1024**3))):
        raise RuntimeError("BACKUP_SIZE_LIMIT_EXCEEDED")
    encrypted = cipher.encrypt(proc.stdout)
    digest=hashlib.sha256(encrypted).hexdigest()
    if str(destination).startswith("s3://"):
        import boto3
        bucket,obj_key=_s3_location(destination)
        boto3.client("s3",region_name=os.getenv("SONAR_BACKUP_S3_REGION"),
            endpoint_url=os.getenv("SONAR_BACKUP_S3_ENDPOINT") or None).put_object(
            Bucket=bucket,Key=obj_key,Body=encrypted,ContentType="application/octet-stream",
            ServerSideEncryption="AES256")
        return {"path":str(destination),"bytes":len(encrypted),"sha256":digest}
    target = _safe_destination(destination)
    target_tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(target_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
    os.replace(target_tmp, target)
    return {"path": str(target), "bytes": len(encrypted), "sha256": digest}

def restore_encrypted_backup(db_config: dict, source: str, confirm: bool = False) -> dict:
    """Destructive restore helper. Requires explicit CLI confirmation."""
    if not confirm:
        raise PermissionError("Restore requires explicit confirmation")
    from cryptography.fernet import Fernet
    key = os.getenv("SONAR_BACKUP_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("SONAR_BACKUP_ENCRYPTION_KEY is not configured")
    raw = _read_backup(source)
    dump = Fernet(key.encode("ascii")).decrypt(raw)
    env = os.environ.copy(); env["PGPASSWORD"] = str(db_config.get("password") or "")
    cmd = ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-acl",
           "-h", str(db_config.get("host", "localhost")), "-p", str(db_config.get("port", 5432)),
           "-U", str(db_config.get("user", "")), "-d", str(db_config.get("dbname", ""))]
    proc = subprocess.run(cmd, input=dump, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, timeout=1800, check=False)
    env.pop("PGPASSWORD", None)
    if proc.returncode:
        raise RuntimeError("PG_RESTORE_FAILED")
    return {"restored": True, "bytes": len(dump)}

def _claim_operation(db):
    with db.get_connection() as (conn, cur):
        cur.execute("""UPDATE rs_operation_items SET status='failed',detail='worker_lease_expired_retry_limit',
          last_error='WORKER_LEASE_EXPIRED',finished_at=NOW(),lease_until=NULL
          WHERE status='running' AND lease_until<NOW() AND attempts>=3""")
        cur.execute("""UPDATE rs_operations o SET
          failed=(SELECT COUNT(*) FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status='failed'),
          status=CASE WHEN o.status='paused' THEN 'paused'
            WHEN o.status='cancel_requested' AND NOT EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status='running') THEN 'cancelled'
            WHEN NOT EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status IN ('queued','running')) THEN 'failed'
            ELSE o.status END,
          finished_at=CASE WHEN NOT EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status IN ('queued','running')) THEN NOW() ELSE o.finished_at END,
          updated_at=NOW()
          WHERE EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status='failed' AND i.last_error='WORKER_LEASE_EXPIRED')""")
        cur.execute("""SELECT i.id AS item_id,i.operation_id,i.resource_id,i.attempts,o.owner_id,o.actor_id,o.kind,o.payload
          FROM rs_operation_items i JOIN rs_operations o ON o.id=i.operation_id
          WHERE (i.status='queued' OR (i.status='running' AND (i.lease_until IS NULL OR i.lease_until<NOW()))) AND i.attempts < 3
            AND i.next_attempt_at <= NOW()
            AND (i.lease_until IS NULL OR i.lease_until < NOW())
            AND o.status IN ('queued','running')
          ORDER BY o.created_at,i.id FOR UPDATE OF i SKIP LOCKED LIMIT 1""")
        row = cur.fetchone()
        if not row:
            conn.commit(); return None
        cur.execute("""UPDATE rs_operation_items SET status='running',attempts=attempts+1,
          lease_until=NOW()+INTERVAL '5 minutes',started_at=COALESCE(started_at,NOW())
          WHERE id=%s""", (row["item_id"],))
        cur.execute("UPDATE rs_operations SET status='running',updated_at=NOW() WHERE id=%s",
                    (row["operation_id"],))
        conn.commit()
        return dict(row)

def _finish_item(db, job, status, detail="", retry=False):
    safe = str(detail or "")[:500]
    with db.get_connection() as (conn, cur):
        if retry:
            cur.execute("""UPDATE rs_operation_items SET status='queued',last_error=%s,
              next_attempt_at=NOW()+make_interval(secs=>LEAST(300,POWER(2,attempts)::int*15)),
              lease_until=NULL,finished_at=NULL,detail=%s WHERE id=%s""",
              (safe[:80], safe, job["item_id"]))
        else:
            cur.execute("""UPDATE rs_operation_items SET status=%s,attempts=CASE WHEN %s='failed' THEN 3 ELSE attempts END,detail=%s,last_error=%s,
              lease_until=NULL,finished_at=NOW() WHERE id=%s""",
              (status, status, safe, None if status == "succeeded" else safe[:80], job["item_id"]))
        cur.execute("""UPDATE rs_operations o SET
          succeeded=(SELECT COUNT(*) FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status='succeeded'),
          failed=(SELECT COUNT(*) FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status='failed' AND i.attempts>=3),
          status=CASE
            WHEN o.status='cancel_requested' AND NOT EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status='running') THEN 'cancelled'
            WHEN o.status='paused' THEN 'paused'
            WHEN NOT EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status IN ('queued','running','failed') AND i.attempts<3) THEN
              CASE WHEN EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status IN ('failed','skipped')) THEN 'failed' ELSE 'succeeded' END
            ELSE 'running' END,
          finished_at=CASE WHEN
            (o.status='cancel_requested' AND NOT EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status='running'))
            OR (o.status<>'paused' AND NOT EXISTS(SELECT 1 FROM rs_operation_items i WHERE i.operation_id=o.id AND i.status IN ('queued','running','failed') AND i.attempts<3))
            THEN NOW() ELSE NULL END,
          updated_at=NOW() WHERE o.id=%s""", (job["operation_id"],))
        conn.commit()
    try:
        current=db.execute("SELECT status,owner_id,id FROM rs_operations WHERE id=%s",(job["operation_id"],),fetch="one")
        if current and current["status"] in ("succeeded","failed"):
            enqueue_webhook_event(db,int(current["owner_id"]),"operation.completed",
                {"operation_id":str(current["id"]),"status":current["status"]},
                event_key="operation:"+str(current["id"]))
    except Exception:
        log.exception("Unable to enqueue operation webhook event")

def verify_agent_release_bytes(version, sha256, signature, artifact):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    key_text=os.getenv("SONAR_AGENT_RELEASE_PUBLIC_KEY","")
    if not key_text:
        raise RuntimeError("SONAR_AGENT_RELEASE_PUBLIC_KEY_NOT_CONFIGURED")
    try:
        key_bytes=base64.b64decode(key_text,validate=True)
        public_key=Ed25519PublicKey.from_public_bytes(key_bytes)
        sig=base64.b64decode(signature,validate=True)
    except Exception:
        raise ValueError("INVALID_AGENT_RELEASE_KEY_OR_SIGNATURE_ENCODING") from None
    if not isinstance(artifact,(bytes,bytearray)) or not artifact or len(artifact)>2_000_000:
        raise ValueError("INVALID_AGENT_ARTIFACT_SIZE")
    actual=hashlib.sha256(artifact).hexdigest()
    if not hmac.compare_digest(actual,str(sha256).lower()):
        raise ValueError("AGENT_ARTIFACT_HASH_MISMATCH")
    try:
        public_key.verify(sig,bytes(artifact))
    except Exception:
        raise ValueError("AGENT_ARTIFACT_SIGNATURE_INVALID") from None
    source=bytes(artifact).decode("utf-8")
    import re
    match=re.search(r"^AGENT_VERSION\s*=\s*['\"]([^'\"]+)['\"]",source,re.M)
    if not match or match.group(1)!=str(version):
        raise ValueError("AGENT_ARTIFACT_VERSION_MISMATCH")
    return source

def _execute_operation(db, job):
    from core import ServerMonitor, sec
    from database import Database
    row = db.execute("""SELECT id,ip,port,username,password,ws_port,is_active FROM servers
       WHERE id=%s AND owner_id=%s""", (job["resource_id"], job["owner_id"]), fetch="one")
    if not row or not row.get("is_active"):
        raise ValueError("SERVER_UNAVAILABLE")
    kind = job["kind"]
    if kind == "diagnostic":
        result = asyncio.run(ServerMonitor.ws_send_command(row["ip"], int(row.get("ws_port") or 8080),
                          "", {"action": "get_stats"}, timeout=15))
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError("AGENT_DIAGNOSTIC_FAILED")
        return "diagnostic_ok"
    if kind == "service_restart":
        service = str((job.get("payload") or {}).get("service", "sonar-agent"))
        if service not in SERVICE_ALLOWLIST:
            raise ValueError("SERVICE_NOT_ALLOWLISTED")
        ok, output = ServerMonitor._ssh_exec_sync(row["ip"], int(row["port"]), row["username"],
                   sec.decrypt(row["password"]), "systemctl restart " + service + " && systemctl is-active " + service, timeout=45)
        if not ok:
            raise RuntimeError("SERVICE_RESTART_FAILED")
        return "service_restarted:" + service
    if kind == "agent_update":
        # Deploy either the checked-out agent (legacy direct operation) or a verified signed release artifact.
        from settings import AGENT_PORT
        artifact_script=None
        release_id=(job.get("payload") or {}).get("release_id")
        if release_id:
            release=db.execute("SELECT version,sha256,signature,artifact FROM rs_agent_releases WHERE id=%s",(int(release_id),),fetch="one")
            if not release or not release.get("artifact") or not release.get("signature"):
                raise ValueError("SIGNED_AGENT_RELEASE_NOT_FOUND")
            artifact_script=verify_agent_release_bytes(release["version"],release["sha256"],release["signature"],bytes(release["artifact"]))
        ok, detail = ServerMonitor.install_agent_service(row["ip"], int(row["port"]), row["username"],
                    sec.decrypt(row["password"]), int(row.get("ws_port") or AGENT_PORT),agent_script=artifact_script)
        if not ok:
            raise RuntimeError("AGENT_UPDATE_FAILED")
        return "agent_update_deployed:" + str(detail)[:180]
    if kind == "maintenance":
        # Explicitly bounded read-only check; no arbitrary maintenance scripts.
        return "maintenance_check_only"
    raise ValueError("UNSUPPORTED_OPERATION")

async def operations_worker(db, stop):
    while not stop.is_set():
        try:
            job = await asyncio.to_thread(_claim_operation, db)
            if job:
                try:
                    result = await asyncio.to_thread(_execute_operation, db, job)
                    await asyncio.to_thread(_finish_item, db, job, "succeeded", result, False)
                except Exception as exc:
                    transient = isinstance(exc, (TimeoutError, ConnectionError)) or "TIMEOUT" in str(exc)
                    log.warning("Enterprise operation item failed id=%s type=%s", job.get("item_id"), type(exc).__name__)
                    await asyncio.to_thread(_finish_item, db, job, "failed", type(exc).__name__.upper(), transient and int(job.get("attempts",0)) < max(0,min(3,int((job.get('payload') or {}).get('max_attempts',3)))-1))
                continue
            await asyncio.sleep(2)
        except asyncio.CancelledError: raise
        except Exception:
            log.exception("Operations worker iteration failed")
            await asyncio.sleep(5)

def _public_https_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Webhook URL must be HTTPS without credentials")
    if parsed.port not in (None, 443):
        raise ValueError("Webhook custom ports are not allowed")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Webhook host does not resolve")
    import ipaddress
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if not ip.is_global:
            raise ValueError("Webhook host resolves to a non-public address")
    return parsed

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def deliver_webhook(url, secret, payload):
    parsed = _public_https_url(url)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = Request(url, data=body, headers={"Content-Type":"application/json",
                "User-Agent":"RadarSonarPlus-Webhook/6.9",
                "X-Radar-Signature":"sha256="+signature}, method="POST")
    opener = build_opener(_NoRedirect)
    with opener.open(req, timeout=8) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError("WEBHOOK_HTTP_STATUS")
        response.read(1024)
        return int(response.status)

def enqueue_webhook_event(db, owner_id, event_type, payload, event_key=None):
    event_key = event_key or hashlib.sha256((event_type+json.dumps(payload,sort_keys=True)).encode()).hexdigest()
    rows = db.execute("""SELECT id,url,secret_ciphertext,secret_hash FROM rs_webhooks
       WHERE owner_id=%s AND enabled=TRUE AND %s=ANY(events)""", (owner_id,event_type), fetch="all")
    from cryptography.fernet import Fernet
    key=os.getenv("SONAR_WEBHOOK_ENCRYPTION_KEY") or os.getenv("SONAR_BACKUP_ENCRYPTION_KEY","")
    if not key: return 0
    cipher=Fernet(key.encode())
    count=0
    for hook in rows:
        secret=cipher.decrypt(hook["secret_ciphertext"].encode()).decode()
        db.execute("""INSERT INTO rs_webhook_deliveries(webhook_id,event_key,payload)
          VALUES(%s,%s,%s::jsonb) ON CONFLICT(webhook_id,event_key) DO NOTHING""",
          (hook["id"],event_key,json.dumps({"event":event_type,"data":payload})))
        count+=1
    return count

def _claim_webhook(db):
    with db.get_connection() as (conn,cur):
        cur.execute("""SELECT d.id,d.webhook_id,d.payload,d.attempts,w.url,w.secret_ciphertext
          FROM rs_webhook_deliveries d JOIN rs_webhooks w ON w.id=d.webhook_id
          WHERE d.delivered_at IS NULL AND d.dead_at IS NULL AND d.attempts<8 AND d.next_attempt_at<=NOW()
            AND (d.lease_until IS NULL OR d.lease_until<NOW()) AND w.enabled=TRUE ORDER BY d.id FOR UPDATE OF d SKIP LOCKED LIMIT 1""")
        row=cur.fetchone()
        if not row: conn.commit(); return None
        cur.execute("UPDATE rs_webhook_deliveries SET attempts=attempts+1,lease_until=NOW()+INTERVAL '90 seconds' WHERE id=%s",(row["id"],))
        conn.commit(); return dict(row)

async def webhook_worker(db, stop):
    while not stop.is_set():
        try:
            row=await asyncio.to_thread(_claim_webhook,db)
            if row:
                try:
                    from cryptography.fernet import Fernet
                    key=os.getenv("SONAR_WEBHOOK_ENCRYPTION_KEY") or os.getenv("SONAR_BACKUP_ENCRYPTION_KEY","")
                    secret=Fernet(key.encode()).decrypt(row["secret_ciphertext"].encode()).decode()
                    http_status=await asyncio.to_thread(deliver_webhook,row["url"],secret,row["payload"])
                    await asyncio.to_thread(db.execute,
                        "UPDATE rs_webhook_deliveries SET delivered_at=NOW(),last_status=%s,last_error=NULL,lease_until=NULL WHERE id=%s",
                        (http_status,row["id"]))
                except Exception as exc:
                    delay=min(3600,15*(2**min(int(row["attempts"]),8)))
                    await asyncio.to_thread(db.execute, """UPDATE rs_webhook_deliveries SET next_attempt_at=NOW()+(%s||' seconds')::interval,
                       last_error=%s,lease_until=NULL,dead_at=CASE WHEN attempts>=8 THEN NOW() ELSE NULL END WHERE id=%s""",
                       (delay,type(exc).__name__.upper()[:80],row["id"]))
                continue
            await asyncio.sleep(3)
        except asyncio.CancelledError: raise
        except Exception:
            log.exception("Webhook worker iteration failed"); await asyncio.sleep(5)


def scheduled_backup_worker(db, stop):
    """Run due backups with a lease; a crashed worker leaves the schedule due for retry."""
    from settings import DB_CONFIG
    while not stop.is_set():
        try:
            with db.get_connection() as (conn, cur):
                cur.execute("""SELECT id,owner_id,name,destination,retention_count,cadence FROM rs_backup_schedules
                  WHERE enabled=TRUE AND next_run_at<=NOW() AND (lease_until IS NULL OR lease_until<NOW())
                  ORDER BY next_run_at FOR UPDATE SKIP LOCKED LIMIT 1""")
                row=cur.fetchone()
                if row:
                    cur.execute("UPDATE rs_backup_schedules SET lease_until=NOW()+INTERVAL '1 hour' WHERE id=%s",(row['id'],))
                conn.commit()
            if row:
                succeeded=False
                try:
                    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    requested=str(row['destination'])
                    if requested.startswith('s3://'):
                        bucket,obj_key=_s3_location(requested)
                        prefix=obj_key.rstrip('/')
                        name=f"s3://{bucket}/{prefix}-{row['owner_id']}-{row['id']}-{stamp}.dump.enc"
                    else:
                        from pathlib import PurePath
                        filename=PurePath(requested).name
                        if not filename or filename in ('.','..'): raise ValueError("Invalid scheduled backup filename")
                        stem=filename[:-8] if filename.endswith('.dump.enc') else filename.rsplit('.',1)[0]
                        name=f"{stem}-{row['owner_id']}-{row['id']}-{stamp}.dump.enc"
                    result=awaitable_backup(DB_CONFIG,name)
                    db.execute("""INSERT INTO rs_backup_runs(schedule_id,owner_id,path,sha256,bytes,status)
                      VALUES(%s,%s,%s,%s,%s,'succeeded')""",
                      (row['id'],row['owner_id'],result['path'],result['sha256'],result['bytes']))
                    stale=db.execute("""SELECT path FROM rs_backup_runs WHERE schedule_id=%s AND path IS NOT NULL
                      AND id NOT IN (SELECT id FROM rs_backup_runs WHERE schedule_id=%s ORDER BY created_at DESC LIMIT %s)""",
                      (row['id'],row['id'],row['retention_count']),fetch="all")
                    db.execute("""DELETE FROM rs_backup_runs WHERE schedule_id=%s AND id NOT IN
                      (SELECT id FROM rs_backup_runs WHERE schedule_id=%s ORDER BY created_at DESC LIMIT %s)""",
                      (row['id'],row['id'],row['retention_count']))
                    for old_file in stale:
                        try: _delete_backup(old_file['path'])
                        except Exception: log.warning("Skipped unsafe backup retention path")
                    succeeded=True
                except Exception as exc:
                    log.exception("Scheduled backup failed schedule=%s",row['id'])
                    db.execute("""INSERT INTO rs_backup_runs(schedule_id,owner_id,status,error_code)
                      VALUES(%s,%s,'failed',%s)""",(row['id'],row['owner_id'],type(exc).__name__.upper()[:80]))
                finally:
                    if succeeded:
                        interval="1 day" if row['cadence']=="daily" else "7 days"
                        db.execute(f"""UPDATE rs_backup_schedules SET next_run_at=NOW()+INTERVAL '{interval}',
                          last_run_at=NOW(),lease_until=NULL WHERE id=%s""",(row['id'],))
                    else:
                        db.execute("""UPDATE rs_backup_schedules SET next_run_at=NOW()+INTERVAL '15 minutes',
                          lease_until=NULL WHERE id=%s""",(row['id'],))
                continue
            time.sleep(10)
        except Exception:
            log.exception("Backup scheduler iteration failed"); time.sleep(10)

def awaitable_backup(db_config, name):
    return create_encrypted_backup(db_config,name)

def _claim_rollouts(db):
    """Claim a small batch of rollouts without blocking the asyncio loop."""
    with db.get_connection() as (conn,cur):
        cur.execute("""SELECT r.*,a.version,a.sha256 FROM rs_agent_rollouts r
          JOIN rs_agent_releases a ON a.id=r.release_id
          WHERE r.state IN ('queued','running','rollback_pending','rolling_back') AND (r.lease_until IS NULL OR r.lease_until<NOW())
          ORDER BY r.id FOR UPDATE OF r SKIP LOCKED LIMIT 5""")
        rows=[dict(x) for x in cur.fetchall()]
        for claimed in rows:
            cur.execute("UPDATE rs_agent_rollouts SET lease_until=NOW()+INTERVAL '2 minutes' WHERE id=%s",
                        (claimed['id'],))
        conn.commit()
    return rows


def _rollout_successful_servers(db, owner_id, rollout_id):
    rows=db.execute("""SELECT DISTINCT i.resource_id FROM rs_operation_items i
      JOIN rs_operations o ON o.id=i.operation_id
      WHERE o.owner_id=%s AND o.idempotency_key LIKE %s AND i.status='succeeded'""",
      (int(owner_id),f"rollout:{int(rollout_id)}:%"),fetch='all')
    return sorted({int(row['resource_id']) for row in rows})


def _resume_rollout_rollback(db, rollout, reason=None):
    """Finish a reserved rollback. Safe to retry after a worker/process restart."""
    rollout_id=int(rollout['id'])
    owner_id=int(rollout['owner_id'])
    previous_release_id=rollout.get('previous_release_id')
    attempt=max(1,int(rollout.get('rollback_attempts') or 1))
    successful=_rollout_successful_servers(db,owner_id,rollout_id)
    if not previous_release_id or not successful:
        detail={'reason':reason or 'rollback_unavailable',
                'rollback':'unavailable' if not previous_release_id else 'no_successful_hosts'}
        db.execute("UPDATE rs_agent_rollouts SET state='failed',lease_until=NULL,detail=%s::jsonb,updated_at=NOW() WHERE id=%s",
                   (json.dumps(detail),rollout_id))
        return {'state':'failed','servers':len(successful)}
    try:
        from enterprise_services import EnterpriseStore
        from pro_store import ProStore
        store=EnterpriseStore(ProStore(db))
        operation=store.create_operation(owner_id,owner_id,'agent_update',successful,
            f'rollout-rollback:{rollout_id}:attempt:{attempt}',
            {'release_id':int(previous_release_id),'rollback_of':rollout_id,
             'automatic_rollback':bool((rollout.get('detail') or {}).get('automatic_rollback',False))})
    except Exception as exc:
        detail={'reason':reason or 'rollback_enqueue_failed','rollback':'enqueue_failed','error':type(exc).__name__}
        db.execute("UPDATE rs_agent_rollouts SET state='rollback_failed',lease_until=NULL,detail=%s::jsonb,updated_at=NOW() WHERE id=%s",
                   (json.dumps(detail),rollout_id))
        log.exception("Agent rollback could not be queued rollout=%s attempt=%s",rollout_id,attempt)
        return {'state':'rollback_failed','servers':len(successful)}
    detail={'reason':reason or 'manual_rollback','rollback':'queued','servers':successful,'attempt':attempt}
    db.execute("""UPDATE rs_agent_rollouts SET state='rolling_back',rollback_operation_id=%s,
      active_operation_id=NULL,active_batch_size=0,lease_until=NULL,detail=%s::jsonb,updated_at=NOW() WHERE id=%s""",
      (operation['id'],json.dumps(detail),rollout_id))
    log.warning("Agent rollback queued rollout=%s hosts=%s attempt=%s",rollout_id,len(successful),attempt)
    return {'state':'rolling_back','servers':len(successful),'operation_id':operation['id']}


def _begin_automatic_rollout_rollback(db, rollout, reason):
    """Reserve and queue rollback for every host already updated by this rollout."""
    rollout_id=int(rollout['id'])
    owner_id=int(rollout['owner_id'])
    previous_release_id=rollout.get('previous_release_id')
    successful=_rollout_successful_servers(db,owner_id,rollout_id)
    if not previous_release_id or not successful:
        detail={'reason':reason,'rollback':'unavailable' if not previous_release_id else 'no_successful_hosts'}
        db.execute("UPDATE rs_agent_rollouts SET state='failed',lease_until=NULL,detail=%s::jsonb,updated_at=NOW() WHERE id=%s",
                   (json.dumps(detail),rollout_id))
        return {'state':'failed','servers':len(successful)}
    detail={'reason':reason,'rollback':'pending','automatic_rollback':True}
    reservation=db.execute("""UPDATE rs_agent_rollouts SET state='rollback_pending',
      rollback_attempts=rollback_attempts+1,rollback_operation_id=NULL,active_operation_id=NULL,
      active_batch_size=0,lease_until=NULL,detail=%s::jsonb,updated_at=NOW()
      WHERE id=%s AND state IN ('queued','running') RETURNING rollback_attempts""",
      (json.dumps(detail),rollout_id),fetch='one')
    if not reservation:
        current=db.execute("SELECT state,rollback_attempts,previous_release_id FROM rs_agent_rollouts WHERE id=%s",
                           (rollout_id,),fetch='one')
        if current and current.get('state')=='rollback_pending':
            rollout=dict(rollout,rollback_attempts=current.get('rollback_attempts'),
                         previous_release_id=current.get('previous_release_id'))
            return _resume_rollout_rollback(db,rollout,reason)
        return {'state':(current or {}).get('state','failed'),'servers':len(successful)}
    rollout=dict(rollout,rollback_attempts=reservation['rollback_attempts'],
                 detail={'automatic_rollback':True})
    return _resume_rollout_rollback(db,rollout,reason)



def _process_service_down_rule(db, rule):
    """Notify from pro_services, the authoritative table for HTTP/TCP probes."""
    services=db.execute("""SELECT id,name,last_detail FROM pro_services
      WHERE owner_id=%s AND enabled=TRUE AND status='down'
        AND last_checked_at>NOW()-INTERVAL '10 minutes'
      ORDER BY last_checked_at DESC LIMIT 20""",(rule['owner_id'],),fetch='all')
    if rule['action']!='notify':
        log.warning("Ignoring legacy service_down action without a server mapping rule=%s",rule['id'])
        return 0
    created=0
    for service in services:
        event_key=f"automation:{rule['id']}:service:{service['id']}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        message=f"Automation rule {rule['id']}: monitored service {service['name']} is DOWN ({service.get('last_detail') or 'no detail'})"
        db.execute("""INSERT INTO pro_notification_outbox(owner_id,event_key,event_type,message)
          VALUES(%s,%s,'automation',%s) ON CONFLICT(event_key) DO NOTHING""",
          (rule['owner_id'],event_key,message[:500]))
        created+=1
    return created


async def recovery_worker(db, stop):
    """Evaluate allow-listed rules against the actual source table for each event."""
    while not stop.is_set():
        try:
            rules=await asyncio.to_thread(db.execute,"""SELECT id,owner_id,event,action,max_attempts FROM rs_automation_rules
              WHERE enabled=TRUE""",fetch='all')
            for rule in rules:
                event=rule['event']
                if event=='service_down':
                    # Service probes have no guaranteed one-to-one server mapping,
                    # so the safe supported action is notification only.
                    await asyncio.to_thread(_process_service_down_rule,db,rule)
                    continue
                if event=='agent_unreachable':
                    condition="COALESCE(last_agent_status,'') ILIKE ANY(ARRAY['offline','disconnected','down','failed'])"
                elif event=='disk_pressure':
                    condition="COALESCE(last_disk,0)>=85"
                else:
                    continue
                servers=await asyncio.to_thread(db.execute,f"""SELECT id,name,last_disk FROM servers WHERE owner_id=%s AND is_active=1
                  AND {condition} AND last_checked_at>NOW()-INTERVAL '10 minutes' LIMIT 20""",
                  (rule['owner_id'],),fetch='all')
                for server in servers:
                    if rule['action']=='notify':
                        await asyncio.to_thread(db.execute,"""INSERT INTO pro_notification_outbox(owner_id,event_key,event_type,message)
                          VALUES(%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING""",
                          (rule['owner_id'],f"automation:{rule['id']}:{server['id']}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}",
                           'automation',f"Automation rule {rule['id']}: {event} on {server['name']}"))
                        continue
                    if event!='agent_unreachable' or rule['action']!='restart_managed_service':
                        continue
                    event_key=f"recovery:{rule['id']}:{server['id']}:"+datetime.now(timezone.utc).strftime("%Y%m%d%H")
                    existing=await asyncio.to_thread(db.execute,"SELECT id FROM rs_automation_runs WHERE rule_id=%s AND server_id=%s AND created_at>NOW()-INTERVAL '1 hour' AND status IN ('queued','running','succeeded','failed')",
                      (rule['id'],server['id']),fetch='one')
                    if existing: continue
                    try:
                        from enterprise_services import EnterpriseStore
                        from pro_store import ProStore
                        svc=EnterpriseStore(ProStore(db))
                        op=await asyncio.to_thread(svc.create_operation,int(rule['owner_id']),int(rule['owner_id']),'service_restart',
                           [int(server['id'])],event_key,{'service':'sonar-agent','recovery_rule':int(rule['id']),'max_attempts':int(rule['max_attempts'])})
                        await asyncio.to_thread(db.execute,"INSERT INTO rs_automation_runs(owner_id,rule_id,server_id,status,operation_id) VALUES(%s,%s,%s,'queued',%s)",
                          (rule['owner_id'],rule['id'],server['id'],op['id']))
                    except Exception:
                        log.exception("Auto recovery enqueue failed rule=%s server=%s",rule['id'],server['id'])
            await asyncio.sleep(30)
        except asyncio.CancelledError: raise
        except Exception:
            log.exception("Auto recovery iteration failed"); await asyncio.sleep(30)


async def rollout_worker(db, stop):
    """Canary-first rollout; failed batches trigger rollback of all successful hosts."""
    while not stop.is_set():
        try:
            rows=await asyncio.to_thread(_claim_rollouts,db)
            progressed=False
            for rollout in rows:
                if rollout['state']=='rollback_pending':
                    await asyncio.to_thread(_resume_rollout_rollback,db,rollout,'reserved_rollback')
                    progressed=True; continue
                if rollout['state']=='rolling_back':
                    rollback_id=rollout.get('rollback_operation_id')
                    op=await asyncio.to_thread(db.execute,"SELECT status FROM rs_operations WHERE id=%s",
                        (rollback_id,),fetch='one') if rollback_id else None
                    if op and op['status'] in ('succeeded','failed','cancelled'):
                        final='rolled_back' if op['status']=='succeeded' else 'rollback_failed'
                        await asyncio.to_thread(db.execute,"UPDATE rs_agent_rollouts SET state=%s,lease_until=NULL,updated_at=NOW() WHERE id=%s",
                            (final,rollout['id']))
                        progressed=True
                    else:
                        await asyncio.to_thread(db.execute,"UPDATE rs_agent_rollouts SET lease_until=NULL WHERE id=%s",(rollout['id'],))
                    continue
                ids=list(rollout['server_ids'] or [])
                offset=int(rollout['batch_offset'] or 0)
                active=rollout.get('active_operation_id')
                if active:
                    op=await asyncio.to_thread(db.execute,"SELECT status FROM rs_operations WHERE id=%s",(active,),fetch='one')
                    if not op or op['status'] in ('failed','cancelled'):
                        await asyncio.to_thread(_begin_automatic_rollout_rollback,db,rollout,'batch_failed')
                        progressed=True; continue
                    if op['status']!='succeeded':
                        await asyncio.to_thread(db.execute,'UPDATE rs_agent_rollouts SET lease_until=NULL WHERE id=%s',(rollout['id'],))
                        continue
                    offset += min(int(rollout.get('active_batch_size') or rollout['batch_size']),len(ids)-offset)
                    await asyncio.to_thread(db.execute,"UPDATE rs_agent_rollouts SET batch_offset=%s,active_operation_id=NULL,active_batch_size=0,updated_at=NOW() WHERE id=%s",
                      (offset,rollout['id']))
                if offset>=len(ids):
                    await asyncio.to_thread(db.execute,"UPDATE rs_agent_rollouts SET state='completed',lease_until=NULL,updated_at=NOW() WHERE id=%s",(rollout['id'],))
                    progressed=True; continue
                release=await asyncio.to_thread(db.execute,"SELECT artifact,signature FROM rs_agent_releases WHERE id=%s",
                    (rollout['release_id'],),fetch='one')
                try:
                    if not release or not release.get('artifact') or not release.get('signature'):
                        raise ValueError("signed_artifact_missing")
                    verify_agent_release_bytes(rollout['version'],rollout['sha256'],release['signature'],bytes(release['artifact']))
                except Exception as exc:
                    await asyncio.to_thread(_begin_automatic_rollout_rollback,db,rollout,'release_verification_failed:'+type(exc).__name__)
                    progressed=True; continue
                batch_size=int(rollout['canary_count']) if offset==0 else int(rollout['batch_size'])
                batch=ids[offset:offset+batch_size]
                from enterprise_services import EnterpriseStore
                from pro_store import ProStore
                svc=EnterpriseStore(ProStore(db))
                key=f"rollout:{rollout['id']}:offset:{offset}"
                op=await asyncio.to_thread(svc.create_operation,int(rollout['owner_id']),int(rollout['owner_id']),
                    'agent_update',batch,key,{'release_id':int(rollout['release_id']),'version':rollout['version']})
                await asyncio.to_thread(db.execute,"UPDATE rs_agent_rollouts SET state='running',active_operation_id=%s,active_batch_size=%s,lease_until=NULL,updated_at=NOW() WHERE id=%s",
                  (op['id'],len(batch),rollout['id']))
                progressed=True
            if not progressed: await asyncio.sleep(8)
        except asyncio.CancelledError: raise
        except Exception:
            log.exception("Agent rollout worker failed"); await asyncio.sleep(10)

def analytics_summary(db, owner_id, days=30):
    days=max(1,min(365,int(days)))
    rows=db.execute("""SELECT COUNT(*) AS incidents,
      COUNT(*) FILTER(WHERE status='resolved') AS resolved,
      AVG(EXTRACT(EPOCH FROM (COALESCE(resolved_at,NOW())-opened_at))/3600.0)
        FILTER(WHERE status='resolved') AS mean_resolution_hours
      FROM pro_incidents WHERE owner_id=%s AND opened_at>=NOW()-(%s||' days')::interval""",
      (owner_id,days),fetch="one")
    ops=db.execute("""SELECT COUNT(*) AS total,
      COUNT(*) FILTER(WHERE status='succeeded') AS succeeded,
      COUNT(*) FILTER(WHERE status='failed') AS failed
      FROM rs_operations WHERE owner_id=%s AND created_at>=NOW()-(%s||' days')::interval""",
      (owner_id,days),fetch="one")
    usage=db.execute("""SELECT COUNT(*) AS servers,
      AVG(last_cpu) AS avg_cpu,AVG(last_ram) AS avg_ram,AVG(last_disk) AS avg_disk
      FROM servers WHERE owner_id=%s AND is_active=1""",(owner_id,),fetch="one")
    return {"period_days":days,"incidents":dict(rows or {}),"operations":dict(ops or {}),
            "resources":dict(usage or {})}
