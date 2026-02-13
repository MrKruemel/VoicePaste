"""Audio recording module for the Voice-to-Summary Paste Tool.

Captures microphone audio using sounddevice into an in-memory buffer.
REQ-S09: Audio is NEVER written to disk.
REQ-S11: Audio data is NEVER logged.
"""

import io
import logging
import threading
import time
import wave
from typing import Optional

import numpy as np
import sounddevice as sd

from constants import (
    DEFAULT_CHANNELS,
    DEFAULT_DTYPE,
    DEFAULT_SAMPLE_RATE,
    MAX_RECORDING_DURATION_SECONDS,
    MIN_RECORDING_DURATION,
)

logger = logging.getLogger(__name__)


class AudioRecorder:
    """Records audio from the default microphone into an in-memory WAV buffer.

    Audio is stored purely in memory (numpy arrays -> BytesIO WAV).
    No audio data ever touches the filesystem.

    Attributes:
        sample_rate: Sample rate in Hz.
        channels: Number of audio channels.
        dtype: NumPy dtype for audio samples.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        dtype: str = DEFAULT_DTYPE,
        on_auto_stop: Optional[callable] = None,
    ) -> None:
        """Initialize the audio recorder.

        Args:
            sample_rate: Sample rate in Hz (default 16000).
            channels: Number of audio channels (default 1, mono).
            dtype: NumPy dtype string for samples (default 'int16').
            on_auto_stop: Optional callback invoked when recording is
                auto-stopped due to max duration. Called from the timer
                thread with no arguments. The callback is responsible
                for triggering the processing pipeline.
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()
        self._recording = False
        self._record_start_time: float = 0.0
        self._max_duration_timer: Optional[threading.Timer] = None
        self._auto_stopped: bool = False
        self._on_auto_stop = on_auto_stop

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback invoked by sounddevice for each audio block.

        Args:
            indata: Recorded audio data as numpy array.
            frames: Number of frames in this block.
            time_info: Timing information (unused).
            status: Status flags from PortAudio.
        """
        if status:
            logger.warning("Audio callback status: %s", status)
        self._frames.append(indata.copy())

    def start(self) -> bool:
        """Start recording audio from the default microphone.

        Returns:
            True if recording started successfully, False otherwise.
        """
        with self._lock:
            if self._recording:
                logger.warning("Recording already in progress, ignoring start request.")
                return False

            self._frames = []

            try:
                # Check if any input device is available
                device_info = sd.query_devices(kind="input")
                logger.debug(
                    "Using input device: %s",
                    device_info.get("name", "unknown") if isinstance(device_info, dict) else "default",
                )
            except sd.PortAudioError:
                logger.error("No microphone detected. Cannot start recording.")
                return False

            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype=self.dtype,
                    callback=self._audio_callback,
                )
                self._stream.start()
                self._recording = True
                self._auto_stopped = False
                self._record_start_time = time.monotonic()

                # Start max-duration timer to auto-stop recording
                self._max_duration_timer = threading.Timer(
                    MAX_RECORDING_DURATION_SECONDS,
                    self._on_max_duration_reached,
                )
                self._max_duration_timer.daemon = True
                self._max_duration_timer.start()

                logger.info(
                    "Recording started. Max duration: %d seconds.",
                    MAX_RECORDING_DURATION_SECONDS,
                )
                return True
            except sd.PortAudioError as e:
                logger.error("Failed to start audio stream: %s", e)
                self._stream = None
                return False

    def stop(self) -> Optional[bytes]:
        """Stop recording and return the audio data as WAV bytes.

        REQ-S09: Audio stays in memory. No disk writes.

        Returns:
            WAV file bytes if recording was active and long enough,
            None if no recording was active or recording was too short.
        """
        with self._lock:
            if not self._recording or self._stream is None:
                logger.warning("No active recording to stop.")
                return None

            duration = time.monotonic() - self._record_start_time

            # Cancel the max-duration timer if it has not fired yet
            if self._max_duration_timer is not None:
                self._max_duration_timer.cancel()
                self._max_duration_timer = None

            try:
                self._stream.stop()
                self._stream.close()
            except sd.PortAudioError as e:
                logger.error("Error stopping audio stream: %s", e)
            finally:
                self._stream = None
                self._recording = False

            logger.info("Recording stopped. Duration: %.1f seconds.", duration)

            if duration < MIN_RECORDING_DURATION:
                logger.info(
                    "Recording too short (%.1fs < %.1fs). Discarding.",
                    duration,
                    MIN_RECORDING_DURATION,
                )
                self._clear_frames()
                return None

            if not self._frames:
                logger.warning("No audio frames captured.")
                self._clear_frames()
                return None

            # Convert frames to WAV bytes in memory
            try:
                audio_data = np.concatenate(self._frames, axis=0)
                wav_bytes = self._numpy_to_wav_bytes(audio_data)
                logger.info(
                    "Audio captured: %.1f seconds, %d bytes WAV.",
                    duration,
                    len(wav_bytes),
                )
                return wav_bytes
            except Exception as e:
                logger.error("Failed to encode audio to WAV: %s", e)
                return None
            finally:
                self._clear_frames()

    def _clear_frames(self) -> None:
        """Clear audio frame buffers from memory.

        REQ-S10: Explicitly clear audio buffers.
        """
        for frame in self._frames:
            frame.fill(0)
        self._frames.clear()

    def _numpy_to_wav_bytes(self, audio_data: np.ndarray) -> bytes:
        """Convert a numpy audio array to WAV bytes in memory.

        Args:
            audio_data: Audio samples as numpy array.

        Returns:
            Complete WAV file as bytes.
        """
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data.tobytes())
        return buffer.getvalue()

    @property
    def is_recording(self) -> bool:
        """Whether the recorder is currently capturing audio."""
        return self._recording

    @property
    def auto_stopped(self) -> bool:
        """Whether the last recording was auto-stopped due to max duration.

        This flag is set when the max-duration timer fires and remains
        True until the next call to start(). Callers should check this
        after stop() returns to determine if the recording was ended
        by the timer rather than by the user.
        """
        return self._auto_stopped

    def _on_max_duration_reached(self) -> None:
        """Callback fired by the max-duration timer.

        Sets the auto_stopped flag and invokes the on_auto_stop callback
        so the application can trigger the stop-and-process pipeline.
        This method is called from the Timer thread.

        The callback (typically VoicePasteApp._stop_recording_and_process)
        is responsible for calling stop() on this recorder and running
        the STT/summarization pipeline.
        """
        logger.warning(
            "Max recording duration reached (%d seconds). Auto-stopping.",
            MAX_RECORDING_DURATION_SECONDS,
        )
        self._auto_stopped = True

        if self._on_auto_stop is not None:
            try:
                self._on_auto_stop()
            except Exception:
                logger.exception("Error in on_auto_stop callback.")
