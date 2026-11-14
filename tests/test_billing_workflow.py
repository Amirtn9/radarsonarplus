"""Real PostgreSQL money, entitlement and capacity regression scenarios."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import pytest
import psycopg2
from psycopg2.extras import Json
from test_postgres_workflow import isolated_database
from billing import Billing,RESOURCES
from schema_migrations import run_startup_migrations
from user_errors import UserError

@pytest.fixture
def service(isolated_database):
    db,_=isolated_database;run_startup_migrations(db)
    db.execute("INSERT INTO users(user_id,full_name) VALUES(901,'Admin'),(902,'Buyer'),(903,'Other')")
    return Billing(db,901)

def plan(service,price=100,limits=None):
    return service.save_plan(901,dict(name='Monitoring',description='test',price=price,days=30,
        limits=limits or dict(zip(RESOURCES,(2,3,1,1,1,2))),features={},enabled=True,priority=0),reason='create tested plan')

def test_wallet_purchase_is_atomic_idempotent_and_snapshot_is_immutable(service):
    ident=plan(service);service.adjust_wallet(901,902,500,'funding-902','initial funding')
    first=service.checkout(902,ident,'wallet','purchase-0001')
    before=service.subscription(902)['expires_at']
    second=service.checkout(902,ident,'wallet','purchase-0001')
    assert str(first['id'])==str(second['id']) and second['status']=='paid'
    assert service.db.get_user(902)['wallet_balance']==400
    assert service.subscription(902)['expires_at']==before
    service.db.execute('UPDATE sonar_plans SET price=999,limits=%s WHERE id=%s',(Json(dict(zip(RESOURCES,[99]*6))),ident))
    assert service.subscription(902)['limits']['servers']==2
    assert second['snapshot']['price']==100
    with pytest.raises(UserError):service.checkout(902,ident,'wallet','purchase-0001',destination=1)

def test_insufficient_wallet_rolls_back_everything(service):
    ident=plan(service)
    with pytest.raises(UserError,match='موجودی'):service.checkout(902,ident,'wallet','insufficient-01')
    assert service.subscription(902) is None
    assert not service.account(902)['orders']
    assert service.db.get_user(902)['wallet_balance']==0

def test_concurrent_wallet_purchases_cannot_overdraw(service):
    ident=plan(service);service.adjust_wallet(901,902,100,'funding-race','funding race')
    def buy(key):
        try:return service.checkout(902,ident,'wallet',key)['status']
        except UserError:return 'insufficient'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(buy,['racing-order-1','racing-order-2']))
    assert sorted(results)==['insufficient','paid']
    assert service.db.get_user(902)['wallet_balance']==0
    assert len(service.account(902)['orders'])==1

def test_manual_crypto_receipt_requires_admin_and_approval_is_once(service):
    ident=plan(service)
    dest=service.save_destination(901,dict(kind='crypto',name='USDT',network='TRC20',address='public-test-address',holder='',instructions='Confirm amount independently',enabled=True),reason='crypto destination')
    order=service.checkout(902,ident,'manual','crypto-order-01',dest)
    assert order['status']=='pending' and service.subscription(902) is None
    with pytest.raises(UserError):service.decide(901,order['id'],True,'verified payment')
    with pytest.raises(UserError):service.evidence(903,order['id'],'other users receipt')
    service.evidence(902,order['id'],'synthetic transaction evidence')
    with pytest.raises(PermissionError):service.decide(902,order['id'],True,'forged approval')
    service.decide(901,order['id'],True,'independently checked transaction')
    expiry=service.subscription(902)['expires_at']
    service.decide(901,order['id'],True,'replayed approval')
    assert service.subscription(902)['expires_at']==expiry
    assert service.db.get_user(902)['wallet_balance']==0

def test_refund_is_once_and_financial_records_are_append_only(service):
    ident=plan(service);service.adjust_wallet(901,902,100,'funding-refund','funding refund')
    order=service.checkout(902,ident,'wallet','refund-order-01')
    service.refund(901,order['id'],'refund after customer request')
    service.refund(901,order['id'],'replayed refund request')
    assert service.db.get_user(902)['wallet_balance']==100
    assert not service.can_monitor(902)
    for table in ('sonar_wallet_ledger','sonar_billing_audit'):
        with pytest.raises(psycopg2.Error):service.db.execute('DELETE FROM '+table)

def test_discount_last_use_is_serialized_at_activation(service):
    ident=plan(service);now=datetime.now(timezone.utc)
    service.save_discount(901,dict(code='SAVE50',kind='percent',value=50,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=1),max_uses=1,user_id=None,plan_id=ident,first_only=True,enabled=True),'discount campaign')
    for uid in (902,903):service.adjust_wallet(901,uid,100,'discount-fund-'+str(uid),'funding discount test')
    service.checkout(902,ident,'wallet','discount-order1',discount_code='SAVE50')
    assert service.db.get_user(902)['wallet_balance']==50
    with pytest.raises(UserError):service.checkout(903,ident,'wallet','discount-order2',discount_code='SAVE50')
    assert service.db.get_user(903)['wallet_balance']==100

def test_quotas_are_independent_and_apply_to_direct_sql(service):
    ident=plan(service,0);service.checkout(902,ident,'wallet','free-order-0001')
    for i in range(2):service.db.execute('INSERT INTO servers(owner_id,name,ip) VALUES(902,%s,%s)',('node'+str(i),'192.0.2.'+str(i+1)))
    with pytest.raises(psycopg2.errors.RaiseException,match='QUOTA_EXCEEDED:servers'):
        service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'overflow','192.0.2.3')")
    for i in range(3):service.db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(902,'single',%s,%s)",('config'+str(i),'test-link-'+str(i)))
    with pytest.raises(psycopg2.errors.RaiseException,match='QUOTA_EXCEEDED:configs'):
        service.db.execute("INSERT INTO tunnel_configs(owner_id,type,name,link) VALUES(902,'single','overflow','overflow-config')")
    service.manage(901,902,overrides={'servers':3},reason='dedicated capacity')
    service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'extra','192.0.2.4')")
    assert service.usage(902)['configs']==3 and service.usage(902)['servers']==3

def test_expiry_preserves_read_access_and_stops_monitoring(service):
    ident=plan(service,0);service.checkout(902,ident,'wallet','expiry-order-1')
    service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'retained','192.0.2.1')")
    service.db.execute("UPDATE sonar_subscriptions SET expires_at=NOW()-INTERVAL '1 second' WHERE user_id=902")
    assert not service.db.check_access(902)[0] and service.db.check_read_access(902)[0]
    assert len(service.account(902)['orders'])==1
    assert not service.db.get_active_servers()
    with pytest.raises(psycopg2.errors.RaiseException,match='SUBSCRIPTION_INACTIVE'):
        service.db.execute("INSERT INTO servers(owner_id,name,ip) VALUES(902,'new','192.0.2.2')")
    service.manage(901,902,days=30,status='active',reason='renew subscription')
    assert service.db.check_access(902)[0] and len(service.db.get_active_servers())==1

def test_capacity_reduction_stops_only_overflow_and_preserves_rows(service):
    ident=plan(service,0);service.checkout(902,ident,'wallet','capacity-order1')
    for i in range(2):service.db.execute('INSERT INTO servers(owner_id,name,ip) VALUES(902,%s,%s)',('node'+str(i),'192.0.2.'+str(i+1)))
    service.manage(901,902,overrides={'servers':1},reason='reduced capacity')
    assert service.usage(902)['servers']==2 and len(service.db.get_active_servers())==1

def test_new_registration_uses_free_plan_without_rewriting_legacy_accounts(service):
    service.db.add_or_update_user(999,'new Telegram user')
    assert service.subscription(999)['limits']['configs']==5
    assert service.subscription(902) is None


def test_financial_roles_have_independent_capabilities_and_can_be_revoked(service):
    service.staff(901,903,'Finance',['users.read','wallet','payments','reports'],True,'delegate finance explicitly')
    service.adjust_wallet(903,902,100,'finance-staff-funding','approved finance adjustment')
    assert service.users(903) and service.db.get_user(902)['wallet_balance']==100
    assert service.finances(903)==[]
    with pytest.raises(PermissionError):service.save_plan(903,{},reason='unauthorized plan change')
    with pytest.raises(PermissionError):service.staff(903,902,'forged',['wallet'],True,'forged delegation')
    service.staff(901,903,'Finance',[],False,'revoke finance access')
    with pytest.raises(PermissionError):service.adjust_wallet(903,902,1,'revoked-staff-funding','attempt after revocation')


def test_financial_reports_include_actual_successful_and_refunded_orders(service):
    ident=plan(service);service.adjust_wallet(901,902,200,'report-funding','report fixture')
    order=service.checkout(902,ident,'wallet','report-order-one')
    rows=service.finances(901)
    assert len(rows)==1 and rows[0]['orders']==1 and rows[0]['amount']==100 and rows[0]['status']=='paid'
    service.refund(901,order['id'],'report refund fixture')
    assert service.finances(901)[0]['status']=='refunded'


def test_manual_voucher_quote_is_honored_after_campaign_ends_and_cancel_releases_reservation(service):
    ident=plan(service);now=datetime.now(timezone.utc)
    voucher=dict(code='QUOTED',kind='percent',value=50,starts_at=now-timedelta(days=1),ends_at=now+timedelta(hours=1),max_uses=2,user_id=None,plan_id=None,first_only=False,enabled=True)
    service.save_discount(901,voucher,'quoted campaign')
    destination=service.save_destination(901,dict(kind='card',name='Synthetic bank',network='',address='synthetic-public-card',holder='',instructions='test only',enabled=True),reason='manual fixture')
    one=service.checkout(902,ident,'manual','voucher-order-01',destination,'QUOTED')
    two=service.checkout(903,ident,'manual','voucher-order-02',destination,'QUOTED')
    counters=service.db.execute("SELECT reserved,uses FROM sonar_discounts WHERE code='QUOTED'",fetch='one');assert counters=={'reserved':2,'uses':0}
    service.cancel(903,two['id'])
    assert service.db.execute("SELECT reserved FROM sonar_discounts WHERE code='QUOTED'",fetch='one')['reserved']==1
    service.db.execute("UPDATE sonar_discounts SET enabled=FALSE,ends_at=NOW()-INTERVAL '1 second' WHERE code='QUOTED'")
    service.evidence(902,one['id'],'synthetic settled bank transfer')
    service.decide(901,one['id'],True,'honor previously accepted quote')
    assert service.subscription(902) and one['amount']==50
    assert service.db.execute("SELECT reserved,uses FROM sonar_discounts WHERE code='QUOTED'",fetch='one')=={'reserved':0,'uses':1}


def test_private_billing_messages_never_use_team_notification_destinations(service):
    import asyncio
    from types import SimpleNamespace
    from billing_delivery import deliver
    service.db.add_channel(902,-1001,'Technical team','all')
    first=service.message(901,902,'Private monitoring account update','private-message01','customer support')
    again=service.message(901,902,'Private monitoring account update','private-message01','retry same message')
    assert first==again
    sent=[]
    async def send(**kwargs):sent.append(kwargs)
    asyncio.run(deliver(service.db,SimpleNamespace(send_message=send)))
    assert len(sent)==1 and sent[0]['chat_id']==902 and sent[0]['text']=='Private monitoring account update'
    asyncio.run(deliver(service.db,SimpleNamespace(send_message=send)))
    assert len(sent)==1
    assert service.db.execute('SELECT state FROM sonar_billing_messages WHERE id=%s',(first,),fetch='one')['state']=='sent'


def test_plan_change_policies_distinguish_grandfathering_renewal_and_current_capacities(service):
    ident=plan(service);service.adjust_wallet(901,902,1000,'pricing-fund-01','pricing policy test')
    service.checkout(902,ident,'wallet','pricing-original')
    source=next(p for p in service.plans(901,True) if p['id']==ident)
    data={k:source[k] for k in ('name','description','price','days','limits','features','enabled','priority','policy')}
    data['price']=200;service.save_plan(901,data,ident,'new','grandfather existing purchases')
    assert next(p for p in service.plans(902) if p['id']==ident)['price']==100
    assert next(p for p in service.plans(903) if p['id']==ident)['price']==200
    assert service.checkout(902,ident,'wallet','pricing-grandfather')['amount']==100
    data['price']=300;service.save_plan(901,data,ident,'renewal','apply new price at renewal')
    assert next(p for p in service.plans(902) if p['id']==ident)['price']==300
    assert service.checkout(902,ident,'wallet','pricing-renewal')['amount']==300
    data['limits']={**data['limits'],'servers':1};data['policy']={'min_interval_seconds':300,'retention_days':1}
    service.save_plan(901,data,ident,'current','apply technical capacities now')
    assert service.subscription(902)['limits']['servers']==1
    assert service.subscription(902)['snapshot']['policy']['retention_days']==1
    original=service.db.execute('SELECT snapshot FROM sonar_orders WHERE user_id=902 AND idempotency_key=%s',('pricing-original',),fetch='one')['snapshot']
    assert original['price']==100 and original['limits']['servers']==2 and original['policy']['retention_days']==30
