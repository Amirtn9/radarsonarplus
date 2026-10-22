"""Regression checks for web startup, Mini App sessions and relay reachability."""
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _Connection:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class _Cursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))


class _DB:
    def __init__(self):
        self.conn = _Connection()
        self.cursor = _Cursor()

    @contextmanager
    def get_connection(self):
        yield self.conn, self.cursor


def test_miniapp_session_uses_random_tokens_and_stores_only_hashes():
    from web_login import create_session, digest, valid_token

    db = _DB()
    session, csrf = create_session(db, 12345)

    assert valid_token(session) and valid_token(csrf)
    assert session != csrf
    assert db.conn.commits == 1
    insert = next((query, params) for query, params in db.cursor.calls if 'INSERT INTO sonar_web_sessions' in query)
    query, params = insert
    assert "NOW()+INTERVAL '8 hours'" in query
    assert params == (digest(session), 12345, digest(csrf))
    assert session not in str(params) and csrf not in str(params)


def test_standalone_web_startup_installs_pro_and_service_schema():
    source = (ROOT / 'pro_portal.py').read_text(encoding='utf-8')
    start = source.index('async def bootstrap_additive_schemas')
    end = source.index("@app.on_event('shutdown')", start)
    startup = source[start:end]
    assert 'await asyncio.to_thread(install_pro_schema,store.db)' in startup


def test_miniapp_frontend_exchanges_initdata_and_uses_csrf_cookie_session():
    source = (ROOT / 'pro_web' / 'static' / 'app.js').read_text(encoding='utf-8')
    assert "fetch('/api/web/miniapp-session'" in source
    assert 'await prepareMiniAppSession()' in source
    assert "'X-CSRF-Token':csrfToken" in source
    assert 'me.id)===telegramUserId' in source


def test_cookie_authenticated_console_polling_does_not_require_telegram_initdata():
    source = (ROOT / 'pro_web' / 'static' / 'console.js').read_text(encoding='utf-8')
    start = source.index('async function refreshVisible()')
    end = source.index('\n  byId(\'channels-refresh\')', start)
    body = source[start:end]
    assert '!window.Telegram?.WebApp?.initData' not in body
    assert "!byId('session-warning')?.hidden" in body


def test_reachability_selection_covers_both_regions_when_available():
    from fleet_api import _select_reachability_nodes

    nodes = [
        {'id': 1, 'location_type': 'ir'},
        {'id': 2, 'location_type': 'ir'},
        {'id': 3, 'location_type': 'ext'},
        {'id': 4, 'location_type': 'ext'},
    ]
    selected = _select_reachability_nodes(nodes, limit=3)
    assert [item['id'] for item in selected] == [1, 3, 2]


def test_reachability_result_distinguishes_iran_and_external_agent_results():
    from fleet_api import _reachability_result

    nodes = [{'id': 1, 'location_type': 'ir'}, {'id': 2, 'location_type': 'ext'}]
    assert _reachability_result(False, [True, False], nodes) == ('reachable_from_iran', True, True)
    assert _reachability_result(False, [False, True], nodes) == ('reachable_from_agent', True, False)
    assert _reachability_result(False, [False], nodes[:1]) == (
        'unreachable_from_available_vantage_points', False, False
    )
    assert _reachability_result(False, [], []) == ('insufficient_evidence', None, None)
