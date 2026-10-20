"""
Auto-split module from original bot_logic.py
Section: ops
"""

from bot_logic_mod.base import *  # noqa
from ssh_support import normalize_host, ssh_port

# 🌍 GLOBAL OPERATIONS (NEW FEATURES)
# ==============================================================================

async def global_ops_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی عملیات همگانی"""
    # استفاده از ماژول کیبورد
    reply_markup = keyboard.global_ops_kb()

    txt = (
        "🌍 **تنظیمات همگانی سرورها**\n\n"
        "در این بخش می‌تونی یک دستور رو همزمان روی **تمام سرورهای فعال** اجرا کنی.\n"
        "⚠️ نکته: عملیات ممکن است بسته به تعداد سرورها کمی طول بکشد."
    )
    await safe_edit_message(update, txt, reply_markup=reply_markup)


async def global_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت درخواست‌های همگانی"""
    query = update.callback_query
    action = query.data.split('_')[2]  # update, ram, disk, full
    uid = update.effective_user.id
    servers = db.get_all_user_servers(uid)
    active_servers = [s for s in servers if s['is_active']]

    if not active_servers:
        await query.answer("❌ هیچ سرور فعالی نداری!", show_alert=True)
        return

    await query.message.reply_text(
        f"⏳ **عملیات در حال اجرا روی {len(active_servers)} سرور...**\n"
        "لطفاً منتظر بمانید، نتیجه نهایی ارسال خواهد شد."
    )

    asyncio.create_task(cronjobs.run_global_commands_background(context, uid, active_servers, action))


# ==============================================================================
# ⏱ AUTO SCHEDULE HANDLERS (CRONJOBS)
# ==============================================================================

async def auto_update_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی تنظیم زمان‌بندی آپدیت خودکار"""
    if update.callback_query:
        await update.callback_query.answer()

    uid = update.effective_user.id
    curr = db.get_setting(uid, 'auto_update_hours') or '0'

    # استفاده از ماژول کیبورد
    reply_markup = keyboard.auto_update_kb(curr)

    txt = (
        "🔄 **تنظیم آپدیت خودکار مخازن (APT Update)**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "ربات می‌تواند به صورت دوره‌ای دستور `apt-get update && upgrade` را روی تمام سرورهای فعال اجرا کند.\n\n"
        "👇 بازه زمانی مورد نظر را انتخاب کنید:"
    )

    await safe_edit_message(update, txt, reply_markup=reply_markup)


async def auto_reboot_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی اصلی وضعیت ریبوت خودکار"""
    if update.callback_query:
        await update.callback_query.answer()

    uid = update.effective_user.id
    curr_setting = db.get_setting(uid, 'auto_reboot_config')

    status_txt = "❌ غیرفعال"
    if curr_setting and curr_setting != 'OFF':
        try:
            days, time_str = curr_setting.split('|')
            days = int(days)
            freq_map = {1: "هر روز", 2: "هر ۲ روز", 7: "هفتگی", 14: "هر ۲ هفته", 30: "ماهانه"}
            freq_txt = freq_map.get(days, f"هر {days} روز")
            status_txt = f"✅ {freq_txt} - ساعت {time_str}"
        except:
            status_txt = "⚠️ نامعتبر"

    txt = (
        "⚠️ **تنظیم ریبوت خودکار سرورها**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "🔴 **هشدار:** ریبوت شدن سرور باعث قطع موقت اتصال کاربران می‌شود.\n"
        "در این بخش می‌توانید تعیین کنید تمام سرورها سر ساعت مشخصی ریبوت شوند.\n\n"
        f"وضعیت فعلی: `{status_txt}`"
    )

    # استفاده از ماژول کیبورد
    reply_markup = keyboard.auto_reboot_kb()

    await safe_edit_message(update, txt, reply_markup=reply_markup)


async def ask_reboot_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پرسیدن ساعت از کاربر"""
    try:
        await update.callback_query.answer()
    except:
        pass

    txt = (
        "🕰 **تنظیم ساعت ریبوت**\n\n"
        "لطفاً ساعتی که می‌خواهید ریبوت انجام شود را به صورت عدد وارد کنید.\n"
        "🔢 بازه مجاز: `0` تا `23`\n\n"
        "مثال: برای ۴ صبح عدد `4` و برای ۲ بعدازظهر عدد `14` را ارسال کنید."
    )
    await safe_edit_message(update, txt, reply_markup=keyboard.get_cancel_markup())
    return GET_REBOOT_TIME


async def receive_reboot_time_and_show_freq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت ساعت و نمایش دکمه‌های فرکانس"""
    try:
        hour = int(update.message.text)
        if not (0 <= hour <= 23):
            raise ValueError()

        time_str = f"{hour:02d}:00"
        context.user_data['temp_reboot_time'] = time_str

        txt = (
            f"✅ ساعت انتخاب شده: `{time_str}`\n\n"
            "📅 **حالا بازه زمانی تکرار را انتخاب کنید:**"
        )

        # استفاده از ماژول کیبورد
        reply_markup = keyboard.reboot_freq_kb(time_str)

        await update.message.reply_text(txt, reply_markup=reply_markup, parse_mode='Markdown')
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("❌ عدد نامعتبر! لطفاً عددی بین 0 تا 23 وارد کنید.")
        return GET_REBOOT_TIME


async def save_auto_reboot_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره نهایی تنظیمات ریبوت"""
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id

    if data == 'disable_reboot':
        db.set_setting(uid, 'auto_reboot_config', 'OFF')
        await query.answer("✅ ریبوت خودکار غیرفعال شد.", show_alert=True)
        await auto_reboot_menu(update, context)
        return

    parts = data.split('_')
    days = parts[1]
    time_str = parts[2]

    config_str = f"{days}|{time_str}"
    db.set_setting(uid, 'auto_reboot_config', config_str)
    db.set_setting(uid, 'last_reboot_date', '2000-01-01')

    await query.answer(f"✅ تنظیم شد: هر {days} روز ساعت {time_str}")
    await auto_reboot_menu(update, context)
# --- تابع اجرایی جاب (Job) ---


async def save_auto_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره تنظیمات آپدیت خودکار"""
    query = update.callback_query
    uid = update.effective_user.id
    hours = query.data.split('_')[2]

    db.set_setting(uid, 'auto_update_hours', hours)

    if hours == '0':
        msg = "❌ آپدیت خودکار غیرفعال شد."
    else:
        msg = f"✅ آپدیت خودکار تنظیم شد: هر {hours} ساعت."

    try:
        await query.answer(msg, show_alert=True)
    except:
        pass

    await auto_update_menu(update, context)
async def show_payment_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش اطلاعات پرداخت (داینامیک از دیتابیس)"""
    query = update.callback_query
    method_type = query.data.split('_')[2]  # card or tron (که ما در دیتابیس card/crypto داریم)

    # مپ کردن دکمه‌های قدیمی به تایپ‌های دیتابیس
    db_type = 'card' if method_type == 'card' else 'crypto'

    plan_key = context.user_data.get('selected_plan')
    if not plan_key:
        await wallet_menu(update, context)
        return

    plan = SUBSCRIPTION_PLANS[plan_key]
    user_id = update.effective_user.id

    # دریافت روش‌های فعال از دیتابیس
    methods = db.get_payment_methods(db_type)

    if not methods:
        await safe_edit_message(update, "❌ متاسفانه در حال حاضر هیچ روش پرداختی برای این گزینه فعال نیست.\nلطفاً با پشتیبانی تماس بگیرید.")
        return

    # ثبت سفارش اولیه
    pay_id = db.create_payment(user_id, plan_key, plan['price'], method_type)

    details_txt = ""
    if db_type == 'card':
        details_txt = f"💳 **شماره کارت‌های فعال:**\n\n"
        for m in methods:
            details_txt += (
                f"🏦 **{m['network']}**\n"
                f"👤 {m['holder_name']}\n"
                f"🔢 `{m['address']}`\n"
                f"──────────────\n"
            )
        amount_txt = f"💰 مبلغ قابل پرداخت: `{plan['price']:,} تومان`"

    else:  # Crypto
        details_txt = f"💎 **آدرس‌های واریز (Crypto):**\n\n"
        for m in methods:
            details_txt += (
                f"🪙 **شبکه: {m['network']}**\n"
                f"🔗 آدرس:\n`{m['address']}`\n"
                f"(روی آدرس بزنید کپی می‌شود)\n"
                f"──────────────\n"
            )
        # اینجا مبلغ تومانی است. اگر بخواهید تتری باشد باید نرخ تبدیل داشته باشید
        # فعلاً همان تومانی را نمایش می‌دهیم
        amount_txt = f"💰 مبلغ معادل تومن: `{plan['price']:,} تومان`\n⚠️ لطفاً معادل تتری/ارزی را محاسبه و واریز کنید."

    txt = (
        f"{details_txt}"
        f"{amount_txt}\n\n"
        f"📝 **دستورالعمل:**\n"
        f"۱. مبلغ را به یکی از روش‌های بالا واریز کنید.\n"
        f"۲. اسکرین‌شات تراکنش را آماده کنید.\n"
        f"۳. دکمه **'✅ پرداخت کردم'** را بزنید."
    )

    # استفاده از ماژول کیبورد
    reply_markup = keyboard.confirm_payment_kb(pay_id)
    
    await safe_edit_message(update, txt, reply_markup=reply_markup)


async def ask_for_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۱: درخواست ارسال رسید از کاربر"""
    query = update.callback_query
    # فرمت دیتا: confirm_pay_ID
    pay_id = query.data.split('_')[2]

    # ذخیره آیدی پرداخت در حافظه موقت برای مرحله بعد
    context.user_data['current_pay_id'] = pay_id

    txt = (
        "📸 **لطفاً تصویر رسید پرداخت را ارسال کنید.**\n\n"
        "می‌توانید عکس بگیرید یا فایل (Screenshot) بفرستید.\n"
        "برای انصراف دکمه زیر را بزنید."
    )

    await safe_edit_message(update, txt, reply_markup=keyboard.get_cancel_markup())
    return GET_RECEIPT


async def process_receipt_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۲: دریافت عکس، ذخیره و ارسال برای ادمین"""
    pay_id = context.user_data.get('current_pay_id')
    if not pay_id:
        await update.message.reply_text("❌ خطای نشست. لطفاً دوباره تلاش کنید.")
        return ConversationHandler.END

    user = update.effective_user

    # پیدا کردن اطلاعات پرداخت از دیتابیس
    with db.get_connection() as (conn, cur):
        cur.execute("SELECT * FROM payments WHERE id=%s", (pay_id,))
        pay_info = cur.fetchone()

    if not pay_info:
        await update.message.reply_text("❌ تراکنش یافت نشد.")
        return ConversationHandler.END

    # تشخیص نوع فایل ارسالی (عکس فشرده یا فایل)
    if update.message.photo:
        # همیشه باکیفیت‌ترین عکس (آخرین در لیست) را برمی‌داریم
        file_id = update.message.photo[-1].file_id
        is_document = False
    elif update.message.document:
        file_id = update.message.document.file_id
        is_document = True
    else:
        await update.message.reply_text("❌ لطفاً فقط **عکس** یا **فایل تصویری** ارسال کنید.")
        return GET_RECEIPT

    # پیام تشکر به کاربر
    await update.message.reply_text(
        "✅ **رسید شما دریافت شد!**\n\n"
        "مدیران سیستم پس از بررسی صحت پرداخت، اشتراک شما را فعال خواهند کرد.\n"
        "این فرآیند معمولاً کمتر از ۱ ساعت زمان می‌برد.",
        reply_markup=keyboard.back_btn()
    )

    # --- ارسال به ادمین ---
    plan = SUBSCRIPTION_PLANS.get(pay_info['plan_type'])
    plan_name = plan['name'] if plan else "Unknown"

    admin_caption = (
        f"💰 **درخواست پرداخت جدید (همراه با رسید)**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"👤 کاربر: {user.full_name} (`{user.id}`)\n"
        f"📦 سرویس: {plan_name}\n"
        f"💵 مبلغ: {pay_info['amount']:,}\n"
        f"💳 روش: {pay_info['method']}\n"
        f"🔢 شناسه پرداخت: `{pay_id}`\n\n"
        f"⚠️ لطفاً رسید را چک کنید و تصمیم بگیرید."
    )

    # استفاده از ماژول کیبورد
    admin_kb = keyboard.admin_receipt_kb(pay_id)

    try:
        if is_document:
            await context.bot.send_document(chat_id=SUPER_ADMIN_ID, document=file_id, caption=admin_caption, reply_markup=admin_kb, parse_mode='Markdown')
        else:
            await context.bot.send_photo(chat_id=SUPER_ADMIN_ID, photo=file_id, caption=admin_caption, reply_markup=admin_kb, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to send receipt to admin: {e}")
        # اگر ارسال عکس شکست خورد، متنی بفرست
        await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=admin_caption + "\n\n❌ (عکس رسید ارسال نشد، خطا در تلگرام)", reply_markup=admin_kb)

    return ConversationHandler.END


async def admin_approve_payment_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید نهایی توسط ادمین"""
    query = update.callback_query
    pay_id = query.data.split('_')[3]

    res = db.approve_payment(pay_id)

    if res:
        user_id, plan_name = res
        await safe_edit_message(update, f"✅ پرداخت #{pay_id} تایید شد.\nسرویس {plan_name} برای کاربر فعال گردید.")
        try:
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **تبریک! پرداخت شما تایید شد.**\n\n✅ اشتراک **{plan_name}** فعال شد.")
        except:
            pass
    else:
        await safe_edit_message(update, "❌ خطا: این پرداخت قبلاً تایید شده است.")


async def admin_reject_payment_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pay_id = update.callback_query.data.split('_')[3]
    await safe_edit_message(update, f"❌ پرداخت #{pay_id} رد شد.")
async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی سیستم دعوت پیشرفته"""
    if update.callback_query:
        await update.callback_query.answer()

    uid = update.effective_user.id
    user = db.get_user(uid)
    bot_username = context.bot.username

    invite_link = f"https://t.me/{bot_username}?start={uid}"
    ref_count = user['referral_count'] if user['referral_count'] else 0

    txt = (
        f"💎 **کمپین بزرگ دعوت دوستان**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"دوستات رو دعوت کن، سرور رایگان بگیر! 🎁\n\n"
        f"🔰 **قوانین و پاداش‌ها:**\n"
        f"به ازای هر نفری که با لینک شما عضو شود:\n\n"
        f"1️⃣ **+10 روز** به اعتبار کل اکانتت اضافه میشه ⏳\n"
        f"2️⃣ **+1 عدد** ظرفیت سرور هدیه می‌گیری 🖥\n"
        f"   ╰ *(نکته: ظرفیت هدیه ۱۰ روزه است و بعد از آن منقضی می‌شود)*\n\n"
        f"📊 **عملکرد شما:**\n"
        f"👥 تعداد زیرمجموعه: `{ref_count} نفر`\n"
        f"📅 اعتبار فعلی شما: `{user['expiry_date']}`\n\n"
        f"🔗 **لینک اختصاصی شما (لمس کنید):**\n"
        f"`{invite_link}`"
    )

    # استفاده از ماژول کیبورد
    reply_markup = keyboard.referral_kb(invite_link)
    
    await safe_edit_message(update, txt, reply_markup=reply_markup)


# ==============================================================================
# 📊 DASHBOARD SORTING FEATURES
# ==============================================================================
async def dashboard_sort_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی انتخاب نوع مرتب‌سازی"""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    # حالت فعلی رو می‌خونیم
    current_sort = context.user_data.get('dash_sort', 'id')

    txt = (
        "📊 **تنظیمات نمایش داشبورد**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "می‌خواهید لیست سرورها بر چه اساسی مرتب شود؟"
    )

    # استفاده از ماژول کیبورد
    reply_markup = keyboard.dashboard_sort_kb(current_sort)

    await safe_edit_message(update, txt, reply_markup=reply_markup)


async def set_dashboard_sort_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره انتخاب کاربر و بازگشت به داشبورد"""
    query = update.callback_query
    sort_type = query.data.split('_')[3]  # uptime, traffic, etc.

    context.user_data['dash_sort'] = sort_type

    # ترجمه فارسی برای پیام تایید
    names = {'uptime': 'آپتایم', 'traffic': 'ترافیک', 'resource': 'منابع', 'id': 'زمان ثبت'}
    await query.answer(f"✅ مرتب‌سازی بر اساس {names.get(sort_type)} تنظیم شد.")

    # مستقیم برمی‌گردیم به داشبورد با تنظیمات جدید
    await status_dashboard(update, context)
# ==============================================================================
# 🎯 ADMIN REPORTS (ADVANCED)
# ==============================================================================

# State for User ID Input
ADMIN_GET_UID_FOR_REPORT = range(300)
async def admin_server_detail_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش حرفه‌ای جزئیات سرور (استفاده مجدد از server_detail)"""
    sid = update.callback_query.data.split('_')[2]
    # از تابع موجود server_detail استفاده می‌شود
    await server_detail(update, context, custom_sid=sid)
# ==============================================================================
# 📡 TUNNEL MONITORING ADMIN FLOW (REWRITTEN & ADVANCED)
# ==============================================================================

async def monitor_settings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پنل مدیریت سرور مانیتورینگ (هوشمند)"""
    uid = update.effective_user.id
    if uid != SUPER_ADMIN_ID: return
    
    if update.callback_query:
        try: await update.callback_query.answer()
        except: pass

    # بررسی اینکه آیا سرور مانیتورینگ وجود دارد یا خیر
    with db.get_connection() as (conn, cur):
        cur.execute("SELECT * FROM servers WHERE is_monitor_node=1 AND owner_id=%s", (SUPER_ADMIN_ID,)) # 👈 اجرا در یک خط
        monitor = cur.fetchone() # 👈 دریافت نتیجه در خط بعد

    # استفاده از ماژول کیبورد
    is_set = monitor is not None
    reply_markup = keyboard.monitor_node_kb(is_set)

    if not monitor:
        # --- حالت اول: هنوز سرور ست نشده ---
        desc = (
            "📡 **سیستم مانیتورینگ تانل (Iran Node)**\n"
            "➖➖➖➖➖➖➖➖➖➖\n"
            "در این سیستم می‌توانید چند Agent ایرانِ تأییدشده برای تست کانفیگ‌ها انتخاب کنید.\n\n"
            "⚠️ **وضعیت فعلی:** هنوز سرور ایران ست نشده است.\n\n"
            "✅ از گزینه انتخاب چند Agent استفاده کنید. نصب سرورهای انتخاب‌شده پس از تأیید شما آغاز می‌شود:"
        )
    else:
        # --- حالت دوم: سرور فعال است ---
        ip_censored = monitor['ip'] # نمایش آی‌پی
        desc = (
            "📡 **سیستم مانیتورینگ تانل (Iran Node)**\n"
            "➖➖➖➖➖➖➖➖➖➖\n"
            f"✅ **وضعیت:** فعال و متصل\n"
            f"🖥 **نام سرور:** `{monitor['name']}`\n"
            f"🌐 **آی‌پی:** `{ip_censored}`\n\n"
            "📂 **مدیریت فایل‌ها و ارتباط:**"
        )

    await safe_edit_message(update, desc, reply_markup=reply_markup)
# --- استیت‌های دریافت مشخصات سرور ایران ---
async def set_iran_monitor_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "📝 **یک نام برای سرور ایران انتخاب کنید:**\n(مثلاً: Iran-MCI)", reply_markup=keyboard.get_cancel_markup())
    return GET_IRAN_NAME

async def get_iran_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['iran_name'] = update.message.text
    await update.message.reply_text("🇮🇷 **آی‌پی سرور ایران را وارد کنید:**", reply_markup=keyboard.get_cancel_markup())
    return GET_IRAN_IP

async def get_iran_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['iran_ip'] = normalize_host(update.message.text)
    except ValueError:
        await update.message.reply_text('❌ آدرس IP یا نام میزبان معتبر وارد کنید.')
        return GET_IRAN_IP
    await update.message.reply_text("🔌 **پورت اتصال SSH را وارد کنید (پیش‌فرض 22):**", reply_markup=keyboard.get_cancel_markup())
    return GET_IRAN_PORT

async def get_iran_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        port = ssh_port(update.message.text)
        context.user_data['iran_port'] = port
        await update.message.reply_text("👤 **نام کاربری (Username) سرور ایران:**\n(معمولاً root)", reply_markup=keyboard.get_cancel_markup())
        return GET_IRAN_USER
    except:
        await update.message.reply_text("❌ لطفاً عدد وارد کنید.")
        return GET_IRAN_PORT

async def get_iran_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['iran_user'] = update.message.text
    await update.message.reply_text("🔑 **رمز عبور (Password) سرور ایران:**", reply_markup=keyboard.get_cancel_markup())
    return GET_IRAN_PASS

async def get_iran_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Share SSH fingerprint enrollment and background installation with the main wizard."""
    password = update.message.text
    name = str(context.user_data.get('iran_name') or '').strip()
    ip = str(context.user_data.get('iran_ip') or '').strip()
    port = int(context.user_data.get('iran_port') or 22)
    user = str(context.user_data.get('iran_user') or '').strip()
    if not name or not ip or not user or not 1 <= port <= 65535:
        await update.message.reply_text('❌ اطلاعات سرور نامعتبر است.')
        return ConversationHandler.END
    if update.effective_user.id != SUPER_ADMIN_ID:
        await update.message.reply_text('⛔ فقط مالک ربات مجاز است.')
        return ConversationHandler.END
    agent_port = int(AGENT_PORT)
    if not 1024 <= agent_port <= 65535:
        await update.message.reply_text('❌ پورت ایجنت نامعتبر است.')
        return ConversationHandler.END
    context.user_data['srv'] = {'name': '🇮🇷 ' + name, 'ip': normalize_host(ip),
        'port': ssh_port(port), 'username': user, 'password': sec.encrypt(password),
        'ws_port': agent_port, 'expiry_date': None, 'location_type': 'ir', '_activate_monitor_node': True}
    context.user_data['srv_group_id'] = 0
    for key in ('iran_name', 'iran_ip', 'iran_port', 'iran_user'):
        context.user_data.pop(key, None)
    from bot_logic_mod.servers import _complete_server_add
    return await _complete_server_add(update, context)


async def delete_monitor_node(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف فایل‌ها از سرور ایران و قطع ارتباط"""
    if not update.effective_user or int(update.effective_user.id) != int(SUPER_ADMIN_ID):
        if update.callback_query:
            await update.callback_query.answer('فقط مدیر اصلی اجازه اجرای این عملیات را دارد',show_alert=True)
        return

    query = update.callback_query
    await query.answer("🗑 در حال حذف فایل‌ها و قطع ارتباط...", show_alert=True)
    msg = await query.message.reply_text("⏳ **در حال پاکسازی سرور ایران...**")

    with db.get_connection() as (conn, cur):
        cur.execute("SELECT * FROM servers WHERE is_monitor_node=1 AND owner_id=%s", (SUPER_ADMIN_ID,))
        monitor = cur.fetchone()

    if not monitor:
        await msg.edit_text("❌ سروری یافت نشد.")
        return

    # دستورات پاکسازی
    cleanup_cmd = "systemctl disable --now sonar-agent && rm -f /etc/systemd/system/sonar-agent.service && systemctl daemon-reload"
    
    loop = asyncio.get_running_loop()
    try:
        server_cleaned,cleanup_detail = await run_sync(ServerMonitor._ssh_exec_sync,
            monitor['ip'],monitor['port'],monitor['username'],sec.decrypt(monitor['password']),cleanup_cmd,30)
    except Exception:
        server_cleaned=False
    if not server_cleaned:
        await msg.edit_text('❌ پاکسازی سرویس مقصد تأیید نشد؛ رکورد نود حفظ شد تا دوباره تلاش کنید.')
        return

    # حذف از دیتابیس (یا فقط برداشتن فلگ مانیتور)
    db.delete_server(monitor['id'], SUPER_ADMIN_ID)

    text = "✅ **ارتباط قطع شد.**\n"
    text += "🔹 سرور از لیست ربات حذف شد.\n"
    if server_cleaned:
        text += "🔹 سرویس ایجنت روی سرور ایران متوقف و از systemd حذف شد."
    else:
        text += "⚠️ نکته: نتوانستیم به سرور وصل شویم تا فایل‌ها را پاک کنیم (احتمالاً سرور خاموش است)."

    await msg.edit_text(text)
    await asyncio.sleep(2)
    await monitor_settings_panel(update, context)
async def update_monitor_node(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-enroll legacy monitor through the secure managed TLS installer."""
    if not update.effective_user or int(update.effective_user.id) != int(SUPER_ADMIN_ID):
        if update.callback_query:
            await update.callback_query.answer('فقط مدیر اصلی اجازه اجرای این عملیات را دارد',show_alert=True)
        return

    query = update.callback_query
    await query.answer()
    with db.get_connection() as (conn, cur):
        cur.execute('SELECT * FROM servers WHERE is_monitor_node=1 AND owner_id=%s', (SUPER_ADMIN_ID,))
        monitor = cur.fetchone()
    if not monitor:
        await query.message.reply_text('❌ نود مانیتورینگ ثبت نشده است.')
        return
    message = await query.message.reply_text('🦇 در حال تعمیر/به‌روزرسانی ایجنت...')
    loop = asyncio.get_running_loop()
    success, detail = await loop.run_in_executor(EXECUTOR, lambda: ServerMonitor.install_agent_service(
        monitor['ip'], int(monitor['port']), monitor['username'],
        sec.decrypt(monitor['password']), int(monitor.get('ws_port') or AGENT_PORT)))
    await message.edit_text(('✅ ' if success else '❌ ') + str(detail)[:950])
