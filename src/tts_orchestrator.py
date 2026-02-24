"""TTS orchestration for VoicePaste.

Extracted from VoicePasteApp to reduce the god-object pattern.
Manages the TTS synthesis, playback, caching, and export pipeline.

v1.2: Extracted from main.py.
"""

import logging
import threading
from typing import Callable, Optional

from constants import AppState, ELEVENLABS_VOICE_PRESETS
from tts import TTSError
from tts_cache import CacheKey

logger = logging.getLogger(__name__)


class TTSOrchestrator:
    """Manages TTS synthesis, caching, playback, and export.

    Owns the pipeline logic previously scattered across multiple
    methods in VoicePasteApp. Receives mutable references to shared
    components so that hot-reload in main.py continues to work.

    The orchestrator does NOT own the TTS backend, audio player, cache,
    or exporter lifecycles -- those are still managed by main.py.
    """

    def __init__(
        self,
        config,
        tts_backend,
        audio_player,
        tts_cache,
        tts_exporter,
        set_state: Callable[[AppState], None],
        get_state: Callable[[], AppState],
        register_cancel: Callable[[Callable], None],
        unregister_cancel: Callable[[], None],
        show_error: Callable[[str], None],
    ) -> None:
        self.config = config
        self.tts = tts_backend
        self.audio_player = audio_player
        self.tts_cache = tts_cache
        self.tts_exporter = tts_exporter
        self._set_state = set_state
        self._get_state = get_state
        self._register_cancel = register_cancel
        self._unregister_cancel = unregister_cancel
        self._show_error = show_error
        # Cancel callback -- set by the app after construction
        self.on_cancel: Optional[Callable[[], None]] = None

    # -- Component update methods for hot-reload --

    def update_tts(self, tts_backend) -> None:
        """Update TTS backend reference after settings change."""
        self.tts = tts_backend

    def update_cache(self, tts_cache) -> None:
        """Update TTS cache reference after settings change."""
        self.tts_cache = tts_cache

    def update_exporter(self, tts_exporter) -> None:
        """Update TTS exporter reference after settings change."""
        self.tts_exporter = tts_exporter

    # -- Cache key / voice label helpers --

    def get_cache_key(self, text: str) -> CacheKey:
        """Build a CacheKey from the current TTS config and text."""
        config = self.config
        if config.tts_provider == "piper":
            return CacheKey(
                provider="piper",
                voice_id=config.tts_local_voice,
                text=text,
            )
        if config.tts_provider == "openai":
            return CacheKey(
                provider="openai",
                voice_id=config.tts_openai_voice,
                text=text,
            )
        return CacheKey(
            provider="elevenlabs",
            voice_id=config.tts_voice_id,
            text=text,
        )

    def get_voice_label(self) -> str:
        """Get a human-readable label for the current TTS voice."""
        config = self.config
        if config.tts_provider == "piper":
            return config.tts_local_voice
        if config.tts_provider == "openai":
            from constants import OPENAI_TTS_VOICE_PRESETS
            preset = OPENAI_TTS_VOICE_PRESETS.get(config.tts_openai_voice, {})
            return preset.get("name", config.tts_openai_voice)
        preset = ELEVENLABS_VOICE_PRESETS.get(config.tts_voice_id, {})
        return preset.get("name", config.tts_voice_id)

    # -- Export helper --

    def export_audio(self, text: str, audio_data: bytes) -> None:
        """Export TTS audio to user directory if enabled. Non-fatal."""
        try:
            result = self.tts_exporter.export(text, audio_data)
            if result is not None:
                logger.info("TTS audio exported to: %s", result)
        except Exception:
            logger.exception("Error exporting TTS audio (non-fatal).")

    # -- Synthesis pipelines --

    def synthesize_and_play(self, text: str) -> None:
        """Synthesize text and play audio. Runs in a worker thread.

        Uses cache-through: checks cache before synthesis, stores after.

        Args:
            text: Text to synthesize and play.
        """
        try:
            cache_key = self.get_cache_key(text)
            audio_data = self.tts_cache.get(cache_key)

            if audio_data is None:
                audio_data = self.tts.synthesize(text)
                self.tts_cache.put(
                    cache_key, audio_data,
                    voice_label=self.get_voice_label(),
                )
            else:
                logger.info("TTS cache hit — skipping synthesis.")

            self.export_audio(text, audio_data)

            self._set_state(AppState.SPEAKING)
            self._register_cancel(self.on_cancel)
            self.audio_player.play(audio_data)

        except TTSError as e:
            logger.error("TTS error: %s", e)
            self._show_error(f"TTS error:\n{e}")

        except Exception:
            logger.exception("Unexpected error in TTS pipeline.")
            self._show_error("TTS playback failed.\nCheck the log file.")

        finally:
            self._unregister_cancel()
            self._set_state(AppState.IDLE)

    def synthesize_and_export(self, text: str, filename_hint: str = "") -> None:
        """Synthesize text and save to the export directory.

        Unlike synthesize_and_play, this does NOT play the audio.
        Used by the POST /tts/export API endpoint.

        Args:
            text: Text to synthesize and export.
            filename_hint: Optional custom filename hint.
        """
        try:
            cache_key = self.get_cache_key(text)
            audio_data = self.tts_cache.get(cache_key)

            if audio_data is None:
                audio_data = self.tts.synthesize(text)
                self.tts_cache.put(
                    cache_key, audio_data,
                    voice_label=self.get_voice_label(),
                )
            else:
                logger.info("TTS cache hit — skipping synthesis for export.")

            result = self.tts_exporter.export(
                text, audio_data, filename_hint=filename_hint,
            )
            if result is not None:
                logger.info("API TTS export complete: %s", result)
            else:
                logger.warning("API TTS export returned None (check config).")

        except TTSError as e:
            logger.error("TTS error in export pipeline: %s", e)
            self._show_error(f"TTS export error:\n{e}")

        except Exception:
            logger.exception("Unexpected error in TTS export pipeline.")
            self._show_error("TTS export failed.\nCheck the log file.")

        finally:
            self._set_state(AppState.IDLE)

    def synthesize_for_ask(self, text: str) -> None:
        """Synthesize and play for TTS Ask mode (within the pipeline thread).

        This is called from _run_pipeline's TTS Ask branch. Unlike
        synthesize_and_play, state transitions are managed by the caller.

        Args:
            text: The LLM answer text to synthesize and play.
        """
        cache_key = self.get_cache_key(text)
        audio_data = self.tts_cache.get(cache_key)

        if audio_data is None:
            audio_data = self.tts.synthesize(text)
            self.tts_cache.put(
                cache_key, audio_data,
                voice_label=self.get_voice_label(),
            )
        else:
            logger.info("TTS cache hit in Ask+TTS — skipping synthesis.")

        self.export_audio(text, audio_data)

        self._set_state(AppState.SPEAKING)
        self._register_cancel(self.on_cancel)
        self.audio_player.play(audio_data)

    # -- Replay --

    def replay_entry(self, entry_id: str) -> bool:
        """Replay a cached TTS entry by its ID. Returns True if started."""
        if self._get_state() != AppState.IDLE:
            return False
        audio_data = self.tts_cache.replay(entry_id)
        if audio_data is None:
            return False
        self._set_state(AppState.SPEAKING)
        thread = threading.Thread(
            target=self._play_cached_audio,
            args=(audio_data,),
            daemon=True,
            name="cache-replay-worker",
        )
        thread.start()
        return True

    def _play_cached_audio(self, audio_data: bytes) -> None:
        """Play cached audio bytes. Runs in worker thread."""
        try:
            self._register_cancel(self.on_cancel)
            self.audio_player.play(audio_data)
        except Exception:
            logger.exception("Error during cached audio playback.")
        finally:
            self._unregister_cancel()
            self._set_state(AppState.IDLE)
