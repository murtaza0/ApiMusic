"""
app.py — anymusic.ai Music Generation Web App
-----------------------------------------------
Flask web server. No captcha required.
Browser makes API calls client-side → sends audio URL to backend → backend downloads + serves MP3.

Run:  python app.py
URL:  http://0.0.0.0:5000
"""
from __future__ import annotations

import os
import re
import time
import threading
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory

import bot_core

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "anymusic-session-key-2026")

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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
