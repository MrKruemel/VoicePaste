"""Clipboard and paste module for VoicePaste.

Writes text to the clipboard and simulates Ctrl+V to paste.
v0.2+: Clipboard backup/restore to preserve user's clipboard contents.
v0.2.3: Fixed 64-bit pointer truncation bug -- all Win32 API functions
now have explicit restype/argtypes declarations so that HGLOBAL, LPVOID,
and HANDLE return values are not truncated to 32-bit c_int.

REQ-S18: Paste as plain text only (CF_UNICODETEXT).
REQ-S14: Never log clipboard contents.
"""

import ctypes
import ctypes.wintypes
import logging
import time

import keyboard as kb

from constants import PASTE_DELAY_MS

logger = logging.getLogger(__name__)

# Windows clipboard format for Unicode text
CF_UNICODETEXT = 13

# Memory allocation flags
GMEM_MOVEABLE = 0x0002

# ---------------------------------------------------------------------------
# Win32 API type declarations for 64-bit compatibility
#
# By default, ctypes assumes all foreign-function return types are c_int
# (32-bit). On 64-bit Windows/Python, pointer return values (HGLOBAL,
# LPVOID, HANDLE) are 64-bit and get silently truncated if the high 32
# bits are non-zero.  This caused the "Failed to lock global memory"
# error on every paste attempt.
#
# We declare restype and argtypes for EVERY Win32 function we call.
# ---------------------------------------------------------------------------

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

# -- kernel32 ---------------------------------------------------------------

# HGLOBAL GlobalAlloc(UINT uFlags, SIZE_T dwBytes)
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]

# LPVOID GlobalLock(HGLOBAL hMem)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]

# BOOL GlobalUnlock(HGLOBAL hMem)
kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

# HGLOBAL GlobalFree(HGLOBAL hMem)
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

# -- user32 ------------------------------------------------------------------

# BOOL OpenClipboard(HWND hWndNewOwner)
user32.OpenClipboard.restype = ctypes.wintypes.BOOL
user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]

# BOOL CloseClipboard(void)
user32.CloseClipboard.restype = ctypes.wintypes.BOOL
user32.CloseClipboard.argtypes = []

# BOOL EmptyClipboard(void)
user32.EmptyClipboard.restype = ctypes.wintypes.BOOL
user32.EmptyClipboard.argtypes = []

# HANDLE SetClipboardData(UINT uFormat, HANDLE hMem)
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.c_void_p]

# HANDLE GetClipboardData(UINT uFormat)
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [ctypes.wintypes.UINT]

# BOOL IsClipboardFormatAvailable(UINT format)
user32.IsClipboardFormatAvailable.restype = ctypes.wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [ctypes.wintypes.UINT]


def _open_clipboard(retries: int = 3, delay: float = 0.05) -> bool:
    """Open the Windows clipboard with retry logic.

    The clipboard is a shared resource. Other applications may have it
    locked, so we retry a few times.

    Args:
        retries: Number of attempts.
        delay: Delay between retries in seconds.

    Returns:
        True if clipboard was opened, False otherwise.
    """
    for attempt in range(retries):
        if user32.OpenClipboard(None):
            return True
        if attempt < retries - 1:
            time.sleep(delay)
    return False


def _close_clipboard() -> None:
    """Close the Windows clipboard."""
    user32.CloseClipboard()


def clipboard_backup() -> str | None:
    """Read the current clipboard text content (CF_UNICODETEXT) for backup.

    Only backs up plain text. If the clipboard contains non-text data
    (images, files, etc.), those formats are not preserved. This is an
    accepted limitation documented in UX-SPEC.md section 4.7.

    REQ-S14: Never log clipboard contents.

    Returns:
        The clipboard text as a string, or None if clipboard is empty
        or contains no text data.
    """
    if not _open_clipboard():
        logger.warning("Failed to open clipboard for backup.")
        return None

    try:
        # Check if clipboard has Unicode text
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            logger.debug("Clipboard has no text data to backup.")
            return None

        h_data = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_data:
            logger.debug("GetClipboardData returned null.")
            return None

        p_data = kernel32.GlobalLock(h_data)
        if not p_data:
            logger.debug("GlobalLock returned null for clipboard data.")
            return None

        try:
            # Read the null-terminated UTF-16LE string
            text = ctypes.wstring_at(p_data)
            logger.info("Clipboard backed up (%d characters).", len(text))
            return text
        finally:
            kernel32.GlobalUnlock(h_data)

    except Exception:
        logger.debug("Error reading clipboard for backup.")
        return None
    finally:
        _close_clipboard()


def clipboard_restore(backup: str | None) -> None:
    """Restore previously backed-up text to the clipboard.

    If backup is None, the clipboard is not modified (the pasted text
    remains on the clipboard, which is still useful to the user).

    REQ-S14: Never log clipboard contents.
    REQ-S18: Uses CF_UNICODETEXT for plain text only.

    Args:
        backup: The text to restore, or None to skip restoration.
    """
    if backup is None:
        logger.debug("No clipboard backup to restore.")
        return

    if not _open_clipboard():
        logger.warning("Failed to open clipboard for restore.")
        return

    try:
        user32.EmptyClipboard()

        # Encode text as UTF-16LE (Windows native Unicode)
        encoded = backup.encode("utf-16-le") + b"\x00\x00"

        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h_mem:
            logger.warning("Failed to allocate memory for clipboard restore.")
            return

        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            kernel32.GlobalFree(h_mem)
            logger.warning("Failed to lock memory for clipboard restore.")
            return

        ctypes.memmove(p_mem, encoded, len(encoded))
        kernel32.GlobalUnlock(h_mem)

        result = user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        if not result:
            kernel32.GlobalFree(h_mem)
            logger.warning("Failed to set clipboard data during restore.")
            return

        logger.info("Clipboard restored (%d characters).", len(backup))

    except Exception:
        logger.debug("Error restoring clipboard.")
    finally:
        _close_clipboard()


def paste_text(text: str, paste_shortcut: str = "auto") -> bool:
    """Write text to clipboard and simulate Ctrl+V to paste.

    REQ-S18: Uses CF_UNICODETEXT for plain text only.
    REQ-S14: Never logs the text content.

    Note: Clipboard backup/restore is handled by the caller (main.py)
    using clipboard_backup() and clipboard_restore() around this call.

    Args:
        text: Text to paste at the current cursor position.
        paste_shortcut: Accepted for API compatibility with the Linux
            backend but ignored on Windows. Windows always uses Ctrl+V
            because terminal emulators on Windows also accept Ctrl+V.

    Returns:
        True if paste was executed, False if clipboard write failed.
    """
    if not text or not text.strip():
        logger.info("Empty text, nothing to paste.")
        return False

    logger.info("Writing text to clipboard (%d characters).", len(text))

    try:
        # Open clipboard
        if not _open_clipboard():
            logger.error("Failed to open clipboard after retries.")
            return False

        try:
            # Clear clipboard
            user32.EmptyClipboard()

            # Encode text as UTF-16LE (Windows native Unicode)
            encoded = text.encode("utf-16-le") + b"\x00\x00"

            # Allocate global memory for the clipboard data
            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not h_mem:
                logger.error("Failed to allocate global memory for clipboard.")
                return False

            # Lock memory and copy data
            p_mem = kernel32.GlobalLock(h_mem)
            if not p_mem:
                kernel32.GlobalFree(h_mem)
                logger.error("Failed to lock global memory.")
                return False

            ctypes.memmove(p_mem, encoded, len(encoded))
            kernel32.GlobalUnlock(h_mem)

            # Set clipboard data (REQ-S18: CF_UNICODETEXT only)
            result = user32.SetClipboardData(CF_UNICODETEXT, h_mem)
            if not result:
                kernel32.GlobalFree(h_mem)
                logger.error("Failed to set clipboard data.")
                return False

        finally:
            _close_clipboard()

        # Brief delay to ensure clipboard is ready
        time.sleep(0.05)

        # Simulate Ctrl+V to paste
        logger.info("Simulating Ctrl+V paste.")
        kb.send("ctrl+v")

        # Wait for paste to complete
        time.sleep(PASTE_DELAY_MS / 1000.0)

        logger.info("Paste operation complete.")
        return True

    except Exception as e:
        logger.error("Paste operation failed: %s", type(e).__name__)
        return False
