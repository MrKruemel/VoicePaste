"""Global hotkey registration for the Voice-to-Summary Paste Tool.

Uses the `keyboard` library for global hotkey hooks.
REQ-S15: Only hooks the specific hotkey combination, not blanket monitoring.

v0.1: Ctrl+Win toggle hotkey.
v0.2+: Escape cancel hotkey (active only during recording).
"""

import logging
import time
import threading
from typing import Callable, Optional

import keyboard as kb

from constants import (
    CANCEL_HOTKEY,
    DEFAULT_HOTKEY,
    DEFAULT_PROMPT_HOTKEY,
    DEFAULT_TTS_ASK_HOTKEY,
    DEFAULT_TTS_HOTKEY,
    HOTKEY_DEBOUNCE_MS,
)

logger = logging.getLogger(__name__)


class HotkeyManager:
    """Manages global hotkey registration and debouncing.

    REQ-S15: Only registers the specific Ctrl+Win and Escape hotkeys.
    Does not perform blanket keyboard monitoring.

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
        debounce_ms: int = HOTKEY_DEBOUNCE_MS,
    ) -> None:
        """Initialize the hotkey manager.

        Args:
            hotkey: Hotkey combination string (default from constants.DEFAULT_HOTKEY).
            prompt_hotkey: Voice Prompt hotkey combination string.
            tts_hotkey: TTS clipboard readout hotkey (v0.6).
            tts_ask_hotkey: TTS Ask AI + readout hotkey (v0.6).
            debounce_ms: Debounce window in milliseconds.
        """
        self.hotkey = hotkey
        self.prompt_hotkey = prompt_hotkey
        self.tts_hotkey = tts_hotkey
        self.tts_ask_hotkey = tts_ask_hotkey
        self.debounce_ms = debounce_ms
        self._callback: Optional[Callable[[], None]] = None
        self._cancel_callback: Optional[Callable[[], None]] = None
        self._prompt_callback: Optional[Callable[[], None]] = None
        self._tts_callback: Optional[Callable[[], None]] = None
        self._tts_ask_callback: Optional[Callable[[], None]] = None
        self._last_trigger_time: float = 0.0
        self._last_prompt_trigger_time: float = 0.0
        self._last_tts_trigger_time: float = 0.0
        self._last_tts_ask_trigger_time: float = 0.0
        self._registered = False
        self._cancel_registered = False
        self._prompt_registered = False
        self._tts_registered = False
        self._tts_ask_registered = False
        self._hotkey_handle: Optional[object] = None
        self._cancel_handle: Optional[object] = None
        self._prompt_handle: Optional[object] = None
        self._tts_handle: Optional[object] = None
        self._tts_ask_handle: Optional[object] = None
        self._lock = threading.Lock()

    def register(self, callback: Callable[[], None]) -> None:
        """Register the global hotkey with a callback.

        Args:
            callback: Function to call when the hotkey is pressed.
        """
        self._callback = callback

        logger.info(
            "Attempting to register hotkey: '%s' (debounce=%dms)",
            self.hotkey,
            self.debounce_ms,
        )

        # Log the parsed hotkey for diagnostics
        try:
            parsed = kb.parse_hotkey(self.hotkey)
            logger.debug("Parsed hotkey scan codes: %s", parsed)
        except Exception as parse_err:
            logger.error(
                "keyboard.parse_hotkey('%s') failed: %s", self.hotkey, parse_err
            )
            raise

        try:
            self._hotkey_handle = kb.add_hotkey(
                self.hotkey, self._on_hotkey, suppress=False
            )
            self._registered = True
            logger.info(
                "Global hotkey registered successfully: '%s' (handle=%s)",
                self.hotkey,
                type(self._hotkey_handle).__name__,
            )
        except Exception as e:
            logger.error(
                "Failed to register hotkey '%s': %s (%s)",
                self.hotkey,
                e,
                type(e).__name__,
            )
            raise

    def _on_hotkey(self) -> None:
        """Internal hotkey handler with debounce logic.

        Ignores hotkey presses that occur within the debounce window.
        """
        logger.debug("Hotkey event received for '%s'.", self.hotkey)

        with self._lock:
            now = time.monotonic()
            elapsed_ms = (now - self._last_trigger_time) * 1000

            if elapsed_ms < self.debounce_ms:
                logger.debug(
                    "Hotkey debounced (%.0fms < %dms).",
                    elapsed_ms,
                    self.debounce_ms,
                )
                return

            self._last_trigger_time = now

        logger.info(
            "Hotkey accepted: '%s' (%.0fms since last trigger)",
            self.hotkey,
            elapsed_ms,
        )

        if self._callback:
            try:
                self._callback()
            except Exception:
                logger.exception("Error in hotkey callback.")
        else:
            logger.warning("Hotkey fired but no callback registered.")

    def register_prompt(self, callback: Callable[[], None]) -> None:
        """Register the Voice Prompt hotkey with a callback.

        Args:
            callback: Function to call when the prompt hotkey is pressed.
        """
        self._prompt_callback = callback

        logger.info(
            "Attempting to register prompt hotkey: '%s' (debounce=%dms)",
            self.prompt_hotkey,
            self.debounce_ms,
        )

        try:
            parsed = kb.parse_hotkey(self.prompt_hotkey)
            logger.debug("Parsed prompt hotkey scan codes: %s", parsed)
        except Exception as parse_err:
            logger.error(
                "keyboard.parse_hotkey('%s') failed: %s", self.prompt_hotkey, parse_err
            )
            raise

        try:
            self._prompt_handle = kb.add_hotkey(
                self.prompt_hotkey, self._on_prompt_hotkey, suppress=False
            )
            self._prompt_registered = True
            logger.info(
                "Prompt hotkey registered successfully: '%s'",
                self.prompt_hotkey,
            )
        except Exception as e:
            logger.error(
                "Failed to register prompt hotkey '%s': %s (%s)",
                self.prompt_hotkey,
                e,
                type(e).__name__,
            )
            raise

    def _on_prompt_hotkey(self) -> None:
        """Internal prompt hotkey handler with debounce logic."""
        logger.debug("Prompt hotkey event received for '%s'.", self.prompt_hotkey)

        with self._lock:
            now = time.monotonic()
            elapsed_ms = (now - self._last_prompt_trigger_time) * 1000

            if elapsed_ms < self.debounce_ms:
                logger.debug(
                    "Prompt hotkey debounced (%.0fms < %dms).",
                    elapsed_ms,
                    self.debounce_ms,
                )
                return

            self._last_prompt_trigger_time = now

        logger.info(
            "Prompt hotkey accepted: '%s' (%.0fms since last trigger)",
            self.prompt_hotkey,
            elapsed_ms,
        )

        if self._prompt_callback:
            try:
                self._prompt_callback()
            except Exception:
                logger.exception("Error in prompt hotkey callback.")
        else:
            logger.warning("Prompt hotkey fired but no callback registered.")

    def register_tts(self, callback: Callable[[], None]) -> None:
        """Register the TTS clipboard readout hotkey (v0.6).

        Args:
            callback: Function to call when the TTS hotkey is pressed.
        """
        self._tts_callback = callback

        logger.info("Attempting to register TTS hotkey: '%s'", self.tts_hotkey)

        try:
            self._tts_handle = kb.add_hotkey(
                self.tts_hotkey, self._on_tts_hotkey, suppress=False
            )
            self._tts_registered = True
            logger.info("TTS hotkey registered: '%s'", self.tts_hotkey)
        except Exception as e:
            logger.warning(
                "Failed to register TTS hotkey '%s': %s", self.tts_hotkey, e
            )

    def _on_tts_hotkey(self) -> None:
        """Internal TTS hotkey handler with debounce."""
        with self._lock:
            now = time.monotonic()
            elapsed_ms = (now - self._last_tts_trigger_time) * 1000
            if elapsed_ms < self.debounce_ms:
                return
            self._last_tts_trigger_time = now

        if self._tts_callback:
            try:
                self._tts_callback()
            except Exception:
                logger.exception("Error in TTS hotkey callback.")

    def register_tts_ask(self, callback: Callable[[], None]) -> None:
        """Register the TTS Ask AI + readout hotkey (v0.6).

        Args:
            callback: Function to call when the TTS Ask hotkey is pressed.
        """
        self._tts_ask_callback = callback

        logger.info("Attempting to register TTS Ask hotkey: '%s'", self.tts_ask_hotkey)

        try:
            self._tts_ask_handle = kb.add_hotkey(
                self.tts_ask_hotkey, self._on_tts_ask_hotkey, suppress=False
            )
            self._tts_ask_registered = True
            logger.info("TTS Ask hotkey registered: '%s'", self.tts_ask_hotkey)
        except Exception as e:
            logger.warning(
                "Failed to register TTS Ask hotkey '%s': %s", self.tts_ask_hotkey, e
            )

    def _on_tts_ask_hotkey(self) -> None:
        """Internal TTS Ask hotkey handler with debounce."""
        with self._lock:
            now = time.monotonic()
            elapsed_ms = (now - self._last_tts_ask_trigger_time) * 1000
            if elapsed_ms < self.debounce_ms:
                return
            self._last_tts_ask_trigger_time = now

        if self._tts_ask_callback:
            try:
                self._tts_ask_callback()
            except Exception:
                logger.exception("Error in TTS Ask hotkey callback.")

    def unregister_tts(self) -> None:
        """Unregister TTS hotkeys. Safe to call even if not registered."""
        for attr_reg, attr_handle, label in [
            ("_tts_registered", "_tts_handle", "TTS"),
            ("_tts_ask_registered", "_tts_ask_handle", "TTS Ask"),
        ]:
            if getattr(self, attr_reg) and getattr(self, attr_handle) is not None:
                try:
                    kb.remove_hotkey(getattr(self, attr_handle))
                    logger.info("%s hotkey unregistered.", label)
                except Exception as e:
                    logger.warning("Failed to unregister %s hotkey: %s", label, e)
                finally:
                    setattr(self, attr_reg, False)
                    setattr(self, attr_handle, None)

    def register_cancel(self, callback: Callable[[], None]) -> None:
        """Register the Escape key as a cancel hotkey.

        The cancel hotkey should only be registered during the RECORDING
        state and unregistered when leaving that state.

        REQ-S15: Only hooks the specific Escape key.

        Args:
            callback: Function to call when Escape is pressed.
        """
        if self._cancel_registered:
            logger.debug("Cancel hotkey already registered.")
            return

        self._cancel_callback = callback
        try:
            self._cancel_handle = kb.add_hotkey(
                CANCEL_HOTKEY, self._on_cancel, suppress=False
            )
            self._cancel_registered = True
            logger.info("Cancel hotkey registered: %s", CANCEL_HOTKEY)
        except Exception as e:
            logger.warning("Failed to register cancel hotkey '%s': %s", CANCEL_HOTKEY, e)

    def _on_cancel(self) -> None:
        """Internal cancel hotkey handler."""
        logger.info("Cancel hotkey pressed: %s", CANCEL_HOTKEY)

        if self._cancel_callback:
            try:
                self._cancel_callback()
            except Exception:
                logger.exception("Error in cancel hotkey callback.")

    def unregister_cancel(self) -> None:
        """Unregister the cancel (Escape) hotkey.

        Safe to call even if cancel is not currently registered.
        """
        if self._cancel_registered and self._cancel_handle is not None:
            try:
                kb.remove_hotkey(self._cancel_handle)
                self._cancel_registered = False
                self._cancel_handle = None
                logger.info("Cancel hotkey unregistered: %s", CANCEL_HOTKEY)
            except Exception as e:
                logger.warning("Failed to unregister cancel hotkey: %s", e)
                self._cancel_registered = False
                self._cancel_handle = None

    def unregister(self) -> None:
        """Unregister all hotkeys (main + prompt + cancel + TTS)."""
        # Unregister cancel first
        self.unregister_cancel()

        # Unregister TTS hotkeys (v0.6)
        self.unregister_tts()

        # Unregister prompt hotkey
        if self._prompt_registered and self._prompt_handle is not None:
            try:
                kb.remove_hotkey(self._prompt_handle)
                self._prompt_registered = False
                self._prompt_handle = None
                logger.info("Prompt hotkey unregistered: %s", self.prompt_hotkey)
            except Exception as e:
                logger.warning("Failed to unregister prompt hotkey: %s", e)
                self._prompt_registered = False
                self._prompt_handle = None

        # Unregister main hotkey
        if self._registered and self._hotkey_handle is not None:
            try:
                kb.remove_hotkey(self._hotkey_handle)
                self._registered = False
                self._hotkey_handle = None
                logger.info("Global hotkey unregistered: %s", self.hotkey)
            except Exception as e:
                logger.warning("Failed to unregister hotkey: %s", e)
                self._registered = False
                self._hotkey_handle = None
