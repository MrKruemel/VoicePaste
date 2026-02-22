"""Evdev-based global hotkey backend for Wayland sessions.

On Wayland, pynput (X11/Xlib) cannot capture keyboard events from native
Wayland windows because Wayland isolates input per-client for security.
This module reads /dev/input/event* directly via the ``evdev`` library,
which works regardless of display server but requires the user to be a
member of the ``input`` group.

Public API mirrors the pynput helpers in hotkey.py:
    evdev_add_hotkey(combo, callback) -> int
    evdev_add_key_listener(key_name, callback) -> int
    evdev_remove_hotkey(handle)
    stop_monitor()

v1.3: Initial implementation for Wayland hotkey support.
"""

import grp
import logging
import os
import select
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy evdev import — module may not be installed on Windows or X11-only setups
# ---------------------------------------------------------------------------
try:
    import evdev
    import evdev.ecodes as ecodes
except ImportError:
    evdev = None  # type: ignore[assignment]
    ecodes = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Modifier and key code mappings (Linux input event codes)
# ---------------------------------------------------------------------------

# Each modifier maps to a set of keycodes (left + right variants)
_MODIFIER_CODES: dict[str, set[int]] = {
    "ctrl": {29, 97},       # KEY_LEFTCTRL, KEY_RIGHTCTRL
    "alt": {56, 100},       # KEY_LEFTALT, KEY_RIGHTALT
    "shift": {42, 54},      # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
    "super": {125, 126},    # KEY_LEFTMETA, KEY_RIGHTMETA
    "cmd": {125, 126},      # alias for super
    "win": {125, 126},      # alias for super
    "meta": {125, 126},     # alias for super
}

# Common key names -> evdev keycode (KEY_* constants)
_KEY_CODES: dict[str, int] = {
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33,
    "g": 34, "h": 35, "i": 23, "j": 36, "k": 37, "l": 38,
    "m": 50, "n": 49, "o": 24, "p": 25, "q": 16, "r": 19,
    "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
    "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "escape": 1, "esc": 1,
    "enter": 28, "return": 28,
    "tab": 15,
    "space": 57,
    "backspace": 14,
    "delete": 111,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63,
    "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68,
    "f11": 87, "f12": 88,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
    "insert": 110,
}


def _parse_combo(combo: str) -> tuple[frozenset[str], int]:
    """Parse a hotkey string like 'ctrl+alt+r' into modifier names + key code.

    Returns:
        (modifier_names, main_keycode) where modifier_names is a frozenset
        of canonical modifier names (e.g. {"ctrl", "alt"}) and main_keycode
        is the evdev keycode of the non-modifier key.

    Raises:
        ValueError: If the combo is empty, has no main key, or contains
            unknown key names.
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    if not parts or not all(parts):
        raise ValueError(f"Invalid hotkey format: '{combo}'")

    modifiers: set[str] = set()
    main_key: Optional[int] = None

    for part in parts:
        # Normalise modifier aliases to canonical names
        canonical = part
        if part in ("cmd", "win", "meta", "super"):
            canonical = "super"

        if canonical in _MODIFIER_CODES:
            modifiers.add(canonical)
        elif part in _KEY_CODES:
            if main_key is not None:
                raise ValueError(
                    f"Multiple non-modifier keys in combo '{combo}': "
                    f"already have keycode {main_key}, got '{part}'"
                )
            main_key = _KEY_CODES[part]
        else:
            raise ValueError(f"Unknown key '{part}' in combo '{combo}'")

    if main_key is None:
        raise ValueError(f"No main (non-modifier) key in combo '{combo}'")

    return frozenset(modifiers), main_key


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------

def check_evdev_permissions() -> tuple[bool, str]:
    """Check whether the current user can access /dev/input/* devices.

    Returns:
        (ok, message): ok is True if access is likely to work.
        message contains guidance if ok is False.
    """
    # Root always has access
    if os.geteuid() == 0:
        return True, "Running as root — evdev access granted."

    # Check if user is in the 'input' group
    username = os.environ.get("USER", "")
    try:
        input_group = grp.getgrnam("input")
        in_group = username in input_group.gr_mem or os.getgid() == input_group.gr_gid
        # Also check supplementary groups of current process
        if not in_group:
            in_group = input_group.gr_gid in os.getgroups()
    except KeyError:
        # 'input' group doesn't exist
        return False, (
            "The 'input' group does not exist on this system.\n"
            "VoicePaste needs access to /dev/input/* for global hotkeys on Wayland.\n"
            "Create the group and add yourself:\n"
            "  sudo groupadd input\n"
            f"  sudo usermod -aG input {username}\n"
            "Then log out and back in."
        )

    if not in_group:
        return False, (
            "VoicePaste needs access to /dev/input/* for global hotkeys on Wayland.\n\n"
            "Your user is not in the 'input' group. Run:\n"
            f"  sudo usermod -aG input {username}\n\n"
            "Then log out and back in for the change to take effect."
        )

    # User is in group — quick probe of actual device access
    try:
        devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
        keyboards = [d for d in devices if _is_keyboard(d)]
        if not keyboards:
            return False, (
                "No keyboard devices found in /dev/input/.\n"
                "Check that your keyboard is connected and that /dev/input/ "
                "device permissions allow the 'input' group."
            )
        return True, f"evdev access OK ({len(keyboards)} keyboard(s) found)."
    except PermissionError:
        return False, (
            "Permission denied reading /dev/input/* devices.\n\n"
            "Your user is in the 'input' group, but the session may not have "
            "picked up the group membership yet.\n"
            "Try logging out and back in, or run:\n"
            f"  newgrp input"
        )
    except Exception as e:
        return False, f"Error probing /dev/input/* devices: {e}"


def _is_keyboard(device) -> bool:
    """Check if an evdev device is a keyboard (has KEY_A capability)."""
    caps = device.capabilities(verbose=False)
    # ecodes.EV_KEY = 1
    key_caps = caps.get(1, [])
    # A keyboard should at least have KEY_A (30)
    return 30 in key_caps


# ---------------------------------------------------------------------------
# EvdevKeyboardMonitor — singleton daemon thread
# ---------------------------------------------------------------------------

class EvdevKeyboardMonitor:
    """Monitors all keyboard input devices via evdev for hotkey combos.

    Runs a daemon thread that uses select() to efficiently wait for events
    from all keyboard devices simultaneously. Re-scans for new devices
    (USB hotplug) every 5 seconds.
    """

    _RESCAN_INTERVAL = 5.0  # seconds between device rescans

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._combos: dict[int, tuple[frozenset[str], int, Callable[[], None]]] = {}
        self._next_handle = 0
        self._held_keys: set[int] = set()
        self._devices: dict[str, "evdev.InputDevice"] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Pipe for waking up select() when stopping
        self._wake_r, self._wake_w = os.pipe()

    def start(self) -> None:
        """Start the monitor daemon thread."""
        if self._running:
            return
        self._running = True
        self._scan_devices()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="evdev-hotkey",
        )
        self._thread.start()
        logger.info(
            "EvdevKeyboardMonitor started (%d keyboard(s)).",
            len(self._devices),
        )

    def stop(self) -> None:
        """Stop the monitor thread and close all devices."""
        if not self._running:
            return
        self._running = False
        # Wake up the select() call
        try:
            os.write(self._wake_w, b"\x00")
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._close_devices()
        # Close the wake pipe
        for fd in (self._wake_r, self._wake_w):
            try:
                os.close(fd)
            except OSError:
                pass
        logger.info("EvdevKeyboardMonitor stopped.")

    def add_hotkey(
        self,
        combo: str,
        callback: Callable[[], None],
    ) -> int:
        """Register a hotkey combo. Returns an integer handle."""
        modifiers, keycode = _parse_combo(combo)
        with self._lock:
            handle = self._next_handle
            self._next_handle += 1
            self._combos[handle] = (modifiers, keycode, callback)
        logger.debug(
            "evdev hotkey added: handle=%d, combo='%s' (mods=%s, key=%d)",
            handle, combo, modifiers, keycode,
        )
        return handle

    def add_key_listener(
        self,
        key_name: str,
        callback: Callable[[], None],
    ) -> int:
        """Register a single-key listener (e.g. Escape, Enter).

        Returns an integer handle.
        """
        key_lower = key_name.lower()
        if key_lower not in _KEY_CODES:
            raise ValueError(f"Unknown key name: '{key_name}'")
        keycode = _KEY_CODES[key_lower]
        with self._lock:
            handle = self._next_handle
            self._next_handle += 1
            # Empty modifier set = no modifiers required
            self._combos[handle] = (frozenset(), keycode, callback)
        logger.debug(
            "evdev key listener added: handle=%d, key='%s' (code=%d)",
            handle, key_name, keycode,
        )
        return handle

    def remove_hotkey(self, handle: int) -> None:
        """Remove a previously registered hotkey by handle."""
        with self._lock:
            removed = self._combos.pop(handle, None)
        if removed:
            logger.debug("evdev hotkey removed: handle=%d", handle)

    # -- Internal methods --

    def _scan_devices(self) -> None:
        """Scan /dev/input/ for keyboard devices."""
        try:
            all_paths = set(evdev.list_devices())
        except Exception as e:
            logger.warning("evdev device scan failed: %s", e)
            return

        current_paths = set(self._devices.keys())

        # Add new devices
        for path in all_paths - current_paths:
            try:
                dev = evdev.InputDevice(path)
                if _is_keyboard(dev):
                    self._devices[path] = dev
                    logger.debug("evdev keyboard found: %s (%s)", dev.name, path)
                else:
                    dev.close()
            except (PermissionError, OSError) as e:
                logger.debug("Cannot open %s: %s", path, e)

    def _close_devices(self) -> None:
        """Close all open device file descriptors."""
        for dev in self._devices.values():
            try:
                dev.close()
            except Exception:
                pass
        self._devices.clear()

    def _run_loop(self) -> None:
        """Main event loop — select() on all keyboard devices + wake pipe."""
        last_scan = 0.0

        while self._running:
            # Periodic device rescan (hotplug support)
            import time
            now = time.monotonic()
            if now - last_scan > self._RESCAN_INTERVAL:
                self._scan_devices()
                last_scan = now

            if not self._devices:
                # No keyboards found — sleep and retry
                import time
                time.sleep(1.0)
                continue

            # Build fd -> device map
            fd_map: dict[int, "evdev.InputDevice"] = {}
            for dev in list(self._devices.values()):
                try:
                    fd_map[dev.fd] = dev
                except Exception:
                    pass

            # select() with timeout for periodic rescan
            try:
                readable, _, _ = select.select(
                    list(fd_map.keys()) + [self._wake_r],
                    [], [],
                    self._RESCAN_INTERVAL,
                )
            except (ValueError, OSError):
                # Bad file descriptor — a device was disconnected
                self._remove_stale_devices()
                continue

            for fd in readable:
                if fd == self._wake_r:
                    # Drain the wake pipe
                    try:
                        os.read(self._wake_r, 1024)
                    except OSError:
                        pass
                    continue

                dev = fd_map.get(fd)
                if dev is None:
                    continue

                try:
                    for event in dev.read():
                        # EV_KEY = 1
                        if event.type != 1:
                            continue
                        # value: 0=up, 1=down, 2=repeat
                        if event.value == 1:
                            self._held_keys.add(event.code)
                            self._check_combos(event.code)
                        elif event.value == 0:
                            self._held_keys.discard(event.code)
                except OSError:
                    # Device disconnected
                    logger.debug(
                        "evdev device disconnected: %s", dev.path,
                    )
                    try:
                        dev.close()
                    except Exception:
                        pass
                    self._devices.pop(dev.path, None)

    def _remove_stale_devices(self) -> None:
        """Remove devices with invalid file descriptors."""
        stale = []
        for path, dev in self._devices.items():
            try:
                # Quick check: can we stat the fd?
                os.fstat(dev.fd)
            except (OSError, ValueError):
                stale.append(path)
        for path in stale:
            dev = self._devices.pop(path, None)
            if dev:
                try:
                    dev.close()
                except Exception:
                    pass
            logger.debug("Removed stale evdev device: %s", path)

    def _check_combos(self, pressed_code: int) -> None:
        """Check if any registered combo matches the current key state."""
        with self._lock:
            combos = list(self._combos.values())

        for modifiers, keycode, callback in combos:
            if pressed_code != keycode:
                continue
            # Check all required modifiers are held
            if not self._modifiers_match(modifiers):
                continue
            # Match! Fire callback in a separate thread to avoid blocking
            threading.Thread(
                target=self._fire_callback,
                args=(callback,),
                daemon=True,
                name="evdev-cb",
            ).start()

    def _modifiers_match(self, required: frozenset[str]) -> bool:
        """Check whether all required modifiers are currently held."""
        for mod_name in required:
            codes = _MODIFIER_CODES.get(mod_name, set())
            if not (self._held_keys & codes):
                return False
        return True

    @staticmethod
    def _fire_callback(callback: Callable[[], None]) -> None:
        """Invoke a hotkey callback, catching exceptions."""
        try:
            callback()
        except Exception:
            logger.exception("Error in evdev hotkey callback.")


# ---------------------------------------------------------------------------
# Singleton monitor instance
# ---------------------------------------------------------------------------

_monitor: Optional[EvdevKeyboardMonitor] = None
_monitor_lock = threading.Lock()


def _get_monitor() -> EvdevKeyboardMonitor:
    """Get or create the singleton EvdevKeyboardMonitor."""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = EvdevKeyboardMonitor()
            _monitor.start()
        return _monitor


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evdev_add_hotkey(combo: str, callback: Callable[[], None]) -> int:
    """Register a global hotkey via evdev. Returns an integer handle."""
    return _get_monitor().add_hotkey(combo, callback)


def evdev_add_key_listener(key_name: str, callback: Callable[[], None]) -> int:
    """Register a single-key listener via evdev. Returns an integer handle."""
    return _get_monitor().add_key_listener(key_name, callback)


def evdev_remove_hotkey(handle: int) -> None:
    """Remove a previously registered evdev hotkey by handle."""
    global _monitor
    with _monitor_lock:
        if _monitor is not None:
            _monitor.remove_hotkey(handle)


def stop_monitor() -> None:
    """Stop the evdev monitor thread. Safe to call if not started."""
    global _monitor
    with _monitor_lock:
        if _monitor is not None:
            _monitor.stop()
            _monitor = None
