# AiMusic API v4 — Production Grade

FastAPI REST API deployed on **Railway** (`apimusic-production-ef04.up.railway.app`).
Generates AI music via **aimusic.so** (Suno-powered).

## Architecture

- **Lyrics** → aimusic.so `/lyrical/create` (unlimited, no auth, pure HTTP)
- **Song**   → 2captcha Turnstile solver → suno/create directly (no browser needed)
- **Queue**  → asyncio.Queue with N async workers handles unlimited concurrent users
- **Identity** → fresh uniqueId (MD5) + full browser fingerprint per request

## File Structure

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app v4 — all routes, 2captcha Turnstile, async workers, fingerprinting |
| `requirements.txt` | fastapi, uvicorn, httpx, selenium, undetected-chromedriver, fake-useragent |
| `nixpacks.toml` | Railway build: installs chromium + chromedriver system packages |
| `railway.json` | Railway deploy config |
| `Procfile` | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

## API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Status + queue info |
| `/health` | GET | Liveness probe |
| `/generate-lyrics` | POST | AI lyrics (no browser, unlimited, ~15s) |
| `/lyrics/{uuid}` | GET | Poll lyrics status |
| `/generate-song` | POST | Queue song (auto Turnstile bypass) |
| `/generate-full` | POST | Lyrics + Song together (auto) |
| `/task/{id}` | GET | Poll song task status |
| `/tasks` | GET | List all tasks |
| `/docs` | GET | Swagger UI |

## Key Parameters

### POST /generate-lyrics
```json
{ "prompt": "sad love story", "language": "urdu", "style": "ghazal" }
```
Languages: english, urdu, hindi, punjabi, arabic

### POST /generate-song
```json
{ "prompt": "...", "style": "pop", "title": "My Song" }
```
Returns `task_id` immediately. Poll `/task/{task_id}` every 5s.

### POST /generate-full
```json
{ "topic": "summer vibes", "style": "pop", "language": "english" }
```
Generates lyrics then queues song. Returns lyrics + task_id.

## Song Generation — How It Works

**With TWOCAPTCHA_API_KEY set (recommended):**
1. Submit Turnstile challenge to 2captcha (~15-30s to solve)
2. Use returned token + fresh uniqueId to call suno/create API directly
3. Poll suno/record until audio URLs appear
4. Cost: ~$0.001 per song (2captcha.com free trial gives $5 = ~5000 songs)

**Without TWOCAPTCHA_API_KEY (fallback):**
- Uses headless Selenium + undetected-chromedriver
- May fail Cloudflare Turnstile check (headless browsers often detected)

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | Auto (Railway) | Server port |
| `TWOCAPTCHA_API_KEY` | **Strongly recommended** | 2captcha API key for Turnstile |
| `BROWSER_WORKERS` | Optional (default: 10) | Number of async workers |
| `CHROMIUM_PATH` | Optional | Override Chromium binary path |

## Browser Fingerprinting (per request)

Each request gets a unique identity:
- Random User-Agent from fake-useragent library
- Random screen resolution (1920x1080, 1366x768, etc.)
- Random timezone, language, platform
- Canvas fingerprint noise (random pixel XOR)
- WebGL vendor/renderer spoofing
- Audio fingerprint noise
- Fake Chrome plugins array
- Fresh MD5 uniqueId = new guest account on aimusic.so (5 credits each)

## Concurrency

- 10 async workers process tasks from a queue
- 100K+ concurrent requests → all queued, processed in order
- Each worker processes tasks independently with its own browser identity

## aimusic.so API Details

- Turnstile sitekey: `0x4AAAAAAAgeJUEUvYlF2CzO`
- Lyrics create: `POST https://api.aimusic.so/api/v1/lyrical/create`
- Lyrics status: `GET https://api.aimusic.so/api/v1/lyrical/getLyricsByUuid/{uuid}`
- Suno create: `POST https://api.aimusic.so/api/v1/suno/create` (needs `verify` header)
- Suno status: `GET https://api.aimusic.so/api/v1/suno/record/{uuid}`
- UniqueId: fresh MD5 hex per request = fresh guest account = 5 credits

## Railway Setup

1. Repo: `github.com/murtaza0/ApiMusic.git`
2. Railway auto-deploys on push
3. **Set environment variable**: `TWOCAPTCHA_API_KEY` = your 2captcha key
4. nixpacks.toml installs chromium system packages for Railway
