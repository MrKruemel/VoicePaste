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
APP_VERSION = "0.2.0"

# Hotkey configuration
# Hotkey history:
# - "ctrl+windows": Broken — Windows intercepts Win-key at shell level.
# - "ctrl+shift+v": Conflicts with "Paste without formatting" in Chrome/Word.
# - "ctrl+alt+r": Safe — "Record" mnemonic, no known system conflicts.
DEFAULT_HOTKEY = "ctrl+alt+r"
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
