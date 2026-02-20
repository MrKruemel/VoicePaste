"""Windows platform implementations for VoicePaste.

Wraps existing Win32 code from paste.py, winsound, ctypes.windll
into the platform_impl interface.  This module is only imported on
Windows (sys.platform == "win32").
"""

import ctypes
import ctypes.wintypes
import logging
import os
import sys
from pathlib import Path

import keyboard as kb
import winsound

# Re-export clipboard functions from existing paste module (unchanged).
from paste import clipboard_backup, clipboard_restore, paste_text  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio beep
# ---------------------------------------------------------------------------

def play_beep(frequency: int, duration_ms: int) -> None:
    """Play a beep tone using the Windows kernel beep driver."""
    winsound.Beep(frequency, duration_ms)


# ---------------------------------------------------------------------------
# Fatal error dialog
# ---------------------------------------------------------------------------

_MB_OK = 0x00000000
_MB_ICONERROR = 0x00000010
_MB_TOPMOST = 0x00040000


def show_fatal_error(message: str, title: str = "Voice Paste") -> None:
    """Show a fatal error message box via Win32 MessageBoxW."""
    try:
        ctypes.windll.user32.MessageBoxW(
            0, message, title, _MB_OK | _MB_ICONERROR | _MB_TOPMOST,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Debug console allocation
# ---------------------------------------------------------------------------

def enable_debug_console() -> None:
    """Allocate a console window for --noconsole PyInstaller builds."""
    try:
        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Single-instance lock (Windows named mutex)
# ---------------------------------------------------------------------------

_MUTEX_NAME = "Global\\VoicePasteToolMutex"
_ERROR_ALREADY_EXISTS = 183


def acquire_single_instance_lock():
    """Acquire a Windows named mutex.  Returns a handle or None."""
    kernel32 = ctypes.windll.kernel32

    handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if handle == 0 or handle is None:
        logger.error(
            "Failed to create mutex '%s'. GetLastError=%d",
            _MUTEX_NAME, kernel32.GetLastError(),
        )
        return None

    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        logger.error(
            "Another instance is already running (mutex '%s' exists).",
            _MUTEX_NAME,
        )
        kernel32.CloseHandle(handle)
        return None

    logger.info("Single-instance mutex acquired: '%s'.", _MUTEX_NAME)
    return handle


def release_single_instance_lock(handle) -> None:
    """Release and close the single-instance mutex."""
    if handle is None:
        return
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
        logger.info("Single-instance mutex released.")
    except Exception:
        logger.exception("Error releasing single-instance mutex.")


# ---------------------------------------------------------------------------
# Keystroke simulation
# ---------------------------------------------------------------------------

def send_key(key: str) -> None:
    """Send a keystroke via the keyboard library."""
    kb.send(key)


def register_key_press(key: str, callback, suppress: bool = False):
    """Register a key-press hook.  Returns a hook handle."""
    return kb.on_press_key(key, callback, suppress=suppress)


def unregister_key_hook(hook) -> None:
    """Remove a previously registered key-press hook."""
    if hook is not None:
        kb.unhook(hook)


# ---------------------------------------------------------------------------
# Application data directories
# ---------------------------------------------------------------------------

def get_app_data_dir() -> Path:
    """Return %LOCALAPPDATA%/VoicePaste."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        local = os.path.expanduser("~/AppData/Local")
    return Path(local) / "VoicePaste"


def get_cache_dir() -> Path:
    """On Windows, cache and data share the same directory."""
    return get_app_data_dir()
