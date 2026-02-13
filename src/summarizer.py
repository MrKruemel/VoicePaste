"""Summarizer backend abstraction and implementations.

v0.1 uses a passthrough summarizer (returns text unchanged).
v0.2+ adds a cloud LLM summarizer using OpenAI GPT-4o-mini.
v0.3: Added base_url support for OpenRouter and custom endpoints,
      and custom system prompt support.

REQ-S24: Never log transcript content.
"""

import logging
import time
from typing import Protocol

import openai

from constants import (
    API_INITIAL_BACKOFF_SECONDS,
    API_MAX_RETRIES,
    SUMMARIZE_MAX_TOKENS,
    SUMMARIZE_MODEL,
    SUMMARIZE_SYSTEM_PROMPT,
    SUMMARIZE_TEMPERATURE,
    SUMMARIZE_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class Summarizer(Protocol):
    """Protocol for text summarization backends.

    Implementations clean up or summarize transcribed text.
    """

    def summarize(
        self, text: str, language: str = "de", system_prompt: str | None = None
    ) -> str:
        """Summarize or clean up transcribed text.

        Args:
            text: Raw transcribed text.
            language: Language code (default 'de' for German).
            system_prompt: Optional override for the system prompt.
                If None, uses the default prompt configured at init time.

        Returns:
            Processed text string.
        """
        ...


class SummarizerError(Exception):
    """Raised when summarization fails."""

    pass


class PassthroughSummarizer:
    """No-op summarizer for v0.1.

    Returns the input text unchanged. This validates the pipeline
    architecture without requiring an LLM dependency.
    """

    def summarize(
        self, text: str, language: str = "de", system_prompt: str | None = None
    ) -> str:
        """Return text unchanged (passthrough).

        Args:
            text: Raw transcribed text.
            language: Language code (unused in passthrough).
            system_prompt: Ignored in passthrough mode.

        Returns:
            The same text, unchanged.
        """
        logger.debug("Passthrough summarizer: returning text unchanged.")
        return text


class CloudLLMSummarizer:
    """Cloud LLM summarizer using OpenAI-compatible APIs.

    Cleans up raw STT transcriptions by removing filler words,
    fixing grammar, and producing concise summaries.

    v0.3: Supports OpenRouter and custom endpoints via base_url parameter.
          Supports custom system prompts.

    REQ-S06: Uses HTTPS only (enforced by the openai library).
    REQ-S07: TLS validation is enabled by default.
    REQ-S24: Never logs transcript content.

    Attributes:
        model: Model identifier (e.g., "gpt-4o-mini", "openai/gpt-4o-mini").
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
    """

    def __init__(
        self,
        api_key: str,
        model: str = SUMMARIZE_MODEL,
        temperature: float = SUMMARIZE_TEMPERATURE,
        max_tokens: int = SUMMARIZE_MAX_TOKENS,
        timeout: int = SUMMARIZE_TIMEOUT_SECONDS,
        system_prompt: str = SUMMARIZE_SYSTEM_PROMPT,
        base_url: str | None = None,
    ) -> None:
        """Initialize the cloud LLM summarizer.

        Args:
            api_key: API key (REQ-S02: never hardcoded).
            model: Model identifier.
            temperature: Sampling temperature (0.0-1.0).
            max_tokens: Maximum output tokens.
            timeout: API call timeout in seconds.
            system_prompt: System prompt for the summarization task.
            base_url: Custom API base URL (None = OpenAI default).
                      Use "https://openrouter.ai/api/v1" for OpenRouter.
        """
        client_kwargs: dict = {
            "api_key": api_key,
            "timeout": timeout,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
            # OpenRouter recommends setting HTTP-Referer and X-Title headers
            if "openrouter.ai" in base_url:
                client_kwargs["default_headers"] = {
                    "HTTP-Referer": "https://github.com/voice-paste",
                    "X-Title": "Voice Paste",
                }

        self._client = openai.OpenAI(**client_kwargs)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt
        self._base_url = base_url or ""

    def summarize(
        self, text: str, language: str = "de", system_prompt: str | None = None
    ) -> str:
        """Summarize text using the OpenAI GPT API.

        Retries up to API_MAX_RETRIES times with exponential backoff for
        transient errors (connection errors, timeouts, rate limits).
        Auth errors and other permanent failures are raised immediately.

        Args:
            text: Raw transcribed text.
            language: Language code (unused; prompt handles language matching).
            system_prompt: Optional override for the system prompt.
                If None, uses the prompt configured at init time.

        Returns:
            Cleaned and summarized text.

        Raises:
            SummarizerError: If the API call fails after all retries or on
                a permanent error.
        """
        if not text or not text.strip():
            return ""

        active_prompt = system_prompt if system_prompt is not None else self._system_prompt

        # REQ-S24: Do not log transcript content, only metadata
        logger.info(
            "Sending text to summarizer (%d characters, model=%s)...",
            len(text),
            self._model,
        )

        last_exception: Exception | None = None

        for attempt in range(1, API_MAX_RETRIES + 2):  # 1 initial + up to 2 retries
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": active_prompt},
                        {"role": "user", "content": text},
                    ],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )

                result = response.choices[0].message.content
                if result is None:
                    result = ""
                result = result.strip()

                # REQ-S24: Only log metadata, not content
                logger.info(
                    "Summarization complete. Input: %d chars, output: %d chars "
                    "(%.0f%% compression).",
                    len(text),
                    len(result),
                    (1 - len(result) / max(len(text), 1)) * 100,
                )

                return result

            except openai.AuthenticationError as e:
                # For local services (Ollama), auth errors are transient
                # (e.g., Ollama not running yet). Retry instead of failing.
                if "localhost" in self._base_url or "127.0.0.1" in self._base_url:
                    last_exception = e
                    if attempt <= API_MAX_RETRIES:
                        backoff = API_INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                        logger.warning(
                            "Local service auth error on attempt %d/%d. "
                            "Retrying in %.1fs... (Is Ollama running?)",
                            attempt, API_MAX_RETRIES + 1, backoff,
                        )
                        time.sleep(backoff)
                        continue
                # Permanent failure for cloud providers -- do not retry.
                logger.error("Summarizer API authentication failed.")
                raise SummarizerError(
                    "API authentication failed. Check your API key."
                ) from e

            except (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.RateLimitError,
            ) as e:
                # Transient failure -- retry with exponential backoff.
                last_exception = e
                if attempt <= API_MAX_RETRIES:
                    backoff = API_INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning(
                        "Transient summarizer API error (%s) on attempt %d/%d. "
                        "Retrying in %.1fs...",
                        type(e).__name__,
                        attempt,
                        API_MAX_RETRIES + 1,
                        backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "Transient summarizer API error (%s) on final attempt "
                        "%d/%d. No more retries.",
                        type(e).__name__,
                        attempt,
                        API_MAX_RETRIES + 1,
                    )

            except openai.APIError as e:
                # Other API errors (e.g., 500 server errors) -- do not retry.
                logger.error("Summarizer API error: %s", type(e).__name__)
                raise SummarizerError(f"API error: {type(e).__name__}") from e

            except Exception as e:
                # Unexpected errors -- do not retry.
                logger.error(
                    "Unexpected summarizer error: %s", type(e).__name__
                )
                raise SummarizerError(
                    f"Unexpected error: {type(e).__name__}"
                ) from e

        # All retries exhausted for a transient error.
        if isinstance(last_exception, openai.APITimeoutError):
            raise SummarizerError("Summarizer timed out.") from last_exception
        elif isinstance(last_exception, openai.APIConnectionError):
            if "localhost" in self._base_url or "127.0.0.1" in self._base_url:
                raise SummarizerError(
                    "Cannot connect to local Ollama. "
                    "Is Ollama running? Start it with: ollama serve"
                ) from last_exception
            raise SummarizerError("Network error.") from last_exception
        elif isinstance(last_exception, openai.RateLimitError):
            raise SummarizerError(
                "API rate limit exceeded."
            ) from last_exception
        else:
            raise SummarizerError(
                "Summarizer failed after retries."
            ) from last_exception
