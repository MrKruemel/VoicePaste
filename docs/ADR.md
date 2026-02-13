# Architecture Decision Record (ADR)

## Voice-to-Summary Paste Tool

**Date**: 2026-02-13
**Status**: Accepted
**Author**: Solution Architect

---

## 1. Context and Problem Statement

We are building a Windows desktop tool that:
1. Captures a global hotkey (Ctrl+Win) to start/stop recording
2. Records microphone audio to an in-memory buffer
3. Transcribes speech to text
4. Optionally summarizes the transcript (v0.2+)
5. Pastes the result at the current cursor position

The tool must ship as a **single-file .exe** via PyInstaller, support **German** as the primary language, and never steal focus or disrupt the user's workflow.

---

## 2. Architecture Overview

### Component Diagram

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
|              | STT Backend |  <-- Protocol/ABC                     |
|              +-----+------+                                        |
|                    |                                                |
|         +----------+----------+                                    |
|         |                     |                                     |
|  +------v-------+   +--------v--------+                            |
|  | Cloud Whisper |   | Local Whisper   |  (v1.0)                   |
|  | (OpenAI API)  |   | (faster-whisper)|                           |
|  +--------------+   +-----------------+                            |
|                                                                    |
|              +----------------+                                    |
|              | Summarizer     |  <-- Protocol/ABC (v0.2+)          |
|              +-------+--------+                                    |
|                      |                                             |
|           +----------+----------+                                  |
|           |                     |                                   |
|  +--------v-------+   +--------v--------+                          |
|  | Cloud LLM      |   | Passthrough     |                         |
|  | (OpenAI API)   |   | (no-op, v0.1)   |                         |
|  +----------------+   +-----------------+                          |
|                                                                    |
|  +------------------+                                              |
|  |  System Tray     |                                              |
|  |  (pystray)       |                                              |
|  +------------------+                                              |
+--------------------------------------------------------------------+
```

### State Machine

```
                    +-------+
                    | IDLE  |<-----------------------+
                    +---+---+                        |
                        |                            |
                   Ctrl+Win                          |
                        |                            |
                    +---v-------+                    |
                    | RECORDING |----Escape---->(CANCELLED)
                    +---+-------+                    |
                        |                            |
                   Ctrl+Win                          |
                        |                            |
                    +---v--------+                   |
                    | PROCESSING |                   |
                    +---+--------+                   |
                        |                            |
                   STT complete                      |
                        |                            |
                    +---v-----+                      |
                    | PASTING  |-----done------------+
                    +---------+
```

**States:**
- **IDLE**: Waiting for hotkey. Tray icon is default.
- **RECORDING**: Capturing audio from microphone. Tray icon is red (v0.2+).
- **PROCESSING**: Audio sent to STT (and summarizer in v0.2+). Tray icon is yellow (v0.2+).
- **PASTING**: Text placed on clipboard and Ctrl+V simulated. Transitions to IDLE immediately.
- **CANCELLED**: Recording discarded (v0.2+, via Escape). Returns to IDLE.

**Error handling**: If any state encounters an error (API failure, mic error), log it, show notification (v0.2+), and return to IDLE.

---

## 3. Decision: Speech-to-Text Backend

### Options Evaluated

| Option | Quality | Binary Size | Latency | Offline | Complexity |
|--------|---------|-------------|---------|---------|------------|
| **OpenAI Whisper API** | Excellent | +0 MB | 2-5s (cloud) | No | Low |
| faster-whisper (tiny) | Good | +75 MB | 3-8s (local) | Yes | Medium |
| faster-whisper (base) | Very Good | +150 MB | 5-15s (local) | Yes | Medium |
| Deepgram API | Excellent | +0 MB | 1-3s (cloud) | No | Low |

### Decision: OpenAI Whisper API (cloud) for v0.1 and v0.2. Local faster-whisper as v1.0 option.

**Rationale:**
1. **Binary size**: Cloud API adds zero bytes to the .exe. Local whisper adds 75-150 MB minimum.
2. **Quality**: Whisper API has excellent German transcription quality out of the box.
3. **Simplicity**: Single HTTP POST with an audio file. No model loading, no GPU detection, no CTranslate2 DLL bundling.
4. **User already needs API key**: For summarization (v0.2+), the user needs an OpenAI key anyway. No additional setup burden.
5. **Backend abstraction**: We define an `STTBackend` Protocol so swapping to local is a clean implementation change, not a refactor.

**Tradeoffs accepted:**
- Requires internet connection (acceptable for v0.1/v0.2; local option addresses this in v1.0)
- Requires OpenAI API key and incurs per-use cost (~$0.006 per minute of audio)
- Audio leaves the user's machine (security implication documented in threat model)

---

## 4. Decision: Summarization Backend

### Options Evaluated

| Option | Quality (German) | Cost | Latency | Binary Impact |
|--------|-------------------|------|---------|---------------|
| **OpenAI GPT-4o-mini** | Very Good | ~$0.0001/call | 1-2s | +0 MB |
| OpenAI GPT-4o | Excellent | ~$0.005/call | 2-4s | +0 MB |
| Claude 3.5 Haiku | Very Good | ~$0.0003/call | 1-2s | +0 MB |
| Local (llama-cpp) | Moderate | Free | 5-30s | +2-4 GB |
| No summarization | N/A | Free | 0s | +0 MB |

### Decision: OpenAI GPT-4o-mini for v0.2+. No summarization in v0.1 (passthrough).

**Rationale:**
1. **Quality/cost ratio**: GPT-4o-mini provides excellent German summarization at negligible cost.
2. **Same API key**: Uses the same OpenAI key as Whisper API. No additional credential management.
3. **Speed**: 1-2 second response time keeps total pipeline under 5 seconds.
4. **Binary size**: Zero impact. Pure HTTP call.
5. **v0.1 simplicity**: v0.1 uses a passthrough (no-op) summarizer that returns raw transcript. This validates the pipeline without the LLM dependency.

**Tradeoffs accepted:**
- Cloud dependency for summarization (acceptable; local summarization is v1.x backlog)
- Transcript content sent to OpenAI (documented in threat model; user must consent)

---

## 5. Decision: Global Hotkey Library

### Options Evaluated

| Option | Global Hotkey | Reliability | PyInstaller | Windows Focus |
|--------|---------------|-------------|-------------|---------------|
| **keyboard** | Yes | High | Good | Does not steal |
| pynput | Yes | Medium | Medium | May steal on some configs |
| win32api (ctypes) | Yes | High | Excellent | Does not steal |
| system_hotkey | Yes | Low | Unknown | Unknown |

### Decision: `keyboard` library

**Rationale:**
1. **Proven reliability** for global hotkeys on Windows without admin rights.
2. **Simple API**: `keyboard.add_hotkey('ctrl+win', callback)` -- one line.
3. **PyInstaller compatible**: No known bundling issues.
4. **Does not steal focus**: Hooks at the OS level without affecting window focus.
5. **Also handles Escape**: Can register Escape to cancel recording (v0.2).

**Tradeoffs accepted:**
- The `keyboard` library uses low-level Windows hooks, which some antivirus software may flag. This is documented in the README as a known issue.
- Requires the `keyboard` library as a dependency (pure Python, small).

**Alternative considered**: Raw Win32 API via `ctypes` would eliminate the dependency but adds significant implementation complexity for no user-facing benefit in v0.1.

---

## 6. Decision: Audio Capture Library

### Options Evaluated

| Option | API Quality | PyInstaller | Latency | Dependencies |
|--------|-------------|-------------|---------|--------------|
| **sounddevice** | Clean | Good | Low | PortAudio (bundled) |
| pyaudio | Functional | Complex | Low | PortAudio (manual) |
| wave + winsound | Limited | Excellent | N/A | None (record not supported) |

### Decision: `sounddevice`

**Rationale:**
1. **Clean API**: `sd.rec()` / stream-based recording with NumPy arrays.
2. **Bundles PortAudio**: No manual DLL management unlike pyaudio.
3. **PyInstaller friendly**: Works with `--onefile` with known hidden imports.
4. **Low latency**: Suitable for real-time recording start/stop.

**Known PyInstaller requirement**: Must include `_sounddevice_data` as hidden import or data file. The Build Engineer must handle this in the .spec file.

---

## 7. Decision: Clipboard and Paste Strategy

### Strategy: Backup -> Write -> Simulate Ctrl+V -> Restore

**Implementation:**
1. Read current clipboard contents (backup)
2. Write transcript/summary text to clipboard
3. Simulate Ctrl+V keystroke via `keyboard` library
4. Wait brief delay (100-200ms) for paste to complete
5. Restore original clipboard contents

**Library**: `win32clipboard` (from `pywin32`) for clipboard read/write. This gives full control over clipboard formats and is more reliable than `pyperclip` for backup/restore.

**v0.1 simplification**: Skip backup/restore. Just write to clipboard and paste. Clipboard preservation is a v0.2 feature (US-0.2.5).

**Tradeoffs:**
- The paste delay (step 4) is a heuristic. Too short and the paste may not complete before clipboard is restored. Too long and the user notices a lag. Default: 150ms, configurable.
- Some applications (e.g., terminals with custom paste handling) may not respond to simulated Ctrl+V. This is documented as a known limitation.

---

## 8. Decision: System Tray Library

### Decision: `pystray` with `Pillow` for icon generation

**Rationale:**
1. **Cross-platform** (Windows focus, but no Windows-only lock-in).
2. **Clean API**: Create icon, define menu, run. Integrates with threading.
3. **Dynamic icons**: Using Pillow, we generate simple colored circle icons for state changes (v0.2+).
4. **PyInstaller compatible**: No known bundling issues.

**v0.1**: Single static icon. Right-click menu with "Quit".
**v0.2**: Dynamic icon colors (grey=idle, red=recording, yellow=processing).

---

## 9. Decision: Configuration Format

### Decision: TOML (`config.toml`)

**Rationale:**
1. **Human-readable** and easy to edit in any text editor.
2. **Python stdlib**: `tomllib` available in Python 3.11+ (no dependency).
3. **Flat structure**: Our config is simple enough for flat TOML.
4. **Convention**: `.toml` is standard for Python project configuration.

**Config structure (v0.1):**
```toml
[api]
openai_api_key = ""

[logging]
level = "INFO"  # DEBUG, INFO, WARNING, ERROR
```

**Config structure (v0.2+):**
```toml
[api]
openai_api_key = ""

[recording]
sample_rate = 16000

[summarization]
enabled = true
prompt = "default"  # or custom prompt text

[feedback]
audio_cues = true

[logging]
level = "INFO"
```

---

## 10. Decision: Threading Model

### Decision: Main thread for pystray, worker thread for pipeline

**Architecture:**
- `pystray` requires running on the main thread (Windows message loop).
- Hotkey listener runs in a daemon thread via the `keyboard` library.
- Recording, transcription, and pasting run in a worker thread to avoid blocking the tray.
- State machine is the central coordinator, accessed with a threading lock.

```
Main Thread:     pystray event loop (system tray)
Thread 1:        keyboard hotkey listener
Thread 2:        Recording + STT + Paste pipeline (spawned per session)
```

**Synchronization**: A simple `threading.Lock` protects the `AppState` enum. State transitions are atomic.

---

## 11. Decision: Python Version and Key Dependencies

| Dependency | Version | Purpose | Size Impact |
|------------|---------|---------|-------------|
| Python | 3.11+ | Runtime (tomllib in stdlib) | Base |
| sounddevice | latest | Audio capture | ~5 MB (with PortAudio) |
| numpy | latest | Audio buffer handling | ~30 MB |
| keyboard | latest | Global hotkey | ~0.5 MB |
| pystray | latest | System tray | ~0.5 MB |
| Pillow | latest | Tray icon generation | ~5 MB |
| openai | latest | Whisper API + GPT API | ~2 MB |
| pywin32 | latest | Clipboard (v0.2+) | ~10 MB |

**Estimated .exe size (cloud-only)**: 40-60 MB (acceptable).
**With local whisper (v1.0)**: 120-200 MB (acceptable for optional feature).

---

## 12. Project File Structure

```
C:\develop\speachtoText\
|-- src\
|   |-- main.py              # Entry point, state machine, tray setup
|   |-- audio.py             # Audio recording module
|   |-- stt.py               # STT backend protocol + implementations
|   |-- summarizer.py        # Summarizer protocol + implementations
|   |-- paste.py             # Clipboard + paste logic
|   |-- config.py            # Config loading and validation
|   |-- hotkey.py            # Hotkey registration
|   |-- tray.py              # System tray setup and icon management
|   |-- constants.py         # Shared constants, enums (AppState)
|   |-- notifications.py    # Audio cues and toast notification interface (winsound)
|-- docs\
|   |-- BACKLOG.md           # Product backlog (this file)
|   |-- ADR.md               # Architecture decisions (this file)
|   |-- UX-SPEC.md           # UX specification
|   |-- THREAT-MODEL.md      # Security threat model
|-- tests\
|   |-- test_config.py
|   |-- test_state_machine.py
|   |-- test_audio.py
|   |-- test_paste.py
|-- config.example.toml      # Template config file
|-- build.bat                # PyInstaller build script
|-- voice_paste.spec         # PyInstaller spec file
|-- requirements.txt         # Python dependencies
|-- README.md
|-- CHANGELOG.md
```

**Note**: Despite the multi-file source structure, PyInstaller bundles everything into a single .exe. The multi-file structure is for development clarity only.

---

## 13. Backend Abstraction Pattern

Both STT and Summarizer use the Python Protocol pattern for clean swapping:

```python
from typing import Protocol

class STTBackend(Protocol):
    def transcribe(self, audio_data: bytes, language: str = "de") -> str:
        """Transcribe audio bytes to text."""
        ...

class Summarizer(Protocol):
    def summarize(self, text: str, language: str = "de") -> str:
        """Summarize/clean up transcribed text."""
        ...
```

**v0.1 implementations:**
- `CloudWhisperSTT` -- calls OpenAI Whisper API
- `PassthroughSummarizer` -- returns text unchanged

**v0.2 implementations:**
- `CloudLLMSummarizer` -- calls OpenAI GPT-4o-mini

**v1.0 implementations:**
- `LocalWhisperSTT` -- uses faster-whisper locally

---

## 14. Open Questions / Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Antivirus flags keyboard hooks | Users cannot use tool | Document in README, provide signing guidance |
| PyInstaller + sounddevice bundling issues | Build fails | Build Engineer tests early in Phase 2 |
| Ctrl+Win conflicts with Windows shortcuts | Hotkey unreliable | Test on Win10/11; provide configurable hotkey in v1.0 |
| Clipboard restore timing | Paste incomplete or clipboard corrupted | Configurable delay; extensive QA testing |
| OpenAI API rate limits | Degraded experience | Implement retry with backoff; document in error handling |
