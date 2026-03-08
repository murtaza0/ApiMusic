"""
app.py — musichero.ai Music Generation Web App
------------------------------------------------
Flask web server. Captcha is solved automatically in the background
using the headless browser bypass engine — users just fill in song
details and click Generate.

Run:  python app.py
URL:  http://0.0.0.0:5000
"""
from __future__ import annotations

import os
import threading
import uuid
from flask import Flask, render_template, request, jsonify

import bot_core
from hcaptcha_bypass import get_hcaptcha_token_bypass

HCAPTCHA_SITEKEY = "6520ce9c-a8b2-4cbe-b698-687e90448dec"
TARGET_SITE      = "https://musichero.ai"

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

    # Step 1 — Solve hCaptcha automatically
    log("[captcha] Solving hCaptcha automatically ...")
    try:
        token = get_hcaptcha_token_bypass(
            sitekey=HCAPTCHA_SITEKEY,
            host=TARGET_SITE,
            max_retries=3,
        )
        log(f"[captcha] Token obtained ({len(token)} chars) ✓")
    except RuntimeError as exc:
        msg = str(exc)
        if "rate_limited" in msg:
            log("[captcha] IP rate limited — please wait 1-2 hours and try again.")
            log("          Or add a HCAPTCHA_PROXY secret to bypass the limit.")
        else:
            log(f"[captcha] Failed to solve captcha: {msg}")
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"]  = "Captcha solve failed. " + msg
        return

    # Step 2 — Generate song
    try:
        result = bot_core.run_job(
            captcha_token=token,
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
