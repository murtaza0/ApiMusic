# musichero.ai Music Generation Bot

A Python automation bot for generating music via the musichero.ai API with hCaptcha bypass support.

## Architecture

Single-file script: `main.py`

### Key components
- **`get_hcaptcha_token()`** — Solves hCaptcha via a third-party provider before every POST request
- **`create_song()`** — POSTs to `/api/v1/suno/create` with captcha token and uniqueId headers
- **`poll_for_result()`** — GETs `/api/v1/suno/pageRecordList` every 10s until `status == "finished"`
- **`run_generation_loop()`** — Processes a list of song configs using a shared `requests.Session`

## Configuration

### Required Secrets (Replit Secrets)
| Secret | Description |
|--------|-------------|
| `CAPTCHA_API_KEY` | API key for your captcha solving service |
| `CAPTCHA_PROVIDER` | Provider name: `2captcha`, `capsolver`, or `anticaptcha` (default: `2captcha`) |

### Supported Captcha Providers
- **2captcha** (`2captcha`) — http://2captcha.com
- **CapSolver** (`capsolver`) — https://capsolver.com
- **Anti-Captcha** (`anticaptcha`) — https://anti-captcha.com

## Usage

Edit the `song_queue` list at the bottom of `main.py` to define your songs, then run the **Run Bot** workflow.

### Simple mode
```python
{"mode": "simple", "prompt": "An upbeat pop song about summer"}
```

### Custom mode
```python
{
    "mode":   "custom",
    "prompt": "Acoustic folk with warm guitar",  # style
    "title":  "My Song Title",
    "lyrics": "[Verse 1]\nYour lyrics here..."
}
```

## API Endpoints
- **Create:** `POST https://api.musichero.ai/api/v1/suno/create`
- **Poll:**   `GET  https://api.musichero.ai/api/v1/suno/pageRecordList?pageNum=1`
- **Site key:** `6520ce9c-a8b2-4cbe-b698-687e90448dec`

## Dependencies
- `requests` (pip)
