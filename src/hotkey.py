"""Global hotkey registration for the Voice-to-Summary Paste Tool.

Uses the `keyboard` library on Windows and `pynput` on Linux for
global hotkey hooks.  On Wayland sessions, `evdev` is used instead
of `pynput` because Wayland isolates input per-client.
REQ-S15: Only hooks the specific hotkey combination, not blanket monitoring.

v0.1: Ctrl+Win toggle hotkey.
v0.2+: Escape cancel hotkey (active only during recording).
v1.1: Linux support via pynput (X11/XWayland).
v1.2: _HotkeySlot dataclass to eliminate 5x copy-paste pattern.
v1.3: evdev backend for Wayland sessions.
"""

import logging
import os
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

if sys.platform == "win32":
    import keyboard as kb
else:
    kb = None  # pynput used on Linux; imported lazily

from constants import (
    CANCEL_HOTKEY,
    DEFAULT_CLAUDE_CODE_HOTKEY,
    DEFAULT_HOTKEY,
    DEFAULT_PROMPT_HOTKEY,
    DEFAULT_TTS_ASK_HOTKEY,
    DEFAULT_TTS_HOTKEY,
    HOTKEY_DEBOUNCE_MS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wayland detection
# ---------------------------------------------------------------------------

def _is_wayland() -> bool:
    """Return True if the current session is Wayland (not X11/XWayland)."""
    return (
        sys.platform == "linux"
        and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )


# ---------------------------------------------------------------------------
# Linux evdev helpers (Wayland)
# ---------------------------------------------------------------------------

def _evdev_add_hotkey(combo: str, callback: Callable[[], None]):
    """Register a global hotkey using evdev. Returns an integer handle."""
    from evdev_hotkey import evdev_add_hotkey
    return evdev_add_hotkey(combo, callback)


def _evdev_add_key_listener(key_name: str, callback: Callable[[], None]):
    """Register a single-key listener using evdev. Returns an integer handle."""
    from evdev_hotkey import evdev_add_key_listener
    return evdev_add_key_listener(key_name, callback)


def _evdev_remove_hotkey(handle) -> None:
    """Remove an evdev hotkey by handle."""
    from evdev_hotkey import evdev_remove_hotkey
    evdev_remove_hotkey(handle)


# ---------------------------------------------------------------------------
# Linux pynput helpers (X11)
# ---------------------------------------------------------------------------

def _hotkey_to_pynput(combo: str) -> str:
    """Convert keyboard-library hotkey string to pynput GlobalHotKeys format.

    'ctrl+alt+r' -> '<ctrl>+<alt>+r'
    """
    parts = combo.lower().split("+")
    converted = []
    for part in parts:
        p = part.strip()
        if p in ("ctrl", "alt", "shift", "cmd", "super"):
            converted.append(f"<{p}>")
        else:
            converted.append(p)
    return "+".join(converted)


def _pynput_add_hotkey(combo: str, callback: Callable[[], None]):
    """Register a global hotkey using pynput. Returns the listener."""
    from pynput import keyboard as pynput_kb

    pynput_combo = _hotkey_to_pynput(combo)
    hotkeys = pynput_kb.GlobalHotKeys({pynput_combo: callback})
    hotkeys.daemon = True
    hotkeys.start()
    return hotkeys


def _pynput_add_key_listener(key_name: str, callback: Callable[[], None]):
    """Register a single-key listener using pynput (for Escape, Enter, etc.)."""
    from pynput import keyboard as pynput_kb

    key_map = {
        "escape": pynput_kb.Key.esc,
        "esc": pynput_kb.Key.esc,
        "enter": pynput_kb.Key.enter,
        "tab": pynput_kb.Key.tab,
        "space": pynput_kb.Key.space,
    }
    target = key_map.get(key_name.lower())
    if target is None:
        target = pynput_kb.KeyCode.from_char(key_name.lower())

    def on_press(pressed_key):
        if pressed_key == target:
            callback()

    listener = pynput_kb.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    return listener


def _pynput_remove_hotkey(handle) -> None:
    """Stop a pynput GlobalHotKeys or Listener."""
    if handle is not None:
        try:
            handle.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Platform-dispatched helpers
# ---------------------------------------------------------------------------

def _add_hotkey(combo: str, callback: Callable[[], None], suppress: bool = False):
    """Register a global hotkey. Returns a handle for later removal."""
    if sys.platform == "win32":
        return kb.add_hotkey(combo, callback, suppress=suppress)
    elif _is_wayland():
        return _evdev_add_hotkey(combo, callback)
    else:
        return _pynput_add_hotkey(combo, callback)


def _add_single_key(key_name: str, callback: Callable[[], None], suppress: bool = False):
    """Register a single-key hotkey (e.g. Escape). Returns a handle."""
    if sys.platform == "win32":
        return kb.add_hotkey(key_name, callback, suppress=suppress)
    elif _is_wayland():
        return _evdev_add_key_listener(key_name, callback)
    else:
        return _pynput_add_key_listener(key_name, callback)


def _remove_hotkey(handle) -> None:
    """Remove a previously registered hotkey."""
    if handle is None:
        return
    if sys.platform == "win32":
        kb.remove_hotkey(handle)
    elif _is_wayland():
        _evdev_remove_hotkey(handle)
    else:
        _pynput_remove_hotkey(handle)


def _parse_hotkey(combo: str):
    """Parse/validate a hotkey string. Raises on invalid format."""
    if sys.platform == "win32":
        return kb.parse_hotkey(combo)
    elif _is_wayland():
        from evdev_hotkey import _parse_combo
        return _parse_combo(combo)
    else:
        # Basic validation: ensure non-empty and well-formed
        parts = combo.lower().split("+")
        if not parts or not all(p.strip() for p in parts):
            raise ValueError(f"Invalid hotkey format: '{combo}'")
        return parts


# ---------------------------------------------------------------------------
# _HotkeySlot: eliminates per-slot copy-paste
# ---------------------------------------------------------------------------

@dataclass
class _HotkeySlot:
    """State for a single registered hotkey.

    Attributes:
        label: Human-readable label for logging (e.g. "main", "TTS Ask").
        combo: The hotkey combination string.
        callback: Function to invoke on accepted press.
        registered: Whether the hotkey is currently registered.
        handle: Platform handle returned by _add_hotkey / _add_single_key.
        last_trigger: Monotonic timestamp of last accepted press.
        is_single_key: True for single-key slots (e.g. Escape/Enter).
        validate: Whether to validate/parse the combo before registering.
    """
    label: str
    combo: str
    callback: Optional[Callable[[], None]] = None
    registered: bool = False
    handle: Optional[object] = None
    last_trigger: float = 0.0
    is_single_key: bool = False
    validate: bool = True


class HotkeyManager:
    """Manages global hotkey registration and debouncing.

    REQ-S15: Only registers the specific configured hotkeys.
    Does not perform blanket keyboard monitoring.

    On Windows uses the `keyboard` library. On Linux/X11 uses `pynput`.
    On Linux/Wayland uses `evdev` (reads /dev/input/* directly).

    Attributes:
        hotkey: The hotkey combination string.
        debounce_ms: Minimum time between accepted hotkey presses.
    """

    def __init__(
        self,
        hotkey: str = DEFAULT_HOTKEY,
        prompt_hotkey: str = DEFAULT_PROMPT_HOTKEY,
        tts_hotkey: str = DEFAULT_TTS_HOTKEY,
        tts_ask_hotkey: str = DEFAULT_TTS_ASK_HOTKEY,
        claude_code_hotkey: str = DEFAULT_CLAUDE_CODE_HOTKEY,
        debounce_ms: int = HOTKEY_DEBOUNCE_MS,
    ) -> None:
        """Initialize the hotkey manager.

        Args:
            hotkey: Hotkey combination string (default from constants.DEFAULT_HOTKEY).
            prompt_hotkey: Voice Prompt hotkey combination string.
            tts_hotkey: TTS clipboard readout hotkey (v0.6).
            tts_ask_hotkey: TTS Ask AI + readout hotkey (v0.6).
            claude_code_hotkey: Claude Code voice input hotkey (v1.2).
            debounce_ms: Debounce window in milliseconds.
        """
        self.hotkey = hotkey
        self.prompt_hotkey = prompt_hotkey
        self.tts_hotkey = tts_hotkey
        self.tts_ask_hotkey = tts_ask_hotkey
        self.claude_code_hotkey = claude_code_hotkey
        self.debounce_ms = debounce_ms
        self._lock = threading.Lock()

        # All hotkey slots, keyed by name
        self._slots: dict[str, _HotkeySlot] = {
            "main": _HotkeySlot(label="main", combo=hotkey, validate=True),
            "prompt": _HotkeySlot(label="Prompt", combo=prompt_hotkey, validate=True),
            "tts": _HotkeySlot(label="TTS", combo=tts_hotkey),
            "tts_ask": _HotkeySlot(label="TTS Ask", combo=tts_ask_hotkey),
            "claude_code": _HotkeySlot(
                label="Claude Code", combo=claude_code_hotkey, validate=True,
            ),
            "cancel": _HotkeySlot(
                label="Cancel", combo=CANCEL_HOTKEY,
                is_single_key=True, validate=False,
            ),
        }

    # -- Backward-compatible attribute access used by main.py and tests --

    @property
    def _tts_registered(self) -> bool:
        return self._slots["tts"].registered

    @_tts_registered.setter
    def _tts_registered(self, value: bool) -> None:
        self._slots["tts"].registered = value

    @property
    def _tts_ask_registered(self) -> bool:
        return self._slots["tts_ask"].registered

    @_tts_ask_registered.setter
    def _tts_ask_registered(self, value: bool) -> None:
        self._slots["tts_ask"].registered = value

    # -- Generic slot operations --

    def _register_slot(
        self,
        name: str,
        callback: Callable[[], None],
        *,
        raise_on_error: bool = False,
    ) -> None:
        """Register a hotkey slot.

        Args:
            name: Slot name (key into self._slots).
            callback: Function to call when the hotkey fires.
            raise_on_error: If True, propagate registration exceptions.
        """
        slot = self._slots[name]
        slot.callback = callback

        if slot.validate:
            try:
                parsed = _parse_hotkey(slot.combo)
                logger.debug("Parsed %s hotkey: %s", slot.label, parsed)
            except Exception as parse_err:
                logger.error(
                    "parse_hotkey('%s') failed for %s: %s",
                    slot.combo, slot.label, parse_err,
                )
                raise

        logger.info(
            "Attempting to register %s hotkey: '%s'",
            slot.label, slot.combo,
        )

        try:
            if slot.is_single_key:
                slot.handle = _add_single_key(
                    slot.combo, lambda: self._on_slot_fired(name), suppress=False,
                )
            else:
                slot.handle = _add_hotkey(
                    slot.combo, lambda: self._on_slot_fired(name), suppress=False,
                )
            slot.registered = True
            logger.info(
                "%s hotkey registered: '%s'",
                slot.label, slot.combo,
            )
        except Exception as e:
            if raise_on_error:
                logger.error(
                    "Failed to register %s hotkey '%s': %s (%s)",
                    slot.label, slot.combo, e, type(e).__name__,
                )
                raise
            else:
                logger.warning(
                    "Failed to register %s hotkey '%s': %s",
                    slot.label, slot.combo, e,
                )

    def _unregister_slot(self, name: str) -> None:
        """Unregister a single hotkey slot. Safe to call if not registered."""
        slot = self._slots[name]
        if not slot.registered or slot.handle is None:
            return
        try:
            _remove_hotkey(slot.handle)
            logger.info("%s hotkey unregistered: %s", slot.label, slot.combo)
        except Exception as e:
            logger.warning(
                "Failed to unregister %s hotkey: %s", slot.label, e,
            )
        finally:
            slot.registered = False
            slot.handle = None

    def _on_slot_fired(self, name: str) -> None:
        """Generic hotkey handler with debounce logic.

        The cancel slot skips debounce (it must respond instantly).
        """
        slot = self._slots[name]

        # Cancel slot: no debounce
        if name == "cancel":
            logger.info("Cancel hotkey pressed: %s", CANCEL_HOTKEY)
            if slot.callback:
                try:
                    slot.callback()
                except Exception:
                    logger.exception("Error in cancel hotkey callback.")
            return

        # All other slots: debounce
        with self._lock:
            now = time.monotonic()
            elapsed_ms = (now - slot.last_trigger) * 1000

            if elapsed_ms < self.debounce_ms:
                logger.debug(
                    "%s hotkey debounced (%.0fms < %dms).",
                    slot.label, elapsed_ms, self.debounce_ms,
                )
                return

            slot.last_trigger = now

        logger.info(
            "%s hotkey accepted: '%s' (%.0fms since last trigger)",
            slot.label, slot.combo, elapsed_ms,
        )

        if slot.callback:
            try:
                slot.callback()
            except Exception:
                logger.exception("Error in %s hotkey callback.", slot.label)
        else:
            logger.warning("%s hotkey fired but no callback registered.", slot.label)

    # -- Public API (preserves existing interface) --

    def register(self, callback: Callable[[], None]) -> None:
        """Register the global hotkey with a callback.

        Args:
            callback: Function to call when the hotkey is pressed.
        """
        self._register_slot("main", callback, raise_on_error=True)

    def register_prompt(self, callback: Callable[[], None]) -> None:
        """Register the Voice Prompt hotkey with a callback.

        Args:
            callback: Function to call when the prompt hotkey is pressed.
        """
        self._register_slot("prompt", callback, raise_on_error=True)

    def register_tts(self, callback: Callable[[], None]) -> None:
        """Register the TTS clipboard readout hotkey (v0.6).

        Args:
            callback: Function to call when the TTS hotkey is pressed.
        """
        self._register_slot("tts", callback)

    def register_tts_ask(self, callback: Callable[[], None]) -> None:
        """Register the TTS Ask AI + readout hotkey (v0.6).

        Args:
            callback: Function to call when the TTS Ask hotkey is pressed.
        """
        self._register_slot("tts_ask", callback)

    def register_cancel(self, callback: Callable[[], None]) -> None:
        """Register the Escape key as a cancel hotkey.

        The cancel hotkey should only be registered during the RECORDING
        state and unregistered when leaving that state.

        REQ-S15: Only hooks the specific Escape key.

        Args:
            callback: Function to call when Escape is pressed.
        """
        if self._slots["cancel"].registered:
            logger.debug("Cancel hotkey already registered.")
            return
        self._register_slot("cancel", callback)

    def unregister_cancel(self) -> None:
        """Unregister the cancel (Escape) hotkey.

        Safe to call even if cancel is not currently registered.
        """
        self._unregister_slot("cancel")

    def register_claude_code(self, callback: Callable[[], None]) -> None:
        """Register the Claude Code hotkey (v1.2).

        Args:
            callback: Function to call when the Claude Code hotkey is pressed.
        """
        self._register_slot("claude_code", callback)

    def unregister_claude_code(self) -> None:
        """Unregister the Claude Code hotkey. Safe to call if not registered."""
        self._unregister_slot("claude_code")

    def unregister_tts(self) -> None:
        """Unregister TTS hotkeys. Safe to call even if not registered."""
        self._unregister_slot("tts")
        self._unregister_slot("tts_ask")

    def unregister(self) -> None:
        """Unregister all hotkeys (main + prompt + cancel + TTS + Claude Code)."""
        self.unregister_cancel()
        self.unregister_tts()
        self.unregister_claude_code()
        self._unregister_slot("prompt")
        self._unregister_slot("main")
