"""Synthetic profiles only; real core tests never dial customer endpoints."""
import base64
import copy
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import pytest

import monitor_agent as agent

UUID='00000000-0000-4000-8000-000000000001'


def vless(**values):
    return 'vless://'+UUID+'@192.0.2.1:443?'+urlencode(values)


def test_sni_defaults_to_ws_host_without_changing_dial_address():
    outbound=agent.parse_xray_config(vless(type='ws',security='tls',host='cdn.example.org',path='/socket'))
    assert outbound['settings']['vnext'][0]['address']=='192.0.2.1'
    assert outbound['streamSettings']['tlsSettings']['serverName']=='cdn.example.org'
    assert outbound['streamSettings']['tlsSettings']['allowInsecure'] is False
    explicit=agent.parse_xray_config(vless(type='ws',security='tls',host='cdn.example.org',sni='tls.example.org'))
    assert explicit['streamSettings']['tlsSettings']['serverName']=='tls.example.org'


@pytest.mark.parametrize('tls,expected', [('', 'none'), ('none','none'), (False,'none'),('false','none'),('tls','tls'),(True,'tls')])
def test_vmess_tls_flags(tls,expected):
    values={'add':'example.org','port':443,'id':UUID,'net':'ws','tls':tls,'host':'cdn.example.org'}
    link='vmess://'+base64.b64encode(json.dumps(values).encode()).decode()
    assert agent.parse_xray_config(link)['streamSettings']['security']==expected


def test_grpc_and_trojan_xhttp_transport_options_are_preserved():
    result=agent.parse_xray_config(vless(type='grpc',security='tls',serviceName='my-service',mode='multi'))
    assert result['streamSettings']['grpcSettings']=={'serviceName':'my-service','multiMode':True}
    uri='trojan://synthetic-password@example.org:443?'+urlencode(dict(type='xhttp',security='tls',path='/up',mode='stream-one',extra='{"noGRPCHeader":true}'))
    result=agent.parse_xray_config(uri)
    assert result['streamSettings']['network']=='xhttp'
    assert result['streamSettings']['xhttpSettings']['extra']=={'noGRPCHeader':True}


def test_full_json_subscription_and_probe_keep_routing_dns_and_chain():
    profile={'remarks':'routed profile','inbounds':[{'tag':'desktop-socks','listen':'0.0.0.0','port':1080}],
             'outbounds':[{'protocol':'blackhole','tag':'blocked'},
                          agent.parse_xray_config(vless(type='ws',security='tls',host='example.org'))],
             'routing':{'rules':[{'type':'field','inboundTag':['desktop-socks'],'outboundTag':'proxy'}]},
             'dns':{'servers':['localhost']},'api':{'tag':'api','services':['HandlerService']}}
    original=copy.deepcopy(profile)
    items=agent.parse_subscription_links(json.dumps(profile))
    assert len(items)==1
    parsed=agent.parse_xray_config(items[0]['link'])
    config=agent.build_probe_config(parsed,18081)
    for field in ('routing','dns','outbounds'):assert config[field]==profile[field]
    assert config['inbounds'][0]['tag']=='desktop-socks'
    assert config['inbounds'][0]['listen']=='127.0.0.1'
    assert config['inbounds'][0]['port']==18081
    assert 'api' not in config
    assert profile==original


def test_share_probe_cannot_accidentally_bypass_proxy_through_geoip_rules():
    config=agent.build_probe_config(agent.parse_xray_config(vless(type='ws',security='none')),18081)
    assert 'routing' not in config
    assert len(config['outbounds'])==1
    assert config['outbounds'][0]['protocol']=='vless'


def test_plaintext_is_explicitly_scoped_to_compatibility_core():
    assert agent.needs_compatibility_core(agent.parse_xray_config(vless(type='ws',security='none')))
    assert not agent.needs_compatibility_core(agent.parse_xray_config(vless(type='ws',security='tls')))
    encrypted=agent.parse_xray_config(vless(type='ws',security='none',encryption='mlkem768x25519plus.native.0rtt.synthetic'))
    assert not agent.needs_compatibility_core(encrypted)
    localhost=agent.parse_xray_config(vless(type='ws',security='none').replace('192.0.2.1','127.0.0.1'))
    assert not agent.needs_compatibility_core(localhost)


@pytest.mark.parametrize('exitcode,http_code', [(0,'403'),(0,'500'),(28,'000'),(6,'000'),(60,'000'),(97,'000')])
def test_target_failures_do_not_claim_config_is_broken(tmp_path,monkeypatch,exitcode,http_code):
    (tmp_path/'xray').touch();(tmp_path/'geoip.dat').touch()
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path))
    monkeypatch.setattr(agent,'XRAY_BIN',str(tmp_path/'xray'))
    monkeypatch.setattr(agent,'check_port_open',lambda *a:True)
    monkeypatch.setattr(agent,'_lowprio_prefix',lambda:[])
    class Process:
        def poll(self):return None
        def kill(self):pass
        def wait(self,**kwargs):pass
    monkeypatch.setattr(agent.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(agent.subprocess,'run',lambda cmd,**kw:subprocess.CompletedProcess(cmd,exitcode,stdout=http_code+' 0.1',stderr=''))
    result=agent.test_config_logic(agent.parse_xray_config(vless(type='ws',security='tls')),0)
    assert result['status']=='Unknown'
    assert result['error_code']
    assert len(result['probes'])==3
    assert not agent._TEST_PORTS
    assert not list(tmp_path.glob('config_*.json'))


def test_unknown_installed_runtime_is_not_reported_as_expected_version(tmp_path,monkeypatch):
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path))
    versions=agent.runtime_versions()
    assert versions['xray_version'] is None
    assert versions['xray_expected_version']=='v26.10.31'


@pytest.mark.parametrize('status',['200','204','301','302'])
def test_successful_http_response_does_not_require_exactly_204(tmp_path,monkeypatch,status):
    (tmp_path/'xray').touch();(tmp_path/'geoip.dat').touch()
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path));monkeypatch.setattr(agent,'XRAY_BIN',str(tmp_path/'xray'))
    monkeypatch.setattr(agent,'check_port_open',lambda *a:True)
    class Process:
        def poll(self):return None
        def kill(self):pass
        def wait(self,**kwargs):pass
    monkeypatch.setattr(agent.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(agent.subprocess,'run',lambda cmd,**kw:subprocess.CompletedProcess(cmd,0,stdout=status+' 0.1',stderr=''))
    result=agent.test_config_logic(agent.parse_xray_config(vless(type='ws',security='tls')),0)
    assert result['status']=='OK'
    assert result['ping']==100


def test_offline_transfer_includes_both_verified_archives(monkeypatch):
    import agent_xray_bridge as bridge
    import agent_bootstrap
    lock=json.loads((Path(__file__).resolve().parents[1]/'agent_runtime.lock.json').read_text())
    calls=[]
    monkeypatch.setattr(agent_bootstrap,'probe',lambda *a:{'arch':'x86_64'})
    monkeypatch.setattr(bridge,'_sftp_bundle',lambda *a:'/tmp/synthetic-transfer')
    def fetch(record,dest,architecture):
        calls.append((record['version'],Path(dest).name))
        return record['assets'][architecture]['sha256']
    monkeypatch.setattr(bridge,'fetch_pinned_archive',fetch)
    commands=[]
    def run(command,timeout):commands.append(command);return True,'ok'
    result=bridge.install_xray_via_main(run,'192.0.2.1',22,'test','',lock)
    assert result[0]=='/var/lib/sonar-agent/.sonar-runtime.zip'
    assert calls==[('v26.10.31','runtime.zip'),('v26.6.27','compat-runtime.zip')]
    assert '.sonar-compat-runtime.zip' in commands[0]
