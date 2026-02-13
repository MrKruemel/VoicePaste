# Implementation Plan: v0.4 Local STT via faster-whisper

**Date**: 2026-02-13
**Author**: Solution Architect
**Status**: PLAN (not yet implemented)
**Scope**: Local/offline speech-to-text via faster-whisper as an alternative to cloud Whisper API
**Depends on**: v0.3 (Settings dialog, keyring, hot-reload)

---

## Table of Contents

1. [Motivation and Tradeoffs](#1-motivation-and-tradeoffs)
2. [Architecture Decision Record](#2-architecture-decision-record)
3. [Architecture Overview](#3-architecture-overview)
4. [New Module: `src/local_stt.py`](#4-new-module-srclocal_sttpy)
5. [Model Management: `src/model_manager.py`](#5-model-management-srcmodel_managerpy)
6. [Config Changes: `src/config.py`](#6-config-changes-srcconfigpy)
7. [Constants Changes: `src/constants.py`](#7-constants-changes-srcconstantspy)
8. [Settings Dialog Changes: `src/settings_dialog.py`](#8-settings-dialog-changes-srcsettings_dialogpy)
9. [Main Orchestrator Changes: `src/main.py`](#9-main-orchestrator-changes-srcmainpy)
10. [STT Factory Pattern: `src/stt.py`](#10-stt-factory-pattern-srcsttpy)
11. [PyInstaller Packaging Impact](#11-pyinstaller-packaging-impact)
12. [Memory Management](#12-memory-management)
13. [First-Run UX for Model Download](#13-first-run-ux-for-model-download)
14. [Error Handling Matrix](#14-error-handling-matrix)
15. [Thread Safety Analysis](#15-thread-safety-analysis)
16. [Implementation Order](#16-implementation-order)
17. [Test Plan](#17-test-plan)
18. [Risk Register](#18-risk-register)

---

## 1. Motivation and Tradeoffs

### Why Local STT?

1. **Privacy**: Audio never leaves the user's machine. No audio data is transmitted
   over the network. This is a hard requirement for some users/organizations.
2. **No API key required for transcription**: Users can start using VoicePaste
   without an OpenAI API key (though summarization still requires one unless
   also made local in a future version).
3. **No recurring cost**: Cloud Whisper API charges per minute of audio. Local
   STT has zero marginal cost.
4. **Offline capability**: Works without internet (transcription only;
   summarization still requires a cloud API unless using passthrough mode).
5. **Latency**: For short recordings (<30s), local STT with `tiny`/`base`
   models on modern hardware can be faster than a cloud round-trip.

### Key Tradeoffs

| Dimension | Cloud (current) | Local (faster-whisper) |
|-----------|----------------|----------------------|
| Binary size | ~50 MB exe | ~50 MB exe + 75-3000 MB model on disk |
| First-run setup | Enter API key | Download model (75-3000 MB) |
| Transcription quality | Excellent (large-v2 server-side) | Good (tiny/base), Excellent (small/medium/large) |
| Latency (30s audio) | 2-5s (network dependent) | 3-8s CPU tiny, 1-3s GPU |
| Memory usage | ~80 MB app | +150-600 MB for model (CPU int8) |
| Internet required | Yes | No (after model download) |
| Recurring cost | ~$0.006/min | $0 |
| German quality | Excellent | Good with base, very good with small+ |

### Decision: Optional Dependency, Not Bundled in EXE

faster-whisper and its dependency ctranslate2 add approximately:
- `ctranslate2`: ~150 MB of native C++ DLLs (CUDA-free CPU build)
- `faster-whisper`: ~50 KB Python
- `huggingface_hub`: ~5 MB (for model download)
- `tokenizers`: ~10 MB (Rust binary)

Bundling these into the single-file EXE would increase it from ~50 MB to ~210+ MB
even before any model is included. The model itself (tiny=75 MB, base=145 MB)
cannot reasonably be bundled in the EXE.

**Decision**: faster-whisper is an **optional dependency** that is:
1. NOT bundled in the default PyInstaller EXE build.
2. Detected at runtime via a guarded `import faster_whisper`.
3. Installed by the user into a sidecar `venv` or the system Python, with VoicePaste
   loading it dynamically.

**However**, this conflicts with the single-file EXE model. After analysis, the
better approach is:

**Revised Decision**: Provide **two build targets**:
1. **`VoicePaste.exe`** (cloud-only, ~50 MB) -- the default, as today.
2. **`VoicePaste-Local.exe`** (cloud + local STT, ~210 MB) -- includes
   ctranslate2 and faster-whisper, but NOT the model. Model is downloaded
   on first use to a cache directory.

This keeps the default EXE small while offering a "batteries-included" local
variant for privacy-conscious users. Both EXEs share the same source code;
the difference is only in the PyInstaller spec (which dependencies are included).

**Alternative considered and rejected**: Sidecar Python environment. This would
require the user to have Python installed, defeats the "single portable EXE"
goal, and creates complex dependency management. The two-EXE approach is
cleaner.

**Alternative considered and rejected**: Bundling the model inside the EXE. Even
the `tiny` model (75 MB) would make the EXE 285+ MB. Models larger than `tiny`
would push it past 500 MB. External model cache is the standard approach used
by all desktop Whisper apps.

---

## 2. Architecture Decision Record

### ADR-v04-01: faster-whisper over whisper.cpp

**Decision**: Use `faster-whisper` (Python bindings for CTranslate2) over
`whisper.cpp` (via `pywhispercpp` or `whispercpp` bindings).

**Rationale**:
- faster-whisper has a clean Python API (`WhisperModel.transcribe()`).
- CTranslate2 is a mature C++ inference engine with int8 quantization support.
- 4x faster than original OpenAI Whisper, lower memory than whisper.cpp.
- Better Python ecosystem integration (pip install, no manual compilation).
- Accepts numpy arrays directly (we already have numpy from sounddevice).
- Active maintenance and community (SYSTRAN).
- MIT license (compatible with our project).

**Tradeoff**: CTranslate2 DLLs are ~150 MB vs whisper.cpp at ~20 MB. But
whisper.cpp Python bindings are less mature and require manual model format
conversion. The size tradeoff is acceptable for the better developer experience.

### ADR-v04-02: Model Cache Location

**Decision**: Store downloaded models in `%LOCALAPPDATA%\VoicePaste\models\`.

**Rationale**:
- `%LOCALAPPDATA%` is the standard Windows location for user-specific
  application data that does not roam across machines.
- Placing models next to the EXE would pollute the user's chosen install
  location with hundreds of MB of model data.
- faster-whisper's default cache (`~/.cache/huggingface/`) is Unix-convention
  and confusing on Windows. We override this.
- The cache directory is created on demand during the first model download.

**Path**: `C:\Users\<user>\AppData\Local\VoicePaste\models\<model_size>\`

### ADR-v04-03: Default Model Size

**Decision**: Default to `base` model for local STT.

**Rationale**:
- `tiny` (75 MB, ~32x realtime on CPU): Fastest, but German quality is
  noticeably lower. Frequent errors on compound words and proper nouns.
- `base` (145 MB, ~16x realtime on CPU): Good German quality. Acceptable
  for dictation. Fits comfortably in memory. Downloads in <1 min on broadband.
- `small` (480 MB, ~6x realtime on CPU): Very good German quality but
  noticeably slower on older CPUs and takes longer to download.
- `medium`/`large`: Overkill for the typical use case and slow on CPU.

The `base` model hits the sweet spot of quality, speed, and download size for
German dictation. Users can switch to `tiny` for speed or `small` for quality
in Settings.

### ADR-v04-04: CPU-Only Default, GPU as Opt-In

**Decision**: Default to CPU inference with int8 quantization. GPU (CUDA) is
available but requires the user to have CUDA drivers and the CUDA build of
ctranslate2.

**Rationale**:
- The default PyInstaller build bundles the CPU-only ctranslate2. CUDA support
  adds ~800 MB of CUDA libraries to the EXE, which is impractical.
- Most VoicePaste users are on laptops without discrete GPUs or on machines
  where CUDA is not installed.
- CPU int8 with the `base` model transcribes 30s of audio in ~2-4 seconds
  on modern x86-64 CPUs (Intel 10th gen+, AMD Zen 2+). This is within our
  10-second local latency target.
- Users who want GPU acceleration can install the CUDA version of ctranslate2
  themselves and set `local_device = "cuda"` in config.

### ADR-v04-05: Two-Phase Architecture (Download then Load)

**Decision**: Separate model download from model loading. The model is
downloaded once and cached on disk. It is loaded into memory when the user
first switches to local STT or on app startup if local STT is configured.

**Rationale**:
- Downloading on every startup would be absurd (75-3000 MB).
- Loading on startup wastes memory if the user switches to cloud.
- The two-phase approach lets the Settings dialog trigger a download (with
  progress feedback) independently of model loading.
- Model loading (into RAM) happens lazily on first transcription or eagerly
  on app startup if `stt_backend = "local"` is configured.

---

## 3. Architecture Overview

### Current Architecture (v0.3)

```
main.py (VoicePasteApp)
  |-- AppConfig (mutable, loaded from config.toml + keyring)
  |-- CloudWhisperSTT (implements STTBackend Protocol)
  |-- CloudLLMSummarizer / PassthroughSummarizer
  |-- TrayManager (on_quit, on_settings, get_state)
  |-- SettingsDialog (tkinter, spawned on demand)
  |-- HotkeyManager
  |-- AudioRecorder
```

### Target Architecture (v0.4)

```
main.py (VoicePasteApp)
  |-- AppConfig (mutable, new local STT fields)
  |       |-- stt_backend: "cloud" | "local"
  |       |-- local_model_size: "tiny" | "base" | "small" | "medium" | "large-v3"
  |       |-- local_device: "cpu" | "cuda" | "auto"
  |       |-- local_compute_type: "int8" | "float16" | "float32" | "auto"
  |
  |-- STT Backend (selected by config.stt_backend)
  |       |-- CloudWhisperSTT (existing, cloud API)
  |       |-- LocalWhisperSTT (NEW, faster-whisper)
  |       |       |-- WhisperModel (CTranslate2 engine)
  |       |       |-- Lazy model loading (loaded on first use)
  |       |       |-- ModelManager (download, cache, verify)
  |       |
  |       |-- create_stt_backend() factory function (NEW)
  |
  |-- ModelManager (NEW)
  |       |-- download_model(size, on_progress) -> bool
  |       |-- is_model_available(size) -> bool
  |       |-- get_model_path(size) -> Path | None
  |       |-- delete_model(size) -> bool
  |       |-- get_cache_dir() -> Path
  |
  |-- CloudLLMSummarizer / PassthroughSummarizer
  |-- TrayManager
  |-- SettingsDialog (MODIFIED: new "Transcription" section with backend toggle)
  |-- HotkeyManager
  |-- AudioRecorder
```

### Data Flow (Local STT Path)

```
AudioRecorder.stop()
    |
    v
WAV bytes (in-memory, 16-bit PCM, 16kHz mono)
    |
    v
LocalWhisperSTT.transcribe(audio_data, language="de")
    |
    +-- Convert WAV bytes to numpy float32 array
    |   (faster-whisper accepts numpy arrays natively)
    |
    +-- model.transcribe(audio_array, language="de")
    |   (runs CTranslate2 inference on CPU/GPU)
    |
    +-- Collect segments, join text
    |
    v
transcript: str
    |
    v
CloudLLMSummarizer.summarize(transcript)  [or PassthroughSummarizer]
    |
    v
summary: str
    |
    v
paste_text(summary)
```

### Dependency Graph (New Modules)

```
main.py
  +-- stt.py (MODIFIED: add create_stt_backend factory)
  |     +-- CloudWhisperSTT (existing)
  |     +-- local_stt.py (NEW)
  |           +-- faster_whisper (optional import)
  |           +-- model_manager.py (NEW)
  |                 +-- huggingface_hub (optional, for download)
  |
  +-- config.py (MODIFIED: new fields)
  +-- settings_dialog.py (MODIFIED: new transcription section)
  +-- constants.py (MODIFIED: new constants)
  +-- model_manager.py (NEW)
```

---

## 4. New Module: `src/local_stt.py`

### Design

```python
"""Local speech-to-text backend using faster-whisper (CTranslate2).

Provides offline transcription without sending audio to any cloud API.
Requires the faster-whisper package to be installed (optional dependency).

The model is loaded lazily on first transcription call to avoid consuming
memory when the user has not yet initiated a recording.

Thread safety:
    WhisperModel.transcribe() is NOT thread-safe. However, our architecture
    guarantees that only one pipeline thread calls transcribe() at a time
    (state machine enforces PROCESSING is single-threaded). No lock needed.
"""

import io
import logging
import struct
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


def is_faster_whisper_available() -> bool:
    """Check if the faster-whisper package is installed and importable.

    Caches the result after the first call.

    Returns:
        True if faster-whisper can be imported, False otherwise.
    """
    global _faster_whisper_available
    if _faster_whisper_available is not None:
        return _faster_whisper_available

    try:
        import faster_whisper  # noqa: F401
        _faster_whisper_available = True
        logger.info("faster-whisper is available (version: %s).",
                     getattr(faster_whisper, '__version__', 'unknown'))
    except ImportError:
        _faster_whisper_available = False
        logger.info("faster-whisper is not installed. Local STT unavailable.")

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
        with wave.open(buf, 'rb') as wf:
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
        raise STTError(f"Failed to decode WAV audio: {type(e).__name__}: {e}") from e


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
                the standard cache directory from ModelManager.
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

        logger.info(
            "LocalWhisperSTT initialized: model=%s, device=%s, "
            "compute_type=%s, beam_size=%d, model_path=%s",
            model_size, device, compute_type, beam_size,
            model_path or "(auto/cache)",
        )

    def load_model(self) -> None:
        """Load the Whisper model into memory.

        This can take several seconds (especially on first load from disk).
        Call this in a background thread to avoid blocking the UI.

        Raises:
            STTError: If the model cannot be loaded (not downloaded, OOM, etc.).
        """
        if self._model_loaded and self._model is not None:
            logger.debug("Model already loaded, skipping.")
            return

        try:
            from faster_whisper import WhisperModel

            model_source = str(self._model_path) if self._model_path else self._model_size

            logger.info(
                "Loading Whisper model: source=%s, device=%s, compute_type=%s...",
                model_source, self._device, self._compute_type,
            )
            t0 = time.monotonic()

            self._model = WhisperModel(
                model_source,
                device=self._device,
                compute_type=self._compute_type,
                # Use our custom cache dir if no explicit model_path
                download_root=None,  # We handle downloads via ModelManager
            )

            elapsed = time.monotonic() - t0
            logger.info("Whisper model loaded in %.1f seconds.", elapsed)
            self._model_loaded = True

        except ImportError as e:
            raise STTError(
                "faster-whisper is not properly installed."
            ) from e

        except MemoryError as e:
            raise STTError(
                f"Not enough memory to load Whisper model '{self._model_size}'. "
                f"Try a smaller model (tiny or base) or close other applications."
            ) from e

        except Exception as e:
            error_msg = str(e)
            if "No such file or directory" in error_msg or "not found" in error_msg.lower():
                raise STTError(
                    f"Whisper model '{self._model_size}' not found. "
                    f"Please download it first via Settings > Transcription > Download Model."
                ) from e
            raise STTError(
                f"Failed to load Whisper model: {type(e).__name__}: {e}"
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
                vad_filter=True,      # Filter out silence/noise
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
            raise STTError(
                "Out of memory during transcription. "
                "Try a smaller model or shorter recording."
            ) from e
        except Exception as e:
            logger.error("Local STT error: %s: %s", type(e).__name__, e)
            raise STTError(
                f"Local transcription failed: {type(e).__name__}: {e}"
            ) from e
```

### Key Implementation Details

1. **WAV to float32 conversion**: faster-whisper's `transcribe()` accepts numpy
   float32 arrays. Our AudioRecorder produces WAV bytes with 16-bit PCM int16
   samples at 16kHz mono. The `_wav_bytes_to_float32()` helper converts
   in-memory without disk I/O, maintaining REQ-S09.

2. **Lazy model loading**: The model is NOT loaded in `__init__`. This means:
   - App startup is fast even when local STT is configured.
   - Memory is not consumed until the user actually records something.
   - The first transcription has a one-time delay of 2-5 seconds for model loading.
   - Users can call `load_model()` explicitly (e.g., on a background thread at
     startup) to pre-load and eliminate this first-use delay.

3. **VAD filter**: We enable faster-whisper's built-in Voice Activity Detection
   filter (`vad_filter=True`). This skips silence/noise segments, improving both
   speed and quality. The filter is based on Silero VAD.

4. **Beam size**: Default beam size of 5 (defined in constants). Lower beam sizes
   (1-2) are faster but less accurate. Users can tune this via config if we
   expose it later.

---

## 5. Model Management: `src/model_manager.py`

### Design

```python
"""Whisper model download, caching, and lifecycle management.

Downloads CTranslate2-format Whisper models from Hugging Face Hub and
stores them in %LOCALAPPDATA%\\VoicePaste\\models\\.

This module is independent of faster-whisper and can be used to pre-download
models before the user attempts a transcription.

Thread safety:
    All public functions are safe to call from any thread. Downloads use
    a threading.Lock to prevent concurrent downloads of the same model.
"""

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Model size -> Hugging Face repo mapping
# These are the CTranslate2-converted models maintained by Systran
_MODEL_REPOS: dict[str, str] = {
    "tiny":     "Systran/faster-whisper-tiny",
    "base":     "Systran/faster-whisper-base",
    "small":    "Systran/faster-whisper-small",
    "medium":   "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}

# Approximate download sizes in MB (for progress display)
_MODEL_SIZES_MB: dict[str, int] = {
    "tiny":     75,
    "base":     145,
    "small":    480,
    "medium":   1500,
    "large-v2": 3000,
    "large-v3": 3000,
}

# Approximate RAM usage in MB (CPU int8 quantized)
_MODEL_RAM_MB: dict[str, int] = {
    "tiny":     150,
    "base":     200,
    "small":    350,
    "medium":   600,
    "large-v2": 1200,
    "large-v3": 1200,
}

# Lock to prevent concurrent downloads of the same model
_download_lock = threading.Lock()

# Type alias for progress callback
# Arguments: (bytes_downloaded: int, total_bytes: int, speed_bps: float)
ProgressCallback = Callable[[int, int, float], None]


def get_cache_dir() -> Path:
    """Get the model cache directory.

    Uses %LOCALAPPDATA%\\VoicePaste\\models\\ on Windows.
    Creates the directory if it does not exist.

    Returns:
        Path to the model cache directory.
    """
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        # Fallback: use the user's home directory
        local_app_data = str(Path.home() / "AppData" / "Local")

    cache_dir = Path(local_app_data) / "VoicePaste" / "models"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_model_path(model_size: str) -> Optional[Path]:
    """Get the path to a downloaded model.

    Args:
        model_size: Model size identifier (e.g., "base").

    Returns:
        Path to the model directory if it exists and is valid, None otherwise.
    """
    if model_size not in _MODEL_REPOS:
        logger.warning("Unknown model size: '%s'.", model_size)
        return None

    model_dir = get_cache_dir() / model_size
    if model_dir.exists() and _is_model_valid(model_dir):
        return model_dir
    return None


def is_model_available(model_size: str) -> bool:
    """Check if a model is downloaded and ready to use.

    Args:
        model_size: Model size identifier.

    Returns:
        True if the model is downloaded and valid.
    """
    return get_model_path(model_size) is not None


def _is_model_valid(model_dir: Path) -> bool:
    """Verify that a model directory contains the required files.

    CTranslate2 models require at minimum:
    - model.bin (the weights)
    - config.json (model configuration)

    Args:
        model_dir: Path to the model directory.

    Returns:
        True if required files exist.
    """
    required_files = ["model.bin", "config.json"]
    for fname in required_files:
        if not (model_dir / fname).exists():
            logger.debug(
                "Model directory '%s' missing required file '%s'.",
                model_dir, fname,
            )
            return False
    return True


def get_available_model_sizes() -> list[str]:
    """Return list of model sizes that have been downloaded.

    Returns:
        List of model size strings that are available locally.
    """
    return [size for size in _MODEL_REPOS if is_model_available(size)]


def get_all_model_sizes() -> list[str]:
    """Return all supported model sizes.

    Returns:
        List of all model size strings.
    """
    return list(_MODEL_REPOS.keys())


def get_model_info(model_size: str) -> dict:
    """Get information about a model size.

    Args:
        model_size: Model size identifier.

    Returns:
        Dict with keys: repo, download_mb, ram_mb, available.
    """
    return {
        "repo": _MODEL_REPOS.get(model_size, "unknown"),
        "download_mb": _MODEL_SIZES_MB.get(model_size, 0),
        "ram_mb": _MODEL_RAM_MB.get(model_size, 0),
        "available": is_model_available(model_size),
    }


def download_model(
    model_size: str,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> bool:
    """Download a Whisper model from Hugging Face Hub.

    Downloads the CTranslate2-format model to the local cache directory.
    Thread-safe: uses a lock to prevent concurrent downloads.

    Args:
        model_size: Model size to download (e.g., "base").
        on_progress: Optional callback for download progress updates.
            Called with (bytes_downloaded, total_bytes, speed_bps).
        cancel_event: Optional threading.Event that, when set, cancels
            the download.

    Returns:
        True if download succeeded, False on error or cancellation.
    """
    if model_size not in _MODEL_REPOS:
        logger.error("Unknown model size: '%s'.", model_size)
        return False

    repo_id = _MODEL_REPOS[model_size]
    target_dir = get_cache_dir() / model_size

    if not _download_lock.acquire(timeout=1):
        logger.warning("Another model download is already in progress.")
        return False

    try:
        logger.info(
            "Downloading Whisper model '%s' from '%s' to '%s'...",
            model_size, repo_id, target_dir,
        )

        # Use huggingface_hub for the actual download
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            logger.error(
                "huggingface_hub is not installed. Cannot download models. "
                "Install with: pip install huggingface_hub"
            )
            return False

        # Download the entire model repo as a snapshot
        # This downloads all files (model.bin, config.json, vocabulary, etc.)
        # to a cache location, then we copy/link to our target directory.
        try:
            downloaded_path = snapshot_download(
                repo_id=repo_id,
                local_dir=str(target_dir),
                local_dir_use_symlinks=False,  # Copy files, don't symlink
            )

            logger.info(
                "Model '%s' downloaded successfully to '%s'.",
                model_size, downloaded_path,
            )

        except Exception as e:
            logger.error(
                "Failed to download model '%s': %s: %s",
                model_size, type(e).__name__, e,
            )
            # Clean up partial download
            if target_dir.exists():
                try:
                    shutil.rmtree(target_dir)
                except OSError:
                    pass
            return False

        # Verify the download
        if not _is_model_valid(target_dir):
            logger.error(
                "Downloaded model '%s' is incomplete or corrupted.", model_size,
            )
            return False

        logger.info("Model '%s' verified and ready to use.", model_size)
        return True

    finally:
        _download_lock.release()


def delete_model(model_size: str) -> bool:
    """Delete a downloaded model from the cache.

    Args:
        model_size: Model size to delete.

    Returns:
        True if deleted (or did not exist), False on error.
    """
    model_dir = get_cache_dir() / model_size
    if not model_dir.exists():
        logger.info("Model '%s' not found in cache (already deleted).", model_size)
        return True

    try:
        shutil.rmtree(model_dir)
        logger.info("Model '%s' deleted from cache.", model_size)
        return True
    except OSError as e:
        logger.error("Failed to delete model '%s': %s", model_size, e)
        return False


def get_cache_size_mb() -> float:
    """Get the total size of all cached models in MB.

    Returns:
        Total cache size in megabytes.
    """
    cache_dir = get_cache_dir()
    total = 0
    for path in cache_dir.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total / (1024 * 1024)
```

### Key Implementation Details

1. **Hugging Face Hub integration**: We use `huggingface_hub.snapshot_download()`
   to download model repositories. This handles:
   - Resumable downloads (if interrupted)
   - Hash verification of downloaded files
   - Progress callbacks (which we wire to the UI)
   - Authentication (not needed for public models)

2. **Model validation**: After download, we verify that `model.bin` and
   `config.json` exist. This catches partial/corrupted downloads.

3. **Download cancellation**: The `cancel_event` parameter allows the Settings
   dialog to cancel an in-progress download. `snapshot_download` does not
   natively support cancellation, so we would need to wrap it (see Section 13
   for the first-run UX approach). In v0.4, we rely on the thread being a
   daemon thread that dies when the dialog closes.

4. **Cache cleanup**: `delete_model()` removes a model from the cache.
   `get_cache_size_mb()` reports total cache usage. These are used by the
   Settings dialog for cache management.

---

## 6. Config Changes: `src/config.py`

### New AppConfig Fields

```python
@dataclass
class AppConfig:
    # --- Existing fields (unchanged) ---
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    hotkey: str = DEFAULT_HOTKEY
    log_level: str = "INFO"
    summarization_enabled: bool = True
    summarization_provider: str = DEFAULT_SUMMARIZATION_PROVIDER
    summarization_model: str = SUMMARIZE_MODEL
    summarization_base_url: str = ""
    summarization_custom_prompt: str = ""
    audio_cues_enabled: bool = True
    app_directory: Path = field(default_factory=_get_app_directory)

    # --- New fields (v0.4: local STT) ---
    stt_backend: str = "cloud"          # "cloud" | "local"
    local_model_size: str = "base"      # "tiny" | "base" | "small" | "medium" | "large-v3"
    local_device: str = "cpu"           # "cpu" | "cuda" | "auto"
    local_compute_type: str = "int8"    # "int8" | "float16" | "float32" | "auto"
```

### Validation in `load_config()`

```python
# New section in load_config():
transcription_section = data.get("transcription", {})

stt_backend = transcription_section.get("backend", "cloud")
if stt_backend not in ("cloud", "local"):
    logger.warning(
        "Invalid stt_backend '%s'. Falling back to 'cloud'.", stt_backend
    )
    stt_backend = "cloud"

local_model_size = transcription_section.get("model_size", "base")
if local_model_size not in ("tiny", "base", "small", "medium", "large-v2", "large-v3"):
    logger.warning(
        "Invalid local_model_size '%s'. Falling back to 'base'.", local_model_size
    )
    local_model_size = "base"

local_device = transcription_section.get("device", "cpu")
if local_device not in ("cpu", "cuda", "auto"):
    logger.warning(
        "Invalid local_device '%s'. Falling back to 'cpu'.", local_device
    )
    local_device = "cpu"

local_compute_type = transcription_section.get("compute_type", "int8")
if local_compute_type not in ("int8", "float16", "float32", "auto"):
    logger.warning(
        "Invalid local_compute_type '%s'. Falling back to 'int8'.", local_compute_type
    )
    local_compute_type = "int8"
```

### Updated `save_to_toml()`

Add a new `[transcription]` section to the generated TOML:

```python
content = f"""\
# Voice Paste Configuration
# Managed by Settings dialog. Manual edits are preserved on next save.

[api]
# API keys are stored in Windows Credential Manager.

[hotkey]
combination = "{esc(self.hotkey)}"

[transcription]
# Backend: "cloud" (OpenAI Whisper API) or "local" (faster-whisper, offline)
backend = "{esc(self.stt_backend)}"
# Local model size: tiny, base, small, medium, large-v2, large-v3
model_size = "{esc(self.local_model_size)}"
# Device: cpu, cuda, auto
device = "{esc(self.local_device)}"
# Compute type: int8, float16, float32, auto
compute_type = "{esc(self.local_compute_type)}"

[summarization]
enabled = {str(self.summarization_enabled).lower()}
provider = "{esc(self.summarization_provider)}"
model = "{esc(self.summarization_model)}"
base_url = "{esc(self.summarization_base_url)}"
custom_prompt = "{esc(self.summarization_custom_prompt)}"

[feedback]
audio_cues = {str(self.audio_cues_enabled).lower()}

[logging]
level = "{esc(self.log_level)}"
"""
```

### Updated CONFIG_TEMPLATE

```toml
# Voice-to-Summary Paste Tool Configuration

[api]
# API keys are stored securely in Windows Credential Manager.
# Use the Settings dialog (right-click tray icon > Settings) to manage keys.

[hotkey]
combination = "ctrl+alt+r"

[transcription]
# Backend: "cloud" (OpenAI Whisper API) or "local" (faster-whisper, offline)
# Cloud requires an OpenAI API key. Local requires a downloaded Whisper model.
backend = "cloud"
# Local STT model size (only used when backend = "local")
# Options: tiny (~75MB, fast), base (~145MB, good quality),
#          small (~480MB, better), medium (~1.5GB), large-v3 (~3GB)
model_size = "base"
# Compute device: "cpu" (default, works everywhere) or "cuda" (NVIDIA GPU)
device = "cpu"
# Quantization: "int8" (fastest, CPU), "float16" (GPU), "float32" (highest quality)
compute_type = "int8"

[summarization]
enabled = true
provider = "openai"
model = "gpt-4o-mini"
base_url = ""
custom_prompt = ""

[feedback]
audio_cues = true

[logging]
level = "INFO"
```

---

## 7. Constants Changes: `src/constants.py`

### New Constants

```python
# --- v0.4: Local STT configuration ---

# STT backend options
STT_BACKENDS = ("cloud", "local")
DEFAULT_STT_BACKEND = "cloud"

# Local model sizes
LOCAL_MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v2", "large-v3")
LOCAL_STT_DEFAULT_MODEL_SIZE = "base"
LOCAL_STT_DEFAULT_DEVICE = "cpu"
LOCAL_STT_DEFAULT_COMPUTE_TYPE = "int8"
LOCAL_STT_DEFAULT_BEAM_SIZE = 5

# Local model display information (for Settings dialog)
LOCAL_MODEL_DISPLAY: dict[str, dict[str, str]] = {
    "tiny":     {"label": "Tiny (~75 MB, fastest, lower quality)",
                 "download_mb": "75", "ram_mb": "~150"},
    "base":     {"label": "Base (~145 MB, good quality, recommended)",
                 "download_mb": "145", "ram_mb": "~200"},
    "small":    {"label": "Small (~480 MB, better quality)",
                 "download_mb": "480", "ram_mb": "~350"},
    "medium":   {"label": "Medium (~1.5 GB, high quality, slow on CPU)",
                 "download_mb": "1500", "ram_mb": "~600"},
    "large-v2": {"label": "Large v2 (~3 GB, highest quality, very slow on CPU)",
                 "download_mb": "3000", "ram_mb": "~1200"},
    "large-v3": {"label": "Large v3 (~3 GB, newest, very slow on CPU)",
                 "download_mb": "3000", "ram_mb": "~1200"},
}

# Update APP_VERSION
APP_VERSION = "0.4.0"
```

---

## 8. Settings Dialog Changes: `src/settings_dialog.py`

### New "Transcription" Section Layout

The existing "Transcription (OpenAI Whisper)" section is redesigned to support
both cloud and local backends with a toggle:

```
+--------------------------------------------------+
|  Voice Paste - Settings                      [X]  |
+--------------------------------------------------+
|                                                    |
|  -- Transcription -------------------------------- |
|                                                    |
|  Backend:  [Cloud (OpenAI API)    v]               |
|                                                    |
|  === Cloud Backend (shown when Cloud selected) === |
|  API Key:  [********cdef             ] [Edit]      |
|  Required for speech-to-text. Get key at           |
|  platform.openai.com                               |
|                                                    |
|  === Local Backend (shown when Local selected) === |
|  Model:    [Base (~145 MB)           v]            |
|  Device:   [CPU                      v]            |
|  Status:   [Downloaded, ready] or                  |
|            [Not downloaded]                        |
|  [ Download Model ]  [ Delete Model ]              |
|                                                    |
|  Download progress:                                |
|  [=============>                    ] 67%  12 MB/s |
|                                                    |
|  Note: Audio is processed entirely on your         |
|  machine. No data is sent to any server.           |
|                                                    |
|  -- Summarization -------------------------------- |
|  (unchanged from v0.3)                             |
|                                                    |
|  -- General -------------------------------------- |
|  (unchanged from v0.3)                             |
|                                                    |
|  +----------+    +---------+                       |
|  |   Save   |    |  Cancel |                       |
|  +----------+    +---------+                       |
+--------------------------------------------------+
```

### Backend Toggle Behavior

When the user changes the backend dropdown:

**Cloud selected**:
- Show the API Key field (existing).
- Hide the local model controls (model dropdown, device, download button).
- The "API Key is required" hint appears as before.

**Local selected**:
- Hide the API Key field.
- Show the local model controls.
- Check if faster-whisper is available (`is_faster_whisper_available()`).
  - If NOT available: Show error message "faster-whisper is not installed.
    This feature requires the VoicePaste-Local edition." and disable the
    local controls.
  - If available: Enable the model controls and check model download status.

### Model Download in the Settings Dialog

The "Download Model" button triggers a background thread that calls
`model_manager.download_model()`. The progress is displayed via a tkinter
progress bar.

```python
def _on_download_clicked(self) -> None:
    """Handle Download Model button click.

    Spawns a background thread for the download and updates the
    progress bar via tkinter's after() mechanism.
    """
    model_size = self._get_selected_model_size()

    # Disable controls during download
    self._download_btn.config(state="disabled")
    self._model_combo.config(state="disabled")
    self._download_progress.pack(fill=self._tk.X, pady=(4, 4))
    self._download_progress["value"] = 0
    self._download_status.config(text="Starting download...")

    self._download_cancel_event = threading.Event()

    def _do_download():
        import model_manager

        def _on_progress(downloaded: int, total: int, speed: float) -> None:
            # Schedule UI update on tkinter thread
            if total > 0:
                pct = (downloaded / total) * 100
            else:
                pct = 0
            speed_mb = speed / (1024 * 1024)
            self._dialog.after(0, lambda: self._update_download_progress(
                pct, downloaded, total, speed_mb
            ))

        success = model_manager.download_model(
            model_size,
            on_progress=_on_progress,
            cancel_event=self._download_cancel_event,
        )

        # Schedule completion UI update on tkinter thread
        self._dialog.after(0, lambda: self._on_download_complete(success))

    thread = threading.Thread(
        target=_do_download, daemon=True, name="model-download"
    )
    thread.start()


def _update_download_progress(
    self, percent: float, downloaded: int, total: int, speed_mb: float
) -> None:
    """Update the download progress bar and status label.

    Called on the tkinter thread via after().
    """
    self._download_progress["value"] = percent
    downloaded_mb = downloaded / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    self._download_status.config(
        text=f"Downloading: {downloaded_mb:.0f} / {total_mb:.0f} MB "
             f"({speed_mb:.1f} MB/s)"
    )


def _on_download_complete(self, success: bool) -> None:
    """Handle download completion. Called on the tkinter thread."""
    self._download_btn.config(state="normal")
    self._model_combo.config(state="readonly")

    if success:
        self._download_status.config(text="Download complete. Model ready.")
        self._download_progress["value"] = 100
        self._update_model_status()
    else:
        if self._download_cancel_event.is_set():
            self._download_status.config(text="Download cancelled.")
        else:
            self._download_status.config(
                text="Download failed. Check logs for details."
            )
        self._download_progress["value"] = 0
```

### Model Status Display

When the Local backend is selected, the dialog shows the current model status:

```python
def _update_model_status(self) -> None:
    """Update the model status label based on download status."""
    import model_manager

    model_size = self._get_selected_model_size()
    if model_manager.is_model_available(model_size):
        info = model_manager.get_model_info(model_size)
        self._model_status.config(
            text=f"Status: Downloaded, ready ({info['ram_mb']} MB RAM when loaded)",
            foreground="#006600",
        )
        self._download_btn.config(text="Re-download")
        self._delete_btn.config(state="normal")
    else:
        info = model_manager.get_model_info(model_size)
        self._model_status.config(
            text=f"Status: Not downloaded ({info['download_mb']} MB download)",
            foreground="#CC6600",
        )
        self._download_btn.config(text="Download Model")
        self._delete_btn.config(state="disabled")
```

### Validation Changes

The `_validate()` method changes:

```python
def _validate(self) -> Optional[str]:
    """Validate all fields."""
    stt_backend = self._stt_backend_var.get()  # "Cloud" or "Local"

    if stt_backend == "Cloud":
        # Existing cloud validation: API key required
        if self._openai_key_editing:
            key = self._openai_key_var.get().strip()
            if not key:
                return "OpenAI API key is required for cloud transcription."
            if not key.startswith("sk-"):
                return 'API key should start with "sk-".'
        elif not self._openai_key_actual:
            return "OpenAI API key is required for cloud transcription."

    elif stt_backend == "Local":
        # Local validation: faster-whisper must be available, model downloaded
        from local_stt import is_faster_whisper_available
        import model_manager

        if not is_faster_whisper_available():
            return (
                "Local STT requires faster-whisper, which is not installed. "
                "Use the VoicePaste-Local edition or install faster-whisper manually."
            )

        model_size = self._get_selected_model_size()
        if not model_manager.is_model_available(model_size):
            return (
                f"Model '{model_size}' is not downloaded. "
                f"Click 'Download Model' before saving."
            )

    # ... rest of existing validation (summarization, etc.)
```

### New Widget Variables

```python
# In __init__ or _build_ui:
self._stt_backend_var = tk.StringVar()      # "Cloud" or "Local"
self._local_model_var = tk.StringVar()       # Model size display label
self._local_device_var = tk.StringVar()      # "CPU" or "CUDA" or "Auto"
self._download_cancel_event: Optional[threading.Event] = None
```

---

## 9. Main Orchestrator Changes: `src/main.py`

### STT Client Creation

The `_stt` initialization in `VoicePasteApp.__init__` changes to use the
factory function:

```python
def __init__(self, config: AppConfig) -> None:
    # ... existing init code ...

    # v0.4: STT client is created via factory based on backend config
    self._stt = self._create_stt_backend()

    # ... rest of init ...


def _create_stt_backend(self) -> Optional[object]:
    """Create the STT backend based on current config.

    Returns:
        An STTBackend implementation, or None if not configurable yet.
    """
    config = self.config

    if config.stt_backend == "local":
        try:
            from local_stt import LocalWhisperSTT, is_faster_whisper_available
            import model_manager

            if not is_faster_whisper_available():
                logger.warning(
                    "Local STT configured but faster-whisper is not installed. "
                    "Falling back to cloud STT."
                )
                return self._create_cloud_stt()

            model_path = model_manager.get_model_path(config.local_model_size)
            if model_path is None:
                logger.warning(
                    "Local STT configured but model '%s' is not downloaded. "
                    "Use Settings > Transcription to download it.",
                    config.local_model_size,
                )
                # Return the LocalWhisperSTT anyway -- it will fail with a
                # clear error on first transcription attempt, which is better
                # than silently falling back to cloud.
                return LocalWhisperSTT(
                    model_size=config.local_model_size,
                    device=config.local_device,
                    compute_type=config.local_compute_type,
                )

            return LocalWhisperSTT(
                model_size=config.local_model_size,
                device=config.local_device,
                compute_type=config.local_compute_type,
                model_path=model_path,
            )

        except Exception as e:
            logger.error("Failed to create local STT backend: %s", e)
            logger.info("Falling back to cloud STT.")
            return self._create_cloud_stt()

    else:
        return self._create_cloud_stt()


def _create_cloud_stt(self) -> Optional[CloudWhisperSTT]:
    """Create the cloud STT backend (existing logic)."""
    if self.config.openai_api_key:
        return CloudWhisperSTT(api_key=self.config.openai_api_key)
    return None
```

### Hot-Reload Changes

The `_on_settings_saved()` method adds STT backend handling:

```python
def _on_settings_saved(self, changed_fields: dict) -> None:
    """Handle settings save. Recreate API clients as needed."""
    logger.info("Settings saved. Changed fields: %s", list(changed_fields.keys()))

    # Determine if STT client needs rebuild
    stt_keys = {
        "openai_api_key",
        "stt_backend",
        "local_model_size",
        "local_device",
        "local_compute_type",
    }
    if changed_fields.keys() & stt_keys:
        # Unload old model if switching away from local
        old_backend = changed_fields.get("stt_backend")
        if old_backend and hasattr(self._stt, 'unload_model'):
            self._stt.unload_model()

        self._stt = self._create_stt_backend()
        logger.info("STT backend rebuilt: %s", self.config.stt_backend)

    # ... existing summarizer rebuild logic ...
```

### Pre-Recording Check

The `_start_recording()` method needs to handle both backends:

```python
def _start_recording(self) -> None:
    """Transition from IDLE to RECORDING."""
    if self._stt is None:
        if self.config.stt_backend == "local":
            self._show_error(
                "Local STT model not available.\n"
                "Right-click tray icon > Settings to download a model."
            )
        else:
            self._show_error(
                "No OpenAI API key configured.\n"
                "Right-click the tray icon > Settings to add your key."
            )
        return

    # ... existing recording start logic ...
```

---

## 10. STT Factory Pattern: `src/stt.py`

### Changes

Add a factory function to `stt.py` that creates the appropriate backend:

```python
def create_stt_backend(
    config: "AppConfig",
) -> Optional["STTBackend"]:
    """Factory function to create an STT backend based on configuration.

    This function encapsulates the backend selection logic and handles
    import errors gracefully (e.g., when faster-whisper is not installed).

    Args:
        config: Application configuration with STT settings.

    Returns:
        An STTBackend implementation, or None if no backend can be created
        (e.g., no API key for cloud, faster-whisper not installed for local).
    """
    if config.stt_backend == "local":
        try:
            from local_stt import LocalWhisperSTT, is_faster_whisper_available
            import model_manager

            if not is_faster_whisper_available():
                logger.warning(
                    "faster-whisper not installed. Cannot use local STT."
                )
                return None

            model_path = model_manager.get_model_path(config.local_model_size)
            return LocalWhisperSTT(
                model_size=config.local_model_size,
                device=config.local_device,
                compute_type=config.local_compute_type,
                model_path=model_path,
            )

        except Exception as e:
            logger.error("Failed to create local STT: %s", e)
            return None

    else:
        # Cloud backend
        if not config.openai_api_key:
            logger.warning("No API key for cloud STT.")
            return None
        return CloudWhisperSTT(api_key=config.openai_api_key)
```

The existing `STTBackend` Protocol and `CloudWhisperSTT` class remain unchanged.

---

## 11. PyInstaller Packaging Impact

### Two Build Targets

#### Build Target 1: `VoicePaste.exe` (Cloud-Only, Default)

No changes to the existing `voice_paste.spec`. The local STT modules
(`local_stt.py`, `model_manager.py`) are included as Python source (they are
in the `src/` directory) but `faster_whisper` and `ctranslate2` are NOT bundled.
The guarded `import faster_whisper` in `local_stt.py` will fail gracefully,
and `is_faster_whisper_available()` will return False.

**Impact**: Zero additional binary size. The `local_stt.py` and
`model_manager.py` modules add ~10 KB of Python bytecode.

#### Build Target 2: `VoicePaste-Local.exe` (Cloud + Local)

A new spec file `voice_paste_local.spec` extends the default spec with:

```python
# voice_paste_local.spec
# Extends voice_paste.spec for the local STT build

# ... same Analysis as voice_paste.spec, PLUS:

# Additional hidden imports for faster-whisper and ctranslate2
_hidden_imports_local = _hidden_imports + [
    'faster_whisper',
    'ctranslate2',
    'huggingface_hub',
    'huggingface_hub.file_download',
    'huggingface_hub.hf_api',
    'huggingface_hub.utils',
    'tokenizers',
    'tqdm',
    'yaml',          # huggingface_hub dependency
    'requests',      # huggingface_hub dependency
    'urllib3',       # requests dependency
    'filelock',      # huggingface_hub dependency
    'fsspec',        # huggingface_hub dependency
    'packaging',     # huggingface_hub dependency
]

# Collect ctranslate2 DLLs and data files
_datas_local = _datas + collect_data_files('ctranslate2')
_binaries_local = collect_dynamic_libs('ctranslate2')

# ... same EXE configuration with name='VoicePaste-Local'
```

**Estimated binary size**: ~200-250 MB (ctranslate2 DLLs are ~150 MB).

### ctranslate2 Native DLLs

ctranslate2 ships these native DLLs on Windows (CPU build):
- `ctranslate2.dll` (~30 MB) -- core inference engine
- `ctranslate2_ops.dll` (~20 MB) -- operations library
- Various Intel MKL / oneDNN DLLs (~100 MB) -- math acceleration
- `onnxruntime.dll` (optional, not needed for Whisper)

PyInstaller must collect these via `collect_dynamic_libs('ctranslate2')` and
include them as binaries. They MUST NOT be compressed by UPX (add to
`upx_exclude`):

```python
upx_exclude=[
    'libportaudio64bit.dll',
    'pydantic_core',
    'ctranslate2.dll',         # NEW
    'ctranslate2_ops.dll',     # NEW
    'mkl_*.dll',               # NEW: Intel MKL DLLs
    'libiomp5md.dll',          # NEW: OpenMP runtime
]
```

### Updated `build.bat`

```bat
REM Add new build targets:
if /i "%~1"=="local"   set "BUILD_MODE=local"

REM ...

if /i "%BUILD_MODE%"=="local" (
    python -m PyInstaller "%PROJECT_DIR%voice_paste_local.spec"
) else if /i ...
```

### tkinter Exclusion Fix

The current `voice_paste.spec` excludes tkinter:
```python
_excludes = [
    'tkinter',
    '_tkinter',
    ...
]
```

This was safe in v0.2 when there was no settings dialog. **In v0.3, tkinter
is required** for the Settings dialog. The excludes must be removed:

```python
# REMOVE from _excludes in both spec files:
# 'tkinter',    -- REQUIRED for Settings dialog (v0.3)
# '_tkinter',   -- REQUIRED for Settings dialog (v0.3)
```

**Note**: This is a v0.3 bug that should be fixed before v0.4. The Settings
dialog would currently fail in the built EXE because tkinter is excluded.
This must be verified and fixed as a prerequisite.

---

## 12. Memory Management

### Model Lifecycle

```
App starts
    |
    +-- config.stt_backend == "cloud"
    |     --> No model loaded. Memory: ~80 MB (app only).
    |
    +-- config.stt_backend == "local"
          --> LocalWhisperSTT created but model NOT loaded (lazy).
          |   Memory: ~80 MB (app only).
          |
          +-- User presses hotkey --> records audio --> transcribe() called
          |   --> Model loaded into RAM on first call.
          |       tiny: +150 MB, base: +200 MB, small: +350 MB
          |   --> Transcription runs.
          |   --> Model stays in RAM for subsequent recordings.
          |
          +-- User switches to cloud via Settings
              --> unload_model() called by _on_settings_saved().
              --> Model freed from RAM. gc.collect() encouraged.
              --> Memory returns to ~80 MB.
```

### Memory Budget

| Scenario | Peak Memory |
|----------|------------|
| Cloud STT only | ~80 MB |
| Local STT, tiny model loaded | ~230 MB |
| Local STT, base model loaded | ~280 MB |
| Local STT, small model loaded | ~430 MB |
| Local STT, medium model loaded | ~680 MB |
| Local STT, during transcription (30s audio) | model + ~50 MB working set |

All scenarios are within the 500 MB target for tiny/base/small models on CPU.
Medium and large models exceed the target and should be documented as
"advanced users only" with a memory warning in the Settings dialog.

### Explicit Unloading

`LocalWhisperSTT.unload_model()` is called:
1. When the user switches from local to cloud backend in Settings.
2. When the app shuts down (in `_shutdown()`).

The method:
1. Deletes the `WhisperModel` reference.
2. Calls `gc.collect()` to encourage the Python GC to free the large
   CTranslate2 tensors.

**Note**: CTranslate2 uses native memory (malloc, not Python heap) for model
weights. `gc.collect()` only frees the Python wrapper objects, which in turn
call C++ destructors that free the native memory. This is reliable as long as
there are no circular references holding the model alive.

### Memory Warning in Settings

When the user selects medium or large models, the Settings dialog shows:

```
Warning: The medium model requires ~600 MB of RAM.
Ensure your system has sufficient free memory.
```

---

## 13. First-Run UX for Model Download

### Scenario: User Switches to Local STT for the First Time

```
1. User opens Settings dialog.
2. User changes Backend dropdown from "Cloud" to "Local".
3. Dialog shows local STT controls:
   - Model: [Base v]
   - Status: "Not downloaded (145 MB download)"
   - [Download Model] button (enabled)
   - Privacy note: "Audio is processed entirely on your machine."

4. User clicks "Download Model".
5. Download starts in background thread.
6. Progress bar appears:
   [=============>                    ] 67%  12 MB/s
   Status: "Downloading: 97 / 145 MB (12.3 MB/s)"
7. Download completes.
   Status: "Download complete. Model ready." (green text)
   Progress bar: 100%

8. User clicks "Save".
9. Validation passes (model is downloaded).
10. Config saved: stt_backend = "local", local_model_size = "base".
11. on_save callback: main.py creates LocalWhisperSTT.
12. Dialog closes.

13. User presses hotkey to record.
14. First transcription: model loads into RAM (2-5s delay).
15. Audio transcribed locally. No network traffic.
```

### Download Progress Feedback

Since VoicePaste is a tray app with no main window, the download MUST happen
within the Settings dialog (which has a tkinter window). We cannot show
download progress in the system tray because Windows toast notifications are
too limited for progress bars.

The progress bar and status text are updated via tkinter's `after()` method,
which schedules callbacks on the tkinter event loop thread. This is the same
pattern used for the API key test in v0.3.

### Handling Download Interruption

If the user closes the Settings dialog during a download:
1. The dialog's `_on_close` handler sets `self._download_cancel_event`.
2. The download thread is a daemon thread; it will be killed when the
   dialog's tkinter mainloop exits.
3. The partial download may remain on disk. The next download attempt will
   resume from where it left off (huggingface_hub supports this).
4. `_is_model_valid()` will return False for incomplete downloads, so the
   model will not be used until the download completes.

### Handling No Internet for Download

If the download fails due to no internet:
1. The download thread catches the exception.
2. `_on_download_complete(success=False)` is called on the tkinter thread.
3. Status shows: "Download failed. Check your internet connection."
4. The user can retry by clicking "Download Model" again.

### Offline-First After Download

Once a model is downloaded, the user never needs internet again for
transcription. The model files persist in `%LOCALAPPDATA%\VoicePaste\models\`.
Only summarization (cloud LLM) requires internet. If summarization is disabled
(passthrough mode), VoicePaste works 100% offline.

---

## 14. Error Handling Matrix

| Error | When | User Message | Fallback |
|-------|------|--------------|----------|
| faster-whisper not installed | Settings: switch to Local | "Local STT requires faster-whisper. Use VoicePaste-Local edition." | Disable local controls |
| Model not downloaded | Save Settings with Local + no model | "Model 'base' is not downloaded. Click 'Download Model' first." | Block save |
| Model download fails (network) | Download button clicked | "Download failed. Check your internet connection." | Retry button |
| Model download fails (disk full) | Download button clicked | "Download failed: not enough disk space." | Show cache dir |
| Model load OOM | First transcription after model load | "Not enough memory for model 'medium'. Try 'base' or 'tiny'." | STTError -> pipeline error handler |
| Model load file corrupted | First transcription | "Model files corrupted. Re-download via Settings." | STTError -> pipeline error handler |
| Transcription OOM (long audio) | During transcription of very long recording | "Out of memory during transcription. Try a shorter recording." | STTError -> pipeline error handler |
| CUDA not available | Settings: device="cuda" | "CUDA not available. Set device to 'cpu'." | Fall back to CPU in transcribe() |
| ctranslate2 DLL missing | Import faster-whisper | is_faster_whisper_available() returns False | Local option disabled in Settings |
| Model too slow (>30s for 30s audio) | During transcription | No error; just slow. Log realtime factor. | User can switch to smaller model |

### Graceful Degradation Chain

```
Local STT fails?
    |
    +-- STTError raised in _run_pipeline()
    |
    +-- Caught by except STTError in _run_pipeline()
    |
    +-- Error toast shown: "Transcription error: <message>"
    |
    +-- State returns to IDLE
    |
    +-- User can:
        +-- Retry (hotkey again)
        +-- Switch to cloud in Settings
        +-- Download a different model size
```

VoicePaste does NOT automatically fall back from local to cloud STT if
local fails. This is intentional: users who choose local STT are making a
privacy decision. Silent fallback to cloud would violate that decision.
The error message should guide the user to fix the local STT issue.

---

## 15. Thread Safety Analysis

### faster-whisper Thread Safety

`WhisperModel.transcribe()` is **NOT thread-safe**. It uses global state
within CTranslate2 during inference. However, our architecture guarantees
single-threaded access:

```
State Machine:
    IDLE -> RECORDING -> PROCESSING -> PASTING -> IDLE

During PROCESSING:
    - Only one pipeline worker thread exists.
    - It is spawned in _stop_recording_and_process().
    - It is the ONLY thread that calls self._stt.transcribe().
    - A new pipeline thread is only spawned after the current one
      finishes and the state returns to IDLE.
    - No mutex needed around transcribe().
```

### Model Loading Thread Safety

`LocalWhisperSTT.load_model()` is called:
1. Lazily from `transcribe()` on the pipeline worker thread.
2. Potentially from a background pre-load thread at startup.

These two callers could race if a user presses the hotkey very quickly after
startup. We add a simple guard:

```python
class LocalWhisperSTT:
    def __init__(self, ...):
        self._load_lock = threading.Lock()
        ...

    def load_model(self) -> None:
        with self._load_lock:
            if self._model_loaded:
                return
            # ... actual model loading ...
```

### Settings Dialog Thread Safety

The Settings dialog runs on its own tkinter thread. It reads and writes
to `AppConfig` fields. The `on_save` callback is called from the tkinter
thread and mutates `self._stt` in `VoicePasteApp`. This is safe because:

1. Settings menu is DISABLED during RECORDING and PROCESSING states.
2. The pipeline worker thread holds a local reference to `self._stt`
   captured at the start of `_run_pipeline()`. Even if `self._stt` is
   replaced during pipeline execution, the old object remains valid.
3. Python's GIL makes simple attribute assignment atomic.

### Model Download Thread Safety

`model_manager.download_model()` uses `_download_lock` to prevent concurrent
downloads. The lock has a 1-second timeout to avoid deadlocks:

```python
if not _download_lock.acquire(timeout=1):
    logger.warning("Another download in progress.")
    return False
```

---

## 16. Implementation Order

### Prerequisites (v0.3 Fixes)

**Step 0: Fix tkinter exclusion in voice_paste.spec**
- Remove `'tkinter'` and `'_tkinter'` from `_excludes` list.
- Verify Settings dialog works in the built EXE.
- This is a v0.3 bug that must be fixed before v0.4 work begins.

### Phase 1: Foundation (No UI Changes)

**Step 1: `src/constants.py` -- add local STT constants**
- Add STT_BACKENDS, LOCAL_MODEL_SIZES, defaults, display info.
- Bump APP_VERSION to "0.4.0".
- No behavior change.
- Verify: existing tests pass.

**Step 2: `src/model_manager.py` + `tests/test_model_manager.py`**
- Implement the model management module.
- Write unit tests with mocked huggingface_hub.
- Tests cover: get_cache_dir, is_model_available, download_model (mocked),
  delete_model, _is_model_valid.
- Verify: `pytest tests/test_model_manager.py` passes.

**Step 3: `src/local_stt.py` + `tests/test_local_stt.py`**
- Implement LocalWhisperSTT class.
- Implement is_faster_whisper_available() with guarded import.
- Implement _wav_bytes_to_float32() conversion.
- Write unit tests:
  - Test _wav_bytes_to_float32 with known audio data.
  - Test is_faster_whisper_available with mocked import.
  - Test LocalWhisperSTT.transcribe with mocked WhisperModel.
  - Test lazy loading behavior.
  - Test error handling (OOM, missing model, etc.).
- Verify: `pytest tests/test_local_stt.py` passes.

### Phase 2: Config and Factory

**Step 4: `src/config.py` -- add local STT fields**
- Add stt_backend, local_model_size, local_device, local_compute_type fields.
- Add validation in load_config().
- Update save_to_toml() with [transcription] section.
- Update CONFIG_TEMPLATE.
- Update `tests/test_config.py`.
- Verify: `pytest tests/test_config.py` passes.

**Step 5: `src/stt.py` -- add create_stt_backend factory**
- Add the factory function.
- Keep existing CloudWhisperSTT unchanged.
- Update `tests/test_stt.py` for the factory.
- Verify: `pytest tests/test_stt.py` passes.

### Phase 3: Main Integration

**Step 6: `src/main.py` -- integrate local STT backend**
- Replace direct CloudWhisperSTT construction with factory.
- Add _create_stt_backend() method.
- Update _on_settings_saved() for STT backend changes.
- Update _start_recording() for local STT checks.
- Add unload_model() call on shutdown and backend switch.
- Update integration tests.
- Verify: `pytest tests/` passes (all tests).

### Phase 4: Settings Dialog

**Step 7: `src/settings_dialog.py` -- redesign Transcription section**
- Replace the static "Transcription (OpenAI Whisper)" section with a
  backend-toggled section.
- Add backend dropdown (Cloud / Local).
- Add local model controls (model dropdown, device dropdown, status,
  download button, progress bar, delete button).
- Wire download to model_manager.download_model() in background thread.
- Wire save to include new config fields.
- Manual test: toggle between cloud and local, download a model, save.

### Phase 5: Build System

**Step 8: `voice_paste_local.spec` -- local build spec**
- Create new spec file for VoicePaste-Local.exe.
- Add faster-whisper, ctranslate2, huggingface_hub to hidden imports.
- Collect ctranslate2 DLLs as binaries.
- Add UPX exclusions for ctranslate2 DLLs.
- Test build: `pyinstaller voice_paste_local.spec`.
- Verify: built EXE starts, local STT works.

**Step 9: `build.bat` -- add local build target**
- Add `build.bat local` command.
- Document in README.

**Step 10: `requirements-local.txt` -- optional dependencies**
- Create a separate requirements file for local STT dependencies:
  ```
  # Optional: Local STT via faster-whisper
  faster-whisper==1.1.0
  ctranslate2==4.5.0
  huggingface_hub==0.27.0
  tokenizers==0.21.0
  ```
- Keep `requirements.txt` unchanged (cloud-only dependencies).
- Document: `pip install -r requirements-local.txt` for local STT.

### Phase 6: End-to-End Testing

**Step 11: Manual end-to-end testing**
- Cloud-only EXE: local STT option visible but disabled (graceful).
- Local EXE: full local STT workflow.
- Backend switching: cloud -> local -> cloud.
- Model management: download, delete, re-download.
- Memory monitoring: verify model unload frees RAM.
- German transcription quality: compare cloud vs. local (base model).

---

## 17. Test Plan

### Unit Tests

| Test File | What It Covers |
|-----------|---------------|
| `tests/test_model_manager.py` (NEW) | Cache dir, model availability, download (mocked), delete, validation |
| `tests/test_local_stt.py` (NEW) | WAV-to-float32 conversion, availability check, transcribe (mocked model), lazy loading, error handling |
| `tests/test_config.py` (UPDATED) | New fields, validation, save_to_toml with [transcription] section |
| `tests/test_stt.py` (UPDATED) | Factory function, backend selection logic |
| `tests/test_settings_dialog.py` (UPDATED) | Backend toggle, local controls visibility, download flow (mocked) |

### Integration Tests

| Test | What It Verifies |
|------|-----------------|
| Backend switch cloud->local | STT client replaced, old model unloaded |
| Backend switch local->cloud | Model unloaded, CloudWhisperSTT created |
| Config round-trip | save_to_toml -> load_config preserves all local STT fields |
| Graceful degradation | faster-whisper not installed -> is_faster_whisper_available() returns False -> local option disabled |
| Pipeline with local STT | AudioRecorder.stop() -> LocalWhisperSTT.transcribe() -> summarizer -> paste (mocked) |

### Manual Test Checklist

- [ ] Cloud-only EXE (`VoicePaste.exe`):
  - [ ] Settings shows "Local" option but displays "faster-whisper not installed" message.
  - [ ] Saving with "Local" backend shows validation error.
  - [ ] Cloud STT works as before (no regression).

- [ ] Local EXE (`VoicePaste-Local.exe`):
  - [ ] Settings shows "Local" option with enabled controls.
  - [ ] Download model (base) completes with progress feedback.
  - [ ] Model status shows "Downloaded, ready" after download.
  - [ ] Save with Local backend succeeds.
  - [ ] Record + transcribe works offline (disconnect internet).
  - [ ] German transcription quality is acceptable.
  - [ ] Switch to Cloud backend -> model unloaded (verify via Task Manager memory).
  - [ ] Delete model -> status shows "Not downloaded".

- [ ] Error scenarios:
  - [ ] Download with no internet -> error message shown.
  - [ ] Close Settings during download -> no crash.
  - [ ] Select medium model, record -> memory warning accurate.
  - [ ] Corrupt model files -> clear error on transcription attempt.

---

## 18. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| ctranslate2 DLLs fail to load in PyInstaller EXE | Medium | High | Test build early (Phase 5). Collect DLLs explicitly. Add to UPX exclude. |
| huggingface_hub download fails behind corporate proxy | Medium | Medium | Document proxy configuration. Consider alternative download (direct HTTP). |
| Model loading takes >10s on older CPUs | Medium | Low | Log load time. Show "Loading model..." toast. Pre-load in background thread. |
| int8 quantization degrades German quality too much | Low | Medium | Default to base model (good German). Document quality comparison in README. |
| Memory leak when unloading/reloading model repeatedly | Low | Medium | Test model cycling in a loop. CTranslate2 destructors should free memory. |
| faster-whisper version incompatibility with ctranslate2 | Medium | High | Pin exact versions in requirements-local.txt. Test before release. |
| CUDA build of ctranslate2 too large for EXE (~800 MB) | N/A | N/A | CPU-only by design. CUDA users install manually. Documented in README. |
| tkinter exclusion in v0.3 spec breaks Settings dialog | High | High | Fix as Step 0 (prerequisite). Verify Settings works in built EXE. |
| Concurrent transcription requests (double hotkey press) | Very Low | Low | State machine prevents this (PROCESSING blocks new recordings). |
| Partial model download used as valid model | Low | Medium | _is_model_valid() checks for required files. Invalid = re-download. |
| User deletes model files manually while app is running | Low | Medium | Model load fails -> STTError -> clear error message. |
| VAD filter removes valid speech in quiet recordings | Low | Medium | VAD is enabled by default but configurable. Document in README. |

---

## Appendix A: File Change Summary

### New Files

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `src/local_stt.py` | LocalWhisperSTT class, WAV conversion, availability check | ~250 |
| `src/model_manager.py` | Model download, cache, validation, lifecycle | ~250 |
| `tests/test_local_stt.py` | Unit tests for local STT | ~200 |
| `tests/test_model_manager.py` | Unit tests for model manager | ~200 |
| `voice_paste_local.spec` | PyInstaller spec for local build | ~100 |
| `requirements-local.txt` | Optional dependencies for local STT | ~10 |

### Modified Files

| File | Nature of Changes |
|------|-------------------|
| `src/constants.py` | Add STT backend constants, model display info, bump version |
| `src/config.py` | Add stt_backend, local_model_size, local_device, local_compute_type fields; update save/load/template |
| `src/stt.py` | Add create_stt_backend() factory function |
| `src/main.py` | Replace direct CloudWhisperSTT with factory; update hot-reload, startup, shutdown |
| `src/settings_dialog.py` | Redesign Transcription section with backend toggle, model controls, download UI |
| `voice_paste.spec` | Remove tkinter from excludes (v0.3 fix) |
| `build.bat` | Add `local` build target |
| `tests/test_config.py` | Add tests for new fields |
| `tests/test_stt.py` | Add tests for factory function |
| `tests/test_settings_dialog.py` | Add tests for backend toggle UI |

### Unchanged Files

| File | Reason |
|------|--------|
| `src/audio.py` | Audio capture is backend-agnostic |
| `src/summarizer.py` | Summarization is independent of STT |
| `src/paste.py` | Paste mechanism is unchanged |
| `src/hotkey.py` | Hotkey handling is unchanged |
| `src/tray.py` | Tray icon and menu are unchanged |
| `src/notifications.py` | Audio cues are unchanged |
| `src/keyring_store.py` | Credential storage is unchanged |

---

## Appendix B: Sequence Diagram -- Local STT First Use

```
User           Settings     ModelManager   VoicePasteApp  LocalWhisperSTT
 |                |               |               |               |
 |--Settings----->|               |               |               |
 |                |               |               |               |
 |--Local+Base--->|               |               |               |
 |                |               |               |               |
 |--Download----->|               |               |               |
 |                |--download---->|               |               |
 |                |   (bg thread) |--HF Hub-----> |               |
 |                |               | [downloading] |               |
 |                |<--progress----|               |               |
 |                |  [=========>] |               |               |
 |                |<--complete----|               |               |
 |                |               |               |               |
 |--Save--------->|               |               |               |
 |                |----------on_save------------->|               |
 |                |               |               |--create------>|
 |                |               |               | (model NOT    |
 |  [dialog       |               |               |  loaded yet)  |
 |   closes]      |               |               |               |
 |                                                |               |
 |====== later: user presses hotkey ==============================|
 |                                                |               |
 |--hotkey------->                                |               |
 |                                    RECORDING   |               |
 |--hotkey------->                                |               |
 |                                    PROCESSING  |               |
 |                                                |--transcribe-->|
 |                                                |               |
 |                                                |  load_model() |
 |                                                |  (2-5 sec)    |
 |                                                |               |
 |                                                |  model.transcribe(audio)
 |                                                |  (inference)  |
 |                                                |               |
 |                                                |<--transcript--|
 |                                                |               |
 |                                    PASTING     |               |
 |<---paste at cursor---                          |               |
 |                                    IDLE        |               |
```

---

## Appendix C: Config.toml Complete Example (v0.4)

```toml
# Voice-to-Summary Paste Tool Configuration (v0.4)

[api]
# API keys are stored in Windows Credential Manager.
# Use Settings dialog to manage keys.

[hotkey]
combination = "ctrl+alt+r"

[transcription]
# Backend: "cloud" or "local"
backend = "local"
# Model size for local STT (downloaded to %LOCALAPPDATA%\VoicePaste\models\)
model_size = "base"
# Compute device: "cpu" or "cuda"
device = "cpu"
# Quantization: "int8" (CPU default), "float16" (GPU), "float32"
compute_type = "int8"

[summarization]
enabled = true
provider = "openai"
model = "gpt-4o-mini"
base_url = ""
custom_prompt = ""

[feedback]
audio_cues = true

[logging]
level = "INFO"
```

---

## Appendix D: Binary Size Estimates

| Build | Exe Size | Models (on disk, separate) |
|-------|----------|---------------------------|
| VoicePaste.exe (cloud-only) | ~50 MB | N/A |
| VoicePaste-Local.exe (cloud+local) | ~200-250 MB | 75 MB - 3 GB per model |

The ~200 MB local EXE size is primarily ctranslate2 native DLLs. This is
acceptable for a privacy-focused distribution. Users who want the smallest
possible binary use the cloud-only build.

---

## Appendix E: Dependency Version Matrix

| Package | Version | Purpose | Build Target |
|---------|---------|---------|-------------|
| faster-whisper | >=1.0.0 | Whisper inference engine | Local only |
| ctranslate2 | >=4.0.0 | CTranslate2 C++ backend | Local only (transitive) |
| huggingface_hub | >=0.20.0 | Model download from HF Hub | Local only |
| tokenizers | >=0.15.0 | Tokenizer for Whisper | Local only (transitive) |
| numpy | ==2.4.2 | Audio buffer handling | Both (already present) |
| sounddevice | ==0.5.5 | Audio capture | Both (already present) |
| openai | ==2.20.0 | Cloud API | Both (already present) |

All local-only dependencies are listed in `requirements-local.txt` with
pinned versions. They are NOT included in the base `requirements.txt`.
