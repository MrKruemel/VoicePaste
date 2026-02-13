"""Speech-to-Text backend abstraction and implementations.

Provides a Protocol for STT backends, a cloud implementation using the
OpenAI Whisper API, and a factory function that creates the appropriate
backend based on application configuration.

v0.4: Added create_stt_backend() factory to select cloud or local backend.

REQ-S06: HTTPS only for all API calls.
REQ-S07: TLS certificate validation is always enabled.
"""

import io
import logging
import time
from typing import Protocol

import openai

from constants import (
    API_INITIAL_BACKOFF_SECONDS,
    API_MAX_RETRIES,
    API_TIMEOUT_SECONDS,
    WHISPER_MODEL,
)

logger = logging.getLogger(__name__)


class STTBackend(Protocol):
    """Protocol for speech-to-text backends.

    Implementations must transcribe audio bytes to text.
    """

    def transcribe(self, audio_data: bytes, language: str = "de") -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_data: WAV audio file bytes.
            language: Language code for transcription (default 'de' for German).

        Returns:
            Transcribed text string.

        Raises:
            STTError: If transcription fails.
        """
        ...


class STTError(Exception):
    """Raised when speech-to-text transcription fails."""

    pass


class CloudWhisperSTT:
    """OpenAI Whisper API implementation of STTBackend.

    Sends audio to the OpenAI Whisper API via HTTPS and returns the transcript.

    REQ-S06: Uses HTTPS only (enforced by the openai library).
    REQ-S07: TLS validation is enabled by default in the openai library.
    REQ-S09: Audio is sent from memory, never from disk.
    REQ-S11: Audio data is never logged.

    Attributes:
        api_key: OpenAI API key.
        model: Whisper model name.
        timeout: API call timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        model: str = WHISPER_MODEL,
        timeout: int = API_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the cloud Whisper STT backend.

        Args:
            api_key: OpenAI API key (REQ-S02: never hardcoded).
            model: Whisper model identifier.
            timeout: Timeout for the API call in seconds.
        """
        self._client = openai.OpenAI(
            api_key=api_key,
            timeout=timeout,
        )
        self._model = model
        self._timeout = timeout

    def transcribe(self, audio_data: bytes, language: str = "de") -> str:
        """Transcribe audio using the OpenAI Whisper API.

        Retries up to API_MAX_RETRIES times with exponential backoff for
        transient errors (connection errors, timeouts, rate limits).
        Auth errors and other permanent failures are raised immediately.

        Args:
            audio_data: WAV audio file bytes (in-memory, never from disk).
            language: Language code for transcription.

        Returns:
            Transcribed text string.

        Raises:
            STTError: If the API call fails after all retries or on a
                permanent error.
        """
        logger.info("Sending audio to Whisper API (%d bytes)...", len(audio_data))

        last_exception: Exception | None = None

        for attempt in range(1, API_MAX_RETRIES + 2):  # 1 initial + up to 2 retries
            try:
                # Create a fresh in-memory file-like object per attempt
                # because BytesIO position is consumed after first read.
                audio_file = io.BytesIO(audio_data)
                audio_file.name = "recording.wav"

                response = self._client.audio.transcriptions.create(
                    model=self._model,
                    file=audio_file,
                    language=language,
                    response_format="text",
                )

                transcript = str(response).strip()

                # REQ-S24/S25: Do not log transcript content.
                # Only log success/failure and metadata.
                if transcript:
                    logger.info(
                        "Transcription complete. Length: %d characters.",
                        len(transcript),
                    )
                else:
                    logger.info("Transcription returned empty text.")

                return transcript

            except openai.AuthenticationError as e:
                # Permanent failure -- do not retry.
                logger.error(
                    "API authentication failed. Check your API key in config.toml."
                )
                raise STTError(
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
                        "Transient API error (%s) on attempt %d/%d. "
                        "Retrying in %.1fs...",
                        type(e).__name__,
                        attempt,
                        API_MAX_RETRIES + 1,
                        backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "Transient API error (%s) on final attempt %d/%d. "
                        "No more retries.",
                        type(e).__name__,
                        attempt,
                        API_MAX_RETRIES + 1,
                    )

            except openai.APIError as e:
                # Other API errors (e.g., 500 server errors) -- do not retry.
                logger.error("OpenAI API error: %s", type(e).__name__)
                raise STTError(f"API error: {type(e).__name__}") from e

            except Exception as e:
                # Unexpected errors -- do not retry.
                logger.error(
                    "Unexpected error during transcription: %s", type(e).__name__
                )
                raise STTError(f"Unexpected error: {type(e).__name__}") from e

        # All retries exhausted for a transient error.
        if isinstance(last_exception, openai.APITimeoutError):
            raise STTError("API call timed out.") from last_exception
        elif isinstance(last_exception, openai.APIConnectionError):
            raise STTError(
                "Network error. Check your internet connection."
            ) from last_exception
        elif isinstance(last_exception, openai.RateLimitError):
            raise STTError(
                "API rate limit exceeded. Try again later."
            ) from last_exception
        else:
            raise STTError("API call failed after retries.") from last_exception


def create_stt_backend(config: "AppConfig") -> "STTBackend | None":
    """Factory function to create an STT backend based on configuration.

    v0.4: Selects CloudWhisperSTT or LocalWhisperSTT based on config.stt_backend.

    For local mode, this function checks three preconditions:
      1. faster-whisper must be importable (package installed, DLLs present).
      2. The selected model must be downloaded (validated via model_manager).
      3. LocalWhisperSTT must be constructable without errors.

    If any precondition fails, a warning is logged with an actionable message
    and None is returned. The caller (VoicePasteApp) is expected to show the
    user a notification when they try to record.

    Args:
        config: Application configuration with STT settings.

    Returns:
        An STTBackend implementation, or None if no backend can be created
        (e.g., no API key for cloud, faster-whisper not installed for local).
    """
    if config.stt_backend == "local":
        try:
            from local_stt import LocalWhisperSTT, is_faster_whisper_available
            import model_manager

            if not is_faster_whisper_available():
                logger.warning(
                    "faster-whisper is not installed or its native libraries "
                    "could not be loaded. Cannot use local STT. "
                    "Install with: pip install faster-whisper"
                )
                return None

            model_path = model_manager.get_model_path(config.local_model_size)

            if model_path is None:
                logger.warning(
                    "Local Whisper model '%s' is not downloaded. "
                    "Use Settings > Transcription > Download Model to "
                    "download it before recording.",
                    config.local_model_size,
                )
                # Still create the backend -- it will raise a clear
                # STTError with user guidance when transcribe() is called,
                # but only if we are NOT in a frozen exe. In a frozen exe,
                # we return None so _start_recording() can show a specific
                # error immediately.
                import sys

                if getattr(sys, "frozen", False):
                    logger.warning(
                        "Running as frozen executable. Auto-download is "
                        "not supported. Returning None for STT backend."
                    )
                    return None

            return LocalWhisperSTT(
                model_size=config.local_model_size,
                device=config.local_device,
                compute_type=config.local_compute_type,
                model_path=model_path,
                vad_filter=config.vad_filter,
            )

        except STTError as e:
            # STTError from LocalWhisperSTT.__init__ (e.g., faster-whisper
            # not importable at construction time)
            logger.error(
                "Local STT backend creation failed: %s", e
            )
            return None

        except ImportError as e:
            logger.error(
                "Failed to import local STT modules: %s. "
                "Check that faster-whisper and its dependencies are "
                "installed.",
                e,
            )
            return None

        except Exception as e:
            logger.error(
                "Unexpected error creating local STT backend: %s: %s",
                type(e).__name__,
                e,
            )
            return None

    else:
        # Cloud backend
        if not config.openai_api_key:
            logger.warning("No API key for cloud STT.")
            return None
        return CloudWhisperSTT(api_key=config.openai_api_key)
