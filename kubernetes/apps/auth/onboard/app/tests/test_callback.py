"""Browser regression: uv run --project observability python -m unittest
discover -s kubernetes/apps/auth/onboard/app/tests -v.
Uses synthetic callbacks and intercepted assets; no identity provider access.
"""

import json
import unittest
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

PAGE = Path(__file__).parents[1] / "static" / "callback.html"
PAYLOAD = '<img src=x onerror="window.injected=true"><svg onload="window.injected=true"></svg>'


class Callback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def run_callback(self, query, expected, *, exchange_error=None):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        context.add_init_script("sessionStorage.setItem('onboard.pkce_state', 'expected')")
        page = context.new_page()

        def respond(route):
            path = route.request.url.split("?", 1)[0]
            if path.endswith("/callback"):
                route.fulfill(content_type="text/html", body=PAGE.read_text())
            elif path.endswith("/app.js"):
                script = "window.Onboard={exchangeCodeForTokens:async()=>{throw new Error(" + json.dumps(exchange_error) + ")}}"
                route.fulfill(content_type="application/javascript", body=script)
            else:
                route.fulfill(body="")

        page.route("**/*", respond)
        page.goto("https://onboard.example.org/callback?" + urlencode(query))
        page.locator(".error").wait_for()
        self.assertEqual(page.locator(".error").text_content(), expected)
        self.assertEqual(page.locator("#card img, #card svg, #card script").count(), 0)
        self.assertFalse(page.evaluate("Boolean(window.injected)"))
        self.assertEqual(page.get_by_role("link", name="Try again").get_attribute("href"), "/")

    def test_provider_error_and_description_are_literal_text(self):
        self.run_callback({"error": PAYLOAD, "error_description": PAYLOAD}, f"{PAYLOAD}: {PAYLOAD}")

    def test_token_exchange_error_is_literal_text(self):
        self.run_callback({"code": "synthetic", "state": "expected"}, PAYLOAD, exchange_error=PAYLOAD)

    def test_missing_code_and_state_mismatch_still_explain_failure(self):
        self.run_callback({}, "Missing authorization code in callback URL.")
        self.run_callback({"code": "synthetic", "state": "wrong"},
                          "State mismatch — the sign-in flow may have been started in a different tab. Please try again.")


if __name__ == "__main__":
    unittest.main()
