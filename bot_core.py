"""
bot_core.py — anymusic.ai generation logic
-------------------------------------------
Calls the anymusic.ai API — no captcha required.
Cookies from the user's browser session are passed with each request.
"""
from __future__ import annotations

import time
import uuid
import requests
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GENERATE_URL = "https://anymusic.ai/api/music/generate"
LIST_URL     = "https://anymusic.ai/api/music/list"

POLL_INTERVAL = 8
POLL_TIMEOUT  = 360   # 6 minutes

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

GENRES = [
    "Pop", "R&B", "Rock", "Hip-Hop", "Jazz", "Classical",
    "Electronic", "Country", "Soul", "Lo-fi", "Ambient",
]

STYLES = ["Classical", "Pop", "Rock", "Jazz", "Electronic", "R&B", "Hip-Hop", "Folk", "Indie", "Soul"]
MOODS  = ["Romantic", "Happy", "Sad", "Energetic", "Calm", "Mysterious", "Epic", "Melancholic", "Hopeful"]
SCENARIOS = [
    "Urban romance", "Late night drive", "Summer vibes", "Heartbreak",
    "Celebration", "Nostalgia", "Road trip", "Rainy day", "First love",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_headers(cookie: str) -> dict:
    h = {
        "User-Agent":   USER_AGENT,
        "Content-Type": "application/json",
        "Accept":       "*/*",
        "Origin":       "https://anymusic.ai",
        "Referer":      "https://anymusic.ai/",
        "sec-ch-ua":    '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


def _extract_audio(record: dict) -> Optional[str]:
    for key in ("audioUrl", "audio_url", "audio", "url", "file_url", "fileUrl"):
        val = record.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    return None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def create_song(
    session: requests.Session,
    cookie: str,
    mode: str,
    prompt: str,
    genre: str = "Pop",
    title: str = "Untitled",
    lyrics: str = "",
    style: str = "Pop",
    mood: str = "Happy",
    scenario: str = "Urban romance",
    log: Callable[[str], None] = print,
) -> Optional[dict]:
    """
    POST to /api/music/generate.
    Returns the parsed JSON response body, or None on failure.
    """
    headers = build_headers(cookie)

    if mode == "simple":
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
            "lyrics":     lyrics,
            "title":      title,
            "styles": {
                "style":    [style],
                "mood":     [mood],
                "scenario": [scenario],
            },
            "quantity":   1,
            "is_private": False,
        }

    log(f"[generate] Submitting — mode={mode}")
    try:
        resp = session.post(GENERATE_URL, json=payload, headers=headers, timeout=120)
    except requests.Timeout:
        log("[generate] Request timed out — the server took too long to respond.")
        return None
    except requests.RequestException as exc:
        log(f"[generate] Network error: {exc}")
        return None

    log(f"[generate] Response status: {resp.status_code}")

    if resp.status_code == 401:
        log("[generate] 401 Unauthorized — your session cookie may be expired or missing.")
        return None
    if resp.status_code == 403:
        log("[generate] 403 Forbidden — access denied.")
        return None
    if resp.status_code == 429:
        log("[generate] 429 Too Many Requests — rate limited.")
        return None
    if resp.status_code != 200:
        log(f"[generate] Unexpected status {resp.status_code}: {resp.text[:300]}")
        return None

    try:
        data = resp.json()
        log(f"[generate] Response: {str(data)[:300]}")
        return data
    except Exception:
        log(f"[generate] Non-JSON response: {resp.text[:300]}")
        return None


def poll_for_result(
    session: requests.Session,
    cookie: str,
    task_id: Optional[str] = None,
    log: Callable[[str], None] = print,
) -> Optional[str]:
    """
    Poll the list endpoint until a finished song appears.
    Returns the audio URL string, or None on timeout/error.
    """
    headers = build_headers(cookie)
    headers["Content-Type"] = "application/json"

    deadline = time.time() + POLL_TIMEOUT
    attempt  = 0

    log(f"[poll] Waiting for song to finish (up to {POLL_TIMEOUT}s) ...")

    while time.time() < deadline:
        attempt += 1
        time.sleep(POLL_INTERVAL)

        try:
            resp = session.get(LIST_URL, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log(f"[poll] Attempt {attempt}: error — {exc}")
            continue

        log(f"[poll] Attempt {attempt}: raw response: {str(data)[:400]}")

        records = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            for key in ("data", "items", "records", "list", "songs", "results"):
                val = data.get(key)
                if isinstance(val, list):
                    records = val
                    break
                if isinstance(val, dict):
                    for subkey in ("records", "items", "list", "data"):
                        sub = val.get(subkey)
                        if isinstance(sub, list):
                            records = sub
                            break
                    if records:
                        break

        if not records:
            log(f"[poll] Attempt {attempt}: no records yet — raw: {str(data)[:200]}")
            continue

        for rec in records:
            status = (rec.get("status") or rec.get("state") or "").lower()
            rec_id = rec.get("id") or rec.get("taskId") or rec.get("task_id")

            if task_id and rec_id and str(rec_id) != str(task_id):
                continue

            log(f"[poll] Attempt {attempt}: id={rec_id} status={status}")

            if status in ("finished", "complete", "completed", "done", "success", "succeeded"):
                audio_url = _extract_audio(rec)
                if audio_url:
                    log(f"[poll] Song ready! Audio URL: {audio_url}")
                    return audio_url
                log("[poll] Status finished but no audio URL found in record.")
                return None

            if status in ("error", "failed", "failure"):
                log(f"[poll] Generation failed: {rec}")
                return None

        log(f"[poll] Attempt {attempt}: still processing ...")

    log("[poll] Timed out waiting for song to finish.")
    return None


# ---------------------------------------------------------------------------
# High-level job runner
# ---------------------------------------------------------------------------

def run_job(
    cookie: str,
    mode: str,
    prompt: str,
    genre: str = "Pop",
    title: str = "Untitled",
    lyrics: str = "",
    style: str = "Pop",
    mood: str = "Happy",
    scenario: str = "Urban romance",
    log: Callable[[str], None] = print,
) -> dict:
    """
    Full generation job: generate + poll.
    Returns {"status": "ok"|"failed"|"timeout"|"auth_error", "audio_url": str|None}.
    """
    session = requests.Session()

    gen_data = create_song(
        session=session,
        cookie=cookie,
        mode=mode,
        prompt=prompt,
        genre=genre,
        title=title,
        lyrics=lyrics,
        style=style,
        mood=mood,
        scenario=scenario,
        log=log,
    )

    if gen_data is None:
        return {"status": "failed", "audio_url": None}

    task_id = None
    audio_url = None

    if isinstance(gen_data, dict):
        data_body = gen_data.get("data") or gen_data
        if isinstance(data_body, list) and data_body:
            audio_url = _extract_audio(data_body[0])
            task_id = data_body[0].get("id") or data_body[0].get("taskId")
        elif isinstance(data_body, dict):
            audio_url = _extract_audio(data_body)
            task_id = data_body.get("id") or data_body.get("taskId") or data_body.get("task_id")

    if audio_url:
        log(f"[run_job] Got audio URL directly from generate response: {audio_url}")
        return {"status": "ok", "audio_url": audio_url}

    log(f"[run_job] No immediate audio URL — starting polling (task_id={task_id})")
    audio_url = poll_for_result(session=session, cookie=cookie, task_id=task_id, log=log)

    if audio_url:
        return {"status": "ok", "audio_url": audio_url}
    return {"status": "timeout", "audio_url": None}
