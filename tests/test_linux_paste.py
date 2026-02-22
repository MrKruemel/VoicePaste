"""Tests for Linux Wayland paste simulation in platform_impl/_linux.py.

Validates:
- _simulate_wayland_keystroke: UInput > ydotool > wtype fallback chain
- _combo_to_ydotool_args: scancode sequence generation
- _combo_to_wtype_args: wtype argument generation
- paste_text: clipboard write + keystroke simulation (Wayland path)
- paste_text: paste_shortcut override ("ctrl+v", "ctrl+shift+v", "auto")
- send_key: Wayland delegation to _simulate_wayland_keystroke
- _is_terminal_focused: session-type dispatch (Wayland vs X11)
- _is_terminal_focused_wayland: gdbus-based terminal detection

All tests mock subprocess, evdev_hotkey, and shutil.which.
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch, call

import pytest

_linux_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Linux-only module",
)


@pytest.fixture(autouse=True)
def _add_src_to_path():
    """Ensure src/ is on sys.path for imports."""
    import os
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    if src not in sys.path:
        sys.path.insert(0, os.path.abspath(src))


def _import_linux():
    """Import the Linux platform module."""
    from platform_impl import _linux
    return _linux


# ---------------------------------------------------------------------------
# TestComboToYdotoolArgs
# ---------------------------------------------------------------------------

@_linux_only
class TestComboToYdotoolArgs:
    """Tests for _combo_to_ydotool_args."""

    def test_ctrl_v(self):
        mod = _import_linux()
        args = mod._combo_to_ydotool_args("ctrl+v")
        # Press ctrl, press v, release v, release ctrl
        assert args == ["29:1", "47:1", "47:0", "29:0"]

    def test_ctrl_shift_v(self):
        mod = _import_linux()
        args = mod._combo_to_ydotool_args("ctrl+shift+v")
        assert args == ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]

    def test_enter(self):
        mod = _import_linux()
        args = mod._combo_to_ydotool_args("enter")
        assert args == ["28:1", "28:0"]

    def test_unknown_key_returns_empty(self):
        mod = _import_linux()
        args = mod._combo_to_ydotool_args("nonexistent")
        assert args == []

    def test_empty_returns_empty(self):
        mod = _import_linux()
        args = mod._combo_to_ydotool_args("")
        assert args == []


# ---------------------------------------------------------------------------
# TestComboToWtypeArgs
# ---------------------------------------------------------------------------

@_linux_only
class TestComboToWtypeArgs:
    """Tests for _combo_to_wtype_args."""

    def test_ctrl_v(self):
        mod = _import_linux()
        args = mod._combo_to_wtype_args("ctrl+v")
        assert args == ["-M", "ctrl", "v", "-m", "ctrl"]

    def test_ctrl_shift_v(self):
        mod = _import_linux()
        args = mod._combo_to_wtype_args("ctrl+shift+v")
        # Modifiers pressed in order, released in reverse
        assert args == ["-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"]

    def test_enter(self):
        mod = _import_linux()
        args = mod._combo_to_wtype_args("enter")
        assert args == ["enter"]

    def test_only_modifiers_returns_empty(self):
        mod = _import_linux()
        args = mod._combo_to_wtype_args("ctrl+alt")
        assert args == []


# ---------------------------------------------------------------------------
# TestSimulateWaylandKeystroke
# ---------------------------------------------------------------------------

@_linux_only
class TestSimulateWaylandKeystroke:
    """Tests for _simulate_wayland_keystroke fallback chain."""

    def test_uinput_preferred(self):
        """UInput is used first when available."""
        mod = _import_linux()

        mock_evdev_hotkey = MagicMock()
        mock_evdev_hotkey.uinput_is_available.return_value = True
        mock_evdev_hotkey.uinput_send_key.return_value = True

        with patch.dict("sys.modules", {"evdev_hotkey": mock_evdev_hotkey}):
            result = mod._simulate_wayland_keystroke("ctrl+v")

        assert result is True
        mock_evdev_hotkey.uinput_send_key.assert_called_once_with("ctrl+v")

    def test_ydotool_fallback(self):
        """ydotool is used when UInput is unavailable."""
        mod = _import_linux()

        mock_evdev_hotkey = MagicMock()
        mock_evdev_hotkey.uinput_is_available.return_value = False

        with (
            patch.dict("sys.modules", {"evdev_hotkey": mock_evdev_hotkey}),
            patch("platform_impl._linux.shutil.which") as mock_which,
            patch("platform_impl._linux.subprocess.run") as mock_run,
        ):
            # ydotool is found
            mock_which.side_effect = lambda cmd: "/usr/bin/ydotool" if cmd == "ydotool" else None
            result = mod._simulate_wayland_keystroke("ctrl+v")

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "/usr/bin/ydotool"
        assert args[1] == "key"

    def test_wtype_fallback(self):
        """wtype is used when both UInput and ydotool are unavailable."""
        mod = _import_linux()

        mock_evdev_hotkey = MagicMock()
        mock_evdev_hotkey.uinput_is_available.return_value = False

        with (
            patch.dict("sys.modules", {"evdev_hotkey": mock_evdev_hotkey}),
            patch("platform_impl._linux.shutil.which") as mock_which,
            patch("platform_impl._linux.subprocess.run") as mock_run,
        ):
            def which_side_effect(cmd):
                if cmd == "wtype":
                    return "/usr/bin/wtype"
                return None
            mock_which.side_effect = which_side_effect
            result = mod._simulate_wayland_keystroke("ctrl+v")

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "/usr/bin/wtype"

    def test_all_methods_fail(self):
        """Returns False when no method is available."""
        mod = _import_linux()

        mock_evdev_hotkey = MagicMock()
        mock_evdev_hotkey.uinput_is_available.return_value = False

        with (
            patch.dict("sys.modules", {"evdev_hotkey": mock_evdev_hotkey}),
            patch("platform_impl._linux.shutil.which", return_value=None),
        ):
            result = mod._simulate_wayland_keystroke("ctrl+v")

        assert result is False

    def test_evdev_import_error_fallback(self):
        """Falls through to ydotool when evdev_hotkey import fails."""
        mod = _import_linux()

        # Make the import inside _simulate_wayland_keystroke fail
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def mock_import(name, *args, **kwargs):
            if name == "evdev_hotkey":
                raise ImportError("no evdev_hotkey")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=mock_import),
            patch("platform_impl._linux.shutil.which") as mock_which,
            patch("platform_impl._linux.subprocess.run") as mock_run,
        ):
            mock_which.side_effect = lambda cmd: "/usr/bin/ydotool" if cmd == "ydotool" else None
            result = mod._simulate_wayland_keystroke("ctrl+v")

        assert result is True


# ---------------------------------------------------------------------------
# TestPasteTextWayland
# ---------------------------------------------------------------------------

@_linux_only
class TestPasteTextWayland:
    """Tests for paste_text() on Wayland sessions."""

    def test_paste_wayland_uinput(self):
        """paste_text writes to clipboard and simulates via UInput on Wayland."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/wl-copy"], "wl-copy"
            )),
            patch("platform_impl._linux.subprocess.run"),
            patch("platform_impl._linux.time.sleep"),
            patch.object(mod, "_is_terminal_focused", return_value=False),
            patch.object(mod, "_simulate_wayland_keystroke", return_value=True) as mock_sim,
        ):
            result = mod.paste_text("Hello World")

        assert result is True
        mock_sim.assert_called_once_with("ctrl+v")

    def test_paste_wayland_terminal_uses_ctrl_shift_v(self):
        """paste_text uses ctrl+shift+v when a terminal is focused."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/wl-copy"], "wl-copy"
            )),
            patch("platform_impl._linux.subprocess.run"),
            patch("platform_impl._linux.time.sleep"),
            patch.object(mod, "_is_terminal_focused", return_value=True),
            patch.object(mod, "_simulate_wayland_keystroke", return_value=True) as mock_sim,
        ):
            result = mod.paste_text("Hello World")

        assert result is True
        mock_sim.assert_called_once_with("ctrl+shift+v")

    def test_paste_wayland_no_simulation_tool(self):
        """paste_text returns False when no simulation tool is available."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/wl-copy"], "wl-copy"
            )),
            patch("platform_impl._linux.subprocess.run"),
            patch("platform_impl._linux.time.sleep"),
            patch.object(mod, "_is_terminal_focused", return_value=False),
            patch.object(mod, "_simulate_wayland_keystroke", return_value=False),
        ):
            result = mod.paste_text("Hello World")

        assert result is False

    def test_paste_empty_text(self):
        """paste_text returns False for empty text."""
        mod = _import_linux()
        assert mod.paste_text("") is False
        assert mod.paste_text("   ") is False


# ---------------------------------------------------------------------------
# TestSendKeyWayland
# ---------------------------------------------------------------------------

@_linux_only
class TestSendKeyWayland:
    """Tests for send_key() on Wayland sessions."""

    def test_send_key_wayland_delegates(self):
        """send_key delegates to _simulate_wayland_keystroke on Wayland."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_simulate_wayland_keystroke", return_value=True) as mock_sim,
        ):
            mod.send_key("enter")

        mock_sim.assert_called_once_with("enter")

    def test_send_key_x11_uses_xdotool(self):
        """send_key uses xdotool on X11."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="x11"),
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/xdotool"),
            patch("platform_impl._linux.subprocess.run") as mock_run,
        ):
            mod.send_key("enter")

        mock_run.assert_called_once_with(
            ["/usr/bin/xdotool", "key", "--clearmodifiers", "Return"],
            timeout=2,
        )


# ---------------------------------------------------------------------------
# TestIsTerminalFocusedDispatch
# ---------------------------------------------------------------------------

@_linux_only
class TestIsTerminalFocusedDispatch:
    """Tests for _is_terminal_focused() session-type dispatch."""

    def test_dispatches_to_wayland_path(self):
        """_is_terminal_focused calls _is_terminal_focused_wayland on Wayland."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(
                mod, "_is_terminal_focused_wayland", return_value=True,
            ) as mock_wayland,
            patch.object(
                mod, "_is_terminal_focused_x11",
            ) as mock_x11,
        ):
            result = mod._is_terminal_focused()

        assert result is True
        mock_wayland.assert_called_once()
        mock_x11.assert_not_called()

    def test_dispatches_to_x11_path(self):
        """_is_terminal_focused calls _is_terminal_focused_x11 on X11."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="x11"),
            patch.object(
                mod, "_is_terminal_focused_wayland",
            ) as mock_wayland,
            patch.object(
                mod, "_is_terminal_focused_x11", return_value=False,
            ) as mock_x11,
        ):
            result = mod._is_terminal_focused()

        assert result is False
        mock_x11.assert_called_once()
        mock_wayland.assert_not_called()


# ---------------------------------------------------------------------------
# TestIsTerminalFocusedWayland
# ---------------------------------------------------------------------------

@_linux_only
class TestIsTerminalFocusedWayland:
    """Tests for _is_terminal_focused_wayland() gdbus detection."""

    def test_returns_true_for_terminal_class(self):
        """Returns True when gdbus reports a known terminal WM_CLASS."""
        mod = _import_linux()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(true, 'gnome-terminal-server')"

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch("platform_impl._linux.subprocess.run", return_value=mock_result),
        ):
            result = mod._is_terminal_focused_wayland()

        assert result is True

    def test_returns_true_for_alacritty(self):
        """Returns True for another terminal emulator (alacritty)."""
        mod = _import_linux()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(true, 'Alacritty')"

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch("platform_impl._linux.subprocess.run", return_value=mock_result),
        ):
            result = mod._is_terminal_focused_wayland()

        assert result is True

    def test_returns_false_for_non_terminal_class(self):
        """Returns False when gdbus reports a non-terminal WM_CLASS."""
        mod = _import_linux()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(true, 'firefox')"

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch("platform_impl._linux.subprocess.run", return_value=mock_result),
        ):
            result = mod._is_terminal_focused_wayland()

        assert result is False

    def test_returns_false_when_gdbus_not_found(self):
        """Returns False (GUI-safe default) when gdbus binary is not installed."""
        mod = _import_linux()

        with patch("platform_impl._linux.shutil.which", return_value=None):
            result = mod._is_terminal_focused_wayland()

        assert result is False  # GUI-safe default: Ctrl+V

    def test_returns_false_on_gdbus_timeout(self):
        """Returns False (GUI-safe default) when gdbus subprocess times out."""
        mod = _import_linux()

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch(
                "platform_impl._linux.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="gdbus", timeout=2),
            ),
        ):
            result = mod._is_terminal_focused_wayland()

        assert result is False  # GUI-safe default: Ctrl+V

    def test_returns_false_on_gdbus_nonzero_exit(self):
        """Returns False (GUI-safe default) when gdbus returns non-zero (e.g. non-GNOME compositor)."""
        mod = _import_linux()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch("platform_impl._linux.subprocess.run", return_value=mock_result),
        ):
            result = mod._is_terminal_focused_wayland()

        assert result is False  # GUI-safe default: Ctrl+V

    def test_returns_false_on_shell_eval_disabled(self):
        """Returns False (GUI-safe default) when Shell.Eval is disabled (GNOME 41+)."""
        mod = _import_linux()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(false, '')"

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch("platform_impl._linux.subprocess.run", return_value=mock_result),
        ):
            result = mod._is_terminal_focused_wayland()

        assert result is False  # GUI-safe default: Ctrl+V

    def test_returns_false_on_unexpected_exception(self):
        """Returns False (GUI-safe default) on any unexpected error from gdbus."""
        mod = _import_linux()

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch(
                "platform_impl._linux.subprocess.run",
                side_effect=OSError("D-Bus not available"),
            ),
        ):
            result = mod._is_terminal_focused_wayland()

        assert result is False  # GUI-safe default: Ctrl+V

    def test_gdbus_called_with_correct_args(self):
        """Verify the exact gdbus command used for GNOME Shell Eval."""
        mod = _import_linux()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(true, '')"

        with (
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/gdbus"),
            patch("platform_impl._linux.subprocess.run", return_value=mock_result) as mock_run,
        ):
            mod._is_terminal_focused_wayland()

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "/usr/bin/gdbus"
        assert args[1:3] == ["call", "--session"]
        assert "--dest" in args
        assert "org.gnome.Shell" in args
        assert "--method" in args
        assert "org.gnome.Shell.Eval" in args


# ---------------------------------------------------------------------------
# TestPasteTextShortcutOverride
# ---------------------------------------------------------------------------

@_linux_only
class TestPasteTextShortcutOverride:
    """Tests for paste_text() paste_shortcut parameter."""

    def test_ctrl_shift_v_override_bypasses_detection(self):
        """paste_shortcut='ctrl+shift+v' forces Ctrl+Shift+V without detection."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/wl-copy"], "wl-copy"
            )),
            patch("platform_impl._linux.subprocess.run"),
            patch("platform_impl._linux.time.sleep"),
            patch.object(mod, "_is_terminal_focused") as mock_detect,
            patch.object(mod, "_simulate_wayland_keystroke", return_value=True) as mock_sim,
        ):
            result = mod.paste_text("test", paste_shortcut="ctrl+shift+v")

        assert result is True
        mock_detect.assert_not_called()
        mock_sim.assert_called_once_with("ctrl+shift+v")

    def test_ctrl_v_override_bypasses_detection(self):
        """paste_shortcut='ctrl+v' forces Ctrl+V without detection."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/wl-copy"], "wl-copy"
            )),
            patch("platform_impl._linux.subprocess.run"),
            patch("platform_impl._linux.time.sleep"),
            patch.object(mod, "_is_terminal_focused") as mock_detect,
            patch.object(mod, "_simulate_wayland_keystroke", return_value=True) as mock_sim,
        ):
            result = mod.paste_text("test", paste_shortcut="ctrl+v")

        assert result is True
        mock_detect.assert_not_called()
        mock_sim.assert_called_once_with("ctrl+v")

    def test_auto_calls_is_terminal_focused(self):
        """paste_shortcut='auto' calls _is_terminal_focused for detection."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/wl-copy"], "wl-copy"
            )),
            patch("platform_impl._linux.subprocess.run"),
            patch("platform_impl._linux.time.sleep"),
            patch.object(mod, "_is_terminal_focused", return_value=False) as mock_detect,
            patch.object(mod, "_simulate_wayland_keystroke", return_value=True) as mock_sim,
        ):
            result = mod.paste_text("test", paste_shortcut="auto")

        assert result is True
        mock_detect.assert_called_once()
        mock_sim.assert_called_once_with("ctrl+v")

    def test_auto_default_calls_is_terminal_focused(self):
        """Default paste_shortcut (no argument) calls _is_terminal_focused."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="wayland"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/wl-copy"], "wl-copy"
            )),
            patch("platform_impl._linux.subprocess.run"),
            patch("platform_impl._linux.time.sleep"),
            patch.object(mod, "_is_terminal_focused", return_value=True) as mock_detect,
            patch.object(mod, "_simulate_wayland_keystroke", return_value=True) as mock_sim,
        ):
            result = mod.paste_text("test")

        assert result is True
        mock_detect.assert_called_once()
        mock_sim.assert_called_once_with("ctrl+shift+v")

    def test_ctrl_v_override_on_x11(self):
        """paste_shortcut='ctrl+v' override also works on X11 path."""
        mod = _import_linux()

        with (
            patch.object(mod, "_detect_session_type", return_value="x11"),
            patch.object(mod, "_find_clipboard_write_tool", return_value=(
                ["/usr/bin/xclip", "-selection", "clipboard", "-i"], "xclip"
            )),
            patch("platform_impl._linux.subprocess.run") as mock_run,
            patch("platform_impl._linux.time.sleep"),
            patch("platform_impl._linux.shutil.which", return_value="/usr/bin/xdotool"),
            patch.object(mod, "_is_terminal_focused") as mock_detect,
        ):
            result = mod.paste_text("test", paste_shortcut="ctrl+v")

        assert result is True
        mock_detect.assert_not_called()
        # The xdotool call should use "ctrl+v" as the key
        xdotool_calls = [
            c for c in mock_run.call_args_list
            if c[0][0][0] == "/usr/bin/xdotool"
        ]
        assert len(xdotool_calls) == 1
        assert xdotool_calls[0][0][0] == ["/usr/bin/xdotool", "key", "ctrl+v"]
