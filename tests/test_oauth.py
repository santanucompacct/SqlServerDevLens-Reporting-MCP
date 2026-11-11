"""OAuth protocol, durable authorization and adversarial regression checks."""
import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from devlens_reporting.app import create_app
from devlens_reporting.oauth import COOKIE, OAuthProvider, SCOPE
from devlens_reporting.storage import Store

ADMIN = 'synthetic-oauth-admin-token-123456789012345'
PARTICIPANT = 'synthetic-oauth-participant-token-123456789012345'
ISSUER = 'https://reporting.test'
REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect'


@pytest.fixture
def configured(tmp_path, monkeypatch):
    state = json.loads(Path('examples/instance.json').read_text())
    state['participants'] = [{'name': 'tester', 'token_sha256': hashlib.sha256(PARTICIPANT.encode()).hexdigest(), 'datasets': ['sales']}]
    path = tmp_path / 'instance.json'
    path.write_text(json.dumps(state))
    monkeypatch.setenv('DEVLENS_ADMIN_TOKEN', ADMIN)
    monkeypatch.setenv('DEVLENS_OAUTH_ISSUER', ISSUER)
    monkeypatch.setenv('DEVLENS_ALLOWED_HOSTS', 'reporting.test,localhost')
    return path


@pytest.fixture
def client(configured):
    with TestClient(create_app(str(configured), demo=True), base_url=ISSUER) as client:
        yield client


def admin():
    return {'Authorization': 'Bearer ' + ADMIN}


def register(client, method='none', redirect=REDIRECT):
    response = client.post('/register', json={'redirect_uris': [redirect], 'client_name': 'Test reporting client',
        'token_endpoint_auth_method': method, 'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'], 'scope': SCOPE})
    assert response.status_code == 201, response.text
    return response.json()


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


def form_values(response):
    return {name: re.search("name='" + name + "' value='([^']+)'", response.text).group(1) for name in ['csrf', 'request']}


def begin(client, registration, **overrides):
    verifier = secrets.token_urlsafe(48)
    params = {'client_id': registration['client_id'], 'redirect_uri': registration['redirect_uris'][0],
              'response_type': 'code', 'code_challenge_method': 'S256', 'code_challenge': challenge(verifier),
              'scope': SCOPE, 'state': 'opaque-client-state', 'resource': ISSUER + '/mcp', **overrides}
    response = client.get('/authorize', params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    page = client.get(response.headers['location'])
    assert page.status_code == 200, page.text
    return verifier, page


def login(client, page, token=PARTICIPANT):
    return client.post('/oauth/consent', data={**form_values(page), 'action': 'login', 'participant_token': token},
                       headers={'Origin': ISSUER}, follow_redirects=False)


def authorize(client, registration):
    verifier, page = begin(client, registration)
    assert any(key.startswith(COOKIE + "-") for key in page.cookies)
    assert 'Secure' in page.headers['set-cookie'] and 'HttpOnly' in page.headers['set-cookie']
    assert page.headers['referrer-policy'] == 'same-origin'
    assert "form-action 'self' https://chatgpt.com;" in page.headers['content-security-policy']
    page = login(client, page)
    assert page.status_code == 200, page.text
    assert 'tester' in page.text and '<li>sales</li>' in page.text
    assert PARTICIPANT not in page.text
    response = client.post('/oauth/consent', data={**form_values(page), 'action': 'approve'},
                           headers={'Origin': ISSUER}, follow_redirects=False)
    assert response.status_code == 302, response.text
    params = parse_qs(urlsplit(response.headers['location']).query)
    assert params['state'] == ['opaque-client-state']
    assert params['iss'] == [ISSUER]
    return params['code'][0], verifier


def exchange(client, registration, code, verifier, **extra):
    data = {'client_id': registration['client_id'], 'code': code, 'code_verifier': verifier,
            'redirect_uri': registration['redirect_uris'][0], 'grant_type': 'authorization_code',
            'resource': ISSUER + '/mcp', **extra}
    if registration.get('client_secret'):
        data['client_secret'] = registration['client_secret']
    return client.post('/token', data=data)


def rpc(client, token, method, params=None):
    return client.post('/mcp', headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json, text/event-stream',
                       'MCP-Protocol-Version': '2025-06-18'},
                       json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})


def test_discovery_and_entire_flow_persists_without_raw_secrets(client, configured):
    response = client.post('/mcp', json={})
    assert response.status_code == 401
    assert ISSUER + '/.well-known/oauth-protected-resource/mcp' in response.headers['www-authenticate']
    resource = client.get('/.well-known/oauth-protected-resource/mcp').json()
    assert resource['resource'] == ISSUER + '/mcp'
    assert resource['authorization_servers'] == [ISSUER]
    metadata = client.get('/.well-known/oauth-authorization-server').json()
    assert metadata['issuer'] == ISSUER
    assert metadata['authorization_response_iss_parameter_supported'] is True
    assert metadata['code_challenge_methods_supported'] == ['S256']
    assert 'none' in metadata['token_endpoint_auth_methods_supported']
    registration = register(client, 'client_secret_post')
    code, verifier = authorize(client, registration)
    response = exchange(client, registration, code, verifier)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    tokens = response.json()
    assert tokens['scope'] == SCOPE and tokens['token_type'] == 'Bearer'
    assert 0 < tokens['expires_in'] <= 3600
    assert rpc(client, tokens['access_token'], 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}).status_code == 200
    tools = rpc(client, tokens['access_token'], 'tools/list').json()['result']['tools']
    assert len(tools) == 3
    assert all(t['securitySchemes'] == [{'type': 'oauth2', 'scopes': [SCOPE]}] for t in tools)
    assert all(t['_meta']['securitySchemes'] == t['securitySchemes'] for t in tools)
    rows = rpc(client, tokens['access_token'], 'tools/call', {'name': 'query_dataset', 'arguments': {'query': {'dataset': 'sales', 'fields': ['amount'], 'limit': 2}}})
    assert rows.status_code == 200 and not rows.json()['result'].get('isError')
    assert rpc(client, PARTICIPANT, 'tools/list').status_code == 200  # Legacy token compatibility.
    with TestClient(create_app(str(configured), demo=True), base_url=ISSUER) as restarted:
        assert rpc(restarted, tokens['access_token'], 'tools/list').status_code == 200
    raw = (configured.parent / 'oauth.sqlite3').read_bytes()
    for secret in [PARTICIPANT, tokens['access_token'], tokens['refresh_token'], registration['client_secret'], code]:
        assert secret.encode() not in raw
    assert (configured.parent / 'oauth.sqlite3').stat().st_mode & 0o077 == 0
    assert client.get('/admin/api/oauth').status_code == 401
    grants = client.get('/admin/api/oauth', headers=admin()).json()['grants']
    assert len(grants) == 1 and grants[0]['participant'] == 'tester'
    assert client.post('/admin/api/oauth/revoke', headers=admin(), json={'grant_id': grants[0]['id']}).status_code == 200
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 401


def test_pkce_resource_redirect_binding_and_code_replay(client):
    registration = register(client)
    code, verifier = authorize(client, registration)
    assert exchange(client, registration, code, 'x' * 64).status_code == 400
    assert exchange(client, registration, code, verifier, resource='https://other.test/mcp').json()['error'] == 'invalid_target'
    assert exchange(client, registration, code, verifier, redirect_uri='https://chatgpt.com/wrong').status_code == 400
    wrong_client = register(client)
    assert exchange(client, wrong_client, code, verifier).status_code == 400
    assert exchange(client, registration, code, verifier).status_code == 200
    assert exchange(client, registration, code, verifier).status_code == 400
    assert rpc(client, 'dl_at_' + 'invalid' * 10, 'tools/list').status_code == 401


def test_refresh_rotation_reuse_detection_and_resource_binding(client):
    registration = register(client)
    code, verifier = authorize(client, registration)
    tokens = exchange(client, registration, code, verifier).json()
    data = {'client_id': registration['client_id'], 'grant_type': 'refresh_token',
            'refresh_token': tokens['refresh_token'], 'resource': ISSUER + '/mcp'}
    assert client.post('/token', data={**data, 'resource': 'https://other.test/mcp'}).status_code == 400
    assert client.post('/token', data={**data, 'scope': 'write'}).status_code == 400
    refreshed = client.post('/token', data=data)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['refresh_token'] != tokens['refresh_token']
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 401
    assert rpc(client, refreshed.json()['access_token'], 'tools/list').status_code == 200
    assert client.post('/token', data=data).status_code == 400
    assert rpc(client, refreshed.json()['access_token'], 'tools/list').status_code == 401


@pytest.mark.parametrize('method', ['none', 'client_secret_post', 'client_secret_basic'])
def test_client_authentication_and_standard_revocation(client, method):
    registration = register(client, method)
    code, verifier = authorize(client, registration)
    form = {'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier,
            'resource': ISSUER + '/mcp', 'redirect_uri': REDIRECT}
    headers = {}
    if method == 'client_secret_basic':
        assert client.post('/token', data={**form, 'client_id': registration['client_id']}).status_code == 401
        headers['Authorization'] = 'Basic ' + base64.b64encode((registration['client_id'] + ':' + registration['client_secret']).encode()).decode()
    else:
        form['client_id'] = registration['client_id']
        if method == 'client_secret_post':
            assert client.post('/token', data=form).status_code == 401
            form['client_secret'] = registration['client_secret']
    response = client.post('/token', data=form, headers=headers)
    assert response.status_code == 200, response.text
    tokens = response.json()
    revoke = {'token': tokens['refresh_token'], 'client_id': registration['client_id']}
    if method == 'client_secret_post':
        revoke['client_secret'] = registration['client_secret']
    assert client.post('/revoke', data=revoke, headers=headers).status_code == 200
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 401
    assert client.post('/revoke', data={**revoke, 'token': 'unknown'}, headers=headers).status_code == 200


@pytest.mark.parametrize('redirect', ['http://evil.test/callback', 'https://evil.test/callback',
    'https://chatgpt.com.evil.test/callback', 'https://user@chatgpt.com/callback',
    'https://chatgpt.com/callback#fragment', 'https://chatgpt.com/callback?state=fixed', 'http://localhost:9999/callback'])
def test_registration_rejects_unsafe_or_unapproved_redirects(client, redirect):
    response = client.post('/register', json={'redirect_uris': [redirect], 'token_endpoint_auth_method': 'none'})
    assert response.status_code == 400
    assert response.json()['error'] == 'invalid_redirect_uri'


def test_consent_csrf_origin_denial_and_wrong_credentials(client):
    registration = register(client)
    verifier, page = begin(client, registration)
    response = client.post('/oauth/consent', data={**form_values(page), 'action': 'login', 'participant_token': PARTICIPANT}, headers={'Origin': 'https://evil.test'})
    assert response.status_code == 403
    response = client.post('/oauth/consent', data={**form_values(page), 'csrf': 'incorrect', 'action': 'approve'}, follow_redirects=False)
    assert response.status_code == 403
    wrong = login(client, page, ADMIN)
    assert wrong.status_code == 401 and ADMIN not in wrong.text
    assert 'Invalid participant token' in wrong.text
    denied = client.post('/oauth/consent', data={**form_values(wrong), 'action': 'deny'}, follow_redirects=False)
    params = parse_qs(urlsplit(denied.headers['location']).query)
    assert params['error'] == ['access_denied'] and params['iss'] == [ISSUER]
    assert params['state'] == ['opaque-client-state']
    assert client.post('/oauth/consent', data={**form_values(wrong), 'action': 'approve'}, follow_redirects=False).status_code == 403


def test_participant_revocation_cannot_be_revived_by_readding_same_token(client):
    registration = register(client)
    code, verifier = authorize(client, registration)
    tokens = exchange(client, registration, code, verifier).json()
    original = client.get('/admin/api/state', headers=admin()).json()
    assert client.post('/admin/api/participants/revoke', headers=admin(), json={'revision': original['revision'], 'name': 'tester'}).status_code == 200
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 401
    current = client.get('/admin/api/state', headers=admin()).json()
    assert client.post('/admin/api/publish', headers=admin(), json={'revision': current['revision'], 'state': original['state']}).status_code == 200
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 401
    refreshed = client.post('/token', data={'grant_type': 'refresh_token', 'client_id': registration['client_id'], 'refresh_token': tokens['refresh_token']})
    assert refreshed.status_code == 400


def test_dataset_permissions_shrink_without_automatic_expansion(client):
    original = client.get('/admin/api/state', headers=admin()).json()
    other = {**original['state']['catalog']['datasets'][0], 'id': 'other'}
    original['state']['catalog']['datasets'].append(other)
    assert client.post('/admin/api/publish', headers=admin(), json={'revision': original['revision'], 'state': original['state']}).status_code == 200
    registration = register(client)
    code, verifier = authorize(client, registration)
    tokens = exchange(client, registration, code, verifier).json()
    current = client.get('/admin/api/state', headers=admin()).json()
    current['state']['participants'][0]['datasets'].append('other')
    assert client.post('/admin/api/publish', headers=admin(), json={'revision': current['revision'], 'state': current['state']}).status_code == 200
    result = rpc(client, tokens['access_token'], 'tools/call', {'name': 'query_dataset', 'arguments': {'query': {'dataset': 'other'}}})
    assert result.json()['result']['isError'] is True


def test_expiry_and_issuer_isolation(client, configured):
    registration = register(client)
    code, verifier = authorize(client, registration)
    tokens = exchange(client, registration, code, verifier).json()
    with sqlite3.connect(configured.parent / 'oauth.sqlite3') as db:
        db.execute("UPDATE tokens SET expires=? WHERE kind='access'", (time.time() - 1,))
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 401
    with pytest.raises(ValueError, match='another issuer'):
        OAuthProvider(Store(configured), 'https://other.test')
    second = configured.parent / 'second'
    second.mkdir()
    (second / 'instance.json').write_bytes(configured.read_bytes())
    provider = OAuthProvider(Store(second / 'instance.json'), 'https://other.test')
    assert provider.resolve_participant(tokens['access_token']) is None


def test_discovery_cors_errors_and_registration_rate_limit(client):
    preflight = client.options('/token', headers={'Origin': 'https://chatgpt.com', 'Access-Control-Request-Method': 'POST'})
    assert preflight.status_code == 200 and preflight.headers['access-control-allow-origin'] == '*'
    assert client.post('/register', content='not json').status_code == 400
    registration = register(client)
    response = client.get('/authorize', params={'client_id': registration['client_id'], 'redirect_uri': REDIRECT,
        'response_type': 'code', 'code_challenge': 'x'*43, 'scope': 'write', 'state': 'state'}, follow_redirects=False)
    params = parse_qs(urlsplit(response.headers['location']).query)
    assert params['error'] == ['invalid_scope'] and params['iss'] == [ISSUER]
    for _ in range(30):
        response = client.post('/register', json={'redirect_uris': [REDIRECT], 'token_endpoint_auth_method': 'none'})
    assert response.status_code == 429


@pytest.mark.parametrize('override', [
    {'code_challenge_method': 'plain'}, {'code_challenge': 'too-short'},
    {'resource': 'https://other.test/mcp'}, {'response_type': 'token'}])
def test_invalid_authorization_never_issues_a_code(client, override):
    registration = register(client)
    params = {'client_id': registration['client_id'], 'redirect_uri': REDIRECT, 'response_type': 'code',
              'code_challenge': 'x' * 43, 'code_challenge_method': 'S256', 'scope': SCOPE,
              'resource': ISSUER + '/mcp', 'state': 'state', **override}
    response = client.get('/authorize', params=params, follow_redirects=False)
    assert response.status_code == 302
    result = parse_qs(urlsplit(response.headers['location']).query)
    assert 'error' in result and 'code' not in result and result['iss'] == [ISSUER]


def test_expired_code_and_grant_and_cross_client_revocation(client, configured):
    registration = register(client)
    code, verifier = authorize(client, registration)
    with sqlite3.connect(configured.parent / 'oauth.sqlite3') as db:
        db.execute("UPDATE tokens SET expires=? WHERE kind='code'", (time.time() - 1,))
    assert exchange(client, registration, code, verifier).status_code == 400
    code, verifier = authorize(client, registration)
    tokens = exchange(client, registration, code, verifier).json()
    other = register(client)
    assert client.post('/revoke', data={'client_id': other['client_id'], 'token': tokens['access_token']}).status_code == 200
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 200
    lower = client.post('/mcp', headers={'Authorization': 'bearer ' + tokens['access_token'], 'Accept': 'application/json, text/event-stream'},
        json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
    assert lower.status_code == 200
    with sqlite3.connect(configured.parent / 'oauth.sqlite3') as db:
        db.execute('UPDATE grants SET expires=?', (time.time() - 1,))
    assert rpc(client, tokens['access_token'], 'tools/list').status_code == 401
    refreshed = client.post('/token', data={'client_id': registration['client_id'], 'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']})
    assert refreshed.status_code == 400


def test_parallel_signins_and_reloads_do_not_invalidate_forms(client):
    registration = register(client)
    _, first = begin(client, registration)
    first_values = form_values(first)
    _, second = begin(client, registration)
    assert first_values['csrf'] != form_values(second)['csrf']
    review = login(client, first)
    assert review.status_code == 200
    assert form_values(review)['csrf'] == first_values['csrf']
    reload_page = client.get('/oauth/consent', params={'request': first_values['request']})
    assert form_values(reload_page)['csrf'] == first_values['csrf']
    approved = client.post('/oauth/consent', headers={'Origin': ISSUER},
        data={**first_values, 'action': 'approve'}, follow_redirects=False)
    assert approved.status_code == 302
    assert login(client, second).status_code == 200


def test_missing_browser_cookie_still_blocks_consent(client):
    registration = register(client)
    _, page = begin(client, registration)
    client.cookies.clear()
    rejected = login(client, page)
    assert rejected.status_code == 403
    assert 'cookie_missing' in rejected.json()['error_description']
