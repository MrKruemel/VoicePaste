"""Configuration loading, validation, and persistence for VoicePaste.

Reads config.toml from the application directory. Creates a template if missing.
v0.3: Mutable AppConfig, keyring integration, migration, save_to_toml(),
      provider/model/base_url/custom_prompt fields.
v0.4: Local STT fields (stt_backend, local_model_size, local_device, local_compute_type).
"""

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from constants import (
    CLAUDE_CODE_RESPONSE_MODES,
    DEFAULT_AUDIO_DEVICE_INDEX,
    DEFAULT_CLAUDE_CODE_CONTINUE_CONVERSATION,
    DEFAULT_CLAUDE_CODE_ENABLED,
    DEFAULT_TERMINAL_MODE_HOTKEY,
    DEFAULT_CLAUDE_CODE_HOTKEY,
    DEFAULT_CLAUDE_CODE_RESPONSE_MODE,
    DEFAULT_CLAUDE_CODE_SKIP_PERMISSIONS,
    DEFAULT_CLAUDE_CODE_SYSTEM_PROMPT,
    DEFAULT_CLAUDE_CODE_TIMEOUT,
    DEFAULT_CLAUDE_CODE_WORKING_DIR,
    DEFAULT_HANDSFREE_COOLDOWN_SECONDS,
    DEFAULT_HANDSFREE_ENABLED,
    DEFAULT_HANDSFREE_MAX_RECORDING_SECONDS,
    DEFAULT_HANDSFREE_SILENCE_THRESHOLD_RMS,
    DEFAULT_HANDSFREE_WAKE_MODEL_SIZE,
    HANDSFREE_WAKE_MODEL_SIZES,
    DEFAULT_TRANSCRIPTION_LANGUAGE,
    SUPPORTED_LANGUAGES,
    DEFAULT_HANDSFREE_PIPELINE,
    DEFAULT_HOTKEY,
    DEFAULT_API_ENABLED,
    DEFAULT_API_PORT,
    DEFAULT_PASTE_AUTO_ENTER,
    DEFAULT_PASTE_CONFIRM,
    DEFAULT_PASTE_CONFIRMATION_TIMEOUT,
    DEFAULT_PASTE_DELAY_SECONDS,
    DEFAULT_PASTE_SHORTCUT,
    DEFAULT_AUDIO_FX_BASS_DB,
    DEFAULT_AUDIO_FX_ENABLED,
    DEFAULT_AUDIO_FX_FORMANT_SHIFT,
    DEFAULT_AUDIO_FX_PITCH_SEMITONES,
    DEFAULT_AUDIO_FX_REVERB_MIX,
    DEFAULT_AUDIO_FX_TREBLE_DB,
    DEFAULT_PIPER_VOICE,
    DEFAULT_PROMPT_HOTKEY,
    DEFAULT_SILENCE_TIMEOUT_SECONDS,
    DEFAULT_STT_BACKEND,
    DEFAULT_SUMMARIZATION_PROVIDER,
    DEFAULT_TTS_ASK_HOTKEY,
    DEFAULT_TTS_CACHE_ENABLED,
    DEFAULT_TTS_CACHE_MAX_AGE_DAYS,
    DEFAULT_TTS_CACHE_MAX_ENTRIES,
    DEFAULT_TTS_CACHE_MAX_SIZE_MB,
    DEFAULT_TTS_EXPORT_ENABLED,
    DEFAULT_TTS_EXPORT_PATH,
    DEFAULT_TTS_HOTKEY,
    DEFAULT_TTS_MODEL_ID,
    DEFAULT_TTS_DYNAMIC_EMOTIONS,
    DEFAULT_TTS_NOISE_SCALE,
    DEFAULT_TTS_NOISE_W,
    DEFAULT_TTS_OUTPUT_FORMAT,
    DEFAULT_TTS_PIPER_SPEAKER_ID,
    DEFAULT_TTS_PREPROCESS_WITH_LLM,
    DEFAULT_TTS_PROVIDER,
    DEFAULT_TTS_SENTENCE_PAUSE_MS,
    DEFAULT_TTS_VOICE_ID,
    DEFAULT_OPENAI_TTS_FORMAT,
    DEFAULT_OPENAI_TTS_MODEL,
    DEFAULT_OPENAI_TTS_VOICE,
    OPENAI_TTS_MODELS,
    OPENAI_TTS_VOICE_PRESETS,
    DEFAULT_WAKE_PHRASE,
    DEFAULT_WAKE_PHRASE_MATCH_MODE,
    HANDSFREE_PIPELINES,
    KEYRING_ELEVENLABS_KEY,
    KEYRING_OPENAI_KEY,
    KEYRING_OPENROUTER_KEY,
    LOCAL_MODEL_SIZES,
    LOCAL_STT_DEFAULT_COMPUTE_TYPE,
    LOCAL_STT_DEFAULT_DEVICE,
    LOCAL_STT_DEFAULT_MODEL_SIZE,
    LOCAL_STT_DEFAULT_VAD_FILTER,
    LOCAL_STT_FROZEN_VAD_FILTER,
    LOCAL_STT_VALID_COMPUTE_TYPES,
    LOCAL_STT_VALID_DEVICES,
    LOG_FILENAME,
    OLLAMA_DEFAULT_BASE_URL,
    OLLAMA_DEFAULT_MODEL,
    OPENAI_DEFAULT_BASE_URL,
    OPENROUTER_DEFAULT_BASE_URL,
    OPENROUTER_DEFAULT_MODEL,
    PASTE_SHORTCUT_OPTIONS,
    PIPER_VOICE_MODELS,
    STT_BACKENDS,
    SUMMARIZE_MODEL,
    SUMMARIZE_SYSTEM_PROMPT,
    TTS_PROVIDERS,
)

logger = logging.getLogger(__name__)

# Template content for config.toml when it does not exist (v0.3)
CONFIG_TEMPLATE = """\
# VoicePaste Configuration
# See README.md for full documentation of all options.

# NOTE: API keys are stored securely in the OS credential store.
# Use the Settings dialog (General tab > API Keys) to manage keys.
# If you prefer to store keys in this file, add them below and they will
# be migrated to Credential Manager on next startup.

[api]
# Legacy API key location (migrated to Credential Manager automatically)
# openai_api_key = ""
# HTTP API: allow external programs to control Voice Paste via localhost.
api_enabled = false
api_port = 18923

[hotkey]
# Global hotkey to start/stop recording (default: "ctrl+alt+r")
combination = "ctrl+alt+r"
# Voice Prompt hotkey: record speech, send as prompt to LLM, paste answer (default: "ctrl+alt+a")
prompt_combination = "ctrl+alt+a"

# Terminal Mode toggle: switch paste between Ctrl+V (GUI) and Ctrl+Shift+V (terminal)
# Useful on Wayland where auto-detection of the focused window is unreliable.
# terminal_mode_combination = "ctrl+alt+m"

[transcription]
# Backend: "cloud" (OpenAI Whisper API) or "local" (faster-whisper, offline)
# Cloud requires an OpenAI API key. Local requires a downloaded Whisper model.
backend = "cloud"
# Transcription language: "auto" (let Whisper detect) or a language code
# e.g. "de" (German), "en" (English), "fr" (French), "es" (Spanish)
language = "de"
# Audio input device index (-1 = system default microphone)
# Use Settings dialog to select from available devices.
audio_device_index = -1
# Local STT model size (only used when backend = "local")
# Options: tiny (~75MB, fast), base (~145MB, good quality),
#          small (~480MB, better), medium (~1.5GB), large-v3 (~3GB)
model_size = "base"
# Compute device: "cpu" (default, works everywhere) or "cuda" (NVIDIA GPU)
device = "cpu"
# Quantization: "int8" (fastest, CPU), "float16" (GPU), "float32" (highest quality)
compute_type = "int8"
# Voice Activity Detection (Silero VAD): skip silence before Whisper inference.
# Improves accuracy on long recordings with pauses. Requires onnxruntime.
# Set to false if transcription crashes in the .exe build (onnxruntime issue).
# Default: true for script, false for frozen .exe (auto-detected at startup).
vad_filter = true

[summarization]
# Enable text cleanup and summarization (default: true)
enabled = true
# Provider: "openai", "openrouter", or "ollama" (default: "openai")
# Ollama runs locally (http://localhost:11434) and requires no API key.
provider = "openai"
# Model name (default: "gpt-4o-mini")
model = "gpt-4o-mini"
# Custom base URL (leave empty to use provider default)
# For OpenRouter: "https://openrouter.ai/api/v1"
base_url = ""
# Custom system prompt for summarization (leave empty to use default)
# This prompt instructs the LLM how to clean up the transcription.
custom_prompt = ""

[paste]
# Confirm before pasting: show a preview and wait for Enter (default: false)
require_confirmation = false
# Delay in seconds before auto-pasting (0 = immediate, only when confirmation is off)
delay_seconds = 0.0
# Timeout in seconds for the confirmation prompt (default: 30)
confirmation_timeout_seconds = 30.0
# Automatically press Enter after pasting (e.g. to run a command in terminal)
auto_enter = false
# Paste shortcut: "auto" (detect terminal vs normal app), "ctrl+v", or "ctrl+shift+v"
# "auto" uses GNOME Shell D-Bus on Wayland, xdotool/xprop on X11.
# Override to "ctrl+shift+v" if auto-detection fails (e.g. non-GNOME Wayland).
paste_shortcut = "auto"

[tts_cache]
# Cache TTS audio locally to avoid re-synthesis of the same text.
# Saves API credits (ElevenLabs) and reduces latency on repeated playback.
enabled = true
# Maximum total cache size in MB. Oldest entries are evicted when exceeded.
max_size_mb = 200
# Maximum age of unused cache entries in days. Set to 0 to disable age limit.
max_age_days = 30
# Maximum number of cached entries. Set to 0 for unlimited (bounded by size).
max_entries = 500

[tts_export]
# TTS Audio Export: save generated audio to a user-chosen folder with
# human-readable filenames. Useful for creating audio collections,
# training material, or any batch TTS output you want to keep.
enabled = false
# Default export directory (empty = ask on first export).
export_path = ""

[feedback]
# Play audio cues on recording start/stop (default: true)
audio_cues = true

[logging]
# Log level: DEBUG, INFO, WARNING, ERROR
level = "INFO"
"""


def _get_app_directory() -> Path:
    """Return the directory where the application executable or script lives.

    When running as a PyInstaller bundle, this returns the directory
    containing the .exe. When running as a script, returns the directory
    containing main.py.

    Returns:
        Path to the application directory.
    """
    if getattr(sys, "frozen", False):
        # Running as PyInstaller bundle
        return Path(sys.executable).parent
    else:
        # Running as script -- use the src directory's parent
        return Path(__file__).resolve().parent.parent


@dataclass
class AppConfig:
    """Application configuration loaded from config.toml and keyring.

    v0.3: Mutable (no longer frozen) to support hot-reload from Settings dialog.

    Attributes:
        openai_api_key: OpenAI API key for Whisper and GPT APIs.
        openrouter_api_key: OpenRouter API key (only for OpenRouter provider).
        hotkey: Global hotkey combination string for the keyboard library.
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR).
        summarization_enabled: Whether to run LLM summarization.
        summarization_provider: "openai" or "openrouter".
        summarization_model: Model name for summarization.
        summarization_base_url: Custom base URL (empty = use provider default).
        summarization_custom_prompt: Custom system prompt (empty = use default).
        audio_cues_enabled: Whether to play audio feedback cues.
        app_directory: Resolved path to the application directory.
    """

    openai_api_key: str = ""
    openrouter_api_key: str = ""
    hotkey: str = DEFAULT_HOTKEY
    prompt_hotkey: str = DEFAULT_PROMPT_HOTKEY
    log_level: str = "INFO"
    summarization_enabled: bool = True
    summarization_provider: str = DEFAULT_SUMMARIZATION_PROVIDER
    summarization_model: str = SUMMARIZE_MODEL
    summarization_base_url: str = ""
    summarization_custom_prompt: str = ""
    audio_cues_enabled: bool = True
    app_directory: Path = field(default_factory=_get_app_directory)

    # --- v0.4: Local STT fields ---
    stt_backend: str = DEFAULT_STT_BACKEND
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE
    audio_device_index: Optional[int] = DEFAULT_AUDIO_DEVICE_INDEX
    local_model_size: str = LOCAL_STT_DEFAULT_MODEL_SIZE
    local_device: str = LOCAL_STT_DEFAULT_DEVICE
    local_compute_type: str = LOCAL_STT_DEFAULT_COMPUTE_TYPE
    vad_filter: bool = LOCAL_STT_DEFAULT_VAD_FILTER
    vocabulary_hints: str = ""

    # --- v0.6: TTS (Text-to-Speech) fields ---
    tts_enabled: bool = False
    tts_provider: str = DEFAULT_TTS_PROVIDER
    elevenlabs_api_key: str = ""
    tts_voice_id: str = DEFAULT_TTS_VOICE_ID
    tts_model_id: str = DEFAULT_TTS_MODEL_ID
    tts_output_format: str = DEFAULT_TTS_OUTPUT_FORMAT
    tts_hotkey: str = DEFAULT_TTS_HOTKEY
    tts_ask_hotkey: str = DEFAULT_TTS_ASK_HOTKEY

    # --- OpenAI TTS fields ---
    tts_openai_voice: str = DEFAULT_OPENAI_TTS_VOICE
    tts_openai_model: str = DEFAULT_OPENAI_TTS_MODEL
    tts_openai_format: str = DEFAULT_OPENAI_TTS_FORMAT
    tts_openai_instructions: str = ""

    # --- v0.7: Local TTS (Piper) fields ---
    tts_local_voice: str = DEFAULT_PIPER_VOICE
    tts_speed: float = 1.0  # Piper length_scale: <1.0 = faster, >1.0 = slower
    tts_sentence_pause_ms: int = DEFAULT_TTS_SENTENCE_PAUSE_MS
    tts_noise_scale: float = DEFAULT_TTS_NOISE_SCALE
    tts_noise_w: float = DEFAULT_TTS_NOISE_W
    tts_piper_speaker_id: int = DEFAULT_TTS_PIPER_SPEAKER_ID
    tts_dynamic_emotions: bool = DEFAULT_TTS_DYNAMIC_EMOTIONS

    # --- Audio Effects (Piper post-processing) ---
    audio_fx_enabled: bool = DEFAULT_AUDIO_FX_ENABLED
    audio_fx_pitch_semitones: float = DEFAULT_AUDIO_FX_PITCH_SEMITONES
    audio_fx_formant_shift: float = DEFAULT_AUDIO_FX_FORMANT_SHIFT
    audio_fx_bass_db: float = DEFAULT_AUDIO_FX_BASS_DB
    audio_fx_treble_db: float = DEFAULT_AUDIO_FX_TREBLE_DB
    audio_fx_reverb_mix: float = DEFAULT_AUDIO_FX_REVERB_MIX

    # --- TTS LLM Preprocessing (Ctrl+Alt+T readback) ---
    tts_preprocess_with_llm: bool = DEFAULT_TTS_PREPROCESS_WITH_LLM
    tts_preprocess_prompt: str = ""  # empty = use default preset

    # --- TTS Emotion/Dialog prompt (custom override) ---
    tts_emotion_prompt: str = ""  # empty = auto-detect DE/EN by voice

    # --- v0.9: HTTP API ---
    api_enabled: bool = DEFAULT_API_ENABLED
    api_port: int = DEFAULT_API_PORT

    # --- v0.9: Confirm-before-paste ---
    paste_require_confirmation: bool = DEFAULT_PASTE_CONFIRM
    paste_delay_seconds: float = DEFAULT_PASTE_DELAY_SECONDS
    paste_confirmation_timeout: float = DEFAULT_PASTE_CONFIRMATION_TIMEOUT
    paste_auto_enter: bool = DEFAULT_PASTE_AUTO_ENTER
    paste_shortcut: str = DEFAULT_PASTE_SHORTCUT

    # --- v0.9: Hands-Free Mode ---
    handsfree_enabled: bool = DEFAULT_HANDSFREE_ENABLED
    wake_phrase: str = DEFAULT_WAKE_PHRASE
    wake_phrase_match_mode: str = DEFAULT_WAKE_PHRASE_MATCH_MODE
    silence_timeout_seconds: float = DEFAULT_SILENCE_TIMEOUT_SECONDS
    handsfree_max_recording_seconds: int = DEFAULT_HANDSFREE_MAX_RECORDING_SECONDS
    handsfree_pipeline: str = DEFAULT_HANDSFREE_PIPELINE
    handsfree_cooldown_seconds: float = DEFAULT_HANDSFREE_COOLDOWN_SECONDS
    handsfree_silence_threshold_rms: float = DEFAULT_HANDSFREE_SILENCE_THRESHOLD_RMS
    handsfree_wake_model_size: str = DEFAULT_HANDSFREE_WAKE_MODEL_SIZE

    # --- v1.0: TTS Audio Cache ---
    tts_cache_enabled: bool = DEFAULT_TTS_CACHE_ENABLED
    tts_cache_max_size_mb: int = DEFAULT_TTS_CACHE_MAX_SIZE_MB
    tts_cache_max_age_days: int = DEFAULT_TTS_CACHE_MAX_AGE_DAYS
    tts_cache_max_entries: int = DEFAULT_TTS_CACHE_MAX_ENTRIES

    # --- v1.0: TTS Audio Export ---
    tts_export_enabled: bool = DEFAULT_TTS_EXPORT_ENABLED
    tts_export_path: str = DEFAULT_TTS_EXPORT_PATH

    # --- v1.3: Terminal Mode toggle hotkey ---
    terminal_mode_hotkey: str = DEFAULT_TERMINAL_MODE_HOTKEY

    # --- v1.2: Claude Code CLI integration ---
    claude_code_enabled: bool = DEFAULT_CLAUDE_CODE_ENABLED
    claude_code_hotkey: str = DEFAULT_CLAUDE_CODE_HOTKEY
    claude_code_working_dir: str = DEFAULT_CLAUDE_CODE_WORKING_DIR
    claude_code_system_prompt: str = DEFAULT_CLAUDE_CODE_SYSTEM_PROMPT
    claude_code_timeout: int = DEFAULT_CLAUDE_CODE_TIMEOUT
    claude_code_response_mode: str = DEFAULT_CLAUDE_CODE_RESPONSE_MODE
    claude_code_skip_permissions: bool = DEFAULT_CLAUDE_CODE_SKIP_PERMISSIONS
    claude_code_continue_conversation: bool = DEFAULT_CLAUDE_CODE_CONTINUE_CONVERSATION

    @property
    def config_path(self) -> Path:
        """Path to the config.toml file."""
        return self.app_directory / "config.toml"

    @property
    def log_path(self) -> Path:
        """Path to the log file."""
        return self.app_directory / LOG_FILENAME

    @property
    def active_summarization_api_key(self) -> str:
        """Return the API key for the configured summarization provider."""
        if self.summarization_provider == "openrouter":
            return self.openrouter_api_key
        if self.summarization_provider == "ollama":
            # Ollama runs locally and needs no real API key.
            # The OpenAI SDK requires a non-empty string, so use a dummy.
            return "ollama"
        return self.openai_api_key

    @property
    def active_summarization_base_url(self) -> Optional[str]:
        """Return the base URL for the summarization provider.

        Returns None to use the openai library default (api.openai.com).
        """
        if self.summarization_base_url:
            return self.summarization_base_url
        if self.summarization_provider == "openrouter":
            return OPENROUTER_DEFAULT_BASE_URL
        if self.summarization_provider == "ollama":
            return OLLAMA_DEFAULT_BASE_URL
        return None  # Use openai library default

    @property
    def active_system_prompt(self) -> str:
        """Return the system prompt for summarization.

        Returns the custom prompt if set, otherwise the default.
        """
        if self.summarization_custom_prompt and self.summarization_custom_prompt.strip():
            return self.summarization_custom_prompt.strip()
        return SUMMARIZE_SYSTEM_PROMPT

    def masked_api_key(self, key: Optional[str] = None) -> str:
        """Return an API key with all but the last 4 characters masked.

        REQ-S01: Never log the full API key.

        Args:
            key: The key to mask. If None, uses openai_api_key.

        Returns:
            Masked API key string, or '<empty>' if not set.
        """
        k = key if key is not None else self.openai_api_key
        if not k:
            return "<empty>"
        if len(k) <= 4:
            return "****"
        return "*" * (len(k) - 4) + k[-4:]

    def save_to_toml(self) -> bool:
        """Write non-secret configuration fields back to config.toml.

        Secrets (API keys) are NOT written -- they belong in keyring.
        Only writes fields that have a corresponding TOML section.

        Returns:
            True if file was written successfully, False otherwise.
        """
        # Escape TOML string values (SEC-015: escape newlines to prevent injection)
        def esc(s: str) -> str:
            return (
                s.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            )

        content = f"""\
# Voice Paste Configuration
# Managed by Settings dialog. Manual edits are preserved on next save.

[api]
# API keys are stored in the OS credential store.
# Use the Settings dialog (General tab > API Keys) to manage keys.
# HTTP API: allow external programs to control Voice Paste via localhost.
api_enabled = {str(self.api_enabled).lower()}
api_port = {self.api_port}

[hotkey]
combination = "{esc(self.hotkey)}"
# Voice Prompt hotkey: record speech, send as prompt to LLM, paste answer
prompt_combination = "{esc(self.prompt_hotkey)}"
# TTS hotkeys (v0.6): read clipboard aloud / ask AI + TTS
tts_combination = "{esc(self.tts_hotkey)}"
tts_ask_combination = "{esc(self.tts_ask_hotkey)}"
# Terminal Mode toggle: switch paste shortcut between Ctrl+V and Ctrl+Shift+V
terminal_mode_combination = "{esc(self.terminal_mode_hotkey)}"

[transcription]
# Backend: "cloud" (OpenAI Whisper API) or "local" (faster-whisper, offline)
backend = "{esc(self.stt_backend)}"
# Transcription language: "auto" or a language code (de, en, fr, es, ...)
language = "{esc(self.transcription_language)}"
# Audio input device index (empty or -1 = system default microphone)
audio_device_index = {self.audio_device_index if self.audio_device_index is not None else -1}
# Local model size: tiny, base, small, medium, large-v2, large-v3
model_size = "{esc(self.local_model_size)}"
# Device: cpu, cuda, auto
device = "{esc(self.local_device)}"
# Compute type: int8, float16, float32, auto
compute_type = "{esc(self.local_compute_type)}"
# Silero VAD: filter silence before Whisper (disable if .exe crashes during transcription)
vad_filter = {str(self.vad_filter).lower()}
# Vocabulary hints: domain terms for better transcription accuracy.
# Whisper uses this as a prompt/context. Separate terms with commas or spaces.
# Example: "Kubernetes, pytest, Anamnese, NGINX"
vocabulary_hints = "{esc(self.vocabulary_hints)}"

[summarization]
enabled = {str(self.summarization_enabled).lower()}
provider = "{esc(self.summarization_provider)}"
model = "{esc(self.summarization_model)}"
base_url = "{esc(self.summarization_base_url)}"
custom_prompt = "{esc(self.summarization_custom_prompt)}"

[tts]
# Text-to-Speech: "elevenlabs" (cloud), "openai" (cloud), or "piper" (local, offline)
enabled = {str(self.tts_enabled).lower()}
provider = "{esc(self.tts_provider)}"
# --- Cloud (ElevenLabs) fields ---
voice_id = "{esc(self.tts_voice_id)}"
model_id = "{esc(self.tts_model_id)}"
output_format = "{esc(self.tts_output_format)}"
# --- Cloud (OpenAI) fields ---
openai_voice = "{esc(self.tts_openai_voice)}"
openai_model = "{esc(self.tts_openai_model)}"
openai_format = "{esc(self.tts_openai_format)}"
openai_instructions = "{esc(self.tts_openai_instructions)}"
# --- Local (Piper) fields (v0.7) ---
# Voice model name. Available: de_DE-thorsten-medium, de_DE-thorsten-high,
# en_US-lessac-medium, en_US-amy-medium. Download via Settings dialog.
local_voice = "{esc(self.tts_local_voice)}"
# Speech speed: 0.5 = double speed, 1.0 = normal, 2.0 = half speed
speed = {self.tts_speed}
# Silence gap between sentences in milliseconds (0 = disabled).
# Improves readability for longer text. Default: 350.
sentence_pause_ms = {self.tts_sentence_pause_ms}
# VITS expressiveness parameters (advanced, power-user only).
# noise_scale controls phoneme noise (0.0-1.0, higher = more expressive).
# noise_w controls duration variation (0.0-1.0, higher = more varied rhythm).
# noise_scale = {self.tts_noise_scale}
# noise_w = {self.tts_noise_w}
# Speaker ID for multi-speaker models (e.g., thorsten_emotional: 0=amused,
# 1=angry, 2=disgusted, 3=drunk, 4=neutral, 5=sleepy, 6=surprised, 7=whisper)
speaker_id = {self.tts_piper_speaker_id}
# Dynamic emotions: let LLM tag each sentence with an emotion before synthesis.
# Requires tts_preprocess_with_llm = true and a multi-speaker model.
dynamic_emotions = {str(self.tts_dynamic_emotions).lower()}
# --- Audio Effects (Piper post-processing, pure numpy) ---
# These effects apply ONLY to the Piper local TTS backend, not cloud providers.
# Master toggle: set to false to disable all audio effects regardless of values below.
audio_fx_enabled = {str(self.audio_fx_enabled).lower()}
# All values at their defaults = no processing (zero overhead).
# Pitch shift in semitones (-6.0 to +6.0, 0 = no shift)
audio_fx_pitch_semitones = {self.audio_fx_pitch_semitones}
# Formant/voice depth shift (0.7 to 1.4, <1.0 = deeper, >1.0 = higher, 1.0 = off)
audio_fx_formant_shift = {self.audio_fx_formant_shift}
# Bass shelf EQ at 200 Hz in dB (-12 to +12, 0 = flat)
audio_fx_bass_db = {self.audio_fx_bass_db}
# Treble shelf EQ at 3000 Hz in dB (-12 to +12, 0 = flat)
audio_fx_treble_db = {self.audio_fx_treble_db}
# Reverb wet/dry mix (0.0 to 0.5, 0.0 = off)
audio_fx_reverb_mix = {self.audio_fx_reverb_mix}

# --- TTS LLM Preprocessing (for Ctrl+Alt+T clipboard readback) ---
# Preprocess clipboard text with LLM before TTS synthesis.
# Rewrites messy text (bullets, markdown, URLs) into natural spoken prose.
tts_preprocess_with_llm = {str(self.tts_preprocess_with_llm).lower()}
# Custom preprocessing prompt (empty = use default "Clean & Natural" preset).
tts_preprocess_prompt = "{esc(self.tts_preprocess_prompt)}"
# Custom emotion/dialog tagging prompt (empty = auto-detect DE/EN by voice model).
tts_emotion_prompt = "{esc(self.tts_emotion_prompt)}"

[paste]
# Confirm before pasting: show a preview notification and wait for Enter.
require_confirmation = {str(self.paste_require_confirmation).lower()}
# Delay in seconds before auto-pasting (0 = immediate). Only used when
# require_confirmation is false; otherwise the user presses Enter.
delay_seconds = {self.paste_delay_seconds}
# Timeout in seconds for the confirmation prompt (auto-cancels after this).
confirmation_timeout_seconds = {self.paste_confirmation_timeout}
# Automatically press Enter after pasting (e.g. to execute a command in a terminal).
auto_enter = {str(self.paste_auto_enter).lower()}
# Paste shortcut: "auto" (detect terminal), "ctrl+v", or "ctrl+shift+v".
# On Wayland, auto-detection uses GNOME Shell D-Bus. Override if detection fails.
paste_shortcut = "{esc(self.paste_shortcut)}"

[handsfree]
# Hands-Free Mode: continuous wake word detection via STT keyword spotting.
# PRIVACY: Microphone is always active while enabled. Detection is 100% local.
enabled = {str(self.handsfree_enabled).lower()}
# Wake phrase to listen for (any text, detected via local Whisper tiny model)
wake_phrase = "{esc(self.wake_phrase)}"
# Match mode: "contains" (forgiving), "startswith" (strict), "fuzzy" (token overlap)
match_mode = "{esc(self.wake_phrase_match_mode)}"
# Seconds of silence before auto-stopping recording (1.0 - 10.0)
silence_timeout = {self.silence_timeout_seconds}
# Maximum recording duration in seconds (hands-free mode)
max_recording_seconds = {self.handsfree_max_recording_seconds}
# Pipeline: "ask_tts" (ask AI + speak), "summary" (transcribe + paste), "prompt" (ask AI + paste), "claude_code" (Claude Code CLI)
pipeline = "{esc(self.handsfree_pipeline)}"
# Cooldown in seconds after wake word detection (prevents re-trigger)
cooldown_seconds = {self.handsfree_cooldown_seconds}
# Silence detection threshold (RMS energy, int16 scale).
# 0 = adaptive (auto-calibrate from ambient noise at recording start).
# Set to a fixed value (e.g. 500-1000) if adaptive detection misbehaves.
silence_threshold_rms = {self.handsfree_silence_threshold_rms}
# Wake word STT model size: "tiny" (~75MB, fast), "base" (~145MB, better noise handling), "small" (~480MB)
wake_model_size = "{esc(self.handsfree_wake_model_size)}"

[tts_cache]
# Cache TTS audio locally to avoid re-synthesis of the same text.
enabled = {str(self.tts_cache_enabled).lower()}
max_size_mb = {self.tts_cache_max_size_mb}
max_age_days = {self.tts_cache_max_age_days}
max_entries = {self.tts_cache_max_entries}

[tts_export]
# Save generated TTS audio to a user-chosen folder with readable filenames.
enabled = {str(self.tts_export_enabled).lower()}
export_path = "{esc(self.tts_export_path)}"

[claude_code]
# Claude Code CLI integration (requires `claude` in PATH)
enabled = {str(self.claude_code_enabled).lower()}
hotkey = "{esc(self.claude_code_hotkey)}"
working_dir = "{esc(self.claude_code_working_dir)}"
system_prompt = "{esc(self.claude_code_system_prompt)}"
response_mode = "{esc(self.claude_code_response_mode)}"
timeout = {self.claude_code_timeout}
skip_permissions = {str(self.claude_code_skip_permissions).lower()}
continue_conversation = {str(self.claude_code_continue_conversation).lower()}

[feedback]
audio_cues = {str(self.audio_cues_enabled).lower()}

[logging]
level = "{esc(self.log_level)}"
"""
        try:
            config_path = self.config_path
            # Write to temp file first, then replace atomically
            tmp_path = config_path.with_suffix(".toml.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(config_path)
            # SEC-069: Restrict config file permissions on Linux (0600)
            if sys.platform != "win32":
                import stat
                os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)
            logger.info("Configuration saved to %s", config_path)
            return True
        except OSError as e:
            logger.error("Failed to save configuration: %s", e)
            return False


def _migrate_api_key_to_keyring(config_path: Path, api_key: str) -> bool:
    """Migrate API key from config.toml to Windows Credential Manager.

    SEC-013: After successful migration, the config.toml is regenerated
    without any key material (via save_to_toml or template rewrite).

    Args:
        config_path: Path to the config.toml file.
        api_key: The API key to migrate.

    Returns:
        True if migration succeeded, False otherwise.
    """
    try:
        import keyring_store

        if not keyring_store.is_available():
            return False

        success = keyring_store.set_credential(KEYRING_OPENAI_KEY, api_key)
        if not success:
            return False

        # SEC-013: Rewrite config.toml without any API key material.
        # Instead of commenting out the old line (which leaves the key on disk),
        # we rewrite the entire file from the template without key values.
        try:
            config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        except OSError:
            pass  # Non-critical: key is in keyring regardless

        logger.info("API key migrated from config.toml to Credential Manager.")
        return True

    except Exception as e:
        logger.warning("API key migration failed: %s", e)
        return False


def load_config() -> Optional[AppConfig]:
    """Load configuration from config.toml and Windows Credential Manager.

    v0.3: API key is loaded from keyring first, with config.toml fallback.
    A missing API key is NO LONGER fatal -- the user can enter it via Settings.

    If config.toml does not exist, creates a template.
    If the TOML is malformed, returns a default AppConfig (allows Settings dialog).

    Returns:
        AppConfig instance (possibly with empty API key), or None on
        unrecoverable file system errors.
    """
    app_dir = _get_app_directory()
    config_path = app_dir / "config.toml"

    if not config_path.exists():
        logger.warning(
            "config.toml not found at %s. Creating template.", config_path
        )
        try:
            config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
            # SEC-069: Restrict config file permissions on Linux (0600)
            if sys.platform != "win32":
                import stat
                os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)
            logger.info("Created config.toml template at %s.", config_path)
        except OSError as e:
            logger.error("Failed to create config.toml template: %s", e)

    # Parse TOML
    data = {}
    if config_path.exists():
        try:
            raw = config_path.read_bytes()
            data = tomllib.loads(raw.decode("utf-8"))
        except tomllib.TOMLDecodeError as e:
            logger.error("config.toml has invalid syntax: %s", e)
            # Continue with empty data -- user can fix via Settings dialog
        except OSError as e:
            logger.error("Failed to read config.toml: %s", e)

    # Extract values with defaults
    api_section = data.get("api", {})
    hotkey_section = data.get("hotkey", {})
    logging_section = data.get("logging", {})
    summarization_section = data.get("summarization", {})
    feedback_section = data.get("feedback", {})
    transcription_section = data.get("transcription", {})
    tts_section = data.get("tts", {})
    paste_section = data.get("paste", {})

    # v0.9: HTTP API settings
    api_enabled = bool(api_section.get("api_enabled", DEFAULT_API_ENABLED))
    api_port = int(api_section.get("api_port", DEFAULT_API_PORT))
    if not (1024 <= api_port <= 65535):
        logger.warning("Invalid API port %d, using default %d.", api_port, DEFAULT_API_PORT)
        api_port = DEFAULT_API_PORT

    toml_api_key = api_section.get("openai_api_key", "").strip()
    hotkey = hotkey_section.get("combination", DEFAULT_HOTKEY)
    prompt_hotkey = hotkey_section.get("prompt_combination", DEFAULT_PROMPT_HOTKEY)
    log_level = logging_section.get("level", "INFO")
    summarization_enabled = summarization_section.get("enabled", True)
    summarization_provider = summarization_section.get("provider", DEFAULT_SUMMARIZATION_PROVIDER)
    summarization_model = summarization_section.get("model", SUMMARIZE_MODEL)
    summarization_base_url = summarization_section.get("base_url", "")
    summarization_custom_prompt = summarization_section.get("custom_prompt", "")
    audio_cues_enabled = feedback_section.get("audio_cues", True)

    # Validate provider
    if summarization_provider not in ("openai", "openrouter", "ollama"):
        logger.warning(
            "Invalid summarization provider '%s'. Falling back to 'openai'.",
            summarization_provider,
        )
        summarization_provider = "openai"

    # --- v0.4: Validate transcription/local STT fields ---
    stt_backend = transcription_section.get("backend", DEFAULT_STT_BACKEND)
    if stt_backend not in STT_BACKENDS:
        logger.warning(
            "Invalid stt_backend '%s'. Falling back to '%s'.",
            stt_backend,
            DEFAULT_STT_BACKEND,
        )
        stt_backend = DEFAULT_STT_BACKEND

    transcription_language = transcription_section.get(
        "language", DEFAULT_TRANSCRIPTION_LANGUAGE
    )
    # Accept any non-empty string (Whisper supports many language codes beyond
    # our SUPPORTED_LANGUAGES display list), but warn if not recognized.
    if not transcription_language or not isinstance(transcription_language, str):
        logger.warning(
            "Invalid transcription language '%s'. Falling back to '%s'.",
            transcription_language,
            DEFAULT_TRANSCRIPTION_LANGUAGE,
        )
        transcription_language = DEFAULT_TRANSCRIPTION_LANGUAGE

    # Audio input device index
    _raw_device_idx = transcription_section.get("audio_device_index", -1)
    try:
        audio_device_index: Optional[int] = int(_raw_device_idx)
        if audio_device_index is not None and audio_device_index < 0:
            audio_device_index = None  # -1 or negative = system default
    except (ValueError, TypeError):
        audio_device_index = None

    local_model_size = transcription_section.get("model_size", LOCAL_STT_DEFAULT_MODEL_SIZE)
    if local_model_size not in LOCAL_MODEL_SIZES:
        logger.warning(
            "Invalid local_model_size '%s'. Falling back to '%s'.",
            local_model_size,
            LOCAL_STT_DEFAULT_MODEL_SIZE,
        )
        local_model_size = LOCAL_STT_DEFAULT_MODEL_SIZE

    local_device = transcription_section.get("device", LOCAL_STT_DEFAULT_DEVICE)
    if local_device not in LOCAL_STT_VALID_DEVICES:
        logger.warning(
            "Invalid local_device '%s'. Falling back to '%s'.",
            local_device,
            LOCAL_STT_DEFAULT_DEVICE,
        )
        local_device = LOCAL_STT_DEFAULT_DEVICE

    local_compute_type = transcription_section.get("compute_type", LOCAL_STT_DEFAULT_COMPUTE_TYPE)
    if local_compute_type not in LOCAL_STT_VALID_COMPUTE_TYPES:
        logger.warning(
            "Invalid local_compute_type '%s'. Falling back to '%s'.",
            local_compute_type,
            LOCAL_STT_DEFAULT_COMPUTE_TYPE,
        )
        local_compute_type = LOCAL_STT_DEFAULT_COMPUTE_TYPE

    # VAD filter: read from TOML, but override to False in frozen exe unless
    # explicitly set to True by the user.
    _vad_raw = transcription_section.get("vad_filter", None)
    if _vad_raw is not None:
        # User explicitly set a value in config.toml -- respect it.
        vad_filter = bool(_vad_raw)
        logger.info(
            "VAD filter explicitly configured: %s",
            "enabled" if vad_filter else "disabled",
        )
    else:
        # No explicit setting: use safe default based on execution context.
        if getattr(sys, "frozen", False):
            vad_filter = LOCAL_STT_FROZEN_VAD_FILTER
            logger.info(
                "VAD filter defaulting to %s (frozen executable). "
                "Set [transcription] vad_filter = true in config.toml "
                "to override.",
                "enabled" if vad_filter else "disabled",
            )
        else:
            vad_filter = LOCAL_STT_DEFAULT_VAD_FILTER
            logger.debug(
                "VAD filter defaulting to %s (script mode).",
                "enabled" if vad_filter else "disabled",
            )

    # Vocabulary hints for better transcription of domain-specific terms
    vocabulary_hints = str(transcription_section.get("vocabulary_hints", "")).strip()

    # Validate hotkey strings using the platform-aware parser from hotkey module
    from hotkey import _parse_hotkey

    if hotkey and hotkey.strip():
        hotkey = hotkey.strip()
        try:
            _parse_hotkey(hotkey)
            logger.info("Hotkey configured: '%s'", hotkey)
        except Exception as e:
            logger.warning(
                "Invalid hotkey '%s' in config.toml: %s. "
                "Falling back to default '%s'.",
                hotkey,
                e,
                DEFAULT_HOTKEY,
            )
            hotkey = DEFAULT_HOTKEY
    else:
        hotkey = DEFAULT_HOTKEY

    if prompt_hotkey and prompt_hotkey.strip():
        prompt_hotkey = prompt_hotkey.strip()
        try:
            _parse_hotkey(prompt_hotkey)
            logger.info("Prompt hotkey configured: '%s'", prompt_hotkey)
        except Exception as e:
            logger.warning(
                "Invalid prompt hotkey '%s' in config.toml: %s. "
                "Falling back to default '%s'.",
                prompt_hotkey,
                e,
                DEFAULT_PROMPT_HOTKEY,
            )
            prompt_hotkey = DEFAULT_PROMPT_HOTKEY
    else:
        prompt_hotkey = DEFAULT_PROMPT_HOTKEY

    # --- v0.6: TTS hotkeys ---
    tts_hotkey = hotkey_section.get("tts_combination", DEFAULT_TTS_HOTKEY)
    if tts_hotkey and tts_hotkey.strip():
        tts_hotkey = tts_hotkey.strip()
        try:
            _parse_hotkey(tts_hotkey)
            logger.info("TTS hotkey configured: '%s'", tts_hotkey)
        except Exception as e:
            logger.warning(
                "Invalid TTS hotkey '%s': %s. Falling back to '%s'.",
                tts_hotkey, e, DEFAULT_TTS_HOTKEY,
            )
            tts_hotkey = DEFAULT_TTS_HOTKEY
    else:
        tts_hotkey = DEFAULT_TTS_HOTKEY

    tts_ask_hotkey = hotkey_section.get("tts_ask_combination", DEFAULT_TTS_ASK_HOTKEY)
    if tts_ask_hotkey and tts_ask_hotkey.strip():
        tts_ask_hotkey = tts_ask_hotkey.strip()
        try:
            _parse_hotkey(tts_ask_hotkey)
            logger.info("TTS Ask hotkey configured: '%s'", tts_ask_hotkey)
        except Exception as e:
            logger.warning(
                "Invalid TTS Ask hotkey '%s': %s. Falling back to '%s'.",
                tts_ask_hotkey, e, DEFAULT_TTS_ASK_HOTKEY,
            )
            tts_ask_hotkey = DEFAULT_TTS_ASK_HOTKEY
    else:
        tts_ask_hotkey = DEFAULT_TTS_ASK_HOTKEY

    # --- v0.6: TTS configuration ---
    tts_enabled = tts_section.get("enabled", False)
    tts_provider = tts_section.get("provider", DEFAULT_TTS_PROVIDER)
    if tts_provider not in TTS_PROVIDERS:
        logger.warning(
            "Invalid TTS provider '%s'. Falling back to '%s'.",
            tts_provider, DEFAULT_TTS_PROVIDER,
        )
        tts_provider = DEFAULT_TTS_PROVIDER
    tts_voice_id = tts_section.get("voice_id", DEFAULT_TTS_VOICE_ID)
    tts_model_id = tts_section.get("model_id", DEFAULT_TTS_MODEL_ID)
    tts_output_format = tts_section.get("output_format", DEFAULT_TTS_OUTPUT_FORMAT)

    # OpenAI TTS fields
    tts_openai_voice = tts_section.get("openai_voice", DEFAULT_OPENAI_TTS_VOICE)
    if tts_openai_voice not in OPENAI_TTS_VOICE_PRESETS:
        logger.warning(
            "Unknown OpenAI TTS voice '%s'. Falling back to '%s'.",
            tts_openai_voice, DEFAULT_OPENAI_TTS_VOICE,
        )
        tts_openai_voice = DEFAULT_OPENAI_TTS_VOICE
    tts_openai_model = tts_section.get("openai_model", DEFAULT_OPENAI_TTS_MODEL)
    if tts_openai_model not in OPENAI_TTS_MODELS:
        logger.warning(
            "Unknown OpenAI TTS model '%s'. Falling back to '%s'.",
            tts_openai_model, DEFAULT_OPENAI_TTS_MODEL,
        )
        tts_openai_model = DEFAULT_OPENAI_TTS_MODEL
    tts_openai_format = tts_section.get("openai_format", DEFAULT_OPENAI_TTS_FORMAT)
    tts_openai_instructions = str(tts_section.get("openai_instructions", ""))

    # v0.7: Local TTS (Piper) voice name
    tts_local_voice = tts_section.get("local_voice", DEFAULT_PIPER_VOICE)
    if tts_local_voice not in PIPER_VOICE_MODELS:
        logger.warning(
            "Unknown Piper voice '%s'. Falling back to '%s'.",
            tts_local_voice,
            DEFAULT_PIPER_VOICE,
        )
        tts_local_voice = DEFAULT_PIPER_VOICE

    # v1.1: TTS speed (Piper length_scale)
    tts_speed = float(tts_section.get("speed", 1.0))
    tts_speed = max(0.25, min(tts_speed, 4.0))  # clamp to sane range

    # v1.2: Sentence pause for Piper (silence gap between sentences)
    tts_sentence_pause_ms = int(
        tts_section.get("sentence_pause_ms", DEFAULT_TTS_SENTENCE_PAUSE_MS)
    )
    tts_sentence_pause_ms = max(0, min(tts_sentence_pause_ms, 2000))

    # VITS expressiveness parameters (advanced)
    tts_noise_scale = float(tts_section.get("noise_scale", DEFAULT_TTS_NOISE_SCALE))
    tts_noise_scale = max(0.0, min(tts_noise_scale, 1.0))
    tts_noise_w = float(tts_section.get("noise_w", DEFAULT_TTS_NOISE_W))
    tts_noise_w = max(0.0, min(tts_noise_w, 1.0))

    # Piper speaker/emotion
    tts_piper_speaker_id = int(
        tts_section.get("speaker_id", DEFAULT_TTS_PIPER_SPEAKER_ID)
    )
    tts_piper_speaker_id = max(0, tts_piper_speaker_id)
    tts_dynamic_emotions = bool(
        tts_section.get("dynamic_emotions", DEFAULT_TTS_DYNAMIC_EMOTIONS)
    )

    # Audio Effects (Piper post-processing)
    audio_fx_enabled = bool(
        tts_section.get("audio_fx_enabled", DEFAULT_AUDIO_FX_ENABLED))
    audio_fx_pitch_semitones = float(
        tts_section.get("audio_fx_pitch_semitones", DEFAULT_AUDIO_FX_PITCH_SEMITONES))
    audio_fx_pitch_semitones = max(-6.0, min(audio_fx_pitch_semitones, 6.0))
    audio_fx_formant_shift = float(
        tts_section.get("audio_fx_formant_shift", DEFAULT_AUDIO_FX_FORMANT_SHIFT))
    audio_fx_formant_shift = max(0.7, min(audio_fx_formant_shift, 1.4))
    audio_fx_bass_db = float(
        tts_section.get("audio_fx_bass_db", DEFAULT_AUDIO_FX_BASS_DB))
    audio_fx_bass_db = max(-12.0, min(audio_fx_bass_db, 12.0))
    audio_fx_treble_db = float(
        tts_section.get("audio_fx_treble_db", DEFAULT_AUDIO_FX_TREBLE_DB))
    audio_fx_treble_db = max(-12.0, min(audio_fx_treble_db, 12.0))
    audio_fx_reverb_mix = float(
        tts_section.get("audio_fx_reverb_mix", DEFAULT_AUDIO_FX_REVERB_MIX))
    audio_fx_reverb_mix = max(0.0, min(audio_fx_reverb_mix, 0.5))

    # TTS LLM Preprocessing
    tts_preprocess_with_llm = bool(
        tts_section.get("tts_preprocess_with_llm", DEFAULT_TTS_PREPROCESS_WITH_LLM)
    )
    tts_preprocess_prompt = str(tts_section.get("tts_preprocess_prompt", "")).strip()
    tts_emotion_prompt = str(tts_section.get("tts_emotion_prompt", "")).strip()

    # --- v1.0: TTS Audio Cache ---
    tts_cache_section = data.get("tts_cache", {})
    tts_cache_enabled = bool(tts_cache_section.get("enabled", DEFAULT_TTS_CACHE_ENABLED))
    tts_cache_max_size_mb = int(tts_cache_section.get("max_size_mb", DEFAULT_TTS_CACHE_MAX_SIZE_MB))
    tts_cache_max_size_mb = max(10, min(tts_cache_max_size_mb, 2000))
    tts_cache_max_age_days = int(tts_cache_section.get("max_age_days", DEFAULT_TTS_CACHE_MAX_AGE_DAYS))
    tts_cache_max_age_days = max(0, min(tts_cache_max_age_days, 365))
    tts_cache_max_entries = int(tts_cache_section.get("max_entries", DEFAULT_TTS_CACHE_MAX_ENTRIES))
    tts_cache_max_entries = max(0, min(tts_cache_max_entries, 5000))

    # --- v1.0: TTS Audio Export ---
    tts_export_section = data.get("tts_export", {})
    tts_export_enabled = bool(tts_export_section.get("enabled", DEFAULT_TTS_EXPORT_ENABLED))
    tts_export_path = str(tts_export_section.get("export_path", DEFAULT_TTS_EXPORT_PATH)).strip()

    # --- v1.2: Claude Code CLI integration ---
    claude_section = data.get("claude_code", {})
    claude_code_enabled = bool(claude_section.get("enabled", DEFAULT_CLAUDE_CODE_ENABLED))
    claude_code_working_dir = str(claude_section.get(
        "working_dir", DEFAULT_CLAUDE_CODE_WORKING_DIR)).strip()
    claude_code_system_prompt = str(claude_section.get(
        "system_prompt", DEFAULT_CLAUDE_CODE_SYSTEM_PROMPT))
    claude_code_timeout = int(claude_section.get("timeout", DEFAULT_CLAUDE_CODE_TIMEOUT))
    claude_code_timeout = max(10, min(claude_code_timeout, 600))
    claude_code_response_mode = claude_section.get(
        "response_mode", DEFAULT_CLAUDE_CODE_RESPONSE_MODE)
    if claude_code_response_mode not in CLAUDE_CODE_RESPONSE_MODES:
        logger.warning(
            "Invalid claude_code_response_mode '%s'. Falling back to '%s'.",
            claude_code_response_mode, DEFAULT_CLAUDE_CODE_RESPONSE_MODE,
        )
        claude_code_response_mode = DEFAULT_CLAUDE_CODE_RESPONSE_MODE
    claude_code_skip_permissions = bool(claude_section.get(
        "skip_permissions", DEFAULT_CLAUDE_CODE_SKIP_PERMISSIONS))
    claude_code_continue_conversation = bool(claude_section.get(
        "continue_conversation", DEFAULT_CLAUDE_CODE_CONTINUE_CONVERSATION))

    claude_code_hotkey = hotkey_section.get(
        "claude_code_combination", DEFAULT_CLAUDE_CODE_HOTKEY)
    # Also check the [claude_code] section for the hotkey
    if "hotkey" in claude_section:
        claude_code_hotkey = str(claude_section["hotkey"]).strip()
    if claude_code_hotkey and claude_code_hotkey.strip():
        claude_code_hotkey = claude_code_hotkey.strip()
        try:
            _parse_hotkey(claude_code_hotkey)
            logger.info("Claude Code hotkey configured: '%s'", claude_code_hotkey)
        except Exception as e:
            logger.warning(
                "Invalid Claude Code hotkey '%s': %s. Falling back to '%s'.",
                claude_code_hotkey, e, DEFAULT_CLAUDE_CODE_HOTKEY,
            )
            claude_code_hotkey = DEFAULT_CLAUDE_CODE_HOTKEY
    else:
        claude_code_hotkey = DEFAULT_CLAUDE_CODE_HOTKEY

    # --- v1.3: Terminal Mode toggle hotkey ---
    terminal_mode_hotkey = hotkey_section.get(
        "terminal_mode_combination", DEFAULT_TERMINAL_MODE_HOTKEY)
    if terminal_mode_hotkey and terminal_mode_hotkey.strip():
        terminal_mode_hotkey = terminal_mode_hotkey.strip()
        try:
            _parse_hotkey(terminal_mode_hotkey)
            logger.info("Terminal Mode hotkey configured: '%s'", terminal_mode_hotkey)
        except Exception as e:
            logger.warning(
                "Invalid Terminal Mode hotkey '%s': %s. Falling back to '%s'.",
                terminal_mode_hotkey, e, DEFAULT_TERMINAL_MODE_HOTKEY,
            )
            terminal_mode_hotkey = DEFAULT_TERMINAL_MODE_HOTKEY
    else:
        terminal_mode_hotkey = DEFAULT_TERMINAL_MODE_HOTKEY

    # --- v0.9: Hands-Free Mode ---
    handsfree_section = data.get("handsfree", {})
    handsfree_enabled = bool(handsfree_section.get("enabled", DEFAULT_HANDSFREE_ENABLED))
    wake_phrase = str(handsfree_section.get("wake_phrase", DEFAULT_WAKE_PHRASE)).strip()
    if not wake_phrase:
        wake_phrase = DEFAULT_WAKE_PHRASE
    wake_phrase_match_mode = handsfree_section.get("match_mode", DEFAULT_WAKE_PHRASE_MATCH_MODE)
    if wake_phrase_match_mode not in ("contains", "startswith", "fuzzy"):
        wake_phrase_match_mode = DEFAULT_WAKE_PHRASE_MATCH_MODE
    silence_timeout_seconds = float(handsfree_section.get(
        "silence_timeout", DEFAULT_SILENCE_TIMEOUT_SECONDS))
    silence_timeout_seconds = max(1.0, min(silence_timeout_seconds, 10.0))
    handsfree_max_recording_seconds = int(handsfree_section.get(
        "max_recording_seconds", DEFAULT_HANDSFREE_MAX_RECORDING_SECONDS))
    handsfree_max_recording_seconds = max(10, min(handsfree_max_recording_seconds, 300))
    handsfree_pipeline = handsfree_section.get("pipeline", DEFAULT_HANDSFREE_PIPELINE)
    if handsfree_pipeline not in HANDSFREE_PIPELINES:
        handsfree_pipeline = DEFAULT_HANDSFREE_PIPELINE
    handsfree_cooldown_seconds = float(handsfree_section.get(
        "cooldown_seconds", DEFAULT_HANDSFREE_COOLDOWN_SECONDS))
    handsfree_cooldown_seconds = max(1.0, min(handsfree_cooldown_seconds, 10.0))

    # Silence threshold RMS (0.0 = adaptive auto-calibration)
    handsfree_silence_threshold_rms = float(handsfree_section.get(
        "silence_threshold_rms", DEFAULT_HANDSFREE_SILENCE_THRESHOLD_RMS))
    handsfree_silence_threshold_rms = max(0.0, min(handsfree_silence_threshold_rms, 10000.0))

    # Wake word model size
    handsfree_wake_model_size = str(handsfree_section.get(
        "wake_model_size", DEFAULT_HANDSFREE_WAKE_MODEL_SIZE)).strip().lower()
    if handsfree_wake_model_size not in HANDSFREE_WAKE_MODEL_SIZES:
        handsfree_wake_model_size = DEFAULT_HANDSFREE_WAKE_MODEL_SIZE

    # --- v0.9: Paste confirmation/delay ---
    paste_require_confirmation = bool(paste_section.get(
        "require_confirmation", DEFAULT_PASTE_CONFIRM))
    paste_delay_seconds = float(paste_section.get(
        "delay_seconds", DEFAULT_PASTE_DELAY_SECONDS))
    paste_confirmation_timeout = float(paste_section.get(
        "confirmation_timeout_seconds", DEFAULT_PASTE_CONFIRMATION_TIMEOUT))
    paste_auto_enter = bool(paste_section.get(
        "auto_enter", DEFAULT_PASTE_AUTO_ENTER))
    paste_shortcut = str(paste_section.get(
        "paste_shortcut", DEFAULT_PASTE_SHORTCUT)).strip().lower()
    if paste_shortcut not in PASTE_SHORTCUT_OPTIONS:
        logger.warning(
            "Invalid paste_shortcut '%s'. Falling back to '%s'.",
            paste_shortcut, DEFAULT_PASTE_SHORTCUT,
        )
        paste_shortcut = DEFAULT_PASTE_SHORTCUT
    # Clamp values to sane ranges
    paste_delay_seconds = max(0.0, min(paste_delay_seconds, 36000.0))
    paste_confirmation_timeout = max(5.0, min(paste_confirmation_timeout, 120.0))

    # --- v0.3: Keyring integration for API keys ---
    openai_api_key = ""
    openrouter_api_key = ""
    elevenlabs_api_key = ""

    try:
        import keyring_store

        if keyring_store.is_available():
            # Try keyring first
            kr_openai_key = keyring_store.get_credential(KEYRING_OPENAI_KEY)
            kr_openrouter_key = keyring_store.get_credential(KEYRING_OPENROUTER_KEY)

            if kr_openai_key:
                openai_api_key = kr_openai_key
                logger.info("OpenAI API key loaded from Credential Manager.")
            elif toml_api_key:
                # Migration: move from config.toml to keyring
                openai_api_key = toml_api_key
                _migrate_api_key_to_keyring(config_path, toml_api_key)

            if kr_openrouter_key:
                openrouter_api_key = kr_openrouter_key
                logger.info("OpenRouter API key loaded from Credential Manager.")

            # v0.6: ElevenLabs API key
            kr_elevenlabs_key = keyring_store.get_credential(KEYRING_ELEVENLABS_KEY)
            if kr_elevenlabs_key:
                elevenlabs_api_key = kr_elevenlabs_key
                logger.info("ElevenLabs API key loaded from Credential Manager.")
        else:
            # Keyring not available -- fall back to config.toml
            if toml_api_key:
                openai_api_key = toml_api_key
                logger.info("Keyring unavailable. Using API key from config.toml.")

    except Exception as e:
        logger.warning("Keyring integration error: %s. Falling back to config.toml.", e)
        if toml_api_key:
            openai_api_key = toml_api_key

    config = AppConfig(
        openai_api_key=openai_api_key,
        openrouter_api_key=openrouter_api_key,
        hotkey=hotkey,
        prompt_hotkey=prompt_hotkey,
        log_level=log_level.upper(),
        summarization_enabled=bool(summarization_enabled),
        summarization_provider=summarization_provider,
        summarization_model=summarization_model,
        summarization_base_url=summarization_base_url,
        summarization_custom_prompt=summarization_custom_prompt,
        audio_cues_enabled=bool(audio_cues_enabled),
        app_directory=app_dir,
        stt_backend=stt_backend,
        transcription_language=transcription_language,
        audio_device_index=audio_device_index,
        local_model_size=local_model_size,
        local_device=local_device,
        local_compute_type=local_compute_type,
        vad_filter=vad_filter,
        vocabulary_hints=vocabulary_hints,
        # v0.6: TTS fields
        tts_enabled=bool(tts_enabled),
        tts_provider=tts_provider,
        elevenlabs_api_key=elevenlabs_api_key,
        tts_voice_id=tts_voice_id,
        tts_model_id=tts_model_id,
        tts_output_format=tts_output_format,
        tts_hotkey=tts_hotkey,
        tts_ask_hotkey=tts_ask_hotkey,
        # OpenAI TTS
        tts_openai_voice=tts_openai_voice,
        tts_openai_model=tts_openai_model,
        tts_openai_format=tts_openai_format,
        tts_openai_instructions=tts_openai_instructions,
        # v0.7: Local TTS (Piper)
        tts_local_voice=tts_local_voice,
        tts_speed=tts_speed,
        tts_sentence_pause_ms=tts_sentence_pause_ms,
        tts_noise_scale=tts_noise_scale,
        tts_noise_w=tts_noise_w,
        tts_piper_speaker_id=tts_piper_speaker_id,
        tts_dynamic_emotions=tts_dynamic_emotions,
        # Audio Effects (Piper post-processing)
        audio_fx_enabled=audio_fx_enabled,
        audio_fx_pitch_semitones=audio_fx_pitch_semitones,
        audio_fx_formant_shift=audio_fx_formant_shift,
        audio_fx_bass_db=audio_fx_bass_db,
        audio_fx_treble_db=audio_fx_treble_db,
        audio_fx_reverb_mix=audio_fx_reverb_mix,
        # TTS LLM Preprocessing
        tts_preprocess_with_llm=tts_preprocess_with_llm,
        tts_preprocess_prompt=tts_preprocess_prompt,
        tts_emotion_prompt=tts_emotion_prompt,
        # v0.9: HTTP API
        api_enabled=api_enabled,
        api_port=api_port,
        # v0.9: Paste confirmation/delay
        paste_require_confirmation=paste_require_confirmation,
        paste_delay_seconds=paste_delay_seconds,
        paste_confirmation_timeout=paste_confirmation_timeout,
        paste_auto_enter=paste_auto_enter,
        paste_shortcut=paste_shortcut,
        # v1.0: TTS Audio Cache
        tts_cache_enabled=tts_cache_enabled,
        tts_cache_max_size_mb=tts_cache_max_size_mb,
        tts_cache_max_age_days=tts_cache_max_age_days,
        tts_cache_max_entries=tts_cache_max_entries,
        # v1.0: TTS Audio Export
        tts_export_enabled=tts_export_enabled,
        tts_export_path=tts_export_path,
        # v1.3: Terminal Mode toggle hotkey
        terminal_mode_hotkey=terminal_mode_hotkey,
        # v1.2: Claude Code CLI
        claude_code_enabled=claude_code_enabled,
        claude_code_hotkey=claude_code_hotkey,
        claude_code_working_dir=claude_code_working_dir,
        claude_code_system_prompt=claude_code_system_prompt,
        claude_code_timeout=claude_code_timeout,
        claude_code_response_mode=claude_code_response_mode,
        claude_code_skip_permissions=claude_code_skip_permissions,
        claude_code_continue_conversation=claude_code_continue_conversation,
        # v0.9: Hands-Free Mode
        handsfree_enabled=handsfree_enabled,
        wake_phrase=wake_phrase,
        wake_phrase_match_mode=wake_phrase_match_mode,
        silence_timeout_seconds=silence_timeout_seconds,
        handsfree_max_recording_seconds=handsfree_max_recording_seconds,
        handsfree_pipeline=handsfree_pipeline,
        handsfree_cooldown_seconds=handsfree_cooldown_seconds,
        handsfree_silence_threshold_rms=handsfree_silence_threshold_rms,
        handsfree_wake_model_size=handsfree_wake_model_size,
    )

    # REQ-S01: Only log the masked key
    logger.info("Configuration loaded. API key: %s", config.masked_api_key())
    logger.debug("Log level: %s", config.log_level)
    logger.debug(
        "Summarization: %s (provider=%s, model=%s)",
        "enabled" if config.summarization_enabled else "disabled",
        config.summarization_provider,
        config.summarization_model,
    )
    logger.debug("Audio cues: %s", "enabled" if config.audio_cues_enabled else "disabled")
    logger.debug(
        "STT backend: %s (local model=%s, device=%s, compute=%s, vad=%s)",
        config.stt_backend,
        config.local_model_size,
        config.local_device,
        config.local_compute_type,
        "on" if config.vad_filter else "off",
    )
    logger.debug("App directory: %s", config.app_directory)
    logger.debug(
        "TTS: %s (provider=%s, voice=%s, model=%s)",
        "enabled" if config.tts_enabled else "disabled",
        config.tts_provider,
        config.tts_voice_id,
        config.tts_model_id,
    )

    return config
