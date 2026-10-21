# نتایج تست Sonar Radar Pro V6 RC1

**زمان:** ۲۰۲۶/۱۰/۱۶ — **نوع محیط:** کانتینر آفلاین، Python 3.13، بدون Telegram Bot API، PostgreSQL فعال، SSH و WSS سرور واقعی.

وضعیت‌ها: `PASS`، `FAIL`، `NOT_TESTED` و `BLOCKED_ENV`. `BLOCKED_ENV` به‌هیچ‌وجه معادل `PASS` نیست.

| آزمون | وضعیت | یادداشت |
|---|---|---|
| بررسی نحوی تمام فایل‌های Python | PASS | `python -m compileall -q .` |
| بررسی نحو `install.sh` و `scripts/staging_smoke.sh` | PASS | `bash -n` |
| بررسی نحو JavaScript پنل وب | PASS | `node --check pro_web/static/app.js` |
| پیش‌بررسی نصاب بدون نصب | PASS | `bash install.sh --check`؛ ۵۴ فایل پایتون و پیکربندی |
| تست‌های انتخابی سینک/امنیت/پارسر/گیت‌هاب | PASS | ۳۰ تست موفق در اجرای متمرکز |
| مجموعه کامل pytest | BLOCKED_ENV | ۸۹ PASS، ۴ FAIL، ۲ ERROR؛ کتابخانه‌های `paramiko` و `telegram` نصب نبودند |
| تست WebSocket محلی با توکن غیر ASCII | PASS | صرفاً شبیه‌سازی محلی، بدون TLS/WSS سرور واقعی |
| تست Parser با نمونه Clash/Sing-box/Reality/SS/Trojan | PASS | فقط نمونه‌های تعریف‌شده؛ سازگاری عمومی با تمام کلاینت‌ها تأیید نشده |
| تست SSRF و IPv4 fallback با Mock | PASS | تست بدون شبکه واقعی |
| نصب/ارتقا/حذف با داده واقعی Ubuntu/Debian | NOT_TESTED | VPS آزمایشی فراهم نبود |
| SSH و ثبت کلید میزبان، SFTP واقعی | NOT_TESTED | سرور واقعی در دسترس نبود |
| WSS/TLS و Agent واقعی + نصب Xray | NOT_TESTED | نیازمند VPS و شبکه |
| اتصال واقعی Telegram و زمان پاسخ همزمان | NOT_TESTED | به توکن و اتصال Bot API نیاز است |
| PostgreSQL migration و restore واقعی | NOT_TESTED | سرور Postgres آزمایشی در دسترس نبود |
| پنل وب تحت HTTPS/Nginx/Mini App واقعی | NOT_TESTED | به دامنه، گواهی و Telegram نیاز دارد |
| بنچمارک فشار و عدم نشت اتصال | NOT_TESTED | محیط staging و ابزار بار لازم است |
| lint/security CI پس از نصب تمامی وابستگی‌ها | NOT_TESTED | باید در GitHub Actions اجرا شود |

**تفسیر:** شکست‌های pytest در این کانتینر عمدتاً ناشی از نبود بسته‌های اجباری آزمایش هستند. با این‌حال **هیچ تضمینی وجود ندارد** که پس از نصب آن‌ها شکست دیگری رخ ندهد؛ نتیجه همه تست‌ها در CI باید دیده شود. زمان اجرا و اعداد جزئی را برای محیط staging مجدداً ثبت کنید.

## مراحل سریع بررسی پیش از استفاده

```bash
python3 scripts/verify_manifest.py
bash install.sh --check
python3 -m pytest -q tests
sudo bash install.sh --doctor
sudo bash install.sh --support
sudo bash scripts/staging_smoke.sh
```

دستورات doctor و staging فقط روی **VPS آزمایشی با سرویس‌ها** معنا دارند. بکاپ ZIP و آرشیو گزارش، بدون رمزها و اطلاعات محرمانه از نظر انسانی بازبینی شود.

**نتیجه انتشار:** `Release Candidate 1`؛ نه Stable و نه Production Ready.
