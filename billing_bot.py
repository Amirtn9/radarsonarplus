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
def toman(value):
    value=int(value or 0)
    return format(value//10,',')+('.'+str(value%10) if value%10 else '')+' تومان'

async def identity(update):
    uid=update.effective_user.id
    user=await asyncio.to_thread(service().db.get_user,uid)
    if not user or user['is_banned']:raise PermissionError('ابتدا ربات را شروع کنید؛ حساب باید مسدود نباشد')
    return uid
async def display(update,text,buttons):
    if update.callback_query:
        await update.callback_query.edit_message_text(text,reply_markup=M(buttons))
    else:await update.effective_message.reply_text(text,reply_markup=M(buttons))
def clear_flow(context):
    for key in ('billing_evidence','billing_decision','billing_manage'):
        context.user_data.pop(key,None)

async def home(update,context):
    clear_flow(context)
    try:
        uid=await identity(update);data=await asyncio.to_thread(service().account,uid);sub=data['subscription']
        text='اشتراک خدمات پایش زیرساخت\nموجودی: '+toman(data['user']['wallet_balance'])+'\n'
        text+=('پلن: '+sub['snapshot']['name']+' | وضعیت: '+sub['effective_status']) if sub else 'حساب با تنظیمات قبلی'
        buttons=[[B('پلن‌ها و تمدید',callback_data='bill_plans')],[B('سفارش‌ها و رسید',callback_data='bill_orders')]]
        if 'payments' in await asyncio.to_thread(service().permissions,uid):buttons.append([B('بررسی پرداخت‌ها',callback_data='bill_admin')])
        if await asyncio.to_thread(service().permissions,uid):buttons.append([B('مدیریت مشتری و آمار',callback_data='bill_console')])
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
            plans=await asyncio.to_thread(service().plans,uid)
            buttons=[[B(f"{p['name']} | {toman(p['price'])} | {p['days']} روز",callback_data='bill_plan_'+str(p['id']))] for p in plans]
            buttons.append([B('بازگشت',callback_data='bill_home')]);await display(update,'پلن خدمات پایش را انتخاب کنید:',buttons)
        elif data.startswith('bill_plan_'):
            ident=int(parts[-1]);plans=await asyncio.to_thread(service().plans,uid);plan=next((p for p in plans if p['id']==ident),None)
            if not plan:raise ValueError('پلن یافت نشد')
            limits='\n'.join(f'{k}: {v}' for k,v in plan['limits'].items())
            buttons=[[B('پرداخت از کیف پول',callback_data=f'bill_wallet_{ident}')]]
            for destination in await asyncio.to_thread(service().destinations):buttons.append([B(destination['name']+' '+destination['network'],callback_data=f"bill_manual_{ident}_{destination['id']}")])
            buttons.append([B('بازگشت',callback_data='bill_plans')]);await display(update,f"{plan['name']}\n{plan['description']}\n{toman(plan['price'])} | {plan['days']} روز\n{limits}",buttons)
        elif data.startswith(('bill_wallet_','bill_manual_')):
            ident=int(parts[2]);destination=int(parts[3]) if len(parts)>3 else None
            method='wallet' if parts[1]=='wallet' else 'manual'
            order_key=f'tg-{uid}-{update.callback_query.message.message_id}-{data}'
            order=await asyncio.to_thread(service().checkout,uid,ident,method,order_key,destination)
            text='اشتراک فعال شد.' if order['status']=='paid' else 'سفارش در انتظار بررسی پرداخت است.'
            if order.get('destination_snapshot'):
                d=order['destination_snapshot'];text+=f"\n{d['name']} | شبکه {d['network']}\n{d['address']}\n{d['instructions']}\nمبلغ سفارش {toman(order['amount'])} است؛ مبلغ رمزارزی را مطابق راهنمای مدیر تعیین کنید."
            buttons=[]
            if order['status']=='pending':buttons.append([B('ارسال رسید یا شناسه تراکنش',callback_data='bill_receipt_'+str(order['id']))])
            buttons.append([B('حساب من',callback_data='bill_home')]);await display(update,text,buttons)
        elif data=='bill_orders':
            account=await asyncio.to_thread(service().account,uid);buttons=[];text='سفارش‌های اخیر:\n'
            for order in account['orders'][:10]:
                text+=f"{order['snapshot']['name'] if order.get('snapshot') else str(order['plan_id'])} | {toman(order['amount'])} | {order['status']}\n"
                if order['status']=='pending':buttons.append([B('ثبت رسید '+str(order['id'])[:8],callback_data='bill_receipt_'+str(order['id']))])
            buttons.append([B('بازگشت',callback_data='bill_home')]);await display(update,text,buttons)
        elif data.startswith('bill_receipt_'):
            clear_flow(context)
            context.user_data['billing_evidence']=data[len('bill_receipt_'):]
            await update.effective_message.reply_text('تصویر رسید یا شناسه تراکنش را در پیام بعدی بفرستید. هرگز کلید خصوصی یا عبارت بازیابی کیف پول نفرستید. برای لغو /cancel را بزنید.')
        elif data=='bill_console':
            from product_admin import Product
            summary=await asyncio.to_thread(Product(service().db,SUPER_ADMIN_ID).overview,uid)
            text='مدیریت مرکزی · آمار ثبت‌شده\n'
            if 'users' in summary:text+=f"کاربران: {summary['users']['total']}\n"
            if 'subscriptions' in summary:text+=f"اشتراک فعال: {summary['subscriptions']['active']} | نزدیک انقضا: {summary['subscriptions']['expiring']}\n"
            if 'payments' in summary:text+=f"پرداخت در انتظار: {summary['payments']['pending']}\n"
            buttons=[]
            if 'users.read' in summary['permissions']:buttons.append([B('انتخاب مشتری',callback_data='bill_customer')])
            buttons.append([B('بازگشت',callback_data='bill_home')]);await display(update,text,buttons)
        elif data=='bill_customer':
            service().admin(uid,'users.read');clear_flow(context);context.user_data['billing_manage']={'step':'user'}
            await update.effective_message.reply_text('شناسه عددی تلگرام مشتری را بفرستید. برای لغو /cancel را بزنید.')
        elif data.startswith('bill_action_'):
            action=parts[2];target=int(parts[3]);caps={'extend':'subscriptions','suspend':'subscriptions','activate':'subscriptions','message':'messages','wallet':'wallet'}
            if action not in caps:raise ValueError('عملیات معتبر نیست')
            service().admin(uid,caps[action])
            flow={'step':'value','action':action,'target':target,'key':f"tg-manage-{uid}-{update.callback_query.message.message_id}-{data}"}
            if action in ('suspend','activate'):
                flow['step']='reason';flow['value']=action
            clear_flow(context)
            context.user_data['billing_manage']=flow
            prompt={'extend':'تعداد روز افزایش یا کاهش اعتبار را بفرستید (منفی برای کاهش).','wallet':'مبلغ صحیح تومان را بفرستید (منفی برای کسر).','message':'متن پیام خصوصی را بفرستید.','suspend':'دلیل تعلیق اشتراک را بفرستید.','activate':'دلیل فعال‌کردن اشتراک را بفرستید.'}
            await update.effective_message.reply_text(prompt[action]+' برای لغو /cancel را بزنید.')
        elif data=='bill_admin':
            service().admin(uid,'payments')
            rows=await asyncio.to_thread(service().db.execute,"SELECT id,user_id,amount,evidence FROM sonar_orders WHERE status='pending' ORDER BY created_at LIMIT 20",None,'all')
            buttons=[[B(f"کاربر {o['user_id']} | {toman(o['amount'])}",callback_data='bill_review_'+str(o['id']))] for o in rows]
            buttons.append([B('بازگشت',callback_data='bill_home')]);await display(update,'پرداخت‌های در انتظار بررسی:',buttons)
        elif data.startswith('bill_review_'):
            service().admin(uid,'payments');ident=data[len('bill_review_'):]
            row=await asyncio.to_thread(service().db.execute,'SELECT * FROM sonar_orders WHERE id=%s',(ident,),'one')
            if not row:raise ValueError('سفارش یافت نشد')
            await display(update,f"کاربر {row['user_id']}\nمبلغ {toman(row['amount'])}\nرسید: {row['evidence']}\nتأیید فقط پس از بررسی مستقل واریز واقعی.",[[B('تأیید',callback_data='bill_approve_'+ident),B('رد',callback_data='bill_reject_'+ident)],[B('بازگشت',callback_data='bill_admin')]])
            if row['evidence'].startswith('telegram-photo:'):await update.effective_message.reply_photo(row['evidence'].split(':',1)[1])
        elif data.startswith(('bill_approve_','bill_reject_')):
            service().admin(uid,'payments');clear_flow(context);context.user_data['billing_decision']=(parts[2],parts[1]=='approve')
            await update.effective_message.reply_text('دلیل تأیید یا رد را در پیام بعدی بنویسید. برای لغو /cancel را بزنید.')
    except (ValueError,PermissionError) as error:await update.effective_message.reply_text(str(error))
    raise ApplicationHandlerStop

async def receipt(update,context):
    evidence=context.user_data.get('billing_evidence');decision=context.user_data.get('billing_decision')
    manage=context.user_data.get('billing_manage')
    if not evidence and not decision and not manage:return
    try:
        uid=await identity(update)
        if manage:
            await manage_input(update,context,uid,manage)
            raise ApplicationHandlerStop
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

async def manage_input(update,context,uid,flow):
    text=(update.effective_message.text or '').strip()
    if flow['step']=='user':
        service().admin(uid,'users.read');target=int(text)
        account=await asyncio.to_thread(service().account,target)
        caps=await asyncio.to_thread(service().permissions,uid);buttons=[]
        for action,label,cap in [('extend','تمدید یا کاهش روز','subscriptions'),('suspend','تعلیق اشتراک','subscriptions'),('activate','آزادسازی اشتراک','subscriptions'),('wallet','اصلاح کیف پول','wallet'),('message','پیام خصوصی','messages')]:
            if cap in caps:buttons.append([B(label,callback_data=f'bill_action_{action}_{target}')])
        buttons.append([B('بازگشت',callback_data='bill_console')]);context.user_data.pop('billing_manage',None)
        await update.effective_message.reply_text(f"مشتری {target} · {account['user']['full_name']}\nموجودی {toman(account['user']['wallet_balance'])}\nاشتراک: {account['subscription']['effective_status'] if account['subscription'] else 'قدیمی'}",reply_markup=M(buttons));return
    if flow['step']=='value':
        if flow['action'] in ('extend','wallet'):
            value=int(text)
            if flow['action']=='extend' and not -3660<=value<=3660:raise ValueError('تعداد روز خارج از محدوده است')
            if flow['action']=='wallet' and (value==0 or abs(value)>10**13):raise ValueError('مبلغ خارج از محدوده است')
        else:
            if not 1<=len(text)<=3500:raise ValueError('متن پیام معتبر نیست')
            value=text
        flow['value']=value;flow['step']='reason'
        await update.effective_message.reply_text(f"مشتری {flow['target']} · مقدار: {value}\nبرای ثبت نهایی دلیل عملیات را بفرستید؛ برای لغو /cancel.");return
    if len(text)<3:raise ValueError('دلیل حداقل سه‌حرفی لازم است')
    target=flow['target'];action=flow['action'];value=flow['value']
    if action=='wallet':await asyncio.to_thread(service().adjust_wallet,uid,target,value*10,flow['key'],text)
    elif action=='message':await asyncio.to_thread(service().message,uid,target,value,flow['key'],text)
    else:await asyncio.to_thread(service().manage,uid,target,{'suspend':'suspended','activate':'active'}.get(action),value if action=='extend' else 0,None,text)
    context.user_data.pop('billing_manage',None)
    await update.effective_message.reply_text('عملیات ثبت شد.',reply_markup=M([[B('مدیریت مرکزی',callback_data='bill_console')]]))

async def cancel(update,context):
    cancelled=False
    for key in ('billing_evidence','billing_decision','billing_manage'):
        if context.user_data.pop(key,None) is not None:cancelled=True
    if cancelled:
        await update.effective_message.reply_text('عملیات لغو شد.');raise ApplicationHandlerStop

def register(app):
    app.add_handler(CommandHandler('subscription',home),group=-10)
    app.add_handler(CallbackQueryHandler(callback,pattern='^(?:bill_|wallet_menu$|buy_plan_|pay_method_)'),group=-10)
    app.add_handler(CommandHandler('cancel',cancel),group=-10)
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND)|filters.PHOTO,receipt),group=-10)
