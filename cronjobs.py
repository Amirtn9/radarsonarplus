import logging
import asyncio
import time
import json
import shlex
import socket
import os
import subprocess
import datetime as dt
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import logging

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
# --- Local Modules ---
from alerts import AlertManager
from telegram.ext import ContextTypes
from server_stats import StatsManager
import keyboard
from database import Database
from settings import (
    SUPER_ADMIN_ID, DOWN_RETRY_LIMIT, AGENT_FILE_PATH,
    SUBSCRIPTION_PLANS, DB_NAME, KEY_FILE, DB_CONFIG, AGENT_PORT
)
from core import (
    ServerMonitor, get_jalali_str, extract_safe_json, 
    get_tehran_datetime, sec
)
from cryptography.fernet import Fernet

# ==============================================================================
# ⚙️ CONFIGURATION & GLOBALS
# ==============================================================================
logger = logging.getLogger(__name__)
db = Database()

# 🔧 نسخه ۴.۲: استخر مشترک پروژه (قبلاً یک استخر ۱۰ تایی جدا بود)
from runtime import SHARED_EXECUTOR, run_sync, spawn_background
from notifications import send_notification
EXECUTOR = SHARED_EXECUTOR

SERVER_FAILURE_COUNTS = {}
CPU_ALERT_TRACKER = {}
DAILY_REPORT_USAGE = {}
TUNNEL_FAIL_STREAKS = {}
IS_SYSTEM_INITIALIZED = False
_RESOURCE_RUNNING=set()
_RESOURCE_LIMIT=asyncio.Semaphore(8)
LAST_SERVER_REPORT_MIN = {}
LAST_CONFIG_REPORT_MIN = {}

# ==============================================================================
# 🔄 SCHEDULED JOBS
# ==============================================================================

async def global_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        servers=await run_sync(db.get_active_servers)
        for srv in servers:
            sid=srv['id']
            if sid not in _RESOURCE_RUNNING:
                _RESOURCE_RUNNING.add(sid)
                spawn_background(context,check_single_server_resources(context,dict(srv)),name=f'server-monitor-{sid}')
    except Exception as exc:
        logger.error('Resource scheduler failed: %s',type(exc).__name__)

async def check_single_server_resources(context, srv):
    sid,owner=srv['id'],srv['owner_id']
    try:
        allowed,_=await run_sync(db.check_access,owner,SUPER_ADMIN_ID)
        if not allowed:return
        async with _RESOURCE_LIMIT:
            stats=await StatsManager.check_full_stats(srv['ip'],srv['port'],srv['username'],sec.decrypt(srv.get('password') or ''),ws_port=srv.get('ws_port'))
        await run_sync(db.persist_server_observation,srv,stats)
        settings=(await _settings_snapshot()).get(owner,{})
        down_enabled=settings.get('down_alert_enabled','1')=='1'
        from pro_store import ProStore
        store=ProStore()
        if stats.get('status')!='Online':
            failures=SERVER_FAILURE_COUNTS.get(sid,0)+1
            SERVER_FAILURE_COUNTS[sid]=failures
            if failures==DOWN_RETRY_LIMIT:
                await run_sync(store.incident,owner,'server',sid,'server_down',f'قطع ارتباط با {srv["name"]}','critical',True)
                if down_enabled and await run_sync(store.should_notify,owner,'server_down'):
                    await send_notification(context,owner,f'🚨 قطع ارتباط با سرور {srv["name"]}\n{stats.get("error","Connection failed")}', 'down',db=db)
            return
        previous=SERVER_FAILURE_COUNTS.pop(sid,0)
        if previous>=DOWN_RETRY_LIMIT:
            await run_sync(store.incident,owner,'server',sid,'server_down',f'بازیابی {srv["name"]}','critical',False)
            if down_enabled and await run_sync(store.should_notify,owner,'recovery'):
                await send_notification(context,owner,f'✅ اتصال سرور {srv["name"]} دوباره برقرار شد.','down',db=db)
        exceeded=[]
        for name,default in (('cpu',80),('ram',80),('disk',90)):
            value=stats.get(name)
            try:limit=max(0,min(100,float(settings.get(name+'_threshold',default))))
            except (ValueError,TypeError):limit=default
            if value is not None and float(value)>=limit:
                exceeded.append(f'{name.upper()}: {float(value):.1f}% | حد: {limit:g}%')
        if exceeded:
            now=time.monotonic()
            if now-CPU_ALERT_TRACKER.get(sid,-1000)>=600:
                sent=await send_notification(context,owner,'⚠️ مصرف منابع '+srv['name']+'\n'+'\n'.join(exceeded),'resource',db=db)
                if sent:CPU_ALERT_TRACKER[sid]=now
        else:
            CPU_ALERT_TRACKER.pop(sid,None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error('Resource monitor failed server=%s type=%s',sid,type(exc).__name__)
    finally:
        _RESOURCE_RUNNING.discard(sid)

# Safe, bounded background work; prevent overlapping job runs.
_ACTIVE_JOB_TYPES = set()

async def _settings_snapshot():
    def _query():
        with db.get_connection() as (conn, cur):
            cur.execute("SELECT owner_id,key,value FROM settings")
            return cur.fetchall()
    rows = await asyncio.get_running_loop().run_in_executor(EXECUTOR, _query)
    results = {}
    for row in rows:
        results.setdefault(row['owner_id'], {})[row['key']] = row['value']
    return results


def _datetime_setting(settings, key):
    try:
        return datetime.fromisoformat(settings.get(key, ''))
    except (TypeError, ValueError):
        return datetime.min


async def monitor_tunnels_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodic config testing for users with enabled config report intervals."""
    if 'monitor' in _ACTIVE_JOB_TYPES: return
    _ACTIVE_JOB_TYPES.add('monitor')
    try:
        from tunnel_logic import tunnel_manager
        rows = await asyncio.get_running_loop().run_in_executor(EXECUTOR, _scheduled_items_snapshot)
        settings = await _settings_snapshot()
        now = datetime.now()
        for uid, configs in rows.items():
            allowed, _ = await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.check_access, uid)
            if not allowed: continue
            monitor=await run_sync(tunnel_manager._get_active_monitor,uid)
            if not monitor:continue
            st = settings.get(uid, {})
            try: interval = int(st.get('config_report_interval', '0'))
            except ValueError: continue
            if interval <= 0 or (now - _datetime_setting(st, 'last_config_report_at')).total_seconds() < interval * 60:
                continue
            # Save the timestamp *before* starting, preventing duplicate overlapping jobs.
            await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.set_setting, uid, 'last_config_report_at', now.isoformat())
            try:
                def size_setting(key,default):
                    try:return max(0.0,min(10.0,float(st.get(key,default))))
                    except (ValueError,TypeError):return default
                try:big_interval=max(10,int(st.get('monitor_big_interval','60')))
                except ValueError:big_interval=60
                heavy=(now-_datetime_setting(st,'last_big_test_at')).total_seconds()>=big_interval*60
                size=size_setting('monitor_big_size',10.0) if heavy else size_setting('monitor_small_size',0.5)
                if heavy:await run_sync(db.set_setting,uid,'last_big_test_at',now.isoformat())
                result = await tunnel_manager.process_singles_async(configs, monitor, uid=uid,size=size)
                report=f'📡 گزارش دوره‌ای: {len(result)} کانفیگ سالم از {len(configs)} مورد'
                if len(result)<len(configs):report+='\n⚠️ برخی کانفیگ‌ها ناموفق هستند.'
                try:
                    from pro_store import ProStore
                    ranks=await run_sync(ProStore().rankings,uid,None,1)
                    if ranks:
                        top=ranks[0]
                        report+=f'\n🏆 رتبه اول براساس آخرین سنجش: {top["name"]} | امتیاز {top["score"]}/100'
                        report+='\nℹ️ امتیاز فقط بر مبنای داده‌های موجود است؛ اگر سنجش تازه‌ای نباشد، نتیجه قطعی نیست.'
                except Exception:
                    logger.warning('Config ranking unavailable for scheduled report')
                delivered=await send_notification(context,uid,report,'config_report',db=db)
                if not delivered:
                    logger.warning('Scheduled config report undelivered user=%s',uid)
            except Exception:
                logger.exception('Config monitoring failed for %s', uid)
    finally:
        _ACTIVE_JOB_TYPES.discard('monitor')


def _scheduled_items_snapshot():
    with db.get_connection() as (conn, cur):
        cur.execute("SELECT * FROM tunnel_configs WHERE type IN ('single','json') ORDER BY owner_id,id")
        rows = cur.fetchall()
    by_owner = {}
    for row in rows:
        by_owner.setdefault(row['owner_id'], []).append(row)
    return by_owner


async def _exec_user_servers(uid, action, servers=None):
    if servers is None:
        servers = await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.get_all_user_servers, uid)
    allowed = {
        'update': ('DEBIAN_FRONTEND=noninteractive apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y', 1200),
        'ram': ('sync && echo 3 > /proc/sys/vm/drop_caches', 30),
        'disk': ('apt-get autoremove -y && apt-get clean', 240),
        'full': ('apt-get update -y && apt-get autoremove -y && apt-get clean', 1200),
        'reboot': ('reboot', 30),
    }
    if action not in allowed: raise ValueError('Unsupported global operation')
    cmd, timeout = allowed[action]
    semaphore = asyncio.Semaphore(3)
    async def _one(srv):
        async with semaphore:
            def _owned():
                return db.owns_resource('servers', srv['id'], uid)
            if not await asyncio.get_running_loop().run_in_executor(EXECUTOR, _owned):
                return srv['name'], False, 'ownership denied'
            if not srv.get('is_active'):
                return srv['name'], False, 'inactive'
            try:
                password = sec.decrypt(srv.get('password') or '')
                ok, msg = await ServerMonitor.run_remote_command(srv['ip'], srv['port'], srv['username'], password, cmd, timeout=timeout)
                return srv['name'], ok, msg[-250:]
            except Exception as exc:
                return srv['name'], False, str(exc)[:250]
    return await asyncio.gather(*[_one(srv) for srv in servers])


async def run_global_commands_background(context, uid, servers, action):
    """Implementation of the previously missing bulk-operations handler."""
    try:
        results = await _exec_user_servers(uid, action, servers)
        ok = sum(1 for _, success, _ in results if success)
        detail = '\n'.join(f"{'✅' if good else '❌'} {name}" for name, good, _ in results)
        await context.bot.send_message(uid, f'🌍 نتیجه عملیات {action}: {ok}/{len(results)} موفق\n{detail}'[:4000])
    except Exception:
        logger.exception('Bulk operation failed for %s', uid)
        await context.bot.send_message(uid, '❌ عملیات همگانی ناموفق بود.')


async def auto_scheduler_job(context: ContextTypes.DEFAULT_TYPE):
    """Apply user's configured update and reboot schedules without event-loop blocking."""
    if 'schedule' in _ACTIVE_JOB_TYPES: return
    _ACTIVE_JOB_TYPES.add('schedule')
    try:
        settings = await _settings_snapshot()
        now = get_tehran_datetime().replace(tzinfo=None)
        for uid, st in settings.items():
            allowed, _ = await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.check_access, uid)
            if not allowed: continue
            try:
                interval = int(st.get('auto_update_hours', '0'))
            except ValueError:
                interval = 0
            if interval > 0 and (now - _datetime_setting(st, 'last_auto_update_at')).total_seconds() >= interval*3600:
                await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.set_setting, uid, 'last_auto_update_at', now.isoformat())
                results = await _exec_user_servers(uid, 'update')
                await context.bot.send_message(uid, f'🔄 آپدیت زمان‌بندی‌شده: {sum(ok for _,ok,_ in results)}/{len(results)} سرور')
            reboot = st.get('auto_reboot_config', 'OFF')
            if reboot and reboot != 'OFF':
                try:
                    days_text, hhmm = reboot.split('|')
                    days = int(days_text)
                    scheduled_hour = int(hhmm.split(':')[0])
                    scheduled_minute = int(hhmm.split(':')[1])
                    due = now.replace(hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0)
                    last = _datetime_setting(st, 'last_reboot_at')
                    if days > 0 and 0 <= (now-due).total_seconds() < 180 and (now-last).total_seconds() >= days*86400:
                        await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.set_setting, uid, 'last_reboot_at', now.isoformat())
                        await _exec_user_servers(uid, 'reboot')
                except (ValueError, IndexError):
                    logger.warning('Invalid reboot schedule for %s', uid)
    except Exception:
        logger.exception('Auto scheduler failed')
    finally:
        _ACTIVE_JOB_TYPES.discard('schedule')


async def auto_update_subs_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled subscription refresh; off unless auto_sub_update_hours > 0."""
    if 'subs' in _ACTIVE_JOB_TYPES: return
    _ACTIVE_JOB_TYPES.add('subs')
    try:
        from tunnel_logic import tunnel_manager
        def _load():
            with db.get_connection() as (conn, cur):
                cur.execute("SELECT * FROM tunnel_configs WHERE type='sub_source' ORDER BY owner_id,id")
                return cur.fetchall()
        sources = await asyncio.get_running_loop().run_in_executor(EXECUTOR, _load)
        st_all = await _settings_snapshot()
        now = datetime.now()
        for sub in sources:
            uid = sub['owner_id']
            allowed, _ = await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.check_access, uid)
            if not allowed: continue
            monitor=await run_sync(tunnel_manager._get_active_monitor,uid)
            if not monitor:continue
            st = st_all.get(uid, {})
            try: interval = int(st.get('auto_sub_update_hours', '0'))
            except ValueError: continue
            if interval <= 0 or (now - _datetime_setting(st, f'last_sub_{sub["id"]}')).total_seconds() < interval*3600:
                continue
            await asyncio.get_running_loop().run_in_executor(EXECUTOR, db.set_setting, uid, f'last_sub_{sub["id"]}', now.isoformat())
            try:
                await tunnel_manager.process_sub_async(uid, sub, monitor)
            except Exception:
                logger.exception('Auto sub update failed for %s', sub['id'])
    except Exception:
        logger.exception('Auto subscription job failed')
    finally:
        _ACTIVE_JOB_TYPES.discard('subs')

async def check_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    """بررسی سررسید سرویس‌ها"""
    try:
        loop = asyncio.get_running_loop()
        near_expiry = await loop.run_in_executor(EXECUTOR, db.get_near_expiry_services)
        
        by_owner = {}
        for server in near_expiry:
            by_owner.setdefault(server['owner_id'], []).append(server)
        for owner, servers in by_owner.items():
            msg = '⏳ سرویس‌های در حال انقضا (۳ روز مانده):\n'
            msg += '\n'.join(f'🔸 {s["name"]} - {s["ip"]}' for s in servers)
            await send_notification(context, owner, msg, 'expiry', db=db)
            
    except Exception as e:
        logger.error(f"Expiry Job Error: {e}")

async def check_bonus_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    """Expire bonus capacity exactly once in a locked database transaction."""
    def _expire():
        with db.get_connection() as (conn, cur):
            now = get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
            cur.execute('SELECT id,user_id,bonus_limit FROM temp_bonuses WHERE expires_at <= %s FOR UPDATE', (now,))
            bonuses = cur.fetchall()
            for b in bonuses:
                cur.execute('UPDATE users SET server_limit=GREATEST(0,server_limit-%s) WHERE user_id=%s',
                            (b['bonus_limit'], b['user_id']))
                cur.execute('DELETE FROM temp_bonuses WHERE id=%s', (b['id'],))
            conn.commit()
            return len(bonuses)
    try:
        total = await asyncio.get_running_loop().run_in_executor(EXECUTOR, _expire)
        if total: logger.info('Expired %s bonuses', total)
    except Exception:
        logger.exception('Bonus expiry job failed')

async def auto_backup_send_job(context: ContextTypes.DEFAULT_TYPE):
    """ارسال بکاپ خودکار دیتابیس"""
    if not SUPER_ADMIN_ID: return
    try:
        # ✅ PostgreSQL Backup (pg_dump)
        # DB_NAME اینجا به معنای فایل خروجی بکاپ است.
        now = datetime.now().strftime('%Y%m%d_%H%M%S')
        import tempfile
        fd, backup_path = tempfile.mkstemp(prefix='sonar_auto_backup_', suffix='.dump')
        os.close(fd)

        def _dump():
            env = os.environ.copy()
            env['PGPASSWORD'] = str(DB_CONFIG.get('password', ''))
            cmd = [
                'pg_dump',
                '-h', str(DB_CONFIG.get('host', 'localhost')),
                '-p', str(DB_CONFIG.get('port', '5432')),
                '-U', str(DB_CONFIG.get('user', 'postgres')),
                '-F', 'c',
                '-f', backup_path,
                str(DB_CONFIG.get('dbname', 'postgres')),
            ]
            subprocess.run(cmd, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(EXECUTOR, _dump)

        if os.path.exists(backup_path):
            with open(backup_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=SUPER_ADMIN_ID,
                    document=f,
                    caption=f"📦 **Auto Backup (PostgreSQL)**\n📅 {get_jalali_str()}",
                    filename=os.path.basename(backup_path)
                )
        try:
            os.remove(backup_path)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Backup Job Error: {e}")

# ==============================================================================
# 🚀 STARTUP TASKS (LIGHTWEIGHT)
# ==============================================================================

async def startup_whitelist_job(context: ContextTypes.DEFAULT_TYPE):
    """
    ❌ نسخه قبلی: تلاش برای SSH به همه سرورها (باعث کندی می‌شد)
    ✅ نسخه فعلی: غیرفعال شده تا ربات سریع بالا بیاید.
    """
    logger.info("⏩ Startup Whitelist Job skipped for performance.")
    pass 

async def silent_update_monitor_agent():
    """
    ❌ نسخه قبلی: تلاش برای نصب ایجنت روی همه سرورها (باعث گیر کردن می‌شد)
    ✅ نسخه فعلی: غیرفعال. نصب باید دستی یا از منوی ادمین انجام شود.
    """
    logger.info("⏩ Silent Agent Update skipped for performance.")
    pass

async def send_startup_topic_test(context: ContextTypes.DEFAULT_TYPE):
    """تست ارسال پیام در تاپیک‌ها (اختیاری)"""
    pass

async def system_startup_notification(context: ContextTypes.DEFAULT_TYPE):
    """اعلان روشن شدن ربات به ادمین"""
    global IS_SYSTEM_INITIALIZED
    IS_SYSTEM_INITIALIZED = True
    
    if not SUPER_ADMIN_ID: return
    
    try:
        txt = (
            f"🤖 **ربات سونار رادار (Ultra Pro) آنلاین شد!**\n"
            f"📅 زمان: `{get_jalali_str()}`\n"
            f"✅ وضعیت: **Ready**"
        )
        await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=txt, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Startup Notification Error: {e}")

# ==============================================================================
# 🔔 UTILS
# ==============================================================================

async def alert_admin(context, text):
    """ارسال پیام به ادمین اصلی"""
    if not SUPER_ADMIN_ID: return
    try:
        await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=text, parse_mode='Markdown')
    except: pass

async def server_report_job(context):
    if 'server-report' in _ACTIVE_JOB_TYPES:return
    _ACTIVE_JOB_TYPES.add('server-report')
    try:
        settings=await _settings_snapshot()
        now=get_tehran_datetime().replace(tzinfo=None)
        for uid,values in settings.items():
            try:interval=int(values.get('report_interval','0'))
            except (ValueError,TypeError):continue
            # Values above the UI's maximum are legacy seconds, never legal new minutes.
            if interval>1440 and interval%60==0:interval//=60
            if interval<=0 or (now-_datetime_setting(values,'last_server_report_at')).total_seconds()<interval*60:continue
            allowed,_=await run_sync(db.check_access,uid,SUPER_ADMIN_ID)
            if not allowed:continue
            rows=await run_sync(db.get_all_user_servers,uid)
            active=[s for s in rows if s.get('is_active')]
            if not active:continue
            text='📊 گزارش دوره‌ای سرورها\n'+get_jalali_str()+'\n\n'
            # Ranking uses only recorded CPU/RAM observations, not an invented ping or live test.
            def load(row):
                try:return (float(row['last_cpu'])+float(row['last_ram']))/2
                except (ValueError,TypeError,KeyError):return None
            candidates=[(load(row),row) for row in active if row.get('last_status')=='Online']
            candidates=[(score,row) for score,row in candidates if score is not None]
            if candidates:
                least=min(candidates,key=lambda x:x[0])
                text+=f'🏆 کم‌مصرف‌ترین سرور ثبت‌شده: {least[1]["name"]} | میانگین CPU/RAM: {least[0]:.1f}%\n'
                text+='ℹ️ این رتبه‌بندی صرفاً براساس آخرین مصرف ثبت‌شده است، نه تست زنده سرعت.\n\n'

            for row in active:
                cpu='?' if row.get('last_cpu') is None else f'{row["last_cpu"]:.1f}'
                ram='?' if row.get('last_ram') is None else f'{row["last_ram"]:.1f}'
                text+=f'{row["name"]}: {row["last_status"]} | CPU {cpu}% | RAM {ram}% | Agent {row.get("last_agent_status","Unknown")}\n'
            if await send_notification(context,uid,text,'report',db=db):
                await run_sync(db.set_setting,uid,'last_server_report_at',now.isoformat())
    except Exception as exc:
        logger.error('Server report job failed: %s',type(exc).__name__)
    finally:
        _ACTIVE_JOB_TYPES.discard('server-report')
