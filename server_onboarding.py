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



def _eligible_relay_nodes(db, user_id, ip):
    from settings import SUPER_ADMIN_ID
    from ssh_relay import eligible_relay_nodes
    if not SUPER_ADMIN_ID:
        return []
    return eligible_relay_nodes(db, SUPER_ADMIN_ID, user_id=int(user_id), exclude_ip=str(ip))

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
        from settings import SUPER_ADMIN_ID
        nodes = eligible_relay_nodes(db, SUPER_ADMIN_ID, user_id=int(admin_id), exclude_ip=ip)
        target_key=f'{ip}:{int(port)}'
        try:
            from ssh_relay import stable_relay_order, remember_relay_success, forget_relay_route, retryable_relay_error
        except ImportError:  # compatibility with isolated relay adapters
            stable_relay_order=lambda values,key,**kwargs: list(values or [])
            remember_relay_success=lambda *args,**kwargs: None
            forget_relay_route=lambda *args,**kwargs: None
            retryable_relay_error=lambda exc,code: retryable_direct_error(code)
        for node in stable_relay_order(nodes,target_key,db=db):
            try:
                candidate=scan_host_key(ip, port, node)
                remember_relay_success(target_key,node['id'],db=db)
                try:
                    from monitor_fleet import record_relay_result
                    record_relay_result(db,node['id'],True)
                except Exception:pass
                return candidate, int(node['id'])
            except Exception as error:
                relay_code=describe_ssh_error(error)
                if not retryable_relay_error(error,relay_code):
                    raise
                forget_relay_route(target_key,node.get('id'),db=db)
                try:
                    from monitor_fleet import record_relay_result
                    record_relay_result(db,node['id'],False)
                except Exception:pass
        raise initial


def confirm_and_connect(db, admin_id, ip, port, username, password, expected):
    """Verify and authenticate SSH, retrying approved relay routes on transport failure.

    The central route is always scanned first. A relay retry is permitted only for
    network/transport errors; fingerprint changes and authentication failures stop
    the workflow immediately. A key is persisted only after a successful SSH command.
    """
    from ssh_support import authenticate_candidate, trust_host_key, scan_host_key, describe_ssh_error
    from ssh_relay import (eligible_relay_nodes, retryable_direct_error,
                           retryable_relay_error, stable_relay_order,
                           remember_relay_success, forget_relay_route)
    ip, port = public_destination(ip, port)
    if not _FINGERPRINT.fullmatch(str(expected)):
        raise ValueError('Enter the independently verified SHA256 host fingerprint')

    candidate, relay_id = scan_direct_first(db, admin_id, ip, port)
    if not hmac.compare_digest(candidate['fingerprint'], expected):
        raise PermissionError('HOST_FINGERPRINT_MISMATCH: SSH key changed or differs from confirmation')

    target_key = f'{ip}:{int(port)}'
    pool = []
    selected = None
    if relay_id is not None:
        pool = _eligible_relay_nodes(db, admin_id, ip)
        selected = next((item for item in pool if str(item.get('id')) == str(relay_id)), None)
        if selected is None:
            raise ConnectionError('RELAY_NO_LONGER_APPROVED')

    try:
        authenticate_candidate(candidate, expected, username, password, selected)
    except Exception as initial_error:
        # Never route around a bad password, an unknown/changed key, or a failed
        # remote command. Retry only if the active transport itself failed.
        initial_code = describe_ssh_error(initial_error)
        retryable = (retryable_relay_error(initial_error, initial_code) if selected is not None
                     else retryable_direct_error(initial_code))
        if not retryable:
            raise

        if selected is not None:
            forget_relay_route(target_key, selected.get('id'), db=db)
            try:
                from monitor_fleet import record_relay_result
                record_relay_result(db, selected['id'], False)
            except Exception:
                pass
        elif not pool:
            pool = _eligible_relay_nodes(db, admin_id, ip)

        ordered = stable_relay_order(pool, target_key, db=db)
        if selected is not None:
            ordered = [node for node in ordered if str(node.get('id')) != str(selected.get('id'))]
        last_error = initial_error
        for node in ordered[:8]:
            try:
                candidate_via_relay = scan_host_key(ip, port, node)
                if not hmac.compare_digest(candidate_via_relay['fingerprint'], expected):
                    raise PermissionError('HOST_FINGERPRINT_MISMATCH: relay observed a different SSH host key')
                authenticate_candidate(candidate_via_relay, expected, username, password, node)
                # Trust only the key whose identity was independently confirmed and
                # whose SSH authentication + harmless command both succeeded.
                trust_host_key(candidate_via_relay, expected)
                remember_relay_success(target_key, node['id'], db=db)
                try:
                    from monitor_fleet import record_relay_result
                    record_relay_result(db, node['id'], True)
                except Exception:
                    pass
                return {'route': 'agent', 'agent_id': int(node['id'])}
            except PermissionError as exc:
                # Fingerprint mismatches are security failures, not failover signals.
                if 'FINGERPRINT' in str(exc) or 'HOST_KEY' in str(exc):
                    raise
                last_error = exc
                code = describe_ssh_error(exc)
                if not retryable_relay_error(exc, code):
                    raise
            except Exception as exc:
                last_error = exc
                code = describe_ssh_error(exc)
                if not retryable_relay_error(exc, code):
                    raise
            forget_relay_route(target_key, node.get('id'), db=db)
            try:
                from monitor_fleet import record_relay_result
                record_relay_result(db, node['id'], False)
            except Exception:
                pass
        raise last_error

    trust_host_key(candidate, expected)
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
