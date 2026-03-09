================================================================
  ApiMusic — AI Music Generation API
  Base URL: https://apimusic-production-ef04.up.railway.app
================================================================

ENDPOINTS
---------
GET  /health              Server alive check
GET  /docs                Swagger UI (browser mein test karo)
POST /generate            Song generate karo
GET  /status/{task_id}    Status check / MP3 download


================================================================
MODE 1: TEXT-TO-SONG
================================================================

Request (POST /generate):
--------------------------
{
  "mode":   "text-to-song",
  "prompt": "romantic Urdu ghazal with soft piano",
  "genre":  "Pop"
}

Fields:
  mode    → "text-to-song" (default)
  prompt  → Song kaisa chahiye (REQUIRED)
  genre   → Pop, Classical, Hip-Hop, Rock, Jazz, R&B (default: Pop)


================================================================
MODE 2: LYRICS-TO-SONG
================================================================

Request (POST /generate):
--------------------------
{
  "mode":     "lyrics-to-song",
  "title":    "Meri Jaan",
  "lyrics":   "Tere bina jeena nahi\nTere bina chain nahi",
  "style":    "Pop",
  "mood":     "Sad",
  "scenario": "heartbreak"
}

Fields:
  mode      → "lyrics-to-song" (REQUIRED)
  title     → Song ka naam (default: AI Track)
  lyrics    → Aapki apni lyrics (REQUIRED)
  style     → Pop, Rock, Classical, Hip-Hop, R&B, Jazz (default: Pop)
  mood      → Happy, Sad, Romantic, Angry, Peaceful (default: Happy)
  scenario  → heartbreak, party, wedding, summer vibes, etc.


================================================================
RESPONSE (POST /generate)
================================================================

{
  "ok": true,
  "variants": [
    { "task_id": "ready_aHR0cHM6...", "status": "ready" },
    { "task_id": "abc123xyz",         "status": "pending" }
  ],
  "elapsed_seconds": 3.2
}

  ready   → Song turant tayyar, seedha /status/ se download karo
  pending → Song ban raha hai, polling karo


================================================================
STEP 2: STATUS CHECK / DOWNLOAD (GET /status/{task_id})
================================================================

GET https://apimusic-production-ef04.up.railway.app/status/{task_id}

  - "ready" task_id  → seedha audio/mpeg stream milega (MP3)
  - "pending" task_id → ya {"status":"pending"} ya MP3

RULE: Har 8 second baad retry karo jab tak MP3 na mile.
MAX WAIT: ~90-120 seconds


================================================================
WEBSITE INTEGRATION (JavaScript)
================================================================

async function generateSong(prompt, genre = "Pop") {
  const res = await fetch(
    "https://apimusic-production-ef04.up.railway.app/generate",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "text-to-song", prompt, genre })
    }
  );
  const data = await res.json();
  if (!data.ok) throw new Error(data.detail);

  for (const variant of data.variants) {
    const audioUrl = await pollUntilReady(variant.task_id);
    if (audioUrl) return audioUrl;
  }
}

async function pollUntilReady(taskId, maxWait = 120000) {
  const start = Date.now();
  while (Date.now() - start < maxWait) {
    const res = await fetch(
      "https://apimusic-production-ef04.up.railway.app/status/" + taskId
    );
    if (res.headers.get("content-type")?.includes("audio")) {
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    }
    await new Promise(r => setTimeout(r, 8000));
  }
  return null;
}

Usage:
  const audioUrl = await generateSong("upbeat Punjabi wedding song");
  document.getElementById("player").src = audioUrl;


================================================================
LYRICS-TO-SONG INTEGRATION (JavaScript)
================================================================

async function generateFromLyrics(title, lyrics, mood = "Happy") {
  const res = await fetch(
    "https://apimusic-production-ef04.up.railway.app/generate",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "lyrics-to-song",
        title,
        lyrics,
        style: "Pop",
        mood,
        scenario: "general"
      })
    }
  );
  const data = await res.json();
  for (const variant of data.variants) {
    const audioUrl = await pollUntilReady(variant.task_id);
    if (audioUrl) return audioUrl;
  }
}


================================================================
TIMELINE
================================================================

  0s      POST /generate bhejo
  1-5s    2 task_ids wapis aate hain
  5-90s   Song generate ho raha hai (har 8s baad /status/ poll karo)
  ~90s    MP3 ready — audio stream milega


================================================================
ERROR RESPONSES
================================================================

{ "ok": false, "detail": "Both variants failed: ..." }

  timed out (>270s)  → anymusic.ai server slow tha, retry karo
  Rate limited       → Thodi der baad try karo
  IP may be blocked  → Server IP issue


================================================================
SWAGGER UI (Browser Testing)
================================================================

  https://apimusic-production-ef04.up.railway.app/docs

  Wahan se directly browser mein saare endpoints test kar sakte ho
  bina koi code likhe.

================================================================
