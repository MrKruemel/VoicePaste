# VoicePaste Architecture

**Overview**: VoicePaste is a cross-platform desktop application (Windows + Linux) that records speech via hotkey, transcribes it (cloud or local), optionally summarizes/cleans with an LLM, and pastes the result at the cursor. It runs entirely in the system tray and supports multiple recording modes (normal transcription, voice prompt Q&A, TTS audio readback, hands-free wake-word activation) and text-to-speech synthesis.

**Current Version**: 1.1.0
**Supported Platforms**: Windows 10/11, Linux (Ubuntu 22.04+)
**Primary Language**: English (application supports German)

---

## System Overview

### High-Level Data Flow

```
User Press Hotkey
    ↓
Register in Hotkey Dispatcher (keyboard/pynput/evdev)
    ↓
Spawn Pipeline Worker Thread
    ↓
RECORDING STATE:
    Record audio from microphone (in-memory WAV buffer)
    Synthesize audio cues (start tone 440→880 Hz)
    ↓
User Press Hotkey Again (or auto-stop on silence)
    ↓
PROCESSING STATE:
    Send audio to STT backend (cloud OpenAI Whisper or local faster-whisper)
    [Optional] Summarize transcript with LLM (OpenAI/OpenRouter/Ollama)
    Synthesize audio cues (stop tone 880→440 Hz)
    ↓
AWAITING_PASTE or PASTING STATE:
    Backup original clipboard contents
    Write result to clipboard
    Simulate Ctrl+V (or auto-detected terminal Ctrl+Shift+V)
    Restore original clipboard (150ms delay)
    ↓
IDLE STATE
    Return to ready
```

### Recording Modes

| Mode | Hotkey | Action |
|------|--------|--------|
| **Normal** | Ctrl+Alt+R (default) | Record speech → Transcribe → [Optional] Summarize → Paste |
| **Voice Prompt** | Ctrl+Alt+A (default) | Record question → Transcribe → Send to LLM as prompt → Paste answer |
| **TTS Read** | Ctrl+Alt+T (Win) / Ctrl+Alt+S (Linux) | Read clipboard content aloud via TTS |
| **TTS Ask** | Ctrl+Alt+Y (default) | Ask question → Transcribe → Send to LLM → Synthesize answer → Play audio |
| **Hands-Free** | Wake phrase (default "Hello Cloud") | Continuous detection via local Whisper tiny → Auto-record → Auto-stop on silence → Execute configured pipeline |
| **Claude Code** | Ctrl+Alt+C (optional) | Record command → Send to claude CLI → Capture output → Return to app |

---

## Threading Model

VoicePaste uses a multi-threaded architecture to handle blocking operations (audio, API calls, TTS synthesis) without freezing the UI or system tray.

### Threads

| Thread | Purpose | Lifecycle |
|--------|---------|-----------|
| **Main** | pystray event loop; system tray icon + right-click menu | Startup → Shutdown |
| **Hotkey** | Global hotkey listener (Windows: `keyboard` library, Linux X11: `pynput`, Linux Wayland: `evdev`) | Startup → Shutdown (daemon) |
| **Pipeline Worker** | STT → Summarization → Paste sequence; spawned per recording session | Per hotkey press → Completion |
| **Settings Dialog** | tkinter GUI with tabbed interface (Transcription, Summarization, TTS, General tabs) | Spawned on demand (right-click tray) → User closes |
| **Wake Word Detector** | Continuous background audio monitoring for hands-free mode (local Whisper tiny) | When hands-free enabled → Disabled |
| **HTTP API Server** | Localhost REST API for external control (v0.9+) | Startup → Shutdown (daemon) |
| **Overlay UI** | Floating window in bottom-right corner showing recording timer, processing animation, speaking feedback | Spawned when needed (v0.8+, daemon) |

All threads except Main are daemon threads and are safely terminated on shutdown via event flags.

---

## State Machine

The application uses a formal state machine defined in `src/constants.py::AppState` to manage transitions and guard against invalid sequences. All state changes are thread-safe (protected by lock in `VoicePasteApp._set_state()`) and automatically update the tray icon color.

### States

| State | Tray Color | Meaning |
|-------|-----------|---------|
| **IDLE** | Grey | Ready to record; no active operation |
| **RECORDING** | Red | User pressing hotkey; audio being captured |
| **PROCESSING** | Yellow | STT/summarization in progress |
| **AWAITING_PASTE** | Teal | User review required before paste (confirmation mode) |
| **PASTING** | Green | Clipboard updated; Ctrl+V simulated |
| **SPEAKING** | Blue | TTS audio playing (v0.6+) |

### State Transition Diagram

```
                    ┌─────────┐
                    │  IDLE   │◄─────────────────────────────────┐
                    └────┬────┘                                   │
                         │                                        │
       (Ctrl+Alt+R press) │                                       │
                         ▼                                        │
                    ┌─────────────┐                               │
                    │  RECORDING  │                               │
                    └────┬────┘                               │
                         │                                        │
      (Hotkey again / silence timeout / Escape cancel)           │
                         │                                        │
        ┌────────────────┼────────────────┬─────────────────┐   │
        │                │                │                 │   │
        ▼ (empty)        ▼                ▼                 ▼   │
    IDLE◄─────────PROCESSING─────┬──►AWAITING_PASTE◄──────┤   │
                         │        │        │         (confirm)  │
                         │        │        ▼                     │
                         │        │   (Enter/timeout/Escape)     │
                         │        │        │                     │
                         ▼        │        ▼                     │
                      PASTING◄────┘───IDLE◄──────────────────────┘
                         │
                         │
                         ▼
                       IDLE

TTS / Ask Mode (separate pipeline):
    IDLE ──(Ctrl+Alt+T/Y)──► PROCESSING ──► SPEAKING ──► IDLE
```

### Valid Transitions

From `src/constants.py::VALID_TRANSITIONS`:

- **Normal flow**: IDLE → RECORDING → PROCESSING → PASTING → IDLE
- **With confirmation** (v0.9+): IDLE → RECORDING → PROCESSING → AWAITING_PASTE → PASTING → IDLE
- **Cancellation**: RECORDING → IDLE (Escape key)
- **Empty transcript/error**: PROCESSING → IDLE
- **TTS mode**: IDLE → PROCESSING → SPEAKING → IDLE
- **TTS replay**: IDLE → SPEAKING → IDLE

Self-transitions (same state) are explicitly blocked to prevent double-entry.

---

## Module Map

All modules are in `src/` directory unless noted.

### Core Modules

| Module | Size | Purpose | Key Classes |
|--------|------|---------|------------|
| `main.py` | ~1950 LOC | Entry point; VoicePasteApp orchestrator; state machine, pipeline logic | `VoicePasteApp` (god object) |
| `config.py` | ~1000 LOC | Configuration loading, validation, persistence; keyring integration | `AppConfig` (mutable dataclass) |
| `constants.py` | ~550 LOC | Enums (AppState), defaults, system prompts, Piper voice registry (14 voices), ElevenLabs presets | `AppState` enum, `VALID_TRANSITIONS` frozenset |

### Audio & Recording

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `audio.py` | Microphone recording via sounddevice (16kHz, mono, int16, in-memory WAV) | `AudioRecorder` (context manager) |
| `audio_playback.py` | TTS audio output via miniaudio (MP3/WAV transparent) | `AudioPlayer` |
| `notifications.py` | Audio cue synthesis (beeps, tones via winsound or sounddevice) | `play_recording_start_cue()`, `play_recording_stop_cue()`, etc. |
| `overlay.py` | Floating overlay UI (tkinter Toplevel, non-focus-stealing, 200×56px) | `OverlayWindow` (daemon thread) |

### Speech-to-Text (STT)

| Module | Purpose | Key Classes |
|--------|---------|------------|
| `stt.py` | Cloud STT via OpenAI Whisper API; timeout handling; retry logic | `CloudWhisperSTT` (protocol implementation), `STTBackend` (protocol) |
| `local_stt.py` | Local STT via faster-whisper (CTranslate2 quantization); Silero VAD; language auto-detect | `LocalWhisperSTT` |
| `model_manager.py` | Download/lifecycle for Whisper models (75MB–3GB); cache at `~/.cache/VoicePaste/models/stt/` or `%LOCALAPPDATA%\VoicePaste\models\stt\`; SHA256 verification | `WhisperModelManager` |

### Summarization & LLM

| Module | Purpose | Key Classes |
|--------|---------|------------|
| `summarizer.py` | LLM-based text cleanup; multi-provider support (OpenAI, OpenRouter, Ollama); graceful fallback | `CloudLLMSummarizer`, `PassthroughSummarizer` |
| `claude_code.py` | Claude Code CLI integration (v1.2+); subprocess control; timeout; response capture | `ClaudeCodeBackend` |

### Text-to-Speech (TTS)

| Module | Purpose | Key Classes |
|--------|---------|------------|
| `tts.py` | Cloud TTS via ElevenLabs API; voice presets; model selection | `ElevenLabsTTS`, `TTSBackend` (protocol) |
| `local_tts.py` | Local TTS via Piper ONNX; phonemization via espeak-ng (ctypes); WAV output | `PiperLocalTTS` |
| `tts_model_manager.py` | Download/lifecycle for Piper voice models (~60–120MB each); 14 voices (German, US, GB); direct HTTPS (no hf_hub_download); SHA256 verification | `PiperModelManager` |
| `tts_cache.py` | LRU audio cache (deduplication, replayed from tray menu); size/age/count eviction | `TTSAudioCache`, `CacheConfig` |
| `tts_export.py` | Save synthesized audio to user-chosen folder (timestamped filenames) | `TTSAudioExporter` |
| `tts_orchestrator.py` | Coordinate TTS playback, caching, export; route TTS responses | `TTSOrchestrator` |

### Hotkey & Input

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `hotkey.py` | Hotkey dispatcher (Windows: `keyboard` lib, Linux X11: `pynput`, Linux Wayland: `evdev`); auto-detection via `XDG_SESSION_TYPE` | `HotkeyManager`, `_parse_hotkey()` |
| `evdev_hotkey.py` | Linux Wayland evdev daemon monitor; /dev/input/* device reading; UInput keystroke injection | `EvdevHotkeyManager`, `UInputController` |

### System Integration

| Module | Purpose | Key Classes |
|--------|---------|------------|
| `tray.py` | System tray icon/menu via pystray; state-colored icon drawing | `TrayManager` |
| `icon_drawing.py` | Dynamic PIL-based tray icon generation (grey/red/yellow/green/blue/error states) | Icon drawing functions |
| `settings_dialog.py` | Tabbed tkinter settings UI (sv_ttk dark theme); hot-reload on save | `SettingsDialog` (v0.7+) |
| `platform_impl/__init__.py` | Platform abstraction dispatch (detects OS at import, re-exports from `_windows.py` or `_linux.py`) | N/A (module loader) |
| `platform_impl/_windows.py` | Windows-specific: clipboard (ctypes WinAPI), Ctrl+V simulation (ctypes), winsound beeps, Win32 mutex lock | `WindowsClipboard`, `WindowsPaste`, etc. |
| `platform_impl/_linux.py` | Linux-specific: clipboard (xclip/wl-copy), Ctrl+V simulation (xdotool/evdev UInput/ydotool), sounddevice beeps, fcntl file lock | `LinuxClipboard`, `LinuxPaste`, etc. |

### Utilities

| Module | Purpose | Key Functions |
|--------|---------|------------|
| `api_server.py` | Localhost REST API (v0.9+); Flask-like routing; CORS, rate limiting, 127.0.0.1-only | `start_api_server()`, `stop_api_server()` |
| `api_dispatch.py` | API controller; routes HTTP requests to VoicePasteApp methods | `APIController` |
| `wake_word.py` | Continuous background audio monitoring for wake phrase (hands-free mode); local Whisper tiny | `WakeWordDetector` |
| `keyring_store.py` | Windows Credential Manager integration (v0.3+); get/set encrypted credentials | `get_credential()`, `set_credential()` |
| `integrity.py` | File hash verification (SHA256); graceful degradation for missing hashes | `compute_file_sha256()`, `verify_file_sha256()`, `verify_directory_files()` |

---

## Platform Abstraction

VoicePaste abstracts all platform-specific code (Windows vs. Linux) behind a single interface in `src/platform_impl/__init__.py`. At import time, the correct backend (`_windows.py` or `_linux.py`) is selected and re-exported.

### Clipboard Operations

| Operation | Windows | Linux X11 | Linux Wayland |
|-----------|---------|-----------|---------------|
| **Read** | ctypes WinAPI `GetClipboardData()` | `xclip -o -selection clipboard` | `wl-paste` (preferred) or xclip via XWayland |
| **Write** | ctypes WinAPI `SetClipboardData()` | `xclip -i -selection clipboard` | `wl-copy` (preferred) or xclip via XWayland |
| **Backup/Restore** | Text format only (CF_UNICODETEXT) | Text format (CLIPBOARD selection) | Text format |
| **Delay** | 0ms (synchronous) | 150ms (xclip is async) | 150ms (wl-paste/wl-copy async) |

### Paste Simulation & Terminal Detection

| Platform | Mechanism | Terminal Detection | Shortcut Options |
|----------|-----------|-------------------|------------------|
| **Windows** | ctypes keyboard_event (VK_CONTROL + VK_V) | None | N/A |
| **Linux X11** | `xdotool key ctrl+v --clearmodifiers` | xprop/xdotool WM_CLASS inspection (20+ terminals: GNOME Terminal, Konsole, Alacritty, kitty, xterm, etc.) | auto / ctrl+v / ctrl+shift+v |
| **Linux Wayland** | evdev UInput (preferred) → ydotool → wtype (fallback) | GNOME Shell D-Bus (gdbus) via `dbus_proxy` | auto / ctrl+v / ctrl+shift+v |

Auto-detection is the default (`paste_shortcut = "auto"` in config). Manual override available via `[paste] paste_shortcut` in config.toml.

### Hotkey Registration

| Platform | Library | Mechanism |
|----------|---------|-----------|
| **Windows** | `keyboard` | Low-level Windows hooks (requires admin) |
| **Linux X11** | `pynput.keyboard.GlobalHotKeys` | XLib (requires X11 display) |
| **Linux Wayland** | `evdev` | Direct /dev/input/event* monitoring; daemon thread; auto-detects key codes from keyboard layout |

Auto-detection: `XDG_SESSION_TYPE` environment variable (x11 vs wayland). Hotkey registration happens in `hotkey.py` dispatcher.

### Single-Instance Lock

| Platform | Method |
|----------|--------|
| **Windows** | Win32 named mutex (`CreateMutexA`) |
| **Linux** | File lock on `/tmp/voicepaste.lock` (fcntl) |

---

## Backend Protocols

VoicePaste uses Python Protocol classes to define pluggable backends for STT, summarization, and TTS. Each backend has a factory function that instantiates the correct implementation based on config.

### Speech-to-Text (STT)

**Protocol** (`src/stt.py::STTBackend`):
```python
class STTBackend(Protocol):
    async def transcribe(self, audio_wav: bytes, language: str) -> str:
        """Transcribe audio to text."""
```

**Implementations**:
- `CloudWhisperSTT`: OpenAI Whisper API (2–5s latency, ~$0.006/min)
- `LocalWhisperSTT`: faster-whisper (15–60s latency, free, offline)

**Factory**: `create_stt_backend(config) -> STTBackend`

### Summarization

No formal protocol (simpler interface).

**Implementations**:
- `CloudLLMSummarizer`: OpenAI, OpenRouter, Ollama (LLM-based text cleanup)
- `PassthroughSummarizer`: No-op (returns raw transcript)

### Text-to-Speech (TTS)

**Protocol** (`src/tts.py::TTSBackend`):
```python
class TTSBackend(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio."""
```

**Implementations**:
- `ElevenLabsTTS`: Cloud TTS (high quality, ~$0.30/1M chars, MP3 output)
- `PiperLocalTTS`: Local ONNX TTS (free, offline, WAV output, 14 voices)

**Factory**: `create_tts_backend(config) -> TTSBackend`

---

## Configuration & Credential Storage

Configuration is split across two systems:

### 1. Config File (`config.toml`)

User-editable TOML file in application directory. Contains non-secret settings (hotkeys, backend selection, model sizes, etc.).

**Sections**:
- `[hotkey]`: Recording, voice prompt, TTS hotkeys
- `[api]`: HTTP API enable/port
- `[transcription]`: STT backend, model size, device, compute type, VAD, language
- `[summarization]`: Enable, provider, model, base_url, custom prompt
- `[tts]`: Provider, voice selection, speed
- `[paste]`: Confirmation, delay, shortcut override
- `[handsfree]`: Wake word, match mode, pipeline, timeouts
- `[tts_cache]`: Cache size, age, entry limits
- `[tts_export]`: Export folder
- `[claude_code]`: CLI integration settings
- `[feedback]`: Audio cues, overlay toggle
- `[logging]`: Log level

### 2. Credential Store

API keys stored in OS credential manager (never in config.toml or logs):
- **Windows**: Credential Manager (via keyring_store.py)
- **Linux**: Secret Service (via keyring library)

**Keys**:
- OpenAI API key (Whisper, GPT-4o-mini)
- OpenRouter API key (Claude, Llama, etc.)
- ElevenLabs API key (TTS)

### Hot-Reload

Settings dialog (`settings_dialog.py`) allows live reconfiguration without restart. Changes are immediately applied via:
1. New `STTBackend` instance if STT settings changed
2. New `Summarizer` instance if summarization settings changed
3. New `TTSBackend` instance if TTS settings changed
4. New `HotkeyManager` instance if hotkeys changed

Config is persisted to disk via `AppConfig.save_to_toml()`.

---

## Build System

VoicePaste uses PyInstaller to bundle Python runtime, dependencies, and assets into a portable executable.

### Windows Build

**Spec File**: `voice_paste.spec`

**Process**:
1. `build.bat` triggers PyInstaller with `--onefile` and `--windowed` flags
2. Bundles: Python runtime, all pip dependencies, PIL icon, sound libraries (winsound)
3. Runtime hook `rthook_onnxruntime.py` handles onnxruntime DLL loading

**Output**: `dist\voice_paste.exe` (~280 MB, includes optional Piper models)

**Build Time**: 2–3 minutes

**Known Issues**:
- onnxruntime segfault with --onefile when loading Silero VAD ONNX file. Workaround: VAD auto-disabled in frozen builds; users can re-enable in config.toml.

### Linux Build

**Spec File**: `voice_paste_linux.spec`

**Process**:
1. `build_linux.sh` requires `--system-site-packages` venv (PEP 668 enforces this on modern Ubuntu)
2. System packages must be pre-installed (espeak-ng, libportaudio2, xclip, xdotool, python3-gi, gir1.2-ayatanaappindicator3-0.1)
3. Linux-specific pip packages: `pynput` (X11 hotkeys, required), `evdev` (Wayland hotkeys, required for Wayland support)
4. PyInstaller bundles: Python runtime, all pip dependencies, PNG icon, .desktop file

**Output**: `dist/VoicePaste` (~241 MB portable binary)

**Build Recipe**:
```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller pynput evdev
./build_linux.sh
```

**Checks**:
- build_linux.sh verifies `pynput` is installed (fatal error if missing)
- build_linux.sh warns if `evdev` is missing (non-fatal; Wayland support unavailable but X11 still works)

**Binary Size Optimization**: System packages (PyGObject, AppIndicator3) not included; they must be pre-installed.

---

## Security Architecture

### Audio Privacy

- **In-Memory Only**: Audio data never written to disk. Recorded WAV stays in memory (byte buffer) until discarded.
- **No Keystroke Logging**: Hotkey listener does not log key presses (privacy fix v1.4).
- **Clipboard Isolation**: Original clipboard backed up before paste, restored after (150ms delay).

### API Key Protection

- **Credential Manager**: Keys stored in OS credential manager (Windows Credential Manager / Linux Secret Service), never in plain text files.
- **No Logging**: Masked API keys logged (e.g., `****...xxxx`); full keys never written to logs.
- **Secure File Permissions**: config.toml restricted to user-only (0600 mode on Linux).

### Model Integrity

- **SHA256 Verification** (v0.8+): Downloaded Whisper and Piper models verified against hashes in `src/constants.py`.
- **Graceful Degradation**: Empty hash dicts log warning but allow operation (hashes collected post-release).
- **Direct HTTPS**: Models downloaded directly from Hugging Face CDN (bypasses XetStorage bugs in hf_hub_download).

### Input Device Access (Linux Wayland)

- **UInput Restrictions** (SEC-082): evdev UInput capabilities limited to `KEY_LEFTCTRL`, `KEY_LEFTSHIFT`, `KEY_V` (paste-only, prevents arbitrary keystroke injection).
- **Group Membership**: User must be in `input` group for `/dev/input/*` access.
- **Udev Rules**: `/etc/udev/rules.d/99-voicepaste-uinput.rules` grants input group write access to `/dev/uinput`.
- **Cleanup on Shutdown** (SEC-083): `cleanup_uinput()` called on application exit.

---

## Data Flow Examples

### Example 1: Normal Recording (Ctrl+Alt+R)

```
1. User presses Ctrl+Alt+R
   → Hotkey dispatcher calls VoicePasteApp._on_hotkey_record()
   → State: IDLE → RECORDING
   → Audio cue: Start tone (440→880 Hz)

2. User speaks into microphone
   → AudioRecorder (sounddevice) captures at 16kHz, mono, int16
   → Bytes accumulated in WAV buffer

3. User presses Ctrl+Alt+R again (or auto-stop on 3s silence)
   → State: RECORDING → PROCESSING
   → Audio cue: Stop tone (880→440 Hz)

4. Pipeline worker thread spawned
   → Send WAV to STT backend:
      - Cloud: POST to OpenAI Whisper API
      - Local: Run faster-whisper (+ Silero VAD if enabled)
   → Transcript received

5. [Optional] Summarization:
   → Send transcript + system prompt to LLM (OpenAI/OpenRouter/Ollama)
   → Receive cleaned text

6. Clipboard & Paste:
   → Backup original clipboard (text format)
   → Write result to clipboard (xclip/wl-copy/ctypes)
   → Simulate Ctrl+V (xdotool/evdev UInput/ctypes)
   → Wait 150ms
   → Restore original clipboard

7. State: PROCESSING → PASTING → IDLE
   → Tray icon: Yellow → Green → Grey
```

### Example 2: Voice Prompt Q&A (Ctrl+Alt+A)

```
1. User presses Ctrl+Alt+A
   → State: IDLE → RECORDING

2. User speaks question ("What's the capital of France?")
   → AudioRecorder captures speech

3. User presses Ctrl+Alt+A again
   → State: RECORDING → PROCESSING

4. Pipeline worker spawned:
   → STT: Transcribe to "What's the capital of France?"
   → [No summarization]
   → Prompt to LLM: "Du bist ein hilfreicher Assistent. Antworte praezise..."
      + "What's the capital of France?"
   → LLM responds: "Paris"

5. Clipboard & Paste:
   → Write "Paris" to clipboard
   → Simulate Ctrl+V

6. State: PROCESSING → PASTING → IDLE
```

### Example 3: TTS Audio Readback (Ctrl+Alt+T / Ctrl+Alt+S)

```
1. User copies text to clipboard
2. User presses Ctrl+Alt+T (Windows) or Ctrl+Alt+S (Linux)
   → State: IDLE → PROCESSING

3. Paste worker spawned:
   → Read clipboard text
   → Check TTS cache (if enabled):
      - Hit: Retrieve cached WAV/MP3
      - Miss: Call TTS backend (ElevenLabs cloud or Piper local)
   → Store in cache

4. Audio playback:
   → State: PROCESSING → SPEAKING
   → miniaudio plays WAV or MP3
   → Audio cue on finish: 660→440 Hz

5. State: SPEAKING → IDLE
```

---

## Version History

| Version | Date | Key Features |
|---------|------|-------------|
| v0.1.0 | 2025-02-13 | MVP: hotkey, record, transcribe, paste |
| v0.2.0 | 2025-02-13 | Summarization, audio cues, clipboard preservation, error handling |
| v0.3.0 | 2026-02-13 | Settings dialog, keyring integration, multiple LLM providers |
| v0.4.0 | 2026-02-13 | Local STT (faster-whisper), model manager, VAD filter |
| v0.5.0 | 2026-02-13 | Voice Prompt mode (Q&A), dynamic icon drawing |
| v0.6.0 | 2026-02-18 | ElevenLabs TTS, TTS hotkeys (Ctrl+Alt+T/Y), audio playback |
| v0.7.0 | 2026-02-19 | Local TTS via Piper (14 voices), model download manager |
| v0.8.0 | 2026-02-19 | Floating overlay UI, SHA256 model verification |
| v0.9.0 | 2026-02-20 | HTTP API, Confirm-before-paste, Hands-Free wake word mode |
| v1.0.0 | 2026-02-20 | TTS audio cache with deduplication, TTS export to files |
| v1.1.0 | 2026-02-20 | Full Linux support (Ubuntu 22.04/24.04), Wayland/X11 hotkeys, evdev UInput paste, CUDA auto-detection |

---

## Testing

**Framework**: pytest + pytest-mock + pytest-timeout
**Coverage**: 47% overall (337 tests)
**Focus**: Config loading, state machine, audio, paste, hotkey registration, STT, summarization, TTS, notifications, tray icon, clipboard, security

**Test Files**:
- `test_config.py` — Configuration loading, validation, hot-reload
- `test_state_machine.py` — State transitions, invalid transitions
- `test_audio.py` — Microphone recording (mocked)
- `test_hotkey.py` — Hotkey registration, parsing
- `test_stt.py` — Cloud STT API mocking
- `test_summarizer.py` — LLM response mocking
- `test_tts.py` — ElevenLabs API mocking
- `test_local_tts.py` — Piper model loading
- `test_notifications.py` — Audio cue synthesis
- `test_tray.py` — Tray icon menu
- `test_clipboard.py` — Clipboard read/write (Windows only)
- `test_paste.py` — Paste simulation (Windows only)
- `test_overlay.py` — Floating overlay rendering
- `test_integrity.py` — SHA256 verification

**CI/CD**: GitHub Actions (Windows, Ubuntu 22.04, Ubuntu 24.04)

---

## Key Design Decisions

### Protocol-Based Backends
STT, TTS, and summarization use Python Protocol classes rather than inheritance. This allows:
- Easy addition of new backends (OpenAI → OpenRouter → Ollama)
- Minimal coupling (each backend is independent)
- Type safety (Protocol enforced at type-check time)

### In-Memory Audio Only
Audio is never written to disk (privacy requirement). Recording data stays in WAV byte buffer; on cancellation, buffer is discarded. This prevents accidental data leaks.

### Clipboard Backup/Restore
Original clipboard preserved before paste (except non-text data). Restored after 150ms delay. Handles the case where user accidentally pastes into wrong window.

### Daemon Threads for Long-Running Operations
Hotkey listener, API server, overlay UI, wake word detector all run as daemon threads. This allows clean shutdown: main thread exits, all daemons are terminated automatically.

### Platform Abstraction via Import-Time Dispatch
`platform_impl/__init__.py` detects OS at import and re-exports the correct backend. No runtime conditionals; simpler than if/else in every function.

### Model Download Direct HTTPS
Models downloaded directly from Hugging Face CDN, not via `hf_hub_download()` (which uses XetStorage and has bugs). Direct HTTPS bypasses XetStorage but requires manual hash verification.

### Auto-Detection of Session Type (Linux)
`XDG_SESSION_TYPE` environment variable determines hotkey backend (x11 vs wayland). Allows same codebase to support both without user configuration.

---

## Limitations & Known Issues

| Issue | Scope | Workaround |
|-------|-------|-----------|
| onnxruntime segfault with --onefile + VAD | Frozen .exe | Disable VAD in settings or use cloud STT |
| Antivirus flags keyboard hooks | Windows | Whitelist .exe or disable AV temporarily |
| Terminal paste shortcuts vary | Linux | Auto-detect terminal and use Ctrl+Shift+V; manual override in config |
| Wayland terminal detection (non-GNOME) | Linux Wayland | Fall back to manual `paste_shortcut = "ctrl+v"` or `"ctrl+shift+v"` |
| Model hash verification incomplete | v0.8 | Empty hashes log warning but allow operation; hashes collected post-release |
| No multi-turn conversation context | Voice Prompt | Each prompt is stateless; context resets after paste |

