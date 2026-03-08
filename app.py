"""
app.py — musichero.ai Music Generation Web App
------------------------------------------------
Flask web server. The hCaptcha widget is shown to the user in the
browser when they click Generate. On solve, the token is sent to
the backend along with the song data and generation begins.

Run:  python app.py
URL:  http://0.0.0.0:5000
"""
from __future__ import annotations

import threading
import uuid
from flask import Flask, render_template, request, jsonify

import bot_core

app = Flask(__name__)

# In-memory job store: job_id -> {status, logs, audio_url, error}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_job_thread(job_id: str, data: dict) -> None:
    def log(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id]["logs"].append(msg)

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    try:
        result = bot_core.run_job(
            captcha_token=data["captcha_token"],
            mode=data.get("mode", "simple"),
            prompt=data.get("prompt", ""),
            title=data.get("title", "Untitled"),
            lyrics=data.get("lyrics", ""),
            log=log,
        )
        with _jobs_lock:
            _jobs[job_id]["status"]    = result["status"]
            _jobs[job_id]["audio_url"] = result["audio_url"]
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"]  = str(exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)

    token = (data.get("captcha_token") or "").strip()
    if len(token) < 20:
        return jsonify({"error": "Captcha token missing or invalid."}), 400

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status":    "queued",
            "logs":      [],
            "audio_url": None,
            "error":     None,
        }

    t = threading.Thread(target=_run_job_thread, args=(job_id, data), daemon=True)
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
