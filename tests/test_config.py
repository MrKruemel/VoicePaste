"""Tests for configuration loading and validation.

Validates:
- US-0.1.6: Configuration file behavior
- REQ-S01: API key masking in logs
- REQ-S02: No hardcoded API keys
- Hotkey configuration (default, custom, invalid fallback)
- v0.3: Mutable AppConfig, keyring integration, provider/model/base_url fields
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from constants import DEFAULT_HOTKEY
from config import AppConfig, load_config, CONFIG_TEMPLATE


# --- Mock keyring_store for all load_config tests ---
def _mock_keyring_unavailable():
    """Return a mock keyring_store that reports keyring as unavailable."""
    mock_ks = MagicMock()
    mock_ks.is_available.return_value = False
    mock_ks.get_credential.return_value = None
    return mock_ks


class TestAppConfig:
    """Test the AppConfig dataclass."""

    def test_default_values(self):
        """AppConfig should have sensible defaults."""
        config = AppConfig()
        assert config.openai_api_key == ""
        assert config.log_level == "INFO"

    def test_default_hotkey(self):
        """AppConfig.hotkey should default to DEFAULT_HOTKEY (ctrl+alt+r)."""
        config = AppConfig()
        assert config.hotkey == DEFAULT_HOTKEY
        assert config.hotkey == "ctrl+alt+r"

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

    def test_mutable_dataclass(self):
        """v0.3: AppConfig is mutable (no longer frozen) for hot-reload."""
        config = AppConfig(openai_api_key="test-key")
        config.openai_api_key = "new-key"
        assert config.openai_api_key == "new-key"

    def test_new_v03_fields(self):
        """v0.3: AppConfig has provider, model, base_url, custom_prompt fields."""
        config = AppConfig()
        assert config.summarization_provider == "openai"
        assert config.summarization_model == "gpt-4o-mini"
        assert config.summarization_base_url == ""
        assert config.summarization_custom_prompt == ""
        assert config.openrouter_api_key == ""

    def test_active_summarization_api_key_openai(self):
        """v0.3: active_summarization_api_key returns openai key for openai provider."""
        config = AppConfig(
            openai_api_key="sk-openai",
            openrouter_api_key="sk-or-test",
            summarization_provider="openai",
        )
        assert config.active_summarization_api_key == "sk-openai"

    def test_active_summarization_api_key_openrouter(self):
        """v0.3: active_summarization_api_key returns openrouter key for openrouter provider."""
        config = AppConfig(
            openai_api_key="sk-openai",
            openrouter_api_key="sk-or-test",
            summarization_provider="openrouter",
        )
        assert config.active_summarization_api_key == "sk-or-test"

    def test_active_base_url_default_openai(self):
        """v0.3: active_summarization_base_url returns None for OpenAI default."""
        config = AppConfig(summarization_provider="openai")
        assert config.active_summarization_base_url is None

    def test_active_base_url_default_openrouter(self):
        """v0.3: active_summarization_base_url returns OpenRouter URL."""
        config = AppConfig(summarization_provider="openrouter")
        assert "openrouter.ai" in config.active_summarization_base_url

    def test_active_base_url_custom(self):
        """v0.3: Custom base_url overrides provider default."""
        config = AppConfig(
            summarization_provider="openai",
            summarization_base_url="https://custom.api.com/v1",
        )
        assert config.active_summarization_base_url == "https://custom.api.com/v1"

    def test_active_system_prompt_default(self):
        """v0.3: active_system_prompt returns default when custom is empty."""
        from constants import SUMMARIZE_SYSTEM_PROMPT
        config = AppConfig(summarization_custom_prompt="")
        assert config.active_system_prompt == SUMMARIZE_SYSTEM_PROMPT

    def test_active_system_prompt_custom(self):
        """v0.3: active_system_prompt returns custom prompt when set."""
        config = AppConfig(summarization_custom_prompt="Clean up this text.")
        assert config.active_system_prompt == "Clean up this text."

    def test_masked_api_key_with_explicit_key(self):
        """v0.3: masked_api_key accepts explicit key parameter."""
        config = AppConfig()
        assert config.masked_api_key("sk-test1234") == "*******1234"
        assert config.masked_api_key("") == "<empty>"


class TestLoadConfig:
    """Test configuration file loading."""

    def test_missing_config_creates_template(self, tmp_path):
        """US-0.1.6: Missing config.toml creates template."""
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            config_file = tmp_path / "config.toml"
            assert config_file.exists()
            content = config_file.read_text(encoding="utf-8")
            assert "[hotkey]" in content

    def test_empty_api_key_returns_config(self, tmp_path):
        """v0.3: Empty API key no longer returns None -- returns config with empty key."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = ""\n\n[logging]\nlevel = "INFO"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.openai_api_key == ""

    def test_missing_api_key_returns_config(self, tmp_path):
        """v0.3: Missing API key field returns config with empty key."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[api]\n\n[logging]\nlevel = \"INFO\"\n",
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.openai_api_key == ""

    def test_valid_config_loads_successfully(self, tmp_path):
        """US-0.1.6: Valid config.toml loads all fields."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-test123456789"\n\n'
            '[logging]\nlevel = "DEBUG"\n',
            encoding="utf-8",
        )
        # Mock keyring as unavailable so key comes from config.toml
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.openai_api_key == "sk-test123456789"
            assert result.log_level == "DEBUG"

    def test_malformed_toml_returns_default_config(self, tmp_path):
        """v0.3: Malformed TOML returns default config (not None) for Settings dialog."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "this is not valid [toml content {\n",
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.openai_api_key == ""

    def test_default_log_level(self, tmp_path):
        """Log level defaults to INFO if not specified."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
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
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.openai_api_key == "sk-testkey1234"

    def test_provider_loaded_from_config(self, tmp_path):
        """v0.3: Provider is loaded from [summarization] section."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-test1234"\n\n'
            '[summarization]\nprovider = "openrouter"\n'
            'model = "openai/gpt-4o-mini"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.summarization_provider == "openrouter"
            assert result.summarization_model == "openai/gpt-4o-mini"

    def test_invalid_provider_falls_back_to_openai(self, tmp_path):
        """v0.3: Invalid provider falls back to openai."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-test1234"\n\n'
            '[summarization]\nprovider = "invalid"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.summarization_provider == "openai"

    def test_custom_prompt_loaded(self, tmp_path):
        """v0.3: Custom prompt is loaded from config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-test1234"\n\n'
            '[summarization]\ncustom_prompt = "Clean up this text."\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.summarization_custom_prompt == "Clean up this text."


class TestSaveToToml:
    """v0.3: Test save_to_toml() method."""

    def test_save_creates_file(self, tmp_path):
        """save_to_toml should create/overwrite config.toml."""
        config = AppConfig(app_directory=tmp_path, hotkey="ctrl+alt+r")
        assert config.save_to_toml()
        assert (tmp_path / "config.toml").exists()

    def test_save_does_not_write_api_keys(self, tmp_path):
        """save_to_toml must NOT write API keys to file."""
        config = AppConfig(
            app_directory=tmp_path,
            openai_api_key="sk-secret123",
            openrouter_api_key="sk-or-secret456",
        )
        config.save_to_toml()
        content = (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert "sk-secret123" not in content
        assert "sk-or-secret456" not in content

    def test_save_writes_provider_and_model(self, tmp_path):
        """save_to_toml should write provider and model."""
        config = AppConfig(
            app_directory=tmp_path,
            summarization_provider="openrouter",
            summarization_model="anthropic/claude-3-haiku",
        )
        config.save_to_toml()
        content = (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert 'provider = "openrouter"' in content
        assert 'model = "anthropic/claude-3-haiku"' in content

    def test_save_escapes_newlines(self, tmp_path):
        """SEC-015: save_to_toml should escape newlines in custom_prompt."""
        config = AppConfig(
            app_directory=tmp_path,
            summarization_custom_prompt="line1\nline2\rline3",
        )
        config.save_to_toml()
        content = (tmp_path / "config.toml").read_text(encoding="utf-8")
        # Should contain escaped newlines, not literal ones in TOML string
        assert "\\n" in content
        assert "\\r" in content

    def test_save_atomic_via_tmp(self, tmp_path):
        """save_to_toml should not leave .tmp files on success."""
        config = AppConfig(app_directory=tmp_path)
        config.save_to_toml()
        assert not (tmp_path / "config.toml.tmp").exists()


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
        mock_kb_module = MagicMock()
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {
                 "keyboard": mock_kb_module,
                 "keyring_store": _mock_keyring_unavailable(),
             }):
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
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
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
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
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
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.hotkey == DEFAULT_HOTKEY

    def test_invalid_hotkey_falls_back_to_default(self, tmp_path):
        """An invalid hotkey string should fall back to DEFAULT_HOTKEY."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n\n'
            '[hotkey]\ncombination = "not+a+real+key+combo+!@#$"\n',
            encoding="utf-8",
        )

        with patch("config._get_app_directory", return_value=tmp_path), \
             patch("hotkey._parse_hotkey", side_effect=ValueError("Invalid hotkey")), \
             patch.dict("sys.modules", {
                 "keyring_store": _mock_keyring_unavailable(),
             }):
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
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.hotkey == "ctrl+shift+v"

    def test_valid_config_default_hotkey_is_ctrl_alt_r(self, tmp_path):
        """When no hotkey is specified, loaded config should have ctrl+alt+r."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.hotkey == "ctrl+alt+r"


class TestConfigTemplateHotkey:
    """Test that the config template documents the hotkey section."""

    def test_template_has_hotkey_section(self):
        """Config template should include a [hotkey] section."""
        assert "[hotkey]" in CONFIG_TEMPLATE

    def test_template_default_hotkey_is_ctrl_alt_r(self):
        """Config template should show ctrl+alt+r as the default combination."""
        assert 'combination = "ctrl+alt+r"' in CONFIG_TEMPLATE

    def test_template_has_summarization_section(self):
        """v0.3: Config template should include a [summarization] section."""
        assert "[summarization]" in CONFIG_TEMPLATE
        assert "provider" in CONFIG_TEMPLATE
        assert "model" in CONFIG_TEMPLATE
        assert "base_url" in CONFIG_TEMPLATE
        assert "custom_prompt" in CONFIG_TEMPLATE


class TestNoHardcodedSecrets:
    """REQ-S02: Verify no hardcoded API keys in source code."""

    def test_config_template_has_no_real_key(self):
        """Config template must not contain a real-looking API key."""
        assert "sk-" not in CONFIG_TEMPLATE


# --- v0.4: Local STT tests ---

class TestAppConfigLocalSTT:
    """Test v0.4 local STT fields on AppConfig."""

    def test_default_stt_backend_is_cloud(self):
        """Default STT backend should be 'cloud'."""
        config = AppConfig(app_directory=Path("/tmp"))
        assert config.stt_backend == "cloud"

    def test_default_local_model_size(self):
        """Default local model size should be 'base'."""
        config = AppConfig(app_directory=Path("/tmp"))
        assert config.local_model_size == "base"

    def test_default_local_device(self):
        """Default local device should be 'cpu'."""
        config = AppConfig(app_directory=Path("/tmp"))
        assert config.local_device == "cpu"

    def test_default_local_compute_type(self):
        """Default local compute type should be 'int8'."""
        config = AppConfig(app_directory=Path("/tmp"))
        assert config.local_compute_type == "int8"

    def test_custom_stt_fields(self):
        """Custom STT fields should be stored."""
        config = AppConfig(
            app_directory=Path("/tmp"),
            stt_backend="local",
            local_model_size="small",
            local_device="cuda",
            local_compute_type="float16",
        )
        assert config.stt_backend == "local"
        assert config.local_model_size == "small"
        assert config.local_device == "cuda"
        assert config.local_compute_type == "float16"


class TestLoadConfigLocalSTT:
    """Test loading v0.4 local STT fields from config.toml."""

    def test_stt_backend_loaded_from_config(self, tmp_path):
        """STT backend should be loaded from [transcription] section."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n\n'
            '[transcription]\nbackend = "local"\n'
            'model_size = "small"\n'
            'device = "cuda"\n'
            'compute_type = "float16"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result is not None
            assert result.stt_backend == "local"
            assert result.local_model_size == "small"
            assert result.local_device == "cuda"
            assert result.local_compute_type == "float16"

    def test_invalid_stt_backend_falls_back(self, tmp_path):
        """Invalid STT backend falls back to 'cloud'."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[transcription]\nbackend = "quantum"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result.stt_backend == "cloud"

    def test_invalid_model_size_falls_back(self, tmp_path):
        """Invalid model size falls back to 'base'."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[transcription]\nmodel_size = "gigantic"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result.local_model_size == "base"

    def test_missing_transcription_section_uses_defaults(self, tmp_path):
        """Missing [transcription] section should use all defaults."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[api]\nopenai_api_key = "sk-testkey1234"\n',
            encoding="utf-8",
        )
        with patch("config._get_app_directory", return_value=tmp_path), \
             patch.dict("sys.modules", {"keyring_store": _mock_keyring_unavailable()}):
            result = load_config()
            assert result.stt_backend == "cloud"
            assert result.local_model_size == "base"
            assert result.local_device == "cpu"
            assert result.local_compute_type == "int8"


class TestSaveToTomlLocalSTT:
    """Test that save_to_toml() writes v0.4 local STT fields."""

    def test_save_writes_transcription_section(self, tmp_path):
        """save_to_toml() should write [transcription] section."""
        config = AppConfig(
            app_directory=tmp_path,
            stt_backend="local",
            local_model_size="small",
            local_device="cuda",
            local_compute_type="float16",
        )
        assert config.save_to_toml() is True

        content = (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert "[transcription]" in content
        assert 'backend = "local"' in content
        assert 'model_size = "small"' in content
        assert 'device = "cuda"' in content
        assert 'compute_type = "float16"' in content

    def test_save_default_values_roundtrip(self, tmp_path):
        """Default STT fields should survive save/load roundtrip."""
        config = AppConfig(app_directory=tmp_path)
        config.save_to_toml()

        import tomllib
        raw = (tmp_path / "config.toml").read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
        assert data["transcription"]["backend"] == "cloud"
        assert data["transcription"]["model_size"] == "base"
        assert data["transcription"]["device"] == "cpu"
        assert data["transcription"]["compute_type"] == "int8"


class TestConfigTemplateLocalSTT:
    """Test that CONFIG_TEMPLATE includes v0.4 transcription section."""

    def test_template_has_transcription_section(self):
        """Config template should include a [transcription] section."""
        assert "[transcription]" in CONFIG_TEMPLATE

    def test_template_default_backend_is_cloud(self):
        """Config template should default to cloud backend."""
        assert 'backend = "cloud"' in CONFIG_TEMPLATE

    def test_template_has_model_size(self):
        """Config template should have model_size field."""
        assert "model_size" in CONFIG_TEMPLATE
