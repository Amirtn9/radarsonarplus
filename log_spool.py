"""Private, bounded, process-shared retry storage for redacted application logs."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
from datetime import datetime
from pathlib import Path


class LogSpool:
    def __init__(self, path=None):
        root=Path(os.getenv('SONAR_STATE_DIR',str(Path(__file__).resolve().parent/'runtime')))/'log-spool'
        self.path=Path(path) if path else root/'pending.sqlite3'
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.path.parent.is_symlink():raise ValueError('Unsafe log spool directory')
        try:
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            os.close(fd)
        except FileExistsError:
            info=self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise ValueError('Unsafe log spool file')
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(str(self.path),timeout=0.1,check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=FULL')
        self.conn.execute('PRAGMA secure_delete=ON')
        # 128 MiB at SQLite's default 4096-byte pages; never grow indefinitely.
        page_size=self.conn.execute('PRAGMA page_size').fetchone()[0]
        self.conn.execute(f'PRAGMA max_page_count={128*1024*1024//page_size}')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS pending (
            event_id TEXT PRIMARY KEY,created_at REAL NOT NULL,payload TEXT NOT NULL)''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS pending_time ON pending(created_at)')
        self.conn.commit()

    def add(self, rows):
        values=[(row[6],row[0].timestamp(),json.dumps([row[0].isoformat(),*row[1:]],ensure_ascii=False)) for row in rows]
        with self.lock,self.conn:
            self.conn.executemany('INSERT OR IGNORE INTO pending VALUES(?,?,?)',values)

    def read(self, limit=100):
        with self.lock:
            rows=self.conn.execute('SELECT payload FROM pending ORDER BY created_at,event_id LIMIT ?',(limit,)).fetchall()
        result=[]
        for row in rows:
            values=json.loads(row[0]);values[0]=datetime.fromisoformat(values[0]);result.append(tuple(values))
        return result

    def remove(self, ids):
        with self.lock,self.conn:
            self.conn.executemany('DELETE FROM pending WHERE event_id=?',[(value,) for value in ids])

    def discard_before(self, cutoff):
        with self.lock:
            with self.conn:
                self.conn.execute('DELETE FROM pending WHERE created_at<=?',(cutoff.timestamp(),))
            self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def count(self):
        with self.lock:return self.conn.execute('SELECT COUNT(*) FROM pending').fetchone()[0]

    def close(self):
        with self.lock:self.conn.close()
