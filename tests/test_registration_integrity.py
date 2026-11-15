"""New commercial registrations must never fall through to legacy quotas."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest
import psycopg2
from test_billing_workflow import service,plan
from test_postgres_workflow import isolated_database
from billing import Billing


def test_new_user_without_free_plan_can_buy_but_cannot_allocate_resources(service):
    paid=plan(service,100)
    service.db.execute('UPDATE sonar_plans SET enabled=FALSE WHERE price=0')
    service.db.add_or_update_user(9901,'New customer')
    sub=service.subscription(9901)
    assert sub is not None and sub['effective_status']!='active'
    assert sub['limits']==dict.fromkeys(('servers','configs','websites','agents','team','destinations'),0)
    assert not service.db.check_access(9901)[0] and service.db.check_read_access(9901)[0]
    with pytest.raises(psycopg2.errors.RaiseException,match='SUBSCRIPTION_INACTIVE'):
        service.db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(9901,'single','denied','synthetic-denied')")
    service.adjust_wallet(901,9901,100,'new-customer-fund','purchase eligibility')
    assert service.checkout(9901,paid,'wallet','new-customer-purchase')['status']=='paid'
    assert service.db.check_access(9901)[0]


def test_enrollment_retry_does_not_downgrade_paid_legacy_fields(service):
    ident=plan(service,0)
    service.checkout(902,ident,'wallet','existing-paid-enrollment')
    service.manage(901,902,overrides={'servers':9},reason='specific purchased capacity')
    before=service.db.get_user(902)
    service.enroll(902)
    after=service.db.get_user(902)
    assert after['server_limit']==before['server_limit']==9
    assert after['expiry_date']==before['expiry_date']


def test_administrator_created_user_is_also_enrolled_without_free_plan(service):
    service.db.execute('UPDATE sonar_plans SET enabled=FALSE WHERE price=0')
    service.set_user(901,9902,'Created by administrator',False,'create commercial account')
    assert service.subscription(9902) is not None
    assert not service.db.check_access(9902)[0]
    # Existing customers without commercial subscriptions remain legacy.
    service.set_user(901,903,'Existing legacy customer',False,'rename old customer')
    assert service.subscription(903) is None


def test_parallel_registration_creates_exactly_one_account_and_subscription(service):
    def register(_):service.db.add_or_update_user(9903,'Concurrent customer')
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(register,range(12)))
    assert service.db.execute('SELECT COUNT(*) n FROM users WHERE user_id=9903',fetch='one')['n']==1
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_subscriptions WHERE user_id=9903',fetch='one')['n']==1


def test_failed_enrollment_rolls_back_account_creation(service):
    with patch.object(Billing,'enroll_in_transaction',side_effect=RuntimeError('injected enrollment failure'),create=True):
        with pytest.raises(RuntimeError,match='injected enrollment failure'):
            service.db.add_or_update_user(9904,'Must roll back')
    assert service.db.get_user(9904) is None


def test_pending_registration_home_explains_plan_selection(service,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import bot_logic_mod.menu as menu
    service.db.execute('UPDATE sonar_plans SET enabled=FALSE WHERE price=0')
    service.db.add_or_update_user(9905,'No free plan')
    monkeypatch.setattr(menu,'db',service.db);monkeypatch.setattr(menu,'Billing',lambda db:service)
    update=SimpleNamespace(effective_user=SimpleNamespace(id=9905,full_name='No free plan'),callback_query=None,message=SimpleNamespace(reply_text=AsyncMock()))
    asyncio.run(menu.show_main_menu(update,SimpleNamespace()))
    args=update.message.reply_text.call_args
    assert 'در انتظار انتخاب پلن' in args.args[0]
    callbacks={b.callback_data for row in args.kwargs['reply_markup'].inline_keyboard for b in row}
    assert 'bill_home' in callbacks and 'v6_servers' not in callbacks
