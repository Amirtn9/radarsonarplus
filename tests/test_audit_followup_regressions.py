"""Stress and failure scenarios requested in the independent audit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import importlib.util
from pathlib import Path
import shutil
import sys
import pytest
import psycopg2
from psycopg2.extras import Json
from test_billing_workflow import service,plan
from test_postgres_workflow import isolated_database
from user_errors import UserError

ROOT=Path(__file__).resolve().parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_release_check_detects_a_stale_security_label(tmp_path):
    validator=module('check_release')
    for name in ('VERSION',*validator.LABELS):
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    assert validator.check(tmp_path)==[]
    (tmp_path/'SECURITY.md').write_text('**Release:** `6.9.6`')
    assert validator.check(tmp_path)==['SECURITY.md: release label must match VERSION (6.9.10)']


def test_repository_map_check_is_read_only_and_detects_new_files(tmp_path,monkeypatch):
    structure=module('update_project_structure')
    monkeypatch.setattr(structure,'ROOT',tmp_path);monkeypatch.setattr(structure,'OUTPUT',tmp_path/'PROJECT_STRUCTURE.md')
    (tmp_path/'app.py').write_text('pass')
    monkeypatch.setattr(sys,'argv',['map']);assert structure.main()==0
    before=structure.OUTPUT.read_text()
    monkeypatch.setattr(sys,'argv',['map','--check']);assert structure.main()==0
    (tmp_path/'new.css').write_text('body{}')
    assert structure.main()==1 and structure.OUTPUT.read_text()==before
    monkeypatch.setattr(sys,'argv',['map']);assert structure.main()==0
    monkeypatch.setattr(sys,'argv',['map','--check']);assert structure.main()==0


def test_directory_budget_stays_an_explicit_independent_check(tmp_path,monkeypatch):
    structure=module('update_project_structure')
    monkeypatch.setattr(structure,'ROOT',tmp_path);monkeypatch.setattr(structure,'OUTPUT',tmp_path/'PROJECT_STRUCTURE.md')
    monkeypatch.setattr(structure,'DIRECT_FILE_LIMIT',1)
    (tmp_path/'app.py').write_text('pass')
    monkeypatch.setattr(sys,'argv',['map']);assert structure.main()==0
    monkeypatch.setattr(sys,'argv',['map','--check-limits']);assert structure.main()==1
    assert 'Maximum direct files in one directory: **2**' in structure.OUTPUT.read_text()


def limited(service,limits):
    ident=plan(service,0)
    service.db.execute('UPDATE sonar_plans SET consumption=%s WHERE id=%s',(Json(limits),ident))
    service.checkout(902,ident,'wallet','audit-limited-subscription')


def test_one_hundred_parallel_consumers_cannot_exceed_daily_limit(service):
    from consumption import consume,usage
    limited(service,{'api_daily':10})
    def request(index):
        try:consume(service.db,902,'api_daily',f'operation-{index}');return True
        except UserError:return False
    with ThreadPoolExecutor(max_workers=8) as pool:assert sum(pool.map(request,range(100)))==10
    assert usage(service.db,902)==[{'kind':'api_daily','used':10}]


def test_one_hundred_parallel_replays_consume_once(service):
    from consumption import consume,usage
    limited(service,{'notifications_daily':1})
    def request(_):consume(service.db,902,'notifications_daily','same-event')
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(request,range(100)))
    assert usage(service.db,902)==[{'kind':'notifications_daily','used':1}]


def test_consumption_event_write_failure_rolls_back_counter(service):
    from consumption import consume,usage
    limited(service,{'notifications_daily':1})
    service.db.execute("""CREATE FUNCTION reject_consumption() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'injected event write failure'; END $$;
        CREATE TRIGGER reject_consumption BEFORE INSERT ON sonar_consumption_events
        FOR EACH ROW EXECUTE FUNCTION reject_consumption()""")
    with pytest.raises(psycopg2.errors.RaiseException,match='injected event write failure'):
        consume(service.db,902,'notifications_daily','attempt')
    assert usage(service.db,902)==[]


def test_expired_probe_slot_is_reclaimed_and_plan_change_does_not_reset_usage(service):
    from consumption import start_probe,finish_probe,usage
    limited(service,{'config_tests_concurrent':1,'config_tests_daily':3})
    first=start_probe(service.db,902)
    service.db.execute("UPDATE sonar_probe_slots SET expires_at=NOW()-INTERVAL '1 second' WHERE id=%s",(first,))
    second=start_probe(service.db,902)
    assert first!=second
    service.db.execute("UPDATE sonar_subscriptions SET snapshot=jsonb_set(snapshot,'{consumption}',%s::jsonb) WHERE user_id=902",(Json({'config_tests_concurrent':0,'config_tests_daily':3}),))
    with pytest.raises(UserError):start_probe(service.db,902)
    finish_probe(service.db,second)
    assert usage(service.db,902)==[{'kind':'config_tests_daily','used':2}]


def test_concurrent_manual_approval_activates_once_and_keeps_destination_snapshot(service):
    ident=plan(service,100)
    data=dict(kind='card',name='Before',network='',address='synthetic-address-before',holder='',instructions='',enabled=True)
    destination=service.save_destination(901,data,reason='original destination')
    order=service.checkout(902,ident,'manual','audit-manual-order',destination)
    service.save_destination(901,{**data,'address':'synthetic-address-after'},destination,'change destination')
    service.evidence(902,order['id'],'synthetic payment proof')
    def approve(_):return service.decide(901,order['id'],True,'independent payment review')
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(approve,range(2)))==[{'status':'paid'}]*2
    subscription=service.subscription(902)
    remaining=(subscription['expires_at']-datetime.now(timezone.utc)).total_seconds()
    assert timedelta(days=29).total_seconds()<remaining<=timedelta(days=30).total_seconds()
    assert service.db.execute('SELECT destination_snapshot FROM sonar_orders WHERE id=%s',(order['id'],),fetch='one')['destination_snapshot']['address']=='synthetic-address-before'


def test_checkout_failure_after_activation_rolls_back_money_order_and_subscription(service):
    ident=plan(service,100);service.adjust_wallet(901,902,200,'audit-rollback-fund','fixture funds')
    service.db.execute("""CREATE FUNCTION reject_checkout() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.event='order.create' THEN RAISE EXCEPTION 'injected checkout failure'; END IF;
        RETURN NEW; END $$;
        CREATE TRIGGER reject_checkout BEFORE INSERT ON sonar_billing_audit
        FOR EACH ROW EXECUTE FUNCTION reject_checkout()""")
    with pytest.raises(psycopg2.errors.RaiseException,match='injected checkout failure'):
        service.checkout(902,ident,'wallet','must-roll-back-all')
    assert service.db.get_user(902)['wallet_balance']==200
    assert service.subscription(902) is None
    for table in ('sonar_orders','sonar_billing_messages'):
        assert service.db.execute(f'SELECT COUNT(*) n FROM {table}',fetch='one')['n']==0
    assert service.db.execute('SELECT COUNT(*) n FROM sonar_wallet_ledger',fetch='one')['n']==1
