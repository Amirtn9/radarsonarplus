# بسته تعمیر Manifest — RadarSonarPlus 6.9

این بسته فقط برای checkout با Revision زیر ساخته شده است:

`be30eef94b92`

## مشکل دقیق

- فایل‌های `.gitattributes` و `.gitignore` در Manifest ثبت شده‌اند اما در checkout وجود ندارند.
- فایل‌های `README_FA.md` و `repair_manifest.sh` در checkout وجود دارند اما در Manifest ثبت نشده‌اند.
- فایل‌های مشترک طبق خروجی Preflight کاربر، Hash صحیح دارند.

## روش اجرا

ابتدا یک checkout دائمی از همان Revision بسازید؛ از پوشه موقت `.sonar-bootstrap.*` استفاده نکنید:

```bash
cd /root
git clone https://github.com/Amirtn9/radarsonarplus.git radarsonarplus-repair
cd /root/radarsonarplus-repair
git checkout --detach be30eef94b92
git rev-parse --short=12 HEAD
```

خروجی دستور آخر باید دقیقاً `be30eef94b92` باشد. سپس این ZIP را روی سرور در `/root` قرار دهید و اجرا کنید:

```bash
mkdir -p /root/sonar-manifest-repair
unzip -o RadarSonarPlus-6.9-MANIFEST-REPAIR.zip -d /root/sonar-manifest-repair
sudo bash /root/sonar-manifest-repair/repair_manifest.sh /root/radarsonarplus-repair
sudo bash /root/radarsonarplus-repair/install.sh --check
```

اگر Preflight موفق بود، نصب را از همین checkout دائمی ادامه دهید. اگر نصب قبلی دیتابیس یا Runtime دارد، گزینه Resume/Preserve را انتخاب کنید؛ Clean Reset را انتخاب نکنید مگر با قصد حذف اطلاعات.

## ایمنی

- اسکریپت Revision را کنترل می‌کند و روی Revision دیگر اجرا نمی‌شود.
- قبل از تغییر، الگوی اختلاف را دقیقاً با خطای گزارش‌شده مقایسه می‌کند؛ اختلاف اضافی باعث توقف می‌شود.
- فایل‌های dotfile را فقط پس از تطبیق SHA-256 با Manifest بازمی‌گرداند.
- `MANIFEST_SHA256.txt` را بازنویسی یا کنترل Hash را غیرفعال نمی‌کند.
- دو فایل کمکی ثبت‌نشده را حذف نمی‌کند؛ آن‌ها را در پوشه قرنطینه کنار سورس نگه می‌دارد.
- دیتابیس، سرویس‌ها، تنظیمات و Runtime را تغییر نمی‌دهد.

اگر Revision یا الگوی خطا متفاوت بود، اسکریپت بدون تغییر فایل‌ها متوقف می‌شود.
