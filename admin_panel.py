"""Admin Panel Handlers for Sonar Radar Ultra Pro. 

This module handles all admin-related operations including:
- User management (create, ban, delete, upgrade)
- Server monitoring and reporting
- Payment settings
- Database backups
- Broadcasting messages
"""

import logging
from telegram_safety import safe_callback_answer
import asyncio
import os
import json
from datetime import datetime, timedelta
import jdatetime
from concurrent.futures import ThreadPoolExecutor

# --- Telegram Imports ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

# --- Local Modules ---
import keyboard
from states import *
from database import Database
from settings import SUPER_ADMIN_ID, KEY_FILE, AGENT_PORT
from ws_client import reconnect_all as ws_reconnect_all
from core import ServerMonitor, get_jalali_str, get_tehran_datetime, sec
from server_stats import StatsManager
from scoring import ScoreEngine
from cryptography.fernet import Fernet

# ✅ یکبار setup logging (در اول فایل، بعد از imports)
logger = logging.getLogger(__name__)

# 🟢 Database instance
db = Database()

# 🔧 نسخه ۴.۲: استخر مشترک پروژه (قبلاً یک استخر ۱۰ تایی جدا بود)
from runtime import SHARED_EXECUTOR, run_sync, spawn_background
from access_policy import valid_agent_port
EXECUTOR = SHARED_EXECUTOR

logger.debug("📦 admin_panel.py module loaded")


# ==============================================================================
# 🛡️ UTILITY FUNCTIONS
# ==============================================================================

async def safe_edit_message(update: Update, text: str, reply_markup=None, parse_mode: str = "Markdown", **kwargs):
    """Safely edit an existing bot message.

    - Works for callback_query edits (most common).
    - If message_id/chat_id are provided, can also edit a specific message via bot.edit_message_text.
    - On parse errors (bad markdown), it retries without parse_mode.
    """
    # Accept and ignore unexpected kwargs (backward compatibility)
    message_id = kwargs.pop("message_id", None)
    chat_id = kwargs.pop("chat_id", None)

    def _sanitize(t: str) -> str:
        # Remove common markdown tokens that often break entity parsing.
        return (
            str(t)
            .replace("`", "")
            .replace("*", "")
            .replace("_", "")
            .replace("[", "")
            .replace("]", "")
        )

    try:
        if update.callback_query:
            return await update.callback_query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )

        # Fallback: edit a specific message if IDs are provided
        if message_id is not None and chat_id is not None:
            return await update.get_bot().edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )

        # Final fallback: send a new message
        if update.message:
            return await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        return None

    except Exception as e:
        # Telegram returns this for harmless refreshes; avoid logging a fake failure
        # and especially avoid retrying the identical request.
        if "Message is not modified" in str(e):
            return update.callback_query.message if update.callback_query else None
        # Only formatting errors benefit from a plain-text retry.
        if not ("parse entities" in str(e).lower() or "can't parse" in str(e).lower()):
            logger.warning("Edit failed (%s)", type(e).__name__)
            return None
        try:
            clean = _sanitize(text)
            if update.callback_query:
                return await update.callback_query.edit_message_text(
                    text=clean, reply_markup=reply_markup, parse_mode=None,
                    disable_web_page_preview=True)
            if message_id is not None and chat_id is not None:
                return await update.get_bot().edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=clean,
                    reply_markup=reply_markup, parse_mode=None,
                    disable_web_page_preview=True)
            if update.message:
                return await update.message.reply_text(
                    text=clean, reply_markup=reply_markup, parse_mode=None,
                    disable_web_page_preview=True)
        except Exception as retry_error:
            if "Message is not modified" not in str(retry_error):
                logger.warning("Plain-text edit failed (%s)", type(retry_error).__name__)
        return None



# ==============================================================================
# 👑 ADMIN PANEL HANDLERS
# ==============================================================================

async def admin_panel_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی اصلی پنل مدیریت."""
    if update.effective_user.id != SUPER_ADMIN_ID:
        logger.warning(f"❌ Unauthorized admin access attempt by {update.effective_user.id}")
        return
    
    try:
        def _summary_counts():
            users_count = len(db.get_all_users())
            with db.get_connection() as (conn, cur):
                cur.execute('SELECT id FROM servers')
                total_servers = len(cur.fetchall())
            return users_count, total_servers
        users_count, total_servers = await run_sync(_summary_counts)
        logger.debug("Admin summary counts loaded: users=%s servers=%s", users_count, total_servers)

        reply_markup = keyboard.admin_main_kb()
        txt = (
            f"🤖 **پنل مدیریت ربات**\n\n"
            f"📊 **آمار کلی:**\n"
            f"👤 کل کاربران: `{users_count}`\n"
            f"🖥 کل سرورهای ثبت شده: `{total_servers}`"
        )
        
        await safe_edit_message(update, txt, reply_markup=reply_markup)
        logger.info(f"✅ Admin panel displayed")
        
    except Exception as e:
        logger.error(f"❌ Error in admin_panel_main: {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا:  {e}")


# ==============================================================================
# 🛠 AGENT MANAGEMENT DASHBOARD
# ==============================================================================

async def admin_agent_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """داشبورد وضعیت ایجنت‌ها (همه سرورها)."""
    if update.effective_user.id != SUPER_ADMIN_ID: 
        logger.warning(f"❌ Unauthorized agent dashboard access by {update.effective_user.id}")
        return

    try:
        servers = await run_sync(db.get_all_servers)
        if not servers:
            await safe_edit_message(
                update, 
                "📭 هیچ سروری در دیتابیس ثبت نشده است.", 
                reply_markup=keyboard.back_btn('admin_panel_main')
            )
            logger.info("⚠️ No servers found in database")
            return

        logger.info(f"🔍 Checking status of {len(servers)} servers...")
        
        # برای جلوگیری از فشار زیاد، همزمانی را محدود می‌کنیم
        sem = asyncio.Semaphore(10)

        async def _probe(s):
            """تست وضعیت یک سرور."""
            async with sem:
                ip = s. get('ip')
                # پروتکل فعلی:  token=password
                token = sec.decrypt(s['password']) if s.get('password') else ''
                try:
                    logger.debug(f"🔌 Probing {ip}...")
                    res = await ServerMonitor.check_full_stats_ws(ip, int(s.get('ws_port') or AGENT_PORT), token)
                except Exception as e:
                    logger.warning(f"⚠️ Probe failed for {ip}: {e}")
                    res = {'status': 'Offline', 'error': str(e)}
                
                # ذخیره در DB برای نمایش لاگ/آخرین وضعیت
                now = get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
                if res. get('status') == 'Online':
                    await run_sync(db.update_agent_state, s['id'], status='Online', last_log='OK', last_seen=now)
                    await run_sync(db.update_server_error, s['id'], '')
                    logger.debug(f"✅ Server {ip} is Online")
                else: 
                    err = str(res.get('error') or 'Unknown')
                    # اگر خطای auth باشد، همان وضعیت را روی last_status هم اعمال می‌کنیم
                    if 'auth' in err.lower():
                        await run_sync(db.update_status, s['id'], 'Auth Failed')
                        logger.warning(f"🔐 Auth failed for {ip}")
                    await run_sync(db.update_agent_state, s['id'], status='', last_log=err, last_seen=now)
                    await run_sync(db.update_server_error, s['id'], err)
                    logger.warning(f"❌ Server {ip} is Offline: {err}")
                
                return s['id'], res

        # اجرای تسک‌های پروب
        results = await asyncio.gather(*[_probe(s) for s in servers], return_exceptions=True)

        # تجزیه نتایج
        online = 0
        offline = 0
        lines = []
        
        for item in results:
            if isinstance(item, Exception):
                logger.error(f"❌ Probe exception:  {item}")
                continue
            
            sid, res = item
            srv = next((x for x in servers if x['id'] == sid), None)
            if not srv:
                continue
            
            if res.get('status') == 'Online':
                online += 1
                lines.append(f"🟢 `{sid}` | {srv['name']} | {srv['ip']}")
            else:
                offline += 1
                err = str(res.get('error') or 'Offline')
                # کوتاه‌سازی متن
                if len(err) > 42:
                    err = err[: 42] + '.. .'
                lines.append(f"🔴 `{sid}` | {srv['name']} | {srv['ip']} | `{err}`")

        txt = (
            "🛠 **تنظیمات و وضعیت ایجنت‌ها**\n\n"
            f"🔌 پورت ایجنت (Config): `{AGENT_PORT}`\n"
            f"🟢 آنلاین: `{online}` | 🔴 آفلاین: `{offline}`\n"
            "➖➖➖➖➖➖➖➖➖➖\n"
            + "\n".join(lines[: 35])
            + ("\n..." if len(lines) > 35 else "")
        )

        kb = [
            [InlineKeyboardButton("🔄 بروزرسانی", callback_data='admin_agent_dashboard')],
            [InlineKeyboardButton("📄 مشاهده لاگ اتصال.. .", callback_data='admin_agent_logs')],
            [InlineKeyboardButton("🔌 تلاش مجدد برای اتصال (Reconnect All)", callback_data='admin_agent_reconnect_all')],
            [InlineKeyboardButton("🧩 نصب/رفع ایجنت‌ها", callback_data='admin_agent_install_menu')],
            [InlineKeyboardButton("🔙 بازگشت", callback_data='admin_panel_main')],
        ]
        
        await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
        logger.info(f"✅ Agent dashboard displayed:  {online} online, {offline} offline")
        
    except Exception as e: 
        logger.error(f"❌ Error in admin_agent_dashboard: {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا: {e}")


async def admin_agent_logs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی انتخاب سرور برای مشاهده لاگ اتصال ایجنت."""
    if update.effective_user.id != SUPER_ADMIN_ID:
        logger.warning(f"❌ Unauthorized agent logs access by {update.effective_user.id}")
        return
    
    try:
        servers = await run_sync(db.get_all_servers)
        if not servers:
            await safe_edit_message(
                update,
                "📭 هیچ سروری ثبت نشده است.",
                reply_markup=keyboard. back_btn('admin_panel_main')
            )
            logger.info("⚠️ No servers found")
            return

        kb = []
        for s in servers[: 50]: 
            status_icon = "🟢" if s.get('last_status') == 'Online' else "🔴"
            kb. append([InlineKeyboardButton(
                f"{status_icon} {s['name']}",
                callback_data=f"admin_agent_log_{s['id']}"
            )])
        
        kb. append([InlineKeyboardButton("🔙 بازگشت", callback_data='admin_agent_dashboard')])
        
        await safe_edit_message(
            update,
            "📄 **انتخاب سرور برای مشاهده آخرین لاگ اتصال ایجنت**",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        logger.info(f"✅ Agent logs menu displayed with {len(servers)} servers")
        
    except Exception as e: 
        logger.error(f"❌ Error in admin_agent_logs_menu: {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا: {e}")


async def admin_agent_log_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لاگ اتصال ایجنت برای یک سرور خاص."""
    if update. effective_user.id != SUPER_ADMIN_ID:
        logger.warning(f"❌ Unauthorized agent log view by {update.effective_user.id}")
        return
    
    try:
        sid = int(update.callback_query.data. split('_')[-1])
    except Exception as e:
        logger.error(f"❌ Invalid server ID:  {e}")
        await safe_callback_answer(update.callback_query, "❌ شناسه نامعتبر", show_alert=True)
        return

    try:
        srv = await run_sync(db.get_server_by_id, sid)
        if not srv:
            await safe_edit_message(
                update,
                "❌ سرور یافت نشد.",
                reply_markup=keyboard. back_btn('admin_agent_dashboard')
            )
            logger.warning(f"⚠️ Server {sid} not found")
            return

        txt = (
            f"📄 **آخرین لاگ اتصال ایجنت**\n\n"
            f"🆔 `{srv['id']}`\n"
            f"📛 {srv['name']}\n"
            f"🌐 `{srv['ip']}`\n\n"
            f"🕒 آخرین مشاهده: `{srv. get('agent_last_seen') or 'N/A'}`\n"
            f"🧾 لاگ:  `{(srv.get('agent_last_log') or srv. get('last_error') or 'N/A')}`"
        )
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 بروزرسانی", callback_data='admin_agent_dashboard')],
            [InlineKeyboardButton("🔙 بازگشت", callback_data='admin_agent_logs')],
        ])
        
        await safe_edit_message(update, txt, reply_markup=kb)
        logger.info(f"✅ Agent log viewed for server {sid}")
        
    except Exception as e:
        logger.error(f"❌ Error in admin_agent_log_view: {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا:  {e}")


async def admin_agent_reconnect_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ریست کردن تمام اتصال‌های WebSocket (reconnect)."""
    if update.effective_user.id != SUPER_ADMIN_ID:
        logger. warning(f"❌ Unauthorized reconnect by {update.effective_user.id}")
        return
    
    try:
        logger.info("🔄 Reconnecting all WebSocket connections...")
        await ws_reconnect_all()
    except Exception as e:
        logger.error(f"❌ Reconnect failed: {e}", exc_info=True)
        pass
    
    try:
        await safe_callback_answer(update.callback_query, 
            "✅ تمام اتصال‌ها ریست شد؛ تلاش مجدد در اولین درخواست انجام می‌شود.",
            show_alert=True
        )
    except Exception as e:
        logger.warning(f"⚠️ Callback answer failed: {e}")
        pass
    
    await admin_agent_dashboard(update, context)
    logger.info("✅ Reconnect completed")


# ==============================================================================
# 👥 USER MANAGEMENT
# ==============================================================================

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست کاربران با صفحه‌بندی."""
    try:
        page = int(update.callback_query.data.split('_')[-1])
    except Exception as e:
        logger.error(f"❌ Invalid page number: {e}")
        page = 1

    try:
        users, total_count = db.get_all_users_paginated(page, 5)
        total_pages = (total_count + 4) // 5

        txt = f"👥 **لیست کاربران (صفحه {page} از {total_pages})**\nتعداد کل:  `{total_count}`\n➖➖➖➖➖➖"
        reply_markup = keyboard.admin_users_list_kb(users, page, total_pages)
        
        await safe_edit_message(update, txt, reply_markup=reply_markup)
        logger.info(f"✅ Users list displayed (page {page})")
        
    except Exception as e:
        logger.error(f"❌ Error in admin_users_list: {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا: {e}")


async def admin_user_manage(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None):
    """مدیریت یک کاربر خاص (نمایش جزئیات و گزینه‌های عمل)."""
    if not user_id and update.callback_query: 
        data = update.callback_query.data
        if "manage_" in data:
            try:
                user_id = int(data.split('_')[-1])
            except Exception as e:
                logger. error(f"❌ Parse manage_ callback failed: {e}")
                pass

    if not user_id: 
        await safe_edit_message(update, "❌ خطای سیستمی:  آیدی کاربر پیدا نشد.")
        logger.error("❌ admin_user_manage: user_id is None")
        return

    try: 
        user = db.get_user(user_id)
        if not user:
            await safe_edit_message(update, "❌ کاربر در دیتابیس یافت نشد.")
            logger.warning(f"⚠️ User {user_id} not found in database")
            return

        plan_txt = "💎 پریمیوم (VIP)" if user['plan_type'] == 1 else "👤 عادی (Normal)"
        ban_status = "🔴 مسدود" if user['is_banned'] else "🟢 فعال"

        txt = (
            f"👤 **مدیریت کاربر:** `{user['full_name']}`\n"
            f"🆔 آیدی: `{user['user_id']}`\n"
            f"💳 **نوع اشتراک:** {plan_txt}\n"
            f"📆 انقضا: `{user['expiry_date']}`\n"
            f"📡 وضعیت: {ban_status}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"📊 سرورها: `{len(db.get_all_user_servers(user_id))}` / `{user['server_limit']}`"
        )
        reply_markup = keyboard.admin_user_manage_kb(user_id, user['plan_type'], user['is_banned'])
        await safe_edit_message(update, txt, reply_markup=reply_markup)
        logger.info(f"✅ User management panel displayed for {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in admin_user_manage:  {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا: {e}")


async def admin_user_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجرای عمل‌های مختلف روی کاربران (ban, delete, add time, etc)."""
    try:
        data = update.callback_query.data
        action = data.split('_')[2]
        target_id = int(data.split('_')[3])
    except Exception as e:
        logger.error(f"❌ Parse user action failed: {e}")
        await safe_callback_answer(update.callback_query, "❌ خطای تجزیه دستور", show_alert=True)
        return

    try:
        if action == 'ban':
            new_state = db.toggle_ban_user(target_id)
            msg = "کاربر مسدود شد." if new_state else "کاربر فعال شد."
            try:
                await safe_callback_answer(update.callback_query, msg)
            except Exception as e:
                logger.warning(f"⚠️ Callback answer failed: {e}")
            await admin_user_manage(update, context, user_id=target_id)
            logger.info(f"✅ User {target_id} ban toggled to {new_state}")

        elif action == 'del':
            db.remove_user(target_id)
            try:
                await safe_callback_answer(update.callback_query, "کاربر حذف شد.")
            except Exception as e:
                logger.warning(f"⚠️ Callback answer failed: {e}")
            await admin_users_list(update, context)
            logger.info(f"✅ User {target_id} deleted")

        elif action == 'addtime':
            db.add_or_update_user(target_id, days=30)
            try:
                await safe_callback_answer(update.callback_query, "30 روز تمدید شد.")
            except Exception as e:
                logger.warning(f"⚠️ Callback answer failed: {e}")
            await admin_user_manage(update, context, user_id=target_id)
            logger.info(f"✅ User {target_id} extended by 30 days")

        elif action == 'limit':
            context.user_data['target_uid'] = target_id
            await safe_edit_message(update, "🔢 **تعداد جدید محدودیت سرور را وارد کنید:**", reply_markup=keyboard.get_cancel_markup())
            return ADMIN_SET_LIMIT

        elif action == 'settime':
            context.user_data['target_uid'] = target_id
            await safe_edit_message(update, "📅 **تعداد روز اعتبار را وارد کنید (مثلا 60):**", reply_markup=keyboard.get_cancel_markup())
            return ADMIN_SET_TIME_MANUAL

        elif action == 'toggleplan':
            new_plan = db.toggle_user_plan(target_id)
            msg = "✅ کاربر به پریمیوم ارتقا یافت" if new_plan == 1 else "⬇️ کاربر به عادی تغییر یافت"
            try:
                await safe_callback_answer(update.callback_query, msg, show_alert=True)
            except Exception as e:
                logger.warning(f"⚠️ Callback answer failed: {e}")
            await admin_user_manage(update, context, user_id=target_id)
            logger.info(f"✅ User {target_id} plan toggled to {new_plan}")
            
    except Exception as e:
        logger.error(f"❌ Error in admin_user_actions:  {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا:  {e}")


async def admin_set_limit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت تعداد محدودیت سرور جدید از کاربر."""
    try:
        lim = int(update.message.text)
        target_id = context.user_data. get('target_uid')
        db.update_user_limit(target_id, lim)
        await update.message.reply_text(f"✅ محدودیت سرور به {lim} تغییر یافت.")
        await admin_user_manage(update, context, user_id=target_id)
        logger.info(f"✅ User {target_id} limit set to {lim}")
        return ConversationHandler.END
    except ValueError:
        await update.message. reply_text("❌ لطفاً فقط عدد انگلیسی وارد کنید.")
        logger.warning(f"⚠️ Invalid limit input: {update.message.text}")
        return ADMIN_SET_LIMIT
    except Exception as e:
        logger.error(f"❌ Error in admin_set_limit_handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا:  {e}")
        return ADMIN_SET_LIMIT


async def admin_set_days_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت تعداد روز اعتبار جدید از کاربر."""
    try:
        days = int(update.message.text)
        target_id = context. user_data.get('target_uid')
        db.add_or_update_user(target_id, days=days)
        await update.message.reply_text(f"✅ اعتبار کاربر {days} روز تمدید شد.")
        await admin_user_manage(update, context, user_id=target_id)
        logger.info(f"✅ User {target_id} extended by {days} days")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ لطفاً فقط عدد انگلیسی وارد کنید.")
        logger.warning(f"⚠️ Invalid days input: {update.message. text}")
        return ADMIN_SET_TIME_MANUAL
    except Exception as e:
        logger.error(f"❌ Error in admin_set_days_handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا: {e}")
        return ADMIN_SET_TIME_MANUAL


async def admin_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع جستجوی کاربر."""
    await safe_edit_message(update, "🔎 **آیدی عددی کاربر را ارسال کنید:**", reply_markup=keyboard.get_cancel_markup())
    logger.info("🔎 Admin user search started")
    return ADMIN_SEARCH_USER


async def admin_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت آیدی کاربر و نمایش جزئیات."""
    try:
        tid = int(update.message.text)
        user = db.get_user(tid)
        if user:
            await admin_user_manage(update, context, user_id=tid)
            logger.info(f"✅ User {tid} found and management panel displayed")
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ کاربر یافت نشد.")
            logger.warning(f"⚠️ User {tid} not found")
            return ADMIN_SEARCH_USER
    except ValueError:
        await update.message.reply_text("❌ فرمت نامعتبر.")
        logger.warning(f"⚠️ Invalid user ID format: {update.message.text}")
        return ADMIN_SEARCH_USER
    except Exception as e:
        logger.error(f"❌ Error in admin_search_handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا: {e}")
        return ADMIN_SEARCH_USER


async def admin_users_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت لیست تمام کاربران به‌صورت متن."""
    try:
        users = db.get_all_users()
        txt = "📋 **لیست کل کاربران:**\n\n"
        for u in users:
            txt += f"🆔 {u['user_id']} | 👤 {u['full_name']} | 📅 Exp: {u['expiry_date']}\n"

        if len(txt) > 4000:
            # Avoid writing to the (deliberately read-only) installation directory.
            import io
            report = io.BytesIO(txt.encode('utf-8'))
            report.name = 'users_list.txt'
            try:
                await update.callback_query.message.reply_document(
                    document=report,
                    filename='users_list.txt',
                    caption="لیست کاربران",
                )
            except Exception as e:
                logger.error("User export send failed: %s", e)
            finally:
                report.close()
        else:
            await update.callback_query.message.reply_text(txt)
        
        logger.info(f"✅ Users text list sent ({len(users)} users)")
        
    except Exception as e:
        logger.error(f"❌ Error in admin_users_text: {e}", exc_info=True)
        await update.callback_query.message.reply_text(f"❌ خطا: {e}")


# ==============================================================================
# 📢 BROADCAST
# ==============================================================================

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع ارسال پیام به تمام کاربران."""
    await safe_edit_message(
        update, 
        "📢 **لطفاً پیام خود را ارسال کنید:**\n\nبرای تمام کاربران ارسال می‌شود.", 
        reply_markup=keyboard.get_cancel_markup()
    )
    logger.info("📢 Broadcast started")
    return GET_BROADCAST_MSG


async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال پیام به تمام کاربران."""
    try:
        users = db.get_all_users()
        total = len(users)
        success = 0
        blocked = 0
        
        status_msg = await update.message.reply_text(f"⏳ در حال ارسال به {total} کاربر...")
        logger.info(f"📢 Broadcasting message to {total} users...")

        for user in users:
            try: 
                await update.message.copy(chat_id=user['user_id'])
                success += 1
            except Exception as e:
                blocked += 1
                logger.debug(f"⚠️ Broadcast failed for user {user['user_id']}: {e}")
            
            if success % 20 == 0:
                await asyncio.sleep(1)

        await status_msg.edit_text(
            f"✅ **ارسال شد.**\n👥 کل: `{total}`\n✅ موفق: `{success}`\n🚫 ناموفق: `{blocked}`"
        )
        await admin_panel_main(update, context)
        logger.info(f"✅ Broadcast completed:  {success} successful, {blocked} blocked")
        return ConversationHandler.END
        
    except Exception as e: 
        logger.error(f"❌ Error in admin_broadcast_send: {e}", exc_info=True)
        await update. message.reply_text(f"❌ خطا: {e}")
        return ConversationHandler.END


# ==============================================================================
# ➕ ADD NEW USER
# ==============================================================================

async def add_new_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع افزودن کاربر جدید."""
    try:
        await safe_callback_answer(update.callback_query, )
    except: 
        pass
    
    await safe_edit_message(
        update, 
        "👤 **شناسه عددی (User ID) کاربر را وارد کنید:**", 
        reply_markup=keyboard.get_cancel_markup()
    )
    logger.info("👤 Add new user started")
    return ADD_ADMIN_ID


async def get_new_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت آیدی کاربر جدید."""
    try:
        context.user_data['new_uid'] = int(update.message.text)
        await update.message.reply_text("📅 **تعداد روز اعتبار:**", reply_markup=keyboard.get_cancel_markup())
        logger.info(f"👤 New user ID received: {context.user_data['new_uid']}")
        return ADD_ADMIN_DAYS
    except ValueError:
        await update.message.reply_text("❌ فقط عدد وارد کنید.")
        logger.warning(f"⚠️ Invalid new user ID:  {update.message.text}")
        return ADD_ADMIN_ID
    except Exception as e:
        logger.error(f"❌ Error in get_new_user_id: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا:  {e}")
        return ADD_ADMIN_ID


async def get_new_user_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت تعداد روز اعتبار و افزودن کاربر."""
    try:
        days = int(update.message.text)
        new_uid = context.user_data. get('new_uid')
        db.add_or_update_user(new_uid, full_name="User (Manual)", days=days)
        await update.message.reply_text("✅ کاربر افزوده شد.")
        await admin_panel_main(update, context)
        logger.info(f"✅ New user added:  {new_uid} with {days} days")
        return ConversationHandler.END
    except ValueError:
        await update. message.reply_text("❌ فقط عدد وارد کنید.")
        logger.warning(f"⚠️ Invalid days input: {update. message.text}")
        return ADD_ADMIN_DAYS
    except Exception as e:
        logger.error(f"❌ Error in get_new_user_days: {e}", exc_info=True)
        await update. message.reply_text(f"❌ خطا: {e}")
        return ADD_ADMIN_DAYS


# ==============================================================================
# 📜 GLOBAL SERVER REPORTS
# ==============================================================================

async def admin_all_servers_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش سرورهای تمام کاربران (با صفحه‌بندی)."""
    if update.effective_user.id != SUPER_ADMIN_ID:
        return
    
    try:
        query = update.callback_query
        try:
            page = int(query.data.split('_')[-1])
        except:
            page = 1
        
        ITEMS_PER_PAGE = 3
        all_users = db.get_all_users()
        users_with_active_servers = []
        
        for u in all_users:
            servers = db.get_all_user_servers(u['user_id'])
            if any(s['is_active'] == 1 for s in servers):
                users_with_active_servers.append(u)

        total = len(users_with_active_servers)
        total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        start_idx = (page - 1) * ITEMS_PER_PAGE
        current_users = users_with_active_servers[start_idx:start_idx + ITEMS_PER_PAGE]

        txt = f"📜 **لیست کاربران دارای سرور فعال**\n📄 صفحه `{page}` از `{total_pages}`\n➖➖➖➖➖➖\n"
        
        for u in current_users:
            servers = db.get_all_user_servers(u['user_id'])
            active = [s for s in servers if s['is_active']]
            txt += f"👤 **{u['full_name']}** (`{u['user_id']}`)\n📦 فعال:  `{len(active)}`\n"
            
            for i, s in enumerate(active, 1):
                status = "🟢" if s['last_status'] == 'Online' else "🔴"
                expiry = s['expiry_date']. split(' ')[0] if s['expiry_date'] else "♾"
                txt += f"   {i}. {status} **{s['name']}** | 📅 {expiry}\n"
            txt += "➖\n"

        reply_markup = keyboard.admin_global_report_kb(page, total_pages)
        await safe_edit_message(update, txt, reply_markup=reply_markup)
        logger.info(f"✅ Global report displayed (page {page})")
        
    except Exception as e:
        logger.error(f"❌ Error in admin_all_servers_report: {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا:  {e}")


async def admin_full_report_global_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع تولید گزارش جامع تمام سرورها."""
    try:
        await safe_callback_answer(update.callback_query, "⏳ شروع گزارش جامع...")
        await update.callback_query.message.reply_text("⚠️ **شروع آنالیز تمام سرورها.. .**\nلطفاً صبور باشید.")
        logger.info("🔄 Full global report started")
        asyncio.create_task(run_full_global_report(context, update. effective_chat.id))
    except Exception as e:
        logger.error(f"❌ Error in admin_full_report_global_action: {e}", exc_info=True)


async def run_full_global_report(context, chat_id):
    """اجرای تولید گزارش جامع سرورها."""
    try:
        loop = asyncio.get_running_loop()
        
        # استفاده از executor برای دریافت لیست (sync)
        all_servers = await loop.run_in_executor(EXECUTOR, db.get_all_servers)
        active_servers = [s for s in all_servers if s['is_active']]

        if not active_servers:
            await context.bot.send_message(chat_id, "❌ سرور فعالی یافت نشد.")
            logger.warning("⚠️ No active servers found for report")
            return
        
        logger.info(f"📊 Generating report for {len(active_servers)} active servers...")
        
        sem = asyncio.Semaphore(10)
        
        async def safe_check(s):
            """بررسی ایمن‌تر وضعیت سرور."""
            async with sem:
                try: 
                    # ✅ StatsManager.check_full_stats خود async است
                    res = await StatsManager.check_full_stats(
                        s['ip'],
                        s['port'],
                        s['username'],
                        sec. decrypt(s['password'])
                    )
                    return res
                except Exception as e: 
                    logger.error(f"❌ Stats check error for {s['ip']}: {e}")
                    return {'status': 'Offline', 'error': str(e)}

        results = await asyncio.gather(*[safe_check(s) for s in active_servers])
        report_lines = []
        
        for srv, res in zip(active_servers, results):
            if isinstance(res, dict) and res.get('status') == 'Online':
                cpu = ScoreEngine.make_bar(res. get('cpu', 0), 5)
                uptime = res.get('uptime_str', 'N/A')
                report_lines.append(
                    f"🟢 **{srv['name']}**\n"
                    f"   🆔 User: `{srv['owner_id']}`\n"
                    f"   🧠 {cpu} {res.get('cpu', 0)}%\n"
                    f"   ⏱ {uptime}\n"
                )
            else:
                err = res.get('error', 'Unknown Error') if isinstance(res, dict) else str(res)
                report_lines. append(
                    f"🔴 **{srv['name']}**\n"
                    f"   🆔 User: `{srv['owner_id']}`\n"
                    f"   ❌ {err}\n"
                )

        final_report = (
            f"🌍 **گزارش جامع سرورها**\n"
            f"📅 `{get_jalali_str()}`\n"
            f"➖➖➖➖➖➖\n"
            + "\n".join(report_lines)
        )
        
        if len(final_report) > 4000:
            for i in range(0, len(final_report), 4000):
                await context.bot.send_message(
                    chat_id,
                    final_report[i:i+4000],
                    parse_mode='Markdown'
                )
        else:
            await context.bot. send_message(chat_id, final_report, parse_mode='Markdown')
        
        logger.info(f"✅ Full report sent ({len(report_lines)} servers)")
            
    except Exception as e: 
        logger.error(f"❌ run_full_global_report error:  {e}", exc_info=True)
        try:
            await context.bot. send_message(
                chat_id,
                f"❌ خطا در تولید گزارش:  {e}"
            )
        except:
            pass


# ==============================================================================
# 🔎 USER SEARCH & DETAIL
# ==============================================================================

async def admin_search_servers_by_uid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع جستجوی سرورهای یک کاربر خاص."""
    await safe_edit_message(
        update, 
        "🔎 **آیدی عددی کاربر را ارسال کنید:**", 
        reply_markup=keyboard. get_cancel_markup()
    )
    logger.info("🔎 Search servers by UID started")
    return ADMIN_GET_UID_FOR_REPORT


async def admin_report_by_uid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت آیدی کاربر و نمایش سرورهای آن."""
    try:
        target_uid = int(update.message.text)
        servers = db.get_all_user_servers(target_uid)
        
        if not servers:
            await update.message.reply_text("⚠️ این کاربر سروری ندارد.")
            logger.warning(f"⚠️ User {target_uid} has no servers")
            return ConversationHandler.END
        
        txt = f"🖥 **سرورهای کاربر:** `{target_uid}`\n➖➖➖➖➖➖\n"
        kb = []
        
        for s in servers:
            icon = "🟢" if s['is_active'] else "🔴"
            kb.append([InlineKeyboardButton(f"{icon} {s['name']}", callback_data=f"admin_detail_{s['id']}")])
        
        kb. append([InlineKeyboardButton("🔙 بازگشت", callback_data='admin_panel_main')])
        
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        logger.info(f"✅ Servers for user {target_uid} displayed")
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ آیدی نامعتبر.")
        logger.warning(f"⚠️ Invalid user ID format: {update.message.text}")
        return ADMIN_GET_UID_FOR_REPORT
    except Exception as e:
        logger.error(f"❌ Error in admin_report_by_uid_handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا: {e}")
        return ADMIN_GET_UID_FOR_REPORT


async def admin_server_detail_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    callback handler برای جزئیات سرور.
    این تابع صرفاً برای هماهنگی کالبک‌هاست.
    لاجیک اصلی در bot_logic.py (server_detail) است.
    """
    logger.debug("📌 admin_server_detail_action called - delegating to server_detail")
    pass


async def admin_user_servers_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش گزارش سرورهای یک کاربر خاص."""
    try:
        query = update.callback_query
        target_uid = int(query.data.split('_')[3])
        servers = db.get_all_user_servers(target_uid)
        
        if not servers: 
            await safe_callback_answer(query, "❌ سروری ندارد.", show_alert=True)
            logger.warning(f"⚠️ User {target_uid} has no servers")
            return

        txt = f"👤 **گزارش سرورهای کاربر {target_uid}**\n\n"
        for s in servers:
            txt += f"🔹 **{s['name']}**\n   🌐 {s['ip']}\n   📡 {s['last_status']}\n\n"
        
        kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin_u_manage_{target_uid}")]]
        await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
        logger.info(f"✅ User {target_uid} servers report displayed")
        
    except Exception as e:
        logger.error(f"❌ Error in admin_user_servers_report:  {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا:  {e}")
# ==============================================================================
# Agent installer (Admin)
# ==============================================================================

async def admin_agent_install_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی نصب/رفع ایجنت‌ها برای ادمین."""
    try:
        query = update.callback_query
        if query:
            await safe_callback_answer(query, )

        servers = db.get_all_servers()
        if not servers:
            await safe_edit_message(update, "❌ هیچ سروری ثبت نشده است.")
            return

        txt = "🧩 **نصب/رفع ایجنت مانیتورینگ (WebSocket Agent)**\n\n" \
              "این بخش با **SSH** به سرور وصل می‌شود، پیش‌نیازها را نصب می‌کند، سرویس `sonar-agent` را می‌سازد/آپدیت می‌کند، سپس اتصال WebSocket را تست می‌کند."

        kb = []
        for s in servers:
            sid = s.get('id')
            name = s.get('name') or f"Server {sid}"
            ip = s.get('ip') or "?"
            kb.append([InlineKeyboardButton(f"🔧 {name} | {ip}", callback_data=f"admin_agent_install_{sid}")])

        kb.append([InlineKeyboardButton("🛠 نصب/تعمیر همه سرورها (انتخاب پورت)", callback_data="admin_agent_install_all")])
        kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data='admin_agent_dashboard')])
        await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
        logger.info(f"✅ admin_agent_install_menu shown for {len(servers)} servers")

    except Exception as e:
        logger.error(f"❌ Error in admin_agent_install_menu: {e}", exc_info=True)
        await safe_edit_message(update, f"❌ خطا: {e}")


async def admin_agent_install_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SUPER_ADMIN_ID or update.effective_user.id!=SUPER_ADMIN_ID:
        return ConversationHandler.END
    await safe_callback_answer(update.callback_query, )
    if update.callback_query.data=='admin_agent_install_all':
        servers=await run_sync(db.get_all_servers)
        context.user_data['agent_install_targets']=[int(s['id']) for s in servers]
        await update.effective_message.reply_text(f'🔌 پورت ایجنت برای نصب گروهی را وارد کنید؛ پیش‌فرض {AGENT_PORT}، بازهٔ 1024 تا 65535. /cancel برای لغو.')
        return ADMIN_AGENT_PORT_INPUT
    try:
        sid=int(update.callback_query.data.rsplit('_',1)[1])
    except (ValueError,IndexError):
        return ConversationHandler.END
    srv=await run_sync(db.get_server_by_id,sid)
    if not srv:
        await update.effective_message.reply_text('❌ سرور یافت نشد.')
        return ConversationHandler.END
    msg=await update.effective_message.reply_text('⏳ نصب/تعمیر و آزمون اتصال واقعی ایجنت در پس‌زمینه آغاز شد.')
    spawn_background(context,_admin_install_many([dict(srv)],None,msg),name=f'admin-agent-{sid}')
    return ConversationHandler.END


async def admin_agent_port_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SUPER_ADMIN_ID or update.effective_user.id!=SUPER_ADMIN_ID:
        return ConversationHandler.END
    try:
        port=valid_agent_port((update.message.text or '').strip())
    except ValueError:
        await update.message.reply_text('❌ پورت باید عددی بین 1024 تا 65535 باشد؛ /cancel برای لغو.')
        return ADMIN_AGENT_PORT_INPUT
    ids=context.user_data.pop('agent_install_targets',[])
    if not ids:
        await update.message.reply_text('❌ درخواست نصب گروهی منقضی شده است.')
        return ConversationHandler.END
    rows=await run_sync(lambda:[dict(s) for sid in ids if (s:=db.get_server_by_id(sid))])
    msg=await update.message.reply_text(f'⏳ نصب/تعمیر {len(rows)} سرور روی پورت {port} در پس‌زمینه آغاز شد.')
    spawn_background(context,_admin_install_many(rows,port,msg),name='admin-agent-bulk')
    return ConversationHandler.END


async def _admin_install_many(servers, selected_port, message):
    count=0
    failures=[]
    for index,srv in enumerate(servers,1):
        current=await run_sync(db.get_server_by_id,srv['id'])
        if not current:
            failures.append(f'{srv["id"]}: سرور حذف شده است.');continue
        port=valid_agent_port(selected_port or current.get('ws_port') or AGENT_PORT)
        await message.edit_text(f'⏳ {index}/{len(servers)} | {current["name"]} | SSH:{current["port"]} | WSS:{port}')
        loop=asyncio.get_running_loop()
        stamp=[0.0]
        def progress(stage, detail):
            import time as _time
            if _time.monotonic()-stamp[0] < 2.0 and stage not in ('failed','success','fallback','transfer'):return
            stamp[0]=_time.monotonic()
            asyncio.run_coroutine_threadsafe(message.edit_text(
                f'⏳ {index}/{len(servers)} | {current["name"]}\n'
                f'🔹 {stage}: {detail[:200]}'),loop)
        ok,detail=await run_sync(ServerMonitor.install_agent_service,current['ip'],current['port'],current['username'],sec.decrypt(current['password']),port, progress=progress)
        await run_sync(db.update_server_error,current['id'],'' if ok else detail)
        if ok:count+=1
        else:failures.append(f'{current["name"]}: {str(detail)[:500]}')
    text=f'✅ موفق: {count}/{len(servers)}'
    if failures:text+='\n\n'+ '\n'.join(failures)
    await message.edit_text(text[:3900],reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📡 وضعیت ایجنت‌ها',callback_data='admin_agent_dashboard')]]))
