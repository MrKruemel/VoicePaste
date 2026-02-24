"""Tests for OpenAI TTS backend (src/tts.py::OpenAITTS)."""

from unittest.mock import MagicMock, patch

import pytest


class TestOpenAITTS:
    """Tests for the OpenAITTS class."""

    def _make_backend(self, **kwargs):
        """Create an OpenAITTS instance with mocked openai client."""
        defaults = {
            "api_key": "test-key",
            "voice": "coral",
            "model": "gpt-4o-mini-tts",
            "response_format": "mp3",
            "instructions": "",
        }
        defaults.update(kwargs)

        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            from tts import OpenAITTS
            backend = OpenAITTS(**defaults)
            backend._client = mock_client
            return backend, mock_client

    def test_synthesize_returns_audio_bytes(self):
        """Successful synthesis returns audio bytes."""
        backend, mock_client = self._make_backend()
        mock_response = MagicMock()
        mock_response.content = b"\xff\xfb\x90\x00" * 100  # fake MP3 data
        mock_client.audio.speech.create.return_value = mock_response

        result = backend.synthesize("Hello world")

        assert result == mock_response.content
        assert len(result) > 0
        mock_client.audio.speech.create.assert_called_once()

    def test_synthesize_passes_correct_params(self):
        """Synthesis passes model, voice, input, response_format to API."""
        backend, mock_client = self._make_backend(
            voice="marin", model="tts-1-hd", response_format="wav"
        )
        mock_response = MagicMock()
        mock_response.content = b"audio-data"
        mock_client.audio.speech.create.return_value = mock_response

        backend.synthesize("Test text")

        call_kwargs = mock_client.audio.speech.create.call_args
        assert call_kwargs.kwargs["model"] == "tts-1-hd"
        assert call_kwargs.kwargs["voice"] == "marin"
        assert call_kwargs.kwargs["input"] == "Test text"
        assert call_kwargs.kwargs["response_format"] == "wav"

    def test_synthesize_passes_instructions_for_gpt4o(self):
        """Instructions are passed when model is gpt-4o-mini-tts."""
        backend, mock_client = self._make_backend(
            model="gpt-4o-mini-tts",
            instructions="Speak cheerfully",
        )
        mock_response = MagicMock()
        mock_response.content = b"audio-data"
        mock_client.audio.speech.create.return_value = mock_response

        backend.synthesize("Hello")

        call_kwargs = mock_client.audio.speech.create.call_args.kwargs
        assert call_kwargs["instructions"] == "Speak cheerfully"

    def test_synthesize_omits_instructions_for_legacy_model(self):
        """Instructions are not passed for tts-1 model."""
        backend, mock_client = self._make_backend(
            model="tts-1",
            instructions="Speak cheerfully",
        )
        mock_response = MagicMock()
        mock_response.content = b"audio-data"
        mock_client.audio.speech.create.return_value = mock_response

        backend.synthesize("Hello")

        call_kwargs = mock_client.audio.speech.create.call_args.kwargs
        assert "instructions" not in call_kwargs

    def test_synthesize_omits_empty_instructions(self):
        """Empty instructions are not passed even for gpt-4o-mini-tts."""
        backend, mock_client = self._make_backend(
            model="gpt-4o-mini-tts",
            instructions="",
        )
        mock_response = MagicMock()
        mock_response.content = b"audio-data"
        mock_client.audio.speech.create.return_value = mock_response

        backend.synthesize("Hello")

        call_kwargs = mock_client.audio.speech.create.call_args.kwargs
        assert "instructions" not in call_kwargs

    def test_synthesize_auth_error_raises_tts_error(self):
        """401 Unauthorized raises TTSError with helpful message."""
        from tts import TTSError

        backend, mock_client = self._make_backend()
        mock_client.audio.speech.create.side_effect = Exception(
            "Error code: 401 - Unauthorized"
        )

        with pytest.raises(TTSError, match="API key is invalid"):
            backend.synthesize("Hello")

    def test_synthesize_rate_limit_raises_tts_error(self):
        """429 Rate limit raises TTSError with helpful message."""
        from tts import TTSError

        backend, mock_client = self._make_backend()
        mock_client.audio.speech.create.side_effect = Exception(
            "Error code: 429 - Rate limit exceeded"
        )

        with pytest.raises(TTSError, match="rate limit"):
            backend.synthesize("Hello")

    def test_synthesize_quota_error_raises_tts_error(self):
        """Quota/billing error raises TTSError with helpful message."""
        from tts import TTSError

        backend, mock_client = self._make_backend()
        mock_client.audio.speech.create.side_effect = Exception(
            "insufficient_quota"
        )

        with pytest.raises(TTSError, match="quota exceeded"):
            backend.synthesize("Hello")

    def test_synthesize_empty_response_raises_tts_error(self):
        """Empty audio content raises TTSError."""
        from tts import TTSError

        backend, mock_client = self._make_backend()
        mock_response = MagicMock()
        mock_response.content = b""
        mock_client.audio.speech.create.return_value = mock_response

        with pytest.raises(TTSError, match="empty audio"):
            backend.synthesize("Hello")

    def test_synthesize_generic_error_raises_tts_error(self):
        """Generic API error raises TTSError without leaking details."""
        from tts import TTSError

        backend, mock_client = self._make_backend()
        mock_client.audio.speech.create.side_effect = Exception(
            "Some internal server error with sensitive details"
        )

        with pytest.raises(TTSError, match="synthesis failed"):
            backend.synthesize("Hello")


class TestOpenAITTSFactory:
    """Tests for create_tts_backend with provider='openai'."""

    def test_openai_requires_api_key(self):
        """OpenAI provider returns None without API key."""
        from tts import create_tts_backend

        result = create_tts_backend(
            api_key="",
            provider="openai",
        )
        assert result is None

    def test_openai_creates_backend_with_key(self):
        """OpenAI provider creates OpenAITTS with valid API key."""
        from tts import OpenAITTS, create_tts_backend

        with patch("openai.OpenAI"):
            result = create_tts_backend(
                api_key="test-key",
                provider="openai",
            )
            assert isinstance(result, OpenAITTS)

    def test_openai_uses_custom_params(self):
        """OpenAI provider passes custom voice, model, format, instructions."""
        from tts import OpenAITTS, create_tts_backend

        with patch("openai.OpenAI"):
            result = create_tts_backend(
                api_key="test-key",
                provider="openai",
                openai_tts_voice="marin",
                openai_tts_model="tts-1-hd",
                openai_tts_format="wav",
                openai_tts_instructions="Speak softly",
            )
            assert isinstance(result, OpenAITTS)
            assert result.voice == "marin"
            assert result.model == "tts-1-hd"
            assert result.response_format == "wav"
            assert result.instructions == "Speak softly"

    def test_openai_uses_defaults_when_empty(self):
        """OpenAI provider uses default constants when params are empty."""
        from constants import (
            DEFAULT_OPENAI_TTS_FORMAT,
            DEFAULT_OPENAI_TTS_MODEL,
            DEFAULT_OPENAI_TTS_VOICE,
        )
        from tts import OpenAITTS, create_tts_backend

        with patch("openai.OpenAI"):
            result = create_tts_backend(
                api_key="test-key",
                provider="openai",
            )
            assert isinstance(result, OpenAITTS)
            assert result.voice == DEFAULT_OPENAI_TTS_VOICE
            assert result.model == DEFAULT_OPENAI_TTS_MODEL
            assert result.response_format == DEFAULT_OPENAI_TTS_FORMAT


class TestOpenAITTSConstants:
    """Tests for OpenAI TTS constants."""

    def test_openai_in_tts_providers(self):
        """'openai' is in TTS_PROVIDERS tuple."""
        from constants import TTS_PROVIDERS

        assert "openai" in TTS_PROVIDERS

    def test_openai_voice_presets_structure(self):
        """OPENAI_TTS_VOICE_PRESETS has expected structure."""
        from constants import OPENAI_TTS_VOICE_PRESETS

        assert "coral" in OPENAI_TTS_VOICE_PRESETS
        assert "marin" in OPENAI_TTS_VOICE_PRESETS
        assert "cedar" in OPENAI_TTS_VOICE_PRESETS

        for key, info in OPENAI_TTS_VOICE_PRESETS.items():
            assert "name" in info
            assert "description" in info

    def test_openai_models_structure(self):
        """OPENAI_TTS_MODELS has expected structure."""
        from constants import OPENAI_TTS_MODELS

        assert "gpt-4o-mini-tts" in OPENAI_TTS_MODELS
        assert "tts-1" in OPENAI_TTS_MODELS
        assert "tts-1-hd" in OPENAI_TTS_MODELS

        for key, info in OPENAI_TTS_MODELS.items():
            assert "label" in info

    def test_legacy_voices_subset(self):
        """Legacy voices are a subset of all voices."""
        from constants import OPENAI_TTS_LEGACY_VOICES, OPENAI_TTS_VOICE_PRESETS

        for voice in OPENAI_TTS_LEGACY_VOICES:
            assert voice in OPENAI_TTS_VOICE_PRESETS

    def test_legacy_voices_excludes_new_voices(self):
        """Legacy voices do not include gpt-4o-mini-tts-only voices."""
        from constants import OPENAI_TTS_LEGACY_VOICES

        assert "ballad" not in OPENAI_TTS_LEGACY_VOICES
        assert "verse" not in OPENAI_TTS_LEGACY_VOICES
        assert "marin" not in OPENAI_TTS_LEGACY_VOICES
        assert "cedar" not in OPENAI_TTS_LEGACY_VOICES
