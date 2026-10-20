import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import threading
import os
import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

logger = logging.getLogger('ws_client')  # ✅ خودکار به سیستم متصل می‌شه

# ✅ جمع‌آوری لاگ‌های WS (بدون ارجاع به متغیرهای تعریف‌نشده در سطح ماژول)
try:
    logger.debug("WS module initialized (ip=%s, port=%s)", locals().get('ip'), locals().get('port'))
except Exception:
    pass
try:
    logger.debug("WebSocket error hook ready")
except Exception:
    pass
# Import settings
from settings import DB_CONFIG, SUBSCRIPTION_PLANS, SUPER_ADMIN_ID, AGENT_PORT

logger = logging.getLogger(__name__)

class Database:
    """PostgreSQL access layer with a shared connection pool. 

    IMPORTANT: 
    - The project imports Database() in multiple modules.  Creating multiple
      pools is unnecessary and can exhaust PostgreSQL connections.
    - We therefore implement a lightweight singleton:  every Database() call
      returns the same instance and pool.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self. db_config = DB_CONFIG
        self.lock = threading.RLock()
        self.pool = None
        self._pool_error = None
        self._ensure_pool(init_schema=True)
        self._initialized = True

    def _ensure_pool(self, init_schema:  bool = False):
        if self.pool is not None:
            return
        with self.lock:
            if self.pool is not None:
                return
            last_err = None
            for _ in range(3):
                try:
                    self.pool = psycopg2.pool.ThreadedConnectionPool(1, max(2,min(20,int(os.getenv('SONAR_DB_POOL_MAX','12')))), **self.db_config)
                    self._pool_error = None
                    if init_schema:
                        self. init_db()
                    return
                except Exception as e:
                    last_err = e
                    self. pool = None
                    self._pool_error = e
            if last_err:
                logger.error(f"❌ Error creating connection pool: {last_err}")

    def _require_pool(self):
        if self.pool is None:
            self._ensure_pool(init_schema=True)
        if self.pool is None:
            raise psycopg2.OperationalError(f"Database pool is not available: {self._pool_error}")

    @contextmanager
    def get_connection(self):
        """Manage database connections using the pool"""
        conn = None
        cur = None
        try:
            self._ensure_pool(init_schema=False)
            if self.pool is None:
                raise psycopg2.OperationalError(self._pool_error or "Database pool is not initialized")
            conn = self.pool.getconn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            from workspace_service import ACTOR_ID
            actor = ACTOR_ID.get()
            if actor:
                cur.execute("SELECT set_config('sonar.actor',%s,true)",(str(int(actor)),))
            yield conn, cur
        except psycopg2.Error as e:
            logger.error(f"⚠️ Database Error: {e}")
            if conn:  conn.rollback()
            raise e
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("Database operation failed: %s", e)
            raise
        finally:
            if conn:
                try:  cur.close()
                except: pass
                self.pool.putconn(conn)

    def execute(self, query, params=None, fetch=None):
        """Helper to execute a single query quickly."""
        with self.get_connection() as (conn, cur):
            cur.execute(query, params)
            res = None
            if fetch == 'one':
                res = cur.fetchone()
            elif fetch == 'all':
                res = cur.fetchall()
            conn.commit()
            return res

    def init_db(self):
        """Initialize database schema"""
        with self.get_connection() as (conn, cur):
            cur.execute("SELECT pg_advisory_xact_lock(535300)")
            # Users
            cur.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, 
                full_name TEXT, 
                added_date TEXT, 
                expiry_date TEXT, 
                server_limit INTEGER DEFAULT 2, 
                is_banned INTEGER DEFAULT 0, 
                plan_type INTEGER DEFAULT 0, 
                wallet_balance BIGINT DEFAULT 0, 
                referral_count INTEGER DEFAULT 0, 
                invited_by BIGINT DEFAULT 0
            )''')
            
            # Groups
            cur.execute('''CREATE TABLE IF NOT EXISTS groups (
                id SERIAL PRIMARY KEY, 
                owner_id BIGINT, 
                name TEXT, 
                UNIQUE(owner_id, name)
            )''')
            
            # Servers
            cur.execute('''CREATE TABLE IF NOT EXISTS servers (
                id SERIAL PRIMARY KEY, 
                owner_id BIGINT, 
                group_id INTEGER, 
                name TEXT, 
                ip TEXT, 
                port INTEGER, 
                username TEXT, 
                password TEXT, 
                expiry_date TEXT, 
                last_status TEXT DEFAULT 'Unknown', 
                last_error TEXT DEFAULT '',
                agent_last_log TEXT DEFAULT '',
                agent_last_seen TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1, 
                location_type TEXT DEFAULT 'ext', 
                created_at TEXT, 
                is_monitor_node INTEGER DEFAULT 0, 
                UNIQUE(owner_id, name)
            )''')

            try:
                cur.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS last_error TEXT DEFAULT ''")
                cur.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS agent_last_log TEXT DEFAULT ''")
                cur.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS agent_last_seen TEXT DEFAULT ''")
                # 🔧 رفع باگ نسخه ۴.۰: این ستون توسط update_server_ws_port() نوشته می‌شد
                # ولی هیچ‌جا ساخته نشده بود -> خطای "column ws_port does not exist"
                cur.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS ws_port INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS agent_token TEXT")
                cur.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS agent_cert_pem TEXT")
            except Exception as e:
                logger.error(f"مایگریشن ستون‌های سرور ناموفق بود / server columns migration failed: {e}", exc_info=True)
            
            # One managed physical agent per SSH endpoint; server rows may share it.
            cur.execute("""CREATE TABLE IF NOT EXISTS agent_nodes (
                id SERIAL PRIMARY KEY, ip TEXT NOT NULL, ssh_port INTEGER NOT NULL,
                ws_port INTEGER NOT NULL, agent_token TEXT NOT NULL,
                agent_cert_pem TEXT NOT NULL DEFAULT '', UNIQUE(ip,ssh_port))""")
            cur.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS agent_id INTEGER")
            cur.execute("UPDATE servers SET ws_port=%s WHERE ws_port IS NULL OR ws_port=0", (AGENT_PORT,))
            cur.execute("""INSERT INTO agent_nodes(ip,ssh_port,ws_port,agent_token,agent_cert_pem)
                SELECT DISTINCT ON (ip,port) ip,port,ws_port,agent_token,COALESCE(agent_cert_pem,'')
                FROM servers WHERE agent_token IS NOT NULL AND agent_token<>''
                ORDER BY ip,port,id ON CONFLICT(ip,ssh_port) DO NOTHING""")
            cur.execute("""UPDATE servers s SET agent_id=a.id,ws_port=a.ws_port
                FROM agent_nodes a WHERE s.ip=a.ip AND s.port=a.ssh_port""")
            cur.execute("CREATE INDEX IF NOT EXISTS agent_nodes_wss_idx ON agent_nodes(ip,ws_port)")

            for column, definition in (
                ('last_cpu','REAL'),('last_ram','REAL'),('last_disk','REAL'),
                ('last_checked_at','TIMESTAMPTZ'),('last_agent_status',"TEXT DEFAULT 'Unknown'")):
                cur.execute(f'ALTER TABLE servers ADD COLUMN IF NOT EXISTS {column} {definition}')
            # Settings
            cur.execute('''CREATE TABLE IF NOT EXISTS settings (
                owner_id BIGINT, 
                key TEXT, 
                value TEXT, 
                PRIMARY KEY(owner_id, key)
            )''')
            
            # Channels
            cur.execute('''CREATE TABLE IF NOT EXISTS channels (
                id SERIAL PRIMARY KEY, 
                owner_id BIGINT, 
                chat_id TEXT, 
                name TEXT, 
                usage_type TEXT DEFAULT 'all',
                topic_id INTEGER DEFAULT 0
            )''')
            
            # Server Stats
            cur.execute('''CREATE TABLE IF NOT EXISTS server_stats (
                id SERIAL PRIMARY KEY, 
                server_id INTEGER, 
                cpu REAL, 
                ram REAL, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Payments
            cur.execute('''CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT, 
                plan_type TEXT, 
                amount BIGINT, 
                method TEXT, 
                status TEXT DEFAULT 'pending', 
                created_at TEXT
            )''')
            
            # Temp Bonuses
            cur.execute('''CREATE TABLE IF NOT EXISTS temp_bonuses (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT, 
                bonus_limit INTEGER, 
                created_at TEXT, 
                expires_at TEXT
            )''')
            
            # Payment Methods
            cur.execute('''CREATE TABLE IF NOT EXISTS payment_methods (
                id SERIAL PRIMARY KEY, 
                type TEXT, 
                network TEXT, 
                address TEXT, 
                holder_name TEXT, 
                is_active INTEGER DEFAULT 1
            )''')
            
            # Tunnel Configs
            cur.execute('''CREATE TABLE IF NOT EXISTS tunnel_configs (
                id SERIAL PRIMARY KEY, 
                owner_id BIGINT, 
                type TEXT, 
                link TEXT UNIQUE, 
                name TEXT, 
                last_status TEXT DEFAULT 'Unknown', 
                last_ping INTEGER DEFAULT 0, 
                quality_score INTEGER DEFAULT 10, 
                added_at TEXT, 
                last_jitter INTEGER DEFAULT 0, 
                last_speed_up TEXT DEFAULT '0 Mbps', 
                last_speed_down TEXT DEFAULT '0 Mbps',
                sub_info TEXT DEFAULT '{}'
            )''')
            
            # Migration: parent IDs prevent collisions between similarly named subscriptions.
            cur.execute("ALTER TABLE tunnel_configs ADD COLUMN IF NOT EXISTS parent_sub_id INTEGER")
            cur.execute("ALTER TABLE tunnel_configs DROP CONSTRAINT IF EXISTS tunnel_configs_link_key")
            cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS tunnel_configs_scoped_link_unique
                ON tunnel_configs (owner_id, type, link, (COALESCE(parent_sub_id, 0)))""")
            cur.execute("CREATE INDEX IF NOT EXISTS tunnel_configs_sub_children_idx ON tunnel_configs(owner_id, parent_sub_id)")
            # One-time migration of legacy "name | item" children. Prefer the longest
            # matching source name to handle source names that are prefixes of others.
            cur.execute("""UPDATE tunnel_configs child SET parent_sub_id = (
                SELECT parent.id FROM tunnel_configs parent
                WHERE parent.owner_id=child.owner_id AND parent.type='sub_source'
                  AND child.name LIKE replace(replace(parent.name, '%', '\\%'), '_', '\\_') || ' | %' ESCAPE '\\'
                ORDER BY length(parent.name) DESC, parent.id LIMIT 1
            ) WHERE child.type='sub_item' AND child.parent_sub_id IS NULL""")
            from workspace_service import SCHEMA, AUDIT_SCHEMA, REDIRECT_SCHEMA
            cur.execute(SCHEMA)
            cur.execute(AUDIT_SCHEMA)
            cur.execute(REDIRECT_SCHEMA)
            conn.commit()

    def workspace_owner(self, user_id):
        from workspace_service import WorkspaceService
        return WorkspaceService(self).owner(user_id)

    def workspace_members(self, user_id):
        from workspace_service import WorkspaceService
        return WorkspaceService(self).members(user_id)

    def workspace_server_limit(self, user_id):
        """Member quotas are pooled, but each account subscription remains private."""
        ids = self.workspace_members(user_id)
        row = self.execute('SELECT COALESCE(SUM(server_limit),0) AS total FROM users WHERE user_id=ANY(%s)',
                           (ids,),fetch='one')
        return max(0,int(row['total'] or 0))

    def get_tehran_datetime(self):
        return datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Tehran'))

    # ==========================================================================
    # 📌 CRITICAL METHODS
    # ==========================================================================
    
    def check_access(self, user_id, super_admin_id=None):
        """Validate user access based on ban status and expiry"""
        if super_admin_id is None:
            super_admin_id = SUPER_ADMIN_ID
        if user_id == super_admin_id:
            return True, "Super Admin"
        user = self.get_user(user_id)
        if not user:  
            return False, "کاربر یافت نشد"
        if user['is_banned']: 
            return False, "حساب شما مسدود شده است ⛔️"
        try:
            if not user['expiry_date']:  
                return True, "Unlimited"
            
            expiry_dt = datetime.strptime(user['expiry_date'], '%Y-%m-%d %H:%M:%S')
            now_tehran_naive = self.get_tehran_datetime().replace(tzinfo=None)
            
            if now_tehran_naive > expiry_dt:  
                return False, "اشتراک شما منقضی شده است 📅"
            return True, (expiry_dt - now_tehran_naive).days
        except Exception as e:
            logger.error(f"Date check error for {user_id}: {e}")
            return False, "خطا در بررسی تاریخ"

    def get_active_servers(self):
        """Get all active servers for monitoring"""
        with self.get_connection() as (conn, cur):
            cur.execute("SELECT * FROM servers WHERE is_active = 1")
            return cur.fetchall()

    def get_near_expiry_services(self):
        """Get services expiring in 3 days"""
        target = (self.get_tehran_datetime().replace(tzinfo=None) + timedelta(days=3)).strftime('%Y-%m-%d')
        with self.get_connection() as (conn, cur):
            cur.execute("SELECT * FROM servers WHERE expiry_date LIKE %s AND is_active = 1", (f"{target}%",))
            return cur.fetchall()

    def add_server_stats_batch(self, stats_list):
        """Batch insert server statistics"""
        if not stats_list:  
            return
        with self.get_connection() as (conn, cur):
            cur.executemany(
                'INSERT INTO server_stats (server_id, cpu, ram) VALUES (%s, %s, %s)',
                stats_list
            )
            cur.execute("DELETE FROM server_stats WHERE created_at < NOW() - INTERVAL '1 day'")
            conn.commit()

    def is_monitor_active(self):
        """Check if a monitor node exists and is active"""
        with self.get_connection() as (conn, cur):
            cur.execute("SELECT id FROM servers WHERE is_monitor_node = 1 AND is_active = 1 LIMIT 1")
            return cur.fetchone() is not None

    # ==========================================================================
    # 👤 USER METHODS
    # ==========================================================================
    
    def get_user(self, user_id):
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
            return cur.fetchone()

    def add_or_update_user(self, user_id, full_name=None, invited_by=0, days=None):
        exist = self.get_user(user_id)
        now_str = self.get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
        default_days = days if days is not None else 60
        
        with self.get_connection() as (conn, cur):
            if exist:
                if full_name:
                    cur. execute('UPDATE users SET full_name = %s WHERE user_id = %s', (full_name, user_id))
                
                if days is not None:
                    try:
                        current_exp_str = exist['expiry_date']
                        if current_exp_str:
                            current_exp = datetime.strptime(current_exp_str, '%Y-%m-%d %H:%M:%S')
                            if current_exp < self.get_tehran_datetime().replace(tzinfo=None): 
                                current_exp = self.get_tehran_datetime().replace(tzinfo=None)
                        else:
                            current_exp = self.get_tehran_datetime().replace(tzinfo=None)
                            
                        new_exp = (current_exp + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
                        cur.execute('UPDATE users SET expiry_date = %s WHERE user_id = %s', (new_exp, user_id))
                    except:  
                        pass
            else:
                expiry = (self.get_tehran_datetime() + timedelta(days=default_days)).strftime('%Y-%m-%d %H:%M:%S')
                cur.execute('''
                    INSERT INTO users (user_id, full_name, added_date, expiry_date, server_limit, invited_by, wallet_balance, referral_count) 
                    VALUES (%s, %s, %s, %s, 2, %s, 0, 0)
                ''', (user_id, full_name, now_str, expiry, invited_by))
            conn.commit()

    def get_all_users(self):
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT * FROM users')
            return cur.fetchall()
            
    def get_all_users_paginated(self, page=1, per_page=5):
        offset = (page - 1) * per_page
        with self. get_connection() as (conn, cur):
            cur.execute('SELECT * FROM users LIMIT %s OFFSET %s', (per_page, offset))
            users = cur.fetchall()
            cur.execute('SELECT COUNT(*) as count FROM users')
            total = cur.fetchone()['count']
            return users, total

    def update_user_limit(self, user_id, limit):
        self.execute('UPDATE users SET server_limit = %s WHERE user_id = %s', (limit, user_id))

    def toggle_ban_user(self, user_id):
        user = self.get_user(user_id)
        if not user:  
            return 0
        new_state = 0 if user['is_banned'] else 1
        self.execute('UPDATE users SET is_banned = %s WHERE user_id = %s', (new_state, user_id))
        return new_state
        
    def toggle_user_plan(self, user_id):
        user = self.get_user(user_id)
        if not user: 
            return 0 
        new_plan = 1 if user['plan_type'] == 0 else 0
        new_limit = 10 if new_plan == 1 else 2
        self.execute('UPDATE users SET plan_type = %s, server_limit = %s WHERE user_id = %s', (new_plan, new_limit, user_id))
        return new_plan

    def remove_user(self, user_id):
        """Delete tenant data transactionally; preserve no orphan pro/team records."""
        owner = int(user_id)
        tables = ('pro_server_monitor_state','pro_notification_outbox','pro_services','pro_metrics','pro_favorites','pro_tags','pro_incidents',
                  'pro_audit','pro_rules','pro_preferences','pro_team',
                  'tunnel_configs','server_stats','settings',
                  'servers','groups','channels')
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT pg_advisory_xact_lock(641400)')
            cur.execute('SELECT 1 FROM sonar_workspaces WHERE owner_id=%s AND member_id<>%s LIMIT 1', (owner,owner))
            if cur.fetchone():
                raise ValueError('این کاربر مدیر فضای کاری مشترک است؛ حذف او بدون انتقال مدیریت مجاز نیست.')
            cur.execute('DELETE FROM sonar_workspaces WHERE member_id=%s', (owner,))
            cur.execute('DELETE FROM sonar_workspace_requests WHERE requester=%s OR target=%s', (owner,owner))
            cur.execute("SELECT to_regclass('public.pro_job_cursors') AS relation")
            if cur.fetchone()['relation']:
                cur.execute('DELETE FROM pro_job_cursors WHERE name IN (%s,%s)',(f'multi_mon:{owner}',f'multi_cfg:{owner}'))
            for table in tables:
                # Legacy installations may not yet have pro_* schema.
                cur.execute('SELECT to_regclass(%s) AS relation', ('public.' + table,))
                if not cur.fetchone()['relation']:
                    continue
                if table == 'server_stats':
                    cur.execute('DELETE FROM server_stats WHERE server_id IN '
                                '(SELECT id FROM servers WHERE owner_id=%s)', (owner,))
                else:
                    cur.execute(f'DELETE FROM {table} WHERE owner_id=%s', (owner,))
            cur.execute('SELECT to_regclass(%s) AS relation', ('public.pro_team',))
            if cur.fetchone()['relation']:
                cur.execute('DELETE FROM pro_team WHERE member_id=%s', (owner,))
            cur.execute('DELETE FROM users WHERE user_id=%s', (owner,))
            conn.commit()

    # ==========================================================================
    # 🖥 SERVER METHODS
    # ==========================================================================
    
    def get_all_user_servers(self, owner_id):
        owner_id = self.workspace_owner(owner_id)
        with self. get_connection() as (conn, cur):
            cur.execute('SELECT * FROM servers WHERE owner_id = %s ORDER BY id ASC', (owner_id,))
            return cur.fetchall()
            
    def get_all_servers(self):
        """دریافت تمام سرورهای سیستم (برای ادمین)"""
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT * FROM servers')
            return cur.fetchall()

    def get_server_by_id(self, sid):
        # دفاع در برابر CallbackData یا ورودی اشتباه (مثلاً 'cleandisk')
        try:
            sid = int(sid)
        except Exception:
            try:
                logger.warning(f"get_server_by_id: invalid sid={sid!r}")
            except Exception:
                pass
            return None

        with self.get_connection() as (conn, cur):
            cur.execute("SELECT * FROM servers WHERE id=%s", (sid,))
            return cur.fetchone()

    def get_server_for_user(self, sid, actor, admin_id=0):
        actor = self.workspace_owner(actor)
        if int(actor)==int(admin_id) and int(admin_id)>0:
            return self.get_server_by_id(sid)
        return self.execute('SELECT * FROM servers WHERE id=%s AND owner_id=%s', (int(sid),int(actor)),fetch='one')

    def add_server(self, owner_id, group_id, data, super_admin_id=0):
        owner_id = self.workspace_owner(owner_id)
        from core import sec
        
        g_id = group_id if group_id != 0 else None
        if g_id is not None:
            if not self.execute('SELECT 1 AS allowed FROM groups WHERE id=%s AND owner_id=%s',
                                (g_id, owner_id), fetch='one'):
                return -3
        user = self.get_user(owner_id)
        current = len(self.get_all_user_servers(owner_id))
        
        if user and owner_id != super_admin_id:
            if current >= self.workspace_server_limit(owner_id):
                return -1
        
        now = self.get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
        loc = data.get('location_type', 'ext')
        
        password = data['password']
        if password:
            from cryptography.fernet import InvalidToken
            try:
                sec.cipher.decrypt(password.encode('utf-8'))
            except (InvalidToken, ValueError, TypeError):
                password = sec.encrypt(password)
        
        with self.get_connection() as (conn, cur):
            try:
                cur.execute(
                    'INSERT INTO servers (owner_id, group_id, name, ip, port, username, password, expiry_date, location_type, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id', 
                    (owner_id, g_id, data['name'], data['ip'], data['port'], data['username'], password, data. get('expiry_date'), loc, now)
                )
                server_id = cur.fetchone()['id']
                ws_port = int(data.get('ws_port') or AGENT_PORT)
                if ws_port is not None:
                    cur.execute('UPDATE servers SET ws_port=%s WHERE id=%s', (int(ws_port), server_id))
                conn.commit()
                return server_id
            except psycopg2.IntegrityError:
                conn.rollback()
                return -2

    def delete_server(self, s_id, owner_id):
        owner_id = self.workspace_owner(owner_id)
        self.execute('DELETE FROM servers WHERE id = %s AND owner_id = %s', (s_id, owner_id))

    def activate_monitor_node(self, sid, owner):
        owner = self.workspace_owner(owner)
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT pg_advisory_xact_lock(535301)')
            cur.execute('SELECT id FROM servers WHERE id=%s AND owner_id=%s FOR UPDATE', (sid, owner))
            if not cur.fetchone():
                raise PermissionError('Monitor server no longer belongs to owner')
            cur.execute('UPDATE servers SET is_monitor_node=CASE WHEN id=%s THEN 1 ELSE 0 END WHERE owner_id=%s', (sid, owner))
            conn.commit()

    def update_status(self, s_id, status):
        self.execute('UPDATE servers SET last_status = %s WHERE id = %s', (status, s_id))

    def update_server_error(self, s_id, error_text:  str = ""):
        try:
            self.execute('UPDATE servers SET last_error = %s WHERE id = %s', (str(error_text or ""), s_id))
        except Exception:  
            pass

    def update_agent_state(self, s_id, *, status:  str = "", last_log: str = "", last_seen: str = ""):
        try:
            if status: 
                self.execute(
                    'UPDATE servers SET agent_last_log=%s, agent_last_seen=%s, last_status=%s WHERE id=%s',
                    (str(last_log or ""), str(last_seen or ""), str(status), s_id),
                )
            else:
                self.execute(
                    'UPDATE servers SET agent_last_log=%s, agent_last_seen=%s WHERE id=%s',
                    (str(last_log or ""), str(last_seen or ""), s_id),
                )
        except Exception: 
            pass

    def update_server_expiry(self, s_id, new_date):
        self.execute('UPDATE servers SET expiry_date = %s WHERE id = %s', (new_date, s_id))

    def get_or_create_agent_token(self, ip, ssh_port=None, ws_port=None):
        import secrets
        from core import sec
        from access_policy import valid_agent_port
        ssh_port, ws_port = int(ssh_port or 22), valid_agent_port(ws_port or AGENT_PORT)
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT id FROM servers WHERE ip=%s AND port=%s ORDER BY id LIMIT 1 FOR UPDATE', (str(ip),ssh_port))
            if not cur.fetchone():
                raise LookupError('Server must be saved before installing the agent')
            cur.execute("""INSERT INTO agent_nodes(ip,ssh_port,ws_port,agent_token)
                VALUES(%s,%s,%s,%s) ON CONFLICT(ip,ssh_port) DO NOTHING""",
                (str(ip),ssh_port,ws_port,sec.encrypt(secrets.token_urlsafe(48))))
            cur.execute('SELECT id,agent_token FROM agent_nodes WHERE ip=%s AND ssh_port=%s FOR UPDATE', (str(ip),ssh_port))
            row=cur.fetchone()
            token=sec.decrypt(row['agent_token'])
            if not token:
                raise ValueError('AGENT_KEY_DECRYPT_FAILED: restore the correct secret.key before reinstalling')
            cur.execute('UPDATE servers SET agent_id=%s WHERE ip=%s AND port=%s', (row['id'],str(ip),ssh_port))
            conn.commit()
            return token

    def get_agent_token(self, ip, ws_port=None):
        return self.get_agent_transport(ip,ws_port or AGENT_PORT)[0]

    def store_agent_certificate(self, ip, ssh_port, certificate, ws_port=None):
        from cryptography import x509
        x509.load_pem_x509_certificate(certificate.encode())
        if len(certificate)>12000:
            raise ValueError('Invalid TLS certificate')
        with self.get_connection() as (conn,cur):
            cur.execute("""UPDATE agent_nodes SET agent_cert_pem=%s,ws_port=COALESCE(%s,ws_port)
                WHERE ip=%s AND ssh_port=%s RETURNING id,ws_port""", (certificate,ws_port,str(ip),int(ssh_port)))
            row=cur.fetchone()
            if not row:
                raise LookupError('Agent must be registered before certificate enrollment')
            cur.execute("""UPDATE servers SET agent_id=%s,ws_port=%s,agent_cert_pem=%s,
                agent_token=(SELECT agent_token FROM agent_nodes WHERE id=%s)
                WHERE ip=%s AND port=%s""", (row['id'],row['ws_port'],certificate,row['id'],str(ip),int(ssh_port)))
            conn.commit()

    def get_agent_transport(self, ip, ws_port):
        from core import sec
        rows=self.execute("""SELECT agent_token,agent_cert_pem FROM agent_nodes
            WHERE ip=%s AND ws_port=%s AND agent_cert_pem<>''""", (str(ip),int(ws_port or AGENT_PORT)),fetch='all')
        if not rows:
            return '', ''
        if len(rows)!=1:
            raise LookupError('AMBIGUOUS_AGENT: use a distinct WSS port for each SSH endpoint')
        row=rows[0]
        token=sec.decrypt(row['agent_token'])
        if not token:
            raise ValueError('AGENT_KEY_DECRYPT_FAILED')
        return token,row['agent_cert_pem']

    def owns_resource(self, table, resource_id, owner_id):
        owner_id = self.workspace_owner(owner_id)
        if table not in ('servers', 'tunnel_configs', 'groups', 'channels'):
            return False
        return bool(self.execute(
            f"SELECT 1 AS allowed FROM {table} WHERE id=%s AND owner_id=%s",
            (int(resource_id), int(owner_id)), fetch='one'))

    def update_server_ws_port(self, server_id, ws_port, owner_id):
        owner_id = self.workspace_owner(owner_id)
        from access_policy import valid_agent_port
        ws_port=valid_agent_port(ws_port)
        with self.get_connection() as (conn,cur):
            cur.execute('UPDATE servers SET ws_port=%s WHERE id=%s AND owner_id=%s', (ws_port,int(server_id),int(owner_id)))
            changed=cur.rowcount
            conn.commit()
            return changed==1

    def toggle_server_active(self, s_id, current_state):
        new_state = 0 if current_state else 1
        self.execute('UPDATE servers SET is_active = %s WHERE id = %s', (new_state, s_id))
        return new_state

    def get_servers_by_group(self, owner_id, group_id):
        owner_id = self.workspace_owner(owner_id)
        with self.get_connection() as (conn, cur):
            if group_id == 0:
                sql = 'SELECT * FROM servers WHERE owner_id = %s AND group_id IS NULL'
                cur.execute(sql, (owner_id,))
            else:
                sql = 'SELECT * FROM servers WHERE owner_id = %s AND group_id = %s'
                cur.execute(sql, (owner_id, group_id))
            return cur.fetchall()

    # ==========================================================================
    # 📊 STATS & GROUP METHODS
    # ==========================================================================
    
    def add_server_stat(self, server_id, cpu, ram):
        with self.get_connection() as (conn, cur):
            cur.execute('INSERT INTO server_stats (server_id, cpu, ram) VALUES (%s, %s, %s)', (server_id, cpu, ram))
            conn.commit()

    def persist_server_observation(self, server, stats):
        online=stats.get('status')=='Online'
        with self.get_connection() as (conn,cur):
            cur.execute("""UPDATE servers SET last_status=%s,last_error=%s,last_cpu=%s,last_ram=%s,
                last_disk=%s,last_agent_status=%s,last_checked_at=NOW()
                WHERE id=%s AND owner_id=%s RETURNING id""",
                ('Online' if online else 'Offline',str(stats.get('error') or stats.get('agent_error') or '')[:1000],
                 stats.get('cpu') if online else None,stats.get('ram') if online else None,
                 stats.get('disk') if online else None,stats.get('agent_status','Offline'),server['id'],server['owner_id']))
            if cur.fetchone() and online:
                cur.execute('INSERT INTO server_stats(server_id,cpu,ram) VALUES(%s,%s,%s)',
                    (server['id'],stats.get('cpu',0),stats.get('ram',0)))
            cur.execute("DELETE FROM server_stats WHERE created_at<NOW()-INTERVAL '1 day'")
            conn.commit()

    def get_server_stats(self, server_id):
        return self.execute("""SELECT cpu,ram,
            to_char(created_at+INTERVAL '3 hours 30 minutes','HH24:MI') time_str
            FROM server_stats WHERE server_id=%s AND created_at>=NOW()-INTERVAL '1 day'
            ORDER BY created_at ASC""",(int(server_id),),fetch='all')

    def add_group(self, owner_id, name):
        owner_id = self.workspace_owner(owner_id)
        try:
            self.execute('INSERT INTO groups (owner_id, name) VALUES (%s,%s)', (owner_id, name))
            return True
        except psycopg2.IntegrityError:  
            return False

    def get_user_groups(self, owner_id):
        owner_id = self.workspace_owner(owner_id)
        with self. get_connection() as (conn, cur):
            cur.execute('SELECT * FROM groups WHERE owner_id = %s', (owner_id,))
            return cur.fetchall()

    def delete_group(self, group_id, owner_id):
        owner_id = self.workspace_owner(owner_id)
        with self.get_connection() as (conn, cur):
            cur.execute('DELETE FROM groups WHERE id = %s AND owner_id = %s', (group_id, owner_id))
            cur.execute('UPDATE servers SET group_id = NULL WHERE group_id = %s AND owner_id = %s', (group_id, owner_id)) 
            conn.commit()

    # ==========================================================================
    # ⚙️ SETTINGS & CHANNELS
    # ==========================================================================
    
    def get_setting(self, owner_id, key):
        owner_id = self.workspace_owner(owner_id)
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT value FROM settings WHERE owner_id = %s AND key = %s', (owner_id, key))
            res = cur.fetchone()
            return res['value'] if res else None

    def set_setting(self, owner_id, key, value):
        owner_id = self.workspace_owner(owner_id)
        with self.get_connection() as (conn, cur):
            cur.execute('''
                INSERT INTO settings (owner_id, key, value) VALUES (%s, %s, %s) 
                ON CONFLICT (owner_id, key) DO UPDATE SET value = EXCLUDED.value
            ''', (owner_id, key, str(value)))
            conn.commit()

    def get_user_channels(self, owner_id):
        owner_id = self.workspace_owner(owner_id)
        with self.get_connection() as (conn, cur):
            cur.execute('SELECT * FROM channels WHERE owner_id = %s', (owner_id,))
            return cur.fetchall()

    def add_channel(self, owner_id, chat_id, name, usage_type='all', topic_id=0):
        owner_id = self.workspace_owner(owner_id)
        with self.get_connection() as (conn, cur):
            cur.execute('DELETE FROM channels WHERE owner_id=%s AND usage_type=%s', (owner_id, usage_type))
            cur.execute('INSERT INTO channels (owner_id, chat_id, name, usage_type, topic_id) VALUES (%s,%s,%s,%s,%s)', (owner_id, chat_id, name, usage_type, topic_id))
            conn.commit()
            
    def delete_channel(self, c_id, owner_id):
        owner_id = self.workspace_owner(owner_id)
        self.execute('DELETE FROM channels WHERE id = %s AND owner_id = %s', (c_id, owner_id))

    # ==========================================================================
    # 💳 PAYMENT & TUNNEL METHODS
    # ==========================================================================
    
    def get_payment_methods(self, p_type=None):
        with self.get_connection() as (conn, cur):
            if p_type:  
                cur.execute('SELECT * FROM payment_methods WHERE type = %s AND is_active = 1', (p_type,))
            else:  
                cur.execute('SELECT * FROM payment_methods')
            return cur.fetchall()
            
    def add_payment_method(self, p_type, network, address, holder):
        self.execute('INSERT INTO payment_methods (type, network, address, holder_name) VALUES (%s, %s, %s, %s)', (p_type, network, address, holder))

    def delete_payment_method(self, p_id):
        self.execute('DELETE FROM payment_methods WHERE id = %s', (p_id,))

    def create_payment(self, user_id, plan_type, amount, method):
        now = self.get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
        with self.get_connection() as (conn, cur):
            cur.execute(
                'INSERT INTO payments (user_id, plan_type, amount, method, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id',
                (user_id, plan_type, amount, method, now)
            )
            pid = cur.fetchone()['id']
            conn.commit()
            return pid

    def approve_payment(self, payment_id):
        with self. get_connection() as (conn, cur):
            cur.execute('SELECT * FROM payments WHERE id = %s', (payment_id,))
            pay = cur.fetchone()
            if not pay or pay['status'] == 'approved':  
                return False
            
            cur.execute("UPDATE payments SET status = 'approved' WHERE id = %s", (payment_id,))
            
            plan = SUBSCRIPTION_PLANS.get(pay['plan_type'])
            if plan:
                cur.execute('SELECT * FROM users WHERE user_id = %s', (pay['user_id'],))
                user = cur.fetchone()
                try:
                    current_exp_str = user['expiry_date']
                    if current_exp_str: 
                        current_exp = datetime.strptime(current_exp_str, '%Y-%m-%d %H:%M:%S')
                        if current_exp < self.get_tehran_datetime().replace(tzinfo=None): 
                            current_exp = self.get_tehran_datetime().replace(tzinfo=None)
                    else:
                        current_exp = self.get_tehran_datetime().replace(tzinfo=None)
                except:
                    current_exp = self.get_tehran_datetime().replace(tzinfo=None)
                
                new_exp = (current_exp + timedelta(days=plan['days'])).strftime('%Y-%m-%d %H:%M:%S')
                p_type_code = 1 if pay['plan_type'] == 'bronze' else 2 if pay['plan_type'] == 'silver' else 3
                
                cur.execute('''
                    UPDATE users 
                    SET server_limit = %s, expiry_date = %s, plan_type = %s 
                    WHERE user_id = %s
                ''', (plan['limit'], new_exp, p_type_code, pay['user_id']))
                
            conn.commit()
            return pay['user_id'], plan['name']

    def delete_tunnel_config(self, cid, owner_id):
        owner_id = self.workspace_owner(owner_id)
        self.execute('DELETE FROM tunnel_configs WHERE id = %s AND owner_id = %s', (cid, owner_id))

    def apply_referral_reward(self, inviter_id):
        user = self.get_user(inviter_id)
        if not user: 
            return False, 0, ""
        
        new_limit = user['server_limit'] + 1
        try:
            current_exp = datetime.strptime(user['expiry_date'], '%Y-%m-%d %H:%M:%S')
            if current_exp < self.get_tehran_datetime().replace(tzinfo=None): 
                current_exp = self.get_tehran_datetime().replace(tzinfo=None)
            new_exp = (current_exp + timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
        except:
            new_exp = (self.get_tehran_datetime().replace(tzinfo=None) + timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')

        bonus_expiry = (self.get_tehran_datetime().replace(tzinfo=None) + timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
        now_str = self.get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')

        with self.get_connection() as (conn, cur):
            cur.execute('''
                UPDATE users 
                SET server_limit = %s, expiry_date = %s, referral_count = referral_count + 1 
                WHERE user_id = %s
            ''', (new_limit, new_exp, inviter_id))
            
            cur.execute('''
                INSERT INTO temp_bonuses (user_id, bonus_limit, created_at, expires_at)
                VALUES (%s, 1, %s, %s)
            ''', (inviter_id, now_str, bonus_expiry))
            conn.commit()
            
        return True, new_limit, new_exp
