"""Bounded per-plan metric retention; never deletes financial or audit records."""

def sweep(db):
    with db.get_connection() as (conn,cur):
        cur.execute('SELECT pg_try_advisory_xact_lock(535325) locked')
        if not cur.fetchone()['locked']:conn.commit();return 0
        cur.execute("INSERT INTO pro_job_cursors(name,last_id) VALUES('plan_retention',0) ON CONFLICT DO NOTHING")
        cur.execute("SELECT last_id FROM pro_job_cursors WHERE name='plan_retention' FOR UPDATE");last=cur.fetchone()['last_id']
        cur.execute('''SELECT user_id,sonar_retention_days(user_id) days FROM sonar_subscriptions
            WHERE user_id>%s AND sonar_retention_days(user_id)<30 ORDER BY user_id LIMIT 10''',(last,));owners=cur.fetchall();removed=0
        for owner in owners:
            uid,days=owner['user_id'],owner['days']
            for table,stamp in (('pro_metrics','measured_at'),('pro_service_history','checked_at'),('sonar_service_regions','checked_at')):
                cur.execute(f'''DELETE FROM {table} WHERE id IN (SELECT id FROM {table}
                    WHERE owner_id=%s AND {stamp}<NOW()-(%s*INTERVAL '1 day') ORDER BY id LIMIT 10000)''',(uid,days));removed+=cur.rowcount
            cur.execute('''DELETE FROM server_stats WHERE ctid IN(SELECT t.ctid FROM server_stats t
                JOIN servers s ON s.id=t.server_id WHERE s.owner_id=%s AND t.created_at<NOW()-(%s*INTERVAL '1 day') LIMIT 10000)''',(uid,days));removed+=cur.rowcount
            cur.execute('''DELETE FROM sonar_hosting_history WHERE id IN(SELECT h.id FROM sonar_hosting_history h
                JOIN sonar_hosting_agents a ON a.id=h.agent_id WHERE a.owner_id=%s AND h.created_at<NOW()-(%s*INTERVAL '1 day') LIMIT 10000)''',(uid,days));removed+=cur.rowcount
            cur.execute("DELETE FROM sonar_config_probes WHERE owner_id=%s AND state IN ('succeeded','failed') AND completed_at<NOW()-(%s*INTERVAL '1 day')",(uid,days))
        cur.execute("UPDATE pro_job_cursors SET last_id=%s,updated_at=NOW() WHERE name='plan_retention'",(owners[-1]['user_id'] if owners else 0,))
        conn.commit();return removed
