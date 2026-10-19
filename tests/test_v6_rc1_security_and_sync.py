"""Portable, network-independent regression tests for V6 RC1 safety guarantees."""
import asyncio
import base64
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_sensitive_bundle_patterns_redact_without_reversible_hashes():
    from diagnostics import sanitize
    cases={
      'SONAR_DB_PASSWORD=NopeABC99':'NopeABC99',
      'SONAR_AGENT_TOKEN=topsecret':'topsecret',
      'SONAR_BOT_TOKEN=bot-secret':'bot-secret',
      'client_secret=xyz':'xyz',
      'agent_token: token12345':'token12345',
      'owner_id=123456789':'123456789',
      'server=2001:db8::1':'2001:db8::1',
      'connect 1.2.3.4':'1.2.3.4',
      'origin server.example.com':'server.example.com',
    }
    for line,secret in cases.items():
        sanitized=sanitize(line)
        assert secret not in sanitized, (line,sanitized)
        assert 'SHA256' not in sanitized


def test_singbox_reality_ws_utls_does_not_silently_downgrade():
    from monitor_agent import parse_subscription_links,parse_xray_config
    data={'outbounds':[{'type':'vless','tag':'reality','server':'example.org',
       'server_port':443,'uuid':'00000000-0000-0000-0000-000000000001',
       'transport':{'type':'ws','path':'/fast','headers':{'Host':'edge.example.org'}},
       'tls':{'enabled':True,'server_name':'sni.example.org',
              'reality':{'enabled':True,'public_key':'key123','short_id':'0a1b'},
              'utls':{'enabled':True,'fingerprint':'chrome'}}}]}
    urls=parse_subscription_links(json.dumps(data))
    assert len(urls)==1
    out=parse_xray_config(urls[0]['link'])
    assert out['streamSettings']['security']=='reality'
    assert out['streamSettings']['network']=='ws'
    assert out['streamSettings']['wsSettings']['path']=='/fast'
    assert out['streamSettings']['realitySettings']['publicKey']=='key123'
    assert out['streamSettings']['realitySettings']['shortId']=='0a1b'


def test_singbox_grpc_tls_and_clash_vm_grpc_service_name():
    from monitor_agent import parse_subscription_links,parse_xray_config
    data={'outbounds':[{'type':'vless','tag':'grpc','server':'example.org',
      'server_port':443,'uuid':'00000000-0000-0000-0000-000000000002',
      'transport':{'type':'grpc','service_name':'myservice'},
      'tls':{'enabled':True,'server_name':'edge.example.org', 'alpn':['h2']}}]}
    config=parse_xray_config(parse_subscription_links(json.dumps(data))[0]['link'])
    assert config['streamSettings']['grpcSettings']['serviceName']=='myservice'
    assert config['streamSettings']['tlsSettings']['alpn']==['h2']
    clash='''proxies:
      - type: vmess
        name: gRPC
        server: proxy.example.org
        port: 443
        uuid: 00000000-0000-0000-0000-000000000003
        network: grpc
        grpc-opts:
          grpc-service-name: real-service
        tls: true
    '''
    vm=parse_xray_config(parse_subscription_links(clash)[0]['link'])
    assert vm['streamSettings']['grpcSettings']['serviceName']=='real-service'


def test_trojan_url_encoding_and_shadowsocks_ipv6():
    from monitor_agent import parse_xray_config
    trojan=parse_xray_config('trojan://pass%40word%3A123@example.org:443?security=tls')
    assert trojan['settings']['servers'][0]['password']=='pass@word:123'
    credentials=base64.urlsafe_b64encode(b'aes-256-gcm:my-password').decode().rstrip('=')
    ss=parse_xray_config('ss://'+credentials+'@[2001:db8::1]:8443')
    assert ss['settings']['servers'][0]['address']=='2001:db8::1'
    assert ss['settings']['servers'][0]['password']=='my-password'
    assert parse_xray_config('ss://'+credentials+'@host.example:443?plugin=v2ray-plugin') is None


def test_reject_unsafe_clash_mappings_instead_of_false_healthy():
    from monitor_agent import parse_subscription_links
    yaml='''proxies:
      - name: insecure
        type: vless
        server: edge.example
        port: 443
        uuid: abc
        tls: true
        skip-cert-verify: true
    '''
    assert not parse_subscription_links(yaml)


def test_invalid_subscription_does_not_delete_previous(monkeypatch):
    from test_regressions import _load_isolated_tunnel
    mod=_load_isolated_tunnel(monkeypatch)
    t=mod.TunnelLogic()
    saved=[]
    t._bulk_save_sub_items=lambda *args,**kwargs:saved.append((args,kwargs))
    async def fetch(*a):return False,'HTTP_STATUS_403',None
    t._fetch_sub_links=fetch
    r=asyncio.run(t.process_sub_async(1,{'name':'a','id':5,'link':'https://test'},{}))
    assert not saved and r[0].startswith('❌')


def test_timeout_and_unknown_still_enter_authoritative_membership(monkeypatch):
    from test_regressions import _load_isolated_tunnel
    mod=_load_isolated_tunnel(monkeypatch)
    t=mod.TunnelLogic()
    saved=[]
    t._bulk_save_sub_items=lambda uid,subid,rows,**kw: (
        saved.append((rows,kw)) or {'added':len(rows),'kept':0,'removed':0})
    async def fetch(*a):return True,[{'name':'a','link':'vless://a'}, {'name':'b','link':'vless://b'}],{'complete':True}
    async def test(_monitor,link,**kw):
        if link.endswith('b'):raise asyncio.TimeoutError()
        return True,{'status':'OK','ping':50,'score':7}
    t._fetch_sub_links=fetch
    t._test_via_ws=test
    asyncio.run(t.process_sub_async(12,{'name':'source','id':8,'link':'https://a'},{}))
    assert len(saved[0][0])==2
    assert {row[7] for row in saved[0][0]}=={'OK','Unknown'}
    assert saved[0][1]['allow_delete'] is True


def test_missing_complete_flag_never_permits_deletion(monkeypatch):
    from test_regressions import _load_isolated_tunnel
    mod=_load_isolated_tunnel(monkeypatch)
    t=mod.TunnelLogic()
    saved=[]
    t._bulk_save_sub_items=lambda uid,sid,rows,**kw: (
        saved.append(kw) or {'added':len(rows),'kept':0,'removed':0})
    async def fetch(*a):return True,[{'name':'a','link':'vless://a'}],{'total':1}
    async def test(*a,**kw):return False,None
    t._fetch_sub_links=fetch
    t._test_via_ws=test
    asyncio.run(t.process_sub_async(1,{'name':'n','link':'https://x','id':1},{}))
    assert saved[0]['allow_delete'] is False


def test_no_qr_third_party_and_no_destructive_tmp_cleanup():
    assert 'api.qrserver.com' not in (ROOT/'bot_logic_mod/tunnels.py').read_text()
    assert 'rm -rf /tmp/*' not in (ROOT/'core.py').read_text()


def test_doctor_does_not_block_ordered_updates():
    source=(ROOT/'v6_ui.py').read_text()
    assert 'background_callback(diag_zip)' in source
    disp=(ROOT/'dispatcher.py').read_text()
    assert "CallbackQueryHandler(background_callback(bot_logic.update_monitor_node)" in disp
    assert "CallbackQueryHandler(background_callback(bot_logic.delete_monitor_node)" in disp


def test_auth_nonascii_token_gets_rejected_without_traceback():
    import websockets
    import monitor_agent as m
    async def run():
        m.AGENT_TOKEN='A'*48
        async with websockets.serve(m.ws_handler,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1]
            async with websockets.connect(f'ws://127.0.0.1:{port}') as client:
                await client.send('❌')
                with pytest.raises(websockets.exceptions.ConnectionClosed):
                    await client.recv()
    asyncio.run(run())


def test_source_manifest_preparation_and_sftp_deploy_guard():
    code=(ROOT/'agent_deploy.py').read_text()
    assert 'ssh.open_sftp()' in code
    assert "SONAR_AGENT_PY'" not in code
    assert '--support' in (ROOT/'install.sh').read_text()
    assert 'verify_restore_rehearsal' in (ROOT/'install.sh').read_text()


def test_agent_fetch_retries_public_ipv4_after_ipv6_timeout(monkeypatch):
    import monitor_agent as m
    import socket, ssl, http.client
    calls=[]
    original_getaddrinfo=socket.getaddrinfo
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,**kw:[
        (socket.AF_INET6,socket.SOCK_STREAM,0,'',('2606:4700::1111',port,0,0)),
        (socket.AF_INET,socket.SOCK_STREAM,0,'',('1.1.1.1',port))])
    class Socket:
        def close(self):pass
    def connect(addr,timeout=None):
        calls.append(addr[0])
        if ':' in addr[0]:raise TimeoutError('IPv6 unavailable')
        return Socket()
    monkeypatch.setattr(socket,'create_connection',connect)
    class TLS:
        def wrap_socket(self,sock,server_hostname=None):return sock
    monkeypatch.setattr(ssl,'create_default_context',lambda:TLS())
    def request(self,*args,**kwargs): self.connect()
    class Response:
        status=200
        def read(self,n):return b'vless://uuid@host.example.org:443'
    monkeypatch.setattr(http.client.HTTPSConnection,'request',request)
    monkeypatch.setattr(http.client.HTTPSConnection,'getresponse',lambda self:Response())
    assert m.fetch_url('https://example.org:8443/sub',raise_errors=True).startswith(b'vless://')
    assert calls==['2606:4700::1111','1.1.1.1']


def test_fetch_rejects_nonpublic_mixed_dns_without_connecting(monkeypatch):
    import socket
    import monitor_agent as m
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[
       (socket.AF_INET,socket.SOCK_STREAM,0,'',('1.1.1.1',443)),
       (socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1',443))])
    def fail(*args,**kw):raise AssertionError('Must not connect')
    monkeypatch.setattr(socket,'create_connection',fail)
    with pytest.raises(m.SubscriptionFetchError,match='NON_PUBLIC_DNS_RESULT'):
        m.fetch_url('https://example.org/sub',raise_errors=True)


def test_db_authoritative_sync_keeps_stable_ids_and_real_pruning(monkeypatch):
    from test_regressions import _load_isolated_tunnel
    from contextlib import contextmanager
    mod=_load_isolated_tunnel(monkeypatch)
    t=mod.TunnelLogic()
    class DB:
        items={'vless://remain':{'id':101,'status':'OK','ping':36},
               'vless://removed':{'id':102,'status':'OK','ping':42}}
        last_id=200
        @contextmanager
        def get_connection(self):
            yield self,self
        def execute(self,sql,args=None):
            if 'UPDATE tunnel_configs SET name' in sql:
                link=args[-1]
                self.items[link]['status']='Unknown' if "last_status='Unknown'" in sql else args[2]
            if 'DELETE FROM tunnel_configs' in sql:
                for link in args[-1]:self.items.pop(link,None)
        def fetchone(self):return {'id':7}
        def fetchall(self):return [{'link':name} for name in self.items]
        def commit(self):pass
    db=DB()
    t.db=db
    def insert(cur,sql,rows):
        for r in rows:
            db.last_id+=1
            db.items[r[3]]={'id':db.last_id,'status':r[7],'ping':r[8]}
    monkeypatch.setattr(mod,'execute_values',insert)
    row=lambda link,status: (1,7,'sub_item',link,'a','2026-10-16',0,status,None,None,None,None)
    result=t._bulk_save_sub_items(1,7,[row('vless://remain','Unknown'),row('vless://new','Fail')],allow_delete=True)
    assert result=={'kept':1,'added':1,'removed':1}
    assert db.items['vless://remain']['id']==101
    assert db.items['vless://remain']['ping']==36
    assert 'vless://removed' not in db.items
    assert db.items['vless://new']['status']=='Fail'
    before=dict(db.items)
    result=t._bulk_save_sub_items(1,7,[row('vless://remain','Unknown')],allow_delete=False)
    assert result['removed']==0 and 'vless://new' in db.items


def test_sensitive_http_urls_are_scrubbed():
    from diagnostics import sanitize
    value=sanitize('Referer: https://private-server.example.org:8443/sub?user=secret')
    assert 'private-server.example.org' not in value
    assert 'user=secret' not in value
    assert '[REDACTED_URL]' in value


def test_legacy_subscription_handlers_enforce_ownership():
    source=(ROOT/'bot_logic_mod/tunnels.py').read_text()
    assert 'SELECT name FROM tunnel_configs WHERE id=%s AND owner_id=%s' in source
    assert '(sub_id, shared_uid(update))' in source
    assert 'api.qrserver.com' not in source
    assert 'UPDATE tunnel_configs SET last_status=\'Fail\' WHERE id=%s AND owner_id=%s' in source


def test_agent_fetch_uses_strict_completeness():
    source=(ROOT/'monitor_agent.py').read_text()
    assert 'len(results) < 600' in source
    assert 'supported = all(' in source
    assert 'len(results)<=600' not in source


def test_release_metadata_consistent():
    version=(ROOT/'VERSION').read_text().strip()
    assert version in (ROOT/'settings.py').read_text()
    assert version in (ROOT/'install.sh').read_text()
    assert version in (ROOT/'pro_web/index.html').read_text()
