"""Navigation regression tests which also run without optional VPS dependencies."""
import ast
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]


def _v6_functions():
    """Isolate navigation handlers from Telegram transport for offline testing."""
    tree = ast.parse((ROOT / 'v6_ui.py').read_text('utf-8'))
    fn = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
          and n.name in {'section', 'return_to_section'}]
    namespace = {}
    exec(compile(ast.Module(body=fn, type_ignores=[]), 'v6_ui.py', 'exec'), namespace)
    async def safe_answer(query, *args, **kwargs):
        return await query.answer(*args, **kwargs)
    namespace['_safe_answer'] = safe_answer
    return namespace


def test_navigation_persists_per_user_and_clears_stale_list():
    from navigation import remember_section, get_section, clear_section, section_parent
    a, b = SimpleNamespace(user_data={}), SimpleNamespace(user_data={})
    assert get_section(a) is None
    assert section_parent(a) == 'main_menu'
    remember_section(a, 'configs')
    a.user_data['last_config_list_route'] = 'list_mode_single_4_0'
    remember_section(a, 'configs')
    assert a.user_data['last_config_list_route'] == 'list_mode_single_4_0'
    remember_section(a, 'servers')
    assert get_section(a) == 'servers'
    assert 'last_config_list_route' not in a.user_data
    assert get_section(b) is None
    assert section_parent(a) == 'v6_servers'
    clear_section(a)
    assert get_section(a) is None


def test_section_return_and_admin_authorization():
    from navigation import remember_section, get_section, clear_section
    ns = _v6_functions()
    shown = AsyncMock()
    answered = AsyncMock()
    ctx = SimpleNamespace(user_data={})
    query = SimpleNamespace(data='v6_servers', answer=answered)
    update = SimpleNamespace(callback_query=query)
    ns.update(dict(MENUS={
        'servers': ('server page', [('Add', 'add_server')]),
        'admin': ('admin page', [('Edit', 'admin_panel_main')]),
    }, remember_section=remember_section, get_section=get_section,
        clear_section=clear_section, is_admin=lambda u: False,
        rows=lambda *args: args, show=shown))
    asyncio.run(ns['section'](update, ctx))
    assert ctx.user_data['_sonar_navigation_section'] == 'servers'
    asyncio.run(ns['return_to_section'](update, ctx))
    assert shown.await_count == 2
    assert shown.await_args.args[1] == 'server page'
    assert ('🏠 صفحه اصلی', 'main_menu') in shown.await_args.args[2]
    query.data = 'v6_admin'
    asyncio.run(ns['section'](update, ctx))
    assert get_section(ctx) == 'servers', 'denied admin route must not replace current section'
    assert answered.await_count == 1


def test_back_buttons_routes_have_registered_handler():
    v6 = (ROOT/'v6_ui.py').read_text('utf-8')
    keyboard = (ROOT/'keyboard.py').read_text('utf-8')
    assert "pattern=r'^v6_return$'" in v6
    assert "callback_data='v6_return'" in keyboard
    assert "callback_data='list_groups_for_servers'" in keyboard
    assert "callback_data='tunnel_list_menu'" in keyboard
    assert "def config_test_result_kb(cid, back_callback=" in keyboard
    assert "def config_detail_kb(cid, parent_id=None, back_callback=" in keyboard


def test_no_restart_after_cancelling_or_saving_data():
    for file in ('bot_logic_mod/ui.py', 'bot_logic_mod/servers.py', 'bot_logic_mod/configs.py'):
        tree = ast.parse((ROOT/file).read_text('utf-8'))
        target = {'cancel_handler_func', 'get_group_name', 'process_json_config', 'finalize_sub_adding'}
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name in target:
                text = ast.get_source_segment((ROOT/file).read_text('utf-8'), node)
                assert 'await start(update, context)' not in text
