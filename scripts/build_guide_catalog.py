#!/usr/bin/env python3
"""Generate the searchable button dictionary from the actual Telegram keyboards."""
import ast
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted([str(path.relative_to(ROOT)) for path in ROOT.glob('*.py')]
                 + [str(path.relative_to(ROOT)) for path in (ROOT/'bot_logic_mod').glob('*.py')])


def literal(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,str):return node.value
    if isinstance(node,ast.JoinedStr):return ''.join(literal(v) if isinstance(v,ast.Constant) else '…' for v in node.values)
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):return literal(node.left)+literal(node.right)
    return ''


def explanation(label, callback):
    if callback.startswith('bill_'):
        return 'بخش اشتراک و پرداخت را باز می‌کند؛ مبلغ ریالی، مقصد و شبکه رمزارز را پیش از پرداخت بررسی کنید. رسید دستی فقط پس از تأیید مسئول مالی به اعتبار تبدیل می‌شود.'
    if 'مدیریت سازمانی' in label:
        return 'مرکز عملیات گروهی، سلامت سرورها، انتشار Agent، گزارش‌ها و سیاست‌های سازمان را برای مدیر مجاز باز می‌کند.'
    if any(word in label for word in ('بازگشت','قبلی','بعدی','صفحه اصلی')):
        return 'جابه‌جایی میان صفحه‌ها و بخش‌ها؛ این دکمه به‌خودی‌خود اطلاعات منبع را تغییر نمی‌دهد.'
    if any(word in label for word in ('انصراف','لغو')):
        return 'درخواست یا مرحله انتخاب‌شده را لغو می‌کند؛ پیش از آن وضعیت عملیات اجراشده را بررسی کنید، چون کار انجام‌شده روی سرور الزاماً برنمی‌گردد.'
    if any(word in label for word in ('حذف','پاک')):
        return 'حذف مورد انتخابی را آغاز می‌کند؛ نام، مالک و محدوده حذف را در پیام تأیید بررسی کنید. در تردید انصراف بزنید.'
    if any(word in label for word in ('تأیید','تایید','ذخیره','ثبت')):
        return 'اطلاعات یا انتخاب‌های مرحله جاری را ثبت می‌کند؛ قبل از تأیید، خلاصه تغییر و منبع انتخاب‌شده را دوباره بخوانید.'
    if any(word in label for word in ('تست','سنجش','Ping','پینگ','اسکن')):
        return 'سنجش واقعی از نود مجاز را درخواست می‌کند؛ پس از پایان، زمان، نقطه سنجش و علت خطا را بخوانید. درخواست سنجش با موفقیت آن یکسان نیست.'
    if any(word in label for word in ('ایجنت','Agent')):
        return 'صفحه یا عملیات Agent انتخاب‌شده را باز می‌کند؛ نصب/به‌روزرسانی باید تا تأیید TLS، WSS و توکن دنبال شود. راهنمای Agent را پیش از اقدام بخوانید.'
    if any(word in label for word in ('روشن','خاموش','فعال','غیرفعال','⏸','✅','توقف')):
        return 'وضعیت این گزینه را کنترل می‌کند؛ علامت کنار آن وضعیت فعلی را نشان می‌دهد. فعال‌کردن یک گزینه، برنامه‌های مستقل دیگر را خاموش نمی‌کند.'
    if any(word in label for word in ('دقیقه','ساعت','بازه','فاصله','زمان‌بندی','زمان بندی')):
        return 'فاصله یا ساعت مربوط به همین برنامه را انتخاب می‌کند؛ تست، گزارش و ساعت سکوت تنظیمات جداگانه دارند. واحد نوشته‌شده روی دکمه را رعایت کنید.'
    if any(word in label for word in ('ویرایش','تغییر','تنظیم')):
        return 'تنظیمات مورد انتخاب‌شده را باز می‌کند؛ مقدار جدید را مطابق تنظیم واقعی سرویس وارد و در پایان ذخیره کنید.'
    if any(word in label for word in ('گزارش','PDF','CSV','لاگ','تاریخچه')):
        return 'اطلاعات ثبت‌شده یا فایل گزارش را نمایش می‌دهد؛ بازه و زمان داده را کنترل کنید. پیش از فرستادن فایل برای دیگران، محرمانگی محتوا را بررسی کنید.'
    if any(word in label for word in ('افزودن','جدید','ساخت')):
        return 'ویزارد ساخت مورد جدید را شروع می‌کند؛ مرحله‌ها را به ترتیب انجام دهید. ثبت اولیه لزوماً نصب یا تست موفق را تضمین نمی‌کند.'
    if any(word in label for word in ('جست','فیلتر','ستاره','علاقه')):
        return 'نمایش فهرست را محدود یا مورد را برای دسترسی سریع علامت‌گذاری می‌کند؛ برای دیدن همه موارد فیلتر را پاک کنید.'
    if any(word in label for word in ('کد','ورود','پنل')):
        return 'مسیر ورود یا پنل مربوط را باز می‌کند؛ Mini App داخل ربات خودکار و وب مستقل با کد یک‌بارمصرف وارد می‌شود. کد را به دیگری ندهید.'
    return 'بخش «'+label.strip(' …')+'» را برای مورد یا فضای کاری انتخاب‌شده باز می‌کند. ابتدا نتیجه و توضیح صفحه را بخوانید؛ تغییرهای حساس در مرحله بعد نیازمند انتخاب و تأیید هستند.'


def build():
    found = {}
    for name in SOURCES:
        class Buttons(ast.NodeVisitor):
            def __init__(self):self.function='';self.admin=name in ('admin_panel.py','bot_logic_mod/admin.py')
            def visit_FunctionDef(self,node):
                previous=self.function;old_admin=self.admin;self.function=node.name
                self.admin=self.admin or node.name.startswith(('admin_','diag_'))
                self.generic_visit(node);self.function=previous;self.admin=old_admin
            visit_AsyncFunctionDef=visit_FunctionDef
            def visit_Dict(self,node):
                for key,value in zip(node.keys,node.values):
                    old=self.admin
                    if isinstance(key,ast.Constant) and key.value=='admin':self.admin=True
                    self.visit(value)
                    self.admin=old
            def visit_Tuple(self,node):
                # Category menus use rows((label, callback), ...) rather than B().
                if name=='v6_ui.py' and len(node.elts)==2:
                    label,callback=map(literal,node.elts)
                    if label and callback and callback.replace('_','').isalnum() and ' ' not in callback:
                        replacement=ast.Call(func=ast.Name(id='B',ctx=ast.Load()),args=[ast.Constant(label)],keywords=[ast.keyword(arg='callback_data',value=ast.Constant(callback))])
                        self.visit_Call(replacement)
                self.generic_visit(node)
            def visit_If(self,node):
                old=self.admin
                test=ast.unparse(node.test)
                if any(word in test for word in ('SUPER_ADMIN_ID','admin_id','is_admin')) and 'not ' not in test:self.admin=True
                for child in node.body:self.visit(child)
                self.admin=old
                for child in node.orelse:self.visit(child)
            def visit_Call(self,node):
                fn=node.func.id if isinstance(node.func,ast.Name) else ''
                if fn in ('B','InlineKeyboardButton') and node.args:
                    label=literal(node.args[0]).strip()
                    callback=next((literal(kw.value) for kw in node.keywords if kw.arg=='callback_data'),'')
                    admin=self.admin or callback.startswith(('admin_','v6_admin','v6_diag','fleet_','enterprise_','ent_','balancer_','bill_admin','bill_review','bill_approve','bill_reject','bill_console','bill_customer','bill_action'))
                    if label.strip(' …'):
                        group=('مدیریت' if admin else 'اعلان‌ها' if name=='config_alert_bot.py' or 'cron' in self.function or 'alert' in self.function
                               else 'همکاری' if name=='workspace_ui.py' else 'سرویس‌ها' if name=='service_bot.py'
                               else 'کانفیگ‌ها' if any(w in self.function for w in ('config','tunnel','sub_'))
                               else 'سرورها' if any(w in self.function for w in ('server','srv','agent')) else 'منوها و ابزارها')
                        found[(label,admin)]={'label':label,'category':group,'description':explanation(label,callback),'admin':bool(admin)}
                self.generic_visit(node)
        Buttons().visit(ast.parse((ROOT/name).read_text(encoding='utf-8')))
    return sorted(found.values(),key=lambda row:(row['admin'],row['category'],row['label']))


if __name__=='__main__':
    entries=build()
    (ROOT/'docs'/'guide_buttons.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Generated guide dictionary for {len(entries)} button labels')
