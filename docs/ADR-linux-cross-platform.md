# Architecture Decision Record: Linux Cross-Platform Support

**Date**: 2026-02-20
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.9.1 (current, Windows-only)
**Target Version**: 1.1.0 (cross-platform Windows + Linux)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Windows Dependencies Inventory](#2-current-windows-dependencies-inventory)
3. [Platform Abstraction Strategy](#3-platform-abstraction-strategy)
4. [Component-by-Component Analysis](#4-component-by-component-analysis)
   - 4.1 [Clipboard and Paste (paste.py) -- HARD BLOCK](#41-clipboard-and-paste-pastepy----hard-block)
   - 4.2 [Global Hotkeys (hotkey.py) -- HARD BLOCK](#42-global-hotkeys-hotkeypy----hard-block)
   - 4.3 [Audio Cues (notifications.py) -- MEDIUM](#43-audio-cues-notificationspy----medium)
   - 4.4 [Single-Instance Mutex (main.py) -- MEDIUM](#44-single-instance-mutex-mainpy----medium)
   - 4.5 [Fatal Error Dialogs (main.py) -- MEDIUM](#45-fatal-error-dialogs-mainpy----medium)
   - 4.6 [Debug Console Allocation (main.py) -- LOW](#46-debug-console-allocation-mainpy----low)
   - 4.7 [Floating Overlay (overlay.py) -- MEDIUM](#47-floating-overlay-overlaypy----medium)
   - 4.8 [System Tray (tray.py) -- MEDIUM](#48-system-tray-traypy----medium)
   - 4.9 [Credential Storage (keyring_store.py) -- LOW](#49-credential-storage-keyring_storepy----low)
   - 4.10 [Data/Model Paths -- LOW](#410-datamodel-paths----low)
   - 4.11 [Settings Dialog Dark Mode (settings_dialog.py) -- LOW](#411-settings-dialog-dark-mode-settings_dialogpy----low)
   - 4.12 [Config File Comments (config.py) -- LOW](#412-config-file-comments-configpy----low)
   - 4.13 [Build and Packaging -- MEDIUM](#413-build-and-packaging----medium)
5. [Already Portable Modules](#5-already-portable-modules)
6. [The Wayland Problem](#6-the-wayland-problem)
7. [Platform Module Architecture](#7-platform-module-architecture)
8. [Implementation Plan](#8-implementation-plan)
9. [Packaging Strategy for Linux](#9-packaging-strategy-for-linux)
10. [Effort Estimate Summary](#10-effort-estimate-summary)
11. [Risk Assessment](#11-risk-assessment)
12. [Decision: Recommended Approach](#12-decision-recommended-approach)

---

## 1. Executive Summary

Voice Paste is a ~2600 SLOC Python desktop application that currently runs exclusively on Windows. This ADR analyzes every Windows-specific dependency and proposes a concrete plan for Linux support.

**Key findings:**

- **25 source files** total. **7 files** contain Windows-specific code (28%).
- **15 files** are already fully portable (60%), needing zero changes.
- **3 files** are partially portable (minor platform branching needed).
- **1 file** is 100% Windows-specific and needs a complete Linux rewrite: `paste.py`. (`overlay.py` exists but is DISABLED and excluded from scope.)
- The **biggest risk** is Wayland vs X11. Global hotkeys and clipboard injection work differently (or not at all) on Wayland. Any Linux release that is honest about its capabilities must document the X11 requirement or accept degraded Wayland support.
- **Estimated effort**: 7-12 developer days for a "works on X11" release, plus 3-5 additional days for Wayland workarounds.

**Recommended approach**: Platform abstraction via a `platform/` package with `platform/windows.py` and `platform/linux.py` modules, behind a common interface. The existing Windows code is left intact. New Linux implementations are added alongside. A platform detection shim selects the correct backend at import time.

---

## 2. Current Windows Dependencies Inventory

The following table lists every Windows-specific API call or import in the codebase, grouped by severity.

### HARD BLOCKS (file is 100% Windows-specific, Linux needs full rewrite)

| File | Win32 API / Import | Lines | Purpose |
|------|-------------------|-------|---------|
| `paste.py` | `ctypes.windll.kernel32` (GlobalAlloc, GlobalLock, GlobalUnlock, GlobalFree) | 42-87 | Clipboard memory management |
| `paste.py` | `ctypes.windll.user32` (OpenClipboard, CloseClipboard, EmptyClipboard, SetClipboardData, GetClipboardData, IsClipboardFormatAvailable) | 42-87 | Clipboard read/write |
| `paste.py` | `keyboard.send("ctrl+v")` | 283 | Simulating paste keystroke |
| `paste.py` | `CF_UNICODETEXT`, `GMEM_MOVEABLE` | 25-28 | Windows clipboard constants |
| `overlay.py` | `ctypes.windll.user32` (GetParent, GetWindowLongW, SetWindowLongW, SetWindowPos, SetLayeredWindowAttributes, SystemParametersInfoW) | 69-99 | Win32 window style manipulation |
| `overlay.py` | `ctypes.wintypes` (HWND, BOOL, RECT, COLORREF, BYTE, DWORD, UINT) | 19, 71-98 | Win32 type declarations |
| `overlay.py` | WS_EX_NOACTIVATE, WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, SPI_GETWORKAREA | 31-43 | Win32 constants |

### MEDIUM (file has isolated Win32 calls that can be branched)

| File | Win32 API / Import | Lines | Purpose |
|------|-------------------|-------|---------|
| `main.py` | `ctypes.windll.user32.MessageBoxW` | 101 | Fatal error dialogs |
| `main.py` | `ctypes.windll.kernel32.AllocConsole` | 124 | Debug console allocation |
| `main.py` | `ctypes.windll.kernel32.CreateMutexW` / `CloseHandle` / `ReleaseMutex` | 148-191 | Single-instance enforcement |
| `main.py` | `ctypes.wintypes.HANDLE` | 137, 179 | Type annotations |
| `main.py` | `import winsound` (in `_wait_before_paste`) | 1513-1518 | Countdown beep during paste confirmation |
| `notifications.py` | `import winsound` / `winsound.Beep(freq, duration)` | 12, 42 | Audio cue tones |
| `settings_dialog.py` | `ctypes.windll.dwmapi.DwmSetWindowAttribute` | 77-80 | Dark title bar on Win10/11 |
| `settings_dialog.py` | `ctypes.windll.user32.GetParent` | 77 | Get HWND for DWM call |
| `hotkey.py` | `import keyboard` | 15 | Global hotkey registration (keyboard library uses `_winkeyboard` backend internally) |

### LOW (platform-specific path or trivial branch)

| File | Issue | Lines | Purpose |
|------|-------|-------|---------|
| `model_manager.py` | `os.environ.get("LOCALAPPDATA")` | 72 | Model cache directory |
| `tts_model_manager.py` | `os.environ.get("LOCALAPPDATA")` | 47 | TTS model cache directory |
| `tts_cache.py` | `os.environ.get("LOCALAPPDATA")` | 123 | TTS audio cache directory |
| `settings_dialog.py` | `os.environ.get("LOCALAPPDATA")` | 2169 | Cache stats display |
| `config.py` | "Windows Credential Manager" in comments/template | multiple | Documentation strings |
| `keyring_store.py` | Uses `keyring` library (auto-detects backend per platform) | all | Credential storage |

---

## 3. Platform Abstraction Strategy

### Decision: Strategy Pattern with Platform Modules

We introduce a `src/platform/` package with a common interface and per-platform implementations.

```
src/
  platform/
    __init__.py          # Platform detection, re-exports
    _interface.py        # Abstract base classes / Protocols
    _windows.py          # Windows implementations
    _linux.py            # Linux implementations
```

**`platform/__init__.py`** detects the OS at import time and re-exports the correct implementations:

```python
import sys

if sys.platform == "win32":
    from platform._windows import (
        ClipboardManager,
        paste_text,
        clipboard_backup,
        clipboard_restore,
        show_fatal_error,
        acquire_single_instance_lock,
        release_single_instance_lock,
        play_beep,
        enable_debug_console,
        apply_overlay_styles,
        get_work_area,
    )
elif sys.platform == "linux":
    from platform._linux import (
        ClipboardManager,
        paste_text,
        clipboard_backup,
        clipboard_restore,
        show_fatal_error,
        acquire_single_instance_lock,
        release_single_instance_lock,
        play_beep,
        enable_debug_console,
        apply_overlay_styles,
        get_work_area,
    )
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
```

**Why not abc.ABC?** The existing codebase uses module-level functions, not class instances. Wrapping them in Protocol classes is sufficient for type checking. The actual dispatch happens at import time via the `platform/__init__.py` shim, which is simpler than dependency injection.

**Why not a single file with `if sys.platform` branches?** The Windows and Linux implementations differ fundamentally (Win32 clipboard API vs xdotool/xclip, named mutex vs flock). Putting them in separate files keeps each clean and testable independently.

### Import Migration

Current code (`paste.py` direct import):
```python
from paste import clipboard_backup, clipboard_restore, paste_text
```

After migration:
```python
from platform import clipboard_backup, clipboard_restore, paste_text
```

The old `paste.py` becomes `platform/_windows.py` (clipboard section). The migration is mechanical: find-and-replace imports in `main.py` and `config.py`.

---

## 4. Component-by-Component Analysis

### 4.1 Clipboard and Paste (paste.py) -- HARD BLOCK

**Current implementation**: 294 lines of pure Win32 API calls via `ctypes.windll` for clipboard management (OpenClipboard, SetClipboardData, etc.) and `keyboard.send("ctrl+v")` for keystroke simulation.

**Linux X11 equivalent**:
- **Clipboard read/write**: `xclip -selection clipboard` or `xsel --clipboard`. Both are standard Linux utilities. Python subprocess call is reliable and simple.
- **Clipboard alternative**: `subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True)` for read, pipe text to `xclip -selection clipboard -i` for write.
- **Keystroke simulation**: `xdotool key ctrl+v` on X11. Well-tested, widely available.
- **Pure Python fallback**: `python-xlib` can do both clipboard and keystroke injection without external tools, but adds a heavyweight dependency.

**Linux Wayland equivalent**:
- **Clipboard**: `wl-copy` (write) and `wl-paste` (read) from `wl-clipboard` package.
- **Keystroke simulation**: **Not natively supported on Wayland.** There is no universal equivalent to `xdotool` on Wayland. Possible workarounds:
  - `wtype` (wlroots-based compositors only, not GNOME/KDE).
  - `ydotool` (requires root or uinput group; simulates at evdev level).
  - D-Bus protocols (compositor-specific, not standardized).
  - **This is the single biggest technical risk for Linux support.**

**Recommended implementation**:
```python
# platform/_linux.py

import shutil
import subprocess

def _detect_session_type() -> str:
    """Detect X11 vs Wayland."""
    return os.environ.get("XDG_SESSION_TYPE", "x11")

def clipboard_backup() -> str | None:
    """Read clipboard text via xclip (X11) or wl-paste (Wayland)."""
    session = _detect_session_type()
    if session == "wayland":
        tool = shutil.which("wl-paste")
        if not tool:
            return None
        result = subprocess.run(
            [tool, "--no-newline"],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout if result.returncode == 0 else None
    else:
        tool = shutil.which("xclip") or shutil.which("xsel")
        if not tool:
            return None
        cmd = [tool, "-selection", "clipboard", "-o"] if "xclip" in tool else [tool, "--clipboard", "-o"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        return result.stdout if result.returncode == 0 else None

def clipboard_restore(text: str | None) -> None:
    """Write text to clipboard."""
    if text is None:
        return
    session = _detect_session_type()
    if session == "wayland":
        tool = shutil.which("wl-copy")
        if tool:
            subprocess.run([tool], input=text, text=True, timeout=2)
    else:
        tool = shutil.which("xclip")
        if tool:
            subprocess.run(
                [tool, "-selection", "clipboard", "-i"],
                input=text, text=True, timeout=2,
            )

def paste_text(text: str) -> bool:
    """Write to clipboard and simulate Ctrl+V."""
    clipboard_restore(text)
    time.sleep(0.05)
    session = _detect_session_type()
    if session == "wayland":
        ydotool = shutil.which("ydotool")
        if ydotool:
            subprocess.run([ydotool, "key", "29:1", "47:1", "47:0", "29:0"], timeout=2)
            return True
        return False  # Cannot simulate keypress on Wayland without ydotool
    else:
        xdotool = shutil.which("xdotool")
        if xdotool:
            subprocess.run([xdotool, "key", "ctrl+v"], timeout=2)
            return True
        return False
```

**Effort**: 2-3 days (implement + test across X11 and Wayland).

**Dependencies to install**: `xclip` or `xsel` (X11), `wl-clipboard` (Wayland), `xdotool` (X11), `ydotool` (Wayland, optional).


### 4.2 Global Hotkeys (hotkey.py) -- HARD BLOCK

**Current implementation**: Uses the `keyboard` Python library, which on Windows hooks into `_winkeyboard` for low-level keyboard monitoring. The library works on Linux BUT requires **root privileges** (or read access to `/dev/input/*`) because it reads raw input events from evdev.

**Option A: `keyboard` library with root (not recommended)**
- Requires `sudo` or adding the user to the `input` group.
- Not acceptable for a desktop application that should "just work."

**Option B: `pynput` library (recommended for X11)**
- `pynput.keyboard.GlobalHotKeys` works on X11 without root.
- Uses Xlib under the hood (`python-xlib` dependency).
- **Does NOT work on Wayland** (X11-only by design, unless XWayland is available).
- API is different from `keyboard` library (context-manager based).

**Option C: D-Bus global shortcuts (Wayland-compatible)**
- GNOME 44+, KDE 5.27+, and wlroots compositors support the `org.freedesktop.portal.GlobalShortcuts` D-Bus interface.
- Python access via `dbus-next` (async) or `pydbus`.
- **Only works if the desktop portal is installed and the compositor supports it.**
- Registration is asynchronous and requires user consent via a system dialog.
- This is the "correct" Wayland approach, but adoption is uneven.

**Option D: `keybinder` / `libkeybinder` (X11 only)**
- GObject-based, works without root on X11.
- Python bindings via `gi.repository.Keybinder`.
- Requires GTK and GObject introspection.

**Recommended implementation**: `pynput` for X11 as the primary path. Document that Wayland users need to either:
1. Run under XWayland (most apps do this anyway).
2. Use the HTTP API (`api_server.py`) as an alternative control mechanism.
3. Wait for D-Bus GlobalShortcuts to be added in a future version.

```python
# platform/_linux.py (hotkey section)

from pynput import keyboard as pynput_kb

class LinuxHotkeyManager:
    """Global hotkey manager using pynput (X11)."""

    def __init__(self, hotkey_str: str):
        self._hotkey_str = hotkey_str
        self._listener = None

    def register(self, callback):
        # Convert "ctrl+alt+r" to pynput format "<ctrl>+<alt>+r"
        pynput_combo = self._convert_hotkey(self._hotkey_str)
        self._listener = pynput_kb.GlobalHotKeys({
            pynput_combo: callback,
        })
        self._listener.start()

    def unregister(self):
        if self._listener:
            self._listener.stop()

    @staticmethod
    def _convert_hotkey(combo: str) -> str:
        """Convert 'ctrl+alt+r' to '<ctrl>+<alt>+r'."""
        parts = combo.lower().split("+")
        converted = []
        for part in parts:
            p = part.strip()
            if p in ("ctrl", "alt", "shift", "cmd", "super"):
                converted.append(f"<{p}>")
            else:
                converted.append(p)
        return "+".join(converted)
```

**Effort**: 1-2 days. The `keyboard` library calls in `hotkey.py` are well-encapsulated and the `HotkeyManager` class is already a clean abstraction. The Linux version just needs a different backend.

**Note**: The `keyboard` library is also used directly in `main.py` lines 1482 and 1643 for Enter key hooks and `keyboard.send("enter")`. These also need `pynput` equivalents.


### 4.3 Audio Cues (notifications.py) -- MEDIUM

**Current implementation**: `winsound.Beep(frequency, duration_ms)` -- a blocking call to the Windows kernel beep driver. Simple and zero-dependency.

**Linux equivalents**:

| Option | Pros | Cons |
|--------|------|------|
| `sounddevice` (already bundled) | Zero new deps; generates sine waves in numpy, plays via PortAudio | ~10 lines of code per beep |
| `paplay` / `aplay` (subprocess) | Uses system audio | Requires pre-recorded WAV files or `sox` |
| `beep` command | Simple | Not installed by default; requires pcspkr module |
| `pygame.mixer` | Well-tested | Large dependency |

**Recommended**: Generate sine waves with numpy and play via sounddevice, which is already a project dependency.

```python
# platform/_linux.py (audio cues)

import numpy as np
import sounddevice as sd

def play_beep(frequency: int, duration_ms: int) -> None:
    """Play a beep tone using sounddevice (cross-platform)."""
    sample_rate = 22050
    t = np.linspace(0, duration_ms / 1000.0, int(sample_rate * duration_ms / 1000), endpoint=False)
    wave = (np.sin(2 * np.pi * frequency * t) * 0.3 * 32767).astype(np.int16)
    sd.play(wave, samplerate=sample_rate, blocking=True)
```

**Effort**: 0.5 day. The `notifications.py` module is small (81 lines). Replace `winsound.Beep` with the `play_beep` platform function.

**Bonus**: This approach also works on Windows, so it could eventually replace `winsound.Beep` entirely, eliminating the platform branch for audio cues. However, `winsound.Beep` uses the PC speaker driver which is more lightweight.


### 4.4 Single-Instance Mutex (main.py) -- MEDIUM

**Current implementation**: `kernel32.CreateMutexW` with a named global mutex (`Global\VoicePasteToolMutex`).

**Linux equivalent**: File-based lock (`fcntl.flock` or `fcntl.lockf`) on a known path.

```python
# platform/_linux.py

import fcntl
import os

_LOCK_PATH = os.path.expanduser("~/.local/share/VoicePaste/.lock")
_lock_fd = None

def acquire_single_instance_lock() -> bool:
    global _lock_fd
    os.makedirs(os.path.dirname(_LOCK_PATH), exist_ok=True)
    _lock_fd = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (OSError, IOError):
        _lock_fd.close()
        _lock_fd = None
        return False

def release_single_instance_lock() -> None:
    global _lock_fd
    if _lock_fd is not None:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        _lock_fd.close()
        _lock_fd = None
        try:
            os.unlink(_LOCK_PATH)
        except OSError:
            pass
```

**Effort**: 0.5 day.


### 4.5 Fatal Error Dialogs (main.py) -- MEDIUM

**Current implementation**: `ctypes.windll.user32.MessageBoxW` with MB_OK | MB_ICONERROR.

**Linux equivalent**:
- `zenity --error --text="..."` (GNOME, widely available).
- `kdialog --error "..."` (KDE).
- `xmessage "..."` (basic X11 fallback).
- `tkinter.messagebox` (Python built-in, but requires Tk).

**Recommended**: Try `zenity` first, fall back to `tkinter.messagebox`, fall back to `stderr`.

```python
# platform/_linux.py

def show_fatal_error(message: str, title: str = "Voice Paste") -> None:
    """Show a fatal error dialog on Linux."""
    import shutil
    import subprocess

    if shutil.which("zenity"):
        subprocess.run(
            ["zenity", "--error", "--title", title, "--text", message],
            timeout=30,
        )
    elif shutil.which("kdialog"):
        subprocess.run(
            ["kdialog", "--error", message, "--title", title],
            timeout=30,
        )
    else:
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror(title, message)
            root.destroy()
        except Exception:
            print(f"FATAL ERROR: {title}: {message}", file=sys.stderr)
```

**Effort**: 0.25 day.


### 4.6 Debug Console Allocation (main.py) -- LOW

**Current implementation**: `kernel32.AllocConsole()` to create a console window for `--noconsole` PyInstaller builds.

**Linux equivalent**: Not needed. On Linux, the application always has a controlling terminal when launched from a terminal emulator. When launched from a desktop entry (`.desktop` file), stdout/stderr go to the journal. No action required.

**Implementation**: The `_enable_debug_console()` function simply becomes a no-op on Linux (or is skipped via platform check).

**Effort**: 0.1 day.


### 4.7 Floating Overlay (overlay.py) -- NOT APPLICABLE

**Status**: The overlay is **DISABLED** in the current codebase (`main.py` line 325: `self._overlay = None`). It was disabled due to a Python 3.14 tkinter conflict (dual `tk.Tk()` instances on separate threads). The code exists but is not instantiated or used.

**Decision for Linux port**: Skip entirely. The overlay is not part of the active product on Windows either. If/when it is re-enabled on Windows (possibly rebuilt with a non-tkinter approach), a Linux version can be considered at that time.

**Effort**: 0 days (excluded from scope).


### 4.8 System Tray (tray.py) -- MEDIUM

**Current implementation**: `pystray` with the Win32 backend. Icon generation via Pillow. Menu items, tooltip, notifications via `icon.notify()`.

**Linux compatibility**: `pystray` officially supports Linux via the `AppIndicator` backend (libappindicator / ayatana-appindicator) and the `Xorg` backend (X11 system tray protocol). The library auto-detects the backend.

**Issues**:
1. **GNOME 3.26+ removed the system tray.** Users need the "AppIndicator and KStatusNotifierItem Support" GNOME Shell extension, or `gnome-shell-extension-appindicator` package. Without it, the tray icon is invisible.
2. **KDE Plasma** supports tray icons natively via StatusNotifierItem (SNI).
3. **XFCE, MATE, Cinnamon** support traditional X11 system trays.
4. **`icon.notify()`** (balloon/toast notifications): On Linux, pystray uses `notify2` or `plyer` for notifications if available, or falls back to `notify-send` (libnotify). This should work on most desktop environments.
5. **Icon format**: Pillow generates the image in memory. This is cross-platform. No changes needed.
6. **`icon.visible = True`**: The manual visibility fix in `_on_tray_ready` is a Win32 quirk. On Linux, pystray may handle this differently. Needs testing.

**Required actions**:
- Add `libappindicator` or `ayatana-appindicator3` to Linux dependencies.
- Test `icon.notify()` on GNOME and KDE.
- Document the GNOME extension requirement.

**Effort**: 0.5-1 day (testing and documentation, minimal code changes).


### 4.9 Credential Storage (keyring_store.py) -- LOW

**Current implementation**: Uses the `keyring` Python library, which auto-detects the backend. On Windows, it uses Windows Credential Manager.

**Linux compatibility**: `keyring` supports `SecretService` (GNOME Keyring, KDE Wallet) out of the box. The `keyring_store.py` code is already fully portable -- it does not import any Windows-specific modules.

**Required actions**:
- Update comments and config template that say "Windows Credential Manager" to say "system credential store" or "keyring".
- Ensure `secretstorage` Python package is in the Linux dependencies (required for SecretService backend).

**Effort**: 0.25 day (comment updates, dependency addition).


### 4.10 Data/Model Paths -- LOW

**Current implementation**: Several modules use `os.environ.get("LOCALAPPDATA")` to locate the model cache and TTS cache directories. This returns an empty string on Linux.

**Linux equivalent**: Follow the XDG Base Directory Specification:
- Cache: `$XDG_CACHE_HOME/VoicePaste/` (default: `~/.cache/VoicePaste/`)
- Data: `$XDG_DATA_HOME/VoicePaste/` (default: `~/.local/share/VoicePaste/`)

**Implementation**: Create a `get_data_dir()` / `get_cache_dir()` function in the platform module:

```python
# platform/_linux.py

def get_cache_dir() -> Path:
    """Return the XDG cache directory for VoicePaste."""
    base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return Path(base) / "VoicePaste"

def get_data_dir() -> Path:
    """Return the XDG data directory for VoicePaste."""
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(base) / "VoicePaste"
```

```python
# platform/_windows.py

def get_cache_dir() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    return Path(local_appdata) / "VoicePaste"

def get_data_dir() -> Path:
    return get_cache_dir()  # Windows uses same dir
```

**Affected files**: `model_manager.py`, `tts_model_manager.py`, `tts_cache.py`, `settings_dialog.py`.

**Effort**: 0.5 day.


### 4.11 Settings Dialog Dark Mode (settings_dialog.py) -- LOW

**Current implementation**: Uses `ctypes.windll.dwmapi.DwmSetWindowAttribute` to apply a dark title bar on Windows 10/11.

**Linux equivalent**: Desktop themes handle title bar colors. No special API call needed. The function should be a no-op on Linux.

**Effort**: 0.1 day (wrap the DWM call in a platform check or move to platform module).


### 4.12 Config File Comments (config.py) -- LOW

**Current implementation**: The `CONFIG_TEMPLATE` and `save_to_toml()` output contain comments referencing "Windows Credential Manager."

**Required action**: Change comments to be platform-neutral: "system credential store (Credential Manager on Windows, keyring on Linux)."

**Effort**: 0.1 day.


### 4.13 Build and Packaging -- MEDIUM

**Current implementation**: PyInstaller `--onefile` producing `VoicePaste.exe` via `build.bat` and `voice_paste.spec`.

**Linux equivalents**:

| Format | Pros | Cons |
|--------|------|------|
| **PyInstaller `--onefile`** | Same toolchain, produces single `VoicePaste` binary | Large binary, slow startup (extraction), users may not trust random binaries |
| **AppImage** | Single file, no installation, widely understood | Requires `appimagetool`, desktop integration is manual |
| **Flatpak** | Sandboxed, auto-updates, works on all distros | Complex packaging, sandbox may break PortAudio/X11 access |
| **Snap** | Ubuntu-native, auto-updates | Snap confinement can break audio, X11, hotkeys |
| **.deb / .rpm** | Native package managers | Need separate builds per distro family |
| **pip install** | Standard Python distribution | Requires Python, not "just works" for end users |

**Recommended**: Two outputs:
1. **PyInstaller `--onefile`** on Linux -- produces a single portable binary. Familiar to the project, keeps parity with Windows.
2. **AppImage** (stretch goal) -- wraps the PyInstaller output for better desktop integration (icon, `.desktop` file).

**Implementation**:
- Create `build_linux.sh` alongside `build.bat`.
- Create a `voice_paste_linux.spec` PyInstaller spec file.
- PortAudio (`libportaudio2`) must be bundled or documented as a system dependency.

**Effort**: 1-2 days.

---

## 5. Already Portable Modules

The following 15 modules require **zero changes** for Linux:

| Module | Lines | Why it is portable |
|--------|-------|-------------------|
| `audio.py` | 327 | sounddevice + numpy (cross-platform) |
| `stt.py` | ~150 | OpenAI API client (HTTP) |
| `local_stt.py` | ~200 | faster-whisper (CTranslate2, cross-platform) |
| `summarizer.py` | ~150 | OpenAI API client (HTTP) |
| `tts.py` | ~80 | Protocol + ElevenLabs HTTP client |
| `local_tts.py` | 740 | ONNX inference + espeak-ng (cross-platform with `espeak-ng` installed) |
| `audio_playback.py` | 119 | sounddevice + miniaudio (cross-platform) |
| `icon_drawing.py` | ~80 | Pillow only |
| `constants.py` | 485 | Pure Python constants |
| `integrity.py` | ~60 | hashlib + pathlib |
| `api_server.py` | ~200 | http.server (stdlib) |
| `tts_cache.py`* | ~300 | JSON + pathlib (*path logic needs platform function) |
| `tts_export.py` | ~150 | pathlib + wave |
| `wake_word.py` | ~250 | faster-whisper + sounddevice |
| `settings_dialog.py`* | ~2200 | tkinter (*DWM dark title bar call needs platform guard) |

*Minor platform-specific touches noted, but core logic is portable.

**espeak-ng note**: `local_tts.py` uses `espeakng-loader` which bundles the espeak-ng DLL for Windows. On Linux, espeak-ng is a system package (`apt install espeak-ng`). The `ctypes.CDLL` loading path may differ:
- Windows: `espeakng_loader.get_library_path()` returns the bundled DLL.
- Linux: `espeakng_loader` may not be needed; load `libespeak-ng.so.1` from system path.

This is a minor branching point in `local_tts.py` (the `EspeakPhonemizer._ensure_initialized` method).

---

## 6. The Wayland Problem

Wayland is the default display protocol on:
- Ubuntu 22.04+ (GNOME)
- Fedora 34+ (GNOME)
- KDE Plasma 5.27+ (opt-in, default in some distros)

Wayland fundamentally restricts application behavior that Voice Paste depends on:

| Capability | X11 | Wayland | Impact |
|-----------|-----|---------|--------|
| Global hotkeys (keyboard grab) | xlib / pynput works | No standard protocol; `ydotool` (evdev) or D-Bus GlobalShortcuts portal | **HARD BLOCK** without workaround |
| Keystroke injection (Ctrl+V) | xdotool works | No standard protocol; `ydotool` requires uinput permissions | **HARD BLOCK** for paste |
| Clipboard access | xclip works | wl-copy/wl-paste works | OK |
| Always-on-top overlay | _NET_WM_STATE atom | Layer Shell (wlroots only) | Partial |
| System tray icon | X11 tray protocol | StatusNotifierItem / AppIndicator | OK with extension |
| Window focus prevention | WS_EX_NOACTIVATE / xprop | Not standardized | Degraded |

### Wayland Strategy Options

**Option A: X11-only, document Wayland as unsupported (recommended for v1.1)**
- Users on Wayland can switch their session to X11 (`GDK_BACKEND=x11` or select "GNOME on Xorg" at login).
- XWayland provides an X11 compatibility layer, and `pynput` works under XWayland for hotkeys within X11 windows.
- This is the pragmatic approach: ship what works, document clearly.

**Option B: Wayland with degraded experience**
- Hotkeys: Use `ydotool` (user must install and have uinput permissions).
- Paste: Use `wl-copy` for clipboard, `ydotool` for Ctrl+V simulation.
- Overlay: Regular topmost window (may steal focus).
- Global hotkeys: Fallback to HTTP API + a companion script that uses D-Bus GlobalShortcuts.

**Option C: Wayland-native via D-Bus portals (future)**
- Implement GlobalShortcuts portal for hotkeys.
- Not yet widely supported (GNOME 44+ only, KDE partial).
- Requires async D-Bus client.
- Significant development effort (3-5 days on its own).

**Recommendation**: Start with Option A for v1.1. Add Option B as experimental in v1.2. Plan Option C for when the D-Bus GlobalShortcuts portal is standardized across major DEs.

---

## 7. Platform Module Architecture

### Directory Structure After Migration

```
src/
  platform/
    __init__.py            # Detection shim, re-exports correct backend
    _interface.py          # Protocol definitions (type hints)
    _windows.py            # Windows implementations (moved from paste.py, overlay.py, etc.)
    _linux.py              # Linux implementations
  main.py                  # Updated imports from platform.*
  hotkey.py                # Updated: uses keyboard (Win) or pynput (Linux) via platform
  notifications.py         # Updated: uses winsound (Win) or sounddevice (Linux) via platform
  overlay.py               # Updated: _apply_win32_styles -> platform.apply_overlay_styles
  config.py                # Updated: platform-neutral comments
  keyring_store.py         # No changes (already portable)
  tray.py                  # No changes (pystray handles backend selection)
  audio.py                 # No changes
  stt.py                   # No changes
  local_stt.py             # No changes
  summarizer.py            # No changes
  tts.py                   # No changes
  local_tts.py             # Minor: espeak-ng path branching
  audio_playback.py        # No changes
  ... (remaining files)    # No changes
```

### Interface Definitions

```python
# platform/_interface.py

from typing import Protocol, Optional
from pathlib import Path

class ClipboardOps(Protocol):
    def backup(self) -> Optional[str]: ...
    def restore(self, text: Optional[str]) -> None: ...
    def paste_text(self, text: str) -> bool: ...

class SingleInstanceLock(Protocol):
    def acquire(self) -> bool: ...
    def release(self) -> None: ...

class AudioCues(Protocol):
    def beep(self, frequency: int, duration_ms: int) -> None: ...

class FatalDialog(Protocol):
    def show(self, message: str, title: str) -> None: ...

def get_cache_dir() -> Path: ...
def get_data_dir() -> Path: ...
def apply_overlay_styles(tk_window) -> None: ...
def get_work_area() -> tuple[int, int, int, int]: ...
```

---

## 8. Implementation Plan

### Phase 1: Foundation (3-4 days)

**Goal**: Platform module infrastructure and the two HARD BLOCK components.

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 1.1 | Create `platform/` package with detection shim | 0.5d | `platform/__init__.py`, `_interface.py` |
| 1.2 | Move Win32 clipboard/paste code to `platform/_windows.py` | 0.5d | `_windows.py`, `paste.py` (becomes thin shim) |
| 1.3 | Implement Linux clipboard/paste (xclip, xdotool) | 1d | `_linux.py` |
| 1.4 | Implement Linux hotkey manager (pynput) | 1d | `_linux.py`, `hotkey.py` refactor |
| 1.5 | Update `main.py` imports and platform branching | 0.5d | `main.py` |

### Phase 2: MEDIUM Components (2-3 days)

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 2.1 | Linux single-instance lock (fcntl) | 0.25d | `_linux.py` |
| 2.2 | Linux fatal error dialog (zenity/tkinter) | 0.25d | `_linux.py` |
| 2.3 | Linux audio cues (sounddevice sine wave) | 0.5d | `_linux.py`, `notifications.py` |
| 2.4 | Linux overlay styles (X11 window type hints) | 1d | `_linux.py`, `overlay.py` |
| 2.5 | XDG path resolution | 0.25d | `_linux.py`, update model_manager/tts_cache |
| 2.6 | System tray testing and GNOME extension documentation | 0.5d | `tray.py` (minor), README |

### Phase 3: Build and Polish (2-3 days)

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 3.1 | PyInstaller spec for Linux | 0.5d | `voice_paste_linux.spec`, `build_linux.sh` |
| 3.2 | espeak-ng system library loading for local_tts.py | 0.5d | `local_tts.py` |
| 3.3 | Platform-neutral comments in config.py | 0.25d | `config.py` |
| 3.4 | Test suite: mock platform functions, add Linux paths | 1d | `tests/` |
| 3.5 | README: Linux section (dependencies, X11 requirement, Wayland status) | 0.5d | README.md |
| 3.6 | CI: GitHub Actions matrix (Windows + Ubuntu) | 0.5d | `.github/workflows/` |

### Phase 4: Wayland Experimental (3-5 days, deferred)

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 4.1 | ydotool integration for paste on Wayland | 1d | `_linux.py` |
| 4.2 | wl-clipboard integration | 0.5d | `_linux.py` |
| 4.3 | D-Bus GlobalShortcuts portal integration | 2d | new `_linux_dbus.py` |
| 4.4 | Wayland overlay (Layer Shell or fallback) | 1d | `_linux.py` |

---

## 9. Packaging Strategy for Linux

### System Dependencies

The following must be installed on the user's system (cannot be bundled in PyInstaller):

```
# Debian/Ubuntu
sudo apt install \
    libportaudio2 \       # PortAudio for sounddevice
    xclip \               # Clipboard on X11
    xdotool \             # Keystroke simulation on X11
    espeak-ng \           # Phonemizer for local TTS
    libappindicator3-1 \  # System tray on GNOME (or ayatana variant)
    python3-tk            # tkinter for settings dialog

# Optional (Wayland)
sudo apt install wl-clipboard ydotool
```

### PyInstaller Binary

```bash
# build_linux.sh
pyinstaller \
    --onefile \
    --name VoicePaste \
    --hidden-import pynput.keyboard._xorg \
    --hidden-import pynput.mouse._xorg \
    --add-data "src/platform:platform" \
    src/main.py
```

The resulting `VoicePaste` binary (no extension) can be placed anywhere and executed directly.

### AppImage (Stretch Goal)

```
VoicePaste/
  AppRun                  # Shell script: exec ./VoicePaste "$@"
  VoicePaste              # PyInstaller binary
  voicepaste.desktop      # Desktop entry
  voicepaste.png          # Application icon
```

Wrapped with `appimagetool` into `VoicePaste-x86_64.AppImage`.

---

## 10. Effort Estimate Summary

| Phase | Effort | Scope |
|-------|--------|-------|
| Phase 1: Foundation (HARD BLOCKs) | 3-4 days | Clipboard, paste, hotkeys, platform module |
| Phase 2: MEDIUM Components | 2-3 days | Mutex, dialogs, audio, overlay, paths, tray |
| Phase 3: Build and Polish | 2-3 days | PyInstaller, tests, docs, CI |
| **Total (X11 support)** | **7-10 days** | **Full Linux/X11 support** |
| Phase 4: Wayland (deferred) | 3-5 days | Experimental Wayland support |
| **Total (including Wayland)** | **10-15 days** | **Linux X11 + Wayland experimental** |

---

## 11. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Wayland prevents global hotkeys | HIGH | HIGH | X11-only for v1.1; HTTP API as fallback |
| pynput does not work on some Linux DEs | MEDIUM | MEDIUM | Test on GNOME/KDE/XFCE; fallback to keyboard lib with docs about input group |
| System tray invisible on GNOME | HIGH | MEDIUM | Document GNOME extension requirement prominently |
| PortAudio not found at runtime | MEDIUM | HIGH | Bundle libportaudio in PyInstaller or document as prereq |
| espeak-ng system library version mismatch | LOW | MEDIUM | Test on Ubuntu 22.04, 24.04; document minimum version |
| PyInstaller Linux binary is >200 MB | MEDIUM | LOW | Acceptable for local-model builds; cloud-only build stays small |
| `pystray` AppIndicator backend has bugs | LOW | MEDIUM | Test on target DEs; fallback to Xorg backend |
| Clipboard race conditions with xclip subprocess | LOW | LOW | Add retry logic (same pattern as Win32 retry in current paste.py) |

---

## 12. Decision: Recommended Approach

### Phase 1 Target: Linux/X11 (v1.1.0)

1. **Create `src/platform/` package** with the strategy pattern as described in Section 7.
2. **Implement clipboard + paste** for X11 using `xclip` + `xdotool` (subprocess).
3. **Implement global hotkeys** using `pynput` (X11 only).
4. **Replace `winsound.Beep`** with sounddevice sine-wave generation (this is also a valid Windows implementation, so consider using it on both platforms to reduce platform branching).
5. **Implement single-instance lock** via `fcntl.flock`.
6. **Keep `pystray`** for system tray (already cross-platform).
7. **Keep `keyring`** for credentials (already cross-platform).
8. **Build with PyInstaller** on Linux.
9. **Document X11 requirement clearly.** Wayland users are directed to use "GNOME on Xorg" or the HTTP API.

### New Python Dependencies for Linux

| Package | Purpose | License |
|---------|---------|---------|
| `pynput` | Global hotkeys on X11 | LGPL-3.0 |
| `secretstorage` | Keyring backend for Linux | BSD |
| `python-xlib` | Transitive dep of pynput | LGPL-2.1+ |

### Not Changing

- The Windows code remains untouched. No regressions.
- The existing `keyboard` library stays for Windows hotkeys.
- The existing `winsound` stays for Windows audio cues.
- The existing Win32 clipboard code stays for Windows paste.
- All 15 already-portable modules stay as-is.

### Deferred

- Wayland support (Phase 4, v1.2.0+).
- D-Bus GlobalShortcuts portal integration.
- AppImage packaging.
- macOS support (not analyzed in this ADR).

---

## Appendix A: Module Portability Matrix

| Module | Win32 Calls | Portable | Linux Effort | Notes |
|--------|------------|----------|-------------|-------|
| `paste.py` | 15+ | NO | 2-3d | Complete rewrite for Linux |
| `overlay.py` | 10+ | NO | 1-2d | Win32 styles need X11 replacement |
| `hotkey.py` | 0 (but keyboard lib) | NO | 1-2d | keyboard needs root on Linux |
| `main.py` | 5 | PARTIAL | 1d | Mutex, MessageBox, AllocConsole |
| `notifications.py` | 2 | NO | 0.5d | winsound.Beep replacement |
| `settings_dialog.py` | 2 | MOSTLY | 0.25d | DWM dark title bar |
| `config.py` | 0 | MOSTLY | 0.25d | Comment text only |
| `model_manager.py` | 0 | MOSTLY | 0.25d | LOCALAPPDATA path |
| `tts_model_manager.py` | 0 | MOSTLY | 0.25d | LOCALAPPDATA path |
| `tts_cache.py` | 0 | MOSTLY | 0.25d | LOCALAPPDATA path |
| `keyring_store.py` | 0 | YES | 0d | keyring auto-detects backend |
| `audio.py` | 0 | YES | 0d | sounddevice is cross-platform |
| `stt.py` | 0 | YES | 0d | HTTP API client |
| `local_stt.py` | 0 | YES | 0d | faster-whisper is cross-platform |
| `summarizer.py` | 0 | YES | 0d | HTTP API client |
| `tts.py` | 0 | YES | 0d | Protocol + HTTP client |
| `local_tts.py` | 0 | MOSTLY | 0.25d | espeak-ng path may differ |
| `audio_playback.py` | 0 | YES | 0d | sounddevice + miniaudio |
| `icon_drawing.py` | 0 | YES | 0d | Pillow only |
| `constants.py` | 0 | YES | 0d | Pure Python |
| `integrity.py` | 0 | YES | 0d | hashlib + pathlib |
| `api_server.py` | 0 | YES | 0d | http.server stdlib |
| `tts_export.py` | 0 | YES | 0d | pathlib + wave |
| `wake_word.py` | 0 | YES | 0d | faster-whisper + sounddevice |

## Appendix B: Dependency Comparison

### Windows-only Dependencies (current)
```
keyboard          # Uses _winkeyboard backend
winsound          # Stdlib, Windows only
ctypes.windll     # Windows DLL access
ctypes.wintypes   # Windows type definitions
```

### Linux-only Dependencies (proposed)
```
pynput            # X11 global hotkeys
python-xlib       # Transitive (from pynput)
secretstorage     # Keyring backend
```

### System Packages Required on Linux
```
libportaudio2     # PortAudio runtime
xclip             # Clipboard (X11)
xdotool           # Keystroke simulation (X11)
espeak-ng         # Phonemizer for local TTS
libappindicator3  # System tray (GNOME)
```

### Shared Dependencies (no changes)
```
pystray           # System tray (cross-platform)
Pillow            # Icon generation
sounddevice       # Audio capture and playback
numpy             # Audio processing
miniaudio         # MP3/WAV decoding
openai            # STT and summarization API
keyring           # Credential storage (cross-platform)
```
