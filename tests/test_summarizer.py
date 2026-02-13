"""Tests for the summarizer module.

Validates:
- US-0.2.1: Summarization via CloudLLMSummarizer
- PassthroughSummarizer behavior (v0.1 fallback)
- CloudLLMSummarizer error handling (auth, rate limit, timeout, network, generic)
- Empty/whitespace input handling
- Security: REQ-S24 (no transcript in logs)
"""

import logging
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import openai

from summarizer import (
    CloudLLMSummarizer,
    PassthroughSummarizer,
    SummarizerError,
)


class TestPassthroughSummarizer:
    """Test the v0.1 passthrough summarizer."""

    def test_returns_text_unchanged(self):
        """Passthrough should return input text exactly as-is."""
        summarizer = PassthroughSummarizer()
        result = summarizer.summarize("Hello world")
        assert result == "Hello world"

    def test_empty_string(self):
        """Passthrough should handle empty strings."""
        summarizer = PassthroughSummarizer()
        result = summarizer.summarize("")
        assert result == ""

    def test_german_text_unchanged(self):
        """Passthrough should not alter German text."""
        summarizer = PassthroughSummarizer()
        text = "Das Meeting findet morgen um zehn Uhr statt."
        result = summarizer.summarize(text)
        assert result == text


class TestCloudLLMSummarizer:
    """Test the v0.2 cloud LLM summarizer."""

    @pytest.fixture
    def mock_openai_client(self):
        """Create a CloudLLMSummarizer with a mocked OpenAI client."""
        with patch("summarizer.openai.OpenAI") as MockClient:
            summarizer = CloudLLMSummarizer(api_key="sk-test1234567890")

            # Configure mock response
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Bereinigter Text."

            summarizer._client.chat.completions.create.return_value = mock_response

            yield summarizer, mock_response

    def test_successful_summarization(self, mock_openai_client):
        """US-0.2.1: Summarization should return cleaned text."""
        summarizer, _ = mock_openai_client
        result = summarizer.summarize("Also aehm der Text ist halt so.")
        assert result == "Bereinigter Text."

    def test_calls_openai_with_correct_messages(self, mock_openai_client):
        """Verify the correct system and user messages are sent."""
        summarizer, _ = mock_openai_client
        summarizer.summarize("Test input")

        call_args = summarizer._client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Test input"

    def test_uses_correct_model(self, mock_openai_client):
        """Verify the correct model is used."""
        summarizer, _ = mock_openai_client
        summarizer.summarize("Test")

        call_args = summarizer._client.chat.completions.create.call_args
        assert call_args.kwargs["model"] == "gpt-4o-mini"

    def test_uses_correct_temperature(self, mock_openai_client):
        """Verify temperature is 0.3 as specified in PROMPTS.md."""
        summarizer, _ = mock_openai_client
        summarizer.summarize("Test")

        call_args = summarizer._client.chat.completions.create.call_args
        assert call_args.kwargs["temperature"] == 0.3

    def test_empty_input_returns_empty(self, mock_openai_client):
        """Empty input should return empty string without API call."""
        summarizer, _ = mock_openai_client
        result = summarizer.summarize("")
        assert result == ""
        summarizer._client.chat.completions.create.assert_not_called()

    def test_whitespace_only_returns_empty(self, mock_openai_client):
        """Whitespace-only input should return empty string without API call."""
        summarizer, _ = mock_openai_client
        result = summarizer.summarize("   \n  \t  ")
        assert result == ""
        summarizer._client.chat.completions.create.assert_not_called()

    def test_none_response_content(self, mock_openai_client):
        """Handle None content in API response gracefully."""
        summarizer, mock_response = mock_openai_client
        mock_response.choices[0].message.content = None

        result = summarizer.summarize("Test input")
        assert result == ""

    def test_strips_whitespace_from_response(self, mock_openai_client):
        """Response should have leading/trailing whitespace stripped."""
        summarizer, mock_response = mock_openai_client
        mock_response.choices[0].message.content = "  Bereinigter Text.  \n"

        result = summarizer.summarize("Test input")
        assert result == "Bereinigter Text."


class TestCloudLLMSummarizerErrors:
    """Test error handling for CloudLLMSummarizer."""

    @pytest.fixture
    def summarizer(self):
        """Create a summarizer with mocked client."""
        with patch("summarizer.openai.OpenAI"):
            s = CloudLLMSummarizer(api_key="sk-test1234567890")
            yield s

    def test_auth_error_raises_summarizer_error(self, summarizer):
        """401 auth error should raise SummarizerError."""
        summarizer._client.chat.completions.create.side_effect = (
            openai.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body=None,
            )
        )
        with pytest.raises(SummarizerError, match="authentication"):
            summarizer.summarize("Test")

    def test_rate_limit_raises_summarizer_error(self, summarizer):
        """429 rate limit should raise SummarizerError."""
        summarizer._client.chat.completions.create.side_effect = (
            openai.RateLimitError(
                message="Rate limited",
                response=MagicMock(status_code=429),
                body=None,
            )
        )
        with pytest.raises(SummarizerError, match="rate limit"):
            summarizer.summarize("Test")

    def test_timeout_raises_summarizer_error(self, summarizer):
        """Timeout should raise SummarizerError."""
        summarizer._client.chat.completions.create.side_effect = (
            openai.APITimeoutError(request=MagicMock())
        )
        with pytest.raises(SummarizerError, match="timed out"):
            summarizer.summarize("Test")

    def test_connection_error_raises_summarizer_error(self, summarizer):
        """Network connection error should raise SummarizerError."""
        summarizer._client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )
        with pytest.raises(SummarizerError, match="Network"):
            summarizer.summarize("Test")

    def test_generic_api_error_raises_summarizer_error(self, summarizer):
        """Generic API error should raise SummarizerError."""
        summarizer._client.chat.completions.create.side_effect = (
            openai.APIError(
                message="Server error",
                request=MagicMock(),
                body=None,
            )
        )
        with pytest.raises(SummarizerError, match="API error"):
            summarizer.summarize("Test")

    def test_unexpected_error_raises_summarizer_error(self, summarizer):
        """Unexpected exceptions should raise SummarizerError."""
        summarizer._client.chat.completions.create.side_effect = RuntimeError("Boom")
        with pytest.raises(SummarizerError, match="Unexpected"):
            summarizer.summarize("Test")


class TestSummarizerNoTranscriptInLogs:
    """REQ-S24: Verify transcript content is never logged."""

    def test_cloud_summarizer_does_not_log_content(self, caplog):
        """CloudLLMSummarizer must not log input or output text content."""
        with patch("summarizer.openai.OpenAI"):
            summarizer = CloudLLMSummarizer(api_key="sk-test1234567890")

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Geheimer Inhalt"
            summarizer._client.chat.completions.create.return_value = mock_response

            with caplog.at_level(logging.DEBUG):
                summarizer.summarize("Vertraulicher Text hier")

            log_output = caplog.text.lower()
            assert "vertraulicher text" not in log_output
            assert "geheimer inhalt" not in log_output


class TestSummarizerProtocol:
    """Verify summarizer implementations conform to the Protocol."""

    def test_passthrough_has_summarize(self):
        """PassthroughSummarizer should have summarize method."""
        s = PassthroughSummarizer()
        assert hasattr(s, "summarize")
        assert callable(s.summarize)

    def test_cloud_summarizer_has_summarize(self):
        """CloudLLMSummarizer should have summarize method."""
        with patch("summarizer.openai.OpenAI"):
            s = CloudLLMSummarizer(api_key="sk-test")
            assert hasattr(s, "summarize")
            assert callable(s.summarize)
