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
 'servers': ('🖥 <b>مرکز مدیریت سرورها</b>\nسرور جدید اضافه کن، گروه‌ها را مدیریت کن، وضعیت و منابع را ببین.',
    [('➕ افزودن سرور','add_server'),('📂 گروه‌ها و سرورها','list_groups_for_servers'),('📊 داشبورد سرورها','status_dashboard'),('🔧 عملیات گروهی','global_ops_menu'),('⚙️ تنظیمات منابع','settings_thresholds')]),
 'configs': ('🧩 <b>مرکز کانفیگ و اشتراک</b>\nثبت و تست کانفیگ، سابسکریپشن و تاریخچه کیفیت.',
    [('➕ افزودن کانفیگ / ساب','add_tunnel_config'),('📋 فهرست کانفیگ‌ها','tunnel_list_menu'),('🏆 رتبه‌بندی کیفیت','pro_rank'),('📈 گزارش تست‌ها','pro_report'),('🇮🇷 نود مانیتورینگ','monitor_settings_panel')]),
 'monitor': ('📡 <b>مانیتورینگ و هشدار</b>\nوضعیت لحظه‌ای، رخدادها و زمان‌بندی خودکار.',
    [('📊 وضعیت شبکه','status_dashboard'),('🚨 رخدادهای شبکه','pro_incidents'),('📅 وظایف زمان‌بندی‌شده','cronjob_hub_main'),('🔔 تنظیمات اعلان‌ها','advanced_monitoring_settings'),('📑 گزارش‌های پیشرفته','pro_home')]),
 'tools': ('🧰 <b>ابزارها و تنظیمات</b>\nابزار شبکه، DNS، زمان‌بندی و اطلاعات پنل.',
    [('📍 تست Ping','manual_ping_start'),('⚙️ تنظیمات برنامه','settings_menu'),('📱 پنل وب','gen_web_token'),('🏷 برچسب‌ها','pro_tags'),('👥 تیم و دسترسی‌ها','pro_team')]),
 'account': ('👤 <b>حساب و فضای کاری</b>\nپروفایل، وضعیت اعتبار و عضویت تیمی.',
    [('👤 پروفایل','user_profile'),('💳 خرید و اعتبار','wallet_menu'),('🤝 دعوت دوستان','referral_menu'),('👥 فضای کاری','pro_home'),('📱 پنل وب','gen_web_token')]),
 'admin': ('👑 <b>مرکز مدیریت نسخه ۶</b>\nکاربران، ایجنت‌ها، بکاپ، سرویس‌ها و عیب‌یابی.',
    [('👥 مدیریت کاربران','admin_users_page_1'),('🔎 جست‌وجوی کاربر','admin_search_start'),('🖥 تمام سرورها','admin_all_servers_1'),('🛰 مدیریت ایجنت‌ها','admin_agent_dashboard'),('📦 بکاپ پایگاه داده','admin_backup_get'),('📢 اعلان همگانی','admin_broadcast_start'),('⚙️ مدیریت قدیمی','admin_panel_main'),('🩺 عیب‌یابی نسخه ۶','v6_diag')]),
}


async def dashboard(update, context):
    owner = is_admin(update)
    markup = rows(('🖥 سرورها','v6_servers'),('🧩 کانفیگ و سابسکریپشن','v6_configs'),
                  ('📡 مانیتورینگ و هشدارها','v6_monitor'),('🧰 ابزارها و تنظیمات','v6_tools'),
                  ('👤 حساب کاربری','v6_account'),('🏆 مرکز Pro','pro_home'),
                  *([('👑 مدیریت ربات','v6_admin')] if owner else []),
                  ('🏠 بازگشت به منوی قبلی','main_menu'))
    await show(update,'🦇 <b>SONAR RADAR • VERSION 6</b>\n\nمرکز کنترل یکپارچه سرورها، کانفیگ‌ها، تست‌ها و هشدارها.\nاز دسته‌بندی‌های زیر انتخاب کنید.',markup)


async def section(update, context):
    section_name = update.callback_query.data[3:]
    if section_name not in MENUS:
        await update.callback_query.answer('منوی ناشناخته',show_alert=True);return
    if section_name == 'admin' and not is_admin(update):
        await update.callback_query.answer('فقط مدیر اصلی',show_alert=True);return
    desc, buttons = MENUS[section_name]
    await show(update,desc,rows(*buttons,('⬅️ دسته‌بندی‌ها','v6_home')))


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


def register(app):
    app.add_handler(SecureCommandHandler('menu',dashboard),group=0)
    app.add_handler(SecureCommandHandler('doctor',diag_menu),group=0)
    app.add_handler(SecureCallbackQueryHandler(dashboard,pattern=r'^v6_home$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(section,pattern=r'^v6_(servers|configs|monitor|tools|account|admin)$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_menu,pattern=r'^v6_diag$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_health,pattern=r'^v6_diag_health$'),group=0)
    app.add_handler(SecureCallbackQueryHandler(diag_zip,pattern=r'^v6_diag_zip(?:_agents)?$'),group=0)
