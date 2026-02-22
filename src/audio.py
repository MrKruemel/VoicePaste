"""Audio recording module for VoicePaste.

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
    ADAPTIVE_CALIBRATION_SECONDS,
    ADAPTIVE_MAX_THRESHOLD,
    ADAPTIVE_MIN_THRESHOLD,
    ADAPTIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_AUDIO_DEVICE_INDEX,
    DEFAULT_CHANNELS,
    DEFAULT_DTYPE,
    DEFAULT_SAMPLE_RATE,
    MAX_RECORDING_DURATION_SECONDS,
    MIN_RECORDING_DURATION,
)

logger = logging.getLogger(__name__)


def calibrate_rms_threshold(
    frames: list[np.ndarray],
    multiplier: float = ADAPTIVE_THRESHOLD_MULTIPLIER,
    min_threshold: float = ADAPTIVE_MIN_THRESHOLD,
    max_threshold: float = ADAPTIVE_MAX_THRESHOLD,
) -> float:
    """Compute adaptive silence threshold from ambient noise frames.

    Uses median RMS (robust to speech outliers during calibration) of the
    provided audio frames, multiplied by a factor, and clamped to a sane
    range.

    Args:
        frames: List of int16 numpy arrays captured during calibration.
        multiplier: Factor applied to median RMS to derive threshold.
        min_threshold: Minimum allowed threshold (floor).
        max_threshold: Maximum allowed threshold (ceiling).

    Returns:
        Computed RMS threshold for silence detection.
    """
    if not frames:
        return min_threshold
    rms_values = [float(np.sqrt(np.mean(f.astype(np.float32) ** 2))) for f in frames]
    baseline_rms = float(np.median(rms_values))
    threshold = baseline_rms * multiplier
    return max(min_threshold, min(threshold, max_threshold))


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
        on_silence_stop: Optional[callable] = None,
        silence_timeout_seconds: float = 3.0,
        silence_threshold_rms: float = 300.0,
        max_duration_override: Optional[int] = None,
        device: Optional[int] = DEFAULT_AUDIO_DEVICE_INDEX,
        adaptive_silence: bool = False,
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
            on_silence_stop: Optional callback invoked when silence is
                detected for longer than silence_timeout_seconds after
                speech was detected. Used by Hands-Free mode.
            silence_timeout_seconds: Seconds of silence before auto-stop
                (only used when on_silence_stop is set).
            silence_threshold_rms: RMS energy threshold for speech detection
                (int16 scale, ~300 works for typical environments).
            max_duration_override: Override MAX_RECORDING_DURATION_SECONDS
                (e.g. 120s for hands-free mode).
            device: PortAudio device index for input. None = system default.
            adaptive_silence: When True, calibrate silence threshold from
                ambient noise during the first ADAPTIVE_CALIBRATION_SECONDS
                of recording. Overrides silence_threshold_rms.
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._device = device
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()
        self._recording = False
        self._record_start_time: float = 0.0
        self._max_duration_timer: Optional[threading.Timer] = None
        self._auto_stopped: bool = False
        self._on_auto_stop = on_auto_stop
        # Silence detection (Hands-Free mode)
        self._on_silence_stop = on_silence_stop
        self._silence_timeout = silence_timeout_seconds
        self._silence_threshold_rms = silence_threshold_rms
        self._speech_detected: bool = False
        self._silence_start: Optional[float] = None
        self._silence_fired: bool = False
        self._max_duration_override = max_duration_override
        # Adaptive silence detection
        self._adaptive_silence = adaptive_silence
        self._calibrating = False
        self._calibration_frames: list[np.ndarray] = []
        self._calibration_start: float = 0.0

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

        # Silence detection (only when callback is registered)
        if self._on_silence_stop is not None and not self._silence_fired:
            # Adaptive calibration phase: collect ambient noise frames
            if self._calibrating:
                self._calibration_frames.append(indata.copy())
                elapsed = time.monotonic() - self._calibration_start
                if elapsed >= ADAPTIVE_CALIBRATION_SECONDS:
                    self._silence_threshold_rms = calibrate_rms_threshold(
                        self._calibration_frames,
                    )
                    self._calibrating = False
                    self._calibration_frames = []  # Free memory
                    logger.info(
                        "Adaptive silence calibration complete. "
                        "Threshold: %.1f RMS (%.1fs ambient noise).",
                        self._silence_threshold_rms,
                        elapsed,
                    )
                return  # Skip normal silence detection during calibration

            rms = np.sqrt(np.mean(indata.astype(np.float32) ** 2))
            if rms > self._silence_threshold_rms:
                self._speech_detected = True
                self._silence_start = None
            elif self._speech_detected:
                now = time.monotonic()
                if self._silence_start is None:
                    self._silence_start = now
                elif (now - self._silence_start) >= self._silence_timeout:
                    self._silence_fired = True
                    # Dispatch off the PortAudio callback thread to avoid
                    # blocking on state transitions, tray updates, logging.
                    threading.Thread(
                        target=self._fire_silence_stop,
                        daemon=True,
                        name="silence-dispatch",
                    ).start()

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
                # Check if the target input device is available
                device_info = sd.query_devices(
                    self._device if self._device is not None else None,
                    kind="input",
                )
                logger.debug(
                    "Using input device: %s (index=%s)",
                    device_info.get("name", "unknown") if isinstance(device_info, dict) else "default",
                    self._device,
                )
            except sd.PortAudioError:
                logger.error("No microphone detected. Cannot start recording.")
                return False

            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype=self.dtype,
                    device=self._device,
                    callback=self._audio_callback,
                )
                self._stream.start()
                self._recording = True
                self._auto_stopped = False
                self._record_start_time = time.monotonic()
                # Reset silence detection state
                self._speech_detected = False
                self._silence_start = None
                self._silence_fired = False

                # Adaptive silence calibration: collect ambient noise at
                # recording start to compute a dynamic threshold.
                if self._adaptive_silence and self._on_silence_stop is not None:
                    self._calibrating = True
                    self._calibration_frames = []
                    self._calibration_start = time.monotonic()
                    logger.info(
                        "Starting ambient noise calibration (%.1fs)...",
                        ADAPTIVE_CALIBRATION_SECONDS,
                    )

                # Start max-duration timer to auto-stop recording
                max_dur = self._max_duration_override or MAX_RECORDING_DURATION_SECONDS
                self._max_duration_timer = threading.Timer(
                    max_dur,
                    self._on_max_duration_reached,
                )
                self._max_duration_timer.daemon = True
                self._max_duration_timer.start()

                logger.info(
                    "Recording started. Max duration: %d seconds.",
                    max_dur,
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

    def _fire_silence_stop(self) -> None:
        """Fire the silence stop callback off the audio thread."""
        logger.info(
            "Silence timeout reached (%.1fs). Auto-stopping.",
            self._silence_timeout,
        )
        try:
            self._on_silence_stop()
        except Exception:
            logger.exception("Error in on_silence_stop callback.")

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
