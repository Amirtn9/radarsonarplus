# ساختار و معماری Sonar Radar Pro

## ماژول‌های اصلی

- `bot.py`, `dispatcher.py`, `bot_logic.py`, `bot_logic_mod/`: اجرای ربات تلگرام و ثبت handlerها.
- `pro_portal.py`: API پنل وب و Mini App، نشست، نقش‌ها و عملیات وب.
- `assets/`: نسخه‌های لوگو/نشان برای مستندات؛ نسخه‌های runtime برای حفظ URL در `pro_web/static/` باقی می‌مانند.
- `pro_web/index.html`: پوسته پنل و مسیر بارگذاری فایل‌های رابط.
- `pro_web/static/app.js`: ناوبری، داشبورد، فهرست منابع و مدیریت نشست.
- `pro_web/static/web_ops.js`: عملیات سرور، بررسی SSH و افزودن مرحله‌ای.
- `pro_web/static/next.js`: تجربه موبایل، نمایش جزئیات، استخر Agent، صف نصب و مصرف ترافیک.
- `pro_web/static/balancer.js`: مدیریت Agentهای relay، سیاست مسیر و اولویت.
- `pro_web/static/enterprise.js`: عملیات سازمانی، گزارش‌ها، Backup، Webhook و Rollout.
- `pro_web/static/console.js`, `ssh-terminal.js`, `subscriptions.js`: جزئیات کنسول، ترمینال SSH و اشتراک‌ها.
- `core.py`, `ssh_support.py`, `server_onboarding.py`: SSH، تأیید fingerprint و ثبت سرور.
- `ssh_relay.py`, `monitor_fleet.py`, `ws_client.py`: relay، سلامت و ارتباط WSS/TLS.
- `agent_deploy.py`, `agent_bootstrap.py`, `agent_xray_bridge.py`, `monitor_agent.py`: نصب و اجرای Agent.
- `database.py`, `workspace_service.py`, `*_api.py`: ذخیره‌سازی، فضای کاری و APIها.
- `install.sh`, `install_web_panel.sh`, `scripts/`: نصب، ارتقا، بازیابی و ابزارهای عملیاتی.
- `tests/`: آزمون‌های واحد، رگرسیون و بررسی قراردادهای انتشار.
- `docs/operations/`: راهنمای عملیات نسخه جاری؛ `docs/history/` و `docs/archive/` مستندات تاریخی.

## نقشه کامل و به‌روزرسانی

فایل `PROJECT_STRUCTURE.md` فهرست پوشه‌ها و فایل‌های مخزن را نشان می‌دهد. آن را پس از تغییر ساختار با دستور زیر بازتولید کنید:

```bash
python3 scripts/update_project_structure.py
```

کش‌های Python، محیط‌های مجازی، لاگ‌ها، runtime و پوشه‌های موقت در نقشه و بسته انتشار وارد نمی‌شوند.

## محدودیت اندازه پوشه‌ها

هر پوشه باید حداکثر ۱۰۰ فایل مستقیم داشته باشد. این شرط در تست‌های پاک‌سازی انتشار بررسی می‌شود. مستندات تاریخی تا زمانی که محتوای یکتا یا ارزش ارجاعی دارند حفظ می‌شوند؛ فقط نسخه‌های تکراریِ تأییدشده حذف شده‌اند.
