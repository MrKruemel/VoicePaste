# Architecture Decision Record: Ubuntu Cross-Platform Support

**Date**: 2026-02-20
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.9.1 (current, Windows-only)
**Target Version**: 1.1.0 (cross-platform Windows + Ubuntu)

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
   - 4.7 [System Tray (tray.py) -- MEDIUM](#47-system-tray-traypy----medium)
   - 4.8 [Credential Storage (keyring_store.py) -- LOW](#48-credential-storage-keyring_storepy----low)
   - 4.9 [Data/Model Paths -- LOW](#49-datamodel-paths----low)
   - 4.10 [Settings Dialog Dark Mode (settings_dialog.py) -- LOW](#410-settings-dialog-dark-mode-settings_dialogpy----low)
   - 4.11 [Config File Comments (config.py) -- LOW](#411-config-file-comments-configpy----low)
   - 4.12 [Build and Packaging -- MEDIUM](#412-build-and-packaging----medium)
5. [Already Portable Modules](#5-already-portable-modules)
6. [The Wayland Problem (Ubuntu-Specific)](#6-the-wayland-problem-ubuntu-specific)
7. [Platform Module Architecture](#7-platform-module-architecture)
8. [Implementation Plan](#8-implementation-plan)
9. [Packaging Strategy for Ubuntu](#9-packaging-strategy-for-ubuntu)
10. [Effort Estimate Summary](#10-effort-estimate-summary)
11. [Risk Assessment](#11-risk-assessment)
12. [Decision: Recommended Approach](#12-decision-recommended-approach)

---

## 1. Executive Summary

Voice Paste is a ~2600 SLOC Python desktop application that currently runs exclusively on Windows. This ADR analyzes every Windows-specific dependency and proposes a concrete plan for **Ubuntu Linux** support, targeting **Ubuntu 22.04 LTS** and **Ubuntu 24.04 LTS** as the primary test platforms.

**Key findings:**

- **24 source files** total. **6 files** contain Windows-specific code (25%).
- **15 files** are already fully portable (63%), needing zero changes.
- **3 files** are partially portable (minor platform branching needed).
- **1 file** is 100% Windows-specific and needs a complete Linux rewrite: `paste.py`.
- The **biggest risk** is Wayland vs X11. Ubuntu defaults to Wayland (GNOME) since 22.04, but global hotkeys and keystroke injection have no standard Wayland API. Users can select the "Ubuntu on Xorg" session at the login screen for full compatibility.
- **Estimated effort**: 6-9 developer days for a "works on X11" release, plus 3-5 additional days for Wayland workarounds.

**Recommended approach**: Platform abstraction via a `platform/` package with `platform/windows.py` and `platform/linux.py` modules, behind a common interface. The existing Windows code is left intact. New Linux implementations are added alongside. A platform detection shim selects the correct backend at import time.

**Target systems:**

| Ubuntu Version | Default Session | Kernel | GNOME | Support Until |
|---------------|----------------|--------|-------|---------------|
| 22.04 LTS (Jammy) | Wayland (GNOME 42) | 5.15+ | 42 | April 2027 |
| 24.04 LTS (Noble) | Wayland (GNOME 46) | 6.8+ | 46 | April 2029 |

Both versions offer an "Ubuntu on Xorg" session selectable at the GDM login screen, which provides full X11 compatibility.

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
    _linux.py            # Linux (Ubuntu) implementations
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

**Ubuntu X11 equivalent**:
- **Clipboard read/write**: `xclip -selection clipboard` or `xsel --clipboard`. Both are in the Ubuntu repositories. Python subprocess call is reliable and simple.
- **Clipboard alternative**: `subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True)` for read, pipe text to `xclip -selection clipboard -i` for write.
- **Keystroke simulation**: `xdotool key ctrl+v` on X11. Available via `sudo apt install xdotool`.
- **Pure Python fallback**: `python-xlib` can do both clipboard and keystroke injection without external tools, but adds a heavyweight dependency.

**Ubuntu Wayland equivalent**:
- **Clipboard**: `wl-copy` (write) and `wl-paste` (read) from `wl-clipboard` package (`sudo apt install wl-clipboard`).
- **Keystroke simulation**: **Not natively supported on Wayland.** There is no universal equivalent to `xdotool` on Wayland. Possible workarounds:
  - `wtype` (wlroots-based compositors only, **not GNOME**).
  - `ydotool` (requires root or uinput group; simulates at evdev level). Available in Ubuntu 22.04+ repos.
  - D-Bus protocols (compositor-specific, not standardized).
  - **This is the single biggest technical risk for Ubuntu support.**

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

**Effort**: 2-3 days (implement + test across X11 and Wayland sessions on Ubuntu).

**Ubuntu packages**: `sudo apt install xclip xdotool` (X11), `sudo apt install wl-clipboard ydotool` (Wayland, optional).


### 4.2 Global Hotkeys (hotkey.py) -- HARD BLOCK

**Current implementation**: Uses the `keyboard` Python library, which on Windows hooks into `_winkeyboard` for low-level keyboard monitoring. The library works on Linux BUT requires **root privileges** (or read access to `/dev/input/*`) because it reads raw input events from evdev.

**Option A: `keyboard` library with root (not recommended)**
- Requires `sudo` or adding the user to the `input` group.
- Not acceptable for a desktop application that should "just work."

**Option B: `pynput` library (recommended for X11)**
- `pynput.keyboard.GlobalHotKeys` works on X11 without root.
- Uses Xlib under the hood (`python-xlib` dependency).
- **Does NOT work on native Wayland** (X11-only by design, unless XWayland is available).
- API is different from `keyboard` library (context-manager based).

**Option C: D-Bus global shortcuts (Wayland-compatible, Ubuntu 24.04+)**
- GNOME 44+ supports the `org.freedesktop.portal.GlobalShortcuts` D-Bus interface.
- Ubuntu 22.04 ships GNOME 42, which does NOT support GlobalShortcuts.
- Ubuntu 24.04 ships GNOME 46, which DOES support GlobalShortcuts.
- Python access via `dbus-next` (async) or `pydbus`.
- Registration is asynchronous and requires user consent via a system dialog.
- This is the "correct" Wayland approach, but only works on Ubuntu 24.04+.

**Option D: `keybinder` / `libkeybinder` (X11 only)**
- GObject-based, works without root on X11.
- Python bindings via `gi.repository.Keybinder`.
- Requires GTK and GObject introspection.

**Recommended implementation**: `pynput` for X11 as the primary path. Document that Wayland users need to either:
1. Select "Ubuntu on Xorg" at the GDM login screen.
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

**Ubuntu equivalents**:

| Option | Pros | Cons |
|--------|------|------|
| `sounddevice` (already bundled) | Zero new deps; generates sine waves in numpy, plays via PortAudio | ~10 lines of code per beep |
| `paplay` / `aplay` (subprocess) | Uses PulseAudio/PipeWire | Requires pre-recorded WAV files |
| `beep` command | Simple | Not installed by default; requires pcspkr module |

**Recommended**: Generate sine waves with numpy and play via sounddevice, which is already a project dependency. Both Ubuntu 22.04 (PulseAudio) and 24.04 (PipeWire) support PortAudio output.

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

**Ubuntu audio note**: Ubuntu 22.04 uses PulseAudio by default. Ubuntu 24.04 uses PipeWire with PulseAudio compatibility layer. Both expose PortAudio-compatible APIs, so sounddevice works identically on both.


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

**Ubuntu equivalent**: `zenity` is pre-installed on Ubuntu with GNOME desktop. It is the most reliable choice.

**Recommended**: Try `zenity` first (pre-installed on Ubuntu GNOME), fall back to `tkinter.messagebox`, fall back to `stderr`.

```python
# platform/_linux.py

def show_fatal_error(message: str, title: str = "Voice Paste") -> None:
    """Show a fatal error dialog on Ubuntu."""
    import shutil
    import subprocess

    # zenity is pre-installed on Ubuntu GNOME desktop
    if shutil.which("zenity"):
        subprocess.run(
            ["zenity", "--error", "--title", title, "--text", message],
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

**Ubuntu note**: `zenity` is part of the `gnome-core` metapackage and is present on all standard Ubuntu desktop installations. The `kdialog` fallback from the previous version of this ADR has been removed since KDE is not a target.


### 4.6 Debug Console Allocation (main.py) -- LOW

**Current implementation**: `kernel32.AllocConsole()` to create a console window for `--noconsole` PyInstaller builds.

**Ubuntu equivalent**: Not needed. On Ubuntu, the application always has a controlling terminal when launched from a terminal emulator. When launched from a `.desktop` file, stdout/stderr go to the systemd journal (accessible via `journalctl --user`). No action required.

**Implementation**: The `_enable_debug_console()` function simply becomes a no-op on Linux (or is skipped via platform check).

**Effort**: 0.1 day.


### 4.7 System Tray (tray.py) -- MEDIUM

**Current implementation**: `pystray` with the Win32 backend. Icon generation via Pillow. Menu items, tooltip, notifications via `icon.notify()`.

**Ubuntu compatibility**: `pystray` officially supports Linux via the `AppIndicator` backend (libappindicator / ayatana-appindicator). The library auto-detects the backend.

**Ubuntu-specific issues**:

1. **GNOME removed the legacy system tray in GNOME 3.26.** Both Ubuntu 22.04 (GNOME 42) and 24.04 (GNOME 46) require the **"AppIndicator and KStatusNotifierItem Support"** GNOME Shell extension for tray icons to appear. This extension is available as:
   ```bash
   sudo apt install gnome-shell-extension-appindicator
   ```
   After installation, enable it via GNOME Extensions app or:
   ```bash
   gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
   ```
   **Without this extension, the tray icon is invisible.** This must be documented prominently.

2. **`libappindicator3-1`** (Ubuntu 22.04) or **`gir1.2-ayatanaappindicator3-0.1`** (Ubuntu 24.04): The pystray AppIndicator backend needs one of these. Ubuntu 24.04 moved from legacy libappindicator to ayatana-appindicator. Both work with pystray.

3. **`icon.notify()`** (toast notifications): On Ubuntu, pystray uses `notify-send` (from `libnotify-bin`, pre-installed on Ubuntu GNOME) for desktop notifications. This integrates with GNOME notification center.

4. **Icon format**: Pillow generates the image in memory. This is cross-platform. No changes needed.

5. **`icon.visible = True`**: The manual visibility fix in `_on_tray_ready` is a Win32 quirk. On Linux with AppIndicator, this may not be needed. Needs testing.

**Required actions**:
- Install `gnome-shell-extension-appindicator` and appropriate `libappindicator` package.
- Test `icon.notify()` on Ubuntu GNOME.
- Document the GNOME extension requirement in README and first-run experience.

**Effort**: 0.5-1 day (testing and documentation, minimal code changes).


### 4.8 Credential Storage (keyring_store.py) -- LOW

**Current implementation**: Uses the `keyring` Python library, which auto-detects the backend. On Windows, it uses Windows Credential Manager.

**Ubuntu compatibility**: `keyring` supports `SecretService` (GNOME Keyring) out of the box. GNOME Keyring is pre-installed on Ubuntu GNOME desktop and starts automatically with the user session. The `keyring_store.py` code is already fully portable -- it does not import any Windows-specific modules.

**Required actions**:
- Update comments and config template that say "Windows Credential Manager" to say "system credential store" or "keyring".
- Ensure `secretstorage` Python package is in the Linux dependencies (required for SecretService backend).
- GNOME Keyring must be unlocked (it is unlocked automatically on login with Ubuntu's default PAM configuration).

**Effort**: 0.25 day (comment updates, dependency addition).


### 4.9 Data/Model Paths -- LOW

**Current implementation**: Several modules use `os.environ.get("LOCALAPPDATA")` to locate the model cache and TTS cache directories. This returns an empty string on Linux.

**Ubuntu equivalent**: Follow the XDG Base Directory Specification:
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


### 4.10 Settings Dialog Dark Mode (settings_dialog.py) -- LOW

**Current implementation**: Uses `ctypes.windll.dwmapi.DwmSetWindowAttribute` to apply a dark title bar on Windows 10/11.

**Ubuntu equivalent**: GNOME desktop themes handle title bar colors. The `prefer-dark` GTK theme setting is respected automatically by tkinter when `gnome-themes-extra` is installed. No special API call needed. The function should be a no-op on Linux.

**Effort**: 0.1 day (wrap the DWM call in a platform check or move to platform module).


### 4.11 Config File Comments (config.py) -- LOW

**Current implementation**: The `CONFIG_TEMPLATE` and `save_to_toml()` output contain comments referencing "Windows Credential Manager."

**Required action**: Change comments to be platform-neutral: "system credential store (Credential Manager on Windows, GNOME Keyring on Ubuntu)."

**Effort**: 0.1 day.


### 4.12 Build and Packaging -- MEDIUM

**Current implementation**: PyInstaller `--onefile` producing `VoicePaste.exe` via `build.bat` and `voice_paste.spec`.

**Ubuntu packaging options**:

| Format | Pros | Cons |
|--------|------|------|
| **PyInstaller `--onefile`** | Same toolchain, produces single `VoicePaste` binary | Large binary, slow startup (extraction), users may not trust random binaries |
| **AppImage** | Single file, no installation, widely understood | Requires `appimagetool`, desktop integration is manual |
| **.deb** | Native Ubuntu package manager, proper dependencies | Need `dpkg-deb` or `fpm`, version-specific builds |
| **Snap** | Ubuntu-native, auto-updates | Snap confinement can break PortAudio/X11 access, hotkeys |
| **pip install** | Standard Python distribution | Requires Python, not "just works" for end users |

**Recommended**: Two outputs:
1. **PyInstaller `--onefile`** on Linux -- produces a single portable binary. Familiar to the project, keeps parity with Windows.
2. **.deb package** (stretch goal) -- wraps the PyInstaller output with proper dependencies for Ubuntu. This is more natural than AppImage on Ubuntu.

**Implementation**:
- Create `build_linux.sh` alongside `build.bat`.
- Create a `voice_paste_linux.spec` PyInstaller spec file.
- PortAudio (`libportaudio2`) must be documented as a system dependency.
- Build on Ubuntu 22.04 for maximum compatibility (older glibc).

**Effort**: 1-2 days.

---

## 5. Already Portable Modules

The following 15 modules require **zero changes** for Ubuntu:

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

**espeak-ng note**: `local_tts.py` uses `espeakng-loader` which bundles the espeak-ng DLL for Windows. On Ubuntu, espeak-ng is a system package:
```bash
sudo apt install espeak-ng
```
The `ctypes.CDLL` loading path may differ:
- Windows: `espeakng_loader.get_library_path()` returns the bundled DLL.
- Ubuntu: `espeakng_loader` may not be needed; load `libespeak-ng.so.1` from system path (`/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1`).

This is a minor branching point in `local_tts.py` (the `EspeakPhonemizer._ensure_initialized` method).

---

## 6. The Wayland Problem (Ubuntu-Specific)

Ubuntu defaults to Wayland since 22.04 LTS. This is the most important platform consideration.

### Ubuntu Session Types

| Session | How to Select | Available On | Global Hotkeys | Keystroke Injection |
|---------|--------------|-------------|----------------|-------------------|
| **Ubuntu** (Wayland, default) | Default at GDM | 22.04, 24.04 | NO standard API | NO standard API |
| **Ubuntu on Xorg** (X11) | GDM gear icon | 22.04, 24.04 | pynput works | xdotool works |

Wayland fundamentally restricts application behavior that Voice Paste depends on:

| Capability | X11 (Ubuntu on Xorg) | Wayland (Ubuntu default) | Impact |
|-----------|-----|---------|--------|
| Global hotkeys (keyboard grab) | pynput / xlib works | No standard protocol; D-Bus GlobalShortcuts (GNOME 44+ / Ubuntu 24.04+ only) | **HARD BLOCK** on 22.04 Wayland |
| Keystroke injection (Ctrl+V) | xdotool works | `ydotool` requires uinput permissions | **HARD BLOCK** for paste |
| Clipboard access | xclip works | wl-copy/wl-paste works | OK |
| System tray icon | AppIndicator works | AppIndicator works (via GNOME extension) | OK with extension |

### Wayland Strategy Options

**Option A: X11-only, document Wayland as unsupported (recommended for v1.1)**
- Users on Wayland select "Ubuntu on Xorg" at the GDM login screen (available on both 22.04 and 24.04).
- This is a one-time setting that persists across logins.
- This is the pragmatic approach: ship what works, document clearly.

**Option B: Wayland with degraded experience**
- Hotkeys: Use `ydotool` (user must be in `input` group for uinput access).
- Paste: Use `wl-copy` for clipboard, `ydotool` for Ctrl+V simulation.
- Global hotkeys: Fallback to HTTP API + a companion script or keybinding via GNOME Settings.

**Option C: Wayland-native via D-Bus portals (future, Ubuntu 24.04+ only)**
- Implement GlobalShortcuts portal for hotkeys.
- Ubuntu 22.04 (GNOME 42) does NOT support this portal.
- Ubuntu 24.04 (GNOME 46) DOES support this portal.
- Requires async D-Bus client.
- Significant development effort (3-5 days on its own).

**Recommendation**: Start with Option A for v1.1. Add Option B as experimental in v1.2. Plan Option C for when Ubuntu 22.04 is no longer a target (post-April 2027).

### Switching to X11 on Ubuntu

Users can switch to X11 without any system changes:

1. Log out of the current session.
2. At the GDM login screen, click the gear icon in the bottom-right.
3. Select "Ubuntu on Xorg."
4. Log in normally.

This persists across reboots until changed. All desktop applications work identically on Xorg. The only difference is the display protocol.

---

## 7. Platform Module Architecture

### Directory Structure After Migration

```
src/
  platform/
    __init__.py            # Detection shim, re-exports correct backend
    _interface.py          # Protocol definitions (type hints)
    _windows.py            # Windows implementations (moved from paste.py, etc.)
    _linux.py              # Ubuntu/Linux implementations
  main.py                  # Updated imports from platform.*
  hotkey.py                # Updated: uses keyboard (Win) or pynput (Linux) via platform
  notifications.py         # Updated: uses winsound (Win) or sounddevice (Linux) via platform
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
```

---

## 8. Implementation Plan

### Phase 1: Foundation (3-4 days)

**Goal**: Platform module infrastructure and the two HARD BLOCK components.

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 1.1 | Create `platform/` package with detection shim | 0.5d | `platform/__init__.py`, `_interface.py` |
| 1.2 | Move Win32 clipboard/paste code to `platform/_windows.py` | 0.5d | `_windows.py`, `paste.py` (becomes thin shim) |
| 1.3 | Implement Ubuntu clipboard/paste (xclip, xdotool) | 1d | `_linux.py` |
| 1.4 | Implement Ubuntu hotkey manager (pynput) | 1d | `_linux.py`, `hotkey.py` refactor |
| 1.5 | Update `main.py` imports and platform branching | 0.5d | `main.py` |

### Phase 2: MEDIUM Components (1.5-2 days)

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 2.1 | Ubuntu single-instance lock (fcntl) | 0.25d | `_linux.py` |
| 2.2 | Ubuntu fatal error dialog (zenity/tkinter) | 0.25d | `_linux.py` |
| 2.3 | Ubuntu audio cues (sounddevice sine wave) | 0.5d | `_linux.py`, `notifications.py` |
| 2.4 | XDG path resolution | 0.25d | `_linux.py`, update model_manager/tts_cache |
| 2.5 | System tray testing on Ubuntu GNOME + extension docs | 0.5d | `tray.py` (minor), README |

### Phase 3: Build and Polish (2-3 days)

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 3.1 | PyInstaller spec for Ubuntu | 0.5d | `voice_paste_linux.spec`, `build_linux.sh` |
| 3.2 | espeak-ng system library loading for local_tts.py | 0.5d | `local_tts.py` |
| 3.3 | Platform-neutral comments in config.py | 0.25d | `config.py` |
| 3.4 | Test suite: mock platform functions, add Ubuntu test paths | 1d | `tests/` |
| 3.5 | README: Ubuntu section (deps, X11 requirement, Wayland status) | 0.5d | README.md |
| 3.6 | CI: GitHub Actions matrix (Windows + Ubuntu 22.04/24.04) | 0.5d | `.github/workflows/` |

### Phase 4: Wayland Experimental (3-5 days, deferred)

| Step | Task | Effort | Files |
|------|------|--------|-------|
| 4.1 | ydotool integration for paste on Wayland | 1d | `_linux.py` |
| 4.2 | wl-clipboard integration | 0.5d | `_linux.py` |
| 4.3 | D-Bus GlobalShortcuts portal integration (Ubuntu 24.04+ only) | 2d | new `_linux_dbus.py` |

---

## 9. Packaging Strategy for Ubuntu

### System Dependencies

The following must be installed on the user's Ubuntu system (cannot be bundled in PyInstaller):

```bash
# Ubuntu 22.04 LTS / 24.04 LTS -- Required
sudo apt install \
    libportaudio2 \
    xclip \
    xdotool \
    espeak-ng \
    python3-tk \
    gnome-shell-extension-appindicator

# Ubuntu 22.04 (libappindicator)
sudo apt install libappindicator3-1

# Ubuntu 24.04 (ayatana-appindicator)
sudo apt install gir1.2-ayatanaappindicator3-0.1

# Enable the GNOME tray extension (requires log out / log in)
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com

# Optional (Wayland session support, experimental)
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

### .deb Package (Stretch Goal)

A `.deb` package would allow proper dependency management:

```
voicepaste_1.1.0_amd64/
  DEBIAN/
    control              # Package metadata, dependencies
    postinst             # Enable GNOME extension, show X11 note
  usr/
    bin/
      voicepaste         # PyInstaller binary (or symlink)
    share/
      applications/
        voicepaste.desktop   # Desktop entry
      icons/hicolor/256x256/apps/
        voicepaste.png       # Application icon
```

Built with `dpkg-deb --build` or `fpm`. Dependencies declared in the `control` file ensure `xclip`, `xdotool`, `espeak-ng`, `libportaudio2`, and `gnome-shell-extension-appindicator` are installed automatically.

---

## 10. Effort Estimate Summary

| Phase | Effort | Scope |
|-------|--------|-------|
| Phase 1: Foundation (HARD BLOCKs) | 3-4 days | Clipboard, paste, hotkeys, platform module |
| Phase 2: MEDIUM Components | 1.5-2 days | Mutex, dialogs, audio, paths, tray |
| Phase 3: Build and Polish | 2-3 days | PyInstaller, tests, docs, CI |
| **Total (X11 support)** | **6.5-9 days** | **Full Ubuntu/X11 support** |
| Phase 4: Wayland (deferred) | 3-5 days | Experimental Wayland support |
| **Total (including Wayland)** | **9.5-14 days** | **Ubuntu X11 + Wayland experimental** |

**Savings vs previous estimate**: Removing the overlay component saved ~1 day from Phase 2 (step 2.4 was "Linux overlay styles" at 1 day, now eliminated). The reduced scope also simplifies the platform module (fewer exports, fewer Protocol definitions, fewer tests).

---

## 11. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Wayland prevents global hotkeys | HIGH | HIGH | X11-only for v1.1; document "Ubuntu on Xorg" session switch |
| pynput does not work on Ubuntu GNOME X11 | LOW | HIGH | Test on both 22.04 and 24.04 Xorg sessions; fallback to keyboard lib with docs about input group |
| System tray invisible on Ubuntu GNOME | HIGH | MEDIUM | Document `gnome-shell-extension-appindicator` requirement prominently; check at startup and warn |
| PortAudio not found at runtime | MEDIUM | HIGH | Document `libportaudio2` as prereq; check at startup |
| espeak-ng system library version mismatch | LOW | MEDIUM | Test on Ubuntu 22.04 (espeak-ng 1.50) and 24.04 (espeak-ng 1.52); document minimum version |
| PyInstaller Ubuntu binary is >200 MB | MEDIUM | LOW | Acceptable for local-model builds; cloud-only build stays small |
| `pystray` AppIndicator backend has bugs | LOW | MEDIUM | Test on Ubuntu GNOME 42 and 46 |
| Clipboard race conditions with xclip subprocess | LOW | LOW | Add retry logic (same pattern as Win32 retry in current paste.py) |
| Ubuntu 22.04 PulseAudio vs 24.04 PipeWire differences | LOW | LOW | sounddevice/PortAudio abstracts audio server differences |
| GNOME Keyring locked (headless/SSH session) | LOW | LOW | Document that GUI session is required for credential storage |

---

## 12. Decision: Recommended Approach

### Phase 1 Target: Ubuntu/X11 (v1.1.0)

1. **Create `src/platform/` package** with the strategy pattern as described in Section 7.
2. **Implement clipboard + paste** for X11 using `xclip` + `xdotool` (subprocess).
3. **Implement global hotkeys** using `pynput` (X11 only).
4. **Replace `winsound.Beep`** with sounddevice sine-wave generation (this is also a valid Windows implementation, so consider using it on both platforms to reduce platform branching).
5. **Implement single-instance lock** via `fcntl.flock`.
6. **Keep `pystray`** for system tray (already cross-platform, needs GNOME extension).
7. **Keep `keyring`** for credentials (already cross-platform, uses GNOME Keyring on Ubuntu).
8. **Build with PyInstaller** on Ubuntu.
9. **Document X11 requirement clearly.** Wayland users are directed to select "Ubuntu on Xorg" at the GDM login screen.

### Target Ubuntu Versions

| Version | Tested | Session | Notes |
|---------|--------|---------|-------|
| Ubuntu 22.04 LTS | Primary | X11 (via "Ubuntu on Xorg") | GNOME 42, PulseAudio, espeak-ng 1.50 |
| Ubuntu 24.04 LTS | Primary | X11 (via "Ubuntu on Xorg") | GNOME 46, PipeWire, espeak-ng 1.52 |
| Ubuntu 22.04 Wayland | Experimental | Wayland (default session) | Degraded: no hotkeys, ydotool for paste |
| Ubuntu 24.04 Wayland | Experimental | Wayland (default session) | D-Bus GlobalShortcuts possible (future) |

### New Python Dependencies for Ubuntu

| Package | Purpose | License |
|---------|---------|---------|
| `pynput` | Global hotkeys on X11 | LGPL-3.0 |
| `secretstorage` | Keyring backend for GNOME Keyring | BSD |
| `python-xlib` | Transitive dep of pynput | LGPL-2.1+ |

### Ubuntu System Packages

```bash
# Required (one-time setup)
sudo apt install libportaudio2 xclip xdotool espeak-ng python3-tk \
    gnome-shell-extension-appindicator
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
# Log out and back in for the GNOME extension to take effect
```

### Not Changing

- The Windows code remains untouched. No regressions.
- The existing `keyboard` library stays for Windows hotkeys.
- The existing `winsound` stays for Windows audio cues.
- The existing Win32 clipboard code stays for Windows paste.
- All 15 already-portable modules stay as-is.

### Deferred

- Wayland support (Phase 4, v1.2.0+).
- D-Bus GlobalShortcuts portal integration (Ubuntu 24.04+ only).
- `.deb` packaging.
- macOS support (not analyzed in this ADR).

---

## Appendix A: Module Portability Matrix

| Module | Win32 Calls | Portable | Ubuntu Effort | Notes |
|--------|------------|----------|-------------|-------|
| `paste.py` | 15+ | NO | 2-3d | Complete rewrite for Ubuntu |
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
| `tts_cache.py` | 0 | MOSTLY | 0.25d | LOCALAPPDATA path |

## Appendix B: Dependency Comparison

### Windows-only Dependencies (current)
```
keyboard          # Uses _winkeyboard backend
winsound          # Stdlib, Windows only
ctypes.windll     # Windows DLL access
ctypes.wintypes   # Windows type definitions
```

### Ubuntu-only Dependencies (proposed)
```
pynput            # X11 global hotkeys
python-xlib       # Transitive (from pynput)
secretstorage     # Keyring backend (GNOME Keyring)
```

### Ubuntu System Packages Required
```bash
# Required
sudo apt install \
    libportaudio2                          # PortAudio runtime
    xclip                                  # Clipboard (X11)
    xdotool                                # Keystroke simulation (X11)
    espeak-ng                              # Phonemizer for local TTS
    python3-tk                             # tkinter for settings dialog
    gnome-shell-extension-appindicator     # System tray on GNOME

# Ubuntu 22.04 only
sudo apt install libappindicator3-1

# Ubuntu 24.04 only
sudo apt install gir1.2-ayatanaappindicator3-0.1
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
