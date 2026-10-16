# راهنمای نصب، ارتقا و بازیابی V6 RC1

## نصب اولیه (Ubuntu/Debian)

```bash
git clone https://github.com/Amirtn9/radarsonarplus.git
cd radarsonarplus
bash install.sh --check
sudo bash install.sh
```

گزینه `01` نصب کامل، `04` تنظیم دامنه و HTTPS، `10` تست سلامت است. دریافت کد از GitHub فقط وقتی این نسخه واقعاً در مخزن منتشر شده باشد ممکن است. برای نسخه داخل ZIP، دستورهای فوق را از پوشه استخراج‌شده اجرا کنید.

## ارتقا بدون حذف داده

1. از دیتابیس و کلید رمزنگاری مستقل بکاپ بگیرید.
2. روی سرور آزمایشی ارتقا و مهاجرت را بررسی کنید.
3. نسخه جدید را در پوشه‌ای جداگانه قرار دهید.
4. اجرا کنید:

```bash
sudo bash install.sh --backup
sudo bash install.sh --update
sudo bash install.sh --doctor
sudo bash scripts/staging_smoke.sh
```

ارتقا قرار نیست خودکار دیتابیس را پاک کند. برگشت خودکار کد به نسخه قدیمی **برگشت Migration دیتابیس را تضمین نمی‌کند**. در شکست باید نسخه سازگار بکاپ و کلید بازیابی شود.

## نصب پاک

```bash
sudo bash install.sh --clean-install
```

**هشدار:** نصب پاک در ادامه از شما تأیید صریح `DELETE SONAR DATA` می‌خواهد، ابتدا بسته آرشیو و بکاپ ایجاد می‌کند و دیتابیس اختصاصی Sonar را پاک می‌کند. این فرمان مناسب ارتقای معمولی نیست.

## خطای SSH/WSS یا اجرای ناقص

```bash
sudo bash install.sh --status
sudo bash install.sh --doctor
sudo bash install.sh --support
sudo bash install.sh --logs errors --lines 200
```

برای اتصال ایجنت، پورت WSS پیش‌فرض 8080 مستقل از SSH است. ابتدا اثرانگشت SSH را خارج از بستر ربات تأیید کنید. TLS و توکن را غیرفعال نکنید. گزارش ZIP را قبل از ارسال برای بررسی، بازبینی و از وجود رمزهای احتمالی پاک کنید.

## بازیابی دیتابیس

```bash
sudo bash install.sh --restore /var/backups/sonar-radar/sonar-YYYYMMDDTHHMMSS.dump
```

بکاپ باید شامل `*.dump`، فایل تنظیمات، کلید `secret.key` و نسخه سازگار کد باشد. ابزار Restore فعلی برای دیتابیس عمل می‌کند؛ تطبیق دستی نسخه تنظیمات و کلید در برگشت کامل ممکن است ضروری باشد.
