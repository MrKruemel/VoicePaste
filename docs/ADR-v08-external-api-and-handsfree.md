# Architecture Decision Record: External API & Hands-Free Mode

**Date**: 2026-02-18
**Status**: Proposed
**Author**: Solution Architect + Product Owner
**Base Version**: 0.5.0 (current), planned through 0.7.0
**Relevant to**: v0.8.0 (External API) and v0.9.0 (Hands-Free Mode)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Feature 1: External API](#2-feature-1-external-api)
   - 2.1 [Product Owner Analysis](#21-product-owner-analysis)
   - 2.2 [User Stories](#22-user-stories)
   - 2.3 [Protocol Decision](#23-protocol-decision)
   - 2.4 [API Surface Design](#24-api-surface-design)
   - 2.5 [Security Model](#25-security-model)
   - 2.6 [Integration with Existing Named Pipe IPC](#26-integration-with-existing-named-pipe-ipc)
   - 2.7 [Enable/Disable Toggle Design](#27-enabledisable-toggle-design)
   - 2.8 [Technical Architecture](#28-technical-architecture)
   - 2.9 [Risk Assessment](#29-risk-assessment)
   - 2.10 [Scope Decision & Release Planning](#210-scope-decision--release-planning)
3. [Feature 2: Hands-Free / Voice-Activated Mode](#3-feature-2-hands-free--voice-activated-mode)
   - 3.1 [Product Owner Analysis](#31-product-owner-analysis)
   - 3.2 [User Stories](#32-user-stories)
   - 3.3 [Wake Word Detection Approach](#33-wake-word-detection-approach)
   - 3.4 [Always-Listening Architecture](#34-always-listening-architecture)
   - 3.5 [Feature Routing After Wake Word](#35-feature-routing-after-wake-word)
   - 3.6 [Silence Detection & Auto-Stop](#36-silence-detection--auto-stop)
   - 3.7 [Privacy Analysis](#37-privacy-analysis)
   - 3.8 [Technical Architecture](#38-technical-architecture)
   - 3.9 [Risk Assessment](#39-risk-assessment)
   - 3.10 [Scope Decision & Release Planning](#310-scope-decision--release-planning)
4. [Cross-Feature Integration Analysis](#4-cross-feature-integration-analysis)
5. [Updated Release Roadmap](#5-updated-release-roadmap)
6. [Open Questions for User](#6-open-questions-for-user)

---

## 1. Executive Summary

Two new features are proposed for Voice Paste:

1. **External API** -- an inter-process communication interface that allows external
   programs to programmatically trigger Voice Paste actions (TTS, recording, status
   queries). This extends Voice Paste from a standalone tool into an automatable
   voice services endpoint.

2. **Hands-Free Mode** ("Hands-Remote") -- a voice-activated mode where the
   application continuously listens for a wake word, then automatically records,
   processes, and responds via TTS. This transforms Voice Paste from a
   hotkey-triggered tool into a voice assistant.

**Key architectural insight**: Both features share a common core requirement -- the
ability to trigger Voice Paste actions from sources other than keyboard hotkeys.
The External API provides programmatic triggers; Hands-Free Mode provides voice
triggers. Both feed into the existing state machine and pipeline.

**Recommended versions**:
- External API: **v0.8.0** (after TTS and Overlay are stable in v0.6/v0.7)
- Hands-Free MVP: **v0.9.0** (requires TTS to be production-ready for the response loop)

---

## 2. Feature 1: External API

### 2.1 Product Owner Analysis

**Wer braucht das?** (Who needs this?)

The External API serves two primary user segments:

1. **Power users / Automation enthusiasts**: People who use tools like AutoHotkey,
   PowerShell scripts, Elgato Stream Deck, or macro software. They want to trigger
   "read this text aloud" or "start recording" from a button press on their Stream
   Deck, or from a script that processes text.

2. **Developers building integrations**: People who want to chain Voice Paste with
   other tools. Example: a browser extension that sends selected webpage text to
   Voice Paste for TTS, or a note-taking app that triggers voice recording.

**Warum jetzt?** (Why now?)

The v0.6 Named Pipe IPC planned for context menu support is already 80% of the work.
The External API is essentially "expose the Named Pipe to external callers with a
documented protocol and a security toggle." The marginal effort on top of the IPC
server is small.

However, the External API should NOT ship before TTS and the core IPC are
battle-tested. A premature API creates a support surface for edge cases in features
that are not yet stable.

**Was ist der minimal nuetzliche Umfang?** (What is the minimally useful scope?)

MVP: Accept `tts`, `stop`, `status`, and `record` commands over a local protocol.
No authentication tokens. Localhost only. Toggle on/off in Settings.

### 2.2 User Stories

#### US-API-1: Trigger TTS from External Program

**Als** Power-User mit einem Stream Deck,
**moechte ich** aus einem externen Programm einen Text an Voice Paste senden und
vorlesen lassen,
**damit** ich waehrend einer Praesentation per Knopfdruck vorbereitete Texte
vorlesen lassen kann.

**Akzeptanzkriterien:**
- [ ] Ein externes Programm kann sich per lokalem Protokoll mit Voice Paste verbinden
- [ ] Der Befehl `{"action": "tts", "text": "Hallo Welt"}` startet die TTS-Wiedergabe
- [ ] Voice Paste antwortet mit `{"status": "ok"}` bei Erfolg
- [ ] Voice Paste antwortet mit `{"status": "error", "message": "..."}` bei Fehler
- [ ] Wenn Voice Paste gerade spricht, antwortet es mit `{"status": "busy"}`
- [ ] Der Befehl funktioniert nur, wenn die API in den Einstellungen aktiviert ist

#### US-API-2: Query Application Status

**Als** Entwickler einer Integration,
**moechte ich** den aktuellen Status von Voice Paste abfragen koennen,
**damit** mein Programm die passende Aktion anzeigen kann (z.B. "Aufnahme laeuft"
im UI meines Tools).

**Akzeptanzkriterien:**
- [ ] Der Befehl `{"action": "status"}` gibt den aktuellen AppState zurueck
- [ ] Antwortformat: `{"status": "ok", "state": "idle", "api_version": "1"}`
- [ ] Moegliche States: idle, recording, processing, pasting, speaking
- [ ] Antwort innerhalb von 100ms

#### US-API-3: Start/Stop Recording

**Als** Automatisierungs-Nutzer,
**moechte ich** eine Aufnahme per API starten und stoppen koennen,
**damit** ich Voice Paste in meine Workflows einbinden kann.

**Akzeptanzkriterien:**
- [ ] `{"action": "record_start", "mode": "summary"}` startet eine Aufnahme
- [ ] `{"action": "record_start", "mode": "prompt"}` startet eine Aufnahme im Prompt-Modus
- [ ] `{"action": "record_stop"}` stoppt die Aufnahme und startet die Pipeline
- [ ] `{"action": "cancel"}` bricht die Aufnahme ab
- [ ] Wenn bereits eine Aufnahme laeuft, antwortet record_start mit `{"status": "busy"}`
- [ ] Der aufgenommene Text wird wie ueblich an der Cursorposition eingefuegt

#### US-API-4: Stop TTS Playback

**Als** Nutzer,
**moechte ich** die TTS-Wiedergabe per API stoppen koennen,
**damit** mein Stream Deck einen "Stopp"-Button haben kann.

**Akzeptanzkriterien:**
- [ ] `{"action": "stop_tts"}` stoppt die aktuelle TTS-Wiedergabe
- [ ] Wenn nichts abgespielt wird, antwortet der Befehl mit `{"status": "ok"}` (idempotent)

#### US-API-5: Security Toggle

**Als** sicherheitsbewusster Nutzer,
**moechte ich** die API deaktivieren koennen,
**damit** keine externen Programme meine Anwendung fernsteuern koennen.

**Akzeptanzkriterien:**
- [ ] In den Einstellungen gibt es einen Toggle "Externe API aktivieren"
- [ ] Der Toggle ist standardmaessig AUS (deaktiviert)
- [ ] Wenn deaktiviert, lehnt der Pipe-Server alle Verbindungen ab
- [ ] Aenderungen am Toggle erfordern keinen Neustart der Anwendung
- [ ] Der Toggle-Status wird in config.toml persistiert
- [ ] Im Overlay wird der API-Status angezeigt (optional, Indikator-Punkt)

### 2.3 Protocol Decision

#### Options Evaluated

| Protocol | Complexity | External Tool Support | Security | Platform |
|----------|------------|----------------------|----------|----------|
| **Named Pipe** (`\\.\pipe\`) | Low | PowerShell, C#, Python, AHK | Same-user DACL | Windows only |
| **REST HTTP (localhost)** | Medium | curl, JS, any language | CORS, port conflicts | Cross-platform |
| **WebSocket (localhost)** | Medium-High | JS, Python | Same as HTTP | Cross-platform |
| **COM Automation** | Very High | VBA, C#, PowerShell | COM security | Windows only |
| **gRPC** | High | Generated clients | TLS optional | Cross-platform |

#### Decision: Named Pipe (primary) + REST HTTP (optional, v0.8.1)

**Primary protocol: Named Pipe** (`\\.\pipe\VoicePasteAPI`)

Rationale:
1. **Already planned**: The v0.6 context menu IPC already requires a Named Pipe
   server (`\\.\pipe\VoicePasteIPC`). The External API reuses the same infrastructure.
2. **Zero new dependencies**: Uses `multiprocessing.connection` (stdlib).
3. **Inherent security**: Windows Named Pipes are scoped to the creating user by
   default (DACL). No port conflicts. No network exposure.
4. **Tool support**: PowerShell (`New-Object System.IO.Pipes.NamedPipeClientStream`),
   Python (`multiprocessing.connection.Client`), C# (`NamedPipeClientStream`),
   AutoHotkey (via DllCall to kernel32).

**Why not HTTP as primary?**

HTTP introduces a listening TCP port on localhost. This:
- Creates port conflicts (another app might use the same port)
- Requires a web framework (Flask, FastAPI, or raw http.server) -- new dependency
- Appears in `netstat` / firewall logs, which may confuse users or trigger security
  software
- Is overkill for local IPC between processes on the same machine

**Optional HTTP adapter (v0.8.1)**: A thin HTTP-to-Pipe bridge could be added later
for tools that cannot easily connect to Named Pipes (e.g., browser extensions,
JavaScript-based tools). This would be a separate small module that listens on
`localhost:18923` (configurable) and forwards JSON commands to the Named Pipe.

**Why not COM?**

COM Automation provides rich Windows integration but requires:
- COM server registration (often needs admin)
- Complex comtypes / pythoncom code
- No cross-platform potential
- The benefits (IntelliSense in VBA) do not justify the complexity for this use case

### 2.4 API Surface Design

#### Pipe Name

```
\\.\pipe\VoicePasteAPI
```

This is SEPARATE from the internal context menu pipe (`\\.\pipe\VoicePasteIPC`).
The internal pipe is always running and handles trusted context menu commands. The
External API pipe is gated behind the `api_enabled` toggle.

Rationale for separation:
- Security: the internal pipe can use a different authkey and is always on.
- The user can disable the External API without breaking context menu functionality.
- Clear logging: commands from the external API are logged differently than
  internal IPC commands.

#### Protocol Specification

```
Transport:    Windows Named Pipe (AF_PIPE)
Encoding:     UTF-8
Framing:      multiprocessing.connection (length-prefixed messages)
Auth:         multiprocessing.connection authkey
Max message:  65536 bytes
Timeout:      5 seconds connection, 10 seconds response
API version:  1
```

#### Command Reference

| Action | Parameters | Description | State Requirement |
|--------|-----------|-------------|-------------------|
| `status` | none | Query current state | Any |
| `tts` | `text` (string, required) | Speak text aloud | IDLE (else busy) |
| `stop_tts` | none | Stop TTS playback | SPEAKING (else ok/noop) |
| `record_start` | `mode` (string: "summary", "prompt", "ask_tts") | Start recording | IDLE (else busy) |
| `record_stop` | none | Stop recording, trigger pipeline | RECORDING (else error) |
| `cancel` | none | Cancel current recording | RECORDING (else noop) |
| `ping` | none | Health check | Any |
| `get_config` | none | Return non-secret config | Any |

#### Request Format

```json
{
  "action": "tts",
  "text": "Dies ist ein Test.",
  "request_id": "abc123"
}
```

The `request_id` is optional. If provided, it is echoed in the response for
correlation.

#### Response Format

```json
{
  "status": "ok",
  "request_id": "abc123",
  "data": {}
}
```

```json
{
  "status": "error",
  "request_id": "abc123",
  "message": "TTS is not configured. Enable TTS in Settings.",
  "error_code": "TTS_NOT_CONFIGURED"
}
```

```json
{
  "status": "busy",
  "request_id": "abc123",
  "state": "recording",
  "message": "Recording is in progress."
}
```

#### Error Codes

| Code | Meaning |
|------|---------|
| `OK` | Success |
| `BUSY` | Another operation is in progress |
| `INVALID_ACTION` | Unknown action |
| `INVALID_PARAMS` | Missing or invalid parameters |
| `TTS_NOT_CONFIGURED` | TTS is disabled or no API key |
| `STT_NOT_CONFIGURED` | No STT backend available |
| `API_DISABLED` | External API is disabled in Settings |
| `INTERNAL_ERROR` | Unexpected error |
| `TEXT_TOO_LONG` | Text exceeds 10,000 character limit |

### 2.5 Security Model

#### Threat Analysis for External API

| Threat | Risk | Mitigation |
|--------|------|------------|
| **Unauthorized local process sends commands** | Medium | Named Pipe DACL (same user). authkey in multiprocessing.connection. API disabled by default. |
| **Malicious process triggers TTS with offensive content** | Low | API requires explicit opt-in. Rate limiting (1 command/second). Log all API commands for audit. |
| **API used to exfiltrate data** | Low | API does not return transcript content, clipboard data, or API keys. `get_config` returns only non-secret settings. |
| **Denial of service via rapid commands** | Low | Rate limiting. Max 1 concurrent connection. Command queue with depth limit. |
| **Process impersonation** | Very Low | Named Pipe runs under the user's session. An attacker with same-user access already owns the system. |

#### Security Controls

1. **API disabled by default** (`api_enabled = false` in config.toml).
2. **Same-user DACL**: Windows Named Pipes inherit the creating process's security
   descriptor. Only processes running as the same user can connect.
3. **authkey**: The `multiprocessing.connection.Listener` uses an authkey for HMAC
   authentication. The authkey is `b"VoicePasteAPI-v1-" + <user SID hash>`. This
   prevents cross-user access even if the DACL is misconfigured.
4. **Rate limiting**: Maximum 1 command per second per connection. Excess commands
   receive `{"status": "error", "error_code": "RATE_LIMITED"}`.
5. **Input validation**: Text for TTS is capped at 10,000 characters. Action strings
   are validated against a whitelist. No eval() or exec() of received data.
6. **Logging**: All API commands are logged at INFO level (without text content for
   TTS commands, to respect REQ-S24/S25). Connection open/close events are logged.
7. **No secret exposure**: The API never returns API keys, audio data, or transcript
   content. `get_config` returns only structural config (hotkeys, providers, model
   names, enabled/disabled toggles).

#### Threat Model Extension

Add to `docs/THREAT-MODEL.md`:

```
### T10: External API Abuse

**Threat**: The External API allows local processes to control Voice Paste. A
malicious process could trigger TTS with unwanted content, start recordings, or
abuse the API for denial-of-service.

**Risk**: LOW -- requires same-user local access, API disabled by default.

**Mitigations**:
- [REQ-S28] API disabled by default. Requires explicit user opt-in.
- [REQ-S29] Same-user DACL on Named Pipe. No network exposure.
- [REQ-S30] Rate limiting on API commands (1/second).
- [REQ-S31] Input validation: whitelist of actions, text length cap.
- [REQ-S32] Audit logging of all API commands (no content logging).
- [REQ-S33] API does not expose secrets, audio, or transcript content.
```

### 2.6 Integration with Existing Named Pipe IPC

The v0.6 ADR already plans a Named Pipe at `\\.\pipe\VoicePasteIPC` for context
menu integration. The External API needs to coexist with this.

#### Design Decision: Single Pipe Server, Dual-Mode Protocol

Instead of running two separate Named Pipe servers, use a **single pipe server**
with the following design:

```
\\.\pipe\VoicePasteIPC  -- internal pipe, always on, no toggle
\\.\pipe\VoicePasteAPI  -- external pipe, gated by api_enabled toggle
```

**Revised decision**: Keep two separate pipes.

Rationale:
1. The internal pipe serves the context menu stub process. It uses a hardcoded
   authkey and is always available. The user cannot and should not disable it (it
   would break context menu).
2. The external pipe serves third-party programs. It uses a different authkey and
   is gated by the toggle. The user can disable it without affecting context menu.
3. Separate pipes allow independent rate limiting, logging, and error handling.
4. The threading cost of a second pipe listener is negligible (one daemon thread,
   blocking read loop).

#### Code Reuse

Both pipes share the same `ipc.py` module. The module provides:

```python
class PipeServer:
    """Named pipe IPC server with command dispatch."""

    def __init__(
        self,
        pipe_name: str,
        authkey: bytes,
        command_handler: Callable[[dict], dict],
        enabled_check: Callable[[], bool] | None = None,
    ) -> None: ...
```

The `enabled_check` callback is polled before accepting connections. For the
internal pipe, it is `None` (always enabled). For the external API pipe, it is
`lambda: config.api_enabled`.

### 2.7 Enable/Disable Toggle Design

#### Toggle Location

The API enable/disable toggle lives in:

1. **Settings dialog**: Under a new "Integration" or "Erweitert" (Advanced)
   section, below the context menu settings.

   ```
   +-- Integration / Erweitert ----------------------------------+
   |                                                              |
   |  [x] Windows-Kontextmenue "Vorlesen mit Voice Paste"        |
   |      [Installieren]  [Deinstallieren]                        |
   |                                                              |
   |  [ ] Externe API aktivieren                                  |
   |      Erlaubt anderen Programmen, Voice Paste zu steuern.     |
   |      Pipe: \\.\pipe\VoicePasteAPI                            |
   |                                                              |
   +--------------------------------------------------------------+
   ```

2. **Config.toml**:

   ```toml
   [integration]
   context_menu_installed = false
   api_enabled = false
   ```

3. **Overlay** (optional, v0.8.1): A small indicator dot that shows whether the
   API is active. Not a button -- the overlay should remain minimal.

#### Hot-Reload Behavior

When the user toggles `api_enabled`:
- **ON**: The API pipe server thread is started (if not already running). The pipe
  begins accepting connections.
- **OFF**: The API pipe server stops accepting new connections. Existing connections
  are terminated gracefully (send `{"status": "error", "error_code": "API_DISABLED"}`
  then close). The pipe server thread exits.

No application restart required.

### 2.8 Technical Architecture

#### Component Diagram (API Addition)

```
+------------------------------------------------------------------+
|                        main.py                                    |
|                                                                   |
|  +------------------+     +---------------------+                 |
|  |  Hotkey Manager  |---->|  State Machine      |<--- [NEW]       |
|  | (keyboard lib)   |     |  (AppState)         |<--- API Pipe    |
|  +------------------+     +-----+---------------+                 |
|                                 |                                 |
|  [NEW] +--------------------+   |                                 |
|        | API Pipe Server    |---+                                  |
|        | Thread 7           |                                     |
|        | \\.\pipe\           |                                     |
|        | VoicePasteAPI      |                                     |
|        +--------------------+                                     |
|                                                                   |
|  [v0.6] +-------------------+                                     |
|         | IPC Pipe Server   |  (context menu, always on)          |
|         | Thread 6          |                                     |
|         | \\.\pipe\          |                                     |
|         | VoicePasteIPC     |                                     |
|         +-------------------+                                     |
+------------------------------------------------------------------+
```

#### Threading Model Update

```
Main Thread:     pystray event loop (system tray)
Thread 1:        keyboard hotkey listener (daemon)
Thread 2:        Pipeline worker (per session, daemon)
Thread 3:        Settings dialog tkinter (on demand)
Thread 4:        Overlay window tkinter (persistent, v0.7)
Thread 5:        TTS playback (per playback, daemon, v0.6)
Thread 6:        IPC pipe server (persistent, daemon, v0.6)
Thread 7: [NEW]  API pipe server (persistent when enabled, daemon)
```

#### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `src/api_server.py` | External API pipe server + command dispatch | ~250 |
| `tests/test_api_server.py` | API server unit tests | ~200 |
| `docs/API.md` | External API documentation for developers | ~150 |

#### Modified Files

| File | Changes |
|------|---------|
| `src/ipc.py` | Refactor into reusable `PipeServer` class (~50 lines changed) |
| `src/config.py` | Add `api_enabled: bool = False` field |
| `src/constants.py` | Add `API_PIPE_NAME`, `API_AUTHKEY_PREFIX`, `API_RATE_LIMIT_SECONDS` |
| `src/main.py` | Initialize API server, wire command dispatch (~60 new lines) |
| `src/settings_dialog.py` | Add API toggle in Integration section (~30 new lines) |

#### Command Dispatch in main.py

```python
def _handle_api_command(self, command: dict) -> dict:
    """Dispatch an External API command to the appropriate handler.

    Called from the API pipe server thread. Must be thread-safe.
    All actions funnel through the same state machine checks
    as hotkey callbacks.

    Args:
        command: Parsed JSON command dict.

    Returns:
        Response dict to send back to the client.
    """
    action = command.get("action", "")
    request_id = command.get("request_id", "")

    if action == "status":
        return {
            "status": "ok",
            "request_id": request_id,
            "data": {
                "state": self.state.value,
                "api_version": "1",
                "app_version": APP_VERSION,
            },
        }

    if action == "tts":
        text = command.get("text", "")
        if not text or not text.strip():
            return {"status": "error", "error_code": "INVALID_PARAMS",
                    "message": "text is required", "request_id": request_id}
        if len(text) > TTS_MAX_TEXT_LENGTH:
            return {"status": "error", "error_code": "TEXT_TOO_LONG",
                    "request_id": request_id}
        if self.state != AppState.IDLE:
            return {"status": "busy", "state": self.state.value,
                    "request_id": request_id}
        # Trigger TTS pipeline (non-blocking, spawns thread)
        threading.Thread(
            target=self._run_tts_pipeline,
            args=(text.strip(),),
            daemon=True,
        ).start()
        return {"status": "ok", "request_id": request_id}

    # ... similar for record_start, record_stop, cancel, stop_tts, ping
```

### 2.9 Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| **Named Pipe authkey brute-force** | Very Low | HMAC-based. 16+ byte key. Same-user DACL already prevents cross-user access. |
| **API pipe server crashes, orphaned thread** | Low | Wrap listener loop in try/except with automatic restart (3 attempts, then give up with tray notification). |
| **Rate limiting too aggressive for legitimate use** | Low | Default 1/second is generous for human-triggered automation. Document that batch operations should use sequential calls with small delays. |
| **PowerShell can't connect to multiprocessing.connection pipe** | Medium | multiprocessing.connection uses a custom framing protocol (length-prefix + HMAC). Raw Named Pipe clients (PowerShell, C#) would need a helper library. **Mitigation**: Ship a `voicepaste-client.py` CLI script and document the wire protocol. For PowerShell, provide a `VoicePaste-Client.ps1` script. |
| **API toggle in Settings is not discoverable** | Low | Add tray menu item "API: ON/OFF" (read-only indicator). Mention API in README. |
| **Breaking changes in API protocol** | Medium | Version the protocol (api_version field). Document backward compatibility commitment. |

**Critical risk -- multiprocessing.connection interoperability**:

The `multiprocessing.connection` module uses a proprietary framing format (HMAC
challenge-response + length-prefixed messages). This means:

- Python clients work out of the box.
- Non-Python clients (PowerShell, C#, AHK) CANNOT connect directly.

**Mitigation options** (pick one):

1. **Ship a CLI tool**: `voicepaste-api.exe` (PyInstaller-built from a 20-line
   Python script) that wraps `multiprocessing.connection.Client`. External tools
   call `voicepaste-api.exe --tts "Hello world"` via subprocess.

2. **Use raw Win32 Named Pipes instead of multiprocessing.connection**: Implement
   the pipe server with `ctypes` + `kernel32.CreateNamedPipeW`. This makes the wire
   protocol a simple `length_u32 + json_bytes` that any language can implement.

3. **Add a secondary HTTP endpoint**: A localhost HTTP server that any tool can call
   with curl. More universal but adds a dependency and port management.

**Recommendation**: Option 2 (raw Win32 Named Pipes) for the External API pipe.
Keep `multiprocessing.connection` for the internal IPC pipe (where both ends are
Python). The External API pipe should use a simple framing protocol:

```
Frame format:
  [4 bytes: big-endian uint32 payload length]
  [N bytes: UTF-8 JSON payload]
```

This is trivially implementable in any language. No HMAC challenge. Security is
provided by the Named Pipe DACL (same-user access only) and the `api_enabled`
toggle.

### 2.10 Scope Decision & Release Planning

#### Version Assignment: v0.8.0

**Why not v0.6 or v0.7?**

- v0.6 is TTS + IPC (internal pipe). The TTS and internal IPC must be stable before
  we expose an external API that depends on them.
- v0.7 is the Overlay UI. The overlay introduces the UI surface where the API toggle
  lives.
- v0.8 is the right time: TTS works, IPC works, overlay works, and we can build
  the External API on proven infrastructure.

#### MVP Scope (v0.8.0)

1. Raw Win32 Named Pipe server with simple framing (4-byte length + JSON).
2. Commands: `ping`, `status`, `tts`, `stop_tts`, `record_start`, `record_stop`,
   `cancel`.
3. API disabled by default. Toggle in Settings.
4. Rate limiting (1 command/second).
5. `voicepaste-api.py` CLI client script (not bundled into main .exe).
6. API documentation (`docs/API.md`).

#### Post-MVP (v0.8.1+)

- Optional REST HTTP adapter (`localhost:18923`).
- PowerShell client script (`VoicePaste-Client.ps1`).
- Stream Deck plugin (community contribution).
- `get_config` and `set_config` commands (limited to non-secret settings).
- Event subscription (client registers for state change notifications via the pipe).

#### Estimated Effort

| Phase | Days |
|-------|------|
| Refactor ipc.py into reusable PipeServer | 0.5 |
| Implement api_server.py with raw Win32 pipe | 2 |
| Wire into main.py + settings_dialog.py | 1 |
| CLI client script + documentation | 1 |
| Tests | 1 |
| **Total** | **5.5 days** |

---

## 3. Feature 2: Hands-Free / Voice-Activated Mode

### 3.1 Product Owner Analysis

**Wer braucht das?** (Who needs this?)

1. **Accessibility users**: People who cannot reliably use a keyboard. A wake word
   lets them use Voice Paste entirely by voice.
2. **Hands-busy professionals**: Surgeons, mechanics, cooks, artists -- anyone whose
   hands are occupied. They need to dictate notes or ask questions without touching
   a keyboard.
3. **Smart-speaker-like experience**: Users who want a desktop voice assistant that
   responds to "Hey Voice Paste, what is...?" and speaks the answer.

**Warum ist das komplex?** (Why is this complex?)

This feature is fundamentally different from everything Voice Paste does today:

- Today: The user PUSHES a trigger (hotkey/button). Explicit, intentional, discrete.
- Hands-Free: The app PULLS triggers from continuous audio. Implicit, continuous,
  probabilistic.

This introduces entirely new categories of problems:
- False activations (the app thinks it heard the wake word but didn't)
- Privacy (microphone is always on)
- CPU/battery usage (continuous audio processing)
- Feature routing (how does the app know WHAT the user wants to do after the wake word?)
- Background noise handling
- The "listening" vs "not listening" UX feedback

**Was ist der minimal nuetzliche Umfang?** (What is the minimally useful scope?)

MVP: Single wake word -> auto-record -> STT -> LLM -> TTS response (the "Ask AI +
TTS" pipeline). This is the most natural hands-free flow: ask a question by voice,
hear the answer by voice. No feature routing in MVP -- the wake word always triggers
the Ask AI + TTS pipeline.

### 3.2 User Stories

#### US-HF-1: Wake Word Detection

**Als** Nutzer mit beschaeftigten Haenden,
**moechte ich** ein Aktivierungswort sagen koennen (z.B. "Hey Voice Paste"),
**damit** ich Voice Paste starten kann, ohne eine Taste druecken zu muessen.

**Akzeptanzkriterien:**
- [ ] Das Aktivierungswort ist konfigurierbar (Standard: "Hey Voice Paste")
- [ ] Die Erkennung funktioniert aus mindestens 2 Metern Entfernung
- [ ] Die Erkennung funktioniert bei normaler Hintergrundlautsthaerke (Buero, ~45 dB)
- [ ] Die Falsch-Positiv-Rate liegt unter 1 pro Stunde bei normalem Gespraech
- [ ] Die Falsch-Negativ-Rate liegt unter 10% bei deutlicher Aussprache
- [ ] Die Erkennung erfolgt innerhalb von 500ms nach Ende des Aktivierungswortes
- [ ] Ein akustisches Signal bestaetigt die Erkennung (der Standard-Aufnahme-Ton)

#### US-HF-2: Automatic Silence-Based Stop

**Als** Nutzer,
**moechte ich** dass die Aufnahme automatisch endet, wenn ich aufhoere zu sprechen,
**damit** ich kein zweites Mal ein Kommando geben oder eine Taste druecken muss.

**Akzeptanzkriterien:**
- [ ] Nach Erkennung des Aktivierungswortes wird automatisch aufgenommen
- [ ] Die Aufnahme endet automatisch nach X Sekunden Stille (konfigurierbar, Standard: 2s)
- [ ] Kurze Pausen (<1.5s) innerhalb eines Satzes unterbrechen die Aufnahme NICHT
- [ ] Die maximale Aufnahmedauer betraegt 60 Sekunden (kuenzer als der Standard von 5 Min)
- [ ] Nach Ende der Aufnahme wird die Pipeline automatisch gestartet
- [ ] Der Nutzer kann die Aufnahme jederzeit mit "Escape" oder dem Hotkey abbrechen

#### US-HF-3: Voice-Activated Ask AI + TTS

**Als** Nutzer,
**moechte ich** eine Frage stellen und die Antwort vorgelesen bekommen,
**damit** ich ein vollstaendig freihsaendiges Frage-Antwort-Erlebnis habe.

**Akzeptanzkriterien:**
- [ ] Aktivierungswort -> Aufnahme -> STT -> LLM -> TTS -> Audiowiedergabe
- [ ] Der gesamte Ablauf erfordert keinen Tastendruck
- [ ] Die Antwort wird ueber die Lautsprecher vorgelesen
- [ ] Die Antwort wird zusaetzlich in die Zwischenablage kopiert
- [ ] Waehrend der Wiedergabe kann der Nutzer eine neue Frage starten
  (neues Aktivierungswort oder Hotkey stoppt TTS und startet neue Aufnahme)

#### US-HF-4: Feature Routing via Voice Command (Post-MVP)

**Als** fortgeschrittener Nutzer,
**moechte ich** nach dem Aktivierungswort per Sprachbefehl waehlen, welche Funktion
ausgefuehrt wird,
**damit** ich alle Voice-Paste-Funktionen freishaendig nutzen kann.

**Akzeptanzkriterien:**
- [ ] "Hey Voice Paste, schreibe" -> Aufnahme -> STT -> Zusammenfassung -> Paste
- [ ] "Hey Voice Paste, frage" -> Aufnahme -> STT -> LLM Prompt -> Paste
- [ ] "Hey Voice Paste, lies vor" -> Zwischenablage -> TTS -> Audiowiedergabe
- [ ] "Hey Voice Paste, frage und lies vor" -> Aufnahme -> STT -> LLM -> TTS
- [ ] Unerkannte Befehle werden als "frage und lies vor" interpretiert (Default)
- [ ] Die Befehlswoerter sind konfigurierbar

#### US-HF-5: Privacy & Resource Controls

**Als** datenschutzbewusster Nutzer,
**moechte ich** den Hands-Free-Modus jederzeit ein- und ausschalten koennen
und verstehen, wann mein Mikrofon aktiv zuhoert,
**damit** ich die Kontrolle ueber meine Privatsphaere behalte.

**Akzeptanzkriterien:**
- [ ] Der Hands-Free-Modus ist standardmaessig AUS (deaktiviert)
- [ ] Ein deutliches visuelles Signal zeigt an, wenn der Always-Listening-Modus aktiv ist
  (z.B. Tray-Icon mit Ohr-Symbol, Overlay mit "Lausche..."-Indikator)
- [ ] Der Modus kann per Toggle in Settings, Overlay und Tray-Menue aktiviert/deaktiviert werden
- [ ] Ein Hotkey (z.B. Ctrl+Alt+H) schaltet den Hands-Free-Modus sofort um
- [ ] Im deaktivierten Zustand verarbeitet keine Komponente Mikrofondaten
- [ ] CPU-Auslastung im Listening-Modus liegt unter 5% auf einem modernen PC
- [ ] Kein Audio wird in die Cloud gesendet, bis das Aktivierungswort erkannt wurde
  (Wake-Word-Erkennung erfolgt lokal)

### 3.3 Wake Word Detection Approach

#### Options Evaluated

| Approach | Quality | CPU Usage | Binary Size | Cost | Offline | Custom Words |
|----------|---------|-----------|-------------|------|---------|-------------|
| **Porcupine (Picovoice)** | Excellent | <1% | ~5 MB | Free (personal), $6k/yr (commercial) | Yes | Yes (via console) |
| **openWakeWord** | Good | 2-3% | ~15 MB (onnxruntime) | Free (Apache 2.0) | Yes | Yes (training notebook) |
| **Vosk keyword spotting** | Fair | 3-5% | ~50 MB (model) | Free (Apache 2.0) | Yes | Limited |
| **Continuous Whisper STT** | Fair | 10-30% | 150+ MB | Free (local) or API cost | Depends | Any phrase |
| **Simple energy-based VAD** | Poor | <1% | 0 MB | Free | Yes | No (not a wake word) |

#### Decision: openWakeWord (primary) with Porcupine as alternative

**Primary: openWakeWord**

Rationale:
1. **Fully open source** (Apache 2.0). No licensing fees, no per-user limits, no
   commercial restrictions. Porcupine's free tier is personal/non-commercial only;
   commercial use costs $6,000/year.
2. **Custom wake words**: Users can train custom wake words via a Google Colab
   notebook. Custom models are ~200 KB ONNX files. This enables "Hey Voice Paste"
   or any other phrase the user wants.
3. **Reasonable CPU usage**: 2-3% on a modern CPU for a single model. Processes
   80ms audio frames. Far cheaper than continuous Whisper STT.
4. **Good accuracy**: False-accept rate <0.5/hour, false-reject rate <5%. Adequate
   for a desktop tool. Not as accurate as Porcupine, but does not require a
   cloud-based console for custom wake word creation.
5. **onnxruntime dependency**: openWakeWord requires onnxruntime. This is already
   a known entity in the project (faster-whisper's VAD filter uses onnxruntime).
   The onnxruntime DLL is already bundled in the Local STT build target.

**Why not Porcupine?**

Porcupine is technically superior (lower CPU, higher accuracy, smaller binary). But:
- The free tier is restricted to personal/non-commercial projects. Voice Paste is
  distributed as a free tool, but some users may use it commercially. Porcupine's
  ToS could create legal risk for those users.
- Custom wake word creation requires the Picovoice Console (cloud service). With
  openWakeWord, everything is local and self-hosted.
- The AccessKey requirement means an additional API key for users to manage.

**However**: Porcupine should be offered as an **alternative backend** for users
who prefer it (and accept the licensing terms). The wake word detection should
follow the same Protocol pattern used for STT and TTS.

**Why not continuous Whisper STT?**

Running Whisper continuously to detect a wake phrase would consume 10-30% CPU and
requires either the local model (150+ MB RAM) or continuous API calls (expensive).
The transcription quality is excellent, but the resource cost is prohibitive for
an always-on listening mode. Wake word detection should be a lightweight classifier,
not a full STT engine.

#### Binary Size Impact

| Component | Size Impact |
|-----------|------------|
| openWakeWord package | ~0.5 MB (Python) |
| onnxruntime (already bundled in local STT build) | 0 MB additional |
| onnxruntime (cloud-only build -- NEW) | +30-40 MB |
| Pre-trained wake word models | ~5 MB per model |
| Custom wake word model | ~0.2 MB per model |

**Key concern**: The cloud-only build target currently does NOT include onnxruntime.
Adding Hands-Free Mode to the cloud-only build would require bundling onnxruntime
(+30-40 MB). This is a meaningful size increase.

**Mitigation**: Hands-Free Mode is only available in the `-Local` build (which
already includes onnxruntime). For the cloud-only build, Hands-Free Mode is
disabled with a message: "Hands-Free Mode requires the Local build."

Alternatively: Make onnxruntime a shared optional dependency. If the user has
installed the Local build, onnxruntime is available for both local STT and
wake word detection. This is already how the VAD filter works.

### 3.4 Always-Listening Architecture

#### Audio Pipeline Design

```
Microphone (sounddevice InputStream)
    |
    | 80ms audio frames (16kHz, mono, int16)
    |
    v
+---------------------+
| Wake Word Detector  |  (openWakeWord, runs on Thread 8)
| - processes 80ms    |
|   frames            |
| - returns score     |
|   0.0 to 1.0        |
| - threshold: 0.5    |
|   (configurable)    |
+--------+------------+
         |
    score > threshold?
         |
    +----+----+
    | NO      | YES
    |         |
    v         v
 (discard)  Trigger!
              |
              v
    +-------------------+
    | State transition: |
    | LISTENING -> RECORDING |
    | - play audio cue  |
    | - start silence   |
    |   detection timer |
    +--------+----------+
             |
             | audio frames continue flowing
             |
             v
    +---------------------+
    | Silence Detector    |
    | (VAD / energy-based)|
    | - tracks speech     |
    |   activity          |
    | - triggers stop     |
    |   after 2s silence  |
    +--------+------------+
             |
        2s silence detected
             |
             v
    State: PROCESSING
    (same pipeline as hotkey-triggered)
```

#### Microphone Sharing Problem

**Critical design question**: Can the wake word detector and the recording pipeline
share the same microphone stream?

**Answer**: YES, with careful design.

Currently, `AudioRecorder` opens its own `sounddevice.InputStream` when recording
starts and closes it when recording stops. In Hands-Free Mode, the microphone must
be open continuously (for wake word detection). When a wake word is detected, the
same audio stream should seamlessly transition into recording mode.

**Design**:

```python
class ContinuousAudioStream:
    """Manages a persistent microphone stream for both wake word
    detection and recording.

    In LISTENING state: audio frames are forwarded to the wake word
    detector only. No storage.

    In RECORDING state: audio frames are stored in the recording
    buffer AND no longer forwarded to the wake word detector.
    """

    def __init__(self) -> None:
        self._stream: sd.InputStream = None
        self._mode: str = "listening"  # "listening" or "recording"
        self._wake_detector: WakeWordDetector = None
        self._recording_buffer: list[np.ndarray] = []
```

This replaces `AudioRecorder` when Hands-Free Mode is active. When Hands-Free
Mode is inactive, the existing `AudioRecorder` is used unchanged.

**Alternative (simpler but less elegant)**: Keep `AudioRecorder` as-is. The
wake word detector opens its own `sounddevice.InputStream` at low quality
(8kHz mono, minimal buffer). When a wake word is detected, the detector's stream
is closed and `AudioRecorder.start()` opens a new high-quality stream (16kHz).
This introduces a 50-100ms gap in audio capture (the gap between streams), which
means the first word after the wake word might be partially cut off.

**Recommendation**: Start with the simpler approach (separate streams, small gap).
The 50-100ms gap is barely noticeable in practice because users naturally pause
briefly after the wake word. If user feedback indicates the gap is a problem,
refactor to the shared stream approach.

### 3.5 Feature Routing After Wake Word

#### MVP Approach: Single Wake Word, Single Feature

In the MVP, the wake word ALWAYS triggers the "Ask AI + TTS" pipeline:

```
Wake word detected
  -> Start recording (auto-stop on silence)
  -> STT -> LLM Prompt -> TTS -> Speak answer
```

This is the most natural hands-free flow. It is also the simplest to implement
because there is no routing decision to make.

**Why "Ask AI + TTS" as the default?**

- "Transcribe + Paste" makes no sense hands-free (the user's hands are not at the
  keyboard to see the paste result).
- "Read clipboard" does not require recording (no wake word needed -- just a
  hotkey/button).
- "Ask AI + TTS" is the only pipeline that is fully hands-free from input to output.

#### Post-MVP Approach: Voice Command Routing

After the wake word is detected and the audio cue plays, the user speaks a command
+ content in a single utterance. The system transcribes the full utterance, then
uses keyword matching (not LLM, to avoid latency) to route to the correct pipeline.

**Command keywords** (configurable):

| Keyword(s) | Pipeline | Action |
|-------------|----------|--------|
| "schreibe", "tippe", "diktiere" | Transcribe + Paste | STT -> clean -> paste |
| "frage", "was ist", "erklaere" | Ask AI + TTS | STT -> LLM -> TTS |
| "lies vor", "lese" | Clipboard -> TTS | Read clipboard -> TTS |
| (no keyword / default) | Ask AI + TTS | STT -> LLM -> TTS |

**Implementation**: Simple string prefix matching on the transcript. Not an LLM
call (too slow). The command keyword is stripped from the transcript before sending
to the pipeline.

```python
def route_command(transcript: str) -> tuple[str, str]:
    """Route a voice command to a pipeline.

    Returns:
        (pipeline_name, cleaned_transcript)
    """
    lower = transcript.lower().strip()

    WRITE_KEYWORDS = ("schreibe", "tippe", "diktiere", "notiere")
    READ_KEYWORDS = ("lies vor", "lese vor", "lies")

    for kw in WRITE_KEYWORDS:
        if lower.startswith(kw):
            return "summary", transcript[len(kw):].strip()

    for kw in READ_KEYWORDS:
        if lower.startswith(kw):
            return "clipboard_tts", ""

    # Default: treat everything as a question
    return "ask_tts", transcript
```

This is intentionally simple. LLM-based intent classification would be more
accurate but adds 1-3 seconds of latency, which defeats the purpose of a snappy
voice assistant.

### 3.6 Silence Detection & Auto-Stop

#### Approach: Energy-Based VAD with Configurable Timeout

**Why not Silero VAD?**

Silero VAD (already used in faster-whisper) is designed for pre-filtering audio
before batch transcription. It is not designed for real-time "is the user still
speaking?" detection with sub-second latency. It could be adapted, but a simpler
energy-based approach is more responsive for this use case.

**Algorithm**:

```python
class SilenceDetector:
    """Detects silence in an audio stream for auto-stop.

    Uses RMS (Root Mean Square) energy of audio frames to determine
    speech activity. When energy drops below the threshold for longer
    than the timeout, triggers stop.
    """

    def __init__(
        self,
        silence_threshold_db: float = -40.0,
        silence_timeout_seconds: float = 2.0,
        min_speech_duration_seconds: float = 0.5,
    ) -> None:
        self._threshold_rms = 10 ** (silence_threshold_db / 20) * 32768
        self._timeout = silence_timeout_seconds
        self._min_speech = min_speech_duration_seconds
        self._silence_start: float | None = None
        self._speech_detected: bool = False

    def process_frame(self, frame: np.ndarray) -> bool:
        """Process an audio frame and return True if silence timeout reached.

        Args:
            frame: Audio frame (int16 numpy array).

        Returns:
            True if silence timeout has been reached and we should stop.
        """
        rms = np.sqrt(np.mean(frame.astype(np.float32) ** 2))

        if rms > self._threshold_rms:
            # Speech detected
            self._speech_detected = True
            self._silence_start = None
            return False

        # Silence detected
        if not self._speech_detected:
            # Haven't heard any speech yet -- don't start the timer
            return False

        now = time.monotonic()
        if self._silence_start is None:
            self._silence_start = now
            return False

        return (now - self._silence_start) >= self._timeout
```

**Configuration**:

```toml
[handsfree]
enabled = false
wake_word = "hey_voice_paste"
silence_timeout = 2.0          # seconds of silence before auto-stop
silence_threshold_db = -40     # RMS threshold in dB
max_recording_seconds = 60     # shorter than normal mode (5 min)
```

### 3.7 Privacy Analysis

#### Always-Listening Microphone: Privacy Implications

This is the most significant privacy decision in the project's history. An
always-on microphone is fundamentally different from a push-to-talk system.

**What data flows where in Hands-Free Mode?**

```
Microphone audio (continuous)
    |
    v
[LOCAL] Wake word detector (openWakeWord, onnxruntime)
    |
    | 80ms frames processed locally
    | NO audio sent to cloud
    | NO audio stored on disk
    | Audio frames discarded after processing
    |
    v
Wake word detected?
    |
    +-- NO: Frame discarded. No trace.
    |
    +-- YES:
        |
        v
    Recording starts (in-memory buffer)
        |
        v
    Recording stops (silence detection)
        |
        v
    [CLOUD or LOCAL] Audio sent to STT backend
    (same as current hotkey-triggered flow)
```

**Privacy guarantees**:

1. **Wake word detection is 100% local**. No audio is sent anywhere until the wake
   word is detected. The openWakeWord model runs entirely in-process using
   onnxruntime. No network requests.

2. **Pre-wake-word audio is never stored**. Each 80ms frame is processed by the
   wake word detector and immediately discarded. There is no buffer, no ring
   buffer, no "last N seconds" cache.

3. **The microphone stream is indistinguishable from recording apps**. Windows shows
   a microphone indicator in the taskbar when any app accesses the microphone. Users
   will see this indicator while Hands-Free Mode is active. This is correct and
   transparent behavior.

4. **No ambient audio reaches the cloud**. Only the user's intentional speech
   (after wake word + during recording + before silence timeout) is sent to the
   STT backend. Background conversations, TV audio, etc. are NOT transcribed.

#### Privacy Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **False wake word activation sends ambient speech to cloud** | Medium | Tune threshold. Default 0.5 is conservative. User-adjustable. Add a 500ms "confirmation tone" after wake word -- user can say "cancel" within 2s to abort. |
| **User forgets Hands-Free is on, sensitive conversation is captured** | High | Prominent visual indicator (tray icon changes, overlay shows "Lausche..."). Toast notification when mode is enabled. Auto-disable after 4 hours of inactivity (configurable). |
| **Windows microphone indicator creates surveillance perception** | Medium | Document clearly in README. The indicator is a Windows feature, not a bug. Offer guidance on explaining to colleagues. |
| **onnxruntime processes audio but could theoretically leak it** | Very Low | onnxruntime is open source and well-audited. No network access. |

#### GDPR / Privacy Section Update

Add to README and THREAT-MODEL:

```
### Hands-Free Mode Privacy

When Hands-Free Mode is enabled:
- The microphone is continuously active (Windows shows the microphone indicator).
- Audio is processed LOCALLY by the wake word detector. No audio is sent to any
  cloud service until the wake word is detected.
- Each 80ms audio frame is processed and immediately discarded. No audio is stored
  or buffered until the wake word is recognized.
- After wake word detection, recording begins. This recorded audio follows the same
  data flow as hotkey-triggered recordings (sent to your configured STT backend).
- You can disable Hands-Free Mode at any time via Settings, Overlay, or the
  Ctrl+Alt+H hotkey.
```

### 3.8 Technical Architecture

#### New State: LISTENING

The state machine gains a new state:

```
                              LISTENING (new)
                                  |
                           Wake word detected
                                  |
                                  v
IDLE --> RECORDING --> PROCESSING --> PASTING --> IDLE
                                  |
                                  v
                              SPEAKING --> IDLE
```

**LISTENING** is a sub-state of IDLE. The microphone is active but no recording is
happening. The app is waiting for the wake word.

Implementation: Rather than adding LISTENING to the AppState enum (which would
require changes across all state-dependent code), model it as a boolean flag:

```python
self._handsfree_active: bool = False  # Separate from AppState
```

When `_handsfree_active` is True and `state` is IDLE, the wake word detector is
running. When a wake word is detected, the normal IDLE -> RECORDING transition
occurs. The flag does not interact with the state machine -- it is purely a "is the
wake word detector running?" indicator.

This avoids changing the AppState enum and all the downstream code that depends on it
(tray icon colors, overlay button states, hotkey guards, etc.).

#### Threading Model Update

```
Main Thread:     pystray event loop (system tray)
Thread 1:        keyboard hotkey listener (daemon)
Thread 2:        Pipeline worker (per session, daemon)
Thread 3:        Settings dialog tkinter (on demand)
Thread 4:        Overlay window tkinter (persistent, v0.7)
Thread 5:        TTS playback (per playback, daemon, v0.6)
Thread 6:        IPC pipe server (persistent, daemon, v0.6)
Thread 7:        API pipe server (when enabled, daemon, v0.8)
Thread 8: [NEW]  Wake word listener (when handsfree enabled, daemon)
```

Thread 8 runs a continuous loop:

```python
def _wake_word_listener(self) -> None:
    """Continuous wake word detection loop. Runs on Thread 8.

    Opens a low-priority sounddevice InputStream and feeds 80ms frames
    to the openWakeWord model. When a detection occurs, triggers the
    recording pipeline.
    """
    import openwakeword
    from openwakeword.model import Model as OWWModel

    model = OWWModel(
        wakeword_models=[self.config.wake_word_model_path],
        inference_framework="onnx",
    )

    FRAME_SIZE = 1280  # 80ms at 16kHz
    stream = sd.InputStream(
        samplerate=16000,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SIZE,
    )
    stream.start()

    try:
        while self._handsfree_active and not self._shutdown_event.is_set():
            if self.state != AppState.IDLE:
                # Don't detect wake words during recording/processing/speaking
                time.sleep(0.1)
                continue

            frame, overflowed = stream.read(FRAME_SIZE)
            if overflowed:
                logger.debug("Wake word stream overflow (non-critical).")

            prediction = model.predict(frame.flatten())

            for model_name, score in prediction.items():
                if score > self.config.wake_word_threshold:
                    logger.info(
                        "Wake word detected: %s (score=%.3f)",
                        model_name, score,
                    )
                    self._on_wake_word_detected()
                    # Cool-down: don't re-detect for 3 seconds
                    time.sleep(3.0)
                    break
    finally:
        stream.stop()
        stream.close()
```

#### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `src/wake_word.py` | Wake word detector abstraction + openWakeWord impl | ~200 |
| `src/silence_detector.py` | RMS-based silence detection for auto-stop | ~80 |
| `src/continuous_audio.py` | Shared audio stream management (v0.9.1) | ~150 |
| `tests/test_wake_word.py` | Wake word detector tests (mocked model) | ~120 |
| `tests/test_silence_detector.py` | Silence detector unit tests | ~80 |

#### Modified Files

| File | Changes |
|------|---------|
| `src/main.py` | Add `_handsfree_active`, `_wake_word_listener()`, `_on_wake_word_detected()`, silence detection in recording loop, handsfree toggle. ~100 new lines |
| `src/audio.py` | Add optional `silence_callback` parameter to support auto-stop on silence. ~30 new lines |
| `src/config.py` | Add handsfree config fields (enabled, wake_word, thresholds, timeouts). ~30 new lines |
| `src/constants.py` | Add handsfree constants (defaults, model paths). ~20 new lines |
| `src/settings_dialog.py` | Add Hands-Free section (toggle, wake word, thresholds). ~60 new lines |
| `src/tray.py` | Add Hands-Free toggle to tray menu. Visual indicator when listening. ~20 new lines |
| `src/overlay.py` | Add listening indicator. ~15 new lines |
| `src/hotkey.py` | Add Ctrl+Alt+H for handsfree toggle. ~20 new lines |

#### Config.toml Additions

```toml
[handsfree]
# Enable Hands-Free / Voice-Activated Mode (default: false)
# When enabled, the app continuously listens for a wake word.
# PRIVACY NOTE: The microphone is active while this mode is enabled.
# Wake word detection is 100% local -- no audio is sent to any cloud
# service until the wake word is detected and recording starts.
enabled = false
# Wake word model file (relative to models directory or absolute path)
# Default: "hey_voice_paste" (bundled pre-trained model)
wake_word = "hey_voice_paste"
# Detection sensitivity threshold (0.0 to 1.0, higher = more strict)
# Lower values = more detections (more false positives)
# Higher values = fewer detections (more false negatives)
threshold = 0.5
# Seconds of silence after speech before auto-stopping recording
silence_timeout = 2.0
# Maximum recording duration in Hands-Free Mode (seconds)
max_recording_seconds = 60
# Default pipeline after wake word detection
# Options: "ask_tts" (ask AI, hear answer), "summary" (transcribe + paste),
#          "prompt" (ask AI + paste)
default_pipeline = "ask_tts"
# Auto-disable Hands-Free after N hours of inactivity (0 = never)
auto_disable_hours = 4
```

### 3.9 Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| **openWakeWord model quality insufficient for German** | Medium | openWakeWord is trained primarily on English. Custom German wake words need to be trained. Provide a pre-trained "hey voice paste" model. Test with German speakers. Fall back to Porcupine if quality is poor. |
| **onnxruntime crashes in PyInstaller bundle** | Medium | Already a known issue (VAD filter crashes in frozen exe). Same mitigation: test thoroughly, provide disable toggle, log detailed diagnostics. |
| **CPU usage too high for always-on mode** | Low | openWakeWord uses <3% CPU for a single model. Monitor in testing. If too high, reduce frame rate or use a simpler model. |
| **False activations in noisy environments** | Medium | Adjustable threshold. Default 0.5 is conservative. Document that noisy environments may require higher thresholds. Add a "confirmation beep + 2s cancel window" after detection. |
| **User forgets mode is on, privacy concern** | High | Prominent tray icon change. Periodic reminder toast (every 2 hours). Auto-disable after 4 hours. Log start/stop of listening mode. |
| **Silence detection too aggressive (cuts off slow speakers)** | Medium | Configurable timeout (default 2s). Document that slow speakers should increase to 3-4s. Adaptive threshold as future enhancement. |
| **Two sounddevice InputStreams (wake word + recorder) conflict** | Medium | Most audio drivers support multiple InputStream instances. Test on a variety of hardware. Fall back to shared stream if conflicts arise. |
| **openWakeWord not compatible with PyInstaller** | Medium | onnxruntime is already bundled. openWakeWord is pure Python. ONNX model files need to be added as data files. Test in frozen build early. |
| **Binary size increase for cloud-only build** | High | Hands-Free requires onnxruntime. Cloud-only build does not include it. Decision: Hands-Free only available in Local build, or add onnxruntime to both builds. |
| **Wake word detected during TTS playback (feedback loop)** | Medium | Disable wake word detection during SPEAKING state (already in the code: skip detection when state != IDLE). |

### 3.10 Scope Decision & Release Planning

#### Version Assignment: v0.9.0

**Why not earlier?**

1. Hands-Free Mode depends on TTS (v0.6) being production-ready. The primary
   hands-free flow is Ask AI + TTS. If TTS is buggy, the hands-free experience
   will be poor.
2. Hands-Free Mode benefits from the Overlay (v0.7) for the visual "listening"
   indicator and the silence detection feedback.
3. The feature is complex enough that it should not compete with API (v0.8)
   for development resources. Sequential delivery reduces risk.
4. openWakeWord + German wake word training needs research and testing time.

#### MVP Scope (v0.9.0)

1. openWakeWord integration with bundled "hey voice paste" model.
2. Single pipeline: wake word -> record -> STT -> LLM -> TTS (Ask AI + TTS).
3. Energy-based silence detection for auto-stop (configurable timeout).
4. Hands-Free toggle in Settings + tray menu + overlay.
5. Ctrl+Alt+H hotkey to toggle hands-free mode.
6. Visual indicator in tray icon and overlay when listening.
7. Privacy documentation in README.
8. Auto-disable after 4 hours of inactivity.

#### Post-MVP (v0.9.1+)

- Voice command routing (multiple pipelines per wake word).
- Custom wake word training guide + Colab notebook link.
- Porcupine as alternative wake word backend.
- Shared audio stream (eliminate the gap between wake word detection and recording).
- Adaptive silence detection (learns user's speech patterns).
- Multiple wake words (different words for different pipelines).
- "Confirmation mode": after wake word, play a tone and wait 500ms. If user says
  "cancel", abort. Reduces false activation impact.

#### Estimated Effort

| Phase | Days |
|-------|------|
| openWakeWord integration + model testing | 3 |
| Silence detector implementation | 1 |
| Hands-Free toggle (settings, tray, overlay, hotkey) | 2 |
| Pipeline integration (wake word -> ask_tts flow) | 2 |
| Visual indicators (tray, overlay) | 1 |
| Privacy documentation | 0.5 |
| Testing (false accept/reject rates, resource usage) | 2 |
| PyInstaller bundle testing with onnxruntime + openWakeWord | 1 |
| **Total** | **12.5 days** |

---

## 4. Cross-Feature Integration Analysis

### 4.1 External API + Hands-Free Mode

These features are independent but have a useful intersection:

- The External API can expose a `handsfree_toggle` command that enables/disables
  Hands-Free Mode programmatically. This allows a Stream Deck button to toggle
  the always-listening mode.

- The External API's `status` response should include `handsfree_active: true/false`
  so external tools can show the current listening state.

### 4.2 Integration with Planned Features

| Planned Feature | External API Impact | Hands-Free Impact |
|----------------|--------------------|--------------------|
| **TTS (v0.6)** | API exposes `tts` command | Hands-Free uses TTS for spoken responses |
| **Overlay (v0.7)** | API toggle shown in overlay | Listening indicator in overlay |
| **Named Pipe IPC (v0.6)** | API reuses pipe infrastructure | No direct impact |
| **Context Menu (v0.7)** | Separate pipe, no conflict | No direct impact |
| **Local STT (v0.4)** | API `record_start` uses configured STT backend | Hands-Free uses same STT |

### 4.3 Dependency Chain

```
v0.6 TTS + IPC (internal pipe)
  |
  v
v0.7 Overlay
  |
  v
v0.8 External API (builds on IPC infrastructure, needs Overlay for toggle UI)
  |
  v
v0.9 Hands-Free Mode (needs TTS for responses, Overlay for indicators, API for programmatic toggle)
```

### 4.4 Threading Model (Full Picture at v0.9)

```
Main Thread:     pystray event loop
Thread 1:        keyboard hotkey listener
Thread 2:        Pipeline worker (per session)
Thread 3:        Settings dialog tkinter (on demand)
Thread 4:        Overlay window tkinter (persistent)
Thread 5:        TTS playback (per playback)
Thread 6:        IPC pipe server (internal, persistent)
Thread 7:        API pipe server (external, when enabled)
Thread 8:        Wake word listener (when handsfree enabled)
```

Eight threads is at the upper end of what is comfortable for a desktop utility.
However:
- Threads 3, 5, 7, 8 are conditional (only active when their feature is enabled).
- Most threads spend 99%+ of their time blocking on I/O (pipe read, audio read,
  tkinter mainloop).
- The GIL is not a bottleneck because no thread does heavy CPU work (onnxruntime
  releases the GIL during inference).

### 4.5 Combined Config Schema (v0.9)

```toml
[api]
# API keys in Credential Manager

[hotkey]
combination = "ctrl+alt+r"
prompt_combination = "ctrl+alt+a"
tts_combination = "ctrl+alt+t"
ask_tts_combination = "ctrl+alt+y"
overlay_combination = "ctrl+alt+o"
handsfree_combination = "ctrl+alt+h"    # NEW (v0.9)

[transcription]
# ... (unchanged)

[summarization]
# ... (unchanged)

[tts]
# ... (v0.6)

[overlay]
# ... (v0.7)

[integration]
context_menu_installed = false           # v0.7
api_enabled = false                      # v0.8

[handsfree]                              # NEW (v0.9)
enabled = false
wake_word = "hey_voice_paste"
threshold = 0.5
silence_timeout = 2.0
max_recording_seconds = 60
default_pipeline = "ask_tts"
auto_disable_hours = 4

[feedback]
audio_cues = true

[logging]
level = "INFO"
```

---

## 5. Updated Release Roadmap

| Version | Feature | Status | Est. Effort |
|---------|---------|--------|-------------|
| v0.5.0 | Voice Prompt mode, icon system, build consolidation | **Released** | -- |
| v0.6.0 | TTS MVP (ElevenLabs, hotkeys, SPEAKING state) | Planned | 8-12 days |
| v0.7.0 | Overlay UI (floating toolbar), Context Menu | Planned | 5-8 days |
| **v0.8.0** | **External API (Named Pipe, toggle, CLI client)** | **Proposed (this ADR)** | **5.5 days** |
| **v0.9.0** | **Hands-Free Mode MVP (openWakeWord, auto-stop, Ask AI + TTS)** | **Proposed (this ADR)** | **12.5 days** |
| v0.9.1 | Hands-Free: voice command routing, custom wake words | Future | TBD |
| v0.10.0 | HTTP API adapter, Stream Deck plugin | Future | TBD |

---

## 6. Open Questions for User

### External API

| # | Frage | Standard-Annahme | Entscheidung benoetigt? |
|---|-------|-------------------|-------------------------|
| 1 | Soll die API auch in der Cloud-Only-Build verfuegbar sein, oder nur im Local-Build? | Beide Builds (API braucht kein onnxruntime) | Nein, klare Empfehlung: beide Builds |
| 2 | Soll ein REST-HTTP-Adapter (localhost:port) als Alternative zu Named Pipes angeboten werden? | Erst in v0.8.1, nicht im MVP | Ja -- wuerdest du das nutzen? |
| 3 | Welche externen Tools willst du konkret anbinden? (Stream Deck, AutoHotkey, eigene Skripte?) | Stream Deck + PowerShell-Skripte | Ja -- hilft bei der Priorisierung |
| 4 | Soll die API Events streamen koennen (z.B. "State hat sich geaendert")? | Nein im MVP, moeglich in v0.8.1 via Subscription-Mechanismus | Nein |
| 5 | Soll die API Konfigurationsaenderungen erlauben (`set_config`)? | Nein im MVP (Sicherheitsrisiko, Settings-Dialog ist ausreichend) | Nein |

### Hands-Free Mode

| # | Frage | Standard-Annahme | Entscheidung benoetigt? |
|---|-------|-------------------|-------------------------|
| 6 | Welches Aktivierungswort moechtest du? "Hey Voice Paste", "Hey Paste", "Computer", oder etwas anderes? | "Hey Voice Paste" (laenger = weniger False Positives) | Ja -- persoenliche Praeferenz |
| 7 | Soll die Stille-Erkennung energie-basiert (einfach, schnell) oder Silero-VAD-basiert (genauer, schwerer) sein? | Energie-basiert im MVP, Silero-VAD als Option spaeter | Nein |
| 8 | Soll nach der Wake-Word-Erkennung ein Bestaetigungston gespielt werden, und soll "Abbrechen" innerhalb von 2 Sekunden moeglich sein? | Ja, Bestaetigungston + Cancel-Fenster | Ja -- wuerdest du das als stoerend empfinden? |
| 9 | Soll der Hands-Free-Modus auch im Cloud-Only-Build verfuegbar sein? (Erhoeuht die Binary-Groesse um ~35 MB durch onnxruntime) | Nur im Local-Build | Ja -- wichtig fuer die Build-Strategie |
| 10 | Wie lang soll die maximale Aufnahmedauer im Hands-Free-Modus sein? (Aktuell: 60s als Vorschlag, normal: 5 Min) | 60 Sekunden | Ja -- zu kurz/zu lang? |
| 11 | Soll es verschiedene Aktivierungswoerter fuer verschiedene Funktionen geben? Oder ein Wort + Sprachbefehl? | Ein Wort + Sprachbefehl (einfacher, weniger Modelle) | Ja -- aber Post-MVP |
| 12 | Soll sich der Hands-Free-Modus nach einer gewissen Zeit automatisch deaktivieren? (Vorschlag: 4 Stunden) | Ja, 4 Stunden | Ja -- sinnvoller Wert? |
| 13 | Ist die Abhaengigkeit von onnxruntime akzeptabel? (Ist bereits im Local-Build enthalten, kann im Frozen-Exe Probleme machen) | Ja, da Local-Build es bereits benoetigt | Nein |

---

## Appendix A: External API Client Examples

### Python Client

```python
"""Example: Trigger TTS via Voice Paste External API."""

import json
import socket
import struct

PIPE_PATH = r"\\.\pipe\VoicePasteAPI"

def send_command(command: dict) -> dict:
    """Send a command to Voice Paste and return the response."""
    payload = json.dumps(command).encode("utf-8")
    frame = struct.pack(">I", len(payload)) + payload

    # Connect to Named Pipe (presented as a file on Windows)
    with open(PIPE_PATH, "r+b", buffering=0) as pipe:
        pipe.write(frame)
        pipe.flush()

        # Read response
        length_bytes = pipe.read(4)
        length = struct.unpack(">I", length_bytes)[0]
        response_bytes = pipe.read(length)
        return json.loads(response_bytes.decode("utf-8"))


# Example usage
response = send_command({"action": "tts", "text": "Hallo Welt"})
print(response)
# {"status": "ok", "request_id": ""}

response = send_command({"action": "status"})
print(response)
# {"status": "ok", "data": {"state": "speaking", "api_version": "1"}}
```

### PowerShell Client

```powershell
# Example: Query Voice Paste status via Named Pipe

$pipeName = "VoicePasteAPI"
$pipe = New-Object System.IO.Pipes.NamedPipeClientStream(".", $pipeName, [System.IO.Pipes.PipeDirection]::InOut)
$pipe.Connect(5000)  # 5 second timeout

$command = '{"action": "status"}' | ConvertTo-Json -Compress
# Note: This is simplified. Real implementation needs length-prefix framing.

$writer = New-Object System.IO.StreamWriter($pipe)
$reader = New-Object System.IO.StreamReader($pipe)

# Send length-prefixed message
$bytes = [System.Text.Encoding]::UTF8.GetBytes($command)
$lengthBytes = [BitConverter]::GetBytes([int]$bytes.Length)
[Array]::Reverse($lengthBytes)  # Big-endian
$pipe.Write($lengthBytes, 0, 4)
$pipe.Write($bytes, 0, $bytes.Length)
$pipe.Flush()

# Read response
$respLengthBytes = New-Object byte[] 4
$pipe.Read($respLengthBytes, 0, 4)
[Array]::Reverse($respLengthBytes)
$respLength = [BitConverter]::ToInt32($respLengthBytes, 0)
$respBytes = New-Object byte[] $respLength
$pipe.Read($respBytes, 0, $respLength)
$response = [System.Text.Encoding]::UTF8.GetString($respBytes)

Write-Host $response
$pipe.Close()
```

## Appendix B: Wake Word Model Training Guide (Reference)

For creating a custom "Hey Voice Paste" wake word model with openWakeWord:

1. Install openWakeWord trainer: `pip install openwakeword`
2. Use the Google Colab notebook: https://github.com/dscripka/openWakeWord
3. Generate synthetic training data using TTS (at least 100 positive samples)
4. Train the model (typically <1 hour on a GPU)
5. Export as ONNX (~200 KB)
6. Place in `%LOCALAPPDATA%\VoicePaste\models\wake_words\`
7. Set `wake_word = "path/to/model.onnx"` in config.toml

The pre-bundled "hey_voice_paste" model will be trained by the project maintainers
and included in the installer.

## Appendix C: Resource Usage Estimates

### External API (v0.8)

| Resource | Idle | Active (processing command) |
|----------|------|-----------------------------|
| CPU | <0.1% (pipe blocking read) | <0.1% (JSON parse + dispatch) |
| RAM | ~1 MB (thread + pipe buffer) | ~1 MB (no significant allocation) |
| Threads | 1 (daemon, blocking) | 1 |
| Network | None (Named Pipe, local only) | None |

### Hands-Free Mode (v0.9)

| Resource | Idle (LISTENING) | Active (wake word processing) |
|----------|-----------------|-------------------------------|
| CPU | 2-3% (onnxruntime inference on 80ms frames) | 2-3% + pipeline CPU |
| RAM | ~50 MB (onnxruntime + openWakeWord model) | ~50 MB + pipeline RAM |
| Threads | 1 (audio read + inference) | 1 + pipeline thread |
| Microphone | Always open (16kHz mono) | Always open |
| Network | None (local inference) | STT/LLM API calls (after wake word) |
| Battery (laptop) | Moderate impact (~5-10% faster drain) | Same + API calls |

## Appendix D: Rejected Alternatives

### Rejected: WebSocket API

WebSocket provides bidirectional communication suitable for event streaming. However:
- Requires a web server dependency (websockets or aiohttp)
- Adds complexity (async event loop, connection management, heartbeats)
- The primary use case (send command, get response) is request-response, not streaming
- Named Pipe is simpler and provides the same functionality for local IPC
- WebSocket is better suited for browser-based clients (a use case we do not have)

### Rejected: COM Automation for External API

Windows COM provides rich automation capabilities (e.g., `Set vp = CreateObject("VoicePaste.Application")`). However:
- COM server registration is complex and sometimes needs admin
- Python COM servers (via comtypes/pythoncom) are fragile in PyInstaller bundles
- The development effort is 5-10x higher than Named Pipes
- The user base that would use COM (VBA/Office automation) is small

### Rejected: Continuous Whisper STT for Wake Word Detection

Running Whisper continuously and scanning transcripts for the wake phrase:
- 10-30% CPU usage vs 2-3% for openWakeWord
- 150+ MB additional RAM (Whisper model) vs ~50 MB (onnxruntime + OWW)
- Higher latency (Whisper processes longer audio chunks)
- Better accuracy for arbitrary phrases, but overkill for a fixed wake word
- Would drain laptop battery significantly faster

### Rejected: Porcupine as Primary Wake Word Engine

Porcupine offers superior accuracy and lower CPU usage, but:
- Free tier restricted to personal/non-commercial use
- Commercial license: $6,000/year
- Custom wake words require cloud-based Picovoice Console
- AccessKey dependency (another API key to manage)
- Closed-source model format (.ppn) -- no transparency into what runs on the user's machine

openWakeWord is fully open source, commercially free, and allows local custom
model training. The accuracy trade-off is acceptable for a desktop tool.
