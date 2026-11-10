import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import subprocess
import zipfile

import pytest
import diagnostics


@pytest.mark.parametrize('hours',[3,6,12,24,48,72,168])
def test_hour_window_applies_to_every_bundle_source(tmp_path,monkeypatch,hours):
    calls=[]
    now=datetime.now(timezone.utc)
    recent=(now-timedelta(hours=1)).isoformat()+' recent'
    old=(now-timedelta(hours=hours+1)).isoformat()+' old'
    path=tmp_path/'sonar.log';path.write_text(old+'\n'+recent+'\nTraceback fixture\n')
    monkeypatch.setattr(diagnostics,'snapshot',lambda:{})
    monkeypatch.setattr(diagnostics,'_application_log_paths',lambda:[path])
    def source(name,days):calls.append((name,days));return 'source fixture'
    monkeypatch.setattr(diagnostics,'log_source',source)
    result=diagnostics.build_bundle(tmp_path/'out.zip',days=hours/24,
                                   remote_logs={'1':recent},agent_rows=[])
    with zipfile.ZipFile(result) as z:
        assert json.loads(z.read('health.json'))['log_window_hours']==hours
        assert f'agents/server-1-{hours}h.log' in z.namelist()
        text=z.read('logs/application/sonar.log').decode()
        assert 'recent' in text and 'Traceback fixture' in text and ' old' not in text
    assert len(calls)==5 and all(days==hours/24 for _,days in calls)
    assert diagnostics.window_since(hours/24)==f'{hours} hours ago'


def test_helper_rejects_shell_injection_and_invalid_hours():
    for value in ['3h;id','0h','999h','-1','3.5h']:
        result=subprocess.run(['bash','scripts/sonar-diagnostic-read.sh','journal','bot',value],capture_output=True,text=True)
        assert result.returncode==64 and 'INVALID_LOG_WINDOW' in result.stderr


def test_privileged_diagnostic_uses_existing_read_access_first(monkeypatch):
    seen=[]
    def run(args,**kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args,0,stdout='readable journal',stderr='')
    monkeypatch.setattr(diagnostics.os,'geteuid',lambda:1000)
    monkeypatch.setattr(diagnostics.subprocess,'run',run)
    assert 'readable journal' in diagnostics._command(['journalctl'],privileged=True)
    assert seen==[['journalctl']]


@pytest.mark.asyncio
async def test_service_workers_cancel_before_http_shutdown():
    from runtime import stop_service_workers
    stop=asyncio.Event();finished=asyncio.Event()
    async def worker():
        try:await asyncio.sleep(3600)
        finally:finished.set()
    task=asyncio.create_task(worker());await asyncio.sleep(0)
    app=SimpleNamespace(bot_data={'enterprise_stop':stop,'enterprise_tasks':[task]})
    await stop_service_workers(app)
    assert stop.is_set() and finished.is_set() and task.cancelled()


def test_health_manifest_remains_valid_json_with_status_booleans(tmp_path,monkeypatch):
    monkeypatch.setattr(diagnostics,'snapshot',lambda:{'token_configured':True,'admin_configured':True,'password':'fixture-secret'})
    result=diagnostics.build_bundle(tmp_path/'health.zip',include_logs=False,days=.125)
    with zipfile.ZipFile(result) as z:
        value=json.loads(z.read('health.json'))
    assert value['token_configured'] is True
    assert value['password']=='[REDACTED]'
    assert value['log_window_hours']==3


@pytest.mark.asyncio
async def test_telegram_picker_defers_collection_until_period_selected():
    import ast
    from pathlib import Path
    tree=ast.parse(Path('v6_ui.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='diag_zip_entry')
    shown=[];started=[]
    async def answer(*args,**kwargs):pass
    async def show(update,text,markup):shown.append(markup)
    async def job(*args):pass
    def spawn(context,coro,**kwargs):started.append(kwargs);coro.close()
    namespace={'is_admin':lambda u:True,'_safe_answer':answer,'show':show,
               'rows':lambda *items:items,'spawn_background':spawn,'diag_zip':job}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'v6_ui.py','exec'),namespace)
    update=SimpleNamespace(callback_query=SimpleNamespace(data='v6_diag_zip'))
    await namespace['diag_zip_entry'](update,SimpleNamespace())
    assert not started
    assert [item[1] for item in shown[0][:-1]]==[f'v6_diag_zip_h{h}' for h in [3,6,12,24,48,72,168]]
    for h in [3,6,12,24,48,72,168]:
        update.callback_query.data=f'v6_diag_zip_h{h}'
        await namespace['diag_zip_entry'](update,SimpleNamespace())
    assert len(started)==7


@pytest.mark.asyncio
async def test_relay_cancellation_awaits_pending_receive(monkeypatch):
    import monitor_agent
    import ssh_relay
    received=asyncio.Event()
    class WS:
        async def send(self,data):pass
        async def recv(self):received.set();await asyncio.Event().wait()
    class Reader:
        async def read(self,size):await asyncio.Event().wait()
    class Writer:
        closed=False
        def close(self):self.closed=True
        async def wait_closed(self):pass
    writer=Writer()
    async def connect(*args,**kwargs):return Reader(),writer
    monkeypatch.setattr(ssh_relay,'valid_relay_destination',lambda *a:('192.0.2.1',22))
    monkeypatch.setattr(monitor_agent,'_relay_semaphore',lambda:asyncio.Semaphore(1))
    monkeypatch.setattr(monitor_agent.asyncio,'open_connection',connect)
    task=asyncio.create_task(monitor_agent._serve_ssh_relay(WS(),{}))
    await asyncio.wait_for(received.wait(),2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert writer.closed
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith('sonar-ssh-relay-')]
