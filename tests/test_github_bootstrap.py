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
    assert '/healthz' in web


def test_version_matches():
    v = (ROOT / 'VERSION').read_text().strip()
    assert v.startswith('6.0.0-')
    assert v in (ROOT / 'settings.py').read_text()
