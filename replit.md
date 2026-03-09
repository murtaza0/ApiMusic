# anymusic.ai Music Generation Bot + REST API

A Python web app (Flask) + REST API + CLI that generates music via the anymusic.ai API.
No captcha, no manual cookie setup — API calls go directly from the user's browser (web UI) or via the public REST API.

## Architecture

| File | Purpose |
|------|---------|
| `app.py` | Flask server — web UI, public REST API `/v1/`, internal endpoints |
| `bot_core.py` | Core logic: generate, poll, download, spoofing (used by CLI/batch/API) |
| `batch.py` | Batch runner — processes prompts from `prompts.txt` |
| `main.py` | Simple CLI entry point |
| `prompts.txt` | Song prompts for batch processing |
| `templates/index.html` | Single-page web UI — makes API calls client-side from browser |
| `music_outputs/` | Downloaded MP3 files (auto-created) |
| `vercel.json` | Vercel deployment config |
| `requirements.txt` | Python dependencies for deployment |

## Public REST API — /v1/

**Base URL:** `https://your-domain/v1/`

**Auth (optional):** Set `API_KEY` env var → clients send `Authorization: Bearer <key>`

### Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/v1/health` | GET | Server health + auth status |
| `/v1/generate` | POST | Start async generation → returns `job_id` |
| `/v1/jobs/<job_id>` | GET | Poll job status + get audio URLs |

### POST /v1/generate

Request body (JSON):
```json
{
  "prompt":   "sad rainy night piano",
  "genre":    "Classical",
  "quantity": 2,
  "mode":     "text-to-song"
}
```

For lyrics mode:
```json
{
  "prompt":   "any description",
  "mode":     "lyrics-to-song",
  "lyrics":   "Verse 1: full lyrics here...",
  "title":    "My Song",
  "style":    "Pop",
  "mood":     "Romantic",
  "scenario": "Late night drive",
  "quantity": 2
}
```

Response (202):
```json
{
  "ok": true,
  "job_id": "a23c6bee...",
  "status": "queued",
  "poll_url": "/v1/jobs/a23c6bee..."
}
```

### GET /v1/jobs/<job_id>

Poll this until `done: true`.

Response:
```json
{
  "ok": true,
  "job_id": "a23c6bee...",
  "status": "ok",
  "done": true,
  "variants": [
    { "cdn_url": "https://...", "local_url": "/audio/track_v1_....mp3" },
    { "cdn_url": "https://...", "local_url": "/audio/track_v2_....mp3" }
  ],
  "logs": ["[generate] ...", "[poll] ..."],
  "error": null
}
```

Status values: `queued` → `running` → `ok` | `failed` | `timeout` | `error`

### Example: call from anywhere (curl)

```bash
# Start generation
curl -X POST https://your-app.com/v1/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{"prompt": "Punjabi folk dhol beat", "genre": "Punjabi Folk", "quantity": 2}'

# Poll until done
curl https://your-app.com/v1/jobs/<job_id> \
  -H "Authorization: Bearer YOUR_KEY"
```

## Vercel Deployment

1. Push repo to GitHub
2. Import on vercel.com → auto-detects `vercel.json`
3. Set env vars: `API_KEY` (optional), `SESSION_SECRET`
4. Deploy — Vercel IPs are not blocked by anymusic.ai so server-side generation works

## Web UI Flow

1. Open the app — "Auto-session active" (no setup needed)
2. **Simple mode**: type song description + genre → text-to-song
3. **Custom mode**: enter lyrics + title + style/mood/scenario → lyrics-to-song
4. Click **Generate Song** → 2 variants generated, shown side-by-side
5. Server saves both MP3s to `music_outputs/`

## anymusic.ai API

- **Generate:** `POST https://anymusic.ai/api/music/generate`
  - `text-to-song`: `{"type":"text-to-song","prompt":"...","genre":"Pop","quantity":2}`
  - `lyrics-to-song`: `{"type":"lyrics-to-song","lyrics":"...","title":"...","styles":{...},"quantity":2}`
- **CORS**: `Access-Control-Allow-Origin: *` — only `Content-Type` header allowed (no custom headers)
- **IP policy**: browser (residential) IPs allowed; datacenter IPs (Replit) blocked; Vercel allowed

## Key Features

- **2 variants per request**: `quantity: 2` in all modes
- **Client-side API calls**: browser fetches anymusic.ai directly (no Replit IP block)
- **Public REST API**: `/v1/generate` + `/v1/jobs/<id>` for external integrations
- **Optional API key auth**: `API_KEY` env var protects all `/v1/` routes
- **Vercel-ready**: `vercel.json` + `requirements.txt` included
- **Auto-session**: GA tracking cookies auto-generated; no browser extraction needed
- **Auto-download**: completed songs saved as MP3 to `music_outputs/`
- **curl_cffi**: Chrome TLS fingerprint spoofing for CLI/batch
