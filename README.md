<h1 align="center">🦇 SONAR RADAR PRO</h1>
<p align="center"><strong>مدیریت، پایش و کنترل سرورها از تلگرام و پنل وب</strong></p>
<p align="center">Telegram Bot · Secure WSS Agent · Mini App · Monitoring Dashboard</p>
<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-5.2.1--github-2563eb?style=for-the-badge">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776ab?style=for-the-badge&amp;logo=python&amp;logoColor=white">
  <img alt="Platform" src="https://img.shields.io/badge/Ubuntu%20%7C%20Debian-Linux-111827?style=for-the-badge&amp;logo=linux&amp;logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/release-staging%20validation-f59e0b?style=for-the-badge">
</p>
<p align="center">
  <a href="#installation">نصب سریع</a> ·
  <a href="#features">قابلیت‌ها</a> ·
  <a href="#management">منوی مدیریت</a> ·
  <a href="#logs">لاگ‌ها</a> ·
  <a href="#security">امنیت</a>
</p>

---

## ✨ رادار سونار چیست؟

**Sonar Radar Pro** سامانه‌ای برای مدیریت و پایش زیرساخت‌های سروری از طریق **ربات تلگرام** و **Telegram Mini App** است. پروژه از یک ایجنت راه‌دور با ارتباط رمزنگاری‌شده، ذخیره‌سازی PostgreSQL، مدیریت پیکربندی‌ها و مشاهده گزارش‌ها استفاده می‌کند.

> **وضعیت انتشار:** نسخه `5.2.1-github` برای بررسی و تست روی سرور آزمایشی آماده شده است. نصب واقعی روی Ubuntu تمیز، اتصال زنده تلگرام، PostgreSQL و ثبت ایجنت‌ها هنوز به تأیید عملی در محیط شما نیاز دارد. از آن مستقیماً به‌عنوان نسخه Production تضمین‌شده استفاده نکنید.

<a id="installation"></a>

## 🚀 نصب سریع

برای شروع، روی سرور **Ubuntu/Debian دارای systemd** وارد ترمینال شوید و این سه دستور را اجرا کنید (این کد را قبل از اجرا از مخزن بررسی کنید):

```bash
wget https://raw.githubusercontent.com/Amirtn9/radarsonarplus/main/install.sh
chmod +x install.sh
./install.sh
```

> اگر با کاربر `root` وارد نشده‌اید، دستور آخر را به‌صورت `sudo ./install.sh` اجرا کنید. اجرای این فایل دسترسی مدیریتی می‌خواهد.

اسکریپت ابتدا سورس کامل پروژه را از مخزن **[Amirtn9/radarsonarplus](https://github.com/Amirtn9/radarsonarplus)** دریافت می‌کند و سپس منوی تعاملی نصب را باز می‌کند. **گزینه 01 به‌تنهایی Bot + PostgreSQL + پنل Mini App را نصب و با systemd فعال می‌کند**. برای عرضه عمومی پنل در تلگرام، دامنه و SSL جداگانه لازم‌اند. مراحل پیشنهادی در منو:

1. انتخاب **`01`** برای نصب یک‌جای ربات، PostgreSQL، پنل Mini App و سرویس‌های systemd؛ توکن ربات و شناسه عددی ادمین درخواست می‌شوند.
2. گزینه **`03`** فقط برای نصب مجدد یا تعمیر پنل به‌صورت مستقل کاربرد دارد.
3. انتخاب **`04`** برای تنظیم دامنه و دریافت گواهی HTTPS، در صورت داشتن دامنه معتبر.
4. انتخاب **`10`** برای بررسی سلامت سرویس‌ها و سپس تست دستور `/start` در تلگرام.

**پیش‌نیاز:** فایل‌های نسخه جدید باید در شاخه `main` مخزن عمومی [`Amirtn9/radarsonarplus`](https://github.com/Amirtn9/radarsonarplus) قرار بگیرند. لینک دانلود فعلی GitHub تا قبل از آپلود نسخه جدید، این اصلاحات را ندارد.

### روش جایگزین: نصب از کل مخزن

اگر ترجیح می‌دهید قبل از اجرا کدها را بررسی کنید:

```bash
git clone https://github.com/Amirtn9/radarsonarplus.git
cd radarsonarplus
bash install.sh --check
sudo bash install.sh
```

در این روش منو مستقیماً با سورس موجود باز می‌شود. اجرای `--check` فقط بررسی اولیه کد و محیط است، نه تضمین عملکرد سرور.

<a id="features"></a>

## 💎 قابلیت‌های پروژه

| بخش | امکانات |
|:---|:---|
| 🤖 **Telegram Bot** | کنترل از تلگرام، مدیریت سرورها و اجرای عملیات مدیریتی |
| 🦇 **Secure Agent** | ارتباط WebSocket رمزنگاری‌شده (WSS)، گواهی اختصاصی و کنترل اتصال |
| 🖥️ **Mini App** | داشبورد، منابع، رتبه‌بندی‌ها، رخدادها، برچسب‌ها و تنظیمات |
| 📊 **Monitoring** | وضعیت و شاخص‌های پایش، رخدادها و ثبت گزارش‌ها |
| 🧠 **PostgreSQL** | نگهداری اطلاعات سرویس‌ها و تنظیمات |
| 🔐 **Security** | کنترل هویت Telegram Mini App، محدودیت اندازه پیام و مدیریت Secretها |
| 🛠️ **Installer** | نصب، ارتقا، مدیریت پنل، TLS، بکاپ، بازیابی و عیب‌یابی |
| 📜 **Logging** | لاگ زنده، فیلتر خطاها، بازه زمانی و خروجی JSON |

<a id="management"></a>

## 🧭 منوی نصب و مدیریت

```text
╭───────────────────────────────────────────────────────╮
│                    🦇 SONAR / RADAR                  │
│                 PRO 5.2.1 · Control Center            │
╰───────────────────────────────────────────────────────╯

01  Install Full Stack (Bot + DB + Mini App)
02  Update application safely
03  Install / repair Mini App panel
04  Configure domain + HTTPS
05  Settings & credentials
06  Service management
07  Professional logs (live/filter/JSON)
08  Create verified backup
09  Restore database from .dump
10  Diagnostics / health
11  Preflight source check
12  Uninstall (archive only)
13  Repair installed services
00  Exit
```

> منو پس از اجرای اینستالر باز می‌شود. نصب Bot، پنل و تنظیم HTTPS در نسخه فعلی **گام‌های جداگانه** هستند، نه یک نصب کاملاً بدون تعامل.

### دستورات کاربردی

دستورهای زیر را داخل پوشه سورس پروژه اجرا کنید:

```bash
# وضعیت سرویس‌ها
sudo bash install.sh --status

# بررسی سلامت نصب
sudo bash install.sh --doctor

# بررسی اولیه سورس
bash install.sh --check

# نصب پنل وب
sudo bash install.sh --panel

# تنظیم دامنه HTTP
sudo bash install.sh --domain panel.example.com

# دریافت گواهی HTTPS پس از تنظیم DNS
sudo bash install.sh --tls panel.example.com admin@example.com

# پشتیبان‌گیری
sudo bash install.sh --backup

# ارتقا با نگهداری نسخه قبلی
sudo bash install.sh --update

# راه‌اندازی مجدد ربات
sudo bash install.sh --service sonar-bot restart
```

`panel.example.com` و `admin@example.com` نمونه‌اند و باید جایگزین شوند. برای گرفتن TLS، DNS دامنه و دسترسی اینترنتی پورت‌های 80 و 443 باید درست تنظیم شده باشند.

<a id="logs"></a>

## 📜 سیستم حرفه‌ای لاگ‌ها

برای تشخیص خطاها بدون حدس زدن و ور رفتن بی‌هدف با سرور:

```bash
# لاگ زنده ربات
sudo bash install.sh --logs bot --follow

# لاگ پنل وب
sudo bash install.sh --logs web --lines 200

# لاگ زنده ربات و پنل با هم
sudo bash install.sh --logs all --follow

# فقط خطاها در یک ساعت اخیر
sudo bash install.sh --logs errors --since '1 hour ago'

# جست‌وجوی یک عبارت در لاگ‌ها
sudo bash install.sh --logs bot --grep 'ERROR' --lines 250

# لاگ ساختاریافته JSON
sudo bash install.sh --logs json --lines 100

# جزئیات نصب و بسته‌های وابسته
sudo bash install.sh --logs install --lines 200
```

همین موارد از گزینه **`07`** اینستالر هم قابل دسترسی‌اند. برای خروج از لاگ زنده `Ctrl+C` را بزنید.

مسیرهای مهم: `/var/log/sonar/` برای گزارش‌های اپلیکیشن، `/var/log/sonar-install.log` برای نصب و `journalctl -u sonar-bot` یا `journalctl -u sonar-web` برای سرویس‌ها.

<details>
<summary><b>عیب‌یابی سریع</b></summary>

```bash
sudo bash install.sh --doctor
sudo systemctl status sonar-bot sonar-web --no-pager
sudo journalctl -u sonar-bot -n 100 --no-pager
sudo journalctl -u sonar-web -n 100 --no-pager
sudo bash install.sh --repair
```

`--repair` فقط کامپایل و بازراه‌اندازی سرویس‌های نصب‌شده را بررسی می‌کند؛ رفع DNS، فایروال، دیتابیس یا ناسازگاری کد را تضمین نمی‌کند.

</details>

<a id="security"></a>

## 🔒 نکات امنیتی مهم

- اتصال ایجنت‌ها در این نسخه باید **WSS** باشد. ایجنت‌های قدیمی `ws://` باید از مسیر مدیریتی دوباره ثبت و نصب شوند.
- پورت هر ایجنت را در فایروال **فقط به IP ربات** محدود کنید. TLS به‌تنهایی جایگزین محدودسازی شبکه نیست.
- نصب خودکار Xray فقط با URL نسخه معین و SHA-256 معتبر انجام می‌شود؛ در غیر این صورت ممکن است تست‌های وابسته به Xray اجرا نشوند.
- فایل‌های `sonar_config.json`، `secret.key`، `.env` و کلیدهای خصوصی را هرگز در GitHub آپلود نکنید.
- API مینی‌اپ روی `127.0.0.1` راه‌اندازی می‌شود؛ برای دسترسی عمومی باید پشت nginx و HTTPS قرار بگیرد.
- اعتبار Telegram Mini App محدود است و پس از انقضای نشست، باید مینی‌اپ را دوباره باز کنید.
- کنترل دسترسی بعضی مسیرهای قدیمی Telegram هنوز نیازمند بازبینی گسترده‌تر است. برای محیط چندمستاجری نامطمئن، پیش از استفاده عملی ممیزی امنیتی ضروری است.

برای جزئیات کامل به [`SECURITY.md`](SECURITY.md) و [`UPGRADE_NOTES_FA.md`](UPGRADE_NOTES_FA.md) مراجعه کنید.

## 🧱 ساختار پروژه

```text
radarsonarplus/
├── install.sh               # Installer & Service Manager
├── bot.py                   # Telegram Bot
├── core.py                  # Core & Agent Enrollment
├── monitor_agent.py         # Secure Remote Agent
├── ws_client.py             # Agent Connection Manager
├── database.py              # PostgreSQL Operations
├── pro_portal.py            # Mini App API
├── pro_web/                 # Mini App Frontend
│   └── static/
├── bot_logic_mod/           # Telegram Features
├── scripts/
│   ├── logs.sh              # Professional Log Console
│   ├── healthcheck.sh       # Health Check
│   ├── repair.sh            # Service Repair
│   └── verify_manifest.py  # Package Integrity
├── tests/                   # Automated Tests
├── requirements.txt
├── requirements-web.txt
├── SECURITY.md
└── README.md
```

## 🧪 تست و بررسی سورس

```bash
python3 scripts/verify_manifest.py
bash -n install.sh scripts/*.sh
python3 -m compileall -q .
python3 -m pytest -q tests
```

بعضی تست‌ها به کتابخانه‌های پروژه نیاز دارند. نتیجه موفق تست آفلاین، به معنی تأیید نصب واقعی، ارتباط SSH و TLS، دیتابیس یا تلگرام نیست.

## 🔄 ارتقا و نسخه پشتیبان

قبل از ارتقای سرویس:

1. از دیتابیس، تنظیمات و کلید رمزنگاری نسخه پشتیبان تهیه و **خارج از سرور** نگهداری کنید.
2. فایل‌های پروژه را در مخزن به نسخه جدید به‌روزرسانی کنید.
3. سورس جدید را دریافت کنید و از داخل همان پوشه `sudo bash install.sh --update` اجرا کنید. اگر `install.sh` را با `wget` دوباره دانلود کرده‌اید، همان فایل ابتدا آخرین سورس `main` را می‌گیرد و بعد `--update` را اجرا می‌کند.
4. نتیجه `--doctor` و لاگ‌ها را بررسی کنید.

> نکته: Rollback کد لزوماً تغییرات Schema دیتابیس را به عقب برنمی‌گرداند. قبل از ارتقای نسخه‌های دارای Migration حتماً بکاپ معتبر داشته باشید.

## 📎 لینک پروژه

**GitHub:** [Amirtn9/radarsonarplus](https://github.com/Amirtn9/radarsonarplus)

---

<div align="center">

**🦇 SONAR RADAR PRO**

*Monitor smarter. Manage securely.*

ساخته‌شده برای مدیریت دقیق‌تر زیرساخت، با اولویت امنیت و قابلیت عیب‌یابی.

</div>
