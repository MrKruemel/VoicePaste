"""Toast notification and audio cue module for VoicePaste.

Provides non-intrusive user feedback via:
- Audio cues using platform_impl.play_beep (winsound on Windows,
  sounddevice sine waves on Linux).

Notifications must never steal focus (UX Principle 3).
"""

import logging
import threading

from platform_impl import play_beep

from constants import (
    APP_NAME,
    AUDIO_CUE_CANCEL_FREQ,
    AUDIO_CUE_CANCEL_GAP_MS,
    AUDIO_CUE_ERROR_FREQ,
    AUDIO_CUE_START_FREQS,
    AUDIO_CUE_STOP_FREQS,
    AUDIO_CUE_TONE_DURATION_MS,
    AUDIO_CUE_WAKEWORD_DURATION_MS,
    AUDIO_CUE_WAKEWORD_FREQS,
)

logger = logging.getLogger(__name__)


def _play_beep_sequence(frequencies: tuple[int, ...], duration_ms: int, gap_ms: int = 0) -> None:
    """Play a sequence of beep tones in a background thread.

    Uses platform_impl.play_beep which is blocking, so it runs in a
    daemon thread.

    Args:
        frequencies: Tuple of frequencies in Hz to play sequentially.
        duration_ms: Duration of each tone in milliseconds.
        gap_ms: Gap between tones in milliseconds.
    """
    def _play() -> None:
        try:
            for i, freq in enumerate(frequencies):
                play_beep(freq, duration_ms)
                if gap_ms > 0 and i < len(frequencies) - 1:
                    import time
                    time.sleep(gap_ms / 1000.0)
        except Exception:
            logger.debug("Audio cue playback failed (no speaker or system restriction).")

    thread = threading.Thread(target=_play, daemon=True, name="audio-cue")
    thread.start()


def play_recording_start_cue() -> None:
    """Play the recording-started audio cue (rising tone)."""
    _play_beep_sequence(AUDIO_CUE_START_FREQS, AUDIO_CUE_TONE_DURATION_MS)


def play_recording_stop_cue() -> None:
    """Play the recording-stopped audio cue (falling tone)."""
    _play_beep_sequence(AUDIO_CUE_STOP_FREQS, AUDIO_CUE_TONE_DURATION_MS)


def play_cancel_cue() -> None:
    """Play the cancel audio cue (two short low beeps)."""
    _play_beep_sequence(
        (AUDIO_CUE_CANCEL_FREQ, AUDIO_CUE_CANCEL_FREQ),
        AUDIO_CUE_TONE_DURATION_MS,
        AUDIO_CUE_CANCEL_GAP_MS,
    )


def play_error_cue() -> None:
    """Play the error audio cue (single low buzz)."""
    _play_beep_sequence((AUDIO_CUE_ERROR_FREQ,), 300)


def play_wakeword_cue() -> None:
    """Play the wake word detection confirmation cue (rising triple chirp)."""
    _play_beep_sequence(AUDIO_CUE_WAKEWORD_FREQS, AUDIO_CUE_WAKEWORD_DURATION_MS)


def show_toast(title: str, message: str) -> None:
    """Show a toast notification.

    Uses the pystray notification mechanism if available.
    Notifications must not steal focus (UX Principle 3).

    Args:
        title: Notification title.
        message: Notification body text.
    """
    logger.info("Toast notification: %s - %s", title, message)
