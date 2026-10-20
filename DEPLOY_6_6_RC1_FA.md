# راهنمای آزمون و ارتقای ایمن SONAR RADAR Pro 6.6.0-rc1

**مهم:** فقط روی VPS آزمایشی یا clone از پایگاه‌داده موجود آغاز کنید. فایل ZIP محلی، بدون انتشار روی مخزن GitHub، با `git clone` دریافت نمی‌شود.

## ۱) فایل‌ها را روی VPS آزمایشی منتقل کنید

1. ZIP را با `scp`/SFTP منتقل کنید و در دایرکتوری مجزا استخراج کنید.
2. بررسی کنید فایل‌های `install.sh`, `bot.py`, `requirements.txt`, `VERSION` و `MANIFEST_SHA256.txt` در ریشه پروژه باشند.
3. فایل manifest را با دستور زیر کنترل کنید:

```bash
python3 scripts/verify_manifest.py
bash install.sh --check
```

`--check` بررسی سورس/پیش‌نیاز است و جایگزین اتصال واقعی Agent، PostgreSQL یا HTTPS نیست.

## ۲) پیش از ارتقا

- از PostgreSQL خروجی `pg_dump -Fc` معتبر بگیرید و قابلیت بازیابی را با یک PostgreSQL staging امتحان کنید.
- کپی امن `/etc/sonar-bot.env`, `sonar_config.json`, `secret.key`, فایل‌های nginx و systemd و نسخه کد فعلی را ذخیره کنید.
- با دستور `sudo bash install.sh --backup` می‌توان از قابلیت بکاپ پروژه استفاده کرد؛ نتیجه را با ابزارهای مستقل نیز تأیید کنید.
- سرویس‌ها و پورت فعلی Agentها و هر تنظیم SSH را فهرست کنید.
- روی سرور آزمایشی از همان پوشه استخراج‌شده و با نسخه بررسی‌شده `sudo bash install.sh --update` اجرا کنید؛ به‌جای `--clean-install` که داده‌ها را reset می‌کند.

## ۳) دامنه و پنل

```bash
sudo bash install.sh --panel
sudo bash install.sh --wizard panel.example.com admin@example.com
sudo bash install.sh --font
sudo bash install.sh --doctor
```

نام دامنه و ایمیل را با مقادیر خودتان جایگزین کنید. منوی تعاملی گزینه **۰۴** نیز ویزارد دامنه را اجرا می‌کند. برای موفقیت ACME پورت‌های ۸۰ و ۴۴۳ ورودی و DNS عمومی معتبر لازم است.

کنترل کنید:

```bash
sudo systemctl status sonar-bot sonar-web
sudo systemd-analyze verify /etc/systemd/system/sonar-web.service
sudo grep -E '^(SONAR_WEB_ORIGIN|SONAR_WEBAPP_URL)=' /etc/sonar-bot.env
curl -fsS https://panel.example.com/healthz
curl -fsS https://panel.example.com/readyz
```

`SONAR_WEB_ORIGIN` باید فقط origin، مانند `https://panel.example.com`، بدون اسلش پایانی باشد. `SONAR_WEBAPP_URL` می‌تواند `https://panel.example.com/` باشد. کد ورود فقط در پیام خصوصی تلگرام دریافت شود؛ آن را به پشتیبانی/لاگ نفرستید. پس از بازنشانی، ورود با کد قدیمی نباید ممکن باشد.

## ۴) سناریوهای پذیرش اجباری

1. **بدون Agent:** لینک اشتراک ثبت کنید؛ پیام «اشتراک ذخیره شد» و وضعیت `waiting_agent` نمایش داده شود؛ کانفیگ‌های قبلی نباید حذف شوند.
2. **Agent ایران:** دو سرور آزمایشی ایران با مجوز مالک و WSS/token معتبر بسازید؛ نصب/ترمیم، پورت آزاد، پاسخ `get_stats`/`xray_ready` و امتیازدهی چندنقطه‌ای را بررسی کنید.
3. **تداخل پورت:** یک پورت اشغال‌شده انتخاب کنید و مطمئن شوید برنامه سرویس ناشناس را متوقف نکرده و پورت ذخیره‌شده قبلی را از بین نمی‌برد.
4. **انتخاب/آمار:** همزمان چند تست بفرستید؛ ثبت رزرو و release، ظرفیت پس از توقف worker، و رفتار عدم replay مبهم را بررسی کنید.
5. **وب:** ورود معتبر/نامعتبر، ۱۰ تلاش در ۱۵ دقیقه، انقضای نشست، CSRF، خروج از همه دستگاه‌ها و مجوز دسترسی کاربر به داده‌های خودش را تست کنید.
6. **گزارش PDF:** فایل فارسی را در مرورگر موبایل و رایانه باز کنید؛ راست‌چین، فونت، جدول، اعداد، RTL/LTR و مقدار N/A را بررسی کنید.
7. **هشدار:** شرایط قطعی، ساعات سکوت، ارسال ناموفق تلگرام، تلاش مجدد پس از بازیابی، بازگشت از خاموشی ربات و ضدتکرار را آزمایش کنید.
8. **ارتقا:** ابتدا backup/restore را روی staging تست و بررسی کنید هیچ سرور، کاربر، اشتراک یا Agent قبلی حذف نشده است.

## ۵) محدودیت بازگشت

اگر آزمون staging ناموفق بود، ترافیک را به نسخه قبلی برگردانید و کد/تنظیمات قبلی را بازیابی کنید. داده‌های PostgreSQL بعد از migration جدید بهتر است با snapshot پیش از ارتقا بازگردانده شوند؛ صرفاً جایگزینی فایل‌های اجرایی معادل rollback تضمین‌شده دیتابیس نیست. تغییرات certificate authority و بسته‌های نصب‌شده توسط certbot خارج از rollback فایل‌های nginx/env هستند.

## ۶) عیب‌یابی

```bash
sudo journalctl -u sonar-bot -n 150 --no-pager
sudo journalctl -u sonar-web -n 150 --no-pager
sudo nginx -t
sudo bash install.sh --support
```

فایل‌های لاگ/گزارش ممکن است آدرس سرور یا اطلاعات حساس داشته باشند؛ قبل از اشتراک‌گذاری بازبینی و پاک‌سازی شوند. تا زمان پاس شدن همه سناریوها، ۶.۶ را به‌عنوان RC آزمایشی نگه دارید.
