"""Additive subscription receipt/status records independent of monitoring Agent.

Subscription source entries remain in tunnel_configs; this table only describes
recent refresh attempts. Neither failed WSS nor test failures delete membership.
"""
from __future__ import annotations

STATES={'saved','fetching','testing','ready','waiting_agent','failed'}


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_subscription_refresh(
           owner_id BIGINT NOT NULL,
           subscription_id INTEGER NOT NULL REFERENCES tunnel_configs(id) ON DELETE CASCADE,
           status TEXT NOT NULL DEFAULT 'saved',
           started_at TIMESTAMPTZ,
           completed_at TIMESTAMPTZ,
           last_success_at TIMESTAMPTZ,
           last_error_code TEXT,
           extracted_count INTEGER,
           testable_count INTEGER,
           healthy_count INTEGER,
           PRIMARY KEY(owner_id,subscription_id))''')
        conn.commit()


def mark(db,owner,sub_id,status,extracted=None,healthy=None,error=None):
    if status not in STATES:raise ValueError('Invalid subscription refresh state')
    # Only machine-safe error identifiers, never config links or remote response.
    if error and (len(error)>60 or not error.replace('_','').isalnum()):
        error='SUB_REFRESH_FAILED'
    with db.get_connection() as (conn,cur):
        cur.execute('''INSERT INTO sonar_subscription_refresh(owner_id,subscription_id,status,
                 started_at,completed_at,last_success_at,last_error_code,extracted_count,testable_count,healthy_count)
              SELECT %s,s.id,%s,
                 CASE WHEN %s='fetching' THEN NOW() END,
                 CASE WHEN %s IN ('ready','failed','waiting_agent') THEN NOW() END,
                 CASE WHEN %s='ready' THEN NOW() END,
                 %s,%s,%s,%s
              FROM tunnel_configs s WHERE s.id=%s AND s.owner_id=%s AND s.type='sub_source'
              ON CONFLICT(owner_id,subscription_id) DO UPDATE SET
                 status=EXCLUDED.status,
                 started_at=CASE WHEN EXCLUDED.status='fetching' THEN NOW()
                       ELSE sonar_subscription_refresh.started_at END,
                 completed_at=CASE WHEN EXCLUDED.status IN ('ready','failed','waiting_agent')
                       THEN NOW() ELSE NULL END,
                 last_success_at=COALESCE(EXCLUDED.last_success_at,sonar_subscription_refresh.last_success_at),
                 last_error_code=EXCLUDED.last_error_code,
                 extracted_count=COALESCE(EXCLUDED.extracted_count,sonar_subscription_refresh.extracted_count),
                 testable_count=COALESCE(EXCLUDED.testable_count,sonar_subscription_refresh.testable_count),
                 healthy_count=COALESCE(EXCLUDED.healthy_count,sonar_subscription_refresh.healthy_count)''',
             (int(owner),status,status,status,status,error,extracted,extracted,healthy,int(sub_id),int(owner)))
        conn.commit()


def summaries(db,owner):
    return [dict(r) for r in db.execute('''SELECT s.id,s.name,s.added_at,
            COALESCE(r.status,'saved') AS refresh_status,
            r.started_at,r.completed_at,r.last_success_at,r.last_error_code,
            r.extracted_count,r.healthy_count,
            COUNT(c.id) AS stored_count,
            COUNT(c.id) FILTER (WHERE c.last_status='OK') AS tested_healthy_count
          FROM tunnel_configs s
          LEFT JOIN sonar_subscription_refresh r ON r.subscription_id=s.id AND r.owner_id=s.owner_id
          LEFT JOIN tunnel_configs c ON c.parent_sub_id=s.id AND c.owner_id=s.owner_id AND c.type='sub_item'
          WHERE s.owner_id=%s AND s.type='sub_source'
          GROUP BY s.id,r.status,r.started_at,r.completed_at,r.last_success_at,
                   r.last_error_code,r.extracted_count,r.healthy_count
          ORDER BY s.id DESC LIMIT 150''',(int(owner),),fetch='all')]
