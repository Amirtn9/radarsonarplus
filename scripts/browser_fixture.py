#!/usr/bin/env python3
"""Dedicated local synthetic browser backend; never uses an application database."""
import argparse
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TOKEN='fixture-only-not-a-telegram-bot-token'


def main(port):
    if os.getenv('SONAR_BROWSER_FIXTURE')!='1':raise RuntimeError('Explicit synthetic fixture flag required')
    import psycopg2
    from psycopg2 import sql
    config=psycopg2.extensions.parse_dsn(os.environ['SONAR_TEST_DB_DSN'])
    if config.get('host') not in ('127.0.0.1','localhost','::1'):
        raise RuntimeError('Only a local PostgreSQL test administrator is permitted')
    name='sonar_browser_'+uuid.uuid4().hex
    admin=psycopg2.connect(os.environ['SONAR_TEST_DB_DSN']);admin.autocommit=True
    state=Path(tempfile.mkdtemp(prefix='sonar-browser-state-'))
    created=False
    try:
        with admin.cursor() as cursor:cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created=True
        for key,field in (('SONAR_DB_USER','user'),('SONAR_DB_PASSWORD','password'),('SONAR_DB_HOST','host'),('SONAR_DB_PORT','port')):
            os.environ[key]=str(config.get(field,''))
        os.environ.update(SONAR_DB_NAME=name,SONAR_STATE_DIR=str(state),SONAR_BOT_TOKEN=TOKEN,
                          SONAR_WEB_ORIGIN=f'https://127.0.0.1:{port}',MPLCONFIGDIR=str(state/'matplotlib'),
                          XDG_CACHE_HOME=str(state/'cache'))
        import settings
        settings.SUPER_ADMIN_ID=901
        from database import Database
        from schema_migrations import run_startup_migrations
        from log_management import LogStore
        from core import sec
        db=Database();run_startup_migrations(db)
        db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'Synthetic administrator'),(902,'Synthetic ordinary user')")
        server=db.execute('''INSERT INTO servers(owner_id,name,ip,port,username,password,last_status,
            last_agent_status,last_cpu,last_ram,last_disk,last_checked_at)
            VALUES(901,'سرور آزمایشی گزارش','192.0.2.1',22,'fixture',%s,'Online','Online',25,50,20,NOW()) RETURNING id''',
            (sec.encrypt('synthetic-ssh-fixture-only'),),fetch='one')['id']
        db.execute('''INSERT INTO server_stats(server_id,cpu,ram,disk,traffic_gb,created_at)
            SELECT %s,25,50,20,1,NOW()-(n*INTERVAL '15 minutes') FROM generate_series(0,192) n''',(server,))
        cid=db.execute('''INSERT INTO tunnel_configs(owner_id,type,name,link,last_status,quality_score,last_ping,last_jitter)
            VALUES(901,'single','Synthetic proxy','vless://00000000-0000-4000-8000-000000000001@192.0.2.2:443?security=tls','OK',9,80,3) RETURNING id''',fetch='one')['id']
        db.execute("INSERT INTO pro_metrics(owner_id,config_id,region,ok,ping,jitter) VALUES(901,%s,'fixture',true,80,3)",(cid,))
        now=datetime.now(timezone.utc)
        LogStore(db,901).write([(now-timedelta(hours=age),'ERROR' if age==30 else 'INFO','node',
                                'fixture',f'fixture-age-{age}','recorded') for age in (1,30,100,200)])
        # Browser tests exercise real authentication, SQL and job state. Network
        # workers and privileged deletion are replaced with explicit test doubles.
        async def idle(*args):await args[-1].wait()
        def idle_sync(*args):
            while not args[-1].is_set():time.sleep(.05)
        import enterprise_runtime,web_reporting,web_agent_jobs,log_purge
        async def synthetic_observations(db,stop):
            import asyncio
            while not stop.is_set():
                await asyncio.to_thread(db.execute,'UPDATE servers SET last_checked_at=NOW() WHERE id=%s',(server,))
                await asyncio.to_thread(db.execute,'INSERT INTO server_stats(server_id,cpu,ram,disk,traffic_gb) VALUES(%s,25,50,20,1)',(server,))
                try:await asyncio.wait_for(stop.wait(),timeout=5)
                except asyncio.TimeoutError:pass
        web_reporting.observation_worker=synthetic_observations;web_agent_jobs.worker=idle
        for field in ('operations_worker','webhook_worker','recovery_worker','rollout_worker'):
            setattr(enterprise_runtime,field,idle)
        enterprise_runtime.scheduled_backup_worker=idle_sync
        log_purge.request_local=lambda:{'ok':False,'sources':{
            'application_files':{'status':'cleared'},'installer_file':{'status':'cleared'},
            'namespace_journal':{'status':'cleared'},'shared_journal':{'status':'blocked_shared'}}}
        log_purge.request_remote=lambda server:{'ok':False,'status':'unavailable','error':'SYNTHETIC_OFFLINE_NODE'}
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import ipaddress
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'local-browser-fixture')])
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
              .not_valid_after(now+timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
              .sign(key,hashes.SHA256()))
        (state/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (state/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        (state/'key.pem').chmod(0o600)
        import pro_portal,uvicorn
        uvicorn.run(pro_portal.app,host='127.0.0.1',port=port,ssl_keyfile=str(state/'key.pem'),
                    ssl_certfile=str(state/'cert.pem'),log_level='warning')
    finally:
        from database import Database
        db=Database()
        if db.pool:db.pool.closeall()
        if created:
            with admin.cursor() as cursor:cursor.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        admin.close();shutil.rmtree(state)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('An unprivileged local port is required')
    main(args.port)
