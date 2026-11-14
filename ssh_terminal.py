"""Authenticated browser SSH terminal over the existing verified SSH/Agent relay."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


def _origin_is_allowed(websocket: WebSocket) -> bool:
    """Require a same-origin browser socket; support a configured public origin."""
    origin = str(websocket.headers.get("origin") or "").rstrip("/")
    if not origin:
        return False

    expected = str(os.getenv("SONAR_WEB_ORIGIN", "") or "").rstrip("/")
    if expected:
        return hmac.compare_digest(origin, expected)

    # A same-host fallback is safe behind a reverse proxy when Host and
    # X-Forwarded-Proto are set by that proxy. The installer configures both.
    host = str(websocket.headers.get("host") or "").lower()
    parsed = urlsplit(origin)
    proto = str(websocket.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    if not proto:
        proto = "https" if websocket.url.scheme in ("wss", "https") else "http"
    return bool(
        host
        and parsed.scheme in ("https", "http")
        and parsed.scheme == proto
        and parsed.netloc.lower() == host
        and not parsed.username
        and not parsed.password
    )


def _terminal_ssh_error(exc) -> str:
    """Return English, non-sensitive terminal error text."""
    try:
        from ssh_support import describe_ssh_error
        code = str(describe_ssh_error(exc)).split(":", 1)[0].strip().upper()
    except Exception:
        code = ""
    messages = {
        "HOST_KEY_CHANGED": "SSH host key changed. Verify the fingerprint from the server console.",
        "HOST_KEY_UNKNOWN": "SSH host key is not trusted. Verify and approve the server fingerprint first.",
        "AUTH_FAILED": "SSH authentication failed. Check the saved username and password.",
        "SSH_AUTH_FAILED": "SSH authentication failed. Check the saved username and password.",
        "SSH_PORT_UNREACHABLE": "SSH port is unreachable. Check the SSH port and firewall.",
        "SSH_DNS_FAILED": "The server hostname could not be resolved.",
        "SSH_TIMEOUT": "SSH connection timed out.",
        "SSH_CONNECTION_REFUSED": "The SSH service refused the connection.",
        "SSH_INPUT_INVALID": "The saved SSH address or port is invalid.",
        "SSH_CONNECTION_FAILED": "SSH connection failed. Check the SSH service and network path.",
    }
    return messages.get(code, "SSH connection failed. Check the server and network configuration.")


def make_router(store, admin_id, *, verify_tma=None, get_bot_token=None):
    router = APIRouter(tags=["ssh-terminal"])

    @router.websocket("/api/terminal/{server_id}")
    async def terminal(websocket: WebSocket, server_id: int):
        if not _origin_is_allowed(websocket):
            log.warning(
                "Browser SSH terminal rejected: origin_check_failed host=%s",
                str(websocket.headers.get("host") or "")[:160],
            )
            await websocket.close(code=4403, reason="Origin check failed")
            return

        await websocket.accept()
        client = None
        channel = None
        receive_task = None
        password = ""
        lease_id=None
        incoming=outgoing=0
        started=time.monotonic()
        last_input=started
        next_heartbeat=started+20
        from control_store import Control
        control=Control(store)
        try:
            if server_id < 1:
                await websocket.send_json({"type": "error", "message": "Invalid server ID."})
                await websocket.close(code=4400, reason="Invalid server ID")
                return

            try:
                first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
                auth = json.loads(first)
            except Exception:
                await websocket.send_json({"type": "error", "message": "Authentication message missing or invalid."})
                await websocket.close(code=4401, reason="Authentication required")
                return

            if not isinstance(auth, dict) or auth.get("type") != "auth":
                await websocket.send_json({"type": "error", "message": "Invalid authentication message."})
                await websocket.close(code=4401, reason="Authentication required")
                return

            from web_login import identify, digest, valid_token, csrf_proof
            session = websocket.cookies.get("sonar_web_session", "")
            session_data = None
            if session:
                try:
                    session_data = await asyncio.to_thread(identify, store.db, session)
                except Exception:
                    log.exception("Browser SSH terminal session lookup failed")
                    await websocket.send_json({"type": "error", "message": "Session service is temporarily unavailable."})
                    await websocket.close(code=1013, reason="Session service unavailable")
                    return

            csrf = auth.get("csrf", "")
            csrf_valid = bool(
                session_data
                and isinstance(csrf, str)
                and valid_token(csrf)
                and (hmac.compare_digest(digest(csrf), str(session_data.get("csrf_hash") or ""))
                     or hmac.compare_digest(csrf,csrf_proof(session)))
            )

            # Telegram Mini Apps can use their signed initData directly when
            # third-party WebView cookie behavior prevents the session cookie
            # from accompanying the WebSocket upgrade.
            tma_uid = None
            init_data = auth.get("initData", "")
            if isinstance(init_data, str) and init_data and verify_tma and get_bot_token:
                try:
                    max_age = min(600, max(60, int(os.getenv("SONAR_MINIAPP_SESSION_MAX_AGE", "300"))))
                    tma_uid = await asyncio.to_thread(
                        verify_tma, init_data, get_bot_token(), max_age=max_age
                    )
                    tma_uid = int(tma_uid)
                except Exception:
                    tma_uid = None

            if csrf_valid:
                uid = int(session_data["user_id"])
                # If both credentials are present, they must represent the same
                # Telegram account; this prevents stale cross-account cookies.
                if tma_uid is not None and tma_uid != uid:
                    await websocket.send_json({"type": "error", "message": "Session account does not match Telegram account. Sign in again."})
                    await websocket.close(code=4403, reason="Account mismatch")
                    return
            elif tma_uid is not None:
                uid = tma_uid
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": "Secure session could not be verified. Reload the panel and sign in again."
                })
                await websocket.close(code=4401, reason="Session verification failed")
                return

            allowed, _ = await asyncio.to_thread(store.db.check_access, uid)
            if not allowed:
                await websocket.send_json({"type": "error", "message": "Account access is inactive."})
                await websocket.close(code=4403, reason="Account inactive")
                return

            from control_api import require_elevated
            recent=await asyncio.to_thread(require_elevated,store.db,session) if csrf_valid else False
            if not recent and tma_uid is not None:
                try:
                    verify_tma(init_data,get_bot_token(),max_age=60)
                    recent=True
                except (PermissionError,ValueError,KeyError):pass
            if not recent:
                await websocket.send_json({'type':'error','message':'Recent verification required. Enter a fresh bot code in the panel.'})
                await websocket.close(code=4403,reason='Step-up required')
                return
            server = await asyncio.to_thread(
                store.db.get_server_for_user, server_id, uid, admin_id
            )
            if not server or int(server.get("is_active") or 0) != 1:
                await websocket.send_json({
                    "type": "error",
                    "message": "Active server not found in your workspace."
                })
                await websocket.close(code=4404, reason="Server unavailable")
                return

            await asyncio.to_thread(store.require,uid,int(server['owner_id']),True)
            lease_id=str(uuid.uuid4())
            await asyncio.to_thread(control.lease,uid,server_id,lease_id)
            from core import ServerMonitor, sec
            from ssh_relay import eligible_relay_nodes

            ip = str(server["ip"])
            port = int(server["port"])
            username = str(server["username"])
            password = sec.decrypt(server.get("password") or "")
            if not password:
                await websocket.send_json({
                    "type": "error",
                    "message": "The saved SSH password cannot be decrypted. Check the server credentials."
                })
                await websocket.close(code=4401, reason="SSH credentials unavailable")
                return

            relays = await asyncio.to_thread(
                eligible_relay_nodes, store.db, admin_id,
                user_id=uid, exclude_ip=ip, limit=5
            )

            try:
                client = await asyncio.to_thread(
                    ServerMonitor.get_ssh_client, ip, port, username, password, relays
                )
                transport=client.get_transport()
                if transport:transport.set_keepalive(30)
                channel = await asyncio.to_thread(
                    client.invoke_shell,
                    term="xterm-256color",
                    width=100,
                    height=30,
                    environment={
                        "LANG": "C",
                        "LC_ALL": "C",
                        "LANGUAGE": "C",
                        "TERM": "xterm-256color",
                    },
                )
                channel.settimeout(0.1)
                # Force a predictable English POSIX locale for this interactive
                # shell only. No server-wide locale or SSH configuration is changed.
                await asyncio.to_thread(
                    channel.sendall,
                    b"export LANG=C LC_ALL=C LANGUAGE=C TERM=xterm-256color PS1='\\u@\\h:\\w\\$ '\n",
                )
            except Exception as exc:
                await websocket.send_json({
                    "type": "error",
                    "message": _terminal_ssh_error(exc)
                })
                await websocket.close(code=4408, reason="SSH connection failed")
                return
            finally:
                password = ""

            await websocket.send_json({
                "type": "ready",
                "server": str(server.get("name") or ip),
                "transport": "ssh"
            })

            receive_task = asyncio.create_task(websocket.receive_text())
            next_auth_check = time.monotonic() + 60
            while True:
                done, _ = await asyncio.wait({receive_task}, timeout=0.1)
                if receive_task in done:
                    try:
                        raw = receive_task.result()
                    except WebSocketDisconnect:
                        break
                    except Exception:
                        break
                    try:
                        message = json.loads(raw)
                    except (TypeError, ValueError):
                        message = {}
                    if not isinstance(message, dict):
                        message = {}
                    kind = message.get("type")
                    if kind == "input":
                        data = message.get("data", "")
                        if isinstance(data, str) and len(data) <= 32768 and not channel.closed:
                            encoded=data.encode("utf-8")
                            await asyncio.to_thread(channel.sendall, encoded)
                            incoming+=len(encoded)
                            last_input=time.monotonic()
                    elif kind == "resize":
                        try:
                            cols = max(2, min(400, int(message.get("cols", 100))))
                            rows = max(2, min(200, int(message.get("rows", 30))))
                            if not channel.closed:
                                await asyncio.to_thread(channel.resize_pty, width=cols, height=rows)
                        except (TypeError, ValueError, OSError):
                            pass
                    elif kind == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif kind == "close":
                        break
                    receive_task = asyncio.create_task(websocket.receive_text())

                if channel.closed:
                    break
                try:
                    if channel.recv_ready():
                        output = await asyncio.to_thread(channel.recv, 65536)
                        if output:
                            outgoing+=len(output)
                            await websocket.send_json({
                                "type": "output",
                                "data": output.decode("utf-8", "replace")
                            })
                        elif channel.closed:
                            break
                    elif channel.exit_status_ready():
                        break
                except OSError:
                    break

                if time.monotonic()-last_input>900:
                    await websocket.send_json({'type':'error','message':'Terminal idle timeout. Reconnect to continue.'})
                    break
                if time.monotonic()>=next_heartbeat:
                    await asyncio.to_thread(control.terminal_touch,lease_id,incoming,outgoing)
                    await websocket.send_json({'type':'pong'})
                    next_heartbeat=time.monotonic()+20
                if time.monotonic() >= next_auth_check:
                    if session:
                        current = await asyncio.to_thread(identify, store.db, session)
                        if current and int(current.get("user_id") or 0) != uid:
                            await websocket.send_json({"type": "error", "message": "Secure session account changed. Reconnect."})
                            break
                        if not current and tma_uid is None:
                            await websocket.send_json({"type": "error", "message": "Secure session expired. Reconnect."})
                            break
                    still_allowed, _ = await asyncio.to_thread(store.db.check_access, uid)
                    if not still_allowed:
                        await websocket.send_json({"type": "error", "message": "Account access is inactive."})
                        break
                    await asyncio.to_thread(store.require,uid,int(server['owner_id']),True)
                    next_auth_check = time.monotonic() + 60

            try:
                await websocket.send_json({"type": "closed"})
            except Exception:
                pass
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.warning("Browser SSH terminal ended: %s", type(exc).__name__)
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": "Terminal connection ended unexpectedly. Check the web proxy and SSH route."
                })
            except Exception:
                pass
        finally:
            if lease_id:
                try:await asyncio.to_thread(control.terminal_touch,lease_id,incoming,outgoing,True)
                except Exception:log.exception('Terminal closing audit failed')
            if receive_task and not receive_task.done():
                receive_task.cancel()
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            try:
                await websocket.close()
            except Exception:
                pass

    return router
