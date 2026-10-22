from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import monitor_fleet


class FakeConn:
    def commit(self): pass


class ConsentCursor:
    def __init__(self, owner_id=222, offered=False):
        self.owner_id=owner_id; self.offered=offered; self.result=None; self.statements=[]
    def execute(self, query, params=()):
        self.statements.append((query,params))
        if 'SELECT s.id,s.owner_id FROM servers s JOIN agent_nodes' in query:
            self.result={'id':7,'owner_id':self.owner_id}
        elif 'SELECT 1 FROM monitor_fleet_nodes WHERE server_id' in query:
            self.result={'ok':1} if self.offered else None
        elif 'SELECT s.id,s.owner_id,f.offered_by FROM servers s' in query:
            self.result={'id':7,'owner_id':self.owner_id,'offered_by':self.owner_id if self.offered else None}
        else:
            self.result=None
    def fetchone(self): return self.result


class FakeDB:
    def __init__(self, owner_id=222, offered=False):
        self.cursor=ConsentCursor(owner_id,offered); self.conn=FakeConn()
    def get_connection(self):
        class Context:
            def __enter__(inner): return self.conn,self.cursor
            def __exit__(inner,*args): return False
        return Context()


def test_foreign_agent_cannot_be_approved_without_owner_opt_in():
    db=FakeDB(owner_id=222,offered=False)
    with pytest.raises(PermissionError,match='explicitly offer'):
        monitor_fleet.approve(db,admin_id=1,server_id=7,enabled=True)


def test_foreign_agent_can_be_approved_after_owner_opt_in():
    db=FakeDB(owner_id=222,offered=True)
    monitor_fleet.approve(db,admin_id=1,server_id=7,enabled=True)
    assert db.conn is not None
    assert 'ON CONFLICT (server_id)' in db.cursor.statements[-1][0]
    assert db.cursor.statements[-1][1][-1] is False  # user-owned, opted-in nodes are shareable


def test_admin_owned_agent_remains_private_by_default():
    db=FakeDB(owner_id=1,offered=False)
    monitor_fleet.approve(db,admin_id=1,server_id=7,enabled=True)
    assert db.cursor.statements[-1][1][-1] is True


def test_relay_failover_retries_agent_transport_but_not_destination_security_errors():
    from ssh_relay import retryable_relay_error
    assert retryable_relay_error(PermissionError('RELAY_AGENT_NOT_ENROLLED'),'AUTH_FAILED') is True
    assert retryable_relay_error(ConnectionError('RELAY_NOT_READY'),'SSH_CONNECTION_FAILED') is True
    assert retryable_relay_error(PermissionError('HOST_KEY_CHANGED'),'HOST_KEY_CHANGED') is False
    assert retryable_relay_error(PermissionError('Authentication failed'),'AUTH_FAILED') is False
