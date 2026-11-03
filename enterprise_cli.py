#!/usr/bin/env python3
"""Encrypted PostgreSQL backup/restore CLI.

Loads the root-owned systemd environment file without shell evaluation.
Run as root (for example: sudo python enterprise_cli.py backup nightly.dump.enc).
"""
import argparse
import os
import re
import sys
from pathlib import Path

ALLOWED_ENV = {
    "SONAR_DB_NAME", "SONAR_DB_USER", "SONAR_DB_PASSWORD", "SONAR_DB_HOST",
    "SONAR_DB_PORT", "SONAR_STATE_DIR", "SONAR_BACKUP_DIR",
    "SONAR_BACKUP_ENCRYPTION_KEY", "SONAR_BACKUP_S3_BUCKET",
    "SONAR_BACKUP_S3_REGION", "SONAR_BACKUP_S3_ENDPOINT",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
}


def load_env_file(path):
    """Parse simple KEY=value lines; never execute shell syntax."""
    env_path = Path(path)
    if not env_path.is_file():
        raise FileNotFoundError(f"Environment file not found: {env_path}")
    for number, raw in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid environment entry on line {number}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in ALLOWED_ENV:
            continue
        if value.startswith(("'", '"')) and value.endswith(value[0]):
            value = value[1:-1]
        if "\n" in value or "\r" in value:
            raise ValueError(f"Invalid environment value on line {number}")
        os.environ.setdefault(key, value)


def main():
    parser = argparse.ArgumentParser(prog="radarsonar-enterprise")
    parser.add_argument(
        "--env-file", default=os.getenv("SONAR_ENV_FILE", "/etc/sonar-bot.env"),
        help="Root-owned environment file (default: /etc/sonar-bot.env)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup", help="Create an encrypted PostgreSQL backup")
    backup.add_argument("filename", help="Filename inside SONAR_BACKUP_DIR or s3:// bucket path")
    restore = sub.add_parser("restore", help="Restore an encrypted PostgreSQL backup (destructive)")
    restore.add_argument("filename", help="Filename inside SONAR_BACKUP_DIR or s3:// bucket path")
    restore.add_argument("--confirm", action="store_true", help="Required confirmation flag")
    args = parser.parse_args()

    try:
        if os.geteuid() != 0 and not os.access(args.env_file, os.R_OK):
            raise PermissionError("Run with sudo to read the protected service environment file")
        load_env_file(args.env_file)
        from settings import DB_CONFIG
        from enterprise_runtime import create_encrypted_backup, restore_encrypted_backup

        if args.command == "backup":
            result = create_encrypted_backup(DB_CONFIG, args.filename)
            print(f"Backup created: {result['path']}")
            print(f"Bytes: {result['bytes']} SHA256: {result['sha256']}")
        else:
            if not args.confirm:
                parser.error("restore requires --confirm; it overwrites database objects")
            result = restore_encrypted_backup(DB_CONFIG, args.filename, confirm=True)
            print(f"Restore completed: {result['bytes']} bytes")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
