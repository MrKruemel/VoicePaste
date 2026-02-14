"""Shared constants and enums for the Voice-to-Summary Paste Tool."""

import enum


class AppState(enum.Enum):
    """Application state machine states.

    State transitions:
        IDLE -> RECORDING  (on hotkey press)
        RECORDING -> PROCESSING  (on hotkey press)
        RECORDING -> IDLE  (on Escape cancel, v0.2+)
        PROCESSING -> PASTING  (on STT/summarization complete)
        PASTING -> IDLE  (on paste complete)
        Any error state -> IDLE  (on error)
    """

    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    PASTING = "pasting"


# Application metadata
APP_NAME = "Voice Paste"
APP_VERSION = "0.5.0"

# Hotkey configuration
# Hotkey history:
# - "ctrl+windows": Broken — Windows intercepts Win-key at shell level.
# - "ctrl+shift+v": Conflicts with "Paste without formatting" in Chrome/Word.
# - "ctrl+alt+r": Safe — "Record" mnemonic, no known system conflicts.
DEFAULT_HOTKEY = "ctrl+alt+r"
DEFAULT_PROMPT_HOTKEY = "ctrl+alt+a"
CANCEL_HOTKEY = "escape"

# Audio configuration
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_DTYPE = "int16"

# API configuration
WHISPER_API_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-1"
API_TIMEOUT_SECONDS = 30

# Summarization configuration
SUMMARIZE_MODEL = "gpt-4o-mini"
SUMMARIZE_TEMPERATURE = 0.3
SUMMARIZE_MAX_TOKENS = 2048
SUMMARIZE_TIMEOUT_SECONDS = 15
SUMMARIZE_SYSTEM_PROMPT = (
    "Du bist ein Textbereinigungsassistent. Du erhaeltst rohe "
    "Sprache-zu-Text-Transkriptionen.\n\n"
    "Regeln:\n"
    "1. Entferne Fuellwoerter (aehm, also, halt, sozusagen, quasi, ne, ja, genau).\n"
    "2. Korrigiere Grammatik und Zeichensetzung.\n"
    "3. Bei Selbstkorrekturen: behalte nur die beabsichtigte Aussage.\n"
    "4. Kuerze den Text auf das Wesentliche, ohne Informationen zu verlieren.\n"
    "5. Antworte NUR mit dem bereinigten Text. Keine Erklaerungen, keine Kommentare.\n"
    "6. Antworte in derselben Sprache wie die Eingabe."
)

# Voice Prompt mode system prompt (v0.5)
# Used when the user speaks a question/command instead of dictating text.
PROMPT_SYSTEM_PROMPT = (
    "Du bist ein hilfreicher Assistent. "
    "Antworte praezise und in derselben Sprache wie die Frage."
)

# API retry configuration (ADR Section 14: retry with backoff)
API_MAX_RETRIES = 2
API_INITIAL_BACKOFF_SECONDS = 1.0

# Paste configuration
PASTE_DELAY_MS = 150

# Logging
LOG_FILENAME = "voice-paste.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Debounce
HOTKEY_DEBOUNCE_MS = 300

# Minimum recording duration in seconds
MIN_RECORDING_DURATION = 0.5

# Maximum recording duration in seconds (auto-stop after this)
MAX_RECORDING_DURATION_SECONDS = 300

# Audio cue frequencies and durations (v0.2+)
AUDIO_CUE_START_FREQS = (440, 880)  # Rising tone
AUDIO_CUE_STOP_FREQS = (880, 440)   # Falling tone
AUDIO_CUE_CANCEL_FREQ = 330         # Two low beeps
AUDIO_CUE_ERROR_FREQ = 220          # Single low buzz
AUDIO_CUE_TONE_DURATION_MS = 75     # Duration per tone
AUDIO_CUE_CANCEL_GAP_MS = 50        # Gap between cancel beeps

# --- v0.3: Keyring configuration ---
KEYRING_SERVICE_NAME = "VoicePaste"
KEYRING_OPENAI_KEY = "openai_api_key"
KEYRING_OPENROUTER_KEY = "openrouter_api_key"

# --- v0.3: Provider configuration ---
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
OLLAMA_DEFAULT_MODEL = "llama3.2"
SUMMARIZATION_PROVIDERS = ("openai", "openrouter", "ollama")
DEFAULT_SUMMARIZATION_PROVIDER = "openai"
OPENROUTER_DEFAULT_MODEL = "openai/gpt-4o-mini"

# --- v0.4: Local STT configuration ---

# STT backend options
STT_BACKENDS = ("cloud", "local")
DEFAULT_STT_BACKEND = "cloud"

# Local model sizes (CTranslate2-format Whisper models from Hugging Face)
LOCAL_MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v2", "large-v3")
LOCAL_STT_DEFAULT_MODEL_SIZE = "base"
LOCAL_STT_DEFAULT_DEVICE = "cpu"
LOCAL_STT_DEFAULT_COMPUTE_TYPE = "int8"
LOCAL_STT_DEFAULT_BEAM_SIZE = 5
LOCAL_STT_VALID_DEVICES = ("cpu", "cuda", "auto")
LOCAL_STT_VALID_COMPUTE_TYPES = ("int8", "float16", "float32", "auto")

# VAD (Voice Activity Detection) filter using Silero VAD via onnxruntime.
# When True, faster-whisper runs Silero VAD to skip non-speech segments before
# Whisper inference.  This improves accuracy on long recordings with silence,
# but requires onnxruntime and the bundled Silero ONNX model files.
#
# KNOWN ISSUE: In PyInstaller --onefile bundles, onnxruntime can crash the
# process (native segfault, no Python traceback) when loading the Silero ONNX
# model from the _MEI* temp directory.  The frozen-exe default is therefore
# False.  Users can re-enable VAD in config.toml once confirmed working.
LOCAL_STT_DEFAULT_VAD_FILTER = True
LOCAL_STT_FROZEN_VAD_FILTER = False

# Local model display information (for Settings dialog)
LOCAL_MODEL_DISPLAY: dict[str, dict[str, str]] = {
    "tiny": {
        "label": "Tiny (~75 MB, fastest, lower quality)",
        "download_mb": "75",
        "ram_mb": "~150",
    },
    "base": {
        "label": "Base (~145 MB, good quality, recommended)",
        "download_mb": "145",
        "ram_mb": "~200",
    },
    "small": {
        "label": "Small (~480 MB, better quality)",
        "download_mb": "480",
        "ram_mb": "~350",
    },
    "medium": {
        "label": "Medium (~1.5 GB, high quality, slow on CPU)",
        "download_mb": "1500",
        "ram_mb": "~600",
    },
    "large-v2": {
        "label": "Large v2 (~3 GB, highest quality, very slow on CPU)",
        "download_mb": "3000",
        "ram_mb": "~1200",
    },
    "large-v3": {
        "label": "Large v3 (~3 GB, newest, very slow on CPU)",
        "download_mb": "3000",
        "ram_mb": "~1200",
    },
}
