# RadarSonarPlus 6.9 — Enterprise Operations

این نسخه بر پایه کدبیس اصلی مخزن `Amirtn9/radarsonarplus` و نسخه 6.6.0-rc3 ساخته شده است.

## قابلیت‌های سازمانی افزوده‌شده
- عملیات پایدار مبتنی بر PostgreSQL و Workerهای مستقل
- اتصال عملیات تشخیصی و مدیریت سرویس به مسیرهای SSH/Agent موجود
- Backup رمزنگاری‌شده PostgreSQL با مقصد محلی و S3-compatible
- CLI برای Backup و Restore با تأیید صریح
- API Token، Scope و محدودیت نرخ درخواست
- Webhook امضاشده HMAC-SHA256 با صف پایدار و Retry
- Auto Recovery محدود به سیاست‌های allowlist
- انتشار Agent به‌صورت Canary و Batch با کنترل SHA-256 سورس
- گزارش‌های سازمانی و پنل وب
- داشبورد مدیریتی سازمانی داخل ربات

## پایداری نصب و ارتباطات

- اتصال PostgreSQL پس از آماده‌شدن سرویس با Retry انجام می‌شود.
- Migrationهای Startup دارای نام مرحله و Traceback هستند.
- Timeout کل WebSocket روی ارسال و دریافت اعمال می‌شود.
- Shutdown تمام اتصال‌های فعال و Idle را می‌بندد.
- نصب‌کننده برای رسیدن به نشانگر آمادگی پس از Migrationها تا ۶۰ ثانیه صبر می‌کند.

## استقرار
مخزن رسمی: https://github.com/Amirtn9/radarsonarplus

نسخه 6.9 در این بسته آماده‌سازی شده است. انتشار به GitHub و تست روی زیرساخت واقعی باید جداگانه انجام شود.
