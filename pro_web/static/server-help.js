'use strict';
(() => {
 const text={
  'آدرس سرور':'نشانی IP یا نام میزبان ذخیره‌شده برای اتصال به سرور.',
  'پورت SSH':'پورت ورود امن برای نصب، تعمیر و ترمینال سرور؛ معمولاً ۲۲ است.',
  'پورت Agent':'پورت ارتباط رمزنگاری‌شده با Agent برای دریافت آمار و تست کانفیگ.',
  'وضعیت سرور':'نتیجه آخرین بررسی اتصال سرور؛ Online همراه با نمونه تازه می‌تواند LIVE باشد.',
  'وضعیت Agent':'نتیجه آخرین ارتباط با سرویس پایش نصب‌شده روی سرور.',
  'ثبت TLS':'نشان می‌دهد گواهی هویت Agent ثبت و تأیید شده است یا نیاز به تعمیر دارد.',
  'CPU':'درصد استفاده از مجموع ظرفیت پردازنده؛ ۱۰۰٪ یعنی ظرفیت کامل درگیر است.',
  'RAM':'درصد حافظه مصرف‌شده طبق سنجش Agent؛ با مقدار آزاد حافظه تفاوت دارد.',
  'Disk':'درصد فضای مصرف‌شده دیسک؛ نزدیک شدن به ۱۰۰٪ می‌تواند سرویس‌ها را متوقف کند.',
  'Hard / Disk':'فضای مصرف‌شده دیسک؛ مقدار ثبت‌شده است و پیش‌بینی نیست.',
  'Swap':'حافظه کمکی روی دیسک؛ در Agent فعلی سنجیده نمی‌شود و خط تیره به معنی صفر نیست.',
  'آخرین نمونه':'تاریخ و ساعت آخرین سنجش سرور در منطقه زمانی دستگاه شما.',
  'آخرین اتصال Agent':'زمان آخرین پیام اتصال ثبت‌شده؛ با زمان آخرین سنجش منابع ممکن است متفاوت باشد.',
  'محل سرور':'دسته‌بندی ایران یا خارج برای انتخاب مسیر مناسب پایش.',
  'گروه':'پوشه سازمان‌دهی سرور؛ جابه‌جایی گروه تنظیمات شبکه را عوض نمی‌کند.',
  'داشبورد لحظه‌ای سرور':'آخرین مقادیر واقعی هر ۱۵ ثانیه بررسی می‌شوند؛ داده قدیمی با LIVE نمایش داده نمی‌شود.',
  'مدیریت عملیاتی سرور':'عملیات واقعی روی سرور؛ پیش از تغییرات حساس، جزئیات و تأیید همان عملیات نمایش داده می‌شود.',
  'بررسی دسترسی از داخل و خارج':'مقایسه اتصال TCP از مسیرهای موجود برای کمک به تشخیص مشکل دسترسی.',
  'روند مصرف منابع':'نمودار زمان‌دار CPU، RAM و دیسک؛ لمس نمودار مقدار و ساعت نمونه را نشان می‌دهد. با دکمه‌های زیر نمودار هر سری را پنهان کنید.'
 };
 const actions={
  'web-ssh-check':'اتصال SSH مسیر مرکزی و مسیر Agent را آزمایش می‌کند؛ تنظیمات را تغییر نمی‌دهد.',
  'web-existing-ssh-probe':'کلید عمومی SSH این سرور را می‌خواند؛ پیش از ثبت، اثر انگشت را با کنسول سرور تطبیق دهید.',
  'web-agent-install':'Agent و گواهی TLS را با SSH معتبر تعمیر می‌کند و ارتباط WSS را دوباره می‌آزماید.',
  'web-ssh-terminal':'ترمینال تعاملی با حساب SSH ذخیره‌شده؛ دستورها واقعاً روی سرور اجرا می‌شوند.',
  'web-agent-port-update':'پورت ارتباط Agent را تغییر می‌دهد؛ ممکن است نیاز به باز کردن همان پورت در فایروال باشد.',
  'web-server-rename':'نام نمایشی سرور را تغییر می‌دهد؛ نشانی و اطلاعات ورود ثابت می‌مانند.',
  'web-server-group':'سرور را برای نظم بهتر به پوشه انتخاب‌شده منتقل می‌کند.',
  'web-server-delete':'رکورد سرور را پس از تأیید حذف می‌کند؛ قبل از انجام، پیام تأیید را بخوانید.',
  'reachability-run':'از مسیرهای موجود به پورت‌های سرور اتصال TCP می‌زند؛ این تست تأیید سلامت کل اینترنت نیست.',
  'detail-hours':'بازه ۱، ۳، ۱۲، ۲۴ یا ۴۸ ساعت از نمونه‌های واقعی؛ فاصله خالی به معنی نبود نمونه معتبر است.'
 };
 function add(host,description){if(!host||host.querySelector('.field-help'))return;const detail=document.createElement('details');detail.className='field-help';const title=document.createElement('summary');title.textContent='؟';title.setAttribute('aria-label','راهنمای '+host.textContent.trim());const body=document.createElement('p');body.textContent=description;detail.append(title,body);host.append(detail);}
 for(const [id,description] of Object.entries(actions)){
  const control=document.getElementById(id);if(!control)continue;
  if(control.tagName==='SELECT'){add(control.closest('label'),description);continue;}
  const wrap=document.createElement('span');wrap.className='server-action';control.before(wrap);wrap.append(control);add(wrap,description);
 }
 const seen=new WeakSet();
 function explain(){document.querySelectorAll('#server-detail .console-detail-cell>span,#server-detail .dial-title,#server-detail h3').forEach(host=>{
  if(seen.has(host))return;seen.add(host);const label=host.textContent.trim().replace(/^[^\p{L}\p{N}]+/u,'');if(text[label])add(host,text[label]);
 });}
 explain();new MutationObserver(explain).observe(document.getElementById('server-detail'),{childList:true,subtree:true});
 document.addEventListener('click',event=>{document.querySelectorAll('.field-help[open]').forEach(detail=>{if(!detail.contains(event.target))detail.open=false;});});
 document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelectorAll('.field-help[open]').forEach(detail=>{detail.open=false;});});
})();
