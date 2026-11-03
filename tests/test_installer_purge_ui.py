from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'install.sh'


def test_installer_exposes_full_purge_as_separate_exit_action():
    text = SCRIPT.read_text('utf8')
    help_result = subprocess.run(['bash', str(SCRIPT), '--help'], cwd=ROOT,
                                 text=True, capture_output=True, timeout=10)
    assert help_result.returncode == 0
    assert '--purge' in help_result.stdout
    assert "'15' 'FULL PURGE: delete all Sonar data and EXIT'" in text
    assert "15) if run --purge; then break; fi;;" in text
    assert "DELETE ALL SONAR DATA" in text
    assert "Purge cancelled; no data was deleted.'; return 2" in text
    assert 'purge_remove_tree "$APP_DIR" \'/opt/radar-sonar\'' in text


def test_purge_is_scoped_and_preserves_shared_services():
    text = SCRIPT.read_text('utf8')
    purge = text.split('purge_project() {', 1)[1].split('\n}\nselect_install_strategy()', 1)[0]
    assert "purge_remove_tree \"$STATE_DIR\" '/var/lib/sonar-radar'" in purge
    assert "purge_remove_tree \"$BACKUP_DIR\" '/var/backups/sonar-radar'" in purge
    assert 'purge_remove_file "$INSTALL_LOG"' in purge
    assert 'apt-get remove' not in purge
    assert 'rm -rf /' not in purge
    assert 'Remote PostgreSQL host' in purge
    assert 'Shared PostgreSQL/nginx packages' in purge
    assert 'systemctl stop "$SERVICE" "$WEB_SERVICE"' in purge


def test_installer_ui_has_whiptail_and_safe_fallback():
    text = SCRIPT.read_text('utf8')
    assert 'menu_whiptail()' in text
    assert 'menu_text()' in text
    assert 'command -v whiptail' in text
    assert 'menu_text\n  fi' in text
    assert 'whiptail' in text.split('install_deps() {', 1)[1].split('\n}', 1)[0]


def test_systemd_start_limit_settings_are_in_unit_section():
    text = SCRIPT.read_text('utf8')
    for marker in ('create_unit() {', 'create_web_unit() {'):
        block = text.split(marker, 1)[1].split('\n}', 1)[0]
        unit_section = block.split('[Unit]', 1)[1].split('[Service]', 1)[0]
        assert 'StartLimitIntervalSec=120' in unit_section
        assert 'StartLimitBurst=8' in unit_section
        service_section = block.split('[Service]', 1)[1].split('[Install]', 1)[0]
        assert 'StartLimitIntervalSec' not in service_section
