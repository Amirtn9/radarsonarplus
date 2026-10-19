"""Focused regression tests for incident report 5.2.3.

These are offline code-path tests; a live Ubuntu/Postgres deployment is still required.
"""
import asyncio
import re
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_actions_have_single_parser_for_both_button_layouts():
    from server_callbacks import parse_server_action
    assert parse_server_action('act_reboot_11') == ('reboot',11)
    assert parse_server_action('act_11_reboot') == ('reboot',11)
    assert parse_server_action('act_checkhost_11_8.8.8.8') == ('checkhost',11)
    for payload in ('act_delete', 'act_reboot_bad_42', 'act_99', 'act_global_full_report',
                    'act_reboot_-1', 'act_11', 'act_0_reboot'):
        assert parse_server_action(payload) is None


def test_authorization_checks_actual_operation_owner_even_for_admin():
    src=(ROOT/'secure_handlers.py').read_text()
    assert 'parse_server_action(data)' in src
    assert "db.owns_resource('servers', sid, uid)" in src
    operation=(ROOT/'bot_logic_mod/servers.py').read_text()
    start=operation.index('async def server_actions(')
    sub=operation[start:operation.index('    if srv[',start)]
    assert 'db.get_server_for_user' in sub
    assert "StatsManager.check_host_api, srv['ip']" in operation[start:]
    assert sub.index('db.get_server_for_user') < sub.index('sec.decrypt') if 'sec.decrypt' in sub else True


def test_ui_search_targets_real_input_and_safe_call_forwards_kwargs():
    script=(ROOT/'pro_web/static/app.js').read_text()
    assert "$('search').querySelector('input')" not in script
    assert "$('search').addEventListener('input'" in script
    pro=(ROOT/'pro_handlers.py').read_text()
    assert 'async def safe_call(update,context,fn,*args,write=False,**kwargs)' in pro
    assert 'return await invoke(fn,*args,**kwargs)' in pro


def test_installer_fails_closed_before_delete_and_imports_project():
    script=(ROOT/'install.sh').read_text()
    strategy=script.split('select_install_strategy() {')[1].split('valid_port() {')[0]
    assert re.search(r'reset_project_components\s*\|\|\s*return 1;\s*return 0', strategy)
    reset=script.split('reset_project_components() {')[1].split('select_install_strategy() {')[0]
    assert 'archive_existing_components || return 1' in reset
    assert reset.index('archive_existing_components ||') < reset.index('dropdb --if-exists')
    stage=script.split('install_app() {')[1].split('configure_web_port() {')[0]
    assert 'chmod -R a+rX "$STATE_STAGE/venv"' in stage
    assert 'verify_service_sources "$STATE_STAGE"' in stage
    assert stage.index('verify_service_sources "$STATE_STAGE"') < stage.index('systemctl enable --now')
    assert stage.index('runuser -u "$APP_USER" -- "$STATE_STAGE/venv/bin/python"') < stage.index('systemctl enable --now')


def test_conversation_bulk_install_state_and_ws_port_unified():
    d=(ROOT/'dispatcher.py').read_text()
    assert "pattern='^admin_agent_install_all$'" in d
    assert 'ADMIN_AGENT_PORT_INPUT:' in d
    panel=(ROOT/'admin_panel.py').read_text()
    assert "CommandHandler('cancel', bot_logic.cancel_handler_func)" in d
    assert 'if update.effective_user.id != SUPER_ADMIN_ID' in panel
    assert 'valid_agent_port(selected_port' in panel
    servers=(ROOT/'bot_logic_mod/servers.py').read_text()
    assert "valid_agent_port(data.get('ws_port') or AGENT_PORT)" in servers


def test_subscriptions_preserve_stable_ids_and_owner_sql_order():
    src=(ROOT/'tunnel_logic.py').read_text()
    section=src.split('def _bulk_save_sub_items')[1].split('def _save_sub_info')[0]
    assert "AND link=%s" in section and 'previous' in section and 'incoming' in section
    assert 'DELETE FROM tunnel_configs WHERE owner_id=%s' in section
    assert "DELETE FROM tunnel_configs WHERE owner_id=%s AND parent_sub_id=%s AND type=\'sub_item\'" not in section
    tunnels=(ROOT/'bot_logic_mod/tunnels.py').read_text()
    assert '(sub_id, update.effective_user.id)' in tunnels


def test_websocket_pool_disconnects_on_cancel():
    import ws_client
    class FakeSocket:
        def __init__(self):self.closed=False
        async def send(self, message):await asyncio.sleep(1000)
        async def recv(self):return '{}'
        async def close(self):self.closed=True
    async def run():
        pool=ws_client.WebSocketPool(max_per_key=1)
        sock=FakeSocket()
        key=('127.0.0.1',1234,'token','cert')
        pool._counts[key]=1
        pool._queues[key]=asyncio.LifoQueue()
        async def fake_acquire(k):return ws_client._Conn(sock,key,0,0,asyncio.Lock())
        pool._acquire=fake_acquire
        task=asyncio.create_task(pool.request(*key[:3],{'action':'get_stats'},retries=0,tls_certificate=key[3]))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await asyncio.sleep(0.05)
        assert sock.closed
        assert pool._counts[key]==0
        assert pool._queues[key].empty()
    asyncio.run(run())


def test_history_monitoring_and_cli_path_match():
    assert '/opt/sonar-agent-venv/bin/python' in (ROOT/'core.py').read_text()
    assert 'async def server_report_job' in (ROOT/'cronjobs.py').read_text()
    assert 'INSERT INTO server_stats' in (ROOT/'database.py').read_text()
