# anymusic.ai — Production-Ready FastAPI

FastAPI-based REST API that generates AI music via anymusic.ai.
Deployable on Vercel Serverless (60 s max duration).

## File Structure

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app — all routes, spoofing logic, async generation |
| `requirements.txt` | fastapi, uvicorn, httpx, curl_cffi, pydantic |
| `vercel.json` | Vercel routing + maxDuration: 60 |
| `music_outputs/` | Local MP3 downloads (Replit only) |

## API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Service info + endpoint map |
| `/health` | GET | Liveness probe |
| `/generate` | POST | Fire 2 parallel requests → returns 2 task_ids |
| `/status/{task_id}` | GET | Single-check poll; streams audio/mpeg when ready |
| `/docs` | GET | Auto-generated Swagger UI |

## POST /generate

```json
{
  "prompt":   "upbeat Punjabi dhol pop",
  "genre":    "Pop",
  "mode":     "text-to-song"
}
```

Lyrics mode adds: `title`, `lyrics`, `style`, `mood`, `scenario`

Response:
```json
{
  "ok": true,
  "variants": [
    {"task_id": "ready_<b64>",  "status": "ready"},
    {"task_id": "abc123",       "status": "pending"}
  ]
}
```

## GET /status/{task_id}

- `ready_<b64>` → decodes CDN URL, fetches and streams `audio/mpeg` immediately
- real task_id → single poll to anymusic.ai `/api/music/task/{id}`; returns
  `{"status":"pending"}` or streams audio if done

**Client should retry every 8 s until it receives audio bytes.**

## Variant Logic

`/generate` fires 2 parallel `curl_cffi AsyncSession` requests using different
browser profiles (randomised User-Agent + Sec-Ch-Ua + platform). Both run via
`asyncio.gather` with a 90 s timeout.

## Cookie / Identity Spoofing

Every request generates fresh `_ga`, `_ga_*`, `_clck`, `_clsk` cookies plus
randomised `Accept-Language`. `curl_cffi` uses Chrome TLS fingerprints
(`impersonate="chrome131"`) to pass bot checks.

## anymusic.ai API

- Generate: `POST https://anymusic.ai/api/music/generate`
- Task poll: `GET  https://anymusic.ai/api/music/task/{task_id}`
- CORS: `Access-Control-Allow-Origin: *`
- IP policy: Vercel IPs allowed; Replit datacenter IPs blocked

## Vercel Deployment

1. Push repo to GitHub (`https://github.com/murtaza0/ApiMusic.git`)
2. Import on vercel.com → auto-detects `vercel.json`
3. No env vars required (optional: set `API_KEY` for auth)
4. Deploy → server-side generation works (Vercel IPs not blocked)

## GitHub Push (from Replit Shell)

```bash
git remote set-url origin https://YOUR_TOKEN@github.com/murtaza0/ApiMusic.git
git add -A
git commit -m "update"
git push origin main
```

Replace `YOUR_TOKEN` with a GitHub Personal Access Token (Settings → Developer settings → Personal access tokens → repo scope).

## Running Locally (Replit)

Workflow: `python main.py` → uvicorn on port 5000
Swagger UI: `https://<replit-url>/docs`
