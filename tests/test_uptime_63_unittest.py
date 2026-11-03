"""Offline behavioral coverage for Sonar 6.3 uptime service (no external deps)."""
import unittest
from unittest.mock import patch
import socket
import ipaddress
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from service_probes import normalize_target, _resolve, check, ProbeResult
from service_monitor import transition, ServiceStore


class TestTargets(unittest.TestCase):
    def test_http_and_tcp_valid(self):
        self.assertEqual(normalize_target('http','https://example.com/health?state=1'),'https://example.com/health?state=1')
        self.assertEqual(normalize_target('tcp','[2001:4860:4860::8888]:443'),'[2001:4860:4860::8888]:443')

    def test_reject_bad_targets(self):
        for kind,addr in [('http','file:///etc/passwd'),('http','http://admin:pass@example.com/'),
                          ('http','https://example.com/#token'),('http','http://example.com:99999'),
                          ('tcp','example.com:0'),('tcp','example.com:65536'),
                          ('tcp','2001:4860:4860::8888:443'),('tcp','127.0.0.1:abc')]:
            with self.subTest(kind=kind,addr=addr),self.assertRaises(ValueError):
                normalize_target(kind,addr)

    def test_private_dns_blocked_by_default(self):
        with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',80))]):
            with self.assertRaises(ValueError): _resolve('localhost',80)
            self.assertEqual(len(_resolve('localhost',80,True)),1)
            failed=check('tcp','localhost:80',1,allow_private=False)
            self.assertFalse(failed.ok)
            self.assertEqual(failed.detail,'Blocked by address policy')

    def test_public_dns_passes_filter(self):
        with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),
                                                      (socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.1',443))]):
            self.assertEqual(len(_resolve('example.com',443)),1)


class TestTransitions(unittest.TestCase):
    def test_down_after_two_failures_and_recovery(self):
        state,bad,good,event='pending',0,0,None
        for ok, expected, alert in [(False,'pending',None),(False,'down','failure'),
                                     (False,'down',None),(True,'up','recovery'),(True,'up',None)]:
            state,bad,good,event=transition(state,bad,good,ok,2,1)
            self.assertEqual((state,event),(expected,alert))

    def test_recovery_threshold_and_pending(self):
        self.assertEqual(transition('down',3,0,True,2,2),('down',0,1,None))
        self.assertEqual(transition('down',0,1,True,2,2),('up',0,2,'recovery'))
        self.assertEqual(transition('pending',0,0,True,2,1),('up',0,1,None))

    def test_pause_does_not_transition(self):
        self.assertEqual(transition('paused',0,0,False),('paused',1,0,None))

    def test_store_forbids_writes_without_role(self):
        class Pro:
            def __init__(self): self.db=object()
            def require(self,actor,owner,write=False):
                if actor!=owner and write:raise PermissionError
        store=ServiceStore(Pro())
        with self.assertRaises(PermissionError):
            store.create(2,1,'api','http','https://example.com')



class TestLocalProbes(unittest.TestCase):
    def test_http_status_and_redirect_handling(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status={'/ok':200,'/error':503,'/redirect':302}.get(self.path,404)
                self.send_response(status)
                if status==302:self.send_header('Location','http://127.0.0.1:9/')
                self.end_headers()
            def log_message(self,*args):pass
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=Thread(target=http.serve_forever,daemon=True);worker.start()
        try:
            port=http.server_port
            up=check('http',f'http://127.0.0.1:{port}/ok',2,allow_private=True)
            self.assertTrue(up.ok);self.assertEqual(up.detail,'HTTP 200')
            self.assertGreaterEqual(up.latency_ms,0)
            down=check('http',f'http://127.0.0.1:{port}/error',2,allow_private=True)
            self.assertFalse(down.ok);self.assertEqual(down.detail,'HTTP 503')
            expected=check('http',f'http://127.0.0.1:{port}/error',2,503,allow_private=True)
            self.assertTrue(expected.ok)
            redirect=check('http',f'http://127.0.0.1:{port}/redirect',2,allow_private=True)
            self.assertTrue(redirect.ok);self.assertEqual(redirect.detail,'HTTP 302')
        finally:
            http.shutdown();http.server_close();worker.join(timeout=2)

    def test_tcp_probe(self):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1',0));listener.listen(2)
            outcome=check('tcp',f'127.0.0.1:{listener.getsockname()[1]}',2,allow_private=True)
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.detail,'TCP connected')

if __name__=='__main__':unittest.main()
