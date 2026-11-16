"""Owner-scoped configuration catalog; never returns links or runs a probe."""
import json


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('ALTER TABLE tunnel_configs ADD COLUMN IF NOT EXISTS catalog_changed_at TIMESTAMPTZ')
        cur.execute('''CREATE OR REPLACE FUNCTION sonar_catalog_changed() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.link IS DISTINCT FROM OLD.link OR NEW.type IS DISTINCT FROM OLD.type THEN
              NEW.catalog_changed_at:=clock_timestamp(); END IF; RETURN NEW; END $$''')
        cur.execute('DROP TRIGGER IF EXISTS sonar_catalog_changed ON tunnel_configs')
        cur.execute('''CREATE TRIGGER sonar_catalog_changed BEFORE UPDATE OF link,type ON tunnel_configs
            FOR EACH ROW EXECUTE FUNCTION sonar_catalog_changed()''')
        cur.execute('CREATE INDEX IF NOT EXISTS sonar_config_probes_catalog ON sonar_config_probes(owner_id,config_id,id DESC)')
        conn.commit()


def list_configs(store,actor,owner=None,term='',tag=None,favorites=False,page=1,per_page=12,
                 health='',config_type='',protocol='',sort='default'):
    owner=store.workspace(actor,owner)
    where=["r.owner_id=%s","r.type IN ('single','sub_item','json','sub_source')"];args=[owner]
    if term:
        where.append('r.name ILIKE %s');args.append('%'+term[:80].replace('%',r'\%').replace('_',r'\_')+'%')
    if tag:
        where.append("EXISTS(SELECT 1 FROM pro_tags t WHERE t.owner_id=r.owner_id AND t.resource_type='config' AND t.resource_id=r.id AND t.tag=%s)");args.append(tag[:40])
    if favorites:where.append("EXISTS(SELECT 1 FROM pro_favorites f WHERE f.owner_id=r.owner_id AND f.resource_type='config' AND f.resource_id=r.id)")
    if config_type:where.append('r.type=%s');args.append(config_type)
    filters=[]
    if health:filters.append('health=%s');args.append(health)
    if protocol:filters.append('protocol=%s');args.append(protocol)
    order={'default':'favorite DESC,id DESC','latency':'last_ping ASC NULLS LAST,id DESC',
           'score':'quality_score DESC NULLS LAST,id DESC','checked':'checked_at DESC NULLS LAST,id DESC',
           'name':'name ASC,id DESC'}[sort]
    # Counts and page use the same snapshot and all selected filters.
    rows=store.db.execute(f'''WITH evidence AS (
        SELECT r.id,r.name,r.type,r.last_status,r.last_ping raw_ping,r.quality_score raw_score,
          r.last_jitter raw_jitter,r.catalog_changed_at,m.measured_at,
          CASE WHEN r.type='sub_source' THEN 'subscription' WHEN r.type='json' THEN 'profile'
            WHEN r.link ILIKE 'hy2://%%' THEN 'hysteria2'
            WHEN r.link ~* '^(vless|vmess|trojan|ss|hysteria2|tuic)://' THEN lower(split_part(r.link,'://',1))
            ELSE 'unknown' END protocol,
          EXISTS(SELECT 1 FROM pro_favorites f WHERE f.owner_id=r.owner_id AND f.resource_type='config' AND f.resource_id=r.id) favorite,
          p.state probe_state,p.completed_at,p.created_at,
          (p.id IS NOT NULL AND COALESCE(p.completed_at,p.created_at)>=COALESCE(m.measured_at,'-infinity'::timestamptz)
           AND COALESCE(p.completed_at,p.created_at)>=COALESCE(r.catalog_changed_at,'-infinity'::timestamptz)) probe_current
        FROM tunnel_configs r
        LEFT JOIN LATERAL (SELECT measured_at FROM pro_metrics WHERE owner_id=r.owner_id AND config_id=r.id
            ORDER BY measured_at DESC,id DESC LIMIT 1) m ON TRUE
        LEFT JOIN LATERAL (SELECT id,state,completed_at,created_at FROM sonar_config_probes
            WHERE owner_id=r.owner_id AND config_id=r.id ORDER BY id DESC LIMIT 1) p ON TRUE
        WHERE {' AND '.join(where)}), classified AS (
        SELECT *,CASE WHEN type='sub_source' THEN 'source'
          WHEN probe_current AND probe_state='failed' THEN 'environment_error'
          WHEN probe_current AND probe_state IN ('pending','running') THEN 'pending'
          WHEN catalog_changed_at IS NOT NULL AND (measured_at IS NULL OR measured_at<catalog_changed_at) THEN 'untested'
          WHEN last_status='OK' THEN 'healthy' WHEN last_status='Fail' THEN 'failed'
          WHEN last_status='Error' THEN 'environment_error'
          WHEN last_status IN ('Unknown','Pending') THEN 'untested' ELSE 'unknown' END health
        FROM evidence), matched AS (
        SELECT id,name,type,last_status,protocol,favorite,health,
          CASE WHEN health='healthy' AND raw_ping>0 AND raw_ping<60000 THEN raw_ping END last_ping,
          CASE WHEN health='healthy' AND raw_jitter>=0 AND raw_jitter<60000 THEN raw_jitter END last_jitter,
          CASE WHEN health IN ('healthy','failed') AND raw_score BETWEEN 0 AND 10 THEN raw_score END quality_score,
          CASE WHEN probe_current AND probe_state='failed' THEN completed_at
            WHEN health IN ('healthy','failed') THEN measured_at END checked_at
        FROM classified {'WHERE '+ ' AND '.join(filters) if filters else ''}), counts AS (
          SELECT COUNT(*) total,COUNT(*) FILTER(WHERE health='healthy') healthy,
            COUNT(*) FILTER(WHERE health='failed') failed,COUNT(*) FILTER(WHERE health NOT IN ('healthy','failed')) unknown,
            COUNT(*) FILTER(WHERE health='environment_error') environment_error,
            COUNT(*) FILTER(WHERE health='untested') untested FROM matched)
        SELECT p.*,row_to_json(counts) _summary FROM counts LEFT JOIN LATERAL (
          SELECT * FROM matched ORDER BY {order} LIMIT %s OFFSET %s) p ON TRUE ORDER BY {order}''',
        (*args,per_page,(page-1)*per_page),fetch='all')
    summary=dict(rows[0]['_summary']);items=[dict(row) for row in rows if row['id'] is not None]
    for item in items:item.pop('_summary',None)
    return items,int(summary['total']),summary


def summary_header(summary):return json.dumps(summary,separators=(',',':'))
