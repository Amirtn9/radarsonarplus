# RadarSonarPlus 6.9 — Patch تکمیلی

این بسته فقط فایل‌های تغییرکرده یا تازه‌اضافه‌شده را شامل می‌شود. ساختار مسیرها نسبت به ریشه پروژه حفظ شده است.

## روش اعمال

1. از پروژه فعلی و دیتابیس PostgreSQL یک Backup بگیرید.
2. ZIP را در ریشه پروژه فعلی Extract کنید و اجازه دهید فایل‌های موجود جایگزین شوند.
3. محیط مجازی پروژه را فعال و وابستگی‌ها را نصب/به‌روزرسانی کنید:
   `pip install -r requirements.txt`
4. پیش از اجرای ربات/وب، کلید عمومی Ed25519 را با نام `SONAR_AGENT_RELEASE_PUBLIC_KEY` در Environment/Secret Manager قرار دهید؛ کلید خصوصی را هرگز روی Backend یا GitHub نگذارید.
5. سرویس را Restart کنید. Migrationهای افزایشی Enterprise هنگام Startup اجرا می‌شوند.
6. ابتدا در Staging عملیات گروهی، Backup/Restore، Webhook، Token و Agent Rollout را آزمایش کنید.

## فایل‌های مهم

- `enterprise_services.py`, `enterprise_api.py`, `enterprise_runtime.py`: API، سرویس‌ها و Workerهای تکمیلی.
- `agent_deploy.py`, `core.py`, `monitor_agent.py`, `database.py`: نصب Artifact امضاشده و تاریخچه Metrics.
- `pro_portal.py`: نگه‌داشتن Actor واقعی سازنده API Token برای اعمال RBAC.
- `pro_web/index.html`, `pro_web/static/enterprise.js`: رابط مدیریت عملیات، رخداد، Health، Backup، Webhook، Token و Release.
- `admin_panel.py`, `keyboard.py`, `dispatcher.py`: کنترل عملیات و Rollout از پنل مدیر کل تلگرام.
- `tests/test_enterprise_completion_69.py`: تست‌های جدید.
- `docs/ENTERPRISE_6_9_RUNBOOK_FA.md`: تنظیم کلید و راهنمای API.
- `MANIFEST_SHA256.txt`: Manifest کامل پروژه پس از اعمال Patch.

## نکات امنیتی

- عملیات در حال اجرای SSH قابل قطع اجباری نیست؛ Cancel صف آیتم‌های بعدی را متوقف و درخواست لغو ثبت می‌کند.
- Agent Release بدون Signature معتبر Ed25519 و Hash صحیح پذیرفته نمی‌شود.
- Rollback گروهی فقط در صورت وجود Release قبلی امضاشده امکان‌پذیر است.
- Backup کامل دیتابیس همچنان به مدیر کل پلتفرم محدود است.
- این Patch روی GitHub Push نشده و تست زیرساخت واقعی جایگزین تست‌های خودکار است.

## اصلاحات پایداری Startup و WebSocket

- `Database()` هنگام import دیگر به PostgreSQL متصل نمی‌شود؛ اولین Query با Retry، Pool و Schema اصلی را آماده می‌کند.
- خطاهای Migration با نام مرحله و Traceback ثبت می‌شوند.
- Installer اتصال دیتابیس محلی را روی `127.0.0.1:5432` همسان با تست سلامت قرار می‌دهد.
- Installer تا ثبت نشانگر آمادگی پس از Migrationها منتظر می‌ماند و Journal کامل را در شکست نمایش می‌دهد.
- WebSocket برای `send` و `recv` از یک deadline کلی استفاده می‌کند.
- `close_all()` همه Connectionهای Idle و checked-out را می‌بندد.
- فایل `docs/STARTUP_RECOVERY_6_9_FA.md` راهنمای تشخیص خطای Startup است.
