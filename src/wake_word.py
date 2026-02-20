"""Wake word detection via STT keyword spotting.

Continuously listens to the microphone and detects a configurable wake
phrase by running short faster-whisper transcriptions on detected speech
segments.  Uses energy-based VAD to minimize CPU usage when no one is
speaking (~0% idle, brief STT bursts only during speech).

Architecture:
    - Opens its own sounddevice InputStream (16 kHz, mono, int16).
    - Computes RMS energy per 100 ms frame to detect speech presence.
    - When speech is detected, buffers audio until speech ends or 3 s max.
    - Runs faster-whisper tiny model on the buffer (beam_size=1 for speed).
    - Checks if the transcription contains the configured wake phrase.
    - On match: fires the on_detected callback, then enters cooldown.

Thread model:
    - _listen_loop runs on its own daemon thread ("wake-word-listener").
    - The sounddevice InputStream runs its callback on the audio thread.
    - The on_detected callback is invoked from the listener thread.

Privacy:
    - All processing is 100% local (no audio sent to cloud).
    - Audio frames are discarded immediately after STT check.
    - No audio is ever written to disk.

v0.9: Initial implementation.
"""

import logging
import re
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from constants import (
    DEFAULT_HANDSFREE_BUFFER_SECONDS,
    DEFAULT_HANDSFREE_COOLDOWN_SECONDS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_WAKE_PHRASE,
    DEFAULT_WAKE_PHRASE_MATCH_MODE,
)

logger = logging.getLogger(__name__)

# Frame size: 100 ms at 16 kHz = 1600 samples
_FRAME_SIZE = 1600
# Minimum speech duration to trigger STT (avoid noise spikes)
_MIN_SPEECH_SECONDS = 0.6
# Grace period: keep buffering this long after energy drops, to bridge
# natural pauses between words (e.g. "Hello ... Cloud")
_SPEECH_END_GRACE_SECONDS = 0.5
# Default RMS threshold for speech detection (int16 scale).
# 200 is sensitive enough for normal speech at arm's length from the mic,
# while still filtering out most ambient noise.
_DEFAULT_ENERGY_THRESHOLD = 200.0


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def _fuzzy_match(phrase_tokens: list[str], transcript_tokens: list[str]) -> bool:
    """Check if at least 70% of phrase tokens appear in transcript."""
    if not phrase_tokens:
        return False
    matches = sum(1 for t in phrase_tokens if t in transcript_tokens)
    return (matches / len(phrase_tokens)) >= 0.7


def _clear_buffer(buf: list[np.ndarray]) -> None:
    """Zero and clear audio buffer (REQ-S10: scrub audio from memory)."""
    for frame in buf:
        frame.fill(0)
    buf.clear()


class WakeWordDetector:
    """Continuous wake word detection using faster-whisper tiny model.

    Opens a persistent sounddevice InputStream and processes audio in a
    background thread.  Uses energy-based VAD to detect speech segments,
    then runs faster-whisper tiny model on short buffers to check for
    the configured wake phrase.

    The detector manages its own separate tiny model instance (not shared
    with the main STT backend) because WhisperModel.transcribe() is not
    thread-safe and the main model may be a different size.
    """

    def __init__(
        self,
        wake_phrase: str = DEFAULT_WAKE_PHRASE,
        on_detected: Optional[Callable[[], None]] = None,
        energy_threshold: float = _DEFAULT_ENERGY_THRESHOLD,
        buffer_duration_seconds: float = DEFAULT_HANDSFREE_BUFFER_SECONDS,
        cooldown_seconds: float = DEFAULT_HANDSFREE_COOLDOWN_SECONDS,
        match_mode: str = DEFAULT_WAKE_PHRASE_MATCH_MODE,
        language: Optional[str] = None,
        should_listen: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._wake_phrase = wake_phrase
        self._wake_phrase_normalized = _normalize_text(wake_phrase)
        self._wake_phrase_tokens = self._wake_phrase_normalized.split()
        self._on_detected = on_detected
        self._energy_threshold = energy_threshold
        self._buffer_duration = buffer_duration_seconds
        self._cooldown = cooldown_seconds
        self._match_mode = match_mode
        # Language hint for STT (e.g. "en", "de"). None = auto-detect.
        self._language = language
        # Callback to check if detector should actively process audio.
        # When False, the listen loop discards frames (saves CPU, avoids
        # concurrent audio stream conflicts during recording/processing).
        self._should_listen = should_listen

        self._model = None
        self._model_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _load_model(self) -> bool:
        """Lazy-load the faster-whisper tiny model.

        Returns:
            True if model loaded successfully, False otherwise.
        """
        with self._model_lock:
            if self._model is not None:
                return True
            try:
                from faster_whisper import WhisperModel
                import model_manager

                model_path = model_manager.get_model_path("tiny")
                if model_path is None:
                    # Try to download (will be cached for future use)
                    logger.info("Tiny model not found locally. Attempting download...")
                    model_path = model_manager.download_model("tiny")

                if model_path is None:
                    logger.error("Failed to get tiny model for wake word detection.")
                    return False

                logger.info("Loading faster-whisper tiny model for wake word detection...")
                self._model = WhisperModel(
                    str(model_path),
                    device="cpu",
                    compute_type="int8",
                )
                logger.info("Wake word tiny model loaded successfully.")
                return True
            except ImportError:
                logger.error(
                    "faster-whisper is not installed. "
                    "Hands-Free mode requires the local STT build."
                )
                return False
            except Exception:
                logger.exception("Failed to load tiny model for wake word detection.")
                return False

    def start(self) -> bool:
        """Start the wake word detector.

        Loads the tiny model (if needed) and starts the listener thread.

        Returns:
            True if started successfully, False otherwise.
        """
        if self._running:
            logger.warning("Wake word detector already running.")
            return True

        if not self._load_model():
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="wake-word-listener",
        )
        self._thread.start()
        self._running = True
        logger.info("Wake word detector started (mode=%s).", self._match_mode)
        logger.debug("Wake phrase: '%s'", self._wake_phrase)
        return True

    def stop(self) -> None:
        """Stop the wake word detector."""
        if not self._running:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._running = False
        logger.info("Wake word detector stopped.")

    def unload_model(self) -> None:
        """Free the tiny whisper model from memory."""
        with self._model_lock:
            if self._model is not None:
                del self._model
                self._model = None
                logger.info("Wake word tiny model unloaded.")

    @property
    def is_running(self) -> bool:
        return self._running

    def _transcribe_buffer(self, audio: np.ndarray) -> str:
        """Run faster-whisper on a short audio buffer.

        Args:
            audio: int16 numpy array at 16 kHz.

        Returns:
            Transcribed text (may be empty).
        """
        if self._model is None:
            return ""
        try:
            # Convert int16 to float32 [-1.0, 1.0] as expected by faster-whisper
            audio_float = audio.astype(np.float32).flatten() / 32768.0
            segments, _ = self._model.transcribe(
                audio_float,
                beam_size=3,
                language=self._language,
                vad_filter=False,  # We do our own VAD
                without_timestamps=True,
                # Permissive thresholds for short wake word clips:
                # defaults reject too many valid short utterances.
                no_speech_threshold=0.9,
                log_prob_threshold=-2.0,
                # Bias the model toward recognizing the wake phrase.
                # This dramatically improves accuracy for specific words.
                initial_prompt=self._wake_phrase,
            )
            text = " ".join(seg.text for seg in segments).strip()
            return text
        except Exception:
            logger.debug("Wake word STT error.", exc_info=True)
            return ""

    def _matches_wake_phrase(self, transcript: str) -> bool:
        """Check if the transcript matches the wake phrase.

        Args:
            transcript: Raw transcript text.

        Returns:
            True if the wake phrase was detected.
        """
        if not transcript:
            return False

        norm = _normalize_text(transcript)
        if not norm:
            return False

        if self._match_mode == "startswith":
            return norm.startswith(self._wake_phrase_normalized)
        elif self._match_mode == "fuzzy":
            return _fuzzy_match(self._wake_phrase_tokens, norm.split())
        else:  # "contains" (default)
            return self._wake_phrase_normalized in norm

    def _listen_loop(self) -> None:
        """Main listening loop running on the wake word detector thread.

        Continuously reads audio frames, detects speech via RMS energy,
        and runs STT on detected speech segments to check for the wake
        phrase.
        """
        speech_buffer: list[np.ndarray] = []
        speech_start_time: Optional[float] = None
        in_speech = False
        silence_since: Optional[float] = None  # When energy last dropped

        try:
            stream = sd.InputStream(
                samplerate=DEFAULT_SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=_FRAME_SIZE,
            )
            stream.start()
        except Exception:
            logger.exception("Failed to open audio stream for wake word detection.")
            self._running = False
            return

        logger.debug("Wake word listener loop started.")

        try:
            while not self._stop_event.is_set():
                # If the app is busy (recording/processing/speaking), skip
                # audio processing to save CPU and avoid stream conflicts.
                if self._should_listen and not self._should_listen():
                    # Drain the stream to prevent buffer overflow, discard data
                    try:
                        stream.read(_FRAME_SIZE)
                    except Exception:
                        pass
                    _clear_buffer(speech_buffer)
                    in_speech = False
                    speech_start_time = None
                    silence_since = None
                    continue

                try:
                    frame, overflowed = stream.read(_FRAME_SIZE)
                except Exception:
                    if not self._stop_event.is_set():
                        logger.debug("Audio read error in wake word loop.", exc_info=True)
                    break

                if overflowed:
                    logger.debug("Wake word audio buffer overflowed.")

                # Compute RMS energy
                rms = np.sqrt(np.mean(frame.astype(np.float32) ** 2))

                if rms > self._energy_threshold:
                    # Speech detected — buffer the frame, reset silence timer
                    if not in_speech:
                        in_speech = True
                        speech_start_time = time.monotonic()
                    speech_buffer.append(frame.copy())
                    silence_since = None
                elif in_speech:
                    # Energy dropped but we're in speech — keep buffering
                    # during the grace period to bridge pauses between words
                    speech_buffer.append(frame.copy())
                    now = time.monotonic()
                    if silence_since is None:
                        silence_since = now
                    elif (now - silence_since) >= _SPEECH_END_GRACE_SECONDS:
                        # Grace period expired — speech truly ended
                        speech_duration = now - speech_start_time
                        if speech_duration >= _MIN_SPEECH_SECONDS:
                            self._check_buffer(speech_buffer)
                        # Reset
                        _clear_buffer(speech_buffer)
                        in_speech = False
                        speech_start_time = None
                        silence_since = None

                # If buffer grows too long, check what we have
                if in_speech and speech_buffer:
                    buffer_duration = len(speech_buffer) * _FRAME_SIZE / DEFAULT_SAMPLE_RATE
                    if buffer_duration >= self._buffer_duration:
                        self._check_buffer(speech_buffer)
                        _clear_buffer(speech_buffer)
                        in_speech = False
                        speech_start_time = None
                        silence_since = None
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            logger.debug("Wake word listener loop exited.")

    def _check_buffer(self, buffer: list[np.ndarray]) -> None:
        """Transcribe a speech buffer and check for the wake phrase.

        If detected, fires the callback and enters cooldown.

        Args:
            buffer: List of int16 numpy arrays.
        """
        if not buffer:
            return

        audio = np.concatenate(buffer, axis=0)
        transcript = self._transcribe_buffer(audio)

        if transcript:
            # REQ-S24: Do NOT log transcript content (privacy).
            logger.debug("Wake word candidate: %d chars", len(transcript))

        if self._matches_wake_phrase(transcript):
            logger.info("Wake word DETECTED (match_mode=%s)", self._match_mode)
            if self._on_detected:
                try:
                    self._on_detected()
                except Exception:
                    logger.exception("Error in wake word callback.")

            # Cooldown to prevent re-triggering
            logger.debug("Wake word cooldown: %.1fs", self._cooldown)
            self._stop_event.wait(self._cooldown)
