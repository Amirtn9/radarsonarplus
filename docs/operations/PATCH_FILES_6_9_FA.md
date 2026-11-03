# RadarSonarPlus 6.9 — فایل‌های تغییرکرده در Patch پایداری

این Patch بر اساس ZIP دریافت‌شده ساخته شده است. مسیرها از ریشه پروژه هستند.

## فایل‌های تغییرکرده

- `bot.py` — ثبت مرحله‌ای Startup، Retry اتصال PostgreSQL و خطایابی Migrationها.
- `database.py` — اتصال Lazy، Retry ساخت Pool و Schema و جلوگیری از شکست ناشی از Race در شروع PostgreSQL.
- `settings.py` — پیش‌فرض دیتابیس محلی روی `127.0.0.1`.
- `install.sh` — تنظیم Host/Port یکسان دیتابیس، مسیر خصوصی Backup، نشانگر آمادگی، انتظار تا ۶۰ ثانیه و گزارش کامل Journal.
- `ws_client.py` — اعمال Deadline روی send و recv و بستن همه اتصال‌های فعال/Idle در Shutdown.
- `enterprise_runtime.py` — مسیر Backup پیش‌فرض داخل State خصوصی سرویس.
- `enterprise_cli.py` — خواندن Environment File بدون اجرای Shell و اتصال درست CLI به تنظیمات سرویس.
- `CHANGELOG.md`, `RELEASE_6_9_FA.md`, `RELEASE_6_9_PROGRESS_FA.md`, `PATCH_6_9_COMPLETION_NOTES_FA.md`, `README.md` — مستندات و راهنمای Release به‌روزشده.
- `MANIFEST_SHA256.txt` — Manifest کامل و بازسازی‌شده.
- `tests/test_database_startup_recovery.py` — تست بازیابی Startup دیتابیس (در این محیط به علت نبود psycopg2 Skip شد).
- `tests/test_enterprise_runtime_7.py` — تست مسیر Backup و خواندن امن Environment.
- `tests/test_installer_recovery_522.py` — تست قرارداد نصب و نشانگر آمادگی.
- `tests/test_pro_features.py` — همسان‌سازی تست ترتیب Migrationها.
- `docs/STARTUP_RECOVERY_6_9_FA.md` — راهنمای تشخیص خطای نصب و Startup.
- `.gitignore` — جلوگیری از انتشار Secretها، Backupها و فایل‌های Runtime.

## فایل حذف‌شده

`RELEASE_7_0_DEV_PROGRESS_FA.md` از بسته کامل حذف شده تا مستندات نسخه 6.9 با نام‌گذاری آزمایشی 7.0 مخلوط نشوند.

## نکته اعمال

ZIP کامل، نسخه صحیح و یکپارچه پروژه است و روش پیشنهادی جایگزینی کل پروژه پس از Backup است. اگر فقط Patch را روی سورس قبلی Extract می‌کنید، فایل حذف‌شده بالا را نیز دستی حذف کنید.
