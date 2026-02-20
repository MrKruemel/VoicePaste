"""Linux platform implementations for VoicePaste.

Provides clipboard (xclip/wl-clipboard), keystroke simulation
(xdotool/ydotool), single-instance locking (fcntl), error dialogs
(zenity/tkinter), and audio beeps (sounddevice sine waves).

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session type detection
# ---------------------------------------------------------------------------

def _detect_session_type() -> str:
    """Detect X11 vs Wayland from XDG_SESSION_TYPE."""
    return os.environ.get("XDG_SESSION_TYPE", "x11")


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def clipboard_backup() -> str | None:
    """Read clipboard text via xclip (X11) or wl-paste (Wayland)."""
    session = _detect_session_type()
    try:
        if session == "wayland":
            tool = shutil.which("wl-paste")
            if not tool:
                logger.debug("wl-paste not found.")
                return None
            result = subprocess.run(
                [tool, "--no-newline"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout if result.returncode == 0 else None
        else:
            tool = shutil.which("xclip") or shutil.which("xsel")
            if not tool:
                logger.debug("xclip/xsel not found.")
                return None
            if "xclip" in tool:
                cmd = [tool, "-selection", "clipboard", "-o"]
            else:
                cmd = [tool, "--clipboard", "-o"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            return result.stdout if result.returncode == 0 else None
    except subprocess.TimeoutExpired:
        logger.warning("Clipboard backup timed out.")
        return None
    except Exception:
        logger.debug("Error reading clipboard for backup.")
        return None


def clipboard_restore(backup: str | None) -> None:
    """Write text back to clipboard."""
    if backup is None:
        logger.debug("No clipboard backup to restore.")
        return
    session = _detect_session_type()
    try:
        if session == "wayland":
            tool = shutil.which("wl-copy")
            if tool:
                subprocess.run([tool], input=backup, text=True, timeout=2)
        else:
            tool = shutil.which("xclip")
            if tool:
                subprocess.run(
                    [tool, "-selection", "clipboard", "-i"],
                    input=backup, text=True, timeout=2,
                )
    except subprocess.TimeoutExpired:
        logger.warning("Clipboard restore timed out.")
    except Exception:
        logger.debug("Error restoring clipboard.")


def paste_text(text: str) -> bool:
    """Write text to clipboard and simulate Ctrl+V to paste."""
    if not text or not text.strip():
        logger.info("Empty text, nothing to paste.")
        return False

    logger.info("Writing text to clipboard (%d characters).", len(text))
    session = _detect_session_type()

    # Write to clipboard
    try:
        if session == "wayland":
            tool = shutil.which("wl-copy")
            if not tool:
                logger.error("wl-copy not found. Install wl-clipboard.")
                return False
            subprocess.run([tool], input=text, text=True, timeout=2)
        else:
            tool = shutil.which("xclip")
            if not tool:
                logger.error("xclip not found. Install xclip.")
                return False
            subprocess.run(
                [tool, "-selection", "clipboard", "-i"],
                input=text, text=True, timeout=2,
            )
    except subprocess.TimeoutExpired:
        logger.error("Clipboard write timed out.")
        return False

    time.sleep(0.05)

    # Simulate Ctrl+V
    try:
        if session == "wayland":
            ydotool = shutil.which("ydotool")
            if ydotool:
                # ydotool key scancodes: 29=LCtrl, 47=V
                subprocess.run(
                    [ydotool, "key", "29:1", "47:1", "47:0", "29:0"],
                    timeout=2,
                )
                time.sleep(PASTE_DELAY_MS / 1000.0)
                logger.info("Paste complete (ydotool/Wayland).")
                return True
            wtype = shutil.which("wtype")
            if wtype:
                subprocess.run(
                    [wtype, "-M", "ctrl", "v", "-m", "ctrl"],
                    timeout=2,
                )
                time.sleep(PASTE_DELAY_MS / 1000.0)
                logger.info("Paste complete (wtype/Wayland).")
                return True
            logger.error("No Wayland keystroke tool. Install ydotool or wtype.")
            return False
        else:
            xdotool = shutil.which("xdotool")
            if not xdotool:
                logger.error("xdotool not found. Install xdotool.")
                return False
            subprocess.run([xdotool, "key", "ctrl+v"], timeout=2)
            time.sleep(PASTE_DELAY_MS / 1000.0)
            logger.info("Paste complete (xdotool/X11).")
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
    """Send a keystroke via xdotool (X11) or ydotool (Wayland)."""
    session = _detect_session_type()
    if session == "wayland":
        ydotool = shutil.which("ydotool")
        if ydotool:
            # Map common keys to ydotool scancodes
            scancode_map = {
                "enter": "28:1 28:0",
                "escape": "1:1 1:0",
                "ctrl+v": "29:1 47:1 47:0 29:0",
            }
            codes = scancode_map.get(key.lower(), "")
            if codes:
                subprocess.run(
                    [ydotool, "key"] + codes.split(), timeout=2,
                )
    else:
        xdotool = shutil.which("xdotool")
        if xdotool:
            # Map key names and pass through combos like "ctrl+v"
            mapped = _XDOTOOL_KEY_MAP.get(key.lower(), key)
            subprocess.run([xdotool, "key", mapped], timeout=2)


def register_key_press(key: str, callback, suppress: bool = False):
    """Register a key-press hook via pynput. Returns a listener handle."""
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
    """Stop a pynput listener."""
    if hook is not None:
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
