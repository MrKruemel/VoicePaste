"""Tests for the evdev-based global hotkey backend (Wayland support).

Validates:
- _parse_combo(): modifier/key parsing, aliases, and error handling
- check_evdev_permissions(): group membership and device probing
- EvdevKeyboardMonitor: combo registration, removal, key matching
- Public API: singleton lifecycle, add/remove/stop

All tests mock the ``evdev`` library since it is a Linux-only optional
dependency that will not be installed in all test environments.
"""

import sys
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_linux_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Linux-only module",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_evdev_hotkey():
    """Import evdev_hotkey, ensuring the evdev lazy-import branch is covered.

    Returns the module object. Each call re-imports to reset module-level
    singleton state (_monitor, _monitor_lock).
    """
    import importlib
    import evdev_hotkey
    importlib.reload(evdev_hotkey)
    return evdev_hotkey


# ---------------------------------------------------------------------------
# TestParseCombo
# ---------------------------------------------------------------------------

@_linux_only
class TestParseCombo:
    """Tests for _parse_combo() hotkey string parsing."""

    def test_parse_ctrl_alt_r(self):
        """'ctrl+alt+r' should parse to modifiers={ctrl, alt}, keycode=19."""
        mod = _import_evdev_hotkey()
        modifiers, keycode = mod._parse_combo("ctrl+alt+r")
        assert modifiers == frozenset({"ctrl", "alt"})
        assert keycode == 19

    def test_parse_single_key(self):
        """'escape' should parse to empty modifiers, keycode=1."""
        mod = _import_evdev_hotkey()
        modifiers, keycode = mod._parse_combo("escape")
        assert modifiers == frozenset()
        assert keycode == 1

    def test_parse_modifier_aliases(self):
        """'win+r' should normalise 'win' to 'super', keycode=19."""
        mod = _import_evdev_hotkey()
        modifiers, keycode = mod._parse_combo("win+r")
        assert modifiers == frozenset({"super"})
        assert keycode == 19

    def test_parse_shift_a(self):
        """'shift+a' should parse to modifiers={shift}, keycode=30."""
        mod = _import_evdev_hotkey()
        modifiers, keycode = mod._parse_combo("shift+a")
        assert modifiers == frozenset({"shift"})
        assert keycode == 30

    def test_parse_invalid_empty(self):
        """Empty string should raise ValueError."""
        mod = _import_evdev_hotkey()
        with pytest.raises(ValueError, match="Invalid hotkey format"):
            mod._parse_combo("")

    def test_parse_unknown_key(self):
        """'ctrl+foobar' should raise ValueError for unknown key."""
        mod = _import_evdev_hotkey()
        with pytest.raises(ValueError, match="Unknown key.*foobar"):
            mod._parse_combo("ctrl+foobar")

    def test_parse_multiple_nonmod_keys(self):
        """'ctrl+a+b' should raise ValueError (two non-modifier keys)."""
        mod = _import_evdev_hotkey()
        with pytest.raises(ValueError, match="Multiple non-modifier keys"):
            mod._parse_combo("ctrl+a+b")

    def test_parse_case_insensitive(self):
        """Combo parsing should be case-insensitive."""
        mod = _import_evdev_hotkey()
        modifiers, keycode = mod._parse_combo("Ctrl+Alt+R")
        assert modifiers == frozenset({"ctrl", "alt"})
        assert keycode == 19

    def test_parse_all_modifier_aliases(self):
        """'cmd', 'meta', 'win', 'super' should all normalise to 'super'."""
        mod = _import_evdev_hotkey()
        for alias in ("cmd", "meta", "win", "super"):
            modifiers, keycode = mod._parse_combo(f"{alias}+a")
            assert modifiers == frozenset({"super"}), f"Alias '{alias}' not normalised"
            assert keycode == 30

    def test_parse_modifiers_only_raises(self):
        """'ctrl+alt' with no main key should raise ValueError."""
        mod = _import_evdev_hotkey()
        with pytest.raises(ValueError, match="No main.*key"):
            mod._parse_combo("ctrl+alt")


# ---------------------------------------------------------------------------
# TestPermissionCheck
# ---------------------------------------------------------------------------

@_linux_only
class TestPermissionCheck:
    """Tests for check_evdev_permissions()."""

    def test_root_always_ok(self):
        """euid=0 (root) should always return (True, ...)."""
        mod = _import_evdev_hotkey()
        with patch("evdev_hotkey.os.geteuid", return_value=0):
            ok, msg = mod.check_evdev_permissions()
        assert ok is True
        assert "root" in msg.lower()

    def test_user_not_in_input_group(self):
        """User not in 'input' group should get (False, usermod hint)."""
        mod = _import_evdev_hotkey()

        mock_group = MagicMock()
        mock_group.gr_mem = ["otheruser"]
        mock_group.gr_gid = 999

        with (
            patch("evdev_hotkey.os.geteuid", return_value=1000),
            patch("evdev_hotkey.os.getgid", return_value=1000),
            patch("evdev_hotkey.os.getgroups", return_value=[1000, 100]),
            patch("evdev_hotkey.os.environ", {"USER": "testuser"}),
            patch("evdev_hotkey.grp.getgrnam", return_value=mock_group),
        ):
            ok, msg = mod.check_evdev_permissions()

        assert ok is False
        assert "usermod" in msg

    def test_input_group_missing(self):
        """Missing 'input' group should return (False, groupadd hint)."""
        mod = _import_evdev_hotkey()

        with (
            patch("evdev_hotkey.os.geteuid", return_value=1000),
            patch("evdev_hotkey.os.environ", {"USER": "testuser"}),
            patch("evdev_hotkey.grp.getgrnam", side_effect=KeyError("input")),
        ):
            ok, msg = mod.check_evdev_permissions()

        assert ok is False
        assert "groupadd" in msg

    def test_user_in_group_via_getgroups(self):
        """User in input group via os.getgroups() should proceed to device probe."""
        mod = _import_evdev_hotkey()

        mock_group = MagicMock()
        mock_group.gr_mem = []  # Not listed by name
        mock_group.gr_gid = 999

        # Mock a keyboard device
        mock_device = MagicMock()
        mock_device.capabilities.return_value = {1: [30, 31, 32]}  # Has KEY_A

        with (
            patch("evdev_hotkey.os.geteuid", return_value=1000),
            patch("evdev_hotkey.os.getgid", return_value=1000),
            patch("evdev_hotkey.os.getgroups", return_value=[1000, 999]),
            patch("evdev_hotkey.os.environ", {"USER": "testuser"}),
            patch("evdev_hotkey.grp.getgrnam", return_value=mock_group),
            patch("evdev_hotkey.evdev") as mock_evdev,
        ):
            mock_evdev.list_devices.return_value = ["/dev/input/event0"]
            mock_evdev.InputDevice.return_value = mock_device

            ok, msg = mod.check_evdev_permissions()

        assert ok is True
        assert "keyboard" in msg.lower()

    def test_user_in_group_by_primary_gid(self):
        """User whose primary GID matches input group should proceed to probe."""
        mod = _import_evdev_hotkey()

        mock_group = MagicMock()
        mock_group.gr_mem = []
        mock_group.gr_gid = 999

        mock_device = MagicMock()
        mock_device.capabilities.return_value = {1: [30]}

        with (
            patch("evdev_hotkey.os.geteuid", return_value=1000),
            patch("evdev_hotkey.os.getgid", return_value=999),  # Primary GID matches
            patch("evdev_hotkey.os.getgroups", return_value=[]),
            patch("evdev_hotkey.os.environ", {"USER": "testuser"}),
            patch("evdev_hotkey.grp.getgrnam", return_value=mock_group),
            patch("evdev_hotkey.evdev") as mock_evdev,
        ):
            mock_evdev.list_devices.return_value = ["/dev/input/event0"]
            mock_evdev.InputDevice.return_value = mock_device

            ok, msg = mod.check_evdev_permissions()

        assert ok is True

    def test_no_keyboards_found(self):
        """User in group but no keyboards -> (False, 'No keyboard devices')."""
        mod = _import_evdev_hotkey()

        mock_group = MagicMock()
        mock_group.gr_mem = ["testuser"]
        mock_group.gr_gid = 999

        # Device that is NOT a keyboard (no KEY_A)
        mock_device = MagicMock()
        mock_device.capabilities.return_value = {1: [100, 200]}

        with (
            patch("evdev_hotkey.os.geteuid", return_value=1000),
            patch("evdev_hotkey.os.getgid", return_value=1000),
            patch("evdev_hotkey.os.getgroups", return_value=[]),
            patch("evdev_hotkey.os.environ", {"USER": "testuser"}),
            patch("evdev_hotkey.grp.getgrnam", return_value=mock_group),
            patch("evdev_hotkey.evdev") as mock_evdev,
        ):
            mock_evdev.list_devices.return_value = ["/dev/input/event0"]
            mock_evdev.InputDevice.return_value = mock_device

            ok, msg = mod.check_evdev_permissions()

        assert ok is False
        assert "No keyboard devices" in msg

    def test_permission_error_on_probe(self):
        """PermissionError during device probe -> (False, 'newgrp' hint)."""
        mod = _import_evdev_hotkey()

        mock_group = MagicMock()
        mock_group.gr_mem = ["testuser"]
        mock_group.gr_gid = 999

        with (
            patch("evdev_hotkey.os.geteuid", return_value=1000),
            patch("evdev_hotkey.os.getgid", return_value=1000),
            patch("evdev_hotkey.os.getgroups", return_value=[]),
            patch("evdev_hotkey.os.environ", {"USER": "testuser"}),
            patch("evdev_hotkey.grp.getgrnam", return_value=mock_group),
            patch("evdev_hotkey.evdev") as mock_evdev,
        ):
            mock_evdev.list_devices.side_effect = PermissionError("denied")
            # The code does `evdev.InputDevice(p) for p in evdev.list_devices()`
            # which triggers `list_devices` first. Since list_devices raises here,
            # it goes into the PermissionError except block.

            ok, msg = mod.check_evdev_permissions()

        assert ok is False
        assert "newgrp" in msg


# ---------------------------------------------------------------------------
# TestEvdevKeyboardMonitor
# ---------------------------------------------------------------------------

@_linux_only
class TestEvdevKeyboardMonitor:
    """Tests for the EvdevKeyboardMonitor class."""

    def _make_monitor(self):
        """Create a fresh EvdevKeyboardMonitor without starting its thread."""
        mod = _import_evdev_hotkey()
        monitor = mod.EvdevKeyboardMonitor()
        return mod, monitor

    def test_add_remove_hotkey(self):
        """add_hotkey should store combo, remove_hotkey should delete it."""
        mod, monitor = self._make_monitor()
        callback = MagicMock()

        handle = monitor.add_hotkey("ctrl+alt+r", callback)

        # Combo should be stored
        assert handle in monitor._combos
        stored_mods, stored_key, stored_cb = monitor._combos[handle]
        assert stored_mods == frozenset({"ctrl", "alt"})
        assert stored_key == 19
        assert stored_cb is callback

        # Remove and verify
        monitor.remove_hotkey(handle)
        assert handle not in monitor._combos

    def test_add_key_listener(self):
        """add_key_listener should register with empty modifier set."""
        mod, monitor = self._make_monitor()
        callback = MagicMock()

        handle = monitor.add_key_listener("escape", callback)

        assert handle in monitor._combos
        stored_mods, stored_key, stored_cb = monitor._combos[handle]
        assert stored_mods == frozenset()
        assert stored_key == 1
        assert stored_cb is callback

    def test_add_key_listener_invalid_key(self):
        """add_key_listener should raise ValueError for unknown key."""
        mod, monitor = self._make_monitor()
        with pytest.raises(ValueError, match="Unknown key name"):
            monitor.add_key_listener("nonexistent", MagicMock())

    def test_check_combos_fires_callback(self):
        """_check_combos should fire callback when held keys match a combo."""
        mod, monitor = self._make_monitor()
        callback = MagicMock()

        monitor.add_hotkey("ctrl+alt+r", callback)

        # Simulate held keys: left ctrl (29) + left alt (56) + r (19)
        monitor._held_keys = {29, 56, 19}

        # _check_combos fires callback in a separate thread, so we need
        # to wait for it briefly.
        with patch.object(
            mod.EvdevKeyboardMonitor,
            "_fire_callback",
            side_effect=lambda cb: cb(),
        ):
            monitor._check_combos(pressed_code=19)

        callback.assert_called_once()

    def test_check_combos_no_match_wrong_key(self):
        """_check_combos should not fire if the pressed key is wrong."""
        mod, monitor = self._make_monitor()
        callback = MagicMock()

        monitor.add_hotkey("ctrl+alt+r", callback)
        monitor._held_keys = {29, 56, 30}  # ctrl + alt + a (not r)

        with patch.object(
            mod.EvdevKeyboardMonitor,
            "_fire_callback",
            side_effect=lambda cb: cb(),
        ):
            monitor._check_combos(pressed_code=30)  # 'a' pressed, combo wants 'r'

        callback.assert_not_called()

    def test_check_combos_no_match_missing_modifier(self):
        """_check_combos should not fire if a required modifier is not held."""
        mod, monitor = self._make_monitor()
        callback = MagicMock()

        monitor.add_hotkey("ctrl+alt+r", callback)
        monitor._held_keys = {29, 19}  # ctrl + r, missing alt

        with patch.object(
            mod.EvdevKeyboardMonitor,
            "_fire_callback",
            side_effect=lambda cb: cb(),
        ):
            monitor._check_combos(pressed_code=19)

        callback.assert_not_called()

    def test_check_combos_single_key_listener(self):
        """Single-key listener (no modifiers) should fire on key press."""
        mod, monitor = self._make_monitor()
        callback = MagicMock()

        monitor.add_key_listener("escape", callback)
        monitor._held_keys = {1}  # Only escape held

        with patch.object(
            mod.EvdevKeyboardMonitor,
            "_fire_callback",
            side_effect=lambda cb: cb(),
        ):
            monitor._check_combos(pressed_code=1)

        callback.assert_called_once()

    def test_modifiers_match_all_present(self):
        """_modifiers_match returns True when all required modifiers are held."""
        _mod, monitor = self._make_monitor()

        # Left ctrl (29) + right alt (100)
        monitor._held_keys = {29, 100, 19}
        assert monitor._modifiers_match(frozenset({"ctrl", "alt"})) is True

    def test_modifiers_match_partial(self):
        """_modifiers_match returns False when not all modifiers are held."""
        _mod, monitor = self._make_monitor()

        monitor._held_keys = {29, 19}  # Only ctrl, no alt
        assert monitor._modifiers_match(frozenset({"ctrl", "alt"})) is False

    def test_modifiers_match_empty_required(self):
        """_modifiers_match with empty required set always returns True."""
        _mod, monitor = self._make_monitor()

        monitor._held_keys = set()
        assert monitor._modifiers_match(frozenset()) is True

    def test_modifiers_match_right_variants(self):
        """Right-hand modifier keys should satisfy modifier requirements."""
        _mod, monitor = self._make_monitor()

        # Right ctrl (97) + right shift (54)
        monitor._held_keys = {97, 54}
        assert monitor._modifiers_match(frozenset({"ctrl", "shift"})) is True

    def test_multiple_hotkeys_independent(self):
        """Multiple hotkeys can be registered and removed independently."""
        _mod, monitor = self._make_monitor()
        cb1 = MagicMock()
        cb2 = MagicMock()

        h1 = monitor.add_hotkey("ctrl+a", cb1)
        h2 = monitor.add_hotkey("ctrl+b", cb2)

        assert h1 in monitor._combos
        assert h2 in monitor._combos
        assert h1 != h2

        monitor.remove_hotkey(h1)
        assert h1 not in monitor._combos
        assert h2 in monitor._combos  # h2 still present

    def test_remove_nonexistent_handle_is_noop(self):
        """Removing a handle that does not exist should not raise."""
        _mod, monitor = self._make_monitor()
        monitor.remove_hotkey(99999)  # Should not error

    def test_handle_counter_increments(self):
        """Each add_hotkey/add_key_listener should return a unique handle."""
        _mod, monitor = self._make_monitor()
        handles = []
        for key in ("a", "b", "c", "d"):
            handles.append(monitor.add_key_listener(key, MagicMock()))

        assert len(set(handles)) == 4
        assert handles == sorted(handles)  # Monotonically increasing

    def test_fire_callback_catches_exceptions(self):
        """_fire_callback should not propagate exceptions from callbacks."""
        mod, monitor = self._make_monitor()

        def bad_callback():
            raise RuntimeError("callback error")

        # Should not raise
        mod.EvdevKeyboardMonitor._fire_callback(bad_callback)


# ---------------------------------------------------------------------------
# TestIsKeyboard
# ---------------------------------------------------------------------------

@_linux_only
class TestIsKeyboard:
    """Tests for the _is_keyboard() helper."""

    def test_device_with_key_a_is_keyboard(self):
        """Device with KEY_A (30) capability is a keyboard."""
        mod = _import_evdev_hotkey()
        device = MagicMock()
        device.capabilities.return_value = {1: [30, 31, 32, 33]}  # EV_KEY with KEY_A
        assert mod._is_keyboard(device) is True

    def test_device_without_key_a_is_not_keyboard(self):
        """Device without KEY_A is not a keyboard."""
        mod = _import_evdev_hotkey()
        device = MagicMock()
        device.capabilities.return_value = {1: [256, 257]}  # BTN_0, BTN_1 (mouse)
        assert mod._is_keyboard(device) is False

    def test_device_without_ev_key(self):
        """Device without EV_KEY capabilities is not a keyboard."""
        mod = _import_evdev_hotkey()
        device = MagicMock()
        device.capabilities.return_value = {3: [0, 1]}  # EV_ABS only (touchpad)
        assert mod._is_keyboard(device) is False


# ---------------------------------------------------------------------------
# TestPublicAPI
# ---------------------------------------------------------------------------

@_linux_only
class TestPublicAPI:
    """Tests for the module-level public API functions."""

    def test_evdev_add_hotkey_starts_monitor(self):
        """evdev_add_hotkey should get/create the singleton and call add_hotkey."""
        mod = _import_evdev_hotkey()

        mock_monitor = MagicMock()
        mock_monitor.add_hotkey.return_value = 42

        with patch.object(mod, "_get_monitor", return_value=mock_monitor):
            handle = mod.evdev_add_hotkey("ctrl+r", lambda: None)

        assert handle == 42
        mock_monitor.add_hotkey.assert_called_once()
        call_args = mock_monitor.add_hotkey.call_args
        assert call_args[0][0] == "ctrl+r"

    def test_evdev_add_key_listener_starts_monitor(self):
        """evdev_add_key_listener should delegate to the singleton monitor."""
        mod = _import_evdev_hotkey()

        mock_monitor = MagicMock()
        mock_monitor.add_key_listener.return_value = 7

        with patch.object(mod, "_get_monitor", return_value=mock_monitor):
            handle = mod.evdev_add_key_listener("escape", lambda: None)

        assert handle == 7
        mock_monitor.add_key_listener.assert_called_once()
        call_args = mock_monitor.add_key_listener.call_args
        assert call_args[0][0] == "escape"

    def test_evdev_remove_hotkey_delegates_to_monitor(self):
        """evdev_remove_hotkey should call remove_hotkey on the singleton."""
        mod = _import_evdev_hotkey()

        mock_monitor = MagicMock()
        mod._monitor = mock_monitor

        mod.evdev_remove_hotkey(42)

        mock_monitor.remove_hotkey.assert_called_once_with(42)

    def test_evdev_remove_hotkey_noop_when_no_monitor(self):
        """evdev_remove_hotkey should not error when no monitor exists."""
        mod = _import_evdev_hotkey()
        mod._monitor = None

        # Should not raise
        mod.evdev_remove_hotkey(42)

    def test_stop_monitor_noop_when_not_started(self):
        """stop_monitor() when _monitor is None should not error."""
        mod = _import_evdev_hotkey()
        mod._monitor = None

        # Should not raise
        mod.stop_monitor()

    def test_stop_monitor_stops_and_clears(self):
        """stop_monitor() should call stop() on the monitor and set it to None."""
        mod = _import_evdev_hotkey()

        mock_monitor = MagicMock()
        mod._monitor = mock_monitor

        mod.stop_monitor()

        mock_monitor.stop.assert_called_once()
        assert mod._monitor is None

    def test_get_monitor_creates_singleton(self):
        """_get_monitor should create and start a monitor on first call."""
        mod = _import_evdev_hotkey()
        mod._monitor = None

        mock_monitor_instance = MagicMock()

        with patch.object(
            mod, "EvdevKeyboardMonitor", return_value=mock_monitor_instance
        ):
            result = mod._get_monitor()

        assert result is mock_monitor_instance
        mock_monitor_instance.start.assert_called_once()
        assert mod._monitor is mock_monitor_instance

    def test_get_monitor_returns_existing(self):
        """_get_monitor should return existing monitor without creating a new one."""
        mod = _import_evdev_hotkey()

        existing = MagicMock()
        mod._monitor = existing

        with patch.object(mod, "EvdevKeyboardMonitor") as mock_cls:
            result = mod._get_monitor()

        assert result is existing
        mock_cls.assert_not_called()
