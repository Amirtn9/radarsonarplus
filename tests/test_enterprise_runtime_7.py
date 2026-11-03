
import hashlib
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import enterprise_runtime as er


def test_webhook_rejects_private_ip(monkeypatch):
    monkeypatch.setattr(er.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="non-public"):
        er._public_https_url("https://example.test/hook")


def test_webhook_requires_https(monkeypatch):
    with pytest.raises(ValueError, match="HTTPS"):
        er._public_https_url("http://example.test/hook")


def test_encrypted_backup_writes_private_file(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("SONAR_BACKUP_ENCRYPTION_KEY", key.decode())
    monkeypatch.setenv("SONAR_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(er.subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(a[0], 0, stdout=b"postgres-dump-test", stderr=b""))
    result = er.create_encrypted_backup(
        {"host":"127.0.0.1","port":5432,"user":"sonar","password":"secret","dbname":"sonar"},
        "test.dump.enc")
    target = Path(result["path"])
    assert target.exists()
    assert target.stat().st_mode & 0o077 == 0
    assert Fernet(key).decrypt(target.read_bytes()) == b"postgres-dump-test"
    assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_backup_destination_cannot_escape_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SONAR_BACKUP_DIR", str(tmp_path / "safe"))
    with pytest.raises(ValueError, match="inside"):
        er._safe_destination("../outside.dump.enc")


def test_restart_allowlist_is_fixed():
    assert er.SERVICE_ALLOWLIST == {"sonar-agent", "nginx", "docker", "ssh"}
    assert "bash" not in er.SERVICE_ALLOWLIST


def test_webhook_signature_is_hmac_sha256(monkeypatch):
    import json
    captured={}
    class Response:
        status=204
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def read(self,n): return b""
    class Opener:
        def open(self,req,timeout):
            captured["body"]=req.data
            captured["signature"]=req.get_header("X-radar-signature")
            return Response()
    monkeypatch.setattr(er,"_public_https_url",lambda u: None)
    monkeypatch.setattr(er,"build_opener",lambda *a: Opener())
    payload={"event":"operation.completed","data":{"id":"abc"}}
    er.deliver_webhook("https://hooks.example.org/event","test-secret",payload)
    expected=__import__("hmac").new(b"test-secret",captured["body"],__import__("hashlib").sha256).hexdigest()
    assert captured["signature"]=="sha256="+expected
    assert json.loads(captured["body"])==payload


def test_s3_backup_destination_requires_configured_bucket(monkeypatch):
    monkeypatch.setenv("SONAR_BACKUP_S3_BUCKET","approved-bucket")
    assert er._s3_location("s3://approved-bucket/sonar/daily.dump.enc")==("approved-bucket","sonar/daily.dump.enc")
    with pytest.raises(ValueError,match="must match"):
        er._s3_location("s3://other-bucket/sonar/daily.dump.enc")
    with pytest.raises(ValueError,match="Invalid S3"):
        er._s3_location("s3://approved-bucket/")


def test_backup_root_defaults_to_private_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SONAR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("SONAR_BACKUP_DIR", raising=False)
    target = er._safe_destination("scheduled.dump.enc")
    assert target == (tmp_path / "state" / "backups" / "scheduled.dump.enc").resolve()


def test_cli_environment_parser_does_not_execute_shell(tmp_path, monkeypatch):
    from enterprise_cli import load_env_file
    marker = tmp_path / "must-not-exist"
    envfile = tmp_path / "service.env"
    envfile.write_text(
        "SONAR_DB_HOST=127.0.0.1\n"
        f"UNTRUSTED=$(touch {marker})\n"
        "SONAR_DB_PORT=5432\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SONAR_DB_HOST", raising=False)
    monkeypatch.delenv("SONAR_DB_PORT", raising=False)
    load_env_file(envfile)
    assert __import__("os").environ["SONAR_DB_HOST"] == "127.0.0.1"
    assert __import__("os").environ["SONAR_DB_PORT"] == "5432"
    assert not marker.exists()
