"""
Auto-split module from original bot_logic.py
Section: ui
"""

from bot_logic_mod.base import *  # noqa

# 🎮 UI HELPERS & GENERAL HANDLERS
# ==============================================================================
async def safe_edit_message(update: Update, text, reply_markup=None, parse_mode='Markdown'):
    """Safely edit a callback message or reply to a message.

    Returns:
        telegram.Message | None
    """
    try:
        if update.callback_query:
            # اگر متن/کیبورد تغییر نکرده باشد، تلگرام BadRequest می‌دهد.
            return await update.callback_query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        if update.message:
            return await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    except BadRequest as e:
        # اگر ارور این بود که "Message is not modified"، نادیده بگیر
        if "Message is not modified" in str(e):
            return update.callback_query.message if update.callback_query else None
        logger.error("Message edit failed: %s",type(e).__name__)
        try:
            if update.callback_query:
                return await update.callback_query.edit_message_text(text=text,reply_markup=reply_markup,parse_mode=None)
            if update.message:
                return await update.message.reply_text(text,reply_markup=reply_markup,parse_mode=None)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"General Edit Error: {e}")
    return None


async def cancel_handler_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except:
            pass
    await safe_edit_message(update, "🚫 **عملیات لغو شد.**")
    await asyncio.sleep(1)
    await start(update, context)
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    from logging_config import redact
    error=context.error
    # No update/message text, callback payload, command, credentials or locals are serialized.
    frames=traceback.extract_tb(error.__traceback__) if error and error.__traceback__ else []
    location=' → '.join(f'{os.path.basename(f.filename)}:{f.lineno}:{f.name}' for f in frames[-8:])
    uid=getattr(getattr(update,'effective_user',None),'id',None)
    summary=f'Error type={type(error).__name__} user={uid} at={location}'
    logger.error('%s',redact(summary))
    if SUPER_ADMIN_ID:
        try:
            await context.bot.send_message(SUPER_ADMIN_ID,'🚨 خطای داخلی\n'+redact(summary)[:1800])
        except Exception:
            logger.error('Could not send sanitized error summary')
async def run_background_ssh_task(context: ContextTypes.DEFAULT_TYPE, chat_id, func, *args):
    # بررسی می‌کنیم آیا ظرفیت خالی داریم یا نه
    if GLOBAL_SEMAPHORE.locked():
        try:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ **سرور شلوغ است!**\nلطفاً چند لحظه صبر کنید تا پردازش‌های فعلی تمام شوند.")
        except: pass
        return

    loop = asyncio.get_running_loop()
    
    # ورود به صف پردازش با استفاده از سمافور
    async with GLOBAL_SEMAPHORE:
        try:
            # نکته مهم: اینجا به جای None، متغیر EXECUTOR رو پاس میدیم
            ok, output = await loop.run_in_executor(EXECUTOR, func, *args)
            
            clean_out = html.escape(str(output))
            if len(clean_out) > 3500:
                clean_out = clean_out[:3500] + "\n... (Output Truncated)"

            status_icon = "✅ عملیات با موفقیت انجام شد." if ok else "❌ عملیات با خطا مواجه شد."
            msg_text = (
                f"{status_icon}\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"<pre>{clean_out}</pre>"
            )
            await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode='HTML')

        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطای غیرمنتظره در عملیات پس‌زمینه:\n{e}")
# ==============================================================================
