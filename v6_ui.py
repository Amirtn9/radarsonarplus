"""Sonar 6 categorized Telegram navigation + administrator diagnostics.
All management features reuse the existing vetted handler callbacks.
"""
from __future__ import annotations
import asyncio
import html
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from secure_handlers import SecureCallbackQueryHandler, SecureCommandHandler
from settings import SUPER_ADMIN_ID
from diagnostics import snapshot, build_bundle, collect_agent_journals, collect_operational_state
from runtime import spawn_background, run_sync
from navigation import remember_section, get_section, clear_section, section_parent

logger = logging.getLogger(__name__)

# Diagnostics use their own bounded pool. Long SSH/journal/ZIP work must not queue
# behind ordinary bot database and network jobs in the shared executor.
_DIAG_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix='sonar-diag')
_DIAG_JOB_ACTIVE = False


async def _diag_sync(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_DIAG_EXECUTOR, partial(func, *args, **kwargs))


def rows(*parts):
    return M([[B(title, callback_data=callback)] for title, callback in parts])


def is_admin(update):
    return bool(SUPER_ADMIN_ID) and update.effective_user and update.effective_user.id == SUPER_ADMIN_ID


async def _safe_answer(query, text=None, *, show_alert=False):
    """A stale Telegram callback must never abort the operation behind it."""
    if query is None:
        return False
    try:
        kwargs = {'cache_time': 0, 'show_alert': show_alert}
        if text is not None:
            kwargs['text'] = str(text)[:190]
        await query.answer(**kwargs)
        return True
    except Exception as exc:
        message = str(exc).lower()
        expected = ('query is too old', 'query id is invalid', 'query is not found',
                    'response timeout expired', 'query too old')
        if any(item in message for item in expected):
            logger.debug('Ignoring expired callback acknowledgement')
        else:
            logger.warning('Telegram callback acknowledgement failed: %s', type(exc).__name__)
        return False


async def show(update, text, menu=None):
    if update.callback_query:
        await _safe_answer(update.callback_query)
        try:
            await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=menu)
        except Exception as exc:
            if 'Message is not modified' not in str(exc):
                try:
                    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=menu)
                except Exception:
                    logger.warning('Telegram menu fallback failed: %s', type(exc).__name__)
    else:
        await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=menu)


MENUS = {
 'servers': ('🖥 <b>سرورهای من</b>\n\nاز اینجا می‌توانید سرور اضافه کنید، سرورهای قبلی را ببینید یا آن‌ها را گروه‌بندی کنید.',
    [('➕ افزودن سرور (مرحله‌ای)','add_server'),('📋 مشاهده سرورها','list_groups_for_servers'),('📂 پوشه‌بندی سرورها','groups_menu'),('📊 وضعیت همه سرورها','show_server_stats')]),
 'configs': ('🧩 <b>کانفیگ‌های من</b>\n\nکانفیگ جدید وارد کنید، کانفیگ‌های ذخیره‌شده را ببینید و وضعیتشان را بررسی کنید.',
    [('➕ افزودن کانفیگ','add_tunnel_config'),('📋 مشاهده کانفیگ‌ها','tunnel_list_menu'),('🔍 گزارش سلامت کانفیگ‌ها','show_config_stats'),('🔄 بروزرسانی و تست','mass_update_test_all')]),
 'monitor': ('📡 <b>وضعیت و هشدارها</b>\n\nسلامت سرورها، قطعی‌ها و هشدارهای مهم را ببینید.',
    [('📊 وضعیت سرورها','status_dashboard'),('🚨 هشدارها و رخدادها','pro_incidents'),('🔔 تنظیم هشدار','menu_schedules'),('📈 گزارش پیشرفته','pro_report')]),
 'tools': ('🧰 <b>ابزارها</b>\n\nبرای بررسی ارتباط، شبکه و کیفیت سرویس‌ها.',
    [('📍 تست Ping','manual_ping_start'),('🔧 عملیات گروهی سرورها','global_ops_menu'),('🏆 کیفیت کانفیگ‌ها','pro_rank'),('🏷 برچسب‌ها','pro_tags')]),
 'settings': ('⚙️ <b>تنظیمات</b>\n\nزمان‌بندی، کانال اعلان و تنظیمات خودکار را از همین بخش کنترل کنید.',
    [('🔔 اعلان کانفیگ‌ها','ca_home'),('⏰ زمان‌بندی و هشدارها','menu_schedules'),('🗓 کرون‌جاب‌ها','cronjob_hub_main'),('📢 کانال اعلان‌ها','channels_menu'),('⚙️ همه تنظیمات','settings_menu'),('🇮🇷 سرور مانیتورینگ','monitor_settings_panel')]),
 'account': ('👤 <b>حساب و همکاران</b>\n\nپروفایل، اعتبار و فضای کاری مشترک.',
    [('👤 پروفایل من','user_profile'),('💳 اعتبار و اشتراک','wallet_menu'),('👥 هم‌گروهی و فضای مشترک','ui_team_home'),('🤝 دعوت دوستان','referral_menu'),('🌐 کد ورود وب‌سایت','gen_web_token'),('💎 مرکز Pro','pro_home')]),
 'admin': ('👑 <b>مدیریت ربات</b>\n\nاین بخش فقط برای مدیر اصلی است.',
    [('👥 مدیریت کاربران','admin_users_page_1'),('🔎 جست‌وجوی کاربر','admin_search_start'),('🖥 سرورهای کاربران','admin_all_servers_1'),('🛰 مدیریت ایجنت‌ها','admin_agent_dashboard'),('📊 ترافیک Agentها','fleet_traffic'),('📦 پشتیبان‌گیری','admin_backup_get'),('📢 پیام همگانی','admin_broadcast_start'),('⚙️ تنظیمات مدیریت','admin_panel_main'),('🩺 عیب‌یابی','v6_diag')]),
}


async def dashboard(update, context):
    from bot_logic_mod.menu import show_main_menu
    clear_section(context)
    await show_main_menu(update,context)


async def section(update, context):
    section_name = update.callback_query.data[3:]
    if section_name not in MENUS:
        await _safe_answer(update.callback_query, 'منوی ناشناخته', show_alert=True);return
    if section_name == 'admin' and not is_admin(update):
        await _safe_answer(update.callback_query, 'فقط مدیر اصلی', show_alert=True);return
    remember_section(context, section_name)
    desc, buttons = MENUS[section_name]
    await show(update,desc,rows(*buttons,('🏠 صفحه اصلی','main_menu')))


async def return_to_section(update, context):
    """Return to the category that launched this flow, not the global categories page."""
    target = get_section(context)
    if target == 'admin' and not is_admin(update):
        clear_section(context)
        target = None
    if target not in MENUS:
        # A user may visit a legacy screen via a /command, bypassing a section.
        from bot_logic import show_main_menu
        await show_main_menu(update, context)
        return
    desc, buttons = MENUS[target]
    await show(update, desc, rows(*buttons, ('🏠 صفحه اصلی','main_menu')))


async def diag_menu(update, context):
    if not is_admin(update):
        if update.callback_query:await _safe_answer(update.callback_query, 'فقط مدیر اصلی', show_alert=True)
        return
    await show(update,'🩺 <b>مرکز عیب‌یابی Sonar V6</b>\n\n'
               'گزارش سلامت بدون رمزها و توکن‌ها تولید می‌شود. '\
               'بسته ZIP شامل آخرین لاگ‌های ربات، وب، نصب و خطاهاست. '\
               'قبل از ارسال برای پشتیبانی، محتوای بسته را شخصاً بررسی کنید.',
               rows(('🔍 گزارش سریع سلامت','v6_diag_health'),('📤 ساخت و ارسال ZIP لاگ‌ها','v6_diag_zip'),
                    ('🛰 مشاهده لاگ ایجنت‌ها','admin_agent_logs'),('📋 منوی مدیریت','v6_admin')))


async def diag_health(update, context):
    if not is_admin(update):
        if update.callback_query:await _safe_answer(update.callback_query, 'مجاز نیست', show_alert=True)
        return
    snap=await run_sync(snapshot)
    label=lambda val: '✅' if val else '❌'
    service_text='\n'.join(f'• {html.escape(name)}: {html.escape(str(data).splitlines()[1] if len(str(data).splitlines())>1 else str(data))[:100]}'
                           for name,data in snap['services'].items())
    content=(f'🩺 <b>گزارش سلامت V{html.escape(snap["project_version"])}</b>\n'
             f'🐍 Python: {html.escape(snap["python"])}\n'
             f'{label(snap["token_configured"])} توکن ربات تنظیم شده\n'
             f'{label(snap["admin_configured"])} مدیر تنظیم شده\n'
             f'📦 فضای خالی: {snap["disk_free_mb"]} MB\n'
             f'🌐 Web: {html.escape(str(snap["web_local_tcp"]))}\n'
             f'⚙️ Config mode: {html.escape(str(snap["config_mode"]))}\n'
             f'{service_text}\n\n'
             'این گزارش آزمون واقعی اتصال به VPSها نیست.')
    await show(update,content, rows(('📤 دریافت ZIP کامل','v6_diag_zip'),('⬅️ مرکز عیب‌یابی','v6_diag')))


async def diag_zip_entry(update, context):
    """Acknowledge Telegram callbacks before spawning slow diagnostic work."""
    if not is_admin(update):
        if update.callback_query:
            await _safe_answer(update.callback_query, 'فقط مدیر اصلی اجازه دارد.', show_alert=True)
        else:
            try:
                await context.bot.send_message(chat_id=update.effective_user.id, text='فقط مدیر اصلی اجازه دارد.')
            except Exception:
                pass
        return
    data=getattr(getattr(update,'callback_query',None),'data','') or ''
    if data in ('v6_diag_zip','v6_diag_zip_agents'):
        await _safe_answer(update.callback_query)
        await show(update,'بازه زمانی لاگ‌ها را انتخاب کنید. یک ZIP شامل لاگ سرور اصلی و همه ایجنت‌های ثبت‌شده ساخته می‌شود.',
            rows(*[(label, f'v6_diag_zip_h{hours}') for hours,label in
                   ((3,'۳ ساعت اخیر'),(6,'۶ ساعت اخیر'),(12,'۱۲ ساعت اخیر'),
                    (24,'۲۴ ساعت اخیر'),(48,'۴۸ ساعت اخیر'),(72,'۷۲ ساعت اخیر'),(168,'۷ روز اخیر'))],
                 ('⬅️ مرکز عیب‌یابی','v6_diag')))
        return
    if data not in {f'v6_diag_zip_h{h}' for h in (3,6,12,24,48,72,168)}:
        return
    if update.callback_query:
        # This is deliberately before create_task: callbacks can expire while the
        # event loop is busy, and an acknowledgement error must not abort ZIP export.
        await _safe_answer(update.callback_query, 'در حال جمع‌آوری گزارش امن')
    spawn_background(context, diag_zip(update, context), name='diag-zip')


async def diag_zip(update, context):
    """Build and send the full diagnostics archive without blocking the bot loop.

    Collection is best-effort by component: a database timeout or offline Agent is
    recorded inside the ZIP instead of aborting the entire local-log export.
    """
    global _DIAG_JOB_ACTIVE
    if not is_admin(update):
        return
    if _DIAG_JOB_ACTIVE:
        try:
            await context.bot.send_message(chat_id=update.effective_user.id,
                text='⏳ یک گزارش عیب‌یابی در حال تهیه است؛ پس از پایان دوباره تلاش کنید.')
        except Exception:
            pass
        return
    _DIAG_JOB_ACTIVE = True

    from settings import PROJECT_VERSION
    data=getattr(getattr(update,'callback_query',None),'data','') or ''
    hours=int(data.rsplit('_h',1)[1]) if '_h' in data else 168
    if hours not in (3,6,12,24,48,72,168):return
    days=hours/24
    path = None
    stage = 'database_connect'
    chat_id = update.effective_user.id
    progress_message = None
    collection_errors = {}
    agents = []
    state = {}
    remote = {}

    async def progress(text):
        nonlocal progress_message
        try:
            if progress_message is None:
                progress_message = await context.bot.send_message(chat_id=chat_id, text=text)
            else:
                await progress_message.edit_text(text)
        except Exception as exc:
            logger.debug('Diagnostics progress update failed: %s', type(exc).__name__)

    try:
        await progress('⏳ گزارش عیب‌یابی آغاز شد؛ لاگ‌های منبع حذف یا کوتاه نمی‌شوند.')
        db = None
        try:
            from database import Database
            db = await _diag_sync(Database)
        except Exception as exc:
            collection_errors['database_connect'] = type(exc).__name__
            logger.warning('Diagnostics DB connection unavailable: %s', type(exc).__name__)

        if db is not None:
            stage = 'server_inventory'
            try:
                agents = await asyncio.wait_for(_diag_sync(db.get_all_servers), timeout=60)
            except Exception as exc:
                collection_errors['server_inventory'] = type(exc).__name__
                logger.warning('Diagnostics server inventory unavailable: %s', type(exc).__name__)

            stage = 'operational_state'
            try:
                state = await asyncio.wait_for(_diag_sync(collect_operational_state, db, days), timeout=90)
                if not isinstance(state, dict):
                    state = {'result': 'invalid_operational_state_shape'}
            except Exception as exc:
                collection_errors['operational_state'] = type(exc).__name__
                state = {}
                logger.warning('Diagnostics operational state unavailable: %s', type(exc).__name__)

            stage = 'remote_agent_logs'
            await progress(f'⏳ جمع‌آوری لاگ‌های {hours} ساعت اخیر؛ {len(agents)} سرور ثبت‌شده بررسی می‌شود.')
            try:
                # The collector has a per-host/global deadline and emits a status line
                # for each offline or timed-out Agent. Do not wrap it in a short timeout.
                remote = await _diag_sync(collect_agent_journals, agents, None, days,
                                          timeout_seconds=420, max_workers=16)
            except Exception as exc:
                collection_errors['remote_agent_logs'] = type(exc).__name__
                remote = {'0': 'REMOTE_AGENT_COLLECTION_FAILED: ' + type(exc).__name__ + '\n'}
                logger.warning('Diagnostics remote Agent collection unavailable: %s', type(exc).__name__)
        else:
            remote = {'0': 'REMOTE_AGENT_COLLECTION_SKIPPED: database unavailable\n'}

        state = dict(state or {})
        if collection_errors:
            state['diagnostic_collection_errors'] = collection_errors
        stage = 'build_zip'
        await progress('⏳ بسته‌بندی لاگ‌های ربات، پنل، نصب، وضعیت دیتابیس و Agentها...')
        path = await _diag_sync(build_bundle, agent_rows=agents, remote_logs=remote,
                                operational_state=state, days=days)
        size = path.stat().st_size
        if size > 45 * 1024 * 1024:
            await context.bot.send_message(chat_id=chat_id,
                text=('گزارش کامل ساخته شد اما از سقف ارسال فایل تلگرام بزرگ‌تر است. '
                      'از پنل وب ← عیب‌یابی مدیر، بسته با همین بازه را دانلود کنید. '
                      'جزئیات خطاهای جمع‌آوری در state/operational-state.json قرار دارد.'))
            return

        stage = 'telegram_upload'
        await progress(f'📦 ZIP آماده شد ({size / (1024 * 1024):.1f} MiB)؛ در حال ارسال...')
        # Retry only transient network timeouts. Each attempt gets a fresh file handle.
        from telegram.error import NetworkError
        uploaded = False
        for attempt in range(2):
            try:
                with path.open('rb') as handle:
                    await context.bot.send_document(
                        chat_id=chat_id, document=handle,
                        filename=f'radarsonarplus-v{PROJECT_VERSION}-full-logs-{hours}h.zip',
                        caption=(f'گزارش {hours} ساعت اخیر ربات، پنل، نصب، وضعیت عملیاتی و همه Agentها. '
                                 'مقادیر محرمانه پوشانده شده‌اند؛ لاگ‌های اصلی دست‌نخورده مانده‌اند.'),
                        read_timeout=600, write_timeout=600, connect_timeout=30, pool_timeout=60)
                uploaded = True
                break
            except (NetworkError, asyncio.TimeoutError, TimeoutError) as exc:
                if attempt:
                    raise
                logger.warning('Diagnostics Telegram upload timed out; retrying once (%s)', type(exc).__name__)
                await progress('⚠️ ارسال گزارش با وقفه شبکه روبه‌رو شد؛ یک بار دیگر تلاش می‌کنم...')
                await asyncio.sleep(2)
        if uploaded and progress_message:
            try:
                await progress_message.edit_text('✅ گزارش کامل ارسال شد.')
            except Exception:
                pass
    except Exception as exc:
        # Keep the stage in the log so a partial failure can be diagnosed without
        # exposing credentials or raw command output to Telegram.
        logger.exception('Diagnostics export failed at stage=%s type=%s', stage, type(exc).__name__)
        try:
            await context.bot.send_message(chat_id=chat_id,
                text=f'❌ تهیه گزارش ناموفق شد. مرحله: {stage} | خطا: {html.escape(type(exc).__name__)}')
        except Exception:
            logger.warning('Unable to deliver diagnostics failure notice')
    finally:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        _DIAG_JOB_ACTIVE = False


async def guide_menu(update, context):
    from guide_content import content
    from urllib.parse import urlsplit, urlunsplit
    from telegram import WebAppInfo
    import os
    chapters=content(is_admin(update))['chapters']
    parsed=urlsplit(os.getenv('SONAR_WEBAPP_URL','').strip())
    buttons=[]
    if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
        guide_url=urlunsplit((parsed.scheme,parsed.netloc,'/guide','v=6.9.10',''))
        buttons.append([B('📚 آکادمی تعاملی؛ آموزش کامل و فرهنگ دکمه‌ها',web_app=WebAppInfo(url=guide_url))])
    for item in chapters:
        buttons.append([B(item['title'],callback_data=f"v6_lesson_{item['id']}_0")])
    buttons.append([B('⬅️ صفحه اصلی',callback_data='main_menu')])
    text=('📚 <b>آکادمی سونار رادار</b>\n\n'
          'آموزش‌ها به ترتیب از شروع کار تا اعلان و نگهداری دسته‌بندی شده‌اند. '
          'در هر آموزش، اقدام، نتیجه مورد انتظار و نکته‌های مهم را جدا می‌بینید.\n\n'
          '🌐 راهنمای کامل: دسته‌بندی موضوعی، جست‌وجو و توضیح دکمه‌ها.\n'
          '💬 نسخه ربات: یکی از دسته‌های زیر را انتخاب کنید.\n\n'
          'ورود Mini App داخل تلگرام خودکار است؛ کد یک‌بارمصرف فقط برای ورود مستقل وب است.')
    await show(update,text,M(buttons))


async def guide_lesson(update,context):
    from guide_content import content
    import html
    tail=update.callback_query.data.removeprefix('v6_lesson_')
    key, raw_page=tail.rsplit('_',1)
    chapter=next((item for item in content(is_admin(update))['chapters'] if item['id']==key),None)
    if chapter is None:
        await _safe_answer(update.callback_query,'این آموزش برای حساب شما در دسترس نیست.',show_alert=True)
        return
    page=int(raw_page); lessons=chapter['lessons']; per_page=3
    if page<0 or page*per_page>=len(lessons):
        return
    text='📚 <b>'+html.escape(chapter['title'])+'</b>\n'+html.escape(chapter['summary'])+'\n\n'
    for lesson in lessons[page*per_page:(page+1)*per_page]:
        text+='🔹 <b>'+html.escape(lesson['button'])+'</b>\n'+html.escape(lesson['action'])+'\n↳ '+html.escape(lesson['result'])+'\n\n'
    pagination=[]
    if page:pagination.append(B('قبلی',callback_data=f'v6_lesson_{key}_{page-1}'))
    if (page+1)*per_page<len(lessons):pagination.append(B('بعدی',callback_data=f'v6_lesson_{key}_{page+1}'))
    buttons=([pagination] if pagination else [])+[[B('📚 دسته‌های آموزش',callback_data='v6_guide')]]
    await show(update,text,M(buttons))


def register(app):
    from workspace_ui import register as register_workspace
    register_workspace(app)
    app.add_handler(SecureCommandHandler('menu',dashboard),group=0)
    app.add_handler(SecureCallbackQueryHandler(guide_menu,pattern=r'^v6_guide$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(guide_lesson,pattern=r'^v6_lesson_[a-z-]+_\d+$'),group=0)
    app.add_handler(SecureCommandHandler('doctor',diag_menu),group=0)
    app.add_handler(SecureCallbackQueryHandler(dashboard,pattern=r'^v6_home$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(return_to_section,pattern=r'^v6_return$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(section,pattern=r'^v6_(servers|configs|monitor|tools|settings|account|admin)$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_menu,pattern=r'^v6_diag$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_health,pattern=r'^v6_diag_health$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_zip_entry,pattern=r'^v6_diag_zip(?:_agents|_h(?:3|6|12|24|48|72|168))?$'),group=0)
