"""
bot_core.py — Shared generation logic for musichero.ai
-------------------------------------------------------
Used by both the web app (app.py) and the CLI (main.py).
Accepts a pre-resolved captcha token so captcha solving
is handled by the caller (web UI or CLI).
"""
from __future__ import annotations

import os
import time
import uuid
import random
import requests
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CREATE_URL       = "https://api.musichero.ai/api/v1/suno/create"
POLL_URL         = "https://api.musichero.ai/api/v1/suno/pageRecordList?pageNum=1"

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS  = 300   # 5 minutes

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh_unique_id() -> str:
    return uuid.uuid4().hex


def build_headers(unique_id: str, captcha_token: str) -> dict:
    return {
        "User-Agent":   USER_AGENT,
        "uniqueId":     unique_id,
        "verify":       captcha_token,
        "Content-Type": "application/json",
        "Accept":       "application/json, text/plain, */*",
        "Origin":       "https://musichero.ai",
        "Referer":      "https://musichero.ai/",
    }


# ---------------------------------------------------------------------------
# Core API calls
# ---------------------------------------------------------------------------

def create_song(
    session: requests.Session,
    captcha_token: str,
    mode: str,
    prompt: str,
    title: str = "Untitled",
    lyrics: str = "",
    log: Callable[[str], None] = print,
) -> bool:
    """
    POST to /create to queue song generation.
    Returns True on success, False on failure.
    """
    unique_id = fresh_unique_id()
    log(f"[create] Submitting — mode={mode} uniqueId={unique_id}")

    headers = build_headers(unique_id, captcha_token)

    if mode == "simple":
        payload = {"prompt": prompt, "customMode": False}
    else:
        payload = {
            "prompt":     prompt,
            "mv":         lyrics,
            "title":      title,
            "customMode": True,
        }

    try:
        resp = session.post(CREATE_URL, json=payload, headers=headers, timeout=30)
    except requests.RequestException as exc:
        log(f"[create] Network error: {exc}")
        return False

    if resp.status_code == 200:
        log("[create] Song creation queued successfully.")
        return True

    if resp.status_code == 403:
        log(f"[create] 403 Forbidden — captcha token rejected or expired.")
        return False

    if resp.status_code == 429:
        log("[create] 429 Too Many Requests — API rate limited.")
        return False

    log(f"[create] Unexpected status {resp.status_code}: {resp.text[:200]}")
    return False


def poll_for_result(
    session: requests.Session,
    log: Callable[[str], None] = print,
    timeout: int = POLL_TIMEOUT_SECONDS,
) -> Optional[str]:
    """
    Poll pageRecordList until the latest song is finished or timeout.
    Returns the audio URL string, or None on timeout/error.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "uniqueId":   fresh_unique_id(),
        "Accept":     "application/json",
        "Referer":    "https://musichero.ai/",
    }

    deadline = time.time() + timeout
    attempt  = 0

    log(f"[poll] Waiting for song to finish (up to {timeout}s) ...")

    while time.time() < deadline:
        attempt += 1
        try:
            resp = session.get(POLL_URL, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log(f"[poll] Attempt {attempt}: error — {exc}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        records = data.get("data", {}).get("records", [])
        if not records:
            log(f"[poll] Attempt {attempt}: no records yet ...")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        latest = records[0]
        status = latest.get("status", "unknown")
        log(f"[poll] Attempt {attempt}: status = {status}")

        if status == "finished":
            audio_url = latest.get("audioUrl") or latest.get("audio_url")
            if audio_url:
                log(f"[poll] Song ready! Audio URL: {audio_url}")
                return audio_url
            log("[poll] Status is 'finished' but no audioUrl found.")
            return None

        if status in ("error", "failed"):
            log(f"[poll] Generation failed with status: {status}")
            return None

        time.sleep(POLL_INTERVAL_SECONDS)

    log("[poll] Timed out waiting for song to finish.")
    return None


# ---------------------------------------------------------------------------
# High-level job runner (used by web app)
# ---------------------------------------------------------------------------

def run_job(
    captcha_token: str,
    mode: str,
    prompt: str,
    title: str = "Untitled",
    lyrics: str = "",
    log: Callable[[str], None] = print,
) -> dict:
    """
    Run a full generation job: create + poll.
    Returns {"status": "ok"|"failed"|"timeout", "audio_url": str|None}.
    """
    session = requests.Session()

    success = create_song(
        session=session,
        captcha_token=captcha_token,
        mode=mode,
        prompt=prompt,
        title=title,
        lyrics=lyrics,
        log=log,
    )

    if not success:
        return {"status": "failed", "audio_url": None}

    audio_url = poll_for_result(session=session, log=log)

    if audio_url:
        return {"status": "ok", "audio_url": audio_url}
    return {"status": "timeout", "audio_url": None}
