"""BackPack-style framed SSH command transport for Sonar Radar.

The controller sends one bounded JSON request over SSH stdin and reads one
base64-framed JSON response from stdout. The remote command is constant: shell
text and secrets are never placed in the SSH command line or process arguments.

SSH connections are pooled per endpoint/credential/trust-store identity. A
stale connection is replaced and retried only when opening the SSH channel
fails before the request is sent. Once a command may have started, it is never
replayed automatically.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import math
import shlex
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


_REMOTE_EXEC_SCRIPT = r"""
import base64
import json
import math
import os
import signal
import subprocess
import sys

MAX_REQUEST = 2 * 1024 * 1024
MAX_COMMAND = 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024

def emit(ok, err="", body=None):
    response = {"ok": bool(ok), "err": str(err or "")[:160], "body": body or {}}
    raw = json.dumps(response, separators=(",", ":")).encode("utf-8")
    sys.stdout.write(base64.b64encode(raw).decode("ascii") + "\n")
    sys.stdout.flush()

def main():
    try:
        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if not line or len(line) > MAX_REQUEST:
            emit(False, "INVALID_REQUEST_SIZE")
            return
        raw = base64.b64decode(line.strip(), validate=True)
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict) or request.get("op") != "run":
            emit(False, "UNKNOWN_OPERATION")
            return
        body = request.get("body")
        if not isinstance(body, dict):
            emit(False, "INVALID_REQUEST_BODY")
            return
        command = body.get("command")
        if not isinstance(command, str) or len(command) > MAX_COMMAND or "\x00" in command:
            emit(False, "INVALID_COMMAND")
            return
        budget = body.get("timeout", 60)
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            emit(False, "INVALID_TIMEOUT")
            return
        budget = float(budget)
        if not math.isfinite(budget) or budget <= 0 or budget > 1800:
            emit(False, "INVALID_TIMEOUT")
            return

        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        argv = ["/bin/bash", "-s"] if os.geteuid() == 0 else [
            "sudo", "-n", "--", "/bin/bash", "-c", "exec /bin/bash -s"
        ]
        script = ("export DEBIAN_FRONTEND=noninteractive\n" + command + "\n").encode("utf-8")
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
        timed_out = False
        try:
            out, err = process.communicate(script, timeout=budget)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            out, err = process.communicate()

        truncated = len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT
        out = out[:MAX_OUTPUT]
        err = err[:MAX_OUTPUT]
        code = None if timed_out else int(process.returncode)
        response_body = {
            "stdout_b64": base64.b64encode(out).decode("ascii"),
            "stderr_b64": base64.b64encode(err).decode("ascii"),
            "exit_code": code,
            "timed_out": timed_out,
            "output_truncated": truncated,
        }
        if timed_out:
            emit(False, "REMOTE_COMMAND_TIMEOUT", response_body)
        elif code != 0:
            emit(False, "REMOTE_COMMAND_FAILED", response_body)
        else:
            emit(True, "", response_body)
    except BaseException as exc:
        # Do not echo request data, command text, credentials or arbitrary
        # exception messages into the controller's logs.
        emit(False, "REMOTE_EXECUTOR_ERROR", {"exception": type(exc).__name__})

if __name__ == "__main__":
    main()
"""

REMOTE_EXEC_COMMAND = "python3 -c " + shlex.quote(_REMOTE_EXEC_SCRIPT)


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    output_truncated: bool = False
    error: str = ""


class SSHProtocolError(RuntimeError):
    """The remote helper did not return a valid framed response."""

    def __init__(self, message: str):
        super().__init__(message)
        self.safe_message = message


class SSHChannelOpenError(RuntimeError):
    """Opening a channel failed before any command request was sent."""

    def __init__(self, original: BaseException):
        super().__init__(str(original))
        self.original = original


@dataclass
class _PoolEntry:
    client: Any
    last_used: float
    active_leases: int = 1
    broken: bool = False


def _client_is_active(client: Any) -> bool:
    try:
        transport = client.get_transport()
        return bool(transport is not None and transport.is_active())
    except AttributeError:
        return not bool(getattr(client, "closed", False))
    except Exception:
        return False


def _close_client(client: Any) -> None:
    try:
        client.close()
    except Exception:
        pass


def make_pool_key(host, port, username, password, relay_nodes=None, trust_identity=()):
    """Build a key without retaining a plaintext password in the pool index."""
    secret = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
    relays = None
    if relay_nodes is not None:
        relays = tuple(sorted(
            (
                str(node.get("id") or ""),
                str(node.get("ip") or ""),
                str(node.get("ws_port") or ""),
            )
            for node in relay_nodes
            if isinstance(node, dict)
        ))
    return (
        str(host or "").strip().lower(),
        int(port or 22),
        str(username or "").strip(),
        secret,
        relays,
        tuple(trust_identity or ()),
    )


class SSHClientPool:
    """Share one SSH transport per target; Paramiko opens independent channels."""

    def __init__(
        self, *, max_per_key=2, max_channels_per_client=8,
        idle_timeout=90.0, acquire_timeout=30.0
    ):
        self.max_per_key = max(1, int(max_per_key))
        self.max_channels_per_client = max(1, int(max_channels_per_client))
        self.idle_timeout = max(1.0, float(idle_timeout))
        self.acquire_timeout = max(0.1, float(acquire_timeout))
        self._condition = threading.Condition()
        self._entries: dict[Any, list[_PoolEntry]] = {}
        self._creating: dict[Any, int] = {}
        self._stop_sweeper = threading.Event()
        self._sweeper = threading.Thread(
            target=self._sweep_loop, name="sonar-ssh-pool-cleaner", daemon=True
        )
        self._sweeper.start()

    def _sweep_loop(self):
        interval = max(1.0, min(30.0, self.idle_timeout / 2.0))
        while not self._stop_sweeper.wait(interval):
            self._sweep()

    def _sweep(self):
        now = time.monotonic()
        to_close = []
        with self._condition:
            for key, entries in list(self._entries.items()):
                for entry in list(entries):
                    if entry.active_leases == 0 and (
                        entry.broken
                        or not _client_is_active(entry.client)
                        or now - entry.last_used >= self.idle_timeout
                    ):
                        entries.remove(entry)
                        entry.broken = True
                        to_close.append(entry.client)
                if not entries and self._creating.get(key, 0) == 0:
                    self._entries.pop(key, None)
                    self._creating.pop(key, None)
            self._condition.notify_all()
        for client in to_close:
            _close_client(client)

    def acquire(self, key, factory: Callable[[], Any]) -> _PoolEntry:
        deadline = time.monotonic() + self.acquire_timeout
        while True:
            to_close = []
            chosen = None
            create_new = False
            timed_out = False
            with self._condition:
                entries = self._entries.setdefault(key, [])
                now = time.monotonic()

                # A healthy SSH transport can carry several independent exec
                # channels. Reuse it even while another command is in flight.
                for entry in list(entries):
                    if entry.broken or not _client_is_active(entry.client):
                        entries.remove(entry)
                        entry.broken = True
                        if entry.active_leases == 0:
                            to_close.append(entry.client)
                        continue
                    if entry.active_leases == 0 and now - entry.last_used >= self.idle_timeout:
                        entries.remove(entry)
                        entry.broken = True
                        to_close.append(entry.client)

                available = [
                    entry for entry in entries
                    if entry.active_leases < self.max_channels_per_client
                ]
                if available:
                    chosen = min(available, key=lambda entry: entry.active_leases)
                    chosen.active_leases += 1
                else:
                    creating = self._creating.get(key, 0)
                    if len(entries) + creating < self.max_per_key:
                        self._creating[key] = creating + 1
                        create_new = True
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                        else:
                            self._condition.wait(remaining)

            for client in to_close:
                _close_client(client)
            if chosen is not None:
                return chosen
            if timed_out:
                raise TimeoutError("SSH connection pool acquisition timed out")
            if not create_new:
                continue

            try:
                client = factory()
                if client is None:
                    raise ConnectionError("SSH connection factory returned no client")
                entry = _PoolEntry(client=client, last_used=time.monotonic(), active_leases=1)
            except BaseException:
                with self._condition:
                    self._creating[key] = max(0, self._creating.get(key, 1) - 1)
                    self._condition.notify_all()
                raise

            with self._condition:
                self._entries.setdefault(key, []).append(entry)
                self._creating[key] = max(0, self._creating.get(key, 1) - 1)
                self._condition.notify_all()
            return entry

    def release(self, key, entry: _PoolEntry, *, broken=False) -> None:
        close = False
        with self._condition:
            entries = self._entries.get(key, [])
            if entry.active_leases > 0:
                entry.active_leases -= 1
            if broken or entry.broken or not _client_is_active(entry.client):
                entry.broken = True
                if entry in entries:
                    entries.remove(entry)
                if entry.active_leases == 0:
                    close = True
            elif entry.active_leases == 0:
                entry.last_used = time.monotonic()
            if not entries and self._creating.get(key, 0) == 0:
                self._entries.pop(key, None)
                self._creating.pop(key, None)
            self._condition.notify_all()
        if close:
            _close_client(entry.client)

    def close_all(self) -> None:
        self._stop_sweeper.set()
        if self._sweeper.is_alive() and threading.current_thread() is not self._sweeper:
            self._sweeper.join(timeout=1.0)
        with self._condition:
            entries = [entry for bucket in self._entries.values() for entry in bucket]
            self._entries.clear()
            close_now = []
            for entry in entries:
                entry.broken = True
                if entry.active_leases == 0:
                    close_now.append(entry.client)
            self._condition.notify_all()
        for client in close_now:
            _close_client(client)


def _decode_b64(value, field):
    if not isinstance(value, str):
        raise SSHProtocolError("SSH_REMOTE_RESPONSE_INVALID: " + field)
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception:
        raise SSHProtocolError("SSH_REMOTE_RESPONSE_INVALID: " + field) from None


def _last_nonempty_line(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _read_channel(channel, timeout: float):
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + max(1.0, float(timeout)) + 15.0
    try:
        channel.settimeout(max(1.0, float(timeout)) + 15.0)
        while True:
            progressed = False
            while channel.recv_ready():
                chunk = channel.recv(32768)
                if not chunk:
                    break
                stdout.extend(chunk)
                progressed = True
            while channel.recv_stderr_ready():
                chunk = channel.recv_stderr(32768)
                if not chunk:
                    break
                stderr.extend(chunk)
                progressed = True
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("SSH command response timed out")
            if getattr(channel, "closed", False) and not channel.exit_status_ready():
                raise ConnectionError("SSH command channel closed before exit status")
            if not progressed:
                time.sleep(0.01)
        status = int(channel.recv_exit_status())
        return bytes(stdout), bytes(stderr), status
    except BaseException:
        try:
            channel.close()
        except Exception:
            pass
        raise


def execute_request(client, command: str, timeout: float = 60.0) -> CommandResult:
    """Send one base64 JSON request over stdin and parse one framed response."""
    if not isinstance(command, str) or len(command) > 1024 * 1024 or "\x00" in command:
        raise ValueError("SSH command is invalid or too large")
    budget = float(timeout)
    if not math.isfinite(budget) or budget <= 0 or budget > 1800:
        raise ValueError("SSH command timeout must be between 0 and 1800 seconds")

    request = {
        "op": "run",
        "body": {"command": command, "timeout": budget},
    }
    raw = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw) + b"\n"

    try:
        stdin, stdout, stderr = client.exec_command(REMOTE_EXEC_COMMAND, timeout=budget + 15.0)
    except Exception as exc:
        raise SSHChannelOpenError(exc) from exc

    try:
        stdin.write(encoded)
        stdin.flush()
        stdin.close()
    except Exception as exc:
        try:
            stdout.channel.close()
        except Exception:
            pass
        raise ConnectionError("SSH request could not be sent") from exc

    outer_out, outer_err, outer_status = _read_channel(stdout.channel, budget)
    framed = _last_nonempty_line(outer_out)
    if not framed:
        detail = _last_nonempty_line(outer_err) or f"remote helper exited with status {outer_status}"
        raise SSHProtocolError("SSH_REMOTE_EXECUTOR_FAILED: " + detail[:180])
    try:
        response_raw = base64.b64decode(framed.encode("ascii"), validate=True)
        response = json.loads(response_raw.decode("utf-8"))
    except Exception:
        raise SSHProtocolError("SSH_REMOTE_RESPONSE_INVALID: response frame is not valid base64 JSON") from None
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise SSHProtocolError("SSH_REMOTE_RESPONSE_INVALID: response envelope")
    body = response.get("body")
    if not isinstance(body, dict):
        raise SSHProtocolError("SSH_REMOTE_RESPONSE_INVALID: response body")

    out = _decode_b64(body.get("stdout_b64", ""), "stdout")
    err = _decode_b64(body.get("stderr_b64", ""), "stderr")
    code = body.get("exit_code")
    if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
        raise SSHProtocolError("SSH_REMOTE_RESPONSE_INVALID: exit_code")
    timed_out = body.get("timed_out", False)
    truncated = body.get("output_truncated", False)
    if type(timed_out) is not bool or type(truncated) is not bool:
        raise SSHProtocolError("SSH_REMOTE_RESPONSE_INVALID: status flags")
    if outer_status != 0:
        raise SSHProtocolError("SSH_REMOTE_EXECUTOR_FAILED: remote helper exit status was nonzero")

    return CommandResult(
        ok=response["ok"],
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        exit_code=code,
        timed_out=timed_out,
        output_truncated=truncated,
        error=str(response.get("err") or "")[:160],
    )


class SSHCommandRunner:
    """Runs framed commands over a shared SSH transport with isolated channels."""

    def __init__(self, pool: SSHClientPool | None = None):
        self.pool = pool or SSHClientPool()

    def run(self, key, factory, command, timeout=60.0) -> CommandResult:
        if not isinstance(command, str) or len(command) > 1024 * 1024 or "\\x00" in command:
            raise ValueError("SSH command is invalid or too large")
        budget = float(timeout)
        if not math.isfinite(budget) or budget <= 0 or budget > 1800:
            raise ValueError("SSH command timeout must be between 0 and 1800 seconds")
        entry = self.pool.acquire(key, factory)
        try:
            try:
                result = execute_request(entry.client, command, budget)
            except SSHChannelOpenError:
                # The request body has not been sent yet, so one fresh-channel
                # retry cannot repeat a remote operation.
                self.pool.release(key, entry, broken=True)
                entry = None
                entry = self.pool.acquire(key, factory)
                try:
                    result = execute_request(entry.client, command, budget)
                except SSHChannelOpenError as second:
                    self.pool.release(key, entry, broken=True)
                    entry = None
                    raise second.original
                except Exception:
                    self.pool.release(key, entry, broken=True)
                    entry = None
                    raise
            except SSHProtocolError:
                # A malformed helper response is a protocol problem, not proof
                # that the SSH carrier itself is dead.
                self.pool.release(key, entry, broken=False)
                entry = None
                raise
            except Exception:
                self.pool.release(key, entry, broken=True)
                entry = None
                raise
            self.pool.release(key, entry, broken=False)
            entry = None
            return result
        finally:
            if entry is not None:
                self.pool.release(key, entry, broken=False)

    def close_all(self):
        self.pool.close_all()


GLOBAL_SSH_COMMANDS = SSHCommandRunner()
atexit.register(GLOBAL_SSH_COMMANDS.close_all)
