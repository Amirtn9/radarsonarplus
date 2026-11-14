"""Authorization at the Telegram handler boundary (not just hidden buttons).

Checks are applied to ConversationHandler children as well as regular handlers.
The database queries are performed off the asyncio event loop.
"""
import asyncio
from telegram_safety import safe_callback_answer
import logging
import re
from access_policy import parse_server_action
from telegram.ext import CallbackQueryHandler, MessageHandler, CommandHandler
from settings import SUPER_ADMIN_ID
from database import Database
from workspace_service import ACTOR_ID

log = logging.getLogger(__name__)

# Administrative actions can also be invoked with forged callback_data.
ADMIN_CALLBACKS = re.compile(
    r'^(?:admin_|add_new_admin$|del_pay_method_|add_pay_method_|'
    r'confirm_admin_|delete_monitor_node$|update_monitor_node$)'
)
ADMIN_MESSAGES = {
    'get_new_user_id', 'get_new_user_days', 'admin_set_limit_handler',
    'admin_set_days_handler', 'admin_search_handler', 'admin_broadcast_send',
    'admin_report_by_uid_handler', 'admin_agent_port_input',
    'admin_backup_restore_handler', 'admin_key_restore_handler',
    'get_pay_network', 'get_pay_address', 'get_pay_holder',
    'admin_search_servers_by_uid_handler',
}
# Captured numerical IDs in callback data. Server ID is at end of act_* except
# act_checkhost_{id}_{IP}; these prefixes are deliberately explicit.
SERVER_ID = (
    re.compile(r'^detail_(\d+)(?:_|$)'),
    re.compile(r'^act_[a-z]+_(\d+)(?:_|$)'),
    re.compile(r'^setdns_[a-z]+_(\d+)$'),
    re.compile(r'^toggle_active_(\d+)$'),
    re.compile(r'^srv_multidel_toggle_(\d+)$'),
    re.compile(r'^cmd_terminal_(\d+)$'),
    re.compile(r'^admin_detail_(\d+)$'),
)
CONFIG_ID = (
    re.compile(r'^(?:test_conf|view_conf|del_conf|conf_detail|copy_conf|qr_conf|manage_sub|update_sub|del_sub_full|get_sub_links)_(\d+)(?:_|$)'),
    re.compile(r'^del_list_item_(\d+)(?:_|$)'),
)
GROUP_ID = (re.compile(r'^delgroup_(\d+)$'),)
CHANNEL_ID = (re.compile(r'^delchan_(\d+)$'),)


_READ_CALLBACK=re.compile(r'^(?:v6_(?:home|return|servers|configs|monitor|tools|settings|account|guide|lesson_[a-z-]+_\d+)|pro_(?:home|favorites|rank|incidents|tags|prefs|find_help|team|audit|report|insights|rules|list_(?:server|config)_\d+|export_(?:rank|server|pdf))|detail_\d+(?:_.*)?|conf_detail_\d+(?:_.*)?|tunnel_list_menu|gen_web_token|wallet_menu|channels_menu|settings_menu)$')

def team_callback_allowed(role,data,name,module):
    if role in ('owner','manager','technical'):return True
    if _READ_CALLBACK.fullmatch(data or ''):return True
    if name in ('help_command','show_profile','history','find','team','home','pdf_report'):return True
    if role=='server' and module=='bot_logic_mod.servers':
        if name in ('ask_terminal_command','run_terminal_action'):return False
        if data.startswith('act_'):
            try:return parse_server_action(data)[0] in ('del','editexpiry','chart')
            except ValueError:return False
        return True
    if role=='monitor' and module=='service_bot':return True
    return False


def _access_sync(uid, data, callback_name,callback_module=""):
    db = Database()
    if not uid:
        return False
    if (ADMIN_CALLBACKS.match(data or '') or callback_name in ADMIN_MESSAGES) and uid != SUPER_ADMIN_ID:
        return False
    if data and data.startswith('act_') and callback_name == 'server_actions':
        try:
            _, sid, _ = parse_server_action(data)
        except ValueError:
            return False
        if uid != SUPER_ADMIN_ID and not db.owns_resource('servers', sid, uid):
            return False
    if uid != SUPER_ADMIN_ID:
        if callable(getattr(db,'workspace_owner',None)):
            owner=db.workspace_owner(uid)
            if owner!=uid:
                from pro_store import ProStore
                role=ProStore(db).role(uid,owner)
                if not team_callback_allowed(role,data or '',callback_name,callback_module):return False
        if data in ('gen_web_token','v6_account','wallet_menu','pro_home'):
            allowed,_=getattr(db,'check_read_access',db.check_access)(uid)
        else:allowed, _ = db.check_access(uid, SUPER_ADMIN_ID)
        if not allowed:
            return False
        if data:
            for table, patterns in [('servers', SERVER_ID), ('tunnel_configs', CONFIG_ID),
                                    ('groups', GROUP_ID), ('channels', CHANNEL_ID)]:
                for pattern in patterns:
                    hit = pattern.match(data)
                    if hit and not db.owns_resource(table, int(hit.group(1)), uid):
                        return False
    if callable(getattr(db,'execute',None)):db.execute('UPDATE users SET last_active_at=NOW() WHERE user_id=%s',(uid,))
    return True


async def _is_allowed(update, callback):
    # Starting a conversation is intentionally allowed for new users.
    if getattr(callback, '__name__', '') == 'start':
        return True
    uid = getattr(getattr(update, 'effective_user', None), 'id', None)
    data = getattr(getattr(update, 'callback_query', None), 'data', '') or ''
    try:
        return await asyncio.to_thread(_access_sync, uid, data, getattr(callback, '__name__', ''),getattr(callback,'__module__',''))
    except Exception:
        log.exception('Authorization failed closed')
        return False


class SecureCallbackQueryHandler(CallbackQueryHandler):
    async def handle_update(self, update, application, check_result, context):
        if not await _is_allowed(update, self.callback):
            if update.callback_query:
                await safe_callback_answer(update.callback_query, '⛔ دسترسی مجاز نیست.', show_alert=True)
            return None
        token=ACTOR_ID.set(int(update.effective_user.id))
        try:
            return await super().handle_update(update, application, check_result, context)
        finally:
            ACTOR_ID.reset(token)


class SecureMessageHandler(MessageHandler):
    async def handle_update(self, update, application, check_result, context):
        if not await _is_allowed(update, self.callback):
            if update.effective_message:
                await update.effective_message.reply_text('⛔ دسترسی مجاز نیست.')
            return None
        token=ACTOR_ID.set(int(update.effective_user.id))
        try:
            return await super().handle_update(update, application, check_result, context)
        finally:
            ACTOR_ID.reset(token)


class SecureCommandHandler(CommandHandler):
    async def handle_update(self, update, application, check_result, context):
        if not await _is_allowed(update, self.callback):
            if update.effective_message:
                await update.effective_message.reply_text('⛔ دسترسی مجاز نیست.')
            return None
        token=ACTOR_ID.set(int(update.effective_user.id))
        try:
            return await super().handle_update(update, application, check_result, context)
        finally:
            ACTOR_ID.reset(token)
