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
        fresh_relays=[]
        def seed_fleet():
            if fresh_relays:return
            # Synthetic fleet evidence; no host is contacted by this fixture.
            created=[]
            with db.get_connection() as (conn,cursor):
                cursor.execute("INSERT INTO users(user_id,full_name) VALUES(1902,'Synthetic relay owner')")
                for index,(label,owner,age,ok) in enumerate((
                        ('Relay Alpha',901,0,True),('Relay Beta',901,0,True),
                        ('Relay Stale',901,600,True),('Relay Consent',1902,0,False))):
                    ip=f'192.0.2.{index+20}'
                    cursor.execute("""INSERT INTO agent_nodes(ip,ssh_port,ws_port,agent_token,agent_cert_pem)
                        VALUES(%s,22,8443,'synthetic-agent-token','synthetic-enrollment-only') RETURNING id""",(ip,))
                    agent=cursor.fetchone()['id']
                    cursor.execute("""INSERT INTO servers(owner_id,name,ip,agent_id,ws_port,port,location_type)
                        VALUES(%s,%s,%s,%s,8443,22,%s) RETURNING id""",(owner,label,ip,agent,'ir' if index%2==0 else 'ext'))
                    relay=cursor.fetchone()['id']
                    if index<2:created.append(relay)
                    cursor.execute("""INSERT INTO monitor_fleet_nodes(server_id,offered_by,approved_by,enabled,
                        relay_ready,last_probe_ok,last_probe_at,route_priority,cpu_pct,latency_ms)
                        VALUES(%s,901,%s,%s,%s,%s,NOW()-(%s*INTERVAL '1 second'),%s,%s,%s)""",
                        (relay,901 if index<3 else None,index<3,index<3,ok,age,10+index,0 if index==0 else 30,20))
                conn.commit()
            fresh_relays.extend(created)
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
        import enterprise_runtime,web_reporting,web_agent_jobs,log_purge,billing_delivery,service_jobs,config_probe_jobs
        async def idle_billing(db,stop,get_token):await stop.wait()
        billing_delivery.worker=idle_billing
        service_jobs.web_worker=idle_billing
        async def synthetic_observations(db,stop):
            import asyncio
            while not stop.is_set():
                await asyncio.to_thread(db.execute,'UPDATE monitor_fleet_nodes SET last_probe_at=NOW() WHERE server_id=ANY(%s)',(fresh_relays,))
                await asyncio.to_thread(db.execute,'UPDATE servers SET last_checked_at=NOW() WHERE id=%s',(server,))
                await asyncio.to_thread(db.execute,'INSERT INTO server_stats(server_id,cpu,ram,disk,traffic_gb) VALUES(%s,25,50,20,1)',(server,))
                try:await asyncio.wait_for(stop.wait(),timeout=5)
                except asyncio.TimeoutError:pass
        web_reporting.observation_worker=synthetic_observations;web_agent_jobs.worker=idle;config_probe_jobs.worker=idle
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
        from core import ServerMonitor
        ssh_outcomes=iter(({'status':'Online'},{'status':'Offline','error':'AUTH_FAILED: synthetic'},
                           {'status':'Offline','error':'HOST_KEY_UNKNOWN: synthetic'}))
        def synthetic_ssh(*args,**kwargs):
            return next(ssh_outcomes,{'status':'Offline','error':'SSH_TIMEOUT: synthetic'})
        ServerMonitor.check_ssh_connection=staticmethod(synthetic_ssh)
        import pro_portal,uvicorn
        from fastapi import Depends,HTTPException
        @pro_portal.app.post('/__fixture/fleet')
        def seed_fleet_endpoint(uid:int=Depends(pro_portal.actor)):
            if uid!=901:raise HTTPException(status_code=403,detail='Synthetic administrator required')
            seed_fleet()
            return {'ok':True}
        incident_seeded=[]
        @pro_portal.app.post('/__fixture/incidents')
        def seed_incidents(uid:int=Depends(pro_portal.actor)):
            if uid!=901:raise HTTPException(status_code=403,detail='Synthetic administrator required')
            if not incident_seeded:
                from pro_store import ProStore
                pro=ProStore(db)
                for index in range(26):
                    row=pro.incident(901,'server',server,'fixture_'+str(index),f'Synthetic incident {index:02}',
                                     'critical' if index%2==0 else 'warning')
                    incident_seeded.append(row['id'])
            return {'server_id':server,'incident_ids':incident_seeded}
        catalog_seed={}
        @pro_portal.app.post('/__fixture/catalog')
        def seed_catalog(uid:int=Depends(pro_portal.actor)):
            if uid!=901:raise HTTPException(status_code=403,detail='Synthetic administrator required')
            if catalog_seed:return catalog_seed
            configs={}
            cases=[(f'healthy {n:02}','single',f'vless://synthetic-private-{n}@192.0.2.2:443','OK',20+n,9) for n in range(14)]
            cases += [('failed','sub_item','vmess://synthetic-private','Fail',0,0),
                      ('untested','single','trojan://synthetic-private','Unknown',0,10),
                      ('environment','single','ss://synthetic-private','OK',10,8),
                      ('profile','json','{"outbounds":[]}','Unknown',0,10),
                      ('source','sub_source','https://example.com/synthetic-private','OK',0,10),
                      ('unsupported','single','tuic://synthetic-private','Unknown',0,10)]
            for label,kind,link,status,ping,score in cases:
                ident=db.execute("""INSERT INTO tunnel_configs(owner_id,name,type,link,last_status,last_ping,quality_score)
                    VALUES(901,%s,%s,%s,%s,%s,%s) RETURNING id""",('Catalog Needle '+label,kind,link,status,ping,score),fetch='one')['id']
                configs[label]=ident
                if status in ('OK','Fail') and kind!='sub_source':db.execute("INSERT INTO pro_metrics(owner_id,config_id,region,ok,ping,measured_at) VALUES(901,%s,'fixture',%s,%s,NOW()-INTERVAL '5 minutes')",(ident,status=='OK',ping))
            db.execute("INSERT INTO sonar_config_probes(owner_id,actor_id,config_id,request_key,state,error_code,completed_at) VALUES(901,901,%s,'catalog-fixture','failed','MONITOR_NODE_UNAVAILABLE',NOW())",(configs['environment'],))
            for ident in (configs['healthy 00'],configs['failed']):db.execute("INSERT INTO pro_tags(owner_id,resource_type,resource_id,tag) VALUES(901,'config',%s,'blue')",(ident,))
            db.execute("INSERT INTO pro_favorites(owner_id,resource_type,resource_id) VALUES(901,'config',%s)",(configs['healthy 00'],))
            agent=db.execute("INSERT INTO agent_nodes(ip,ssh_port,ws_port,agent_token) VALUES('192.0.2.55',22,8443,'synthetic-private-token') RETURNING id",fetch='one')['id']
            sid=db.execute("INSERT INTO servers(owner_id,name,ip,port,agent_id) VALUES(901,'Catalog Needle server','192.0.2.55',22,%s) RETURNING id",(agent,),fetch='one')['id']
            service=db.execute("INSERT INTO pro_services(owner_id,name,kind,target) VALUES(901,'Catalog Needle service','http','https://example.com/synthetic-private') RETURNING id",fetch='one')['id']
            from pro_store import ProStore
            incident=ProStore(db).incident(901,'server',sid,'catalog_fixture','Catalog Needle incident')['id']
            db.execute("INSERT INTO users(user_id,full_name) VALUES(1905,'Tenant external')")
            db.execute("INSERT INTO tunnel_configs(owner_id,name,type,link) VALUES(1905,'Catalog Needle foreign','single','synthetic-private-other')")
            catalog_seed.update(configs=configs,server_id=sid,service_id=service,incident_id=incident)
            return catalog_seed
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
