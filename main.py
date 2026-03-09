"""
main.py — anymusic.ai CLI
--------------------------
Simple command-line runner for anymusic.ai generation.
For the full web interface, run app.py instead.

Usage:
  python main.py

Set your cookie string in the ANYMUSIC_COOKIE environment variable,
or it will prompt you to paste it.
"""
import os
import bot_core

COOKIE = os.getenv("ANYMUSIC_COOKIE", "")


def main():
    print("=" * 60)
    print("  anymusic.ai Music Generation Bot  (CLI)")
    print("=" * 60)

    cookie = COOKIE
    if not cookie:
        print("[setup] No ANYMUSIC_COOKIE set — cookies will be auto-generated.")

    songs = [
        {
            "mode":  "simple",
            "prompt": "An upbeat electronic pop song about chasing dreams at night",
            "genre": "Electronic",
        },
    ]

    for idx, song in enumerate(songs, 1):
        print(f"\nSong {idx}/{len(songs)} — {song['prompt'][:60]}")
        result = bot_core.run_job(
            cookie=cookie,
            mode=song.get("mode", "lyrics-to-song"),
            prompt=song.get("prompt", ""),
            genre=song.get("genre", "Classical"),
            title=song.get("title", "AI_Track"),
            lyrics=song.get("prompt", ""),
            auto_download=True,
        )
        if result["status"] == "ok":
            print(f"[OK] Audio URL : {result['audio_url']}")
            print(f"[OK] Local file: {result['local_path']}")
        else:
            print(f"[FAIL] Status: {result['status']}")


if __name__ == "__main__":
    main()
