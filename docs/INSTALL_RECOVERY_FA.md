# راهنمای نصب و بازیابی RadarSonarPlus 6.9

راهنمای کامل [نصب و ارتقا](operations/UPGRADE_NOTES_FA.md) و دستورات [README](../README.md) را ببینید.

## نصب یا ادامه نصب ناموفق

بعد از دریافت بسته اصلاح‌شده، آن را در مسیری جدا از `/opt/radar-sonar` استخراج کنید و از همان پوشه اجرا کنید:

```bash
cd ~/radarsonarplus
sudo bash install.sh --check
sudo bash install.sh --install
```

اگر نصب قبلی کد برنامه را Rollback کرده ولی دیتابیس و فایل‌های Runtime باقی مانده‌اند، نصب‌کننده وجود آن‌ها را تشخیص می‌دهد. گزینه **1 (Resume / Preserve existing database and runtime data)** را انتخاب کنید. گزینه Clean Reset یا `--clean-install` را انتخاب نکنید؛ آن گزینه برای حذف آگاهانه دیتابیس Sonar است.

اگر برنامه کامل در `/opt/radar-sonar` وجود دارد، گزینه 1 به‌روزرسانی امن با بکاپ را انجام می‌دهد. نصب‌کننده قبل از فعال‌سازی سرویس، اجرای Python با کاربر `sonar-bot`، مجوز فایل‌ها، Import واقعی `bot.py` و صحت فایل systemd را بررسی می‌کند. پس از آن تا ۱۲۰ ثانیه منتظر تکمیل Migrationها و نشانگر آمادگی ربات می‌ماند.

## خطای systemd با کد 203/EXEC یا Permission denied

این خطا یعنی systemd نتوانسته مفسر Python را اجرا کند؛ الزاماً خطای داخل کد ربات نیست. نسخه اصلاح‌شده:

- محیط مجازی را با مجوزهای قابل‌دسترسی برای کاربر سرویس می‌سازد، درحالی‌که فایل کلید رمزنگاری و تنظیمات محرمانه را `0600` نگه می‌دارد.
- قبل از اجرا، مجوزهای مسیر Python و گزینه mount از جمله `noexec` را بررسی می‌کند.
- در واحد `sonar-bot.service` از `ExecStartPre` برای کنترل دسترسی مفسر و سورس استفاده می‌کند.
- `StartLimitIntervalSec` و `StartLimitBurst` را در بخش `[Unit]` قرار می‌دهد.
- هنگام شکست، مسیرهای فایل، symlink، مجوزها، mount options و مشخصات سرویس را چاپ می‌کند.

## بررسی پس از نصب ناموفق

این دستورات فقط وضعیت را می‌خوانند و دیتابیس را پاک نمی‌کنند:

```bash
sudo systemctl status sonar-bot --no-pager -l
sudo journalctl -u sonar-bot -b -n 200 --no-pager -o short-precise
sudo namei -l /opt/radar-sonar/venv/bin/python
sudo readlink -f /opt/radar-sonar/venv/bin/python
sudo findmnt -no TARGET,OPTIONS -T /opt/radar-sonar/venv/bin/python
sudo bash /opt/radar-sonar/install.sh --doctor
```

برای دیدن مرحله Startup که شکست خورده است:

```bash
sudo journalctl -u sonar-bot -b --no-pager | grep -E 'STARTUP_STAGE_|PostgreSQL|BOT_PROCESS_EXITED|BOT_STARTUP_READY'
```

## Backup و دیتابیس

پیام نصب‌کننده درباره Rollback دیتابیس مهم است: کد و سرویس ممکن است به نسخه قبلی برگردند، اما Migrationهای PostgreSQL خودکار معکوس نمی‌شوند. قبل از Upgrade، نصب‌کننده بکاپ معتبر می‌گیرد؛ برای نصب ناموفق اول، دیتابیس را حفظ کنید و نصب را با گزینه Resume ادامه دهید. قبل از هر عملیات بازیابی دستی، بکاپ و وضعیت Migrationها را بررسی کنید.

## حریم خصوصی لاگ‌ها

- Journal و لاگ نصب ممکن است IP، شناسه کاربر یا جزئیات محیط را نشان دهند. پیش از ارسال، آن‌ها را بازبینی کنید.
- توکن تلگرام، رمز دیتابیس، کلید رمزنگاری یا فایل `sonar_config.json` را ارسال نکنید.
