# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned for v1.0 (Release)
- Configurable language support (default: German)
- Local speech-to-text option (faster-whisper with bundled model)
- Customizable summarization prompts (professional, concise, etc.)
- Single-file .exe distribution via PyInstaller
- Full documentation and README
- Tested on Windows 10 and Windows 11
- Security review and dependency audit
- Code signing for reduced antivirus false positives

See [BACKLOG.md](docs/BACKLOG.md) for the complete v1.0 roadmap.

---

## [0.2.0] - 2025-02-13

### Added
- **GPT-4o-mini summarization**: Transcripts are now cleaned up and summarized before pasting. Removes filler words (aehm, also, halt, etc.), fixes grammar, and preserves key information.
- **Audio feedback cues**: Recording start/stop, cancellation, and error conditions produce distinct beeps for audio confirmation (can be disabled in config).
- **Visual tray icon state changes**: Tray icon color indicates app state (grey=idle, red=recording, yellow=processing, green=pasting). Helps users confirm their actions.
- **Clipboard backup and restore**: Original clipboard contents are preserved. Tool backs up before paste and restores afterward, protecting user's clipboard data.
- **Cancel recording with Escape key**: Pressing Escape during recording discards audio and returns to idle without pasting anything.
- **Toast notifications for errors**: Windows toast notifications display errors (API failures, no microphone, network errors) non-intrusively. No modal dialogs.
- **API retry logic with exponential backoff**: Failed API calls are automatically retried up to 2 times with exponential backoff (1.0s initial delay), improving reliability on unreliable connections.
- **Single-instance enforcement**: Only one instance of the tool can run at a time (Windows named mutex). Prevents hotkey conflicts and clipboard race conditions.
- **Log file rotation**: Log files are rotated at 5MB with 3 backup files kept, preventing unbounded disk usage.
- **5-minute maximum recording duration**: Recordings automatically stop after 5 minutes to prevent accidental endless recordings and excessive API charges.
- **notifications.py module**: New module providing audio cues (start/stop/cancel/error) and toast notifications. Synthesizes tones programmatically (no bundled audio files).
- **Configurable hotkey**: Hotkey is now customizable via `[hotkey] combination` in `config.toml`. Supports any combination of modifiers (ctrl, shift, alt, windows) with a key.

### Fixed
- **Hotkey unregistration**: Now correctly unregisters global hotkey using handle instead of string. Fixes potential resource leaks when tool exits.
- **State machine test mocks**: Added missing mocks for `paste_text` in state machine tests, enabling proper test coverage of the pasting flow.

### Changed
- **Default hotkey changed**: Changed from `Ctrl+Win` to `Ctrl+Shift+V` to avoid Windows key interception and reduce hotkey conflicts. This is now the standard default for all new installations.
- **Default summarization behavior**: `summarization.enabled` now defaults to `true` in config (was false in early v0.2 development). Users get cleaned text by default.
- **Logging enhancement**: API key is masked in logs (shows only last 4 characters) per security requirement REQ-S01.
- **Config template**: `config.example.toml` now includes explanatory comments for all options and clearly marks required fields.

---

## [0.1.0] - 2025-02-13

### Added
- **Global hotkey (Ctrl+Win)**: Press to start recording, press again to stop and transcribe. Works regardless of active application.
- **Microphone audio capture**: Records from system default input device to in-memory buffer (16 kHz, mono WAV format). Audio never touches disk.
- **Cloud speech-to-text via OpenAI Whisper API**: Transcribes audio to text using the industry-leading Whisper model. Supports multiple languages.
- **Raw transcript paste**: Transcribed text is placed on clipboard and simulated Ctrl+V pastes it at the cursor position in any application.
- **System tray presence**: Application runs in system tray with minimal footprint. No main window, no taskbar entry. Right-click for Quit option.
- **TOML configuration file**: User-friendly `config.toml` for API key and settings. Created automatically if missing.
- **Structured logging**: Logs to `voice-paste.log` with timestamps, levels, and descriptive messages. Log level configurable in config.
- **API key security**: API key is never logged, never hardcoded. Read from config only. Mask in logs.
- **Audio-only data handling**: Audio and transcripts are kept in memory only. Never written to disk. On error or cancel, audio buffers are cleared.

### Technical Details
- **Architecture**: Multi-threaded design with main thread running pystray event loop, daemon thread for keyboard hotkey listener, and worker thread for pipeline.
- **Dependencies**: Minimal core set: Python 3.11+ stdlib, sounddevice, numpy, keyboard, pystray, Pillow, openai, pywin32.
- **Estimated binary size**: 40-60 MB when bundled with PyInstaller (includes Python runtime and all dependencies).

---

## Versioning

### v0.1 (MVP - Walking Skeleton)
Validates end-to-end pipeline: hotkey → record → transcribe → paste.

### v0.2 (Core Experience)
Adds practical features users want daily: summarization, visual feedback, audio cues, error handling, clipboard preservation, cancel option.

### v1.0 (Release)
Polished, fully documented, tested, and packaged. Ready for public distribution.

---

## Security and Compliance

All releases follow the security requirements defined in [THREAT-MODEL.md](docs/THREAT-MODEL.md):

- Audio never persisted to disk
- API keys never logged or hardcoded
- All API communication via HTTPS with TLS validation
- Clipboard data protected with backup/restore
- No telemetry or analytics
- Logs contain only safe metadata (no secrets, audio, or transcript content)

See [THREAT-MODEL.md](docs/THREAT-MODEL.md) for complete security model and requirements.
