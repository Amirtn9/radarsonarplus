"""Fail-closed recovery contract; no destructive real-server commands run in tests."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'install.sh').read_text(encoding='utf-8')


def test_systemd_limits_live_in_unit_section_not_service_section():
    bot = SCRIPT.split('create_unit() {', 1)[1].split('rollback() {', 1)[0]
    web = SCRIPT.split('create_web_unit() {', 1)[1].split('install_web() {', 1)[0]
    for unit in (bot, web):
        assert unit.index('StartLimitIntervalSec=120') < unit.index('[Service]')
        assert unit.index('StartLimitBurst=8') < unit.index('[Service]')
        assert 'User=$APP_USER' in unit
        assert 'Group=$APP_USER' in unit
        assert 'ProtectSystem=strict' in unit


def test_runtime_permissions_and_prestart_checks_before_service_enable():
    stage = SCRIPT.split('install_app() {', 1)[1].split('configure_web_port() {', 1)[0]
    assert 'normalize_venv_permissions "$STATE_STAGE/venv"' in stage
    assert 'normalize_venv_permissions "$APP_DIR/venv"' in stage
    assert stage.index('runuser -u "$APP_USER" -- "$STATE_STAGE/venv/bin/python"') < stage.index('systemctl enable --now "$SERVICE"')
    assert 'systemd-analyze verify "$BOT_UNIT"' in stage
    assert 'ExecStartPre=/usr/bin/test -x $APP_DIR/venv/bin/python' in SCRIPT
    assert 'diagnose_service_exec "$APP_DIR/venv/bin/python"' in SCRIPT
    assert '[[ "$main_status" == 203 || "$active_state" == failed ]]' in SCRIPT
    assert 'journalctl -u "$SERVICE" -n 60 --no-pager' in stage


def test_venv_creation_uses_non_secret_umask_and_restores_private_umask():
    create = SCRIPT.split('create_venv() {', 1)[1].split('ensure_db() {', 1)[0]
    assert 'previous_umask="$(umask)"' in create
    assert 'umask 022' in create
    assert 'umask "$previous_umask"' in create
    assert 'chmod 0600 "$STATE_STAGE/secret.key"' in create
    helper = SCRIPT.split('normalize_venv_permissions() {', 1)[1].split('prepare_runtime_state() {', 1)[0]
    assert 'find "$venv" -type d -exec chmod 0755 {} +' in helper
    assert 'findmnt -no OPTIONS -T "$venv/bin/python"' in helper
    assert 'grep -qx noexec' in helper


def test_clean_reset_is_scoped_confirmed_and_database_backed_up():
    reset = SCRIPT.split('reset_project_components() {',1)[1].split('select_install_strategy() {',1)[0]
    for snippet in ('DELETE SONAR DATA', 'archive_existing_components',
                    'runuser -u postgres -- dropdb --if-exists "$DB_NAME"',
                    'dropuser "$DB_USER"', '"$APP_DIR" == /opt/radar-sonar',
                    'PostgreSQL unreachable; refusing unsafe reset'):
        assert snippet in reset
    assert 'rm -rf /opt/' not in reset
    assert 'rm -rf /var/lib/postgresql' not in SCRIPT


def test_full_install_and_help_recovery():
    assert '--clean-install)' in SCRIPT
    assert '14  Clean reset + reinstall' in SCRIPT
    assert 'spinner_run' in SCRIPT
    out = subprocess.run(['bash', str(ROOT/'install.sh'), '--help'], capture_output=True, text=True)
    assert out.returncode == 0
    assert '--clean-install' in out.stdout
    assert '--inventory' in out.stdout


def test_python_venv_umask_077_permission_fix_proven():
    if os.geteuid() != 0 or not Path('/usr/sbin/runuser').exists():
        return
    capability=subprocess.run(['runuser','-u','nobody','--','true'],capture_output=True)
    if capability.returncode != 0:
        pytest.skip('Container cannot change supplementary groups for runuser')
    with tempfile.TemporaryDirectory(prefix='sonar-venv-perms-',dir='/tmp') as tmp:
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', tmp + '/venv'], check=True)
        # tempfile and a 077-created venv deny a non-root service user traversal.
        os.chmod(tmp,0o755)
        before = subprocess.run(['runuser','-u','nobody','--',tmp+'/venv/bin/python','-c','print(1)'], capture_output=True)
        # System's default umask may vary, so explicitly model the affected venv dirs.
        subprocess.run(['chmod','-R','go-rX',tmp+'/venv'],check=True)
        blocked = subprocess.run(['runuser','-u','nobody','--',tmp+'/venv/bin/python','-c','print(1)'], capture_output=True)
        assert blocked.returncode != 0
        subprocess.run(['chmod','-R','a+rX',tmp+'/venv'],check=True)
        allowed = subprocess.run(['runuser','-u','nobody','--',tmp+'/venv/bin/python','-c','print(1)'], capture_output=True)
        assert allowed.returncode == 0, allowed.stderr.decode(errors='replace')


def test_startup_waits_for_database_and_completes_migrations_before_workers():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "async def _wait_for_database(db)" in bot
    assert "SELECT 1 AS ready" in bot
    assert "SONAR_DB_STARTUP_ATTEMPTS" in bot
    assert "await _wait_for_database(db)" in bot
    assert "async def _startup_step(name, callback, *args)" in bot
    assert "from schema_migrations import run_startup_migrations" in bot
    assert 'await _startup_step("schema_migrations", run_startup_migrations, db)' in bot
    assert "All startup migrations completed; enterprise workers started." in bot
    assert 'journalctl -u "$SERVICE" -n 60 --no-pager' in SCRIPT

def test_web_unit_execstart_is_separate_and_runtime_state_is_writable():
    web = SCRIPT.split('create_web_unit() {', 1)[1].split('wait_for_web_health() {', 1)[0]
    assert r'\nExecStart=' not in web
    assert '\nExecStart=$APP_DIR/venv/bin/python -m uvicorn pro_portal:app' in web
    assert 'ReadWritePaths=$LOG_DIR $STATE_DIR' in web
    assert 'import pro_portal; print("WEB_IMPORT_PREFLIGHT_OK")' in SCRIPT
    assert 'systemd-analyze verify "$WEB_UNIT" >> "$INSTALL_LOG" 2>&1 || die' in SCRIPT

