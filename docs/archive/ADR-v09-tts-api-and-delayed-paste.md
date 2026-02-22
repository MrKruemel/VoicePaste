# Architecture Decision Record: TTS API (Local HTTP Server) & Delayed Paste

**Date**: 2026-02-20
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.8.0 (current)
**Relevant to**: v0.8.1 or v0.9.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Feature 1: TTS API (Local HTTP Server)](#2-feature-1-tts-api-local-http-server)
   - 2.1 [Context and Motivation](#21-context-and-motivation)
   - 2.2 [Protocol Decision: HTTP vs Named Pipe Revisited](#22-protocol-decision-http-vs-named-pipe-revisited)
   - 2.3 [HTTP Framework Selection](#23-http-framework-selection)
   - 2.4 [API Surface Design](#24-api-surface-design)
   - 2.5 [Security Model](#25-security-model)
   - 2.6 [Threading Architecture](#26-threading-architecture)
   - 2.7 [Configuration & Settings Integration](#27-configuration--settings-integration)
   - 2.8 [PyInstaller Bundling Impact](#28-pyinstaller-bundling-impact)
   - 2.9 [Risk Assessment](#29-risk-assessment)
   - 2.10 [Scope & Release Planning](#210-scope--release-planning)
3. [Feature 2: Delayed Paste](#3-feature-2-delayed-paste)
   - 3.1 [Context and Motivation](#31-context-and-motivation)
   - 3.2 [Pipeline Insertion Point](#32-pipeline-insertion-point)
   - 3.3 [Feedback Mechanism](#33-feedback-mechanism)
   - 3.4 [Confirmation Mode (Enter Key)](#34-confirmation-mode-enter-key)
   - 3.5 [Cancel During Delay](#35-cancel-during-delay)
   - 3.6 [State Machine Changes](#36-state-machine-changes)
   - 3.7 [Configuration & Settings Integration](#37-configuration--settings-integration)
   - 3.8 [Risk Assessment](#38-risk-assessment)
   - 3.9 [Scope & Release Planning](#39-scope--release-planning)
4. [Cross-Feature Analysis](#4-cross-feature-analysis)
5. [Open Questions](#5-open-questions)

---

## 1. Executive Summary

Two new features are proposed:

1. **TTS API (Local HTTP Server)** -- A lightweight HTTP server bound to
   `127.0.0.1` that allows external programs (scripts, curl, browser extensions,
   Stream Deck, Claude Code) to send text to Voice Paste for TTS playback and to
   query application status.

2. **Delayed Paste** -- After voice recording and processing completes, the
   application waits a configurable number of seconds (or until Enter key
   confirmation) before pasting, giving the user time to position their cursor or
   cancel.

**Key insight**: The TTS API supersedes the Named Pipe approach from ADR-v08 for
the TTS use case. The user explicitly requested HTTP ("kleinen Web-Server mit
lokalem HTTP-Request") because it is universally accessible from any language and
tool via `curl`, `fetch()`, `Invoke-WebRequest`, etc. Named Pipes require
language-specific client code and are not accessible from web browsers.

**Recommendation**: Implement HTTP as the primary (and only) external API
protocol. Abandon the Named Pipe approach from ADR-v08 Section 2. The simplicity
of HTTP outweighs Named Pipe's marginal security advantages for a localhost-only
service.

---

## 2. Feature 1: TTS API (Local HTTP Server)

### 2.1 Context and Motivation

The ADR-v08 proposed Named Pipes (`\\.\pipe\VoicePasteAPI`) as the external API
protocol. The rationale was sound (zero dependencies, Windows DACL security, no
port conflicts). However, Named Pipes have a critical practical flaw identified
in Section 2.9 of that ADR:

> **Critical risk -- multiprocessing.connection interoperability**: Non-Python
> clients (PowerShell, C#, AHK) CANNOT connect directly.

The user's request for a "kleinen Web-Server mit lokalem HTTP-Request" reflects
this exact pain point. HTTP is the universal IPC protocol. Every programming
language, every scripting tool, and every shell has HTTP client support built in.

**Use cases that drove this request:**

1. A Python or PowerShell script that sends text to Voice Paste for TTS with a
   single `curl` call.
2. Claude Code (or any LLM CLI) calling Voice Paste to speak its output aloud.
3. A browser extension or Electron app that sends selected text via `fetch()`.
4. Stream Deck calling a local URL with a single HTTP action plugin.

All of these are trivial with HTTP and require significant effort with Named Pipes.

### 2.2 Protocol Decision: HTTP vs Named Pipe Revisited

| Criterion | Named Pipe | HTTP (localhost) |
|-----------|-----------|------------------|
| **Client availability** | Python, C# (with effort), PowerShell (complex), AHK (DllCall) | curl, Python requests, PowerShell Invoke-WebRequest, fetch(), ANY language |
| **Simplest client call** | 20+ lines of Python or 40+ lines of PowerShell | `curl -X POST http://localhost:18923/tts -d '{"text":"hello"}'` |
| **New dependency** | None (stdlib) | None (stdlib `http.server`) |
| **Security** | Same-user DACL, no network exposure | 127.0.0.1 only, optional Bearer token |
| **Port conflicts** | Impossible (pipe namespace) | Possible but mitigable (configurable port) |
| **Firewall visibility** | Invisible | Visible in netstat (localhost only, no firewall popup) |
| **Cross-platform potential** | Windows only | Works anywhere |

**Decision: HTTP only. Drop Named Pipe for the external API.**

Rationale:
1. The user explicitly requested HTTP. User preference overrides theoretical
   security advantages.
2. The universal client accessibility of HTTP eliminates the biggest risk
   identified in ADR-v08 (interoperability).
3. `http.server` from the Python stdlib adds zero new dependencies and zero
   binary size increase.
4. Binding to `127.0.0.1` (not `0.0.0.0`) ensures no network exposure. This is
   equivalent to Named Pipe security in practice: both require local access on
   the same machine.
5. Windows does NOT show a firewall popup for localhost-only servers. The
   `127.0.0.1` binding never touches the network stack's external interfaces.

**Named Pipe for internal IPC**: If/when context menu integration is implemented
(currently deferred), it can still use a Named Pipe for the trusted internal
channel. The HTTP server is for external/third-party use only.

### 2.3 HTTP Framework Selection

| Framework | Dependency | Binary Size Impact | Threading Model | Complexity |
|-----------|-----------|-------------------|-----------------|------------|
| **`http.server` (stdlib)** | None | 0 MB | `ThreadingHTTPServer` (1 thread per request) | Low |
| **Flask** | flask, werkzeug, jinja2, markupsafe, click, blinker, itsdangerous | +3-5 MB | WSGI, needs waitress/gunicorn | Medium |
| **FastAPI** | fastapi, starlette, uvicorn, pydantic, anyio, etc. | +10-15 MB | asyncio, needs uvicorn | Medium-High |
| **Bottle** | bottle (single file) | +0.1 MB | Built-in WSGI | Low |

**Decision: `http.server` from the Python standard library.**

Rationale:
1. **Zero new dependencies.** The current binary is ~280 MB. Adding Flask would
   increase it by 3-5 MB, FastAPI by 10-15 MB. More importantly, new
   dependencies mean new PyInstaller bundling issues to debug.
2. **`http.server.ThreadingHTTPServer`** provides thread-per-request handling
   which is perfect for our low-concurrency use case (1-2 requests per second
   maximum).
3. **The API surface is tiny** (5-6 endpoints). We do not need routing,
   middleware, template engines, request parsing, or any other framework feature.
   Raw `http.server` is entirely sufficient.
4. **Simplicity.** The entire HTTP server can be implemented in ~200 lines. Less
   code = fewer bugs = easier maintenance.

**What we lose vs Flask/FastAPI:**
- No automatic OpenAPI/Swagger docs (we document manually in API.md).
- No automatic request validation (we validate manually, ~20 lines).
- No CORS middleware (we add 3 lines of CORS headers manually).
- No async support (not needed; our API calls dispatch to existing worker threads
  and return immediately).

These tradeoffs are acceptable for a utility with 5 endpoints and <2 requests/second.

### 2.4 API Surface Design

#### Base URL

```
http://127.0.0.1:18923
```

Port `18923` chosen because:
- Above the well-known port range (0-1023) and registered port range (1024-49151).
- Not used by any known common application (checked against IANA registry).
- Easy to remember (1-8-9-2-3, ascending digits).
- Configurable in `config.toml` under `[api] port = 18923`.

#### Endpoint Reference

| Method | Path | Body | Description | State Requirement |
|--------|------|------|-------------|-------------------|
| `GET` | `/status` | none | Query current state + app info | Any |
| `POST` | `/tts` | `{"text": "..."}` | Speak text aloud via TTS | IDLE (else 409) |
| `POST` | `/stop` | none | Stop current TTS playback | SPEAKING (else 200 noop) |
| `POST` | `/record/start` | `{"mode": "summary"}` | Start recording | IDLE (else 409) |
| `POST` | `/record/stop` | none | Stop recording, trigger pipeline | RECORDING (else 409) |
| `POST` | `/cancel` | none | Cancel current recording | RECORDING (else 200 noop) |
| `GET` | `/health` | none | Health check (always 200) | Any |

**Design decisions:**

1. **RESTful paths over JSON-RPC.** The ADR-v08 Named Pipe design used a single
   endpoint with `{"action": "tts"}` JSON-RPC style. For HTTP, RESTful paths
   (`POST /tts`) are more natural and self-documenting. They also work better
   with tools like `curl` where the URL tells you what happens.

2. **POST for mutations, GET for queries.** Standard REST semantics. GET
   requests are safe and idempotent. POST requests cause side effects.

3. **No authentication in MVP.** The server binds to `127.0.0.1` only. Any
   process on the local machine can connect, but remote machines cannot. This is
   the same security posture as the Named Pipe DACL approach. An optional Bearer
   token can be added in a later version for users who want defense-in-depth.

4. **STT/summarization exposed via `/record/start` and `/record/stop`.** This
   gives external tools full access to the recording pipeline, not just TTS. The
   result is pasted at the cursor position, same as hotkey-triggered recording.

5. **No clipboard/transcript content in responses.** The API never returns the
   transcribed or summarized text. It only reports status. This prevents
   information leakage if a rogue local process connects. In a future version, a
   `/record/start` variant could return the result instead of pasting it.

#### Request Format

Content-Type: `application/json` (for POST requests with body).

```json
POST /tts
Content-Type: application/json

{
  "text": "Dies ist ein Test.",
  "request_id": "abc123"
}
```

The `request_id` field is optional. If provided, it is echoed in the response.

#### Response Format

All responses are JSON with Content-Type `application/json`.

**Success (200):**
```json
{
  "status": "ok",
  "request_id": "abc123",
  "data": {}
}
```

**Status response (200):**
```json
{
  "status": "ok",
  "data": {
    "state": "idle",
    "tts_enabled": true,
    "api_version": "1",
    "app_version": "0.8.1"
  }
}
```

**Conflict (409 -- busy):**
```json
{
  "status": "busy",
  "state": "recording",
  "message": "Recording is in progress."
}
```

**Error (400 -- bad request):**
```json
{
  "status": "error",
  "error_code": "INVALID_PARAMS",
  "message": "text is required and must be non-empty"
}
```

**Error (413 -- text too long):**
```json
{
  "status": "error",
  "error_code": "TEXT_TOO_LONG",
  "message": "Text exceeds 10000 character limit"
}
```

#### Error Codes

| HTTP Status | Error Code | Meaning |
|-------------|-----------|---------|
| 200 | -- | Success |
| 400 | `INVALID_PARAMS` | Missing or invalid parameters |
| 404 | `NOT_FOUND` | Unknown endpoint |
| 405 | `METHOD_NOT_ALLOWED` | Wrong HTTP method for endpoint |
| 409 | `BUSY` | Another operation is in progress |
| 413 | `TEXT_TOO_LONG` | Text exceeds 10,000 character limit |
| 429 | `RATE_LIMITED` | Too many requests (>5/second) |
| 500 | `INTERNAL_ERROR` | Unexpected server error |
| 503 | `TTS_NOT_CONFIGURED` | TTS is disabled or has no API key |

### 2.5 Security Model

#### Threat Analysis

| Threat | Risk | Mitigation |
|--------|------|------------|
| **Remote attacker connects to API** | None | Bound to `127.0.0.1`. TCP connections from remote machines are rejected at the OS level. |
| **Local malicious process sends TTS commands** | Low | Same risk as Named Pipe. Any local process running as the same user can connect. API disabled by default. |
| **TTS abuse (offensive content)** | Low | API requires explicit opt-in. Rate limiting (5 req/s). Text length cap (10,000 chars). All requests logged. |
| **Port scanning reveals Voice Paste** | Very Low | Port `18923` is high and uncommon. Only responds on `127.0.0.1`. Port scanner would need to run locally. |
| **API used to exfiltrate data** | Very Low | API never returns transcript content, clipboard data, or API keys. `/status` returns only structural info. |
| **Port conflict with another app** | Low | Port is configurable. Startup logs a clear error if port is in use. |

#### Security Controls

1. **API disabled by default** (`api_enabled = false` in config.toml).
2. **`127.0.0.1` binding only.** The server never binds to `0.0.0.0` or any
   external interface. This is hardcoded, not configurable, to prevent accidental
   network exposure.
3. **Rate limiting**: Maximum 5 requests per second from the same connection.
   Excess requests receive HTTP 429.
4. **Input validation**: Text for TTS capped at 10,000 characters. All request
   fields validated against expected types and ranges.
5. **No secret exposure**: The API never returns API keys, audio data, or
   transcript content.
6. **Logging**: All API requests logged at INFO level (text content NOT logged,
   only metadata: endpoint, response status, text length).
7. **CORS headers**: `Access-Control-Allow-Origin: http://localhost:*` only.
   No wildcard CORS. This prevents arbitrary websites from calling the API via
   browser fetch() (defense-in-depth; the 127.0.0.1 binding already prevents
   remote access).
8. **Optional Bearer token (future)**: In a future version, users can set an API
   token in Settings. Requests without `Authorization: Bearer <token>` are
   rejected with 401. Not in MVP.

#### Why CORS matters

Even though the server binds to `127.0.0.1`, a malicious website loaded in the
user's browser could attempt `fetch("http://127.0.0.1:18923/tts", ...)`. Without
CORS restrictions, the browser would send the request and the TTS would trigger.

Mitigation: Set `Access-Control-Allow-Origin` to only allow `http://localhost`
origins (for legitimate local web tools). Requests from other origins (e.g.,
`https://evil.example.com`) will be blocked by the browser's CORS enforcement.

Note: CORS is a browser-only protection. Non-browser clients (curl, Python, etc.)
ignore CORS headers entirely. This is acceptable because non-browser clients
require local code execution, which already implies full system access.

### 2.6 Threading Architecture

#### Current Threading Model (v0.8.0)

```
Main Thread (T0):   pystray event loop (system tray, blocks)
Thread 1 (T1):      keyboard hotkey listener (daemon, blocks on hook)
Thread 2 (T2):      Pipeline worker (per session, daemon, spawned on demand)
Thread 3 (T3):      Settings dialog tkinter (on demand, spawned per open)
```

Threads T4 (overlay) and T5 (TTS playback) are subsumed into T2 in practice --
the overlay is disabled, and TTS playback runs as part of the pipeline worker.

#### Proposed Addition

```
Thread N (Tn):      HTTP API server (daemon, persistent while api_enabled)
                    Using http.server.ThreadingHTTPServer
```

**Threading details:**

1. The HTTP server runs on its own daemon thread. It calls
   `ThreadingHTTPServer.serve_forever()` which blocks.

2. `ThreadingHTTPServer` spawns a new thread for each incoming request. These
   request handler threads are short-lived (parse JSON, dispatch command, return
   response) and complete in <10ms for most endpoints.

3. For the `/tts` endpoint, the request handler dispatches to
   `app._run_tts_pipeline(text)` on a new worker thread (same pattern as the
   hotkey-triggered TTS). The HTTP handler returns `{"status": "ok"}` immediately
   (fire-and-forget). The TTS pipeline runs asynchronously.

4. For `/record/start`, the handler calls `app._start_recording()` synchronously
   (it only starts the audio stream, which is fast) and returns. The actual
   processing happens later when `/record/stop` is called and the pipeline
   thread is spawned.

5. Thread shutdown: `ThreadingHTTPServer` has a `shutdown()` method that
   interrupts `serve_forever()`. Called during `app._shutdown()` or when
   `api_enabled` is toggled off.

#### Interaction Diagram

```
External Tool                  HTTP Server (Tn)              VoicePasteApp (T0/T2)
     |                              |                              |
     | POST /tts {"text":"hi"}      |                              |
     |----------------------------->|                              |
     |                              | validate request             |
     |                              | check app.state == IDLE      |
     |                              |------ dispatch ------------->|
     |                              |          (spawn worker T2)   |
     |    200 {"status": "ok"}      |                              |
     |<-----------------------------|                              |
     |                              |                     [TTS pipeline runs]
     |                              |                     [state: PROCESSING]
     |                              |                     [state: SPEAKING]
     |                              |                     [audio plays]
     |                              |                     [state: IDLE]
     |                              |                              |
     | GET /status                  |                              |
     |----------------------------->|                              |
     |                              | read app.state               |
     |    200 {"state": "idle"}     |                              |
     |<-----------------------------|                              |
```

### 2.7 Configuration & Settings Integration

#### Config.toml Additions

```toml
[api]
# Enable the local HTTP API server (default: false)
# When enabled, other programs can control Voice Paste via HTTP.
# The server binds to 127.0.0.1 only (no network exposure).
enabled = false
# HTTP port (default: 18923)
port = 18923
```

#### AppConfig Additions

```python
# --- v0.8.1 or v0.9: HTTP API ---
api_enabled: bool = False
api_port: int = 18923
```

#### Settings Dialog

Add to the existing Settings dialog under a new "Integration" or "API" tab/section:

```
+-- API / Integration -----------------------------------------+
|                                                              |
|  [ ] Lokale HTTP-API aktivieren                              |
|      Erlaubt anderen Programmen, Voice Paste zu steuern.     |
|      Adresse: http://127.0.0.1:18923                         |
|                                                              |
|  Port: [18923]                                               |
|                                                              |
|  Hinweis: Nur lokaler Zugriff moeglich (127.0.0.1).          |
|  Kein Netzwerkzugriff von aussen.                            |
+--------------------------------------------------------------+
```

#### Hot-Reload Behavior

When `api_enabled` is toggled in Settings:

- **ON**: Start the HTTP server thread. Log the listening address and port.
  Show a tray notification: "HTTP API gestartet auf http://127.0.0.1:18923".
- **OFF**: Call `server.shutdown()`. The server thread exits. Existing in-flight
  requests are completed before shutdown. Log shutdown. Show tray notification:
  "HTTP API gestoppt".

Port changes require stopping and restarting the server. The settings dialog
should show a note: "Port-Aenderungen erfordern Neustart der API."

### 2.8 PyInstaller Bundling Impact

**Zero impact.** `http.server` is part of the Python standard library. It is
already included in every PyInstaller bundle. No `--hidden-import`, no
`--collect-data`, no additional DLLs.

This is the strongest argument for `http.server` over Flask/FastAPI. The current
build script does not need any changes.

### 2.9 Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| **Port already in use** | Low | Catch `OSError` on bind. Log clear error. Show tray notification with the conflicting port. Allow configuration. |
| **Windows Firewall popup** | Very Low | `127.0.0.1` binding does not trigger Windows Firewall. Tested on Windows 10/11. Only `0.0.0.0` triggers the prompt. |
| **Security scanner flags localhost listener** | Low | Document in README that this is expected behavior. API is disabled by default. |
| **ThreadingHTTPServer leaks threads on crash** | Low | Request handler threads are daemon threads with try/except. Server thread has top-level exception handler with auto-restart (3 attempts). |
| **Concurrent requests cause race condition** | Medium | The state machine is already thread-safe (guarded by `_state_lock`). API handlers read state atomically. Mutations (start recording, TTS) are dispatched through the same callbacks as hotkeys, which already handle concurrent access. |
| **API keeps app running after tray quit** | Low | HTTP server thread is a daemon thread. When the main thread exits (pystray stops), daemon threads are terminated. Additionally, `server.shutdown()` is called explicitly in `_shutdown()`. |

### 2.10 Scope & Release Planning

#### MVP Scope

1. `http.server.ThreadingHTTPServer` bound to `127.0.0.1:18923`.
2. Endpoints: `/health`, `/status`, `/tts`, `/stop`, `/record/start`,
   `/record/stop`, `/cancel`.
3. API disabled by default. Toggle + port in Settings.
4. Rate limiting (5 req/s).
5. CORS headers for localhost origins.
6. Logging (no content).
7. Documentation in `docs/API.md` with curl examples.

#### Post-MVP

- Optional Bearer token authentication.
- `/tts` with `wait=true` query parameter: block until TTS completes, then
  return with duration info.
- `/transcribe` endpoint: accept audio file upload, return transcript (bypasses
  recording, useful for batch processing).
- Event streaming via Server-Sent Events (SSE) on `GET /events`: push state
  changes to connected clients in real time.

#### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `src/api_server.py` | HTTP server, request handler, command dispatch | ~250 |
| `docs/API.md` | API documentation with curl examples | ~150 |

#### Modified Files

| File | Changes |
|------|---------|
| `src/config.py` | Add `api_enabled: bool`, `api_port: int` fields |
| `src/constants.py` | Add `DEFAULT_API_PORT`, `API_RATE_LIMIT_PER_SECOND` |
| `src/main.py` | Initialize API server, wire dispatch, shutdown |
| `src/settings_dialog.py` | Add API toggle + port field |

#### Estimated Effort

| Phase | Days |
|-------|------|
| Implement `api_server.py` with all endpoints | 1.5 |
| Wire into `main.py` + settings + config | 1 |
| Rate limiting + CORS + error handling | 0.5 |
| API documentation + curl examples | 0.5 |
| Testing (manual + unit tests) | 1 |
| **Total** | **4.5 days** |

---

## 3. Feature 2: Delayed Paste

### 3.1 Context and Motivation

Currently, the pipeline is:

```
RECORDING -> PROCESSING (STT + summarize) -> PASTING (immediate clipboard + Ctrl+V) -> IDLE
```

The paste happens instantly after summarization completes. This can be problematic:

1. **Wrong window focused**: The user may have switched windows during
   the 1-3 seconds of processing. The paste lands in the wrong application.

2. **Wrong cursor position**: Even in the right window, the cursor may not be
   where the user wants the text.

3. **Review before paste**: The user may want to see what was transcribed before
   committing to paste.

The Delayed Paste feature adds a configurable pause (or confirmation step)
between summarization and the actual paste action.

### 3.2 Pipeline Insertion Point

The delay goes **after summarization, before clipboard write + Ctrl+V**.

Current flow in `_run_pipeline()` (main.py lines 846-994):

```python
# Step 1: Transcribe
transcript = self._stt.transcribe(audio_data)
# Step 2: Summarize
summary = self._summarizer.summarize(transcript)
# Step 3: Paste (immediately)
self._set_state(AppState.PASTING)
success = paste_text(summary)
```

Proposed flow:

```python
# Step 1: Transcribe
transcript = self._stt.transcribe(audio_data)
# Step 2: Summarize
summary = self._summarizer.summarize(transcript)
# Step 3: Wait (NEW)
if not self._wait_before_paste(summary):
    # User cancelled during wait
    return
# Step 4: Paste
self._set_state(AppState.PASTING)
success = paste_text(summary)
```

The `_wait_before_paste()` method handles both the countdown delay and the
optional Enter key confirmation.

### 3.3 Feedback Mechanism

**Problem**: The tkinter overlay is disabled on Python 3.14 due to the dual-Tk()
conflict with the settings dialog. We cannot use tkinter for countdown feedback.

**Options evaluated:**

| Feedback Method | Dependency | Dual-Tk Conflict | User Visibility |
|----------------|-----------|-------------------|-----------------|
| Tkinter overlay countdown | tkinter | YES (blocked) | High |
| Win32 API tooltip/balloon | ctypes (stdlib) | No | Medium |
| System tray notification | pystray (existing) | No | Medium |
| Audio countdown beeps | sounddevice (existing) | No | Medium |
| Console/log only | None | No | Low |
| Win32 layered window (ctypes) | ctypes (stdlib) | No | High |

**Decision: Combination of (a) tray notification + (b) audio countdown beeps +
(c) tray icon tooltip update.**

Rationale:
1. **Tray notification**: When the delay starts, show a balloon notification:
   "Einfuegen in 3 Sekunden... (Escape = abbrechen, Enter = sofort einfuegen)".
   This is the primary feedback channel.
2. **Audio beeps**: Play a short beep each second during the countdown (same
   frequency as the recording start tone, but shorter -- 30ms pulses). This gives
   audible feedback without requiring visual attention.
3. **Tray icon tooltip**: Update the tray icon tooltip to show "Einfuegen in Xs..."
   during the countdown. Less visible than a notification but provides persistent
   state information.
4. **No tkinter dependency.** All three channels use existing infrastructure
   (pystray, sounddevice, ctypes).

**Why not a Win32 layered window?** A pure ctypes overlay window (no tkinter)
would provide the best visual feedback (countdown number floating near the
cursor). However, this is significant implementation effort (~200 lines of Win32
API code) and is better suited as a general overlay replacement project. The
tray notification + audio beeps approach works well enough for MVP and can be
enhanced later.

**Future enhancement**: When the overlay is rebuilt using pure Win32 API (ctypes)
to solve the dual-Tk conflict, the delayed paste countdown will integrate into
that overlay with a visual countdown timer.

### 3.4 Confirmation Mode (Enter Key)

When `paste_require_confirmation = true`, the pipeline waits indefinitely (up to
a timeout) for the user to press Enter before pasting.

**Implementation approach:**

```python
def _wait_for_confirmation(self, summary: str, timeout: float = 30.0) -> bool:
    """Wait for Enter key press to confirm paste.

    The user can also press Escape to cancel.

    Args:
        summary: The text that will be pasted (for notification).
        timeout: Maximum seconds to wait (default 30).

    Returns:
        True if confirmed (Enter pressed), False if cancelled or timed out.
    """
    confirm_event = threading.Event()
    cancelled = threading.Event()

    def on_enter():
        confirm_event.set()

    def on_escape():
        cancelled.set()

    # Register temporary hotkeys
    enter_handle = kb.add_hotkey("enter", on_enter, suppress=False)
    # Escape is already registered as cancel during this state

    try:
        # Show notification
        preview = summary[:80] + ("..." if len(summary) > 80 else "")
        self._tray_manager.notify(
            APP_NAME,
            f"Text bereit. Enter = einfuegen, Escape = abbrechen.\n{preview}"
        )

        # Wait for either Enter or Escape or timeout
        while not confirm_event.is_set() and not cancelled.is_set():
            if self._shutdown_event.wait(timeout=0.1):
                return False
            timeout -= 0.1
            if timeout <= 0:
                logger.info("Paste confirmation timed out after 30 seconds.")
                self._tray_manager.notify(APP_NAME, "Einfuegen abgebrochen (Zeitlimit).")
                return False

        return confirm_event.is_set() and not cancelled.is_set()

    finally:
        kb.remove_hotkey(enter_handle)
```

**Important design decision: `suppress=False` on the Enter hotkey.**

Setting `suppress=False` means the Enter keypress is NOT consumed by the
keyboard hook. It propagates to the focused application. This is intentional:

- If the user is in a text editor and presses Enter, the editor also receives
  the Enter keypress. This is acceptable because the paste happens immediately
  after, overwriting whatever the Enter did.

- Using `suppress=True` would prevent Enter from reaching the focused app, which
  could cause confusion if the user presses Enter expecting it to work in the
  app and nothing visible happens (the paste also has not occurred yet).

- The 50ms delay between the Enter key release and the Ctrl+V paste (PASTE_DELAY_MS)
  ensures the Enter keypress is fully processed before the paste occurs.

**Alternative considered: `suppress=True`.** This would cleanly consume the Enter
key so only the paste happens. However, the `keyboard` library's suppress feature
can be unreliable across applications and may interfere with the keyboard hook
chain. The simpler approach (do not suppress) is more robust.

**Conflict with global hotkey system:** The Enter key is not a modifier+key
combination. Registering a global hook on bare "enter" could interfere with
normal typing. This is why the hook is only registered during the AWAITING_PASTE
state (a brief, bounded window) and unregistered immediately after.

### 3.5 Cancel During Delay

During both the countdown delay and the confirmation wait:

1. **Escape key** cancels the paste. The Escape cancel hotkey is already
   registered during RECORDING and can be extended to the AWAITING_PASTE state.

2. **Primary hotkey** (Ctrl+Alt+R) is ignored during the delay. Starting a new
   recording while awaiting paste confirmation would be confusing.

3. **Shutdown** (tray quit) cancels the paste cleanly.

When cancelled:
- The summary text is discarded.
- Clipboard is restored from backup (same as normal flow).
- State returns to IDLE.
- Tray notification: "Einfuegen abgebrochen."
- Cancel audio cue plays.

### 3.6 State Machine Changes

#### New State: AWAITING_PASTE

```
IDLE -> RECORDING -> PROCESSING -> AWAITING_PASTE (NEW) -> PASTING -> IDLE
                                        |
                                   (Escape/timeout)
                                        |
                                        v
                                       IDLE
```

**AWAITING_PASTE** is a new state between PROCESSING and PASTING. It is only
entered when `paste_delay_seconds > 0` or `paste_require_confirmation = true`.
When both are disabled (default), the pipeline skips directly from PROCESSING
to PASTING (no behavior change from current version).

Add to `AppState` enum:

```python
class AppState(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    AWAITING_PASTE = "awaiting_paste"  # NEW
    PASTING = "pasting"
    SPEAKING = "speaking"
```

**Impact on existing code:**

- **Tray icon**: The AWAITING_PASTE state can reuse the PROCESSING icon color
  (yellow/amber) or introduce a new color (blue). Recommendation: reuse
  PROCESSING color to minimize changes. The tray tooltip differentiates:
  "Verarbeitung..." vs "Bereit zum Einfuegen...".

- **Hotkey guards**: Hotkey callbacks already check `if current != AppState.IDLE`
  for most actions. AWAITING_PASTE is automatically handled by this guard (hotkeys
  are ignored during the wait).

- **Overlay**: When the overlay is eventually rebuilt, AWAITING_PASTE will get
  its own visual state (countdown display).

- **API server**: The `/status` endpoint returns `"state": "awaiting_paste"`.
  The `/cancel` endpoint can also cancel an awaiting paste.

**Alternative considered: No new state, use a boolean flag.**

As done for Hands-Free Mode (`_handsfree_active`), the delay could be modeled as
a flag within the PROCESSING state. However, a new state is cleaner because:
- It is a distinct user-visible phase (the user should see different feedback
  during processing vs waiting-to-paste).
- The state machine diagram remains clear and testable.
- Hotkey guards can be state-specific if needed.
- The API `/status` response distinguishes the two phases.

### 3.7 Configuration & Settings Integration

#### Config.toml Additions

```toml
[paste]
# Delay in seconds before pasting (0 = paste immediately, default)
# During the delay, press Escape to cancel or Enter to paste immediately.
delay_seconds = 0.0
# Require Enter key confirmation before pasting (default: false)
# When true, the app waits for Enter after processing. Escape cancels.
# If delay_seconds > 0 AND require_confirmation is true, the delay
# runs first, then confirmation is required.
require_confirmation = false
# Timeout for confirmation mode in seconds (default: 30)
# After this time without Enter or Escape, the paste is cancelled.
confirmation_timeout_seconds = 30.0
```

#### AppConfig Additions

```python
# --- v0.8.1 or v0.9: Delayed paste ---
paste_delay_seconds: float = 0.0
paste_require_confirmation: bool = False
paste_confirmation_timeout_seconds: float = 30.0
```

#### Settings Dialog

Add to the Settings dialog, either as a new "Paste" section or under an existing
"Advanced" section:

```
+-- Einfuegen (Paste) ----------------------------------------+
|                                                              |
|  Verzoegerung vor dem Einfuegen:  [0.0] Sekunden             |
|  (0 = sofort, empfohlen: 2-5 Sekunden)                      |
|                                                              |
|  [ ] Enter-Bestaetigung vor dem Einfuegen erfordern          |
|      Wartet auf Enter-Taste nach der Verarbeitung.           |
|      Escape bricht ab. Timeout: 30 Sekunden.                 |
|                                                              |
+--------------------------------------------------------------+
```

**Validation:**
- `paste_delay_seconds` must be >= 0.0 and <= 30.0. Values > 30 are clamped.
- `paste_confirmation_timeout_seconds` must be >= 5.0 and <= 120.0.

### 3.8 Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| **Enter hotkey interferes with normal typing** | Medium | Hook is only active during AWAITING_PASTE state (brief, bounded window). Unregistered immediately after. |
| **User forgets about pending paste** | Low | Audio beeps every second during countdown. Tray notification. Confirmation mode has a 30-second timeout. |
| **Race condition between countdown timer and Escape** | Low | Both paths (timeout expiry and Escape) set the same `threading.Event`. The `_wait_before_paste()` method checks atomically. |
| **Countdown beeps are annoying** | Low | Audio cues can be disabled via existing `audio_cues_enabled` setting. The beeps are very short (30ms) and soft. |
| **Default delay of 0 confuses users** | None | The default is 0 (immediate paste, same as current behavior). Users must explicitly opt in. |
| **Clipboard expires during long confirmation wait** | Very Low | The clipboard is only written at paste time, not during the wait. The backup/restore cycle is unchanged. |
| **Pipeline thread blocks during delay** | Acceptable | The pipeline worker thread (T2) already blocks during recording and STT. Adding a delay does not change the threading model. The thread is a daemon and will terminate on shutdown. |

### 3.9 Scope & Release Planning

#### MVP Scope

1. `paste_delay_seconds` config field with countdown timer.
2. `paste_require_confirmation` config field with Enter key wait.
3. AWAITING_PASTE state in AppState enum.
4. Tray notification feedback during delay/confirmation.
5. Audio beeps during countdown (reuse existing audio cue infrastructure).
6. Escape to cancel during delay.
7. Settings dialog integration.

#### Post-MVP

- Win32 overlay countdown display (part of overlay rebuild project).
- Text preview in notification (show first N characters of summary).
- "Edit before paste" mode: open a text box with the summary for manual editing.

#### Modified Files

| File | Changes |
|------|---------|
| `src/constants.py` | Add `AWAITING_PASTE` to AppState, add paste config defaults |
| `src/config.py` | Add paste delay/confirmation fields |
| `src/main.py` | Add `_wait_before_paste()`, modify `_run_pipeline()` |
| `src/tray.py` | Add AWAITING_PASTE icon state (or reuse PROCESSING) |
| `src/settings_dialog.py` | Add paste delay/confirmation UI |
| `src/notifications.py` | Add countdown beep function |

#### Estimated Effort

| Phase | Days |
|-------|------|
| AppState enum + config + constants changes | 0.5 |
| `_wait_before_paste()` implementation | 1 |
| Enter key confirmation logic | 0.5 |
| Audio countdown beeps | 0.5 |
| Settings dialog integration | 0.5 |
| Testing (delay, confirmation, cancel, timeout) | 1 |
| **Total** | **4 days** |

---

## 4. Cross-Feature Analysis

### 4.1 TTS API + Delayed Paste

These features are independent. However:

- The HTTP API's `/record/start` + `/record/stop` flow triggers the same
  pipeline as hotkey-triggered recording. The delayed paste applies to
  API-triggered recording as well. This is correct behavior: the external tool
  triggers the recording, and the user still controls when/where to paste.

- A future API endpoint (`/record/start?return_text=true`) could bypass the
  paste entirely and return the transcript/summary in the HTTP response. This
  would make the delay irrelevant for API-triggered recording. Not in MVP.

### 4.2 Combined Config Schema

```toml
[api]
enabled = false
port = 18923

[paste]
delay_seconds = 0.0
require_confirmation = false
confirmation_timeout_seconds = 30.0
```

### 4.3 Combined Threading Model (after both features)

```
Main Thread (T0):   pystray event loop
Thread 1 (T1):      keyboard hotkey listener (daemon)
Thread 2 (T2):      Pipeline worker (per session, daemon)
Thread 3 (T3):      Settings dialog tkinter (on demand)
Thread N (Tn):      HTTP API server (when api_enabled, daemon)
Thread N+1..N+k:    HTTP request handler threads (per request, transient)
```

No increase in persistent threads beyond the single HTTP server thread.

### 4.4 Implementation Order

**Recommendation: Implement Delayed Paste first, then TTS API.**

1. Delayed Paste changes only internal pipeline code (main.py, constants.py,
   config.py). It does not add new modules. It is self-contained and easy to test.

2. TTS API adds a new module (`api_server.py`) and depends on the internal
   dispatch methods being stable. Implementing Delayed Paste first ensures the
   pipeline (including the new AWAITING_PASTE state) is solid before exposing it
   via HTTP.

3. The TTS API's `/cancel` endpoint should be able to cancel an AWAITING_PASTE
   state. This is easier to implement if AWAITING_PASTE already exists.

---

## 5. Open Questions

| # | Question | Default Assumption | Decision Needed? |
|---|---------|-------------------|------------------|
| 1 | Should the HTTP API also expose STT/summarization (`/record/*`), or TTS only? | Expose both (full control). | Yes -- TTS-only would be simpler. |
| 2 | Should the API support `wait=true` for synchronous TTS (block until audio finishes)? | No in MVP. Fire-and-forget. | No |
| 3 | Default port `18923` -- acceptable? | Yes | Confirm or suggest alternative. |
| 4 | Should the delayed paste countdown use audio beeps? Or silent (notification only)? | Audio beeps (reuse existing cue system). | Yes -- some users may find beeps annoying. |
| 5 | Maximum delay before paste -- 30 seconds reasonable? | Yes. Longer delays are likely user error. | Confirm. |
| 6 | Should confirmation mode show a text preview in the notification? | Yes, first 80 characters. | No (obvious UX choice). |
| 7 | AWAITING_PASTE icon -- reuse PROCESSING color or new color? | Reuse PROCESSING (yellow). | Preference? |
| 8 | Should the API be in v0.8.1 (patch) or v0.9.0 (minor)? | v0.9.0 (new feature = minor version). | Depends on release timing. |

---

## Appendix A: curl Examples

### Health Check

```bash
curl http://127.0.0.1:18923/health
# {"status": "ok"}
```

### Query Status

```bash
curl http://127.0.0.1:18923/status
# {"status": "ok", "data": {"state": "idle", "tts_enabled": true, "api_version": "1", "app_version": "0.9.0"}}
```

### Trigger TTS

```bash
curl -X POST http://127.0.0.1:18923/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Hallo, dies ist ein Test."}'
# {"status": "ok"}
```

### Stop TTS

```bash
curl -X POST http://127.0.0.1:18923/stop
# {"status": "ok"}
```

### Start Recording (Summary Mode)

```bash
curl -X POST http://127.0.0.1:18923/record/start \
  -H "Content-Type: application/json" \
  -d '{"mode": "summary"}'
# {"status": "ok"}
```

### Stop Recording (Trigger Pipeline)

```bash
curl -X POST http://127.0.0.1:18923/record/stop
# {"status": "ok"}
```

### Cancel Recording

```bash
curl -X POST http://127.0.0.1:18923/cancel
# {"status": "ok"}
```

### PowerShell Example

```powershell
# Trigger TTS
Invoke-RestMethod -Uri "http://127.0.0.1:18923/tts" `
    -Method POST `
    -ContentType "application/json" `
    -Body '{"text": "Hallo aus PowerShell"}'

# Query status
Invoke-RestMethod -Uri "http://127.0.0.1:18923/status"
```

### Python requests Example

```python
import requests

# Trigger TTS
response = requests.post(
    "http://127.0.0.1:18923/tts",
    json={"text": "Hallo aus Python"},
)
print(response.json())  # {"status": "ok"}

# Query status
status = requests.get("http://127.0.0.1:18923/status").json()
print(status["data"]["state"])  # "idle"
```

## Appendix B: `api_server.py` Implementation Sketch

```python
"""Local HTTP API server for Voice Paste.

Provides a localhost-only REST API that allows external programs to
control Voice Paste (TTS, recording, status queries).

Uses http.server from the Python standard library (zero dependencies).

v0.9: Initial implementation.
"""

import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Rate limiting: max requests per second per client
RATE_LIMIT_PER_SECOND = 5
MAX_CONTENT_LENGTH = 65536  # 64 KB max request body


class _RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, max_per_second: int = RATE_LIMIT_PER_SECOND) -> None:
        self._max = max_per_second
        self._tokens: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            timestamps = self._tokens.get(client_id, [])
            # Remove timestamps older than 1 second
            timestamps = [t for t in timestamps if now - t < 1.0]
            if len(timestamps) >= self._max:
                self._tokens[client_id] = timestamps
                return False
            timestamps.append(now)
            self._tokens[client_id] = timestamps
            return True


class VoicePasteAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Voice Paste API."""

    server: "VoicePasteAPIServer"

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP server logs to our logger instead of stderr."""
        logger.debug("HTTP: %s", format % args)

    def _send_json(self, status_code: int, data: dict) -> None:
        """Send a JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # CORS headers
        origin = self.headers.get("Origin", "")
        if origin.startswith("http://localhost"):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Optional[dict]:
        """Read and parse JSON request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_CONTENT_LENGTH:
            return None
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        return json.loads(body.decode("utf-8"))

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/status":
            data = self.server.command_handler({"action": "status"})
            self._send_json(200, data)
        else:
            self._send_json(404, {"status": "error", "error_code": "NOT_FOUND"})

    def do_POST(self) -> None:
        # Rate limiting
        client = self.client_address[0]
        if not self.server.rate_limiter.allow(client):
            self._send_json(429, {"status": "error", "error_code": "RATE_LIMITED"})
            return

        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"status": "error", "error_code": "INVALID_PARAMS",
                                   "message": "Invalid JSON body"})
            return

        if body is None:
            self._send_json(413, {"status": "error", "error_code": "INVALID_PARAMS",
                                   "message": "Request body too large"})
            return

        # Route to handler
        if self.path == "/tts":
            body["action"] = "tts"
        elif self.path == "/stop":
            body["action"] = "stop_tts"
        elif self.path == "/record/start":
            body["action"] = "record_start"
        elif self.path == "/record/stop":
            body["action"] = "record_stop"
        elif self.path == "/cancel":
            body["action"] = "cancel"
        else:
            self._send_json(404, {"status": "error", "error_code": "NOT_FOUND"})
            return

        result = self.server.command_handler(body)
        status_code = 200
        if result.get("status") == "busy":
            status_code = 409
        elif result.get("status") == "error":
            code = result.get("error_code", "")
            if code == "INVALID_PARAMS":
                status_code = 400
            elif code == "TEXT_TOO_LONG":
                status_code = 413
            elif code == "TTS_NOT_CONFIGURED":
                status_code = 503
            else:
                status_code = 500

        self._send_json(status_code, result)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        origin = self.headers.get("Origin", "")
        if origin.startswith("http://localhost"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class VoicePasteAPIServer(HTTPServer):
    """Threaded HTTP server for the Voice Paste API."""

    def __init__(
        self,
        port: int,
        command_handler: Callable[[dict], dict],
    ) -> None:
        self.command_handler = command_handler
        self.rate_limiter = _RateLimiter()
        # ThreadingMixIn equivalent: handle each request in a new thread
        super().__init__(("127.0.0.1", port), VoicePasteAPIHandler)

    # Override to handle each request in a thread (like ThreadingHTTPServer)
    def process_request(self, request, client_address):
        t = threading.Thread(
            target=self.process_request_thread,
            args=(request, client_address),
            daemon=True,
        )
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
```

## Appendix C: `_wait_before_paste()` Implementation Sketch

```python
def _wait_before_paste(self, summary: str) -> bool:
    """Wait before pasting, with countdown and/or Enter confirmation.

    Called from the pipeline worker thread (T2).

    Args:
        summary: The text that will be pasted.

    Returns:
        True if paste should proceed, False if cancelled/timed out.
    """
    delay = self.config.paste_delay_seconds
    require_confirm = self.config.paste_require_confirmation

    # Skip if no delay and no confirmation required
    if delay <= 0 and not require_confirm:
        return True

    self._set_state(AppState.AWAITING_PASTE)

    # Register Escape to cancel
    cancel_event = threading.Event()
    confirm_event = threading.Event()

    def on_cancel():
        cancel_event.set()

    self._hotkey_manager.register_cancel(on_cancel)

    # Register Enter for immediate paste / confirmation
    enter_handle = None
    try:
        enter_handle = kb.add_hotkey("enter", lambda: confirm_event.set(),
                                      suppress=False)
    except Exception as e:
        logger.warning("Could not register Enter hotkey: %s", e)

    try:
        # Phase 1: Countdown delay
        if delay > 0:
            remaining = delay
            self._tray_manager.notify(
                APP_NAME,
                f"Einfuegen in {delay:.0f}s... "
                f"(Escape = abbrechen, Enter = sofort)"
            )

            while remaining > 0:
                if cancel_event.is_set():
                    logger.info("Paste cancelled during countdown.")
                    self._play_audio_cue(play_cancel_cue)
                    self._tray_manager.notify(APP_NAME, "Einfuegen abgebrochen.")
                    return False

                if confirm_event.is_set():
                    logger.info("Paste confirmed early (Enter pressed).")
                    return True

                if self._shutdown_event.is_set():
                    return False

                # Audio beep each second
                if self.config.audio_cues_enabled and remaining == int(remaining):
                    play_countdown_beep()

                time.sleep(0.1)
                remaining -= 0.1

        # Phase 2: Confirmation (if required)
        if require_confirm:
            if not confirm_event.is_set():
                # Reset confirm event in case it was set during countdown
                confirm_event.clear()
                preview = summary[:80] + ("..." if len(summary) > 80 else "")
                self._tray_manager.notify(
                    APP_NAME,
                    f"Enter = einfuegen, Escape = abbrechen\n{preview}"
                )

                timeout = self.config.paste_confirmation_timeout_seconds
                while timeout > 0:
                    if cancel_event.is_set():
                        logger.info("Paste cancelled during confirmation.")
                        self._play_audio_cue(play_cancel_cue)
                        self._tray_manager.notify(APP_NAME, "Einfuegen abgebrochen.")
                        return False

                    if confirm_event.is_set():
                        logger.info("Paste confirmed (Enter pressed).")
                        return True

                    if self._shutdown_event.is_set():
                        return False

                    time.sleep(0.1)
                    timeout -= 0.1

                # Timeout reached
                logger.info("Paste confirmation timed out.")
                self._tray_manager.notify(APP_NAME, "Einfuegen abgebrochen (Zeitlimit).")
                return False

        # No confirmation required, delay completed
        return True

    finally:
        self._hotkey_manager.unregister_cancel()
        if enter_handle is not None:
            try:
                kb.remove_hotkey(enter_handle)
            except Exception:
                pass
```
