"""Telegram controls for the same durable config alert preferences as the Web UI."""
from urllib.parse import urlsplit, urlunsplit
import os
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, WebAppInfo
from secure_handlers import SecureCallbackQueryHandler, SecureCommandHandler


async def menu(update, context):
    from pro_handlers import store, owned_context, display, invoke
    from config_alerts import settings, save_settings
    uid = update.effective_user.id
    owner = owned_context(update, context)
    try:
        await invoke(store.require, uid, owner)
        prefs = await invoke(settings, store.db, owner)
        data = update.callback_query.data if update.callback_query else 'ca_home'
        if data != 'ca_home':
            parts = data.split('_')
            if data.startswith('ca_toggle_'):
                key = data.removeprefix('ca_toggle_')
                if key not in ('enabled','notify_down','notify_recovery','notify_score','scheduled','report_enabled'):
                    raise ValueError('گزینه نامعتبر')
                prefs[key] = not prefs[key]
            elif len(parts)==3 and parts[1] in ('interval','threshold','report') and parts[2].isdigit():
                prefs[{'interval':'probe_interval_minutes','threshold':'failure_threshold','report':'report_interval_minutes'}[parts[1]]] = int(parts[2])
            else:
                raise ValueError('گزینه نامعتبر')
            await invoke(save_settings, store, uid, owner, prefs)
        flag = lambda key: '✅' if prefs[key] else '⏸'
        text = ('🔔 <b>مرکز اعلان کانفیگ‌ها</b>\n\n'
                f"اعلان‌ها: {flag('enabled')} | تست دوره‌ای: {flag('scheduled')}\n"
                f"فاصله تست: {prefs['probe_interval_minutes']} دقیقه · آستانه قطعی: {prefs['failure_threshold']} شکست متوالی\n"
                f"گزارش: هر {prefs['report_interval_minutes']} دقیقه · تغییر امتیاز: {prefs['score_delta']} از ۱۰۰\n\n"
                'اعلان فوری حداکثر حدود ۱۵ ثانیه پس از ثبت نتیجه در صف قرار می‌گیرد؛ تشخیص قطعی به زمان تست وابسته است. '
                'تست خودکار به Agent سالم نیاز دارد. خطای خود Agent قطعی کانفیگ محسوب نمی‌شود.\n'
                'برای ساعت سکوت، منطقه زمانی، فاصله پیام‌های امتیاز و بی‌صداکردن کانفیگ خاص، تنظیمات پیشرفته را باز کنید.')
        rows = [[B(f"{flag('enabled')} همه اعلان‌ها",callback_data='ca_toggle_enabled')],
                [B(f"{flag('notify_down')} قطعی",callback_data='ca_toggle_notify_down'),B(f"{flag('notify_recovery')} اتصال مجدد",callback_data='ca_toggle_notify_recovery')],
                [B(f"{flag('notify_score')} تغییر امتیاز",callback_data='ca_toggle_notify_score')],
                [B(f"{flag('scheduled')} تست زمان‌بندی‌شده",callback_data='ca_toggle_scheduled')],
                [B(f'{n} دقیقه',callback_data=f'ca_interval_{n}') for n in (2,5,10,30,60)],
                [B(f'{n} شکست',callback_data=f'ca_threshold_{n}') for n in (1,2,3,5)],
                [B(f"{flag('report_enabled')} گزارش دوره‌ای امتیازها",callback_data='ca_toggle_report_enabled')],
                [B(f'گزارش {n} دقیقه',callback_data=f'ca_report_{n}') for n in (15,60,180)]]
        parsed = urlsplit(os.getenv('SONAR_WEBAPP_URL',''))
        if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
            url = urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'view=settings',''))
            rows.append([B('⚙️ تنظیمات پیشرفته در مینی‌اپ', web_app=WebAppInfo(url=url))])
        rows.append([B('📢 مقصد اعلان‌ها',callback_data='channels_menu'),B('📚 راهنما',callback_data='v6_guide')])
        rows.append([B('⬅️ تنظیمات',callback_data='v6_settings')])
        await display(update,text,M(rows))
    except (ValueError, PermissionError) as error:
        await display(update,str(error),M([[B('بازگشت',callback_data='ca_home')]]))


def register(app):
    app.add_handler(SecureCommandHandler('configalerts',menu),group=0)
    app.add_handler(SecureCallbackQueryHandler(menu,pattern=r'^ca_(?:home|toggle_[a-z_]+|(?:interval|threshold|report)_\d+)$'),group=0)
