"""Separate connectivity health from server capacity. No LLM/paid service needed."""
from math import isfinite

def _clamp(n, low, high): return max(low,min(high,n))

def _optional_number(value,maximum=None):
    try: value=float(value)
    except (TypeError,ValueError,OverflowError):return None
    if not isfinite(value) or value<0:return None
    return min(value,maximum) if maximum is not None else value

def score_config(ping, jitter, ok, uptime_percent=None, download_mbps=None, success_rate=None, sample_count=None):
    if not ok:return 0
    try: p=float(ping); j=float(jitter)
    except (ValueError,TypeError,OverflowError):return 0
    if not all(isfinite(n) for n in (p,j)) or p<=0 or j<0:return 0
    latency=_clamp(100-(p-30)*.18,0,100)
    stability=_clamp(100-j*1.5,0,100)
    parts=[(latency,.60),(stability,.40)]
    up=_optional_number(uptime_percent,100)
    if up is not None:
        parts=[(latency,.45),(stability,.30),(up,.25)]
    score=sum(value*weight for value,weight in parts)
    speed=_optional_number(download_mbps)
    if speed is not None and speed>0:
        score=.9*score+.1*_clamp(speed*5,0,100)
    # A fast successful request must not hide failed attempts. Older Agents
    # without sample metadata retain their existing score until upgraded.
    reliability=_optional_number(success_rate)
    if success_rate is not None:
        if reliability is None or reliability>1:return 0
        score*=reliability
    if sample_count is not None:
        count=_optional_number(sample_count)
        if count is None or count<1:return 0
        score*=min(1,count/3)
    return round(_clamp(score,0,100),1)


def likely_cause(iran_ok,external_ok):
    if iran_ok is False and external_ok is True:return 'احتمال اختلال مسیر ایران؛ نیازمند بررسی از نقاط مستقل'
    if iran_ok is False and external_ok is False:return 'احتمال اختلال مشترک سرویس یا زیرساخت؛ نیازمند بررسی بیشتر'
    if iran_ok is True and external_ok is False:return 'احتمال اختلال مسیر خارج؛ نیازمند بررسی بیشتر'
    return 'شواهد کافی برای تشخیص علت وجود ندارد'
