# Sonar Radar Pro 6.3.1 — اصلاح ناوبری و آماده‌سازی انتشار آزمایشی

**وضعیت: Release Candidate.** آزمون واقعی روی VPS اوبونتو، Telegram Bot API، PostgreSQL و Agent/WebSocket هنوز در این محیط قابل اجرا نبوده است؛ بنابراین هیچ ادعای تضمینی مبنی بر «بدون باگ بودن تمام قابلیت‌ها» یا استقرار موفق در سرور واقعی مطرح نمی‌شود.

## تغییرات اجراشده

- افزوده‌شدن مدیریت مسیر **به‌ازای هر کاربر** (`navigation.py`). هرکدام از دسته‌بندی‌های «سرورها»، «کانفیگ‌ها»، «مانیتورینگ»، «ابزارها»، «حساب» و «مدیریت» مسیر بازگشت مخصوص خود را دارند. در صورت نداشتن سابقهٔ مسیر، بازگشت ایمن به منوی اصلی انجام می‌شود. دستهٔ ادمین فقط با بررسی شناسهٔ مدیر قابل دسترسی است.
- اتصال دکمه‌های بازگشت صفحات ریشه و پروفایل/کیف پول/ابزارها/تنظیمات/مرکز Pro به `v6_return` به‌جای پرتاب ناخواسته به خانه.
- حفظ نوع فهرست کانفیگ، شماره صفحه و حالت قبلی برای بازگشت از تست و حذف؛ بازگشت از مدیریت یک اشتراک به فهرست اشتراک‌ها و استفاده از `parent_sub_id` برای تشخیص والد کانفیگ.
- حذف فراخوانی مجدد `/start` و پاک‌شدن وضعیت مسیر هنگام **لغو عملیات (همراه با پاک‌سازی اطلاعات موقت حساس)، افزودن گروه و افزودن کانفیگ**. بعد از تکمیل مسیرهای مهم، دکمه‌ی ادامه/بازگشت نمایش داده می‌شود.
- فارسی‌سازی عنوان‌های مدیریت گروه‌ها، اعتبارسنجی اولیه طول نام گروه و حذف بلوک `except` تکراری در نمایش فهرست کانفیگ.
- یکسان‌سازی شماره نسخه نمایش‌داده‌شده در صفحه اصلی و تنظیمات با 6.3.1؛ اصلاح نام پکیج تست اختیاری `httpx2` به `httpx`.
- به‌روزرسانی manifest یکپارچگی SHA-256؛ پاک‌سازی فایل‌های کش و کلیدهای ایجادشده در محیط آزمایش از بسته انتشار.

## فهرست فایل‌هایی که تغییر داده **نشده‌اند**

`monitor_agent.py`, `ws_client.py`, `agent_deploy.py`, `agent_runtime.lock.json`, `sonar_config.example.json`, `core.py`, `tunnel_logic.py`, `runtime.py`, `service_monitor.py`.

در `settings.py` صرفاً مقدار `PROJECT_VERSION` به‌روز شده است؛ **هیچ متغیر اتصال، پورت، pool، timeout، ping یا گزینه مربوط به Agent/WebSocket تغییر نکرده است.** با این حال اعتبارسنجی رفتاری در شبکه واقعی همچنان لازم است.

## نصب آزمایشی روی Ubuntu VPS (Ubuntu 22.04 / 24.04)

ابتدا ZIP را در VPS آپلود و استخراج کنید؛ دستورات زیر را از **داخل پوشهٔ استخراج‌شدهٔ سورس** اجرا کنید. از نصب با `wget install.sh` قبل از انتشار همین نسخه در GitHub اجتناب کنید؛ آن روش کد متفاوتی را می‌گیرد.

```bash
sudo apt-get update
sudo apt-get install -y unzip python3 python3-venv
unzip Sonar_Radar_Pro_v6.3.1.zip
cd Sonar_Radar_Pro_v6.3.1
python3 scripts/verify_manifest.py
bash install.sh --check
sudo bash install.sh --install
```

نصاب نیاز به systemd، PostgreSQL و دسترسی نصب پکیج‌ها از اینترنت دارد؛ توکن معتبر ربات Telegram و شناسه عددی مدیر را طبق راهنمای نصب وارد کنید. هنگام اولین نصب پایگاه‌داده و کلید رمزنگاری به‌صورت محلی ساخته می‌شوند؛ آنها را ذخیره، محافظت و همراه فایل سورس عمومی توزیع نکنید.

### ارتقای سروری که از قبل فعال است

قبل از ارتقا، **بکاپ دیتابیس و نسخهٔ کلید رمزنگاری** را با روش امن بگیرید، بازیابی آن را در staging بررسی کنید و قطع موقت سرویس را پیش‌بینی کنید. از پوشه ZIP جدید اجرا کنید:

```bash
sudo bash install.sh --backup
sudo bash install.sh --update
sudo bash install.sh --doctor
sudo bash install.sh --status
sudo bash install.sh --logs bot
```

نصب تمیز `--clean-install` را روی سرور دارای داده‌ی واقعی اجرا نکنید؛ ممکن است داده‌های فعال را حذف کند. اسکریپت ارتقا برای حفظ پیکربندی و کلید قبلی طراحی شده است، اما اجرای واقعی آن هنوز باید در VPS شما تأیید شود.

### فعال‌سازی پنل وب

نصب کامل `--install` برای ربات، دیتابیس و API پنل وب در نظر گرفته شده است. برای Mini App عمومی، یک دامنه با DNS معتبر، HTTPS و مقدار `SONAR_WEBAPP_URL` لازم است. پس از اجرای تستی:

```bash
sudo bash install.sh --panel
sudo bash install.sh --domain panel.example.com
sudo bash install.sh --tls panel.example.com admin@example.com
sudo bash install.sh --doctor
```

`panel.example.com` و `admin@example.com` فقط مثال‌اند؛ با دامنه و ایمیل واقعی خودتان جایگزین کنید. از نصب ایجنت روی VPS مشتری صرفاً بعد از تأیید اثر انگشت SSH از منبع مستقل استفاده کنید.

## آزمون‌های قابل‌تکرار

```bash
python3 -m compileall -q .
bash -n install.sh install_web_panel.sh scripts/*.sh
node --check pro_web/static/app.js
python3 scripts/verify_manifest.py
python3 -m pytest -q tests/test_navigation_631.py tests/test_regressions.py tests/test_v6_release.py tests/test_pro_features.py tests/test_uptime_63_unittest.py
```

آزمون‌های End-to-End نیازمند: اتصال واقعی به Telegram، سرور PostgreSQL، SSH، WSS، دامنه/HTTPS، و پکیج‌های `requirements.txt` نصب‌شده‌اند. در محیط حاضر دسترسی به نصب آنلاین وابستگی‌ها ممکن نبود. برای استقرار production پس از smoke test، مسیرهای افزودن سرور، تأیید SSH، مانیتورینگ، هشدار، بکاپ و بازگشت صفحه‌ها را دستی تست کنید.
