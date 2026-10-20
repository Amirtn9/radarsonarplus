"""Beginner-first multi-select wizard for Iran monitoring agents.
Does not replace the original single-node installer or WebSocket protocol.
"""
from __future__ import annotations
import asyncio
from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup
from secure_handlers import SecureCallbackQueryHandler
from runtime import run_sync
from settings import SUPER_ADMIN_ID
from database import Database
from monitor_fleet import available,offer,approve,queue_installs,install_job_schema,install_schema


def _allowed(update):
    return bool(SUPER_ADMIN_ID and update.effective_user and update.effective_user.id==SUPER_ADMIN_ID)


async def manage(update,context):
    q=update.callback_query
    await q.answer()
    if not _allowed(update):
        await q.answer('فقط مدیر اصلی مجاز است',show_alert=True);return
    db=Database()
    def get():
        from monitor_fleet import install_schema, install_job_schema
        install_schema(db); install_job_schema(db)
        owned=db.execute('''SELECT id,name,ip,ws_port,owner_id FROM servers
               WHERE location_type='ir' AND is_active=1 ORDER BY id LIMIT 150''',fetch='all')
        offers=available(db,SUPER_ADMIN_ID,SUPER_ADMIN_ID)
        all_options={int(s['id']):dict(s) for s in owned}
        for row in offers:
            all_options.setdefault(int(row['id']),dict(row))
        return all_options,offers
    options,offers=await run_sync(get)
    selected=context.user_data.get('fleet_checked')
    if selected is None:
        selected=[int(item['id']) for item in offers if item['enabled']]
        context.user_data['fleet_checked']=selected
    buttons=[]
    for sid,item in list(options.items())[:40]:
        prefix='☑️' if sid in selected else '⬜️'
        buttons.append([Button(f'{prefix} {item["name"][:25]} • {str(item.get("ip",""))[:21]}',callback_data=f'fleet_pick_{sid}')])
    buttons.extend([[Button('✅ تأیید نودهای انتخاب‌شده',callback_data='fleet_apply')],
                    [Button('🧩 نصب Agent روی انتخاب‌ها',callback_data='fleet_install_prepare')],
                    [Button('🔙 بازگشت',callback_data='monitor_settings_panel')]])
    text='🛰 **انتخاب چند Agent ایران**\n\nمدیر اصلی می‌تواند Agentهای سالم ثبت‌شده برای حساب‌های مختلف را فعال کند؛ استفاده از SSH و بررسی سلامت در لاگ ثبت می‌شود.\n\nروی هر سرور بزنید تا تیک بخورد. سپس عملیات موردنظر را انتخاب کنید.\n\n⚠️ انتخاب، به‌تنهایی نصب یا تأیید ارتباط نیست.'
    if not options:text+='\n\nهیچ سرور ایران واجد شرایطی ثبت نشده است.'
    await q.edit_message_text(text,reply_markup=Markup(buttons),parse_mode='Markdown')


async def dispatch(update,context):
    q=update.callback_query;code=q.data
    if not _allowed(update):
        await q.answer('فقط مدیر اصلی مجاز است',show_alert=True);return
    if code.startswith('fleet_pick_'):
        sid=int(code.rsplit('_',1)[1]);ids=set(context.user_data.get('fleet_checked',[]))
        if sid in ids:ids.remove(sid)
        else:ids.add(sid)
        context.user_data['fleet_checked']=sorted(ids)
        return await manage(update,context)
    if code=='fleet_apply':
        ids=context.user_data.get('fleet_checked') or []
        if not ids:
            await q.answer('حداقل یک سرور را انتخاب کنید',show_alert=True);return
        await q.answer()
        def save():
            db=Database();options=available(db,SUPER_ADMIN_ID,SUPER_ADMIN_ID)
            valid={int(x['id']) for x in available(db,SUPER_ADMIN_ID,SUPER_ADMIN_ID)}
            registered=db.execute('SELECT id FROM servers WHERE id=ANY(%s) AND location_type=%s AND is_active=1',
                                  (ids,'ir'),fetch='all')
            if set(ids)-{int(x['id']) for x in registered}:raise PermissionError('Unregistered or inactive Iran server')
            for sid in ids:approve(db,SUPER_ADMIN_ID,sid,True)
            return len(ids)
        try:
            total=await run_sync(save)
            await q.edit_message_text(f'✅ {total} نود در استخر فعال شد. فقط Agentهایی برای تست انتخاب می‌شوند که WSS سالم و تازه داشته باشند.',reply_markup=Markup([[Button('🔙 فهرست Agentها',callback_data='fleet_manage')]]))
        except Exception:
            await q.edit_message_text('❌ انتخاب تأیید نشد. مجوز مالک یا ثبت سرور را بررسی کنید.',reply_markup=Markup([[Button('🔙 بازگشت',callback_data='fleet_manage')]]))
        return
    if code=='fleet_install_prepare':
        await q.answer()
        ids=context.user_data.get('fleet_checked') or []
        await q.edit_message_text(f'🔧 **نصب چندتایی**\n\n{len(ids)} سرور انتخاب شده است. مدیر اصلی می‌تواند برای سرورهای ثبت‌شدهٔ کاربران نیز نصب را درخواست کند؛ این عملیات به SSH معتبر نیاز دارد و ثبت می‌شود.\n\n🔌 پورت ایجنت چگونه انتخاب شود؟',parse_mode='Markdown',reply_markup=Markup([
            [Button('پورت ثبت‌شده هر سرور',callback_data='fleet_install_stored')],
            [Button('🎲 پورت آزاد خودکار (۲۰۰۰۰ تا ۵۹۹۹۹)',callback_data='fleet_install_automatic')],
            [Button('🔙 بازگشت',callback_data='fleet_manage')]]))
        return
    if code.startswith('fleet_install_'):
        await q.answer()
        mode=code.removeprefix('fleet_install_')
        if mode not in ('stored','automatic'):return
        ids=context.user_data.get('fleet_checked') or []
        if not ids:
            # Callback was already acknowledged; provide visible feedback.
            await q.edit_message_text('سروری انتخاب نشده است. به فهرست بازگردید.',reply_markup=Markup([[Button('🔙 فهرست',callback_data='fleet_manage')]]));return
        try:
            jobs=await run_sync(queue_installs,Database(),SUPER_ADMIN_ID,ids,mode)
            await q.edit_message_text(f'⏳ {len(jobs)} درخواست نصب در صف ثبت شد.\n\nپیشرفت از طریق پنل وب قابل مشاهده است. نصب پس از بررسی واقعی WSS موفق اعلام می‌شود.',reply_markup=Markup([[Button('🔙 فهرست',callback_data='fleet_manage')]]))
        except Exception:
            await q.edit_message_text('❌ نصب گروهی ثبت نشد. تنها سرورهای ثبت‌شده و فعال ایران بدون نصب درحال‌اجرا مجازند.',reply_markup=Markup([[Button('🔙 بازگشت',callback_data='fleet_manage')]]))


def register(app):
    app.add_handler(SecureCallbackQueryHandler(traffic_menu,pattern=r'^fleet_traffic$'))
    app.add_handler(SecureCallbackQueryHandler(traffic_reset,pattern=r'^fleet_meter_(?:reset|confirm)_\d+$'))
    app.add_handler(SecureCallbackQueryHandler(manage,pattern=r'^fleet_manage$'))
    app.add_handler(SecureCallbackQueryHandler(dispatch,pattern=r'^fleet_(?:pick_\d+|apply|install_prepare|install_(?:stored|automatic))$'))


def _gb(value):
    return f'{int(value or 0)/1073741824:,.3f} GB'


async def traffic_menu(update, context):
    """Root-only usage view; totals are WSS/relay bytes measured by Agent."""
    q=update.callback_query
    await q.answer()
    if not _allowed(update):
        await q.answer('فقط مدیر اصلی مجاز است',show_alert=True)
        return
    from agent_traffic import admin_report
    rows=await run_sync(admin_report,Database(),SUPER_ADMIN_ID)
    buttons=[]
    text=['📊 <b>مصرف ارتباطی Agentها</b>',
          '📌 این اندازه‌گیری مجموع بایت‌های واقعی WSS و تونل SSH است؛ مصرف جداگانه Xray و ترافیک کل VPS نیست.',
          '⏱ نمونه‌برداری دوره‌ای است؛ بایت‌های بین خاموشی یا راه‌اندازی مجدد Agent ممکن است کامل ثبت نشده باشند.']
    import html
    for row in rows[:12]:
        text.append(f'\n🛰 <b>{html.escape(str(row["name"]))}</b> | #{row["agent_id"]}'
                    f' | مالک: <code>{row["owner_id"]}</code>\n'
                    f'کل پس از آخرین ریست: {_gb(row["total"])}\n'
                    f'امروز: {_gb(row["today"])} | ۷ روز: {_gb(row["week"])} | ۳۰ روز: {_gb(row["month"])}\n'
                    f'آخرین نمونه: {html.escape(str(row["sampled_at"] or "ثبت نشده"))}')
        buttons.append([Button(f'🔄 ریست شمارنده {str(row["name"])[:19]}',callback_data=f'fleet_meter_reset_{row["agent_id"]}')])
    if not rows:text.append('\nهنوز اطلاعات قابل‌اندازه‌گیری دریافت نشده است.')
    buttons.append([Button('🔁 تازه‌سازی',callback_data='fleet_traffic')])
    buttons.append([Button('⬅️ مدیریت',callback_data='v6_admin')])
    await q.edit_message_text('\n'.join(text)[:3950],parse_mode='HTML',reply_markup=Markup(buttons[:14]))


async def traffic_reset(update,context):
    q=update.callback_query
    await q.answer()
    if not _allowed(update):
        await q.answer('فقط مدیر اصلی مجاز است',show_alert=True);return
    bits=q.data.split('_')
    try:agent_id=int(bits[-1]);confirmed='confirm' in bits
    except ValueError:return
    if not confirmed:
        await q.edit_message_text(f'⚠️ بازنشانی نمایش مصرف Agent شماره {agent_id}؟\nداده‌های تاریخی برای بررسی و حسابرسی حفظ می‌شوند.',reply_markup=Markup([
           [Button('✅ تأیید ریست',callback_data=f'fleet_meter_confirm_{agent_id}')],
           [Button('❌ انصراف',callback_data='fleet_traffic')]]))
        return
    from agent_traffic import reset_meter
    try:
        epoch=await run_sync(reset_meter,Database(),SUPER_ADMIN_ID,agent_id)
        await q.edit_message_text(f'✅ شمارنده Agent شماره {agent_id} بازنشانی شد. دوره جدید: {epoch}\nآمار قبلی حذف نشده است.',reply_markup=Markup([[Button('⬅️ گزارش‌ها',callback_data='fleet_traffic')]]))
    except Exception:
        await q.edit_message_text('❌ ریست ناموفق بود؛ ابتدا باید یک نمونه مصرف معتبر دریافت شده باشد.',reply_markup=Markup([[Button('⬅️ بازگشت',callback_data='fleet_traffic')]]))
