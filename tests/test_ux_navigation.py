"""Real subscription information and safe, reachable Bot navigation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_postgres_workflow import isolated_database
from test_billing_workflow import service,plan


def setup_menu(service,monkeypatch,uid=902):
    import bot_logic_mod.menu as menu
    monkeypatch.setattr(menu,'db',service.db)
    monkeypatch.setattr(menu,'SUPER_ADMIN_ID',901)
    monkeypatch.setattr(menu,'Billing',lambda db:service)
    update=SimpleNamespace(callback_query=None,effective_user=SimpleNamespace(id=uid,full_name='Customer <b>'),message=SimpleNamespace(reply_text=AsyncMock()))
    context=SimpleNamespace(args=[],bot=SimpleNamespace(send_message=AsyncMock()),user_data={})
    return menu,update,context


def test_bot_home_uses_real_subscription_without_claiming_unlimited(service,monkeypatch):
    ident=plan(service);service.manage(901,902,'active',3,reason='three day grant',plan_id=ident)
    menu,update,context=setup_menu(service,monkeypatch)
    asyncio.run(menu.show_main_menu(update,context))
    text=update.message.reply_text.call_args.args[0]
    assert 'Monitoring' in text and 'روز تا پایان' in text
    assert 'نامحدود' not in text and 'بدون پایان زمانی' not in text
    callbacks={b.callback_data for row in update.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row}
    assert {'bill_home','v6_servers','v6_configs'}<=callbacks


def test_expired_user_can_reach_renewal_and_help_without_technical_actions(service,monkeypatch):
    ident=plan(service);service.manage(901,902,'active',1,reason='initial grant',plan_id=ident)
    service.db.execute("UPDATE sonar_subscriptions SET expires_at=NOW()-INTERVAL '1 hour' WHERE user_id=902")
    menu,update,context=setup_menu(service,monkeypatch)
    asyncio.run(menu.show_main_menu(update,context))
    args=update.message.reply_text.call_args
    assert 'پایان اعتبار' in args.args[0] and 'حفظ شده' in args.args[0]
    buttons={b.callback_data for row in args.kwargs['reply_markup'].inline_keyboard for b in row}
    assert 'bill_home' in buttons and 'v6_guide' in buttons and 'v6_servers' not in buttons
    import secure_handlers
    monkeypatch.setattr(secure_handlers,'Database',lambda:service.db)
    monkeypatch.setattr(secure_handlers,'SUPER_ADMIN_ID',901)
    assert secure_handlers._access_sync(902,'v6_guide','guide_menu')
    assert not secure_handlers._access_sync(902,'add_server','add_server')


def test_welcome_reads_trial_terms_and_escapes_user_name(service,monkeypatch):
    menu,update,context=setup_menu(service,monkeypatch,904)
    asyncio.run(menu.register_user_logic(update,context))
    text=update.message.reply_text.call_args.args[0]
    sub=service.subscription(904)
    assert f"مدت اولیه: {sub['snapshot']['days']} روز" in text
    assert '60 روز' not in text and '&lt;b&gt;' in text
    assert update.message.reply_text.call_args.kwargs['parse_mode']=='HTML'


def test_workspace_capabilities_follow_role_and_license(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from control_api import router
    from control_store import Control
    from pro_store import ProStore
    from workspace_service import ACTOR_ID,ACTIVE_WORKSPACE
    pro=ProStore(service.db);control=Control(pro)
    invite=control.invite(901,903,'viewer');control.decide(903,invite,True)
    async def actor():
        token=ACTOR_ID.set(903);workspace=ACTIVE_WORKSPACE.set(901)
        try:yield 903
        finally:ACTOR_ID.reset(token);ACTIVE_WORKSPACE.reset(workspace)
    async def guarded(fn,*args,**kwargs):return await asyncio.to_thread(fn,*args,**kwargs)
    app=FastAPI();app.include_router(router(pro,actor,guarded));client=TestClient(app)
    caps=client.get('/api/control/capabilities').json()
    assert caps['workspace']==901 and caps['role']=='viewer'
    assert not any(caps[k] for k in ('technical','server','config','monitor','team'))
    control.role(901,903,'server');caps=client.get('/api/control/capabilities').json()
    assert caps['server'] and not caps['technical'] and not caps['config']
    control.role(901,903,'technical');caps=client.get('/api/control/capabilities').json()
    assert caps['server'] and caps['technical'] and caps['config'] and not caps['team']
