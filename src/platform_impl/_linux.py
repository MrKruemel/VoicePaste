"""Linux platform implementations for VoicePaste.

Provides clipboard (xclip/wl-clipboard), keystroke simulation
(evdev UInput/xdotool/ydotool/wtype), single-instance locking (fcntl),
error dialogs (zenity/tkinter), and audio beeps (sounddevice sine waves).

On Wayland, keystroke simulation uses evdev UInput as the preferred
method (no external tools needed), falling back to ydotool or wtype.
On X11, xdotool is used.

This module is only imported on Linux (sys.platform == "linux").
Target: Ubuntu 22.04 LTS and 24.04 LTS.
"""

import fcntl
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from constants import PASTE_DELAY_MS

# SEC-070: Maximum clipboard content size to prevent memory issues
_MAX_CLIPBOARD_BYTES = 1 * 1024 * 1024  # 1 MB

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session type detection
# ---------------------------------------------------------------------------

def _detect_session_type() -> str:
    """Detect X11 vs Wayland from XDG_SESSION_TYPE."""
    return os.environ.get("XDG_SESSION_TYPE", "x11")


# ---------------------------------------------------------------------------
# Clipboard tool detection (cached)
# ---------------------------------------------------------------------------

# Module-level cache so tool lookup + fallback logging happens only once.
_clipboard_read_cache: dict[str, tuple[list[str], str] | None] = {}
_clipboard_write_cache: dict[str, tuple[list[str], str] | None] = {}


def _find_clipboard_read_tool(session: str) -> tuple[list[str], str] | None:
    """Find the best clipboard read command for the current session.

    Results are cached per session type so fallback logging fires only once.

    On Wayland, prefers wl-paste but falls back to xclip or xsel (via
    XWayland). On X11, uses xclip or xsel.

    Args:
        session: Session type string ("wayland" or "x11").

    Returns:
        A tuple of (command_list, tool_name) or None if no tool is found.
    """
    if session in _clipboard_read_cache:
        return _clipboard_read_cache[session]

    result = _detect_clipboard_read_tool(session)
    _clipboard_read_cache[session] = result
    return result


def _detect_clipboard_read_tool(session: str) -> tuple[list[str], str] | None:
    """Perform the actual clipboard read tool detection (uncached).

    Args:
        session: Session type string ("wayland" or "x11").

    Returns:
        A tuple of (command_list, tool_name) or None if no tool is found.
    """
    if session == "wayland":
        tool = shutil.which("wl-paste")
        if tool:
            logger.info("Clipboard read tool: wl-paste (native Wayland).")
            return ([tool, "--no-newline"], "wl-paste")
        # Fallback: xclip via XWayland
        tool = shutil.which("xclip")
        if tool:
            logger.info(
                "wl-paste not found; falling back to xclip via XWayland. "
                "Install wl-clipboard for native Wayland clipboard support: "
                "sudo apt install wl-clipboard"
            )
            return ([tool, "-selection", "clipboard", "-o"], "xclip (XWayland fallback)")
        tool = shutil.which("xsel")
        if tool:
            logger.info(
                "wl-paste not found; falling back to xsel via XWayland. "
                "Install wl-clipboard for native Wayland clipboard support: "
                "sudo apt install wl-clipboard"
            )
            return ([tool, "--clipboard", "-o"], "xsel (XWayland fallback)")
        logger.warning(
            "No clipboard read tool found. Install wl-clipboard (Wayland) "
            "or xclip (X11/XWayland): sudo apt install wl-clipboard xclip"
        )
        return None
    else:
        tool = shutil.which("xclip")
        if tool:
            return ([tool, "-selection", "clipboard", "-o"], "xclip")
        tool = shutil.which("xsel")
        if tool:
            logger.info("xclip not found; using xsel for clipboard read.")
            return ([tool, "--clipboard", "-o"], "xsel")
        logger.warning(
            "No clipboard read tool found. "
            "Install xclip or xsel: sudo apt install xclip"
        )
        return None


def _find_clipboard_write_tool(session: str) -> tuple[list[str], str] | None:
    """Find the best clipboard write command for the current session.

    Results are cached per session type so fallback logging fires only once.

    On Wayland, prefers wl-copy but falls back to xclip or xsel (via
    XWayland). On X11, uses xclip or xsel.

    Args:
        session: Session type string ("wayland" or "x11").

    Returns:
        A tuple of (command_list, tool_name) or None if no tool is found.
    """
    if session in _clipboard_write_cache:
        return _clipboard_write_cache[session]

    result = _detect_clipboard_write_tool(session)
    _clipboard_write_cache[session] = result
    return result


def _detect_clipboard_write_tool(session: str) -> tuple[list[str], str] | None:
    """Perform the actual clipboard write tool detection (uncached).

    Args:
        session: Session type string ("wayland" or "x11").

    Returns:
        A tuple of (command_list, tool_name) or None if no tool is found.
    """
    if session == "wayland":
        tool = shutil.which("wl-copy")
        if tool:
            logger.info("Clipboard write tool: wl-copy (native Wayland).")
            return ([tool], "wl-copy")
        # Fallback: xclip via XWayland
        tool = shutil.which("xclip")
        if tool:
            logger.info(
                "wl-copy not found; falling back to xclip via XWayland. "
                "Install wl-clipboard for native Wayland clipboard support: "
                "sudo apt install wl-clipboard"
            )
            return (
                [tool, "-selection", "clipboard", "-i"],
                "xclip (XWayland fallback)",
            )
        # Fallback: xsel via XWayland
        tool = shutil.which("xsel")
        if tool:
            logger.info(
                "wl-copy not found; falling back to xsel via XWayland. "
                "Install wl-clipboard for native Wayland clipboard support: "
                "sudo apt install wl-clipboard"
            )
            return (
                [tool, "--clipboard", "-i"],
                "xsel (XWayland fallback)",
            )
        logger.warning(
            "No clipboard write tool found. Install wl-clipboard (Wayland) "
            "or xclip (X11/XWayland): sudo apt install wl-clipboard xclip"
        )
        return None
    else:
        tool = shutil.which("xclip")
        if tool:
            return ([tool, "-selection", "clipboard", "-i"], "xclip")
        # Fallback: xsel
        tool = shutil.which("xsel")
        if tool:
            logger.info("xclip not found; using xsel for clipboard write.")
            return ([tool, "--clipboard", "-i"], "xsel")
        logger.error(
            "No clipboard write tool found. "
            "Install xclip or xsel: sudo apt install xclip"
        )
        return None


def clipboard_backup() -> str | None:
    """Read clipboard text via xclip (X11) or wl-paste (Wayland).

    On Wayland, falls back to xclip via XWayland if wl-paste is not
    installed.
    """
    session = _detect_session_type()
    content = None
    try:
        found = _find_clipboard_read_tool(session)
        if not found:
            return None
        cmd, tool_name = found
        logger.debug("Clipboard backup using %s.", tool_name)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        content = result.stdout if result.returncode == 0 else None
    except subprocess.TimeoutExpired:
        logger.warning("Clipboard backup timed out.")
        return None
    except Exception:
        logger.debug("Error reading clipboard for backup.")
        return None

    # SEC-070: Guard against huge clipboard content
    if content is not None and len(content) > _MAX_CLIPBOARD_BYTES:
        logger.warning(
            "Clipboard content too large (%d bytes), skipping backup.",
            len(content),
        )
        return None

    return content


def clipboard_restore(backup: str | None) -> None:
    """Write text back to clipboard.

    On Wayland, falls back to xclip via XWayland if wl-copy is not
    installed.
    """
    if backup is None:
        logger.debug("No clipboard backup to restore.")
        return
    session = _detect_session_type()
    try:
        found = _find_clipboard_write_tool(session)
        if not found:
            logger.debug("No clipboard write tool; cannot restore.")
            return
        cmd, tool_name = found
        logger.debug("Clipboard restore using %s.", tool_name)
        subprocess.run(cmd, input=backup, text=True, timeout=2)
    except subprocess.TimeoutExpired:
        logger.warning("Clipboard restore timed out.")
    except Exception:
        logger.debug("Error restoring clipboard.")


def _is_terminal_focused() -> bool:
    """Check if the currently focused window is a terminal emulator.

    Terminal emulators use Ctrl+Shift+V for paste instead of Ctrl+V.
    We detect this by checking the WM_CLASS of the active X11 window
    via xprop (xdotool's getwindowclassname is not available in all versions).
    """
    xdotool = shutil.which("xdotool")
    xprop = shutil.which("xprop")
    if not xdotool or not xprop:
        return False
    try:
        # Get active window ID
        win_result = subprocess.run(
            [xdotool, "getactivewindow"],
            capture_output=True, text=True, timeout=2,
        )
        win_id = win_result.stdout.strip()
        if not win_id:
            return False

        # Query WM_CLASS via xprop (works on all xdotool versions)
        prop_result = subprocess.run(
            [xprop, "-id", win_id, "WM_CLASS"],
            capture_output=True, text=True, timeout=2,
        )
        # xprop output: WM_CLASS(STRING) = "instance", "class"
        # Extract both instance and class names
        wm_classes = set()
        for part in prop_result.stdout.split('"'):
            stripped = part.strip().lower()
            if stripped and stripped not in (',', '=', '') and 'wm_class' not in stripped:
                wm_classes.add(stripped)

        terminal_classes = {
            "gnome-terminal", "gnome-terminal-server",
            "xterm", "uxterm", "konsole", "xfce4-terminal",
            "terminator", "tilix", "alacritty", "kitty",
            "wezterm", "foot", "sakura", "lxterminal",
            "mate-terminal", "guake", "yakuake", "st",
        }
        matched = wm_classes & terminal_classes
        if matched:
            logger.debug("Terminal detected: %s", matched)
            return True
        return False
    except Exception:
        return False


def _simulate_wayland_keystroke(key_combo: str) -> bool:
    """Simulate a keystroke on Wayland, trying methods in priority order.

    Priority:
        1. evdev UInput -- native, no external tools, uses existing
           input group permissions + /dev/uinput write access.
        2. ydotool -- requires ydotoold daemon and /dev/uinput.
        3. wtype -- works on wlroots compositors.

    Args:
        key_combo: Key combo string like "ctrl+v" or "ctrl+shift+v".

    Returns:
        True if the keystroke was simulated, False otherwise.
    """
    # --- Method 1: evdev UInput (preferred) ---
    try:
        from evdev_hotkey import uinput_is_available, uinput_send_key
        if uinput_is_available():
            if uinput_send_key(key_combo):
                logger.info(
                    "Keystroke simulated via evdev UInput: %s", key_combo,
                )
                return True
            logger.warning("evdev UInput send_key returned False.")
    except ImportError:
        logger.debug("evdev_hotkey not available for UInput.")
    except Exception as e:
        logger.warning("evdev UInput failed: %s", e)

    # --- Method 2: ydotool ---
    ydotool = shutil.which("ydotool")
    if ydotool:
        # Build ydotool scancode sequence from combo string
        ydotool_args = _combo_to_ydotool_args(key_combo)
        if ydotool_args:
            try:
                subprocess.run(
                    [ydotool, "key"] + ydotool_args,
                    timeout=2,
                )
                logger.info(
                    "Keystroke simulated via ydotool: %s", key_combo,
                )
                return True
            except subprocess.TimeoutExpired:
                logger.warning("ydotool timed out.")
            except Exception as e:
                logger.warning("ydotool failed: %s", e)

    # --- Method 3: wtype ---
    wtype = shutil.which("wtype")
    if wtype:
        wtype_args = _combo_to_wtype_args(key_combo)
        if wtype_args:
            try:
                subprocess.run(
                    [wtype] + wtype_args,
                    timeout=2,
                )
                logger.info(
                    "Keystroke simulated via wtype: %s", key_combo,
                )
                return True
            except subprocess.TimeoutExpired:
                logger.warning("wtype timed out.")
            except Exception as e:
                logger.warning("wtype failed: %s", e)

    return False


# ydotool scancode mapping (Linux input event codes)
_YDOTOOL_SCANCODES: dict[str, int] = {
    "ctrl": 29, "alt": 56, "shift": 42, "super": 125,
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33,
    "g": 34, "h": 35, "i": 23, "j": 36, "k": 37, "l": 38,
    "m": 50, "n": 49, "o": 24, "p": 25, "q": 16, "r": 19,
    "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    "enter": 28, "return": 28, "escape": 1, "tab": 15,
    "space": 57, "backspace": 14, "delete": 111,
}


def _combo_to_ydotool_args(combo: str) -> list[str]:
    """Convert a combo string to ydotool key arguments.

    E.g. "ctrl+v" -> ["29:1", "47:1", "47:0", "29:0"]
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    codes: list[int] = []
    for part in parts:
        code = _YDOTOOL_SCANCODES.get(part)
        if code is None:
            logger.debug("Unknown key for ydotool: '%s'", part)
            return []
        codes.append(code)

    if not codes:
        return []

    # Press all in order, release in reverse
    args: list[str] = []
    for code in codes:
        args.append(f"{code}:1")
    for code in reversed(codes):
        args.append(f"{code}:0")
    return args


def _combo_to_wtype_args(combo: str) -> list[str]:
    """Convert a combo string to wtype arguments.

    E.g. "ctrl+v" -> ["-M", "ctrl", "v", "-m", "ctrl"]
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    if not parts:
        return []

    modifiers = []
    main_key = None
    mod_names = {"ctrl", "alt", "shift", "super"}

    for part in parts:
        if part in mod_names:
            modifiers.append(part)
        else:
            main_key = part

    if main_key is None:
        return []

    args: list[str] = []
    for mod in modifiers:
        args.extend(["-M", mod])
    args.append(main_key)
    for mod in reversed(modifiers):
        args.extend(["-m", mod])
    return args


def paste_text(text: str) -> bool:
    """Write text to clipboard and simulate paste keystroke.

    Detects terminal emulators and uses Ctrl+Shift+V (the standard
    terminal paste shortcut) instead of Ctrl+V.

    On Wayland, keystroke simulation uses (in priority order):
    1. evdev UInput -- native, no external tools needed
    2. ydotool -- requires ydotoold daemon
    3. wtype -- works on wlroots compositors

    On X11, uses xdotool.
    """
    if not text or not text.strip():
        logger.info("Empty text, nothing to paste.")
        return False

    logger.info("Writing text to clipboard (%d characters).", len(text))
    session = _detect_session_type()

    # Write to clipboard
    try:
        found = _find_clipboard_write_tool(session)
        if not found:
            logger.error(
                "No clipboard write tool found. "
                "Install wl-clipboard (Wayland) or xclip (X11)."
            )
            return False
        cmd, tool_name = found
        logger.debug("Paste: clipboard write using %s.", tool_name)
        subprocess.run(cmd, input=text, text=True, timeout=2)
    except subprocess.TimeoutExpired:
        logger.error("Clipboard write timed out.")
        return False

    # Give clipboard tool time to register the content.
    # X11 clipboard works asynchronously (xclip forks a background process
    # that serves the selection). 150ms is a safe margin.
    time.sleep(0.15)

    # Detect terminal for correct paste shortcut
    is_terminal = _is_terminal_focused()
    paste_key = "ctrl+shift+v" if is_terminal else "ctrl+v"

    # Simulate paste keystroke
    try:
        if session == "wayland":
            if _simulate_wayland_keystroke(paste_key):
                time.sleep(PASTE_DELAY_MS / 1000.0)
                return True
            logger.error(
                "No Wayland keystroke simulation method available.\n"
                "Options (in order of preference):\n"
                "  1. Add udev rule for /dev/uinput access:\n"
                "     echo 'KERNEL==\"uinput\", GROUP=\"input\", "
                "MODE=\"0660\"' | sudo tee "
                "/etc/udev/rules.d/99-voicepaste-uinput.rules\n"
                "     sudo udevadm control --reload-rules && "
                "sudo udevadm trigger\n"
                "  2. Install ydotool: sudo apt install ydotool\n"
                "  3. Install wtype (wlroots compositors only)\n\n"
                "Text was written to clipboard -- "
                "paste manually with Ctrl+V."
            )
            return False
        else:
            xdotool = shutil.which("xdotool")
            if not xdotool:
                logger.error("xdotool not found. Install xdotool.")
                return False
            subprocess.run([xdotool, "key", paste_key], timeout=2)
            time.sleep(PASTE_DELAY_MS / 1000.0)
            logger.info(
                "Paste complete (xdotool/X11, key=%s, terminal=%s).",
                paste_key, is_terminal,
            )
            return True
    except subprocess.TimeoutExpired:
        logger.error("Keystroke simulation timed out.")
        return False
    except Exception as e:
        logger.error("Paste failed: %s", type(e).__name__)
        return False


# ---------------------------------------------------------------------------
# Audio beep (sounddevice sine wave)
# ---------------------------------------------------------------------------

def play_beep(frequency: int, duration_ms: int) -> None:
    """Play a beep tone using sounddevice (cross-platform audio)."""
    try:
        import numpy as np
        import sounddevice as sd

        sample_rate = 22050
        n_samples = int(sample_rate * duration_ms / 1000)
        t = np.linspace(0, duration_ms / 1000.0, n_samples, endpoint=False)
        wave = (np.sin(2 * np.pi * frequency * t) * 0.3 * 32767).astype(np.int16)
        sd.play(wave, samplerate=sample_rate, blocking=True)
    except Exception:
        logger.debug("Audio beep failed (no audio device or missing deps).")


# ---------------------------------------------------------------------------
# Fatal error dialog
# ---------------------------------------------------------------------------

def show_fatal_error(message: str, title: str = "Voice Paste") -> None:
    """Show a fatal error dialog via zenity, tkinter, or stderr."""
    # zenity is pre-installed on Ubuntu GNOME
    if shutil.which("zenity"):
        try:
            subprocess.run(
                ["zenity", "--error", "--title", title, "--text", message],
                timeout=30,
            )
            return
        except Exception:
            pass
    # tkinter fallback
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
        return
    except Exception:
        pass
    # Last resort: stderr
    print(f"FATAL ERROR: {title}: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Debug console allocation
# ---------------------------------------------------------------------------

def enable_debug_console() -> None:
    """No-op on Linux -- terminal is always available."""
    pass


# ---------------------------------------------------------------------------
# Single-instance lock (fcntl file lock)
# ---------------------------------------------------------------------------

_lock_fd = None
_lock_path = None


def acquire_single_instance_lock():
    """Acquire a file-based lock. Returns a handle (fd) or None."""
    global _lock_fd, _lock_path
    data_dir = get_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _lock_path = str(data_dir / ".lock")

    try:
        _lock_fd = open(_lock_path, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        logger.info("Single-instance lock acquired: '%s'.", _lock_path)
        return _lock_fd
    except (OSError, IOError):
        logger.error("Another instance is already running (lock '%s').", _lock_path)
        if _lock_fd:
            _lock_fd.close()
            _lock_fd = None
        return None


def release_single_instance_lock(handle) -> None:
    """Release the file-based lock."""
    global _lock_fd, _lock_path
    if handle is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        except Exception:
            pass
        try:
            if _lock_path:
                os.unlink(_lock_path)
        except OSError:
            pass
    _lock_fd = None


# ---------------------------------------------------------------------------
# Keystroke simulation
# ---------------------------------------------------------------------------

# xdotool key names differ from keyboard library names
_XDOTOOL_KEY_MAP = {
    "enter": "Return",
    "escape": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
}


def send_key(key: str) -> None:
    """Send a keystroke via the best available method.

    On Wayland: evdev UInput (preferred), ydotool, or wtype.
    On X11: xdotool with --clearmodifiers.
    """
    session = _detect_session_type()
    if session == "wayland":
        if not _simulate_wayland_keystroke(key):
            logger.warning(
                "Could not simulate keystroke '%s' on Wayland. "
                "See paste_text() error for setup instructions.",
                key,
            )
    else:
        xdotool = shutil.which("xdotool")
        if xdotool:
            # Map key names and pass through combos like "ctrl+v"
            mapped = _XDOTOOL_KEY_MAP.get(key.lower(), key)
            subprocess.run(
                [xdotool, "key", "--clearmodifiers", mapped], timeout=2,
            )


def register_key_press(key: str, callback, suppress: bool = False):
    """Register a key-press hook. Uses evdev on Wayland, pynput on X11.

    Returns a handle — either a tagged tuple ("evdev", int) on Wayland
    or a pynput Listener on X11.
    """
    session = _detect_session_type()
    if session == "wayland":
        try:
            from evdev_hotkey import evdev_add_key_listener
            # Wrap callback to match pynput signature (receives pressed_key)
            handle = evdev_add_key_listener(key, lambda: callback(key))
            return ("evdev", handle)
        except ImportError:
            logger.warning("evdev_hotkey not available. Key press hooks disabled.")
            return None
        except Exception as e:
            logger.warning("evdev key listener failed: %s", e)
            return None

    # X11 / XWayland: use pynput
    try:
        from pynput import keyboard as pynput_kb

        key_map = {
            "enter": pynput_kb.Key.enter,
            "escape": pynput_kb.Key.esc,
            "tab": pynput_kb.Key.tab,
            "space": pynput_kb.Key.space,
            "backspace": pynput_kb.Key.backspace,
            "delete": pynput_kb.Key.delete,
        }
        target = key_map.get(key.lower())
        if target is None:
            target = pynput_kb.KeyCode.from_char(key)

        def on_press(pressed_key):
            if pressed_key == target:
                callback(pressed_key)

        listener = pynput_kb.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        return listener
    except ImportError:
        logger.warning("pynput not available. Key press hooks disabled.")
        return None


def unregister_key_hook(hook) -> None:
    """Stop a key-press hook (evdev or pynput)."""
    if hook is None:
        return
    # Evdev handles are tagged tuples
    if isinstance(hook, tuple) and len(hook) == 2 and hook[0] == "evdev":
        try:
            from evdev_hotkey import evdev_remove_hotkey
            evdev_remove_hotkey(hook[1])
        except Exception:
            pass
        return
    # pynput listener
    try:
        hook.stop()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Application data directories (XDG)
# ---------------------------------------------------------------------------

def get_app_data_dir() -> Path:
    """Return XDG data directory for VoicePaste."""
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(base) / "VoicePaste"


def get_cache_dir() -> Path:
    """Return XDG cache directory for VoicePaste."""
    base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return Path(base) / "VoicePaste"
