"""Security-focused tests for the Voice-to-Summary Paste Tool.

Validates all Critical security requirements from the threat model:
- REQ-S01: Never log API key
- REQ-S02: Never hardcode API key
- REQ-S06: HTTPS only
- REQ-S07: TLS validation enabled
- REQ-S09: Audio never written to disk
- REQ-S11: No audio data in logs
- REQ-S15: Only specific hotkey hooks
- REQ-S18: Paste as plain text only
"""

import os
import pytest

# Source directory for file scanning
_src_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)


def _read_all_source_files() -> dict[str, str]:
    """Read all Python source files and return as {filename: content}."""
    sources = {}
    for fname in os.listdir(_src_dir):
        if fname.endswith(".py"):
            filepath = os.path.join(_src_dir, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                sources[fname] = f.read()
    return sources


class TestREQS01_NoApiKeyInLogs:
    """REQ-S01: The API key must never appear in logs."""

    def test_config_uses_masked_key_for_logging(self):
        """Config should provide a masked key method."""
        from config import AppConfig
        config = AppConfig(openai_api_key="sk-abc123def456ghi789")
        masked = config.masked_api_key()
        assert "sk-abc123def456" not in masked
        assert masked.endswith("i789")

    def test_source_never_logs_raw_api_key(self):
        """No source file should log the raw API key in a logging call."""
        sources = _read_all_source_files()
        import re
        # Pattern: logger.xxx(...openai_api_key...) on the same line
        log_key_pattern = re.compile(
            r"logger\.\w+\(.*openai_api_key(?!.*masked).*\)"
        )
        for fname, content in sources.items():
            for i, line in enumerate(content.split("\n")):
                if log_key_pattern.search(line):
                    pytest.fail(
                        f"{fname}:{i+1} may log raw API key: {line.strip()}"
                    )


class TestREQS02_NoHardcodedSecrets:
    """REQ-S02: No API keys hardcoded in source."""

    def test_no_sk_prefix_in_source(self):
        """No source file should contain an OpenAI API key pattern."""
        sources = _read_all_source_files()
        for fname, content in sources.items():
            # Real OpenAI keys start with sk- followed by many chars
            lines = content.split("\n")
            for i, line in enumerate(lines):
                # Skip test files, comments, and string patterns
                if "sk-test" in line or "sk-" in line and "example" in line:
                    continue
                if line.strip().startswith("#"):
                    continue
                # Check for real-looking keys (sk- followed by 20+ chars)
                import re
                matches = re.findall(r'sk-[a-zA-Z0-9]{20,}', line)
                assert len(matches) == 0, \
                    f"{fname}:{i+1} may contain a hardcoded API key"


class TestREQS06_HTTPSOnly:
    """REQ-S06: All API calls must use HTTPS."""

    def test_no_http_urls_in_source(self):
        """No source file should contain http:// URLs for API calls."""
        sources = _read_all_source_files()
        for fname, content in sources.items():
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if "http://" in line and "https://" not in line:
                    # Allow comments about HTTP
                    if not line.strip().startswith("#") and \
                       not line.strip().startswith('"') and \
                       not line.strip().startswith("'") and \
                       "localhost" not in line and \
                       "127.0.0.1" not in line:
                        pytest.fail(
                            f"{fname}:{i+1} contains http:// URL: {line.strip()}"
                        )

    def test_whisper_url_is_https(self):
        """Whisper API URL must be HTTPS."""
        from constants import WHISPER_API_URL
        assert WHISPER_API_URL.startswith("https://")


class TestREQS07_TLSValidation:
    """REQ-S07: TLS certificate validation must be enabled."""

    def test_no_verify_false_in_source(self):
        """No source file should disable TLS verification."""
        sources = _read_all_source_files()
        for fname, content in sources.items():
            assert "verify=False" not in content, \
                f"{fname} disables TLS verification"
            assert "verify = False" not in content, \
                f"{fname} disables TLS verification"


class TestREQS09_AudioNeverOnDisk:
    """REQ-S09: Audio must never be written to disk."""

    def test_no_file_write_of_audio(self):
        """Source should not write audio to files."""
        sources = _read_all_source_files()
        for fname, content in sources.items():
            if fname == "audio.py":
                # audio.py should only use BytesIO, not file paths
                assert "open(" not in content or "wave.open(buffer" in content, \
                    f"{fname} may write audio to disk"

    def test_no_tempfile_usage(self):
        """No source file should use tempfile for audio."""
        sources = _read_all_source_files()
        for fname, content in sources.items():
            assert "tempfile" not in content, \
                f"{fname} uses tempfile module (audio may hit disk)"
            assert "NamedTemporaryFile" not in content, \
                f"{fname} uses NamedTemporaryFile"


class TestREQS15_SpecificHotkeyOnly:
    """REQ-S15: Only hook specific hotkey combinations."""

    def test_no_blanket_keyboard_hook(self):
        """hotkey.py should not use keyboard.hook() for blanket monitoring."""
        sources = _read_all_source_files()
        hotkey_src = sources.get("hotkey.py", "")
        # keyboard.hook() captures ALL keypresses -- must not be used
        assert "kb.hook(" not in hotkey_src
        assert "keyboard.hook(" not in hotkey_src
        assert "kb.on_press(" not in hotkey_src


class TestREQS18_PlainTextOnly:
    """REQ-S18: Paste must use CF_UNICODETEXT (plain text only)."""

    def test_cf_unicodetext_is_used(self):
        """paste.py must use CF_UNICODETEXT."""
        sources = _read_all_source_files()
        paste_src = sources.get("paste.py", "")
        assert "CF_UNICODETEXT" in paste_src

    def test_no_rich_text_clipboard_formats(self):
        """paste.py must not use rich text clipboard formats."""
        sources = _read_all_source_files()
        paste_src = sources.get("paste.py", "")
        assert "CF_HTML" not in paste_src
        assert "CF_RTF" not in paste_src
        assert "49411" not in paste_src  # CF_HTML numeric value
