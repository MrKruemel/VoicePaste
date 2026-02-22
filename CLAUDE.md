# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

VoicePaste is a desktop application (Windows + Linux) that records speech via a global hotkey, transcribes it (cloud OpenAI Whisper or local faster-whisper), optionally summarizes/cleans with an LLM, and pastes the result at the cursor. It also supports text-to-speech readback, a voice prompt Q&A mode, hands-free wake-word activation, and an HTTP API for external control. The app runs entirely in the system tray.

## Common Commands

```bash
# Run from source
python src/main.py
python src/main.py --debug      # allocate console window
python src/main.py --verbose    # force DEBUG log level

# Install dependencies
pip install -r requirements.txt          # production
pip install -r requirements-dev.txt      # includes pytest, coverage

# Run all tests
python -m pytest tests/ -v --tb=short

# Run a single test file or test
python -m pytest tests/test_state_machine.py -v
python -m pytest tests/test_config.py::test_load_default_config -v

# Run tests with coverage
python -m pytest tests/ --cov=src --cov-report=term-missing

# Build (Windows)
build.bat                # release .exe
build.bat debug          # debug .exe with console
build.bat clean          # remove build artifacts

# Build (Linux)
./build_linux.sh         # release binary
./build_linux.sh debug   # debug binary
./build_linux.sh clean   # remove build artifacts
```

### Linux system dependencies for development

```bash
sudo apt install espeak-ng libportaudio2 xclip xdotool python3-tk
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
sudo apt install gnome-shell-extension-appindicator  # GNOME tray icon
pip install pynput evdev  # not in requirements.txt, Linux-only hotkey libraries
# For Wayland: ensure you are in the 'input' group (for evdev device access)
sudo usermod -aG input $USER  # then logout and login
```

## Architecture

### Threading Model

- **Main thread**: pystray event loop (system tray icon + menu)
- **Hotkey thread**: `keyboard` library listener (Windows), `pynput` GlobalHotKeys (Linux X11), or `evdev` monitor daemon (Linux Wayland), daemon thread
- **Pipeline worker**: spawned per recording session — handles STT → summarization → paste sequence
- **Settings dialog**: tkinter on a separate thread, spawned on demand
- **Wake word detector**: continuous background audio monitoring thread (hands-free mode)
- **HTTP API server**: `http.server` on daemon thread (localhost-only)

### State Machine

Defined in `src/constants.py::AppState`. All transitions go through `VoicePasteApp._set_state()` which is thread-safe (lock-guarded) and updates the tray icon.

```
IDLE → RECORDING → PROCESSING → PASTING → IDLE
                              → AWAITING_PASTE → PASTING → IDLE
                              → SPEAKING → IDLE
     RECORDING → IDLE  (Escape cancel)
```

### Platform Abstraction (`src/platform_impl/`)

`platform_impl/__init__.py` detects OS at import time and re-exports from `_windows.py` or `_linux.py`. All platform-specific operations (clipboard, paste simulation, beeps, single-instance lock, file paths) go through this layer. Never import `_windows.py` or `_linux.py` directly.

### Backend Protocols

STT, summarization, and TTS each use a `Protocol` class with a factory function:

| Concern | Protocol | Factory | Implementations |
|---------|----------|---------|-----------------|
| Speech-to-text | `STTBackend` (stt.py) | `create_stt_backend()` | `CloudWhisperSTT`, `LocalWhisperSTT` (local_stt.py) |
| Summarization | — | — | `CloudLLMSummarizer`, `PassthroughSummarizer` (summarizer.py) |
| Text-to-speech | `TTSBackend` (tts.py) | `create_tts_backend()` | `ElevenLabsTTS`, `PiperLocalTTS` (local_tts.py) |

### Configuration

- `config.toml` (user config, gitignored) and `config.example.toml` (template, committed)
- `src/config.py::AppConfig` is a mutable dataclass with `save_to_toml()` for hot-reload
- API keys are stored in the OS credential store (Windows Credential Manager / Linux keyring), never in config files
- All defaults live in `src/constants.py`

### Key Modules

| Module | Purpose |
|--------|---------|
| `main.py` | Entry point, `VoicePasteApp` orchestrator class, pipeline logic |
| `audio.py` | Microphone recording via sounddevice (in-memory WAV buffer) |
| `hotkey.py` | Global hotkey registration (keyboard on Windows, pynput on Linux X11, evdev on Linux Wayland) |
| `evdev_hotkey.py` | Linux Wayland global hotkey support via evdev device monitoring |
| `tray.py` | System tray icon/menu via pystray, state-colored icons |
| `settings_dialog.py` | Tabbed tkinter Settings UI with sv_ttk dark theme |
| `paste.py` | Windows clipboard + Ctrl+V simulation (ctypes) |
| `notifications.py` | Audio cue functions (beep patterns for start/stop/cancel/error) |
| `api_server.py` | Localhost HTTP API for external control |
| `wake_word.py` | Continuous wake-phrase detection for hands-free mode |
| `tts_cache.py` | LRU audio cache with size/age/count eviction |
| `tts_export.py` | Save synthesized audio to timestamped files |
| `model_manager.py` | Whisper model download/lifecycle |
| `tts_model_manager.py` | Piper voice model download/lifecycle |
| `integrity.py` | File hash verification for model downloads |

## Testing Notes

- Tests are in `tests/` and use pytest with `pytest-mock` and `pytest-timeout`.
- `conftest.py` adds `src/` to `sys.path` and skips Windows-only test files (`test_paste.py`, `test_clipboard.py`, `test_single_instance.py`) on Linux.
- Tests mock all external APIs (OpenAI, ElevenLabs) and hardware (microphone, clipboard). No real API calls or audio recording in tests.
- CI runs on Windows, Ubuntu 22.04, and Ubuntu 24.04 (see `.github/workflows/ci.yml`).

## Build Notes

- PyInstaller builds use `.spec` files: `voice_paste.spec` (Windows), `voice_paste_linux.spec` (Linux).
- All pip dependency versions are pinned with `==` in `requirements.txt` for reproducible PyInstaller builds. Do not loosen to `>=` without re-testing the built binary.
- onnxruntime has a known issue with PyInstaller `--onefile` bundles — VAD filter is auto-disabled in frozen builds (see `constants.py`).
- `rthook_onnxruntime.py` is a PyInstaller runtime hook for onnxruntime DLL loading.
- **Linux builds require `pynput` and `evdev`** in the build environment. `pynput` handles X11 hotkeys; `evdev` handles Wayland hotkeys. Both are NOT listed in `requirements.txt` (because they are Linux-only and the `keyboard` library is used on Windows). If pynput is missing, the binary fails with `"No module named 'pynput'"` / `"Could not register the hotkey"`. If evdev is missing, Wayland support is unavailable but X11 still works. The build script (`build_linux.sh`) now checks for pynput and warns if evdev is missing.
- **Use a venv for building** — system Python on modern Ubuntu (PEP 668) blocks global pip installs. The venv **must** use `--system-site-packages` so that PyGObject (`gi`) and AppIndicator3 are available — these are system packages that cannot be installed via pip. Without them, pystray falls back to the `_xorg` backend and the tray right-click menu does not work. Build recipe: `python3 -m venv --system-site-packages .venv && source .venv/bin/activate && pip install -r requirements.txt pyinstaller pynput evdev`.

## Important Conventions

- Audio data is **never written to disk** — kept in memory only (security/privacy requirement).
- API keys are **never logged** — use `config.masked_api_key` for log output.
- The app uses `winsound`/`sounddevice` beeps for audio feedback, not system notification sounds.
- Settings changes hot-reload without restart — `_on_settings_saved()` rebuilds only the affected backends.
- The primary spoken language is German (default transcription language is `"de"`), but the app supports all Whisper-supported languages.
