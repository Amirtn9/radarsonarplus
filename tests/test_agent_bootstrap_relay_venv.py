"""Regressions for Agent installation through an approved SSH relay."""
import pytest

import agent_bootstrap as bootstrap


def test_known_probe_version_is_reused_when_relay_is_intermittent(monkeypatch):
    calls = []
    preflights = iter([
        (False, 'exit=1: VENV_CREATE_PROBE_FAILED: ensurepip missing'),
        (True, 'SONAR_VENV_READY'),
    ])

    def run(command, timeout=60):
        calls.append(command)
        if 'command -v python3' in command:
            return True, 'SYSTEM_TOOLS_READY'
        if 'python3.11-venv' in command or 'python3-venv' in command:
            return True, 'PACKAGES_REPAIRED'
        if 'sys.version_info' in command:
            # This second SSH round-trip used to fail intermittently when the
            # destination was reachable only via an approved Agent relay.
            return False, 'SSH_TIMEOUT: relay timed out'
        return True, 'OK'

    monkeypatch.setattr(bootstrap, '_venv_preflight', lambda run, timeout=35: next(preflights))
    bootstrap._ensure_os_tools(run, None, target_python='3.11')

    assert any('python3.11-venv' in command for command in calls)
    assert not any('sys.version_info' in command for command in calls)


def test_venv_transport_failure_is_not_reported_as_missing_python(monkeypatch):
    calls = []

    def run(command, timeout=60):
        calls.append(command)
        if 'command -v python3' in command:
            return True, 'SYSTEM_TOOLS_READY'
        return True, 'OK'

    monkeypatch.setattr(
        bootstrap,
        '_venv_preflight',
        lambda run, timeout=35: (False, 'SSH_TIMEOUT: approved Agent route temporarily unavailable'),
    )

    with pytest.raises(RuntimeError, match='SSH_TRANSPORT_UNAVAILABLE'):
        bootstrap._ensure_os_tools(run, None, target_python='3.11')

    assert not any('apt-get' in command for command in calls)


def test_version_probe_transport_failure_keeps_transport_error(monkeypatch):
    def run(command, timeout=60):
        if 'command -v python3' in command:
            return True, 'SYSTEM_TOOLS_READY'
        if 'sys.version_info' in command:
            return False, 'SSH_PORT_UNREACHABLE: direct port closed and relay unavailable'
        return True, 'OK'

    monkeypatch.setattr(
        bootstrap,
        '_venv_preflight',
        lambda run, timeout=35: (False, 'VENV_CREATE_PROBE_FAILED: ensurepip missing'),
    )

    with pytest.raises(RuntimeError, match='SSH_TRANSPORT_UNAVAILABLE'):
        bootstrap._ensure_os_tools(run, None)


def test_probe_classifies_transport_failure_without_blaming_python():
    with pytest.raises(RuntimeError, match='SSH_TRANSPORT_UNAVAILABLE'):
        bootstrap.probe(lambda command, timeout=35: (False, 'SSH_TIMEOUT: no direct or relay route'))
