"""
anymusic.ai  —  Production-Ready FastAPI + Vercel Serverless
POST /generate        → fires 2 parallel requests, returns task_ids immediately
GET  /status/{id}     → single-check poll; streams audio/mpeg when ready
GET  /health          → liveness probe
"""

import asyncio
import base64
import os
import random
import string
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

try:
    from curl_cffi.requests import AsyncSession as CurlSession
    _USE_CURL = True
except ImportError:
    import httpx
    _USE_CURL = False

# ─────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────
app = FastAPI(title="anymusic.ai API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
#  anymusic.ai endpoints
# ─────────────────────────────────────────────
GENERATE_URL = "https://anymusic.ai/api/music/generate"
TASK_URL     = "https://anymusic.ai/api/music/task/{task_id}"

# ─────────────────────────────────────────────
#  Browser spoofing — headers + cookies
# ─────────────────────────────────────────────
_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": '"Windows"',
        "impersonate": "chrome131",
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": '"macOS"',
        "impersonate": "chrome131",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
        "impersonate": "chrome124",
    },
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": '"Linux"',
        "impersonate": "chrome131",
    },
]

_ACCEPT_LANGS = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.8",
    "en-CA,en;q=0.9,fr-CA;q=0.8",
]


def _rand(n: int, chars: str = string.ascii_lowercase + string.digits) -> str:
    return "".join(random.choices(chars, k=n))


def _fresh_cookie() -> str:
    n1   = random.randint(100_000_000, 999_999_999)
    ts   = int(time.time()) - random.randint(0, 7 * 86400)
    ga   = f"GA1.1.{n1}.{ts}"

    ts2  = int(time.time()) - random.randint(0, 3600)
    off  = random.randint(1, 120)
    ga4  = (
        f"GS2.1.s{ts2}$o{random.randint(1, 5)}"
        f"$g0$t{ts2 + off}$j60$l0$h0"
    )

    clck = (
        f"{_rand(6)}%5E2%5Eg{random.randint(10, 9999)}"
        f"%5E0%5E{random.randint(10, 9999)}"
    )

    clsk_ts = int(time.time()) - random.randint(0, 1800)
    clsk = (
        f"{_rand(8)}%5E{clsk_ts}"
        f"%5E{random.randint(1, 5)}%5E1"
        "%5El.clarity.ms%2Fcollect"
    )

    return f"_ga={ga}; _ga_9R01R8BKDB={ga4}; _clck={clck}; _clsk={clsk}"


def _fresh_headers(profile: Optional[dict] = None) -> dict:
    p = profile or random.choice(_PROFILES)
    return {
        "Origin":             "https://anymusic.ai",
        "Referer":            "https://anymusic.ai/",
        "Content-Type":       "application/json",
        "Accept":             "*/*",
        "Accept-Language":    random.choice(_ACCEPT_LANGS),
        "User-Agent":         p["ua"],
        "sec-ch-ua":          p["sec_ch_ua"],
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": p["platform"],
        "Sec-Fetch-Dest":     "empty",
        "Sec-Fetch-Mode":     "cors",
        "Sec-Fetch-Site":     "same-origin",
        "Cookie":             _fresh_cookie(),
    }


# ─────────────────────────────────────────────
#  Response parsing helpers
# ─────────────────────────────────────────────
_AUDIO_KEYS = (
    "audio_url", "audioUrl", "audio", "mp3_url",
    "mp3Url", "file_url", "fileUrl", "url",
)
_TASK_KEYS  = ("taskId", "task_id", "id", "jobId", "job_id", "musicId")
_DATA_KEYS  = ("data", "result", "songs", "music", "items", "task")


def _extract_audio_url(obj) -> Optional[str]:
    if isinstance(obj, list):
        obj = obj[0] if obj else None
    if not isinstance(obj, dict):
        return None
    for k in _AUDIO_KEYS:
        v = obj.get(k)
        if v and isinstance(v, str) and v.startswith("http"):
            return v
    for k in _DATA_KEYS:
        v = obj.get(k)
        if v:
            r = _extract_audio_url(v)
            if r:
                return r
    return None


def _extract_task_id(obj) -> Optional[str]:
    if isinstance(obj, list):
        obj = obj[0] if obj else None
    if not isinstance(obj, dict):
        return None
    for scope in (obj, obj.get("data") or {}):
        if not isinstance(scope, dict):
            continue
        for k in _TASK_KEYS:
            v = scope.get(k)
            if v:
                return str(v)
    data = obj.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            for k in _TASK_KEYS:
                v = first.get(k)
                if v:
                    return str(v)
    return None


def _status_from_record(obj) -> str:
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict):
        return "pending"
    for scope in (obj, obj.get("data") or {}, obj.get("task") or {}):
        if not isinstance(scope, dict):
            continue
        s = (scope.get("status") or scope.get("state") or "").lower()
        if s in ("error", "failed", "failure", "cancelled", "canceled"):
            return "failed"
        if s in ("success", "completed", "done", "finished"):
            return "success"
    return "pending"


# ─────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────
class GenerateRequest(BaseModel):
    prompt:   str
    genre:    str = "Pop"
    mode:     str = "text-to-song"   # "text-to-song" | "lyrics-to-song"
    title:    str = "AI Track"
    lyrics:   str = ""
    style:    str = "Pop"
    mood:     str = "Happy"
    scenario: str = "Summer vibes"


# ─────────────────────────────────────────────
#  Core: single generation call
# ─────────────────────────────────────────────
async def _generate_one(req: GenerateRequest, profile: dict) -> dict:
    if req.mode == "lyrics-to-song":
        payload = {
            "type":       "lyrics-to-song",
            "lyrics":     req.lyrics or req.prompt,
            "title":      req.title,
            "styles":     {
                "style":    [req.style],
                "mood":     [req.mood],
                "scenario": [req.scenario],
            },
            "quantity":   1,
            "is_private": False,
        }
    else:
        payload = {
            "type":       "text-to-song",
            "prompt":     req.prompt,
            "genre":      req.genre,
            "quantity":   1,
            "is_private": False,
        }

    headers = _fresh_headers(profile)

    if _USE_CURL:
        async with CurlSession() as session:
            resp = await session.post(
                GENERATE_URL,
                json=payload,
                headers=headers,
                impersonate=profile.get("impersonate", "chrome131"),
                timeout=90,
            )
        status_code = resp.status_code
        if status_code in (401, 403):
            raise Exception(f"Auth / IP blocked ({status_code}): {resp.text[:200]}")
        if status_code == 429:
            raise Exception("Rate limited by anymusic.ai")
        if status_code not in (200, 201):
            raise Exception(f"HTTP {status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception:
            raise Exception(f"Non-JSON response (HTTP {status_code}): {resp.text[:200]}")
    else:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(GENERATE_URL, json=payload, headers=headers)
        status_code = resp.status_code
        if status_code in (401, 403):
            raise Exception(f"Auth / IP blocked ({status_code}): {resp.text[:200]}")
        if status_code == 429:
            raise Exception("Rate limited by anymusic.ai")
        if status_code not in (200, 201):
            raise Exception(f"HTTP {status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception:
            raise Exception(f"Non-JSON response (HTTP {status_code}): {resp.text[:200]}")

    # Case 1: audio URL returned directly (synchronous generation)
    audio_url = _extract_audio_url(data)
    if audio_url:
        encoded  = base64.urlsafe_b64encode(audio_url.encode()).decode().rstrip("=")
        task_id  = f"ready_{encoded}"
        return {"task_id": task_id, "status": "ready"}

    # Case 2: task ID returned for async polling
    task_id = _extract_task_id(data)
    if task_id:
        return {"task_id": task_id, "status": "pending"}

    raise Exception(f"Unrecognised response shape: {str(data)[:300]}")


# ─────────────────────────────────────────────
#  Audio streaming helper
# ─────────────────────────────────────────────
async def _stream_audio_url(audio_url: str) -> StreamingResponse:
    ua = random.choice(_PROFILES)["ua"]
    if _USE_CURL:
        async with CurlSession() as session:
            resp = await session.get(
                audio_url,
                headers={"User-Agent": ua},
                impersonate="chrome131",
                timeout=30,
            )
        if resp.status_code not in (200, 206):
            raise HTTPException(
                status_code=502,
                detail=f"CDN returned HTTP {resp.status_code} for audio URL",
            )
        content = resp.content
    else:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(audio_url, headers={"User-Agent": ua})
        resp.raise_for_status()
        content = resp.content

    if not content:
        raise HTTPException(status_code=502, detail="CDN returned empty audio content")

    return StreamingResponse(
        iter([content]),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'attachment; filename="track.mp3"',
            "Content-Length":      str(len(content)),
            "Accept-Ranges":       "bytes",
        },
    )


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────
@app.get("/")
async def root():
    return JSONResponse({
        "service": "anymusic.ai API",
        "version": "1.0.0",
        "docs":    "/docs",
        "endpoints": {
            "health":   "GET  /health",
            "generate": "POST /generate",
            "status":   "GET  /status/{task_id}",
        },
    })


@app.get("/health")
async def health():
    return {
        "ok":      True,
        "service": "anymusic-api",
        "version": "1.0.0",
        "backend": "curl_cffi" if _USE_CURL else "httpx",
    }


@app.post("/generate")
async def generate(req: GenerateRequest):
    """
    Fire 2 parallel generation requests (Variant 1 & 2) to anymusic.ai.
    Returns task_ids immediately — poll GET /status/{task_id} for each.
    """
    profiles = random.sample(_PROFILES, 2)

    results = await asyncio.gather(
        _generate_one(req, profiles[0]),
        _generate_one(req, profiles[1]),
        return_exceptions=True,
    )

    variants = []
    for r in results:
        if isinstance(r, Exception):
            variants.append({"task_id": "", "status": "failed", "error": str(r)})
        else:
            variants.append(r)

    if all(v["status"] == "failed" for v in variants):
        errors = "; ".join(v.get("error", "") for v in variants)
        raise HTTPException(status_code=500, detail=f"Both variants failed: {errors}")

    return {"ok": True, "variants": variants}


@app.get("/status/{task_id:path}")
async def status(task_id: str):
    """
    Single-check poll endpoint.

    - "ready_<b64>" task_id  → decode URL, fetch audio, stream as audio/mpeg
    - real task_id           → call anymusic.ai task endpoint once, return status
                               or stream audio if complete
    Client should retry every 8 s until status != "pending".
    """
    # ── ready_ prefix: audio URL is embedded in the task_id ──────────────
    if task_id.startswith("ready_"):
        encoded   = task_id[6:]
        padding   = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        try:
            audio_url = base64.urlsafe_b64decode(encoded.encode()).decode()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid task_id encoding: {exc}")
        return await _stream_audio_url(audio_url)

    # ── real task_id: single poll to anymusic.ai ─────────────────────────
    poll_url = TASK_URL.format(task_id=task_id)
    headers  = _fresh_headers()

    try:
        if _USE_CURL:
            async with CurlSession() as session:
                resp = await session.get(
                    poll_url,
                    headers=headers,
                    impersonate="chrome131",
                    timeout=15,
                )
            poll_status = resp.status_code
            if poll_status == 404:
                return JSONResponse({"status": "pending", "task_id": task_id})
            try:
                data = resp.json()
            except Exception:
                return JSONResponse({"status": "pending", "task_id": task_id})
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(poll_url, headers=headers)
            poll_status = resp.status_code
            if poll_status == 404:
                return JSONResponse({"status": "pending", "task_id": task_id})
            try:
                data = resp.json()
            except Exception:
                return JSONResponse({"status": "pending", "task_id": task_id})

        # Audio URL present → stream it
        audio_url = _extract_audio_url(data)
        if audio_url:
            return await _stream_audio_url(audio_url)

        # Check status field
        st = _status_from_record(data)
        if st == "failed":
            return JSONResponse({"status": "failed", "task_id": task_id})
        if st == "success":
            # Task says success but no audio URL found — treat as failed so client doesn't loop forever
            return JSONResponse({
                "status": "failed",
                "task_id": task_id,
                "error": "Task completed but no audio URL in response",
            })

        return JSONResponse({"status": "pending", "task_id": task_id})

    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(
            {"status": "pending", "task_id": task_id, "error": str(exc)}
        )


# ─────────────────────────────────────────────
#  Dev entry point (Replit only)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=True)
