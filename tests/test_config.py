"""Tests for configuration loading and validation.

Validates:
- US-0.1.6: Configuration file behavior
- REQ-S01: API key masking in logs
- REQ-S02: No hardcoded API keys
- Hotkey configuration (default, custom, invalid fallback)
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from constants import DEFAULT_HOTKEY
from config import AppConfig, load_config, CONFIG_TEMPLATE


class TestAppConfig:
    """Test the AppConfig dataclass."""

    def test_default_values(self):
        """AppConfig should have sensible defaults."""
        config = AppConfig()
        assert config.openai_api_key == ""
        assert config.log_level == "INFO"

    def test_default_hotkey(self):
        """AppConfig.hotkey should default to DEFAULT_HOTKEY."""
        config = AppConfig()
        assert config.hotkey == DEFAULT_HOTKEY
        assert config.hotkey == "ctrl+shift+v"

    def test_custom_hotkey(self):
        """AppConfig should accept a custom hotkey value."""
        config = AppConfig(hotkey="F9")
        assert config.hotkey == "F9"

    def test_masked_api_key_empty(self):
        """REQ-S01: Empty API key shows '<empty>'."""
        config = AppConfig(openai_api_key="")
        assert config.masked_api_key() == "<empty>"

    def test_masked_api_key_short(self):
        """REQ-S01: Short API key (<=4 chars) is fully masked."""
        config = AppConfig(openai_api_key="abcd")
        assert config.masked_api_key() == "****"

    def test_masked_api_key_normal(self):
        """REQ-S01: Normal API key shows only last 4 characters."""
        config = AppConfig(openai_api_key="sk-1234567890abcdef")
        masked = config.masked_api_key()
        assert masked.endswith("cdef")
        assert "sk-1234567890ab" not in masked
        assert masked.count("*") == len("sk-1234567890abcdef") - 4

    def test_config_path(self, tmp_path):
        """config_path should be in the app directory."""
        config = AppConfig(app_directory=tmp_path)
        assert config.config_path == tmp_path / "config.toml"

    def test_log_path(self, tmp_path):
        """log_path should be in the app directory."""
        config = AppConfig(app_directory=tmp_path)
        assert config.log_path == tmp_path / "voice-paste.log"

    def test_frozen_dataclass(self):
        """AppConfig should be immutable (frozen)."""
        config = AppConfig(openai_api_key="test-key")
        with pytest.raises(AttributeError):
            config.openai_api_key = "new-key"


class TestLoadConfig:
    """Test configuration file loading."""

    def test_missing_config_creates_template(self, tmp_path):
        """US-0.1.6: Missing config.toml creates template and returns None."""
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is None
            config_file = tmp_path / "config.toml"
            assert config_file.exists()
            content = config_file.read_text(encoding="utf-8")
            assert "openai_api_key" in content

    def test_empty_api_key_returns_none(self, tmp_path):
        """US-0.1.6: Empty API key in config returns None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = ""\n\n[logging]\nlevel = "INFO"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is None

    def test_missing_api_key_returns_none(self, tmp_path):
        """US-0.1.6: Missing API key field returns None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[api]\n\n[logging]\nlevel = \"INFO\"\n",
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is None

    def test_valid_config_loads_successfully(self, tmp_path):
        """US-0.1.6: Valid config.toml loads all fields."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-test123456789"\n\n'
            '[logging]\nlevel = "DEBUG"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.openai_api_key == "sk-test123456789"
            assert result.log_level == "DEBUG"

    def test_malformed_toml_returns_none(self, tmp_path):
        """US-0.1.6: Malformed TOML returns None gracefully."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "this is not valid [toml content {\n",
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is None

    def test_default_log_level(self, tmp_path):
        """Log level defaults to INFO if not specified."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.log_level == "INFO"

    def test_api_key_with_whitespace_is_trimmed(self, tmp_path):
        """API key with whitespace should be trimmed."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "  sk-testkey1234  "\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.openai_api_key == "sk-testkey1234"


class TestLoadConfigHotkey:
    """Test hotkey configuration loading from TOML."""

    def test_custom_hotkey_from_config(self, tmp_path):
        """Custom hotkey combination should be loaded from [hotkey] section."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n\n'
            '[hotkey]\ncombination = "ctrl+alt+r"\n',
            encoding="utf-8",
        )
        # keyboard is imported locally inside load_config() as
        # `import keyboard as _kb`. Mock it via sys.modules so
        # parse_hotkey succeeds without the real keyboard library.
        mock_kb_module = MagicMock()
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyboard": mock_kb_module}):
            result = load_config()
            assert result is not None
            assert result.hotkey == "ctrl+alt+r"

    def test_missing_hotkey_section_uses_default(self, tmp_path):
        """When [hotkey] section is absent, DEFAULT_HOTKEY should be used."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.hotkey == DEFAULT_HOTKEY

    def test_empty_hotkey_uses_default(self, tmp_path):
        """An empty hotkey string should fall back to DEFAULT_HOTKEY."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n\n'
            '[hotkey]\ncombination = ""\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.hotkey == DEFAULT_HOTKEY

    def test_whitespace_only_hotkey_uses_default(self, tmp_path):
        """A hotkey that is only whitespace should fall back to DEFAULT_HOTKEY."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n\n'
            '[hotkey]\ncombination = "   "\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.hotkey == DEFAULT_HOTKEY

    def test_invalid_hotkey_falls_back_to_default(self, tmp_path):
        """An invalid hotkey string should fall back to DEFAULT_HOTKEY after keyboard.parse_hotkey raises."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n\n'
            '[hotkey]\ncombination = "not+a+real+key+combo+!@#$"\n',
            encoding="utf-8",
        )
        # Mock keyboard.parse_hotkey to raise an error for invalid combos
        mock_kb_module = MagicMock()
        mock_kb_module.parse_hotkey.side_effect = ValueError("Invalid hotkey")

        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyboard": mock_kb_module}):
            result = load_config()
            assert result is not None
            assert result.hotkey == DEFAULT_HOTKEY

    def test_hotkey_is_trimmed(self, tmp_path):
        """Hotkey string with surrounding whitespace should be trimmed."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n\n'
            '[hotkey]\ncombination = "  ctrl+shift+v  "\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.hotkey == "ctrl+shift+v"

    def test_valid_config_default_hotkey_is_ctrl_shift_v(self, tmp_path):
        """When no hotkey is specified, loaded config should have ctrl+shift+v."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path):
            result = load_config()
            assert result is not None
            assert result.hotkey == "ctrl+shift+v"


class TestConfigTemplateHotkey:
    """Test that the config template documents the hotkey section."""

    def test_template_has_hotkey_section(self):
        """Config template should include a [hotkey] section."""
        assert "[hotkey]" in CONFIG_TEMPLATE

    def test_template_default_hotkey_is_ctrl_shift_v(self):
        """Config template should show ctrl+shift+v as the default combination."""
        assert 'combination = "ctrl+shift+v"' in CONFIG_TEMPLATE

    def test_template_warns_about_ctrl_windows(self):
        """Config template should warn that ctrl+windows does not work reliably."""
        assert "ctrl+windows" in CONFIG_TEMPLATE.lower()


class TestNoHardcodedSecrets:
    """REQ-S02: Verify no hardcoded API keys in source code."""

    def test_config_template_has_empty_key(self):
        """Config template must have an empty API key."""
        assert 'openai_api_key = ""' in CONFIG_TEMPLATE

    def test_config_template_no_real_key(self):
        """Config template must not contain a real-looking API key."""
        assert "sk-" not in CONFIG_TEMPLATE
