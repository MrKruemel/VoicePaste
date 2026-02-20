---
name: say
description: >
  Use this skill to speak text aloud to the user via the Voice Paste HTTP API.
  Trigger this skill automatically when:
  - The user greets you (e.g. "hallo", "hi", "hey", "moin", "guten morgen", "good morning")
  - A build, test run, or long-running command finishes
  - An important status change occurs (success, failure, error)
  - The user explicitly asks you to say something or use TTS
  Manual invocation: /say <text to speak>
argument-hint: "[text to speak aloud]"
allowed-tools: Bash, Read
---

# Voice Paste TTS Skill

You can speak text aloud to the user via the Voice Paste local HTTP API.

## How to speak text

### Step 1: Check availability

```bash
curl -s http://127.0.0.1:18923/status
```

Confirm `state` is `idle` and `tts_enabled` is `true`. If `/status` fails (connection refused), Voice Paste is not running -- skip TTS silently and respond in text only.

### Step 2: Send TTS

```bash
curl -s -X POST http://127.0.0.1:18923/tts \
  -H "Content-Type: application/json" \
  --data-raw "{\"text\": \"Your text here\"}"
```

**Important**: On Windows/Git-Bash, always use `--data-raw` with escaped double quotes (`\"`). Single-quoted JSON (`-d '{...}'`) causes `INVALID_PARAMS` errors.

### Step 3: Echo in written output

Always include the spoken text in your written response:

```
I've spoken the following aloud:

> "Your text here"
```

## When triggered manually with `/say`

If the user types `/say some text`, speak `$ARGUMENTS` via TTS. If no arguments given, ask what they want you to say.

## When triggered by a greeting

If the user greets you (hallo, hi, hey, moin, guten morgen, etc.):

1. Check `/status` to see if Voice Paste is running and idle
2. If available, greet back via TTS in the user's language. Keep it short and friendly (1 sentence).
3. Also greet back in your written response as normal.

Example: User says "Hallo" -> speak "Hallo! Ich bin bereit." and write a normal greeting.

## When triggered after a build/test/command

After a significant operation completes:

1. Check `/status` -- if not idle or TTS not enabled, skip TTS
2. Speak a **brief summary** (1-2 sentences, spoken language, no technical jargon)
3. Written response shows full details as normal

## TTS language

The TTS voice language and the chat language are independent. The user may write in German but have an English TTS voice configured, or vice versa.

- **TTS text**: Always use the language the user has **explicitly requested for TTS**. If the user says "speak in English" or "I have an English voice", use English for all TTS output until told otherwise. If no preference is stated, default to matching the user's chat language.
- **Written response**: Always match the user's chat language (German chat = German text, English chat = English text). The written response language is independent of TTS language.

## Rules

1. **Always echo spoken text** in written output -- the user may not hear it
2. **Spoken language, not written prose** -- short sentences, no markdown, no code
3. **TTS language follows user preference** -- see "TTS language" section above
4. **Max 2-3 sentences** for status updates; longer only if explicitly requested
5. **Summarize, don't read raw output** -- say "Build done, no errors" not the full log
6. **Fire-and-forget** -- don't poll waiting for TTS to finish unless chaining messages
7. **Handle errors gracefully** -- if TTS fails (503, 409, connection refused), fall back to text only. Never retry more than once.
8. **Don't block on TTS** -- `/tts` returns immediately, audio plays async

## Connection details

| Parameter | Value |
|-----------|-------|
| Host | `127.0.0.1` |
| Port | `18923` |
| Protocol | HTTP |
| Content-Type | `application/json` |

## Full API reference

For all endpoints and error codes, see [API-SKILL-REFERENCE.md](../../../docs/API-SKILL-REFERENCE.md).
