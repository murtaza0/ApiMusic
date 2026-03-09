# anymusic.ai Music Generation Bot

A Python web app + CLI that generates music via the anymusic.ai API.
No captcha required — just paste your browser session cookie once and
generate songs directly through the UI.

## Architecture

| File | Purpose |
|------|---------|
| `app.py` | Flask web server — routes, background job threads, job polling |
| `bot_core.py` | Core API logic: `create_song()`, `poll_for_result()`, `run_job()` |
| `main.py` | CLI entry point (fallback; web UI is preferred) |
| `templates/index.html` | Single-page web UI |

## Running

```bash
python app.py       # web UI at http://0.0.0.0:5000
python main.py      # CLI mode (set ANYMUSIC_COOKIE env var or paste when prompted)
```

## Web UI Flow

1. Click the **Session Settings** bar → expand the cookie panel
2. Open anymusic.ai in your browser, copy the full Cookie header value from DevTools
3. Paste into the cookie field and click **Save Cookie** (dot turns green)
4. Choose **Simple** mode (prompt + genre) or **Custom** mode (lyrics + style/mood/scenario)
5. Click **✨ Generate Song** — job appears immediately in the right panel
6. Live logs stream in; when done an audio player appears with the result

## API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serve web UI |
| `/api/set-cookie` | POST | Save session cookie `{cookie}` → `{ok, has_cookie}` |
| `/api/cookie-status` | GET | Check if cookie is set |
| `/api/generate` | POST | Start a generation job; returns `{job_id}` |
| `/api/status/<job_id>` | GET | Poll job status: `{status, logs, audio_url, error}` |

## anymusic.ai API

- **Generate:** `POST https://anymusic.ai/api/music/generate`
  - Simple mode: `{"type":"text-to-song","prompt":"...","genre":"R&B","quantity":1,"is_private":false}`
  - Custom mode: `{"type":"lyrics-to-song","lyrics":"...","title":"...","styles":{"style":["Classical"],"mood":["Romantic"],"scenario":["Urban romance"]},"quantity":1,"is_private":false}`
  - Required headers: Cookie (user's browser session), Origin/Referer: https://anymusic.ai
- **Poll:** `GET https://anymusic.ai/api/music/list`
  - Returns list of songs; checks `status` field for completion
  - Audio URL extracted from `audioUrl` or similar fields

## Notes

- No captcha of any kind — anymusic.ai uses cookie-based sessions only
- The cookie from the network capture includes GA and Clarity tracking cookies
- If the API returns 401, the session cookie has expired and needs refreshing
- Generation typically takes 1-3 minutes; polling runs every 8 seconds up to 6 minutes
