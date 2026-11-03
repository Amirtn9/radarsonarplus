"""Fast offline Sonar v6 tests: safe diagnostics and parser compatibility."""
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from diagnostics import sanitize, build_bundle
from monitor_agent import parse_subscription_links, parse_xray_config


def test_diagnostic_redacts_telegram_tokens_and_links():
    token = '123456789:ABCdEFgHIJkLmNOPqrSTUvwxYZabcDEFghi'
    line=f'token={token} password=hunter2 Bearer veryLongSecretWithManyLetters12345 vless://SECRET@1.2.3.4:443?key=abc'
    cleaned=sanitize(line)
    for secret in (token,'hunter2','veryLongSecretWithManyLetters12345','vless://SECRET'):
        assert secret not in cleaned
    assert '[REDACTED' in cleaned


def test_bundle_has_redacted_bounded_contents(tmp_path, monkeypatch):
    import diagnostics
    monkeypatch.setattr(diagnostics,'log_source',lambda kind: 'TOKEN=secretPass007 vless://uuid@8.8.8.8:443\n')
    monkeypatch.setattr(diagnostics,'snapshot',lambda: {'project_version':'6.0.0','token_configured': True})
    path=build_bundle(tmp_path/'support.zip')
    with zipfile.ZipFile(path) as z:
        assert sorted(z.namelist())==sorted(['README.txt','health.json','logs/bot.log','logs/web.log','logs/errors.log','logs/install.log','logs/structured.log'])
        for name in z.namelist():
            contents=z.read(name).decode()
            assert 'secretPass007' not in contents
            assert 'vless://uuid' not in contents
    assert path.stat().st_mode & 0o777 == 0o600


def test_parse_clash_yaml_vless_and_trojan():
    content='''proxies:
  - name: Tehran-1
    type: vless
    server: public.example
    port: 443
    uuid: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
    tls: true
    network: ws
    ws-opts:
      path: /websocket
      headers:
        Host: edge.example
  - name: Backup
    type: trojan
    server: public.example
    port: 443
    password: supersecret
  - name: ignored
    type: hysteria2
    server: public.example
    port: 443
'''
    result=parse_subscription_links(content)
    assert len(result)==2
    assert result[0]['name']=='Tehran-1'
    assert result[0]['link'].startswith('vless://')
    assert parse_xray_config(result[0]['link'])['protocol']=='vless'
    assert parse_xray_config(result[1]['link'])['protocol']=='trojan'


def test_parse_singbox_json_shadowsocks():
    data=json.dumps({'outbounds':[{'type':'shadowsocks','tag':'a','server':'example.org',
           'server_port':443,'method':'aes-128-gcm','password':'s3cret'},
           {'type':'direct','tag':'direct'}]})
    result=parse_subscription_links(data)
    assert len(result)==1
    assert result[0]['name']=='a'
    assert parse_xray_config(result[0]['link'])['protocol']=='shadowsocks'


def test_plain_subscription_deduplicates_and_limits():
    items='\n'.join(['vless://uid@example.org:443?security=tls#A']*5+['trojan://key@example.org:443#B'])
    assert len(parse_subscription_links(items))==2
    assert len(parse_subscription_links(items,limit=1))==1


def test_unknown_protocol_skipped():
    assert parse_subscription_links('hysteria2://a@example.com:443\n') == []


def test_layout_exists():
    html=(ROOT/'pro_web'/'index.html').read_text()
    for section in ('overview','team','audit','settings','support'):
        assert f'id="{section}"' in html
