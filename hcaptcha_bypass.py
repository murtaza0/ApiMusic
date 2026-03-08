"""
hCaptcha Bypass — Multi-Strategy Engine
-----------------------------------------
Strategy 1 (INVISIBLE):  Render hCaptcha in invisible/programmatic mode and
                          call hcaptcha.execute() directly.  For enterprise
                          passive sites (pass=true) this returns a token with
                          no visual challenge.

Strategy 2 (NORMAL-CLICK): Render the normal checkbox widget and use Playwright's
                            frame_locator click (trusted input event) + an init-
                            script auto-click inside the iframe.

Both strategies use playwright-stealth to mask ~20 browser fingerprint signals.

Rate-limit handling:  hCaptcha's getcaptcha endpoint enforces per-IP per-sitekey
rate limits.  Heavy automated testing from the same IP triggers 429s that last
1-2 hours.  Normal production usage (1 captcha per song, a few songs/hour) does
NOT trigger rate limits.  When 429 is detected the solver backs off exponentially.

Fallback: set CAPTCHA_PROVIDER + CAPTCHA_API_KEY env vars to use 2captcha /
capsolver / anticaptcha as a reliable paid alternative.
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Optional

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHECKSITECONFIG_URL = "https://api.hcaptcha.com/checksiteconfig"
HCAP_VERSION        = "5ea3feff9cf1292d7051510930d98c4719f64575"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Auto-click init script — runs inside every frame including hCaptcha iframe
AUTO_CLICK_SCRIPT = r"""
(function () {
  var isHcapFrame = window.location.href.indexOf('hcaptcha.com') !== -1;
  if (!isHcapFrame) return;
  var clicked = false;
  function tryClick() {
    var cb = document.querySelector('#checkbox');
    if (!cb) return;
    if (cb.getAttribute('data-checked') === 'true') return;
    if (!clicked) { try { cb.click(); clicked = true; } catch(e) {} }
  }
  [100,300,600,1000,1500,2000,3000].forEach(function(d){ setTimeout(tryClick,d); });
  var iv = setInterval(function(){ tryClick(); }, 400);
  setTimeout(function(){ clearInterval(iv); }, 45000);
})();
"""

# HTML templates — served at the target host so hCaptcha sees the correct host=
_HTML_INVISIBLE = """\
<!DOCTYPE html><html><head><meta charset="utf-8"><title>v</title></head><body>
<div class="h-captcha" id="hcap"
     data-sitekey="{sitekey}"
     data-callback="__hcapDone"
     data-size="invisible">
</div>
<script>
  window.__hcapToken = null;
  window.__hcapDone  = function(t) {{
    window.__hcapToken = (typeof t==='string'&&t.length>10) ? t
      : (document.querySelector('[name="h-captcha-response"]')||{{}}).value || null;
  }};
  setInterval(function() {{
    var ta = document.querySelector('[name="h-captcha-response"]');
    if (ta && ta.value && ta.value.length > 20 && !window.__hcapToken)
      window.__hcapToken = ta.value;
  }}, 200);
</script>
<script src="https://js.hcaptcha.com/1/api.js" async defer onload="__apiLoaded=true"></script>
<script>window.__apiLoaded = false;</script>
</body></html>
"""

_HTML_NORMAL = """\
<!DOCTYPE html><html><head><meta charset="utf-8"><title>v</title>
<style>body{{margin:0;padding:60px 40px;}}</style>
</head><body>
<div class="h-captcha" id="hcap"
     data-sitekey="{sitekey}"
     data-callback="__hcapDone"
     data-size="normal">
</div>
<script>
  window.__hcapToken = null;
  window.__hcapDone  = function(t) {{
    window.__hcapToken = (typeof t==='string'&&t.length>10) ? t
      : (document.querySelector('[name="h-captcha-response"]')||{{}}).value || null;
  }};
  setInterval(function() {{
    var ta = document.querySelector('[name="h-captcha-response"]');
    if (ta && ta.value && ta.value.length > 20 && !window.__hcapToken)
      window.__hcapToken = ta.value;
  }}, 200);
</script>
<script src="https://js.hcaptcha.com/1/api.js" async defer></script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_passive_site(sitekey: str, host: str) -> bool:
    """Return True if hCaptcha Enterprise reports pass=true for this host."""
    try:
        r = requests.post(
            CHECKSITECONFIG_URL,
            params={"v": HCAP_VERSION, "host": host.replace("https://","").replace("http://",""),
                    "sitekey": sitekey, "sc": 1, "swa": 1, "spst": 1},
            headers={"User-Agent": random.choice(USER_AGENTS),
                     "Origin": "https://newassets.hcaptcha.com"},
            timeout=10,
        )
        return r.json().get("pass", False)
    except Exception:
        return False


def _poll_token(page, timeout_ms: int) -> Optional[str]:
    """Poll page for the hCaptcha token; return it or None on timeout."""
    deadline = time.monotonic() + timeout_ms / 1000
    interval  = 1.5
    while time.monotonic() < deadline:
        page.wait_for_timeout(int(interval * 1000))
        tok = page.evaluate("() => window.__hcapToken")
        if tok and isinstance(tok, str) and len(tok) > 20:
            return tok
        tok = page.evaluate(
            "() => { var ta=document.querySelector('[name=\"h-captcha-response\"]');"
            " return ta&&ta.value&&ta.value.length>20?ta.value:null; }"
        )
        if tok:
            return tok
        interval = min(interval * 1.15, 4.0)
    return None


# ---------------------------------------------------------------------------
# Core solver
# ---------------------------------------------------------------------------

class HCaptchaSolver:
    """
    Solves hCaptcha using headless Chromium with playwright-stealth.

    Tries two strategies in order:
    1. Invisible mode + hcaptcha.execute()  — fastest for passive enterprise sites
    2. Normal checkbox widget + auto-click  — fallback
    """

    _stealth = Stealth()

    def __init__(self, sitekey: str, host: str, timeout_ms: int = 90_000) -> None:
        self.sitekey    = sitekey
        self.host       = host.rstrip("/")
        self.timeout_ms = timeout_ms
        self.ua         = random.choice(USER_AGENTS)
        self._intercept = self.host + "/__hcap_verify__"

    def _launch(self):
        browser = self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
                  "--disable-gpu","--disable-blink-features=AutomationControlled","--window-size=1280,720"],
        )
        ctx = browser.new_context(
            user_agent=self.ua,
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            timezone_id="America/New_York",
        )
        ctx.add_init_script(AUTO_CLICK_SCRIPT)
        page = ctx.new_page()
        self._stealth.apply_stealth_sync(page)
        return browser, page

    def _route_and_goto(self, page, html: str) -> None:
        page.route(self._intercept,
                   lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=html))
        page.goto(self._intercept, wait_until="domcontentloaded", timeout=30_000)

    def _check_rate_limit(self, page) -> dict:
        """Return a mutable dict with key 'v' set to True if hCaptcha 429s our IP."""
        hit = {"v": False}
        def _on_resp(resp):
            if "hcaptcha.com" in resp.url and resp.status == 429:
                hit["v"] = True
        page.on("response", _on_resp)
        return hit

    def _solve_invisible(self) -> Optional[str]:
        """Strategy 1: invisible mode + programmatic execute()."""
        print("  [bypass] Strategy 1: invisible execute() ...")
        html = _HTML_INVISIBLE.format(sitekey=self.sitekey)
        browser, page = self._launch()
        rate_hit = self._check_rate_limit(page)
        self._route_and_goto(page, html)

        try:
            page.wait_for_function("()=>typeof window.hcaptcha!=='undefined'", timeout=20_000)
        except PWTimeout:
            browser.close()
            return None

        page.wait_for_timeout(2_000)

        try:
            page.evaluate(
                "() => window.hcaptcha.render('hcap', {sitekey: arguments[0], "
                "callback: window.__hcapDone, size: 'invisible'})"
            )
        except Exception:
            pass
        try:
            page.evaluate("() => { try { window.hcaptcha.execute(); } catch(e) {} }")
        except Exception:
            pass

        tok = _poll_token(page, min(self.timeout_ms, 35_000))

        if rate_hit["v"]:
            browser.close()
            raise RuntimeError("429_rate_limited")

        browser.close()
        return tok

    def _solve_normal(self) -> Optional[str]:
        """Strategy 2: normal checkbox + auto-click + Playwright frame click."""
        print("  [bypass] Strategy 2: normal checkbox click ...")
        html = _HTML_NORMAL.format(sitekey=self.sitekey)
        browser, page = self._launch()
        rate_hit = self._check_rate_limit(page)
        self._route_and_goto(page, html)

        try:
            page.wait_for_function("()=>typeof window.hcaptcha!=='undefined'", timeout=20_000)
        except PWTimeout:
            browser.close()
            return None

        page.wait_for_timeout(3_000)

        try:
            cb = page.frame_locator("iframe[src*='frame=checkbox']").locator("#checkbox")
            if cb.count() > 0:
                cb.click(timeout=5_000, force=True)
        except Exception:
            pass

        tok = _poll_token(page, min(self.timeout_ms, 50_000))

        if rate_hit["v"]:
            browser.close()
            raise RuntimeError("429_rate_limited")

        browser.close()
        return tok

    def solve(self) -> str:
        with sync_playwright() as pw:
            self._pw = pw

            # Strategy 1
            try:
                tok = self._solve_invisible()
                if tok:
                    print(f"  [bypass] Strategy 1 succeeded ({len(tok)} chars): {tok[:40]}...")
                    return tok
                print("  [bypass] Strategy 1: no token, trying strategy 2 ...")
            except RuntimeError as e:
                if "429" in str(e):
                    raise
                print(f"  [bypass] Strategy 1 error: {e}")

            # Strategy 2
            tok = self._solve_normal()
            if tok:
                print(f"  [bypass] Strategy 2 succeeded ({len(tok)} chars): {tok[:40]}...")
                return tok

        raise RuntimeError(
            "No token produced. The site may require a visual challenge or "
            "the IP is temporarily rate-limited by hCaptcha (usually clears "
            "in 1-2 hours of inactivity)."
        )


# ---------------------------------------------------------------------------
# Public wrapper with retries and exponential back-off
# ---------------------------------------------------------------------------

# Rate-limit backoff schedule (seconds per attempt).
# hCaptcha IP rate limits for this sitekey last ~1-2 hours when heavily tested.
# Normal production usage (a few songs per hour) does NOT trigger rate limits.
_RATE_LIMIT_BACKOFFS = [120, 300, 600, 1200]   # 2 min, 5 min, 10 min, 20 min


def get_hcaptcha_token_bypass(sitekey: str, host: str, max_retries: int = 4) -> str:
    """
    Solve hCaptcha using the multi-strategy headless browser engine.

    Retries up to *max_retries* times with exponential back-off.
    Rate-limit (429) uses longer back-off than other failures.

    Raises RuntimeError on total failure.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        print(f"  [bypass] Attempt {attempt}/{max_retries} ...")
        try:
            solver = HCaptchaSolver(sitekey=sitekey, host=host)
            return solver.solve()

        except RuntimeError as exc:
            last_error = exc
            msg = str(exc)
            print(f"  [bypass] Attempt {attempt} failed: {msg}")

            if "429_rate_limited" in msg or "429" in msg or "rate limit" in msg.lower():
                delay = _RATE_LIMIT_BACKOFFS[min(attempt - 1, len(_RATE_LIMIT_BACKOFFS) - 1)]
                delay += random.randint(0, 30)
                if attempt < max_retries:
                    print(f"  [bypass] IP temporarily rate-limited by hCaptcha.")
                    print(f"           Backing off for {delay}s (attempt {attempt}/{max_retries}).")
                    print(f"           TIP: Set CAPTCHA_PROVIDER=capsolver (or 2captcha / anticaptcha)")
                    print(f"                + CAPTCHA_API_KEY in Replit Secrets to avoid rate limits.")
                    time.sleep(delay)
            else:
                if attempt < max_retries:
                    delay = random.uniform(5, 12) * attempt
                    print(f"  [bypass] Retrying in {delay:.0f}s ...")
                    time.sleep(delay)

        except Exception as exc:
            last_error = exc
            print(f"  [bypass] Unexpected error: {exc}")
            if attempt < max_retries:
                time.sleep(random.uniform(5, 10) * attempt)

    raise RuntimeError(
        f"hCaptcha bypass failed after {max_retries} attempts "
        f"(last: {last_error}). "
        "Set CAPTCHA_PROVIDER + CAPTCHA_API_KEY in Replit Secrets to use a "
        "paid solver (capsolver / 2captcha / anticaptcha) which avoids "
        "per-IP rate limits."
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
