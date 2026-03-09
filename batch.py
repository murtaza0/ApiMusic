"""
batch.py — Batch music generation from prompts.txt
----------------------------------------------------
Reads up to 5 prompts from prompts.txt (one per line) and generates a
song for each one sequentially, waiting for each to finish before starting
the next. MP3 files are saved to music_outputs/.

Usage:
  python batch.py

Set your anymusic.ai cookie via:
  - ANYMUSIC_COOKIE environment variable, OR
  - Enter it manually when prompted

prompts.txt format (one entry per line, # = comment):
  # lyrics-to-song (default)
  Your full lyrics text here  →  title::Your Song Title
  
  # text-to-song
  text-to-song::Upbeat summer pop with female vocals  →  genre::Pop
  
Simple format (just paste lyrics, no special syntax needed):
  Verse 1: City lights at midnight...
  Chorus: Dancing in the rain...
"""
from __future__ import annotations

import os
import re
import sys
import time

import bot_core

PROMPTS_FILE = "prompts.txt"
MAX_PROMPTS  = 5

# ---------------------------------------------------------------------------
# Cookie
# ---------------------------------------------------------------------------

def get_cookie() -> str:
    cookie = os.getenv("ANYMUSIC_COOKIE", "").strip()
    if cookie:
        print(f"[setup] Using ANYMUSIC_COOKIE from environment.")
        return cookie

    print()
    print("=" * 60)
    print("  anymusic.ai Batch Generator")
    print("=" * 60)
    print()
    print("Paste your anymusic.ai Cookie header value below.")
    print("(DevTools → Network → any request → Cookie header)")
    print("Leave empty to skip authentication (may fail).\n")
    try:
        val = input("Cookie > ").strip()
    except (EOFError, KeyboardInterrupt):
        val = ""
    return val


# ---------------------------------------------------------------------------
# Prompt parser
# ---------------------------------------------------------------------------

def parse_prompts(path: str) -> list[dict]:
    """
    Parse prompts.txt into a list of job configs.

    Supported syntax per non-blank, non-comment line:
      text-to-song::<description>  [→  genre::<genre>]
      <lyrics>  [→  title::<title>]

    Multi-line lyrics: lines are joined until a blank separator or max 5 entries.
    """
    if not os.path.exists(path):
        print(f"[batch] '{path}' not found. Creating a sample file ...")
        _write_sample(path)
        print(f"[batch] Edit '{path}' with your prompts and re-run.\n")
        sys.exit(0)

    with open(path, encoding="utf-8") as f:
        raw = f.read()

    blocks = re.split(r"\n\s*---\s*\n", raw.strip())
    jobs = []

    for block in blocks:
        lines = [l.rstrip() for l in block.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not lines:
            continue
        if len(jobs) >= MAX_PROMPTS:
            break

        text = "\n".join(lines)

        title    = "AI_Track"
        mode     = "lyrics-to-song"
        genre    = "Classical"
        style    = "Classical"
        mood     = "Romantic"
        scenario = "Urban romance"

        if "→" in text:
            main_part, meta_part = text.split("→", 1)
            main_part = main_part.strip()
            for seg in meta_part.split(","):
                seg = seg.strip()
                if "::" in seg:
                    k, v = seg.split("::", 1)
                    k, v = k.strip().lower(), v.strip()
                    if k == "title":    title    = v
                    elif k == "genre":  genre    = v
                    elif k == "style":  style    = v
                    elif k == "mood":   mood     = v
                    elif k == "scenario": scenario = v
        else:
            main_part = text.strip()

        if main_part.lower().startswith("text-to-song::"):
            mode = "text-to-song"
            main_part = main_part[len("text-to-song::"):].strip()

        jobs.append({
            "mode":     mode,
            "prompt":   main_part,
            "title":    title,
            "genre":    genre,
            "style":    style,
            "mood":     mood,
            "scenario": scenario,
        })

    return jobs


def _write_sample(path: str) -> None:
    sample = """\
# anymusic.ai Batch Prompts
# --------------------------
# Separate each song with  ---  on its own line.
# Maximum 5 songs per run.
#
# Lyrics mode (default): write your full lyrics.
#   Add  → title::Your Title  on the last line for a custom title.
#
# Text-to-song mode: start with  text-to-song::
#   Add  → genre::Pop  for genre control.

Verse 1:
City lights are shining bright tonight
I'll wait for you until the morning light

Chorus:
Eternal love will always be
The thing that sets our hearts free → title::Eternal Love

---

Verse 1:
Walking down the empty street
Raindrops falling at my feet

Chorus:
Missing you like crazy tonight
Nothing feels the same without your light → title::Missing You

---

text-to-song::Upbeat electronic pop with driving synths and female vocalist → genre::Electronic

---

text-to-song::Smooth jazz lounge with piano and saxophone, late night vibes → genre::Jazz

---

text-to-song::Epic orchestral cinematic score building to a powerful climax → genre::Classical
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(sample)


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(jobs: list[dict], cookie: str) -> None:
    total = len(jobs)
    print(f"\n[batch] Starting {total} job(s) → output: {bot_core.OUTPUT_DIR}/\n")
    bot_core.ensure_output_dir()

    results = []

    for idx, job in enumerate(jobs, 1):
        print("=" * 60)
        print(f"  [{idx}/{total}]  {job['mode']}  |  {job['prompt'][:70]}")
        print("=" * 60)

        def log(msg: str, i=idx) -> None:
            print(f"  [{i}] {msg}")

        result = bot_core.run_job(
            cookie=cookie,
            mode=job["mode"],
            prompt=job["prompt"],
            genre=job["genre"],
            title=job["title"],
            lyrics=job["prompt"] if job["mode"] == "lyrics-to-song" else "",
            style=job["style"],
            mood=job["mood"],
            scenario=job["scenario"],
            auto_download=True,
            log=log,
        )

        results.append({
            "idx":        idx,
            "title":      job["title"],
            "status":     result["status"],
            "audio_url":  result["audio_url"],
            "local_path": result["local_path"],
        })

        tag = "OK  " if result["status"] == "ok" else "FAIL"
        url = result["audio_url"] or "—"
        loc = result["local_path"] or "—"
        print(f"\n  [{tag}]  URL  : {url}")
        print(f"         File : {loc}\n")

        if idx < total:
            delay = 5
            print(f"  [delay] Waiting {delay}s before next job ...\n")
            time.sleep(delay)

    print("=" * 60)
    print("  BATCH SUMMARY")
    print("=" * 60)
    for r in results:
        tag = "OK  " if r["status"] == "ok" else "FAIL"
        print(f"  [{tag}] #{r['idx']:>2} {r['title'][:30]:<30}  {r['local_path'] or r['audio_url'] or r['status']}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cookie = get_cookie()
    jobs   = parse_prompts(PROMPTS_FILE)

    if not jobs:
        print(f"[batch] No prompts found in '{PROMPTS_FILE}'. Exiting.")
        sys.exit(0)

    print(f"\n[batch] Loaded {len(jobs)} prompt(s) from '{PROMPTS_FILE}'")
    for i, j in enumerate(jobs, 1):
        print(f"  {i}. [{j['mode']}] {j['prompt'][:60]}")

    print()
    run_batch(jobs, cookie)
