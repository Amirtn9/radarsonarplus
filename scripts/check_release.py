#!/usr/bin/env python3
"""Keep the public release labels aligned with the authoritative VERSION file."""
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
LABELS={
    'README.md':r'^>[^\n]*`([0-9]+\.[0-9]+\.[0-9]+)`',
    'CHANGELOG.md':r'^# RadarSonarPlus ([0-9]+\.[0-9]+\.[0-9]+)',
    'SECURITY.md':r'\*\*Release:\*\* `([^`]+)`',
    'settings.py':r'PROJECT_VERSION\s*=\s*[\'"]([^\'"]+)',
    'monitor_agent.py':r'AGENT_VERSION\s*=\s*[\'"]([^\'"]+)',
    'install.sh':r'# Sonar Radar Pro ([0-9]+\.[0-9]+\.[0-9]+)',
    'pro_portal.py':r'FastAPI\(title=[\'"]RadarSonarPlus ([0-9]+\.[0-9]+\.[0-9]+)',
    'pro_web/index.html':r'<title>Sonar Radar ([0-9]+\.[0-9]+\.[0-9]+)',
}


def check(root=ROOT):
    errors=[]
    try:version=(root/'VERSION').read_text().strip()
    except OSError:return ['VERSION is missing or unreadable']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):return ['VERSION must contain a release number']
    for path,pattern in LABELS.items():
        try:content=(root/path).read_text(encoding='utf-8')
        except OSError:
            errors.append(path+': missing or unreadable');continue
        match=re.search(pattern,content,re.MULTILINE)
        if not match or match[1]!=version:
            errors.append(f'{path}: release label must match VERSION ({version})')
    return errors


if __name__=='__main__':
    failures=check()
    if failures:
        print('\n'.join(failures),file=sys.stderr);raise SystemExit(1)
    print('Release labels match VERSION.')
