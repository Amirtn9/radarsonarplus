"""
Auto-split base module from original bot_logic.py (imports, config, globals)
"""

"""Main Bot Logic for Sonar Radar Ultra Pro. 

This module handles: 
- Command handlers
- Message processing
- User interactions
- Server monitoring callbacks
- Admin operations
"""

import logging
import traceback
import os
import json
import asyncio
import time
import warnings
import threading
import statistics
import io
import html
import re
import base64
import urllib.parse
import shlex
import datetime as dt
import subprocess
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

# ✅ یکبار import logging (در اول فایل)
# --- Third-Party Libraries ---
import jdatetime
from cryptography. fernet import Fernet
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram. error import BadRequest, TelegramError, Conflict, NetworkError
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler, JobQueue
)

# --- Local Modules ---
from states import *
import topics
from database import Database
from tunnel_logic import tunnel_manager
from server_stats import StatsManager
from scoring import ScoreEngine
from core import (
    ServerMonitor, get_jalali_str, generate_plot, format_speed, 
    get_tehran_datetime, extract_safe_json, sec
)
from settings import (
    DB_NAME, CONFIG_FILE, KEY_FILE, AGENT_FILE_PATH, 
    SUBSCRIPTION_PLANS, PAYMENT_INFO, DEFAULT_INTERVAL, 
    DOWN_RETRY_LIMIT, SUPER_ADMIN_ID, DB_CONFIG, AGENT_PORT
)
import keyboard
import admin_panel
import cronjobs

# ✅ یکبار تعریف logger (بعد از imports)
logger = logging.getLogger(__name__)


from telegram_safety import safe_callback_answer


logger.debug("📦 bot_logic.py module loaded")



# ------------------------------------------------------------------------------
# Lazy proxy to main menu (avoids circular imports)
# ------------------------------------------------------------------------------
async def start(update, context):
    """Proxy for bot_logic_mod.menu.start to prevent NameError in split modules."""
    from bot_logic_mod.menu import start as _start
    return await _start(update, context)

# ==============================================================================
# ⚙️ CONCURRENCY SETTINGS (تنظیمات همزمانی و مدیریت فشار)
# ==============================================================================

# تعداد پردازش‌های همزمان مجاز (برای جلوگیری از کرش کردن سرور)
MAX_CONCURRENT_TASKS = 50

# 🔧 نسخه ۴.۲: به جای ساختن استخر جدید، از استخر مشترک پروژه استفاده می‌کنیم.
# قبلاً چهار استخر مجزا وجود داشت و کارها بین‌شان پخش می‌شد؛ نتیجه این بود که
# یک استخر کوچک (پیش‌فرض asyncio) گلوگاه کل ربات می‌شد.
from runtime import SHARED_EXECUTOR, run_sync  # noqa: F401
EXECUTOR = SHARED_EXECUTOR
logger.debug("🔧 Using SHARED_EXECUTOR for sync work")

# ساخت سمافور سراسری (برای صف‌بندی درخواست‌ها وقتی ظرفیت پر است)
GLOBAL_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
logger.debug(f"🔒 Global semaphore initialized with {MAX_CONCURRENT_TASKS} slots")


# ==============================================================================
# 🚀 INITIALIZATION & CONFIGURATION
# ==============================================================================

# ✅ Database instance
db = Database()
logger.info("✅ Database initialized")

# ✅ Filter warnings
warnings.filterwarnings("ignore")
logger.debug("⚠️ Warnings filtered")


def get_agent_content():
    """
    خواندن محتوای فایل ایجنت به صورت داینامیک. 
    
    Returns:
        محتوای فایل ایجنت یا رشته خالی اگر فایل موجود نباشد
    """
    try:
        if os.path.exists(AGENT_FILE_PATH):
            with open(AGENT_FILE_PATH, "r", encoding="utf-8") as f:
                content = f.read()
                logger.debug(f"✅ Agent script loaded ({len(content)} bytes)")
                return content
        else:
            logger.warning(f"⚠️ Agent script not found at:  {AGENT_FILE_PATH}")
            return ""
    except Exception as e:
        logger.error(f"❌ Error loading agent script: {e}", exc_info=True)
        return ""


# ✅ بررسی وجود فایل ایجنت
agent_status = "Found" if get_agent_content() else "Not Found (Will retry later)"
logger.info(f"✅ Agent Script Status: {agent_status}")
print(f"✅ Agent Script Status: {agent_status}")


# ==============================================================================
# ⚙️ DYNAMIC CONFIGURATION
# ==============================================================================

TOKEN = None
try:
    if os. path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            TOKEN = config. get('bot_token', 'Not_Set')
            logger. debug(f"📋 Config loaded from {CONFIG_FILE}")
            
            try:
                admin_id_str = config.get('admin_id', str(SUPER_ADMIN_ID))
                SUPER_ADMIN_ID = int(admin_id_str)
                logger.info(f"👑 Admin ID set to: {SUPER_ADMIN_ID}")
            except ValueError as e:
                logger.warning(f"⚠️ Invalid admin_id in config, using default: {e}")
    else:
        TOKEN = 'TOKEN_NOT_SET'
        logger.error(f"❌ Config file ({CONFIG_FILE}) not found. Please run install. sh")
        print(f"⚠️ Config file ({CONFIG_FILE}) not found. Please run install.sh")
        
except Exception as e:
    logger.error(f"❌ Error loading config: {e}", exc_info=True)
    TOKEN = 'ERROR'


# ==============================================================================
# 📊 GLOBAL STATE & CACHE
# ==============================================================================

# Global cache برای milestone‌های uptime (جلوگیری از تکرار هشدارها)
UPTIME_MILESTONE_TRACKER = set()
logger.debug("📊 Uptime milestone tracker initialized")

# Cache برای جلسات SSH (برای بهبود کارایی)
SSH_SESSION_CACHE = {}
logger.debug("🔐 SSH session cache initialized")

# Track کارهای فعال برای هر کاربر (جلوگیری از همپوشانی)
USER_ACTIVE_TASKS = {}
logger.debug("👥 User active tasks tracker initialized")
# ==============================================================================


def shared_uid(update):
    """Technical workspace data owner; financial profile always uses real actor ID."""
    return db.workspace_owner(update.effective_user.id)

# Explicit compatibility exports; new dependencies are not exported implicitly.
_PUBLIC_EXPORTS = ['ADD_ADMIN_DAYS', 'ADD_ADMIN_ID', 'ADD_PAY_ADDR', 'ADD_PAY_HOLDER', 'ADD_PAY_NET', 'ADD_PAY_TYPE', 'ADMIN_AGENT_PORT_INPUT', 'ADMIN_GET_UID_FOR_REPORT', 'ADMIN_RESTORE_DB', 'ADMIN_RESTORE_KEY', 'ADMIN_SEARCH_USER', 'ADMIN_SET_LIMIT', 'ADMIN_SET_TIME_MANUAL', 'AGENT_FILE_PATH', 'AGENT_PORT', 'ApplicationBuilder', 'BadRequest', 'CONFIG_FILE', 'CONFIRM_EXISTING_SSH_HOST', 'CONFIRM_SSH_HOST', 'CONFIRM_WS_PORT', 'CallbackQueryHandler', 'CommandHandler', 'Conflict', 'ContextTypes', 'ConversationHandler', 'DB_CONFIG', 'DB_NAME', 'DEFAULT_INTERVAL', 'DOWN_RETRY_LIMIT', 'Database', 'EDIT_SERVER_EXPIRY', 'EXECUTOR', 'Fernet', 'GET_BROADCAST_MSG', 'GET_CHANNEL_FORWARD', 'GET_CHANNEL_TYPE', 'GET_CONFIG_LINKS', 'GET_CPU_LIMIT', 'GET_CUSTOM_BIG_INTERVAL', 'GET_CUSTOM_BIG_SIZE', 'GET_CUSTOM_INTERVAL', 'GET_CUSTOM_SMALL_SIZE', 'GET_CUSTOM_WS_PORT', 'GET_DISK_LIMIT', 'GET_EXPIRY', 'GET_GROUP_ID_FOR_TOPICS', 'GET_GROUP_NAME', 'GET_IP', 'GET_IRAN_IP', 'GET_IRAN_NAME', 'GET_IRAN_PASS', 'GET_IRAN_PORT', 'GET_IRAN_USER', 'GET_JSON_CONF', 'GET_LINEAR_DATA', 'GET_LOCATION', 'GET_MANUAL_HOST', 'GET_NAME', 'GET_PASS', 'GET_PORT', 'GET_RAM_LIMIT', 'GET_REBOOT_TIME', 'GET_RECEIPT', 'GET_REMOTE_COMMAND', 'GET_SUB_LINK', 'GET_SUB_NAME', 'GET_USER', 'GLOBAL_SEMAPHORE', 'InlineKeyboardButton', 'InlineKeyboardMarkup', 'InputMediaPhoto', 'JobQueue', 'KEY_FILE', 'MAX_CONCURRENT_TASKS', 'MessageHandler', 'NetworkError', 'PAYMENT_INFO', 'SELECT_ADD_METHOD', 'SELECT_CONFIG_TYPE', 'SELECT_GROUP', 'SHARED_EXECUTOR', 'SSH_SESSION_CACHE', 'SUBSCRIPTION_PLANS', 'SUPER_ADMIN_ID', 'ScoreEngine', 'ServerMonitor', 'StatsManager', 'TOKEN', 'TelegramError', 'ThreadPoolExecutor', 'UPTIME_MILESTONE_TRACKER', 'USER_ACTIVE_TASKS', 'Update', 'admin_id_str', 'admin_panel', 'agent_status', 'asyncio', 'base64', 'config', 'contextmanager', 'cronjobs', 'datetime', 'db', 'dt', 'extract_safe_json', 'filters', 'format_speed', 'generate_plot', 'get_agent_content', 'get_jalali_str', 'get_tehran_datetime', 'html', 'io', 'jdatetime', 'json', 'keyboard', 'logger', 'logging', 'os', 're', 'run_sync', 'safe_callback_answer', 'sec', 'shared_uid', 'shlex', 'start', 'statistics', 'subprocess', 'threading', 'time', 'timedelta', 'timezone', 'topics', 'traceback', 'tunnel_manager', 'urllib', 'warnings']

__all__ = tuple(name for name in _PUBLIC_EXPORTS if name in globals())
