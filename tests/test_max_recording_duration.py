"""Tests for max recording duration (5-minute auto-stop).

Validates:
- AudioRecorder starts a Timer when recording starts.
- Timer calls _on_max_duration_reached after MAX_RECORDING_DURATION_SECONDS.
- auto_stopped property is set when timer fires.
- on_auto_stop callback is invoked.
- Timer is cancelled when stop() is called manually.
- auto_stopped flag is reset on next start().
- _on_auto_stop in VoicePasteApp shows notification and processes pipeline.
"""

import time
import threading
import pytest
from unittest.mock import patch, MagicMock, PropertyMock, call

from constants import MAX_RECORDING_DURATION_SECONDS, AppState


class TestAudioRecorderMaxDuration:
    """Test max recording duration timer in AudioRecorder."""

    @patch("audio.sd")
    def test_timer_is_started_on_recording_start(self, mock_sd):
        """A threading.Timer should be started when recording begins."""
        from audio import AudioRecorder

        mock_sd.query_devices.return_value = {"name": "Test Mic"}
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        recorder = AudioRecorder()
        recorder.start()

        try:
            assert recorder._max_duration_timer is not None, (
                "Max duration timer should be created on start."
            )
            assert recorder._max_duration_timer.is_alive(), (
                "Max duration timer should be running after start."
            )
        finally:
            # Clean up: stop recording to cancel the timer
            recorder._recording = False
            if recorder._max_duration_timer:
                recorder._max_duration_timer.cancel()

    @patch("audio.sd")
    def test_timer_is_daemon_thread(self, mock_sd):
        """Timer thread should be daemon so it does not block app shutdown."""
        from audio import AudioRecorder

        mock_sd.query_devices.return_value = {"name": "Test Mic"}
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        recorder = AudioRecorder()
        recorder.start()

        try:
            assert recorder._max_duration_timer.daemon is True
        finally:
            recorder._recording = False
            if recorder._max_duration_timer:
                recorder._max_duration_timer.cancel()

    @patch("audio.sd")
    def test_timer_cancelled_on_manual_stop(self, mock_sd):
        """Timer should be cancelled when stop() is called by the user."""
        from audio import AudioRecorder

        mock_sd.query_devices.return_value = {"name": "Test Mic"}
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        recorder = AudioRecorder()
        recorder.start()

        timer_ref = recorder._max_duration_timer
        assert timer_ref is not None

        recorder.stop()

        assert recorder._max_duration_timer is None, (
            "Timer reference should be cleared after stop."
        )
        # cancel() sets the internal Event so the timer will not fire its callback.
        # We check the finished event rather than is_alive() to avoid a race
        # condition where the thread has not yet fully terminated.
        assert timer_ref.finished.is_set(), (
            "Timer's finished event should be set after cancel."
        )

    @patch("audio.sd")
    def test_auto_stopped_flag_set_on_max_duration(self, mock_sd):
        """auto_stopped should be True after _on_max_duration_reached fires."""
        from audio import AudioRecorder

        recorder = AudioRecorder()
        assert recorder.auto_stopped is False

        recorder._on_max_duration_reached()

        assert recorder.auto_stopped is True

    @patch("audio.sd")
    def test_auto_stopped_flag_reset_on_start(self, mock_sd):
        """auto_stopped should be reset to False on next start()."""
        from audio import AudioRecorder

        mock_sd.query_devices.return_value = {"name": "Test Mic"}
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        recorder = AudioRecorder()
        # Simulate a previous auto-stop
        recorder._auto_stopped = True

        recorder.start()

        try:
            assert recorder.auto_stopped is False, (
                "auto_stopped should be reset when a new recording starts."
            )
        finally:
            recorder._recording = False
            if recorder._max_duration_timer:
                recorder._max_duration_timer.cancel()

    @patch("audio.sd")
    def test_on_auto_stop_callback_invoked(self, mock_sd):
        """on_auto_stop callback should be called when timer fires."""
        from audio import AudioRecorder

        callback = MagicMock()
        recorder = AudioRecorder(on_auto_stop=callback)

        recorder._on_max_duration_reached()

        callback.assert_called_once()

    @patch("audio.sd")
    def test_on_auto_stop_callback_not_called_if_none(self, mock_sd):
        """If no on_auto_stop callback is provided, timer should not crash."""
        from audio import AudioRecorder

        recorder = AudioRecorder(on_auto_stop=None)

        # Should not raise
        recorder._on_max_duration_reached()

        assert recorder.auto_stopped is True

    @patch("audio.sd")
    def test_on_auto_stop_callback_exception_is_caught(self, mock_sd):
        """If on_auto_stop callback raises, the exception should be caught."""
        from audio import AudioRecorder

        callback = MagicMock(side_effect=RuntimeError("callback error"))
        recorder = AudioRecorder(on_auto_stop=callback)

        # Should not raise despite callback error
        recorder._on_max_duration_reached()

        # auto_stopped should still be set
        assert recorder.auto_stopped is True
        callback.assert_called_once()

    @patch("audio.sd")
    def test_initial_auto_stopped_is_false(self, mock_sd):
        """auto_stopped should be False initially."""
        from audio import AudioRecorder

        recorder = AudioRecorder()
        assert recorder.auto_stopped is False

    def test_max_recording_duration_constant_is_300(self):
        """MAX_RECORDING_DURATION_SECONDS should be 300 (5 minutes)."""
        assert MAX_RECORDING_DURATION_SECONDS == 300

    @patch("audio.sd")
    def test_timer_uses_max_duration_constant(self, mock_sd):
        """Timer interval should match MAX_RECORDING_DURATION_SECONDS."""
        from audio import AudioRecorder

        mock_sd.query_devices.return_value = {"name": "Test Mic"}
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        recorder = AudioRecorder()
        recorder.start()

        try:
            assert recorder._max_duration_timer.interval == MAX_RECORDING_DURATION_SECONDS
        finally:
            recorder._recording = False
            if recorder._max_duration_timer:
                recorder._max_duration_timer.cancel()


class TestAutoStopInVoicePasteApp:
    """Test auto-stop handling in VoicePasteApp._on_auto_stop."""

    @pytest.fixture
    def auto_stop_app(self):
        """Create a VoicePasteApp with all external dependencies mocked."""
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.CloudWhisperSTT") as MockSTT, \
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
                summarization_enabled=True,
                audio_cues_enabled=True,
            )

            app = VoicePasteApp(config)

            # Configure mocks for a successful pipeline
            app._recorder.start.return_value = True
            app._recorder.stop.return_value = b"fake-wav-data"
            type(app._recorder).is_recording = PropertyMock(return_value=False)

            app._stt.transcribe.return_value = "Das Meeting ist morgen um zehn."
            app._summarizer.summarize.return_value = "Meeting morgen um 10 Uhr."
            MockPaste.return_value = True
            MockClipBackup.return_value = "previous clipboard"

            app._mocks = {
                "clip_backup": MockClipBackup,
                "clip_restore": MockClipRestore,
                "paste": MockPaste,
                "start_cue": MockStartCue,
                "stop_cue": MockStopCue,
                "cancel_cue": MockCancelCue,
                "error_cue": MockErrorCue,
                "tray": MockTray,
            }

            yield app

    def test_auto_stop_shows_notification(self, auto_stop_app):
        """Auto-stop should show a notification about reaching max duration."""
        # Put app into RECORDING state first
        auto_stop_app._on_hotkey()  # IDLE -> RECORDING
        assert auto_stop_app.state == AppState.RECORDING

        auto_stop_app._on_auto_stop()
        time.sleep(0.8)  # Wait for pipeline thread

        # Check that notification was shown about auto-stop
        notify_calls = auto_stop_app._tray_manager.notify.call_args_list
        auto_stop_messages = [
            str(c) for c in notify_calls if "auto-stop" in str(c).lower()
            or "5 minutes" in str(c)
        ]
        assert len(auto_stop_messages) > 0, (
            f"Expected a notification about auto-stop. "
            f"Actual notifications: {notify_calls}"
        )

    def test_auto_stop_triggers_processing_pipeline(self, auto_stop_app):
        """Auto-stop should trigger the STT/summarization/paste pipeline."""
        auto_stop_app._on_hotkey()  # IDLE -> RECORDING

        auto_stop_app._on_auto_stop()
        time.sleep(0.8)

        # STT should have been called
        auto_stop_app._stt.transcribe.assert_called_once()
        # Summarizer should have been called
        auto_stop_app._summarizer.summarize.assert_called_once()
        # Paste should have been called
        auto_stop_app._mocks["paste"].assert_called_once()

    def test_auto_stop_returns_to_idle(self, auto_stop_app):
        """After auto-stop pipeline completes, state should return to IDLE."""
        auto_stop_app._on_hotkey()  # IDLE -> RECORDING

        auto_stop_app._on_auto_stop()
        time.sleep(0.8)

        assert auto_stop_app.state == AppState.IDLE

    def test_auto_stop_ignored_when_not_recording(self, auto_stop_app):
        """Auto-stop should be ignored if state is not RECORDING."""
        assert auto_stop_app.state == AppState.IDLE

        auto_stop_app._on_auto_stop()

        # Should still be IDLE, no pipeline started
        assert auto_stop_app.state == AppState.IDLE
        auto_stop_app._stt.transcribe.assert_not_called()

    def test_auto_stop_ignored_during_processing(self, auto_stop_app):
        """Auto-stop should be ignored if state is PROCESSING."""
        auto_stop_app._set_state(AppState.PROCESSING)

        auto_stop_app._on_auto_stop()

        assert auto_stop_app.state == AppState.PROCESSING

    def test_auto_stop_plays_stop_cue(self, auto_stop_app):
        """Auto-stop should play the stop recording cue."""
        auto_stop_app._on_hotkey()  # IDLE -> RECORDING

        auto_stop_app._on_auto_stop()
        time.sleep(0.3)

        auto_stop_app._mocks["stop_cue"].assert_called()

    def test_auto_stop_unregisters_cancel_hotkey(self, auto_stop_app):
        """Auto-stop should unregister the Escape cancel hotkey."""
        auto_stop_app._on_hotkey()  # IDLE -> RECORDING

        auto_stop_app._on_auto_stop()
        time.sleep(0.3)

        auto_stop_app._hotkey_manager.unregister_cancel.assert_called()

    def test_auto_stop_preserves_clipboard(self, auto_stop_app):
        """Auto-stop pipeline should still backup and restore the clipboard."""
        auto_stop_app._on_hotkey()  # IDLE -> RECORDING

        auto_stop_app._on_auto_stop()
        time.sleep(0.8)

        auto_stop_app._mocks["clip_backup"].assert_called_once()
        auto_stop_app._mocks["clip_restore"].assert_called_once_with("previous clipboard")

    def test_on_auto_stop_callback_passed_to_recorder(self):
        """VoicePasteApp should pass _on_auto_stop as callback to AudioRecorder."""
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.CloudWhisperSTT"), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager"), \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"):

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(openai_api_key="sk-test1234567890")
            app = VoicePasteApp(config)

            # AudioRecorder should have been called with on_auto_stop=app._on_auto_stop
            MockRecorder.assert_called_once()
            call_kwargs = MockRecorder.call_args
            assert "on_auto_stop" in call_kwargs.kwargs or (
                len(call_kwargs.args) > 0
            ), "AudioRecorder should receive on_auto_stop callback."
            assert call_kwargs.kwargs.get("on_auto_stop") == app._on_auto_stop
