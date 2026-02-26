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

from constants import (
    AppState,
    ELEVENLABS_VOICE_PRESETS,
    TTS_EMOTION_PROMPT_DE,
    TTS_DIALOG_PROMPT_EN,
)
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
            # Include audio FX parameters in cache key so different
            # effect settings produce different cache entries.
            from audio_fx import AudioFXConfig
            fx = AudioFXConfig(
                pitch_semitones=config.audio_fx_pitch_semitones,
                formant_shift=config.audio_fx_formant_shift,
                bass_db=config.audio_fx_bass_db,
                treble_db=config.audio_fx_treble_db,
                reverb_mix=config.audio_fx_reverb_mix,
            )
            fx_suffix = fx.to_cache_suffix()
            voice_id = config.tts_local_voice
            if fx_suffix:
                voice_id = f"{voice_id}|{fx_suffix}"
            return CacheKey(
                provider="piper",
                voice_id=voice_id,
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

        When no custom prompt is configured, the default prompt is
        selected automatically based on the Piper voice language
        (German or English).

        Returns the original text if preprocessing is disabled or
        if the summarizer is unavailable.
        """
        config = self.config
        if not config.tts_preprocess_with_llm:
            return text
        if self.summarizer is None:
            logger.debug("TTS preprocess enabled but no summarizer available.")
            return text

        # Determine the effective prompt, with language-adaptive default
        from constants import (
            TTS_PREPROCESS_DEFAULT_PROMPT,
            TTS_PREPROCESS_DEFAULT_PROMPT_EN,
            get_voice_language,
        )
        prompt = config.tts_preprocess_prompt.strip()
        if not prompt:
            lang = get_voice_language(config.tts_local_voice)
            if lang == "en":
                prompt = TTS_PREPROCESS_DEFAULT_PROMPT_EN
            else:
                prompt = TTS_PREPROCESS_DEFAULT_PROMPT
            logger.debug(
                "TTS preprocess: using %s default prompt (voice=%s).",
                lang, config.tts_local_voice,
            )

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
        """Check if dynamic emotion tagging should be used.

        Requires: dynamic_emotions enabled, streaming TTS with speaker map,
        and a REAL LLM summarizer (not PassthroughSummarizer).
        """
        if not self.config.tts_dynamic_emotions:
            return False
        if not (getattr(self.tts, "supports_streaming", False) is True
                and getattr(self.tts, "speaker_id_map", None) is not None):
            return False
        if self.summarizer is None:
            logger.debug("Dynamic emotions: no summarizer available.")
            return False
        # PassthroughSummarizer just returns text unchanged — can't do tagging
        cls_name = type(self.summarizer).__name__
        if cls_name == "PassthroughSummarizer":
            logger.warning(
                "Dynamic emotions requires a real LLM summarizer (not Passthrough). "
                "Enable summarization in Settings with an API key."
            )
            return False
        return True

    def _tag_emotions(
        self, text: str,
    ) -> Optional[list[tuple[str, Optional[int]]]]:
        """Use the LLM to tag each sentence with an emotion or character.

        Automatically selects the German emotion prompt or English dialog
        prompt based on the current Piper voice language. Uses
        ``speaker_examples`` from the voice model registry when available,
        falling back to auto-generated generic examples.

        A custom prompt (``config.tts_emotion_prompt``) takes priority
        over the auto-selected prompt.

        Returns a list of (sentence_text, speaker_id) tuples, or None
        if tagging fails (caller should fall back to normal streaming).
        """
        sid_map = self.tts.speaker_id_map
        if not sid_map:
            return None

        # Build descriptions from voice model registry
        from constants import PIPER_VOICE_MODELS, get_voice_language
        voice_info = PIPER_VOICE_MODELS.get(self.config.tts_local_voice, {})
        descriptions = voice_info.get("speaker_descriptions", {})
        speaker_examples = voice_info.get("speaker_examples", {})

        label_names = sorted(sid_map.keys())
        label_list_str = ", ".join(label_names)
        desc_lines = []
        for name in label_names:
            desc = descriptions.get(name, "")
            if desc:
                desc_lines.append(f"- {name}: {desc}")
            else:
                desc_lines.append(f"- {name}")

        # Build examples: prefer speaker_examples from registry, fall back
        # to generic auto-generated placeholders.
        if speaker_examples:
            example_labels = [k for k in label_names if k in speaker_examples]
            if not example_labels:
                example_labels = label_names[:3]
            examples = "\n".join(
                f"{label}: {speaker_examples.get(label, 'Example sentence.')}"
                for label in example_labels
            )
        else:
            example_labels = label_names[:3] if len(label_names) >= 3 else label_names
            examples = "\n".join(
                f"{label}: Example sentence for {label}."
                for label in example_labels
            )

        # Select prompt: custom > auto (by voice language)
        custom_prompt = getattr(self.config, "tts_emotion_prompt", "")
        if custom_prompt and custom_prompt.strip():
            prompt_template = custom_prompt.strip()
            logger.debug("Emotion tagging: using custom prompt.")
        else:
            lang = get_voice_language(self.config.tts_local_voice)
            if lang == "en":
                prompt_template = TTS_DIALOG_PROMPT_EN
            else:
                prompt_template = TTS_EMOTION_PROMPT_DE
            logger.debug(
                "Emotion tagging: using %s prompt (voice=%s).",
                lang, self.config.tts_local_voice,
            )

        prompt = prompt_template.format(
            label_descriptions="\n".join(desc_lines),
            label_list=label_list_str,
            examples=examples,
            # Keep backward compat with old {emotion_descriptions} placeholder
            emotion_descriptions="\n".join(desc_lines),
        )

        logger.debug(
            "Emotion tagging prompt (%d chars):\n%s",
            len(prompt), prompt[:500],
        )

        try:
            tagged = self.summarizer.summarize(text, system_prompt=prompt)
            if not tagged or not tagged.strip():
                logger.warning("Emotion tagging returned empty result.")
                return None
            logger.debug("Emotion tagging LLM response:\n%s", tagged[:500])
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
                    logger.warning(
                        "Unknown emotion label '%s' (valid: %s), using default.",
                        emotion_label, ", ".join(sorted(sid_map.keys())),
                    )
                else:
                    logger.debug("Matched label '%s' -> speaker_id=%d", emotion_label, sid)
                segments.append((sentence_text, sid))
            else:
                # Line doesn't match "label: text" pattern — use as-is with default
                logger.debug("No label match for line: %.60s", line)
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
