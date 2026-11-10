"""Local HTTP -> SOCKS -> real VLESS/WS core -> HTTP, with/without TLS/routing."""
import json
import os
import socket
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

import monitor_agent as agent

LATEST=os.getenv('SONAR_TEST_XRAY_BIN')
COMPAT=os.getenv('SONAR_TEST_XRAY_COMPAT_BIN')
pytestmark=pytest.mark.skipif(not LATEST or not COMPAT,reason='Both checksum-verified Xray cores are required')
UUID='00000000-0000-4000-8000-000000000001'


@pytest.mark.parametrize('tls,routed,compat',[(False,False,False),(True,False,False),
    (False,True,False),(True,True,False),(False,False,True)])
@pytest.mark.parametrize("transport", ["ws", "xhttp"])
def test_real_proxy_traffic_with_tls_plaintext_and_profile_routing(tmp_path,monkeypatch,tls,routed,compat,transport):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    calls=[]
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(204);self.end_headers()
        def log_message(self,*args):pass
    health=ThreadingHTTPServer(('127.0.0.1',0),Health)
    worker=threading.Thread(target=health.serve_forever,daemon=True);worker.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));server_port=sock.getsockname()[1]
    protocol='vmess' if transport=='xhttp' else 'vless'
    stream={'network':transport,'security':'tls' if tls else 'none',
            'wsSettings' if transport=='ws' else 'xhttpSettings':{'path':'/fixture'}}
    if tls:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'cdn.fixture.test')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
              .not_valid_after(now+timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('cdn.fixture.test')]),False)
              .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
        (tmp_path/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tmp_path/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        stream['tlsSettings']={'certificates':[{'certificateFile':str(tmp_path/'cert.pem'),'keyFile':str(tmp_path/'key.pem')}]}
    access=tmp_path/'server-access.log'
    server_config={'log':{'loglevel':'debug','access':str(access),'error':str(tmp_path/'server-error.log')},'inbounds':[{
        'port':server_port,'listen':'127.0.0.1','protocol':protocol,
        'settings':{'clients':[{'id':UUID}],'decryption':'none'},'streamSettings':stream}],
        # Current core blocks private destinations by default. Permit only
        # this fixture's exact loopback HTTP endpoint on its ephemeral port.
        'outbounds':[{'protocol':'freedom','settings':{'finalRules':[
            {'action':'allow','network':'tcp','ip':['127.0.0.1/32'],'port':str(health.server_port)}]}}]}
    (tmp_path/'server.json').write_text(json.dumps(server_config))
    (tmp_path/'xray').symlink_to(LATEST)
    (tmp_path/'geoip.dat').touch()
    (tmp_path/'compat').mkdir();(tmp_path/'compat'/'xray').symlink_to(COMPAT)
    monkeypatch.setattr(agent,'WORK_DIR',str(tmp_path));monkeypatch.setattr(agent,'XRAY_BIN',str(tmp_path/'xray'))
    monkeypatch.setattr(agent,'_health_urls',lambda:(f'http://127.0.0.1:{health.server_port}/generate_204',))
    original_builder=agent.build_probe_config
    def diagnostic_builder(profile,port):
        config=original_builder(profile,port)
        config['log']={'loglevel':'debug','error':str(tmp_path/'probe-error.log')}
        return config
    monkeypatch.setattr(agent,'build_probe_config',diagnostic_builder)
    if compat:monkeypatch.setattr(agent,'needs_compatibility_core',lambda profile:True)
    values={'type':transport,'security':'tls' if tls else 'none','host':'cdn.fixture.test','path':'/fixture'}
    if transport=='xhttp':
        import base64
        values.update(add='127.0.0.1',port=str(server_port),id=UUID,aid='0',net='xhttp',tls='tls' if tls else '')
        outbound=agent.parse_xray_config('vmess://'+base64.b64encode(json.dumps(values).encode()).decode().rstrip('='))
    else:
        values['path']='/fixture?ed=2048'
        outbound=agent.parse_xray_config(f'vless://{UUID}@127.0.0.1:{server_port}?'+urlencode(values))
    if tls:
        assert outbound['streamSettings']['tlsSettings']['serverName']=='cdn.fixture.test'
        assert outbound['streamSettings']['tlsSettings']['allowInsecure'] is False
        outbound['streamSettings']['tlsSettings']['certificates']=[{'certificateFile':str(tmp_path/'cert.pem'),'usage':'verify'}]
    profile=outbound
    if routed:
        profile={'inbounds':[{'tag':'desktop-socks','port':1080}],
                 'outbounds':[{'protocol':'blackhole','tag':'blocked'},outbound],
                 'dns':{'servers':['localhost']},
                 'routing':{'rules':[{'type':'field','inboundTag':['desktop-socks'],'outboundTag':'proxy'}]}}
    server=subprocess.Popen([LATEST,'run','-config',str(tmp_path/'server.json')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        assert agent.check_port_open(server_port), 'Local Xray fixture failed to start'
        result=agent.test_config_logic(profile,0)
        assert result['status']=='OK',result
        assert result['ping']>0
        assert result['core_mode']==('compatibility' if compat else 'latest')
        assert result['measurement_scope']==('profile_route' if routed else 'proxy')
        assert len(calls)==3
        assert '127.0.0.1:'+str(health.server_port) in access.read_text()
        assert not list(tmp_path.glob('config_*.json'))
    finally:
        server.terminate();server.wait(timeout=5)
        health.shutdown();health.server_close();worker.join(timeout=2)
