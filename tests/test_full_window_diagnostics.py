from datetime import datetime, timezone
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import diagnostics


def test_window_filter_keeps_all_recent_lines_and_multiline_traceback():
    cutoff = datetime(2026, 10, 15, tzinfo=timezone.utc)
    text = (
        '2026-10-14T23:59:00+00:00 old record\n'
        '2026-10-16T10:00:00+00:00 ERROR first line\n'
        'Traceback (most recent call last):\n'
        '  File "agent.py", line 5, in run\n'
        'RuntimeError: example\n'
        '2026-10-22T11:00:00+00:00 next record\n'
    )
    result = diagnostics.filter_log_window(text, cutoff)
    assert 'old record' not in result
    assert 'ERROR first line' in result
    assert 'Traceback' in result and 'RuntimeError: example' in result
    assert 'next record' in result


def test_secret_values_are_redacted_without_dropping_log_line():
    line = '2026-10-22T11:00:00Z ERROR job=42 password=do-not-leak stage=venv-create failed\n'
    result = diagnostics.sanitize(line, hide_ips=False, hide_identifiers=False)
    assert 'job=42' in result and 'stage=venv-create failed' in result
    assert 'do-not-leak' not in result
    assert 'ERROR' in result and 'password=[REDACTED]' in result


def test_bundle_keeps_large_log_body_without_per_file_tail_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, 'snapshot', lambda: {'project_version': 'test'})
    payload = ''.join(f'2026-10-22T11:{i // 60:02d}:{i % 60:02d}Z INFO full-record-{i:05d}\n' for i in range(12000))
    monkeypatch.setattr(diagnostics, 'log_source', lambda name, days=7: payload if name == 'bot' else f'{name}\n')
    path = diagnostics.build_bundle(tmp_path / 'full.zip', days=7)
    with zipfile.ZipFile(path) as archive:
        text = archive.read('logs/bot.log').decode()
        assert archive.testzip() is None
    assert 'full-record-00000' in text
    assert 'full-record-11999' in text
    assert text.count('full-record-') == 12000


def test_relay_affinity_keeps_same_agent_until_failure_or_expiry():
    from ssh_relay import stable_relay_order, remember_relay_success, forget_relay_route
    pool = [{'id': 10}, {'id': 20}, {'id': 30}]
    key = '203.0.113.10:22'
    remember_relay_success(key, 20)
    assert [x['id'] for x in stable_relay_order(pool, key)] == [20, 10, 30]
    forget_relay_route(key, 20)
    assert [x['id'] for x in stable_relay_order(pool, key)] == [10, 20, 30]


def test_relay_affinity_uses_database_across_worker_restarts():
    from ssh_relay import stable_relay_order
    class DB:
        def execute(self, query, params=(), fetch=None):
            if 'monitor_relay_affinity' in query:
                return {'server_id': 30}
            raise AssertionError(query)
    pool = [{'id': 10}, {'id': 20}, {'id': 30}]
    assert [x['id'] for x in stable_relay_order(pool, '8.8.8.8:22', db=DB())] == [30, 10, 20]


def test_global_relay_pool_can_include_foreign_region_nodes():
    from datetime import datetime, timezone
    from monitor_fleet import available_relay_nodes
    class DB:
        def execute(self, query, params=(), fetch=None):
            if 'SELECT strategy FROM monitor_fleet_policy' in query:
                return {'strategy':'balanced'}
            if 'FROM servers s' in query and 'monitor_fleet_nodes f' in query:
                now = datetime.now(timezone.utc)
                return [
                    {'id':2,'name':'foreign','ip':'8.8.4.4','location_type':'ext','enabled':True,'approved_by':1,
                     'relay_ready':True,'tls_enrolled':True,'token_configured':True,'route_priority':50,
                     'latency_ms':20,'cpu_pct':10,'last_probe_ok':True,'last_probe_at':now,'inflight':0,
                     'completed_tests':20,'successful_tests':19,'consecutive_failures':0},
                    {'id':1,'name':'iran','ip':'1.1.1.1','location_type':'ir','enabled':True,'approved_by':1,
                     'relay_ready':True,'tls_enrolled':True,'token_configured':True,'route_priority':10,
                     'latency_ms':30,'cpu_pct':15,'last_probe_ok':True,'last_probe_at':now,'inflight':0,
                     'completed_tests':20,'successful_tests':19,'consecutive_failures':0},
                ]
            raise AssertionError(query)
    rows = available_relay_nodes(DB(), 99)
    assert [r['id'] for r in rows] == [1, 2]
    assert {r['location_type'] for r in rows} == {'ir','ext'}


def test_diagnostic_helper_rejects_arbitrary_command():
    import subprocess
    helper = ROOT / 'scripts' / 'sonar-diagnostic-read.sh'
    result = subprocess.run(['bash', str(helper), 'cat', '/etc/passwd'], capture_output=True, text=True)
    assert result.returncode == 64
    assert 'USAGE' in result.stderr
