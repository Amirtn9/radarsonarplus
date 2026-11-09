import importlib.util
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path

import pytest

from log_management import DatabaseLogHandler
from log_spool import LogSpool

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('maintenance',ROOT/'scripts/sonar_log_maintenance.py')
maintenance=importlib.util.module_from_spec(spec);spec.loader.exec_module(maintenance)


def row(message='redacted fixture'):
    return (datetime.now(timezone.utc),'INFO','system','fixture',message,'recorded',str(uuid.uuid4()))


def eventually(predicate,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.02)
    raise AssertionError('Background logging condition not reached')


def test_disk_spool_survives_reopen_and_is_shared_and_private(tmp_path):
    path=tmp_path/'spool'/'pending.sqlite3'
    first=LogSpool(path);value=row();first.add([value]);first.add([value])
    other=LogSpool(path)
    assert other.read()==[value]
    assert other.count()==1
    assert path.stat().st_mode&0o077==0
    first.close();other.close()
    reopened=LogSpool(path)
    assert reopened.read()==[value]
    reopened.discard_before(datetime.now(timezone.utc))
    assert reopened.count()==0
    reopened.close()


def test_disk_spool_rejects_symlink_and_hardlink(tmp_path):
    target=tmp_path/'private';target.write_text('do not overwrite')
    linked=tmp_path/'linked.sqlite3';linked.symlink_to(target)
    with pytest.raises(ValueError):LogSpool(linked)
    linked.unlink();os.link(target,linked)
    with pytest.raises(ValueError):LogSpool(linked)
    assert target.read_text()=='do not overwrite'


def test_spool_initialization_retries_temporary_database_lock(tmp_path):
    import sqlite3
    path=tmp_path/'initializing.sqlite3'
    blocker=sqlite3.connect(path,check_same_thread=False)
    blocker.execute('CREATE TABLE blocker (id INTEGER)')
    blocker.commit()
    blocker.execute('BEGIN IMMEDIATE')
    release=threading.Timer(.4,blocker.rollback);release.start()
    try:
        spool=LogSpool(path)
        try:
            spool.add([row()])
            assert spool.count()==1
        finally:spool.close()
    finally:release.join();blocker.close()


def test_independent_processes_share_spool_and_survive_abrupt_exit(tmp_path):
    import sys
    state=tmp_path/'shared-state'
    code='''
import logging,os,time
from log_management import DatabaseLogHandler
class OfflineStore:
 def write(self,rows):raise RuntimeError('synthetic offline database')
 def cleanup(self):pass
handler=DatabaseLogHandler(OfflineStore())
for number in range(50):
 handler.emit(logging.LogRecord('fixture',logging.INFO,__file__ if '__file__' in globals() else 'fixture',1,'process-'+os.environ['FIXTURE_PROCESS']+'-'+str(number),(),None))
# Deliberately bypass all shutdown hooks; successful disk commits must survive.
os._exit(0)
'''
    children=[subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,
              env={**os.environ,'SONAR_STATE_DIR':str(state),'FIXTURE_PROCESS':str(number)},
              stdout=subprocess.PIPE,stderr=subprocess.PIPE) for number in range(2)]
    for child in children:
        out,err=child.communicate(timeout=15)
        assert child.returncode==0,(out+err).decode()
    spool=LogSpool(state/'log-spool'/'pending.sqlite3')
    try:
        rows=spool.read(limit=200)
        assert len(rows)==100
        assert len({value[6] for value in rows})==100
        assert {value[4] for value in rows}=={f'process-{process}-{number}' for process in range(2) for number in range(50)}
    finally:spool.close()


def test_database_outage_is_spooled_and_replayed_without_new_messages(tmp_path,monkeypatch):
    monkeypatch.setenv('SONAR_STATE_DIR',str(tmp_path))
    class Store:
        available=False
        saved=[]
        def write(self,rows):
            if not self.available:raise RuntimeError('synthetic outage')
            self.saved.extend(rows)
        def cleanup(self):pass
    store=Store();handler=DatabaseLogHandler(store)
    try:
        record=logging.LogRecord('fixture',logging.INFO,__file__,1,'password=never-store-this',(),None)
        handler.emit(record)
        eventually(lambda:handler.spool.count()==1)
        assert 'never-store-this' not in handler.spool.read()[0][4]
        assert handler.dropped==0
        store.available=True
        eventually(lambda:len(store.saved)==1)
        eventually(lambda:handler.spool.count()==0)
    finally:handler.close()


def test_overflow_uses_disk_instead_of_dropping(tmp_path,monkeypatch):
    monkeypatch.setenv('SONAR_STATE_DIR',str(tmp_path))
    entered=threading.Event();release=threading.Event()
    class Store:
        def write(self,rows):entered.set();release.wait(5)
        def cleanup(self):pass
    handler=DatabaseLogHandler(Store())
    record=logging.LogRecord('fixture',logging.INFO,__file__,1,'fixture',(),None)
    try:
        handler.emit(record);assert entered.wait(2)
        for _ in range(2001):handler.emit(record)
        assert handler.spool.count()==2002
        assert handler.dropped==0
    finally:release.set();handler.close()


def test_cleanup_failure_does_not_requeue_or_count_delivered_logs_as_lost(tmp_path,monkeypatch):
    monkeypatch.setenv('SONAR_STATE_DIR',str(tmp_path))
    class Store:
        saved=[]
        def write(self,rows):self.saved.extend(rows)
        def cleanup(self):raise RuntimeError('synthetic retention failure')
    store=Store();handler=DatabaseLogHandler(store)
    try:
        handler.emit(logging.LogRecord('fixture',logging.INFO,__file__,1,'fixture',(),None))
        eventually(lambda:len(store.saved)==1 and handler.maintenance_failures>0)
        assert handler.spool.count()==0 and handler.dropped==0 and handler.failures==0
    finally:handler.close()


def test_temporary_sqlite_lock_and_database_outage_retain_pending_batch(tmp_path,monkeypatch):
    monkeypatch.setenv('SONAR_STATE_DIR',str(tmp_path))
    class Store:
        available=False
        saved=[]
        def write(self,rows):
            if not self.available:raise RuntimeError('synthetic database outage')
            self.saved.extend(rows)
        def cleanup(self):pass
    store=Store();handler=DatabaseLogHandler(store)
    external=LogSpool(handler.spool.path)
    external.conn.execute('BEGIN IMMEDIATE')
    release=threading.Timer(1,external.conn.rollback);release.start()
    try:
        handler.emit(logging.LogRecord('fixture',logging.INFO,__file__,1,'locked spool fixture',(),None))
        eventually(lambda:handler.spool.count()==1,timeout=4)
        store.available=True
        eventually(lambda:len(store.saved)==1)
        assert handler.dropped==0
    finally:
        release.join();external.close();handler.close()


def test_file_purge_preserves_open_writer_and_all_non_log_files(tmp_path):
    log=tmp_path/'sonar.log';log.write_text('old log')
    rotated=tmp_path/'sonar.log.1.gz';rotated.write_bytes(b'synthetic compressed log')
    secret=tmp_path/'secret.key';secret.write_text('preserve key')
    config=tmp_path/'sonar_config.json';config.write_text('preserve configuration')
    with log.open('a') as writer:
        result=maintenance.purge_files(str(tmp_path),maintenance.APP_NAME)
        assert result=={'status':'cleared','files':2}
        assert log.read_text()=='' and not rotated.exists()
        writer.write('new log');writer.flush()
    assert log.read_text()=='new log'
    assert secret.read_text()=='preserve key' and config.read_text()=='preserve configuration'


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_file_purge_preflight_refuses_unsafe_inode_before_truncation(tmp_path,kind):
    safe=tmp_path/'sonar.json';safe.write_text('preserve until safe')
    target=tmp_path/'secret.key';target.write_text('private fixture')
    link=tmp_path/'sonar.log'
    if kind=='symlink':link.symlink_to(target)
    else:os.link(target,link)
    assert maintenance.purge_files(str(tmp_path),maintenance.APP_NAME)['status']=='failed'
    assert safe.read_text()=='preserve until safe' and target.read_text()=='private fixture'


def test_shared_journal_is_never_vacuumed(monkeypatch):
    commands=[]
    def run(command,**kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command,0,stdout='legacy service record' if '--namespace=sonar' not in command else '',stderr='')
    monkeypatch.setattr(maintenance.subprocess,'run',run)
    monkeypatch.setattr(maintenance.os.path,'isdir',lambda path:True)
    monkeypatch.setattr(maintenance.os,'listdir',lambda path:['fixture-machine.sonar'])
    result=maintenance.journals()
    assert result['shared_journal']['status']=='blocked_shared'
    assert result['namespace_journal']['status']=='cleared'
    assert all('--namespace=sonar' in cmd for cmd in commands if any('vacuum' in arg for arg in cmd))


def test_release_agent_protocol_and_current_documentation_match():
    import settings,monitor_agent
    version=(ROOT/'VERSION').read_text().strip()
    assert settings.PROJECT_VERSION==monitor_agent.AGENT_VERSION==version
    assert monitor_agent.AGENT_PROTOCOL==2
    assert f'نسخه فعلی `{version}`' in (ROOT/'README.md').read_text()
    assert f'# SONAR RADAR PRO {version}' in (ROOT/'README_FA.md').read_text()
    assert f'**Release:** `{version}`' in (ROOT/'SECURITY.md').read_text()


def test_agent_namespace_retention_script_creates_valid_ini(tmp_path):
    import ast
    import configparser
    tree=ast.parse((ROOT/'agent_deploy.py').read_text())
    command=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)
                 and 'printf' in n.value and 'SystemMaxUse' in n.value)
    line=next(line for line in command.splitlines() if line.startswith('printf '))
    target=tmp_path/'retention.conf'
    subprocess.run(['bash','-c',line.replace('/etc/systemd/journald@sonar.conf.d/retention.conf',str(target))],check=True)
    parsed=configparser.ConfigParser();parsed.read(target)
    assert parsed['Journal']['SystemMaxUse']=='128M'
    assert parsed['Journal']['MaxRetentionSec']=='30day'


def test_maintenance_socket_units_verify_and_preserve_hardening(tmp_path):
    import shutil
    if not shutil.which('systemd-analyze'):pytest.skip('systemd unit verifier not installed')
    source=(ROOT/'install.sh').read_text()
    socket_unit=source.split('cat > /etc/systemd/system/sonar-log-maintenance.socket <<EOF\n',1)[1].split('\nEOF',1)[0]
    service=source.split("cat > /etc/systemd/system/sonar-log-maintenance@.service <<'SONAR_MAINTENANCE'\n",1)[1].split('\nSONAR_MAINTENANCE',1)[0]
    assert 'SocketMode=0600' in socket_unit
    assert 'NoNewPrivileges=true' in service and 'ProtectSystem=strict' in service
    assert 'LogNamespace=sonar' in source
    units=tmp_path/'etc/systemd/system';units.mkdir(parents=True)
    (units/'sonar-log-maintenance.socket').write_text(socket_unit.replace('SocketUser=$APP_USER',f'SocketUser={os.getuid()}').replace('SocketGroup=$APP_USER',f'SocketGroup={os.getgid()}')+'\n')
    (units/'sonar-log-maintenance@.service').write_text(service+'\n')
    # Use the same unit-file parser in user verification mode so this test
    # needs neither a running PID 1 nor write permission on /run/systemd.
    runtime=tmp_path/'runtime';runtime.mkdir(mode=0o700)
    env={**os.environ,'XDG_RUNTIME_DIR':str(runtime)}
    result=subprocess.run(['systemd-analyze','--user','verify',str(units/'sonar-log-maintenance.socket'),
                           str(units/'sonar-log-maintenance@.service')],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
