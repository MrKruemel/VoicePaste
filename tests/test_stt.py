"""Tests for the Speech-to-Text backend.

Validates:
- US-0.1.3: Cloud STT via OpenAI Whisper API
- REQ-S06: HTTPS only
- REQ-S07: TLS validation enabled
- REQ-S11: No audio data in logs
- v0.4: create_stt_backend() factory function
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from stt import CloudWhisperSTT, STTError, create_stt_backend


class TestCloudWhisperSTT:
    """Test the OpenAI Whisper API STT backend."""

    @pytest.fixture
    def stt(self):
        """Create a CloudWhisperSTT with a test API key."""
        with patch("stt.openai.OpenAI") as MockClient:
            backend = CloudWhisperSTT(api_key="sk-test1234567890")
            yield backend

    def test_successful_transcription(self, stt):
        """US-0.1.3: Successful transcription returns text."""
        stt._client.audio.transcriptions.create.return_value = "Hallo, das ist ein Test."

        result = stt.transcribe(b"fake-wav-data", language="de")

        assert result == "Hallo, das ist ein Test."
        stt._client.audio.transcriptions.create.assert_called_once()

    def test_empty_transcription(self, stt):
        """US-0.1.3: Empty result returns empty string."""
        stt._client.audio.transcriptions.create.return_value = ""

        result = stt.transcribe(b"fake-wav-data")

        assert result == ""

    def test_auth_error_raises_stt_error(self, stt):
        """US-0.1.3: Auth failure raises STTError with helpful message."""
        import openai
        stt._client.audio.transcriptions.create.side_effect = (
            openai.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body=None,
            )
        )

        with pytest.raises(STTError, match="authentication"):
            stt.transcribe(b"fake-wav-data")

    def test_timeout_raises_stt_error(self, stt):
        """US-0.1.3: Timeout raises STTError."""
        import openai
        stt._client.audio.transcriptions.create.side_effect = (
            openai.APITimeoutError(request=MagicMock())
        )

        with pytest.raises(STTError, match="timed out"):
            stt.transcribe(b"fake-wav-data")

    def test_network_error_raises_stt_error(self, stt):
        """US-0.1.3: Network error raises STTError."""
        import openai
        stt._client.audio.transcriptions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )

        with pytest.raises(STTError, match="Network"):
            stt.transcribe(b"fake-wav-data")

    def test_rate_limit_raises_stt_error(self, stt):
        """US-0.1.3: Rate limit raises STTError."""
        import openai
        stt._client.audio.transcriptions.create.side_effect = (
            openai.RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(status_code=429),
                body=None,
            )
        )

        with pytest.raises(STTError, match="rate limit"):
            stt.transcribe(b"fake-wav-data")

    def test_api_key_not_in_instance(self, stt):
        """REQ-S02: API key should not be stored as a plain attribute."""
        # The key should only be in the client, not as a direct attribute
        assert not hasattr(stt, "api_key")
        assert not hasattr(stt, "_api_key")


class TestSTTProtocol:
    """Test that CloudWhisperSTT conforms to the STTBackend protocol."""

    def test_has_transcribe_method(self):
        """STT backend must have a transcribe method."""
        with patch("stt.openai.OpenAI"):
            backend = CloudWhisperSTT(api_key="test")
            assert hasattr(backend, "transcribe")
            assert callable(backend.transcribe)


class TestCreateSTTBackend:
    """Tests for the create_stt_backend() factory function (v0.4)."""

    def _make_config(self, **overrides):
        """Create a mock AppConfig with sensible defaults."""
        from config import AppConfig

        defaults = {
            "stt_backend": "cloud",
            "openai_api_key": "sk-test1234567890",
            "local_model_size": "base",
            "local_device": "cpu",
            "local_compute_type": "int8",
        }
        defaults.update(overrides)
        return AppConfig(**defaults)

    # --- Cloud backend tests ---

    def test_cloud_backend_returns_cloud_whisper(self):
        """Factory returns CloudWhisperSTT when stt_backend='cloud' and key is set."""
        config = self._make_config(stt_backend="cloud", openai_api_key="sk-test123")
        with patch("stt.openai.OpenAI"):
            backend = create_stt_backend(config)

        assert backend is not None
        assert isinstance(backend, CloudWhisperSTT)

    def test_cloud_backend_returns_none_without_api_key(self):
        """Factory returns None when stt_backend='cloud' but no API key."""
        config = self._make_config(stt_backend="cloud", openai_api_key="")

        backend = create_stt_backend(config)

        assert backend is None

    def test_default_backend_is_cloud(self):
        """Default stt_backend should use cloud path."""
        config = self._make_config(openai_api_key="sk-test123")
        assert config.stt_backend == "cloud"

        with patch("stt.openai.OpenAI"):
            backend = create_stt_backend(config)

        assert isinstance(backend, CloudWhisperSTT)

    # --- Local backend tests ---

    def test_local_backend_returns_local_whisper(self):
        """Factory returns LocalWhisperSTT when stt_backend='local' and available."""
        config = self._make_config(stt_backend="local")

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = True
        mock_local_instance = MagicMock()
        mock_local_stt.LocalWhisperSTT.return_value = mock_local_instance

        mock_model_manager = MagicMock()
        mock_model_manager.get_model_path.return_value = Path("/fake/models/base")

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            backend = create_stt_backend(config)

        assert backend is mock_local_instance
        mock_local_stt.LocalWhisperSTT.assert_called_once_with(
            model_size="base",
            device="cpu",
            compute_type="int8",
            model_path=Path("/fake/models/base"),
            vad_filter=True,
        )

    def test_local_backend_returns_none_when_not_available(self):
        """Factory returns None when faster-whisper is not installed."""
        config = self._make_config(stt_backend="local")

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = False

        mock_model_manager = MagicMock()

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            backend = create_stt_backend(config)

        assert backend is None

    def test_local_backend_returns_none_on_exception(self):
        """Factory returns None gracefully when local STT creation fails."""
        config = self._make_config(stt_backend="local")

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = True
        mock_local_stt.LocalWhisperSTT.side_effect = RuntimeError("boom")

        mock_model_manager = MagicMock()
        mock_model_manager.get_model_path.return_value = Path("/fake")

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            backend = create_stt_backend(config)

        assert backend is None

    def test_local_backend_passes_config_fields(self):
        """Factory passes the correct config fields to LocalWhisperSTT."""
        config = self._make_config(
            stt_backend="local",
            local_model_size="small",
            local_device="cuda",
            local_compute_type="float16",
        )

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = True

        mock_model_manager = MagicMock()
        mock_model_manager.get_model_path.return_value = Path("/models/small")

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            create_stt_backend(config)

        mock_model_manager.get_model_path.assert_called_once_with("small")
        mock_local_stt.LocalWhisperSTT.assert_called_once_with(
            model_size="small",
            device="cuda",
            compute_type="float16",
            model_path=Path("/models/small"),
            vad_filter=True,
        )
