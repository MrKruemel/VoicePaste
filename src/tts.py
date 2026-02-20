"""Text-to-Speech backend for Voice Paste.

Protocol-based TTS abstraction with ElevenLabs and Piper implementations.
Follows the same pattern as stt.py and summarizer.py.

v0.6: Initial implementation (ElevenLabs cloud TTS).
v0.7: Added Piper local TTS via direct ONNX inference.
"""

import logging
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """Raised when TTS synthesis fails."""


class TTSBackend(Protocol):
    """Protocol for TTS backends."""

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes (MP3 or WAV).

        Args:
            text: Text to synthesize.

        Returns:
            Audio bytes (MP3 for cloud backends, WAV for local backends).
            The AudioPlayer handles both formats transparently.

        Raises:
            TTSError: If synthesis fails.
        """
        ...


class ElevenLabsTTS:
    """ElevenLabs TTS backend using the elevenlabs SDK.

    Synthesizes text to MP3 audio using the ElevenLabs API.

    Attributes:
        voice_id: ElevenLabs voice identifier.
        model_id: ElevenLabs model identifier.
        output_format: Audio output format string.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "mp3_44100_128",
    ) -> None:
        """Initialize the ElevenLabs TTS client.

        Args:
            api_key: ElevenLabs API key.
            voice_id: Voice ID for synthesis.
            model_id: Model ID (default: eleven_flash_v2_5 for low latency).
            output_format: Output audio format.
        """
        from elevenlabs.client import ElevenLabs

        self._client = ElevenLabs(api_key=api_key, timeout=30.0)
        self.voice_id = voice_id
        self.model_id = model_id
        self.output_format = output_format

        logger.info(
            "ElevenLabs TTS initialized: voice=%s, model=%s, format=%s",
            voice_id, model_id, output_format,
        )

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to MP3 audio bytes.

        Args:
            text: Text to synthesize.

        Returns:
            MP3-encoded audio bytes.

        Raises:
            TTSError: If synthesis fails.
        """
        try:
            audio_iter = self._client.text_to_speech.convert(
                text=text,
                voice_id=self.voice_id,
                model_id=self.model_id,
                output_format=self.output_format,
            )

            # The API returns an iterator of bytes chunks — collect them
            audio_bytes = b"".join(audio_iter)

            if not audio_bytes:
                raise TTSError("ElevenLabs returned empty audio.")

            logger.info(
                "TTS synthesis complete: %d bytes for %d chars",
                len(audio_bytes),
                len(text),
            )
            return audio_bytes

        except TTSError:
            raise
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                raise TTSError(
                    "ElevenLabs API key is invalid. "
                    "Check your key in Settings > Text-to-Speech."
                ) from e
            if "429" in error_msg:
                raise TTSError(
                    "ElevenLabs rate limit exceeded. "
                    "Wait a moment and try again."
                ) from e
            if "quota" in error_msg.lower() or "limit" in error_msg.lower():
                raise TTSError(
                    "ElevenLabs quota exceeded. "
                    "Check your plan at elevenlabs.io."
                ) from e
            # Sanitize error message to avoid leaking API response details
            raise TTSError(
                "TTS synthesis failed. Check your API key and network connection."
            ) from e


def create_tts_backend(
    api_key: str,
    provider: str = "elevenlabs",
    voice_id: str = "",
    model_id: str = "",
    output_format: str = "",
    local_voice: str = "",
    speed: float = 1.0,
) -> Optional[TTSBackend]:
    """Factory: create a TTS backend from configuration.

    Args:
        api_key: API key for the TTS provider (required for cloud providers).
        provider: TTS provider name ("elevenlabs" or "piper").
        voice_id: Voice ID override (ElevenLabs).
        model_id: Model ID override (ElevenLabs).
        output_format: Output format override (ElevenLabs).
        local_voice: Piper voice name (e.g., "de_DE-thorsten-medium").

    Returns:
        TTSBackend instance, or None if configuration is incomplete.
    """
    if provider == "piper":
        # v0.7: Local TTS via Piper ONNX -- no API key needed
        try:
            from local_tts import PiperLocalTTS, is_espeakng_available

            if not is_espeakng_available():
                logger.warning(
                    "espeakng-loader is not available. "
                    "Local TTS (Piper) cannot be used."
                )
                return None

            from constants import DEFAULT_PIPER_VOICE

            voice = local_voice or DEFAULT_PIPER_VOICE
            return PiperLocalTTS(voice_name=voice, speed=speed)

        except ImportError as e:
            logger.warning(
                "Piper local TTS not available (missing dependencies): %s", e
            )
            return None
        except Exception as e:
            logger.error("Failed to create Piper TTS backend: %s", e)
            return None

    # Cloud providers require an API key
    if not api_key:
        logger.warning("No TTS API key configured. TTS will not be available.")
        return None

    if provider == "elevenlabs":
        from constants import (
            DEFAULT_TTS_MODEL_ID,
            DEFAULT_TTS_OUTPUT_FORMAT,
            DEFAULT_TTS_VOICE_ID,
        )

        return ElevenLabsTTS(
            api_key=api_key,
            voice_id=voice_id or DEFAULT_TTS_VOICE_ID,
            model_id=model_id or DEFAULT_TTS_MODEL_ID,
            output_format=output_format or DEFAULT_TTS_OUTPUT_FORMAT,
        )

    logger.warning("Unknown TTS provider '%s'.", provider)
    return None
