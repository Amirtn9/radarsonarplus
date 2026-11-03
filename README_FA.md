# تعمیر ناسازگاری Manifest در RadarSonarPlus 6.9

این بسته برای خطایی است که در آن Preflight گزارش می‌کند:

- `missing=['.gitattributes', '.gitignore']`
- `new=['APPLY_PATCH_FA.txt']`
- `altered=[]`

اسکریپت دو فایل dotfile را با نسخه‌ای که SHA-256 آن با Manifest همین Release تطبیق دارد بازمی‌گرداند. فایل `APPLY_PATCH_FA.txt` را حذف دائمی نمی‌کند؛ ابتدا آن را در پوشه والد سورس با پسوند `.saved.<timestamp>` نگه می‌دارد و سپس از ریشه سورس خارج می‌کند. در پایان Manifest را دوباره بررسی می‌کند.

## استفاده

```bash
unzip radarsonarplus-manifest-repair.zip -d /root/sonar-manifest-repair
sudo bash /root/sonar-manifest-repair/repair_manifest.sh /root/radarsonarplus
```

به‌جای `/root/radarsonarplus` مسیر واقعی checkout را وارد کنید.

بعد از موفقیت:

```bash
cd /root/radarsonarplus
sudo bash install.sh --check
sudo bash install.sh
```

برای اینکه Bootstrapهای بعدی GitHub هم درست کار کنند، تغییرات Git را در یک کامپیوتر/محیط دارای مجوز Push به شاخه `main` ارسال کنید. تا قبل از انتشار اصلاح در GitHub، اجرای `wget install.sh` یا Bootstrap که از GitHub clone می‌کند، نسخه خراب را دوباره دریافت خواهد کرد.

این بسته دیتابیس، سرویس‌ها یا اطلاعات نصب‌شده روی سرور را تغییر نمی‌دهد.
