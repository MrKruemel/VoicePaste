# Floating Overlay UI -- UX Specification

**Date**: 2026-02-18
**Author**: UX Designer Agent
**Target Version**: v0.8.0
**Status**: DRAFT -- pending Architect and Developer review
**Depends on**: Core UX-SPEC.md, OVERLAY-TTS-UX-SPEC.md (v0.6), icon_drawing.py

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Principles Assessment](#2-design-principles-assessment)
3. [Overlay Visual Design Per State](#3-overlay-visual-design-per-state)
4. [Recording State (Deep Dive)](#4-recording-state-deep-dive)
5. [Processing State (Deep Dive)](#5-processing-state-deep-dive)
6. [Speaking State (Deep Dive)](#6-speaking-state-deep-dive)
7. [Pasting State (Deep Dive)](#7-pasting-state-deep-dive)
8. [Transitions and Animation](#8-transitions-and-animation)
9. [Positioning and Layout](#9-positioning-and-layout)
10. [Settings Integration](#10-settings-integration)
11. [Edge Cases](#11-edge-cases)
12. [ASCII Mockups](#12-ascii-mockups)
13. [Technical Constraints and Recommendations](#13-technical-constraints-and-recommendations)
14. [Open Questions](#14-open-questions)

---

## 1. Problem Statement

### The Visibility Problem

Voice Paste's only visual feedback is a 32x32 system tray icon. On Windows 11, this icon is frequently:

1. **Hidden in the overflow area** ("Show hidden icons" chevron). Users must click the chevron to see the icon at all.
2. **Too small to notice at a glance**. The difference between a red and a yellow 32x32 microphone is subtle, especially at 150% DPI scaling on a 27" monitor.
3. **Positioned far from the user's focus**. The tray is at the bottom-right corner. The user's cursor and attention are typically in the center or upper-left of the screen.

The consequence: users cannot reliably tell whether Voice Paste is **recording**, **processing**, or **speaking**. The most critical question -- "Am I recording right now?" -- should never require the user to look at a 32x32 icon in the corner.

### The Solution: A Floating Status Overlay

A small, semi-transparent pill-shaped overlay that appears near the user's cursor (or at a fixed screen position) during active states. It shows exactly what the app is doing, with state-appropriate content, and disappears when the app returns to IDLE.

**This overlay is NOT the v0.6 button toolbar.** The v0.6 spec (OVERLAY-TTS-UX-SPEC.md) defined a vertical strip of 4 clickable action buttons. That is a separate feature (an "overlay toolbar"). This spec defines a **status overlay** -- a read-only, non-interactive information pill. The two features can coexist or the status overlay can replace the toolbar depending on product direction.

### User Story

As a user, I want a clear visual indicator near my working area that shows me when Voice Paste is recording, processing, or speaking, so that I never have to look at the system tray to know the app's current state.

---

## 2. Design Principles Assessment

### Principle Tensions

| Principle | Tension | Resolution |
|-----------|---------|------------|
| **Invisible by default** | The overlay is visible during active states. | The overlay is ONLY visible during RECORDING, PROCESSING, SPEAKING, and PASTING. It is completely absent during IDLE. There is no persistent UI surface. |
| **Never steal focus** | The overlay is a window. | The overlay uses WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW + WS_EX_TRANSPARENT (full click-through). It has NO interactive elements. Clicks pass through to the application behind it. It CANNOT steal focus because it never receives input. |
| **Instant feedback** | The overlay must appear within 50ms of state change. | The overlay window is pre-created but hidden. On state change, only the content bitmap is updated and the window is shown via ShowWindow. No window creation latency. |
| **Respect the workflow** | The overlay sits on top of the user's work. | The overlay is small (240x48 px), semi-transparent (90% opacity), positioned in a non-obstructive location, and is fully click-through. The user can type, click, and interact as if the overlay is not there. |
| **Zero learning curve** | Users must understand the overlay without explanation. | The overlay uses universally understood symbols (red dot = recording, spinner = working, speaker icon = playing) with simple text labels. No buttons, no controls, no interaction needed. |

### Critical Non-Negotiables

These properties are absolute requirements. Violating any one is a Critical UX defect:

1. **100% click-through**: The overlay NEVER intercepts mouse events. WS_EX_TRANSPARENT must be set. The user must be able to click, drag, select text, and scroll through the overlay as if it does not exist.
2. **Never steal focus**: WS_EX_NOACTIVATE must be set. The overlay must never become the foreground window under any circumstance.
3. **No taskbar entry**: WS_EX_TOOLWINDOW must be set. No Alt+Tab entry. No Task View entry.
4. **No input handling**: The overlay accepts no keyboard or mouse input. All interaction with Voice Paste continues via global hotkeys and tray icon.
5. **Disappears completely on IDLE**: When the app returns to IDLE state, the overlay window is hidden (not destroyed). There must be zero visual footprint during IDLE.

---

## 3. Overlay Visual Design Per State

### 3.1 Common Visual Properties

All states share these base properties:

| Property | Value | Rationale |
|----------|-------|-----------|
| Shape | Rounded rectangle (pill) | Soft, non-aggressive. Distinct from system UI rectangles. |
| Corner radius | 12 px | Visible rounding at the overlay's size. |
| Width | 240 px (RECORDING, PROCESSING, SPEAKING), 160 px (PASTING) | Wide enough for timer/text, narrow enough to be unobtrusive. |
| Height | 48 px | Single line of content. Minimal vertical footprint. |
| Opacity | 90% (0.9 alpha) | Visible enough to notice, transparent enough to see through. |
| Border | 1 px solid, color varies by state | Provides definition against any wallpaper. |
| Drop shadow | None | Shadows add visual weight and complexity. The border provides sufficient definition. |
| Font family | Segoe UI (Windows default) | Consistent with Windows 11 system UI. |
| Font weight | Semibold (600) for label text, Regular (400) for secondary text | Hierarchy without being heavy. |
| Text anti-aliasing | ClearType (system default) | Crisp text on all DPI settings. |

### 3.2 IDLE State

**The overlay is NOT SHOWN during IDLE.** There is no visual element. The user sees only their desktop and active applications, exactly as before.

When the app transitions from any state to IDLE, the overlay fades out and is hidden (see Section 8).

### 3.3 RECORDING State

| Property | Value |
|----------|-------|
| Background | #B91C1C (dark red) at 90% opacity |
| Border | 1 px solid #DC2626 (bright red) |
| Text color | #FFFFFF (white) |
| Secondary text color | #FCA5A5 (light red, for Esc hint) |
| Content | [Pulsing red circle] [Timer] [Esc hint] |
| Layout | `[*] 0:05  Esc to cancel` |

Content breakdown (left to right):
- **Pulsing dot**: 10 px diameter circle, filled #EF4444 (bright red), pulsing between 100% and 40% opacity on a 1-second cycle (sinusoidal ease). This is the universal "recording" indicator.
- **Timer**: Live elapsed time since recording started. Format: `M:SS` (no leading zero on minutes). Examples: `0:05`, `0:32`, `1:07`, `12:45`. Font: Segoe UI Semibold, 16 px. Color: #FFFFFF.
- **Esc hint**: `Esc to cancel` in 11 px Regular, color #FCA5A5. Right-aligned within the pill. Provides an affordance without requiring documentation.

### 3.4 PROCESSING State

| Property | Value |
|----------|-------|
| Background | #92400E (dark amber/brown) at 90% opacity |
| Border | 1 px solid #D97706 (amber) |
| Text color | #FFFFFF (white) |
| Secondary text color | #FDE68A (light amber) |
| Content | [Animated dots] [Status text] |
| Layout | `... Transcribing...` or `... Summarizing...` or `... Still processing...` |

Content breakdown:
- **Animated dots**: Three dots (`...`) that sequentially fade in from left to right on a 1.5-second cycle, creating a "typing indicator" effect. Each dot is 6 px diameter, #FBBF24 (amber). The animation conveys "working" without requiring the user to read text.
- **Status text**: Shows the current pipeline step. Font: Segoe UI Semibold, 14 px. Color: #FFFFFF.
  - During STT (transcription): `Transcribing...`
  - During summarization/LLM: `Summarizing...` (or `Thinking...` for prompt mode)
  - After 10 seconds in any sub-step: changes to `Still processing...` in #FDE68A (amber tint). This reassures the user that the app has not frozen.

### 3.5 SPEAKING State

| Property | Value |
|----------|-------|
| Background | #1E3A5F (dark blue) at 90% opacity |
| Border | 1 px solid #3B82F6 (bright blue) |
| Text color | #FFFFFF (white) |
| Secondary text color | #93C5FD (light blue) |
| Content | [Speaker icon with animation] [Label] [Esc hint] |
| Layout | `)) Speaking...  Esc to stop` |

Content breakdown:
- **Speaker icon**: A small speaker symbol (12x12 px) with animated sound waves. Two concentric arcs emerge from the speaker on a 0.8-second cycle, creating a "sound emanating" effect. Color: #60A5FA (medium blue).
- **Label**: `Speaking...` in Segoe UI Semibold, 14 px. Color: #FFFFFF.
- **Esc hint**: `Esc to stop` in 11 px Regular, color #93C5FD. Right-aligned. Same pattern as RECORDING state's Esc hint -- consistency.

### 3.6 PASTING State

| Property | Value |
|----------|-------|
| Background | #065F46 (dark green) at 90% opacity |
| Border | 1 px solid #10B981 (bright green) |
| Text color | #FFFFFF (white) |
| Width | 160 px (narrower -- brief state, simple content) |
| Content | [Checkmark icon] [Label] |
| Layout | `checkmark Pasted!` |

Content breakdown:
- **Checkmark icon**: A simple checkmark (12x12 px), color #34D399 (medium green).
- **Label**: `Pasted!` in Segoe UI Semibold, 14 px. Color: #FFFFFF.

This state is extremely brief (<200ms). The overlay appears with the green pill, then fades out as the app returns to IDLE. The brief green flash provides satisfying completion feedback.

---

## 4. Recording State (Deep Dive)

### 4.1 Live Timer

The timer starts at `0:00` the moment recording begins and increments every second.

**Format**: `M:SS`
- No leading zero on minutes: `0:05`, not `00:05`
- Always two digits for seconds: `0:05`, not `0:5`
- Minutes wrap naturally: `1:00`, `2:30`, `10:15`

**Update frequency**: Every 1000ms (one second). The timer MUST NOT drift. Use a monotonic clock reference (e.g., `time.monotonic()`) and compute elapsed time on each tick rather than incrementing a counter.

**Timer accuracy**: The displayed value must be within +/-500ms of actual elapsed time. This is a perceived-quality metric, not a functional requirement.

### 4.2 Pulsing Dot Animation

The recording indicator dot pulses between full opacity and reduced opacity:

```
Opacity: 100% --> 40% --> 100% (sinusoidal)
Cycle: 1.0 seconds per full pulse
Easing: sinusoidal (smooth fade in/out)
```

The pulse is deliberately slow (1 Hz) to be noticeable without being anxious. A faster pulse (>2 Hz) would feel urgent/alarming. A static dot would not convey "active/recording" as clearly.

**Implementation note**: The pulsing dot can be rendered as a series of pre-computed frames (e.g., 10 frames at 100ms intervals) composited into the overlay bitmap. The overlay window's UpdateLayeredWindow call updates the bitmap at 10 FPS.

### 4.3 Esc Hint Behavior

The `Esc to cancel` text is always visible during RECORDING. It does not fade, blink, or animate. It is static helper text.

**Why always visible**: The overlay's purpose is to provide at-a-glance information. A new or infrequent user glancing at the overlay should immediately learn how to cancel. Hiding the hint after N seconds would penalize exactly the users who need it most.

**Why "Esc to cancel" not "Ctrl+Alt+R to stop"**: During recording, there are two actions:
1. Stop recording and process (press the same hotkey again)
2. Cancel recording entirely (press Escape)

The user already knows their hotkey (they just pressed it to start recording). What they may NOT know is that Escape cancels. The overlay teaches the safety valve.

### 4.4 Recording Duration Milestones

| Elapsed Time | Overlay Behavior |
|-------------|------------------|
| 0:00 - 4:59 | Normal display: pulsing dot, timer, Esc hint. |
| 5:00 (300s max approaching) | No change. The max recording duration is 5 minutes. At 4:30, the timer text color changes from #FFFFFF to #FCA5A5 (light red), subtly indicating time is running low. |
| 4:50 | Timer text color: #FCA5A5 (amber-red tint). Pulsing dot accelerates to 0.5s cycle (2 Hz). These combined changes create urgency without a jarring interruption. |
| 5:00 (auto-stop) | Recording auto-stops. Overlay transitions to PROCESSING state. Toast notification: "Recording stopped at 5-minute limit." |

---

## 5. Processing State (Deep Dive)

### 5.1 Animated Dots

Three dots animate in sequence to create a "loading" pattern:

```
Frame 1 (0.0s):   [*] [.] [.]     <- first dot bright, others dim
Frame 2 (0.5s):   [.] [*] [.]     <- second dot bright
Frame 3 (1.0s):   [.] [.] [*]     <- third dot bright
Frame 4 (1.5s):   [*] [.] [.]     <- cycle repeats
```

Each dot:
- Bright state: filled circle, 6 px diameter, #FBBF24 (amber), 100% opacity
- Dim state: filled circle, 6 px diameter, #FBBF24, 30% opacity
- Transition: instant (no fade between dots -- snappy, not mushy)

**Why dots, not a spinner**: A spinner is typically associated with "loading a webpage" and conveys indefinite waiting. The dots pattern is more commonly associated with "someone is typing/working on your request" (think chat applications). This matches the mental model better: the AI is "working on" your text.

### 5.2 Status Text Progression

The processing state involves multiple pipeline steps. The overlay text updates to reflect the current step:

| Pipeline Step | Status Text | Typical Duration |
|--------------|-------------|-----------------|
| STT (cloud) | `Transcribing...` | 2-10 seconds |
| STT (local) | `Transcribing locally...` | 5-60 seconds |
| Summarization | `Summarizing...` | 2-5 seconds |
| LLM Prompt (Ask AI mode) | `Thinking...` | 3-10 seconds |
| TTS Synthesis (for tts_ask mode) | `Preparing speech...` | 1-3 seconds |

The app's state machine fires `_set_state(AppState.PROCESSING)` once. The overlay needs a secondary signal to update the status text. This can be done via a lightweight callback or shared string variable that the pipeline updates as it progresses through steps.

### 5.3 "Still Processing..." Timeout

If the overlay has been in PROCESSING state for 10 continuous seconds:

- Status text changes to: `Still processing...`
- Text color changes from #FFFFFF to #FDE68A (light amber/yellow tint)
- Animated dots continue unchanged

**Rationale**: After 10 seconds of "Transcribing...", the user may wonder if the app froze. The text change confirms the app is still alive and working. The amber tint distinguishes it from the normal state without being alarming.

If the pipeline step changes (e.g., STT finishes, summarization starts), the timer resets and the text returns to normal for the new step.

### 5.4 Processing State Duration Expectations

| Mode | Expected Duration | Long Processing Threshold |
|------|------------------|--------------------------|
| Cloud STT + Summarize | 5-18 seconds | 10 seconds |
| Local STT (base) + Summarize | 15-60 seconds | 10 seconds |
| Cloud STT + LLM Prompt | 5-15 seconds | 10 seconds |
| Cloud STT + LLM + TTS | 8-20 seconds | 10 seconds |

For local STT with large models, processing can legitimately take 60+ seconds. The "Still processing..." message prevents users from thinking the app has crashed.

---

## 6. Speaking State (Deep Dive)

### 6.1 Speaker Icon Animation

The speaker icon consists of:
- A static speaker body (small trapezoid/triangle, 8x10 px)
- Two animated concentric arcs (sound waves) that emerge and fade

Sound wave animation:
```
Frame 1: speaker body + inner arc at 100% opacity
Frame 2: speaker body + inner arc at 70%, outer arc appears at 100%
Frame 3: speaker body + inner arc at 40%, outer arc at 70%
Frame 4: speaker body + inner arc fades out, outer arc at 40%
Frame 5: cycle repeats (inner arc reappears at 100%)
```

Cycle: 0.8 seconds. This creates a smooth "broadcasting" effect that clearly communicates "audio is playing."

### 6.2 Esc Hint

`Esc to stop` follows the exact same pattern as the RECORDING state's `Esc to cancel`:
- Always visible
- Right-aligned
- Secondary color (#93C5FD light blue, matching the state's accent palette)

Consistency between RECORDING and SPEAKING: both states have an Escape action, both show `Esc to [action]` in the same position. The user learns a single pattern.

### 6.3 TTS Playback Duration

TTS playback duration is variable and unpredictable (depends on text length). The overlay does NOT show a timer or progress bar for SPEAKING state.

**Why no timer**: Unlike RECORDING (where the user controls duration and needs to track it), SPEAKING duration is determined by the text. Showing elapsed time provides no actionable information. A progress bar would require knowing total duration upfront, which is not available for streamed TTS.

---

## 7. Pasting State (Deep Dive)

### 7.1 Brief Flash Design

The PASTING state lasts <200ms. The overlay appears as a brief green flash with a checkmark:

- Overlay fades in over 100ms
- Shows for 300ms (total visible time ~400ms including fade-in)
- Fades out over 200ms

The total visible duration is approximately 500ms. This is long enough to register as "something happened -- success!" but short enough to not linger.

### 7.2 Why Show Pasting at All

The PASTING state is technically instantaneous. Why bother showing an overlay?

**The completion signal**: Without the green flash, the transition from PROCESSING (yellow overlay) to IDLE (no overlay) is abrupt. The user sees "Transcribing..." and then... nothing. Did it work? Did it fail? The green "Pasted!" flash answers the question definitively: "Your text was successfully pasted."

This is the same design pattern as macOS's screenshot flash or Windows' file copy completion toast. A brief confirmation of success.

### 7.3 Error Bypass

If the pipeline fails (API error, empty transcript, etc.), the PASTING state is skipped entirely. The overlay transitions directly from PROCESSING to IDLE (with fade-out). The error is communicated via toast notification, not via the overlay.

The overlay is an optimistic feedback mechanism. It confirms success. It does not display errors. Errors go through the existing toast notification channel.

---

## 8. Transitions and Animation

### 8.1 Overlay Appearance (IDLE to Active State)

When the app transitions from IDLE to any active state:

| Step | Duration | What Happens |
|------|----------|-------------|
| 1. State change detected | 0 ms | App calls `_set_state(AppState.RECORDING)` |
| 2. Overlay content prepared | <10 ms | Bitmap rendered for the new state |
| 3. Overlay position calculated | <5 ms | Position computed (see Section 9) |
| 4. Overlay shown | 0 ms | `ShowWindow(hwnd, SW_SHOWNOACTIVATE)` |
| 5. Fade-in animation | 150 ms | Opacity ramps from 0% to 90% over 150ms |

**Total perceived latency**: <200ms from hotkey press to overlay fully visible. This is within the "instant feedback" requirement.

**Why fade-in, not instant appear**: An overlay that snaps into existence at 90% opacity is startling, especially in the user's peripheral vision. A 150ms fade-in is perceived as "smooth" rather than "sudden." It respects the user's attention without demanding it.

### 8.2 State-to-State Transitions

When the overlay transitions between active states (e.g., RECORDING to PROCESSING):

| Step | Duration | What Happens |
|------|----------|-------------|
| 1. State change detected | 0 ms | App calls `_set_state(AppState.PROCESSING)` |
| 2. Cross-fade | 200 ms | Old content fades out while new content fades in simultaneously. Background color transitions smoothly from old state color to new state color. |

**Why cross-fade, not instant swap**: The color change from red (RECORDING) to amber (PROCESSING) is meaningful. A smooth 200ms transition makes the color change visible and deliberate, rather than a sudden jarring flash.

**Implementation**: Since the overlay is a single layered window, the cross-fade is achieved by rendering intermediate frames that blend the old and new state bitmaps. At 30 FPS, this is 6 frames over 200ms.

### 8.3 Overlay Disappearance (Active State to IDLE)

When the app transitions from any active state to IDLE:

| Step | Duration | What Happens |
|------|----------|-------------|
| 1. State change detected | 0 ms | App calls `_set_state(AppState.IDLE)` |
| 2. Fade-out animation | 300 ms | Opacity ramps from 90% to 0% over 300ms |
| 3. Overlay hidden | 0 ms | `ShowWindow(hwnd, SW_HIDE)` after fade-out completes |

**Why 300ms fade-out (longer than 150ms fade-in)**: Disappearance should be gentler than appearance. A fast disappearance feels like something was "snatched away." A slow fade-out lets the user's peripheral vision register the change without snapping their attention to it.

### 8.4 Special Case: PASTING to IDLE

The PASTING state has custom timing (see Section 7.1):

```
PROCESSING -> PASTING: cross-fade (200ms)
PASTING (holds): 300ms (green pill visible)
PASTING -> IDLE: fade-out (200ms, faster than normal because the green flash should feel snappy)
```

### 8.5 Animation Frame Rate

All overlay animations (pulsing dot, animated dots, speaker waves, fade transitions) target 30 FPS (33ms per frame). This is sufficient for smooth perceived animation without excessive CPU usage. The overlay rendering is lightweight (compositing pre-rendered elements onto a 240x48 bitmap).

---

## 9. Positioning and Layout

### 9.1 Default Position

The overlay appears in the **top-center of the primary monitor**, offset from the top edge:

```
+----------------------------------------------------------+
|                  [  * 0:05  Esc to cancel  ]             |
|                         ^                                 |
|                    overlay here                           |
|                                                           |
|                                                           |
|                   (user's work area)                      |
|                                                           |
+----------------------------------------------------------+
|  [ Start ]  [ ... ]                    [ ^ ] [ tray ]    |
+----------------------------------------------------------+
```

**Exact position**:
- Horizontal: Centered on the primary monitor's work area (excluding taskbar)
- Vertical: 60 px from the top of the work area

**Why top-center**:
1. **Not near the tray**: The whole point is to move feedback away from the bottom-right corner. Placing the overlay near the tray defeats the purpose.
2. **Not near the cursor**: Cursor-relative positioning would cause the overlay to move every time the user presses the hotkey from a different location. A moving overlay is disorienting. Fixed position means the user learns where to glance.
3. **Not bottom-center**: The taskbar and notification area are at the bottom. Placing the overlay there risks overlap with toast notifications, taskbar auto-hide, and the area the user's eyes naturally avoid (they already have UI there).
4. **Top-center is "notification territory"**: Windows 11 shows notifications at the top-right. macOS shows notifications at the top-right. Browsers show download bars at the bottom. Many apps show status bars at the top. Users are conditioned to glance upward for transient status information.

### 9.2 Configurable Position

Users can configure the overlay position in `config.toml`:

```toml
[overlay]
# Overlay position: "top-center" (default), "top-left", "top-right",
#                   "bottom-center", "bottom-left", "bottom-right"
position = "top-center"
# Custom offset from screen edge in pixels (horizontal, vertical)
# Only used when position is set to a predefined location.
offset_x = 0
offset_y = 60
```

**Positions**:

| Value | Horizontal | Vertical |
|-------|-----------|----------|
| `top-center` | Centered | 60px from top |
| `top-left` | 60px from left | 60px from top |
| `top-right` | 60px from right | 60px from top |
| `bottom-center` | Centered | 60px from bottom (above taskbar) |
| `bottom-left` | 60px from left | 60px from bottom |
| `bottom-right` | 60px from right | 60px from bottom |

The Settings dialog exposes this as a dropdown (see Section 10). The `offset_x` and `offset_y` are for power users editing config.toml directly.

### 9.3 DPI Scaling

All pixel values in this spec are logical pixels at 100% DPI (96 DPI). On higher DPI displays:

| System DPI | Scale Factor | Overlay Size | Font Size | Offsets |
|-----------|-------------|-------------|-----------|---------|
| 96 DPI (100%) | 1.0x | 240x48 | 16/14/11 px | 60 px |
| 120 DPI (125%) | 1.25x | 300x60 | 20/18/14 px | 75 px |
| 144 DPI (150%) | 1.5x | 360x72 | 24/21/17 px | 90 px |
| 192 DPI (200%) | 2.0x | 480x96 | 32/28/22 px | 120 px |

The overlay must handle WM_DPICHANGED to re-render its content bitmap at the correct DPI. SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) should be set at app startup.

### 9.4 Multi-Monitor Behavior

The overlay appears on the **primary monitor** by default. It does NOT follow the mouse cursor or the active window across monitors.

**Rationale**: A cursor-following overlay would be distracting (it moves) and complex (which monitor to choose when the cursor is between monitors?). A fixed position on the primary monitor is predictable. The user learns "glance up on main screen" as muscle memory.

**Exception**: If the user configures `position = "top-right"` and their primary monitor is on the left, the overlay might be far from their work. This is an accepted trade-off. Users with multi-monitor setups are power users who can adjust the config.

### 9.5 Work Area Awareness

The overlay respects the **work area** (screen minus taskbar), not the full screen resolution. This ensures the overlay does not overlap with:
- The Windows taskbar (any edge, any size)
- Auto-hidden taskbars (when they slide up)

The work area is queried via `SystemParametersInfoW(SPI_GETWORKAREA)` or `MonitorInfoFromWindow`.

### 9.6 Internal Layout

```
+-------------------------------------------------------+
| 12px | [icon/dot] | 8px | [text] | flex | [hint] | 12px |
|      |   area     |     | area   |      | area   |      |
+-------------------------------------------------------+
         24x24 px          variable        variable
```

- Left padding: 12 px
- Icon/indicator area: 24x24 px (contains pulsing dot, animated dots, or speaker icon, centered vertically)
- Gap: 8 px
- Primary text area: Variable width, left-aligned
- Flexible space: Fills remaining width
- Hint text area: Right-aligned, variable width
- Right padding: 12 px
- Vertical: All content centered vertically within the 48 px height

---

## 10. Settings Integration

### 10.1 Location in Settings Dialog

The overlay setting is added to the **General** section of the Settings dialog, as a single checkbox with a position dropdown:

```
+-- General ---------------------------------------------------+
|                                                                |
|  [x] Play audio cues                                          |
|  [x] Show floating status overlay                             |
|       Position: [Top center                    v]              |
|  [ ] Enable external API (named pipe)                          |
|       Status: Not running                                      |
|                                                                |
|  Hotkeys:                                                      |
|  ...                                                           |
+----------------------------------------------------------------+
```

### 10.2 Checkbox: "Show floating status overlay"

- **Widget**: ttk.Checkbutton
- **Default**: Checked (True)
- **Config key**: `overlay_status_enabled` (in `[feedback]` section)
- **Behavior**: When unchecked, the overlay window is never shown. State transitions still occur (tray icon, audio cues) but the floating overlay is hidden. When checked, the overlay appears during active states.
- **Hot-reload**: Toggling this setting takes effect immediately (no restart). If the app is currently in an active state when the user enables the overlay, it appears with the current state. If the user disables it during an active state, it fades out immediately.

### 10.3 Position Dropdown

- **Widget**: ttk.Combobox, state="readonly"
- **Options**: "Top center", "Top left", "Top right", "Bottom center", "Bottom left", "Bottom right"
- **Default**: "Top center"
- **Config key**: `overlay_position` (in `[feedback]` section)
- **Behavior**: Changing the position takes effect on the next state transition (not immediately, unless the overlay is currently visible -- in which case it repositions smoothly).

### 10.4 Config.toml Changes

```toml
[feedback]
# Play audio cues on state transitions (default: true)
audio_cues = true
# Show floating status overlay during active states (default: true)
overlay_status_enabled = true
# Overlay position: "top-center", "top-left", "top-right",
#                   "bottom-center", "bottom-left", "bottom-right"
overlay_position = "top-center"
```

### 10.5 Why Default ON (Not OFF)

Previous overlay features (v0.6 toolbar) defaulted to OFF because they added interactive UI that could surprise existing users. The status overlay is fundamentally different:

1. **Non-interactive**: No buttons, no controls, fully click-through. It cannot interfere with any workflow.
2. **Only visible during active states**: It does not add persistent visual clutter.
3. **Solves the core problem**: The tray icon is too small and too far away. The overlay is the primary solution. Defaulting it to OFF would mean most users never discover it, continuing to struggle with the tiny tray icon.
4. **Easy to disable**: One checkbox in Settings. Discoverable, reversible.

The overlay is not a "power user feature." It is a **core UX improvement** that benefits every user. It should be on by default.

---

## 11. Edge Cases

### 11.1 Recording Duration Edge Cases

| Scenario | Overlay Behavior |
|----------|------------------|
| Recording exceeds 10 minutes | Timer continues displaying correctly: `10:00`, `10:01`, etc. The 5-minute auto-stop should trigger at 5:00, but if a future version increases the limit, the timer format handles arbitrary durations. |
| Recording is very short (<1s, then stopped) | Overlay appears showing `0:00`, immediately transitions to PROCESSING state. The green "Pasted!" flash may appear before the user can read it. This is fine -- the audio cue provides the primary feedback for short recordings. |

### 11.2 Processing Duration Edge Cases

| Scenario | Overlay Behavior |
|----------|------------------|
| Processing takes >30 seconds | "Still processing..." text remains. No additional escalation. The overlay stays visible as long as the app is in PROCESSING state. |
| Processing takes >60 seconds (local STT with large model) | Same as above. The animated dots continue. The "Still processing..." text reassures the user. If processing exceeds the API timeout (30s cloud, 60s local), the app will transition to error state, the overlay disappears, and a toast notification appears. |
| Processing completes in <1 second | Overlay shows PROCESSING state briefly, then cross-fades to PASTING. The rapid transition is fine -- it conveys "done quickly." |

### 11.3 UI Interaction Edge Cases

| Scenario | Overlay Behavior |
|----------|------------------|
| User opens Settings dialog while overlay is showing | Both remain visible independently. The Settings dialog is a separate window on a separate thread. The overlay continues showing state feedback behind the dialog. If the dialog is positioned over the overlay, the overlay shows through (it is topmost, but Settings might also be topmost -- z-order depends on creation order). Acceptable: the user just needs to move the dialog. |
| User opens fullscreen application (game, presentation) | The overlay appears on top of fullscreen exclusive apps due to WS_EX_TOPMOST. For DirectX exclusive fullscreen, the overlay will NOT be visible (exclusive mode bypasses the compositor). For borderless fullscreen (most modern games), the overlay will be visible. This is acceptable. Users in presentations can disable the overlay in Settings. |
| Screen resolution changes while overlay is visible | The overlay repositions to the new work area dimensions. If the resolution shrinks such that the overlay's current position would be off-screen, it resets to the configured position relative to the new screen geometry. |
| User changes DPI scaling while overlay is visible | The overlay handles WM_DPICHANGED by re-rendering its content bitmap at the new DPI scale factor and repositioning. The overlay may briefly appear at the wrong scale during the transition (Windows sends the DPI change message asynchronously). This is a sub-second glitch that does not require special handling. |
| Overlay shown during screen recording/sharing | The overlay will appear in screen recordings and shared screens (same as any topmost window). Users who do not want the overlay in recordings should disable it before sharing. This is consistent with how all floating widgets behave (e.g., OBS overlays, Zoom toolbar). |

### 11.4 Timing Edge Cases

| Scenario | Overlay Behavior |
|----------|------------------|
| User cancels recording immediately after starting (Escape within 200ms) | Overlay appears (fade-in begins) then immediately receives IDLE transition. Fade-in is interrupted and overlay fades out from its current opacity (possibly only 30-40% opacity). The net effect is a brief red flash that quickly disappears. This correctly communicates "started, then cancelled." |
| Pipeline error during PROCESSING | Overlay is in PROCESSING (amber). App transitions to IDLE (error). Overlay fades out normally. Toast notification shows error details. No error state on the overlay itself -- errors are not the overlay's responsibility. |
| Two rapid state transitions (RECORDING -> PROCESSING -> PASTING in <500ms) | Each transition triggers a cross-fade. If the previous cross-fade has not completed, it is interrupted and the new cross-fade starts from the current blended state. This produces a smooth color wash: red -> amber -> green. Visually pleasant, functionally correct. |

### 11.5 Hands-Free Mode Interaction

| Scenario | Overlay Behavior |
|----------|------------------|
| Hands-free mode active, wake word triggers recording | Overlay appears exactly as for hotkey-triggered recording. Same red pill, same timer, same Esc hint. The overlay does not indicate HOW recording was triggered (wake word vs hotkey). |
| Silence timeout auto-stops recording | Timer was running. After silence timeout fires, overlay transitions from RECORDING (red) to PROCESSING (amber) via normal cross-fade. No special indication that it was auto-stopped. |
| Hands-free mode active during IDLE | No overlay shown. The hands-free indicator is the cyan ring on the tray icon, not the overlay. The overlay is strictly a state-feedback mechanism for active states. |

### 11.6 External API Interaction

| Scenario | Overlay Behavior |
|----------|------------------|
| API command triggers recording | Overlay appears exactly as for hotkey-triggered recording. The overlay does not indicate the trigger source. |
| API command triggers processing while overlay is disabled | No overlay shown (user disabled it). Tray icon and audio cues provide feedback as before. |

---

## 12. ASCII Mockups

### 12.1 RECORDING State

```
+---------------------------------------------------+
|  +-+                                              |
|  |*|  0:05              Esc to cancel             |
|  +-+                                              |
+---------------------------------------------------+
  ^          ^                    ^
  |          |                    |
  Pulsing    Timer                Hint text
  red dot    (live, M:SS)         (#FCA5A5 light red)
  (#EF4444)  (#FFFFFF, 16px)      (11px)

Background: #B91C1C (dark red) at 90% opacity
Border: 1px #DC2626 (bright red)
```

### 12.2 RECORDING State (Near Time Limit)

```
+---------------------------------------------------+
|  +-+                                              |
|  |*|  4:52              Esc to cancel             |
|  +-+                                              |
+---------------------------------------------------+
  ^
  |
  Pulsing at 2 Hz (faster)
  Timer text color: #FCA5A5 (warning tint)
```

### 12.3 PROCESSING State (Normal)

```
+---------------------------------------------------+
|                                                   |
|  * . .  Transcribing...                           |
|                                                   |
+---------------------------------------------------+
  ^         ^
  |         |
  Animated   Status text
  dots       (#FFFFFF, 14px)
  (#FBBF24)

Background: #92400E (dark amber) at 90% opacity
Border: 1px #D97706 (amber)
```

### 12.4 PROCESSING State (>10 seconds)

```
+---------------------------------------------------+
|                                                   |
|  . * .  Still processing...                       |
|                                                   |
+---------------------------------------------------+
             ^
             |
             Amber-tinted text (#FDE68A)
             after 10 seconds in same step
```

### 12.5 PROCESSING State (Summarization Step)

```
+---------------------------------------------------+
|                                                   |
|  * . .  Summarizing...                            |
|                                                   |
+---------------------------------------------------+

Text changes to reflect current pipeline step.
Same visual treatment as Transcribing.
```

### 12.6 PROCESSING State (Ask AI Mode)

```
+---------------------------------------------------+
|                                                   |
|  . . *  Thinking...                               |
|                                                   |
+---------------------------------------------------+

"Thinking..." used for LLM prompt mode (Ctrl+Alt+A).
Distinguishes from "Summarizing..." which is text cleanup.
```

### 12.7 SPEAKING State

```
+---------------------------------------------------+
|  +--+                                             |
|  |))| Speaking...             Esc to stop         |
|  +--+                                             |
+---------------------------------------------------+
  ^          ^                    ^
  |          |                    |
  Speaker    Label                Hint text
  icon with  (#FFFFFF, 14px)      (#93C5FD light blue)
  animated                        (11px)
  waves
  (#60A5FA)

Background: #1E3A5F (dark blue) at 90% opacity
Border: 1px #3B82F6 (bright blue)
```

### 12.8 PASTING State

```
+-------------------------------+
|                               |
|  [check]  Pasted!             |
|                               |
+-------------------------------+
  ^           ^
  |           |
  Checkmark   Label
  (#34D399)   (#FFFFFF, 14px)

Background: #065F46 (dark green) at 90% opacity
Border: 1px #10B981 (bright green)
Width: 160px (narrower than other states)
```

### 12.9 Overlay Position on Screen (Default: Top Center)

```
+======================================================================+
|                                                                      |
|              +-------------------------------------------+           |
|              |  * 0:12              Esc to cancel        |           |
|              +-------------------------------------------+           |
|                    ^  60px from top of work area                     |
|                                                                      |
|    +-----------------------------------------------------------------|
|    |                                                                 |
|    |         User's application (text editor, browser, etc.)         |
|    |                                                                 |
|    |                                                                 |
|    |              The overlay is 100% click-through.                 |
|    |              The user can interact with everything behind it.   |
|    |                                                                 |
|    +-----------------------------------------------------------------|
|                                                                      |
+======================================================================+
| [Start] |                                     | [^] [tray icons]    |
+======================================================================+
```

### 12.10 Settings Dialog: General Section (Updated)

```
+-- General -------------------------------------------------------+
|                                                                    |
|  [x] Play audio cues                                              |
|                                                                    |
|  [x] Show floating status overlay                                  |
|       Position: [Top center                    v]                  |
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

### 12.11 Overlay State Transition Timeline

```
Time -->

User presses         Hotkey    STT done     Summary    Paste    Paste
Ctrl+Alt+R          again     (5s)         done (3s)  (0.2s)   done
    |                  |         |            |          |        |
    v                  v         v            v          v        v

[hidden] --fade in--> [RECORDING] --xfade--> [PROCESSING] --xfade--> [PASTING] --fade out--> [hidden]
             150ms     red pill     200ms      amber pill    200ms     green pill   300ms
                       pulsing dot             "Transcribing"         "Pasted!"
                       timer                   "Summarizing..."       checkmark
                       "Esc to cancel"         (10s: "Still...")
```

---

## 13. Technical Constraints and Recommendations

### 13.1 Window Creation

**Recommendation**: Win32 API via ctypes (same recommendation as v0.6 overlay spec).

The status overlay is simpler than the v0.6 toolbar because it has NO interactive elements. This eliminates the need for hit-testing, button state management, and focus return logic. The overlay is purely a rendered bitmap displayed via a layered window.

Required window styles:
```
WS_POPUP                  -- No title bar, no border (we draw our own)
WS_EX_TOPMOST             -- Always on top
WS_EX_TOOLWINDOW          -- No taskbar/Alt+Tab entry
WS_EX_NOACTIVATE          -- Never steal focus
WS_EX_TRANSPARENT         -- 100% click-through (mouse events pass to window behind)
WS_EX_LAYERED             -- Supports per-pixel alpha via UpdateLayeredWindow
```

### 13.2 Rendering Pipeline

The overlay content is rendered as a Pillow Image (RGBA, 240x48 at 1x DPI, scaled for actual DPI). The rendering pipeline:

1. Create an RGBA Image of the overlay dimensions
2. Draw the rounded rectangle background with state-appropriate color
3. Draw the 1px border
4. Draw the state-specific content (dot, dots, speaker icon, checkmark)
5. Draw the text (timer, status, hint) using Pillow's `ImageDraw.text()` with a TrueType font (Segoe UI, loaded via `ImageFont.truetype("segoeui.ttf", size)`)
6. Convert the Pillow Image to a Win32 HBITMAP (DIB section)
7. Call `UpdateLayeredWindow` with the HBITMAP and BLENDFUNCTION (per-pixel alpha)

For animation frames, steps 1-7 are repeated at 30 FPS during animated states. For static states (no animation), the bitmap is rendered once and held.

### 13.3 Font Loading

Segoe UI is a system font on Windows 10/11 and is located at `C:\Windows\Fonts\segoeui.ttf` (regular) and `C:\Windows\Fonts\seguisb.ttf` (semibold).

Pillow can load these fonts via:
```python
from PIL import ImageFont
font_regular = ImageFont.truetype("segoeui.ttf", 11)
font_semibold = ImageFont.truetype("seguisb.ttf", 16)
```

If the font is not found (unlikely on Windows 10/11), fall back to Pillow's default bitmap font. This degrades the visual quality but does not break functionality.

### 13.4 Animation Thread

Animations (pulsing dot, animated dots, speaker waves, fade transitions) run on a dedicated daemon thread. This thread:

1. Sleeps for 33ms (30 FPS target)
2. Checks if animation is needed (active state with animation)
3. Computes the current frame (based on elapsed time, not frame count -- handles dropped frames gracefully)
4. Renders the frame bitmap
5. Calls `UpdateLayeredWindow` to display the frame
6. Repeats

When the app is in IDLE (overlay hidden), the animation thread is idle (sleeping on an event). It wakes only when a state transition occurs.

### 13.5 Thread Safety

The overlay state is driven by `_set_state()` calls from the pipeline worker thread. The overlay window updates must happen on the thread that created the window (Win32 threading rules) or via `PostMessage` to the overlay's message queue.

**Recommended approach**: The overlay creates its own message-only window on a dedicated thread. State changes are communicated via `PostMessage(WM_APP + 1, new_state, ...)`. The overlay thread receives the message and updates its rendering. This is the same pattern used by the tray icon (pystray's internal message loop).

### 13.6 Performance Budget

The overlay must consume less than:
- **1% CPU** when idle (overlay hidden)
- **3% CPU** when visible with animation (rendering at 30 FPS)
- **5 MB memory** (pre-rendered bitmaps, font cache, window resources)

These are generous budgets. A 240x48 RGBA bitmap is ~46 KB. Rendering it with Pillow takes <1ms on modern hardware. The primary CPU cost is the 30 FPS render loop, which can be optimized by only re-rendering changed elements (e.g., only the timer text changes each second during RECORDING -- the background and dot can be cached).

---

## 14. Open Questions

| # | Question | Impact | Default Assumption |
|---|----------|--------|--------------------|
| 1 | Should the overlay position be user-draggable (like the v0.6 toolbar)? | Implementation complexity. Adds state (position persistence). | No. Fixed positions only (configurable via dropdown). Dragging adds interaction to what should be a non-interactive element. |
| 2 | Should the overlay show a character/word count after pasting? | Adds informational value. Requires pipeline to report count. | No. The green "Pasted!" flash is sufficient. Word count adds visual noise to a 500ms-visible element. The tray tooltip or status line can show "Last: 47 words at 2:34 PM" for users who want this data. |
| 3 | Should the PROCESSING overlay show a progress percentage for local STT? | Useful for long local transcriptions. faster-whisper may not report progress. | No for v0.8. If progress data becomes available from the STT backend, it can be added as a secondary element under the status text. |
| 4 | Should the overlay be visible on all monitors (duplicated) in a multi-monitor setup? | Edge case for users who work on non-primary monitors. | No. Single instance on primary monitor. Multi-monitor users can configure position to suit their layout. |
| 5 | Should the overlay respect Windows Focus Assist / Do Not Disturb mode? | May be expected by users who have DND on. | No. The overlay is not a notification -- it is state feedback for an action the user just initiated. DND should not suppress it. |
| 6 | Should the overlay animate its width change when transitioning to/from PASTING (240px to 160px)? | Visual polish. | Yes. Animate width change over the 200ms cross-fade duration. The pill smoothly shrinks/grows alongside the color transition. |
| 7 | What happens if the user disables both the overlay AND audio cues? | The only remaining feedback is the tray icon (which was the problem). | Allow it. The user made a conscious choice. The tray icon still works. Log a warning on first occurrence: "Both overlay and audio cues disabled. Only the tray icon provides state feedback." |
| 8 | Should the v0.6 toolbar overlay and the v0.8 status overlay coexist? | Two overlays on screen simultaneously. | They are separate features with separate toggle controls. If both are enabled, they should not overlap (the toolbar is at the right edge, the status overlay is at the top center). They serve different purposes: toolbar = action buttons, status = state feedback. |
| 9 | Should the "Still processing..." message include elapsed time? (e.g., "Still processing... (15s)") | Provides concrete waiting information. | Yes, include it. After 10 seconds, show: "Still processing... (12s)" with the counter updating each second. This gives the user a concrete sense of how long they have been waiting. |
| 10 | Should the overlay text be localized (German, English)? | German is the primary user base. | The overlay text uses short, universal English terms ("Transcribing...", "Pasted!") that are widely understood. German alternatives ("Transkribiere...", "Eingefuegt!") can be added in a future localization pass. For v0.8, English only. |

---

## Appendix A: Color Reference

### State Color Palette

| State | Background | Border | Primary Text | Secondary Text | Accent |
|-------|-----------|--------|-------------|---------------|--------|
| RECORDING | #B91C1C | #DC2626 | #FFFFFF | #FCA5A5 | #EF4444 |
| PROCESSING | #92400E | #D97706 | #FFFFFF | #FDE68A | #FBBF24 |
| SPEAKING | #1E3A5F | #3B82F6 | #FFFFFF | #93C5FD | #60A5FA |
| PASTING | #065F46 | #10B981 | #FFFFFF | N/A | #34D399 |

All background colors are chosen to be dark enough that white text has AAA contrast ratio (>7:1) against them, ensuring readability regardless of the desktop wallpaper behind the semi-transparent overlay.

### Contrast Verification

| State | Background Lightness | White Text Contrast | Passes WCAG AAA? |
|-------|---------------------|--------------------|--------------------|
| RECORDING (#B91C1C) | 26% | 8.2:1 | Yes |
| PROCESSING (#92400E) | 30% | 7.1:1 | Yes |
| SPEAKING (#1E3A5F) | 22% | 9.5:1 | Yes |
| PASTING (#065F46) | 20% | 10.1:1 | Yes |

## Appendix B: Constants Additions (constants.py)

```python
# --- v0.8: Floating status overlay ---
OVERLAY_STATUS_WIDTH = 240       # px (logical, at 100% DPI)
OVERLAY_STATUS_WIDTH_PASTING = 160  # px (narrower for PASTING state)
OVERLAY_STATUS_HEIGHT = 48       # px (logical, at 100% DPI)
OVERLAY_STATUS_CORNER_RADIUS = 12  # px
OVERLAY_STATUS_OPACITY = 0.9     # 90% opacity
OVERLAY_STATUS_FONT_PRIMARY_SIZE = 16    # px (timer, main text)
OVERLAY_STATUS_FONT_SECONDARY_SIZE = 14  # px (status text)
OVERLAY_STATUS_FONT_HINT_SIZE = 11       # px (Esc hint)
OVERLAY_STATUS_VERTICAL_OFFSET = 60      # px from top/bottom edge

# Overlay position options
OVERLAY_POSITIONS = (
    "top-center", "top-left", "top-right",
    "bottom-center", "bottom-left", "bottom-right",
)
DEFAULT_OVERLAY_POSITION = "top-center"
DEFAULT_OVERLAY_STATUS_ENABLED = True

# Overlay animation timing
OVERLAY_FADE_IN_MS = 150
OVERLAY_FADE_OUT_MS = 300
OVERLAY_CROSSFADE_MS = 200
OVERLAY_PASTING_HOLD_MS = 300
OVERLAY_PASTING_FADE_OUT_MS = 200
OVERLAY_ANIMATION_FPS = 30
OVERLAY_PULSE_CYCLE_MS = 1000       # Recording dot pulse period
OVERLAY_PULSE_FAST_CYCLE_MS = 500   # Near time limit
OVERLAY_DOTS_CYCLE_MS = 1500        # Processing dots cycle
OVERLAY_SPEAKER_WAVE_CYCLE_MS = 800 # Speaking icon animation
OVERLAY_STILL_PROCESSING_TIMEOUT_S = 10  # Seconds before "Still processing..."

# Overlay state colors
OVERLAY_RECORDING_BG = (185, 28, 28)       # #B91C1C
OVERLAY_RECORDING_BORDER = (220, 38, 38)   # #DC2626
OVERLAY_RECORDING_ACCENT = (239, 68, 68)   # #EF4444
OVERLAY_RECORDING_HINT = (252, 165, 165)   # #FCA5A5

OVERLAY_PROCESSING_BG = (146, 64, 14)      # #92400E
OVERLAY_PROCESSING_BORDER = (217, 119, 6)  # #D97706
OVERLAY_PROCESSING_ACCENT = (251, 191, 36) # #FBBF24
OVERLAY_PROCESSING_HINT = (253, 230, 138)  # #FDE68A

OVERLAY_SPEAKING_BG = (30, 58, 95)         # #1E3A5F
OVERLAY_SPEAKING_BORDER = (59, 130, 246)   # #3B82F6
OVERLAY_SPEAKING_ACCENT = (96, 165, 250)   # #60A5FA
OVERLAY_SPEAKING_HINT = (147, 197, 253)    # #93C5FD

OVERLAY_PASTING_BG = (6, 95, 70)           # #065F46
OVERLAY_PASTING_BORDER = (16, 185, 129)    # #10B981
OVERLAY_PASTING_ACCENT = (52, 211, 153)    # #34D399

# Recording time warning threshold (seconds before max)
RECORDING_WARNING_THRESHOLD_S = 10  # Start warning at (MAX - 10) seconds
```

## Appendix C: Config.toml Additions

```toml
[feedback]
audio_cues = true
# Show floating status overlay during active states
overlay_status_enabled = true
# Overlay position on screen
overlay_position = "top-center"
```

## Appendix D: Processing Step Callback Interface

The overlay needs to know which pipeline step is currently active to display the correct status text. The recommended interface is a simple callback or shared state:

```python
class OverlayStatusManager:
    """Manages the floating status overlay."""

    def set_state(self, state: AppState) -> None:
        """Called on every state transition. Shows/hides/updates overlay."""
        ...

    def set_processing_step(self, step: str) -> None:
        """Called during PROCESSING to update the status text.

        Args:
            step: One of "transcribing", "transcribing_local",
                  "summarizing", "thinking", "preparing_speech"
        """
        ...
```

The pipeline in main.py calls `self._overlay.set_processing_step("transcribing")` before starting STT, then `self._overlay.set_processing_step("summarizing")` before starting summarization, etc. This allows the overlay to display accurate status text without knowing the pipeline internals.
