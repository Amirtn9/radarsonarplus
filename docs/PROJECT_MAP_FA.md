# نقشه پروژه و مسیرهای ارتباط

| بخش | مسیر | مسئولیت |
|---|---|---|
| ربات تلگرام | `bot.py`, `dispatcher.py`, `bot_logic.py`, `bot_logic_mod/` | نقطه ورود، ثبت handlerها، فرمان‌ها و جریان‌های تعاملی |
| منطق مرکزی | `core.py`, `ssh_support.py`, `server_onboarding.py` | SSH مستقیم، تأیید fingerprint و ثبت امن سرور |
| مسیر Agent | `ssh_relay.py`, `monitor_fleet.py`, `ws_client.py`, `monitor_agent.py` | ارتباط WSS/TLS، relay، سلامت و مانیتورینگ Agent |
| نصب Agent | `agent_deploy.py`, `agent_bootstrap.py`, `agent_xray_bridge.py` | نصب، بازیابی، انتقال وابستگی‌ها و اتصال Xray |
| پایگاه داده | `database.py`, `workspace_service.py`, `pro_store.py` | ذخیره‌سازی، فضای کاری، مالکیت و رمزنگاری |
| API وب | `pro_portal.py`, `web_operations.py`, `console_api.py`, `fleet_api.py`, `enterprise_api.py` | ورود، APIهای پنل و عملیات مدیریتی |
| صف عملیات وب | `web_agent_jobs.py`, `service_jobs.py`, `enterprise_runtime.py` | صف‌های ماندگار و پردازش پس‌زمینه |
| رابط وب | `pro_web/index.html`, `pro_web/guide.html`, `pro_web/static/` | پنل، Mini App، مدیریت Agent و بالانسر |
| منابع و کانفیگ | `tunnel_logic.py`, `agent_xray_bridge.py`, `subscription_health.py` | پردازش و بررسی منابع پشتیبانی‌شده |
| وظایف زمان‌بندی‌شده | `cronjobs.py`, `monitor_fleet.py`, `service_monitor.py` | پایش، اعلان و عملیات دوره‌ای |
| نصب و نگهداری | `install.sh`, `install_web_panel.sh`, `scripts/` | نصب، ارتقا، عیب‌یابی، لاگ و بررسی سلامت |
| آزمون‌ها | `tests/` | تست‌های واحد، رگرسیون و قراردادهای نصب |
| مستندات | `docs/`, `docs/operations/`, `docs/history/`, `docs/archive/` | راهنماهای جاری، عملیات، تاریخچه و اسناد قدیمی |
| نقشه ساختار | `PROJECT_STRUCTURE.md`, `scripts/update_project_structure.py` | نقشه خودکار و قابل بازتولید فایل‌ها |

## مسیر افزودن سرور

1. درخواست از پنل وب/Mini App یا ربات تلگرام با IP و پورت SSH ثبت می‌شود.
2. بررسی کلید میزبان ابتدا از کنترل‌کننده مرکزی انجام می‌شود؛ در خطای شبکه، فقط Agentهای سالم و مجاز بررسی می‌شوند.
3. fingerprint مشاهده‌شده برای مقایسه مستقل نمایش داده می‌شود؛ تأیید باید از کنسول یا پنل ارائه‌دهنده مقصد انجام شود.
4. احراز هویت SSH با کلید مورد تأیید انجام می‌شود؛ کلید پیشنهادی قبل از احراز هویت موفق ذخیره نمی‌شود.
5. سرور با کنترل مالکیت و فضای کاری در PostgreSQL ثبت می‌شود.
6. در صورت درخواست نصب Agent، کار به صف ماندگار منتقل و نتیجه مرحله‌ای ثبت می‌شود.
7. Agent فقط پس از بررسی واقعی TLS/WSS، توکن و heartbeat سالم می‌تواند برای مسیر جایگزین واجد شرایط شود.

## سیاست مسیر ارتباط

- مسیر مستقیم کنترل‌کننده اولویت نخست است.
- Relay فقط از Agentهای تأییدشده و سالم استفاده می‌کند.
- خطاهای fingerprint یا احراز هویت SSH نباید به مسیر جایگزین ناامن تبدیل شوند.
- پورت‌های relay باید در allowlist پیکربندی‌شده باقی بمانند؛ کنترل مقصد برای رفع خطای اتصال حذف نمی‌شود.
- جزئیات عملیاتی در `docs/operations/` و نقشه کامل فایل‌ها در `PROJECT_STRUCTURE.md` نگهداری می‌شود.
