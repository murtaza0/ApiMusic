"""
aimusic.so  —  Production-Ready FastAPI (Railway / Replit)

ENDPOINTS:
  POST /generate-lyrics   → AI se lyrics generate karo (unlimited, no auth needed)
  GET  /lyrics/{uuid}     → lyrics status poll karo
  POST /generate-song     → Suno song generate (requires verify token from browser)
  GET  /health            → liveness probe
  GET  /docs              → Swagger UI
"""

import asyncio
import hashlib
import os
import random
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# ─────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────
app = FastAPI(
    title="aimusic.so API Wrapper",
    version="2.0.0",
    description="Lyrics generation (unlimited) + Suno song generation via aimusic.so",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
#  aimusic.so endpoints
# ─────────────────────────────────────────────
BASE_URL          = "https://api.aimusic.so"
LYRICAL_CREATE    = f"{BASE_URL}/api/v1/lyrical/create"
LYRICAL_STATUS    = f"{BASE_URL}/api/v1/lyrical/getLyricsByUuid/{{uuid}}"
SUNO_CREATE       = f"{BASE_URL}/api/v1/suno/create"

# ─────────────────────────────────────────────
#  Browser profiles for header spoofing
# ─────────────────────────────────────────────
_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "platform": "Windows",
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not:A-Brand";v="99", "Google Chrome";v="144", "Chromium";v="144"',
        "platform": "macOS",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
        "sec_ch_ua": '"Microsoft Edge";v="143", "Chromium";v="143", "Not:A-Brand";v="99"',
        "platform": "Windows",
    },
]

_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8,ur;q=0.6",
    "en-US,en;q=0.9,pk;q=0.7",
]


def _fresh_unique_id() -> str:
    """Generate a fresh random MD5 uniqueId — each is a fresh guest account with 5 credits."""
    return hashlib.md5(os.urandom(16)).hexdigest()


def _fresh_headers(include_verify: Optional[str] = None) -> dict:
    """Build browser-spoofed headers for aimusic.so requests."""
    profile = random.choice(_PROFILES)
    headers = {
        "Accept":             "application/json, text/plain, */*",
        "Accept-Language":    random.choice(_LANGUAGES),
        "Connection":         "keep-alive",
        "Content-Type":       "application/json",
        "Host":               "api.aimusic.so",
        "Origin":             "https://aimusic.so",
        "Referer":            "https://aimusic.so/",
        "sec-ch-ua":          profile["sec_ch_ua"],
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": f'"{profile["platform"]}"',
        "Sec-Fetch-Dest":     "empty",
        "Sec-Fetch-Mode":     "cors",
        "Sec-Fetch-Site":     "same-site",
        "uniqueId":           _fresh_unique_id(),
        "User-Agent":         profile["ua"],
    }
    if include_verify:
        headers["verify"] = include_verify
    return headers


# ─────────────────────────────────────────────
#  Request / Response models
# ─────────────────────────────────────────────
class LyricsRequest(BaseModel):
    prompt:   str
    language: str = "urdu"


class SongRequest(BaseModel):
    prompt:       str
    style:        str = "Pop, Emotional"
    title:        str = "AI Song"
    custom_mode:  bool = False
    instrumental: bool = False
    model:        str = "Prime"
    verify_token: str = ""


class LyricsWithSongRequest(BaseModel):
    prompt:       str
    style:        str = "Pop, Emotional"
    title:        str = "AI Song"
    instrumental: bool = False
    verify_token: str


# ─────────────────────────────────────────────
#  Routes — Health / Info
# ─────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service":  "aimusic.so API Wrapper",
        "version":  "2.0.0",
        "backend":  "aimusic.so (Suno-powered)",
        "endpoints": {
            "POST /generate-lyrics":    "AI lyrics generate karo (unlimited)",
            "GET  /lyrics/{uuid}":      "Lyrics status poll karo",
            "POST /generate-song":      "Suno song generate (verify_token required from browser)",
            "POST /generate-full":      "Lyrics + Song (verify_token required from browser)",
            "GET  /health":             "Liveness probe",
        },
        "how_to_get_verify_token": (
            "Open aimusic.so in browser → inspect network tab → copy 'verify' header "
            "from any request → pass as verify_token in body (valid ~2 minutes)"
        ),
    }


@app.get("/health")
async def health():
    return {"ok": True, "service": "aimusic-api", "version": "2.0.0"}


# ─────────────────────────────────────────────
#  ROUTE 1: Lyrics Generation (UNLIMITED — no verify needed)
# ─────────────────────────────────────────────
@app.post("/generate-lyrics")
async def generate_lyrics(req: LyricsRequest):
    """
    Generate AI lyrics from a prompt — completely unlimited, no Turnstile, no credits.

    - language: 'urdu', 'english', 'hindi', 'punjabi' etc (just include in prompt)
    - Returns uuid for polling via GET /lyrics/{uuid}

    Example prompt: 'sad urdu ghazal about lost love'
    """
    full_prompt = req.prompt
    if req.language.lower() != "english":
        lang_map = {
            "urdu":    "in Urdu language",
            "hindi":   "in Hindi language",
            "punjabi": "in Punjabi language",
            "arabic":  "in Arabic language",
        }
        lang_hint = lang_map.get(req.language.lower(), f"in {req.language} language")
        full_prompt = f"{req.prompt} ({lang_hint})"

    headers = _fresh_headers()
    payload = {"prompt": full_prompt}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(LYRICAL_CREATE, json=payload, headers=headers)
    except Exception as e:
        raise HTTPException(503, f"Cannot reach aimusic.so: {e}")

    data = resp.json()
    if data.get("code") != 200:
        raise HTTPException(502, f"aimusic.so error: {data.get('msg', 'unknown')}")

    uuid = data["data"]["uuid"]
    return {
        "ok":        True,
        "uuid":      uuid,
        "status":    "generating",
        "poll_url":  f"/lyrics/{uuid}",
        "note":      "Poll every 3s — takes ~10-30 seconds for lyrics to be ready",
    }


# ─────────────────────────────────────────────
#  ROUTE 2: Lyrics Status Poll
# ─────────────────────────────────────────────
@app.get("/lyrics/{uuid}")
async def get_lyrics(uuid: str):
    """
    Poll lyrics generation status.

    Response status codes:
      status=1 → complete (lyrics ready in completeData)
      status=3 → still generating (retry after 3s)
      status=2 → failed
    """
    headers = _fresh_headers()
    url = LYRICAL_STATUS.format(uuid=uuid)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:
        raise HTTPException(503, f"Cannot reach aimusic.so: {e}")

    data = resp.json()
    if data.get("code") != 200:
        raise HTTPException(502, f"aimusic.so error: {data.get('msg')}")

    info    = data["data"]
    status  = info.get("status")   # 1=complete, 3=processing
    results = info.get("completeData")

    if status == 1 and results:
        lyrics_list = [
            {
                "title": item.get("title", ""),
                "text":  item.get("text", ""),
            }
            for item in results
            if item.get("status") == "complete"
        ]
        return {
            "ok":     True,
            "status": "complete",
            "lyrics": lyrics_list,
        }
    elif status == 3 or results is None:
        return {
            "ok":     True,
            "status": "generating",
            "note":   "Retry after 3 seconds",
        }
    else:
        return {
            "ok":     False,
            "status": "failed",
            "raw":    info,
        }


# ─────────────────────────────────────────────
#  ROUTE 3: Song Generation (requires verify_token from browser)
# ─────────────────────────────────────────────
@app.post("/generate-song")
async def generate_song(req: SongRequest):
    """
    Generate a Suno AI song via aimusic.so.

    REQUIRES verify_token (Cloudflare Turnstile token from browser):
    - Open aimusic.so in Chrome → F12 → Network tab → filter 'suno'
    - Click 'Create' on the site → copy 'verify' header value from request
    - Paste it as verify_token here (valid ~2 minutes)

    Each fresh uniqueId (auto-rotated per request) = 5 free credits.
    With a valid verify_token, every request uses a fresh uniqueId = unlimited!

    Returns task_id — poll GET /song/{task_id} for audio.
    """
    if not req.verify_token:
        raise HTTPException(
            400,
            detail={
                "error": "verify_token required",
                "how_to_get": (
                    "1. Open https://aimusic.so in Chrome browser\n"
                    "2. Press F12 → Network tab\n"
                    "3. Click 'Create' to generate any song\n"
                    "4. Find 'suno/create' request → Headers → copy 'verify' value\n"
                    "5. Paste here as verify_token (valid ~2 minutes)"
                ),
            },
        )

    headers = _fresh_headers(include_verify=req.verify_token)
    payload = {
        "prompt":       req.prompt,
        "style":        req.style,
        "title":        req.title,
        "customMode":   req.custom_mode,
        "instrumental": req.instrumental,
        "model":        req.model,
        "privateFlag":  False,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(SUNO_CREATE, json=payload, headers=headers)
    except Exception as e:
        raise HTTPException(503, f"Cannot reach aimusic.so: {e}")

    data = resp.json()
    code = data.get("code")

    if code == 200:
        return {"ok": True, "data": data.get("data"), "note": "Song generation started"}

    if code == 100001:
        raise HTTPException(
            403,
            detail={
                "error":       "Cloudflare Turnstile token invalid or expired",
                "code":        100001,
                "fix":         "Get a fresh verify_token from aimusic.so (valid only ~2 minutes)",
            },
        )

    if code == 430:
        raise HTTPException(
            429,
            detail={
                "error": "This verify_token is linked to an exhausted account",
                "code":  430,
                "fix":   "Get a fresh verify_token — it will auto-rotate to a new uniqueId",
            },
        )

    if code == 400:
        raise HTTPException(
            400,
            detail={
                "error": "Turnstile verification failed",
                "code":  400,
                "fix":   "verify_token expired. Get a fresh one from aimusic.so",
            },
        )

    raise HTTPException(502, detail={"error": data.get("msg"), "code": code, "raw": data})


# ─────────────────────────────────────────────
#  ROUTE 4: Full Flow — Lyrics then Song
# ─────────────────────────────────────────────
@app.post("/generate-full")
async def generate_full(req: LyricsWithSongRequest):
    """
    Full pipeline: Generate lyrics THEN submit as Suno song.

    1. Generates Urdu/multilingual lyrics via /api/v1/lyrical/create (unlimited)
    2. Waits for lyrics to complete (~15-30s)
    3. Submits lyrics to suno/create with your verify_token
    4. Returns song data

    Requires verify_token from browser (see /generate-song docs).
    """
    if not req.verify_token:
        raise HTTPException(400, "verify_token required — see /generate-song for instructions")

    # Step 1: Generate lyrics
    lyric_headers = _fresh_headers()
    lyric_payload = {"prompt": req.prompt}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            lyric_resp = await client.post(LYRICAL_CREATE, json=lyric_payload, headers=lyric_headers)
    except Exception as e:
        raise HTTPException(503, f"Lyrics generation failed: {e}")

    lyric_data = lyric_resp.json()
    if lyric_data.get("code") != 200:
        raise HTTPException(502, f"Lyric API error: {lyric_data.get('msg')}")

    uuid = lyric_data["data"]["uuid"]

    # Step 2: Poll until lyrics ready
    lyrics_text = None
    lyrics_title = req.title
    for _ in range(20):
        await asyncio.sleep(3)
        poll_headers = _fresh_headers()
        poll_url = LYRICAL_STATUS.format(uuid=uuid)
        async with httpx.AsyncClient(timeout=15.0) as client:
            poll_resp = await client.get(poll_url, headers=poll_headers)
        poll_data = poll_resp.json()
        info = poll_data.get("data", {})
        if info.get("status") == 1 and info.get("completeData"):
            first = info["completeData"][0]
            lyrics_text  = first.get("text", "")
            lyrics_title = first.get("title", req.title)
            break

    if not lyrics_text:
        raise HTTPException(504, "Lyrics generation timed out after 60s")

    # Step 3: Submit to Suno
    song_headers = _fresh_headers(include_verify=req.verify_token)
    song_payload = {
        "prompt":       lyrics_text,
        "style":        req.style,
        "title":        lyrics_title,
        "customMode":   True,
        "instrumental": req.instrumental,
        "model":        "Prime",
        "privateFlag":  False,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        song_resp = await client.post(SUNO_CREATE, json=song_payload, headers=song_headers)

    song_data = song_resp.json()
    code = song_data.get("code")

    if code == 200:
        return {
            "ok":           True,
            "lyrics_uuid":  uuid,
            "lyrics_title": lyrics_title,
            "lyrics_text":  lyrics_text[:300] + "..." if len(lyrics_text) > 300 else lyrics_text,
            "song_data":    song_data.get("data"),
        }

    raise HTTPException(
        502,
        detail={
            "error":       song_data.get("msg"),
            "code":        code,
            "lyrics_uuid": uuid,
            "note":        "Lyrics were generated. Song submission failed — check verify_token",
        },
    )


# ─────────────────────────────────────────────
#  Entry point — Replit / Railway
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port   = int(os.getenv("PORT", 5000))
    reload = os.getenv("RAILWAY_ENVIRONMENT") is None
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)
