# RadarSonarPlus 7.0-dev — توسعه مرحله‌ای

این خروجی یک checkpoint توسعه است و **نسخه نهایی 7.0 یا Production Ready نیست**.

## موارد پیاده‌سازی‌شده در این checkpoint

- سرویس سازمانی افزایشی در `enterprise_services.py` با مهاجرت دیتابیس additive.
- API احراز هویت‌شده در `/api/enterprise` برای سلامت، عملیات، گردش‌کار رخداد، نقش تیم، برنامه Backup، قوانین Automation و صدور توکن.
- Health summary با تفکیک `healthy / warning / critical / unknown` و حذف امتیاز سلامت برای داده کهنه.
- Operations Center پایه با UUID، idempotency key، اقلام هدف و وضعیت پایدار در PostgreSQL.
- Incident workflow شامل acknowledged، investigating و resolved، یادداشت و تخصیص عضو تیم.
- نقش‌های `manager / operator / viewer / auditor` در جدول تیم و تفکیک مجوز عملیات عمومی از عملیات مجاز Operator.
- ثبت برنامه Backup با cadence و retention و رمزنگاری metadata.
- Rule Engine محدود به allowlist؛ اجرای فرمان دلخواه یا SSH خودکار از این مسیر ممکن نیست.
- صدور API token با نمایش secret فقط هنگام ایجاد و نگهداری hash در دیتابیس.
- نمای اولیه مرکز سازمانی در پنل وب برای سلامت و ثبت عملیات.

## مواردی که عمداً هنوز کامل/فعال نشده‌اند

- Worker اجرای عملیات گروهی، اتصال واقعی به عملیات SSH موجود، cancellation و retry policy.
- اجرای زمان‌بندی‌شده Backup، انتقال مقصد خارجی، رمزنگاری فایل واقعی و restore drill.
- Worker اجرای Auto Recovery و صحت‌سنجی پس از اقدام.
- Middleware احراز هویت API token، scope enforcement و rate limit.
- CRUD و delivery worker برای Webhook، امضای HMAC و retry/dead-letter.
- انتشار تدریجی Agent و rollback خودکار نسخه Agent.
- گزارش‌گیری/تحلیل تاریخی کامل و نمودارهای health/network.
- تکمیل رابط تلگرام و Mini App برای همه جریان‌های جدید.
- تست یکپارچه PostgreSQL واقعی، TLS/WSS، SSH و staging.

## آزمون‌های اجراشده در محیط توسعه

- `python -m pytest -q`: 256 passed, 6 skipped, 8 subtests passed.
- Python compile check برای ماژول‌های جدید و نقاط اتصال: موفق.
- JavaScript syntax check: موفق.
- `scripts/verify_manifest.py`: موفق.

شش تست Skip به وابستگی‌های محیطی مربوط هستند. این نتایج جایگزین تست staging یا تست زیرساخت واقعی نیستند.
