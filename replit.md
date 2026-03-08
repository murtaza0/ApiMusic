# musichero.ai Music Generation Bot

A Python web app + CLI bot that generates music via the musichero.ai API.
The user solves hCaptcha manually in their browser; the web UI walks them
through each step and runs generation jobs with live status updates.

## Architecture

| File | Purpose |
|------|---------|
| `app.py` | Flask web server — routes, background job threads, SSE polling |
| `bot_core.py` | Core API logic: `create_song()`, `poll_for_result()`, `run_job()` |
| `main.py` | CLI entry point (fallback; web UI is preferred) |
| `hcaptcha_bypass.py` | Headless-browser hCaptcha solver (bypass mode only) |
| `templates/index.html` | Single-page web UI |

## Running

```bash
python app.py       # web UI at http://0.0.0.0:5000
python main.py      # CLI mode
```

## Web UI Flow

1. User enters song prompt (Simple mode) or prompt + title + lyrics (Custom mode)
2. Clicks **Open musichero.ai** → solves hCaptcha in that tab
3. Presses F12 → Console → runs JS command → copies token
4. Pastes token into the web UI → clicks **Generate Song**
5. Job appears on the right panel with live log output
6. When done, an audio player appears with the result URL

## API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serve web UI |
| `/api/generate` | POST | Start a generation job; returns `{job_id}` |
| `/api/status/<job_id>` | GET | Poll job status: `{status, logs, audio_url, error}` |

## Environment Variables (Replit Secrets)

| Variable | Default | Description |
|----------|---------|-------------|
| `CAPTCHA_PROVIDER` | `manual` | `manual` / `bypass` / `2captcha` / `capsolver` / `anticaptcha` |
| `CAPTCHA_API_KEY` | — | Required for paid providers |
| `HCAPTCHA_PROXY` | — | Proxy for bypass engine: `socks5://host:port` |

## musichero.ai API

- **Create:** `POST https://api.musichero.ai/api/v1/suno/create`
  - Headers: `uniqueId` (uuid4 hex), `verify` (hCaptcha token)
  - Body: `{prompt, customMode}` or `{prompt, mv, title, customMode: true}`
- **Poll:** `GET https://api.musichero.ai/api/v1/suno/pageRecordList?pageNum=1`
  - Returns `data.records[0].status` → `"finished"` when done
  - Audio URL in `records[0].audioUrl`

## hCaptcha Notes

- **Sitekey:** `6520ce9c-a8b2-4cbe-b698-687e90448dec`
- **Rate limit:** IP-based, ~30 solves/30 min trigger 429. Normal use (few songs/hour) never hits it.
- **Bypass protocol:** MessagePack `[c_json_string, ExtType(code=18, n_token)]`; n_token ≈ 19 KB bundling HSW PoW + motion data + browser fingerprint.
- **Proxy fix:** Set `HCAPTCHA_PROXY=socks5://host:port` to route bypass traffic through a clean IP.
