# بسته تعمیر Manifest — RadarSonarPlus 6.9

این بسته فقط برای checkout با Revision زیر ساخته شده است:

`59b5f2ebd1f2`

## مشکل دقیق

- `.gitattributes` و `.gitignore` در Manifest ثبت شده‌اند اما در checkout منتشرشده وجود ندارند.
- `README_FA.md` و `repair_manifest.sh` در checkout وجود دارند اما در Manifest ثبت نشده‌اند.
- Hash دو فایل بازگردانی‌شونده دقیقاً با Manifest همین Revision تطبیق داده شده است.

## روش اجرا روی سرور

از clone فعلی استفاده کنید؛ نیازی به clone مجدد یا checkout کردن Revision قبلی نیست.

ابتدا:

```bash
cd /root/radarsonarplus
git rev-parse --short=12 HEAD
```

خروجی باید دقیقاً این باشد:

```text
59b5f2ebd1f2
```

ZIP را به `/root` منتقل کنید و سپس اجرا کنید:

```bash
mkdir -p /root/sonar-manifest-repair-59b5
unzip -o /root/RadarSonarPlus-6.9-MANIFEST-REPAIR-59B5.zip \
  -d /root/sonar-manifest-repair-59b5

sudo bash /root/sonar-manifest-repair-59b5/repair_manifest.sh \
  /root/radarsonarplus

sudo bash /root/radarsonarplus/install.sh --check
```

فقط اگر `--check` موفق شد، نصب را از همان سورس ادامه دهید:

```bash
sudo bash /root/radarsonarplus/install.sh --install
```

اگر نصب قبلی اطلاعات دیتابیس یا Runtime دارد، گزینه Resume/Preserve را انتخاب کنید؛ Clean Reset را انتخاب نکنید مگر اینکه عمداً قصد حذف داده‌ها را داشته باشید.

## رفتار ایمن اسکریپت

- Revision را بررسی می‌کند و روی نسخه دیگری اجرا نمی‌شود.
- الگوی خطا را دقیقاً بررسی می‌کند؛ هر اختلاف اضافی باعث توقف می‌شود.
- دو فایل dotfile را فقط پس از تطبیق SHA-256 با Manifest برمی‌گرداند.
- Manifest را بازنویسی نمی‌کند و کنترل یکپارچگی را غیرفعال نمی‌کند.
- دو فایل کمکی ثبت‌نشده را حذف نمی‌کند؛ آن‌ها را در پوشه قرنطینه کنار سورس نگه می‌دارد.
- دیتابیس، Runtime، سرویس‌ها و تنظیمات نصب‌شده را تغییر نمی‌دهد.
- در صورت شکست بررسی نهایی، تغییرات فایل‌ها را Rollback می‌کند.
