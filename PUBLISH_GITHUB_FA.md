# انتشار V6.1 RC1 در مخزن GitHub

مخزن هدف: https://github.com/Amirtn9/radarsonarplus

این بسته به‌صورت محلی تولید شده است؛ **هیچ فایلی خودکار روی GitHub آپلود نشده است.** ابتدا تست‌ها، لاگ‌ها و فایل‌های حساس را بررسی کنید.

از داخل پوشه سورس:

```bash
python3 scripts/verify_manifest.py
bash install.sh --check
python3 -m pytest -q tests
```

سپس روی سیستم توسعه با `git`، در مخزن متعلق به خودتان یک branch جدید بسازید:

```bash
git checkout -b release/v6.1-rc1
git add -A
git status --short
git diff --cached --check
git commit -m "Sonar Radar Pro 6.1.0-rc.1: release consistency and regression checks"
git push -u origin release/v6.1-rc1
```

پس از بازبینی Pull Request و تست CI، انتشار را انجام دهید. **بدون قبولی تست‌های انتهابه‌انتها نسخه را Stable ننامید.** `MANIFEST_SHA256.txt` امضای امنیتی نیست. برای نصب از GitHub باید کد RC1 را واقعاً به مخزن منتقل کنید؛ بازکردن URL به‌تنهایی محتوای این ZIP را منتشر نمی‌کند.
