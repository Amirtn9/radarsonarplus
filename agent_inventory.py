"""Read-only release identity; no secrets, downloads or remote commands."""
import hashlib
import json
from pathlib import Path

BUNDLE_FILES=('monitor_agent.py','agent_inventory.py','agent_metering.py','service_probes.py',
              'pro_scoring.py','ssh_relay.py','agent_runtime.lock.json')


def bundle_fingerprint(root):
    digest=hashlib.sha256()
    try:
        for name in BUNDLE_FILES:
            data=(Path(root)/name).read_bytes()
            if name.endswith('.json'):
                data=json.dumps(json.loads(data),sort_keys=True,separators=(',',':')).encode()
            digest.update(name.encode()+b'\0'+hashlib.sha256(data).digest())
    except (OSError,ValueError,TypeError):return None
    return digest.hexdigest()


def compare_agent(stats,expected_version,expected_build,expected_protocol):
    if not isinstance(stats,dict) or stats.get('status')!='Online':
        return {'state':'unknown','reason':'AGENT_UNREACHABLE'}
    version=stats.get('agent_version')
    protocol=stats.get('agent_protocol')
    build=stats.get('agent_build')
    if protocol is not None and protocol!=expected_protocol:
        state,reason='incompatible','PROTOCOL_MISMATCH'
    elif not version or not build or not expected_build or protocol is None:
        state,reason='unknown','INCOMPLETE_VERSION_METADATA'
    elif version==expected_version and build==expected_build:
        state,reason='current','BUNDLE_MATCH'
    else:
        # A different build could be newer or locally customized, not outdated.
        state,reason='different','BUNDLE_DIFFERS'
    return {'state':state,'reason':reason,'agent_version':str(version)[:64] if version else None,
            'agent_protocol':protocol if type(protocol) is int else None,
            'agent_build':build if isinstance(build,str) and len(build)==64 and all(c in '0123456789abcdef' for c in build) else None,
            **{key:stats.get(key) if isinstance(stats.get(key),str) else None for key in
               ('xray_version','xray_compat_version')}}


async def check_versions(rows):
    import asyncio
    from datetime import datetime,timezone
    from core import ServerMonitor
    from settings import PROJECT_VERSION
    root=Path(__file__).resolve().parent
    expected=bundle_fingerprint(root)
    lock=json.loads((root/'agent_runtime.lock.json').read_text())
    semaphore=asyncio.Semaphore(6)
    async def check(row):
        async with semaphore:
            try:
                stats=await asyncio.wait_for(ServerMonitor.ws_send_command(row['ip'],
                    int(row['ws_port']),None,{'action':'get_stats'},timeout=7),timeout=10)
            except Exception:stats=None
        result=compare_agent(stats,PROJECT_VERSION,expected,2)
        for key,target in (('xray_version',lock['version']),('xray_compat_version',lock['compatibility']['version'])):
            installed=result.get(key)
            result[key+'_state']='unknown' if not installed else 'current' if installed==target else 'different'
        return {'id':int(row['id']),'name':str(row['name']),
                'checked_at':datetime.now(timezone.utc).isoformat(),**result}
    return {'expected_version':PROJECT_VERSION,'expected_build':expected,'expected_protocol':2,
            'expected_xray_version':lock['version'],
            'expected_xray_compat_version':lock['compatibility']['version'],
            'nodes':await asyncio.gather(*(check(row) for row in rows))}
