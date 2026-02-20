"""Tests for v0.2.1 startup UX improvements.

Validates:
- show_fatal_error() calls MessageBoxW with correct flags.
- show_fatal_error() swallows exceptions from MessageBoxW.
- enable_debug_console() allocates a console and redirects stdout/stderr.
- enable_debug_console() swallows exceptions from AllocConsole.
- --debug flag in sys.argv triggers enable_debug_console() in main().
- main() shows fatal error and exits on mutex/lock conflict.
- main() shows fatal error and exits on config failure.
- main() shows fatal error on RuntimeError from app.run().
- main() shows fatal error on unexpected Exception.
- TrayManager._on_tray_ready() shows startup balloon notification.
- TrayManager.run() passes setup callback to icon.run().
"""

import sys
import pytest
from unittest.mock import patch, MagicMock, call

from constants import APP_NAME, APP_VERSION


# =========================================================================
# _show_fatal_error
# =========================================================================

class TestShowFatalError:
    """Test the show_fatal_error() helper for MessageBox display."""

    @patch("platform_impl._windows.ctypes")
    def test_calls_message_box_with_correct_params(self, mock_ctypes):
        """Should call MessageBoxW(0, message, title, flags)."""
        from platform_impl._windows import show_fatal_error, _MB_OK, _MB_ICONERROR, _MB_TOPMOST

        show_fatal_error("Something went wrong", "Error Title")

        expected_flags = _MB_OK | _MB_ICONERROR | _MB_TOPMOST
        mock_ctypes.windll.user32.MessageBoxW.assert_called_once_with(
            0,
            "Something went wrong",
            "Error Title",
            expected_flags,
        )

    @patch("platform_impl._windows.ctypes")
    def test_default_title_is_app_name(self, mock_ctypes):
        """When no title is given, should default to 'Voice Paste'."""
        from platform_impl._windows import show_fatal_error

        show_fatal_error("Test message")

        call_args = mock_ctypes.windll.user32.MessageBoxW.call_args
        assert call_args[0][2] == "Voice Paste"

    @patch("platform_impl._windows.ctypes")
    def test_swallows_messagebox_exception(self, mock_ctypes):
        """If MessageBoxW raises, the exception should be silently swallowed."""
        from platform_impl._windows import show_fatal_error

        mock_ctypes.windll.user32.MessageBoxW.side_effect = OSError("No GUI")

        # Should not raise
        show_fatal_error("This should not crash")

    @patch("platform_impl._windows.ctypes")
    def test_flags_include_topmost(self, mock_ctypes):
        """Message box should be topmost (0x00040000) so it is visible."""
        from platform_impl._windows import show_fatal_error, _MB_TOPMOST

        show_fatal_error("Test")

        call_args = mock_ctypes.windll.user32.MessageBoxW.call_args
        actual_flags = call_args[0][3]
        assert actual_flags & _MB_TOPMOST, (
            f"Flags 0x{actual_flags:08X} should include MB_TOPMOST 0x{_MB_TOPMOST:08X}"
        )

    @patch("platform_impl._windows.ctypes")
    def test_flags_include_icon_error(self, mock_ctypes):
        """Message box should have the error icon (0x00000010)."""
        from platform_impl._windows import show_fatal_error, _MB_ICONERROR

        show_fatal_error("Test")

        call_args = mock_ctypes.windll.user32.MessageBoxW.call_args
        actual_flags = call_args[0][3]
        assert actual_flags & _MB_ICONERROR, (
            f"Flags 0x{actual_flags:08X} should include MB_ICONERROR 0x{_MB_ICONERROR:08X}"
        )


# =========================================================================
# _enable_debug_console
# =========================================================================

class TestEnableDebugConsole:
    """Test the enable_debug_console() helper for --debug flag."""

    @patch("builtins.open", create=True)
    @patch("platform_impl._windows.ctypes")
    def test_calls_alloc_console(self, mock_ctypes, mock_open):
        """Should call kernel32.AllocConsole() to create a console window."""
        from platform_impl._windows import enable_debug_console

        enable_debug_console()

        mock_ctypes.windll.kernel32.AllocConsole.assert_called_once()

    @patch("builtins.open", create=True)
    @patch("platform_impl._windows.ctypes")
    def test_reopens_stdout_and_stderr(self, mock_ctypes, mock_open):
        """After AllocConsole, should reopen stdout and stderr to CONOUT$."""
        from platform_impl._windows import enable_debug_console

        enable_debug_console()

        # Check that open("CONOUT$", ...) was called (for stdout and stderr)
        conout_calls = [
            c for c in mock_open.call_args_list
            if c[0][0] == "CONOUT$"
        ]
        assert len(conout_calls) == 2, (
            f"Expected 2 calls to open('CONOUT$', ...) for stdout and stderr, "
            f"got {len(conout_calls)}"
        )

    @patch("platform_impl._windows.ctypes")
    def test_swallows_alloc_console_exception(self, mock_ctypes):
        """If AllocConsole fails (e.g., console already attached), should not crash."""
        from platform_impl._windows import enable_debug_console

        mock_ctypes.windll.kernel32.AllocConsole.side_effect = OSError("Already attached")

        # Should not raise
        enable_debug_console()


# =========================================================================
# main() -- --debug flag
# =========================================================================

class TestMainDebugFlag:
    """Test that --debug flag in sys.argv triggers _enable_debug_console."""

    @patch("main.enable_debug_console")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.release_single_instance_lock")
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_debug_flag_triggers_enable_debug_console(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_release, mock_acquire, mock_enable_debug
    ):
        """When --debug is in sys.argv, _enable_debug_console should be called."""
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )

        with patch.object(sys, "argv", ["main.py", "--debug"]):
            from main import main
            main()

        mock_enable_debug.assert_called_once()

    @patch("main.enable_debug_console")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.release_single_instance_lock")
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_no_debug_flag_does_not_trigger_debug_console(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_release, mock_acquire, mock_enable_debug
    ):
        """Without --debug, _enable_debug_console should NOT be called."""
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )

        with patch.object(sys, "argv", ["main.py"]):
            from main import main
            main()

        mock_enable_debug.assert_not_called()


# =========================================================================
# main() -- mutex conflict
# =========================================================================

class TestMainMutexConflict:
    """Test that main() shows fatal error and exits on mutex conflict."""

    @patch("main.show_fatal_error")
    @patch("main.acquire_single_instance_lock", return_value=None)
    def test_mutex_conflict_calls_show_fatal_error(
        self, mock_acquire, mock_fatal
    ):
        """When mutex acquisition fails, _show_fatal_error should be called."""
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1
        mock_fatal.assert_called_once()

    @patch("main.show_fatal_error")
    @patch("main.acquire_single_instance_lock", return_value=None)
    def test_mutex_conflict_message_mentions_already_running(
        self, mock_acquire, mock_fatal
    ):
        """Mutex conflict message should mention the app is already running."""
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            with pytest.raises(SystemExit):
                main()

        message = mock_fatal.call_args[0][0]
        assert "already running" in message.lower(), (
            f"Message should mention 'already running'. Got: {message}"
        )

    @patch("main.show_fatal_error")
    @patch("main.acquire_single_instance_lock", return_value=None)
    def test_mutex_conflict_exits_with_code_1(
        self, mock_acquire, mock_fatal
    ):
        """Mutex conflict should exit with status code 1."""
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1


# =========================================================================
# main() -- config failure
# =========================================================================

class TestMainConfigFailure:
    """Test that main() handles load_config() returning None gracefully.

    v0.3+: Missing/invalid config is NO LONGER fatal. When load_config()
    returns None, main() falls back to a default AppConfig and continues
    so the user can configure via the Settings dialog.
    """

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config", return_value=None)
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_config_none_falls_back_to_defaults(
        self, MockApp, mock_setup_log, mock_load, mock_acquire,
        mock_release, mock_fatal
    ):
        """When load_config() returns None, main() should use default config."""
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            main()

        # App should still be created and run
        MockApp.assert_called_once()
        MockApp.return_value.run.assert_called_once()
        # Fatal error should NOT be shown
        mock_fatal.assert_not_called()

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config", return_value=None)
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_config_none_releases_mutex(
        self, MockApp, mock_setup_log, mock_load, mock_acquire,
        mock_release, mock_fatal
    ):
        """Mutex should be released after successful run with default config."""
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            main()

        mock_release.assert_called_once_with(12345)

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config", return_value=None)
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_config_none_still_calls_setup_logging(
        self, MockApp, mock_setup_log, mock_load, mock_acquire,
        mock_release, mock_fatal
    ):
        """setup_logging should be called even with default config."""
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            main()

        mock_setup_log.assert_called_once()

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config", return_value=None)
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_config_none_creates_default_appconfig(
        self, MockApp, mock_setup_log, mock_load, mock_acquire,
        mock_release, mock_fatal
    ):
        """Fallback config should be a default AppConfig instance."""
        from config import AppConfig
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            main()

        # The config passed to VoicePasteApp should be an AppConfig
        actual_config = MockApp.call_args[0][0]
        assert isinstance(actual_config, AppConfig)
        # It should have empty API key (default)
        assert actual_config.openai_api_key == ""

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config", return_value=None)
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_config_none_does_not_exit(
        self, MockApp, mock_setup_log, mock_load, mock_acquire,
        mock_release, mock_fatal
    ):
        """main() should NOT call sys.exit when config is None."""
        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            # Should NOT raise SystemExit
            main()


# =========================================================================
# main() -- RuntimeError from app.run()
# =========================================================================

class TestMainRuntimeError:
    """Test that main() shows fatal error on RuntimeError from app.run()."""

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_runtime_error_calls_show_fatal_error(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_acquire, mock_release, mock_fatal
    ):
        """RuntimeError from app.run() should be shown via _show_fatal_error."""
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )
        MockApp.return_value.run.side_effect = RuntimeError(
            "Could not register hotkey"
        )

        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1
        mock_fatal.assert_called_once()
        assert "Could not register hotkey" in mock_fatal.call_args[0][0]

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_runtime_error_releases_mutex(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_acquire, mock_release, mock_fatal
    ):
        """Mutex should be released after RuntimeError."""
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )
        MockApp.return_value.run.side_effect = RuntimeError("Hotkey error")

        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            with pytest.raises(SystemExit):
                main()

        mock_release.assert_called_once_with(12345)


# =========================================================================
# main() -- unexpected Exception catch-all
# =========================================================================

class TestMainUnexpectedException:
    """Test catch-all exception handler in main()."""

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_unexpected_exception_calls_show_fatal_error(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_acquire, mock_release, mock_fatal
    ):
        """Unexpected exceptions should be caught and shown via MessageBox."""
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )
        MockApp.return_value.run.side_effect = ValueError("Something unexpected")

        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1
        mock_fatal.assert_called_once()
        message = mock_fatal.call_args[0][0]
        assert "unexpected error" in message.lower()

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_unexpected_exception_includes_traceback_section(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_acquire, mock_release, mock_fatal
    ):
        """Fatal error message should include a traceback section.

        The traceback is truncated to 500 chars in the message, so the
        actual exception string may be cut off. We verify that the
        message contains the 'Error:' header and 'Traceback' keyword
        which proves the traceback is included.
        """
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )
        MockApp.return_value.run.side_effect = ValueError("Something broke")

        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            with pytest.raises(SystemExit):
                main()

        message = mock_fatal.call_args[0][0]
        assert "Error:" in message, (
            f"Message should contain 'Error:' section header. Got: {message}"
        )
        assert "Traceback" in message, (
            f"Message should contain 'Traceback' from the formatted exception. Got: {message}"
        )


# =========================================================================
# TrayManager._on_tray_ready -- startup balloon notification
# =========================================================================

class TestTrayStartupBalloon:
    """Test the startup balloon notification in TrayManager._on_tray_ready."""

    def test_on_tray_ready_calls_icon_notify(self):
        """_on_tray_ready should call icon.notify() with startup message."""
        from tray import TrayManager

        tray = TrayManager()
        mock_icon = MagicMock()

        tray._on_tray_ready(mock_icon)

        mock_icon.notify.assert_called_once()

    def test_on_tray_ready_message_mentions_hotkey(self):
        """Startup notification body should mention the configured hotkey."""
        from tray import TrayManager

        tray = TrayManager()
        mock_icon = MagicMock()

        tray._on_tray_ready(mock_icon)

        # icon.notify(message, title) -- message is first positional arg
        call_args = mock_icon.notify.call_args
        message = call_args[0][0]
        assert "Ctrl+Alt+R" in message, (
            f"Startup message should mention Ctrl+Alt+R. Got: {message}"
        )

    def test_on_tray_ready_title_includes_version(self):
        """Startup notification title should include app name and version."""
        from tray import TrayManager

        tray = TrayManager()
        mock_icon = MagicMock()

        tray._on_tray_ready(mock_icon)

        call_args = mock_icon.notify.call_args
        title = call_args[0][1]
        assert APP_NAME in title, (
            f"Title should include APP_NAME '{APP_NAME}'. Got: {title}"
        )
        assert APP_VERSION in title, (
            f"Title should include APP_VERSION '{APP_VERSION}'. Got: {title}"
        )

    def test_on_tray_ready_swallows_notify_exception(self):
        """If icon.notify() raises, _on_tray_ready should not crash."""
        from tray import TrayManager

        tray = TrayManager()
        mock_icon = MagicMock()
        mock_icon.notify.side_effect = RuntimeError("Notification failed")

        # Should not raise
        tray._on_tray_ready(mock_icon)

    def test_run_passes_setup_callback(self):
        """TrayManager.run() should pass _on_tray_ready as setup callback."""
        from tray import TrayManager

        tray = TrayManager()

        with patch("tray.pystray") as mock_pystray:
            mock_icon_instance = MagicMock()
            mock_pystray.Icon.return_value = mock_icon_instance

            tray.run()

            # icon.run(setup=...) should have been called
            mock_icon_instance.run.assert_called_once()
            call_kwargs = mock_icon_instance.run.call_args
            assert "setup" in call_kwargs.kwargs or (
                len(call_kwargs.args) > 0
            ), "icon.run() should receive a setup argument."

            # The setup argument should be the _on_tray_ready method
            setup_fn = call_kwargs.kwargs.get("setup", call_kwargs.args[0] if call_kwargs.args else None)
            assert setup_fn == tray._on_tray_ready, (
                "setup callback should be TrayManager._on_tray_ready"
            )


# =========================================================================
# main() -- happy path (normal startup, no errors)
# =========================================================================

class TestMainHappyPath:
    """Test that main() completes successfully under normal conditions."""

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_normal_startup_does_not_show_fatal_error(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_acquire, mock_release, mock_fatal
    ):
        """Under normal conditions, _show_fatal_error should not be called."""
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )

        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            main()

        mock_fatal.assert_not_called()

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_normal_startup_releases_mutex(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_acquire, mock_release, mock_fatal
    ):
        """Mutex should be released at the end of normal execution."""
        from config import AppConfig

        mock_load_config.return_value = AppConfig(
            openai_api_key="sk-test1234567890"
        )

        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            main()

        mock_release.assert_called_once_with(12345)

    @patch("main.show_fatal_error")
    @patch("main.release_single_instance_lock")
    @patch("main.acquire_single_instance_lock", return_value=12345)
    @patch("main.load_config")
    @patch("main.setup_logging")
    @patch("main.VoicePasteApp")
    def test_normal_startup_creates_app_and_runs(
        self, MockApp, mock_setup_log, mock_load_config,
        mock_acquire, mock_release, mock_fatal
    ):
        """main() should create VoicePasteApp and call .run()."""
        from config import AppConfig

        config = AppConfig(openai_api_key="sk-test1234567890")
        mock_load_config.return_value = config

        from main import main

        with patch.object(sys, "argv", ["main.py"]):
            main()

        MockApp.assert_called_once_with(config)
        MockApp.return_value.run.assert_called_once()
