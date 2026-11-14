"""Behavior checks for the first audit corrections."""
import asyncio
import ast
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_only_explicit_user_errors_expose_their_message():
    from access_policy import monitoring_setting
    from pro_portal import guarded
    from user_errors import UserError
    async def check():
        with pytest.raises(HTTPException) as result:
            await guarded(monitoring_setting, 'small', 11)
        assert result.value.status_code == 400
        assert 'حجم تست' in result.value.detail
        def internal():
            raise ValueError('secret-internal-value')
        with pytest.raises(HTTPException) as result:
            await guarded(internal)
        assert result.value.detail == 'Invalid request'
    asyncio.run(check())
    assert issubclass(UserError, ValueError)


def test_security_headers_apply_without_enabling_hsts_for_http():
    from pro_portal import app
    for scheme in ('http', 'https'):
        response = TestClient(app, base_url=scheme+'://testserver').get('/healthz')
        assert response.status_code == 200
        csp = response.headers['content-security-policy']
        for directive in ("connect-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'self'"):
            assert directive in csp.split('; ')
        assert ' ws:' not in csp and ' wss:' not in csp
        assert response.headers['permissions-policy'] == 'camera=(), microphone=(), geolocation=()'
        assert response.headers['cross-origin-opener-policy'] == 'same-origin'
        assert ('strict-transport-security' in response.headers) == (scheme == 'https')


def test_bot_token_rotation_is_visible_without_restart(monkeypatch):
    from pro_portal import get_bot_token
    monkeypatch.setenv('SONAR_BOT_TOKEN', 'first-synthetic-token')
    assert get_bot_token() == 'first-synthetic-token'
    monkeypatch.setenv('SONAR_BOT_TOKEN', 'rotated-synthetic-token')
    assert get_bot_token() == 'rotated-synthetic-token'


def test_config_file_token_rotation_is_visible_without_restart(monkeypatch, tmp_path):
    import json
    import pro_portal
    monkeypatch.delenv('SONAR_BOT_TOKEN', raising=False)
    monkeypatch.setattr(pro_portal, 'BASE', tmp_path)
    config = tmp_path / 'sonar_config.json'
    for token in ('first-synthetic-token', 'rotated-synthetic-token'):
        config.write_text(json.dumps({'bot_token': token}))
        assert pro_portal.get_bot_token() == token


def test_daily_jobs_use_tehran_wall_clock():
    # Extract the scheduling expressions without starting a live Telegram bot.
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text())
    schedules = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == 'run_daily']
    import datetime as dt
    times = [eval(compile(ast.Expression(next(k.value for k in call.keywords if k.arg == 'time')), '<schedule>', 'eval'),
                  {'dt': dt, 'ZoneInfo': ZoneInfo}) for call in schedules]
    assert [(value.hour, value.minute) for value in times] == [(8, 30), (3, 0)]
    assert all(value.tzinfo.key == 'Asia/Tehran' for value in times)


def test_query_validation_rejects_invalid_filters():
    import pro_portal
    pro_portal.app.dependency_overrides[pro_portal.actor] = lambda: 901
    try:
        client = TestClient(pro_portal.app)
        for query in ('days=0', 'days=31', 'days=invalid'):
            assert client.get('/api/metrics/1?' + query).status_code == 422
        assert client.get('/api/incidents?status=typo').status_code == 422
    finally:
        pro_portal.app.dependency_overrides.pop(pro_portal.actor, None)
