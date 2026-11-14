"""Hashed one-time website codes and revocable sessions for web and Telegram Mini Apps.
Fresh Telegram initData can be exchanged for an HttpOnly session cookie.
"""
from __future__ import annotations
import hashlib
import hmac
import secrets
from datetime import datetime, timezone


def digest(s):return hashlib.sha256(s.encode('ascii')).hexdigest()


def csrf_proof(session):
    """Recover a stable, domain-separated proof without storing the cookie token.

    The session remains HttpOnly. This proof cannot be used as a session cookie.
    Existing randomly issued proofs remain valid until their session expires.
    """
    if not valid_token(session):
        raise ValueError('Invalid session token')
    return hmac.new(session.encode('ascii'), b'sonar-web-csrf-v1', hashlib.sha256).hexdigest()


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


def install_security_schema(db):
    """Add indexes without changing already-deployed migration six."""
    with db.get_connection() as (conn,cur):
        for statement in (
            'CREATE INDEX IF NOT EXISTS sonar_web_sessions_expiry_idx ON sonar_web_sessions(expires_at)',
            'CREATE INDEX IF NOT EXISTS sonar_web_codes_expiry_idx ON sonar_web_codes(expires_at)',
            'CREATE INDEX IF NOT EXISTS sonar_web_codes_user_idx ON sonar_web_codes(user_id,created_at)',
            'CREATE INDEX IF NOT EXISTS sonar_web_attempts_window_idx ON sonar_web_login_attempts(window_started)',
        ):cur.execute(statement)
        conn.commit()


def issue_code(db,uid):
    code=secrets.token_urlsafe(24)
    with db.get_connection() as (conn,cur):
        # Serialize issuance per user; keep the three newest live codes so
        # accidental double clicks do not silently invalidate the first link.
        cur.execute('SELECT pg_advisory_xact_lock(535301,hashtext(%s))',(str(int(uid)),))
        cur.execute('DELETE FROM sonar_web_codes WHERE expires_at<NOW()')
        cur.execute('DELETE FROM sonar_web_codes WHERE user_id=%s AND used_at IS NOT NULL',(int(uid),))
        cur.execute('''DELETE FROM sonar_web_codes WHERE code_hash IN (
            SELECT code_hash FROM sonar_web_codes WHERE user_id=%s
            ORDER BY created_at DESC,code_hash DESC OFFSET 2)''',(int(uid),))
        cur.execute("INSERT INTO sonar_web_codes(code_hash,user_id,expires_at) VALUES (%s,%s,NOW()+INTERVAL '8 minutes')",(digest(code),int(uid)))
        conn.commit()
    return code


def create_session(db, uid):
    """Create a revocable, HttpOnly-cookie session after trusted authentication."""
    uid=int(uid)
    if uid<=0:raise ValueError('Invalid user ID')
    session=secrets.token_urlsafe(32)
    csrf=secrets.token_urlsafe(32)
    with db.get_connection() as (conn,cur):
        # Bound storage growth without deleting active or recently revoked sessions.
        cur.execute('DELETE FROM sonar_web_sessions WHERE expires_at<NOW()')
        cur.execute("INSERT INTO sonar_web_sessions(token_hash,user_id,csrf_hash,expires_at,elevated_at) VALUES(%s,%s,%s,NOW()+INTERVAL '8 hours',NOW())",
                    (digest(session),uid,digest(csrf)))
        conn.commit()
    return session,csrf


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
        cur.execute("INSERT INTO sonar_web_sessions(token_hash,user_id,csrf_hash,expires_at,elevated_at) VALUES(%s,%s,%s,NOW()+INTERVAL '8 hours',NOW())",(digest(session),uid,digest(csrf)))
        conn.commit()
    return uid,session,csrf


def identify(db,session):
    if not valid_token(session):return None
    row=db.execute('''SELECT user_id,csrf_hash FROM sonar_web_sessions
         WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>NOW()
         AND last_seen_at>NOW()-INTERVAL '30 minutes'
         ''',(digest(session),),fetch='one')
    return dict(row) if row else None


def logout(db,session):
    if valid_token(session):db.execute('UPDATE sonar_web_sessions SET revoked_at=NOW() WHERE token_hash=%s',(digest(session),))


def allow_login_attempt(db,client_hash,limit=10):
    """Reserve a login attempt; successful authentication releases its reservation.

    Key is HMAC(client IP, private bot secret) produced by the API, not the IP.
    One SQL statement is atomic under concurrent ASGI workers.
    """
    if not isinstance(client_hash,str) or len(client_hash)!=64:
        return False
    with db.get_connection() as (conn,cur):
        cur.execute("DELETE FROM sonar_web_login_attempts WHERE window_started<NOW()-INTERVAL '1 day'")
        cur.execute("""INSERT INTO sonar_web_login_attempts(client_hash,attempts,window_started)
          VALUES (%s,1,NOW()) ON CONFLICT(client_hash) DO UPDATE SET
            attempts=CASE WHEN sonar_web_login_attempts.window_started<NOW()-INTERVAL '15 minutes'
              THEN 1 ELSE sonar_web_login_attempts.attempts+1 END,
            window_started=NOW()
          RETURNING attempts""",(client_hash,))
        attempts=int(cur.fetchone()['attempts'])
        conn.commit()
        return attempts<=int(limit)


def release_login_attempt(db,client_hash):
    # Release just this successful reservation, never erase earlier failures.
    db.execute('''UPDATE sonar_web_login_attempts SET attempts=GREATEST(attempts-1,0)
                  WHERE client_hash=%s''',(client_hash,))


def logout_all(db,uid):
    """Owner-initiated session reset revokes all devices and outstanding codes."""
    with db.get_connection() as (conn,cur):
        cur.execute('UPDATE sonar_web_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL',(int(uid),))
        cur.execute('DELETE FROM sonar_web_codes WHERE user_id=%s',(int(uid),))
        conn.commit()


def live_code(db,code):
    if not valid_token(code):return False
    return bool(db.execute("SELECT 1 FROM sonar_web_codes WHERE code_hash=%s AND used_at IS NULL AND expires_at>NOW()",(digest(code),),fetch='one'))


def touch_session(db,session):
    db.execute("UPDATE sonar_web_sessions SET last_seen_at=NOW(),expires_at=LEAST(created_at+INTERVAL '30 days',NOW()+INTERVAL '8 hours') WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>NOW() AND last_seen_at>NOW()-INTERVAL '30 minutes'",(digest(session),))
