"""Interactive Telegram access to the same subscription and wallet service."""
import asyncio
import os
from telegram import InlineKeyboardButton as B,InlineKeyboardMarkup as M,WebAppInfo
from telegram.ext import CommandHandler,CallbackQueryHandler,MessageHandler,filters,ApplicationHandlerStop
from billing import Billing
from database import Database
from settings import SUPER_ADMIN_ID
from telegram_safety import safe_callback_answer


def service():return Billing(Database(),SUPER_ADMIN_ID)
async def identity(update):
    uid=update.effective_user.id
    user=await asyncio.to_thread(service().db.get_user,uid)
    if not user or user['is_banned']:raise PermissionError('ابتدا ربات را شروع کنید؛ حساب باید مسدود نباشد')
    return uid
async def display(update,text,buttons):
    if update.callback_query:
        await update.callback_query.edit_message_text(text,reply_markup=M(buttons))
    else:await update.effective_message.reply_text(text,reply_markup=M(buttons))
async def home(update,context):
    try:
        uid=await identity(update);data=await asyncio.to_thread(service().account,uid);sub=data['subscription']
        text='اشتراک خدمات پایش زیرساخت\nموجودی: '+format(data['user']['wallet_balance'] or 0,',')+' ریال\n'
        text+=('پلن: '+sub['snapshot']['name']+' | وضعیت: '+sub['effective_status']) if sub else 'حساب با تنظیمات قبلی'
        buttons=[[B('پلن‌ها و تمدید',callback_data='bill_plans')],[B('سفارش‌ها و رسید',callback_data='bill_orders')]]
        if 'payments' in await asyncio.to_thread(service().permissions,uid):buttons.append([B('بررسی پرداخت‌ها',callback_data='bill_admin')])
        url=os.getenv('SONAR_WEBAPP_URL','').strip()
        if url.startswith('https://'):buttons.append([B('مدیریت کامل در مینی‌اپ',web_app=WebAppInfo(url=url.split('?')[0]+'?view='+('users' if uid==SUPER_ADMIN_ID else 'billing')))])
        await display(update,text,buttons)
    except (ValueError,PermissionError) as error:await update.effective_message.reply_text(str(error))

async def callback(update,context):
    await safe_callback_answer(update.callback_query)
    try:
        uid=await identity(update);data=update.callback_query.data;parts=data.split('_')
        if data=='bill_home' or data.startswith(('wallet_menu','buy_plan_','pay_method_')):await home(update,context)
        elif data=='bill_plans':
            plans=await asyncio.to_thread(service().plans)
            buttons=[[B(f"{p['name']} | {p['price']:,} ریال | {p['days']} روز",callback_data='bill_plan_'+str(p['id']))] for p in plans]
            buttons.append([B('بازگشت',callback_data='bill_home')]);await display(update,'پلن خدمات پایش را انتخاب کنید:',buttons)
        elif data.startswith('bill_plan_'):
            ident=int(parts[-1]);plans=await asyncio.to_thread(service().plans);plan=next((p for p in plans if p['id']==ident),None)
            if not plan:raise ValueError('پلن یافت نشد')
            limits='\n'.join(f'{k}: {v}' for k,v in plan['limits'].items())
            buttons=[[B('پرداخت از کیف پول',callback_data=f'bill_wallet_{ident}')]]
            for destination in await asyncio.to_thread(service().destinations):buttons.append([B(destination['name']+' '+destination['network'],callback_data=f"bill_manual_{ident}_{destination['id']}")])
            buttons.append([B('بازگشت',callback_data='bill_plans')]);await display(update,f"{plan['name']}\n{plan['description']}\n{plan['price']:,} ریال | {plan['days']} روز\n{limits}",buttons)
        elif data.startswith(('bill_wallet_','bill_manual_')):
            ident=int(parts[2]);destination=int(parts[3]) if len(parts)>3 else None
            method='wallet' if parts[1]=='wallet' else 'manual'
            order_key=f'tg-{uid}-{update.callback_query.message.message_id}-{data}'
            order=await asyncio.to_thread(service().checkout,uid,ident,method,order_key,destination)
            text='اشتراک فعال شد.' if order['status']=='paid' else 'سفارش در انتظار بررسی پرداخت است.'
            if order.get('destination_snapshot'):
                d=order['destination_snapshot'];text+=f"\n{d['name']} | شبکه {d['network']}\n{d['address']}\n{d['instructions']}\nمبلغ سفارش {order['amount']:,} ریال است؛ مبلغ رمزارزی را مطابق راهنمای مدیر تعیین کنید."
            buttons=[]
            if order['status']=='pending':buttons.append([B('ارسال رسید یا شناسه تراکنش',callback_data='bill_receipt_'+str(order['id']))])
            buttons.append([B('حساب من',callback_data='bill_home')]);await display(update,text,buttons)
        elif data=='bill_orders':
            account=await asyncio.to_thread(service().account,uid);buttons=[];text='سفارش‌های اخیر:\n'
            for order in account['orders'][:10]:
                text+=f"{order['snapshot']['name'] if order.get('snapshot') else str(order['plan_id'])} | {order['amount']:,} ریال | {order['status']}\n"
                if order['status']=='pending':buttons.append([B('ثبت رسید '+str(order['id'])[:8],callback_data='bill_receipt_'+str(order['id']))])
            buttons.append([B('بازگشت',callback_data='bill_home')]);await display(update,text,buttons)
        elif data.startswith('bill_receipt_'):
            context.user_data['billing_evidence']=data[len('bill_receipt_'):]
            await update.effective_message.reply_text('تصویر رسید یا شناسه تراکنش را در پیام بعدی بفرستید. هرگز کلید خصوصی یا عبارت بازیابی کیف پول نفرستید. برای لغو /cancel را بزنید.')
        elif data=='bill_admin':
            service().admin(uid,'payments')
            rows=await asyncio.to_thread(service().db.execute,"SELECT id,user_id,amount,evidence FROM sonar_orders WHERE status='pending' ORDER BY created_at LIMIT 20",None,'all')
            buttons=[[B(f"کاربر {o['user_id']} | {o['amount']:,} ریال",callback_data='bill_review_'+str(o['id']))] for o in rows]
            buttons.append([B('بازگشت',callback_data='bill_home')]);await display(update,'پرداخت‌های در انتظار بررسی:',buttons)
        elif data.startswith('bill_review_'):
            service().admin(uid,'payments');ident=data[len('bill_review_'):]
            row=await asyncio.to_thread(service().db.execute,'SELECT * FROM sonar_orders WHERE id=%s',(ident,),'one')
            if not row:raise ValueError('سفارش یافت نشد')
            await display(update,f"کاربر {row['user_id']}\nمبلغ {row['amount']:,} ریال\nرسید: {row['evidence']}\nتأیید فقط پس از بررسی مستقل واریز واقعی.",[[B('تأیید',callback_data='bill_approve_'+ident),B('رد',callback_data='bill_reject_'+ident)],[B('بازگشت',callback_data='bill_admin')]])
            if row['evidence'].startswith('telegram-photo:'):await update.effective_message.reply_photo(row['evidence'].split(':',1)[1])
        elif data.startswith(('bill_approve_','bill_reject_')):
            service().admin(uid,'payments');context.user_data['billing_decision']=(parts[2],parts[1]=='approve')
            await update.effective_message.reply_text('دلیل تأیید یا رد را در پیام بعدی بنویسید. برای لغو /cancel را بزنید.')
    except (ValueError,PermissionError) as error:await update.effective_message.reply_text(str(error))
    raise ApplicationHandlerStop

async def receipt(update,context):
    evidence=context.user_data.get('billing_evidence');decision=context.user_data.get('billing_decision')
    if not evidence and not decision:return
    try:
        uid=await identity(update)
        if decision:
            text=(update.effective_message.text or '').strip()
            if len(text)<3:raise ValueError('دلیل حداقل سه‌حرفی لازم است')
            await asyncio.to_thread(service().decide,uid,decision[0],decision[1],text)
            context.user_data.pop('billing_decision',None)
        else:
            photos=update.effective_message.photo
            text='telegram-photo:'+photos[-1].file_id if photos else (update.effective_message.text or '')
            await asyncio.to_thread(service().evidence,uid,evidence,text)
            context.user_data.pop('billing_evidence',None)
        await update.effective_message.reply_text('ثبت شد.',reply_markup=M([[B('حساب و پرداخت‌ها',callback_data='bill_home')]]))
    except (ValueError,PermissionError) as error:await update.effective_message.reply_text(str(error))
    raise ApplicationHandlerStop

async def cancel(update,context):
    if context.user_data.pop('billing_evidence',None) or context.user_data.pop('billing_decision',None):
        await update.effective_message.reply_text('عملیات پرداخت لغو شد.');raise ApplicationHandlerStop

def register(app):
    app.add_handler(CommandHandler('subscription',home),group=-10)
    app.add_handler(CallbackQueryHandler(callback,pattern='^(?:bill_|wallet_menu$|buy_plan_|pay_method_)'),group=-10)
    app.add_handler(CommandHandler('cancel',cancel),group=-10)
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND)|filters.PHOTO,receipt),group=-10)
