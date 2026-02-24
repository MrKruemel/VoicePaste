# Threat Model -- VoicePaste

**Version**: 2.0 (supersedes THREAT-MODEL.md v1.0)
**Date**: 2026-02-22
**Author**: Security Engineer
**Scope**: VoicePaste v1.3+ (Windows + Linux, X11 + Wayland)

---

## 1. System Overview

VoicePaste is a cross-platform desktop application (Windows + Linux) that:

1. Captures microphone audio via a global hotkey
2. Transcribes audio via a cloud API (OpenAI Whisper) or locally (faster-whisper/CTranslate2)
3. Optionally processes transcripts via a cloud LLM for summarization or Q&A
4. Pastes results at the cursor position via clipboard + keystroke simulation
5. Optionally reads text aloud via cloud TTS (ElevenLabs) or local TTS (Piper/ONNX)
6. Provides hands-free mode via continuous wake-word detection (local whisper-tiny)
7. Exposes a localhost-only HTTP API for external control
8. Runs in the system tray with a settings UI

The tool handles **sensitive data** at every stage: voice recordings (biometric data under GDPR), transcribed text, API credentials, clipboard contents, and has privileged system access (input devices, virtual keyboard).

### Platform-Specific Implementation

| Concern | Windows | Linux X11 | Linux Wayland |
|---------|---------|-----------|---------------|
| **Hotkey monitoring** | `keyboard` library (SetWindowsHookEx) | `pynput` (Xlib) | `evdev` (/dev/input/*) |
| **Keystroke injection** | ctypes (SendInput) | `xdotool` (subprocess) | evdev UInput (/dev/uinput) |
| **Clipboard** | Win32 API (ctypes) | `xclip`/`xsel` (subprocess) | `wl-copy`/`wl-paste` (subprocess) |
| **Credential storage** | Windows Credential Manager | SecretService/GNOME Keyring | SecretService/GNOME Keyring |
| **Single instance** | Named mutex (Win32) | fcntl file lock | fcntl file lock |

---

## 2. Asset Inventory

| Asset | Sensitivity | Location | Lifecycle |
|-------|-------------|----------|-----------|
| **Audio recording** | HIGH (GDPR Art 9: biometric) | In-memory numpy arrays + BytesIO only | Created on record start; destroyed (zeroed) after API call or cancel |
| **API keys (3)** | HIGH -- paid service access | OS keyring (primary); config.toml fallback | Persistent; loaded at startup |
| **Transcript text** | HIGH -- may contain PII, secrets | In-memory only | Created by STT response; destroyed after paste |
| **Summary/LLM output** | MEDIUM-HIGH | In-memory only | Created by LLM response; destroyed after paste |
| **Clipboard contents** | MEDIUM -- shared OS resource | OS clipboard | Backed up before paste; restored after |
| **TTS cache audio** | MEDIUM -- synthesized speech | Disk (XDG cache dir) | LRU eviction by size/age/count |
| **TTS cache index** | MEDIUM -- contains full source text | Disk (index.json in cache dir) | Updated per TTS synthesis |
| **Log file** | LOW-MEDIUM | Disk (rotating, 5MB x 3) | Persistent |
| **config.toml** | MEDIUM -- preferences (no keys if keyring works) | Disk (0600 on Linux) | Persistent |
| **Model files** | LOW | Disk (XDG cache) | Downloaded once; SHA256 verified |
| **/dev/uinput virtual keyboard** | HIGH -- keystroke injection | Kernel device | Ephemeral; created on demand |
| **/dev/input/* device handles** | HIGH -- raw keyboard input | Kernel devices (read-only) | Open during app lifetime |

---

## 3. Threat Analysis

### T1: API Key Exposure

**Threat**: API keys (OpenAI, OpenRouter, ElevenLabs) could be exposed through accidental commit, malware reading config, log files, or screen sharing.

**Severity**: HIGH

**Mitigations**:
- [REQ-S01] API keys are never logged. `config.masked_api_key()` shows only last 4 chars. **Status: MITIGATED**
- [REQ-S02] No hardcoded keys in source. Keys from keyring or config.toml. **Status: MITIGATED**
- [REQ-S03] config.toml in .gitignore. Only config.example.toml is committed. **Status: MITIGATED**
- [REQ-S05] config.toml created with 0600 permissions on Linux (SEC-069 fix). **Status: MITIGATED**
- Keyring integration: Windows Credential Manager / Linux SecretService. **Status: MITIGATED**
- Migration: keys in config.toml auto-migrated to keyring on startup. **Status: MITIGATED**

**Residual Risk**: LOW. If keyring is unavailable, keys fall back to config.toml (0600 perms). A local attacker with same-user access could still read them.

### T2: Audio Data in Transit

**Threat**: Voice recordings sent to cloud APIs could be intercepted or retained by the provider.

**Severity**: MEDIUM

**Mitigations**:
- [REQ-S06] All API calls use HTTPS. No HTTP endpoints for STT/LLM/TTS. **Status: MITIGATED**
- [REQ-S07] No `verify=False` anywhere in codebase. TLS validation enforced. **Status: MITIGATED**
- [REQ-S08] README documents that audio is sent to third-party APIs. **Status: MITIGATED**
- Local STT/TTS option: users can operate 100% offline. **Status: MITIGATED**
- Ollama integration uses `http://localhost:11434` (local-only, acceptable). **Status: N/A**

**Residual Risk**: LOW. Users choosing cloud APIs accept third-party data processing. Fully offline mode available.

### T3: Audio Data at Rest

**Threat**: Audio recordings could be written to disk (temp files, crash dumps) and later recovered.

**Severity**: HIGH if audio hits disk; LOW if strictly in-memory

**Mitigations**:
- [REQ-S09] Audio never written to disk. All paths use BytesIO/numpy arrays. **Status: MITIGATED**
- [REQ-S10] Audio buffers zeroed on error/cancel (`_clear_frames()` fills with zeros). **Status: MITIGATED**
- [REQ-S11] No audio data in logs. Only byte counts and durations logged. **Status: MITIGATED**
- Wake word buffer: zeroed via `_clear_buffer()`. **Status: MITIGATED**

**Residual Risk**: LOW. No code path writes audio to disk. OS swap/hibernation could theoretically capture in-memory audio.

### T4: Clipboard Data Leakage

**Threat**: Transcript temporarily on clipboard; other apps could read it; crash during paste loses original content.

**Severity**: MEDIUM

**Mitigations**:
- [REQ-S12] Clipboard exposure minimized: write-paste-restore in quick succession. **Status: MITIGATED**
- [REQ-S13] Clipboard restored in finally block of `_run_pipeline()`. **Status: MITIGATED**
- [REQ-S14] Clipboard content never logged. Only character count logged. **Status: MITIGATED**
- [REQ-S18] Plain text only (CF_UNICODETEXT on Windows). **Status: MITIGATED**
- Linux: subprocess `stdin` pipe (no shell=True, no temp files). **Status: MITIGATED**

**Residual Risk**: LOW. Brief window where transcript is on clipboard. On Linux, clipboard write return code is not checked (SEC-078, Low severity).

### T5: Global Hotkey / Input Device Access

**Threat**: Input monitoring mechanisms could be exploited or create keylogger-like behavior.

**Severity**: MEDIUM (platform-dependent)

#### Windows (keyboard library)
- Uses SetWindowsHookEx. Only hooks specific key combos (REQ-S15). **Status: MITIGATED**

#### Linux X11 (pynput)
- Uses Xlib. Only registers specific GlobalHotKeys. **Status: MITIGATED**

#### Linux Wayland (evdev) -- NEW in v1.3
- Reads ALL keyboard events from /dev/input/* via select(). The monitor receives every keystroke from every keyboard device.
- However: only key codes are compared against registered hotkey combos (`_check_combos`). Key values are stored in `_held_keys` set as integer keycodes only.
- No keystroke content is logged. Per-keystroke debug logging was explicitly removed (privacy fix, documented in module docstring).
- `_held_keys` tracks modifier state (keycodes, not characters). Cleared on device disconnect.

**Residual Risk**: MEDIUM. The evdev monitor inherently sees all keystrokes. This is architecturally unavoidable on Wayland (by design -- Wayland isolates per-client input). The code processes only hotkey-relevant events and logs nothing about individual keys. An attacker who can inject code into the process could theoretically intercept `_held_keys` state, but this requires same-process access (which is already game-over).

### T6: UInput Virtual Keyboard (Keystroke Injection) -- NEW in v1.3

**Threat**: The UInputController creates a virtual keyboard via /dev/uinput and can inject arbitrary keystrokes into the compositor.

**Severity**: LOW (after SEC-082/SEC-083 remediation)

**Analysis**:
- `UInputController._ensure_device()` creates the UInput device with **restricted capabilities** -- only `KEY_LEFTCTRL` (29), `KEY_LEFTSHIFT` (42), and `KEY_V` (47) are registered via the `ecodes.EV_KEY` capability dict. The virtual keyboard cannot inject any other keystrokes.
- The public API `uinput_send_key()` is called from `_simulate_wayland_keystroke()` in `_linux.py` which passes either "ctrl+v" or "ctrl+shift+v" for paste simulation.
- `cleanup_uinput()` is called during `_shutdown()` to explicitly close the UInput file descriptor.

**Mitigations**:
- **SEC-082 (RESOLVED)**: UInput capabilities restricted to only 3 keycodes needed for paste simulation. The device physically cannot emit any other key events.
- **SEC-083 (RESOLVED)**: `cleanup_uinput()` is called in `_shutdown()` after `stop_monitor()`, ensuring the UInput device is properly closed on exit.
- The UInput device requires /dev/uinput write access (not granted by default).
- Requires a custom udev rule: `KERNEL=="uinput", GROUP="input", MODE="0660"`.
- The device is named "VoicePaste Virtual Keyboard" for identification.
- UInput is write-only -- it cannot read other devices' keystrokes (SEC-081: confirmed, no privacy concern).
- Kernel reclaims the device on process exit as a safety net.

**Residual Risk**: LOW. UInput is inherently privileged but tightly scoped to 3 key codes, and requires explicit setup (udev rule + input group membership).

### T7: /dev/input/* Access (Evdev Device Monitoring) -- NEW in v1.3

**Threat**: Process has read access to all keyboard devices. Could be used as a keylogger if compromised.

**Severity**: MEDIUM

**Mitigations**:
- Requires `input` group membership (explicit setup step).
- Only keyboard devices are opened (filtered by KEY_A capability).
- Event processing: only EV_KEY events are handled; only keycodes compared against registered combos.
- No keystroke logging: per-key debug logging was explicitly removed.
- `_held_keys` contains integer keycodes, not characters (no way to reconstruct typed text from modifier state alone).
- Device file descriptors are properly closed on stop/disconnect.

**Residual Risk**: MEDIUM. This is inherent to the Wayland security model -- global hotkeys require raw input device access. The risk is equivalent to running any Wayland hotkey daemon (e.g., swhkd, keyd). Users must opt-in by joining the `input` group.

### T8: Subprocess Injection (Linux Clipboard/Paste) -- Updated

**Threat**: Clipboard operations and keystroke simulation use subprocess calls to external tools. Injection could occur through crafted clipboard content.

**Severity**: LOW

**Mitigations**:
- All `subprocess.run()` calls use **list arguments** (never shell=True). **Status: MITIGATED**
- Clipboard content is passed via **stdin pipe** (never as command arguments). **Status: MITIGATED**
- Tool paths are resolved via `shutil.which()` to absolute paths. **Status: MITIGATED**
- All calls have **2-second timeouts**. **Status: MITIGATED**
- Maximum clipboard size enforced: 1MB (`_MAX_CLIPBOARD_BYTES`). **Status: MITIGATED**

**Residual Risk**: LOW. Shell injection is not possible with the current implementation. The 1MB limit prevents memory issues.

### T9: Dependency Supply Chain

**Threat**: Compromised PyPI packages could contain malicious code.

**Severity**: MEDIUM

**Mitigations**:
- [REQ-S20] All dependencies pinned with `==` in requirements.txt. **Status: MITIGATED**
- [REQ-S22] Dependencies audited for necessity. **Status: PARTIALLY MITIGATED**
- [REQ-S23] Install from official PyPI only. **Status: MITIGATED**
- Model downloads: SHA256 verification via `integrity.py`. **Status: MITIGATED**

**FINDING (SEC-075, Informational, carried forward)**: requirements.txt uses `==` pinning but not `--require-hashes`. Hash pinning would prevent substitution attacks. **Residual Risk**: LOW for desktop app distributed as binary.

### T10: Log File Information Disclosure

**Threat**: Log files may accumulate sensitive information.

**Severity**: LOW-MEDIUM

**Mitigations**:
- [REQ-S24] Transcript content never logged. Only `len(transcript)` logged. **Status: MITIGATED**
- [REQ-S25] Only safe metadata logged (timestamps, states, durations, char counts). **Status: MITIGATED**
- [REQ-S26] Log rotation: 5MB max, 3 backup files. **Status: MITIGATED**
- API keys: only masked versions logged. **Status: MITIGATED**
- Clipboard: only character count logged, never content. **Status: MITIGATED**
- Keystroke data: not logged (evdev per-key logging removed). **Status: MITIGATED**

**Residual Risk**: LOW. Logs contain: file paths, device names, session types, error messages (which might include partial stack traces).

### T11: TTS Cache Data Persistence (GDPR) -- Existing

**Threat**: TTS cache stores synthesized audio and full source text on disk in index.json.

**Severity**: MEDIUM

**FINDING (SEC-058/SEC-066, carried forward)**: `tts_cache.py` line 291 writes the full source text (`key.text`) to `index.json`. This text may contain user-dictated content that is personal data under GDPR.

**Current Mitigations**:
- Cache is in XDG cache directory (user-owned).
- LRU eviction by size/age/count.
- Users can clear cache via tray menu.

**Residual Risk**: MEDIUM. Full text persists on disk until eviction. Recommend: store only text_preview (first 80 chars) or a hash, not full text.

### T12: HTTP API (Localhost)

**Threat**: Local API server could be exploited by malicious web pages via CSRF or by other local processes.

**Severity**: MEDIUM

**Mitigations**:
- Binds to 127.0.0.1 only (hardcoded, not configurable). **Status: MITIGATED**
- Rate limited: 5 requests/second. **Status: MITIGATED**
- Body size limit: 64KB. **Status: MITIGATED**
- CORS: regex validates `http://localhost` origins. **Status: PARTIALLY MITIGATED**
- Entry ID validation: `^[0-9a-f]{16}$` regex. **Status: MITIGATED**
- Disabled by default. **Status: MITIGATED**

**FINDING (SEC-050, carried forward)**: CORS allows ANY localhost port. A malicious web page on any localhost port could make cross-origin requests to the API. **Residual Risk**: LOW. Requires local web server + user visiting malicious page. API is disabled by default.

### T13: Binary Distribution Security

**Threat**: Tampered binaries, missing code signing, bundled unexpected files.

**Severity**: LOW

**Current State**:
- PyInstaller --onefile binary.
- No code signing on either platform (SEC-076, Informational).
- Excluded list in .spec file is thorough (av, Cython, test frameworks, GUI frameworks, system packages).
- Runtime hooks: `rthook_av_stub.py` and `rthook_onnxruntime.py`.

**Residual Risk**: LOW. Recommend SHA256 hash in release notes and code signing when feasible.

### T14: av Stub Module Injection

**Threat**: The dummy `av` module injected by `local_stt.py` (line 62) and `rthook_av_stub.py` could mask real import errors or cause confusing behavior.

**Severity**: LOW

**Analysis**:
- Only injected if `av` is not already in `sys.modules` AND `import av` fails with ImportError.
- The stub has `__version__ = "0.0.0-stub"` and `__path__ = []` (package marker).
- Any code that actually calls `av.open()` or similar will get AttributeError (the stub has no functions).
- This is a build-size optimization (saves ~119MB by excluding PyAV/FFmpeg).
- VoicePaste never calls `faster_whisper.audio.decode_audio()` -- it feeds pre-decoded PCM arrays.

**FINDING (SEC-077, Informational, carried forward)**: The dummy module could produce confusing AttributeError if code accidentally calls an av function. Consider adding `__getattr__` for a clearer error message.

**Residual Risk**: LOW. Fail-closed behavior -- operations that need real av will fail immediately with clear errors.

---

## 4. Security Requirements Summary

### Critical (Enforced)

| ID | Requirement | Threat | Status |
|----|-------------|--------|--------|
| REQ-S01 | Never log API keys | T1 | MITIGATED |
| REQ-S02 | Never hardcode API keys | T1 | MITIGATED |
| REQ-S06 | HTTPS only for cloud API calls | T2 | MITIGATED |
| REQ-S07 | TLS certificate validation enabled | T2 | MITIGATED |
| REQ-S09 | Audio never written to disk | T3 | MITIGATED |
| REQ-S11 | No audio data in logs | T3 | MITIGATED |
| REQ-S15 | Only hook specific hotkey combinations | T5 | MITIGATED |
| REQ-S18 | Paste as plain text only | T4 | MITIGATED |

### High (Enforced)

| ID | Requirement | Threat | Status |
|----|-------------|--------|--------|
| REQ-S03 | config.toml in .gitignore | T1 | MITIGATED |
| REQ-S05 | Config file permissions (0600 on Linux) | T1 | MITIGATED |
| REQ-S10 | Clear audio buffers on error/cancel | T3 | MITIGATED |
| REQ-S12 | Minimize clipboard exposure window | T4 | MITIGATED |
| REQ-S13 | Always restore clipboard in finally block | T4 | MITIGATED |
| REQ-S14 | Never log clipboard contents | T4 | MITIGATED |
| REQ-S24 | Never log transcript content | T10 | MITIGATED |
| REQ-S25 | Log only safe data | T10 | MITIGATED |
| REQ-S27 | Single-instance enforcement | T12 | MITIGATED |
| REQ-S28 | No keystroke content logging (evdev) | T5/T7 | MITIGATED |
| REQ-S29 | UInput capabilities should be restricted | T6 | MITIGATED |

### Medium (Tracked)

| ID | Requirement | Threat | Status |
|----|-------------|--------|--------|
| REQ-S20 | Pin all dependency versions | T9 | MITIGATED |
| REQ-S26 | Log file rotation | T10 | MITIGATED |
| REQ-S30 | TTS cache should not store full user text | T11 | OPEN |
| REQ-S31 | CORS should restrict to specific port(s) | T12 | OPEN |

---

## 5. Data Flow Diagram

```
User speaks into microphone
    |
    v
[Microphone] --sounddevice--> [numpy array IN MEMORY]
    |
    v
[BytesIO WAV encoding IN MEMORY]
    |
    +--(cloud)--> [HTTPS POST to api.openai.com/v1/audio/transcriptions]
    |                  |
    |                  v
    |             [Transcript text IN MEMORY]
    |
    +--(local)--> [faster-whisper CTranslate2 inference IN MEMORY]
    |                  |
    |                  v
    |             [Transcript text IN MEMORY]
    |
    v
[Optional: HTTPS POST to LLM API for summarization]
    |
    v
[Summary/result text IN MEMORY]
    |
    v
[Clipboard write via OS API / subprocess stdin pipe]
    |
    v
[Keystroke simulation: Ctrl+V]
    |   Windows: SendInput (ctypes)
    |   Linux X11: xdotool (subprocess)
    |   Linux Wayland: UInput (/dev/uinput) or ydotool/wtype
    |
    v
[Clipboard restore from backup]
    |
    v
[Text variables cleared / go out of scope]
```

### Evdev Input Path (Wayland)

```
[/dev/input/event*] --select()--> [EvdevKeyboardMonitor]
    |                                    |
    |  ALL keyboard events               | Only EV_KEY events processed
    |  (inherent to evdev)               | Only keycodes compared against registered combos
    |                                    | No content logged
    v                                    v
[_held_keys: set of int keycodes]  [Callback fired for matched combo only]
```

### UInput Injection Path (Wayland)

```
[UInputController] --write()--> [/dev/uinput]
    |                                 |
    | Currently: any KEY_* code       | Kernel delivers to compositor
    | Should be: only paste keys      |
    v                                 v
[Compositor processes keystroke]  [Focused application receives paste]
```

---

## 6. GDPR and Privacy Considerations

### Voice Data (GDPR Art 9: Special Category)

Voice data is **biometric personal data** under GDPR when used for identification purposes. Even when used only for transcription, voice recordings are personal data requiring:

1. **Data minimization**: Audio kept in memory only. Never written to disk. Buffers zeroed after use. **Status: COMPLIANT**
2. **Purpose limitation**: Audio used solely for transcription. No analytics, telemetry, or secondary processing. **Status: COMPLIANT**
3. **Transparency**: README documents that audio may be sent to third-party APIs (OpenAI, ElevenLabs). **Status: COMPLIANT**
4. **User control**: Every recording is user-initiated (hotkey or wake word). No background recording without user-configured wake word. **Status: COMPLIANT**
5. **Data deletion**: Audio buffers cleared (zeroed) after each operation. **Status: COMPLIANT**
6. **Local alternative**: Users can choose 100% local processing (faster-whisper + Piper TTS + Ollama). **Status: COMPLIANT**

### Transcribed Text

1. Not persisted to disk in normal operation. **Status: COMPLIANT**
2. Not logged (only character count). **Status: COMPLIANT**
3. **Exception**: TTS cache stores full source text in index.json (SEC-058/SEC-066). **Status: OPEN -- recommend storing only preview/hash**

### Keystroke Data

1. Evdev monitor receives all keystrokes from all keyboards. **Status: INHERENT TO WAYLAND**
2. No keystroke content is logged or stored. Only integer keycodes for modifier state. **Status: COMPLIANT**
3. Per-keystroke debug logging was explicitly removed (documented in module docstring). **Status: COMPLIANT**

### Network Communications

Only the following outbound connections are made:
- `api.openai.com` (STT, LLM) -- HTTPS
- `api.openrouter.ai` (LLM) -- HTTPS
- `api.elevenlabs.io` (TTS) -- HTTPS
- `huggingface.co` (model download) -- HTTPS
- `localhost:11434` (Ollama, local only) -- HTTP

**No telemetry, analytics, crash reporting, or phone-home behavior.** Verified by code review.

---

## 7. Security Review Checklist (Pre-Release)

- [x] No API keys or secrets in source code
- [x] No API keys in log output (masked via `config.masked_api_key()`)
- [x] All HTTP calls use HTTPS with certificate validation
- [x] Audio data never written to disk
- [x] Clipboard restored in all code paths (finally block)
- [x] No transcript/clipboard content in logs
- [x] All dependencies pinned to specific versions
- [x] .gitignore includes config.toml, *.log
- [x] No keystroke content logging in evdev monitor
- [x] No shell=True in subprocess calls
- [x] Subprocess inputs via stdin pipe (not command arguments)
- [x] config.toml created with 0600 permissions on Linux
- [x] UInput capabilities restricted to paste keys only (SEC-082) -- DONE
- [x] cleanup_uinput() called during shutdown (SEC-083) -- DONE
- [ ] TTS cache index.json does not store full user text (SEC-058)
- [ ] pip-audit run against current requirements.txt
