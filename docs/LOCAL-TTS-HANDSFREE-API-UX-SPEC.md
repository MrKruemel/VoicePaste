# UX Specification: Local TTS, Hands-Free Mode, and External API

**Date**: 2026-02-18
**Author**: UX Designer Agent
**Target Version**: v0.8.0
**Status**: DRAFT -- pending Architect and Developer review
**Depends on**: v0.6 Overlay+TTS spec (OVERLAY-TTS-UX-SPEC.md)

---

## Table of Contents

1. [Design Principles Assessment](#1-design-principles-assessment)
2. [Feature 1: Local TTS via Piper](#2-feature-1-local-tts-via-piper)
3. [Feature 2: External API (Named Pipe)](#3-feature-2-external-api-named-pipe)
4. [Feature 3: Hands-Free Mode (Wake Word)](#4-feature-3-hands-free-mode-wake-word)
5. [Settings Dialog Layout (Updated v0.8)](#5-settings-dialog-layout-updated-v08)
6. [State Machine (Updated v0.8)](#6-state-machine-updated-v08)
7. [Audio Cue Specifications (Updated v0.8)](#7-audio-cue-specifications-updated-v08)
8. [Edge Cases and Error Handling](#8-edge-cases-and-error-handling)
9. [ASCII Mockups](#9-ascii-mockups)
10. [Technical Constraints and Trade-offs](#10-technical-constraints-and-trade-offs)
11. [Open Questions](#11-open-questions)

---

## 1. Design Principles Assessment

### Feature-by-Feature Principle Tension Analysis

| Feature | Principle | Tension | Resolution |
|---------|-----------|---------|------------|
| **Local TTS** | Invisible by default | Model download requires user initiation | Download happens inside Settings dialog. No pop-ups, no wizard. |
| **Local TTS** | Zero learning curve | Cloud vs local choice adds complexity | Default to cloud if API key exists, local if not. Radio buttons with clear labels. |
| **Local TTS** | Graceful failure | Model not downloaded, model corrupted | Clear status indicators. Download/delete within Settings. Fallback guidance in error toasts. |
| **External API** | Never steal focus | External programs could trigger actions | Actions triggered externally follow the same state machine. No new windows. |
| **External API** | Respect the workflow | Unexpected pastes from external triggers | Tray icon shows normal state changes. Optional subtle notification for API-triggered actions. |
| **Hands-Free** | Invisible by default | Always-listening mic requires clear indicator | Dedicated tray icon badge/ring. Icon must ALWAYS show when hands-free is active. |
| **Hands-Free** | Instant feedback | Wake word detection has inherent latency | Audio confirmation cue immediately on detection (<200ms). Visual transition within <100ms. |
| **Hands-Free** | Never steal focus | Wake word + command flow is multi-step | All feedback via tray icon + audio cues. No windows, no dialogs during operation. |
| **Hands-Free** | Respect the workflow | Always-listening microphone is a privacy concern | OFF by default. Prominent visual indicator when active. Easy one-click disable. |

---

## 2. Feature 1: Local TTS via Piper

### 2.1 Overview

**User Story**: As a user, I want to use text-to-speech without an internet connection or API key, so that I can hear text read aloud in any environment using a free, local voice engine.

Piper is a local neural TTS engine that runs entirely on the user's CPU. It downloads ONNX voice models (~15-50 MB each) and synthesizes speech locally. This mirrors the existing local STT (faster-whisper) pattern already established in the Transcription section.

### 2.2 Cloud vs Local TTS Selection

The TTS section in Settings gains a **Backend** selector, identical in pattern to the Transcription section's backend selector. This is a deliberate consistency choice -- users who configured local STT will immediately recognize the pattern.

**Design Decision**: The TTS section backend selector uses the same visual language as the Transcription section:
- Radio-style dropdown (not actual radio buttons, matching existing pattern)
- Sub-frames that swap based on selection (cloud frame vs local frame)
- Same flow: select backend, configure provider-specific fields, download model if needed

### 2.3 Settings Dialog: TTS Section (Updated)

```
+-- Text-to-Speech ------------------------------------------------+
|                                                                    |
|  [x] Enable Text-to-Speech                                        |
|                                                                    |
|  Backend:   [Cloud (ElevenLabs API)              v]                |
|                                                                    |
|  --- Cloud sub-frame (visible when backend = cloud) ---            |
|  API Key:   [****************************1234    ] [Edit]          |
|             Get a key at elevenlabs.io                             |
|                                                                    |
|  Voice:     [Lily (Female, warm, DE/EN)          v]                |
|  Voice ID:  [pFZP5JQG7iQjIQuC4Bku               ]                 |
|  Model:     [eleven_flash_v2_5 (fast, low latency) v]              |
|                                                                    |
|  --- OR ---                                                        |
|                                                                    |
|  --- Local sub-frame (visible when backend = local) ---            |
|  Voice:     [Thorsten (Male, German, natural)    v]                |
|  Status:    Model downloaded and ready.               [Delete]     |
|             ---- OR ----                                           |
|  Status:    Not downloaded (~20 MB).       [Download Voice]        |
|  [===================70%===========] 14.0 / 20.0 MB (70%)         |
|                                                                    |
|  Speed:     [---|====O============|---]   1.0x                     |
|             0.5x                    2.0x                           |
|                                                                    |
|  [Preview]                                                         |
|                                                                    |
|  Local mode: audio is never sent to any server.                    |
+--------------------------------------------------------------------+
```

### 2.4 Backend Dropdown Values

| Value | Label | Description |
|-------|-------|-------------|
| cloud | "Cloud (ElevenLabs API)" | High quality, requires API key + internet |
| local | "Local (Piper, offline)" | Free, offline, lower quality, requires model download |

**Default**: "Cloud (ElevenLabs API)" if an ElevenLabs API key exists in keyring. Otherwise "Local (Piper, offline)".

**Rationale**: Users who already have ElevenLabs configured should not have their workflow disrupted by the addition of a local option. New users benefit from local-first since it requires no API key.

### 2.5 Local TTS Voice Selection

Piper voices are distributed as paired files: a `.onnx` model file and a `.onnx.json` config file. Each voice has a name, language, quality level, and approximate download size.

**Voice Dropdown** (local mode):

| Voice ID | Label | Language | Size | Quality |
|----------|-------|----------|------|---------|
| de_DE-thorsten-high | "Thorsten (Male, German, natural)" | de_DE | ~20 MB | High |
| de_DE-thorsten-medium | "Thorsten (Male, German, compact)" | de_DE | ~15 MB | Medium |
| en_US-amy-medium | "Amy (Female, English, clear)" | en_US | ~20 MB | Medium |
| en_US-lessac-high | "Lessac (Male, English, natural)" | en_US | ~25 MB | High |
| en_GB-alba-medium | "Alba (Female, British English)" | en_GB | ~20 MB | Medium |

**Note**: The voice list is hardcoded in constants.py (like ELEVENLABS_VOICE_PRESETS). Users who want additional Piper voices can enter a custom voice ID. Voice discovery from an online catalog is a future enhancement.

### 2.6 Model Download Flow

The download flow reuses the exact pattern from the Transcription section's local model download (faster-whisper). This is a deliberate consistency choice for zero learning curve.

```
User selects local backend
  |
  v
Voice dropdown shows available voices
  |
  v
User selects a voice (e.g., "Thorsten")
  |
  +--> Voice model already downloaded?
  |      |
  |      YES --> Status: "Model downloaded and ready." [Delete] button shown.
  |      |
  |      NO --> Status: "Not downloaded (~20 MB)." [Download Voice] button shown.
  |
  v (user clicks Download Voice)
  |
  +-- Button text changes to "Cancel"
  +-- Status: "Downloading..." (blue text)
  +-- Progress bar appears: [===========40%==========] 8.0 / 20.0 MB
  +-- Voice dropdown disabled
  +-- Backend dropdown disabled
  |
  v (download complete)
  |
  +-- Status: "Model downloaded and ready." (green text)
  +-- [Download Voice] button disappears
  +-- [Delete] button appears
  +-- Voice dropdown re-enabled
  +-- Backend dropdown re-enabled
```

**Download source**: Piper voices are hosted on Hugging Face (rhasspy/piper-voices). The download URL pattern is:
`https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{language}/{voice_id}/{voice_id}.onnx`

**Storage location**: Same pattern as faster-whisper models. A `piper-voices` subdirectory under the app's cache directory.

### 2.7 Model Delete Flow

Identical to the faster-whisper model delete flow:

```
User clicks [Delete]
  |
  v
Confirmation dialog: "Delete the 'Thorsten' voice model?
                      You will need to download it again to use local TTS."
  [Yes] [No]
  |
  v (Yes)
  |
  +-- Model files deleted from disk
  +-- Status: "Not downloaded (~20 MB)." (amber text)
  +-- [Delete] button disappears
  +-- [Download Voice] button appears
```

### 2.8 Preview Button Behavior

The Preview button works identically regardless of backend (cloud or local):

| Backend | Preview Text | Behavior |
|---------|-------------|----------|
| Cloud | "Dies ist eine Vorschau der ausgewaehlten Stimme." | Calls ElevenLabs API, plays audio |
| Local | "Dies ist eine Vorschau der ausgewaehlten Stimme." | Runs Piper locally, plays audio |

During preview:
- Button text changes to "Stop"
- Clicking "Stop" halts playback
- If preview fails (model not downloaded, API error): error text appears below button in red, 8pt font

**Note**: For local TTS preview, the voice model must be downloaded first. If not downloaded, the Preview button is disabled and tooltip reads "Download the voice model first."

### 2.9 How the User Knows Which TTS Mode is Active

Three indicators:

1. **Settings dialog**: The Backend dropdown clearly shows "Cloud (ElevenLabs API)" or "Local (Piper, offline)". The sub-frame below it shows provider-specific fields.

2. **Tray tooltip**: Updated to include TTS backend info when TTS is enabled.
   - IDLE tooltip with cloud TTS: `"Voice Paste - Ready (Ctrl+Alt+R) | TTS: Cloud"`
   - IDLE tooltip with local TTS: `"Voice Paste - Ready (Ctrl+Alt+R) | TTS: Local"`
   - IDLE tooltip with TTS disabled: `"Voice Paste - Ready (Ctrl+Alt+R)"` (unchanged)

3. **Log output**: On startup and after settings change, log line shows: `TTS: enabled (provider=piper, voice=de_DE-thorsten-high)` or `TTS: enabled (provider=elevenlabs, voice=pFZP5JQG7iQjIQuC4Bku)`.

**No tray icon change**: The tray icon does NOT change for cloud vs local TTS. The user does not need to know the backend during normal operation -- they just hear speech. The distinction matters only during configuration.

### 2.10 Config.toml Changes (TTS Section)

```toml
[tts]
enabled = false
# Backend: "cloud" (ElevenLabs API) or "local" (Piper, offline)
backend = "cloud"
# Cloud settings
provider = "elevenlabs"
voice_id = "pFZP5JQG7iQjIQuC4Bku"
model_id = "eleven_flash_v2_5"
output_format = "mp3_44100_128"
# Local settings (only used when backend = "local")
local_voice = "de_DE-thorsten-high"
# Shared settings
speed = 1.0
```

### 2.11 Constants Additions (constants.py)

```python
# TTS backends
TTS_BACKENDS = ("cloud", "local")
DEFAULT_TTS_BACKEND = "cloud"

# Piper voice presets (local TTS)
PIPER_VOICE_PRESETS: dict[str, dict[str, str]] = {
    "de_DE-thorsten-high": {
        "name": "Thorsten",
        "description": "Male, German, natural",
        "language": "de_DE",
        "download_mb": "20",
        "quality": "high",
    },
    "de_DE-thorsten-medium": {
        "name": "Thorsten",
        "description": "Male, German, compact",
        "language": "de_DE",
        "download_mb": "15",
        "quality": "medium",
    },
    "en_US-amy-medium": {
        "name": "Amy",
        "description": "Female, English, clear",
        "language": "en_US",
        "download_mb": "20",
        "quality": "medium",
    },
    "en_US-lessac-high": {
        "name": "Lessac",
        "description": "Male, English, natural",
        "language": "en_US",
        "download_mb": "25",
        "quality": "high",
    },
    "en_GB-alba-medium": {
        "name": "Alba",
        "description": "Female, British English",
        "language": "en_GB",
        "download_mb": "20",
        "quality": "medium",
    },
}
DEFAULT_PIPER_VOICE = "de_DE-thorsten-high"
```

---

## 3. Feature 2: External API (Named Pipe)

### 3.1 Overview

**User Story**: As a developer or power user, I want external programs (scripts, browser extensions, Autohotkey macros) to trigger Voice Paste actions programmatically, so that I can integrate voice transcription into my custom workflows.

The External API exposes Voice Paste's capabilities via a Windows Named Pipe. External programs write JSON commands to the pipe, and Voice Paste executes them through the same state machine as hotkey-triggered actions.

### 3.2 API Toggle UX

**Location in Settings**: The API toggle lives in the **General** section of the Settings dialog, positioned after the audio cues checkbox and before the hotkey display. It is a simple checkbox with an inline indicator.

```
+-- General ---------------------------------------------------+
|                                                                |
|  [x] Play audio cues                                          |
|  [ ] Enable external API (named pipe)                         |
|       Status: Not running                                      |
|       Pipe name: \\.\pipe\VoicePaste                           |
|                                                                |
|  Hotkeys:                                                      |
|  Summarize:  Ctrl+Alt+R                                        |
|  Ask LLM:    Ctrl+Alt+A                                        |
|  Read TTS:   Ctrl+Alt+T                                        |
|  Ask+TTS:    Ctrl+Alt+Y                                        |
|  Overlay:    Ctrl+Alt+O                                        |
|                                                                |
|  Change hotkeys in config.toml (requires restart)              |
+----------------------------------------------------------------+
```

### 3.3 API Status Indicator

| API State | Status Text | Color |
|-----------|-------------|-------|
| Disabled (checkbox unchecked) | "Not running" | Grey (#999999) |
| Enabled, listening | "Listening on \\\\.\pipe\VoicePaste" | Green (#66CC66) |
| Enabled, error (pipe creation failed) | "Error: could not create pipe" | Red (#FF6B6B) |

The status text updates immediately when the checkbox is toggled. No restart required.

### 3.4 Visual Indicator When API is Active

**Tray Icon**: No change. The API is a passive listener. Adding a visual indicator to the tray icon for "API is listening" would add noise for a power-user feature that most users will never enable.

**Tray Tooltip**: When the API is enabled, the IDLE tooltip appends " | API":
- `"Voice Paste - Ready (Ctrl+Alt+R) | API"`

**Tray Context Menu**: When the API is enabled, the status line shows: `"Status: Idle | API active"`

### 3.5 Notifications for API-Triggered Actions

**Design Decision**: By default, NO notification is shown when an external program triggers an action via the API. The tray icon transitions through its normal states (IDLE -> RECORDING -> PROCESSING -> PASTING -> IDLE or SPEAKING -> IDLE), which provides sufficient visual feedback.

**Rationale**: The user who enables the API is a power user who integrated it deliberately. Notifications for expected API calls would be noise. The tray icon state changes are the feedback mechanism.

**Optional Config**: A config flag `api_notify = false` (default) can be set to `true` to show a toast notification when an API command is received: `"Voice Paste: Action triggered via API (record)"`. This is for debugging integrations, not daily use.

### 3.6 API-Triggered Action Behavior

All API-triggered actions follow the exact same state machine as hotkey-triggered actions:
- Same icon state transitions
- Same audio cues
- Same error handling
- Same debounce (300ms between commands)

The only difference: the trigger source is logged as "API" instead of "hotkey":
`"Hotkey accepted: 'ctrl+alt+r' (API trigger)"` vs `"Hotkey accepted: 'ctrl+alt+r' (keyboard trigger)"`

### 3.7 Security Considerations

Named pipes on Windows support security descriptors. The pipe should be created with a security descriptor that limits access to the current user session (SECURITY_ATTRIBUTES with the user's SID). This prevents other users on the same machine from sending commands.

The Settings dialog shows a brief security note:
`"External API allows local programs to control Voice Paste. Only enable if you use automation scripts."`

### 3.8 Config.toml Changes (API Section)

```toml
[api]
# Enable external API via Windows Named Pipe (default: false)
# When enabled, external programs can trigger Voice Paste actions
# by writing JSON commands to \\.\pipe\VoicePaste
external_api_enabled = false
# Show toast notifications when API commands are received (debug aid)
api_notify = false
```

---

## 4. Feature 3: Hands-Free Mode (Wake Word)

### 4.1 Overview

**User Story**: As a user, I want to activate Voice Paste by saying a wake word instead of pressing a hotkey, so that I can use it hands-free while away from the keyboard.

Hands-free mode enables a continuously-listening, low-power wake word detector. When the wake word is detected, Voice Paste automatically enters RECORDING state as if the user had pressed the hotkey. The user then speaks their content, and recording ends either via a configurable silence timeout, a second wake word utterance, or the Escape key.

This is the most complex UX addition in Voice Paste's history. It fundamentally changes the interaction model from "push-to-talk" to "always-listening." Every design decision must be evaluated against the privacy implications and the "invisible by default" principle.

### 4.2 Tray Icon: Hands-Free Mode Indicator

**Problem**: The user MUST always be able to see at a glance whether hands-free mode (always-listening microphone) is active. This is a privacy requirement. The tray icon must clearly communicate "my microphone is being monitored right now."

**Solution**: When hands-free mode is enabled, the tray icon gets a persistent **ring/halo** around the microphone. This ring is visible in ALL states, not just IDLE.

| State | Normal Icon | Hands-Free Icon |
|-------|-------------|-----------------|
| IDLE | Silver microphone | Silver microphone with **cyan ring** |
| RECORDING | Red microphone | Red microphone with **cyan ring** |
| PROCESSING | Yellow microphone | Yellow microphone with **cyan ring** |
| PASTING | Green microphone | Green microphone with **cyan ring** |
| SPEAKING | Blue microphone | Blue microphone with **cyan ring** |

**Ring Color**: Cyan (0, 200, 210). Chosen because:
- Distinct from all existing state colors (silver, red, yellow, green, blue)
- High contrast against the dark tray background (#2D2D2D)
- Semantically neutral (not alarming like red, not "success" like green)
- Commonly associated with "listening/awareness" in smart speaker UIs

**Ring Implementation**: A 2px solid ring drawn at the outer edge of the 32x32 icon, inset by 1px. The ring is drawn BEFORE the microphone silhouette, so the mic is rendered on top.

```
+------ 32x32 icon ------+
| +----- cyan ring -----+ |
| |                      | |
| |   [microphone in     | |
| |    state color]      | |
| |                      | |
| +----------------------+ |
+--------------------------+
```

### 4.3 Tray Tooltip: Hands-Free Indicator

| Mode | IDLE Tooltip |
|------|-------------|
| Hands-free OFF | `"Voice Paste - Ready (Ctrl+Alt+R)"` |
| Hands-free ON | `"Voice Paste - Listening for wake word... (Ctrl+Alt+R)"` |
| Hands-free ON, recording | `"Voice Paste - Recording... (speak or pause to stop)"` |

### 4.4 Tray Context Menu: Hands-Free Toggle

When hands-free mode is available (configured in Settings), a new menu item appears:

```
Right-click tray icon:
+---------------------------+
| Status: Idle              |  (greyed out)
+---------------------------+
| [*] Hands-Free Mode       |  (* = checkmark when active)
+---------------------------+
| Show Overlay              |
+---------------------------+
| Settings...               |
+---------------------------+
| Quit                      |
+---------------------------+
```

**Menu item behavior**:
- Clicking toggles hands-free mode on/off immediately
- Checkmark appears when active
- When enabled via menu, the tray icon ring appears immediately
- When disabled via menu, the ring disappears immediately
- This menu item is only visible if hands-free is configured in Settings (wake word engine is available)

### 4.5 Wake Word Detection Flow

```
User enables hands-free mode (via Settings or tray menu)
  |
  v
State: IDLE (with cyan ring on tray icon)
  |-- Wake word detector running (low-CPU background thread)
  |-- Microphone is open but only wake word detector receives audio
  |-- No audio is recorded, transcribed, or sent anywhere
  |
  v (wake word detected, e.g., "Hey Voice")
  |
  +-- [AUDIO] Wake word confirmation chime (distinct from recording start)
  +-- [ICON] Red microphone with cyan ring
  +-- State: RECORDING
  +-- Full audio recording begins (same as hotkey-triggered recording)
  |
  v (user speaks content)
  |
  +--> How does recording stop?
       |
       +-- Option A: Silence timeout (default: 2 seconds of silence)
       |   User stops speaking -> 2s silence -> auto-stop -> PROCESSING
       |
       +-- Option B: Wake word again ("Hey Voice" said again)
       |   Detected -> stop recording -> PROCESSING
       |
       +-- Option C: Hotkey (Ctrl+Alt+R pressed)
       |   Same as normal flow -> stop recording -> PROCESSING
       |
       +-- Option D: Escape
           Cancel recording -> IDLE (with cyan ring)
```

### 4.6 Wake Word Detection: Audio and Visual Feedback

**On wake word detected**:
- **Audio**: A short, distinctive chime -- three quick ascending tones (C5-E5-G5, 50ms each, 150ms total). This must be clearly distinct from the normal recording-start rising tone (440Hz->880Hz). The chime says "I heard you" before the user continues speaking.
- **Visual**: Tray icon transitions IDLE (silver+ring) -> RECORDING (red+ring) within <100ms.
- **Timing**: The audio chime plays concurrently with the visual transition. The microphone immediately starts capturing audio for transcription (the wake word itself is NOT included in the transcript).

**On silence timeout (recording auto-stop in hands-free mode)**:
- **Audio**: Same falling tone as normal recording stop (880Hz->440Hz, 150ms).
- **Visual**: Tray icon transitions RECORDING (red+ring) -> PROCESSING (yellow+ring).
- **No toast notification**: Silence timeout is the expected/normal way to end a hands-free recording. A notification would be noise.

**On wake word detected but app is busy (PROCESSING/PASTING/SPEAKING)**:
- **Audio**: None. The wake word is silently ignored.
- **Visual**: No change.
- **Log**: `"Wake word detected during PROCESSING state, ignored."`

### 4.7 Hands-Free Mode with Different Features

The wake word always triggers the **default recording action** (configurable). The user does not specify "summarize" vs "ask AI" via voice -- that would require a second stage of command recognition, which adds latency and complexity.

**Settings option**: "When wake word is detected, start:"
| Option | Label | Behavior |
|--------|-------|----------|
| summary | "Record + Summarize + Paste (like Ctrl+Alt+R)" | Default. Same as pressing Ctrl+Alt+R. |
| prompt | "Record + Ask AI + Paste (like Ctrl+Alt+A)" | Same as pressing Ctrl+Alt+A. |
| tts_ask | "Record + Ask AI + Read Aloud (like Ctrl+Alt+Y)" | Same as pressing Ctrl+Alt+Y. Most useful for true hands-free. |

**Default**: "Record + Summarize + Paste" (same as the primary hotkey).

**Rationale**: The wake word replaces the hotkey press. Everything after that follows the existing pipeline. Users who want Ask AI + TTS for hands-free can select that option in Settings. Switching between modes does not require a restart -- it takes effect on the next wake word detection.

### 4.8 Wake Word Configuration

**Settings dialog: New "Hands-Free" section**, positioned between "Text-to-Speech" and "General":

```
+-- Hands-Free Mode -------------------------------------------+
|                                                                |
|  [ ] Enable hands-free mode (wake word detection)              |
|       Privacy: microphone is always open when enabled.         |
|       Wake word audio is processed locally. Never uploaded.    |
|                                                                |
|  Wake word:   [Hey Voice                         ]             |
|               Custom wake words require a model file (.ppn).   |
|               Built-in: "Hey Voice" (English), "Okay Stimme"   |
|               (German). See docs for custom wake word creation. |
|                                                                |
|  Sensitivity: [---|====O============|---]   0.5                |
|               Low (fewer false triggers) ... High (more        |
|               responsive, may trigger on similar sounds)        |
|                                                                |
|  Action:      [Record + Summarize + Paste (Ctrl+Alt+R) v]     |
|                                                                |
|  Silence timeout: [2.0] seconds                                |
|               Time of silence before recording auto-stops.     |
|               Set to 0 to disable (use hotkey/wake word to     |
|               stop recording instead).                          |
|                                                                |
+----------------------------------------------------------------+
```

### 4.9 Wake Word: Privacy Indicator

**Non-negotiable requirement**: The user must ALWAYS know when the microphone is actively being monitored. The cyan ring on the tray icon serves this purpose. Additionally:

1. **First enable notification**: When hands-free is enabled for the first time (ever), show a toast: `"Hands-free mode enabled. Your microphone is now always open for wake word detection. The wake word is processed locally and never uploaded. Look for the cyan ring on the tray icon."`

2. **Startup notification**: If hands-free was enabled on last shutdown and auto-starts, the startup toast includes: `"Hands-free mode is active (wake word: 'Hey Voice')."`

3. **Settings warning**: The privacy note in the Settings section is always visible (not hidden behind a toggle), styled in amber (#FFB347) text: `"Privacy: microphone is always open when enabled. Wake word audio is processed locally. Never uploaded."`

### 4.10 Hands-Free Hotkey (Quick Toggle)

**New hotkey**: Ctrl+Alt+H (H = Hands-free)

- Pressing Ctrl+Alt+H toggles hands-free mode on/off
- When toggled ON: cyan ring appears, toast: `"Hands-free mode on."`
- When toggled OFF: cyan ring disappears, toast: `"Hands-free mode off."`
- This hotkey is only functional if hands-free is configured in Settings (wake word engine available)
- If pressed when not configured: toast: `"Hands-free not configured. Right-click tray > Settings."`

### 4.11 Hands-Free + Overlay Interaction

When hands-free mode is active and the overlay is visible, the overlay buttons work normally. However, a small visual indicator is added:

- A 6px cyan dot appears in the top-right corner of the overlay's drag handle area, indicating hands-free is active
- This dot disappears when hands-free is toggled off

No other overlay changes. The overlay buttons trigger the same actions as before -- they do not interact with the wake word detector.

### 4.12 Silence Timeout vs Max Recording Duration

Hands-free mode adds a **silence timeout** (default: 2.0 seconds) that is separate from the existing **max recording duration** (300 seconds / 5 minutes).

| Timeout | Trigger | Behavior |
|---------|---------|----------|
| Silence timeout (2.0s) | No speech detected for N seconds | Auto-stop recording, process captured audio |
| Max recording duration (300s) | Recording has been active for 5 minutes | Auto-stop recording, process captured audio, show notification |

The silence timeout is ONLY active during hands-free recording. When recording is started via hotkey, the silence timeout does NOT apply -- the user presses the hotkey again to stop. This distinction is important: hotkey users have a deliberate stop action; hands-free users need an automatic one.

### 4.13 Config.toml Changes (Hands-Free Section)

```toml
[handsfree]
# Enable hands-free mode with wake word detection (default: false)
# PRIVACY: When enabled, the microphone is always open.
# Wake word detection runs locally. Audio is never uploaded.
enabled = false
# Wake word phrase (built-in: "hey_voice", "okay_stimme")
# Custom wake words require a .ppn model file path.
wake_word = "hey_voice"
# Detection sensitivity (0.0 to 1.0, default: 0.5)
# Lower = fewer false triggers, higher = more responsive
sensitivity = 0.5
# Action when wake word is detected: "summary", "prompt", "tts_ask"
action = "summary"
# Silence timeout in seconds (0 = disabled, use hotkey/wake word to stop)
silence_timeout = 2.0

[hotkey]
# ... existing hotkeys ...
handsfree_combination = "ctrl+alt+h"
```

### 4.14 Constants Additions (constants.py)

```python
# Hands-free mode configuration
DEFAULT_HANDSFREE_ENABLED = False
DEFAULT_WAKE_WORD = "hey_voice"
DEFAULT_HANDSFREE_SENSITIVITY = 0.5
DEFAULT_HANDSFREE_ACTION = "summary"
DEFAULT_SILENCE_TIMEOUT_SECONDS = 2.0
HANDSFREE_VALID_ACTIONS = ("summary", "prompt", "tts_ask")
DEFAULT_HANDSFREE_HOTKEY = "ctrl+alt+h"

# Wake word audio cue: three ascending tones (C5, E5, G5)
AUDIO_CUE_WAKEWORD_FREQS = (523, 659, 784)  # C5, E5, G5
AUDIO_CUE_WAKEWORD_DURATION_MS = 50  # per tone

# Hands-free icon ring color
HANDSFREE_RING_COLOR = (0, 200, 210)  # Cyan
HANDSFREE_RING_WIDTH = 2  # pixels

# Piper voice model Hugging Face base URL
PIPER_VOICES_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
```

---

## 5. Settings Dialog Layout (Updated v0.8)

### 5.1 Full Layout ASCII Mockup

```
+-- Voice Paste - Settings -------------------------------------------+
|                                                                      |
|  +-- Transcription -----------------------------------------------+  |
|  |  Backend:  [Cloud (OpenAI Whisper API)  v]                      | |
|  |  API Key:  [****1234                    ] [Edit]                | |
|  |    -- OR (when local) --                                        | |
|  |  Model:    [Base (~145 MB, good quality)  v]                    | |
|  |  Device:   [cpu  v]   cpu = works everywhere, cuda = NVIDIA GPU | |
|  |  Status:   Model downloaded and ready.              [Delete]    | |
|  +------------------------------------------------------------------+|
|                                                                      |
|  +-- Summarization -----------------------------------------------+  |
|  |  [x] Enable summarization                                      | |
|  |  Provider:  [OpenAI  v]   Model: [gpt-4o-mini        ]         | |
|  |  Base URL:  [https://api.openai.com/v1                ]         | |
|  |  Cleanup Prompt:                            [Reset to Default]  | |
|  |  +------------------------------------------------------------+ | |
|  |  | Du bist ein Textbereinigungsassistent...                   | | |
|  |  +------------------------------------------------------------+ | |
|  +------------------------------------------------------------------+|
|                                                                      |
|  +-- Text-to-Speech ---------------------------------------------+  |
|  |  [x] Enable Text-to-Speech                                    | |
|  |  Backend:  [Cloud (ElevenLabs API)  v]                         | |
|  |    -- Cloud fields (API Key, Voice, Model) --                  | |
|  |    -- OR Local fields (Voice, Status, Download) --             | |
|  |  Speed:    [---|====O============|---]   1.0x                  | |
|  |  [Preview]                                                     | |
|  +------------------------------------------------------------------+|
|                                                                      |
|  +-- Hands-Free Mode --------------------------------------------+  |
|  |  [ ] Enable hands-free mode (wake word detection)              | |
|  |       Privacy: microphone is always open when enabled.         | |
|  |  Wake word:    [Hey Voice                       ]              | |
|  |  Sensitivity:  [---|===O============|---]   0.5                | |
|  |  Action:       [Record + Summarize + Paste  v]                 | |
|  |  Silence timeout: [2.0] seconds                                | |
|  +------------------------------------------------------------------+|
|                                                                      |
|  +-- General -----------------------------------------------------+  |
|  |  [x] Play audio cues                                          | |
|  |  [ ] Enable external API (named pipe)                          | |
|  |       Status: Not running                                      | |
|  |                                                                | |
|  |  Hotkeys:                                                      | |
|  |  Summarize:   Ctrl+Alt+R                                       | |
|  |  Ask LLM:     Ctrl+Alt+A                                       | |
|  |  Read TTS:    Ctrl+Alt+T                                       | |
|  |  Ask+TTS:     Ctrl+Alt+Y                                       | |
|  |  Overlay:     Ctrl+Alt+O                                       | |
|  |  Hands-free:  Ctrl+Alt+H                                       | |
|  |  Change hotkeys in config.toml (requires restart)               | |
|  +------------------------------------------------------------------+|
|                                                                      |
|                                        [Cancel]  [Save]              |
+----------------------------------------------------------------------+
```

### 5.2 Section Order Rationale

The sections are ordered by **frequency of configuration change**:

1. **Transcription**: Configured once (cloud vs local, API key). Changes rarely.
2. **Summarization**: Configured once. May change model occasionally.
3. **Text-to-Speech**: Configured once. May preview voices.
4. **Hands-Free Mode**: New section. Likely adjusted during initial setup.
5. **General**: Contains toggles (audio cues, API) and read-only hotkey display. Least frequently changed.

### 5.3 Dialog Sizing

With the addition of Hands-Free Mode and the API toggle, the dialog height increases. Recommended minimum size update:

- **Current**: `minsize(540, 680)`
- **Updated**: `minsize(540, 820)`

If the dialog exceeds the screen height on small displays, add a scrollable frame for the main content area. The button bar (Cancel / Save) remains fixed at the bottom.

---

## 6. State Machine (Updated v0.8)

### 6.1 New State: LISTENING (Hands-Free Only)

The LISTENING state is NOT a new AppState enum value. It is a sub-mode of IDLE. The app is in IDLE state but the wake word detector is running. The distinction is purely visual (cyan ring) and behavioral (wake word triggers recording).

**Rationale**: Adding a separate LISTENING AppState would complicate the entire state machine. Every existing state transition check would need to account for it. Since LISTENING behaves identically to IDLE (accepts all the same hotkeys, responds to all the same triggers), it is modeled as a property of IDLE.

Implementation: `self._handsfree_active: bool = False` flag on VoicePasteApp.

### 6.2 Complete State Transitions (v0.8)

```
IDLE (handsfree_active = False)
  + Ctrl+Alt+R  --> RECORDING (summary mode)
  + Ctrl+Alt+A  --> RECORDING (prompt mode)
  + Ctrl+Alt+Y  --> RECORDING (tts_ask mode)
  + Ctrl+Alt+T  --> TTS_PROCESSING (clipboard read)
  + Ctrl+Alt+O  --> IDLE (overlay toggles)
  + Ctrl+Alt+H  --> IDLE (handsfree_active = True, start wake word detector)
  + API command  --> (same as corresponding hotkey)

IDLE (handsfree_active = True)
  + All hotkeys above still work identically
  + Wake word detected --> RECORDING (configured action mode)
  + Ctrl+Alt+H  --> IDLE (handsfree_active = False, stop wake word detector)

RECORDING
  + Same hotkey  --> PROCESSING
  + Escape       --> IDLE (cancel)
  + 5-min limit  --> PROCESSING (auto-stop)
  + Silence timeout (handsfree only) --> PROCESSING (auto-stop)

PROCESSING
  + Success (summary/prompt mode) --> PASTING --> IDLE
  + Success (tts_ask mode)        --> TTS_PROCESSING --> SPEAKING
  + Error                          --> IDLE

PASTING
  + Complete     --> IDLE

TTS_PROCESSING
  + TTS audio ready   --> SPEAKING
  + Error              --> IDLE

SPEAKING
  + Playback complete  --> IDLE
  + Escape             --> IDLE (stop playback)
  + Ctrl+Alt+R/A       --> stop playback, --> RECORDING
```

**Key**: When returning to IDLE from any state, `handsfree_active` is preserved. If hands-free was on before recording, it remains on after processing completes, and the wake word detector resumes listening.

### 6.3 Wake Word Detector Lifecycle

```
Hands-free enabled (toggle on)
  |
  v
Start wake word detector thread
  |-- Opens microphone in low-power mode
  |-- Runs detection loop (e.g., Picovoice Porcupine, or Vosk keyword spotting)
  |-- On detection: calls _on_wake_word() on main state machine
  |
  v (user starts recording via wake word or hotkey)
  |
  Pause wake word detector
  |-- The microphone is now used for full audio recording
  |-- Wake word detector cannot share the mic stream (or can, depending on engine)
  |
  v (recording complete, pipeline finishes, back to IDLE)
  |
  Resume wake word detector
  |-- Low-power detection loop resumes
```

**Pause/Resume vs Stop/Start**: The wake word detector should be paused (not stopped) during recording, and resumed afterward. Stopping and starting adds latency and may cause audible mic clicks. If the wake word engine supports feeding it audio frames, it can share the same mic stream and simply be ignored during recording.

---

## 7. Audio Cue Specifications (Updated v0.8)

### 7.1 New Audio Cues

| Cue | Frequencies | Duration | Trigger | Rationale |
|-----|-------------|----------|---------|-----------|
| **Wake word detected** | 523Hz, 659Hz, 784Hz (C5, E5, G5) | 50ms each, 150ms total | Wake word recognition | Ascending major triad = positive, attention-getting. Distinct from recording start (440->880Hz rising tone). Shorter and higher-pitched to feel responsive. |
| **Hands-free toggle ON** | 523Hz, 784Hz | 75ms each, 150ms total | Ctrl+Alt+H to enable | Two quick high tones = "activated." |
| **Hands-free toggle OFF** | 784Hz, 523Hz | 75ms each, 150ms total | Ctrl+Alt+H to disable | Same tones reversed = "deactivated." Falling pattern = shutdown. |
| **Silence timeout stop** | 880Hz, 440Hz | 75ms each, 150ms total | Silence timeout fires | Same as normal recording stop. No new cue needed -- the stop action is identical. |

### 7.2 Complete Audio Cue Table (v0.8)

| Cue | Frequencies | Duration | Trigger |
|-----|-------------|----------|---------|
| Recording start | 440Hz -> 880Hz | 75ms each | Enter RECORDING from any trigger |
| Recording stop | 880Hz -> 440Hz | 75ms each | Exit RECORDING to PROCESSING |
| Cancel | 330Hz, 330Hz | 100ms each + 50ms gap | Escape during RECORDING |
| Error | 220Hz | 300ms | Any error |
| TTS stop (user) | 660Hz, 440Hz | 75ms each | Escape during SPEAKING |
| Wake word detected | 523Hz, 659Hz, 784Hz | 50ms each | Wake word recognition |
| Hands-free ON | 523Hz, 784Hz | 75ms each | Hands-free enabled |
| Hands-free OFF | 784Hz, 523Hz | 75ms each | Hands-free disabled |

**Note**: The wake word chime plays BEFORE the recording start cue. Sequence on wake word: chime (150ms) -> tiny pause (50ms) -> recording start tone (150ms). Total: 350ms. The user hears "ding-ding-ding [pause] dee-dee" which clearly communicates "heard you, now recording."

Wait -- that is too many sounds in quick succession. Let me reconsider.

**Revised**: The wake word detection chime REPLACES the recording start cue for wake-word-triggered recordings. The chime communicates both "heard you" and "now recording." Playing both would be 350ms of beeps, which is jarring.

| Trigger | Audio Sequence |
|---------|---------------|
| Hotkey starts recording | Recording start tone (440->880Hz, 150ms) |
| Wake word starts recording | Wake word chime (C5-E5-G5, 150ms). No additional recording start tone. |
| Hotkey stops recording | Recording stop tone (880->440Hz, 150ms) |
| Silence timeout stops recording | Recording stop tone (880->440Hz, 150ms) |
| Escape cancels recording | Cancel cue (330Hz x2, 250ms) |

---

## 8. Edge Cases and Error Handling

### 8.1 Local TTS Edge Cases

| Scenario | Behavior |
|----------|----------|
| User enables local TTS but model not downloaded | Toast: "TTS voice model not downloaded. Right-click tray > Settings > Text-to-Speech > Download Voice." Return to IDLE. |
| Model file corrupted (ONNX load fails) | Toast: "TTS voice model is corrupted. Delete and re-download in Settings." Return to IDLE. Log full error. |
| Download interrupted (network loss, cancel) | "Download cancelled." Progress bar disappears. Model files cleaned up (partial downloads deleted). User can retry. |
| User switches TTS backend while TTS is speaking | TTS continues with old backend until playback completes. New backend takes effect on next TTS request. No interruption. |
| Piper produces empty/silent audio | Toast: "TTS produced no audio. Try a different voice or check the text." Return to IDLE. |
| Piper runs out of memory (very long text) | Toast: "Out of memory during TTS. Try shorter text." Return to IDLE. |
| User selects local TTS voice in different language than text | Piper will attempt to synthesize. Result may be unintelligible. No error -- this is user's choice. |
| Local TTS speed adjustment | Piper supports length_scale parameter. Values: 0.5x = fast (length_scale=0.5), 2.0x = slow (length_scale=2.0). Inverted from ElevenLabs. |
| Both cloud and local TTS configured, user switches | Settings sub-frame swaps. No data loss. Cloud API key remains in keyring. Local model remains on disk. |

### 8.2 External API Edge Cases

| Scenario | Behavior |
|----------|----------|
| API enabled but pipe creation fails | Status shows red error text. Toast: "External API could not start. Another program may be using the pipe name." Log details. App continues without API. |
| External program sends invalid JSON | Log warning: "Invalid API command received." No state change. No notification. |
| External program sends command during RECORDING | Command rejected (same as hotkey during recording). Response: `{"error": "busy", "state": "recording"}` |
| Two external programs send commands simultaneously | Named pipe serializes automatically. Second command waits. Debounce applies. |
| API enabled at startup | Pipe created during app initialization. If creation fails, log warning and continue without API. |
| User disables API via Settings while pipe is active | Pipe server thread stopped gracefully. Connected clients receive pipe closure. |
| Very rapid API commands (<300ms apart) | Same debounce as hotkeys (300ms). Commands within debounce window are rejected with `{"error": "debounced"}`. |
| API command to start TTS when TTS is not configured | Response: `{"error": "tts_not_configured"}`. Same toast as hotkey: "TTS is not configured." |

### 8.3 Hands-Free Mode Edge Cases

| Scenario | Behavior |
|----------|----------|
| Wake word detected during PROCESSING | Ignored. Log: "Wake word during PROCESSING, ignored." |
| Wake word detected during SPEAKING | Ignored during SPEAKING. The user should say the wake word after TTS finishes. |
| Wake word detected during RECORDING | Treated as "stop recording" command. Same as pressing the hotkey again. |
| False wake word trigger (TV, background noise) | Recording starts. If no speech follows, silence timeout fires in 2s. Empty transcript -> "No speech detected." toast. Minimal disruption. |
| Multiple rapid wake word detections (<300ms) | Same debounce as hotkeys (300ms). Second detection ignored. |
| Hands-free enabled but wake word engine not available | Toast: "Wake word engine not available. Install the required library (see docs)." Hands-free checkbox unchecked. |
| User speaks wake word very quietly | Sensitivity slider controls this. Lower sensitivity = fewer detections. User can increase sensitivity in Settings. |
| Wake word engine crashes | Log error. Hands-free deactivated. Cyan ring removed. Toast: "Hands-free mode stopped due to an error. Check the log file." App continues in normal (hotkey) mode. |
| Hands-free mode and screen lock | Wake word detector continues running (it is a background thread). If the user speaks the wake word while the screen is locked, recording starts but paste will fail (no foreground app). This is acceptable -- the transcript is still processed and clipboard is preserved. |
| Headset microphone vs built-in mic | Wake word detector uses the system's default recording device, same as normal recording. If the user switches microphones, the detector follows automatically. |
| User has two Voice Paste features: hands-free + overlay | Both work independently. Hands-free detects wake word. Overlay buttons trigger actions on click. No conflict. |
| Silence timeout set to 0 (disabled) | Recording continues until hotkey, wake word, Escape, or 5-minute max. Toast: "Silence auto-stop disabled. Press hotkey or say wake word to stop recording." (shown once, on first recording after configuration) |
| Privacy concern: is wake word audio stored? | No. Wake word detection runs in a rolling buffer (typically <1 second). Audio frames are discarded immediately after detection analysis. No audio is written to disk, sent to any server, or kept in memory. The `[handsfree]` section in Settings prominently states this. |

### 8.4 Combined Feature Edge Cases

| Scenario | Behavior |
|----------|----------|
| API command triggers recording while hands-free is active | Normal recording starts (API trigger takes precedence). Wake word detector pauses. Resumes on IDLE. |
| Hands-free wake word triggers during API-triggered processing | Ignored (same as any wake word during PROCESSING). |
| User enables hands-free + TTS, says wake word, speaks question, silence timeout -> Ask AI -> TTS reads answer -> back to IDLE with hands-free active | Full pipeline works. After TTS playback completes, state returns to IDLE, hands-free resumes. The user can immediately say the wake word again. |
| User enables all three features simultaneously (hands-free + API + local TTS) | All work independently. Wake word triggers local pipeline. API commands trigger local pipeline. Cloud/local TTS based on config. No conflicts. |

---

## 9. ASCII Mockups

### 9.1 Tray Icon: Normal vs Hands-Free

```
Normal IDLE:                    Hands-Free IDLE:
+--------+                      +--------+
|  +--+  |                      |+-+--+-+|
|  |  |  |                      || |  | ||
|  |  |  |    Silver mic        || |  | ||  Silver mic + cyan ring
|  \  /  |                      |\ \  / /|
|   ||   |                      | \ || / |
|  ----  |                      |  -||-  |
+--------+                      +--------+

Normal RECORDING:               Hands-Free RECORDING:
+--------+                      +--------+
|  +--+  |                      |+-+--+-+|
|  |  |  |                      || |  | ||
|  |  |  |    Red mic           || |  | ||  Red mic + cyan ring
|  \  /  |                      |\ \  / /|
|   ||   |                      | \ || / |
|  ----  |                      |  -||-  |
+--------+                      +--------+
```

(The cyan ring is rendered as a 2px border at the icon's outer edge.)

### 9.2 Settings: TTS Section with Local Backend Selected

```
+-- Text-to-Speech ------------------------------------------------+
|                                                                    |
|  [x] Enable Text-to-Speech                                        |
|                                                                    |
|  Backend:   [Local (Piper, offline)              v]                |
|                                                                    |
|  Voice:     [Thorsten (Male, German, natural)    v]                |
|                                                                    |
|  Status:    Not downloaded (~20 MB).       [Download Voice]        |
|                                                                    |
|  Speed:     [---|====O============|---]   1.0x                     |
|             0.5x                    2.0x                           |
|                                                                    |
|  [Preview]  (disabled -- download voice first)                     |
|                                                                    |
|  Local mode: audio is never sent to any server.                    |
+--------------------------------------------------------------------+
```

### 9.3 Settings: TTS Section During Download

```
+-- Text-to-Speech ------------------------------------------------+
|                                                                    |
|  [x] Enable Text-to-Speech                                        |
|                                                                    |
|  Backend:   [Local (Piper, offline)              v]  (disabled)    |
|                                                                    |
|  Voice:     [Thorsten (Male, German, natural)    v]  (disabled)    |
|                                                                    |
|  Status:    Downloading...                       [Cancel]          |
|  [==================70%==================] 14.0 / 20.0 MB (70%)    |
|                                                                    |
|  Speed:     [---|====O============|---]   1.0x                     |
|             0.5x                    2.0x                           |
|                                                                    |
|  [Preview]  (disabled)                                             |
|                                                                    |
|  Local mode: audio is never sent to any server.                    |
+--------------------------------------------------------------------+
```

### 9.4 Settings: Hands-Free Section

```
+-- Hands-Free Mode -----------------------------------------------+
|                                                                    |
|  [x] Enable hands-free mode (wake word detection)                  |
|       Privacy: microphone is always open when enabled.             |
|       Wake word audio is processed locally. Never uploaded.        |
|                                                                    |
|  Wake word:     [Hey Voice                          ]              |
|                 Built-in: "Hey Voice" (EN), "Okay Stimme" (DE)     |
|                                                                    |
|  Sensitivity:   [---|===O============|---]   0.5                   |
|                 Low                       High                     |
|                                                                    |
|  Action:        [Record + Summarize + Paste (Ctrl+Alt+R)   v]     |
|                                                                    |
|  Silence timeout: [2.0] seconds                                    |
|                 Time of silence before auto-stop. 0 = disabled.    |
|                                                                    |
+--------------------------------------------------------------------+
```

### 9.5 Settings: General Section with API Toggle

```
+-- General -------------------------------------------------------+
|                                                                    |
|  [x] Play audio cues                                              |
|                                                                    |
|  [ ] Enable external API (named pipe)                              |
|       Status: Not running                                          |
|       Pipe: \\.\pipe\VoicePaste                                    |
|       External API allows local programs to control Voice Paste.   |
|       Only enable if you use automation scripts.                   |
|                                                                    |
|  Hotkeys:                                                          |
|  Summarize:    Ctrl+Alt+R                                          |
|  Ask LLM:      Ctrl+Alt+A                                          |
|  Read TTS:     Ctrl+Alt+T                                          |
|  Ask+TTS:      Ctrl+Alt+Y                                          |
|  Overlay:      Ctrl+Alt+O                                          |
|  Hands-free:   Ctrl+Alt+H                                          |
|  Change hotkeys in config.toml (requires restart)                  |
|                                                                    |
+--------------------------------------------------------------------+
```

### 9.6 Updated Tray Context Menu (v0.8)

```
Right-click tray icon:
+---------------------------+
| Status: Idle              |  (greyed out)
+---------------------------+
| [*] Hands-Free Mode       |  (only shown if configured)
+---------------------------+
| Show Overlay              |  (or "Hide Overlay")
+---------------------------+
| Settings...               |
+---------------------------+
| Quit                      |
+---------------------------+
```

When hands-free is active:
```
Right-click tray icon:
+---------------------------+
| Status: Idle (Listening)  |  (greyed out)
+---------------------------+
| [*] Hands-Free Mode       |  (checkmark shown)
+---------------------------+
| Show Overlay              |
+---------------------------+
| Settings...               |
+---------------------------+
| Quit                      |
+---------------------------+
```

---

## 10. Technical Constraints and Trade-offs

### 10.1 Local TTS Engine: Piper

**Ideal**: A local TTS engine with:
- Small model files (<50 MB per voice)
- Fast synthesis (<1s for a sentence on CPU)
- Good quality German voices
- No GPU required
- Python API
- Permissive license

**Piper** (rhasspy/piper) meets all criteria:
- ONNX-based, runs on CPU via onnxruntime (already a transitive dependency of faster-whisper)
- German voices (thorsten) are high quality for local TTS
- Python package: `piper-tts` (pip install)
- MIT license
- Models hosted on Hugging Face

**Constraint**: Piper's Python package `piper-tts` bundles piper-phonemize (a C++ library). In PyInstaller builds, this may need special handling (bundling the shared library). Test in frozen builds early.

**Alternative considered**: Coqui TTS -- larger models, more dependencies, project maintenance uncertain.

### 10.2 Wake Word Engine Options

**Option 1: Picovoice Porcupine** (RECOMMENDED for v0.8)
- Built-in wake words + custom wake words
- Very low CPU usage (<1% sustained)
- Python package: `pvporcupine`
- Free tier: 3 custom wake words per account
- Limitation: requires Picovoice access key (free, but registration required)

**Option 2: Vosk keyword spotting**
- Open source (Apache 2.0)
- No registration required
- Higher CPU usage than Porcupine
- Less accurate keyword spotting (more false triggers)
- Python package: `vosk`

**Option 3: OpenWakeWord**
- Open source
- Good accuracy
- Python package: `openwakeword`
- Requires TensorFlow Lite (adds ~50MB)

**Recommendation**: Start with Picovoice Porcupine for v0.8. It has the best accuracy-to-resource ratio. If the access key requirement is a deal-breaker, fall back to Vosk. The UX design is engine-agnostic -- the Settings dialog abstracts the engine behind the "wake word" text field and sensitivity slider.

### 10.3 Hands-Free Microphone Sharing

**Problem**: The wake word detector and the audio recorder both need microphone access. Can they share?

**Option A: Shared audio stream** (RECOMMENDED)
- A single audio capture stream feeds both the wake word detector and the recorder
- When in IDLE+handsfree, only the wake word detector processes frames
- When in RECORDING, only the recorder buffers frames (wake word detector paused)
- Requires a shared audio capture layer that dispatches frames to the active consumer

**Option B: Separate audio streams**
- Wake word detector opens its own microphone
- Recorder opens its own microphone when recording starts
- Simpler implementation
- Risk: two processes opening the same mic may cause conflicts on some audio drivers

**Recommendation**: Option A. It also has the benefit that the wake word detector can immediately recognize the wake word without any mic-open latency, since the stream is already running.

### 10.4 Hands-Free Icon Ring via Pillow

The icon ring can be drawn in `icon_drawing.py` by adding an optional `ring_color` parameter to `create_icon_image()`:

```python
def create_icon_image(
    size: int = 32,
    color: tuple[int, int, int] = (220, 220, 230),
    bg_color: tuple[int, int, int] = ICON_BG_COLOR,
    mode: str = "RGB",
    ring_color: tuple[int, int, int] | None = None,
    ring_width: int = 2,
) -> Image.Image:
```

When `ring_color` is not None, draw an ellipse ring at the icon's outer edge before drawing the microphone. This is a minor modification to existing code.

---

## 11. Open Questions

| # | Question | Impact | Default Assumption |
|---|----------|--------|--------------------|
| 1 | Should Piper voice download use the same cache directory as faster-whisper models? | Directory structure, cleanup | Yes. A sibling subdirectory: `cache/piper-voices/` next to `cache/faster-whisper/`. |
| 2 | Should the hands-free sensitivity slider be exposed in the tray context menu for quick adjustment? | Menu complexity | No. Settings dialog only. Context menu should stay minimal. |
| 3 | Should wake word detection run during SPEAKING state (to allow "stop reading")? | Voice command complexity | No for v0.8. Wake word = start recording only. Escape key stops TTS. Voice-controlled stop is v1.0+. |
| 4 | Should the API pipe name be configurable? | Config surface area | No. Fixed as `\\.\pipe\VoicePaste`. Power users can change via config.toml if needed: `api_pipe_name = "\\.\pipe\VoicePaste"`. |
| 5 | Should hands-free mode auto-disable when the user locks the screen? | Privacy, edge case | No. The wake word detector is passive. Screen lock does not imply microphone should stop. Users can toggle Ctrl+Alt+H before locking. |
| 6 | How should the hands-free indicator interact with the overlay's drag handle dot? | Visual complexity | Cyan dot in overlay drag handle area = simple, consistent. No animation needed. Dot appears/disappears with hands-free toggle. |
| 7 | Should the wake word chime volume be independent of the audio cues toggle? | If user disables audio cues, should wake word chime also be silenced? | Yes, tied to audio cues toggle. If audio_cues = false, wake word chime is also silenced. The visual indicator (red icon) is sufficient. |
| 8 | German-first: should "Okay Stimme" be the default wake word for German-locale systems? | Localization | Yes. If `locale.getdefaultlocale()` returns `de_*`, default wake word = "okay_stimme". Otherwise "hey_voice". |
| 9 | Should the Piper model download show estimated time remaining? | UX polish | No for v0.8. Models are small (~20 MB). Download completes in seconds on broadband. Elapsed time display (existing pattern) is sufficient. |
| 10 | Picovoice Porcupine requires an access key. Where is it stored? | Keyring, config | In keyring, like other API keys. New keyring key: "picovoice_access_key". Settings field in Hands-Free section, masked like API keys. |

---

## Appendix A: New Keyring Constants (v0.8)

```python
KEYRING_PICOVOICE_KEY = "picovoice_access_key"
```

## Appendix B: Complete Hotkey Table (v0.8)

| Hotkey | Action | Version | Required |
|--------|--------|---------|----------|
| Ctrl+Alt+R | Record + Summarize + Paste | v0.1+ | Always |
| Ctrl+Alt+A | Record + Ask AI + Paste | v0.5+ | Always |
| Ctrl+Alt+T | Read clipboard TTS | v0.6+ | TTS enabled |
| Ctrl+Alt+Y | Record + Ask AI + TTS | v0.6+ | TTS enabled |
| Ctrl+Alt+O | Toggle overlay | v0.7+ | Always |
| Ctrl+Alt+H | Toggle hands-free mode | v0.8+ | Hands-free configured |
| Escape | Cancel recording / Stop TTS | v0.2+ | During RECORDING/SPEAKING |

## Appendix C: Tray Icon Color Matrix (v0.8)

| State | Hands-Free OFF | Hands-Free ON |
|-------|---------------|---------------|
| IDLE | Silver (220, 220, 230) | Silver (220, 220, 230) + Cyan ring (0, 200, 210) |
| RECORDING | Red (230, 50, 50) | Red (230, 50, 50) + Cyan ring |
| PROCESSING | Yellow (240, 200, 40) | Yellow (240, 200, 40) + Cyan ring |
| PASTING | Green (50, 200, 80) | Green (50, 200, 80) + Cyan ring |
| SPEAKING | Blue (70, 130, 230) | Blue (70, 130, 230) + Cyan ring |

## Appendix D: New Config.toml Sections Summary (v0.8)

```toml
[tts]
# Updated: new "backend" and "local_voice" fields
enabled = false
backend = "cloud"
provider = "elevenlabs"
voice_id = "pFZP5JQG7iQjIQuC4Bku"
model_id = "eleven_flash_v2_5"
output_format = "mp3_44100_128"
local_voice = "de_DE-thorsten-high"
speed = 1.0

[handsfree]
# New section
enabled = false
wake_word = "hey_voice"
sensitivity = 0.5
action = "summary"
silence_timeout = 2.0

[api]
# Updated: new external API fields
external_api_enabled = false
api_notify = false

[hotkey]
# Updated: new hands-free hotkey
combination = "ctrl+alt+r"
prompt_combination = "ctrl+alt+a"
tts_combination = "ctrl+alt+t"
tts_ask_combination = "ctrl+alt+y"
overlay_combination = "ctrl+alt+o"
handsfree_combination = "ctrl+alt+h"
```
