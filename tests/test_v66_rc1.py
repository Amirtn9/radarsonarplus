"""Regression checks for v6.6 additive pool claims and authenticated health."""
from datetime import datetime, timezone
from monitor_fleet import claim_test_node, finish_test_node, valid_agent_stats, choose, score


def fresh(id,latency=40,cpu=20,queue=0):
    return dict(id=id,latency_ms=latency,cpu_pct=cpu,inflight=queue,
                enabled=True,approved_by=1,last_probe_ok=True,
                last_probe_at=datetime.now(timezone.utc))


def test_agent_health_requires_auth_peer_contract():
    assert valid_agent_stats({'status':'Online','cpu':45,
                              'capabilities':{'xray_ready':True}})
    for bad in (None,{}, {'status':'Online','cpu':8},
                {'status':'Online','cpu':8,'capabilities':{'xray_ready':False}},
                {'status':'Online','cpu':float('nan'), 'capabilities':{'xray_ready':True}},
                {'status':'Offline','cpu':8,'capabilities':{'xray_ready':True}}):
        assert not valid_agent_stats(bad)


def test_balancer_counts_reserved_work_and_actual_failures():
    healthy=fresh(1,45,20,queue=0)
    busy=fresh(2,40,20,queue=8)
    assert choose([healthy,busy],'balanced')['id']==1
    assert score(healthy,'load')<score(busy,'load')
    unreliable=dict(healthy,id=3,completed_tests=100,successful_tests=5)
    trusted=dict(healthy,id=4,completed_tests=100,successful_tests=98)
    assert choose([unreliable,trusted],'balanced')['id']==4


class FakeCursor:
    def __init__(self):
        self.queries=[];self.last='';self.count=0;self.deleted=False
    def execute(self,sql,args=None):self.last=sql;self.queries.append((sql,args))
    def fetchone(self):
        if 'SELECT strategy FROM monitor_fleet_policy' in self.last:return {'strategy':'balanced'}
        if 'SELECT server_id FROM monitor_fleet_nodes' in self.last:return {'server_id':5}
        if 'SELECT * FROM servers' in self.last:return {'id':5,'ip':'203.0.113.5','ws_port':25000}
        if 'DELETE FROM monitor_fleet_test_leases' in self.last:
            if self.deleted:return None
            self.deleted=True;return {'server_id':5}
        return None
    def fetchall(self):return [fresh(5)]

class FakeConnection:
    def __init__(self):self.cur=FakeCursor();self.committed=0;self.rolled_back=0
    def __enter__(self):return self,self.cur
    def __exit__(self,*args):pass
    def commit(self):self.committed+=1
    def rollback(self):self.rolled_back+=1

class FakeDB:
    def __init__(self):self.cn=FakeConnection()
    def workspace_owner(self,uid):return uid
    def get_connection(self):return self.cn


def test_lease_claims_once_releases_once():
    db=FakeDB()
    lease=claim_test_node(db,7,999)
    assert lease['id']==5 and len(lease['lease_id'])==32
    assert any('pg_advisory_xact_lock' in sql for sql,_ in db.cn.cur.queries)
    assert any('monitor_fleet_test_leases' in sql and 'INSERT INTO' in sql for sql,_ in db.cn.cur.queries)
    assert finish_test_node(db,5,True,lease['lease_id']) is True
    assert finish_test_node(db,5,True,lease['lease_id']) is False
    assert sum('completed_tests=completed_tests+1' in sql for sql,_ in db.cn.cur.queries)==1
    assert db.cn.committed==2
