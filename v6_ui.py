"""Sonar 6 categorized Telegram navigation + administrator diagnostics.
All management features reuse the existing vetted handler callbacks.
"""
from __future__ import annotations
import asyncio
import html
from pathlib import Path
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from secure_handlers import SecureCallbackQueryHandler, SecureCommandHandler
from settings import SUPER_ADMIN_ID
from diagnostics import snapshot, build_bundle, collect_agent_journals
from runtime import background_callback
from navigation import remember_section, get_section, clear_section, section_parent


def rows(*parts):
    return M([[B(title, callback_data=callback)] for title, callback in parts])


def is_admin(update):
    return bool(SUPER_ADMIN_ID) and update.effective_user and update.effective_user.id == SUPER_ADMIN_ID


async def show(update, text, menu=None):
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=menu)
        except Exception as exc:
            if 'Message is not modified' not in str(exc):
                await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=menu)
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
    [('⏰ زمان‌بندی و هشدارها','menu_schedules'),('🗓 کرون‌جاب‌ها','cronjob_hub_main'),('📢 کانال اعلان‌ها','channels_menu'),('⚙️ همه تنظیمات','settings_menu'),('🇮🇷 سرور مانیتورینگ','monitor_settings_panel')]),
 'account': ('👤 <b>حساب و همکاران</b>\n\nپروفایل، اعتبار و فضای کاری مشترک.',
    [('👤 پروفایل من','user_profile'),('💳 اعتبار و اشتراک','wallet_menu'),('👥 هم‌گروهی و فضای مشترک','ui_team_home'),('🤝 دعوت دوستان','referral_menu'),('📱 پنل وب','gen_web_token'),('💎 مرکز Pro','pro_home')]),
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
        await update.callback_query.answer('منوی ناشناخته',show_alert=True);return
    if section_name == 'admin' and not is_admin(update):
        await update.callback_query.answer('فقط مدیر اصلی',show_alert=True);return
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
        if update.callback_query:await update.callback_query.answer('فقط مدیر اصلی',show_alert=True)
        return
    await show(update,'🩺 <b>مرکز عیب‌یابی Sonar V6</b>\n\n'
               'گزارش سلامت بدون رمزها و توکن‌ها تولید می‌شود. '\
               'بسته ZIP شامل آخرین لاگ‌های ربات، وب، نصب و خطاهاست. '\
               'قبل از ارسال برای پشتیبانی، محتوای بسته را شخصاً بررسی کنید.',
               rows(('🔍 گزارش سریع سلامت','v6_diag_health'),('📤 ساخت و ارسال ZIP لاگ‌ها','v6_diag_zip'),('🛰 ZIP به‌همراه لاگ ۵ ایجنت','v6_diag_zip_agents'),
                    ('🛰 مشاهده لاگ ایجنت‌ها','admin_agent_logs'),('📋 منوی مدیریت','v6_admin')))


async def diag_health(update, context):
    if not is_admin(update):
        if update.callback_query:await update.callback_query.answer('مجاز نیست',show_alert=True)
        return
    snap=await asyncio.to_thread(snapshot)
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


async def diag_zip(update, context):
    if not is_admin(update):
        if update.callback_query:await update.callback_query.answer('مجاز نیست',show_alert=True)
        return
    if update.callback_query:await update.callback_query.answer('در حال جمع‌آوری گزارش امن')
    path=None
    try:
        # Pull ONLY persisted status/last-error fields; never serialize DB credentials.
        from database import Database
        agents=await asyncio.wait_for(asyncio.to_thread(lambda: Database().get_all_servers()),timeout=20)
        remote=None
        if update.callback_query and update.callback_query.data == 'v6_diag_zip_agents':
            remote=await asyncio.wait_for(asyncio.to_thread(collect_agent_journals,agents,5),timeout=180)
        path=await asyncio.wait_for(asyncio.to_thread(build_bundle,agent_rows=agents,remote_logs=remote), timeout=65)
        with path.open('rb') as f:
            await context.bot.send_document(chat_id=update.effective_user.id,document=f,
                     filename='sonar-v6-support.zip',caption='📦 گزارش عیب‌یابی نسخه ۶. پیش از اشتراک‌گذاری فایل را بررسی کنید.',
                     read_timeout=65, write_timeout=65)
    except Exception as exc:
        await context.bot.send_message(chat_id=update.effective_user.id,
                  text=f'❌ تهیه گزارش ناموفق: {html.escape(type(exc).__name__)}')
    finally:
        if path:
            try:Path(path).unlink(missing_ok=True)
            except OSError:pass


async def guide_menu(update, context):
    """Accessible from Telegram, with a full public HTTPS mini-app if deployed."""
    import os
    from urllib.parse import urlsplit
    from telegram import WebAppInfo
    url=os.getenv('SONAR_WEBAPP_URL','').strip()
    parsed=urlsplit(url)
    buttons=[]
    if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
        guide_url=url.rstrip('/').split('?',1)[0]+'/guide'
        buttons.append([B('🌐 باز کردن راهنمای تصویری',web_app=WebAppInfo(url=guide_url))])
    buttons.extend([[B('🖥 شروع افزودن سرور',callback_data='add_server')],
                    [B('⚙️ تنظیم کانال‌ها',callback_data='channels_menu')],
                    [B('⬅️ صفحه اصلی',callback_data='main_menu')]])
    txt=('📚 <b>راهنمای کامل سونار رادار</b>\n\n'
         '🖥 <b>سرورها:</b> از «سرورهای من ← افزودن سرور» نام، IP، پورت SSH، نام کاربری و رمز را مرحله‌به‌مرحله وارد کنید. اثر انگشت SSH را مستقل تأیید کنید.\n\n'
         '🛰 <b>Agent:</b> از صفحه سرور، امکانات پیشرفته ← نصب/رفع ایجنت را بزنید؛ نصب مستقیم، در صورت لزوم بسته آفلاین و سپس صحت TLS/WSS بررسی می‌شود.\n\n'
         '🧩 <b>کانفیگ‌ها:</b> افزودن، مشاهده، تست و رتبه‌بندی بر اساس آخرین داده‌های سنجش.\n\n'
         '📢 <b>اعلان‌ها:</b> در تنظیمات، کانال را ثبت کنید؛ ربات باید مجوز ارسال پیام داشته باشد. سپس نوع هشدار و زمان‌بندی گزارش را انتخاب کنید.\n\n'
         '👥 <b>هم‌گروهی:</b> درخواست با آیدی عددی فرستاده می‌شود و فقط بعد از تأیید همکار، اطلاعات فنی مشترک می‌شود.\n\n'
         '💡 برای آموزش کامل و پرسش‌های متداول، نسخه وب راهنما را باز کنید.')
    await show(update,txt,M(buttons))


def register(app):
    from workspace_ui import register as register_workspace
    register_workspace(app)
    app.add_handler(SecureCommandHandler('menu',dashboard),group=0)
    app.add_handler(SecureCallbackQueryHandler(guide_menu,pattern=r'^v6_guide$'),group=0)
    app.add_handler(SecureCommandHandler('doctor',diag_menu),group=0)
    app.add_handler(SecureCallbackQueryHandler(dashboard,pattern=r'^v6_home$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(return_to_section,pattern=r'^v6_return$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(section,pattern=r'^v6_(servers|configs|monitor|tools|settings|account|admin)$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_menu,pattern=r'^v6_diag$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_health,pattern=r'^v6_diag_health$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(background_callback(diag_zip),pattern=r'^v6_diag_zip(?:_agents)?$'),group=0)
