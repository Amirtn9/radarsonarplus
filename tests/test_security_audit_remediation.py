import ast
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_no_bare_except_handlers_remain_in_runtime_python():
    for path in ROOT.rglob('*.py'):
        if 'tests' in path.parts or '__pycache__' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.type is None], path


def test_apt_package_download_requires_sha256_not_md5():
    source = (ROOT / 'agent_bootstrap.py').read_text(encoding='utf-8')
    assert 'SHA256:(?P<digest>[A-Fa-f0-9]{64})' in source
    assert 'hashlib.md5' not in source
    assert 'MD5Sum' not in source


def test_backup_restore_keeps_database_and_encryption_key_as_a_pair():
    source = (ROOT / 'install.sh').read_text(encoding='utf-8')
    assert '"${file}.secret.key"' in source
    assert 'sha256sum "${checksum_files[@]}"' in source
    restore = source.split('restore_dump() {', 1)[1].split('\nuninstall_app() {', 1)[0]
    assert 'Matching .secret.key sidecar is required' in restore
    assert 'sha256sum -c' in restore
    assert 'restore_backup_pair "$now"' in restore
    assert 'verify_restored_secret_store' in restore
    assert 'verify_secret_key.py' in source
    assert '"$STATE_DIR/secret.key"' in restore


def test_private_key_authentication_is_exposed_only_through_web_onboarding():
    html = (ROOT / 'pro_web' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'pro_web' / 'static' / 'web_ops.js').read_text(encoding='utf-8')
    api = (ROOT / 'web_operations.py').read_text(encoding='utf-8')
    ssh = (ROOT / 'ssh_support.py').read_text(encoding='utf-8')
    assert 'new-server-auth-method' in html and 'new-server-private-key' in html
    assert 'auth_method:method' in js and 'private_key:' in js and "data.private_key=''" in js
    assert "Literal['password','private_key']" in api
    assert 'encode_private_key_auth' in api and 'paramiko_auth_kwargs' in ssh


def test_release_bootstrap_fails_closed_without_commit_pin():
    installer = (ROOT / 'install.sh').read_text(encoding='utf-8')
    assert 'Refusing unpinned GitHub bootstrap' in installer
    assert 'GITHUB_BRANCH' not in installer
    assert 'git -C "$_sonar_bootstrap_dir/source" fetch --quiet --no-tags --depth 1 origin "$approved_commit"' in installer


def test_direct_runtime_dependencies_are_exact_pins_and_s3_is_optional():
    runtime = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    web = (ROOT / 'requirements-web.txt').read_text(encoding='utf-8')
    s3 = (ROOT / 'requirements-s3.txt').read_text(encoding='utf-8')
    assert 'boto3' not in runtime and 'boto3==' in s3
    assert 'fastapi==' in web and 'uvicorn==' in web
    for line in runtime.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            assert '==' in line and '>=' not in line and '<' not in line


def test_release_builder_is_byte_for_byte_reproducible(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from build_release import build
    first = tmp_path / 'first.zip'
    second = tmp_path / 'second.zip'
    build(first)
    build(second)
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()


def test_ci_runs_tests_manifest_shell_and_frontend_checks():
    ci = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
    for expected in ('python -m pytest -q', 'python scripts/verify_manifest.py', 'bash -n install.sh', 'node --check', 'scripts/build_release.py'):
        assert expected in ci


def test_no_direct_requests_or_time_sleep_calls_inside_async_functions():
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.async_depth = 0
            self.blocking = []

        def visit_AsyncFunctionDef(self, node):
            self.async_depth += 1
            for child in node.body:
                self.visit(child)
            self.async_depth -= 1

        def visit_FunctionDef(self, node):
            # A normal helper nested in an async function is safe only if the
            # caller explicitly moves it to a worker thread (checked below).
            if self.async_depth:
                return
            self.generic_visit(node)

        def visit_Call(self, node):
            if self.async_depth and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name) and (
                    (owner.id == 'time' and node.func.attr == 'sleep')
                    or owner.id == 'requests'
                ):
                    self.blocking.append((node.lineno, ast.unparse(node.func)))
            self.generic_visit(node)

    for path in ROOT.rglob('*.py'):
        if 'tests' in path.parts or '__pycache__' in path.parts:
            continue
        visitor = Visitor()
        visitor.visit(ast.parse(path.read_text(encoding='utf-8'), filename=str(path)))
        assert not visitor.blocking, (path, visitor.blocking)

    topics = (ROOT / 'topics.py').read_text(encoding='utf-8')
    assert 'run_in_executor(SHARED_EXECUTOR, _dl)' in topics


def test_release_root_is_clean_and_active_version_is_aligned():
    for legacy in ('APPLY_PATCH_FA.txt', 'HOTFIX_README_FA.md', 'PATCH_NOTES_FA.md', 'repair_manifest.sh'):
        assert not (ROOT / legacy).exists()
    assert (ROOT / 'docs' / 'archive' / 'patch-history' / 'README.md').is_file()
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    for path in ('settings.py', 'monitor_agent.py', 'install.sh', 'pro_web/index.html', 'pro_portal.py'):
        assert version in (ROOT / path).read_text(encoding='utf-8'), path


def test_dotfiles_are_part_of_source_manifest():
    manifest = (ROOT / 'MANIFEST_SHA256.txt').read_text(encoding='utf-8')
    assert '.gitattributes' in manifest
    assert '.gitignore' in manifest


def test_restore_key_verifier_checks_encrypted_server_and_agent_credentials(monkeypatch, capsys):
    import importlib.util
    import types

    seen = []

    class FakeSecurity:
        def __init__(self, key_file, secure_parent=True):
            assert secure_parent is True
        def decrypt(self, value):
            seen.append(value)
            return 'verified'

    class FakeCursor:
        def __init__(self):
            self.rows = []
        def execute(self, sql):
            if 'SELECT password FROM servers' in sql:
                self.rows = [{'password': 'gAAAA-server-secret'}]
            elif 'to_regclass' in sql:
                self.rows = [{'relation': 'agent_nodes'}]
            elif 'SELECT agent_token FROM agent_nodes' in sql:
                self.rows = [{'agent_token': 'gAAAA-agent-secret'}]
        def fetchall(self):
            return self.rows
        def fetchone(self):
            return self.rows[0]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class FakeConnection:
        def cursor(self, **kwargs):
            return FakeCursor()
        def close(self):
            pass

    psycopg2 = types.ModuleType('psycopg2')
    psycopg2.connect = lambda **kwargs: FakeConnection()
    extras = types.ModuleType('psycopg2.extras')
    extras.RealDictCursor = object
    monkeypatch.setitem(sys.modules, 'psycopg2', psycopg2)
    monkeypatch.setitem(sys.modules, 'psycopg2.extras', extras)
    monkeypatch.setitem(sys.modules, 'secret_crypto', types.SimpleNamespace(Security=FakeSecurity))
    monkeypatch.setitem(sys.modules, 'settings', types.SimpleNamespace(
        BASE_DIR='/srv/app', KEY_FILE='/srv/app/runtime/secret.key', DB_CONFIG={'dbname': 'test'}
    ))

    spec = importlib.util.spec_from_file_location('verify_secret_key_test', ROOT / 'scripts' / 'verify_secret_key.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0
    output = capsys.readouterr().out
    assert 'server_passwords=1' in output and 'agent_tokens=1' in output
    assert 'gAAAA-' not in output
    assert seen == ['gAAAA-server-secret', 'gAAAA-agent-secret']
