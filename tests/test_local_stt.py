"""Tests for the local STT module (faster-whisper backend).

Validates:
- is_faster_whisper_available() with mocked imports
- _wav_bytes_to_float32() conversion
- LocalWhisperSTT lazy loading behavior
- LocalWhisperSTT.transcribe() with mocked model
- Error handling (OOM, missing model, etc.)
- Model unloading
"""

import io
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

# We need to mock faster_whisper before importing local_stt
# because is_faster_whisper_available() caches its result.


def _make_wav_bytes(
    samples: list[int] | np.ndarray,
    sample_rate: int = 16000,
    channels: int = 1,
    sampwidth: int = 2,
) -> bytes:
    """Create WAV file bytes from int16 samples."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        if isinstance(samples, np.ndarray):
            wf.writeframes(samples.astype(np.int16).tobytes())
        else:
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


class TestWavBytesToFloat32:
    """Test WAV bytes to float32 conversion."""

    def setup_method(self):
        """Reset the availability cache before each test."""
        import local_stt
        local_stt._faster_whisper_available = None

    def test_silence_converts_to_zeros(self):
        """Silent audio (all zeros) should convert to zeros."""
        from local_stt import _wav_bytes_to_float32

        wav = _make_wav_bytes([0, 0, 0, 0])
        result = _wav_bytes_to_float32(wav)
        assert result.dtype == np.float32
        assert len(result) == 4
        np.testing.assert_array_equal(result, np.zeros(4, dtype=np.float32))

    def test_max_positive_converts_near_one(self):
        """Max int16 value should convert to ~1.0."""
        from local_stt import _wav_bytes_to_float32

        wav = _make_wav_bytes([32767])
        result = _wav_bytes_to_float32(wav)
        assert abs(result[0] - (32767 / 32768.0)) < 0.0001

    def test_max_negative_converts_near_minus_one(self):
        """Min int16 value should convert to -1.0."""
        from local_stt import _wav_bytes_to_float32

        wav = _make_wav_bytes([-32768])
        result = _wav_bytes_to_float32(wav)
        assert abs(result[0] - (-1.0)) < 0.0001

    def test_output_is_1d_array(self):
        """Output should be a 1-D array."""
        from local_stt import _wav_bytes_to_float32

        wav = _make_wav_bytes([100, -200, 300])
        result = _wav_bytes_to_float32(wav)
        assert result.ndim == 1
        assert len(result) == 3

    def test_invalid_wav_raises_stt_error(self):
        """Invalid WAV data should raise STTError."""
        from local_stt import _wav_bytes_to_float32
        from stt import STTError

        with pytest.raises(STTError, match="Failed to decode"):
            _wav_bytes_to_float32(b"not-a-wav-file")

    def test_stereo_downmix_to_mono(self):
        """Stereo audio should be downmixed to mono."""
        from local_stt import _wav_bytes_to_float32

        # Stereo: left=1000, right=3000 -> mono avg=2000
        wav = _make_wav_bytes(
            np.array([1000, 3000], dtype=np.int16),
            channels=2,
        )
        result = _wav_bytes_to_float32(wav)
        assert len(result) == 1
        expected = 2000 / 32768.0
        assert abs(result[0] - expected) < 0.01

    def test_preserves_sample_count(self):
        """Number of output samples should match input frames."""
        from local_stt import _wav_bytes_to_float32

        n_samples = 160
        samples = np.zeros(n_samples, dtype=np.int16)
        wav = _make_wav_bytes(samples)
        result = _wav_bytes_to_float32(wav)
        assert len(result) == n_samples


class TestIsFasterWhisperAvailable:
    """Test faster-whisper availability detection."""

    def setup_method(self):
        """Reset the availability cache before each test."""
        import local_stt
        local_stt._faster_whisper_available = None

    def test_returns_true_when_installed(self):
        """Returns True when faster_whisper can be imported."""
        import local_stt
        local_stt._faster_whisper_available = None

        mock_fw = MagicMock()
        mock_fw.__version__ = "1.0.0"
        with patch.dict("sys.modules", {"faster_whisper": mock_fw}):
            result = local_stt.is_faster_whisper_available()

        assert result is True

    def test_returns_false_when_not_installed(self):
        """Returns False when faster_whisper import fails."""
        import local_stt
        local_stt._faster_whisper_available = None

        with patch.dict("sys.modules", {"faster_whisper": None}):
            # Force re-evaluation by clearing cache
            with patch("builtins.__import__", side_effect=ImportError):
                result = local_stt.is_faster_whisper_available()

        assert result is False

    def test_caches_result(self):
        """Second call returns cached result without re-importing."""
        import local_stt
        local_stt._faster_whisper_available = True

        # Should return cached True without trying import
        result = local_stt.is_faster_whisper_available()
        assert result is True


class TestLocalWhisperSTT:
    """Test LocalWhisperSTT class."""

    def setup_method(self):
        """Reset the availability cache."""
        import local_stt
        local_stt._faster_whisper_available = True

    def test_init_does_not_load_model(self):
        """Model should not be loaded during __init__."""
        from local_stt import LocalWhisperSTT

        stt = LocalWhisperSTT(model_size="tiny")
        assert stt.is_model_loaded is False
        assert stt._model is None

    def test_init_raises_when_not_available(self):
        """Raises STTError when faster-whisper is not installed."""
        import local_stt
        local_stt._faster_whisper_available = False

        from stt import STTError

        with pytest.raises(STTError, match="not installed"):
            local_stt.LocalWhisperSTT(model_size="tiny")

    def test_load_model_calls_whisper_model(self):
        """load_model() should create a WhisperModel instance."""
        from local_stt import LocalWhisperSTT

        mock_model = MagicMock()
        mock_whisper_model_cls = MagicMock(return_value=mock_model)

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_whisper_model_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            stt.load_model()

        assert stt.is_model_loaded is True
        mock_whisper_model_cls.assert_called_once()

    def test_load_model_with_memory_error(self):
        """MemoryError during load raises STTError with helpful message."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=MemoryError("out of memory"))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="medium")
            with pytest.raises(STTError, match="Not enough memory"):
                stt.load_model()

    def test_load_model_with_file_not_found(self):
        """Missing model files raise STTError with download hint."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(
            side_effect=Exception("No such file or directory: /fake/model.bin")
        )

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base")
            with pytest.raises(STTError, match="not found"):
                stt.load_model()

    def test_unload_model(self):
        """unload_model() should free the model and reset state."""
        from local_stt import LocalWhisperSTT

        stt = LocalWhisperSTT(model_size="tiny")
        stt._model = MagicMock()
        stt._model_loaded = True

        stt.unload_model()

        assert stt._model is None
        assert stt._model_loaded is False
        assert stt.is_model_loaded is False

    def test_unload_model_when_not_loaded(self):
        """unload_model() is safe when model is not loaded."""
        from local_stt import LocalWhisperSTT

        stt = LocalWhisperSTT(model_size="tiny")
        stt.unload_model()  # Should not raise

    def test_transcribe_lazy_loads_model(self):
        """transcribe() should load model on first call."""
        from local_stt import LocalWhisperSTT

        mock_model = MagicMock()
        # Simulate segments generator
        mock_segment = SimpleNamespace(text=" Hallo Welt ")
        mock_info = SimpleNamespace(language="de", language_probability=0.95)
        mock_model.transcribe.return_value = ([mock_segment], mock_info)

        mock_cls = MagicMock(return_value=mock_model)

        wav = _make_wav_bytes([100, -200, 300, 400])

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            assert stt.is_model_loaded is False

            result = stt.transcribe(wav, language="de")

        assert stt.is_model_loaded is True
        assert result == "Hallo Welt"

    def test_transcribe_returns_joined_segments(self):
        """Multiple segments should be joined with spaces."""
        from local_stt import LocalWhisperSTT

        mock_model = MagicMock()
        segments = [
            SimpleNamespace(text=" Erster Satz. "),
            SimpleNamespace(text=" Zweiter Satz. "),
        ]
        mock_info = SimpleNamespace(language="de", language_probability=0.9)
        mock_model.transcribe.return_value = (segments, mock_info)

        mock_cls = MagicMock(return_value=mock_model)
        wav = _make_wav_bytes([100, -200])

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            result = stt.transcribe(wav)

        assert result == "Erster Satz. Zweiter Satz."

    def test_transcribe_uses_vad_filter(self):
        """transcribe() should enable VAD filter."""
        from local_stt import LocalWhisperSTT

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], SimpleNamespace(
            language="de", language_probability=0.9
        ))

        mock_cls = MagicMock(return_value=mock_model)
        wav = _make_wav_bytes([100])

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            stt.transcribe(wav)

        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs["vad_filter"] is True

    def test_transcribe_memory_error(self):
        """MemoryError during transcription raises STTError."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = MemoryError("OOM")

        mock_cls = MagicMock(return_value=mock_model)
        wav = _make_wav_bytes([100])

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            with pytest.raises(STTError, match="Out of memory"):
                stt.transcribe(wav)

    def test_transcribe_does_not_log_content(self):
        """Transcript content must not appear in log output (REQ-S24/S25)."""
        from local_stt import LocalWhisperSTT

        secret_text = "GeheimesPasswort123"
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            [SimpleNamespace(text=secret_text)],
            SimpleNamespace(language="de", language_probability=0.9),
        )

        mock_cls = MagicMock(return_value=mock_model)
        wav = _make_wav_bytes([100])

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")

            import logging

            with patch.object(logging.getLogger("local_stt"), "info") as mock_log:
                result = stt.transcribe(wav)

            # Verify transcript text was NOT logged
            for call_args in mock_log.call_args_list:
                log_msg = str(call_args)
                assert secret_text not in log_msg

        assert result == secret_text

    def test_empty_segments_returns_empty_string(self):
        """Empty segments list should return empty string."""
        from local_stt import LocalWhisperSTT

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            [],
            SimpleNamespace(language="de", language_probability=0.9),
        )

        mock_cls = MagicMock(return_value=mock_model)
        wav = _make_wav_bytes([100])

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            result = stt.transcribe(wav)

        assert result == ""
