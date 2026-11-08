import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from log_management import LogStore, category_for, time_bounds
from logging_config import JSONFormatter, RedactedFormatter, redact


@pytest.mark.parametrize('secret', [
    'password="sensitive value"', 'Authorization: Bearer abc-super-secret',
    '{"access_token": "abc-super-secret"}', 'postgresql://user:abc-super-secret@localhost/db',
    'vless://abc-super-secret@localhost:443',
])
def test_secrets_are_redacted_in_formatted_text(secret):
    result = redact(secret)
    assert 'abc-super-secret' not in result
    assert 'sensitive value' not in result


def test_json_logging_redacts_nested_extras_and_preserves_valid_json():
    record = logging.LogRecord('test', logging.ERROR, __file__, 1,
                               'Authorization: Bearer abc-super-secret', (), None)
    record.extra_data = {'nested': {'password': 'nested-secret', 'access_token': 'token-secret'},
                         'safe': ['line one\nline two', 42], 'cookie': 'cookie-secret'}
    encoded = JSONFormatter().format(record)
    parsed = json.loads(encoded)
    assert parsed['safe'] == ['line one\nline two', 42]
    for secret in ('nested-secret', 'token-secret', 'cookie-secret', 'abc-super-secret'):
        assert secret not in encoded
    assert 'abc-super-secret' not in RedactedFormatter().format(record)


@pytest.mark.parametrize('hours', [24, 48, 168])
def test_windows_use_actual_utc_time(hours):
    start, end = time_bounds(str(hours))
    assert end-start == timedelta(hours=hours)
    assert end.tzinfo == timezone.utc


def test_custom_dates_require_timezone_and_order():
    start = datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=3, minutes=30)))
    end = start+timedelta(days=1)
    assert time_bounds('custom', start, end) == (start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    for bounds in ((None, end), (start.replace(tzinfo=None), end), (end, start)):
        with pytest.raises(ValueError):
            time_bounds('custom', *bounds)
    assert time_bounds('all')[0] is None


def test_admin_and_confirmation_are_checked_before_any_database_access():
    store = LogStore(object(), 901)
    for user in (0, 902):
        with pytest.raises(PermissionError):
            store.clear(user, 'DELETE ALL LOGS')
        with pytest.raises(PermissionError):
            store.list(user)
    with pytest.raises(ValueError):
        store.clear(901, 'yes')
    with pytest.raises(PermissionError):
        LogStore(object(), 0).clear(0, 'DELETE ALL LOGS')


def test_invalid_filters_are_rejected_before_database_access():
    store = LogStore(object(), 901)
    for kwargs in ({'level': "ERROR' OR true --"}, {'category': 'unknown'}, {'page': 0}, {'per_page': 1000}):
        with pytest.raises(ValueError):
            store.list(901, **kwargs)


@pytest.mark.parametrize('source,expected', [('ws_client','node'), ('database','database'),
    ('pro_auth','authentication'), ('pro_portal','api'), ('cronjobs','system')])
def test_categories(source, expected):
    assert category_for(source) == expected


def test_log_api_enforces_admin_access_and_confirmation(monkeypatch):
    import pro_portal
    from fastapi.testclient import TestClient
    client = TestClient(pro_portal.app)
    monkeypatch.setattr(pro_portal, 'SUPER_ADMIN_ID', 901)
    pro_portal.app.dependency_overrides[pro_portal.actor] = lambda: 902
    try:
        assert client.get('/api/admin/logs').status_code == 403
        assert client.request('DELETE', '/api/admin/logs', json={'confirmation':'DELETE ALL LOGS'}).status_code == 403
        pro_portal.app.dependency_overrides[pro_portal.actor] = lambda: 901
        assert client.request('DELETE', '/api/admin/logs', json={'confirmation':'yes'}).status_code == 400
        assert client.get('/api/admin/logs?window=custom').status_code == 400
    finally:
        pro_portal.app.dependency_overrides.pop(pro_portal.actor, None)
