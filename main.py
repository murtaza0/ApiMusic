"""
musichero.ai Music Generation Bot
----------------------------------
Supports Simple and Custom generation modes with manual or automated
hCaptcha solving, automatic polling, error handling, and unlimited
generation loops.

Environment variables (set in Replit Secrets):
  CAPTCHA_PROVIDER  - "manual" (default — you solve in browser) OR
                      "bypass"      (free automated headless solver) OR
                      "2captcha" | "capsolver" | "anticaptcha" (paid)
  CAPTCHA_API_KEY   - Only needed when using a paid provider above
"""

import os
import time
import uuid
import random
import requests

from hcaptcha_bypass import get_hcaptcha_token_bypass

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CREATE_URL       = "https://api.musichero.ai/api/v1/suno/create"
POLL_URL         = "https://api.musichero.ai/api/v1/suno/pageRecordList?pageNum=1"
HCAPTCHA_SITEKEY = "6520ce9c-a8b2-4cbe-b698-687e90448dec"
TARGET_SITE      = "https://musichero.ai"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS  = 180   # 3 minutes before skipping
CAPTCHA_MAX_RETRIES   = 3
REQUEST_MAX_RETRIES   = 3

CAPTCHA_API_KEY  = os.getenv("CAPTCHA_API_KEY")
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "manual").lower()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def random_delay(low: float = 2.0, high: float = 5.0) -> None:
    """Sleep for a random duration between low and high seconds."""
    delay = random.uniform(low, high)
    print(f"  [delay] Waiting {delay:.1f}s ...")
    time.sleep(delay)


def fresh_unique_id() -> str:
    """Return a fresh hex UUID for use in the uniqueId header."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# hCaptcha solving
# ---------------------------------------------------------------------------

def _solve_with_2captcha(api_key: str) -> str:
    """Submit hCaptcha task to 2captcha and return the solved token."""
    # Step 1 — Submit task
    submit = requests.post(
        "http://2captcha.com/in.php",
        data={
            "key":    api_key,
            "method": "hcaptcha",
            "sitekey": HCAPTCHA_SITEKEY,
            "pageurl": TARGET_SITE,
            "json":    1,
        },
        timeout=30,
    )
    submit.raise_for_status()
    data = submit.json()
    if data.get("status") != 1:
        raise RuntimeError(f"2captcha submit failed: {data}")

    task_id = data["request"]
    print(f"  [captcha] 2captcha task submitted — id={task_id}")

    # Step 2 — Poll for result (up to ~2 minutes)
    for _ in range(24):
        time.sleep(5)
        result = requests.get(
            "http://2captcha.com/res.php",
            params={"key": api_key, "action": "get", "id": task_id, "json": 1},
            timeout=30,
        )
        result.raise_for_status()
        res_data = result.json()
        if res_data.get("status") == 1:
            return res_data["request"]
        if res_data.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
            raise RuntimeError("2captcha: captcha unsolvable")
        # CAPCHA_NOT_READY — keep waiting

    raise RuntimeError("2captcha: timed out waiting for solution")


def _solve_with_capsolver(api_key: str) -> str:
    """Submit hCaptcha task to CapSolver and return the solved token."""
    # Step 1 — Create task
    submit = requests.post(
        "https://api.capsolver.com/createTask",
        json={
            "clientKey": api_key,
            "task": {
                "type":       "HCaptchaTaskProxyLess",
                "websiteURL": TARGET_SITE,
                "websiteKey": HCAPTCHA_SITEKEY,
            },
        },
        timeout=30,
    )
    submit.raise_for_status()
    data = submit.json()
    if data.get("errorId", 0) != 0:
        raise RuntimeError(f"CapSolver create task failed: {data}")

    task_id = data["taskId"]
    print(f"  [captcha] CapSolver task submitted — id={task_id}")

    # Step 2 — Poll for result
    for _ in range(24):
        time.sleep(5)
        result = requests.post(
            "https://api.capsolver.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id},
            timeout=30,
        )
        result.raise_for_status()
        res_data = result.json()
        if res_data.get("status") == "ready":
            return res_data["solution"]["gRecaptchaResponse"]
        if res_data.get("errorId", 0) != 0:
            raise RuntimeError(f"CapSolver error: {res_data}")

    raise RuntimeError("CapSolver: timed out waiting for solution")


def _solve_with_anticaptcha(api_key: str) -> str:
    """Submit hCaptcha task to Anti-Captcha and return the solved token."""
    # Step 1 — Create task
    submit = requests.post(
        "https://api.anti-captcha.com/createTask",
        json={
            "clientKey": api_key,
            "task": {
                "type":       "HCaptchaTaskProxyless",
                "websiteURL": TARGET_SITE,
                "websiteKey": HCAPTCHA_SITEKEY,
            },
        },
        timeout=30,
    )
    submit.raise_for_status()
    data = submit.json()
    if data.get("errorId", 0) != 0:
        raise RuntimeError(f"Anti-Captcha create task failed: {data}")

    task_id = data["taskId"]
    print(f"  [captcha] Anti-Captcha task submitted — id={task_id}")

    # Step 2 — Poll for result
    for _ in range(24):
        time.sleep(5)
        result = requests.post(
            "https://api.anti-captcha.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id},
            timeout=30,
        )
        result.raise_for_status()
        res_data = result.json()
        if res_data.get("status") == "ready":
            return res_data["solution"]["gRecaptchaResponse"]
        if res_data.get("errorId", 0) != 0:
            raise RuntimeError(f"Anti-Captcha error: {res_data}")

    raise RuntimeError("Anti-Captcha: timed out waiting for solution")


def _solve_manually() -> str:
    """
    Ask the user to solve hCaptcha in their own browser and paste the token
    back into the terminal.

    Steps shown to the user:
      1. Open https://musichero.ai in a browser
      2. Solve the hCaptcha checkbox
      3. Open DevTools Console (F12)
      4. Run the one-liner to copy the token
      5. Paste it here
    """
    print()
    print("━" * 62)
    print("  MANUAL CAPTCHA  —  آپ کو خود captcha solve کرنا ہے")
    print("━" * 62)
    print()
    print("  Step 1  →  اپنے browser میں یہ link کھولیں:")
    print("             https://musichero.ai")
    print()
    print("  Step 2  →  صفحے پر hCaptcha checkbox نظر آئے گا,")
    print("             اسے solve کریں (images select کریں)۔")
    print()
    print("  Step 3  →  Solve ہونے کے فوری بعد F12 دبائیں")
    print("             اور Console tab کھولیں۔")
    print()
    print("  Step 4  →  Console میں یہ command paste کریں اور Enter دبائیں:")
    print()
    print("    document.querySelector('[name=\"h-captcha-response\"]').value")
    print()
    print("  Step 5  →  جو لمبا text آئے (P0_eyJ... سے شروع ہوگا)")
    print("             اسے copy کریں اور نیچے paste کریں۔")
    print()
    print("━" * 62)

    while True:
        try:
            token = input("  Token paste کریں یہاں: ").strip()
        except EOFError:
            raise RuntimeError(
                "Manual captcha: no input received (stdin closed). "
                "Run the bot in an interactive terminal."
            )

        if not token:
            print("  [!] Token خالی ہے — دوبارہ try کریں۔")
            continue

        # Basic sanity check — hCaptcha tokens start with "P0_" and are long
        if len(token) < 20:
            print(f"  [!] Token بہت چھوٹا لگتا ہے ({len(token)} chars) — پوری value copy کریں۔")
            continue

        print(f"  [manual] Token مل گیا ({len(token)} chars) ✓")
        print()
        return token


def get_hcaptcha_token() -> str:
    """
    Solve the hCaptcha challenge.

    Provider routing (set CAPTCHA_PROVIDER in Replit Secrets):
      "manual"     — you solve it in your browser (default)
      "bypass"     — free automated headless browser solver
      "2captcha"   — paid 2captcha.com service
      "capsolver"  — paid capsolver.com service
      "anticaptcha"— paid anti-captcha.com service
    """
    # --- Manual: user solves in their own browser ---
    if CAPTCHA_PROVIDER == "manual":
        return _solve_manually()

    # --- Free self-contained automated bypass ---
    if CAPTCHA_PROVIDER == "bypass":
        return get_hcaptcha_token_bypass(
            sitekey=HCAPTCHA_SITEKEY,
            host=TARGET_SITE,
            max_retries=CAPTCHA_MAX_RETRIES,
        )

    # --- Paid service providers ---
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
            print(f"  [captcha] Solving via {CAPTCHA_PROVIDER} (attempt {attempt}/{CAPTCHA_MAX_RETRIES}) ...")
            token = solver(CAPTCHA_API_KEY)
            print("  [captcha] Token obtained.")
            return token
        except Exception as exc:
            print(f"  [captcha] Attempt {attempt} failed: {exc}")
            if attempt < CAPTCHA_MAX_RETRIES:
                time.sleep(3)

    raise RuntimeError(
        f"hCaptcha solving failed after {CAPTCHA_MAX_RETRIES} attempts."
    )


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------

def build_headers(unique_id: str, captcha_token: str) -> dict:
    """Build the required request headers for the musichero.ai API."""
    return {
        "User-Agent":   USER_AGENT,
        "uniqueId":     unique_id,
        "verify":       captcha_token,
        "Content-Type": "application/json",
        "Accept":       "application/json, text/plain, */*",
        "Origin":       "https://musichero.ai",
        "Referer":      "https://musichero.ai/",
    }


def create_song(session: requests.Session, mode: str, **kwargs) -> bool:
    """
    POST to /create to initiate song generation.

    Modes:
      simple — kwargs: prompt (str)
      custom — kwargs: prompt (style), lyrics (str), title (str)

    Returns True on success, False on unrecoverable failure.
    """
    for attempt in range(1, REQUEST_MAX_RETRIES + 1):
        unique_id = fresh_unique_id()
        print(f"\n[create] Attempt {attempt}/{REQUEST_MAX_RETRIES} — uniqueId={unique_id}")

        # Solve a fresh captcha token for every POST request
        try:
            token = get_hcaptcha_token()
        except RuntimeError as exc:
            msg = str(exc)
            if "hCaptcha_rate_limited" in msg or "rate_limited" in msg:
                # Raise so the caller (run_generation_loop) can stop the whole run
                raise
            print(f"[create] Captcha solve error: {exc}")
            return False
        except EnvironmentError as exc:
            print(f"[create] Captcha config error: {exc}")
            return False

        headers = build_headers(unique_id, token)

        if mode == "simple":
            payload = {
                "prompt":     kwargs["prompt"],
                "customMode": False,
            }
        else:  # custom
            payload = {
                "prompt":     kwargs["prompt"],        # style descriptor
                "mv":         kwargs.get("lyrics", ""),
                "title":      kwargs.get("title", "Untitled"),
                "customMode": True,
            }

        random_delay()

        try:
            resp = session.post(CREATE_URL, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            print(f"[create] Network error: {exc}")
            time.sleep(5)
            continue

        if resp.status_code == 200:
            print("[create] Song creation queued successfully.")
            return True

        if resp.status_code == 429:
            wait = 30 * attempt
            print(f"[create] 429 Too Many Requests — waiting {wait}s before retry ...")
            time.sleep(wait)
            continue

        if resp.status_code == 403:
            # Captcha token most likely expired; loop will fetch a fresh one
            print("[create] 403 Forbidden — captcha token may have expired. Retrying ...")
            continue

        print(f"[create] Unexpected status {resp.status_code}: {resp.text[:200]}")
        time.sleep(5)

    print("[create] All attempts exhausted.")
    return False


def poll_for_result(session: requests.Session) -> str | None:
    """
    Poll pageRecordList every POLL_INTERVAL_SECONDS until the latest song
    finishes or POLL_TIMEOUT_SECONDS is exceeded.

    Returns the audioUrl string on success, or None on timeout/error.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "uniqueId":   fresh_unique_id(),
        "Accept":     "application/json",
        "Referer":    "https://musichero.ai/",
    }

    deadline = time.time() + POLL_TIMEOUT_SECONDS
    attempt  = 0

    print(f"\n[poll] Polling for result (timeout={POLL_TIMEOUT_SECONDS}s) ...")

    while time.time() < deadline:
        attempt += 1
        try:
            resp = session.get(POLL_URL, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[poll] Error on attempt {attempt}: {exc}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        records = data.get("data", {}).get("records", [])
        if not records:
            print(f"[poll] Attempt {attempt}: No records yet ...")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        latest = records[0]
        status = latest.get("status", "unknown")
        print(f"[poll] Attempt {attempt}: status = {status}")

        if status == "finished":
            # Try both common key names
            audio_url = latest.get("audioUrl") or latest.get("audio_url")
            if audio_url:
                print(f"\n[poll] Song ready!")
                print(f"       Audio URL: {audio_url}")
                return audio_url
            print("[poll] Status is 'finished' but no audioUrl found in response.")
            print(f"       Full record: {latest}")
            return None

        if status in ("error", "failed"):
            print(f"[poll] Generation failed with status: {status}")
            return None

        time.sleep(POLL_INTERVAL_SECONDS)

    print("[poll] Timed out waiting for song to finish. Skipping ...")
    return None


# ---------------------------------------------------------------------------
# Main generation loop
# ---------------------------------------------------------------------------

def run_generation_loop(songs: list[dict]) -> None:
    """
    Process a list of song configs one by one using a shared session
    (JSESSIONID cookies persist automatically via requests.Session).

    Each song dict must contain:
      mode   (str) : "simple" or "custom"
      prompt (str) : style/mood description for both modes
      lyrics (str) : lyrics text   (custom mode only)
      title  (str) : song title    (custom mode only)
    """
    session = requests.Session()

    print("=" * 60)
    print("  musichero.ai Music Generation Bot")
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

        song_kwargs = {k: v for k, v in song.items() if k not in ("mode",)}
        try:
            success = create_song(session, mode, **song_kwargs)
        except RuntimeError as exc:
            if "rate_limited" in str(exc):
                print(f"\n[loop] Stopping run — hCaptcha IP rate limit hit.")
                print(f"       Re-run the workflow in 1-2 hours, or add a")
                print(f"       HCAPTCHA_PROXY or paid CAPTCHA_PROVIDER secret.")
                break
            raise
        if not success:
            print(f"[loop] Skipping song {idx} — creation request failed.")
            results.append({"index": idx, "status": "failed", "url": None})
            continue

        audio_url = poll_for_result(session)
        if audio_url:
            print(f"\n  [OK] Song {idx} complete")
            print(f"       {audio_url}")
            results.append({"index": idx, "status": "ok", "url": audio_url})
        else:
            print(f"\n  [SKIP] Song {idx} did not complete.")
            results.append({"index": idx, "status": "timeout", "url": None})

        # Pause between songs to reduce rate-limit risk
        if idx < len(songs):
            random_delay(3, 7)

    # Final summary
    print("\n" + "=" * 60)
    print("  Generation Summary")
    print("=" * 60)
    for r in results:
        tag = "OK  " if r["status"] == "ok" else "FAIL"
        url = r["url"] or "—"
        print(f"  [{tag}] Song {r['index']:>3}: {url}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point — edit the song_queue list to define your generation batch
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Add as many entries as you like for unlimited generation.
    song_queue = [
        # --- Simple mode: just a prompt ---
        {
            "mode":   "simple",
            "prompt": "An upbeat electronic pop song about chasing dreams at night",
        },
        # --- Custom mode: style + lyrics + title ---
        {
            "mode":   "custom",
            "prompt": "Dark cinematic orchestral with haunting piano",   # style
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
