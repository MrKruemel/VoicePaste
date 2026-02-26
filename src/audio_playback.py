"""Audio playback module for TTS output.

Decodes MP3 or WAV audio bytes via miniaudio and plays through sounddevice.
Supports cancel/stop mid-playback. Runs blocking on a worker thread.

v0.6: Initial implementation for TTS playback (MP3 from ElevenLabs).
v0.7: WAV support for local Piper TTS (miniaudio handles both formats).
"""

import logging
import queue
import threading
from typing import Iterable, Optional

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

    def play_streaming(
        self,
        pcm_iter: Iterable[np.ndarray],
        sample_rate: int,
    ) -> tuple[bool, list[np.ndarray]]:
        """Play int16 PCM chunks with true parallel synthesis and playback.

        Uses a producer-consumer model: a background thread synthesizes
        clauses (advancing the generator) and enqueues PCM chunks, while
        the calling thread consumes them and writes to sounddevice. This
        ensures synthesis of clause N+1 overlaps with playback of clause N
        regardless of how long synthesis takes.

        Args:
            pcm_iter: Iterator yielding 1-D int16 numpy arrays.
            sample_rate: Audio sample rate in Hz.

        Returns:
            (completed, chunks) — completed is True if playback finished
            normally. chunks contains all received PCM arrays for
            post-playback caching.
        """
        self._stop_event.clear()
        collected_chunks: list[np.ndarray] = []
        chunk_size = sample_rate // 4  # 250ms sub-chunks
        # maxsize=2: producer can be up to 2 clauses ahead
        pcm_queue: queue.Queue[Optional[np.ndarray]] = queue.Queue(maxsize=2)
        producer_error: list[Exception] = []

        def _produce() -> None:
            """Synthesize clauses and enqueue PCM chunks."""
            try:
                for pcm_chunk in pcm_iter:
                    if self._stop_event.is_set():
                        return
                    # Put with timeout so we can check stop_event
                    while not self._stop_event.is_set():
                        try:
                            pcm_queue.put(pcm_chunk, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            except Exception as e:
                producer_error.append(e)
            finally:
                # Sentinel: signal end-of-stream
                while not self._stop_event.is_set():
                    try:
                        pcm_queue.put(None, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                else:
                    # stop_event is set — force sentinel to unblock consumer
                    try:
                        pcm_queue.put_nowait(None)
                    except queue.Full:
                        pass

        try:
            with self._lock:
                self._playing = True

            producer = threading.Thread(
                target=_produce, daemon=True, name="tts-synth-producer",
            )
            producer.start()

            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=chunk_size,
            )
            stream.start()

            try:
                while True:
                    if self._stop_event.is_set():
                        logger.info("Streaming TTS playback stopped.")
                        return (False, collected_chunks)

                    try:
                        pcm_chunk = pcm_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if pcm_chunk is None:
                        break  # Producer finished

                    collected_chunks.append(pcm_chunk)

                    # Write in 250ms sub-chunks for stop responsiveness
                    offset = 0
                    while offset < len(pcm_chunk):
                        if self._stop_event.is_set():
                            logger.info("Streaming TTS playback stopped.")
                            return (False, collected_chunks)

                        end = min(offset + chunk_size, len(pcm_chunk))
                        sub = pcm_chunk[offset:end]
                        stream.write(sub.reshape(-1, 1))
                        offset = end

                if producer_error:
                    raise producer_error[0]

                return (True, collected_chunks)

            finally:
                stream.stop()
                stream.close()
                producer.join(timeout=2.0)

        except Exception:
            logger.exception("Error during streaming TTS playback.")
            return (False, collected_chunks)

        finally:
            with self._lock:
                self._playing = False

    def stop(self) -> None:
        """Stop playback. Safe to call from any thread, even if not playing."""
        self._stop_event.set()
