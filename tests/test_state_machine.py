"""Tests for the application state machine.

Validates:
- US-0.1.1: Hotkey toggle recording (state transitions)
- US-0.2.6: Cancel recording via Escape
- State machine integrity (no invalid transitions)
- Thread safety of state transitions
"""

import threading
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from constants import AppState


class TestAppState:
    """Test the AppState enum."""

    def test_all_states_exist(self):
        """All expected states should be defined."""
        assert AppState.IDLE is not None
        assert AppState.RECORDING is not None
        assert AppState.PROCESSING is not None
        assert AppState.PASTING is not None

    def test_state_values(self):
        """States should have string values for logging."""
        assert AppState.IDLE.value == "idle"
        assert AppState.RECORDING.value == "recording"
        assert AppState.PROCESSING.value == "processing"
        assert AppState.PASTING.value == "pasting"


class TestVoicePasteAppStateMachine:
    """Test state machine transitions in VoicePasteApp."""

    @pytest.fixture
    def mock_app(self):
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
            )

            app = VoicePasteApp(config)

            # Configure mock recorder
            app._recorder.start.return_value = True
            app._recorder.stop.return_value = b"fake-wav-data"
            app._recorder.is_recording = False
            type(app._recorder).is_recording = PropertyMock(return_value=False)

            # Configure mock STT
            app._stt.transcribe.return_value = "Test transcript"

            # Configure mock summarizer (CloudLLMSummarizer when enabled)
            app._summarizer.summarize.return_value = "Test transcript"

            # Configure mock clipboard and paste
            MockClipBackup.return_value = None
            MockClipRestore.return_value = None
            MockPaste.return_value = True

            yield app

    def test_initial_state_is_idle(self, mock_app):
        """Application should start in IDLE state."""
        assert mock_app.state == AppState.IDLE

    def test_hotkey_idle_to_recording(self, mock_app):
        """US-0.1.1: Hotkey in IDLE starts recording."""
        mock_app._on_hotkey()
        assert mock_app.state == AppState.RECORDING

    def test_hotkey_recording_to_processing(self, mock_app):
        """US-0.1.1: Hotkey in RECORDING stops and starts processing."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        assert mock_app.state == AppState.RECORDING

        mock_app._on_hotkey()  # RECORDING -> PROCESSING

        # Give the pipeline thread time to complete
        time.sleep(0.5)

        # After pipeline completes, should return to IDLE
        assert mock_app.state == AppState.IDLE

    def test_hotkey_during_processing_is_ignored(self, mock_app):
        """Hotkey during PROCESSING should be ignored."""
        mock_app._set_state(AppState.PROCESSING)
        mock_app._on_hotkey()
        assert mock_app.state == AppState.PROCESSING

    def test_hotkey_during_pasting_is_ignored(self, mock_app):
        """Hotkey during PASTING should be ignored."""
        mock_app._set_state(AppState.PASTING)
        mock_app._on_hotkey()
        assert mock_app.state == AppState.PASTING

    def test_failed_recording_stays_idle(self, mock_app):
        """If recording fails to start, state stays IDLE."""
        mock_app._recorder.start.return_value = False
        mock_app._on_hotkey()
        assert mock_app.state == AppState.IDLE

    def test_empty_audio_returns_to_idle(self, mock_app):
        """If recording produces no audio, returns to IDLE."""
        mock_app._recorder.stop.return_value = None
        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_hotkey()  # RECORDING -> PROCESSING (no audio)
        # Should return to IDLE since no audio
        assert mock_app.state == AppState.IDLE

    def test_stt_error_returns_to_idle(self, mock_app):
        """If STT fails, application returns to IDLE."""
        from stt import STTError
        mock_app._stt.transcribe.side_effect = STTError("API error")

        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_hotkey()  # RECORDING -> PROCESSING

        # Wait for pipeline thread
        time.sleep(0.5)
        assert mock_app.state == AppState.IDLE

    def test_empty_transcript_returns_to_idle(self, mock_app):
        """If transcript is empty, returns to IDLE without pasting."""
        mock_app._stt.transcribe.return_value = ""

        mock_app._on_hotkey()  # IDLE -> RECORDING
        mock_app._on_hotkey()  # RECORDING -> PROCESSING

        # Wait for pipeline thread
        time.sleep(0.5)
        assert mock_app.state == AppState.IDLE

    def test_only_one_recording_at_a_time(self, mock_app):
        """US-0.1.1: Only one recording session can be active."""
        mock_app._on_hotkey()  # IDLE -> RECORDING
        assert mock_app.state == AppState.RECORDING

        # Pressing hotkey again should stop, not start a second
        mock_app._on_hotkey()  # RECORDING -> PROCESSING
        # State should be PROCESSING (or IDLE after pipeline completes)
        assert mock_app.state in (AppState.PROCESSING, AppState.PASTING, AppState.IDLE)
