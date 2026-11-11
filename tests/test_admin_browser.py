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
                if httpx.get(url + "/health").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.1)
        with sync_playwright() as driver:
            browser = driver.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "/admin")
            page.locator("#token").fill(admin_token)
            page.locator("#load").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Loaded synthetic demo configuration'")
            page.locator("#sql_password").fill("Synthetic password#;{}-123!")
            page.locator("#save_password").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'SQL password saved. Test the connection next.'")
            assert page.locator("#sql_password").input_value() == ""
            assert (tmp_path / "sql-password").read_text() == "Synthetic password#;{}-123!"
            page.locator("#inspect").click()
            page.wait_for_function("document.querySelector('#status').textContent === 'Connection verified'")
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
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
