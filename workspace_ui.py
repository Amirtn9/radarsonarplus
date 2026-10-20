"""Beginner-friendly reciprocal workspace invitation and server IP wizard.

No agent or WebSocket settings are changed here.
"""
from __future__ import annotations
import asyncio
import html
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from telegram.ext import ConversationHandler, filters
from secure_handlers import SecureCallbackQueryHandler as CQ, SecureMessageHandler as MH, SecureCommandHandler as CH
from bot_logic_mod.base import db
from workspace_service import WorkspaceService, WorkspaceError, validate_ip
from v6_ui import show
from runtime import run_sync

INVITEE, NEW_IP, CONFIRM_IP = 6401,6402,6403


def service():
    return WorkspaceService(db)


def back(callback):
    return M([[B('⬅️ بازگشت',callback_data=callback)],[B('🏠 خانه',callback_data='main_menu')]])


async def broadcast(context, actor:int, action:str, detail:str):
    """Best-effort partner alerts; never leak SSH secrets, config URLs or tokens."""
    try:
        people=await run_sync(service().members,actor)
    except Exception:
        return
    for peer in people:
        if peer==int(actor):continue
        try:
            await context.bot.send_message(peer, f'🔔 تغییر در فضای کاری مشترک\n\n👤 انجام‌دهنده: {actor}\n🛠 اقدام: {action}\n📋 جزئیات: {detail[:350]}\n\nاین تغییر برای همه اعضا اعمال شده است.')
        except Exception:
            # User may have blocked the bot. Core mutation must not be rolled back.
            pass


async def team_home(update,context):
    actor=update.effective_user.id
    members=await run_sync(service().members,actor)
    leader=await run_sync(service().owner,actor)
    txt=('👥 <b>فضای کاری مشترک</b>\n\n'
         'در این بخش می‌توانید سرورها، کانفیگ‌ها، گروه‌ها و تنظیمات فنی را با همکاران به اشتراک بگذارید.\n'
         'ادغام فقط با موافقت فرد دعوت‌شده انجام می‌شود. پس از ادغام، جداسازی خودکار اطلاعات وجود ندارد.\n\n'
         f'👤 مدیر فضا: <code>{leader}</code>\n'
         f'👥 اعضا: {len(members)}\n' + '\n'.join(f'• <code>{m}</code>' for m in members[:20]) +
         '\n\n💳 اعتبار، کیف پول و پرداخت‌های هر حساب خصوصی باقی می‌ماند.')
    buttons=[[B('🤝 درخواست هم‌گروهی',callback_data='ui_team_invite')]] if actor==leader else []
    buttons += [[B('⬅️ حساب و تیم',callback_data='v6_account')],[B('🏠 صفحه اصلی',callback_data='main_menu')]]
    await show(update,txt,M(buttons))


async def invite_start(update,context):
    actor=update.effective_user.id
    owner=await run_sync(service().owner,actor)
    if actor!=owner:
        await show(update,'تنها مدیر فضای کاری می‌تواند همکار جدید دعوت کند.',back('ui_team_home'))
        return ConversationHandler.END
    await show(update,'🤝 <b>دعوت همکار | مرحله ۱ از ۲</b>\n\nآیدی عددی تلگرام همکارتان را ارسال کنید.\nاو باید قبلاً ربات را Start کرده باشد.\n\nمثال: <code>123456789</code>',back('ui_team_cancel'))
    return INVITEE


async def invite_submit(update,context):
    raw=(update.effective_message.text or '').strip()
    if not raw.isascii() or not raw.isdecimal():
        await update.message.reply_text('❌ فقط آیدی عددی تلگرام را وارد کنید. یا /cancel را بزنید.')
        return INVITEE
    target=int(raw)
    try:
        request_id=await run_sync(service().request,update.effective_user.id,target)
    except (ValueError,WorkspaceError) as err:
        await update.message.reply_text(str(err),reply_markup=back('ui_team_home'))
        return ConversationHandler.END
    actor=update.effective_user
    name=html.escape(actor.full_name or str(actor.id))
    txt=(f'🤝 <b>درخواست ادغام فضای کاری</b>\n\n'
         f'👤 <b>{name}</b> (شناسه <code>{actor.id}</code>) از شما درخواست همکاری کرده است.\n\n'
         'در صورت تأیید، سرورها (همراه اطلاعات SSH)، کانفیگ‌ها، گروه‌ها و تنظیمات فنی شما به فضای کاری ایشان منتقل می‌شوند. '
         'از آن پس اضافه‌کردن یا حذف منابع برای هر دو نفر اعمال می‌شود. اگر تنظیماتی متناقض باشد، مقدار مدیر فضای کاری اولویت دارد.\n'
         '⚠️ هر عضو می‌تواند سرور مشترک را ویرایش یا حذف کند. پس از ادغام، جداسازی خودکار اطلاعات امکان‌پذیر نیست.\n'
         '💳 کیف پول و اطلاعات پرداخت مشترک نمی‌شود.\n\n'
         'آیا با ادغام موافق هستید؟')
    kb=M([[B('✅ تأیید ادغام',callback_data=f'ui_team_yes_{request_id}')],
          [B('❌ رد درخواست',callback_data=f'ui_team_no_{request_id}')]])
    try:
        await context.bot.send_message(target,txt,parse_mode='HTML',reply_markup=kb)
    except Exception:
        await run_sync(service().cancel_request,actor.id,request_id)
        await update.message.reply_text('⚠️ پیام دعوت به مقصد نرسید و درخواست لغو شد. از همکارتان بخواهید ابتدا ربات را باز کند.',reply_markup=back('ui_team_home'))
    else:
        await update.message.reply_text('✅ درخواست ارسال شد. ادغام فقط پس از قبول همکارتان انجام می‌شود.',reply_markup=back('ui_team_home'))
    return ConversationHandler.END


async def decide(update,context):
    raw=update.callback_query.data.split('_')
    accept=raw[2]=='yes'
    try:
        owner,accepted=await run_sync(service().decide,update.effective_user.id,int(raw[3]),accept)
    except WorkspaceError as exc:
        await show(update,'❌ '+html.escape(str(exc)),back('ui_team_home'))
        return
    if accepted:
        await show(update,'✅ <b>ادغام انجام شد.</b>\n\nاکنون فهرست سرورها، کانفیگ‌ها و تنظیمات فنی برای همه اعضا یکسان است.',back('ui_team_home'))
        await broadcast(context,update.effective_user.id,'پذیرش دعوت و ادغام فضا',f'کاربر {update.effective_user.id} وارد فضای مشترک شد.')
    else:
        await show(update,'درخواست رد شد. هیچ داده‌ای منتقل نشد.',back('ui_team_home'))
        try: await context.bot.send_message(owner,f'❌ کاربر {update.effective_user.id} درخواست هم‌گروهی را رد کرد.')
        except Exception: pass


def server_heading(server, title):
    """Same high-level server identity across all submenus; no new SSH poll."""
    def value(key, unit=''):
        item=server.get(key)
        return 'نامشخص' if item is None else html.escape(str(item))+unit
    return (f'{title}\n'
            f'🖥 <b>{html.escape(str(server.get("name") or "سرور"))}</b>\n'
            f'🌐 IP: <code>{html.escape(str(server.get("ip") or "?"))}</code>\n'
            f'📶 وضعیت: {html.escape(str(server.get("last_status") or "نامشخص"))}\n'
            f'🧠 CPU: {value("last_cpu","٪")} | 💾 RAM: {value("last_ram","٪")}\n'
            f'🛰 Agent: {html.escape(str(server.get("last_agent_status") or "نامشخص"))}\n'
            '━━━━━━━━━━━━━━\n')


async def server_tools(update,context):
    sid=int(update.callback_query.data.rsplit('_',1)[1])
    server=await run_sync(db.get_server_for_user,sid,update.effective_user.id)
    if not server:
        await update.callback_query.answer('این سرور در دسترس نیست.',show_alert=True);return
    buttons=[
        [B('📊 گزارش کامل',callback_data=f'act_fullreport_{sid}')],
        [B('🌐 تست ارتباط ایران',callback_data=f'act_checkhost_{sid}_{server["ip"]}')],
        [B('🚀 تست سرعت',callback_data=f'act_speedtest_{sid}'),B('📉 نمودار مصرف',callback_data=f'act_chart_{sid}')],
        [B('🧹 پاکسازی RAM',callback_data=f'act_clearcache_{sid}')],
        [B('⬅️ جزئیات سرور',callback_data=f'detail_{sid}')]]
    await show(update,server_heading(server,'🧰 <b>ابزارهای سرور</b>')+'ابزار مورد نظر را انتخاب کنید.',M(buttons))


async def server_settings(update,context):
    sid=int(update.callback_query.data.rsplit('_',1)[1])
    server=await run_sync(db.get_server_for_user,sid,update.effective_user.id)
    if not server:
        await update.callback_query.answer('سرور در دسترس نیست.',show_alert=True);return
    await show(update,server_heading(server,'⚙️ <b>تنظیمات سرور</b>')+'برای تغییر هر مورد یک مرحله جدید باز می‌شود.',
         M([[B('🌐 تغییر IP',callback_data=f'ui_ip_start_{sid}'),B('📅 تنظیم انقضا',callback_data=f'act_editexpiry_{sid}')],
            [B('⚙️ DNS',callback_data=f'act_dns_{sid}')],
            [B('⚠️ حذف سرور',callback_data=f'ui_delete_{sid}')],
            [B('⬅️ بازگشت',callback_data=f'detail_{sid}')]]))


async def server_advanced(update,context):
    sid=int(update.callback_query.data.rsplit('_',1)[1])
    srv=await run_sync(db.get_server_for_user,sid,update.effective_user.id)
    if not srv:
        await update.callback_query.answer('سرور در دسترس نیست.',show_alert=True);return
    kb=M([
      [B('🏢 اطلاعات دیتاسنتر',callback_data=f'act_datacenter_{sid}'),B('📝 گزارش جهانی',callback_data=f'act_fullreport_{sid}')],
      [B('📥 نصب ابزار سرعت',callback_data=f'act_installspeed_{sid}'),B('📦 بروزرسانی مخازن',callback_data=f'act_repoupdate_{sid}')],
      [B('💎 ارتقای سیستم',callback_data=f'act_fullupdate_{sid}'),B('🧹 پاکسازی دیسک',callback_data=f'act_cleandisk_{sid}')],
      [B('🛠 اجرای اسکریپت',callback_data=f'act_installscript_{sid}'),B('🧩 نصب/رفع ایجنت',callback_data=f'act_installagent_{sid}')],
      [B('📜 لاگ ایجنت',callback_data=f'act_agentlog_{sid}')],
      [B('⚠️ ریستارت سرور',callback_data=f'act_reboot_{sid}')],
      [B('⬅️ جزئیات سرور',callback_data=f'detail_{sid}')],
    ])
    await show(update,server_heading(srv,'🛠 <b>امکانات پیشرفته</b>')+'این عملیات ممکن است تنظیمات سرور را تغییر دهند؛ با دقت انتخاب کنید.',kb)


async def delete_confirm(update,context):
    sid=int(update.callback_query.data.rsplit('_',1)[1])
    srv=await run_sync(db.get_server_for_user,sid,update.effective_user.id)
    if not srv:
        await update.callback_query.answer('دسترسی ندارید.',show_alert=True);return
    await show(update,f'⚠️ <b>حذف سرور {html.escape(srv["name"])}</b>\n\nاین سرور برای تمام هم‌گروهی‌ها حذف می‌شود. مطمئن هستید؟',
         M([[B('🗑 بله، حذف شود',callback_data=f'act_del_{sid}')],
            [B('نه، بازگشت',callback_data=f'ui_server_settings_{sid}')]]))


async def wizard_home(update,context):
    context.user_data.pop('ui_ip_sid',None)
    context.user_data.pop('ui_ip_address',None)
    from bot_logic_mod.menu import main_menu
    await main_menu(update,context)
    return ConversationHandler.END


async def cancel_team(update,context):
    await show(update,'دعوت همکار لغو شد. هیچ درخواستی ارسال نشد.',back('ui_team_home'))
    return ConversationHandler.END


async def ip_start(update,context):
    sid=int(update.callback_query.data.rsplit('_',1)[1])
    srv=await run_sync(db.get_server_for_user,sid,update.effective_user.id)
    if not srv:
        await update.callback_query.answer('سرور در دسترس نیست.',show_alert=True);return ConversationHandler.END
    context.user_data['ui_ip_sid']=sid
    await show(update,f'🌐 <b>ویرایش آدرس سرور | مرحله ۱ از ۲</b>\n\n'
         f'سرور: {html.escape(srv["name"])}\n'
         f'IP فعلی: <code>{html.escape(srv["ip"])}</code>\n\n'
         'IP جدید را ارسال کنید.\nتنظیمات Agent و WebSocket تغییر نمی‌کنند؛ اگر آدرس Agent هم عوض شده باشد لازم است اتصال آن را جداگانه بررسی کنید.',M([[B('❌ انصراف از تغییر IP',callback_data='ui_ip_cancel')],
             [B('🏠 خانه',callback_data='main_menu')]]))
    return NEW_IP


async def ip_input(update,context):
    try: address=validate_ip(update.effective_message.text or '')
    except WorkspaceError as exc:
        await update.message.reply_text(f'❌ {exc}');return NEW_IP
    context.user_data['ui_ip_address']=address
    sid=context.user_data['ui_ip_sid']
    await update.message.reply_text(f'🌐 مرحله ۲ از ۲\n\nآدرس جدید: {address}\nآیا تغییر برای تمام اعضای فضای کاری ثبت شود؟',
        reply_markup=M([[B('✅ ثبت IP جدید',callback_data='ui_ip_save')],
                        [B('❌ انصراف',callback_data='ui_ip_cancel')]]))
    return CONFIRM_IP


async def ip_save(update,context):
    sid=context.user_data.pop('ui_ip_sid',None)
    new=context.user_data.pop('ui_ip_address',None)
    if not sid or not new:
        await show(update,'زمان عملیات به پایان رسید.',back('v6_servers'));return ConversationHandler.END
    try: name,old,ip=await run_sync(service().change_server_ip,update.effective_user.id,sid,new)
    except WorkspaceError as exc:
        await show(update,'❌ '+html.escape(str(exc)),back('v6_servers'));return ConversationHandler.END
    await show(update,f'✅ IP سرور <b>{html.escape(name)}</b> تغییر کرد.\n<code>{html.escape(old)}</code> ← <code>{html.escape(ip)}</code>',back(f'ui_server_settings_{sid}'))
    return ConversationHandler.END


async def cancel_ip(update,context):
    sid=context.user_data.pop('ui_ip_sid',None)
    context.user_data.pop('ui_ip_address',None)
    await show(update,'ویرایش IP لغو شد.',back(f'ui_server_settings_{sid}' if sid else 'v6_servers'))
    return ConversationHandler.END


def register(app):
    app.add_handler(CQ(team_home,pattern='^ui_team_home$'),group=0)
    app.add_handler(CQ(decide,pattern=r'^ui_team_(yes|no)_[0-9]+$'),group=0)
    app.add_handler(CQ(server_tools,pattern=r'^ui_server_tools_[0-9]+$'),group=0)
    app.add_handler(CQ(server_settings,pattern=r'^ui_server_settings_[0-9]+$'),group=0)
    app.add_handler(CQ(delete_confirm,pattern=r'^ui_delete_[0-9]+$'),group=0)
    app.add_handler(CQ(server_advanced,pattern=r'^ui_server_advanced_[0-9]+$'),group=0)
    app.add_handler(ConversationHandler(
        entry_points=[CQ(invite_start,pattern='^ui_team_invite$')],
        states={INVITEE:[MH(filters.TEXT & ~filters.COMMAND,invite_submit)]},
        fallbacks=[CQ(cancel_team,pattern='^ui_team_cancel$'),CQ(wizard_home,pattern='^main_menu$'),CH('cancel',cancel_team)],allow_reentry=True),group=0)
    app.add_handler(ConversationHandler(
        entry_points=[CQ(ip_start,pattern=r'^ui_ip_start_[0-9]+$')],
        states={NEW_IP:[MH(filters.TEXT & ~filters.COMMAND,ip_input)],
                CONFIRM_IP:[CQ(ip_save,pattern='^ui_ip_save$'),CQ(cancel_ip,pattern='^ui_ip_cancel$')]},
        fallbacks=[CQ(cancel_ip,pattern='^ui_ip_cancel$'),CQ(wizard_home,pattern='^main_menu$'),CH('cancel',cancel_ip)],allow_reentry=True),group=0)
