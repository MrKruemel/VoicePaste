"""Audio playback module for TTS output.

Decodes MP3 or WAV audio bytes via miniaudio and plays through sounddevice.
Supports cancel/stop mid-playback. Runs blocking on a worker thread.

v0.6: Initial implementation for TTS playback (MP3 from ElevenLabs).
v0.7: WAV support for local Piper TTS (miniaudio handles both formats).
"""

import logging
import threading
from typing import Optional

import miniaudio
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Plays audio bytes (MP3/WAV) through the default output device.

    Uses miniaudio to decode compressed audio to PCM, then plays via
    sounddevice OutputStream. Supports mid-playback cancellation.

    Thread-safe: play() blocks until done or stopped. stop() can be
    called from any thread.
    """

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._playing = False
        self._lock = threading.Lock()

    @property
    def is_playing(self) -> bool:
        """Whether audio is currently playing."""
        return self._playing

    def play(self, audio_data: bytes, sample_rate: int = 44100) -> bool:
        """Play audio data (MP3 or WAV bytes) through the default output device.

        Blocks until playback completes or stop() is called. miniaudio
        auto-detects the format from the audio data header.

        Args:
            audio_data: Audio bytes (MP3 from ElevenLabs, WAV from Piper).
            sample_rate: Expected sample rate (default 44100 for ElevenLabs MP3).

        Returns:
            True if playback completed normally, False if stopped/error.
        """
        self._stop_event.clear()

        try:
            # Decode MP3 to PCM using miniaudio
            decoded = miniaudio.decode(
                audio_data,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=sample_rate,
            )

            # Convert to numpy array for sounddevice
            samples = np.frombuffer(decoded.samples, dtype=np.int16)
            actual_rate = decoded.sample_rate

            logger.info(
                "Playing TTS audio: %d samples, %d Hz, %.1f seconds",
                len(samples),
                actual_rate,
                len(samples) / actual_rate,
            )

            with self._lock:
                self._playing = True

            # Play in chunks so we can check stop_event
            chunk_size = actual_rate // 4  # 250ms chunks
            offset = 0

            stream = sd.OutputStream(
                samplerate=actual_rate,
                channels=1,
                dtype="int16",
                blocksize=chunk_size,
            )
            stream.start()

            try:
                while offset < len(samples):
                    if self._stop_event.is_set():
                        logger.info("TTS playback stopped by user.")
                        return False

                    end = min(offset + chunk_size, len(samples))
                    chunk = samples[offset:end]
                    stream.write(chunk.reshape(-1, 1))
                    offset = end

                return True

            finally:
                stream.stop()
                stream.close()

        except Exception:
            logger.exception("Error during TTS audio playback.")
            return False

        finally:
            with self._lock:
                self._playing = False

    def stop(self) -> None:
        """Stop playback. Safe to call from any thread, even if not playing."""
        self._stop_event.set()
