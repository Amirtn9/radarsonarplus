"""Safe port ownership preflight tests, no remote VPS required."""
import ast
from pathlib import Path

# Test the pure diagnostic function in isolation; do not shadow real ssh_support.
_source=Path(__file__).resolve().parents[1]/'agent_deploy.py'
_tree=ast.parse(_source.read_text(encoding='utf8'))
_node=next(n for n in _tree.body if isinstance(n,ast.FunctionDef) and n.name=='agent_port_preflight')
_ns={}
exec(compile(ast.Module(body=[_node],type_ignores=[]),str(_source),'exec'),_ns)
agent_port_preflight=_ns['agent_port_preflight']

def test_available_port():
    ok, status=agent_port_preflight(lambda command, timeout: (True, 'SONAR_PORT_AVAILABLE'),8080)
    assert ok and status=='SONAR_PORT_AVAILABLE'


def test_existing_managed_agent():
    ok, status=agent_port_preflight(lambda command, timeout: (True, 'SONAR_PORT_MANAGED_AGENT'),8080)
    assert ok


def test_other_process_refused():
    ok, status=agent_port_preflight(lambda command, timeout: (False, 'exit=1: AGENT_PORT_CONFLICT: another process is listening on the Agent port'),8080)
    assert not ok and 'AGENT_PORT_CONFLICT' in status


def test_diagnostic_does_not_leak_process_arguments():
    ok, status=agent_port_preflight(lambda command, timeout: (False, 'exit=1: ss failed: --secret=top-secret'),8080)
    assert not ok and 'top-secret' not in status


def test_bad_preflight_never_passes():
    ok,status=agent_port_preflight(lambda command, timeout: (True, ''),8080)
    assert not ok and 'CHECK_FAILED' in status


def test_port_argument_is_not_shell_interpolated():
    seen=[]
    agent_port_preflight(lambda command, timeout: (seen.append(command) or True,'SONAR_PORT_AVAILABLE'),8080)
    assert 'python3 - 8080' in seen[0]
    assert "<<'SONAR_PORT_PREFLIGHT'" in seen[0]


def test_preflight_checks_systemd_cgroup_not_only_main_pid():
    source=_source.read_text(encoding='utf8')
    assert "--property=ControlGroup" in source
    assert "pathlib.Path('/proc',pid,'cgroup')" in source
    assert 'not all(managed_pid(pid) for pid in pids)' in source


def test_port_selector_uses_requested_port_when_free_and_unreserved():
    tree = ast.parse(_source.read_text(encoding='utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'select_available_agent_port')
    ns = {'agent_port_preflight': lambda run, port: (True, 'SONAR_PORT_AVAILABLE'),
          'valid_agent_port': lambda value: int(value)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(_source), 'exec'), ns)
    port, status = ns['select_available_agent_port'](lambda *a: (True, 'unused'), 8080,
                                                      reserved=lambda candidate: False)
    assert port == 8080 and status == 'SONAR_PORT_AVAILABLE'


def test_port_selector_falls_back_without_killing_other_listener():
    tree = ast.parse(_source.read_text(encoding='utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'select_available_agent_port')
    calls = []
    def preflight(run, port):
        calls.append(port)
        if port in (8080, 5542):
            return False, f'AGENT_PORT_CONFLICT: {port} occupied'
        return True, 'SONAR_PORT_AVAILABLE'
    ns = {'agent_port_preflight': preflight, 'valid_agent_port': lambda value: int(value)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(_source), 'exec'), ns)
    port, status = ns['select_available_agent_port'](lambda *a: (True, 'unused'), 8080,
                                                      reserved=lambda candidate: False)
    assert port == 8443
    assert 'AGENT_PORT_AUTO_SELECTED' in status
    assert calls == [8080, 5542, 8443]


def test_port_selector_skips_database_reserved_ports():
    tree = ast.parse(_source.read_text(encoding='utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'select_available_agent_port')
    ns = {'agent_port_preflight': lambda run, port: (True, 'SONAR_PORT_AVAILABLE'),
          'valid_agent_port': lambda value: int(value)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(_source), 'exec'), ns)
    port, status = ns['select_available_agent_port'](lambda *a: (True, 'unused'), 8080,
                                                      reserved=lambda candidate: candidate in (8080, 5542))
    assert port == 8443
    assert 'AGENT_PORT_AUTO_SELECTED' in status
