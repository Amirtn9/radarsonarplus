# راهنمای رفع خطای Startup در RadarSonarPlus 6.9

## علامت خطا

اگر نصب‌کننده پس از ساخت محیط مجازی و تنظیم PostgreSQL این پیام را نشان داد:

`Bot process exited after startup`

این پیام به‌تنهایی علت را مشخص نمی‌کند. نسخه 6.9 اکنون برای هر مرحله Migration رویدادهای `STARTUP_STAGE_BEGIN`، `STARTUP_STAGE_OK` و `STARTUP_STAGE_FAILED` را ثبت می‌کند.

## اصلاحات این بسته

- اتصال PostgreSQL در `Database()` دیگر هنگام import ماژول‌های Bot انجام نمی‌شود؛ اتصال و Migration هسته در اولین Query واقعی و با Retry انجام می‌شود.
- اگر PostgreSQL لحظه‌ای دیر آماده شود، ربات پیش از Migrationهای وابسته تا ۶ نوبت اتصال را بررسی می‌کند.
- آدرس دیتابیس نصب محلی به `127.0.0.1:5432` تثبیت شده تا اختلاف IPv4/IPv6 بین تست نصب‌کننده و تنظیمات Python رخ ندهد.
- خطای Migration دیگر با `except` عمومی بلعیده نمی‌شود؛ مرحله و Traceback واقعی ثبت می‌شود.
- نصب‌کننده تا ۱۲۰ ثانیه منتظر نشانگر آمادگی پس از Migrationها می‌ماند و در صورت شکست، وضعیت systemd، ۶۰ خط Journal، انتهای لاگ نصب و جزئیات مجوز و mount مسیر Python را نمایش می‌دهد.
- مسیر Backup سازمانی در دایرکتوری خصوصی و قابل‌نوشتن سرویس قرار می‌گیرد: `/var/lib/sonar-radar/backups`.

## بررسی پس از نصب ناموفق

این دستورات فقط وضعیت را می‌خوانند و دیتابیس را پاک نمی‌کنند:

```bash
sudo systemctl status sonar-bot --no-pager -l
sudo journalctl -u sonar-bot -b -n 200 --no-pager -o short-precise
sudo tail -n 100 /var/log/sonar/sonar_errors.json
sudo bash /opt/radar-sonar/install.sh --doctor
```

برای مشاهده مرحله‌ای که شکست خورده است:

```bash
sudo journalctl -u sonar-bot -b --no-pager | grep -E 'STARTUP_STAGE_|PostgreSQL|BOT_PROCESS_EXITED'
```

اگر پیام `STARTUP_STAGE_FAILED stage=...` وجود داشت، همان نام مرحله و Traceback زیر آن، علت مستقیم شکست را مشخص می‌کند.

## بازیابی بدون حذف اطلاعات

```bash
sudo systemctl restart postgresql
sudo bash /opt/radar-sonar/install.sh --doctor
sudo systemctl restart sonar-bot
```

از گزینه Clean Reset استفاده نکنید؛ این گزینه برای حذف آگاهانه داده‌های Sonar است و برای رفع خطای Startup لازم نیست.

## لاگ‌ها و حریم خصوصی

- `sonar_errors.json` و Journal می‌توانند جزئیات محیط و خطا را داشته باشند. پیش از ارسال، توکن تلگرام، رمز دیتابیس، Secretها، IPها و اطلاعات کاربران را بازبینی و حذف کنید.
- رمزها یا فایل `sonar_config.json` را ارسال نکنید.
- اگر خطای `203/EXEC` یا `Permission denied` دیده می‌شود، خروجی `namei -l /opt/radar-sonar/venv/bin/python` و `findmnt -T /opt/radar-sonar/venv/bin/python` را هم بررسی کنید. اگر ربات هنوز به نشانگر آمادگی نمی‌رسد، خروجی Journal از اولین `STARTUP_STAGE_FAILED` برای تشخیص دقیق لازم است.
