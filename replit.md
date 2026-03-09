# anymusic.ai Music Generation Bot

A Python web app (Flask) + CLI that generates music via the anymusic.ai API.
No captcha, no manual cookie setup — API calls go directly from the user's browser.

## Architecture

| File | Purpose |
|------|---------|
| `app.py` | Flask web server — serves UI, `/api/notify` for download, cookie override |
| `bot_core.py` | Core API logic: generate, poll, download, cookie rotation (used by CLI/batch) |
| `batch.py` | Batch runner — processes up to 5 prompts from `prompts.txt` |
| `main.py` | Simple CLI entry point |
| `prompts.txt` | Song prompts for batch processing (edit this) |
| `templates/index.html` | Single-page web UI — makes API calls client-side from browser |
| `music_outputs/` | Downloaded MP3 files (auto-created) |

## How Generation Works (Web UI)

**Key insight**: anymusic.ai blocks Replit's server IPs but allows browser requests.
The web UI makes API calls **directly from the user's browser** to anymusic.ai, bypassing the server-IP block.
anymusic.ai returns `Access-Control-Allow-Origin: *` so cross-origin browser fetch works fine.

Flow:
1. User types prompt + genre → clicks Generate Song
2. Browser JS calls `POST https://anymusic.ai/api/music/generate` directly
3. Browser polls `GET https://anymusic.ai/api/music/task/{taskId}` until done
4. Audio player appears; browser notifies our server (`/api/notify`) to save the MP3

## Web UI Flow

1. Open the app — session is "Auto-session active" (no cookie setup needed)
2. **Simple mode**: type song description + type genre (Punjabi Folk, Qawwali, Pop, etc.) → text-to-song
3. **Custom mode**: enter lyrics + title + style/mood/scenario → lyrics-to-song
4. Click **Generate Song** → job card appears with live logs
5. When done, audio player appears and MP3 is saved to `music_outputs/`

## Batch Processing (batch.py / CLI)

Edit `prompts.txt` — separate songs with `---` on its own line:
```
Verse 1: your lyrics here...
Chorus: more lyrics... → title::Song Title

---

text-to-song::Upbeat pop song description → genre::Pop
```
Then run `python batch.py`. Processes up to 5 songs sequentially.
Set `ANYMUSIC_COOKIE` env var to provide a custom session cookie.

## Flask API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serve web UI |
| `/api/set-cookie` | POST | Save manual session cookie override |
| `/api/cookie-status` | GET | Check if manual cookie is set |
| `/api/generate` | POST | Server-side generation (used by batch/CLI, not web UI) |
| `/api/status/<job_id>` | GET | Poll server-side job status |
| `/api/notify` | POST | Receive completed audio URL from browser, trigger MP3 download |

## anymusic.ai API

- **Generate:** `POST https://anymusic.ai/api/music/generate`
  - `text-to-song`: `{"type":"text-to-song","prompt":"...","genre":"Pop","quantity":1}`
  - `lyrics-to-song`: `{"type":"lyrics-to-song","lyrics":"...","title":"...","styles":{...}}`
- **Status poll:** `GET https://anymusic.ai/api/music/task/{taskId}` → checks `status` field
- **CORS**: `Access-Control-Allow-Origin: *` on all API endpoints
- **IP policy**: browser (residential) IPs allowed; datacenter IPs blocked

## Key Features

- **Client-side API calls**: browser makes anymusic.ai requests directly (no server IP block)
- **Auto-session**: tracking cookies auto-generated; no browser extraction needed
- **Free-text genre**: type any genre (Punjabi Folk, Qawwali, R&B) with autocomplete suggestions
- **Cookie rotation**: GA cookies randomised every 3 requests (CLI/batch mode)
- **Auto-download**: completed songs saved as MP3 to `music_outputs/`
- **curl_cffi**: uses Chrome 131 TLS fingerprint for CLI/batch requests
