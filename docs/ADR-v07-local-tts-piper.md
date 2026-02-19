# Architecture Decision Record: v0.7 -- Local TTS via Piper

**Date**: 2026-02-18
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.6.0 (TTS via ElevenLabs, cloud-only)
**Target Version**: 0.7.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Package Choice: piper-onnx vs piper-tts vs Direct ONNX](#2-package-choice)
3. [TTSBackend Protocol Fit](#3-ttsbackend-protocol-fit)
4. [Model Management](#4-model-management)
5. [PyInstaller Bundling Strategy](#5-pyinstaller-bundling-strategy)
6. [Binary Size Impact](#6-binary-size-impact)
7. [State Machine -- No Changes](#7-state-machine)
8. [Threading Model -- No Changes](#8-threading-model)
9. [Dependencies and Version Pins](#9-dependencies-and-version-pins)
10. [Integration Points with Existing Code](#10-integration-points)
11. [Config Schema Extensions](#11-config-schema-extensions)
12. [Settings Dialog Extensions](#12-settings-dialog-extensions)
13. [Risk Assessment](#13-risk-assessment)
14. [Implementation Plan](#14-implementation-plan)
15. [Trade-offs Summary](#15-trade-offs-summary)
16. [Cross-Feature Notes: External API and Hands-Free](#16-cross-feature-notes)

---

## 1. Executive Summary

v0.7 adds **local, offline TTS** to Voice Paste via Piper, a fast neural TTS
engine that runs ONNX models on the CPU. This complements the existing
ElevenLabs cloud TTS (v0.6) by providing:

- **Zero-cost, zero-latency** TTS with no API key or internet connection.
- **Privacy**: audio synthesis happens entirely on-device.
- **German voice quality**: Piper's `thorsten` voice models are trained
  specifically for German and sound natural for a local engine.

The implementation follows the same Protocol + Factory pattern established for
STT and TTS backends. The user selects "Cloud (ElevenLabs)" or
"Local (Piper)" in Settings. Both backends implement the existing `TTSBackend`
Protocol, so all downstream code (audio playback, state machine, hotkeys)
remains unchanged.

**Key architectural decision**: Use the `piper-onnx` package (MIT license)
rather than the official `piper-tts` package (GPL-3.0). This avoids GPL
license contamination while providing a cleaner, lighter integration that
reuses onnxruntime already bundled for local STT.

---

## 2. Package Choice: piper-onnx vs piper-tts vs Direct ONNX

### Options Evaluated

| Criterion | piper-onnx (MIT) | piper-tts (GPL-3.0) | Direct ONNX Inference |
|-----------|------------------|---------------------|----------------------|
| **License** | MIT | GPL-3.0-or-later | N/A (own code) |
| **Python version** | >=3.10 | >=3.9 | Any |
| **Dependencies** | onnxruntime, phonemizer-fork (GPL), espeakng-loader | piper-phonemize, onnxruntime | onnxruntime, espeak-ng |
| **API complexity** | Simple: `Piper.create(text)` returns `(numpy_array, sample_rate)` | Rich: `PiperVoice.synthesize()` writes WAV, `synthesize_stream_raw()` yields bytes | Full control, 200+ lines to write |
| **Output format** | float32 numpy array | WAV file or raw PCM bytes | float32 numpy array |
| **Binary size** | espeak-ng.dll (0.4 MB) + data (17.5 MB) + Python code (~50 KB) | espeak-ng (similar) + piper_phonemize native lib (~5 MB) | espeak-ng only |
| **PyInstaller compat** | Good (pure Python + onnxruntime already bundled) | Complex (native piper_phonemize C++ lib) | Best (no third-party package) |
| **Maintenance** | Active (thewh1teagle, MIT) | Moved to OHF-Voice/piper1-gpl, archived original | N/A |
| **Streaming** | No (returns full array per call) | Yes (sentence-level streaming) | Manual implementation |
| **espeak-ng bundling** | espeakng-loader bundles DLL+data in Python package | Requires external espeak-ng or bundled piper_phonemize | Requires espeak-ng |

### Decision: piper-onnx

**Primary rationale: License compliance.**

The `piper-tts` package is GPL-3.0. Our project (Voice Paste) is distributed
as a compiled binary. Including GPL-3.0 code would require us to either:
(a) release Voice Paste under GPL-3.0, or (b) provide the complete
corresponding source code alongside the binary. Neither is desirable for a
utility tool.

`piper-onnx` is MIT-licensed. However, its dependency `phonemizer-fork` is
GPL-3.0. This creates a transitive GPL contamination risk.

**Mitigation**: We will NOT use phonemizer-fork directly. Instead, we will
use `espeakng-loader` (which bundles the espeak-ng native library and data)
and call espeak-ng's phonemization through a thin wrapper that does NOT
import the GPL phonemizer. The `piper-onnx.Piper.create()` method calls
`phonemize(text)` from `phonemizer-fork`, but we can replace this call by:

1. Subclassing or wrapping `Piper` to inject our own phonemization step.
2. Calling espeak-ng directly via ctypes or `espeakng-loader`'s API.
3. Using piper-onnx's `is_phonemes=True` flag and providing pre-phonemized
   input.

**Revised approach**: After analyzing the piper-onnx source code, the simplest
path is to NOT depend on `piper-onnx` at all. The entire package is 80 lines
of Python. We can implement equivalent functionality directly in our own
`local_tts.py` module using only:
- `onnxruntime` (Apache 2.0, already bundled)
- `espeakng-loader` (MIT, bundles espeak-ng DLL+data)
- `numpy` (BSD, already bundled)

This eliminates ALL license concerns and gives us full control over the code.

### Final Decision: Direct ONNX Inference (no piper-onnx, no piper-tts)

**Implementation**: Port the ~80 lines of piper-onnx's `Piper` class into our
own `src/local_tts.py`, attributing the original MIT-licensed code. Use
`espeakng-loader` for espeak-ng phonemization via ctypes (not phonemizer-fork).

**Why this is simplest**: We already have onnxruntime in the bundle. We already
understand ONNX inference (used by local STT VAD). We avoid adding ANY new
Python package dependency. The espeak-ng phonemization can be done via ctypes
calls to the DLL that `espeakng-loader` bundles, sidestepping the GPL
`phonemizer` package entirely.

---

## 3. TTSBackend Protocol Fit

### Current Protocol (src/tts.py)

```python
class TTSBackend(Protocol):
    def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes (MP3)."""
        ...
```

### Problem: Output Format Mismatch

The current protocol specifies MP3 return bytes because ElevenLabs returns MP3.
Piper produces raw PCM float32 audio. The `AudioPlayer` in `audio_playback.py`
decodes MP3 via miniaudio before playing through sounddevice.

### Solution: Extend AudioPlayer, Keep Protocol Return as bytes

Rather than changing the Protocol to be format-aware, we keep `synthesize()`
returning `bytes` but allow either MP3 or WAV format. The `AudioPlayer.play()`
method already accepts `audio_data: bytes` and decodes via miniaudio, which
handles both MP3 and WAV transparently.

The local TTS backend returns WAV bytes (with a proper WAV header). miniaudio's
`decode()` function handles WAV natively, so AudioPlayer works without changes.

```python
class PiperLocalTTS:
    def synthesize(self, text: str) -> bytes:
        """Synthesize text to WAV audio bytes."""
        pcm_float32, sample_rate = self._synthesize_raw(text)
        return self._pcm_to_wav(pcm_float32, sample_rate)
```

**Why not return raw PCM?** The AudioPlayer already has miniaudio-based
decoding that handles format detection. Returning WAV means we do not need to
pass sample_rate out-of-band. The WAV header is 44 bytes of overhead -- trivial.

### Protocol Change: None Required

The `TTSBackend.synthesize()` Protocol stays exactly as-is. The docstring
mentions "MP3" but this is just a documentation note, not a type constraint.
The actual type is `bytes`. We update the docstring to say "audio bytes
(MP3 or WAV)" and update AudioPlayer's docstring accordingly.

---

## 4. Model Management

### Model Storage

Piper voice models are stored in the same `%LOCALAPPDATA%\VoicePaste\models\`
directory tree, under a `tts/` subdirectory:

```
%LOCALAPPDATA%\VoicePaste\models\
  base\                          # Whisper STT model (existing)
  tts\
    de_DE-thorsten-medium\       # Piper TTS model directory
      de_DE-thorsten-medium.onnx       # ONNX model (~63 MB)
      de_DE-thorsten-medium.onnx.json  # Config (~5 KB)
    en_US-lessac-medium\
      en_US-lessac-medium.onnx
      en_US-lessac-medium.onnx.json
```

### Available German Voices

| Voice | Quality | Size | Sample Rate | Description |
|-------|---------|------|-------------|-------------|
| de_DE-thorsten-low | Low | 63 MB | 16 kHz | Fastest, adequate for short text |
| de_DE-thorsten-medium | Medium | 63 MB | 22.05 kHz | Good balance, **recommended** |
| de_DE-thorsten-high | High | 114 MB | 22.05 kHz | Best quality, slower |
| de_DE-thorsten_emotional-medium | Medium | 77 MB | 22.05 kHz | Multi-emotion support |

### Available English Voices (secondary)

| Voice | Quality | Size | Description |
|-------|---------|------|-------------|
| en_US-lessac-medium | Medium | ~64 MB | Professional male |
| en_US-amy-medium | Medium | ~64 MB | Professional female |

### Download Mechanism

Reuse the existing `model_manager.py` infrastructure with a new module
`tts_model_manager.py` (or extend model_manager.py) that:

1. Maps voice names to Hugging Face URLs (rhasspy/piper-voices repo).
2. Downloads the `.onnx` and `.onnx.json` files.
3. Validates the download (check both files exist).
4. Provides progress callbacks (same pattern as Whisper model download).

### Model Lifecycle

- **Lazy loading**: The ONNX model is loaded into onnxruntime InferenceSession
  on first `synthesize()` call, NOT at application startup.
- **Unload**: `unload_model()` deletes the InferenceSession and triggers GC.
- **Switching voices**: Unload current model, load new model. Handled by the
  factory function when config changes.

### Model Validation

```python
def _is_tts_model_valid(model_dir: Path) -> bool:
    """Check that model directory contains required files."""
    onnx_files = list(model_dir.glob("*.onnx"))
    json_files = list(model_dir.glob("*.onnx.json"))
    return len(onnx_files) >= 1 and len(json_files) >= 1
```

---

## 5. PyInstaller Bundling Strategy

### espeak-ng Native Library

The `espeakng-loader` package bundles:
- `espeak-ng.dll` (0.4 MB) -- the phonemization engine
- `espeak-ng-data/` directory (17.5 MB) -- language data files

These must be collected by PyInstaller:

```python
# voice_paste.spec additions

# --- espeakng-loader: espeak-ng DLL and language data ---
try:
    _espeakng_data = collect_data_files('espeakng_loader')
    _datas += _espeakng_data
    print(f'[voice_paste.spec] Collected {len(_espeakng_data)} espeakng-loader data files.')
except Exception as e:
    print(f'[voice_paste.spec] Note: espeakng-loader data not found: {e}')

try:
    _espeakng_bins = collect_dynamic_libs('espeakng_loader')
    _binaries += _espeakng_bins
    print(f'[voice_paste.spec] Collected {len(_espeakng_bins)} espeakng-loader binaries.')
except Exception as e:
    print(f'[voice_paste.spec] Note: espeakng-loader binaries not found: {e}')
```

### Hidden Imports

```python
_hidden_imports += [
    'espeakng_loader',
]
```

### UPX Exclusion

```python
_upx_exclude += [
    'espeak-ng.dll',
]
```

### Piper ONNX Models

The Piper voice ONNX models are NOT bundled in the exe. They are downloaded
on demand by the user (same pattern as Whisper models). This keeps the exe
size manageable.

### espeak-ng Data Path in Frozen Exe

When running as a PyInstaller bundle, `espeakng_loader.get_data_path()` returns
a path inside the `_MEI*` temp directory. This should work transparently because
`collect_data_files` preserves the package directory structure. We verify this
during Phase 1 testing.

---

## 6. Binary Size Impact

### Size Breakdown

| Component | Cloud-Only Build | Local Build (with STT) |
|-----------|-----------------|----------------------|
| Current exe size | ~50-60 MB | ~150-200 MB |
| espeakng-loader (DLL + data) | +18 MB | +18 MB |
| local_tts.py (own code) | <1 KB | <1 KB |
| onnxruntime (already bundled) | +0 MB (NEW: +31 MB) | +0 MB (already present) |

### Analysis

**Local build**: onnxruntime is already bundled for STT. Adding Piper TTS costs
only the espeak-ng DLL and data (~18 MB). Total impact: **+18 MB**.

**Cloud-only build**: onnxruntime is NOT currently bundled. Adding it would cost
~31 MB. Combined with espeak-ng: **+49 MB**. This nearly doubles the cloud-only
binary size.

### Decision: Local TTS Only in Local Build

Following the same pattern as Hands-Free Mode (v0.9), Piper local TTS is only
available in the Local build target (which already includes onnxruntime). The
cloud-only build continues to offer only ElevenLabs TTS.

**Rationale**: Users who want local/offline features already choose the Local
build. Users who prefer the smaller cloud-only build can use ElevenLabs for TTS.
This avoids a 49 MB size increase for users who will not use local TTS.

**Alternative considered**: Make onnxruntime a shared optional dependency. If the
user has the Local build, both local STT and local TTS are available. This is
already the de facto situation because both features share the same exe.

### Expected Final Sizes

| Build | Current | With v0.7 |
|-------|---------|-----------|
| Cloud-only | ~55 MB | ~55 MB (unchanged) |
| Local | ~175 MB | ~193 MB (+18 MB espeak-ng) |

---

## 7. State Machine -- No Changes

The state machine is unchanged from v0.6:

```
IDLE -> RECORDING -> PROCESSING -> PASTING -> IDLE
                                -> SPEAKING -> IDLE
```

Local TTS produces the same SPEAKING state as cloud TTS. The AudioPlayer
receives WAV bytes instead of MP3 bytes, but miniaudio handles both
transparently. No state machine changes are needed.

---

## 8. Threading Model -- No Changes

The threading model is unchanged from v0.6:

```
Main Thread:     pystray event loop
Thread 1:        keyboard hotkey listener
Thread 2:        Pipeline worker (per session)
Thread 3:        Settings dialog tkinter (on demand)
Thread 5:        TTS playback (per playback, via AudioPlayer)
```

Piper's ONNX inference runs on Thread 2 (pipeline worker) during the
PROCESSING state, same as STT inference. The onnxruntime InferenceSession
releases the GIL during inference, so other threads are not blocked.

Thread safety: The Piper model's InferenceSession is NOT thread-safe, but the
state machine guarantees single-threaded access (only one pipeline runs at a
time in PROCESSING state). This is the same guarantee that protects the local
STT model.

---

## 9. Dependencies and Version Pins

### New Dependencies

| Package | Version | License | Purpose | Required By |
|---------|---------|---------|---------|-------------|
| espeakng-loader | >=0.2.4 | MIT | Bundles espeak-ng DLL + data for phonemization | Local TTS |

### Existing Dependencies (Reused)

| Package | Already Bundled In | Purpose for Local TTS |
|---------|-------------------|----------------------|
| onnxruntime | Local build (STT VAD) | ONNX model inference |
| numpy | Both builds | Audio array manipulation |
| sounddevice | Both builds | Audio playback (via AudioPlayer) |
| miniaudio | Both builds (v0.6) | WAV/MP3 decoding for AudioPlayer |

### Dependencies NOT Needed

| Package | Reason for Exclusion |
|---------|---------------------|
| piper-tts | GPL-3.0 license |
| piper-onnx | Unnecessary (only 80 lines of code, we implement directly) |
| phonemizer-fork | GPL-3.0 license, replaced by direct espeak-ng ctypes calls |
| phonemizer | GPL-3.0 license |

### requirements.txt Addition

```
espeakng-loader>=0.2.4
```

---

## 10. Integration Points with Existing Code

### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `src/local_tts.py` | PiperLocalTTS class implementing TTSBackend Protocol | ~250 |
| `src/tts_model_manager.py` | Piper voice model download/cache/validate | ~200 |
| `tests/test_local_tts.py` | Unit tests for PiperLocalTTS (mocked onnxruntime) | ~150 |
| `tests/test_tts_model_manager.py` | Unit tests for TTS model management | ~100 |

### Modified Files

| File | Changes |
|------|---------|
| `src/tts.py` | Update `create_tts_backend()` factory to support `provider="piper"`. Update `TTSBackend.synthesize()` docstring (MP3 or WAV). |
| `src/constants.py` | Add `TTS_PROVIDERS` entry for "piper". Add `PIPER_DEFAULT_VOICE`, `PIPER_VOICE_MODELS` dict, related constants. |
| `src/config.py` | Add `tts_local_voice` field to AppConfig. Update `load_config()` and `save_to_toml()`. |
| `src/settings_dialog.py` | Add Piper voice selection in TTS section. Add voice model download UI (reuse pattern from Whisper model download). |
| `src/audio_playback.py` | Update `play()` docstring to note WAV support. No code changes needed (miniaudio handles WAV). |
| `voice_paste.spec` | Add espeakng-loader data collection, hidden imports, UPX exclusions. |

### src/tts.py Factory Extension

```python
def create_tts_backend(
    api_key: str,
    provider: str = "elevenlabs",
    voice_id: str = "",
    model_id: str = "",
    output_format: str = "",
    # v0.7: Local TTS fields
    local_voice: str = "",
) -> Optional[TTSBackend]:
    """Factory: create a TTS backend from configuration."""

    if provider == "elevenlabs":
        # ... existing ElevenLabs code ...
        pass

    if provider == "piper":
        try:
            from local_tts import PiperLocalTTS
            return PiperLocalTTS(voice_name=local_voice)
        except ImportError:
            logger.warning("Piper local TTS not available (missing dependencies).")
            return None
        except Exception as e:
            logger.error("Failed to create Piper TTS backend: %s", e)
            return None

    logger.warning("Unknown TTS provider '%s'.", provider)
    return None
```

### src/local_tts.py Core Implementation

```python
"""Local text-to-speech via Piper ONNX models.

Provides offline TTS using ONNX models trained with the Piper/VITS
architecture. Uses espeak-ng for phonemization and onnxruntime for
inference. No internet connection required.

Dependencies:
    - onnxruntime (Apache 2.0, already bundled for local STT)
    - espeakng-loader (MIT, bundles espeak-ng DLL + data)
    - numpy (BSD, already bundled)

Acknowledgement:
    The ONNX inference approach is based on the piper-onnx project
    by thewh1teagle (MIT license).
"""

import io
import json
import logging
import struct
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from tts import TTSError

logger = logging.getLogger(__name__)


class PiperLocalTTS:
    """Local TTS backend using Piper ONNX models.

    Implements the TTSBackend Protocol. Loads the voice model lazily
    on first synthesize() call. Thread safety: same as LocalWhisperSTT
    (state machine guarantees single-thread access).
    """

    def __init__(self, voice_name: str, model_path: Optional[Path] = None):
        self._voice_name = voice_name
        self._model_path = model_path
        self._session = None
        self._config = None
        self._sample_rate = 22050
        self._load_lock = threading.Lock()
        self._loaded = False

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to WAV audio bytes."""
        if not self._loaded:
            self._load_model()

        # Phonemize text using espeak-ng
        phonemes = self._phonemize(text)

        # Convert phonemes to IDs
        ids = self._phonemes_to_ids(phonemes)

        # Run ONNX inference
        pcm_float32 = self._infer(ids)

        # Convert to WAV bytes
        return self._pcm_to_wav(pcm_float32, self._sample_rate)

    def _load_model(self) -> None:
        """Load ONNX model and config. Thread-safe via lock."""
        with self._load_lock:
            if self._loaded:
                return
            # ... load onnxruntime session and config ...
            self._loaded = True

    def _phonemize(self, text: str) -> str:
        """Convert text to phonemes using espeak-ng."""
        # Uses espeakng_loader's bundled espeak-ng via ctypes
        ...

    def _phonemes_to_ids(self, phonemes: str) -> list[int]:
        """Convert phoneme string to integer IDs using config map."""
        ...

    def _infer(self, phoneme_ids: list[int]) -> np.ndarray:
        """Run ONNX model inference. Returns float32 PCM array."""
        ...

    @staticmethod
    def _pcm_to_wav(pcm: np.ndarray, sample_rate: int) -> bytes:
        """Convert float32 PCM array to WAV bytes."""
        # Scale to int16
        pcm_int16 = (pcm * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_int16.tobytes())
        return buf.getvalue()

    def unload_model(self) -> None:
        """Release ONNX session and free memory."""
        ...
```

---

## 11. Config Schema Extensions

### New config.toml Fields

```toml
[tts]
enabled = true
# Provider: "elevenlabs" (cloud) or "piper" (local, offline)
provider = "piper"
# --- Cloud (ElevenLabs) fields (existing) ---
voice_id = "pFZP5JQG7iQjIQuC4Bku"
model_id = "eleven_flash_v2_5"
output_format = "mp3_44100_128"
# --- Local (Piper) fields (NEW) ---
# Voice model name. Available voices are downloaded via Settings.
# German: de_DE-thorsten-medium (recommended), de_DE-thorsten-high
# English: en_US-lessac-medium, en_US-amy-medium
local_voice = "de_DE-thorsten-medium"
```

### New AppConfig Fields

```python
@dataclass
class AppConfig:
    # ... existing fields ...

    # v0.7: Local TTS (Piper) fields
    tts_local_voice: str = DEFAULT_PIPER_VOICE  # "de_DE-thorsten-medium"
```

### New Constants

```python
# --- v0.7: Piper local TTS configuration ---
TTS_PROVIDERS = ("elevenlabs", "piper")  # Updated from v0.6
DEFAULT_PIPER_VOICE = "de_DE-thorsten-medium"

PIPER_VOICE_MODELS: dict[str, dict[str, str]] = {
    "de_DE-thorsten-medium": {
        "label": "Thorsten (DE, medium quality, recommended)",
        "repo": "rhasspy/piper-voices",
        "files": ["de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx",
                  "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json"],
        "download_mb": "63",
        "sample_rate": "22050",
    },
    "de_DE-thorsten-high": {
        "label": "Thorsten (DE, high quality, larger)",
        "repo": "rhasspy/piper-voices",
        "files": ["de/de_DE/thorsten/high/de_DE-thorsten-high.onnx",
                  "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json"],
        "download_mb": "114",
        "sample_rate": "22050",
    },
    "de_DE-thorsten_emotional-medium": {
        "label": "Thorsten Emotional (DE, medium, multi-emotion)",
        "repo": "rhasspy/piper-voices",
        "files": ["de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium.onnx",
                  "de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium.onnx.json"],
        "download_mb": "77",
        "sample_rate": "22050",
    },
    "en_US-lessac-medium": {
        "label": "Lessac (EN, medium quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": ["en/en_US/lessac/medium/en_US-lessac-medium.onnx",
                  "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"],
        "download_mb": "64",
        "sample_rate": "22050",
    },
    "en_US-amy-medium": {
        "label": "Amy (EN, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": ["en/en_US/amy/medium/en_US-amy-medium.onnx",
                  "en/en_US/amy/medium/en_US-amy-medium.onnx.json"],
        "download_mb": "64",
        "sample_rate": "22050",
    },
}
```

---

## 12. Settings Dialog Extensions

### Updated TTS Section

The TTS section in the Settings dialog gains a provider selector:

```
+-- Text-to-Speech -----------------------------------------------+
|                                                                   |
|  [x] Enable Text-to-Speech                                       |
|                                                                   |
|  Provider:  [Cloud (ElevenLabs) v]  /  [Local (Piper, offline) v] |
|                                                                   |
|  === Cloud (ElevenLabs) sub-frame ===                             |
|  API Key:   [****9abc] [Edit]                                     |
|  Voice:     [Lily (Female, warm, DE/EN) v]                        |
|  Voice ID:  [pFZP5JQG7iQjIQuC4Bku]                               |
|  Model:     [eleven_flash_v2_5 (fast) v]                          |
|                                                                   |
|  === Local (Piper) sub-frame ===                                  |
|  Voice:     [Thorsten (DE, medium quality) v]                     |
|  Status:    Model downloaded and ready.  [Delete]                 |
|  [Download Model]  [progress bar]                                 |
|  Note: Local TTS is offline. No API key needed.                   |
|                                                                   |
+-------------------------------------------------------------------+
```

The pattern mirrors the Transcription section's Cloud/Local toggle:
- When Cloud is selected, show ElevenLabs API key and voice fields.
- When Local is selected, show Piper voice selector and model download controls.

### Model Download UI

Reuses the same pattern as the Whisper model download:
- Download button with progress bar.
- Cancel button during download.
- Status label showing download state.
- Delete button when model is downloaded.

The implementation shares infrastructure with `model_manager.py` (Hugging Face
Hub download, progress callback, cancel event).

---

## 13. Risk Assessment

### Risk 1: espeak-ng DLL Fails in PyInstaller Bundle

**Risk level**: Medium
**Reason**: espeak-ng requires both the DLL and a data directory with language
files. If the data path is not correctly resolved in the `_MEI*` temp directory,
phonemization will fail silently or crash.

**Mitigation**:
1. Use `espeakng_loader.get_library_path()` and `espeakng_loader.get_data_path()`
   which are designed to work in bundled environments.
2. If PyInstaller extracts the data to a different relative path, add a runtime
   hook (`rthook_espeakng.py`) that sets the data path before any imports.
3. Test in frozen exe EARLY (Phase 1).
4. Fallback: if espeak-ng fails, show a clear error "Local TTS unavailable in
   this build. Use Cloud (ElevenLabs) instead."

### Risk 2: phonemizer-fork GPL Contamination

**Risk level**: Eliminated (by design)
**Reason**: We do NOT import `phonemizer-fork` or `piper-onnx`. We implement
phonemization directly via espeakng-loader ctypes calls, which only uses the
MIT-licensed `espeakng-loader` and the LGPL espeak-ng library (LGPL allows
dynamic linking without GPL contamination).

### Risk 3: Piper ONNX Model Quality for German

**Risk level**: Low
**Reason**: The `thorsten` voice models are well-established in the German TTS
community. They are trained by a native German speaker and are the default
German voice in Home Assistant and other Piper deployments.

**Mitigation**: Default to `de_DE-thorsten-medium` which balances quality and
size. Offer `de_DE-thorsten-high` for users who want better quality.

### Risk 4: ONNX Inference Latency

**Risk level**: Low
**Reason**: Piper is designed for real-time inference on Raspberry Pi hardware.
On a modern desktop CPU, inference latency per sentence is 40-100ms. A 30-word
paragraph synthesizes in under 1 second.

**Benchmarks to validate in Phase 1**:
- 10-word sentence: target <200ms
- 50-word paragraph: target <1s
- 200-word text: target <3s

### Risk 5: onnxruntime Version Conflict with faster-whisper VAD

**Risk level**: Low
**Reason**: Both Piper TTS and faster-whisper VAD use onnxruntime, but they
use separate InferenceSession instances with different models. They never
run simultaneously (state machine guarantees PROCESSING is single-threaded).

**Mitigation**: The onnxruntime library is shared (one installation). No
version pinning conflicts because both features use the same version.

### Risk 6: espeak-ng ctypes Phonemization Complexity

**Risk level**: Medium
**Reason**: Calling espeak-ng via ctypes requires understanding the C API
(espeak_TextToPhonemes or espeak_ng_Speak with phoneme output). The API is
well-documented but has platform-specific quirks (string encoding, memory
management).

**Mitigation**:
1. Use `espeakng-loader`'s bundled DLL path (no system espeak-ng required).
2. Implement a minimal ctypes wrapper (~50 lines) that calls
   `espeak_TextToPhonemes()` with IPA output.
3. Alternative if ctypes is too fragile: spawn `espeak-ng --ipa -q "text"` as a
   subprocess. Slower but dead simple. The exe is bundled by espeakng-loader.
4. As a last resort: accept the GPL dependency on phonemizer-fork for the
   initial implementation, and replace it in a follow-up release.

### Risk 7: Piper Voice Model Download from Hugging Face

**Risk level**: Low
**Reason**: Same download mechanism as Whisper models (already working). The
rhasspy/piper-voices repo is public and well-maintained. Individual voice files
are 63-114 MB, similar in size to Whisper models.

---

## 14. Implementation Plan

### Phase 1: Core PiperLocalTTS (3 days)

1. Create `src/local_tts.py` with PiperLocalTTS class.
2. Implement espeak-ng phonemization via ctypes + espeakng-loader.
3. Implement ONNX inference (port piper-onnx logic).
4. Implement PCM-to-WAV conversion.
5. Unit tests with mocked onnxruntime session.
6. Manual test: synthesize German text, play via AudioPlayer.
7. Benchmark: measure latency for various text lengths.
8. **PyInstaller test**: build frozen exe, verify espeak-ng data loads.

**Deliverable**: `PiperLocalTTS.synthesize("Hallo Welt")` returns valid WAV.

### Phase 2: Model Management (2 days)

1. Create `src/tts_model_manager.py` (or extend model_manager.py).
2. Define voice model registry in constants.py.
3. Implement download from Hugging Face (reuse snapshot_download pattern).
4. Implement model validation (check .onnx and .onnx.json exist).
5. Unit tests for download/validate/delete.

**Deliverable**: Voice models downloadable and cached locally.

### Phase 3: Config + Factory Integration (1 day)

1. Add `tts_local_voice` to AppConfig.
2. Update `load_config()` and `save_to_toml()`.
3. Update `create_tts_backend()` factory to support `provider="piper"`.
4. Update TTS_PROVIDERS constant.
5. Wire into `main.py` `_rebuild_tts()` method.

**Deliverable**: Config toggle between Cloud and Local TTS works.

### Phase 4: Settings Dialog (2 days)

1. Add TTS provider selector (Cloud/Local) to Settings dialog.
2. Add Piper voice dropdown with model download UI.
3. Reuse download progress pattern from Whisper model download.
4. Hot-reload: rebuild TTS backend on Settings save.

**Deliverable**: Full Settings UI for switching between Cloud and Local TTS.

### Phase 5: Build + Polish (1 day)

1. Update `voice_paste.spec` with espeakng-loader data/binaries.
2. Add espeak-ng.dll to UPX exclusion.
3. Test frozen build (cloud-only: no Piper; Local: Piper works).
4. Update CHANGELOG, README with Local TTS documentation.

**Total estimated effort**: 9 developer days.

---

## 15. Trade-offs Summary

| Decision | Chosen | Alternative | Rationale |
|----------|--------|-------------|-----------|
| Package | Direct ONNX (own code) | piper-onnx (MIT) | Avoids transitive GPL from phonemizer-fork; full control over phonemization |
| Phonemization | espeakng-loader ctypes | phonemizer-fork (GPL) | License compliance; espeak-ng is LGPL (safe for dynamic linking) |
| Output format | WAV bytes via Protocol | Separate PCM path | Zero Protocol changes; miniaudio decodes WAV natively |
| Voice models | User-downloaded, not bundled | Bundle default voice in exe | Keeps exe size small; same UX pattern as Whisper models |
| Build availability | Local build only | Both builds | Avoids +49 MB size increase for cloud-only build |
| Default voice | de_DE-thorsten-medium | de_DE-thorsten-high | Better size/quality balance for default; high available for power users |
| Model storage | %LOCALAPPDATA%\VoicePaste\models\tts\ | Beside exe | Consistent with Whisper model storage; survives exe updates |
| Lazy loading | Load on first synthesize() | Load at startup | Faster startup; same pattern as LocalWhisperSTT |

---

## 16. Cross-Feature Notes: External API and Hands-Free

### External API (v0.8)

The External API's `tts` command works identically with both Cloud and Local
TTS backends. The API does not need to know which backend is active -- it calls
`self._tts.synthesize(text)` which is polymorphic via the Protocol pattern.

No API surface changes are needed for Local TTS support.

### Hands-Free Mode (v0.9)

Hands-Free Mode's Ask AI + TTS pipeline uses whatever TTS backend is configured.
With Local TTS, the entire hands-free flow can be fully offline:

```
Wake word (local, openWakeWord)
  -> Record speech
  -> STT (local, faster-whisper)
  -> LLM prompt (requires cloud unless Ollama)
  -> TTS response (local, Piper)
```

With Ollama for summarization, the pipeline is 100% offline. This is a
compelling use case for privacy-sensitive environments. The only remaining cloud
dependency is the LLM, which is already configurable to use Ollama (local).

### Shared Dependencies

| Dependency | Used By | Added In |
|------------|---------|----------|
| onnxruntime | Local STT (VAD), Local TTS (Piper), Hands-Free (openWakeWord) | v0.4 |
| espeakng-loader | Local TTS (Piper phonemization) | v0.7 (this ADR) |
| sounddevice | Audio recording, TTS playback | v0.1 |
| miniaudio | TTS audio decoding (MP3 and WAV) | v0.6 |

The onnxruntime dependency is increasingly central to the Local build. All three
local features (STT, TTS, Hands-Free) depend on it. This validates the two-build
strategy: the cloud-only build stays small, the local build bundles the full
onnxruntime + model ecosystem.

---

## Appendix A: espeak-ng Phonemization via ctypes

The espeak-ng C library provides `espeak_TextToPhonemes()` which converts text
to IPA phonemes. The ctypes wrapper is approximately:

```python
import ctypes
import espeakng_loader

def _init_espeak() -> ctypes.CDLL:
    """Load and initialize espeak-ng."""
    lib_path = espeakng_loader.get_library_path()
    data_path = espeakng_loader.get_data_path()
    lib = ctypes.CDLL(str(lib_path))

    # espeak_Initialize(output, buflength, path, options)
    # output=0 (AUDIO_OUTPUT_PLAYBACK, but we don't need audio)
    # options=1 (espeakINITIALIZE_DONT_EXIT)
    lib.espeak_Initialize.restype = ctypes.c_int
    lib.espeak_Initialize.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int
    ]
    result = lib.espeak_Initialize(
        0x02,  # AUDIO_OUTPUT_RETRIEVAL (no actual audio output)
        0,
        str(data_path).encode('utf-8'),
        0x8000,  # espeakINITIALIZE_DONT_EXIT
    )
    if result < 0:
        raise RuntimeError(f"espeak_Initialize failed: {result}")

    return lib


def phonemize_text(lib: ctypes.CDLL, text: str, language: str = "de") -> str:
    """Convert text to IPA phonemes using espeak-ng.

    Args:
        lib: Loaded espeak-ng library.
        text: Input text.
        language: Language code (e.g., "de", "en").

    Returns:
        Phoneme string in IPA format.
    """
    # Set voice/language
    lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    lib.espeak_SetVoiceByName.restype = ctypes.c_int
    lib.espeak_SetVoiceByName(language.encode('utf-8'))

    # espeak_TextToPhonemes(textptr, textmode, phonememode)
    # textmode: 0 = null-terminated string
    # phonememode: 0x02 = IPA
    text_bytes = text.encode('utf-8')
    text_ptr = ctypes.c_char_p(text_bytes)
    ptr_to_ptr = ctypes.pointer(text_ptr)

    lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
    lib.espeak_TextToPhonemes.argtypes = [
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int,
        ctypes.c_int,
    ]

    phonemes_parts = []
    while True:
        result = lib.espeak_TextToPhonemes(ptr_to_ptr, 1, 0x02)
        if result is None or result == b'':
            break
        phonemes_parts.append(result.decode('utf-8'))

    return ''.join(phonemes_parts)
```

**Fallback**: If ctypes phonemization proves fragile, use subprocess:

```python
import subprocess
import espeakng_loader

def phonemize_subprocess(text: str, language: str = "de") -> str:
    """Phonemize via espeak-ng subprocess (simpler but slower)."""
    exe_dir = Path(espeakng_loader.get_library_path()).parent
    # On Windows, espeak-ng.exe may not exist separately -- the DLL is
    # loaded by the Python process. If subprocess is needed, we need to
    # find or bundle the espeak-ng.exe separately.
    ...
```

The ctypes approach is preferred because it runs in-process (no subprocess
overhead, no need to bundle a separate exe).

## Appendix B: Piper Voice Audio Samples

Users can preview Piper voices at:
https://rhasspy.github.io/piper-samples/

This URL can be linked in the Settings dialog help text.

## Appendix C: Performance Expectations

Based on Piper benchmarks on Raspberry Pi 4 (ARM, 1.5 GHz):
- Inference: ~0.5x-1.0x real-time (i.e., 1 second of audio takes 0.5-1.0s)

On a modern desktop CPU (x86_64, 3+ GHz):
- Expected: ~5-10x real-time (1 second of audio in 100-200ms)
- 10-word sentence (~2s audio): 200-400ms inference
- 50-word paragraph (~10s audio): 1-2s inference

These are estimates based on the ~10x CPU performance difference between
a Raspberry Pi 4 and a modern desktop CPU. Actual benchmarks should be
collected during Phase 1 testing.
