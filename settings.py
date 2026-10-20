import os
import logging
import json

PROJECT_VERSION = "6.5.4"

# ==============================================================================
# 📂 FILE PATHS CONFIGURATION
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# نام فایل‌های حیاتی
# NOTE: پروژه از PostgreSQL استفاده می‌کند. DB_NAME صرفاً برای سازگاری با بخش‌هایی
# مثل بکاپ‌گیری (خروجی pg_dump) نگه داشته شده است.
DB_NAME = "sonar_ultra_pro.db"
CONFIG_FILE = os.path.join(BASE_DIR, "sonar_config.json")
STATE_DIR = os.environ.get("SONAR_STATE_DIR", os.path.join(BASE_DIR, "runtime"))
KEY_FILE = os.path.join(STATE_DIR, "secret.key")
# Manual upgrades keep the legacy key until the installer migrates runtime state.
_legacy_key = os.path.join(BASE_DIR, "secret.key")
if not os.getenv("SONAR_STATE_DIR") and not os.path.exists(KEY_FILE) and os.path.isfile(_legacy_key):
    KEY_FILE = _legacy_key
KNOWN_HOSTS_FILE = os.path.join(STATE_DIR, ".ssh", "known_hosts")
AGENT_FILE_PATH = os.path.join(BASE_DIR, "monitor_agent.py")

# ==============================================================================
# ⚙️ DATABASE CONFIGURATION (PostgreSQL)
# ==============================================================================
DB_CONFIG = {
    'dbname': os.getenv('SONAR_DB_NAME', 'sonar_ultra_pro'),
    'user': os.getenv('SONAR_DB_USER', 'sonar_user'),
    'password': os.environ.get('SONAR_DB_PASSWORD', ''),
    'host': os.getenv('SONAR_DB_HOST', 'localhost'),
    'port': os.getenv('SONAR_DB_PORT', '5432'),
    'connect_timeout': 5,
    'options': '-c timezone=UTC'
}

# ==============================================================================
# 👤 ADMIN & PORT CONFIGURATION (Dynamic)
# ==============================================================================
SUPER_ADMIN_ID = 0
AGENT_PORT = 8080  # پورت پیش‌فرض

try:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            data = json.load(f)
            SUPER_ADMIN_ID = int(data.get('admin_id', 0))
            AGENT_PORT = int(data.get('agent_port', 8080)) # خواندن پورت از فایل
except Exception as e:
    print(f"Error loading settings: {e}")

# ==============================================================================
# 💳 SUBSCRIPTION PLANS
# ==============================================================================
SUBSCRIPTION_PLANS = {
    'bronze': {
        'name': 'برنزی 🥉',
        'limit': 5,
        'days': 30,
        'price': 100000,
        'desc': 'مناسب برای استفاده شخصی'
    },
    'silver': {
        'name': 'نقره‌ای 🥈',
        'limit': 10,
        'days': 30,
        'price': 180000,
        'desc': 'مناسب برای تیم‌های کوچک'
    },
    'gold': {
        'name': 'طلایی 🥇',
        'limit': 15,
        'days': 30,
        'price': 240000,
        'desc': 'حرفه‌ای، حداکثر ۱۵ سرور'
    }
}

# ==============================================================================
# 💰 PAYMENT INFO DEFAULT
# ==============================================================================
PAYMENT_INFO = {
    'card': {
        'number': os.getenv('SONAR_PAYMENT_CARD_NUMBER', ''),
        'name': os.getenv('SONAR_PAYMENT_CARD_HOLDER', '')
    },
    'tron': {
        'address': os.getenv('SONAR_PAYMENT_TRC20_ADDRESS', ''),
        'network': 'TRC20'
    }
}

# ==============================================================================
# 📟 MONITORING CONFIG
# ==============================================================================
DEFAULT_INTERVAL = 120
DOWN_RETRY_LIMIT = 3

# ============================================================================== 
# 🔌 WEBSOCKET (AGENT) STABILITY SETTINGS
# ============================================================================== 
# این مقادیر به صورت پیش‌فرض، ارتباط وب‌سوکت را "همیشه زنده" نگه می‌دارند.
# در صورت نیاز می‌توانید آن‌ها را در sonar_config.json یا Env تغییر دهید.

# 🔧 نسخه ۴.۲: همه‌ی کاربرها به یک نود مانیتورینگ وصل می‌شوند، یعنی یک
# کلید استخر مشترک. با سقف ۵، نفر ششم ۳۰ ثانیه صبر می‌کرد و خطا می‌گرفت.
WS_POOL_MAX_PER_KEY = int(os.getenv("SONAR_WS_POOL_MAX", "10"))
WS_OPEN_TIMEOUT = float(os.getenv("SONAR_WS_OPEN_TIMEOUT", "6"))
WS_CLOSE_TIMEOUT = float(os.getenv("SONAR_WS_CLOSE_TIMEOUT", "6"))
WS_PING_INTERVAL = float(os.getenv("SONAR_WS_PING_INTERVAL", "30"))
# 🔧 v4.3: زیر بار سنگین ایجنت دیر به ping جواب می‌دهد؛ با ۲۰ ثانیه
# همه‌ی کانکشن‌ها با هم بسته می‌شدند و چرخه‌ی reconnect راه می‌افتاد.
WS_PING_TIMEOUT = float(os.getenv("SONAR_WS_PING_TIMEOUT", "90"))
WS_ACQUIRE_TIMEOUT = float(os.getenv("SONAR_WS_ACQUIRE_TIMEOUT", "45"))

# حداکثر پیام ۴ MiB برای کاهش ریسک مصرف حافظه بی‌رویه.
WS_MAX_MESSAGE_SIZE = 4 * 1024 * 1024

try:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            _data = json.load(f)
            WS_POOL_MAX_PER_KEY = int(_data.get('ws_pool_max', WS_POOL_MAX_PER_KEY))
            WS_PING_INTERVAL = float(_data.get('ws_ping_interval', WS_PING_INTERVAL))
            WS_PING_TIMEOUT = float(_data.get('ws_ping_timeout', WS_PING_TIMEOUT))
except Exception:
    # اگر فایل کانفیگ خراب بود یا نبود، با مقادیر پیش‌فرض ادامه می‌دهیم.
    pass

# ==============================================================================
# 📝 LOGGING CONFIG
# ==============================================================================
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
LOG_LEVEL = logging.INFO