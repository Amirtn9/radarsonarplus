# راهنمای Hotfix نصب Sonar Radar Plus 6.9

این Hotfix برای خطاهای زیر اضافه شده است:

- `status=203/EXEC` و `Permission denied` هنگام اجرای `/opt/radar-sonar/venv/bin/python`
- هشدار systemd درباره `StartLimitIntervalSec` در بخش `[Service]`

## روش امن اعمال روی نصب موجود

1. فایل ZIP کامل را در VPS به مسیر موقت مانند `/root/radarsonarplus-hotfix` منتقل و استخراج کنید. آن را روی `/opt/radar-sonar` استخراج نکنید.
2. ابتدا Repair غیرمخرب را اجرا کنید:

   ```bash
   sudo bash /root/radarsonarplus-hotfix/radarsonarplus/scripts/repair.sh
   ```

3. اگر Repair موفق شد، سورس اصلاح‌شده را با حفظ دیتابیس و Runtime نصب کنید:

   ```bash
   sudo bash /root/radarsonarplus-hotfix/radarsonarplus/install.sh --update
   ```

   گزینه `--update` برای ارتقا است؛ از `--clean-install` استفاده نکنید.

4. در پایان وضعیت سرویس را بررسی کنید:

   ```bash
   sudo systemctl status sonar-bot --no-pager -l
   sudo journalctl -u sonar-bot -n 80 --no-pager
   ```

## اگر Repair خطای noexec گزارش کرد

گزینه `noexec` با chmod قابل رفع نیست. خروجی این دستورها را بررسی کنید:

```bash
readlink -f /opt/radar-sonar/venv/bin/python
findmnt -no TARGET,OPTIONS -T "$(readlink -f /opt/radar-sonar/venv/bin/python)"
namei -l /opt/radar-sonar/venv/bin/python
runuser -u sonar-bot -- /opt/radar-sonar/venv/bin/python -V
```

سیاست mount را بدون بررسی، کورکورانه به `exec` تغییر ندهید. اگر فایل‌سیستم مفسر `noexec` است، باید محل venv روی یک فایل‌سیستم مجاز به اجرا قرار بگیرد یا سیاست mount توسط مدیر سرور اصلاح شود.

Repair پیش از بازنویسی Unit، نسخه قبلی را در `/var/backups/sonar-radar/` نگه می‌دارد. این عملیات دیتابیس را حذف یا Reset نمی‌کند.
