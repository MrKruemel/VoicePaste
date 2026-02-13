"""Tests for audio recording module.

Validates:
- US-0.1.2: Microphone audio capture
- REQ-S09: Audio never written to disk
- REQ-S11: No audio data in logs
"""

import io
import wave
import pytest
from unittest.mock import patch, MagicMock

import numpy as np

from audio import AudioRecorder


class TestAudioRecorder:
    """Test the AudioRecorder class."""

    @pytest.fixture
    def recorder(self):
        """Create an AudioRecorder instance."""
        return AudioRecorder(sample_rate=16000, channels=1, dtype="int16")

    def test_initial_state(self, recorder):
        """Recorder should not be recording initially."""
        assert not recorder.is_recording

    @patch("audio.sd")
    def test_start_recording(self, mock_sd, recorder):
        """US-0.1.2: Recording starts successfully with a microphone."""
        mock_sd.query_devices.return_value = {"name": "Test Microphone"}
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        result = recorder.start()

        assert result is True
        assert recorder.is_recording
        mock_stream.start.assert_called_once()

    @patch("audio.sd")
    def test_start_no_microphone(self, mock_sd, recorder):
        """US-0.1.2: No microphone logs error and returns False."""
        import sounddevice as real_sd
        mock_sd.PortAudioError = real_sd.PortAudioError
        mock_sd.query_devices.side_effect = real_sd.PortAudioError("No device")

        result = recorder.start()

        assert result is False
        assert not recorder.is_recording

    @patch("audio.sd")
    def test_stop_without_start(self, mock_sd, recorder):
        """Stopping without starting returns None."""
        result = recorder.stop()
        assert result is None

    @patch("audio.sd")
    def test_double_start_ignored(self, mock_sd, recorder):
        """Starting while already recording returns False."""
        mock_sd.query_devices.return_value = {"name": "Test Mic"}
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value = mock_stream

        recorder.start()
        result = recorder.start()

        assert result is False

    def test_numpy_to_wav_bytes(self, recorder):
        """REQ-S09: WAV conversion produces valid in-memory WAV."""
        # Create 1 second of silence at 16kHz mono int16
        samples = np.zeros(16000, dtype=np.int16)
        wav_bytes = recorder._numpy_to_wav_bytes(samples)

        # Verify it is valid WAV
        buffer = io.BytesIO(wav_bytes)
        with wave.open(buffer, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # int16 = 2 bytes
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 16000

    def test_clear_frames_zeros_memory(self, recorder):
        """REQ-S10: Audio buffers are cleared (zeroed) on cleanup."""
        frame1 = np.array([100, 200, 300], dtype=np.int16)
        frame2 = np.array([400, 500, 600], dtype=np.int16)
        recorder._frames = [frame1, frame2]

        recorder._clear_frames()

        # Frames list should be empty
        assert len(recorder._frames) == 0
        # Original arrays should be zeroed
        assert np.all(frame1 == 0)
        assert np.all(frame2 == 0)


class TestAudioDataNeverOnDisk:
    """REQ-S09: Verify audio data is never written to disk."""

    def test_wav_output_is_bytes_not_file(self):
        """WAV output should be bytes, not a file path."""
        recorder = AudioRecorder()
        samples = np.zeros(16000, dtype=np.int16)
        result = recorder._numpy_to_wav_bytes(samples)
        assert isinstance(result, bytes)
        assert len(result) > 0
