import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import enterprise_runtime as runtime
from enterprise_services import EnterpriseStore


def test_signed_agent_release_verifies_ed25519_and_version(monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv("SONAR_AGENT_RELEASE_PUBLIC_KEY", base64.b64encode(public).decode())
    artifact = b"AGENT_VERSION = '6.9.1'\nAGENT_PROTOCOL = 2\n"
    signature = private.sign(artifact)
    source = runtime.verify_agent_release_bytes(
        "6.9.1",
        hashlib.sha256(artifact).hexdigest(),
        base64.b64encode(signature).decode(),
        artifact,
    )
    assert "AGENT_VERSION = '6.9.1'" in source


def test_signed_agent_release_rejects_hash_tampering(monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv("SONAR_AGENT_RELEASE_PUBLIC_KEY", base64.b64encode(public).decode())
    artifact = b"AGENT_VERSION = '6.9.1'\n"
    signature = base64.b64encode(private.sign(artifact)).decode()
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        runtime.verify_agent_release_bytes("6.9.1", "0" * 64, signature, artifact)


def test_signed_agent_release_rejects_invalid_signature(monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv("SONAR_AGENT_RELEASE_PUBLIC_KEY", base64.b64encode(public).decode())
    artifact = b"AGENT_VERSION = '6.9.1'\n"
    wrong_signature = base64.b64encode(b"x" * 64).decode()
    with pytest.raises(ValueError, match="SIGNATURE_INVALID"):
        runtime.verify_agent_release_bytes(
            "6.9.1", hashlib.sha256(artifact).hexdigest(), wrong_signature, artifact
        )


class _HealthDB:
    def __init__(self):
        self.policy = {
            "weights": {"agent": 50, "cpu": 20, "ram": 20, "disk": 10},
            "thresholds": {"cpu": 100, "ram": 100, "disk": 100},
            "stale_seconds": 180,
        }

    def execute(self, query, params=(), fetch=None):
        if "FROM rs_health_policies" in query:
            return self.policy if fetch == "one" else [self.policy]
        if "FROM servers WHERE owner_id" in query:
            from datetime import datetime, timezone
            return [{
                "id": 5, "name": "edge-1", "last_status": "Online",
                "last_agent_status": "Online", "last_cpu": 50, "last_ram": 0,
                "last_disk": 0, "last_checked_at": datetime.now(timezone.utc),
                "agent_last_seen": None,
            }]
        raise AssertionError(query)


class _HealthPro:
    def __init__(self):
        self.db = _HealthDB()

    def workspace(self, actor, owner=None):
        return int(owner or actor)

    def require(self, actor, owner, write=False):
        return "owner"

    def audit(self, *args, **kwargs):
        return None


def test_health_score_uses_configurable_weights():
    service = EnterpriseStore(_HealthPro())
    result = service.health(101)
    assert result["servers"][0]["score"] == 90
    assert result["servers"][0]["state"] == "healthy"


def test_health_policy_rejects_weights_not_equal_to_100():
    service = EnterpriseStore(_HealthPro())
    with pytest.raises(ValueError, match="sum to 100"):
        service.set_health_policy(
            101, 101,
            {"agent": 40, "cpu": 20, "ram": 20, "disk": 10},
            {"cpu": 90, "ram": 90, "disk": 90},
            180,
        )


def test_agent_deploy_accepts_only_preverified_script_injection(monkeypatch):
    pytest.importorskip('paramiko')
    import agent_deploy
    captured = {}
    def fake_deploy(monitor, db, ip, port, user, password, ws_port, progress=None, agent_script=None):
        captured["script"] = agent_script
        return True, "ok"
    monkeypatch.setattr(agent_deploy, "_deploy", fake_deploy)
    ok, detail = agent_deploy.deploy_agent(
        object(), object(), "127.0.0.1", 22, "root", "secret", 22000,
        agent_script="AGENT_VERSION = '6.9.1'\n",
    )
    assert ok and detail == "ok"
    assert captured["script"] == "AGENT_VERSION = '6.9.1'\n"
