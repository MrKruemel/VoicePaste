"""Floating overlay window for real-time state feedback.

v0.8: Provides visual indicators for RECORDING (timer), PROCESSING
(animation), and SPEAKING (indicator) states.

Threading model:
    The overlay runs its own tkinter Tk() root and mainloop() on a
    dedicated daemon thread (T4). All widget modifications happen on
    T4 via event_generate(). External threads communicate state
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
        return (
            self._running
            and self._thread is not None
            and self._thread.is_alive()
        )

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
        Blocks until the overlay thread has fully exited and the Tcl
        interpreter is destroyed, to prevent dual-Tk() conflicts when
        the settings dialog creates its own Tk() root shortly after.
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
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Overlay thread did not exit within 5s.")

        # Ensure Tcl interpreter is fully gone before another Tk() is created
        self._root = None
        self._window = None
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

        Layout: [DOT] [STATE_LABEL] [TIME_LABEL]
        The window is created at the bottom-right of the primary monitor
        work area, positioned above the taskbar.

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

        Applies WS_EX_NOACTIVATE, WS_EX_TOPMOST, WS_EX_TRANSPARENT,
        WS_EX_TOOLWINDOW, and WS_EX_LAYERED via SetWindowLongW. Also
        sets the window opacity via SetLayeredWindowAttributes.

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
        """Show recording indicator with elapsed time counter.

        Displays a red dot, "Recording" label, and a MM:SS timer
        that updates every second via root.after().
        """
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
        """Show processing indicator with animated dots.

        Displays an amber dot and "Processing..." label with
        cycling dots (. .. ...) every 500ms.
        """
        self._dot_label.configure(fg=STATE_COLORS[AppState.PROCESSING])
        self._state_label.configure(text=STATE_LABELS[AppState.PROCESSING])
        self._time_label.pack_forget()  # Hide timer during processing

        if self._window is not None:
            self._window.deiconify()

        # Start dot animation
        self._animation_frame = 0
        self._animate_processing()

    def _show_speaking(self) -> None:
        """Show speaking indicator with pulsing dot.

        Displays a blue dot and "Speaking..." label. The dot
        pulses between bright and dim blue every 600ms.
        """
        self._dot_label.configure(fg=STATE_COLORS[AppState.SPEAKING])
        self._state_label.configure(text=STATE_LABELS[AppState.SPEAKING])
        self._time_label.pack_forget()  # Hide timer during speaking

        if self._window is not None:
            self._window.deiconify()

        # Start pulsing animation (dot opacity cycle)
        self._animation_frame = 0
        self._animate_speaking()

    def _show_pasting(self) -> None:
        """Show brief pasting indicator, then auto-hide.

        Displays a green dot and "Pasted" label. Auto-hides
        after 800ms by scheduling _show_idle via root.after().
        """
        self._dot_label.configure(fg="#32c850")  # Green
        self._state_label.configure(text="Pasted")
        self._time_label.pack_forget()

        if self._window is not None:
            self._window.deiconify()

        # Auto-hide after 800ms
        self._timer_id = self._root.after(800, self._show_idle)

    def _tick_recording_timer(self) -> None:
        """Update recording elapsed time. Called every 1s on T4.

        Calculates the elapsed time since recording started and
        updates the timer label. Schedules itself again via
        root.after(1000) until the state leaves RECORDING.
        """
        if self._current_state != AppState.RECORDING:
            return  # Stop ticking when no longer RECORDING

        elapsed = time.monotonic() - self._recording_start_time
        minutes = int(elapsed) // 60
        seconds = int(elapsed) % 60
        self._time_label.configure(text=f"{minutes:02d}:{seconds:02d}")

        # Schedule next tick in 1 second
        self._timer_id = self._root.after(1000, self._tick_recording_timer)

    def _animate_processing(self) -> None:
        """Animate processing dots (cycling: . .. ...). Called every 500ms.

        Updates the state label text to cycle through "Processing.",
        "Processing..", and "Processing..." to indicate ongoing work.
        """
        if self._current_state != AppState.PROCESSING:
            return

        dots = "." * ((self._animation_frame % 3) + 1)
        self._state_label.configure(
            text=f"Processing{dots}".ljust(len("Processing..."))
        )
        self._animation_frame += 1

        self._animation_id = self._root.after(500, self._animate_processing)

    def _animate_speaking(self) -> None:
        """Pulse the speaking dot between bright and dim. Called every 600ms.

        Alternates the dot color between bright blue (#4682e6) and
        dim blue (#2a5298) to indicate ongoing TTS playback.
        """
        if self._current_state != AppState.SPEAKING:
            return

        # Alternate between bright blue and dim blue
        colors = ["#4682e6", "#2a5298"]
        color = colors[self._animation_frame % 2]
        self._dot_label.configure(fg=color)
        self._animation_frame += 1

        self._animation_id = self._root.after(600, self._animate_speaking)

    def _cancel_timers(self) -> None:
        """Cancel any pending after() callbacks.

        Called before every state transition to ensure that timers
        from a previous state do not fire unexpectedly.
        """
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
