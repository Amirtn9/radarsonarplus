#!/usr/bin/env python3
"""Run every browser smoke test against one disposable local HTTPS/SQL fixture."""
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    if os.getenv('SONAR_BROWSER_FIXTURE')!='1' or not os.getenv('SONAR_TEST_DB_DSN'):
        raise RuntimeError('Dedicated local test database and SONAR_BROWSER_FIXTURE=1 required')
    output=Path(os.environ['SONAR_BROWSER_TEST_OUTPUT']).resolve();output.mkdir(parents=True,exist_ok=True)
    if output==ROOT or ROOT in output.parents:raise ValueError('Browser artifacts must be outside the source tree')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'SONAR_BROWSER_TEST_URL':f'https://127.0.0.1:{port}',
         'SONAR_BROWSER_TEST_TOKEN':'fixture-only-not-a-telegram-bot-token',
         'SONAR_TEST_PYTHON':sys.executable,'SONAR_BROWSER_TEST_PURGE_JOB':'1',
         'SONAR_GUIDE_SCREENSHOTS':str(output/'guide-layout')}
    if not env.get('SONAR_CHROMIUM_PATH') and shutil.which('chromium'):
        env['SONAR_CHROMIUM_PATH']=shutil.which('chromium')
    # The unverified context is solely for this generated, loopback-only fixture.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    with (output/'fixture.log').open('w') as log:
        fixture=subprocess.Popen([sys.executable,str(ROOT/'scripts/browser_fixture.py'),'--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+40
            while True:
                if fixture.poll() is not None:raise RuntimeError('Browser fixture exited; inspect fixture.log')
                try:
                    with opener.open(env['SONAR_BROWSER_TEST_URL']+'/readyz',timeout=1) as response:
                        if response.status==200:break
                except (OSError,urllib.error.URLError):pass
                if time.monotonic()>=deadline:raise TimeoutError('Browser fixture readiness timed out')
                time.sleep(.1)
            for name in ('browser_guide_layout.cjs','browser_feature_smoke.cjs','browser_telemetry_smoke.cjs','browser_log_management.cjs','browser_billing_control.cjs'):
                print('Running '+name,flush=True)
                subprocess.run(['node',str(ROOT/'scripts'/name)],cwd=ROOT,env=env,check=True,timeout=180)
        finally:
            fixture.terminate()
            try:fixture.wait(timeout=30)
            except subprocess.TimeoutExpired:
                fixture.kill();fixture.wait()


if __name__=='__main__':main()
