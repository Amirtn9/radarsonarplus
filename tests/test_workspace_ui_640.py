"""Offline v6.4 shared-workspace and navigational invariants.

No live Telegram or PostgreSQL credentials are required to run these tests.
"""
from __future__ import annotations
import ast
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import pytest

from workspace_service import WorkspaceService, WorkspaceError, validate_ip, SCHEMA, AUDIT_SCHEMA, REDIRECT_SCHEMA

ROOT=Path(__file__).resolve().parents[1]


def test_literal_ip_rejects_urls_shell_tokens_and_invalid_values():
    assert validate_ip(' 1.1.1.1 ')=='1.1.1.1'
    assert validate_ip('2001:db8::1')=='2001:db8::1'
    for ip in ('127.0.0.1; reboot','https://example.com','example.com','1.1.1.999','', '127.0.0.1\nrm -rf /'):
        with pytest.raises(WorkspaceError):validate_ip(ip)


def test_schema_has_atomic_consent_and_change_tracking():
    assert 'sonar_workspace_requests' in SCHEMA
    assert 'CHECK(requester <> target)' in SCHEMA
    assert 'sonar_workspace_events' in AUDIT_SCHEMA
    assert "TG_OP = 'DELETE'" in AUDIT_SCHEMA or "TG_OP='DELETE'" in AUDIT_SCHEMA
    assert 'AFTER INSERT OR UPDATE OR DELETE' in AUDIT_SCHEMA
    assert 'FOR EACH ROW' in REDIRECT_SCHEMA
    assert 'BEFORE INSERT' in REDIRECT_SCHEMA
    for secret in ('NEW.password','OLD.password','NEW.agent_token','NEW.link ||'):
        assert secret not in AUDIT_SCHEMA


class Cursor:
    def __init__(self, approved=True):
        self.approved=approved
        self.commands=[]
        self.query=''
        self.rows=[]
        self.row=None
    def execute(self,sql,params=None):
        self.query=' '.join(sql.split())
        self.commands.append((self.query,params))
        q=self.query
        self.row=None;self.rows=[]
        if 'FROM sonar_workspace_requests WHERE id=%s FOR UPDATE' in q:
            self.row={'target':202,'requester':101,'state':'pending','expires_at':datetime.now(timezone.utc)+timedelta(hours=5)}
        elif 'to_regclass(' in q:
            self.row={'relation':None}
        elif 'FROM groups WHERE owner_id' in q and 'FOR UPDATE' in q:
            self.rows=[]
        elif 'FROM servers WHERE owner_id' in q and 'FOR UPDATE' in q:
            self.rows=[]
        elif 'FROM sonar_workspaces WHERE member_id=%s' in q:
            self.row=None
        elif 'FROM tunnel_configs a JOIN' in q:
            self.row=None
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


class DummyDB:
    def __init__(self):
        self.cursor=Cursor()
        self.committed=False
    @contextmanager
    def get_connection(self):
        db=self
        class Connection:
            def commit(self): db.committed=True
        yield Connection(),self.cursor


def test_only_recipient_can_approve_and_no_mutation_on_denial():
    db=DummyDB();service=WorkspaceService(db)
    with pytest.raises(WorkspaceError):service.decide(999,4,True)
    assert not db.committed
    assert not any('UPDATE servers' in sql for sql,_ in db.cursor.commands)
    db=DummyDB(); service=WorkspaceService(db)
    assert service.decide(202,4,False)==(101,False)
    assert db.committed
    assert not any('UPDATE servers' in sql for sql,_ in db.cursor.commands)


def test_approval_merges_resources_only_after_confirmation():
    db=DummyDB();service=WorkspaceService(db)
    assert service.decide(202,4,True)==(101,True)
    sqls='\n'.join(sql for sql,_ in db.cursor.commands)
    for table in ('groups','servers','settings','channels','tunnel_configs'):
        assert f'UPDATE {table} SET owner_id=%s' in sqls or (table=='settings' and 'INSERT INTO settings' in sqls)
    assert "state='accepted'" in sqls
    assert 'sonar_workspaces(member_id,owner_id)' in sqls
    assert db.committed


def test_in_place_ip_update_scoped_to_workspace_owner():
    class Db(DummyDB):
        def execute(self,query,params=(),fetch=None):
            if 'sonar_workspaces' in query:
                return {'owner_id':101}
        @contextmanager
        def get_connection(self):
            with super().get_connection() as (conn,cursor):
                original=cursor.execute
                def exec_(sql,params=None):
                    original(sql,params)
                    if 'SELECT id,name,ip FROM servers' in sql:
                        cursor.row={'id':77,'name':'test','ip':'1.1.1.1'}
                cursor.execute=exec_
                yield conn,cursor
    db=Db()
    assert WorkspaceService(db).change_server_ip(202,77,'8.8.8.8')==('test','1.1.1.1','8.8.8.8')
    assert ('8.8.8.8',77) in [params for q,params in db.cursor.commands if q.startswith('UPDATE servers SET ip=')]
    assert not any('agent_nodes' in query or 'ws_port' in query for query,_ in db.cursor.commands)


def test_beginner_menus_and_server_actions_have_handlers():
    v6=(ROOT/'v6_ui.py').read_text()
    keyboard=(ROOT/'keyboard.py').read_text()
    ui=(ROOT/'workspace_ui.py').read_text()
    for name in ('servers','configs','monitor','tools','settings','account','admin'):
        assert f"'{name}':" in v6
    for event in ('ui_team_yes_', 'ui_team_no_', 'ui_ip_save',
                  'ui_server_advanced_', 'ui_server_settings_','ui_delete_'):
        assert event in ui
    for call in ('ui_server_tools_','ui_server_settings_','ui_server_advanced_'):
        assert call in keyboard
    assert 'v6_settings' in keyboard
    assert "'v6_admin'" in keyboard


def test_version_consistent_and_agent_ws_sources_untouched_by_new_logic():
    version=(ROOT/'VERSION').read_text().strip()
    assert version=='6.5.3'
    assert version in (ROOT/'settings.py').read_text()
    assert 'ACTOR_ID' in (ROOT/'secure_handlers.py').read_text()
    assert 'copy_context' in (ROOT/'runtime.py').read_text()
    assert 'from workspace_service import workspace_alert_job' in (ROOT/'bot.py').read_text()
