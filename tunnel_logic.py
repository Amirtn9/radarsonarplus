"""
tunnel_logic.py — نسخه ۴.۲

تغییرات نسبت به ۴.۱ (همه مربوط به رفع «قفل شدن ربات برای بقیه کاربرها»):

1) تست کانفیگ از روی WebSocket انجام می‌شود، نه SSH.
   در ۴.۱ تابع `_exec_via_ws` اسمش وب‌سوکت بود ولی در عمل SSH می‌زد و به ازای
   هر کانفیگ یک `python3 monitor_agent.py` جدید روی سرور ایران بالا می‌آورد.

2) صف عادلانه (fair queue):
   قبلاً یک `Semaphore(5)` سراسری بین همه‌ی کاربرها مشترک بود؛ اگر کاربر A یک
   ساب ۲۰۰ تایی تست می‌کرد، کاربر B باید تا آخرش صبر می‌کرد.
   حالا: سقف سراسری + سقف جداگانه برای هر کاربر.

3) دیتابیس دیگر روی event loop اجرا نمی‌شود.
   قبلاً `process_sub_async` به ازای هر خط خروجی یک INSERT جداگانه می‌زد،
   آن هم مستقیم روی لوپ. برای یک ساب ۳۰۰ تایی یعنی ۳۰۰ بار قفل شدن کل ربات.
   حالا: پارس کامل → یک batch insert → داخل ترد جداگانه.
"""

import logging
import json
import asyncio
import html
import re
from datetime import datetime

from psycopg2.extras import execute_values

# --- Telegram Imports ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

# --- Local Modules ---
import keyboard
from database import Database
from core import extract_safe_json, sec, ServerMonitor
from runtime import run_sync
from settings import AGENT_PORT

logger = logging.getLogger(__name__)


# ==============================================================================
# ⚙️ سقف‌های همزمانی
# ==============================================================================
# 🔧 نسخه ۴.۳: این اعداد باید با ظرفیت واقعی نود ایران هم‌خوان باشند.
# فرستادن ۲۰ تست همزمان به ایجنتی که فقط ۳ تا را موازی اجرا می‌کند فایده‌ای
# ندارد؛ فقط ۱۷ درخواست معطل می‌ماند و تایم‌اوت می‌خورد.
import os as _os

GLOBAL_TEST_LIMIT = int(_os.getenv("SONAR_GLOBAL_TEST_LIMIT", "8"))
# سقف تست همزمان برای هر کاربر (تا یک نفر کل صف را نبلعد)
PER_USER_TEST_LIMIT = int(_os.getenv("SONAR_USER_TEST_LIMIT", "2"))


class TunnelLogic:
    def __init__(self):
        self.db = Database()
        self.global_sem = asyncio.Semaphore(GLOBAL_TEST_LIMIT)
        self._user_sems: dict = {}

    # ------------------------------------------------------------------
    def _user_sem(self, uid) -> asyncio.Semaphore:
        """سمافور اختصاصی هر کاربر (صف عادلانه)."""
        key = int(uid or 0)
        sem = self._user_sems.get(key)
        if sem is None:
            sem = asyncio.Semaphore(PER_USER_TEST_LIMIT)
            self._user_sems[key] = sem
        return sem

    def decrypt(self, txt: str) -> str:
        return sec.decrypt(txt)

    # ==================================================================
    # 🔌 AGENT HELPERS
    # ==================================================================
    def _agent_creds(self, monitor: dict):
        """(ip, ws_port, token) نود مانیتورینگ."""
        ip = monitor.get('ip')
        ws_port = monitor.get('ws_port') or AGENT_PORT
        # core.ServerMonitor loads a dedicated agent token from the database.
        # Never decrypt or reuse the SSH password as a WebSocket credential.
        return ip, int(ws_port), None

    async def _test_via_ws(self, monitor: dict, link: str, size: float = 5.0, timeout: int = 60):
        """تست یک کانفیگ روی کانکشن وب‌سوکت پایدار ایجنت."""
        ip, ws_port, token = self._agent_creds(monitor)
        return await ServerMonitor.ws_test_config(ip, ws_port, token, link, size=size, timeout=timeout)

    async def _fetch_sub_links(self, monitor: dict, sub_link: str, timeout: int = 60):
        """دریافت لیست کانفیگ‌های داخل یک لینک اشتراک از طریق ایجنت."""
        ip, ws_port, token = self._agent_creds(monitor)
        res = await ServerMonitor.ws_send_command(
            ip, ws_port, token,
            {"action": "fetch_sub", "link": sub_link},
            timeout=timeout,
        )

        if not isinstance(res, dict):
            return False, 'AGENT_INVALID_RESPONSE: پاسخ ایجنت معتبر نیست', None
        if res.get('error'):
            return False, 'SUB_FETCH_' + str(res['error'])[:120], None
        if 'links' not in res or not isinstance(res['links'], list):
            return False, 'AGENT_FETCH_UNSUPPORTED: نسخه ایجنت دریافت ساب را پشتیبانی نمی‌کند', None
        links = res['links']
        if not links:
            return False, 'SUB_EMPTY_OR_INVALID: اشتراک خالی یا فاقد کانفیگ قابل‌خواندن است؛ داده قبلی حفظ شد', None
        return True, links, res.get('sub_info') or {}

    async def _fetch_sub_links_cli(self, monitor, sub_link, timeout=120):
        """Legacy WebSocket shell fallback was intentionally removed for security."""
        return False, "The agent must support fetch_sub; reinstall the current agent", None

    # ==================================================================
    # 💾 DATABASE (همه خارج از event loop)
    # ==================================================================
    def _bulk_save_sub_items(self, uid, sub_id, rows, *, allow_delete=False):
        """Synchronize by SOURCE membership, never by test results.

        A Failed/Unknown probe is still a member. Destructive pruning is only
        permitted for a fully received, valid, non-truncated subscription.
        The caller must never set allow_delete for untrusted partial fetches.
        """
        if not rows:
            return False
        # Be defensive: duplicate URL rows must not make UPDATE nondeterministic.
        rows = list({row[3]: row for row in rows}.values())
        with self.db.get_connection() as (conn, cur):
            cur.execute("SELECT id FROM tunnel_configs WHERE id=%s AND owner_id=%s AND type='sub_source' FOR UPDATE",
                        (sub_id, uid))
            if not cur.fetchone():
                return False
            cur.execute("SELECT link FROM tunnel_configs WHERE owner_id=%s AND parent_sub_id=%s AND type='sub_item' FOR UPDATE",
                        (uid, sub_id))
            previous = {r['link'] if isinstance(r, dict) else r[0] for r in cur.fetchall()}
            incoming = {r[3] for r in rows}
            common = [r for r in rows if r[3] in previous]
            fresh = [r for r in rows if r[3] not in previous]
            for r in common:
                # Unknown means no test, not a measured regression. Keep the
                # previous metrics and score; record Unknown as current status.
                if r[7] == 'Unknown':
                    cur.execute("""UPDATE tunnel_configs SET name=%s, last_status='Unknown'
                                   WHERE owner_id=%s AND parent_sub_id=%s AND type='sub_item' AND link=%s""",
                                (r[4],uid,sub_id,r[3]))
                else:
                    cur.execute("""UPDATE tunnel_configs SET name=%s, quality_score=%s,
                                   last_status=%s, last_ping=%s, last_jitter=%s,
                                   last_speed_down=%s, last_speed_up=%s
                                   WHERE owner_id=%s AND parent_sub_id=%s AND type='sub_item' AND link=%s""",
                                (r[4],r[6],r[7],r[8],r[9],r[10],r[11],uid,sub_id,r[3]))
            if fresh:
                execute_values(cur, """INSERT INTO tunnel_configs
                      (owner_id,parent_sub_id,type,link,name,added_at,quality_score,
                       last_status,last_ping,last_jitter,last_speed_down,last_speed_up)
                      VALUES %s ON CONFLICT DO NOTHING""", fresh)
            stale = previous - incoming if allow_delete else set()
            if stale:
                cur.execute("""DELETE FROM tunnel_configs WHERE owner_id=%s AND parent_sub_id=%s
                               AND type='sub_item' AND link = ANY(%s)""", (uid,sub_id,list(stale)))
            conn.commit()
            logger.info('Subscription sync owner=%s sub=%s kept=%s added=%s removed=%s delete_enabled=%s',
                        uid,sub_id,len(common),len(fresh),len(stale),allow_delete)
            return {'kept':len(common), 'added':len(fresh),'removed':len(stale)}

    def _save_sub_info(self, sub_id, sub_info):
        with self.db.get_connection() as (conn, cur):
            cur.execute("UPDATE tunnel_configs SET sub_info=%s WHERE id=%s",
                        (json.dumps(sub_info), sub_id))
            conn.commit()

    def _bulk_update_singles(self, ok_rows, fail_ids):
        with self.db.get_connection() as (conn, cur):
            for r in ok_rows:
                cur.execute(
                    """UPDATE tunnel_configs SET last_status='OK', last_ping=%s, last_jitter=%s,
                       last_speed_down=%s, last_speed_up=%s, quality_score=%s WHERE id=%s""",
                    r,
                )
            if fail_ids:
                cur.execute(
                    "UPDATE tunnel_configs SET last_status='Fail' WHERE id = ANY(%s)",
                    (list(fail_ids),),
                )
            # Persist ordinary/manual test history too, not only Pro opt-in probes.
            # Batch writes share the existing transaction; failure rolls back all updates.
            if ok_rows:
                execute_values(cur, """INSERT INTO pro_metrics(owner_id,config_id,region,ok,ping,jitter)
                   SELECT c.owner_id,c.id,'default',TRUE,v.ping,v.jitter
                   FROM (VALUES %s) v(cid,ping,jitter) JOIN tunnel_configs c ON c.id=v.cid""",
                   [(int(r[-1]),float(r[0]),float(r[1])) for r in ok_rows])
            if fail_ids:
                cur.execute("""INSERT INTO pro_metrics(owner_id,config_id,region,ok)
                   SELECT owner_id,id,'default',FALSE FROM tunnel_configs WHERE id=ANY(%s)""",(list(fail_ids),))
            conn.commit()

    def _get_active_monitor(self, uid=None):
        from settings import SUPER_ADMIN_ID
        from monitor_fleet import select_monitor_row
        return select_monitor_row(self.db,int(uid or SUPER_ADMIN_ID),SUPER_ADMIN_ID)

    def _register_sub_source(self, uid, sub_link, sub_name):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.db.get_connection() as (conn, cur):
            cur.execute(
                """INSERT INTO tunnel_configs (owner_id, type, link, name, added_at, quality_score)
                   VALUES (%s, 'sub_source', %s, %s, %s, 10) ON CONFLICT DO NOTHING""",
                (uid, sub_link, sub_name, now),
            )
            cur.execute("SELECT id FROM tunnel_configs WHERE owner_id=%s AND link=%s AND type='sub_source'", (uid, sub_link))
            row = cur.fetchone()
            conn.commit()
            return row['id'] if row else None

    # ==================================================================
    # 🔄 PROCESSING
    # ==================================================================
    async def _refresh_state(self,uid,sub_id,status,**counts):
        try:
            from subscription_health import mark
            await run_sync(mark,self.db,uid,sub_id,status,**counts)
        except Exception:
            logger.exception('Subscription status write failed (source membership remains intact)')

    async def process_sub_async(self, uid, sub, monitor: dict):
        """پردازش یک اشتراک: دریافت لینک‌ها، تست موازی، ذخیره یک‌جا."""
        sub_name = sub['name']
        sub_link = sub['link']
        sub_id = sub['id']
        await self._refresh_state(uid,sub_id,'fetching')
        ok, links, sub_info = await self._fetch_sub_links(monitor, sub_link)
        if not ok:
            await self._refresh_state(uid,sub_id,'failed',error='SUB_FETCH_FAILED')
            return [f"❌ خطا در دریافت اشتراک از نود مانیتورینگ: {links}"]
        if not links:
            await self._refresh_state(uid,sub_id,'failed',error='SUB_EMPTY')
            return ["❌ اشتراک خالی دریافت شد؛ کانفیگ‌های قبلی حفظ شدند."]

        # Membership comes exclusively from the fetched subscription, before tests.
        # At the cap (600), we cannot prove the list wasn't truncated: no deletions.
        if (not isinstance(links, list) or any(not isinstance(item,dict) or
             not isinstance(item.get('link'),str) or not
             item['link'].startswith(('vmess://','vless://','trojan://','ss://'))
             for item in links)):
            await self._refresh_state(uid,sub_id,'failed',error='SUB_MALFORMED')
            return ['❌ SUB_MALFORMED: ساختار پاسخ ایجنت معتبر نیست؛ داده قبلی حفظ شد.']
        unique = {item['link']: item for item in links}
        if not unique:
            await self._refresh_state(uid,sub_id,'failed',error='SUB_EMPTY')
            return ['❌ سابسکریپشن معتبر خالی بود؛ داده‌های قبلی محفوظ است.']
        await self._refresh_state(uid,sub_id,'testing',extracted=len(unique))
        authoritative = bool(sub_info.get('complete') is True and len(unique) < 600) if isinstance(sub_info,dict) else False
        async def _one(item):
            try:
                async with self._user_sem(uid):
                    async with self.global_sem:
                        ok_t, data = await self._test_via_ws(monitor, item['link'], size=0.5, timeout=45)
                    return (data if ok_t and isinstance(data,dict) else None)
            except Exception as exc:
                logger.warning('Subscription test failed (%s); membership unchanged',type(exc).__name__)
                return None

        # Bounded tests. Failed connections can NEVER prune source membership.
        results = await asyncio.gather(*[_one(item) for item in unique.values()], return_exceptions=True)
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows, report_lines = [], []
        counts = {'OK':0, 'Fail':0, 'Unknown':0}
        for item, data in zip(unique.values(), results):
            if not isinstance(data,dict):data=None
            status = data.get('status') if data else 'Unknown'
            if status not in ('OK','Fail'):status='Unknown'
            counts[status]+=1
            c_name = (data.get('extracted_name') if data else None) or item.get('name') or 'Unknown'
            full_name = f"{sub_name} | {str(c_name)[:180]}"
            score = data.get('score',0) if status=='OK' else 0
            rows.append((uid,sub_id,'sub_item',item['link'],full_name,now_dt,score,status,
                         data.get('ping') if data else None,data.get('jitter') if data else None,
                         data.get('down') if data else None,data.get('up') if data else None))
            if status=='OK':
                report_lines.append(f"<b>{html.escape(str(c_name))}</b>\n"
                                    f"├ 📶 Ping: <code>{data.get('ping',0)}</code>\n"
                                    f"└ ⭐ Score: <code>{score}/10</code>")
        diff = await run_sync(self._bulk_save_sub_items, uid, sub_id, rows, allow_delete=authoritative)
        if not diff:
            await self._refresh_state(uid,sub_id,'failed',error='SUB_SAVE_FAILED')
            return ['❌ ذخیره کانفیگ‌ها ناموفق بود؛ داده قبلی حفظ شد.']
        await self._refresh_state(uid,sub_id,'ready',extracted=len(unique),healthy=counts['OK'])
        report_lines.insert(0, f"🔄 همگام‌سازی: {diff['added']} جدید | {diff['removed']} حذف‌شده | {diff['kept']} بدون تغییر\n"
                               f"✅ سالم: {counts['OK']} | ❌ خراب: {counts['Fail']} | ⚪ نامشخص: {counts['Unknown']}")
        if not authoritative:
            report_lines.append('⚠️ حذف خودکار انجام نشد: منبع کامل‌بودن فهرست را تأیید نکرده یا به سقف ۶۰۰ رسیده است.')
        if sub_info:
            try:
                await run_sync(self._save_sub_info, sub_id, sub_info)
            except Exception as exc:
                logger.warning('Subscription metadata save failed: %s', exc)

        return report_lines

    async def process_singles_async(self, singles, monitor: dict, uid=None, size=0.5):
        """پردازش لیست کانفیگ‌های تکی (موازی + ذخیره دسته‌ای)."""
        async def _one(cfg):
            async with self._user_sem(uid or cfg.get('owner_id')):
                async with self.global_sem:
                    ok, data = await self._test_via_ws(monitor,cfg['link'],size=float(size),timeout=60)
                return cfg, (data if ok else None)

        results = await asyncio.gather(*[_one(c) for c in singles], return_exceptions=True)

        ok_rows, fail_ids, report_lines = [], [], []
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            cfg, data = res
            if isinstance(data, dict) and data.get('status') == 'OK':
                score = data.get('score', 0)
                ok_rows.append((
                    data.get('ping', 0), data.get('jitter', 0),
                    data.get('down', 0), data.get('up', 0), score, cfg['id'],
                ))
                bar = "🟩" * int(score) + "⬜️" * (10 - int(score))
                report_lines.append(
                    f"<b>{html.escape(cfg['name'])}</b>\n📶 {data.get('ping', 0)}ms | {bar}"
                )
            else:
                fail_ids.append(cfg['id'])

        await run_sync(self._bulk_update_singles, ok_rows, fail_ids)
        return report_lines

    # ==================================================================
    # 🚀 MAIN ORCHESTRATOR
    # ==================================================================
    async def run_mass_update_process(self, context, uid, subs, singles, monitor, status_msg):
        final_report_groups = []
        tasks = [self.process_sub_async(uid, sub, monitor) for sub in subs]
        if singles:
            tasks.append(self.process_singles_async(singles, monitor, uid=uid))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        idx = 0
        for sub in subs:
            sub_res = results[idx]
            if isinstance(sub_res, Exception):
                logger.error("sub task failed: %s", sub_res)
            elif sub_res:
                final_report_groups.append({
                    "title": f"📂 <b>{html.escape(sub['name'])}</b>",
                    "lines": sub_res,
                })
            idx += 1

        if singles:
            singles_res = results[idx]
            if isinstance(singles_res, Exception):
                logger.error("singles task failed: %s", singles_res)
            elif singles_res:
                final_report_groups.append({
                    "title": "👤 <b>کانفیگ‌های تکی</b>",
                    "lines": singles_res,
                })

        try:
            await status_msg.delete()
        except Exception:
            pass

        if not final_report_groups:
            await context.bot.send_message(
                chat_id=uid,
                text="❌ هیچ کانفیگ سالمی یافت نشد (یا خطا در اتصال به نود مانیتورینگ).",
            )
            return

        header = (
            f"📊 <b>گزارش نهایی تست همگانی</b>\n"
            f"📦 تعداد منابع: {len(final_report_groups)}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
        )
        await context.bot.send_message(chat_id=uid, text=header, parse_mode='HTML')

        for group in final_report_groups:
            chunk = f"{group['title']}\n➖➖➖➖➖➖➖➖\n"
            for line in group['lines']:
                if len(chunk) + len(line) > 4000:
                    await context.bot.send_message(chat_id=uid, text=chunk, parse_mode='HTML')
                    chunk = ""
                chunk += line + "\n"
            if chunk:
                await context.bot.send_message(chat_id=uid, text=chunk, parse_mode='HTML')

        kb = [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data='tunnel_list_menu')]]
        await context.bot.send_message(
            chat_id=uid, text="✅ **پایان عملیات.**",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown',
        )

    async def finalize_sub_adding(self, update: Update, context: ContextTypes.DEFAULT_TYPE, temp_sub_link):
        sub_name = update.message.text.strip()
        uid = update.effective_user.id

        status_msg = await update.message.reply_text(
            "⏳ <b>در حال دریافت کانفیگ‌ها...</b>\n(لطفاً صبر کنید)", parse_mode='HTML'
        )

        # Register the source first. Lack of an Iran Agent must never be
        # confused with lack of a subscription in subsequent screens.
        sub_id = await run_sync(self._register_sub_source, uid, temp_sub_link, sub_name)
        if not sub_id:
            await status_msg.edit_text("❌ ثبت اشتراک در دیتابیس ناموفق بود.")
            return ConversationHandler.END

        monitor = await run_sync(self._get_active_monitor,uid)
        if not monitor:
            await self._refresh_state(uid,sub_id,'waiting_agent')
            await status_msg.edit_text(
                "✅ اشتراک ذخیره شد؛ اما هنوز Agent سالمی برای دریافت و تست کانفیگ‌ها آماده نیست. "
                "پس از فعال‌کردن Agent، از مدیریت اشتراک گزینه به‌روزرسانی را بزنید."
            )
            return ConversationHandler.END

        report_lines = await self.process_sub_async(
            uid, {'name': sub_name, 'link': temp_sub_link, 'id': sub_id}, monitor
        )

        if report_lines and not report_lines[0].startswith("❌"):
            await status_msg.edit_text(
                f"🏁 <b>عملیات پایان یافت.</b>\n📂 {html.escape(sub_name)}\n"
                f"✅ تعداد سالم: {len(report_lines)}",
                parse_mode='HTML',
            )
        else:
            err = report_lines[0] if report_lines else "خطای ناشناخته"
            await status_msg.edit_text(f"❌ خطا: {err}")

        return ConversationHandler.END


tunnel_manager = TunnelLogic()
