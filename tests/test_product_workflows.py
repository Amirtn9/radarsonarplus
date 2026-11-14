"""Acceptance tests against isolated PostgreSQL for product management workflows."""
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_postgres_workflow import isolated_database
from test_billing_workflow import service,plan
from product_admin import Product,finance_csv
from user_errors import UserError


def test_plan_schedule_draft_archive_and_purchase_enforcement(service):
    ident=plan(service)
    data=dict(service.plans(901,True)[-1]);data={k:data[k] for k in ('name','description','price','days','limits','features','policy','enabled','priority')}
    data['publish_at']=datetime.now(timezone.utc)+timedelta(days=1)
    service.save_plan(901,data,ident,reason='scheduled launch')
    assert ident not in [p['id'] for p in service.plans(902)]
    with pytest.raises(UserError):service.checkout(902,ident,'wallet','future-checkout-01')
    data['publish_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    data['enabled']=False;service.save_plan(901,data,ident,reason='save as draft')
    assert ident not in [p['id'] for p in service.plans(902)]
    data['enabled']=True;service.save_plan(901,data,ident,reason='publish launch')
    service.adjust_wallet(901,902,200,'product-launch-fund','fund launch test')
    order=service.checkout(902,ident,'wallet','launched-order01')
    Product(service.db,901).archive(901,ident,'retire published plan')
    assert ident not in [p['id'] for p in service.plans(902)]
    assert service.account(902)['orders'][0]['status']=='paid'
    assert service.subscription(902)['snapshot']['price']==order['amount']
    with pytest.raises(UserError):service.checkout(902,ident,'wallet','archived-order01')


def test_report_filters_csv_and_actual_money_are_consistent(service):
    ident=plan(service);service.adjust_wallet(901,902,500,'product-reports-fund','fund report')
    a=service.checkout(902,ident,'wallet','product-report01')
    service.checkout(902,ident,'wallet','product-report02')
    service.refund(901,str(a['id']),'refunding first purchase')
    product=Product(service.db,901)
    from workspace_service import ACTOR_ID
    token=ACTOR_ID.set(901)
    try:report=product.finances(901,user_id=902,plan_id=ident,method='wallet')
    finally:ACTOR_ID.reset(token)
    assert report['summary']['gross_sales']==200
    assert report['summary']['net_sales']==100
    assert report['summary']['wallet_refunds']==100
    assert sum(r['orders'] for r in report['rows'])==2
    assert sum(r['renewals'] for r in report['rows'])==1
    assert product.finances(901,user_id=903)['summary']['orders']==0
    assert product.finances(901,method='manual')['summary']['orders']==0
    output=finance_csv(report)
    assert 'amount_IRR' in output and '\ufeff'==output[0]
    assert len(product.orders(901,status='refunded'))==1
    assert not product.orders(901,q='unknown-id')
    with pytest.raises(UserError):product.finances(901,datetime.now(),datetime.now()+timedelta(days=1))
    with pytest.raises(UserError):product.finances(901,datetime.now(timezone.utc)-timedelta(days=400))
    with pytest.raises(PermissionError):product.finances(902)


def test_dossier_contains_only_explicit_resource_fields_and_permissions(service):
    service.db.execute("INSERT INTO servers(owner_id,name,ip,username,password) VALUES(902,'Customer VPS','192.0.2.1','private-name','encrypted-secret')")
    service.staff(901,903,'Support',['users.read'],True,'limited support')
    product=Product(service.db,901)
    data=product.dossier(903,902)
    assert data['resources']['servers'][0]['name']=='Customer VPS'
    assert 'password' not in data['resources']['servers'][0]
    assert 'username' not in data['resources']['servers'][0]
    assert 'messages' not in data and 'activity' not in data
    assert 'today' not in product.overview(903)
    assert 'servers' not in product.overview(903)
    assert product.overview(901)['users']['total']==3
    with pytest.raises(PermissionError):product.dossier(902,903)


def test_bulk_messages_are_atomic_idempotent_and_private(service):
    product=Product(service.db,901)
    def send():return product.bulk_message(901,[903,902,902],'Private customer notice','product-batch-001','notifying selected customers')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:send(),range(2)))
    assert results[0]==results[1] and len(results[0])==2
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_billing_messages',fetch='one')['n']==2
    with pytest.raises(UserError):product.bulk_message(901,[902],'Changed','product-batch-001','changed request')
    with pytest.raises(UserError):product.bulk_message(901,[902,999999],'Notice','bad-batch-001','unknown recipient')
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_billing_messages',fetch='one')['n']==2
    with pytest.raises(PermissionError):product.bulk_message(902,[903],'Notice','no-access-001','unauthorized request')


def test_user_saved_filters_and_real_plan_usage(service):
    ident=plan(service);service.manage(901,902,'active',3,reason='test grant',plan_id=ident)
    product=Product(service.db,901)
    assert len(product.users(901,filter='expiring'))==1
    assert product.users(901,q='Buyer')[0]['user_id']==902
    assert len(product.users(901,filter='zero_wallet'))==3
    assert product.plan_usage(901,ident)[0]['user_id']==902
    with pytest.raises(UserError):product.users(901,filter='unsafe SQL')


def test_update_preserves_configured_web_port_in_generated_unit_and_health(tmp_path):
    import subprocess
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'install.sh').read_text()
    function='install_web() {'+source.split('install_web() {',1)[1].split('set_domain() {',1)[0]
    # Run the real installer function with all OS/service effects replaced locally.
    harness='''set -e
require_root(){ :; }; require_install(){ :; }; say(){ :; }; ok(){ :; };
read_env(){ test "$1" = SONAR_WEB_PORT; printf 8091; }
valid_port(){ test "$1" = 8091; }
runuser(){ :; }; systemctl(){ :; }; systemd-analyze(){ :; }
create_web_unit(){ test "$1" = 8091; printf 'unit:%s\\n' "$1"; }
wait_for_web_health(){ test "$1" = 8091; printf 'health:%s\\n' "$1"; }
die(){ printf '%s\\n' "$*" >&2; exit 1; }
APP_USER=fixture;APP_DIR=/nonexistent;STATE_DIR=/nonexistent;LOG_DIR=/nonexistent
WEB_UNIT=/nonexistent;WEB_SERVICE=fixture;INSTALL_LOG=/dev/null
'''+function+'\ninstall_web\n'
    result=subprocess.run(['bash','-c',harness],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert 'unit:8091' in result.stdout and 'health:8091' in result.stdout


def test_product_api_checks_role_and_rejects_naive_dates(service):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pro_store import ProStore
    from product_api import router
    from fastapi import HTTPException
    current=[901]
    async def actor():return current[0]
    async def guarded(fn,*args,**kwargs):
        try:return await asyncio.to_thread(fn,*args,**kwargs)
        except PermissionError:raise HTTPException(403,'denied')
        except UserError as error:raise HTTPException(400,str(error))
    app=FastAPI();app.include_router(router(ProStore(service.db),actor,guarded,901));client=TestClient(app)
    assert client.get('/api/product/overview').status_code==200
    assert client.get('/api/product/finances?start=2026-01-01T00:00:00&end=2026-01-02T00:00:00').status_code==400
    assert client.get('/api/product/finances?format=csv').headers['content-type'].startswith('text/csv')
    assert client.post('/api/product/messages/batch',json={'text':'x','user_ids':[902],'request_key':'short','reason':'short'}).status_code==422
    service.staff(901,903,'Support',['users.read'],True,'scoped customer support')
    current[0]=903
    assert client.get('/api/product/users/902').status_code==200
    assert client.get('/api/product/finances').status_code==403
    assert client.get('/api/product/orders').status_code==403
    assert client.post('/api/product/messages/batch',json={'text':'Do not send','user_ids':[902],'request_key':'forbidden-01','reason':'must deny access'}).status_code==403
    current[0]=902
    assert client.get('/api/product/users/903').status_code==403
    assert client.get('/api/product/search?q=Admin').json()==[]


def test_bot_customer_operations_recheck_capability_before_commit(service,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import billing_bot
    monkeypatch.setattr(billing_bot,'service',lambda:service)
    reply=AsyncMock();update=SimpleNamespace(effective_message=SimpleNamespace(text='Explained manual credit',reply_text=reply))
    flow={'step':'reason','action':'wallet','target':902,'value':15,'key':'bot-product-funding01'}
    context=SimpleNamespace(user_data={'billing_manage':flow})
    asyncio.run(billing_bot.manage_input(update,context,901,flow))
    assert service.db.get_user(902)['wallet_balance']==150
    service.staff(901,903,'Read only',['users.read'],True,'readonly operator')
    with pytest.raises(PermissionError):asyncio.run(billing_bot.manage_input(update,context,903,flow))
    assert service.db.get_user(902)['wallet_balance']==150


def assign_consumption(service,limits):
    ident=plan(service)
    data={k:v for k,v in service.plans(901,True)[-1].items() if k in ('name','description','price','days','limits','features','policy','enabled','priority')}
    data['consumption']=limits
    service.save_plan(901,data,ident,reason='set real consumption limits')
    service.manage(901,902,'active',30,reason='grant limited plan',plan_id=ident)
    return ident


def test_daily_consumption_is_atomic_idempotent_and_optional(service):
    from consumption import consume,usage
    assign_consumption(service,{'api_daily':1,'notifications_daily':1})
    def call(_):
        try:consume(service.db,902,'api_daily');return True
        except UserError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sum(pool.map(call,range(2)))==1
    consume(service.db,902,'notifications_daily','same-event')
    consume(service.db,902,'notifications_daily','same-event')
    with pytest.raises(UserError):consume(service.db,902,'notifications_daily','different-event')
    assert {r['kind']:r['used'] for r in usage(service.db,902)}=={'api_daily':1,'notifications_daily':1}
    # No explicit plan limit retains the existing technical limit, not a hidden zero.
    consume(service.db,903,'api_daily')
    assert usage(service.db,903)==[]


def test_plan_test_slots_and_daily_attempts_share_a_transaction(service):
    from consumption import start_probe,finish_probe,usage
    assign_consumption(service,{'config_tests_concurrent':1,'config_tests_daily':2})
    first=start_probe(service.db,902)
    with pytest.raises(UserError):start_probe(service.db,902)
    assert usage(service.db,902)[0]['used']==1
    finish_probe(service.db,first)
    second=start_probe(service.db,902);finish_probe(service.db,second)
    with pytest.raises(UserError):start_probe(service.db,902)
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_probe_slots WHERE owner_id=902',fetch='one')['n']==0
    assert usage(service.db,902)[0]['used']==2


def test_common_probe_transport_enforces_plan_without_contacting_agent(service,monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import core,database
    assign_consumption(service,{'config_tests_daily':0})
    monkeypatch.setattr(database,'Database',lambda:service.db)
    real=AsyncMock(return_value=(True,{'status':'OK'}))
    monkeypatch.setattr(core.ServerMonitor,'_ws_test_config_unmetered',real)
    with pytest.raises(UserError):asyncio.run(core.ServerMonitor.ws_test_config('192.0.2.1',9090,None,'vless://synthetic',owner_id=902))
    real.assert_not_awaited()


def test_consumption_change_policy_preserves_existing_until_current_apply(service):
    ident=assign_consumption(service,{'reports_daily':2})
    data={k:v for k,v in service.plans(901,True)[-1].items() if k in ('name','description','price','days','limits','features','policy','enabled','priority')}
    data['consumption']={'reports_daily':0}
    service.save_plan(901,data,ident,apply='renewal',reason='next renewal budget')
    assert service.subscription(902)['snapshot']['consumption']['reports_daily']==2
    service.save_plan(901,data,ident,apply='current',reason='apply current budget')
    assert service.subscription(902)['snapshot']['consumption']['reports_daily']==0
    from consumption import consume
    with pytest.raises(UserError):consume(service.db,902,'reports_daily')


def test_notification_budget_never_sends_when_zero_but_financial_queue_stays_separate(service,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import notifications
    assign_consumption(service,{'notifications_daily':0})
    bot=SimpleNamespace(send_message=AsyncMock())
    with pytest.raises(UserError):asyncio.run(notifications.send_notification(SimpleNamespace(bot=bot),902,'technical event','down',db=service.db,event_key='test-blocked-event'))
    bot.send_message.assert_not_awaited()
    service.message(901,902,'A financial private notice','unlimited-finance-msg','manual private notice')
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_billing_messages WHERE user_id=902',fetch='one')['n']==1


def test_operational_export_uses_report_budget(service):
    import asyncio
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from pro_store import ProStore
    from web_reporting import router
    assign_consumption(service,{'reports_daily':0})
    async def actor():return 902
    async def guarded(fn,*args,**kwargs):
        try:return await asyncio.to_thread(fn,*args,**kwargs)
        except UserError as error:raise HTTPException(400,str(error))
    app=FastAPI();app.include_router(router(ProStore(service.db),actor,guarded));client=TestClient(app)
    assert client.get('/api/reports/overview').status_code==200
    assert client.get('/api/reports/servers.csv').status_code==400
    assert client.get('/api/reports/snapshot.json').status_code==400


def test_older_plan_editor_preserves_new_consumption_and_publication_fields(service):
    ident=assign_consumption(service,{'reports_daily':2})
    stored=next(p for p in service.plans(901,True) if p['id']==ident)
    fields=('name','description','price','days','limits','features','policy','enabled','priority')
    data={k:stored[k] for k in fields}
    launch=datetime.now(timezone.utc)+timedelta(days=1)
    service.save_plan(901,{**data,'consumption':{'reports_daily':2},'publish_at':launch},ident,reason='scheduled next launch')
    service.save_plan(901,{**data,'description':'edited from older client'},ident,reason='older compatible editor')
    latest=next(p for p in service.plans(901,True) if p['id']==ident)
    assert latest['consumption']=={'reports_daily':2}
    assert latest['publish_at']==launch


def test_returning_to_billing_home_cancels_pending_sensitive_input(service,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import billing_bot
    monkeypatch.setattr(billing_bot,'service',lambda:service)
    context=SimpleNamespace(user_data={'billing_manage':{'step':'reason','action':'wallet','target':902,'value':100,'key':'must-not-run'},'billing_evidence':'old-order'})
    update=SimpleNamespace(callback_query=None,effective_user=SimpleNamespace(id=901),effective_message=SimpleNamespace(reply_text=AsyncMock(),text='Unrelated text'))
    asyncio.run(billing_bot.home(update,context))
    asyncio.run(billing_bot.receipt(update,context))
    assert service.db.get_user(902)['wallet_balance']==0
    assert not context.user_data
