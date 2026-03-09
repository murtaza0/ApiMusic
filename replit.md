# anymusic.ai Music Generation Bot

A Python web app + CLI that generates music via the anymusic.ai API.
No captcha required — cookie-based session authentication only.

## Architecture

| File | Purpose |
|------|---------|
| `app.py` | Flask web server — routes, background job threads, job polling |
| `bot_core.py` | Core API logic: generate, poll, download, cookie rotation |
| `batch.py` | Batch runner — processes up to 5 prompts from `prompts.txt` |
| `main.py` | Simple CLI entry point |
| `prompts.txt` | Song prompts for batch processing (edit this) |
| `templates/index.html` | Single-page web UI |
| `music_outputs/` | Downloaded MP3 files (auto-created) |

## Running

```bash
python app.py       # Web UI at http://0.0.0.0:5000
python batch.py     # Batch process prompts.txt
python main.py      # Single-song CLI
```

## Web UI Flow

1. Click **Session Settings** → expand cookie panel
2. Copy Cookie header from DevTools (F12 → Network → any anymusic.ai request)
3. Paste and click **Save Cookie** (dot turns green)
4. **Simple mode**: describe song + pick genre → generates text-to-song
5. **Custom mode**: enter lyrics + title + style/mood/scenario → generates lyrics-to-song
6. Click **Generate Song** → job appears in the right panel with live logs
7. When done, audio player appears and MP3 is saved to `music_outputs/`

## Batch Processing (batch.py)

Edit `prompts.txt` — separate songs with `---` on its own line:
```
Verse 1: your lyrics here...
Chorus: more lyrics... → title::Song Title

---

text-to-song::Upbeat pop song description → genre::Pop
```
Then run `python batch.py`. Processes 5 songs sequentially.
Set `ANYMUSIC_COOKIE` env var to skip the cookie prompt.

## API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serve web UI |
| `/api/set-cookie` | POST | Save session cookie |
| `/api/cookie-status` | GET | Check if cookie is set |
| `/api/generate` | POST | Start a generation job |
| `/api/status/<job_id>` | GET | Poll job status |

## anymusic.ai API

- **Generate:** `POST https://anymusic.ai/api/music/generate`
  - `text-to-song`: `{"type":"text-to-song","prompt":"...","genre":"R&B","quantity":1}`
  - `lyrics-to-song`: `{"type":"lyrics-to-song","lyrics":"...","title":"...","styles":{...}}`
- **Status poll:** `GET https://anymusic.ai/api/music/task/{taskId}` → checks `status` field
- **Fallback poll:** `GET https://anymusic.ai/api/music/list`

## Key Features

- **Cookie rotation**: `_ga` and `_clck` randomised every 3 requests to mimic new sessions
- **Auto-download**: completed songs saved as MP3 to `music_outputs/`
- **Smart polling**: tries task-specific URL first, falls back to list endpoint
- **Session spoofing**: full browser header set on every request
