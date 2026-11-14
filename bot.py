import os
import logging
import json
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

# 🔴 لاگینگ باید همین اول، قبل از import هر ماژول دیگه‌ی پروژه، یک‌بار
# و فقط یک‌بار تنظیم بشه. قبلاً هم اینجا و هم داخل dispatcher.py صدا زده
# می‌شد (چون import شدن dispatcher قبل از این فراخوانی اتفاق می‌افتاد)
# که باعث دو مسیر لاگ موازی و گیج‌کننده می‌شد. الان فقط همین‌جاست.
from logging_config import setup_advanced_logging
setup_advanced_logging(
    log_dir=os.getenv("SONAR_LOG_DIR", "/var/log/sonar"),
    level=getattr(logging, os.getenv("SONAR_LOG_LEVEL", "INFO").upper(), logging.INFO),
    console_level=logging.INFO
)

from telegram.ext import ApplicationBuilder
from update_processor import OrderedUserProcessor
from logger_setup import setup_logger
from dispatcher import register_all_handlers
import cronjobs
import bot_logic  # برای دسترسی به ارور هندلر و توکن

# راه‌اندازی هوک‌های کرش (crash hooks) - این تابع دیگه هندلر جدید نمی‌سازه،
# فقط excepthook و سطح لاگ کتابخانه‌های شلوغ (telegram/httpx/websockets) رو تنظیم می‌کنه
logger = setup_logger()

# خواندن تنظیمات (مسیر مطلق - مستقل از CWD هنگام اجرا با systemd)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'sonar_config.json')
try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
        TOKEN = config.get('bot_token')
except Exception as e:
    logger.critical(f"❌ خواندن فایل کانفیگ ناموفق بود / Failed to read config file ({CONFIG_PATH}): {e}", exc_info=True)
    TOKEN = None

def _readiness_file():
    from pathlib import Path
    state_dir = os.getenv("SONAR_STATE_DIR", os.path.join(BASE_DIR, "runtime"))
    return Path(os.getenv("SONAR_READY_FILE", os.path.join(state_dir, "bot-ready")))


def main():
    ready_file = _readiness_file()
    try:
        ready_file.unlink(missing_ok=True)
    except OSError:
        logger.exception("Cannot clear stale bot readiness marker: %s", ready_file)
        raise

    if not isinstance(TOKEN, str) or not TOKEN.strip():
        logger.critical("Telegram bot token is missing. Configure bot_token in sonar_config.json.")
        raise SystemExit(2)

    import re
    if not re.fullmatch(r"[0-9]{6,15}:[A-Za-z0-9_-]{20,}", TOKEN.strip()):
        logger.critical("Telegram bot token has an invalid format; refusing to start.")
        raise SystemExit(2)

    version_path = os.path.join(BASE_DIR, "VERSION")
    try:
        with open(version_path, encoding="utf-8") as version_file:
            version = version_file.read().strip()
    except OSError:
        version = "unknown"
    logger.info("Starting Sonar Radar Pro %s", version)
    logger.info("BOT_BOOTSTRAP_BEGIN python=%s", __import__("sys").version.split()[0])

    async def _startup_step(name, callback, *args):
        logger.info("STARTUP_STAGE_BEGIN stage=%s", name)
        try:
            result = await asyncio.to_thread(callback, *args)
        except Exception:
            logger.exception("STARTUP_STAGE_FAILED stage=%s", name)
            raise
        logger.info("STARTUP_STAGE_OK stage=%s", name)
        return result

    async def _wait_for_database(db):
        """Retry the actual SQL connection before any dependent migrations."""
        attempts = max(1, min(12, int(os.getenv("SONAR_DB_STARTUP_ATTEMPTS", "6"))))
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.to_thread(db.execute, "SELECT 1 AS ready", None, "one")
                if not result or int(result.get("ready", 0)) != 1:
                    raise RuntimeError("PostgreSQL health query returned an unexpected result")
                logger.info("PostgreSQL readiness verified (attempt %s/%s)", attempt, attempts)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "PostgreSQL not ready (attempt %s/%s): %s",
                    attempt, attempts, type(exc).__name__, exc_info=True
                )
                if attempt < attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(
            f"PostgreSQL did not become ready after {attempts} attempts"
        ) from last_error

    async def _post_init(application):
        # Use the same bounded executor for run_sync() and asyncio.to_thread().
        import runtime
        asyncio.get_running_loop().set_default_executor(runtime.SHARED_EXECUTOR)
        runtime.start_watchdog(threshold=1.0)

        from database import Database
        db = Database()
        await _wait_for_database(db)

        # Bot and Web share one versioned migration ledger and PostgreSQL
        # advisory lock. Their independent systemd startup order is irrelevant.
        from schema_migrations import run_startup_migrations
        await _startup_step("schema_migrations", run_startup_migrations, db)
        from log_management import attach_logging
        from settings import SUPER_ADMIN_ID
        application.bot_data['database_logs']=attach_logging(db,SUPER_ADMIN_ID)

        from enterprise_runtime import (
            operations_worker, webhook_worker, recovery_worker,
            scheduled_backup_worker, rollout_worker
        )

        stop = asyncio.Event()
        application.bot_data["enterprise_stop"] = stop
        application.bot_data["enterprise_tasks"] = [
            asyncio.create_task(operations_worker(db, stop), name="enterprise-operations"),
            asyncio.create_task(webhook_worker(db, stop), name="enterprise-webhooks"),
            asyncio.create_task(recovery_worker(db, stop), name="enterprise-recovery"),
            asyncio.create_task(rollout_worker(db, stop), name="enterprise-agent-rollouts"),
            asyncio.create_task(
                asyncio.to_thread(scheduled_backup_worker, db, stop),
                name="enterprise-backups"
            ),
        ]
        # The installer waits for this marker instead of guessing readiness
        # from systemd's ActiveState (Type=simple becomes active before Python
        # has validated Telegram credentials or completed database migrations).
        ready_file = _readiness_file()
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = ready_file.with_name(ready_file.name + f".{os.getpid()}.tmp")
        temporary.write_text(dt.datetime.now(dt.timezone.utc).isoformat() + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, ready_file)
        logger.info("All startup migrations completed; enterprise workers started.")
        logger.info("BOT_STARTUP_READY marker=%s", ready_file)

    async def _post_stop(application):
        # PTB invokes this before closing its HTTP clients. Infinite workers
        # must not be PTB-owned tasks: Application.stop waits for those tasks.
        from runtime import stop_service_workers
        await stop_service_workers(application)

    async def _post_shutdown(application):
        handler=application.bot_data.pop('database_logs',None)
        if handler:
            from log_management import detach_logging
            await asyncio.to_thread(detach_logging,handler)
        try:
            from ws_client import GLOBAL_WS_POOL
            await GLOBAL_WS_POOL.close_all()
        except Exception:
            logger.exception("WebSocket pool shutdown failed")
        try:
            import runtime
            runtime.shutdown()
        except Exception:
            logger.exception("Shared executor shutdown failed")

    logger.info("Building Telegram application")
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .pool_timeout(30.0)
        .connection_pool_size(256)
        .concurrent_updates(OrderedUserProcessor(16))  # Same-user updates remain strictly ordered
        .post_init(_post_init)
        .post_stop(_post_stop)
        .post_shutdown(_post_shutdown)
        .build()
    )
    
    logger.info("Telegram application constructed; registering handlers")
    # اضافه کردن ارور هندلر از فایل لاجیک
    app.add_error_handler(bot_logic.error_handler)

    # ✅ ثبت تمام هندلرها از فایل دیسپچر
    # این خط حیاتی است: تمام دکمه‌ها و دستورات اینجا لود می‌شوند
    register_all_handlers(app)
    from service_bot import register as register_service_handlers
    register_service_handlers(app)
    from config_alert_bot import register as register_config_alerts
    register_config_alerts(app)
    from fleet_bot import register as register_fleet
    register_fleet(app)

    # راه‌اندازی جاب‌ها (Job Queue)
    if app.job_queue:
        from billing_delivery import bot_job as billing_message_job
        app.job_queue.run_repeating(billing_message_job,interval=15,first=20,job_kwargs={'max_instances':1,'coalesce':True})
        app.job_queue.run_once(cronjobs.system_startup_notification, when=2)
        app.job_queue.run_once(cronjobs.startup_whitelist_job, when=15)
        app.job_queue.run_once(cronjobs.send_startup_topic_test, when=10)
        app.job_queue.run_daily(cronjobs.check_expiry_job, time=dt.time(hour=8, minute=30, second=0, tzinfo=ZoneInfo('Asia/Tehran')))
        from workspace_service import workspace_alert_job
        app.job_queue.run_repeating(workspace_alert_job, interval=10, first=15,
                                    job_kwargs={'max_instances':1, 'coalesce':True})
        app.job_queue.run_repeating(cronjobs.auto_scheduler_job, interval=120, first=30)
        app.job_queue.run_repeating(cronjobs.global_monitor_job, interval=60, first=10, job_kwargs={'max_instances':1,'coalesce':True})
        app.job_queue.run_repeating(cronjobs.server_report_job,interval=60,first=45,job_kwargs={'max_instances':1,'coalesce':True})
        app.job_queue.run_repeating(cronjobs.monitor_tunnels_job, interval=60, first=20)
        app.job_queue.run_repeating(cronjobs.auto_update_subs_job, interval=43200, first=3600)
        app.job_queue.run_repeating(cronjobs.auto_backup_send_job, interval=3600, first=300)
        app.job_queue.run_repeating(cronjobs.check_bonus_expiry_job, interval=43200, first=600)
        from pro_jobs import multi_probe_job, digest_job, cleanup_job
        from config_alerts import config_alert_job, scheduled_config_probe_job
        app.job_queue.run_repeating(config_alert_job, interval=15, first=20,
                                    job_kwargs={'max_instances':1,'coalesce':True})
        app.job_queue.run_repeating(scheduled_config_probe_job, interval=30, first=40,
                                    job_kwargs={'max_instances':1,'coalesce':True})
        app.job_queue.run_repeating(multi_probe_job, interval=1800, first=300,
                                    job_kwargs={'max_instances': 1, 'coalesce': True})
        app.job_queue.run_repeating(digest_job, interval=3600, first=360,
                                    job_kwargs={'max_instances': 1, 'coalesce': True})
        app.job_queue.run_daily(cleanup_job, time=dt.time(hour=3, minute=0, tzinfo=ZoneInfo('Asia/Tehran')))
        from monitor_fleet import fleet_heartbeat_job, fleet_install_job
        app.job_queue.run_repeating(fleet_install_job, interval=35, first=50,
                                    job_kwargs={'max_instances':1,'coalesce':True})
        app.job_queue.run_repeating(fleet_heartbeat_job, interval=90, first=45,
                                    job_kwargs={'max_instances':1,'coalesce':True})
        from service_jobs import service_check_job
        app.job_queue.run_repeating(service_check_job, interval=30, first=35,
                                    job_kwargs={'max_instances':1,'coalesce':True})
    else:
        logger.critical("PTB_JOB_QUEUE_UNAVAILABLE: install python-telegram-bot[job-queue].")
        raise RuntimeError("PTB_JOB_QUEUE_UNAVAILABLE")

    try:
        from runtime import bot_instance_lock
        from database import Database
        with bot_instance_lock(Database(),TOKEN):
            app.run_polling(
                drop_pending_updates=os.getenv("SONAR_DROP_PENDING_UPDATES", "1") == "1",
                close_loop=False,
                allowed_updates=None,
            )
    except Exception:
        logger.exception("BOT_PROCESS_EXITED_WITH_EXCEPTION")
        raise


if __name__ == "__main__":
    main()
