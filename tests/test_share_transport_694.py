"""Generic share-format regressions; no customer endpoints or credentials."""
import base64
import json
import os
import subprocess
from urllib.parse import urlencode

import pytest

from monitor_agent import parse_xray_config, needs_compatibility_core

UUID='00000000-0000-4000-8000-000000000001'
KEY=base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')


def share_cases():
    # Match the transport combinations reported by the user, with reserved
    # documentation addresses and a synthetic identifier throughout.
    for port,tls,fp,encryption,path in [
        (8880,False,'chrome','none','/'),
        (2095,False,'chrome','none','/sample?ed=2048'),
        (8443,True,'random','mlkem768x25519plus.native.0rtt.'+KEY,'/sample?ed=2048'),
        (8443,True,'safari','none','/sample?ed=2048'),
        (2053,True,'chrome','none','/sample?ed=2048'),
        (2053,False,'chrome','none','/sample?ed=2048'),
        (2086,False,'chrome','none','/sample?ed=2048'),
        (2083,True,'chrome','none','/sample?ed=2048'),
        (2096,True,'chrome','none','/sample?ed=2048')]:
        values=dict(encryption=encryption,security='tls' if tls else 'none',type='ws',path=path,host='example.org',sni='example.org',fp=fp)
        yield 'vless://'+UUID+'@192.0.2.1:'+str(port)+'?'+urlencode(values),values
    for network,port in [('ws',2087),('xhttp',443)]:
        values=dict(add='example.org',id=UUID,port=port,aid='0',net=network,host='example.org',sni='example.org',tls='tls',path='/sample?ed=2048',fp='random',scy='auto',type='auto')
        if network=='xhttp':values['extra']='{"xPaddingBytes":"100-1000"}'
        yield 'vmess://'+base64.b64encode(json.dumps(values).encode()).decode(),values


@pytest.mark.parametrize('link,source',list(share_cases()))
def test_share_options_reach_the_core_without_downgrading(link,source):
    result=parse_xray_config(link)
    assert result is not None
    stream=result['streamSettings']
    net=source.get('net',source.get('type'))
    assert stream['network']==net
    if result['protocol']=='vless':
        assert result['settings']['vnext'][0]['users'][0]['encryption']==source['encryption']
    if net=='ws':
        assert stream['wsSettings']['path']==source['path']
        assert stream['wsSettings']['headers']['Host']==source['host']
    else:
        assert stream['xhttpSettings']==dict(host='example.org',path='/sample?ed=2048',mode='auto',extra={'xPaddingBytes':'100-1000'})
    if stream['security']=='tls':
        assert stream['tlsSettings']['allowInsecure'] is False
        assert stream['tlsSettings']['fingerprint']==source['fp']


@pytest.mark.parametrize('extra',['[]','not-json','"string"'])
def test_malformed_xhttp_options_are_rejected_instead_of_ignored(extra):
    link='vless://'+UUID+'@example.org:443?'+urlencode(dict(type='xhttp',extra=extra))
    assert parse_xray_config(link) is None


def test_vless_xhttp_uri_keeps_mode_host_path_and_extra():
    link='vless://'+UUID+'@example.org:443?'+urlencode(dict(type='xhttp',mode='stream-one',host='front.example.org',path='/upload',extra='{"noGRPCHeader":true}'))
    assert parse_xray_config(link)['streamSettings']['xhttpSettings']==dict(mode='stream-one',host='front.example.org',path='/upload',extra={'noGRPCHeader':True})


@pytest.mark.skipif(not os.environ.get('SONAR_TEST_XRAY_BIN'),reason='Set SONAR_TEST_XRAY_BIN to the verified pinned runtime')
@pytest.mark.parametrize('link,source',list(share_cases()))
def test_real_pinned_core_accepts_synthetic_transport_combinations(link,source,tmp_path):
    config=tmp_path/'synthetic.json'
    outbound=parse_xray_config(link)
    binary=os.environ['SONAR_TEST_XRAY_BIN']
    if needs_compatibility_core(outbound):
        binary=os.getenv('SONAR_TEST_XRAY_COMPAT_BIN')
        if not binary:pytest.skip('Verified plaintext compatibility runtime is required')
    config.write_text(json.dumps({'outbounds':[outbound]}))
    result=subprocess.run([binary,'run','-test','-config',str(config)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
