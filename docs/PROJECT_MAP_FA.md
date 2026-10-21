# نقشه پروژه و مسیرهای ارتباط

| بخش | مسیر | وظیفه |
|---|---|---|
| ربات | `bot.py`, `bot_logic_mod/`, `dispatcher.py` | فرمان‌ها و ویزاردهای تلگرام |
| منطق مرکزی | `core.py`, `ssh_support.py`, `server_onboarding.py` | SSH مستقیم، اثرانگشت و fallback |
| مسیر Agent | `ssh_relay.py`, `monitor_agent.py`, `ws_client.py` | SSH روی WSS دارای TLS پین‌شده، مانیتورینگ |
| نصب Agent | `agent_deploy.py`, `agent_bootstrap.py` | نصب، بررسی پورت، راه‌اندازی و اعتبارسنجی WSS |
| دیتابیس | `database.py`, `workspace_service.py` | کاربران، مالکیت، منابع، رمزنگاری |
| API وب | `pro_portal.py`, `web_operations.py`, `console_api.py`, `fleet_api.py` | ورود امن، API و عملیات منابع |
| صف وب | `web_agent_jobs.py` | کارهای ماندگار نصب و نمایش وضعیت |
| رابط وب | `pro_web/`, `pro_web/static/` | پنل مرورگر و Telegram Mini App |
| منابع Xray | `tunnel_logic.py`, `agent_xray_bridge.py` | تست کانفیگ و Xray |
| وظایف دوره‌ای | `cronjobs.py`, `monitor_fleet.py`, `service_monitor.py` | پایش و صف‌های مدیریتی |
| نصب و تست | `install.sh`, `scripts/`, `tests/` | راه‌اندازی، عیب‌یابی و آزمون‌ها |
| اسناد | `docs/`, `docs/history/`, `RELEASE_6_6_RC3_FA.md` | مستندات و گزارش نسخه‌ها |

## مسیر افزودن سرور

1. درخواست از وب/مینی‌اپ یا تلگرام با IP و پورت SSH.
2. اسکن کلید ابتدا **مستقیم از مرکز**؛ در خطای شبکه، Agentهای سالم و تأییدشده، به ترتیب امتیاز.
3. نمایش SHA256 کلید مشاهده‌شده و الزام مقایسه **مستقل** با کنسول سرور.
4. احراز هویت SSH با همان کلید مورد تأیید؛ ذخیره کلید فقط بعد از احراز هویت.
5. ثبت در جدول `servers` با رمزنگاری قبلی و شناسه فضای کاری.
6. در صورت درخواست، افزودن نصب به `web_agent_install_jobs` با کنترل مالکیت و وضعیت قابل پیگیری.
7. نصب Agent از همان `ServerMonitor.install_agent_service` ربات با کنترل واقعی TLS/توکن و Xray.

## نکات عملیاتی

- `SONAR_AGENT_RELAY_PORTS` محدودیت امنیتی پورت SSH در relay است. پورت اختصاصی باید روی **مرکز و Agentها** در allowlist قرار بگیرد؛ نباید برای حل مشکل، کنترل مقصد را حذف کرد.
- Agentها باید TLS-enrolled، سالم و توسط مدیر تأیید شده باشند؛ نصب بدون این پیش‌شرط از مسیر جایگزین انجام نمی‌شود.
- خطاهای `HOST_KEY_CHANGED` و `AUTH_FAILED` اجازه سوئیچ خودکار به مسیر ناامن نمی‌دهند.
- نصب وب فقط پس از راه‌اندازی PostgreSQL و استارت سرویس وب صف را پردازش می‌کند؛ در توقف طولانی سرویس، وضعیت کار باید بررسی شود.
- تعداد فایل‌های هر پوشه در خروجی نهایی کمتر از ۹۰ مورد است. فایل‌های `__pycache__` و اسناد آرشیوی دقیقاً تکراری حذف شده‌اند.
