# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Code signing for reduced antivirus false positives
- Multi-language UI (localization)
- Bundled language models in single-file .exe
- Advanced model management (delete models, clear cache)
- Usage statistics and cost tracking
- Custom keybinds for all actions (not just recording/prompt)
- Streaming response output (paste as LLM answers)
- Multi-turn conversation context for Voice Prompt mode

See [BACKLOG.md](docs/BACKLOG.md) for the complete v1.0 roadmap.

---

## [1.1.0] - 2026-02-20

### Added
- **Linux Platform Support**: Full cross-platform support for Ubuntu 22.04 and 24.04 alongside Windows. Platform abstraction layer (`src/platform_impl/`) handles clipboard, paste simulation, audio feedback, file paths, and single-instance locking per OS.
- **Wayland & X11 Hotkey Support**: Auto-detects session type. X11 uses pynput GlobalHotKeys. Wayland uses evdev device monitoring daemon thread with auto-detected key codes from keyboard layout. No external hotkey daemons required.
- **Wayland Paste Simulation**: evdev UInput for keystroke injection (preferred, no external tools, requires /dev/uinput writable), fallback to ydotool or wtype. Configurable paste delay via `[paste] delay_seconds`.
- **Wayland Clipboard**: wl-copy/wl-paste (native, requires wl-clipboard package), fallback to xclip via XWayland.
- **Terminal Paste Detection (Linux)**: Automatically detects 20+ terminal emulators (GNOME Terminal, Konsole, Alacritty, kitty, xterm, etc.) via X11 WM_CLASS and uses Ctrl+Shift+V instead of Ctrl+V for correct paste behavior.
- **CUDA Safe Auto-Detection**: New `_resolve_device()` in local_stt.py pre-checks for NVIDIA GPU + cuDNN before allowing CUDA. Prevents CTranslate2 segfaults when CUDA libraries are partially installed. Frozen PyInstaller binaries always use CPU.
- **TTS Speech Speed Setting**: New `[tts] speed` config field (default: 1.0, range: 0.25–4.0). Maps to Piper's `length_scale` parameter. Configurable via Settings dialog spinbox (0.5–2.0 UI range).
- **pystray GtkIcon Patch**: Monkey-patches pystray's `_update_fs_icon` to add `.png` suffix on Linux, fixing "Failed to recognize image format" errors with GNOME Shell's AppIndicator extension.
- **Linux CI Matrix**: GitHub Actions CI now runs on windows-latest, ubuntu-22.04, and ubuntu-24.04 with platform-specific system dependencies.

### Changed
- **Default TTS Hotkey (Linux)**: Changed from Ctrl+Alt+T to Ctrl+Alt+S on Linux to avoid conflict with GNOME Terminal's default shortcut. Windows remains Ctrl+Alt+T.
- **xclip Clipboard Delay**: Increased from 50ms to 150ms on Linux to accommodate X11's asynchronous clipboard (xclip forks a background process to serve the selection).
- **xdotool --clearmodifiers**: `send_key()` now passes `--clearmodifiers` to xdotool to release held modifier keys before sending keystrokes, preventing stuck-modifier issues after hotkey combos.
- **Settings Dialog (Linux)**: "Open Cache Folder" button uses `xdg-open` instead of `os.startfile` on Linux. TTS speed spinbox added to Text-to-Speech tab.
- **Build Dependencies**: `build_linux.sh` and CI workflow updated to include `python3-gi` and `gir1.2-ayatanaappindicator3-0.1` for pystray AppIndicator support.
- **APP_VERSION**: Bumped to 1.1.0.

### Fixed
- **Terminal paste failures on Linux**: Terminals that only accept Ctrl+Shift+V now receive the correct keystroke automatically.
- **Tray icon not appearing on GNOME**: pystray wrote temp icon files without `.png` extension; GNOME's GdkPixbuf couldn't determine the image format.
- **CUDA segfaults in frozen builds**: PyInstaller binaries no longer attempt CUDA initialization, which caused native crashes when CUDA runtime libraries weren't bundled.
- **Stuck modifier keys after hotkey**: xdotool `--clearmodifiers` flag prevents modifier keys from remaining held after a hotkey-triggered action.

### Security
- test_security.py: Added `tray.py` to tempfile allowlist (uses tempfile for pystray icon PNG files, not audio data). Both Windows and Linux builds verified secure.

---

## [0.9.0] - 2026-02-20

### Added
- **Confirm-Before-Paste (v0.9)**: New AWAITING_PASTE state. After processing, a brief delay or Enter keypress is required before pasting. Press Escape to cancel. Prevents accidental pasting into wrong window.
- **Delayed Paste with Auto-Enter (v0.9)**: Configurable paste delay (0-10s) and optional auto-Enter after paste. Settings: `[paste] confirm_before_paste`, `paste_delay_seconds`, `auto_enter_after_paste`.
- **HTTP API Server (v0.9)**: Localhost-only REST API for external program control. Endpoints: GET /health, GET /status, POST /tts, POST /stop, POST /record/start, POST /record/stop, POST /cancel. Binds to 127.0.0.1 only (hardcoded). Rate-limited to 5 req/s. CORS restricted to http://localhost origins.
- **API Server Module (api_server.py, v0.9)**: Threaded HTTP server using Python stdlib http.server. Each request handled in a daemon thread. Strict CORS origin validation via regex.
- **Hands-Free Mode (v0.9)**: Wake word detection using faster-whisper tiny model as a keyword spotter. Say the configured wake phrase (default "Hello Cloud") to start recording. Recording auto-stops when silence is detected. Fully configurable: wake phrase, match mode, pipeline, silence timeout, max duration.
- **Wake Word Detector (wake_word.py, v0.9)**: Energy-based VAD (RMS on 100ms frames) triggers short STT bursts only during speech. ~0% CPU when idle. Manages its own tiny model instance. Privacy-safe: all processing local, no audio logged or written to disk, buffers zeroed after use.
- **Silence-Based Auto-Stop (v0.9)**: AudioRecorder now supports optional silence detection. After speech is detected, recording auto-stops when silence exceeds configurable timeout (default 3.0s). Used by Hands-Free mode for natural conversation flow.
- **Hands-Free Settings Tab (v0.9)**: New tab in Settings dialog with: enable checkbox + privacy warning, wake phrase text field, match mode dropdown (contains/startswith/fuzzy), pipeline selector (Ask+TTS/Transcribe+Paste/Ask+Paste), silence timeout spinner (1-10s), max recording spinner (10-300s).
- **Tray Menu Hands-Free Toggle (v0.9)**: "Hands-Free: ON/OFF" toggle in system tray context menu for quick enable/disable without opening Settings.
- **Wake Word Confirmation Cue (v0.9)**: Rising triple chirp (660-880-1100 Hz) plays when wake phrase is detected, confirming recording is about to start.
- **API Skill Reference (docs/API-SKILL-REFERENCE.md, v0.9)**: Documentation for AI agents and scripts to interact with Voice Paste via the HTTP API.

### Changed
- **APP_VERSION**: Bumped to 0.9.0.
- **State Machine (v0.9)**: New AWAITING_PASTE state with teal icon color. Transitions: PROCESSING -> AWAITING_PASTE -> PASTING (Enter) or IDLE (Escape/timeout).
- **AudioRecorder (v0.9)**: Added `on_silence_stop`, `silence_timeout_seconds`, `silence_threshold_rms`, `max_duration_override` parameters. Silence callback dispatched off PortAudio thread for thread safety.
- **Threading Model (v0.9)**: Wake word detector runs on its own daemon thread. Pauses audio processing when app is not IDLE (avoids concurrent audio stream conflicts).

### Fixed
- **Wake Word False Positives (v0.9)**: Removed initial_prompt parameter that caused tiny model to hallucinate wake phrase on any input. Tuned thresholds (no_speech=0.75, log_prob=-1.5, energy=300 RMS, min_speech=0.8s).
- **CORS Origin Validation (SEC-050, v0.9)**: Fixed `startswith("http://localhost")` matching `http://localhost.evil.com`. Now uses strict regex `^http://localhost(:\d+)?$`.
- **Privacy: Wake Word Transcripts (SEC-048, v0.9)**: Transcripts from ambient speech are no longer logged. Only character count is recorded for debugging.
- **Audio Buffer Scrubbing (SEC-053, v0.9)**: Wake word audio buffers are explicitly zeroed before clearing (REQ-S10 compliance).
- **PortAudio Thread Safety (v0.9)**: Silence auto-stop callback dispatched to a separate thread to avoid blocking PortAudio callback.
- **Hot-Reload State Guard (v0.9)**: Hands-Free settings hot-reload only triggers when app is IDLE, preventing state conflicts during recording/processing.
- **Timing-Safe Hash Comparison (v0.9)**: Model integrity verification uses `hmac.compare_digest()` for constant-time comparison.

### Security
- SEC-048: Stop logging wake word transcripts (ambient speech privacy).
- SEC-050: Strict CORS origin regex for API server.
- SEC-052: Downgrade wake phrase logging from INFO to DEBUG.
- SEC-053: Zero wake word audio buffers before clearing.
- SEC-054: Stop echoing request paths in API error responses.

---

## [0.8.0] - 2026-02-19

### Added
- **Floating Overlay UI (v0.8)**: Non-intrusive status display in bottom-right corner of screen. Shows state-specific visual feedback: RECORDING with live timer (MM:SS), PROCESSING with animated dots, SPEAKING with pulsing blue dot, PASTING with confirmation message. Overlay never steals focus (WS_EX_NOACTIVATE, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW). 200x56px, dark background (#1c1c1c), 86% opacity, 20px from edges.
- **OverlayWindow Module (overlay.py, v0.8)**: Dedicated T4 thread for overlay management. Runs daemon thread from startup to shutdown. Hot-reload support: can toggle overlay on/off in Settings without restart.
- **Overlay Configuration (v0.8)**: New config field `[feedback] show_overlay = true` (default: enabled). Checkbox in Settings > General tab.
- **Expanded Piper Voice List (v0.8)**: From 5 German voices to 14 voices across 3 language variants:
  - German (4): Thorsten medium (recommended), Thorsten high, Thorsten Emotional medium, MLS medium
  - English US (5): Ryan high (NEW), Ryan medium (NEW), Lessac high (NEW), Lessac medium, Amy medium
  - English GB (5): Cori high (NEW, female), Cori medium (NEW), Alba medium (NEW, female), Jenny medium (NEW, female), Alan medium (NEW, male)
- **SHA256 Integrity Verification (v0.8)**: New module `src/integrity.py` with `compute_file_sha256()`, `verify_file_sha256()`, `verify_directory_files()` functions. Integrated into TTS model downloads (tts_model_manager.py) and STT model downloads (model_manager.py). Addresses security findings SEC-040 (TTS) and SEC-027 (STT).
- **Hash Dictionary Stubs (constants.py, v0.8)**: `PIPER_VOICE_MODELS` and `STT_MODEL_SHA256` include empty `sha256` dicts for graceful degradation. Logs computed hashes for collection.
- **Startup Notification Enhancement (v0.8)**: When TTS is enabled, balloon notification includes Ctrl+Alt+T and Ctrl+Alt+Y hotkey hints.
- **Security Fix SEC-039 (v0.8)**: espeakng-loader pinned to ==0.2.4 (from >=0.2.4).
- **Comprehensive Tests (v0.8)**: 132 new tests: 108 tests for overlay functionality (test_overlay.py), 24 tests for integrity verification (test_integrity.py). Total test count: 580 tests, all passing.
- **ADR Documentation (docs/ADR-v08-floating-overlay.md, v0.8)**: Comprehensive design document for floating overlay feature, threading model, and state management.

### Changed
- **APP_VERSION**: Bumped to 0.8.0
- **Binary Size**: ~280 MB (includes overlay and expanded voice models in frozen .exe)
- **Threading Model (v0.8)**: T4 thread (daemon) added for overlay window management. Lifecycle: startup → overlay loop → shutdown.
- **Startup Notification Logic (v0.8)**: Conditional TTS hotkey display based on `[tts] enabled` setting.

### Fixed
- **Overlay Thread Safety (v0.8)**: State transitions synchronized via thread-safe queue communication. No race conditions between main state machine (T1) and overlay UI thread (T4).

---

## [0.7.0] - 2026-02-19

### Added
- **Local TTS via Piper ONNX (v0.7)**: Offline text-to-speech synthesis without API keys or internet. Includes espeak-ng phonemization via ctypes for natural prosody.
- **5 German Piper Voices (v0.7)**: de_DE-thorsten-medium (recommended), de_DE-thorsten-high, de_DE-thorsten_emotional-medium. English voices also available (en_US-lessac-medium, en_US-amy-medium).
- **Direct HTTPS Model Streaming (v0.7)**: Replaced `hf_hub_download` with direct HTTPS streaming from Hugging Face CDN. Fixes AttributeError with Xet Storage repos and stale .lock file infinite retry loops.
- **TTS Model Manager (tts_model_manager.py, v0.7)**: Manages Piper voice model downloads and caching to `%LOCALAPPDATA%\VoicePaste\models\tts\`. Download UI in Settings dialog with progress bar.
- **Tabbed Settings Dialog (v0.7)**: Redesigned from vertical sections to ttk.Notebook with 4 tabs: Transcription, Summarization, Text-to-Speech, General. Dark theme via sv_ttk for modern appearance.
- **Local TTS Module (local_tts.py, v0.7)**: PiperLocalTTS class implementing ONNX inference with espeak-ng phonemization and Piper model loader.
- **Audio Playback for WAV (audio_playback.py, v0.7)**: Extends miniaudio playback to handle WAV format (used by local Piper). Cloud ElevenLabs continues to use MP3.
- **Tray icon state: SPEAKING (v0.7)**: New state added for TTS playback. Icon color: blue. Returns to IDLE on completion or Escape.

### Changed
- **Settings Dialog UI Overhaul**: Moved from vertical LabelFrame layout to tabbed ttk.Notebook interface. More organized, easier to navigate.
- **TTS Tab Organization (v0.7)**: Cloud provider settings (ElevenLabs voice ID, model ID, format) in one section. Local provider settings (Piper voice selection, model download) in another.
- **Model Caching Location (v0.7)**: Piper models cached in `%LOCALAPPDATA%\VoicePaste\models\tts\` (separate from STT models in `models\`).
- **Config Section Naming**: [tts] section unified for both cloud and local providers (was separate in design phase).

### Fixed
- **Hugging Face Download Bug (v0.7)**: AttributeError: 'XetStorageFile' object has no attribute 'name' when downloading from Xet repos. Direct HTTPS streaming bypasses this issue entirely.
- **Stale Lock File Retry (v0.7)**: Xet Storage repos could hang with infinite retries on stale .lock files. Direct streaming has no lock file overhead.
- **Model Download Progress (v0.7)**: TTS model downloads now show progress bar in Settings dialog (similar to STT model downloads).

---

## [0.6.0] - 2026-02-18

### Added
- **ElevenLabs Cloud TTS (v0.6)**: Text-to-speech synthesis via ElevenLabs API. High-quality, human-like voices in multiple languages. Requires ElevenLabs API key.
- **TTS Hotkeys (v0.6)**: Two new global hotkeys for TTS workflows:
  - Ctrl+Alt+T: Read clipboard content aloud via TTS
  - Ctrl+Alt+Y: Ask AI a question and hear the answer read aloud (record → summarize → TTS)
- **Audio Playback (audio_playback.py, v0.6)**: Plays MP3 audio output from ElevenLabs TTS using miniaudio (C library via ctypes). Thread-safe, minimal latency.
- **TTS Configuration in Settings (v0.6)**: New "Text-to-Speech" tab in Settings dialog. Enable/disable TTS, select voice ID, change model, manage API key.
- **Voice Selection UI (v0.6)**: Predefined ElevenLabs voice presets in Settings dropdown (Lily, Brian, Sarah, George, Daniel). Users can also enter custom voice IDs.
- **TTS Error Handling (v0.6)**: Specific error messages for API key validation (401), rate limits (429), quota exceeded, and network errors.
- **ELEVENLABS_VOICE_PRESETS (constants.py, v0.6)**: Dictionary of common ElevenLabs voices with names and descriptions for easy selection.
- **KEYRING_ELEVENLABS_KEY (constants.py, v0.6)**: ElevenLabs API key stored in Windows Credential Manager (same as OpenAI and OpenRouter).
- **SPEAKING state (AppState enum, v0.6)**: New application state for TTS audio playback. Transitions: PROCESSING → SPEAKING → IDLE.

### Changed
- **State Machine Enhancement (v0.6)**: Added SPEAKING state for TTS workflows. Icon color changes to blue during playback.
- **Hotkey Registration (v0.6)**: Extended to register TTS hotkeys (Ctrl+Alt+T and Ctrl+Alt+Y) in addition to recording hotkeys.
- **Default TTS Provider**: ElevenLabs (cloud) for backward compatibility. Users opt-in to Piper (local) in Settings (v0.7+).
- **API Key Management (v0.6)**: Added ElevenLabs API key to Credential Manager, alongside OpenAI and OpenRouter keys.

### Fixed
- **TTS Initialization**: Graceful fallback if ElevenLabs SDK not installed (optional dependency). TTS simply remains unavailable.
- **Audio Playback Blocking**: Playback runs on separate thread to avoid blocking UI or pipeline.

---

## [0.5.0] - 2026-02-13

### Added
- **Voice Prompt Mode (Ctrl+Alt+A)**: New hotkey to record speech, send as prompt to LLM, and paste the answer. Separate from normal transcription mode. Useful for questions, commands, and interactive tasks.
- **Dynamic Icon Drawing System**: Tray icons are now rendered programmatically (Python PIL) instead of bundled image files. Colors change per state (idle grey, recording red, processing yellow, pasting green).
- **Icon Drawing Module (icon_drawing.py)**: New module to generate state-aware tray icons at runtime. Supports any size and color scheme without bundled assets.
- **Settings Dialog Enhancements**: Improved UI for voice prompt settings and icon customization (v0.5).
- **Build Consolidation**: Single unified build.bat script handles both PyInstaller .exe and console/GUI variants.

### Changed
- **Default hotkeys refined**: Recording hotkey now `ctrl+alt+r` (was `ctrl+shift+v`). Voice Prompt hotkey `ctrl+alt+a` (new).
- **Tray icon generation**: All icons now generated dynamically at startup. No more .png files needed in distribution.

### Fixed
- **Icon rendering performance**: Dynamic generation is lightweight and fast (<50ms per icon).

---

## [0.4.0] - 2026-02-13

### Added
- **Local Speech-to-Text via faster-whisper (v0.4)**: Users can now choose between cloud (OpenAI Whisper API) and offline (faster-whisper with CTranslate2) backends via `[transcription] backend = "local"` in config.
- **Whisper Model Manager (model_manager.py)**: Downloads and caches CTranslate2 Whisper models from Hugging Face Hub to `%LOCALAPPDATA%\VoicePaste\models\`. Supports 6 model sizes (tiny to large-v3, 75MB to 3GB).
- **Model Download UI (settings_dialog.py)**: Settings dialog shows available models, download size, RAM requirements, and download progress. Users can pre-download models or let them auto-download on first use.
- **Local STT Module (local_stt.py)**: Standalone implementation of local Whisper transcription. Lazy-loads model on first call. Thread-safe design.
- **Voice Activity Detection (VAD) Filter**: Uses Silero VAD via onnxruntime to skip silence before Whisper inference, improving accuracy on long recordings. Auto-disabled in frozen .exe due to onnxruntime PyInstaller issue.
- **Dual-mode STT Backend Abstraction**: Factory function `create_stt_backend()` selects cloud or local based on config. Clean protocol-based design allows easy future backends.
- **Compute Options for Local STT**: Configure device (cpu/cuda) and quantization (int8/float16/float32) for Whisper inference.
- **Beam Search Control**: Configurable beam size for local Whisper inference (default: 5).
- **onnxruntime Hook for PyInstaller (rthook_onnxruntime.py)**: Handles the native onnxruntime ONNX model loader crash in --onefile builds by setting required environment variables.

### Changed
- **Config Structure Enhanced**: New `[transcription]` section with backend, model_size, device, compute_type, vad_filter options.
- **API Key Migration Path**: v0.3+ moved API keys to Windows Credential Manager. v0.4 preserves backward compatibility: config.toml keys auto-migrate to keyring on first load.
- **Default STT Backend**: Cloud (OpenAI Whisper API) for compatibility. Users opt-in to local mode in settings.
- **Model Download Location**: %LOCALAPPDATA%\VoicePaste\models\ to keep user's home directory clean.

### Fixed
- **Model Thread Safety**: Download locks prevent concurrent downloads of the same model.
- **Frozen Binary Model Handling**: VAD filter auto-disabled in .exe to avoid onnxruntime segfault. Users can re-enable if stable.

---

## [0.3.0] - 2026-02-13

### Added
- **Settings Dialog (settings_dialog.py)**: GUI for configuration without editing config.toml. Right-click tray → Settings opens tkinter dialog on dedicated thread.
- **Credentials Tab**: Secure management of OpenAI and OpenRouter API keys via Windows Credential Manager (keyring integration).
- **Transcription Tab**: Select Whisper model source (cloud/local), model size, compute device, VAD filter toggle.
- **Summarization Tab**: Enable/disable summarization, select provider (openai/openrouter/ollama), model, custom base URL, custom system prompt.
- **Feedback Tab**: Toggle audio cues, adjust logging level.
- **Windows Credential Manager Integration (keyring_store.py)**: Securely store and retrieve API keys from Windows Credential Manager instead of config.toml. Credentials are encrypted by Windows and never written to disk as plain text.
- **Hot-reload Support**: Settings dialog writes changes back to AppConfig (non-secret fields to config.toml, secrets to keyring) without restarting the tool.
- **Multiple Summarization Providers**: Support for OpenRouter (access to Claude, Llama, etc.) and Ollama (local LLM) in addition to OpenAI.
- **OpenRouter API Integration**: Provider option to use OpenRouter with custom models. Requires OpenRouter API key.
- **Ollama Local LLM Support**: Provider option to use Ollama running on localhost:11434. No API key required. Works offline.
- **Custom Summarization Prompts**: Users can provide their own system prompt for the summarizer via settings or config. Enables use cases like "professional tone", "concise notes", etc.
- **Custom Base URLs**: Allow overriding API endpoints (useful for proxies, self-hosted, OpenRouter custom instances).
- **AppConfig Dataclass Improvements**: Made mutable (unfrozen) to support hot-reload. Added properties for active provider keys/URLs/prompts.

### Changed
- **Config File Layout**: New sections [transcription], [summarization] with provider/model/base_url/custom_prompt fields.
- **Default Summarization Provider**: Remains OpenAI gpt-4o-mini (backward compatible).
- **API Key Management Philosophy**: Keys no longer in config.toml (v0.3+). Config template notes that keys are in Credential Manager.

### Fixed
- **Config Validation**: Stricter validation of provider choices (openai/openrouter/ollama).

---

## [0.2.0] - 2025-02-13

### Added
- **GPT-4o-mini Summarization**: Transcripts are now cleaned up and summarized before pasting. Removes filler words (aehm, also, halt, etc.), fixes grammar, and preserves key information.
- **Audio Feedback Cues**: Recording start/stop, cancellation, and error conditions produce distinct beeps for audio confirmation (can be disabled in config).
- **Visual Tray Icon State Changes**: Tray icon color indicates app state (grey=idle, red=recording, yellow=processing, green=pasting). Helps users confirm their actions.
- **Clipboard Backup and Restore**: Original clipboard contents are preserved. Tool backs up before paste and restores afterward, protecting user's clipboard data.
- **Cancel Recording with Escape Key**: Pressing Escape during recording discards audio and returns to idle without pasting anything.
- **Toast Notifications for Errors**: Windows toast notifications display errors (API failures, no microphone, network errors) non-intrusively. No modal dialogs.
- **API Retry Logic with Exponential Backoff**: Failed API calls are automatically retried up to 2 times with exponential backoff (1.0s initial delay), improving reliability on unreliable connections.
- **Single-Instance Enforcement**: Only one instance of the tool can run at a time (Windows named mutex). Prevents hotkey conflicts and clipboard race conditions.
- **Log File Rotation**: Log files are rotated at 5MB with 3 backup files kept, preventing unbounded disk usage.
- **5-Minute Maximum Recording Duration**: Recordings automatically stop after 5 minutes to prevent accidental endless recordings and excessive API charges.
- **notifications.py Module**: New module providing audio cues (start/stop/cancel/error) and toast notifications. Synthesizes tones programmatically (no bundled audio files).
- **Configurable Hotkey**: Hotkey is now customizable via `[hotkey] combination` in `config.toml`. Supports any combination of modifiers (ctrl, shift, alt, windows) with a key.

### Fixed
- **Hotkey Unregistration**: Now correctly unregisters global hotkey using handle instead of string. Fixes potential resource leaks when tool exits.
- **State Machine Test Mocks**: Added missing mocks for `paste_text` in state machine tests, enabling proper test coverage of the pasting flow.

### Changed
- **Default Hotkey Changed**: Changed from `Ctrl+Win` to `Ctrl+Shift+V` to avoid Windows key interception and reduce hotkey conflicts. This is now the standard default for all new installations.
- **Default Summarization Behavior**: `summarization.enabled` now defaults to `true` in config (was false in early v0.2 development). Users get cleaned text by default.
- **Logging Enhancement**: API key is masked in logs (shows only last 4 characters) per security requirement REQ-S01.
- **Config Template**: `config.example.toml` now includes explanatory comments for all options and clearly marks required fields.

---

## [0.1.0] - 2025-02-13

### Added
- **Global Hotkey (Ctrl+Win)**: Press to start recording, press again to stop and transcribe. Works regardless of active application.
- **Microphone Audio Capture**: Records from system default input device to in-memory buffer (16 kHz, mono WAV format). Audio never touches disk.
- **Cloud Speech-to-Text via OpenAI Whisper API**: Transcribes audio to text using the industry-leading Whisper model. Supports multiple languages.
- **Raw Transcript Paste**: Transcribed text is placed on clipboard and simulated Ctrl+V pastes it at the cursor position in any application.
- **System Tray Presence**: Application runs in system tray with minimal footprint. No main window, no taskbar entry. Right-click for Quit option.
- **TOML Configuration File**: User-friendly `config.toml` for API key and settings. Created automatically if missing.
- **Structured Logging**: Logs to `voice-paste.log` with timestamps, levels, and descriptive messages. Log level configurable in config.
- **API Key Security**: API key is never logged, never hardcoded. Read from config only. Masked in logs.
- **Audio-Only Data Handling**: Audio and transcripts are kept in memory only. Never written to disk. On error or cancel, audio buffers are cleared.

### Technical Details
- **Architecture**: Multi-threaded design with main thread running pystray event loop, daemon thread for keyboard hotkey listener, and worker thread for pipeline.
- **Dependencies**: Minimal core set: Python 3.11+ stdlib, sounddevice, numpy, keyboard, pystray, Pillow, openai, pywin32.
- **Estimated Binary Size**: 40-60 MB when bundled with PyInstaller (includes Python runtime and all dependencies).

---

## Versioning

### v0.1 (MVP - Walking Skeleton)
Validates end-to-end pipeline: hotkey → record → transcribe → paste.

### v0.2 (Core Experience)
Adds practical features users want daily: summarization, visual feedback, audio cues, error handling, clipboard preservation, cancel option.

### v0.3 (Settings & Flexibility)
Moves beyond single-provider setup: configurable prompts, multiple summarization backends (OpenRouter, Ollama), secure credential storage in Windows Credential Manager.

### v0.4 (Offline & Performance)
Local transcription option via faster-whisper. Model downloads, VAD filter, compute device selection. Offline transcription without API costs.

### v0.5 (Voice Prompts & UI Polish)
Voice Prompt mode for interactive Q&A. Dynamic icon generation. Enhanced settings. Build consolidation.

### v0.6 (Text-to-Speech)
ElevenLabs cloud TTS with hotkeys (Ctrl+Alt+T, Ctrl+Alt+Y). Audio playback via miniaudio. SPEAKING state.

### v0.7 (Local TTS)
Offline TTS via Piper ONNX. 14 voices across German/English. Tabbed Settings dialog. Direct HTTPS model streaming.

### v0.8 (Overlay UI & Integrity)
Floating overlay with live recording timer. SHA256 model integrity verification. Expanded voice list.

### v0.9 (API, Confirm-Paste & Hands-Free)
HTTP API server for external control. Confirm-before-paste with AWAITING_PASTE state. Hands-Free wake word detection via faster-whisper tiny. Silence-based auto-stop. 5 security fixes.

### v1.1 (Linux Support)
Cross-platform: Ubuntu 22.04/24.04 alongside Windows. Terminal paste detection, CUDA safe auto-detection, TTS speed control, pystray GNOME fix, platform-specific hotkey defaults. CI matrix across 3 OS targets.

### v1.2+ (Future)
Wayland support (D-Bus GlobalShortcuts), ydotool auto-setup, code signing, localization, advanced features.

---

## Security and Compliance

All releases follow the security requirements defined in [THREAT-MODEL.md](docs/THREAT-MODEL.md):

- Audio never persisted to disk
- API keys never logged or hardcoded (stored securely in Windows Credential Manager since v0.3)
- All API communication via HTTPS with TLS validation
- Clipboard data protected with backup/restore
- No telemetry or analytics
- Logs contain only safe metadata (no secrets, audio, or transcript content)

See [THREAT-MODEL.md](docs/THREAT-MODEL.md) for complete security model and requirements.
