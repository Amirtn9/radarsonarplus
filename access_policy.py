"""One callback grammar and capability policy for UI and execution boundaries."""
import re
import math
from urllib.parse import urlsplit

SERVER_ACTIONS = frozenset({
    'del', 'reboot', 'editexpiry', 'fullreport', 'chart', 'datacenter',
    'checkhost', 'speedtest', 'installspeed', 'repoupdate', 'fullupdate',
    'clearcache', 'cleandisk', 'dns', 'locked_terminal', 'installscript',
    'installagent', 'agentlog',
})
_ACTION = re.compile(r'^act_([a-z]+(?:_[a-z]+)*)_([1-9][0-9]*)(?:_(.+))?$')


def parse_server_action(data):
    hit = _ACTION.fullmatch(str(data or ''))
    if not hit or hit[1] not in SERVER_ACTIONS:
        raise ValueError('Invalid server action')
    action, sid, argument = hit[1], int(hit[2]), hit[3]
    if argument is not None and action != 'checkhost':
        raise ValueError('Unexpected server action argument')
    return action, sid, argument


def has_premium(user, actor, admin):
    if admin and int(actor) == int(admin):
        return True
    try:
        return int((user or {}).get('plan_type', 0)) in (1, 2, 3)
    except (ValueError, TypeError):
        return False


def valid_agent_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError('Agent port must be an integer') from None
    if not 1024 <= port <= 65535:
        raise ValueError('Agent port must be between 1024 and 65535')
    return port


def valid_subscription_url(value):
    """Structural UI validation; the agent separately pins DNS and rejects private IPs."""
    value = str(value or '').strip()
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == 'https' and parsed.hostname and
                 not parsed.username and not parsed.password and
                 parsed.port in (None, 443) and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid or len(value) > 8192:
        raise ValueError('لینک اشتراک باید HTTPS با پورت 443 و بدون نام کاربری یا رمز باشد.')
    return value


def monitoring_setting(kind, value):
    if kind == 'int':
        result = int(value)
        if not 10 <= result <= 1440:
            raise ValueError('فاصلهٔ تست باید بین 10 تا 1440 دقیقه باشد.')
    elif kind in ('small', 'big'):
        result = float(value)
        if not math.isfinite(result) or not 0 <= result <= 10:
            raise ValueError('حجم تست باید بین 0 تا 10 مگابایت باشد؛ 0 یعنی فقط پینگ.')
    else:
        raise ValueError('تنظیم ناشناخته است.')
    return result
