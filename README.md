> نسخه جاری **۶.۹.۷** (`6.9.10`): [اعتبارسنجی CI، سازگاری Python و اصلاح ثبت لاگ](docs/RELEASE_6_9_10_FA.md).

<div align="center">
  <img src="assets/sonar-radar-logo.png" alt="SONAR RADAR logo" width="240" />
  <h1>SONAR RADAR PRO</h1>
  <p><strong>سامانه مدیریت سرورها، Agentها و سلامت سرویس‌ها</strong></p>
  <p>ربات تلگرام، پنل وب و Mini App با مسیرهای ارتباطی کنترل‌شده</p>
</div>

---

## معرفی

**SONAR RADAR PRO** یک سامانه مدیریت زیرساخت است که مدیریت سرورها، Agentهای راه دور، منابع اتصال، مانیتورهای سرویس و رخدادهای عملیاتی را از طریق ربات تلگرام و پنل وب فراهم می‌کند. پنل و ربات از منطق برنامه و پایگاه داده مشترک استفاده می‌کنند.

نسخه فعلی `6.9.10` در `VERSION` ثبت شده است. وضعیت پروژه همچنان Release Candidate است؛ بنابراین پیش از استفاده عملیاتی، نصب، مهاجرت دیتابیس، بازیابی بکاپ و مسیرهای واقعی SSH/WSS/TLS را در محیط آزمایشی بررسی کنید.

**مخزن رسمی:** [github.com/Amirtn9/radarsonarplus](https://github.com/Amirtn9/radarsonarplus)

## قابلیت‌های موجود

- **مدیریت سرورها:** ثبت سرور، گروه‌بندی، احراز هویت SSH با رمز یا کلید خصوصی در پنل وب، بررسی اتصال و مدیریت Agent.
- **ارتباط چندمسیره:** تلاش مستقیم از کنترل‌کننده مرکزی و استفاده از Agentهای سالم و تأییدشده در صورت خطای شبکه.
- **اعتماد SSH:** نمایش fingerprint و الزام تأیید مستقل؛ خطای کلید ناشناخته/تغییریافته به اعتماد خودکار تبدیل نمی‌شود.
- **Agent و WSS/TLS:** بررسی گواهی، توکن، heartbeat و وضعیت واقعی Agent پیش از استفاده به‌عنوان relay.
- **مانیتورینگ:** پایش HTTP(S)/TCP، تاریخچه رخدادها و اعلان‌های مرتبط.
- **کانفیگ و اشتراک:** مدیریت منابع پشتیبانی‌شده و نمایش نتیجه آزمون بر اساس داده ثبت‌شده.
- **پنل وب و Mini App:** داشبورد، جزئیات سرور، عملیات مدیریتی، ترمینال SSH، استخر Agent و بالانسر مسیرها.
- **رابط یکپارچه 6.9.10:** پوسته مشترک وب و Telegram Mini App با طراحی Apple-inspired، پوسته روشن/تاریک، ناوبری موبایل، Safe Area، فیلترهای واقعی علاقه‌مندی و رخدادها و نمایش وضعیت صف اعلان‌ها.
- **عملیات سازمانی:** صف عملیات، سلامت، گزارش‌ها، Backup، Webhook و انتشار تدریجی Agent؛ جزئیات به پیکربندی و کلیدهای سمت سرور وابسته است.
- **عیب‌یابی:** بررسی نصب، لاگ‌ها، بسته پشتیبانی و ابزارهای Backup/Restore.

## معماری در یک نگاه

```text
Telegram Bot ───────┐
                    ├── Application Services / API ─── PostgreSQL
Web Panel / Mini App┘

Central Controller ── first SSH attempt ──> Destination
        │
        └── approved, healthy Agent relay ─> Destination
```

- مسیر مستقیم کنترل‌کننده اولویت نخست است.
- Relay فقط برای Agentهای مجاز، سالم و دارای TLS/توکن معتبر در دسترس است.
- خطای fingerprint یا احراز هویت SSH نباید با مسیر جایگزین ناامن دور زده شود.
- Bot و Web از یک migration ledger نسخه‌دار و PostgreSQL advisory lock مشترک استفاده می‌کنند؛ فقط یک دیتابیس وجود دارد.
- پنل وب از API محلی استفاده می‌کند و برای انتشار عمومی باید پشت reverse proxy و HTTPS معتبر قرار بگیرد.

## نسخه‌ها و وابستگی‌ها

- `requirements.txt` و `requirements-web.txt` نسخه دقیق وابستگی‌های مستقیم Runtime را pin می‌کنند.
- وابستگی S3 اختیاری است؛ فقط در صورت استفاده از مقصد S3، در virtualenv برنامه `pip install -r requirements-s3.txt` اجرا کنید.
- آزمون‌ها و CI از `requirements-dev.txt` استفاده می‌کنند. وابستگی‌های مستقیم و transitive محیط CI لینوکس برای Python 3.10 تا 3.12 در `requirements/ci-py*.lock` همراه hash قفل شده‌اند؛ این lockها پوشش همه سیستم‌عامل‌ها و افزونه اختیاری S3 را ادعا نمی‌کنند.
- Bootstrap تک‌فایلی فقط با `SONAR_RELEASE_COMMIT` چهل‌رقمی تأییدشده سورس را دریافت می‌کند؛ دریافت پیش‌فرض از `main` غیرفعال است.

## پیش‌نیازها

### سیستم‌عامل و ابزارهای میزبان

- Ubuntu یا Debian با `systemd`
- Python نسخه `3.10` یا بالاتر
- `apt-get`, `systemctl`, `runuser` و دسترسی مدیریتی `sudo` یا root
- اتصال شبکه برای دریافت بسته‌های سیستم و وابستگی‌های Python
- PostgreSQL؛ نصب‌کننده بسته‌های موردنیاز را روی میزبان پشتیبانی‌شده نصب می‌کند

نصب‌کننده فقط خانواده سیستم‌عامل Ubuntu/Debian را می‌پذیرد؛ نسخه‌های دیگر یا سیستم‌های بدون systemd در این پروژه تأیید نشده‌اند.

### اطلاعات موردنیاز برای پیکربندی

- توکن معتبر ربات تلگرام
- شناسه عددی مدیر اصلی
- برای پنل عمومی: دامنه‌ای که DNS آن به سرور اشاره کند و امکان صدور HTTPS
- برای عملیات SSH: دسترسی شبکه به مقصد و fingerprint قابل بررسی از یک منبع مستقل

برای حساب SSH مقصدی که `root` نیست (از جمله `ubuntu`)، نصب Agent به دسترسی `sudo -n` نیاز دارد. روی همان سرور مقصد این موارد را بررسی کنید:

```bash
sudo -n true
sudo -n id -u
```

دستور دوم باید `0` برگرداند. ربات رمز sudo را در فرمان‌ها قرار نمی‌دهد.

## نصب از GitHub

### روش پیشنهادی: دریافت کامل مخزن

مخزن را با کاربر عادی دریافت کنید؛ فقط نصب و تغییر سرویس‌های سیستم را با `sudo` اجرا کنید.

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates

git clone https://github.com/Amirtn9/radarsonarplus.git
cd radarsonarplus

# مقدار را با SHA چهل‌رقمی تأییدشده از انتشار رسمی جایگزین کنید.
APPROVED_COMMIT='replace-with-approved-40-hex-sha'
git fetch --no-tags origin "$APPROVED_COMMIT"
git checkout --detach "$APPROVED_COMMIT"

bash install.sh --check
sudo bash install.sh --install
```

برای نمایش منوی تعاملی:

```bash
sudo bash install.sh
```

در نصب تعاملی، گزینه نصب کامل را انتخاب کنید. نصب کامل شامل ربات، PostgreSQL و API خصوصی پنل وب است.

### روش Bootstrap با دریافت فقط install.sh

اسکریپت مستقل فقط در صورت دریافت SHA چهل‌رقمیِ تأییدشده اجازه دریافت سورس را می‌دهد؛ شاخه شناور `main` به‌صورت پیش‌فرض پذیرفته نمی‌شود:

```bash
export SONAR_RELEASE_COMMIT='replace-with-approved-40-hex-sha'
wget "https://raw.githubusercontent.com/Amirtn9/radarsonarplus/${SONAR_RELEASE_COMMIT}/install.sh"
chmod +x install.sh
sudo -E ./install.sh --install
```

SHA را از انتشار رسمی/مخزن مورد اعتماد و از مسیر مستقل تأیید کنید. اگر سورس کامل و بررسی‌شده را از آرشیو انتشار دریافت کرده‌اید، نصب‌کننده محلی را اجرا کنید؛ مسیر Bootstrap استفاده نمی‌شود.

## اجرای پروژه

پس از نصب موفق، سرویس‌ها توسط systemd مدیریت می‌شوند:

- `sonar-bot`: ربات تلگرام
- `sonar-web`: API پنل وب و Mini App
- `postgresql`: پایگاه داده

برای مشاهده وضعیت:

```bash
sudo bash install.sh --status
sudo systemctl status sonar-bot
sudo systemctl status sonar-web
sudo systemctl status postgresql
```

API وب به‌صورت محلی اجرا می‌شود؛ مقدار پیش‌فرض پورت `8090` است و نصب‌کننده آن را از تنظیمات محیطی می‌خواند. برای دسترسی عمومی، دامنه و HTTPS را با راهنمای نصب‌کننده و Nginx تنظیم کنید.

## دستورات نگهداری

| دستور | کاربرد |
|---|---|
| `bash install.sh --check` | بررسی آفلاین سورس، نحو Bash و پیکربندی نمونه |
| `sudo bash install.sh --install` | نصب کامل جدید |
| `sudo bash install.sh --update` | ارتقای نصب موجود با Backup پیش از تغییر |
| `sudo bash install.sh --inventory` | شناسایی اجزای نصب‌شده |
| `sudo bash install.sh --status` | وضعیت سرویس‌ها و نسخه نصب‌شده |
| `sudo bash install.sh --doctor` | عیب‌یابی نصب، Python، سرویس‌ها و PostgreSQL |
| `sudo bash install.sh --logs errors --lines 200` | مشاهده لاگ خطاها |
| `sudo bash install.sh --backup` | ساخت و اعتبارسنجی Backup |
| `sudo bash install.sh --restore /path/to/backup.dump` | بازیابی Backup به‌همراه `.secret.key`، `.sha256` و فایل‌های جانبی همان Backup پس از تأیید |
| `sudo bash install.sh --repair` | تعمیر سرویس‌ها با کنترل‌های موجود |
| `sudo bash install.sh --support` | تولید ZIP پشتیبانیِ پاک‌سازی‌شده |
| `sudo bash install.sh --uninstall` | آرشیو برنامه و حذف سرویس‌ها؛ پایگاه داده باقی می‌ماند |
| `sudo bash install.sh --purge` | حذف غیرقابل‌بازگشت همه منابع محلی Sonar، دیتابیس، کلیدها، لاگ‌ها و Backupها؛ سپس خروج از اسکریپت |

اجرای بدون آرگومان، منوی تمام‌صفحه `whiptail` را (در صورت نصب بودن) باز می‌کند؛ در غیر این صورت منوی رنگی متنی نمایش داده می‌شود. گزینه حذف کامل با عبارت دقیق `DELETE ALL SONAR DATA` تأیید می‌شود و هیچ نصب مجددی انجام نمی‌دهد. این گزینه بسته‌های مشترک PostgreSQL/Nginx، دیتابیس‌های دیگر، SSH، گواهی‌های TLS و Agentهای راه‌دور را حذف نمی‌کند. برای جزئیات محدوده حذف، `docs/INSTALLER_PURGE_FA.md` را ببینید.

فهرست کامل گزینه‌ها:

```bash
bash install.sh --help
```

### بروزرسانی

مخزن را خارج از مسیر نصب زنده (`/opt/radar-sonar`) نگه دارید. از پوشه Cloneشده:

```bash
git pull --ff-only
bash install.sh --check
sudo bash install.sh --update
```

نصب‌کننده پیش از ارتقا Backup می‌گیرد و در شکست استقرار، بازگردانی کد و واحدهای سرویس را تلاش می‌کند. مهاجرت‌های دیتابیس به‌صورت خودکار rollback نمی‌شوند؛ Backup و برنامه بازیابی باید پیش از ارتقا بررسی شوند.

### حذف

```bash
sudo bash install.sh --uninstall
```

برای تأیید باید عبارت `UNINSTALL SONAR` را در ترمینال وارد کنید. برنامه و تنظیمات در مسیر Backup آرشیو می‌شوند؛ سرویس‌ها و پیکربندی Nginx پروژه حذف می‌شوند، اما پایگاه داده، بسته‌های سیستم و Backupها باقی می‌مانند.

گزینه `--clean-install` با تأیید جداگانه، داده و پایگاه داده اختصاصی Sonar را نیز حذف می‌کند. این عملیات مخرب است؛ فقط پس از ساخت و بررسی Backup و در صورت نیاز واقعی اجرا شود.

## ساختار مخزن

| مسیر | کاربرد |
|---|---|
| `bot.py`, `dispatcher.py`, `bot_logic.py`, `bot_logic_mod/` | ربات تلگرام و منطق تعاملات |
| `pro_portal.py`, `*_api.py` | API پنل وب و عملیات مدیریتی |
| `pro_web/` | رابط وب، Mini App و منابع استاتیک |
| `assets/` | نسخه‌های اصلی نشان/لوگو برای مستندات مخزن |
| `database.py`, `workspace_service.py` | پایگاه داده، فضای کاری و مالکیت |
| `agent_*.py`, `monitor_agent.py`, `ssh_relay.py` | نصب و ارتباط Agent |
| `install.sh`, `install_web_panel.sh`, `scripts/` | نصب، ارتقا و نگهداری |
| `tests/` | آزمون‌های خودکار |
| `docs/operations/` | راهنماهای عملیاتی نسخه جاری |
| `docs/history/`, `docs/archive/` | گزارش‌ها و مستندات نسخه‌های قدیمی |
| `PROJECT_STRUCTURE.md` | نقشه کامل و تولیدشده ساختار |

برای بازتولید نقشه:

```bash
python3 scripts/update_project_structure.py
```

## آزمون و کنترل کیفیت

پس از نصب وابستگی‌های توسعه:

```bash
python3 -m compileall -q .
bash -n install.sh install_web_panel.sh scripts/*.sh
node --check pro_web/static/app.js
node --check pro_web/static/next.js
node --check pro_web/static/balancer.js
python3 -m pytest -q
```

`requirements-dev.txt` وابستگی‌های توسعه را مشخص می‌کند. اجرای کامل آزمون‌ها به وابستگی‌های runtime تعریف‌شده در `requirements.txt` و `requirements-web.txt` نیز نیاز دارد. آزمون‌هایی که به Telegram، SSH، PostgreSQL، systemd یا سرور واقعی نیاز دارند ممکن است در محیط آفلاین Skip شوند؛ Skip به معنی تأیید آن قابلیت نیست.

## عیب‌یابی

- **`HOST_KEY_UNKNOWN` یا `HOST_KEY_CHANGED`:** fingerprint را از کنسول یا پنل مستقل ارائه‌دهنده مقصد بررسی کنید؛ اعتماد خودکار را فعال نکنید.
- **Agent در استخر relay دیده نمی‌شود:** وضعیت سرویس، TLS enrollment، توکن، heartbeat پنج دقیقه اخیر، تأیید مدیر و رضایت مالک را بررسی کنید.
- **خطای دسترسی SSH یا `SUDO_REQUIRED`:** نام کاربر، پورت SSH، مجوز کلید و خروجی `sudo -n id -u` را روی مقصد بررسی کنید.
- **پنل در دسترس نیست:** `sonar-web`, `postgresql`, پورت محلی API، Nginx، DNS و گواهی HTTPS را بررسی کنید.
- **نصب یا ارتقا متوقف می‌شود:** `sudo bash install.sh --logs install --lines 300` و سپس `sudo bash install.sh --doctor` را اجرا کنید.
- **Backup قابل بازیابی نیست:** dump، فایل هم‌نام `.secret.key` و فایل `.sha256` را کنار هم نگه دارید؛ `--restore` کلید متناظر را همراه دیتابیس بازیابی می‌کند، هش‌ها و رمزگشایی credentialها را بررسی می‌کند و dump بدون sidecar کلید را رد می‌کند.

راهنمای بازیابی و عملیات سازمانی:
- [گزارش وضعیت اصلاح ممیزی](docs/SECURITY_REMEDIATION_6_9_2_FA.md)
- [مهاجرت نسخه‌دار دیتابیس](docs/SCHEMA_MIGRATIONS_FA.md)
- [احراز هویت SSH با کلید خصوصی](docs/SSH_KEY_AUTH_FA.md)
- [راهنمای Enterprise 6.9.10](docs/ENTERPRISE_6_9_RUNBOOK_FA.md)
- [بازیابی نصب](docs/INSTALL_RECOVERY_FA.md)
- [بازیابی خطای Startup](docs/STARTUP_RECOVERY_6_9_FA.md)
- [نکات ارتقا](docs/operations/UPGRADE_NOTES_FA.md)
- [نقشه معماری](docs/PROJECT_MAP_FA.md)
- [ساختار پروژه](docs/PROJECT_STRUCTURE_FA.md)
- [سیاست امنیتی](SECURITY.md)

## امنیت و اطلاعات محرمانه

- فایل `sonar_config.json`, `secret.key`, فایل‌های `.env`, کلیدهای خصوصی و dump دیتابیس نباید Commit شوند.
- مقادیر واقعی توکن، رمز و کلید را در README، Issue یا بسته پشتیبانی قرار ندهید.
- پنل عمومی را پشت HTTPS منتشر کنید و API محلی را مستقیماً روی اینترنت باز نکنید.
- fingerprint SSH را خارج از همان اتصال بررسی کنید.
- پیش از ارسال بسته پشتیبانی، محتویات آن را بازبینی کنید؛ پاک‌سازی خودکار تضمین حذف همه داده‌های حساس نیست.

## مجوز و مشارکت

در نسخه فعلی مخزن فایل `LICENSE` وجود ندارد؛ بنابراین مجوز بازنشر و استفاده مجدد از سورس در این بسته مشخص نشده است. پیش از انتشار عمومی، Fork یا توزیع مجدد، مجوز را با مالک مخزن هماهنگ کنید. فهرست رسمی مشارکت‌کنندگان نیز در این بسته تعریف نشده است.

برای گزارش باگ، نسخه پروژه، سیستم‌عامل، مراحل بازتولید و خروجی پاک‌سازی‌شده لاگ را ارائه دهید. برای مسائل امنیتی از مسیر خصوصی معرفی‌شده در `SECURITY.md` استفاده کنید. هرگز توکن، کلید خصوصی، رمز SSH یا dump خام دیتابیس را ارسال نکنید.

---

<div align="center">
  <img src="assets/sonar-radar-mark.png" alt="SONAR RADAR mark" width="88" />
  <p><strong>SONAR RADAR PRO</strong></p>
</div>
