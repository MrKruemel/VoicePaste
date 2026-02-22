"""Tests for the UInputController keystroke injection (Wayland paste).

Validates:
- UInputController: device creation, send_key, close
- Combo injection: modifier + key sequences
- Singleton: get_uinput_controller, cleanup_uinput
- Public API: uinput_is_available, uinput_send_key
- Error handling: unavailable /dev/uinput, unknown keys

All tests mock evdev.UInput since it requires /dev/uinput write access.
"""

import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_linux_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Linux-only module",
)


def _import_evdev_hotkey():
    """Import and reload evdev_hotkey to reset singleton state."""
    import importlib
    import evdev_hotkey
    importlib.reload(evdev_hotkey)
    return evdev_hotkey


# ---------------------------------------------------------------------------
# TestUInputController
# ---------------------------------------------------------------------------

@_linux_only
class TestUInputController:
    """Tests for the UInputController class."""

    def test_is_available_writable(self):
        """is_available returns True when /dev/uinput is writable."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()
        with patch("evdev_hotkey.os.access", return_value=True):
            assert ctrl.is_available() is True

    def test_is_available_not_writable(self):
        """is_available returns False when /dev/uinput is not writable."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()
        with patch("evdev_hotkey.os.access", return_value=False):
            assert ctrl.is_available() is False

    def test_is_available_no_evdev(self):
        """is_available returns False when evdev is not installed."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()
        with patch.object(mod, "evdev", None):
            assert ctrl.is_available() is False

    def test_send_key_simple(self):
        """send_key('enter') injects key down + up + syn for keycode 28."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        mock_uinput = MagicMock()
        with patch.object(ctrl, "_ensure_device", return_value=mock_uinput):
            result = ctrl.send_key("enter")

        assert result is True
        # Should have: key down (28, 1), syn, sleep, key up (28, 0), syn
        write_calls = mock_uinput.write.call_args_list
        # Key down
        assert any(
            c.args == (1, 28, 1) for c in write_calls
        ), f"Expected key down (1, 28, 1) in {write_calls}"
        # Key up
        assert any(
            c.args == (1, 28, 0) for c in write_calls
        ), f"Expected key up (1, 28, 0) in {write_calls}"

    def test_send_key_combo_ctrl_v(self):
        """send_key('ctrl+v') injects ctrl down, v down/up, ctrl up."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        mock_uinput = MagicMock()
        with (
            patch.object(ctrl, "_ensure_device", return_value=mock_uinput),
            patch("evdev_hotkey.time.sleep"),  # speed up test
        ):
            result = ctrl.send_key("ctrl+v")

        assert result is True
        write_calls = mock_uinput.write.call_args_list
        # Extract (type, code, value) tuples, filtering out EV_SYN
        key_events = [c.args for c in write_calls if c.args[0] == 1]
        # Expected sequence: ctrl down, v down, v up, ctrl up
        assert key_events == [
            (1, 29, 1),   # KEY_LEFTCTRL down
            (1, 47, 1),   # KEY_V down
            (1, 47, 0),   # KEY_V up
            (1, 29, 0),   # KEY_LEFTCTRL up
        ]

    def test_send_key_combo_ctrl_shift_v(self):
        """send_key('ctrl+shift+v') handles multi-modifier combos."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        mock_uinput = MagicMock()
        with (
            patch.object(ctrl, "_ensure_device", return_value=mock_uinput),
            patch("evdev_hotkey.time.sleep"),
        ):
            result = ctrl.send_key("ctrl+shift+v")

        assert result is True
        write_calls = mock_uinput.write.call_args_list
        key_events = [c.args for c in write_calls if c.args[0] == 1]
        # Should have: ctrl down, shift down, v down, v up, shift up, ctrl up
        assert len(key_events) == 6
        # First two are modifier downs (ctrl=29, shift=42 in some order)
        mod_down_codes = {key_events[0][1], key_events[1][1]}
        assert mod_down_codes == {29, 42}
        # Main key
        assert key_events[2] == (1, 47, 1)  # V down
        assert key_events[3] == (1, 47, 0)  # V up
        # Last two are modifier ups (reversed)
        mod_up_codes = {key_events[4][1], key_events[5][1]}
        assert mod_up_codes == {29, 42}

    def test_send_key_unknown_key(self):
        """send_key with unknown key returns False."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        result = ctrl.send_key("nonexistent")
        assert result is False

    def test_send_key_no_main_key(self):
        """send_key with only modifiers returns False."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        result = ctrl.send_key("ctrl+alt")
        assert result is False

    def test_send_key_uinput_error(self):
        """send_key returns False when UInput write raises an error."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        mock_uinput = MagicMock()
        mock_uinput.write.side_effect = OSError("write failed")
        with patch.object(ctrl, "_ensure_device", return_value=mock_uinput):
            result = ctrl.send_key("enter")

        assert result is False

    def test_ensure_device_creates_uinput(self):
        """_ensure_device creates a UInput with the VoicePaste name."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        mock_uinput_cls = MagicMock()
        mock_uinput_instance = MagicMock()
        mock_uinput_instance.device = "/dev/input/event99"
        mock_uinput_cls.return_value = mock_uinput_instance

        with patch.object(mod.evdev, "UInput", mock_uinput_cls):
            result = ctrl._ensure_device()

        assert result is mock_uinput_instance
        mock_uinput_cls.assert_called_once_with(
            name="VoicePaste Virtual Keyboard",
            phys="voicepaste/uinput",
        )

    def test_ensure_device_reuses_existing(self):
        """_ensure_device returns existing device without creating new one."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        existing = MagicMock()
        ctrl._uinput = existing

        result = ctrl._ensure_device()
        assert result is existing

    def test_close_closes_device(self):
        """close() closes the UInput device and sets it to None."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        mock_device = MagicMock()
        ctrl._uinput = mock_device

        ctrl.close()

        mock_device.close.assert_called_once()
        assert ctrl._uinput is None

    def test_close_noop_when_no_device(self):
        """close() does not error when no device exists."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()
        ctrl.close()  # Should not raise

    def test_close_handles_exception(self):
        """close() handles exceptions from device.close()."""
        mod = _import_evdev_hotkey()
        ctrl = mod.UInputController()

        mock_device = MagicMock()
        mock_device.close.side_effect = OSError("close failed")
        ctrl._uinput = mock_device

        ctrl.close()  # Should not raise
        assert ctrl._uinput is None


# ---------------------------------------------------------------------------
# TestUInputPublicAPI
# ---------------------------------------------------------------------------

@_linux_only
class TestUInputPublicAPI:
    """Tests for the module-level UInput public API functions."""

    def test_get_uinput_controller_creates_singleton(self):
        """get_uinput_controller creates a new controller on first call."""
        mod = _import_evdev_hotkey()
        mod._uinput_controller = None

        result = mod.get_uinput_controller()

        assert result is not None
        assert isinstance(result, mod.UInputController)
        assert mod._uinput_controller is result

    def test_get_uinput_controller_returns_existing(self):
        """get_uinput_controller returns existing controller."""
        mod = _import_evdev_hotkey()
        existing = mod.UInputController()
        mod._uinput_controller = existing

        result = mod.get_uinput_controller()

        assert result is existing

    def test_uinput_is_available_true(self):
        """uinput_is_available returns True when /dev/uinput is writable."""
        mod = _import_evdev_hotkey()
        mod._uinput_controller = None

        with patch("evdev_hotkey.os.access", return_value=True):
            assert mod.uinput_is_available() is True

    def test_uinput_is_available_false(self):
        """uinput_is_available returns False when /dev/uinput is not writable."""
        mod = _import_evdev_hotkey()
        mod._uinput_controller = None

        with patch("evdev_hotkey.os.access", return_value=False):
            assert mod.uinput_is_available() is False

    def test_uinput_is_available_no_evdev(self):
        """uinput_is_available returns False when evdev is not installed."""
        mod = _import_evdev_hotkey()
        with patch.object(mod, "evdev", None):
            assert mod.uinput_is_available() is False

    def test_uinput_send_key_success(self):
        """uinput_send_key delegates to controller.send_key."""
        mod = _import_evdev_hotkey()

        mock_ctrl = MagicMock()
        mock_ctrl.send_key.return_value = True
        mod._uinput_controller = mock_ctrl

        result = mod.uinput_send_key("ctrl+v")

        assert result is True
        mock_ctrl.send_key.assert_called_once_with("ctrl+v")

    def test_uinput_send_key_failure(self):
        """uinput_send_key returns False when controller fails."""
        mod = _import_evdev_hotkey()

        mock_ctrl = MagicMock()
        mock_ctrl.send_key.return_value = False
        mod._uinput_controller = mock_ctrl

        result = mod.uinput_send_key("ctrl+v")

        assert result is False

    def test_uinput_send_key_no_evdev(self):
        """uinput_send_key returns False when evdev is not installed."""
        mod = _import_evdev_hotkey()
        with patch.object(mod, "evdev", None):
            result = mod.uinput_send_key("ctrl+v")
        assert result is False

    def test_cleanup_uinput(self):
        """cleanup_uinput closes and clears the singleton."""
        mod = _import_evdev_hotkey()
        mock_ctrl = MagicMock()
        mod._uinput_controller = mock_ctrl

        mod.cleanup_uinput()

        mock_ctrl.close.assert_called_once()
        assert mod._uinput_controller is None

    def test_cleanup_uinput_noop_when_none(self):
        """cleanup_uinput does not error when no controller exists."""
        mod = _import_evdev_hotkey()
        mod._uinput_controller = None
        mod.cleanup_uinput()  # Should not raise


# ---------------------------------------------------------------------------
# TestGetRelevantKeycodes
# ---------------------------------------------------------------------------

@_linux_only
class TestGetRelevantKeycodes:
    """Tests for _get_relevant_keycodes (used for filtered logging)."""

    def _make_monitor(self):
        mod = _import_evdev_hotkey()
        return mod, mod.EvdevKeyboardMonitor()

    def test_empty_when_no_combos(self):
        """Returns empty set when no combos registered."""
        _mod, monitor = self._make_monitor()
        assert monitor._get_relevant_keycodes() == set()

    def test_includes_main_key(self):
        """Includes the main trigger keycode."""
        _mod, monitor = self._make_monitor()
        monitor.add_hotkey("ctrl+alt+r", lambda: None)
        relevant = monitor._get_relevant_keycodes()
        assert 19 in relevant  # KEY_R

    def test_includes_modifier_variants(self):
        """Includes left and right variants of required modifiers."""
        _mod, monitor = self._make_monitor()
        monitor.add_hotkey("ctrl+r", lambda: None)
        relevant = monitor._get_relevant_keycodes()
        assert 29 in relevant   # KEY_LEFTCTRL
        assert 97 in relevant   # KEY_RIGHTCTRL
        assert 19 in relevant   # KEY_R

    def test_excludes_unrelated_keys(self):
        """Regular typing keys are not in the relevant set."""
        _mod, monitor = self._make_monitor()
        monitor.add_hotkey("ctrl+alt+r", lambda: None)
        relevant = monitor._get_relevant_keycodes()
        # Regular letter keys should NOT be relevant
        assert 30 not in relevant  # KEY_A
        assert 17 not in relevant  # KEY_W
        assert 57 not in relevant  # KEY_SPACE

    def test_multiple_combos_merged(self):
        """Multiple combos contribute to the relevant set."""
        _mod, monitor = self._make_monitor()
        monitor.add_hotkey("ctrl+r", lambda: None)
        monitor.add_key_listener("escape", lambda: None)
        relevant = monitor._get_relevant_keycodes()
        assert 19 in relevant  # KEY_R
        assert 1 in relevant   # KEY_ESCAPE
        assert 29 in relevant  # KEY_LEFTCTRL


# ---------------------------------------------------------------------------
# TestHotkeyLogging
# ---------------------------------------------------------------------------

@_linux_only
class TestHotkeyLogging:
    """Tests verifying that combo match logging works correctly."""

    def _make_monitor(self):
        mod = _import_evdev_hotkey()
        return mod, mod.EvdevKeyboardMonitor()

    def test_combo_match_logs_info(self):
        """When a combo matches, an INFO log line is emitted."""
        mod, monitor = self._make_monitor()
        callback = MagicMock()
        monitor.add_hotkey("ctrl+alt+r", callback)

        # Simulate: ctrl + alt + r held
        monitor._held_keys = {29, 56, 19}

        with (
            patch.object(
                mod.EvdevKeyboardMonitor,
                "_fire_callback",
                side_effect=lambda cb: cb(),
            ),
            patch("evdev_hotkey.logger") as mock_logger,
        ):
            monitor._check_combos(pressed_code=19)

        callback.assert_called_once()
        # Verify the info log was called with the combo string
        mock_logger.info.assert_called()
        log_msg = mock_logger.info.call_args[0][0]
        assert "Hotkey matched" in log_msg
