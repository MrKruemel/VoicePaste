---
name: say
description: "Speak text aloud via the Voice Paste local TTS API. Trigger this skill on ANY greeting (hi, hallo, moin, servus, hey, etc.), after ANY significant build/test/deploy completes, or when the user types /say. Also trigger when the user asks Claude to 'say something', 'read this aloud', 'announce', or 'tell me out loud'. If Voice Paste isn't running, skip TTS silently — never let a failed TTS check block or delay a response."
allowed-tools: Bash, Read
---

# Voice Paste TTS Skill

You can speak text aloud to the user via the Voice Paste local HTTP API. This adds a voice layer on top of your normal written responses — think of it as a friendly audio notification, not a replacement for text.

## API at a glance

Voice Paste exposes a localhost-only REST API. No auth required.

| Detail | Value |
|--------|-------|
| Base URL | `http://127.0.0.1:18923` |
| Protocol | HTTP (no TLS, localhost only) |
| Content-Type | `application/json` |
| Rate limit | 5 requests/second (HTTP 429 if exceeded) |

### Endpoints used by this skill

**GET /status** — Check if TTS is available.

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

**POST /tts** — Speak text aloud. Fire-and-forget: returns 200 immediately, audio plays async.

Request: `{"text": "Your text here"}` (max 10,000 chars)

| Status | Meaning |
|--------|---------|
| 200 | TTS started |
| 400 | Missing or empty `text` |
| 409 | Busy (another operation in progress) |
| 413 | Text exceeds 10,000 characters |
| 503 | TTS not enabled or not configured |

**POST /stop** — Stop currently playing TTS audio. Always returns `{"status": "ok"}`.

**POST /cancel** — Cancel any current operation. Always returns `{"status": "ok"}`.

### Error format

All errors return:
```json
{
  "status": "error",
  "error_code": "INVALID_PARAMS",
  "message": "Human-readable description"
}
```

Error codes: `NOT_FOUND`, `INVALID_PARAMS`, `TEXT_TOO_LONG`, `TTS_NOT_CONFIGURED`, `RATE_LIMITED`.

---

## How to speak text

### Step 1: Check availability

```bash
curl -s http://127.0.0.1:18923/status
```

TTS is available when BOTH conditions are true:
- `state` is `idle`
- `tts_enabled` is `true`

**If any of these fail, skip TTS entirely and respond in text only:**
- Connection refused → Voice Paste isn't running
- `state` is anything other than `idle` (e.g. `speaking`, `processing`) → it's busy
- `tts_enabled` is `false` → user has TTS disabled

Never mention the failure. Never apologize. Just respond normally in text.

### Step 2: Prepare the text for speech

Before sending to TTS, make the text *sound* good when spoken aloud:

- **Strip URLs**: Replace `https://github.com/user/repo` with "the GitHub repo" or just omit
- **Humanize identifiers**: `CVE-2024-1234` → "a CVE from 2024", `JIRA-4521` → "Jira ticket forty-five twenty-one"
- **No file paths**: Replace `/home/user/.config/app/settings.json` with "the settings file"
- **No markdown**: No asterisks, backticks, headers, or bullet formatting
- **No code**: Don't read code aloud — describe what it does instead
- **Short sentences**: TTS sounds best with simple, conversational phrasing

The written echo (Step 4) preserves all the original detail — the TTS version is a friendly summary for the ears.

### Step 3: Send TTS

Build the JSON payload carefully. The text **will** sometimes contain characters that break JSON — especially double quotes and backslashes.

```bash
curl -s -X POST http://127.0.0.1:18923/tts \
  -H "Content-Type: application/json" \
  --data-raw "{\"text\": \"ESCAPED_TEXT_HERE\"}"
```

**Escaping rules for the JSON string value:**
- `"` → `\"`
- `\` → `\\`
- Newlines → `\n` (or just remove them — TTS ignores line breaks anyway)
- Tabs → remove or replace with a space

**Important**: On Windows/Git-Bash, always use `--data-raw` with escaped double quotes. Single-quoted JSON (`-d '{...}'`) causes `INVALID_PARAMS` errors on Windows.

### Step 4: Echo in written output

Always include the spoken text in your written response so the user can see what was said even if they didn't hear it. But keep the echo natural — don't use the same phrasing every time.

Good variations:
- > 🔊 *"Build finished, no errors!"*
- > Spoken: "Hallo! Wie kann ich helfen?"
- > I said aloud: "Three tests passed, one skipped."

Bad (robotic repetition):
- ❌ Always writing "I've spoken the following aloud:" — vary it

---

## Trigger: Manual `/say`

If the user types `/say some text`, speak the text via TTS.

- `/say some text` → speak "some text"
- `/say` (no arguments) → ask the user what they'd like you to say. Don't call TTS.

## Trigger: Greetings

Greet back via TTS when the user opens with a greeting. Recognize these broadly — any casual hello-type opener counts:

**German**: hallo, hi, hey, moin, moinsen, servus, grüß gott, gude, tach, na, mahlzeit, guten morgen, guten tag, guten abend
**English**: hello, hi, hey, yo, good morning, morning, what's up, howdy, sup
**Other**: Recognize greetings in other languages too — if it's clearly a greeting, trigger TTS.

Flow:
1. Check `/status`
2. If available → speak a short, warm greeting (1 sentence) in the appropriate TTS language
3. Write a normal greeting in the chat language

Example: User says "Servus!" → TTS: "Servus! Schön, dass du da bist." → Written: normal German greeting.

## Trigger: After significant operations

When a meaningful task finishes — a build, a test suite, a deployment, a long-running script — speak a brief result summary via TTS.

**What counts as "significant"** (trigger TTS):
- Build/compile completing (webpack, cargo, gcc, make, etc.)
- Test suite finishing (pytest, jest, cargo test, etc.)
- Deployment or publish completing
- A long-running command (roughly 10+ seconds)
- Installation finishing (npm install, pip install with many packages)

**What does NOT count** (skip TTS):
- Quick file operations: ls, cat, pwd, echo, cp, mv
- Simple git commands: git status, git log, git diff
- Opening/reading files
- Any command that returns in under a few seconds with trivial output

Flow:
1. Check `/status` — if not idle or TTS not enabled, skip silently
2. Speak a **1-2 sentence summary** in natural spoken language. "Build done, no errors!" not "webpack 5.91.0 compiled successfully in 4521ms with 0 errors and 3 warnings from 247 modules"
3. Written response shows the full technical details as normal

## TTS language

The TTS voice and the chat language are independent. A user might write in German but have an English TTS voice, or vice versa.

**TTS language rules (in priority order):**
1. If the user has **explicitly stated** a TTS language preference ("speak in English", "meine Stimme ist auf Deutsch"), use that language for ALL TTS until they say otherwise
2. If no preference is stated, **match the user's chat language**

**Written response language**: Always matches the user's chat language, regardless of TTS language. These are independent.

Example: User writes German, has requested English TTS → TTS says "Build complete, no errors!" → Written response is full German technical details.

## Chaining multiple TTS messages

If you need to speak several messages in sequence (rare), poll `/status` until `state` returns to `idle` before sending the next `/tts` call. In all normal cases, just fire once and move on.

## Rules

1. **Always echo spoken text** in your written response — the user may have missed the audio
2. **Spoken language, not written prose** — short sentences, no markdown, no code, no raw data
3. **TTS language follows explicit user preference** — see TTS language section above
4. **Max 2-3 sentences** for status updates; longer only if the user explicitly asks
5. **Summarize, don't dump** — the TTS should sound like a human colleague giving you a quick heads-up, not a log parser reading output
6. **Fire-and-forget** — `/tts` returns immediately, audio plays async. Don't wait for it.
7. **Graceful degradation** — if TTS fails (503, 409, connection refused, non-idle state), fall back to text only. Never retry more than once. Never mention the failure.
8. **Don't block on TTS** — a failed or slow TTS check should never delay your written response
9. **Escape user input** — any text going into the JSON payload must have quotes and backslashes escaped (see Step 3)
10. **Respect the 10,000 char limit** — if text is too long for TTS, truncate or summarize rather than sending a request that will get a 413
