"""Explicit, access-controlled Telegram commands for uptime services.

Commands:
  /uptime
  /uptime_add http Name|https://example.com/health
  /uptime_add tcp Name|example.com:443
  /uptime_pause 12
  /uptime_resume 12
  /uptime_delete 12
"""
from __future__ import annotations
import asyncio
from telegram.ext import CommandHandler
from secure_handlers import SecureCommandHandler
from pro_store import ProStore
from service_monitor import ServiceStore


def _service(): return ServiceStore(ProStore())

async def overview(update,context):
    owner=update.effective_user.id
    try:
        rows=await asyncio.to_thread(_service().list,owner,owner)
        text='📡 مانیتورینگ HTTP/TCP\n'
        for row in rows[:25]:
            icon={'up':'🟢','down':'🔴','pending':'⚪','paused':'⏸'}.get(row['status'],'⚪')
            text+=f'\n{icon} #{row["id"]} {row["name"][:60]} ({row["kind"].upper()})'
        text+='\n\nافزودن: /uptime_add http MySite|https://example.com/health\nافزودن TCP: /uptime_add tcp SSH|example.com:443\nتوقف: /uptime_pause ID\nفعال‌سازی: /uptime_resume ID\nحذف: /uptime_delete ID'
        await update.effective_message.reply_text(text[:3900])
    except (PermissionError,ValueError) as exc:
        await update.effective_message.reply_text(f'⛔ {exc}')

async def add(update,context):
    owner=update.effective_user.id
    text=' '.join(context.args or []).strip()
    try:
        kind,sep,rest=text.partition(' ')
        name,sep2,target=rest.partition('|')
        if not sep or not sep2:raise ValueError('Format: /uptime_add http Site|https://example.com/health')
        sid=await asyncio.to_thread(_service().create,owner,owner,name,kind,target)
        await update.effective_message.reply_text(f'✅ مانیتور #{sid} اضافه شد. اولین تست در چرخه بعدی انجام می‌شود.')
    except (PermissionError,ValueError) as exc:
        await update.effective_message.reply_text(f'⛔ {exc}')

async def action(update,context):
    owner=update.effective_user.id
    cmd=update.effective_message.text.split()[0].split('@')[0]
    try:
        ident=int(context.args[0])
        if ident<=0:raise ValueError('Invalid ID')
        svc=_service()
        if cmd=='/uptime_delete':await asyncio.to_thread(svc.delete,owner,owner,ident)
        else:await asyncio.to_thread(svc.set_enabled,owner,owner,ident,cmd=='/uptime_resume')
        await update.effective_message.reply_text('✅ تغییر ذخیره شد.')
    except (IndexError,ValueError,PermissionError) as exc:
        await update.effective_message.reply_text(f'⛔ شناسه سرویس نامعتبر یا دسترسی غیرمجاز است. {exc}')


def register(app):
    # SecureCommandHandler checks user access before any handler executes.
    for name,fn in [('uptime',overview),('uptime_add',add),('uptime_pause',action),
                    ('uptime_resume',action),('uptime_delete',action)]:
        app.add_handler(SecureCommandHandler(name,fn))
