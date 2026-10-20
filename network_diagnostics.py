"""Bounded independent ICMP diagnosis via Check-Host.

ICMP reachability does NOT establish TCP/22, SSH trust or Agent readiness.
Never gate the Agent fallback on this third-party API: network filtering often
blocks one vantage point but not another.
"""
from __future__ import annotations
import ipaddress
import time
import requests


def checkhost_ping(host):
    try:
        target=ipaddress.ip_address(str(host))
        if not target.is_global:return 'unsupported_target'
        headers={'Accept':'application/json','User-Agent':'SonarRadar/6.6'}
        response=requests.get('https://check-host.net/check-ping',
                              params={'host':str(target),'max_nodes':5},
                              headers=headers,timeout=4)
        response.raise_for_status()
        request_id=response.json().get('request_id')
        if not isinstance(request_id,str) or not request_id or len(request_id)>160:
            return 'unknown'
        for attempt in range(2):
            if attempt:time.sleep(1)
            result=requests.get('https://check-host.net/check-result/'+request_id,
                                headers=headers,timeout=4)
            result.raise_for_status()
            raw=result.json()
            if not isinstance(raw,dict):continue
            # Actual API replies can be null while pending or a list of
            # attempts; only explicit 'OK' proves successful ICMP response.
            for node_attempts in raw.values():
                if isinstance(node_attempts,list):
                    for attempt_item in node_attempts:
                        if isinstance(attempt_item,list) and attempt_item and attempt_item[0]=='OK':
                            return 'ping_reachable'
        return 'unknown'
    except (requests.RequestException,ValueError,TypeError,KeyError):
        return 'unknown'
