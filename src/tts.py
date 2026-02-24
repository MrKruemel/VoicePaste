"""Text-to-Speech backend for Voice Paste.

Protocol-based TTS abstraction with ElevenLabs, OpenAI, and Piper implementations.
Follows the same pattern as stt.py and summarizer.py.

v0.6: Initial implementation (ElevenLabs cloud TTS).
v0.7: Added Piper local TTS via direct ONNX inference.
v0.9.2: Added OpenAI TTS (gpt-4o-mini-tts, tts-1, tts-1-hd).
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


class OpenAITTS:
    """OpenAI TTS backend using the openai SDK.

    Synthesizes text to audio using the OpenAI Audio Speech API.
    Supports gpt-4o-mini-tts (with instructions), tts-1, and tts-1-hd models.
    """

    def __init__(
        self,
        api_key: str,
        voice: str = "coral",
        model: str = "gpt-4o-mini-tts",
        response_format: str = "mp3",
        instructions: str = "",
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=30.0)
        self.voice = voice
        self.model = model
        self.response_format = response_format
        self.instructions = instructions

        logger.info(
            "OpenAI TTS initialized: voice=%s, model=%s, format=%s",
            voice, model, response_format,
        )

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes.

        Returns:
            Audio bytes in the configured format (default MP3).

        Raises:
            TTSError: If synthesis fails.
        """
        try:
            kwargs: dict = {
                "model": self.model,
                "voice": self.voice,
                "input": text,
                "response_format": self.response_format,
            }
            # instructions is only supported by gpt-4o-mini-tts
            if self.instructions and self.model == "gpt-4o-mini-tts":
                kwargs["instructions"] = self.instructions

            response = self._client.audio.speech.create(**kwargs)
            audio_bytes = response.content

            if not audio_bytes:
                raise TTSError("OpenAI TTS returned empty audio.")

            logger.info(
                "OpenAI TTS synthesis complete: %d bytes for %d chars",
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
                    "OpenAI API key is invalid. "
                    "Check your key in Settings > Transcription."
                ) from e
            if "429" in error_msg or "rate" in error_msg.lower():
                raise TTSError(
                    "OpenAI rate limit exceeded. "
                    "Wait a moment and try again."
                ) from e
            if "quota" in error_msg.lower() or "insufficient" in error_msg.lower():
                raise TTSError(
                    "OpenAI quota exceeded. "
                    "Check your billing at platform.openai.com."
                ) from e
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
    sentence_pause_ms: int = 350,
    noise_scale: Optional[float] = None,
    noise_w: Optional[float] = None,
    openai_tts_voice: str = "",
    openai_tts_model: str = "",
    openai_tts_format: str = "",
    openai_tts_instructions: str = "",
) -> Optional[TTSBackend]:
    """Factory: create a TTS backend from configuration.

    Args:
        api_key: API key for the TTS provider (required for cloud providers).
        provider: TTS provider name ("elevenlabs", "openai", or "piper").
        voice_id: Voice ID override (ElevenLabs).
        model_id: Model ID override (ElevenLabs).
        output_format: Output format override (ElevenLabs).
        local_voice: Piper voice name (e.g., "de_DE-thorsten-medium").
        speed: Speech speed for Piper local TTS.
        sentence_pause_ms: Silence gap between sentences in ms (Piper).
        noise_scale: VITS phoneme noise override (Piper). None = model default.
        noise_w: VITS duration noise override (Piper). None = model default.
        openai_tts_voice: OpenAI voice name (e.g., "coral").
        openai_tts_model: OpenAI model name (e.g., "gpt-4o-mini-tts").
        openai_tts_format: OpenAI audio format (e.g., "mp3").
        openai_tts_instructions: Optional tone/style instructions (gpt-4o-mini-tts only).

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
            return PiperLocalTTS(
                voice_name=voice,
                speed=speed,
                sentence_pause_ms=sentence_pause_ms,
                noise_scale=noise_scale,
                noise_w=noise_w,
            )

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

    if provider == "openai":
        from constants import (
            DEFAULT_OPENAI_TTS_FORMAT,
            DEFAULT_OPENAI_TTS_MODEL,
            DEFAULT_OPENAI_TTS_VOICE,
        )

        return OpenAITTS(
            api_key=api_key,
            voice=openai_tts_voice or DEFAULT_OPENAI_TTS_VOICE,
            model=openai_tts_model or DEFAULT_OPENAI_TTS_MODEL,
            response_format=openai_tts_format or DEFAULT_OPENAI_TTS_FORMAT,
            instructions=openai_tts_instructions,
        )

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
