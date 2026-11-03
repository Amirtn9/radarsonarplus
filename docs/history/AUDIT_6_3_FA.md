# گزارش فنی نسخه 6.3.0 رادار سونار

تاریخ بررسی: 2026-10-19

## مقایسه دو پروژه مرجع

- Uptime Kuma (MIT): وضعیت‌های Up/Down/Pending، تاریخچه heartbeat، نمودار نوار زمانی و جلوگیری از اعلان روی هر نمونه. این مفاهیم با طراحی بومی Python و PostgreSQL پیاده‌سازی شدند.
- service-monitor-tgbot (Apache-2.0): تست‌های مستقل HTTP و TCP با timeout، threshold برای خرابی/بازیابی و رساندن فقط تغییر وضعیت به تلگرام. این منطق متناسب با JobQueue رادار سونار بازطراحی شد.
- هیچ سورس برنامه Go/JavaScript پروژه‌های مرجع داخل رادار سونار کپی نشده است. قابلیت‌های پیشرفته Uptime Kuma مثل DNS/SMTP/Docker/Push و status page عمومی در این نسخه توسعه نیافته‌اند.

## کدهای تغییر کرده

- `service_probes.py`: HTTP/TCP با resolve+pin آدرس، TLS validation، timeout، عدم redirect و مسدودسازی آدرس‌های غیرعمومی به‌صورت پیش‌فرض.
- `service_monitor.py`: جداول مانیتور/تاریخچه، اعمال دسترسی‌ها، عملیات CRUD، claim با SKIP LOCKED و state machine؛ ثبت رخداد در transaction.
- `service_jobs.py`: زمان‌بند با concurrency کنترل‌شده و اعلان قطعی و بازیابی بر اساس تنظیمات.
- `service_bot.py`: مدیریت مانیتورها با دستورات تلگرام و کنترل دسترسی موجود.
- `pro_portal.py`, `pro_store.py`: API سازگار، گزارش سلامت واقعی schema، خلاصه سرویس‌ها و نصب migration.
- `pro_web/`: نمای مانیتورینگ همراه heartbeat bar، فرم ایجاد/ویرایش، فعال/غیرفعال، حذف و تاریخچه.
- `database.py`: رفع تشخیص غلط ciphertext بر اساس پیشوند.
- `bot_logic_mod/servers.py`: تلاش برای پاک‌کردن پیام‌های حاوی پسورد تلگرام پس از ذخیره رمزنگاری‌شده.
- `ws_client.py`: جلوگیری از باقی‌ماندن sentinel بی‌مصرف هنگام dispose کانکشن WebSocket.
- `bot.py`, `pro_jobs.py`: ثبت فرمان‌ها، مهاجرت و اجرای jobهای جدید، cleanup داده‌ها.
- فایل‌های مستندات، اسکریپت نصاب و کنترل بسته به نسخه 6.3 ارتقا یافته‌اند.

## آزمون‌های اجرا شده در این محیط

- `python -m compileall -q .`: موفق؛ بررسی نحوی تمامی فایل‌های پایتون.
- `bash -n install.sh install_web_panel.sh scripts/*.sh`: موفق؛ فقط نحو Bash.
- `node --check pro_web/static/app.js`: موفق؛ فقط نحو JS.
- `bash install.sh --check`: موفق (source preflight؛ بدون نصب واقعی).
- `python -m unittest discover -s tests -p test_uptime_63_unittest.py`: ده تست موفق، از جمله HTTP محلی، TCP محلی، threshold و policy شبکه.
- `python -m pytest -q tests`: **109 passed, 4 failed, 2 errors**؛ 8 subtests passed. چهار fail و دو error با نبود کتابخانه‌های `paramiko` و `python-telegram-bot` در محیط مرتبط هستند. تست‌های دسترسی به دیتابیس واقعی و API تلگرام اجرا نشده‌اند.
- `python scripts/verify_manifest.py`: باید پیش و پس از بسته‌بندی پاس شود.

## ریسک‌ها و محدودیت‌های باقیمانده

1. ادعای تست کامل عملیاتی یا رفع همه باگ‌ها ممکن نیست: سرور Debian/Ubuntu، PostgreSQL، Telegram API، Agent SSH/WSS و reverse proxy زنده در محیط آزمون وجود ندارند.
2. تست‌های ادغام پس از نصب وابستگی‌ها در یک VPS آزمایشی الزامی است؛ از این بسته مستقیم روی سرور Production جایگزینی انجام ندهید.
3. قفل‌کردن آدرس IP هنگام اتصال، DNS rebinding را برای هر probe کاهش می‌دهد، اما جایگزین egress firewall نیست.
4. محیط `SONAR_SERVICE_ALLOW_PRIVATE=1` فقط برای ادمین آگاه و محیط قابل‌اعتماد تک‌کاربره توصیه می‌شود.
5. برنامه‌ریز در هر چرخه 20 سرویس را claim می‌کند؛ در نصب‌های بزرگ باید ظرفیت و ماندگاری دیتابیس اندازه‌گیری شود.
6. حذف پسورد در تلگرام best-effort است؛ تاریخچه/بکاپ پیام‌رسان و حقوق حذف محدودیت دارند.
7. نمودار نرخ موفقیت مبتنی بر نمونه‌های اخیر تا سقف 1500 است؛ SLA رسمی/weighted uptime محاسبه نمی‌کند.
8. APIهای Mini App همچنان به initData امضاشده و نشست کوتاه تلگرام متکی‌اند؛ در صورت منقضی‌شدن، بازکردن مجدد لازم است.

## مراحل پذیرش قبل از تولید

1. بکاپ `pg_dump -Fc` و کلید رمزگذاری را بگیرید؛ بازگردانی در VPS آزمایشی آزمایش شود.
2. همه requirements در venv واقعی نصب شود و خروجی کامل pytest بدون خطای dependency گرفته شود.
3. نصب جدید و ارتقای یک دیتابیس نسخه 6.2 شبیه‌سازی شود؛ تأیید افزوده‌شدن جدول‌های pro_services و pro_service_history.
4. به‌صورت کنترل‌شده یک سرویس HTTP و یک سرویس TCP اضافه، و دو شکست متوالی و سپس recovery شبیه‌سازی و پیام تلگرام بررسی شود.
5. ایجاد مانیتور توسط مدیر، عدم امکان تغییر توسط viewer، جداسازی tenantها، پایان نشست Mini App و مهاجرت/rollback چک شوند.
6. مانیتور به آدرس 127.0.0.1 و DNS خصوصی باید در حالت پیش‌فرض مسدود شود.
7. دو instance هم‌زمان ربات و race هنگام ویرایش مانیتور ارزیابی شوند.
8. قبل از انتشار مخزن و انتشار public یک ممیزی امنیتی مستقل انجام شود.
