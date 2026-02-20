"""Configuration loading, validation, and persistence for the Voice-to-Summary Paste Tool.

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
    DEFAULT_HOTKEY,
    DEFAULT_API_ENABLED,
    DEFAULT_API_PORT,
    DEFAULT_PASTE_AUTO_ENTER,
    DEFAULT_PASTE_CONFIRM,
    DEFAULT_PASTE_CONFIRMATION_TIMEOUT,
    DEFAULT_PASTE_DELAY_SECONDS,
    DEFAULT_PIPER_VOICE,
    DEFAULT_PROMPT_HOTKEY,
    DEFAULT_STT_BACKEND,
    DEFAULT_SUMMARIZATION_PROVIDER,
    DEFAULT_TTS_ASK_HOTKEY,
    DEFAULT_TTS_HOTKEY,
    DEFAULT_TTS_MODEL_ID,
    DEFAULT_TTS_OUTPUT_FORMAT,
    DEFAULT_TTS_PROVIDER,
    DEFAULT_TTS_VOICE_ID,
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
    PIPER_VOICE_MODELS,
    STT_BACKENDS,
    SUMMARIZE_MODEL,
    SUMMARIZE_SYSTEM_PROMPT,
    TTS_PROVIDERS,
)

logger = logging.getLogger(__name__)

# Template content for config.toml when it does not exist (v0.3)
CONFIG_TEMPLATE = """\
# Voice-to-Summary Paste Tool Configuration
# See README.md for full documentation of all options.

# NOTE: API keys are stored securely in Windows Credential Manager.
# Use the Settings dialog (right-click tray icon > Settings) to manage keys.
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

[feedback]
# Play audio cues on recording start/stop (default: true)
audio_cues = true
# Show floating overlay window with state feedback (default: true)
show_overlay = true

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
    local_model_size: str = LOCAL_STT_DEFAULT_MODEL_SIZE
    local_device: str = LOCAL_STT_DEFAULT_DEVICE
    local_compute_type: str = LOCAL_STT_DEFAULT_COMPUTE_TYPE
    vad_filter: bool = LOCAL_STT_DEFAULT_VAD_FILTER

    # --- v0.6: TTS (Text-to-Speech) fields ---
    tts_enabled: bool = False
    tts_provider: str = DEFAULT_TTS_PROVIDER
    elevenlabs_api_key: str = ""
    tts_voice_id: str = DEFAULT_TTS_VOICE_ID
    tts_model_id: str = DEFAULT_TTS_MODEL_ID
    tts_output_format: str = DEFAULT_TTS_OUTPUT_FORMAT
    tts_hotkey: str = DEFAULT_TTS_HOTKEY
    tts_ask_hotkey: str = DEFAULT_TTS_ASK_HOTKEY

    # --- v0.7: Local TTS (Piper) fields ---
    tts_local_voice: str = DEFAULT_PIPER_VOICE

    # --- v0.8: Overlay ---
    show_overlay: bool = True

    # --- v0.9: HTTP API ---
    api_enabled: bool = DEFAULT_API_ENABLED
    api_port: int = DEFAULT_API_PORT

    # --- v0.9: Confirm-before-paste ---
    paste_require_confirmation: bool = DEFAULT_PASTE_CONFIRM
    paste_delay_seconds: float = DEFAULT_PASTE_DELAY_SECONDS
    paste_confirmation_timeout: float = DEFAULT_PASTE_CONFIRMATION_TIMEOUT
    paste_auto_enter: bool = DEFAULT_PASTE_AUTO_ENTER

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
# API keys are stored in Windows Credential Manager.
# Use the Settings dialog (right-click tray icon > Settings) to manage keys.
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

[transcription]
# Backend: "cloud" (OpenAI Whisper API) or "local" (faster-whisper, offline)
backend = "{esc(self.stt_backend)}"
# Local model size: tiny, base, small, medium, large-v2, large-v3
model_size = "{esc(self.local_model_size)}"
# Device: cpu, cuda, auto
device = "{esc(self.local_device)}"
# Compute type: int8, float16, float32, auto
compute_type = "{esc(self.local_compute_type)}"
# Silero VAD: filter silence before Whisper (disable if .exe crashes during transcription)
vad_filter = {str(self.vad_filter).lower()}

[summarization]
enabled = {str(self.summarization_enabled).lower()}
provider = "{esc(self.summarization_provider)}"
model = "{esc(self.summarization_model)}"
base_url = "{esc(self.summarization_base_url)}"
custom_prompt = "{esc(self.summarization_custom_prompt)}"

[tts]
# Text-to-Speech: "elevenlabs" (cloud) or "piper" (local, offline)
enabled = {str(self.tts_enabled).lower()}
provider = "{esc(self.tts_provider)}"
# --- Cloud (ElevenLabs) fields ---
voice_id = "{esc(self.tts_voice_id)}"
model_id = "{esc(self.tts_model_id)}"
output_format = "{esc(self.tts_output_format)}"
# --- Local (Piper) fields (v0.7) ---
# Voice model name. Available: de_DE-thorsten-medium, de_DE-thorsten-high,
# en_US-lessac-medium, en_US-amy-medium. Download via Settings dialog.
local_voice = "{esc(self.tts_local_voice)}"

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

[feedback]
audio_cues = {str(self.audio_cues_enabled).lower()}
show_overlay = {str(self.show_overlay).lower()}

[logging]
level = "{esc(self.log_level)}"
"""
        try:
            config_path = self.config_path
            # Write to temp file first, then replace atomically
            tmp_path = config_path.with_suffix(".toml.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(config_path)
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
    show_overlay = feedback_section.get("show_overlay", True)

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

    # Validate hotkey strings using the keyboard library
    if hotkey and hotkey.strip():
        hotkey = hotkey.strip()
        try:
            import keyboard as _kb
            _kb.parse_hotkey(hotkey)
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
            import keyboard as _kb
            _kb.parse_hotkey(prompt_hotkey)
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
            import keyboard as _kb
            _kb.parse_hotkey(tts_hotkey)
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
            import keyboard as _kb
            _kb.parse_hotkey(tts_ask_hotkey)
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

    # v0.7: Local TTS (Piper) voice name
    tts_local_voice = tts_section.get("local_voice", DEFAULT_PIPER_VOICE)
    if tts_local_voice not in PIPER_VOICE_MODELS:
        logger.warning(
            "Unknown Piper voice '%s'. Falling back to '%s'.",
            tts_local_voice,
            DEFAULT_PIPER_VOICE,
        )
        tts_local_voice = DEFAULT_PIPER_VOICE

    # --- v0.9: Paste confirmation/delay ---
    paste_require_confirmation = bool(paste_section.get(
        "require_confirmation", DEFAULT_PASTE_CONFIRM))
    paste_delay_seconds = float(paste_section.get(
        "delay_seconds", DEFAULT_PASTE_DELAY_SECONDS))
    paste_confirmation_timeout = float(paste_section.get(
        "confirmation_timeout_seconds", DEFAULT_PASTE_CONFIRMATION_TIMEOUT))
    paste_auto_enter = bool(paste_section.get(
        "auto_enter", DEFAULT_PASTE_AUTO_ENTER))
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
        local_model_size=local_model_size,
        local_device=local_device,
        local_compute_type=local_compute_type,
        vad_filter=vad_filter,
        # v0.6: TTS fields
        tts_enabled=bool(tts_enabled),
        tts_provider=tts_provider,
        elevenlabs_api_key=elevenlabs_api_key,
        tts_voice_id=tts_voice_id,
        tts_model_id=tts_model_id,
        tts_output_format=tts_output_format,
        tts_hotkey=tts_hotkey,
        tts_ask_hotkey=tts_ask_hotkey,
        # v0.7: Local TTS (Piper)
        tts_local_voice=tts_local_voice,
        # v0.8: Overlay
        show_overlay=bool(show_overlay),
        # v0.9: HTTP API
        api_enabled=api_enabled,
        api_port=api_port,
        # v0.9: Paste confirmation/delay
        paste_require_confirmation=paste_require_confirmation,
        paste_delay_seconds=paste_delay_seconds,
        paste_confirmation_timeout=paste_confirmation_timeout,
        paste_auto_enter=paste_auto_enter,
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
