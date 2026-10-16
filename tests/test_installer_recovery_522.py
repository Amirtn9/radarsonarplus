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


def test_runtime_permissions_and_smoke_check_before_start():
    stage = SCRIPT.split('install_app() {', 1)[1].split('configure_web_port() {', 1)[0]
    assert stage.index('chmod -R a+rX "$STATE_STAGE/venv"') < stage.index('systemctl enable --now "$SERVICE"')
    assert stage.index('runuser -u "$APP_USER" -- "$STATE_STAGE/venv/bin/python"') < stage.index('systemctl enable --now "$SERVICE"')
    assert 'systemd-analyze verify "$BOT_UNIT"' in stage
    assert 'journalctl -u "$SERVICE" -n 60' in stage


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
