# انتشار Sonar Radar Pro روی GitHub

مخزن اصلی: <https://github.com/Amirtn9/radarsonarplus> · شاخه `main`

## گام‌های انتشار

1. از مخزن موجودتان بکاپ بگیرید؛ این ZIP یک Snapshot از پروژه اصلاح‌شده است و
   باید قبل از جایگزین‌کردن فایل‌ها با تغییرات هم‌زمان در GitHub تطبیق داده شود.
2. ZIP را روی سیستم خود Extract کنید. **محتویات داخل پوشهٔ Sonar_Radar_Pro_5.3.0** (به‌خصوص فایل
   `install.sh`) را به **ریشه مخزن** روی شاخه `main` منتقل کنید.
3. فایل‌های خصوصی از قبیل `.env`، `secret.key`، `sonar_config.json` و بکاپ‌ها
   نباید آپلود شوند.
4. در مخزن مطمئن شوید مسیر زیر بدون خطای 404 باز می‌شود:
   <https://raw.githubusercontent.com/Amirtn9/radarsonarplus/main/install.sh>
5. اگر workflow بخش Actions اجرا شد، نتیجهٔ تست‌ها را بررسی کنید.

## انتشار با Git CLI (در پوشه‌ای که ZIP را Extract کرده‌اید)

```bash
git clone https://github.com/Amirtn9/radarsonarplus.git radarsonarplus-publish
# محتویات ZIP را با ابزار فایل خود به radarsonarplus-publish منتقل کنید.
cd radarsonarplus-publish
python3 scripts/verify_manifest.py
bash install.sh --check
git status
git add -A
git commit -m "Fix SSH enrollment, WSS ports and agent deployment in 5.3.0"
git push origin main
```

از `git push --force` استفاده نکنید. اگر نسخهٔ GitHub بعد از ساخت بسته تغییر کرده، تغییرات
را با دقت ادغام کنید.

## نصب پس از انتشار (Ubuntu/Debian + systemd)

```bash
wget https://raw.githubusercontent.com/Amirtn9/radarsonarplus/main/install.sh
chmod +x install.sh
sudo ./install.sh
```

در منو **01** نصب کامل Bot + PostgreSQL + Mini App را اجرا می‌کند. گواهی عمومی HTTPS
باید با تنظیم DNS دامنه و گزینه **04** دریافت شود.

## ارتقا با جدیدترین نسخه GitHub

```bash
wget -O install.sh https://raw.githubusercontent.com/Amirtn9/radarsonarplus/main/install.sh
chmod +x install.sh
sudo ./install.sh --update
sudo ./install.sh --doctor
```

## مشاهده لاگ

```bash
sudo bash /opt/radar-sonar/install.sh --logs all --follow
sudo bash /opt/radar-sonar/install.sh --doctor
```
