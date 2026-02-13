"""Configuration loading and validation for the Voice-to-Summary Paste Tool.

Reads config.toml from the application directory. Creates a template if missing.
"""

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from constants import DEFAULT_HOTKEY, LOG_FILENAME

logger = logging.getLogger(__name__)

# Template content for config.toml when it does not exist
CONFIG_TEMPLATE = """\
# Voice-to-Summary Paste Tool Configuration
# See README.md for full documentation of all options.

[api]
# Your OpenAI API key (required)
# Get one at https://platform.openai.com/api-keys
openai_api_key = ""

[hotkey]
# Global hotkey to start/stop recording (default: "ctrl+shift+v")
# Uses the 'keyboard' library key name format.
# Examples: "ctrl+shift+v", "ctrl+alt+r", "F9"
# NOTE: "ctrl+windows" does NOT work reliably on Windows 10/11 because
# the OS intercepts Win-key combinations before applications see them.
combination = "ctrl+shift+v"

[summarization]
# Enable text cleanup and summarization via GPT-4o-mini (default: true)
enabled = true

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


@dataclass(frozen=True)
class AppConfig:
    """Application configuration loaded from config.toml.

    Attributes:
        openai_api_key: OpenAI API key for Whisper and GPT APIs.
        hotkey: Global hotkey combination string for the keyboard library.
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR).
        summarization_enabled: Whether to run LLM summarization (v0.2+).
        audio_cues_enabled: Whether to play audio feedback cues (v0.2+).
        app_directory: Resolved path to the application directory.
    """

    openai_api_key: str = ""
    hotkey: str = DEFAULT_HOTKEY
    log_level: str = "INFO"
    summarization_enabled: bool = True
    audio_cues_enabled: bool = True
    app_directory: Path = field(default_factory=_get_app_directory)

    @property
    def config_path(self) -> Path:
        """Path to the config.toml file."""
        return self.app_directory / "config.toml"

    @property
    def log_path(self) -> Path:
        """Path to the log file."""
        return self.app_directory / LOG_FILENAME

    def masked_api_key(self) -> str:
        """Return the API key with all but the last 4 characters masked.

        REQ-S01: Never log the full API key.

        Returns:
            Masked API key string, or '<empty>' if not set.
        """
        if not self.openai_api_key:
            return "<empty>"
        if len(self.openai_api_key) <= 4:
            return "****"
        return "*" * (len(self.openai_api_key) - 4) + self.openai_api_key[-4:]


def load_config() -> AppConfig | None:
    """Load configuration from config.toml.

    If config.toml does not exist, creates a template and returns None.
    If the API key is empty or missing, returns None.
    If the TOML is malformed, returns None.

    Returns:
        AppConfig instance if valid, None if the application should exit.
    """
    app_dir = _get_app_directory()
    config_path = app_dir / "config.toml"

    if not config_path.exists():
        logger.warning(
            "config.toml not found at %s. Creating template.", config_path
        )
        try:
            config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
            logger.info(
                "Created config.toml template at %s. "
                "Please add your OpenAI API key and restart.",
                config_path,
            )
        except OSError as e:
            logger.error("Failed to create config.toml template: %s", e)
        return None

    try:
        raw = config_path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        logger.error("config.toml has invalid syntax: %s", e)
        return None
    except OSError as e:
        logger.error("Failed to read config.toml: %s", e)
        return None

    # Extract values with defaults
    api_section = data.get("api", {})
    hotkey_section = data.get("hotkey", {})
    logging_section = data.get("logging", {})
    summarization_section = data.get("summarization", {})
    feedback_section = data.get("feedback", {})

    api_key = api_section.get("openai_api_key", "")
    hotkey = hotkey_section.get("combination", DEFAULT_HOTKEY)
    log_level = logging_section.get("level", "INFO")
    summarization_enabled = summarization_section.get("enabled", True)
    audio_cues_enabled = feedback_section.get("audio_cues", True)

    # Validate hotkey string using the keyboard library
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

    if not api_key or not api_key.strip():
        logger.error(
            "OpenAI API key not configured in config.toml. "
            "Please add your API key to [api] openai_api_key and restart."
        )
        return None

    config = AppConfig(
        openai_api_key=api_key.strip(),
        hotkey=hotkey,
        log_level=log_level.upper(),
        summarization_enabled=bool(summarization_enabled),
        audio_cues_enabled=bool(audio_cues_enabled),
        app_directory=app_dir,
    )

    # REQ-S01: Only log the masked key
    logger.info("Configuration loaded. API key: %s", config.masked_api_key())
    logger.debug("Log level: %s", config.log_level)
    logger.debug("Summarization: %s", "enabled" if config.summarization_enabled else "disabled")
    logger.debug("Audio cues: %s", "enabled" if config.audio_cues_enabled else "disabled")
    logger.debug("App directory: %s", config.app_directory)

    return config
