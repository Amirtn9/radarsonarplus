"""One-time website login codes, stored hashed; website sessions are revocable.
Telegram Mini App initData remains supported unchanged.
"""
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timezone


def digest(s):return hashlib.sha256(s.encode('ascii')).hexdigest()


def valid_token(value, minimum=28, maximum=128):
    return isinstance(value,str) and minimum <= len(value) <= maximum and all(
        c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in value
    )


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_web_codes(
          code_hash CHAR(64) PRIMARY KEY, user_id BIGINT NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_web_sessions(
          token_hash CHAR(64) PRIMARY KEY, user_id BIGINT NOT NULL,
          csrf_hash CHAR(64) NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
          revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
        cur.execute('CREATE INDEX IF NOT EXISTS sonar_web_sessions_user_idx ON sonar_web_sessions(user_id)')
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_web_login_attempts (
            client_hash CHAR(64) PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0,
            window_started TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
        conn.commit()


def issue_code(db,uid):
    code=secrets.token_urlsafe(24)
    with db.get_connection() as (conn,cur):
        cur.execute('DELETE FROM sonar_web_codes WHERE user_id=%s OR expires_at<NOW() OR used_at IS NOT NULL',(int(uid),))
        cur.execute("INSERT INTO sonar_web_codes(code_hash,user_id,expires_at) VALUES (%s,%s,NOW()+INTERVAL '8 minutes')",(digest(code),int(uid)))
        conn.commit()
    return code


def redeem(db,code):
    if not valid_token(code):return None
    session=secrets.token_urlsafe(32)
    csrf=secrets.token_urlsafe(32)
    with db.get_connection() as (conn,cur):
        cur.execute('''UPDATE sonar_web_codes SET used_at=NOW() WHERE code_hash=%s
              AND used_at IS NULL AND expires_at>NOW() RETURNING user_id''',(digest(code),))
        row=cur.fetchone()
        if not row:
            conn.rollback();return None
        uid=int(row['user_id'])
        cur.execute("INSERT INTO sonar_web_sessions(token_hash,user_id,csrf_hash,expires_at) VALUES(%s,%s,%s,NOW()+INTERVAL '8 hours')",(digest(session),uid,digest(csrf)))
        conn.commit()
    return uid,session,csrf


def identify(db,session):
    if not valid_token(session):return None
    row=db.execute('''SELECT user_id,csrf_hash FROM sonar_web_sessions
         WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>NOW()''',(digest(session),),fetch='one')
    return dict(row) if row else None


def logout(db,session):
    if valid_token(session):db.execute('UPDATE sonar_web_sessions SET revoked_at=NOW() WHERE token_hash=%s',(digest(session),))


def allow_login_attempt(db,client_hash,limit=10):
    """One attempt counts against a 15-minute window, including invalid codes.

    Key is HMAC(client IP, private bot secret) produced by the API, not the IP.
    One SQL statement is atomic under concurrent ASGI workers.
    """
    if not isinstance(client_hash,str) or len(client_hash)!=64:
        return False
    with db.get_connection() as (conn,cur):
        cur.execute("""INSERT INTO sonar_web_login_attempts(client_hash,attempts,window_started)
          VALUES (%s,1,NOW()) ON CONFLICT(client_hash) DO UPDATE SET
            attempts=CASE WHEN sonar_web_login_attempts.window_started<NOW()-INTERVAL '15 minutes'
              THEN 1 ELSE sonar_web_login_attempts.attempts+1 END,
            window_started=CASE WHEN sonar_web_login_attempts.window_started<NOW()-INTERVAL '15 minutes'
              THEN NOW() ELSE sonar_web_login_attempts.window_started END
          RETURNING attempts""",(client_hash,))
        attempts=int(cur.fetchone()['attempts'])
        conn.commit()
        return attempts<=int(limit)


def logout_all(db,uid):
    """Owner-initiated session reset revokes all devices and outstanding codes."""
    with db.get_connection() as (conn,cur):
        cur.execute('UPDATE sonar_web_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL',(int(uid),))
        cur.execute('DELETE FROM sonar_web_codes WHERE user_id=%s',(int(uid),))
        conn.commit()
