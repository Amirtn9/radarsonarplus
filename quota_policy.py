"""Database-enforced independent capacities shared by Bot, Web and workers."""
SCHEMA = '''
CREATE OR REPLACE FUNCTION sonar_quota_guard() RETURNS TRIGGER AS $$
DECLARE sub RECORD; quota TEXT; used BIGINT; maximum BIGINT; actor BIGINT;
BEGIN
  -- Resource ownership is immutable through normal UI operations. Workspace
  -- merges still pass through this same serialized capacity check.
  PERFORM pg_advisory_xact_lock(535320,hashtext(NEW.owner_id::text));
  SELECT * INTO sub FROM sonar_subscriptions WHERE user_id=NEW.owner_id;
  IF NOT FOUND THEN RETURN NEW; END IF; -- preserve existing installations
  IF sub.status<>'active' OR (sub.expires_at IS NOT NULL AND sub.expires_at<=NOW()) THEN
    RAISE EXCEPTION 'SUBSCRIPTION_INACTIVE' USING ERRCODE='P0001';
  END IF;
  IF TG_TABLE_NAME='servers' THEN
    quota:='servers'; SELECT COUNT(*) INTO used FROM servers WHERE owner_id=NEW.owner_id AND id<>NEW.id;
  ELSIF TG_TABLE_NAME='tunnel_configs' THEN
    IF NEW.type='sub_source' THEN RETURN NEW; END IF;
    quota:='configs'; SELECT COUNT(*) INTO used FROM tunnel_configs WHERE owner_id=NEW.owner_id AND type<>'sub_source' AND id<>NEW.id;
  ELSIF TG_TABLE_NAME='pro_services' THEN
    quota:='websites'; SELECT COUNT(*) INTO used FROM pro_services WHERE owner_id=NEW.owner_id AND id<>NEW.id;
  ELSIF TG_TABLE_NAME='channels' THEN
    quota:='destinations'; SELECT COUNT(*) INTO used FROM channels WHERE owner_id=NEW.owner_id AND id<>NEW.id;
  ELSIF TG_TABLE_NAME='sonar_hosting_agents' THEN
    IF NEW.revoked_at IS NOT NULL THEN RETURN NEW; END IF;
    quota:='agents'; SELECT (SELECT COUNT(*) FROM servers WHERE owner_id=NEW.owner_id AND agent_id IS NOT NULL)+(SELECT COUNT(*) FROM sonar_hosting_agents WHERE owner_id=NEW.owner_id AND revoked_at IS NULL AND id<>NEW.id) INTO used;
  ELSIF TG_TABLE_NAME='pro_team' THEN
    quota:='team'; SELECT COUNT(*) INTO used FROM pro_team WHERE owner_id=NEW.owner_id AND member_id<>NEW.member_id;
  END IF;
  maximum:=COALESCE((sub.overrides->>quota)::BIGINT,(sub.snapshot->'limits'->>quota)::BIGINT,0);
  IF used>=maximum THEN RAISE EXCEPTION 'QUOTA_EXCEEDED:%',quota USING ERRCODE='P0001'; END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION sonar_agent_quota_guard() RETURNS TRIGGER AS $$
DECLARE sub RECORD; used BIGINT; maximum BIGINT;
BEGIN
 IF NEW.agent_id IS NULL OR (TG_OP='UPDATE' AND OLD.agent_id IS NOT NULL AND OLD.owner_id=NEW.owner_id) THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(535320,hashtext(NEW.owner_id::text));
 SELECT * INTO sub FROM sonar_subscriptions WHERE user_id=NEW.owner_id;
 IF NOT FOUND THEN RETURN NEW; END IF;
 IF sub.status<>'active' OR (sub.expires_at IS NOT NULL AND sub.expires_at<=NOW()) THEN
  RAISE EXCEPTION 'SUBSCRIPTION_INACTIVE' USING ERRCODE='P0001'; END IF;
 SELECT (SELECT COUNT(*) FROM servers WHERE owner_id=NEW.owner_id AND agent_id IS NOT NULL AND id<>NEW.id)+(SELECT COUNT(*) FROM sonar_hosting_agents WHERE owner_id=NEW.owner_id AND revoked_at IS NULL) INTO used;
 maximum:=COALESCE((sub.overrides->>'agents')::BIGINT,(sub.snapshot->'limits'->>'agents')::BIGINT,0);
 IF used>=maximum THEN RAISE EXCEPTION 'QUOTA_EXCEEDED:agents' USING ERRCODE='P0001'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
'''


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute(SCHEMA)
        for table in ('servers','tunnel_configs','pro_services','channels','pro_team'):
            cur.execute(f'DROP TRIGGER IF EXISTS sonar_quota_insert ON {table}')
            cur.execute(f'CREATE TRIGGER sonar_quota_insert BEFORE INSERT OR UPDATE OF owner_id ON {table} FOR EACH ROW EXECUTE FUNCTION sonar_quota_guard()')
        cur.execute('DROP TRIGGER IF EXISTS sonar_agent_quota ON servers')
        cur.execute('CREATE TRIGGER sonar_agent_quota BEFORE INSERT OR UPDATE OF agent_id,owner_id ON servers FOR EACH ROW EXECUTE FUNCTION sonar_agent_quota_guard()')
        conn.commit()
    install_runtime_policy(db)


def install_runtime_policy(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE OR REPLACE FUNCTION sonar_retention_days(owner BIGINT) RETURNS INTEGER LANGUAGE SQL STABLE AS $$ SELECT COALESCE((SELECT LEAST(30,GREATEST(1,COALESCE((snapshot->'policy'->>'retention_days')::INTEGER,30))) FROM sonar_subscriptions WHERE user_id=owner),30) $$;
        CREATE OR REPLACE FUNCTION sonar_min_interval(owner BIGINT) RETURNS INTEGER LANGUAGE SQL STABLE AS $$ SELECT COALESCE((SELECT LEAST(3600,GREATEST(60,COALESCE((snapshot->'policy'->>'min_interval_seconds')::INTEGER,60))) FROM sonar_subscriptions WHERE user_id=owner),60) $$;
        CREATE OR REPLACE FUNCTION sonar_service_interval_guard() RETURNS TRIGGER AS $$ BEGIN IF NEW.interval_seconds<sonar_min_interval(NEW.owner_id) THEN RAISE EXCEPTION 'PLAN_INTERVAL_TOO_SHORT' USING ERRCODE='P0001'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS sonar_service_min_interval ON pro_services;
        CREATE TRIGGER sonar_service_min_interval BEFORE INSERT OR UPDATE OF interval_seconds,owner_id ON pro_services FOR EACH ROW EXECUTE FUNCTION sonar_service_interval_guard();
        ''')
        cur.execute('''CREATE OR REPLACE FUNCTION sonar_monitor_allowed(owner BIGINT,kind TEXT,rid BIGINT) RETURNS BOOLEAN AS $$
        DECLARE sub RECORD; maximum BIGINT; position BIGINT;
        BEGIN
          SELECT * INTO sub FROM sonar_subscriptions WHERE user_id=owner;
          IF NOT FOUND THEN RETURN TRUE; END IF;
          IF sub.status<>'active' OR (sub.expires_at IS NOT NULL AND sub.expires_at<=NOW()) THEN RETURN FALSE; END IF;
          IF kind='agents' AND sub.snapshot->'features'->>'agents'='false' THEN RETURN FALSE; END IF;
          maximum:=COALESCE((sub.overrides->>kind)::BIGINT,(sub.snapshot->'limits'->>kind)::BIGINT,0);
          IF kind='servers' THEN SELECT COUNT(*) INTO position FROM servers WHERE owner_id=owner AND id<=rid;
          ELSIF kind='websites' THEN SELECT COUNT(*) INTO position FROM pro_services WHERE owner_id=owner AND id<=rid;
          ELSIF kind='agents' THEN SELECT COUNT(*) INTO position FROM servers WHERE owner_id=owner AND agent_id IS NOT NULL AND id<=rid;
          ELSIF kind='configs' THEN SELECT COUNT(*) INTO position FROM tunnel_configs WHERE owner_id=owner AND type<>'sub_source' AND id<=rid;
          ELSE RETURN FALSE; END IF;
          RETURN position<=maximum;
        END; $$ LANGUAGE plpgsql STABLE''')
        conn.commit()
