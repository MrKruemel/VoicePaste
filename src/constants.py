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
        PROCESSING -> IDLE  (on empty transcript, error)
        AWAITING_PASTE -> PASTING  (on Enter or delay elapsed, v0.9)
        AWAITING_PASTE -> IDLE  (on Escape cancel or timeout, v0.9)
        PASTING -> IDLE  (on paste complete)
        SPEAKING -> IDLE  (on TTS playback complete or Escape, v0.6)

        Pipeline queueing (v1.1):
        During PROCESSING, pressing the hotkey starts a new recording
        (tracked by _recording_during_processing flag, not a new state).
        When the current pipeline finishes, queued audio is processed
        before returning to IDLE.  Queue depth = 1.
    """

    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    AWAITING_PASTE = "awaiting_paste"
    PASTING = "pasting"
    SPEAKING = "speaking"


# Valid state transitions.  Key = (from_state, to_state).
# Any transition not in this set is invalid and will be logged as an error.
VALID_TRANSITIONS: frozenset[tuple[AppState, AppState]] = frozenset({
    # Normal recording flow
    (AppState.IDLE, AppState.RECORDING),
    (AppState.RECORDING, AppState.PROCESSING),
    (AppState.RECORDING, AppState.IDLE),          # cancel or no audio
    (AppState.PROCESSING, AppState.PASTING),
    (AppState.PROCESSING, AppState.AWAITING_PASTE),
    (AppState.PROCESSING, AppState.SPEAKING),     # TTS ask mode
    (AppState.PROCESSING, AppState.IDLE),          # empty transcript, error
    (AppState.AWAITING_PASTE, AppState.PASTING),
    (AppState.AWAITING_PASTE, AppState.IDLE),      # cancel or timeout
    (AppState.PASTING, AppState.IDLE),
    (AppState.SPEAKING, AppState.IDLE),

    # TTS / API dispatch flows (start from IDLE)
    (AppState.IDLE, AppState.PROCESSING),          # TTS pipeline, API tts
    (AppState.IDLE, AppState.SPEAKING),            # replay cached audio
})


# Application metadata
APP_NAME = "Voice Paste"
APP_VERSION = "0.9.1"

# Hotkey configuration
# Hotkey history:
# - "ctrl+windows": Broken — Windows intercepts Win-key at shell level.
# - "ctrl+shift+v": Conflicts with "Paste without formatting" in Chrome/Word.
# - "ctrl+alt+r": Safe — "Record" mnemonic, no known system conflicts.
DEFAULT_HOTKEY = "ctrl+alt+r"
DEFAULT_PROMPT_HOTKEY = "ctrl+alt+a"
CANCEL_HOTKEY = "escape"

# v0.6: TTS hotkeys
# Note: ctrl+alt+t is the GNOME Terminal shortcut on Ubuntu, so use
# ctrl+alt+s (Speak) on Linux to avoid conflict.
import sys as _sys
if _sys.platform == "win32":
    DEFAULT_TTS_HOTKEY = "ctrl+alt+t"      # T = Talk/TTS — read clipboard aloud
    DEFAULT_TTS_ASK_HOTKEY = "ctrl+alt+y"  # Y = adjacent to T — Ask AI + TTS
else:
    DEFAULT_TTS_HOTKEY = "ctrl+alt+s"      # S = Speak — avoids GNOME ctrl+alt+t
    DEFAULT_TTS_ASK_HOTKEY = "ctrl+alt+y"  # Y = Ask AI + TTS

# Audio configuration
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_DTYPE = "int16"
DEFAULT_AUDIO_DEVICE_INDEX: int | None = None  # None = system default microphone

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
DEFAULT_PASTE_SHORTCUT = "auto"  # "auto", "ctrl+v", "ctrl+shift+v"
PASTE_SHORTCUT_OPTIONS = ("auto", "ctrl+v", "ctrl+shift+v")

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
HANDSFREE_PIPELINES = ("ask_tts", "summary", "prompt", "claude_code")

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

# Transcription language: "auto" lets Whisper detect, or use a language code.
DEFAULT_TRANSCRIPTION_LANGUAGE = "de"
SUPPORTED_LANGUAGES = {
    "auto": "Auto-detect",
    "de": "Deutsch (German)",
    "en": "English",
    "fr": "Fran\u00e7ais (French)",
    "es": "Espa\u00f1ol (Spanish)",
    "it": "Italiano (Italian)",
    "pt": "Portugu\u00eas (Portuguese)",
    "nl": "Nederlands (Dutch)",
    "pl": "Polski (Polish)",
    "ja": "\u65e5\u672c\u8a9e (Japanese)",
    "zh": "\u4e2d\u6587 (Chinese)",
    "ko": "\ud55c\uad6d\uc5b4 (Korean)",
    "ru": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439 (Russian)",
}

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
        "sha256": {
            "de_DE-thorsten-medium.onnx": "7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819",
            "de_DE-thorsten-medium.onnx.json": "974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85",
        },
    },
    "de_DE-thorsten-high": {
        "label": "Thorsten (DE, high quality, larger)",
        "repo": "rhasspy/piper-voices",
        "files": "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx,"
                 "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {
            "de_DE-thorsten-high.onnx": "9df1c43c61149ef9b39e618e2b861fbe41e1fcea9390b2dac62e8761573ea4f1",
            "de_DE-thorsten-high.onnx.json": "6de734444e4c3f9e33b7ebe2746dbc19b71e85f613e79c65acf623200b99a76a",
        },
    },
    "de_DE-thorsten_emotional-medium": {
        "label": "Thorsten Emotional (DE, medium, multi-emotion)",
        "repo": "rhasspy/piper-voices",
        "files": "de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium.onnx,"
                 "de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium.onnx.json",
        "download_mb": "77",
        "sample_rate": "22050",
        "sha256": {
            "de_DE-thorsten_emotional-medium.onnx": "c1764e652266cd6dcebf1b95c61973df5970a5f5272e94b655ff1ddf9a99d1ff",
            "de_DE-thorsten_emotional-medium.onnx.json": "92895b9e99f7cfc13f4a9879da615c3d6e0baa4d660e26d7b685abdd27a6d1d3",
        },
    },
    "de_DE-mls-medium": {
        "label": "MLS (DE, medium quality)",
        "repo": "rhasspy/piper-voices",
        "files": "de/de_DE/mls/medium/de_DE-mls-medium.onnx,"
                 "de/de_DE/mls/medium/de_DE-mls-medium.onnx.json",
        "download_mb": "95",
        "sample_rate": "22050",
        "sha256": {
            "de_DE-mls-medium.onnx": "69cd1d2aa5a35839a518966fcc4924b5f93e5f8c948ed0752b1a616ad53f65bf",
            "de_DE-mls-medium.onnx.json": "b0af1c89ddfdc72d32e015729b0e89b99eec13c2c8caa1db7488d98e9e570b40",
        },
    },
    # --- English (US) voices ---
    "en_US-ryan-high": {
        "label": "Ryan (EN-US, high quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/ryan/high/en_US-ryan-high.onnx,"
                 "en/en_US/ryan/high/en_US-ryan-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {
            "en_US-ryan-high.onnx": "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca",
            "en_US-ryan-high.onnx.json": "c6d3b98f08315cb4bebf0d49d50fc4ff491b503c64b940cd3d5ca28543b48011",
        },
    },
    "en_US-ryan-medium": {
        "label": "Ryan (EN-US, medium quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/ryan/medium/en_US-ryan-medium.onnx,"
                 "en/en_US/ryan/medium/en_US-ryan-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {
            "en_US-ryan-medium.onnx": "abf4c274862564ed647ba0d2c47f8ee7c9b717d27bdad9219100eb310db4047a",
            "en_US-ryan-medium.onnx.json": "44034c056cb15681b2ad494307c7f3f2e4499d1253c700c711fa0a4607ffe78d",
        },
    },
    "en_US-lessac-high": {
        "label": "Lessac (EN-US, high quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/lessac/high/en_US-lessac-high.onnx,"
                 "en/en_US/lessac/high/en_US-lessac-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {
            "en_US-lessac-high.onnx": "4cabf7c3a638017137f34a1516522032d4fe3f38228a843cc9b764ddcbcd9e09",
            "en_US-lessac-high.onnx.json": "db42b97d9859f257bc1561b8ed980e7fb2398402050a74ddd6cbec931a92412f",
        },
    },
    "en_US-lessac-medium": {
        "label": "Lessac (EN-US, medium quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/lessac/medium/en_US-lessac-medium.onnx,"
                 "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {
            "en_US-lessac-medium.onnx": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
            "en_US-lessac-medium.onnx.json": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
        },
    },
    "en_US-amy-medium": {
        "label": "Amy (EN-US, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_US/amy/medium/en_US-amy-medium.onnx,"
                 "en/en_US/amy/medium/en_US-amy-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {
            "en_US-amy-medium.onnx": "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
            "en_US-amy-medium.onnx.json": "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
        },
    },
    # --- English (GB) voices ---
    "en_GB-cori-high": {
        "label": "Cori (EN-GB, high quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/cori/high/en_GB-cori-high.onnx,"
                 "en/en_GB/cori/high/en_GB-cori-high.onnx.json",
        "download_mb": "114",
        "sample_rate": "22050",
        "sha256": {
            "en_GB-cori-high.onnx": "470b4dd634c98f8a4850d7626ffc3dfc90774628eeef6605a6dd8f88f30a5903",
            "en_GB-cori-high.onnx.json": "9e7fb5b5671612c22f3c81cbe46c1ae87b031a4632bcb509e499dad6f1e2adec",
        },
    },
    "en_GB-cori-medium": {
        "label": "Cori (EN-GB, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/cori/medium/en_GB-cori-medium.onnx,"
                 "en/en_GB/cori/medium/en_GB-cori-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {
            "en_GB-cori-medium.onnx": "1899f98e5fb8310154f3c2973f4b8a929ba7245e722b3d3a85680b833d95f10d",
            "en_GB-cori-medium.onnx.json": "e262c16d7f192f69d4edd6b4ef8a5915379e67495fcc402f1ab15eeb33da3d36",
        },
    },
    "en_GB-alba-medium": {
        "label": "Alba (EN-GB, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/alba/medium/en_GB-alba-medium.onnx,"
                 "en/en_GB/alba/medium/en_GB-alba-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {
            "en_GB-alba-medium.onnx": "401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b",
            "en_GB-alba-medium.onnx.json": "aa965a2f02ecced632c2694e1fc72bbff6d65f265fab567ca945918c73dd89f4",
        },
    },
    "en_GB-jenny_dioco-medium": {
        "label": "Jenny (EN-GB, medium quality, female)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx,"
                 "en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {
            "en_GB-jenny_dioco-medium.onnx": "469c630d209e139dd392a66bf4abde4ab86390a0269c1e47b4e5d7ce81526b01",
            "en_GB-jenny_dioco-medium.onnx.json": "a9a7a93a317c9a3cb6563e37eb057df9ef09c06188a8a4341b0fcb58cba54dd4",
        },
    },
    "en_GB-alan-medium": {
        "label": "Alan (EN-GB, medium quality, male)",
        "repo": "rhasspy/piper-voices",
        "files": "en/en_GB/alan/medium/en_GB-alan-medium.onnx,"
                 "en/en_GB/alan/medium/en_GB-alan-medium.onnx.json",
        "download_mb": "64",
        "sample_rate": "22050",
        "sha256": {
            "en_GB-alan-medium.onnx": "0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330",
            "en_GB-alan-medium.onnx.json": "c0f0d124e5895c00e7c03b35dcc8287f319a6998a365b182deb5c8e752ee8c1e",
        },
    },
}

# --- v1.2: Claude Code CLI integration ---
DEFAULT_CLAUDE_CODE_ENABLED = False
DEFAULT_CLAUDE_CODE_HOTKEY = "ctrl+alt+c"
DEFAULT_CLAUDE_CODE_WORKING_DIR = ""      # empty = VoicePaste's cwd
DEFAULT_CLAUDE_CODE_SYSTEM_PROMPT = ""
DEFAULT_CLAUDE_CODE_TIMEOUT = 120         # seconds
DEFAULT_CLAUDE_CODE_RESPONSE_MODE = "speak"  # "paste" | "speak" | "both"
DEFAULT_CLAUDE_CODE_SKIP_PERMISSIONS = False
DEFAULT_CLAUDE_CODE_CONTINUE_CONVERSATION = False
CLAUDE_CODE_RESPONSE_MODES = ("paste", "speak", "both")

# --- v1.0: TTS Audio Cache configuration ---
DEFAULT_TTS_CACHE_ENABLED = True
DEFAULT_TTS_CACHE_MAX_SIZE_MB = 200
DEFAULT_TTS_CACHE_MAX_AGE_DAYS = 30
DEFAULT_TTS_CACHE_MAX_ENTRIES = 500
TTS_CACHE_TRAY_MENU_LIMIT = 10  # Max entries shown in tray submenu

# --- v1.0: TTS Audio Export configuration ---
DEFAULT_TTS_EXPORT_ENABLED = False
DEFAULT_TTS_EXPORT_PATH = ""  # Empty = ask user on first export

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
    "tiny": {
        "config.json": "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8",
        "model.bin": "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
    "base": {
        "config.json": "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
        "model.bin": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
    "small": {
        "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
    "medium": {
        "config.json": "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807",
        "model.bin": "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
    "large-v2": {
        "config.json": "d86b7a7664a12559d644aa210a32ce9a7e03913e794b7ea4fb7182de69e273a7",
        "model.bin": "bf2a9746382e1aa7ffff6b3a0d137ed9edbd9670c3b87e5d35f5e85e70d0333a",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
    "large-v3": {
        "config.json": "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
        "model.bin": "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
        "tokenizer.json": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
    },
}
