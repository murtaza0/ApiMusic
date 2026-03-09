"""
app.py — anymusic.ai Music Generation Web App
-----------------------------------------------
Flask web server. No captcha required.
User pastes their anymusic.ai session cookie once and songs are generated.

Run:  python app.py
URL:  http://0.0.0.0:5000
"""
from __future__ import annotations

import threading
import uuid
from flask import Flask, render_template, request, jsonify, session as flask_session

import bot_core

app = Flask(__name__)
app.secret_key = "anymusic-session-key-2026"

# In-memory job store: job_id -> {status, logs, audio_url, error}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Stored cookie (server-side, shared across all requests for simplicity)
_saved_cookie: str = ""
_cookie_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_job_thread(job_id: str, data: dict, cookie: str) -> None:
    def log(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id]["logs"].append(msg)

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    try:
        ui_mode = data.get("mode", "custom")
        api_mode = "text-to-song" if ui_mode == "simple" else "lyrics-to-song"
        result = bot_core.run_job(
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
        with _jobs_lock:
            _jobs[job_id]["status"]     = result["status"]
            _jobs[job_id]["audio_url"]  = result["audio_url"]
            _jobs[job_id]["local_path"] = result.get("local_path")
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


@app.route("/api/set-cookie", methods=["POST"])
def api_set_cookie():
    global _saved_cookie
    data = request.get_json(force=True)
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
    global _saved_cookie
    data = request.get_json(force=True)

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    with _cookie_lock:
        cookie = _saved_cookie

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status":     "queued",
            "logs":       [],
            "audio_url":  None,
            "local_path": None,
            "error":      None,
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
