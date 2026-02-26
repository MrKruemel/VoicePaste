"""TTS orchestration for VoicePaste.

Extracted from VoicePasteApp to reduce the god-object pattern.
Manages the TTS synthesis, playback, caching, and export pipeline.

v1.2: Extracted from main.py.
"""

import io
import logging
import re
import threading
import wave
from typing import Callable, Optional

import numpy as np

from constants import AppState, ELEVENLABS_VOICE_PRESETS, TTS_EMOTION_TAGGING_PROMPT
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
        summarizer=None,
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
        self.summarizer = summarizer
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

    def update_summarizer(self, summarizer) -> None:
        """Update summarizer reference after settings change."""
        self.summarizer = summarizer

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

    # -- LLM preprocessing --

    def _preprocess_text(self, text: str) -> str:
        """Optionally preprocess text with LLM before TTS synthesis.

        Used by the Ctrl+Alt+T readback pipeline. Rewrites messy
        clipboard text (bullets, markdown, URLs) into natural spoken
        prose via the summarizer.

        Returns the original text if preprocessing is disabled or
        if the summarizer is unavailable.
        """
        config = self.config
        if not config.tts_preprocess_with_llm:
            return text
        if self.summarizer is None:
            logger.debug("TTS preprocess enabled but no summarizer available.")
            return text

        # Determine the effective prompt
        from constants import TTS_PREPROCESS_DEFAULT_PROMPT
        prompt = config.tts_preprocess_prompt.strip()
        if not prompt:
            prompt = TTS_PREPROCESS_DEFAULT_PROMPT

        try:
            processed = self.summarizer.summarize(
                text, system_prompt=prompt,
            )
            if processed and processed.strip():
                logger.info(
                    "TTS LLM preprocess: %d chars -> %d chars",
                    len(text), len(processed),
                )
                return processed.strip()
            logger.warning("TTS LLM preprocess returned empty; using original.")
            return text
        except Exception:
            logger.exception("TTS LLM preprocess failed; using original text.")
            return text

    # -- Dynamic emotion tagging --

    def _should_use_dynamic_emotions(self) -> bool:
        """Check if dynamic emotion tagging should be used."""
        return (
            self.config.tts_dynamic_emotions
            and getattr(self.tts, "supports_streaming", False) is True
            and getattr(self.tts, "speaker_id_map", None) is not None
            and self.summarizer is not None
        )

    def _tag_emotions(
        self, text: str,
    ) -> Optional[list[tuple[str, Optional[int]]]]:
        """Use the LLM to tag each sentence with an emotion.

        Returns a list of (sentence_text, speaker_id) tuples, or None
        if tagging fails (caller should fall back to normal streaming).
        """
        sid_map = self.tts.speaker_id_map
        if not sid_map:
            return None

        # Build descriptions from voice model registry
        from constants import PIPER_VOICE_MODELS
        voice_info = PIPER_VOICE_MODELS.get(self.config.tts_local_voice, {})
        descriptions = voice_info.get("speaker_descriptions", {})

        emotion_names = sorted(sid_map.keys())
        desc_lines = []
        for name in emotion_names:
            desc = descriptions.get(name, "")
            if desc:
                desc_lines.append(f"- {name}: {desc}")
            else:
                desc_lines.append(f"- {name}")

        # Build examples using first few labels
        example_labels = emotion_names[:3] if len(emotion_names) >= 3 else emotion_names
        examples = "\n".join(
            f"{label}: Example sentence for {label}."
            for label in example_labels
        )

        prompt = TTS_EMOTION_TAGGING_PROMPT.format(
            emotion_descriptions="\n".join(desc_lines),
            examples=examples,
            text=text,
        )

        try:
            tagged = self.summarizer.summarize(text, system_prompt=prompt)
            if not tagged or not tagged.strip():
                logger.warning("Emotion tagging returned empty result.")
                return None
        except Exception:
            logger.exception("Emotion tagging LLM call failed.")
            return None

        return self._parse_emotion_tags(tagged, sid_map)

    @staticmethod
    def _parse_emotion_tags(
        tagged_text: str,
        sid_map: dict[str, int],
    ) -> Optional[list[tuple[str, Optional[int]]]]:
        """Parse LLM emotion-tagged output into (text, speaker_id) segments.

        Expected format: one line per sentence, "emotion: sentence text".
        Lines that don't match fall back to speaker_id=None (model default).
        """
        segments: list[tuple[str, Optional[int]]] = []
        pattern = re.compile(r"^(\w+)\s*:\s*(.+)$")

        for line in tagged_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            match = pattern.match(line)
            if match:
                emotion_label = match.group(1).lower()
                sentence_text = match.group(2).strip()
                sid = sid_map.get(emotion_label)
                if sid is None:
                    logger.debug(
                        "Unknown emotion '%s', using default.", emotion_label,
                    )
                segments.append((sentence_text, sid))
            else:
                # Line doesn't match pattern — use as-is with default speaker
                segments.append((line, None))

        if not segments:
            logger.warning("Emotion tag parsing produced no segments.")
            return None

        logger.info(
            "Emotion tagging: %d segments, emotions: %s",
            len(segments),
            [s[1] for s in segments],
        )
        return segments

    # -- Synthesis pipelines --

    def synthesize_and_play(self, text: str) -> None:
        """Synthesize text and play audio. Runs in a worker thread.

        Uses cache-through: checks cache before synthesis, stores after.
        Applies LLM preprocessing if enabled.

        For Piper local TTS, uses streaming playback: audio starts after
        the first clause is synthesized (~200ms) instead of waiting for
        the entire text. Cloud backends use the batch path.

        Args:
            text: Text to synthesize and play.
        """
        try:
            text = self._preprocess_text(text)
            cache_key = self.get_cache_key(text)
            audio_data = self.tts_cache.get(cache_key)

            if audio_data is not None:
                # Cache hit — batch play (fast path)
                logger.info("TTS cache hit — skipping synthesis.")
                self.export_audio(text, audio_data)
                self._set_state(AppState.SPEAKING)
                self._register_cancel(self.on_cancel)
                self.audio_player.play(audio_data)
            elif getattr(self.tts, "supports_streaming", False) is True:
                # Streaming path (Piper local)
                # Try dynamic emotion tagging if enabled
                if self._should_use_dynamic_emotions():
                    segments = self._tag_emotions(text)
                    if segments is not None:
                        sample_rate, pcm_iter = (
                            self.tts.synthesize_streaming_with_emotions(segments)
                        )
                    else:
                        sample_rate, pcm_iter = self.tts.synthesize_streaming(text)
                else:
                    sample_rate, pcm_iter = self.tts.synthesize_streaming(text)

                self._set_state(AppState.SPEAKING)
                self._register_cancel(self.on_cancel)
                completed, chunks = self.audio_player.play_streaming(
                    pcm_iter, sample_rate
                )
                # Cache the full result after playback
                if chunks:
                    full_pcm = np.concatenate(chunks)
                    wav_bytes = self._pcm_to_wav(full_pcm, sample_rate)
                    self.tts_cache.put(
                        cache_key, wav_bytes,
                        voice_label=self.get_voice_label(),
                    )
                    self.export_audio(text, wav_bytes)
            else:
                # Batch path (cloud backends)
                audio_data = self.tts.synthesize(text)
                self.tts_cache.put(
                    cache_key, audio_data,
                    voice_label=self.get_voice_label(),
                )
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
            text = self._preprocess_text(text)
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

        For Piper local TTS, uses streaming playback for lower latency.

        Args:
            text: The LLM answer text to synthesize and play.
        """
        cache_key = self.get_cache_key(text)
        audio_data = self.tts_cache.get(cache_key)

        if audio_data is not None:
            # Cache hit — batch play (fast path)
            logger.info("TTS cache hit in Ask+TTS — skipping synthesis.")
            self.export_audio(text, audio_data)
            self._set_state(AppState.SPEAKING)
            self._register_cancel(self.on_cancel)
            self.audio_player.play(audio_data)
        elif getattr(self.tts, "supports_streaming", False) is True:
            # Streaming path (Piper local)
            if self._should_use_dynamic_emotions():
                segments = self._tag_emotions(text)
                if segments is not None:
                    sample_rate, pcm_iter = (
                        self.tts.synthesize_streaming_with_emotions(segments)
                    )
                else:
                    sample_rate, pcm_iter = self.tts.synthesize_streaming(text)
            else:
                sample_rate, pcm_iter = self.tts.synthesize_streaming(text)

            self._set_state(AppState.SPEAKING)
            self._register_cancel(self.on_cancel)
            completed, chunks = self.audio_player.play_streaming(
                pcm_iter, sample_rate
            )
            if chunks:
                full_pcm = np.concatenate(chunks)
                wav_bytes = self._pcm_to_wav(full_pcm, sample_rate)
                self.tts_cache.put(
                    cache_key, wav_bytes,
                    voice_label=self.get_voice_label(),
                )
                self.export_audio(text, wav_bytes)
        else:
            # Batch path (cloud backends)
            audio_data = self.tts.synthesize(text)
            self.tts_cache.put(
                cache_key, audio_data,
                voice_label=self.get_voice_label(),
            )
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

    # -- Helpers --

    @staticmethod
    def _pcm_to_wav(pcm_int16: np.ndarray, sample_rate: int) -> bytes:
        """Convert int16 PCM array to WAV bytes for caching.

        Args:
            pcm_int16: 1-D int16 numpy array of audio samples.
            sample_rate: Audio sample rate in Hz.

        Returns:
            Complete WAV file as bytes.
        """
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_int16.tobytes())
        return buf.getvalue()
