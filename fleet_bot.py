"""Multi-select wizard for approved Iran/foreign relay Agents and admin-owned installs.
Does not replace the original single-node installer or WebSocket protocol.
"""
from __future__ import annotations
import asyncio
import logging
from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup
from secure_handlers import SecureCallbackQueryHandler
from runtime import run_sync
from settings import SUPER_ADMIN_ID
from database import Database
from monitor_fleet import available_workspace_nodes,offer,approve,queue_installs,install_job_schema,install_schema


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
        rows=db.execute('''SELECT s.id,s.name,s.ip,s.port,s.ws_port,s.owner_id,s.location_type,
              COALESCE(a.agent_cert_pem,'')<>'' AS tls_enrolled,
              COALESCE(a.agent_token,'')<>'' AS token_configured,
              COALESCE(f.enabled,FALSE) AS enabled,(f.approved_by IS NOT NULL) AS approved,
              COALESCE(f.relay_ready,FALSE) AS relay_ready,
              EXISTS(SELECT 1 FROM monitor_fleet_nodes offered WHERE offered.server_id=s.id AND offered.offered_by=s.owner_id AND offered.admin_only=FALSE) AS owner_opted_in
            FROM servers s LEFT JOIN agent_nodes a ON a.ip=s.ip AND a.ssh_port=s.port
            LEFT JOIN monitor_fleet_nodes f ON f.server_id=s.id
            WHERE s.is_active=1 AND s.location_type IN ('ir','ext')
              AND (s.owner_id=%s OR EXISTS(SELECT 1 FROM monitor_fleet_nodes offered WHERE offered.server_id=s.id AND offered.offered_by=s.owner_id)
                   OR f.enabled=TRUE)
            ORDER BY CASE WHEN s.location_type='ir' THEN 0 ELSE 1 END,s.id LIMIT 250''',(int(SUPER_ADMIN_ID),),fetch='all')
        options={int(row['id']):dict(row) for row in rows or []}
        offers=available_workspace_nodes(db,SUPER_ADMIN_ID,SUPER_ADMIN_ID)
        return options,offers
    options,offers=await run_sync(get)
    selected=context.user_data.get('fleet_checked')
    if selected is None:
        selected=[int(item['id']) for item in offers if item['enabled']]
        context.user_data['fleet_checked']=selected
    buttons=[]
    for sid,item in list(options.items())[:40]:
        prefix='☑️' if sid in selected else '⬜️'
        region='IR' if item.get('location_type')=='ir' else 'EXT'
        state='TLS' if item.get('tls_enrolled') and item.get('token_configured') else 'NO-AGENT'
        buttons.append([Button(f'{prefix} [{region}/{state}] {item["name"][:20]} • {str(item.get("ip",""))[:18]}',callback_data=f'fleet_pick_{sid}')])
    buttons.extend([[Button('✅ تأیید نودهای انتخاب‌شده',callback_data='fleet_apply')],
                    [Button('🧩 نصب Agent روی انتخاب‌ها',callback_data='fleet_install_prepare')],
                    [Button('🔙 بازگشت',callback_data='monitor_settings_panel')]])
    text=('🛰 **مدیریت Agentهای ایران و خارج**\n\n'
          'مسیر اصلی همیشه سرور مرکزی است؛ Agentهای تأییدشده فقط هنگام خطای شبکه مسیر جایگزین می‌شوند.\n\n'
          'نصب گروهی فقط روی سرورهای متعلق به مدیر اصلی مجاز است. مالکین دیگر باید Agent را روی سرور خود نصب و برای اشتراک پیشنهاد کنند.\n\n'
          'نشان TLS یعنی گواهی/توکن ثبت شده است؛ NO-AGENT یعنی ابتدا نصب/تعمیر لازم است. انتخاب به‌تنهایی نصب یا تأیید ارتباط نیست.')
    if not options:text+='\n\nهیچ سرور فعالی در ایران یا خارج ثبت نشده است.'
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
            db=Database();done=0
            for sid in ids:
                approve(db,SUPER_ADMIN_ID,sid,True);done+=1
            return done
        try:
            total=await run_sync(save)
            await q.edit_message_text(f'✅ {total} نود در استخر فعال شد. فقط Agentهایی برای تست انتخاب می‌شوند که WSS سالم و تازه داشته باشند.',reply_markup=Markup([[Button('🔙 فهرست Agentها',callback_data='fleet_manage')]]))
        except Exception:
            await q.edit_message_text('❌ انتخاب تأیید نشد. مجوز مالک یا ثبت سرور را بررسی کنید.',reply_markup=Markup([[Button('🔙 بازگشت',callback_data='fleet_manage')]]))
        return
    if code=='fleet_install_prepare':
        await q.answer()
        ids=context.user_data.get('fleet_checked') or []
        await q.edit_message_text(f'🔧 **نصب چندتایی**\n\n{len(ids)} سرور انتخاب شده است. نصب گروهی فقط روی سرورهای متعلق به مدیر اصلی در ایران یا خارج مجاز است؛ سرورهای دیگر باید توسط مالک نصب و برای اشتراک پیشنهاد شوند.\n\n🔌 پورت ایجنت چگونه انتخاب شود؟',parse_mode='Markdown',reply_markup=Markup([
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
            await q.edit_message_text('❌ نصب گروهی ثبت نشد. فقط سرورهای فعال متعلق به مدیر اصلی مجازند؛ سرورهای دیگر باید توسط مالک مدیریت شوند.',reply_markup=Markup([[Button('🔙 بازگشت',callback_data='fleet_manage')]]))


def register(app):
    # Apply additive fleet/affinity schema even when the web service is offline.
    # Handler registration remains resilient in isolated test/minimal environments.
    try:
        _db=Database();install_schema(_db);install_job_schema(_db)
    except Exception as exc:
        logging.getLogger(__name__).warning('Fleet schema bootstrap deferred: %s',type(exc).__name__)
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
