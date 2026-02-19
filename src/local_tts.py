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
    by thewh1teagle (MIT license). The phonemization is implemented
    directly via espeak-ng ctypes calls, avoiding the GPL-licensed
    phonemizer package.

Thread safety:
    The ONNX InferenceSession is NOT thread-safe. However, the
    application's state machine guarantees that only one pipeline
    thread calls synthesize() at a time (PROCESSING state is
    single-threaded). Model loading uses a Lock for safety.

v0.7: Initial implementation.
"""

import ctypes
import io
import json
import logging
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from tts import TTSError

logger = logging.getLogger(__name__)

# Piper VITS model special tokens
_BOS = "^"  # Beginning of sequence
_EOS = "$"  # End of sequence
_PAD = "_"  # Padding (inserted between every phoneme for alignment)

# Sentinel to track whether espeakng-loader is available
_espeakng_available: Optional[bool] = None


def is_espeakng_available() -> bool:
    """Check if espeakng-loader is installed and the DLL can be loaded.

    Caches the result after the first call.

    Returns:
        True if espeakng-loader is available, False otherwise.
    """
    global _espeakng_available
    if _espeakng_available is not None:
        return _espeakng_available

    try:
        import espeakng_loader  # noqa: F401

        # Verify the DLL exists
        lib_path = espeakng_loader.get_library_path()
        data_path = espeakng_loader.get_data_path()
        if not Path(lib_path).exists():
            logger.warning(
                "espeakng-loader installed but DLL not found at: %s", lib_path
            )
            _espeakng_available = False
            return False
        if not Path(data_path).exists():
            logger.warning(
                "espeakng-loader installed but data not found at: %s", data_path
            )
            _espeakng_available = False
            return False

        _espeakng_available = True
        logger.info("espeakng-loader is available: lib=%s", lib_path)

    except ImportError:
        _espeakng_available = False
        logger.info(
            "espeakng-loader is not installed. Local TTS unavailable."
        )

    return _espeakng_available


def _is_onnxruntime_available() -> bool:
    """Check if onnxruntime is installed.

    Returns:
        True if onnxruntime can be imported, False otherwise.
    """
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


class EspeakPhonemizerError(TTSError):
    """Raised when espeak-ng phonemization fails."""


class EspeakPhonemizer:
    """Phonemize text using espeak-ng via ctypes.

    Loads the espeak-ng shared library from espeakng-loader and calls
    espeak_TextToPhonemes() to convert text to IPA phonemes. This avoids
    depending on the GPL-licensed phonemizer Python package.

    The IPA output includes stress markers (primary and secondary), which
    Piper voice models expect in their phoneme_id_map.

    Thread safety: espeak-ng is NOT thread-safe. Must be called from a
    single thread at a time (guaranteed by the state machine).
    """

    def __init__(self) -> None:
        """Initialize the phonemizer. Loads espeak-ng DLL."""
        self._lib: Optional[ctypes.CDLL] = None
        self._initialized = False
        self._current_language: Optional[str] = None

    def _ensure_initialized(self) -> None:
        """Load and initialize espeak-ng if not already done.

        Raises:
            EspeakPhonemizerError: If initialization fails.
        """
        if self._initialized:
            return

        try:
            import espeakng_loader

            lib_path = espeakng_loader.get_library_path()
            data_path = str(espeakng_loader.get_data_path())

            self._lib = ctypes.CDLL(str(lib_path))

            # espeak_Initialize(output, buflength, path, options)
            # output=0x02 (AUDIO_OUTPUT_RETRIEVAL: no actual audio output)
            # options=0x8000 (espeakINITIALIZE_DONT_EXIT)
            self._lib.espeak_Initialize.restype = ctypes.c_int
            self._lib.espeak_Initialize.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
            ]
            result = self._lib.espeak_Initialize(
                0x02,       # AUDIO_OUTPUT_RETRIEVAL
                0,          # buflength (default)
                data_path.encode("utf-8"),
                0x8000,     # espeakINITIALIZE_DONT_EXIT
            )
            if result < 0:
                raise EspeakPhonemizerError(
                    f"espeak_Initialize failed with code {result}. "
                    f"Data path: {data_path}"
                )

            # Set up function signatures
            self._lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
            self._lib.espeak_SetVoiceByName.restype = ctypes.c_int

            self._lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
            self._lib.espeak_TextToPhonemes.argtypes = [
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.c_int,   # textmode: 1=UTF-8
                ctypes.c_int,   # phonememode: 0x02=IPA with stress
            ]

            self._initialized = True
            logger.info(
                "espeak-ng initialized: sample_rate=%d, data=%s",
                result,
                data_path,
            )

        except ImportError as e:
            raise EspeakPhonemizerError(
                "espeakng-loader is not installed. "
                "Install with: pip install espeakng-loader"
            ) from e
        except OSError as e:
            raise EspeakPhonemizerError(
                f"Failed to load espeak-ng DLL: {e}"
            ) from e
        except EspeakPhonemizerError:
            raise
        except Exception as e:
            raise EspeakPhonemizerError(
                f"espeak-ng initialization failed: {type(e).__name__}: {e}"
            ) from e

    def _set_language(self, language: str) -> None:
        """Set the espeak-ng voice/language.

        Args:
            language: Language code (e.g., "de", "en", "en-us").

        Raises:
            EspeakPhonemizerError: If the language is not supported.
        """
        if self._current_language == language:
            return

        result = self._lib.espeak_SetVoiceByName(language.encode("utf-8"))
        if result != 0:
            raise EspeakPhonemizerError(
                f"espeak-ng does not support language '{language}' "
                f"(error code {result})."
            )
        self._current_language = language
        logger.debug("espeak-ng language set to: %s", language)

    def phonemize(self, text: str, language: str = "de") -> str:
        """Convert text to IPA phonemes using espeak-ng.

        Calls espeak_TextToPhonemes() which processes the text word by
        word, returning IPA phoneme strings. The output includes stress
        markers (primary U+02C8 and secondary U+02CC) which Piper models
        expect.

        Args:
            text: Input text to phonemize.
            language: Language code (default: "de" for German).

        Returns:
            Phoneme string in IPA format with stress markers.

        Raises:
            EspeakPhonemizerError: If phonemization fails.
        """
        if not text or not text.strip():
            return ""

        self._ensure_initialized()
        self._set_language(language)

        try:
            text_bytes = text.encode("utf-8")
            text_ptr = ctypes.c_char_p(text_bytes)
            ptr_to_ptr = ctypes.pointer(text_ptr)

            phoneme_parts: list[str] = []

            while True:
                result = self._lib.espeak_TextToPhonemes(
                    ptr_to_ptr,
                    1,      # textmode: UTF-8 input
                    0x02,   # phonememode: IPA with stress markers
                )
                if result is None or result == b"":
                    break
                phoneme_parts.append(result.decode("utf-8"))

            phonemes = " ".join(phoneme_parts)

            if not phonemes.strip():
                logger.warning(
                    "espeak-ng returned empty phonemes for text of length %d.",
                    len(text),
                )

            logger.debug(
                "Phonemized %d chars -> %d phoneme chars",
                len(text),
                len(phonemes),
            )
            return phonemes

        except EspeakPhonemizerError:
            raise
        except Exception as e:
            raise EspeakPhonemizerError(
                f"Phonemization failed: {type(e).__name__}: {e}"
            ) from e

    def cleanup(self) -> None:
        """Release espeak-ng resources.

        Note: espeak-ng does not have a clean shutdown API. The DLL stays
        loaded until the process exits. This method resets internal state.
        """
        self._initialized = False
        self._current_language = None
        self._lib = None


class PiperLocalTTS:
    """Local TTS backend using Piper ONNX models.

    Implements the TTSBackend Protocol. Loads the voice model lazily
    on first synthesize() call. Thread safety: same as LocalWhisperSTT
    (state machine guarantees single-thread access).

    The synthesis pipeline:
        1. Phonemize text via espeak-ng (ctypes)
        2. Convert phoneme characters to integer IDs using the model's
           phoneme_id_map (from .onnx.json config)
        3. Run ONNX inference (VITS model)
        4. Convert float32 PCM output to WAV bytes

    Attributes:
        voice_name: Piper voice name (e.g., "de_DE-thorsten-medium").
    """

    def __init__(
        self,
        voice_name: str,
        model_dir: Optional[Path] = None,
    ) -> None:
        """Initialize the Piper local TTS backend.

        The ONNX model is NOT loaded during __init__. It is loaded
        lazily on the first synthesize() call. Call load_model()
        explicitly to pre-load.

        Args:
            voice_name: Piper voice name (e.g., "de_DE-thorsten-medium").
            model_dir: Explicit path to the model directory containing
                the .onnx and .onnx.json files. If None, resolves from
                the standard cache directory.
        """
        self._voice_name = voice_name
        self._model_dir = model_dir
        self._session: Optional[object] = None  # ort.InferenceSession
        self._config: Optional[dict] = None
        self._phoneme_id_map: Optional[dict[str, list[int]]] = None
        self._sample_rate: int = 22050
        self._inference_params: dict = {}
        self._session_input_names: list[str] = []
        self._load_lock = threading.Lock()
        self._loaded = False
        self._phonemizer = EspeakPhonemizer()

        logger.info(
            "PiperLocalTTS initialized: voice=%s, model_dir=%s",
            voice_name,
            model_dir or "(auto/cache)",
        )

    @property
    def voice_name(self) -> str:
        """The Piper voice name."""
        return self._voice_name

    @property
    def is_model_loaded(self) -> bool:
        """Whether the ONNX model is currently loaded in memory."""
        return self._loaded and self._session is not None

    def load_model(self) -> None:
        """Load the ONNX model and voice config into memory.

        Thread-safe via lock. Can take 1-3 seconds depending on model
        size. Call in a background thread to avoid blocking the UI.

        Raises:
            TTSError: If the model cannot be loaded.
        """
        with self._load_lock:
            if self._loaded and self._session is not None:
                logger.debug("Piper model already loaded, skipping.")
                return

            model_dir = self._resolve_model_dir()
            if model_dir is None:
                raise TTSError(
                    f"Piper voice model '{self._voice_name}' is not "
                    f"downloaded.\n\n"
                    f"Download it via Settings > Text-to-Speech > "
                    f"Download Model."
                )

            # Find the .onnx and .onnx.json files
            onnx_files = list(model_dir.glob("*.onnx"))
            json_files = list(model_dir.glob("*.onnx.json"))

            if not onnx_files:
                raise TTSError(
                    f"No .onnx model file found in {model_dir}.\n"
                    f"The model may be corrupted. Try re-downloading."
                )
            if not json_files:
                raise TTSError(
                    f"No .onnx.json config file found in {model_dir}.\n"
                    f"The model may be corrupted. Try re-downloading."
                )

            onnx_path = onnx_files[0]
            json_path = json_files[0]

            logger.info(
                "Loading Piper model: %s (%s)",
                onnx_path.name,
                f"{onnx_path.stat().st_size / 1024 / 1024:.1f} MB",
            )

            t0 = time.monotonic()

            # Load config
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                raise TTSError(
                    f"Failed to read voice config {json_path.name}: {e}"
                ) from e

            self._phoneme_id_map = self._config.get("phoneme_id_map", {})
            self._sample_rate = self._config.get("audio", {}).get(
                "sample_rate", 22050
            )
            self._inference_params = self._config.get("inference", {})

            if not self._phoneme_id_map:
                raise TTSError(
                    f"Voice config {json_path.name} has no phoneme_id_map. "
                    f"The model may be corrupted."
                )

            # Load ONNX session
            try:
                import onnxruntime as ort

                sess_options = ort.SessionOptions()
                # Intra-op parallelism for VITS inference
                sess_options.intra_op_num_threads = 2
                # Disable graph optimization logging
                sess_options.log_severity_level = 3

                self._session = ort.InferenceSession(
                    str(onnx_path),
                    sess_options=sess_options,
                    providers=["CPUExecutionProvider"],
                )
                self._session_input_names = [
                    i.name for i in self._session.get_inputs()
                ]

            except ImportError as e:
                raise TTSError(
                    "onnxruntime is not installed. "
                    "Local TTS requires the Local build.\n\n"
                    "Install with: pip install onnxruntime"
                ) from e
            except Exception as e:
                raise TTSError(
                    f"Failed to load ONNX model {onnx_path.name}: "
                    f"{type(e).__name__}: {e}"
                ) from e

            self._loaded = True
            elapsed = time.monotonic() - t0
            logger.info(
                "Piper model loaded in %.2f seconds. "
                "Sample rate: %d Hz, phonemes: %d",
                elapsed,
                self._sample_rate,
                len(self._phoneme_id_map),
            )

    def unload_model(self) -> None:
        """Unload the ONNX model from memory.

        Frees memory. The model can be reloaded by the next
        synthesize() call or an explicit load_model() call.
        """
        with self._load_lock:
            if self._session is not None:
                logger.info("Unloading Piper model '%s'...", self._voice_name)
                del self._session
                self._session = None
                self._config = None
                self._phoneme_id_map = None
                self._loaded = False

                import gc
                gc.collect()
                logger.info("Piper model unloaded.")

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to WAV audio bytes.

        Implements the TTSBackend Protocol. Returns WAV bytes (with
        header) that can be decoded by miniaudio or any standard WAV
        decoder.

        Args:
            text: Text to synthesize.

        Returns:
            WAV-encoded audio bytes.

        Raises:
            TTSError: If synthesis fails.
        """
        if not text or not text.strip():
            raise TTSError("Cannot synthesize empty text.")

        # Lazy load model on first use
        if not self._loaded or self._session is None:
            self.load_model()

        t0 = time.monotonic()

        try:
            # Step 1: Determine language from voice name
            language = self._get_language()

            # Step 2: Phonemize text
            phonemes = self._phonemizer.phonemize(text, language=language)
            if not phonemes.strip():
                raise TTSError(
                    "Phonemization returned empty result. "
                    "The text may contain only unsupported characters."
                )

            # Step 3: Convert phonemes to integer IDs
            phoneme_ids = self._phonemes_to_ids(phonemes)

            # Step 4: Run ONNX inference
            pcm_float32 = self._infer(phoneme_ids)

            # Step 5: Convert to WAV bytes
            wav_bytes = self._pcm_to_wav(pcm_float32, self._sample_rate)

            elapsed = time.monotonic() - t0
            audio_duration = len(pcm_float32) / self._sample_rate

            logger.info(
                "Piper TTS: %d chars -> %.1fs audio in %.2fs "
                "(%.1fx realtime), %d bytes WAV",
                len(text),
                audio_duration,
                elapsed,
                audio_duration / max(elapsed, 0.001),
                len(wav_bytes),
            )

            return wav_bytes

        except TTSError:
            raise
        except MemoryError as e:
            raise TTSError(
                "Out of memory during TTS synthesis.\n"
                "Try shorter text or close other applications."
            ) from e
        except Exception as e:
            logger.error(
                "Piper TTS error: %s: %s", type(e).__name__, e
            )
            raise TTSError(
                f"Local TTS synthesis failed: {type(e).__name__}: {e}"
            ) from e

    def _resolve_model_dir(self) -> Optional[Path]:
        """Resolve the model directory path.

        If model_dir was provided at init, uses that. Otherwise, looks
        in the standard cache directory.

        Returns:
            Path to the model directory, or None if not found.
        """
        if self._model_dir is not None:
            if self._model_dir.exists():
                return self._model_dir
            logger.warning(
                "Explicit model_dir does not exist: %s", self._model_dir
            )
            return None

        # Look in standard cache directory
        try:
            from tts_model_manager import get_tts_model_path

            return get_tts_model_path(self._voice_name)
        except ImportError:
            logger.debug("tts_model_manager not available.")
            return None

    def _get_language(self) -> str:
        """Extract the language code from the voice config or name.

        Returns:
            Language code (e.g., "de", "en").
        """
        # First, check the config file
        if self._config:
            espeak_config = self._config.get("espeak", {})
            voice = espeak_config.get("voice", "")
            if voice:
                return voice

        # Fall back to parsing the voice name (e.g., "de_DE-thorsten-medium")
        if "_" in self._voice_name:
            return self._voice_name.split("_")[0]

        return "de"  # Default to German

    def _phonemes_to_ids(self, phonemes: str) -> list[int]:
        """Convert a phoneme string to a list of integer IDs.

        Uses the model's phoneme_id_map from the .onnx.json config.
        Each phoneme character is mapped to its ID(s), with PAD tokens
        inserted between every phoneme for alignment (as required by
        VITS architecture).

        BOS (^) is prepended and EOS ($) is appended.

        Characters not in the phoneme_id_map are silently skipped
        with a debug-level log.

        Args:
            phonemes: IPA phoneme string from espeak-ng.

        Returns:
            List of integer phoneme IDs.
        """
        ids: list[int] = []

        # Prepend BOS token
        if _BOS in self._phoneme_id_map:
            ids.extend(self._phoneme_id_map[_BOS])
            if _PAD in self._phoneme_id_map:
                ids.extend(self._phoneme_id_map[_PAD])

        skipped_chars: set[str] = set()

        for char in phonemes:
            if char in self._phoneme_id_map:
                ids.extend(self._phoneme_id_map[char])
                if _PAD in self._phoneme_id_map:
                    ids.extend(self._phoneme_id_map[_PAD])
            else:
                skipped_chars.add(char)

        # Append EOS token
        if _EOS in self._phoneme_id_map:
            ids.extend(self._phoneme_id_map[_EOS])

        if skipped_chars:
            logger.debug(
                "Skipped %d unmapped phoneme characters: %s",
                len(skipped_chars),
                [hex(ord(c)) for c in skipped_chars],
            )

        return ids

    def _infer(self, phoneme_ids: list[int]) -> np.ndarray:
        """Run ONNX model inference to generate audio.

        Args:
            phoneme_ids: List of integer phoneme IDs.

        Returns:
            1-D numpy array of float32 PCM samples.

        Raises:
            TTSError: If inference fails.
        """
        # Build input tensors
        ids_array = np.expand_dims(
            np.array(phoneme_ids, dtype=np.int64), 0
        )
        lengths_array = np.array([ids_array.shape[1]], dtype=np.int64)

        noise_scale = self._inference_params.get("noise_scale", 0.667)
        length_scale = self._inference_params.get("length_scale", 1.0)
        noise_w = self._inference_params.get("noise_w", 0.8)
        scales_array = np.array(
            [noise_scale, length_scale, noise_w], dtype=np.float32
        )

        inputs = {
            "input": ids_array,
            "input_lengths": lengths_array,
            "scales": scales_array,
        }

        # Add speaker ID if the model supports multiple speakers
        if "sid" in self._session_input_names:
            inputs["sid"] = np.array([0], dtype=np.int64)

        try:
            output = self._session.run(None, inputs)
        except Exception as e:
            raise TTSError(
                f"ONNX inference failed: {type(e).__name__}: {e}"
            ) from e

        # Output shape: [1, 1, num_samples] -- squeeze to 1-D
        samples = output[0].squeeze()

        if samples.ndim == 0:
            raise TTSError("ONNX model returned empty audio output.")

        return samples

    @staticmethod
    def _pcm_to_wav(pcm: np.ndarray, sample_rate: int) -> bytes:
        """Convert float32 PCM array to WAV bytes.

        Scales float32 [-1, 1] to int16 [-32767, 32767] and writes
        a standard WAV header.

        Args:
            pcm: 1-D float32 numpy array of audio samples.
            sample_rate: Audio sample rate in Hz.

        Returns:
            Complete WAV file as bytes.
        """
        # Clip to [-1, 1] to prevent int16 overflow
        pcm_clipped = np.clip(pcm, -1.0, 1.0)
        pcm_int16 = (pcm_clipped * 32767).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_int16.tobytes())

        return buf.getvalue()
