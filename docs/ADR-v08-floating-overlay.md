# Architecture Decision Record: v0.8 -- Floating Overlay UI

**Date**: 2026-02-18
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.7.0 (Local TTS via Piper)
**Target Version**: 0.8.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Requirements Recap](#2-requirements-recap)
3. [Decision 1: Window Technology](#3-decision-1-window-technology)
4. [Decision 2: Threading Model](#4-decision-2-threading-model)
5. [Decision 3: State Machine Integration](#5-decision-3-state-machine-integration)
6. [Decision 4: Timer Updates During RECORDING](#6-decision-4-timer-updates-during-recording)
7. [Decision 5: Positioning and Monitor Awareness](#7-decision-5-positioning-and-monitor-awareness)
8. [Win32 Focus-Stealing Prevention -- Deep Dive](#8-win32-focus-stealing-prevention)
9. [Module Design: src/overlay.py](#9-module-design)
10. [Integration Points with Existing Code](#10-integration-points)
11. [Configuration Schema Extensions](#11-configuration-schema-extensions)
12. [Threading Diagram](#12-threading-diagram)
13. [Risk Assessment](#13-risk-assessment)
14. [Visual Design Specification](#14-visual-design-specification)
15. [Implementation Plan](#15-implementation-plan)
16. [Trade-offs Summary](#16-trade-offs-summary)

---

## 1. Executive Summary

v0.8 adds a **floating overlay window** that provides real-time visual feedback
for the application state machine. The overlay shows:

- **RECORDING**: a red dot with an elapsed-time counter ("00:05")
- **PROCESSING**: an animated spinner or pulsing dots
- **SPEAKING**: a blue pulsing waveform indicator

The overlay is positioned in the bottom-right corner of the primary monitor,
above the taskbar, and has these critical properties:

- **Never steals keyboard focus** from the user's application (WS_EX_NOACTIVATE)
- **Click-through** -- mouse events pass to the window underneath (WS_EX_TRANSPARENT)
- **Always-on-top** without appearing in Alt+Tab or taskbar (WS_EX_TOOLWINDOW + WS_EX_TOPMOST)
- **Togglable** via `[feedback] show_overlay = true` in config.toml
- **Hot-reloadable** -- can be enabled/disabled via Settings without restart

**Key architectural decision**: Use a **tkinter Toplevel with `overrideredirect(True)`**
on a **dedicated daemon thread (T4)**, with Win32 extended window styles applied
via ctypes post-creation. This reuses the existing tkinter dependency, avoids
adding a raw Win32 window class (hundreds of lines of boilerplate), and follows
the same threading pattern already proven by the Settings dialog (T3).

---

## 2. Requirements Recap

| ID | Requirement | Priority |
|----|-------------|----------|
| US-0.8.1 | Show state-specific visual feedback (Recording timer, Processing animation, Speaking indicator) | Must |
| US-0.8.2 | Never steal keyboard focus from the user's current application | Must (non-negotiable) |
| US-0.8.3 | Click-through (mouse clicks pass through to window underneath) | Must |
| US-0.8.4 | Always-on-top but NOT in Alt+Tab or taskbar | Must |
| US-0.8.5 | Position in bottom-right of primary monitor, ~60px above taskbar | Should |
| US-0.8.6 | Togglable via config.toml `[feedback] show_overlay = true` | Must |
| US-0.8.7 | Hot-reload (enable/disable without restart) | Should |

---

## 3. Decision 1: Window Technology

### Options Evaluated

| Criterion | tkinter Toplevel + ctypes | Raw Win32 CreateWindowExW | Qt/PySide overlay |
|-----------|--------------------------|---------------------------|-------------------|
| **Dependencies** | Already in project (settings_dialog.py) | ctypes only (stdlib) | Adds ~30 MB PyQt6/PySide6 |
| **Code volume** | ~200 lines (overlay class) | ~400 lines (WNDCLASS, message pump, painting, DPI, cleanup) | ~150 lines |
| **Focus control** | overrideredirect(True) + ctypes to patch GWL_EXSTYLE with WS_EX_NOACTIVATE. Proven technique. | Native WS_EX_NOACTIVATE at creation time. Maximum control. | Qt.WindowDoesNotAcceptFocus + WindowStaysOnTopHint. Less granular. |
| **Click-through** | ctypes: add WS_EX_TRANSPARENT to GWL_EXSTYLE | Native WS_EX_TRANSPARENT at creation | Requires platform-specific code anyway |
| **Text rendering** | tk.Label with custom font | GDI/Direct2D (complex) | QLabel (trivial) |
| **Animation** | root.after() for periodic updates | SetTimer() / WM_TIMER | QTimer (trivial) |
| **Binary size impact** | 0 bytes (tkinter already bundled) | 0 bytes (ctypes is stdlib) | +30 MB |
| **Debugging** | Familiar Python-level debugging | Win32 message debugging is painful | Good Python debugging |
| **Threading** | Needs its own thread with Tk() + mainloop() | Needs its own thread with GetMessage() loop | Needs its own thread |
| **Proven in project** | Yes (settings_dialog.py) | Partially (paste.py ctypes calls) | No |

### Decision: tkinter Toplevel + ctypes

**Rationale:**

1. **Zero new dependencies.** tkinter is already bundled for the Settings dialog.
   Adding Qt would increase binary size by ~30 MB for a widget that displays a
   few labels.

2. **Proven pattern.** The Settings dialog (`settings_dialog.py`) already
   demonstrates the exact threading model: spawn a daemon thread, create a Tk()
   root, run mainloop(), destroy on close. The overlay follows this pattern
   identically.

3. **Sufficient for the use case.** The overlay is a 200x60 pixel rectangle
   with 2-3 labels and periodic timer updates. tkinter handles this trivially.
   Complex rendering (smooth animations, vector graphics) is not needed.

4. **Win32 style patching is well-understood.** The `_apply_dark_title_bar()`
   function in `settings_dialog.py` already demonstrates the pattern of getting
   the HWND from a tkinter widget and calling DwmSetWindowAttribute via ctypes.
   Adding `SetWindowLongW(GWL_EXSTYLE, ...)` is the same pattern.

**Why not raw Win32?** While raw Win32 gives maximum control over focus behavior
at window creation time, it requires writing a full WNDCLASS, message pump,
WM_PAINT handler with GDI text rendering, and DPI-aware positioning -- roughly
400 lines of dense ctypes code for something tkinter handles in 20 lines.
The risk of subtle Win32 bugs (leaked DCs, incorrect PAINTSTRUCT handling,
DPI scaling) outweighs the marginal benefit of "native" WS_EX_NOACTIVATE at
creation time. With tkinter, we apply WS_EX_NOACTIVATE immediately after window
creation (before any user interaction is possible), achieving the same result.

**Why not Qt?** Adding PySide6/PyQt6 just for an overlay is a disproportionate
dependency. It contradicts the project's "simplest working solution" principle.

---

## 4. Decision 2: Threading Model

### Current Thread Architecture

```
T0 (Main)    : pystray.Icon.run() -- Win32 GetMessage loop, blocks until stop()
T1 (keyboard): keyboard library listener thread (daemon, auto-started)
T2 (pipeline): spawned per recording session (daemon), runs STT+LLM+paste
T3 (settings): spawned on demand (daemon), runs tkinter Tk() + mainloop()
```

### Options for Overlay Thread

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Share T3 (Settings thread)** | Overlay is a Toplevel on the same Tk() root as Settings | Single Tcl interpreter, simpler | Settings thread is ephemeral (destroyed on dialog close). Overlay must be persistent. Would force Settings to keep the Tk root alive forever, fundamentally changing its lifecycle. |
| **B: New T4 (dedicated overlay thread)** | Overlay gets its own Tk() root and mainloop() on a new daemon thread | Clean lifecycle: overlay thread lives as long as the app. Independent of Settings. Thread can be started/stopped for hot-reload. | Additional thread. Two Tcl interpreters (not a problem -- they are independent). |
| **C: Run overlay on T0 (main thread)** | Move pystray to a thread, give main thread to tkinter | tkinter on main thread is "canonical" | pystray uses Win32 Shell_NotifyIcon which has thread affinity. Moving it off main thread risks notification failures. Major refactoring. |

### Decision: Option B -- Dedicated T4 thread

**Rationale:**

1. **Lifecycle independence.** The overlay must be visible whenever the app is
   running (or whenever `show_overlay` is True). The Settings dialog is
   ephemeral -- it creates and destroys its Tk() root each time the user opens
   and closes it. Forcing the overlay onto the Settings thread would require a
   persistent Tk() root that outlives the dialog, which is a fundamental
   redesign of the settings_dialog.py module.

2. **Hot-reload simplicity.** When the user toggles `show_overlay` in Settings,
   we can start or stop the T4 thread cleanly. Starting = spawn new thread with
   new Tk() + mainloop(). Stopping = call `root.quit()` from any thread (Tcl
   is thread-safe for `quit()`), then join the thread.

3. **No cross-thread Tcl calls.** Each tkinter Tk() instance has its own Tcl
   interpreter. The overlay thread's Tcl interpreter is independent of the
   Settings thread's Tcl interpreter. No shared state, no deadlock risk.

4. **Proven pattern.** This is exactly how `open_settings_dialog()` works:
   spawn daemon thread, create Tk(), run mainloop(), destroy root. The overlay
   does the same, just with a longer lifetime.

**Memory cost:** A Python thread with a tkinter Tk() root and a 200x60 window
uses approximately 5-8 MB. Negligible compared to the app's overall footprint.

### Updated Thread Architecture (v0.8)

```
T0 (Main)    : pystray.Icon.run() -- Win32 GetMessage loop
T1 (keyboard): keyboard library listener thread (daemon)
T2 (pipeline): spawned per recording session (daemon)
T3 (settings): spawned on demand, destroyed on dialog close (daemon)
T4 (overlay) : spawned at startup if show_overlay=true, lives until shutdown (daemon)
```

---

## 5. Decision 3: State Machine Integration

### How the overlay learns about state changes

The current flow is:

```
VoicePasteApp._set_state(new_state)
  -> self._tray_manager.update_state(new_state)
```

The overlay needs to be notified the same way. Two options:

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Direct callback** | `_set_state()` calls `self._overlay.update_state(new_state)` alongside the tray manager call | Simple. No abstraction overhead. Follows existing pattern (tray manager is called directly). | Adds one more direct call to `_set_state()`. If we add more observers, this grows linearly. |
| **B: Observer pattern** | State changes broadcast to a list of observers | Cleaner extension point for future observers (e.g., API pipe, hands-free module) | Over-engineering for 2 observers (tray + overlay). Adds abstraction complexity. |

### Decision: Option A -- Direct callback

**Rationale:** The project currently has exactly one state observer (TrayManager).
Adding the overlay makes it two. An observer pattern with a subscriber list is
warranted when there are 3+ dynamic observers, or when observers are registered
and unregistered frequently at runtime. For two known, static observers, direct
calls are simpler and easier to trace in the debugger.

If v0.9 (hands-free mode) or the external API add more observers, we can
refactor to an observer list at that time. The refactoring cost is low: change
two direct calls in `_set_state()` to a loop over a list.

### Integration pattern

```python
# In VoicePasteApp._set_state():
def _set_state(self, new_state: AppState) -> None:
    with self._state_lock:
        old_state = self._state
        self._state = new_state
        logger.info("State: %s -> %s", old_state.value, new_state.value)

    # Notify visual feedback components
    self._tray_manager.update_state(new_state)
    if self._overlay is not None:
        self._overlay.update_state(new_state)
```

The overlay's `update_state()` method must be **thread-safe** because
`_set_state()` is called from multiple threads (T1 via hotkey callback, T2 via
pipeline worker). The overlay uses tkinter's `root.after_idle()` / `root.event_generate()`
to marshal the update onto the Tcl event loop thread (T4). See Section 9 for
the implementation.

---

## 6. Decision 4: Timer Updates During RECORDING

During the RECORDING state, the overlay must display an elapsed time counter
that updates every second (e.g., "00:05", "00:06", "00:07"...).

### Approach: `root.after()` polling loop

```python
def _tick_recording_timer(self) -> None:
    """Update the recording elapsed time display. Runs on T4 (tkinter)."""
    if self._current_state != AppState.RECORDING:
        return  # Stop ticking when no longer RECORDING

    elapsed = time.monotonic() - self._recording_start_time
    minutes = int(elapsed) // 60
    seconds = int(elapsed) % 60
    self._time_label.configure(text=f"{minutes:02d}:{seconds:02d}")

    # Schedule next tick in 1 second
    self._timer_id = self._root.after(1000, self._tick_recording_timer)
```

**Why `root.after()` and not a separate timer thread?**

- `root.after()` executes the callback on the Tcl event loop thread (T4),
  which is the only thread allowed to modify tkinter widgets.
- A separate timer thread would need to use `root.after_idle()` or
  `root.event_generate()` to marshal the update back to T4 -- adding
  complexity for no benefit.
- `root.after()` is the canonical tkinter approach for periodic updates.

**Cancellation:** When leaving RECORDING state (transition to PROCESSING or
IDLE via cancel), the overlay cancels the pending `after()` callback using
`root.after_cancel(self._timer_id)` to prevent orphaned callbacks.

---

## 7. Decision 5: Positioning and Monitor Awareness

### Getting the primary monitor work area

The overlay must be positioned in the bottom-right corner, above the taskbar.
On Windows, the "work area" is the screen area minus the taskbar.

**Approach: `SystemParametersInfoW(SPI_GETWORKAREA)`**

```python
import ctypes
import ctypes.wintypes

SPI_GETWORKAREA = 0x0030

def _get_work_area() -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the primary monitor work area."""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
    )
    return rect.left, rect.top, rect.right, rect.bottom
```

This returns the work area of the **primary** monitor, which is the correct
target. Multi-monitor positioning (placing the overlay on the monitor where the
user is currently focused) is a future enhancement, not a v0.8 requirement.

### Position calculation

```
work_area_right  = rect.right
work_area_bottom = rect.bottom

overlay_x = work_area_right - OVERLAY_WIDTH - OVERLAY_MARGIN_RIGHT
overlay_y = work_area_bottom - OVERLAY_HEIGHT - OVERLAY_MARGIN_BOTTOM
```

Where `OVERLAY_MARGIN_RIGHT = 20` and `OVERLAY_MARGIN_BOTTOM = 20` provide
a small gap from the screen edge and taskbar.

### DPI awareness

On high-DPI displays (125%, 150%, 200% scaling), `SystemParametersInfoW` returns
coordinates in physical pixels if the process is DPI-aware, or in scaled
"virtual" pixels if not. Since Python 3.11+, tkinter calls `SetProcessDpiAwareness`
automatically, so the coordinates are already correct.

However, tkinter's `wm_geometry()` operates in tkinter's internal coordinate
system, which may differ from Win32 pixel coordinates on high-DPI. To ensure
correctness, we:

1. Query the work area in Win32 pixels via `SystemParametersInfoW`.
2. Use tkinter's `winfo_screenwidth()` / `winfo_screenheight()` as a
   cross-check.
3. Position via `wm_geometry(f"+{x}+{y}")` which tkinter handles correctly
   on the primary monitor even under DPI scaling, because tkinter internally
   adjusts for the DPI of the display the window is placed on.

---

## 8. Win32 Focus-Stealing Prevention -- Deep Dive

This is the single most critical technical requirement. Here is the full
strategy, with fallbacks.

### Extended Window Styles Applied

After creating the tkinter Toplevel and calling `update_idletasks()` (which
forces HWND creation), we apply these extended styles via `SetWindowLongW`:

```python
import ctypes

GWL_EXSTYLE = -20
user32 = ctypes.windll.user32

# Get the HWND from tkinter
hwnd = user32.GetParent(toplevel.winfo_id())

# Read current extended style
current_ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

# Apply overlay-specific styles
WS_EX_NOACTIVATE  = 0x08000000  # Window cannot be activated (focused)
WS_EX_TOPMOST     = 0x00000008  # Always on top
WS_EX_TRANSPARENT  = 0x00000020  # Click-through
WS_EX_TOOLWINDOW  = 0x00000080  # Hidden from Alt+Tab and taskbar
WS_EX_LAYERED     = 0x00080000  # Supports transparency

new_ex = current_ex | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TRANSPARENT \
       | WS_EX_TOOLWINDOW | WS_EX_LAYERED

user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex)
```

### Style-by-style justification

| Style | Hex | Purpose | Without it |
|-------|-----|---------|------------|
| `WS_EX_NOACTIVATE` | 0x08000000 | **The** focus-prevention flag. Window does not become the foreground window when clicked or shown. `ShowWindow()`, `SetWindowPos()`, and `BringWindowToTop()` all skip activation. | The window would steal focus when shown or when tkinter calls `update()`. |
| `WS_EX_TOPMOST` | 0x00000008 | Always on top of non-topmost windows. Equivalent to HWND_TOPMOST in `SetWindowPos`. | Overlay would be hidden behind other windows. |
| `WS_EX_TRANSPARENT` | 0x00000020 | Hit-test transparency. Mouse clicks "fall through" to the window underneath. WM_NCHITTEST returns HTTRANSPARENT. | User could not click on the window underneath the overlay (the text editor, browser, etc.). |
| `WS_EX_TOOLWINDOW` | 0x00000080 | Hides the window from Alt+Tab, taskbar, and `EnumWindows` enumeration. | Overlay would appear in Alt+Tab and the taskbar, cluttering the user's workflow. |
| `WS_EX_LAYERED` | 0x00080000 | Required for per-pixel alpha and `SetLayeredWindowAttributes`. Allows the dark background to have adjustable opacity. | The overlay background would be fully opaque. On Windows, non-layered windows cannot have alpha transparency. |

### Additional safeguards

1. **`overrideredirect(True)`**: Removes the title bar and window frame.
   Without a frame, there is no system menu, minimize/maximize/close buttons,
   or title bar that could receive focus on click. This also removes the
   window from the Windows window manager's resize/move logic.

2. **`-topmost True`**: tkinter's `wm_attributes('-topmost', True)` calls
   `SetWindowPos(HWND_TOPMOST, ...)` which is redundant with WS_EX_TOPMOST
   but ensures the topmost state is set through tkinter's API as well.

3. **No `focus_force()` or `focus_set()` calls**: The overlay code never
   calls any tkinter focus method. All widget updates use `.configure()` on
   existing widgets, which does not trigger focus changes.

4. **No `deiconify()` without prior `withdraw()`**: The overlay is created
   once and kept visible. It is never iconified and restored (which can
   trigger focus). Show/hide is done via `withdraw()` and `deiconify()`,
   but `deiconify()` on a WS_EX_NOACTIVATE window does not steal focus.

5. **SetWindowPos with SWP_NOACTIVATE**: If we ever need to re-position or
   re-z-order the overlay, we use `SetWindowPos` with the `SWP_NOACTIVATE`
   flag (0x0010) to prevent activation during the operation.

### 64-bit safety

Following the pattern established in `paste.py`, all Win32 API calls must have
explicit `restype` and `argtypes` declarations to prevent 64-bit pointer
truncation:

```python
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]

user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]

user32.GetParent.restype = ctypes.wintypes.HWND
user32.GetParent.argtypes = [ctypes.wintypes.HWND]

user32.SetWindowPos.restype = ctypes.wintypes.BOOL
user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.UINT,
]

user32.SystemParametersInfoW.restype = ctypes.wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [
    ctypes.wintypes.UINT, ctypes.wintypes.UINT,
    ctypes.c_void_p, ctypes.wintypes.UINT,
]

user32.SetLayeredWindowAttributes.restype = ctypes.wintypes.BOOL
user32.SetLayeredWindowAttributes.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.COLORREF,
    ctypes.wintypes.BYTE, ctypes.wintypes.DWORD,
]
```

---

## 9. Module Design: src/overlay.py

### Class: `OverlayWindow`

```python
"""Floating overlay window for real-time state feedback.

v0.8: Provides visual indicators for RECORDING (timer), PROCESSING
(animation), and SPEAKING (indicator) states.

Threading model:
    The overlay runs its own tkinter Tk() root and mainloop() on a
    dedicated daemon thread (T4). All widget modifications happen on
    T4 via root.after_idle(). External threads communicate state
    changes through the thread-safe update_state() method.

Critical constraint:
    The overlay window MUST NEVER steal keyboard focus. This is
    enforced via WS_EX_NOACTIVATE + WS_EX_TRANSPARENT extended
    window styles applied via ctypes after window creation.
"""

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from typing import Optional

from constants import APP_NAME, AppState

logger = logging.getLogger(__name__)


# --- Win32 constants ---
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x02
SPI_GETWORKAREA = 0x0030
SWP_NOACTIVATE = 0x0010
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = -1

# --- Layout constants ---
OVERLAY_WIDTH = 200
OVERLAY_HEIGHT = 56
OVERLAY_MARGIN_RIGHT = 20
OVERLAY_MARGIN_BOTTOM = 20
OVERLAY_BG_COLOR = "#1c1c1c"
OVERLAY_BG_ALPHA = 220  # 0-255, 220 = ~86% opacity
OVERLAY_CORNER_RADIUS = 12

# --- State-specific colors ---
STATE_COLORS = {
    AppState.RECORDING: "#e63232",    # Red
    AppState.PROCESSING: "#f0c828",   # Amber/yellow
    AppState.SPEAKING: "#4682e6",     # Blue
}

# --- State-specific labels ---
STATE_LABELS = {
    AppState.RECORDING: "Recording",
    AppState.PROCESSING: "Processing...",
    AppState.SPEAKING: "Speaking...",
}


# --- Win32 API type declarations (64-bit safe) ---
_user32 = ctypes.windll.user32

_user32.GetParent.restype = ctypes.wintypes.HWND
_user32.GetParent.argtypes = [ctypes.wintypes.HWND]

_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]

_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [
    ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long,
]

_user32.SetWindowPos.restype = ctypes.wintypes.BOOL
_user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.UINT,
]

_user32.SetLayeredWindowAttributes.restype = ctypes.wintypes.BOOL
_user32.SetLayeredWindowAttributes.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.COLORREF,
    ctypes.wintypes.BYTE, ctypes.wintypes.DWORD,
]

_user32.SystemParametersInfoW.restype = ctypes.wintypes.BOOL
_user32.SystemParametersInfoW.argtypes = [
    ctypes.wintypes.UINT, ctypes.wintypes.UINT,
    ctypes.c_void_p, ctypes.wintypes.UINT,
]


def _get_work_area() -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of primary monitor work area.

    Uses SystemParametersInfoW(SPI_GETWORKAREA) which returns the screen
    area excluding the taskbar.

    Returns:
        Tuple of (left, top, right, bottom) in pixels.
    """
    rect = ctypes.wintypes.RECT()
    _user32.SystemParametersInfoW(
        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
    )
    return rect.left, rect.top, rect.right, rect.bottom


class OverlayWindow:
    """Floating overlay window for state feedback.

    Lifecycle:
        1. __init__(): Stores config, does NOT create window.
        2. start(): Spawns T4 thread, creates Tk root + window, runs mainloop.
        3. update_state(state): Thread-safe. Marshals to T4 via event_generate.
        4. stop(): Calls root.quit() from any thread, joins T4.

    Thread safety:
        - start() and stop() may be called from any thread.
        - update_state() may be called from any thread.
        - All tkinter widget operations happen exclusively on T4.

    Attributes:
        is_running: Whether the overlay thread is alive and the window exists.
    """

    def __init__(self) -> None:
        """Initialize overlay (no window created yet)."""
        self._thread: Optional[threading.Thread] = None
        self._root = None  # tk.Tk, set on T4
        self._window = None  # tk.Toplevel, set on T4
        self._state_label = None  # tk.Label
        self._time_label = None  # tk.Label
        self._dot_label = None  # tk.Label (colored dot indicator)
        self._current_state: AppState = AppState.IDLE
        self._recording_start_time: float = 0.0
        self._timer_id = None  # after() callback ID
        self._animation_id = None  # after() callback ID for processing dots
        self._animation_frame: int = 0
        self._running = False
        self._ready_event = threading.Event()  # Signals T4 is initialized

    @property
    def is_running(self) -> bool:
        """Whether the overlay is active and visible."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the overlay on a dedicated thread (T4).

        Safe to call multiple times; does nothing if already running.
        Blocks briefly (up to 2 seconds) until the window is created,
        to ensure update_state() can be called immediately after.
        """
        if self.is_running:
            logger.debug("Overlay already running.")
            return

        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_overlay,
            daemon=True,
            name="overlay-T4",
        )
        self._thread.start()

        # Wait for T4 to finish window creation
        if not self._ready_event.wait(timeout=2.0):
            logger.warning("Overlay thread did not signal ready within 2s.")

    def stop(self) -> None:
        """Stop the overlay and destroy the window.

        Safe to call from any thread. Safe to call if not running.
        """
        if not self._running:
            return

        self._running = False

        if self._root is not None:
            try:
                # root.quit() is thread-safe in Tcl
                self._root.quit()
            except Exception:
                logger.debug("Error calling root.quit() on overlay.")

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("Overlay thread did not exit within 3s.")

        self._thread = None
        logger.info("Overlay stopped.")

    def update_state(self, new_state: AppState) -> None:
        """Update the overlay to reflect a new application state.

        Thread-safe. Marshals the update to T4 via root.event_generate().

        If the new state is IDLE, the overlay is hidden (withdrawn).
        For RECORDING, PROCESSING, and SPEAKING, the overlay is shown
        with state-appropriate visuals.

        Args:
            new_state: The new application state.
        """
        if not self._running or self._root is None:
            return

        self._current_state = new_state

        try:
            # Use event_generate with a virtual event to marshal to T4.
            # This is thread-safe in Tcl 8.6+ (which Python 3.11+ bundles).
            self._root.event_generate("<<StateChanged>>", when="tail")
        except Exception:
            # Window may have been destroyed between the check and the call.
            logger.debug("Failed to generate state-change event on overlay.")

    # --- Internal methods (T4 only) ---

    def _run_overlay(self) -> None:
        """Main function for T4. Creates window and runs mainloop.

        This method runs entirely on the overlay thread (T4).
        It creates a Tk root (hidden), a Toplevel overlay window,
        applies Win32 extended styles, and enters the Tcl event loop.
        """
        try:
            import tkinter as tk

            self._root = tk.Tk()
            self._root.withdraw()  # Hide the helper root window

            self._create_window(tk)
            self._apply_win32_styles()

            # Bind the virtual event for cross-thread state updates
            self._root.bind("<<StateChanged>>", self._on_state_changed)

            self._running = True
            self._ready_event.set()

            logger.info("Overlay window created and ready on T4.")

            # Enter the Tcl event loop (blocks until root.quit())
            self._root.mainloop()

        except Exception:
            logger.exception("Overlay thread crashed.")
            self._running = False
            self._ready_event.set()  # Unblock start() even on failure

        finally:
            # Cleanup
            self._cancel_timers()
            try:
                if self._root is not None:
                    self._root.destroy()
            except Exception:
                pass
            self._root = None
            self._window = None
            self._running = False
            logger.info("Overlay thread exited.")

    def _create_window(self, tk) -> None:
        """Create the overlay Toplevel window with all widgets.

        Args:
            tk: The tkinter module reference.
        """
        # Calculate position
        _, _, wa_right, wa_bottom = _get_work_area()
        x = wa_right - OVERLAY_WIDTH - OVERLAY_MARGIN_RIGHT
        y = wa_bottom - OVERLAY_HEIGHT - OVERLAY_MARGIN_BOTTOM

        # Create the overlay as a Toplevel (root is hidden)
        self._window = tk.Toplevel(self._root)
        self._window.overrideredirect(True)
        self._window.attributes("-topmost", True)
        self._window.geometry(
            f"{OVERLAY_WIDTH}x{OVERLAY_HEIGHT}+{x}+{y}"
        )
        self._window.configure(bg=OVERLAY_BG_COLOR)

        # Prevent the window from appearing in taskbar
        self._window.attributes("-toolwindow", True)

        # --- Layout ---
        # [ DOT ] [ STATE_LABEL ] [ TIME_LABEL ]
        # All in a horizontal row, vertically centered.

        frame = tk.Frame(
            self._window, bg=OVERLAY_BG_COLOR, padx=14, pady=12
        )
        frame.pack(fill="both", expand=True)

        # Colored dot indicator (Unicode circle)
        self._dot_label = tk.Label(
            frame,
            text="\u25CF",  # Filled circle
            font=("Segoe UI", 16),
            fg="#e63232",
            bg=OVERLAY_BG_COLOR,
        )
        self._dot_label.pack(side="left", padx=(0, 8))

        # State label ("Recording", "Processing...", etc.)
        self._state_label = tk.Label(
            frame,
            text="Recording",
            font=("Segoe UI", 12),
            fg="#e0e0e0",
            bg=OVERLAY_BG_COLOR,
            anchor="w",
        )
        self._state_label.pack(side="left", fill="x", expand=True)

        # Timer label (right-aligned, shown during RECORDING)
        self._time_label = tk.Label(
            frame,
            text="00:00",
            font=("Segoe UI Semibold", 13),
            fg="#e0e0e0",
            bg=OVERLAY_BG_COLOR,
            anchor="e",
        )
        self._time_label.pack(side="right", padx=(8, 0))

        # Force geometry so HWND exists before we apply Win32 styles
        self._window.update_idletasks()

        # Start hidden -- only shown when state != IDLE
        self._window.withdraw()

    def _apply_win32_styles(self) -> None:
        """Apply Win32 extended window styles for focus/click/taskbar behavior.

        MUST be called after _create_window() and update_idletasks().
        """
        try:
            hwnd = _user32.GetParent(self._window.winfo_id())
            if not hwnd:
                logger.warning("Could not get HWND for overlay window.")
                return

            current_ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            new_ex = (
                current_ex
                | WS_EX_NOACTIVATE
                | WS_EX_TOPMOST
                | WS_EX_TRANSPARENT
                | WS_EX_TOOLWINDOW
                | WS_EX_LAYERED
            )

            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex)

            # Set window opacity
            _user32.SetLayeredWindowAttributes(
                hwnd, 0, OVERLAY_BG_ALPHA, LWA_ALPHA
            )

            logger.info(
                "Win32 overlay styles applied: NOACTIVATE | TOPMOST | "
                "TRANSPARENT | TOOLWINDOW | LAYERED (alpha=%d)",
                OVERLAY_BG_ALPHA,
            )
        except Exception:
            logger.exception("Failed to apply Win32 overlay styles.")

    def _on_state_changed(self, event=None) -> None:
        """Handle the <<StateChanged>> virtual event on T4.

        Routes to the appropriate display method based on current state.

        Args:
            event: The tkinter event (unused).
        """
        state = self._current_state
        self._cancel_timers()

        if state == AppState.IDLE:
            self._show_idle()
        elif state == AppState.RECORDING:
            self._show_recording()
        elif state == AppState.PROCESSING:
            self._show_processing()
        elif state == AppState.SPEAKING:
            self._show_speaking()
        elif state == AppState.PASTING:
            # PASTING is very brief (<200ms). Show a quick flash then hide.
            self._show_pasting()

    def _show_idle(self) -> None:
        """Hide the overlay (IDLE state)."""
        if self._window is not None:
            self._window.withdraw()

    def _show_recording(self) -> None:
        """Show recording indicator with elapsed time counter."""
        self._recording_start_time = time.monotonic()

        self._dot_label.configure(fg=STATE_COLORS[AppState.RECORDING])
        self._state_label.configure(text=STATE_LABELS[AppState.RECORDING])
        self._time_label.configure(text="00:00")
        self._time_label.pack(side="right", padx=(8, 0))  # Ensure visible

        if self._window is not None:
            self._window.deiconify()

        # Start the 1-second timer tick
        self._tick_recording_timer()

    def _show_processing(self) -> None:
        """Show processing indicator with animated dots."""
        self._dot_label.configure(fg=STATE_COLORS[AppState.PROCESSING])
        self._state_label.configure(text=STATE_LABELS[AppState.PROCESSING])
        self._time_label.pack_forget()  # Hide timer during processing

        if self._window is not None:
            self._window.deiconify()

        # Start dot animation
        self._animation_frame = 0
        self._animate_processing()

    def _show_speaking(self) -> None:
        """Show speaking indicator."""
        self._dot_label.configure(fg=STATE_COLORS[AppState.SPEAKING])
        self._state_label.configure(text=STATE_LABELS[AppState.SPEAKING])
        self._time_label.pack_forget()  # Hide timer during speaking

        if self._window is not None:
            self._window.deiconify()

        # Start pulsing animation (dot opacity cycle)
        self._animation_frame = 0
        self._animate_speaking()

    def _show_pasting(self) -> None:
        """Show brief pasting indicator, then auto-hide."""
        self._dot_label.configure(fg="#32c850")  # Green
        self._state_label.configure(text="Pasted")
        self._time_label.pack_forget()

        if self._window is not None:
            self._window.deiconify()

        # Auto-hide after 800ms
        self._timer_id = self._root.after(800, self._show_idle)

    def _tick_recording_timer(self) -> None:
        """Update recording elapsed time. Called every 1s on T4."""
        if self._current_state != AppState.RECORDING:
            return

        elapsed = time.monotonic() - self._recording_start_time
        minutes = int(elapsed) // 60
        seconds = int(elapsed) % 60
        self._time_label.configure(text=f"{minutes:02d}:{seconds:02d}")

        self._timer_id = self._root.after(1000, self._tick_recording_timer)

    def _animate_processing(self) -> None:
        """Animate processing dots (cycling: . .. ...). Called every 500ms."""
        if self._current_state != AppState.PROCESSING:
            return

        dots = "." * ((self._animation_frame % 3) + 1)
        self._state_label.configure(
            text=f"Processing{dots}".ljust(len("Processing..."))
        )
        self._animation_frame += 1

        self._animation_id = self._root.after(500, self._animate_processing)

    def _animate_speaking(self) -> None:
        """Pulse the speaking dot between bright and dim. Called every 600ms."""
        if self._current_state != AppState.SPEAKING:
            return

        # Alternate between bright blue and dim blue
        colors = ["#4682e6", "#2a5298"]
        color = colors[self._animation_frame % 2]
        self._dot_label.configure(fg=color)
        self._animation_frame += 1

        self._animation_id = self._root.after(600, self._animate_speaking)

    def _cancel_timers(self) -> None:
        """Cancel any pending after() callbacks."""
        if self._timer_id is not None and self._root is not None:
            try:
                self._root.after_cancel(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

        if self._animation_id is not None and self._root is not None:
            try:
                self._root.after_cancel(self._animation_id)
            except Exception:
                pass
            self._animation_id = None
```

### Public API Summary

| Method | Thread | Description |
|--------|--------|-------------|
| `__init__()` | Any | Initialize. No window created. |
| `start()` | Any | Spawn T4, create window, enter mainloop. Blocks up to 2s for readiness. |
| `stop()` | Any | Quit mainloop, destroy window, join T4. |
| `update_state(state)` | Any | Thread-safe state change notification. |
| `is_running` | Any | Property: whether overlay is active. |

---

## 10. Integration Points with Existing Code

### 10.1 main.py -- VoicePasteApp.__init__()

```python
# After TrayManager initialization:
from overlay import OverlayWindow

# Create overlay (does not start the window yet)
self._overlay: Optional[OverlayWindow] = None
if config.show_overlay:
    self._overlay = OverlayWindow()
```

### 10.2 main.py -- VoicePasteApp.run()

```python
# After hotkey registration, before tray run:
if self._overlay is not None:
    self._overlay.start()
    logger.info("Overlay window started.")
```

### 10.3 main.py -- VoicePasteApp._set_state()

```python
def _set_state(self, new_state: AppState) -> None:
    with self._state_lock:
        old_state = self._state
        self._state = new_state
        logger.info("State: %s -> %s", old_state.value, new_state.value)

    # Update tray icon
    self._tray_manager.update_state(new_state)

    # Update overlay (thread-safe, no-op if overlay is None or not running)
    if self._overlay is not None:
        self._overlay.update_state(new_state)
```

### 10.4 main.py -- VoicePasteApp._shutdown()

```python
# Before unregistering hotkeys:
if self._overlay is not None:
    self._overlay.stop()
```

### 10.5 main.py -- VoicePasteApp._on_settings_saved()

```python
# Hot-reload overlay visibility:
if "show_overlay" in changed_fields:
    if self.config.show_overlay:
        if self._overlay is None:
            self._overlay = OverlayWindow()
        if not self._overlay.is_running:
            self._overlay.start()
        logger.info("Overlay enabled via settings.")
    else:
        if self._overlay is not None:
            self._overlay.stop()
            self._overlay = None
        logger.info("Overlay disabled via settings.")
```

### 10.6 config.py -- AppConfig

Add one new field:

```python
# --- v0.8: Overlay fields ---
show_overlay: bool = True
```

### 10.7 config.py -- CONFIG_TEMPLATE and save_to_toml()

In CONFIG_TEMPLATE under `[feedback]`:
```toml
[feedback]
audio_cues = true
# Show floating overlay window with state feedback (default: true)
show_overlay = true
```

In `save_to_toml()` under `[feedback]`:
```python
[feedback]
audio_cues = {str(self.audio_cues_enabled).lower()}
show_overlay = {str(self.show_overlay).lower()}
```

### 10.8 config.py -- load_config()

```python
show_overlay = feedback_section.get("show_overlay", True)
```

Pass to AppConfig constructor:
```python
config = AppConfig(
    ...
    show_overlay=bool(show_overlay),
)
```

### 10.9 constants.py

No new constants needed. The overlay constants (dimensions, colors, margins)
are module-local to `overlay.py` because they are not shared with other modules.

### 10.10 settings_dialog.py

Add a checkbox in the Feedback tab:

```python
# Overlay checkbox
self._show_overlay_var = tk.BooleanVar(value=config.show_overlay)
ttk.Checkbutton(
    feedback_frame,
    text="Show floating overlay (Recording/Processing/Speaking indicator)",
    variable=self._show_overlay_var,
).pack(anchor="w", padx=20, pady=4)
```

On save, include in changed_fields:
```python
if self._show_overlay_var.get() != original_config.show_overlay:
    config.show_overlay = self._show_overlay_var.get()
    changed["show_overlay"] = config.show_overlay
```

---

## 11. Configuration Schema Extensions

### config.toml changes

```toml
[feedback]
# Play audio cues on recording start/stop (default: true)
audio_cues = true
# Show floating overlay window with state feedback (default: true)
show_overlay = true
```

### AppConfig dataclass addition

```python
@dataclass
class AppConfig:
    # ... existing fields ...

    # --- v0.8: Overlay ---
    show_overlay: bool = True
```

No API keys or secrets involved. No keyring interaction needed.

---

## 12. Threading Diagram

### Full thread lifecycle (v0.8)

```
                        Application Lifecycle
Time  ======================================================================>

T0 (Main)
  |--- load_config() ---|
  |--- VoicePasteApp()  |
  |     |                |
  |     |-- create TrayManager
  |     |-- create OverlayWindow (no thread yet)
  |     |-- create HotkeyManager
  |     |
  |     |-- app.run()
  |     |   |
  |     |   |-- register hotkeys (keyboard starts T1 internally)
  |     |   |
  |     |   |-- overlay.start() -----> spawns T4
  |     |   |                             |
  |     |   |                             |-- Tk() + Toplevel
  |     |   |                             |-- apply Win32 styles
  |     |   |                             |-- mainloop() ------+
  |     |   |                             |     ^              |
  |     |   |-- tray.run()                |     |              |
  |     |   |    |                        |     |              |
  |     |   |    |-- pystray mainloop --->| ... | ... (blocks) |
  |     |   |    |                        |     |              |
  |     |   |    :                        |     |              |

T1 (keyboard)  [daemon, auto-started by keyboard lib]
  |--- listening for hotkey events ------------------------------------------>
  |
  | (hotkey pressed) --> VoicePasteApp._on_hotkey()
  |                        |
  |                        |-- _set_state(RECORDING)
  |                        |     |-- tray.update_state(RECORDING)
  |                        |     |-- overlay.update_state(RECORDING) --+
  |                        |                                           |
  |                        |                                     [event_generate
  |                        |                                      on T4's root]
  |                        |                                           |
  |                        |                                           v
  |                        |                              T4: _on_state_changed()
  |                        |                                   -> _show_recording()
  |                        |                                   -> _tick_timer() @ 1s
  |                        |
  | (hotkey pressed again) --> _set_state(PROCESSING)
  |                              |-- overlay.update_state(PROCESSING)
  |                              |                                           |
  |                              |-- spawn T2 (pipeline) -----> T2 starts
  |                              |                                |
  |                              |                          T4: _show_processing()
  |                              |                               -> animate dots
  |                              |
  |                              T2: STT -> Summarize -> Paste
  |                              T2: _set_state(PASTING)
  |                              |     |-- overlay.update_state(PASTING)
  |                              |                                           |
  |                              |                          T4: _show_pasting()
  |                              |                               -> auto-hide 800ms
  |                              |
  |                              T2: _set_state(IDLE)
  |                                    |-- overlay.update_state(IDLE)
  |                                                                          |
  |                                                         T4: _show_idle()
  |                                                              -> withdraw()

T3 (settings)  [spawned on demand, ephemeral]
  |--- (user opens Settings) --->
  |      Tk() + dialog + mainloop()
  |      (user changes show_overlay checkbox and saves)
  |      --> _on_settings_saved({"show_overlay": False})
  |           --> overlay.stop()  (stops T4)
  |      (or {"show_overlay": True})
  |           --> overlay.start() (starts new T4)
  |--- (dialog closes, thread exits)

Shutdown:
  T0: _shutdown()
        |-- overlay.stop() --> T4: root.quit() --> mainloop exits --> thread exits
        |-- hotkey.unregister() --> T1: (keyboard library cleans up)
        |-- tray.stop() --> T0: pystray mainloop exits
```

### Cross-thread communication for state updates

```
T1/T2 (caller)                    T4 (overlay tkinter)
     |                                   |
     |-- overlay.update_state(RECORDING) |
     |     |                             |
     |     |-- self._current_state = RECORDING
     |     |-- root.event_generate("<<StateChanged>>", when="tail")
     |     |                             |
     |     |                      [Tcl event queue]
     |     |                             |
     |     |                      _on_state_changed()
     |     |                        |-- read self._current_state
     |     |                        |-- _show_recording()
     |     |                        |-- update labels, show window
```

**Why `event_generate` and not `root.after()`?**

`root.after(0, callback)` is also thread-safe in Tcl 8.6, but `event_generate`
with a virtual event is the canonical pattern for cross-thread signaling in
tkinter. It allows us to bind a single handler to `<<StateChanged>>` rather
than creating a lambda or closure for each state update. Both approaches work;
`event_generate` is slightly cleaner because the state is stored in
`self._current_state` (an atomic write under the GIL) and the event handler
reads it, avoiding closure capture of stale state values.

---

## 13. Risk Assessment

### Risk 1: Focus stealing despite WS_EX_NOACTIVATE (CRITICAL)

**Risk**: Some edge case causes the overlay to steal focus -- for example,
tkinter internally calling `SetForegroundWindow()` or `BringWindowToTop()`
during widget updates, or `deiconify()` activating the window.

**Probability**: Low. WS_EX_NOACTIVATE is the Windows-documented mechanism to
prevent activation. tkinter's `deiconify()` calls `ShowWindow(SW_NORMAL)`,
but with WS_EX_NOACTIVATE, this shows the window without activating it.

**Mitigation**:
1. Apply WS_EX_NOACTIVATE before any `deiconify()` call.
2. After every `deiconify()`, verify with `GetForegroundWindow()` that the
   foreground window did not change. Log a warning if it did. (Debug builds
   only -- this adds a syscall per state change.)
3. Integration test: script that checks `GetForegroundWindow()` before and
   after each state transition and asserts they are equal.
4. Fallback: if focus stealing is detected in testing, switch to raw Win32
   `CreateWindowExW` with WS_EX_NOACTIVATE at creation time (eliminates the
   tkinter creation-then-patch window).

### Risk 2: DPI scaling mismatch

**Risk**: On high-DPI displays (150%, 200%), the overlay appears at the wrong
position or the wrong size.

**Probability**: Medium. Python 3.11+ sets per-monitor DPI awareness, and
tkinter adjusts coordinates, but inconsistencies between `SystemParametersInfoW`
(returns physical pixels) and tkinter geometry (may use scaled coordinates)
are possible.

**Mitigation**:
1. Test on 100%, 125%, 150%, and 200% scaling.
2. If misalignment is found, use `ctypes.windll.shcore.GetScaleFactorForMonitor()`
   to get the scaling factor and adjust coordinates manually.
3. Alternatively, use tkinter's `winfo_screenwidth/height` for positioning
   (which returns values in tkinter's coordinate system) instead of Win32 API.

### Risk 3: Two Tcl interpreters in one process

**Risk**: Running two independent `Tk()` instances (one for Settings on T3,
one for Overlay on T4) causes crashes or undefined behavior.

**Probability**: Very Low. Tcl 8.6 is designed for multi-threaded use. Each
`Tk()` creates an independent Tcl interpreter. The critical rule is: never
access Tcl interpreter A from thread B. Our architecture enforces this by
design -- T3 only touches its own Tk(), T4 only touches its own Tk().

**Mitigation**:
1. This is the same pattern used by the existing Settings dialog -- it works
   today and has been tested.
2. Ensure no shared tkinter variables (StringVar, IntVar) across interpreters.

### Risk 4: Overlay thread does not exit on shutdown

**Risk**: `root.quit()` does not interrupt `mainloop()`, causing the overlay
thread to hang and prevent process exit.

**Probability**: Low. `root.quit()` sets a flag in the Tcl interpreter that
causes `mainloop()` to return on the next event loop iteration. As a daemon
thread, it will be terminated by the Python interpreter on process exit even
if it hangs.

**Mitigation**:
1. Use `self._thread.join(timeout=3.0)` to detect hangs.
2. Log a warning if the join times out.
3. Daemon thread flag ensures process exit is not blocked.

### Risk 5: Race condition in `update_state()`

**Risk**: Two rapid state changes (e.g., PROCESSING -> PASTING -> IDLE in
<100ms) result in `_current_state` being read as IDLE in `_on_state_changed()`
when the handler was triggered by the PASTING event.

**Probability**: Low but possible. The GIL ensures atomic assignment of
`self._current_state`, and `event_generate("<<StateChanged>>", when="tail")`
appends to the Tcl event queue. If two events are queued before the first is
processed, the handler will be called twice, both times reading the final state
(IDLE), and calling `_show_idle()` twice (which is idempotent -- withdrawing
an already-withdrawn window is a no-op).

**Impact**: The PASTING visual may be skipped entirely (user never sees the
green "Pasted" indicator). This is acceptable because the PASTING state is
already very brief (<200ms).

**Mitigation**: No action needed. Skipping a transient visual for a 200ms
state is acceptable UX. If precise state tracking is needed in the future,
switch to a thread-safe queue of state changes (but this adds complexity for
minimal benefit).

### Risk 6: Overlay visible but stale after crash

**Risk**: If the pipeline thread (T2) crashes without returning to IDLE, the
overlay stays visible showing "Processing..." indefinitely.

**Probability**: Very Low. The pipeline's `finally` block always calls
`_set_state(AppState.IDLE)`. A truly catastrophic crash (segfault in native
code) would kill the process entirely.

**Mitigation**: The existing error handling in `_run_pipeline()` already
ensures `_set_state(AppState.IDLE)` is called in the `finally` block, covering
all Python-level exceptions. No additional mitigation needed.

---

## 14. Visual Design Specification

### Layout (200 x 56 pixels)

```
+----------------------------------------------+
|  [14px pad]                         [14px pad]|
|                                               |
|   (RED DOT)  Recording          00:05         |
|                                               |
|  [14px pad]                         [14px pad]|
+----------------------------------------------+
```

### State visuals

| State | Dot Color | Label Text | Timer | Animation |
|-------|-----------|------------|-------|-----------|
| RECORDING | #e63232 (red) | "Recording" | "MM:SS" counting up | Steady dot |
| PROCESSING | #f0c828 (amber) | "Processing..." | Hidden | Dots cycle (. .. ...) every 500ms |
| SPEAKING | #4682e6 (blue) | "Speaking..." | Hidden | Dot pulses bright/dim every 600ms |
| PASTING | #32c850 (green) | "Pasted" | Hidden | Static, auto-hides after 800ms |
| IDLE | N/A | N/A | N/A | Window withdrawn (hidden) |

### Colors and fonts

- Background: #1c1c1c (matches Settings dialog dark theme)
- Text: #e0e0e0 (light grey, high contrast on dark)
- Font: "Segoe UI" 12pt for label, "Segoe UI Semibold" 13pt for timer
- Window opacity: 86% (alpha=220 out of 255)

### Positioning

- Bottom-right corner of primary monitor
- 20px from right edge, 20px above taskbar top edge
- Calculated using `SystemParametersInfoW(SPI_GETWORKAREA)`

---

## 15. Implementation Plan

### Phase 1: Minimal overlay (RECORDING state only)

1. Create `src/overlay.py` with `OverlayWindow` class.
2. Implement `start()`, `stop()`, `update_state()`.
3. Implement `_show_recording()` with timer.
4. Apply Win32 extended styles.
5. Integrate into `main.py` (`_set_state()`, `__init__()`, `_shutdown()`).
6. Add `show_overlay` to `config.py` and `constants.py`.
7. **Test**: Verify overlay appears during recording, shows timer, hides on IDLE.
8. **Test**: Verify NO focus stealing (open Notepad, type, trigger recording,
   verify typing is not interrupted).

### Phase 2: PROCESSING and SPEAKING states

1. Implement `_show_processing()` with dot animation.
2. Implement `_show_speaking()` with pulse animation.
3. Implement `_show_pasting()` with auto-hide.
4. **Test**: Full pipeline through all states.

### Phase 3: Hot-reload and Settings integration

1. Add checkbox to Settings dialog.
2. Implement hot-reload in `_on_settings_saved()`.
3. Update `save_to_toml()` and `load_config()`.
4. **Test**: Toggle overlay on/off in Settings without restart.

### Phase 4: Polish and edge cases

1. DPI testing on 125%, 150%, 200% scaling.
2. Multi-monitor testing (overlay should appear on primary monitor).
3. Focus-steal regression testing across Chrome, VS Code, Notepad, Word.
4. Memory profiling (T4 thread footprint).

### Estimated effort: 3-4 developer days

| Phase | Effort | Risk |
|-------|--------|------|
| Phase 1: Core overlay | 1 day | Medium (Win32 style patching) |
| Phase 2: All states | 0.5 day | Low |
| Phase 3: Settings integration | 0.5 day | Low |
| Phase 4: Polish and testing | 1-2 days | Medium (DPI, focus edge cases) |

---

## 16. Trade-offs Summary

| Trade-off | Decision | Rationale |
|-----------|----------|-----------|
| tkinter vs raw Win32 | tkinter + ctypes style patching | Reuses existing dependency, 50% less code, proven pattern in project. Marginal risk of focus-steal from tkinter's internal ShowWindow call, mitigated by applying WS_EX_NOACTIVATE before first deiconify. |
| Dedicated thread vs shared thread | Dedicated T4 | Overlay lifecycle is independent of Settings dialog. Clean start/stop for hot-reload. |
| Observer pattern vs direct calls | Direct calls | Only 2 observers (tray + overlay). Observer pattern adds abstraction without proportional benefit. Easy to refactor if v0.9 adds more. |
| `event_generate` vs `root.after` | `event_generate` | Canonical cross-thread pattern. Avoids closure capture issues. Both work; event_generate is slightly cleaner. |
| Animated GIF/canvas vs label text | Label text + Unicode symbols | Simpler. No image assets to bundle. Unicode filled circle (\u25CF) renders consistently on Windows 10/11 with Segoe UI. |
| Per-pixel alpha vs window alpha | Window alpha (LWA_ALPHA) | Per-pixel alpha requires UpdateLayeredWindow with a premultiplied ARGB bitmap -- significantly more complex. Window-level alpha (SetLayeredWindowAttributes) is sufficient for a uniform dark background. |
| Multi-monitor positioning vs primary-only | Primary monitor only (v0.8) | Simpler. Multi-monitor is a v0.9 enhancement. Most users have the taskbar on their primary monitor. |
