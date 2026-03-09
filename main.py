"""
AiMusic API v4 — Production Grade (Railway / Replit)
=====================================================
Architecture:
  - Lyrics generation  → aimusic.so /lyrical/create (unlimited, no auth)
  - Song generation    → 2captcha Turnstile solver → suno/create directly
                         (no browser needed, pure HTTP, scales to 100K+)
  - Task queue         → asyncio queue, N async workers
  - Unique identity    → fresh uniqueId (MD5) per request = fresh guest account

Browser fingerprinting is applied at HTTP header level for all requests.
Selenium/undetected-chromedriver runs ONLY as fallback if 2captcha key missing.

ENDPOINTS
  GET  /                     → status / queue info
  POST /generate-lyrics      → lyrics only  (no browser, unlimited, fast)
  GET  /lyrics/{uuid}        → poll lyrics
  POST /generate-song        → queue song (full auto: 2captcha + suno API)
  POST /generate-full        → lyrics + song together
  GET  /task/{id}            → poll song task
  GET  /tasks                → list all tasks
  DEL  /task/{id}            → delete task
  GET  /health               → liveness probe
"""

import asyncio
import hashlib
import logging
import os
import random
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
#  Optional: undetected-chromedriver (fallback when no 2captcha key)
# ---------------------------------------------------------------------------
try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    UC_AVAILABLE = True
except ImportError:
    UC_AVAILABLE = False

try:
    from fake_useragent import UserAgent as _UA
    _ua_gen = _UA()
    def _random_ua() -> str:
        return _ua_gen.random
except Exception:
    _UAS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    ]
    def _random_ua() -> str:
        return random.choice(_UAS)

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("aimusic")

# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------
N_WORKERS           = int(os.getenv("BROWSER_WORKERS", "10"))
TWOCAPTCHA_KEY      = os.getenv("TWOCAPTCHA_API_KEY", "")
AIMUSIC_APP         = "https://aimusic.so/app"
API_BASE            = "https://api.aimusic.so/api/v1"
LYRICAL_CREATE      = f"{API_BASE}/lyrical/create"
LYRICAL_STATUS      = f"{API_BASE}/lyrical/getLyricsByUuid/{{}}"
SUNO_CREATE         = f"{API_BASE}/suno/create"
SUNO_STATUS         = f"{API_BASE}/suno/record/{{}}"
TURNSTILE_SITEKEY   = "0x4AAAAAAAgeJUEUvYlF2CzO"
TURNSTILE_PAGE_URL  = "https://aimusic.so/app"

# ---------------------------------------------------------------------------
#  Browser fingerprint constants
# ---------------------------------------------------------------------------
_SCREEN_SIZES = [
    (1920, 1080), (1366, 768), (1440, 900),
    (1536, 864),  (1280, 720), (1600, 900),
]
_LANGUAGES = [
    "en-US,en;q=0.9", "en-GB,en;q=0.9",
    "en-US,en;q=0.8,es;q=0.6", "en-CA,en;q=0.9",
]
_PLATFORMS = ["Win32", "MacIntel", "Linux x86_64"]
_SEC_UA_MAP = {
    "Win32":    '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "MacIntel": '"Chromium";v="130", "Not_A Brand";v="24", "Google Chrome";v="130"',
    "Linux x86_64": '"Chromium";v="129", "Not_A Brand";v="24", "Google Chrome";v="129"',
}
_TIMEZONES = [
    "America/New_York", "Europe/London", "Asia/Dubai",
    "America/Los_Angeles", "Asia/Tokyo", "Europe/Paris",
]
_HW_CONCURRENCY = [4, 6, 8, 12, 16]
_DEVICE_MEMORY  = [4, 8, 16]

# ---------------------------------------------------------------------------
#  Task store
# ---------------------------------------------------------------------------
task_store: Dict[str, Dict] = {}

# ---------------------------------------------------------------------------
#  Identity / fingerprint helpers
# ---------------------------------------------------------------------------

def _fresh_unique_id() -> str:
    """Each MD5 hex = fresh guest account with 5 credits on aimusic.so."""
    return hashlib.md5(os.urandom(32)).hexdigest()


def build_fingerprint() -> Dict[str, Any]:
    """Complete unique browser identity for one request."""
    sw, sh = random.choice(_SCREEN_SIZES)
    plat   = random.choice(_PLATFORMS)
    ua     = _random_ua()
    return {
        "user_agent":    ua,
        "screen_w":      sw,
        "screen_h":      sh,
        "language":      random.choice(_LANGUAGES),
        "platform":      plat,
        "sec_ch_ua":     _SEC_UA_MAP.get(plat, _SEC_UA_MAP["Win32"]),
        "timezone":      random.choice(_TIMEZONES),
        "hw_concurrency":random.choice(_HW_CONCURRENCY),
        "device_memory": random.choice(_DEVICE_MEMORY),
        "unique_id":     _fresh_unique_id(),
        "profile_id":    str(uuid.uuid4()),
        "canvas_noise":  [random.randint(1, 5) for _ in range(3)],
    }


def _http_headers(fp: Optional[Dict] = None, verify_token: str = "") -> Dict:
    """Build spoofed browser request headers."""
    if fp is None:
        fp = build_fingerprint()
    plat_name = {"Win32": "Windows", "MacIntel": "macOS"}.get(fp["platform"], "Linux")
    h = {
        "Accept":               "application/json, text/plain, */*",
        "Accept-Language":      fp["language"],
        "Connection":           "keep-alive",
        "Content-Type":         "application/json",
        "Origin":               "https://aimusic.so",
        "Referer":              "https://aimusic.so/",
        "sec-ch-ua":            fp["sec_ch_ua"],
        "sec-ch-ua-mobile":     "?0",
        "sec-ch-ua-platform":   f'"{plat_name}"',
        "Sec-Fetch-Dest":       "empty",
        "Sec-Fetch-Mode":       "cors",
        "Sec-Fetch-Site":       "same-site",
        "uniqueId":             fp["unique_id"],
        "User-Agent":           fp["user_agent"],
    }
    if verify_token:
        h["verify"] = verify_token
    return h


# ---------------------------------------------------------------------------
#  2captcha Turnstile solver
# ---------------------------------------------------------------------------

async def solve_turnstile_2captcha(task_id: str) -> str:
    """
    Submit Turnstile challenge to 2captcha, poll until solved.
    Returns a valid Cloudflare Turnstile token.
    Costs ~$0.001 per solve.
    """
    if not TWOCAPTCHA_KEY:
        raise Exception("TWOCAPTCHA_API_KEY not set — cannot auto-solve Turnstile")

    log.info(f"[{task_id}] Submitting Turnstile to 2captcha…")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.2captcha.com/createTask",
            json={
                "clientKey": TWOCAPTCHA_KEY,
                "task": {
                    "type":       "TurnstileTaskProxyless",
                    "websiteURL": TURNSTILE_PAGE_URL,
                    "websiteKey": TURNSTILE_SITEKEY,
                },
            },
        )
    resp = r.json()
    if resp.get("errorId", 0) != 0:
        raise Exception(f"2captcha submit error: {resp.get('errorDescription')}")

    captcha_id = resp["taskId"]
    log.info(f"[{task_id}] 2captcha taskId={captcha_id}, polling…")

    # Poll for result
    for attempt in range(40):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15) as c:
            pr = await c.post(
                "https://api.2captcha.com/getTaskResult",
                json={"clientKey": TWOCAPTCHA_KEY, "taskId": captcha_id},
            )
        pdata = pr.json()
        if pdata.get("errorId", 0) != 0:
            raise Exception(f"2captcha poll error: {pdata.get('errorDescription')}")
        if pdata.get("status") == "ready":
            token = pdata["solution"]["token"]
            log.info(f"[{task_id}] Turnstile solved! token_len={len(token)}")
            return token
        log.debug(f"[{task_id}] 2captcha pending ({attempt+1}/40)…")

    raise Exception("2captcha Turnstile solve timed out (200s)")


# ---------------------------------------------------------------------------
#  Song generation — primary method (2captcha + direct suno/create)
# ---------------------------------------------------------------------------

async def generate_song_via_2captcha(task: Dict) -> Dict:
    """
    1. Solve Turnstile via 2captcha (~15-30s)
    2. Call suno/create directly with token + fresh uniqueId
    3. Poll suno/record until audio URLs appear
    """
    task_id = task["task_id"]
    fp      = build_fingerprint()

    # Step 1: Solve Turnstile
    verify_token = await solve_turnstile_2captcha(task_id)

    # Step 2: Call suno/create
    log.info(f"[{task_id}] Calling suno/create with fresh identity…")
    payload = {
        "prompt":       task["prompt"],
        "style":        task.get("style", ""),
        "title":        task.get("title", "AI Song"),
        "customMode":   bool(task.get("style")),
        "instrumental": task.get("instrumental", False),
        "model":        "Prime",
        "privateFlag":  False,
    }
    headers = _http_headers(fp, verify_token=verify_token)

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(SUNO_CREATE, json=payload, headers=headers)
    data = r.json()
    log.info(f"[{task_id}] suno/create response: code={data.get('code')}")

    if data.get("code") == 100001:
        raise Exception("Turnstile token rejected by server (code=100001)")
    if data.get("code") == 400:
        raise Exception("Turnstile token invalid or expired (code=400)")
    if data.get("code") == 430:
        # UniqueId exhausted — retry with new identity
        log.warning(f"[{task_id}] UniqueId exhausted (code=430) — retrying with fresh ID")
        fp = build_fingerprint()
        headers = _http_headers(fp, verify_token=verify_token)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(SUNO_CREATE, json=payload, headers=headers)
        data = r.json()
        if data.get("code") not in (200, None):
            raise Exception(f"suno/create retry failed: {data.get('msg')} (code={data.get('code')})")

    if data.get("code") != 200:
        raise Exception(f"suno/create error: {data.get('msg')} (code={data.get('code')})")

    # Step 3: Poll for audio URLs
    song_data = data.get("data", {})
    su_uuid   = (
        song_data.get("uuid") or
        song_data.get("taskId") or
        (song_data[0].get("uuid") if isinstance(song_data, list) and song_data else None)
    )

    if not su_uuid:
        # Already contains audio? (some APIs return immediately)
        if isinstance(song_data, list) and song_data[0].get("audioUrl"):
            return {"songs": song_data}
        if isinstance(song_data, dict) and song_data.get("audioUrl"):
            return {"songs": [song_data]}
        raise Exception(f"No uuid in suno/create response: {song_data}")

    return await poll_suno_async(su_uuid, fp["unique_id"], task_id)


async def poll_suno_async(task_uuid: str, unique_id: str, task_id: str) -> Dict:
    """Poll suno/record until audio URLs appear."""
    log.info(f"[{task_id}] Polling suno/record uuid={task_uuid}")
    fp = build_fingerprint()
    fp["unique_id"] = unique_id
    for i in range(72):
        await asyncio.sleep(5)
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    SUNO_STATUS.format(task_uuid),
                    headers=_http_headers(fp),
                )
            data = r.json()
            songs = data.get("data", {})
            if isinstance(songs, list):
                ready = [s for s in songs if s.get("audioUrl")]
                if ready:
                    log.info(f"[{task_id}] Songs ready! count={len(ready)}")
                    return {"uuid": task_uuid, "songs": ready}
            elif isinstance(songs, dict) and songs.get("audioUrl"):
                return {"uuid": task_uuid, "songs": [songs]}
            log.debug(f"[{task_id}] Poll {i+1}/72 — pending…")
        except Exception as e:
            log.warning(f"[{task_id}] Poll error: {e}")
    raise Exception("suno/record polling timed out (6 min)")


# ---------------------------------------------------------------------------
#  Fallback: browser-based generation (when no 2captcha key)
# ---------------------------------------------------------------------------

def _chromium_path() -> Optional[str]:
    env_path = os.getenv("CHROMIUM_PATH", "")
    if env_path and os.path.isfile(env_path):
        return env_path
    for p in [
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    ]:
        if os.path.isfile(p):
            return p
    import glob
    pw_paths = glob.glob(
        "/home/runner/workspace/.cache/ms-playwright/chromium*/chrome-linux/chrome"
    ) + glob.glob(
        os.path.expanduser("~/.cache/ms-playwright/chromium*/chrome-linux/chrome")
    )
    return sorted(pw_paths)[-1] if pw_paths else None


def _stealth_js(fp: Dict) -> str:
    cn = fp["canvas_noise"]
    return f"""
Object.defineProperty(navigator,'webdriver',{{get:()=>undefined,configurable:true}});
Object.defineProperty(navigator,'platform',{{get:()=>'{fp["platform"]}'}});
Object.defineProperty(navigator,'language',{{get:()=>'{fp["language"].split(",")[0]}'}});
Object.defineProperty(navigator,'languages',{{get:()=>['{fp["language"].split(",")[0]}','en']}});
Object.defineProperty(navigator,'hardwareConcurrency',{{get:()=>{fp["hw_concurrency"]}}});
Object.defineProperty(navigator,'deviceMemory',{{get:()=>{fp["device_memory"]}}});
Object.defineProperty(screen,'width',{{get:()=>{fp["screen_w"]}}});
Object.defineProperty(screen,'height',{{get:()=>{fp["screen_h"]}}});
const _pWebGL=(ctx)=>{{
  if(!ctx)return;
  const _g=ctx.prototype.getParameter;
  ctx.prototype.getParameter=function(p){{
    if(p===37445)return'Google Inc. (NVIDIA)';
    if(p===37446)return'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)';
    return _g.call(this,p);
  }};
}};
_pWebGL(WebGLRenderingContext);
if(typeof WebGL2RenderingContext!=='undefined')_pWebGL(WebGL2RenderingContext);
const _odtu=HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL=function(...a){{
  const c=this.getContext('2d');
  if(c){{const id=c.getImageData(0,0,1,1);id.data[0]^={cn[0]};id.data[1]^={cn[1]};id.data[2]^={cn[2]};c.putImageData(id,0,0);}}
  return _odtu.apply(this,a);
}};
window.chrome={{runtime:{{connect:()=>{{}},sendMessage:()=>{{}},onMessage:{{addListener:()=>{{}}}}}},loadTimes:()=>({{}}),csi:()=>({{}})}};
window._suno_response=null;window._suno_status='waiting';
const __of=window.fetch;
window.fetch=async function(...a){{
  const res=await __of.apply(this,a);
  const url=typeof a[0]==='string'?a[0]:(a[0].url||'');
  if(url.includes('suno/create')){{try{{const c=res.clone();const b=await c.json();window._suno_response=b;window._suno_status='captured';}}catch(e){{window._suno_status='error';}}}}
  return res;
}};
"""


def browser_generate_song_fallback(task: Dict) -> Dict:
    """Fallback: use headless Chrome to generate song (may fail Turnstile)."""
    if not UC_AVAILABLE:
        raise Exception("No 2captcha key and undetected-chromedriver not available")
    fp          = build_fingerprint()
    profile_dir = tempfile.mkdtemp(prefix=f"uc_{fp['profile_id']}_")
    driver      = None
    tid         = task["task_id"]
    try:
        opts = uc.ChromeOptions()
        for arg in [
            "--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--headless=new",
            "--disable-blink-features=AutomationControlled","--disable-infobars",
            "--disable-notifications","--disable-extensions","--no-first-run",
            f"--window-size={fp['screen_w']},{fp['screen_h']}",
            f"--lang={fp['language'].split(',')[0]}",
            f"--user-data-dir={profile_dir}",
            f"--user-agent={fp['user_agent']}",
        ]:
            opts.add_argument(arg)
        chrom = _chromium_path()
        # Detect version
        chrome_ver = None
        if chrom:
            import subprocess as _sp, re as _re
            try:
                out = _sp.run([chrom,"--version"], capture_output=True, text=True, timeout=5).stdout
                m   = _re.search(r"(\d+)\.\d+\.\d+", out)
                if m: chrome_ver = int(m.group(1))
            except Exception:
                pass
        uc_kw: Dict = {"options": opts, "use_subprocess": True, "version_main": chrome_ver}
        if chrom:
            uc_kw["browser_executable_path"] = chrom
        driver = uc.Chrome(**uc_kw)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _stealth_js(fp)})
        try:
            driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": fp["timezone"]})
        except Exception:
            pass

        driver.get(AIMUSIC_APP)
        wait = WebDriverWait(driver, 40)
        textarea = wait.until(EC.presence_of_element_located((By.TAG_NAME, "textarea")))

        # Wait a bit for Turnstile to attempt solve
        time.sleep(random.uniform(4, 8))
        textarea.click()
        time.sleep(0.4)
        for ch in task["prompt"]:
            textarea.send_keys(ch)
            time.sleep(random.uniform(0.04, 0.14))
        time.sleep(random.uniform(0.8, 2.0))

        btn = wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//button[contains(translate(.,'GENERATE MUSIC','generate music'),'generate music')]"
        )))
        ActionChains(driver).move_to_element(btn).pause(random.uniform(0.2, 0.5)).click().perform()
        log.info(f"[{tid}] [fallback] Clicked Generate Music")

        # Wait up to 90s for suno/create response
        deadline = time.time() + 90
        while time.time() < deadline:
            status = driver.execute_script("return window._suno_status;")
            if status == "captured":
                resp = driver.execute_script("return window._suno_response;")
                if resp and resp.get("code") == 200:
                    return {"songs": resp.get("data", {})}
                elif resp:
                    raise Exception(f"suno error {resp.get('code')}: {resp.get('msg')}")
            time.sleep(2)
        raise Exception("Browser fallback: 90s timeout — Turnstile not solved by headless Chrome")
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass
        shutil.rmtree(profile_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
#  Worker coroutines
# ---------------------------------------------------------------------------

async def _async_worker(worker_id: int, task_queue: asyncio.Queue):
    log.info(f"Async worker-{worker_id} ready")
    loop = asyncio.get_event_loop()
    while True:
        task = await task_queue.get()
        tid  = task["task_id"]
        log.info(f"Worker-{worker_id} ▶ task {tid}")
        task_store[tid]["status"]     = "processing"
        task_store[tid]["worker"]     = worker_id
        task_store[tid]["started_at"] = time.time()
        try:
            if TWOCAPTCHA_KEY:
                result = await generate_song_via_2captcha(task)
            else:
                # Fallback: run blocking browser in thread pool
                result = await loop.run_in_executor(
                    None, browser_generate_song_fallback, task
                )
            task_store[tid]["status"] = "done"
            task_store[tid]["result"] = result
            log.info(f"Worker-{worker_id} ✓ task {tid}")
        except Exception as e:
            log.error(f"Worker-{worker_id} ✗ task {tid}: {e}")
            task_store[tid]["status"] = "failed"
            task_store[tid]["error"]  = str(e)
        finally:
            task_queue.task_done()


# ---------------------------------------------------------------------------
#  Lyrics helper
# ---------------------------------------------------------------------------

async def _generate_lyrics_full(prompt: str) -> Dict:
    fp      = build_fingerprint()
    headers = _http_headers(fp)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(LYRICAL_CREATE, json={"prompt": prompt}, headers=headers)
    data = r.json()
    if data.get("code") != 200:
        raise HTTPException(502, f"Lyrics create error: {data.get('msg')}")
    luid = data["data"]["uuid"]
    for _ in range(60):
        await asyncio.sleep(3)
        async with httpx.AsyncClient(timeout=10) as c:
            sr = await c.get(LYRICAL_STATUS.format(luid), headers=_http_headers())
        sd = sr.json().get("data", {})
        if sd.get("status") == 1 and sd.get("completeData"):
            return {"uuid": luid, "items": sd["completeData"]}
        if sd.get("status") == 4:
            raise HTTPException(502, "Lyrics generation failed on server")
    raise HTTPException(504, "Lyrics timed out (180s)")


# ---------------------------------------------------------------------------
#  FastAPI
# ---------------------------------------------------------------------------

app       = FastAPI(title="AiMusic API", version="4.0.0")
_tq: Optional[asyncio.Queue] = None  # populated at startup

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LyricsReq(BaseModel):
    prompt:   str
    language: str = "english"
    style:    str = ""


class SongReq(BaseModel):
    prompt:       str
    style:        str = ""
    title:        str = "AI Song"
    instrumental: bool = False


class FullReq(BaseModel):
    topic:        str
    style:        str = "pop"
    language:     str = "english"
    instrumental: bool = False


# ── Status ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    q = _tq.qsize() if _tq else 0
    proc   = sum(1 for t in task_store.values() if t["status"] == "processing")
    done   = sum(1 for t in task_store.values() if t["status"] == "done")
    failed = sum(1 for t in task_store.values() if t["status"] == "failed")
    pending= sum(1 for t in task_store.values() if t["status"] == "pending")
    return {
        "service":          "AiMusic API v4",
        "mode":             "2captcha" if TWOCAPTCHA_KEY else "browser-fallback",
        "async_workers":    N_WORKERS,
        "uc_available":     UC_AVAILABLE,
        "queue":            q,
        "pending":          pending,
        "processing":       proc,
        "done":             done,
        "failed":           failed,
        "endpoints": {
            "POST /generate-lyrics": "Fast lyrics (no browser, unlimited)",
            "GET  /lyrics/{uuid}":   "Poll lyrics",
            "POST /generate-song":   "Queue song — auto Turnstile solve",
            "POST /generate-full":   "Lyrics + Song auto",
            "GET  /task/{id}":       "Poll task",
            "GET  /tasks":           "List tasks",
        },
        "setup_note": (
            "Set TWOCAPTCHA_API_KEY env var for automatic Turnstile solving. "
            "Get free $5 credit at 2captcha.com (~5000 free songs)."
        ) if not TWOCAPTCHA_KEY else "2captcha configured ✓",
    }


@app.get("/health")
async def health():
    return {"ok": True, "workers": N_WORKERS, "queue": _tq.qsize() if _tq else 0}


# ── Lyrics ─────────────────────────────────────────────────────────────────

@app.post("/generate-lyrics")
async def api_lyrics(req: LyricsReq):
    """Generate AI lyrics — unlimited, no browser, fast (~15s)."""
    lang_map = {
        "urdu":    "(Write in Urdu and Roman Urdu)",
        "hindi":   "(Write in Hindi)",
        "punjabi": "(Write in Punjabi)",
        "arabic":  "(Write in Arabic)",
    }
    suffix = lang_map.get(req.language.lower(), f"(Write in {req.language})" if req.language.lower() != "english" else "")
    prompt = f"{req.prompt} {suffix}"
    if req.style:
        prompt += f". Style: {req.style}"

    result = await _generate_lyrics_full(prompt.strip())
    items  = result["items"]
    return {
        "ok":     True,
        "uuid":   result["uuid"],
        "lyrics": [
            {"title": i.get("title",""), "text": i.get("text","")}
            for i in items if i.get("status") == "complete"
        ],
    }


@app.get("/lyrics/{luid}")
async def api_lyrics_status(luid: str):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(LYRICAL_STATUS.format(luid), headers=_http_headers())
    d = r.json()
    if d.get("code") != 200:
        raise HTTPException(502, d.get("msg"))
    info = d["data"]
    if info.get("status") == 1 and info.get("completeData"):
        return {
            "status": "complete",
            "lyrics": [
                {"title": i.get("title",""), "text": i.get("text","")}
                for i in info["completeData"] if i.get("status") == "complete"
            ],
        }
    return {"status": "generating", "note": "Retry after 3s"}


# ── Song ───────────────────────────────────────────────────────────────────

@app.post("/generate-song")
async def api_song(req: SongReq):
    """
    Queue song generation. Each request = unique identity + unique guest account.

    If TWOCAPTCHA_API_KEY is set: uses 2captcha to auto-solve Turnstile → suno/create.
    If not set: tries headless Chrome (may fail Cloudflare Turnstile check).

    Concurrency: N_WORKERS async workers handle 100K+ queued requests.
    Poll GET /task/{task_id} every 5s for result (audio URLs).
    """
    if _tq is None:
        raise HTTPException(503, "Task queue not initialized — server starting up")
    tid  = str(uuid.uuid4())
    task = {
        "task_id":      tid,
        "prompt":       req.prompt,
        "style":        req.style,
        "title":        req.title,
        "instrumental": req.instrumental,
    }
    task_store[tid] = {
        "task_id":    tid,
        "status":     "pending",
        "prompt":     req.prompt,
        "style":      req.style,
        "queued_at":  time.time(),
        "started_at": None,
        "worker":     None,
        "result":     None,
        "lyrics":     None,
        "error":      None,
    }
    await _tq.put(task)
    qsize = _tq.qsize()
    log.info(f"Task {tid} queued (queue_size={qsize})")
    return {
        "ok":             True,
        "task_id":        tid,
        "status":         "pending",
        "queue_position": qsize,
        "poll_url":       f"/task/{tid}",
        "note":           "Unique browser identity per request. Poll /task/{task_id} every 5s for audio URLs.",
    }


@app.post("/generate-full")
async def api_full(req: FullReq):
    """
    Full pipeline: generates lyrics first (fast), then queues song.
    Returns lyrics immediately + task_id for song polling.
    """
    if _tq is None:
        raise HTTPException(503, "Task queue not initialized")
    lang_map = {
        "urdu": "(Write in Urdu and Roman Urdu)",
        "hindi": "(Write in Hindi)",
        "punjabi": "(Write in Punjabi)",
    }
    lang_suffix = lang_map.get(req.language.lower(), f"(Write in {req.language})" if req.language.lower() != "english" else "")
    lyric_prompt = f"Write a {req.style} song about: {req.topic} {lang_suffix}".strip()

    result = await _generate_lyrics_full(lyric_prompt)
    items  = result["items"]
    lyrics = "\n\n".join(
        i.get("text","") for i in items if i.get("status") == "complete"
    )
    title  = items[0].get("title", req.topic) if items else req.topic

    tid  = str(uuid.uuid4())
    task = {
        "task_id":      tid,
        "prompt":       lyrics[:500] if lyrics else req.topic,
        "style":        req.style,
        "title":        title,
        "instrumental": req.instrumental,
    }
    task_store[tid] = {
        "task_id":    tid,
        "status":     "pending",
        "topic":      req.topic,
        "style":      req.style,
        "lyrics":     lyrics,
        "lyrics_uuid":result["uuid"],
        "queued_at":  time.time(),
        "started_at": None,
        "worker":     None,
        "result":     None,
        "error":      None,
    }
    await _tq.put(task)
    log.info(f"Full task {tid} queued (lyrics_uuid={result['uuid']})")
    return {
        "ok":          True,
        "task_id":     tid,
        "status":      "pending",
        "title":       title,
        "lyrics":      lyrics,
        "lyrics_uuid": result["uuid"],
        "poll_url":    f"/task/{tid}",
        "note":        "Lyrics ready. Song generation queued — poll /task/{task_id} every 5-10s.",
    }


# ── Task polling ───────────────────────────────────────────────────────────

@app.get("/task/{tid}")
async def api_task(tid: str):
    """
    Poll task status.
    status: pending → processing → done / failed
    When done: result.songs = list of {audioUrl, imageUrl, title, ...}
    """
    if tid not in task_store:
        raise HTTPException(404, "Task not found")
    t       = task_store[tid]
    elapsed = round(time.time() - (t.get("queued_at") or time.time()), 1)
    proc_s  = round(time.time() - t["started_at"], 1) if t.get("started_at") else None
    return {
        "task_id":      tid,
        "status":       t["status"],
        "elapsed_s":    elapsed,
        "processing_s": proc_s,
        "worker":       t.get("worker"),
        "result":       t.get("result"),
        "lyrics":       t.get("lyrics"),
        "error":        t.get("error"),
    }


@app.get("/tasks")
async def api_tasks():
    all_t = list(task_store.values())
    return {
        "total":      len(all_t),
        "pending":    sum(1 for t in all_t if t["status"] == "pending"),
        "processing": sum(1 for t in all_t if t["status"] == "processing"),
        "done":       sum(1 for t in all_t if t["status"] == "done"),
        "failed":     sum(1 for t in all_t if t["status"] == "failed"),
        "tasks":      all_t[-100:],
    }


@app.delete("/task/{tid}")
async def api_delete_task(tid: str):
    if tid not in task_store:
        raise HTTPException(404, "Task not found")
    del task_store[tid]
    return {"deleted": tid}


# ---------------------------------------------------------------------------
#  Startup: launch async workers
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    global _tq
    _tq = asyncio.Queue()
    mode = "2captcha + direct API" if TWOCAPTCHA_KEY else "browser fallback (set TWOCAPTCHA_API_KEY for reliability)"
    log.info(f"Starting {N_WORKERS} async workers — mode: {mode}")
    for i in range(N_WORKERS):
        asyncio.create_task(_async_worker(i, _tq))
    log.info(f"AiMusic API v4 ready ✓  ({N_WORKERS} workers, mode={mode})")


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port   = int(os.getenv("PORT", "5000"))
    reload = os.getenv("RAILWAY_ENVIRONMENT") is None
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)
