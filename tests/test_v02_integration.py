"""Integration tests for v0.2 features.

Validates the full v0.2 pipeline integration:
- Summarization in the pipeline (CloudLLMSummarizer integration)
- Audio cues during state transitions
- Tray icon state updates
- Clipboard backup/restore around paste
- Error notifications via toast
- Edge cases: empty summary, summarizer error, audio cues disabled
"""

import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call

from constants import APP_NAME, AppState


@pytest.fixture
def v02_app():
    """Create a fully mocked v0.2 VoicePasteApp for integration tests."""
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

        # Configure mocks
        app._recorder.start.return_value = True
        app._recorder.stop.return_value = b"fake-wav-data"
        app._recorder.is_recording = False
        type(app._recorder).is_recording = PropertyMock(return_value=False)

        app._stt.transcribe.return_value = "Also aehm das Meeting ist morgen."
        app._summarizer.summarize.return_value = "Das Meeting ist morgen."
        MockPaste.return_value = True
        MockClipBackup.return_value = "previous clipboard"

        # Store mock references for assertions
        app._mocks = {
            "clip_backup": MockClipBackup,
            "clip_restore": MockClipRestore,
            "paste": MockPaste,
            "start_cue": MockStartCue,
            "stop_cue": MockStopCue,
            "cancel_cue": MockCancelCue,
            "error_cue": MockErrorCue,
        }

        yield app


class TestV02HappyPath:
    """Test the full v0.2 happy path pipeline."""

    def test_full_pipeline_completes(self, v02_app):
        """Full v0.2 path: hotkey -> record -> hotkey -> transcribe -> summarize -> paste."""
        v02_app._on_hotkey()  # IDLE -> RECORDING
        assert v02_app.state == AppState.RECORDING

        v02_app._on_hotkey()  # RECORDING -> PROCESSING
        time.sleep(0.8)  # Wait for pipeline thread

        assert v02_app.state == AppState.IDLE

    def test_clipboard_backed_up_before_paste(self, v02_app):
        """US-0.2.5: Clipboard should be backed up before paste."""
        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._mocks["clip_backup"].assert_called_once()

    def test_clipboard_restored_after_paste(self, v02_app):
        """US-0.2.5: Clipboard should be restored after paste."""
        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._mocks["clip_restore"].assert_called_once_with("previous clipboard")

    def test_start_cue_played_on_recording(self, v02_app):
        """US-0.2.3: Audio cue should play when recording starts."""
        v02_app._on_hotkey()  # IDLE -> RECORDING
        v02_app._mocks["start_cue"].assert_called_once()

    def test_stop_cue_played_on_stop(self, v02_app):
        """US-0.2.3: Audio cue should play when recording stops."""
        v02_app._on_hotkey()  # IDLE -> RECORDING
        v02_app._on_hotkey()  # RECORDING -> PROCESSING
        v02_app._mocks["stop_cue"].assert_called_once()

    def test_tray_icon_updated_on_state_change(self, v02_app):
        """US-0.2.2: Tray icon should update on every state change."""
        v02_app._on_hotkey()  # IDLE -> RECORDING

        # TrayManager.update_state should have been called with RECORDING
        v02_app._tray_manager.update_state.assert_called_with(AppState.RECORDING)

    def test_summarizer_called_with_transcript(self, v02_app):
        """US-0.2.1: Summarizer should process the transcript."""
        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._summarizer.summarize.assert_called_once_with(
            "Also aehm das Meeting ist morgen."
        )


class TestV02ErrorHandling:
    """Test error handling and toast notifications in v0.2."""

    def test_stt_error_shows_toast(self, v02_app):
        """US-0.2.4: STT error should show toast notification."""
        from stt import STTError

        v02_app._stt.transcribe.side_effect = STTError("Auth failed")

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._tray_manager.notify.assert_called()
        notify_args = v02_app._tray_manager.notify.call_args
        assert APP_NAME in str(notify_args)

    def test_summarizer_error_shows_toast(self, v02_app):
        """US-0.2.4: Summarizer error should show toast notification."""
        from summarizer import SummarizerError

        v02_app._summarizer.summarize.side_effect = SummarizerError("Timeout")

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._tray_manager.notify.assert_called()

    def test_error_plays_error_cue(self, v02_app):
        """Errors should play the error audio cue."""
        from stt import STTError

        v02_app._stt.transcribe.side_effect = STTError("Timeout")

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._mocks["error_cue"].assert_called()

    def test_clipboard_restored_on_error(self, v02_app):
        """US-0.2.5: Clipboard should be restored even if pipeline errors."""
        from stt import STTError

        v02_app._stt.transcribe.side_effect = STTError("Fail")

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._mocks["clip_restore"].assert_called_once_with("previous clipboard")

    def test_returns_to_idle_on_error(self, v02_app):
        """State should return to IDLE after any error."""
        from stt import STTError

        v02_app._stt.transcribe.side_effect = STTError("Fail")

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        assert v02_app.state == AppState.IDLE

    def test_failed_recording_shows_microphone_error(self, v02_app):
        """US-0.2.4: Failed mic start should show toast about microphone."""
        v02_app._recorder.start.return_value = False
        v02_app._on_hotkey()

        v02_app._tray_manager.notify.assert_called()
        notify_args = v02_app._tray_manager.notify.call_args
        # Should mention microphone
        assert "microphone" in str(notify_args).lower() or "Microphone" in str(notify_args)


class TestV02EmptySummary:
    """Test handling of empty summarizer output (all filler words)."""

    def test_empty_summary_does_not_paste(self, v02_app):
        """If summary is empty (all filler), nothing should be pasted."""
        v02_app._summarizer.summarize.return_value = ""

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._mocks["paste"].assert_not_called()

    def test_empty_summary_shows_no_speech_toast(self, v02_app):
        """Empty summary should show 'No speech detected' notification."""
        v02_app._summarizer.summarize.return_value = "  "

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        v02_app._tray_manager.notify.assert_called()
        notify_args = str(v02_app._tray_manager.notify.call_args)
        assert "speech" in notify_args.lower() or "No speech" in notify_args

    def test_empty_summary_returns_to_idle(self, v02_app):
        """Empty summary should still return to IDLE."""
        v02_app._summarizer.summarize.return_value = ""

        v02_app._on_hotkey()
        v02_app._on_hotkey()
        time.sleep(0.8)

        assert v02_app.state == AppState.IDLE


class TestV02AudioCuesDisabled:
    """Test that audio cues respect the config setting."""

    @pytest.fixture
    def silent_app(self):
        """Create app with audio cues disabled."""
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.CloudWhisperSTT") as MockSTT, \
             patch("main.CloudLLMSummarizer") as MockCloudSummarizer, \
             patch("main.PassthroughSummarizer") as MockSummarizer, \
             patch("main.HotkeyManager") as MockHotkey, \
             patch("main.TrayManager") as MockTray, \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text") as MockPaste, \
             patch("main.play_recording_start_cue") as MockStartCue, \
             patch("main.play_recording_stop_cue") as MockStopCue, \
             patch("main.play_cancel_cue") as MockCancelCue, \
             patch("main.play_error_cue") as MockErrorCue:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                audio_cues_enabled=False,
            )

            app = VoicePasteApp(config)
            app._recorder.start.return_value = True
            app._recorder.stop.return_value = b"fake-wav-data"
            type(app._recorder).is_recording = PropertyMock(return_value=False)

            app._mocks = {
                "start_cue": MockStartCue,
                "stop_cue": MockStopCue,
                "cancel_cue": MockCancelCue,
                "error_cue": MockErrorCue,
            }

            yield app

    def test_no_start_cue_when_disabled(self, silent_app):
        """Audio cue should not play when disabled."""
        silent_app._on_hotkey()  # IDLE -> RECORDING
        silent_app._mocks["start_cue"].assert_not_called()

    def test_no_stop_cue_when_disabled(self, silent_app):
        """Stop cue should not play when disabled."""
        silent_app._on_hotkey()
        silent_app._on_hotkey()
        silent_app._mocks["stop_cue"].assert_not_called()

    def test_no_cancel_cue_when_disabled(self, silent_app):
        """Cancel cue should not play when disabled."""
        silent_app._on_hotkey()
        silent_app._on_cancel()
        silent_app._mocks["cancel_cue"].assert_not_called()


class TestV02SummarizationDisabled:
    """Test behavior when summarization is disabled in config."""

    @pytest.fixture
    def passthrough_app(self):
        """Create app with summarization disabled."""
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.CloudWhisperSTT") as MockSTT, \
             patch("main.CloudLLMSummarizer") as MockCloudSummarizer, \
             patch("main.PassthroughSummarizer") as MockSummarizer, \
             patch("main.HotkeyManager") as MockHotkey, \
             patch("main.TrayManager") as MockTray, \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text") as MockPaste, \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"):

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                summarization_enabled=False,
            )

            app = VoicePasteApp(config)
            app._recorder.start.return_value = True
            app._recorder.stop.return_value = b"fake-wav-data"
            type(app._recorder).is_recording = PropertyMock(return_value=False)
            app._stt.transcribe.return_value = "Raw transcript text"
            app._summarizer.summarize.return_value = "Raw transcript text"
            MockPaste.return_value = True

            app._mock_paste = MockPaste
            app._mock_cloud_summarizer = MockCloudSummarizer

            yield app

    def test_passthrough_summarizer_used(self, passthrough_app):
        """When summarization is disabled, PassthroughSummarizer should be used."""
        # The app should have instantiated PassthroughSummarizer, not CloudLLMSummarizer
        # CloudLLMSummarizer should NOT have been called
        passthrough_app._mock_cloud_summarizer.assert_not_called()

    def test_pipeline_still_works(self, passthrough_app):
        """Pipeline should still work with passthrough summarizer."""
        passthrough_app._on_hotkey()
        passthrough_app._on_hotkey()
        time.sleep(0.8)

        assert passthrough_app.state == AppState.IDLE
