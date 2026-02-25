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
    LOCAL_STT_DEFAULT_VAD_FILTER,
    LOCAL_STT_FROZEN_VAD_FILTER,
)
from stt import STTError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stub out PyAV (av) so faster-whisper can import without the real package.
#
# faster_whisper.__init__ does ``from faster_whisper.audio import decode_audio``
# which triggers a top-level ``import av`` inside audio.py.  PyAV bundles the
# FFmpeg native libraries and adds ~119 MB to a PyInstaller build, but
# VoicePaste never calls decode_audio() -- we feed pre-decoded PCM float32
# arrays (via _wav_bytes_to_float32) directly to WhisperModel.transcribe().
#
# By inserting a lightweight dummy module into sys.modules we let
# faster_whisper.audio import without error while keeping the binary small.
# If ``av`` IS genuinely installed (e.g. running from source), we leave it
# alone.
# ---------------------------------------------------------------------------
if "av" not in sys.modules:
    try:
        import av  # noqa: F401 -- real package present, nothing to do
    except ImportError:
        import types

        _dummy_av = types.ModuleType("av")
        _dummy_av.__version__ = "0.0.0-stub"  # type: ignore[attr-defined]
        _dummy_av.__path__ = []  # type: ignore[attr-defined]  # mark as package
        sys.modules["av"] = _dummy_av
        logger.debug(
            "Injected dummy 'av' module -- PyAV is not installed. "
            "This is expected; VoicePaste does not use decode_audio()."
        )

# Sentinel to track whether faster-whisper is available
_faster_whisper_available: Optional[bool] = None


def _is_frozen() -> bool:
    """Check if we are running inside a PyInstaller frozen executable.

    Returns:
        True if running as a frozen .exe, False otherwise.
    """
    return getattr(sys, "frozen", False)


def _configure_onnxruntime_for_frozen() -> None:
    """Configure onnxruntime to use only CPUExecutionProvider in frozen exes.

    When running inside a PyInstaller --onefile bundle, onnxruntime unpacks
    into a _MEI* temp directory.  The automatic execution provider discovery
    can fail (e.g., trying to load CUDA providers that are not bundled),
    which sometimes causes a native crash (segfault) with no Python traceback.

    This function forces onnxruntime to:
      1. Disable telemetry (avoid network calls from frozen exe).
      2. Log a diagnostic message about the provider configuration.

    The actual provider restriction ('CPUExecutionProvider' only) is done
    at the InferenceSession level by faster-whisper's SileroVAD code.
    We cannot patch that directly, but we CAN set environment variables
    that onnxruntime respects before any session is created.

    This function is safe to call even if onnxruntime is not installed
    (catches ImportError).
    """
    if not _is_frozen():
        return

    try:
        import os

        # ORT_DISABLE_ALL_TELEMETRY: prevent onnxruntime from making
        # network calls during provider init (belt-and-suspenders).
        os.environ.setdefault("ORT_DISABLE_ALL_TELEMETRY", "1")

        import onnxruntime as ort

        # Log available providers for diagnostics
        available = ort.get_available_providers()
        logger.info(
            "onnxruntime providers in frozen exe: %s (version %s)",
            available,
            getattr(ort, "__version__", "unknown"),
        )
    except ImportError:
        logger.debug(
            "onnxruntime not available; Silero VAD will not work."
        )
    except Exception as e:
        logger.warning(
            "Failed to configure onnxruntime for frozen exe: %s: %s",
            type(e).__name__,
            e,
        )


def _resolve_device() -> str:
    """Resolve 'auto' device to 'cuda' or 'cpu' safely.

    CTranslate2's own auto-detection can cause a native crash (segfault)
    when CUDA libraries are partially installed (e.g. the NVIDIA driver is
    present but cuDNN is not).  We pre-check that both an NVIDIA GPU *and*
    a usable cuDNN library are present before allowing CUDA.

    In a frozen PyInstaller executable, CUDA is always disabled because:
    - The bundled onnxruntime has no CUDA providers (Silero VAD would fail)
    - CUDA runtime libraries (libcudart, libcublas) aren't in the bundle
    - CTranslate2 may segfault when it can't find CUDA runtime at inference

    Returns:
        'cuda' if NVIDIA GPU + cuDNN are available (non-frozen), 'cpu' otherwise.
    """
    # In a frozen PyInstaller bundle, always force CPU.
    # CUDA runtime libs aren't bundled and onnxruntime is CPU-only.
    if _is_frozen():
        logger.info(
            "Frozen executable detected — forcing device='cpu'. "
            "Run from source for CUDA GPU acceleration."
        )
        return "cpu"

    import ctypes
    import ctypes.util

    # Step 1: check for NVIDIA GPU
    if sys.platform == "win32":
        import shutil
        has_gpu = shutil.which("nvidia-smi") is not None
    else:
        has_gpu = Path("/dev/nvidia0").exists()

    if not has_gpu:
        logger.info("No NVIDIA GPU detected — using device='cpu'.")
        return "cpu"

    # Step 2: check for cuDNN (required by CTranslate2 for CUDA inference).
    # Without cuDNN, CTranslate2 segfaults during model load.
    cudnn_found = ctypes.util.find_library("cudnn") is not None
    if not cudnn_found:
        # Try common sonames directly
        for soname in ("libcudnn.so.9", "libcudnn.so.8", "libcudnn.so"):
            try:
                ctypes.cdll.LoadLibrary(soname)
                cudnn_found = True
                break
            except OSError:
                continue

    if not cudnn_found:
        logger.info(
            "NVIDIA GPU found but cuDNN is not installed — using device='cpu'. "
            "Install cuDNN for GPU acceleration."
        )
        return "cpu"

    logger.info("NVIDIA GPU + cuDNN detected — using device='cuda'.")
    return "cuda"


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


def _flush_all_log_handlers() -> None:
    """Flush all handlers on the root logger and this module's logger.

    This ensures log messages are written to disk before entering native
    C++ code (CTranslate2, onnxruntime) that may crash the process.
    A native segfault would lose any buffered log output.
    """
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    for handler in logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass


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
        vad_filter: Whether to run Silero VAD before Whisper inference.
    """

    def __init__(
        self,
        model_size: str = LOCAL_STT_DEFAULT_MODEL_SIZE,
        device: str = LOCAL_STT_DEFAULT_DEVICE,
        compute_type: str = LOCAL_STT_DEFAULT_COMPUTE_TYPE,
        model_path: Optional[Path] = None,
        beam_size: int = LOCAL_STT_DEFAULT_BEAM_SIZE,
        vad_filter: bool = LOCAL_STT_DEFAULT_VAD_FILTER,
        initial_prompt: str = "",
    ) -> None:
        """Initialize the local Whisper STT backend.

        The model is NOT loaded during __init__. It is loaded lazily on
        the first call to transcribe(). Call load_model() explicitly to
        pre-load (e.g., on app startup in a background thread).

        When running as a frozen PyInstaller executable, VAD is
        auto-disabled by default (configurable via config.toml) to avoid
        a known onnxruntime native crash in the _MEI* temp directory.

        Args:
            model_size: Whisper model size identifier.
            device: Compute device for inference.
            compute_type: Weight quantization type.
            model_path: Explicit path to model directory. If None, uses
                the model_size string (faster-whisper auto-downloads).
            beam_size: Beam search width (lower = faster, higher = more accurate).
            vad_filter: Enable Silero VAD to filter silence before Whisper.
                Defaults to True in script mode, False in frozen exe.
            initial_prompt: Optional vocabulary hints / context for Whisper.

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
        self._vad_filter = vad_filter
        self._initial_prompt = initial_prompt.strip() if initial_prompt else ""
        self._model = None  # Lazy loaded
        self._model_loaded = False
        self.detected_language: str | None = None  # Set after transcription
        self._load_lock = threading.Lock()

        # Pre-configure onnxruntime for frozen exe before any model load
        if self._vad_filter:
            _configure_onnxruntime_for_frozen()

        logger.info(
            "LocalWhisperSTT initialized: model=%s, device=%s, "
            "compute_type=%s, beam_size=%d, vad_filter=%s, model_path=%s",
            model_size,
            device,
            compute_type,
            beam_size,
            vad_filter,
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

                # Resolve "auto" device safely: CTranslate2's auto-detection
                # can cause a native crash (segfault) when CUDA libs are
                # partially installed (e.g. libcudnn missing).  Pre-check
                # whether CUDA is actually usable and fall back to CPU.
                device = self._device
                if device == "auto":
                    device = _resolve_device()

                logger.info(
                    "Loading Whisper model: source=%s, device=%s, "
                    "compute_type=%s, model_path=%s...",
                    model_source,
                    device,
                    self._compute_type,
                    self._model_path or "(auto/cache)",
                )
                t0 = time.monotonic()

                self._model = WhisperModel(
                    model_source,
                    device=device,
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

    def transcribe(self, audio_data: bytes, language: str | None = "de") -> str:
        """Transcribe audio bytes to text using the local Whisper model.

        Loads the model lazily on first call if not already loaded.

        Before calling into the native CTranslate2/onnxruntime code, all
        log handlers are flushed so that diagnostic messages are preserved
        even if the native call crashes the process (segfault).

        Args:
            audio_data: WAV audio file bytes (in-memory, never from disk).
            language: Language code for transcription (default 'de' for German).
                Pass None or "auto" for automatic language detection.

        Returns:
            Transcribed text string.

        Raises:
            STTError: If transcription fails (model not available, decode error, etc.).
        """
        # Normalize "auto" to None (faster-whisper auto-detects when language=None)
        if language == "auto":
            language = None

        logger.info("Local STT: transcribing %d bytes of audio...", len(audio_data))

        # Lazy load model on first use
        if not self._model_loaded or self._model is None:
            self.load_model()

        try:
            # Convert WAV bytes to float32 numpy array
            audio_array = _wav_bytes_to_float32(audio_data)

            audio_duration = len(audio_array) / DEFAULT_SAMPLE_RATE

            logger.debug(
                "Audio converted to float32: %d samples, %.1f seconds.",
                len(audio_array),
                audio_duration,
            )

            # --- Pre-transcription diagnostic logging ---
            # Log all parameters BEFORE calling native code so the log
            # file contains useful information if the process crashes.
            logger.info(
                "Calling model.transcribe(): vad_filter=%s, beam_size=%d, "
                "language=%s, audio_duration=%.1fs, frozen=%s",
                self._vad_filter,
                self._beam_size,
                language,
                audio_duration,
                _is_frozen(),
            )

            # Force-flush ALL log handlers before entering native code.
            # If CTranslate2 or onnxruntime segfaults, the buffered log
            # messages would be lost. This ensures they are on disk.
            _flush_all_log_handlers()

            t0 = time.monotonic()

            # Build transcription kwargs. VAD parameters are only passed
            # when vad_filter is True to avoid any Silero/onnxruntime
            # code path whatsoever when disabled.
            transcribe_kwargs: dict = dict(
                language=language,
                beam_size=self._beam_size,
                vad_filter=self._vad_filter,
            )
            if self._initial_prompt:
                transcribe_kwargs["initial_prompt"] = self._initial_prompt
            if self._vad_filter:
                transcribe_kwargs["vad_parameters"] = dict(
                    min_silence_duration_ms=500,
                )

            # Run transcription -- this enters native CTranslate2 and
            # (if VAD enabled) onnxruntime code. A native crash here
            # will kill the process with no Python traceback.
            segments, info = self._model.transcribe(
                audio_array,
                **transcribe_kwargs,
            )

            # Collect all segments into a single string.
            # segments is a generator -- we must consume it to trigger inference.
            transcript_parts = []
            for segment in segments:
                transcript_parts.append(segment.text.strip())

            transcript = " ".join(transcript_parts).strip()

            elapsed = time.monotonic() - t0

            # Store detected language for external access
            self.detected_language = info.language

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
        except (SystemError, OSError) as e:
            # Native DLL crashes sometimes surface as SystemError or
            # OSError rather than killing the process outright.
            # This includes onnxruntime provider failures and
            # CTranslate2 DLL load issues in the _MEI* temp dir.
            error_msg = str(e)
            logger.error(
                "Native library error during transcription (%s): %s",
                type(e).__name__,
                error_msg,
            )
            # If VAD was enabled, suggest disabling it as a workaround
            vad_hint = ""
            if self._vad_filter:
                vad_hint = (
                    "\n\nThis may be caused by the Silero VAD component "
                    "(onnxruntime). Try disabling VAD in config.toml:\n"
                    '  [transcription]\n  vad_filter = false'
                )
            raise STTError(
                f"A native library crashed during transcription.\n\n"
                f"{type(e).__name__}: {error_msg}"
                f"{vad_hint}"
            ) from e
        except RuntimeError as e:
            # CTranslate2 raises RuntimeError for inference failures
            # (e.g., corrupted model, unsupported audio format, CUDA OOM).
            # onnxruntime can also raise RuntimeError for provider issues.
            error_msg = str(e)
            logger.error(
                "RuntimeError during transcription: %s",
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
            # Check for onnxruntime-related RuntimeErrors
            if "onnx" in error_msg.lower() or "provider" in error_msg.lower():
                vad_hint = ""
                if self._vad_filter:
                    vad_hint = (
                        "\n\nTry disabling VAD in config.toml:\n"
                        '  [transcription]\n  vad_filter = false'
                    )
                raise STTError(
                    f"ONNX runtime error during transcription.\n\n"
                    f"{error_msg}{vad_hint}"
                ) from e
            raise STTError(
                f"Local transcription failed: {error_msg}"
            ) from e
        except Exception as e:
            logger.error("Local STT error: %s: %s", type(e).__name__, e)
            raise STTError(
                f"Local transcription failed: {type(e).__name__}: {e}"
            ) from e
