"""BackPack-style SSH framing, pooling and safe retry regressions."""
import base64
import json
import shlex
import threading
import time
from types import SimpleNamespace

import pytest

from ssh_transport import (
    CommandResult,
    GLOBAL_SSH_COMMANDS,
    REMOTE_EXEC_COMMAND,
    SSHChannelOpenError,
    SSHClientPool,
    SSHCommandRunner,
    SSHProtocolError,
    _REMOTE_EXEC_SCRIPT,
    execute_request,
    make_pool_key,
)


class FakeChannel:
    def __init__(self, stdout=b"", stderr=b"", status=0):
        self.stdout = bytearray(stdout)
        self.stderr = bytearray(stderr)
        self.status = status
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def recv_ready(self):
        return bool(self.stdout)

    def recv(self, size):
        data = bytes(self.stdout[:size])
        del self.stdout[:size]
        return data

    def recv_stderr_ready(self):
        return bool(self.stderr)

    def recv_stderr(self, size):
        data = bytes(self.stderr[:size])
        del self.stderr[:size]
        return data

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.status

    def close(self):
        self.closed = True


class FakeOutput:
    def __init__(self, channel):
        self.channel = channel


class FakeStdin:
    def __init__(self, channel, on_close):
        self.channel = channel
        self.on_close = on_close
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.on_close(bytes(self.data))


class FakeSSHClient:
    def __init__(self, *, open_error=None, response=None, write_error=None):
        self.closed = False
        self.open_error = open_error
        self.response = response or {
            "ok": True,
            "err": "",
            "body": {
                "stdout_b64": base64.b64encode(b"ok").decode(),
                "stderr_b64": base64.b64encode(b"warning").decode(),
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
            },
        }
        self.write_error = write_error
        self.commands = []
        self.request = None
        self.channel = FakeChannel()

    def get_transport(self):
        return SimpleNamespace(is_active=lambda: not self.closed)

    def close(self):
        self.closed = True

    def exec_command(self, command, timeout):
        self.commands.append((command, timeout))
        if self.open_error:
            raise self.open_error

        def on_close(data):
            if self.write_error:
                raise self.write_error
            self.request = json.loads(base64.b64decode(data.strip(), validate=True))
            encoded = base64.b64encode(
                json.dumps(self.response, separators=(",", ":")).encode()
            ) + b"\n"
            self.channel.stdout.extend(encoded)

        stdin = FakeStdin(self.channel, on_close)
        return stdin, FakeOutput(self.channel), FakeOutput(self.channel)


def test_command_is_sent_as_base64_stdin_not_ssh_command_argument():
    client = FakeSSHClient()
    result = execute_request(client, "printf '%s' 'secret; $HOME'", timeout=7)

    assert client.commands[0][0] == REMOTE_EXEC_COMMAND
    assert "secret" not in client.commands[0][0]
    assert client.request == {
        "op": "run",
        "body": {"command": "printf '%s' 'secret; $HOME'", "timeout": 7.0},
    }
    assert result == CommandResult(
        ok=True, stdout="ok", stderr="warning", exit_code=0,
        timed_out=False, output_truncated=False, error=""
    )


def test_remote_helper_has_stdin_envelope_and_noninteractive_sudo():
    assert "base64.b64decode" in _REMOTE_EXEC_SCRIPT
    assert 'request.get("op") != "run"' in _REMOTE_EXEC_SCRIPT
    assert '"sudo", "-n", "--", "/bin/bash", "-c", "exec /bin/bash -s"' in _REMOTE_EXEC_SCRIPT
    assert "start_new_session=True" in _REMOTE_EXEC_SCRIPT
    assert "REMOTE_COMMAND_TIMEOUT" in _REMOTE_EXEC_SCRIPT


def test_response_parser_rejects_invalid_protocol_frame():
    client = FakeSSHClient()
    client.response = {"ok": "yes", "body": {}}
    with pytest.raises(SSHProtocolError, match="response envelope"):
        execute_request(client, "true")


def test_pool_reuses_client_then_retires_broken_client():
    pool = SSHClientPool(max_per_key=1, idle_timeout=90)
    created = []

    def factory():
        client = FakeSSHClient()
        created.append(client)
        return client

    key = ("server",)
    first = pool.acquire(key, factory)
    pool.release(key, first)
    second = pool.acquire(key, factory)
    assert second.client is first.client
    pool.release(key, second, broken=True)
    assert created[0].closed

    third = pool.acquire(key, factory)
    assert third.client is created[1]
    pool.release(key, third)
    pool.close_all()
    assert created[1].closed


def test_pool_reuses_one_ssh_transport_for_parallel_exec_channels():
    pool = SSHClientPool(max_per_key=1, acquire_timeout=0.2)
    key = ("parallel",)
    first = pool.acquire(key, FakeSSHClient)
    second = pool.acquire(key, FakeSSHClient)
    assert first is second
    assert second.active_leases == 2
    pool.release(key, first)
    assert second.active_leases == 1
    pool.release(key, second)
    assert second.active_leases == 0
    pool.close_all()




def test_pool_opens_second_transport_only_after_channel_limit_is_reached():
    pool = SSHClientPool(max_per_key=2, max_channels_per_client=2)
    key = ("channel-limit",)
    first = pool.acquire(key, FakeSSHClient)
    second = pool.acquire(key, FakeSSHClient)
    assert first is second
    third = pool.acquire(key, FakeSSHClient)
    assert third is not first
    pool.release(key, first)
    pool.release(key, second)
    pool.release(key, third)
    pool.close_all()

def test_pool_sweeper_closes_idle_transport_and_frees_relay_slot():
    pool = SSHClientPool(max_per_key=1, idle_timeout=90)
    client = FakeSSHClient()
    entry = pool.acquire(("idle",), lambda: client)
    pool.release(("idle",), entry)
    entry.last_used -= 100
    pool._sweep()
    assert client.closed
    assert not pool._entries.get(("idle",))
    pool.close_all()

def test_runner_retries_once_only_when_channel_open_fails_before_send():
    pool = SSHClientPool(max_per_key=1)
    stale = FakeSSHClient(open_error=OSError("stale channel"))
    healthy = FakeSSHClient()
    created = iter([stale, healthy])
    calls = []

    def factory():
        calls.append(1)
        return next(created)

    runner = SSHCommandRunner(pool)
    result = runner.run(("retry",), factory, "printf ready", timeout=5)

    assert result.stdout == "ok"
    assert len(calls) == 2
    assert stale.closed
    pool.close_all()


def test_runner_does_not_replay_after_request_write_started():
    pool = SSHClientPool(max_per_key=1)
    client = FakeSSHClient(write_error=OSError("broken stdin"))
    calls = []

    def factory():
        calls.append(1)
        return client

    runner = SSHCommandRunner(pool)
    with pytest.raises(ConnectionError, match="could not be sent"):
        runner.run(("no-replay",), factory, "systemctl restart service", timeout=5)
    assert len(calls) == 1
    assert client.closed
    pool.close_all()



def test_runner_does_not_replay_when_response_channel_breaks_after_dispatch():
    pool = SSHClientPool(max_per_key=1)

    class BrokenAfterSendChannel(FakeChannel):
        def recv_ready(self):
            raise OSError("connection dropped after request dispatch")

    class BrokenAfterSendClient(FakeSSHClient):
        def __init__(self):
            super().__init__()
            self.channel = BrokenAfterSendChannel()

    client = BrokenAfterSendClient()
    calls = []

    def factory():
        calls.append(1)
        return client

    runner = SSHCommandRunner(pool)
    with pytest.raises(OSError, match="after request dispatch"):
        runner.run(("no-replay-after-dispatch",), factory, "systemctl restart service", timeout=5)
    assert len(calls) == 1
    assert client.closed
    pool.close_all()

def test_pool_key_changes_with_credentials_relay_set_or_trust_store():
    base = make_pool_key("Example.COM", 22, "root", "one", trust_identity=(("known", 1),))
    assert base != make_pool_key("example.com", 22, "root", "two", trust_identity=(("known", 1),))
    assert base != make_pool_key("example.com", 22, "root", "one", relay_nodes=[{"id": 2}], trust_identity=(("known", 1),))
    assert base != make_pool_key("example.com", 22, "root", "one", trust_identity=(("known", 2),))
