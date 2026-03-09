"""
bot_core.py — anymusic.ai generation logic
-------------------------------------------
Features:
  - Full per-request browser spoofing: User-Agent, TLS fingerprint, cookies, platform
  - Task ID capture from POST response
  - Status polling at /api/music/task/{taskId}
  - Auto-download MP3 to music_outputs/
  - No captcha, no manual setup required
"""
from __future__ import annotations

import os
import re
import time
import random
import string
from typing import Callable, Optional

try:
    from curl_cffi import requests as cf_requests
    from curl_cffi.requests.impersonate import BrowserType
    _CURL_AVAILABLE = True
    _IMPERSONATE_TARGETS = [t.name for t in BrowserType if t.name.startswith("chrome")]
except ImportError:
    import requests as cf_requests
    _CURL_AVAILABLE = False
    _IMPERSONATE_TARGETS = []

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GENERATE_URL  = "https://anymusic.ai/api/music/generate"
TASK_URL      = "https://anymusic.ai/api/music/task/{task_id}"
TASK_URL_ALT  = "https://anymusic.ai/api/music/tasks/{task_id}"
LIST_URL      = "https://anymusic.ai/api/music/list"

POLL_INTERVAL = 10
POLL_TIMEOUT  = 420   # 7 minutes

OUTPUT_DIR = "music_outputs"

# ---------------------------------------------------------------------------
# Per-request browser spoofing profiles
# ---------------------------------------------------------------------------

# Pool of realistic Chrome browser profiles (UA + platform + sec-ch-ua)
_BROWSER_PROFILES = [
    {
        "version": "120",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "platform": '"Windows"',
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    },
    {
        "version": "124",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "platform": '"Windows"',
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    },
    {
        "version": "131",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "platform": '"Windows"',
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "version": "131",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "platform": '"macOS"',
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "version": "133",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "platform": '"Windows"',
        "sec_ch_ua": '"Chromium";v="133", "Google Chrome";v="133", "Not-A.Brand";v="24"',
    },
    {
        "version": "136",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "platform": '"Windows"',
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="24"',
    },
    {
        "version": "120",
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "platform": '"Linux"',
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    },
    {
        "version": "124",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "platform": '"macOS"',
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    },
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.8",
    "en-CA,en;q=0.9,fr-CA;q=0.8",
    "en-AU,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.7",
]


def _random_profile() -> dict:
    return random.choice(_BROWSER_PROFILES)


def _random_impersonate() -> str:
    if not _IMPERSONATE_TARGETS:
        return ""
    return random.choice(_IMPERSONATE_TARGETS)

GENRES = [
    "Pop", "R&B", "Rock", "Hip-Hop", "Jazz", "Classical",
    "Electronic", "Country", "Soul", "Lo-fi", "Ambient",
]
STYLES    = ["Classical", "Pop", "Rock", "Jazz", "Electronic", "R&B", "Hip-Hop", "Folk", "Indie", "Soul"]
MOODS     = ["Romantic", "Happy", "Sad", "Energetic", "Calm", "Mysterious", "Epic", "Melancholic", "Hopeful"]
SCENARIOS = [
    "Urban romance", "Late night drive", "Summer vibes", "Heartbreak",
    "Celebration", "Nostalgia", "Road trip", "Rainy day", "First love",
]


# ---------------------------------------------------------------------------
# Cookie + Header spoofing — fully randomized on every request
# ---------------------------------------------------------------------------

def _rand_str(length: int, chars: str = string.ascii_lowercase + string.digits) -> str:
    return "".join(random.choices(chars, k=length))


def _random_ga() -> str:
    """Google Analytics client ID — new random user + random first-visit timestamp."""
    n1 = random.randint(100_000_000, 999_999_999)
    n2 = random.randint(100_000_000, 999_999_999)
    ts = int(time.time()) - random.randint(0, 7 * 86400)
    return f"GA1.1.{n1}.{ts}"


def _random_ga4() -> str:
    """GA4 session cookie with randomized session start time."""
    ts  = int(time.time()) - random.randint(0, 3600)
    off = random.randint(1, 120)
    return f"GS2.1.s{ts}$o{random.randint(1,5)}$g0$t{ts + off}$j60$l0$h0"


def _random_clck() -> str:
    """Microsoft Clarity click fingerprint."""
    part = _rand_str(6)
    n1   = random.randint(10, 9999)
    n2   = random.randint(10, 9999)
    return f"{part}%5E2%5Eg{n1}%5E0%5E{n2}"


def _random_clsk() -> str:
    """Microsoft Clarity session cookie."""
    ts   = int(time.time()) - random.randint(0, 1800)
    part = _rand_str(8)
    page = random.randint(1, 5)
    return f"{part}%5E{ts}%5E{page}%5E1%5El.clarity.ms%2Fcollect"


def auto_generate_cookies() -> str:
    """
    Fresh set of realistic tracking cookies on every call.
    Mimics Google Analytics + Microsoft Clarity cookies from a real browser visit.
    All values are randomized: client ID, session timestamps, click counters.
    """
    return (
        f"_ga={_random_ga()}; "
        f"_ga_9R01R8BKDB={_random_ga4()}; "
        f"_clck={_random_clck()}; "
        f"_clsk={_random_clsk()}"
    )


def _get_cookie(base_cookie: str) -> str:
    """
    Always return a fresh set of cookies.
    - base_cookie provided → merge it with fresh tracking cookies
    - no base_cookie → generate all 4 tracking cookies fresh
    """
    if not base_cookie:
        return auto_generate_cookies()
    # User provided their own cookie string — keep non-tracking parts, refresh tracking
    parts = [p.strip() for p in base_cookie.split(";") if p.strip()]
    kept = [p for p in parts
            if not (p.startswith("_ga") or p.startswith("_clck") or p.startswith("_clsk"))]
    fresh = [
        f"_ga={_random_ga()}",
        f"_ga_9R01R8BKDB={_random_ga4()}",
        f"_clck={_random_clck()}",
        f"_clsk={_random_clsk()}",
    ]
    return "; ".join(kept + fresh)


def _build_headers(cookie: str = "") -> dict:
    """
    Build a fully spoofed, randomized header set for each request.
    Picks a random browser profile (UA + platform + sec-ch-ua) and
    a random Accept-Language on every call.
    """
    profile = _random_profile()
    lang    = random.choice(_ACCEPT_LANGUAGES)
    h = {
        "Origin":             "https://anymusic.ai",
        "Referer":            "https://anymusic.ai/",
        "Content-Type":       "application/json",
        "User-Agent":         profile["ua"],
        "Accept":             "*/*",
        "Accept-Language":    lang,
        "sec-ch-ua":          profile["sec_ch_ua"],
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": profile["platform"],
        "Sec-Fetch-Dest":     "empty",
        "Sec-Fetch-Mode":     "cors",
        "Sec-Fetch-Site":     "same-origin",
        "Priority":           "u=1, i",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


def _get_impersonate(profile: dict) -> str:
    """Pick a curl_cffi impersonation target matching the browser profile version."""
    if not _CURL_AVAILABLE:
        return ""
    ver = profile.get("version", "131")
    # Find the closest available target
    for target in _IMPERSONATE_TARGETS:
        if ver in target:
            return target
    return random.choice(_IMPERSONATE_TARGETS) if _IMPERSONATE_TARGETS else ""




# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

def ensure_output_dir() -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


# ---------------------------------------------------------------------------
# Download MP3
# ---------------------------------------------------------------------------

def download_audio(
    audio_url: str,
    filename: str = "",
    log: Callable[[str], None] = print,
) -> Optional[str]:
    """
    Download an audio file from audio_url and save to music_outputs/.
    Returns the local file path, or None on failure.
    """
    ensure_output_dir()

    if not filename:
        slug = re.sub(r"[^a-z0-9]+", "_", audio_url.split("/")[-1].lower())
        filename = slug if slug.endswith(".mp3") else slug + ".mp3"

    out_path = os.path.join(OUTPUT_DIR, filename)

    try:
        log(f"[download] Downloading: {audio_url}")
        dl_profile = _random_profile()
        dl_headers = _build_headers()
        dl_imp     = _get_impersonate(dl_profile)
        dl_imp_opts = {"impersonate": dl_imp} if (_CURL_AVAILABLE and dl_imp) else {}
        resp = cf_requests.get(audio_url, headers=dl_headers, timeout=60, stream=True, **dl_imp_opts)
        resp.raise_for_status()

        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

        size_kb = os.path.getsize(out_path) // 1024
        log(f"[download] Saved to {out_path} ({size_kb} KB)")
        return out_path
    except Exception as exc:
        log(f"[download] Failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Core: start_generation (POST)
# ---------------------------------------------------------------------------

def start_generation(
    prompt: str,
    mode: str = "lyrics-to-song",
    cookie: str = "",
    title: str = "AI_Track",
    genre: str = "Classical",
    style: str = "Classical",
    mood: str = "Romantic",
    scenario: str = "Urban romance",
    log: Callable[[str], None] = print,
) -> Optional[str]:
    """
    POST to /api/music/generate.
    Returns the task_id string, or None on failure.
    """
    profile  = _random_profile()
    fresh_cookie = _get_cookie(cookie)
    headers  = _build_headers(fresh_cookie)
    impersonate = _get_impersonate(profile)

    log(f"[generate] POST {GENERATE_URL}  mode={mode}")
    log(f"[spoof]    UA={profile['ua'][:60]}...")
    log(f"[spoof]    Platform={profile['platform']}  Fingerprint={impersonate or 'native'}")

    if mode == "text-to-song":
        payload = {
            "type":       "text-to-song",
            "prompt":     prompt,
            "genre":      genre,
            "quantity":   1,
            "is_private": False,
        }
    else:
        payload = {
            "type":       "lyrics-to-song",
            "lyrics":     prompt,
            "title":      title,
            "styles": {
                "style":    [style],
                "mood":     [mood],
                "scenario": [scenario],
            },
            "quantity":   1,
            "is_private": False,
        }

    try:
        impersonate_opts = {"impersonate": impersonate} if (_CURL_AVAILABLE and impersonate) else {}
        resp = cf_requests.post(
            GENERATE_URL, json=payload, headers=headers, timeout=60,
            **impersonate_opts
        )
    except Exception as exc:
        if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
            log("[generate] Request timed out.")
        else:
            log(f"[generate] Network error: {exc}")
        return None

    log(f"[generate] Status: {resp.status_code}")

    if resp.status_code == 401:
        log("[generate] 401 Unauthorized — session cookie may be expired or missing.")
        return None
    if resp.status_code == 403:
        log("[generate] 403 Forbidden — access denied.")
        return None
    if resp.status_code == 429:
        log("[generate] 429 Too Many Requests — rate limited, retrying after 30s ...")
        time.sleep(30)
        return None
    if resp.status_code != 200:
        log(f"[generate] Unexpected status {resp.status_code}: {resp.text[:300]}")
        return None

    try:
        data = resp.json()
        log(f"[generate] Response: {str(data)[:400]}")
    except Exception:
        log(f"[generate] Non-JSON response: {resp.text[:300]}")
        return None

    task_id = _extract_task_id(data)
    if task_id:
        log(f"[generate] Task ID: {task_id}")
    else:
        log("[generate] No task_id found in response — will poll list endpoint instead.")

    return task_id or "__list__"


def _extract_task_id(data: dict | list) -> Optional[str]:
    """Try every common key path to find a task/job ID."""
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return None

    for d in (data, data.get("data") or {}, (data.get("data") or {}) if isinstance(data.get("data"), dict) else {}):
        if not isinstance(d, dict):
            continue
        for key in ("taskId", "task_id", "id", "jobId", "job_id", "musicId", "music_id", "requestId"):
            val = d.get(key)
            if val and isinstance(val, (str, int)):
                return str(val)

    if isinstance(data.get("data"), list) and data["data"]:
        first = data["data"][0]
        if isinstance(first, dict):
            for key in ("taskId", "task_id", "id", "jobId"):
                val = first.get(key)
                if val:
                    return str(val)

    return None


def _extract_audio(record: dict) -> Optional[str]:
    for key in ("audioUrl", "audio_url", "audio", "url", "file_url", "fileUrl", "mp3Url", "mp3_url"):
        val = record.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    return None


# ---------------------------------------------------------------------------
# Core: poll_task_status (GET)
# ---------------------------------------------------------------------------

def poll_task_status(
    task_id: str,
    cookie: str = "",
    log: Callable[[str], None] = print,
) -> Optional[str]:
    """
    Poll the task status endpoint every POLL_INTERVAL seconds.
    Returns audio_url when done, or None on timeout/error.
    """
    deadline = time.time() + POLL_TIMEOUT
    attempt  = 0

    use_list = task_id == "__list__"
    urls_to_try = (
        [LIST_URL]
        if use_list
        else [
            TASK_URL.format(task_id=task_id),
            TASK_URL_ALT.format(task_id=task_id),
            LIST_URL,
        ]
    )

    log(f"[poll] Watching task_id={task_id} (timeout={POLL_TIMEOUT}s, interval={POLL_INTERVAL}s)")

    working_url = None

    while time.time() < deadline:
        attempt += 1
        time.sleep(POLL_INTERVAL)

        for url in (urls_to_try if working_url is None else [working_url]):
            try:
                poll_profile = _random_profile()
                poll_headers = _build_headers(_get_cookie(cookie))
                poll_imp     = _get_impersonate(poll_profile)
                imp_opts     = {"impersonate": poll_imp} if (_CURL_AVAILABLE and poll_imp) else {}
                resp = cf_requests.get(url, headers=poll_headers, timeout=30, **imp_opts)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                working_url = url
            except Exception as exc:
                log(f"[poll] Attempt {attempt}: {url} → error: {exc}")
                continue

            log(f"[poll] Attempt {attempt}: {url} → {str(data)[:300]}")

            audio_url = _parse_poll_response(data, task_id if not use_list else None)
            if audio_url is True:
                continue
            if audio_url is None:
                return None
            if isinstance(audio_url, str):
                return audio_url
            break

        else:
            log(f"[poll] Attempt {attempt}: all URLs returned 404 / error")
            continue

    log("[poll] Timed out.")
    return None


def _parse_poll_response(data, task_id: Optional[str] = None):
    """
    Returns:
      str       — audio URL (done)
      None      — terminal failure
      True      — still processing, keep polling
    """
    records = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("data", "result", "task", "song", "music"):
            val = data.get(key)
            if isinstance(val, list):
                records = val
                break
            if isinstance(val, dict):
                records = [val]
                break
        if not records:
            records = [data]

    for rec in records:
        if not isinstance(rec, dict):
            continue

        rec_id = str(rec.get("taskId") or rec.get("task_id") or rec.get("id") or "")
        if task_id and rec_id and rec_id != task_id:
            continue

        status = (
            rec.get("status") or rec.get("state") or rec.get("taskStatus") or ""
        ).lower()

        if status in ("success", "finished", "complete", "completed", "done", "succeeded"):
            url = _extract_audio(rec)
            if url:
                return url
            return True

        if status in ("error", "failed", "failure", "cancelled", "canceled"):
            return None

    return True


# ---------------------------------------------------------------------------
# High-level: run_job (used by web app & batch runner)
# ---------------------------------------------------------------------------

def run_job(
    cookie: str,
    mode: str = "lyrics-to-song",
    prompt: str = "",
    genre: str = "Classical",
    title: str = "AI_Track",
    lyrics: str = "",
    style: str = "Classical",
    mood: str = "Romantic",
    scenario: str = "Urban romance",
    auto_download: bool = True,
    log: Callable[[str], None] = print,
) -> dict:
    """
    Full pipeline: generate → poll → (optional) download.
    Returns:
      {"status": "ok"|"failed"|"timeout", "audio_url": str|None, "local_path": str|None}
    """
    actual_prompt = lyrics if (mode == "lyrics-to-song" and lyrics) else prompt

    task_id = start_generation(
        prompt=actual_prompt,
        mode=mode,
        cookie=cookie,
        title=title,
        genre=genre,
        style=style,
        mood=mood,
        scenario=scenario,
        log=log,
    )

    if task_id is None:
        return {"status": "failed", "audio_url": None, "local_path": None}

    audio_url = poll_task_status(task_id=task_id, cookie=cookie, log=log)

    if not audio_url:
        return {"status": "timeout", "audio_url": None, "local_path": None}

    local_path = None
    if auto_download:
        safe_title = re.sub(r"[^a-z0-9]+", "_", title.lower())[:40]
        filename = f"{safe_title}_{int(time.time())}.mp3"
        local_path = download_audio(audio_url, filename=filename, log=log)

    return {"status": "ok", "audio_url": audio_url, "local_path": local_path}
