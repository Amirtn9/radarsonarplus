"""Static regressions for stale callback acknowledgement and diagnostics timeout handling."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_diagnostics_acknowledged_before_background_work():
    source=(ROOT/'v6_ui.py').read_text(encoding='utf8')
    entry=source[source.index('async def diag_zip_entry'):source.index('async def diag_zip(')]
    assert entry.index('await _safe_answer') < entry.index('spawn_background')
    assert "SecureCallbackQueryHandler(diag_zip_entry" in source
    assert 'await update.callback_query.answer' not in entry

def test_diagnostics_remote_collection_has_no_outer_short_timeout():
    source=(ROOT/'v6_ui.py').read_text(encoding='utf8')
    worker=source[source.index('async def diag_zip('):source.index('async def guide_menu')]
    assert 'await _diag_sync(collect_agent_journals' in worker
    assert 'timeout_seconds=420' in worker
    assert 'wait_for(run_sync(collect_agent_journals' not in worker

def test_staged_venv_retries_exact_python_venv_package():
    source=(ROOT/'agent_bootstrap.py').read_text(encoding='utf8')
    assert "exact_python = 'python' + str(target['py'])" in source
    assert "exact_package = 'python' + pyver + '-venv'" in source
    assert 'matching python3-venv repair and staging retry failed' in source


def test_callback_answer_stale_query_is_nonfatal():
    import asyncio
    from telegram_safety import safe_callback_answer, BadRequest
    class Query:
        async def answer(self, *args, **kwargs):
            raise BadRequest('Query is too old and response timeout expired')
    assert asyncio.run(safe_callback_answer(Query())) is False


def test_relay_socket_exposes_socket_compatibility_methods():
    from ssh_relay import RelaySocket
    class FakeWS:
        def __init__(self): self.frames=[b'abc']; self.closed=False
        def recv(self, timeout=None): return self.frames.pop(0)
        def send(self, value): return None
        def close(self): self.closed=True
        @property
        def socket(self): return None
    relay=RelaySocket(FakeWS(),'8.8.8.8',22)
    target=bytearray(3)
    assert relay.recv_into(target)==3 and bytes(target)==b'abc'
    assert relay.getpeername()==('8.8.8.8',22)
    assert relay.gettimeout()==15.0
    relay.close()
    assert relay.closed


def test_diagnostic_report_survives_database_and_agent_timeouts(monkeypatch, tmp_path):
    import ast
    import asyncio
    import html
    import logging
    import sys
    import types
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'v6_ui.py').read_text(encoding='utf8'))
    fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'diag_zip')
    namespace = {'asyncio': asyncio, 'html': html, 'Path': Path,
                 'logger': logging.getLogger('diag-test'), '_DIAG_JOB_ACTIVE': False,
                 'is_admin': lambda update: True}

    class DB:
        def get_all_servers(self): return []

    class FakeMessage:
        async def edit_text(self, text): return None

    sent = {'documents': 0, 'messages': []}
    async def send_message(**kwargs):
        sent['messages'].append(kwargs.get('text', ''))
        return FakeMessage()
    async def send_document(**kwargs):
        sent['documents'] += 1

    fake_database = types.ModuleType('database')
    fake_database.Database = DB
    fake_telegram = types.ModuleType('telegram'); fake_telegram.__path__ = []
    fake_error = types.ModuleType('telegram.error')
    fake_error.NetworkError = type('NetworkError', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'database', fake_database)
    monkeypatch.setitem(sys.modules, 'telegram', fake_telegram)
    monkeypatch.setitem(sys.modules, 'telegram.error', fake_error)

    def collect_state(*args, **kwargs): return {}
    def collect_agents(*args, **kwargs): return {}
    def build(*args, **kwargs): return Path(tmp_path / 'diagnostics.zip')
    namespace.update({'collect_operational_state': collect_state,
                      'collect_agent_journals': collect_agents, 'build_bundle': build})
    captured = {}
    async def diag_sync(func, *args, **kwargs):
        name = getattr(func, '__name__', '')
        if name == 'get_all_servers' or func is collect_state or func is collect_agents:
            raise TimeoutError('simulated stage timeout')
        if name == 'build':
            captured.update(kwargs)
            path = Path(tmp_path / 'diagnostics.zip')
            path.write_bytes(b'PK\x03\x04test')
            return path
        if func is DB:
            return DB()
        raise AssertionError(f'unexpected diagnostic stage: {name}')
    namespace['_diag_sync'] = diag_sync
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'v6_ui.py', 'exec'), namespace)
    update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=777))
    context = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=send_message, send_document=send_document))
    asyncio.run(namespace['diag_zip'](update, context))
    assert sent['documents'] == 1
    assert captured['operational_state']['diagnostic_collection_errors'] == {
        'server_inventory': 'TimeoutError', 'operational_state': 'TimeoutError', 'remote_agent_logs': 'TimeoutError'}
    assert not (tmp_path / 'diagnostics.zip').exists()


def test_production_callback_acknowledgements_use_safe_helper():
    root = Path(__file__).resolve().parents[1]
    for name in ('dispatcher.py', 'admin_panel.py', 'bot_logic_mod/ui.py'):
        source = (root / name).read_text(encoding='utf8')
        assert 'callback_query.answer(' not in source, name
    dispatcher = (root / 'dispatcher.py').read_text(encoding='utf8')
    assert "CallbackQueryHandler(_dev_feature_callback, pattern='^dev_feature$')" in dispatcher


def test_all_application_callback_acknowledgements_use_safe_helpers():
    """Only the shared safety helpers may call Telegram's callback answer directly."""
    import ast
    allowed = {ROOT / 'telegram_safety.py', ROOT / 'v6_ui.py'}
    violations = []
    for path in ROOT.rglob('*.py'):
        if 'tests' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != 'answer':
                continue
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr == 'callback_query':
                violations.append(f'{path.relative_to(ROOT)}:{node.lineno}')
            elif isinstance(owner, ast.Name) and owner.id in {'query', 'q'} and path not in allowed:
                violations.append(f'{path.relative_to(ROOT)}:{node.lineno}')
    assert not violations, 'Unsafe callback acknowledgements: ' + ', '.join(violations)


def test_placeholder_callback_uses_shared_safe_acknowledger():
    source = (ROOT / 'dispatcher.py').read_text(encoding='utf8')
    assert 'async def _dev_feature_callback' in source
    assert 'await safe_callback_answer(getattr(update, "callback_query", None)' in source
    assert 'CallbackQueryHandler(_dev_feature_callback, pattern=\'^dev_feature$\')' in source
    assert 'lambda u, c: u.callback_query.answer' not in source


def test_agent_dashboard_database_writes_are_off_event_loop():
    source = (ROOT / 'admin_panel.py').read_text(encoding='utf8')
    section = source[source.index('async def admin_agent_dashboard'):source.index('async def admin_agent_logs_menu')]
    assert 'servers = await run_sync(db.get_all_servers)' in section
    assert 'await run_sync(db.update_agent_state' in section
    assert 'await run_sync(db.update_server_error' in section
    assert 'db.update_agent_state(' not in section
    assert 'db.update_server_error(' not in section
