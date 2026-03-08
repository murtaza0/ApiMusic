"""
Self-contained hCaptcha Bypass
--------------------------------
Uses headless Chromium with playwright-stealth and an auto-click injection
to solve hCaptcha challenges without any paid service.

How it works:
  1. Playwright routes a request to the target host and serves a minimal HTML
     page embedding the hCaptcha widget — this gives hCaptcha the correct
     host= parameter so the enterprise scoring profile fires.
  2. playwright-stealth patches navigator.webdriver and ~20 other browser
     fingerprint signals before every page load.
  3. The auto-click init script runs inside every frame including the hCaptcha
     iframe; it waits for #checkbox to render and calls .click() on it.
  4. Belt-and-suspenders: after the widget loads, Playwright's own
     frame_locator click is also dispatched so both trusted- and script-
     level clicks hit the checkbox.
  5. hCaptcha scores the fingerprint.  For passive/enterprise sites a token
     is issued directly.  The token is read from window.__hcapToken (set by
     the callback) and from the hidden h-captcha-response textarea.

Rate-limit note:
  hCaptcha Enterprise limits requests per IP.  If the server IP has been
  used heavily for captcha requests (e.g. during repeated testing), expect
  a 429 back-off period of a few minutes before tokens are issued again.
  The solver retries with exponential back-off to handle this automatically.
"""

from __future__ import annotations

import os
import random
import time
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

# ---------------------------------------------------------------------------
# Auto-click injection script — injected into every frame as an init script.
# Fires inside the hCaptcha checkbox iframe and clicks #checkbox as soon as
# it appears.
# ---------------------------------------------------------------------------

AUTO_CLICK_SCRIPT = r"""
(function () {
  'use strict';

  var isHcaptchaFrame = (
    window.location.href.indexOf('hcaptcha.com') !== -1 ||
    window.location.href.indexOf('newassets.hcaptcha.com') !== -1
  );
  if (!isHcaptchaFrame) return;

  var clicked = false;

  function tryClick() {
    var cb = document.querySelector('#checkbox');
    if (!cb) return false;
    if (cb.getAttribute('data-checked') === 'true') return true;
    if (!clicked) {
      try { cb.click(); clicked = true; } catch (e) {}
    }
    return false;
  }

  // Fire at multiple points so we catch the element whenever it renders
  [100, 300, 600, 1000, 1500, 2000, 3000].forEach(function(d) {
    setTimeout(tryClick, d);
  });

  var iv = setInterval(function () {
    if (tryClick()) clearInterval(iv);
  }, 400);

  // Give up after 45 s to avoid memory leaks
  setTimeout(function () { clearInterval(iv); }, 45000);
})();
"""

# ---------------------------------------------------------------------------
# HTML page served at the target host URL
# ---------------------------------------------------------------------------

HCAPTCHA_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>verify</title>
<style>body{{margin:0;padding:60px 40px;}}</style>
</head>
<body>
<div class="h-captcha"
     id="hcap-widget"
     data-sitekey="{sitekey}"
     data-callback="__hcapDone"
     data-size="normal">
</div>
<script>
  window.__hcapToken = null;
  window.__hcapDone  = function(tkn) {{
    if (typeof tkn === 'string' && tkn.length > 10) {{
      window.__hcapToken = tkn;
      return;
    }}
    var ta = document.querySelector('[name="h-captcha-response"]');
    if (ta && ta.value) window.__hcapToken = ta.value;
  }};
  // Poll textarea as a safety net (some integrations skip the callback)
  setInterval(function () {{
    if (window.__hcapToken) return;
    var ta = document.querySelector('[name="h-captcha-response"]');
    if (ta && ta.value && ta.value.length > 20) window.__hcapToken = ta.value;
  }}, 250);
</script>
<script src="https://js.hcaptcha.com/1/api.js" async defer></script>
</body>
</html>
"""

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


# ---------------------------------------------------------------------------
# Core solver
# ---------------------------------------------------------------------------

class HCaptchaSolver:
    """
    Solves hCaptcha using headless Chromium + playwright-stealth + auto-click.

    playwright-stealth patches ~20 browser fingerprint signals (webdriver,
    chrome runtime, plugins, WebGL vendor, etc.) before every navigation,
    making the headless browser indistinguishable from a real one for most
    checks.
    """

    _stealth = Stealth()

    def __init__(self, sitekey: str, host: str, timeout_ms: int = 90_000) -> None:
        self.sitekey    = sitekey
        self.host       = host.rstrip("/")
        self.timeout_ms = timeout_ms
        self.ua         = random.choice(USER_AGENTS)

    def _build_html(self) -> str:
        return HCAPTCHA_PAGE.format(sitekey=self.sitekey)

    def solve(self) -> str:
        html          = self._build_html()
        intercept_url = self.host + "/__hcap_verify__"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1280,720",
                ],
            )

            context = browser.new_context(
                user_agent=self.ua,
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                timezone_id="America/New_York",
                java_script_enabled=True,
            )

            # Inject auto-click into every frame (including hCaptcha iframes)
            context.add_init_script(AUTO_CLICK_SCRIPT)

            page = context.new_page()

            # Apply all playwright-stealth patches to this page
            self._stealth.apply_stealth_sync(page)

            # Track 429s from hCaptcha's backend
            rate_limited = {"hit": False}
            def on_response(resp):
                if "hcaptcha.com" in resp.url and resp.status == 429:
                    rate_limited["hit"] = True
            page.on("response", on_response)

            # Route the target host so hCaptcha sees the correct host= param
            page.route(
                intercept_url,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=html,
                ),
            )

            print(f"  [bypass] Opening browser for {self.host} ...")
            try:
                page.goto(intercept_url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                browser.close()
                raise RuntimeError(f"Failed to load hCaptcha page: {exc}") from exc

            print("  [bypass] Waiting for hCaptcha widget ...")
            try:
                page.wait_for_function(
                    "() => typeof window.hcaptcha !== 'undefined'",
                    timeout=25_000,
                )
            except PlaywrightTimeout:
                browser.close()
                raise RuntimeError("hCaptcha JS did not load within 25 s.")

            # Give the widget time to render, then also fire Playwright's
            # native frame-locator click (trusted input event)
            page.wait_for_timeout(3_000)

            try:
                cb_locator = page.frame_locator(
                    "iframe[src*='frame=checkbox']"
                ).locator("#checkbox")
                if cb_locator.count() > 0:
                    cb_locator.click(timeout=5_000, force=True)
                    print("  [bypass] Frame-locator click dispatched.")
            except Exception:
                pass  # init-script click already ran; this is just belt-and-suspenders

            # Poll for the token
            print("  [bypass] Waiting for token ...")
            token: Optional[str] = None
            deadline = time.monotonic() + self.timeout_ms / 1000
            poll_interval = 2.0

            while time.monotonic() < deadline:
                page.wait_for_timeout(int(poll_interval * 1000))

                if rate_limited["hit"]:
                    browser.close()
                    raise RuntimeError(
                        "hCaptcha returned HTTP 429 (rate limited). "
                        "The server IP has made too many captcha requests recently. "
                        "Wait a few minutes then retry, or set CAPTCHA_PROVIDER + "
                        "CAPTCHA_API_KEY in Replit Secrets to use a paid solver."
                    )

                token = page.evaluate("() => window.__hcapToken")
                if token and isinstance(token, str) and len(token) > 20:
                    break

                # Fallback: hidden textarea
                token = page.evaluate(
                    "() => { var ta=document.querySelector('[name=\"h-captcha-response\"]'); "
                    "return (ta && ta.value && ta.value.length>20) ? ta.value : null; }"
                )
                if token:
                    break

                poll_interval = min(poll_interval * 1.1, 5.0)

            browser.close()

        if not token:
            raise RuntimeError(
                f"No token produced within {self.timeout_ms // 1000} s. "
                "The site may require a visual image challenge that cannot be "
                "automated without a paid solver (2captcha / capsolver / anticaptcha)."
            )

        print(f"  [bypass] Token obtained ({len(token)} chars): {token[:40]}...")
        return token


# ---------------------------------------------------------------------------
# Public wrapper with retries and exponential back-off
# ---------------------------------------------------------------------------

def get_hcaptcha_token_bypass(
    sitekey: str,
    host: str,
    max_retries: int = 5,
) -> str:
    """
    Solve hCaptcha using headless browser + playwright-stealth + auto-click.

    Retries up to *max_retries* times with exponential back-off.
    Raises RuntimeError on total failure.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"  [bypass] Attempt {attempt}/{max_retries} ...")
            solver = HCaptchaSolver(sitekey=sitekey, host=host)
            return solver.solve()
        except RuntimeError as exc:
            last_error = exc
            msg = str(exc)
            print(f"  [bypass] Attempt {attempt} failed: {msg}")

            if "429" in msg or "rate limit" in msg.lower():
                # Rate limited — back off longer
                delay = random.uniform(30, 60) * attempt
                print(f"  [bypass] Rate limited — backing off {delay:.0f} s ...")
            elif attempt < max_retries:
                delay = random.uniform(3, 8) * attempt
                print(f"  [bypass] Retrying in {delay:.1f} s ...")
            else:
                break

            time.sleep(delay)
        except Exception as exc:
            last_error = exc
            print(f"  [bypass] Unexpected error: {exc}")
            if attempt < max_retries:
                time.sleep(random.uniform(3, 6) * attempt)

    raise RuntimeError(
        f"hCaptcha bypass failed after {max_retries} attempts "
        f"(last error: {last_error}). "
        "Set CAPTCHA_PROVIDER and CAPTCHA_API_KEY in Replit Secrets to use a "
        "paid solver (2captcha / capsolver / anticaptcha)."
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sitekey = os.getenv("HCAPTCHA_SITEKEY", "6520ce9c-a8b2-4cbe-b698-687e90448dec")
    host    = os.getenv("HCAPTCHA_HOST",    "https://musichero.ai")

    print(f"Testing bypass\n  sitekey : {sitekey}\n  host    : {host}\n")
    try:
        token = get_hcaptcha_token_bypass(sitekey, host)
        print(f"\nToken ({len(token)} chars):\n{token}")
    except RuntimeError as e:
        print(f"\nFailed: {e}")
