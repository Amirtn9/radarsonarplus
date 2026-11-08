import pytest

from core import ServerMonitor
from server_stats import StatsManager


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [{'error':'timeout'}, {}, {'status':'Unknown'}])
async def test_failed_or_incomplete_agent_response_is_not_online_or_zero(monkeypatch, response):
    async def request(*args, **kwargs):
        return response
    monkeypatch.setattr(ServerMonitor, 'ws_send_command', request)
    result = await ServerMonitor.check_full_stats_ws('127.0.0.1', 8080, '')
    assert result['status'] != 'Online'
    for key in ('cpu','ram','traffic_gb','uptime_sec'):
        assert key not in result


@pytest.mark.asyncio
async def test_failed_ssh_fallback_leaves_measurements_unknown(monkeypatch):
    async def ws(*args, **kwargs):
        return {'status':'Offline','error':'timeout'}
    def ssh(*args, **kwargs):
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(ServerMonitor, 'check_full_stats_ws', ws)
    monkeypatch.setattr(ServerMonitor, 'check_full_stats', ssh)
    result = await StatsManager.check_full_stats('127.0.0.1', 22, 'test', '')
    assert result['status'] == 'Unknown'
    assert 'cpu' not in result and 'ram' not in result
