#!/usr/bin/env python3
"""Verify restored Fernet material against encrypted PostgreSQL credentials."""
from __future__ import annotations

import sys
import os
import psycopg2
from psycopg2.extras import RealDictCursor

from secret_crypto import Security
from settings import BASE_DIR, DB_CONFIG, KEY_FILE


def main() -> int:
    secure_parent = os.path.abspath(os.path.dirname(KEY_FILE)) != os.path.abspath(BASE_DIR)
    security = Security(KEY_FILE, secure_parent=secure_parent)
    connection = psycopg2.connect(**DB_CONFIG)
    checked = {"server_passwords": 0, "agent_tokens": 0}
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT password FROM servers WHERE COALESCE(password,'')<>''")
            for row in cursor.fetchall():
                value = row.get("password") or ""
                if value.startswith("gAAAA"):
                    security.decrypt(value)
                    checked["server_passwords"] += 1

            cursor.execute("SELECT to_regclass('public.agent_nodes') AS relation")
            if cursor.fetchone()["relation"]:
                cursor.execute("SELECT agent_token FROM agent_nodes WHERE COALESCE(agent_token,'')<>''")
                for row in cursor.fetchall():
                    value = row.get("agent_token") or ""
                    if value.startswith("gAAAA"):
                        security.decrypt(value)
                        checked["agent_tokens"] += 1
    finally:
        connection.close()

    print(
        "SECRET_KEY_MATCH_OK "
        f"server_passwords={checked['server_passwords']} "
        f"agent_tokens={checked['agent_tokens']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Exception messages from database drivers are not printed; no secrets.
        print(f"SECRET_KEY_MATCH_FAILED {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
