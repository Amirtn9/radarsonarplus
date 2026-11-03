# RadarSonarPlus 6.9 — گزارش Patch تکمیلی قابلیت‌های سازمانی

مبنای کار، فایل ZIP بارگذاری‌شده توسط کاربر است. ساختار و نشانی مخزن اصلی حفظ شده‌اند:
`https://github.com/Amirtn9/radarsonarplus`

## تغییرات این Patch

- کنترل عملیات گروهی از API و پنل وب: جزئیات هر سرور، Pause، Resume، Cancel، Retry.
- بازیابی آیتم‌های Worker با Lease منقضی‌شده و جلوگیری از گیرکردن عملیات پس از Crash Worker.
- Lease برای Webhook Delivery و Backup Schedule جهت جلوگیری از اجرای هم‌زمان چند Worker.
- مدیریت کامل‌تر Webhook: فهرست، فعال/غیرفعال، چرخش Secret، حذف، تاریخچه Delivery، Dead Letter و Retry دستی.
- مدیریت API Token: فهرست، لغو و انقضای قابل تنظیم هنگام ساخت.
- سیاست Health Score قابل تنظیم بر اساس وزن Agent/CPU/RAM/Disk، Threshold و زمان کهنگی داده.
- ذخیره تاریخچه ۳۰ روزه CPU/RAM/Disk/Traffic/Uptime و API دریافت Metrics.
- مدیریت Backup Schedule: فهرست، اجرا در نوبت بعدی Worker، فعال/غیرفعال، تاریخچه و حذف همراه با فایل‌های ثبت‌شده.
- Incident list/detail و تغییر وضعیت در رابط وب.
- انتشار Agent با Artifact ذخیره‌شده در PostgreSQL و امضای Ed25519؛ بررسی امضا و SHA-256 هنگام ثبت و دوباره پیش از نصب.
- پشتیبانی از Pause/Resume/Cancel و Rollback گروهی Rollout به آخرین Release امضاشده قبلی در همان کانال، در صورت موجود بودن.
- اتصال Artifact تأییدشده به مسیر نصب موجود `agent_deploy.py`، بدون اجرای Shell آزاد.
- داشبورد مدیر کل تلگرام برای مشاهده عملیات و Rollout و لغو آن‌ها.
- اصلاح هویت نسخه در عنوان پنل، User-Agentها و منوی مدیریت.
- اصلاح چرخه Startup: اتصال دیتابیس به‌صورت Lazy، Retry اتصال و Migration هسته پس از آماده‌شدن PostgreSQL.
- افزودن لاگ مرحله‌ای برای تمام Migrationهای Startup و نشانگر آمادگی پس از تکمیل آن‌ها.
- تثبیت اتصال PostgreSQL نصب محلی روی `127.0.0.1:5432` و ایجاد مسیر Backup خصوصی و قابل‌نوشتن برای سرویس.
- اصلاح WebSocket request deadline تا Timeout روی `send()` و `recv()` اعمال شود.
- تکمیل مدیریت اتصال‌های WebSocket فعال و Idle هنگام Shutdown؛ جلوگیری از نشت Connection و گیرکردن Worker.
- تکمیل بارگذاری امن Environment File در Enterprise CLI بدون اجرای Shell.
- اضافه‌کردن `.gitignore` برای جلوگیری از انتشار Credential، کلید و Backup.

## تست‌های خودکار

نتیجه آخرین اجرای تست در محیط توسعه:
- 281 passed
- 8 skipped
- 8 subtests passed
- Full suite completed in 12.48 seconds in the latest run.

تست‌های جدید شامل تأیید امضای Ed25519، رد Hash دستکاری‌شده، رد Signature نامعتبر و محاسبه Health Score با وزن قابل تنظیم هستند.

همچنین `python -m compileall -q .`، `bash -n install.sh`، `node --check pro_web/static/enterprise.js` و بررسی Manifest موفق بوده‌اند.

## پیکربندی لازم برای Agent Release امضاشده

روی Backend باید `SONAR_AGENT_RELEASE_PUBLIC_KEY` با کلید عمومی Ed25519 به‌صورت Base64 تنظیم شود. کلید خصوصی باید خارج از Backend و خارج از مخزن Git نگهداری شود. دستور تولید کلید و امضای Artifact در `docs/ENTERPRISE_6_9_RUNBOOK_FA.md` آمده است.

بدون کلید عمومی، ثبت Release امضاشده عمداً رد می‌شود.

## مواردی که همچنان تکمیل/تأیید نشده‌اند

- Multi-Workspace مستقل برای هر حساب؛ Workspace اشتراکی موجود معادل چند Workspace مستقل نیست.
- Backup/Restore منطقی مستقل هر Tenant؛ Backup کامل PostgreSQL همچنان فقط Platform-admin است.
- سیستم صورتحساب و درگاه پرداخت کامل و اعمال یکپارچه محدودیت پلن در تمام عملیات.
- Registry خارجی Artifact؛ این Patch Artifact امضاشده را در PostgreSQL ذخیره می‌کند.
- پوشش همه رویدادهای Webhook و گزارش تحلیلی کامل Dead Letter.
- SLA/MTTR/MTBF پیشرفته، نمودارهای کامل شبکه و خروجی PDF/CSV تحلیلی.
- تست واقعی PostgreSQL، SSH، TLS/WSS، S3، Telegram و نصب/ارتقا روی VPS/Staging. تست واحد Startup و WebSocket جایگزین تست زیرساخت زنده نیست.
- ممیزی امنیتی و تست بار چند Tenant در محیط عملیاتی.

این Patch کد و رابط قابلیت‌های ذکرشده را توسعه می‌دهد، اما جایگزین تست روی زیرساخت واقعی نیست. انتشار به GitHub انجام نشده است.
