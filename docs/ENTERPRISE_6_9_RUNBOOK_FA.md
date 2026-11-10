# راهنمای عملیات سازمانی RadarSonarPlus 6.9.9

## اجزای فعال‌شده

- Worker عملیات PostgreSQL با `FOR UPDATE SKIP LOCKED`، lease، idempotency، شمارنده تلاش و نتیجه نهایی.
- عملیات Diagnostic از مسیر Agent موجود؛ Restart فقط برای `sonar-agent`، `nginx`، `docker` و `ssh`.
- Agent update از تابع deploy موجود استفاده می‌کند؛ rollback داخلی نصب‌کننده برای هر سرور حفظ شده است.
- Backup رمزنگاری‌شده PostgreSQL با Fernet، ذخیره محلی یا S3-compatible، نگهداری نسخه‌ها و CLI بازیابی.
- API Token با SHA-256، انقضا/لغو، Scope و سقف ۱۲۰ درخواست در دقیقه.
- Webhook HTTPS با HMAC-SHA256، جلوگیری از مقصد خصوصی، صف پایدار و backoff تا ۸ تلاش.
- Auto Recovery محدود به allowlist؛ فشار دیسک فقط اعلان می‌دهد و Restart سرویس مدیریت‌شده فقط برای رویدادهای تعریف‌شده.
- Agent rollout با canary و batch از سورس Checkout فعلی و کنترل SHA-256.
- گزارش‌های رخداد، عملیات و مصرف منابع در API و پنل وب.
- داشبورد مدیر کل در ربات برای وضعیت عملیات و فعال/متوقف‌کردن قوانین اتوماسیون.

## متغیرهای محیطی

نمونه ساخت کلیدهای رمزنگاری:

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print("SONAR_BACKUP_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
print("SONAR_WEBHOOK_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
PY
```

این کلیدها را در Secret Manager یا فایل محیطی با مجوز `0600` قرار دهید. کلیدها را داخل Git، ZIP یا `sonar_config.json` commit نکنید.

برای S3-compatible:

```bash
SONAR_BACKUP_S3_BUCKET=your-private-bucket
SONAR_BACKUP_S3_REGION=eu-central-1
SONAR_BACKUP_S3_ENDPOINT=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

در AWS ترجیحاً IAM Role به‌جای Access Key ثابت استفاده شود. برای S3-compatible غیر AWS، endpoint را تنظیم کنید. مقصد S3 باید با `SONAR_BACKUP_S3_BUCKET` تطبیق داشته باشد.

برای Backup محلی:

```bash
SONAR_BACKUP_DIR=/var/backups/radarsonarplus
```

## اجرای Worker

Workerها با Startup ربات و FastAPI بالا می‌آیند و از قفل ردیفی PostgreSQL استفاده می‌کنند؛ بنابراین اجرای هم‌زمان چند نمونه برای عملیات و Webhook مجاز است. برای جلوگیری از دو نوبت Backup هم‌زمان، از یک Worker زمان‌بندی Backup در هر محیط استفاده کنید.

## CLI

```bash
python enterprise_cli.py backup manual.dump.enc
python enterprise_cli.py restore manual.dump.enc --confirm
```

Restore مخرب است و اشیای دیتابیس موجود را پاک/جایگزین می‌کند. ابتدا روی Staging اجرا کنید. قبل از Restore، سرویس‌های ربات و وب را متوقف و Backup جداگانه بگیرید.

## API Token

توکن از `POST /api/enterprise/tokens` ساخته می‌شود و فقط Hash آن ذخیره می‌شود. مقدار خام فقط یک بار برگردانده می‌شود. از `Authorization: Bearer rs_...` استفاده کنید. Scopeهای پشتیبانی‌شده:
`servers:read`, `incidents:read`, `incidents:write`, `metrics:read`, `operations:read`, `operations:write`, `workspace:admin`, `backups:write`, `automation:write`, `webhooks:write`, `tokens:write`, `analytics:read`, `agents:read`, `agents:write`.

سقف پیش‌فرض ۱۲۰ درخواست در دقیقه برای هر توکن است.

## Webhook

امضای درخواست در هدر `X-Radar-Signature: sha256=<hex>` قرار می‌گیرد و با HMAC-SHA256 روی بایت‌های Body محاسبه می‌شود. مصرف‌کننده باید امضا را با مقایسه constant-time بررسی کند. URL باید HTTPS و DNS عمومی باشد؛ redirect دنبال نمی‌شود.

## محدوده‌ها و محدودیت‌های شناخته‌شده

- Backup کامل PostgreSQL به‌دلیل دربرگرفتن داده تمام Tenantها فقط برای `SUPER_ADMIN_ID` فعال است؛ Export مستقل هر Workspace هنوز پیاده‌سازی نشده است.
- مقصد خارجی فعلاً S3-compatible است؛ FTP/SFTP و Google Drive مقصد Backup نیستند.
- Agent rollout سورس Agent موجود در Checkout را با SHA-256 ثبت‌شده نصب می‌کند. دریافت Artifact امضاشده از Registry خارجی و rollback گروهی به Release قبلی هنوز فعال نشده است. نصب ناموفق هر Agent از rollback داخلی `agent_deploy` استفاده می‌کند.
- Auto Recovery برای disk pressure فقط اعلان تولید می‌کند. اجرای فرمان آزاد یا Restart دلخواه پشتیبانی نمی‌شود.
- برای تأیید Production، تست واقعی PostgreSQL، S3، SSH، WSS/TLS، Telegram و Staging لازم است.


## تکمیل عملیات نسخه 6.9 (Patch تکمیلی)

### Worker عملیات
- آیتم‌های `running` با Lease منقضی‌شده دوباره Claim می‌شوند؛ Jobی که Worker آن Crash کرده در حالت معلق نمی‌ماند.
- عملیات از API قابلیت `pause`, `resume`, `cancel`, `retry` دارد.
- لغو عملیات در حال اجرا ابتدا درخواست لغو ثبت می‌کند؛ فرمان SSH در حال اجرا قابل قطع اجباری نیست، اما آیتم‌های صف‌شده اجرا نمی‌شوند.
- جزئیات هر Operation شامل وضعیت هر سرور و تلاش‌ها از API قابل دریافت است.

### سیاست Health
سیاست Health برای هر Workspace در PostgreSQL ذخیره می‌شود. وزن‌ها باید مجموعاً 100 باشند:
`agent`, `cpu`, `ram`, `disk`.
آستانه‌های CPU/RAM/Disk و بازه کهنگی داده قابل تنظیم هستند. داده کهنه همچنان Unknown است و امتیاز سالم دریافت نمی‌کند.

### تاریخچه منابع
Agent اکنون `bytes_sent`, `bytes_recv`, `traffic_gb` و `uptime_sec` را در کنار CPU/RAM/Disk گزارش می‌کند. جدول `server_stats` ستون‌های افزوده را با Migration خودکار می‌گیرد و تا 30 روز داده نگه می‌دارد. API:
`GET /api/enterprise/servers/{server_id}/metrics?days=7`

### مدیریت Backup
- `GET /api/enterprise/backups/schedules`
- `GET /api/enterprise/backups/schedules/{id}/runs`
- `POST /api/enterprise/backups/schedules/{id}/control` با actionهای `enable`, `disable`, `run_now`, `delete`.
- حذف Schedule فایل‌های Backup ثبت‌شده آن Schedule را نیز از مقصد محلی/S3 پاک می‌کند.
- Backup کامل همچنان Platform-admin-only است؛ این Patch آن را به Backup مستقل Tenant تبدیل نمی‌کند.

### مدیریت Webhook و Token
- فهرست، ویرایش فعال/غیرفعال، چرخش Secret و حذف Webhook.
- تاریخچه تحویل، Dead Letter و Retry دستی.
- فهرست Tokenها و لغو Token از API.
- هنگام ساخت Token می‌توان `expires_in_days` بین 1 تا 3650 تعیین کرد.
- صف تحویل Webhook دارای Lease است تا چند Worker یک Delivery را هم‌زمان ارسال نکنند.

### Artifact امضاشده Agent (Ed25519)
برای فعال‌سازی انتشار امضاشده، کلید خصوصی را خارج از سرور Backend و در محیط امن نگه دارید. روی ماشین امن انتشار، کلید بسازید:

```bash
python - <<'PY'
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print("PRIVATE_KEY_B64=" + base64.b64encode(private).decode())
print("PUBLIC_KEY_B64=" + base64.b64encode(public).decode())
PY
```

کلید خصوصی را فقط در سیستم امضاکننده نگه دارید. کلید عمومی Base64 را در Secret Manager سرور Backend با نام `SONAR_AGENT_RELEASE_PUBLIC_KEY` قرار دهید.

امضای فایل Agent:

```bash
python - <<'PY'
import base64, os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["SONAR_AGENT_RELEASE_PRIVATE_KEY"]))
artifact = Path("monitor_agent.py").read_bytes()
print("SHA256=" + __import__("hashlib").sha256(artifact).hexdigest())
print("SIGNATURE=" + base64.b64encode(private.sign(artifact)).decode())
PY
```

در پنل Enterprise فایل دقیق `monitor_agent.py`، نسخه داخل `AGENT_VERSION`، SHA-256 و Signature را ثبت کنید. Backend امضا را پیش از ذخیره بررسی می‌کند و Worker پیش از هر نصب دوباره Hash و Signature را اعتبارسنجی می‌کند. Artifact در PostgreSQL ذخیره می‌شود تا URL خارجی یا تغییر فایل روی میزبان انتشار، محتوای Rollout را عوض نکند.

API ثبت Release به این فیلدها نیاز دارد:
`version`, `sha256`, `artifact_base64`, `signature`, `channel`.

### Agent Rollout
- Pause/Resume/Cancel از پنل و API.
- Rollback گروهی به آخرین Release امضاشده قبلی در همان کانال، برای سرورهای موفق در Batchهای قبلی و سرورهای موفق Batch شکست‌خورده.
- اگر Release قبلی امضاشده ثبت نشده باشد، Rollback گروهی رد می‌شود.
- Rollback نیز از مسیر Agent deploy موجود عبور می‌کند و در صورت شکست نصب، Rollback محلی نصب‌کننده اجرا می‌شود.

### APIهای مدیریتی اضافه‌شده
- `GET /api/enterprise/operations/{id}`
- `POST /api/enterprise/operations/{id}/control`
- `GET /api/enterprise/incidents` و `GET /api/enterprise/incidents/{id}`
- `GET/PUT /api/enterprise/health/policy`
- `GET /api/enterprise/tokens`
- `DELETE /api/enterprise/tokens/{id}`
- `GET /api/enterprise/webhooks`
- `PUT/DELETE /api/enterprise/webhooks/{id}`
- `GET /api/enterprise/webhooks/{id}/deliveries`
- `POST /api/enterprise/webhooks/{id}/retry`
- `POST /api/enterprise/agent/rollouts/{id}/control`

### محدودیت‌های باقیمانده
این Patch موارد مدیریتی و اجرایی Enterprise را گسترش می‌دهد؛ اما Multi-Workspace واقعی (چند Workspace مستقل برای یک حساب)، صورتحساب/درگاه پرداخت کامل، SLA/MTBF کامل، خروجی PDF/CSV تحلیلی و تست زیرساخت واقعی در VPS/Staging هنوز نیازمند فاز مستقل هستند. Shared workspace فعلی جایگزین Multi-Tenancy کامل نیست.
