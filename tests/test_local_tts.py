"""Unit tests for local TTS via Piper ONNX (src/local_tts.py).

Tests cover:
- EspeakPhonemizer initialization and phonemization
- PiperLocalTTS model loading, synthesis pipeline, WAV output
- Error handling and edge cases
- phoneme_to_ids conversion

Most tests use mocked onnxruntime and espeakng-loader to avoid
requiring model files or native libraries in the test environment.
"""

import io
import json
import struct
import threading
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_piper_config() -> dict:
    """Create a minimal Piper voice config matching real model format."""
    return {
        "audio": {"sample_rate": 22050},
        "espeak": {"voice": "de"},
        "inference": {
            "noise_scale": 0.667,
            "length_scale": 1.0,
            "noise_w": 0.8,
        },
        "phoneme_id_map": {
            "_": [0],
            "^": [1],
            "$": [2],
            " ": [3],
            "h": [20],
            "a": [14],
            "l": [24],
            "o": [27],
            "v": [34],
            "e": [18],
            "t": [32],
            "\u02c8": [120],   # Primary stress
            "\u02cc": [121],   # Secondary stress
            "\u02d0": [122],   # Long vowel
            "\u025b": [61],    # Open-mid front unrounded (epsilon)
        },
    }


@pytest.fixture
def sample_config_path(tmp_path: Path, sample_piper_config: dict) -> Path:
    """Write sample Piper config to a temp file and return the path."""
    config_path = tmp_path / "test-voice.onnx.json"
    config_path.write_text(json.dumps(sample_piper_config), encoding="utf-8")
    return config_path


@pytest.fixture
def sample_onnx_path(tmp_path: Path) -> Path:
    """Create a dummy .onnx file in the temp directory."""
    onnx_path = tmp_path / "test-voice.onnx"
    # Write enough bytes to pass the size check (> 1 MB)
    onnx_path.write_bytes(b"\x00" * (1024 * 1024 + 1))
    return onnx_path


@pytest.fixture
def model_dir(tmp_path: Path, sample_config_path: Path, sample_onnx_path: Path) -> Path:
    """Return a tmp_path that contains both .onnx and .onnx.json files."""
    return tmp_path


# ---------------------------------------------------------------------------
# EspeakPhonemizer Tests
# ---------------------------------------------------------------------------

class TestEspeakPhonemizer:
    """Tests for the EspeakPhonemizer class."""

    def test_phonemize_empty_text(self) -> None:
        """phonemize() with empty text returns empty string."""
        from local_tts import EspeakPhonemizer

        phonemizer = EspeakPhonemizer()
        assert phonemizer.phonemize("") == ""
        assert phonemizer.phonemize("   ") == ""

    @patch("local_tts.is_espeakng_available", return_value=False)
    def test_espeakng_not_available(self, _mock: Any) -> None:
        """Verify is_espeakng_available returns False when not installed."""
        from local_tts import is_espeakng_available

        # Reset the cached value
        import local_tts
        local_tts._espeakng_available = None

        # This calls the mocked version
        result = is_espeakng_available()
        assert result is False

    def test_cleanup_resets_state(self) -> None:
        """cleanup() resets the phonemizer's internal state."""
        from local_tts import EspeakPhonemizer

        phonemizer = EspeakPhonemizer()
        phonemizer._initialized = True
        phonemizer._current_language = "de"
        phonemizer._lib = MagicMock()

        phonemizer.cleanup()

        assert phonemizer._initialized is False
        assert phonemizer._current_language is None
        assert phonemizer._lib is None


# ---------------------------------------------------------------------------
# PiperLocalTTS Tests
# ---------------------------------------------------------------------------

class TestPiperLocalTTS:
    """Tests for the PiperLocalTTS class."""

    def test_init_sets_attributes(self) -> None:
        """__init__ correctly sets voice_name and model_dir."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="de_DE-thorsten-medium")
        assert tts.voice_name == "de_DE-thorsten-medium"
        assert tts.is_model_loaded is False

    def test_init_with_explicit_model_dir(self, tmp_path: Path) -> None:
        """__init__ with explicit model_dir stores the path."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(
            voice_name="test-voice",
            model_dir=tmp_path,
        )
        assert tts._model_dir == tmp_path

    def test_synthesize_empty_text_raises(self) -> None:
        """synthesize() with empty text raises TTSError."""
        from local_tts import PiperLocalTTS
        from tts import TTSError

        tts = PiperLocalTTS(voice_name="test-voice")
        with pytest.raises(TTSError, match="empty text"):
            tts.synthesize("")

    def test_synthesize_whitespace_raises(self) -> None:
        """synthesize() with whitespace-only text raises TTSError."""
        from local_tts import PiperLocalTTS
        from tts import TTSError

        tts = PiperLocalTTS(voice_name="test-voice")
        with pytest.raises(TTSError, match="empty text"):
            tts.synthesize("   ")

    def test_load_model_no_model_dir_raises(self) -> None:
        """load_model() raises TTSError when model directory is not found."""
        from local_tts import PiperLocalTTS
        from tts import TTSError

        tts = PiperLocalTTS(
            voice_name="nonexistent-voice",
            model_dir=Path("/nonexistent/path"),
        )
        with pytest.raises(TTSError, match="not downloaded"):
            tts.load_model()

    def test_load_model_no_onnx_file_raises(
        self, tmp_path: Path, sample_config_path: Path
    ) -> None:
        """load_model() raises TTSError when .onnx file is missing."""
        from local_tts import PiperLocalTTS
        from tts import TTSError

        tts = PiperLocalTTS(voice_name="test-voice", model_dir=tmp_path)
        # Config exists but no .onnx file
        with pytest.raises(TTSError, match="No .onnx model"):
            tts.load_model()

    def test_load_model_no_json_file_raises(
        self, tmp_path: Path, sample_onnx_path: Path
    ) -> None:
        """load_model() raises TTSError when .onnx.json is missing."""
        from local_tts import PiperLocalTTS
        from tts import TTSError

        tts = PiperLocalTTS(voice_name="test-voice", model_dir=tmp_path)
        # ONNX file exists but no .onnx.json
        with pytest.raises(TTSError, match="No .onnx.json config"):
            tts.load_model()

    @patch("local_tts.PiperLocalTTS._resolve_model_dir")
    def test_load_model_success(
        self,
        mock_resolve: MagicMock,
        model_dir: Path,
        sample_piper_config: dict,
    ) -> None:
        """load_model() successfully loads config and creates ONNX session."""
        from local_tts import PiperLocalTTS

        mock_resolve.return_value = model_dir

        # Mock onnxruntime
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input"
        mock_session.get_inputs.return_value = [
            mock_input,
            MagicMock(name="input_lengths"),
            MagicMock(name="scales"),
        ]
        # Fix the name attribute for MagicMock items
        mock_session.get_inputs.return_value[1].name = "input_lengths"
        mock_session.get_inputs.return_value[2].name = "scales"

        with patch("onnxruntime.SessionOptions") as mock_opts, \
             patch("onnxruntime.InferenceSession", return_value=mock_session):
            tts = PiperLocalTTS(voice_name="test-voice")
            tts.load_model()

            assert tts.is_model_loaded is True
            assert tts._sample_rate == 22050
            assert len(tts._phoneme_id_map) == len(sample_piper_config["phoneme_id_map"])

    def test_unload_model(self) -> None:
        """unload_model() clears session and sets loaded=False."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._session = MagicMock()
        tts._config = {"test": True}
        tts._phoneme_id_map = {"a": [1]}
        tts._loaded = True

        tts.unload_model()

        assert tts.is_model_loaded is False
        assert tts._session is None
        assert tts._config is None
        assert tts._phoneme_id_map is None

    def test_unload_model_when_not_loaded(self) -> None:
        """unload_model() is a no-op when model is not loaded."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        # Should not raise
        tts.unload_model()
        assert tts.is_model_loaded is False


class TestPhonemeToIds:
    """Tests for the _phonemes_to_ids method."""

    def test_basic_conversion(self, sample_piper_config: dict) -> None:
        """Basic phoneme-to-ID conversion with BOS/EOS/PAD tokens."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._phoneme_id_map = sample_piper_config["phoneme_id_map"]

        # "halo" in IPA
        ids = tts._phonemes_to_ids("halo")

        # Expected: BOS, PAD, h, PAD, a, PAD, l, PAD, o, PAD, EOS
        assert ids[0] == 1   # BOS (^)
        assert ids[1] == 0   # PAD (_)
        assert ids[2] == 20  # h
        assert ids[3] == 0   # PAD
        assert ids[4] == 14  # a
        assert ids[5] == 0   # PAD
        assert ids[6] == 24  # l
        assert ids[7] == 0   # PAD
        assert ids[8] == 27  # o
        assert ids[9] == 0   # PAD
        assert ids[-1] == 2  # EOS ($)

    def test_unmapped_characters_skipped(
        self, sample_piper_config: dict
    ) -> None:
        """Characters not in phoneme_id_map are silently skipped."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._phoneme_id_map = sample_piper_config["phoneme_id_map"]

        # 'z' is not in our test config
        ids = tts._phonemes_to_ids("hzalo")

        # 'z' is skipped, result is same as "halo"
        ids_without_z = tts._phonemes_to_ids("halo")
        assert ids == ids_without_z

    def test_empty_phonemes(self, sample_piper_config: dict) -> None:
        """Empty phoneme string produces only BOS + PAD + EOS."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._phoneme_id_map = sample_piper_config["phoneme_id_map"]

        ids = tts._phonemes_to_ids("")
        # Should have BOS, PAD, EOS = [1, 0, 2]
        assert ids == [1, 0, 2]

    def test_stress_markers_included(
        self, sample_piper_config: dict
    ) -> None:
        """IPA stress markers are correctly mapped to IDs."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._phoneme_id_map = sample_piper_config["phoneme_id_map"]

        # Primary stress + 'a'
        ids = tts._phonemes_to_ids("\u02c8a")
        # BOS(1), PAD(0), stress(120), PAD(0), a(14), PAD(0), EOS(2)
        assert 120 in ids  # Primary stress marker


class TestPcmToWav:
    """Tests for the _pcm_to_wav static method."""

    def test_valid_wav_output(self) -> None:
        """_pcm_to_wav produces valid WAV bytes."""
        from local_tts import PiperLocalTTS

        # Create a simple sine wave
        sample_rate = 22050
        duration = 0.1  # 100ms
        t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
        pcm = np.sin(2 * np.pi * 440 * t).astype(np.float32)

        wav_bytes = PiperLocalTTS._pcm_to_wav(pcm, sample_rate)

        # Verify it's valid WAV
        assert wav_bytes[:4] == b"RIFF"
        assert wav_bytes[8:12] == b"WAVE"

        # Decode and verify properties
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # 16-bit
            assert wf.getframerate() == sample_rate
            assert wf.getnframes() == len(pcm)

    def test_clipping(self) -> None:
        """_pcm_to_wav clips values outside [-1, 1]."""
        from local_tts import PiperLocalTTS

        # PCM with values outside [-1, 1]
        pcm = np.array([2.0, -2.0, 0.5, -0.5], dtype=np.float32)
        wav_bytes = PiperLocalTTS._pcm_to_wav(pcm, 22050)

        # Decode and check values are clipped
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            raw = wf.readframes(4)
            samples = np.frombuffer(raw, dtype=np.int16)
            assert samples[0] == 32767   # Clipped from 2.0
            assert samples[1] == -32767  # Clipped from -2.0


class TestGetLanguage:
    """Tests for the _get_language method."""

    def test_from_config(self, sample_piper_config: dict) -> None:
        """Language is extracted from config espeak.voice."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._config = sample_piper_config
        assert tts._get_language() == "de"

    def test_from_voice_name(self) -> None:
        """Language is extracted from voice name prefix."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="en_US-lessac-medium")
        tts._config = {}  # No espeak.voice in config
        assert tts._get_language() == "en"

    def test_default_german(self) -> None:
        """Default language is German when no info available."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="custom-voice")
        tts._config = {}
        assert tts._get_language() == "de"


class TestInfer:
    """Tests for the _infer method."""

    def test_creates_correct_inputs(self, sample_piper_config: dict) -> None:
        """_infer creates correct numpy input arrays for the model."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._inference_params = sample_piper_config["inference"]
        tts._session_input_names = ["input", "input_lengths", "scales"]

        # Create a mock session
        mock_output = np.random.randn(1, 1, 4410).astype(np.float32)
        tts._session = MagicMock()
        tts._session.run.return_value = [mock_output]

        ids = [1, 0, 20, 0, 14, 0, 2]  # BOS, PAD, h, PAD, a, PAD, EOS
        result = tts._infer(ids)

        # Verify the session was called with correct inputs
        call_args = tts._session.run.call_args
        inputs = call_args[1] if call_args[1] else call_args[0][1]

        assert inputs["input"].shape == (1, len(ids))
        assert inputs["input_lengths"].tolist() == [len(ids)]
        assert len(inputs["scales"]) == 3
        assert "sid" not in inputs  # No speaker ID in this config

        # Verify output is squeezed to 1-D
        assert result.ndim == 1
        assert len(result) == 4410

    def test_with_speaker_id(self, sample_piper_config: dict) -> None:
        """_infer adds speaker ID when model supports it."""
        from local_tts import PiperLocalTTS

        tts = PiperLocalTTS(voice_name="test-voice")
        tts._inference_params = sample_piper_config["inference"]
        tts._session_input_names = ["input", "input_lengths", "scales", "sid"]

        mock_output = np.random.randn(1, 1, 4410).astype(np.float32)
        tts._session = MagicMock()
        tts._session.run.return_value = [mock_output]

        ids = [1, 0, 20, 0, 2]
        tts._infer(ids)

        call_args = tts._session.run.call_args
        inputs = call_args[1] if call_args[1] else call_args[0][1]
        assert "sid" in inputs
        assert inputs["sid"].tolist() == [0]


# ---------------------------------------------------------------------------
# TTS Model Manager Tests
# ---------------------------------------------------------------------------

class TestTtsModelManager:
    """Tests for tts_model_manager.py functions."""

    def test_get_tts_cache_dir_creates_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_tts_cache_dir creates the directory if it doesn't exist."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        from tts_model_manager import get_tts_cache_dir

        cache_dir = get_tts_cache_dir()
        assert cache_dir.exists()
        assert cache_dir == tmp_path / "VoicePaste" / "models" / "tts"

    def test_is_tts_model_valid_with_both_files(
        self, model_dir: Path
    ) -> None:
        """is_tts_model_valid returns True when both files exist."""
        from tts_model_manager import is_tts_model_valid

        assert is_tts_model_valid(model_dir) is True

    def test_is_tts_model_valid_missing_onnx(
        self, tmp_path: Path, sample_config_path: Path
    ) -> None:
        """is_tts_model_valid returns False when .onnx is missing."""
        from tts_model_manager import is_tts_model_valid

        assert is_tts_model_valid(tmp_path) is False

    def test_is_tts_model_valid_missing_json(
        self, tmp_path: Path, sample_onnx_path: Path
    ) -> None:
        """is_tts_model_valid returns False when .onnx.json is missing."""
        from tts_model_manager import is_tts_model_valid

        assert is_tts_model_valid(tmp_path) is False

    def test_is_tts_model_valid_small_onnx(self, tmp_path: Path) -> None:
        """is_tts_model_valid returns False for suspiciously small .onnx."""
        (tmp_path / "test.onnx").write_bytes(b"\x00" * 100)
        (tmp_path / "test.onnx.json").write_text("{}", encoding="utf-8")

        from tts_model_manager import is_tts_model_valid

        assert is_tts_model_valid(tmp_path) is False

    def test_is_tts_model_available_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """is_tts_model_available returns False for non-existent voice."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        from tts_model_manager import is_tts_model_available

        assert is_tts_model_available("nonexistent-voice") is False

    def test_delete_tts_model_nonexistent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete_tts_model returns True for non-existent voice."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        from tts_model_manager import delete_tts_model

        assert delete_tts_model("nonexistent-voice") is True

    def test_delete_tts_model_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete_tts_model removes the model directory."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        from tts_model_manager import delete_tts_model, get_tts_cache_dir

        # Create a fake model directory
        model_dir = get_tts_cache_dir() / "test-voice"
        model_dir.mkdir(parents=True)
        (model_dir / "test.onnx").write_bytes(b"\x00" * 100)

        assert delete_tts_model("test-voice") is True
        assert not model_dir.exists()

    def test_get_tts_model_size_mb(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_tts_model_size_mb returns correct size."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        from tts_model_manager import get_tts_model_size_mb, get_tts_cache_dir

        model_dir = get_tts_cache_dir() / "test-voice"
        model_dir.mkdir(parents=True)
        # Write 1 MB of data
        (model_dir / "test.onnx").write_bytes(b"\x00" * (1024 * 1024))

        size = get_tts_model_size_mb("test-voice")
        assert abs(size - 1.0) < 0.01

    def test_get_tts_model_size_mb_not_downloaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_tts_model_size_mb returns 0 for non-existent voice."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        from tts_model_manager import get_tts_model_size_mb

        assert get_tts_model_size_mb("nonexistent-voice") == 0.0


# ---------------------------------------------------------------------------
# Factory (create_tts_backend) Tests
# ---------------------------------------------------------------------------

class TestCreateTtsBackend:
    """Tests for the create_tts_backend factory function."""

    def test_elevenlabs_requires_api_key(self) -> None:
        """ElevenLabs provider returns None without API key."""
        from tts import create_tts_backend

        result = create_tts_backend(
            api_key="",
            provider="elevenlabs",
        )
        assert result is None

    def test_piper_no_api_key_needed(self) -> None:
        """Piper provider does not require an API key."""
        from tts import create_tts_backend

        # Even with empty API key, Piper should attempt to create
        # (may fail due to missing espeakng-loader in test env)
        with patch("local_tts.is_espeakng_available", return_value=True):
            with patch("local_tts.PiperLocalTTS") as mock_cls:
                mock_cls.return_value = MagicMock()
                result = create_tts_backend(
                    api_key="",
                    provider="piper",
                    local_voice="de_DE-thorsten-medium",
                )
                assert result is not None

    def test_piper_unavailable_returns_none(self) -> None:
        """Piper provider returns None when espeakng is not available."""
        from tts import create_tts_backend

        with patch("local_tts.is_espeakng_available", return_value=False):
            result = create_tts_backend(
                api_key="",
                provider="piper",
            )
            assert result is None

    def test_unknown_provider(self) -> None:
        """Unknown provider returns None."""
        from tts import create_tts_backend

        result = create_tts_backend(
            api_key="test-key",
            provider="unknown",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Constants Tests
# ---------------------------------------------------------------------------

class TestPiperConstants:
    """Tests for Piper-related constants."""

    def test_piper_in_tts_providers(self) -> None:
        """'piper' is in TTS_PROVIDERS tuple."""
        from constants import TTS_PROVIDERS

        assert "piper" in TTS_PROVIDERS
        assert "elevenlabs" in TTS_PROVIDERS

    def test_piper_voice_models_structure(self) -> None:
        """PIPER_VOICE_MODELS has the expected structure."""
        from constants import PIPER_VOICE_MODELS

        assert "de_DE-thorsten-medium" in PIPER_VOICE_MODELS

        voice = PIPER_VOICE_MODELS["de_DE-thorsten-medium"]
        assert "label" in voice
        assert "repo" in voice
        assert "files" in voice
        assert "download_mb" in voice
        assert "sample_rate" in voice

        # files should be a list (parsed from comma-separated string)
        assert isinstance(voice["files"], list)
        assert len(voice["files"]) == 2  # .onnx and .onnx.json

    def test_default_piper_voice_exists(self) -> None:
        """DEFAULT_PIPER_VOICE is in PIPER_VOICE_MODELS."""
        from constants import DEFAULT_PIPER_VOICE, PIPER_VOICE_MODELS

        assert DEFAULT_PIPER_VOICE in PIPER_VOICE_MODELS


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------

class TestConfigLocalTts:
    """Tests for config.py v0.7 fields."""

    def test_app_config_has_tts_local_voice(self) -> None:
        """AppConfig has tts_local_voice field with correct default."""
        from config import AppConfig
        from constants import DEFAULT_PIPER_VOICE

        cfg = AppConfig()
        assert cfg.tts_local_voice == DEFAULT_PIPER_VOICE

    def test_save_to_toml_includes_local_voice(self, tmp_path: Path) -> None:
        """save_to_toml() writes the local_voice field."""
        from config import AppConfig

        cfg = AppConfig(app_directory=tmp_path)
        cfg.tts_local_voice = "de_DE-thorsten-high"
        result = cfg.save_to_toml()
        assert result is True

        content = (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert 'local_voice = "de_DE-thorsten-high"' in content
