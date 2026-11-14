"""
Auto-split module from original bot_logic.py
Section: menu
"""

from bot_logic_mod.base import *  # noqa
from access_policy import has_premium
from html import escape
from datetime import datetime,timezone
from math import ceil
from billing import Billing
from settings import PROJECT_VERSION

# 🚀 STARTUP & MENU HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # /start is navigation, not a cancellation command. A persisted fetch/test
    # must finish even if the user changes menus during the operation.
    task=USER_ACTIVE_TASKS.get(user_id)
    if task is not None and task.done():
        USER_ACTIVE_TASKS.pop(user_id,None)
    context.user_data.clear()
    await register_user_logic(update, context)
    if not cronjobs.IS_SYSTEM_INITIALIZED:
        asyncio.create_task(cronjobs.silent_update_monitor_agent())
        cronjobs.IS_SYSTEM_INITIALIZED = True
    await show_main_menu(update, context)
    return ConversationHandler.END

async def register_user_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    loop = asyncio.get_running_loop()
    args = context.args 
    inviter_id = 0
    existing_user = await loop.run_in_executor(EXECUTOR, db.get_user, user_id)
    is_new_user = False if existing_user else True
    if is_new_user and user_id != SUPER_ADMIN_ID and args and args[0].isdigit():
        possible_inviter = int(args[0])
        if possible_inviter != user_id:
            inviter_exists = await loop.run_in_executor(EXECUTOR, db.get_user, possible_inviter)
            if inviter_exists:
                inviter_id = possible_inviter
    await loop.run_in_executor(EXECUTOR, db.add_or_update_user, user_id, full_name, inviter_id)

    if user_id == SUPER_ADMIN_ID: return

    if is_new_user:
        try:
            admin_msg = f"🔔 **کاربر جدید!**\n👤 {full_name}\n🆔 `{user_id}`\n🔗 دعوت: `{inviter_id if inviter_id else 'مستقیم'}`"
            await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=admin_msg, parse_mode='Markdown')
        except Exception: pass

        if inviter_id != 0:
            ok, new_lim, new_exp = await loop.run_in_executor(EXECUTOR, db.apply_referral_reward, inviter_id)
            if ok:
                try:
                    await context.bot.send_message(
                        chat_id=inviter_id,
                        text=(f"🎉 **تبریک! زیرمجموعه جدید:** {full_name}\n🎁 **پاداش:** +1 سرور (مجموع: {new_lim}) | +10 روز اعتبار")
                    )
                except Exception: pass

        try:
            sub=await loop.run_in_executor(EXECUTOR,Billing(db).subscription,user_id)
            detail=(f"پلن: {escape(sub['snapshot']['name'])}\n"
                    f"مدت اولیه: {sub['snapshot']['days']} روز\n"
                    f"ظرفیت سرور: {sub['limits']['servers']}" if sub else 'ظرفیت و اعتبار را از بخش «اشتراک و خرید» ببینید.')
            await update.message.reply_text(
                f"سلام {escape(full_name)}، خوش آمدید.\n\nحساب شما ساخته شد.\n{detail}\n\nاز «سرورهای من» یا «کانفیگ‌ها» شروع کنید؛ راهنمای هر بخش همراه شماست.",
                parse_mode='HTML'
            )
        except Exception: pass

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    loop = asyncio.get_running_loop()
    
    user=await loop.run_in_executor(EXECUTOR,db.get_user,user_id)
    if not user or user.get('is_banned'):
        txt='حساب شما در دسترس نیست. برای بررسی وضعیت با پشتیبانی تماس بگیرید.'
        if update.callback_query:await safe_edit_message(update,txt)
        else:await update.message.reply_text(txt)
        return
    has_access,msg=await loop.run_in_executor(EXECUTOR,db.check_access,user_id)
    sub=await loop.run_in_executor(EXECUTOR,Billing(db).subscription,user_id)
    if sub:
        status={'active':'فعال','expired':'پایان اعتبار','suspended':'معلق','cancelled':'لغوشده'}[sub['effective_status']]
        expiry=sub.get('expires_at')
        remaining=(f"{max(0,ceil((expiry-datetime.now(timezone.utc)).total_seconds()/86400))} روز تا پایان" if expiry else 'بدون پایان زمانی')
        plan=sub['snapshot']['name']
    else:
        status='فعال' if has_access else 'نیازمند بررسی اعتبار'
        plan='مدیر سامانه' if user_id==SUPER_ADMIN_ID else 'حساب قبلی'
        expiry=user.get('subscription_expires_at')
        remaining=(f"{max(0,ceil((expiry-datetime.now(timezone.utc)).total_seconds()/86400))} روز تا پایان" if expiry else 'اعتبار قبلی' if user.get('subscription_date_invalid') else 'بدون پایان زمانی' if user.get('subscription_unlimited') or user_id==SUPER_ADMIN_ID else 'جزئیات در بخش اشتراک')
    # Local DB reads only; opening the menu never waits for an Agent/network probe.
    reply_markup=keyboard.main_menu_kb(user_id,False,SUPER_ADMIN_ID) if has_access else keyboard.renewal_menu_kb()
    txt=(f"🛰 SONAR RADAR · {PROJECT_VERSION}\n\n"
         f"وضعیت حساب: {status}\nپلن: {plan}\n{remaining}\n\n"
         +('برای شروع، بخش موردنظر را انتخاب کنید.' if has_access else 'برای ادامهٔ پایش، اشتراک را تمدید یا وضعیت حساب را بررسی کنید. اطلاعات قبلی شما حفظ شده‌اند.'))

    if update.callback_query:
        await safe_edit_message(update,txt,reply_markup=reply_markup,parse_mode=None)
    else:
        await update.message.reply_text(txt, reply_markup=reply_markup)

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from navigation import clear_section
    clear_section(context)
    await show_main_menu(update, context)

async def user_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try: await safe_callback_answer(update.callback_query, )
        except Exception: pass
    
    uid = update.effective_user.id
    user = db.get_user(uid)

    if not user:
        await safe_edit_message(update, "❌ کاربر یافت نشد.")
        return

    try:
        join_date = datetime.strptime(user['added_date'], '%Y-%m-%d %H:%M:%S')
        j_join = jdatetime.date.fromgregorian(date=join_date.date())
        join_str = f"{j_join.day} {jdatetime.date.j_months_fa[j_join.month - 1]} {j_join.year}"
    except Exception: join_str = "نامشخص"

    access, time_left = db.check_access(uid)
    if uid == SUPER_ADMIN_ID:
        sub_type = "👑 مدیریت کل (God Mode)"
        expiry_str = "♾ نامحدود"
    else:
        sub_type = "💎 پریمیوم (VIP)" if has_premium(user,uid,SUPER_ADMIN_ID) else "👤 عادی (Normal)"
        expiry_str = f"{time_left} روز مانده" if isinstance(time_left, int) else "نامحدود"

    servers = db.get_all_user_servers(uid)
    srv_count = len(servers)
    active_srv = sum(1 for s in servers if s['is_active'])

    txt = (f"👤 **پروفایل کاربری شما**\n➖➖➖➖➖➖➖➖➖➖\n🏷 **نام:** `{user['full_name']}`\n🆔 **آیدی عددی:** `{user['user_id']}`\n📅 **تاریخ عضویت:** `{join_str}`\n\n💳 **نوع اشتراک:** {sub_type}\n⏳ **اعتبار باقی‌مانده:** `{expiry_str}`\n🔢 **سقف مجاز سرور:** `{user['server_limit']} عدد`\n\n🖥 **وضعیت سرورها:**\n   ├ 🟢 فعال: `{active_srv}`\n   └ ⚪️ کل ثبت شده: `{srv_count}`")
    reply_markup = keyboard.user_profile_kb()
    await safe_edit_message(update,txt,reply_markup=reply_markup,parse_mode=None)

async def web_token_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Issue a single-use, short-lived website code only in the user's DM."""
    from keyboard import _webapp_button
    from web_login import issue_code, install_schema
    uid=update.effective_user.id
    web_url=os.environ.get('SONAR_WEB_ORIGIN','').rstrip('/')
    if update.effective_chat.type != 'private':
        await safe_callback_answer(update.callback_query, 'برای دریافت کد ورود، ربات را در پیام خصوصی باز کنید.',show_alert=True)
        return
    if not web_url.startswith('https://'):
        await safe_edit_message(update,'ابتدا مدیر باید SONAR_WEB_ORIGIN را با آدرس HTTPS معتبر تنظیم کند.',reply_markup=keyboard.back_btn())
        return
    await run_sync(install_schema,db)
    code=await run_sync(issue_code,db,uid)
    btn=_webapp_button()
    rows=[[btn]] if btn else []
    rows.append([InlineKeyboardButton('🔄 بازنشانی کد ورود',callback_data='gen_web_token')])
    rows.append([InlineKeyboardButton('🚪 خروج از تمام دستگاه‌های وب',callback_data='web_logout_all')])
    rows.append([InlineKeyboardButton('🔙 بازگشت',callback_data='main_menu')])
    await safe_edit_message(update,
         '🔐 **کد یک‌بارمصرف ورود به وب‌سایت**\n\n'
         f'`{code}`\n\n'
         'اعتبار: ۸ دقیقه • پس از اولین استفاده باطل می‌شود.\n'
         'این کد فقط برای وب‌سایت در مرورگر است؛ مینی‌اپ داخل تلگرام خودکار وارد می‌شود و کد نمی‌خواهد.\n'
         'کد را با کسی به اشتراک نگذارید.\n'
         f'🌐 {web_url}', reply_markup=InlineKeyboardMarkup(rows))

# ==============================================================================


async def web_logout_all_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Only the logged-in Telegram account can revoke its web sessions."""
    from web_login import logout_all
    if not update.effective_chat or update.effective_chat.type!='private':
        await safe_callback_answer(update.callback_query, 'این گزینه فقط در گفت‌وگوی خصوصی فعال است.',show_alert=True)
        return
    await run_sync(logout_all,db,update.effective_user.id)
    await safe_callback_answer(update.callback_query, 'تمام نشست‌های وب لغو شدند.',show_alert=True)
    await safe_edit_message(update,'✅ تمام نشست‌های وب و کدهای استفاده‌نشده باطل شدند. برای ورود دوباره کد جدید بگیرید.',reply_markup=keyboard.back_btn())
