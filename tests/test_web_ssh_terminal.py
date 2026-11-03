import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ssh_terminal import make_router
from web_operations import make_router as make_operations_router


class DummyStore:
    pass


def test_browser_ssh_terminal_websocket_route_is_registered():
    app = FastAPI()
    app.include_router(make_router(DummyStore(), 123))
    assert any(getattr(route, "path", None) == "/api/terminal/{server_id}"
               for route in app.routes)


def test_browser_ssh_terminal_rejects_cross_origin_before_auth(monkeypatch):
    monkeypatch.setenv("SONAR_WEB_ORIGIN", "https://sonar.example")
    app = FastAPI()
    app.include_router(make_router(DummyStore(), 123))
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/api/terminal/1",
            headers={"origin": "https://attacker.example"}
        ):
            pass
    assert exc.value.code == 4403


def test_agent_tls_reenrollment_endpoint_is_registered():
    app = FastAPI()
    app.include_router(make_operations_router(DummyStore(), lambda: 1, lambda fn, *a, **k: None, 123))
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/operations/server/{server_id}/agent/re-enroll" in paths


def test_terminal_ssh_error_text_is_english(monkeypatch):
    import sys
    from types import ModuleType
    from ssh_terminal import _terminal_ssh_error

    fake = ModuleType("ssh_support")
    fake.describe_ssh_error = lambda exc: "SSH_TIMEOUT: localized source text"
    monkeypatch.setitem(sys.modules, "ssh_support", fake)
    message = _terminal_ssh_error(TimeoutError())
    assert message == "SSH connection timed out."
    assert not any("\u0600" <= ch <= "\u06ff" for ch in message)


def test_same_host_origin_is_allowed_without_explicit_origin_setting(monkeypatch):
    from types import SimpleNamespace
    from ssh_terminal import _origin_is_allowed

    monkeypatch.delenv("SONAR_WEB_ORIGIN", raising=False)
    ws = SimpleNamespace(
        headers={
            "origin": "https://sonar.example",
            "host": "sonar.example",
            "x-forwarded-proto": "https",
        },
        url=SimpleNamespace(scheme="ws"),
    )
    assert _origin_is_allowed(ws)


def test_same_host_origin_rejects_host_mismatch(monkeypatch):
    from types import SimpleNamespace
    from ssh_terminal import _origin_is_allowed

    monkeypatch.delenv("SONAR_WEB_ORIGIN", raising=False)
    ws = SimpleNamespace(
        headers={
            "origin": "https://attacker.example",
            "host": "sonar.example",
            "x-forwarded-proto": "https",
        },
        url=SimpleNamespace(scheme="ws"),
    )
    assert not _origin_is_allowed(ws)


def test_installer_contains_dedicated_websocket_upgrade_location():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "install.sh").read_text(encoding="utf-8")
    assert "location ^~ /api/terminal/" in source
    assert "proxy_set_header Upgrade \\$http_upgrade;" in source
    assert 'proxy_set_header Connection "upgrade";' in source
    assert "proxy_read_timeout 3600s;" in source


def test_terminal_ui_copy_is_english():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "pro_web/static/ssh-terminal.js").read_text(encoding="utf-8")
    for ch in source:
        assert not ("\u0600" <= ch <= "\u06ff")
