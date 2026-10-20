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
        owned=db.execute('''SELECT id,name,ip,ws_port FROM servers WHERE owner_id=%s
               AND location_type='ir' AND is_active=1 ORDER BY id LIMIT 100''',
               (SUPER_ADMIN_ID,),fetch='all')
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
    text='🛰 **انتخاب چند Agent ایران**\n\nسرورهای ایرانِ خودتان و مواردی که مالکشان اشتراک‌گذاری را تأیید کرده نمایش داده می‌شوند.\n\nروی هر سرور بزنید تا تیک بخورد. سپس عملیات موردنظر را انتخاب کنید.\n\n⚠️ انتخاب، به‌تنهایی نصب یا تأیید ارتباط نیست.'
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
            owned=db.execute('SELECT id FROM servers WHERE owner_id=%s AND id=ANY(%s) AND location_type=%s',(SUPER_ADMIN_ID,ids,'ir'),fetch='all')
            for row in owned:offer(db,SUPER_ADMIN_ID,int(row['id']),True)
            valid={int(x['id']) for x in available(db,SUPER_ADMIN_ID,SUPER_ADMIN_ID)}
            if set(ids)-valid:raise PermissionError('Server was not offered by its owner')
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
        await q.edit_message_text(f'🔧 **نصب چندتایی**\n\n{len(ids)} سرور انتخاب شده است. نصب فقط روی سرورهای متعلق به مدیر امکان دارد.\n\n🔌 پورت ایجنت چگونه انتخاب شود؟',parse_mode='Markdown',reply_markup=Markup([
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
            await q.edit_message_text('❌ نصب گروهی ثبت نشد. فقط سرورهای ایران متعلق به مدیر و بدون نصب درحال‌اجرا مجازند.',reply_markup=Markup([[Button('🔙 بازگشت',callback_data='fleet_manage')]]))


def register(app):
    app.add_handler(SecureCallbackQueryHandler(manage,pattern=r'^fleet_manage$'))
    app.add_handler(SecureCallbackQueryHandler(dispatch,pattern=r'^fleet_(?:pick_\d+|apply|install_prepare|install_(?:stored|automatic))$'))
