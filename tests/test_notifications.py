"""Tests for the notifications module (audio cues and toast notifications).

Validates:
- US-0.2.3: Audio feedback cues
- Audio cue functions exist and are callable
- Background thread execution for non-blocking cues
- Toast notification logging
"""

import threading
import pytest
from unittest.mock import patch, MagicMock, call

from notifications import (
    play_recording_start_cue,
    play_recording_stop_cue,
    play_cancel_cue,
    play_error_cue,
    show_toast,
    _play_beep_sequence,
)


class TestAudioCueFunctions:
    """Test that all audio cue functions are callable and use correct frequencies."""

    def test_start_cue_callable(self):
        """play_recording_start_cue should be callable."""
        assert callable(play_recording_start_cue)

    def test_stop_cue_callable(self):
        """play_recording_stop_cue should be callable."""
        assert callable(play_recording_stop_cue)

    def test_cancel_cue_callable(self):
        """play_cancel_cue should be callable."""
        assert callable(play_cancel_cue)

    def test_error_cue_callable(self):
        """play_error_cue should be callable."""
        assert callable(play_error_cue)


class TestAudioCueExecution:
    """Test that audio cues execute without errors (mocked play_beep)."""

    @patch("notifications.play_beep")
    def test_start_cue_plays_rising_tones(self, mock_beep):
        """Recording start cue should play a rising two-tone."""
        play_recording_start_cue()
        # Wait briefly for the daemon thread
        import time
        time.sleep(0.2)

        # Should have been called with two frequencies (rising: 440, 880)
        assert mock_beep.call_count == 2
        calls = mock_beep.call_args_list
        # First call frequency should be lower than second
        assert calls[0].args[0] < calls[1].args[0]

    @patch("notifications.play_beep")
    def test_stop_cue_plays_falling_tones(self, mock_beep):
        """Recording stop cue should play a falling two-tone."""
        play_recording_stop_cue()
        import time
        time.sleep(0.2)

        assert mock_beep.call_count == 2
        calls = mock_beep.call_args_list
        # First call frequency should be higher than second (falling)
        assert calls[0].args[0] > calls[1].args[0]

    @patch("notifications.play_beep")
    def test_cancel_cue_plays_two_low_beeps(self, mock_beep):
        """Cancel cue should play two low-frequency beeps."""
        play_cancel_cue()
        import time
        time.sleep(0.3)

        assert mock_beep.call_count == 2
        # Both should be the same (low) frequency
        calls = mock_beep.call_args_list
        assert calls[0].args[0] == calls[1].args[0]

    @patch("notifications.play_beep")
    def test_error_cue_plays_single_buzz(self, mock_beep):
        """Error cue should play a single low buzz."""
        play_error_cue()
        import time
        time.sleep(0.3)

        assert mock_beep.call_count == 1


class TestAudioCueThreading:
    """Test that audio cues run in background threads."""

    @patch("notifications.play_beep")
    def test_cue_runs_in_daemon_thread(self, mock_beep):
        """Audio cues should not block the calling thread."""
        # Make play_beep slow to verify non-blocking
        import time

        def slow_beep(freq, duration):
            time.sleep(0.5)

        mock_beep.side_effect = slow_beep

        start_time = time.monotonic()
        play_recording_start_cue()
        elapsed = time.monotonic() - start_time

        # Should return almost immediately (< 100ms) since beep runs in thread
        assert elapsed < 0.1

    @patch("notifications.play_beep")
    def test_cue_does_not_crash_on_beep_failure(self, mock_beep):
        """Audio cue should handle play_beep failures gracefully."""
        mock_beep.side_effect = RuntimeError("No speaker")

        # Should not raise
        play_recording_start_cue()
        import time
        time.sleep(0.2)


class TestShowToast:
    """Test the show_toast function."""

    def test_show_toast_callable(self):
        """show_toast should be callable."""
        assert callable(show_toast)

    def test_show_toast_logs_message(self, caplog):
        """show_toast should log the notification title and message."""
        import logging
        with caplog.at_level(logging.INFO):
            show_toast("Voice Paste", "Network error. Check connection.")

        assert "Voice Paste" in caplog.text
        assert "Network error" in caplog.text
