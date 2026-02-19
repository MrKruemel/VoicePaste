# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned for v1.0 (Release)
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

### v1.0 (Release)
Polished, fully documented, tested, and packaged. Ready for public distribution. Code signing, localization, advanced features.

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
