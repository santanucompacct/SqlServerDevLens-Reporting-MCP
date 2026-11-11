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
