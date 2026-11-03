# گزارش کنترل نسخه ۶ (Developer Preview)

- تاریخ بررسی: 2026-10-16
- `python -m compileall -q .`: موفق
- `bash -n install.sh` و اسکریپت‌های `scripts/*.sh`: موفق
- `node --check pro_web/static/app.js`: موفق
- `bash install.sh --check`: موفق در بررسی آفلاین سورس (بدون systemd واقعی)
- `python -m pytest -q tests/test_v6_release.py`: ۷ موفق
- `python -m pytest -q tests/test_v6_release.py tests/test_github_bootstrap.py tests/test_regressions.py`: ۱۸ موفق
- اجرای کل مجموعه تست‌ها در محیط فعلی: ۷۰ موفق، ۴ شکست و ۲ خطای آماده‌سازی. شکست‌ها مربوط به نبود `paramiko` یا `python-telegram-bot` در محیط اجرای تست هستند؛ نصب این کتابخانه‌ها در این محیط به‌علت محدودیت DNS انجام نشد. این موضوع **به معنی اثبات عملکرد کامل روی سرور واقعی نیست**.

## موارد تأیید نشده

Telegram API، SSH host enrollment و اتصال واقعی، WSS TLS، PostgreSQL migration، Nginx/ACME، تست همه کانفیگ‌ها و پنل روی مرورگر واقعی، تست فشار.

## نتیجه

این فایل یک **پیش‌نمایش قابل آزمایش نسخه ۶** است، نه گواهی آماده بودن نسخه Production. ابتدا روی VPS آزمایشی اجرا شود. ابزار `--support` و `/doctor` برای ارسال لاگ‌های جدید استفاده شوند.
