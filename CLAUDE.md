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
sudo apt install wl-clipboard  # Wayland clipboard (wl-copy/wl-paste)
pip install pynput evdev  # not in requirements.txt, Linux-only hotkey libraries
# For Wayland: ensure you are in the 'input' group (for evdev device access)
sudo usermod -aG input $USER  # then logout and login
# For Wayland paste: grant input group write access to /dev/uinput
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-voicepaste-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
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

**Clipboard Operations:**
- **Windows**: ctypes WinAPI (OpenClipboard, GetClipboardData, SetClipboardData)
- **X11**: xclip via subprocess (preferred), falls back to xsel
- **Wayland**: wl-copy/wl-paste (native), falls back to xclip via XWayland

**Paste Simulation (Ctrl+V):**
- **Windows**: ctypes keyboard_event (VK_CONTROL + VK_V)
- **X11**: xdotool key ctrl+v (with --clearmodifiers flag to release stuck modifier keys)
- **Wayland**: evdev UInput (preferred, no external tools, requires /dev/uinput), falls back to ydotool, final fallback to wtype

**Hotkey Registration:**
- **Windows**: `keyboard` library with low-level Windows hooks
- **X11**: `pynput` GlobalHotKeys via XLib
- **Wayland**: `evdev` device monitoring daemon thread (reads /dev/input/event* directly), auto-detects key codes from current keyboard layout

**Audio Feedback:**
- **Windows**: `winsound` module
- **Linux**: `sounddevice` for tone synthesis (works on both X11 and Wayland)

**Single-Instance Lock:**
- **Windows**: Win32 named mutex
- **Linux**: file lock on `/tmp/voicepaste.lock`

**File Paths:**
- **Config**: `~/.config/voicepaste/` (Linux), `%APPDATA%\VoicePaste\` (Windows)
- **Cache**: `~/.cache/voicepaste/` (Linux), `%LOCALAPPDATA%\VoicePaste\` (Windows)
- **Logs**: `./voice-paste.log` (cwd)

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
| `hotkey.py` | Global hotkey dispatcher (keyboard on Windows, pynput on Linux X11, evdev on Linux Wayland) |
| `evdev_hotkey.py` | Linux Wayland hotkey support. Monitors /dev/input/* for keypresses via evdev, spawns daemon listener thread. Auto-detects key codes via keyboard layout. |
| `tray.py` | System tray icon/menu via pystray, state-colored icons |
| `settings_dialog.py` | Tabbed tkinter Settings UI with sv_ttk dark theme |
| `platform_impl/_windows.py` | Windows clipboard (ctypes), Ctrl+V paste simulation (ctypes), audio beeps (winsound), single-instance lock (Win32 mutex) |
| `platform_impl/_linux.py` | Linux clipboard (xclip/wl-copy), Ctrl+V paste simulation (xdotool/evdev UInput/ydotool), audio beeps (sounddevice), file paths (/tmp locks) |
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

### Windows
- PyInstaller spec: `voice_paste.spec`
- Build script: `build.bat` (supports `release`, `debug`, `clean` modes)
- All pip dependency versions pinned with `==` in `requirements.txt` for reproducibility
- onnxruntime has a known issue with PyInstaller `--onefile` — VAD filter auto-disabled in frozen builds. See `src/constants.py` line 138.
- `rthook_onnxruntime.py` is a PyInstaller runtime hook for onnxruntime DLL loading.

### Linux
- PyInstaller spec: `voice_paste_linux.spec`
- Build script: `build_linux.sh` (supports `debug`, `clean` modes; release is default)
- **CRITICAL: Venv must use `--system-site-packages`**. Modern Ubuntu enforces PEP 668, blocking pip installs globally. PyGObject (`gi`) and AppIndicator3 are system packages required by pystray; without them the tray right-click menu doesn't work.
- **Build recipe**:
  ```bash
  python3 -m venv --system-site-packages .venv
  source .venv/bin/activate
  pip install -r requirements.txt pyinstaller pynput evdev
  ./build_linux.sh
  ```
- **Linux-specific build dependencies** (not in `requirements.txt` because they are Linux-only):
  - `pynput`: Required. Handles X11 hotkey registration via GlobalHotKeys. Without it: `ModuleNotFoundError: No module named 'pynput'`.
  - `evdev`: Required for Wayland hotkey support. Without it, X11 still works but Wayland support is unavailable.
  - Build script checks for `pynput` and warns if `evdev` is missing.
- Binary size: ~241 MB (optimized; excludes PyAV 119MB, espeakng_loader 21MB, system package leaks).
- PNG icon and `.desktop` file included for desktop integration.

## Important Conventions

- Audio data is **never written to disk** — kept in memory only (security/privacy requirement).
- API keys are **never logged** — use `config.masked_api_key` for log output.
- The app uses `winsound`/`sounddevice` beeps for audio feedback, not system notification sounds.
- Settings changes hot-reload without restart — `_on_settings_saved()` rebuilds only the affected backends.
- The primary spoken language is German (default transcription language is `"de"`), but the app supports all Whisper-supported languages.
