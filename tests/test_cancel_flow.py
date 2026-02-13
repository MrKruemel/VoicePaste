"""Tests for the cancel recording flow (Escape key).

Validates:
- US-0.2.6: Cancel recording via Escape
- Cancel only works during RECORDING state
- Audio data is discarded on cancel
- Cancel returns to IDLE state
- Cancel hotkey registration/deregistration lifecycle
"""

import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from constants import AppState, CANCEL_HOTKEY


class TestCancelHotkeyRegistration:
    """Test cancel hotkey registration on HotkeyManager."""

    @patch("hotkey.kb")
    def test_register_cancel_adds_hotkey(self, mock_kb):
        """register_cancel should call kb.add_hotkey with Escape."""
        from hotkey import HotkeyManager

        mgr = HotkeyManager()
        callback = MagicMock()
        mgr.register_cancel(callback)

        mock_kb.add_hotkey.assert_called_with(
            CANCEL_HOTKEY, mgr._on_cancel, suppress=False
        )

    @patch("hotkey.kb")
    def test_unregister_cancel_removes_hotkey(self, mock_kb):
        """unregister_cancel should call kb.remove_hotkey with the handle from add_hotkey."""
        from hotkey import HotkeyManager

        mgr = HotkeyManager()
        mgr.register_cancel(MagicMock())

        # The handle stored is the return value of kb.add_hotkey()
        expected_handle = mock_kb.add_hotkey.return_value
        mgr.unregister_cancel()

        mock_kb.remove_hotkey.assert_called_with(expected_handle)

    @patch("hotkey.kb")
    def test_double_register_cancel_ignored(self, mock_kb):
        """Registering cancel twice should be idempotent."""
        from hotkey import HotkeyManager

        mgr = HotkeyManager()
        mgr.register_cancel(MagicMock())
        mgr.register_cancel(MagicMock())

        # add_hotkey should only be called once for cancel
        cancel_calls = [
            c for c in mock_kb.add_hotkey.call_args_list
            if c.args[0] == CANCEL_HOTKEY
        ]
        assert len(cancel_calls) == 1

    @patch("hotkey.kb")
    def test_unregister_cancel_safe_when_not_registered(self, mock_kb):
        """unregister_cancel should not crash when not registered."""
        from hotkey import HotkeyManager

        mgr = HotkeyManager()
        # Should not raise
        mgr.unregister_cancel()

    @patch("hotkey.kb")
    def test_unregister_all_also_unregisters_cancel(self, mock_kb):
        """unregister() should also unregister cancel hotkey."""
        from hotkey import HotkeyManager

        mgr = HotkeyManager()
        mgr.register(MagicMock())
        mgr.register_cancel(MagicMock())
        mgr.unregister()

        # remove_hotkey should have been called for both hotkeys
        remove_calls = [str(c) for c in mock_kb.remove_hotkey.call_args_list]
        assert len(mock_kb.remove_hotkey.call_args_list) >= 2


class TestCancelFlowStateMachine:
    """Test cancel flow integration with VoicePasteApp state machine."""

    @pytest.fixture
    def mock_app(self):
        """Create a VoicePasteApp with mocked dependencies."""
        mock_stt_instance = MagicMock()
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.create_stt_backend", return_value=mock_stt_instance) as MockFactory, \
             patch("main.CloudLLMSummarizer") as MockCloudSummarizer, \
             patch("main.PassthroughSummarizer") as MockSummarizer, \
             patch("main.HotkeyManager") as MockHotkey, \
             patch("main.TrayManager") as MockTray, \
             patch("main.clipboard_backup") as MockClipBackup, \
             patch("main.clipboard_restore") as MockClipRestore, \
             patch("main.paste_text") as MockPaste, \
             patch("main.play_recording_start_cue") as MockStartCue, \
             patch("main.play_recording_stop_cue") as MockStopCue, \
             patch("main.play_cancel_cue") as MockCancelCue, \
             patch("main.play_error_cue") as MockErrorCue:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                log_level="DEBUG",
            )

            app = VoicePasteApp(config)

            # Configure mock recorder
            app._recorder.start.return_value = True
            app._recorder.stop.return_value = b"fake-wav-data"
            app._recorder.is_recording = False
            type(app._recorder).is_recording = PropertyMock(return_value=False)

            # Configure mock STT
            app._stt.transcribe.return_value = "Test transcript"

            # Configure mock summarizer
            app._summarizer.summarize.return_value = "Test transcript"

            # Configure mock paste
            MockPaste.return_value = True

            # Store references to cue mocks
            app._test_cancel_cue = MockCancelCue
            app._test_start_cue = MockStartCue

            yield app

    def test_cancel_from_recording_returns_to_idle(self, mock_app):
        """US-0.2.6: Escape during RECORDING should return to IDLE."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        assert mock_app.state == AppState.RECORDING

        mock_app._on_cancel()  # Cancel
        assert mock_app.state == AppState.IDLE

    def test_cancel_stops_recording(self, mock_app):
        """Cancelling should stop the recorder."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_cancel()

        mock_app._recorder.stop.assert_called()

    def test_cancel_unregisters_cancel_hotkey(self, mock_app):
        """Cancelling should unregister the cancel hotkey."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_cancel()

        mock_app._hotkey_manager.unregister_cancel.assert_called()

    def test_cancel_does_not_paste(self, mock_app):
        """US-0.2.6: No transcription or paste after cancel."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_cancel()

        # STT should never have been called
        mock_app._stt.transcribe.assert_not_called()

    def test_cancel_ignored_in_idle(self, mock_app):
        """Cancel should be ignored when not recording."""
        assert mock_app.state == AppState.IDLE
        mock_app._on_cancel()
        assert mock_app.state == AppState.IDLE

    def test_cancel_ignored_in_processing(self, mock_app):
        """Cancel should be ignored during processing."""
        mock_app._set_state(AppState.PROCESSING)
        mock_app._on_cancel()
        assert mock_app.state == AppState.PROCESSING

    def test_cancel_plays_cancel_cue(self, mock_app):
        """US-0.2.6: A cancellation sound should play on cancel."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_cancel()

        # Cancel cue should have been called
        mock_app._test_cancel_cue.assert_called()

    def test_can_record_again_after_cancel(self, mock_app):
        """After cancel, user should be able to start a new recording."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_cancel()  # Back to IDLE
        assert mock_app.state == AppState.IDLE

        # Start new recording
        mock_app._on_hotkey()
        assert mock_app.state == AppState.RECORDING
