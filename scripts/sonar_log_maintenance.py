#!/usr/bin/python3
"""Root-owned, fixed-scope log maintenance; no arbitrary paths or commands.

Installed behind an owner-only systemd socket. Agent mode is executed through
the existing authenticated SSH maintenance transport, never via public WSS.
"""
import fcntl
import json
import os
import pwd
import re
import stat
import subprocess
import sys

APP_NAME=re.compile(r'^(sonar\.log|sonar\.json|sonar_errors\.json|sonar_bot\.log|sonar-web\.log)(\.[0-9]+)?(\.gz)?$')
AGENT_NAME=re.compile(r'^agent_debug\.log(\.[0-9]+)?(\.gz)?$')


def open_directory(path):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.strip('/').split('/'):
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        return fd
    except BaseException:
        os.close(fd);raise


def purge_files(directory, pattern):
    """Preflight all selected inodes; preserve active file descriptors on truncate."""
    try:base=open_directory(directory)
    except FileNotFoundError:return {'status':'not_present','files':0}
    files=[]
    try:
        fcntl.flock(base,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for name in sorted(os.listdir(base)):
            if not pattern.fullmatch(name):continue
            fd=os.open(name,os.O_WRONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=base)
            files.append((name,fd))
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise ValueError('Unsafe log inode')
        # No wildcard deletion, recursion or symlink/hardlink following.
        for name,fd in files:
            os.ftruncate(fd,0)
            os.fsync(fd)
            if name.endswith('.gz'):
                before=os.fstat(fd);current=os.stat(name,dir_fd=base,follow_symlinks=False)
                if (before.st_dev,before.st_ino)==(current.st_dev,current.st_ino):
                    os.unlink(name,dir_fd=base)
        return {'status':'cleared','files':len(files)}
    except Exception as exc:
        return {'status':'failed','error':type(exc).__name__}
    finally:
        for _,fd in files:os.close(fd)
        os.close(base)


def journals():
    result={}
    try:
        # Historical service records in the shared journal cannot be deleted
        # selectively. Never vacuum the global journal to hide this limitation.
        old=subprocess.run(['/usr/bin/journalctl','-u','sonar-bot','-u','sonar-web','-u','sonar-agent',
                            '--quiet','--no-pager','-n','1','--output=json'],
                           capture_output=True,text=True,timeout=15,check=False)
        result['shared_journal']={'status':('blocked_shared' if old.stdout.strip() else 'not_present')
                                  if old.returncode==0 else 'unavailable'}
        roots=('/var/log/journal','/run/log/journal')
        present=any(os.path.isdir(root) and any(name.endswith('.sonar') for name in os.listdir(root)) for root in roots)
        if present:
            cleared=subprocess.run(['/usr/bin/journalctl','--namespace=sonar','--rotate','--vacuum-time=1us','--vacuum-size=1'],
                                   capture_output=True,text=True,timeout=30,check=False)
            result['namespace_journal']={'status':'cleared' if cleared.returncode==0 else 'failed'}
        else:result['namespace_journal']={'status':'not_present'}
    except Exception as exc:
        result['namespace_journal']={'status':'unavailable','error':type(exc).__name__}
    return result


def purge(role):
    if role not in ('local','agent'):raise ValueError('Unknown maintenance role')
    result={}
    if role=='local':
        result['application_files']=purge_files('/var/log/sonar',APP_NAME)
        result['installer_file']=purge_files('/var/log',re.compile(r'^sonar-install\.log(\.[0-9]+)?(\.gz)?$'))
    else:
        result['agent_files']=purge_files('/var/lib/sonar-agent',AGENT_NAME)
        # Legacy root-based Agents used this exact file; protect all other files.
        result['legacy_agent_file']=purge_files('/root',AGENT_NAME)
        try:home=pwd.getpwnam('sonar-agent').pw_dir
        except KeyError:home=None
        if home and home not in ('/var/lib/sonar-agent','/root'):
            result['agent_home_files']=purge_files(home,AGENT_NAME)
    result.update(journals())
    return {'ok':all(row['status'] in ('cleared','not_present') for row in result.values()),'sources':result}


def main():
    if os.geteuid()!=0:raise PermissionError('Root maintenance service required')
    if sys.argv[1:]==['--agent']:
        role='agent'
    elif not sys.argv[1:]:
        raw=sys.stdin.buffer.readline(1025)
        if len(raw)>1024:raise ValueError('Request too large')
        request=json.loads(raw)
        if request!={'action':'purge_local','confirmation':'DELETE ALL LOGS'}:
            raise ValueError('Unsupported maintenance request')
        role='local'
    else:raise ValueError('Unsupported arguments')
    print(json.dumps(purge(role)),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__}),flush=True)
        sys.exit(1)
