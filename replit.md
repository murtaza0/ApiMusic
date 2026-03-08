# musichero.ai Music Generation Bot

A Python automation bot that generates music via the musichero.ai API with a
self-contained hCaptcha bypass (no paid service required by default) and
unlimited song generation loops.

## Architecture

| File | Purpose |
|------|---------|
| `main.py` | Entry point, API calls, song queue, polling loop |
| `hcaptcha_bypass.py` | Headless-browser hCaptcha solver (playwright + stealth) |

### Key functions in `main.py`
- **`get_hcaptcha_token()`** — Routes to bypass or paid solver; returns token string
- **`create_song()`** — POSTs to `/api/v1/suno/create` with fresh `uniqueId` + token
- **`poll_for_result()`** — GETs `/api/v1/suno/pageRecordList` every 10 s until `status == "finished"`
- **`run_generation_loop()`** — Iterates a list of song configs using a shared `requests.Session`

### How the bypass works (`hcaptcha_bypass.py`)
The bypass uses two headless-Chromium strategies in order:

**Strategy 1 — Invisible execute()** (fastest):
1. Playwright launches headless Chromium with `playwright-stealth` applied
2. An intercepted route serves a minimal HTML page at the target host domain,
   so hCaptcha sees the correct `host=` parameter and enterprise scoring profile
3. `hcaptcha.execute()` is called programmatically, triggering the invisible flow
4. For passive/enterprise sites (musichero.ai uses `pass=true`), hCaptcha resolves
   the proof-of-work internally (hsw.js WASM) and returns the token without
   any visual challenge
5. The token is read from `window.__hcapToken` or the hidden textarea

**Strategy 2 — Normal checkbox click** (fallback):
- Same Playwright setup but renders the normal checkbox widget
- Auto-click init script injected into every frame fires `.click()` on `#checkbox`
- Playwright's `frame_locator().click()` also dispatches a trusted input event

**hCaptcha protocol (researched):**
- `checksiteconfig` → returns HSW proof-of-work challenge JWT + `pass=true`
- `hsw.js` (843 KB, with embedded WASM) executes the proof-of-work
- `getcaptcha` POST body is **MessagePack** (not JSON), format: `[c_json, ExtType(18, n_token)]`
  where `n_token` is 19–20 KB of HSW result + browser fingerprint + motion data
- For passive enterprise sites, `getcaptcha` returns `generated_pass_UUID` (the token) directly

**Rate limits:**
- hCaptcha enforces per-IP per-sitekey rate limits on `getcaptcha`
- Heavy testing (30+ solves in ~30 min) from the same IP triggers 429s that last 1-2 hours
- Normal production usage (1-2 songs/hour) does NOT trigger rate limits
- The bypass includes exponential backoff: 2 min → 5 min → 10 min → 20 min

## Configuration

All settings via Replit Secrets (environment variables):

| Secret | Default | Description |
|--------|---------|-------------|
| `CAPTCHA_PROVIDER` | `bypass` | `bypass` (free) or `2captcha` / `capsolver` / `anticaptcha` |
| `CAPTCHA_API_KEY` | — | Required only for paid providers |

### Supported paid solvers (avoid IP rate limits entirely)
- **CapSolver** — https://capsolver.com (~$0.80 per 1,000 solves)
- **2captcha** — http://2captcha.com (~$1–2 per 1,000 solves)
- **Anti-Captcha** — https://anti-captcha.com (~$1 per 1,000 solves)

## Usage

Edit `song_queue` at the bottom of `main.py`, then run the **Run Bot** workflow.

### Simple mode (auto-generated style + lyrics)
```python
{"mode": "simple", "prompt": "An upbeat electronic pop song about chasing dreams"}
```

### Custom mode (your own lyrics + title)
```python
{
    "mode":   "custom",
    "prompt": "Dark cinematic orchestral with haunting piano",  # style
    "title":  "Shadows of Tomorrow",
    "lyrics": "[Verse 1]\nIn the silence of the night...",
}
```

## API Reference
- **Create:** `POST https://api.musichero.ai/api/v1/suno/create`
  - Headers: `uniqueId` (uuid4 hex, fresh per request), `verify` (hCaptcha token)
  - Body: `{"prompt": "...", "customMode": false}` or `{"prompt": "...", "mv": "...", "title": "...", "customMode": true}`
- **Poll:** `GET https://api.musichero.ai/api/v1/suno/pageRecordList?pageNum=1`
- **hCaptcha site key:** `6520ce9c-a8b2-4cbe-b698-687e90448dec`
- **hCaptcha version hash:** `5ea3feff9cf1292d7051510930d98c4719f64575`

## Dependencies
- `requests` — HTTP client
- `playwright` + Playwright Chromium — headless browser for bypass
- `playwright-stealth` — browser fingerprint evasion (~20 signals patched)
- `msgpack` — MessagePack codec (hCaptcha's binary protocol format)
