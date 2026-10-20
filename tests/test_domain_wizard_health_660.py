from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "install.sh").read_text(encoding="utf-8")


def test_web_health_waits_for_service_readiness_and_emits_diagnostics():
    health = SCRIPT.split("wait_for_web_health() {", 1)[1].split("install_web() {", 1)[0]
    assert "systemctl is-active --quiet \"$WEB_SERVICE\"" in health
    assert "for ((attempt=1; attempt<=timeout; attempt++))" in health
    assert '"http://127.0.0.1:${port}/healthz"' in health
    assert 'systemctl status "$WEB_SERVICE" --no-pager -l' in health
    assert 'journalctl -u "$WEB_SERVICE" -n 80 --no-pager' in health
    assert 'systemd unit port=$unit_port' in health


def test_domain_wizard_repairs_panel_before_tls_and_waits_after_restart():
    wizard = SCRIPT.split("configure_domain_https() (", 1)[1].split("set_telegram_credentials()", 1)[0]
    assert 'cp -p "$WEB_UNIT" "$backup/sonar-web.service"' in wizard
    assert 'if ((oldunit));then cp -p "$backup/sonar-web.service" "$WEB_UNIT"' in wizard
    assert wizard.index("install_web") < wizard.index('issue_tls "$domain" "$email"')
    assert 'wait_for_web_health "$api_port" 30' in wizard
    assert 'systemctl daemon-reload' in wizard
    assert 'oldbotactive=0 oldwebactive=0' in wizard
    assert 'if ((oldwebactive));then systemctl restart "$WEB_SERVICE"' in wizard


def test_version_is_rc3_consistently_declared():
    version = (ROOT / "VERSION").read_text().strip()
    assert version == "6.6.0-rc3"
    assert version in (ROOT / "settings.py").read_text()
    assert version in (ROOT / "monitor_agent.py").read_text()
