"""Actual authenticated TLS WebSocket Agent protocol and SSRF boundary."""
import asyncio
from datetime import datetime,timedelta,timezone
import ipaddress
import json
import ssl


def test_agent_service_probe_self_check_and_telemetry_over_verified_wss(tmp_path,monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import websockets
    import monitor_agent as agent
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic loopback Agent')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False).sign(key,hashes.SHA256()))
    certificate=tmp_path/'tls.pem';certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private=tmp_path/'private.key';private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    server_tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_tls.load_cert_chain(certificate,private)
    client_tls=ssl.create_default_context(cadata=certificate.read_text())
    monkeypatch.setattr(agent,'AGENT_TOKEN','synthetic-wss-token-12345')
    monkeypatch.setenv('SONAR_AGENT_TLS_CERT',str(certificate))
    async def run():
        async with websockets.serve(agent.ws_handler,'127.0.0.1',0,ssl=server_tls) as server:
            port=server.sockets[0].getsockname()[1]
            async with websockets.connect(f'wss://127.0.0.1:{port}',ssl=client_tls) as ws:
                await ws.send('synthetic-wss-token-12345')
                await ws.send(json.dumps({'action':'service_probe','kind':'http','target':'http://127.0.0.1/','timeout':1}))
                result=json.loads(await ws.recv());assert result['ok'] is False and result['detail']=='Blocked by address policy'
                await ws.send(json.dumps({'action':'get_stats'}));stats=json.loads(await ws.recv())
                assert stats['capabilities']['service_probe'] and stats['memory_total_bytes']>0 and stats['disk_free_bytes']>=0
                await ws.send(json.dumps({'action':'self_check'}));check=json.loads(await ws.recv())
                assert check['tls_configured'] and check['uptime_sec']>=0
                await ws.send(json.dumps({'action':'run_cmd','cmd':'touch forbidden'}))
                assert json.loads(await ws.recv())['error']=='remote_shell_disabled'
    asyncio.run(run())
