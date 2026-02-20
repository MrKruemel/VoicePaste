# Architecture Decision Record (ADR)

## Voice Paste Tool

**Date**: 2026-02-14
**Status**: Accepted
**Author**: Solution Architect
**Current Version**: 0.9.0

---

## 1. Context and Problem Statement

We are building a Windows desktop tool that:
1. Captures global hotkeys to start/stop recording (default: Ctrl+Alt+R for normal mode, Ctrl+Alt+A for voice prompt mode)
2. Records microphone audio to an in-memory buffer
3. Transcribes speech to text (cloud or local)
4. Optionally summarizes/cleans the transcript (v0.2+)
5. Optionally sends transcript as a prompt to LLM for Q&A (v0.5+)
6. Pastes the result at the current cursor position
7. Provides a settings dialog for easy configuration (v0.3+)
8. Stores API keys securely in Windows Credential Manager (v0.3+)

The tool must ship as a **single-file .exe** via PyInstaller, support multiple transcription and summarization backends, and never steal focus or disrupt the user's workflow.

---

## 2. Architecture Overview

### Component Diagram (v0.9)

```
+------------------------------------------------------------------+
|                        main.py (Entry Point)                      |
+------------------------------------------------------------------+
|                                                                    |
|  +------------------+     +------------------+                     |
|  |   Config Loader  |     |   Logger Setup   |                     |
|  |  (config.toml)   |     | (voice-paste.log)|                     |
|  +------------------+     +------------------+                     |
|                                                                    |
|  +------------------+     +------------------+                     |
|  |  Hotkey Manager  |---->|  State Machine   |                     |
|  | (keyboard lib)   |     |  (AppState enum) |                     |
|  +------------------+     +------------------+                     |
|                                |                                   |
|                    +-----------+-----------+                        |
|                    |                       |                        |
|              +-----v------+         +------v-------+               |
|              |   Audio     |         |   Paste      |              |
|              |   Recorder  |         |   Manager    |              |
|              | (sounddevice)|        | (clipboard + |              |
|              +-----+------+         |  Ctrl+V sim) |              |
|                    |                 +--------------+               |
|              +-----v------+                                        |
|              | STT Backend |  <-- Factory + Protocol               |
|              +-----+------+                                        |
|                    |                                                |
|         +----------+----------+                                    |
|         |                     |                                     |
|  +------v-------+   +--------v--------+                            |
|  | Cloud Whisper|   | Local Whisper   |                            |
|  | (OpenAI API) |   | (faster-whisper)|  (v0.4+)                   |
|  +--------------+   +-----------------+                            |
|        |                     |                                      |
|        +----------+----------+                                      |
|                   |                                                |
|              +----v--------+                                      |
|              | Model Manager|  (v0.4+, downloads from HF)          |
|              +--------------+                                      |
|                                                                    |
|              +----------------+                                    |
|              | Summarizer     |  <-- Factory + Protocol            |
|              +-------+--------+                                    |
|                      |                                             |
|           +----------+----------+                                  |
|           |          |          |                                   |
|  +--------v--+  +----v----+  +--v--------+                         |
|  | OpenAI    |  |OpenRouter|  | Ollama   |                         |
|  | GPT-4o-mi |  | Claude   |  | Local    |  (v0.3+)                |
|  | (API)     |  | (API)    |  | (local)  |                         |
|  +-----------+  +----------+  +----------+                          |
|                                                                    |
|              +----------------+                                    |
|              | TTS Backend    |  <-- Factory + Protocol (v0.6+)   |
|              +-------+--------+                                    |
|                      |                                             |
|           +----------+----------+                                  |
|           |                     |                                   |
|  +--------v--+         +--------v--------+                         |
|  | ElevenLabs |        | Piper (ONNX)   |                         |
|  | (cloud,    |        | (local, v0.7+) |                         |
|  |  MP3)      |        | espeak-ng      |  (v0.6+)                |
|  +-----------+         +--------+-------+                          |
|                                |                                   |
|                        +-------v-------+                          |
|                        | Model Manager |  (v0.7+, HF HTTPS)       |
|                        +--------------+                          |
|                                                                    |
|  +------------------+                                              |
|  |  Settings Dialog |  (v0.7+, ttk.Notebook, tabbed)             |
|  |  Tabs:           |   - Transcription, Summarization           |
|  |  - Transcript    |   - Text-to-Speech (v0.6+)                 |
|  |  - Summary       |   - General (credentials, feedback, logging)|
|  |  - Text-to-Speech|   - Hot-reload without restart              |
|  |  - General       |   - Dark theme via sv_ttk                  |
|  +------------------+                                              |
|                                                                    |
|  +------------------+                                              |
|  |  Keyring Store   |  (v0.3+, Windows Credential Manager)        |
|  +------------------+                                              |
|                                                                    |
|  +------------------+     +------------------+                     |
|  |  Icon Drawing    |     |  System Tray     |                     |
|  |  (icon_drawing)  |     |  (pystray)       |  (v0.5 dynamic)    |
|  +------------------+     +------------------+                     |
|                                                                    |
|  +------------------+                                              |
|  | Notifications    |  (audio cues + toast notifications)          |
|  +------------------+                                              |
+--------------------------------------------------------------------+
```

### State Machine (v0.9)

```
                    +-------+
                    | IDLE  |<--------+
                    +---+---+         |
                        |             |
                   Hotkey press       |
                   (Ctrl+Alt+R/A/T/Y) |
                        |             |
                    +---v-------+     |
                    | RECORDING |--Escape->(CANCELLED)
                    +---+-------+     |
                        |             |
                   Hotkey press       |
                        |             |
                    +---v--------+    |
                    | PROCESSING |    |
                    +---+--------+    |
                        |             |
                   (If paste delay/   |
                    confirmation)    |
                        |             |
                    +---v--------+    |
                    |AWAITING_   |--Escape->(CANCELLED)
                    |PASTE  (v0.9)|   |
                    +---+--------+    |
                        |             |
                    +---+---+         |
                    |       |         |
              (No TTS)  (With TTS)    |
                    |       |         |
              +-----v+ +----v----+    |
              |PASTING| |SPEAKING |   |
              +-----+-+ +----+----+   |
                    |        |        |
                    +---+----+--------+
                        |
                       IDLE
```

**States (v0.9):**
- **IDLE**: Waiting for hotkey. Tray icon is grey.
- **RECORDING**: Capturing audio from microphone. Tray icon is red. Active for normal mode (Ctrl+Alt+R), voice prompt (Ctrl+Alt+A), TTS ask (Ctrl+Alt+Y), or hands-free wake word (v0.9+).
- **PROCESSING**: Audio sent to STT; transcript sent to summarizer, prompt handler, or TTS synthesis. Tray icon is yellow.
- **AWAITING_PASTE** (v0.9+): Post-processing pause before paste. Activated if `paste_delay_seconds > 0` or `paste_require_confirmation = true`. Tray icon is teal. Press Enter to paste immediately or Escape to cancel.
- **PASTING**: Text placed on clipboard and Ctrl+V simulated. Tray icon is green. Returns to IDLE immediately after paste.
- **SPEAKING** (v0.6+): TTS audio playback in progress. Tray icon is blue. Triggered by Ctrl+Alt+T (read clipboard) or Ctrl+Alt+Y (ask AI + TTS). Returns to IDLE on completion or Escape.
- **CANCELLED**: Recording discarded (via Escape). Returns to IDLE with notification.

**Error handling**: If any state encounters an error (API failure, mic error, model load failure, TTS synthesis error), log it, show toast notification, and return to IDLE.

---

## 3. Decision: Speech-to-Text Backend

### Options Evaluated (v0.1–v0.4)

| Option | Quality | Binary Size | Latency | Offline | Complexity |
|--------|---------|-------------|---------|---------|------------|
| **OpenAI Whisper API (cloud)** | Excellent | +0 MB | 2-5s | No | Low |
| faster-whisper (tiny) | Good | +75 MB | 3-8s | Yes | Medium |
| faster-whisper (base) | Very Good | +145 MB | 5-15s | Yes | Medium |
| Deepgram API | Excellent | +0 MB | 1-3s | No | Low |

### Decision: Cloud default (v0.1–v0.4). Local option available (v0.4+).

**Rationale:**
1. **Binary size**: Cloud adds zero bytes. Local adds 75–3000 MB.
2. **Quality**: Whisper API has excellent German transcription.
3. **Simplicity**: Single HTTP POST. No model loading, no GPU detection.
4. **User choice**: v0.4 allows users to opt-in to local transcription.
5. **Backend abstraction**: Factory function `create_stt_backend()` cleanly selects implementation.

**Tradeoffs accepted:**
- Cloud requires internet and API key
- Audio leaves the machine (addressed in threat model)
- Cost ~$0.006 per minute of audio

### v0.4 Enhancement: Local Transcription

**New capabilities:**
- `LocalWhisperSTT` class using faster-whisper (CTranslate2)
- Model Manager downloads models from Hugging Face Hub
- 6 model sizes (tiny ~75MB to large-v3 ~3GB)
- Silero VAD filter (via onnxruntime) to skip silence
- Configurable device (CPU, CUDA)
- Configurable quantization (int8, float16, float32)
- Lazy model loading on first transcription
- Thread-safe design

**Known issue**: onnxruntime crashes in PyInstaller --onefile builds. VAD auto-disabled in frozen .exe (users can re-enable if stable).

---

## 4. Decision: Summarization Backend

### Options Evaluated (v0.1–v0.3)

| Option | Quality (German) | Cost | Latency | Binary Impact |
|--------|-------------------|------|---------|---------------|
| **OpenAI GPT-4o-mini** | Very Good | ~$0.0001/call | 1-2s | +0 MB |
| OpenAI GPT-4o | Excellent | ~$0.005/call | 2-4s | +0 MB |
| Claude 3.5 Haiku | Very Good | ~$0.0003/call | 1-2s | +0 MB |
| Local (llama-cpp) | Moderate | Free | 5-30s | +2-4 GB |

### Decision: Cloud by default (v0.2+). Multiple providers in v0.3+.

**v0.2 Decision**: OpenAI GPT-4o-mini for summarization.

**Rationale:**
1. **Quality/cost**: Excellent German output at ~$0.0001 per call.
2. **Same API key**: Uses existing OpenAI credential.
3. **Speed**: 1-2 seconds fits pipeline latency budget.
4. **v0.1 simplicity**: Passthrough (no-op) summarizer validates pipeline.

**v0.3 Enhancement**: Multiple providers

**New capabilities:**
- `CloudLLMSummarizer` supports OpenAI, OpenRouter, Ollama
- Custom base URLs for proxies or self-hosted
- Custom system prompts per user
- Hot-reload from Settings dialog

**Supported providers:**
- **OpenAI**: gpt-4o-mini (default), other models
- **OpenRouter**: Access to Claude, Llama, etc. Requires OpenRouter API key.
- **Ollama**: Local LLM at localhost:11434. No API key needed.

---

## 5. Decision: Voice Prompt Mode (v0.5)

### New Feature: Interactive Q&A

**Use case**: User speaks a question, LLM generates answer, answer is pasted.

**Implementation:**
- Separate hotkey: Ctrl+Alt+A (configurable)
- Same pipeline: Record → Transcribe → Send to LLM → Paste
- Different system prompt: "helpful assistant" (not "text cleanup assistant")
- Uses same provider configuration as summarization
- Can be disabled by setting `prompt_combination = ""` in config

**System Prompt (German):**
```
Du bist ein hilfreicher Assistent. Antworte praezise und in derselben Sprache wie die Frage.
```

**Stored in**: `constants.py` line 66-69 as `PROMPT_SYSTEM_PROMPT`

---

## 6. Decision: Text-to-Speech Backend (v0.6+)

### Options Evaluated (v0.6–v0.7)

| Option | Quality | Binary Size | Latency | Offline | Complexity | Cost |
|--------|---------|-------------|---------|---------|------------|------|
| **ElevenLabs API (cloud)** | Excellent (human-quality) | +0 MB | 2-4s | No | Low | ~$0.30/1M chars |
| Piper ONNX (local) | Good | +50–120 MB per voice | 1-3s | Yes | Medium | Free |
| Google Cloud TTS | Excellent | +0 MB | 2-3s | No | Low | ~$16/1M chars |
| Azure Speech | Good | +0 MB | 2-3s | No | Low | Varies |

### Decision: Cloud default (ElevenLabs, v0.6). Local option available (Piper, v0.7+).

**v0.6 Decision**: ElevenLabs for cloud TTS.

**Rationale:**
1. **Quality**: Human-quality, natural-sounding voices (especially for German).
2. **Binary size**: Zero impact on .exe size.
3. **Voice selection**: Rich library of voices, easy voice ID browsing.
4. **Latency**: 2-4 seconds acceptable for typical clipboard text.
5. **Cost**: Low per-use cost (~$0.30/1M characters).

**v0.7 Enhancement**: Piper local TTS

**New capabilities:**
- `PiperLocalTTS` class using ONNX inference + espeak-ng phonemization (ctypes)
- 5 pre-configured German voices (thorsten variants, kerstin, eva_k)
- Direct HTTPS downloads from Hugging Face (bypasses Xet Storage issues)
- Model caching in `%LOCALAPPDATA%\VoicePaste\models\tts\`
- Zero cost, works offline, no API key needed
- Binary impact: only loader (~0.5 MB); models downloaded on demand

**Download Fix (v0.7)**:
- Replaced `hf_hub_download` with direct HTTPS streaming from Hugging Face CDN
- Fixes: AttributeError with Xet Storage repos, stale .lock file infinite retries
- Benefits: Simpler, faster, more reliable

---

## 7. Decision: Audio Playback Library

### Options Evaluated

| Option | Global Hotkey | Reliability | PyInstaller | Windows Focus |
|--------|---------------|-------------|-------------|---------------|
| **keyboard** | Yes | High | Good | Does not steal |
| pynput | Yes | Medium | Medium | May steal |
| win32api (ctypes) | Yes | High | Excellent | Does not steal |

### Decision: `keyboard` library

**Rationale:**
1. **Proven reliability** for global hotkeys without admin rights.
2. **Simple API**: `keyboard.add_hotkey('ctrl+alt+r', callback)`
3. **PyInstaller compatible**: No known bundling issues.
4. **Does not steal focus**: Hooks at OS level.
5. **Multi-hotkey support**: Handles both normal and voice prompt hotkeys in v0.5.

**Tradeoffs accepted:**
- Some antivirus software may flag low-level hooks (documented in README)
- Requires explicit permission handling (admin recommended)

---

### Audio Playback Library (v0.6+)

**Decision**: miniaudio (C library via ctypes) for audio playback.

**Rationale:**
1. **Format support**: Handles both MP3 (ElevenLabs) and WAV (Piper) transparently.
2. **Low latency**: Suitable for real-time TTS response playback.
3. **Minimal dependencies**: Single shared library, no Python wrappers needed.
4. **PyInstaller friendly**: Bundled or dynamically loaded.
5. **Cross-platform**: Available on Windows, macOS, Linux.

---

## 8. Decision: Audio Capture Library

### Options Evaluated

| Option | API Quality | PyInstaller | Latency | Dependencies |
|--------|-------------|-------------|---------|--------------|
| **sounddevice** | Clean | Good | Low | PortAudio (bundled) |
| pyaudio | Functional | Complex | Low | PortAudio (manual) |

### Decision: `sounddevice`

**Rationale:**
1. **Clean API**: `sd.rec()` with NumPy arrays.
2. **Bundles PortAudio**: No manual DLL management.
3. **PyInstaller friendly**: Works with hidden imports.
4. **Low latency**: Suitable for real-time start/stop.

**PyInstaller requirement**: Must include `_sounddevice_data` as hidden import or data file.

---

## 9. Decision: Clipboard and Paste Strategy

### Strategy: Backup → Write → Simulate Ctrl+V → Restore (v0.2+)

**Implementation:**
1. Read current clipboard contents (backup)
2. Write text to clipboard
3. Simulate Ctrl+V keystroke
4. Wait 150ms for paste to complete
5. Restore original clipboard contents

**Library**: `win32clipboard` (pywin32) for reliable read/write.

**v0.1 simplification**: Skip backup/restore.

**Tradeoffs:**
- Paste delay is a heuristic (150ms default)
- Some terminals may not respond to simulated Ctrl+V (documented)

---

## 10. Decision: System Tray Library

### Decision: `pystray` with Pillow icon generation (v0.5)

**Rationale:**
1. **Cross-platform**: Good Windows support.
2. **Clean API**: Create icon, menu, run.
3. **Dynamic icons**: Programmatically generated colors via Pillow.
4. **PyInstaller compatible**: No bundling issues.

**v0.1–v0.2**: Static or simple Pillow-generated icons.

**v0.5 enhancement**: All icons generated at runtime with no bundled .png files. Icon colors:
- Grey: IDLE
- Red: RECORDING
- Yellow: PROCESSING
- Green: PASTING
- Red exclamation: ERROR

**Icon drawing module**: `icon_drawing.py` creates state-aware tray icons.

---

## 11. Decision: Settings Dialog (v0.3–v0.7)

### Evolution of Settings Dialog

**v0.3 (Initial)**: tkinter-based dialog with vertical LabelFrame sections for Credentials, Transcription, Summarization, Feedback.

**v0.6 (TTS Addition)**: Added Text-to-Speech tab for ElevenLabs configuration (voice ID, model, API key).

**v0.7 (Tabbed Redesign)**: Migrated to ttk.Notebook (modern tabbed interface) with sv_ttk dark theme. Reorganized into 4 clear tabs:
1. **Transcription**: Cloud/Local backend selection, model size, device, compute type, VAD filter, download progress
2. **Summarization**: Enable/disable, provider selection, model, custom base URL, custom prompt
3. **Text-to-Speech**: Enable/disable, cloud/local provider toggle, voice selection, model download (v0.6+)
4. **General**: Audio cues toggle, log level dropdown, API credential management (OpenAI, OpenRouter, ElevenLabs)

**Implementation:**
- tkinter-based dialog on dedicated thread
- Does NOT block pystray main thread
- Singleton guard (only one dialog at a time)
- Hot-reload: Changes apply immediately without restart
- Dark theme via sv_ttk for modern appearance

**Threading model:**
- Main thread: pystray event loop
- Settings thread: tkinter Tcl event loop (spawned on demand)
- Lock: `_settings_lock` prevents concurrent dialogs

---

## 12. Decision: Credential Storage (v0.3+)

### Windows Credential Manager via Keyring

**Rationale:**
1. **Security**: Credentials encrypted by Windows, not plain text
2. **User expectation**: Integrates with Windows built-in Credential Manager
3. **Backwards compatible**: Legacy config.toml keys auto-migrate to keyring on first load
4. **No additional UI**: Settings dialog handles everything

**Implementation**: `keyring_store.py` wraps Windows Credential Manager access.

**Credentials stored:**
- `VoicePaste:openai_api_key`
- `VoicePaste:openrouter_api_key`

**Config.toml changes:**
- v0.3+: API keys NOT stored in config.toml (moved to keyring)
- Legacy config.toml keys are migrated on first startup
- config.example.toml notes this clearly

---

## 13. Decision: Configuration Format and Hot-Reload

### Decision: TOML with hot-reload (v0.3+)

**Config structure (v0.5):**
```toml
[hotkey]
combination = "ctrl+alt+r"
prompt_combination = "ctrl+alt+a"

[api]
# Legacy location (migrated to Credential Manager automatically)

[transcription]
backend = "cloud"  # or "local"
model_size = "base"
device = "cpu"
compute_type = "int8"
vad_filter = true

[summarization]
enabled = true
provider = "openai"
model = "gpt-4o-mini"
base_url = ""
custom_prompt = ""

[feedback]
audio_cues = true

[logging]
level = "INFO"
```

**Hot-reload mechanism:**
- Settings dialog modifies AppConfig (dataclass, unfrozen in v0.3+)
- Non-secret fields written back to config.toml
- Secrets stored in keyring
- No restart required; changes take effect immediately

---

## 14. Decision: Threading Model

### Architecture (v0.1–v0.5)

```
Main Thread:     pystray event loop (system tray, message pump)
Thread 1:        keyboard hotkey listener (daemon)
Thread 2:        Recording + STT + Summarization + Paste (per session)
Thread 3:        Settings dialog tkinter loop (on demand, v0.3+)
```

**Synchronization:**
- `threading.Lock` protects AppState enum
- State transitions are atomic
- Settings dialog uses `_settings_lock` singleton guard

---

## 15. Decision: Python Version and Key Dependencies

| Dependency | Version | Purpose | Size Impact |
|------------|---------|---------|-------------|
| Python | 3.11+ | Runtime (tomllib, Protocol in stdlib) | Base |
| sounddevice | latest | Audio capture | ~5 MB (with PortAudio) |
| numpy | latest | Audio buffer handling | ~30 MB |
| keyboard | latest | Global hotkey | ~0.5 MB |
| pystray | latest | System tray | ~0.5 MB |
| Pillow | latest | Icon generation | ~5 MB |
| openai | latest | Whisper + GPT APIs | ~2 MB |
| pywin32 | latest | Clipboard, Credential Manager | ~10 MB |
| faster-whisper | optional | Local STT (v0.4+) | +~0.5 MB (loader only) |
| onnxruntime | optional | VAD filter (v0.4+) | +~50 MB |
| huggingface-hub | optional | Model downloads (v0.4+) | +~1 MB |

**Estimated .exe size:**
- Cloud-only: 50–60 MB
- With local STT: 150–200 MB (optional)

---

## 16. Project File Structure (v0.7)

```
C:\develop\speachtoText\
|-- src\
|   |-- main.py                      # Entry point, state machine orchestrator
|   |-- audio.py                     # Audio recording (microphone)
|   |-- audio_playback.py            # Audio playback for TTS (miniaudio via ctypes, v0.6+)
|   |-- stt.py                       # Cloud STT (OpenAI Whisper)
|   |-- local_stt.py                 # Local STT (faster-whisper, v0.4+)
|   |-- tts.py                       # TTS backend factory + protocol (v0.6+)
|   |-- local_tts.py                 # Local TTS (Piper ONNX + espeak-ng, v0.7+)
|   |-- summarizer.py                # Summarizer (OpenAI, OpenRouter, Ollama)
|   |-- paste.py                     # Clipboard + paste
|   |-- config.py                    # Config loading, AppConfig dataclass
|   |-- hotkey.py                    # Hotkey registration (keyboard lib)
|   |-- tray.py                      # System tray + context menu
|   |-- constants.py                 # Shared constants, AppState enum, prompts, voices
|   |-- notifications.py             # Audio cues + toast notifications
|   |-- settings_dialog.py           # Settings GUI (tkinter, v0.3+; tabbed v0.7+)
|   |-- keyring_store.py             # Keyring integration (v0.3+)
|   |-- model_manager.py             # STT model download/caching (v0.4+)
|   |-- tts_model_manager.py         # TTS model download/caching (v0.7+)
|   |-- icon_drawing.py              # Tray icon generation (v0.5+)
|-- docs\
|   |-- ADR.md                       # This file (Architecture Decision Record)
|   |-- UX-SPEC.md                   # UX specification and flows
|   |-- THREAT-MODEL.md              # Security threat model and privacy
|   |-- PROMPTS.md                   # System prompts (summarization + voice prompt)
|   |-- BACKLOG.md                   # Product backlog (v1.0 roadmap)
|-- tests\
|   |-- test_config.py
|   |-- test_state_machine.py
|   |-- test_audio.py
|   |-- test_paste.py
|   |-- test_tts.py                  # TTS backend tests (v0.6+)
|   |-- (13+ test files total)
|-- config.example.toml              # Configuration template (all options with comments)
|-- build.bat                        # PyInstaller build script
|-- voice_paste.spec                 # PyInstaller spec file
|-- rthook_onnxruntime.py            # PyInstaller runtime hook (v0.4+)
|-- requirements.txt                 # Python dependencies
|-- requirements-dev.txt             # Development dependencies
|-- README.md                        # User documentation (features, quick start, troubleshooting)
|-- CHANGELOG.md                     # Release notes (all versions)
```

**Note**: PyInstaller bundles everything into a single .exe. Multi-file structure is for development clarity.

---

## 17. Backend Abstraction Pattern

### STT Backend (Cloud + Local)

```python
class STTBackend(Protocol):
    def transcribe(self, audio_data: bytes, language: str = "en") -> str:
        """Transcribe audio bytes to text."""
        ...
```

**Factory function:**
```python
def create_stt_backend(config: AppConfig) -> STTBackend:
    if config.stt_backend == "local":
        return LocalWhisperSTT(config)
    return CloudWhisperSTT(config)
```

**Implementations:**
- `CloudWhisperSTT` (v0.1+): OpenAI Whisper API
- `LocalWhisperSTT` (v0.4+): faster-whisper with CTranslate2

### Summarizer (Multiple Providers)

```python
class Summarizer(Protocol):
    def summarize(self, text: str) -> str:
        """Summarize/clean up text."""
        ...
```

**Implementations:**
- `PassthroughSummarizer` (v0.1): Returns text unchanged
- `CloudLLMSummarizer` (v0.2+): OpenAI, OpenRouter, Ollama

### TTS Backend (Cloud + Local, v0.6+)

```python
class TTSBackend(Protocol):
    def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes (MP3 or WAV)."""
        ...
```

**Implementations:**
- `ElevenLabsTTS` (v0.6+): Cloud synthesis via ElevenLabs API (MP3 output)
- `PiperLocalTTS` (v0.7+): Local ONNX inference via Piper + espeak-ng (WAV output)

**Factory function:**
```python
def create_tts_backend(
    api_key: str,
    provider: str = "elevenlabs",
    voice_id: str = "",
    local_voice: str = "",
) -> Optional[TTSBackend]:
    if provider == "piper":
        return PiperLocalTTS(voice_name=local_voice)
    if provider == "elevenlabs":
        return ElevenLabsTTS(api_key=api_key, voice_id=voice_id)
    return None
```

---

## 18. Known Issues & Mitigations

| Issue | Impact | Mitigation |
|-------|--------|-----------|
| Antivirus flags keyboard hooks | Users cannot use tool | Document in README; provide signing/whitelist guidance |
| onnxruntime crashes in .exe (--onefile) | Local STT fails | Auto-disable VAD in frozen builds; users can opt-in |
| Ctrl+Alt+R conflicts with some apps | Hotkey unreliable | Configurable hotkey in config.toml and Settings |
| Clipboard restore timing race | Paste incomplete or clipboard corrupted | Configurable delay (default 150ms); extensive QA |
| OpenAI API rate limits | Degraded experience | Implement retry with exponential backoff (2 retries) |
| Terminal emulators don't respond to Ctrl+V | Paste doesn't work in terminal | Document; user can manually paste or use Ctrl+Shift+V |
| ElevenLabs API key invalid (v0.6+) | TTS fails to synthesize | Specific error message; user checks Settings > Credentials |
| Piper ONNX model not downloaded (v0.7+) | Local TTS unavailable | Prompt user to download model via Settings > Text-to-Speech |
| espeak-ng not installed (v0.7+) | Piper local TTS unavailable | Document installation (bundled in binary); graceful fallback |
| Hugging Face CDN slow/unavailable (v0.7+) | Model downloads slow or fail | Direct HTTPS streaming with retry logic; document alternatives |

---

## 19. Future Enhancements (v1.0 Roadmap)

### Completed in v0.7
- ✅ Local TTS via Piper (offline, free)
- ✅ Direct HTTPS model downloads (replaces buggy hf_hub_download)
- ✅ Tabbed Settings dialog with dark theme

### Planned for v1.0
- Code signing to reduce antivirus false positives
- Multi-language UI localization (German/English primary)
- Bundled language models in single-file .exe (~500MB–1GB)
- Advanced model management (delete models, clear cache)
- Usage statistics and cost tracking
- Custom keybinds for all actions (not just recording)
- Streaming response output (LLM answers appear in real-time)
- Multi-turn conversation context for Voice Prompt mode (maintain conversation history)
- Voice style/emotion control for Piper TTS
- Context retention for Voice Prompt (multi-turn conversation)
