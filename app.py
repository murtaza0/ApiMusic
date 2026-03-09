"""
app.py — anymusic.ai Music Generation Web App + REST API
---------------------------------------------------------
Flask web server. No captcha required.
Browser makes API calls client-side → sends audio URL to backend → backend downloads + serves MP3.

Public REST API:
  POST /v1/generate          — start async generation (returns job_id)
  GET  /v1/jobs/<id>         — poll job status + results
  GET  /v1/health            — server health check

Auth (optional): set API_KEY env var → send  Authorization: Bearer <key>

Run:  python app.py
URL:  http://0.0.0.0:5000
"""
from __future__ import annotations

import os
import re
import time
import threading
import uuid
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_from_directory

import bot_core

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "anymusic-session-key-2026")

# Optional API key — if set, all /v1/ routes require  Authorization: Bearer <key>
_API_KEY: str = os.environ.get("API_KEY", "").strip()

# In-memory job store: job_id -> {status, logs, audio_url, local_url, error}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Stored manual cookie override (optional)
_saved_cookie: str = ""
_cookie_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Background worker (used by CLI / batch — not the web UI flow)
# ---------------------------------------------------------------------------

def _run_job_thread(job_id: str, data: dict, cookie: str) -> None:
    def log(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id]["logs"].append(msg)

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    try:
        ui_mode  = data.get("mode", "custom")
        api_mode = "text-to-song" if ui_mode == "simple" else "lyrics-to-song"
        result   = bot_core.run_job(
            cookie=cookie,
            mode=api_mode,
            prompt=data.get("prompt", ""),
            genre=data.get("genre", "Classical"),
            title=data.get("title", "AI_Track"),
            lyrics=data.get("lyrics", ""),
            style=data.get("style", "Classical"),
            mood=data.get("mood", "Romantic"),
            scenario=data.get("scenario", "Urban romance"),
            auto_download=True,
            log=log,
        )
        local_url = None
        if result.get("local_path"):
            fn = os.path.basename(result["local_path"])
            local_url = f"/audio/{fn}"

        with _jobs_lock:
            _jobs[job_id]["status"]    = result["status"]
            _jobs[job_id]["audio_url"] = result["audio_url"]
            _jobs[job_id]["local_url"] = local_url
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"]  = str(exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    with _cookie_lock:
        manual_cookie = bool(_saved_cookie)
    return render_template("index.html",
                           manual_cookie=manual_cookie,
                           genres=bot_core.GENRES,
                           styles=bot_core.STYLES,
                           moods=bot_core.MOODS,
                           scenarios=bot_core.SCENARIOS)


@app.route("/audio/<filename>")
def serve_audio(filename: str):
    """Serve downloaded MP3 files from music_outputs/."""
    safe = re.sub(r"[^a-zA-Z0-9_.\-]", "", filename)
    return send_from_directory(
        os.path.abspath(bot_core.OUTPUT_DIR),
        safe,
        mimetype="audio/mpeg",
    )


@app.route("/api/set-cookie", methods=["POST"])
def api_set_cookie():
    global _saved_cookie
    data   = request.get_json(force=True)
    cookie = (data.get("cookie") or "").strip()
    with _cookie_lock:
        _saved_cookie = cookie
    return jsonify({"ok": True, "has_cookie": bool(cookie)})


@app.route("/api/cookie-status")
def api_cookie_status():
    with _cookie_lock:
        return jsonify({"has_cookie": bool(_saved_cookie)})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Server-side generation (used by batch/CLI — not the web UI)."""
    global _saved_cookie
    data   = request.get_json(force=True)
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    with _cookie_lock:
        cookie = _saved_cookie

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status":    "queued",
            "logs":      [],
            "audio_url": None,
            "local_url": None,
            "error":     None,
        }

    t = threading.Thread(target=_run_job_thread, args=(job_id, data, cookie), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/notify", methods=["POST"])
def api_notify():
    """
    Called by the browser when client-side generation completes.
    Downloads the MP3 to music_outputs/ synchronously and returns the local URL.
    Frontend then switches the audio player to our server URL.
    """
    data      = request.get_json(force=True)
    task_id   = (data.get("task_id") or "").strip()
    audio_url = (data.get("audio_url") or "").strip()

    if not audio_url:
        return jsonify({"ok": False, "error": "No audio_url provided"}), 400

    bot_core.ensure_output_dir()
    safe = re.sub(r"[^a-z0-9]+", "_", task_id[:24].lower()) if task_id else "track"
    filename  = f"{safe}_{int(time.time())}.mp3"

    local_path = bot_core.download_audio(audio_url, filename=filename)

    if local_path and os.path.exists(local_path):
        local_url = f"/audio/{os.path.basename(local_path)}"
        return jsonify({"ok": True, "local_url": local_url, "filename": filename})

    # Download failed — frontend can keep using the original CDN URL
    return jsonify({"ok": False, "error": "Download failed", "local_url": None})


# ---------------------------------------------------------------------------
# REST API — /v1/
# ---------------------------------------------------------------------------

def _require_api_key(f):
    """Decorator: if API_KEY is set, enforce  Authorization: Bearer <key>."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if _API_KEY:
            auth = request.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if token != _API_KEY:
                return jsonify({"error": "Unauthorized — invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return wrapper


def _v1_run_job_thread(job_id: str, params: dict) -> None:
    """Background thread for /v1/generate — runs full generate+poll+download pipeline."""
    def log(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id]["logs"].append(msg)

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    try:
        result = bot_core.run_job(
            cookie      = params.get("cookie", ""),
            mode        = params.get("mode", "text-to-song"),
            prompt      = params.get("prompt", ""),
            genre       = params.get("genre", "Pop"),
            title       = params.get("title", "AI_Track"),
            lyrics      = params.get("lyrics", ""),
            style       = params.get("style", "Pop"),
            mood        = params.get("mood", "Happy"),
            scenario    = params.get("scenario", "Summer vibes"),
            quantity    = int(params.get("quantity", 2)),
            auto_download = True,
            log         = log,
        )

        variants = []
        for i, v in enumerate(result.get("variants", [])):
            local_path = v.get("local_path")
            filename   = os.path.basename(local_path) if local_path else None
            variants.append({
                "index":      i + 1,
                "filename":   filename,
                "local_path": local_path,
                "download_url": f"/v1/download/{job_id}/{i + 1}" if filename else None,
            })

        with _jobs_lock:
            _jobs[job_id]["status"]   = result["status"]
            _jobs[job_id]["variants"] = variants

    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"]  = str(exc)


@app.route("/v1/health")
def v1_health():
    """
    GET /v1/health
    Returns server status and version info.
    """
    return jsonify({
        "ok":      True,
        "service": "anymusic-api",
        "version": "1.0.0",
        "auth":    bool(_API_KEY),
    })


@app.route("/v1/generate", methods=["POST"])
@_require_api_key
def v1_generate():
    """
    POST /v1/generate
    Start an async music generation job. Returns job_id immediately.
    Poll GET /v1/jobs/<job_id> for status and results.

    Body (JSON):
      prompt    string   required  Song description or lyrics
      mode      string   optional  "text-to-song" (default) | "lyrics-to-song"
      genre     string   optional  e.g. "Pop", "Qawwali", "Punjabi Folk"
      quantity  int      optional  Number of variants (default: 2)
      title     string   optional  Song title (for lyrics mode)
      lyrics    string   optional  Full lyrics (for lyrics mode)
      style     string   optional  Music style
      mood      string   optional  Mood
      scenario  string   optional  Scenario / vibe

    Response:
      { "ok": true, "job_id": "...", "status": "queued", "poll_url": "/v1/jobs/..." }
    """
    data   = request.get_json(force=True, silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "prompt is required"}), 400

    quantity = int(data.get("quantity", 2))
    if quantity < 1 or quantity > 4:
        return jsonify({"ok": False, "error": "quantity must be 1–4"}), 400

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status":    "queued",
            "logs":      [],
            "variants":  [],
            "audio_url": None,
            "local_url": None,
            "error":     None,
        }

    params = {
        "prompt":   prompt,
        "mode":     data.get("mode", "text-to-song"),
        "genre":    data.get("genre", "Pop"),
        "title":    data.get("title", "AI_Track"),
        "lyrics":   data.get("lyrics", ""),
        "style":    data.get("style", "Pop"),
        "mood":     data.get("mood", "Happy"),
        "scenario": data.get("scenario", "Summer vibes"),
        "quantity": quantity,
        "cookie":   data.get("cookie", ""),
    }

    t = threading.Thread(target=_v1_run_job_thread, args=(job_id, params), daemon=True)
    t.start()

    return jsonify({
        "ok":       True,
        "job_id":   job_id,
        "status":   "queued",
        "poll_url": f"/v1/jobs/{job_id}",
    }), 202


@app.route("/v1/jobs/<job_id>")
@_require_api_key
def v1_job_status(job_id: str):
    """
    GET /v1/jobs/<job_id>
    Poll until done=true. Each variant has a download_url for the actual MP3 file.

    Response fields:
      status        "queued" | "running" | "ok" | "failed" | "timeout" | "error"
      done          true when generation is complete
      variants      [{ "index": 1, "filename": "...", "download_url": "/v1/download/..." }, ...]
      logs          generation log lines
      error         error message (only on failure)
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "job not found"}), 404

    done = job["status"] in ("ok", "failed", "timeout", "error")

    # Strip local_path from response — expose only the download_url
    variants = [
        {
            "index":        v.get("index"),
            "filename":     v.get("filename"),
            "download_url": v.get("download_url"),
        }
        for v in job.get("variants", [])
    ]

    return jsonify({
        "ok":       True,
        "job_id":   job_id,
        "status":   job["status"],
        "done":     done,
        "variants": variants,
        "logs":     job.get("logs", []),
        "error":    job.get("error"),
    })


@app.route("/v1/download/<job_id>/<int:variant_n>")
@_require_api_key
def v1_download(job_id: str, variant_n: int):
    """
    GET /v1/download/<job_id>/<variant_n>
    Stream the generated MP3 audio file directly (Content-Type: audio/mpeg).
    variant_n is 1-based (1 = first variant, 2 = second, etc.)
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "job not found"}), 404
    if job["status"] != "ok":
        return jsonify({"ok": False, "error": f"job not ready (status: {job['status']})"}), 409

    variants = job.get("variants", [])
    # Find variant by index
    variant = next((v for v in variants if v.get("index") == variant_n), None)
    if not variant or not variant.get("local_path"):
        return jsonify({"ok": False, "error": f"variant {variant_n} not found"}), 404

    local_path = variant["local_path"]
    if not os.path.exists(local_path):
        return jsonify({"ok": False, "error": "audio file missing on server"}), 404

    return send_from_directory(
        os.path.abspath(bot_core.OUTPUT_DIR),
        os.path.basename(local_path),
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=variant.get("filename", "song.mp3"),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
