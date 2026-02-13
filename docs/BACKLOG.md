# Voice-to-Summary Paste Tool -- Product Backlog

## Release Plan

### v0.1 -- MVP (Walking Skeleton)
**Goal**: Prove the end-to-end pipeline works: hotkey -> record -> transcribe -> paste.

**Scope**:
- Global hotkey (Ctrl+Shift+V) toggles recording on/off
- Microphone audio capture to in-memory buffer
- Cloud STT via OpenAI Whisper API
- Raw transcript pasted at cursor position (no summarization)
- System tray icon with Quit option
- `config.toml` for API key
- Basic file logging

**Not in scope**: Summarization, audio cues, tray state icons, error notifications, clipboard preservation, configurable hotkey, configurable language. These are v0.2+.

---

### v0.2 -- Core Experience
**Goal**: A tool you would actually want to use daily.

**Scope**:
- Cloud summarization (filler removal, grammar fix, concise output)
- System tray icon states (idle, recording, processing)
- Audio feedback cues (start/stop beeps)
- Error notifications (toast) for common failures
- Clipboard backup/restore (preserve user's clipboard)
- Configurable summarization prompt
- Escape key cancels recording

---

### v1.0 -- Release
**Goal**: Polished, packaged, ready for public distribution.

**Scope**:
- Configurable language (default: German)
- Local STT option (faster-whisper with bundled model)
- Polished config file with all options documented
- Single-file .exe via PyInstaller
- README, CHANGELOG, architecture docs
- Tested on Windows 10 and 11
- Security-reviewed and dependency-audited

---

### v1.x -- Future Backlog
- Local summarization (eliminate all cloud dependency)
- Custom hotkey configuration
- Recording time limit with auto-stop
- History of recent transcriptions (opt-in)
- Multi-language auto-detection
- Whisper model size selection in config
- Configurable output format (bullet points, plain text, formal)
- Auto-start on Windows login (opt-in)

---

## v0.1 MVP User Stories

### US-0.1.1: Global Hotkey Toggle Recording

As a knowledge worker,
I want to press Ctrl+Shift+V to start recording and press Ctrl+Shift+V again to stop,
so that I can quickly dictate text without switching away from my current application.

**Acceptance Criteria:**
- [ ] Pressing Ctrl+Shift+V while idle starts microphone recording
- [ ] Pressing Ctrl+Shift+V while recording stops recording and triggers transcription
- [ ] The hotkey works regardless of which application has focus (global hotkey)
- [ ] The hotkey does not steal focus from the currently active application
- [ ] The hotkey does not conflict with existing Windows system shortcuts in a way that breaks either
- [ ] Only one recording session can be active at a time

---

### US-0.1.2: Microphone Audio Capture

As a user,
I want the tool to capture audio from my default microphone when recording is active,
so that my spoken words are recorded for transcription.

**Acceptance Criteria:**
- [ ] Audio is captured from the system's default input device
- [ ] Audio is stored in an in-memory buffer (not written to disk)
- [ ] Audio format is compatible with the OpenAI Whisper API (WAV, 16kHz or higher)
- [ ] Recording starts within 500ms of hotkey press
- [ ] If no microphone is available, the tool logs an error and does not crash

---

### US-0.1.3: Cloud Speech-to-Text Transcription

As a user,
I want my recorded audio to be transcribed to text using the OpenAI Whisper API,
so that I get an accurate text version of what I said.

**Acceptance Criteria:**
- [ ] Recorded audio is sent to the OpenAI Whisper API for transcription
- [ ] The API call uses HTTPS
- [ ] Transcription completes and returns text for a 30-second recording within 10 seconds
- [ ] If the API call fails (network error, auth error, timeout), the tool logs the error and does not crash
- [ ] The API key is read from config.toml, not hardcoded

---

### US-0.1.4: Paste Transcript at Cursor

As a user,
I want the transcribed text to be automatically pasted at my current cursor position,
so that I can continue typing without manually copying and pasting.

**Acceptance Criteria:**
- [ ] After transcription completes, the text is placed on the clipboard
- [ ] A simulated Ctrl+V keystroke pastes the text at the current cursor position
- [ ] The paste works in at least Notepad and a browser text field
- [ ] The paste does not steal window focus -- text appears in whichever app was active
- [ ] If the transcript is empty (silence detected), nothing is pasted

---

### US-0.1.5: System Tray Presence

As a user,
I want the tool to run in the system tray,
so that it is always available without taking up space in the taskbar.

**Acceptance Criteria:**
- [ ] The application shows an icon in the Windows system tray on launch
- [ ] Right-clicking the tray icon shows a context menu
- [ ] The context menu includes a "Quit" option that cleanly exits the application
- [ ] The application has no main window (tray-only)
- [ ] The application does not appear in the taskbar

---

### US-0.1.6: Configuration File

As a user,
I want to configure my OpenAI API key in a config.toml file,
so that I can use the tool with my own API credentials.

**Acceptance Criteria:**
- [ ] The tool looks for config.toml in the same directory as the executable
- [ ] config.toml contains at minimum an `openai_api_key` field
- [ ] If config.toml is missing, the tool creates a template file and exits with a clear log message
- [ ] If the API key is empty or missing from config.toml, the tool logs an error and exits gracefully
- [ ] The config file is plain text TOML format, editable in any text editor

---

### US-0.1.7: Logging

As a developer/user troubleshooting issues,
I want the tool to write logs to a file,
so that I can diagnose problems when something goes wrong.

**Acceptance Criteria:**
- [ ] The tool writes logs to a file in the same directory as the executable
- [ ] Log file name follows the pattern `voice-paste.log`
- [ ] Logs include timestamps, log level, and descriptive messages
- [ ] Key events are logged: app start, hotkey press, recording start/stop, API call start/complete, paste action, errors
- [ ] Log level is configurable in config.toml (default: INFO)
- [ ] The log file does not contain the API key or audio data

---

## v0.2 Core Experience User Stories

### US-0.2.1: Summarization

As a knowledge worker,
I want my dictated text to be cleaned up and summarized before pasting,
so that I get a concise, well-formed text instead of raw speech.

**Acceptance Criteria:**
- [ ] Transcribed text is sent to a cloud LLM (OpenAI GPT-4o-mini) for summarization
- [ ] Filler words (uhm, ah, also, etc.) are removed
- [ ] Grammar and sentence structure are corrected
- [ ] The output language matches the input language (German in, German out)
- [ ] The summary preserves all key information from the original speech
- [ ] Summarization adds no more than 3 seconds to the total processing time for a 30-second recording

---

### US-0.2.2: Visual State Feedback

As a user,
I want the system tray icon to change appearance based on the tool's state,
so that I always know whether it is idle, recording, or processing.

**Acceptance Criteria:**
- [ ] Idle state: default icon color/appearance
- [ ] Recording state: visually distinct icon (e.g., red dot or pulsing)
- [ ] Processing state: visually distinct icon (e.g., spinning or different color)
- [ ] State transitions are immediate (within 200ms of state change)

---

### US-0.2.3: Audio Feedback Cues

As a user,
I want to hear a short sound when recording starts and stops,
so that I have audio confirmation without looking at the tray icon.

**Acceptance Criteria:**
- [ ] A short, distinct sound plays when recording starts
- [ ] A different short sound plays when recording stops
- [ ] Sounds are non-intrusive (brief, low volume)
- [ ] Sounds can be disabled in config.toml

---

### US-0.2.4: Error Notifications

As a user,
I want to see a Windows toast notification when something goes wrong,
so that I know the tool encountered a problem without having to check log files.

**Acceptance Criteria:**
- [ ] Network/API errors show a notification with a brief description
- [ ] Microphone access errors show a notification
- [ ] Notifications disappear automatically after a few seconds
- [ ] Notifications do not steal focus from the active application

---

### US-0.2.5: Clipboard Preservation

As a user,
I want my clipboard contents to be preserved when the tool pastes text,
so that the tool does not overwrite data I previously copied.

**Acceptance Criteria:**
- [ ] Clipboard contents are backed up before the tool writes to the clipboard
- [ ] Original clipboard contents are restored after the paste operation
- [ ] Restoration works even if an error occurs during processing
- [ ] At least plain text clipboard content is preserved

---

### US-0.2.6: Cancel Recording

As a user,
I want to press Escape to cancel an active recording without pasting anything,
so that I can abort if I started recording by mistake.

**Acceptance Criteria:**
- [ ] Pressing Escape while recording cancels the recording
- [ ] No transcription or paste occurs after cancellation
- [ ] The tool returns to idle state
- [ ] A brief cancellation sound or notification confirms the cancel action

---

## v1.0 Release User Stories (Titles and Descriptions)

### US-1.0.1: Configurable Language
Allow the user to set the transcription and summarization language in config.toml. Default is German (de).

### US-1.0.2: Local STT Option
Support local speech-to-text via faster-whisper with a bundled model, enabling offline use without an API key for STT.

### US-1.0.3: Configurable Summarization Prompt
Allow the user to customize or select from preset summarization prompts (concise, professional, bullet points) in config.toml.

### US-1.0.4: Single-File Executable Distribution
The tool must be distributed as a single .exe file via PyInstaller that runs on Windows 10/11 without additional installation.

### US-1.0.5: Complete Documentation
Provide README with setup instructions, configuration reference, troubleshooting guide, and architecture overview.

### US-1.0.6: Windows 10/11 Compatibility
The tool must be tested and confirmed working on both Windows 10 and Windows 11.
