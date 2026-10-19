"""Behavioral regression tests for the audited 5.3 fixes; no production services."""
import ast
import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import types
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def isolated(file, name, scope, cls=None):
    tree = ast.parse((ROOT / file).read_text())
    body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body if cls else tree.body
    node = next(n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    node.returns = None
    for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
        arg.annotation = None
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, 'exec'), scope)
    return scope[name]


async def direct(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def test_one_action_grammar_rejects_inverted_and_unknown_actions():
    from access_policy import parse_server_action
    assert parse_server_action('act_reboot_5') == ('reboot', 5, None)
    assert parse_server_action('act_installagent_5') == ('installagent', 5, None)
    for value in ('act_5_reboot', 'act_reboot_0', 'act_unknown_5', 'act_reboot_5_extra'):
        with pytest.raises(ValueError):
            parse_server_action(value)


def test_all_paid_plans_share_one_capability_policy():
    from access_policy import has_premium
    for plan in (1, 2, 3):
        assert has_premium({'plan_type': plan}, 4, 777)
    assert not has_premium({'plan_type': 0}, 4, 777)
    assert has_premium(None, 777, 777)


def test_operation_boundary_rechecks_owner_before_remote_command():
    from access_policy import parse_server_action
    answers = AsyncMock()
    remote = AsyncMock()
    scope = {'asyncio': asyncio, 'parse_server_action': parse_server_action,
             'db': types.SimpleNamespace(get_server_for_user=lambda *a: None), 'run_sync': direct,
             'SUPER_ADMIN_ID': 777, 'ServerMonitor': types.SimpleNamespace(run_remote_command=remote)}
    fn = isolated('bot_logic_mod/servers.py', 'server_actions', scope)
    update = types.SimpleNamespace(callback_query=types.SimpleNamespace(data='act_reboot_5', answer=answers),
                                   effective_user=types.SimpleNamespace(id=15))
    asyncio.run(fn(update, types.SimpleNamespace()))
    assert answers.await_count == 1
    assert remote.await_count == 0


@pytest.fixture
def local_ssh(monkeypatch, tmp_path):
    import paramiko
    import ssh_support
    monkeypatch.setattr(ssh_support, 'KNOWN_HOSTS_FILE', str(tmp_path / 'trust' / 'known_hosts'))
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(.1)
    stop = threading.Event()
    transports = []
    workers = []
    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ('audit', 'local-only') else paramiko.AUTH_FAILED
        def get_allowed_auths(self, username):
            return 'password'
    def client(sock):
        transport = paramiko.Transport(sock)
        transports.append(transport)
        transport.add_server_key(key)
        try:
            transport.start_server(server=Server())
            stop.wait(.1)
            while transport.is_active() and not stop.wait(.05):
                pass
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()
    def accept():
        while not stop.is_set():
            try:
                sock, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            task = threading.Thread(target=client, args=(sock,), daemon=True)
            workers.append(task)
            task.start()
    worker = threading.Thread(target=accept, daemon=True)
    worker.start()
    yield ssh_support, listener.getsockname()[1], key
    stop.set()
    listener.close()
    for transport in transports:
        transport.close()
    worker.join(2)
    for task in workers:
        task.join(2)


def test_real_ssh_unknown_key_verify_authenticate_then_connect(local_ssh):
    import paramiko
    support, port, key = local_ssh
    with pytest.raises(paramiko.SSHException) as error:
        support.connect_ssh('127.0.0.1', port, 'audit', 'local-only')
    assert support.describe_ssh_error(error.value).startswith('HOST_KEY_UNKNOWN')
    candidate = support.scan_host_key('127.0.0.1', port)
    assert candidate['fingerprint'] == support.fingerprint(key)
    with pytest.raises(ValueError):
        support.trust_host_key(candidate, 'SHA256:wrong')
    assert not Path(support.KNOWN_HOSTS_FILE).exists()
    support.authenticate_candidate(candidate, candidate['fingerprint'], 'audit', 'local-only')
    support.trust_host_key(candidate, candidate['fingerprint'])
    connected = support.connect_ssh('127.0.0.1', port, 'audit', 'local-only')
    assert connected.get_transport().is_authenticated()
    connected.close()


def test_wrong_ssh_password_does_not_persist_candidate(local_ssh):
    import paramiko
    support, port, _ = local_ssh
    candidate = support.scan_host_key('127.0.0.1', port)
    with pytest.raises(paramiko.AuthenticationException):
        support.authenticate_candidate(candidate, candidate['fingerprint'], 'audit', 'wrong')
    assert not Path(support.KNOWN_HOSTS_FILE).exists()


def test_ssh_port_443_and_agent_port_validation_are_distinct():
    from ssh_support import ssh_port
    from access_policy import valid_agent_port
    assert ssh_port(443) == 443
    assert valid_agent_port(8080) == 8080
    with pytest.raises(ValueError):
        valid_agent_port(443)


def test_pro_wrapper_forwards_query_keywords():
    seen = []
    async def invoke(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    scope = {'owned_context': lambda *a: 15, 'invoke': invoke,
             'store': types.SimpleNamespace(require=lambda *a: True)}
    fn = isolated('pro_handlers.py', 'safe_call', scope)
    update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=15))
    def query(**kwargs):
        seen.append(kwargs)
        return ['matched']
    assert asyncio.run(fn(update, object(), query, page=2, per_page=15, term='name')) == ['matched']
    assert seen == [{'page': 2, 'per_page': 15, 'term': 'name'}]


def test_web_app_initialization_and_page_switch_use_actual_input():
    if not shutil.which('node'):
        pytest.skip('Node not installed')
    script = r'''
const fs=require('fs'),vm=require('vm');
const elements=new Map(),calls=[];
function element(id){return {id,textContent:'',value:'',classList:{add(){},remove(){},toggle(){}},
 addEventListener(){},replaceChildren(){},append(){},querySelector(){throw Error('An input has no child input')}}}
const document={getElementById(id){if(!elements.has(id))elements.set(id,element(id));return elements.get(id)},
 querySelectorAll(){return []},createElement(tag){return element(tag)}};
const context={document,window:{Telegram:{WebApp:{initData:'test',ready(){},expand(){}}},setTimeout},setTimeout,clearTimeout,
 fetch:async(path)=>{calls.push(path);return {ok:true,json:async()=>path==='/api/summary'?{servers:{active:0,total:0},configs:{healthy:0,total:0},incidents:0}:[]}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
setTimeout(()=>{context.choose('configs');setTimeout(()=>{
if(!calls.some(p=>p.includes('/api/resources/config')))process.exit(2);
console.log('Frontend initialized and switched page');},10)},10);
'''
    result = subprocess.run(['node', '-e', script, str(ROOT / 'pro_web/static/app.js')], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_cancelled_ws_response_never_reaches_next_request():
    import ws_client
    async def check():
        connections = []
        class WS:
            state = types.SimpleNamespace(name='OPEN')
            def __init__(self):
                self.queue = asyncio.Queue()
            async def send(self, raw):
                value = json.loads(raw)
                async def answer():
                    await asyncio.sleep(.04)
                    await self.queue.put(json.dumps({'reply': value['request']}))
                asyncio.create_task(answer())
            async def recv(self):
                return await self.queue.get()
            async def close(self):
                self.state = types.SimpleNamespace(name='CLOSED')
        pool = ws_client.WebSocketPool(max_per_key=1, acquire_timeout=.2)
        async def connect(key):
            socket = WS()
            connections.append(socket)
            return ws_client._Conn(socket, key, 0, 0, asyncio.Lock())
        pool._connect = connect
        first = asyncio.create_task(pool.request('test', 8080, 'dummy', {'request': 'old'}, retries=0))
        await asyncio.sleep(.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = await pool.request('test', 8080, 'dummy', {'request': 'new'}, retries=0)
        assert second == {'reply': 'new'}
        assert len(connections) == 2
        await pool.close_all()
    asyncio.run(check())


def test_cancelled_ws_connect_releases_reserved_capacity():
    import ws_client
    async def check():
        pool = ws_client.WebSocketPool(max_per_key=1)
        async def wait(key):
            await asyncio.Future()
        pool._connect = wait
        task = asyncio.create_task(pool.request('test', 8080, 'dummy', {}, retries=0))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert list(pool._counts.values()) == [0]
        await pool.close_all()
    asyncio.run(check())


def test_cancelled_ws_connect_wakes_an_existing_waiter():
    import ws_client
    async def check():
        pool = ws_client.WebSocketPool(max_per_key=1, acquire_timeout=2)
        attempts = []
        class WS:
            state = types.SimpleNamespace(name='OPEN')
            async def send(self, raw): pass
            async def recv(self): return '{"reply":"new"}'
            async def close(self): self.state = types.SimpleNamespace(name='CLOSED')
        async def connect(key):
            attempts.append(1)
            if len(attempts) == 1:
                await asyncio.Future()
            return ws_client._Conn(WS(), key, 0, 0, asyncio.Lock())
        pool._connect = connect
        first = asyncio.create_task(pool.request('test', 8080, 'dummy', {}, retries=0))
        await asyncio.sleep(.01)
        second = asyncio.create_task(pool.request('test', 8080, 'dummy', {}, retries=0))
        await asyncio.sleep(.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        assert await asyncio.wait_for(second, timeout=.5) == {'reply': 'new'}
        await pool.close_all()
    asyncio.run(check())


@pytest.mark.parametrize('confirmation,backup_ok', [('CANCEL', True), ('DELETE SONAR DATA', False)])
def test_clean_reset_aborts_even_in_conditional_function_context(tmp_path, confirmation, backup_ok):
    source = (ROOT / 'install.sh').read_text()
    definitions = 'reset_project_components() {' + source.split('reset_project_components() {', 1)[1].split('valid_port() {', 1)[0]
    # Model a valid interactive terminal while retaining the actual confirmation/backup/deletion code.
    definitions = definitions.replace('[[ -t 0 ]]', 'true')
    die = next(line for line in source.splitlines() if line.startswith('die()'))
    header = '''set -Eeuo pipefail
C_RED=;C_RESET=
say(){ :; };warn(){ :; };ok(){ :; }
require_root(){ :; };require_service_host(){ :; };ensure_directories(){ :; }
existing_components(){ return 1; }
app_database_exists(){ return 1; };app_role_exists(){ return 1; }
systemctl(){ :; };runuser(){ :; }
rm(){ echo DELETION_REACHED; }
APP_DIR=/opt/radar-sonar
STATE_DIR=/var/lib/sonar-radar
ENV_FILE=/nonexistent/test.env
BOT_UNIT=/nonexistent/bot.service
WEB_UNIT=/nonexistent/web.service
NGINX_SITE=/nonexistent/nginx.conf
NGINX_LINK=/nonexistent/nginx-link.conf
SERVICE=test;WEB_SERVICE=test
DB_NAME=test;DB_USER=test
'''
    header += f'archive_existing_components(){{ return {0 if backup_ok else 1}; }}\n'
    env = os.environ.copy()
    env['INSTALL_LOG'] = str(tmp_path / 'log')
    script = header + die + '\n' + definitions + '\nchosen=0\nselect_install_strategy || chosen=$?\necho RESULT=$chosen\n'
    result = subprocess.run(['bash', '-c', script], input='2\n' + confirmation + '\n', text=True, capture_output=True, env=env, timeout=5)
    assert 'DELETION_REACHED' not in result.stdout, result.stdout
    assert result.returncode != 0 or 'RESULT=1' in result.stdout


def test_source_permission_normalization_preserves_secrets(tmp_path):
    source = (ROOT / 'install.sh').read_text()
    fn = 'normalize_source_permissions() {' + source.split('normalize_source_permissions() {', 1)[1].split('prepare_runtime_state() {', 1)[0]
    (tmp_path / 'bot_logic_mod').mkdir(mode=0o700)
    (tmp_path / 'bot_logic_mod/code.py').write_text('print(1)')
    (tmp_path / 'bot_logic_mod/code.py').chmod(0o600)
    for name in ('secret.key', 'sonar_config.json'):
        (tmp_path / name).write_text('test-only')
        (tmp_path / name).chmod(0o600)
    result = subprocess.run(['bash', '-c', fn + '\nnormalize_source_permissions "$1"', 'bash', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'bot_logic_mod/code.py').stat().st_mode & 0o004
    assert (tmp_path / 'bot_logic_mod').stat().st_mode & 0o001
    assert (tmp_path / 'secret.key').stat().st_mode & 0o777 == 0o600
    assert (tmp_path / 'sonar_config.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('failure', ['offline', 'inventory', 'dump'])
def test_backup_never_reports_success_when_database_step_fails(tmp_path, failure):
    import shlex
    source = (ROOT / 'install.sh').read_text()
    function = 'make_backup() {' + source.split('make_backup() {', 1)[1].split('normalize_source_permissions() {', 1)[0]
    app, state, backups = (tmp_path / name for name in ('app', 'state', 'backups'))
    for path in (app, state, backups):
        path.mkdir()
    (app / 'sonar_config.json').write_text('{}')
    (state / 'secret.key').write_text('local-only')
    header = 'set -Eeuo pipefail\nensure_directories(){ :; }; require_install(){ :; }\n'
    header += 'die(){ echo "FAILURE: $*" >&2; exit 1; }\n'
    for key, value in {'APP_DIR': app, 'STATE_DIR': state, 'BACKUP_DIR': backups,
                       'ENV_FILE': tmp_path / 'no-env', 'DB_NAME': 'local_test', 'FAIL_AT': failure}.items():
        header += key + '=' + shlex.quote(str(value)) + '\n'
    header += r'''
runuser(){
 if [[ "$*" == *pg_dump* ]]; then [[ "$FAIL_AT" != dump ]] && echo dump-data; return; fi
 if [[ "$FAIL_AT" == offline ]]; then return 1; fi
 if [[ "$*" == *pg_database* && "$FAIL_AT" == inventory ]]; then return 1; fi
 echo 1
}
pg_restore(){ return 0; }
'''
    result = subprocess.run(['bash', '-c', header + function + '\nmake_backup || exit $?\necho BACKUP_SUCCESS\n'],
                            text=True, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert 'BACKUP_SUCCESS' not in result.stdout
    assert 'FAILURE:' in result.stderr


def test_reset_archive_rejects_failed_database_inventory(tmp_path):
    source = (ROOT / 'install.sh').read_text()
    function = 'archive_existing_components() {' + source.split('archive_existing_components() {', 1)[1].split('reset_project_components() {', 1)[0]
    script = r'''
set -Eeuo pipefail
BACKUP_DIR="$1"
APP_DIR="$1/no-app";STATE_DIR="$1/no-state"
ENV_FILE="$1/no-env";BOT_UNIT="$1/no-bot";WEB_UNIT="$1/no-web";NGINX_SITE="$1/no-nginx"
DB_NAME=local_test
die(){ echo "$*" >&2; exit 1; }
ok(){ echo ARCHIVE_SUCCESS; }
psql(){ :; }
id(){ return 0; }
runuser(){ return 1; }
'''
    result = subprocess.run(['bash', '-c', script + function + '\narchive_existing_components || exit $?\n',
                             'bash', str(tmp_path)], text=True, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert 'inventory failed' in result.stderr
    assert 'ARCHIVE_SUCCESS' not in result.stdout


def test_notifications_route_to_specific_topic_and_fallback_to_owner():
    from notifications import send_notification
    calls = []
    async def send(**kwargs):
        calls.append(kwargs)
    db = types.SimpleNamespace(get_user_channels=lambda owner: [
        {'chat_id': -1001, 'topic_id': 11, 'usage_type': 'down'},
        {'chat_id': -1002, 'topic_id': 12, 'usage_type': 'all'}])
    context = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=send))
    assert asyncio.run(send_notification(context, 15, 'test', 'down', db=db))
    assert calls == [{'chat_id': -1001, 'message_thread_id': 11, 'text': 'test', 'parse_mode': None}]
    async def fail_channel(**kwargs):
        calls.append(kwargs)
        if kwargs['chat_id'] < 0:
            raise OSError('local test')
    calls.clear()
    context.bot.send_message = fail_channel
    assert asyncio.run(send_notification(context, 15, 'test', 'down', db=db))
    assert [call['chat_id'] for call in calls] == [-1001, 15]


def test_error_summary_excludes_update_contents_and_exception_text():
    secret = 'PRIVATE-PASSWORD-SENTINEL'
    class Update:
        effective_user = types.SimpleNamespace(id=15)
        def __str__(self):
            return secret
    try:
        raise RuntimeError(secret)
    except RuntimeError as exc:
        error = exc
    send = AsyncMock()
    scope = {'traceback': __import__('traceback'), 'os': os, 'SUPER_ADMIN_ID': 777, 'logger': logging.getLogger('test')}
    fn = isolated('bot_logic_mod/ui.py', 'error_handler', scope)
    context = types.SimpleNamespace(error=error, bot=types.SimpleNamespace(send_message=send))
    asyncio.run(fn(Update(), context))
    assert secret not in str(send.call_args)
    assert 'RuntimeError' in str(send.call_args)


def test_datacenter_response_is_normalized(monkeypatch):
    import requests
    response = types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'status': 'success', 'country': 'Iran', 'countryCode': 'IR', 'isp': 'test', 'query': '8.8.8.8'})
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: response)
    fn = isolated('server_stats.py', 'get_datacenter_info', {'requests': requests}, cls='StatsManager')
    ok, data = fn('8.8.8.8')
    assert ok
    assert (data['country_name'], data['country_code2'], data['ip_number'], data['ip']) == ('Iran', 'IR', '8.8.8.8', '8.8.8.8')


def test_subscription_validation_handles_bad_ports_without_crashing():
    from access_policy import valid_subscription_url
    assert valid_subscription_url('https://example.com:443/sub') == 'https://example.com:443/sub'
    for url in ('http://example.com/sub', 'https://example.com:nope/sub', 'https://user:pass@example.com',
                'https://example.com:444/sub', 'https://[broken', 'https:///sub'):
        with pytest.raises(ValueError):
            valid_subscription_url(url)


def test_monitoring_options_match_agent_limits():
    from access_policy import monitoring_setting
    assert monitoring_setting('small', '0') == 0
    assert monitoring_setting('big', '10') == 10
    assert monitoring_setting('int', '60') == 60
    for kind, value in (('big', '20'), ('small', '-1'), ('big', 'nan'), ('big', 'inf'), ('int', '9')):
        with pytest.raises(ValueError):
            monitoring_setting(kind, value)


@pytest.mark.parametrize('healthy', [False, True])
def test_deployment_commits_actual_port_only_after_wss_and_runtime_health(monkeypatch, tmp_path, healthy):
    import agent_deploy
    import ws_client
    import ssh_support
    calls, commits, firewall = [], [], []
    # Deployment uploads the agent via SFTP over a verified SSH connection.
    # Simulate the remote file system instead of contacting localhost:22.
    class RemoteWriter:
        def __enter__(self):return self
        def __exit__(self, *_):return False
        def write(self, payload):assert isinstance(payload, bytes)
        def flush(self):pass
    class FakeSFTP:
        def normalize(self, path):return '/tmp'
        def mkdir(self, path, mode=0o700):pass
        def open(self, name, mode):return RemoteWriter()
        def remove(self, name):pass
        def rmdir(self, path):pass
        def close(self):pass
    class FakeSSH:
        def open_sftp(self):return FakeSFTP()
        def close(self):pass
    monkeypatch.setattr(ssh_support,'connect_ssh',lambda *args:FakeSSH())
    monkeypatch.setattr(agent_deploy, 'privileged_script', lambda command: command)
    monkeypatch.setattr(agent_deploy.asyncio, 'sleep', AsyncMock())
    async def request(self, ip, port, token, payload, **kwargs):
        assert port == 9090 and token == 'local-only-token'
        assert kwargs['tls_certificate'] == 'test-certificate'
        return {'status': 'Online', 'capabilities': {'xray_ready': healthy}}
    monkeypatch.setattr(ws_client.WebSocketPool, 'request', request)
    def run(ip, port, user, password, command, timeout):
        calls.append(command)
        if command == 'cat /etc/sonar-agent/tls.crt':
            return True, 'test-certificate'
        return True, ''
    monitor = types.SimpleNamespace(_run_ssh_command=run, get_bot_public_ip=lambda: '8.8.8.8',
        whitelist_bot_ip=lambda *args: (firewall.append(args) or (True, 'rule accepted')))
    db = types.SimpleNamespace(get_or_create_agent_token=lambda *args: 'local-only-token',
        store_agent_certificate=lambda *args: commits.append(args))
    ok, detail = agent_deploy.deploy_agent(monitor, db, '127.0.0.1', 22, 'audit', 'local-only', 9090)
    assert ok == healthy, detail
    assert commits == ([('127.0.0.1', 22, 'test-certificate', 9090)] if healthy else [])
    assert firewall[0][-1] == 9090
    unit = next(command for command in calls if 'Description=Sonar Monitor Agent' in command)
    assert 'monitor_agent.py 9090' in unit and "sport = :9090" in unit
    # Execute the exact generated environment merge in a disposable directory.
    env_command = next(command for command in calls if "<<'SONAR_ENV_PY'" in command)
    env_source = env_command.split('\n', 1)[1].rsplit('\nSONAR_ENV_PY', 1)[0]
    env_source = env_source.replace('/etc/sonar-agent.env', str(tmp_path / 'agent.env'))
    env_source = env_source.replace("dir='/etc'", 'dir=' + repr(str(tmp_path)))
    env_file = tmp_path / 'agent.env'
    env_file.write_text('SONAR_XRAY_URL=https://github.com/pinned.zip\nSONAR_XRAY_SHA256=custom-pin\nSONAR_AGENT_TOKEN=old\n')
    exec(compile(env_source, 'generated-agent-env', 'exec'), {})
    parsed = dict(line.split('=', 1) for line in env_file.read_text().splitlines())
    assert parsed['SONAR_XRAY_SHA256'] == 'custom-pin'
    assert parsed['SONAR_AGENT_TOKEN'] == 'local-only-token'
    assert env_file.stat().st_mode & 0o777 == 0o600
    syntax = subprocess.run(['bash', '-n'], input=unit, text=True, capture_output=True)
    assert syntax.returncode == 0, syntax.stderr


def test_real_telegram_handler_registration_and_conversation_states(tmp_path):
    import sys
    code = r'''
import os, sys, types
sys.path.insert(0,sys.argv[1]);os.environ['SONAR_STATE_DIR']=sys.argv[2]
fake=types.ModuleType('database');fake.Database=type('DB',(),{})
sys.modules['database']=fake
from telegram.ext import Application, ConversationHandler
from telegram.request import HTTPXRequest
import dispatcher
app=(Application.builder().token('123456:local-only-test')
     .request(HTTPXRequest(httpx_kwargs={'trust_env':False}))
     .get_updates_request(HTTPXRequest(httpx_kwargs={'trust_env':False})).build())
dispatcher.register_all_handlers(app)
conversations=[handler for group in app.handlers.values() for handler in group if isinstance(handler,ConversationHandler)]
assert any(108 in handler.states and 106 in handler.states for handler in conversations)
assert any(handler.callback.__name__=='admin_agent_install_run' for conv in conversations for handler in conv.entry_points)
assert sum(len(handlers) for handlers in app.handlers.values())>60
print('Telegram handler registration passed without network access')
'''
    result = subprocess.run([sys.executable, '-c', code, str(ROOT), str(tmp_path / 'state')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_server_wizard_preserves_custom_port_for_background_install():
    from access_policy import valid_agent_port
    saved = {'id': 21, 'owner_id': 15, 'ws_port': 9090}
    jobs = []
    async def installer(context, uid, server, message):
        jobs.append((uid, server))
    pending = []
    scope = {'run_sync': direct, 'valid_agent_port': valid_agent_port, 'AGENT_PORT': 8080,
        'SUPER_ADMIN_ID': 777, 'ConversationHandler': types.SimpleNamespace(END=-1),
        'sec': types.SimpleNamespace(decrypt=lambda value: value),
        'ServerMonitor': types.SimpleNamespace(check_ssh_connection=lambda *a: {'status': 'Online'}),
        'db': types.SimpleNamespace(add_server=lambda *a: 21, get_server_for_user=lambda *a: saved),
        '_install_saved_server': installer,
        'keyboard': types.SimpleNamespace(back_btn=lambda *args,**kwargs: None),
        'spawn_background': lambda context, job, **kw: pending.append(job)}
    fn = isolated('bot_logic_mod/servers.py', '_complete_server_add', scope)
    message = types.SimpleNamespace(edit_text=AsyncMock())
    update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=15),
        effective_message=types.SimpleNamespace(reply_text=AsyncMock(return_value=message)))
    context = types.SimpleNamespace(user_data={'srv': {'name': 'test', 'ip': '127.0.0.1', 'port': 22,
        'username': 'audit', 'password': 'local-only', 'ws_port': 9090}, 'srv_group_id': 0})
    async def check():
        assert await fn(update, context) == -1
        assert 'srv' not in context.user_data
        assert len(pending) == 1
        await pending[0]
    asyncio.run(check())
    assert jobs[0][1]['ws_port'] == 9090


def test_monitor_job_persists_down_streak_and_recovery(monkeypatch):
    observations, incidents = [], []
    notify = AsyncMock(return_value=True)
    health = [{'status': 'Offline', 'error': 'test failure'}] * 3 + [
        {'status': 'Online', 'cpu': 15, 'ram': 20, 'disk': 30}]
    async def stats(*args, **kwargs):
        return health.pop(0)
    store = types.SimpleNamespace(incident=lambda *args: incidents.append(args), should_notify=lambda *args: True)
    monkeypatch.setitem(__import__('sys').modules, 'pro_store', types.SimpleNamespace(ProStore=lambda: store))
    scope = {'asyncio': asyncio, 'run_sync': direct, 'SUPER_ADMIN_ID': 777, 'DOWN_RETRY_LIMIT': 3,
        'SERVER_FAILURE_COUNTS': {}, 'CPU_ALERT_TRACKER': {}, '_RESOURCE_RUNNING': {21},
        '_RESOURCE_LIMIT': asyncio.Semaphore(8), 'time': __import__('time'),
        'logger': logging.getLogger('test'), 'send_notification': notify,
        'sec': types.SimpleNamespace(decrypt=lambda value: value),
        'db': types.SimpleNamespace(check_access=lambda *args: (True, 'ok'),
            persist_server_observation=lambda *args: observations.append(args)),
        'StatsManager': types.SimpleNamespace(check_full_stats=stats),
        '_settings_snapshot': AsyncMock(return_value={})}
    fn = isolated('cronjobs.py', 'check_single_server_resources', scope)
    server = {'id': 21, 'owner_id': 15, 'ip': '127.0.0.1', 'port': 22,
        'username': 'audit', 'password': 'local-only', 'name': 'test'}
    async def check():
        for _ in range(4):
            await fn(object(), server)
    asyncio.run(check())
    assert len(observations) == 4
    assert [incident[-1] for incident in incidents] == [True, False]
    assert notify.await_count == 2
    assert not scope['SERVER_FAILURE_COUNTS'] and not scope['_RESOURCE_RUNNING']
