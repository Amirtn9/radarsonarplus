"""Recorded incident transitions and independent SSH checks; never performs a probe."""
from __future__ import annotations

SSH_CODES={
    'SUDO_REQUIRED':'sudo_required','AUTH_FAILED':'auth_failed','HOST_KEY_UNKNOWN':'host_key_unknown','HOST_KEY_CHANGED':'host_key_changed',
    'SSH_PORT_UNREACHABLE':'network_failed','SSH_DNS_FAILED':'network_failed','SSH_TIMEOUT':'network_failed',
    'SSH_CONNECTION_REFUSED':'network_failed','SSH_CONNECTION_FAILED':'network_failed',
}


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS pro_incident_events(
            id BIGSERIAL PRIMARY KEY,incident_id BIGINT NOT NULL REFERENCES pro_incidents(id) ON DELETE CASCADE,
            owner_id BIGINT NOT NULL,event VARCHAR(20) NOT NULL,status VARCHAR(20) NOT NULL,
            severity VARCHAR(12) NOT NULL,occurrences INTEGER NOT NULL,actor_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())''')
        cur.execute('CREATE INDEX IF NOT EXISTS pro_incident_events_lookup ON pro_incident_events(owner_id,incident_id,id)')
        cur.execute('''CREATE OR REPLACE FUNCTION sonar_record_incident_event() RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE event_name TEXT;
            BEGIN
              IF TG_OP='INSERT' THEN event_name:='opened';
              ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
                event_name:=CASE NEW.status WHEN 'resolved' THEN 'resolved' WHEN 'acknowledged' THEN 'acknowledged' ELSE 'status_changed' END;
              ELSIF NEW.occurrences IS DISTINCT FROM OLD.occurrences THEN event_name:='repeated';
              ELSIF NEW.severity IS DISTINCT FROM OLD.severity OR NEW.title IS DISTINCT FROM OLD.title THEN event_name:='updated';
              ELSE RETURN NEW;
              END IF;
              INSERT INTO pro_incident_events(incident_id,owner_id,event,status,severity,occurrences,actor_id)
              VALUES(NEW.id,NEW.owner_id,event_name,NEW.status,NEW.severity,NEW.occurrences,
                     CASE WHEN event_name='acknowledged' THEN NEW.ack_by ELSE NULL END);
              RETURN NEW;
            END $$''')
        cur.execute('DROP TRIGGER IF EXISTS sonar_record_incident_event ON pro_incidents')
        cur.execute('''CREATE TRIGGER sonar_record_incident_event AFTER INSERT OR UPDATE ON pro_incidents
            FOR EACH ROW EXECUTE FUNCTION sonar_record_incident_event()''')
        # No historical events or successful SSH checks are invented for existing rows.
        cur.execute('''CREATE TABLE IF NOT EXISTS server_ssh_checks(
            id BIGSERIAL PRIMARY KEY,server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            owner_id BIGINT NOT NULL,actor_id BIGINT NOT NULL,
            state VARCHAR(24) NOT NULL DEFAULT 'checking',error_code VARCHAR(40),
            attempted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),completed_at TIMESTAMPTZ,
            CHECK(state IN ('checking','success','sudo_required','auth_failed','host_key_unknown','host_key_changed','network_failed','unknown')))''')
        cur.execute('CREATE INDEX IF NOT EXISTS server_ssh_checks_lookup ON server_ssh_checks(owner_id,server_id,id DESC)')
        cur.execute("CREATE INDEX IF NOT EXISTS server_ssh_success_lookup ON server_ssh_checks(owner_id,server_id,completed_at DESC) WHERE state IN ('success','sudo_required')")
        conn.commit()


# Correlated, indexed reads used by the safe list/detail projections. No credentials.
def ssh_select(alias):
    return f"""(SELECT json_build_object('state',c.state,'error_code',c.error_code,
        'attempted_at',c.attempted_at,'completed_at',c.completed_at) FROM server_ssh_checks c
        WHERE c.owner_id={alias}.owner_id AND c.server_id={alias}.id ORDER BY c.id DESC LIMIT 1) AS ssh,
        (SELECT MAX(c.completed_at) FROM server_ssh_checks c WHERE c.owner_id={alias}.owner_id
        AND c.server_id={alias}.id AND c.state IN ('success','sudo_required')) AS ssh_last_success_at"""


def check_ssh(db,server,actor,probe):
    row=db.execute('INSERT INTO server_ssh_checks(server_id,owner_id,actor_id) VALUES(%s,%s,%s) RETURNING id',
                   (server['id'],server['owner_id'],actor),fetch='one')
    try:
        result=probe()
    except Exception as exc:
        from ssh_support import describe_ssh_error
        result={'status':'Offline','error':describe_ssh_error(exc)}
    success=isinstance(result,dict) and result.get('status')=='Online'
    code=str(result.get('error','') if isinstance(result,dict) else '').split(':',1)[0].strip()
    code=code if code in SSH_CODES else 'SSH_CHECK_FAILED'
    state='success' if success else SSH_CODES.get(code,'unknown')
    db.execute('UPDATE server_ssh_checks SET state=%s,error_code=%s,completed_at=clock_timestamp() WHERE id=%s',
               (state,None if success else code,row['id']))
    return {'status':'Online' if success else 'Offline','state':state,**({} if success else {'error':code})}


def incident_page(store,actor,owner,status='unresolved',severity='',resource_type='',resource_id=None,
                  after=None,before=None,sort='updated',page=1,term=''):
    owner=store.workspace(actor,owner)
    where=['i.owner_id=%s'];args=[owner]
    if status in ('open','acknowledged','investigating','resolved'):where.append('i.status=%s');args.append(status)
    elif status!='all':where.append("i.status<>'resolved'")
    for field,value in [('severity',severity),('resource_type',resource_type),('resource_id',resource_id)]:
        if value:where.append(f'i.{field}=%s');args.append(value)
    if after:where.append('i.opened_at>=%s');args.append(after)
    if before:where.append('i.opened_at<=%s');args.append(before)
    if term:
        where.append('i.title ILIKE %s');args.append('%'+term[:80].replace('%',r'\%').replace('_',r'\_')+'%')
    order={'updated':'updated_at DESC,id DESC','newest':'opened_at DESC,id DESC',
           'oldest':'opened_at ASC,id ASC','severity':"CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,updated_at DESC,id DESC"}[sort]
    rows=store.db.execute(f'''WITH matched AS (
        SELECT i.id,i.resource_type,i.resource_id,i.kind,i.title,i.severity,i.status,i.occurrences,
        i.opened_at,i.updated_at,i.resolved_at FROM pro_incidents i WHERE {' AND '.join(where)})
        SELECT p.*,counts._total FROM (SELECT COUNT(*) _total FROM matched) counts
        LEFT JOIN LATERAL (SELECT * FROM matched ORDER BY {order} LIMIT 12 OFFSET %s) p ON TRUE
        ORDER BY {order.replace('id ', 'p.id ').replace('updated_at ', 'p.updated_at ').replace('opened_at ', 'p.opened_at ')}''',
        (*args,(page-1)*12),fetch='all')
    total=int(rows[0]['_total']);items=[dict(row) for row in rows if row['id'] is not None]
    for item in items:item.pop('_total',None)
    return items,total


def incident_detail(store,actor,owner,ident,page=1):
    owner=store.workspace(actor,owner)
    incident=store.db.execute('''SELECT id,title,resource_type,resource_id,status,severity,occurrences,
        opened_at,updated_at,resolved_at FROM pro_incidents WHERE owner_id=%s AND id=%s''',(owner,ident),fetch='one')
    if not incident:return None
    # Lifecycle events and existing human notes are both recorded facts.
    rows=store.db.execute('''WITH events AS (
        SELECT id,'lifecycle' source,event,status,severity,occurrences,actor_id,created_at,NULL::text note
        FROM pro_incident_events WHERE owner_id=%s AND incident_id=%s
        UNION ALL SELECT id,'note','note',NULL,NULL,NULL,actor_id,created_at,note
        FROM rs_incident_notes WHERE owner_id=%s AND incident_id=%s
        UNION ALL SELECT id,'audit',event,NULL,NULL,NULL,actor_id,created_at,detail
        FROM pro_audit WHERE owner_id=%s AND target=%s AND event='incident_transition')
        SELECT p.*,counts._total FROM (SELECT COUNT(*) _total FROM events) counts
        LEFT JOIN LATERAL (SELECT * FROM events ORDER BY created_at,id,source LIMIT 20 OFFSET %s) p ON TRUE
        ORDER BY p.created_at,p.id,p.source''',(owner,ident,owner,ident,owner,str(ident),(page-1)*20),fetch='all')
    events=[dict(row) for row in rows if row['id'] is not None]
    from logging_config import redact
    for event in events:
        event.pop('_total',None)
        if event['note']:event['note']=redact(event['note'])
    return {'incident':dict(incident),'events':events,'total':int(rows[0]['_total']),'page':page}
