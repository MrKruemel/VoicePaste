"""Tests for the system tray module.

Validates:
- US-0.2.2: Visual state feedback (tray icon colors per state)
- v0.2 menu structure (hidden default + Status + Quit)
- Toast notification method
- Icon color mapping
- Tooltip templates with configurable hotkey
- Hidden default menu item to prevent empty window on tray click
- TrayManager hotkey_label parameter
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from constants import APP_NAME, AppState, DEFAULT_HOTKEY


class TestTrayIconColors:
    """Test tray icon color mapping per UX-SPEC.md section 2.1."""

    def test_state_color_mapping_exists(self):
        """All states should have a defined color."""
        from tray import _STATE_COLORS

        assert AppState.IDLE in _STATE_COLORS
        assert AppState.RECORDING in _STATE_COLORS
        assert AppState.PROCESSING in _STATE_COLORS
        assert AppState.PASTING in _STATE_COLORS

    def test_idle_is_light_silver(self):
        """IDLE state should use light silver-white color (visible on dark bg)."""
        from tray import _STATE_COLORS

        color = _STATE_COLORS[AppState.IDLE]
        assert isinstance(color, tuple)
        assert len(color) == 3
        # Light color: all channels above 200
        assert all(c >= 200 for c in color)

    def test_recording_is_red(self):
        """RECORDING state should use bright red color."""
        from tray import _STATE_COLORS

        color = _STATE_COLORS[AppState.RECORDING]
        assert isinstance(color, tuple)
        assert len(color) == 3
        # Red channel dominant
        assert color[0] > 200
        assert color[1] < 100
        assert color[2] < 100

    def test_processing_is_yellow(self):
        """PROCESSING state should use bright yellow/amber color."""
        from tray import _STATE_COLORS

        color = _STATE_COLORS[AppState.PROCESSING]
        assert isinstance(color, tuple)
        assert len(color) == 3
        # Yellow: high R, high G, low B
        assert color[0] > 200
        assert color[1] > 150
        assert color[2] < 100

    def test_pasting_is_green(self):
        """PASTING state should use bright green color."""
        from tray import _STATE_COLORS

        color = _STATE_COLORS[AppState.PASTING]
        assert isinstance(color, tuple)
        assert len(color) == 3
        # Green channel dominant
        assert color[1] > 150
        assert color[0] < 100


class TestTrayTooltipTemplates:
    """Test tray tooltip templates per UX-SPEC.md section 2.1.

    Tooltips now use template strings with a {hotkey} placeholder,
    resolved at runtime by TrayManager._get_tooltip().
    """

    def test_tooltip_template_mapping_exists(self):
        """All states should have a defined tooltip template."""
        from tray import _STATE_TOOLTIP_TEMPLATES

        for state in AppState:
            assert state in _STATE_TOOLTIP_TEMPLATES

    def test_idle_template_includes_hotkey_placeholder(self):
        """IDLE tooltip template should contain {hotkey} placeholder."""
        from tray import _STATE_TOOLTIP_TEMPLATES

        template = _STATE_TOOLTIP_TEMPLATES[AppState.IDLE]
        assert "{hotkey}" in template
        assert "Ready" in template

    def test_recording_tooltip_template(self):
        """RECORDING tooltip should say Recording."""
        from tray import _STATE_TOOLTIP_TEMPLATES

        assert "Recording" in _STATE_TOOLTIP_TEMPLATES[AppState.RECORDING]

    def test_processing_tooltip_template(self):
        """PROCESSING tooltip should say Processing."""
        from tray import _STATE_TOOLTIP_TEMPLATES

        assert "Processing" in _STATE_TOOLTIP_TEMPLATES[AppState.PROCESSING]

    def test_pasting_tooltip_template(self):
        """PASTING tooltip should say Pasting."""
        from tray import _STATE_TOOLTIP_TEMPLATES

        assert "Pasting" in _STATE_TOOLTIP_TEMPLATES[AppState.PASTING]


class TestTrayManagerGetTooltip:
    """Test the _get_tooltip method that resolves hotkey into templates."""

    def test_idle_tooltip_includes_configured_hotkey(self):
        """IDLE tooltip should include the configured hotkey label."""
        from tray import TrayManager

        tray = TrayManager(hotkey_label="Ctrl+Alt+R")
        tooltip = tray._get_tooltip(AppState.IDLE)

        assert "Ctrl+Alt+R" in tooltip
        assert "Ready" in tooltip

    def test_idle_tooltip_with_custom_hotkey(self):
        """IDLE tooltip should show whatever hotkey was configured."""
        from tray import TrayManager

        tray = TrayManager(hotkey_label="F9")
        tooltip = tray._get_tooltip(AppState.IDLE)

        assert "F9" in tooltip
        assert "Ready" in tooltip

    def test_recording_tooltip_does_not_include_hotkey(self):
        """RECORDING tooltip should not contain {hotkey} artifacts."""
        from tray import TrayManager

        tray = TrayManager(hotkey_label="ctrl+shift+v")
        tooltip = tray._get_tooltip(AppState.RECORDING)

        assert "Recording" in tooltip
        assert "{hotkey}" not in tooltip


class TestTrayStatusLabels:
    """Test tray menu status labels."""

    def test_status_labels_exist(self):
        """All states should have a status label."""
        from tray import _STATE_LABELS

        for state in AppState:
            assert state in _STATE_LABELS

    def test_idle_label(self):
        """IDLE label should say Status: Idle."""
        from tray import _STATE_LABELS

        assert _STATE_LABELS[AppState.IDLE] == "Status: Idle"

    def test_recording_label(self):
        """RECORDING label should say Status: Recording."""
        from tray import _STATE_LABELS

        assert _STATE_LABELS[AppState.RECORDING] == "Status: Recording"


class TestCreateIconImage:
    """Test icon image generation.

    v0.2.4: Icons use RGB mode (no transparency) with a solid dark background
    and a microphone silhouette drawn in the state color. This ensures
    visibility on both dark and light Windows 11 taskbars.
    """

    def test_creates_rgb_image(self):
        """Icon should be RGB format (no transparency) for Windows 11 compat."""
        from tray import _create_icon_image

        img = _create_icon_image((220, 220, 230))
        assert img.mode == "RGB"

    def test_creates_32x32_image(self):
        """Icon should be 32x32 pixels (standard Windows tray size)."""
        from tray import _create_icon_image, ICON_SIZE

        img = _create_icon_image((230, 50, 50))
        assert img.size == (ICON_SIZE, ICON_SIZE)
        assert ICON_SIZE == 32

    def test_different_colors_produce_different_images(self):
        """Different colors should produce visually different icons."""
        from tray import _create_icon_image

        idle = _create_icon_image((220, 220, 230))
        red = _create_icon_image((230, 50, 50))
        assert idle.tobytes() != red.tobytes()

    def test_icon_has_solid_background(self):
        """Icon should NOT have fully transparent background.

        The old icon used RGBA with (0,0,0,0) background which was invisible
        on Windows 11. The new icon uses RGB with a solid dark background.
        """
        from tray import _create_icon_image
        from icon_drawing import ICON_BG_COLOR as _ICON_BG_COLOR

        img = _create_icon_image((220, 220, 230))
        # Check that the top-left corner pixel matches the background color
        pixel = img.getpixel((0, 0))
        assert pixel == _ICON_BG_COLOR

    def test_icon_contains_foreground_pixels(self):
        """Icon should contain pixels matching the mic color (not all bg)."""
        from tray import _create_icon_image
        from icon_drawing import ICON_BG_COLOR as _ICON_BG_COLOR

        mic_color = (230, 50, 50)
        img = _create_icon_image(mic_color)
        # get_flattened_data() is the non-deprecated replacement for getdata()
        # in Pillow 14+. Use getdata() with fallback for compatibility.
        if hasattr(img, "get_flattened_data"):
            pixels = list(img.get_flattened_data())
        else:
            pixels = list(img.getdata())
        # At least some pixels should be the mic color
        assert mic_color in pixels, (
            "Icon should contain pixels drawn in the microphone color."
        )

    def test_default_color_parameter(self):
        """Calling _create_icon_image with no args should use default color."""
        from tray import _create_icon_image

        # Should not raise
        img = _create_icon_image()
        assert img.mode == "RGB"
        assert img.size == (32, 32)


class TestTrayManagerInit:
    """Test TrayManager initialization and hotkey_label parameter."""

    def test_default_hotkey_label(self):
        """TrayManager should default to 'Ctrl+Alt+R' hotkey label."""
        from tray import TrayManager

        tray = TrayManager()
        assert tray._hotkey_label == "Ctrl+Alt+R"

    def test_custom_hotkey_label(self):
        """TrayManager should accept a custom hotkey_label."""
        from tray import TrayManager

        tray = TrayManager(hotkey_label="F9")
        assert tray._hotkey_label == "F9"

    def test_hotkey_label_appears_in_idle_tooltip(self):
        """The configured hotkey label should appear in the IDLE tooltip."""
        from tray import TrayManager

        tray = TrayManager(hotkey_label="ctrl+alt+r")
        tooltip = tray._get_tooltip(AppState.IDLE)
        assert "ctrl+alt+r" in tooltip


class TestTrayManagerUpdateState:
    """Test the update_state method for dynamic icon changes."""

    def test_update_state_changes_internal_state(self):
        """update_state should track current state."""
        from tray import TrayManager

        tray = TrayManager()
        tray.update_state(AppState.RECORDING)
        assert tray._current_state == AppState.RECORDING

    def test_update_state_no_crash_when_not_running(self):
        """update_state should not crash before icon is started."""
        from tray import TrayManager

        tray = TrayManager()
        # Should not raise even though icon is not running
        tray.update_state(AppState.RECORDING)
        tray.update_state(AppState.PROCESSING)
        tray.update_state(AppState.IDLE)


class TestTrayManagerNotify:
    """Test the notify method for toast notifications."""

    def test_notify_no_crash_when_not_running(self):
        """notify should not crash when icon is not running."""
        from tray import TrayManager

        tray = TrayManager()
        # Should not raise
        tray.notify("Test Title", "Test message")

    def test_notify_calls_icon_notify_when_running(self):
        """notify should call pystray's Icon.notify when running."""
        from tray import TrayManager

        tray = TrayManager()
        tray._icon = MagicMock()
        tray._running = True

        tray.notify("Voice Paste", "Error occurred")
        tray._icon.notify.assert_called_once_with("Error occurred", "Voice Paste")


class TestTrayManagerMenu:
    """Test the tray context menu structure."""

    def test_menu_has_quit_option(self):
        """v0.2 menu should include Quit option."""
        from tray import TrayManager

        tray = TrayManager()
        menu = tray._build_menu()
        # pystray.Menu items are accessible; check we have at least 3 items
        # (hidden default, status, separator, quit)
        items = list(menu.items)
        assert len(items) >= 3

    def test_quit_callback_invoked(self):
        """Quit menu action should call the on_quit callback."""
        from tray import TrayManager

        quit_called = []
        tray = TrayManager(on_quit=lambda: quit_called.append(True))
        tray._handle_quit(MagicMock(), MagicMock())
        assert len(quit_called) == 1

    def test_hidden_default_menu_item_exists(self):
        """Menu should have a hidden default item to prevent empty window on tray click.

        This is the fix for the 'empty window on tray click' bug.
        The first menu item must have default=True and visible=False so
        pystray does not surface its internal Win32 message-only window
        when the user left-clicks or double-clicks the tray icon.
        """
        from tray import TrayManager
        import pystray

        tray = TrayManager()
        menu = tray._build_menu()
        items = list(menu.items)

        # The first item should be the hidden default action
        first_item = items[0]
        assert first_item.default is True, (
            "First menu item must have default=True to absorb left-click."
        )
        assert first_item.visible is False, (
            "First menu item must have visible=False to stay hidden."
        )

    def test_handle_default_action_is_noop(self):
        """_handle_default_action should not crash or change state (it is a no-op)."""
        from tray import TrayManager

        tray = TrayManager()
        # Should not raise -- this is intentionally a no-op
        tray._handle_default_action(MagicMock(), MagicMock())
        # State should still be IDLE
        assert tray._current_state == AppState.IDLE
