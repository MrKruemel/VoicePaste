# Voice Paste API -- Skill Reference

> Give this document to any AI agent (e.g. Claude Cloud, Claude Code, custom scripts) so it can speak to the user and control Voice Paste via the local HTTP API.

## Quick start

Voice Paste exposes a **localhost-only REST API** on `http://127.0.0.1:18923`.
The API must be enabled in Settings > General > HTTP API (disabled by default).

```bash
# Health check
curl http://127.0.0.1:18923/health

# Speak text aloud
curl -X POST http://127.0.0.1:18923/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Build completed successfully."}'
```

---

## Endpoints

| Method | Path             | Purpose                                |
|--------|------------------|----------------------------------------|
| GET    | `/health`        | Health check (always 200 when running) |
| GET    | `/status`        | Current app state + version info       |
| POST   | `/tts`           | Speak text via TTS (default) or stream as audio bytes |
| POST   | `/stt`           | Transcribe raw audio bytes to text     |
| POST   | `/stop`          | Stop TTS playback                      |
| POST   | `/record/start`  | Start microphone recording             |
| POST   | `/record/stop`   | Stop recording, trigger STT pipeline   |
| POST   | `/cancel`        | Cancel current operation               |

### GET /health

Returns `{"status": "ok"}`. Use this to check if Voice Paste is running.

### GET /status

```json
{
  "status": "ok",
  "data": {
    "state": "idle",
    "tts_enabled": true,
    "api_version": "1",
    "app_version": "0.9.0"
  }
}
```

Possible `state` values: `idle`, `recording`, `processing`, `speaking`, `pasting`, `awaiting_paste`.

### POST /tts

Two modes: **default** (speak on user's speakers, fire-and-forget) or **stream** (return raw audio bytes, no playback).

**Request body:**
```json
{
  "text": "The deployment finished with 0 errors.",
  "voice": "de_DE-thorsten_emotional-medium",
  "stream": false
}
```

| Field    | Type    | Required | Constraint                                              |
|----------|---------|----------|---------------------------------------------------------|
| `text`   | string  | yes      | Max 10,000 chars                                        |
| `voice`  | string  | no       | Override for this call. Must be a known Piper voice ID (see `src/constants.py:PIPER_VOICE_MODELS`). Falls back to `config.tts_local_voice` if absent. Only meaningful with the Piper provider. |
| `stream` | boolean | no       | If `true`, response is audio bytes instead of JSON. Equivalent triggers: `?stream=ogg` query param, or `Accept: audio/*` header. **In stream mode the audio is NOT played on the speakers** — to avoid Tim hearing his own assistant twice when a remote consumer (e.g. PURR's Telegram-voice-out path) renders it elsewhere. |

**Default-mode response:** `{"status": "ok"}` (audio plays in background).

**Stream-mode response:** raw audio bytes, `Content-Type: audio/wav` (Piper produces WAV natively — no transcoding to OGG/Opus inside VoicePaste). Consumers that need OGG (e.g. Telegram-`send_voice`) should convert client-side (`pydub`, `ffmpeg`, `pyav`).

**Responses:**

| Status | Meaning                                |
|--------|----------------------------------------|
| 200    | TTS started (default) or bytes returned (stream) |
| 400    | Missing/empty `text`, or unknown `voice`         |
| 409    | Busy (another operation in progress)             |
| 413    | Text exceeds 10,000 character limit              |
| 503    | TTS not enabled or configured                    |

### POST /stt

Transcribe raw audio bytes via the local Whisper backend (`LocalWhisperSTT`, faster-whisper).

**Request body:** raw audio bytes (`Content-Type` should be set, e.g. `audio/wav`, `audio/ogg`, `audio/x-opus+ogg`). Max 25 MB.

**Query params:**

| Param      | Type   | Default | Purpose                                       |
|------------|--------|---------|-----------------------------------------------|
| `language` | string | `de`    | Whisper-recognized language code (`de`, `en`, …). |

**Response:**
```json
{
  "status": "ok",
  "transcript": "Hier spricht Cati",
  "language": "de"
}
```

**Responses:**

| Status | Meaning                                                                            |
|--------|------------------------------------------------------------------------------------|
| 200    | Transcript ready                                                                   |
| 400    | Empty body                                                                         |
| 409    | Busy (another operation in progress)                                               |
| 413    | Body > 25 MB                                                                       |
| 503    | STT backend not configured (faster-whisper missing) or audio format not decodable in this build (see Caveats below) |

**Caveats:**

- **WAV input is the fast path** — read directly into faster-whisper.
- **OGG/Opus/MP3/etc.** require PyAV for decoding. In the **frozen Linux binary build** PyAV is stubbed out (`rthook_av_stub.py`, saves ~119 MB) so non-WAV input returns 503 `STT_NOT_CONFIGURED`. In source-mode (`python src/main.py`) PyAV is available and OGG works.
  - **For PURR's Telegram-voice-in pipeline**: convert OGG/Opus to WAV client-side (PURR already has PyAV via its `.venv`) and POST the WAV — that's the recommended path.
  - Alternative: rebuild VoicePaste without the PyAV stub (`voice_paste_linux.spec`, remove the rthook), at the cost of ~+119 MB binary size.
- **First call loads the Whisper model** — for `base` this takes 2–4 s on the first hit; subsequent calls are warm. If you're calling from a context with a tight timeout (e.g. a Telegram-bot handler), consider pre-warming with one dummy POST during your service startup.

### POST /stop

Stop any currently playing TTS audio. Always returns `{"status": "ok"}`.

### POST /record/start

Start microphone recording. Optional `mode` field:

```json
{
  "mode": "summary"
}
```

- `"summary"` (default): Record -> Transcribe -> Summarize -> Paste
- `"prompt"`: Record -> Transcribe -> Send as LLM prompt -> Paste answer

Returns 409 if not idle.

### POST /record/stop

Stop recording and trigger the STT/summarization pipeline. Returns 409 if not currently recording.

### POST /cancel

Cancel whatever is currently happening (recording, TTS, awaiting paste). Always returns `{"status": "ok"}`.

---

## Error format

All errors follow this structure:

```json
{
  "status": "error",
  "error_code": "INVALID_PARAMS",
  "message": "Human-readable description"
}
```

Error codes: `NOT_FOUND`, `INVALID_PARAMS`, `TEXT_TOO_LONG`, `TTS_NOT_CONFIGURED`, `STT_NOT_CONFIGURED`, `BUSY`, `RATE_LIMITED`.

---

## Rate limiting

5 requests per second. Exceeding this returns HTTP 429.

---

## Behavior guidelines for AI agents

When using this API as a skill, follow these rules:

### 1. Always echo spoken text in your written output

The user may not be within hearing range. Whenever you call `/tts`, **always include the spoken text in your written response** so the user can read it too. Format it clearly:

```
I've spoken the following aloud:

> "Build completed successfully. 3 tests passed, 0 failed."
```

### 2. Keep TTS text concise and natural

- Write TTS text as **spoken language**, not written prose. Short sentences, no markdown, no code blocks.
- Summarize results instead of reading raw output. Say "Build finished in 45 seconds, all tests passed" not the full build log.
- Keep it under 2-3 sentences for status updates. Use longer text only when the user explicitly asks for a detailed readout.
- Use the user's language (German if they write in German, English if they write in English).

### 3. Provide a summary, not the full output

When reporting results of a build, test run, or similar operation:

- **Written response**: Show the full details (logs, errors, file paths) as normal.
- **TTS**: Speak only a **brief summary** of the outcome.

Example:

```
Build completed to dist2/VoicePaste.exe (280 MB).

I've spoken the following aloud:

> "Der Build ist fertig. VoicePaste.exe, 280 Megabyte, liegt in dist2."
```

### 4. Check availability before calling TTS

Before calling `/tts`, check `/status` to confirm the app is `idle` and `tts_enabled` is `true`. If the state is not idle, wait or skip the TTS call.

### 5. Don't block on TTS

`/tts` is fire-and-forget. Do **not** poll `/status` waiting for TTS to finish unless you need to send another TTS message after the first one completes.

If you need to chain multiple TTS messages, poll `/status` until `state` returns to `idle` before sending the next one.

### 6. Handle errors gracefully

If `/tts` returns an error (503, 409, etc.), fall back to written-only output. Never retry more than once. If Voice Paste is not running (`/health` fails), just skip TTS entirely and inform the user in text.

---

## Example: complete agent workflow

```
1. User asks: "Build the project and let me know when it's done"
2. Agent runs the build command
3. Build finishes (success or failure)
4. Agent calls:  GET /status  -> confirms idle + tts_enabled
5. Agent calls:  POST /tts    -> {"text": "Build erfolgreich. 280 MB, keine Fehler."}
6. Agent writes to chat:

   Build completed successfully.
   - Output: dist2/VoicePaste.exe (280 MB)
   - Duration: 2m 34s
   - Warnings: 3 (non-critical)
   - Errors: 0

   I've spoken the following aloud:

   > "Build erfolgreich. 280 MB, keine Fehler."
```

---

## Connection details

| Parameter     | Value                          |
|---------------|--------------------------------|
| Host          | `127.0.0.1` (localhost only)   |
| Default port  | `18923`                        |
| Protocol      | HTTP (no TLS, localhost only)  |
| Content-Type  | `application/json`             |
| Auth          | None (localhost-only security) |
