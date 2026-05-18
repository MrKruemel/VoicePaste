"""API command dispatch for VoicePaste.

Extracted from VoicePasteApp to reduce the god-object pattern.
Maps incoming API commands to application actions and returns
JSON-serializable response dicts.

v1.1: Extracted from main.py.
"""

import logging
import threading
from typing import Optional, Protocol, runtime_checkable

from constants import (
    APP_VERSION,
    AppState,
    PIPER_VOICE_MODELS,
    TTS_MAX_TEXT_LENGTH,
    TTS_MAX_TEXT_LENGTH_LOCAL,
)

logger = logging.getLogger(__name__)


class _STTBackendUnavailable(Exception):
    """Raised when the STT backend cannot transcribe at all
    (e.g. faster-whisper / PyAV not installed)."""


class _STTDecodeError(Exception):
    """Raised when audio bytes could not be decoded for STT."""


def _looks_like_wav(data: bytes) -> bool:
    """Quick sniff: True if ``data`` starts with a RIFF/WAVE header."""
    return (
        len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WAVE"
    )


def _local_transcribe_nonwav(
    stt_backend, audio_data: bytes, language: str,
) -> str:
    """Decode non-WAV audio via PyAV and run the LocalWhisperSTT model.

    Mirrors ``LocalWhisperSTT.transcribe`` but skips the WAV-specific
    decoding path. Reuses the model's lazy-load + thread-safety, but
    bypasses ``_wav_bytes_to_float32`` (which only handles RIFF/WAVE).
    """
    # Lazy import so the dispatch module stays light-weight in tests.
    # In the frozen PyInstaller build PyAV is stubbed out (rthook_av_stub.py)
    # to shave ~119 MB, so decode_audio is never functional there. Detect
    # that case explicitly and surface a clear error to PURR/Tim.
    try:
        import av as _av  # noqa: F401
        if getattr(_av, "__version__", "") == "0.0.0-stub":
            raise _STTBackendUnavailable(
                "Non-WAV audio decoding requires the real PyAV library, "
                "but this build ships a stub (frozen binary). "
                "Send WAV (audio/wav) instead, or rebuild without "
                "rthook_av_stub.py to embed PyAV."
            )
        from faster_whisper.audio import decode_audio  # type: ignore
    except _STTBackendUnavailable:
        raise
    except Exception as e:  # ImportError, OSError (missing PyAV/ffmpeg)
        raise _STTBackendUnavailable(
            "Audio decoding for non-WAV input requires PyAV (libav). "
            f"Detail: {type(e).__name__}: {e}"
        ) from e

    import io as _io
    import time as _time

    # Ensure the model is loaded (under the existing lock).
    if not stt_backend._model_loaded or stt_backend._model is None:
        stt_backend.load_model()

    try:
        # decode_audio reads any libav-supported format and returns a
        # float32 numpy array at the requested sample rate.
        audio_array = decode_audio(_io.BytesIO(audio_data), sampling_rate=16000)
    except Exception as e:
        raise _STTDecodeError(
            f"Could not decode audio for STT: {type(e).__name__}: {e}"
        ) from e

    if audio_array is None or len(audio_array) == 0:
        raise _STTDecodeError("Decoded audio is empty.")

    transcribe_kwargs: dict = dict(
        language=None if language in (None, "auto") else language,
        beam_size=stt_backend._beam_size,
        vad_filter=stt_backend._vad_filter,
    )
    if stt_backend._initial_prompt:
        transcribe_kwargs["initial_prompt"] = stt_backend._initial_prompt
    if stt_backend._vad_filter:
        transcribe_kwargs["vad_parameters"] = dict(
            min_silence_duration_ms=500,
        )

    t0 = _time.monotonic()
    segments, info = stt_backend._model.transcribe(
        audio_array, **transcribe_kwargs,
    )
    transcript_parts = [seg.text.strip() for seg in segments]
    transcript = " ".join(transcript_parts).strip()
    elapsed = _time.monotonic() - t0

    stt_backend.detected_language = info.language
    duration = len(audio_array) / 16000.0
    logger.info(
        "API STT (non-WAV) complete: %d chars, %.1fs audio, %.1fs "
        "inference (%.1fx realtime). Detected: %s (prob=%.2f).",
        len(transcript), duration, elapsed,
        duration / max(elapsed, 0.001),
        info.language, info.language_probability,
    )
    return transcript


@runtime_checkable
class AppContext(Protocol):
    """Interface that the API dispatch layer needs from the main app.

    This protocol defines the minimal surface area required by the
    APIController, decoupling it from the full VoicePasteApp class.
    """

    @property
    def state(self) -> AppState: ...
    config: object  # AppConfig (avoid circular import)

    def _set_state(self, new_state: AppState) -> None: ...
    def _start_recording(self) -> None: ...
    def _stop_recording_and_process(self) -> None: ...
    def _on_cancel(self) -> None: ...
    def _run_tts_pipeline(self, text: str, voice: Optional[str] = None) -> None: ...
    def _run_tts_export_pipeline(self, text: str, filename_hint: str = "") -> None: ...
    def replay_tts_entry(self, entry_id: str) -> bool: ...


class APIController:
    """Handles API command dispatch, mapping actions to app operations.

    This class owns the routing logic previously in
    ``VoicePasteApp._api_dispatch``.  It receives a reference to the
    application context (``AppContext``) and delegates to it.

    Attributes:
        app: Reference to the application context.
    """

    def __init__(
        self,
        app: AppContext,
        tts_backend,
        audio_player,
        tts_cache,
        tts_exporter,
        paste_cancel_event: threading.Event,
        stt_backend=None,
        tts_orchestrator=None,
    ) -> None:
        self._app = app
        self._tts = tts_backend
        self._audio_player = audio_player
        self._tts_cache = tts_cache
        self._tts_exporter = tts_exporter
        self._paste_cancel_event = paste_cancel_event
        self._stt = stt_backend
        self._tts_orchestrator = tts_orchestrator

    # Allow main.py to update references after hot-reload
    def update_tts(self, tts_backend) -> None:
        """Update the TTS backend reference after settings change."""
        self._tts = tts_backend

    def update_cache(self, tts_cache) -> None:
        """Update the TTS cache reference after settings change."""
        self._tts_cache = tts_cache

    def update_exporter(self, tts_exporter) -> None:
        """Update the TTS exporter reference after settings change."""
        self._tts_exporter = tts_exporter

    def update_stt(self, stt_backend) -> None:
        """Update the STT backend reference after settings change."""
        self._stt = stt_backend

    def update_orchestrator(self, tts_orchestrator) -> None:
        """Update the TTS orchestrator reference."""
        self._tts_orchestrator = tts_orchestrator

    def dispatch(self, command: dict) -> dict:
        """Handle an API command and return a JSON-serializable response.

        Args:
            command: Dict with "action" key and optional parameters.

        Returns:
            Response dict with "status" key.
        """
        action = command.get("action", "")

        if action == "status":
            return self._handle_status()

        if action == "tts":
            return self._handle_tts(command)

        if action == "stop_tts":
            return self._handle_stop_tts()

        if action == "record_start":
            return self._handle_record_start(command)

        if action == "record_stop":
            return self._handle_record_stop()

        if action == "cancel":
            return self._handle_cancel()

        if action == "tts_history_list":
            return self._handle_tts_history_list()

        if action == "tts_history_get":
            return self._handle_tts_history_get(command)

        if action == "tts_replay":
            return self._handle_tts_replay(command)

        if action == "tts_history_delete":
            return self._handle_tts_history_delete(command)

        if action == "tts_history_clear":
            return self._handle_tts_history_clear()

        if action == "tts_export_list":
            return self._handle_tts_export_list()

        if action == "tts_export":
            return self._handle_tts_export(command)

        return {
            "status": "error",
            "error_code": "INVALID_PARAMS",
            "message": f"Unknown action: {action}",
        }

    # -- Streaming TTS (binary out) --

    def dispatch_tts_stream(
        self, command: dict,
    ) -> tuple[Optional[bytes], str, dict]:
        """Synthesize text and return raw audio bytes (no playback).

        Used by the HTTP API when the client requests audio streaming
        via ``Accept: audio/*``, ``?stream=ogg`` or ``stream: true``.

        Args:
            command: Dict with ``text`` and optional ``voice``.

        Returns:
            Tuple of ``(audio_bytes, mime_type, status_dict)``.
            On error, ``audio_bytes`` is ``None`` and ``status_dict``
            holds the error payload that the HTTP layer should send.
            On success, ``status_dict`` is ``{"status": "ok"}``.
        """
        text = command.get("text", "")
        if not text or not text.strip():
            return None, "", {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "text is required and must be non-empty",
            }
        text = text.strip()
        max_len = (TTS_MAX_TEXT_LENGTH_LOCAL
                   if self._app.config.tts_provider == "piper"
                   else TTS_MAX_TEXT_LENGTH)
        if len(text) > max_len:
            return None, "", {
                "status": "error",
                "error_code": "TEXT_TOO_LONG",
                "message": f"Text exceeds {max_len} character limit",
            }
        voice_override: Optional[str] = None
        if "voice" in command and command["voice"] is not None:
            requested = command["voice"]
            if not isinstance(requested, str) or not requested.strip():
                return None, "", {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": "voice must be a non-empty string",
                }
            requested = requested.strip()
            if requested not in PIPER_VOICE_MODELS:
                return None, "", {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": (
                        f"Unknown voice '{requested}'. "
                        f"Must be one of the registered Piper voices."
                    ),
                }
            if self._app.config.tts_provider != "piper":
                return None, "", {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": (
                        "voice override is only supported with the Piper "
                        "TTS provider"
                    ),
                }
            voice_override = requested
        if not self._tts:
            return None, "", {
                "status": "error",
                "error_code": "TTS_NOT_CONFIGURED",
                "message": "TTS is not enabled or configured",
            }
        if self._tts_orchestrator is None:
            return None, "", {
                "status": "error",
                "error_code": "TTS_NOT_CONFIGURED",
                "message": "TTS orchestrator is not wired",
            }
        if self._app.state != AppState.IDLE:
            return None, "", {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        # Synchronous synthesis under the PROCESSING guard. We hold
        # the state for the duration of the synthesis so concurrent
        # callers see "busy". No SPEAKING transition because no audio
        # is played locally.
        self._app._set_state(AppState.PROCESSING)
        try:
            logger.info(
                "API TTS stream: %d chars, voice=%s",
                len(text), voice_override or "(default)",
            )
            audio_bytes, mime = self._tts_orchestrator.synthesize_to_bytes(
                text, voice=voice_override,
            )
            logger.info(
                "API TTS stream complete: %d bytes (%s).",
                len(audio_bytes), mime,
            )
            return audio_bytes, mime, {"status": "ok"}
        except Exception as e:
            logger.error(
                "API TTS stream synthesis failed: %s: %s",
                type(e).__name__, e,
            )
            return None, "", {
                "status": "error",
                "error_code": "TTS_FAILED",
                "message": f"TTS synthesis failed: {type(e).__name__}",
            }
        finally:
            self._app._set_state(AppState.IDLE)

    # -- Speech-to-Text (binary in, JSON out) --

    def dispatch_stt(
        self,
        audio_data: bytes,
        content_type: str = "",
        language: Optional[str] = None,
    ) -> tuple[int, dict]:
        """Transcribe raw audio bytes and return a JSON-friendly dict.

        Args:
            audio_data: Raw audio bytes (WAV, OGG/Opus, MP3, FLAC, ...).
                Format is auto-detected by faster-whisper via PyAV.
            content_type: Client-provided Content-Type (for logging only).
            language: Optional language code (e.g. ``"de"``). Defaults to
                the configured ``transcription_language`` or German.

        Returns:
            Tuple of ``(http_status_code, response_dict)``.
        """
        if not audio_data:
            return 400, {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "Audio body is empty",
            }
        if self._stt is None:
            return 503, {
                "status": "error",
                "error_code": "STT_NOT_CONFIGURED",
                "message": (
                    "STT backend is not available. Configure the local "
                    "or cloud STT backend in Settings."
                ),
            }
        if self._app.state != AppState.IDLE:
            return 409, {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }

        if language is None or not str(language).strip():
            language = getattr(
                self._app.config, "transcription_language", "de",
            ) or "de"

        self._app._set_state(AppState.PROCESSING)
        try:
            logger.info(
                "API STT request: %d bytes, content_type=%s, language=%s",
                len(audio_data), content_type or "(unset)", language,
            )
            transcript = self._stt_transcribe(audio_data, language)
            detected = getattr(self._stt, "detected_language", None) or language
            logger.info(
                "API STT complete: transcript=%d chars, language=%s",
                len(transcript), detected,
            )
            return 200, {
                "status": "ok",
                "transcript": transcript,
                "language": detected,
            }
        except _STTBackendUnavailable as e:
            return 503, {
                "status": "error",
                "error_code": "STT_NOT_CONFIGURED",
                "message": str(e),
            }
        except _STTDecodeError as e:
            return 400, {
                "status": "error",
                "error_code": "AUDIO_DECODE_FAILED",
                "message": str(e),
            }
        except Exception as e:
            logger.error(
                "API STT failed: %s: %s", type(e).__name__, e,
            )
            return 500, {
                "status": "error",
                "error_code": "STT_FAILED",
                "message": f"Transcription failed: {type(e).__name__}",
            }
        finally:
            self._app._set_state(AppState.IDLE)

    def _stt_transcribe(self, audio_data: bytes, language: str) -> str:
        """Run the configured STT backend on raw audio bytes.

        The current STT backend protocol expects WAV bytes (the local
        WhisperSTT calls ``_wav_bytes_to_float32``). For non-WAV input
        (e.g. OGG/Opus from Telegram), we decode via faster-whisper's
        bundled ``decode_audio`` helper (PyAV) and feed the resulting
        float32 array directly to the WhisperModel.

        Raises:
            _STTBackendUnavailable: faster-whisper / native libs missing.
            _STTDecodeError: audio could not be decoded.
        """
        if _looks_like_wav(audio_data):
            # Fast path: WAV (16-bit PCM). Existing STT backends handle
            # this natively without PyAV.
            return self._stt.transcribe(audio_data, language=language)

        # Non-WAV input. Decode to float32 via PyAV (faster-whisper
        # bundled helper), then drive the WhisperModel directly.
        # Cloud STT (OpenAI Whisper API) supports OGG natively, so we
        # only need the local-decode dance when running local STT.
        try:
            from local_stt import LocalWhisperSTT
        except Exception:
            LocalWhisperSTT = None  # type: ignore[assignment]

        if LocalWhisperSTT is not None and isinstance(
            self._stt, LocalWhisperSTT,
        ):
            return _local_transcribe_nonwav(
                self._stt, audio_data, language,
            )

        # Cloud backend (e.g. OpenAI Whisper) -> pass through; the API
        # accepts OGG/MP3/WAV/etc. The CloudWhisperSTT names the file
        # ``recording.wav`` which is fine -- Whisper sniffs content.
        return self._stt.transcribe(audio_data, language=language)

    # -- Individual action handlers --

    def _handle_status(self) -> dict:
        return {
            "status": "ok",
            "data": {
                "state": self._app.state.value,
                "tts_enabled": self._app.config.tts_enabled,
                "api_version": "1",
                "app_version": APP_VERSION,
            },
        }

    def _handle_tts(self, command: dict) -> dict:
        text = command.get("text", "")
        if not text or not text.strip():
            return {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "text is required and must be non-empty",
            }
        text = text.strip()
        max_len = (TTS_MAX_TEXT_LENGTH_LOCAL
                   if self._app.config.tts_provider == "piper"
                   else TTS_MAX_TEXT_LENGTH)
        if len(text) > max_len:
            return {
                "status": "error",
                "error_code": "TEXT_TOO_LONG",
                "message": f"Text exceeds {max_len} character limit",
            }
        # Optional per-call voice override (Piper voice name).
        # Defaults to None -> pipeline keeps the globally configured voice.
        voice_override: Optional[str] = None
        if "voice" in command and command["voice"] is not None:
            requested = command["voice"]
            if not isinstance(requested, str) or not requested.strip():
                return {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": "voice must be a non-empty string",
                }
            requested = requested.strip()
            if requested not in PIPER_VOICE_MODELS:
                return {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": (
                        f"Unknown voice '{requested}'. "
                        f"Must be one of the registered Piper voices."
                    ),
                }
            if self._app.config.tts_provider != "piper":
                return {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": (
                        "voice override is only supported with the Piper "
                        "TTS provider"
                    ),
                }
            voice_override = requested
        if not self._tts:
            return {
                "status": "error",
                "error_code": "TTS_NOT_CONFIGURED",
                "message": "TTS is not enabled or configured",
            }
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        # Fire-and-forget: start TTS in worker thread
        self._app._set_state(AppState.PROCESSING)
        thread = threading.Thread(
            target=self._app._run_tts_pipeline,
            args=(text,),
            kwargs={"voice": voice_override},
            daemon=True,
            name="api-tts-worker",
        )
        thread.start()
        return {"status": "ok"}

    def _handle_stop_tts(self) -> dict:
        if self._app.state == AppState.SPEAKING:
            self._audio_player.stop()
        return {"status": "ok"}

    def _handle_record_start(self, command: dict) -> dict:
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        mode = command.get("mode", "summary")
        if mode not in ("summary", "prompt"):
            mode = "summary"
        self._app._active_mode = mode
        self._app._start_recording()
        return {"status": "ok"}

    def _handle_record_stop(self) -> dict:
        if self._app.state != AppState.RECORDING:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Not currently recording",
            }
        self._app._stop_recording_and_process()
        return {"status": "ok"}

    def _handle_cancel(self) -> dict:
        current = self._app.state
        if current == AppState.RECORDING:
            self._app._on_cancel()
        elif current == AppState.SPEAKING:
            self._audio_player.stop()
        elif current == AppState.AWAITING_PASTE:
            self._paste_cancel_event.set()
        return {"status": "ok"}

    def _handle_tts_history_list(self) -> dict:
        entries = self._tts_cache.list_entries(limit=50)
        stats = self._tts_cache.stats()
        return {
            "status": "ok",
            "data": {
                "entries": entries,
                "total_entries": stats["total_entries"],
                "total_size_mb": stats["total_size_mb"],
                "cache_enabled": stats["cache_enabled"],
            },
        }

    def _handle_tts_history_get(self, command: dict) -> dict:
        entry_id = command.get("id", "")
        entry = self._tts_cache.get_entry(entry_id)
        if entry is None:
            return {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": f"Cache entry '{entry_id}' not found",
            }
        return {"status": "ok", "data": entry}

    def _handle_tts_replay(self, command: dict) -> dict:
        entry_id = command.get("id", "")
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        success = self._app.replay_tts_entry(entry_id)
        if not success:
            return {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": f"Cache entry '{entry_id}' not found",
            }
        return {"status": "ok"}

    def _handle_tts_history_delete(self, command: dict) -> dict:
        entry_id = command.get("id", "")
        deleted = self._tts_cache.delete(entry_id)
        if not deleted:
            return {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": f"Cache entry '{entry_id}' not found",
            }
        return {"status": "ok", "deleted": True}

    def _handle_tts_history_clear(self) -> dict:
        count = self._tts_cache.clear()
        return {"status": "ok", "deleted_count": count}

    def _handle_tts_export_list(self) -> dict:
        exports = self._tts_exporter.list_exports()
        stats = self._tts_exporter.stats()
        return {
            "status": "ok",
            "data": {
                "exports": exports,
                "total_files": stats["total_files"],
                "total_size_mb": stats["total_size_mb"],
                "export_enabled": stats["enabled"],
                "export_dir": stats["export_dir"],
            },
        }

    def _handle_tts_export(self, command: dict) -> dict:
        text = command.get("text", "")
        if not text or not text.strip():
            return {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "text is required and must be non-empty",
            }
        text = text.strip()
        max_len = (TTS_MAX_TEXT_LENGTH_LOCAL
                   if self._app.config.tts_provider == "piper"
                   else TTS_MAX_TEXT_LENGTH)
        if len(text) > max_len:
            return {
                "status": "error",
                "error_code": "TEXT_TOO_LONG",
                "message": f"Text exceeds {max_len} character limit",
            }
        if not self._tts:
            return {
                "status": "error",
                "error_code": "TTS_NOT_CONFIGURED",
                "message": "TTS is not enabled or configured",
            }
        if not self._tts_exporter.enabled:
            return {
                "status": "error",
                "error_code": "EXPORT_DISABLED",
                "message": "TTS export is not enabled. Enable in Settings.",
            }
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        # Synthesize + export in worker thread (fire-and-forget)
        self._app._set_state(AppState.PROCESSING)
        thread = threading.Thread(
            target=self._app._run_tts_export_pipeline,
            args=(text, command.get("filename_hint", "")),
            daemon=True,
            name="api-tts-export-worker",
        )
        thread.start()
        return {"status": "ok", "message": "Export started"}
