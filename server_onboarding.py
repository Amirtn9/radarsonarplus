"""Shared direct-first SSH onboarding used by the website and Telegram workflows.

Only operator-supplied global numeric IP addresses are accepted from the web.
The fingerprint is *never* trusted just because a network scan returned it.
"""
from __future__ import annotations

import hmac
import ipaddress
import re


_FINGERPRINT = re.compile(r'^SHA256:[A-Za-z0-9+/]{43}$')


def public_destination(ip, port):
    try:
        address = ipaddress.ip_address(str(ip).strip())
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError('A public numeric IP and valid SSH port are required') from None
    if not address.is_global or not 1 <= port <= 65535:
        raise ValueError('Only public IP addresses and SSH ports 1-65535 are supported')
    return str(address), port


def scan_direct_first(db, admin_id, ip, port):
    """Direct first, then approved & freshly healthy relay nodes on network errors.

    Returns candidate and route metadata; the candidate is NOT trusted here.
    """
    from ssh_support import scan_host_key, describe_ssh_error
    from ssh_relay import eligible_relay_nodes, retryable_direct_error
    ip, port = public_destination(ip, port)
    try:
        return scan_host_key(ip, port), None
    except Exception as initial:
        if not retryable_direct_error(describe_ssh_error(initial)):
            raise
        nodes = eligible_relay_nodes(db, admin_id, exclude_ip=ip)
        for node in nodes:
            try:
                return scan_host_key(ip, port, node), int(node['id'])
            except Exception as error:
                if not retryable_direct_error(describe_ssh_error(error)):
                    raise
        raise initial


def confirm_and_connect(db, admin_id, ip, port, username, password, expected):
    """Re-scan key, compare to independently obtained fingerprint and authenticate.

    Never enroll a host key before a successful SSH authentication. A direct
    outage permits relay fallback, but not fingerprint or password bypass.
    """
    from ssh_support import authenticate_candidate, trust_host_key
    from ssh_relay import eligible_relay_nodes
    ip, port = public_destination(ip, port)
    if not _FINGERPRINT.fullmatch(str(expected)):
        raise ValueError('Enter the independently verified SHA256 host fingerprint')
    candidate, relay_id = scan_direct_first(db, admin_id, ip, port)
    if not hmac.compare_digest(candidate['fingerprint'], expected):
        raise PermissionError('HOST_FINGERPRINT_MISMATCH: SSH key changed or differs from confirmation')
    node = None
    if relay_id is not None:
        pool = eligible_relay_nodes(db, admin_id, exclude_ip=ip)
        node = next((item for item in pool if int(item['id']) == relay_id), None)
        if node is None:
            raise ConnectionError('RELAY_NO_LONGER_APPROVED')
    authenticate_candidate(candidate, expected, username, password, node)
    trust_host_key(candidate, expected)
    from core import ServerMonitor
    nodes = eligible_relay_nodes(db, admin_id, exclude_ip=ip)
    result = ServerMonitor.check_ssh_connection(ip, port, username, password, nodes)
    if result.get('status') != 'Online':
        raise ConnectionError('SSH_VERIFICATION_FAILED: '+str(result.get('error') or 'unknown')[:120])
    return {'route': 'agent' if relay_id is not None else 'direct', 'agent_id': relay_id}


def probe_saved_server(db, admin_id, server):
    """Read a candidate key for an authorized, already-saved server.

    Callers must resolve the server with get_server_for_user before invoking.
    Never persist or automatically trust a key obtained by network scanning.
    """
    candidate, relay_id = scan_direct_first(db, admin_id, server['ip'], server['port'])
    return {
        'fingerprint': candidate['fingerprint'],
        'key_type': candidate['type'],
        'route': 'agent' if relay_id is not None else 'direct',
        'agent_id': relay_id,
        'verified': False,
    }


def confirm_saved_server(db, admin_id, server, expected):
    """Enroll host key after operator approval and SSH authentication.

    No modification to the existing server record, group, or credentials.
    The shared onboarding process re-scans the key to prevent stale approval.
    """
    from core import sec
    return confirm_and_connect(
        db, admin_id, server['ip'], server['port'], server['username'],
        sec.decrypt(server['password']), expected,
    )
