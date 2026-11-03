"""Regression tests for SSH error formatting and fallback handling."""
import ast
import importlib.util
import logging
from pathlib import Path
import sys
import textwrap
import types

ROOT = Path(__file__).resolve().parents[1]


def _load_ssh_support(monkeypatch, *, expose_ssh_exception=True):
    fake_paramiko = types.ModuleType("paramiko")

    class BadHostKeyException(Exception):
        pass

    class AuthenticationException(Exception):
        pass

    class NoValidConnectionsError(OSError):
        pass

    fake_paramiko.BadHostKeyException = BadHostKeyException
    fake_paramiko.AuthenticationException = AuthenticationException
    if expose_ssh_exception:
        fake_paramiko.ssh_exception = types.SimpleNamespace(
            NoValidConnectionsError=NoValidConnectionsError
        )
    else:
        # Some custom/mocked/older module layouts do not expose this namespace.
        fake_paramiko.NoValidConnectionsError = NoValidConnectionsError

    fake_settings = types.ModuleType("settings")
    fake_settings.KNOWN_HOSTS_FILE = "/tmp/sonar-test-known-hosts"
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)
    monkeypatch.setitem(sys.modules, "settings", fake_settings)

    spec = importlib.util.spec_from_file_location(
        "ssh_support_errors_test_module", ROOT / "ssh_support.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, NoValidConnectionsError, AuthenticationException


def test_describe_ssh_error_handles_paramiko_without_ssh_exception_namespace(monkeypatch):
    support, no_valid_connections, _ = _load_ssh_support(
        monkeypatch, expose_ssh_exception=False
    )
    message = support.describe_ssh_error(no_valid_connections("connection refused"))
    assert message.startswith("SSH_PORT_UNREACHABLE:")


def test_describe_ssh_error_does_not_raise_for_name_error(monkeypatch):
    support, _, _ = _load_ssh_support(monkeypatch)
    message = support.describe_ssh_error(NameError("NoValidConnectionsError is not defined"))
    assert message.startswith("SSH_CONNECTION_FAILED: NameError")


def test_describe_ssh_error_keeps_known_error_mappings(monkeypatch):
    support, _, authentication_error = _load_ssh_support(monkeypatch)
    assert support.describe_ssh_error(authentication_error("bad credentials")).startswith(
        "AUTH_FAILED:"
    )
    assert support.describe_ssh_error(TimeoutError("timeout")).startswith("SSH_TIMEOUT:")


def test_run_ssh_command_contains_secondary_error_formatter_failure(monkeypatch):
    source = (ROOT / "core.py").read_text(encoding="utf-8")
    module_ast = ast.parse(source)
    monitor_class = next(
        node for node in module_ast.body
        if isinstance(node, ast.ClassDef) and node.name == "ServerMonitor"
    )
    function = next(
        node for node in monitor_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_ssh_command"
    )

    def fail_connection(*args, **kwargs):
        raise ConnectionError("synthetic SSH failure")

    generated = "class ServerMonitor:\n    get_ssh_client = staticmethod(GET_CLIENT)\n"
    generated += textwrap.indent(ast.unparse(function), "    ")
    scope = {
        "GET_CLIENT": fail_connection,
        "paramiko": types.SimpleNamespace(AuthenticationException=type("AuthError", (Exception,), {})),
        "describe_ssh_error": lambda exc: (_ for _ in ()).throw(
            NameError("synthetic formatter regression")
        ),
        "logger": logging.getLogger("test.sonar.ssh"),
    }
    exec(generated, scope)
    ok, detail = scope["ServerMonitor"]._run_ssh_command(
        "127.0.0.1", 22, "test", "secret", "true"
    )
    assert ok is False
    assert detail == "SSH_CONNECTION_FAILED: ConnectionError"


def test_server_add_preflight_name_error_is_reported_and_not_reraised(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    source = (ROOT / "bot_logic_mod" / "servers.py").read_text(encoding="utf-8")
    module_ast = ast.parse(source)
    function = next(
        node for node in module_ast.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_complete_server_add"
    )

    fake_relay = types.ModuleType("ssh_relay")
    fake_relay.eligible_relay_nodes = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "ssh_relay", fake_relay)

    async def run_sync(*args, **kwargs):
        raise NameError("synthetic undefined symbol")

    source_code = ast.unparse(function)
    namespace = {
        "valid_agent_port": lambda value: int(value),
        "AGENT_PORT": 8080,
        "ConversationHandler": types.SimpleNamespace(END=-1),
        "run_sync": run_sync,
        "db": object(),
        "SUPER_ADMIN_ID": 999,
        "ServerMonitor": types.SimpleNamespace(),
        "sec": types.SimpleNamespace(decrypt=lambda value: value),
        "describe_ssh_error": lambda exc: f"SSH_CONNECTION_FAILED: {type(exc).__name__}",
        "logger": logging.getLogger("test.sonar.server-add"),
    }
    exec("async def _complete_server_add(update, context):\n" +
         textwrap.indent(source_code.split("\n", 1)[1] if source_code.startswith("async def") else source_code, "    "),
         namespace)

    status = types.SimpleNamespace(edit_text=AsyncMock())
    update = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=12),
        effective_message=types.SimpleNamespace(reply_text=AsyncMock(return_value=status)),
    )
    context = types.SimpleNamespace(user_data={
        "srv": {"name": "test", "ip": "192.0.2.10", "port": 22,
                "username": "root", "password": "encrypted", "location_type": "ir"},
    })

    result = asyncio.run(namespace["_complete_server_add"](update, context))
    assert result == -1
    status.edit_text.assert_awaited_once()
    assert "SSH_CONNECTION_FAILED: NameError" in status.edit_text.await_args.args[0]
    assert "ذخیره نشد" in status.edit_text.await_args.args[0]
