"""Agent-side, process-lifetime WSS/relay traffic counters.

This is measured application traffic only, NOT total NIC traffic or the
bytes downloaded by an Xray subprocess. The boot ID lets DB deltas survive
counter resets without claiming unobserved traffic.
"""
import secrets
import threading

INSTANCE = secrets.token_hex(16)
_LOCK = threading.Lock()
_RX = 0
_TX = 0
_DOWN = 0


def record_rx(n):
    global _RX
    with _LOCK: _RX += max(0,int(n))


def record_tx(n):
    global _TX
    with _LOCK: _TX += max(0,int(n))


def record_download(n):
    """Count observed subscription / speed-test response bodies."""
    global _DOWN
    with _LOCK:_DOWN += max(0,int(n))


def snapshot():
    with _LOCK:
        return {'instance': INSTANCE, 'rx_bytes': _RX, 'tx_bytes': _TX,
                'download_bytes': _DOWN, 'scope': 'agent_wss_relay_and_test_payloads'}


class CountingWS:
    def __init__(self, ws):self.ws=ws
    async def recv(self):
        v=await self.ws.recv()
        record_rx(len(v.encode('utf-8')) if isinstance(v,str) else len(v))
        return v
    async def send(self, value):
        await self.ws.send(value)
        record_tx(len(value.encode('utf-8')) if isinstance(value,str) else len(value))
    async def close(self,*args,**kwargs):return await self.ws.close(*args,**kwargs)
    async def __aiter__(self):
        async for value in self.ws:
            record_rx(len(value.encode('utf-8')) if isinstance(value,str) else len(value))
            yield value
