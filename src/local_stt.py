"""Local speech-to-text backend using faster-whisper (CTranslate2).

Provides offline transcription without sending audio to any cloud API.
Requires the faster-whisper package to be installed (optional dependency).

The model is loaded lazily on first transcription call to avoid consuming
memory when the user has not yet initiated a recording.

Thread safety:
    WhisperModel.transcribe() is NOT thread-safe. However, our architecture
    guarantees that only one pipeline thread calls transcribe() at a time
    (state machine enforces PROCESSING is single-threaded). Model loading
    uses a Lock to prevent races between lazy-load and pre-load calls.

REQ-S09: Audio is never written to disk.
REQ-S24/S25: Transcript content is never logged.
"""

import io
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from constants import (
    DEFAULT_SAMPLE_RATE,
    LOCAL_STT_DEFAULT_BEAM_SIZE,
    LOCAL_STT_DEFAULT_COMPUTE_TYPE,
    LOCAL_STT_DEFAULT_DEVICE,
    LOCAL_STT_DEFAULT_MODEL_SIZE,
)
from stt import STTError

logger = logging.getLogger(__name__)

# Sentinel to track whether faster-whisper is available
_faster_whisper_available: Optional[bool] = None


def _is_frozen() -> bool:
    """Check if we are running inside a PyInstaller frozen executable.

    Returns:
        True if running as a frozen .exe, False otherwise.
    """
    return getattr(sys, "frozen", False)


def is_faster_whisper_available() -> bool:
    """Check if the faster-whisper package is installed and importable.

    Caches the result after the first call. Also checks for the CTranslate2
    native library which is the common failure point (missing DLLs).

    Returns:
        True if faster-whisper can be imported, False otherwise.
    """
    global _faster_whisper_available
    if _faster_whisper_available is not None:
        return _faster_whisper_available

    try:
        import faster_whisper  # noqa: F401

        _faster_whisper_available = True
        logger.info(
            "faster-whisper is available (version: %s).",
            getattr(faster_whisper, "__version__", "unknown"),
        )
    except ImportError as e:
        _faster_whisper_available = False
        error_msg = str(e)
        # Provide specific guidance for common DLL failures
        if "DLL" in error_msg or "dll" in error_msg:
            logger.warning(
                "faster-whisper import failed due to missing DLL: %s. "
                "This often means the Visual C++ Redistributable is not "
                "installed. Download it from: "
                "https://aka.ms/vs/17/release/vc_redist.x64.exe",
                error_msg,
            )
        elif "ctranslate2" in error_msg.lower():
            logger.warning(
                "faster-whisper import failed because ctranslate2 is missing: %s. "
                "Install with: pip install ctranslate2",
                error_msg,
            )
        else:
            logger.info(
                "faster-whisper is not installed. Local STT unavailable. "
                "Error: %s",
                error_msg,
            )
    except OSError as e:
        # OSError can occur when a native .dll/.so is present but cannot
        # be loaded (wrong architecture, missing dependency).
        _faster_whisper_available = False
        logger.warning(
            "faster-whisper native library failed to load: %s: %s. "
            "The Visual C++ Redistributable may be missing or the wrong "
            "architecture. Download the x64 version from: "
            "https://aka.ms/vs/17/release/vc_redist.x64.exe",
            type(e).__name__,
            e,
        )

    return _faster_whisper_available


def _wav_bytes_to_float32(wav_data: bytes) -> np.ndarray:
    """Convert WAV file bytes to a float32 numpy array normalized to [-1, 1].

    Handles our specific WAV format: 16-bit PCM, mono, 16kHz.
    Does NOT write to disk (REQ-S09).

    Args:
        wav_data: Complete WAV file as bytes (including header).

    Returns:
        1-D numpy array of float32 samples.

    Raises:
        STTError: If the WAV data is invalid or in an unsupported format.
    """
    try:
        import wave

        buf = io.BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()

            if sampwidth != 2:
                raise STTError(
                    f"Unsupported WAV sample width: {sampwidth} bytes "
                    f"(expected 2 for 16-bit PCM)."
                )

            raw_frames = wf.readframes(n_frames)

        # Convert 16-bit signed integers to float32 in [-1, 1]
        int16_array = np.frombuffer(raw_frames, dtype=np.int16)

        if n_channels > 1:
            # Downmix to mono by averaging channels
            int16_array = int16_array.reshape(-1, n_channels).mean(axis=1)

        float32_array = int16_array.astype(np.float32) / 32768.0
        return float32_array

    except STTError:
        raise
    except Exception as e:
        raise STTError(
            f"Failed to decode WAV audio: {type(e).__name__}: {e}"
        ) from e


class LocalWhisperSTT:
    """Local speech-to-text using faster-whisper (CTranslate2 engine).

    Provides offline transcription with no cloud dependency. The Whisper
    model is loaded lazily on first transcription call to conserve memory
    when the app starts.

    Implements the STTBackend Protocol.

    Attributes:
        model_size: Whisper model size (tiny, base, small, medium, large-v3).
        device: Inference device ("cpu", "cuda", or "auto").
        compute_type: Quantization type ("int8", "float16", "float32", "auto").
    """

    def __init__(
        self,
        model_size: str = LOCAL_STT_DEFAULT_MODEL_SIZE,
        device: str = LOCAL_STT_DEFAULT_DEVICE,
        compute_type: str = LOCAL_STT_DEFAULT_COMPUTE_TYPE,
        model_path: Optional[Path] = None,
        beam_size: int = LOCAL_STT_DEFAULT_BEAM_SIZE,
    ) -> None:
        """Initialize the local Whisper STT backend.

        The model is NOT loaded during __init__. It is loaded lazily on
        the first call to transcribe(). Call load_model() explicitly to
        pre-load (e.g., on app startup in a background thread).

        Args:
            model_size: Whisper model size identifier.
            device: Compute device for inference.
            compute_type: Weight quantization type.
            model_path: Explicit path to model directory. If None, uses
                the model_size string (faster-whisper auto-downloads).
            beam_size: Beam search width (lower = faster, higher = more accurate).

        Raises:
            STTError: If faster-whisper is not installed.
        """
        if not is_faster_whisper_available():
            raise STTError(
                "faster-whisper is not installed. "
                "Install it with: pip install faster-whisper"
            )

        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model_path = model_path
        self._beam_size = beam_size
        self._model = None  # Lazy loaded
        self._model_loaded = False
        self._load_lock = threading.Lock()

        logger.info(
            "LocalWhisperSTT initialized: model=%s, device=%s, "
            "compute_type=%s, beam_size=%d, model_path=%s",
            model_size,
            device,
            compute_type,
            beam_size,
            model_path or "(auto/cache)",
        )

    def load_model(self) -> None:
        """Load the Whisper model into memory.

        This can take several seconds (especially on first load from disk).
        Call this in a background thread to avoid blocking the UI.
        Thread-safe: uses a Lock to prevent concurrent loads.

        When ``model_path`` is None (model not pre-downloaded via
        model_manager), the behaviour depends on whether we are running
        in a frozen PyInstaller exe:
          - Frozen: raises STTError immediately telling the user to
            download the model, because auto-download via HF Hub will
            not work reliably without the full network/SSL stack.
          - Script: falls back to the model_size string which triggers
            faster-whisper's built-in auto-download via CTranslate2.

        Raises:
            STTError: If the model cannot be loaded (not downloaded, OOM,
                missing DLLs, etc.).
        """
        with self._load_lock:
            if self._model_loaded and self._model is not None:
                logger.debug("Model already loaded, skipping.")
                return

            # Guard: refuse to auto-download in a frozen executable.
            # The HF Hub / CTranslate2 auto-download path depends on
            # network libraries and cache directories that may not work
            # correctly inside a PyInstaller --onefile bundle.
            if self._model_path is None and _is_frozen():
                logger.error(
                    "Local model '%s' is not downloaded and we are running "
                    "in a frozen executable. Auto-download is not supported.",
                    self._model_size,
                )
                raise STTError(
                    f"Whisper model '{self._model_size}' is not downloaded.\n\n"
                    f"Please download the model first:\n"
                    f"  Right-click the tray icon > Settings > "
                    f"Transcription > Download Model\n\n"
                    f"The model only needs to be downloaded once."
                )

            if self._model_path is None:
                logger.warning(
                    "No pre-downloaded model path for '%s'. "
                    "faster-whisper will attempt auto-download. "
                    "This may be slow on first run.",
                    self._model_size,
                )

            try:
                from faster_whisper import WhisperModel

                model_source = (
                    str(self._model_path) if self._model_path else self._model_size
                )

                logger.info(
                    "Loading Whisper model: source=%s, device=%s, "
                    "compute_type=%s, model_path=%s...",
                    model_source,
                    self._device,
                    self._compute_type,
                    self._model_path or "(auto/cache)",
                )
                t0 = time.monotonic()

                self._model = WhisperModel(
                    model_source,
                    device=self._device,
                    compute_type=self._compute_type,
                    download_root=None,
                )

                elapsed = time.monotonic() - t0
                logger.info("Whisper model loaded in %.1f seconds.", elapsed)
                self._model_loaded = True

            except ImportError as e:
                error_msg = str(e)
                if "DLL" in error_msg or "dll" in error_msg:
                    raise STTError(
                        "A required DLL for faster-whisper/CTranslate2 is "
                        "missing.\n\n"
                        "Install the Visual C++ Redistributable (x64):\n"
                        "https://aka.ms/vs/17/release/vc_redist.x64.exe"
                    ) from e
                if "ctranslate2" in error_msg.lower():
                    raise STTError(
                        "The CTranslate2 library is missing or could not be "
                        "loaded.\n\n"
                        "Reinstall faster-whisper: pip install faster-whisper"
                    ) from e
                raise STTError(
                    "faster-whisper is not properly installed.\n\n"
                    f"Import error: {error_msg}"
                ) from e

            except OSError as e:
                # OSError covers missing DLLs and file-not-found at the
                # native library level (e.g., libctranslate2.dll).
                error_msg = str(e)
                if "model.bin" in error_msg or "config.json" in error_msg:
                    raise STTError(
                        f"Whisper model '{self._model_size}' files are "
                        f"missing or corrupted.\n\n"
                        f"Please re-download the model via Settings > "
                        f"Transcription > Download Model."
                    ) from e
                raise STTError(
                    f"Operating system error loading Whisper model: "
                    f"{type(e).__name__}: {error_msg}\n\n"
                    f"If the error mentions a DLL, install the Visual C++ "
                    f"Redistributable (x64):\n"
                    f"https://aka.ms/vs/17/release/vc_redist.x64.exe"
                ) from e

            except MemoryError as e:
                raise STTError(
                    f"Not enough memory to load Whisper model "
                    f"'{self._model_size}'.\n\n"
                    f"Try a smaller model (tiny or base) or close other "
                    f"applications."
                ) from e

            except RuntimeError as e:
                # CTranslate2 raises RuntimeError for many internal issues:
                # unsupported compute type, model format mismatch, etc.
                error_msg = str(e)
                logger.error(
                    "CTranslate2 RuntimeError during model load: %s",
                    error_msg,
                )
                if "compute type" in error_msg.lower():
                    raise STTError(
                        f"Compute type '{self._compute_type}' is not "
                        f"supported on this device ({self._device}).\n\n"
                        f"Try changing device to 'cpu' and compute type "
                        f"to 'int8' in Settings."
                    ) from e
                if "cuda" in error_msg.lower() or "gpu" in error_msg.lower():
                    raise STTError(
                        f"CUDA/GPU error while loading the model.\n\n"
                        f"Your GPU may not be supported or CUDA is not "
                        f"installed. Try setting device to 'cpu' in "
                        f"Settings.\n\n"
                        f"Detail: {error_msg}"
                    ) from e
                raise STTError(
                    f"Failed to load Whisper model: {error_msg}\n\n"
                    f"Try re-downloading the model via Settings > "
                    f"Transcription > Download Model."
                ) from e

            except Exception as e:
                error_msg = str(e)
                if (
                    "No such file or directory" in error_msg
                    or "not found" in error_msg.lower()
                ):
                    raise STTError(
                        f"Whisper model '{self._model_size}' not found.\n\n"
                        f"Please download it first via Settings > "
                        f"Transcription > Download Model."
                    ) from e
                logger.error(
                    "Unexpected error loading Whisper model: %s: %s",
                    type(e).__name__,
                    error_msg,
                )
                raise STTError(
                    f"Failed to load Whisper model: "
                    f"{type(e).__name__}: {error_msg}"
                ) from e

    def unload_model(self) -> None:
        """Unload the Whisper model from memory.

        Frees the memory used by the model. The model can be reloaded
        by calling load_model() or by the next transcribe() call.
        """
        if self._model is not None:
            logger.info("Unloading Whisper model '%s'...", self._model_size)
            del self._model
            self._model = None
            self._model_loaded = False
            # Encourage garbage collection of the large model tensors
            import gc

            gc.collect()
            logger.info("Whisper model unloaded.")

    @property
    def is_model_loaded(self) -> bool:
        """Whether the Whisper model is currently loaded in memory."""
        return self._model_loaded and self._model is not None

    def transcribe(self, audio_data: bytes, language: str = "de") -> str:
        """Transcribe audio bytes to text using the local Whisper model.

        Loads the model lazily on first call if not already loaded.

        Args:
            audio_data: WAV audio file bytes (in-memory, never from disk).
            language: Language code for transcription (default 'de' for German).

        Returns:
            Transcribed text string.

        Raises:
            STTError: If transcription fails (model not available, decode error, etc.).
        """
        logger.info("Local STT: transcribing %d bytes of audio...", len(audio_data))

        # Lazy load model on first use
        if not self._model_loaded or self._model is None:
            self.load_model()

        try:
            # Convert WAV bytes to float32 numpy array
            audio_array = _wav_bytes_to_float32(audio_data)

            logger.debug(
                "Audio converted to float32: %d samples, %.1f seconds.",
                len(audio_array),
                len(audio_array) / DEFAULT_SAMPLE_RATE,
            )

            t0 = time.monotonic()

            # Run transcription
            segments, info = self._model.transcribe(
                audio_array,
                language=language,
                beam_size=self._beam_size,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                ),
            )

            # Collect all segments into a single string.
            # segments is a generator -- we must consume it to trigger inference.
            transcript_parts = []
            for segment in segments:
                transcript_parts.append(segment.text.strip())

            transcript = " ".join(transcript_parts).strip()

            elapsed = time.monotonic() - t0
            audio_duration = len(audio_array) / DEFAULT_SAMPLE_RATE

            # REQ-S24/S25: Do not log transcript content, only metadata
            logger.info(
                "Local STT complete: %d chars, %.1fs audio, %.1fs inference "
                "(%.1fx realtime). Detected language: %s (prob=%.2f).",
                len(transcript),
                audio_duration,
                elapsed,
                audio_duration / max(elapsed, 0.001),
                info.language,
                info.language_probability,
            )

            return transcript

        except STTError:
            raise
        except MemoryError as e:
            logger.error("Out of memory during local transcription.")
            raise STTError(
                "Out of memory during transcription.\n\n"
                "Try a smaller model (tiny or base) or a shorter recording."
            ) from e
        except RuntimeError as e:
            # CTranslate2 raises RuntimeError for inference failures
            # (e.g., corrupted model, unsupported audio format, CUDA OOM).
            error_msg = str(e)
            logger.error(
                "CTranslate2 RuntimeError during transcription: %s",
                error_msg,
            )
            if "cuda" in error_msg.lower() or "gpu" in error_msg.lower():
                raise STTError(
                    "GPU error during transcription.\n\n"
                    "Try setting device to 'cpu' in Settings, or use "
                    "a smaller model.\n\n"
                    f"Detail: {error_msg}"
                ) from e
            if "out of memory" in error_msg.lower():
                raise STTError(
                    "Out of memory during transcription.\n\n"
                    "Try a smaller model (tiny or base) or a shorter "
                    "recording."
                ) from e
            raise STTError(
                f"Local transcription failed: {error_msg}"
            ) from e
        except Exception as e:
            logger.error("Local STT error: %s: %s", type(e).__name__, e)
            raise STTError(
                f"Local transcription failed: {type(e).__name__}: {e}"
            ) from e
