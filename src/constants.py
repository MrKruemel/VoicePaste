"""Shared constants and enums for the Voice-to-Summary Paste Tool."""

import enum
from typing import Any


class AppState(enum.Enum):
    """Application state machine states.

    State transitions:
        IDLE -> RECORDING  (on hotkey press)
        RECORDING -> PROCESSING  (on hotkey press)
        RECORDING -> IDLE  (on Escape cancel, v0.2+)
        PROCESSING -> PASTING  (on STT/summarization complete)
        PROCESSING -> AWAITING_PASTE  (when confirmation/delay configured, v0.9)
        PROCESSING -> SPEAKING  (on Ask AI + TTS pipeline, v0.6)
        AWAITING_PASTE -> PASTING  (on Enter or delay elapsed, v0.9)
        AWAITING_PASTE -> IDLE  (on Escape cancel or timeout, v0.9)
        PASTING -> IDLE  (on paste complete)
        SPEAKING -> IDLE  (on TTS playback complete or Escape, v0.6)
        Any error state -> IDLE  (on error)
    """

    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    AWAITING_PASTE = "awaiting_paste"
    PASTING = "pasting"
    SPEAKING = "speaking"


# Application metadata
APP_NAME = "Voice Paste"
APP_VERSION = "0.9.0"

# Hotkey configuration
# Hotkey history:
# - "ctrl+windows": Broken — Windows intercepts Win-key at shell level.
# - "ctrl+shift+v": Conflicts with "Paste without formatting" in Chrome/Word.
# - "ctrl+alt+r": Safe — "Record" mnemonic, no known system conflicts.
DEFAULT_HOTKEY = "ctrl+alt+r"
DEFAULT_PROMPT_HOTKEY = "ctrl+alt+a"
CANCEL_HOTKEY = "escape"

# v0.6: TTS hotkeys
DEFAULT_TTS_HOTKEY = "ctrl+alt+t"          # T = Talk/TTS — read clipboard aloud
DEFAULT_TTS_ASK_HOTKEY = "ctrl+alt+y"      # Y = adjacent to T — Ask AI + TTS

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

# v0.9: HTTP API configuration
DEFAULT_API_ENABLED = False
DEFAULT_API_PORT = 18923

# v0.9: Confirm-before-paste configuration
DEFAULT_PASTE_CONFIRM = False
DEFAULT_PASTE_DELAY_SECONDS = 0.0
DEFAULT_PASTE_CONFIRMATION_TIMEOUT = 30.0
DEFAULT_PASTE_AUTO_ENTER = False
PASTE_COUNTDOWN_BEEP_FREQ = 880
PASTE_COUNTDOWN_BEEP_DURATION_MS = 30

# v0.9: Hands-Free Mode configuration
DEFAULT_HANDSFREE_ENABLED = False
DEFAULT_WAKE_PHRASE = "Hello Cloud"
DEFAULT_WAKE_PHRASE_MATCH_MODE = "contains"  # "contains", "startswith", "fuzzy"
DEFAULT_SILENCE_TIMEOUT_SECONDS = 3.0
DEFAULT_HANDSFREE_MAX_RECORDING_SECONDS = 120
DEFAULT_HANDSFREE_PIPELINE = "ask_tts"  # "ask_tts", "summary", "prompt"
DEFAULT_HANDSFREE_COOLDOWN_SECONDS = 3.0
DEFAULT_HANDSFREE_BUFFER_SECONDS = 2.5
HANDSFREE_PIPELINES = ("ask_tts", "summary", "prompt")

# Wake word confirmation tone: rising triple chirp
AUDIO_CUE_WAKEWORD_FREQS = (660, 880, 1100)
AUDIO_CUE_WAKEWORD_DURATION_MS = 60

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
KEYRING_ELEVENLABS_KEY = "elevenlabs_api_key"

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

# --- v0.6: TTS (Text-to-Speech) configuration ---
# v0.7: Added "piper" for local offline TTS
TTS_PROVIDERS = ("elevenlabs", "piper")
DEFAULT_TTS_PROVIDER = "elevenlabs"
DEFAULT_TTS_VOICE_ID = "pFZP5JQG7iQjIQuC4Bku"  # Lily — ElevenLabs default
DEFAULT_TTS_MODEL_ID = "eleven_flash_v2_5"        # Low-latency flash model
DEFAULT_TTS_OUTPUT_FORMAT = "mp3_44100_128"

# Predefined ElevenLabs voices for the Settings dropdown
ELEVENLABS_VOICE_PRESETS: dict[str, dict[str, str]] = {
    "pFZP5JQG7iQjIQuC4Bku": {"name": "Lily", "description": "Female, warm, DE/EN"},
    "nPczCjzI2devNBz1zQrb": {"name": "Brian", "description": "Male, narrative, EN"},
    "EXAVITQu4vr4xnSDxMaL": {"name": "Sarah", "description": "Female, soft, EN"},
    "JBFqnCBsd6RMkjVDRZzb": {"name": "George", "description": "Male, warm, EN"},
    "onwK4e9ZLuTAKqWW03F9": {"name": "Daniel", "description": "Male, authoritative, EN/DE"},
}

# TTS audio cue: confirmation tone when TTS stops (660 Hz -> 440 Hz, 75ms each)
AUDIO_CUE_TTS_STOP_FREQS = (660, 440)
AUDIO_CUE_TTS_STOP_DURATION_MS = 75

# Max text length for TTS (prevent accidentally reading huge clipboard content)
TTS_MAX_TEXT_LENGTH = 10000

# --- v0.7: Piper local TTS configuration ---
DEFAULT_PIPER_VOICE = "de_DE-thorsten-medium"

# Piper voice model registry.
# Each entry maps a voice name to its Hugging Face repo path and metadata.
# Models are downloaded on demand by the user via Settings > TTS > Download.
#
# The "sha256" field maps each downloaded filename to its expected SHA256
# hex digest.  An empty dict means "hashes not yet computed -- skip
# verification but log a warning".  Populate hashes after downloading
# each model once and copying the computed values from the log output.
# (Security finding SEC-040)
PIPER_VOICE_MODELS: dict[str, dict[str, Any]] = {
    "de_DE-thorsten-medium": {
        "label": "Thorsten (DE, medium quality, recommended)",
        "repo": "rhasspy/piper-voices",
        "files": "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx,"
                 "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json",
        "download_mb": "63",
        "sample_rate": "22050",
        "sha256": {},  # Populate after first download from log output
    },
    "de_DE-thorsten-high": {
        "label": "Thorsten (DE, high quality, larger)",
        "repo": "rhasspy/piper-voices",
        "files": "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx,"
                 "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {},
    },
    "de_DE-thorsten_emotional-medium": {
        "label": "Thorsten Emotional (DE, medium, multi-emotion)",
        "repo": "rhasspy/piper-voices",
        "files": "de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium.onnx,"
                 "de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium.onnx.json",
        "download_mb": "77",
        "sample_rate": "22050",
        "sha256": {},
    },
    "de_DE-mls-medium": {
        "label": "MLS (DE, medium quality)",
        "repo": "rhasspy/piper-voices",
        "files": "de/de_DE/mls/medium/de_DE-mls-medium.onnx,"
                 "de/de_DE/mls/medium/de_DE-mls-medium.onnx.json",
        "download_mb": "95",
        "sample_rate": "22050",
        "sha256": {},
    },
    # --- English (US) voices ---
    "en_US-ryan-high": {
        "label": "Ryan (EN-US, high quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/ryan/high/en_US-ryan-high.onnx,"
                 "en/en_US/ryan/high/en_US-ryan-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_US-ryan-medium": {
        "label": "Ryan (EN-US, medium quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/ryan/medium/en_US-ryan-medium.onnx,"
                 "en/en_US/ryan/medium/en_US-ryan-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_US-lessac-high": {
        "label": "Lessac (EN-US, high quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/lessac/high/en_US-lessac-high.onnx,"
                 "en/en_US/lessac/high/en_US-lessac-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_US-lessac-medium": {
        "label": "Lessac (EN-US, medium quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/lessac/medium/en_US-lessac-medium.onnx,"
                 "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_US-amy-medium": {
        "label": "Amy (EN-US, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/amy/medium/en_US-amy-medium.onnx,"
                 "en/en_US/amy/medium/en_US-amy-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {},
    },
    # --- English (GB) voices ---
    "en_GB-cori-high": {
        "label": "Cori (EN-GB, high quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/cori/high/en_GB-cori-high.onnx,"
                 "en/en_GB/cori/high/en_GB-cori-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_GB-cori-medium": {
        "label": "Cori (EN-GB, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/cori/medium/en_GB-cori-medium.onnx,"
                 "en/en_GB/cori/medium/en_GB-cori-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_GB-alba-medium": {
        "label": "Alba (EN-GB, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/alba/medium/en_GB-alba-medium.onnx,"
                 "en/en_GB/alba/medium/en_GB-alba-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_GB-jenny_dioco-medium": {
        "label": "Jenny (EN-GB, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx,"
                 "en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {},
    },
    "en_GB-alan-medium": {
        "label": "Alan (EN-GB, medium quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/alan/medium/en_GB-alan-medium.onnx,"
                 "en/en_GB/alan/medium/en_GB-alan-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {},
    },
}

# Parse the comma-separated file paths into lists for runtime use.
# This is done here rather than using list literals in the dict above
# because TOML-style constants should use simple string values.
for _voice_name, _voice_info in PIPER_VOICE_MODELS.items():
    _files_str = _voice_info.get("files", "")
    if isinstance(_files_str, str):
        _voice_info["files"] = [f.strip() for f in _files_str.split(",") if f.strip()]

# --- SHA256 integrity hashes for STT (Whisper) models (SEC-027) ---
# Maps model size -> {filename: expected_sha256_hex_digest}.
# Only critical files (model.bin, config.json) are verified.
# An empty inner dict means "hashes not yet computed -- skip verification
# but log a warning".  Populate after first download from log output.
STT_MODEL_SHA256: dict[str, dict[str, str]] = {
    "tiny": {},
    "base": {},
    "small": {},
    "medium": {},
    "large-v2": {},
    "large-v3": {},
}
