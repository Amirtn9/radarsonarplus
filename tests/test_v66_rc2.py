"""Offline acceptance checks for WSS SSH relay / root Agent metering.

These tests intentionally don't contact registered users' servers.
"""
import asyncio
import ipaddress
from pathlib import Path
from types import SimpleNamespace

import pytest
from ssh_relay import RelaySocket, retryable_direct_error, valid_relay_destination
from agent_metering import CountingWS, snapshot, record_download, record_tx, record_rx
from agent_traffic import validated_usage, record_sample, reset_meter


def test_relay_destination_rejects_ssrf_and_unapproved_ports(monkeypatch):
    monkeypatch.setenv('SONAR_AGENT_RELAY_PORTS','22,2222')
    for host in ('127.0.0.1','10.0.0.1','::1','169.254.169.254','localhost',
                 '192.168.1.1','192.0.2.1','0.0.0.0'):
        with pytest.raises(ValueError):valid_relay_destination(host,22)
    with pytest.raises(ValueError):valid_relay_destination('8.8.8.8',8000)
    assert valid_relay_destination('8.8.8.8',22)==('8.8.8.8',22)


def test_ssh_relay_only_for_network_errors():
    for code in ('SSH_TIMEOUT: timed out','SSH_PORT_UNREACHABLE: filtered',
                 'SSH_CONNECTION_REFUSED: no service'):
        assert retryable_direct_error(code)
    for code in ('AUTH_FAILED: bad password','HOST_KEY_CHANGED: stale',
                 'HOST_KEY_UNKNOWN: fingerprint required','SSH_INPUT_INVALID'):
        assert not retryable_direct_error(code)


class FakeWS:
    def __init__(self,inputs):self.inputs=list(inputs);self.outputs=[];self.closed=False
    def recv(self,timeout=None):return self.inputs.pop(0)
    def send(self,value):self.outputs.append(value)
    def close(self):self.closed=True


def test_relay_socket_preserves_binary_stream_and_bounds_chunk_size():
    ws=FakeWS([b'12345',b'xyz'])
    sock=RelaySocket(ws,'8.8.8.8',22)
    assert sock.recv(2)==b'12'
    assert sock.recv(3)==b'345'
    assert sock.recv(3)==b'xyz'
    sock.sendall(b'X'*70000)
    assert len(ws.outputs)==2
    assert max(map(len,ws.outputs))<=65536
    assert b''.join(ws.outputs)==b'X'*70000
    sock.close();assert ws.closed


def test_meter_wss_binary_utf8_and_speed_payload():
    old=snapshot()
    class Underlying:
        async def send(self,value):self.sent=value
        async def recv(self):return 'سلام'
        async def close(self,*a,**k):pass
        async def __aiter__(self):
            yield b'\x00\xff'
    async def exercise():
        ws=CountingWS(Underlying())
        await ws.send(b'abc')
        assert await ws.recv()=='سلام'
        got=[]
        async for message in ws:got.append(message)
        assert got==[b'\x00\xff']
    asyncio.run(exercise())
    record_download(197)
    current=snapshot()
    assert current['tx_bytes']>=old['tx_bytes']+3
    assert current['rx_bytes']>=old['rx_bytes']+len('سلام'.encode())+2
    assert current['download_bytes']>=old['download_bytes']+197
    assert validated_usage({'agent_usage':current}) is not None
    assert validated_usage({'agent_usage':dict(current,download_bytes=-1)}) is None
    assert validated_usage({'agent_usage':dict(current,instance='spoof')}) is None


class Cursor:
    def __init__(self):
        self.data=None
        self.epoch=0
        self.writes=[]
        self.query=''
    def execute(self,query,args=None):
        self.query=query
        self.writes.append((query,args))
        if 'UPDATE monitor_agent_meter SET boot_id' in query:
            self.data={'boot_id':args[0],'tx_total':args[1],'rx_total':args[2],
                       'test_total':args[3],'reset_epoch':self.epoch}
        elif 'INSERT INTO monitor_agent_meter\n' in query:
            self.data={'boot_id':args[1],'tx_total':args[2],
                       'rx_total':args[3],'test_total':args[4],'reset_epoch':self.epoch}
        elif 'UPDATE monitor_agent_meter SET reset_epoch' in query:
            if self.data:self.epoch+=1;self.data['reset_epoch']=self.epoch
    def fetchone(self):
        if 'SELECT a.id FROM agent_nodes' in self.query:return {'id':20}
        if 'SELECT * FROM monitor_agent_meter' in self.query:return self.data
        if 'UPDATE monitor_agent_meter SET reset_epoch' in self.query:
            return {'reset_epoch':self.epoch} if self.data else None
    def __enter__(self):return self
    def __exit__(self,*args):pass


class Conn:
    def __init__(self):self.cursor=Cursor();self.commits=0
    def __enter__(self):return self,self.cursor
    def __exit__(self,*args):pass
    def commit(self):self.commits+=1
    def rollback(self):pass


class DB:
    def __init__(self):self.conn=Conn()
    def get_connection(self):return self.conn


def usage(boot,tx,rx,down):
    return {'agent_usage':{'instance':boot,'rx_bytes':rx,'tx_bytes':tx,
           'download_bytes':down,'scope':'agent_wss_relay_and_test_payloads'}}


def test_meter_deltas_dedupe_counter_restart_and_reset():
    db=DB();boot='a'*32;newboot='b'*32
    assert record_sample(db,1,usage(boot,120,200,60))
    assert record_sample(db,2,usage(boot,120,200,60))
    assert record_sample(db,1,usage(boot,140,210,80))
    assert reset_meter(db,1,20)==1
    assert record_sample(db,1,usage(newboot,7,9,11))
    samples=[args for sql,args in db.conn.cursor.writes if 'INSERT INTO monitor_agent_meter_daily' in sql and 'test_bytes)' in sql]
    assert samples==[(20,0,120,200,60),(20,0,20,10,20),(20,1,7,9,11)]
    assert db.conn.commits==5


@pytest.mark.asyncio
async def test_agent_ssh_relay_binary_roundtrip(monkeypatch):
    import monitor_agent
    import ssh_relay
    delivered=[]
    async def server(reader,writer):
        message=await reader.read(100)
        writer.write(message[::-1]);await writer.drain()
        writer.close();await writer.wait_closed()
    listener=await asyncio.start_server(server,'127.0.0.1',0)
    port=listener.sockets[0].getsockname()[1]
    monkeypatch.setattr(ssh_relay,'valid_relay_destination',lambda host,requested:('127.0.0.1',port))
    class TestWebSocket:
        def __init__(self):self.to_agent=asyncio.Queue();self.sent=[]
        async def send(self,value):
            self.sent.append(value)
            if isinstance(value,bytes):delivered.append(value)
        async def recv(self):return await self.to_agent.get()
    ws=TestWebSocket()
    await ws.to_agent.put(b'abcdef')
    async with listener:
        await asyncio.wait_for(monitor_agent._serve_ssh_relay(ws,{'target':'8.8.8.8','port':22}),timeout=3)
    assert delivered==[b'fedcba']
    assert 'relay_ready' in ws.sent[0]
