# Overlay Window and TTS Playback -- UX Specification

**Date**: 2026-02-18
**Author**: UX Designer Agent
**Target Version**: v0.6.0
**Status**: DRAFT -- pending Architect and Developer review

---

## Table of Contents

1. [Design Principles Assessment](#1-design-principles-assessment)
2. [New Application States](#2-new-application-states)
3. [Overlay Window](#3-overlay-window)
4. [TTS Playback](#4-tts-playback)
5. [Combined Flows: Ask AI + TTS](#5-combined-flows-ask-ai--tts)
6. [Context Menu Integration](#6-context-menu-integration)
7. [Settings Panel: TTS Configuration](#7-settings-panel-tts-configuration)
8. [Hotkey Recommendations](#8-hotkey-recommendations)
9. [Audio Cue Specifications](#9-audio-cue-specifications)
10. [Edge Cases and Error Handling](#10-edge-cases-and-error-handling)
11. [ASCII Mockups](#11-ascii-mockups)
12. [Technical Constraints and Trade-offs](#12-technical-constraints-and-trade-offs)
13. [Open Questions](#13-open-questions)

---

## 1. Design Principles Assessment

The overlay window is the most significant UX departure in the application's history. It introduces a **visible, persistent UI surface** into what was previously an invisible tool. Every design decision in this spec has been evaluated against the five core principles. Where tension exists, I have documented it explicitly.

### Principle Tensions

| Principle | Tension | Resolution |
|-----------|---------|------------|
| **Invisible by default** | The overlay is, by definition, visible. | The overlay is **off by default**. It is toggled on-demand via a dedicated hotkey. When hidden, the app is identical to v0.5. |
| **Never steal focus** | The overlay is a window. Windows can steal focus. | The overlay uses WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW. It never appears in the taskbar. It never receives keyboard focus. Clicks on buttons return focus to the previous window immediately. |
| **Zero learning curve** | Four new buttons, a new hotkey, TTS behavior. | Buttons use icon + text labels. Tooltips on hover. The overlay itself is entirely optional -- all overlay actions are also accessible via hotkeys. |
| **Respect the workflow** | TTS audio plays over the user's work. | TTS has a global stop hotkey (Escape). Volume follows system notification volume. No modal dialogs during playback. |

---

## 2. New Application States

### 2.1 Extended State Machine (v0.6)

The existing states (IDLE, RECORDING, PROCESSING, PASTING) are retained unchanged. Two new states are added:

| State | Tray Icon | Overlay Indicator | Tooltip | Duration |
|-------|-----------|-------------------|---------|----------|
| **SPEAKING** | Blue microphone (new) | Speaker icon animated | "Voice Paste - Speaking..." | Until TTS completes or user stops |
| **TTS_PROCESSING** | Yellow microphone (reused) | Spinner on active button | "Voice Paste - Preparing speech..." | 1-5 seconds (API call) |

**SPEAKING** is a new terminal-ish state. It does not interfere with IDLE for the purposes of hotkey listening. The user CAN start a new recording while TTS is playing (TTS stops, recording starts -- see Section 4.5).

### 2.2 State Transition Diagram (v0.6)

```
                           Ctrl+Alt+R / Ctrl+Alt+A
                    +----------------------------------+
                    |                                  |
                    v                                  |
                  IDLE -----> RECORDING -----> PROCESSING -----> PASTING -----> IDLE
                    |              |                |
                    |          Escape: IDLE     Error: IDLE
                    |
                    |  (Overlay: Clipboard->TTS)    (Overlay: Ask AI+TTS)
                    |         |                            |
                    +-----> TTS_PROCESSING -----> SPEAKING -----> IDLE
                                   |                  |
                               Error: IDLE      Escape / Stop: IDLE
                                              Recording starts: stop TTS, -> RECORDING
```

### 2.3 Icon Colors (Updated for v0.6)

| State | Color | RGB Value | Rationale |
|-------|-------|-----------|-----------|
| IDLE | Silver-white | (220, 220, 230) | Unchanged |
| RECORDING | Red | (230, 50, 50) | Unchanged |
| PROCESSING | Yellow | (240, 200, 40) | Unchanged -- reused for TTS_PROCESSING |
| PASTING | Green | (50, 200, 80) | Unchanged |
| **SPEAKING** | **Blue** | **(70, 130, 230)** | New. Distinct from all other states. Blue = audio output (vs red = audio input). |
| TTS_PROCESSING | Yellow | (240, 200, 40) | Same as PROCESSING -- both mean "waiting for API". |

---

## 3. Overlay Window

### 3.1 Overview

The overlay is a floating, always-on-top, non-activating mini-window that provides mouse-accessible buttons for Voice Paste actions. It is designed for users who prefer point-and-click to memorizing hotkeys, and for the new TTS actions which have no existing hotkey equivalents.

**User Story**: As a user, I want a small floating panel with buttons for recording, AI query, and text-to-speech, so that I can use Voice Paste features without memorizing hotkeys.

### 3.2 Dimensions and Layout

- **Width**: 52 pixels (4 icon buttons in a vertical strip)
- **Height**: 220 pixels (4 buttons at 44px each + 8px padding top/bottom + 12px drag handle area)
- **Orientation**: Vertical strip (like a floating toolbar)
- **Corner Radius**: 8px
- **Background**: Semi-transparent dark (#1C1C1C at 92% opacity)
- **Border**: 1px solid #444444

The overlay is deliberately narrow to minimize screen real estate usage. A vertical strip hugs the screen edge and is less disruptive than a horizontal bar.

### 3.3 Button Specifications

Each button is a 44x44 pixel touch target containing a 20x20 icon with a text label below (8px font). Buttons are stacked vertically.

| # | Icon | Label | Action | Equivalent Hotkey |
|---|------|-------|--------|-------------------|
| 1 | Microphone | "Record" | Start/stop recording + transcribe + paste | Ctrl+Alt+R |
| 2 | Chat bubble | "Ask AI" | Start/stop recording + AI answer + paste | Ctrl+Alt+A |
| 3 | Speaker | "Read" | Read clipboard text aloud via TTS | Ctrl+Alt+T (new) |
| 4 | Speaker+Chat | "Ask+Read" | Record question -> AI answer -> read aloud | Ctrl+Alt+Y (new) |

**Button States** (per button):

| State | Appearance | Cursor |
|-------|------------|--------|
| Default | Icon in #BBBBBB on transparent background | Arrow |
| Hover | Icon brightens to #FFFFFF, background fills #333333 | Hand pointer |
| Active (pressed) | Background fills #555555 | Hand pointer |
| Recording (buttons 1, 2) | Icon pulses red, background fills #3A1515 | Hand pointer (click to stop) |
| Processing | Small yellow spinner replaces icon | Arrow (click ignored) |
| Speaking (buttons 3, 4) | Icon pulses blue, background fills #15253A | Hand pointer (click to stop) |
| Disabled | Icon at 30% opacity (#555555) | Arrow |

### 3.4 Positioning

- **Default position**: Right edge of the primary monitor, vertically centered.
- **Offset from edge**: 8 pixels from the right screen boundary.
- **Draggable**: Yes. A 12-pixel-tall drag handle area at the top (three small dots icon, #666666). Dragging repositions the overlay. Position is saved to config.toml on release and restored on next launch.
- **Multi-monitor**: The overlay stays on whichever monitor it was dragged to. If that monitor is disconnected, it resets to the primary monitor's right edge.
- **Snap behavior**: When dragged within 20px of any screen edge, the overlay snaps flush to that edge (with 8px margin).

### 3.5 Show/Hide Behavior

- **Default on launch**: Hidden (overlay_visible = false in config.toml).
- **Toggle hotkey**: **Ctrl+Alt+O** (O for Overlay). Pressing this toggles the overlay visible/hidden with no animation (instant appear/disappear).
- **Tray menu**: New menu item "Show Overlay" / "Hide Overlay" (text changes based on current state). Positioned above "Settings..." in the context menu.
- **Left-click tray icon**: Toggles the overlay (replaces the current no-op default action). This is the most natural discovery mechanism -- users instinctively click tray icons.
- **Close button**: None. The overlay has no title bar, no close button, no minimize. It is hidden via the toggle hotkey, tray menu, or tray click.
- **On app quit**: Overlay is destroyed as part of normal shutdown. Its last position and visibility state are saved to config.toml.

### 3.6 Window Behavior (Non-Negotiable)

These properties are absolute requirements. Violating any of them is a Critical UX defect.

1. **Never steal focus**: The overlay window uses `WS_EX_NOACTIVATE` (Win32) or equivalent. When the user clicks a button, the click is processed but focus immediately returns to the previously focused application. The overlay must NEVER become the foreground window.
2. **Never appear in taskbar**: Uses `WS_EX_TOOLWINDOW` flag. No Alt+Tab entry.
3. **Never appear in task switcher**: No entry in Windows Task View.
4. **Always on top**: Uses `WS_EX_TOPMOST`. The overlay floats above all normal windows.
5. **Click-through when not hovering buttons**: The non-button areas of the overlay (background, drag handle when not dragging) should NOT intercept mouse events for the underlying window. Only the button hit areas and drag handle intercept clicks.
6. **No shadow, no animation**: Appears/disappears instantly. No fade, no slide. Speed is respectful.

### 3.7 Overlay Visual Feedback During States

The overlay provides redundant feedback to the tray icon. When the app is in a non-IDLE state, the overlay reflects it:

| App State | Overlay Behavior |
|-----------|-----------------|
| IDLE | All buttons in default state. Ready for interaction. |
| RECORDING (via button 1) | Button 1 icon pulses red. Buttons 2-4 disabled (greyed). Clicking button 1 stops recording. |
| RECORDING (via button 2) | Button 2 icon pulses red. Buttons 1, 3, 4 disabled. Clicking button 2 stops recording. |
| RECORDING (via button 4) | Button 4 icon pulses red. Buttons 1-3 disabled. Clicking button 4 stops recording. |
| PROCESSING | Active button shows yellow spinner. All other buttons disabled. |
| PASTING | Brief green flash on active button (<200ms). Then IDLE. |
| TTS_PROCESSING | Button 3 or 4 shows yellow spinner. All other buttons disabled. |
| SPEAKING | Button 3 or 4 pulses blue. Buttons 1, 2 remain enabled (can start recording). Clicking the speaking button stops TTS. |

**Key distinction**: During SPEAKING, buttons 1 and 2 (Record, Ask AI) remain ENABLED. This allows the user to start a new recording, which auto-stops the TTS. This is intentional -- the user may hear something in the TTS output that prompts them to dictate a response.

### 3.8 Tooltips

Every button shows a tooltip on hover (500ms delay, standard Windows tooltip):

| Button | Tooltip |
|--------|---------|
| Record | "Record and paste transcription (Ctrl+Alt+R)" |
| Ask AI | "Record and paste AI answer (Ctrl+Alt+A)" |
| Read | "Read clipboard text aloud (Ctrl+Alt+T)" |
| Ask+Read | "Record question, hear AI answer (Ctrl+Alt+Y)" |

During active states, tooltips change:

| State | Tooltip |
|-------|---------|
| Recording (button 1) | "Stop recording (Ctrl+Alt+R)" |
| Recording (button 2) | "Stop recording (Ctrl+Alt+A)" |
| Speaking (button 3) | "Stop reading (Escape)" |
| Speaking (button 4) | "Stop reading (Escape)" |
| Processing | "Processing..." |

---

## 4. TTS Playback

### 4.1 Overview

TTS (Text-to-Speech) converts text into spoken audio and plays it through the user's default audio output device. Two sources of text for TTS:

1. **Clipboard text**: The user clicks "Read" (or presses Ctrl+Alt+T). The current clipboard text is sent to the TTS API and played back.
2. **AI answer**: The user clicks "Ask+Read" (or presses Ctrl+Alt+Y). After the AI generates an answer, instead of pasting it, the answer is spoken aloud.

### 4.2 TTS Pipeline Flow: Clipboard to Speech

```
User clicks "Read" or presses Ctrl+Alt+T
  |
  v
Read clipboard text (CF_UNICODETEXT)
  |
  +--> Empty clipboard? --> Toast: "Clipboard is empty." --> IDLE
  |
  v
State: TTS_PROCESSING
  |-- Tray icon: Yellow
  |-- Overlay button 3: Yellow spinner
  |-- Tooltip: "Preparing speech..."
  |
Send text to TTS API (ElevenLabs)
  |
  +--> API error? --> Toast: "TTS error: [details]" --> Error cue --> IDLE
  |
  v
Receive audio stream
  |
State: SPEAKING
  |-- Tray icon: Blue
  |-- Overlay button 3: Pulses blue
  |-- Audio cue: None (speech is the feedback)
  |
Play audio through default output device
  |
  +--> User presses Escape or clicks button 3? --> Stop playback --> IDLE
  +--> User starts recording (Ctrl+Alt+R)? --> Stop playback --> RECORDING
  |
  v
Playback complete naturally
  |
State: IDLE
  |-- Tray icon: Silver
  |-- Overlay: All buttons default
```

### 4.3 TTS Pipeline Flow: Ask AI + Speak

```
User clicks "Ask+Read" or presses Ctrl+Alt+Y
  |
  v
State: RECORDING
  |-- Tray icon: Red
  |-- Overlay button 4: Pulses red
  |-- Audio cue: Rising tone
  |
User speaks question
  |
User clicks button 4 again or presses Ctrl+Alt+Y
  |
  v
State: PROCESSING
  |-- Tray icon: Yellow
  |-- Overlay button 4: Yellow spinner
  |-- Audio cue: Falling tone
  |
Transcribe audio (Whisper) --> Send as prompt to LLM
  |
  +--> Error? --> Toast + Error cue --> IDLE
  |
  v
Receive AI answer text
  |
  +--> Instead of pasting: send to TTS API
  |
State: TTS_PROCESSING (brief, may merge with PROCESSING visually)
  |
  v
State: SPEAKING
  |-- Tray icon: Blue
  |-- Overlay button 4: Pulses blue
  |-- Answer is spoken aloud
  |-- (The answer text is ALSO placed on the clipboard silently,
  |    so the user can paste it later if desired.)
  |
  v
Playback complete --> IDLE
```

### 4.4 TTS Playback Controls

**Stop TTS**:
- Press **Escape** at any time during SPEAKING state. Audio stops immediately (<100ms). State returns to IDLE.
- Click the active overlay button (3 or 4) during SPEAKING. Same behavior.
- The Escape key is already used for "cancel recording" during RECORDING state. There is no conflict because RECORDING and SPEAKING are mutually exclusive states.

**No pause/resume**: Pause adds complexity (another state, another button state, another mental model). Users who want to re-hear can click "Read" again. The clipboard still contains the text.

**No progress bar**: TTS audio is streamed. The user hears the speech as it arrives. There is no meaningful "percentage complete" to show. A progress bar would add visual noise to the minimal overlay.

**No volume control**: TTS volume follows the system's default audio device volume. Adding a per-app volume slider violates the "invisible tool" principle and adds a configuration surface the user does not need.

### 4.5 TTS + Recording Interaction

This is a critical interaction. The user may be listening to TTS and want to immediately start dictating.

| Scenario | Behavior |
|----------|----------|
| User presses Ctrl+Alt+R during SPEAKING | TTS stops immediately. Rising tone plays. RECORDING state begins. |
| User presses Ctrl+Alt+A during SPEAKING | TTS stops immediately. Rising tone plays. RECORDING state begins (prompt mode). |
| User clicks Record button during SPEAKING | Same as pressing Ctrl+Alt+R. TTS stops, recording starts. |
| User clicks Ask AI button during SPEAKING | Same as pressing Ctrl+Alt+A. TTS stops, recording starts. |
| User presses Ctrl+Alt+T during RECORDING | Ignored. Recording takes priority. User must stop recording first. |
| User presses Ctrl+Alt+Y during RECORDING | Ignored. Recording takes priority. |
| User presses Ctrl+Alt+T during PROCESSING | Ignored. Pipeline takes priority. |

**Rationale**: Recording is the primary workflow. TTS is secondary output. Recording always wins over TTS playback. But TTS never wins over recording or processing.

### 4.6 TTS Streaming vs Buffered

**Recommendation**: Use streaming playback if the TTS API supports it (ElevenLabs does). This means audio begins playing as soon as the first chunk arrives, typically within 500-1500ms. The user perceives near-instant response.

If streaming is not available, buffer the full audio before playing. In this case, TTS_PROCESSING may last 3-10 seconds depending on text length. The yellow spinner provides feedback during this wait.

### 4.7 TTS Text Length Limits

| Text Length | Behavior |
|-------------|----------|
| 0 characters (empty clipboard) | Toast: "Clipboard is empty." No API call. |
| 1-5000 characters | Normal TTS. |
| 5001-10000 characters | Toast: "Long text may take a moment." Then proceed normally. |
| 10001+ characters | Toast: "Text too long for speech (max 10,000 characters). Try selecting a shorter passage." Return to IDLE. No API call. |

The 10,000-character limit is a UX guardrail. Reading 10,000 characters aloud takes roughly 5-7 minutes. Beyond this, the user almost certainly did not intend to read the entire clipboard.

---

## 5. Combined Flows: Ask AI + TTS

### 5.1 Complete Flow Diagram

```
User                          Tool                          System
 |                              |                              |
 |-- Press Ctrl+Alt+Y -------->|                              |
 |   (or click "Ask+Read")     |                              |
 |                              |-- [AUDIO] Rising tone        |
 |                              |-- [ICON] Red microphone      |
 |                              |-- [OVERLAY] Btn 4 pulses red |
 |                              |-- Start mic recording ------>|
 |                              |                              |
 |   (user speaks question)     |   (audio captured)           |
 |                              |                              |
 |-- Press Ctrl+Alt+Y -------->|                              |
 |   (or click btn 4 again)    |                              |
 |                              |-- [AUDIO] Falling tone       |
 |                              |-- [ICON] Yellow microphone   |
 |                              |-- [OVERLAY] Btn 4 spinner    |
 |                              |-- Stop recording             |
 |                              |-- Send audio to Whisper      |
 |                              |   ...waiting...              |
 |                              |<- Transcript received         |
 |                              |-- Send transcript as prompt  |
 |                              |   to LLM (same as Ctrl+Alt+A)|
 |                              |   ...waiting...              |
 |                              |<- AI answer received          |
 |                              |                              |
 |                              |-- Copy answer to clipboard   |
 |                              |   (silently, for later paste) |
 |                              |                              |
 |                              |-- Send answer to TTS API     |
 |                              |   ...waiting (brief)...      |
 |                              |-- [ICON] Blue microphone     |
 |                              |-- [OVERLAY] Btn 4 pulses blue|
 |                              |                              |
 |   (user hears the answer)   |<-- Audio stream plays --------|
 |                              |                              |
 |                              |   Playback completes          |
 |                              |-- [ICON] Silver microphone   |
 |                              |-- [OVERLAY] All buttons idle |
 |                              |-- Return to IDLE             |
```

### 5.2 Key UX Decision: No Auto-Paste for Ask+Read

When the user chooses "Ask+Read" instead of "Ask AI", the answer is **spoken aloud but NOT auto-pasted**. The answer IS placed on the clipboard silently, so the user can Ctrl+V manually if they want.

**Rationale**: If we both speak AND paste, the text appears at the cursor while the user is listening, which may not be what they want. The "Ask+Read" mode is for when the user wants to HEAR the answer (e.g., while their hands are away from the keyboard, or while working in an application where pasting is irrelevant). If they wanted the text pasted, they would use "Ask AI" (Ctrl+Alt+A) instead.

---

## 6. Context Menu Integration

### 6.1 Overview

**User Story**: As a user, I want to right-click on selected text in any application and choose "Read aloud with Voice Paste" to hear it spoken.

### 6.2 UX Recommendation: Defer to v0.7

After careful evaluation, I recommend **deferring context menu integration to v0.7** for the following reasons:

1. **Windows shell context menu registration** requires writing to the registry (HKEY_CLASSES_ROOT) and involves different mechanisms for legacy menus (Windows 10) vs modern menus (Windows 11 IExplorerCommand). This is a significant engineering effort with many failure modes.

2. **The selected text problem**: When the user right-clicks in most applications, the selection is maintained. But Voice Paste would need to read the selected text. The only reliable way to get selected text from an arbitrary application is to simulate Ctrl+C (which modifies the clipboard) or use UI Automation APIs (which are unreliable across apps). Both approaches violate "Respect the workflow."

3. **The overlay already covers this use case**: The user can select text, Ctrl+C to copy, then press Ctrl+Alt+T (or click "Read" on the overlay) to hear it. This is one extra keypress compared to a context menu, but it is reliable and does not require registry modifications.

### 6.3 If Implemented (v0.7 Reference Design)

If the team decides to implement this, here is the reference design:

**Menu Item**: "Read aloud with Voice Paste" with the Voice Paste microphone icon.

**Flow**:
```
User selects text in any application
  |
  v
User right-clicks --> Context menu appears
  |
  v
User clicks "Read aloud with Voice Paste"
  |
  v
Voice Paste reads the selected text via Ctrl+C simulation
  |-- Clipboard backed up first
  |-- Ctrl+C simulated to copy selection
  |-- Clipboard text extracted
  |-- Clipboard restored from backup
  |
  v
Send text to TTS API --> Play audio (same as "Read" button flow)
```

**Problems**:
- Clipboard modification is unavoidable.
- Ctrl+C may not work in all applications (terminals, custom widgets).
- The context menu item appears everywhere, including in contexts where text selection makes no sense (desktop, file explorer).

---

## 7. Settings Panel: TTS Configuration

### 7.1 Overview

A new "Text-to-Speech" section is added to the existing Settings dialog, positioned between "Summarization" and "General".

### 7.2 Layout

```
+-- Text-to-Speech -------------------------------------------+
|                                                              |
|  [x] Enable Text-to-Speech                                  |
|                                                              |
|  Provider:    [ElevenLabs        v]                          |
|                                                              |
|  API Key:     [****************************1234] [Edit]      |
|               Get a key at elevenlabs.io                     |
|                                                              |
|  Voice:       [Rachel - Calm, clear        v]  [Preview]     |
|               American English, female, 28                   |
|                                                              |
|  Speed:       [-- |====O=========| ++ ]   1.0x              |
|               Range: 0.5x to 2.0x                            |
|                                                              |
+--------------------------------------------------------------+
```

### 7.3 Field Specifications

#### Enable Toggle
- **Widget**: ttk.Checkbutton
- **Default**: Unchecked (TTS disabled by default -- requires API key)
- **Behavior**: When unchecked, all other TTS fields are disabled (greyed). The "Read" and "Ask+Read" buttons on the overlay show as permanently disabled. Pressing Ctrl+Alt+T shows a toast: "TTS is not configured. Right-click tray icon > Settings."

#### Provider Dropdown
- **Widget**: ttk.Combobox, state="readonly"
- **Options**: "ElevenLabs" (v0.6, sole option). Future: "Azure", "Google Cloud", "OpenAI TTS".
- **Behavior**: Changing provider clears the voice list and triggers a voice refresh if an API key is present.

#### API Key
- **Widget**: ttk.Entry with masked display
- **Behavior**: Identical to existing OpenAI/OpenRouter key fields. Stored in Windows Credential Manager via keyring. Shows masked value (asterisks + last 4 chars). "Edit" button toggles between viewing and editing. Never loads the full key into the widget.
- **Keyring key**: "elevenlabs_api_key"
- **Validation on save**: Key must not be empty when TTS is enabled. No format validation (ElevenLabs keys have no consistent prefix).

#### Voice Dropdown
- **Widget**: ttk.Combobox, state="readonly"
- **Population**: When the TTS section is first expanded (or when the API key changes), make an API call to list available voices. Show a "Loading voices..." placeholder during the call. If the call fails, show "Could not load voices" and disable the dropdown.
- **Display format**: "Name - Description" (e.g., "Rachel - Calm, clear")
- **Sub-label**: Shows voice metadata below the dropdown: language, gender, approximate age (if available from API).
- **Default**: The first voice in the list. If the user has previously selected a voice, restore that selection.
- **Config storage**: `tts_voice_id` (the API voice ID string, not the display name).

#### Preview Button
- **Widget**: ttk.Button, text="Preview"
- **Behavior**: Sends a short test phrase to the TTS API using the selected voice and plays it. The phrase is "Dies ist eine Vorschau der ausgewaehlten Stimme." (German, matching the primary user base). English fallback: "This is a preview of the selected voice."
- **During playback**: Button text changes to "Stop". Click stops playback.
- **Disabled when**: No API key, no voice selected, or TTS disabled.

#### Speed Slider
- **Widget**: ttk.Scale (horizontal)
- **Range**: 0.5 to 2.0 in increments of 0.1
- **Default**: 1.0
- **Label**: Shows current value next to the slider (e.g., "1.0x")
- **Persistence**: Stored as `tts_speed` in config.toml

### 7.4 Config.toml Additions

```toml
[tts]
# Enable Text-to-Speech (default: false)
enabled = false
# TTS provider (currently only "elevenlabs")
provider = "elevenlabs"
# Voice ID (set via Settings dialog after loading available voices)
voice_id = ""
# Playback speed multiplier (0.5 to 2.0, default: 1.0)
speed = 1.0
```

### 7.5 Keyring Additions

| Service | Key Name | Description |
|---------|----------|-------------|
| VoicePaste | elevenlabs_api_key | ElevenLabs API key for TTS |

---

## 8. Hotkey Recommendations

### 8.1 New Hotkeys

| Hotkey | Action | Mnemonic | State Requirement |
|--------|--------|----------|-------------------|
| **Ctrl+Alt+T** | Read clipboard aloud (TTS) | T = Talk / TTS | IDLE or SPEAKING (toggles) |
| **Ctrl+Alt+Y** | Ask AI + read answer aloud | Y = adjacent to T on keyboard | IDLE (starts recording) or RECORDING (stops, processes, speaks) |
| **Ctrl+Alt+O** | Toggle overlay visibility | O = Overlay | Any state |
| **Escape** | Stop TTS playback (extended) | Already used for cancel recording | SPEAKING state (new), RECORDING state (existing) |

### 8.2 Hotkey Conflict Analysis

| Hotkey | Known Conflicts | Risk |
|--------|----------------|------|
| Ctrl+Alt+T | Opens Terminal in Ubuntu (not relevant on Windows). No known Windows conflicts. | LOW |
| Ctrl+Alt+Y | No known conflicts on Windows. | LOW |
| Ctrl+Alt+O | Outlook "Go to Outbox" in some configurations. | LOW -- only active within Outlook, and Voice Paste hotkeys are global. Acceptable overlap since the actions are contextually different. |
| Escape | Used by many applications. | NONE -- Voice Paste only captures Escape during RECORDING and SPEAKING states, then unregisters. No permanent Escape capture. |

### 8.3 Complete Hotkey Table (v0.6)

| Hotkey | Action | Status |
|--------|--------|--------|
| Ctrl+Alt+R | Record + Transcribe + Paste | Existing (v0.1+) |
| Ctrl+Alt+A | Record + AI Prompt + Paste | Existing (v0.5+) |
| Ctrl+Alt+T | Clipboard Text -> TTS | **New (v0.6)** |
| Ctrl+Alt+Y | Record + AI Prompt -> TTS | **New (v0.6)** |
| Ctrl+Alt+O | Toggle overlay | **New (v0.6)** |
| Escape | Cancel recording / Stop TTS | Extended (v0.6) |

### 8.4 Hotkey Configurability

All new hotkeys are configurable in config.toml under a new `[hotkeys]` section (replacing the existing `[hotkey]` section with backward compatibility):

```toml
[hotkey]
combination = "ctrl+alt+r"
prompt_combination = "ctrl+alt+a"
tts_combination = "ctrl+alt+t"
ask_tts_combination = "ctrl+alt+y"
overlay_combination = "ctrl+alt+o"
```

---

## 9. Audio Cue Specifications

### 9.1 Existing Cues (Unchanged)

| Cue | Frequencies | Duration | Trigger |
|-----|------------|----------|---------|
| Recording start | 440Hz -> 880Hz | 150ms total | Entering RECORDING |
| Recording stop | 880Hz -> 440Hz | 150ms total | Exiting RECORDING to PROCESSING |
| Cancel | 330Hz, 330Hz | 100ms each + 50ms gap | Escape during RECORDING |
| Error | 220Hz | 300ms | Any error state |

### 9.2 New Cues (v0.6)

| Cue | Frequencies | Duration | Trigger | Rationale |
|-----|------------|----------|---------|-----------|
| **TTS start** | None | N/A | Entering SPEAKING | The speech itself IS the feedback. An audio cue before speech would be jarring and feel like a glitch. |
| **TTS stop (user-initiated)** | 660Hz, 440Hz | 75ms each | User presses Escape or clicks Stop during SPEAKING | A quick descending chirp. Shorter and higher-pitched than recording stop. Confirms the stop action. |
| **TTS complete (natural end)** | None | N/A | TTS finishes playing | No cue. The silence after the last word is the natural endpoint. A beep would be startling. |
| **TTS error** | 220Hz | 300ms | TTS API error | Same as existing error cue. Errors share a single consistent sound. |

### 9.3 Audio Cue Decision Rationale

The deliberate absence of start/complete cues for TTS is a conscious design choice. TTS produces audio output -- adding audio cues before or after audio output creates an awkward "beep-speech-beep" sandwich that feels robotic. The only cue that makes sense is the stop cue, because the user performed an explicit action (Escape) and needs confirmation that the action registered.

---

## 10. Edge Cases and Error Handling

### 10.1 Overlay Edge Cases

| Scenario | Behavior |
|----------|----------|
| User drags overlay off-screen | Clamp position so at least 20px of the overlay remains visible on any monitor. If all monitors are removed except primary, reset to primary right-edge center. |
| User clicks overlay button while settings dialog is open | Allowed. Settings dialog and overlay are independent. But if Settings is in front, the overlay button click may be intercepted by Settings (z-order). This is acceptable -- the user simply needs to close or move Settings. |
| Overlay toggle pressed during recording | Overlay appears/disappears. Recording continues unaffected. The overlay merely shows/hides; it does not affect app state. |
| Multiple rapid clicks on Record button (<300ms) | Same debounce as hotkey (300ms). Second click ignored. |
| Screen resolution changes while overlay is visible | Reposition overlay if current position is now off-screen. Clamp to visible area. |
| Windows DPI scaling changes | Overlay redraws at new DPI. Button sizes scale proportionally. Requires handling WM_DPICHANGED. |
| User has "Hide inactive icons" enabled in Windows | Does not affect the overlay (it is not a tray icon). The tray icon behavior is unchanged. |
| Remote Desktop / screen sharing | Overlay appears on the shared screen. This is expected behavior for an always-on-top window. Users sensitive to this can hide the overlay before sharing. |

### 10.2 TTS Edge Cases

| Scenario | Behavior |
|----------|----------|
| Clipboard contains non-text data (image, file) | Toast: "Clipboard does not contain text." Return to IDLE. |
| Clipboard text is only whitespace | Toast: "Clipboard is empty." Return to IDLE. |
| Clipboard text contains markup (HTML, RTF) | Extract plain text only (CF_UNICODETEXT). HTML tags are not spoken. |
| TTS API returns empty audio | Toast: "TTS returned no audio. Try again." Return to IDLE. |
| TTS API rate limit (429) | Toast: "TTS rate limit reached. Wait a moment and try again." Return to IDLE. |
| TTS API key invalid (401) | Toast: "TTS API key is invalid. Check Settings > Text-to-Speech." Return to IDLE. |
| Network timeout during TTS | Toast: "TTS request timed out. Check your internet connection." Return to IDLE. Timeout: 15 seconds. |
| Audio output device disconnected during playback | Playback fails silently at OS level. State returns to IDLE after playback "completes" (the audio subsystem reports end-of-stream). Log warning. |
| User switches audio output device during playback | OS handles this. Audio may briefly interrupt and resume on new device. No action needed from the app. |
| TTS called with text in non-Latin script (Chinese, Arabic) | Pass through to TTS API. ElevenLabs supports multilingual voices. If the selected voice does not support the language, the output quality will be poor but the app should not crash. |
| User triggers "Read" while already SPEAKING | Stop current TTS, read new clipboard content. Treat as: stop then re-trigger. |
| User triggers "Ask+Read" while already SPEAKING | Stop current TTS, begin recording for Ask+Read flow. |

### 10.3 Ask+Read Edge Cases

| Scenario | Behavior |
|----------|----------|
| AI returns empty answer | Toast: "AI returned no answer. Try rephrasing your question." Return to IDLE. Do not send empty text to TTS. |
| AI answer exceeds TTS character limit | Truncate to 10,000 characters. Toast: "Answer truncated to 10,000 characters for speech." Speak the truncated version. Place FULL answer on clipboard. |
| User cancels recording (Escape) during Ask+Read | Same as normal cancel. No TTS, no processing. Return to IDLE. |
| STT succeeds but LLM fails | Toast with LLM error. Return to IDLE. No TTS attempted. |
| STT succeeds, LLM succeeds, but TTS fails | Place answer on clipboard. Toast: "Could not read answer aloud. Answer copied to clipboard." Return to IDLE. The user still gets the answer, just not spoken. |

---

## 11. ASCII Mockups

### 11.1 Overlay Window (Vertical Strip, Default Position)

The overlay sits at the right edge of the screen:

```
                                          Screen edge
                                               |
                                        +------+
                                        | ...  |  <-- Drag handle (12px)
                                        +------+
                                        |      |
                                        | [MIC]|  <-- Record button
                                        | Rec  |
                                        |      |
                                        +------+
                                        |      |
                                        | [AI] |  <-- Ask AI button
                                        | Ask  |
                                        |      |
                                        +------+
                                        |      |
                                        | [SPK]|  <-- Read clipboard button
                                        | Read |
                                        |      |
                                        +------+
                                        |      |
                                        |[A+SP]|  <-- Ask + Read button
                                        |A+Read|
                                        |      |
                                        +------+
                                               |
```

### 11.2 Overlay in Recording State (Button 1 Active)

```
                                        +------+
                                        | ...  |
                                        +------+
                                        |######|
                                        |*[MIC]|  <-- Pulsing red background
                                        | Stop |  <-- Label changes to "Stop"
                                        |######|
                                        +------+
                                        |      |
                                        | [AI] |  <-- Greyed out
                                        |      |
                                        |      |
                                        +------+
                                        |      |
                                        | [SPK]|  <-- Greyed out
                                        |      |
                                        |      |
                                        +------+
                                        |      |
                                        |[A+SP]|  <-- Greyed out
                                        |      |
                                        |      |
                                        +------+
```

### 11.3 Overlay in Speaking State (Button 3 Active)

```
                                        +------+
                                        | ...  |
                                        +------+
                                        |      |
                                        | [MIC]|  <-- ENABLED (can start recording)
                                        | Rec  |
                                        |      |
                                        +------+
                                        |      |
                                        | [AI] |  <-- ENABLED (can start recording)
                                        | Ask  |
                                        |      |
                                        +------+
                                        |~~~~~~|
                                        |~[SPK]|  <-- Pulsing blue background
                                        | Stop |  <-- Label changes to "Stop"
                                        |~~~~~~|
                                        +------+
                                        |      |
                                        |[A+SP]|  <-- Greyed out (another TTS flow)
                                        |      |
                                        |      |
                                        +------+
```

### 11.4 Settings Dialog: TTS Section

```
+-- Text-to-Speech ------------------------------------------------+
|                                                                    |
|  [x] Enable Text-to-Speech                                        |
|                                                                    |
|  Provider:    [ElevenLabs                          v]              |
|                                                                    |
|  API Key:     [****************************1234    ] [Edit]        |
|               Get a key at elevenlabs.io                           |
|                                                                    |
|  Voice:       [Rachel - Calm, clear                v] [Preview]    |
|               American English, female                             |
|                                                                    |
|  Speed:       [---|=====O============|---]   1.0x                  |
|               0.5x                    2.0x                         |
|                                                                    |
+--------------------------------------------------------------------+
```

### 11.5 Settings Dialog: Full Layout with TTS (v0.6)

```
+-- Voice Paste - Settings -----------------------------------------+
|                                                                    |
|  +-- Transcription ---------------------------------------------+ |
|  |  Backend:  [Cloud (OpenAI Whisper API)  v]                    | |
|  |  API Key:  [****1234                    ] [Edit]              | |
|  +---------------------------------------------------------------+ |
|                                                                    |
|  +-- Summarization ---------------------------------------------+ |
|  |  [x] Enable summarization                                    | |
|  |  Provider:  [OpenAI  v]   Model: [gpt-4o-mini        ]       | |
|  |  Base URL:  [https://api.openai.com/v1                ]       | |
|  |  Cleanup Prompt:                            [Reset to Default]| |
|  |  +--------------------------------------------------------+  | |
|  |  | Du bist ein Textbereinigungsassistent...               |  | |
|  |  +--------------------------------------------------------+  | |
|  +---------------------------------------------------------------+ |
|                                                                    |
|  +-- Text-to-Speech -------------------------------------------+  |
|  |  [x] Enable Text-to-Speech                                  | |
|  |  Provider:  [ElevenLabs  v]                                  | |
|  |  API Key:   [****1234              ] [Edit]                  | |
|  |  Voice:     [Rachel - Calm, clear  v] [Preview]              | |
|  |  Speed:     [--|====O=========|--]   1.0x                    | |
|  +---------------------------------------------------------------+ |
|                                                                    |
|  +-- General ---------------------------------------------------+ |
|  |  Summarize:  Ctrl+Alt+R                                      | |
|  |  Ask LLM:    Ctrl+Alt+A                                      | |
|  |  Read TTS:   Ctrl+Alt+T                                      | |
|  |  Ask+Read:   Ctrl+Alt+Y                                      | |
|  |  Overlay:    Ctrl+Alt+O                                      | |
|  |  [x] Play audio cues                                         | |
|  |  [x] Show overlay on startup                                 | |
|  +---------------------------------------------------------------+ |
|                                                                    |
|                                    [Cancel]  [Save]                |
+--------------------------------------------------------------------+
```

### 11.6 Updated System Tray Context Menu (v0.6)

```
Right-click tray icon:
+-------------------------+
| Status: Idle            |  (greyed out)
+-------------------------+
| Show Overlay            |  (or "Hide Overlay")
+-------------------------+
| Settings...             |
+-------------------------+
| Quit                    |
+-------------------------+
```

---

## 12. Technical Constraints and Trade-offs

### 12.1 Overlay Window Technology

**Ideal**: A lightweight, GPU-composited, DPI-aware overlay window with per-pixel alpha transparency, click-through regions, and WS_EX_NOACTIVATE behavior.

**Constraint**: The app currently uses tkinter (for Settings) and pystray (for tray). tkinter can create always-on-top windows (`overrideredirect`, `topmost`) but its support for WS_EX_NOACTIVATE and click-through regions is limited. A tkinter overlay would steal focus on click.

**Options** (ranked by UX quality):

1. **Win32 API via ctypes** (RECOMMENDED): Create a native Win32 window with exact flag control. Full support for WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, layered windows. Requires more code but gives precise control. The Settings dialog already uses ctypes for DwmSetWindowAttribute.

2. **tkinter with workarounds**: Use `overrideredirect(True)` and `wm_attributes('-topmost', True)`. Apply WS_EX_NOACTIVATE via ctypes after window creation. This mostly works but has edge cases with focus management on older Windows versions.

3. **PyQt/PySide**: Full-featured widget toolkit with proper overlay support. But adds a large dependency (50+ MB) which contradicts the app's minimalist philosophy.

**Recommendation**: Option 1 (Win32 via ctypes). The overlay is a simple fixed-layout window with 4 buttons. The rendering can be done with Pillow (already a dependency) composited onto a layered window. Button hit-testing is trivial with known fixed coordinates. This avoids any new dependencies.

### 12.2 TTS Audio Playback

**Constraint**: The app currently uses `winsound.Beep()` for audio cues (no dependency) and `sounddevice` for recording. TTS requires playing arbitrary audio data (PCM/MP3 from the API).

**Options**:

1. **sounddevice** (RECOMMENDED): Already a dependency for recording. Can play PCM audio via `sd.play()`. Supports streaming via callback mode. No new dependency.

2. **pygame.mixer**: Good audio playback support but adds a large dependency.

3. **pyaudio/wave**: Low-level, complex API. More code for the same result.

**Recommendation**: Use `sounddevice` for TTS playback. It is already installed and can handle both PCM and numpy array audio.

### 12.3 TTS API Streaming

**ElevenLabs streaming**: The ElevenLabs API supports streaming audio via chunked HTTP response. Audio arrives as raw PCM or MP3 chunks. For true streaming playback, the app would need to decode chunks in real-time and feed them to the audio output.

**Simpler alternative**: Download the full audio response, then play it. This adds 1-3 seconds of latency before speech begins but is much simpler to implement.

**Recommendation**: Start with the simpler buffered approach (v0.6.0). Add streaming in v0.6.1 if users report that the initial latency is bothersome. The yellow spinner during TTS_PROCESSING provides adequate feedback during the wait.

---

## 13. Open Questions

These require input from the Architect or Product before the spec can be finalized:

| # | Question | Impact | Default Assumption |
|---|----------|--------|--------------------|
| 1 | Should the overlay support horizontal orientation as an option? | Layout complexity doubles | No. Vertical only. |
| 2 | Should "Ask+Read" also paste the answer in addition to speaking it? | Pipeline change, violates Section 5.2 rationale | No. Clipboard only. User can Ctrl+V manually. |
| 3 | Should TTS work offline via a local TTS engine (e.g., piper-tts)? | Major scope expansion. New model management. | No. Cloud-only for v0.6. Local TTS is a v0.8+ feature. |
| 4 | What is the ElevenLabs free tier limit? Should we show remaining credits? | Requires API call to check quota. Adds UI complexity. | No. Users manage their own API quota. Show a clear error if quota exceeded. |
| 5 | Should the overlay be visible during fullscreen applications (games, presentations)? | May require `WS_EX_TOPMOST` with higher z-order or special fullscreen handling. | No. Overlay hides automatically when a fullscreen exclusive app is detected. |
| 6 | Should the overlay auto-hide after a period of inactivity? | Adds a timer, a config option, and a state ("auto-hidden"). | No. The overlay is either shown or hidden, controlled by the user. No auto behavior. |
| 7 | Should there be a "Repeat last TTS" button or hotkey? | Requires caching the last TTS audio. | No for v0.6. Possible future feature. |
| 8 | German-first: should the TTS preview text and default voice be German? | Voice selection, preview text language. | Yes. Default voice should be a German-capable voice. Preview text in German. |

---

## Appendix A: Config.toml Changes Summary (v0.6)

New sections and fields:

```toml
[tts]
enabled = false
provider = "elevenlabs"
voice_id = ""
speed = 1.0

[hotkey]
# Existing:
combination = "ctrl+alt+r"
prompt_combination = "ctrl+alt+a"
# New:
tts_combination = "ctrl+alt+t"
ask_tts_combination = "ctrl+alt+y"
overlay_combination = "ctrl+alt+o"

[overlay]
visible = false
position_x = -1
position_y = -1
```

(`position_x = -1` means "use default position: right edge center")

## Appendix B: New Constants (constants.py)

```python
# TTS configuration
DEFAULT_TTS_PROVIDER = "elevenlabs"
DEFAULT_TTS_SPEED = 1.0
TTS_MAX_TEXT_LENGTH = 10000
TTS_LONG_TEXT_WARNING_THRESHOLD = 5000
TTS_API_TIMEOUT_SECONDS = 15

# New hotkey defaults
DEFAULT_TTS_HOTKEY = "ctrl+alt+t"
DEFAULT_ASK_TTS_HOTKEY = "ctrl+alt+y"
DEFAULT_OVERLAY_HOTKEY = "ctrl+alt+o"

# Overlay configuration
OVERLAY_WIDTH = 52
OVERLAY_HEIGHT = 220
OVERLAY_BUTTON_SIZE = 44
OVERLAY_DRAG_HANDLE_HEIGHT = 12
OVERLAY_SNAP_DISTANCE = 20
OVERLAY_EDGE_MARGIN = 8

# New audio cue
AUDIO_CUE_TTS_STOP_FREQS = (660, 440)
AUDIO_CUE_TTS_STOP_DURATION_MS = 75

# New AppState values
# SPEAKING = "speaking"
# TTS_PROCESSING = "tts_processing"

# Tray icon color for SPEAKING state
SPEAKING_ICON_COLOR = (70, 130, 230)  # Blue
```

## Appendix C: New Keyring Constants

```python
KEYRING_ELEVENLABS_KEY = "elevenlabs_api_key"
```

## Appendix D: State Machine Transitions (Complete v0.6)

```
IDLE
  + Ctrl+Alt+R  --> RECORDING (summary mode)
  + Ctrl+Alt+A  --> RECORDING (prompt mode)
  + Ctrl+Alt+Y  --> RECORDING (ask+tts mode)
  + Ctrl+Alt+T  --> TTS_PROCESSING (clipboard read)
  + Ctrl+Alt+O  --> IDLE (overlay toggles, state unchanged)

RECORDING
  + Same hotkey  --> PROCESSING
  + Escape       --> IDLE (cancel)
  + 5-min limit  --> PROCESSING (auto-stop)

PROCESSING
  + Success (summary/prompt mode) --> PASTING --> IDLE
  + Success (ask+tts mode)        --> TTS_PROCESSING --> SPEAKING
  + Error                          --> IDLE

PASTING
  + Complete     --> IDLE

TTS_PROCESSING
  + TTS audio ready   --> SPEAKING
  + Error              --> IDLE
  + Ctrl+Alt+R/A       --> stop TTS attempt, --> RECORDING

SPEAKING
  + Playback complete  --> IDLE
  + Escape             --> IDLE (stop playback)
  + Click stop button  --> IDLE (stop playback)
  + Ctrl+Alt+R         --> stop playback, --> RECORDING (summary mode)
  + Ctrl+Alt+A         --> stop playback, --> RECORDING (prompt mode)
  + Ctrl+Alt+T         --> stop playback, --> TTS_PROCESSING (new clipboard read)
  + Ctrl+Alt+Y         --> stop playback, --> RECORDING (ask+tts mode)
```
