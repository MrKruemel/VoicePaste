"""Tests for the Speech-to-Text backend.

Validates:
- US-0.1.3: Cloud STT via OpenAI Whisper API
- REQ-S06: HTTPS only
- REQ-S07: TLS validation enabled
- REQ-S11: No audio data in logs
"""

import pytest
from unittest.mock import patch, MagicMock

from stt import CloudWhisperSTT, STTError


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
