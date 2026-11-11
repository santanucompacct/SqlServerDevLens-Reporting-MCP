import sys
"""Exercise the actual private webpage in the optional browser CI job."""
import os
from pathlib import Path
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("DEVLENS_BROWSER_TEST") != "1", reason="Browser test is opt-in")


def test_private_admin_workflow(tmp_path):
    from playwright.sync_api import sync_playwright
    state = tmp_path / "instance.json"
    state.write_text(Path("examples/instance.json").read_text())
    admin_token = "synthetic-browser-admin-token-1234567890"
    environment = {**os.environ, "DEVLENS_STATE": str(state), "DEVLENS_DEMO": "1",
                   "DEVLENS_ADMIN_TOKEN": admin_token, "DEVLENS_PORT": "8129"}
    process = subprocess.Popen(["devlens-reporting"], env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = "http://127.0.0.1:8129"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/health", trust_env=False).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.1)
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('DEVLENS_CHROMIUM'))
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "/admin")
            page.locator("#token").fill(admin_token)
            page.locator("#load").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Loaded synthetic demo configuration'")
            assert page.locator("#encrypt").is_checked()
            assert not page.locator("#trust_server_certificate").is_checked()
            assert page.locator('#inspect').is_disabled()
            page.locator('#server').fill('synthetic-sql')
            page.locator('#database').fill('synthetic-db')
            page.locator('#username').fill('report-reader')
            page.locator("#trust_server_certificate").check()
            page.locator('#save_connection').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'SQL details saved. Test SQL to list objects.'")
            page.locator("#sql_password").fill("Synthetic password#;{}-123!")
            page.locator("#save_password").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'SQL password saved. Test the connection next.'")
            assert page.locator("#sql_password").input_value() == ""
            assert (tmp_path / "sql-password").read_text() == "Synthetic password#;{}-123!"
            page.locator("#inspect").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Demo metadata loaded (synthetic data)'")
            page.locator('#object_select').select_option(index=1)
            page.wait_for_function("document.querySelector('#status').textContent === 'Object fields loaded'")
            assert page.locator('#field_rows tr').count() == 4
            remark = '<script>alert(1)</script> Net bill amount after credits'
            page.get_by_label('Remarks for amount', exact=True).fill(remark)
            page.locator('#save_fields').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Field remarks saved'")
            page.reload()
            page.locator('#token').fill(admin_token)
            page.locator('#load').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Loaded synthetic demo configuration'")
            assert page.locator('#server').input_value() == 'synthetic-sql'
            assert page.locator('#database').input_value() == 'synthetic-db'
            assert page.locator('#username').input_value() == 'report-reader'
            assert page.locator('#sql_password').input_value() == ''
            assert page.locator('#save_connection').text_content() == 'Update SQL details'
            page.locator('#object_select').select_option(index=1)
            page.wait_for_function("document.querySelector('#status').textContent === 'Object fields loaded'")
            assert page.get_by_label('Remarks for amount', exact=True).input_value() == remark
            assert '[synthetic-db]' in page.locator('#grant_help').text_content()
            page.screenshot(path=os.environ.get('DEVLENS_SCREENSHOT', str(tmp_path / 'setup-desktop.png')), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.locator('details.advanced > summary').click()
            page.locator("#preview").click()
            page.wait_for_function("document.querySelector('#results').textContent.includes('Example A')")
            page.locator("#new_user").fill("browser_participant")
            page.locator("#generate").click()
            page.wait_for_function("document.querySelector('#new_token').textContent.includes('Copy and securely share')")
            page.locator("#participants").fill(page.locator("#participants").input_value().replace('"datasets": []', '"datasets": ["sales"]'))
            page.locator("#publish").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Configuration published'")
            assert not errors
            assert 'browser_participant' in state.read_text()
            saved = httpx.get(url + "/admin/api/state", trust_env=False, headers={"Authorization": "Bearer " + admin_token}).json()
            assert saved["state"]["connection"]["encrypt"] is True
            assert saved["state"]["connection"]["trust_server_certificate"] is True
            page.locator("#load").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Loaded synthetic demo configuration'")
            assert page.locator("#trust_server_certificate").is_checked()
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_guided_publishing_and_participant_ui(tmp_path):
    from playwright.sync_api import sync_playwright
    state = tmp_path / 'instance.json'
    state.write_text(Path('examples/instance.json').read_text())
    token = 'synthetic-guided-admin-token-1234567890'
    environment = {**os.environ, 'DEVLENS_STATE': str(state), 'DEVLENS_DEMO': '1',
                   'DEVLENS_ADMIN_TOKEN': token, 'DEVLENS_PORT': '8130'}
    process = subprocess.Popen(['devlens-reporting'], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = 'http://127.0.0.1:8130'
    try:
        for _ in range(100):
            try:
                if httpx.get(url + '/health', trust_env=False).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(.1)
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('DEVLENS_CHROMIUM'))
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url + '/admin')
            page.locator('#token').fill(token)
            page.locator('#load').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Loaded synthetic demo configuration'")
            for key, value in [('server', 'synthetic'), ('database', 'synthetic'), ('username', 'reader')]:
                page.locator('#' + key).fill(value)
            page.locator('#save_connection').click()
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('SQL details saved.')")
            page.locator('#inspect').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Demo metadata loaded (synthetic data)'")
            page.locator('#object_select').select_option(index=1)
            page.wait_for_function("document.querySelector('#status').textContent === 'Object fields loaded'")
            for name in ['customer', 'doc_date', 'amount', 'branch']:
                page.get_by_label('Remarks for ' + name, exact=True).fill('Meaning of ' + name)
            assert page.locator('#prepare_dataset').is_disabled()
            page.locator('#save_fields').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Field remarks saved'")
            page.locator('#dataset_id').fill('franchise_sales')
            page.locator('#dataset_description').fill('Franchise sales reporting')
            page.locator('#dataset_grain').fill('One sales row')
            page.locator('#dataset_rules').fill('Do not sum identifiers.\nAmounts are synthetic for this test.')
            page.locator('#prepare_dataset').click()
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Dataset prepared.')")
            assert page.locator('#prepared_columns tr').count() == 4
            assert page.get_by_label('Reporting type for amount', exact=True).input_value() == 'number'
            assert page.locator('#replace_example').is_checked()
            assert page.locator('#publish_dataset').is_disabled()
            page.locator('#publish_ack').check()
            page.locator('#publish_dataset').click()
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Reporting dataset published.')")
            page.locator('#preview_from').fill('2026-01-01')
            page.locator('#preview_to').fill('2026-01-02')
            page.locator('#preview_dataset').click()
            page.wait_for_function("document.querySelector('#published_preview').textContent.includes('Example A')")
            page.locator('#participant_name').fill('report_tester')
            page.locator('#dataset_grants input[value="franchise_sales"]').check()
            page.locator('#create_participant').click()
            page.wait_for_function("document.querySelector('#status').textContent.startsWith('Participant active.')")
            participant_token = page.locator('#issued_token').text_content()
            assert len(participant_token) >= 32
            assert participant_token not in state.read_text()
            page.locator('#test_participant').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Participant token, dataset grant and read-only query passed'")
            assert 'franchise_sales' in page.locator('#participant_test_result').text_content()
            page.screenshot(path=str(tmp_path / 'publishing-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.on('dialog', lambda dialog: dialog.accept())
            page.locator('#participant_rows button').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Participant access revoked'")
            page.locator('#test_participant').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Invalid participant token'")
            page.reload()
            page.locator('#token').fill(token)
            page.locator('#load').click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Loaded synthetic demo configuration'")
            assert page.locator('#issued_token_panel').is_hidden()
            assert page.locator('#participant_test_token').input_value() == ''
            assert page.locator('#published_dataset').input_value() == 'franchise_sales'
            assert not errors
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_oauth_consent_and_admin_session_revocation(tmp_path):
    """Exercise real HTTPS and Secure cookies with a disposable self-signed certificate."""
    import base64
    import hashlib
    import json
    import secrets
    from urllib.parse import parse_qs, urlencode, urlsplit
    from playwright.sync_api import sync_playwright
    state = tmp_path / 'instance.json'
    value = json.loads(Path('examples/instance.json').read_text())
    participant_token = 'synthetic-browser-participant-12345678901234567890'
    value['participants'] = [{'name': 'oauth_tester', 'token_sha256': hashlib.sha256(participant_token.encode()).hexdigest(), 'datasets': ['sales']}]
    state.write_text(json.dumps(value))
    admin_token = 'synthetic-browser-oauth-admin-123456789012345'
    issuer = local = 'https://localhost:8131'
    redirect = 'https://chatgpt.com/connector_platform_oauth_redirect'
    key, cert = tmp_path / 'tls.key', tmp_path / 'tls.crt'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    environment = {**os.environ, 'DEVLENS_STATE': str(state), 'DEVLENS_DEMO': '1', 'DEVLENS_PORT': '8131',
                   'DEVLENS_ADMIN_TOKEN': admin_token, 'DEVLENS_OAUTH_ISSUER': issuer,
                   'DEVLENS_ALLOWED_HOSTS': 'reporting.test,localhost,127.0.0.1:*'}
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'devlens_reporting.app:create_app', '--factory',
                                '--host', '127.0.0.1', '--port', '8131', '--ssl-keyfile', str(key), '--ssl-certfile', str(cert)],
                               env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=local, trust_env=False, verify=False) as api:
            for _ in range(100):
                try:
                    if api.get('/health').status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(.1)
            registration = api.post('/register', json={'redirect_uris': [redirect], 'token_endpoint_auth_method': 'none', 'client_name': 'Browser OAuth test'}).json()
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            params = {'client_id': registration['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
                      'scope': 'reporting:read', 'code_challenge': challenge, 'code_challenge_method': 'S256',
                      'state': 'browser-state', 'resource': issuer + '/mcp'}
            with sync_playwright() as driver:
                browser = driver.chromium.launch(executable_path=os.environ.get('DEVLENS_CHROMIUM'))
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                errors, callbacks = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                def consent(route):
                    if route.request.method != 'POST':
                        route.continue_()
                        return
                    response = route.fetch(max_redirects=0)
                    assert response.status in (200, 302), {'status': response.status, 'body': response.text(), 'origin': route.request.headers.get('origin'), 'cookie_present': bool(route.request.headers.get('cookie'))}
                    location = response.headers.get('location', '')
                    if response.status == 302 and location.startswith(redirect + '?'):
                        callbacks.append(location)
                        route.fulfill(status=200, content_type='text/html', body='<p>OAuth callback received</p>')
                    else:
                        route.fulfill(response=response)
                page.route(issuer + '/oauth/consent**', consent)
                page.goto(issuer + '/authorize?' + urlencode(params))
                other_tab = page.context.new_page()
                other_tab.goto(issuer + '/authorize?' + urlencode(params))
                page.reload()
                other_tab.close()
                page.get_by_label('Participant token', exact=True).fill(participant_token)
                page.get_by_role('button', name='Continue', exact=True).click()
                page.get_by_role('heading', name='Review reporting access').wait_for()
                assert 'sales' in page.locator('main').text_content()
                assert participant_token not in page.content()
                screenshot_dir = Path(os.environ.get('DEVLENS_SCREENSHOT', str(tmp_path / 'setup-desktop.png'))).parent
                page.screenshot(path=str(screenshot_dir / 'oauth-consent.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.get_by_role('button', name='Allow read-only reporting access').click()
                page.get_by_text('OAuth callback received', exact=True).wait_for()
                callback_params = parse_qs(urlsplit(callbacks[-1]).query)
                assert callback_params['iss'] == [issuer] and callback_params['state'] == ['browser-state']
                tokens = api.post('/token', data={'grant_type': 'authorization_code', 'client_id': registration['client_id'],
                    'redirect_uri': redirect, 'code_verifier': verifier, 'code': callback_params['code'][0], 'resource': issuer + '/mcp'}).json()
                assert 'access_token' in tokens
                page.goto(local + '/admin')
                page.locator('#token').fill(admin_token)
                page.locator('#load').click()
                page.wait_for_function("document.querySelector('#status').textContent === 'Loaded synthetic demo configuration'")
                assert 'OAuth is enabled' in page.locator('#oauth_status').text_content()
                assert page.locator('#oauth_grants tr').count() == 1
                assert 'oauth_tester' in page.locator('#oauth_grants').text_content()
                page.on('dialog', lambda dialog: dialog.accept())
                page.get_by_role('button', name='Revoke OAuth access').click()
                page.wait_for_function("document.querySelector('#status').textContent === 'OAuth connection revoked'")
                denied = api.post('/mcp', headers={'Authorization': 'Bearer ' + tokens['access_token']}, json={})
                assert denied.status_code == 401
                assert not errors
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
