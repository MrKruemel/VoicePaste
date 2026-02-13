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
    DEFAULT_PROMPT_HOTKEY,
    DEFAULT_STT_BACKEND,
    DEFAULT_SUMMARIZATION_PROVIDER,
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
    STT_BACKENDS,
    SUMMARIZE_MODEL,
    SUMMARIZE_SYSTEM_PROMPT,
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
    local_model_size: str = LOCAL_STT_DEFAULT_MODEL_SIZE
    local_device: str = LOCAL_STT_DEFAULT_DEVICE
    local_compute_type: str = LOCAL_STT_DEFAULT_COMPUTE_TYPE
    vad_filter: bool = LOCAL_STT_DEFAULT_VAD_FILTER

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

[hotkey]
combination = "{esc(self.hotkey)}"
# Voice Prompt hotkey: record speech, send as prompt to LLM, paste answer
prompt_combination = "{esc(self.prompt_hotkey)}"

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

    # --- v0.3: Keyring integration for API keys ---
    openai_api_key = ""
    openrouter_api_key = ""

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

    return config
