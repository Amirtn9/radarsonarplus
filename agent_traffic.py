"""Auditable sampled Agent usage; never label host-wide NIC traffic as Agent use.

All totals reflect actual WebSocket frame bytes including the SSH relay,
not Xray's separate outbound downloads, retransmits, TLS framing or other
network processes. A sample may be lost if an Agent restarts between polls.
"""
from __future__ import annotations
import re


def install_schema(db):
    with db.get_connection() as (conn, cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS monitor_agent_meter (
          agent_id INTEGER PRIMARY KEY REFERENCES agent_nodes(id) ON DELETE CASCADE,
          boot_id TEXT NOT NULL, tx_total BIGINT NOT NULL DEFAULT 0,
          rx_total BIGINT NOT NULL DEFAULT 0, test_total BIGINT NOT NULL DEFAULT 0, sampled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          reset_epoch BIGINT NOT NULL DEFAULT 0, reset_at TIMESTAMPTZ
        )''')
        cur.execute('ALTER TABLE monitor_agent_meter ADD COLUMN IF NOT EXISTS test_total BIGINT NOT NULL DEFAULT 0')
        cur.execute('''CREATE TABLE IF NOT EXISTS monitor_agent_meter_daily (
          agent_id INTEGER NOT NULL REFERENCES agent_nodes(id) ON DELETE CASCADE,
          reset_epoch BIGINT NOT NULL, day DATE NOT NULL,
          tx_bytes BIGINT NOT NULL DEFAULT 0, rx_bytes BIGINT NOT NULL DEFAULT 0,
          test_bytes BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(agent_id,reset_epoch,day)
        )''')
        cur.execute('ALTER TABLE monitor_agent_meter_daily ADD COLUMN IF NOT EXISTS test_bytes BIGINT NOT NULL DEFAULT 0')
        conn.commit()


def validated_usage(stats):
    if not isinstance(stats,dict):return None
    obj=stats.get('agent_usage')
    if not isinstance(obj,dict) or obj.get('scope')!='agent_wss_relay_and_test_payloads':return None
    boot=obj.get('instance')
    if not isinstance(boot,str) or not re.fullmatch('[a-f0-9]{32}',boot):return None
    rx,tx,down=obj.get('rx_bytes'),obj.get('tx_bytes'),obj.get('download_bytes')
    if any(type(v) is not int or v<0 or v>2**63-1 for v in (rx,tx,down)):return None
    return boot,tx,rx,down


def record_sample(db,server_id,stats):
    """Delta upsert in one PostgreSQL transaction, keyed to physical Agent ID."""
    parsed=validated_usage(stats)
    if not parsed:return False
    boot,tx,rx,down=parsed
    with db.get_connection() as (conn,cur):
        cur.execute('''SELECT a.id FROM agent_nodes a JOIN servers s ON s.agent_id=a.id
              WHERE s.id=%s AND s.is_active=1 AND a.agent_cert_pem<>''
              FOR UPDATE OF a''',(int(server_id),))
        row=cur.fetchone()
        if not row:
            conn.rollback();return False
        agent_id=int(row['id'])
        cur.execute('SELECT * FROM monitor_agent_meter WHERE agent_id=%s FOR UPDATE',(agent_id,))
        prev=cur.fetchone()
        if prev:
            same=prev['boot_id']==boot and tx>=prev['tx_total'] and rx>=prev['rx_total'] and down>=prev['test_total']
            txdelta=tx-int(prev['tx_total']) if same else tx
            rxdelta=rx-int(prev['rx_total']) if same else rx
            downdelta=down-int(prev['test_total']) if same else down
            epoch=int(prev['reset_epoch'])
            cur.execute('''UPDATE monitor_agent_meter SET boot_id=%s,tx_total=%s,
                         rx_total=%s,test_total=%s,sampled_at=NOW() WHERE agent_id=%s''',(boot,tx,rx,down,agent_id))
        else:
            txdelta,rxdelta,downdelta,epoch=tx,rx,down,0
            cur.execute('''INSERT INTO monitor_agent_meter
                 (agent_id,boot_id,tx_total,rx_total,test_total) VALUES(%s,%s,%s,%s,%s)''',
                 (agent_id,boot,tx,rx,down))
        # Agent samples may be repeated by multiple registered server aliases.
        if txdelta or rxdelta or downdelta:
            cur.execute('''INSERT INTO monitor_agent_meter_daily(agent_id,reset_epoch,day,tx_bytes,rx_bytes,test_bytes)
                 VALUES(%s,%s,CURRENT_DATE,%s,%s,%s)
                 ON CONFLICT(agent_id,reset_epoch,day) DO UPDATE
                 SET tx_bytes=monitor_agent_meter_daily.tx_bytes+EXCLUDED.tx_bytes,
                     rx_bytes=monitor_agent_meter_daily.rx_bytes+EXCLUDED.rx_bytes,
                     test_bytes=monitor_agent_meter_daily.test_bytes+EXCLUDED.test_bytes''',
                 (agent_id,epoch,txdelta,rxdelta,downdelta))
        conn.commit()
    return True


def admin_report(db,admin_id):
    if not admin_id:raise PermissionError('Admin role is not configured')
    # The caller must independently check the actor matches configured admin.
    rows=db.execute('''SELECT a.id AS agent_id, s.id AS server_id,s.name,s.owner_id,
      a.ip,a.ws_port,m.sampled_at,m.reset_at,
      COALESCE((SELECT SUM(x.tx_bytes+x.rx_bytes+x.test_bytes) FROM monitor_agent_meter_daily x
        WHERE x.agent_id=a.id AND x.reset_epoch=COALESCE(m.reset_epoch,0)),0) AS total,
      COALESCE((SELECT SUM(x.tx_bytes+x.rx_bytes+x.test_bytes) FROM monitor_agent_meter_daily x
        WHERE x.agent_id=a.id AND x.reset_epoch=COALESCE(m.reset_epoch,0)
        AND x.day=CURRENT_DATE),0) AS today,
      COALESCE((SELECT SUM(x.tx_bytes+x.rx_bytes+x.test_bytes) FROM monitor_agent_meter_daily x
        WHERE x.agent_id=a.id AND x.reset_epoch=COALESCE(m.reset_epoch,0)
        AND x.day >= CURRENT_DATE-6),0) AS week,
      COALESCE((SELECT SUM(x.tx_bytes+x.rx_bytes+x.test_bytes) FROM monitor_agent_meter_daily x
        WHERE x.agent_id=a.id AND x.reset_epoch=COALESCE(m.reset_epoch,0)
        AND x.day >= CURRENT_DATE-29),0) AS month
      FROM agent_nodes a
      JOIN LATERAL (SELECT id,name,owner_id FROM servers WHERE agent_id=a.id
        ORDER BY id LIMIT 1) s ON TRUE
      LEFT JOIN monitor_agent_meter m ON m.agent_id=a.id ORDER BY s.id LIMIT 250''',fetch='all')
    return [dict(x) for x in rows]


def reset_meter(db,admin_id,agent_id):
    if not admin_id:raise PermissionError('Admin role is not configured')
    with db.get_connection() as (conn,cur):
        cur.execute('''UPDATE monitor_agent_meter SET reset_epoch=reset_epoch+1,reset_at=NOW()
          WHERE agent_id=%s RETURNING reset_epoch''',(int(agent_id),))
        row=cur.fetchone()
        if not row:raise ValueError('AGENT_USAGE_HAS_NO_SAMPLES')
        # Keep historical epochs for audit instead of deleting traffic evidence.
        cur.execute('''INSERT INTO monitor_agent_meter_daily(agent_id,reset_epoch,day)
                 VALUES(%s,%s,CURRENT_DATE) ON CONFLICT DO NOTHING''',(int(agent_id),row['reset_epoch']))
        conn.commit()
        return int(row['reset_epoch'])
