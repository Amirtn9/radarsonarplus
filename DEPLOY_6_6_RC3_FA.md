# SONAR RADAR Pro 6.6.0-rc3 — راهنمای رفع LOCAL_WEB_HEALTH_FAILED

## قبل از ارتقا

روی VPS اصلی بدون بکاپ معتبر ارتقا ندهید. از PostgreSQL، `/etc/sonar-bot.env`، `/etc/nginx/sites-available/sonar-radar.conf`، لینک Nginx و `/etc/systemd/system/sonar-web.service` نسخهٔ قابل‌بازیابی تهیه کنید.

## نصب آزمایشی RC3

از ZIP استخراج‌شده در همان پوشه اجرا کنید:

```bash
python3 scripts/verify_manifest.py
bash -n install.sh
sudo bash install.sh --check
sudo bash install.sh --doctor
```

پس از بکاپ و بررسی محیط staging، کد را با روش ارتقای امن پروژه جایگزین کنید و سپس:

```bash
sudo bash install.sh --panel
sudo bash install.sh --wizard radar.pingyar.com
```

ویزارد اکنون پیش از TLS سرویس محلی را بررسی می‌کند و پس از restart برای آماده‌شدن آن صبر می‌کند. اگر همچنان شکست خورد، وضعیت و journal سرویس وب در خروجی نمایش داده می‌شوند.

## عیب‌یابی دستی در سرور

```bash
sudo systemctl status sonar-web --no-pager -l
sudo journalctl -u sonar-web -n 100 --no-pager
sudo systemctl cat sonar-web
sudo grep -E '^SONAR_WEB_PORT=' /etc/sonar-bot.env || true
sudo ss -ltnp | grep -E ':(8090|[0-9]{4,5})\\b' || true
curl -v --max-time 5 http://127.0.0.1:8090/healthz
```

اگر `ExecStart` پورت دیگری دارد، آن را با `SONAR_WEB_PORT` و `proxy_pass` مقایسه کنید. اگر سرویس `failed` است، علت اصلی را در `journalctl` پیدا کنید؛ از بازکردن عمومی پورت داخلی 8090 خودداری کنید.

## معیار موفقیت

- `sonar-web.service` فعال و پایدار باشد.
- `http://127.0.0.1:<SONAR_WEB_PORT>/healthz` خروجی `ok` بدهد.
- `https://radar.pingyar.com/healthz` پاسخ موفق بدهد.
- متغیرهای `SONAR_WEB_ORIGIN` و `SONAR_WEBAPP_URL` با دامنهٔ موردنظر هماهنگ باشند.
