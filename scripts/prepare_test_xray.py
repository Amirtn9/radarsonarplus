#!/usr/bin/env python3
"""Download the exact test runtimes in the release lock and verify their hashes."""
import argparse
import hashlib
import json
import os
import platform
import re
import stat
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def prepare(destination):
    lock=json.loads((ROOT/'agent_runtime.lock.json').read_text())
    arch={'amd64':'x86_64','arm64':'aarch64'}.get(platform.machine().lower(),platform.machine().lower())
    paths={}
    for role,release in (('latest',lock),('compat',lock['compatibility'])):
        asset=release['assets'][arch]
        if not asset['url'].startswith('https://github.com/XTLS/Xray-core/releases/download/') or not re.fullmatch('[a-f0-9]{64}',asset['sha256']):
            raise ValueError('Invalid pinned test runtime')
        folder=destination/role;folder.mkdir(parents=True,exist_ok=True)
        archive=folder/'runtime.zip'
        request=urllib.request.Request(asset['url'],headers={'User-Agent':'RadarSonarPlus-CI'})
        digest=hashlib.sha256();size=0
        with urllib.request.urlopen(request,timeout=60) as response,archive.open('wb') as stream:
            while True:
                part=response.read(256*1024)
                if not part:break
                size+=len(part)
                if size>90*1024*1024:raise ValueError('Test runtime archive too large')
                digest.update(part);stream.write(part)
        if digest.hexdigest()!=asset['sha256']:raise ValueError('Test runtime SHA-256 mismatch')
        binary=folder/'xray'
        with zipfile.ZipFile(archive) as source:
            member=source.getinfo('xray')
            if member.is_dir() or stat.S_ISLNK(member.external_attr>>16) or not 1024<member.file_size<150*1024*1024:
                raise ValueError('Invalid test runtime executable')
            binary.write_bytes(source.read(member))
        binary.chmod(0o700)
        paths['SONAR_TEST_XRAY_COMPAT_BIN' if role=='compat' else 'SONAR_TEST_XRAY_BIN']=str(binary.resolve())
        print(f'Verified {role}: {release["version"]} ({arch})')
    return paths


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--github-env',type=Path)
    args=parser.parse_args()
    paths=prepare(args.output)
    if args.github_env:
        with args.github_env.open('a') as stream:
            for name,value in paths.items():
                if '\n' in value or '\r' in value:raise ValueError('Invalid environment output path')
                stream.write(f'{name}={value}\n')
    else:print(json.dumps(paths))
