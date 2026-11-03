# گزارش نهایی بازسازی و آماده‌سازی GitHub

**پروژه:** SONAR RADAR PRO  
**نسخه اعلام‌شده در مخزن:** `6.9`  
**مبنای بررسی:** فایل ZIP تحویلی در همین گفتگو  
**وضعیت:** بازسازی فایل‌ها در نسخه کاری مستقل انجام شد؛ ZIP اصلی دست‌نخورده باقی مانده است.

## 1. خلاصه اجرایی

| شاخص | مقدار |
|---|---:|
| فایل‌های اولیه در ZIP | 313 |
| ورودی‌های کل آرشیو با احتساب پوشه‌ها | 330 |
| فایل‌های نهایی قابل انتشار | 220 |
| فایل‌های مستقیم ریشه در ابتدا | 127 |
| فایل‌های مستقیم ریشه در نسخه نهایی | 82 |
| فایل‌های Python نهایی | 113 |
| مستندات Markdown نهایی | 64 |
| اسکریپت‌های Bash | 9 |
| پوشه‌های بررسی‌شده توسط ابزار ساختار | 12 |
| بیشترین تعداد فایل مستقیم در یک پوشه | 82 |
| فایل‌های Cache/Bytecode در ZIP نهایی | 0 |

نسخه نهایی از نظر تعداد فایل مستقیم در تمام پوشه‌ها محدودیت 100 فایل را رعایت می‌کند. هیچ فایل اجرایی یا پیکربندی عملیاتی به دلیل ناشناخته بودن یا کوچک بودن حذف نشده است.

## 2. فایل‌های حذف‌شده و علت

### 2.1 فایل‌های Cache

54 فایل `*.pyc` از پوشه‌های `__pycache__/` در ریشه، `bot_logic_mod/`، `scripts/` و `tests/` حذف شدند. این فایل‌ها خروجی کامپایل محلی Python هستند و در انتشار سورس لازم نیستند؛ فایل‌های `.py` متناظر حفظ شده‌اند.

### 2.2 مستندات تکراری در ریشه

نسخه‌های ریشه‌ای زیر با محتوای یکسان در مسیر مرجع دیگری موجود بودند؛ نسخه مرجع حفظ و کپی تکراری ریشه حذف شد:

- `RELEASE_6_2_FA.md`
- `QA_V6_RC1_FA.md`
- `FIXES_5.3_FA.md`
- `AUDIT_6_3_FA.md`
- `RELEASE_6_6_RC1_FA.md`
- `DEPLOY_6_6_RC1_FA.md`
- `RELEASE_6_4_FA.md`
- `DEPLOY_6_5_4_FA.md`
- `DEPLOY_6_6_RC2_FA.md`
- `QA_5.3_FA.md`
- `RELEASE_6_3_1_FA.md`
- `CHANGELOG_5_PRO.md`
- `SONAR_RADAR_PRO_BUGFIX_REPORT_FA.md`
- `BUGFIX_REPORT_FA.md`
- `RELEASE_6_5_3_AUDIT_FA.md`
- `README_PRO_5_FA.md`
- `README_5_PRO_FA.md`
- `RELEASE_NOTES_5_1_FA.md`
- `RELEASE_6_3_FA.md`
- `PATCH_NOTES_5_2_3_FA.md`
- `RELEASE_6_6_RC2_FA.md`
- `RELEASE_6_5_FA.md`
- `RELEASE_6_5_1_FA.md`
- `QA_REPORT_FA.md`
- `RELEASE_6_5_2_FA.md`
- `QA_V6_FA.md`
- `README_PATCH_FA.md`
- `AUDIT_V6_1_RC1_FA.md`
- `CHANGELOG_4.3.md`
- `RELEASE_V6_STATUS_FA.md`
- `RELEASE_6_5_4_FA.md`
- `INSTALL_AUDIT_FA.md`

دو گزارش `SONAR_RADAR_PRO_BUGFIX_REPORT_FA.md` و `BUGFIX_REPORT_FA.md` با گزارش موجود در `docs/operations/BUGFIX_REPORT_2026-10-22_FA.md` یکسان بودند.

### 2.3 کپی‌های تکراری در آرشیو مستندات

13 کپی تکراری از `docs/archive/` حذف شد؛ نسخه‌های مرجع در `docs/history/` نگهداری شدند:

- `docs/archive/legacy_5_3/FIXES_5.3_FA.md`
- `docs/archive/legacy_5_3/QA_5.3_FA.md`
- `docs/archive/CHANGELOG_5_PRO.md`
- `docs/archive/README_PRO_5_FA.md`
- `docs/archive/README_5_PRO_FA.md`
- `docs/archive/RELEASE_NOTES_5_1_FA.md`
- `docs/archive/QA_REPORT_FA.md`
- `docs/archive/legacy_v6_preview/QA_V6_FA.md`
- `docs/archive/README_PATCH_FA.md`
- `docs/archive/CHANGELOG_4.3.md`
- `docs/archive/legacy_v6_preview/RELEASE_V6_STATUS_FA.md`
- `docs/archive/INSTALL_AUDIT_FA.md`
- `docs/archive/legacy_5_3/RELEASE_NOTES.md`

دو فایل یکتای `docs/archive/README.md` و `docs/archive/README_5_3_ORIGINAL.md` حفظ شدند.

### 2.4 ادغام یک سند نزدیک به تکراری

`RELEASE_NOTES.md` در ریشه با `docs/history/RELEASE_NOTES.md` تقریباً یکسان بود و فقط در جمله پایانی تفاوت جزئی داشت. نسخه ریشه حذف شد و جمله دقیق‌تر آن در نسخه مرجع تاریخچه حفظ شد؛ محتوای فنی آن از بین نرفت.

## 3. فایل‌های جابه‌جاشده

15 سند از ریشه به پوشه‌های مسئولیت‌محور منتقل شدند:

| مبدأ | مقصد |
|---|---|
| `PATCH_FILES_6_9_FA.md` | `docs/operations/PATCH_FILES_6_9_FA.md` |
| `RELEASE_6_6_RC3_FA.md` | `docs/history/RELEASE_6_6_RC3_FA.md` |
| `PATCH_6_9_COMPLETION_NOTES_FA.md` | `docs/operations/PATCH_6_9_COMPLETION_NOTES_FA.md` |
| `WEB_PANEL_FIXES_FA.md` | `docs/operations/WEB_PANEL_FIXES_FA.md` |
| `EXISTING_SERVER_SSH_FA.md` | `docs/operations/EXISTING_SERVER_SSH_FA.md` |
| `PUBLISH_GITHUB_FA.md` | `docs/operations/PUBLISH_GITHUB_FA.md` |
| `RELEASE_6_9_FA.md` | `docs/operations/RELEASE_6_9_FA.md` |
| `RELEASE_6_9_PROGRESS_FA.md` | `docs/operations/RELEASE_6_9_PROGRESS_FA.md` |
| `QA_WEB_AGENT_FA.md` | `docs/operations/QA_WEB_AGENT_FA.md` |
| `DEPLOY_6_6_RC3_FA.md` | `docs/history/DEPLOY_6_6_RC3_FA.md` |
| `AUDIT_V6_RC1_FA.md` | `docs/history/AUDIT_V6_RC1_FA.md` |
| `WEB_AGENT_FIXES_FA.md` | `docs/operations/WEB_AGENT_FIXES_FA.md` |
| `PATCH_NOTES_FA.md` | `docs/operations/PATCH_NOTES_FA.md` |
| `RELEASE_7_0_DEV_PROGRESS_FA.md` | `docs/history/RELEASE_7_0_DEV_PROGRESS_FA.md` |
| `UPGRADE_NOTES_FA.md` | `docs/operations/UPGRADE_NOTES_FA.md` |

مسیر لینک‌های Markdown بررسی و اصلاح شد. 19 لینک داخلی Markdown بررسی شدند و مقصد همه آن‌ها وجود دارد.

## 4. فایل‌های ایجادشده و اصلاح‌شده

### فایل‌های جدید

- `.gitignore`: جلوگیری از ورود Cache، محیط مجازی، فایل‌های `.env`، کلیدها، لاگ‌ها و Backupهای محلی به Git.
- `.gitattributes`: تثبیت Line Ending فایل‌های Bash/Python/JavaScript و مستندات روی LF و معرفی تصاویر به‌عنوان Binary.
- `PROJECT_STRUCTURE.md`: نقشه کامل تولیدشده ساختار مخزن.
- `assets/sonar-radar-logo.png`, `assets/sonar-radar-mark.png`: نسخه‌های یکسان از آیکون‌های اصلی برای README و هویت بصری مخزن.
- `scripts/update_project_structure.py`: تولید مجدد نقشه و کنترل سقف 100 فایل مستقیم.
- `docs/REFACTOR_REPORT_FA.md`: همین گزارش، داخل بسته نهایی.

### فایل‌های بازنویسی یا اصلاح‌شده

- `README.md`: بازنویسی فارسی با پیش‌نیازها، نصب، اجرا، ارتقا، نگهداری، حذف، عیب‌یابی و محدودیت مجوز.
- `SECURITY.md`: همسان‌سازی اطلاعات نسخه و شفاف‌سازی محدودیت‌های امنیتی.
- `docs/PROJECT_MAP_FA.md`: نقشه معماری و مسیرهای واقعی ماژول‌ها.
- `docs/PROJECT_STRUCTURE_FA.md`: توضیح ساختار واقعی و روش تولید نقشه.
- `CHANGELOG.md`, `docs/INSTALL_RECOVERY_FA.md`, `docs/WEB_AGENT_FIXES_FA.md` و چند سند عملیاتی/آرشیوی: اصلاح ارجاع‌های مسیر پس از جابه‌جایی.
- `pro_web/index.html`: اصلاح نشانگر نسخه پنل از `v6.6.0` به `v6.9` برای تطبیق با `VERSION`.
- `tests/test_installer_recovery_522.py`: اصلاح دو انتظار قدیمی تست تا رفتار واقعی فعلی Installer و منطق انتظار دیتابیس/مهاجرت‌ها را بررسی کنند.
- `tests/test_web_panel_stability.py`: افزودن کنترل وجود فایل‌های استاتیک در `index.html` و `guide.html` و تطبیق نسخه قابل مشاهده پنل با `VERSION`.
- `MANIFEST_SHA256.txt`: بازتولید فهرست صحت فایل‌ها پس از نهایی‌سازی.

دو تصویر اصلی `sonar-radar-logo.png` و `sonar-radar-mark.png` بدون تغییر حفظ شدند و نسخه‌های یکسان آن‌ها در `assets/` برای README و استفاده برند مخزن قرار گرفتند؛ نسخه‌های runtime در `pro_web/static/` نیز دست‌نخورده‌اند. پس‌زمینه مشکی بخشی از ترکیب فعلی طرح است و نوشته سفید لوگو روی زمینه شفاف/روشن خوانایی خود را از دست می‌دهد؛ بنابراین برای جلوگیری از تغییر هویت بصری، تبدیل به نسخه شفاف انجام نشد.

**نکته درباره فایل‌های رابط:** `pro_web/static/next.js` و `pro_web/static/balancer.js` در ZIP اولیه موجود بودند و هر دو بدون تغییر نسبت به بایت‌های آرشیو اصلی حفظ شدند. فایل‌های اصلی و تنظیمات عملیاتی نیز تغییر نکردند.

## 5. مجوزهای اجرایی و معماری

ZIP اولیه بیت اجرایی فایل‌ها را حفظ نکرده بود. مجوز اجرا برای 11 ورودی اجرایی تنظیم شد:

- `install.sh`
- `install_web_panel.sh`
- همه اسکریپت‌های `scripts/*.sh`
- `scripts/verify_manifest.py`
- `scripts/update_project_structure.py`

پوشه `assets/` برای نگهداری نسخه‌های برندِ مورد استفاده README ایجاد شد. ساختارهای موجود `docs/operations/` و `docs/history/` برای دسته‌بندی مستندات استفاده شدند. دو پوشه قدیمی آرشیو که پس از حذف کپی‌های تکراری خالی شدند، حذف شدند.

## 6. بررسی اطلاعات محرمانه و تنظیمات

اسکن الگوهای متداول Token، کلید خصوصی و Secret انجام شد. هیچ کلید خصوصی یا اعتبارنامه عملیاتی با اطمینان بالا شناسایی نشد. موارد منطبق در فایل‌های تست، داده‌های ساختگی/آزمایشی هستند؛ مقدار محرمانه‌ای در این گزارش بازنشر نشده است. این اسکن جایگزین بازبینی انسانی و Secret scanning سمت GitHub نیست.

هش فایل‌های زیر با نسخه اولیه یکسان باقی ماند؛ بنابراین در این بازسازی تغییری در آن‌ها اعمال نشده است:

- `install.sh`
- `install_web_panel.sh`
- `WEB_PROXY_EXAMPLE.conf`
- `sonar_config.example.json`
- `agent_runtime.lock.json`
- `VERSION`
- `requirements.txt`
- `requirements-web.txt`
- `requirements-dev.txt`

آدرس مخزن، دامنه‌ها، پورت‌ها، تنظیمات اتصال و مقادیر عملیاتی تغییر داده نشدند. فایل اصلی ZIP نیز دست‌نخورده باقی ماند.

## 7. باگ‌ها و اصلاحات

| مورد | نتیجه |
|---|---|
| نبود `.gitignore` | اصلاح شد |
| ریشه با 127 فایل مستقیم | با دسته‌بندی مستندات به 81 فایل رسید |
| مستندات تکراری | کپی‌های یکسان حذف؛ نسخه مرجع حفظ شد |
| Cacheهای Python | 54 فایل `.pyc` حذف شد |
| مجوز اجرا در ZIP | برای 11 اسکریپت/ابزار تنظیم شد |
| نشانگر نسخه پنل `v6.6.0` در برابر `VERSION=6.9` | اصلاح شد |
| دو تست با انتظار قدیمی Installer | با رفتار فعلی کد همسان شد |
| نقشه ساختار دستی/قدیمی | تولید خودکار و کنترل سقف پوشه اضافه شد |
| Manifest قدیمی | دوباره تولید و اعتبارسنجی شد |
| ارجاع‌های Markdown پس از جابه‌جایی | اصلاح و اعتبارسنجی شد |

منطق اصلی برنامه، تنظیمات نصب، مسیرهای SSH/WSS/TLS، APIها و ماژول‌های Python برنامه بازنویسی نشدند. از فایل‌های Python اولیه فقط دو فایل تست اصلاح شدند؛ ابزار ساختار یک فایل Python جدید است.

## 8. نتایج آزمون

| آزمون | نتیجه |
|---|---|
| `python3 -m compileall -q .` | موفق |
| `bash -n` برای 9 اسکریپت Shell | موفق |
| `node --check` برای 8 فایل JavaScript | موفق |
| `bash install.sh --check` | موفق؛ 113 منبع Python و پیکربندی بررسی شد |
| `python3 -m pytest -q -rs` | 292 موفق، 8 Skip، 8 subtest موفق |
| بررسی سقف 100 فایل مستقیم | موفق؛ 11 پوشه بررسی شد |
| بررسی لینک‌های داخلی Markdown | 19 لینک؛ بدون مقصد شکسته |
| بررسی Manifest | موفق؛ 219 فایل سورس در Manifest تأیید شد |

### علت Skip شدن 8 آزمون

محیط بررسی این وابستگی‌ها را نداشت:

- `psycopg2`: یک آزمون بازیابی دیتابیس
- `paramiko`: آزمون‌های SSH و Enterprise
- `python-telegram-bot` با ماژول `telegram`: یک آزمون مربوط به ربات

به همین دلیل آزمون‌های وابسته به این کتابخانه‌ها اجرا نشدند. این موارد به‌عنوان آزمون موفق گزارش نمی‌شوند.

### آزمون‌هایی که در این محیط اجرا نشدند

- نصب کامل روی VPS ایزوله Ubuntu/Debian
- راه‌اندازی واقعی PostgreSQL و بازیابی کامل Backup
- اتصال زنده به Telegram Bot API
- SSH واقعی به سرور مقصد
- WSS/TLS واقعی با Agent راه دور
- صدور گواهی و تست Nginx روی دامنه عمومی

این موارد نیازمند محیط staging واقعی، دسترسی شبکه و اعتبارنامه‌های متعلق به مالک پروژه هستند. نصب مخرب یا تغییر سرویس‌های میزبان فعلی برای شبیه‌سازی آن‌ها انجام نشد. `shellcheck` نیز در محیط موجود نصب نبود.

## 9. مشکلات باقی‌مانده و ملاحظات انتشار

1. پروژه در مستندات خود هنوز Release Candidate معرفی شده است؛ پیش از انتشار عملیاتی، تست‌های انتهابه‌انتها در VPS آزمایشی لازم‌اند.
2. فایل `LICENSE` در مخزن وجود ندارد؛ مجوز بازنشر/استفاده مجدد باید توسط مالک تعیین شود.
3. آزمون‌های Skip شده پس از نصب وابستگی‌های واقعی باید دوباره اجرا شوند.
4. انتشار یا Push به GitHub انجام نشده است؛ فقط بسته آماده انتشار تهیه شده است.
5. برای نصب تولیدی، Commit مشخص و تأییدشده را به‌جای اتکای بدون قید به شاخه متغیر `main` انتخاب کنید.

## 10. روش دقیق دریافت و نصب از GitHub با Bash

روش پیشنهادی:

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates

git clone https://github.com/Amirtn9/radarsonarplus.git
cd radarsonarplus

bash install.sh --check
sudo bash install.sh --install
```

برای ارتقا از همان Clone، در حالی که پوشه سورس خارج از مسیر نصب زنده است:

```bash
git pull --ff-only
bash install.sh --check
sudo bash install.sh --update
```

روش Bootstrap با دریافت فقط Installer:

```bash
wget https://raw.githubusercontent.com/Amirtn9/radarsonarplus/main/install.sh
chmod +x install.sh
sudo ./install.sh --install
```

برای گزینه‌ها و عملیات نگهداری:

```bash
bash install.sh --help
sudo bash install.sh --status
sudo bash install.sh --doctor
sudo bash install.sh --logs errors --lines 200
```

**نتیجه:** بسته برای بازبینی و انتشار در GitHub آماده شده است؛ اما تأیید نهایی استقرار عملیاتی منوط به اجرای تست‌های یکپارچه در محیط staging واقعی است.
