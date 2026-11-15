"""Telegram configuration handlers exercised against an isolated real database."""
import ast
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_billing_workflow import service
from test_postgres_workflow import isolated_database


def handler(name,namespace):
    source=Path(__file__).resolve().parents[1]/'bot_logic_mod/tunnels.py'
    tree=ast.parse(source.read_text())
    node=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name==name)
    node.returns=None
    for arg in node.args.args:arg.annotation=None
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    return namespace[name]


@pytest.mark.asyncio
async def test_subscription_export_uses_owner_and_parent_in_correct_order(service):
    db=service.db
    parent=db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(902,'sub_source','My subscription','https://example.org/sub') RETURNING id",fetch='one')['id']
    db.execute("INSERT INTO tunnel_configs(owner_id,parent_sub_id,type,name,link) VALUES(902,%s,'sub_item','Owned','vless://synthetic-owned')",(parent,))
    db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(903,'single','Private','vless://private-other-owner')")
    message=SimpleNamespace(reply_document=AsyncMock())
    update=SimpleNamespace(callback_query=SimpleNamespace(data=f'get_sub_links_{parent}',message=message))
    export=handler('get_sub_links_action',{'db':db,'shared_uid':lambda u:902,'io':io,'safe_callback_answer':AsyncMock()})
    await export(update,SimpleNamespace())
    message.reply_document.assert_awaited_once()
    document=message.reply_document.call_args.kwargs['document']
    assert document.getvalue()==b'vless://synthetic-owned\n'


@pytest.mark.asyncio
async def test_unknown_config_details_dont_crash_or_display_red_failure(service):
    db=service.db
    ident=db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link,last_status,last_ping) VALUES(902,'single','Untested','vless://synthetic','Unknown',NULL) RETURNING id",fetch='one')['id']
    edit=AsyncMock()
    show=handler('show_config_details',{'db':db,'shared_uid':lambda u:902,'safe_callback_answer':AsyncMock(),
        'safe_edit_message':edit,'keyboard':SimpleNamespace(config_detail_kb=lambda *a:None)})
    update=SimpleNamespace(callback_query=SimpleNamespace(data=f'config_details_{ident}'))
    await show(update,SimpleNamespace(user_data={}))
    text=edit.call_args.args[1]
    assert '⚪' in text and 'اندازه‌گیری نشده' in text and '🔴' not in text
    assert '10/10' not in text
