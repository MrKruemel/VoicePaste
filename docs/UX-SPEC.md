# UX Specification

## Voice-to-Summary Paste Tool

**Date**: 2026-02-13
**Author**: UX Designer

---

## 1. Design Principles

1. **Invisible until needed.** The tool lives in the system tray. It has no main window, no taskbar presence, no splash screen. The user should forget it exists until they need it.

2. **Instant feedback.** Every user action (hotkey press, cancel) produces immediate feedback (audio cue, icon change) within 200ms. The user must never wonder "did it register?"

3. **Never steal focus.** The tool must never bring a window to the foreground, move the cursor, or change the active application. The user's current context is sacred.

4. **Fail gracefully and visibly.** Errors are communicated via non-intrusive toast notifications. The tool returns to idle state. No silent failures, no modal dialogs, no crashes.

5. **Zero learning curve.** One hotkey does everything. The interaction is: press, speak, press, done. No modes to remember, no configuration required beyond the API key.

---

## 2. State Definitions and Visual Feedback

### 2.1 States

| State | Tray Icon | Tooltip | Audio Cue | Duration |
|-------|-----------|---------|-----------|----------|
| **IDLE** | Grey microphone | "Voice Paste - Ready (Ctrl+Shift+V)" | None | Indefinite |
| **RECORDING** | Red microphone (solid) | "Voice Paste - Recording..." | Short rising tone on enter | Until user stops |
| **PROCESSING** | Yellow microphone | "Voice Paste - Processing..." | Short falling tone on enter | 2-10 seconds |
| **PASTING** | Green microphone (flash) | N/A (too brief) | None | <200ms |
| **ERROR** | Red exclamation icon (3s) | Error description | Error tone | 3 seconds, then IDLE |
| **CANCELLED** | Grey microphone | "Voice Paste - Cancelled" | Two short low tones | Immediate return to IDLE |

### 2.2 v0.1 Simplified States

For the MVP, only these states need visual distinction:
- **IDLE**: Static tray icon (any simple icon). Tooltip: "Voice Paste - Ready".
- **RECORDING/PROCESSING/PASTING**: No visual distinction from idle in v0.1.
- No audio cues in v0.1.

The tray icon in v0.1 is purely for the context menu (Quit) and to indicate the app is running.

---

## 3. Interaction Flows

### 3.1 Happy Path (v0.1)

```
User                          Tool                          System
 |                              |                              |
 |-- Press Ctrl+Shift+V ------->|                              |
 |                              |-- Start mic recording ------>|
 |                              |                              |
 |   (user speaks)              |   (audio captured)           |
 |                              |                              |
 |-- Press Ctrl+Shift+V ------->|                              |
 |                              |-- Stop recording             |
 |                              |-- Send audio to Whisper API  |
 |                              |   ...waiting...              |
 |                              |<- Transcript received         |
 |                              |-- Write to clipboard         |
 |                              |-- Simulate Ctrl+V ---------->|
 |                              |                              |-- Text appears at cursor
 |                              |-- Return to IDLE             |
```

### 3.2 Happy Path (v0.2+)

```
User                          Tool                          System
 |                              |                              |
 |-- Press Ctrl+Shift+V ------->|                              |
 |                              |-- [AUDIO] Rising tone        |
 |                              |-- [ICON] Red microphone      |
 |                              |-- Start mic recording ------>|
 |                              |                              |
 |   (user speaks)              |   (audio captured)           |
 |                              |                              |
 |-- Press Ctrl+Shift+V ------->|                              |
 |                              |-- [AUDIO] Falling tone       |
 |                              |-- [ICON] Yellow microphone   |
 |                              |-- Stop recording             |
 |                              |-- Backup clipboard           |
 |                              |-- Send audio to Whisper API  |
 |                              |   ...waiting...              |
 |                              |<- Transcript received         |
 |                              |-- Send to summarizer          |
 |                              |   ...waiting...              |
 |                              |<- Summary received            |
 |                              |-- Write summary to clipboard |
 |                              |-- Simulate Ctrl+V ---------->|
 |                              |                              |-- Text appears at cursor
 |                              |-- Wait 150ms                 |
 |                              |-- Restore clipboard          |
 |                              |-- [ICON] Grey microphone     |
 |                              |-- Return to IDLE             |
```

### 3.3 Cancel Flow (v0.2+)

```
User                          Tool
 |                              |
 |-- Press Ctrl+Shift+V ------->|
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

## 5. System Tray Menu

### v0.1 Menu
```
Right-click tray icon:
+------------------+
| Quit             |
+------------------+
```

### v0.2+ Menu
```
Right-click tray icon:
+------------------+
| Status: Idle     |  (greyed out, informational)
+------------------+
| Quit             |
+------------------+
```

### v1.0 Menu
```
Right-click tray icon:
+------------------+
| Status: Idle     |  (greyed out, informational)
+------------------+
| Open Log File    |
| Open Config      |
+------------------+
| Quit             |
+------------------+
```

---

## 6. Audio Cue Specification (v0.2+)

All audio cues are synthesized programmatically (no bundled .wav files) to minimize binary size.

| Cue | Description | Frequency | Duration |
|-----|-------------|-----------|----------|
| Recording start | Rising two-tone beep | 440Hz -> 880Hz | 150ms total |
| Recording stop | Falling two-tone beep | 880Hz -> 440Hz | 150ms total |
| Cancel | Two short low beeps | 330Hz, 330Hz | 100ms each, 50ms gap |
| Error | Single low buzz | 220Hz | 300ms |

**Volume**: System notification volume. Not independently configurable.

**Implementation note**: Use `winsound.Beep()` or generate tones via `sounddevice` playback. `winsound.Beep()` is simpler and has zero dependency cost but is blocking -- must be called from a non-UI thread.

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

## 8. Timing Constraints

| Event | Maximum Acceptable Latency |
|-------|---------------------------|
| Hotkey press to recording start | 500ms |
| Hotkey press to recording stop | 200ms |
| Icon state change | 200ms |
| Audio cue playback | 200ms from trigger |
| STT API call (30s audio) | 10 seconds |
| Summarization API call | 5 seconds |
| Paste execution | 200ms |
| Full pipeline (30s audio) | 15 seconds (v0.1), 18 seconds (v0.2 with summarization) |

---

## 9. Accessibility Notes

- The tool is primarily hotkey-driven. No mouse interaction required for core workflow.
- Tray icon tooltips provide state information for screen reader users.
- Audio cues provide non-visual feedback (can be disabled for users who don't want sound).
- All error information is both logged (persistent) and notified (ephemeral).
