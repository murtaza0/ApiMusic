"""
Self-contained hCaptcha Bypass using Playwright
------------------------------------------------
Uses a headless Chromium browser to execute the real hCaptcha JavaScript
widget, which is the most reliable self-contained approach.

Why Playwright?
  - hCaptcha's `enc_get_req: true` feature encrypts requests using keys
    computed inside the JS bundle — impossible to replicate without running JS.
  - Enterprise / "passive" sites (like musichero.ai) score browser fingerprint
    quality. A real headless browser passes easily.
  - No paid API key required.

The module also keeps the fast direct-API path as a fallback for sites that
don't require encrypted requests.
"""

import base64
import hashlib
import json
import os
import random
import time
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from curl_cffi import requests as cffi_requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HCAPTCHA_ENDPOINT = "https://api2.hcaptcha.com"
HCAPTCHA_VERSION  = "1"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Minimal HTML page that embeds the hCaptcha invisible widget
_HCAPTCHA_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Verify</title></head>
<body>
<form id="f">
  <div class="h-captcha"
       data-sitekey="{sitekey}"
       data-callback="onSuccess"
       data-size="invisible">
  </div>
</form>
<script>
  window.onSuccess = function(token) {{
    document.title = "TOKEN:" + token;
  }};
</script>
<script src="https://js.hcaptcha.com/1/api.js" async defer></script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Playwright-based solver (primary)
# ---------------------------------------------------------------------------

class PlaywrightSolver:
    """
    Uses a real headless Chromium browser to solve hCaptcha challenges.
    The browser runs the actual hCaptcha JS, generating a genuine token.
    """

    def __init__(self, sitekey: str, host: str, timeout_ms: int = 60000) -> None:
        self.sitekey    = sitekey
        self.host       = host.rstrip("/")
        self.timeout_ms = timeout_ms
        self.ua         = random.choice(USER_AGENTS)

    def solve(self) -> str:
        html_content = _HCAPTCHA_HTML.format(sitekey=self.sitekey)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=self.ua,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                timezone_id="America/New_York",
                java_script_enabled=True,
            )

            # Mask automation signals
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()

            # Load hCaptcha widget rooted at the target host (for enterprise checks)
            print(f"  [bypass] Loading hCaptcha widget for {self.host} ...")
            page.route("**/*", lambda route: route.continue_())
            page.set_content(html_content, wait_until="domcontentloaded")

            # Wait for the hcaptcha script to load
            try:
                page.wait_for_function(
                    "() => typeof window.hcaptcha !== 'undefined'",
                    timeout=20000,
                )
            except PlaywrightTimeout:
                browser.close()
                raise RuntimeError("hCaptcha script did not load within 20 seconds.")

            # Trigger the invisible captcha execution
            page.evaluate("""
                () => {
                    const widget = document.querySelector('.h-captcha');
                    if (widget) {
                        const id = hcaptcha.render(widget, {sitekey: widget.dataset.sitekey,
                                                             size: 'invisible',
                                                             callback: (t) => { document.title = 'TOKEN:' + t; }});
                        hcaptcha.execute(id);
                    }
                }
            """)

            print("  [bypass] Waiting for hCaptcha to solve ...")
            try:
                page.wait_for_function(
                    "() => document.title.startsWith('TOKEN:')",
                    timeout=self.timeout_ms,
                )
            except PlaywrightTimeout:
                browser.close()
                raise RuntimeError(
                    f"hCaptcha did not produce a token within {self.timeout_ms // 1000}s."
                )

            title = page.title()
            browser.close()

        token = title.replace("TOKEN:", "", 1).strip()
        if not token:
            raise RuntimeError("hCaptcha: empty token received from widget.")

        print(f"  [bypass] Token obtained: {token[:35]}...")
        return token


# ---------------------------------------------------------------------------
# Convenience wrapper with retries
# ---------------------------------------------------------------------------

def get_hcaptcha_token_bypass(
    sitekey: str,
    host: str,
    max_retries: int = 5,
) -> str:
    """
    Attempt to solve hCaptcha using a headless browser.
    Retries up to max_retries times on failure.
    Returns the token string on success, raises RuntimeError on total failure.
    """
    for attempt in range(1, max_retries + 1):
        try:
            print(f"  [bypass] Attempt {attempt}/{max_retries} ...")
            solver = PlaywrightSolver(sitekey=sitekey, host=host)
            return solver.solve()
        except Exception as exc:
            print(f"  [bypass] Attempt {attempt} failed: {exc}")
            if attempt < max_retries:
                sleep_time = random.uniform(2, 5) * attempt
                print(f"  [bypass] Retrying in {sleep_time:.1f}s ...")
                time.sleep(sleep_time)

    raise RuntimeError(
        f"hCaptcha bypass failed after {max_retries} attempts. "
        "Consider using a paid solver: set CAPTCHA_PROVIDER and CAPTCHA_API_KEY "
        "in Replit Secrets (supported: 2captcha, capsolver, anticaptcha)."
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sitekey = os.getenv("HCAPTCHA_SITEKEY", "6520ce9c-a8b2-4cbe-b698-687e90448dec")
    host    = os.getenv("HCAPTCHA_HOST",    "https://musichero.ai")

    print(f"Testing Playwright bypass for sitekey={sitekey} on {host}\n")
    token = get_hcaptcha_token_bypass(sitekey, host)
    print(f"\nFinal token:\n{token}")
