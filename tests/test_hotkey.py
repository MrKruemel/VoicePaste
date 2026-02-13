"""Tests for the hotkey manager.

Validates:
- US-0.1.1: Global hotkey registration
- REQ-S15: Only specific hotkey hooks
- Debounce behavior
- Configurable hotkey support
"""

import time
import pytest
from unittest.mock import patch, MagicMock, call

from constants import DEFAULT_HOTKEY
from hotkey import HotkeyManager


class TestHotkeyManager:
    """Test the HotkeyManager class."""

    @patch("hotkey.kb")
    def test_register_hotkey_with_default(self, mock_kb):
        """US-0.1.1: Default hotkey is registered globally."""
        manager = HotkeyManager()
        callback = MagicMock()

        manager.register(callback)

        mock_kb.add_hotkey.assert_called_once()
        call_args = mock_kb.add_hotkey.call_args
        assert call_args[0][0] == DEFAULT_HOTKEY

    @patch("hotkey.kb")
    def test_register_hotkey_with_custom_combination(self, mock_kb):
        """A custom hotkey string should be forwarded to kb.add_hotkey."""
        manager = HotkeyManager(hotkey="ctrl+alt+r")
        callback = MagicMock()

        manager.register(callback)

        mock_kb.add_hotkey.assert_called_once()
        call_args = mock_kb.add_hotkey.call_args
        assert call_args[0][0] == "ctrl+alt+r"

    @patch("hotkey.kb")
    def test_unregister_hotkey(self, mock_kb):
        """Hotkey can be unregistered cleanly."""
        manager = HotkeyManager()
        manager.register(MagicMock())
        manager.unregister()

        mock_kb.remove_hotkey.assert_called_once()

    @patch("hotkey.kb")
    def test_debounce_blocks_rapid_presses(self, mock_kb):
        """Rapid presses within debounce window are ignored."""
        manager = HotkeyManager(debounce_ms=300)
        callback = MagicMock()
        manager._callback = callback

        # First press should go through
        manager._on_hotkey()
        assert callback.call_count == 1

        # Second press immediately should be debounced
        manager._on_hotkey()
        assert callback.call_count == 1

    @patch("hotkey.kb")
    def test_debounce_allows_after_window(self, mock_kb):
        """Presses after debounce window are accepted."""
        manager = HotkeyManager(debounce_ms=50)
        callback = MagicMock()
        manager._callback = callback

        manager._on_hotkey()
        assert callback.call_count == 1

        # Wait for debounce to expire
        time.sleep(0.1)

        manager._on_hotkey()
        assert callback.call_count == 2

    @patch("hotkey.kb")
    def test_hotkey_attribute_stores_configured_value(self, mock_kb):
        """HotkeyManager.hotkey should reflect the configured combination."""
        manager = HotkeyManager(hotkey="F9")
        assert manager.hotkey == "F9"


class TestOnlySpecificHotkeys:
    """REQ-S15: Verify only specific hotkey combinations are hooked."""

    @patch("hotkey.kb")
    def test_no_blanket_monitoring(self, mock_kb):
        """Should not use keyboard.hook() or keyboard.on_press()."""
        manager = HotkeyManager()
        manager.register(MagicMock())

        # Should only call add_hotkey, not hook or on_press
        mock_kb.hook.assert_not_called()
        if hasattr(mock_kb, "on_press"):
            mock_kb.on_press.assert_not_called()

    def test_default_hotkey_is_ctrl_alt_r(self):
        """Default hotkey should be ctrl+alt+r (safe default, no conflicts)."""
        manager = HotkeyManager()
        assert manager.hotkey == "ctrl+alt+r"
        assert manager.hotkey == DEFAULT_HOTKEY
