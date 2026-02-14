# Architecture Decision Record (ADR)

## Voice Paste Tool

**Date**: 2026-02-14
**Status**: Accepted
**Author**: Solution Architect
**Current Version**: 0.5.0

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

### Component Diagram (v0.5)

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
|  +------------------+                                              |
|  |  Settings Dialog |  (v0.3+, tkinter, dedicated thread)         |
|  |  (Credentials,   |   - Manage API keys via keyring             |
|  |   Transcription, |   - Configure transcription/summarization   |
|  |   Summarization, |   - Hot-reload without restart              |
|  |   Feedback)      |                                              |
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

### State Machine (v0.5)

```
                    +-------+
                    | IDLE  |<-----------------------+
                    +---+---+                        |
                        |                            |
                   Hotkey press                      |
                   (Ctrl+Alt+R or A)                |
                        |                            |
                    +---v-------+                    |
                    | RECORDING |----Escape---->(CANCELLED)
                    +---+-------+                    |
                        |                            |
                   Hotkey press                      |
                        |                            |
                    +---v--------+                   |
                    | PROCESSING |                   |
                    +---+--------+                   |
                        |                            |
                   STT + optional                    |
                   Summarization or                  |
                   Prompt complete                   |
                        |                            |
                    +---v-----+                      |
                    | PASTING  |-----done------------+
                    +---------+
```

**States:**
- **IDLE**: Waiting for hotkey. Tray icon is grey.
- **RECORDING**: Capturing audio from microphone. Tray icon is red.
- **PROCESSING**: Audio sent to STT, transcript sent to summarizer or prompt handler. Tray icon is yellow.
- **PASTING**: Text placed on clipboard and Ctrl+V simulated. Tray icon is green. Returns to IDLE immediately.
- **CANCELLED**: Recording discarded (via Escape). Returns to IDLE with notification.

**Error handling**: If any state encounters an error (API failure, mic error, model load failure), log it, show toast notification, and return to IDLE.

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

## 6. Decision: Global Hotkey Library

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

## 7. Decision: Audio Capture Library

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

## 8. Decision: Clipboard and Paste Strategy

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

## 9. Decision: System Tray Library

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

## 10. Decision: Settings Dialog (v0.3+)

### New Feature: Configuration via GUI

**Implementation:**
- tkinter-based dialog on dedicated thread
- Does NOT block pystray main thread
- Singleton guard (only one dialog at a time)
- Hot-reload: Changes apply immediately without restart

**Tabs:**
1. **Credentials**: Manage OpenAI and OpenRouter API keys via keyring
2. **Transcription**: Cloud/Local backend, model size, device, VAD filter
3. **Summarization**: Enable/disable, provider, model, custom prompt
4. **Feedback**: Audio cues, log level

**Threading model:**
- Main thread: pystray event loop
- Settings thread: tkinter Tcl event loop (spawned on demand)
- Lock: `_settings_lock` prevents concurrent dialogs

---

## 11. Decision: Credential Storage (v0.3+)

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

## 12. Decision: Configuration Format and Hot-Reload

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

## 13. Decision: Threading Model

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

## 14. Decision: Python Version and Key Dependencies

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

## 15. Project File Structure (v0.5)

```
C:\develop\speachtoText\
|-- src\
|   |-- main.py                      # Entry point, state machine
|   |-- audio.py                     # Audio recording
|   |-- stt.py                       # Cloud STT (OpenAI Whisper)
|   |-- local_stt.py                 # Local STT (faster-whisper, v0.4+)
|   |-- summarizer.py                # Summarizer (OpenAI, OpenRouter, Ollama)
|   |-- paste.py                     # Clipboard + paste
|   |-- config.py                    # Config loading, AppConfig dataclass
|   |-- hotkey.py                    # Hotkey registration (keyboard lib)
|   |-- tray.py                      # System tray + context menu
|   |-- constants.py                 # Shared constants, AppState enum
|   |-- notifications.py             # Audio cues + toast notifications
|   |-- settings_dialog.py           # Settings GUI (tkinter, v0.3+)
|   |-- keyring_store.py             # Keyring integration (v0.3+)
|   |-- model_manager.py             # Model download/caching (v0.4+)
|   |-- icon_drawing.py              # Tray icon generation (v0.5+)
|-- docs\
|   |-- ADR.md                       # This file
|   |-- UX-SPEC.md                   # UX specification
|   |-- THREAT-MODEL.md              # Security threat model
|   |-- PROMPTS.md                   # Prompt templates (summarization + voice prompt)
|   |-- BACKLOG.md                   # Product backlog
|-- tests\
|   |-- test_config.py
|   |-- test_state_machine.py
|   |-- test_audio.py
|   |-- test_paste.py
|   |-- (13+ test files total)
|-- config.example.toml              # Configuration template
|-- build.bat                        # PyInstaller build script
|-- voice_paste.spec                 # PyInstaller spec file
|-- rthook_onnxruntime.py            # PyInstaller runtime hook (v0.4+)
|-- requirements.txt                 # Python dependencies
|-- requirements-dev.txt             # Development dependencies
|-- README.md                        # User documentation
|-- CHANGELOG.md                     # Release notes
```

**Note**: PyInstaller bundles everything into a single .exe. Multi-file structure is for development clarity.

---

## 16. Backend Abstraction Pattern

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

---

## 17. Known Issues & Mitigations

| Issue | Impact | Mitigation |
|-------|--------|-----------|
| Antivirus flags keyboard hooks | Users cannot use tool | Document in README; provide signing/whitelist guidance |
| onnxruntime crashes in .exe (--onefile) | Local STT fails | Auto-disable VAD in frozen builds; users can opt-in |
| Ctrl+Alt+R conflicts with some apps | Hotkey unreliable | Configurable hotkey in config.toml and Settings |
| Clipboard restore timing race | Paste incomplete or clipboard corrupted | Configurable delay (default 150ms); extensive QA |
| OpenAI API rate limits | Degraded experience | Implement retry with exponential backoff (2 retries) |
| Terminal emulators don't respond to Ctrl+V | Paste doesn't work in terminal | Document; user can manually paste or use Ctrl+Shift+V |

---

## 18. Future Enhancements (v1.0 Roadmap)

- Code signing to reduce antivirus false positives
- Multi-language UI (localization)
- Bundled language models in single-file .exe
- Advanced model management (delete models, clear cache)
- Usage statistics and cost tracking
- Dark/light theme toggle
- Custom keybinds for all actions
- Streaming response output (LLM answers appear in real-time)
- Context retention for Voice Prompt (multi-turn conversation)
