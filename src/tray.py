"""System tray icon management for the Voice-to-Summary Paste Tool.

Uses pystray for system tray integration and Pillow for icon generation.

v0.1: Single static icon with Quit menu.
v0.2+: Dynamic icon colors for state feedback, status display, toast notifications.
v0.2.4: Microphone silhouette icon with solid background for Windows 11 visibility.
v0.2.5: Critical fix -- explicitly set icon.visible = True in setup callback.
         pystray only auto-sets visible when NO setup callback is given.
         Also: 32x32 icon size, PID-unique icon name, enhanced diagnostics.
v0.3: Added "Settings..." menu item with dynamic enable/disable based on app state.
v0.4: Extracted icon drawing to shared icon_drawing module.
"""

import logging
import os
import threading
from typing import Callable, Optional

import pystray

from constants import APP_NAME, APP_VERSION, AppState
from icon_drawing import create_icon_image

logger = logging.getLogger(__name__)

# Icon dimensions -- 32x32 is the standard Windows system tray icon size.
ICON_SIZE = 32

# State-to-color mapping per UX-SPEC.md section 2.1
# Colors chosen for high contrast against the dark background.
# IDLE uses white (not grey) to be clearly visible on the dark background.
_STATE_COLORS: dict[AppState, tuple[int, int, int]] = {
    AppState.IDLE: (220, 220, 230),      # Light silver-white
    AppState.RECORDING: (230, 50, 50),    # Bright red
    AppState.PROCESSING: (240, 200, 40),  # Bright yellow/amber
    AppState.PASTING: (50, 200, 80),      # Bright green
    AppState.SPEAKING: (70, 130, 230),    # Blue — audio OUTPUT (v0.6)
}

# State-to-tooltip template mapping per UX-SPEC.md section 2.1
# The IDLE tooltip includes a {hotkey} placeholder, filled at runtime
# by TrayManager._get_tooltip() using the configured hotkey.
_STATE_TOOLTIP_TEMPLATES: dict[AppState, str] = {
    AppState.IDLE: f"{APP_NAME} - Ready ({{hotkey}})",
    AppState.RECORDING: f"{APP_NAME} - Recording...",
    AppState.PROCESSING: f"{APP_NAME} - Processing...",
    AppState.PASTING: f"{APP_NAME} - Pasting...",
    AppState.SPEAKING: f"{APP_NAME} - Speaking... (Escape to stop)",
}

# State-to-status label for menu display
_STATE_LABELS: dict[AppState, str] = {
    AppState.IDLE: "Status: Idle",
    AppState.RECORDING: "Status: Recording",
    AppState.PROCESSING: "Status: Processing",
    AppState.PASTING: "Status: Pasting",
    AppState.SPEAKING: "Status: Speaking",
}


def _create_icon_image(
    color: tuple[int, int, int] = (220, 220, 230),
) -> "Image.Image":
    """Create a 32x32 RGB tray icon via the shared icon_drawing module."""
    return create_icon_image(size=ICON_SIZE, color=color, mode="RGB")


class TrayManager:
    """Manages the system tray icon and context menu.

    v0.2: Dynamic icon colors reflecting application state, status menu item,
    and toast notification support via pystray's notify mechanism.
    v0.3: "Settings..." menu item with dynamic enable/disable.

    Attributes:
        on_quit: Callback invoked when user selects Quit.
        on_settings: Callback invoked when user selects Settings.
        hotkey_label: Display string for the configured hotkey combo.
    """

    def __init__(
        self,
        on_quit: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[], None]] = None,
        hotkey_label: str = "Ctrl+Alt+R",
        prompt_hotkey_label: str = "Ctrl+Alt+A",
        get_state: Optional[Callable[[], AppState]] = None,
        tts_enabled: bool = False,
        tts_hotkey_label: str = "Ctrl+Alt+T",
        tts_ask_hotkey_label: str = "Ctrl+Alt+Y",
    ) -> None:
        """Initialize the tray manager.

        Args:
            on_quit: Callback for the Quit menu action.
            on_settings: Callback for the Settings menu action (v0.3).
            hotkey_label: Human-readable hotkey string for summary mode.
            prompt_hotkey_label: Human-readable hotkey string for prompt mode.
            get_state: Callable returning current AppState for dynamic
                menu enablement (v0.3).
            tts_enabled: Whether TTS is enabled in the config. When True,
                TTS hotkeys are included in the startup notification.
            tts_hotkey_label: Human-readable hotkey string for TTS clipboard mode.
            tts_ask_hotkey_label: Human-readable hotkey string for TTS ask mode.
        """
        self._on_quit = on_quit
        self._on_settings = on_settings
        self._get_state = get_state
        self._hotkey_label = hotkey_label
        self._prompt_hotkey_label = prompt_hotkey_label
        self._tts_enabled = tts_enabled
        self._tts_hotkey_label = tts_hotkey_label
        self._tts_ask_hotkey_label = tts_ask_hotkey_label
        self._icon: Optional[pystray.Icon] = None
        self._running = False
        self._current_state = AppState.IDLE
        self._lock = threading.Lock()

    def _get_tooltip(self, state: AppState) -> str:
        """Get the tooltip text for a given state.

        Substitutes the configured hotkey label into the template.

        Args:
            state: The application state.

        Returns:
            Tooltip string for the tray icon.
        """
        template = _STATE_TOOLTIP_TEMPLATES.get(state, f"{APP_NAME} - Unknown")
        return template.format(hotkey=self._hotkey_label)

    def _get_status_text(self) -> str:
        """Get the current status label for the menu.

        Returns:
            Status text string for display in the context menu.
        """
        return _STATE_LABELS.get(self._current_state, "Status: Unknown")

    def _is_settings_enabled(self) -> bool:
        """Check if the Settings menu item should be enabled.

        Settings is disabled during RECORDING and PROCESSING states to
        prevent configuration changes while audio is being captured or
        an API call is in progress.

        Returns:
            True if Settings should be clickable.
        """
        if self._get_state is None:
            return True
        state = self._get_state()
        return state in (AppState.IDLE, AppState.PASTING, AppState.SPEAKING)

    def _handle_settings(
        self, icon: pystray.Icon, item: pystray.MenuItem
    ) -> None:
        """Handle the Settings menu action.

        Args:
            icon: The pystray Icon instance.
            item: The menu item that was clicked.
        """
        logger.info("Settings requested from tray menu.")
        if self._on_settings:
            self._on_settings()

    def _build_menu(self) -> pystray.Menu:
        """Build the system tray context menu.

        v0.2: Status display (greyed out) + Quit option.
        v0.3: Added "Settings..." menu item with dynamic enable/disable.
        The hidden default item prevents pystray from showing its
        internal Win32 window when the user left-clicks or double-clicks
        the tray icon (known pystray issue on Windows).

        Returns:
            pystray.Menu with menu items.
        """
        return pystray.Menu(
            # Hidden default action: absorbs left-click / double-click so
            # pystray does not surface its internal message-only window.
            pystray.MenuItem(
                "Open",
                self._handle_default_action,
                default=True,
                visible=False,
            ),
            pystray.MenuItem(
                lambda _: self._get_status_text(),
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            # v0.3: Settings dialog
            pystray.MenuItem(
                "Settings...",
                self._handle_settings,
                enabled=lambda _: self._is_settings_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._handle_quit),
        )

    def _handle_default_action(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """Handle the default tray icon action (left-click / double-click).

        This intentionally does nothing. It exists solely to prevent pystray
        from surfacing its internal Win32 message-only window, which appears
        as an empty/blank window on the user's screen.

        Args:
            icon: The pystray Icon instance.
            item: The menu item that was clicked.
        """
        logger.debug("Tray icon default action triggered (no-op).")

    def _handle_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """Handle the Quit menu action.

        Args:
            icon: The pystray Icon instance.
            item: The menu item that was clicked.
        """
        logger.info("Quit requested from tray menu.")
        if self._on_quit:
            self._on_quit()

    def update_state(self, state: AppState) -> None:
        """Update the tray icon appearance to reflect the current application state.

        Changes the icon color and tooltip text. Thread-safe.

        Args:
            state: The new application state.
        """
        with self._lock:
            self._current_state = state

        if not self._icon or not self._running:
            return

        color = _STATE_COLORS.get(state, (220, 220, 230))
        tooltip = self._get_tooltip(state)

        try:
            self._icon.icon = _create_icon_image(color)
            self._icon.title = tooltip
            # Force menu refresh so status text updates
            self._icon.update_menu()
            logger.debug(
                "Tray icon updated: state=%s, color=%s",
                state.value,
                color,
            )
        except Exception:
            logger.debug("Failed to update tray icon (may not be visible yet).")

    def notify(self, title: str, message: str) -> None:
        """Show a toast notification via the tray icon.

        Uses pystray's Icon.notify() which displays a Windows balloon/toast
        notification. Must not steal focus (UX Principle 3).

        Args:
            title: Notification title.
            message: Notification body text.
        """
        if not self._icon or not self._running:
            logger.warning("Cannot show notification: tray icon not running.")
            return

        try:
            self._icon.notify(message, title)
            logger.info("Toast notification: %s - %s", title, message)
        except Exception:
            logger.debug("Failed to show toast notification.")

    def run(self) -> None:
        """Start the system tray icon.

        This method blocks (runs the pystray event loop).
        It should be called from the main thread.

        A startup balloon notification is shown once the icon is visible,
        informing the user that the application is running and which hotkey
        to use. This is essential for --noconsole builds where the tray
        icon may be hidden in the Windows overflow area.

        IMPORTANT: pystray only auto-sets ``icon.visible = True`` when NO
        setup callback is provided. Since we provide ``_on_tray_ready``,
        we MUST explicitly set ``icon.visible = True`` in that callback.
        Without this, Shell_NotifyIcon(NIM_ADD) is never called and the
        icon is invisible. (Fixed in v0.2.5.)
        """
        idle_color = _STATE_COLORS[AppState.IDLE]
        icon_image = _create_icon_image(idle_color)
        tooltip = self._get_tooltip(AppState.IDLE)

        # Use a PID-unique icon name to avoid Windows shell caching issues.
        # If a previous instance crashed without proper cleanup, Windows may
        # cache the old (invisible) icon entry under the same name.
        icon_name = f"{APP_NAME}_{os.getpid()}"

        self._icon = pystray.Icon(
            name=icon_name,
            icon=icon_image,
            title=tooltip,
            menu=self._build_menu(),
        )

        self._running = True
        logger.info(
            "System tray icon starting: name=%r, hotkey=%s, icon_size=%d, "
            "pystray_version=%s.",
            icon_name,
            self._hotkey_label,
            ICON_SIZE,
            getattr(pystray, '__version__', 'unknown'),
        )

        # This blocks until icon.stop() is called.
        # The setup callback fires once pystray's message loop is ready.
        # NOTE: We MUST set icon.visible = True inside _on_tray_ready;
        # pystray does NOT do it for us when a setup callback is provided.
        logger.info("Entering pystray event loop (icon.run).")
        self._icon.run(setup=self._on_tray_ready)
        logger.info("Exited pystray event loop.")

    def _on_tray_ready(self, icon: pystray.Icon) -> None:
        """Callback fired by pystray once the message loop is ready.

        CRITICAL: pystray does NOT set icon.visible = True when a custom
        setup callback is provided. We must do it ourselves, or the icon
        will never be registered with the Windows shell (Shell_NotifyIcon
        NIM_ADD is never called) and the icon will be completely invisible.

        After making the icon visible, we show a startup balloon
        notification so the user knows the app is running, even if the
        icon is hidden in the Windows overflow area.

        Finally, we force a menu refresh to ensure Windows has the latest
        menu state cached.

        Args:
            icon: The pystray Icon instance (message loop ready, but NOT
                yet visible until we set icon.visible = True).
        """
        logger.info(
            "_on_tray_ready called. icon.visible BEFORE set: %s",
            icon.visible,
        )

        # --- Make the icon visible (the critical fix) ---
        try:
            icon.visible = True
            logger.info(
                "icon.visible set to True. icon.visible AFTER set: %s",
                icon.visible,
            )
        except Exception:
            logger.exception("FAILED to set icon.visible = True.")
            return

        # --- Force a menu refresh to ensure Windows has current state ---
        try:
            icon.update_menu()
            logger.debug("Menu refresh forced after icon became visible.")
        except Exception:
            logger.debug("Failed to force menu refresh (non-fatal).")

        # --- Show startup balloon notification ---
        try:
            notification_lines = [
                f"{self._hotkey_label}: Record + Summarize",
                f"{self._prompt_hotkey_label}: Record + Ask LLM",
            ]
            if self._tts_enabled:
                notification_lines.append(
                    f"{self._tts_hotkey_label}: Read Clipboard aloud"
                )
                notification_lines.append(
                    f"{self._tts_ask_hotkey_label}: Ask AI + TTS"
                )
            notification_lines.append("Right-click for options.")
            icon.notify(
                "\n".join(notification_lines),
                f"{APP_NAME} v{APP_VERSION} is running",
            )
            logger.info("Startup notification shown (tts_enabled=%s).", self._tts_enabled)
        except Exception:
            logger.debug("Failed to show startup notification.")

    def stop(self) -> None:
        """Stop the system tray icon."""
        if self._icon and self._running:
            logger.info("Stopping system tray icon.")
            self._icon.stop()
            self._running = False

    @property
    def is_running(self) -> bool:
        """Whether the tray icon is currently running."""
        return self._running
