"""Deployment manager tests that run WITHOUT systemd, sudo, apt, network or real credentials."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(['bash', str(ROOT / 'install.sh'), *args],
                          cwd=ROOT, text=True, capture_output=True, timeout=25)


def test_installer_syntax_and_public_cli():
    result=subprocess.run(['bash', '-n', str(ROOT/'install.sh'), str(ROOT/'install_web_panel.sh')],
                          capture_output=True, text=True)
    assert result.returncode==0, result.stderr
    result=run('--help')
    assert result.returncode==0
    for term in ('--install', '--update', '--panel', '--domain', '--tls', '--backup', '--restore',
                 '--uninstall', '--doctor', '--trust-ssh', '--set', '--credentials'):
        assert term in result.stdout


def test_preflight_no_secrets_and_compiles_sources():
    result=run('--check')
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'Validated' in result.stdout
    assert 'Preflight' in result.stdout or 'PREFLIGHT' in result.stdout


def test_source_release_defaults_are_unprivileged():
    x=json.loads((ROOT/'sonar_config.example.json').read_text('utf8'))
    assert not x['bot_token']
    assert x['admin_id']=='0'
    gitignore=(ROOT/'.gitignore').read_text('utf8')
    for item in ('sonar_config.json','secret.key','.env','*.dump','venv/','*.pem'):
        assert item in gitignore
    assert not (ROOT/'secret.key').exists()


def test_web_local_only_and_cert_provisioned_via_acme():
    script=(ROOT/'install.sh').read_text('utf8')
    assert '--host 127.0.0.1' in script
    assert 'certbot "${args[@]}"' in script
    assert 'SONAR_WEBAPP_URL' in script
    assert 'valid_domain' in script
    assert 'nginx -t' in script
    assert 'NoNewPrivileges=true' in script
    assert 'User=$APP_USER' in script


def test_rollbacks_backup_and_restore_are_guarded():
    script=(ROOT/'install.sh').read_text('utf8')
    assert "trap rollback EXIT" in script
    assert 'STATE_SWITCH=1' in script
    assert 'runuser -u postgres -- pg_dump -Fc' in script
    assert 'pg_restore --list' in script
    assert 'Type RESTORE DATABASE' in script
    assert 'UNINSTALL SONAR' in script
    assert 'Preparing a verified pre-upgrade' not in script or 'make_backup' in script
    assert 'STATE_OLD' in script


def test_agent_does_not_use_insecure_archive_extraction_or_shell_curl():
    script=(ROOT/'monitor_agent.py').read_text('utf8')
    segment=script.split('def install_xray():',1)[1].split('# ==============================================================================',1)[0]
    assert '"--fail"' in segment or "'--fail'" in segment
    assert '"--location"' in segment or "'--location'" in segment
    assert 'extractall(' not in segment
    assert 'shell=True' not in segment
    testing=script.split('def test_config_logic(',1)[1].split('def ',1)[0]
    assert 'shell=True' not in testing
