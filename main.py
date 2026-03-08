"""
main.py — CLI entry point for musichero.ai Music Generation Bot
----------------------------------------------------------------
For the web interface, run app.py instead.

Usage:
  python main.py

Environment variables (Replit Secrets):
  CAPTCHA_PROVIDER  - "manual" (default) | "bypass" | "2captcha" | "capsolver" | "anticaptcha"
  CAPTCHA_API_KEY   - Required only for paid providers
  HCAPTCHA_PROXY    - Optional proxy URL for the bypass engine (socks5://host:port)
"""

import os
import time
import random

from hcaptcha_bypass import get_hcaptcha_token_bypass
import bot_core

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HCAPTCHA_SITEKEY = "6520ce9c-a8b2-4cbe-b698-687e90448dec"
TARGET_SITE      = "https://musichero.ai"

CAPTCHA_MAX_RETRIES = 3
CAPTCHA_API_KEY     = os.getenv("CAPTCHA_API_KEY")
CAPTCHA_PROVIDER    = os.getenv("CAPTCHA_PROVIDER", "manual").lower()


# ---------------------------------------------------------------------------
# Captcha solving (CLI)
# ---------------------------------------------------------------------------

def _solve_with_2captcha(api_key: str) -> str:
    import requests
    submit = requests.post(
        "http://2captcha.com/in.php",
        data={"key": api_key, "method": "hcaptcha",
              "sitekey": HCAPTCHA_SITEKEY, "pageurl": TARGET_SITE, "json": 1},
        timeout=30,
    )
    submit.raise_for_status()
    data = submit.json()
    if data.get("status") != 1:
        raise RuntimeError(f"2captcha submit failed: {data}")
    task_id = data["request"]
    print(f"  [captcha] 2captcha task submitted — id={task_id}")
    for _ in range(24):
        time.sleep(5)
        result = requests.get(
            "http://2captcha.com/res.php",
            params={"key": api_key, "action": "get", "id": task_id, "json": 1},
            timeout=30,
        )
        result.raise_for_status()
        res = result.json()
        if res.get("status") == 1:
            return res["request"]
        if res.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
            raise RuntimeError("2captcha: captcha unsolvable")
    raise RuntimeError("2captcha: timed out waiting for solution")


def _solve_with_capsolver(api_key: str) -> str:
    import requests
    submit = requests.post(
        "https://api.capsolver.com/createTask",
        json={"clientKey": api_key, "task": {
            "type": "HCaptchaTaskProxyLess",
            "websiteURL": TARGET_SITE, "websiteKey": HCAPTCHA_SITEKEY,
        }},
        timeout=30,
    )
    submit.raise_for_status()
    data = submit.json()
    if data.get("errorId", 0) != 0:
        raise RuntimeError(f"CapSolver create task failed: {data}")
    task_id = data["taskId"]
    print(f"  [captcha] CapSolver task submitted — id={task_id}")
    for _ in range(24):
        time.sleep(5)
        result = requests.post(
            "https://api.capsolver.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id}, timeout=30,
        )
        result.raise_for_status()
        res = result.json()
        if res.get("status") == "ready":
            return res["solution"]["gRecaptchaResponse"]
        if res.get("errorId", 0) != 0:
            raise RuntimeError(f"CapSolver error: {res}")
    raise RuntimeError("CapSolver: timed out waiting for solution")


def _solve_with_anticaptcha(api_key: str) -> str:
    import requests
    submit = requests.post(
        "https://api.anti-captcha.com/createTask",
        json={"clientKey": api_key, "task": {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": TARGET_SITE, "websiteKey": HCAPTCHA_SITEKEY,
        }},
        timeout=30,
    )
    submit.raise_for_status()
    data = submit.json()
    if data.get("errorId", 0) != 0:
        raise RuntimeError(f"Anti-Captcha create task failed: {data}")
    task_id = data["taskId"]
    print(f"  [captcha] Anti-Captcha task submitted — id={task_id}")
    for _ in range(24):
        time.sleep(5)
        result = requests.post(
            "https://api.anti-captcha.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id}, timeout=30,
        )
        result.raise_for_status()
        res = result.json()
        if res.get("status") == "ready":
            return res["solution"]["gRecaptchaResponse"]
        if res.get("errorId", 0) != 0:
            raise RuntimeError(f"Anti-Captcha error: {res}")
    raise RuntimeError("Anti-Captcha: timed out waiting for solution")


def _solve_manually() -> str:
    """Ask the user to solve hCaptcha in their browser and paste the token."""
    print()
    print("=" * 62)
    print("  MANUAL CAPTCHA  —  Please solve the captcha in your browser")
    print("=" * 62)
    print()
    print("  Step 1  ->  Open this URL in your browser:")
    print("              https://musichero.ai")
    print()
    print("  Step 2  ->  Solve the hCaptcha on the page.")
    print()
    print("  Step 3  ->  Press F12 and open the Console tab.")
    print()
    print("  Step 4  ->  Paste this command and press Enter:")
    print()
    print('    document.querySelector(\'[name="h-captcha-response"]\').value')
    print()
    print("  Step 5  ->  Copy the long token (starts with P0_eyJ...)")
    print("              and paste it below.")
    print()
    print("=" * 62)

    while True:
        try:
            token = input("  Paste token here: ").strip()
        except EOFError:
            raise RuntimeError(
                "Manual captcha: no input received (stdin closed). "
                "Run the bot in an interactive terminal, or use the web UI (app.py)."
            )
        if not token:
            print("  [!] Token is empty — please try again.")
            continue
        if len(token) < 20:
            print(f"  [!] Token looks too short ({len(token)} chars) — copy the full value.")
            continue
        print(f"  [manual] Token received ({len(token)} chars)")
        print()
        return token


def get_captcha_token() -> str:
    """
    Resolve a captcha token using the configured provider.

    Set CAPTCHA_PROVIDER in Replit Secrets:
      "manual"      — you solve it in your browser (default)
      "bypass"      — free automated headless browser solver
      "2captcha"    — paid 2captcha.com service
      "capsolver"   — paid capsolver.com service
      "anticaptcha" — paid anti-captcha.com service
    """
    if CAPTCHA_PROVIDER == "manual":
        return _solve_manually()

    if CAPTCHA_PROVIDER == "bypass":
        token = get_hcaptcha_token_bypass(
            sitekey=HCAPTCHA_SITEKEY,
            host=TARGET_SITE,
            max_retries=CAPTCHA_MAX_RETRIES,
        )
        return token

    if not CAPTCHA_API_KEY:
        raise EnvironmentError(
            "CAPTCHA_API_KEY is not set. Add it in Replit Secrets, "
            "or set CAPTCHA_PROVIDER=manual to solve captchas yourself."
        )

    solver_map = {
        "2captcha":    _solve_with_2captcha,
        "capsolver":   _solve_with_capsolver,
        "anticaptcha": _solve_with_anticaptcha,
    }
    solver = solver_map.get(CAPTCHA_PROVIDER)
    if not solver:
        raise ValueError(
            f"Unknown CAPTCHA_PROVIDER '{CAPTCHA_PROVIDER}'. "
            f"Choose from: manual, bypass, {', '.join(solver_map.keys())}"
        )

    for attempt in range(1, CAPTCHA_MAX_RETRIES + 1):
        try:
            print(f"  [captcha] Solving via {CAPTCHA_PROVIDER} "
                  f"(attempt {attempt}/{CAPTCHA_MAX_RETRIES}) ...")
            token = solver(CAPTCHA_API_KEY)
            print("  [captcha] Token obtained.")
            return token
        except Exception as exc:
            print(f"  [captcha] Attempt {attempt} failed: {exc}")
            if attempt < CAPTCHA_MAX_RETRIES:
                time.sleep(3)

    raise RuntimeError(f"hCaptcha solving failed after {CAPTCHA_MAX_RETRIES} attempts.")


# ---------------------------------------------------------------------------
# Generation loop
# ---------------------------------------------------------------------------

def run_generation_loop(songs: list[dict]) -> None:
    print("=" * 60)
    print("  musichero.ai Music Generation Bot  (CLI)")
    print(f"  Provider : {CAPTCHA_PROVIDER}")
    print(f"  Songs    : {len(songs)}")
    print("=" * 60)

    results = []

    for idx, song in enumerate(songs, start=1):
        mode   = song.get("mode", "simple")
        prompt = song.get("prompt", "")

        print(f"\n{'─'*60}")
        print(f"  Song {idx}/{len(songs)}  |  mode={mode}")
        print(f"  Prompt: {prompt[:80]}")
        print(f"{'─'*60}")

        try:
            token = get_captcha_token()
        except RuntimeError as exc:
            if "rate_limited" in str(exc):
                print("\n[loop] Stopping — hCaptcha IP rate limit hit.")
                print("       Re-run in 1-2 hours, or add HCAPTCHA_PROXY / paid solver.")
                break
            print(f"[loop] Captcha error: {exc}")
            results.append({"index": idx, "status": "failed", "url": None})
            continue
        except EnvironmentError as exc:
            print(f"[loop] Config error: {exc}")
            break

        result = bot_core.run_job(
            captcha_token=token,
            mode=mode,
            prompt=prompt,
            title=song.get("title", "Untitled"),
            lyrics=song.get("lyrics", ""),
        )

        results.append({"index": idx, "status": result["status"], "url": result["audio_url"]})

        if result["status"] == "ok":
            print(f"\n  [OK] Song {idx} complete: {result['audio_url']}")
        else:
            print(f"\n  [SKIP] Song {idx} ended with status: {result['status']}")

        if idx < len(songs):
            delay = random.uniform(3, 7)
            print(f"  [delay] Waiting {delay:.1f}s before next song ...")
            time.sleep(delay)

    print("\n" + "=" * 60)
    print("  Generation Summary")
    print("=" * 60)
    for r in results:
        tag = "OK  " if r["status"] == "ok" else "FAIL"
        url = r["url"] or "—"
        print(f"  [{tag}] Song {r['index']:>3}: {url}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    song_queue = [
        {
            "mode":   "simple",
            "prompt": "An upbeat electronic pop song about chasing dreams at night",
        },
        {
            "mode":   "custom",
            "prompt": "Dark cinematic orchestral with haunting piano",
            "title":  "Shadows of Tomorrow",
            "lyrics": (
                "[Verse 1]\n"
                "In the silence of the night\n"
                "I hear shadows calling out my name\n\n"
                "[Chorus]\n"
                "Shadows of tomorrow\n"
                "Guide me through the dark\n"
            ),
        },
    ]

    run_generation_loop(song_queue)
