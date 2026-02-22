# UX Specification

## Voice Paste Tool

**Date**: 2026-02-14
**Author**: UX Designer
**Current Version**: 0.5.0

---

## 1. Design Principles

1. **Invisible until needed.** The tool lives in the system tray. It has no main window, no taskbar presence, no splash screen. The user should forget it exists until they need it.

2. **Instant feedback.** Every user action (hotkey press, cancel) produces immediate feedback (audio cue, icon change) within 200ms. The user must never wonder "did it register?"

3. **Never steal focus.** The tool must never bring a window to the foreground, move the cursor, or change the active application. The user's current context is sacred.

4. **Configuration without configuration.** Settings are available but optional. Defaults work out-of-the-box. Advanced users can customize via Settings dialog (no need to edit config files).

5. **Fail gracefully and visibly.** Errors are communicated via non-intrusive toast notifications. The tool returns to idle state. No silent failures, no modal dialogs, no crashes.

6. **Zero learning curve for core task.** One hotkey does everything for the main use case: record → transcribe → paste. Voice Prompt mode (Ctrl+Alt+A) is optional for advanced users.

---

## 2. State Definitions and Visual Feedback

### 2.1 States (v0.5)

| State | Tray Icon | Tooltip | Audio Cue | Duration |
|-------|-----------|---------|-----------|----------|
| **IDLE** | Grey microphone | "Voice Paste - Ready (Ctrl+Alt+R)" | None | Indefinite |
| **RECORDING** | Red microphone (solid) | "Voice Paste - Recording..." | Short rising tone on enter | Until user stops or 5-min max |
| **PROCESSING** | Yellow microphone | "Voice Paste - Processing..." | Short falling tone on enter | 2-30 seconds (depends on STT backend) |
| **PASTING** | Green microphone (flash) | N/A (too brief) | None | <200ms |
| **ERROR** | Red exclamation icon | Error description | Error tone | 3 seconds, then IDLE |
| **CANCELLED** | Grey microphone | "Voice Paste - Ready" | Two short low tones | Immediate return to IDLE |

**Icon Colors (v0.5):**
- Grey: IDLE (ready for input)
- Red: RECORDING (actively capturing audio)
- Yellow: PROCESSING (transcribing or summarizing)
- Green: PASTING (briefly shown during paste execution)
- Red exclamation: ERROR (transient, 3s)

**Note**: Icons are generated dynamically at runtime via `icon_drawing.py`. No bundled .png files.

---

## 3. Interaction Flows

### 3.1 Normal Mode - Transcribe & Paste (Ctrl+Alt+R)

```
User                          Tool                          System
 |                              |                              |
 |-- Press Ctrl+Alt+R -------->|                              |
 |                              |-- [AUDIO] Rising tone        |
 |                              |-- [ICON] Red microphone      |
 |                              |-- Start mic recording ------>|
 |                              |                              |
 |   (user speaks)              |   (audio captured)           |
 |                              |                              |
 |-- Press Ctrl+Alt+R -------->|                              |
 |                              |-- [AUDIO] Falling tone       |
 |                              |-- [ICON] Yellow microphone   |
 |                              |-- Stop recording             |
 |                              |-- Backup clipboard           |
 |                              |-- Send audio to Whisper      |
 |                              |   (cloud or local)           |
 |                              |   ...waiting...              |
 |                              |<- Transcript received         |
 |                              |-- Send to Summarizer (optional)|
 |                              |   ...waiting...              |
 |                              |<- Summary/cleaned text        |
 |                              |-- Write to clipboard         |
 |                              |-- [ICON] Green microphone    |
 |                              |-- Simulate Ctrl+V ---------->|
 |                              |                              |-- Text appears at cursor
 |                              |-- Wait 150ms                 |
 |                              |-- Restore clipboard          |
 |                              |-- [ICON] Grey microphone     |
 |                              |-- Return to IDLE             |
```

### 3.2 Voice Prompt Mode - Q&A (Ctrl+Alt+A, v0.5)

```
User                          Tool                          System
 |                              |                              |
 |-- Press Ctrl+Alt+A -------->|                              |
 |                              |-- [AUDIO] Rising tone        |
 |                              |-- [ICON] Red microphone      |
 |                              |-- Start mic recording ------>|
 |                              |                              |
 |   (user speaks question)     |   (audio captured)           |
 |                              |                              |
 |-- Press Ctrl+Alt+A -------->|                              |
 |                              |-- [AUDIO] Falling tone       |
 |                              |-- [ICON] Yellow microphone   |
 |                              |-- Stop recording             |
 |                              |-- Send audio to Whisper      |
 |                              |   ...waiting...              |
 |                              |<- Transcript received         |
 |                              |-- Send transcript to LLM     |
 |                              |   as prompt (not cleanup)    |
 |                              |   ...waiting...              |
 |                              |<- LLM answer received         |
 |                              |-- Write answer to clipboard  |
 |                              |-- [ICON] Green microphone    |
 |                              |-- Simulate Ctrl+V ---------->|
 |                              |                              |-- Answer appears at cursor
 |                              |-- Return to IDLE             |
```

### 3.3 Cancel Flow (Escape)

```
User                          Tool
 |                              |
 |-- Press Ctrl+Alt+R -------->|
 |                              |-- [AUDIO] Rising tone
 |                              |-- [ICON] Red microphone
 |                              |-- Start recording
 |                              |
 |-- Press Escape ------------>|
 |                              |-- [AUDIO] Two low tones
 |                              |-- Discard audio buffer
 |                              |-- [ICON] Grey microphone
 |                              |-- Return to IDLE
 |                              |   (nothing pasted)
```

### 3.4 Settings Dialog (Right-click → Settings, v0.3+)

```
User                          Tool
 |                              |
 |-- Right-click tray -------->|
 |   icon                       |
 |                              |-- Show context menu
 |-- Click "Settings" -------->|
 |                              |-- Spawn tkinter dialog on
 |                              |   dedicated thread
 |                              |   (does NOT block tray)
 |                              |
 |-- (User adjusts tabs)       |-- Display tabs:
 |   - Credentials             |   Credentials, Transcription,
 |   - Transcription           |   Summarization, Feedback
 |   - Summarization           |
 |   - Feedback                |-- Hot-reload: changes
 |                              |   apply immediately
 |-- Click "Save" ------------>|
 |                              |-- Write non-secrets to
 |                              |   config.toml
 |                              |-- Store secrets in keyring
 |                              |-- Apply changes (no restart)
 |-- Close dialog ------------>|
 |                              |-- Singleton lock released
```

---

## 4. Edge Cases and Error Handling

### 4.1 No Microphone Available

| When | Behavior |
|------|----------|
| v0.1 | Log error "No microphone detected". Return to IDLE. |
| v0.2+ | Show toast notification: "No microphone detected. Check your audio settings." Return to IDLE. Show error icon for 3 seconds. |

**Detection**: Check for available input devices at recording start, not at app launch. Microphones can be plugged in/out.

### 4.2 API Authentication Failure (401)

| When | Behavior |
|------|----------|
| v0.1 | Log error "API authentication failed. Check your API key in config.toml". Return to IDLE. |
| v0.2+ | Toast: "API key invalid. Check config.toml." Log full error. Return to IDLE. |

### 4.3 API Network Error / Timeout

| When | Behavior |
|------|----------|
| v0.1 | Log error with details. Return to IDLE. |
| v0.2+ | Toast: "Network error. Check your internet connection." Log full error. Return to IDLE. Clipboard restored if backed up. |

**Timeout**: 30 seconds for STT API call, 15 seconds for summarization. These are generous to accommodate large recordings.

### 4.4 Empty Recording (Silence / Very Short)

| When | Behavior |
|------|----------|
| Detection | Recording shorter than 0.5 seconds or Whisper returns empty string |
| v0.1 | Log "Empty recording, nothing to paste." Return to IDLE. |
| v0.2+ | Toast: "No speech detected." Return to IDLE. No paste. |

### 4.5 Rapid Hotkey Toggle (Double-Press / Accidental)

| Scenario | Behavior |
|----------|----------|
| Press-release-press within 300ms | **Debounce**: Ignore the second press. The first press toggles state. A 300ms debounce window prevents accidental double-triggers. |
| Press during PROCESSING state | **Ignore**: Hotkey is disabled while processing. Log "Hotkey pressed during processing, ignored." |
| Press during PASTING state | **Ignore**: Pasting is <200ms; practically impossible but handled by ignoring. |

### 4.6 Application Loses Focus During Processing

| When | Behavior |
|------|----------|
| User switches apps while processing | The paste targets whatever app has focus at paste time. This is correct behavior -- the user switched deliberately. |

### 4.7 Clipboard Contains Non-Text Data (Images, Files)

| When | Behavior |
|------|----------|
| v0.1 | Clipboard is overwritten with transcript text. No backup/restore. |
| v0.2+ | Backup only plain text format (CF_TEXT/CF_UNICODETEXT). If clipboard had non-text data, it will be lost. Log a warning. This is an accepted limitation documented in README. |

### 4.8 Target Application Does Not Accept Ctrl+V

| When | Behavior |
|------|----------|
| Paste fails silently | The transcript is still on the clipboard. User can manually Ctrl+V. In v0.2+, clipboard is restored after delay, so the user has a window to paste manually if needed. |
| Known problematic apps | Terminal emulators (use Ctrl+Shift+V), some custom controls. Documented in README as known limitation. |

### 4.9 Config File Missing or Invalid

| When | Behavior |
|------|----------|
| config.toml missing | Create a template config.toml with empty API key. Log: "Created config.toml template. Please add your OpenAI API key and restart." Exit cleanly. |
| API key empty/missing | Log: "OpenAI API key not configured in config.toml." Exit cleanly. |
| TOML parse error | Log: "config.toml has invalid syntax: [details]." Exit cleanly. |

### 4.10 Multiple Instances

| When | Behavior |
|------|----------|
| User launches a second instance | The second instance should detect the first (via a lock file or named mutex) and exit with a message: "Voice Paste is already running." |
| v0.1 | Acceptable to have undefined behavior with multiple instances. Lock file is a v0.2 enhancement. |

---

## 5. System Tray Menu (v0.5)

```
Right-click tray icon:
+------------------+
| Status: Idle     |  (greyed out, informational)
+------------------+
| Settings         |  (v0.3+, opens dialog)
+------------------+
| Quit             |
+------------------+
```

**Status line** shows current state: "Status: Idle", "Status: Recording", "Status: Processing", "Status: Pasting", or "Status: Error".

**Settings** opens the tkinter settings dialog on a dedicated thread (non-blocking tray).

**v1.0 future enhancements**: Open Log File, Open Config, Model Manager UI.

---

## 6. Audio Cue Specification (v0.2+, v0.5 unchanged)

All audio cues are synthesized programmatically via `notifications.py` to minimize binary size.

| Cue | Description | Frequency | Duration |
|-----|-------------|-----------|----------|
| Recording start | Rising two-tone beep | 440Hz -> 880Hz | 150ms total |
| Recording stop | Falling two-tone beep | 880Hz -> 440Hz | 150ms total |
| Cancel | Two short low beeps | 330Hz, 330Hz | 100ms each, 50ms gap |
| Error | Single low buzz | 220Hz | 300ms |

**Volume**: System notification volume. Not independently configurable.

**Disable**: Users can disable audio cues in Settings > Feedback tab or via `[feedback] audio_cues = false` in config.toml.

**Implementation**: `notifications.py` uses `winsound.Beep()` or numpy-generated sine waves via sounddevice. Called from worker threads to avoid blocking.

---

## 7. Toast Notification Specification (v0.2+)

Toast notifications are Windows 10/11 native notifications.

| Type | Title | Body | Duration |
|------|-------|------|----------|
| API Error | "Voice Paste" | "API error: [brief description]" | 5 seconds |
| Network Error | "Voice Paste" | "Network error. Check connection." | 5 seconds |
| No Microphone | "Voice Paste" | "No microphone detected." | 5 seconds |
| No Speech | "Voice Paste" | "No speech detected." | 3 seconds |
| Config Error | "Voice Paste" | "Config error: [details]" | 5 seconds |

**Implementation**: Use `plyer` library or Windows COM notifications via `win10toast_click` / `winotify`. Choose the option with smallest binary impact.

---

## 8. Timing Constraints (v0.5)

| Event | Maximum Acceptable Latency |
|-------|---------------------------|
| Hotkey press to recording start | 500ms |
| Hotkey press to recording stop | 200ms |
| Icon state change | 200ms |
| Audio cue playback | 200ms from trigger |
| STT (cloud, 30s audio) | 10 seconds |
| STT (local, 30s audio) | 15-60 seconds (depends on model size) |
| Summarization API call | 5 seconds |
| Prompt (Q&A) API call | 5-10 seconds |
| Paste execution | 200ms |
| **Full pipeline (cloud STT + summarization)** | 18 seconds (typical) |
| **Full pipeline (local STT, base model)** | 30 seconds (typical) |

**Note**: Users can configure timeouts in config.toml if they need different limits.

---

## 9. Settings Dialog Tabs (v0.3+, v0.5)

### Credentials Tab
- OpenAI API Key (masked input, stored in keyring)
- OpenRouter API Key (masked input, stored in keyring)
- Help text: "Keys are stored securely in Windows Credential Manager."
- Test button: "Test API Key" (makes a small API call to verify)

### Transcription Tab
- Backend selection: Radio buttons (Cloud / Local)
- Model size (Local only): Dropdown (tiny, base, small, medium, large-v2, large-v3)
- Device (Local only): Radio buttons (CPU / CUDA)
- Quantization (Local only): Dropdown (int8, float16, float32)
- VAD Filter toggle (Local only): Checkbox
- Download Model button (Local mode): Opens progress dialog, downloads from Hugging Face

### Summarization Tab
- Summarization enabled: Toggle
- Provider: Radio buttons (OpenAI / OpenRouter / Ollama)
- Model: Text input (with provider-specific defaults suggested)
- Custom Base URL: Text input (optional)
- Custom Prompt: Text area (optional, shows default if empty)
- Test button: "Test Summarization" (uses test text)

### Feedback Tab
- Audio cues enabled: Toggle
- Log level: Dropdown (DEBUG / INFO / WARNING / ERROR)
- Help text: Links to log file location

---

## 10. Accessibility Notes (v0.5)

- The tool is primarily hotkey-driven. No mouse interaction required for core workflow (normal mode or voice prompt mode).
- Tray icon tooltips provide state information.
- Audio cues provide non-visual feedback (can be disabled in Settings for users who don't want sound).
- Settings dialog is keyboard-navigable (Tab, Enter, Spacebar).
- All error information is both logged (persistent) and notified (ephemeral via toast).
- Toast notifications display for 3-5 seconds (configurable system setting).

---

## 11. First-Run Experience (v0.3+)

1. User runs the tool for the first time.
2. No config.toml exists → tool creates a template.
3. Tray icon appears.
4. Right-click tray → "Settings" opens automatically (or can be triggered on first hotkey press).
5. Settings dialog: Credentials tab highlighted.
6. User enters OpenAI API key → click Save.
7. Key is stored in keyring. Settings dialog closes.
8. User can now press Ctrl+Alt+R to record.
9. On first recording, tool defaults to cloud STT + summarization enabled.

**Fallback**: If settings dialog fails to open, a startup notification directs user to Settings menu later.
