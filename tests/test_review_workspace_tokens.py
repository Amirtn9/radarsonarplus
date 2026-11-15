"""API credentials must stay within their workspace and delegated capabilities."""
import pytest
from fastapi.testclient import TestClient
from test_billing_workflow import service
from test_postgres_workflow import isolated_database
from enterprise_services import EnterpriseStore
from pro_store import ProStore


@pytest.fixture
def token_client(service, monkeypatch):
    import pro_portal
    monkeypatch.setattr(pro_portal.store, 'db', service.db)
    # One person administers two workspaces; their token belongs to only one.
    for owner in (901, 903):
        service.db.execute("INSERT INTO pro_team(owner_id,member_id,role) VALUES(%s,902,'manager')", (owner,))
    enterprise = EnterpriseStore(ProStore(service.db))
    issued = enterprise.create_api_token(902, 903, 'Limited integration', ['servers:read', 'tokens:write'])
    client = TestClient(pro_portal.app, base_url='https://testserver')
    client.headers['Authorization'] = 'Bearer ' + issued['token']
    return client, enterprise


def test_token_cannot_cross_workspace_via_query_or_header(token_client):
    client, _ = token_client
    assert client.get('/api/enterprise/health?owner=903').status_code == 200
    assert client.get('/api/enterprise/health?owner=901').status_code == 403
    assert client.get('/api/enterprise/health', headers={'X-Workspace-Id': '901'}).status_code == 403


def test_token_defaults_to_its_workspace_instead_of_its_creator(token_client, monkeypatch):
    client, _ = token_client
    def health(self, actor, owner):
        return {'actor': actor, 'owner': owner}
    monkeypatch.setattr(EnterpriseStore, 'health', health)
    assert client.get('/api/enterprise/health').json() == {'actor': 902, 'owner': 903}


def test_token_cannot_mint_stronger_tokens_or_tokens_for_another_workspace(token_client):
    client, _ = token_client
    body = {'label': 'Child', 'scopes': ['operations:write']}
    assert client.post('/api/enterprise/tokens', json=body).status_code == 403
    body['scopes'] = ['servers:read']
    body['owner'] = 901
    assert client.post('/api/enterprise/tokens', json=body).status_code == 403
    body['owner'] = 903
    result = client.post('/api/enterprise/tokens', json=body)
    assert result.status_code == 200, result.text


def test_http_monitor_rejects_zero_port_and_normalizes_input():
    from service_probes import normalize_target, check
    with pytest.raises(ValueError, match='port'):
        normalize_target('http', 'https://example.com:0/')
    # Normalization must also be applied by the probe entry point itself.
    result = check(' TCP ', ' 127.0.0.1:1 ', timeout=1, allow_private=False)
    assert result.detail == 'Blocked by address policy'
