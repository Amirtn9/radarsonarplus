"""Validate public GitHub bootstrap and one-choice full-stack installation."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / 'install.sh').read_text(encoding='utf-8')
README = (ROOT / 'README.md').read_text(encoding='utf-8')


def test_canonical_repo_in_installer():
    assert "GITHUB_REPOSITORY='Amirtn9/radarsonarplus'" in INSTALL
    assert '"$GITHUB_URL"' in INSTALL
    assert 'radarsonarlpus' not in INSTALL


def test_readme_three_line_install():
    assert 'wget https://raw.githubusercontent.com/Amirtn9/radarsonarplus/main/install.sh' in README
    assert 'chmod +x install.sh' in README
    assert './install.sh' in README
    assert 'radarsonarlpus' not in README


def test_one_choice_installs_panel_and_bot():
    body = INSTALL.split('install_app() {', 1)[1].split('configure_web_port()', 1)[0]
    assert re.search(r'systemctl enable --now "\$SERVICE"', body)
    assert re.search(r'\n  install_web\n  STATE_SWITCH=0', body)
    web = INSTALL.split('install_web() {',1)[1].split('set_domain()',1)[0]
    assert 'systemctl restart "$WEB_SERVICE"' in web
    assert 'wait_for_web_health' in web
    health = INSTALL.split('wait_for_web_health() {', 1)[1].split('install_web() {', 1)[0]
    assert '/healthz' in health


def test_version_matches():
    v = (ROOT / 'VERSION').read_text().strip()
    assert v == '6.6.0-rc3'
    assert v in (ROOT / 'settings.py').read_text()


def test_direct_installer_requests_privileges_automatically():
    # The requested wget/chmod/./install.sh flow must not fail just because
    # the user did not prefix the command with sudo.
    assert 'exec sudo -- bash "$SOURCE_DIR/install.sh" "$@"' in INSTALL
    assert '--check|--help|-h) ;;' in INSTALL
    assert "GITHUB_REPOSITORY='Amirtn9/radarsonarplus'" in INSTALL


def test_upgrade_path_keeps_backup_before_code_switch():
    body = INSTALL.split('install_app() {', 1)[1].split('configure_web_port()', 1)[0]
    assert body.index('make_backup') < body.index('prepare_release')
    assert 'Preserving data and upgrading existing installation.' in body
    assert 'STATE_OLD="$BACKUP_DIR/previous-code-' in body
