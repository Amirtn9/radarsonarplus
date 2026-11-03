# اصلاحات بازبینی RadarSonarPlus 6.9

این بسته، اصلاحات بازبینی سورس و مسیر نصب را روی نسخه 6.9 اعمال می‌کند.

## اصلاحات اصلی

- داشبورد سلامت اکنون مقدار متنی `agent_last_seen` را به‌صورت امن به تاریخ تبدیل می‌کند؛ تاریخ نامعتبر یا متریک مفقود دیگر به‌عنوان سلامت کامل محاسبه نمی‌شود و وضعیت `unknown` می‌گیرد.
- رویداد `service_down` از جدول واقعی مانیتورینگ HTTP/TCP یعنی `pro_services` خوانده می‌شود. چون برای هر Probe اتصال قطعی به یک سرور مدیریتی وجود ندارد، این رویداد فعلاً فقط اعلان می‌فرستد؛ درخواست Restart بدون نگاشت صریح هم در مدل API و هم در لایه سرویس رد می‌شود و قوانین قدیمیِ Restart نیز غیرفعال می‌شوند.
- Migration مانیتورینگ HTTP/TCP اکنون در Startup ربات و پنل وب اجرا می‌شود؛ جدول‌های `pro_services` و `pro_service_history` در نصب تازه جا نمی‌مانند.
- شکست Batch در Agent Rollout، در صورت وجود Release قبلی امضاشده، Rollback خودکار را برای تمام سرورهایی که در همان Rollout با موفقیت به نسخه جدید رفته‌اند صف می‌کند؛ سرورهای موفق Batch شکست‌خورده نیز پوشش داده می‌شوند.
- Rollback در وضعیت `rollback_pending` قابل بازیابی پس از Restart پردازش است؛ تلاش مجدد دستی بعد از `rollback_failed` پشتیبانی می‌شود و تا پایان Batch فعال، Rollback دستی زودهنگام پذیرفته نمی‌شود.
- ثبت Release تکراری با همان محتوا idempotent است؛ تغییر Artifact با همان `version/channel` رد می‌شود تا تاریخچه Rollout تغییر نکند.
- عملیات دیتابیس Workerهای Webhook، Auto Recovery و Rollout از حلقه Async خارج و به Thread منتقل شده‌اند.
- مسیر Backup تکراری و غیرقابل‌فراخوانی از Worker حذف شده است؛ Backupهای زمان‌بندی‌شده از مسیر مستقل خود استفاده می‌کنند.

## اصلاحات نصب

- ساخت venv فقط با `umask 022` انجام می‌شود و سپس مجوزهای فایل‌ها/دایرکتوری‌ها صریحاً نرمال می‌شوند؛ `umask 077` سراسری برای فایل‌های محرمانه حفظ می‌شود.
- قبل از راه‌اندازی سرویس، اجرای مفسر Python و Import واقعی `bot.py` با حساب `sonar-bot` آزمایش می‌شود.
- واحد systemd دارای `ExecStartPre` برای کنترل دسترسی مفسر و سورس است؛ تنظیمات `StartLimitIntervalSec` و `StartLimitBurst` در بخش `[Unit]` قرار دارند.
- در خطای `203/EXEC`، مسیرهای والد با `namei`، مجوزها، مقصد symlink، mount optionهای `noexec` و مشخصات اجرای systemd چاپ می‌شوند.
- نصب‌کننده به‌جای اتکا به `systemctl is-active`، تا 120 ثانیه منتظر Marker واقعی آمادگی ربات می‌ماند؛ این Marker پس از اعتبارسنجی Telegram و تکمیل Migrationها نوشته می‌شود.
- در صورت شکست شروع، وضعیت سرویس، Journal و انتهای لاگ نصب چاپ می‌شود.
- اگر نصب اولیه شکست بخورد و کد برنامه Rollback شود، اجرای دوباره نصب می‌تواند دیتابیس، فایل محیطی و کلید رمزنگاری باقی‌مانده را بدون Clean Install و بدون پاک‌کردن داده‌ها استفاده کند.

## اعتبارسنجی این بسته

- `bash install.sh --check`: موفق
- Python compileall: موفق
- Bash syntax checks: موفق
- JavaScript syntax check (`node --check`): موفق
- بررسی نمونه واحد systemd با `systemd-analyze verify`: موفق
- Pytest: **311 passed, 8 skipped, 8 subtests passed**
- 8 تست Skip شده به‌دلیل نصب نبودن `psycopg2`، `paramiko` و `python-telegram-bot` در محیط ممیزی هستند؛ این وابستگی‌ها در فایل‌های requirements پروژه تعریف شده‌اند.

تست زنده Telegram API، PostgreSQL واقعی، SSH و Agent روی VPS انجام نشده است. اتصال زنده به Telegram، PostgreSQL، SSH و Agent روی VPS انجام نشده؛ خطای `203/EXEC` در این بسته با بررسی مجوزها، مسیر اجرا و mount options پوشش داده شده است، اما تأیید نهایی باید روی همان VPS انجام شود.
