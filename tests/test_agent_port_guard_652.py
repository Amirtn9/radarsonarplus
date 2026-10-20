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
